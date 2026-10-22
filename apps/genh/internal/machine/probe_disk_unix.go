//go:build !windows

package machine

import "syscall"

// probeDiskFree đọc dung lượng trống tại path bằng statfs (Linux/macOS).
func probeDiskFree(path string) (uint64, error) {
	var stat syscall.Statfs_t
	if err := syscall.Statfs(path, &stat); err != nil {
		return 0, err
	}
	return uint64(stat.Bavail) * uint64(stat.Bsize), nil
}
