// Package pull cài Bước 3 (Tải image, trọng số 50% — lớn nhất trong 8 bước)
// của trình cài genh: tải song song các image compose.yaml đã ghim sẵn
// (db, redis, objects, proxy, api, web, bridge), tính % tổng hợp theo BYTE
// THẬT của các layer (không đoán, không chia đều theo số image) — đúng yêu
// cầu docs/handoff/05-installer.md.
//
// Khi stdout của tiến trình con KHÔNG phải TTY — luôn đúng khi genh tự chạy
// `docker pull` làm subprocess qua os/exec — chính docker CLI in tiến độ
// dạng NDJSON (một dòng một object JSON, giống hệt Docker Engine API
// `/images/create` trả về) thay vì vẽ thanh tiến độ, nên Puller phân tích
// được tiến độ theo byte mà không cần dùng thư viện Docker Engine API.
package pull

import (
	"context"
	"encoding/json"

	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/dockercli"
)

// Event là một lần cập nhật tiến độ tải một image, tương ứng một dòng NDJSON
// docker CLI in ra.
type Event struct {
	Image   string
	LayerID string
	Status  string // "Pulling fs layer", "Downloading", "Verifying Checksum", "Extracting", "Pull complete", "Already exists", "Downloaded newer image for…"…
	Current int64  // byte đã tải của layer này (0 nếu Status không mang progressDetail)
	Total   int64  // tổng byte của layer này (0 nếu chưa biết)
	ErrMsg  string // khác rỗng nếu dòng này là lỗi (docker pull in {"error":"..."} khi thất bại)
}

// Puller tải một image, phát Event qua onEvent cho mỗi dòng tiến độ. Cài đặt
// thật là CLIPuller; test tiêm một Puller giả phát Event định sẵn, không
// đụng Docker/mạng thật.
type Puller interface {
	Pull(ctx context.Context, image string, onEvent func(Event)) error
}

// CLIPuller là Puller thật, gọi `docker pull <image>` qua dockercli.Runner.
type CLIPuller struct {
	Runner dockercli.Runner
	// Env là biến môi trường thêm cho tiến trình `docker pull` (ví dụ
	// DOCKER_HOST nếu Bước 2 vừa tự cài runtime rootless) — hầu hết trường
	// hợp để trống, dùng runtime mặc định của máy.
	Env []string
}

func (p CLIPuller) Pull(ctx context.Context, image string, onEvent func(Event)) error {
	runner := p.Runner
	if runner == nil {
		runner = dockercli.ExecRunner{}
	}
	return runner.Stream(ctx, dockercli.Cmd{Name: "docker", Args: []string{"pull", image}, Env: p.Env}, func(line string) {
		if ev, ok := ParsePullLine(image, line); ok {
			onEvent(ev)
		}
	})
}

// pullLineJSON phản ánh đúng các khoá NDJSON docker CLI/Engine API in ra
// cho `docker pull`/`POST /images/create`.
type pullLineJSON struct {
	Status         string `json:"status"`
	ID             string `json:"id"`
	Error          string `json:"error"`
	ProgressDetail struct {
		Current int64 `json:"current"`
		Total   int64 `json:"total"`
	} `json:"progressDetail"`
}

// ParsePullLine phân tích một dòng NDJSON của `docker pull` thành Event —
// hàm thuần, test bằng chuỗi mẫu, không cần Docker thật. ok=false nếu dòng
// không phải JSON hợp lệ (docker pull thỉnh thoảng in dòng trắng hoặc chú
// thích không phải JSON — bỏ qua thay vì lỗi cả tiến trình).
func ParsePullLine(image, line string) (Event, bool) {
	var raw pullLineJSON
	if err := json.Unmarshal([]byte(line), &raw); err != nil {
		return Event{}, false
	}
	return Event{
		Image:   image,
		LayerID: raw.ID,
		Status:  raw.Status,
		Current: raw.ProgressDetail.Current,
		Total:   raw.ProgressDetail.Total,
		ErrMsg:  raw.Error,
	}, true
}
