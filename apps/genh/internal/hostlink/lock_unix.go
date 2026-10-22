//go:build !windows

package hostlink

import (
	"errors"
	"fmt"
	"os"
	"syscall"
)

// openLockFile mở (tạo nếu chưa có) tệp khoá 0600, KHÔNG đi theo symlink ở
// thành phần cuối (O_NOFOLLOW), không treo ở FIFO (O_NONBLOCK); chỉ nhận tệp
// thường. Go mở fd với O_CLOEXEC.
func openLockFile(path string) (*os.File, error) {
	f, err := os.OpenFile(path, os.O_CREATE|os.O_RDWR|syscall.O_NOFOLLOW|syscall.O_NONBLOCK, 0o600)
	if err != nil {
		return nil, err
	}
	fi, err := f.Stat()
	if err != nil {
		_ = f.Close()
		return nil, err
	}
	if !fi.Mode().IsRegular() {
		_ = f.Close()
		return nil, fmt.Errorf("%s không phải tệp thường", path)
	}
	return f, nil
}

// tryLock: flock LOCK_EX|LOCK_NB — khoá gắn với open file description, nên hai
// lần mở riêng trong CÙNG tiến trình cũng loại trừ nhau.
func tryLock(f *os.File) (busy bool, err error) {
	for {
		err = syscall.Flock(int(f.Fd()), syscall.LOCK_EX|syscall.LOCK_NB)
		if errors.Is(err, syscall.EINTR) {
			continue
		}
		break
	}
	if errors.Is(err, syscall.EWOULDBLOCK) || errors.Is(err, syscall.EAGAIN) {
		return true, nil
	}
	return false, err
}

func unlock(f *os.File) { _ = syscall.Flock(int(f.Fd()), syscall.LOCK_UN) }
