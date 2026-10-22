package ops

import (
	"context"
	"encoding/json"
	"errors"
	"os"
	"strings"
	"testing"

	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/dockercli"
	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/dockercli/fake"
	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/hostlink"
)

// cmdIs khớp lệnh có đúng tên + đúng chuỗi args (nối bằng dấu cách).
func cmdIs(name, args string) func(dockercli.Cmd) bool {
	return func(c dockercli.Cmd) bool { return c.Name == name && strings.Join(c.Args, " ") == args }
}

var errExit1 = errors.New("exit status 1")

const (
	argsDockerInfo   = "info --format {{json .SecurityOptions}}"
	argsSysDocker    = "is-enabled docker.service"
	argsUserDocker   = "--user is-enabled docker.service"
	argsLinger       = "show-user 1000 --property=Linger --value"
	argsUserReqPath  = "--user is-enabled gen-harness-update-request.path"
	argsUserTimer    = "--user is-enabled gen-harness-update.timer"
	argsUserWatchdog = "--user is-enabled gen-harness-watchdog.timer"
	argsUserOffsite  = "--user is-enabled gen-harness-offsite.timer"
	securityOptsSys  = `["name=seccomp,profile=builtin","name=cgroupns"]`
	securityOptsRoot = `["name=seccomp,profile=builtin","name=rootless","name=cgroupns"]`
)

func TestCheckAutostart_SystemDockerEnabled_LingerYes(t *testing.T) {
	fr := &fake.Runner{Responses: []fake.Response{
		{Match: cmdIs("docker", argsDockerInfo), Output: []byte(securityOptsSys)},
		{Match: cmdIs("systemctl", argsSysDocker), Output: []byte("enabled\n")},
		{Match: cmdIs("loginctl", argsLinger), Output: []byte("yes\n")},
		{Match: cmdIs("systemctl", argsUserReqPath), Output: []byte("enabled\n")},
		{Match: cmdIs("systemctl", argsUserTimer), Output: []byte("enabled\n")},
	}}
	st := CheckAutostart(context.Background(), AutostartDeps{Runner: fr, GOOS: "linux", UID: "1000"})
	if st.OS != "linux" || st.DockerMode != "system" || st.DockerEnabled != "yes" || st.Linger != "yes" || !st.LingerRequired {
		t.Fatalf("sai: %+v", st)
	}
	if st.CheckedAt == "" {
		t.Error("thiếu checked_at")
	}
}

func TestCheckAutostart_DisabledVaEnabledRuntime_LaNo(t *testing.T) {
	for _, state := range []string{"disabled", "enabled-runtime", "masked", "linked-runtime"} {
		fr := &fake.Runner{Responses: []fake.Response{
			{Match: cmdIs("docker", argsDockerInfo), Output: []byte(securityOptsSys)},
			// systemctl is-enabled trả mã ≠0 khi disabled nhưng stdout vẫn có trạng thái.
			{Match: cmdIs("systemctl", argsSysDocker), Output: []byte(state + "\n"), Err: errExit1},
			{Match: cmdIs("loginctl", argsLinger), Output: []byte("no\n")},
			{Match: cmdIs("systemctl", argsUserReqPath), Output: []byte("disabled\n"), Err: errExit1},
			{Match: cmdIs("systemctl", argsUserTimer), Output: []byte("disabled\n"), Err: errExit1},
		}}
		st := CheckAutostart(context.Background(), AutostartDeps{Runner: fr, GOOS: "linux", UID: "1000"})
		if st.DockerEnabled != "no" {
			t.Errorf("%s: docker_enabled phải là \"no\", được %+v", state, st)
		}
		if st.Linger != "no" || st.LingerRequired {
			t.Errorf("%s: linger=no, không cần linger: %+v", state, st)
		}
	}
}

func TestCheckAutostart_Rootless_DungSystemctlUser_CanLinger(t *testing.T) {
	fr := &fake.Runner{Responses: []fake.Response{
		{Match: cmdIs("docker", argsDockerInfo), Output: []byte(securityOptsRoot)},
		{Match: cmdIs("systemctl", argsUserDocker), Output: []byte("enabled\n")},
		{Match: cmdIs("loginctl", argsLinger), Output: []byte("no\n")},
	}}
	st := CheckAutostart(context.Background(), AutostartDeps{Runner: fr, GOOS: "linux", UID: "1000"})
	if st.DockerMode != "rootless" || st.DockerEnabled != "yes" || !st.LingerRequired || st.Linger != "no" {
		t.Fatalf("rootless sai: %+v", st)
	}
	for _, c := range fr.Calls {
		if c.Cmd.Name == "systemctl" && strings.Join(c.Cmd.Args, " ") == argsSysDocker {
			t.Error("rootless phải hỏi `systemctl --user`, không hỏi systemd hệ thống")
		}
	}
	lines := autostartLines(st)
	if lines[1].OK || !strings.Contains(lines[1].Info, "sudo loginctl enable-linger $USER") {
		t.Errorf("linger cần mà tắt: phải cảnh báo kèm lệnh sửa: %+v", lines[1])
	}
	st.DockerEnabled = "no"
	if l := autostartLines(st)[0]; l.OK || !strings.Contains(l.Info, "systemctl --user enable docker") || strings.Contains(l.Info, "sudo systemctl") {
		t.Errorf("rootless tắt: lệnh sửa phải là systemctl --user enable docker: %+v", l)
	}
}

func TestCheckAutostart_LoginctlLoi_Unknown(t *testing.T) {
	fr := &fake.Runner{Responses: []fake.Response{
		{Match: cmdIs("docker", argsDockerInfo), Output: []byte(securityOptsSys)},
		{Match: cmdIs("systemctl", argsSysDocker), Output: []byte("enabled\n")},
		{Match: cmdIs("loginctl", argsLinger), Err: errors.New("Failed to connect to bus")},
	}}
	st := CheckAutostart(context.Background(), AutostartDeps{Runner: fr, GOOS: "linux", UID: "1000"})
	if st.Linger != "unknown" {
		t.Fatalf("loginctl lỗi ⇒ linger unknown: %+v", st)
	}
	// Lệnh systemctl --user không trả lời (không có trong fake ⇒ lỗi) ⇒ không bịa "cần linger".
	if st.LingerRequired {
		t.Errorf("không biết lịch --user ⇒ không coi là cần linger: %+v", st)
	}
}

func TestCheckAutostart_DockerInfoLoi_Unknown(t *testing.T) {
	st := CheckAutostart(context.Background(), AutostartDeps{Runner: &fake.Runner{}, GOOS: "linux", UID: "1000"})
	if st.DockerMode != "unknown" || st.DockerEnabled != "unknown" || st.Linger != "unknown" {
		t.Fatalf("mọi lệnh lỗi ⇒ unknown: %+v", st)
	}
}

const argsDockerOS = "info --format {{.OperatingSystem}}"

// Ngoài Linux: chỉ Docker Desktop mới "tự lo" (not_applicable); không hỏi systemd.
func TestCheckAutostart_Darwin_DockerDesktop_KhongApDung(t *testing.T) {
	for _, goos := range []string{"darwin", "windows"} {
		fr := &fake.Runner{Responses: []fake.Response{{Match: cmdIs("docker", argsDockerOS), Output: []byte("Docker Desktop\n")}}}
		st := CheckAutostart(context.Background(), AutostartDeps{Runner: fr, GOOS: goos})
		if st.OS != goos || st.Linger != "not_applicable" || st.DockerEnabled != "not_applicable" || st.DockerMode != "desktop" || st.LingerRequired {
			t.Errorf("%s: sai %+v", goos, st)
		}
		for _, c := range fr.Calls {
			if c.Cmd.Name != "docker" {
				t.Errorf("%s: không được gọi %s", goos, c.Cmd.Name)
			}
		}
		if l := autostartLines(st)[0]; !l.OK || !strings.Contains(l.Info, "Docker Desktop") {
			t.Errorf("%s: dòng Docker: %+v", goos, l)
		}
	}
}

// Colima (macOS) / WSL do genh cài, hoặc docker info lỗi: KHÔNG được báo
// not_applicable (Console sẽ hiện sai "Có") — "unknown".
func TestCheckAutostart_Darwin_Colima_Unknown(t *testing.T) {
	for name, resp := range map[string]fake.Response{
		"colima": {Match: cmdIs("docker", argsDockerOS), Output: []byte("Ubuntu 24.04 LTS\n")},
		"loi":    {Match: cmdIs("docker", argsDockerOS), Err: errors.New("Cannot connect to the Docker daemon")},
	} {
		fr := &fake.Runner{Responses: []fake.Response{resp}}
		st := CheckAutostart(context.Background(), AutostartDeps{Runner: fr, GOOS: "darwin"})
		if st.DockerEnabled != "unknown" || st.DockerMode != "unknown" || st.Linger != "not_applicable" {
			t.Errorf("%s: muốn docker unknown, linger not_applicable: %+v", name, st)
		}
		if l := autostartLines(st)[0]; l.OK || !strings.Contains(l.Info, "không rõ") {
			t.Errorf("%s: dòng Docker phải \"không rõ\": %+v", name, l)
		}
	}
}

// docker info lỗi (mode unknown): docker.service hệ thống "disabled" KHÔNG kết
// luận "no" (máy rootless thường tắt nó) — chỉ tin "yes".
func TestCheckAutostart_DockerInfoLoi_SystemDisabled_KhongBaoNo(t *testing.T) {
	fr := &fake.Runner{Responses: []fake.Response{
		{Match: cmdIs("docker", argsDockerInfo), Err: errors.New("timeout")},
		{Match: cmdIs("systemctl", argsSysDocker), Output: []byte("disabled\n"), Err: errExit1},
	}}
	st := CheckAutostart(context.Background(), AutostartDeps{Runner: fr, GOOS: "linux", UID: "1000"})
	if st.DockerMode != "unknown" || st.DockerEnabled != "unknown" {
		t.Fatalf("muốn docker_enabled unknown: %+v", st)
	}
	if l := autostartLines(st)[0]; strings.Contains(l.Info, "sudo systemctl enable docker") {
		t.Errorf("không được khuyên bật Docker rootful: %+v", l)
	}
	fr = &fake.Runner{Responses: []fake.Response{
		{Match: cmdIs("docker", argsDockerInfo), Err: errors.New("timeout")},
		{Match: cmdIs("systemctl", argsSysDocker), Output: []byte("enabled\n")},
	}}
	if st := CheckAutostart(context.Background(), AutostartDeps{Runner: fr, GOOS: "linux", UID: "1000"}); st.DockerEnabled != "yes" {
		t.Errorf("mode unknown + docker.service enabled ⇒ yes: %+v", st)
	}
}

func TestNormalizeIsEnabled(t *testing.T) {
	cases := map[string]string{
		"enabled": "yes", "static": "yes", "alias": "yes", "indirect": "yes", "generated": "yes",
		"disabled": "no", "masked": "no", "enabled-runtime": "no", "linked": "no", "linked-runtime": "no",
		"": "unknown", "bad-setting": "unknown",
	}
	for in, want := range cases {
		if got := normalizeIsEnabled([]byte(in+"\n"), nil); got != want {
			t.Errorf("%q ⇒ %q, muốn %q", in, got, want)
		}
	}
}

// autostartFakeResponses: Docker system bật, linger tắt nhưng có lịch --user ⇒ cảnh báo.
func autostartFakeResponses() []fake.Response {
	return []fake.Response{
		{Match: cmdIs("docker", argsDockerInfo), Output: []byte(securityOptsSys)},
		{Match: cmdIs("systemctl", argsSysDocker), Output: []byte("disabled\n"), Err: errExit1},
		{Match: cmdIs("loginctl", argsLinger), Output: []byte("no\n")},
		{Match: cmdIs("systemctl", argsUserTimer), Output: []byte("enabled\n")},
	}
}

// checkAutostartFile: run/autostart-status.json có đúng khoá JSON của hợp đồng.
func checkAutostartFile(t *testing.T, installDir string) map[string]any {
	t.Helper()
	b, err := os.ReadFile(hostlink.AutostartStatusPath(installDir))
	if err != nil {
		t.Fatalf("phải ghi run/autostart-status.json: %v", err)
	}
	var raw map[string]any
	if err := json.Unmarshal(b, &raw); err != nil {
		t.Fatal(err)
	}
	for _, k := range []string{"os", "linger", "linger_required", "docker_enabled", "docker_mode", "checked_at"} {
		if _, ok := raw[k]; !ok {
			t.Errorf("autostart-status.json thiếu khoá %q: %s", k, b)
		}
	}
	return raw
}

// v0.1.53 (F-95): trực canh (12 phút) và bản sao ngoài máy (tuần) cũng là timer systemd --user —
// chỉ cần MỘT trong số đó bật là cần linger.
func TestCheckAutostart_ChiTrucCanhHoacOffsiteBat_CanLinger(t *testing.T) {
	for name, enabledArgs := range map[string]string{"trực canh": argsUserWatchdog, "bản sao ngoài máy": argsUserOffsite} {
		fr := &fake.Runner{Responses: []fake.Response{
			{Match: cmdIs("docker", argsDockerInfo), Output: []byte(securityOptsSys)},
			{Match: cmdIs("systemctl", argsSysDocker), Output: []byte("enabled\n")},
			{Match: cmdIs("loginctl", argsLinger), Output: []byte("no\n")},
			{Match: cmdIs("systemctl", argsUserReqPath), Output: []byte("disabled\n"), Err: errExit1},
			{Match: cmdIs("systemctl", argsUserTimer), Output: []byte("disabled\n"), Err: errExit1},
			{Match: cmdIs("systemctl", enabledArgs), Output: []byte("enabled\n")},
		}}
		st := CheckAutostart(context.Background(), AutostartDeps{Runner: fr, GOOS: "linux", UID: "1000"})
		if !st.LingerRequired || st.Linger != "no" {
			t.Errorf("%s bật (còn lại tắt) ⇒ phải cần linger: %+v", name, st)
		}
		if l := autostartLines(st)[1]; l.OK || !strings.Contains(l.Info, "sudo loginctl enable-linger $USER") {
			t.Errorf("%s: linger tắt mà cần ⇒ phải cảnh báo: %+v", name, l)
		}
	}
	// Không timer/path nào bật ⇒ không cần linger.
	fr := &fake.Runner{Responses: []fake.Response{
		{Match: cmdIs("docker", argsDockerInfo), Output: []byte(securityOptsSys)},
		{Match: cmdIs("systemctl", argsSysDocker), Output: []byte("enabled\n")},
		{Match: cmdIs("loginctl", argsLinger), Output: []byte("no\n")},
	}}
	if st := CheckAutostart(context.Background(), AutostartDeps{Runner: fr, GOOS: "linux", UID: "1000"}); st.LingerRequired {
		t.Errorf("không lịch nào ⇒ không cần linger: %+v", st)
	}
}
