// Package access quản lý cách Owner truy cập Gen-Harness từ xa (v0.1.46,
// F-21/F-27): tệp `.env` cạnh compose.yaml mang đúng 4 khoá do genh quản lý.
//
//	GH_ACCESS_MODE   local | lan | lan_legacy | tailscale | cloudflare
//	GH_BIND_ADDR     127.0.0.1 | 0.0.0.0   (deploy/compose.yaml: proxy.ports)
//	GH_SITE_ADDRESS  tên miền/IPv4 đã kiểm (Caddyfile khớp Host; không có ở chế độ local)
//	GH_PUBLIC_URL    https://… dựng từ giá trị đã kiểm (không nhận URL tuỳ ý)
//
// Compose tự nạp `.env` ở thư mục dự án (= thư mục chứa compose.yaml, cũng là
// Cmd.Dir của dockercli). Mọi dòng khác của Owner được giữ nguyên, đúng thứ tự.
// Tệp không chứa bí mật (0644) và KHÔNG đưa vào gói chẩn đoán.
//
// Gói này thuần (chỉ đọc/ghi tệp) — test được không cần Docker.
package access

import (
	"errors"
	"fmt"
	"net"
	"os"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
)

// Tên 4 khoá genh quản lý.
const (
	KeyMode = "GH_ACCESS_MODE"
	KeyBind = "GH_BIND_ADDR"
	KeySite = "GH_SITE_ADDRESS"
	KeyURL  = "GH_PUBLIC_URL"
)

// DefaultPort là cổng HTTPS mặc định của proxy (machine.DefaultPort).
const DefaultPort = 8443

// Mode là chế độ truy cập.
type Mode string

const (
	ModeLocal      Mode = "local"
	ModeLAN        Mode = "lan"
	ModeLANLegacy  Mode = "lan_legacy"
	ModeTailscale  Mode = "tailscale"
	ModeCloudflare Mode = "cloudflare"
)

// Địa chỉ nghe hợp lệ.
const (
	BindLocal = "127.0.0.1"
	BindAll   = "0.0.0.0"
)

// HeaderComment là dòng đầu tệp .env do genh ghi.
const HeaderComment = "# genh quản lý các khoá GH_ACCESS_MODE/GH_BIND_ADDR/GH_SITE_ADDRESS/GH_PUBLIC_URL — đổi bằng genh remote"

// State là cấu hình truy cập. BindAddr rỗng = tệp chưa có GH_BIND_ADDR.
type State struct {
	Mode        Mode
	BindAddr    string
	SiteAddress string
	PublicURL   string
}

// Label là nhãn tiếng Việt của chế độ (hiển thị trong genh status/remote).
func (m Mode) Label() string {
	switch m {
	case ModeLocal:
		return "Chỉ máy này"
	case ModeLAN:
		return "Mạng nội bộ (LAN)"
	case ModeLANLegacy:
		return "Cổng đang mở cho cả mạng"
	case ModeTailscale:
		return "Tailscale"
	case ModeCloudflare:
		return "Cloudflare Tunnel"
	}
	return "Chưa rõ"
}

func (m Mode) valid() bool {
	switch m {
	case ModeLocal, ModeLAN, ModeLANLegacy, ModeTailscale, ModeCloudflare:
		return true
	}
	return false
}

// EnvPath là đường dẫn .env cạnh compose.yaml.
func EnvPath(composePath string) string {
	return filepath.Join(filepath.Dir(composePath), ".env")
}

// PublicURL dựng GH_PUBLIC_URL từ chế độ + tên đã kiểm + cổng.
func PublicURL(mode Mode, site string, port int) string {
	if port <= 0 {
		port = DefaultPort
	}
	switch mode {
	case ModeTailscale, ModeCloudflare:
		return "https://" + site
	case ModeLAN:
		return "https://" + site + ":" + strconv.Itoa(port)
	}
	return "https://localhost:" + strconv.Itoa(port)
}

var domainRe = regexp.MustCompile(`^[A-Za-z0-9]([A-Za-z0-9-]{0,62})(\.[A-Za-z0-9]([A-Za-z0-9-]{0,62}))*$`)
var digitsDotsRe = regexp.MustCompile(`^[0-9.]+$`)

// ValidateSiteAddress chỉ nhận tên miền hoặc IPv4 hợp lệ — giá trị được thế
// thẳng vào Caddyfile và .env nên phải chặn chèn chỉ thị (khoảng trắng, dấu
// phẩy, ngoặc nhọn, scheme/cổng, xuống dòng). Từ chối localhost, 127.x, 0.0.0.0.
func ValidateSiteAddress(s string) error {
	bad := func(why string) error { return fmt.Errorf("địa chỉ %q không hợp lệ: %s", s, why) }
	if s == "" {
		return bad("để trống")
	}
	if len(s) > 253 {
		return bad("dài quá 253 ký tự")
	}
	if !domainRe.MatchString(s) {
		return bad("chỉ nhận tên miền (chữ, số, gạch nối, dấu chấm) hoặc IPv4")
	}
	if strings.EqualFold(s, "localhost") {
		return bad("localhost đã có sẵn")
	}
	if digitsDotsRe.MatchString(s) {
		ip := net.ParseIP(s)
		if ip == nil || ip.To4() == nil || strings.Contains(s, ":") {
			return bad("không phải IPv4 hợp lệ")
		}
		if ip.IsLoopback() || ip.IsUnspecified() {
			return bad("địa chỉ loopback/0.0.0.0 không dùng được")
		}
	}
	return nil
}

func validateState(st State) error {
	if !st.Mode.valid() {
		return fmt.Errorf("chế độ truy cập %q không hợp lệ", st.Mode)
	}
	if st.BindAddr != BindLocal && st.BindAddr != BindAll {
		return fmt.Errorf("GH_BIND_ADDR %q không hợp lệ (chỉ 127.0.0.1 hoặc 0.0.0.0)", st.BindAddr)
	}
	switch st.Mode {
	case ModeLocal, ModeLANLegacy:
		if st.SiteAddress != "" {
			return fmt.Errorf("chế độ %s không có địa chỉ site", st.Mode)
		}
	default:
		if err := ValidateSiteAddress(st.SiteAddress); err != nil {
			return err
		}
	}
	if !validPublicURL(st.PublicURL) {
		return fmt.Errorf("GH_PUBLIC_URL %q không hợp lệ", st.PublicURL)
	}
	return nil
}

// parseKey trả khoá của dòng KEY=VALUE (bỏ "export "), "" nếu không phải.
func parseKey(line string) (key, val string, ok bool) {
	t := strings.TrimSpace(line)
	if t == "" || strings.HasPrefix(t, "#") {
		return "", "", false
	}
	t = strings.TrimPrefix(t, "export ")
	i := strings.IndexByte(t, '=')
	if i <= 0 {
		return "", "", false
	}
	k := strings.TrimSpace(t[:i])
	v := strings.TrimSpace(t[i+1:])
	if len(v) >= 2 && (v[0] == '"' && v[len(v)-1] == '"' || v[0] == '\'' && v[len(v)-1] == '\'') {
		v = v[1 : len(v)-1]
	}
	return k, v, true
}

func isManaged(k string) bool {
	return k == KeyMode || k == KeyBind || k == KeySite || k == KeyURL
}

func readLines(path string) ([]string, error) {
	b, err := os.ReadFile(path)
	if err != nil {
		if errors.Is(err, os.ErrNotExist) {
			return nil, nil
		}
		return nil, err
	}
	s := strings.ReplaceAll(string(b), "\r\n", "\n")
	s = strings.TrimSuffix(s, "\n")
	if s == "" {
		return nil, nil
	}
	return strings.Split(s, "\n"), nil
}

// Read đọc 4 khoá từ .env cạnh composePath. Tệp không có → State rỗng, nil.
func Read(composePath string) (State, error) {
	lines, err := readLines(EnvPath(composePath))
	if err != nil {
		return State{}, err
	}
	var st State
	for _, l := range lines {
		k, v, ok := parseKey(l)
		if !ok {
			continue
		}
		switch k {
		case KeyMode:
			st.Mode = Mode(v)
		case KeyBind:
			st.BindAddr = v
		case KeySite:
			st.SiteAddress = v
		case KeyURL:
			st.PublicURL = v
		}
	}
	return st, nil
}

// Write ghi 4 khoá vào .env, giữ nguyên mọi dòng khác và thứ tự; khoá thiếu
// thì thêm cuối; ghi nguyên tử (tệp tạm cùng thư mục + rename), 0644.
func Write(composePath string, st State) error {
	if err := validateState(st); err != nil {
		return err
	}
	path := EnvPath(composePath)
	lines, err := readLines(path)
	if err != nil {
		return err
	}
	want := map[string]string{KeyMode: string(st.Mode), KeyBind: st.BindAddr, KeyURL: st.PublicURL}
	if st.SiteAddress != "" {
		want[KeySite] = st.SiteAddress
	}
	order := []string{KeyMode, KeyBind, KeySite, KeyURL}
	seen := map[string]bool{}
	var out []string
	for _, l := range lines {
		k, _, ok := parseKey(l)
		if !ok || !isManaged(k) {
			out = append(out, l)
			continue
		}
		v, has := want[k]
		if !has || seen[k] {
			continue // GH_SITE_ADDRESS cũ ở chế độ không dùng, hoặc khoá trùng
		}
		seen[k] = true
		out = append(out, k+"="+v)
	}
	for _, k := range order {
		if v, has := want[k]; has && !seen[k] {
			out = append(out, k+"="+v)
		}
	}
	if len(out) == 0 || out[0] != HeaderComment {
		out = append([]string{HeaderComment}, out...)
	}
	return writeAtomic(path, []byte(strings.Join(out, "\n")+"\n"))
}

func writeAtomic(path string, data []byte) error {
	f, err := os.CreateTemp(filepath.Dir(path), ".env.*.tmp")
	if err != nil {
		return err
	}
	tmp := f.Name()
	done := false
	defer func() {
		if !done {
			_ = os.Remove(tmp)
		}
	}()
	if _, err := f.Write(data); err != nil {
		_ = f.Close()
		return err
	}
	if err := f.Chmod(0o644); err != nil { // không chứa bí mật
		_ = f.Close()
		return err
	}
	if err := f.Close(); err != nil {
		return err
	}
	if err := os.Rename(tmp, path); err != nil {
		return err
	}
	done = true
	return nil
}

func defaultState(fresh bool) State {
	if fresh {
		return State{Mode: ModeLocal, BindAddr: BindLocal, PublicURL: PublicURL(ModeLocal, "", DefaultPort)}
	}
	// Giữ hành vi cũ (QD-12): máy đã cài từ trước đang nghe mọi giao diện.
	return State{Mode: ModeLANLegacy, BindAddr: BindAll, PublicURL: PublicURL(ModeLANLegacy, "", DefaultPort)}
}

func validPublicURL(u string) bool {
	return strings.HasPrefix(u, "https://") && len(u) > len("https://") && !strings.ContainsAny(u, " \t\r\n\"'$`\\#")
}

// upgradeState là trạng thái cho máy cài từ trước v0.1.46 (.env chưa có GH_BIND_ADDR): vẫn nghe mọi giao diện như
// cũ (QD-12) nhưng GIỮ GH_SITE_ADDRESS / GH_PUBLIC_URL Owner đã tự đặt trong .env — mất chúng thì Caddy thôi khớp
// Host của Owner và link mời/Telegram trỏ về localhost. Có tên site hợp lệ ⇒ chế độ lan (0.0.0.0 + tên đó).
func upgradeState(old State) State {
	ns := defaultState(false)
	if old.SiteAddress != "" && ValidateSiteAddress(old.SiteAddress) == nil {
		ns = State{Mode: ModeLAN, BindAddr: BindAll, SiteAddress: old.SiteAddress, PublicURL: PublicURL(ModeLAN, old.SiteAddress, DefaultPort)}
	}
	if validPublicURL(old.PublicURL) {
		ns.PublicURL = old.PublicURL
	}
	return ns
}

// Ensure đảm bảo .env có GH_BIND_ADDR. Chưa có → fresh ? local/127.0.0.1 :
// lan_legacy/0.0.0.0 (giữ GH_SITE_ADDRESS/GH_PUBLIC_URL Owner đã đặt — upgradeState).
// Đã có → không đổi gì (changed=false).
func Ensure(composePath string, fresh bool) (State, bool, error) {
	st, err := Read(composePath)
	if err != nil {
		return State{}, false, err
	}
	if st.BindAddr != "" {
		return st, false, nil
	}
	ns := defaultState(true)
	if !fresh {
		ns = upgradeState(st)
	}
	if err := Write(composePath, ns); err != nil {
		return State{}, false, err
	}
	return ns, true, nil
}

// EnsureFresh dành cho `genh install` lần đầu: như Ensure(fresh=true) và sửa
// lan_legacy về local (một lệnh vận hành chạy chen giữa lúc cài mới đã ghi
// 0.0.0.0 — cài mới thì chỉ máy này).
func EnsureFresh(composePath string) (State, bool, error) {
	st, changed, err := Ensure(composePath, true)
	if err != nil {
		return State{}, false, err
	}
	if st.Mode != ModeLANLegacy {
		return st, changed, nil
	}
	ns := defaultState(true)
	if err := Write(composePath, ns); err != nil {
		return State{}, false, err
	}
	return ns, true, nil
}

// Snapshot là bản chụp nguyên văn .env để khôi phục khi đổi cách truy cập
// không thành công (genh remote: ready lỗi → trả lại tệp cũ).
type Snapshot struct {
	data    []byte
	existed bool
}

// Take chụp .env hiện tại (chưa có tệp cũng là một trạng thái hợp lệ).
func Take(composePath string) (Snapshot, error) {
	b, err := os.ReadFile(EnvPath(composePath))
	if err != nil {
		if errors.Is(err, os.ErrNotExist) {
			return Snapshot{}, nil
		}
		return Snapshot{}, err
	}
	return Snapshot{data: b, existed: true}, nil
}

// Restore trả .env về đúng bản chụp (ghi nguyên tử; bản chụp "chưa có tệp" thì xoá tệp).
func (s Snapshot) Restore(composePath string) error {
	path := EnvPath(composePath)
	if !s.existed {
		if err := os.Remove(path); err != nil && !errors.Is(err, os.ErrNotExist) {
			return err
		}
		return nil
	}
	return writeAtomic(path, s.data)
}
