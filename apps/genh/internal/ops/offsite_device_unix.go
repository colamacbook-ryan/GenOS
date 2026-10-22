//go:build !windows

package ops

import (
	"fmt"
	"os"
	"syscall"
)

// sameDevice (Unix): dest và installDir cùng thiết bị (Stat_t.Dev)? Ổ USB/NAS
// đã mount có Dev riêng; rút ổ ra thì thư mục mount rỗng nằm lại trên ổ chính
// — cùng Dev với gốc cài ⇒ coi là "chưa thấy ổ USB/NAS". Dev khác nhưng CÙNG
// thiết bị khối theo /proc/self/mountinfo (subvolume btrfs, bind mount của
// cùng ổ) cũng là cùng ổ: hỏng ổ là mất cả hai.
func sameDevice(dest, installDir string) (bool, error) {
	a, err := os.Stat(dest)
	if err != nil {
		return false, err
	}
	b, err := os.Stat(installDir)
	if err != nil {
		return false, err
	}
	sa, ok1 := a.Sys().(*syscall.Stat_t)
	sb, ok2 := b.Sys().(*syscall.Stat_t)
	if !ok1 || !ok2 {
		return false, fmt.Errorf("không đọc được thông tin thiết bị")
	}
	if sa.Dev == sb.Dev {
		return true, nil
	}
	return sameBlockSource(readMounts(), resolveForCompare(dest), resolveForCompare(installDir)), nil
}

// volatileFS (Unix): kiểu hệ tệp tạm (tmpfs/ramfs/overlay) chứa dest, hoặc "".
// Không có /proc (macOS) ⇒ "" (chỉ còn kiểm Dev).
func volatileFS(dest string) string {
	return volatileKind(readMounts(), resolveForCompare(dest))
}

func readMounts() []mountEntry {
	b, err := os.ReadFile("/proc/self/mountinfo")
	if err != nil {
		return nil
	}
	return parseMountinfo(string(b))
}
