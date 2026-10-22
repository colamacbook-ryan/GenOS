package autoupdate

import (
	"strings"
	"testing"
)

func TestSystemdServiceUnit_ContainsGenhPathAndLogFile(t *testing.T) {
	unit := SystemdServiceUnit("/home/o/.gen-harness/bin/genh", "/home/o/.gen-harness/logs/auto-update.log", NightlyJob{})
	mustContain(t, unit, "/home/o/.gen-harness/bin/genh update --yes --quiet")
	if strings.Contains(unit, "--install-dir") || strings.Contains(unit, "GEN_HARNESS_HOME") || strings.Contains(unit, "--port") {
		t.Errorf("không có job thì unit y như cũ (không --install-dir/--port/Environment):\n%s", unit)
	}
	mustContain(t, unit, "StandardOutput=append:/home/o/.gen-harness/logs/auto-update.log")
	mustContain(t, unit, "Type=oneshot")
}

func TestSystemdServiceUnit_QuotesPathWithSpaces(t *testing.T) {
	unit := SystemdServiceUnit("/home/o dir/genh", "/tmp/log", NightlyJob{})
	mustContain(t, unit, `"/home/o dir/genh" update --yes --quiet`)
}

func TestSystemdTimerUnit_DailyAt3AMPersistent(t *testing.T) {
	timer := SystemdTimerUnit()
	mustContain(t, timer, "OnCalendar=*-*-* 03:00:00")
	mustContain(t, timer, "Persistent=true")
	mustContain(t, timer, "RandomizedDelaySec=1800")
}

func TestCrontabLine_DailyAt3(t *testing.T) {
	line := CrontabLine("/home/o/.gen-harness/bin/genh", "/tmp/log", 7, NightlyJob{})
	want := "7 3 * * * /home/o/.gen-harness/bin/genh update --yes --quiet >> /tmp/log 2>&1"
	if line != want {
		t.Fatalf("CrontabLine = %q, muốn %q", line, want)
	}
}

func TestCrontabLine_MinuteWraps(t *testing.T) {
	line := CrontabLine("genh", "/tmp/log", 45, NightlyJob{}) // 45 % 30 = 15
	if !contains(line, "15 3 * * *") {
		t.Fatalf("muốn phút lẻ hoá về 0-29, được %q", line)
	}
}

func TestMergeCrontab_AppendsWhenEmpty(t *testing.T) {
	got := MergeCrontab("", "0 3 * * * genh update --yes --quiet", false)
	want := CrontabMarker + "\n0 3 * * * genh update --yes --quiet\n"
	if got != want {
		t.Fatalf("MergeCrontab = %q, muốn %q", got, want)
	}
}

func TestMergeCrontab_KeepsOtherLines_ReplacesOwnLine(t *testing.T) {
	existing := "0 9 * * * some-other-job\n" + CrontabMarker + "\n0 3 * * * genh update --yes --quiet (cu)\n30 22 * * * backup-job\n"
	got := MergeCrontab(existing, "5 3 * * * genh update --yes --quiet (moi)", false)

	mustContain(t, got, "0 9 * * * some-other-job")
	mustContain(t, got, "30 22 * * * backup-job")
	mustContain(t, got, "5 3 * * * genh update --yes --quiet (moi)")
	if contains(got, "(cu)") {
		t.Fatalf("dòng cron CŨ của genh phải bị thay, còn thấy trong %q", got)
	}
	// Chỉ đúng MỘT marker (không nhân đôi).
	if countOccurrences(got, CrontabMarker) != 1 {
		t.Fatalf("muốn đúng 1 marker, được nội dung %q", got)
	}
}

func TestMergeCrontab_RemoveOnly_DropsGenhLineKeepsOthers(t *testing.T) {
	existing := "0 9 * * * some-other-job\n" + CrontabMarker + "\n0 3 * * * genh update --yes --quiet\n"
	got := MergeCrontab(existing, "", true)

	mustContain(t, got, "0 9 * * * some-other-job")
	if contains(got, "genh update") {
		t.Fatalf("removeOnly phải bỏ dòng genh, còn thấy trong %q", got)
	}
	if contains(got, CrontabMarker) {
		t.Fatalf("removeOnly phải bỏ marker, còn thấy trong %q", got)
	}
}

func TestMergeCrontab_RemoveOnly_EmptyResultWhenNothingElse(t *testing.T) {
	existing := CrontabMarker + "\n0 3 * * * genh update --yes --quiet\n"
	got := MergeCrontab(existing, "", true)
	if got != "" {
		t.Fatalf("muốn rỗng khi không còn dòng nào khác, được %q", got)
	}
}

func TestLaunchdPlist_ContainsScheduleAndArgs(t *testing.T) {
	plist := LaunchdPlist("/Users/o/.gen-harness/bin/genh", "/Users/o/.gen-harness/logs/auto-update.log", 12, NightlyJob{})
	mustContain(t, plist, "<string>/Users/o/.gen-harness/bin/genh</string>")
	mustContain(t, plist, "<string>update</string>")
	mustContain(t, plist, "<string>--yes</string>")
	mustContain(t, plist, "<string>--quiet</string>")
	mustContain(t, plist, "<integer>3</integer>")
	mustContain(t, plist, "<integer>12</integer>")
	mustContain(t, plist, "com.gen-harness.update")
}

func TestSchtasksCreateArgs_DailyAt3(t *testing.T) {
	args := SchtasksCreateArgs(`C:\Users\o\genh.exe`, `C:\Users\o\log.txt`, NightlyJob{})
	joined := ""
	for _, a := range args {
		joined += a + "|"
	}
	mustContain(t, joined, "/Create")
	mustContain(t, joined, "/SC|DAILY")
	mustContain(t, joined, "/ST|03:00")
	mustContain(t, joined, TaskName)
	mustContain(t, joined, "update --yes --quiet")
}

func TestSchtasksDeleteAndQueryArgs_UseTaskName(t *testing.T) {
	del := SchtasksDeleteArgs()
	if del[0] != "/Delete" || del[2] != TaskName {
		t.Fatalf("SchtasksDeleteArgs = %v", del)
	}
	q := SchtasksQueryArgs()
	if q[0] != "/Query" || q[2] != TaskName || q[len(q)-1] != "/V" {
		t.Fatalf("SchtasksQueryArgs = %v", q)
	}
}

func TestParseMinuteFromClock(t *testing.T) {
	if m := parseMinuteFromClock("03:17:42"); m != 17 {
		t.Fatalf("parseMinuteFromClock = %d, muốn 17", m)
	}
	if m := parseMinuteFromClock("khong-hop-le"); m != 0 {
		t.Fatalf("parseMinuteFromClock chuỗi lỗi phải trả 0, được %d", m)
	}
}

// --- trợ giúp ---

func mustContain(t *testing.T, haystack, needle string) {
	t.Helper()
	if !contains(haystack, needle) {
		t.Fatalf("muốn thấy %q trong:\n%s", needle, haystack)
	}
}

func contains(haystack, needle string) bool {
	return len(needle) == 0 || indexOf(haystack, needle) >= 0
}

func indexOf(haystack, needle string) int {
	for i := 0; i+len(needle) <= len(haystack); i++ {
		if haystack[i:i+len(needle)] == needle {
			return i
		}
	}
	return -1
}

func countOccurrences(haystack, needle string) int {
	count := 0
	for i := 0; i+len(needle) <= len(haystack); i++ {
		if haystack[i:i+len(needle)] == needle {
			count++
			i += len(needle) - 1
		}
	}
	return count
}

// v0.1.37 (F-34): tắt máy giữa chừng — systemd chỉ SIGTERM tiến trình chính và
// chờ 15 phút cho phần quay về bản cũ.
func TestSystemdServiceUnits_KillModeMixed_TimeoutStop900(t *testing.T) {
	units := map[string]string{
		"lịch đêm":        SystemdServiceUnit("/g/genh", "/g/log", NightlyJob{InstallDir: "/r"}),
		"watcher yêu cầu": SystemdRequestServiceUnit("/g/genh", "/g/log", RequestPaths{InstallDir: "/r", RequestFile: "/r/run/request/update.json"}),
	}
	for name, u := range units {
		svc := u[strings.Index(u, "[Service]"):]
		if !strings.Contains(svc, "KillMode=mixed\n") || !strings.Contains(svc, "TimeoutStopSec=900\n") {
			t.Errorf("%s: [Service] phải có KillMode=mixed và TimeoutStopSec=900:\n%s", name, u)
		}
	}
}

// v0.1.53 (F-98): unit lịch đêm mang --install-dir/--port/Environment của bản cài.
func TestSystemdServiceUnit_MangBanCaiVaCong(t *testing.T) {
	job := NightlyJob{InstallDir: "/home/o/.gen-harness", Port: 9443, Env: []string{"GENH_COMPOSE_FILE=/src/deploy/compose.yaml"}}
	unit := SystemdServiceUnit("/home/o/.gen-harness/bin/genh", "/g/log", job)
	mustContain(t, unit, "Environment=GEN_HARNESS_HOME=/home/o/.gen-harness\n")
	mustContain(t, unit, "Environment=GENH_COMPOSE_FILE=/src/deploy/compose.yaml\n")
	// Giữ nguyên tiền tố `update --yes --quiet` (cổng 24 giờ dựa vào --yes; E2E grep).
	mustContain(t, unit, "ExecStart=/home/o/.gen-harness/bin/genh update --yes --quiet --install-dir /home/o/.gen-harness --port 9443\n")

	// Cổng mặc định (8443) thì KHÔNG truyền --port.
	def := SystemdServiceUnit("/g/genh", "/g/log", NightlyJob{InstallDir: "/r", Port: 8443})
	if strings.Contains(def, "--port") {
		t.Errorf("cổng mặc định không được truyền --port:\n%s", def)
	}
	mustContain(t, def, "update --yes --quiet --install-dir /r\n")

	// Đường dẫn có khoảng trắng: quote cả Environment lẫn --install-dir.
	sp := SystemdServiceUnit("/g/genh", "/g/log", NightlyJob{InstallDir: "/home/o dir/gh"})
	mustContain(t, sp, `Environment="GEN_HARNESS_HOME=/home/o dir/gh"`)
	mustContain(t, sp, `--install-dir "/home/o dir/gh"`)
	if dir, ok := installDirOfText(sp); !ok || dir != "/home/o dir/gh" {
		t.Errorf("đọc ngược bản cài chủ = %q, %v", dir, ok)
	}
}

func TestNightlyJob_CrontabPlistSchtasksMangCungDoiSo(t *testing.T) {
	job := NightlyJob{InstallDir: "/home/o/.gen-harness", Port: 9443, Env: []string{"GENH_COMPOSE_FILE=/src/deploy/compose.yaml"}}
	line := CrontabLine("/g/genh", "/g/log", 7, job)
	mustContain(t, line, "7 3 * * * GEN_HARNESS_HOME=/home/o/.gen-harness GENH_COMPOSE_FILE=/src/deploy/compose.yaml /g/genh update --yes --quiet --install-dir /home/o/.gen-harness --port 9443 >> /g/log 2>&1")

	plist := LaunchdPlist("/g/genh", "/g/log", 12, job)
	mustContain(t, plist, "<string>--install-dir</string>\n\t\t<string>/home/o/.gen-harness</string>")
	mustContain(t, plist, "<string>--port</string>\n\t\t<string>9443</string>")
	mustContain(t, plist, "<key>EnvironmentVariables</key>")
	mustContain(t, plist, "<key>GEN_HARNESS_HOME</key>\n\t\t<string>/home/o/.gen-harness</string>")
	mustContain(t, plist, "<key>GENH_COMPOSE_FILE</key>")
	if strings.Contains(LaunchdPlist("/g/genh", "/g/log", 12, NightlyJob{}), "EnvironmentVariables") {
		t.Error("không có job thì plist không có EnvironmentVariables")
	}

	tr := strings.Join(SchtasksCreateArgs(`C:\g\genh.exe`, `C:\g\log.txt`, NightlyJob{InstallDir: `C:\Users\o\GenHarness`, Port: 9443}), "|")
	mustContain(t, tr, `update --yes --quiet --install-dir C:\Users\o\GenHarness --port 9443`)
	// Cổng mặc định không --port; đường dẫn có khoảng trắng được quote.
	tr = strings.Join(SchtasksCreateArgs(`C:\g\genh.exe`, `C:\g\log.txt`, NightlyJob{InstallDir: `C:\Program Files\GH`, Port: 8443}), "|")
	mustContain(t, tr, `--install-dir "C:\Program Files\GH"`)
	if strings.Contains(tr, "--port") {
		t.Errorf("cổng mặc định không --port: %s", tr)
	}
}

// v0.1.53: chặn kích lặp khi tệp yêu cầu không xoá được.
func TestRequestUnits_GioiHanKichLap(t *testing.T) {
	svc := SystemdRequestServiceUnit("/g/genh", "/g/log", RequestPaths{InstallDir: "/r", RequestFile: "/r/run/request/update.json"})
	unit := svc[:strings.Index(svc, "[Service]")]
	mustContain(t, unit, "StartLimitIntervalSec=300\n")
	mustContain(t, unit, "StartLimitBurst=5\n")
	path := SystemdRequestPathUnit("/r/run/request/update.json")
	section := path[strings.Index(path, "[Path]"):]
	mustContain(t, section, "TriggerLimitIntervalSec=60\n")
	mustContain(t, section, "TriggerLimitBurst=10\n")
	mustContain(t, section, "Unit="+RequestTaskName+".service")
}
