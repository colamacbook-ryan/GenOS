package ops

import (
	"archive/zip"
	"context"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
	"time"

	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/dockercli/fake"
	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/hostlink"
)

func TestRunDoctor_HappyPath_WritesZipWithReportAndLogs(t *testing.T) {
	composePath := testComposePath(t, "")
	env := testEnv(t, composePath)
	outPath := filepath.Join(t.TempDir(), "report.zip")

	fr := &fake.Runner{Responses: []fake.Response{
		{Match: fake.MatchArgsContain("version", "--format"), Output: []byte("27.1.0")},
		{Match: fake.MatchArgsContain("system", "df", "-v"), Output: []byte("VOLUME NAME\ngen-harness_pg_data 1 10MB\n")},
		{Match: fake.MatchArgsContain("logs", "--tail=2000"), Output: []byte("api log line 1\ndb log line 1\n")},
	}}

	srv := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_ = json.NewEncoder(w).Encode(map[string]string{"db": "ok", "redis": "ok", "objects": "skip", "bridge": "ok"})
	}))
	defer srv.Close()

	deps := DoctorDeps{
		Runner: fr,
		Client: srv.Client(),
		DialTCP: func(address string, timeout time.Duration) error {
			return nil
		},
		DialTLS: func(address string, timeout time.Duration) (string, time.Time, error) {
			return "CN=Gen-Harness Local CA", time.Now().Add(24 * time.Hour), nil
		},
	}

	var out strings.Builder
	if err := RunDoctor(context.Background(), env, outPath, deps, &out); err != nil {
		t.Fatalf("RunDoctor: %v", err)
	}

	if !strings.Contains(out.String(), "Docker runtime") {
		t.Errorf("output thiếu mục Docker runtime: %s", out.String())
	}

	zr, err := zip.OpenReader(outPath)
	if err != nil {
		t.Fatalf("mở zip báo cáo: %v", err)
	}
	defer func() { _ = zr.Close() }()

	names := map[string]bool{}
	for _, f := range zr.File {
		names[f.Name] = true
	}
	if !names["report.txt"] || !names["logs.txt"] {
		t.Fatalf("zip phải có report.txt và logs.txt, được %v", names)
	}
}

func TestRunDoctor_PortDialFails_ReportedButNotFatal(t *testing.T) {
	composePath := testComposePath(t, "")
	env := testEnv(t, composePath)
	outPath := filepath.Join(t.TempDir(), "report.zip")

	fr := &fake.Runner{Responses: []fake.Response{
		{Match: fake.MatchArgsContain("version", "--format"), Output: []byte("27.1.0")},
		{Match: fake.MatchArgsContain("system", "df", "-v"), Output: []byte("")},
		{Match: fake.MatchArgsContain("logs", "--tail=2000"), Output: []byte("")},
	}}

	deps := DoctorDeps{
		Runner:  fr,
		DialTCP: func(address string, timeout time.Duration) error { return errors.New("connection refused") },
		DialTLS: func(address string, timeout time.Duration) (string, time.Time, error) {
			return "", time.Time{}, errors.New("connection refused")
		},
	}

	var out strings.Builder
	// RunDoctor KHÔNG được trả lỗi chỉ vì cổng/TLS không kết nối được — đó
	// là một MỤC chẩn đoán thất bại (✕), không phải lỗi của chính lệnh
	// doctor (báo cáo vẫn phải xuất ra để Owner gửi hỗ trợ).
	if err := RunDoctor(context.Background(), env, outPath, deps, &out); err != nil {
		t.Fatalf("RunDoctor không được trả lỗi khi một mục chẩn đoán thất bại: %v", err)
	}
	if !strings.Contains(out.String(), "✕") {
		t.Errorf("output phải đánh dấu ✕ cho mục cổng/TLS thất bại: %s", out.String())
	}
}

func TestRunDoctor_ZipWriteFails_ReturnsOpError(t *testing.T) {
	composePath := testComposePath(t, "")
	env := testEnv(t, composePath)
	// Đường dẫn đích là một thư mục có thật (t.TempDir()), không phải tên
	// tệp — os.Create sẽ lỗi "is a directory".
	outPath := t.TempDir()

	fr := &fake.Runner{Responses: []fake.Response{
		{Match: fake.MatchArgsContain("version", "--format"), Output: []byte("27.1.0")},
		{Match: fake.MatchArgsContain("system", "df", "-v"), Output: []byte("")},
		{Match: fake.MatchArgsContain("logs", "--tail=2000"), Output: []byte("")},
	}}
	deps := DoctorDeps{
		Runner:  fr,
		DialTCP: func(string, time.Duration) error { return nil },
		DialTLS: func(string, time.Duration) (string, time.Time, error) { return "", time.Time{}, nil },
	}

	err := RunDoctor(context.Background(), env, outPath, deps, &strings.Builder{})
	opErr, ok := err.(*OpError)
	if !ok {
		t.Fatalf("lỗi phải là *OpError, được %T (%v)", err, err)
	}
	if opErr.Code != ErrCodeDoctorReportFailed {
		t.Errorf("Code = %q, muốn %q", opErr.Code, ErrCodeDoctorReportFailed)
	}
}

// v0.1.36 (F-4) / v0.1.44 (F-4b — 2000 dòng): log trong báo cáo doctor phải có dấu thời gian (`docker compose logs -t --tail=2000`).
func TestRunDoctor_LogsHaveTimestamps(t *testing.T) {
	composePath := testComposePath(t, "")
	env := testEnv(t, composePath)
	outPath := filepath.Join(t.TempDir(), "report.zip")

	logsMatch := fake.MatchArgsContain("logs", "-t", "--tail=2000")
	fr := &fake.Runner{Responses: []fake.Response{
		{Match: fake.MatchArgsContain("version", "--format"), Output: []byte("27.1.0")},
		{Match: fake.MatchArgsContain("system", "df", "-v"), Output: []byte("")},
		{Match: logsMatch, Output: []byte("api  | 2026-10-02T01:02:03.000000000Z dòng có giờ\n")},
	}}
	srv := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_ = json.NewEncoder(w).Encode(map[string]string{"db": "ok", "redis": "ok", "objects": "skip", "bridge": "ok"})
	}))
	defer srv.Close()
	deps := DoctorDeps{
		Runner:  fr,
		Client:  srv.Client(),
		DialTCP: func(address string, timeout time.Duration) error { return nil },
		DialTLS: func(address string, timeout time.Duration) (string, time.Time, error) {
			return "CN=Gen-Harness Local CA", time.Now().Add(24 * time.Hour), nil
		},
	}
	var out strings.Builder
	if err := RunDoctor(context.Background(), env, outPath, deps, &out); err != nil {
		t.Fatalf("RunDoctor: %v", err)
	}

	found := false
	for _, c := range fr.Calls {
		if logsMatch(c.Cmd) {
			found = true
		}
	}
	if !found {
		t.Fatalf("doctor phải gọi `docker compose logs -t --tail=2000`, các lệnh đã gọi: %v", fr.Calls)
	}

	zr, err := zip.OpenReader(outPath)
	if err != nil {
		t.Fatalf("mở zip báo cáo: %v", err)
	}
	defer func() { _ = zr.Close() }()
	for _, f := range zr.File {
		if f.Name != "logs.txt" {
			continue
		}
		rc, err := f.Open()
		if err != nil {
			t.Fatalf("mở logs.txt: %v", err)
		}
		b, _ := io.ReadAll(rc)
		_ = rc.Close()
		if !strings.Contains(string(b), "2026-10-02T01:02:03") {
			t.Fatalf("logs.txt thiếu dấu thời gian: %q", b)
		}
		return
	}
	t.Fatal("zip thiếu logs.txt")
}

// v0.1.37 (F-73): genh doctor thêm 2 dòng Docker/linger tự chạy lại + ghi run/autostart-status.json.
func TestRunDoctor_TuChayLaiKhiBatMay(t *testing.T) {
	composePath := testComposePath(t, "")
	env := testEnv(t, composePath)
	outPath := filepath.Join(t.TempDir(), "report.zip")
	fr := &fake.Runner{Responses: append([]fake.Response{
		{Match: fake.MatchArgsContain("version", "--format"), Output: []byte("27.1.0")},
		{Match: fake.MatchArgsContain("system", "df", "-v"), Output: []byte("VOLUME NAME\n")},
		{Match: fake.MatchArgsContain("logs", "--tail=2000"), Output: []byte("")},
	}, autostartFakeResponses()...)}
	srv := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {}))
	defer srv.Close()
	deps := DoctorDeps{Runner: fr, Client: srv.Client(), GOOS: "linux", UID: "1000",
		DialTCP: func(string, time.Duration) error { return nil },
		DialTLS: func(string, time.Duration) (string, time.Time, error) { return "CN=x", time.Now(), nil },
	}
	var out strings.Builder
	if err := RunDoctor(context.Background(), env, outPath, deps, &out); err != nil {
		t.Fatalf("RunDoctor: %v", err)
	}
	got := out.String()
	for _, want := range []string{"✕ Docker tự chạy", "sudo systemctl enable docker", "✕ Linger (systemd --user)", "sudo loginctl enable-linger $USER"} {
		if !strings.Contains(got, want) {
			t.Errorf("thiếu %q:\n%s", want, got)
		}
	}
	raw := checkAutostartFile(t, env.InstallDir)
	if raw["docker_enabled"] != "no" || raw["linger_required"] != true {
		t.Errorf("autostart-status.json sai: %v", raw)
	}

	// Darwin: không áp dụng — vẫn ✓ và ghi tệp.
	var out2 strings.Builder
	deps.GOOS = "darwin"
	fr.Responses = append(fr.Responses, fake.Response{Match: cmdIs("docker", "info --format {{.OperatingSystem}}"), Output: []byte("Docker Desktop\n")})
	if err := RunDoctor(context.Background(), env, outPath, deps, &out2); err != nil {
		t.Fatalf("RunDoctor darwin: %v", err)
	}
	if !strings.Contains(out2.String(), "✓ Docker tự chạy") {
		t.Errorf("darwin: %s", out2.String())
	}
	if raw := checkAutostartFile(t, env.InstallDir); raw["linger"] != "not_applicable" || raw["docker_mode"] != "desktop" {
		t.Errorf("darwin: %v", raw)
	}
}

// ─── v0.1.44 (F-4b): gói chẩn đoán qua hộp thư ──────────────────────────────

func writeDoctorRequest(t *testing.T, installDir, body string) {
	t.Helper()
	if err := os.WriteFile(hostlink.DoctorRequestPath(installDir), []byte(body), 0o666); err != nil {
		t.Fatal(err)
	}
}

func TestRunDoctorRequest_DoneGiu3Zip0644(t *testing.T) {
	env, lits := doctorInstall(t)
	base := time.Date(2026, 10, 3, 10, 0, 0, 0, time.UTC)
	for i := 0; i < 4; i++ {
		writeDoctorRequest(t, env.InstallDir, `{"schema":1,"request_id":"0123456789abcde`+string(rune('0'+i))+`","requested_at":"x"}`)
		now := base.Add(time.Duration(i) * time.Minute)
		if err := RunDoctorRequest(context.Background(), env, doctorTestDeps(doctorRunner(lits), now), &strings.Builder{}); err != nil {
			t.Fatalf("lần %d: %v", i, err)
		}
		if hostlink.HasDoctorRequest(env.InstallDir) {
			t.Fatal("phải xoá tệp yêu cầu")
		}
	}
	dir := hostlink.DiagnosticsDirPath(env.InstallDir)
	ents, _ := os.ReadDir(dir)
	var zips []string
	for _, e := range ents {
		zips = append(zips, e.Name())
	}
	if len(zips) != 3 || zips[0] != "genh-doctor-20261003T100100Z.zip" || zips[2] != "genh-doctor-20261003T100300Z.zip" {
		t.Fatalf("chỉ giữ 3 zip mới nhất, được %v", zips)
	}
	// Windows không có bit quyền POSIX (Perm() luôn 0666/0777) — chỉ kiểm trên Unix.
	if fi, _ := os.Stat(dir); runtime.GOOS != "windows" && fi.Mode().Perm() != 0o755 {
		t.Fatalf("thư mục diagnostics %v, muốn 0755", fi.Mode().Perm())
	}
	st, err := hostlink.ReadDoctorStatus(env.InstallDir)
	if err != nil || st.State != "done" || st.RequestID != "0123456789abcde3" || st.File != "genh-doctor-20261003T100300Z.zip" ||
		st.StartedAt == "" || st.FinishedAt == "" || st.ErrorCode != "" {
		t.Fatalf("doctor-status = %+v, %v", st, err)
	}
	path := filepath.Join(dir, st.File)
	fi, err := os.Stat(path)
	if err != nil || (runtime.GOOS != "windows" && fi.Mode().Perm() != 0o644) || fi.Size() != st.SizeBytes {
		t.Fatalf("zip %v (size %d, status %d), muốn 0644", fi.Mode().Perm(), fi.Size(), st.SizeBytes)
	}
	sum, _, _ := fileSHA256(path)
	if sum != st.SHA256 || len(st.SHA256) != 64 {
		t.Fatalf("sha256 lệch: %s vs %s", sum, st.SHA256)
	}
}

func TestRunDoctorRequest_Failed_DiagnosticsLaSymlink(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("os.Symlink trên Windows cần quyền đặc biệt — lối chặn symlink kiểm trên Unix")
	}
	env, lits := doctorInstall(t)
	target := t.TempDir()
	if err := os.Symlink(target, hostlink.DiagnosticsDirPath(env.InstallDir)); err != nil {
		t.Fatal(err)
	}
	writeDoctorRequest(t, env.InstallDir, `{"schema":1,"request_id":"aaaaaaaaaaaaaaaa"}`)
	err := RunDoctorRequest(context.Background(), env, doctorTestDeps(doctorRunner(lits), time.Now()), &strings.Builder{})
	var oe *OpError
	if !errors.As(err, &oe) || oe.Code != ErrCodeDoctorBundleFailed {
		t.Fatalf("muốn GH-E962, được %v", err)
	}
	st, _ := hostlink.ReadDoctorStatus(env.InstallDir)
	if st.State != "failed" || st.ErrorCode != ErrCodeDoctorBundleFailed || st.Message == "" || strings.Contains(st.Message, "`") {
		t.Fatalf("doctor-status = %+v", st)
	}
	if ents, _ := os.ReadDir(target); len(ents) != 0 {
		t.Fatalf("không được ghi qua symlink: %v", ents)
	}
}

func TestRunDoctorRequest_RequestIDSaiDang_BiBo(t *testing.T) {
	env, lits := doctorInstall(t)
	fr := doctorRunner(lits)
	writeDoctorRequest(t, env.InstallDir, `{"schema":1,"request_id":"../../../etc/passwd"}`)
	if err := RunDoctorRequest(context.Background(), env, doctorTestDeps(fr, time.Now()), &strings.Builder{}); err != nil {
		t.Fatal(err)
	}
	if hostlink.HasDoctorRequest(env.InstallDir) {
		t.Fatal("tệp yêu cầu sai dạng vẫn phải bị xoá")
	}
	if _, err := hostlink.ReadDoctorStatus(env.InstallDir); err == nil {
		t.Fatal("request_id sai dạng: không ghi doctor-status.json")
	}
	if len(fr.Calls) != 0 {
		t.Fatalf("không chạy chẩn đoán: %v", fr.Calls)
	}
	// Không có yêu cầu ⇒ nil, không làm gì.
	if err := RunDoctorRequest(context.Background(), env, doctorTestDeps(fr, time.Now()), &strings.Builder{}); err != nil {
		t.Fatal(err)
	}
}

// Zip chẩn đoán (0644, log đầy đủ) không được nằm quá 24 giờ — lượt trực canh dọn.
func TestPruneDiagnostics_XoaZipQua24Gio(t *testing.T) {
	root := t.TempDir()
	dir, err := hostlink.EnsureDiagnosticsDir(root)
	if err != nil {
		t.Fatal(err)
	}
	for _, n := range []string{"genh-doctor-20261001T090000Z.zip", "genh-doctor-20261003T090000Z.zip", "ghi-chu.txt"} {
		if err := os.WriteFile(filepath.Join(dir, n), []byte("x"), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	PruneDiagnostics(root, time.Date(2026, 10, 3, 10, 0, 0, 0, time.UTC))
	ents, _ := os.ReadDir(dir)
	var got []string
	for _, e := range ents {
		got = append(got, e.Name())
	}
	if strings.Join(got, ",") != "genh-doctor-20261003T090000Z.zip,ghi-chu.txt" {
		t.Fatalf("chỉ xoá zip quá 24 giờ, còn %v", got)
	}
}
