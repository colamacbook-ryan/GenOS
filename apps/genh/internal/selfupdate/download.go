package selfupdate

// Tải tệp phát hành (asset genh vài chục MB, checksums.txt) theo THỜI GIAN
// RẢNH thay vì timeout cả tệp — sửa lỗi cũ: một http.Client{Timeout: 20s}
// áp cho cả asset nên mạng chậm (dữ liệu vẫn đều đặn về) là hỏng tự cập
// nhật. Giờ chỉ coi là hỏng khi KHÔNG nhận được byte nào trong IdleTimeout,
// và thử lại tối đa DownloadAttempts lần cho lỗi tạm thời (mạng, đứt giữa
// chừng, 5xx/408/429). Lỗi chắc chắn (404/4xx khác, vượt kích thước, tín
// hiệu dừng) trả ngay, không thử lại.

import (
	"context"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"path"
	"strings"
	"sync/atomic"
	"time"
)

const (
	defaultIdleTimeout      = 60 * time.Second
	defaultDownloadAttempts = 3
	// maxAttemptDuration là trần an toàn mỗi lần thử: dù dữ liệu vẫn nhỏ
	// giọt về (không bao giờ "rảnh"), một lần thử cũng không kéo quá 30 phút.
	maxAttemptDuration = 30 * time.Minute

	// maxAssetBytes / maxChecksumsBytes: trần kích thước nhận — vượt là từ
	// chối ngay (không thử lại), tránh bị nhồi đầy RAM.
	maxAssetBytes     = 256 << 20
	maxChecksumsBytes = 1 << 20

	dialTimeout           = 30 * time.Second
	tlsHandshakeTimeout   = 30 * time.Second
	responseHeaderTimeout = 30 * time.Second
)

var defaultRetryDelays = []time.Duration{5 * time.Second, 15 * time.Second}

func (o Options) idleTimeout() time.Duration {
	if o.IdleTimeout > 0 {
		return o.IdleTimeout
	}
	return defaultIdleTimeout
}

func (o Options) downloadAttempts() int {
	if o.DownloadAttempts > 0 {
		return o.DownloadAttempts
	}
	return defaultDownloadAttempts
}

// retryDelay trả thời gian chờ trước lần thử lại thứ n (n = 0 là lần thử
// lại đầu tiên); hết danh sách thì dùng phần tử cuối.
func (o Options) retryDelay(n int) time.Duration {
	delays := o.RetryDelays
	if len(delays) == 0 {
		delays = defaultRetryDelays
	}
	if n >= len(delays) {
		n = len(delays) - 1
	}
	if n < 0 {
		n = 0
	}
	return delays[n]
}

// downloadClient: KHÔNG đặt Timeout tổng (đồng hồ rảnh lo phần đó); chỉ chặn
// từng pha bắt tay (kết nối, TLS, chờ header) ở 30 giây. Options.Client
// (test) được dùng nguyên nếu có — đồng hồ rảnh vẫn áp qua ctx.
func (o Options) downloadClient() *http.Client {
	if o.Client != nil {
		return o.Client
	}
	tr := http.DefaultTransport.(*http.Transport).Clone()
	tr.DialContext = (&net.Dialer{Timeout: dialTimeout, KeepAlive: 30 * time.Second}).DialContext
	tr.TLSHandshakeTimeout = tlsHandshakeTimeout
	tr.ResponseHeaderTimeout = responseHeaderTimeout
	return &http.Client{Transport: tr}
}

// idleReader gia hạn đồng hồ rảnh sau MỖI lần Read nhận được dữ liệu.
type idleReader struct {
	r     io.Reader
	timer *time.Timer
	idle  time.Duration
}

func (r *idleReader) Read(p []byte) (int, error) {
	n, err := r.r.Read(p)
	if n > 0 {
		r.timer.Reset(r.idle)
	}
	return n, err
}

// downloadWithRetry tải url vào bộ nhớ (tối đa maxBytes) theo thời gian rảnh
// opts.IdleTimeout, thử lại tối đa opts.DownloadAttempts lần cho lỗi tạm
// thời. ctx bị huỷ (tín hiệu dừng) ⇒ trả ngay, kể cả đang chờ giữa hai lần
// thử; lỗi bọc ctx.Err() (errors.Is(err, context.Canceled) dùng được).
func downloadWithRetry(ctx context.Context, opts Options, url string, maxBytes int64) ([]byte, error) {
	name := path.Base(url)
	attempts := opts.downloadAttempts()
	client := opts.downloadClient()
	idle := opts.idleTimeout()

	var lastErr error
	for i := 1; i <= attempts; i++ {
		data, retry, err := downloadOnce(ctx, client, url, maxBytes, idle)
		if err == nil {
			return data, nil
		}
		if ctx.Err() != nil {
			return nil, fmt.Errorf("tải %s bị dừng giữa chừng: %w", name, ctx.Err())
		}
		if !retry {
			return nil, err
		}
		lastErr = err
		if i == attempts {
			break
		}
		delay := opts.retryDelay(i - 1)
		opts.logf(true, "genh: tải %s bị ngắt (%v) — thử lại lần %d/%d sau %s…", name, err, i+1, attempts, delay)
		t := time.NewTimer(delay)
		select {
		case <-ctx.Done():
			t.Stop()
			return nil, fmt.Errorf("tải %s bị dừng giữa chừng: %w", name, ctx.Err())
		case <-t.C:
		}
	}
	return nil, fmt.Errorf("tải %s thất bại sau %d lần: %w", name, attempts, lastErr)
}

// downloadOnce là MỘT lần thử; retry báo lỗi có đáng thử lại không.
func downloadOnce(parent context.Context, client *http.Client, url string, maxBytes int64, idle time.Duration) (data []byte, retry bool, err error) {
	ctx, cancel := context.WithTimeout(parent, maxAttemptDuration)
	defer cancel()
	var idleFired atomic.Bool
	timer := time.AfterFunc(idle, func() {
		idleFired.Store(true)
		cancel()
	})
	defer timer.Stop()

	// classify đổi lỗi mạng/đọc thành lỗi dễ hiểu + quyết định thử lại.
	classify := func(e error) (bool, error) {
		switch {
		case parent.Err() != nil:
			return false, parent.Err()
		case idleFired.Load():
			return true, fmt.Errorf("không nhận được dữ liệu trong %s", idle)
		case errors.Is(ctx.Err(), context.DeadlineExceeded):
			return true, fmt.Errorf("một lần tải vượt trần %s", maxAttemptDuration)
		}
		return true, e
	}

	req, err := http.NewRequestWithContext(ctx, http.MethodGet, url, nil)
	if err != nil {
		return nil, false, err
	}
	req.Header.Set("User-Agent", "gen-harness-genh")

	resp, err := client.Do(req)
	if err != nil {
		retry, err := classify(err)
		return nil, retry, err
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		body, _ := io.ReadAll(io.LimitReader(resp.Body, 4096))
		retry := resp.StatusCode >= 500 || resp.StatusCode == http.StatusRequestTimeout || resp.StatusCode == http.StatusTooManyRequests
		return nil, retry, fmt.Errorf("GET %s: %s — %s", url, resp.Status, strings.TrimSpace(string(body)))
	}
	if resp.ContentLength > maxBytes {
		return nil, false, fmt.Errorf("GET %s: tệp %d byte vượt giới hạn %d byte — từ chối", url, resp.ContentLength, maxBytes)
	}

	body := &idleReader{r: resp.Body, timer: timer, idle: idle}
	data, err = io.ReadAll(io.LimitReader(body, maxBytes+1))
	if err != nil {
		retry, err := classify(err)
		return nil, retry, err
	}
	if int64(len(data)) > maxBytes {
		return nil, false, fmt.Errorf("GET %s: tệp vượt giới hạn %d byte — từ chối", url, maxBytes)
	}
	if resp.ContentLength >= 0 && int64(len(data)) != resp.ContentLength {
		return nil, true, fmt.Errorf("nhận %d/%d byte: %w", len(data), resp.ContentLength, io.ErrUnexpectedEOF)
	}
	return data, false, nil
}
