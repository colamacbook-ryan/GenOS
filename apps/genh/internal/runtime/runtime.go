// Package runtime cài Bước 2 (Chuẩn bị container runtime) của trình cài
// genh: phát hiện Docker Engine ≥ 24 + Compose v2 có sẵn hay chưa, và nếu
// chưa thì tự cung cấp runtime — Docker Engine rootless tĩnh trên Linux,
// Colima+Lima trên macOS, WSL2 trên Windows — theo đúng
// docs/handoff/05-installer.md mục "Container runtime — tự cung cấp".
//
// Phần phát hiện (Detect) chạy giống nhau trên mọi nền tảng, chỉ gọi
// `docker version`/`docker compose version` qua dockercli.Runner nên test
// được đầy đủ bằng Runner giả. Phần tự cài (Bootstrap) khác nhau theo GOOS,
// cài trong bootstrap_linux.go/bootstrap_darwin.go/bootstrap_windows.go
// (build tag) — cùng chữ ký hàm, chỉ một bản được biên dịch theo GOOS đích,
// giống cách internal/machine tách probeRAM/probeDiskFree theo nền tảng.
package runtime

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os/exec"
	"strconv"
	"strings"

	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/dockercli"
)

// MinEngineMajor là phiên bản Docker Engine tối thiểu theo tài liệu
// ("Engine ≥ 24 và Compose v2").
const MinEngineMajor = 24

// Info là kết quả phát hiện runtime hiện có trên máy.
type Info struct {
	Available      bool   // Docker daemon phản hồi được
	EngineVersion  string // ví dụ "27.1.1", rỗng nếu không phát hiện được
	EngineOK       bool   // Available && EngineVersion >= MinEngineMajor
	ComposeVersion string // ví dụ "v2.29.1"
	ComposeOK      bool   // có compose plugin v2
	Reason         string // lý do Available/ComposeOK=false, tiếng Việt, dùng làm Detail/Why
}

// Ready cho biết runtime hiện có đã đủ dùng ngay (bỏ qua Bước 2) hay chưa.
func (i Info) Ready() bool { return i.EngineOK && i.ComposeOK }

// dockerVersionJSON phản ánh đủ phần cần của `docker version --format
// '{{json .}}'` — chỉ lấy Server.Version, bỏ qua các trường khác.
type dockerVersionJSON struct {
	Server *struct {
		Version string `json:"Version"`
	} `json:"Server"`
	Client *struct {
		Version string `json:"Version"`
	} `json:"Client"`
}

type composeVersionJSON struct {
	Version string `json:"version"`
}

// Detect gọi `docker version`/`docker compose version` qua r và trả về Info.
// Không trả error: mọi tình huống (chưa cài Docker, daemon không chạy,
// thiếu compose plugin) đều là kết quả hợp lệ của Bước 2 (nghĩa là "cần tự
// cài"), không phải lỗi chương trình.
func Detect(ctx context.Context, r dockercli.Runner) Info {
	out, err := r.Output(ctx, dockercli.Cmd{Name: "docker", Args: []string{"version", "--format", "{{json .}}"}})
	if err != nil {
		if isNotFound(err) {
			return Info{Reason: "chưa cài Docker"}
		}
		return Info{Reason: "Docker đã cài nhưng daemon không phản hồi (" + shortErr(err) + ")"}
	}

	version, err := parseServerVersion(out)
	if err != nil || version == "" {
		return Info{Reason: "không đọc được phiên bản Docker Engine từ `docker version` (" + shortErr(err) + ")"}
	}

	info := Info{Available: true, EngineVersion: version, EngineOK: EngineMeetsMinimum(version)}
	if !info.EngineOK {
		info.Reason = fmt.Sprintf("Docker Engine %s cũ hơn mức tối thiểu %d", version, MinEngineMajor)
	}

	composeOut, cErr := r.Output(ctx, dockercli.Cmd{Name: "docker", Args: []string{"compose", "version", "--format", "json"}})
	if cErr != nil {
		info.Reason = appendReason(info.Reason, "thiếu Compose v2 plugin ("+shortErr(cErr)+")")
		return info
	}
	cv, cErr := parseComposeVersion(composeOut)
	if cErr != nil || cv == "" {
		info.Reason = appendReason(info.Reason, "không đọc được phiên bản Compose")
		return info
	}
	info.ComposeVersion = cv
	info.ComposeOK = true
	return info
}

func appendReason(existing, add string) string {
	if existing == "" {
		return add
	}
	return existing + "; " + add
}

// parseServerVersion đọc trường Server.Version từ JSON của `docker version
// --format '{{json .}}'` — hàm thuần, test bằng chuỗi JSON mẫu.
func parseServerVersion(data []byte) (string, error) {
	var v dockerVersionJSON
	if err := json.Unmarshal(data, &v); err != nil {
		return "", err
	}
	if v.Server != nil && v.Server.Version != "" {
		return v.Server.Version, nil
	}
	return "", nil
}

func parseComposeVersion(data []byte) (string, error) {
	var v composeVersionJSON
	if err := json.Unmarshal(data, &v); err != nil {
		return "", err
	}
	return v.Version, nil
}

// EngineMeetsMinimum so sánh số hiệu chính (major) của version với
// MinEngineMajor — hàm thuần, test độc lập với các định dạng version thật
// gặp ("27.1.1", "24.0.9", "v25.0.3", rác).
func EngineMeetsMinimum(version string) bool {
	major, ok := parseMajor(version)
	return ok && major >= MinEngineMajor
}

func parseMajor(version string) (int, bool) {
	v := strings.TrimPrefix(strings.TrimSpace(version), "v")
	dot := strings.IndexByte(v, '.')
	if dot > 0 {
		v = v[:dot]
	}
	n, err := strconv.Atoi(v)
	if err != nil {
		return 0, false
	}
	return n, true
}

func isNotFound(err error) bool {
	return errors.Is(err, exec.ErrNotFound)
}

func shortErr(err error) string {
	if err == nil {
		return "?"
	}
	s := err.Error()
	const max = 160
	if len(s) > max {
		s = s[:max] + "…"
	}
	return s
}
