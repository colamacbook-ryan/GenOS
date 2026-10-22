package autoupdate

import (
	"context"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

var testWatchdogJob = WatchdogJob{InstallDir: "/home/u/.gen-harness", Port: 9443, Env: []string{"GENH_COMPOSE_FILE=/src/deploy/compose.yaml"}}

func TestSystemdWatchdogUnits(t *testing.T) {
	svc := SystemdWatchdogServiceUnit("/home/u/My Apps/genh", "/home/u/.gen-harness/logs/watchdog.log",
		WatchdogJob{InstallDir: "/home/u/Gen Harness", Env: []string{"GENH_COMPOSE_FILE=/src/deploy/compose.yaml"}})
	for _, want := range []string{
		"Type=oneshot", "Nice=10", "TimeoutStartSec=300",
		`ExecStart="/home/u/My Apps/genh" doctor --notify --quiet --install-dir "/home/u/Gen Harness"`,
		`Environment="GEN_HARNESS_HOME=/home/u/Gen Harness"`,
		"Environment=GENH_COMPOSE_FILE=/src/deploy/compose.yaml",
		"StandardOutput=append:/home/u/.gen-harness/logs/watchdog.log",
		"StandardError=append:/home/u/.gen-harness/logs/watchdog.log",
	} {
		mustContain(t, svc, want)
	}
	if strings.Contains(svc, "--port") {
		t.Fatalf("cổng mặc định (0) không truyền --port:\n%s", svc)
	}
	mustContain(t, SystemdWatchdogServiceUnit("/g/genh", "/g/log", testWatchdogJob), "--install-dir /home/u/.gen-harness --port 9443")
	tim := SystemdWatchdogTimerUnit()
	for _, want := range []string{"OnBootSec=5min", "OnUnitActiveSec=12min", "AccuracySec=1min", "WantedBy=timers.target"} {
		mustContain(t, tim, want)
	}
}

func TestWatchdogCrontabLine_MarkerVaMerge(t *testing.T) {
	line := WatchdogCrontabLine("/g/genh", "/g/logs/watchdog.log", testWatchdogJob)
	if !strings.HasPrefix(line, "*/12 * * * * ") {
		t.Fatalf("phải chạy mỗi 12 phút: %q", line)
	}
	mustContain(t, line, "GEN_HARNESS_HOME=/home/u/.gen-harness GENH_COMPOSE_FILE=/src/deploy/compose.yaml /g/genh doctor --notify --quiet --install-dir /home/u/.gen-harness --port 9443 >> /g/logs/watchdog.log 2>&1")
	existing := "0 1 * * * backup.sh\n" + CrontabMarker + "\n7 3 * * * genh update --yes --quiet\n" +
		OffsiteCrontabMarker + "\n37 5 * * 0 genh offsite run --quiet\n"
	merged := MergeWatchdogCrontab(existing, line, false)
	for _, want := range []string{"backup.sh", "update --yes --quiet", "offsite run", WatchdogCrontabMarker + "\n" + line} {
		mustContain(t, merged, want)
	}
	if again := MergeWatchdogCrontab(merged, line, false); strings.Count(again, WatchdogCrontabMarker) != 1 {
		t.Fatalf("bật lại không được nhân đôi:\n%s", again)
	}
	removed := MergeWatchdogCrontab(merged, "", true)
	if strings.Contains(removed, "doctor --notify") || !strings.Contains(removed, "offsite run") || !strings.Contains(removed, "update --yes") {
		t.Fatalf("gỡ trực canh phải giữ các dòng khác:\n%s", removed)
	}
	if r := MergeOffsiteCrontab(merged, "", true); !strings.Contains(r, WatchdogCrontabMarker) {
		t.Fatalf("tắt lịch tuần không được gỡ trực canh:\n%s", r)
	}
}

func TestWatchdogLaunchdPlist(t *testing.T) {
	p := WatchdogLaunchdPlist("/Applications/Gen Harness/genh", "/u/logs/watchdog.log", testWatchdogJob)
	for _, want := range []string{
		"<string>" + WatchdogLaunchdLabel + "</string>",
		"<key>StartInterval</key>\n\t<integer>720</integer>",
		"\t\t<string>doctor</string>\n\t\t<string>--notify</string>\n\t\t<string>--quiet</string>\n",
		"<key>GENH_COMPOSE_FILE</key>",
		"<string>/u/logs/watchdog.log</string>",
	} {
		mustContain(t, p, want)
	}
}

func TestWatchdogSchtasksArgs(t *testing.T) {
	args := WatchdogSchtasksCreateArgs(`C:\Users\u\genh.exe`, `C:\Users\u\.gen-harness\logs\watchdog.log`, WatchdogJob{InstallDir: `C:\Users\u\Gen Harness`})
	joined := strings.Join(args, " ")
	for _, want := range []string{"/Create /TN gen-harness-watchdog", "/SC MINUTE", "/MO 12", "/RL LIMITED", "/F"} {
		mustContain(t, joined, want)
	}
	if args[4] != `cmd.exe /c ""C:\Users\u\genh.exe" doctor --notify --quiet --install-dir "C:\Users\u\Gen Harness" >> "C:\Users\u\.gen-harness\logs\watchdog.log" 2>&1"` {
		t.Fatalf("/TR = %s", args[4])
	}
	if strings.Join(WatchdogSchtasksDeleteArgs(), " ") != "/Delete /TN gen-harness-watchdog /F" {
		t.Fatal(WatchdogSchtasksDeleteArgs())
	}
}

func TestEnableWatchdog_LinuxSystemd_Idempotent(t *testing.T) {
	r := newFakeRunner()
	r.outputs["systemctl|--user|is-enabled|"+WatchdogTaskName+".timer"] = []byte("enabled\n")
	deps := offsiteDeps(t, r, "linux")
	for i := 0; i < 2; i++ {
		msg, mech, err := EnableWatchdog(context.Background(), deps, testWatchdogJob)
		if err != nil || mech != ScheduleSystemd {
			t.Fatalf("lần %d: EnableWatchdog = %q, %q, %v", i+1, msg, mech, err)
		}
		mustContain(t, msg, "Đã bật trực canh máy chủ mỗi 12 phút (systemd --user timer)")
	}
	if !r.calledWith("systemctl", "--user", "enable", "--now", WatchdogTaskName+".timer") {
		t.Fatalf("phải enable --now timer: %+v", r.calls)
	}
	dir := filepath.Join(deps.HomeDir, ".config", "systemd", "user")
	svc, err := os.ReadFile(filepath.Join(dir, WatchdogTaskName+".service"))
	if err != nil {
		t.Fatal(err)
	}
	mustContain(t, string(svc), "doctor --notify --quiet --install-dir /home/u/.gen-harness")
	tim, _ := os.ReadFile(filepath.Join(dir, WatchdogTaskName+".timer"))
	mustContain(t, string(tim), "OnUnitActiveSec=12min")
	st, _ := WatchdogScheduleStatus(context.Background(), deps)
	if !st.Enabled || st.Mechanism != ScheduleSystemd {
		t.Fatalf("status = %+v", st)
	}
	for i := 0; i < 2; i++ {
		if _, err := DisableWatchdog(context.Background(), deps); err != nil {
			t.Fatalf("DisableWatchdog lần %d: %v", i+1, err)
		}
	}
	if _, err := os.Stat(filepath.Join(dir, WatchdogTaskName+".service")); !os.IsNotExist(err) {
		t.Fatalf("unit phải bị xoá: %v", err)
	}
	if !r.calledWith("systemctl", "--user", "disable", "--now", WatchdogTaskName+".timer") {
		t.Fatal("phải disable --now timer")
	}
	if r.calledWith("systemctl", "disable", OffsiteTaskName+".timer") || r.calledWith("systemctl", "disable", TaskName+".timer") {
		t.Fatal("tắt trực canh không được tắt lịch khác")
	}
}

func TestEnableWatchdog_LinuxCronFallback(t *testing.T) {
	r := &crontabCapture{fakeRunner: newFakeRunner()}
	r.errs["systemctl|--user|daemon-reload"] = errors.New("no user bus")
	r.errs["systemctl|--user|is-enabled|"+WatchdogTaskName+".timer"] = errors.New("no user bus")
	r.outputs["crontab|-l"] = []byte(CrontabMarker + "\n7 3 * * * genh update --yes --quiet\n")
	deps := offsiteDeps(t, r, "linux")
	for i := 0; i < 2; i++ {
		_, mech, err := EnableWatchdog(context.Background(), deps, testWatchdogJob)
		if err != nil || mech != ScheduleCron {
			t.Fatalf("EnableWatchdog = %q, %v", mech, err)
		}
	}
	got := r.last()
	if strings.Count(got, WatchdogCrontabMarker) != 1 {
		t.Fatalf("bật hai lần vẫn chỉ một dòng:\n%s", got)
	}
	mustContain(t, got, "*/12 * * * * ")
	mustContain(t, got, "update --yes --quiet")
	st, _ := WatchdogScheduleStatus(context.Background(), deps)
	if !st.Enabled || st.Mechanism != ScheduleCron {
		t.Fatalf("status = %+v", st)
	}
	if _, err := DisableWatchdog(context.Background(), deps); err != nil {
		t.Fatal(err)
	}
	if strings.Contains(r.last(), "doctor --notify") || !strings.Contains(r.last(), "update --yes --quiet") {
		t.Fatalf("Disable phải gỡ đúng dòng trực canh:\n%s", r.last())
	}
	n := len(r.installed)
	if _, err := DisableWatchdog(context.Background(), deps); err != nil {
		t.Fatal(err)
	}
	if len(r.installed) != n {
		t.Fatal("tắt lần hai không cần cài lại crontab")
	}
	if st, _ := WatchdogScheduleStatus(context.Background(), deps); st.Enabled {
		t.Fatalf("sau Disable vẫn báo bật: %+v", st)
	}
}

func TestEnableWatchdog_DarwinVaWindows(t *testing.T) {
	r := newFakeRunner()
	r.outputs["launchctl|list"] = []byte("-\t0\t" + WatchdogLaunchdLabel + "\n")
	deps := offsiteDeps(t, r, "darwin")
	if _, mech, err := EnableWatchdog(context.Background(), deps, testWatchdogJob); err != nil || mech != ScheduleLaunchd {
		t.Fatalf("darwin: %q, %v", mech, err)
	}
	path := filepath.Join(deps.HomeDir, "Library", "LaunchAgents", WatchdogLaunchdLabel+".plist")
	if b, err := os.ReadFile(path); err != nil || !strings.Contains(string(b), "<integer>720</integer>") {
		t.Fatalf("plist: %v", err)
	}
	if _, err := DisableWatchdog(context.Background(), deps); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(path); !os.IsNotExist(err) {
		t.Fatal("plist phải bị xoá")
	}

	w := newFakeRunner()
	wdeps := offsiteDeps(t, w, "windows")
	if _, mech, err := EnableWatchdog(context.Background(), wdeps, testWatchdogJob); err != nil || mech != ScheduleSchtasks {
		t.Fatalf("windows: %q, %v", mech, err)
	}
	if !w.calledWith("schtasks", "/Create", "/TN gen-harness-watchdog", "/SC MINUTE", "/MO 12") {
		t.Fatalf("args schtasks sai: %+v", w.calls)
	}
	w.errs["schtasks|/Delete|/TN|"+WatchdogTaskName+"|/F"] = errors.New("không tìm thấy task")
	if _, err := DisableWatchdog(context.Background(), wdeps); err != nil {
		t.Fatal(err)
	}
	if _, _, err := EnableWatchdog(context.Background(), Deps{Runner: newFakeRunner(), GOOS: "linux", HomeDir: t.TempDir()}, testWatchdogJob); err == nil {
		t.Fatal("thiếu GenhPath phải lỗi")
	}
}

// v0.1.53 (F-95): hỏi lại linger bằng `loginctl show-user` thay vì tin mã thoát của enable-linger.
func TestEnableWatchdog_LingerSauEnableLinger(t *testing.T) {
	for _, tc := range []struct {
		show     string
		enable   error
		wantWarn bool
	}{{"no\n", nil, true}, {"yes\n", nil, false}, {"yes\n", errExit1, false}, {"", errExit1, true}} {
		rr := &recRunner{}
		rr.on("loginctl enable-linger", "", tc.enable)
		rr.on("loginctl show-user", tc.show, nil)
		deps := Deps{Runner: rr, GenhPath: "/g/genh", LogFile: filepath.Join(t.TempDir(), "x.log"), HomeDir: t.TempDir(), GOOS: "linux", UID: "1000",
			LookPath: func(string) (string, error) { return "/usr/bin/systemctl", nil }}
		msg, _, err := EnableWatchdog(context.Background(), deps, WatchdogJob{InstallDir: "/i"})
		if err != nil {
			t.Fatal(err)
		}
		warned := strings.Contains(msg, "sudo loginctl enable-linger $USER")
		if warned != tc.wantWarn {
			t.Errorf("show=%q enableErr=%v: cảnh báo=%v, muốn %v\n%s", tc.show, tc.enable, warned, tc.wantWarn, msg)
		}
		if strings.Contains(msg, "enable-linger $USER.") {
			t.Errorf("không có dấu chấm ngay sau lệnh:\n%s", msg)
		}
		if !rr.ran("loginctl show-user 1000 -p Linger --value") {
			t.Errorf("phải hỏi lại linger: %v", rr.calls)
		}
	}
}
