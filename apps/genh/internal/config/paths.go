// Package config định nghĩa các đường dẫn chuẩn mà genh dùng trên máy
// người dùng (~/.gen-harness và các thư mục con), tách khỏi logic sinh bí
// mật (internal/secrets) để dễ tiêm thư mục giả khi test.
package config

import (
	"os"
	"path/filepath"
	"runtime"
)

// Paths gom các thư mục genh dùng dưới một gốc cài đặt (Root).
type Paths struct {
	Root string
}

// EnvRoot là biến môi trường chỉ định gốc cài đặt — install.sh/install.ps1
// đặt binary genh theo biến này, nên genh cũng phải dùng đúng gốc đó.
const EnvRoot = "GEN_HARNESS_HOME"

// DefaultRoot trả về thư mục gốc cài đặt mặc định của genh:
//   - $GEN_HARNESS_HOME nếu có đặt
//   - Windows: %LOCALAPPDATA%\GenHarness
//   - Linux/macOS: ~/.gen-harness
func DefaultRoot() (string, error) {
	if v := os.Getenv(EnvRoot); v != "" {
		return v, nil
	}
	return HomeRoot()
}

// HomeRoot là gốc cài đặt mặc định theo tài khoản, BỎ QUA $GEN_HARNESS_HOME:
// Windows %LOCALAPPDATA%\GenHarness, còn lại ~/.gen-harness. Dùng khi cần gốc mà
// một tiến trình chạy NGOÀI phiên shell (lịch systemd/cron/launchd/schtasks không
// mang biến này) sẽ tự tìm thấy — không phụ thuộc biến môi trường của người gọi.
func HomeRoot() (string, error) {
	if runtime.GOOS == "windows" {
		if v := os.Getenv("LOCALAPPDATA"); v != "" {
			return filepath.Join(v, "GenHarness"), nil
		}
	}
	home, err := os.UserHomeDir()
	if err != nil {
		return "", err
	}
	return filepath.Join(home, ".gen-harness"), nil
}

// New tạo Paths từ một gốc cụ thể (dùng DefaultRoot() cho máy thật, hoặc
// một thư mục tạm khi test).
func New(root string) Paths {
	return Paths{Root: root}
}

// ConfigDir chứa bí mật/cấu hình sinh ra ở Bước 4 (quyền 0700/0600).
func (p Paths) ConfigDir() string { return filepath.Join(p.Root, "config") }

// BinDir chứa binary genh và các công cụ genh tự tải (compose plugin…).
func (p Paths) BinDir() string { return filepath.Join(p.Root, "bin") }

// LogsDir chứa log cài đặt/vận hành, ví dụ install-<timestamp>.log.
func (p Paths) LogsDir() string { return filepath.Join(p.Root, "logs") }

// RuntimeDir chứa container runtime tự cài (Docker rootless / Colima / hệ
// tối giản WSL) khi máy chưa có sẵn.
func (p Paths) RuntimeDir() string { return filepath.Join(p.Root, "runtime") }

// DataDir chứa dữ liệu bền vững của các dịch vụ (db, objects…).
func (p Paths) DataDir() string { return filepath.Join(p.Root, "data") }
