package ops

import (
	"context"
	"crypto/tls"
	"encoding/json"
	"errors"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/compose"
	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/dockercli"
	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/dockercli/fake"
	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/hostlink"
	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/secretgen"
)

// updateTestComposeYAML phản ánh đúng dạng thật của deploy/compose.yaml:
// proxy/redis có "image:" cố định (pull được), api/web/bridge/db/worker dùng
// "build:" cục bộ (KHÔNG pull được) — đúng resolveUpdateServices phải tách
// hai nhóm này ra. Khác bản nhúng thật → với testEnv (compose genh quản lý)
// lần cập nhật này "đổi compose".
const updateTestComposeYAML = `name: gen-harness
services:
  proxy:
    image: caddy:2-alpine
  redis:
    image: redis:7-alpine
  api:
    build: { context: .. }
  worker:
    build: { context: .. }
  web:
    build: { context: .. }
  bridge:
    build: { context: .. }
  db:
    build: { context: .. }
`

const updateTestBackupKey = "backups/20260925T100000Z-deadbeef.pgcustom.enc"
const updateTestBackupLine = "INFO:gh.backup:Backup mới: " + updateTestBackupKey + " (999 byte, CSDL gen_harness)"

const testVersion = "v0.1.34"

// exactArgs khớp lệnh có MỌI phần tử want xuất hiện NGUYÊN VẸN trong Args —
// tránh khớp nhầm chuỗi con ("up" nằm trong "gh.backup"/"update-next").
func exactArgs(want ...string) func(dockercli.Cmd) bool {
	return func(cmd dockercli.Cmd) bool { return hasExactArgs(cmd.Args, want...) }
}

var (
	matchAlembicCurrent = exactArgs("alembic", "current")
	matchAlembicHeads   = exactArgs("alembic", "heads")
	matchBackupRun      = exactArgs("gh.backup", "run")
	matchRestore        = exactArgs("gh.backup", "restore")
	matchPull           = exactArgs("pull")
	matchMigrate        = func(cmd dockercli.Cmd) bool {
		return hasExactArgs(cmd.Args, "run", "--rm", "-T", "--no-deps", "migrate") && !hasExactArgs(cmd.Args, "alembic")
	}
	matchUp    = exactArgs("up", "-d")
	matchStop  = exactArgs("stop")
	matchStart = exactArgs("start")
	// matchFullUp: `up -d --remove-orphans` (cả hệ thống) — khác `up -d --wait
	// --no-deps db` (dựng lại db bằng ảnh cũ trước khi khôi phục).
	matchFullUp = exactArgs("up", "-d", "--remove-orphans")
	matchDBUp   = exactArgs("up", "-d", "--wait", "--no-deps", "db")
)

// pendingMigration: `alembic current` bằng ảnh mới báo CHƯA ở head — bản mới
// có thay đổi CSDL (dừng worker/bridge trước sao lưu; lỗi sau migrate thì phải
// khôi phục CSDL).
var pendingMigration = fake.Response{Match: matchAlembicCurrent, Output: []byte("0041_x\n")}

// lastCallIndex như callIndex nhưng lấy lần gọi CUỐI.
func lastCallIndex(fr *fake.Runner, match func(dockercli.Cmd) bool) int {
	for i := len(fr.Calls) - 1; i >= 0; i-- {
		if match(fr.Calls[i].Cmd) {
			return i
		}
	}
	return -1
}

// updateFakeRunner dựng fake.Runner cho luồng cập nhật, mọi lệnh thành công
// trừ khi over ghi đè (response trong over được xét TRƯỚC). `alembic current`
// mặc định báo đã ở head (không có thay đổi CSDL).
func updateFakeRunner(over ...fake.Response) *fake.Runner {
	base := []fake.Response{
		{Match: matchAlembicCurrent, Output: []byte("INFO  [alembic.runtime.migration] Context impl PostgresqlImpl.\n0042_x (head)\n")},
		{Match: matchAlembicHeads, Output: []byte("0042_x (head)\n")},
		{Match: matchBackupRun, Lines: []string{updateTestBackupLine}},
		{Match: matchPull, Output: []byte("")},
		{Match: matchMigrate, Lines: []string{}},
		{Match: matchRestore, Output: []byte("")},
		{Match: matchStop, Output: []byte("")},
		{Match: matchStart, Output: []byte("")},
		{Match: matchUp, Output: []byte("")},
	}
	return &fake.Runner{Responses: append(append([]fake.Response{}, over...), base...)}
}

// updateHappyFakeRunner giữ tên cũ cho các test di trú dữ liệu.
func updateHappyFakeRunner() *fake.Runner { return updateFakeRunner() }

// listenReadyServer bind một httptest TLS server vào 127.0.0.1:0 (cổng ngẫu
// nhiên do hệ điều hành cấp), trả về server + cổng — dùng để test RunUpdate
// đường "readiness thành công thật" (waitReady gọi thẳng
// https://127.0.0.1:<port>/api/v1/ready qua localURL, không có cách tiêm
// URL khác, nên phải bind đúng cổng env.Port sẽ dùng).
func listenReadyServer(t *testing.T, healthy bool) (srv *httptest.Server, port int) {
	t.Helper()
	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatalf("net.Listen: %v", err)
	}
	handler := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		status := http.StatusServiceUnavailable
		if healthy {
			status = http.StatusOK
		}
		w.WriteHeader(status)
		_ = json.NewEncoder(w).Encode(map[string]string{"db": "ok"})
	})
	srv = httptest.NewUnstartedServer(handler)
	srv.Listener = ln
	srv.TLS = &tls.Config{}
	srv.StartTLS()
	t.Cleanup(srv.Close)
	return srv, ln.Addr().(*net.TCPAddr).Port
}

// fastUpdateDeps: thời gian chờ ngắn, KHÔNG đo đĩa thật (100 GB trống), không
// chờ giữa các lần pull.
func fastUpdateDeps(runner dockercli.Runner) UpdateDeps {
	return UpdateDeps{Runner: runner, Timeout: 200 * time.Millisecond, PollEvery: 5 * time.Millisecond,
		ReTrustCA:   func(context.Context, *Env) {},
		DiskFree:    func(string) (uint64, error) { return 100 << 30, nil },
		PullBackoff: []time.Duration{0},
		PullTimeout: 5 * time.Second,
	}
}

func callIndex(fr *fake.Runner, match func(dockercli.Cmd) bool) int {
	for i, c := range fr.Calls {
		if match(c.Cmd) {
			return i
		}
	}
	return -1
}

func countCalls(fr *fake.Runner, match func(dockercli.Cmd) bool) int {
	n := 0
	for _, c := range fr.Calls {
		if match(c.Cmd) {
			n++
		}
	}
	return n
}

func blockedExists(t *testing.T, installDir string) bool {
	t.Helper()
	_, ok, err := hostlink.ReadUpdateBlocked(installDir)
	if err != nil {
		t.Fatalf("ReadUpdateBlocked: %v", err)
	}
	return ok
}

func asOpError(t *testing.T, err error) *OpError {
	t.Helper()
	opErr, ok := err.(*OpError)
	if !ok {
		t.Fatalf("lỗi phải là *OpError, được %T (%v)", err, err)
	}
	return opErr
}

func TestRunUpdate_HappyPath_ReadySucceeds_NoRollback(t *testing.T) {
	composePath := testComposePath(t, updateTestComposeYAML)
	env := testEnv(t, composePath)

	_, port := listenReadyServer(t, true)
	env.Port = port

	fr := updateFakeRunner()

	var out strings.Builder
	if err := RunUpdate(context.Background(), env, UpdateOptions{Channel: "stable", Version: testVersion}, fastUpdateDeps(fr), &out); err != nil {
		t.Fatalf("RunUpdate: %v", err)
	}
	if !strings.Contains(out.String(), "Cập nhật xong") {
		t.Errorf("output phải xác nhận cập nhật xong, được %q", out.String())
	}
	if n := countCalls(fr, matchRestore); n != 0 {
		t.Errorf("KHÔNG được gọi restore khi cập nhật thành công, restoreCalls=%d", n)
	}
	// Bản sao lưu trước cập nhật ghi nguồn "pre-update" (Console hiện cột Nguồn).
	bi := callIndex(fr, matchBackupRun)
	if bi < 0 || !strings.Contains(strings.Join(fr.Calls[bi].Cmd.Args, " "), "-e GH_BACKUP_TRIGGER=pre-update api python -m gh.backup run") {
		t.Errorf("backup trước cập nhật phải mang GH_BACKUP_TRIGGER=pre-update: %+v", fr.Calls)
	}
	ds, err := hostlink.ReadDiskStatus(env.InstallDir)
	if err != nil || ds.State != "ok" || ds.FreeBytes != 100<<30 {
		t.Errorf("disk-status.json phải state=ok, 100 GB: %+v (%v)", ds, err)
	}
	if n := countCalls(fr, matchStop); n != 0 {
		t.Errorf("không có thay đổi CSDL thì không được dừng dịch vụ nào, stop=%d", n)
	}
}

func TestRunUpdate_ReadyNeverHealthy_RestoresWithOldImage_WritesBlocked(t *testing.T) {
	composePath := testComposePath(t, updateTestComposeYAML)
	env := testEnv(t, composePath)

	_, port := listenReadyServer(t, false) // luôn 503
	env.Port = port

	fr := updateFakeRunner(pendingMigration)

	var out strings.Builder
	err := RunUpdate(context.Background(), env, UpdateOptions{Version: testVersion}, fastUpdateDeps(fr), &out)
	opErr := asOpError(t, err)
	if opErr.Code != ErrCodeUpdateRolledBack {
		t.Errorf("Code = %q, muốn %q", opErr.Code, ErrCodeUpdateRolledBack)
	}
	if !strings.Contains(out.String(), "Rollback xong") {
		t.Errorf("output phải xác nhận rollback xong, được %q", out.String())
	}
	ri := callIndex(fr, matchRestore)
	if ri < 0 || !hasExactArgs(fr.Calls[ri].Cmd.Args, "run", "--rm", "--no-deps", "-T", "api") {
		t.Errorf("restore phải chạy bằng container tạm `run --rm --no-deps -T api`: %+v", fr.Calls)
	}
	b, ok, _ := hostlink.ReadUpdateBlocked(env.InstallDir)
	if !ok || b.Version != testVersion || b.Code != ErrCodeUpdateNotReady || b.BackupKey != updateTestBackupKey || !b.DBTouched {
		t.Errorf("update-blocked.json sai: ok=%v %+v", ok, b)
	}
}

func TestRunUpdate_BackupFails_NoRestoreNoUp(t *testing.T) {
	composePath := testComposePath(t, updateTestComposeYAML)
	env := testEnv(t, composePath)

	fr := updateFakeRunner(fake.Response{Match: matchBackupRun, Err: errors.New("db down")})

	err := RunUpdate(context.Background(), env, UpdateOptions{}, fastUpdateDeps(fr), &strings.Builder{})
	opErr := asOpError(t, err)
	if opErr.Code != ErrCodeUpdateBackupFailed {
		t.Errorf("Code = %q, muốn %q", opErr.Code, ErrCodeUpdateBackupFailed)
	}
	if countCalls(fr, matchRestore) != 0 || countCalls(fr, matchUp) != 0 || countCalls(fr, matchMigrate) != 0 {
		t.Errorf("backup lỗi thì KHÔNG restore/up/migrate: %+v", fr.Calls)
	}
}

func TestRunUpdate_PullFails_NoRestore_NothingTouched(t *testing.T) {
	composePath := testComposePath(t, updateTestComposeYAML)
	env := testEnv(t, composePath)

	fr := updateFakeRunner(fake.Response{Match: matchPull, Err: errors.New("net down")})

	var out strings.Builder
	err := RunUpdate(context.Background(), env, UpdateOptions{Version: testVersion}, fastUpdateDeps(fr), &out)
	opErr := asOpError(t, err)
	if opErr.Code != ErrCodeUpdatePullFailed {
		t.Errorf("Code = %q, muốn %q", opErr.Code, ErrCodeUpdatePullFailed)
	}
	if !strings.Contains(opErr.What, "CHƯA đụng gì") || !strings.Contains(opErr.What, "3 lần") {
		t.Errorf("What phải nói rõ thử 3 lần và chưa đụng gì: %q", opErr.What)
	}
	if n := countCalls(fr, matchPull); n != 3 {
		t.Errorf("pull phải thử 3 lần, được %d", n)
	}
	for name, m := range map[string]func(dockercli.Cmd) bool{"backup": matchBackupRun, "restore": matchRestore, "up": matchUp, "stop": matchStop, "migrate": matchMigrate} {
		if n := countCalls(fr, m); n != 0 {
			t.Errorf("pull lỗi thì KHÔNG được %s (được %d lần)", name, n)
		}
	}
	after, _ := os.ReadFile(composePath)
	if string(after) != updateTestComposeYAML {
		t.Error("compose.yaml phải giữ nguyên khi tải lỗi")
	}
	if blockedExists(t, env.InstallDir) {
		t.Error("tải lỗi thì KHÔNG ghi update-blocked.json")
	}
	if _, err := os.Stat(filepath.Join(filepath.Dir(composePath), updateNextComposeName)); !os.IsNotExist(err) {
		t.Errorf("compose tạm phải bị xoá: %v", err)
	}
}

func TestRunUpdate_PullRetriesThenSucceeds(t *testing.T) {
	composePath := testComposePath(t, updateTestComposeYAML)
	env := testEnv(t, composePath)
	_, port := listenReadyServer(t, true)
	env.Port = port

	fr := updateFakeRunner(fake.Response{Match: matchPull, ErrSeq: []error{errors.New("lỗi 1"), errors.New("lỗi 2"), nil}})
	if err := RunUpdate(context.Background(), env, UpdateOptions{}, fastUpdateDeps(fr), &strings.Builder{}); err != nil {
		t.Fatalf("RunUpdate: %v", err)
	}
	if n := countCalls(fr, matchPull); n != 3 {
		t.Errorf("pull phải gọi 3 lần, được %d", n)
	}
	if countCalls(fr, matchBackupRun) != 1 || countCalls(fr, matchMigrate) != 1 {
		t.Errorf("tải được ở lần 3 thì phải chạy tiếp sao lưu + migrate: %+v", fr.Calls)
	}
}

func TestRunUpdate_PullTimeoutPerAttempt(t *testing.T) {
	composePath := testComposePath(t, updateTestComposeYAML)
	env := testEnv(t, composePath)

	fr := updateFakeRunner(fake.Response{Match: matchPull, WaitCtx: true})
	deps := fastUpdateDeps(fr)
	deps.PullTimeout = 50 * time.Millisecond

	start := time.Now()
	err := RunUpdate(context.Background(), env, UpdateOptions{}, deps, &strings.Builder{})
	opErr := asOpError(t, err)
	if opErr.Code != ErrCodeUpdatePullFailed {
		t.Errorf("Code = %q, muốn %q", opErr.Code, ErrCodeUpdatePullFailed)
	}
	if n := countCalls(fr, matchPull); n != 3 {
		t.Errorf("pull phải thử 3 lần, được %d", n)
	}
	if time.Since(start) > 10*time.Second {
		t.Errorf("mỗi lần pull phải bị cắt theo PullTimeout, chạy mất %s", time.Since(start))
	}
	if countCalls(fr, matchBackupRun) != 0 {
		t.Error("tải quá giờ thì không được sao lưu")
	}
}

// realComposeUpdateEnv dựng một *Env KHÔNG tiêm locate/locateSync giả — đi
// thẳng qua compose.Locate/LocateAndSync THẬT — dùng để kiểm thứ tự đồng bộ
// compose.yaml và rollback compose.yaml trên đĩa.
func realComposeUpdateEnv(t *testing.T, yaml string) (env *Env, composePath string) {
	t.Helper()
	t.Setenv(compose.EnvOverrideVar, "")

	installDir := t.TempDir()
	composePath = filepath.Join(installDir, "deploy", "compose.yaml")
	if err := os.MkdirAll(filepath.Dir(composePath), 0o755); err != nil {
		t.Fatalf("MkdirAll: %v", err)
	}
	if err := os.WriteFile(composePath, []byte(yaml), 0o644); err != nil {
		t.Fatalf("WriteFile: %v", err)
	}
	if _, err := secretgen.Ensure(filepath.Join(installDir, "config")); err != nil {
		t.Fatalf("secretgen.Ensure: %v", err)
	}
	return &Env{InstallDir: installDir, Port: 18443}, composePath
}

func TestRunUpdate_PullBeforeBackup_UsesTempComposeNotSynced(t *testing.T) {
	env, composePath := realComposeUpdateEnv(t, updateTestComposeYAML)
	_, port := listenReadyServer(t, true)
	env.Port = port
	nextPath := filepath.Join(filepath.Dir(composePath), updateNextComposeName)

	var composeAtPull, composeAtBackup, nextAtPull string
	fr := updateFakeRunner(
		fake.Response{Match: func(cmd dockercli.Cmd) bool {
			if !matchPull(cmd) {
				return false
			}
			b, _ := os.ReadFile(composePath)
			composeAtPull = string(b)
			n, _ := os.ReadFile(nextPath)
			nextAtPull = string(n)
			return true
		}},
		fake.Response{Match: func(cmd dockercli.Cmd) bool {
			if !matchBackupRun(cmd) {
				return false
			}
			b, _ := os.ReadFile(composePath)
			composeAtBackup = string(b)
			return true
		}, Lines: []string{updateTestBackupLine}},
	)

	if err := RunUpdate(context.Background(), env, UpdateOptions{}, fastUpdateDeps(fr), &strings.Builder{}); err != nil {
		t.Fatalf("RunUpdate: %v", err)
	}
	pi, bi := callIndex(fr, matchPull), callIndex(fr, matchBackupRun)
	if pi < 0 || bi < 0 || pi > bi {
		t.Fatalf("pull (idx %d) phải đứng TRƯỚC backup (idx %d)", pi, bi)
	}
	pullArgs := fr.Calls[pi].Cmd.Args
	if len(pullArgs) < 3 || pullArgs[1] != "-f" || pullArgs[2] != nextPath {
		t.Errorf("pull phải dùng -f %s, được %v", nextPath, pullArgs)
	}
	if nextAtPull != string(compose.EmbeddedCompose()) {
		t.Error("compose tạm lúc pull phải là bản nhúng (bản đích)")
	}
	if composeAtPull != updateTestComposeYAML || composeAtBackup != updateTestComposeYAML {
		t.Error("compose.yaml phải còn bản CŨ lúc pull và lúc backup (chỉ đồng bộ SAU backup)")
	}
	after, _ := os.ReadFile(composePath)
	if string(after) != string(compose.EmbeddedCompose()) {
		t.Error("sau khi cập nhật xong, compose.yaml phải là bản nhúng")
	}
	if _, err := os.Stat(nextPath); !os.IsNotExist(err) {
		t.Errorf("compose tạm phải bị xoá sau đó: %v", err)
	}
	bak, err := os.ReadFile(composePath + ".bak")
	if err != nil || string(bak) != updateTestComposeYAML {
		t.Errorf("phải giữ bản cũ ở compose.yaml.bak: %v", err)
	}
}

func TestRunUpdate_MigrateFails_RestoresWithOldImage_WritesBlocked(t *testing.T) {
	composePath := testComposePath(t, updateTestComposeYAML)
	env := testEnv(t, composePath)

	fr := updateFakeRunner(pendingMigration, fake.Response{Match: matchMigrate, Err: errors.New("alembic: lock timeout")})

	var out strings.Builder
	err := RunUpdate(context.Background(), env, UpdateOptions{Version: testVersion}, fastUpdateDeps(fr), &out)
	opErr := asOpError(t, err)
	if opErr.Code != ErrCodeUpdateRolledBack {
		t.Errorf("Code = %q, muốn %q", opErr.Code, ErrCodeUpdateRolledBack)
	}
	// Console nhận What: tiếng Việt "quay về bản cũ"; log CLI vẫn có "Rollback xong" (E2E grep).
	if !strings.Contains(opErr.What, "đã tự quay về bản cũ") || strings.Contains(opErr.What, "rollback") || !strings.Contains(out.String(), "Rollback xong") {
		t.Errorf("What phải nói \"đã tự quay về bản cũ\" (không dùng chữ rollback), log có \"Rollback xong\", được %q", opErr.What)
	}

	// stop ĐẦU là dừng worker/bridge trước sao lưu; stop CUỐI là của rollback.
	si, ri, ui := lastCallIndex(fr, matchStop), callIndex(fr, matchRestore), callIndex(fr, matchFullUp)
	if si < 0 || ri < 0 || ui < 0 || !(si < ri && ri < ui) {
		t.Fatalf("thứ tự phải là stop → restore → up, được stop=%d restore=%d up=%d: %+v", si, ri, ui, fr.Calls)
	}
	stopArgs := strings.Join(fr.Calls[si].Cmd.Args, " ")
	if !strings.HasSuffix(stopArgs, "stop api worker bridge web") {
		t.Errorf("phải dừng api worker bridge web, được %q", stopArgs)
	}
	restoreArgs := strings.Join(fr.Calls[ri].Cmd.Args, " ")
	if !strings.Contains(restoreArgs, "run --rm --no-deps -T api python -m gh.backup restore --key "+updateTestBackupKey) {
		t.Errorf("restore phải là `run --rm --no-deps -T api …`, được %q", restoreArgs)
	}
	for _, c := range fr.Calls {
		if matchRestore(c.Cmd) && hasExactArgs(c.Cmd.Args, "exec") {
			t.Errorf("KHÔNG BAO GIỜ được `exec … restore` (api ảnh mới): %v", c.Cmd.Args)
		}
	}
	if !hasExactArgs(fr.Calls[ui].Cmd.Args, "--remove-orphans") {
		t.Errorf("up của rollback phải có --remove-orphans: %v", fr.Calls[ui].Cmd.Args)
	}
	if countCalls(fr, matchFullUp) != 1 {
		t.Errorf("chỉ 1 lần up -d --remove-orphans (của rollback), được %d", countCalls(fr, matchFullUp))
	}
	// Compose đổi → dựng lại db bằng ảnh CŨ (compose cũ) TRƯỚC khi khôi phục.
	if di := callIndex(fr, matchDBUp); di < 0 || di > ri || di < si {
		t.Errorf("phải `up -d --wait --no-deps db` bằng compose cũ giữa stop và restore: db=%d stop=%d restore=%d", di, si, ri)
	}
	b, ok, _ := hostlink.ReadUpdateBlocked(env.InstallDir)
	if !ok || b.Version != testVersion || b.Code != ErrCodeUpdateMigrateFailed || b.BackupKey != updateTestBackupKey || b.BlockedAt == "" {
		t.Errorf("update-blocked.json sai: ok=%v %+v", ok, b)
	}
}

// hasExactArgs trả true nếu mọi phần tử trong want xuất hiện NGUYÊN VẸN
// (không phải chuỗi con) trong args.
func hasExactArgs(args []string, want ...string) bool {
	set := make(map[string]bool, len(args))
	for _, a := range args {
		set[a] = true
	}
	for _, w := range want {
		if !set[w] {
			return false
		}
	}
	return true
}

func TestRunUpdate_MigrateFails_RollbackAlsoFails_ReportsBothFailures_StillBlocked(t *testing.T) {
	composePath := testComposePath(t, updateTestComposeYAML)
	env := testEnv(t, composePath)

	fr := updateFakeRunner(
		pendingMigration,
		fake.Response{Match: matchMigrate, Err: errors.New("alembic: lock timeout")},
		fake.Response{Match: matchRestore, Err: errors.New("khoá không tồn tại")},
		fake.Response{Match: matchUp, Err: errors.New("container không khởi động lại được")},
	)

	var out strings.Builder
	err := RunUpdate(context.Background(), env, UpdateOptions{Version: testVersion}, fastUpdateDeps(fr), &out)
	opErr := asOpError(t, err)
	if opErr.Code != ErrCodeUpdateRolledBack {
		t.Errorf("Code = %q, muốn %q", opErr.Code, ErrCodeUpdateRolledBack)
	}
	if !strings.Contains(opErr.What, "ROLLBACK") {
		t.Errorf("What phải cảnh báo RÕ RÀNG rollback cũng thất bại, được %q", opErr.What)
	}
	if !strings.Contains(opErr.Next, "chạy tay `docker compose run --rm --no-deps -T api python -m gh.backup restore --key "+updateTestBackupKey+"`") {
		t.Errorf("Next phải hướng dẫn chạy tay bằng container tạm, được %q", opErr.Next)
	}
	if !strings.Contains(out.String(), "ROLLBACK THẤT BẠI") {
		t.Errorf("output phải cảnh báo rõ rollback thất bại, được %q", out.String())
	}
	b, ok, _ := hostlink.ReadUpdateBlocked(env.InstallDir)
	if !ok || !b.RollbackFailed {
		t.Errorf("rollback hỏng vẫn phải ghi update-blocked.json kèm rollback_failed=true: ok=%v %+v", ok, b)
	}
	if !hostlink.UpdateInProgressExists(env.InstallDir) {
		t.Error("rollback hỏng: phải GIỮ update-inprogress.json (lần sau không được coi là đã khớp)")
	}
}

func TestRunUpdate_RestartFails_RestoresWithOldImage_WritesBlocked(t *testing.T) {
	composePath := testComposePath(t, updateTestComposeYAML)
	env := testEnv(t, composePath)

	fr := updateFakeRunner(pendingMigration, fake.Response{Match: matchFullUp, ErrSeq: []error{errors.New("port already in use"), nil}})

	err := RunUpdate(context.Background(), env, UpdateOptions{Version: testVersion}, fastUpdateDeps(fr), &strings.Builder{})
	opErr := asOpError(t, err)
	if opErr.Code != ErrCodeUpdateRolledBack {
		t.Errorf("Code = %q, muốn %q", opErr.Code, ErrCodeUpdateRolledBack)
	}
	ri := callIndex(fr, matchRestore)
	if ri < 0 || !hasExactArgs(fr.Calls[ri].Cmd.Args, "run", "--rm", "--no-deps") {
		t.Errorf("phải restore bằng container tạm: %+v", fr.Calls)
	}
	b, ok, _ := hostlink.ReadUpdateBlocked(env.InstallDir)
	if !ok || b.Code != ErrCodeUpdateRestartFailed || b.Version != testVersion {
		t.Errorf("update-blocked.json sai: ok=%v %+v", ok, b)
	}
	// compose.yaml trả về bản cũ (testEnv: compose genh quản lý, lần này đổi compose).
	after, _ := os.ReadFile(composePath)
	if string(after) != updateTestComposeYAML {
		t.Error("compose.yaml phải là bản cũ sau rollback")
	}
}

func TestRunUpdate_InvalidChannel_ReturnsErrorBeforeAnyDockerCall(t *testing.T) {
	composePath := testComposePath(t, updateTestComposeYAML)
	env := testEnv(t, composePath)
	fr := &fake.Runner{}

	err := RunUpdate(context.Background(), env, UpdateOptions{Channel: "nightly"}, fastUpdateDeps(fr), &strings.Builder{})
	opErr := asOpError(t, err)
	if opErr.Code != ErrCodeUpdatePullFailed {
		t.Errorf("Code = %q, muốn %q", opErr.Code, ErrCodeUpdatePullFailed)
	}
	if len(fr.Calls) != 0 {
		t.Error("không được gọi docker khi --channel không hợp lệ")
	}
}

// legacyObjectsHappyResponses là các Response bổ sung mô phỏng container
// "api" (v0.1.0) có /tmp/gh-objects không rỗng, worker thì không — dùng
// chung cho các test RunUpdate liên quan tới di trú dữ liệu (mục #2 v0.1.2).
func legacyObjectsHappyResponses() []fake.Response {
	return []fake.Response{
		{Match: fake.MatchArgsContain("exec", "-T", "api", "sh", "-c"), Output: []byte("/tmp/gh-objects/docs/a.pdf")},
		{Match: fake.MatchArgsContain("exec", "-T", "worker", "sh", "-c"), Output: []byte("")},
		{Match: fake.MatchArgsContain("cp", "api:/tmp/gh-objects/."), Output: []byte("")},
	}
}

func TestRunUpdate_LegacyObjectsFound_SeedsVolumeAfterHealthy(t *testing.T) {
	composePath := testComposePath(t, updateTestComposeYAML)
	env := testEnv(t, composePath)
	_, port := listenReadyServer(t, true)
	env.Port = port

	fr := updateHappyFakeRunner()
	fr.Responses = append(fr.Responses, legacyObjectsHappyResponses()...)
	fr.Responses = append(fr.Responses, fake.Response{
		Match: fake.MatchArgsContain("cp", "api:"+volumeObjectsDir), Output: []byte(""),
	})
	fr.Responses = append(fr.Responses, fake.Response{
		Match: fake.MatchArgsContain("exec", "-u", "root", "-T", "api", "chown", "-R", "gh:gh", volumeObjectsDir), Output: []byte(""),
	})

	var out strings.Builder
	if err := RunUpdate(context.Background(), env, UpdateOptions{}, fastUpdateDeps(fr), &out); err != nil {
		t.Fatalf("RunUpdate: %v", err)
	}
	if !strings.Contains(out.String(), "đã di trú dữ liệu") {
		t.Errorf("output phải xác nhận đã di trú, được %q", out.String())
	}
	if !strings.Contains(out.String(), "Chép dữ liệu đã di trú vào volume") {
		t.Errorf("output phải xác nhận đã chép vào volume sau khi healthy, được %q", out.String())
	}

	seedCpDone, chownDone, seededBeforeUp := false, false, false
	upSeen := false
	for _, c := range fr.Calls {
		joined := strings.Join(c.Cmd.Args, " ")
		if strings.Contains(joined, "cp") && strings.Contains(joined, "api:"+volumeObjectsDir) {
			seedCpDone = true
			if !upSeen {
				seededBeforeUp = true
			}
		}
		if hasExactArgs(c.Cmd.Args, "up", "-d") {
			upSeen = true
		}
		if strings.Contains(joined, "chown") {
			chownDone = true
		}
	}
	if !seedCpDone || !chownDone {
		t.Errorf("phải chép + chown vào volume, Calls=%+v", fr.Calls)
	}
	if seededBeforeUp {
		t.Error("chép vào volume PHẢI chạy SAU `docker compose up -d` (container mới đã mount volume), không phải trước")
	}
}

// Di trú /tmp/gh-objects lỗi (CSDL chưa bị đụng): không khôi phục, trả
// compose.yaml về bản cũ (bản cũ để lại compose MỚI + container cũ) và up -d.
func TestRunUpdate_ObjectsCaptureFails_NoRestore_ComposeRestored(t *testing.T) {
	env, composePath := realComposeUpdateEnv(t, updateTestComposeYAML)

	fr := updateFakeRunner(
		fake.Response{Match: fake.MatchArgsContain("exec", "-T", "api", "sh", "-c"), Output: []byte("/tmp/gh-objects/docs/a.pdf")},
		fake.Response{Match: fake.MatchArgsContain("exec", "-T", "worker", "sh", "-c"), Output: []byte("")},
		fake.Response{Match: fake.MatchArgsContain("cp", "api:/tmp/gh-objects/."), Err: errors.New("container biến mất giữa chừng")},
	)

	var out strings.Builder
	err := RunUpdate(context.Background(), env, UpdateOptions{Version: testVersion}, fastUpdateDeps(fr), &out)
	opErr := asOpError(t, err)
	if opErr.Code != ErrCodeUpdateObjectsMigrateFailed {
		t.Errorf("Code = %q, muốn %q", opErr.Code, ErrCodeUpdateObjectsMigrateFailed)
	}
	if !strings.Contains(opErr.What, "CSDL chưa bị đụng") {
		t.Errorf("What phải nói CSDL chưa bị đụng: %q", opErr.What)
	}
	if countCalls(fr, matchRestore) != 0 || countCalls(fr, matchMigrate) != 0 {
		t.Errorf("không được restore/migrate: %+v", fr.Calls)
	}
	ui := callIndex(fr, matchUp)
	if ui < 0 || !hasExactArgs(fr.Calls[ui].Cmd.Args, "--remove-orphans") {
		t.Errorf("phải up -d --remove-orphans bằng compose cũ: %+v", fr.Calls)
	}
	after, _ := os.ReadFile(composePath)
	if string(after) != updateTestComposeYAML {
		t.Errorf("compose.yaml phải về bản cũ, được:\n%s", after)
	}
	if blockedExists(t, env.InstallDir) {
		t.Error("CSDL chưa bị đụng thì KHÔNG ghi update-blocked.json")
	}
}

func TestRunUpdate_SeedFailsAfterHealthy_ReportsErrorServiceStaysUpNoRollback(t *testing.T) {
	composePath := testComposePath(t, updateTestComposeYAML)
	env := testEnv(t, composePath)
	_, port := listenReadyServer(t, true)
	env.Port = port

	fr := updateHappyFakeRunner()
	fr.Responses = append(fr.Responses, legacyObjectsHappyResponses()...)
	fr.Responses = append(fr.Responses, fake.Response{
		Match: fake.MatchArgsContain("cp", "api:"+volumeObjectsDir), Err: errors.New("no such container"),
	})

	err := RunUpdate(context.Background(), env, UpdateOptions{}, fastUpdateDeps(fr), &strings.Builder{})
	opErr := asOpError(t, err)
	if opErr.Code != ErrCodeUpdateObjectsMigrateFailed {
		t.Errorf("Code = %q, muốn %q", opErr.Code, ErrCodeUpdateObjectsMigrateFailed)
	}
	if !strings.Contains(opErr.What, "sẵn sàng") {
		t.Errorf("What phải nói rõ dịch vụ ĐÃ sẵn sàng (chỉ chép vào volume lỗi, không phải cả update) — được %q", opErr.What)
	}
	if countCalls(fr, matchRestore) != 0 {
		t.Error("KHÔNG được rollback khi service đã healthy, chỉ bước chép vào volume thất bại")
	}
}

func TestRunUpdate_RollbackAfterUpReSeedsVolumeBeforeRestore(t *testing.T) {
	composePath := testComposePath(t, updateTestComposeYAML)
	env := testEnv(t, composePath)
	_, port := listenReadyServer(t, false) // luôn 503 -> waitReady thất bại -> rollback SAU khi up -d đã chạy
	env.Port = port

	fr := updateFakeRunner(pendingMigration)
	fr.Responses = append(fr.Responses, legacyObjectsHappyResponses()...)
	fr.Responses = append(fr.Responses, fake.Response{
		Match: fake.MatchArgsContain("cp", "api:"+volumeObjectsDir), Output: []byte(""),
	})
	fr.Responses = append(fr.Responses, fake.Response{
		Match: fake.MatchArgsContain("exec", "-u", "root", "-T", "api", "chown", "-R", "gh:gh", volumeObjectsDir), Output: []byte(""),
	})

	err := RunUpdate(context.Background(), env, UpdateOptions{}, fastUpdateDeps(fr), &strings.Builder{})
	opErr := asOpError(t, err)
	if opErr.Code != ErrCodeUpdateRolledBack {
		t.Errorf("Code = %q, muốn %q", opErr.Code, ErrCodeUpdateRolledBack)
	}

	seedIdx := callIndex(fr, func(c dockercli.Cmd) bool {
		joined := strings.Join(c.Args, " ")
		return strings.Contains(joined, "cp") && strings.Contains(joined, "api:"+volumeObjectsDir)
	})
	restoreIdx := callIndex(fr, matchRestore)
	stopIdx := lastCallIndex(fr, matchStop)
	if seedIdx == -1 || restoreIdx == -1 {
		t.Fatalf("rollback phải chép lại dữ liệu di trú vào volume rồi restore, Calls=%+v", fr.Calls)
	}
	if seedIdx > restoreIdx || (stopIdx >= 0 && seedIdx > stopIdx) {
		t.Errorf("chép vào volume (idx %d) phải chạy TRƯỚC khi dừng api (idx %d) và restore (idx %d)", seedIdx, stopIdx, restoreIdx)
	}
}

// TestRunUpdate_RollbackRestoresOldComposeBeforeUpAndRemovesOrphans: lỗi SAU
// đồng bộ compose.yaml (dịch vụ không bao giờ sẵn sàng) → rollback trả
// compose.yaml về đúng bản CŨ (từ bộ nhớ) TRƯỚC restore + `up -d
// --remove-orphans`.
func TestRunUpdate_RollbackRestoresOldComposeBeforeUpAndRemovesOrphans(t *testing.T) {
	env, composePath := realComposeUpdateEnv(t, updateTestComposeYAML)
	_, port := listenReadyServer(t, false)
	env.Port = port

	var composeAtRestore string
	fr := updateFakeRunner(pendingMigration, fake.Response{Match: func(cmd dockercli.Cmd) bool {
		if !matchRestore(cmd) {
			return false
		}
		b, _ := os.ReadFile(composePath)
		composeAtRestore = string(b)
		return true
	}})

	err := RunUpdate(context.Background(), env, UpdateOptions{}, fastUpdateDeps(fr), &strings.Builder{})
	opErr := asOpError(t, err)
	if opErr.Code != ErrCodeUpdateRolledBack {
		t.Errorf("Code = %q, muốn %q", opErr.Code, ErrCodeUpdateRolledBack)
	}
	if composeAtRestore != updateTestComposeYAML {
		t.Error("lúc restore, compose.yaml phải đã về bản CŨ (ảnh cũ)")
	}
	after, _ := os.ReadFile(composePath)
	if string(after) != updateTestComposeYAML {
		t.Errorf("rollback phải khôi phục compose.yaml về đúng bản CŨ, được:\n%s", after)
	}

	var rollbackUpArgs []string
	upCount := 0
	for _, c := range fr.Calls {
		if matchFullUp(c.Cmd) {
			upCount++
			rollbackUpArgs = c.Cmd.Args
		}
	}
	if upCount < 2 {
		t.Fatalf("phải có ít nhất 2 lần `up -d` (chính + rollback), được %d: %+v", upCount, fr.Calls)
	}
	if !hasExactArgs(rollbackUpArgs, "--remove-orphans") {
		t.Errorf("`up -d` của rollback phải có --remove-orphans, được %v", rollbackUpArgs)
	}
}

// compose.yaml KHÔNG đổi lần này (đã khớp bản nhúng) nhưng có .bak cũ từ lần
// trước: rollback KHÔNG được thay compose.yaml bằng .bak.
func TestRunUpdate_ComposeUnchanged_StaleBakNotRestoredOnRollback(t *testing.T) {
	embedded := string(compose.EmbeddedCompose())
	env, composePath := realComposeUpdateEnv(t, embedded)
	stale := "name: ban-rat-cu\nservices: {}\n"
	if err := os.WriteFile(composePath+".bak", []byte(stale), 0o644); err != nil {
		t.Fatal(err)
	}

	fr := updateFakeRunner(pendingMigration, fake.Response{Match: matchMigrate, Err: errors.New("alembic lỗi")})
	err := RunUpdate(context.Background(), env, UpdateOptions{Version: testVersion}, fastUpdateDeps(fr), &strings.Builder{})
	if asOpError(t, err).Code != ErrCodeUpdateRolledBack {
		t.Fatalf("muốn %s, được %v", ErrCodeUpdateRolledBack, err)
	}
	after, _ := os.ReadFile(composePath)
	if string(after) != embedded {
		t.Errorf("compose.yaml KHÔNG được bị thay bằng .bak cũ, được:\n%s", after)
	}
	if countCalls(fr, matchDBUp) != 0 {
		t.Error("compose không đổi (ảnh db không đổi) thì không cần dựng lại db trước khi khôi phục")
	}
	// Compose không đổi → pull bằng chính compose.yaml, không có compose tạm.
	pi := callIndex(fr, matchPull)
	if pi < 0 || fr.Calls[pi].Cmd.Args[2] != composePath {
		t.Errorf("compose không đổi thì pull bằng compose.yaml: %+v", fr.Calls)
	}
}

func TestRunUpdate_NeedsMigrate_StopsWorkerBridgeBeforeBackup(t *testing.T) {
	composePath := testComposePath(t, updateTestComposeYAML)
	env := testEnv(t, composePath)
	_, port := listenReadyServer(t, true)
	env.Port = port

	fr := updateFakeRunner(fake.Response{Match: matchAlembicCurrent, Output: []byte("0041_x\n")})
	var out strings.Builder
	if err := RunUpdate(context.Background(), env, UpdateOptions{}, fastUpdateDeps(fr), &out); err != nil {
		t.Fatalf("RunUpdate: %v", err)
	}
	si, bi := callIndex(fr, matchStop), callIndex(fr, matchBackupRun)
	if si < 0 || si > bi {
		t.Fatalf("phải dừng worker/bridge TRƯỚC backup: stop=%d backup=%d", si, bi)
	}
	if got := strings.Join(fr.Calls[si].Cmd.Args, " "); !strings.HasSuffix(got, "stop worker bridge") || fr.Calls[si].Cmd.Args[2] != composePath {
		t.Errorf("phải `compose -f <compose.yaml> stop worker bridge`, được %q", got)
	}
	if !strings.Contains(out.String(), "tạm dừng worker và bridge") {
		t.Errorf("phải báo tạm dừng worker và bridge: %q", out.String())
	}
	ai := callIndex(fr, matchAlembicCurrent)
	if ai < 0 || !strings.HasSuffix(fr.Calls[ai].Cmd.Args[2], updateNextComposeName) {
		t.Errorf("alembic current phải chạy bằng ảnh MỚI (compose tạm): %+v", fr.Calls)
	}
}

func TestRunUpdate_NoPendingMigration_DoesNotStopWriters(t *testing.T) {
	composePath := testComposePath(t, updateTestComposeYAML)
	env := testEnv(t, composePath)
	_, port := listenReadyServer(t, true)
	env.Port = port

	fr := updateFakeRunner(fake.Response{Match: matchAlembicCurrent, Output: []byte("0042_x (head)\n")})
	if err := RunUpdate(context.Background(), env, UpdateOptions{}, fastUpdateDeps(fr), &strings.Builder{}); err != nil {
		t.Fatalf("RunUpdate: %v", err)
	}
	if countCalls(fr, matchStop) != 0 {
		t.Errorf("không có migration chờ thì không dừng worker/bridge: %+v", fr.Calls)
	}
}

func TestRunUpdate_AlembicCheckFails_AssumesMigrate(t *testing.T) {
	composePath := testComposePath(t, updateTestComposeYAML)
	env := testEnv(t, composePath)
	_, port := listenReadyServer(t, true)
	env.Port = port

	fr := updateFakeRunner(fake.Response{Match: matchAlembicCurrent, Err: errors.New("không kết nối được db")})
	if err := RunUpdate(context.Background(), env, UpdateOptions{}, fastUpdateDeps(fr), &strings.Builder{}); err != nil {
		t.Fatalf("RunUpdate: %v", err)
	}
	si, bi := callIndex(fr, matchStop), callIndex(fr, matchBackupRun)
	if si < 0 || si > bi {
		t.Errorf("dò migrate lỗi thì coi như có (an toàn): phải dừng worker/bridge trước backup, stop=%d backup=%d", si, bi)
	}
}

func TestRunUpdate_BackupFailsAfterStoppingWriters_StartsThemAgain(t *testing.T) {
	composePath := testComposePath(t, updateTestComposeYAML)
	env := testEnv(t, composePath)

	fr := updateFakeRunner(
		fake.Response{Match: matchAlembicCurrent, Output: []byte("0041_x\n")},
		fake.Response{Match: matchBackupRun, Err: errors.New("db down")},
	)
	err := RunUpdate(context.Background(), env, UpdateOptions{}, fastUpdateDeps(fr), &strings.Builder{})
	if asOpError(t, err).Code != ErrCodeUpdateBackupFailed {
		t.Fatalf("muốn %s, được %v", ErrCodeUpdateBackupFailed, err)
	}
	sti := callIndex(fr, matchStart)
	if sti < 0 || !strings.HasSuffix(strings.Join(fr.Calls[sti].Cmd.Args, " "), "start worker bridge") {
		t.Errorf("phải `start worker bridge` lại: %+v", fr.Calls)
	}
	if countCalls(fr, matchRestore) != 0 || countCalls(fr, matchUp) != 0 {
		t.Error("backup lỗi thì không restore/up")
	}
}

// ghcrCompose dựng compose dùng ảnh ghcr.io gen-harness theo digest.
func ghcrCompose(api, web, db string) string {
	return "name: gen-harness\nservices:\n" +
		"  api:\n    image: ghcr.io/acme/gen-harness-api@" + api + "\n" +
		"  web:\n    image: ghcr.io/acme/gen-harness-web@" + web + "\n" +
		"  db:\n    image: ghcr.io/acme/gen-harness-db@" + db + "\n" +
		"  redis:\n    image: redis:7-alpine\n"
}

// unmanagedEnv: compose.yaml NGOÀI gốc cài đặt (GENH_COMPOSE_FILE/checkout) —
// genh không đồng bộ nó, bản đích = chính nó.
func unmanagedEnv(t *testing.T, yaml string) (*Env, string) {
	t.Helper()
	composePath := testComposePath(t, yaml)
	installDir := t.TempDir()
	if _, err := secretgen.Ensure(filepath.Join(installDir, "config")); err != nil {
		t.Fatalf("secretgen.Ensure: %v", err)
	}
	return &Env{InstallDir: installDir, Port: 18443, locate: func(string) (string, error) { return composePath, nil }}, composePath
}

func imagesListing(lines ...string) []byte { return []byte(strings.Join(lines, "\n") + "\n") }

func TestRunUpdate_DiskLow_PrunesThenStops(t *testing.T) {
	env, _ := unmanagedEnv(t, ghcrCompose("sha256:a3", "sha256:w3", "sha256:d3"))

	fr := updateFakeRunner(
		fake.Response{Match: exactArgs("images"), Output: imagesListing(
			"ghcr.io/acme/gen-harness-api\t<none>\tsha256:a3\tid-a3",
			"ghcr.io/acme/gen-harness-api\t<none>\tsha256:a1\tid-a1",
			"postgres\t16\tsha256:pg\tid-pg",
		)},
		fake.Response{Match: exactArgs("rmi"), Output: []byte("")},
	)
	deps := fastUpdateDeps(fr)
	deps.DiskFree = func(string) (uint64, error) { return 1 << 30, nil }

	err := RunUpdate(context.Background(), env, UpdateOptions{}, deps, &strings.Builder{})
	opErr := asOpError(t, err)
	if opErr.Code != ErrCodeUpdateDiskLow {
		t.Fatalf("Code = %q, muốn %q", opErr.Code, ErrCodeUpdateDiskLow)
	}
	if !strings.Contains(opErr.Why, "1.0 GB") || !strings.Contains(opErr.Why, "5 GB") {
		t.Errorf("Why phải nêu còn bao nhiêu / cần 5 GB: %q", opErr.Why)
	}
	if countCalls(fr, exactArgs("images")) == 0 {
		t.Error("đĩa thiếu thì phải liệt kê ảnh để dọn")
	}
	ri := callIndex(fr, exactArgs("rmi"))
	if ri < 0 || fr.Calls[ri].Cmd.Args[1] != "ghcr.io/acme/gen-harness-api@sha256:a1" || countCalls(fr, exactArgs("rmi")) != 1 {
		t.Errorf("chỉ được rmi ảnh không thuộc bản giữ: %+v", fr.Calls)
	}
	if countCalls(fr, matchPull) != 0 || countCalls(fr, matchBackupRun) != 0 {
		t.Error("đĩa thiếu thì KHÔNG pull/backup")
	}
	ds, err := hostlink.ReadDiskStatus(env.InstallDir)
	if err != nil || ds.State != "low" || ds.FreeBytes != 1<<30 || ds.PrunedImages != 1 {
		t.Errorf("disk-status.json phải state=low: %+v (%v)", ds, err)
	}
}

func TestRunUpdate_DiskLow_PruneFreesEnough_Continues(t *testing.T) {
	env, _ := unmanagedEnv(t, ghcrCompose("sha256:a3", "sha256:w3", "sha256:d3"))
	_, port := listenReadyServer(t, true)
	env.Port = port

	fr := updateFakeRunner(
		fake.Response{Match: exactArgs("images"), Output: imagesListing("ghcr.io/acme/gen-harness-api\t<none>\tsha256:a1\tid-a1")},
		fake.Response{Match: exactArgs("rmi"), Output: []byte("")},
	)
	deps := fastUpdateDeps(fr)
	var calls int32
	deps.DiskFree = func(string) (uint64, error) {
		if atomic.AddInt32(&calls, 1) == 1 {
			return 1 << 30, nil
		}
		return 50 << 30, nil
	}
	if err := RunUpdate(context.Background(), env, UpdateOptions{}, deps, &strings.Builder{}); err != nil {
		t.Fatalf("RunUpdate: %v", err)
	}
	if countCalls(fr, matchPull) != 1 || countCalls(fr, matchBackupRun) != 1 {
		t.Errorf("dọn xong đủ chỗ thì phải tải + sao lưu tiếp: %+v", fr.Calls)
	}
	ds, _ := hostlink.ReadDiskStatus(env.InstallDir)
	if ds.State != "ok" || ds.FreeBytes != 50<<30 {
		t.Errorf("disk-status.json phải state=ok sau khi dọn: %+v", ds)
	}
}

func TestRunUpdate_DiskProbeFails_WarnsAndContinues(t *testing.T) {
	composePath := testComposePath(t, updateTestComposeYAML)
	env := testEnv(t, composePath)
	_, port := listenReadyServer(t, true)
	env.Port = port

	fr := updateFakeRunner()
	deps := fastUpdateDeps(fr)
	deps.DiskFree = func(string) (uint64, error) { return 0, errors.New("statfs lỗi") }
	var out strings.Builder
	if err := RunUpdate(context.Background(), env, UpdateOptions{}, deps, &out); err != nil {
		t.Fatalf("đo đĩa lỗi không được chặn cập nhật: %v", err)
	}
	if !strings.Contains(out.String(), "không đo được chỗ trống") {
		t.Errorf("phải cảnh báo không đo được: %q", out.String())
	}
}

func TestRunUpdate_Success_ClearsBlockedAndPrunesKeepingTwo(t *testing.T) {
	env, composePath := unmanagedEnv(t, ghcrCompose("sha256:a3", "sha256:w3", "sha256:d3"))
	if err := os.WriteFile(composePath+".bak", []byte(ghcrCompose("sha256:a2", "sha256:w2", "sha256:d2")), 0o644); err != nil {
		t.Fatal(err)
	}
	if err := hostlink.WriteUpdateBlocked(env.InstallDir, hostlink.UpdateBlocked{Version: "v0.1.33"}); err != nil {
		t.Fatal(err)
	}
	_, port := listenReadyServer(t, true)
	env.Port = port

	fr := updateFakeRunner(
		fake.Response{Match: exactArgs("images"), Output: imagesListing(
			"ghcr.io/acme/gen-harness-api\t<none>\tsha256:a3\tid-a3",
			"ghcr.io/acme/gen-harness-api\t<none>\tsha256:a2\tid-a2",
			"ghcr.io/acme/gen-harness-api\t<none>\tsha256:a1\tid-a1",
			"ghcr.io/acme/gen-harness-web\t<none>\tsha256:w1\tid-w1",
			"ghcr.io/acme/gen-harness-web\t<none>\tsha256:w2\tid-w2",
		)},
		fake.Response{Match: exactArgs("rmi"), Output: []byte("")},
	)
	var out strings.Builder
	if err := RunUpdate(context.Background(), env, UpdateOptions{Version: testVersion}, fastUpdateDeps(fr), &out); err != nil {
		t.Fatalf("RunUpdate: %v", err)
	}
	if blockedExists(t, env.InstallDir) {
		t.Error("cập nhật thành công phải xoá update-blocked.json")
	}
	var removed []string
	for _, c := range fr.Calls {
		if exactArgs("rmi")(c.Cmd) {
			removed = append(removed, c.Cmd.Args[1])
		}
	}
	want := []string{"ghcr.io/acme/gen-harness-api@sha256:a1", "ghcr.io/acme/gen-harness-web@sha256:w1"}
	if strings.Join(removed, ",") != strings.Join(want, ",") {
		t.Errorf("rmi = %v, muốn %v", removed, want)
	}
	if !strings.Contains(out.String(), "Đã dọn 2 ảnh cũ (giữ bản hiện tại và bản liền trước).") {
		t.Errorf("phải báo đã dọn: %q", out.String())
	}
}

func TestRunBackupInContainer_IsRestarting_FallsBackToRunRm(t *testing.T) {
	composePath := testComposePath(t, "")
	fr := &fake.Runner{Responses: []fake.Response{
		{Match: fake.MatchArgsContain("exec", "-T", "api", "python", "-m", "gh.backup", "run"), Err: errors.New("Error response from daemon: Container abc is restarting, wait until the container is running")},
		{Match: fake.MatchArgsContain("run", "--rm", "--no-deps", "-T", "api", "python", "-m", "gh.backup", "run"), Lines: []string{updateTestBackupLine}},
	}}
	key, err := runBackupInContainer(context.Background(), fr, composePath, nil, filepath.Dir(composePath), BackupTriggerPreUpdate)
	if err != nil {
		t.Fatalf("runBackupInContainer: %v", err)
	}
	if key != updateTestBackupKey {
		t.Errorf("key = %q", key)
	}
	if len(fr.Calls) != 2 || !hasExactArgs(fr.Calls[1].Cmd.Args, "run", "--rm", "--no-deps", "-T", "api") {
		t.Errorf("phải rơi về `run --rm --no-deps -T api …`: %+v", fr.Calls)
	}
}

func TestPendingMigrationFromCurrent(t *testing.T) {
	cases := []struct {
		out  string
		want bool
	}{
		{"0042_x (head)\n", false},
		{"INFO  [alembic.runtime.migration] Context impl.\n0042_x (head)\n", false},
		{"0042_a (head)\n0042_b (head)\n", false},
		{"0041_x\n", true},
		{"0042_a (head)\n0040_b\n", true},
		{"", true},
		{"INFO  [alembic.runtime.migration] Will assume transactional DDL.\n", true},
	}
	for _, c := range cases {
		if got := pendingMigrationFromCurrent(c.out); got != c.want {
			t.Errorf("pendingMigrationFromCurrent(%q) = %v, muốn %v", c.out, got, c.want)
		}
	}
}

// Review v0.1.34: bản mới thêm head RIÊNG (nhánh/gốc mới) — `alembic current`
// vẫn in "(head)" cho nhánh cũ; phải so với `alembic heads` của ảnh mới.
func TestNeedsMigration_ComparesWithHeads(t *testing.T) {
	cases := []struct {
		name     string
		current  string
		heads    string
		headsErr error
		want     bool
	}{
		{"khớp", "0042_x (head)\n", "0042_x (head)\n", nil, false},
		{"bản mới thêm head riêng", "0042_x (head)\n", "0042_x (head)\n0001_new (head)\n", nil, true},
		{"nhiều head, khớp không theo thứ tự", "0042_a (head)\n0042_b (head)\n", "INFO  [alembic] x\n0042_b (head)\n0042_a (head)\n", nil, false},
		{"heads lỗi → theo riêng current", "0042_x (head)\n", "", errors.New("lỗi"), false},
		{"current chưa ở head → khỏi hỏi heads", "0041_x\n", "0042_x (head)\n", nil, true},
	}
	for _, c := range cases {
		fr := &fake.Runner{Responses: []fake.Response{
			{Match: matchAlembicCurrent, Output: []byte(c.current)},
			{Match: matchAlembicHeads, Output: []byte(c.heads), Err: c.headsErr},
		}}
		if got := needsMigration(context.Background(), fr, "/x/compose.yaml", nil, "/x"); got != c.want {
			t.Errorf("%s: needsMigration = %v, muốn %v", c.name, got, c.want)
		}
	}
}

func TestPullWithRetry_RespectsContextDuringBackoff(t *testing.T) {
	fr := &fake.Runner{Responses: []fake.Response{{Match: matchPull, Err: errors.New("net down")}}}
	ctx, cancel := context.WithTimeout(context.Background(), 50*time.Millisecond)
	defer cancel()
	start := time.Now()
	_, err := pullWithRetry(ctx, fr, dockercli.Cmd{Name: "docker", Args: []string{"compose", "pull"}},
		UpdateDeps{PullBackoff: []time.Duration{time.Hour}}, &strings.Builder{})
	if err == nil {
		t.Fatal("muốn lỗi")
	}
	if time.Since(start) > 5*time.Second {
		t.Errorf("chờ giữa các lần phải dừng khi ctx huỷ, mất %s", time.Since(start))
	}
	if len(fr.Calls) != 1 {
		t.Errorf("ctx huỷ trong lúc chờ thì không thử lần 2, Calls=%d", len(fr.Calls))
	}
}

func TestUpdateNeeded(t *testing.T) {
	env, composePath := realComposeUpdateEnv(t, updateTestComposeYAML)
	if ok, err := UpdateNeeded(env); err != nil || ok {
		t.Fatalf("compose lệch bản nhúng: muốn false,nil — được %v,%v", ok, err)
	}
	if err := os.WriteFile(composePath, compose.EmbeddedCompose(), 0o644); err != nil {
		t.Fatal(err)
	}
	// LocatePath (không đồng bộ) đã ghi Caddyfile nhúng khi thiếu.
	if ok, err := UpdateNeeded(env); err != nil || !ok {
		t.Fatalf("compose khớp bản nhúng: muốn true,nil — được %v,%v", ok, err)
	}
}

func TestResolveUpdateServices_SplitsPullableAndSkipped(t *testing.T) {
	composePath := testComposePath(t, updateTestComposeYAML)
	cf, err := compose.Load(composePath)
	if err != nil {
		t.Fatalf("compose.Load: %v", err)
	}
	pullable, skipped := resolveUpdateServices(cf)

	wantPullable := map[string]bool{"proxy": true, "redis": true}
	for _, p := range pullable {
		if !wantPullable[p] {
			t.Errorf("pullable chứa %q không mong đợi", p)
		}
		delete(wantPullable, p)
	}
	if len(wantPullable) != 0 {
		t.Errorf("thiếu trong pullable: %v", wantPullable)
	}

	wantSkipped := map[string]bool{"api": true, "web": true, "bridge": true, "db": true, "browser": true, "agy-box": true, "agy-egress": true}
	for _, s := range skipped {
		if !wantSkipped[s] {
			t.Errorf("skipped chứa %q không mong đợi", s)
		}
		delete(wantSkipped, s)
	}
	if len(wantSkipped) != 0 {
		t.Errorf("thiếu trong skipped: %v", wantSkipped)
	}
}

func TestRunUpdate_Success_ReTrustsCA_FailureDoesNot(t *testing.T) {
	for _, healthy := range []bool{true, false} {
		composePath := testComposePath(t, updateTestComposeYAML)
		env := testEnv(t, composePath)
		_, port := listenReadyServer(t, healthy)
		env.Port = port

		fr := updateFakeRunner()
		deps := fastUpdateDeps(fr)
		called := 0
		deps.ReTrustCA = func(context.Context, *Env) { called++ }

		_ = RunUpdate(context.Background(), env, UpdateOptions{Channel: "stable"}, deps, &strings.Builder{})
		want := 0
		if healthy {
			want = 1
		}
		if called != want {
			t.Errorf("healthy=%v: ReTrustCA gọi %d lần, muốn %d", healthy, called, want)
		}
	}
}

// F-xx (review v0.1.34): lỗi SAU migrate mà KHÔNG có migration chờ — worker/
// bridge/api vẫn ghi suốt từ lúc sao lưu: KHÔNG được khôi phục CSDL (mất dữ
// liệu), chỉ trả compose.yaml + up -d; vẫn chặn lịch đêm (bản hỏng).
func TestRunUpdate_ReadyFails_NoPendingMigration_NoRestore_StillBlocked(t *testing.T) {
	env, composePath := realComposeUpdateEnv(t, updateTestComposeYAML)
	_, port := listenReadyServer(t, false)
	env.Port = port

	fr := updateFakeRunner() // alembic current: đã ở head
	var out strings.Builder
	err := RunUpdate(context.Background(), env, UpdateOptions{Version: testVersion}, fastUpdateDeps(fr), &out)
	opErr := asOpError(t, err)
	if opErr.Code != ErrCodeUpdateRolledBack {
		t.Errorf("Code = %q, muốn %q", opErr.Code, ErrCodeUpdateRolledBack)
	}
	if n := countCalls(fr, matchRestore); n != 0 {
		t.Errorf("không có migration chờ thì KHÔNG được khôi phục CSDL, restore=%d", n)
	}
	if n := countCalls(fr, matchStop); n != 0 {
		t.Errorf("không có migration chờ thì không dừng gì, stop=%d", n)
	}
	if n := countCalls(fr, matchFullUp); n != 2 {
		t.Errorf("phải up -d --remove-orphans 2 lần (bản mới + quay về), được %d", n)
	}
	after, _ := os.ReadFile(composePath)
	if string(after) != updateTestComposeYAML {
		t.Error("compose.yaml phải về bản cũ")
	}
	b, ok, _ := hostlink.ReadUpdateBlocked(env.InstallDir)
	if !ok || b.Version != testVersion || b.Code != ErrCodeUpdateNotReady || b.RollbackFailed {
		t.Errorf("bản hỏng vẫn phải chặn lịch đêm: ok=%v %+v", ok, b)
	}
	if b.BackupKey != "" || b.DBTouched {
		t.Errorf("CSDL chưa bị đụng: KHÔNG ghi bản sao lưu cần khôi phục (khôi phục = mất dữ liệu): %+v", b)
	}
	if !strings.Contains(opErr.What, "CSDL chưa bị đụng") || !strings.Contains(opErr.Next, "lịch đêm sẽ không tự thử lại") {
		t.Errorf("What/Next sai: %q / %q", opErr.What, opErr.Next)
	}
	if !strings.Contains(out.String(), "rollback") {
		t.Errorf("log phải nhắc rollback (E2E grep): %q", out.String())
	}
	if hostlink.UpdateInProgressExists(env.InstallDir) {
		t.Error("đã trả compose.yaml về bản cũ và chạy lại được thì phải xoá update-inprogress.json")
	}
}

// Review v0.1.34: lỗi sau migrate, KHÔNG có migration chờ, và khởi động lại bằng
// bản cũ CŨNG lỗi — update-blocked.json không được mang bản sao lưu (hướng dẫn
// xử lý tay chỉ là up -d, không khôi phục), nhưng phải rollback_failed.
func TestRunUpdate_ReadyFails_NoPendingMigration_RestartAlsoFails_NoBackupKey(t *testing.T) {
	env, _ := realComposeUpdateEnv(t, updateTestComposeYAML)
	_, port := listenReadyServer(t, false)
	env.Port = port

	fr := updateFakeRunner(fake.Response{Match: matchFullUp, ErrSeq: []error{nil, errors.New("không khởi động được")}})
	err := RunUpdate(context.Background(), env, UpdateOptions{Version: testVersion}, fastUpdateDeps(fr), &strings.Builder{})
	opErr := asOpError(t, err)
	if opErr.Code != ErrCodeUpdateRolledBack {
		t.Errorf("Code = %q, muốn %q", opErr.Code, ErrCodeUpdateRolledBack)
	}
	if countCalls(fr, matchRestore) != 0 {
		t.Error("CSDL chưa bị đụng thì không khôi phục")
	}
	b, ok, _ := hostlink.ReadUpdateBlocked(env.InstallDir)
	if !ok || !b.RollbackFailed || b.DBTouched || b.BackupKey != "" {
		t.Errorf("muốn rollback_failed=true, db_touched=false, không backup_key: ok=%v %+v", ok, b)
	}
	if strings.Contains(opErr.Next, "gh.backup restore") {
		t.Errorf("Next không được bảo khôi phục bản sao lưu: %q", opErr.Next)
	}
	if !hostlink.UpdateInProgressExists(env.InstallDir) {
		t.Error("quay về chưa trọn: phải GIỮ update-inprogress.json")
	}
}

// Review v0.1.34: nhánh c) (đã đụng CSDL) quay về ổn + compose.yaml đã trả về
// bản cũ → xoá update-inprogress.json như nhánh b).
func TestRunUpdate_ReadyFails_PendingMigration_RolledBack_ClearsInProgress(t *testing.T) {
	env, composePath := realComposeUpdateEnv(t, updateTestComposeYAML)
	_, port := listenReadyServer(t, false)
	env.Port = port

	fr := updateFakeRunner(pendingMigration)
	err := RunUpdate(context.Background(), env, UpdateOptions{Version: testVersion}, fastUpdateDeps(fr), &strings.Builder{})
	if asOpError(t, err).Code != ErrCodeUpdateRolledBack {
		t.Fatalf("muốn %s, được %v", ErrCodeUpdateRolledBack, err)
	}
	if countCalls(fr, matchRestore) != 1 {
		t.Error("đã đụng CSDL thì phải khôi phục")
	}
	if after, _ := os.ReadFile(composePath); string(after) != updateTestComposeYAML {
		t.Error("compose.yaml phải về bản cũ")
	}
	if hostlink.UpdateInProgressExists(env.InstallDir) {
		t.Error("quay về ổn + compose.yaml đã về bản cũ thì phải xoá update-inprogress.json")
	}
	if b, ok, _ := hostlink.ReadUpdateBlocked(env.InstallDir); !ok || !b.DBTouched || b.BackupKey != updateTestBackupKey {
		t.Errorf("đã đụng CSDL: phải ghi db_touched + backup_key: ok=%v %+v", ok, b)
	}
}

// Dấu cập nhật dở: ghi TRƯỚC khi đổi compose.yaml, xoá khi đã sẵn sàng.
func TestRunUpdate_InProgressMarker_WrittenBeforeSyncClearedOnSuccess(t *testing.T) {
	env, composePath := realComposeUpdateEnv(t, updateTestComposeYAML)
	_, port := listenReadyServer(t, true)
	env.Port = port

	markerAtMigrate, composeAtMigrate := false, ""
	fr := updateFakeRunner(fake.Response{Match: func(cmd dockercli.Cmd) bool {
		if !matchMigrate(cmd) {
			return false
		}
		markerAtMigrate = hostlink.UpdateInProgressExists(env.InstallDir)
		b, _ := os.ReadFile(composePath)
		composeAtMigrate = string(b)
		return true
	}, Lines: []string{}})
	if err := RunUpdate(context.Background(), env, UpdateOptions{Version: testVersion}, fastUpdateDeps(fr), &strings.Builder{}); err != nil {
		t.Fatalf("RunUpdate: %v", err)
	}
	if !markerAtMigrate || composeAtMigrate != string(compose.EmbeddedCompose()) {
		t.Errorf("lúc migrate (compose.yaml đã là bản mới) phải có update-inprogress.json: marker=%v", markerAtMigrate)
	}
	if hostlink.UpdateInProgressExists(env.InstallDir) {
		t.Error("cập nhật xong phải xoá update-inprogress.json")
	}
}

// genh chết sau khi đã ghi compose.yaml mới (còn dấu dở) → lần sau KHÔNG được
// coi "đã khớp" dù compose.yaml + Caddyfile trùng bản nhúng.
func TestUpdateNeeded_InProgressMarker_NotInSync(t *testing.T) {
	env, composePath := realComposeUpdateEnv(t, string(compose.EmbeddedCompose()))
	if ok, err := UpdateNeeded(env); err != nil || !ok {
		t.Fatalf("compose khớp, không có dấu dở: muốn true — được %v,%v (%s)", ok, err, composePath)
	}
	if err := hostlink.MarkUpdateInProgress(env.InstallDir, hostlink.UpdateInProgress{Version: testVersion}); err != nil {
		t.Fatal(err)
	}
	if ok, err := UpdateNeeded(env); err != nil || ok {
		t.Fatalf("còn update-inprogress.json: muốn false,nil — được %v,%v", ok, err)
	}
}

// GENH_COMPOSE_FILE / checkout (compose ngoài gốc cài đặt): không bao giờ "đã
// khớp" — `genh update` gõ tay / "Cập nhật ngay" luôn chạy RunUpdate đủ bước.
func TestUpdateNeeded_UnmanagedCompose_AlwaysRuns(t *testing.T) {
	env, _ := unmanagedEnv(t, string(compose.EmbeddedCompose()))
	if ok, err := UpdateNeeded(env); err != nil || ok {
		t.Fatalf("compose ngoài (trùng bản nhúng): muốn false,nil — được %v,%v", ok, err)
	}
}

// Dọn ảnh chỉ trong đúng repo (owner/tên) của bản giữ — không đụng ảnh
// gen-harness-* của owner khác dùng chung Docker.
func TestPruneOldImages_OnlyKeptRepos(t *testing.T) {
	fr := &fake.Runner{Responses: []fake.Response{
		{Match: exactArgs("images"), Output: imagesListing(
			"ghcr.io/acme/gen-harness-api\t<none>\tsha256:a3\tid-a3",
			"ghcr.io/acme/gen-harness-api\t<none>\tsha256:a1\tid-a1",
			"ghcr.io/other/gen-harness-api\t<none>\tsha256:o1\tid-o1",
			"ghcr.io/acme/gen-harness-worker\tdev\t<none>\tid-wk",
		)},
		{Match: exactArgs("rmi"), Output: []byte("")},
	}}
	n, err := pruneOldImages(context.Background(), fr, [][]byte{[]byte(ghcrCompose("sha256:a3", "sha256:w3", "sha256:d3"))}, &strings.Builder{})
	if err != nil || n != 1 {
		t.Fatalf("muốn dọn đúng 1 ảnh, được %d (%v)", n, err)
	}
	ri := callIndex(fr, exactArgs("rmi"))
	if fr.Calls[ri].Cmd.Args[1] != "ghcr.io/acme/gen-harness-api@sha256:a1" {
		t.Errorf("chỉ được rmi ảnh cũ cùng repo bản giữ: %+v", fr.Calls)
	}
}

// ─── v0.1.37 F-34: tín hiệu dừng giữa chừng ─────────────────────────────────

// ctxCall ghi một lệnh cùng ctx.Err() ĐÚNG LÚC lệnh được gọi.
type ctxCall struct {
	cmd         dockercli.Cmd
	ctxErr      error
	hasDeadline bool
}

// ctxRecRunner bọc fake.Runner: ghi ctx.Err() của từng lệnh; before (nếu có)
// chạy trước khi giao cho fake — trả lỗi khác nil thì lệnh "lỗi" bằng lỗi đó
// (dùng để giả lập SIGTERM: gọi cancel() rồi trả context.Canceled).
type ctxRecRunner struct {
	*fake.Runner
	mu     sync.Mutex
	calls  []ctxCall
	before func(dockercli.Cmd) error
}

func (r *ctxRecRunner) rec(ctx context.Context, cmd dockercli.Cmd) error {
	r.mu.Lock()
	_, dl := ctx.Deadline()
	r.calls = append(r.calls, ctxCall{cmd: cmd, ctxErr: ctx.Err(), hasDeadline: dl})
	r.mu.Unlock()
	if r.before != nil {
		return r.before(cmd)
	}
	return nil
}

func (r *ctxRecRunner) Output(ctx context.Context, cmd dockercli.Cmd) ([]byte, error) {
	if err := r.rec(ctx, cmd); err != nil {
		return nil, err
	}
	return r.Runner.Output(ctx, cmd)
}

func (r *ctxRecRunner) Stream(ctx context.Context, cmd dockercli.Cmd, onLine func(string)) error {
	if err := r.rec(ctx, cmd); err != nil {
		return err
	}
	return r.Runner.Stream(ctx, cmd, onLine)
}

// indexAfter: chỉ số lệnh đầu tiên SAU from khớp match (-1 nếu không có).
func (r *ctxRecRunner) indexAfter(from int, match func(dockercli.Cmd) bool) int {
	for i := from + 1; i < len(r.calls); i++ {
		if match(r.calls[i].cmd) {
			return i
		}
	}
	return -1
}

func (r *ctxRecRunner) count(match func(dockercli.Cmd) bool) int {
	n := 0
	for _, c := range r.calls {
		if match(c.cmd) {
			n++
		}
	}
	return n
}

// cancelOn: before-hook huỷ ctx và trả context.Canceled ở lệnh khớp match.
func cancelOn(match func(dockercli.Cmd) bool, cancel context.CancelFunc) func(dockercli.Cmd) error {
	return func(cmd dockercli.Cmd) error {
		if match(cmd) {
			cancel()
			return context.Canceled
		}
		return nil
	}
}

func TestRunUpdate_CtxHuyOMigrate_RollbackVanChayRestoreVaUp(t *testing.T) {
	composePath := testComposePath(t, updateTestComposeYAML)
	env := testEnv(t, composePath)
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	rr := &ctxRecRunner{Runner: updateFakeRunner(pendingMigration)}
	rr.before = cancelOn(matchMigrate, cancel)

	var out strings.Builder
	err := RunUpdate(ctx, env, UpdateOptions{Version: testVersion}, fastUpdateDeps(rr), &out)
	opErr := asOpError(t, err)
	if opErr.Code != ErrCodeUpdateInterrupted {
		t.Fatalf("Code = %q, muốn %q (%v)\n%s", opErr.Code, ErrCodeUpdateInterrupted, err, out.String())
	}
	if !strings.Contains(opErr.What, "dừng giữa chừng") || !strings.Contains(opErr.What, "đã tự quay về bản cũ") {
		t.Errorf("What phải nói dừng giữa chừng + đã quay về: %q", opErr.What)
	}
	mi := rr.indexAfter(-1, matchMigrate)
	if mi < 0 {
		t.Fatal("chưa chạy migrate")
	}
	ri := rr.indexAfter(mi, matchRestore)
	if ri < 0 {
		t.Fatalf("huỷ ở migrate vẫn phải chạy `gh.backup restore`: %+v", rr.calls)
	}
	if !hasExactArgs(rr.calls[ri].cmd.Args, "restore", "--key", updateTestBackupKey) {
		t.Errorf("restore phải dùng đúng --key bản sao lưu: %v", rr.calls[ri].cmd.Args)
	}
	if rr.calls[ri].ctxErr != nil {
		t.Errorf("restore phải chạy bằng ngữ cảnh KHÔNG bị huỷ, ctx.Err()=%v", rr.calls[ri].ctxErr)
	}
	ui := rr.indexAfter(ri, matchFullUp)
	if ui < 0 || rr.calls[ui].ctxErr != nil {
		t.Fatalf("phải `up -d --remove-orphans` sau restore bằng ngữ cảnh không bị huỷ: %+v", rr.calls)
	}
	for i := mi + 1; i < len(rr.calls); i++ {
		if rr.calls[i].ctxErr != nil {
			t.Errorf("lệnh quay về %v chạy với ctx đã huỷ", rr.calls[i].cmd.Args)
		}
	}
	if blockedExists(t, env.InstallDir) {
		t.Error("bị dừng do tín hiệu thì KHÔNG ghi update-blocked.json (lịch đêm phải thử lại)")
	}
	if hostlink.UpdateInProgressExists(env.InstallDir) {
		t.Error("quay về ổn thì update-inprogress.json phải được xoá")
	}
}

// listenHandlerServer như listenReadyServer nhưng nhận handler tuỳ ý.
func listenHandlerServer(t *testing.T, h http.HandlerFunc) int {
	t.Helper()
	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatalf("net.Listen: %v", err)
	}
	srv := httptest.NewUnstartedServer(h)
	srv.Listener = ln
	srv.TLS = &tls.Config{}
	srv.StartTLS()
	t.Cleanup(srv.Close)
	return ln.Addr().(*net.TCPAddr).Port
}

func TestRunUpdate_CtxHuyOReady_KhongDungCsdl_VanUp(t *testing.T) {
	composePath := testComposePath(t, updateTestComposeYAML)
	env := testEnv(t, composePath)
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	env.Port = listenHandlerServer(t, func(w http.ResponseWriter, _ *http.Request) {
		cancel() // SIGTERM tới đúng lúc đang chờ sẵn sàng
		w.WriteHeader(http.StatusServiceUnavailable)
	})
	rr := &ctxRecRunner{Runner: updateFakeRunner()} // không có migration chờ ⇒ dbTouched=false

	deps := fastUpdateDeps(rr)
	deps.Timeout = 5 * time.Second
	var out strings.Builder
	err := RunUpdate(ctx, env, UpdateOptions{Version: testVersion}, deps, &out)
	opErr := asOpError(t, err)
	if opErr.Code != ErrCodeUpdateInterrupted {
		t.Fatalf("Code = %q, muốn %q (%v)\n%s", opErr.Code, ErrCodeUpdateInterrupted, err, out.String())
	}
	if rr.count(matchRestore) != 0 {
		t.Error("CSDL chưa bị đụng — KHÔNG được khôi phục")
	}
	upIdx := -1
	for i, c := range rr.calls {
		if matchFullUp(c.cmd) {
			upIdx = i
		}
	}
	if rr.count(matchFullUp) < 2 || upIdx < 0 || rr.calls[upIdx].ctxErr != nil {
		t.Fatalf("phải `up -d` lại bằng compose cũ với ngữ cảnh không bị huỷ: %+v", rr.calls)
	}
	if b, _ := os.ReadFile(composePath); string(b) != updateTestComposeYAML {
		t.Error("compose.yaml phải được trả về bản cũ")
	}
	if blockedExists(t, env.InstallDir) {
		t.Error("bị dừng do tín hiệu thì KHÔNG ghi update-blocked.json")
	}
}

// Ctrl-C: bước NHẸ (`up -d`) treo thì có hạn riêng — không treo mãi.
func TestRollback_CoHanRieng(t *testing.T) {
	old := rollbackTimeout
	rollbackTimeout = 50 * time.Millisecond
	defer func() { rollbackTimeout = old }()

	composePath := testComposePath(t, updateTestComposeYAML)
	fr := updateFakeRunner(fake.Response{Match: matchFullUp, WaitCtx: true})
	plan := rollbackPlan{runner: fr, composePath: composePath, dir: filepath.Dir(composePath),
		key: updateTestBackupKey, dbTouched: true, versionBroken: true, installDir: t.TempDir(), version: testVersion}
	// Hạn riêng chỉ áp cho rollback vì tín hiệu dừng (Ctrl-C).
	ctx, cancel := context.WithCancelCause(context.Background())
	cancel(ErrInterruptSignal)
	var out strings.Builder
	start := time.Now()
	err := rollbackAndWrap(ctx, plan, &out, &OpError{Code: ErrCodeUpdateMigrateFailed, What: "migrate lỗi"})
	if d := time.Since(start); d >= time.Second {
		t.Fatalf("bước nhẹ của rollback phải có hạn riêng — mất %v", d)
	}
	opErr := asOpError(t, err)
	if !strings.Contains(out.String(), "ROLLBACK THẤT BẠI") || opErr.Code != ErrCodeUpdateInterrupted || !strings.Contains(opErr.What, "CHƯA trọn") {
		t.Errorf("up treo quá hạn phải báo ROLLBACK THẤT BẠI / quay về CHƯA trọn: %q / %s %q", out.String(), opErr.Code, opErr.What)
	}
	b, ok, _ := hostlink.ReadUpdateBlocked(plan.installDir)
	if !ok || !b.RollbackFailed || b.BackupKey != updateTestBackupKey {
		t.Errorf("bị dừng mà quay về chưa trọn phải ghi update-blocked.json (rollback_failed + backup_key): %+v ok=%v", b, ok)
	}
}

// slowRestoreRunner: `gh.backup restore` (restoreInContainer gọi qua Output)
// chạy lâu (delay) — trả lỗi nếu ctx bị huỷ/hết hạn trước khi xong (giả lập
// CommandContext giết docker CLI).
type slowRestoreRunner struct {
	dockercli.Runner
	delay       time.Duration
	restoreDone bool
	restoreErr  error
}

func (r *slowRestoreRunner) Output(ctx context.Context, cmd dockercli.Cmd) ([]byte, error) {
	if matchRestore(cmd) {
		select {
		case <-ctx.Done():
			r.restoreErr = ctx.Err()
			return nil, ctx.Err()
		case <-time.After(r.delay):
			r.restoreDone = true
		}
	}
	return r.Runner.Output(ctx, cmd)
}

// Ctrl-C + đã đụng CSDL + pg_restore chạy LÂU HƠN rollbackTimeout: khôi phục
// KHÔNG bị cắt (cắt giữa chừng sau DROP DATABASE = CSDL trống/dở).
func TestRollback_SIGINT_KhoiPhucLauHonHan_VanChayXong(t *testing.T) {
	old := rollbackTimeout
	rollbackTimeout = 30 * time.Millisecond
	defer func() { rollbackTimeout = old }()

	composePath := testComposePath(t, updateTestComposeYAML)
	installDir := t.TempDir()
	rr := &slowRestoreRunner{Runner: updateFakeRunner(), delay: 200 * time.Millisecond}
	plan := rollbackPlan{runner: rr, composePath: composePath, dir: filepath.Dir(composePath),
		key: updateTestBackupKey, dbTouched: true, versionBroken: true, installDir: installDir, version: testVersion}
	ctx, cancel := context.WithCancelCause(context.Background())
	cancel(ErrInterruptSignal)
	var out strings.Builder
	err := rollbackAndWrap(ctx, plan, &out, &OpError{Code: ErrCodeUpdateMigrateFailed, What: "migrate lỗi"})
	if rr.restoreErr != nil || !rr.restoreDone {
		t.Fatalf("restore phải chạy xong dù lâu hơn rollbackTimeout: done=%v err=%v\n%s", rr.restoreDone, rr.restoreErr, out.String())
	}
	opErr := asOpError(t, err)
	if opErr.Code != ErrCodeUpdateInterrupted || !strings.Contains(opErr.What, "đã tự quay về bản cũ") {
		t.Errorf("muốn GH-E94B đã quay về, được %s %q", opErr.Code, opErr.What)
	}
	if blockedExists(t, installDir) {
		t.Error("quay về ổn sau Ctrl-C thì KHÔNG ghi update-blocked.json")
	}
}

// Rollback vì lỗi thường (không có tín hiệu dừng): KHÔNG có hạn — pg_restore
// trên CSDL lớn/đĩa chậm phải được chạy xong.
func TestRollback_KhongBiDung_KhoiPhucKhongCoHan(t *testing.T) {
	old := rollbackTimeout
	rollbackTimeout = 50 * time.Millisecond
	defer func() { rollbackTimeout = old }()

	composePath := testComposePath(t, updateTestComposeYAML)
	rr := &ctxRecRunner{Runner: updateFakeRunner()}
	plan := rollbackPlan{runner: rr, composePath: composePath, dir: filepath.Dir(composePath),
		key: updateTestBackupKey, dbTouched: true, versionBroken: true, installDir: t.TempDir(), version: testVersion}
	err := rollbackAndWrap(context.Background(), plan, &strings.Builder{}, &OpError{Code: ErrCodeUpdateMigrateFailed, What: "migrate lỗi"})
	if opErr := asOpError(t, err); opErr.Code != ErrCodeUpdateRolledBack || !strings.Contains(opErr.What, "đã tự quay về") {
		t.Fatalf("muốn GH-E945 đã quay về, được %s %q", opErr.Code, opErr.What)
	}
	ri := rr.indexAfter(-1, matchRestore)
	if ri < 0 {
		t.Fatalf("phải khôi phục: %+v", rr.calls)
	}
	if rr.calls[ri].hasDeadline {
		t.Error("rollback không do tín hiệu dừng: restore KHÔNG được có hạn")
	}
	if ui := rr.indexAfter(ri, matchFullUp); ui < 0 || rr.calls[ui].hasDeadline {
		t.Errorf("up -d sau restore cũng không có hạn: %+v", rr.calls)
	}
}

// Bị dừng (Ctrl-C) + đã đụng CSDL + khôi phục LỖI (vd pg_restore bị cắt sau
// DROP DATABASE): PHẢI ghi update-blocked.json — lịch đêm không được chạy lại
// `genh update` đè lên CSDL trống/dở.
func TestRollback_BiDung_KhoiPhucLoi_VanGhiBlocked(t *testing.T) {
	composePath := testComposePath(t, updateTestComposeYAML)
	installDir := t.TempDir()
	if err := hostlink.MarkUpdateInProgress(installDir, hostlink.UpdateInProgress{}); err != nil {
		t.Fatal(err)
	}
	fr := updateFakeRunner(fake.Response{Match: matchRestore, Err: errors.New("pg_restore: bị giết")})
	plan := rollbackPlan{runner: fr, composePath: composePath, dir: filepath.Dir(composePath),
		key: updateTestBackupKey, dbTouched: true, versionBroken: true, installDir: installDir, version: testVersion}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	var out strings.Builder
	err := rollbackAndWrap(ctx, plan, &out, &OpError{Code: ErrCodeUpdateMigrateFailed, What: "migrate lỗi"})
	opErr := asOpError(t, err)
	if opErr.Code != ErrCodeUpdateInterrupted || strings.Contains(opErr.What, "đã tự quay về") {
		t.Fatalf("muốn GH-E94B chưa quay về, được %s %q", opErr.Code, opErr.What)
	}
	b, ok, rerr := hostlink.ReadUpdateBlocked(installDir)
	if rerr != nil || !ok {
		t.Fatalf("phải ghi update-blocked.json: ok=%v err=%v", ok, rerr)
	}
	if !b.RollbackFailed || b.BackupKey != updateTestBackupKey || b.Version != testVersion || !b.DBTouched {
		t.Errorf("update-blocked.json phải có rollback_failed + backup_key + version: %+v", b)
	}
	if !hostlink.UpdateInProgressExists(installDir) {
		t.Error("quay về chưa trọn thì GIỮ update-inprogress.json")
	}
}

// Bị dừng + CSDL chưa đụng + bản hỏng (versionBroken) + up -d lại LỖI: vẫn
// chặn lịch đêm (rollback_failed, không có backup_key).
func TestRollback_BiDung_KhongDungCsdl_UpLoi_GhiBlocked(t *testing.T) {
	composePath := testComposePath(t, updateTestComposeYAML)
	installDir := t.TempDir()
	fr := updateFakeRunner(fake.Response{Match: matchFullUp, Err: errors.New("docker đang dừng")})
	plan := rollbackPlan{runner: fr, composePath: composePath, dir: filepath.Dir(composePath),
		key: updateTestBackupKey, dbTouched: false, versionBroken: true, installDir: installDir, version: testVersion}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	err := rollbackAndWrap(ctx, plan, &strings.Builder{}, &OpError{Code: ErrCodeUpdateNotReady, What: "chưa sẵn sàng"})
	if opErr := asOpError(t, err); opErr.Code != ErrCodeUpdateInterrupted {
		t.Fatalf("muốn GH-E94B, được %s", opErr.Code)
	}
	b, ok, _ := hostlink.ReadUpdateBlocked(installDir)
	if !ok || !b.RollbackFailed || b.BackupKey != "" || b.DBTouched {
		t.Errorf("phải ghi update-blocked.json rollback_failed, KHÔNG backup_key: %+v ok=%v", b, ok)
	}
}

// SIGTERM (máy tắt) sau khi đã đụng CSDL: KHÔNG khôi phục (không dừng dịch
// vụ, không DROP/pg_restore, không up), KHÔNG trả compose.yaml về bản cũ (CSDL
// đã migrate chỉ khớp compose mới), KHÔNG ghi update-blocked.json, GIỮ
// update-inprogress.json — sau khi bật lại máy `genh update` đi tiếp lên bản mới.
func TestRollback_SIGTERM_DaDungCsdl_GiuBanMoiDeDiTiep(t *testing.T) {
	const newCompose = "services: {}\n"
	composePath := testComposePath(t, newCompose) // compose MỚI đang nằm trên đĩa
	installDir := t.TempDir()
	if err := hostlink.MarkUpdateInProgress(installDir, hostlink.UpdateInProgress{}); err != nil {
		t.Fatal(err)
	}
	fr := updateFakeRunner()
	plan := rollbackPlan{runner: fr, composePath: composePath, dir: filepath.Dir(composePath),
		key: updateTestBackupKey, dbTouched: true, versionBroken: true, installDir: installDir, version: testVersion,
		oldCompose: []byte(updateTestComposeYAML)}
	ctx, cancel := context.WithCancelCause(context.Background())
	cancel(ErrShutdownSignal)
	var out strings.Builder
	err := rollbackAndWrap(ctx, plan, &out, &OpError{Code: ErrCodeUpdateMigrateFailed, What: "migrate lỗi"})
	opErr := asOpError(t, err)
	if opErr.Code != ErrCodeUpdateInterrupted || !strings.Contains(opErr.What, updateShutdownForwardMarker) {
		t.Fatalf("muốn GH-E94B %q, được %s %q", updateShutdownForwardMarker, opErr.Code, opErr.What)
	}
	if strings.Contains(opErr.What, "chưa trọn") || strings.Contains(opErr.What, "CHƯA trọn") {
		t.Errorf("không được nói \"quay về chưa trọn\" (Console sẽ bảo xử lý tay/khôi phục): %q", opErr.What)
	}
	if !strings.Contains(opErr.Next, "genh update") || strings.Contains(opErr.Next, "restore --key") {
		t.Errorf("Next phải bảo chạy lại `genh update`, KHÔNG bảo khôi phục bản sao lưu cũ: %q", opErr.Next)
	}
	if len(fr.Calls) != 0 {
		t.Errorf("máy đang tắt: KHÔNG chạy lệnh docker nào (stop/restore/up): %+v", fr.Calls)
	}
	if b, _ := os.ReadFile(composePath); string(b) != newCompose {
		t.Error("compose.yaml phải GIỮ bản mới (khớp CSDL đã migrate)")
	}
	if blockedExists(t, installDir) {
		t.Error("KHÔNG ghi update-blocked.json (không đẩy Owner đi khôi phục bản sao lưu cũ)")
	}
	if !hostlink.UpdateInProgressExists(installDir) {
		t.Error("GIỮ update-inprogress.json để lần chạy sau coi \"chưa khớp\" và đi tiếp")
	}
}

func TestRunUpdate_HuyTruocSaoLuu_ChuaDungGi(t *testing.T) {
	composePath := testComposePath(t, updateTestComposeYAML)
	env := testEnv(t, composePath)
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	rr := &ctxRecRunner{Runner: updateFakeRunner()}
	rr.before = cancelOn(matchPull, cancel)

	err := RunUpdate(ctx, env, UpdateOptions{Version: testVersion}, fastUpdateDeps(rr), &strings.Builder{})
	opErr := asOpError(t, err)
	if opErr.Code != ErrCodeUpdateInterrupted || !strings.Contains(opErr.What, "chưa đụng gì") {
		t.Fatalf("muốn GH-E94B \"chưa đụng gì\", được %s %q", opErr.Code, opErr.What)
	}
	if rr.count(matchBackupRun) != 0 || rr.count(matchRestore) != 0 || rr.count(matchUp) != 0 || rr.count(matchMigrate) != 0 {
		t.Errorf("huỷ lúc tải: không sao lưu/khôi phục/up/migrate: %+v", rr.calls)
	}
	if rr.count(matchPull) != 1 {
		t.Errorf("huỷ thì không thử tải lại: %d lần", rr.count(matchPull))
	}
	if b, _ := os.ReadFile(composePath); string(b) != updateTestComposeYAML {
		t.Error("compose.yaml phải giữ nguyên")
	}
	if blockedExists(t, env.InstallDir) {
		t.Error("không ghi update-blocked.json")
	}
}

func TestRunUpdate_SaoLuuLoiDoHuy_VanBatLaiWorkerBridge(t *testing.T) {
	composePath := testComposePath(t, updateTestComposeYAML)
	env := testEnv(t, composePath)
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	rr := &ctxRecRunner{Runner: updateFakeRunner(pendingMigration)}
	rr.before = cancelOn(matchBackupRun, cancel)

	err := RunUpdate(ctx, env, UpdateOptions{Version: testVersion}, fastUpdateDeps(rr), &strings.Builder{})
	opErr := asOpError(t, err)
	if opErr.Code != ErrCodeUpdateInterrupted {
		t.Fatalf("muốn %s, được %v", ErrCodeUpdateInterrupted, err)
	}
	bi := rr.indexAfter(-1, matchBackupRun)
	si := rr.indexAfter(bi, matchStart)
	if bi < 0 || si < 0 {
		t.Fatalf("sao lưu lỗi do huỷ vẫn phải `start worker bridge`: %+v", rr.calls)
	}
	if !strings.HasSuffix(strings.Join(rr.calls[si].cmd.Args, " "), "start worker bridge") || rr.calls[si].ctxErr != nil {
		t.Errorf("`start worker bridge` phải chạy bằng ngữ cảnh không bị huỷ: %v ctxErr=%v", rr.calls[si].cmd.Args, rr.calls[si].ctxErr)
	}
	if rr.count(matchRestore) != 0 || rr.count(matchUp) != 0 || rr.count(matchMigrate) != 0 {
		t.Error("sao lưu lỗi thì không restore/up/migrate")
	}
}

// Dòng thử lại tải giữ đúng chuỗi E2E grep "thử lại lần %d/%d".
func TestPullWithRetry_DongThuLai(t *testing.T) {
	fr := &fake.Runner{Responses: []fake.Response{{Match: matchPull, ErrSeq: []error{errors.New("mạng"), nil}}}}
	var out strings.Builder
	n, err := pullWithRetry(context.Background(), fr, dockercli.Cmd{Name: "docker", Args: []string{"compose", "pull"}},
		UpdateDeps{PullAttempts: 3, PullBackoff: []time.Duration{0}, PullTimeout: time.Second}, &out)
	if err != nil || n != 2 {
		t.Fatalf("n=%d err=%v", n, err)
	}
	if !strings.Contains(out.String(), "thử lại lần 2/3") {
		t.Errorf("thiếu dòng \"thử lại lần 2/3\": %q", out.String())
	}
}
