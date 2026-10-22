package redact

import (
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/secretgen"
)

func TestRedactor_LiteralVaMau(t *testing.T) {
	r := New("", "short", "supersecretvalue123", "supersecretvalue123456")
	cases := []struct{ in, mustNot, want string }{
		{"pw=supersecretvalue123456 end", "supersecretvalue", "pw=*** end"},
		{"x supersecretvalue123 y", "supersecretvalue123", "x *** y"},
		{"short không che", "", "short không che"},
		{"POST https://api.telegram.org/bot123456789:AAFakeTokenForTestOnly_abcdefghijkl/sendMessage", "AAFake", "POST https://api.telegram.org/bot***/sendMessage"},
		{"token 123456789:AAFakeTokenForTestOnly_abcdefghijkl here", "AAFake", "token *** here"},
		{"Authorization: Bearer abc.def-ghi", "abc.def", "Authorization: Bearer ***"},
		{"dsn postgresql://gh:pw@db:5432/gh", "gh:pw@", "dsn postgresql://***:***@db:5432/gh"},
		{"password=hunter2&x=1", "hunter2", "password=***&x=1"},
		{`{"api_key": "abc123xyz"}`, "abc123xyz", `{"api_key": "***"}`},
		{"secret: zzzz", "zzzz", "secret: ***"},
		{"key sk-ant-REDACTEDREDACTEDREDACTED00 ok", "sk-ant", "key *** ok"},
		{"g AIzaSyA1234567890abcdefghijklmnopqrstu x", "AIza", "g *** x"},
		{"Tokens: 5 · secrets/ thư mục", "", "Tokens: 5 · secrets/ thư mục"},
	}
	for _, c := range cases {
		got := r.String(c.in)
		if got != c.want {
			t.Errorf("String(%q) = %q, muốn %q", c.in, got, c.want)
		}
		if c.mustNot != "" && strings.Contains(got, c.mustNot) {
			t.Errorf("String(%q) còn %q", c.in, c.mustNot)
		}
	}
	if r.Count() == 0 {
		t.Fatal("Count phải > 0")
	}
	if r.LiteralCount() != 2 {
		t.Fatalf("LiteralCount = %d, muốn 2 (bỏ rỗng/ngắn)", r.LiteralCount())
	}
	if string(r.Bytes([]byte("a supersecretvalue123 b"))) != "a *** b" {
		t.Fatal("Bytes không che")
	}
	var nilR *Redactor
	if got := nilR.String("Bearer x"); got != "Bearer ***" {
		t.Fatalf("Redactor nil vẫn phải che mẫu: %q", got)
	}
}

func TestSecretsFromInstall(t *testing.T) {
	dir := t.TempDir()
	res, err := secretgen.Ensure(filepath.Join(dir, "config"))
	if err != nil {
		t.Fatal(err)
	}
	if err := os.MkdirAll(filepath.Join(dir, "deploy"), 0o755); err != nil {
		t.Fatal(err)
	}
	sec := filepath.Join(dir, "secrets")
	if err := os.MkdirAll(sec, 0o700); err != nil {
		t.Fatal(err)
	}
	_ = os.WriteFile(filepath.Join(sec, "gh_bridge_key"), []byte("bridgekeyvalue-0001\n"), 0o644)
	_ = os.WriteFile(filepath.Join(sec, "gh_offsite_key"), []byte("ABCDE-FGHIJ-KLMNO-PQRST-UVWXY-Z2345"), 0o644)
	_ = os.WriteFile(filepath.Join(sec, "gh_agy_key"), []byte("agyboxkeyvalue-0002\n"), 0o644)
	// Symlink bị bỏ qua (không đọc theo).
	_ = os.Symlink("/etc/hostname", filepath.Join(sec, "gh_browser_key"))
	got := SecretsFromInstall(dir, filepath.Join(dir, "deploy", "compose.yaml"), "123456789:AAFakeTokenForTestOnly_abcdefghijkl")
	joined := strings.Join(got, "\n")
	for _, want := range []string{res.Bundle.MasterKey, res.Bundle.DBPassword, res.Bundle.AppDBPassword, res.Bundle.BackupKey, res.Bundle.SetupToken,
		"bridgekeyvalue-0001", "agyboxkeyvalue-0002", "ABCDE-FGHIJ-KLMNO-PQRST-UVWXY-Z2345", "ABCDEFGHIJKLMNOPQRSTUVWXYZ2345", "123456789:AAFakeTokenForTestOnly_abcdefghijkl"} {
		if !strings.Contains(joined, want) {
			t.Errorf("thiếu literal (độ dài %d)", len(want))
		}
	}
	r := New(got...)
	in := "x " + res.Bundle.DBPassword + " ABCDEFGHIJKLMNOPQRSTUVWXYZ2345 y"
	if out := r.String(in); strings.Contains(out, res.Bundle.DBPassword) || strings.Contains(out, "ABCDEFGHIJ") {
		t.Fatal("còn bí mật sau khi che")
	}
}
