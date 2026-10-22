//go:build windows

package ops

// sameDevice (Windows): so tên ổ (C: so với E:) — UNC \\NAS\share luôn coi là
// khác ổ của máy chủ. Xem sameVolumeWindows (hàm thuần, test trên mọi hệ).
func sameDevice(dest, installDir string) (bool, error) {
	return sameVolumeWindows(dest, installDir), nil
}

// volatileFS (Windows): không có tmpfs/overlay — luôn "".
func volatileFS(string) string { return "" }
