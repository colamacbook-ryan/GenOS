// Package compose đọc deploy/compose.yaml và gọi `docker compose` qua
// dockercli.Runner — dùng chung cho Bước 3 (danh sách image cần tải), Bước
// 5 (khởi động dữ liệu, chờ healthy) và Bước 7 (khởi động dịch vụ).
package compose

import (
	"fmt"
	"os"

	"gopkg.in/yaml.v3"
)

// Service là phần genh cần biết về một service trong compose.yaml.
type Service struct {
	Name  string
	Image string // rỗng nếu service dùng "build:" (chưa có image sẵn để pull)
	Build bool   // true nếu service có khoá "build:" (ảnh phải build/đã build từ nguồn, không pull được)
}

// File là kết quả đọc một compose.yaml.
type File struct {
	Path     string
	Services map[string]Service
}

// rawFile phản ánh đủ phần cần của compose.yaml để lấy service/image — các
// khoá khác (x-app-env, secrets, volumes, environment…) bị bỏ qua có chủ ý:
// yaml.v3 không lỗi khi struct đích thiếu trường, và alias YAML (*app-env)
// đã được chính parser giải quyết trước khi ánh xạ vào struct nên không cần
// xử lý riêng.
type rawFile struct {
	Services map[string]struct {
		Image string `yaml:"image"`
		Build any    `yaml:"build"`
	} `yaml:"services"`
}

// Parse đọc nội dung compose.yaml (đã đọc sẵn từ đĩa) thành File — hàm
// thuần, test bằng chuỗi YAML mẫu, không cần tệp thật.
func Parse(data []byte) (File, error) {
	var raw rawFile
	if err := yaml.Unmarshal(data, &raw); err != nil {
		return File{}, fmt.Errorf("compose.yaml không hợp lệ: %w", err)
	}
	services := make(map[string]Service, len(raw.Services))
	for name, svc := range raw.Services {
		services[name] = Service{
			Name:  name,
			Image: svc.Image,
			Build: svc.Build != nil,
		}
	}
	return File{Services: services}, nil
}

// Load đọc và Parse compose.yaml tại path.
func Load(path string) (File, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return File{}, fmt.Errorf("đọc %s: %w", path, err)
	}
	f, err := Parse(data)
	if err != nil {
		return File{}, fmt.Errorf("%s: %w", path, err)
	}
	f.Path = path
	return f, nil
}

// BaseArgs dựng phần đầu chung của mọi lệnh `docker compose` genh gọi —
// luôn dùng compose plugin genh mang theo qua `docker compose` (không phải
// binary docker-compose v1 rời), luôn chỉ định -f rõ ràng (không phụ thuộc
// thư mục làm việc/biến COMPOSE_FILE của người dùng).
func BaseArgs(composePath string, sub ...string) []string {
	args := []string{"compose", "-f", composePath}
	return append(args, sub...)
}
