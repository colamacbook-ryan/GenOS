package ops

// `genh offsite` (v0.1.40, F-12) — "Bản sao ngoài máy": mỗi tuần xuất một gói
// .ghbundle (CSDL + tệp + khoá, mã hoá bằng "Khoá khôi phục" secrets/gh_offsite_key)
// ra một thư mục Owner chọn trên ổ USB/NAS, rồi TỰ KIỂM gói vừa ghi đọc lại
// được (gh.bundle verify). Không có S3.
//
//   - Cấu hình: <gốc cài>/config/offsite.json (0600, chỉ genh đọc) —
//     {"path","allow_same_disk","set_at","keep":4}.
//   - Trạng thái cho Console: run/offsite-status.json (hostlink.OffsiteStatus).
//   - Yêu cầu từ Console: run/request/offsite.json — run/ 0777 nên KHÔNG tin
//     cậy: path đi qua đúng bộ kiểm của CLI, Console không bao giờ bật được
//     allow_same_disk.
//   - Đích chưa mount (USB rút ra ⇒ thư mục mount rỗng nằm trên ổ chính) ⇒
//     GH-EB01, KHÔNG tạo/ghi gì.

import (
	"context"
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
	"unicode"
	"unicode/utf8"

	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/autoupdate"
	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/compose"
	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/config"
	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/dockercli"
	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/hostlink"
	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/machine"
)

const (
	// offsiteConfigFile nằm trong <gốc cài>/config/ (KHÔNG trong run/).
	offsiteConfigFile = "offsite.json"
	// OffsiteSubdir là thư mục con genh tạo trong đích (đích gốc thì không bao giờ tạo).
	OffsiteSubdir = "gen-harness-offsite"
	// offsiteDefaultKeep: số gói mới nhất giữ lại.
	offsiteDefaultKeep = 4
	// offsiteMaxPathLen: giới hạn độ dài đường dẫn đích (yêu cầu từ Console không tin cậy).
	offsiteMaxPathLen = 400
	// offsiteProbeFile: tệp thăm dò quyền ghi (tạo rồi xoá ngay).
	offsiteProbeFile = ".genh-probe"
	// defaultOffsiteLockWait: chờ khoá loại trừ tối đa (update/restore/import đang chạy).
	defaultOffsiteLockWait = 2 * time.Minute
	// offsiteRecentWindow: "bản sao ngoài máy gần đây" (cảnh báo khi uninstall --delete-data).
	offsiteRecentWindow = 7 * 24 * time.Hour
	// OffsiteSameDiskWarning: cảnh báo khi Owner tự chọn --allow-same-disk.
	OffsiteSameDiskWarning = "Bản sao nằm cùng ổ với máy chủ — hỏng ổ là mất cả hai"
)

// offsiteBundleRe: mẫu tên gói genh tạo — xoay vòng CHỈ đụng tệp đúng mẫu này.
var offsiteBundleRe = regexp.MustCompile(`^gen-harness-\d{8}T\d{6}Z\.ghbundle$`)

// OffsiteConfig là nội dung <gốc cài>/config/offsite.json.
type OffsiteConfig struct {
	Path          string `json:"path"`
	AllowSameDisk bool   `json:"allow_same_disk"`
	SetAt         string `json:"set_at"`
	Keep          int    `json:"keep"`
	// Disabled: `genh offsite disable` — giữ đường dẫn để bật lại dễ, nhưng coi như chưa cấu hình.
	Disabled bool `json:"disabled,omitempty"`
}

func offsiteConfigPath(installDir string) string {
	return filepath.Join(installDir, "config", offsiteConfigFile)
}

// loadOffsiteConfig đọc config/offsite.json — ok=false nếu chưa có.
func loadOffsiteConfig(installDir string) (OffsiteConfig, bool, error) {
	var c OffsiteConfig
	b, err := os.ReadFile(offsiteConfigPath(installDir))
	if errors.Is(err, os.ErrNotExist) {
		return c, false, nil
	}
	if err != nil {
		return c, false, err
	}
	if err := json.Unmarshal(b, &c); err != nil {
		return c, false, err
	}
	return c, true, nil
}

// saveOffsiteConfig ghi nguyên tử config/offsite.json quyền 0600.
func saveOffsiteConfig(installDir string, c OffsiteConfig) error {
	dir := filepath.Dir(offsiteConfigPath(installDir))
	if err := os.MkdirAll(dir, 0o700); err != nil {
		return err
	}
	b, err := json.MarshalIndent(c, "", "  ")
	if err != nil {
		return err
	}
	f, err := os.CreateTemp(dir, offsiteConfigFile+".*.tmp")
	if err != nil {
		return err
	}
	tmp := f.Name()
	if _, err := f.Write(append(b, '\n')); err != nil {
		_ = f.Close()
		_ = os.Remove(tmp)
		return err
	}
	if err := f.Close(); err != nil {
		_ = os.Remove(tmp)
		return err
	}
	_ = os.Chmod(tmp, 0o600)
	if err := os.Rename(tmp, offsiteConfigPath(installDir)); err != nil {
		_ = os.Remove(tmp)
		return err
	}
	return nil
}

func (c OffsiteConfig) keep() int {
	if c.Keep < 1 {
		return offsiteDefaultKeep
	}
	if c.Keep > 52 {
		return 52
	}
	return c.Keep
}

// OffsiteScheduler bật/tắt/hỏi lịch tuần (mặc định: internal/autoupdate theo
// hệ điều hành — tiêm giả khi test).
type OffsiteScheduler interface {
	Enable(ctx context.Context) (msg, mechanism string, err error)
	Disable(ctx context.Context) (string, error)
	Status(ctx context.Context) (autoupdate.OffsiteScheduleStatus, error)
}

type autoupdateOffsiteScheduler struct {
	deps autoupdate.Deps
	job  autoupdate.OffsiteJob
}

func (s autoupdateOffsiteScheduler) Enable(ctx context.Context) (string, string, error) {
	return autoupdate.EnableOffsite(ctx, s.deps, s.job)
}

func (s autoupdateOffsiteScheduler) Disable(ctx context.Context) (string, error) {
	return autoupdate.DisableOffsite(ctx, s.deps)
}

func (s autoupdateOffsiteScheduler) Status(ctx context.Context) (autoupdate.OffsiteScheduleStatus, error) {
	return autoupdate.OffsiteStatus(ctx, s.deps)
}

// NewOffsiteScheduler dựng lịch tuần thật cho bản cài env: `<genh> offsite run
// --quiet --install-dir <dir> [--port N]`, log logs/offsite.log, mang theo
// GENH_COMPOSE_FILE nếu phiên hiện tại có (như watcher).
func NewOffsiteScheduler(env *Env) OffsiteScheduler {
	genh, err := os.Executable()
	if err == nil {
		genh, _ = filepath.Abs(genh)
	} else {
		genh = ""
	}
	job := autoupdate.OffsiteJob{InstallDir: env.InstallDir}
	if env.Port > 0 && env.Port != machine.DefaultPort {
		job.Port = env.Port
	}
	if v := os.Getenv(compose.EnvOverrideVar); v != "" {
		job.Env = append(job.Env, compose.EnvOverrideVar+"="+v)
	}
	return autoupdateOffsiteScheduler{
		deps: autoupdate.Deps{GenhPath: genh, LogFile: filepath.Join(config.New(env.InstallDir).LogsDir(), "offsite.log"), InstallDir: env.InstallDir},
		job:  job,
	}
}

// OffsiteDeps cho phép tiêm giả khi test.
type OffsiteDeps struct {
	Runner    dockercli.Runner
	Scheduler OffsiteScheduler
	// SameDevice báo dest và installDir có nằm cùng một ổ/thiết bị không — nil
	// dùng sameDevice của hệ điều hành (Unix: Stat_t.Dev; Windows: tên ổ/UNC).
	SameDevice func(dest, installDir string) (bool, error)
	// VolatileFS trả kiểu hệ tệp tạm (tmpfs/ramfs/overlay) chứa dest, "" nếu
	// không — nil dùng volatileFS của hệ điều hành (Linux: /proc/self/mountinfo).
	VolatileFS func(dest string) string
	Now        func() time.Time
	// LockWait: thời gian tối đa chờ khoá loại trừ (0 = defaultOffsiteLockWait).
	LockWait time.Duration
}

func (d OffsiteDeps) now() time.Time {
	if d.Now != nil {
		return d.Now().UTC()
	}
	return time.Now().UTC()
}

func (d OffsiteDeps) scheduler(env *Env) OffsiteScheduler {
	if d.Scheduler != nil {
		return d.Scheduler
	}
	return NewOffsiteScheduler(env)
}

func (d OffsiteDeps) sameDevice() func(string, string) (bool, error) {
	if d.SameDevice != nil {
		return d.SameDevice
	}
	return sameDevice
}

func (d OffsiteDeps) volatileFS() func(string) string {
	if d.VolatileFS != nil {
		return d.VolatileFS
	}
	return volatileFS
}

func (d OffsiteDeps) runner() dockercli.Runner {
	if d.Runner != nil {
		return d.Runner
	}
	return dockercli.ExecRunner{}
}

func rfc3339(t time.Time) string { return t.UTC().Format(time.RFC3339) }

// ─── Kiểm đích ──────────────────────────────────────────────────────────────

func invalidDest(path, why string) *OpError {
	return &OpError{
		Code: ErrCodeOffsiteInvalidDest,
		What: "Nơi lưu bản sao ngoài máy không hợp lệ",
		Why:  why,
		Next: "Chọn một thư mục có sẵn trên ổ USB/NAS (đường dẫn tuyệt đối, ví dụ /media/usb hoặc E:\\), không nằm trong thư mục cài Gen-Harness.",
	}
}

func notMounted(path, why string, err error) *OpError {
	return &OpError{
		Code: ErrCodeOffsiteNotMounted,
		What: "Chưa thấy ổ USB/NAS tại " + path + " — cắm lại ổ rồi thử lại",
		Why:  why,
		Next: "Cắm/mount lại ổ USB/NAS rồi chạy `genh offsite run` (hoặc bấm \"Sao lưu ra ổ ngoài ngay\").",
		Err:  err,
	}
}

// cleanOffsitePath kiểm phần CÚ PHÁP của đường dẫn đích (dùng cho cả CLI lẫn
// yêu cầu Console): không rỗng, ≤ 400 byte, UTF-8 hợp lệ, không ký tự điều
// khiển, tuyệt đối. Trả đường dẫn đã Clean.
func cleanOffsitePath(path string) (string, *OpError) {
	if strings.TrimSpace(path) == "" {
		return "", invalidDest(path, "đường dẫn rỗng")
	}
	if len(path) > offsiteMaxPathLen {
		return "", invalidDest("", fmt.Sprintf("đường dẫn dài %d byte (tối đa %d)", len(path), offsiteMaxPathLen))
	}
	if !utf8.ValidString(path) {
		return "", invalidDest("", "đường dẫn không phải UTF-8 hợp lệ")
	}
	for _, r := range path {
		if unicode.IsControl(r) {
			return "", invalidDest("", "đường dẫn có ký tự điều khiển")
		}
	}
	if !filepath.IsAbs(path) {
		return "", invalidDest(path, "cần đường dẫn TUYỆT ĐỐI (ví dụ /media/usb), không phải "+path)
	}
	return filepath.Clean(path), nil
}

// resolveForCompare: EvalSymlinks nếu được (so "nằm trong" chính xác hơn), không thì Clean.
func resolveForCompare(p string) string {
	if r, err := filepath.EvalSymlinks(p); err == nil {
		return filepath.Clean(r)
	}
	return filepath.Clean(p)
}

// isWithin báo child == parent hoặc nằm bên trong parent.
func isWithin(child, parent string) bool {
	rel, err := filepath.Rel(parent, child)
	if err != nil {
		return false
	}
	return rel == "." || (rel != ".." && !strings.HasPrefix(rel, ".."+string(filepath.Separator)))
}

// checkOffsiteDest kiểm đích TỒN TẠI, là thư mục, không nằm trong gốc cài và
// (trừ allowSameDisk) KHÁC thiết bị với gốc cài. KHÔNG tạo gì. forSet: "không
// phải thư mục" là GH-EB07 (Owner chọn sai) thay vì GH-EB01.
func checkOffsiteDest(dest, installDir string, allowSameDisk, forSet bool, same func(string, string) (bool, error), volatile func(string) string) (sameDisk bool, opErr *OpError) {
	if isWithin(resolveForCompare(dest), resolveForCompare(installDir)) || isWithin(dest, filepath.Clean(installDir)) {
		return false, invalidDest(dest, "thư mục nằm trong thư mục cài Gen-Harness ("+installDir+") — hỏng ổ là mất cả hai")
	}
	fi, err := os.Stat(dest)
	if err != nil {
		if errors.Is(err, os.ErrNotExist) {
			return false, notMounted(dest, "thư mục không tồn tại (ổ chưa cắm/chưa mount?)", err)
		}
		return false, notMounted(dest, err.Error(), err)
	}
	if !fi.IsDir() {
		if forSet {
			return false, invalidDest(dest, dest+" không phải thư mục")
		}
		return false, notMounted(dest, dest+" không phải thư mục", nil)
	}
	// tmpfs (/tmp, /dev/shm), ramfs, overlay: Dev khác ổ chính nên lọt qua kiểm
	// "khác ổ", nhưng bản sao mất khi khởi động lại / vẫn nằm trên ổ chính —
	// không bao giờ là ổ USB/NAS (kể cả --allow-same-disk).
	if volatile != nil {
		if kind := volatile(dest); kind != "" {
			return false, invalidDest(dest, "thư mục nằm trên hệ tệp tạm "+kind+" (bộ nhớ RAM/lớp ghi đè) — bản sao mất khi khởi động lại, không phải ổ USB/NAS")
		}
	}
	s, err := same(dest, installDir)
	if err != nil {
		return false, notMounted(dest, "không kiểm được ổ đĩa của thư mục: "+err.Error(), err)
	}
	if s && !allowSameDisk {
		return true, notMounted(dest, "thư mục nằm cùng ổ đĩa với máy chủ — ổ USB/NAS có thể chưa được cắm/mount (chỉ còn thư mục rỗng trên ổ chính)", nil)
	}
	return s, nil
}

// probeWritable tạo rồi xoá <dir>/.genh-probe để chắc chắn ghi được.
func probeWritable(dir string) *OpError {
	p := filepath.Join(dir, offsiteProbeFile)
	f, err := os.OpenFile(p, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0o600)
	if errors.Is(err, os.ErrExist) {
		_ = os.Remove(p) // tệp thăm dò cũ sót lại
		f, err = os.OpenFile(p, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0o600)
	}
	if err == nil {
		_, err = f.Write([]byte("genh"))
		if cerr := f.Close(); err == nil {
			err = cerr
		}
		if rerr := os.Remove(p); err == nil {
			err = rerr
		}
	}
	if err != nil {
		return &OpError{
			Code: ErrCodeOffsiteWriteFailed,
			What: "Không ghi được vào " + dir,
			Why:  err.Error(),
			Next: "Kiểm ổ USB/NAS còn chỗ trống, không bị khoá ghi và người dùng chạy genh có quyền ghi thư mục này.",
			Err:  err,
		}
	}
	return nil
}

// sameVolumeWindows (hàm thuần, test được trên mọi hệ): hai đường dẫn Windows
// cùng ổ đĩa? UNC (\\NAS\share) luôn coi là KHÁC ổ của máy chủ.
func sameVolumeWindows(dest, installDir string) bool {
	dv, iv := windowsVolume(dest), windowsVolume(installDir)
	if dv == "" || strings.HasPrefix(dv, `\\`) {
		return false
	}
	return strings.EqualFold(dv, iv)
}

func windowsVolume(p string) string {
	p = strings.ReplaceAll(p, "/", `\`)
	if strings.HasPrefix(p, `\\`) {
		parts := strings.SplitN(p[2:], `\`, 3)
		if len(parts) >= 2 {
			return `\\` + parts[0] + `\` + parts[1]
		}
		return p
	}
	if len(p) >= 2 && p[1] == ':' {
		return strings.ToUpper(p[:2])
	}
	return ""
}

// ─── Trạng thái ─────────────────────────────────────────────────────────────

// baseStatus dựng trạng thái nền: giữ thông tin lần thành công trước nếu cùng đích.
func baseStatus(installDir, dest string) hostlink.OffsiteStatus {
	prev, err := hostlink.ReadOffsiteStatus(installDir)
	st := hostlink.OffsiteStatus{Dest: dest}
	if err == nil && prev.Dest == dest {
		st.LastSuccessAt, st.LastFile, st.LastSizeBytes, st.Verified, st.Kept = prev.LastSuccessAt, prev.LastFile, prev.LastSizeBytes, prev.Verified, prev.Kept
		st.LastAttemptAt = prev.LastAttemptAt
		st.State, st.ErrorCode = prev.State, prev.ErrorCode
	}
	return st
}

// readOffsiteKey đọc secrets/gh_offsite_key (cạnh deploy/ của compose.yaml).
// KHÔNG bao giờ in/log giá trị.
func readOffsiteKey(composePath string) (string, error) {
	p := filepath.Join(filepath.Dir(composePath), "..", "secrets", offsiteKeyName)
	b, err := os.ReadFile(p)
	if err != nil {
		return "", err
	}
	k := strings.TrimSpace(string(b))
	if len(k) < minBundlePasswordLen {
		return "", fmt.Errorf("%s quá ngắn (cần ≥ %d ký tự)", offsiteKeyName, minBundlePasswordLen)
	}
	return k, nil
}

// offsiteKeyIDFor trả key_id của khoá khôi phục hiện có ("" nếu chưa đọc được).
func offsiteKeyIDFor(env *Env) string {
	composePath, err := env.LocatePath()
	if err != nil {
		return ""
	}
	k, err := readOffsiteKey(composePath)
	if err != nil {
		return ""
	}
	return offsiteKeyID(k)
}

func scheduleMechanism(ctx context.Context, s OffsiteScheduler) string {
	st, err := s.Status(ctx)
	if err != nil || !st.Enabled {
		return ""
	}
	return st.Mechanism
}

// OffsiteRecentSuccess báo run/offsite-status.json có last_success_at trong 7
// ngày gần nhất (dùng cho cảnh báo uninstall --delete-data).
func OffsiteRecentSuccess(installDir string, now time.Time) bool {
	st, err := hostlink.ReadOffsiteStatus(installDir)
	if err != nil || st.LastSuccessAt == "" {
		return false
	}
	t, err := time.Parse(time.RFC3339, st.LastSuccessAt)
	if err != nil {
		return false
	}
	return now.Sub(t) <= offsiteRecentWindow && now.Sub(t) >= -time.Hour
}

// ─── genh offsite set ───────────────────────────────────────────────────────

// OffsiteSetOptions là cờ của `genh offsite set`.
type OffsiteSetOptions struct {
	Path          string
	AllowSameDisk bool // CHỈ CLI — FromConsole thì luôn bị bỏ
	NoRun         bool
	// FromConsole: yêu cầu đến từ run/request/offsite.json (không tin cậy).
	FromConsole bool
	Quiet       bool
}

// RunOffsiteSet kiểm đích, lưu config/offsite.json, bật lịch tuần rồi (trừ
// NoRun) xuất bản sao đầu tiên ngay.
func RunOffsiteSet(ctx context.Context, env *Env, opts OffsiteSetOptions, deps OffsiteDeps, out io.Writer) error {
	allowSame := opts.AllowSameDisk && !opts.FromConsole
	now := deps.now()

	fail := func(e *OpError, state string) error {
		st := baseStatus(env.InstallDir, "")
		if cfg, ok, _ := loadOffsiteConfig(env.InstallDir); ok && !cfg.Disabled {
			st = baseStatus(env.InstallDir, cfg.Path)
			st.Configured = true
		}
		st.State, st.ErrorCode, st.LastAttemptAt = state, e.Code, rfc3339(now)
		if !st.Configured {
			st.State = hostlink.OffsiteStateNotConfigured
		}
		st.Schedule = scheduleMechanism(ctx, deps.scheduler(env))
		st.KeyID = offsiteKeyIDFor(env)
		_ = hostlink.WriteOffsiteStatus(env.InstallDir, st)
		return e
	}

	dest, opErr := cleanOffsitePath(opts.Path)
	if opErr != nil {
		return fail(opErr, hostlink.OffsiteStateFailed)
	}
	sameDisk, opErr := checkOffsiteDest(dest, env.InstallDir, allowSame, true, deps.sameDevice(), deps.volatileFS())
	if opErr != nil {
		state := hostlink.OffsiteStateFailed
		if opErr.Code == ErrCodeOffsiteNotMounted {
			state = hostlink.OffsiteStateNotMounted
		}
		return fail(opErr, state)
	}
	if sameDisk {
		_, _ = fmt.Fprintln(out, "CẢNH BÁO: "+OffsiteSameDiskWarning+". Bạn đã tự chọn --allow-same-disk — hãy chép gói ra ổ khác/ra khỏi máy định kỳ.")
	}
	if e := probeWritable(dest); e != nil {
		return fail(e, hostlink.OffsiteStateFailed)
	}

	cfg := OffsiteConfig{Path: dest, AllowSameDisk: allowSame, SetAt: rfc3339(now), Keep: offsiteDefaultKeep}
	if err := saveOffsiteConfig(env.InstallDir, cfg); err != nil {
		return &OpError{
			Code: ErrCodeOffsiteWriteFailed,
			What: "Không lưu được cấu hình bản sao ngoài máy",
			Why:  err.Error(),
			Next: "Kiểm quyền ghi thư mục " + filepath.Join(env.InstallDir, "config") + " rồi thử lại.",
			Err:  err,
		}
	}
	_, _ = fmt.Fprintln(out, "Đã chọn nơi lưu bản sao ngoài máy: "+dest)

	sched := deps.scheduler(env)
	mechanism := ""
	if msg, mech, err := sched.Enable(ctx); err != nil {
		_, _ = fmt.Fprintf(out, "Cảnh báo: chưa bật được lịch sao lưu ra ổ ngoài hằng tuần (%v) — chạy tay `genh offsite run` định kỳ, hoặc chạy lại `genh offsite set`.\n", err)
	} else {
		mechanism = mech
		_, _ = fmt.Fprintln(out, msg)
	}

	if opts.NoRun {
		st := baseStatus(env.InstallDir, dest)
		st.Configured, st.Schedule, st.KeyID = true, mechanism, offsiteKeyIDFor(env)
		if st.State == "" || st.State == hostlink.OffsiteStateNotConfigured {
			// Chưa có lần xuất nào cho đích này: "failed" không mã lỗi + last_success_at
			// rỗng — Console hiện "chưa có bản sao" cho tới lần chạy đầu (lịch/nút).
			st.State, st.ErrorCode = hostlink.OffsiteStateFailed, ""
		}
		_ = hostlink.WriteOffsiteStatus(env.InstallDir, st)
		_, _ = fmt.Fprintln(out, "Chưa xuất bản sao (--no-run) — lịch tuần sẽ chạy, hoặc chạy ngay `genh offsite run`.")
		return nil
	}
	return RunOffsiteRun(ctx, env, OffsiteRunOptions{Quiet: opts.Quiet}, deps, out)
}

// ─── genh offsite run ───────────────────────────────────────────────────────

// OffsiteRunOptions là cờ của `genh offsite run`.
type OffsiteRunOptions struct {
	Quiet bool
}

// RunOffsiteRun xuất + kiểm một bản sao ngoài máy. Chưa cấu hình → ghi
// not_configured, trả nil. Lỗi trả *OpError mã GH-EBxx (trạng thái đã ghi).
func RunOffsiteRun(ctx context.Context, env *Env, opts OffsiteRunOptions, deps OffsiteDeps, out io.Writer) error {
	progress := out
	if opts.Quiet {
		progress = io.Discard
	}
	now := deps.now()
	sched := deps.scheduler(env)

	cfg, ok, cerr := loadOffsiteConfig(env.InstallDir)
	if cerr != nil {
		_, _ = fmt.Fprintf(out, "genh: cảnh báo — config/offsite.json hỏng (%v), coi như chưa chọn nơi lưu.\n", cerr)
	}
	if !ok || cfg.Disabled || cfg.Path == "" {
		st := baseStatus(env.InstallDir, "")
		st.State, st.ErrorCode, st.Configured = hostlink.OffsiteStateNotConfigured, "", false
		st.KeyID = offsiteKeyIDFor(env)
		_ = hostlink.WriteOffsiteStatus(env.InstallDir, st)
		_, _ = fmt.Fprintln(out, "genh: chưa chọn nơi lưu bản sao ngoài máy — chạy `genh offsite set <thư mục trên ổ USB/NAS>` (hoặc \"Chọn nơi lưu bản sao ngoài máy\" trong Console).")
		return nil
	}

	st := baseStatus(env.InstallDir, cfg.Path)
	st.Configured = true
	st.LastAttemptAt = rfc3339(now)
	st.Schedule = scheduleMechanism(ctx, sched)
	st.KeyID = offsiteKeyIDFor(env)
	finish := func(state string, e *OpError) error {
		st.State = state
		st.ErrorCode = ""
		if e != nil {
			st.ErrorCode = e.Code
		}
		_ = hostlink.WriteOffsiteStatus(env.InstallDir, st)
		if e != nil {
			return e
		}
		return nil
	}

	// 1. Đích còn đó và đúng là ổ ngoài — KHÔNG tạo gì nếu chưa thấy.
	if _, e := checkOffsiteDest(cfg.Path, env.InstallDir, cfg.AllowSameDisk, false, deps.sameDevice(), deps.volatileFS()); e != nil {
		return finish(hostlink.OffsiteStateNotMounted, e)
	}

	// 2. Khoá loại trừ (cùng khoá với update/restore/import).
	wait := deps.LockWait
	if wait <= 0 {
		wait = defaultOffsiteLockWait
	}
	lock, lerr := hostlink.AcquireLockWait(ctx, env.InstallDir, wait)
	switch {
	case lerr == nil:
		defer lock.Release()
	case errors.Is(lerr, hostlink.ErrLockBusy) || ctx.Err() != nil:
		return finish(hostlink.OffsiteStateSkippedBusy, &OpError{
			Code: ErrCodeOffsiteBusy,
			What: "Chưa sao lưu ra ổ ngoài — đang có một lần cập nhật/khôi phục khác chạy",
			Why:  lerr.Error(),
			Next: "Đợi lần đó xong rồi chạy lại `genh offsite run` (lịch tuần sẽ tự thử lại tuần sau).",
			Err:  lerr,
		})
	default:
		_, _ = fmt.Fprintf(out, "genh: cảnh báo — không lấy được khoá loại trừ (%v); vẫn chạy tiếp.\n", lerr)
	}
	stopBeat := hostlink.StartHeartbeat(env.InstallDir, "offsite")
	defer stopBeat()
	_ = finish(hostlink.OffsiteStateRunning, nil)

	// 3. Khoá khôi phục + dịch vụ đang chạy.
	composePath, err := env.LocatePath()
	if err != nil {
		var oe *OpError
		if errors.As(err, &oe) {
			return finish(hostlink.OffsiteStateFailed, &OpError{Code: ErrCodeOffsiteNotRunning, What: "Dịch vụ Gen-Harness chưa chạy — chưa sao lưu ra ổ ngoài", Why: oe.Error(), Next: oe.Next, Err: err})
		}
		return finish(hostlink.OffsiteStateFailed, &OpError{Code: ErrCodeOffsiteNotRunning, What: "Dịch vụ Gen-Harness chưa chạy — chưa sao lưu ra ổ ngoài", Why: err.Error(), Err: err})
	}
	key, err := readOffsiteKey(composePath)
	if err != nil {
		return finish(hostlink.OffsiteStateFailed, &OpError{
			Code: ErrCodeOffsiteExportFailed,
			What: "Chưa có Khoá khôi phục (secrets/" + offsiteKeyName + ") — chưa sao lưu ra ổ ngoài",
			Why:  err.Error(),
			Next: "Chạy `genh status` (genh tự sinh khoá còn thiếu) rồi `genh offsite run`.",
			Err:  err,
		})
	}
	st.KeyID = offsiteKeyID(key)
	if e := checkAPIRunning(ctx, env, composePath, deps.runner()); e != nil {
		return finish(hostlink.OffsiteStateFailed, e)
	}

	// 4. Thư mục con trong đích (đích đã xác nhận là ổ ngoài).
	sub := filepath.Join(cfg.Path, OffsiteSubdir)
	if err := os.MkdirAll(sub, 0o700); err != nil {
		return finish(hostlink.OffsiteStateFailed, &OpError{
			Code: ErrCodeOffsiteWriteFailed,
			What: "Không tạo được thư mục " + sub,
			Why:  err.Error(),
			Next: "Kiểm ổ USB/NAS còn chỗ trống và cho phép ghi rồi thử lại.",
			Err:  err,
		})
	}

	// 5. Xuất gói (mật khẩu = khoá khôi phục, chỉ qua env của tiến trình con).
	name := "gen-harness-" + now.Format("20060102T150405Z") + ".ghbundle"
	file := filepath.Join(sub, name)
	_, _ = fmt.Fprintln(progress, "Đang xuất bản sao ngoài máy ra "+file+"…")
	if err := exportBundle(ctx, env, file, key, ExportDeps{Runner: deps.Runner}, progress); err != nil {
		code := ErrCodeOffsiteExportFailed
		var oe *OpError
		if errors.As(err, &oe) && oe.Code == ErrCodeExportWriteFailed {
			code = ErrCodeOffsiteWriteFailed
		}
		what := "Xuất bản sao ngoài máy thất bại"
		if code == ErrCodeOffsiteWriteFailed {
			what = "Không ghi được bản sao vào " + sub
		}
		return finish(hostlink.OffsiteStateFailed, &OpError{
			Code: code, What: what, Why: err.Error(),
			Next: "Kiểm `genh status` (db/api phải healthy) và ổ USB/NAS còn chỗ, rồi chạy lại `genh offsite run`.",
			Err:  err,
		})
	}
	fi, err := os.Stat(file)
	if err != nil {
		return finish(hostlink.OffsiteStateFailed, &OpError{Code: ErrCodeOffsiteWriteFailed, What: "Không đọc lại được " + file, Why: err.Error(), Err: err})
	}

	// 6. KIỂM gói vừa ghi đọc lại được — lỗi ⇒ xoá, coi như CHƯA có bản sao.
	_, _ = fmt.Fprintln(progress, "Đang kiểm bản sao vừa ghi (giải mã + pg_restore --list)…")
	if _, err := verifyBundle(ctx, env, file, key, ExportDeps{Runner: deps.Runner}); err != nil {
		_ = os.Remove(file)
		var oe *OpError
		if !errors.As(err, &oe) || oe.Code != ErrCodeOffsiteVerifyFailed {
			oe = &OpError{Code: ErrCodeOffsiteVerifyFailed, What: "Bản sao vừa tạo không đọc lại được — CHƯA có bản sao ngoài máy", Why: err.Error(), Err: err}
		}
		return finish(hostlink.OffsiteStateFailed, oe)
	}

	// 7. Xoay vòng: giữ N gói mới nhất ĐÚNG mẫu tên, không đụng tệp khác.
	kept, rerr := rotateOffsiteBundles(sub, cfg.keep())
	if rerr != nil {
		_, _ = fmt.Fprintf(out, "genh: cảnh báo — không xoá được gói cũ: %v\n", rerr)
	}

	st.LastSuccessAt, st.LastFile, st.LastSizeBytes, st.Verified, st.Kept = rfc3339(now), file, fi.Size(), true, kept
	_ = finish(hostlink.OffsiteStateOK, nil)
	_, _ = fmt.Fprintf(out, "genh: đã sao lưu ra ổ ngoài và kiểm đọc lại được: %s (%s, giữ %d bản gần nhất).\n", file, humanBytes(fi.Size()), kept)
	return nil
}

// checkAPIRunning: `docker compose ps --status running -q api` phải có container.
func checkAPIRunning(ctx context.Context, env *Env, composePath string, runner dockercli.Runner) *OpError {
	bundle, err := env.LoadSecrets()
	if err != nil {
		return &OpError{Code: ErrCodeOffsiteNotRunning, What: "Dịch vụ Gen-Harness chưa chạy — chưa sao lưu ra ổ ngoài", Why: err.Error(), Next: "Chạy `genh install` hoặc kiểm --install-dir.", Err: err}
	}
	args := compose.BaseArgs(composePath, "ps", "--status", "running", "-q", bundleServiceName)
	outB, err := runner.Output(ctx, dockercli.Cmd{Name: "docker", Args: args, Env: EnvOverlay(bundle), Dir: composeDir(composePath)})
	if err != nil || strings.TrimSpace(string(outB)) == "" {
		why := "container api không chạy"
		if err != nil {
			why = err.Error()
		}
		return &OpError{
			Code: ErrCodeOffsiteNotRunning,
			What: "Dịch vụ Gen-Harness chưa chạy — chưa sao lưu ra ổ ngoài",
			Why:  why,
			Next: "Chạy `genh start` (hoặc kiểm Docker đang chạy) rồi `genh offsite run`.",
			Err:  err,
		}
	}
	return nil
}

// rotateOffsiteBundles xoá gói cũ, giữ keep gói mới nhất (tên chứa thời điểm
// UTC nên sắp theo tên = theo thời gian). Trả số gói còn lại.
func rotateOffsiteBundles(dir string, keep int) (int, error) {
	entries, err := os.ReadDir(dir)
	if err != nil {
		return 0, err
	}
	var names []string
	for _, e := range entries {
		if e.Type().IsRegular() && offsiteBundleRe.MatchString(e.Name()) {
			names = append(names, e.Name())
		}
	}
	sort.Sort(sort.Reverse(sort.StringSlice(names)))
	var firstErr error
	kept := len(names)
	for i := keep; i < len(names); i++ {
		if err := os.Remove(filepath.Join(dir, names[i])); err != nil {
			if firstErr == nil {
				firstErr = err
			}
			continue
		}
		kept--
	}
	return kept, firstErr
}

func humanBytes(n int64) string {
	const unit = 1024
	if n < unit {
		return fmt.Sprintf("%d B", n)
	}
	div, exp := int64(unit), 0
	for m := n / unit; m >= unit; m /= unit {
		div *= unit
		exp++
	}
	return fmt.Sprintf("%.1f %ciB", float64(n)/float64(div), "KMGTPE"[exp])
}

// ─── genh offsite disable / status ──────────────────────────────────────────

// RunOffsiteDisable tắt lịch tuần, đánh dấu config disabled (giữ đường dẫn),
// ghi trạng thái not_configured. Idempotent.
func RunOffsiteDisable(ctx context.Context, env *Env, deps OffsiteDeps, out io.Writer) error {
	msg, err := deps.scheduler(env).Disable(ctx)
	if err != nil {
		_, _ = fmt.Fprintf(out, "Cảnh báo: gỡ lịch tuần chưa trọn: %v\n", err)
	} else {
		_, _ = fmt.Fprintln(out, msg)
	}
	if cfg, ok, _ := loadOffsiteConfig(env.InstallDir); ok && !cfg.Disabled {
		cfg.Disabled = true
		if err := saveOffsiteConfig(env.InstallDir, cfg); err != nil {
			return &OpError{Code: ErrCodeOffsiteWriteFailed, What: "Không lưu được cấu hình bản sao ngoài máy", Why: err.Error(), Err: err}
		}
	}
	prev, _ := hostlink.ReadOffsiteStatus(env.InstallDir)
	st := hostlink.OffsiteStatus{State: hostlink.OffsiteStateNotConfigured, KeyID: offsiteKeyIDFor(env),
		LastSuccessAt: prev.LastSuccessAt, LastFile: prev.LastFile, LastSizeBytes: prev.LastSizeBytes, Verified: prev.Verified, Kept: prev.Kept}
	_ = hostlink.WriteOffsiteStatus(env.InstallDir, st)
	_, _ = fmt.Fprintln(out, "Đã tắt bản sao ngoài máy (các gói đã có trên ổ USB/NAS giữ nguyên).")
	return nil
}

// RunOffsiteStatus in tình trạng bản sao ngoài máy bằng tiếng Việt.
func RunOffsiteStatus(ctx context.Context, env *Env, deps OffsiteDeps, out io.Writer) error {
	cfg, ok, _ := loadOffsiteConfig(env.InstallDir)
	if !ok || cfg.Disabled || cfg.Path == "" {
		_, _ = fmt.Fprintln(out, "Bản sao ngoài máy: CHƯA BẬT — chạy `genh offsite set <thư mục trên ổ USB/NAS>`.")
	} else {
		_, _ = fmt.Fprintln(out, "Bản sao ngoài máy: BẬT")
		_, _ = fmt.Fprintln(out, "  Nơi lưu: "+filepath.Join(cfg.Path, OffsiteSubdir))
		if cfg.AllowSameDisk {
			_, _ = fmt.Fprintln(out, "  CẢNH BÁO: "+OffsiteSameDiskWarning+" (--allow-same-disk).")
		}
	}
	if st, err := hostlink.ReadOffsiteStatus(env.InstallDir); err == nil {
		if st.LastSuccessAt != "" {
			_, _ = fmt.Fprintf(out, "  Bản sao ngoài máy gần nhất: %s — %s (%s, đã kiểm đọc lại: %s)\n",
				st.LastSuccessAt, st.LastFile, humanBytes(st.LastSizeBytes), yesNo(st.Verified))
		} else {
			_, _ = fmt.Fprintln(out, "  Bản sao ngoài máy gần nhất: chưa có")
		}
		if st.State != "" {
			line := "  Lần chạy gần nhất: " + offsiteStateVI(st.State)
			if st.ErrorCode != "" {
				line += " (" + st.ErrorCode + ")"
			}
			if st.LastAttemptAt != "" {
				line += " lúc " + st.LastAttemptAt
			}
			_, _ = fmt.Fprintln(out, line)
		}
	}
	s, err := deps.scheduler(env).Status(ctx)
	switch {
	case err != nil:
		_, _ = fmt.Fprintf(out, "  Lịch tuần: không kiểm được (%v)\n", err)
	case s.Enabled:
		_, _ = fmt.Fprintf(out, "  Lịch tuần: BẬT mỗi Chủ nhật ~05:30 (%s) — %s\n", s.Mechanism, s.Detail)
	default:
		_, _ = fmt.Fprintln(out, "  Lịch tuần: TẮT — "+s.Detail)
	}
	if id := offsiteKeyIDFor(env); id != "" {
		_, _ = fmt.Fprintln(out, "  Khoá khôi phục: mã nhận diện "+id+" (xem/in \"Bộ khôi phục\" trong Console — cất TÁCH khỏi ổ USB)")
	}
	return nil
}

func yesNo(b bool) string {
	if b {
		return "có"
	}
	return "không"
}

func offsiteStateVI(s string) string {
	switch s {
	case hostlink.OffsiteStateOK:
		return "thành công"
	case hostlink.OffsiteStateFailed:
		return "lỗi"
	case hostlink.OffsiteStateNotMounted:
		return "chưa thấy ổ USB/NAS"
	case hostlink.OffsiteStateNotConfigured:
		return "chưa chọn nơi lưu"
	case hostlink.OffsiteStateRunning:
		return "đang chạy"
	case hostlink.OffsiteStateSkippedBusy:
		return "bỏ qua vì đang bận cập nhật/khôi phục"
	}
	return s
}

// RefreshOffsiteSchedule ghi lại (idempotent) unit/dòng lịch tuần khi bản sao
// ngoài máy đang bật — máy cập nhật từ bản genh cũ có unit/watcher mới. Chưa
// bật thì không làm gì (false, nil).
func RefreshOffsiteSchedule(ctx context.Context, env *Env, deps OffsiteDeps) (bool, error) {
	cfg, ok, err := loadOffsiteConfig(env.InstallDir)
	if err != nil || !ok || cfg.Disabled || cfg.Path == "" {
		return false, err
	}
	if _, _, err := deps.scheduler(env).Enable(ctx); err != nil {
		return false, err
	}
	return true, nil
}

// ─── Yêu cầu từ Console ─────────────────────────────────────────────────────

// clearOffsiteRequest xoá tệp yêu cầu — biến để test tiêm lỗi xoá (chạy bằng root thì
// quyền thư mục không chặn được Remove).
var clearOffsiteRequest = hostlink.ClearOffsiteRequest

// RunOffsiteRequest làm yêu cầu trong run/request/offsite.json (watcher gọi qua
// `genh offsite … --if-requested`). XOÁ tệp yêu cầu TRƯỚC khi làm (watcher không
// kích lặp). handled=false khi hộp thư không có yêu cầu.
//
// v0.1.53 (F-97): KHÔNG xoá được tệp yêu cầu ⇒ trả lỗi bọc hostlink.ErrRequestUndeletable
// và KHÔNG làm gì (không set/run/disable, không ghi offsite-status) — như ConsumeRequest
// của cập nhật: làm tiếp thì trình nhận yêu cầu kích lặp. Caller báo GH-E94C.
func RunOffsiteRequest(ctx context.Context, env *Env, deps OffsiteDeps, out io.Writer) (handled bool, err error) {
	if !hostlink.HasOffsiteRequest(env.InstallDir) {
		return false, nil
	}
	req, rerr := hostlink.ReadOffsiteRequest(env.InstallDir)
	if cerr := clearOffsiteRequest(env.InstallDir); cerr != nil {
		return true, fmt.Errorf("%w: %w", hostlink.ErrRequestUndeletable, cerr)
	}
	if rerr != nil {
		return true, failOffsiteRequest(ctx, env, deps, &OpError{Code: ErrCodeOffsiteInvalidDest, What: "Yêu cầu bản sao ngoài máy từ Console không đọc được", Why: rerr.Error(), Next: "Thử lại trong Console.", Err: rerr})
	}
	switch req.Action {
	case "set":
		// Console KHÔNG BAO GIỜ bật được allow_same_disk; path đi qua đúng bộ kiểm của CLI.
		return true, RunOffsiteSet(ctx, env, OffsiteSetOptions{Path: req.Path, FromConsole: true, Quiet: true}, deps, out)
	case "run":
		return true, RunOffsiteRun(ctx, env, OffsiteRunOptions{Quiet: true}, deps, out)
	case "disable":
		return true, RunOffsiteDisable(ctx, env, deps, out)
	default:
		return true, failOffsiteRequest(ctx, env, deps, &OpError{Code: ErrCodeOffsiteInvalidDest, What: "Yêu cầu bản sao ngoài máy từ Console không hợp lệ", Why: fmt.Sprintf("action %q không được hỗ trợ (set|run|disable)", truncateForLog(req.Action, 40)), Next: "Thử lại trong Console."})
	}
}

// failOffsiteRequest ghi kết quả "failed" (mã của e — GH-EB07) vào
// offsite-status.json cho yêu cầu Console hỏng (JSON không đọc được / action
// lạ) — yêu cầu đã bị xoá nên thiếu bước này Console thấy yêu cầu biến mất mà
// không có kết quả. Giữ nguyên thông tin lần thành công trước (nơi lưu, tệp…).
func failOffsiteRequest(ctx context.Context, env *Env, deps OffsiteDeps, e *OpError) *OpError {
	st := baseStatus(env.InstallDir, "")
	if prev, err := hostlink.ReadOffsiteStatus(env.InstallDir); err == nil {
		st = prev
	}
	if cfg, ok, _ := loadOffsiteConfig(env.InstallDir); ok && !cfg.Disabled {
		st.Configured, st.Dest = true, cfg.Path
	}
	st.State, st.ErrorCode, st.LastAttemptAt = hostlink.OffsiteStateFailed, e.Code, rfc3339(deps.now())
	st.Schedule = scheduleMechanism(ctx, deps.scheduler(env))
	st.KeyID = offsiteKeyIDFor(env)
	_ = hostlink.WriteOffsiteStatus(env.InstallDir, st)
	return e
}

func truncateForLog(s string, n int) string {
	if len(s) <= n {
		return s
	}
	return s[:n] + "…"
}
