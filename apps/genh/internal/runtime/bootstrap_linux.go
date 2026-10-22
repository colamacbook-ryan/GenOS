//go:build linux

package runtime

import (
	"context"
	"fmt"
	"os"
	"path/filepath"
	"runtime"

	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/dockercli"
)

// dockerStaticVersion là bản Docker Engine tĩnh genh tự tải khi máy Linux
// chưa có Docker hợp lệ. Ghim cứng (không tự trôi theo "latest") để mọi lần
// cài ra cùng một kết quả kiểm chứng được — nâng cấp version này là một
// thay đổi có chủ đích, không phải tự động.
const dockerStaticVersion = "29.8.1"

// dockerStaticBaseURL là gốc thư mục bản tĩnh chính thức của Docker.
const dockerStaticBaseURL = "https://download.docker.com"

// dockerStaticChecksums ghim sẵn SHA-256 (hex) của gói Docker Engine tĩnh
// và docker-rootless-extras cho từng kiến trúc, đúng dockerStaticVersion.
//
// LÝ DO ghim thay vì tải SHA256SUMS: đã xác minh trực tiếp — Docker KHÔNG
// phát hành tệp SHA256SUMS trong https://download.docker.com/linux/static/stable/<arch>/
// (trả 404), thư mục đó chỉ có các *.tgz. Bản trước của tệp này tải
// ChecksumsURL() (SHA256SUMS) nên trên máy Linux thật luôn lỗi
// "lấy checksum: máy chủ trả 404 Not Found" — không cài được Docker
// (GH-E021).
//
// CẬP NHẬT khi nâng dockerStaticVersion: với MỖI kiến trúc bên dưới, tải
// https://download.docker.com/linux/static/stable/<arch>/docker-<version>.tgz
// và .../docker-rootless-extras-<version>.tgz thật rồi `sha256sum` từng
// tệp, dán hex vào đúng ô. Thiếu ô nào, PinnedChecksum trả lỗi rõ ràng thay
// vì âm thầm bỏ qua kiểm chứng. CI (xem release.yml, bước "Kiểm checksum
// Docker tĩnh đã ghim") tải lại đúng các tệp này và so sha256 mỗi lần
// release để phát hiện sớm nếu Docker thay nội dung tệp mà không đổi tên.
var dockerStaticChecksums = map[string]map[string]string{
	"x86_64": {
		"docker-29.8.1.tgz":                 "d8db66739d2e28d4933786d73e918d9be643a67fbd835db1bf740d650a259e70",
		"docker-rootless-extras-29.8.1.tgz": "8f1ed16fc6913241e599af6234a7f30502bde6e73bebbbc7176b17802d967d49",
	},
	"aarch64": {
		"docker-29.8.1.tgz":                 "667395fbffab52901b80181dfbb39ea76da2fbd7642c4fbddd24e42146b07b48",
		"docker-rootless-extras-29.8.1.tgz": "3890bed82dc432e9fed7e52efcc18bb82647cd7beae45b03e9e2a2f6c3282c66",
	},
}

// PinnedChecksum trả về SHA-256 (hex) đã ghim trong dockerStaticChecksums
// cho filename (chỉ so basename) trên kiến trúc goarch (tên Go, sẽ tự đổi
// qua dockerArchName) — thay cho việc tải SHA256SUMS (không tồn tại, xem
// comment ở dockerStaticChecksums).
func PinnedChecksum(goarch, filename string) (string, error) {
	arch := dockerArchName(goarch)
	table, ok := dockerStaticChecksums[arch]
	if !ok {
		return "", fmt.Errorf("không có checksum ghim cho kiến trúc %q — cập nhật dockerStaticChecksums trong bootstrap_linux.go", arch)
	}
	base := filepath.Base(filename)
	sum, ok := table[base]
	if !ok {
		return "", fmt.Errorf("không có checksum ghim cho %q (kiến trúc %s, version %s) — cập nhật dockerStaticChecksums trong bootstrap_linux.go khi đổi dockerStaticVersion", base, arch, dockerStaticVersion)
	}
	return sum, nil
}

// DockerVersion trả về dockerStaticVersion ghim cứng — CI (release.yml, job
// verify-docker-pins) dùng để biết chính xác đang cần kiểm checksum của bản
// nào, tránh chép tay version vào workflow rồi lệch với mã nguồn.
func DockerVersion() string { return dockerStaticVersion }

// PinnedChecksums trả về bản sao của dockerStaticChecksums (không trả biến
// gốc để nơi gọi không sửa nhầm) — CI dùng để tải lại từng tệp thật từ
// download.docker.com và so sha256, phát hiện sớm nếu Docker thay nội dung
// tệp mà không đổi tên.
func PinnedChecksums() map[string]map[string]string {
	out := make(map[string]map[string]string, len(dockerStaticChecksums))
	for arch, table := range dockerStaticChecksums {
		inner := make(map[string]string, len(table))
		for name, sum := range table {
			inner[name] = sum
		}
		out[arch] = inner
	}
	return out
}

// dockerArchName ánh xạ GOARCH của Go sang tên kiến trúc dùng trong đường
// dẫn tải của download.docker.com.
func dockerArchName(goarch string) string {
	switch goarch {
	case "amd64":
		return "x86_64"
	case "arm64":
		return "aarch64"
	default:
		return goarch
	}
}

// StaticDownloadURL trả về URL tải gói Docker Engine tĩnh (đầy đủ dockerd,
// containerd, runc, docker CLI, buildx, compose plugin) cho goarch/version.
func StaticDownloadURL(baseURL, goarch, version string) string {
	return fmt.Sprintf("%s/linux/static/stable/%s/docker-%s.tgz", baseURL, dockerArchName(goarch), version)
}

// RootlessExtrasURL trả về URL tải gói docker-rootless-extras (chứa
// rootlesskit, dockerd-rootless.sh, dockerd-rootless-setuptool.sh) cùng
// version với gói chính.
func RootlessExtrasURL(baseURL, goarch, version string) string {
	return fmt.Sprintf("%s/linux/static/stable/%s/docker-rootless-extras-%s.tgz", baseURL, dockerArchName(goarch), version)
}

// BootstrapOptions gom mọi thứ Bootstrap cần — tách khỏi biến toàn cục để
// test tiêm được máy chủ HTTP giả + Runner giả, không đụng mạng/Docker thật.
type BootstrapOptions struct {
	RuntimeDir string // thường là config.Paths.RuntimeDir(), ví dụ ~/.gen-harness/runtime
	GOARCH     string // runtime.GOARCH thật khi chạy, tiêm giá trị khác khi test
	BaseURL    string // dockerStaticBaseURL khi chạy thật, httptest.Server.URL khi test
	Version    string // dockerStaticVersion khi chạy thật

	HTTP   HTTPDoer
	Runner dockercli.Runner

	// AutoApprove: Owner đã đồng ý trước (`genh install --yes`) cho phép
	// chạy dockerd-rootless-setuptool.sh — bước này có thể cần sudo cho
	// newuidmap/newgidmap (tài liệu: "Chỉ dùng sudo cho bước bắt buộc,
	// hỏi trước, giải thích lý do"). false => Bootstrap tải+giải nén xong
	// rồi DỪNG lại, báo rõ lệnh cần Owner tự chạy hoặc chạy lại với --yes.
	AutoApprove bool

	// OnProgress báo tiến độ tải theo byte thật (đọc Content-Length nếu
	// máy chủ trả về) — Step 2 dùng để cập nhật Reporter.
	OnProgress func(stage string, current, total int64)
}

// Outcome là kết quả Bootstrap — Step 2 dùng để quyết định OK/WARN và dựng
// Detail hiển thị.
type Outcome struct {
	Installed       bool   // đã tải+giải nén xong Docker Engine tĩnh
	ServiceStarted  bool   // đã chạy dockerd-rootless-setuptool.sh thành công
	BinDir          string // thư mục chứa binary docker/dockerd đã giải nén
	ManualNextSteps string // hướng dẫn Owner tự hoàn tất nếu ServiceStarted=false
}

// Bootstrap tải Docker Engine rootless tĩnh + gói rootless-extras, kiểm
// SHA-256 theo dockerStaticChecksums đã ghim (xem comment ở đó — Docker
// không phát hành SHA256SUMS), giải nén vào opts.RuntimeDir, rồi — chỉ khi
// AutoApprove — chạy dockerd-rootless-setuptool.sh để tạo và khởi động
// systemd user service.
//
// GIỚI HẠN: bước "chạy dockerd-rootless-setuptool.sh install" chỉ được gọi
// thật (không giả lập được ý nghĩa của nó), và cần systemd user session +
// newuidmap/newgidmap — sandbox CI không có, nên phần này KHÔNG chạy được
// thật trong test; test chỉ kiểm tra Bootstrap dừng đúng chỗ khi
// AutoApprove=false và gọi đúng lệnh khi AutoApprove=true (qua Runner giả).
func Bootstrap(ctx context.Context, opts BootstrapOptions) (*Outcome, error) {
	if opts.GOARCH == "" {
		opts.GOARCH = runtime.GOARCH
	}
	if opts.BaseURL == "" {
		opts.BaseURL = dockerStaticBaseURL
	}
	if opts.Version == "" {
		opts.Version = dockerStaticVersion
	}
	if opts.Runner == nil {
		opts.Runner = dockercli.ExecRunner{}
	}
	report := opts.OnProgress
	if report == nil {
		report = func(string, int64, int64) {}
	}

	binDir := filepath.Join(opts.RuntimeDir, "docker", "bin")
	marker := filepath.Join(opts.RuntimeDir, "docker", ".installed-"+opts.Version)

	if _, err := os.Stat(marker); err == nil {
		// Idempotent: đã tải+giải nén đúng phiên bản này từ lần chạy trước.
		return finishOutcome(ctx, opts, binDir)
	}

	dlDir := filepath.Join(opts.RuntimeDir, "downloads")

	mainTar := filepath.Join(dlDir, fmt.Sprintf("docker-%s.tgz", opts.Version))
	if err := downloadVerified(ctx, opts, StaticDownloadURL(opts.BaseURL, opts.GOARCH, opts.Version), mainTar, "docker Engine tĩnh", report); err != nil {
		return nil, err
	}

	extrasTar := filepath.Join(dlDir, fmt.Sprintf("docker-rootless-extras-%s.tgz", opts.Version))
	if err := downloadVerified(ctx, opts, RootlessExtrasURL(opts.BaseURL, opts.GOARCH, opts.Version), extrasTar, "docker-rootless-extras", report); err != nil {
		return nil, err
	}

	extractDir := filepath.Join(opts.RuntimeDir, "docker")
	if err := ExtractTarGz(mainTar, extractDir); err != nil {
		return nil, fmt.Errorf("giải nén %s: %w", mainTar, err)
	}
	if err := ExtractTarGz(extrasTar, extractDir); err != nil {
		return nil, fmt.Errorf("giải nén %s: %w", extrasTar, err)
	}

	if err := os.MkdirAll(filepath.Dir(marker), 0o755); err != nil {
		return nil, err
	}
	if err := os.WriteFile(marker, []byte(opts.Version+"\n"), 0o644); err != nil {
		return nil, err
	}

	return finishOutcome(ctx, opts, binDir)
}

func downloadVerified(ctx context.Context, opts BootstrapOptions, fileURL, destPath, label string, report func(string, int64, int64)) error {
	if _, err := os.Stat(destPath); err == nil {
		return nil // đã tải ở lần chạy trước (idempotent) — vẫn sẽ bị vứt nếu checksum sai lúc giải nén marker chưa ghi.
	}

	want, err := PinnedChecksum(opts.GOARCH, fileURL)
	if err != nil {
		return fmt.Errorf("lấy checksum ghim cho %s: %w", label, err)
	}

	report(label, 0, 0)
	got, err := DownloadFile(ctx, opts.HTTP, fileURL, destPath)
	if err != nil {
		return fmt.Errorf("tải %s: %w", label, err)
	}
	if err := VerifyChecksum(got, want); err != nil {
		os.Remove(destPath)
		return fmt.Errorf("%s: %w", label, err)
	}
	return nil
}

// finishOutcome quyết định có chạy dockerd-rootless-setuptool.sh hay không
// tuỳ AutoApprove, và mô tả bước tiếp theo nếu chưa chạy.
func finishOutcome(ctx context.Context, opts BootstrapOptions, binDir string) (*Outcome, error) {
	setupTool := filepath.Join(binDir, "dockerd-rootless-setuptool.sh")
	// Gói tĩnh Docker giải nén các binary trực tiếp vào <extractDir>/docker/
	// (không có thư mục con bin/) theo đúng cấu trúc tarball thật — binDir
	// ở trên là quy ước genh đặt ra để PATH gọn, nên trỏ lại đúng chỗ giải
	// nén thật khi tìm script.
	flatDir := filepath.Join(opts.RuntimeDir, "docker")
	if _, err := os.Stat(setupTool); err != nil {
		setupTool = filepath.Join(flatDir, "dockerd-rootless-setuptool.sh")
	}

	out := &Outcome{Installed: true, BinDir: flatDir}

	if !opts.AutoApprove {
		out.ManualNextSteps = fmt.Sprintf(
			"chạy: PATH=%s:$PATH %s install    (có thể cần sudo cho newuidmap/newgidmap — hoặc chạy lại `genh install --yes`)",
			flatDir, setupTool)
		return out, nil
	}

	if err := runSetupTool(ctx, opts.Runner, setupTool, flatDir); err != nil {
		out.ManualNextSteps = fmt.Sprintf(
			"tự chạy: PATH=%s:$PATH %s install", flatDir, setupTool)
		return out, fmt.Errorf("dockerd-rootless-setuptool.sh install: %w", err)
	}
	out.ServiceStarted = true
	return out, nil
}

func runSetupTool(ctx context.Context, r dockercli.Runner, scriptPath, flatDir string) error {
	_, err := r.Output(ctx, dockercli.Cmd{
		Name: scriptPath,
		Args: []string{"install"},
		Env:  []string{"PATH=" + flatDir + ":" + os.Getenv("PATH")},
	})
	return err
}
