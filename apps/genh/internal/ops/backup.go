package ops

import (
	"context"
	"fmt"
	"io"
	"net/http"
	"os"
	"strings"
	"time"

	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/compose"
	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/dockercli"
	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/hostlink"
)

// RunBackup chạy `python -m gh.backup run` trong container api (backup vào
// ObjectStore nội bộ — phần lõi bắt buộc của lệnh này). Nếu toPath khác
// rỗng, SAU KHI backup xong trong container, cố COPY thêm bản đó ra host
// tại toPath — đọc bytes qua getObjectBytesScript rồi ghi thẳng ra đĩa host.
//
// GIỚI HẠN: bytes ghi ra toPath là bản ĐÃ MÃ HOÁ bằng gh.crypto (đúng những
// gì ObjectStore giữ, xem BACKUP_AAD trong apps/api/gh/backup.py) — không tự
// giải mã ở đây (khoá giải mã là GH_MASTER_KEY, chỉ container api nên biết
// thẳng, genh không nên có bản rõ trên host). Tệp ở toPath dùng được để lưu
// trữ ngoài/gửi hỗ trợ, KHÔNG dùng trực tiếp được với `pg_restore` bên ngoài
// container.
func RunBackup(ctx context.Context, env *Env, toPath string, runner dockercli.Runner, out io.Writer) error {
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
	envOverlay := EnvOverlay(bundle)
	dir := composeDir(composePath)

	key, backupErr := runBackupInContainer(ctx, runner, composePath, envOverlay, dir, BackupTriggerManual)
	if backupErr != nil && key == "" && strings.Contains(backupErr.Error(), "không đọc được khoá") {
		// Lệnh chạy xong (không lỗi tiến trình) nhưng không đọc được khoá từ
		// log — không phải lỗi chặn, backup vẫn có thể đã nằm trong
		// ObjectStore, chỉ là genh không biết khoá nào để in ra/copy tiếp.
		_, _ = fmt.Fprintln(out, "Backup có vẻ đã chạy xong nhưng không đọc được khoá từ log — dùng `docker compose exec api python -m gh.backup list` để kiểm tay.")
	} else if backupErr != nil {
		return &OpError{
			Code: ErrCodeBackupFailed,
			What: "`python -m gh.backup run` thất bại trong container " + backupServiceName,
			Why:  backupErr.Error(),
			Next: "Kiểm `genh status` (db phải healthy) rồi thử lại.",
			Err:  backupErr,
		}
	} else {
		_, _ = fmt.Fprintln(out, "Backup xong: "+key)
	}

	if toPath == "" {
		return nil
	}
	if key == "" {
		return &OpError{
			Code: ErrCodeBackupFailed,
			What: "Không copy được backup ra host vì không xác định được khoá vừa tạo",
			Why:  "không thấy dòng log \"Backup mới: ...\" trong output của `python -m gh.backup run`",
			Next: "Backup vẫn nằm trong ObjectStore nội bộ — dùng `docker compose exec api python -m gh.backup list` để tìm khoá rồi tự copy tay nếu cần.",
		}
	}

	scriptArgs := compose.BaseArgs(composePath, "exec", "-T", backupServiceName, "python", "-c", getObjectBytesScript, key)
	data, err := runner.Output(ctx, dockercli.Cmd{Name: "docker", Args: scriptArgs, Env: envOverlay, Dir: dir})
	if err != nil {
		return &OpError{
			Code: ErrCodeBackupFailed,
			What: "Backup đã lưu trong ObjectStore nội bộ, nhưng copy ra host thất bại",
			Why:  err.Error(),
			Next: "Backup vẫn an toàn (khoá: " + key + "); thử lại `genh backup --to " + toPath + "` hoặc bỏ --to.",
			Err:  err,
		}
	}
	if err := os.WriteFile(toPath, data, 0o600); err != nil {
		return &OpError{
			Code: ErrCodeBackupFailed,
			What: "Backup đã lưu trong ObjectStore nội bộ, nhưng ghi ra " + toPath + " thất bại",
			Why:  err.Error(),
			Next: "Kiểm quyền ghi/đường dẫn " + toPath + " rồi thử lại — backup không mất (khoá: " + key + ").",
			Err:  err,
		}
	}
	_, _ = fmt.Fprintf(out, "Đã copy bản mã hoá ra %s (%d byte) — xem GIỚI HẠN trong ops.RunBackup: tệp này vẫn ở dạng mã hoá.\n", toPath, len(data))
	return nil
}

// RestoreDeps gom phụ thuộc tiêm được của RunRestore (test thay runner/HTTP).
type RestoreDeps struct {
	Runner    dockercli.Runner
	Client    *http.Client
	Timeout   time.Duration
	PollEvery time.Duration
}

// RunRestore khôi phục một bản backup theo khoá ObjectStore ("backups/....enc")
// — dùng chung cho `genh restore <khoá>` và nút "Khôi phục" trong Console
// (v0.1.20, `genh restore --if-requested`). Trả về khoá bản sao lưu an toàn
// chụp ngay trước khi khôi phục (rỗng nếu lỗi trước bước đó).
//
// LUỒNG (cùng khuôn `genh import`, xem RunImport): khôi phục từ BÊN TRONG api
// đang chạy là không an toàn (api/worker vừa đọc/ghi vừa bị xoá CSDL dưới chân),
// nên:
//
//  1. Sao lưu an toàn trạng thái hiện tại (nguồn "pre-restore") — có đường lui.
//  2. `docker compose stop api worker`.
//  3. `run --rm --no-deps api python -m gh.backup restore --key <khoá>` rồi
//     `run --rm --no-deps migrate` (bản sao lưu có thể ở schema cũ hơn).
//     Lỗi ⇒ khôi phục lại bản an toàn ở bước 1 rồi khởi động lại.
//  4. `docker compose up -d` + chờ /api/v1/ready.
//
// GIỚI HẠN: gh.backup.restore_backup() chỉ nhận khoá đã có trong ObjectStore
// nội bộ — không nhận đường dẫn file tuỳ ý trên host (báo lỗi rõ ràng).
func RunRestore(ctx context.Context, env *Env, key string, deps RestoreDeps, out io.Writer) (string, error) {
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
	if !strings.HasPrefix(key, "backups/") {
		if info, statErr := os.Stat(key); statErr == nil && !info.IsDir() {
			return "", &OpError{
				Code: ErrCodeRestoreFailed,
				What: "genh restore chưa hỗ trợ khôi phục từ một tệp tuỳ ý trên host",
				Why:  fmt.Sprintf("%q là một tệp có thật trên máy này, nhưng apps/api/gh/backup.py::restore_backup() chỉ nhận khoá đã có sẵn trong ObjectStore nội bộ (dạng \"backups/...\"), không nhận đường dẫn file host", key),
				Next: "Dùng khoá từ `docker compose exec api python -m gh.backup list` (hoặc danh sách Sao lưu trong Console), ví dụ: genh restore backups/20260925T...enc",
			}
		}
	}
	if !ValidBackupKey(key) {
		return "", &OpError{
			Code: ErrCodeRestoreFailed,
			What: "Khoá bản sao lưu không hợp lệ",
			Why:  fmt.Sprintf("%q không có dạng backups/<thời điểm>-<mã>.pgcustom.enc", key),
			Next: "Chọn bản sao lưu từ danh sách trong Console (Cài đặt › Sao lưu & cập nhật).",
		}
	}

	composePath, err := env.LocatePath()
	if err != nil {
		return "", err
	}
	bundle, err := env.LoadSecrets()
	if err != nil {
		return "", err
	}
	envOverlay := EnvOverlay(bundle)
	dir := composeDir(composePath)
	docker := func(args ...string) error {
		_, err := runner.Output(ctx, dockercli.Cmd{Name: "docker", Args: compose.BaseArgs(composePath, args...), Env: envOverlay, Dir: dir})
		return err
	}
	restoreOneOff := func(k string) error {
		return docker(append([]string{"run", "--rm", "--no-deps", "-T", backupServiceName}, "python", "-m", "gh.backup", "restore", "--key", k)...)
	}

	// Kiểm khoá có thật TRƯỚC khi đụng gì (dừng api/worker, sao lưu an toàn).
	if err := checkBackupExists(ctx, runner, composePath, envOverlay, dir, key); err != nil {
		return "", err
	}

	_, _ = fmt.Fprintln(out, "1/4 Sao lưu an toàn trạng thái hiện tại…")
	// GH_BACKUP_KEEP: bản an toàn không được dọn (prune) mất bản đang khôi phục; api cũ bỏ qua biến lạ.
	safety, err := runBackupInContainer(ctx, runner, composePath, envOverlay, dir, BackupTriggerPreRestore, backupKeepEnv+"="+key)
	if err != nil {
		return "", &OpError{
			Code: ErrCodeRestoreFailed,
			What: "Sao lưu an toàn trước khi khôi phục thất bại — DỪNG LẠI, chưa đụng gì",
			Why:  err.Error(),
			Next: "Kiểm `genh status` (db phải healthy) rồi thử lại.",
			Err:  err,
		}
	}
	_, _ = fmt.Fprintln(out, "     bản an toàn: "+safety)

	// fail: khôi phục lại bản an toàn rồi bật lại toàn bộ dịch vụ.
	fail := func(what string, cause error) (string, error) {
		_, _ = fmt.Fprintf(out, "LỖI: %s — đang quay về bản an toàn %s…\n", what, safety)
		next := "Dữ liệu đã quay về như trước khi khôi phục (bản an toàn " + safety + ")."
		if rbErr := restoreOneOff(safety); rbErr != nil {
			next = "Quay về bản an toàn CŨNG thất bại (" + rbErr.Error() + ") — chạy tay `genh restore " + safety + "`."
		}
		if upErr := docker("up", "-d"); upErr != nil {
			next += " `docker compose up -d` thất bại (" + upErr.Error() + ") — chạy `genh start`."
		}
		return safety, &OpError{Code: ErrCodeRestoreFailed, What: what, Why: cause.Error(), Next: next, Err: cause}
	}

	_, _ = fmt.Fprintln(out, "2/4 Dừng api/worker…")
	if err := docker("stop", "api", "worker"); err != nil {
		return fail("`docker compose stop api worker` thất bại", err)
	}

	_, _ = fmt.Fprintln(out, "3/4 Khôi phục "+key+"…")
	if err := restoreOneOff(key); err != nil {
		return fail("`python -m gh.backup restore --key "+key+"` thất bại", err)
	}
	migrateArgs := compose.BaseArgs(composePath, "run", "--rm", "--no-deps", "-T", "migrate")
	if err := runner.Stream(ctx, dockercli.Cmd{Name: "docker", Args: migrateArgs, Env: envOverlay, Dir: dir}, func(string) {}); err != nil {
		return fail("`alembic upgrade heads` sau khi khôi phục thất bại", err)
	}

	_, _ = fmt.Fprintln(out, "4/4 Khởi động lại dịch vụ…")
	if err := docker("up", "-d"); err != nil {
		return safety, &OpError{
			Code: ErrCodeRestoreFailed,
			What: "Đã khôi phục dữ liệu nhưng `docker compose up -d` thất bại",
			Why:  err.Error(),
			Next: "Chạy `genh start` rồi `genh status`.",
			Err:  err,
		}
	}
	if err := waitReady(ctx, client, localURL(env.Port, readyPath), timeout, pollEvery); err != nil {
		return safety, &OpError{
			Code: ErrCodeRestoreFailed,
			What: "Đã khôi phục dữ liệu nhưng " + readyPath + " chưa sẵn sàng",
			Why:  err.Error(),
			Next: "Xem `genh logs api`. Cần quay lại thì chạy `genh restore " + safety + "`.",
			Err:  err,
		}
	}
	_, _ = fmt.Fprintln(out, "Đã khôi phục "+key+" (bản an toàn trước đó: "+safety+")")
	return safety, nil
}

// ValidBackupKey: đúng dạng khoá mà gh.backup.run_backup sinh ra.
func ValidBackupKey(key string) bool { return backupKeyStrictRe.MatchString(key) }

// RunRestoreRequest xử lý yêu cầu "Khôi phục" từ Console (hộp thư
// internal/hostlink): không có yêu cầu ⇒ (false, nil). Có ⇒ XOÁ yêu cầu trước
// (watcher không kích lặp), ghi restore-status.json "running", chạy RunRestore
// rồi ghi "done"/"failed" (kèm khoá bản an toàn để Console hiện đường lui).
func RunRestoreRequest(ctx context.Context, env *Env, deps RestoreDeps, out io.Writer) (bool, error) {
	if !hostlink.HasRestoreRequest(env.InstallDir) {
		return false, nil
	}
	req, err := hostlink.ConsumeRestoreRequest(env.InstallDir)
	if err != nil {
		_ = hostlink.StartRestore(env.InstallDir, req.Key)
		_ = hostlink.FinishRestore(env.InstallDir, "failed", "", "Yêu cầu khôi phục không đọc được — bấm Khôi phục lại")
		return true, &OpError{Code: ErrCodeRestoreFailed, What: "Yêu cầu khôi phục từ Console không hợp lệ", Why: err.Error(),
			Next: "Bấm Khôi phục lại trong Console."}
	}
	_ = hostlink.StartRestore(env.InstallDir, req.Key)
	safety, err := RunRestore(ctx, env, req.Key, deps, out)
	if err != nil {
		msg := err.Error()
		if opErr, ok := err.(*OpError); ok {
			msg = opErr.What + " — " + opErr.Next
		}
		_ = hostlink.FinishRestore(env.InstallDir, "failed", safety, msg)
		return true, err
	}
	_ = hostlink.FinishRestore(env.InstallDir, "done", safety, "")
	return true, nil
}
