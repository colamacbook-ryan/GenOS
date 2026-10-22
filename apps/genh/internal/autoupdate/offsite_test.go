package autoupdate

import (
	"context"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

var testJob = OffsiteJob{InstallDir: "/home/u/.gen-harness", Port: 9443, Env: []string{"GENH_COMPOSE_FILE=/src/deploy/compose.yaml"}}

// crontabCapture bọc fakeRunner, ghi lại NỘI DUNG tệp `crontab <tệp>` được cài
// (installCrontab xoá tệp tạm ngay sau đó) và trả lại nó cho `crontab -l` sau.
type crontabCapture struct {
	*fakeRunner
	installed []string
}

func (c *crontabCapture) Output(ctx context.Context, name string, args []string) ([]byte, error) {
	if name == "crontab" && len(args) == 1 && args[0] != "-l" {
		b, _ := os.ReadFile(args[0])
		c.installed = append(c.installed, string(b))
		c.outputs["crontab|-l"] = b
	}
	return c.fakeRunner.Output(ctx, name, args)
}

func (c *crontabCapture) last() string {
	if len(c.installed) == 0 {
		return ""
	}
	return c.installed[len(c.installed)-1]
}

func TestSystemdOffsiteUnits(t *testing.T) {
	svc := SystemdOffsiteServiceUnit("/home/u/My Apps/genh", "/home/u/.gen-harness/logs/offsite.log",
		OffsiteJob{InstallDir: "/home/u/Gen Harness", Env: []string{"GENH_COMPOSE_FILE=/src/deploy/compose.yaml"}})
	for _, want := range []string{
		"Type=oneshot", "KillMode=mixed", "TimeoutStopSec=900",
		`ExecStart="/home/u/My Apps/genh" offsite run --quiet --install-dir "/home/u/Gen Harness"`,
		`Environment="GEN_HARNESS_HOME=/home/u/Gen Harness"`,
		"Environment=GENH_COMPOSE_FILE=/src/deploy/compose.yaml",
		"StandardOutput=append:/home/u/.gen-harness/logs/offsite.log",
		"StandardError=append:/home/u/.gen-harness/logs/offsite.log",
	} {
		mustContain(t, svc, want)
	}
	if strings.Contains(svc, "--port") {
		t.Fatalf("cổng mặc định (0) không truyền --port:\n%s", svc)
	}
	mustContain(t, SystemdOffsiteServiceUnit("/g/genh", "/g/log", testJob), "--install-dir /home/u/.gen-harness --port 9443")
	tim := SystemdOffsiteTimerUnit()
	for _, want := range []string{"OnCalendar=Sun *-*-* 05:30:00", "Persistent=true", "RandomizedDelaySec=1800", "WantedBy=timers.target"} {
		mustContain(t, tim, want)
	}
}

func TestOffsiteCrontabLine_MarkerVaMerge(t *testing.T) {
	line := OffsiteCrontabLine("/g/genh", "/g/logs/offsite.log", testJob, 7)
	if !strings.HasPrefix(line, "37 5 * * 0 ") {
		t.Fatalf("phải chạy Chủ nhật 05:37 (phút lẻ hoá 30–59): %q", line)
	}
	mustContain(t, line, "GEN_HARNESS_HOME=/home/u/.gen-harness GENH_COMPOSE_FILE=/src/deploy/compose.yaml /g/genh offsite run --quiet --install-dir /home/u/.gen-harness --port 9443 >> /g/logs/offsite.log 2>&1")
	for _, m := range []int{0, 29, 30, 59, 61, -1} {
		f := strings.Fields(OffsiteCrontabLine("/g/genh", "/g/log", testJob, m))[0]
		if f < "30" || f > "59" || len(f) != 2 {
			t.Errorf("phút %d → %q, phải trong 30–59", m, f)
		}
	}
	// Đường dẫn có khoảng trắng được quote cho sh.
	mustContain(t, OffsiteCrontabLine("/home/u/My Apps/genh", "/l o/g", OffsiteJob{InstallDir: "/a b"}, 0),
		"GEN_HARNESS_HOME='/a b' '/home/u/My Apps/genh' offsite run --quiet --install-dir '/a b' >> '/l o/g' 2>&1")

	existing := "0 1 * * * backup.sh\n" + CrontabMarker + "\n7 3 * * * genh update --yes --quiet\n" +
		CrontabRequestMarker + "\n* * * * * [ -f /r ] && genh handle-requests\n"
	merged := MergeOffsiteCrontab(existing, line, false)
	for _, want := range []string{"backup.sh", CrontabMarker, "update --yes --quiet", CrontabRequestMarker, "handle-requests", OffsiteCrontabMarker + "\n" + line} {
		mustContain(t, merged, want)
	}
	// Gọi lại: không nhân đôi.
	if again := MergeOffsiteCrontab(merged, line, false); strings.Count(again, OffsiteCrontabMarker) != 1 {
		t.Fatalf("bật lại phải thay dòng cũ, không nhân đôi:\n%s", again)
	}
	removed := MergeOffsiteCrontab(merged, "", true)
	if strings.Contains(removed, "offsite") || !strings.Contains(removed, "update --yes --quiet") || !strings.Contains(removed, "handle-requests") {
		t.Fatalf("gỡ lịch tuần phải giữ dòng tự cập nhật đêm + watcher:\n%s", removed)
	}
	// Gỡ dòng tự cập nhật đêm không đụng lịch tuần.
	if r := MergeCrontab(merged, "", true); !strings.Contains(r, OffsiteCrontabMarker) {
		t.Fatalf("auto-update disable không được gỡ lịch tuần:\n%s", r)
	}
}

func TestOffsiteLaunchdPlist(t *testing.T) {
	p := OffsiteLaunchdPlist("/Applications/Gen Harness/genh", "/u/logs/offsite.log", testJob, 12)
	for _, want := range []string{
		"<string>" + OffsiteLaunchdLabel + "</string>",
		"<key>Weekday</key>\n\t\t<integer>0</integer>",
		"<key>Hour</key>\n\t\t<integer>5</integer>",
		"<key>Minute</key>\n\t\t<integer>42</integer>",
		"\t\t<string>/Applications/Gen Harness/genh</string>\n\t\t<string>offsite</string>\n\t\t<string>run</string>\n\t\t<string>--quiet</string>\n\t\t<string>--install-dir</string>\n\t\t<string>/home/u/.gen-harness</string>\n\t\t<string>--port</string>\n\t\t<string>9443</string>\n",
		"<key>GENH_COMPOSE_FILE</key>",
		"<string>/u/logs/offsite.log</string>",
	} {
		mustContain(t, p, want)
	}
}

func TestOffsiteSchtasksArgs(t *testing.T) {
	args := OffsiteSchtasksCreateArgs(`C:\Users\u\genh.exe`, `C:\Users\u\.gen-harness\logs\offsite.log`, OffsiteJob{InstallDir: `C:\Users\u\Gen Harness`})
	joined := strings.Join(args, " ")
	for _, want := range []string{"/Create /TN gen-harness-offsite", "/SC WEEKLY", "/D SUN", "/ST 05:30", "/RL LIMITED", "/F"} {
		mustContain(t, joined, want)
	}
	if args[4] != `cmd.exe /c ""C:\Users\u\genh.exe" offsite run --quiet --install-dir "C:\Users\u\Gen Harness" >> "C:\Users\u\.gen-harness\logs\offsite.log" 2>&1"` {
		t.Fatalf("/TR = %s", args[4])
	}
	if strings.Join(OffsiteSchtasksDeleteArgs(), " ") != "/Delete /TN gen-harness-offsite /F" {
		t.Fatalf("delete args = %v", OffsiteSchtasksDeleteArgs())
	}
}

func offsiteDeps(t *testing.T, r Runner, goos string) Deps {
	t.Helper()
	return Deps{Runner: r, GenhPath: "/g/genh", LogFile: filepath.Join(t.TempDir(), "logs", "offsite.log"), HomeDir: t.TempDir(), GOOS: goos,
		LookPath: func(string) (string, error) { return "/usr/bin/systemctl", nil }}
}

func TestEnableOffsite_LinuxSystemd(t *testing.T) {
	r := newFakeRunner()
	r.outputs["systemctl|--user|is-enabled|"+OffsiteTaskName+".timer"] = []byte("enabled\n")
	deps := offsiteDeps(t, r, "linux")
	msg, mech, err := EnableOffsite(context.Background(), deps, testJob)
	if err != nil || mech != ScheduleSystemd {
		t.Fatalf("EnableOffsite = %q, %q, %v", msg, mech, err)
	}
	mustContain(t, msg, "Đã bật lịch sao lưu ra ổ ngoài mỗi Chủ nhật ~05:30 (systemd --user timer)")
	if !r.calledWith("systemctl", "--user", "enable", "--now", OffsiteTaskName+".timer") {
		t.Fatalf("phải enable --now timer: %+v", r.calls)
	}
	dir := filepath.Join(deps.HomeDir, ".config", "systemd", "user")
	svc, err := os.ReadFile(filepath.Join(dir, OffsiteTaskName+".service"))
	if err != nil {
		t.Fatal(err)
	}
	mustContain(t, string(svc), "offsite run --quiet --install-dir /home/u/.gen-harness")
	if _, err := os.Stat(filepath.Join(dir, OffsiteTaskName+".timer")); err != nil {
		t.Fatal(err)
	}
	st, _ := OffsiteStatus(context.Background(), deps)
	if !st.Enabled || st.Mechanism != ScheduleSystemd {
		t.Fatalf("OffsiteStatus = %+v", st)
	}
	// Linger không bật được → cảnh báo (không lỗi).
	r2 := newFakeRunner()
	r2.errs["loginctl|enable-linger"] = errors.New("polkit")
	msg, _, err = EnableOffsite(context.Background(), offsiteDeps(t, r2, "linux"), testJob)
	if err != nil || !strings.Contains(msg, "linger") {
		t.Fatalf("thiếu cảnh báo linger: %q, %v", msg, err)
	}
	// Tắt: gỡ unit, idempotent.
	for i := 0; i < 2; i++ {
		if _, err := DisableOffsite(context.Background(), deps); err != nil {
			t.Fatalf("DisableOffsite lần %d: %v", i+1, err)
		}
	}
	if _, err := os.Stat(filepath.Join(dir, OffsiteTaskName+".service")); !os.IsNotExist(err) {
		t.Fatalf("unit phải bị xoá: %v", err)
	}
	if !r.calledWith("systemctl", "--user", "disable", "--now", OffsiteTaskName+".timer") {
		t.Fatal("phải disable --now timer")
	}
	// Không đụng lịch tự cập nhật đêm.
	if r.calledWith("systemctl", "disable", TaskName+".timer") {
		t.Fatal("tắt lịch tuần không được tắt lịch đêm")
	}
}

func TestEnableOffsite_LinuxCronFallback(t *testing.T) {
	r := &crontabCapture{fakeRunner: newFakeRunner()}
	r.errs["systemctl|--user|daemon-reload"] = errors.New("no user bus")
	r.errs["systemctl|--user|is-enabled|"+OffsiteTaskName+".timer"] = errors.New("no user bus")
	r.outputs["crontab|-l"] = []byte(CrontabMarker + "\n7 3 * * * genh update --yes --quiet\n")
	deps := offsiteDeps(t, r, "linux")
	msg, mech, err := EnableOffsite(context.Background(), deps, testJob)
	if err != nil || mech != ScheduleCron {
		t.Fatalf("EnableOffsite = %q, %q, %v", msg, mech, err)
	}
	mustContain(t, msg, "crontab")
	got := r.last()
	mustContain(t, got, OffsiteCrontabMarker+"\n")
	mustContain(t, got, " 5 * * 0 ")
	mustContain(t, got, "update --yes --quiet") // dòng đêm còn nguyên
	st, _ := OffsiteStatus(context.Background(), deps)
	if !st.Enabled || st.Mechanism != ScheduleCron {
		t.Fatalf("OffsiteStatus = %+v", st)
	}
	if _, err := DisableOffsite(context.Background(), deps); err != nil {
		t.Fatal(err)
	}
	if strings.Contains(r.last(), "offsite") || !strings.Contains(r.last(), "update --yes --quiet") {
		t.Fatalf("Disable phải gỡ đúng dòng lịch tuần:\n%s", r.last())
	}
	n := len(r.installed)
	if _, err := DisableOffsite(context.Background(), deps); err != nil {
		t.Fatal(err)
	}
	if len(r.installed) != n {
		t.Fatal("tắt lần hai (không còn dòng) không cần cài lại crontab")
	}
	st, _ = OffsiteStatus(context.Background(), deps)
	if st.Enabled || st.Mechanism != "" {
		t.Fatalf("sau Disable OffsiteStatus = %+v", st)
	}
}

func TestEnableOffsite_Darwin(t *testing.T) {
	r := newFakeRunner()
	r.outputs["launchctl|list"] = []byte("-\t0\t" + OffsiteLaunchdLabel + "\n")
	deps := offsiteDeps(t, r, "darwin")
	msg, mech, err := EnableOffsite(context.Background(), deps, testJob)
	if err != nil || mech != ScheduleLaunchd {
		t.Fatalf("EnableOffsite = %q, %q, %v", msg, mech, err)
	}
	path := filepath.Join(deps.HomeDir, "Library", "LaunchAgents", OffsiteLaunchdLabel+".plist")
	b, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	mustContain(t, string(b), "<key>Weekday</key>")
	if !r.calledWith("launchctl", "load", "-w", path) {
		t.Fatalf("phải launchctl load -w: %+v", r.calls)
	}
	st, _ := OffsiteStatus(context.Background(), deps)
	if !st.Enabled || st.Mechanism != ScheduleLaunchd {
		t.Fatalf("OffsiteStatus = %+v", st)
	}
	for i := 0; i < 2; i++ {
		if _, err := DisableOffsite(context.Background(), deps); err != nil {
			t.Fatal(err)
		}
	}
	if _, err := os.Stat(path); !os.IsNotExist(err) {
		t.Fatal("plist phải bị xoá")
	}
	if st, _ := OffsiteStatus(context.Background(), deps); st.Enabled {
		t.Fatalf("sau Disable vẫn báo bật: %+v", st)
	}
}

func TestEnableOffsite_Windows(t *testing.T) {
	r := newFakeRunner()
	r.outputs["schtasks|/Query|/TN|"+OffsiteTaskName+"|/FO|LIST"] = []byte("TaskName: \\gen-harness-offsite\nStatus: Ready\n")
	deps := offsiteDeps(t, r, "windows")
	msg, mech, err := EnableOffsite(context.Background(), deps, testJob)
	if err != nil || mech != ScheduleSchtasks {
		t.Fatalf("EnableOffsite = %q, %q, %v", msg, mech, err)
	}
	if !r.calledWith("schtasks", "/Create", "/TN gen-harness-offsite", "/SC WEEKLY", "/D SUN", "/RL LIMITED", "/F") {
		t.Fatalf("args schtasks sai: %+v", r.calls)
	}
	st, _ := OffsiteStatus(context.Background(), deps)
	if !st.Enabled || st.Mechanism != ScheduleSchtasks {
		t.Fatalf("OffsiteStatus = %+v", st)
	}
	// Disable idempotent: task không tồn tại (schtasks lỗi) vẫn không lỗi.
	r.errs["schtasks|/Delete|/TN|"+OffsiteTaskName+"|/F"] = errors.New("không tìm thấy task")
	for i := 0; i < 2; i++ {
		if _, err := DisableOffsite(context.Background(), deps); err != nil {
			t.Fatal(err)
		}
	}
	r.errs["schtasks|/Query|/TN|"+OffsiteTaskName+"|/FO|LIST"] = errors.New("không tìm thấy task")
	if st, _ := OffsiteStatus(context.Background(), deps); st.Enabled {
		t.Fatalf("task không còn mà vẫn báo bật: %+v", st)
	}
}

func TestEnableOffsite_ThieuGenhPath(t *testing.T) {
	if _, _, err := EnableOffsite(context.Background(), Deps{Runner: newFakeRunner(), GOOS: "linux", HomeDir: t.TempDir()}, testJob); err == nil {
		t.Fatal("thiếu GenhPath phải lỗi")
	}
}

// v0.1.53 (F-95): hỏi lại linger bằng `loginctl show-user` thay vì tin mã thoát của enable-linger.
func TestEnableOffsite_LingerSauEnableLinger(t *testing.T) {
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
		msg, _, err := EnableOffsite(context.Background(), deps, OffsiteJob{InstallDir: "/i"})
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
