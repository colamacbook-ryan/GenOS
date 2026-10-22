package autoupdate

import (
	"context"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// ─── Hồi quy H-a (F-93/F-98): tên unit systemd --user là CHUNG cho mọi bản cài ───
//
// Chạy trên MÃ CŨ (trước v0.1.53) cả hai test này ĐỎ:
//   - Uninstall bản phụ: tệp gen-harness-update.timer + .path của bản chính bị xoá và có lệnh
//     `systemctl --user disable --now gen-harness-update.timer` (runUninstall dùng Deps{} rỗng);
//   - Update/install bản phụ: gen-harness-update-request.path bị ghi đè sang hộp thư bản phụ.

type twoInstalls struct {
	home, mainDir, otherDir string
	rr                      *recRunner
}

func newTwoInstalls(t *testing.T) twoInstalls {
	t.Helper()
	base := t.TempDir()
	ti := twoInstalls{home: t.TempDir(), mainDir: filepath.Join(base, "main"), otherDir: filepath.Join(base, "other"), rr: &recRunner{}}
	writeSecrets(t, ti.mainDir)
	return ti
}

func (ti twoInstalls) deps(installDir string) Deps {
	return Deps{
		Runner: ti.rr, GenhPath: "/g/bin/genh", LogFile: filepath.Join(installDir, "logs", "auto-update.log"),
		HomeDir: ti.home, GOOS: "linux", UID: "1000",
		LookPath:   func(string) (string, error) { return "/usr/bin/systemctl", nil },
		InstallDir: installDir, Nightly: NightlyJob{InstallDir: installDir},
	}
}

func (ti twoInstalls) requestPaths(dir string) RequestPaths {
	return RequestPaths{InstallDir: dir, RequestDir: filepath.Join(dir, "run", "request"), RequestFile: filepath.Join(dir, "run", "request", "update.json")}
}

func TestRegressionUninstallOtherInstallKeepsNightly(t *testing.T) {
	ti := newTwoInstalls(t)
	ctx := context.Background()
	// Bản chính bật lịch đêm + trình nhận yêu cầu + trực canh + bản sao ngoài máy.
	if _, err := Enable(ctx, ti.deps(ti.mainDir)); err != nil {
		t.Fatal(err)
	}
	if _, err := EnsureRequestWatcher(ctx, ti.deps(ti.mainDir), ti.requestPaths(ti.mainDir)); err != nil {
		t.Fatal(err)
	}
	if _, _, err := EnableWatchdog(ctx, ti.deps(ti.mainDir), WatchdogJob{InstallDir: ti.mainDir}); err != nil {
		t.Fatal(err)
	}
	if _, _, err := EnableOffsite(ctx, ti.deps(ti.mainDir), OffsiteJob{InstallDir: ti.mainDir}); err != nil {
		t.Fatal(err)
	}
	dir := systemdUserDir(ti.home)
	files := []string{TaskName + ".timer", TaskName + ".service", RequestTaskName + ".path", RequestTaskName + ".service",
		WatchdogTaskName + ".timer", WatchdogTaskName + ".service", OffsiteTaskName + ".timer", OffsiteTaskName + ".service"}
	before := map[string]string{}
	for _, f := range files {
		b, err := os.ReadFile(filepath.Join(dir, f))
		if err != nil {
			t.Fatalf("bản chính phải ghi %s: %v", f, err)
		}
		before[f] = string(b)
	}

	// `genh uninstall --install-dir <bản phụ>`.
	ti.rr.reset()
	other := ti.deps(ti.otherDir)
	msg, err := Disable(ctx, other)
	if err != nil || !strings.HasPrefix(msg, "Giữ nguyên lịch tự cập nhật đêm của bản cài "+ti.mainDir) || !strings.HasSuffix(msg, "(không phải bản đang gỡ).") {
		t.Fatalf("Disable bản phụ: %q, %v", msg, err)
	}
	if m := DisableRequestWatcher(ctx, other); !strings.HasPrefix(m, "Giữ nguyên") || !strings.Contains(m, ti.mainDir) {
		t.Errorf("DisableRequestWatcher bản phụ: %q", m)
	}
	if m, err := DisableWatchdog(ctx, other); err != nil || !strings.HasPrefix(m, "Giữ nguyên lịch trực canh máy chủ của bản cài "+ti.mainDir) {
		t.Errorf("DisableWatchdog bản phụ: %q, %v", m, err)
	}
	if m, err := DisableOffsite(ctx, other); err != nil || !strings.HasPrefix(m, "Giữ nguyên lịch sao lưu ra ổ ngoài của bản cài "+ti.mainDir) {
		t.Errorf("DisableOffsite bản phụ: %q, %v", m, err)
	}

	for _, f := range files {
		b, err := os.ReadFile(filepath.Join(dir, f))
		if err != nil || string(b) != before[f] {
			t.Errorf("%s của bản chính phải còn nguyên khi gỡ bản phụ: err=%v", f, err)
		}
	}
	for _, bad := range []string{"disable", "stop", "mask", "daemon-reload", "crontab /"} {
		if ti.rr.ran(bad) {
			t.Errorf("gỡ bản phụ KHÔNG được gọi %q: %v", bad, ti.rr.calls)
		}
	}
	if ti.rr.ran("disable --now gen-harness-update.timer") {
		t.Fatal("ĐỎ trên mã cũ: đã disable --now gen-harness-update.timer của bản chính")
	}

	// Gỡ chính bản chính thì vẫn gỡ đủ.
	if msg, err := Disable(ctx, ti.deps(ti.mainDir)); err != nil || msg != "Đã tắt tự cập nhật hằng đêm." {
		t.Fatalf("Disable bản chính: %q, %v", msg, err)
	}
	if _, err := os.Stat(filepath.Join(dir, TaskName+".timer")); !os.IsNotExist(err) {
		t.Errorf("gỡ bản chính phải xoá timer: %v", err)
	}
	DisableRequestWatcher(ctx, ti.deps(ti.mainDir))
	if _, err := os.Stat(filepath.Join(dir, RequestTaskName+".path")); !os.IsNotExist(err) {
		t.Errorf("gỡ bản chính phải xoá .path: %v", err)
	}
}

func TestRegressionUpdateOtherInstallKeepsWatcher(t *testing.T) {
	ti := newTwoInstalls(t)
	ctx := context.Background()
	if _, err := EnsureRequestWatcher(ctx, ti.deps(ti.mainDir), ti.requestPaths(ti.mainDir)); err != nil {
		t.Fatal(err)
	}
	dir := systemdUserDir(ti.home)
	pathBefore, _ := os.ReadFile(filepath.Join(dir, RequestTaskName+".path"))
	svcBefore, _ := os.ReadFile(filepath.Join(dir, RequestTaskName+".service"))
	mustContain(t, string(pathBefore), "PathExists="+ti.requestPaths(ti.mainDir).RequestFile)

	// `genh install|update --install-dir <bản phụ>` → publishHostInfo → EnsureRequestWatcher.
	ti.rr.reset()
	_, err := EnsureRequestWatcher(ctx, ti.deps(ti.otherDir), ti.requestPaths(ti.otherDir))
	if !errors.Is(err, ErrScheduleOwnedByOther) {
		t.Fatalf("muốn ErrScheduleOwnedByOther, được %v", err)
	}
	other, ok := OwnerOf(err)
	if !ok || other != ti.mainDir {
		t.Errorf("OwnerOf = %q, %v", other, ok)
	}
	mustContain(t, err.Error(), "Máy này có bản cài khác đang giữ lịch đêm/nút Cập nhật ngay ("+ti.mainDir+") — bản cài "+ti.otherDir+" không đổi lịch.")
	pathAfter, _ := os.ReadFile(filepath.Join(dir, RequestTaskName+".path"))
	svcAfter, _ := os.ReadFile(filepath.Join(dir, RequestTaskName+".service"))
	if string(pathAfter) != string(pathBefore) || string(svcAfter) != string(svcBefore) {
		t.Fatalf("ĐỎ trên mã cũ: .path/.service của bản chính bị ghi đè:\n%s", pathAfter)
	}
	if ti.rr.ran("--user enable") || ti.rr.ran("reset-failed") {
		t.Errorf("không được đổi lịch của bản chính: %v", ti.rr.calls)
	}

	// Lịch đêm, trực canh, bản sao ngoài máy của bản phụ cũng không ghi đè.
	if _, err := Enable(ctx, ti.deps(ti.mainDir)); err != nil {
		t.Fatal(err)
	}
	if _, err := Enable(ctx, ti.deps(ti.otherDir)); !errors.Is(err, ErrScheduleOwnedByOther) {
		t.Errorf("Enable lịch đêm của bản phụ: %v", err)
	}
	if _, _, err := EnableWatchdog(ctx, ti.deps(ti.mainDir), WatchdogJob{InstallDir: ti.mainDir}); err != nil {
		t.Fatal(err)
	}
	if _, _, err := EnableWatchdog(ctx, Deps{Runner: ti.rr, GenhPath: "/g/bin/genh", HomeDir: ti.home, GOOS: "linux",
		LookPath: func(string) (string, error) { return "/usr/bin/systemctl", nil }}, WatchdogJob{InstallDir: ti.otherDir}); !errors.Is(err, ErrScheduleOwnedByOther) {
		t.Errorf("EnableWatchdog của bản phụ (chỉ job.InstallDir): %v", err)
	}
	if _, _, err := EnableOffsite(ctx, ti.deps(ti.mainDir), OffsiteJob{InstallDir: ti.mainDir}); err != nil {
		t.Fatal(err)
	}
	if _, _, err := EnableOffsite(ctx, ti.deps(ti.otherDir), OffsiteJob{InstallDir: ti.otherDir}); !errors.Is(err, ErrScheduleOwnedByOther) {
		t.Errorf("EnableOffsite của bản phụ: %v", err)
	}
	svc, _ := os.ReadFile(serviceUnitPath(ti.home))
	mustContain(t, string(svc), "Environment=GEN_HARNESS_HOME="+ti.mainDir)
	if strings.Contains(string(svc), ti.otherDir) {
		t.Errorf("unit lịch đêm bị trỏ sang bản phụ:\n%s", svc)
	}
}

// Bản cài kia đã gỡ (không còn config/secrets.json) ⇒ được phép ghi đè/gỡ lịch của nó.
func TestOwner_BanKiaDaGo_DuocGhiDeVaGo(t *testing.T) {
	ti := newTwoInstalls(t)
	ctx := context.Background()
	if _, err := Enable(ctx, ti.deps(ti.mainDir)); err != nil {
		t.Fatal(err)
	}
	if _, err := EnsureRequestWatcher(ctx, ti.deps(ti.mainDir), ti.requestPaths(ti.mainDir)); err != nil {
		t.Fatal(err)
	}
	if err := os.Remove(filepath.Join(ti.mainDir, "config", "secrets.json")); err != nil { // bản chính đã gỡ
		t.Fatal(err)
	}

	// Bản phụ ghi đè được.
	if _, err := EnsureRequestWatcher(ctx, ti.deps(ti.otherDir), ti.requestPaths(ti.otherDir)); err != nil {
		t.Fatalf("bản chủ đã gỡ thì bản khác được giữ lịch: %v", err)
	}
	b, _ := os.ReadFile(filepath.Join(systemdUserDir(ti.home), RequestTaskName+".path"))
	mustContain(t, string(b), ti.requestPaths(ti.otherDir).RequestFile)
	if _, err := Enable(ctx, ti.deps(ti.otherDir)); err != nil {
		t.Fatalf("Enable lịch đêm: %v", err)
	}
	// Và gỡ được.
	writeSecrets(t, ti.mainDir)
	_ = os.Remove(filepath.Join(ti.otherDir, "config", "secrets.json"))
	if msg, _ := Disable(ctx, ti.deps(ti.mainDir)); strings.HasPrefix(msg, "Giữ nguyên") {
		// lịch giờ thuộc other (đã gỡ, không còn secrets.json) nên main được gỡ
		t.Errorf("chủ lịch đã gỡ thì bản khác gỡ được: %q", msg)
	}
	if _, err := os.Stat(timerUnitPath(ti.home)); !os.IsNotExist(err) {
		t.Errorf("timer phải bị gỡ: %v", err)
	}
}

// legacyHome đặt HOME (USERPROFILE trên Windows; LOCALAPPDATA rỗng) để gốc mặc định theo
// HOME (config.HomeRoot) là <tạm>/.gen-harness, ghi secrets cho bản đó; trả về thư mục đó.
func legacyHome(t *testing.T) string {
	t.Helper()
	h := t.TempDir()
	t.Setenv("HOME", h)
	t.Setenv("USERPROFILE", h)
	t.Setenv("LOCALAPPDATA", "")
	dir := filepath.Join(h, ".gen-harness")
	writeSecrets(t, dir)
	return dir
}

// Unit cũ (trước v0.1.53) không có GEN_HARNESS_HOME/--install-dir ⇒ coi là bản mặc định THEO HOME.
func TestOwner_UnitCuKhongGhiBanCai_LaBanMacDinh(t *testing.T) {
	ti := newTwoInstalls(t)
	ti.mainDir = legacyHome(t) // bản mặc định theo HOME = bản chính
	writeUnit(t, ti.home, TaskName+".service", "[Service]\nType=oneshot\nExecStart=/g/bin/genh update --yes --quiet\n")
	writeUnit(t, ti.home, TaskName+".timer", "[Timer]\nOnCalendar=*-*-* 03:00:00\n")

	if dir, found := UnitInstallDir(ti.home, TaskName+".service"); !found || dir != ti.mainDir {
		t.Fatalf("UnitInstallDir(unit cũ) = %q, %v — muốn bản mặc định %q", dir, found, ti.mainDir)
	}
	if _, found := UnitInstallDir(ti.home, "khong-co.service"); found {
		t.Error("unit không tồn tại ⇒ found=false")
	}
	if other, yes := OwnedByOther(ti.home, TaskName+".service", ti.mainDir); yes {
		t.Errorf("bản mặc định tự gỡ lịch cũ của mình: other=%q", other)
	}
	other, yes := OwnedByOther(ti.home, TaskName+".service", ti.otherDir)
	if !yes || other != ti.mainDir {
		t.Fatalf("bản phụ không được đụng lịch cũ của bản mặc định: %q %v", other, yes)
	}
	if msg, _ := Disable(context.Background(), ti.deps(ti.otherDir)); !strings.HasPrefix(msg, "Giữ nguyên") {
		t.Errorf("Disable bản phụ: %q", msg)
	}
	if _, err := os.Stat(timerUnitPath(ti.home)); err != nil {
		t.Errorf("timer của bản mặc định phải còn: %v", err)
	}
}

func TestUnitInstallDir_DocEnvVaExecStart(t *testing.T) {
	home := t.TempDir()
	writeUnit(t, home, "a.service", "[Service]\nEnvironment=\"GEN_HARNESS_HOME=/co khoang trang/gh\"\nExecStart=/g/genh update\n")
	writeUnit(t, home, "b.service", "[Service]\nExecStart=\"/g/genh\" update --yes --quiet --install-dir \"/b dir\" --port 9443\n")
	writeUnit(t, home, "c.service", "[Service]\nExecStart=/g/genh update --install-dir=/c/dir\n")
	writeUnit(t, home, "d.service", "[Service]\nEnvironment=GENH_COMPOSE_FILE=/x\nEnvironment=GEN_HARNESS_HOME=/d/env\nExecStart=/g/genh update --install-dir /d/arg\n")
	for name, want := range map[string]string{"a.service": "/co khoang trang/gh", "b.service": "/b dir", "c.service": "/c/dir", "d.service": "/d/env"} {
		if got, ok := UnitInstallDir(home, name); !ok || got != want {
			t.Errorf("%s: %q, %v — muốn %q", name, got, ok, want)
		}
	}
}

// Đường dẫn kiểu Windows (genh ghi unit KHÔNG thoát \) phải đọc lại nguyên vẹn; \ chỉ là ký tự
// thoát trước khoảng trắng/dấu nháy/\ (ma trận Installer windows-2022 đỏ ở v0.1.53 trước sửa này).
func TestSplitWords_DuongDanWindowsVaKyTuThoat(t *testing.T) {
	got := splitWords(`/g/genh --install-dir C:\Users\RUNNER~1\AppData\Local\Temp\x a\ b "c\"d" "e\\f" "C:\Temp\y" 'g\h'`)
	want := []string{"/g/genh", "--install-dir", `C:\Users\RUNNER~1\AppData\Local\Temp\x`, "a b", `c"d`, `e\f`, `C:\Temp\y`, `g\h`}
	if strings.Join(got, "|") != strings.Join(want, "|") {
		t.Fatalf("splitWords:\n được %q\n muốn %q", got, want)
	}
	home := t.TempDir()
	writeUnit(t, home, "w.service", "[Service]\nEnvironment=GEN_HARNESS_HOME=C:\\Users\\o\\gh\nExecStart=C:\\g\\genh.exe update --install-dir C:\\Users\\o\\gh\n")
	if d, ok := UnitInstallDir(home, "w.service"); !ok || d != `C:\Users\o\gh` {
		t.Fatalf("UnitInstallDir = %q, %v", d, ok)
	}
}

func TestSamePath_ClearVaSymlink(t *testing.T) {
	real := t.TempDir()
	link := filepath.Join(t.TempDir(), "lien-ket")
	if err := os.Symlink(real, link); err != nil {
		t.Skip("không tạo được symlink:", err)
	}
	if !samePath(real, link) || !samePath(real+"/./", real) {
		t.Error("symlink/Clean phải coi là cùng bản cài")
	}
	if samePath(real, filepath.Join(real, "con")) {
		t.Error("thư mục con không phải cùng bản cài")
	}
	// Lỗi EvalSymlinks (đường dẫn chưa tồn tại) ⇒ so chuỗi đã Clean.
	if !samePath("/khong/ton/tai/a/../b", "/khong/ton/tai/b") || samePath("/khong/ton/tai/a", "/khong/ton/tai/b") {
		t.Error("đường dẫn chưa tồn tại: so chuỗi Clean")
	}
}

// Cron: dòng cron do genh ghi mang bản cài chủ ⇒ cũng được bảo vệ.
func TestOwner_Crontab(t *testing.T) {
	ti := newTwoInstalls(t)
	ctx := context.Background()
	line := CrontabLine("/g/genh", "/g/log", 7, NightlyJob{InstallDir: ti.mainDir})
	ti.rr.on("crontab -l", MergeCrontab("0 9 * * * viec-khac\n", line, false), nil)
	if dir, found := crontabInstallDir(MergeCrontab("", line, false), CrontabMarker); !found || dir != ti.mainDir {
		t.Fatalf("crontabInstallDir = %q, %v", dir, found)
	}
	// Không systemd --user: Enable của bản phụ bị chặn, Disable giữ nguyên.
	noSystemd := func(d Deps) Deps {
		d.LookPath = func(string) (string, error) { return "", os.ErrNotExist }
		return d
	}
	if _, err := Enable(ctx, noSystemd(ti.deps(ti.otherDir))); !errors.Is(err, ErrScheduleOwnedByOther) {
		t.Fatalf("Enable cron bản phụ: %v", err)
	}
	if ti.rr.ran("crontab /") {
		t.Error("không được cài lại crontab")
	}
	if msg, _ := Disable(ctx, noSystemd(ti.deps(ti.otherDir))); !strings.HasPrefix(msg, "Giữ nguyên") {
		t.Errorf("Disable cron bản phụ: %q", msg)
	}
	if ti.rr.ran("crontab /") {
		t.Error("Disable bản phụ không được ghi crontab")
	}
	st, _ := GetStatus(ctx, noSystemd(ti.deps(ti.otherDir)))
	if !st.Enabled || !st.OwnedByOther || st.Owner != ti.mainDir {
		t.Errorf("status của bản phụ: %+v", st)
	}
}

// Không có InstallDir (hành vi cũ): không bảo vệ gì.
func TestOwner_KhongInstallDirThiKhongBaoVe(t *testing.T) {
	ti := newTwoInstalls(t)
	ctx := context.Background()
	if _, err := Enable(ctx, ti.deps(ti.mainDir)); err != nil {
		t.Fatal(err)
	}
	legacy := ti.deps("")
	legacy.Nightly = NightlyJob{}
	if msg, err := Disable(ctx, legacy); err != nil || msg != "Đã tắt tự cập nhật hằng đêm." {
		t.Fatalf("Disable cũ: %q, %v", msg, err)
	}
	if _, err := os.Stat(timerUnitPath(ti.home)); !os.IsNotExist(err) {
		t.Error("không có InstallDir ⇒ gỡ như cũ")
	}
}

// $GEN_HARNESS_HOME của NGƯỜI GỌI không đổi chủ của lịch cũ: `GEN_HARNESS_HOME=<bản phụ> genh
// uninstall` (không --install-dir) không được coi lịch cũ (chạy ngoài phiên shell, luôn làm việc
// trên gốc theo HOME) là của bản phụ rồi gỡ mất lịch của bản chính.
func TestOwner_UnitCu_KhongDocGEN_HARNESS_HOMECuaNguoiGoi(t *testing.T) {
	ti := newTwoInstalls(t)
	ti.mainDir = legacyHome(t)
	t.Setenv("GEN_HARNESS_HOME", ti.otherDir)
	writeSecrets(t, ti.otherDir)
	writeUnit(t, ti.home, TaskName+".service", "[Service]\nType=oneshot\nExecStart=/g/bin/genh update --yes --quiet\n")
	writeUnit(t, ti.home, TaskName+".timer", "[Timer]\nOnCalendar=*-*-* 03:00:00\n")
	if dir, found := UnitInstallDir(ti.home, TaskName+".service"); !found || dir != ti.mainDir {
		t.Fatalf("UnitInstallDir(unit cũ) = %q — muốn gốc theo HOME %q, không phải $GEN_HARNESS_HOME", dir, ti.mainDir)
	}
	if msg, _ := Disable(context.Background(), ti.deps(ti.otherDir)); !strings.HasPrefix(msg, "Giữ nguyên") {
		t.Errorf("bản phụ (qua GEN_HARNESS_HOME) không được gỡ lịch cũ của bản chính: %q", msg)
	}
	if _, err := os.Stat(timerUnitPath(ti.home)); err != nil {
		t.Errorf("timer của bản chính phải còn: %v", err)
	}
	// Dòng cron cũ cũng vậy.
	if d, ok := crontabInstallDir(CrontabMarker+"\n7 3 * * * /g/genh update --yes --quiet >> /l 2>&1\n", CrontabMarker); !ok || d != ti.mainDir {
		t.Errorf("crontab cũ: %q %v", d, ok)
	}
}

// Windows (F-98): Task Scheduler cũng là lịch dùng chung — bản cài phụ không /F ghi đè, không
// xoá Task của bản chính còn sống; status báo đúng chủ.
func TestOwner_Windows_TaskCuaBanChinh(t *testing.T) {
	ti := newTwoInstalls(t)
	writeSecrets(t, ti.otherDir)
	tr := SchtasksCreateArgs(`C:\g\genh.exe`, `C:\g\log.txt`, NightlyJob{InstallDir: ti.mainDir})[4]
	query := "Folder: \\\r\nHostName:      PC\r\nTaskName:      \\" + TaskName + "\r\nNext Run Time: N/A\r\nStatus:        Ready\r\n" +
		"Task To Run:   " + tr + "\r\nLast Run Time: N/A\r\n"
	rr := (&recRunner{}).on("schtasks /Query", query, nil)
	win := func(dir string) Deps {
		d := ti.deps(dir)
		d.GOOS, d.Runner, d.GenhPath = "windows", rr, `C:\g\genh.exe`
		return d
	}
	ctx := context.Background()

	if _, err := Enable(ctx, win(ti.otherDir)); !errors.Is(err, ErrScheduleOwnedByOther) {
		t.Fatalf("Enable bản phụ phải bị từ chối: %v", err)
	}
	if msg, err := Disable(ctx, win(ti.otherDir)); err != nil || !strings.HasPrefix(msg, "Giữ nguyên lịch tự cập nhật đêm của bản cài "+ti.mainDir) {
		t.Fatalf("Disable bản phụ: %q %v", msg, err)
	}
	for _, c := range rr.calls {
		if strings.Contains(c, "/Create") || strings.Contains(c, "/Delete") {
			t.Fatalf("bản phụ không được ghi/xoá Task của bản chính: %v", rr.calls)
		}
	}
	st, err := GetStatus(ctx, win(ti.otherDir))
	if err != nil || st.Owner != ti.mainDir || !st.OwnedByOther {
		t.Fatalf("status bản phụ: %+v %v", st, err)
	}

	// Chính bản chính: được ghi đè/xoá như thường.
	rr.calls = nil
	if st, _ := GetStatus(ctx, win(ti.mainDir)); st.OwnedByOther {
		t.Errorf("bản chính: OwnedByOther phải false: %+v", st)
	}
	if _, err := Enable(ctx, win(ti.mainDir)); err != nil {
		t.Fatalf("Enable bản chính: %v", err)
	}
	if msg, _ := Disable(ctx, win(ti.mainDir)); msg != "Đã tắt tự cập nhật hằng đêm." {
		t.Fatalf("Disable bản chính: %q", msg)
	}
	created, deleted := false, false
	for _, c := range rr.calls {
		created = created || strings.Contains(c, "/Create")
		deleted = deleted || strings.Contains(c, "/Delete")
	}
	if !created || !deleted {
		t.Errorf("bản chính phải /Create rồi /Delete: %v", rr.calls)
	}

	// Bản chính đã gỡ (không còn secrets.json) ⇒ bản khác được thay.
	_ = os.Remove(filepath.Join(ti.mainDir, "config", "secrets.json"))
	if _, err := Enable(ctx, win(ti.otherDir)); err != nil {
		t.Errorf("chủ Task đã gỡ thì bản khác bật được: %v", err)
	}
}

func TestSchtasksOwner(t *testing.T) {
	legacy := legacyHome(t)
	for _, c := range []struct {
		name, text, want string
		found            bool
	}{
		{"có --install-dir", "Task To Run: " + SchtasksCreateArgs(`C:\g\genh.exe`, `C:\l`, NightlyJob{InstallDir: `C:\Users\o\gh`})[4], `C:\Users\o\gh`, true},
		{"đường dẫn có khoảng trắng", "Task To Run: " + SchtasksCreateArgs(`C:\g\genh.exe`, `C:\l`, NightlyJob{InstallDir: `C:\Users\o\Gen Harness`})[4], `C:\Users\o\Gen Harness`, true},
		{"nhãn bản địa hoá", "Auszuführende Aufgabe: " + SchtasksCreateArgs(`C:\g\genh.exe`, `C:\l`, NightlyJob{InstallDir: `D:\gh`})[4] + "\r", `D:\gh`, true},
		{"Task cũ không --install-dir ⇒ gốc theo HOME", "Task To Run: " + SchtasksCreateArgs(`C:\g\genh.exe`, `C:\l`, NightlyJob{})[4], legacy, true},
		{"không có lệnh genh", "Status: Ready\r\nTask To Run: notepad.exe\r\n", "", false},
	} {
		got, found := schtasksOwner(c.text)
		if got != c.want || found != c.found {
			t.Errorf("%s: schtasksOwner = %q, %v — muốn %q, %v", c.name, got, found, c.want, c.found)
		}
	}
}
