//go:build windows

package hostlink

import (
	"context"
	"os"
)

// ensureRunDirs (Windows): như trước v0.1.45 — Docker Desktop tự ánh xạ quyền
// thư mục chia sẻ, không có uid/gid kiểu Unix để siết.
func ensureRunDirs(dirs []string) error {
	for _, d := range dirs {
		if err := os.MkdirAll(d, 0o777); err != nil {
			return err
		}
		if err := os.Chmod(d, 0o777); err != nil {
			return err
		}
	}
	return nil
}

// requestOwnerOK (Windows): không có uid kiểu Unix — giữ hành vi cũ.
func requestOwnerOK(string, os.FileInfo) bool { return true }

// EnsureRunPerms (Windows): không đổi gì — RunModeNA.
func EnsureRunPerms(context.Context, RunPermSpec) (string, error) { return RunModeNA, nil }
