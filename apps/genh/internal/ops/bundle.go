package ops

import (
	"bufio"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"time"

	"golang.org/x/term"

	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/compose"
	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/dockercli"
)

// Gói hồ sơ .ghbundle — HỢP ĐỒNG với một agent Python khác đang viết
// `python -m gh.bundle` (xem docs mục "Gói hồ sơ .ghbundle"): genh chỉ gọi
// vào hai lệnh con qua `docker compose exec`, KHÔNG biết/không cần biết định
// dạng bytes bên trong gói (mã hoá bằng mật khẩu Owner tự chọn, khác hẳn
// GH_MASTER_KEY dùng cho `genh backup`/`genh restore` — xem backupcore.go).
//
//   - `python -m gh.bundle export --out -`  : gói ra STDOUT, log ra STDERR.
//   - `python -m gh.bundle import --in -`   : gói đọc từ STDIN.
//   - `python -m gh.bundle verify --in -`   : (v0.1.40) chỉ KIỂM gói đọc từ STDIN
//     (giải mã + pg_restore --list), không đụng CSDL — xem verifyBundle.
//   - Mật khẩu truyền qua biến môi trường GH_BUNDLE_PASSWORD (KHÔNG qua argv
//     — argv của tiến trình con hiện ra trong `ps`/log hệ thống, môi trường
//     của một tiến trình không-con thì không).
//   - Mã thoát: 0 ok · 1 lỗi khác · 2 sai mật khẩu/gói hỏng · 3 gói không
//     tương thích (phiên bản gh.bundle khác).
const (
	bundleMagic          = "GHBUNDLE1\n"
	bundlePasswordEnv    = "GH_BUNDLE_PASSWORD"
	minBundlePasswordLen = 12
)

// bundleServiceName là service compose chạy `python -m gh.bundle` — cùng
// container với gh.backup (backupServiceName ở backupcore.go): cần đọc
// GH_DATABASE_URL/khoá bí mật runtime đã mount sẵn ở container api đang
// sống, không phải một container `run` mới.
const bundleServiceName = backupServiceName

// readSecretLine đọc MỘT dòng ẩn (không echo) từ terminal thật — tách ra
// thành một biến thay vì gọi thẳng term.ReadPassword để test tiêm giả (sandbox
// test không có TTY thật để gõ mật khẩu).
var readSecretLine = func() (string, error) {
	b, err := term.ReadPassword(int(os.Stdin.Fd()))
	if err != nil {
		return "", err
	}
	return string(b), nil
}

// resolveBundlePassword lấy mật khẩu gói .ghbundle: ưu tiên biến môi trường
// GH_BUNDLE_PASSWORD (dùng cho script/test, xem hợp đồng ở trên) — nếu có,
// KHÔNG hỏi gì thêm, kể cả khi confirm=true (env đã là một giá trị chắc
// chắn, hỏi lại hai lần chỉ có ý nghĩa khi Owner tự gõ). Nếu không có, hỏi
// ẩn qua readSecretLine: một lần khi confirm=false (import — chỉ cần đúng
// mật khẩu đã dùng lúc export), hai lần khi confirm=true (export — gõ sai
// một lần là mất khả năng đọc lại gói, phải bắt khớp trước khi dùng).
func resolveBundlePassword(out io.Writer, confirm bool, shortCode string) (string, error) {
	if v := os.Getenv(bundlePasswordEnv); v != "" {
		if len(v) < minBundlePasswordLen {
			return "", &OpError{
				Code: shortCode,
				What: fmt.Sprintf("Mật khẩu qua biến %s quá ngắn", bundlePasswordEnv),
				Why:  fmt.Sprintf("cần ít nhất %d ký tự, %s hiện có %d ký tự", minBundlePasswordLen, bundlePasswordEnv, len(v)),
				Next: fmt.Sprintf("Đặt %s dài hơn (≥%d ký tự) rồi thử lại.", bundlePasswordEnv, minBundlePasswordLen),
			}
		}
		return v, nil
	}

	_, _ = fmt.Fprintf(out, "Mật khẩu gói .ghbundle (ẩn, ≥%d ký tự): ", minBundlePasswordLen)
	p1, err := readSecretLine()
	_, _ = fmt.Fprintln(out)
	if err != nil {
		return "", &OpError{
			Code: shortCode,
			What: "Không đọc được mật khẩu từ terminal",
			Why:  err.Error(),
			Next: fmt.Sprintf("Chạy trong terminal thật (có TTY), hoặc đặt biến %s để bỏ qua hỏi mật khẩu (script/CI).", bundlePasswordEnv),
			Err:  err,
		}
	}
	if len(p1) < minBundlePasswordLen {
		return "", &OpError{
			Code: shortCode,
			What: "Mật khẩu quá ngắn",
			Why:  fmt.Sprintf("cần ít nhất %d ký tự, bạn gõ %d ký tự", minBundlePasswordLen, len(p1)),
			Next: "Gõ lại một mật khẩu dài hơn.",
		}
	}
	if !confirm {
		return p1, nil
	}

	_, _ = fmt.Fprint(out, "Gõ lại mật khẩu để xác nhận: ")
	p2, err := readSecretLine()
	_, _ = fmt.Fprintln(out)
	if err != nil {
		return "", &OpError{
			Code: shortCode,
			What: "Không đọc được mật khẩu xác nhận từ terminal",
			Why:  err.Error(),
			Next: fmt.Sprintf("Chạy trong terminal thật (có TTY), hoặc đặt biến %s để bỏ qua hỏi mật khẩu (script/CI).", bundlePasswordEnv),
			Err:  err,
		}
	}
	if p1 != p2 {
		return "", &OpError{
			Code: shortCode,
			What: "Hai lần gõ mật khẩu không khớp",
			Next: "Chạy lại `genh export` và gõ đúng cùng một mật khẩu cả hai lần.",
		}
	}
	return p1, nil
}

// ExportDeps cho phép tiêm dockercli.Runner giả khi test — không có cách
// tiêm readSecretLine qua đây (nó là biến gói dùng chung với import, test tự
// đổi giá trị rồi khôi phục, xem bundle_test.go).
type ExportDeps struct {
	Runner dockercli.Runner
}

// RunExport chạy `python -m gh.bundle export --out -` trong container api,
// stream bytes stdout THẲNG vào một tệp tạm cạnh toPath rồi rename — KHÔNG
// bao giờ đọc cả gói vào RAM (gói có thể rất lớn, chứa toàn bộ dữ liệu Owner).
func RunExport(ctx context.Context, env *Env, toPath string, deps ExportDeps, out io.Writer) error {
	password, err := resolveBundlePassword(out, true, ErrCodeExportPasswordMismatch)
	if err != nil {
		return err
	}
	return exportBundle(ctx, env, toPath, password, deps, out)
}

// exportBundle là lõi của RunExport (v0.1.40 tách ra cho `genh offsite run`):
// KHÔNG hỏi mật khẩu — caller đưa sẵn. Mật khẩu chỉ đi qua biến môi trường
// GH_BUNDLE_PASSWORD của tiến trình con (Cmd.Env), không bao giờ qua argv/log.
func exportBundle(ctx context.Context, env *Env, toPath, password string, deps ExportDeps, out io.Writer) error {
	runner := deps.Runner
	if runner == nil {
		runner = dockercli.ExecRunner{}
	}

	composePath, err := env.LocatePath()
	if err != nil {
		return err
	}
	bundle, err := env.LoadSecrets()
	if err != nil {
		return err
	}
	envOverlay := append(append([]string{}, EnvOverlay(bundle)...), bundlePasswordEnv+"="+password)
	dir := composeDir(composePath)

	args := compose.BaseArgs(composePath, "exec", "-T", "-e", bundlePasswordEnv, bundleServiceName, "python", "-m", "gh.bundle", "export", "--out", "-")

	absTo, err := filepath.Abs(toPath)
	if err != nil {
		return &OpError{
			Code: ErrCodeExportWriteFailed,
			What: "Đường dẫn --to không hợp lệ",
			Why:  err.Error(),
			Next: "Dùng một đường dẫn tệp hợp lệ cho --to.",
			Err:  err,
		}
	}
	tmpPath := absTo + ".tmp-genh-export"
	tmpFile, err := os.OpenFile(tmpPath, os.O_WRONLY|os.O_CREATE|os.O_TRUNC, 0o600)
	if err != nil {
		return &OpError{
			Code: ErrCodeExportWriteFailed,
			What: "Không tạo được tệp tạm cạnh " + toPath,
			Why:  err.Error(),
			Next: "Kiểm quyền ghi thư mục chứa " + toPath + " rồi thử lại.",
			Err:  err,
		}
	}

	_, _ = fmt.Fprintln(out, "Đang xuất gói .ghbundle…")
	sink := &writeErrRecorder{w: tmpFile}
	runErr := runner.RunIO(ctx, dockercli.Cmd{Name: "docker", Args: args, Env: envOverlay, Dir: dir}, nil, sink)
	closeErr := tmpFile.Close()

	if runErr != nil && sink.err != nil {
		// Lỗi là do GHI tệp tạm (ổ USB đầy/bị rút/chỉ đọc) chứ không phải lệnh xuất —
		// mã riêng để offsite báo GH-EB04 "Không ghi được vào ổ ngoài" thay vì GH-EB02.
		_ = os.Remove(tmpPath)
		return &OpError{
			Code: ErrCodeExportWriteFailed,
			What: "Ghi tệp tạm " + tmpPath + " thất bại",
			Why:  sink.err.Error(),
			Next: "Kiểm ổ đích còn chỗ trống, còn cắm và cho phép ghi rồi thử lại.",
			Err:  sink.err,
		}
	}
	if runErr != nil {
		_ = os.Remove(tmpPath)
		return &OpError{
			Code: ErrCodeExportFailed,
			What: "`python -m gh.bundle export` thất bại",
			Why:  runErr.Error(),
			Next: "Kiểm `genh status` (db phải healthy) rồi thử lại `genh export --to " + toPath + "`.",
			Err:  runErr,
		}
	}
	if closeErr != nil {
		_ = os.Remove(tmpPath)
		return &OpError{
			Code: ErrCodeExportWriteFailed,
			What: "Ghi tệp tạm " + tmpPath + " thất bại",
			Why:  closeErr.Error(),
			Next: "Kiểm dung lượng đĩa còn trống rồi thử lại.",
			Err:  closeErr,
		}
	}
	if err := os.Rename(tmpPath, absTo); err != nil {
		_ = os.Remove(tmpPath)
		return &OpError{
			Code: ErrCodeExportWriteFailed,
			What: "Không đổi tên tệp tạm thành " + toPath,
			Why:  err.Error(),
			Next: "Kiểm quyền ghi thư mục chứa " + toPath + " rồi thử lại.",
			Err:  err,
		}
	}
	if err := os.Chmod(absTo, 0o600); err != nil {
		_, _ = fmt.Fprintln(out, "Cảnh báo: không đặt được quyền 0600 cho "+absTo+": "+err.Error())
	}

	_, _ = fmt.Fprintln(out, "Xuất gói xong: "+absTo)
	return nil
}

// writeErrRecorder nhớ lỗi GHI đầu tiên của w — tách "ghi tệp đích hỏng" khỏi
// "lệnh xuất hỏng" khi RunIO trả lỗi (cả hai đều làm RunIO lỗi).
type writeErrRecorder struct {
	w   io.Writer
	err error
}

func (r *writeErrRecorder) Write(p []byte) (int, error) {
	n, err := r.w.Write(p)
	if err != nil && r.err == nil {
		r.err = err
	}
	return n, err
}

// bundleVerifyInfo là dòng JSON `gh.bundle verify` in ra stdout khi gói đọc được.
type bundleVerifyInfo struct {
	OK              bool   `json:"ok"`
	AlembicRevision string `json:"alembic_revision"`
	Objects         int64  `json:"objects"`
	DBDumpBytes     int64  `json:"db_dump_bytes"`
	CreatedAt       string `json:"created_at"`
}

// maxVerifyStdout giới hạn stdout của `gh.bundle verify` genh giữ lại (một dòng JSON).
const maxVerifyStdout = 64 << 10

// limitedBuffer giữ tối đa n byte đầu, bỏ phần thừa (không lỗi — tiến trình con
// không bị chặn vì stdout dài).
type limitedBuffer struct {
	buf []byte
	n   int
}

func (b *limitedBuffer) Write(p []byte) (int, error) {
	if room := b.n - len(b.buf); room > 0 {
		if len(p) < room {
			room = len(p)
		}
		b.buf = append(b.buf, p[:room]...)
	}
	return len(p), nil
}

// verifyBundle (v0.1.40, F-12) kiểm gói vừa ghi đọc lại được: `docker compose
// exec -T -e GH_BUNDLE_PASSWORD api python -m gh.bundle verify --in -` (stdin =
// tệp gói; giải mã + `pg_restore --list`, không đụng CSDL). Mã thoát (hợp đồng
// gh.bundle): 0 OK · 2 sai khoá/gói hỏng/pg_restore --list lỗi · 3 không tương
// thích · 1 khác. Mọi lỗi trả *OpError mã GH-EB03 (= CHƯA có bản sao dùng được).
func verifyBundle(ctx context.Context, env *Env, path, password string, deps ExportDeps) (bundleVerifyInfo, error) {
	var info bundleVerifyInfo
	runner := deps.Runner
	if runner == nil {
		runner = dockercli.ExecRunner{}
	}
	composePath, err := env.LocatePath()
	if err != nil {
		return info, err
	}
	bundle, err := env.LoadSecrets()
	if err != nil {
		return info, err
	}
	f, err := os.Open(path)
	if err != nil {
		return info, &OpError{
			Code: ErrCodeOffsiteVerifyFailed,
			What: "Bản sao vừa tạo không đọc lại được — CHƯA có bản sao ngoài máy",
			Why:  err.Error(),
			Next: "Kiểm ổ USB/NAS còn cắm và đọc được rồi chạy lại `genh offsite run`.",
			Err:  err,
		}
	}
	defer func() { _ = f.Close() }()

	envOverlay := append(append([]string{}, EnvOverlay(bundle)...), bundlePasswordEnv+"="+password)
	args := compose.BaseArgs(composePath, "exec", "-T", "-e", bundlePasswordEnv, bundleServiceName, "python", "-m", "gh.bundle", "verify", "--in", "-")
	stdout := &limitedBuffer{n: maxVerifyStdout}
	runErr := runner.RunIO(ctx, dockercli.Cmd{Name: "docker", Args: args, Env: envOverlay, Dir: composeDir(composePath)}, f, stdout)
	if runErr == nil {
		line := strings.TrimSpace(string(stdout.buf))
		if i := strings.LastIndex(line, "\n"); i >= 0 {
			line = line[i+1:]
		}
		_ = json.Unmarshal([]byte(line), &info) // chỉ để in số liệu; thoát 0 là đủ
		return info, nil
	}
	what := "Bản sao vừa tạo không đọc lại được — CHƯA có bản sao ngoài máy"
	why := runErr.Error()
	var exitErr *dockercli.ExitError
	if errors.As(runErr, &exitErr) {
		switch exitErr.Code {
		case 2:
			why = "gói không giải mã được bằng khoá khôi phục, hoặc pg_restore --list không đọc được bản CSDL trong gói (" + runErr.Error() + ")"
		case 3:
			why = "gh.bundle báo gói không tương thích với phiên bản hiện tại (" + runErr.Error() + ")"
		default:
			why = "gh.bundle verify lỗi (" + runErr.Error() + ")"
		}
	}
	return info, &OpError{
		Code: ErrCodeOffsiteVerifyFailed,
		What: what,
		Why:  why,
		Next: "Kiểm ổ USB/NAS (còn chỗ, không lỗi) rồi chạy lại `genh offsite run`; vẫn lỗi thì gửi `genh doctor` cho người hỗ trợ.",
		Err:  runErr,
	}
}

// ImportOptions là các cờ đã phân tích của `genh import`.
type ImportOptions struct {
	AutoApprove bool // --yes: bỏ qua hỏi xác nhận GHI ĐÈ
}

// ImportDeps cho phép tiêm dockercli.Runner/http.Client giả + thời gian chờ
// ngắn hơn khi test (cùng vai trò UpdateDeps ở update.go — RunImport dùng lại
// waitReady sau khi restart api/worker).
type ImportDeps struct {
	Runner    dockercli.Runner
	Client    *http.Client
	Timeout   time.Duration
	PollEvery time.Duration
}

// validateBundleFile mở path, kiểm tồn tại + không phải thư mục + bắt đầu
// bằng bundleMagic ("GHBUNDLE1\n") — trả về *os.File đã seek về đầu (sẵn
// sàng dùng làm stdin của `gh.bundle import`) nếu hợp lệ.
func validateBundleFile(path string) (*os.File, error) {
	info, err := os.Stat(path)
	if err != nil {
		return nil, &OpError{
			Code: ErrCodeImportNotBundle,
			What: "Không tìm thấy tệp gói .ghbundle",
			Why:  err.Error(),
			Next: "Kiểm đường dẫn " + path + " rồi thử lại.",
			Err:  err,
		}
	}
	if info.IsDir() {
		return nil, &OpError{
			Code: ErrCodeImportNotBundle,
			What: path + " là một thư mục, không phải tệp gói .ghbundle",
			Next: "Trỏ đúng tệp .ghbundle do `genh export` tạo ra.",
		}
	}

	f, err := os.Open(path)
	if err != nil {
		return nil, &OpError{
			Code: ErrCodeImportNotBundle,
			What: "Không mở được " + path,
			Why:  err.Error(),
			Next: "Kiểm quyền đọc tệp rồi thử lại.",
			Err:  err,
		}
	}

	magic := make([]byte, len(bundleMagic))
	if _, err := io.ReadFull(bufio.NewReader(f), magic); err != nil || string(magic) != bundleMagic {
		_ = f.Close()
		why := fmt.Sprintf("không bắt đầu bằng %q", bundleMagic)
		if err != nil && !errors.Is(err, io.EOF) && !errors.Is(err, io.ErrUnexpectedEOF) {
			why = err.Error()
		}
		return nil, &OpError{
			Code: ErrCodeImportNotBundle,
			What: path + " không phải một gói .ghbundle hợp lệ",
			Why:  why,
			Next: "Kiểm bạn trỏ đúng tệp do `genh export` tạo ra, chưa bị sửa/cắt bớt.",
		}
	}
	if _, err := f.Seek(0, io.SeekStart); err != nil {
		_ = f.Close()
		return nil, &OpError{
			Code: ErrCodeImportNotBundle,
			What: "Không đọc lại được " + path + " từ đầu",
			Why:  err.Error(),
			Next: "Thử lại; nếu vẫn lỗi, kiểm tệp có nằm trên một hệ tệp hỗ trợ seek (không phải pipe/FIFO).",
			Err:  err,
		}
	}
	return f, nil
}

// RunImport khôi phục một gói .ghbundle. LUỒNG (SỬA LỖI mục #4/#5 của
// v0.1.2, xem docs/reports/HANDOFF-v0.1.1.md): bản v0.1.1 chạy
// `python -m gh.bundle import` qua `docker compose exec` vào container api
// ĐANG SỐNG (api/worker vẫn chạy song song, có thể vừa đọc/ghi DB vừa bị
// pg_restore --clean của gh.bundle xoá bảng ngay dưới chân — race thật) và
// KHÔNG chạy lại migrate sau khi restore (gói .ghbundle có thể cũ hơn schema
// hiện tại). Luồng mới:
//
//  1. Backup AN TOÀN vào ObjectStore nội bộ (như cũ, dùng lại
//     runBackupInContainer) — NHƯNG chép NGAY bản đó ra host (giống `genh
//     backup --to`, dùng lại getObjectBytesScript) trước khi đụng gì khác:
//     nếu container api/worker sau này bị thay (ví dụ Owner tự docker compose
//     down đâu đó giữa chừng), bản backup trong ObjectStore container CŨ
//     không còn ý nghĩa để restore vào container MỚI — bản trên host luôn
//     dùng lại được.
//  2. `docker compose stop api worker` — dừng hẳn hai service này TRƯỚC khi
//     đụng DB, tránh api/worker vừa đọc/ghi vừa bị pg_restore --clean xoá
//     bảng ngay dưới chân.
//  3. `docker compose run --rm --no-deps -T -e GH_BUNDLE_PASSWORD api
//     python -m gh.bundle import --in -` (stdin = tệp gói) — CHẠY MỘT
//     CONTAINER MỚI (`run`, không phải `exec` vào container đã dừng ở bước
//  2. cùng image api, không phụ thuộc service khác đang chạy hay không
//     (--no-deps).
//  4. `docker compose run --rm --no-deps -T migrate` — chạy lại
//     `alembic upgrade heads` NGAY sau restore (gói có thể ở schema cũ hơn
//     bản genh hiện tại).
//  5. `docker compose up -d` — khởi động lại toàn bộ, đợi healthy.
//
// LỖI GIỮA CHỪNG (sau khi bước 2 đã dừng api/worker): LUÔN cố
// `docker compose up -d api worker` lại (đưa Owner về trạng thái ít nhất còn
// chạy được, dù chưa chắc đã import xong) rồi báo lỗi kèm hướng dẫn khôi
// phục từ bản backup an toàn ĐÃ CHÉP RA HOST ở bước 1. Mã thoát 2 (sai mật
// khẩu/gói hỏng) và 3 (không tương thích) của `gh.bundle import` giữ nguyên
// ánh xạ ErrCodeImportWrongPassword/ErrCodeImportIncompatible như trước.
func RunImport(ctx context.Context, env *Env, path string, opts ImportOptions, deps ImportDeps, in io.Reader, out io.Writer) error {
	runner := deps.Runner
	if runner == nil {
		runner = dockercli.ExecRunner{}
	}
	client := deps.Client
	if client == nil {
		client = insecureLocalClient(5 * time.Second)
	}
	timeout := deps.Timeout
	if timeout <= 0 {
		timeout = defaultUpdateReadyTimeout
	}
	pollEvery := deps.PollEvery
	if pollEvery <= 0 {
		pollEvery = defaultUpdatePollEvery
	}

	f, err := validateBundleFile(path)
	if err != nil {
		return err
	}
	defer func() { _ = f.Close() }()

	_, _ = fmt.Fprintln(out, "CẢNH BÁO: import sẽ GHI ĐÈ toàn bộ dữ liệu hiện tại của Gen-Harness bằng nội dung trong gói "+path+".")
	if !opts.AutoApprove {
		_, _ = fmt.Fprintln(out, "Tiếp tục? [y/N]")
		if !confirmYesNo(in) {
			return &OpError{
				Code: ErrCodeImportCancelled,
				What: "Đã huỷ — không import gì cả, dữ liệu hiện tại còn nguyên",
				Next: "Chạy lại `genh import " + path + "` (hoặc kèm --yes) khi chắc chắn.",
			}
		}
	}

	password, err := resolveBundlePassword(out, false, ErrCodeImportPasswordInvalid)
	if err != nil {
		return err
	}

	composePath, err := env.LocatePath()
	if err != nil {
		return err
	}
	bundle, err := env.LoadSecrets()
	if err != nil {
		return err
	}
	envOverlay := EnvOverlay(bundle)
	dir := composeDir(composePath)

	_, _ = fmt.Fprintln(out, "1/4 Backup an toàn trước khi import…")
	key, err := runBackupInContainer(ctx, runner, composePath, envOverlay, dir, BackupTriggerPreImport)
	if err != nil {
		return &OpError{
			Code: ErrCodeImportBackupFailed,
			What: "Backup an toàn trước khi import thất bại — DỪNG LẠI, chưa đụng gì",
			Why:  err.Error(),
			Next: "Kiểm `genh status` (db phải healthy) rồi thử lại `genh import " + path + "`.",
			Err:  err,
		}
	}
	_, _ = fmt.Fprintln(out, "     backup an toàn: "+key)

	safeBackupHostPath, err := copyBackupObjectToHost(ctx, runner, composePath, envOverlay, dir, key, env.InstallDir)
	if err != nil {
		return &OpError{
			Code: ErrCodeImportBackupFailed,
			What: "Backup an toàn (" + key + ") xong, nhưng chép ra host thất bại — DỪNG LẠI, chưa đụng gì khác",
			Why:  err.Error(),
			Next: "Bản backup vẫn còn TRONG container api hiện tại (khoá: " + key + "); kiểm dung lượng đĩa/quyền ghi ở " + env.InstallDir + "/data rồi thử lại `genh import " + path + "`.",
			Err:  err,
		}
	}
	_, _ = fmt.Fprintln(out, "     đã chép ra host: "+safeBackupHostPath)

	restoreHint := fmt.Sprintf(
		"khôi phục từ bản backup an toàn (khoá %s, đã chép ra host tại %s): dùng `docker compose exec -T %s python -m gh.backup restore --key %s` nếu container api còn dùng chung ObjectStore, hoặc chép %s vào container/volume mới rồi restore.",
		key, safeBackupHostPath, bundleServiceName, key, safeBackupHostPath)

	_, _ = fmt.Fprintln(out, "2/4 Dừng api/worker…")
	stopArgs := compose.BaseArgs(composePath, "stop", "api", "worker")
	if _, err := runner.Output(ctx, dockercli.Cmd{Name: "docker", Args: stopArgs, Env: envOverlay, Dir: dir}); err != nil {
		return recoverImportAndWrap(ctx, runner, composePath, envOverlay, dir, out, &OpError{
			Code: ErrCodeImportFailed,
			What: "`docker compose stop api worker` trước khi import thất bại",
			Why:  err.Error(),
			Next: restoreHint,
			Err:  err,
		})
	}

	_, _ = fmt.Fprintln(out, "3/4 Import gói…")
	importArgs := compose.BaseArgs(composePath, "run", "--rm", "--no-deps", "-T", "-e", bundlePasswordEnv, bundleServiceName, "python", "-m", "gh.bundle", "import", "--in", "-")
	importEnv := append(append([]string{}, envOverlay...), bundlePasswordEnv+"="+password)
	runErr := runner.RunIO(ctx, dockercli.Cmd{Name: "docker", Args: importArgs, Env: importEnv, Dir: dir}, f, out)

	if runErr != nil {
		var exitErr *dockercli.ExitError
		if errors.As(runErr, &exitErr) {
			switch exitErr.Code {
			case 2:
				return recoverImportAndWrap(ctx, runner, composePath, envOverlay, dir, out, &OpError{
					Code: ErrCodeImportWrongPassword,
					What: "Sai mật khẩu gói .ghbundle, hoặc gói bị hỏng",
					Why:  runErr.Error(),
					Next: "Kiểm đúng mật khẩu đã dùng lúc `genh export` và tệp gói còn nguyên vẹn. Dữ liệu hiện tại KHÔNG bị mất — " + restoreHint,
					Err:  runErr,
				})
			case 3:
				return recoverImportAndWrap(ctx, runner, composePath, envOverlay, dir, out, &OpError{
					Code: ErrCodeImportIncompatible,
					What: "Gói .ghbundle không tương thích với phiên bản Gen-Harness hiện tại",
					Why:  runErr.Error(),
					Next: "Dùng bản genh/gh.bundle cùng phiên bản đã tạo gói này. Dữ liệu hiện tại KHÔNG bị mất — " + restoreHint,
					Err:  runErr,
				})
			default:
				return recoverImportAndWrap(ctx, runner, composePath, envOverlay, dir, out, &OpError{
					Code: ErrCodeImportFailed,
					What: "`python -m gh.bundle import` thất bại",
					Why:  runErr.Error(),
					Next: "Xem log ở trên. Dữ liệu hiện tại KHÔNG bị mất — " + restoreHint,
					Err:  runErr,
				})
			}
		}
		return recoverImportAndWrap(ctx, runner, composePath, envOverlay, dir, out, &OpError{
			Code: ErrCodeImportFailed,
			What: "Không chạy được `python -m gh.bundle import`",
			Why:  runErr.Error(),
			Next: "Kiểm Gen-Harness đang chạy (`genh status`). Dữ liệu hiện tại KHÔNG bị mất — " + restoreHint,
			Err:  runErr,
		})
	}

	_, _ = fmt.Fprintln(out, "     import xong, chạy lại migrate…")
	migrateArgs := compose.BaseArgs(composePath, "run", "--rm", "--no-deps", "-T", "migrate")
	if err := runner.Stream(ctx, dockercli.Cmd{Name: "docker", Args: migrateArgs, Env: envOverlay, Dir: dir}, func(string) {}); err != nil {
		return recoverImportAndWrap(ctx, runner, composePath, envOverlay, dir, out, &OpError{
			Code: ErrCodeImportFailed,
			What: "Import xong nhưng `alembic upgrade heads` sau import thất bại",
			Why:  err.Error(),
			Next: "Xem `docker compose logs migrate`. Dữ liệu ĐÃ import (có thể ở schema chưa khớp bản genh hiện tại) — nếu cần quay lại: " + restoreHint,
			Err:  err,
		})
	}

	_, _ = fmt.Fprintln(out, "4/4 Khởi động lại dịch vụ…")
	upArgs := compose.BaseArgs(composePath, "up", "-d")
	if _, err := runner.Output(ctx, dockercli.Cmd{Name: "docker", Args: upArgs, Env: envOverlay, Dir: dir}); err != nil {
		return &OpError{
			Code: ErrCodeImportRestartFailed,
			What: "Import xong nhưng `docker compose up -d` thất bại",
			Why:  err.Error(),
			Next: "Chạy tay `docker compose up -d` rồi kiểm `genh status`. Dữ liệu ĐÃ import — nếu cần quay lại: " + restoreHint,
			Err:  err,
		}
	}

	readyURL := localURL(env.Port, readyPath)
	if err := waitReady(ctx, client, readyURL, timeout, pollEvery); err != nil {
		return &OpError{
			Code: ErrCodeImportRestartFailed,
			What: readyPath + " không trả 200 sau khi import + khởi động lại",
			Why:  err.Error(),
			Next: "Xem `docker compose logs api worker`. Dữ liệu ĐÃ import — nếu cần quay lại: " + restoreHint,
			Err:  err,
		}
	}

	_, _ = fmt.Fprintln(out, "Import xong, dịch vụ đã sẵn sàng. (backup an toàn trước import: "+key+", "+safeBackupHostPath+")")
	return nil
}

// copyBackupObjectToHost đọc bytes ĐÃ MÃ HOÁ của một khoá backup (qua
// getObjectBytesScript — cùng cách RunBackup dùng cho `genh backup --to`) và
// ghi ra installDir/data/import-safety-<tên tệp trong khoá>, trả về đường
// dẫn đã ghi. Dùng ở RunImport để bản backup AN TOÀN trước import không phụ
// thuộc container api hiện tại còn sống hay ObjectStore hiện tại còn nguyên
// vẹn hay không — xem doc-comment RunImport.
func copyBackupObjectToHost(ctx context.Context, runner dockercli.Runner, composePath string, envOverlay []string, dir, key, installDir string) (string, error) {
	scriptArgs := compose.BaseArgs(composePath, "exec", "-T", backupServiceName, "python", "-c", getObjectBytesScript, key)
	data, err := runner.Output(ctx, dockercli.Cmd{Name: "docker", Args: scriptArgs, Env: envOverlay, Dir: dir})
	if err != nil {
		return "", err
	}

	destDir := filepath.Join(installDir, "data")
	if err := os.MkdirAll(destDir, 0o755); err != nil {
		return "", err
	}
	destPath := filepath.Join(destDir, "import-safety-"+filepath.Base(key))
	if err := os.WriteFile(destPath, data, 0o600); err != nil {
		return "", err
	}
	return destPath, nil
}

// recoverImportAndWrap CỐ khởi động lại api/worker (`docker compose up -d
// api worker` — best-effort, không phải lỗi chặn nếu thất bại, chỉ ghi chú
// thêm vào Next) rồi trả lại đúng original (giữ nguyên Code/What/Why) —
// dùng cho MỌI lỗi giữa chừng SAU KHI bước 2/4 đã dừng api/worker (xem
// doc-comment RunImport mục "LỖI GIỮA CHỪNG").
func recoverImportAndWrap(ctx context.Context, runner dockercli.Runner, composePath string, envOverlay []string, dir string, out io.Writer, original *OpError) error {
	_, _ = fmt.Fprintf(out, "LỖI (%s) — đang cố khởi động lại api/worker…\n", original.Code)
	upArgs := compose.BaseArgs(composePath, "up", "-d", "api", "worker")
	_, upErr := runner.Output(ctx, dockercli.Cmd{Name: "docker", Args: upArgs, Env: envOverlay, Dir: dir})

	next := original.Next
	if upErr != nil {
		_, _ = fmt.Fprintln(out, "     `docker compose up -d api worker` CŨNG thất bại — cần can thiệp tay ngay.")
		next = "`docker compose up -d api worker` CŨNG thất bại (" + upErr.Error() + ") — cần can thiệp tay ngay. " + next
	} else {
		_, _ = fmt.Fprintln(out, "     đã khởi động lại api/worker.")
	}

	return &OpError{Code: original.Code, What: original.What, Why: original.Why, Next: next, Err: original.Err}
}
