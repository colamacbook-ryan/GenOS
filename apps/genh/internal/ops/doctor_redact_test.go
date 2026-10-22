package ops

import (
	"archive/zip"
	"context"
	"encoding/base64"
	"encoding/json"
	"io"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/config"
	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/dockercli"
	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/dockercli/fake"
	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/hostlink"
	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/notify"
	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/secretgen"
)

const doctorTestCompose = `name: gen-harness
services:
  api:
    image: ghcr.io/o/gen-harness-api:v0.1.44
  db:
    image: postgres:16
`

// doctorInstall dựng bản cài giả có ĐỦ bí mật: config/secrets.json (secretgen),
// secrets/* giá trị biết trước, run/telegram.json mã hoá bằng master key giả.
// Trả env + danh sách mọi literal bí mật.
func doctorInstall(t *testing.T) (*Env, []string) {
	t.Helper()
	composePath := testComposePath(t, doctorTestCompose)
	env := testEnv(t, composePath)
	b, err := secretgen.Load(env.ConfigDir())
	if err != nil {
		t.Fatal(err)
	}
	secrets := map[string]string{
		"gh_master_key":  wdMasterHex,
		"gh_bridge_key":  "QnJpZGdlS2V5VmFsdWVGb3JUZXN0T25seTAxMjM0NTY3OA==",
		"gh_browser_key": "QnJvd3NlcktleVZhbHVlRm9yVGVzdE9ubHkwMTIzNDU2Nzg=",
		"gh_agy_key":     "QWd5Qm94S2V5VmFsdWVGb3JUZXN0T25seTAxMjM0NTY3ODk=",
		"gh_offsite_key": "K7QX2-ABCDE-FGHIJ-KLMNO-PQRST-UVWXY",
	}
	dir := filepath.Join(env.InstallDir, "secrets")
	if err := os.MkdirAll(dir, 0o700); err != nil {
		t.Fatal(err)
	}
	for name, v := range secrets {
		if err := os.WriteFile(filepath.Join(dir, name), []byte(v+"\n"), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	if err := hostlink.EnsureDir(env.InstallDir); err != nil {
		t.Fatal(err)
	}
	mk, _ := notify.DecodeMasterKey(wdMasterHex)
	blob, err := notify.SealEnvelope(mk, []byte(`{"token":"`+wdToken+`","chat_id":"987654321"}`), []byte(notify.TelegramAAD))
	if err != nil {
		t.Fatal(err)
	}
	tg, _ := json.Marshal(map[string]any{"schema": 1, "enabled": true, "enc": base64.StdEncoding.EncodeToString(blob)})
	if err := os.WriteFile(hostlink.TelegramConfigPath(env.InstallDir), tg, 0o644); err != nil {
		t.Fatal(err)
	}
	lits := []string{b.MasterKey, b.DBPassword, b.AppDBPassword, b.BackupKey, b.SetupToken, wdToken, "K7QX2ABCDEFGHIJKLMNOPQRSTUVWXY"}
	for _, v := range secrets {
		lits = append(lits, v)
	}
	return env, lits
}

// doctorRunner trả log/phiên bản có NHÉT mọi bí mật (giả lập log rò rỉ).
func doctorRunner(lits []string) *fake.Runner {
	leak := strings.Join(lits, " | ") + "\nDATABASE_URL=postgresql://gh:pw@db:5432/gh\nPOST https://api.telegram.org/bot" + wdToken + "/sendMessage\n"
	exact := func(args ...string) func(dockercli.Cmd) bool {
		return func(c dockercli.Cmd) bool { return strings.Join(c.Args, " ") == strings.Join(args, " ") }
	}
	return &fake.Runner{Responses: []fake.Response{
		{Match: fake.MatchArgsContain("version", "--format"), Output: []byte("27.1.0")},
		{Match: fake.MatchArgsContain("system", "df", "-v"), Output: []byte("")},
		{Match: fake.MatchArgsContain("logs", "-t", "--tail=2000"), Output: []byte("2026-10-03T10:00:00Z api | khởi động " + leak)},
		{Match: exact("version"), Output: []byte("Client: Docker Engine 27.1.0\nEnv: " + leak)},
		{Match: exact("compose", "version"), Output: []byte("Docker Compose version v2.29.1")},
		{Match: fake.MatchArgsContain("alembic_version"), Output: []byte("0029\n")},
		{Match: fake.MatchArgsContain("ps", "--all", "--format", "json"), Output: []byte(`[{"Service":"api","State":"running","Image":"ghcr.io/o/gen-harness-api:v0.1.44"}]`)},
		{Match: fake.MatchArgsContain("image", "inspect"), Output: []byte(`["ghcr.io/o/gen-harness-api@sha256:abc123"]`)},
		{Output: nil},
	}}
}

func doctorTestDeps(r dockercli.Runner, now time.Time) DoctorDeps {
	return DoctorDeps{
		Runner:  r,
		DialTCP: func(string, time.Duration) error { return nil },
		DialTLS: func(string, time.Duration) (string, time.Time, error) {
			return "CN=test", time.Now().Add(time.Hour), nil
		},
		Version: "v0.1.44-test",
		Now:     func() time.Time { return now },
		GOOS:    "linux",
		UID:     "1000",
	}
}

func readZip(t *testing.T, path string) map[string]string {
	t.Helper()
	zr, err := zip.OpenReader(path)
	if err != nil {
		t.Fatal(err)
	}
	defer func() { _ = zr.Close() }()
	out := map[string]string{}
	for _, f := range zr.File {
		rc, err := f.Open()
		if err != nil {
			t.Fatal(err)
		}
		b, _ := io.ReadAll(rc)
		_ = rc.Close()
		out[f.Name] = string(b)
	}
	return out
}

func TestDoctorBundleHasNoSecrets(t *testing.T) {
	env, lits := doctorInstall(t)
	// Log genh + tệp trạng thái host cũng nhét bí mật.
	logs := config.New(env.InstallDir).LogsDir()
	_ = os.MkdirAll(logs, 0o755)
	_ = os.WriteFile(filepath.Join(logs, "auto-update.log"), []byte("lần chạy đêm: setup token "+lits[4]+"\n"), 0o644)
	_ = hostlink.Start(env.InstallDir, "v0.1.43")
	_ = hostlink.Finish(env.InstallDir, "failed", "v0.1.44", "lỗi kết nối postgresql://gh:"+lits[1]+"@db/gh")
	now := time.Date(2026, 10, 3, 10, 0, 0, 0, time.UTC)
	if err := os.WriteFile(hostlink.DoctorRequestPath(env.InstallDir), []byte(`{"schema":1,"request_id":"0123456789abcdef","requested_at":"x"}`), 0o666); err != nil {
		t.Fatal(err)
	}
	var out strings.Builder
	if err := RunDoctorRequest(context.Background(), env, doctorTestDeps(doctorRunner(lits), now), &out); err != nil {
		t.Fatalf("RunDoctorRequest: %v", err)
	}
	st, err := hostlink.ReadDoctorStatus(env.InstallDir)
	if err != nil || st.State != "done" || st.File != "genh-doctor-20261003T100000Z.zip" {
		t.Fatalf("doctor-status = %+v, %v", st, err)
	}
	entries := readZip(t, filepath.Join(hostlink.DiagnosticsDirPath(env.InstallDir), st.File))
	for _, want := range []string{"report.txt", "logs.txt", "versions.txt", "genh-logs/auto-update.log", "host/update-status.json", "manifest.json"} {
		if _, ok := entries[want]; !ok {
			t.Errorf("gói thiếu %s (có: %v)", want, keysOf(entries))
		}
	}
	for name, body := range entries {
		low := strings.ToLower(name)
		if strings.Contains(low, "telegram") || strings.Contains(low, "secret") || strings.Contains(low, "offsite.json") || strings.HasSuffix(low, ".env") {
			t.Errorf("gói không được chứa %s", name)
		}
		for _, lit := range lits {
			if strings.Contains(body, lit) {
				t.Errorf("%s còn bí mật (độ dài %d)", name, len(lit))
			}
		}
		if strings.Contains(body, "gh:pw@") || strings.Contains(body, "AAFakeToken") {
			t.Errorf("%s còn mật khẩu URL/token Telegram", name)
		}
	}
	v := entries["versions.txt"]
	for _, want := range []string{"genh: v0.1.44-test", "0029", "ghcr.io/o/gen-harness-api:v0.1.44 → [\"ghcr.io/o/gen-harness-api@sha256:abc123\"]", "Docker Compose version"} {
		if !strings.Contains(v, want) {
			t.Errorf("versions.txt thiếu %q:\n%s", want, v)
		}
	}
	if !strings.Contains(entries["logs.txt"], "postgresql://***:***@db") {
		t.Errorf("logs.txt phải che userinfo URL:\n%s", entries["logs.txt"])
	}
	var mf map[string]any
	if err := json.Unmarshal([]byte(entries["manifest.json"]), &mf); err != nil {
		t.Fatal(err)
	}
	if n, _ := mf["redactions"].(float64); n < float64(len(lits)) {
		t.Errorf("manifest redactions = %v, phải ≥ %d", mf["redactions"], len(lits))
	}
	if strings.Contains(out.String(), wdToken) {
		t.Error("out lộ token")
	}
}

// `genh doctor --out` (gõ tay) cũng lọc bí mật.
func TestRunDoctor_OutZipCungLocBiMat(t *testing.T) {
	env, lits := doctorInstall(t)
	outPath := filepath.Join(t.TempDir(), "r.zip")
	if err := RunDoctor(context.Background(), env, outPath, doctorTestDeps(doctorRunner(lits), time.Now()), &strings.Builder{}); err != nil {
		t.Fatal(err)
	}
	for name, body := range readZip(t, outPath) {
		for _, lit := range lits {
			if strings.Contains(body, lit) {
				t.Errorf("%s còn bí mật", name)
			}
		}
	}
}

func keysOf(m map[string]string) []string {
	var out []string
	for k := range m {
		out = append(out, k)
	}
	return out
}
