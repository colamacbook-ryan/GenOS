package ops

import (
	"context"
	"fmt"
	"regexp"
	"strings"

	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/compose"
	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/dockercli"
)

// backupServiceName là service compose chạy CLI `python -m gh.backup` (xem
// apps/api/gh/backup.py) — cùng image với "api"/"migrate", chạy qua `docker
// compose exec` vào container "api" đang sống (không phải `run` một
// container mới, vì backup cần đọc GH_DATABASE_URL/khoá bí mật runtime đã
// mount sẵn ở container api).
const backupServiceName = "api"

// backupKeyRe khớp khoá bản backup mà apps/api/gh/backup.py sinh ra, ví dụ
// "backups/20260925T120000Z-abcd1234.pgcustom.enc" — xuất hiện trong dòng
// log INFO "Backup mới: <khoá> (...)" mà run_backup() ghi (logging module
// mặc định ra stderr, dockercli không phân biệt được stdout/stderr khi
// Stream gộp cả hai theo thứ tự — không thành vấn đề, chỉ cần bắt được
// dòng).
var backupKeyRe = regexp.MustCompile(`backups/\S+\.enc`)

// backupKeyStrictRe: khoá hợp lệ để khôi phục (yêu cầu từ Console đi qua tệp
// trong hộp thư — không tin nội dung, chỉ nhận đúng dạng này).
var backupKeyStrictRe = regexp.MustCompile(`^backups/[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}\.pgcustom\.enc$`)

// getObjectBytesScript đọc thẳng bytes đã MÃ HOÁ của một khoá backup qua
// đúng abstraction ObjectStore mà apps/api/gh/backup.py dùng (get_object_
// store().get(key)) — không giả định biết trước đường dẫn đĩa thật của
// backend (LocalObjectStore hiện tại, hoặc MinIO thật sau này khi được nối
// dây — xem apps/api/gh/chassis/objects.py), nên vẫn đúng bất kể backend
// nào đang cấu hình.
const getObjectBytesScript = `import asyncio, sys
from gh.chassis.objects import get_object_store
async def _m():
    data = await get_object_store().get(sys.argv[1])
    sys.stdout.buffer.write(data)
asyncio.run(_m())`

// Nguồn gốc bản backup (v0.1.20) — Console hiện cột "Nguồn" trong danh sách sao
// lưu. Truyền qua biến môi trường GH_BACKUP_TRIGGER (`-e`), KHÔNG qua cờ CLI:
// `genh update` chạy backup TRONG container api CŨ (trước khi tải bản mới),
// bản cũ không biết cờ lạ sẽ thoát lỗi, còn biến môi trường lạ thì bỏ qua.
const (
	backupTriggerEnv        = "GH_BACKUP_TRIGGER"
	backupKeepEnv           = "GH_BACKUP_KEEP"
	BackupTriggerManual     = "manual"
	BackupTriggerPreUpdate  = "pre-update"
	BackupTriggerPreRestore = "pre-restore"
	BackupTriggerPreImport  = "pre-import"
)

// runBackupInContainer chạy `python -m gh.backup run` trong container api và
// trả về khoá backup vừa tạo (đọc từ dòng log) — logic dùng chung giữa `genh
// backup` (backup.go) và `genh update` (update.go — rollback cần đúng khoá
// backup vừa tạo TRƯỚC khi đụng gì). trigger ghi vào danh mục backup.
func runBackupInContainer(ctx context.Context, runner dockercli.Runner, composePath string, envOverlay []string, dir, trigger string, extraEnv ...string) (string, error) {
	var lines []string
	stream := func(args []string) error {
		lines = nil
		return runner.Stream(ctx, dockercli.Cmd{Name: "docker", Args: args, Env: envOverlay, Dir: dir}, func(line string) {
			lines = append(lines, line)
		})
	}
	cmd := []string{"-e", backupTriggerEnv + "=" + trigger}
	for _, e := range extraEnv {
		cmd = append(cmd, "-e", e)
	}
	cmd = append(cmd, backupServiceName, "python", "-m", "gh.backup", "run")
	err := stream(compose.BaseArgs(composePath, append([]string{"exec", "-T"}, cmd...)...))
	if isServiceNotRunning(err) {
		err = stream(compose.BaseArgs(composePath, append([]string{"run", "--rm", "--no-deps", "-T"}, cmd...)...))
	}
	if err != nil {
		return "", err
	}
	key := findBackupKey(lines)
	if key == "" {
		return "", fmt.Errorf("backup chạy xong nhưng không đọc được khoá từ log")
	}
	return key, nil
}

func findBackupKey(lines []string) string {
	for _, l := range lines {
		if m := backupKeyRe.FindString(l); m != "" {
			return m
		}
	}
	return ""
}

// restoreInContainer chạy `python -m gh.backup restore --key <key>` trong
// container api — dùng chung giữa `genh restore` (backup.go) và rollback tự
// động của `genh update` (update.go).
//
// forceOneOff=true (rollback của update, F-33): gọi THẲNG `run --rm --no-deps`
// — container tạm dựng từ ảnh theo compose.yaml CŨ (vừa ghi lại), không exec
// vào container api đang chạy ảnh MỚI (đang lỗi/khởi động lại liên tục, hoặc
// mang mã gh.backup mới không khớp bản sao lưu cũ). forceOneOff=false: exec
// trước, api không chạy thì rơi về run --rm.
func restoreInContainer(ctx context.Context, runner dockercli.Runner, composePath string, envOverlay []string, dir, key string, forceOneOff bool) error {
	run := func(oneOff bool) error {
		args := apiCommandArgs(composePath, oneOff, "python", "-m", "gh.backup", "restore", "--key", key)
		_, err := runner.Output(ctx, dockercli.Cmd{Name: "docker", Args: args, Env: envOverlay, Dir: dir})
		return err
	}
	if forceOneOff {
		return run(true)
	}
	err := run(false)
	if isServiceNotRunning(err) {
		err = run(true)
	}
	return err
}

// apiCommandArgs dựng lệnh chạy cmd trong service api: `exec` vào container
// đang sống, hoặc (oneOff) `run --rm --no-deps` một container tạm cùng cấu
// hình (env, secrets, volume) khi api KHÔNG chạy — ví dụ máy cài lỗi dở dang
// (proxy/api chưa lên) mà Owner chạy `genh update` để sửa: backup trước khi
// cập nhật vẫn phải làm được, chỉ cần db sống.
func apiCommandArgs(composePath string, oneOff bool, cmd ...string) []string {
	if oneOff {
		return compose.BaseArgs(composePath, append([]string{"run", "--rm", "--no-deps", "-T", backupServiceName}, cmd...)...)
	}
	return compose.BaseArgs(composePath, append([]string{"exec", "-T", backupServiceName}, cmd...)...)
}

// isServiceNotRunning nhận ra lỗi `docker compose exec` khi container api
// không chạy ("service \"api\" is not running") HOẶC đang khởi động lại liên
// tục ("Container … is restarting, wait until the container is running" —
// F-33: api ảnh mới lỗi vòng lặp restart) — cả hai đều rơi về `run --rm`.
func isServiceNotRunning(err error) bool {
	if err == nil {
		return false
	}
	msg := err.Error()
	return strings.Contains(msg, "is not running") || strings.Contains(msg, "is restarting")
}

// checkBackupExists chạy `python -m gh.backup list` trong api và báo lỗi rõ nếu
// khoá không có trong danh mục — gọi TRƯỚC mọi thao tác phá huỷ.
func checkBackupExists(ctx context.Context, runner dockercli.Runner, composePath string, envOverlay []string, dir, key string) error {
	var lines []string
	list := func(oneOff bool) error {
		lines = nil
		args := apiCommandArgs(composePath, oneOff, "python", "-m", "gh.backup", "list")
		return runner.Stream(ctx, dockercli.Cmd{Name: "docker", Args: args, Env: envOverlay, Dir: dir}, func(l string) { lines = append(lines, l) })
	}
	err := list(false)
	if isServiceNotRunning(err) {
		err = list(true)
	}
	if err != nil {
		return &OpError{
			Code: ErrCodeRestoreFailed,
			What: "Không đọc được danh sách bản sao lưu — DỪNG LẠI, chưa đụng gì",
			Why:  err.Error(),
			Next: "Kiểm `genh status` (db/api phải chạy) rồi thử lại.",
			Err:  err,
		}
	}
	for _, l := range lines {
		if strings.Contains(l, key) {
			return nil
		}
	}
	return &OpError{
		Code: ErrCodeRestoreFailed,
		What: "Không tìm thấy bản sao lưu " + key + " — DỪNG LẠI, chưa đụng gì",
		Why:  "khoá không có trong `python -m gh.backup list` (có thể đã bị dọn theo vòng đời)",
		Next: "Chọn lại bản sao lưu trong Console (Cài đặt › Sao lưu & cập nhật).",
	}
}
