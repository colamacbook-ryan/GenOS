package access

import (
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
)

func composeIn(t *testing.T) string {
	t.Helper()
	return filepath.Join(t.TempDir(), "compose.yaml")
}

func readEnv(t *testing.T, cp string) string {
	t.Helper()
	b, err := os.ReadFile(EnvPath(cp))
	if err != nil {
		t.Fatalf("đọc .env: %v", err)
	}
	return string(b)
}

func TestEnsure_FreshAndLegacy(t *testing.T) {
	cp := composeIn(t)
	st, changed, err := Ensure(cp, true)
	if err != nil || !changed {
		t.Fatalf("Ensure fresh: %v changed=%v", err, changed)
	}
	if st.BindAddr != "127.0.0.1" || st.Mode != ModeLocal {
		t.Errorf("fresh = %+v", st)
	}
	body := readEnv(t, cp)
	if !strings.HasPrefix(body, HeaderComment+"\n") || !strings.Contains(body, "GH_BIND_ADDR=127.0.0.1\n") || !strings.Contains(body, "GH_ACCESS_MODE=local\n") {
		t.Errorf("nội dung .env: %q", body)
	}

	cp2 := composeIn(t)
	st, changed, err = Ensure(cp2, false)
	if err != nil || !changed || st.BindAddr != "0.0.0.0" || st.Mode != ModeLANLegacy {
		t.Fatalf("legacy = %+v changed=%v err=%v", st, changed, err)
	}
	if !strings.Contains(readEnv(t, cp2), "GH_BIND_ADDR=0.0.0.0\n") {
		t.Error("thiếu GH_BIND_ADDR=0.0.0.0")
	}
	// Windows không có bit quyền POSIX (file ghi được báo 0666).
	if runtime.GOOS != "windows" {
		if fi, _ := os.Stat(EnvPath(cp2)); fi.Mode().Perm() != 0o644 {
			t.Errorf("quyền = %v, muốn 0644", fi.Mode().Perm())
		}
	}
}

func TestEnsure_KeepsOwnerLinesAndIdempotent(t *testing.T) {
	cp := composeIn(t)
	orig := "# của Owner\nFOO=bar\nexport BAZ=\"x y\"\n\nGH_PG_WORK_MEM=8MB\n"
	if err := os.WriteFile(EnvPath(cp), []byte(orig), 0o600); err != nil {
		t.Fatal(err)
	}
	if _, changed, err := Ensure(cp, false); err != nil || !changed {
		t.Fatalf("Ensure: %v %v", err, changed)
	}
	body := readEnv(t, cp)
	for _, l := range []string{"# của Owner", "FOO=bar", "export BAZ=\"x y\"", "GH_PG_WORK_MEM=8MB"} {
		if !strings.Contains(body, l+"\n") {
			t.Errorf("mất dòng %q:\n%s", l, body)
		}
	}
	if strings.Index(body, "FOO=bar") > strings.Index(body, "GH_PG_WORK_MEM") {
		t.Error("đổi thứ tự dòng của Owner")
	}
	again, changed, err := Ensure(cp, true)
	if err != nil || changed || again.Mode != ModeLANLegacy {
		t.Errorf("lần 2: %+v changed=%v err=%v", again, changed, err)
	}
	if readEnv(t, cp) != body {
		t.Error("lần 2 đổi nội dung")
	}
}

func TestEnsureFresh_FixesLegacy(t *testing.T) {
	cp := composeIn(t)
	if _, _, err := Ensure(cp, false); err != nil {
		t.Fatal(err)
	}
	st, changed, err := EnsureFresh(cp)
	if err != nil || !changed || st.Mode != ModeLocal || st.BindAddr != "127.0.0.1" {
		t.Fatalf("EnsureFresh = %+v %v %v", st, changed, err)
	}
	got, _ := Read(cp)
	if got.Mode != ModeLocal || got.BindAddr != "127.0.0.1" {
		t.Errorf("đọc lại = %+v", got)
	}
	// Đã chọn LAN thật thì EnsureFresh không đụng.
	if err := Write(cp, State{Mode: ModeLAN, BindAddr: BindAll, SiteAddress: "gh.lan", PublicURL: "https://gh.lan:8443"}); err != nil {
		t.Fatal(err)
	}
	st, changed, _ = EnsureFresh(cp)
	if changed || st.Mode != ModeLAN {
		t.Errorf("EnsureFresh đổi lựa chọn LAN: %+v %v", st, changed)
	}
}

func TestWrite_ReplacesInPlaceAndDropsSite(t *testing.T) {
	cp := composeIn(t)
	_ = Write(cp, State{Mode: ModeTailscale, BindAddr: BindLocal, SiteAddress: "a.ts.net", PublicURL: "https://a.ts.net"})
	if err := Write(cp, State{Mode: ModeLocal, BindAddr: BindLocal, PublicURL: "https://localhost:8443"}); err != nil {
		t.Fatal(err)
	}
	body := readEnv(t, cp)
	if strings.Contains(body, "GH_SITE_ADDRESS=") || strings.Count(body, "GH_ACCESS_MODE=") != 1 || strings.Count(body, "genh quản lý") != 1 {
		t.Errorf("body:\n%s", body)
	}
}

func TestWrite_RejectsInvalid(t *testing.T) {
	cp := composeIn(t)
	if err := Write(cp, State{Mode: ModeLAN, BindAddr: BindAll, SiteAddress: "a b", PublicURL: "https://a b:8443"}); err == nil {
		t.Error("phải từ chối site có khoảng trắng")
	}
	if err := Write(cp, State{Mode: "x", BindAddr: BindAll}); err == nil {
		t.Error("phải từ chối mode lạ")
	}
	if _, err := os.Stat(EnvPath(cp)); err == nil {
		t.Error("không được tạo .env khi lỗi")
	}
}

func TestValidateSiteAddress(t *testing.T) {
	for _, s := range []string{"a b", "a,b", "x{", "x}", "https://x", "x:1", "localhost", "LocalHost", "127.0.0.1", "127.1.2.3", "0.0.0.0", "a\nb", "a\n", "", "300.1.1.1", "1.2.3", "-a.com", "a.com }\nreverse_proxy x"} {
		if ValidateSiteAddress(s) == nil {
			t.Errorf("phải từ chối %q", s)
		}
	}
	for _, s := range []string{"gen-harness.tail1234.ts.net", "192.168.1.20", "gh.lan", "box"} {
		if err := ValidateSiteAddress(s); err != nil {
			t.Errorf("phải nhận %q: %v", s, err)
		}
	}
}

func TestPublicURL(t *testing.T) {
	cases := []struct {
		m    Mode
		s    string
		p    int
		want string
	}{
		{ModeTailscale, "a.ts.net", 8443, "https://a.ts.net"},
		{ModeCloudflare, "gh.example.com", 8443, "https://gh.example.com"},
		{ModeLAN, "192.168.1.5", 9443, "https://192.168.1.5:9443"},
		{ModeLocal, "", 8443, "https://localhost:8443"},
		{ModeLANLegacy, "", 0, "https://localhost:8443"},
	}
	for _, c := range cases {
		if got := PublicURL(c.m, c.s, c.p); got != c.want {
			t.Errorf("PublicURL(%s)=%q muốn %q", c.m, got, c.want)
		}
	}
}

// Máy cài từ trước v0.1.46 mà Owner đã tự đặt GH_SITE_ADDRESS/GH_PUBLIC_URL trong .env: nâng cấp (Ensure
// fresh=false, chạy ở mọi lệnh genh kể cả tự cập nhật 03:00) không được xoá/ghi đè hai dòng đó.
func TestEnsure_UpgradeKeepsOwnerSiteAndPublicURL(t *testing.T) {
	cp := composeIn(t)
	orig := "FOO=bar\nGH_SITE_ADDRESS=gh.local\nGH_PUBLIC_URL=https://gh.local:8443\n"
	if err := os.WriteFile(EnvPath(cp), []byte(orig), 0o600); err != nil {
		t.Fatal(err)
	}
	st, changed, err := Ensure(cp, false)
	if err != nil || !changed {
		t.Fatalf("Ensure: %v %v", err, changed)
	}
	if st.Mode != ModeLAN || st.BindAddr != BindAll || st.SiteAddress != "gh.local" || st.PublicURL != "https://gh.local:8443" {
		t.Errorf("state = %+v", st)
	}
	body := readEnv(t, cp)
	for _, w := range []string{"FOO=bar\n", "GH_SITE_ADDRESS=gh.local\n", "GH_PUBLIC_URL=https://gh.local:8443\n", "GH_BIND_ADDR=0.0.0.0\n", "GH_ACCESS_MODE=lan\n"} {
		if !strings.Contains(body, w) {
			t.Errorf(".env thiếu %q:\n%s", w, body)
		}
	}
	if strings.Contains(body, "localhost") {
		t.Errorf(".env bị ghi đè về localhost:\n%s", body)
	}

	// Chỉ có GH_PUBLIC_URL (không site) → giữ lan_legacy nhưng GIỮ địa chỉ Owner đặt.
	cp2 := composeIn(t)
	if err := os.WriteFile(EnvPath(cp2), []byte("GH_PUBLIC_URL=https://gen.congty.vn\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	st, _, err = Ensure(cp2, false)
	if err != nil || st.Mode != ModeLANLegacy || st.PublicURL != "https://gen.congty.vn" {
		t.Errorf("chỉ có URL: %+v %v", st, err)
	}

	// Site không hợp lệ (chèn chỉ thị Caddy) → bỏ, về lan_legacy như cũ, không lỗi.
	cp3 := composeIn(t)
	if err := os.WriteFile(EnvPath(cp3), []byte("GH_SITE_ADDRESS=a.vn, evil.vn\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	st, _, err = Ensure(cp3, false)
	if err != nil || st.Mode != ModeLANLegacy || st.SiteAddress != "" || strings.Contains(readEnv(t, cp3), "evil") {
		t.Errorf("site xấu: %+v %v\n%s", st, err, readEnv(t, cp3))
	}
}
