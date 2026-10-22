package ops

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strconv"
	"strings"
	"time"

	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/access"
	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/compose"
	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/dockercli"
	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/hostlink"
)

// Mã lỗi truy cập từ xa (v0.1.46, F-21/F-27). GH-E960 đã thuộc gói chẩn đoán
// nên nhóm mới là GH-EC0x.
const (
	ErrCodeAccessWriteFailed = "GH-EC00" // không ghi được .env (cấu hình truy cập)
	ErrCodeRemoteNeedConfirm = "GH-EC01" // chế độ LAN cần xác nhận (không TTY và thiếu --yes) — thoát 2
	ErrCodeRemoteTailscale   = "GH-EC02" // Tailscale chưa sẵn sàng / lệnh serve lỗi
	ErrCodeRemoteCloudflare  = "GH-EC03" // thiếu cloudflared hoặc thiếu --hostname
	ErrCodeRemoteBadAddress  = "GH-EC04" // tên/IP không hợp lệ
	ErrCodeRemoteNotReady    = "GH-EC05" // đổi xong nhưng /api/v1/ready không xanh — đã khôi phục cấu hình cũ
	ErrCodeRemoteUpFailed    = "GH-EC06" // `docker compose up -d` lỗi — đã khôi phục cấu hình cũ
	ErrCodeRemoteBadUsage    = "GH-EC07" // cách dùng lệnh sai
)

// RemoteOptions là các cờ đã phân tích của `genh remote`.
type RemoteOptions struct {
	// Action: "status" (mặc định), "tailscale", "cloudflare", "lan", "local".
	Action   string
	Hostname string // cloudflare --hostname
	Name     string // lan --name
	Yes      bool
}

// RemoteDeps cho phép tiêm phần phụ thuộc khi test; trường nil dùng bản thật.
type RemoteDeps struct {
	Runner dockercli.Runner
	Client *http.Client
	// ReadyURL ghi đè URL /api/v1/ready (rỗng: localURL(env.Port, readyPath)).
	ReadyURL  string
	Timeout   time.Duration
	PollEvery time.Duration
	// Interactive: stdin/stdout là TTY (được phép hỏi xác nhận).
	Interactive bool
	In          io.Reader
	// LookPath tìm lệnh trên PATH (nil: exec.LookPath) — dùng cho cloudflared.
	LookPath func(name string) (string, error)
	// LANAddr dò IPv4 riêng đầu tiên của máy (nil: dò thật).
	LANAddr func() (string, error)
	GOOS    string
	// Warn tô đỏ cảnh báo (nil: giữ nguyên chữ).
	Warn func(string) string
}

const defaultRemoteReadyTimeout = 3 * time.Minute

// RemoteExitCode: 2 khi cần xác nhận mà không có (LAN không TTY và thiếu --yes)
// hoặc dùng sai lệnh; 1 cho lỗi khác.
func RemoteExitCode(err error) int {
	var oe *OpError
	if errors.As(err, &oe) && (oe.Code == ErrCodeRemoteNeedConfirm || oe.Code == ErrCodeRemoteBadUsage) {
		return 2
	}
	return 1
}

// RunRemote chạy `genh remote [status|tailscale|cloudflare|lan|local]`.
func RunRemote(ctx context.Context, env *Env, opts RemoteOptions, deps RemoteDeps, out io.Writer) error {
	if deps.Runner == nil {
		deps.Runner = dockercli.ExecRunner{}
	}
	if deps.Warn == nil {
		deps.Warn = func(s string) string { return s }
	}
	if deps.GOOS == "" {
		deps.GOOS = runtime.GOOS
	}
	switch opts.Action {
	case "", "status":
		return runRemoteStatus(env, deps, out)
	case "tailscale":
		return runRemoteTailscale(ctx, env, opts, deps, out)
	case "cloudflare":
		return runRemoteCloudflare(ctx, env, opts, deps, out)
	case "lan":
		return runRemoteLAN(ctx, env, opts, deps, out)
	case "local":
		return runRemoteLocal(ctx, env, opts, deps, out)
	}
	return &OpError{
		Code: ErrCodeRemoteBadUsage,
		What: "Cách truy cập không rõ: " + opts.Action,
		Next: "Dùng một trong: genh remote status | tailscale | cloudflare --hostname <tên> | lan | local.",
	}
}

// ─── Trạng thái ──────────────────────────────────────────────────────────

func effectiveURL(st access.State, port int) string {
	if st.PublicURL != "" {
		return st.PublicURL
	}
	return access.PublicURL(st.Mode, st.SiteAddress, port)
}

// printAccessLine in dòng "Truy cập: <nhãn> — <URL>" (+ cảnh báo lan_legacy) cho genh status.
func printAccessLine(out io.Writer, st access.State, port int) {
	_, _ = fmt.Fprintf(out, "Truy cập: %s — %s\n", st.Mode.Label(), effectiveURL(st, port))
	if st.Mode == access.ModeLANLegacy {
		_, _ = fmt.Fprintf(out, "  ! Cổng %d đang mở cho MỌI máy cùng mạng. Chạy `genh remote` để chọn cách truy cập an toàn hơn.\n", ResolvePort(port))
	}
}

// writeNetworkStatus ghi run/network-status.json (bỏ qua khi chưa có run/);
// lỗi chỉ in cảnh báo (out != nil) — không làm hỏng lệnh đang chạy.
func writeNetworkStatus(env *Env, st access.State, out io.Writer) error {
	if env.InstallDir == "" {
		return nil
	}
	if fi, err := os.Stat(hostlink.Dir(env.InstallDir)); err != nil || !fi.IsDir() {
		return nil
	}
	port := ResolvePort(env.Port)
	err := hostlink.WriteNetworkStatus(env.InstallDir, hostlink.NetworkStatus{
		Mode:        string(st.Mode),
		BindAddr:    st.BindAddr,
		SiteAddress: st.SiteAddress,
		PublicURL:   effectiveURL(st, port),
		Port:        port,
	})
	if err != nil && out != nil {
		_, _ = fmt.Fprintf(out, "  (cảnh báo: không ghi được %s — %v)\n", hostlink.NetworkStatusFile, err)
	}
	return err
}

func runRemoteStatus(env *Env, deps RemoteDeps, out io.Writer) error {
	composePath, err := env.LocatePath()
	if err != nil {
		return err
	}
	st, err := access.Read(composePath)
	if err != nil {
		return &OpError{
			Code: ErrCodeAccessWriteFailed,
			What: "Không đọc được cấu hình truy cập (.env)",
			Why:  err.Error(),
			Next: "Kiểm quyền đọc " + access.EnvPath(composePath) + ".",
			Err:  err,
		}
	}
	_ = writeNetworkStatus(env, st, out)
	port := ResolvePort(env.Port)
	bind := st.BindAddr
	if bind == "" {
		bind = access.BindAll
	}
	_, _ = fmt.Fprintf(out, "Truy cập từ xa: %s\n", st.Mode.Label())
	_, _ = fmt.Fprintf(out, "  Cổng đang nghe ở : %s:%d\n", bind, port)
	_, _ = fmt.Fprintf(out, "  Địa chỉ đăng nhập: %s\n", effectiveURL(st, port))
	if bind == access.BindAll {
		_, _ = fmt.Fprintln(out, deps.Warn(fmt.Sprintf("  ! Cổng %d đang mở cho MỌI máy cùng mạng (Wi-Fi văn phòng, khách…).", port)))
	}
	_, _ = fmt.Fprint(out, `
Bốn cách truy cập (đổi bằng lệnh):
  genh remote tailscale                    KHUYÊN DÙNG — vào từ điện thoại/máy khác qua Tailscale, không mở cổng ra mạng
  genh remote cloudflare --hostname <tên>  qua Cloudflare Tunnel (tên miền của bạn)
  genh remote lan [--name <tên|IP>]        mạng nội bộ (LAN) — mở cổng cho cả mạng, phải cài Chứng chỉ CA trên từng máy
  genh remote local                        chỉ máy này (an toàn nhất)
`)
	return nil
}

// ─── Khung chung: ghi .env → up -d → chờ ready → khôi phục nếu lỗi ─────────

func (d RemoteDeps) readyURL(env *Env) string {
	if d.ReadyURL != "" {
		return d.ReadyURL
	}
	return localURL(env.Port, readyPath)
}

// undo (có thể nil) hoàn tác việc người gọi đã làm TRƯỚC applyAccess (vd đã bật `tailscale serve`): gọi ở MỌI
// đường lỗi — kể cả lỗi sớm trước khi ghi .env — để "chưa đổi gì" là thật.
func applyAccess(ctx context.Context, env *Env, deps RemoteDeps, ns access.State, out io.Writer, undo func()) error {
	early := func(e error) error {
		if undo != nil {
			undo()
		}
		return e
	}
	composePath, err := env.LocatePath()
	if err != nil {
		return early(err)
	}
	bundle, err := env.LoadSecrets()
	if err != nil {
		return early(err)
	}
	overlay := EnvOverlay(bundle)
	dir := composeDir(composePath)
	prev, _ := access.Read(composePath)
	snap, err := access.Take(composePath)
	if err != nil {
		return early(&OpError{Code: ErrCodeAccessWriteFailed, What: "Không đọc được cấu hình truy cập (.env)", Why: err.Error(), Next: "Kiểm quyền đọc thư mục " + dir + ".", Err: err})
	}
	if err := access.Write(composePath, ns); err != nil {
		return early(&OpError{Code: ErrCodeAccessWriteFailed, What: "Không ghi được cấu hình truy cập (.env)", Why: err.Error(), Next: "Kiểm quyền ghi thư mục " + dir + " rồi thử lại.", Err: err})
	}
	_ = writeNetworkStatus(env, ns, out)

	upArgs := compose.BaseArgs(composePath, "up", "-d")
	revert := func() {
		_, _ = fmt.Fprintln(out, "Khôi phục cấu hình truy cập cũ…")
		if rerr := snap.Restore(composePath); rerr != nil {
			_, _ = fmt.Fprintf(out, "  (cảnh báo: không khôi phục được .env — %v)\n", rerr)
		}
		_ = writeNetworkStatus(env, prev, out)
		if undo != nil {
			undo()
		}
		if _, uerr := deps.Runner.Output(ctx, dockercli.Cmd{Name: "docker", Args: upArgs, Env: overlay, Dir: dir}); uerr != nil {
			_, _ = fmt.Fprintf(out, "  (cảnh báo: `docker compose up -d` sau khôi phục lỗi — %v)\n", uerr)
		}
	}

	_, _ = fmt.Fprintln(out, "Áp dụng cấu hình truy cập (tạo lại proxy/api/worker)…")
	if _, err := deps.Runner.Output(ctx, dockercli.Cmd{Name: "docker", Args: upArgs, Env: overlay, Dir: dir}); err != nil {
		revert()
		return &OpError{
			Code: ErrCodeRemoteUpFailed,
			What: "`docker compose up -d` thất bại — đã trả cấu hình truy cập về như cũ",
			Why:  err.Error(),
			Next: "Xem `genh logs` rồi thử lại.",
			Err:  err,
		}
	}
	reportRunDirPerms(ctx, deps.Runner, env.InstallDir, composePath, overlay, out)

	client := deps.Client
	if client == nil {
		client = insecureLocalClient(5 * time.Second)
	}
	timeout := deps.Timeout
	if timeout <= 0 {
		timeout = defaultRemoteReadyTimeout
	}
	pollEvery := deps.PollEvery
	if pollEvery <= 0 {
		pollEvery = defaultUpdatePollEvery
	}
	_, _ = fmt.Fprintln(out, "Chờ dịch vụ sẵn sàng…")
	if err := waitReady(ctx, client, deps.readyURL(env), timeout, pollEvery); err != nil {
		revert()
		return &OpError{
			Code: ErrCodeRemoteNotReady,
			What: readyPath + " không trả 200 sau khi đổi cách truy cập — đã trả cấu hình về như cũ",
			Why:  err.Error(),
			Next: "Xem `genh logs` và `genh status`; giữ cách truy cập hiện tại hoặc thử lại sau.",
			Err:  err,
		}
	}
	return nil
}

// ─── Tailscale ───────────────────────────────────────────────────────────

type tailscaleStatus struct {
	BackendState string `json:"BackendState"`
	Self         struct {
		DNSName string `json:"DNSName"`
	} `json:"Self"`
	CertDomains []string `json:"CertDomains"`
}

func tailscaleInstallHint(goos string) string {
	if goos == "linux" {
		return "Cài Tailscale: `sudo dnf install tailscale`, rồi `sudo systemctl enable --now tailscaled` và `sudo tailscale up` (đăng nhập tài khoản Tailscale)."
	}
	return "Cài ứng dụng Tailscale (tailscale.com/download) trên máy chủ này và đăng nhập."
}

func runRemoteTailscale(ctx context.Context, env *Env, opts RemoteOptions, deps RemoteDeps, out io.Writer) error {
	port := ResolvePort(env.Port)
	tsErr := func(what, why, next string) error {
		return &OpError{Code: ErrCodeRemoteTailscale, What: what, Why: why, Next: next}
	}
	raw, err := deps.Runner.Output(ctx, dockercli.Cmd{Name: "tailscale", Args: []string{"status", "--json"}})
	if err != nil {
		if errors.Is(err, dockercli.ErrNotFound) || errors.Is(err, exec.ErrNotFound) {
			return &OpError{
				Code: ErrCodeRemoteTailscale,
				What: "Chưa thấy lệnh `tailscale` trên máy chủ — chưa đổi gì",
				Why:  err.Error(),
				Next: tailscaleInstallHint(deps.GOOS) + " Xong chạy lại `genh remote tailscale`.",
				Err:  err,
			}
		}
		if len(strings.TrimSpace(string(raw))) == 0 {
			return &OpError{Code: ErrCodeRemoteTailscale, What: "Không đọc được trạng thái Tailscale — chưa đổi gì", Why: err.Error(), Next: "Kiểm `tailscale status` chạy được (dịch vụ tailscaled đã bật, đã `sudo tailscale up`).", Err: err}
		}
	}
	var ts tailscaleStatus
	if jerr := json.Unmarshal(raw, &ts); jerr != nil {
		return tsErr("Không hiểu kết quả `tailscale status --json` — chưa đổi gì", jerr.Error(), "Cập nhật Tailscale lên bản mới rồi chạy lại.")
	}
	if ts.BackendState != "Running" {
		return tsErr("Tailscale chưa chạy (trạng thái: "+ts.BackendState+") — chưa đổi gì", "",
			"Chạy `sudo tailscale up` và đăng nhập, rồi chạy lại `genh remote tailscale`.")
	}
	dns := strings.TrimSuffix(strings.TrimSpace(ts.Self.DNSName), ".")
	if dns == "" {
		return tsErr("Chưa có tên Tailscale của máy này — chưa đổi gì", "", "Bật MagicDNS trong trang quản trị Tailscale (login.tailscale.com › DNS), rồi chạy lại.")
	}
	if len(ts.CertDomains) == 0 {
		return tsErr("Tailscale chưa bật HTTPS — chưa đổi gì", "CertDomains rỗng",
			"Trong trang quản trị Tailscale (login.tailscale.com › DNS): bật MagicDNS và bật \"HTTPS Certificates\", rồi chạy lại `genh remote tailscale`.")
	}
	if err := access.ValidateSiteAddress(dns); err != nil {
		return &OpError{Code: ErrCodeRemoteBadAddress, What: "Tên Tailscale không hợp lệ — chưa đổi gì", Why: err.Error(), Next: "Kiểm `tailscale status`.", Err: err}
	}

	// PHẢI là localhost (không phải 127.0.0.1): Caddy cần SNI localhost.
	target := "https+insecure://localhost:" + strconv.Itoa(port)
	if serr := runTailscaleServe(ctx, deps, []string{"serve", "--bg", "--https=443", target}); serr != nil {
		msg := strings.ToLower(serr.Error())
		next := "Chạy `tailscale serve status` để xem lỗi, rồi thử lại."
		if strings.Contains(msg, "access denied") || strings.Contains(msg, "permission denied") || strings.Contains(msg, "operator") || strings.Contains(msg, "not permitted") {
			next = "Chạy một lần: `sudo tailscale set --operator=$USER`, rồi chạy lại `genh remote tailscale`."
		}
		return &OpError{Code: ErrCodeRemoteTailscale, What: "`tailscale serve` thất bại — chưa đổi cấu hình Gen-Harness", Why: serr.Error(), Next: next, Err: serr}
	}

	ns := access.State{Mode: access.ModeTailscale, BindAddr: access.BindLocal, SiteAddress: dns, PublicURL: access.PublicURL(access.ModeTailscale, dns, port)}
	if err := applyAccess(ctx, env, deps, ns, out, func() { _ = runTailscaleServe(ctx, deps, []string{"serve", "--https=443", "off"}) }); err != nil {
		return err
	}
	_, _ = fmt.Fprintf(out, "Xong. Mở trên điện thoại: %s (cài app Tailscale, đăng nhập cùng tài khoản)\n", ns.PublicURL)
	return nil
}

func runTailscaleServe(ctx context.Context, deps RemoteDeps, args []string) error {
	_, err := deps.Runner.Output(ctx, dockercli.Cmd{Name: "tailscale", Args: args})
	return err
}

// ─── Cloudflare ──────────────────────────────────────────────────────────

func runRemoteCloudflare(ctx context.Context, env *Env, opts RemoteOptions, deps RemoteDeps, out io.Writer) error {
	port := ResolvePort(env.Port)
	host := strings.TrimSpace(opts.Hostname)
	if host == "" {
		return &OpError{Code: ErrCodeRemoteCloudflare, What: "Thiếu --hostname — chưa đổi gì", Next: "Chạy `genh remote cloudflare --hostname gh.tenmiencuaban.com`."}
	}
	if err := access.ValidateSiteAddress(host); err != nil {
		return &OpError{Code: ErrCodeRemoteBadAddress, What: "Tên miền không hợp lệ — chưa đổi gì", Why: err.Error(), Next: "Nhập tên miền thuần, ví dụ gh.tenmiencuaban.com (không có https://, cổng hay khoảng trắng).", Err: err}
	}
	look := deps.LookPath
	if look == nil {
		look = exec.LookPath
	}
	if _, err := look("cloudflared"); err != nil {
		return &OpError{
			Code: ErrCodeRemoteCloudflare,
			What: "Chưa thấy lệnh `cloudflared` trên máy chủ — chưa đổi gì",
			Why:  err.Error(),
			Next: "Cài cloudflared (developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads), tạo tunnel rồi chạy lại.",
			Err:  err,
		}
	}
	_, _ = fmt.Fprintf(out, "Cấu hình ingress mẫu cho cloudflared (config.yml):\n\n  ingress:\n    - hostname: %s\n      service: https://localhost:%d\n      originRequest:\n        noTLSVerify: true\n        originServerName: localhost\n    - service: http_status:404\n\n", host, port)
	ns := access.State{Mode: access.ModeCloudflare, BindAddr: access.BindLocal, SiteAddress: host, PublicURL: access.PublicURL(access.ModeCloudflare, host, port)}
	if err := applyAccess(ctx, env, deps, ns, out, nil); err != nil {
		return err
	}
	_, _ = fmt.Fprintf(out, "Xong. Địa chỉ đăng nhập: %s — chạy tunnel cloudflared theo cấu hình ở trên để vào được từ ngoài.\n", ns.PublicURL)
	return nil
}

// ─── LAN ─────────────────────────────────────────────────────────────────

// lanIface là một card mạng cùng các địa chỉ của nó (tách khỏi net.Interface để test được).
type lanIface struct {
	Name  string
	Up    bool
	Loop  bool
	Addrs []net.Addr
}

// virtualIfacePrefixes: card ảo của Docker/libvirt/k8s/VPN — có IP RFC1918 nhưng điện thoại trong LAN không tới được.
// So khớp KHÔNG phân biệt hoa thường. Windows: "vEthernet (WSL…)"/"vEthernet (Default Switch)" của Hyper-V/WSL2/Docker
// Desktop (khớp "veth"); macOS: bridge100+ / vmenet* (Parallels/VMware/Docker Desktop), utun* (VPN); ZeroTier zt*,
// WireGuard wg*.
var virtualIfacePrefixes = []string{"docker", "br-", "veth", "virbr", "cni", "flannel", "kube", "podman", "lxc", "lxd",
	"vmnet", "vboxnet", "vethernet", "bridge1", "vmenet", "utun", "zt", "wg"}

// pickLANIPv4 chọn IPv4 riêng (RFC1918) đầu tiên trên card thật đang bật — bỏ card tắt, loopback và card ảo
// (docker0, br-*, veth*, virbr*…): chọn nhầm IP 172.17.x của docker0 thì điện thoại không vào được.
func pickLANIPv4(ifaces []lanIface) (string, bool) {
	for _, it := range ifaces {
		if !it.Up || it.Loop {
			continue
		}
		virtual := false
		for _, p := range virtualIfacePrefixes {
			if strings.HasPrefix(strings.ToLower(it.Name), p) {
				virtual = true
				break
			}
		}
		if virtual {
			continue
		}
		for _, a := range it.Addrs {
			ipn, ok := a.(*net.IPNet)
			if !ok {
				continue
			}
			if ip4 := ipn.IP.To4(); ip4 != nil && ip4.IsPrivate() {
				return ip4.String(), true
			}
		}
	}
	return "", false
}

// privateIPv4 trả IPv4 riêng (RFC1918) đầu tiên của máy trên card mạng thật đang bật (pickLANIPv4).
func privateIPv4() (string, error) {
	nifs, err := net.Interfaces()
	if err != nil {
		return "", err
	}
	ifaces := make([]lanIface, 0, len(nifs))
	for _, ni := range nifs {
		addrs, err := ni.Addrs()
		if err != nil {
			continue
		}
		ifaces = append(ifaces, lanIface{Name: ni.Name, Up: ni.Flags&net.FlagUp != 0, Loop: ni.Flags&net.FlagLoopback != 0, Addrs: addrs})
	}
	if ip, ok := pickLANIPv4(ifaces); ok {
		return ip, nil
	}
	return "", errors.New("không thấy địa chỉ IPv4 riêng (192.168.x.x / 10.x.x.x) nào trên card mạng đang bật (đã bỏ qua card ảo của Docker/VM)")
}

func runRemoteLAN(ctx context.Context, env *Env, opts RemoteOptions, deps RemoteDeps, out io.Writer) error {
	port := ResolvePort(env.Port)
	name := strings.TrimSpace(opts.Name)
	if name == "" {
		det := deps.LANAddr
		if det == nil {
			det = privateIPv4
		}
		ip, err := det()
		if err != nil {
			return &OpError{Code: ErrCodeRemoteBadAddress, What: "Không tự dò được địa chỉ LAN của máy — chưa đổi gì", Why: err.Error(), Next: "Chạy lại kèm --name <tên máy hoặc IP>, ví dụ `genh remote lan --name 192.168.1.20`.", Err: err}
		}
		name = ip
	}
	if err := access.ValidateSiteAddress(name); err != nil {
		return &OpError{Code: ErrCodeRemoteBadAddress, What: "Tên/IP LAN không hợp lệ — chưa đổi gì", Why: err.Error(), Next: "Dùng tên máy hoặc IPv4 thuần, ví dụ 192.168.1.20.", Err: err}
	}
	_, _ = fmt.Fprintln(out, deps.Warn(fmt.Sprintf("CẢNH BÁO: Cổng %d sẽ mở cho MỌI máy cùng mạng (Wi-Fi văn phòng, khách…); trang đăng nhập lộ ra mạng; chứng chỉ nội bộ — điện thoại phải cài CA.", port)))
	if !opts.Yes {
		if !deps.Interactive {
			return &OpError{
				Code: ErrCodeRemoteNeedConfirm,
				What: "Chưa mở cổng cho mạng nội bộ — cần bạn xác nhận",
				Why:  "không có cửa sổ dòng lệnh tương tác để hỏi và thiếu --yes",
				Next: "Chạy lại `genh remote lan --yes` nếu bạn chắc chắn muốn mở cổng cho cả mạng.",
			}
		}
		_, _ = fmt.Fprintln(out, "Tiếp tục mở cổng cho mạng nội bộ? [y/N]")
		if deps.In == nil || !confirmYesNo(deps.In) {
			return &OpError{Code: ErrCodeRemoteNeedConfirm, What: "Đã huỷ — không đổi cách truy cập", Next: "Chạy lại `genh remote lan` khi muốn mở cổng cho mạng nội bộ."}
		}
	}
	ns := access.State{Mode: access.ModeLAN, BindAddr: access.BindAll, SiteAddress: name, PublicURL: access.PublicURL(access.ModeLAN, name, port)}
	if err := applyAccess(ctx, env, deps, ns, out, nil); err != nil {
		return err
	}
	ca := filepath.Join(env.InstallDir, "config", "caddy-root.crt")
	_, _ = fmt.Fprintf(out, `Xong. Địa chỉ đăng nhập trong mạng: %s

Cài Chứng chỉ CA cho điện thoại (chép tệp %s sang điện thoại):
  iPhone : mở tệp › Cài đặt › Cài đặt chung › VPN & Quản lý thiết bị › Cài;
           rồi Cài đặt › Cài đặt chung › Giới thiệu › Cài đặt tin cậy chứng nhận › bật cho chứng chỉ này.
  Android: Cài đặt › Bảo mật › Mã hoá & thông tin xác thực › Cài chứng chỉ CA.
`, ns.PublicURL, ca)
	if hint := firewallHint(ctx, deps, port); hint != "" {
		_, _ = fmt.Fprint(out, hint)
	}
	return nil
}

// firewallHint gợi ý mở firewalld (Fedora) khi nó đang chạy mà cổng chưa mở.
// genh không tự sudo.
func firewallHint(ctx context.Context, deps RemoteDeps, port int) string {
	if deps.GOOS != "linux" {
		return ""
	}
	st, err := deps.Runner.Output(ctx, dockercli.Cmd{Name: "firewall-cmd", Args: []string{"--state"}})
	if err != nil || strings.TrimSpace(string(st)) != "running" {
		return ""
	}
	q, _ := deps.Runner.Output(ctx, dockercli.Cmd{Name: "firewall-cmd", Args: []string{fmt.Sprintf("--query-port=%d/tcp", port)}})
	if strings.TrimSpace(string(q)) == "yes" {
		return ""
	}
	return fmt.Sprintf("\nTường lửa firewalld đang bật và chưa mở cổng %d. Chạy (genh không tự sudo):\n  sudo firewall-cmd --add-port=%d/tcp --permanent && sudo firewall-cmd --reload\n", port, port)
}

// ─── Local ───────────────────────────────────────────────────────────────

func runRemoteLocal(ctx context.Context, env *Env, opts RemoteOptions, deps RemoteDeps, out io.Writer) error {
	port := ResolvePort(env.Port)
	composePath, err := env.LocatePath()
	if err != nil {
		return err
	}
	prev, _ := access.Read(composePath)
	if !opts.Yes && deps.Interactive && (prev.Mode == access.ModeLAN || prev.Mode == access.ModeTailscale || prev.Mode == access.ModeCloudflare) {
		_, _ = fmt.Fprintf(out, "Chuyển về \"Chỉ máy này\" sẽ cắt truy cập từ điện thoại/máy khác (đang dùng: %s). Tiếp tục? [y/N]\n", prev.Mode.Label())
		if deps.In == nil || !confirmYesNo(deps.In) {
			return &OpError{Code: ErrCodeRemoteNeedConfirm, What: "Đã huỷ — không đổi cách truy cập", Next: "Chạy lại `genh remote local --yes` khi chắc chắn."}
		}
	}
	ns := access.State{Mode: access.ModeLocal, BindAddr: access.BindLocal, PublicURL: access.PublicURL(access.ModeLocal, "", port)}
	if err := applyAccess(ctx, env, deps, ns, out, nil); err != nil {
		return err
	}
	// Tắt `tailscale serve` CHỈ SAU KHI đã áp dụng xong: lỗi giữa chừng thì revert trả .env tailscale về và serve vẫn
	// chạy — "đã trả về như cũ" là thật, không cắt truy cập từ xa.
	if prev.Mode == access.ModeTailscale {
		if err := runTailscaleServe(ctx, deps, []string{"serve", "--https=443", "off"}); err != nil {
			_, _ = fmt.Fprintf(out, "  (cảnh báo: không tắt được `tailscale serve` — %v; tự tắt bằng `tailscale serve --https=443 off`)\n", err)
		}
	}
	_, _ = fmt.Fprintf(out, "Xong. Chỉ máy này truy cập được: %s\n", ns.PublicURL)
	return nil
}
