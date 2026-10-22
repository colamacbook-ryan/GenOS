package autoupdate

import (
	"context"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

// recRunner ghi lại MỌI lệnh hệ thống (nối thành "tên đối số…") và trả đầu ra/lỗi giả
// theo luật đầu tiên có chuỗi con khớp dòng lệnh; không luật nào khớp thì trả (nil, nil)
// — tức mọi lệnh "thành công" (systemd --user coi như dùng được).
type recRunner struct {
	calls []string
	rules []recRule
}

type recRule struct {
	match string
	out   string
	err   error
}

func (r *recRunner) on(match, out string, err error) *recRunner {
	r.rules = append(r.rules, recRule{match, out, err})
	return r
}

func (r *recRunner) reset(rules ...recRule) {
	r.calls = nil
	r.rules = rules
}

func (r *recRunner) Output(_ context.Context, name string, args []string) ([]byte, error) {
	line := name + " " + strings.Join(args, " ")
	r.calls = append(r.calls, line)
	for _, rule := range r.rules {
		if strings.Contains(line, rule.match) {
			return []byte(rule.out), rule.err
		}
	}
	return nil, nil
}

func (r *recRunner) ran(sub string) bool { return r.count(sub) > 0 }

func (r *recRunner) count(sub string) int {
	n := 0
	for _, c := range r.calls {
		if strings.Contains(c, sub) {
			n++
		}
	}
	return n
}

// showOut dựng đầu ra `systemctl --user show gen-harness-update.timer -p …`.
func showOut(loadState, unitFileState, active, last, next string) string {
	return "LoadState=" + loadState + "\nUnitFileState=" + unitFileState + "\nActiveState=" + active +
		"\nLastTriggerUSec=" + last + "\nNextElapseUSecRealtime=" + next + "\n"
}

var errExit1 = errors.New("exit status 1")

func nightlyDeps(t *testing.T, rr *recRunner) Deps {
	t.Helper()
	root := t.TempDir()
	home := t.TempDir()
	// Cô lập HOME: unit cũ (không có --install-dir) coi chủ là config.HomeRoot() = $HOME/.gen-harness. Không đặt
	// HOME thì test đọc HOME THẬT của máy chạy — máy Sếp có bản cài sống ở ~/.gen-harness nên unit bị coi là
	// "của bản khác" và RefreshUnits không sửa gì (TestRefreshUnits_* đỏ, CI GitHub xanh vì runner không có bản cài).
	t.Setenv("HOME", home)
	t.Setenv("USERPROFILE", home)
	t.Setenv("LOCALAPPDATA", filepath.Join(home, "AppData", "Local"))
	return Deps{
		Runner: rr, GenhPath: "/g/bin/genh", LogFile: filepath.Join(root, "logs", "auto-update.log"),
		HomeDir: home, GOOS: "linux", UID: "1000", Location: time.UTC,
		LookPath:   func(string) (string, error) { return "/usr/bin/systemctl", nil },
		InstallDir: root, Nightly: NightlyJob{InstallDir: root},
	}
}

func healthyRules() []recRule {
	return []recRule{
		{"show gen-harness-update.timer", showOut("loaded", "enabled", "active", "@1760000000", "@1760086400"), nil},
		{"loginctl show-user", "yes\n", nil},
	}
}

func hasWriteCommand(rr *recRunner) bool {
	for _, w := range []string{"--user enable", "--user disable", "daemon-reload", "--user restart", "--user start", "--user stop", "--user mask", "reset-failed", "crontab /"} {
		if rr.ran(w) {
			return true
		}
	}
	return false
}

// ─── EnsureNightly (F-93, C2 tự lành) ───────────────────────────────────────

func TestEnsureNightly_KhoeKhongGoiLenhGhi(t *testing.T) {
	rr := &recRunner{rules: healthyRules()}
	deps := nightlyDeps(t, rr)
	healed, msg, err := EnsureNightly(context.Background(), deps)
	if err != nil || healed || msg != "" {
		t.Fatalf("lịch khoẻ: healed=%v msg=%q err=%v", healed, msg, err)
	}
	if hasWriteCommand(rr) {
		t.Fatalf("lịch khoẻ KHÔNG được gọi lệnh ghi nào, đã gọi: %v", rr.calls)
	}
	if _, err := os.Stat(serviceUnitPath(deps.HomeDir)); !os.IsNotExist(err) {
		t.Fatalf("lịch khoẻ không được ghi unit: %v", err)
	}
}

func TestEnsureNightly_TuLanh_BaCaHong(t *testing.T) {
	cases := []struct {
		name  string
		rules []recRule
	}{
		{"không có tệp unit", []recRule{
			{"show gen-harness-update.timer", showOut("not-found", "", "inactive", "", ""), nil},
			{"crontab -l", "", errExit1},
		}},
		{"UnitFileState=disabled, show thoát 1 nhưng stdout vẫn có trạng thái", []recRule{
			{"show gen-harness-update.timer", showOut("loaded", "disabled", "inactive", "", ""), errExit1},
		}},
		{"show hỏng hẳn, is-enabled thoát 1 nhưng stdout 'disabled'", []recRule{
			{"show gen-harness-update.timer", "", errExit1},
			{"is-enabled gen-harness-update.timer", "disabled\n", errExit1},
			{"is-active gen-harness-update.timer", "inactive\n", errExit1},
		}},
		{"enabled nhưng ActiveState=inactive", []recRule{
			{"show gen-harness-update.timer", showOut("loaded", "enabled", "inactive", "@1760000000", ""), nil},
		}},
		{"enabled nhưng ActiveState=failed", []recRule{
			{"show gen-harness-update.timer", showOut("loaded", "enabled", "failed", "@1760000000", ""), nil},
		}},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			rr := &recRunner{rules: append([]recRule{{"loginctl show-user", "yes\n", nil}}, tc.rules...)}
			deps := nightlyDeps(t, rr)
			healed, msg, err := EnsureNightly(context.Background(), deps)
			if err != nil || !healed {
				t.Fatalf("phải tự lành: healed=%v err=%v calls=%v", healed, err, rr.calls)
			}
			mustContain(t, msg, "genh: lịch tự cập nhật đêm đã bị tắt/mất — đã bật lại (~03:00). Muốn tắt hẳn: genh auto-update disable")
			if !rr.ran("systemctl --user enable --now gen-harness-update.timer") {
				t.Fatalf("phải gọi enable --now gen-harness-update.timer, đã gọi: %v", rr.calls)
			}
			svc, err := os.ReadFile(serviceUnitPath(deps.HomeDir))
			if err != nil {
				t.Fatalf("phải ghi lại unit: %v", err)
			}
			mustContain(t, string(svc), "update --yes --quiet --install-dir "+deps.InstallDir)
			mustContain(t, string(svc), "Environment=GEN_HARNESS_HOME="+deps.InstallDir)
			if rr.ran("--user disable") || rr.ran("--user mask") || rr.ran("--user stop") {
				t.Fatalf("tự lành không được disable/mask/stop: %v", rr.calls)
			}
		})
	}
}

func TestEnsureNightly_ChayHaiLanLanHaiKhongGhi(t *testing.T) {
	rr := &recRunner{rules: []recRule{
		{"loginctl show-user", "yes\n", nil},
		{"show gen-harness-update.timer", showOut("loaded", "disabled", "inactive", "", ""), errExit1},
	}}
	deps := nightlyDeps(t, rr)
	if healed, _, err := EnsureNightly(context.Background(), deps); err != nil || !healed {
		t.Fatalf("lần 1: healed=%v err=%v", healed, err)
	}
	svc1, _ := os.ReadFile(serviceUnitPath(deps.HomeDir))
	// Sau khi lành: systemd báo enabled + active.
	rr.reset(healthyRules()...)
	healed, msg, err := EnsureNightly(context.Background(), deps)
	if err != nil || healed || msg != "" {
		t.Fatalf("lần 2: healed=%v msg=%q err=%v", healed, msg, err)
	}
	if hasWriteCommand(rr) || rr.ran("--user enable") {
		t.Fatalf("lần 2 KHÔNG được gọi enable/ghi: %v", rr.calls)
	}
	if svc2, _ := os.ReadFile(serviceUnitPath(deps.HomeDir)); string(svc1) != string(svc2) {
		t.Fatal("lần 2 không được đổi unit")
	}
}

// H-b: timer active mà không có lần chạy kế tiếp (daemon-reload/enable từ bên trong
// gen-harness-update.service từng làm mất lịch?) ⇒ restart timer, không enable.
func TestEnsureNightly_HB_ActiveNhungKhongCoLanKeTiep_RestartTimer(t *testing.T) {
	rr := &recRunner{rules: []recRule{
		{"show gen-harness-update.timer", showOut("loaded", "enabled", "active", "@1760000000", ""), nil},
		{"loginctl show-user", "yes\n", nil},
	}}
	deps := nightlyDeps(t, rr)
	healed, msg, err := EnsureNightly(context.Background(), deps)
	if err != nil || !healed || msg != RearmedMessage {
		t.Fatalf("healed=%v msg=%q err=%v", healed, msg, err)
	}
	if !rr.ran("systemctl --user restart gen-harness-update.timer") {
		t.Fatalf("phải restart gen-harness-update.timer: %v", rr.calls)
	}
	if rr.ran("--user enable") || rr.ran("daemon-reload") {
		t.Fatalf("H-b chỉ restart, không enable/daemon-reload: %v", rr.calls)
	}
}

// Timer "running" vì service lịch đêm ĐANG chạy (kể cả chính genh này) ⇒ lần kế tiếp rỗng là bình
// thường (systemd tính lại khi service xong) — không restart, không báo "mất lịch".
func TestEnsureNightly_HB_ServiceDangChay_KhongRestart(t *testing.T) {
	for _, state := range []string{"activating", "active", "reloading"} {
		rr := &recRunner{rules: []recRule{
			{"is-active gen-harness-update.service", state + "\n", nil},
			{"show gen-harness-update.timer", showOut("loaded", "enabled", "active", "@1760000000", ""), nil},
			{"loginctl show-user", "yes\n", nil},
		}}
		healed, msg, err := EnsureNightly(context.Background(), nightlyDeps(t, rr))
		if err != nil || healed || msg != "" || hasWriteCommand(rr) {
			t.Fatalf("service %s: healed=%v msg=%q err=%v calls=%v", state, healed, msg, err, rr.calls)
		}
	}
}

func TestEnsureNightly_Crontab_KhoeThiKhongGhi(t *testing.T) {
	rr := &recRunner{}
	rr.on("show gen-harness-update.timer", "", errExit1)
	rr.on("is-enabled", "", errExit1)
	rr.on("crontab -l", CrontabMarker+"\n7 3 * * * genh update --yes --quiet\n", nil)
	deps := nightlyDeps(t, rr)
	healed, _, err := EnsureNightly(context.Background(), deps)
	if err != nil || healed || hasWriteCommand(rr) {
		t.Fatalf("cron khoẻ: healed=%v err=%v calls=%v", healed, err, rr.calls)
	}
}

func TestEnsureNightly_LanhThemCanhBaoLinger(t *testing.T) {
	rr := &recRunner{}
	rr.on("loginctl show-user", "no\n", nil)
	rr.on("show gen-harness-update.timer", showOut("not-found", "", "inactive", "", ""), nil)
	rr.on("crontab -l", "", errExit1)
	_, msg, err := EnsureNightly(context.Background(), nightlyDeps(t, rr))
	if err != nil {
		t.Fatal(err)
	}
	mustContain(t, msg, HealedMessage)
	mustContain(t, msg, "sudo loginctl enable-linger $USER")
}

// ─── Bảo vệ lịch dùng chung trong EnsureNightly ─────────────────────────────

func TestEnsureNightly_LichThuocBanCaiKhac(t *testing.T) {
	other := t.TempDir()
	writeSecrets(t, other)
	rr := &recRunner{}
	deps := nightlyDeps(t, rr)
	writeUnit(t, deps.HomeDir, TaskName+".service", "[Service]\nEnvironment=GEN_HARNESS_HOME="+other+"\nExecStart=/g/genh update --yes --quiet --install-dir "+other+"\n")

	// Lịch của bản kia khoẻ ⇒ im lặng, không đụng gì.
	rr.reset(healthyRules()...)
	if healed, msg, err := EnsureNightly(context.Background(), deps); err != nil || healed || msg != "" || hasWriteCommand(rr) {
		t.Fatalf("lịch khoẻ của bản khác: healed=%v msg=%q err=%v calls=%v", healed, msg, err, rr.calls)
	}
	// Lịch của bản kia hỏng ⇒ KHÔNG ghi đè, trả lỗi có kiểu.
	rr.reset(recRule{"show gen-harness-update.timer", showOut("loaded", "disabled", "inactive", "", ""), errExit1}, recRule{"loginctl show-user", "yes\n", nil})
	_, _, err := EnsureNightly(context.Background(), deps)
	if !errors.Is(err, ErrScheduleOwnedByOther) {
		t.Fatalf("muốn ErrScheduleOwnedByOther, được %v", err)
	}
	if rr.ran("--user enable") {
		t.Fatalf("không được enable lịch của bản khác: %v", rr.calls)
	}
}

// ─── Trạng thái trung thực (F-94, C1) ───────────────────────────────────────

func TestStatusLinux_DisabledThoat1_VanLaSystemdKhongRoiSangCrontab(t *testing.T) {
	// Đúng triệu chứng máy Sếp: `is-enabled` thoát ≠0 khi disabled nhưng stdout có trạng thái;
	// bản cũ rơi sang crontab và in "crontab: không có dòng tự cập nhật".
	rr := &recRunner{}
	rr.on("show gen-harness-update.timer", "disabled\n", errExit1) // đầu ra không đọc được
	rr.on("is-enabled gen-harness-update.timer", "disabled\n", errExit1)
	rr.on("is-active gen-harness-update.timer", "inactive\n", errExit1)
	rr.on("crontab -l", "", nil)
	st, err := GetStatus(context.Background(), nightlyDeps(t, rr))
	if err != nil {
		t.Fatal(err)
	}
	if st.Mechanism != ScheduleSystemd || !st.UnitPresent || st.Enabled || st.UnitFileState != "disabled" || st.Active != "inactive" {
		t.Fatalf("status = %+v", st)
	}
	if rr.ran("crontab") {
		t.Fatalf("không được rơi sang crontab khi unit systemd có mặt: %v", rr.calls)
	}
	mustContain(t, st.Detail, "UnitFileState=disabled")
}

func TestStatusLinux_ShowThoat1NhungCoStdout(t *testing.T) {
	rr := &recRunner{}
	rr.on("show gen-harness-update.timer", showOut("loaded", "disabled", "inactive", "", ""), errExit1)
	st, err := GetStatus(context.Background(), nightlyDeps(t, rr))
	if err != nil || st.Mechanism != ScheduleSystemd || !st.UnitPresent || st.Enabled || st.UnitFileState != "disabled" {
		t.Fatalf("status = %+v err=%v", st, err)
	}
	if rr.ran("crontab") {
		t.Fatalf("không được rơi sang crontab: %v", rr.calls)
	}
}

func TestStatusLinux_MocGio_Unix_VaDangNgay(t *testing.T) {
	rr := &recRunner{}
	rr.on("--timestamp=unix", showOut("loaded", "enabled", "active", "@1760000000", "@1760086400"), nil)
	st, _ := GetStatus(context.Background(), nightlyDeps(t, rr))
	if !st.LastRun.Equal(time.Unix(1760000000, 0)) || !st.NextRun.Equal(time.Unix(1760086400, 0)) {
		t.Fatalf("dạng '@giây': last=%v next=%v", st.LastRun, st.NextRun)
	}

	// systemd < 247: không có --timestamp=unix (lệnh lỗi, không đầu ra) ⇒ hỏi lại không cờ,
	// mốc giờ dạng "Mon 2006-01-02 15:04:05 MST".
	rr = &recRunner{}
	rr.on("--timestamp=unix", "", errExit1)
	rr.on("show gen-harness-update.timer", showOut("loaded", "enabled", "active", "Sat 2025-10-11 03:07:12 UTC", "Sun 2025-10-12 03:21:00 UTC"), nil)
	st, _ = GetStatus(context.Background(), nightlyDeps(t, rr))
	wantLast := time.Date(2025, 10, 11, 3, 7, 12, 0, time.UTC)
	wantNext := time.Date(2025, 10, 12, 3, 21, 0, 0, time.UTC)
	if !st.LastRun.Equal(wantLast) || !st.NextRun.Equal(wantNext) {
		t.Fatalf("dạng ngày: last=%v next=%v", st.LastRun, st.NextRun)
	}
	if !st.Enabled {
		t.Fatalf("enabled+active phải Enabled: %+v", st)
	}
}

// Hồi quy (e2e-nightly-real, v0.1.53): systemd tạo tệp stamp ngay khi timer Persistent=true khởi động lần
// đầu, nên mtime của nó KHÔNG phải lần chạy — lấy nó làm LastRun khiến máy vừa cài báo "BẬT NHƯNG KHÔNG CHẠY".
func TestStatusLinux_KhongLayMtimeStampLamLanChay(t *testing.T) {
	rr := &recRunner{}
	rr.on("show gen-harness-update.timer", showOut("loaded", "enabled", "active", "", "@1760086400"), nil)
	deps := nightlyDeps(t, rr)
	stamp := filepath.Join(deps.HomeDir, ".local", "share", "systemd", "timers", "stamp-"+TaskName+".timer")
	if err := os.MkdirAll(filepath.Dir(stamp), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(stamp, nil, 0o644); err != nil {
		t.Fatal(err)
	}
	mt := time.Date(2025, 10, 9, 3, 11, 0, 0, time.UTC)
	if err := os.Chtimes(stamp, mt, mt); err != nil {
		t.Fatal(err)
	}
	st, _ := GetStatus(context.Background(), deps)
	if !st.LastRun.IsZero() {
		t.Fatalf("LastTriggerUSec rỗng ⇒ LastRun phải zero (không lấy mtime stamp), được %v", st.LastRun)
	}
	if st.NextRun.IsZero() || !st.Enabled {
		t.Fatalf("NextRun/Enabled vẫn phải đọc từ systemctl show: %+v", st)
	}
}

func TestStatusLinux_ChuaBat_KhongUnitKhongCron(t *testing.T) {
	rr := &recRunner{}
	rr.on("show gen-harness-update.timer", showOut("not-found", "", "inactive", "", ""), nil)
	rr.on("crontab -l", "0 9 * * * viec-khac\n", nil)
	st, _ := GetStatus(context.Background(), nightlyDeps(t, rr))
	if st.Enabled || st.Mechanism != "" || st.UnitPresent {
		t.Fatalf("status = %+v", st)
	}
	mustContain(t, st.Detail, "crontab: không có dòng tự cập nhật")
}

func TestStatusLinux_Linger(t *testing.T) {
	for _, tc := range []struct{ out, want string }{{"yes\n", "yes"}, {"no\n", "no"}, {"\n", "unknown"}} {
		rr := &recRunner{}
		rr.on("show gen-harness-update.timer", showOut("loaded", "enabled", "active", "@1", "@2"), nil)
		rr.on("loginctl show-user 1000 -p Linger --value", tc.out, nil)
		st, _ := GetStatus(context.Background(), nightlyDeps(t, rr))
		if st.Linger != tc.want {
			t.Errorf("loginctl %q → Linger=%q, muốn %q", tc.out, st.Linger, tc.want)
		}
	}
}

func TestParseSystemdTime(t *testing.T) {
	loc := time.FixedZone("ICT", 7*3600)
	if got := parseSystemdTime("Sat 2025-10-11 03:07:12 ICT", loc); !got.Equal(time.Date(2025, 10, 11, 3, 7, 12, 0, loc)) {
		t.Errorf("tên múi giờ máy: %v", got)
	}
	if got := parseSystemdTime("Sat 2025-10-11 03:07:12 +07", loc); !got.Equal(time.Date(2025, 10, 11, 3, 7, 12, 0, loc)) {
		t.Errorf("múi giờ dạng +07: %v", got)
	}
	for _, empty := range []string{"", "n/a", "0", "@0", "@abc", "linh tinh"} {
		if got := parseSystemdTime(empty, loc); !got.IsZero() {
			t.Errorf("%q phải ra zero, được %v", empty, got)
		}
	}
}

// ─── Linger (F-95, C3) ──────────────────────────────────────────────────────

func TestEnableLinux_Linger(t *testing.T) {
	for _, tc := range []struct {
		name       string
		show       string
		enableErr  error
		wantWarn   bool
		wantLinger string
	}{
		{"linger=no", "no\n", nil, true, "no"},
		{"linger=yes", "yes\n", nil, false, "yes"},
		{"enable-linger lỗi nhưng linger đã yes", "yes\n", errExit1, false, "yes"},
		{"không hỏi được và enable-linger lỗi", "", errExit1, true, "unknown"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			rr := &recRunner{}
			rr.on("loginctl enable-linger", "", tc.enableErr)
			if tc.show == "" {
				rr.on("loginctl show-user", "", errExit1)
			} else {
				rr.on("loginctl show-user", tc.show, nil)
			}
			res, err := Enable(context.Background(), nightlyDeps(t, rr))
			if err != nil {
				t.Fatal(err)
			}
			if res.Linger != tc.wantLinger || (res.Warning != "") != tc.wantWarn {
				t.Fatalf("Linger=%q Warning=%q, muốn linger=%q warn=%v", res.Linger, res.Warning, tc.wantLinger, tc.wantWarn)
			}
			if tc.wantWarn {
				text := res.Text()
				mustContain(t, text, "sudo loginctl enable-linger $USER")
				mustContain(t, text, "CẢNH BÁO: linger đang TẮT")
				if strings.Contains(text, "enable-linger $USER.") {
					t.Errorf("không được có dấu chấm ngay sau lệnh:\n%s", text)
				}
			} else if res.Text() != res.Msg {
				t.Errorf("linger ổn thì không cảnh báo:\n%s", res.Text())
			}
			if !rr.ran("loginctl show-user 1000 -p Linger --value") {
				t.Errorf("phải hỏi lại linger bằng loginctl show-user: %v", rr.calls)
			}
		})
	}
}

// ─── RefreshUnits: sửa ExecStart + Environment (F-98, C5) ───────────────────

func TestRefreshUnits_SuaExecStartCungBinary_GiuDongKhac(t *testing.T) {
	rr := &recRunner{}
	deps := nightlyDeps(t, rr)
	deps.GenhPath = "/g/bin/genh"
	path := serviceUnitPath(deps.HomeDir)
	old := "[Unit]\nDescription=cu\n\n[Service]\nType=oneshot\nExecStart=/g/bin/genh update --yes --quiet\nNice=10\nEnvironment=HTTPS_PROXY=http://proxy:3128\nKillMode=mixed\nTimeoutStopSec=900\n"
	writeUnit(t, deps.HomeDir, TaskName+".service", old)

	rep, err := RefreshUnitsReport(context.Background(), deps)
	if err != nil || !rep.Changed {
		t.Fatalf("(i) report=%+v err=%v", rep, err)
	}
	if strings.Join(rep.What, ",") != "ExecStart,Environment=GEN_HARNESS_HOME" {
		t.Errorf("What = %v", rep.What)
	}
	b, _ := os.ReadFile(path)
	want := "[Unit]\nDescription=cu\n\n[Service]\nType=oneshot\nEnvironment=GEN_HARNESS_HOME=" + deps.InstallDir +
		"\nExecStart=/g/bin/genh update --yes --quiet --install-dir " + deps.InstallDir +
		"\nNice=10\nEnvironment=HTTPS_PROXY=http://proxy:3128\nKillMode=mixed\nTimeoutStopSec=900\n"
	if string(b) != want {
		t.Fatalf("(i) unit sau khi làm mới:\n%s\n--- muốn ---\n%s", b, want)
	}
	if !rr.ran("systemctl --user daemon-reload") || len(rr.calls) != 1 {
		t.Fatalf("chỉ được daemon-reload: %v", rr.calls)
	}

	// (iii) lần 2: không đổi gì, không gọi lệnh.
	rr.reset()
	changed, err := RefreshUnits(context.Background(), deps)
	if err != nil || changed || len(rr.calls) != 0 {
		t.Fatalf("(iii) changed=%v err=%v calls=%v", changed, err, rr.calls)
	}
	if b2, _ := os.ReadFile(path); string(b2) != string(b) {
		t.Fatal("(iii) unit bị đổi ở lần 2")
	}
}

func TestRefreshUnits_BinaryKhacConTonTai_GiuNguyen(t *testing.T) {
	rr := &recRunner{}
	deps := nightlyDeps(t, rr)
	other := filepath.Join(t.TempDir(), "genh-khac")
	if err := os.WriteFile(other, []byte("x"), 0o755); err != nil {
		t.Fatal(err)
	}
	cur := "[Service]\nKillMode=mixed\nTimeoutStopSec=900\nExecStart=" + other + " update --yes --quiet\nNice=10\n"
	writeUnit(t, deps.HomeDir, TaskName+".service", cur)
	changed, err := RefreshUnits(context.Background(), deps)
	if err != nil || changed || len(rr.calls) != 0 {
		t.Fatalf("(ii) binary khác còn tồn tại phải giữ nguyên: changed=%v err=%v calls=%v", changed, err, rr.calls)
	}
	if b, _ := os.ReadFile(serviceUnitPath(deps.HomeDir)); string(b) != cur {
		t.Fatalf("(ii) unit bị đổi:\n%s", b)
	}
}

func TestRefreshUnits_BinaryKhongConTonTai_ThayBangBinaryHienTai(t *testing.T) {
	rr := &recRunner{}
	deps := nightlyDeps(t, rr)
	gone := filepath.Join(t.TempDir(), "da-xoa", "genh")
	writeUnit(t, deps.HomeDir, TaskName+".service", "[Service]\nKillMode=mixed\nTimeoutStopSec=900\nExecStart="+gone+" update --yes --quiet\n")
	rep, err := RefreshUnitsReport(context.Background(), deps)
	if err != nil || !rep.Changed {
		t.Fatalf("report=%+v err=%v", rep, err)
	}
	b, _ := os.ReadFile(serviceUnitPath(deps.HomeDir))
	mustContain(t, string(b), "ExecStart=/g/bin/genh update --yes --quiet --install-dir "+deps.InstallDir)
	if strings.Contains(string(b), gone) {
		t.Errorf("binary đã mất phải được thay:\n%s", b)
	}
}

func TestRefreshUnits_DongEnvironmentKhacHuongSuaGEN_HARNESS_HOME(t *testing.T) {
	rr := &recRunner{}
	deps := nightlyDeps(t, rr)
	cur := "[Service]\nKillMode=mixed\nTimeoutStopSec=900\nEnvironment=GEN_HARNESS_HOME=/cu/khong-con\nEnvironment=A=1 B=2\nExecStart=/g/bin/genh update --yes --quiet --install-dir /cu/khong-con\n"
	writeUnit(t, deps.HomeDir, TaskName+".service", cur)
	rep, err := RefreshUnitsReport(context.Background(), deps)
	if err != nil || !rep.Changed {
		t.Fatalf("report=%+v err=%v", rep, err)
	}
	b, _ := os.ReadFile(serviceUnitPath(deps.HomeDir))
	mustContain(t, string(b), "Environment=GEN_HARNESS_HOME="+deps.InstallDir+"\n")
	mustContain(t, string(b), "Environment=A=1 B=2\n") // biến khác của Sếp giữ nguyên
}

func TestRefreshUnits_DonViCuaBanKhac_KhongDungExecStart(t *testing.T) {
	other := t.TempDir()
	writeSecrets(t, other)
	rr := &recRunner{}
	deps := nightlyDeps(t, rr)
	cur := "[Service]\nKillMode=mixed\nTimeoutStopSec=900\nEnvironment=GEN_HARNESS_HOME=" + other + "\nExecStart=/g/bin/genh update --yes --quiet --install-dir " + other + "\n"
	writeUnit(t, deps.HomeDir, TaskName+".service", cur)
	changed, err := RefreshUnits(context.Background(), deps)
	if err != nil || changed {
		t.Fatalf("unit thuộc bản cài khác còn sống không được sửa: changed=%v err=%v", changed, err)
	}
}

// ─── Trình nhận yêu cầu: trạng thái ─────────────────────────────────────────

func TestRequestWatcherState(t *testing.T) {
	for _, tc := range []struct{ out, want string }{{"active\n", "active"}, {"failed\n", "failed"}, {"inactive\n", "inactive"}, {"", "unknown"}, {"activating\n", "unknown"}} {
		rr := &recRunner{}
		rr.on("is-active gen-harness-update-request.path", tc.out, errExit1)
		if got := RequestWatcherState(context.Background(), Deps{Runner: rr, GOOS: "linux"}); got != tc.want {
			t.Errorf("%q → %q, muốn %q", tc.out, got, tc.want)
		}
	}
	if got := RequestWatcherState(context.Background(), Deps{Runner: &recRunner{}, GOOS: "darwin"}); got != "unknown" {
		t.Errorf("darwin → %q", got)
	}
}

// ─── trợ giúp ───────────────────────────────────────────────────────────────

func writeUnit(t *testing.T, home, name, content string) {
	t.Helper()
	p := filepath.Join(systemdUserDir(home), name)
	if err := os.MkdirAll(filepath.Dir(p), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(p, []byte(content), 0o644); err != nil {
		t.Fatal(err)
	}
}

func writeSecrets(t *testing.T, installDir string) {
	t.Helper()
	p := filepath.Join(installDir, "config", "secrets.json")
	if err := os.MkdirAll(filepath.Dir(p), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(p, []byte("{}"), 0o600); err != nil {
		t.Fatal(err)
	}
}

// ─── Trạng thái macOS / Windows (điền tối đa trường có thể) ──────────────────

func TestStatusDarwin(t *testing.T) {
	home := t.TempDir()
	log := filepath.Join(home, "auto-update.log")
	if err := os.WriteFile(log, []byte("x"), 0o644); err != nil {
		t.Fatal(err)
	}
	mt := time.Date(2026, 10, 9, 3, 12, 0, 0, time.UTC)
	_ = os.Chtimes(log, mt, mt)
	rr := &recRunner{}
	deps := Deps{Runner: rr, GenhPath: "/g/genh", HomeDir: home, GOOS: "darwin", LogFile: log, Location: time.UTC,
		Now: func() time.Time { return time.Date(2026, 10, 10, 1, 0, 0, 0, time.UTC) }, InstallDir: "/i", Nightly: NightlyJob{InstallDir: "/i"}}

	// Chưa có plist.
	if st, _ := GetStatus(context.Background(), deps); st.Enabled || st.UnitPresent || st.Mechanism != "" {
		t.Fatalf("chưa có plist: %+v", st)
	}
	// Bật: plist + launchctl list có nhãn.
	rr.on("launchctl load", "", nil)
	if _, err := Enable(context.Background(), deps); err != nil {
		t.Fatal(err)
	}
	rr.on("launchctl list", "-\t0\t"+launchAgentLabel()+"\n", nil)
	st, err := GetStatus(context.Background(), deps)
	if err != nil || !st.Enabled || st.Mechanism != ScheduleLaunchd || !st.UnitPresent || st.Active != "active" || st.Linger != "not_applicable" || st.Owner != "/i" {
		t.Fatalf("status = %+v, %v", st, err)
	}
	if !st.LastRun.Equal(mt) {
		t.Errorf("LastRun = mtime log: %v", st.LastRun)
	}
	if st.NextRun.IsZero() || st.NextRun.Hour() != 3 || !st.NextRun.After(deps.Now()) || st.NextRun.Sub(deps.Now()) > 24*time.Hour {
		t.Errorf("NextRun (03:<phút> kế tiếp) = %v", st.NextRun)
	}
	// Đã có plist nhưng chưa nạp.
	rr.rules = nil
	st, _ = GetStatus(context.Background(), deps)
	if st.Enabled || !st.UnitPresent || st.Active != "inactive" {
		t.Errorf("chưa nạp: %+v", st)
	}
}

func TestStatusWindows(t *testing.T) {
	rr := &recRunner{}
	rr.on("schtasks /Query", "TaskName: \\gen-harness-update\nNext Run Time: 10/11/2026 3:00:00 AM\nStatus: Ready\nLast Run Time: 10/10/2026 3:00:01 AM\nScheduled Task State: Enabled\n", nil)
	st, err := GetStatus(context.Background(), Deps{Runner: rr, GOOS: "windows"})
	if err != nil || !st.Enabled || st.Mechanism != ScheduleSchtasks || !st.UnitPresent || st.Active != "active" || st.Linger != "not_applicable" {
		t.Fatalf("status = %+v, %v", st, err)
	}
	if want := time.Date(2026, 10, 10, 3, 0, 1, 0, time.Local); !st.LastRun.Equal(want) {
		t.Errorf("LastRun = %v, muốn %v", st.LastRun, want)
	}
	if want := time.Date(2026, 10, 11, 3, 0, 0, 0, time.Local); !st.NextRun.Equal(want) {
		t.Errorf("NextRun = %v, muốn %v", st.NextRun, want)
	}
	if !rr.ran("/V") {
		t.Error("phải hỏi /V để có Next/Last Run Time")
	}
	// "Chưa từng chạy" và Disabled.
	rr = &recRunner{}
	rr.on("schtasks /Query", "Next Run Time: N/A\nLast Run Time: 11/30/1999 12:00:00 AM\nStatus: Disabled\n", nil)
	st, _ = GetStatus(context.Background(), Deps{Runner: rr, GOOS: "windows"})
	if st.Enabled || !st.LastRun.IsZero() || !st.NextRun.IsZero() || st.Active != "inactive" {
		t.Errorf("Disabled/chưa chạy: %+v", st)
	}
}

// Lịch đêm bật bằng Task Scheduler mang --install-dir (không ghi đè lịch bản khác — Windows chưa bảo vệ,
// nhưng đối số phải đúng).
func TestEnableWindows_MangInstallDir(t *testing.T) {
	rr := &recRunner{}
	res, err := Enable(context.Background(), Deps{Runner: rr, GenhPath: `C:\g\genh.exe`, LogFile: `C:\g\log.txt`, GOOS: "windows",
		Nightly: NightlyJob{InstallDir: `C:\Users\o\GenHarness`, Port: 9443}})
	if err != nil || res.Mechanism != ScheduleSchtasks {
		t.Fatalf("%+v %v", res, err)
	}
	if !rr.ran(`update --yes --quiet --install-dir C:\Users\o\GenHarness --port 9443`) {
		t.Fatalf("%v", rr.calls)
	}
}
