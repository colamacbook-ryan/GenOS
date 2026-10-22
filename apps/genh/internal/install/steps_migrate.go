package install

import (
	"context"
	"crypto/rand"
	"encoding/base32"
	"encoding/base64"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"sync"
	"time"

	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/compose"
	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/dockercli"
	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/secretgen"
)

// migrateServiceName là service compose thật chạy migration.
//
// LƯU Ý — khác biệt so với docs/handoff/05-installer.md: tài liệu mô tả Bước
// 6 là "chạy migration trong container api" (ngụ ý `docker compose exec api
// alembic upgrade head`). deploy/compose.yaml THẬT lại định nghĩa một
// service riêng "migrate" (build cùng Dockerfile với api, command cố định
// ["alembic", "upgrade", "heads"], restart: "no"), và chính "api"/"worker"
// khai depends_on migrate: {condition: service_completed_successfully} — nói
// cách khác api còn chưa thể khởi động trước khi migrate chạy xong, nên
// "exec vào container api" không phải là cơ chế thật. Step này chạy đúng
// theo compose.yaml thật (`docker compose run` service "migrate"), không
// theo mô tả rút gọn trong tài liệu — xem thêm Makefile (`make migrate` gọi
// `alembic upgrade heads`, số nhiều, không phải "head" số ít).
const migrateServiceName = "migrate"

// defaultMigrateTimeout: migration chạy trong container Python khởi động
// nhanh (không cần build lại nếu image đã build ở Bước 2/3), bản thân
// alembic áp một loạt migration cho một CSDL mới thường chỉ vài giây tới vài
// chục giây — 3 phút dư dả cho máy chậm/lần cài đầu tiên.
const defaultMigrateTimeout = 3 * time.Minute

// migrationLineRe khớp dòng log alembic khi áp dụng một migration, ví dụ:
//
//	INFO  [alembic.runtime.migration] Running upgrade  -> 0001, tạo bảng gốc
//	INFO  [alembic.runtime.migration] Running upgrade 0001 -> 0002, thêm cột x
//
// Alembic ghi các dòng này qua logging (thường ra stderr) — dockercli.Runner
// .Stream gộp cả stdout lẫn stderr theo thứ tự xuất hiện nên vẫn bắt được.
var migrationLineRe = regexp.MustCompile(`(?i)running (upgrade|downgrade)\b`)

// migrateStep cài Bước 6 — Tạo cấu trúc dữ liệu (9%): chạy
// `docker compose run --rm migrate` (alembic upgrade heads, xem
// migrateServiceName ở trên), báo tiến độ theo số dòng "Running upgrade"
// thấy được trong log thật — không suy đoán tổng số migration còn lại khi
// không có cách nào biết chắc trước khi chạy, nên percent tăng dần theo số
// migration ĐÃ áp dụng thật (không phải tỉ lệ trên một tổng bịa ra).
type migrateStep struct {
	// runner cho phép tiêm dockercli.Runner giả khi test — nil dùng ExecRunner thật.
	runner dockercli.Runner
	// locate cho phép tiêm compose.LocateAndSync giả khi test — nil dùng
	// compose.LocateAndSync (genh install CÓ trách nhiệm đồng bộ compose.yaml
	// nhúng, khác Locate thường dùng ở internal/ops — xem doc-comment
	// compose.LocateAndSync).
	locate  func(installDir string) (string, error)
	timeout time.Duration
}

func (migrateStep) ID() StepID   { return StepMigrate }
func (migrateStep) Name() string { return "Tạo cấu trúc dữ liệu" }

func (s migrateStep) Run(ctx context.Context, env *Env, rep Reporter) error {
	runner := s.runner
	if runner == nil {
		runner = dockercli.ExecRunner{}
	}
	locate := s.locate
	if locate == nil {
		locate = compose.LocateAndSync
	}
	timeout := s.timeout
	if timeout <= 0 {
		timeout = defaultMigrateTimeout
	}

	installDir := ""
	if env != nil {
		installDir = env.InstallDir
	}
	composePath, err := locate(installDir)
	if err != nil {
		se := &StepError{
			Code: ErrCodeComposeNotFound,
			What: "Không tìm thấy deploy/compose.yaml",
			Why:  err.Error(),
			Next: "Đặt biến GENH_COMPOSE_FILE trỏ tới compose.yaml rồi bấm r.",
			Err:  err,
		}
		rep.Report(Progress{Status: StatusError, Percent: 100, Err: se})
		return se
	}

	res, err := secretsResult(env)
	if err != nil {
		se := &StepError{
			Code: ErrCodeMigrateFailed,
			What: "Chưa có bí mật để chạy migration",
			Why:  err.Error(),
			Next: "Chạy lại `genh install` từ đầu (Bước 4 phải chạy trước Bước 6).",
			Err:  err,
		}
		rep.Report(Progress{Status: StatusError, Percent: 100, Err: se})
		return se
	}
	envOverlay, err := secretsEnvOverlay(env)
	if err != nil {
		se := &StepError{
			Code: ErrCodeMigrateFailed,
			What: "Chưa có bí mật để chạy migration",
			Why:  err.Error(),
			Next: "Chạy lại `genh install` từ đầu (Bước 4 phải chạy trước Bước 6).",
			Err:  err,
		}
		rep.Report(Progress{Status: StatusError, Percent: 100, Err: se})
		return se
	}

	if err := ensureComposeSecretFiles(composePath, res); err != nil {
		se := &StepError{
			Code: ErrCodeMigrateFailed,
			What: "Không chuẩn bị được khoá bí mật cho docker compose (gh_master_key/gh_bridge_key)",
			Why:  err.Error(),
			Next: "Kiểm quyền ghi thư mục cài đặt rồi bấm r.",
			Err:  err,
		}
		rep.Report(Progress{Status: StatusError, Percent: 100, Err: se})
		return se
	}

	rep.Report(Progress{Status: StatusRunning, Percent: 5, Detail: "đang chạy migration trong container " + migrateServiceName})

	// -T (--no-TTY) bắt buộc: genh chạy lệnh này như một subprocess không
	// gắn TTY thật, để `docker compose run` không tự ý cố cấp phát pseudo-TTY
	// (mặc định "auto-detected") rồi treo/lỗi khi không có gì để gắn vào.
	// --no-deps: db đã được Bước 5 khởi động và chờ healthy — không cần
	// compose tự đối chiếu lại phụ thuộc "db: condition: service_healthy"
	// của service migrate lần nữa. --rm: mỗi lần chạy lại (idempotent, ví dụ
	// sau khi Bước 7 thêm migration mới ở một bản cập nhật) tạo container
	// mới, không cố dùng lại container "migrate" cũ đã exited.
	runArgs := compose.BaseArgs(composePath, "run", "--rm", "-T", "--no-deps", migrateServiceName)
	dir := filepath.Dir(composePath)

	// runCtx giới hạn thời gian container migrate được phép chạy — không có
	// giới hạn này, một lệnh alembic treo (ví dụ chờ khoá bảng không bao giờ
	// nhả) sẽ làm genh install đứng hình vô thời hạn.
	runCtx, cancel := context.WithTimeout(ctx, timeout)
	defer cancel()

	var mu sync.Mutex
	applied := 0

	streamErr := runner.Stream(runCtx, dockercli.Cmd{Name: "docker", Args: runArgs, Env: envOverlay, Dir: dir}, func(line string) {
		if !migrationLineRe.MatchString(line) {
			return
		}
		mu.Lock()
		applied++
		n := applied
		mu.Unlock()
		rep.Report(Progress{
			Status:  StatusRunning,
			Percent: migrateProgressPercent(n),
			Detail:  fmt.Sprintf("đã áp dụng %d migration", n),
		})
	})

	if streamErr != nil {
		se := &StepError{
			Code: ErrCodeMigrateFailed,
			What: "`alembic upgrade heads` thất bại trong container " + migrateServiceName,
			Why:  streamErr.Error(),
			Next: "Xem log ở trên (hoặc `docker compose logs " + migrateServiceName + "`) rồi bấm r để thử lại — dữ liệu đã có không bị mất.",
			Err:  streamErr,
		}
		rep.Report(Progress{Status: StatusError, Percent: 100, Err: se})
		return se
	}

	mu.Lock()
	final := applied
	mu.Unlock()

	detail := "đã ở phiên bản mới nhất (không có migration mới)"
	if final > 0 {
		detail = fmt.Sprintf("đã áp dụng %d migration", final)
	}
	rep.Report(Progress{Status: StatusOK, Percent: 100, Detail: detail})
	return nil
}

// migrateProgressPercent ánh xạ số migration đã áp dụng thành % nội bộ
// 10..90 của Bước 6 — không biết trước tổng số migration còn lại (alembic
// không cho cách rẻ để đếm trước mà không tốn thêm một lượt chạy container
// riêng), nên dùng một đường cong tăng chậm dần thay vì chia theo một tổng
// bịa ra: mỗi migration mới cộng thêm ít hơn migration trước, không bao giờ
// chạm 100 (Run tự đặt 100 sau khi biết chắc đã xong).
func migrateProgressPercent(applied int) float64 {
	pct := 10 + 80*(1-1/float64(applied+1))
	if pct > 90 {
		pct = 90
	}
	return pct
}

// ensureComposeSecretFiles đảm bảo hai tệp Docker secret mà deploy/
// compose.yaml khai (secrets: gh_master_key, gh_bridge_key — đường dẫn
// "../secrets/<tên>" TƯƠNG ĐỐI VỚI THƯ MỤC CHỨA compose.yaml, tức nằm cạnh
// deploy/, không ở trong nó) tồn tại trước khi "docker compose run/up" cho
// migrate/api/worker/bridge — thiếu tệp này Compose báo lỗi "secret ... not
// found" ngay khi tạo container.
//
// LƯU Ý — khoảng trống giữa tài liệu và Bước 4 đã triển khai: docs/handoff/
// 05-installer.md liệt kê bí mật Bước 4 gồm "khoá master, mật khẩu DB, khoá
// MinIO, khoá backup, CA TLS nội bộ, setup token" — KHÔNG có khoá bridge —
// và secretgen.Bundle (Bước 4, đã triển khai ở phiên trước, ngoài phạm vi
// phiên này) đúng như vậy, không có trường BridgeKey. Nhưng compose.yaml
// thật lại đòi hỏi cả gh_bridge_key (service "bridge" ở Bước 7). Để Bước 6/7
// chạy được thật với compose.yaml hiện tại mà không phải sửa lại Bước 4,
// hàm này:
//   - ghi gh_master_key từ res.Bundle.MasterKey đã có sẵn;
//   - tự sinh gh_bridge_key (32 byte ngẫu nhiên, base64 — đúng cách
//     Makefile `make secrets` sinh) NẾU CHƯA CÓ, rồi ghi lại — idempotent,
//     không đụng tệp đã có ở lần chạy sau (giống hệt cách secretgen.EnsureCA
//     tự sinh phần còn thiếu, giữ nguyên phần đã có).
func ensureComposeSecretFiles(composePath string, res secretgen.Result) error {
	secretsDir := filepath.Join(filepath.Dir(composePath), "..", "secrets")
	if err := os.MkdirAll(secretsDir, 0o700); err != nil {
		return fmt.Errorf("tạo thư mục %s: %w", secretsDir, err)
	}
	if err := writeSecretFileIfMissing(filepath.Join(secretsDir, "gh_master_key"), res.Bundle.MasterKey); err != nil {
		return err
	}
	// gh_browser_key (v0.1.29): khoá riêng giữa api/worker và browser-worker —
	// cùng cách sinh với gh_bridge_key (ops.ensureAuxSecrets làm y hệt cho máy
	// cài từ bản cũ khi `genh update`).
	// gh_agy_key (v0.1.63, H3): khoá HMAC riêng giữa api/worker và hộp agy-box
	// (ops.ensureAuxSecrets sinh y hệt cho máy cài từ bản cũ khi `genh update`).
	for _, name := range randomSecretNames {
		if _, err := ensureRandomSecretFile(filepath.Join(secretsDir, name)); err != nil {
			return err
		}
	}
	// gh_offsite_key (v0.1.40, F-12): "Khoá khôi phục" — mật khẩu gói .ghbundle
	// của bản sao ngoài máy, định dạng dễ gõ/in (xem generateOffsiteKey).
	if err := ensureOffsiteKeyFile(filepath.Join(secretsDir, "gh_offsite_key")); err != nil {
		return err
	}
	// Docker secret dạng file là bind mount GIỮ NGUYÊN quyền trên host: tệp
	// 0600 thuộc user host thì tiến trình trong container (USER gh / node,
	// uid khác) không đọc được → api/worker/bridge chết ngay lúc khởi động
	// (EACCES — phát hiện ở e2e cài thật). Bảo vệ nằm ở thư mục secrets/
	// 0700 (user khác trên host không vào được); tệp bên trong để 0644 cho
	// container đọc. Chmod cả tệp cũ để sửa luôn bản cài đã có.
	if err := os.Chmod(secretsDir, 0o700); err != nil {
		return fmt.Errorf("đặt quyền %s: %w", secretsDir, err)
	}
	for _, name := range append([]string{"gh_master_key", "gh_offsite_key"}, randomSecretNames...) {
		if err := os.Chmod(filepath.Join(secretsDir, name), secretFilePerm); err != nil {
			return fmt.Errorf("đặt quyền %s: %w", name, err)
		}
	}
	return nil
}

// randomSecretNames: Docker secret ngẫu nhiên 32 byte base64 do genh sinh (xem ops.auxSecretFiles).
var randomSecretNames = []string{"gh_bridge_key", "gh_browser_key", "gh_agy_key"}

// secretFilePerm: xem giải thích trong ensureComposeSecretFiles.
const secretFilePerm = 0o644

// writeSecretFileIfMissing ghi content vào path chỉ khi path CHƯA tồn tại —
// không bao giờ ghi đè một bí mật đang được container khác dùng.
func writeSecretFileIfMissing(path, content string) error {
	if _, err := os.Stat(path); err == nil {
		return nil
	} else if !os.IsNotExist(err) {
		return fmt.Errorf("kiểm tra %s: %w", path, err)
	}
	if strings.TrimSpace(content) == "" {
		return fmt.Errorf("%s: nội dung bí mật rỗng (Bước 4 chưa sinh xong?)", filepath.Base(path))
	}
	return writeFileAtomicPerm(path, []byte(content), secretFilePerm)
}

// ensureRandomSecretFile đọc lại path nếu đã có (idempotent), hoặc sinh 32
// byte ngẫu nhiên mã hoá base64 rồi ghi mới — cùng cách `make secrets` sinh
// secrets/gh_bridge_key cho luồng dev (xem Makefile).
//
// Tệp RỖNG (lần chạy trước ghi dở) hoặc THƯ MỤC RỖNG (Docker tự tạo khi bind-mount
// nguồn chưa có) không phải khoá dùng được ⇒ sinh lại; khoá đã có nội dung thì
// không bao giờ ghi đè.
func ensureRandomSecretFile(path string) (string, error) {
	if st, err := os.Stat(path); err == nil && st.IsDir() {
		if err := os.Remove(path); err != nil { // chỉ gỡ được thư mục rỗng
			return "", fmt.Errorf("%s là thư mục có nội dung (đáng lẽ là tệp khoá) — chuyển nó đi rồi chạy lại: %w", path, err)
		}
	}
	if data, err := os.ReadFile(path); err == nil {
		if key := strings.TrimSpace(string(data)); key != "" {
			return key, nil
		}
	} else if !os.IsNotExist(err) {
		return "", fmt.Errorf("đọc %s: %w", path, err)
	}
	buf := make([]byte, 32)
	if _, err := rand.Read(buf); err != nil {
		return "", fmt.Errorf("sinh khoá ngẫu nhiên cho %s: %w", filepath.Base(path), err)
	}
	key := base64.StdEncoding.EncodeToString(buf)
	if err := writeFileAtomicPerm(path, []byte(key), secretFilePerm); err != nil {
		return "", err
	}
	return key, nil
}

// ensureOffsiteKeyFile sinh secrets/gh_offsite_key nếu CHƯA có (không bao giờ
// ghi đè — khoá cũ đang mã hoá các gói trên ổ USB/NAS của Owner).
func ensureOffsiteKeyFile(path string) error {
	if _, err := os.Stat(path); err == nil {
		return nil
	} else if !os.IsNotExist(err) {
		return fmt.Errorf("kiểm tra %s: %w", path, err)
	}
	key, err := generateOffsiteKey()
	if err != nil {
		return fmt.Errorf("sinh khoá khôi phục %s: %w", filepath.Base(path), err)
	}
	return writeFileAtomicPerm(path, []byte(key), secretFilePerm)
}

// generateOffsiteKey: 6 nhóm × 5 ký tự base32 HOA (A–Z, 2–7) nối '-', 150 bit,
// không xuống dòng — BẢN SAO y hệt ops.GenerateOffsiteKey (install không import
// được ops: ops đã import install). Đổi một bên thì đổi cả bên kia.
func generateOffsiteKey() (string, error) {
	buf := make([]byte, 19)
	if _, err := rand.Read(buf); err != nil {
		return "", err
	}
	s := base32.StdEncoding.WithPadding(base32.NoPadding).EncodeToString(buf)[:30]
	groups := make([]string, 0, 6)
	for i := 0; i < 30; i += 5 {
		groups = append(groups, s[i:i+5])
	}
	return strings.Join(groups, "-"), nil
}

// writeFileAtomicPerm ghi qua tệp tạm rồi rename — tránh để lại tệp bí mật
// nửa vời nếu tiến trình bị ngắt giữa chừng (cùng cách
// secretgen.writeFileAtomic làm, viết riêng ở đây vì hàm đó không xuất ra
// khỏi package secretgen).
func writeFileAtomicPerm(path string, data []byte, perm os.FileMode) error {
	tmp := path + ".tmp"
	if err := os.WriteFile(tmp, data, perm); err != nil {
		return fmt.Errorf("ghi %s: %w", tmp, err)
	}
	if err := os.Rename(tmp, path); err != nil {
		return fmt.Errorf("đổi tên %s -> %s: %w", tmp, path, err)
	}
	return nil
}
