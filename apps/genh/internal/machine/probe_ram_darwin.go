//go:build darwin

package machine

import (
	"context"
	"os/exec"
	"strconv"
	"strings"
	"time"
)

// probeRAM đọc tổng RAM vật lý qua `sysctl -n hw.memsize` trên macOS. Không
// dùng cgo/syscall trực tiếp để giữ genh biên dịch tĩnh, đơn giản.
func probeRAM() (uint64, error) {
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()
	out, err := exec.CommandContext(ctx, "sysctl", "-n", "hw.memsize").Output()
	if err != nil {
		return 0, err
	}
	return strconv.ParseUint(strings.TrimSpace(string(out)), 10, 64)
}
