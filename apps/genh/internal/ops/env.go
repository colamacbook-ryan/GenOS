package ops

import (
	"os"
	"path/filepath"

	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/access"
	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/compose"
	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/config"
	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/machine"
	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/pgtune"
	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/secretgen"
)

// Env là ngữ cảnh dùng chung cho mọi lệnh vận hành — cố ý nhỏ hơn
// install.Env (không có Secrets/AutoApprove/BrowserOpened, những trường chỉ
// có ý nghĩa trong lúc `genh install` đang chạy): các lệnh vận hành đọc lại
// bí mật đã có qua LoadSecrets khi cần, không tự sinh mới (xem
// secretgen.Load).
type Env struct {
	// InstallDir là gốc cài đặt, mặc định ~/.gen-harness (config.DefaultRoot).
	InstallDir string
	// Port là cổng HTTPS cho proxy, mặc định machine.DefaultPort — dùng để
	// gọi /api/v1/ready và dựng URL Console.
	Port int

	// locate cho phép tiêm compose.Locate giả khi test — nil dùng
	// compose.Locate.
	locate func(installDir string) (string, error)

	// locateSync cho phép tiêm compose.LocateAndSync giả khi test — nil dùng
	// locate (nếu đã tiêm, để test khỏi phải tiêm cả hai) rồi compose.
	// LocateAndSync (sản xuất thật). Chỉ LocatePathSync (dùng bởi `genh
	// update`, xem update.go) dùng trường này — mọi lệnh vận hành khác dùng
	// LocatePath/locate (KHÔNG đồng bộ, xem compose.Locate và mục #3 v0.1.2).
	locateSync func(installDir string) (string, error)
}

// ResolveInstallDir trả về InstallDir đã cấu hình, hoặc config.DefaultRoot()
// nếu rỗng — dùng bởi cmd/genh khi dựng Env từ cờ `--install-dir`.
func ResolveInstallDir(installDir string) (string, error) {
	if installDir != "" {
		return installDir, nil
	}
	return config.DefaultRoot()
}

// ResolvePort trả về port đã cấu hình, hoặc machine.DefaultPort nếu <= 0.
func ResolvePort(port int) int {
	if port <= 0 {
		return machine.DefaultPort
	}
	return port
}

// ConfigDir trả về thư mục cấu hình/bí mật của bản cài (Bước 4 ghi vào đây).
func (e *Env) ConfigDir() string {
	return filepath.Join(e.InstallDir, "config")
}

// LocatePath tìm deploy/compose.yaml thật, hoặc trả OpError có cấu trúc nếu
// không thấy.
func (e *Env) LocatePath() (string, error) {
	locate := e.locate
	if locate == nil {
		locate = compose.Locate
	}
	path, err := locate(e.InstallDir)
	if err != nil {
		return "", &OpError{
			Code: ErrCodeComposeNotFound,
			What: "Không tìm thấy deploy/compose.yaml",
			Why:  err.Error(),
			Next: "Đặt biến GENH_COMPOSE_FILE trỏ tới compose.yaml, hoặc chạy `genh install` trước.",
			Err:  err,
		}
	}
	if err := e.ensureAccess(path); err != nil {
		return "", err
	}
	return path, ensureAuxSecretsOp(path)
}

// ensureAccess đảm bảo .env cạnh compose.yaml có GH_BIND_ADDR (v0.1.46, F-21):
// compose.yaml mới nghe `${GH_BIND_ADDR:-127.0.0.1}` nên máy cũ PHẢI được ghi
// 0.0.0.0/lan_legacy TRƯỚC khi compose.yaml mới được dùng cho `up` (nếu không
// cập nhật âm thầm đóng cổng LAN mà Owner đang dùng — QD-12). Mọi đường
// (kể cả LocatePath, chạy đầu RunUpdate, trước LocatePathSync) đều qua đây.
// Thay đổi thì ghi run/network-status.json cho Console (lỗi ghi chỉ bỏ qua).
func (e *Env) ensureAccess(composePath string) error {
	if fi, serr := os.Stat(composeDir(composePath)); serr != nil || !fi.IsDir() {
		return nil // chưa có thư mục compose thì cũng chưa có gì để `up`
	}
	st, changed, err := access.Ensure(composePath, false)
	if err != nil {
		return &OpError{
			Code: ErrCodeAccessWriteFailed,
			What: "Không ghi được cấu hình truy cập (.env)",
			Why:  err.Error(),
			Next: "Kiểm quyền ghi thư mục " + composeDir(composePath) + " rồi thử lại.",
			Err:  err,
		}
	}
	if changed {
		_ = writeNetworkStatus(e, st, nil)
	}
	return nil
}

// LocatePathSync tìm deploy/compose.yaml thật NHƯ LocatePath, nhưng qua
// compose.LocateAndSync — TỰ ĐỒNG BỘ lại compose.yaml GENH QUẢN LÝ với bản
// nhúng của binary genh đang chạy nếu lệch (giữ bản cũ ở compose.yaml.bak).
// CHỈ `genh update` gọi hàm này (xem update.go) — mọi lệnh vận hành khác
// dùng LocatePath (không đồng bộ, chỉ nhắc — xem docs/reports/
// HANDOFF-v0.1.1.md mục "Lỗi cần sửa" #3 của v0.1.2).
func (e *Env) LocatePathSync() (string, error) {
	locate := e.locateSync
	if locate == nil {
		locate = e.locate // test thường chỉ tiêm locate — dùng lại cho khỏi phải tiêm hai lần
	}
	if locate == nil {
		locate = compose.LocateAndSync
	}
	path, err := locate(e.InstallDir)
	if err != nil {
		return "", &OpError{
			Code: ErrCodeComposeNotFound,
			What: "Không tìm thấy deploy/compose.yaml",
			Why:  err.Error(),
			Next: "Đặt biến GENH_COMPOSE_FILE trỏ tới compose.yaml, hoặc chạy `genh install` trước.",
			Err:  err,
		}
	}
	if err := e.ensureAccess(path); err != nil {
		return "", err
	}
	return path, ensureAuxSecretsOp(path)
}

// LoadSecrets đọc lại bí mật đã sinh ở Bước 4, TỰ BỔ SUNG (và ghi lại ngay)
// bất kỳ trường nào còn thiếu so với phiên bản genh hiện tại (secretgen.
// LoadFillingMissing, tái dùng đúng logic fillMissing của secretgen.Ensure) —
// KHÔNG tự "cài" (sinh secrets.json từ đầu) nếu thư mục cấu hình chưa có bí
// mật nào, trả OpError "chưa cài" trong trường hợp đó.
//
// SỬA LỖI (docs/reports/HANDOFF-v0.1.1.md mục "Lỗi cần sửa" #1 của v0.1.2):
// trước đây gọi secretgen.Load (chỉ đọc, không bổ sung) — một bản cài từ
// v0.1.0 nâng cấp genh lên v0.1.1/v0.1.2 có secrets.json thiếu
// app_db_password (trường mới từ v0.1.1) → EnvOverlay truyền
// GH_APP_DB_PASSWORD rỗng → MỌI lệnh `docker compose` lỗi ngay ở
// ${GH_APP_DB_PASSWORD:?...} trong compose.yaml, kể cả `genh status`.
func (e *Env) LoadSecrets() (secretgen.Bundle, error) {
	b, err := secretgen.LoadFillingMissing(e.ConfigDir())
	if err != nil {
		return secretgen.Bundle{}, &OpError{
			Code: ErrCodeNotInstalled,
			What: "Chưa cài Gen-Harness (hoặc thư mục cài đặt không đúng)",
			Why:  err.Error(),
			Next: "Chạy `genh install` trước, hoặc kiểm cờ --install-dir nếu bạn cài vào thư mục khác mặc định.",
			Err:  err,
		}
	}
	return b, nil
}

// EnvOverlay dựng các biến môi trường compose.yaml cần cho MỌI lệnh `docker
// compose` (kể cả `ps`/`logs`/`stop` — Compose parse toàn bộ tệp, kể cả các
// khoá ${VAR:?...} bắt buộc, trước khi chạy bất kỳ subcommand nào) — cùng
// logic secretsEnvOverlay trong internal/install/steps_data.go, viết lại ở
// đây vì đó là hàm không xuất của package khác.
//
// GH_PG_* (tinh chỉnh Postgres) được dò/tính lại mỗi lần gọi qua
// pgtune.DetectAndCompute — rẻ (chỉ đọc /proc/meminfo hoặc tương đương, xem
// internal/machine), và luôn phản ánh đúng RAM máy hiện tại (kể cả khi máy
// được cấp thêm RAM giữa hai lần chạy genh) thay vì đông cứng giá trị đo ở
// lần `genh install` đầu tiên.
func EnvOverlay(b secretgen.Bundle) []string {
	env := []string{
		"POSTGRES_PASSWORD=" + b.DBPassword,
		"GH_APP_DB_PASSWORD=" + b.AppDBPassword,
		"GH_SETUP_TOKEN=" + b.SetupToken,
		// GH_BACKUP_KEY: khoá mã hoá backup (secretgen.Bundle.BackupKey, hex
		// 64 ký tự) — deploy/compose.yaml đọc qua ${GH_BACKUP_KEY:-} trong
		// x-app-env, agent Python (apps/api/gh/backup.py) dùng để mã hoá bytes
		// backup, độc lập với GH_MASTER_KEY.
		"GH_BACKUP_KEY=" + b.BackupKey,
		// MINIO_ROOT_USER/MINIO_ROOT_PASSWORD: MinIO đã BỊ BỎ khỏi
		// compose.yaml từ v0.1.1 (xem docs/reports/HANDOFF-v0.1.1.md), và
		// secretgen.Bundle không còn trường nào cho MinIO — nhưng compose.yaml
		// CŨ (v0.1.0) còn nằm nguyên trên đĩa những máy CHƯA qua `genh
		// update`/`genh install` mới (compose.yaml chỉ được ĐỒNG BỘ bởi hai
		// lệnh đó, xem compose.LocateAndSync), và tệp đó có
		// ${MINIO_ROOT_PASSWORD:?...} BẮT BUỘC. Compose CLI nội suy TOÀN BỘ
		// tệp (kể cả service không được "up") trước khi chạy BẤT KỲ subcommand
		// nào, nên thiếu biến này làm MỌI lệnh `docker compose` (kể cả `genh
		// status`) lỗi ngay ở bước nội suy, dù MinIO không còn được dùng thật
		// (SỬA LỖI docs/reports/HANDOFF-v0.1.1.md mục "Lỗi cần sửa" #4 của
		// v0.1.3, đã tái hiện: "required variable MINIO_ROOT_PASSWORD is
		// missing"). Giá trị dưới đây chỉ để COMPOSE CŨ NỘI SUY ĐƯỢC — không
		// dịch vụ nào thật sự đọc chúng (compose.yaml MỚI không còn khai
		// service MinIO), và giá trị không rỗng để khớp cú pháp ":?" (bắt buộc
		// KHÁC RỖNG, không phải chỉ "đã đặt").
		"MINIO_ROOT_USER=unused-minio-removed-v0.1.1",
		"MINIO_ROOT_PASSWORD=unused-minio-removed-v0.1.1",
	}
	return append(env, pgtune.DetectAndCompute().EnvPairs()...)
}

// composeDir trả về thư mục chứa composePath, dùng làm Dir cho dockercli.Cmd.
func composeDir(composePath string) string { return filepath.Dir(composePath) }

// ensureAuxSecretsOp: xem ensureAuxSecrets (secrets.go) — lỗi ghi khoá (quyền
// thư mục) trả OpError rõ ràng thay vì để `docker compose up` hỏng sau đó.
func ensureAuxSecretsOp(composePath string) error {
	if err := ensureAuxSecrets(composePath); err != nil {
		return &OpError{
			Code: ErrCodeComposeNotFound,
			What: "Không chuẩn bị được khoá bí mật cho docker compose (gh_bridge_key/gh_browser_key/gh_agy_key)",
			Why:  err.Error(),
			Next: "Kiểm quyền ghi thư mục secrets/ cạnh deploy/ rồi thử lại.",
			Err:  err,
		}
	}
	return nil
}
