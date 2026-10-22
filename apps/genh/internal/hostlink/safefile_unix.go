//go:build !windows

package hostlink

import (
	"os"
	"syscall"
)

// openNoFollow mở chỉ-đọc, KHÔNG đi theo symlink ở thành phần cuối, không
// treo ở FIFO.
func openNoFollow(path string) (*os.File, error) {
	return os.OpenFile(path, os.O_RDONLY|syscall.O_NOFOLLOW|syscall.O_NONBLOCK, 0)
}

func links(fi os.FileInfo) uint64 {
	if st, ok := fi.Sys().(*syscall.Stat_t); ok {
		return uint64(st.Nlink) // Nlink là uint16 trên darwin, uint64 trên linux
	}
	return 1
}

func ownedBySelf(fi os.FileInfo) bool {
	st, ok := fi.Sys().(*syscall.Stat_t)
	return ok && int(st.Uid) == os.Getuid()
}
