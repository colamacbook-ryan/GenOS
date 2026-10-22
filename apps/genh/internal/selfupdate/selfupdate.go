// Package selfupdate cho `genh update` khả năng tự thay BINARY genh đang
// chạy bằng bản mới nhất trên GitHub Releases, TRƯỚC KHI chạy phần nâng cấp
// dịch vụ (internal/ops.RunUpdate) — xem docs/handoff/05-installer.md mục
// "Phát hành" và docs/reports/HANDOFF-v0.1.1.md mục v0.1.5.
//
// Owner không rành code: mục tiêu là sau khi cài một lần, KHÔNG BAO GIỜ phải
// tự tải lại install.sh/install.ps1 để lấy genh mới — `genh update` (tự chạy
// mỗi đêm qua internal/autoupdate) tự lo hết.
//
// Thời gian chín 24 giờ (từ v0.1.33): lịch tự cập nhật đêm (`genh update
// --yes --quiet`, internal/autoupdate) đặt Options.MinAge = NightlyMinAge —
// bản mới nhất chưa LÀ BẢN CHÍNH THỨC đủ 24 giờ thì BỎ QUA (Result.Deferred),
// đợi đêm sau. Mốc tính tuổi là lúc promote (dấu PromotedMarker mà job
// `promote` của e2e-install.yml ghi vào ghi chú Release), không có dấu thì
// published_at — xem officialSince: published_at là lúc tạo BẢN THỬ, promote
// không đổi nó, nên một bản thử promote muộn (vd promote tay skip_e2e sau
// vài ngày) sẽ lọt cổng ngay nếu chỉ dựa vào published_at. Một bản lỗi vừa
// lên bản chính thức vì thế không tự lan sang mọi máy trong đêm đầu tiên; ai
// cần ngay thì bấm "Cập nhật ngay" trong Console (đi qua `--if-requested`,
// MinAge = 0, không bị chặn) hoặc gõ `genh update` không kèm --yes. Bản thử
// (prerelease) và bản nháp (draft) luôn bị bỏ qua, kể cả khi MinAge = 0.
//
// Gói này CHỈ lo phần "tải + kiểm checksum + thay tệp trên đĩa" — việc
// RE-EXEC lại chính nó bằng code mới (để phần nâng cấp dịch vụ chạy đúng
// compose.yaml nhúng mới) nằm ở cmd/genh/main.go (xem cờ nội bộ
// --self-updated), vì đó là quyết định của lệnh gọi, không phải của gói này.
package selfupdate

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"regexp"
	"runtime"
	"strconv"
	"strings"
	"time"
)

// Options cấu hình một lần chạy Run — mọi trường có URL/HTTP đều có thể
// tiêm giả (httptest.Server) khi test, KHÔNG BAO GIỜ gọi thật github.com
// trong unit test.
type Options struct {
	// Owner/Repo xác định repo GitHub phát hành genh, ví dụ
	// "Genesis-ryan-84-0567536339" / "Gen-Harness".
	Owner string
	Repo  string

	// CurrentVersion là main.version của binary đang chạy. "" hoặc "dev" ⇒
	// Run luôn bỏ qua (bản dev không có gì để so semver, và không nên bị
	// một `genh update` tự động âm thầm thay bằng bản release).
	CurrentVersion string

	// GOOS/GOARCH chọn asset đúng nền tảng — mặc định runtime.GOOS/GOARCH
	// nếu để rỗng, tách riêng để test dựng asset cho nền tảng khác máy chạy
	// test.
	GOOS   string
	GOARCH string

	// ExecutablePath là đường dẫn tệp binary ĐANG CHẠY cần thay — thường
	// os.Executable() đã resolve. Bắt buộc phải khác rỗng để Run tự thay
	// binary; để trống nếu chỉ muốn hỏi bản mới nhất mà không thay gì (dùng
	// khi test bước so sánh).
	ExecutablePath string

	// Client là *http.Client dùng cho mọi request — nil thì metadata dùng
	// client riêng có Timeout theo HTTPTimeout, còn tải asset/checksums.txt
	// dùng client KHÔNG có Timeout tổng (xem download.go). Khác nil (test)
	// thì dùng nó cho cả hai, đồng hồ rảnh IdleTimeout vẫn áp khi tải.
	Client *http.Client
	// HTTPTimeout CHỈ áp cho request metadata (/releases/latest) khi Client
	// nil — mặc định 20s. KHÔNG áp cho tải asset/checksums.txt (asset vài
	// chục MB qua mạng chậm sẽ quá giờ dù dữ liệu vẫn đều đặn về).
	HTTPTimeout time.Duration

	// IdleTimeout là thời gian rảnh khi tải asset/checksums.txt: không nhận
	// được byte nào trong khoảng này ⇒ coi lần thử là hỏng — mặc định 60s.
	IdleTimeout time.Duration
	// DownloadAttempts là số lần thử tối đa mỗi tệp tải (lỗi tạm thời: mạng,
	// hết thời gian rảnh, đứt giữa chừng, HTTP 5xx/408/429) — mặc định 3.
	DownloadAttempts int
	// RetryDelays là thời gian chờ trước lần thử lại thứ 1, 2… (hết danh
	// sách dùng phần tử cuối) — mặc định 5s, 15s; test đặt mili giây.
	RetryDelays []time.Duration

	// APIBase mặc định "https://api.github.com" — tiêm httptest.Server.URL
	// khi test.
	APIBase string
	// DownloadBase mặc định "https://github.com" (asset tải qua
	// /<owner>/<repo>/releases/download/<tag>/<asset>) — tiêm khi test.
	DownloadBase string

	// Out nhận các dòng tiến độ/cảnh báo — nil dùng io.Discard. Quiet=true
	// chỉ in dòng QUAN TRỌNG (có bản mới hơn / lỗi mạng-checksum / đã thay
	// xong), bỏ các dòng "đang kiểm tra…".
	Out   io.Writer
	Quiet bool

	// MinAge là thời gian chín: > 0 ⇒ bản mới nhất thành bản chính thức
	// (officialSince: dấu promote, không có thì published_at) chưa đủ MinAge
	// bị BỎ QUA (Result.Deferred = true), đợi lần chạy sau. 0 = không chặn —
	// mặc định cho nút "Cập nhật ngay" và `genh update` gõ tay; `--yes` (lịch
	// đêm) dùng NightlyMinAge (quyết định ở cmd/genh selfUpdateMinAge).
	MinAge time.Duration
	// Now trả "bây giờ" để tính tuổi bản phát hành — nil dùng time.Now; tách
	// riêng để test tiêm đồng hồ cố định.
	Now func() time.Time
}

// NightlyMinAge là thời gian chín cho lịch tự cập nhật đêm: chỉ tự cài bản
// đã là bản chính thức ít nhất 24 giờ (tính từ lúc promote — xem
// officialSince).
const NightlyMinAge = 24 * time.Hour

// promotedMarkerRe khớp dấu PromotedMarker trong ghi chú Release. Chỉ nhận
// đúng dạng RFC 3339 UTC giây (job promote ghi bằng `date -u
// +%Y-%m-%dT%H:%M:%SZ`) — chuỗi lạ bị bỏ qua, rơi về published_at.
var promotedMarkerRe = regexp.MustCompile(`<!--\s*genh:promoted_at=([0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z)\s*-->`)

// PromotedMarker là dòng job `promote` (.github/workflows/e2e-install.yml)
// ghi vào cuối ghi chú Release CÙNG LÚC nâng bản thử thành bản chính thức
// (một lệnh `gh release edit --prerelease=false --latest --notes-file`):
// một chú thích HTML — không hiện khi GitHub hiển thị ghi chú — mang thời
// điểm promote, để thời gian chín tính từ lúc bản này tới tay máy người
// dùng thay vì từ lúc tạo bản thử (published_at, promote không đổi).
// Hàm này là nguồn chuẩn của định dạng; test giữ workflow khớp nó.
func PromotedMarker(t time.Time) string {
	return "<!-- genh:promoted_at=" + t.UTC().Format(time.RFC3339) + " -->"
}

// promotedAt đọc thời điểm promote MUỘN NHẤT trong ghi chú Release (đề
// phòng ghi chú còn dấu cũ của một lần promote trước); không có dấu hợp lệ
// ⇒ zero.
func promotedAt(body string) time.Time {
	var latest time.Time
	for _, m := range promotedMarkerRe.FindAllStringSubmatch(body, -1) {
		t, err := time.Parse(time.RFC3339, m[1])
		if err == nil && t.After(latest) {
			latest = t
		}
	}
	return latest
}

// Result là kết quả một lần Run.
type Result struct {
	Updated bool   // đã thay binary trên đĩa hay chưa
	From    string // CurrentVersion
	To      string // tag bản mới (rỗng nếu Updated == false)
	Skipped bool   // true nếu Run chủ động bỏ qua (bản dev, đã mới nhất, lỗi mạng...)
	// Deferred = true khi có bản mới hơn nhưng chưa đủ thời gian chín
	// (Options.MinAge) — luôn đi kèm Skipped = true; lần chạy sau sẽ cài.
	Deferred bool
	Reason   string // vì sao bỏ qua/không thay — LUÔN có giá trị khi Skipped
}

const defaultHTTPTimeout = 20 * time.Second

func (o Options) client() *http.Client {
	if o.Client != nil {
		return o.Client
	}
	timeout := o.HTTPTimeout
	if timeout <= 0 {
		timeout = defaultHTTPTimeout
	}
	return &http.Client{Timeout: timeout}
}

func (o Options) apiBase() string {
	if o.APIBase != "" {
		return strings.TrimSuffix(o.APIBase, "/")
	}
	return "https://api.github.com"
}

func (o Options) downloadBase() string {
	if o.DownloadBase != "" {
		return strings.TrimSuffix(o.DownloadBase, "/")
	}
	return "https://github.com"
}

func (o Options) goos() string {
	if o.GOOS != "" {
		return o.GOOS
	}
	return runtime.GOOS
}

func (o Options) goarch() string {
	if o.GOARCH != "" {
		return o.GOARCH
	}
	return runtime.GOARCH
}

func (o Options) now() time.Time {
	if o.Now != nil {
		return o.Now()
	}
	return time.Now()
}

func (o Options) logf(important bool, format string, args ...any) {
	if o.Out == nil {
		return
	}
	if o.Quiet && !important {
		return
	}
	_, _ = fmt.Fprintf(o.Out, format+"\n", args...)
}

// AssetName trả về tên tệp binary phát hành đúng quy ước của install.sh/
// install.ps1 (docs/handoff/05-installer.md): "genh-<os>-<arch>", thêm
// ".exe" trên Windows.
func AssetName(goos, goarch string) string {
	if goos == "windows" {
		return fmt.Sprintf("genh-windows-%s.exe", goarch)
	}
	return fmt.Sprintf("genh-%s-%s", goos, goarch)
}

// Run thực hiện toàn bộ khung: hỏi bản mới nhất -> so semver với
// CurrentVersion -> nếu mới hơn, tải asset + checksums.txt của ĐÚNG tag đó
// (không dùng /latest/download để tránh lệch bản giữa lúc hỏi metadata và
// lúc tải nếu có release mới xen vào giữa chừng) -> kiểm SHA-256 (BẮT BUỘC,
// sai -> từ chối, KHÔNG thay gì) -> thay ExecutablePath an toàn.
//
// Chế độ lịch đêm (MinAge > 0, v0.1.53): hỏi DANH SÁCH bản phát hành
// (GET /releases?per_page=10, pickRelease) và cài bản semver CAO NHẤT đã đủ chín
// — không bị bản mới nhất chưa chín chặn "đói" khi bản ra dồn dập; chưa có bản
// nào đủ chín mà có bản mới hơn ⇒ Skipped+Deferred (Reason nêu bản mới nhất và
// tuổi). MinAge == 0 (nút "Cập nhật ngay", gõ tay) vẫn chỉ hỏi /releases/latest.
//
// Trước khi tải, bản mới hơn còn phải qua 2 cửa (cả hai đều trả Skipped,
// err == nil, KHÔNG có request tải asset/checksums nào):
//   - bản thử (prerelease) hoặc bản nháp (draft) ⇒ luôn bỏ qua (phòng hờ —
//     /releases/latest của GitHub vốn không trả 2 loại này);
//   - thời gian chín: MinAge > 0 và bản này chưa là bản chính thức đủ MinAge
//     (officialSince — dấu promote, không có thì published_at; không đọc
//     được cả hai) ⇒ bỏ qua; riêng trường hợp chưa đủ tuổi đặt
//     Result.Deferred = true. Đúng biên (tuổi == MinAge) thì cho cài.
//
// LỖI MẠNG khi hỏi bản mới nhất KHÔNG làm Run trả lỗi — theo đúng yêu cầu
// "báo và tiếp tục với binary hiện tại": trả Result{Skipped:true} với Reason
// mô tả lỗi, err == nil. Run chỉ trả err khác nil khi ĐÃ CHẮC có bản mới hơn
// nhưng tải/kiểm checksum/thay tệp thất bại — khi đó gọi bên ngoài (cmd/genh)
// tự quyết định có coi là lỗi chặn `genh update` hay chỉ cảnh báo (bản thân
// gói này không biết mức độ nghiêm trọng người gọi muốn).
func Run(ctx context.Context, opts Options) (Result, error) {
	current := strings.TrimSpace(opts.CurrentVersion)
	if current == "" || current == "dev" {
		return Result{Skipped: true, Reason: "binary bản dev (chưa gắn phiên bản phát hành) — bỏ qua tự cập nhật"}, nil
	}

	// Lịch đêm (MinAge > 0): chọn trong DANH SÁCH bản phát hành bản cao nhất ĐÃ ĐỦ
	// chín (v0.1.53, F-96). /releases/latest chỉ cho bản MỚI NHẤT — khi bản ra dồn
	// dập (vd v0.1.54 vừa lên 1 giờ trước, v0.1.53 đã chín 25 giờ) lịch đêm cứ bị
	// bản mới nhất chưa chín hoãn, "đói" mãi không cài được bản nào. Nút "Cập nhật
	// ngay"/gõ tay (MinAge == 0) giữ đường /releases/latest như cũ. Không hỏi được
	// danh sách ⇒ rơi về /releases/latest (1 dòng log).
	if opts.MinAge > 0 {
		opts.logf(false, "genh: đang hỏi danh sách bản genh (%s/%s)…", opts.Owner, opts.Repo)
		list, err := listReleases(ctx, opts)
		if err == nil {
			return runFromList(ctx, opts, current, list)
		}
		opts.logf(true, "genh: không hỏi được danh sách bản phát hành (%v) — dùng bản mới nhất (releases/latest).", err)
	}

	opts.logf(false, "genh: đang hỏi bản genh mới nhất (%s/%s)…", opts.Owner, opts.Repo)
	meta, err := latestRelease(ctx, opts)
	if err != nil {
		reason := fmt.Sprintf("không hỏi được bản mới nhất (%v) — tiếp tục với bản hiện tại %s", err, current)
		opts.logf(true, "genh: %s", reason)
		return Result{Skipped: true, Reason: reason}, nil
	}
	latest := meta.TagName

	if !IsNewer(current, latest) {
		opts.logf(false, "genh: đã ở bản mới nhất (%s).", current)
		return Result{Skipped: true, Reason: "đã ở bản mới nhất"}, nil
	}

	if meta.Prerelease || meta.Draft {
		reason := fmt.Sprintf("bản %s chưa phải bản chính thức — bỏ qua", latest)
		opts.logf(true, "genh: %s", reason)
		return Result{Skipped: true, Reason: reason}, nil
	}

	if opts.MinAge > 0 {
		since := meta.officialSince()
		if since.IsZero() {
			reason := fmt.Sprintf("không đọc được thời điểm phát hành của %s — chế độ --yes (lịch đêm) bỏ qua cho an toàn; %s", latest, installNowHint)
			opts.logf(true, "genh: %s", reason)
			return Result{Skipped: true, Reason: reason}, nil
		}
		age := opts.now().Sub(since)
		if age < opts.MinAge {
			reason := fmt.Sprintf("bản %s mới phát hành %s trước — chế độ --yes (lịch đêm) đợi đủ %s rồi mới cài; %s",
				latest, formatDurationVi(age), formatDurationVi(opts.MinAge), installNowHint)
			// important=true: dòng này phải vào logs/auto-update.log cả khi --quiet.
			opts.logf(true, "genh: %s", reason)
			return Result{Skipped: true, Deferred: true, Reason: reason}, nil
		}
	}

	return applyRelease(ctx, opts, current, latest)
}

// applyRelease tải + kiểm checksum + thay binary bằng ĐÚNG tag đã chọn.
func applyRelease(ctx context.Context, opts Options, current, tag string) (Result, error) {
	opts.logf(true, "genh: có bản mới %s (đang chạy %s) — đang tải…", tag, current)

	if opts.ExecutablePath == "" {
		return Result{Skipped: true, Reason: "không có ExecutablePath để thay"}, nil
	}

	asset := AssetName(opts.goos(), opts.goarch())
	data, err := downloadVerified(ctx, opts, tag, asset)
	if err != nil {
		return Result{}, fmt.Errorf("tải/kiểm checksum %s bản %s: %w", asset, tag, err)
	}

	if err := replaceExecutable(opts.ExecutablePath, data); err != nil {
		return Result{}, fmt.Errorf("thay binary %s: %w", opts.ExecutablePath, err)
	}

	opts.logf(true, "genh: đã tự cập nhật lên %s (bản cũ %s).", tag, current)
	return Result{Updated: true, From: current, To: tag}, nil
}

// runFromList là Run cho chế độ lịch đêm (MinAge > 0) khi đã có danh sách bản phát hành.
func runFromList(ctx context.Context, opts Options, current string, list []releaseMeta) (Result, error) {
	if pick, ok := pickRelease(list, current, opts.MinAge, opts.now()); ok {
		return applyRelease(ctx, opts, current, pick.TagName)
	}
	newest, any := newestNewer(list, current)
	if !any {
		opts.logf(false, "genh: đã ở bản mới nhất (%s).", current)
		return Result{Skipped: true, Reason: "đã ở bản mới nhất"}, nil
	}
	since := newest.officialSince()
	switch {
	case promotedAt(newest.Body).IsZero():
		// Không có dấu promote hợp lệ: danh sách không chọn bản này (không biết nó
		// thành bản chính thức lúc nào) — không hứa "đợi đủ 24 giờ" vì có đợi cũng không tự cài.
		reason := fmt.Sprintf("bản %s chưa có dấu promote (không biết khi nào thành bản chính thức) — chế độ --yes (lịch đêm) bỏ qua cho an toàn; %s", newest.TagName, installNowHint)
		opts.logf(true, "genh: %s", reason)
		return Result{Skipped: true, Reason: reason}, nil
	default:
		age := opts.now().Sub(since)
		reason := fmt.Sprintf("bản %s mới phát hành %s trước — chế độ --yes (lịch đêm) đợi đủ %s rồi mới cài; %s",
			newest.TagName, formatDurationVi(age), formatDurationVi(opts.MinAge), installNowHint)
		opts.logf(true, "genh: %s", reason)
		return Result{Skipped: true, Deferred: true, Reason: reason}, nil
	}
}

// installNowHint là phần "muốn cài ngay thì làm gì" của Reason khi thời
// gian chín chặn — nút chỉ có khi máy chủ đã cài watcher nhận yêu cầu (Console
// thiếu watcher thì hiện lệnh), nên nêu cả lệnh gõ tay.
const installNowHint = "muốn cài ngay: bấm \"Cập nhật ngay\" trong Console hoặc chạy `genh update` (không kèm --yes)"

// releaseMeta là phần JSON cần của response GitHub
// GET /repos/{owner}/{repo}/releases/latest.
type releaseMeta struct {
	TagName string `json:"tag_name"`
	// PublishedAt rỗng (zero) nếu GitHub trả null/thiếu. Là lúc tạo bản
	// (bản thử) — promote KHÔNG đổi trường này.
	PublishedAt time.Time `json:"published_at"`
	Prerelease  bool      `json:"prerelease"`
	Draft       bool      `json:"draft"`
	// Body là ghi chú Release — nơi job promote ghi PromotedMarker.
	Body string `json:"body"`
}

// officialSince là mốc tính thời gian chín: lúc bản này thành bản chính
// thức. Lấy cái MUỘN HƠN giữa dấu promote trong ghi chú và published_at —
// bản phát hành trước cổng v0.1.33 (thẳng thành latest, không có dấu) dùng
// published_at; dấu lỡ ghi sớm hơn published_at cũng không làm cổng ngắn
// đi. Cả hai zero ⇒ zero (Run coi là không đọc được thời điểm phát hành).
func (m releaseMeta) officialSince() time.Time {
	since := m.PublishedAt
	if p := promotedAt(m.Body); p.After(since) {
		since = p
	}
	return since
}

func latestRelease(ctx context.Context, opts Options) (releaseMeta, error) {
	url := fmt.Sprintf("%s/repos/%s/%s/releases/latest", opts.apiBase(), opts.Owner, opts.Repo)
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, url, nil)
	if err != nil {
		return releaseMeta{}, err
	}
	// GitHub API từ chối request không có User-Agent (403) — xem
	// https://docs.github.com/en/rest/overview/resources-in-the-rest-api.
	req.Header.Set("User-Agent", "gen-harness-genh")
	req.Header.Set("Accept", "application/vnd.github+json")

	resp, err := opts.client().Do(req)
	if err != nil {
		return releaseMeta{}, err
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		body, _ := io.ReadAll(io.LimitReader(resp.Body, 4096))
		return releaseMeta{}, fmt.Errorf("GET %s: %s — %s", url, resp.Status, strings.TrimSpace(string(body)))
	}

	var meta releaseMeta
	if err := json.NewDecoder(resp.Body).Decode(&meta); err != nil {
		return releaseMeta{}, fmt.Errorf("giải mã JSON release: %w", err)
	}
	if meta.TagName == "" {
		return releaseMeta{}, errors.New("response không có tag_name")
	}
	return meta, nil
}

// listReleases hỏi GET <api>/repos/<owner>/<repo>/releases?per_page=10 (10 bản phát
// hành gần nhất, mới trước) — dùng cho chế độ lịch đêm (pickRelease).
func listReleases(ctx context.Context, opts Options) ([]releaseMeta, error) {
	url := fmt.Sprintf("%s/repos/%s/%s/releases?per_page=10", opts.apiBase(), opts.Owner, opts.Repo)
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, url, nil)
	if err != nil {
		return nil, err
	}
	req.Header.Set("User-Agent", "gen-harness-genh")
	req.Header.Set("Accept", "application/vnd.github+json")

	resp, err := opts.client().Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		body, _ := io.ReadAll(io.LimitReader(resp.Body, 4096))
		return nil, fmt.Errorf("GET %s: %s — %s", url, resp.Status, strings.TrimSpace(string(body)))
	}
	var list []releaseMeta
	if err := json.NewDecoder(io.LimitReader(resp.Body, 8<<20)).Decode(&list); err != nil {
		return nil, fmt.Errorf("giải mã JSON danh sách release: %w", err)
	}
	out := list[:0]
	for _, m := range list {
		if m.TagName != "" {
			out = append(out, m)
		}
	}
	return out, nil
}

// pickRelease chọn bản semver CAO NHẤT thoả: không phải bản nháp/bản thử, có dấu
// PromotedMarker hợp lệ, mới hơn current, và đã là bản chính thức đủ minAge
// (now − officialSince ≥ minAge; đúng biên thì cho cài). found=false nếu không có bản nào.
func pickRelease(list []releaseMeta, current string, minAge time.Duration, now time.Time) (pick releaseMeta, found bool) {
	for _, m := range list {
		if m.Draft || m.Prerelease || !IsNewer(current, m.TagName) {
			continue
		}
		if promotedAt(m.Body).IsZero() {
			continue
		}
		since := m.officialSince()
		if since.IsZero() || now.Sub(since) < minAge {
			continue
		}
		if !found || IsNewer(pick.TagName, m.TagName) {
			pick, found = m, true
		}
	}
	return pick, found
}

// newestNewer: bản chính thức (không nháp/thử) semver cao nhất mới hơn current,
// chín hay chưa — để Reason nêu "bản mới nhất" khi chưa có bản nào đủ chín.
func newestNewer(list []releaseMeta, current string) (newest releaseMeta, any bool) {
	for _, m := range list {
		if m.Draft || m.Prerelease || !IsNewer(current, m.TagName) {
			continue
		}
		if !any || IsNewer(newest.TagName, m.TagName) {
			newest, any = m, true
		}
	}
	return newest, any
}

// formatDurationVi in thời lượng làm tròn XUỐNG tới phút theo kiểu tiếng Việt
// cho log/Reason: "24 giờ", "3 giờ 5 phút", "45 phút"; dưới 1 phút (kể cả âm
// — đồng hồ máy lệch so với GitHub) ⇒ "chưa đến 1 phút". Làm tròn xuống để
// bản 23 giờ 59 phút 40 giây không bị in thành "24 giờ" khi đang bị hoãn.
func formatDurationVi(d time.Duration) string {
	d = d.Truncate(time.Minute)
	if d < time.Minute {
		return "chưa đến 1 phút"
	}
	h := int64(d / time.Hour)
	m := int64((d % time.Hour) / time.Minute)
	switch {
	case h > 0 && m > 0:
		return fmt.Sprintf("%d giờ %d phút", h, m)
	case h > 0:
		return fmt.Sprintf("%d giờ", h)
	default:
		return fmt.Sprintf("%d phút", m)
	}
}

// downloadVerified tải asset + checksums.txt của đúng tag, kiểm SHA-256
// BẮT BUỘC (giống install.sh verify_checksum) trước khi trả bytes — không
// bao giờ trả bytes chưa kiểm. Cả hai tệp tải qua downloadWithRetry (thời
// gian rảnh + thử lại); sai checksum thì KHÔNG thử lại.
func downloadVerified(ctx context.Context, opts Options, tag, asset string) ([]byte, error) {
	base := fmt.Sprintf("%s/%s/%s/releases/download/%s", opts.downloadBase(), opts.Owner, opts.Repo, tag)

	sums, err := downloadWithRetry(ctx, opts, base+"/checksums.txt", maxChecksumsBytes)
	if err != nil {
		return nil, fmt.Errorf("tải checksums.txt: %w", err)
	}
	want, err := findChecksum(string(sums), asset)
	if err != nil {
		return nil, err
	}

	data, err := downloadWithRetry(ctx, opts, base+"/"+asset, maxAssetBytes)
	if err != nil {
		return nil, fmt.Errorf("tải %s: %w", asset, err)
	}

	got := sha256Hex(data)
	if !strings.EqualFold(got, want) {
		return nil, fmt.Errorf("SHA-256 của %s không khớp checksums.txt (muốn %s, được %s) — từ chối, không thay binary", asset, want, got)
	}
	return data, nil
}

// sha256Hex trả về SHA-256 của data dạng chuỗi hex thường (khớp định dạng
// checksums.txt do `sha256sum`/`shasum -a 256` sinh ra — xem install.sh
// sha256_of).
func sha256Hex(data []byte) string {
	sum := sha256.Sum256(data)
	return hex.EncodeToString(sum[:])
}

// findChecksum tìm dòng "<sha256>  <tên tệp>" khớp basename asset trong nội
// dung checksums.txt — cùng cách so khớp với install.sh (bỏ tiền tố "./").
func findChecksum(checksums, asset string) (string, error) {
	for _, line := range strings.Split(checksums, "\n") {
		fields := strings.Fields(line)
		if len(fields) < 2 {
			continue
		}
		name := strings.TrimPrefix(fields[1], "./")
		if name == asset {
			return fields[0], nil
		}
	}
	return "", fmt.Errorf("không tìm thấy %s trong checksums.txt", asset)
}

// replaceExecutable ghi data ra một tệp tạm CÙNG THƯ MỤC với execPath (đảm
// bảo rename là cùng filesystem, atomic trên Linux/macOS) rồi thay execPath:
//
//   - Linux/macOS: os.Rename thẳng đè lên execPath — hệ điều hành cho phép
//     thay/xoá tệp thực thi đang chạy (inode cũ vẫn sống cho tới khi tiến
//     trình đang dùng nó thoát), tiến trình genh hiện tại tiếp tục chạy bình
//     thường bằng nội dung CŨ cho tới khi re-exec (cmd/genh/main.go).
//   - Windows: KHÔNG rename được lên một .exe đang chạy (file bị khoá) — đổi
//     tên bản cũ thành ".old" trước (Windows cho phép rename tệp đang chạy,
//     chỉ không cho ghi đè/xoá), rồi đặt bản mới đúng tên cũ. Tệp ".old" dọn
//     best-effort ở lần chạy genh KẾ TIẾP (os.Remove không lỗi thì thôi, có
//     lỗi cũng không sao — không phải tệp quan trọng).
func replaceExecutable(execPath string, data []byte) error {
	dir := filepath.Dir(execPath)
	tmp, err := os.CreateTemp(dir, ".genh-update-*")
	if err != nil {
		return fmt.Errorf("tạo tệp tạm trong %s: %w", dir, err)
	}
	tmpPath := tmp.Name()
	_, writeErr := tmp.Write(data)
	closeErr := tmp.Close()
	if writeErr != nil {
		_ = os.Remove(tmpPath)
		return fmt.Errorf("ghi tệp tạm: %w", writeErr)
	}
	if closeErr != nil {
		_ = os.Remove(tmpPath)
		return fmt.Errorf("đóng tệp tạm: %w", closeErr)
	}
	if err := os.Chmod(tmpPath, 0o755); err != nil {
		_ = os.Remove(tmpPath)
		return fmt.Errorf("chmod tệp tạm: %w", err)
	}

	if runtime.GOOS == "windows" {
		oldPath := execPath + ".old"
		_ = os.Remove(oldPath) // dọn tàn dư lần thay trước, nếu có — bỏ qua lỗi
		if err := os.Rename(execPath, oldPath); err != nil {
			_ = os.Remove(tmpPath)
			return fmt.Errorf("đổi tên bản cũ %s -> %s: %w", execPath, oldPath, err)
		}
		if err := os.Rename(tmpPath, execPath); err != nil {
			// Cố khôi phục bản cũ để không để máy không có genh.exe nào chạy được.
			_ = os.Rename(oldPath, execPath)
			return fmt.Errorf("đặt bản mới vào %s: %w", execPath, err)
		}
		_ = os.Remove(oldPath)
		return nil
	}

	if err := os.Rename(tmpPath, execPath); err != nil {
		_ = os.Remove(tmpPath)
		return fmt.Errorf("đổi tên %s -> %s: %w", tmpPath, execPath, err)
	}
	return nil
}

// --- so sánh semver ---

// IsNewer báo true nếu latest là một bản semver MỚI HƠN current theo đúng
// nghĩa (major, minor, patch) — bản tiền phát hành (-rc1, -beta…) bị bỏ qua
// phần hậu tố, chỉ so 3 số chính; latest/current không đúng dạng
// "vMAJOR.MINOR.PATCH[...]" -> false (không bao giờ tự nhận "mới hơn" một
// chuỗi không so được, an toàn hơn là lỡ tự cập nhật nhầm).
func IsNewer(current, latest string) bool {
	cm, cn, cp, ok := parseSemver(current)
	if !ok {
		return false
	}
	lm, ln, lp, ok := parseSemver(latest)
	if !ok {
		return false
	}
	if lm != cm {
		return lm > cm
	}
	if ln != cn {
		return ln > cn
	}
	return lp > cp
}

func parseSemver(s string) (major, minor, patch int, ok bool) {
	s = strings.TrimSpace(s)
	s = strings.TrimPrefix(s, "v")
	if i := strings.IndexAny(s, "-+"); i >= 0 {
		s = s[:i]
	}
	parts := strings.Split(s, ".")
	if len(parts) != 3 {
		return 0, 0, 0, false
	}
	nums := make([]int, 3)
	for i, p := range parts {
		n, err := strconv.Atoi(p)
		if err != nil || n < 0 {
			return 0, 0, 0, false
		}
		nums[i] = n
	}
	return nums[0], nums[1], nums[2], true
}
