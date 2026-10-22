// Package browseropen mở trình duyệt hệ thống và tạo/định vị lối tắt
// desktop trỏ vào một URL — logic dùng chung giữa Bước 8 (Hoàn tất) của
// `genh install` (internal/install/steps_finalize.go) và các lệnh vận hành
// `genh open`/`genh uninstall` (internal/ops), để không copy-paste cùng một
// cách gọi `xdg-open`/`open`/`rundll32` hay cùng một đường dẫn lối tắt ở hai
// nơi.
package browseropen

import (
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
)

// Open khởi chạy trình duyệt hệ thống vào url và KHÔNG chờ nó thoát (trình
// duyệt là tiến trình chạy lâu, chờ Wait() ở đây sẽ treo tiến trình gọi cho
// tới khi người dùng đóng cửa sổ) — chỉ trả lỗi nếu không khởi chạy được
// tiến trình (binary không có, ví dụ máy không có desktop).
func Open(url string) error {
	name, args, err := command(url)
	if err != nil {
		return err
	}
	cmd := exec.Command(name, args...)
	if err := cmd.Start(); err != nil {
		return err
	}
	go func() { _ = cmd.Wait() }() // reap tiến trình con trong nền, không chặn caller
	return nil
}

func command(url string) (name string, args []string, err error) {
	switch runtime.GOOS {
	case "linux":
		return "xdg-open", []string{url}, nil
	case "darwin":
		return "open", []string{url}, nil
	case "windows":
		return "rundll32", []string{"url.dll,FileProtocolHandler", url}, nil
	default:
		return "", nil, fmt.Errorf("chưa hỗ trợ tự mở trình duyệt trên %s", runtime.GOOS)
	}
}

// ShortcutPath trả về đường dẫn tệp lối tắt mà CreateShortcut sẽ ghi cho
// GOOS hiện tại, KHÔNG đụng đĩa — dùng khi cần biết đường dẫn để xoá (`genh
// uninstall`) mà không tạo lại lối tắt.
func ShortcutPath() (string, error) {
	switch runtime.GOOS {
	case "linux":
		home, err := os.UserHomeDir()
		if err != nil {
			return "", fmt.Errorf("không xác định được thư mục home: %w", err)
		}
		return filepath.Join(home, ".local", "share", "applications", "gen-harness.desktop"), nil
	case "darwin":
		home, err := os.UserHomeDir()
		if err != nil {
			return "", fmt.Errorf("không xác định được thư mục home: %w", err)
		}
		return filepath.Join(home, "Applications", "Gen-Harness.command"), nil
	case "windows":
		appData := os.Getenv("APPDATA")
		if appData == "" {
			home, err := os.UserHomeDir()
			if err != nil {
				return "", fmt.Errorf("không xác định được %%APPDATA%% lẫn thư mục home: %w", err)
			}
			appData = filepath.Join(home, "AppData", "Roaming")
		}
		return filepath.Join(appData, "Microsoft", "Windows", "Start Menu", "Programs", "Gen-Harness.url"), nil
	default:
		return "", fmt.Errorf("chưa hỗ trợ lối tắt trên %s", runtime.GOOS)
	}
}

// CreateShortcut tạo một lối tắt desktop trỏ vào url tại ShortcutPath(),
// trả về đường dẫn tệp đã ghi. KHÔNG cần quyền rộng — tạo một tệp trong thư
// mục riêng của user.
func CreateShortcut(url string) (string, error) {
	switch runtime.GOOS {
	case "linux":
		return createLinux(url)
	case "darwin":
		return createDarwin(url)
	case "windows":
		return createWindows(url)
	default:
		return "", fmt.Errorf("chưa hỗ trợ tạo lối tắt trên %s", runtime.GOOS)
	}
}

// createLinux ghi một tệp .desktop vào ~/.local/share/applications — chuẩn
// XDG Desktop Entry, được hầu hết môi trường desktop Linux (GNOME, KDE,
// XFCE…) tự nhận vào trình đơn ứng dụng mà không cần đăng ký gì thêm.
func createLinux(url string) (string, error) {
	path, err := ShortcutPath()
	if err != nil {
		return "", err
	}
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return "", fmt.Errorf("tạo thư mục %s: %w", filepath.Dir(path), err)
	}
	content := "[Desktop Entry]\n" +
		"Type=Application\n" +
		"Name=Gen-Harness\n" +
		"Comment=Mở trình thiết lập Gen-Harness\n" +
		"Exec=xdg-open " + url + "\n" +
		"Icon=utilities-terminal\n" +
		"Terminal=false\n" +
		"Categories=Utility;\n"
	if err := os.WriteFile(path, []byte(content), 0o644); err != nil {
		return "", fmt.Errorf("ghi %s: %w", path, err)
	}
	return path, nil
}

// createDarwin tạo một script .command có quyền thực thi trong
// ~/Applications — double-click trong Finder mở Terminal chạy `open <url>`.
// Cách đơn giản nhất khả thi mà không cần dựng .app bundle/AppleScript.
func createDarwin(url string) (string, error) {
	path, err := ShortcutPath()
	if err != nil {
		return "", err
	}
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return "", fmt.Errorf("tạo thư mục %s: %w", filepath.Dir(path), err)
	}
	content := "#!/bin/sh\nopen \"" + url + "\"\n"
	if err := os.WriteFile(path, []byte(content), 0o755); err != nil {
		return "", fmt.Errorf("ghi %s: %w", path, err)
	}
	return path, nil
}

// createWindows ghi một Internet Shortcut (.url) vào Start Menu Programs
// của user — định dạng text đơn giản, KHÔNG cần COM/admin như .lnk thật.
func createWindows(url string) (string, error) {
	path, err := ShortcutPath()
	if err != nil {
		return "", err
	}
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return "", fmt.Errorf("tạo thư mục %s: %w", filepath.Dir(path), err)
	}
	content := "[InternetShortcut]\r\nURL=" + url + "\r\n"
	if err := os.WriteFile(path, []byte(content), 0o644); err != nil {
		return "", fmt.Errorf("ghi %s: %w", path, err)
	}
	return path, nil
}
