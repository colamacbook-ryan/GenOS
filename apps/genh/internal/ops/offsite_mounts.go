package ops

import (
	"path/filepath"
	"strconv"
	"strings"
)

// mountEntry là một dòng /proc/self/mountinfo đã tách: điểm mount, kiểu hệ
// tệp và nguồn (vd /dev/sda1, tmpfs, 192.168.1.5:/share).
type mountEntry struct {
	Point  string
	FSType string
	Source string
}

// volatileFSTypes: hệ tệp nằm trong RAM / lớp ghi đè — bản sao đặt ở đây mất
// khi khởi động lại (tmpfs /tmp, /dev/shm) hoặc vẫn nằm trên ổ chính
// (overlay của container). Không bao giờ là "ổ USB/NAS".
var volatileFSTypes = map[string]bool{"tmpfs": true, "ramfs": true, "devtmpfs": true, "overlay": true}

// unescapeMount giải mã \040 (dấu cách), \011, \012, \134 trong mountinfo.
func unescapeMount(s string) string {
	if !strings.Contains(s, `\`) {
		return s
	}
	var b strings.Builder
	for i := 0; i < len(s); i++ {
		if s[i] == '\\' && i+3 < len(s) {
			if n, err := strconv.ParseUint(s[i+1:i+4], 8, 8); err == nil {
				b.WriteByte(byte(n))
				i += 3
				continue
			}
		}
		b.WriteByte(s[i])
	}
	return b.String()
}

// parseMountinfo tách nội dung /proc/self/mountinfo (hàm thuần, test được
// trên mọi hệ). Dòng hỏng bị bỏ qua.
func parseMountinfo(content string) []mountEntry {
	var out []mountEntry
	for _, line := range strings.Split(content, "\n") {
		left, right, ok := strings.Cut(line, " - ")
		if !ok {
			continue
		}
		lf := strings.Fields(left)
		rf := strings.Fields(right)
		if len(lf) < 5 || len(rf) < 2 {
			continue
		}
		out = append(out, mountEntry{Point: unescapeMount(lf[4]), FSType: rf[0], Source: unescapeMount(rf[1])})
	}
	return out
}

// mountFor trả điểm mount sâu nhất chứa path (path đã Clean/giải symlink).
// Điểm mount trùng nhau (mount chồng) ⇒ dòng sau thắng như kernel.
func mountFor(entries []mountEntry, path string) (mountEntry, bool) {
	var best mountEntry
	found := false
	for _, e := range entries {
		if !isWithin(path, filepath.Clean(e.Point)) {
			continue
		}
		if !found || len(e.Point) >= len(best.Point) {
			best, found = e, true
		}
	}
	return best, found
}

// volatileKind (hàm thuần): kiểu hệ tệp tạm của path ("tmpfs"…) hoặc "".
func volatileKind(entries []mountEntry, path string) string {
	if e, ok := mountFor(entries, path); ok && volatileFSTypes[e.FSType] {
		return e.FSType
	}
	return ""
}

// sameBlockSource (hàm thuần): dest và installDir nằm trên CÙNG một thiết bị
// khối (/dev/…) dù Stat_t.Dev khác nhau — vd hai subvolume btrfs của cùng ổ,
// bind mount. Nguồn không phải /dev/… (NAS, tmpfs) ⇒ false.
func sameBlockSource(entries []mountEntry, dest, installDir string) bool {
	a, okA := mountFor(entries, dest)
	b, okB := mountFor(entries, installDir)
	return okA && okB && strings.HasPrefix(a.Source, "/dev/") && a.Source == b.Source
}
