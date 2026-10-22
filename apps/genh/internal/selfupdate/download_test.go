package selfupdate

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"
)

// slowGH là httptest.Server giả GitHub với handler asset tuỳ biến (theo số
// thứ tự request asset, bắt đầu từ 1) — để dựng máy chủ chậm/treo/đứt.
type slowGH struct {
	*httptest.Server
	assetReqs atomic.Int64
}

const slowTag = "v0.2.0"

func newSlowGH(t *testing.T, asset string, data []byte, assetHandler func(n int64, w http.ResponseWriter, r *http.Request)) *slowGH {
	t.Helper()
	gh := &slowGH{}
	base := "/o/r/releases/download/" + slowTag + "/"
	gh.Server = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/repos/o/r/releases/latest":
			_ = json.NewEncoder(w).Encode(map[string]any{"tag_name": slowTag})
		case base + "checksums.txt":
			_, _ = fmt.Fprintf(w, "%s  %s\n", sumHex(data), asset)
		case base + asset:
			assetHandler(gh.assetReqs.Add(1), w, r)
		default:
			http.NotFound(w, r)
		}
	}))
	t.Cleanup(gh.Close)
	return gh
}

// syncBuf là bytes.Buffer an toàn cho nhiều goroutine (logf có thể gọi từ
// goroutine test khác với goroutine đọc kết quả).
type syncBuf struct {
	mu sync.Mutex
	b  bytes.Buffer
}

func (s *syncBuf) Write(p []byte) (int, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.b.Write(p)
}

func (s *syncBuf) String() string {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.b.String()
}

func slowOpts(t *testing.T, gh *slowGH, out *syncBuf) (Options, string) {
	t.Helper()
	execPath := filepath.Join(t.TempDir(), "genh")
	if err := os.WriteFile(execPath, []byte("binary cu"), 0o755); err != nil {
		t.Fatal(err)
	}
	return Options{
		Owner: "o", Repo: "r",
		CurrentVersion: "v0.1.0",
		GOOS:           "linux", GOARCH: "amd64",
		ExecutablePath: execPath,
		APIBase:        gh.URL,
		DownloadBase:   gh.URL,
		Out:            out,
		Quiet:          true,
		IdleTimeout:    200 * time.Millisecond,
		RetryDelays:    []time.Duration{10 * time.Millisecond, 20 * time.Millisecond},
	}, execPath
}

func makeData(n int) []byte {
	b := make([]byte, n)
	for i := range b {
		b[i] = byte(i*7 + i/251)
	}
	return b
}

func mustContent(t *testing.T, path string, want []byte) {
	t.Helper()
	got, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	if !bytes.Equal(got, want) {
		t.Fatalf("nội dung %s không như mong đợi (%d byte, muốn %d byte)", path, len(got), len(want))
	}
}

func TestTai_MayChuCham_RanhDuoiNguong_VanXong(t *testing.T) {
	asset := AssetName("linux", "amd64")
	data := makeData(1 << 20)
	gh := newSlowGH(t, asset, data, func(_ int64, w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Length", strconv.Itoa(len(data)))
		const chunk = 64 << 10
		for off := 0; off < len(data); off += chunk {
			if off > 0 {
				time.Sleep(50 * time.Millisecond)
			}
			_, _ = w.Write(data[off : off+chunk])
			w.(http.Flusher).Flush()
		}
	})
	out := &syncBuf{}
	opts, execPath := slowOpts(t, gh, out)
	opts.HTTPTimeout = 100 * time.Millisecond // chỉ áp metadata; nếu còn áp asset (>500ms) sẽ hỏng

	start := time.Now()
	res, err := Run(context.Background(), opts)
	if err != nil {
		t.Fatalf("Run: %v\nlog:\n%s", err, out)
	}
	if el := time.Since(start); el < 500*time.Millisecond {
		t.Fatalf("máy chủ giả phải gửi chậm > 500ms, đo được %s", el)
	}
	if !res.Updated {
		t.Fatalf("muốn Updated=true, được %+v", res)
	}
	if n := gh.assetReqs.Load(); n != 1 {
		t.Fatalf("mạng chậm nhưng đều không được thử lại: %d request asset", n)
	}
	mustContent(t, execPath, data)
}

func TestTai_TreoGiuaChung_ThuLai(t *testing.T) {
	asset := AssetName("linux", "amd64")
	data := makeData(256 << 10)
	gh := newSlowGH(t, asset, data, func(n int64, w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Length", strconv.Itoa(len(data)))
		if n == 1 {
			_, _ = w.Write(data[:len(data)/2])
			w.(http.Flusher).Flush()
			<-r.Context().Done() // treo tới khi client bỏ request
			return
		}
		_, _ = w.Write(data)
	})
	out := &syncBuf{}
	opts, execPath := slowOpts(t, gh, out)

	res, err := Run(context.Background(), opts)
	if err != nil {
		t.Fatalf("Run: %v\nlog:\n%s", err, out)
	}
	if !res.Updated {
		t.Fatalf("muốn Updated=true, được %+v", res)
	}
	if n := gh.assetReqs.Load(); n != 2 {
		t.Fatalf("muốn đúng 2 request asset, được %d", n)
	}
	log := out.String()
	if !strings.Contains(log, "thử lại lần 2/3") {
		t.Fatalf("thiếu dòng thử lại (quiet vẫn phải in), log:\n%s", log)
	}
	if !strings.Contains(log, "không nhận được dữ liệu trong 200ms") {
		t.Fatalf("thiếu lý do hết thời gian rảnh dễ hiểu, log:\n%s", log)
	}
	mustContent(t, execPath, data)
}

func TestTai_DutKetNoi_ThuLai(t *testing.T) {
	asset := AssetName("linux", "amd64")
	data := makeData(128 << 10)
	gh := newSlowGH(t, asset, data, func(n int64, w http.ResponseWriter, r *http.Request) {
		if n == 1 {
			conn, buf, err := w.(http.Hijacker).Hijack()
			if err != nil {
				t.Errorf("hijack: %v", err)
				return
			}
			_, _ = fmt.Fprintf(buf, "HTTP/1.1 200 OK\r\nContent-Length: %d\r\n\r\n", len(data))
			_, _ = buf.Write(data[:len(data)/2])
			_ = buf.Flush()
			_ = conn.Close()
			return
		}
		_, _ = w.Write(data)
	})
	out := &syncBuf{}
	opts, execPath := slowOpts(t, gh, out)

	res, err := Run(context.Background(), opts)
	if err != nil {
		t.Fatalf("Run: %v\nlog:\n%s", err, out)
	}
	if !res.Updated {
		t.Fatalf("muốn Updated=true, được %+v", res)
	}
	if n := gh.assetReqs.Load(); n != 2 {
		t.Fatalf("muốn đúng 2 request asset, được %d", n)
	}
	if log := out.String(); !strings.Contains(log, "unexpected EOF") || !strings.Contains(log, "thử lại lần 2/3") {
		t.Fatalf("muốn dòng thử lại nêu unexpected EOF, log:\n%s", log)
	}
	mustContent(t, execPath, data)
}

func TestTai_HongCa3Lan_GiuBinaryCu(t *testing.T) {
	asset := AssetName("linux", "amd64")
	data := makeData(1024)
	gh := newSlowGH(t, asset, data, func(_ int64, w http.ResponseWriter, r *http.Request) {
		http.Error(w, "loi may chu", http.StatusInternalServerError)
	})
	out := &syncBuf{}
	opts, execPath := slowOpts(t, gh, out)

	_, err := Run(context.Background(), opts)
	if err == nil || !strings.Contains(err.Error(), "sau 3 lần") {
		t.Fatalf("muốn lỗi \"sau 3 lần\", được %v", err)
	}
	if n := gh.assetReqs.Load(); n != 3 {
		t.Fatalf("muốn đúng 3 request asset, được %d", n)
	}
	if log := out.String(); !strings.Contains(log, "thử lại lần 2/3") || !strings.Contains(log, "thử lại lần 3/3") {
		t.Fatalf("thiếu dòng thử lại, log:\n%s", log)
	}
	mustContent(t, execPath, []byte("binary cu"))
}

func TestTai_404_KhongThuLai(t *testing.T) {
	asset := AssetName("linux", "amd64")
	data := makeData(1024)
	gh := newSlowGH(t, asset, data, func(_ int64, w http.ResponseWriter, r *http.Request) {
		http.NotFound(w, r)
	})
	out := &syncBuf{}
	opts, execPath := slowOpts(t, gh, out)

	_, err := Run(context.Background(), opts)
	if err == nil || !strings.Contains(err.Error(), "404") {
		t.Fatalf("muốn lỗi 404, được %v", err)
	}
	if n := gh.assetReqs.Load(); n != 1 {
		t.Fatalf("404 không được thử lại: %d request asset", n)
	}
	if strings.Contains(out.String(), "thử lại") {
		t.Fatalf("404 không được in dòng thử lại, log:\n%s", out)
	}
	mustContent(t, execPath, []byte("binary cu"))
}

func TestTai_CtxHuyKhiDangCho_TraNgay(t *testing.T) {
	asset := AssetName("linux", "amd64")
	data := makeData(1024)
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	gh := newSlowGH(t, asset, data, func(_ int64, w http.ResponseWriter, r *http.Request) {
		http.Error(w, "loi may chu", http.StatusBadGateway)
	})
	out := &syncBuf{}
	opts, execPath := slowOpts(t, gh, out)
	opts.RetryDelays = []time.Duration{10 * time.Second}

	// Huỷ ngay khi dòng thử lại xuất hiện (đang chờ backoff 10s).
	go func() {
		for !strings.Contains(out.String(), "thử lại lần 2/3") {
			time.Sleep(5 * time.Millisecond)
		}
		cancel()
	}()

	start := time.Now()
	_, err := Run(ctx, opts)
	el := time.Since(start)
	if err == nil || !strings.Contains(err.Error(), "context canceled") {
		t.Fatalf("muốn lỗi bọc context canceled, được %v", err)
	}
	if el >= 200*time.Millisecond {
		t.Fatalf("huỷ ctx khi đang chờ thử lại phải trả ngay, mất %s", el)
	}
	if n := gh.assetReqs.Load(); n != 1 {
		t.Fatalf("huỷ rồi không được tải thêm: %d request asset", n)
	}
	mustContent(t, execPath, []byte("binary cu"))
}

func TestTai_VuotKichThuoc_TuChoi(t *testing.T) {
	// 1) Content-Length khai vượt trần asset 256 MiB ⇒ từ chối ngay, không
	// đọc thân, không thử lại.
	asset := AssetName("linux", "amd64")
	data := makeData(1024)
	gh := newSlowGH(t, asset, data, func(_ int64, w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Length", strconv.Itoa(maxAssetBytes+1))
		w.WriteHeader(http.StatusOK)
	})
	out := &syncBuf{}
	opts, execPath := slowOpts(t, gh, out)
	_, err := Run(context.Background(), opts)
	if err == nil || !strings.Contains(err.Error(), "vượt giới hạn") {
		t.Fatalf("muốn lỗi vượt giới hạn, được %v", err)
	}
	if n := gh.assetReqs.Load(); n != 1 {
		t.Fatalf("vượt kích thước không được thử lại: %d request", n)
	}
	mustContent(t, execPath, []byte("binary cu"))

	// 2) Không khai Content-Length (chunked) mà gửi quá trần ⇒ cũng từ chối.
	var reqs atomic.Int64
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		reqs.Add(1)
		for i := 0; i < 4; i++ {
			_, _ = w.Write(make([]byte, 1024))
			w.(http.Flusher).Flush()
		}
	}))
	defer srv.Close()
	_, err = downloadWithRetry(context.Background(), opts, srv.URL+"/checksums.txt", 2048)
	if err == nil || !strings.Contains(err.Error(), "vượt giới hạn") {
		t.Fatalf("muốn lỗi vượt giới hạn (chunked), được %v", err)
	}
	if n := reqs.Load(); n != 1 {
		t.Fatalf("vượt kích thước không được thử lại: %d request", n)
	}
}

func TestRetryDelay_HetDanhSachDungPhanTuCuoi(t *testing.T) {
	var o Options
	if o.retryDelay(0) != 5*time.Second || o.retryDelay(1) != 15*time.Second || o.retryDelay(5) != 15*time.Second {
		t.Fatalf("mặc định phải 5s, 15s, rồi giữ 15s")
	}
	if o.idleTimeout() != 60*time.Second || o.downloadAttempts() != 3 {
		t.Fatalf("mặc định IdleTimeout=60s, DownloadAttempts=3")
	}
}
