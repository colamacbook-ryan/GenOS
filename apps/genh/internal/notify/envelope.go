// Package notify là phía genh của "Báo động & bản tin" (v0.1.44, F-6b): giải
// cấu hình Telegram do api ghi vào run/telegram.json (token MÃ HOÁ phong bì GH1
// bằng khoá master — y hệt apps/api/gh/crypto.py) và gửi tin văn bản thường qua
// Bot API. Trực canh máy chủ (internal/ops/watchdog.go) là người dùng duy nhất.
package notify

import (
	"crypto/aes"
	"crypto/cipher"
	"crypto/rand"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"

	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/hostlink"
)

// TelegramAAD là associated data khi api mã hoá plaintext {token, chat_id}
// (gh.crypto.encrypt(..., associated=b"telegram_notify")) — hợp đồng, giữ đúng chữ.
const TelegramAAD = "telegram_notify"

// envelopeMagic + độ dài các phần của định dạng GH1:
// b"GH1" | nonce_k(12) | enc_dek(48) | nonce_d(12) | ct.
const (
	envelopeMagic = "GH1"
	nonceLen      = 12
	encDEKLen     = 48 // dek 32 byte + tag 16 byte
	headerLen     = len(envelopeMagic) + nonceLen + encDEKLen + nonceLen
)

var (
	// ErrNotConfigured: chưa cấu hình (không có run/telegram.json) hoặc đã tắt.
	ErrNotConfigured = errors.New("chưa cấu hình Telegram")
	// ErrDisabled: có cấu hình nhưng enabled=false (errors.Is(err, ErrNotConfigured) cũng đúng).
	ErrDisabled = fmt.Errorf("%w (đã tắt)", ErrNotConfigured)
	// ErrKeyMismatch: không giải được token bằng khoá master hiện tại (khoá đổi
	// sau nhập gói/khôi phục mà api chưa đồng bộ lại tệp, hoặc tệp bị sửa).
	ErrKeyMismatch = errors.New("không giải mã được cấu hình Telegram bằng khoá master hiện tại")
)

// DecodeMasterKey giải khoá 32 byte dạng hex (64 ký tự — genh sinh) hoặc
// base64 (make secrets cho dev) — y hệt apps/api/gh/crypto.py::decode_key.
func DecodeMasterKey(raw string) ([]byte, error) {
	raw = strings.TrimSpace(raw)
	if len(raw) == 64 {
		if k, err := hex.DecodeString(raw); err == nil {
			return k, nil
		}
	}
	k, err := base64.StdEncoding.DecodeString(raw)
	if err != nil || len(k) != 32 {
		return nil, errors.New("khoá phải là 32 byte dạng hex (64 ký tự) hoặc base64")
	}
	return k, nil
}

// OpenEnvelope giải phong bì GH1: dek = AES-256-GCM(mk).Open(nonce_k, enc_dek,
// aad="dek"); pt = AES-256-GCM(dek).Open(nonce_d, ct, aad).
func OpenEnvelope(mk, blob, aad []byte) ([]byte, error) {
	if len(blob) < headerLen+16 || string(blob[:3]) != envelopeMagic {
		return nil, errors.New("định dạng bí mật không hợp lệ")
	}
	nonceK := blob[3 : 3+nonceLen]
	encDEK := blob[3+nonceLen : 3+nonceLen+encDEKLen]
	nonceD := blob[3+nonceLen+encDEKLen : headerLen]
	ct := blob[headerLen:]
	outer, err := newGCM(mk)
	if err != nil {
		return nil, err
	}
	dek, err := outer.Open(nil, nonceK, encDEK, []byte("dek"))
	if err != nil {
		return nil, errors.New("giải khoá dữ liệu thất bại")
	}
	inner, err := newGCM(dek)
	if err != nil {
		return nil, err
	}
	pt, err := inner.Open(nil, nonceD, ct, aad)
	if err != nil {
		return nil, errors.New("giải nội dung thất bại")
	}
	return pt, nil
}

// SealEnvelope là chiều ngược của OpenEnvelope (như gh.crypto.encrypt) — genh
// không tự ghi run/telegram.json (api ghi); hàm này cho test dựng tệp giả.
func SealEnvelope(mk, plaintext, aad []byte) ([]byte, error) {
	dek := make([]byte, 32)
	nonceK := make([]byte, nonceLen)
	nonceD := make([]byte, nonceLen)
	for _, b := range [][]byte{dek, nonceK, nonceD} {
		if _, err := rand.Read(b); err != nil {
			return nil, err
		}
	}
	outer, err := newGCM(mk)
	if err != nil {
		return nil, err
	}
	inner, err := newGCM(dek)
	if err != nil {
		return nil, err
	}
	out := append([]byte(envelopeMagic), nonceK...)
	out = append(out, outer.Seal(nil, nonceK, dek, []byte("dek"))...)
	out = append(out, nonceD...)
	return append(out, inner.Seal(nil, nonceD, plaintext, aad)...), nil
}

func newGCM(key []byte) (cipher.AEAD, error) {
	if len(key) != 32 {
		return nil, errors.New("khoá AES-256 phải 32 byte")
	}
	block, err := aes.NewCipher(key)
	if err != nil {
		return nil, err
	}
	return cipher.NewGCM(block)
}

// Config là cấu hình Telegram đã giải mã — Token là BÍ MẬT, không bao giờ in.
type Config struct {
	Token     string
	ChatID    string
	Briefing  bool
	Reminders bool
}

// telegramFile là nội dung run/telegram.json (api ghi — KHÔNG tin cậy).
type telegramFile struct {
	Schema    int    `json:"schema"`
	Enabled   bool   `json:"enabled"`
	Enc       string `json:"enc"`
	Briefing  bool   `json:"briefing"`
	Reminders bool   `json:"reminders"`
	UpdatedAt string `json:"updated_at"`
}

// MasterKeyPath là <thư mục chứa compose.yaml>/../secrets/gh_master_key.
func MasterKeyPath(composePath string) string {
	return filepath.Join(filepath.Dir(composePath), "..", "secrets", "gh_master_key")
}

// LoadTelegramConfig đọc run/telegram.json AN TOÀN (hostlink — không theo
// symlink, ≤ 64 KiB), schema=1. Không có tệp / enabled=false ⇒ ErrNotConfigured
// (ErrDisabled); giải mã lỗi (khoá không khớp, tệp bị sửa) ⇒ ErrKeyMismatch.
// Lỗi KHÔNG BAO GIỜ chứa token.
func LoadTelegramConfig(installDir, composePath string) (Config, error) {
	raw, err := hostlink.ReadTelegramConfigFile(installDir)
	if err != nil {
		if errors.Is(err, os.ErrNotExist) {
			return Config{}, ErrNotConfigured
		}
		return Config{}, fmt.Errorf("đọc run/telegram.json: %w", err)
	}
	var f telegramFile
	if err := json.Unmarshal(raw, &f); err != nil {
		return Config{}, errors.New("run/telegram.json hỏng (không phải JSON)")
	}
	if f.Schema != 1 {
		return Config{}, fmt.Errorf("run/telegram.json: schema %d không hỗ trợ", f.Schema)
	}
	if !f.Enabled {
		return Config{}, ErrDisabled
	}
	if f.Enc == "" {
		return Config{}, ErrNotConfigured
	}
	keyRaw, err := os.ReadFile(MasterKeyPath(composePath))
	if err != nil {
		return Config{}, fmt.Errorf("%w: không đọc được secrets/gh_master_key", ErrKeyMismatch)
	}
	mk, err := DecodeMasterKey(string(keyRaw))
	if err != nil {
		return Config{}, fmt.Errorf("%w: secrets/gh_master_key sai định dạng", ErrKeyMismatch)
	}
	blob, err := base64.StdEncoding.DecodeString(strings.TrimSpace(f.Enc))
	if err != nil {
		return Config{}, fmt.Errorf("%w: enc không phải base64", ErrKeyMismatch)
	}
	pt, err := OpenEnvelope(mk, blob, []byte(TelegramAAD))
	if err != nil {
		return Config{}, ErrKeyMismatch
	}
	var p struct {
		Token  string `json:"token"`
		ChatID string `json:"chat_id"`
	}
	if err := json.Unmarshal(pt, &p); err != nil || p.Token == "" || p.ChatID == "" {
		return Config{}, fmt.Errorf("%w: nội dung giải mã thiếu token/chat_id", ErrKeyMismatch)
	}
	return Config{Token: p.Token, ChatID: p.ChatID, Briefing: f.Briefing, Reminders: f.Reminders}, nil
}
