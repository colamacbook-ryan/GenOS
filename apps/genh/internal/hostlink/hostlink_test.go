package hostlink

import (
	"errors"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"syscall"
	"testing"
)

// v0.1.33: genh.json mang trạng thái lịch tự cập nhật đêm cho Console (gh/system_api/update.py).
func TestAutoUpdateEnabled(t *testing.T) {
	root := t.TempDir()
	// Không rõ ⇒ không có khoá (api trả null).
	if err := WriteInfo(root, "v0.1.33", "systemd", nil); err != nil {
		t.Fatal(err)
	}
	raw, _ := os.ReadFile(filepath.Join(Dir(root), InfoFile))
	if strings.Contains(string(raw), "auto_update_enabled") {
		t.Fatalf("nil phải bỏ khoá auto_update_enabled: %s", raw)
	}
	off := false
	if err := WriteInfo(root, "v0.1.33", "systemd", &off); err != nil {
		t.Fatal(err)
	}
	raw, _ = os.ReadFile(filepath.Join(Dir(root), InfoFile))
	if !strings.Contains(string(raw), `"auto_update_enabled": false`) {
		t.Fatalf("false phải ghi rõ (không omitempty): %s", raw)
	}
	// auto-update enable: chỉ đổi cờ, giữ version/updater/requests.
	if err := SetAutoUpdate(root, "v9.9.9", true); err != nil {
		t.Fatal(err)
	}
	info, err := ReadInfo(root)
	if err != nil || info.AutoUpdateEnabled == nil || !*info.AutoUpdateEnabled || info.Version != "v0.1.33" ||
		info.Updater != "systemd" || len(info.Requests) != len(Requests) {
		t.Fatalf("SetAutoUpdate(true) = %+v, %v", info, err)
	}
	if err := SetAutoUpdate(root, "v9.9.9", false); err != nil {
		t.Fatal(err)
	}
	if info, _ := ReadInfo(root); info.AutoUpdateEnabled == nil || *info.AutoUpdateEnabled {
		t.Fatalf("SetAutoUpdate(false) = %+v", info)
	}
	// Chưa có genh.json ⇒ ghi mới với version cho trước, chưa có watcher.
	fresh := t.TempDir()
	if err := SetAutoUpdate(fresh, "v0.1.33", true); err != nil {
		t.Fatal(err)
	}
	if info, err := ReadInfo(fresh); err != nil || info.Version != "v0.1.33" || info.Updater != "" ||
		info.AutoUpdateEnabled == nil || !*info.AutoUpdateEnabled {
		t.Fatalf("SetAutoUpdate trên máy chưa có genh.json = %+v, %v", info, err)
	}
}

func TestRoundTrip(t *testing.T) {
	root := t.TempDir()
	if err := WriteInfo(root, "v0.1.17", "systemd", nil); err != nil {
		t.Fatal(err)
	}
	info, err := ReadInfo(root)
	if err != nil || info.Version != "v0.1.17" || info.Updater != "systemd" {
		t.Fatalf("info = %+v, %v", info, err)
	}
	st, _ := os.Stat(Dir(root))
	switch runtime.GOOS {
	case "linux":
		// v0.1.45: tạo 0770 — EnsureRunPerms (sau compose up) mới đặt nhóm 10001 + 2770.
		if st.Mode().Perm()&0o007 != 0 {
			t.Fatalf("run dir perm = %v, không được mở cho người dùng khác", st.Mode().Perm())
		}
	case "darwin":
		if st.Mode().Perm() != 0o777 {
			t.Fatalf("run dir perm = %v, want 0777 (macOS giữ như cũ)", st.Mode().Perm())
		}
	}

	if c, err := ConsumeRequest(root); HasRequest(root) || c || err != nil {
		t.Fatalf("chưa có yêu cầu mà HasRequest/ConsumeRequest báo có: consumed=%v err=%v", c, err)
	}
	if err := os.WriteFile(RequestPath(root), []byte(`{"requested_at":"x"}`), 0o666); err != nil {
		t.Fatal(err)
	}
	if !HasRequest(root) {
		t.Fatal("phải thấy yêu cầu")
	}
	if c, err := ConsumeRequest(root); !c || err != nil || HasRequest(root) {
		t.Fatalf("ConsumeRequest phải báo có rồi xoá tệp yêu cầu: consumed=%v err=%v", c, err)
	}

	if err := Start(root, "v0.1.16"); err != nil {
		t.Fatal(err)
	}
	if err := Finish(root, "done", "v0.1.17", "xong"); err != nil {
		t.Fatal(err)
	}
	s, err := ReadStatus(root)
	if err != nil || s.State != "done" || s.From != "v0.1.16" || s.To != "v0.1.17" || s.StartedAt == "" || s.FinishedAt == "" {
		t.Fatalf("status = %+v, %v", s, err)
	}
}

func TestRestoreRequestRoundTrip(t *testing.T) {
	root := t.TempDir()
	if err := WriteInfo(root, "v0.1.20", "cron", nil); err != nil {
		t.Fatal(err)
	}
	if info, _ := ReadInfo(root); len(info.Requests) != 5 || info.Requests[1] != "restore" || info.Requests[2] != "offsite" ||
		info.Requests[3] != "doctor" || info.Requests[4] != "watchdog" {
		t.Fatalf("genh.json phải báo watcher nhận update, restore, offsite (v0.1.40), doctor, watchdog (v0.1.44): %+v", info)
	}
	if err := WriteInfo(root, "v0.1.20", "", nil); err != nil {
		t.Fatal(err)
	}
	if info, _ := ReadInfo(root); len(info.Requests) != 0 {
		t.Fatalf("chưa có watcher thì không báo loại yêu cầu nào: %+v", info)
	}

	if HasRestoreRequest(root) {
		t.Fatal("chưa có yêu cầu khôi phục")
	}
	if _, err := ConsumeRestoreRequest(root); err == nil {
		t.Fatal("không có tệp thì phải lỗi")
	}
	key := "backups/20260929T010203Z-abcdef12.pgcustom.enc"
	if err := os.WriteFile(RestoreRequestPath(root), []byte(`{"id":"r1","key":"`+key+`"}`), 0o666); err != nil {
		t.Fatal(err)
	}
	if !HasRestoreRequest(root) {
		t.Fatal("phải thấy yêu cầu khôi phục")
	}
	req, err := ConsumeRestoreRequest(root)
	if err != nil || req.Key != key || HasRestoreRequest(root) {
		t.Fatalf("ConsumeRestoreRequest = %+v, %v (phải xoá tệp)", req, err)
	}
	// Tệp hỏng: vẫn bị xoá để watcher không kích lặp.
	_ = os.WriteFile(RestoreRequestPath(root), []byte(`{"key":""}`), 0o666)
	if _, err := ConsumeRestoreRequest(root); err == nil || HasRestoreRequest(root) {
		t.Fatal("yêu cầu thiếu khoá phải lỗi và bị xoá")
	}

	if err := StartRestore(root, key); err != nil {
		t.Fatal(err)
	}
	if err := FinishRestore(root, "done", "backups/safe.enc", ""); err != nil {
		t.Fatal(err)
	}
	s, err := ReadRestoreStatus(root)
	if err != nil || s.State != "done" || s.Key != key || s.SafetyKey != "backups/safe.enc" || s.StartedAt == "" || s.FinishedAt == "" {
		t.Fatalf("restore status = %+v, %v", s, err)
	}
}

func TestPendingDispatch(t *testing.T) {
	root := t.TempDir()
	if err := EnsureDir(root); err != nil {
		t.Fatal(err)
	}
	if got := Pending(root); got != "" {
		t.Fatalf("hộp thư trống, Pending = %q", got)
	}
	// v0.1.44: thứ tự update > restore > offsite > doctor > watchdog.
	_ = os.WriteFile(WatchdogRequestPath(root), []byte(`{"action":"test"}`), 0o666)
	if got := Pending(root); got != "watchdog" {
		t.Fatalf("Pending = %q, muốn watchdog", got)
	}
	_ = os.WriteFile(DoctorRequestPath(root), []byte(`{}`), 0o666)
	if got := Pending(root); got != "doctor" {
		t.Fatalf("Pending = %q, muốn doctor (trước watchdog)", got)
	}
	_ = os.WriteFile(OffsiteRequestPath(root), []byte(`{"action":"run"}`), 0o666)
	if got := Pending(root); got != "offsite" {
		t.Fatalf("Pending = %q, muốn offsite (trước doctor)", got)
	}
	_ = os.WriteFile(RestoreRequestPath(root), []byte(`{"key":"k"}`), 0o666)
	if got := Pending(root); got != "restore" {
		t.Fatalf("Pending = %q, muốn restore", got)
	}
	_ = os.WriteFile(RequestPath(root), []byte(`{}`), 0o666)
	if got := Pending(root); got != "update" {
		t.Fatalf("có cả hai thì cập nhật trước, Pending = %q", got)
	}
}

// v0.1.34 (F-33): update-blocked.json — ghi/đọc/xoá.
func TestUpdateBlocked_RoundTrip(t *testing.T) {
	root := t.TempDir()
	if _, ok, err := ReadUpdateBlocked(root); ok || err != nil {
		t.Fatalf("chưa có tệp: muốn false,nil — được %v,%v", ok, err)
	}
	if err := ClearUpdateBlocked(root); err != nil {
		t.Fatalf("xoá khi chưa có tệp phải nil: %v", err)
	}
	if err := WriteUpdateBlocked(root, UpdateBlocked{Version: "v0.1.34", Code: "GH-E942", BackupKey: "backups/x.enc", Message: "migrate lỗi"}); err != nil {
		t.Fatal(err)
	}
	b, ok, err := ReadUpdateBlocked(root)
	if err != nil || !ok {
		t.Fatalf("đọc: %v,%v", ok, err)
	}
	if b.Version != "v0.1.34" || b.Code != "GH-E942" || b.BackupKey != "backups/x.enc" || b.BlockedAt == "" {
		t.Errorf("nội dung sai: %+v", b)
	}
	raw, _ := os.ReadFile(filepath.Join(Dir(root), UpdateBlockedFile))
	for _, k := range []string{`"version"`, `"blocked_at"`, `"code"`, `"backup_key"`, `"message"`} {
		if !strings.Contains(string(raw), k) {
			t.Errorf("thiếu khoá %s: %s", k, raw)
		}
	}
	if err := ClearUpdateBlocked(root); err != nil {
		t.Fatal(err)
	}
	if _, ok, _ := ReadUpdateBlocked(root); ok {
		t.Error("đã xoá mà vẫn đọc được")
	}
}

// v0.1.34 (F-11): disk-status.json — chuông "đĩa sắp đầy" (v0.1.36) đọc, giữ tên khoá.
func TestDiskStatus_RoundTrip(t *testing.T) {
	root := t.TempDir()
	if _, err := ReadDiskStatus(root); err == nil {
		t.Fatal("chưa có tệp phải lỗi")
	}
	in := DiskStatus{State: "low", FreeBytes: 1 << 30, MinBytes: 5 << 30, Path: "/var/lib/docker", PrunedImages: 2}
	if err := WriteDiskStatus(root, in); err != nil {
		t.Fatal(err)
	}
	got, err := ReadDiskStatus(root)
	if err != nil {
		t.Fatal(err)
	}
	if got.State != "low" || got.FreeBytes != in.FreeBytes || got.MinBytes != in.MinBytes || got.Path != in.Path || got.PrunedImages != 2 || got.CheckedAt == "" {
		t.Errorf("nội dung sai: %+v", got)
	}
	raw, _ := os.ReadFile(filepath.Join(Dir(root), DiskStatusFile))
	for _, k := range []string{`"state"`, `"free_bytes"`, `"min_bytes"`, `"path"`, `"pruned_images"`, `"checked_at"`} {
		if !strings.Contains(string(raw), k) {
			t.Errorf("thiếu khoá %s: %s", k, raw)
		}
	}
}

// Quay về bản cũ thất bại → rollback_failed; tệp cũ không có khoá = quay về ổn.
func TestUpdateBlocked_RollbackFailed(t *testing.T) {
	root := t.TempDir()
	if err := WriteUpdateBlocked(root, UpdateBlocked{Version: "v0.1.34", RollbackFailed: true}); err != nil {
		t.Fatal(err)
	}
	b, ok, err := ReadUpdateBlocked(root)
	if err != nil || !ok || !b.RollbackFailed {
		t.Fatalf("muốn rollback_failed=true: %+v ok=%v err=%v", b, ok, err)
	}
	if err := os.WriteFile(filepath.Join(Dir(root), UpdateBlockedFile), []byte(`{"version":"v0.1.34","blocked_at":"x"}`), 0o644); err != nil {
		t.Fatal(err)
	}
	if b, _, _ := ReadUpdateBlocked(root); b.RollbackFailed {
		t.Fatal("tệp không có khoá rollback_failed phải đọc ra false")
	}
}

func TestUpdateInProgress_MarkClear(t *testing.T) {
	root := t.TempDir()
	if UpdateInProgressExists(root) {
		t.Fatal("chưa ghi thì không có")
	}
	if err := MarkUpdateInProgress(root, UpdateInProgress{Version: "v0.1.34", BackupKey: "k"}); err != nil {
		t.Fatal(err)
	}
	if !UpdateInProgressExists(root) {
		t.Fatal("phải có sau khi ghi")
	}
	if err := ClearUpdateInProgress(root); err != nil || UpdateInProgressExists(root) {
		t.Fatalf("phải xoá được: %v", err)
	}
	if err := ClearUpdateInProgress(root); err != nil {
		t.Fatalf("xoá lần 2 (không có tệp) phải nil: %v", err)
	}
}

// Lịch đêm gặp bản bị chặn: trả hộp thư về ĐÚNG từng byte như trước (không
// làm mới finished_at, không ghi đè thông điệp gốc).
func TestSnapshotRestoreStatus(t *testing.T) {
	root := t.TempDir()
	if _, ok := SnapshotStatus(root); ok {
		t.Fatal("chưa có tệp thì ok=false")
	}
	if err := Start(root, "v0.1.33"); err != nil {
		t.Fatal(err)
	}
	if err := RestoreStatusSnapshot(root, nil, false); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(filepath.Join(Dir(root), StatusFile)); !os.IsNotExist(err) {
		t.Fatalf("trước đó chưa có tệp thì phải xoá: %v", err)
	}
	if err := Finish(root, "failed", "v0.1.34", "lỗi gốc (GH-E945)"); err != nil {
		t.Fatal(err)
	}
	raw, ok := SnapshotStatus(root)
	if !ok {
		t.Fatal("phải đọc được")
	}
	if err := Start(root, "v0.1.34"); err != nil {
		t.Fatal(err)
	}
	if err := RestoreStatusSnapshot(root, raw, true); err != nil {
		t.Fatal(err)
	}
	got, _ := os.ReadFile(filepath.Join(Dir(root), StatusFile))
	if string(got) != string(raw) {
		t.Fatalf("phải trả về đúng từng byte:\n%s\n---\n%s", raw, got)
	}
}

// Tệp tạm cố định <tệp>.tmp bị cài sẵn symlink tới tệp ngoài: genh KHÔNG được
// ghi theo symlink đó (tên tạm ngẫu nhiên, O_EXCL).
func TestWriteJSON_DoesNotFollowPlantedTmpSymlink(t *testing.T) {
	root := t.TempDir()
	if err := EnsureDir(root); err != nil {
		t.Fatal(err)
	}
	victim := filepath.Join(t.TempDir(), "victim.txt")
	if err := os.WriteFile(victim, []byte("nguyên vẹn"), 0o644); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink(victim, filepath.Join(Dir(root), UpdateBlockedFile+".tmp")); err != nil {
		t.Skipf("không tạo được symlink: %v", err)
	}
	if err := WriteUpdateBlocked(root, UpdateBlocked{Version: "v0.1.34"}); err != nil {
		t.Fatal(err)
	}
	if b, _ := os.ReadFile(victim); string(b) != "nguyên vẹn" {
		t.Fatalf("tệp ngoài bị ghi đè qua symlink: %q", b)
	}
	if b, ok, err := ReadUpdateBlocked(root); err != nil || !ok || b.Version != "v0.1.34" {
		t.Fatalf("update-blocked.json phải được ghi đúng: %+v %v %v", b, ok, err)
	}
	if fi, err := os.Stat(filepath.Join(Dir(root), UpdateBlockedFile)); runtime.GOOS != "windows" && (err != nil || fi.Mode().Perm() != 0o644) {
		t.Fatalf("tệp trạng thái phải 0644 để api đọc được: %v %v", fi, err)
	}
}

// ─── v0.1.53 (F-97): xoá yêu cầu lỗi KHÔNG được coi là "đã nhận" ─────────────

func fakeRemove(t *testing.T, f func(string) error) {
	t.Helper()
	old := removeFile
	removeFile = f
	t.Cleanup(func() { removeFile = old })
}

func TestConsumeRequest_LoiXoa(t *testing.T) {
	root := t.TempDir()
	if err := EnsureDir(root); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(RequestPath(root), []byte(`{"requested_at":"2026-10-10T01:00:00Z"}`), 0o666); err != nil {
		t.Fatal(err)
	}

	// EACCES ⇒ (false, err): caller KHÔNG được làm yêu cầu.
	fakeRemove(t, func(string) error { return &os.PathError{Op: "remove", Path: RequestPath(root), Err: syscall.EACCES} })
	consumed, err := ConsumeRequest(root)
	if consumed || err == nil || !errors.Is(err, ErrRequestUndeletable) || !errors.Is(err, syscall.EACCES) {
		t.Fatalf("EACCES: consumed=%v err=%v", consumed, err)
	}
	if !HasRequest(root) {
		t.Fatal("tệp yêu cầu phải còn nguyên")
	}

	// ENOENT (bị xoá mất giữa chừng) ⇒ (true, nil).
	fakeRemove(t, func(string) error { return &os.PathError{Op: "remove", Path: RequestPath(root), Err: syscall.ENOENT} })
	if consumed, err := ConsumeRequest(root); !consumed || err != nil {
		t.Fatalf("ENOENT: consumed=%v err=%v", consumed, err)
	}

	// Xoá thật ⇒ (true, nil); không có tệp ⇒ (false, nil).
	fakeRemove(t, os.Remove)
	if consumed, err := ConsumeRequest(root); !consumed || err != nil || HasRequest(root) {
		t.Fatalf("xoá thật: consumed=%v err=%v", consumed, err)
	}
	if consumed, err := ConsumeRequest(root); consumed || err != nil {
		t.Fatalf("không có tệp: consumed=%v err=%v", consumed, err)
	}
}

func TestConsumeRestoreRequest_LoiXoa(t *testing.T) {
	root := t.TempDir()
	if err := EnsureDir(root); err != nil {
		t.Fatal(err)
	}
	key := "backups/20260929T010203Z-abcdef12.pgcustom.enc"
	if err := os.WriteFile(RestoreRequestPath(root), []byte(`{"key":"`+key+`","requested_at":"2026-10-10T01:00:00Z"}`), 0o666); err != nil {
		t.Fatal(err)
	}
	fakeRemove(t, func(string) error { return syscall.EACCES })
	if _, err := ConsumeRestoreRequest(root); !errors.Is(err, ErrRequestUndeletable) {
		t.Fatalf("restore xoá lỗi phải trả ErrRequestUndeletable: %v", err)
	}
	if !HasRestoreRequest(root) {
		t.Fatal("tệp yêu cầu khôi phục phải còn")
	}
	// Ghi failed GH-E94C một lần cho mỗi yêu cầu (idempotent theo requested_at).
	wrote, err := FailUndeletableRestore(root, "GH-E94C", "Không xoá được yêu cầu khôi phục trong run/request (GH-E94C)")
	if err != nil || !wrote {
		t.Fatalf("lần 1: wrote=%v err=%v", wrote, err)
	}
	before, _ := ReadRestoreStatus(root)
	if before.State != "failed" || before.RequestedAt != "2026-10-10T01:00:00Z" || !RestoreUndeletableReported(root, "GH-E94C") {
		t.Fatalf("restore-status = %+v", before)
	}
	if wrote, _ := FailUndeletableRestore(root, "GH-E94C", "x (GH-E94C)"); wrote {
		t.Fatal("lần 2 cùng yêu cầu không được ghi lại")
	}
	// Yêu cầu MỚI (requested_at khác) thì báo lại.
	if err := os.WriteFile(RestoreRequestPath(root), []byte(`{"key":"`+key+`","requested_at":"2026-10-10T02:00:00Z"}`), 0o666); err != nil {
		t.Fatal(err)
	}
	if RestoreUndeletableReported(root, "GH-E94C") {
		t.Fatal("yêu cầu mới không được coi là đã báo")
	}
}

func TestFailUndeletableUpdate_IdempotentTheoRequestedAt(t *testing.T) {
	root := t.TempDir()
	if err := EnsureDir(root); err != nil {
		t.Fatal(err)
	}
	write := func(at string) {
		t.Helper()
		if err := os.WriteFile(RequestPath(root), []byte(`{"requested_at":"`+at+`"}`), 0o666); err != nil {
			t.Fatal(err)
		}
	}
	write("2026-10-10T01:00:00Z")
	if UpdateUndeletableReported(root, "GH-E94C") {
		t.Fatal("chưa báo gì")
	}
	msg := "Không xoá được yêu cầu cập nhật trong run/request — máy chủ không làm yêu cầu này để tránh chạy lặp. Kiểm quyền thư mục run/request trên máy chủ rồi bấm Thử lại (GH-E94C)"
	if wrote, err := FailUndeletableUpdate(root, "v0.1.52", "GH-E94C", msg); err != nil || !wrote {
		t.Fatalf("lần 1: wrote=%v err=%v", wrote, err)
	}
	st1, err := ReadStatus(root)
	if err != nil || st1.State != "failed" || st1.Message != msg || st1.From != "v0.1.52" || st1.To != "v0.1.52" || st1.RequestedAt != "2026-10-10T01:00:00Z" {
		t.Fatalf("update-status = %+v, %v", st1, err)
	}
	raw1, _ := os.ReadFile(filepath.Join(Dir(root), StatusFile))
	if !UpdateUndeletableReported(root, "GH-E94C") {
		t.Fatal("cùng yêu cầu ⇒ đã báo")
	}
	if wrote, _ := FailUndeletableUpdate(root, "v0.1.52", "GH-E94C", msg); wrote {
		t.Fatal("lần 2 không được ghi lại")
	}
	if raw2, _ := os.ReadFile(filepath.Join(Dir(root), StatusFile)); string(raw1) != string(raw2) {
		t.Fatal("update-status.json bị ghi lại ở lần 2")
	}
	// Bấm Thử lại ⇒ yêu cầu mới ⇒ báo lại được.
	write("2026-10-10T03:00:00Z")
	if wrote, _ := FailUndeletableUpdate(root, "v0.1.52", "GH-E94C", msg); !wrote {
		t.Fatal("yêu cầu mới phải được báo")
	}
}

func TestRequestDirWritable(t *testing.T) {
	root := t.TempDir()
	if err := EnsureDir(root); err != nil {
		t.Fatal(err)
	}
	if err := RequestDirWritable(root); err != nil {
		t.Fatalf("thư mục ghi được: %v", err)
	}
	entries, _ := os.ReadDir(RequestDirPath(root))
	if len(entries) != 0 {
		t.Fatalf("tệp thăm dò phải được xoá: %v", entries)
	}
	fakeRemove(t, func(string) error { return syscall.EACCES })
	if err := RequestDirWritable(root); !errors.Is(err, ErrRequestUndeletable) {
		t.Fatalf("xoá lỗi: %v", err)
	}
	_ = os.RemoveAll(RequestDirPath(root))
	if err := RequestDirWritable(root); !errors.Is(err, ErrRequestUndeletable) {
		t.Fatalf("thư mục không có: %v", err)
	}
}
