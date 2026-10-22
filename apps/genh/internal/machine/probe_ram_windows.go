//go:build windows

package machine

import (
	"fmt"
	"syscall"
	"unsafe"
)

// memoryStatusEx phản chiếu MEMORYSTATUSEX của Windows API, đủ trường cần
// cho GlobalMemoryStatusEx. Không dùng golang.org/x/sys/windows vì gói đó
// chưa bọc sẵn hàm này; gọi thẳng qua syscall.NewLazyDLL (chỉ biên dịch khi
// GOOS=windows).
type memoryStatusEx struct {
	Length               uint32
	MemoryLoad           uint32
	TotalPhys            uint64
	AvailPhys            uint64
	TotalPageFile        uint64
	AvailPageFile        uint64
	TotalVirtual         uint64
	AvailVirtual         uint64
	AvailExtendedVirtual uint64
}

// probeRAM đọc tổng RAM vật lý qua GlobalMemoryStatusEx trên Windows.
func probeRAM() (uint64, error) {
	kernel32 := syscall.NewLazyDLL("kernel32.dll")
	proc := kernel32.NewProc("GlobalMemoryStatusEx")

	var status memoryStatusEx
	status.Length = uint32(unsafe.Sizeof(status))

	ret, _, err := proc.Call(uintptr(unsafe.Pointer(&status)))
	if ret == 0 {
		return 0, fmt.Errorf("GlobalMemoryStatusEx thất bại: %w", err)
	}
	return status.TotalPhys, nil
}
