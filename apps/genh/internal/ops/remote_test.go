package ops

import (
	"context"
	"errors"
	"net"
	"os"
	"strings"
	"testing"
	"time"

	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/access"
	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/dockercli"
	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/dockercli/fake"
	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/hostlink"
)

func remoteEnv(t *testing.T) (*Env, string) {
	t.Helper()
	composePath := testComposePath(t, updateTestComposeYAML)
	env := testEnv(t, composePath)
	env.Port = 8443
	if err := hostlink.EnsureDir(env.InstallDir); err != nil {
		t.Fatal(err)
	}
	return env, composePath
}

func remoteDeps(t *testing.T, fr *fake.Runner, healthy bool) RemoteDeps {
	t.Helper()
	srv, _ := listenReadyServer(t, healthy)
	return RemoteDeps{
		Runner: fr, Client: srv.Client(), ReadyURL: srv.URL + readyPath,
		Timeout: 100 * time.Millisecond, PollEvery: 5 * time.Millisecond,
		GOOS: "linux",
	}
}

func cmdNamed(name string) func(dockercli.Cmd) bool {
	return func(c dockercli.Cmd) bool { return c.Name == name }
}

func tsStatusJSON(state, dns string, certs ...string) []byte {
	cs := `[]`
	if len(certs) > 0 {
		cs = `["` + strings.Join(certs, `","`) + `"]`
	}
	return []byte(`{"BackendState":"` + state + `","Self":{"DNSName":"` + dns + `"},"CertDomains":` + cs + `}`)
}

func envFile(t *testing.T, composePath string) access.State {
	t.Helper()
	st, err := access.Read(composePath)
	if err != nil {
		t.Fatal(err)
	}
	return st
}

func TestRemoteLAN_WritesEnvThenUpThenReady(t *testing.T) {
	env, cp := remoteEnv(t)
	fr := &fake.Runner{Responses: []fake.Response{{Match: matchUp, Output: []byte("")}}}
	var out strings.Builder
	err := RunRemote(context.Background(), env, RemoteOptions{Action: "lan", Name: "gh.lan", Yes: true}, remoteDeps(t, fr, true), &out)
	if err != nil {
		t.Fatalf("RunRemote: %v\n%s", err, out.String())
	}
	st := envFile(t, cp)
	if st.Mode != access.ModeLAN || st.BindAddr != "0.0.0.0" || st.SiteAddress != "gh.lan" || st.PublicURL != "https://gh.lan:8443" {
		t.Errorf("state = %+v", st)
	}
	if callIndex(fr, matchUp) < 0 {
		t.Error("phải gọi compose up")
	}
	for _, want := range []string{"CẢNH BÁO", "Chứng chỉ CA", "caddy-root.crt", "iPhone", "Android"} {
		if !strings.Contains(out.String(), want) {
			t.Errorf("thiếu %q trong output", want)
		}
	}
	ns, err := hostlink.ReadNetworkStatus(env.InstallDir)
	if err != nil || ns.Schema != 1 || ns.Mode != "lan" || ns.BindAddr != "0.0.0.0" || ns.SiteAddress != "gh.lan" || ns.PublicURL != "https://gh.lan:8443" || ns.Port != 8443 || ns.CheckedAt == "" {
		t.Errorf("network-status.json = %+v (%v)", ns, err)
	}
}

func TestRemoteLAN_NoYesNoTTY_ExitsTwoAndChangesNothing(t *testing.T) {
	env, cp := remoteEnv(t)
	fr := &fake.Runner{}
	err := RunRemote(context.Background(), env, RemoteOptions{Action: "lan", Name: "gh.lan"}, remoteDeps(t, fr, true), &strings.Builder{})
	if err == nil || RemoteExitCode(err) != 2 {
		t.Fatalf("muốn lỗi thoát 2, được %v (mã %d)", err, RemoteExitCode(err))
	}
	if st := envFile(t, cp); st.Mode == access.ModeLAN {
		t.Errorf(".env bị đổi: %+v", st)
	}
	if len(fr.Calls) != 0 {
		t.Errorf("không được chạy lệnh nào: %+v", fr.Calls)
	}
}

func TestRemoteLAN_RejectsInjectedName(t *testing.T) {
	env, cp := remoteEnv(t)
	fr := &fake.Runner{}
	err := RunRemote(context.Background(), env, RemoteOptions{Action: "lan", Name: "x\nGH_EVIL=1", Yes: true}, remoteDeps(t, fr, true), &strings.Builder{})
	if err == nil {
		t.Fatal("phải từ chối tên có xuống dòng")
	}
	if st := envFile(t, cp); st.Mode == access.ModeLAN {
		t.Error(".env bị đổi")
	}
}

func TestRemoteLAN_FirewallHint(t *testing.T) {
	env, _ := remoteEnv(t)
	fr := &fake.Runner{Responses: []fake.Response{
		{Match: matchUp, Output: []byte("")},
		{Match: exactArgs("--state"), Output: []byte("running\n")},
		{Match: exactArgs("--query-port=8443/tcp"), Output: []byte("no\n")},
	}}
	var out strings.Builder
	d := remoteDeps(t, fr, true)
	d.LANAddr = func() (string, error) { return "192.168.1.20", nil }
	if err := RunRemote(context.Background(), env, RemoteOptions{Action: "lan", Yes: true}, d, &out); err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(out.String(), "https://192.168.1.20:8443") || !strings.Contains(out.String(), "sudo firewall-cmd --add-port=8443/tcp --permanent && sudo firewall-cmd --reload") {
		t.Errorf("output: %s", out.String())
	}
}

func TestRemoteTailscale_HappyPath(t *testing.T) {
	env, cp := remoteEnv(t)
	fr := &fake.Runner{Responses: []fake.Response{
		{Match: exactArgs("status", "--json"), Output: tsStatusJSON("Running", "gen.tail1234.ts.net.", "gen.tail1234.ts.net")},
		{Match: cmdNamed("tailscale"), Output: []byte("")},
		{Match: matchUp, Output: []byte("")},
	}}
	var out strings.Builder
	if err := RunRemote(context.Background(), env, RemoteOptions{Action: "tailscale", Yes: true}, remoteDeps(t, fr, true), &out); err != nil {
		t.Fatalf("RunRemote: %v", err)
	}
	st := envFile(t, cp)
	if st.Mode != access.ModeTailscale || st.BindAddr != "127.0.0.1" || st.SiteAddress != "gen.tail1234.ts.net" || st.PublicURL != "https://gen.tail1234.ts.net" {
		t.Errorf("state = %+v", st)
	}
	si := callIndex(fr, exactArgs("serve", "--bg", "--https=443", "https+insecure://localhost:8443"))
	if si < 0 {
		t.Fatalf("thiếu lệnh tailscale serve đúng tham số: %+v", fr.Calls)
	}
	if fr.Calls[si].Cmd.Name != "tailscale" || si > callIndex(fr, matchUp) {
		t.Error("serve phải là lệnh tailscale và chạy trước up")
	}
	if !strings.Contains(out.String(), "Mở trên điện thoại: https://gen.tail1234.ts.net") {
		t.Errorf("output: %s", out.String())
	}
}

func TestRemoteTailscale_MissingBinary_NoEnvChange(t *testing.T) {
	env, cp := remoteEnv(t)
	fr := &fake.Runner{Responses: []fake.Response{{Match: cmdNamed("tailscale"), Err: dockercli.ErrNotFound}}}
	err := RunRemote(context.Background(), env, RemoteOptions{Action: "tailscale"}, remoteDeps(t, fr, true), &strings.Builder{})
	oe := asOpError(t, err)
	if oe.Code != ErrCodeRemoteTailscale || !strings.Contains(oe.Next, "sudo dnf install tailscale") || !strings.Contains(oe.Next, "sudo tailscale up") {
		t.Errorf("OpError = %+v", oe)
	}
	if st := envFile(t, cp); st.Mode == access.ModeTailscale {
		t.Error(".env bị đổi")
	}
}

func TestRemoteTailscale_NoCertDomains(t *testing.T) {
	env, cp := remoteEnv(t)
	fr := &fake.Runner{Responses: []fake.Response{{Match: cmdNamed("tailscale"), Output: tsStatusJSON("Running", "gen.tail1234.ts.net.")}}}
	err := RunRemote(context.Background(), env, RemoteOptions{Action: "tailscale"}, remoteDeps(t, fr, true), &strings.Builder{})
	oe := asOpError(t, err)
	if !strings.Contains(oe.Next, "HTTPS Certificates") || !strings.Contains(oe.Next, "MagicDNS") {
		t.Errorf("Next = %q", oe.Next)
	}
	if callIndex(fr, exactArgs("serve")) >= 0 || envFile(t, cp).Mode == access.ModeTailscale {
		t.Error("không được serve/đổi .env")
	}
}

func TestRemoteTailscale_NotRunningAndPermission(t *testing.T) {
	env, cp := remoteEnv(t)
	fr := &fake.Runner{Responses: []fake.Response{{Match: cmdNamed("tailscale"), Output: tsStatusJSON("Stopped", "")}}}
	if err := RunRemote(context.Background(), env, RemoteOptions{Action: "tailscale"}, remoteDeps(t, fr, true), &strings.Builder{}); err == nil {
		t.Fatal("BackendState != Running phải lỗi")
	}

	fr = &fake.Runner{Responses: []fake.Response{
		{Match: exactArgs("status", "--json"), Output: tsStatusJSON("Running", "gen.ts.net.", "gen.ts.net")},
		{Match: exactArgs("serve"), Err: errors.New("Access denied: serve config denied")},
	}}
	err := RunRemote(context.Background(), env, RemoteOptions{Action: "tailscale"}, remoteDeps(t, fr, true), &strings.Builder{})
	oe := asOpError(t, err)
	if !strings.Contains(oe.Next, "sudo tailscale set --operator=$USER") {
		t.Errorf("Next = %q", oe.Next)
	}
	if envFile(t, cp).Mode == access.ModeTailscale {
		t.Error(".env bị đổi")
	}
}

func TestRemoteCloudflare(t *testing.T) {
	env, cp := remoteEnv(t)
	fr := &fake.Runner{Responses: []fake.Response{{Match: matchUp, Output: []byte("")}}}
	d := remoteDeps(t, fr, true)
	d.LookPath = func(string) (string, error) { return "/usr/bin/cloudflared", nil }
	var out strings.Builder
	if err := RunRemote(context.Background(), env, RemoteOptions{Action: "cloudflare", Hostname: "gh.example.com"}, d, &out); err != nil {
		t.Fatal(err)
	}
	st := envFile(t, cp)
	if st.Mode != access.ModeCloudflare || st.BindAddr != "127.0.0.1" || st.SiteAddress != "gh.example.com" || st.PublicURL != "https://gh.example.com" {
		t.Errorf("state = %+v", st)
	}
	for _, w := range []string{"service: https://localhost:8443", "noTLSVerify: true", "originServerName: localhost"} {
		if !strings.Contains(out.String(), w) {
			t.Errorf("thiếu %q", w)
		}
	}

	d.LookPath = func(string) (string, error) { return "", errors.New("not found") }
	env2, cp2 := remoteEnv(t)
	err := RunRemote(context.Background(), env2, RemoteOptions{Action: "cloudflare", Hostname: "gh.example.com"}, d, &strings.Builder{})
	if oe := asOpError(t, err); oe.Code != ErrCodeRemoteCloudflare {
		t.Errorf("code = %s", oe.Code)
	}
	if envFile(t, cp2).Mode == access.ModeCloudflare {
		t.Error(".env bị đổi")
	}
	if err := RunRemote(context.Background(), env2, RemoteOptions{Action: "cloudflare"}, d, &strings.Builder{}); err == nil {
		t.Error("thiếu --hostname phải lỗi")
	}
}

func TestRemoteLocal_FromTailscaleTurnsServeOff(t *testing.T) {
	env, cp := remoteEnv(t)
	if err := access.Write(cp, access.State{Mode: access.ModeTailscale, BindAddr: access.BindLocal, SiteAddress: "gen.ts.net", PublicURL: "https://gen.ts.net"}); err != nil {
		t.Fatal(err)
	}
	fr := &fake.Runner{Responses: []fake.Response{
		{Match: cmdNamed("tailscale"), Err: errors.New("lỗi giả")}, // chỉ cảnh báo
		{Match: matchUp, Output: []byte("")},
	}}
	var out strings.Builder
	if err := RunRemote(context.Background(), env, RemoteOptions{Action: "local", Yes: true}, remoteDeps(t, fr, true), &out); err != nil {
		t.Fatal(err)
	}
	st := envFile(t, cp)
	if st.Mode != access.ModeLocal || st.BindAddr != "127.0.0.1" || st.SiteAddress != "" || st.PublicURL != "https://localhost:8443" {
		t.Errorf("state = %+v", st)
	}
	if callIndex(fr, exactArgs("serve", "--https=443", "off")) < 0 {
		t.Error("phải thử tắt tailscale serve")
	}
	if !strings.Contains(out.String(), "cảnh báo") {
		t.Error("lỗi tắt serve phải in cảnh báo")
	}
}

func TestRemote_ReadyFails_RestoresOldEnv(t *testing.T) {
	env, cp := remoteEnv(t)
	old := access.State{Mode: access.ModeLocal, BindAddr: access.BindLocal, PublicURL: "https://localhost:8443"}
	if err := access.Write(cp, old); err != nil {
		t.Fatal(err)
	}
	before := mustRead(t, access.EnvPath(cp))
	fr := &fake.Runner{Responses: []fake.Response{{Match: matchUp, Output: []byte("")}}}
	err := RunRemote(context.Background(), env, RemoteOptions{Action: "lan", Name: "gh.lan", Yes: true}, remoteDeps(t, fr, false), &strings.Builder{})
	oe := asOpError(t, err)
	if oe.Code != ErrCodeRemoteNotReady {
		t.Errorf("code = %s", oe.Code)
	}
	if after := mustRead(t, access.EnvPath(cp)); after != before {
		t.Errorf(".env không được khôi phục:\n%s\n---\n%s", before, after)
	}
	if n := countCalls(fr, matchUp); n != 2 {
		t.Errorf("up phải chạy 2 lần (đổi + khôi phục), được %d", n)
	}
	ns, _ := hostlink.ReadNetworkStatus(env.InstallDir)
	if ns.Mode != "local" {
		t.Errorf("network-status.json phải về local: %+v", ns)
	}
}

func TestRemote_UpFails_RestoresOldEnv(t *testing.T) {
	env, cp := remoteEnv(t)
	_, _, _ = access.Ensure(cp, false)
	before := mustRead(t, access.EnvPath(cp))
	fr := &fake.Runner{Responses: []fake.Response{{Match: matchUp, Err: errors.New("cổng bận")}}}
	err := RunRemote(context.Background(), env, RemoteOptions{Action: "local", Yes: true}, remoteDeps(t, fr, true), &strings.Builder{})
	if oe := asOpError(t, err); oe.Code != ErrCodeRemoteUpFailed {
		t.Errorf("code = %s", oe.Code)
	}
	if mustRead(t, access.EnvPath(cp)) != before {
		t.Error(".env không được khôi phục")
	}
}

func TestRemoteStatus_PrintsModeMenuAndWarning(t *testing.T) {
	env, cp := remoteEnv(t)
	var out strings.Builder
	if err := RunRemote(context.Background(), env, RemoteOptions{}, RemoteDeps{GOOS: "linux"}, &out); err != nil {
		t.Fatal(err)
	}
	s := out.String()
	for _, w := range []string{"Cổng đang mở cho cả mạng", "0.0.0.0:8443", "https://localhost:8443", "genh remote tailscale", "KHUYÊN DÙNG", "genh remote cloudflare", "genh remote lan", "genh remote local"} {
		if !strings.Contains(s, w) {
			t.Errorf("status thiếu %q:\n%s", w, s)
		}
	}
	if envFile(t, cp).Mode != access.ModeLANLegacy {
		t.Error("máy cũ phải được ghi lan_legacy")
	}
	ns, err := hostlink.ReadNetworkStatus(env.InstallDir)
	if err != nil || ns.Mode != "lan_legacy" {
		t.Errorf("network-status.json = %+v %v", ns, err)
	}
}

func TestRemote_UnknownAction(t *testing.T) {
	env, _ := remoteEnv(t)
	if err := RunRemote(context.Background(), env, RemoteOptions{Action: "wat"}, RemoteDeps{}, &strings.Builder{}); err == nil || RemoteExitCode(err) != 2 {
		t.Errorf("muốn lỗi dùng sai (mã 2): %v", err)
	}
}

func mustRead(t *testing.T, p string) string {
	t.Helper()
	b, err := os.ReadFile(p)
	if err != nil {
		t.Fatal(err)
	}
	return string(b)
}

func TestPickLANIPv4_SkipsVirtualAndDownInterfaces(t *testing.T) {
	ipn := func(c string) net.Addr {
		ip, n, err := net.ParseCIDR(c)
		if err != nil {
			t.Fatal(err)
		}
		n.IP = ip
		return n
	}
	ifaces := []lanIface{
		{Name: "lo", Up: true, Loop: true, Addrs: []net.Addr{ipn("127.0.0.1/8")}},
		{Name: "docker0", Up: true, Addrs: []net.Addr{ipn("172.17.0.1/16")}},
		{Name: "br-3f2a", Up: true, Addrs: []net.Addr{ipn("172.18.0.1/16")}},
		{Name: "veth12ab", Up: true, Addrs: []net.Addr{ipn("172.17.0.5/16")}},
		{Name: "virbr0", Up: true, Addrs: []net.Addr{ipn("192.168.122.1/24")}},
		{Name: "eth1", Up: false, Addrs: []net.Addr{ipn("10.0.0.9/24")}},
		{Name: "tailscale0", Up: true, Addrs: []net.Addr{ipn("100.101.102.103/32")}},
		{Name: "wlan0", Up: true, Addrs: []net.Addr{ipn("fe80::1/64"), ipn("192.168.1.20/24")}},
	}
	if got, ok := pickLANIPv4(ifaces); !ok || got != "192.168.1.20" {
		t.Fatalf("pickLANIPv4 = %q %v, muốn 192.168.1.20", got, ok)
	}
	if _, ok := pickLANIPv4(ifaces[:6]); ok {
		t.Fatal("chỉ có card ảo/tắt mà vẫn chọn được IP")
	}
}

// Windows (WSL2/Docker Desktop) và macOS (Parallels/VMware/VPN): card ảo đứng trước Wi-Fi không được chọn.
func TestPickLANIPv4_SkipsWindowsAndMacVirtualInterfaces(t *testing.T) {
	ipn := func(c string) net.Addr {
		ip, n, err := net.ParseCIDR(c)
		if err != nil {
			t.Fatal(err)
		}
		n.IP = ip
		return n
	}
	cases := []struct {
		name   string
		ifaces []lanIface
		want   string
	}{
		{"windows", []lanIface{
			{Name: "vEthernet (WSL (Hyper-V firewall))", Up: true, Addrs: []net.Addr{ipn("172.29.144.1/20")}},
			{Name: "vEthernet (Default Switch)", Up: true, Addrs: []net.Addr{ipn("172.20.0.1/20")}},
			{Name: "Wi-Fi", Up: true, Addrs: []net.Addr{ipn("192.168.1.31/24")}},
		}, "192.168.1.31"},
		{"macos", []lanIface{
			{Name: "bridge100", Up: true, Addrs: []net.Addr{ipn("192.168.64.1/24")}},
			{Name: "vmenet0", Up: true, Addrs: []net.Addr{ipn("192.168.65.1/24")}},
			{Name: "utun3", Up: true, Addrs: []net.Addr{ipn("10.8.0.2/24")}},
			{Name: "en0", Up: true, Addrs: []net.Addr{ipn("192.168.0.12/24")}},
		}, "192.168.0.12"},
		{"vpn", []lanIface{
			{Name: "zt7nnig26", Up: true, Addrs: []net.Addr{ipn("10.147.17.5/24")}},
			{Name: "wg0", Up: true, Addrs: []net.Addr{ipn("10.6.0.2/24")}},
			{Name: "enp3s0", Up: true, Addrs: []net.Addr{ipn("10.0.0.40/24")}},
		}, "10.0.0.40"},
	}
	for _, c := range cases {
		if got, ok := pickLANIPv4(c.ifaces); !ok || got != c.want {
			t.Errorf("%s: pickLANIPv4 = %q %v, muốn %s", c.name, got, ok, c.want)
		}
	}
}

// `genh remote local` từ tailscale mà `up -d` lỗi: revert trả .env tailscale về — serve KHÔNG được tắt trước đó, nếu
// không truy cập từ xa bị cắt trong khi genh báo "đã trả về như cũ".
func TestRemoteLocal_UpFails_KeepsTailscaleServe(t *testing.T) {
	env, cp := remoteEnv(t)
	if err := access.Write(cp, access.State{Mode: access.ModeTailscale, BindAddr: access.BindLocal, SiteAddress: "gen.ts.net", PublicURL: "https://gen.ts.net"}); err != nil {
		t.Fatal(err)
	}
	before := mustRead(t, access.EnvPath(cp))
	fr := &fake.Runner{Responses: []fake.Response{
		{Match: cmdNamed("tailscale"), Output: []byte("")},
		{Match: matchUp, Err: errors.New("cổng bận")},
	}}
	err := RunRemote(context.Background(), env, RemoteOptions{Action: "local", Yes: true}, remoteDeps(t, fr, true), &strings.Builder{})
	if oe := asOpError(t, err); oe.Code != ErrCodeRemoteUpFailed {
		t.Errorf("code = %s", oe.Code)
	}
	if callIndex(fr, exactArgs("serve", "--https=443", "off")) >= 0 {
		t.Error("không được tắt tailscale serve khi chưa áp dụng xong chế độ local")
	}
	if mustRead(t, access.EnvPath(cp)) != before {
		t.Error(".env không được khôi phục")
	}
}

// `genh remote tailscale`: serve đã bật mà applyAccess lỗi SỚM (chưa ghi .env) ⇒ vẫn phải tắt serve lại.
func TestRemoteTailscale_EarlyApplyError_TurnsServeOff(t *testing.T) {
	env, cp := remoteEnv(t)
	if err := os.Mkdir(access.EnvPath(cp), 0o755); err != nil { // .env là thư mục ⇒ không đọc/ghi được
		t.Fatal(err)
	}
	fr := &fake.Runner{Responses: []fake.Response{
		{Match: exactArgs("status", "--json"), Output: tsStatusJSON("Running", "gen.tail1234.ts.net.", "gen.tail1234.ts.net")},
		{Match: cmdNamed("tailscale"), Output: []byte("")},
		{Match: matchUp, Output: []byte("")},
	}}
	err := RunRemote(context.Background(), env, RemoteOptions{Action: "tailscale", Yes: true}, remoteDeps(t, fr, true), &strings.Builder{})
	if oe := asOpError(t, err); oe.Code != ErrCodeAccessWriteFailed {
		t.Errorf("code = %s", oe.Code)
	}
	on := callIndex(fr, exactArgs("serve", "--bg", "--https=443", "https+insecure://localhost:8443"))
	off := callIndex(fr, exactArgs("serve", "--https=443", "off"))
	if on < 0 || off < on {
		t.Errorf("serve phải được bật rồi tắt lại (on=%d off=%d): %+v", on, off, fr.Calls)
	}
	if callIndex(fr, matchUp) >= 0 {
		t.Error("không được chạy up khi chưa ghi được .env")
	}
}
