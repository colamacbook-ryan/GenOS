// Package redact che bí mật trong văn bản genh đưa ra ngoài máy (gói chẩn đoán
// `genh doctor`, lỗi gửi Telegram, log trực canh) — v0.1.44 (F-4b, F-6b).
//
// Hai lớp:
//   - literal: giá trị bí mật biết trước của chính bản cài (secrets.json,
//     secrets/*, token Telegram giải mã được) — thay nguyên văn bằng "***";
//   - mẫu (regex): bí mật lạ lọt vào log (token bot Telegram, "Bearer …",
//     userinfo trong URL, password=…, khoá sk-…/AIza…).
//
// KHÔNG BAO GIỜ in literal ra log — Redactor chỉ giữ chúng trong bộ nhớ.
package redact

import (
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"sync"

	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/secretgen"
)

// Mask là chuỗi thay cho mọi chỗ bị che.
const Mask = "***"

// minLiteralLen: literal ngắn hơn bỏ qua (che "abc" sẽ phá nát log mà không
// bảo vệ gì — bí mật thật của genh dài ≥ 32 ký tự).
const minLiteralLen = 8

type pattern struct {
	re   *regexp.Regexp
	repl string
}

// patterns áp SAU literal, theo thứ tự (URL /bot<token>/ trước mẫu token trần).
var patterns = []pattern{
	// Token bot Telegram trong đường dẫn API: https://api.telegram.org/bot<token>/sendMessage.
	{regexp.MustCompile(`/bot\d{5,12}:[^/\s"']+`), "/bot" + Mask},
	// Token bot Telegram trần: <bot id>:<≥ 30 ký tự>.
	{regexp.MustCompile(`\b\d{5,12}:[A-Za-z0-9_-]{30,}`), Mask},
	// Authorization: Bearer <token>.
	{regexp.MustCompile(`(?i)\b(Bearer\s+)[^\s'",]+`), "${1}" + Mask},
	// scheme://user:pass@host — che cả user lẫn mật khẩu.
	{regexp.MustCompile(`\b([a-zA-Z][a-zA-Z0-9+.-]*://)[^/\s:@'"]+:[^/\s@'"]+@`), "${1}" + Mask + ":" + Mask + "@"},
	// khoá=giá trị · "khoá": "giá trị" · khoá: giá trị.
	{regexp.MustCompile(`(?i)\b(access_token|refresh_token|client_secret|api[_-]?key|apikey|password|passwd|secret|token)(['"]?\s*[=:]\s*['"]?)[^\s&'",;}]+`), "${1}${2}" + Mask},
	// Khoá API kiểu sk-… (OpenAI/Anthropic).
	{regexp.MustCompile(`\bsk-[A-Za-z0-9_-]{20,}`), Mask},
	// Khoá API Google.
	{regexp.MustCompile(`\bAIza[0-9A-Za-z_-]{30,}`), Mask},
}

// Redactor che literal + mẫu. An toàn khi dùng song song.
type Redactor struct {
	literals []string
	mu       sync.Mutex
	count    int
}

// New dựng Redactor từ các literal bí mật: bỏ chuỗi rỗng/ngắn (< 8 ký tự) và
// trùng lặp, sắp xếp DÀI TRƯỚC (literal chứa literal khác vẫn che trọn).
func New(literals ...string) *Redactor {
	seen := map[string]bool{}
	var out []string
	for _, l := range literals {
		l = strings.TrimSpace(l)
		if len(l) < minLiteralLen || seen[l] {
			continue
		}
		seen[l] = true
		out = append(out, l)
	}
	sort.SliceStable(out, func(i, j int) bool { return len(out[i]) > len(out[j]) })
	return &Redactor{literals: out}
}

// String trả s đã che (Redactor nil vẫn che theo mẫu).
func (r *Redactor) String(s string) string {
	n := 0
	if r != nil {
		for _, l := range r.literals {
			if c := strings.Count(s, l); c > 0 {
				n += c
				s = strings.ReplaceAll(s, l, Mask)
			}
		}
	}
	for _, p := range patterns {
		p := p
		s = p.re.ReplaceAllStringFunc(s, func(m string) string {
			repl := p.re.ReplaceAllString(m, p.repl)
			if repl != m {
				n++
			}
			return repl
		})
	}
	if n > 0 && r != nil {
		r.mu.Lock()
		r.count += n
		r.mu.Unlock()
	}
	return s
}

// Bytes như String cho []byte.
func (r *Redactor) Bytes(b []byte) []byte { return []byte(r.String(string(b))) }

// Count là tổng số chỗ đã che từ khi tạo (ghi vào manifest — không kèm giá trị).
func (r *Redactor) Count() int {
	if r == nil {
		return 0
	}
	r.mu.Lock()
	defer r.mu.Unlock()
	return r.count
}

// LiteralCount là số literal đang che (không lộ giá trị).
func (r *Redactor) LiteralCount() int {
	if r == nil {
		return 0
	}
	return len(r.literals)
}

// SecretFileNames là các Docker secret trong <thư mục compose>/../secrets mà
// genh che (gh_offsite_key — "Khoá khôi phục" — che cả dạng bỏ dấu '-').
var SecretFileNames = []string{"gh_master_key", "gh_bridge_key", "gh_browser_key", "gh_agy_key", "gh_offsite_key"}

// SecretsFromInstall gom literal bí mật của một bản cài: secretgen bundle
// (config/secrets.json — CHỈ ĐỌC, không sinh/ghi gì) + nội dung
// <thư mục chứa compose.yaml>/../secrets/{SecretFileNames} + extra (token
// Telegram đã giải mã — bên gọi tự giải bằng internal/notify; gói này là lá,
// không phụ thuộc notify để notify dùng được Redactor). Tệp thiếu/không đọc
// được bỏ qua.
func SecretsFromInstall(installDir, composePath string, extra ...string) []string {
	var out []string
	if b, err := secretgen.Load(filepath.Join(installDir, "config")); err == nil {
		out = append(out, b.MasterKey, b.DBPassword, b.AppDBPassword, b.BackupKey, b.SetupToken)
	}
	if composePath != "" {
		dir := filepath.Join(filepath.Dir(composePath), "..", "secrets")
		for _, name := range SecretFileNames {
			raw, err := readSmall(filepath.Join(dir, name))
			if err != nil {
				continue
			}
			v := strings.TrimSpace(string(raw))
			out = append(out, v)
			if strings.Contains(v, "-") {
				out = append(out, strings.ReplaceAll(v, "-", ""))
			}
		}
	}
	for _, e := range extra {
		out = append(out, strings.TrimSpace(e))
	}
	return out
}

// readSmall đọc tệp thường ≤ 64 KiB (không theo symlink ở thành phần cuối).
func readSmall(path string) ([]byte, error) {
	fi, err := os.Lstat(path)
	if err != nil {
		return nil, err
	}
	if !fi.Mode().IsRegular() || fi.Size() > 64<<10 {
		return nil, os.ErrInvalid
	}
	return os.ReadFile(path)
}
