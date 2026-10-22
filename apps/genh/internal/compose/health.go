package compose

import (
	"context"
	"encoding/json"
	"fmt"
	"strings"
	"time"

	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/dockercli"
)

// ContainerStatus là phần cần của một dòng `docker compose ps --format
// json`.
type ContainerStatus struct {
	Service string `json:"Service"`
	State   string `json:"State"`  // "running", "exited", "created"…
	Health  string `json:"Health"` // "healthy"/"starting"/"unhealthy", hoặc "" nếu service không có healthcheck
}

// psEntry ánh xạ đúng các khoá `docker compose ps --format json` thật xuất
// ra — tên trường viết hoa chữ đầu, khác chút so với ContainerStatus dùng
// nội bộ nên tách struct thay vì gắn json tag lên ContainerStatus, tránh
// nhầm khi Compose đổi định dạng.
type psEntry struct {
	Service string `json:"Service"`
	State   string `json:"State"`
	Health  string `json:"Health"`
}

// ParsePS đọc output của `docker compose ps --format json`. Compose v2 tuỳ
// phiên bản in một mảng JSON DUY NHẤT hoặc NDJSON (một dòng một object) —
// ParsePS thử cả hai, không giả định trước.
func ParsePS(data []byte) ([]ContainerStatus, error) {
	trimmed := strings.TrimSpace(string(data))
	if trimmed == "" {
		return nil, nil
	}

	if trimmed[0] == '[' {
		var entries []psEntry
		if err := json.Unmarshal([]byte(trimmed), &entries); err != nil {
			return nil, fmt.Errorf("phân tích `docker compose ps --format json` (dạng mảng): %w", err)
		}
		return toStatuses(entries), nil
	}

	var entries []psEntry
	for _, line := range strings.Split(trimmed, "\n") {
		line = strings.TrimSpace(line)
		if line == "" {
			continue
		}
		var e psEntry
		if err := json.Unmarshal([]byte(line), &e); err != nil {
			return nil, fmt.Errorf("phân tích `docker compose ps --format json` (dòng %q): %w", line, err)
		}
		entries = append(entries, e)
	}
	return toStatuses(entries), nil
}

func toStatuses(entries []psEntry) []ContainerStatus {
	out := make([]ContainerStatus, len(entries))
	for i, e := range entries {
		out[i] = ContainerStatus{Service: e.Service, State: e.State, Health: e.Health}
	}
	return out
}

// AllHealthy đối chiếu statuses với danh sách services cần có, trả về
// ok=true chỉ khi MỌI service đều "healthy" (hoặc "running" nếu service đó
// không khai báo healthcheck — Health rỗng), kèm mô tả ngắn từng service để
// hiển thị làm SubLines. Hàm thuần, test bằng dữ liệu ContainerStatus dựng
// tay, không cần Docker thật.
func AllHealthy(statuses []ContainerStatus, services []string) (ok bool, detail map[string]string) {
	bySvc := make(map[string]ContainerStatus, len(statuses))
	for _, s := range statuses {
		bySvc[s.Service] = s
	}

	detail = make(map[string]string, len(services))
	ok = true
	for _, name := range services {
		st, found := bySvc[name]
		switch {
		case !found:
			detail[name] = "chưa thấy container"
			ok = false
		case st.Health == "healthy":
			detail[name] = "healthy"
		case st.Health == "" && st.State == "running":
			detail[name] = "đang chạy (không khai báo healthcheck)"
		case st.Health != "":
			detail[name] = st.Health
			ok = false
		default:
			detail[name] = st.State
			ok = false
		}
	}
	return ok, detail
}

// PollInterval là khoảng nghỉ giữa hai lần gọi `docker compose ps` khi chờ
// healthy — đủ nhanh để TUI cảm giác mượt, không dồn dập gọi CLI vô ích.
// Biến (không phải const) và XUẤT RA để test của package này lẫn
// internal/install (Bước 5/7 dùng WaitHealthy) rút ngắn được khi test —
// không nên mất giây thật cho mỗi lần lặp trong test.
var PollInterval = 2 * time.Second

// WaitHealthy gọi `docker compose ps --format json` lặp lại cho tới khi mọi
// service trong services đều healthy (AllHealthy), hoặc hết timeout.
// onTick, nếu khác nil, được gọi sau mỗi lần đọc (kể cả khi lỗi phân tích)
// để Step báo tiến độ qua Reporter.
func WaitHealthy(ctx context.Context, r dockercli.Runner, composePath string, env []string, services []string, timeout time.Duration, onTick func(detail map[string]string)) error {
	deadline := time.Now().Add(timeout)
	args := BaseArgs(composePath, append([]string{"ps", "--format", "json"}, services...)...)

	for {
		out, err := r.Output(ctx, dockercli.Cmd{Name: "docker", Args: args, Env: env})
		if err == nil {
			statuses, perr := ParsePS(out)
			if perr == nil {
				ok, detail := AllHealthy(statuses, services)
				if onTick != nil {
					onTick(detail)
				}
				if ok {
					return nil
				}
			}
		}

		if time.Now().After(deadline) {
			return fmt.Errorf("hết thời gian chờ %s", timeout)
		}

		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-time.After(PollInterval):
		}
	}
}
