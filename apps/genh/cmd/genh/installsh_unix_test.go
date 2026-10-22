//go:build !windows

// Kiểm install.sh (bootstrap một lệnh ở gốc repo) bằng cách chạy thật với
// `sh`, thay curl bằng một script giả (đọc tệp từ thư mục fixture, không gọi
// mạng) và genh bằng một script giả ghi lại đối số nó được gọi — để biết
// install.sh giao lại cho `genh install`, `genh update` hay
// `genh update --no-self-update`. Chỉ chạy trên Linux/macOS (install.sh
// không dành cho Windows — Windows dùng install.ps1).

package main

import (
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
)

type installShEnv struct {
	home     string // GEN_HARNESS_HOME = HOME
	fixtures string // tệp curl giả trả về, theo tên cuối URL
	argsFile string // đối số genh giả nhận được
	urlsFile string // các URL curl giả đã được gọi
	pathDir  string // thư mục chứa curl giả, đặt đầu PATH
	argsLog  string // toàn bộ đối số curl giả nhận, mỗi lần gọi một dòng
	extraEnv []string
}

func installShAsset(t *testing.T) string {
	t.Helper()
	arch := runtime.GOARCH
	if arch != "amd64" && arch != "arm64" {
		t.Skipf("install.sh chỉ hỗ trợ amd64/arm64, máy test là %s", arch)
	}
	return fmt.Sprintf("genh-%s-%s", runtime.GOOS, arch)
}

// newInstallShEnv dựng fixture: asset genh giả (script ghi đối số) +
// checksums.txt đúng SHA-256 của nó, và curl giả. withAsset=false ⇒ curl giả
// trả 404 cho mọi tệp (tag không tồn tại).
func newInstallShEnv(t *testing.T, withAsset bool) installShEnv {
	t.Helper()
	root := t.TempDir()
	e := installShEnv{
		home:     filepath.Join(root, "home"),
		fixtures: filepath.Join(root, "fixtures"),
		argsFile: filepath.Join(root, "genh-args.txt"),
		urlsFile: filepath.Join(root, "curl-urls.txt"),
		pathDir:  filepath.Join(root, "bin"),
		argsLog:  filepath.Join(root, "curl-args.txt"),
	}
	for _, d := range []string{e.home, e.fixtures, e.pathDir} {
		if err := os.MkdirAll(d, 0o755); err != nil {
			t.Fatal(err)
		}
	}
	if withAsset {
		asset := installShAsset(t)
		genh := []byte("#!/bin/sh\nprintf '%s' \"$*\" > \"$GENH_ARGS_FILE\"\n")
		if err := os.WriteFile(filepath.Join(e.fixtures, asset), genh, 0o644); err != nil {
			t.Fatal(err)
		}
		sum := sha256.Sum256(genh)
		sums := fmt.Sprintf("%s  %s\n", hex.EncodeToString(sum[:]), asset)
		if err := os.WriteFile(filepath.Join(e.fixtures, "checksums.txt"), []byte(sums), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	// install.sh gọi: curl <cờ…> -o <đích> <url> (url là đối số cuối).
	// CURL_FAIL28_NAME/CURL_FAIL28_TIMES: trả mã 28 (quá chậm/rảnh) cho
	// tệp tên đó trong CURL_FAIL28_TIMES lần gọi đầu — giả mạng chập chờn.
	// CURL_FAIL503_NAME/CURL_FAIL503_TIMES: như trên nhưng HTTP 503 (curl -f
	// mã 22, -w in "503") — máy chủ quá tải tạm thời. Lỗi HTTP in mã qua -w
	// (như curl thật) khi được gọi kèm -w.
	curl := `#!/bin/sh
printf '%s\n' "$*" >> "$CURL_ARGS_LOG"
dest=""
url=""
wfmt=""
while [ $# -gt 0 ]; do
	case "$1" in
	-o) dest="$2"; shift 2 ;;
	-w) wfmt="$2"; shift 2 ;;
	*) url="$1"; shift ;;
	esac
done
code() { [ -n "$wfmt" ] && printf '%s' "$1"; return 0; }
printf '%s\n' "$url" >> "$CURL_URLS_FILE"
name="${url##*/}"
if [ -n "${CURL_FAIL28_NAME:-}" ] && [ "$name" = "$CURL_FAIL28_NAME" ]; then
	n=$(cat "$CURL_FAIL28_COUNT" 2>/dev/null || echo 0)
	if [ "$n" -lt "$CURL_FAIL28_TIMES" ]; then
		echo $((n + 1)) > "$CURL_FAIL28_COUNT"
		echo "curl: (28) Operation too slow. Less than 1024 bytes/sec transferred the last 60 seconds" >&2
		exit 28
	fi
fi
if [ -n "${CURL_FAIL503_NAME:-}" ] && [ "$name" = "$CURL_FAIL503_NAME" ]; then
	n=$(cat "$CURL_FAIL503_COUNT" 2>/dev/null || echo 0)
	if [ "$n" -lt "$CURL_FAIL503_TIMES" ]; then
		echo $((n + 1)) > "$CURL_FAIL503_COUNT"
		code 503
		echo "curl: (22) The requested URL returned error: 503" >&2
		exit 22
	fi
fi
if [ -f "$FIXTURES_DIR/$name" ]; then
	cp "$FIXTURES_DIR/$name" "$dest"
	code 200
	exit 0
fi
code 404
echo "curl: (22) The requested URL returned error: 404" >&2
exit 22
`
	if err := os.WriteFile(filepath.Join(e.pathDir, "curl"), []byte(curl), 0o755); err != nil {
		t.Fatal(err)
	}
	return e
}

func (e installShEnv) markInstalled(t *testing.T) {
	t.Helper()
	cfg := filepath.Join(e.home, "config")
	if err := os.MkdirAll(cfg, 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(cfg, "secrets.json"), []byte("{}"), 0o600); err != nil {
		t.Fatal(err)
	}
}

// run chạy install.sh của repo với tag ghim (rỗng = không đặt biến), trả
// (stderr+stdout, lỗi thoát).
func (e installShEnv) run(t *testing.T, tag string) (string, error) {
	t.Helper()
	script, err := filepath.Abs(filepath.Join("..", "..", "..", "..", "install.sh"))
	if err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(script); err != nil {
		t.Fatalf("không thấy install.sh ở gốc repo (%s): %v", script, err)
	}
	cmd := exec.Command("sh", script)
	cmd.Env = []string{
		"PATH=" + e.pathDir + string(os.PathListSeparator) + os.Getenv("PATH"),
		"HOME=" + e.home,
		"GEN_HARNESS_HOME=" + e.home,
		"SHELL=/bin/sh",
		"TMPDIR=" + t.TempDir(),
		"FIXTURES_DIR=" + e.fixtures,
		"CURL_URLS_FILE=" + e.urlsFile,
		"GENH_ARGS_FILE=" + e.argsFile,
		"CURL_ARGS_LOG=" + e.argsLog,
	}
	cmd.Env = append(cmd.Env, e.extraEnv...)
	if tag != "" {
		cmd.Env = append(cmd.Env, "GEN_HARNESS_RELEASE_TAG="+tag)
	}
	out, err := cmd.CombinedOutput()
	return string(out), err
}

func (e installShEnv) genhArgs(t *testing.T) string {
	t.Helper()
	b, err := os.ReadFile(e.argsFile)
	if err != nil {
		t.Fatalf("genh giả chưa được gọi (install.sh không exec genh): %v", err)
	}
	return string(b)
}

func (e installShEnv) urls(t *testing.T) string {
	t.Helper()
	b, _ := os.ReadFile(e.urlsFile)
	return string(b)
}

func TestInstallSh_TagSaiDang_TuChoi(t *testing.T) {
	if _, err := exec.LookPath("sh"); err != nil {
		t.Skip("máy test không có sh")
	}
	for _, tag := range []string{"v1a.2b.3c", "0.1.33", "v0.1", "v0.1.33-", "v0.1.33\nv0.1.34", "v0.1.33;rm"} {
		e := newInstallShEnv(t, true)
		out, err := e.run(t, tag)
		if err == nil {
			t.Fatalf("tag %q sai dạng mà install.sh vẫn chạy tiếp:\n%s", tag, out)
		}
		if !strings.Contains(out, "GEN_HARNESS_RELEASE_TAG không hợp lệ") {
			t.Errorf("tag %q: thiếu lời báo dễ hiểu, được:\n%s", tag, out)
		}
		if e.urls(t) != "" {
			t.Errorf("tag %q sai dạng mà vẫn gọi curl: %q", tag, e.urls(t))
		}
	}
}

func TestInstallSh_TagKhongTonTai_BaoLoiDeHieu(t *testing.T) {
	if _, err := exec.LookPath("sh"); err != nil {
		t.Skip("máy test không có sh")
	}
	e := newInstallShEnv(t, false) // curl giả trả 404 cho mọi tệp
	out, err := e.run(t, "v9.9.9")
	if err == nil {
		t.Fatalf("tag không tồn tại mà install.sh thoát 0:\n%s", out)
	}
	want := "của bản v9.9.9 (tag không tồn tại hoặc Release thiếu asset)"
	if !strings.Contains(out, want) {
		t.Fatalf("thiếu lời báo %q, được:\n%s", want, out)
	}
}

func TestInstallSh_GhimTag_MayDaCai_KhongTuCapNhatBinary(t *testing.T) {
	if _, err := exec.LookPath("sh"); err != nil {
		t.Skip("máy test không có sh")
	}
	e := newInstallShEnv(t, true)
	e.markInstalled(t)
	out, err := e.run(t, "v0.1.33-rc.1")
	if err != nil {
		t.Fatalf("install.sh lỗi: %v\n%s", err, out)
	}
	if got := e.genhArgs(t); got != "update --no-self-update" {
		t.Fatalf("máy đã cài + ghim tag: muốn `genh update --no-self-update` (không để selfupdate thay bản ghim bằng latest), được %q", got)
	}
	if !strings.Contains(e.urls(t), "/releases/download/v0.1.33-rc.1/") {
		t.Fatalf("phải tải đúng tag ghim, các URL đã gọi:\n%s", e.urls(t))
	}
}

func TestInstallSh_KhongGhim_MayDaCai_GenhUpdate(t *testing.T) {
	if _, err := exec.LookPath("sh"); err != nil {
		t.Skip("máy test không có sh")
	}
	e := newInstallShEnv(t, true)
	e.markInstalled(t)
	out, err := e.run(t, "")
	if err != nil {
		t.Fatalf("install.sh lỗi: %v\n%s", err, out)
	}
	if got := e.genhArgs(t); got != "update" {
		t.Fatalf("máy đã cài, không ghim: muốn `genh update`, được %q", got)
	}
	if !strings.Contains(e.urls(t), "/releases/latest/download/") {
		t.Fatalf("không ghim thì phải tải từ releases/latest, các URL đã gọi:\n%s", e.urls(t))
	}
}

func TestInstallSh_GhimTag_MaySach_GenhInstall(t *testing.T) {
	if _, err := exec.LookPath("sh"); err != nil {
		t.Skip("máy test không có sh")
	}
	e := newInstallShEnv(t, true)
	out, err := e.run(t, "v0.1.33")
	if err != nil {
		t.Fatalf("install.sh lỗi: %v\n%s", err, out)
	}
	if got := e.genhArgs(t); got != "install" {
		t.Fatalf("máy sạch: muốn `genh install`, được %q", got)
	}
}

// countSuffix đếm số dòng trong urls kết thúc bằng suffix (URL tải asset).
func countSuffix(urls, suffix string) int {
	n := 0
	for _, line := range strings.Split(urls, "\n") {
		if strings.HasSuffix(line, suffix) {
			n++
		}
	}
	return n
}

func TestInstallSh_Curl28HaiLan_ThuLaiRoiCaiTiep(t *testing.T) {
	if _, err := exec.LookPath("sh"); err != nil {
		t.Skip("máy test không có sh")
	}
	e := newInstallShEnv(t, true)
	asset := installShAsset(t)
	e.extraEnv = []string{
		"CURL_FAIL28_NAME=" + asset,
		"CURL_FAIL28_TIMES=2",
		"CURL_FAIL28_COUNT=" + filepath.Join(t.TempDir(), "fail28-count"),
	}
	out, err := e.run(t, "")
	if err != nil {
		t.Fatalf("install.sh lỗi dù lần 3 tải được: %v\n%s", err, out)
	}
	if got := e.genhArgs(t); got != "install" {
		t.Fatalf("máy sạch: muốn `genh install`, được %q", got)
	}
	if n := countSuffix(e.urls(t), "/"+asset); n != 3 {
		t.Fatalf("muốn đúng 3 lần gọi URL asset, được %d:\n%s", n, e.urls(t))
	}
	for _, want := range []string{"thử lại lần 2/3", "thử lại lần 3/3"} {
		if !strings.Contains(out, want) {
			t.Errorf("thiếu dòng %q, được:\n%s", want, out)
		}
	}
	args, _ := os.ReadFile(e.argsLog)
	for _, flag := range []string{"--connect-timeout 30", "--speed-limit 1024", "--speed-time 60"} {
		if !strings.Contains(string(args), flag) {
			t.Errorf("curl phải được gọi với %q (thời gian rảnh), đối số:\n%s", flag, args)
		}
	}
}

// HTTP 503 (curl -f mã 22) là lỗi tạm thời của GitHub/CDN — thử lại như
// downloadWithRetry của genh, không coi như 404.
func TestInstallSh_Curl503_ThuLaiRoiCaiTiep(t *testing.T) {
	if _, err := exec.LookPath("sh"); err != nil {
		t.Skip("máy test không có sh")
	}
	e := newInstallShEnv(t, true)
	asset := installShAsset(t)
	e.extraEnv = []string{
		"CURL_FAIL503_NAME=" + asset,
		"CURL_FAIL503_TIMES=1",
		"CURL_FAIL503_COUNT=" + filepath.Join(t.TempDir(), "fail503-count"),
	}
	out, err := e.run(t, "")
	if err != nil {
		t.Fatalf("install.sh lỗi dù lần 2 tải được: %v\n%s", err, out)
	}
	if n := countSuffix(e.urls(t), "/"+asset); n != 2 {
		t.Fatalf("503 phải thử lại: muốn 2 lần gọi URL asset, được %d:\n%s", n, e.urls(t))
	}
	if !strings.Contains(out, "HTTP 503") || !strings.Contains(out, "thử lại lần 2/3") {
		t.Errorf("thiếu dòng thử lại kèm HTTP 503:\n%s", out)
	}
}

func TestInstallSh_Curl404_KhongThuLai(t *testing.T) {
	if _, err := exec.LookPath("sh"); err != nil {
		t.Skip("máy test không có sh")
	}
	e := newInstallShEnv(t, false) // curl giả trả 404 (mã 22) cho mọi tệp
	asset := installShAsset(t)
	out, err := e.run(t, "")
	if err == nil {
		t.Fatalf("404 mà install.sh thoát 0:\n%s", out)
	}
	if n := countSuffix(e.urls(t), "/"+asset); n != 1 {
		t.Fatalf("404 không được thử lại: %d lần gọi URL asset:\n%s", n, e.urls(t))
	}
	if strings.Contains(out, "thử lại") {
		t.Errorf("404 không được in dòng thử lại:\n%s", out)
	}
	want := "không tải được " + asset + " từ bản phát hành mới nhất"
	if !strings.Contains(out, want) {
		t.Fatalf("thiếu thông điệp fetch_failed %q, được:\n%s", want, out)
	}
}

func TestInstallSh_Shellcheck(t *testing.T) {
	sc, err := exec.LookPath("shellcheck")
	if err != nil {
		t.Skip("máy test không có shellcheck")
	}
	script := filepath.Join("..", "..", "..", "..", "install.sh")
	if out, err := exec.Command(sc, script).CombinedOutput(); err != nil {
		t.Fatalf("shellcheck install.sh chưa sạch: %v\n%s", err, out)
	}
}
