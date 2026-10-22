package hostlink

import (
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"runtime"
	"testing"
	"time"
)

func readHeartbeatRaw(t *testing.T, dir string) map[string]any {
	t.Helper()
	b, err := os.ReadFile(HeartbeatPath(dir))
	if err != nil {
		t.Fatalf("đọc genh-heartbeat.json: %v", err)
	}
	var m map[string]any
	if err := json.Unmarshal(b, &m); err != nil {
		t.Fatalf("genh-heartbeat.json không phải JSON: %v", err)
	}
	return m
}

func TestStartHeartbeat_GhiDuKhoa_StopXoaTep(t *testing.T) {
	old := heartbeatEvery
	heartbeatEvery = 20 * time.Millisecond
	defer func() { heartbeatEvery = old }()

	dir := t.TempDir()
	stop := StartHeartbeat(dir, "restore")
	m := readHeartbeatRaw(t, dir)
	for _, k := range []string{"pid", "op", "boot_id", "started_at", "at"} {
		if _, ok := m[k]; !ok {
			t.Errorf("thiếu khoá %q: %v", k, m)
		}
	}
	if int(m["pid"].(float64)) != os.Getpid() || m["op"] != "restore" {
		t.Errorf("pid/op sai: %v", m)
	}
	if runtime.GOOS == "linux" && m["boot_id"] == "" {
		t.Errorf("Linux phải có boot_id: %v", m)
	}
	first := m["at"].(string)
	if _, err := time.Parse(time.RFC3339, first); err != nil {
		t.Errorf("at phải là RFC3339: %q", first)
	}
	// Nhịp được ghi lại (at đổi sau ≥ 1 giây — RFC3339 làm tròn giây; chỉ kiểm tệp còn đó).
	time.Sleep(60 * time.Millisecond)
	hb, err := ReadHeartbeat(dir)
	if err != nil || hb.PID != os.Getpid() || hb.Op != "restore" {
		t.Fatalf("ReadHeartbeat: %+v %v", hb, err)
	}
	stop()
	stop() // gọi lại vô hại
	if _, err := os.Stat(HeartbeatPath(dir)); !errors.Is(err, os.ErrNotExist) {
		t.Fatalf("stop() phải xoá genh-heartbeat.json: %v", err)
	}
}

func TestStart_GhiPIDVaBootID_FinishGiuNguyen(t *testing.T) {
	dir := t.TempDir()
	if err := Start(dir, "v0.1.36"); err != nil {
		t.Fatal(err)
	}
	st, err := ReadStatus(dir)
	if err != nil {
		t.Fatal(err)
	}
	if st.PID != os.Getpid() || st.BootID != BootID() {
		t.Fatalf("Start phải ghi pid + boot_id: %+v", st)
	}
	if runtime.GOOS == "linux" && st.BootID == "" {
		t.Fatalf("Linux phải có boot_id: %+v", st)
	}
	if err := Finish(dir, "done", "v0.1.37", ""); err != nil {
		t.Fatal(err)
	}
	st2, _ := ReadStatus(dir)
	if st2.PID != st.PID || st2.BootID != st.BootID || st2.State != "done" {
		t.Fatalf("Finish phải giữ pid/boot_id: %+v", st2)
	}
	var raw map[string]any
	b, _ := os.ReadFile(filepath.Join(Dir(dir), StatusFile))
	_ = json.Unmarshal(b, &raw)
	if _, ok := raw["pid"]; !ok {
		t.Errorf("update-status.json thiếu khoá pid: %s", b)
	}
	if runtime.GOOS == "linux" {
		if _, ok := raw["boot_id"]; !ok {
			t.Errorf("update-status.json thiếu khoá boot_id: %s", b)
		}
	}
}

func TestAutostartStatus_KhoaJSON(t *testing.T) {
	dir := t.TempDir()
	in := AutostartStatus{OS: "linux", Linger: "no", LingerRequired: true, DockerEnabled: "yes", DockerMode: "system"}
	if err := WriteAutostartStatus(dir, in); err != nil {
		t.Fatal(err)
	}
	b, err := os.ReadFile(AutostartStatusPath(dir))
	if err != nil {
		t.Fatal(err)
	}
	var raw map[string]any
	if err := json.Unmarshal(b, &raw); err != nil {
		t.Fatal(err)
	}
	for _, k := range []string{"os", "linger", "linger_required", "docker_enabled", "docker_mode", "checked_at"} {
		if _, ok := raw[k]; !ok {
			t.Errorf("thiếu khoá %q: %s", k, b)
		}
	}
	if raw["linger_required"] != true {
		t.Errorf("linger_required phải là bool true: %s", b)
	}
	out, err := ReadAutostartStatus(dir)
	if err != nil || out.Linger != "no" || out.CheckedAt == "" {
		t.Fatalf("ReadAutostartStatus: %+v %v", out, err)
	}
}
