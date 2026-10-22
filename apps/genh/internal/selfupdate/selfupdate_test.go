package selfupdate

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"
)

// fakeRelease mô tả bản phát hành fakeGitHub trả về ở /releases/latest.
// PublishedAt zero ⇒ KHÔNG có trường published_at trong JSON (giả lập
// response thiếu thời điểm phát hành).
type fakeRelease struct {
	Tag         string
	PublishedAt time.Time
	Prerelease  bool
	Draft       bool
	// Body là ghi chú Release (nơi job promote ghi PromotedMarker); rỗng ⇒
	// không có trường body trong JSON.
	Body      string
	Assets    map[string][]byte
	Checksums string
}

// fakeGH là httptest.Server giả GitHub kèm bộ đếm số request tải
// (asset lẫn checksums.txt) — để test khẳng định "bị hoãn thì KHÔNG tải gì".
type fakeGH struct {
	*httptest.Server
	downloads atomic.Int64
}

// fakeGitHub dựng một httptest.Server giả cả API metadata lẫn tải asset,
// dùng chung một base URL cho cả Options.APIBase và Options.DownloadBase
// (đường dẫn phân biệt bằng path, không cần 2 server riêng).
func fakeGitHub(t *testing.T, rel fakeRelease) *fakeGH {
	t.Helper()
	gh := &fakeGH{}
	mux := http.NewServeMux()
	mux.HandleFunc("/repos/o/r/releases/latest", func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("User-Agent") == "" {
			t.Errorf("thiếu User-Agent trên request tới GitHub API")
		}
		body := map[string]any{"tag_name": rel.Tag, "prerelease": rel.Prerelease, "draft": rel.Draft}
		if !rel.PublishedAt.IsZero() {
			// Đúng định dạng GitHub trả: RFC 3339, UTC, đơn vị giây.
			body["published_at"] = rel.PublishedAt.UTC().Format(time.RFC3339)
		}
		if rel.Body != "" {
			body["body"] = rel.Body
		}
		_ = json.NewEncoder(w).Encode(body)
	})
	mux.HandleFunc(fmt.Sprintf("/o/r/releases/download/%s/checksums.txt", rel.Tag), func(w http.ResponseWriter, r *http.Request) {
		_, _ = w.Write([]byte(rel.Checksums))
	})
	for name, data := range rel.Assets {
		data := data
		mux.HandleFunc(fmt.Sprintf("/o/r/releases/download/%s/%s", rel.Tag, name), func(w http.ResponseWriter, r *http.Request) {
			_, _ = w.Write(data)
		})
	}
	gh.Server = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if strings.Contains(r.URL.Path, "/releases/download/") {
			gh.downloads.Add(1)
		}
		mux.ServeHTTP(w, r)
	}))
	return gh
}

func sumHex(b []byte) string {
	s := sha256.Sum256(b)
	return hex.EncodeToString(s[:])
}

func TestRun_SameVersion_DoesNothing(t *testing.T) {
	srv := fakeGitHub(t, fakeRelease{Tag: "v0.1.5"})
	defer srv.Close()

	res, err := Run(context.Background(), Options{
		Owner: "o", Repo: "r",
		CurrentVersion: "v0.1.5",
		APIBase:        srv.URL,
		DownloadBase:   srv.URL,
	})
	if err != nil {
		t.Fatalf("Run: %v", err)
	}
	if !res.Skipped || res.Updated {
		t.Fatalf("muốn Skipped=true, Updated=false — được %+v", res)
	}
}

func TestRun_DevVersion_AlwaysSkipped(t *testing.T) {
	// Không cần server thật — Run phải bỏ qua TRƯỚC KHI gọi mạng.
	res, err := Run(context.Background(), Options{
		Owner: "o", Repo: "r",
		CurrentVersion: "dev",
		APIBase:        "http://127.0.0.1:0", // sẽ lỗi kết nối nếu Run lỡ gọi tới
	})
	if err != nil {
		t.Fatalf("Run: %v", err)
	}
	if !res.Skipped || res.Updated {
		t.Fatalf("muốn Skipped=true cho bản dev — được %+v", res)
	}
}

func TestRun_NetworkError_SkipsWithoutFailing(t *testing.T) {
	res, err := Run(context.Background(), Options{
		Owner: "o", Repo: "r",
		CurrentVersion: "v0.1.4",
		APIBase:        "http://127.0.0.1:1", // cổng không ai lắng nghe -> lỗi kết nối
		HTTPTimeout:    500_000_000,          // 0.5s, khỏi chờ lâu
	})
	if err != nil {
		t.Fatalf("lỗi mạng không được làm Run trả err, được: %v", err)
	}
	if !res.Skipped {
		t.Fatalf("muốn Skipped=true khi lỗi mạng, được %+v", res)
	}
}

func TestRun_NewerVersion_DownloadsVerifiesAndReplaces(t *testing.T) {
	newContent := []byte("genh binary noi dung ban moi v0.1.5")
	asset := AssetName("linux", "amd64")
	sums := fmt.Sprintf("%s  %s\n", sumHex(newContent), asset)
	srv := fakeGitHub(t, fakeRelease{Tag: "v0.1.5", Assets: map[string][]byte{asset: newContent}, Checksums: sums})
	defer srv.Close()

	dir := t.TempDir()
	execPath := filepath.Join(dir, "genh")
	if err := os.WriteFile(execPath, []byte("noi dung binary cu"), 0o755); err != nil {
		t.Fatal(err)
	}

	res, err := Run(context.Background(), Options{
		Owner: "o", Repo: "r",
		CurrentVersion: "v0.1.4",
		GOOS:           "linux", GOARCH: "amd64",
		ExecutablePath: execPath,
		APIBase:        srv.URL,
		DownloadBase:   srv.URL,
	})
	if err != nil {
		t.Fatalf("Run: %v", err)
	}
	if !res.Updated || res.To != "v0.1.5" || res.From != "v0.1.4" {
		t.Fatalf("muốn Updated=true To=v0.1.5 From=v0.1.4 — được %+v", res)
	}

	got, err := os.ReadFile(execPath)
	if err != nil {
		t.Fatal(err)
	}
	if string(got) != string(newContent) {
		t.Fatalf("binary trên đĩa chưa được thay đúng nội dung mới")
	}
	// Windows không có bit thực thi (os.Stat luôn trả 0666/0444 cho tệp
	// thường — chạy được hay không do đuôi .exe quyết định), nên chỉ kiểm
	// quyền thực thi trên Linux/macOS.
	if runtime.GOOS != "windows" {
		info, err := os.Stat(execPath)
		if err != nil {
			t.Fatal(err)
		}
		if info.Mode().Perm()&0o111 == 0 {
			t.Fatalf("binary mới phải có quyền thực thi, được %v", info.Mode())
		}
	}
}

func TestRun_ChecksumMismatch_RejectsAndKeepsOldBinary(t *testing.T) {
	newContent := []byte("noi dung ban moi bi hong hoac bi thay doi giua duong")
	asset := AssetName("linux", "amd64")
	// checksums.txt cố tình sai (không khớp sha256 thật của newContent).
	badSum := strings.Repeat("0", 64)
	sums := fmt.Sprintf("%s  %s\n", badSum, asset)
	srv := fakeGitHub(t, fakeRelease{Tag: "v9.9.9", Assets: map[string][]byte{asset: newContent}, Checksums: sums})
	defer srv.Close()

	dir := t.TempDir()
	execPath := filepath.Join(dir, "genh")
	oldContent := []byte("noi dung binary cu, khong duoc doi")
	if err := os.WriteFile(execPath, oldContent, 0o755); err != nil {
		t.Fatal(err)
	}

	_, err := Run(context.Background(), Options{
		Owner: "o", Repo: "r",
		CurrentVersion: "v0.1.4",
		GOOS:           "linux", GOARCH: "amd64",
		ExecutablePath: execPath,
		APIBase:        srv.URL,
		DownloadBase:   srv.URL,
	})
	if err == nil {
		t.Fatalf("muốn Run trả lỗi khi checksum sai, được nil")
	}

	got, rerr := os.ReadFile(execPath)
	if rerr != nil {
		t.Fatal(rerr)
	}
	if string(got) != string(oldContent) {
		t.Fatalf("checksum sai nhưng binary cũ đã bị thay đổi — không được phép")
	}

	// Không được để lại tệp tạm trong thư mục.
	entries, err := os.ReadDir(dir)
	if err != nil {
		t.Fatal(err)
	}
	if len(entries) != 1 {
		t.Fatalf("thư mục phải chỉ còn đúng 1 tệp (binary cũ), được %d", len(entries))
	}
}

func TestIsNewer(t *testing.T) {
	cases := []struct {
		current, latest string
		want            bool
	}{
		{"v0.1.4", "v0.1.5", true},
		{"v0.1.5", "v0.1.4", false},
		{"v0.1.5", "v0.1.5", false},
		{"v0.9.9", "v1.0.0", true},
		{"v1.2.3", "v1.2.10", true},
		{"dev", "v0.1.5", false},
		{"v0.1.5", "not-a-version", false},
	}
	for _, c := range cases {
		if got := IsNewer(c.current, c.latest); got != c.want {
			t.Errorf("IsNewer(%q, %q) = %v, muốn %v", c.current, c.latest, got, c.want)
		}
	}
}

// --- thời gian chín (MinAge) + bản thử (prerelease) ---

// fixedNow là "bây giờ" cố định tiêm vào Options.Now — để tuổi bản phát hành
// trong test không phụ thuộc đồng hồ máy chạy test.
var fixedNow = time.Date(2026, 10, 1, 3, 0, 0, 0, time.UTC)

const (
	oldBinary = "noi dung binary cu v0.1.32"
	newBinary = "noi dung binary moi v0.1.33"
)

// minAgeFixture dựng server giả có bản v0.1.33 (asset + checksums đúng) và
// một binary cũ v0.1.32 trên đĩa; trả server, đường dẫn binary và Options
// nền (caller tự đặt MinAge/Out/Quiet).
func minAgeFixture(t *testing.T, rel fakeRelease) (*fakeGH, string, Options) {
	t.Helper()
	asset := AssetName("linux", "amd64")
	rel.Tag = "v0.1.33"
	rel.Assets = map[string][]byte{asset: []byte(newBinary)}
	rel.Checksums = fmt.Sprintf("%s  %s\n", sumHex([]byte(newBinary)), asset)
	srv := fakeGitHub(t, rel)
	t.Cleanup(srv.Close)

	execPath := filepath.Join(t.TempDir(), "genh")
	if err := os.WriteFile(execPath, []byte(oldBinary), 0o755); err != nil {
		t.Fatal(err)
	}
	return srv, execPath, Options{
		Owner: "o", Repo: "r",
		CurrentVersion: "v0.1.32",
		GOOS:           "linux", GOARCH: "amd64",
		ExecutablePath: execPath,
		APIBase:        srv.URL,
		DownloadBase:   srv.URL,
		Now:            func() time.Time { return fixedNow },
	}
}

func readBinary(t *testing.T, path string) string {
	t.Helper()
	b, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	return string(b)
}

// assertNotInstalled: Run bỏ qua, không request tải nào, binary giữ nguyên.
func assertNotInstalled(t *testing.T, res Result, srv *fakeGH, execPath string) {
	t.Helper()
	if !res.Skipped || res.Updated || res.To != "" {
		t.Fatalf("muốn Skipped=true, Updated=false, To rỗng — được %+v", res)
	}
	if res.Reason == "" {
		t.Fatalf("Skipped thì Reason phải có giá trị")
	}
	if n := srv.downloads.Load(); n != 0 {
		t.Fatalf("bản không được cài thì KHÔNG được tải asset/checksums — có %d request tải", n)
	}
	if got := readBinary(t, execPath); got != oldBinary {
		t.Fatalf("binary trên đĩa đã bị thay (%q) — không được phép", got)
	}
}

func assertInstalled(t *testing.T, res Result, execPath string) {
	t.Helper()
	if !res.Updated || res.Skipped || res.Deferred || res.To != "v0.1.33" || res.From != "v0.1.32" {
		t.Fatalf("muốn Updated=true To=v0.1.33 From=v0.1.32 — được %+v", res)
	}
	if got := readBinary(t, execPath); got != newBinary {
		t.Fatalf("binary trên đĩa chưa được thay bằng bản mới, được %q", got)
	}
}

func TestRun_MinAge_BoQuaBanDuoi24h(t *testing.T) {
	srv, execPath, opts := minAgeFixture(t, fakeRelease{PublishedAt: fixedNow.Add(-2 * time.Hour)})
	var out bytes.Buffer
	opts.MinAge = NightlyMinAge
	opts.Out = &out
	opts.Quiet = true // như lịch đêm: --quiet vẫn phải ghi lý do vào log

	res, err := Run(context.Background(), opts)
	if err != nil {
		t.Fatalf("Run: %v", err)
	}
	assertNotInstalled(t, res, srv, execPath)
	if !res.Deferred {
		t.Fatalf("chưa đủ thời gian chín thì Deferred phải = true — được %+v", res)
	}
	// Lý do phải nói rõ cờ nào gây đợi và cách cài ngay CẢ KHI Console không
	// có nút (máy chủ chưa cài watcher): gõ `genh update` không kèm --yes.
	for _, want := range []string{"v0.1.33", "2 giờ trước", "đợi đủ 24 giờ", "--yes (lịch đêm)", "Cập nhật ngay", "`genh update` (không kèm --yes)"} {
		if !strings.Contains(res.Reason, want) {
			t.Errorf("Reason thiếu %q: %q", want, res.Reason)
		}
	}
	if !strings.Contains(out.String(), res.Reason) {
		t.Errorf("dòng hoãn cài phải in ra cả khi Quiet (vào logs/auto-update.log), Out = %q", out.String())
	}
}

func TestRun_MinAge_CaiBanDu24h(t *testing.T) {
	srv, execPath, opts := minAgeFixture(t, fakeRelease{PublishedAt: fixedNow.Add(-25 * time.Hour)})
	opts.MinAge = NightlyMinAge

	res, err := Run(context.Background(), opts)
	if err != nil {
		t.Fatalf("Run: %v", err)
	}
	assertInstalled(t, res, execPath)
	if n := srv.downloads.Load(); n != 2 {
		t.Errorf("muốn đúng 2 request tải (checksums.txt + asset), được %d", n)
	}
}

func TestRun_MinAge_BienDung24h(t *testing.T) {
	_, execPath, opts := minAgeFixture(t, fakeRelease{PublishedAt: fixedNow.Add(-NightlyMinAge)})
	opts.MinAge = NightlyMinAge

	res, err := Run(context.Background(), opts)
	if err != nil {
		t.Fatalf("Run: %v", err)
	}
	assertInstalled(t, res, execPath)
}

func TestRun_KhongMinAge_CapNhatNgay(t *testing.T) {
	// Nút "Cập nhật ngay" (và `genh update` gõ tay) chạy với MinAge = 0: bản
	// vừa phát hành 1 phút cũng phải cài được ngay.
	_, execPath, opts := minAgeFixture(t, fakeRelease{PublishedAt: fixedNow.Add(-time.Minute)})
	opts.MinAge = 0

	res, err := Run(context.Background(), opts)
	if err != nil {
		t.Fatalf("Run: %v", err)
	}
	assertInstalled(t, res, execPath)
}

func TestRun_MinAge_ThieuPublishedAt(t *testing.T) {
	srv, execPath, opts := minAgeFixture(t, fakeRelease{}) // không có published_at
	opts.MinAge = NightlyMinAge

	res, err := Run(context.Background(), opts)
	if err != nil {
		t.Fatalf("Run: %v", err)
	}
	assertNotInstalled(t, res, srv, execPath)
	if !strings.Contains(res.Reason, "thời điểm phát hành") {
		t.Errorf("Reason phải nói rõ không đọc được thời điểm phát hành: %q", res.Reason)
	}
}

// Bản thử tạo từ lâu (published_at 5 ngày trước) nhưng vừa được promote 2
// giờ trước (vd promote tay skip_e2e sau khi E2E đỏ) — thời gian chín phải
// tính từ lúc promote, KHÔNG được lọt cổng chỉ vì published_at đã cũ.
func TestRun_MinAge_PromoteMuon_TinhTuLucPromote(t *testing.T) {
	body := "## Điểm mới\n- sửa lỗi\n\n" + PromotedMarker(fixedNow.Add(-2*time.Hour)) + "\n"
	srv, execPath, opts := minAgeFixture(t, fakeRelease{PublishedAt: fixedNow.Add(-5 * 24 * time.Hour), Body: body})
	opts.MinAge = NightlyMinAge

	res, err := Run(context.Background(), opts)
	if err != nil {
		t.Fatalf("Run: %v", err)
	}
	assertNotInstalled(t, res, srv, execPath)
	if !res.Deferred {
		t.Fatalf("promote mới 2 giờ thì phải hoãn (Deferred=true) dù published_at đã 5 ngày — được %+v", res)
	}
	if !strings.Contains(res.Reason, "2 giờ trước") {
		t.Errorf("tuổi trong Reason phải tính từ lúc promote (2 giờ), được %q", res.Reason)
	}
}

func TestRun_MinAge_PromoteDu24h_Cai(t *testing.T) {
	body := PromotedMarker(fixedNow.Add(-25 * time.Hour))
	_, execPath, opts := minAgeFixture(t, fakeRelease{PublishedAt: fixedNow.Add(-5 * 24 * time.Hour), Body: body})
	opts.MinAge = NightlyMinAge

	res, err := Run(context.Background(), opts)
	if err != nil {
		t.Fatalf("Run: %v", err)
	}
	assertInstalled(t, res, execPath)
}

// Dấu promote lỡ ghi SỚM hơn published_at (không xảy ra ở luồng chuẩn) không
// được làm cổng ngắn đi: lấy mốc muộn hơn.
func TestRun_MinAge_DauPromoteSomHonPublished_LayMocMuonHon(t *testing.T) {
	body := PromotedMarker(fixedNow.Add(-30 * time.Hour))
	srv, execPath, opts := minAgeFixture(t, fakeRelease{PublishedAt: fixedNow.Add(-2 * time.Hour), Body: body})
	opts.MinAge = NightlyMinAge

	res, err := Run(context.Background(), opts)
	if err != nil {
		t.Fatalf("Run: %v", err)
	}
	assertNotInstalled(t, res, srv, execPath)
	if !res.Deferred {
		t.Fatalf("muốn Deferred=true — được %+v", res)
	}
}

// Thiếu published_at nhưng có dấu promote đủ 24 giờ ⇒ vẫn đọc được mốc, cài.
func TestRun_MinAge_ChiCoDauPromote(t *testing.T) {
	_, execPath, opts := minAgeFixture(t, fakeRelease{Body: PromotedMarker(fixedNow.Add(-48 * time.Hour))})
	opts.MinAge = NightlyMinAge

	res, err := Run(context.Background(), opts)
	if err != nil {
		t.Fatalf("Run: %v", err)
	}
	assertInstalled(t, res, execPath)
}

func TestPromotedMarker_DinhDangVaDocLai(t *testing.T) {
	at := time.Date(2026, 10, 1, 12, 34, 56, 0, time.UTC)
	// Định dạng phải khớp đúng dòng job promote (e2e-install.yml) sinh bằng
	// `date -u +%Y-%m-%dT%H:%M:%SZ` — check_release_gate.py giữ phía workflow.
	if got, want := PromotedMarker(at), "<!-- genh:promoted_at=2026-10-01T12:34:56Z -->"; got != want {
		t.Fatalf("PromotedMarker = %q, muốn %q", got, want)
	}
	// Múi giờ khác vẫn ghi ra UTC.
	if got := PromotedMarker(at.In(time.FixedZone("ICT", 7*3600))); got != PromotedMarker(at) {
		t.Fatalf("PromotedMarker phải luôn ghi UTC, được %q", got)
	}

	cases := []struct {
		name string
		body string
		want time.Time
	}{
		{"không có dấu", "## Điểm mới\n- a", time.Time{}},
		{"một dấu", "x\n" + PromotedMarker(at) + "\n", at},
		{"nhiều dấu lấy muộn nhất", PromotedMarker(at.Add(-time.Hour)) + "\n" + PromotedMarker(at) + "\n" + PromotedMarker(at.Add(-2*time.Hour)), at},
		{"khoảng trắng thừa", "<!--   genh:promoted_at=2026-10-01T12:34:56Z   -->", at},
		{"sai định dạng bị bỏ qua", "<!-- genh:promoted_at=hom-qua -->", time.Time{}},
		{"không phải UTC bị bỏ qua", "<!-- genh:promoted_at=2026-10-01T12:34:56+07:00 -->", time.Time{}},
	}
	for _, c := range cases {
		if got := promotedAt(c.body); !got.Equal(c.want) {
			t.Errorf("%s: promotedAt = %v, muốn %v", c.name, got, c.want)
		}
	}
}

func TestRun_Prerelease_BoQua(t *testing.T) {
	cases := map[string]fakeRelease{
		"prerelease": {PublishedAt: fixedNow.Add(-48 * time.Hour), Prerelease: true},
		"draft":      {PublishedAt: fixedNow.Add(-48 * time.Hour), Draft: true},
	}
	for name, rel := range cases {
		t.Run(name, func(t *testing.T) {
			srv, execPath, opts := minAgeFixture(t, rel)
			opts.MinAge = 0 // kể cả "Cập nhật ngay" cũng không nhận bản thử/bản nháp

			res, err := Run(context.Background(), opts)
			if err != nil {
				t.Fatalf("Run: %v", err)
			}
			assertNotInstalled(t, res, srv, execPath)
			if res.Deferred {
				t.Errorf("bản thử/bản nháp không phải \"hoãn\" — Deferred phải = false, được %+v", res)
			}
			if !strings.Contains(res.Reason, "chưa phải bản chính thức") {
				t.Errorf("Reason chưa nói rõ đây không phải bản chính thức: %q", res.Reason)
			}
		})
	}
}

func TestRun_MinAge_DongHoThatKhiNowNil(t *testing.T) {
	// Now nil ⇒ dùng time.Now: bản phát hành 1 giờ trước (theo đồng hồ thật)
	// vẫn phải bị hoãn.
	srv, execPath, opts := minAgeFixture(t, fakeRelease{PublishedAt: time.Now().Add(-time.Hour)})
	opts.Now = nil
	opts.MinAge = NightlyMinAge

	res, err := Run(context.Background(), opts)
	if err != nil {
		t.Fatalf("Run: %v", err)
	}
	assertNotInstalled(t, res, srv, execPath)
	if !res.Deferred {
		t.Fatalf("muốn Deferred=true — được %+v", res)
	}
}

func TestFormatDurationVi(t *testing.T) {
	cases := []struct {
		d    time.Duration
		want string
	}{
		{24 * time.Hour, "24 giờ"},
		{3*time.Hour + 5*time.Minute, "3 giờ 5 phút"},
		{3*time.Hour + 5*time.Minute + 59*time.Second, "3 giờ 5 phút"},
		{45 * time.Minute, "45 phút"},
		{2 * time.Hour, "2 giờ"},
		{23*time.Hour + 59*time.Minute + 40*time.Second, "23 giờ 59 phút"},
		{30 * time.Second, "chưa đến 1 phút"},
		{-5 * time.Minute, "chưa đến 1 phút"},
	}
	for _, c := range cases {
		if got := formatDurationVi(c.d); got != c.want {
			t.Errorf("formatDurationVi(%v) = %q, muốn %q", c.d, got, c.want)
		}
	}
}

// ─── v0.1.53 (F-96): lịch đêm chọn bản đủ chín trong DANH SÁCH release ───────

// listRel mô tả một release trong /releases?per_page=10 (mới trước).
type listRel struct {
	Tag        string
	PromotedAt time.Time // zero ⇒ không có dấu promote trong ghi chú
	Published  time.Time // published_at (zero ⇒ dùng PromotedAt)
	Prerelease bool
	Draft      bool
}

// listGH là GitHub giả có CẢ /releases?per_page=10 lẫn /releases/latest lẫn tải asset; ghi lại mọi đường dẫn.
type listGH struct {
	*httptest.Server
	mu         sync.Mutex
	paths      []string // path+query của mọi request tới API (không tính tải)
	downloads  []string // path của mọi request tải
	listStatus int
}

func fakeGitHubList(t *testing.T, rels []listRel, latest string) *listGH {
	t.Helper()
	gh := &listGH{listStatus: http.StatusOK}
	asset := AssetName("linux", "amd64")
	mux := http.NewServeMux()
	meta := func(r listRel) map[string]any {
		pub := r.Published
		if pub.IsZero() {
			pub = r.PromotedAt
		}
		body := "ghi chú " + r.Tag
		if !r.PromotedAt.IsZero() {
			body += "\n" + PromotedMarker(r.PromotedAt)
		}
		m := map[string]any{"tag_name": r.Tag, "prerelease": r.Prerelease, "draft": r.Draft, "body": body}
		if !pub.IsZero() {
			m["published_at"] = pub.UTC().Format(time.RFC3339)
		}
		return m
	}
	mux.HandleFunc("/repos/o/r/releases", func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("User-Agent") == "" || r.Header.Get("Accept") != "application/vnd.github+json" {
			t.Errorf("thiếu User-Agent/Accept trên /releases: %v", r.Header)
		}
		gh.mu.Lock()
		status := gh.listStatus
		gh.mu.Unlock()
		if status != http.StatusOK {
			http.Error(w, "boom", status)
			return
		}
		out := []map[string]any{}
		for _, rel := range rels {
			out = append(out, meta(rel))
		}
		_ = json.NewEncoder(w).Encode(out)
	})
	mux.HandleFunc("/repos/o/r/releases/latest", func(w http.ResponseWriter, r *http.Request) {
		for _, rel := range rels {
			if rel.Tag == latest {
				_ = json.NewEncoder(w).Encode(meta(rel))
				return
			}
		}
		http.NotFound(w, r)
	})
	for _, rel := range rels {
		rel := rel
		content := []byte("genh binary " + rel.Tag)
		mux.HandleFunc("/o/r/releases/download/"+rel.Tag+"/checksums.txt", func(w http.ResponseWriter, r *http.Request) {
			_, _ = fmt.Fprintf(w, "%s  %s\n", sumHex(content), asset)
		})
		mux.HandleFunc("/o/r/releases/download/"+rel.Tag+"/"+asset, func(w http.ResponseWriter, r *http.Request) { _, _ = w.Write(content) })
	}
	gh.Server = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gh.mu.Lock()
		if strings.Contains(r.URL.Path, "/releases/download/") {
			gh.downloads = append(gh.downloads, r.URL.Path)
		} else {
			gh.paths = append(gh.paths, r.URL.RequestURI())
		}
		gh.mu.Unlock()
		mux.ServeHTTP(w, r)
	}))
	t.Cleanup(gh.Close)
	return gh
}

func (g *listGH) askedList() bool {
	g.mu.Lock()
	defer g.mu.Unlock()
	for _, p := range g.paths {
		if strings.HasPrefix(p, "/repos/o/r/releases?") {
			return true
		}
	}
	return false
}

func nightlyOpts(t *testing.T, gh *listGH, current string, now time.Time) (Options, string) {
	t.Helper()
	exe := filepath.Join(t.TempDir(), "genh")
	if err := os.WriteFile(exe, []byte("genh cu "+current), 0o755); err != nil {
		t.Fatal(err)
	}
	return Options{
		Owner: "o", Repo: "r", CurrentVersion: current, GOOS: "linux", GOARCH: "amd64",
		APIBase: gh.URL, DownloadBase: gh.URL, ExecutablePath: exe,
		MinAge: NightlyMinAge, Now: func() time.Time { return now }, Out: &strings.Builder{},
	}, exe
}

var nowFixed = time.Date(2026, 10, 10, 3, 0, 0, 0, time.UTC)

// Bản ra dồn dập: v0.1.54 mới 1 giờ, v0.1.53 đã chín 25 giờ ⇒ cài v0.1.53 (không "đói").
func TestRun_LichDem_ChonBanDuChinTrongDanhSach(t *testing.T) {
	gh := fakeGitHubList(t, []listRel{
		{Tag: "v0.1.54", PromotedAt: nowFixed.Add(-1 * time.Hour)},
		{Tag: "v0.1.53", PromotedAt: nowFixed.Add(-25 * time.Hour)},
	}, "v0.1.54")
	opts, exe := nightlyOpts(t, gh, "v0.1.52", nowFixed)
	res, err := Run(context.Background(), opts)
	if err != nil {
		t.Fatalf("Run: %v", err)
	}
	if !res.Updated || res.To != "v0.1.53" || res.From != "v0.1.52" {
		t.Fatalf("muốn cài v0.1.53, được %+v", res)
	}
	if !gh.askedList() || len(gh.downloads) != 2 {
		t.Fatalf("phải hỏi danh sách và tải checksums+asset: paths=%v downloads=%v", gh.paths, gh.downloads)
	}
	for _, d := range gh.downloads {
		if !strings.HasPrefix(d, "/o/r/releases/download/v0.1.53/") {
			t.Errorf("phải tải đúng tag v0.1.53, được %s", d)
		}
	}
	if b, _ := os.ReadFile(exe); string(b) != "genh binary v0.1.53" {
		t.Fatalf("binary = %q", b)
	}
	if out := opts.Out.(*strings.Builder).String(); !strings.Contains(out, "genh: đã tự cập nhật lên v0.1.53 (bản cũ v0.1.52).") {
		t.Errorf("log: %q", out)
	}
}

func TestRun_LichDem_ChonBanSemverCaoNhatTrongCacBanChin(t *testing.T) {
	gh := fakeGitHubList(t, []listRel{
		{Tag: "v0.1.55", PromotedAt: nowFixed.Add(-26 * time.Hour)},
		{Tag: "v0.1.54", PromotedAt: nowFixed.Add(-90 * time.Hour)},
		{Tag: "v0.1.53", PromotedAt: nowFixed.Add(-100 * time.Hour)},
	}, "v0.1.55")
	opts, _ := nightlyOpts(t, gh, "v0.1.52", nowFixed)
	if res, err := Run(context.Background(), opts); err != nil || res.To != "v0.1.55" {
		t.Fatalf("phải chọn bản cao nhất đủ chín: %+v %v", res, err)
	}
	// Đúng biên (tuổi == MinAge) thì cho cài.
	gh = fakeGitHubList(t, []listRel{{Tag: "v0.1.53", PromotedAt: nowFixed.Add(-NightlyMinAge)}}, "v0.1.53")
	opts, _ = nightlyOpts(t, gh, "v0.1.52", nowFixed)
	if res, err := Run(context.Background(), opts); err != nil || !res.Updated {
		t.Fatalf("đúng biên 24 giờ phải cài: %+v %v", res, err)
	}
}

func TestRun_LichDem_BoQuaBanThuNhapVaThieuDau(t *testing.T) {
	gh := fakeGitHubList(t, []listRel{
		{Tag: "v0.1.57", PromotedAt: nowFixed.Add(-40 * time.Hour), Prerelease: true},                         // bản thử
		{Tag: "v0.1.56", PromotedAt: nowFixed.Add(-40 * time.Hour), Draft: true},                              // bản nháp
		{Tag: "v0.1.55", Published: nowFixed.Add(-40 * time.Hour)},                                            // THIẾU dấu promote
		{Tag: "v0.1.53", PromotedAt: nowFixed.Add(-30 * time.Hour), Published: nowFixed.Add(-80 * time.Hour)}, // hợp lệ
	}, "v0.1.55")
	opts, _ := nightlyOpts(t, gh, "v0.1.52", nowFixed)
	res, err := Run(context.Background(), opts)
	if err != nil || !res.Updated || res.To != "v0.1.53" {
		t.Fatalf("bản thử/nháp/thiếu dấu phải bị bỏ, cài v0.1.53: %+v %v", res, err)
	}

	// Chỉ có bản thiếu dấu ⇒ bỏ qua cho an toàn (không hứa "đợi đủ 24 giờ" vì đợi cũng không tự cài).
	gh = fakeGitHubList(t, []listRel{{Tag: "v0.1.55", Published: nowFixed.Add(-40 * time.Hour)}}, "v0.1.55")
	opts, exe := nightlyOpts(t, gh, "v0.1.52", nowFixed)
	res, err = Run(context.Background(), opts)
	if err != nil || res.Updated || !res.Skipped || res.Deferred {
		t.Fatalf("thiếu dấu promote: %+v %v", res, err)
	}
	if !strings.Contains(res.Reason, "chưa có dấu promote") || !strings.Contains(res.Reason, installNowHint) || len(gh.downloads) != 0 {
		t.Errorf("reason=%q downloads=%v", res.Reason, gh.downloads)
	}
	if b, _ := os.ReadFile(exe); string(b) != "genh cu v0.1.52" {
		t.Error("không được thay binary")
	}
}

// Tất cả < 24 giờ ⇒ Deferred (đợi đêm sau), Reason nêu bản mới nhất + tuổi, KHÔNG tải gì.
func TestRun_LichDem_TatCaChuaChin_Deferred(t *testing.T) {
	gh := fakeGitHubList(t, []listRel{
		{Tag: "v0.1.54", PromotedAt: nowFixed.Add(-1 * time.Hour)},
		{Tag: "v0.1.53", PromotedAt: nowFixed.Add(-23*time.Hour - 30*time.Minute)},
	}, "v0.1.54")
	opts, exe := nightlyOpts(t, gh, "v0.1.52", nowFixed)
	res, err := Run(context.Background(), opts)
	if err != nil || res.Updated || !res.Skipped || !res.Deferred {
		t.Fatalf("muốn Skipped+Deferred: %+v %v", res, err)
	}
	for _, want := range []string{"v0.1.54", "mới phát hành 1 giờ trước", "đợi đủ 24 giờ", installNowHint} {
		if !strings.Contains(res.Reason, want) {
			t.Errorf("Reason thiếu %q: %s", want, res.Reason)
		}
	}
	if len(gh.downloads) != 0 {
		t.Errorf("hoãn thì KHÔNG tải gì: %v", gh.downloads)
	}
	if b, _ := os.ReadFile(exe); string(b) != "genh cu v0.1.52" {
		t.Error("không được thay binary")
	}
	if out := opts.Out.(*strings.Builder).String(); !strings.Contains(out, "đợi đủ 24 giờ") {
		t.Errorf("dòng log (cả khi Quiet) phải nói đợi: %q", out)
	}

	// Đã ở bản mới nhất ⇒ không Deferred.
	opts, _ = nightlyOpts(t, gh, "v0.1.54", nowFixed)
	if res, err := Run(context.Background(), opts); err != nil || res.Updated || res.Deferred || res.Reason != "đã ở bản mới nhất" {
		t.Fatalf("đã mới nhất: %+v %v", res, err)
	}
}

// MinAge == 0 (nút "Cập nhật ngay", gõ tay) GIỮ đường /releases/latest — không hỏi danh sách.
func TestRun_MinAge0_ChiGoiReleasesLatest(t *testing.T) {
	gh := fakeGitHubList(t, []listRel{
		{Tag: "v0.1.54", PromotedAt: nowFixed.Add(-1 * time.Hour)},
		{Tag: "v0.1.53", PromotedAt: nowFixed.Add(-25 * time.Hour)},
	}, "v0.1.54")
	opts, _ := nightlyOpts(t, gh, "v0.1.52", nowFixed)
	opts.MinAge = 0
	res, err := Run(context.Background(), opts)
	if err != nil || !res.Updated || res.To != "v0.1.54" {
		t.Fatalf("MinAge=0 phải cài ngay bản mới nhất: %+v %v", res, err)
	}
	if gh.askedList() {
		t.Fatalf("MinAge=0 KHÔNG được gọi /releases?: %v", gh.paths)
	}
	if len(gh.paths) != 1 || gh.paths[0] != "/repos/o/r/releases/latest" {
		t.Errorf("chỉ gọi /releases/latest: %v", gh.paths)
	}
}

// Danh sách lỗi (500) ⇒ rơi về /releases/latest + một dòng log.
func TestRun_LichDem_DanhSachLoi_RoiVeLatest(t *testing.T) {
	gh := fakeGitHubList(t, []listRel{{Tag: "v0.1.53", PromotedAt: nowFixed.Add(-25 * time.Hour)}}, "v0.1.53")
	gh.listStatus = http.StatusInternalServerError
	opts, _ := nightlyOpts(t, gh, "v0.1.52", nowFixed)
	res, err := Run(context.Background(), opts)
	if err != nil || !res.Updated || res.To != "v0.1.53" {
		t.Fatalf("danh sách lỗi ⇒ dùng latest: %+v %v", res, err)
	}
	if !gh.askedList() {
		t.Error("phải thử danh sách trước")
	}
	out := opts.Out.(*strings.Builder).String()
	if n := strings.Count(out, "không hỏi được danh sách bản phát hành"); n != 1 {
		t.Errorf("đúng 1 dòng log về lỗi danh sách, được %d:\n%s", n, out)
	}

	// Rơi về latest mà bản đó chưa chín ⇒ vẫn bị cổng 24 giờ hoãn như cũ.
	gh = fakeGitHubList(t, []listRel{{Tag: "v0.1.54", PromotedAt: nowFixed.Add(-1 * time.Hour)}}, "v0.1.54")
	gh.listStatus = http.StatusBadGateway
	opts, _ = nightlyOpts(t, gh, "v0.1.52", nowFixed)
	if res, err := Run(context.Background(), opts); err != nil || !res.Deferred || res.Updated {
		t.Fatalf("latest chưa chín phải bị hoãn: %+v %v", res, err)
	}
}

func TestPickRelease(t *testing.T) {
	mk := func(tag string, age time.Duration, marker bool) releaseMeta {
		m := releaseMeta{TagName: tag, PublishedAt: nowFixed.Add(-age)}
		if marker {
			m.Body = PromotedMarker(nowFixed.Add(-age))
		}
		return m
	}
	list := []releaseMeta{mk("v0.1.55", time.Hour, true), mk("v0.1.54", 30*time.Hour, true), mk("v0.1.53", 50*time.Hour, true), mk("v0.1.52", 90*time.Hour, true)}
	got, ok := pickRelease(list, "v0.1.52", 24*time.Hour, nowFixed)
	if !ok || got.TagName != "v0.1.54" {
		t.Fatalf("pick = %q %v", got.TagName, ok)
	}
	if _, ok := pickRelease(list, "v0.1.54", 24*time.Hour, nowFixed); ok {
		t.Error("v0.1.55 mới 1 giờ, chưa chín ⇒ không chọn")
	}
	if _, ok := pickRelease(list, "v0.1.55", 24*time.Hour, nowFixed); ok {
		t.Error("đã ở bản cao nhất")
	}
	if _, ok := pickRelease([]releaseMeta{mk("v0.1.60", 99*time.Hour, false)}, "v0.1.52", 24*time.Hour, nowFixed); ok {
		t.Error("thiếu dấu promote ⇒ không chọn")
	}
	// Dấu promote MUỘN hơn published_at thì tuổi tính từ dấu.
	late := releaseMeta{TagName: "v0.1.60", PublishedAt: nowFixed.Add(-99 * time.Hour), Body: PromotedMarker(nowFixed.Add(-2 * time.Hour))}
	if _, ok := pickRelease([]releaseMeta{late}, "v0.1.52", 24*time.Hour, nowFixed); ok {
		t.Error("promote 2 giờ trước ⇒ chưa chín dù published_at đã cũ")
	}
	if n, any := newestNewer(list, "v0.1.52"); !any || n.TagName != "v0.1.55" {
		t.Errorf("newestNewer = %q %v", n.TagName, any)
	}
}
