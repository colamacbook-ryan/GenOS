package hostlink

import (
	"context"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"time"
)

// LockFile là tên tệp khoá loại trừ, đặt NGAY dưới gốc cài đặt
// (<gốc cài đặt>/genh.lock) — CỐ Ý không đặt trong run/: run/ để 0777 và
// bind-mount vào container api, nên api (hoặc kẻ chiếm được api) có thể xoá/thay
// tệp khoá (hai genh cùng chạy) hoặc giữ flock mãi (chặn mọi bản cập nhật, kể cả
// bản vá bảo mật). Gốc cài đặt chỉ người dùng chạy genh ghi được.
const LockFile = "genh.lock"

// lockRetryEvery là nhịp thử lại của AcquireLockWait (biến để test đặt ngắn).
var lockRetryEvery = 2 * time.Second

// ErrLockBusy: đang có một tiến trình genh khác (update/restore/import) giữ
// khoá. Lỗi trả về thật là *LockBusyError (errors.Is(err, ErrLockBusy) == true).
var ErrLockBusy = errors.New("đang có một lần cập nhật/khôi phục khác chạy")

// LockBusyError kèm PID tiến trình giữ khoá (đọc từ nhịp sống — 0 nếu không rõ).
type LockBusyError struct{ PID int }

func (e *LockBusyError) Error() string {
	if e.PID > 0 {
		return fmt.Sprintf("%s (PID %d)", ErrLockBusy.Error(), e.PID)
	}
	return ErrLockBusy.Error()
}

// Is cho errors.Is(err, ErrLockBusy).
func (e *LockBusyError) Is(target error) bool { return target == ErrLockBusy }

// BusyPID lấy PID chủ khoá từ lỗi bận (0 nếu không rõ / không phải lỗi bận).
func BusyPID(err error) int {
	var be *LockBusyError
	if errors.As(err, &be) {
		return be.PID
	}
	return 0
}

// Lock là khoá loại trừ đang giữ. Fd mở mặc định có CLOEXEC nên tiến trình con
// (re-exec sau tự cập nhật, docker) KHÔNG thừa hưởng khoá: khoá nhả đúng lúc
// tiến trình giữ nó thoát (kể cả bị kill — hệ điều hành tự nhả).
type Lock struct {
	f *os.File
}

// LockPath là đường dẫn tệp khoá của một bản cài.
func LockPath(installDir string) string { return filepath.Join(installDir, LockFile) }

// AcquireLock thử lấy khoá NGAY (không chờ). Bận → *LockBusyError (kèm PID chủ
// khoá đọc từ run/genh-heartbeat.json nếu có); lỗi khác (không mở được tệp,
// genh.lock là symlink…) trả nguyên.
func AcquireLock(installDir string) (*Lock, error) {
	f, err := openLockFile(LockPath(installDir))
	if err != nil {
		return nil, fmt.Errorf("mở %s: %w", LockPath(installDir), err)
	}
	busy, err := tryLock(f)
	if err != nil {
		_ = f.Close()
		return nil, fmt.Errorf("khoá %s: %w", LockPath(installDir), err)
	}
	if busy {
		_ = f.Close()
		pid := 0
		if hb, herr := ReadHeartbeat(installDir); herr == nil && hb.PID > 0 {
			pid = hb.PID
		}
		return nil, &LockBusyError{PID: pid}
	}
	return &Lock{f: f}, nil
}

// WaitLockFile: tệp khoá "người chờ" (<gốc cài đặt>/genh-wait.lock) — chỉ MỘT
// tiến trình `genh update/restore --if-requested` được chờ khoá chính cùng lúc.
// Watcher crontab chạy mỗi phút khi còn tệp yêu cầu: thiếu khoá này thì trong
// lúc lịch đêm giữ khoá, mỗi phút lại thêm một genh chờ (tới ~30 tiến trình
// cùng thăm dò genh.lock).
const WaitLockFile = "genh-wait.lock"

// AcquireWaitLock thử lấy khoá người chờ NGAY (không chờ). Bận → *LockBusyError
// (PID 0); lỗi khác trả nguyên.
func AcquireWaitLock(installDir string) (*Lock, error) {
	path := filepath.Join(installDir, WaitLockFile)
	f, err := openLockFile(path)
	if err != nil {
		return nil, fmt.Errorf("mở %s: %w", path, err)
	}
	busy, err := tryLock(f)
	if err != nil {
		_ = f.Close()
		return nil, fmt.Errorf("khoá %s: %w", path, err)
	}
	if busy {
		_ = f.Close()
		return nil, &LockBusyError{}
	}
	return &Lock{f: f}, nil
}

// AcquireLockWait thử lấy khoá mỗi lockRetryEvery cho tới khi được, hết max
// (trả lỗi bận cuối cùng) hoặc ctx bị huỷ (trả ctx.Err()). Lỗi không phải "bận"
// trả ngay.
func AcquireLockWait(ctx context.Context, installDir string, max time.Duration) (*Lock, error) {
	deadline := time.Now().Add(max)
	for {
		l, err := AcquireLock(installDir)
		if err == nil || !errors.Is(err, ErrLockBusy) {
			return l, err
		}
		wait := time.Until(deadline)
		if wait <= 0 {
			return nil, err
		}
		if wait > lockRetryEvery {
			wait = lockRetryEvery
		}
		t := time.NewTimer(wait)
		select {
		case <-ctx.Done():
			t.Stop()
			return nil, ctx.Err()
		case <-t.C:
		}
	}
}

// Release nhả khoá (gọi nhiều lần / trên nil vô hại). KHÔNG xoá tệp khoá: xoá
// rồi tạo lại mở ra khe hai tiến trình khoá hai inode khác nhau.
func (l *Lock) Release() {
	if l == nil || l.f == nil {
		return
	}
	unlock(l.f)
	_ = l.f.Close()
	l.f = nil
}
