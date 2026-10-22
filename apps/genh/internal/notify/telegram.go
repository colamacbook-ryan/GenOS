package notify

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"strings"
	"time"
	"unicode/utf8"

	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/redact"
)

// DefaultAPIBase là gốc Bot API thật.
const DefaultAPIBase = "https://api.telegram.org"

// EnvAPIBase đổi gốc Bot API — CHỈ cho test/e2e (máy chủ HTTP giả), không
// phải cấu hình cho Owner.
const EnvAPIBase = "GENH_TELEGRAM_API_BASE"

// maxTextRunes: Telegram giới hạn 4096 ký tự/tin — cắt ở 3900 cho an toàn.
const maxTextRunes = 3900

// Mã lỗi gửi Telegram (hợp đồng chung với apps/api — giữ đúng chữ).
const (
	CodeNotConfigured  = "TELEGRAM_NOT_CONFIGURED"
	CodeTokenRejected  = "TELEGRAM_TOKEN_REJECTED"
	CodeChatNotFound   = "TELEGRAM_CHAT_NOT_FOUND"
	CodeBotBlocked     = "TELEGRAM_BOT_BLOCKED"
	CodeRateLimited    = "TELEGRAM_RATE_LIMITED"
	CodeUnreachable    = "TELEGRAM_UNREACHABLE"
	CodeKeyMismatch    = "TELEGRAM_KEY_MISMATCH" // chỉ phía genh: không giải được token bằng khoá master
	defaultHTTPTimeout = 10 * time.Second
)

// SendError là lỗi gửi tin — Error() KHÔNG BAO GIỜ chứa token.
type SendError struct {
	Code       string
	Status     int           // mã HTTP (0 = lỗi mạng/timeout)
	RetryAfter time.Duration // 429: Telegram bảo chờ bao lâu
	msg        string        // đã che bí mật
}

func (e *SendError) Error() string {
	s := "Telegram " + e.Code
	if e.Status > 0 {
		s += fmt.Sprintf(" (HTTP %d)", e.Status)
	}
	if e.msg != "" {
		s += ": " + e.msg
	}
	return s
}

// Client gửi tin qua Bot API.
type Client struct {
	// BaseURL: gốc Bot API ("" = biến GENH_TELEGRAM_API_BASE hoặc DefaultAPIBase).
	BaseURL string
	// HTTP: nil = client timeout 10 giây, Proxy lấy từ môi trường.
	HTTP *http.Client
}

// NewClient dựng Client mặc định (đọc GENH_TELEGRAM_API_BASE cho test/e2e).
func NewClient() *Client {
	return &Client{BaseURL: os.Getenv(EnvAPIBase), HTTP: defaultHTTPClient()}
}

func defaultHTTPClient() *http.Client {
	tr := http.DefaultTransport.(*http.Transport).Clone()
	tr.Proxy = http.ProxyFromEnvironment
	return &http.Client{Timeout: defaultHTTPTimeout, Transport: tr}
}

func (c *Client) base() string {
	b := ""
	if c != nil {
		b = c.BaseURL
	}
	if b == "" {
		b = os.Getenv(EnvAPIBase)
	}
	if b == "" {
		b = DefaultAPIBase
	}
	return strings.TrimRight(b, "/")
}

func (c *Client) httpClient() *http.Client {
	if c != nil && c.HTTP != nil {
		return c.HTTP
	}
	return defaultHTTPClient()
}

// TruncateText cắt text còn ≤ 3900 rune (thêm "…").
func TruncateText(text string) string {
	if utf8.RuneCountInString(text) <= maxTextRunes {
		return text
	}
	rs := []rune(text)
	return string(rs[:maxTextRunes-1]) + "…"
}

// Send gửi MỘT tin văn bản thường (không parse_mode, tắt xem trước liên kết).
// Lỗi luôn là *SendError đã che token.
func (c *Client) Send(ctx context.Context, cfg Config, text string) error {
	red := redact.New(cfg.Token)
	if cfg.Token == "" || cfg.ChatID == "" {
		return &SendError{Code: CodeNotConfigured}
	}
	body, err := json.Marshal(map[string]any{
		"chat_id":                  cfg.ChatID,
		"text":                     TruncateText(text),
		"disable_web_page_preview": true,
	})
	if err != nil {
		return &SendError{Code: CodeUnreachable, msg: "không dựng được nội dung tin"}
	}
	url := c.base() + "/bot" + cfg.Token + "/sendMessage"
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, url, bytes.NewReader(body))
	if err != nil {
		return &SendError{Code: CodeUnreachable, msg: red.String(err.Error())}
	}
	req.Header.Set("Content-Type", "application/json")
	resp, err := c.httpClient().Do(req)
	if err != nil {
		// *url.Error mang nguyên URL (có token) — KHÔNG bọc %w, chỉ giữ chuỗi đã che.
		return &SendError{Code: CodeUnreachable, msg: red.String(err.Error())}
	}
	defer func() { _ = resp.Body.Close() }()
	raw, _ := io.ReadAll(io.LimitReader(resp.Body, 64<<10))
	var tr struct {
		OK          bool   `json:"ok"`
		ErrorCode   int    `json:"error_code"`
		Description string `json:"description"`
		Parameters  struct {
			RetryAfter int `json:"retry_after"`
		} `json:"parameters"`
	}
	_ = json.Unmarshal(raw, &tr)
	if resp.StatusCode == http.StatusOK && tr.OK {
		return nil
	}
	desc := red.String(strings.TrimSpace(tr.Description))
	if len(desc) > 300 {
		desc = strings.ToValidUTF8(desc[:300], "")
	}
	e := &SendError{Status: resp.StatusCode, msg: desc}
	switch {
	case resp.StatusCode == http.StatusUnauthorized || resp.StatusCode == http.StatusNotFound:
		e.Code = CodeTokenRejected
	case resp.StatusCode == http.StatusForbidden:
		e.Code = CodeBotBlocked
	case resp.StatusCode == http.StatusTooManyRequests:
		e.Code = CodeRateLimited
		e.RetryAfter = time.Duration(tr.Parameters.RetryAfter) * time.Second
	case resp.StatusCode == http.StatusBadRequest && strings.Contains(strings.ToLower(tr.Description), "chat not found"):
		e.Code = CodeChatNotFound
	default:
		e.Code = CodeUnreachable
	}
	return e
}
