package ops

import (
	"encoding/base64"
	"os"
	"path/filepath"
	"regexp"
	"runtime"
	"strings"
	"testing"
)

// Máy cài từ bản cũ (chỉ có gh_master_key + gh_bridge_key): mọi lệnh vận hành
// tìm compose.yaml phải tự sinh gh_browser_key (v0.1.29) — không ghi đè khoá cũ,
// không tạo secrets/ cạnh một compose.yaml lạ.
func TestEnsureAuxSecrets_FillsMissingBrowserKeyOnly(t *testing.T) {
	root := t.TempDir()
	deploy := filepath.Join(root, "deploy")
	secrets := filepath.Join(root, "secrets")
	if err := os.MkdirAll(deploy, 0o755); err != nil {
		t.Fatal(err)
	}
	composePath := filepath.Join(deploy, "compose.yaml")
	// Chưa có secrets/ → không làm gì.
	if err := ensureAuxSecrets(composePath); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(secrets); !os.IsNotExist(err) {
		t.Fatalf("không được tự tạo secrets/: %v", err)
	}
	if err := os.MkdirAll(secrets, 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(secrets, "gh_bridge_key"), []byte("khoa-bridge-cu"), 0o644); err != nil {
		t.Fatal(err)
	}
	if err := ensureAuxSecrets(composePath); err != nil {
		t.Fatal(err)
	}
	bridge, _ := os.ReadFile(filepath.Join(secrets, "gh_bridge_key"))
	if string(bridge) != "khoa-bridge-cu" {
		t.Fatalf("gh_bridge_key bị ghi đè: %q", bridge)
	}
	raw, err := os.ReadFile(filepath.Join(secrets, "gh_browser_key"))
	if err != nil {
		t.Fatalf("thiếu gh_browser_key: %v", err)
	}
	key, err := base64.StdEncoding.DecodeString(string(raw))
	if err != nil || len(key) != 32 {
		t.Fatalf("gh_browser_key phải là 32 byte base64, được %q (%v)", raw, err)
	}
	// Quyền 0644 của gh_browser_key: kiểm ở secrets_unix_test.go (bit quyền POSIX).
	// Lần hai: giữ nguyên.
	if err := ensureAuxSecrets(composePath); err != nil {
		t.Fatal(err)
	}
	again, _ := os.ReadFile(filepath.Join(secrets, "gh_browser_key"))
	if string(again) != string(raw) {
		t.Fatal("gh_browser_key phải giữ nguyên giữa hai lần gọi")
	}
}

// v0.1.40 (F-12): gh_offsite_key = "Khoá khôi phục" — 6 nhóm × 5 ký tự base32 HOA
// nối '-', không xuống dòng, quyền 0644; không bao giờ ghi đè khoá đã có.
func TestEnsureAuxSecrets_OffsiteKeyFormat(t *testing.T) {
	root := t.TempDir()
	secrets := filepath.Join(root, "secrets")
	if err := os.MkdirAll(filepath.Join(root, "deploy"), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.MkdirAll(secrets, 0o700); err != nil {
		t.Fatal(err)
	}
	composePath := filepath.Join(root, "deploy", "compose.yaml")
	if err := ensureAuxSecrets(composePath); err != nil {
		t.Fatal(err)
	}
	raw, err := os.ReadFile(filepath.Join(secrets, "gh_offsite_key"))
	if err != nil {
		t.Fatalf("thiếu gh_offsite_key: %v", err)
	}
	if !offsiteKeyRe.Match(raw) {
		t.Fatalf("gh_offsite_key sai định dạng 6×5 base32 có '-' (không xuống dòng): %q", raw)
	}
	if runtime.GOOS != "windows" {
		st, _ := os.Stat(filepath.Join(secrets, "gh_offsite_key"))
		if st.Mode().Perm() != 0o644 {
			t.Fatalf("quyền gh_offsite_key = %v, muốn 0644", st.Mode().Perm())
		}
	}
	// Không ghi đè.
	if err := os.WriteFile(filepath.Join(secrets, "gh_offsite_key"), []byte("AAAAA-BBBBB-CCCCC-DDDDD-EEEEE-FFFFF"), 0o644); err != nil {
		t.Fatal(err)
	}
	if err := ensureAuxSecrets(composePath); err != nil {
		t.Fatal(err)
	}
	if again, _ := os.ReadFile(filepath.Join(secrets, "gh_offsite_key")); string(again) != "AAAAA-BBBBB-CCCCC-DDDDD-EEEEE-FFFFF" {
		t.Fatalf("gh_offsite_key bị ghi đè: %q", again)
	}
	// Các khoá cũ vẫn là 32 byte base64.
	b, _ := os.ReadFile(filepath.Join(secrets, "gh_bridge_key"))
	if k, err := base64.StdEncoding.DecodeString(string(b)); err != nil || len(k) != 32 {
		t.Fatalf("gh_bridge_key phải giữ định dạng base64 32 byte: %q", b)
	}
}

var offsiteKeyRe = regexp.MustCompile(`^[A-Z2-7]{5}(-[A-Z2-7]{5}){5}$`)

func TestGenerateOffsiteKey_NgauNhienDungDinhDang(t *testing.T) {
	seen := map[string]bool{}
	for i := 0; i < 50; i++ {
		k, err := GenerateOffsiteKey()
		if err != nil || !offsiteKeyRe.MatchString(k) || len(k) < minBundlePasswordLen {
			t.Fatalf("GenerateOffsiteKey = %q, %v", k, err)
		}
		if seen[k] {
			t.Fatal("khoá trùng — bộ sinh không ngẫu nhiên")
		}
		seen[k] = true
	}
	if id := offsiteKeyID("AAAAA-BBBBB-CCCCC-DDDDD-EEEEE-FFFFF"); len(id) != 8 || strings.ToLower(id) != id {
		t.Fatalf("key_id phải 8 ký tự hex thường: %q", id)
	}
}

// LocatePath (mọi lệnh vận hành, kể cả `genh update` trước khi `up`) tự bổ sung khoá thiếu.
func TestLocatePath_EnsuresBrowserKey(t *testing.T) {
	root := t.TempDir()
	composePath := filepath.Join(root, "deploy", "compose.yaml")
	if err := os.MkdirAll(filepath.Join(root, "secrets"), 0o700); err != nil {
		t.Fatal(err)
	}
	env := &Env{InstallDir: root, locate: func(string) (string, error) { return composePath, nil }}
	if _, err := env.LocatePath(); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(filepath.Join(root, "secrets", "gh_browser_key")); err != nil {
		t.Fatalf("LocatePath phải sinh gh_browser_key: %v", err)
	}
}

// agyBoxInstall dựng cây <root>/{deploy,secrets} và trả (đường dẫn compose, thư mục secrets).
func agyBoxInstall(t *testing.T) (string, string) {
	t.Helper()
	root := t.TempDir()
	if err := os.MkdirAll(filepath.Join(root, "deploy"), 0o755); err != nil {
		t.Fatal(err)
	}
	secrets := filepath.Join(root, "secrets")
	if err := os.MkdirAll(secrets, 0o700); err != nil {
		t.Fatal(err)
	}
	return filepath.Join(root, "deploy", "compose.yaml"), secrets
}

// v0.1.63 (H3): máy cài từ v0.1.62 chưa có gh_agy_key (khoá HMAC api/worker ↔ agy-box) —
// genh update phải tự sinh 32 byte base64, KHÁC các khoá khác, và giữ nguyên các khoá cũ.
func TestEnsureAuxSecrets_SinhAgyKeyKhiThieu(t *testing.T) {
	composePath, secrets := agyBoxInstall(t)
	for name, v := range map[string]string{"gh_bridge_key": "cu-bridge", "gh_browser_key": "cu-browser"} {
		if err := os.WriteFile(filepath.Join(secrets, name), []byte(v), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	if err := ensureAuxSecrets(composePath); err != nil {
		t.Fatal(err)
	}
	raw, err := os.ReadFile(filepath.Join(secrets, "gh_agy_key"))
	if err != nil {
		t.Fatalf("thiếu gh_agy_key sau ensureAuxSecrets: %v", err)
	}
	key, err := base64.StdEncoding.DecodeString(string(raw))
	if err != nil || len(key) != 32 {
		t.Fatalf("gh_agy_key phải là 32 byte base64 (không xuống dòng), được %q (%v)", raw, err)
	}
	for name, want := range map[string]string{"gh_bridge_key": "cu-bridge", "gh_browser_key": "cu-browser"} {
		if got, _ := os.ReadFile(filepath.Join(secrets, name)); string(got) != want {
			t.Fatalf("%s bị ghi đè: %q", name, got)
		}
	}
	browser, _ := os.ReadFile(filepath.Join(secrets, "gh_browser_key"))
	if string(raw) == string(browser) {
		t.Fatal("gh_agy_key không được trùng khoá khác")
	}
}

// Chạy lại (nhiều lệnh vận hành liên tiếp) không đổi khoá đã có — box đang dùng khoá đó.
func TestEnsureAuxSecrets_AgyKeyChayLaiKhongDoi(t *testing.T) {
	composePath, secrets := agyBoxInstall(t)
	if err := ensureAuxSecrets(composePath); err != nil {
		t.Fatal(err)
	}
	first, _ := os.ReadFile(filepath.Join(secrets, "gh_agy_key"))
	for i := 0; i < 3; i++ {
		if err := ensureAuxSecrets(composePath); err != nil {
			t.Fatal(err)
		}
	}
	again, _ := os.ReadFile(filepath.Join(secrets, "gh_agy_key"))
	if len(first) == 0 || string(first) != string(again) {
		t.Fatalf("gh_agy_key phải giữ nguyên: %q → %q", first, again)
	}
	// Khoá có sẵn (kể cả định dạng lạ do Owner tự đặt) không bao giờ bị ghi đè.
	if err := os.WriteFile(filepath.Join(secrets, "gh_agy_key"), []byte("khoa-do-owner-dat"), 0o644); err != nil {
		t.Fatal(err)
	}
	if err := ensureAuxSecrets(composePath); err != nil {
		t.Fatal(err)
	}
	if got, _ := os.ReadFile(filepath.Join(secrets, "gh_agy_key")); string(got) != "khoa-do-owner-dat" {
		t.Fatalf("gh_agy_key có sẵn bị ghi đè: %q", got)
	}
}

// Migration an toàn khi lần chạy trước dở dang: tệp rỗng (ghi chưa xong) hoặc THƯ MỤC rỗng
// (Docker tự tạo khi bind-mount nguồn chưa có) đều không dùng được làm khoá ⇒ sinh lại.
// Thư mục có nội dung là thứ lạ của Owner ⇒ báo lỗi, không xoá.
func TestEnsureAuxSecrets_AgyKeyHong(t *testing.T) {
	t.Run("tệp rỗng", func(t *testing.T) {
		composePath, secrets := agyBoxInstall(t)
		if err := os.WriteFile(filepath.Join(secrets, "gh_agy_key"), []byte(" \n"), 0o644); err != nil {
			t.Fatal(err)
		}
		if err := ensureAuxSecrets(composePath); err != nil {
			t.Fatal(err)
		}
		raw, _ := os.ReadFile(filepath.Join(secrets, "gh_agy_key"))
		if key, err := base64.StdEncoding.DecodeString(string(raw)); err != nil || len(key) != 32 {
			t.Fatalf("tệp rỗng phải được sinh lại, được %q (%v)", raw, err)
		}
	})
	t.Run("thư mục rỗng do Docker tạo", func(t *testing.T) {
		composePath, secrets := agyBoxInstall(t)
		if err := os.Mkdir(filepath.Join(secrets, "gh_agy_key"), 0o755); err != nil {
			t.Fatal(err)
		}
		if err := ensureAuxSecrets(composePath); err != nil {
			t.Fatal(err)
		}
		st, err := os.Stat(filepath.Join(secrets, "gh_agy_key"))
		if err != nil || st.IsDir() || st.Size() == 0 {
			t.Fatalf("gh_agy_key phải là tệp có nội dung: %v %v", st, err)
		}
	})
	t.Run("thư mục có nội dung", func(t *testing.T) {
		composePath, secrets := agyBoxInstall(t)
		dir := filepath.Join(secrets, "gh_agy_key")
		if err := os.Mkdir(dir, 0o755); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(filepath.Join(dir, "x"), []byte("x"), 0o644); err != nil {
			t.Fatal(err)
		}
		if err := ensureAuxSecrets(composePath); err == nil {
			t.Fatal("thư mục có nội dung tại secrets/gh_agy_key phải báo lỗi")
		}
		if _, err := os.Stat(filepath.Join(dir, "x")); err != nil {
			t.Fatalf("không được xoá nội dung lạ: %v", err)
		}
	})
}

// Cả đường LocatePath (mọi lệnh vận hành, kể cả `genh update` trước `docker compose up`).
func TestLocatePath_EnsuresAgyKey(t *testing.T) {
	root := t.TempDir()
	composePath := filepath.Join(root, "deploy", "compose.yaml")
	if err := os.MkdirAll(filepath.Join(root, "secrets"), 0o700); err != nil {
		t.Fatal(err)
	}
	env := &Env{InstallDir: root, locate: func(string) (string, error) { return composePath, nil }}
	if _, err := env.LocatePath(); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(filepath.Join(root, "secrets", "gh_agy_key")); err != nil {
		t.Fatalf("LocatePath phải sinh gh_agy_key: %v", err)
	}
}
