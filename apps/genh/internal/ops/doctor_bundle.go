package ops

import (
	"archive/zip"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"time"

	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/compose"
	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/config"
	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/dockercli"
	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/hostlink"
	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/notify"
	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/redact"
)

// ─── Gói chẩn đoán ĐÃ LỌC BÍ MẬT (v0.1.44, F-4b) ────────────────────────────
//
// Dùng chung cho `genh doctor [--out f]` và nút "Gói chẩn đoán" trong Console
// (run/request/doctor.json → `genh doctor --if-requested`). MỌI mục văn bản đi
// qua redact.Redactor (literal từ config/secrets.json + secrets/* + token
// Telegram giải mã được, cộng mẫu token/mật khẩu). TUYỆT ĐỐI không đưa vào:
// secrets/, config/secrets.json, .env, run/telegram.json, config/offsite.json.

const (
	doctorLogsTail   = "--tail=2000"
	doctorGenhLogMax = 1 << 20
	doctorKeepZips   = 3
	// doctorZipMaxAge: zip (log đầy đủ mọi dịch vụ, có thể chứa dữ liệu khách)
	// phải 0644 để api (uid khác) đọc — nên không để lâu: quá hạn này thì xoá
	// (lượt trực canh 12 phút và lần tạo gói sau đều dọn).
	doctorZipMaxAge     = 24 * time.Hour
	doctorZipPrefix     = "genh-doctor-"
	doctorRequestTimout = 10 * time.Minute
)

// doctorGenhLogs là log của genh (logs/) đưa vào gói (đuôi ≤ 1 MiB).
var doctorGenhLogs = []string{"auto-update.log", "offsite.log", "watchdog.log"}

// doctorHostFiles là tệp trạng thái trong run/ đưa vào gói (đọc an toàn).
// KHÔNG có telegram.json (token mã hoá), api-health.json (không cần).
var doctorHostFiles = []string{"update-status", "restore-status", "disk-status", "autostart-status", "offsite-status",
	"genh", "update-blocked", "watchdog-status", "doctor-status", "network-status", "nightly-status"}

var doctorZipRe = regexp.MustCompile(`^genh-doctor-\d{8}T\d{6}Z\.zip$`)

// bundleEntry là một mục trong zip (đã che).
type bundleEntry struct {
	Name string
	Data []byte
}

// doctorRedactor dựng bộ che bí mật của bản cài (kể cả khi chưa tìm được compose.yaml).
func doctorRedactor(env *Env, composePath string) *redact.Redactor {
	if composePath == "" {
		composePath = filepath.Join(env.InstallDir, "deploy", "compose.yaml")
	}
	var extra []string
	if cfg, err := notify.LoadTelegramConfig(env.InstallDir, composePath); err == nil {
		extra = append(extra, cfg.Token)
	}
	return redact.New(redact.SecretsFromInstall(env.InstallDir, composePath, extra...)...)
}

// collectBundle gom mọi mục của gói chẩn đoán (CHƯA che — writeBundleZip che).
func collectBundle(ctx context.Context, env *Env, runner dockercli.Runner, deps DoctorDeps, lines []diagLine, now time.Time) []bundleEntry {
	var entries []bundleEntry

	var report strings.Builder
	report.WriteString("Gen-Harness — báo cáo chẩn đoán (genh doctor)\n")
	report.WriteString("Sinh lúc: " + now.UTC().Format(time.RFC3339) + "\n\n")
	for _, l := range lines {
		report.WriteString(l.String() + "\n")
	}
	entries = append(entries, bundleEntry{"report.txt", []byte(report.String())})

	composePath, locErr := env.LocatePath()
	var envOverlay []string
	if locErr == nil {
		if bundle, err := env.LoadSecrets(); err == nil {
			envOverlay = EnvOverlay(bundle)
		} else {
			locErr = err
		}
	}
	dockerCompose := func(timeout time.Duration, args ...string) ([]byte, error) {
		if locErr != nil {
			return nil, locErr
		}
		cctx, cancel := context.WithTimeout(ctx, timeout)
		defer cancel()
		return runner.Output(cctx, dockercli.Cmd{Name: "docker", Args: compose.BaseArgs(composePath, args...), Env: envOverlay, Dir: composeDir(composePath)})
	}
	docker := func(args ...string) ([]byte, error) {
		cctx, cancel := context.WithTimeout(ctx, 30*time.Second)
		defer cancel()
		return runner.Output(cctx, dockercli.Cmd{Name: "docker", Args: args})
	}

	// logs.txt: mọi service, có dấu thời gian.
	if out, err := dockerCompose(2*time.Minute, "logs", "-t", doctorLogsTail); err != nil {
		entries = append(entries, bundleEntry{"logs.txt", []byte("không lấy được log: " + err.Error() + "\n")})
	} else {
		entries = append(entries, bundleEntry{"logs.txt", out})
	}

	// genh-logs/*: đuôi ≤ 1 MiB.
	logsDir := config.New(env.InstallDir).LogsDir()
	for _, name := range doctorGenhLogs {
		if b, err := tailFile(filepath.Join(logsDir, name), doctorGenhLogMax); err == nil {
			entries = append(entries, bundleEntry{"genh-logs/" + name, b})
		}
	}

	// host/*.json: đọc AN TOÀN (run/ do api ghi được).
	for _, name := range doctorHostFiles {
		if b, err := hostlink.ReadStateFileSafe(filepath.Join(hostlink.Dir(env.InstallDir), name+".json")); err == nil {
			entries = append(entries, bundleEntry{"host/" + name + ".json", b})
		}
	}

	// versions.txt.
	var v strings.Builder
	ver := deps.Version
	if ver == "" {
		ver = "không rõ"
	}
	v.WriteString("genh: " + ver + "\n\n== docker version ==\n")
	v.WriteString(cmdOut(docker("version")))
	v.WriteString("\n== docker compose version ==\n")
	v.WriteString(cmdOut(docker("compose", "version")))
	v.WriteString("\n== alembic (revision CSDL) ==\n")
	v.WriteString(cmdOut(dockerCompose(30*time.Second, "exec", "-T", "db", "sh", "-c",
		`psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc "SELECT version_num FROM alembic_version"`)))
	v.WriteString("\n== ảnh (digest) ==\n")
	refs := map[string]bool{}
	if locErr == nil {
		if data, err := os.ReadFile(composePath); err == nil {
			for _, r := range imageRefsFromCompose(data) {
				refs[r] = true
			}
		}
	}
	if out, err := dockerCompose(30*time.Second, "ps", "--all", "--format", "json"); err == nil {
		if rows, perr := parsePSRows(out); perr == nil {
			for _, r := range rows {
				if r.Image != "" {
					refs[r.Image] = true
				}
			}
		}
	}
	sorted := make([]string, 0, len(refs))
	for r := range refs {
		sorted = append(sorted, r)
	}
	sort.Strings(sorted)
	if len(sorted) == 0 {
		v.WriteString("(không tìm thấy ảnh nào)\n")
	}
	for _, r := range sorted {
		out, err := docker("image", "inspect", "--format", "{{json .RepoDigests}}", r)
		if err != nil {
			v.WriteString(r + " → lỗi: " + shortText(err.Error(), 200) + "\n")
			continue
		}
		v.WriteString(r + " → " + strings.TrimSpace(string(out)) + "\n")
	}
	entries = append(entries, bundleEntry{"versions.txt", []byte(v.String())})
	return entries
}

func cmdOut(out []byte, err error) string {
	if err != nil {
		return "lỗi: " + shortText(err.Error(), 500) + "\n"
	}
	s := strings.TrimRight(string(out), "\n")
	if s == "" {
		s = "(trống)"
	}
	return s + "\n"
}

// tailFile đọc ≤ max byte cuối của một tệp thường (không theo symlink).
func tailFile(path string, max int64) ([]byte, error) {
	fi, err := os.Lstat(path)
	if err != nil {
		return nil, err
	}
	if !fi.Mode().IsRegular() {
		return nil, fmt.Errorf("%s không phải tệp thường", path)
	}
	f, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	defer func() { _ = f.Close() }()
	if fi.Size() > max {
		if _, err := f.Seek(fi.Size()-max, io.SeekStart); err != nil {
			return nil, err
		}
	}
	return io.ReadAll(io.LimitReader(f, max))
}

// writeBundleZip ghi các mục (ĐÃ CHE bằng red) + manifest.json vào w.
func writeBundleZip(w io.Writer, entries []bundleEntry, red *redact.Redactor, now time.Time) error {
	zw := zip.NewWriter(w)
	type mf struct {
		Name string `json:"name"`
		Size int    `json:"size"`
	}
	var files []mf
	for _, e := range entries {
		data := red.Bytes(e.Data)
		fw, err := zw.CreateHeader(&zip.FileHeader{Name: e.Name, Method: zip.Deflate, Modified: now})
		if err != nil {
			return err
		}
		if _, err := fw.Write(data); err != nil {
			return err
		}
		files = append(files, mf{e.Name, len(data)})
	}
	manifest, _ := json.MarshalIndent(map[string]any{
		"schema":         1,
		"generated_at":   now.UTC().Format(time.RFC3339),
		"files":          files,
		"redactions":     red.Count(),
		"redacted_note":  "Mọi bí mật đã thay bằng ***; không kèm secrets/, config/secrets.json, .env, run/telegram.json, config/offsite.json.",
		"literal_values": red.LiteralCount(),
	}, "", "  ")
	fw, err := zw.CreateHeader(&zip.FileHeader{Name: "manifest.json", Method: zip.Deflate, Modified: now})
	if err != nil {
		return err
	}
	if _, err := fw.Write(append(manifest, '\n')); err != nil {
		return err
	}
	return zw.Close()
}

// writeDoctorBundleFile ghi gói chẩn đoán ra outPath (tệp tạm O_EXCL cùng thư
// mục rồi rename) với quyền perm.
func writeDoctorBundleFile(outPath string, perm os.FileMode, entries []bundleEntry, red *redact.Redactor, now time.Time) error {
	dir := mustAbsDir(outPath)
	if err := os.MkdirAll(dir, 0o755); err != nil {
		return err
	}
	f, err := os.CreateTemp(dir, ".genh-doctor-*.tmp")
	if err != nil {
		return err
	}
	tmp := f.Name()
	ok := false
	defer func() {
		if !ok {
			_ = os.Remove(tmp)
		}
	}()
	if err := writeBundleZip(f, entries, red, now); err != nil {
		_ = f.Close()
		return err
	}
	if err := f.Chmod(perm); err != nil {
		_ = f.Close()
		return err
	}
	if err := f.Close(); err != nil {
		return err
	}
	if fi, err := os.Stat(outPath); err == nil && fi.IsDir() {
		return fmt.Errorf("%s là thư mục", outPath)
	}
	if err := os.Rename(tmp, outPath); err != nil {
		return err
	}
	ok = true
	return nil
}

// failUndeletableDoctor: tệp yêu cầu còn nguyên sau khi đã cố xoá ⇒ ghi doctor-status
// failed GH-E94C (idempotent theo request_id) và thôi. Tệp yêu cầu đã biến mất ⇒ không làm gì.
func failUndeletableDoctor(env *Env, req hostlink.DoctorRequest, out io.Writer) {
	if !hostlink.HasDoctorRequest(env.InstallDir) {
		return
	}
	if prev, err := hostlink.ReadDoctorStatus(env.InstallDir); err == nil && prev.State == "failed" &&
		prev.ErrorCode == ErrCodeRequestUndeletable && prev.RequestID == req.RequestID {
		return
	}
	t := time.Now().UTC().Format(time.RFC3339)
	_ = hostlink.WriteDoctorStatus(env.InstallDir, hostlink.DoctorStatus{
		RequestID: req.RequestID, State: "failed", StartedAt: t, FinishedAt: t,
		ErrorCode: ErrCodeRequestUndeletable, Message: RequestUndeletableMessage("yêu cầu gói chẩn đoán"),
	})
	_, _ = fmt.Fprintln(out, "genh: "+RequestUndeletableMessage("yêu cầu gói chẩn đoán"))
}

// RunDoctorRequest làm yêu cầu "Gói chẩn đoán" của Console: đọc + xoá
// run/request/doctor.json (request_id sai dạng ⇒ bỏ), báo running, tạo zip ĐÃ
// LỌC BÍ MẬT vào run/diagnostics/genh-doctor-<UTC>.zip (0644 để api đọc), báo
// done {file, size_bytes, sha256} hoặc failed {error_code, message}; giữ 3 zip
// mới nhất. KHÔNG lấy khoá loại trừ (chỉ đọc).
func RunDoctorRequest(ctx context.Context, env *Env, deps DoctorDeps, out io.Writer) error {
	req, err := hostlink.ConsumeDoctorRequest(env.InstallDir)
	if err != nil {
		if errors.Is(err, os.ErrNotExist) {
			return nil
		}
		_, _ = fmt.Fprintln(out, "Bỏ yêu cầu gói chẩn đoán không hợp lệ: "+shortText(err.Error(), 200))
		failUndeletableDoctor(env, req, out)
		return nil
	}
	if hostlink.HasDoctorRequest(env.InstallDir) {
		// Đọc được nhưng KHÔNG xoá được (quyền…): không làm — xoá-trước-khi-làm là thứ
		// giữ cho watcher không kích lặp (GH-E94C, v0.1.53).
		failUndeletableDoctor(env, req, out)
		return nil
	}
	ctx, cancel := context.WithTimeout(ctx, doctorRequestTimout)
	defer cancel()
	now := deps.now()
	st := hostlink.DoctorStatus{RequestID: req.RequestID, State: "running", StartedAt: now.Format(time.RFC3339)}
	_ = hostlink.WriteDoctorStatus(env.InstallDir, st)

	fail := func(err error) error {
		st.State, st.FinishedAt = "failed", deps.now().Format(time.RFC3339)
		st.ErrorCode = ErrCodeDoctorBundleFailed
		st.Message = "Không tạo được gói chẩn đoán. Thử lại sau ít phút; nếu vẫn lỗi, chạy genh doctor trên máy chủ."
		_ = hostlink.WriteDoctorStatus(env.InstallDir, st)
		return &OpError{Code: ErrCodeDoctorBundleFailed, What: "Không tạo được gói chẩn đoán cho Console", Why: err.Error(),
			Next: "Kiểm quyền ghi thư mục run/diagnostics dưới thư mục cài đặt rồi bấm lại trong Console.", Err: err}
	}

	dir, err := hostlink.EnsureDiagnosticsDir(env.InstallDir)
	if err != nil {
		return fail(err)
	}
	runner := deps.runner()
	lines, as := collectDoctorLines(ctx, env, deps, runner)
	writeAutostartStatus(env, as, out)
	composePath, _ := env.LocatePath()
	red := doctorRedactor(env, composePath)
	entries := collectBundle(ctx, env, runner, deps, lines, now)
	name := doctorZipPrefix + now.UTC().Format("20060102T150405Z") + ".zip"
	path := filepath.Join(dir, name)
	if err := writeDoctorBundleFile(path, 0o644, entries, red, now); err != nil {
		return fail(err)
	}
	sum, size, err := fileSHA256(path)
	if err != nil {
		return fail(err)
	}
	pruneDoctorZips(dir, doctorKeepZips, now)
	st.State, st.FinishedAt = "done", deps.now().Format(time.RFC3339)
	st.File, st.SizeBytes, st.SHA256 = name, size, sum
	if err := hostlink.WriteDoctorStatus(env.InstallDir, st); err != nil {
		return fail(err)
	}
	_, _ = fmt.Fprintf(out, "Gói chẩn đoán (đã lọc bí mật): %s (%d byte)\n", path, size)
	return nil
}

func fileSHA256(path string) (string, int64, error) {
	f, err := os.Open(path)
	if err != nil {
		return "", 0, err
	}
	defer func() { _ = f.Close() }()
	h := sha256.New()
	n, err := io.Copy(h, f)
	if err != nil {
		return "", 0, err
	}
	return hex.EncodeToString(h.Sum(nil)), n, nil
}

// pruneDoctorZips chỉ giữ keep zip mới nhất (tên chứa thời điểm UTC ⇒ sắp xếp
// theo tên là theo thời gian) và xoá zip cũ hơn doctorZipMaxAge. Thư mục là
// symlink/không phải thư mục thật ⇒ không đụng.
func pruneDoctorZips(dir string, keep int, now time.Time) {
	if fi, err := os.Lstat(dir); err != nil || !fi.IsDir() {
		return
	}
	ents, err := os.ReadDir(dir)
	if err != nil {
		return
	}
	var names []string
	for _, e := range ents {
		if e.Type().IsRegular() && doctorZipRe.MatchString(e.Name()) {
			names = append(names, e.Name())
		}
	}
	sort.Sort(sort.Reverse(sort.StringSlice(names)))
	for i, n := range names {
		old := false
		if t, err := time.Parse("20060102T150405Z", strings.TrimSuffix(strings.TrimPrefix(n, doctorZipPrefix), ".zip")); err == nil {
			old = now.Sub(t) > doctorZipMaxAge
		}
		if i >= keep || old {
			_ = os.Remove(filepath.Join(dir, n))
		}
	}
}

// PruneDiagnostics dọn zip chẩn đoán quá hạn (gọi từ lượt trực canh định kỳ).
func PruneDiagnostics(installDir string, now time.Time) {
	pruneDoctorZips(hostlink.DiagnosticsDirPath(installDir), doctorKeepZips, now)
}
