//go:build windows

package machine

import (
	"fmt"
	"syscall"
	"unsafe"
)

// probeDiskFree đọc dung lượng trống tại path bằng GetDiskFreeSpaceExW.
func probeDiskFree(path string) (uint64, error) {
	kernel32 := syscall.NewLazyDLL("kernel32.dll")
	proc := kernel32.NewProc("GetDiskFreeSpaceExW")

	p, err := syscall.UTF16PtrFromString(path)
	if err != nil {
		return 0, err
	}

	var freeAvailable, totalBytes, totalFree uint64
	ret, _, callErr := proc.Call(
		uintptr(unsafe.Pointer(p)),
		uintptr(unsafe.Pointer(&freeAvailable)),
		uintptr(unsafe.Pointer(&totalBytes)),
		uintptr(unsafe.Pointer(&totalFree)),
	)
	if ret == 0 {
		return 0, fmt.Errorf("GetDiskFreeSpaceExW thất bại: %w", callErr)
	}
	return freeAvailable, nil
}
