//go:build windows

package hostlink

import "os"

// Windows: Lstat đã loại symlink/junction (không phải tệp thường); không có
// O_NOFOLLOW/uid kiểu Unix — run/ không phải thư mục 0777 bind-mount như Linux.
func openNoFollow(path string) (*os.File, error) { return os.Open(path) }

func links(os.FileInfo) uint64 { return 1 }

func ownedBySelf(os.FileInfo) bool { return true }
