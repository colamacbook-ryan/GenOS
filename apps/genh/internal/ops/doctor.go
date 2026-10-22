package ops

import (
	"context"
	"crypto/tls"
	"fmt"
	"io"
	"net"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/compose"
	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/dockercli"
	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/hostlink"
)

// DoctorDeps cho phép tiêm mọi phụ thuộc I/O thật (Docker CLI, TCP, TLS,
// HTTP) khi test — các trường nil dùng cài đặt thật.
type DoctorDeps struct {
	Runner dockercli.Runner
	Client *http.Client
	// DialTCP thử kết nối TCP tới address, trả lỗi nếu không kết nối được
	// trong timeout — nil dùng net.DialTimeout thật.
	DialTCP func(address string, timeout time.Duration) error
	// DialTLS thử bắt tay TLS tới address (InsecureSkipVerify — chỉ kiểm
	// TLS có phục vụ được, không xác minh CA), trả về mô tả chứng chỉ nhận
	// được — nil dùng tls.DialWithDialer thật.
	DialTLS func(address string, timeout time.Duration) (subject string, notAfter time.Time, err error)
	// GOOS/UID cho phần "Tự chạy lại khi bật máy" (CheckAutostart) — rỗng dùng
	// runtime.GOOS / os.Getuid().
	GOOS string
	UID  string
	// Version là phiên bản genh ghi vào versions.txt của gói chẩn đoán (v0.1.44).
	Version string
	// Now cho test (nil = time.Now) — tên tệp zip/thời điểm trong doctor-status.json.
	Now func() time.Time
}

func (d DoctorDeps) runner() dockercli.Runner {
	if d.Runner == nil {
		return dockercli.ExecRunner{}
	}
	return d.Runner
}

func (d DoctorDeps) now() time.Time {
	if d.Now != nil {
		return d.Now().UTC()
	}
	return time.Now().UTC()
}

func realDialTCP(address string, timeout time.Duration) error {
	conn, err := net.DialTimeout("tcp", address, timeout)
	if err != nil {
		return err
	}
	return conn.Close()
}

func realDialTLS(address string, timeout time.Duration) (string, time.Time, error) {
	dialer := &net.Dialer{Timeout: timeout}
	conn, err := tls.DialWithDialer(dialer, "tcp", address, &tls.Config{InsecureSkipVerify: true, ServerName: ProxyHost}) //nolint:gosec // chỉ kiểm TLS phục vụ được, xem DoctorDeps.DialTLS
	if err != nil {
		return "", time.Time{}, err
	}
	defer func() { _ = conn.Close() }()
	certs := conn.ConnectionState().PeerCertificates
	if len(certs) == 0 {
		return "", time.Time{}, fmt.Errorf("không có chứng chỉ nào được trình ra")
	}
	return certs[0].Subject.String(), certs[0].NotAfter, nil
}

// diagLine là một dòng chẩn đoán: mục kiểm, OK hay không, chi tiết.
type diagLine struct {
	Check string
	OK    bool
	Info  string
}

func (d diagLine) String() string {
	mark := "✓"
	if !d.OK {
		mark = "✕"
	}
	return fmt.Sprintf("%s %-24s %s", mark, d.Check, d.Info)
}

// RunDoctor chẩn đoán runtime/cổng/chứng chỉ/dung lượng/đồng hồ/kết nối
// kênh, in tóm tắt ra out, và xuất gói chẩn đoán ĐÃ LỌC BÍ MẬT (v0.1.44,
// F-4b — xem doctor_bundle.go: report.txt, logs.txt `docker compose logs -t
// --tail=2000`, genh-logs/, host/*.json, versions.txt, manifest.json) vào một
// tệp zip tại outPath.
func RunDoctor(ctx context.Context, env *Env, outPath string, deps DoctorDeps, out io.Writer) error {
	runner := deps.runner()
	lines, as := collectDoctorLines(ctx, env, deps, runner)
	// v0.1.45: tự chữa quyền hộp thư run/ (2770 nhóm 10001) + một dòng trạng thái.
	lines = append(lines, runDirPermsDiagLine(ctx, env, runner))
	for _, l := range lines {
		_, _ = fmt.Fprintln(out, l.String())
	}
	writeAutostartStatus(env, as, out)

	composePath, _ := env.LocatePath()
	red := doctorRedactor(env, composePath)
	now := deps.now()
	entries := collectBundle(ctx, env, runner, deps, lines, now)
	if err := writeDoctorBundleFile(outPath, 0o600, entries, red, now); err != nil {
		return &OpError{
			Code: ErrCodeDoctorReportFailed,
			What: "Không ghi được báo cáo chẩn đoán ra " + outPath,
			Why:  err.Error(),
			Next: "Kiểm quyền ghi thư mục đích rồi thử lại.",
			Err:  err,
		}
	}
	_, _ = fmt.Fprintln(out, "\nBáo cáo đầy đủ (đã lọc bí mật): "+outPath)
	return nil
}

// runDirPermsDiagLine siết lại quyền run/ (EnsureRunDirPerms) và trả dòng trạng thái.
func runDirPermsDiagLine(ctx context.Context, env *Env, runner dockercli.Runner) diagLine {
	mode, err := EnsureRunDirPerms(ctx, env, runner)
	ok, text := RunDirPermsLine(mode)
	if !ok && err != nil {
		text += " (" + err.Error() + ")"
	}
	return diagLine{"Hộp thư run/", ok, text}
}

// collectDoctorLines chạy các mục chẩn đoán (không in, không ghi gì).
func collectDoctorLines(ctx context.Context, env *Env, deps DoctorDeps, runner dockercli.Runner) ([]diagLine, hostlink.AutostartStatus) {
	client := deps.Client
	if client == nil {
		client = insecureLocalClient(5 * time.Second)
	}
	dialTCP := deps.DialTCP
	if dialTCP == nil {
		dialTCP = realDialTCP
	}
	dialTLS := deps.DialTLS
	if dialTLS == nil {
		dialTLS = realDialTLS
	}

	var lines []diagLine

	// 1. Runtime: `docker version --format`.
	verOut, verErr := runner.Output(ctx, dockercli.Cmd{Name: "docker", Args: []string{"version", "--format", "{{.Server.Version}}"}})
	if verErr != nil {
		lines = append(lines, diagLine{"Docker runtime", false, verErr.Error()})
	} else {
		lines = append(lines, diagLine{"Docker runtime", true, "Docker Engine " + strings.TrimSpace(string(verOut))})
	}

	// 2. Cổng: thử kết nối TCP tới 127.0.0.1:port.
	addr := fmt.Sprintf("127.0.0.1:%d", ResolvePort(env.Port))
	if err := dialTCP(addr, 2*time.Second); err != nil {
		lines = append(lines, diagLine{"Cổng " + addr, false, err.Error()})
	} else {
		lines = append(lines, diagLine{"Cổng " + addr, true, "đang lắng nghe"})
	}

	// 3. Chứng chỉ: bắt tay TLS tới cùng cổng.
	if subject, notAfter, err := dialTLS(addr, 2*time.Second); err != nil {
		lines = append(lines, diagLine{"Chứng chỉ TLS", false, err.Error()})
	} else {
		lines = append(lines, diagLine{"Chứng chỉ TLS", true, fmt.Sprintf("%s (hết hạn %s)", subject, notAfter.Format("2006-01-02"))})
	}

	// 4. Dung lượng: `docker system df -v`, lọc theo volume Gen-Harness.
	dfOut, dfErr := runner.Output(ctx, dockercli.Cmd{Name: "docker", Args: []string{"system", "df", "-v"}})
	if dfErr != nil {
		lines = append(lines, diagLine{"Dung lượng", false, dfErr.Error()})
	} else {
		vl := volumeLines(string(dfOut))
		lines = append(lines, diagLine{"Dung lượng", true, fmt.Sprintf("%d dòng volume liên quan (chi tiết trong báo cáo)", len(vl))})
	}

	// 5. Đồng hồ: so time.Now() UTC với header HTTP "Date" của chính
	// /api/v1/ready (nguồn tin cậy có sẵn — mọi phản hồi HTTP chuẩn đều có
	// header Date do chính server đặt, không cần thêm endpoint mới) — nếu
	// gọi /api/v1/ready thất bại (dịch vụ chưa chạy), KHÔNG suy đoán, ghi rõ
	// bỏ qua vì không có nguồn tin cậy nào khác sẵn có.
	readyURL := localURL(env.Port, readyPath)
	req, _ := http.NewRequestWithContext(ctx, http.MethodGet, readyURL, nil)
	resp, clockErr := client.Do(req)
	switch {
	case clockErr != nil:
		lines = append(lines, diagLine{"Đồng hồ hệ thống", false, "bỏ qua — không gọi được " + readyPath + " để lấy giờ tham chiếu (" + clockErr.Error() + ")"})
	default:
		defer func() { _ = resp.Body.Close() }()
		dateHdr := resp.Header.Get("Date")
		if dateHdr == "" {
			lines = append(lines, diagLine{"Đồng hồ hệ thống", false, "bỏ qua — phản hồi không có header Date"})
		} else if serverTime, perr := http.ParseTime(dateHdr); perr != nil {
			lines = append(lines, diagLine{"Đồng hồ hệ thống", false, "bỏ qua — không phân tích được header Date: " + perr.Error()})
		} else {
			skew := time.Since(serverTime)
			lines = append(lines, diagLine{"Đồng hồ hệ thống", true, fmt.Sprintf("lệch %s so với header Date của %s", skew.Round(time.Second), readyPath)})
		}
	}

	// 6. Kết nối kênh (bridge): đọc lại chính /api/v1/ready ("bridge":
	// "ok"/"down", xem apps/api/gh/shell/routes.py) — bridge không có
	// healthcheck compose riêng nên "ready" là nguồn thật duy nhất hiện có;
	// KHÔNG bịa số liệu nào khác.
	readyBody, readyErr := probeReadyBody(ctx, client, readyURL)
	switch {
	case readyErr != nil:
		lines = append(lines, diagLine{"Kết nối kênh (bridge)", false, "không gọi được " + readyPath + ": " + readyErr.Error()})
	default:
		bridgeStatus := readyBody["bridge"]
		lines = append(lines, diagLine{"Kết nối kênh (bridge)", bridgeStatus == "ok", "bridge: " + bridgeStatus})
	}

	// 6b. Hộp riêng Antigravity (v0.1.63, H3): chỉ khi compose.yaml có agy-box.
	if l, ok := agyBoxDiagLine(ctx, env, runner); ok {
		lines = append(lines, l)
	}

	// 7–8. Tự chạy lại khi bật máy (v0.1.37, F-73): Docker + linger — ghi kèm
	// run/autostart-status.json cho Console (bên gọi ghi).
	as := CheckAutostart(ctx, AutostartDeps{Runner: runner, GOOS: deps.GOOS, UID: deps.UID})
	lines = append(lines, autostartLines(as)...)
	return lines, as
}

// mustAbsDir trả về Dir(path) — nếu path chỉ là tên tệp (không có thư mục
// cha rõ ràng), trả "." để os.MkdirAll không lỗi.
func mustAbsDir(path string) string {
	dir := filepath.Dir(path)
	if dir == "" {
		return "."
	}
	return dir
}

// agyBoxDiagLine kiểm dịch vụ agy-box (hộp riêng cho agent ngoại giao: chạy Antigravity không
// có khoá, CSDL hay bí mật). ok=false (không có dòng) khi compose.yaml chưa khai agy-box
// (bản cài v0.1.62) hoặc không đọc được compose. Hộp hỏng KHÔNG làm cả hệ thống hỏng — Gen
// chỉ mất model cho agent ngoại giao — nên đây là dòng chẩn đoán, không phải lỗi chặn.
func agyBoxDiagLine(ctx context.Context, env *Env, runner dockercli.Runner) (diagLine, bool) {
	const check = "Hộp Antigravity (agy-box)"
	composePath, err := env.LocatePath()
	if err != nil {
		return diagLine{}, false
	}
	data, err := os.ReadFile(composePath)
	if err != nil {
		return diagLine{}, false
	}
	cf, err := compose.Parse(data)
	if err != nil {
		return diagLine{}, false
	}
	if _, has := cf.Services["agy-box"]; !has {
		return diagLine{}, false
	}
	// Gói sửa review v0.1.63 (P2-2): hộp chỉ ra Internet qua proxy agy-egress — compose có proxy thì kiểm cả proxy.
	_, hasEgress := cf.Services["agy-egress"]
	names := []string{"agy-box"}
	if hasEgress {
		names = append(names, "agy-egress")
	}
	bundle, err := env.LoadSecrets()
	if err != nil {
		return diagLine{check, false, "không đọc được bí mật để hỏi trạng thái — chạy `genh status`"}, true
	}
	psArgs := compose.BaseArgs(composePath, append([]string{"ps", "--all", "--format", "json"}, names...)...)
	psOut, err := runner.Output(ctx, dockercli.Cmd{Name: "docker", Args: psArgs, Env: EnvOverlay(bundle), Dir: composeDir(composePath)})
	if err != nil {
		return diagLine{check, false, "không hỏi được docker compose: " + err.Error()}, true
	}
	statuses, err := compose.ParsePS(psOut)
	if err != nil {
		return diagLine{check, false, "không đọc được trạng thái: " + err.Error()}, true
	}
	byName := map[string]compose.ContainerStatus{}
	for _, s := range statuses {
		byName[s.Service] = s
	}
	healthy := func(s compose.ContainerStatus) bool {
		return s.State == "running" && (s.Health == "" || s.Health == "healthy")
	}
	stateOf := func(s compose.ContainerStatus) string {
		if s.Health != "" {
			return s.State + "/" + s.Health
		}
		return s.State
	}
	box, ok := byName["agy-box"]
	if !ok {
		return diagLine{check, false, "chưa có container — `genh start`, rồi xem `genh logs agy-box` nếu vẫn lỗi"}, true
	}
	if !healthy(box) {
		return diagLine{check, false, stateOf(box) + " — agent ngoại giao tạm không dùng được Antigravity; xem `genh logs agy-box`"}, true
	}
	info := "đang chạy"
	if box.Health != "" {
		info += " (" + box.Health + ")"
	}
	if hasEgress {
		eg, ok := byName["agy-egress"]
		switch {
		case !ok:
			return diagLine{check, false, info + " nhưng proxy lối ra agy-egress chưa có container — `genh start`, rồi xem `genh logs agy-egress`"}, true
		case eg.State != "running" || eg.Health == "unhealthy":
			return diagLine{check, false, info + " nhưng proxy lối ra agy-egress " + stateOf(eg) + " — Antigravity trong hộp không ra được Internet; xem `genh logs agy-egress`"}, true
		}
		info += " · proxy lối ra đang chạy"
	}
	return diagLine{check, true, info}, true
}
