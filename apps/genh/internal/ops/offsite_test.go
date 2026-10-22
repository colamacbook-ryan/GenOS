package ops

import (
	"context"
	"errors"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
	"time"

	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/dockercli/fake"
	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/hostlink"
)

var offsiteTestNow = time.Date(2026, 10, 4, 5, 41, 7, 0, time.UTC)

// offsiteFixture: bản cài giả (compose.yaml + secrets/ + config/), một đích
// "ổ USB" (thư mục tạm, hàm thiết bị giả báo KHÁC ổ), lịch tuần giả.
type offsiteFixture struct {
	env   *Env
	dest  string
	sched *fakeOffsiteScheduler
	same  bool
	key   string
}

func newOffsiteFixture(t *testing.T) *offsiteFixture {
	t.Helper()
	composePath := testComposePath(t, "")
	env := testEnv(t, composePath)
	if err := os.MkdirAll(filepath.Join(env.InstallDir, "secrets"), 0o700); err != nil {
		t.Fatal(err)
	}
	if _, err := env.LocatePath(); err != nil { // sinh gh_offsite_key
		t.Fatal(err)
	}
	key, err := os.ReadFile(filepath.Join(env.InstallDir, "secrets", "gh_offsite_key"))
	if err != nil {
		t.Fatal(err)
	}
	return &offsiteFixture{env: env, dest: t.TempDir(), sched: &fakeOffsiteScheduler{}, key: string(key)}
}

func (f *offsiteFixture) deps(r *fake.Runner) OffsiteDeps {
	return OffsiteDeps{
		Runner:     r,
		Scheduler:  f.sched,
		SameDevice: func(string, string) (bool, error) { return f.same, nil },
		Now:        func() time.Time { return offsiteTestNow },
		LockWait:   20 * time.Millisecond,
	}
}

func (f *offsiteFixture) configure(t *testing.T, path string, allowSame bool) {
	t.Helper()
	if err := saveOffsiteConfig(f.env.InstallDir, OffsiteConfig{Path: path, AllowSameDisk: allowSame, SetAt: "2026-10-01T00:00:00Z", Keep: 4}); err != nil {
		t.Fatal(err)
	}
}

func (f *offsiteFixture) status(t *testing.T) hostlink.OffsiteStatus {
	t.Helper()
	st, err := hostlink.ReadOffsiteStatus(f.env.InstallDir)
	if err != nil {
		t.Fatalf("đọc offsite-status.json: %v", err)
	}
	return st
}

// okRunner: api đang chạy, export in một gói, verify thoát verifyExit.
func okRunner(verifyExit int) *fake.Runner {
	return &fake.Runner{Responses: []fake.Response{
		{Match: fake.MatchArgsContain("ps", "--status", "running", "-q", "api"), Output: []byte("c0ffee\n")},
		{Match: fake.MatchArgsContain("gh.bundle", "export", "--out", "-"), RunIOStdout: []byte(bundleMagic + "goi-gia")},
		{Match: fake.MatchArgsContain("gh.bundle", "verify", "--in", "-"), ExitCode: verifyExit,
			RunIOStdout: []byte(`{"ok":true,"alembic_revision":"0026","objects":1,"db_dump_bytes":10,"created_at":"x"}`)},
	}}
}

func wantOpCode(t *testing.T, err error, code string) *OpError {
	t.Helper()
	var oe *OpError
	if !errors.As(err, &oe) || oe.Code != code {
		t.Fatalf("muốn lỗi %s, được %v", code, err)
	}
	return oe
}

// (a) Đích không tồn tại → GH-EB01, KHÔNG tạo thư mục nào, status not_mounted.
func TestOffsiteRun_DichKhongTonTai_EB01_KhongTaoGi(t *testing.T) {
	f := newOffsiteFixture(t)
	missing := filepath.Join(f.dest, "usb")
	f.configure(t, missing, false)
	fr := okRunner(0)
	err := RunOffsiteRun(context.Background(), f.env, OffsiteRunOptions{Quiet: true}, f.deps(fr), &strings.Builder{})
	oe := wantOpCode(t, err, ErrCodeOffsiteNotMounted)
	if !strings.Contains(oe.What, "Chưa thấy ổ USB/NAS tại "+missing) {
		t.Fatalf("What = %q", oe.What)
	}
	if _, err := os.Stat(missing); !os.IsNotExist(err) {
		t.Fatal("KHÔNG được tạo thư mục đích")
	}
	if len(fr.Calls) != 0 {
		t.Fatalf("chưa thấy ổ thì không gọi docker: %+v", fr.Calls)
	}
	st := f.status(t)
	if st.State != hostlink.OffsiteStateNotMounted || st.ErrorCode != ErrCodeOffsiteNotMounted || !st.Configured || st.Dest != missing {
		t.Fatalf("status = %+v", st)
	}

	// `set` vào thư mục không tồn tại: cũng GH-EB01, không tạo, config không đổi.
	err = RunOffsiteSet(context.Background(), f.env, OffsiteSetOptions{Path: filepath.Join(f.dest, "khong-co")}, f.deps(okRunner(0)), &strings.Builder{})
	_ = wantOpCode(t, err, ErrCodeOffsiteNotMounted)
	if _, err := os.Stat(filepath.Join(f.dest, "khong-co")); !os.IsNotExist(err) {
		t.Fatal("set KHÔNG được tạo thư mục đích")
	}
	if cfg, _, _ := loadOffsiteConfig(f.env.InstallDir); cfg.Path != missing {
		t.Fatalf("set lỗi không được đổi config: %+v", cfg)
	}
}

// (b) Cùng thiết bị với gốc cài (USB rút ra, thư mục mount rỗng) → GH-EB01; allow_same_disk → chạy tiếp.
func TestOffsiteRun_CungThietBi(t *testing.T) {
	f := newOffsiteFixture(t)
	f.same = true
	f.configure(t, f.dest, false)
	err := RunOffsiteRun(context.Background(), f.env, OffsiteRunOptions{Quiet: true}, f.deps(okRunner(0)), &strings.Builder{})
	_ = wantOpCode(t, err, ErrCodeOffsiteNotMounted)
	if entries, _ := os.ReadDir(f.dest); len(entries) != 0 {
		t.Fatalf("không được ghi gì vào ổ chính: %v", entries)
	}
	if st := f.status(t); st.State != hostlink.OffsiteStateNotMounted {
		t.Fatalf("status = %+v", st)
	}

	f.configure(t, f.dest, true)
	if err := RunOffsiteRun(context.Background(), f.env, OffsiteRunOptions{Quiet: true}, f.deps(okRunner(0)), &strings.Builder{}); err != nil {
		t.Fatalf("allow_same_disk phải chạy tiếp: %v", err)
	}
	if st := f.status(t); st.State != hostlink.OffsiteStateOK {
		t.Fatalf("status = %+v", st)
	}
}

// (c) Xuất OK + verify thoát 0 → ok, verified, xoay vòng giữ 4 gói đúng mẫu, tệp lạ còn nguyên.
// (h) Mật khẩu (khoá khôi phục) chỉ qua Env của Cmd, không có trong Args.
func TestOffsiteRun_ThanhCong_XoayVong_MatKhauQuaEnv(t *testing.T) {
	f := newOffsiteFixture(t)
	f.configure(t, f.dest, false)
	sub := filepath.Join(f.dest, OffsiteSubdir)
	if err := os.MkdirAll(sub, 0o700); err != nil {
		t.Fatal(err)
	}
	old := []string{"gen-harness-20260906T053000Z.ghbundle", "gen-harness-20260913T053000Z.ghbundle",
		"gen-harness-20260920T053000Z.ghbundle", "gen-harness-20260927T053000Z.ghbundle", "gen-harness-20260830T053000Z.ghbundle"}
	strangers := []string{"ghi-chu.txt", "gen-harness-ban-tay.ghbundle", "gen-harness-20260801T000000Z.ghbundle.bak"}
	for _, n := range append(append([]string{}, old...), strangers...) {
		if err := os.WriteFile(filepath.Join(sub, n), []byte("cu"), 0o600); err != nil {
			t.Fatal(err)
		}
	}
	f.sched.enabled, f.sched.mechanism = true, "systemd"
	fr := okRunner(0)
	var out strings.Builder
	if err := RunOffsiteRun(context.Background(), f.env, OffsiteRunOptions{Quiet: true}, f.deps(fr), &out); err != nil {
		t.Fatalf("RunOffsiteRun: %v", err)
	}
	newFile := filepath.Join(sub, "gen-harness-20261004T054107Z.ghbundle")
	if b, err := os.ReadFile(newFile); err != nil || string(b) != bundleMagic+"goi-gia" {
		t.Fatalf("gói mới %s: %q, %v", newFile, b, err)
	}
	want := map[string]bool{"gen-harness-20261004T054107Z.ghbundle": true, "gen-harness-20260927T053000Z.ghbundle": true,
		"gen-harness-20260920T053000Z.ghbundle": true, "gen-harness-20260913T053000Z.ghbundle": true}
	for _, s := range strangers {
		want[s] = true
	}
	entries, _ := os.ReadDir(sub)
	got := map[string]bool{}
	for _, e := range entries {
		got[e.Name()] = true
	}
	for n := range want {
		if !got[n] {
			t.Errorf("thiếu %s sau xoay vòng (có: %v)", n, got)
		}
	}
	for _, n := range []string{"gen-harness-20260906T053000Z.ghbundle", "gen-harness-20260830T053000Z.ghbundle"} {
		if got[n] {
			t.Errorf("gói cũ %s phải bị xoá", n)
		}
	}
	st := f.status(t)
	if st.State != hostlink.OffsiteStateOK || !st.Verified || st.LastSuccessAt != "2026-10-04T05:41:07Z" || st.LastFile != newFile ||
		st.LastSizeBytes != int64(len(bundleMagic+"goi-gia")) || st.Kept != 4 || st.ErrorCode != "" || st.Schedule != "systemd" ||
		st.KeyID != offsiteKeyID(f.key) || !st.Configured || st.Schema != 1 {
		t.Fatalf("status = %+v", st)
	}
	if _, err := os.Stat(hostlink.HeartbeatPath(f.env.InstallDir)); !os.IsNotExist(err) {
		t.Fatal("nhịp sống phải bị xoá khi xong")
	}
	// (h)
	sawEnv := 0
	for _, c := range fr.Calls {
		if strings.Contains(strings.Join(c.Cmd.Args, " "), f.key) {
			t.Fatalf("khoá khôi phục lộ vào argv: %v", c.Cmd.Args)
		}
		for _, e := range c.Cmd.Env {
			if e == bundlePasswordEnv+"="+f.key {
				sawEnv++
			}
		}
	}
	if sawEnv != 2 {
		t.Fatalf("export + verify phải nhận khoá qua Env GH_BUNDLE_PASSWORD (thấy %d lần)", sawEnv)
	}
	if strings.Contains(out.String(), f.key) {
		t.Fatal("không được in khoá khôi phục")
	}
}

// (d) verify thoát 2 → xoá tệp hỏng, status failed GH-EB03, trả lỗi.
func TestOffsiteRun_VerifyLoi_XoaTep_EB03(t *testing.T) {
	f := newOffsiteFixture(t)
	f.configure(t, f.dest, false)
	err := RunOffsiteRun(context.Background(), f.env, OffsiteRunOptions{Quiet: true}, f.deps(okRunner(2)), &strings.Builder{})
	oe := wantOpCode(t, err, ErrCodeOffsiteVerifyFailed)
	if !strings.Contains(oe.What, "CHƯA có bản sao ngoài máy") {
		t.Fatalf("What = %q", oe.What)
	}
	entries, _ := os.ReadDir(filepath.Join(f.dest, OffsiteSubdir))
	if len(entries) != 0 {
		t.Fatalf("tệp hỏng phải bị xoá: %v", entries)
	}
	st := f.status(t)
	if st.State != hostlink.OffsiteStateFailed || st.ErrorCode != ErrCodeOffsiteVerifyFailed || st.Verified || st.LastSuccessAt != "" {
		t.Fatalf("status = %+v", st)
	}
}

// (e) Khoá loại trừ bận (update/restore đang chạy) → skipped_busy, GH-EB05.
func TestOffsiteRun_KhoaBan_SkippedBusy(t *testing.T) {
	f := newOffsiteFixture(t)
	f.configure(t, f.dest, false)
	held, err := hostlink.AcquireLock(f.env.InstallDir)
	if err != nil {
		t.Fatal(err)
	}
	defer held.Release()
	fr := okRunner(0)
	err = RunOffsiteRun(context.Background(), f.env, OffsiteRunOptions{Quiet: true}, f.deps(fr), &strings.Builder{})
	_ = wantOpCode(t, err, ErrCodeOffsiteBusy)
	if st := f.status(t); st.State != hostlink.OffsiteStateSkippedBusy || st.ErrorCode != ErrCodeOffsiteBusy {
		t.Fatalf("status = %+v", st)
	}
	if len(fr.Calls) != 0 {
		t.Fatal("bận thì không xuất gì")
	}
}

// (f) Chưa cấu hình → not_configured, không lỗi; disable cũng về not_configured.
func TestOffsiteRun_ChuaCauHinh(t *testing.T) {
	f := newOffsiteFixture(t)
	if err := RunOffsiteRun(context.Background(), f.env, OffsiteRunOptions{Quiet: true}, f.deps(&fake.Runner{}), &strings.Builder{}); err != nil {
		t.Fatalf("chưa cấu hình không được lỗi: %v", err)
	}
	st := f.status(t)
	if st.State != hostlink.OffsiteStateNotConfigured || st.Configured {
		t.Fatalf("status = %+v", st)
	}
	f.configure(t, f.dest, false)
	f.sched.enabled = true
	if err := RunOffsiteDisable(context.Background(), f.env, f.deps(&fake.Runner{}), &strings.Builder{}); err != nil {
		t.Fatal(err)
	}
	if f.sched.enabled || f.sched.disables != 1 {
		t.Fatal("disable phải gỡ lịch tuần")
	}
	cfg, ok, _ := loadOffsiteConfig(f.env.InstallDir)
	if !ok || !cfg.Disabled || cfg.Path != f.dest {
		t.Fatalf("disable giữ config, đánh dấu disabled: %+v", cfg)
	}
	if st := f.status(t); st.State != hostlink.OffsiteStateNotConfigured || st.Configured {
		t.Fatalf("status sau disable = %+v", st)
	}
	fr := &fake.Runner{}
	if err := RunOffsiteRun(context.Background(), f.env, OffsiteRunOptions{Quiet: true}, f.deps(fr), &strings.Builder{}); err != nil || len(fr.Calls) != 0 {
		t.Fatalf("đã tắt thì run không làm gì: %v %d", err, len(fr.Calls))
	}
}

// (g) Yêu cầu từ Console: path tương đối / ký tự điều khiển / quá dài → GH-EB07;
// Console KHÔNG BAO GIỜ bật được allow_same_disk; tệp yêu cầu bị xoá trước khi làm.
func TestOffsiteRequest_Console_KhongTinCay(t *testing.T) {
	f := newOffsiteFixture(t)
	if err := hostlink.EnsureDir(f.env.InstallDir); err != nil {
		t.Fatal(err)
	}
	write := func(body string) {
		if err := os.WriteFile(hostlink.OffsiteRequestPath(f.env.InstallDir), []byte(body), 0o666); err != nil {
			t.Fatal(err)
		}
	}
	for _, p := range []string{`media/usb`, "/media/usb\nx", "/media/u\x01sb", "/" + strings.Repeat("a", 401)} {
		write(`{"action":"set","path":` + jsonQuote(p) + `}`)
		handled, err := RunOffsiteRequest(context.Background(), f.env, f.deps(okRunner(0)), &strings.Builder{})
		if !handled {
			t.Fatal("phải nhận yêu cầu")
		}
		_ = wantOpCode(t, err, ErrCodeOffsiteInvalidDest)
		if hostlink.HasOffsiteRequest(f.env.InstallDir) {
			t.Fatal("tệp yêu cầu phải bị xoá trước khi làm")
		}
		if _, ok, _ := loadOffsiteConfig(f.env.InstallDir); ok {
			t.Fatal("đích không hợp lệ không được lưu config")
		}
	}
	// Nằm trong thư mục cài → GH-EB07.
	inside := filepath.Join(f.env.InstallDir, "data")
	_ = os.MkdirAll(inside, 0o755)
	write(`{"action":"set","path":` + jsonQuote(inside) + `}`)
	_, err := RunOffsiteRequest(context.Background(), f.env, f.deps(okRunner(0)), &strings.Builder{})
	_ = wantOpCode(t, err, ErrCodeOffsiteInvalidDest)

	// Cùng ổ + Console cố gửi allow_same_disk → vẫn GH-EB01, config không bật allow_same_disk.
	f.same = true
	write(`{"action":"set","path":` + jsonQuote(f.dest) + `,"allow_same_disk":true}`)
	_, err = RunOffsiteRequest(context.Background(), f.env, f.deps(okRunner(0)), &strings.Builder{})
	_ = wantOpCode(t, err, ErrCodeOffsiteNotMounted)
	if cfg, ok, _ := loadOffsiteConfig(f.env.InstallDir); ok || cfg.AllowSameDisk {
		t.Fatalf("Console không bao giờ đặt được allow_same_disk: %+v", cfg)
	}
	// Kể cả khi gọi thẳng RunOffsiteSet với FromConsole + AllowSameDisk.
	err = RunOffsiteSet(context.Background(), f.env, OffsiteSetOptions{Path: f.dest, AllowSameDisk: true, FromConsole: true}, f.deps(okRunner(0)), &strings.Builder{})
	_ = wantOpCode(t, err, ErrCodeOffsiteNotMounted)

	// Ổ ngoài hợp lệ: set từ Console lưu config (allow_same_disk=false), bật lịch, chạy lần đầu.
	f.same = false
	write(`{"id":"o1","action":"set","path":` + jsonQuote(f.dest) + `}`)
	handled, err := RunOffsiteRequest(context.Background(), f.env, f.deps(okRunner(0)), &strings.Builder{})
	if !handled || err != nil {
		t.Fatalf("set hợp lệ: %v %v", handled, err)
	}
	cfg, ok, _ := loadOffsiteConfig(f.env.InstallDir)
	if !ok || cfg.Path != f.dest || cfg.AllowSameDisk || cfg.Keep != 4 {
		t.Fatalf("config = %+v", cfg)
	}
	if f.sched.enables != 1 || f.status(t).State != hostlink.OffsiteStateOK {
		t.Fatalf("phải bật lịch + chạy lần đầu: enables=%d status=%+v", f.sched.enables, f.status(t))
	}
	// action lạ → lỗi, tệp vẫn bị xoá; Console thấy KẾT QUẢ (failed/GH-EB07) chứ không phải yêu cầu biến mất,
	// thông tin lần thành công trước giữ nguyên.
	okBefore := f.status(t)
	write(`{"action":"rm -rf"}`)
	if _, err := RunOffsiteRequest(context.Background(), f.env, f.deps(okRunner(0)), &strings.Builder{}); err == nil || hostlink.HasOffsiteRequest(f.env.InstallDir) {
		t.Fatalf("action lạ phải lỗi và xoá tệp: %v", err)
	}
	st := f.status(t)
	if st.State != hostlink.OffsiteStateFailed || st.ErrorCode != ErrCodeOffsiteInvalidDest {
		t.Fatalf("action lạ phải ghi failed/GH-EB07: %+v", st)
	}
	if st.LastSuccessAt != okBefore.LastSuccessAt || st.LastFile != okBefore.LastFile || st.Dest != f.dest || !st.Configured {
		t.Fatalf("phải giữ thông tin lần thành công trước: trước=%+v sau=%+v", okBefore, st)
	}
	// JSON hỏng → cũng ghi kết quả failed/GH-EB07.
	write(`{không phải json`)
	if _, err := RunOffsiteRequest(context.Background(), f.env, f.deps(okRunner(0)), &strings.Builder{}); err == nil {
		t.Fatal("JSON hỏng phải lỗi")
	}
	if st := f.status(t); st.State != hostlink.OffsiteStateFailed || st.ErrorCode != ErrCodeOffsiteInvalidDest {
		t.Fatalf("JSON hỏng phải ghi failed/GH-EB07: %+v", st)
	}
	if handled, _ := RunOffsiteRequest(context.Background(), f.env, f.deps(okRunner(0)), &strings.Builder{}); handled {
		t.Fatal("hộp thư trống thì handled=false")
	}
}

// v0.1.53 (F-97): KHÔNG xoá được tệp yêu cầu ⇒ dừng TRƯỚC khi làm (không set/run/disable,
// không ghi offsite-status) và trả lỗi bọc ErrRequestUndeletable để caller báo GH-E94C — như
// ConsumeRequest của cập nhật. Trước đây lỗi xoá bị bỏ qua: bản sao chạy trọn rồi kết quả
// thành công bị ghi đè bằng failed GH-E94C.
func TestOffsiteRequest_XoaLoi_KhongLamGi(t *testing.T) {
	f := newOffsiteFixture(t)
	if err := hostlink.EnsureDir(f.env.InstallDir); err != nil {
		t.Fatal(err)
	}
	old := clearOffsiteRequest
	t.Cleanup(func() { clearOffsiteRequest = old })
	clearOffsiteRequest = func(string) error { return os.ErrPermission }
	for _, body := range []string{
		`{"id":"o1","action":"set","path":` + jsonQuote(f.dest) + `}`,
		`{"id":"o2","action":"run"}`,
		`{"id":"o3","action":"disable"}`,
		`{không phải json`,
	} {
		if err := os.WriteFile(hostlink.OffsiteRequestPath(f.env.InstallDir), []byte(body), 0o666); err != nil {
			t.Fatal(err)
		}
		r := okRunner(0)
		handled, err := RunOffsiteRequest(context.Background(), f.env, f.deps(r), &strings.Builder{})
		if !handled || !errors.Is(err, hostlink.ErrRequestUndeletable) || !errors.Is(err, os.ErrPermission) {
			t.Fatalf("%s: handled=%v err=%v — phải trả ErrRequestUndeletable", body, handled, err)
		}
		if len(r.Calls) != 0 || f.sched.enables != 0 || f.sched.disables != 0 {
			t.Fatalf("%s: không được làm gì khi không xoá được yêu cầu: calls=%d enables=%d disables=%d", body, len(r.Calls), f.sched.enables, f.sched.disables)
		}
		if _, ok, _ := loadOffsiteConfig(f.env.InstallDir); ok {
			t.Fatalf("%s: không được lưu nơi lưu", body)
		}
		if _, err := hostlink.ReadOffsiteStatus(f.env.InstallDir); err == nil {
			t.Fatalf("%s: không được ghi offsite-status (caller ghi GH-E94C)", body)
		}
	}
	// Xoá được thì làm như thường.
	clearOffsiteRequest = old
	if handled, err := RunOffsiteRequest(context.Background(), f.env, f.deps(okRunner(0)), &strings.Builder{}); !handled || errors.Is(err, hostlink.ErrRequestUndeletable) {
		t.Fatalf("xoá được: handled=%v err=%v", handled, err)
	}
}

func jsonQuote(s string) string {
	r := strings.NewReplacer(`\`, `\\`, `"`, `\"`, "\n", `\n`, "\x01", `\u0001`)
	return `"` + r.Replace(s) + `"`
}

// CLI set: đích hợp lệ → config 0600 + lịch + chạy lần đầu; --allow-same-disk in cảnh báo; --no-run không xuất.
func TestOffsiteSet_CLI(t *testing.T) {
	f := newOffsiteFixture(t)
	f.same = true
	var out strings.Builder
	fr := okRunner(0)
	if err := RunOffsiteSet(context.Background(), f.env, OffsiteSetOptions{Path: f.dest, AllowSameDisk: true, NoRun: true}, f.deps(fr), &out); err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(out.String(), OffsiteSameDiskWarning) {
		t.Fatalf("thiếu cảnh báo cùng ổ:\n%s", out.String())
	}
	if len(fr.Calls) != 0 {
		t.Fatal("--no-run không được xuất")
	}
	if _, err := os.Stat(filepath.Join(f.dest, offsiteProbeFile)); !os.IsNotExist(err) {
		t.Fatal("tệp thăm dò phải bị xoá")
	}
	cfg, ok, _ := loadOffsiteConfig(f.env.InstallDir)
	if !ok || !cfg.AllowSameDisk || cfg.Path != f.dest || cfg.SetAt != "2026-10-04T05:41:07Z" {
		t.Fatalf("config = %+v", cfg)
	}
	if runtime.GOOS != "windows" {
		fi, _ := os.Stat(offsiteConfigPath(f.env.InstallDir))
		if fi.Mode().Perm() != 0o600 {
			t.Fatalf("config/offsite.json phải 0600, là %v", fi.Mode().Perm())
		}
	}
	st := f.status(t)
	if !st.Configured || st.Dest != f.dest || st.Schedule != "cron" || st.LastSuccessAt != "" {
		t.Fatalf("status sau --no-run = %+v", st)
	}
	// Không phải thư mục → GH-EB07.
	file := filepath.Join(f.dest, "tep.txt")
	_ = os.WriteFile(file, []byte("x"), 0o600)
	_ = wantOpCode(t, RunOffsiteSet(context.Background(), f.env, OffsiteSetOptions{Path: file}, f.deps(okRunner(0)), &strings.Builder{}), ErrCodeOffsiteInvalidDest)
}

func TestRunOffsiteRun_DichVuChuaChay_EB06(t *testing.T) {
	f := newOffsiteFixture(t)
	f.configure(t, f.dest, false)
	fr := &fake.Runner{Responses: []fake.Response{{Match: fake.MatchArgsContain("ps", "--status", "running"), Output: []byte("")}}}
	_ = wantOpCode(t, RunOffsiteRun(context.Background(), f.env, OffsiteRunOptions{Quiet: true}, f.deps(fr), &strings.Builder{}), ErrCodeOffsiteNotRunning)
	if st := f.status(t); st.State != hostlink.OffsiteStateFailed || st.ErrorCode != ErrCodeOffsiteNotRunning {
		t.Fatalf("status = %+v", st)
	}
}

func TestOffsiteSet_HeTepTam_EB07_KeCaAllowSameDisk(t *testing.T) {
	f := newOffsiteFixture(t)
	deps := f.deps(okRunner(0))
	deps.VolatileFS = func(string) string { return "tmpfs" }
	for _, allow := range []bool{false, true} {
		err := RunOffsiteSet(context.Background(), f.env, OffsiteSetOptions{Path: f.dest, AllowSameDisk: allow, NoRun: true}, deps, &strings.Builder{})
		oe := wantOpCode(t, err, ErrCodeOffsiteInvalidDest)
		if !strings.Contains(oe.Why, "tmpfs") {
			t.Fatalf("lý do phải nêu tmpfs: %q", oe.Why)
		}
		if _, ok, _ := loadOffsiteConfig(f.env.InstallDir); ok {
			t.Fatal("đích tmpfs không được lưu config")
		}
	}
}

func TestMountinfo_TmpfsVaCungThietBiKhoi(t *testing.T) {
	const mi = `22 1 252:1 / / rw,relatime shared:1 - ext4 /dev/vda1 rw
23 22 0:21 / /tmp rw,nosuid shared:2 - tmpfs tmpfs rw
24 22 0:22 / /dev/shm rw - tmpfs shm rw
25 22 0:40 /@data /srv/data rw - btrfs /dev/sdb1 rw
26 22 0:41 /@home /home rw - btrfs /dev/sdb1 rw
27 22 8:33 / /media/sep/USB\040Moi rw - vfat /dev/sdc1 rw
28 22 0:50 / /mnt/nas rw - nfs4 192.168.1.5:/share rw
hỏng không có dấu gạch`
	es := parseMountinfo(mi)
	if len(es) != 7 {
		t.Fatalf("phải tách 7 dòng, được %d: %+v", len(es), es)
	}
	for path, want := range map[string]string{"/tmp/gen": "tmpfs", "/dev/shm": "tmpfs", "/media/sep/USB Moi/x": "", "/srv/data": "", "/": ""} {
		if got := volatileKind(es, path); got != want {
			t.Errorf("volatileKind(%q) = %q, muốn %q", path, got, want)
		}
	}
	if e, ok := mountFor(es, "/media/sep/USB Moi/gen"); !ok || e.Source != "/dev/sdc1" {
		t.Fatalf("điểm mount có dấu cách (\\040) phải giải mã: %+v %v", e, ok)
	}
	// Hai subvolume btrfs của CÙNG /dev/sdb1 (Dev khác nhau) ⇒ cùng ổ.
	if !sameBlockSource(es, "/srv/data/gen", "/home/sep/gen-harness") {
		t.Fatal("subvolume btrfs cùng /dev/sdb1 phải coi là cùng ổ")
	}
	if sameBlockSource(es, "/media/sep/USB Moi", "/home/sep/gen-harness") {
		t.Fatal("ổ USB /dev/sdc1 khác /dev/sdb1")
	}
	if sameBlockSource(es, "/mnt/nas/a", "/mnt/nas/b") {
		t.Fatal("nguồn không phải /dev/… (NAS) không so theo thiết bị khối")
	}
}

func TestWriteErrRecorder_NhoLoiGhiDauTien(t *testing.T) {
	w := &writeErrRecorder{w: failWriter{}}
	if _, err := w.Write([]byte("a")); err == nil || w.err == nil || !strings.Contains(w.err.Error(), "đầy") {
		t.Fatalf("phải nhớ lỗi ghi: %v %v", err, w.err)
	}
}

type failWriter struct{}

func (failWriter) Write([]byte) (int, error) { return 0, errors.New("ổ đã đầy") }

func TestSameVolumeWindows(t *testing.T) {
	cases := []struct {
		dest, install string
		want          bool
	}{
		{`C:\Backup`, `C:\Users\u\.gen-harness`, true},
		{`c:\Backup`, `C:\Users\u\.gen-harness`, true},
		{`E:\`, `C:\Users\u\.gen-harness`, false},
		{`\\NAS\share\gh`, `C:\Users\u\.gen-harness`, false},
		{`\\NAS\share\gh`, `\\NAS\share\gh2`, false},
	}
	for _, c := range cases {
		if got := sameVolumeWindows(c.dest, c.install); got != c.want {
			t.Errorf("sameVolumeWindows(%q, %q) = %v, muốn %v", c.dest, c.install, got, c.want)
		}
	}
}

func TestOffsiteRecentSuccess(t *testing.T) {
	dir := t.TempDir()
	now := time.Date(2026, 10, 10, 0, 0, 0, 0, time.UTC)
	if OffsiteRecentSuccess(dir, now) {
		t.Fatal("chưa có status thì không phải gần đây")
	}
	_ = hostlink.WriteOffsiteStatus(dir, hostlink.OffsiteStatus{LastSuccessAt: "2026-10-04T05:40:00Z"})
	if !OffsiteRecentSuccess(dir, now) {
		t.Fatal("6 ngày trước là gần đây")
	}
	if OffsiteRecentSuccess(dir, now.Add(48*time.Hour)) {
		t.Fatal("8 ngày trước không còn gần đây")
	}
}
