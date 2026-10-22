package notify

import (
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/hostlink"
)

// VECTOR CỐ ĐỊNH (hợp đồng với apps/api — pytest giải cùng vector): token giả
// chỉ dùng cho test.
const (
	vectorKeyHex    = "00112233445566778899aabbccddeeff00112233445566778899aabbccddeeff"
	vectorBlobB64   = "R0gxAQEBAQEBAQEBAQEBcDa822qwcq3EjBvydVHl/Eae0cnW0+lWvy+7VUcXKoAnR65d/L2duDWg+nq0YHK+AgICAgICAgICAgIC4Apt1NK8X3nxp5PKAE3vJSvRllbVFT6QKTuFbBwOJ4S/iowHbEK8trUT3dCImyr9FL5k9CzCtEYg8CnafG0xPbjhaU8GEmin34k+9X0cd0uL5rEAfHt9yDbxIe8FrQ4="
	vectorPlaintext = `{"token":"123456789:AAFakeTokenForTestOnly_abcdefghijkl","chat_id":"987654321"}`
	vectorToken     = "123456789:AAFakeTokenForTestOnly_abcdefghijkl"
)

func TestOpenEnvelope_VectorCoDinh(t *testing.T) {
	mk, err := DecodeMasterKey(vectorKeyHex)
	if err != nil {
		t.Fatal(err)
	}
	blob, err := base64.StdEncoding.DecodeString(vectorBlobB64)
	if err != nil {
		t.Fatal(err)
	}
	pt, err := OpenEnvelope(mk, blob, []byte(TelegramAAD))
	if err != nil {
		t.Fatalf("vector cố định phải giải được: %v", err)
	}
	if string(pt) != vectorPlaintext {
		t.Fatalf("plaintext = %q", pt)
	}
	// Sai aad.
	if _, err := OpenEnvelope(mk, blob, []byte("telegram_token")); err == nil {
		t.Fatal("aad khác phải lỗi")
	}
	// Sửa 1 byte ở mỗi vùng (nonce_k, enc_dek, nonce_d, ct).
	for _, i := range []int{3, 20, 70, len(blob) - 1} {
		b := append([]byte(nil), blob...)
		b[i] ^= 0x01
		if _, err := OpenEnvelope(mk, b, []byte(TelegramAAD)); err == nil {
			t.Fatalf("sửa byte %d phải lỗi", i)
		}
	}
	// Sai magic / quá ngắn / sai khoá.
	if _, err := OpenEnvelope(mk, append([]byte("GH2"), blob[3:]...), []byte(TelegramAAD)); err == nil {
		t.Fatal("magic sai phải lỗi")
	}
	if _, err := OpenEnvelope(mk, blob[:50], []byte(TelegramAAD)); err == nil {
		t.Fatal("quá ngắn phải lỗi")
	}
	other := append([]byte(nil), mk...)
	other[0] ^= 0xff
	if _, err := OpenEnvelope(other, blob, []byte(TelegramAAD)); err == nil {
		t.Fatal("khoá khác phải lỗi")
	}
}

func TestDecodeMasterKey_HexVaBase64(t *testing.T) {
	want, _ := hex.DecodeString(vectorKeyHex)
	got, err := DecodeMasterKey("  " + vectorKeyHex + "\n")
	if err != nil || string(got) != string(want) {
		t.Fatalf("hex: %x, %v", got, err)
	}
	got, err = DecodeMasterKey(base64.StdEncoding.EncodeToString(want))
	if err != nil || string(got) != string(want) {
		t.Fatalf("base64: %x, %v", got, err)
	}
	for _, bad := range []string{"", "abc", base64.StdEncoding.EncodeToString([]byte("ngắn")), strings.Repeat("z", 64)} {
		if _, err := DecodeMasterKey(bad); err == nil {
			t.Errorf("%q phải lỗi", bad)
		}
	}
}

func TestSealOpen_KhuHoi(t *testing.T) {
	mk, _ := DecodeMasterKey(vectorKeyHex)
	blob, err := SealEnvelope(mk, []byte("xin chào"), []byte("a"))
	if err != nil {
		t.Fatal(err)
	}
	pt, err := OpenEnvelope(mk, blob, []byte("a"))
	if err != nil || string(pt) != "xin chào" {
		t.Fatalf("%q, %v", pt, err)
	}
}

// writeInstall dựng <root>/deploy/compose.yaml + <root>/secrets/gh_master_key + run/.
func writeInstall(t *testing.T, keyRaw string) (root, composePath string) {
	t.Helper()
	root = t.TempDir()
	composePath = filepath.Join(root, "deploy", "compose.yaml")
	_ = os.MkdirAll(filepath.Dir(composePath), 0o755)
	_ = os.WriteFile(composePath, []byte("name: gen-harness\n"), 0o644)
	_ = os.MkdirAll(filepath.Join(root, "secrets"), 0o700)
	_ = os.WriteFile(filepath.Join(root, "secrets", "gh_master_key"), []byte(keyRaw), 0o644)
	if err := hostlink.EnsureDir(root); err != nil {
		t.Fatal(err)
	}
	return root, composePath
}

func writeTelegramFile(t *testing.T, root string, v map[string]any) {
	t.Helper()
	b, _ := json.Marshal(v)
	if err := os.WriteFile(hostlink.TelegramConfigPath(root), b, 0o644); err != nil {
		t.Fatal(err)
	}
}

func TestLoadTelegramConfig(t *testing.T) {
	root, cp := writeInstall(t, vectorKeyHex+"\n")
	if _, err := LoadTelegramConfig(root, cp); !errors.Is(err, ErrNotConfigured) {
		t.Fatalf("không có tệp ⇒ ErrNotConfigured, được %v", err)
	}
	writeTelegramFile(t, root, map[string]any{"schema": 1, "enabled": false, "updated_at": "x"})
	if _, err := LoadTelegramConfig(root, cp); !errors.Is(err, ErrDisabled) || !errors.Is(err, ErrNotConfigured) {
		t.Fatalf("enabled=false ⇒ ErrDisabled (cũng là ErrNotConfigured), được %v", err)
	}
	writeTelegramFile(t, root, map[string]any{"schema": 1, "enabled": true, "enc": vectorBlobB64, "briefing": true, "reminders": false})
	cfg, err := LoadTelegramConfig(root, cp)
	if err != nil || cfg.Token != vectorToken || cfg.ChatID != "987654321" || !cfg.Briefing || cfg.Reminders {
		t.Fatalf("cfg = chat %q briefing %v, err %v", cfg.ChatID, cfg.Briefing, err)
	}
	// Khoá master khác (sau nhập gói) ⇒ ErrKeyMismatch, lỗi không chứa token.
	root2, cp2 := writeInstall(t, strings.Repeat("ab", 32))
	writeTelegramFile(t, root2, map[string]any{"schema": 1, "enabled": true, "enc": vectorBlobB64})
	_, err = LoadTelegramConfig(root2, cp2)
	if !errors.Is(err, ErrKeyMismatch) || strings.Contains(err.Error(), "AAFake") {
		t.Fatalf("khoá khác ⇒ ErrKeyMismatch, được %v", err)
	}
	// Schema lạ.
	writeTelegramFile(t, root, map[string]any{"schema": 2, "enabled": true, "enc": vectorBlobB64})
	if _, err := LoadTelegramConfig(root, cp); err == nil || errors.Is(err, ErrNotConfigured) {
		t.Fatalf("schema 2 phải lỗi riêng, được %v", err)
	}
}
