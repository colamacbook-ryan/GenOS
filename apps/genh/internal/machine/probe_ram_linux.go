//go:build linux

package machine

import (
	"bufio"
	"fmt"
	"os"
	"strconv"
	"strings"
)

// probeRAM đọc tổng RAM vật lý từ /proc/meminfo trên Linux.
func probeRAM() (uint64, error) {
	f, err := os.Open("/proc/meminfo")
	if err != nil {
		return 0, err
	}
	defer f.Close()

	scanner := bufio.NewScanner(f)
	for scanner.Scan() {
		line := scanner.Text()
		if !strings.HasPrefix(line, "MemTotal:") {
			continue
		}
		fields := strings.Fields(line)
		if len(fields) < 2 {
			return 0, fmt.Errorf("dòng MemTotal không đúng định dạng: %q", line)
		}
		kb, err := strconv.ParseUint(fields[1], 10, 64)
		if err != nil {
			return 0, err
		}
		return kb * 1024, nil
	}
	if err := scanner.Err(); err != nil {
		return 0, err
	}
	return 0, fmt.Errorf("không tìm thấy MemTotal trong /proc/meminfo")
}
