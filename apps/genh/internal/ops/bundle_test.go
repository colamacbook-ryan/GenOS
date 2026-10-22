package ops

import (
	"context"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/dockercli"
	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/dockercli/fake"
)

// withBundlePassword đặt GH_BUNDLE_PASSWORD cho thời gian chạy test rồi tự
// gỡ khi xong — dùng để bỏ qua hỏi mật khẩu ẩn (không có TTY thật trong test).
func withBundlePassword(t *testing.T, v string) {
	t.Helper()
	t.Setenv(bundlePasswordEnv, v)
}

// withSecretLines tiêm readSecretLine giả cho một test cần mô phỏng gõ tay
// (mật khẩu ngắn/không khớp) — khôi phục biến gói khi test xong.
func withSecretLines(t *testing.T, lines ...string) {
	t.Helper()
	orig := readSecretLine
	i := 0
	readSecretLine = func() (string, error) {
		if i >= len(lines) {
			t.Fatalf("readSecretLine gọi nhiều hơn %d lần đã chuẩn bị", len(lines))
		}
		v := lines[i]
		i++
		return v, nil
	}
	t.Cleanup(func() { readSecretLine = orig })
}

func TestRunExport_HappyPath_StreamsStdoutToFile(t *testing.T) {
	withBundlePassword(t, "mat-khau-du-dai-123")
	composePath := testComposePath(t, "")
	env := testEnv(t, composePath)
	toPath := filepath.Join(t.TempDir(), "out.ghbundle")

	fr := &fake.Runner{Responses: []fake.Response{
		{Match: fake.MatchArgsContain("exec", "-T", "-e", bundlePasswordEnv, "api", "python", "-m", "gh.bundle", "export", "--out", "-"),
			RunIOStdout: []byte(bundleMagic + "fake-bytes")},
	}}

	var out strings.Builder
	if err := RunExport(context.Background(), env, toPath, ExportDeps{Runner: fr}, &out); err != nil {
		t.Fatalf("RunExport: %v", err)
	}
	data, err := os.ReadFile(toPath)
	if err != nil {
		t.Fatalf("đọc lại %s: %v", toPath, err)
	}
	if string(data) != bundleMagic+"fake-bytes" {
		t.Errorf("nội dung tệp = %q, muốn %q", data, bundleMagic+"fake-bytes")
	}
	// Quyền 0600 của tệp xuất: kiểm ở bundle_unix_test.go (bit quyền POSIX).
	// Mật khẩu KHÔNG được xuất hiện trong argv của lệnh đã gọi.
	for _, c := range fr.Calls {
		for _, a := range c.Cmd.Args {
			if strings.Contains(a, "mat-khau-du-dai-123") {
				t.Errorf("mật khẩu lộ vào argv: %v", c.Cmd.Args)
			}
		}
	}
	// Tệp tạm không còn sót lại.
	if _, err := os.Stat(toPath + ".tmp-genh-export"); !os.IsNotExist(err) {
		t.Errorf("tệp tạm phải bị xoá/rename, os.Stat trả err=%v", err)
	}
}

func TestRunExport_PasswordTooShort_ReturnsOpError(t *testing.T) {
	composePath := testComposePath(t, "")
	env := testEnv(t, composePath)
	withSecretLines(t, "curt") // < 12 ký tự, chỉ 1 lần đọc (thất bại trước khi hỏi lại)

	err := RunExport(context.Background(), env, filepath.Join(t.TempDir(), "out.ghbundle"), ExportDeps{Runner: &fake.Runner{}}, &strings.Builder{})
	opErr, ok := err.(*OpError)
	if !ok {
		t.Fatalf("lỗi phải là *OpError, được %T", err)
	}
	if opErr.Code != ErrCodeExportPasswordMismatch {
		t.Errorf("Code = %q, muốn %q", opErr.Code, ErrCodeExportPasswordMismatch)
	}
}

func TestRunExport_PasswordMismatch_ReturnsOpError(t *testing.T) {
	composePath := testComposePath(t, "")
	env := testEnv(t, composePath)
	withSecretLines(t, "mat-khau-du-dai-123", "mat-khau-khac-han-456")

	err := RunExport(context.Background(), env, filepath.Join(t.TempDir(), "out.ghbundle"), ExportDeps{Runner: &fake.Runner{}}, &strings.Builder{})
	opErr, ok := err.(*OpError)
	if !ok {
		t.Fatalf("lỗi phải là *OpError, được %T", err)
	}
	if opErr.Code != ErrCodeExportPasswordMismatch {
		t.Errorf("Code = %q, muốn %q", opErr.Code, ErrCodeExportPasswordMismatch)
	}
}

func TestRunExport_ContainerCommandFails_RemovesTmpFile(t *testing.T) {
	withBundlePassword(t, "mat-khau-du-dai-123")
	composePath := testComposePath(t, "")
	env := testEnv(t, composePath)
	toPath := filepath.Join(t.TempDir(), "out.ghbundle")

	fr := &fake.Runner{Responses: []fake.Response{
		{Match: fake.MatchArgsContain("gh.bundle", "export"), Err: errors.New("db down")},
	}}

	err := RunExport(context.Background(), env, toPath, ExportDeps{Runner: fr}, &strings.Builder{})
	opErr, ok := err.(*OpError)
	if !ok {
		t.Fatalf("lỗi phải là *OpError, được %T", err)
	}
	if opErr.Code != ErrCodeExportFailed {
		t.Errorf("Code = %q, muốn %q", opErr.Code, ErrCodeExportFailed)
	}
	if _, statErr := os.Stat(toPath); !os.IsNotExist(statErr) {
		t.Errorf("toPath không được tồn tại khi export lỗi")
	}
	if _, statErr := os.Stat(toPath + ".tmp-genh-export"); !os.IsNotExist(statErr) {
		t.Errorf("tệp tạm phải bị xoá khi export lỗi")
	}
}

// bundleFile dựng một tệp .ghbundle giả hợp lệ (đúng magic) dưới t.TempDir().
func bundleFile(t *testing.T, body string) string {
	t.Helper()
	path := filepath.Join(t.TempDir(), "in.ghbundle")
	if err := os.WriteFile(path, []byte(bundleMagic+body), 0o600); err != nil {
		t.Fatalf("WriteFile: %v", err)
	}
	return path
}

const importTestBackupLine = "INFO:gh.backup:Backup mới: backups/20260925T110000Z-c0ffee12.pgcustom.enc (321 byte, CSDL gen_harness)"

// importHappyFakeRunner dựng fake.Runner khớp đúng luồng MỚI của `genh
// import` (mục #4/#5 v0.1.2 — xem doc-comment RunImport ở bundle.go):
// backup -> chép ra host -> stop api/worker -> run import -> run migrate ->
// up -d.
func importHappyFakeRunner() *fake.Runner {
	return &fake.Runner{Responses: []fake.Response{
		{Match: fake.MatchArgsContain("exec", "-T", "api", "python", "-m", "gh.backup", "run"), Lines: []string{importTestBackupLine}},
		{Match: fake.MatchArgsContain("exec", "-T", "api", "python", "-c"), Output: []byte("bytes-ma-hoa-gia")},
		{Match: fake.MatchArgsContain("stop", "api", "worker"), Output: []byte("")},
		{Match: fake.MatchArgsContain("gh.bundle", "import"), ExitCode: 0},
		{Match: fake.MatchArgsContain("run", "--rm", "--no-deps", "-T", "migrate"), Lines: []string{}},
		{Match: fake.MatchArgsContain("up", "-d"), Output: []byte("")},
	}}
}

func TestRunImport_HappyPath_BacksUpThenImportsThenRestarts(t *testing.T) {
	withBundlePassword(t, "mat-khau-du-dai-123")
	composePath := testComposePath(t, "")
	env := testEnv(t, composePath)
	bundlePath := bundleFile(t, "noi-dung-goi")

	_, port := listenReadyServer(t, true)
	env.Port = port

	fr := importHappyFakeRunner()

	var out strings.Builder
	opts := ImportOptions{AutoApprove: true}
	deps := ImportDeps{Runner: fr, Timeout: 200 * time.Millisecond, PollEvery: 5 * time.Millisecond}
	if err := RunImport(context.Background(), env, bundlePath, opts, deps, strings.NewReader(""), &out); err != nil {
		t.Fatalf("RunImport: %v", err)
	}
	if !strings.Contains(out.String(), "backups/20260925T110000Z-c0ffee12.pgcustom.enc") {
		t.Errorf("output phải nêu khoá backup an toàn, được %q", out.String())
	}
	if !strings.Contains(out.String(), "đã chép ra host") {
		t.Errorf("output phải xác nhận đã chép backup an toàn ra host, được %q", out.String())
	}

	// Bản backup an toàn phải thật sự nằm trên host (installDir/data/...).
	safetyFiles, _ := filepath.Glob(filepath.Join(env.InstallDir, "data", "import-safety-*"))
	if len(safetyFiles) != 1 {
		t.Fatalf("muốn đúng 1 tệp import-safety-* dưới installDir/data, được %v", safetyFiles)
	}
	if data, err := os.ReadFile(safetyFiles[0]); err != nil || string(data) != "bytes-ma-hoa-gia" {
		t.Errorf("nội dung %s = %q, %v — muốn \"bytes-ma-hoa-gia\"", safetyFiles[0], data, err)
	}

	// Stdin của lệnh import phải đúng bytes tệp gói (đọc từ đầu, kể cả magic).
	var sawStop, sawMigrate, sawUp bool
	stopIdx, importIdx, migrateIdx, upIdx := -1, -1, -1, -1
	for i, c := range fr.Calls {
		if fake.MatchArgsContain("gh.bundle", "import")(c.Cmd) {
			want := bundleMagic + "noi-dung-goi"
			if string(c.Stdin) != want {
				t.Errorf("stdin lệnh import = %q, muốn %q", c.Stdin, want)
			}
			importIdx = i
		}
		if hasExactArgs(c.Cmd.Args, "stop", "api", "worker") {
			sawStop = true
			stopIdx = i
		}
		if fake.MatchArgsContain("run", "--rm", "--no-deps", "-T", "migrate")(c.Cmd) {
			sawMigrate = true
			migrateIdx = i
		}
		if hasExactArgs(c.Cmd.Args, "up", "-d") {
			sawUp = true
			upIdx = i
		}
	}
	if !sawStop {
		t.Error("phải gọi `docker compose stop api worker` trước khi import (tránh api/worker vừa sống vừa bị pg_restore --clean xoá bảng)")
	}
	if !sawMigrate {
		t.Error("phải chạy lại `alembic upgrade heads` (docker compose run migrate) NGAY sau import")
	}
	if !sawUp {
		t.Error("phải `docker compose up -d` sau khi import + migrate")
	}
	if !(stopIdx < importIdx && importIdx < migrateIdx && migrateIdx < upIdx) {
		t.Errorf("thứ tự lệnh sai: stop=%d import=%d migrate=%d up=%d, muốn tăng dần", stopIdx, importIdx, migrateIdx, upIdx)
	}
}

func TestRunImport_Cancelled_WithoutYes(t *testing.T) {
	composePath := testComposePath(t, "")
	env := testEnv(t, composePath)
	bundlePath := bundleFile(t, "x")

	err := RunImport(context.Background(), env, bundlePath, ImportOptions{}, ImportDeps{Runner: &fake.Runner{}}, strings.NewReader("n\n"), &strings.Builder{})
	opErr, ok := err.(*OpError)
	if !ok {
		t.Fatalf("lỗi phải là *OpError, được %T", err)
	}
	if opErr.Code != ErrCodeImportCancelled {
		t.Errorf("Code = %q, muốn %q", opErr.Code, ErrCodeImportCancelled)
	}
}

func TestRunImport_NotABundle_ReturnsOpError(t *testing.T) {
	composePath := testComposePath(t, "")
	env := testEnv(t, composePath)

	notBundle := filepath.Join(t.TempDir(), "random.txt")
	if err := os.WriteFile(notBundle, []byte("khong-phai-goi"), 0o600); err != nil {
		t.Fatalf("WriteFile: %v", err)
	}

	err := RunImport(context.Background(), env, notBundle, ImportOptions{AutoApprove: true}, ImportDeps{Runner: &fake.Runner{}}, strings.NewReader(""), &strings.Builder{})
	opErr, ok := err.(*OpError)
	if !ok {
		t.Fatalf("lỗi phải là *OpError, được %T", err)
	}
	if opErr.Code != ErrCodeImportNotBundle {
		t.Errorf("Code = %q, muốn %q", opErr.Code, ErrCodeImportNotBundle)
	}
}

func TestRunImport_MissingFile_ReturnsOpError(t *testing.T) {
	composePath := testComposePath(t, "")
	env := testEnv(t, composePath)

	err := RunImport(context.Background(), env, filepath.Join(t.TempDir(), "khong-ton-tai.ghbundle"), ImportOptions{AutoApprove: true}, ImportDeps{Runner: &fake.Runner{}}, strings.NewReader(""), &strings.Builder{})
	opErr, ok := err.(*OpError)
	if !ok {
		t.Fatalf("lỗi phải là *OpError, được %T", err)
	}
	if opErr.Code != ErrCodeImportNotBundle {
		t.Errorf("Code = %q, muốn %q", opErr.Code, ErrCodeImportNotBundle)
	}
}

func TestRunImport_WrongPassword_ExitCode2_MapsToClearError(t *testing.T) {
	withBundlePassword(t, "mat-khau-du-dai-123")
	composePath := testComposePath(t, "")
	env := testEnv(t, composePath)
	bundlePath := bundleFile(t, "x")

	fr := &fake.Runner{Responses: []fake.Response{
		{Match: fake.MatchArgsContain("exec", "-T", "api", "python", "-m", "gh.backup", "run"), Lines: []string{importTestBackupLine}},
		{Match: fake.MatchArgsContain("exec", "-T", "api", "python", "-c"), Output: []byte("bytes-ma-hoa-gia")},
		{Match: fake.MatchArgsContain("stop", "api", "worker"), Output: []byte("")},
		{Match: fake.MatchArgsContain("gh.bundle", "import"), ExitCode: 2},
		{Match: fake.MatchArgsContain("up", "-d", "api", "worker"), Output: []byte("")},
	}}

	err := RunImport(context.Background(), env, bundlePath, ImportOptions{AutoApprove: true}, ImportDeps{Runner: fr}, strings.NewReader(""), &strings.Builder{})
	opErr, ok := err.(*OpError)
	if !ok {
		t.Fatalf("lỗi phải là *OpError, được %T", err)
	}
	if opErr.Code != ErrCodeImportWrongPassword {
		t.Errorf("Code = %q, muốn %q", opErr.Code, ErrCodeImportWrongPassword)
	}
	if !strings.Contains(opErr.Next, "backups/20260925T110000Z-c0ffee12.pgcustom.enc") {
		t.Errorf("Next phải nhắc khôi phục từ backup an toàn, được %q", opErr.Next)
	}
	var sawRecoveryUp bool
	for _, c := range fr.Calls {
		if hasExactArgs(c.Cmd.Args, "up", "-d", "api", "worker") {
			sawRecoveryUp = true
		}
	}
	if !sawRecoveryUp {
		t.Error("phải cố `docker compose up -d api worker` lại sau khi import lỗi (đã dừng ở bước 2/4)")
	}
}

func TestRunImport_Incompatible_ExitCode3_MapsToClearError(t *testing.T) {
	withBundlePassword(t, "mat-khau-du-dai-123")
	composePath := testComposePath(t, "")
	env := testEnv(t, composePath)
	bundlePath := bundleFile(t, "x")

	fr := &fake.Runner{Responses: []fake.Response{
		{Match: fake.MatchArgsContain("exec", "-T", "api", "python", "-m", "gh.backup", "run"), Lines: []string{importTestBackupLine}},
		{Match: fake.MatchArgsContain("exec", "-T", "api", "python", "-c"), Output: []byte("bytes-ma-hoa-gia")},
		{Match: fake.MatchArgsContain("stop", "api", "worker"), Output: []byte("")},
		{Match: fake.MatchArgsContain("gh.bundle", "import"), ExitCode: 3},
		{Match: fake.MatchArgsContain("up", "-d", "api", "worker"), Output: []byte("")},
	}}

	err := RunImport(context.Background(), env, bundlePath, ImportOptions{AutoApprove: true}, ImportDeps{Runner: fr}, strings.NewReader(""), &strings.Builder{})
	opErr, ok := err.(*OpError)
	if !ok {
		t.Fatalf("lỗi phải là *OpError, được %T", err)
	}
	if opErr.Code != ErrCodeImportIncompatible {
		t.Errorf("Code = %q, muốn %q", opErr.Code, ErrCodeImportIncompatible)
	}
}

func TestRunImport_BackupFailsFirst_StopsBeforeTouchingAnything(t *testing.T) {
	withBundlePassword(t, "mat-khau-du-dai-123")
	composePath := testComposePath(t, "")
	env := testEnv(t, composePath)
	bundlePath := bundleFile(t, "x")

	fr := &fake.Runner{Responses: []fake.Response{
		{Match: fake.MatchArgsContain("exec", "-T", "api", "python", "-m", "gh.backup", "run"), Err: errors.New("db down")},
	}}

	err := RunImport(context.Background(), env, bundlePath, ImportOptions{AutoApprove: true}, ImportDeps{Runner: fr}, strings.NewReader(""), &strings.Builder{})
	opErr, ok := err.(*OpError)
	if !ok {
		t.Fatalf("lỗi phải là *OpError, được %T", err)
	}
	if opErr.Code != ErrCodeImportBackupFailed {
		t.Errorf("Code = %q, muốn %q", opErr.Code, ErrCodeImportBackupFailed)
	}
	for _, c := range fr.Calls {
		if fake.MatchArgsContain("gh.bundle", "import")(c.Cmd) {
			t.Fatalf("KHÔNG được gọi gh.bundle import khi backup an toàn thất bại")
		}
	}
}

// TestRunImport_CopyBackupToHostFails_StopsBeforeStoppingContainers là test
// cho mục #4/#5 v0.1.2: nếu chép bản backup an toàn ra host thất bại, PHẢI
// dừng lại NGAY (chưa `docker compose stop api worker`, chưa đụng gì khác) —
// bản backup trong ObjectStore container vẫn còn, nhưng KHÔNG có bản trên
// host thì rollback không còn đáng tin nếu container sau đó bị thay.
func TestRunImport_CopyBackupToHostFails_StopsBeforeStoppingContainers(t *testing.T) {
	withBundlePassword(t, "mat-khau-du-dai-123")
	composePath := testComposePath(t, "")
	env := testEnv(t, composePath)
	bundlePath := bundleFile(t, "x")

	fr := &fake.Runner{Responses: []fake.Response{
		{Match: fake.MatchArgsContain("exec", "-T", "api", "python", "-m", "gh.backup", "run"), Lines: []string{importTestBackupLine}},
		{Match: fake.MatchArgsContain("exec", "-T", "api", "python", "-c"), Err: errors.New("container không phản hồi")},
	}}

	err := RunImport(context.Background(), env, bundlePath, ImportOptions{AutoApprove: true}, ImportDeps{Runner: fr}, strings.NewReader(""), &strings.Builder{})
	opErr, ok := err.(*OpError)
	if !ok {
		t.Fatalf("lỗi phải là *OpError, được %T", err)
	}
	if opErr.Code != ErrCodeImportBackupFailed {
		t.Errorf("Code = %q, muốn %q", opErr.Code, ErrCodeImportBackupFailed)
	}
	for _, c := range fr.Calls {
		joined := strings.Join(c.Cmd.Args, " ")
		if strings.Contains(joined, "stop") || strings.Contains(joined, "gh.bundle") {
			t.Errorf("KHÔNG được stop container/import khi chép backup ra host đã thất bại, Calls=%+v", fr.Calls)
		}
	}
}

// TestRunImport_StopContainersFails_TriesRecoveryUpAndReportsBoth kiểm hành
// vi "LỖI GIỮA CHỪNG" của RunImport: nếu `docker compose stop api worker`
// thất bại, PHẢI cố `docker compose up -d api worker` lại NGAY, và nếu bước
// cố gắng đó CŨNG thất bại, Next phải nói rõ CẢ HAI thất bại (không chỉ lỗi
// gốc).
func TestRunImport_StopContainersFails_TriesRecoveryUpAndReportsBoth(t *testing.T) {
	withBundlePassword(t, "mat-khau-du-dai-123")
	composePath := testComposePath(t, "")
	env := testEnv(t, composePath)
	bundlePath := bundleFile(t, "x")

	fr := &fake.Runner{Responses: []fake.Response{
		{Match: fake.MatchArgsContain("exec", "-T", "api", "python", "-m", "gh.backup", "run"), Lines: []string{importTestBackupLine}},
		{Match: fake.MatchArgsContain("exec", "-T", "api", "python", "-c"), Output: []byte("bytes-ma-hoa-gia")},
		{Match: fake.MatchArgsContain("stop", "api", "worker"), Err: errors.New("timeout dừng container")},
		{Match: fake.MatchArgsContain("up", "-d", "api", "worker"), Err: errors.New("port đã bị chiếm")},
	}}

	var out strings.Builder
	err := RunImport(context.Background(), env, bundlePath, ImportOptions{AutoApprove: true}, ImportDeps{Runner: fr}, strings.NewReader(""), &out)
	opErr, ok := err.(*OpError)
	if !ok {
		t.Fatalf("lỗi phải là *OpError, được %T", err)
	}
	if !strings.Contains(opErr.Next, "CŨNG thất bại") {
		t.Errorf("Next phải nói rõ cả nỗ lực khởi động lại CŨNG thất bại, được %q", opErr.Next)
	}
	if !strings.Contains(opErr.Next, "backups/20260925T110000Z-c0ffee12.pgcustom.enc") {
		t.Errorf("Next vẫn phải nhắc khoá backup an toàn, được %q", opErr.Next)
	}
	for _, c := range fr.Calls {
		if strings.Contains(strings.Join(c.Cmd.Args, " "), "gh.bundle") {
			t.Error("KHÔNG được chạy gh.bundle import khi stop api/worker đã thất bại")
		}
	}
}

// v0.1.40: RunExport (CLI) vẫn hỏi mật khẩu ẩn 2 lần khi không có GH_BUNDLE_PASSWORD.
func TestRunExport_CLI_VanHoiMatKhauHaiLan(t *testing.T) {
	t.Setenv(bundlePasswordEnv, "")
	env := testEnv(t, testComposePath(t, ""))
	calls := 0
	orig := readSecretLine
	readSecretLine = func() (string, error) { calls++; return "mat-khau-du-dai-123", nil }
	t.Cleanup(func() { readSecretLine = orig })
	fr := &fake.Runner{Responses: []fake.Response{{Match: fake.MatchArgsContain("gh.bundle", "export"), RunIOStdout: []byte(bundleMagic)}}}
	if err := RunExport(context.Background(), env, filepath.Join(t.TempDir(), "o.ghbundle"), ExportDeps{Runner: fr}, &strings.Builder{}); err != nil {
		t.Fatal(err)
	}
	if calls != 2 {
		t.Fatalf("RunExport phải hỏi mật khẩu 2 lần, hỏi %d", calls)
	}
}

// exportBundle (lõi cho `genh offsite run`) KHÔNG hỏi gì; mật khẩu chỉ qua Env, không qua argv.
func TestExportBundle_KhongHoiMatKhau_MatKhauQuaEnv(t *testing.T) {
	t.Setenv(bundlePasswordEnv, "")
	env := testEnv(t, testComposePath(t, ""))
	orig := readSecretLine
	readSecretLine = func() (string, error) { t.Fatal("exportBundle không được hỏi mật khẩu"); return "", nil }
	t.Cleanup(func() { readSecretLine = orig })
	fr := &fake.Runner{Responses: []fake.Response{
		{Match: fake.MatchArgsContain("exec", "-T", "-e", bundlePasswordEnv, "api", "python", "-m", "gh.bundle", "export", "--out", "-"), RunIOStdout: []byte(bundleMagic + "x")},
	}}
	to := filepath.Join(t.TempDir(), "o.ghbundle")
	const pw = "K7QX2-AAAAA-BBBBB-CCCCC-DDDDD-EEEEE"
	if err := exportBundle(context.Background(), env, to, pw, ExportDeps{Runner: fr}, &strings.Builder{}); err != nil {
		t.Fatal(err)
	}
	if b, _ := os.ReadFile(to); string(b) != bundleMagic+"x" {
		t.Fatalf("nội dung = %q", b)
	}
	c := fr.Calls[0].Cmd
	if strings.Contains(strings.Join(c.Args, " "), pw) {
		t.Fatalf("mật khẩu lộ vào argv: %v", c.Args)
	}
	found := false
	for _, e := range c.Env {
		if e == bundlePasswordEnv+"="+pw {
			found = true
		}
	}
	if !found {
		t.Fatal("mật khẩu phải đi qua Env GH_BUNDLE_PASSWORD của tiến trình con")
	}
}

// verifyBundle: hợp đồng tham số docker + ánh xạ mã thoát 0/2/3/1.
func TestVerifyBundle_HopDongThamSoVaMaThoat(t *testing.T) {
	env := testEnv(t, testComposePath(t, ""))
	path := bundleFile(t, "noi-dung")
	const pw = "K7QX2-AAAAA-BBBBB-CCCCC-DDDDD-EEEEE"
	verifyArgs := []string{"exec", "-T", "-e", bundlePasswordEnv, "api", "python", "-m", "gh.bundle", "verify", "--in", "-"}

	fr := &fake.Runner{Responses: []fake.Response{{Match: fake.MatchArgsContain(verifyArgs...),
		RunIOStdout: []byte(`{"ok":true,"alembic_revision":"0026","objects":3,"db_dump_bytes":42,"created_at":"2026-10-04T05:40:00Z"}` + "\n")}}}
	info, err := verifyBundle(context.Background(), env, path, pw, ExportDeps{Runner: fr})
	if err != nil || !info.OK || info.Objects != 3 || info.AlembicRevision != "0026" {
		t.Fatalf("verify thoát 0 = %+v, %v", info, err)
	}
	call := fr.Calls[0]
	args := strings.Join(call.Cmd.Args, " ")
	if !strings.Contains(args, strings.Join(verifyArgs, " ")) || strings.Contains(args, pw) {
		t.Fatalf("args docker sai hoặc lộ mật khẩu: %v", call.Cmd.Args)
	}
	if string(call.Stdin) != bundleMagic+"noi-dung" {
		t.Fatalf("stdin phải là đúng tệp gói, được %q", call.Stdin)
	}
	for _, code := range []int{2, 3, 1} {
		fr := &fake.Runner{Responses: []fake.Response{{Match: fake.MatchArgsContain("gh.bundle", "verify"), ExitCode: code}}}
		_, err := verifyBundle(context.Background(), env, path, pw, ExportDeps{Runner: fr})
		var opErr *OpError
		if !errors.As(err, &opErr) || opErr.Code != ErrCodeOffsiteVerifyFailed {
			t.Fatalf("thoát %d phải là GH-EB03, được %v", code, err)
		}
		var exitErr *dockercli.ExitError
		if !errors.As(err, &exitErr) || exitErr.Code != code {
			t.Fatalf("thoát %d: phải giữ mã thoát gốc, được %v", code, err)
		}
	}
}

var _ dockercli.Runner = (*fake.Runner)(nil)
