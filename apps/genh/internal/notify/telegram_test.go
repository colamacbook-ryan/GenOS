package notify

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

var testCfg = Config{Token: vectorToken, ChatID: "987654321"}

func TestSend_DungDuongDanVaPayload(t *testing.T) {
	var gotPath string
	var got map[string]any
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotPath = r.URL.Path
		if r.Method != http.MethodPost || r.Header.Get("Content-Type") != "application/json" {
			t.Errorf("method %s, content-type %q", r.Method, r.Header.Get("Content-Type"))
		}
		b, _ := io.ReadAll(r.Body)
		_ = json.Unmarshal(b, &got)
		_, _ = w.Write([]byte(`{"ok":true,"result":{}}`))
	}))
	defer srv.Close()
	c := &Client{BaseURL: srv.URL + "/", HTTP: srv.Client()}
	long := strings.Repeat("ạ", 5000)
	if err := c.Send(context.Background(), testCfg, long); err != nil {
		t.Fatal(err)
	}
	if gotPath != "/bot"+vectorToken+"/sendMessage" {
		t.Fatalf("path = %q", gotPath)
	}
	if got["chat_id"] != "987654321" || got["disable_web_page_preview"] != true {
		t.Fatalf("payload = %v", got)
	}
	if _, ok := got["parse_mode"]; ok {
		t.Fatal("KHÔNG được có parse_mode")
	}
	if n := len([]rune(got["text"].(string))); n > 3900 {
		t.Fatalf("text %d rune, phải ≤ 3900", n)
	}
}

func TestSend_AnhXaMaLoi_KhongLoToken(t *testing.T) {
	cases := []struct {
		status int
		body   string
		code   string
	}{
		{401, `{"ok":false,"error_code":401,"description":"Unauthorized"}`, CodeTokenRejected},
		{404, `{"ok":false,"error_code":404,"description":"Not Found"}`, CodeTokenRejected},
		{403, `{"ok":false,"error_code":403,"description":"Forbidden: bot was blocked by the user"}`, CodeBotBlocked},
		{400, `{"ok":false,"error_code":400,"description":"Bad Request: chat not found"}`, CodeChatNotFound},
		{429, `{"ok":false,"error_code":429,"description":"Too Many Requests: retry after 7","parameters":{"retry_after":7}}`, CodeRateLimited},
		{502, `bad gateway ` + vectorToken, CodeUnreachable},
		// Telegram (hoặc proxy) vọng lại token trong description — vẫn bị che.
		{400, `{"ok":false,"description":"Bad Request: chat not found for bot` + vectorToken + `"}`, CodeChatNotFound},
	}
	for _, c := range cases {
		srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			w.WriteHeader(c.status)
			_, _ = w.Write([]byte(c.body))
		}))
		err := (&Client{BaseURL: srv.URL, HTTP: srv.Client()}).Send(context.Background(), testCfg, "x")
		srv.Close()
		var se *SendError
		if !errors.As(err, &se) || se.Code != c.code || se.Status != c.status {
			t.Errorf("HTTP %d: muốn %s, được %v", c.status, c.code, err)
			continue
		}
		if c.code == CodeRateLimited && se.RetryAfter != 7*time.Second {
			t.Errorf("retry_after = %v", se.RetryAfter)
		}
		if strings.Contains(err.Error(), vectorToken) || strings.Contains(err.Error(), "AAFake") {
			t.Errorf("HTTP %d: lỗi lộ token: %s", c.status, err)
		}
	}
}

func TestSend_LoiMang_KhongLoToken(t *testing.T) {
	// Máy chủ đã đóng ⇒ connection refused — *url.Error mang nguyên URL có token.
	srv := httptest.NewServer(http.HandlerFunc(func(http.ResponseWriter, *http.Request) {}))
	base := srv.URL
	srv.Close()
	err := (&Client{BaseURL: base}).Send(context.Background(), testCfg, "x")
	var se *SendError
	if !errors.As(err, &se) || se.Code != CodeUnreachable || se.Status != 0 {
		t.Fatalf("muốn TELEGRAM_UNREACHABLE, được %v", err)
	}
	if strings.Contains(err.Error(), vectorToken) || strings.Contains(err.Error(), "AAFake") {
		t.Fatalf("lỗi mạng lộ token: %s", err)
	}
	if !strings.Contains(err.Error(), "/bot***/sendMessage") {
		t.Fatalf("lỗi nên còn đường dẫn đã che để chẩn đoán: %s", err)
	}

	// Timeout.
	slow := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		select {
		case <-r.Context().Done():
		case <-time.After(2 * time.Second):
		}
	}))
	defer slow.Close()
	err = (&Client{BaseURL: slow.URL, HTTP: &http.Client{Timeout: 50 * time.Millisecond}}).Send(context.Background(), testCfg, "x")
	if !errors.As(err, &se) || se.Code != CodeUnreachable || strings.Contains(err.Error(), "AAFake") {
		t.Fatalf("timeout: %v", err)
	}

	// Chưa cấu hình.
	if err := (&Client{BaseURL: base}).Send(context.Background(), Config{}, "x"); !errors.As(err, &se) || se.Code != CodeNotConfigured {
		t.Fatalf("thiếu token: %v", err)
	}
}

func TestClient_BaseTuBienMoiTruong(t *testing.T) {
	t.Setenv(EnvAPIBase, "http://127.0.0.1:9/")
	if got := NewClient().base(); got != "http://127.0.0.1:9" {
		t.Fatalf("base = %q", got)
	}
	t.Setenv(EnvAPIBase, "")
	if got := (&Client{}).base(); got != DefaultAPIBase {
		t.Fatalf("base = %q", got)
	}
}
