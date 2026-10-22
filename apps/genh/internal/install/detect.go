package install

import (
	"context"
	"path/filepath"

	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/compose"
	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/config"
	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/dockercli"
	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/secretgen"
)

// DetectExistingInstall báo máy này ĐÃ có một bản cài Gen-Harness HOÀN
// CHỈNH hay chưa — dùng bởi `genh install` (cmd/genh/main.go) để DỪNG SỚM,
// TRƯỚC KHI chạy 8 Bước, nếu Owner lỡ chạy lại `genh install` trên một máy
// đã cài. Đây là tình huống THẬT (đã xác minh trên máy Owner): install.sh/
// install.ps1 `exec genh install` VÔ ĐIỀU KIỆN ngay sau khi tự cập nhật
// binary genh (xem docs/reports/HANDOFF-v0.1.1.md mục "Lỗi cần sửa" #2 của
// v0.1.3) — chạy lại toàn bộ 8 Bước trên một máy đã cài sẽ DỰNG LẠI container
// (Bước 2/5/7 idempotent theo hướng "container không có thì tạo", KHÔNG tự
// hỏi "đã có container SỐNG của bản cài cũ chưa"), BỎ QUA backup + di trú dữ
// liệu mà CHỈ `genh update` mới làm đúng — MẤT dữ liệu/tài liệu Owner.
//
// TIÊU CHÍ CHẮC CHẮN đã cài xong (KHÔNG được chặn nhầm một lần cài DỞ DANG —
// Runner.Run tiếp tục đúng bước còn thiếu nếu bị ngắt giữa chừng, xem
// runner.go, đây chính là cơ chế "chạy lại tiếp tục" mà genh install PHẢI
// giữ nguyên cho một lần cài chưa xong):
//
//  1. secrets.json đã tồn tại (Bước 4 — Sinh bí mật — đã xong xuôi), VÀ
//  2. `docker compose ps -a` thấy container service "api" (Bước 5 trở đi mới
//     tạo container này) ở BẤT KỲ trạng thái nào (kể cả "exited" — nghĩa là
//     container ĐÃ được compose tạo ra, không phải "chưa tới Bước 5").
//
// Thiếu MỘT trong hai điều kiện trên (secrets.json chưa có, HOẶC secrets.json
// đã có nhưng chưa có container api nào — ví dụ dừng giữa Bước 4 và Bước 5)
// nghĩa là "chưa cài xong hẳn" → trả về false, để genh install chạy tiếp
// bình thường, không chặn.
//
// Lỗi đọc Docker (daemon chưa chạy, chưa có docker compose…) không được coi
// là "đã cài" — trả về false, im lặng, để chính 8 Bước tự phát hiện + báo lỗi
// rõ ràng hơn (Bước 1/2 kiểm máy/runtime).
func DetectExistingInstall(ctx context.Context, runner dockercli.Runner, installDir string) bool {
	if installDir == "" {
		return false
	}
	if !secretgen.Exists(config.New(installDir).ConfigDir()) {
		return false
	}

	composePath, err := compose.Locate(installDir)
	if err != nil {
		return false
	}

	psArgs := compose.BaseArgs(composePath, "ps", "-a", "--format", "json", "api")
	out, err := runner.Output(ctx, dockercli.Cmd{Name: "docker", Args: psArgs, Dir: filepath.Dir(composePath)})
	if err != nil {
		return false
	}
	statuses, err := compose.ParsePS(out)
	if err != nil {
		return false
	}
	for _, s := range statuses {
		if s.Service == "api" {
			return true
		}
	}
	return false
}
