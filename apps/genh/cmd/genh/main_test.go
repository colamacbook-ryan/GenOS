package main

import (
	"context"
	"errors"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
	"syscall"
	"testing"
	"time"

	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/autoupdate"
	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/hostlink"
	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/ops"
	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/selfupdate"
)

func TestSelfUpdateMinAge(t *testing.T) {
	if selfupdate.NightlyMinAge != 24*time.Hour {
		t.Fatalf("thời gian chín của lịch đêm phải là 24 giờ, được %v", selfupdate.NightlyMinAge)
	}
	cases := []struct {
		name             string
		yes, ifRequested bool
		want             time.Duration
	}{
		{"lịch đêm: --yes, không --if-requested", true, false, 24 * time.Hour},
		{"Cập nhật ngay: --yes --if-requested", true, true, 0},
		{"gõ tay genh update (không --yes)", false, false, 0},
		{"--if-requested không --yes", false, true, 0},
	}
	for _, c := range cases {
		if got := selfUpdateMinAge(c.yes, c.ifRequested); got != c.want {
			t.Errorf("%s: selfUpdateMinAge(%v, %v) = %v, muốn %v", c.name, c.yes, c.ifRequested, got, c.want)
		}
	}
}

// Đi trọn đường nút "Cập nhật ngay": cờ runHandleRequests chuyển sang (kèm
// --port/--install-dir như thật) → parse bằng đúng bộ cờ của `genh update` →
// ifRequested = true → thời gian chín = 0 (không bị chặn).
func TestHandleRequestUpdateArgs_CapNhatNgayKhongBiChan(t *testing.T) {
	for _, quiet := range []bool{true, false} {
		args := append(handleRequestUpdateArgs(quiet), "--port", "8443", "--install-dir", t.TempDir())
		f, err := parseUpdateFlags(args)
		if err != nil {
			t.Fatalf("quiet=%v: parse %q bằng cờ của genh update lỗi: %v", quiet, args, err)
		}
		if !f.yes || !f.ifRequested || f.quiet != quiet {
			t.Fatalf("quiet=%v: muốn yes=true ifRequested=true quiet=%v — được %+v", quiet, quiet, f)
		}
		if got := selfUpdateMinAge(f.yes, f.ifRequested); got != 0 {
			t.Fatalf("quiet=%v: nút \"Cập nhật ngay\" không được bị thời gian chín chặn, MinAge = %v", quiet, got)
		}
	}
}

// Đi trọn đường lịch đêm: lấy đúng các cờ timer truyền cho `genh update`
// (dòng crontab do internal/autoupdate sinh) → parse → thời gian chín 24 giờ.
func TestLichDem_ApThoiGianChin24h(t *testing.T) {
	line := autoupdate.CrontabLine("/opt/genh/bin/genh", "/tmp/auto-update.log", 7, autoupdate.NightlyJob{InstallDir: "/home/o/.gen-harness", Port: 9443})
	fields := strings.Fields(line)
	var args []string
	inUpdate := false
	for _, f := range fields {
		if f == ">>" {
			break
		}
		if inUpdate {
			args = append(args, f)
		}
		if f == "update" {
			inUpdate = true
		}
	}
	if !inUpdate {
		t.Fatalf("dòng crontab không gọi `genh update`: %q", line)
	}
	f, err := parseUpdateFlags(args)
	if err != nil {
		t.Fatalf("parse cờ lịch đêm %q lỗi: %v", args, err)
	}
	// v0.1.53: lịch đêm mang --install-dir/--port của bản cài (không chỉ `update --yes --quiet`).
	if f.installDir != "/home/o/.gen-harness" || f.port != 9443 {
		t.Fatalf("lịch đêm phải mang --install-dir/--port: %+v", f)
	}
	if got := selfUpdateMinAge(f.yes, f.ifRequested); got != selfupdate.NightlyMinAge {
		t.Fatalf("lịch đêm (%q) phải áp thời gian chín %v, được %v", args, selfupdate.NightlyMinAge, got)
	}
}

// Bản genh mới bị thời gian chín hoãn: dòng kết của `--quiet` (vào
// logs/auto-update.log) KHÔNG được nói "cập nhật xong." — người đọc log sẽ
// tưởng bản mới đã cài.
func TestUpdateDoneLine_HoanThiKhongNoiCapNhatXong(t *testing.T) {
	if got := updateDoneLine(false); got != "genh: cập nhật xong." {
		t.Fatalf("không hoãn: muốn %q, được %q", "genh: cập nhật xong.", got)
	}
	for _, got := range []string{updateDoneLine(true), deferredAfterRunLine} {
		if strings.Contains(got, "genh: cập nhật xong.") || strings.Contains(got, "cập nhật xong") {
			t.Fatalf("bị hoãn mà dòng kết vẫn nói cập nhật xong: %q", got)
		}
		if !strings.Contains(got, "đợi đủ 24 giờ") {
			t.Errorf("dòng kết khi hoãn thiếu %q: %q", "đợi đủ 24 giờ", got)
		}
	}
	if !strings.Contains(updateDoneLine(true), "không có gì để cập nhật") {
		t.Errorf("hoãn + dịch vụ đã khớp: phải nói không có gì để cập nhật: %q", updateDoneLine(true))
	}
}

func TestDecideServiceUpdate(t *testing.T) {
	const v = "v0.1.34"
	cases := []struct {
		name     string
		in       serviceUpdateInput
		wantSkip bool
		wantKind string
	}{
		{"(a) lịch đêm + bản bị chặn đúng bản này → blocked",
			serviceUpdateInput{Scheduled: true, Version: v, BlockedVersion: v}, true, "blocked"},
		{"(b) lịch đêm + bị chặn bản KHÁC + chưa khớp → chạy (bản mới hơn vẫn nhận)",
			serviceUpdateInput{Scheduled: true, Version: v, BlockedVersion: "v0.1.33"}, false, ""},
		{"(c) gõ tay/\"Cập nhật ngay\" + bị chặn đúng bản này → chạy",
			serviceUpdateInput{Scheduled: false, Version: v, BlockedVersion: v}, false, ""},
		{"(d) đã khớp + không vừa tự cập nhật → up-to-date",
			serviceUpdateInput{Version: v, InSync: true}, true, "up-to-date"},
		{"(e) vừa tự cập nhật + đã khớp → chạy",
			serviceUpdateInput{Version: v, InSync: true, SelfUpdated: true}, false, ""},
		{"(f) bị chặn bản khác + đã khớp → up-to-date",
			serviceUpdateInput{Scheduled: true, Version: v, InSync: true, BlockedVersion: "v0.1.33"}, true, "up-to-date"},
		{"lịch đêm + bị chặn đúng bản + đã khớp → blocked (ưu tiên)",
			serviceUpdateInput{Scheduled: true, Version: v, InSync: true, BlockedVersion: v}, true, "blocked"},
		{"so khớp CHÍNH XÁC version (v0.1.3 ≠ v0.1.34)",
			serviceUpdateInput{Scheduled: true, Version: v, BlockedVersion: "v0.1.3"}, false, ""},
	}
	for _, c := range cases {
		skip, kind := decideServiceUpdate(c.in)
		if skip != c.wantSkip || kind != c.wantKind {
			t.Errorf("%s: được (%v,%q), muốn (%v,%q)", c.name, skip, kind, c.wantSkip, c.wantKind)
		}
	}
}

func TestServiceUpdateLines_E2EContract(t *testing.T) {
	ok := hostlink.UpdateBlocked{Version: "v0.1.34"}
	if !strings.Contains(blockedLine("v0.1.34", ok), "lịch đêm không tự thử lại") {
		t.Errorf("dòng bản bị chặn phải chứa \"lịch đêm không tự thử lại\": %q", blockedLine("v0.1.34", ok))
	}
	if !strings.Contains(upToDateLine("v0.1.34"), "không cần cập nhật") {
		t.Errorf("dòng đã mới nhất phải chứa \"không cần cập nhật\": %q", upToDateLine("v0.1.34"))
	}
	for _, l := range []string{blockedLine("v0.1.34", ok), upToDateLine("v0.1.34")} {
		if strings.Contains(l, "genh: cập nhật xong.") {
			t.Errorf("chỉ in \"genh: cập nhật xong.\" khi RunUpdate chạy xong: %q", l)
		}
	}
}

// Lịch đêm gặp bản bị chặn HAI đêm liền: hộp thư Console giữ NGUYÊN thông
// điệp + finished_at của đêm lỗi (thẻ đỏ tự hết sau 24 giờ, không mất chi tiết).
func TestSkipBlockedUpdate_KeepsStatusAcrossNights(t *testing.T) {
	dir := t.TempDir()
	orig := "migrate lỗi — đã tự quay về bản cũ (GH-E945)"
	if err := hostlink.Start(dir, "v0.1.33"); err != nil {
		t.Fatal(err)
	}
	if err := hostlink.Finish(dir, "failed", "v0.1.34", orig); err != nil {
		t.Fatal(err)
	}
	before, _ := hostlink.SnapshotStatus(dir)
	b := hostlink.UpdateBlocked{Version: "v0.1.34", BackupKey: "backups/k.enc"}
	for night := 0; night < 2; night++ {
		snap, had := hostlink.SnapshotStatus(dir)
		if err := hostlink.Start(dir, "v0.1.34"); err != nil { // đúng như runUpdate
			t.Fatal(err)
		}
		var out strings.Builder
		skipBlockedUpdate(&out, dir, "v0.1.34", b, snap, had, false)
		if !strings.Contains(out.String(), "lịch đêm không tự thử lại") {
			t.Errorf("đêm %d: thiếu dòng log: %q", night, out.String())
		}
	}
	after, _ := hostlink.SnapshotStatus(dir)
	if string(after) != string(before) {
		t.Fatalf("update-status.json bị đổi:\n%s\n---\n%s", before, after)
	}
	st, _ := hostlink.ReadStatus(dir)
	if st.State != "failed" || st.Message != orig {
		t.Errorf("phải giữ thông điệp gốc: %+v", st)
	}
}

// Quay về bản cũ THẤT BẠI: dòng log/hộp thư không được nói "đã tự quay về bản
// cũ", phải nói cần xử lý tay + bản sao lưu.
func TestBlockedMessages_RollbackFailed(t *testing.T) {
	b := hostlink.UpdateBlocked{Version: "v0.1.34", BackupKey: "backups/k.enc", RollbackFailed: true}
	for _, m := range []string{blockedLine("v0.1.34", b), blockedConsoleMessage("v0.1.34", b)} {
		if strings.Contains(m, "đã tự quay về bản cũ") {
			t.Errorf("rollback thất bại mà vẫn nói đã quay về bản cũ: %q", m)
		}
		for _, want := range []string{"cần xử lý tay", "backups/k.enc", "lịch đêm không tự thử lại"} {
			if !strings.Contains(strings.ToLower(m), strings.ToLower(want)) {
				t.Errorf("thiếu %q: %q", want, m)
			}
		}
	}
	// Tiến trình re-exec (không có ảnh chụp): kết thúc "running" bằng thông điệp chặn đúng.
	dir := t.TempDir()
	_ = hostlink.Start(dir, "v0.1.33")
	skipBlockedUpdate(&strings.Builder{}, dir, "v0.1.34", b, nil, false, true)
	st, _ := hostlink.ReadStatus(dir)
	if st.State != "failed" || !strings.Contains(st.Message, "CŨNG THẤT BẠI") {
		t.Errorf("re-exec: hộp thư phải báo cần xử lý tay: %+v", st)
	}
}

// Hộp thư Console nhận đủ What — Next (mã); ổ đĩa đầy kèm số GB; bỏ dấu `.
func TestConsoleUpdateMessage(t *testing.T) {
	disk := &ops.OpError{Code: ops.ErrCodeUpdateDiskLow, What: "Ổ đĩa không đủ chỗ để tải bản mới — DỪNG LẠI, chưa đụng gì",
		Why: "còn 1.0 GB trống tại /var/lib/docker, cần tối thiểu 5 GB", Next: "Giải phóng ổ đĩa (xem `docker system df`), rồi chạy lại `genh update`."}
	got := consoleUpdateMessage(disk)
	for _, want := range []string{"Ổ đĩa không đủ chỗ", "còn 1.0 GB", "Giải phóng ổ đĩa", "(GH-E948)"} {
		if !strings.Contains(got, want) {
			t.Errorf("thiếu %q: %q", want, got)
		}
	}
	if strings.Contains(got, "`") {
		t.Errorf("không được còn dấu `: %q", got)
	}
	pull := &ops.OpError{Code: ops.ErrCodeUpdatePullFailed, What: "Tải bản mới thất bại", Why: "raw docker output", Next: "Kiểm mạng."}
	if got := consoleUpdateMessage(pull); got != "Tải bản mới thất bại — Kiểm mạng. (GH-E941)" || strings.Contains(got, "raw docker") {
		t.Errorf("được %q", got)
	}
	if got := consoleUpdateMessage(errors.New("lạ")); got != "lạ" {
		t.Errorf("lỗi thường: %q", got)
	}
}

// Review v0.1.34: quay về bản cũ thất bại mà CSDL CHƯA bị đụng — KHÔNG được
// bảo khôi phục bản sao lưu (xoá mất ghi chép sau lúc sao lưu), chỉ up -d.
func TestBlockedLine_RollbackFailed_DBNotTouched_NoRestoreAdvice(t *testing.T) {
	b := hostlink.UpdateBlocked{Version: "v0.1.34", RollbackFailed: true, DBTouched: false}
	for _, m := range []string{blockedLine("v0.1.34", b), blockedConsoleMessage("v0.1.34", b)} {
		if strings.Contains(m, "khôi phục bản sao lưu backups") || !strings.Contains(m, "KHÔNG khôi phục bản sao lưu") {
			t.Errorf("không được khuyên khôi phục: %q", m)
		}
		if !strings.Contains(m, "docker compose up -d --remove-orphans") || !strings.Contains(m, "cần xử lý tay") {
			t.Errorf("phải hướng dẫn up -d: %q", m)
		}
	}
	// Đã đụng CSDL: vẫn chỉ đúng bản sao lưu cần khôi phục.
	touched := hostlink.UpdateBlocked{Version: "v0.1.34", RollbackFailed: true, DBTouched: true, BackupKey: "backups/k.enc"}
	if l := blockedLine("v0.1.34", touched); !strings.Contains(l, "khôi phục bản sao lưu backups/k.enc") {
		t.Errorf("đã đụng CSDL phải chỉ bản sao lưu: %q", l)
	}
}

// Review v0.1.34: update-status.json là symlink tới tệp bí mật (container api
// cài vào run/) + lịch đêm đi nhánh bản bị chặn: bí mật không được lọt vào run/.
func TestSkipBlockedUpdate_SymlinkedStatus_DoesNotLeakSecret(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("symlink kiểu Unix")
	}
	dir := t.TempDir()
	if err := hostlink.EnsureDir(dir); err != nil {
		t.Fatal(err)
	}
	secret := filepath.Join(t.TempDir(), "id_ed25519")
	if err := os.WriteFile(secret, []byte(`{"state":"failed","message":"BI-MAT-KHOA-SSH"}`), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink(secret, filepath.Join(hostlink.Dir(dir), hostlink.StatusFile)); err != nil {
		t.Fatal(err)
	}
	// Đúng thứ tự runUpdate: chụp → Start → nhánh bị chặn.
	snap, had := hostlink.SnapshotStatus(dir)
	_ = hostlink.Start(dir, "v0.1.34")
	skipBlockedUpdate(&strings.Builder{}, dir, "v0.1.34", hostlink.UpdateBlocked{Version: "v0.1.34"}, snap, had, false)
	entries, _ := os.ReadDir(hostlink.Dir(dir))
	for _, e := range entries {
		if b, err := os.ReadFile(filepath.Join(hostlink.Dir(dir), e.Name())); err == nil && strings.Contains(string(b), "BI-MAT") {
			t.Fatalf("bí mật lọt vào run/%s", e.Name())
		}
	}
	if b, _ := os.ReadFile(secret); !strings.Contains(string(b), "BI-MAT") {
		t.Fatal("tệp bí mật bị ghi đè")
	}
}

// Review v0.1.34: lịch đêm nuốt yêu cầu "Cập nhật ngay" của Owner → tiến trình
// con (re-exec) phải nhận --if-requested để không bị chặn/không đợi chín.
func TestChildUpdateArgs_PassesConsumedRequest(t *testing.T) {
	args := []string{"--yes", "--quiet"}
	got := childUpdateArgs(args, false, true)
	if strings.Join(got, " ") != "--yes --quiet --if-requested" {
		t.Errorf("phải thêm --if-requested: %v", got)
	}
	if strings.Join(args, " ") != "--yes --quiet" {
		t.Errorf("không được sửa args gốc: %v", args)
	}
	if got := childUpdateArgs(args, false, false); strings.Join(got, " ") != "--yes --quiet" {
		t.Errorf("không có yêu cầu thì giữ nguyên: %v", got)
	}
	if got := childUpdateArgs([]string{"--if-requested"}, true, true); len(got) != 1 {
		t.Errorf("đã có --if-requested thì không thêm lần nữa: %v", got)
	}
	// Có yêu cầu (dù lịch đêm --yes) → không còn là "lịch đêm": bản bị chặn vẫn chạy.
	if skip, _ := decideServiceUpdate(serviceUpdateInput{Scheduled: false, Version: "v0.1.34", BlockedVersion: "v0.1.34"}); skip {
		t.Error("có yêu cầu thì không bị chặn")
	}
	parsed, err := parseUpdateFlags(got)
	if err != nil || !parsed.ifRequested || !parsed.yes {
		t.Errorf("args con phải parse được --yes --if-requested: %+v %v", parsed, err)
	}
}

// ─── v0.1.37: tín hiệu dừng + khoá loại trừ ─────────────────────────────────

// signalContext phải huỷ khi nhận SIGTERM (systemd tắt máy), không chỉ Ctrl-C.
func TestSignalContext_HuyKhiNhanSIGTERM(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("Windows không gửi được SIGTERM")
	}
	ctx, stop := signalContext()
	defer stop()
	p, err := os.FindProcess(os.Getpid())
	if err != nil {
		t.Fatal(err)
	}
	if err := p.Signal(syscall.SIGTERM); err != nil {
		t.Fatal(err)
	}
	select {
	case <-ctx.Done():
	case <-time.After(5 * time.Second):
		t.Fatal("signalContext không huỷ khi nhận SIGTERM")
	}
	if !errors.Is(context.Cause(ctx), ops.ErrShutdownSignal) {
		t.Errorf("SIGTERM phải để nguyên nhân ops.ErrShutdownSignal (rollback không khôi phục CSDL lúc máy tắt), được %v", context.Cause(ctx))
	}
}

func TestSignalCause_PhanBietSIGTERMVaCtrlC(t *testing.T) {
	if !errors.Is(signalCause(syscall.SIGTERM), ops.ErrShutdownSignal) || !errors.Is(signalCause(os.Interrupt), ops.ErrInterruptSignal) {
		t.Fatal("SIGTERM → ErrShutdownSignal, Ctrl-C → ErrInterruptSignal")
	}
	ctx, cancel := context.WithCancelCause(context.Background())
	cancel(ops.ErrInterruptSignal)
	if forwardSignal(ctx) != os.Interrupt {
		t.Error("Ctrl-C phải chuyển tiếp SIGINT cho con")
	}
	ctx2, cancel2 := context.WithCancelCause(context.Background())
	cancel2(ops.ErrShutdownSignal)
	if forwardSignal(ctx2) != syscall.SIGTERM {
		t.Error("SIGTERM phải chuyển tiếp SIGTERM cho con")
	}
}

// captureStd chạy f với os.Stdout/os.Stderr chuyển hướng, trả nội dung.
func captureStd(t *testing.T, f func()) (stdout, stderr string) {
	t.Helper()
	oldOut, oldErr := os.Stdout, os.Stderr
	ro, wo, _ := os.Pipe()
	re, we, _ := os.Pipe()
	os.Stdout, os.Stderr = wo, we
	outC, errC := make(chan string), make(chan string)
	go func() { b, _ := io.ReadAll(ro); outC <- string(b) }()
	go func() { b, _ := io.ReadAll(re); errC <- string(b) }()
	defer func() { os.Stdout, os.Stderr = oldOut, oldErr }()
	f()
	_ = wo.Close()
	_ = we.Close()
	return <-outC, <-errC
}

// lockTestInstall dựng gốc cài đặt tạm có compose.yaml GENH QUẢN LÝ (để không
// dò lên compose.yaml của repo), một yêu cầu "Cập nhật ngay" và update-status.json.
func lockTestInstall(t *testing.T) string {
	t.Helper()
	dir := t.TempDir()
	if err := os.MkdirAll(filepath.Join(dir, "deploy"), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(dir, "deploy", "compose.yaml"), []byte("name: gen-harness\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	t.Setenv("GENH_COMPOSE_FILE", filepath.Join(dir, "deploy", "compose.yaml"))
	if err := hostlink.EnsureDir(dir); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(hostlink.RequestPath(dir), []byte(`{"by":"owner"}`), 0o666); err != nil {
		t.Fatal(err)
	}
	if err := hostlink.Start(dir, "v0.1.35"); err != nil {
		t.Fatal(err)
	}
	if err := hostlink.Finish(dir, "done", "v0.1.36", ""); err != nil {
		t.Fatal(err)
	}
	return dir
}

func readFileStr(t *testing.T, path string) string {
	t.Helper()
	b, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("đọc %s: %v", path, err)
	}
	return string(b)
}

func TestRunUpdate_KhoaBan_LichDem_BoQuaThoat0_KhongDungHopThu(t *testing.T) {
	dir := lockTestInstall(t)
	held, err := hostlink.AcquireLock(dir)
	if err != nil {
		t.Fatal(err)
	}
	defer held.Release()
	stopBeat := hostlink.StartHeartbeat(dir, "update") // "lần khác" đang chạy
	defer stopBeat()
	statusBefore := readFileStr(t, filepath.Join(hostlink.Dir(dir), hostlink.StatusFile))
	reqBefore := readFileStr(t, hostlink.RequestPath(dir))

	var code int
	out, _ := captureStd(t, func() {
		code = runUpdate([]string{"--yes", "--quiet", "--no-self-update", "--install-dir", dir})
	})
	if code != 0 {
		t.Fatalf("lịch đêm gặp khoá bận phải thoát 0, được %d", code)
	}
	want := fmt.Sprintf("genh: đang có một lần cập nhật/khôi phục khác chạy (PID %d) — lần này bỏ qua.", os.Getpid())
	if !strings.Contains(out, want) {
		t.Fatalf("stdout (cả khi --quiet) phải có %q, được %q", want, out)
	}
	if got := readFileStr(t, filepath.Join(hostlink.Dir(dir), hostlink.StatusFile)); got != statusBefore {
		t.Errorf("update-status.json bị đổi:\n%s\n---\n%s", statusBefore, got)
	}
	if got := readFileStr(t, hostlink.RequestPath(dir)); got != reqBefore {
		t.Errorf("request/update.json bị đổi/xoá")
	}
}

func TestRunUpdate_KhoaBan_GoTay_Thoat1_GHE94A(t *testing.T) {
	dir := lockTestInstall(t)
	held, err := hostlink.AcquireLock(dir)
	if err != nil {
		t.Fatal(err)
	}
	defer held.Release()
	statusBefore := readFileStr(t, filepath.Join(hostlink.Dir(dir), hostlink.StatusFile))

	var code int
	_, errOut := captureStd(t, func() {
		code = runUpdate([]string{"--no-self-update", "--install-dir", dir})
	})
	if code != 1 {
		t.Fatalf("gõ tay gặp khoá bận phải thoát 1, được %d", code)
	}
	for _, want := range []string{"đang có một lần cập nhật/khôi phục khác chạy", ops.ErrCodeUpdateLocked, "rồi chạy lại"} {
		if !strings.Contains(errOut, want) {
			t.Errorf("stderr thiếu %q: %q", want, errOut)
		}
	}
	if got := readFileStr(t, filepath.Join(hostlink.Dir(dir), hostlink.StatusFile)); got != statusBefore {
		t.Error("gõ tay gặp khoá bận không được đụng hộp thư")
	}
	if !hostlink.HasRequest(dir) {
		t.Error("không được nuốt yêu cầu của Console")
	}
}

// Nút Console (--if-requested): chờ lần khác nhả khoá rồi chạy (nuốt yêu cầu,
// báo running → kết quả).
func TestRunUpdate_IfRequested_ChoKhoaNhaRoiChay(t *testing.T) {
	old := requestLockWait
	requestLockWait = 20 * time.Second
	defer func() { requestLockWait = old }()
	dir := lockTestInstall(t)
	held, err := hostlink.AcquireLock(dir)
	if err != nil {
		t.Fatal(err)
	}
	released := make(chan time.Time, 1)
	go func() {
		time.Sleep(300 * time.Millisecond)
		released <- time.Now()
		held.Release()
	}()

	var code int
	out, _ := captureStd(t, func() {
		code = runUpdate([]string{"--yes", "--if-requested", "--no-self-update", "--install-dir", dir})
	})
	releasedAt := <-released
	if time.Now().Before(releasedAt) {
		t.Fatal("không được chạy trước khi khoá nhả")
	}
	if strings.Contains(out, "lần này bỏ qua") {
		t.Fatalf("--if-requested phải CHỜ, không bỏ qua: %q", out)
	}
	if hostlink.HasRequest(dir) {
		t.Error("chờ được khoá rồi thì phải xử lý (nuốt) yêu cầu")
	}
	st, err := hostlink.ReadStatus(dir)
	if err != nil || st.State == "done" && st.To == "v0.1.36" {
		t.Fatalf("phải đã chạy (ghi trạng thái mới): %+v %v (code=%d)", st, err, code)
	}
	if st.PID != os.Getpid() {
		t.Errorf("update-status.json phải ghi PID tiến trình ngoài cùng: %+v", st)
	}
	if _, err := os.Stat(hostlink.HeartbeatPath(dir)); !os.IsNotExist(err) {
		t.Errorf("xong thì phải xoá genh-heartbeat.json: %v", err)
	}
	// Khoá đã được nhả lại (cả khoá người chờ).
	l, err := hostlink.AcquireLock(dir)
	if err != nil {
		t.Fatalf("runUpdate xong phải nhả khoá: %v", err)
	}
	l.Release()
	w, err := hostlink.AcquireWaitLock(dir)
	if err != nil {
		t.Fatalf("chờ xong phải nhả khoá người chờ: %v", err)
	}
	w.Release()
}

// Khoá chính bận VÀ đã có một người chờ (watcher crontab kích mỗi phút): lần
// này thoát NGAY (0), không chờ, không nuốt yêu cầu — không chồng ~30 genh chờ.
func TestRunUpdate_IfRequested_DaCoNguoiCho_ThoatNgay(t *testing.T) {
	dir := lockTestInstall(t)
	held, err := hostlink.AcquireLock(dir)
	if err != nil {
		t.Fatal(err)
	}
	defer held.Release()
	waiter, err := hostlink.AcquireWaitLock(dir)
	if err != nil {
		t.Fatal(err)
	}
	defer waiter.Release()

	start := time.Now()
	var code int
	out, _ := captureStd(t, func() {
		code = runUpdate([]string{"--yes", "--if-requested", "--no-self-update", "--install-dir", dir})
	})
	if code != 0 || time.Since(start) > 5*time.Second {
		t.Fatalf("phải thoát 0 ngay, được code=%d sau %v", code, time.Since(start))
	}
	if !strings.Contains(out, "đã có một tiến trình khác đang chờ") {
		t.Errorf("thiếu dòng log người chờ: %q", out)
	}
	if !hostlink.HasRequest(dir) {
		t.Error("không được nuốt yêu cầu — người chờ kia sẽ làm")
	}
}

// Chờ được khoá mà yêu cầu đã bị lần trước nuốt → thoát 0, không làm gì.
func TestAcquireOpLock_IfRequested_YeuCauDaBiNuot(t *testing.T) {
	dir := t.TempDir()
	var out, errOut strings.Builder
	l, code, ok := acquireOpLock(context.Background(), dir, lockRequested, func() bool { return false }, &out, &errOut)
	if ok || code != 0 || l != nil {
		t.Fatalf("yêu cầu đã hết: ok=%v code=%d", ok, code)
	}
	l2, err := hostlink.AcquireLock(dir)
	if err != nil {
		t.Fatalf("phải nhả khoá khi không làm gì: %v", err)
	}
	l2.Release()
}

// Tiến trình con --self-updated KHÔNG lấy khoá (cha đang giữ).
func TestRunUpdate_SelfUpdated_KhongLayKhoa(t *testing.T) {
	dir := lockTestInstall(t)
	held, err := hostlink.AcquireLock(dir)
	if err != nil {
		t.Fatal(err)
	}
	defer held.Release()
	_ = hostlink.Start(dir, "v0.1.36") // tiến trình cha đã báo running

	out, errOut := captureStd(t, func() {
		_ = runUpdate([]string{"--yes", "--quiet", "--self-updated", "--install-dir", dir})
	})
	if strings.Contains(out+errOut, "đang có một lần cập nhật/khôi phục khác chạy") {
		t.Fatalf("--self-updated không được kiểm khoá: %q %q", out, errOut)
	}
	st, _ := hostlink.ReadStatus(dir)
	if st.State == "running" {
		t.Fatalf("tiến trình con phải chạy tiếp và ghi kết quả: %+v", st)
	}
	if !hostlink.HasRequest(dir) {
		t.Error("tiến trình con không nuốt hộp thư (việc của tiến trình ngoài)")
	}
}

// restore gõ tay / import khi khoá bận: thoát 1, GH-E94A.
func TestRunRestoreImport_KhoaBan_GoTay_Thoat1(t *testing.T) {
	dir := lockTestInstall(t)
	held, err := hostlink.AcquireLock(dir)
	if err != nil {
		t.Fatal(err)
	}
	defer held.Release()
	for name, f := range map[string]func() int{
		"restore": func() int { return runRestore([]string{"--install-dir", dir, "backups/k.enc"}) },
		"import": func() int {
			return runImport([]string{"--install-dir", dir, "--yes", filepath.Join(dir, "x.ghbundle")})
		},
	} {
		var code int
		_, errOut := captureStd(t, func() { code = f() })
		if code != 1 || !strings.Contains(errOut, ops.ErrCodeUpdateLocked) || !strings.Contains(errOut, "đang có một lần cập nhật/khôi phục khác chạy") {
			t.Errorf("%s: muốn thoát 1 + GH-E94A, được %d %q", name, code, errOut)
		}
	}
}

// Tín hiệu dừng tới TRƯỚC khi chạy con: không chạy con (con chưa kịp bắt tín
// hiệu sẽ bị giết ngay, không ghi kết quả).
func TestRunChildForwardingSignal_DaHuyTruoc_KhongChayCon(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	marker := filepath.Join(t.TempDir(), "ran")
	child := exec.Command("sh", "-c", "touch "+marker)
	if err := runChildForwardingSignal(ctx, child); !errors.Is(err, errStoppedBeforeReExec) {
		t.Fatalf("muốn errStoppedBeforeReExec, được %v", err)
	}
	if child.Process != nil {
		t.Error("không được Start tiến trình con")
	}
	if _, err := os.Stat(marker); !os.IsNotExist(err) {
		t.Error("tiến trình con đã chạy")
	}
}

// Đang chờ tiến trình con (sau tự cập nhật) mà nhận tín hiệu dừng: chuyển tiếp
// SIGTERM cho con và CHỜ con tự kết thúc (con quay về bản cũ) — không Kill.
func TestRunChildForwardingSignal_ChuyenTiepSIGTERM_VanCho(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("Windows không chuyển tiếp được SIGTERM")
	}
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	child := exec.Command("sh", "-c", "trap 'sleep 0.3; exit 7' TERM; while :; do sleep 0.05; done")
	go func() { time.Sleep(200 * time.Millisecond); cancel() }()
	err := runChildForwardingSignal(ctx, child)
	var exitErr *exec.ExitError
	if !errors.As(err, &exitErr) || exitErr.ExitCode() != 7 {
		t.Fatalf("con phải nhận SIGTERM, tự dọn rồi thoát 7 (không bị Kill), được %v", err)
	}
}

// v0.1.40: cờ `genh offsite set|run|status|disable` — mọi cờ đứng trước đối số vị trí.
func TestParseOffsiteFlags(t *testing.T) {
	f, err := parseOffsiteFlags([]string{"set", "--allow-same-disk", "--no-run", "--install-dir", "/i", "--port", "9443", "/media/usb"})
	if err != nil || f.sub != "set" || !f.allowSameDisk || !f.noRun || f.installDir != "/i" || f.port != 9443 || f.path != "/media/usb" {
		t.Fatalf("set = %+v, %v", f, err)
	}
	if f, err := parseOffsiteFlags([]string{"set", "/media/usb"}); err != nil || f.allowSameDisk || f.noRun || f.path != "/media/usb" {
		t.Fatalf("set mặc định = %+v, %v", f, err)
	}
	// Cờ đặt SAU đường dẫn không được tính (package flag dừng ở đối số đầu) → lỗi cách dùng.
	if _, err := parseOffsiteFlags([]string{"set", "/media/usb", "--allow-same-disk"}); err == nil {
		t.Fatal("cờ sau đường dẫn phải lỗi cách dùng")
	}
	if _, err := parseOffsiteFlags([]string{"set"}); err == nil {
		t.Fatal("set thiếu đường dẫn phải lỗi")
	}
	f, err = parseOffsiteFlags([]string{"run", "--quiet", "--install-dir", "/i"})
	if err != nil || f.sub != "run" || !f.quiet || f.ifRequested {
		t.Fatalf("run = %+v, %v", f, err)
	}
	// run không có --allow-same-disk (chỉ set từ CLI mới có).
	if _, err := parseOffsiteFlags([]string{"run", "--allow-same-disk"}); err == nil {
		t.Fatal("run không nhận --allow-same-disk")
	}
	for _, sub := range []string{"status", "disable"} {
		if f, err := parseOffsiteFlags([]string{sub, "--install-dir", "/i"}); err != nil || f.sub != sub {
			t.Fatalf("%s = %+v, %v", sub, f, err)
		}
		if _, err := parseOffsiteFlags([]string{sub, "thua"}); err == nil {
			t.Fatalf("%s không nhận đối số vị trí", sub)
		}
	}
	if _, err := parseOffsiteFlags([]string{"xoa"}); err == nil {
		t.Fatal("lệnh con lạ phải lỗi")
	}
	if _, err := parseOffsiteFlags(nil); err == nil {
		t.Fatal("thiếu lệnh con phải lỗi")
	}
	// Lịch tuần (dòng do internal/autoupdate sinh) parse được bằng đúng bộ cờ này.
	line := autoupdate.OffsiteCrontabLine("/g/genh", "/g/log", autoupdate.OffsiteJob{InstallDir: "/i", Port: 9443}, 0)
	fields := strings.Fields(line)
	var cmdArgs []string
	for i, x := range fields {
		if x == "offsite" {
			cmdArgs = fields[i+1:]
			break
		}
	}
	for i, x := range cmdArgs {
		if x == ">>" {
			cmdArgs = cmdArgs[:i]
			break
		}
	}
	if f, err := parseOffsiteFlags(cmdArgs); err != nil || f.sub != "run" || !f.quiet || f.installDir != "/i" || f.port != 9443 {
		t.Fatalf("cờ lịch tuần %v → %+v, %v", cmdArgs, f, err)
	}
}

// Khoá bận: lịch tuần (--quiet) / watcher (--if-requested) thoát 0; gõ tay thoát 1;
// chưa thấy ổ USB/NAS luôn thoát 1.
func TestOffsiteExitCode(t *testing.T) {
	busy := &ops.OpError{Code: ops.ErrCodeOffsiteBusy}
	if offsiteExitCode(busy, offsiteFlags{quiet: true}) != 0 || offsiteExitCode(busy, offsiteFlags{ifRequested: true}) != 0 {
		t.Fatal("lịch/watcher gặp khoá bận phải thoát 0")
	}
	if offsiteExitCode(busy, offsiteFlags{}) != 1 {
		t.Fatal("gõ tay gặp khoá bận thoát 1")
	}
	if offsiteExitCode(&ops.OpError{Code: ops.ErrCodeOffsiteNotMounted}, offsiteFlags{quiet: true}) != 1 {
		t.Fatal("chưa thấy ổ USB/NAS phải thoát 1 kể cả lịch")
	}
	if offsiteExitCode(nil, offsiteFlags{}) != 0 {
		t.Fatal("không lỗi thoát 0")
	}
}

// handle-requests: run/request/offsite.json → chuyển sang `genh offsite run --if-requested`
// (xoá tệp yêu cầu trước khi làm); update/restore vẫn được ưu tiên trước.
func TestHandleRequests_ChuyenSangOffsite(t *testing.T) {
	f, err := parseOffsiteFlags(append(handleRequestOffsiteArgs(true), "--port", "8443", "--install-dir", "/i"))
	if err != nil || f.sub != "run" || !f.ifRequested || !f.quiet || f.installDir != "/i" {
		t.Fatalf("cờ handle-requests → offsite = %+v, %v", f, err)
	}

	dir := t.TempDir()
	if err := os.MkdirAll(filepath.Join(dir, "deploy"), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(dir, "deploy", "compose.yaml"), []byte("name: gen-harness\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	t.Setenv("GENH_COMPOSE_FILE", filepath.Join(dir, "deploy", "compose.yaml"))
	if err := hostlink.EnsureDir(dir); err != nil {
		t.Fatal(err)
	}
	// Chưa cấu hình bản sao ngoài máy + Console bấm "Sao lưu ra ổ ngoài ngay".
	if err := os.WriteFile(hostlink.OffsiteRequestPath(dir), []byte(`{"action":"run"}`), 0o666); err != nil {
		t.Fatal(err)
	}
	var code int
	captureStd(t, func() { code = runHandleRequests([]string{"--quiet", "--install-dir", dir}) })
	if code != 0 {
		t.Fatalf("thoát %d, muốn 0", code)
	}
	if hostlink.HasOffsiteRequest(dir) {
		t.Fatal("tệp yêu cầu offsite phải bị xoá")
	}
	st, err := hostlink.ReadOffsiteStatus(dir)
	if err != nil || st.State != hostlink.OffsiteStateNotConfigured {
		t.Fatalf("offsite-status = %+v, %v", st, err)
	}
}

// Tín hiệu dừng tới ngay sau lúc tải genh mới (chưa chạy con): Console phải
// nhận GH-E94B "chưa đụng gì" — không phải thông điệp không mã (rơi vào thẻ đỏ
// "đã tự quay về").
func TestChildFailedMessage_DungTruocCon_GHE94BChuaDungGi(t *testing.T) {
	msg := childFailedMessage(true, ops.ErrShutdownSignal)
	if !strings.Contains(msg, "("+ops.ErrCodeUpdateInterrupted+")") || !strings.Contains(msg, "chưa đụng gì") {
		t.Errorf("muốn GH-E94B \"chưa đụng gì\", được %q", msg)
	}
	if strings.Contains(msg, "đã tự quay về") || strings.Contains(msg, "`") {
		t.Errorf("không nói \"đã tự quay về\", không có dấu `: %q", msg)
	}
	if m := childFailedMessage(true, nil); !strings.Contains(m, ops.ErrCodeUpdateInterrupted) {
		t.Errorf("cause nil vẫn phải có GH-E94B: %q", m)
	}
	if m := childFailedMessage(false, nil); strings.Contains(m, "GH-E") {
		t.Errorf("con chết giữa chừng (không do dừng trước khi chạy) giữ thông điệp cũ: %q", m)
	}
}

// v0.1.44: cờ của `genh doctor` — --notify/--quiet/--test (trực canh) và --if-requested (gói chẩn đoán).
func TestParseDoctorFlags(t *testing.T) {
	f, err := parseDoctorFlags([]string{"--notify", "--quiet", "--install-dir", "/i", "--port", "9443"})
	if err != nil || !f.notify || !f.quiet || f.test || f.ifRequested || f.installDir != "/i" || f.port != 9443 {
		t.Fatalf("--notify --quiet = %+v, %v", f, err)
	}
	f, err = parseDoctorFlags([]string{"--out", "x.zip"})
	if err != nil || f.notify || f.out != "x.zip" {
		t.Fatalf("--out = %+v, %v", f, err)
	}
	for _, bad := range [][]string{{"--test"}, {"--notify", "--if-requested"}, {"thừa"}, {"--lạ"}} {
		if _, err := parseDoctorFlags(bad); err == nil {
			t.Errorf("%v phải lỗi", bad)
		}
	}
	// Đúng cờ lịch 12 phút truyền (internal/autoupdate) parse được bằng bộ cờ của doctor.
	line := autoupdate.WatchdogCrontabLine("/g/genh", "/g/log", autoupdate.WatchdogJob{InstallDir: "/i", Port: 9443})
	fields := strings.Fields(line)
	var args []string
	for i, fd := range fields {
		if fd == "/g/genh" {
			for _, a := range fields[i+1:] {
				if a == ">>" {
					break
				}
				args = append(args, a)
			}
			break
		}
	}
	if len(args) == 0 || args[0] != "doctor" {
		t.Fatalf("dòng cron không gọi doctor: %q", line)
	}
	f, err = parseDoctorFlags(args[1:])
	if err != nil || !f.notify || !f.quiet || f.installDir != "/i" || f.port != 9443 {
		t.Fatalf("cờ lịch trực canh = %+v, %v", f, err)
	}
}

// handle-requests: doctor.json → `doctor --if-requested`; watchdog.json → xoá yêu cầu
// rồi `doctor --notify --test --quiet`; yêu cầu gửi thử hỏng ⇒ bỏ, thoát 0.
func TestHandleRequests_DoctorVaWatchdog(t *testing.T) {
	f, err := parseDoctorFlags(append(handleRequestDoctorArgs(), "--port", "8443", "--install-dir", "/i"))
	if err != nil || !f.ifRequested || f.notify {
		t.Fatalf("handle-requests → doctor = %+v, %v", f, err)
	}
	f, err = parseDoctorFlags(append(handleRequestWatchdogArgs(), "--port", "8443", "--install-dir", "/i"))
	if err != nil || !f.notify || !f.test || !f.quiet {
		t.Fatalf("handle-requests → watchdog = %+v, %v", f, err)
	}

	dir := t.TempDir()
	if err := os.MkdirAll(filepath.Join(dir, "deploy"), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(dir, "deploy", "compose.yaml"), []byte("name: gen-harness\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	t.Setenv("GENH_COMPOSE_FILE", filepath.Join(dir, "deploy", "compose.yaml"))
	if err := hostlink.EnsureDir(dir); err != nil {
		t.Fatal(err)
	}
	// Yêu cầu hỏng: bỏ, xoá tệp, thoát 0.
	_ = os.WriteFile(hostlink.WatchdogRequestPath(dir), []byte(`{"action":"xoá hết"}`), 0o666)
	var code int
	captureStd(t, func() { code = runHandleRequests([]string{"--quiet", "--install-dir", dir}) })
	if code != 0 || hostlink.HasWatchdogRequest(dir) {
		t.Fatalf("yêu cầu hỏng: thoát %d, còn tệp %v", code, hostlink.HasWatchdogRequest(dir))
	}
	// Yêu cầu đúng: xoá tệp rồi chạy trực canh (bản cài chưa có bí mật ⇒ lỗi cấu
	// hình, state=error — KHÔNG gọi docker).
	_ = os.WriteFile(hostlink.WatchdogRequestPath(dir), []byte(`{"schema":1,"action":"test"}`), 0o666)
	captureStd(t, func() { code = runHandleRequests([]string{"--quiet", "--install-dir", dir}) })
	if hostlink.HasWatchdogRequest(dir) {
		t.Fatal("phải xoá run/request/watchdog.json trước khi làm")
	}
	st, err := hostlink.ReadWatchdogStatus(dir)
	if err != nil || st.State != hostlink.WatchdogStateError || code != 1 {
		t.Fatalf("chưa cài: muốn state=error, thoát 1 — được %+v, %v, %d", st, err, code)
	}
	// Không có yêu cầu doctor ⇒ `doctor --if-requested` thoát 0 ngay.
	captureStd(t, func() { code = runDoctor([]string{"--if-requested", "--install-dir", dir}) })
	if code != 0 {
		t.Fatalf("không có yêu cầu thì thoát 0, được %d", code)
	}
}

func TestWatchdogStatusText(t *testing.T) {
	txt := watchdogStatusText(autoupdate.WatchdogSchedule{Enabled: true, Mechanism: "cron", Detail: "crontab"},
		hostlink.WatchdogStatus{LastRunAt: "2026-10-03T10:00:00Z", State: "issues", Telegram: "failed", TelegramErrorCode: "TELEGRAM_BOT_BLOCKED",
			Incidents: []hostlink.WatchdogIncident{{Key: "api.down", Title: "Máy chủ ứng dụng (api) không chạy", Since: "2026-10-03T09:48:00Z"}}}, nil, false)
	for _, want := range []string{"Trực canh máy chủ: BẬT (cron)", "Lần chạy gần nhất: 2026-10-03T10:00:00Z (issues)", "TELEGRAM_BOT_BLOCKED", "Sự cố đang mở (1):", "api.down"} {
		if !strings.Contains(txt, want) {
			t.Errorf("thiếu %q:\n%s", want, txt)
		}
	}
	if txt := watchdogStatusText(autoupdate.WatchdogSchedule{Detail: "chưa bật"}, hostlink.WatchdogStatus{}, os.ErrNotExist, false); !strings.Contains(txt, "TẮT") || !strings.Contains(txt, "chưa có") || strings.Contains(txt, "Owner đã tắt") {
		t.Errorf("%s", txt)
	}
	if txt := watchdogStatusText(autoupdate.WatchdogSchedule{Detail: "chưa bật"}, hostlink.WatchdogStatus{}, os.ErrNotExist, true); !strings.Contains(txt, "Owner đã tắt") || !strings.Contains(txt, "genh watchdog enable") {
		t.Errorf("Owner tắt thì status phải nói rõ:\n%s", txt)
	}
}

// Owner `genh watchdog disable` ⇒ install/update (kể cả lịch đêm) không bật lại.
func TestEnableWatchdogSchedule_TonTrongOwnerTat(t *testing.T) {
	dir := t.TempDir()
	if err := ops.SetWatchdogOptOut(dir, true, time.Now()); err != nil {
		t.Fatal(err)
	}
	out, errOut := captureStd(t, func() { enableWatchdogSchedule(context.Background(), dir, 0) })
	if out != "" || errOut != "" {
		t.Fatalf("Owner đã tắt thì không bật/không in gì, được %q %q", out, errOut)
	}
	if _, err := hostlink.ReadWatchdogStatus(dir); err == nil {
		t.Fatal("không được ghi lịch trực canh khi Owner đã tắt")
	}
	if err := ops.SetWatchdogOptOut(dir, false, time.Now()); err != nil || ops.WatchdogOptedOut(dir) {
		t.Fatalf("enable phải xoá đánh dấu: %v", err)
	}
	if err := ops.SetWatchdogOptOut(dir, false, time.Now()); err != nil {
		t.Fatalf("xoá khi không có tệp không phải lỗi: %v", err)
	}
}
