// Package hostlink là "hộp thư" giữa Console (container api) và genh trên máy
// chủ, để Owner bấm "Cập nhật ngay" trong ứng dụng thay vì gõ lệnh:
//
//	<gốc cài đặt>/run/           ← bind-mount vào api tại /var/lib/gh/host
//	  genh.json                  ← genh ghi: phiên bản đang chạy + cơ chế nhận yêu cầu
//	  request/update.json        ← api ghi khi Owner bấm nút; genh xoá khi bắt đầu
//	  update-status.json         ← genh ghi: running → done/failed (+ thông báo)
//	  request/restore.json       ← api ghi khi Owner bấm "Khôi phục" (v0.1.20): {key}
//	  restore-status.json        ← genh ghi: running → done/failed (+ bản an toàn)
//	  update-blocked.json        ← genh ghi (v0.1.34): bản đã lỗi + đã quay về bản cũ sau khi
//	                               đụng CSDL — lịch đêm không thử lại; xoá khi cập nhật thành công
//	  disk-status.json           ← genh ghi (v0.1.34) mỗi lần update kiểm đĩa: ok|low + số byte
//	  update-inprogress.json     ← genh ghi (v0.1.34) ngay trước khi đổi compose.yaml; xoá khi xong —
//	                               còn tệp = lần trước dừng giữa chừng, lần sau phải chạy lại đủ
//	  genh-heartbeat.json        ← genh ghi (v0.1.37) mỗi 30 giây khi đang giữ khoá loại trừ
//	                               (update/restore/import): {pid, op, boot_id, started_at, at};
//	                               xoá khi nhả khoá — Console suy "tiến trình còn sống" từ đây
//	  autostart-status.json      ← genh ghi (v0.1.37) lúc status/doctor/update: Docker và linger
//	                               có tự chạy lại khi bật máy không (xem autostart.go)
//	  request/offsite.json       ← api ghi (v0.1.40) khi Owner chọn nơi lưu / bấm "Sao lưu ra ổ
//	                               ngoài ngay" / tắt bản sao ngoài máy; genh xoá trước khi làm
//	  offsite-status.json        ← genh ghi (v0.1.40): trạng thái bản sao ngoài máy (offsite.go)
//	  request/doctor.json        ← api ghi (v0.1.44) khi Owner bấm "Gói chẩn đoán" (doctor.go)
//	  doctor-status.json         ← genh ghi (v0.1.44): running → done/failed + tệp zip
//	  diagnostics/               ← genh ghi (v0.1.44): zip gói chẩn đoán ĐÃ LỌC BÍ MẬT, giữ 3 bản
//	  telegram.json              ← api ghi (v0.1.44): "Báo động & bản tin", token MÃ HOÁ (watchdog.go)
//	  api-health.json            ← api ghi (v0.1.44) mỗi ~60 giây: sự cố phía api cho trực canh
//	  watchdog-status.json       ← genh ghi (v0.1.44) mỗi lượt trực canh máy chủ (12 phút)
//	  request/watchdog.json      ← api ghi (v0.1.44) khi Owner bấm "Gửi thử"
//	  network-status.json        ← genh ghi (v0.1.46): chế độ truy cập từ xa {schema, mode, bind_addr,
//	                               site_address, public_url, port, checked_at} (network.go)
//
// Khoá loại trừ (lock.go) KHÔNG nằm trong run/ mà ở <gốc cài đặt>/genh.lock —
// run/ bind-mount vào api, ai ghi được run/ sẽ xoá/thay/giữ được khoá.
//
// Bên máy chủ, một "watcher" (systemd path unit / crontab mỗi phút / launchd
// QueueDirectories — xem internal/autoupdate) chạy `genh handle-requests`
// khi thấy request/update.json hoặc request/restore.json — genh tự chọn việc
// (cập nhật trước, khôi phục sau) (thư mục riêng để launchd
// QueueDirectories chỉ chạy khi thư mục này có tệp).
//
// Quyền (v0.1.45, Linux): container api chạy uid/gid 10001 — khác người dùng máy
// chủ. run/ và run/request là 2770, chủ = người chạy genh, nhóm 10001 (ảnh api
// tạo nhóm gh gid 10001): chỉ genh và api ghi được, người dùng khác trên máy
// không vào được (EnsureRunPerms, gọi sau `docker compose up`; không siết được
// thì mở lại 0777 như cũ và genh.json ghi run_mode "open"). Tệp genh ghi 0644
// (api đọc qua bit nhóm/khác). Vì api ghi được, genh vẫn không tin tệp nào ở
// đây khi đọc (readStateFile: không theo symlink, 1 liên kết, giới hạn kích
// thước) và tệp YÊU CẦU phải do api hoặc chính genh sở hữu (readRequestFile).
// macOS/Windows: Docker Desktop tự ánh xạ quyền — giữ như cũ (run_mode "n/a").
package hostlink

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"time"

	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/dockercli"
)

// removeFile xoá tệp yêu cầu — biến gói để test giả lỗi xoá (EACCES…) mà không cần
// chạy non-root.
var removeFile = os.Remove

// ErrRequestUndeletable: không xoá được tệp yêu cầu trong run/request (quyền…). genh
// KHÔNG làm yêu cầu đó (xoá-trước-khi-làm là thứ giữ cho watcher không kích lặp);
// caller ghi trạng thái failed GH-E94C. errors.Is(err, ErrRequestUndeletable).
var ErrRequestUndeletable = errors.New("không xoá được tệp yêu cầu trong run/request")

// EnvDir là biến môi trường genh đặt cho docker compose: đường dẫn hộp thư trên
// máy chủ (compose.yaml: ${GH_HOST_LINK_DIR:-../run}:/var/lib/gh/host).
const EnvDir = "GH_HOST_LINK_DIR"

const (
	InfoFile    = "genh.json"
	RequestDir  = "request"
	RequestFile = "update.json"
	StatusFile  = "update-status.json"

	RestoreRequestFile = "restore.json"
	RestoreStatusFile  = "restore-status.json"
)

// Requests là các loại yêu cầu watcher hiện tại nhận (ghi vào genh.json để
// Console biết nút nào bấm được — watcher cũ v0.1.19 chỉ nhận "update";
// "offsite" từ v0.1.40; "doctor", "watchdog" từ v0.1.44).
var Requests = []string{"update", "restore", "offsite", "doctor", "watchdog"}

// Dir là thư mục hộp thư dưới gốc cài đặt.
func Dir(installDir string) string { return filepath.Join(installDir, "run") }

// RequestDirPath là thư mục chứa yêu cầu (launchd QueueDirectories theo dõi thư mục này).
func RequestDirPath(installDir string) string { return filepath.Join(Dir(installDir), RequestDir) }

// RequestPath là đường dẫn tệp yêu cầu cập nhật (systemd/cron theo dõi tệp này).
func RequestPath(installDir string) string {
	return filepath.Join(RequestDirPath(installDir), RequestFile)
}

// EnsureDir tạo thư mục run + run/request nếu chưa có — PHẢI chạy trước
// `docker compose up`, nếu không Docker tự tạo thư mục bind mount với chủ root.
// Linux (v0.1.45): tạo 0770 và KHÔNG chmod lại thư mục đã có (không mở lại 0777
// thư mục EnsureRunPerms đã siết 2770); macOS/Windows giữ 0777 như cũ.
func EnsureDir(installDir string) error {
	return ensureRunDirs([]string{Dir(installDir), RequestDirPath(installDir)})
}

// ─── Quyền hộp thư run/ (v0.1.45) ───────────────────────────────────────────

// Giá trị run_mode trong genh.json.
const (
	RunModeRestricted = "restricted" // 2770 nhóm 10001: chỉ genh + api
	RunModeOpen       = "open"       // 0777: siết không được (docker lỗi, ảnh api cũ)
	RunModeNA         = "n/a"        // macOS/Windows: Docker Desktop tự ánh xạ quyền
)

// APIGID là gid nhóm gh trong ảnh api (deploy/images/api.Dockerfile).
const APIGID = 10001

// DefaultAPIUID là uid tiến trình api trong container (USER gh).
const DefaultAPIUID = 10001

// EnvAPIUID ghi đè uid tiến trình api nhìn từ máy chủ (Docker rootless).
const EnvAPIUID = "GENH_API_UID"

// HostlinkConfigFile: <gốc cài đặt>/config/hostlink.json (0600, KHÔNG ở run/) —
// uid thật của api dò được (Docker rootless ánh xạ 10001 sang subuid).
const HostlinkConfigFile = "hostlink.json"

type hostlinkConfig struct {
	APIUID uint32 `json:"api_uid"`
}

func hostlinkConfigPath(installDir string) string {
	return filepath.Join(installDir, "config", HostlinkConfigFile)
}

// APIUID là uid chủ các tệp api ghi vào hộp thư, nhìn từ máy chủ: GENH_API_UID
// nếu đặt, không thì giá trị đã dò lưu ở config/hostlink.json, mặc định 10001.
func APIUID(installDir string) uint32 {
	if v := strings.TrimSpace(os.Getenv(EnvAPIUID)); v != "" {
		if n, err := strconv.ParseUint(v, 10, 32); err == nil {
			return uint32(n)
		}
	}
	if b, err := readStateFile(hostlinkConfigPath(installDir), true); err == nil {
		var c hostlinkConfig
		if json.Unmarshal(b, &c) == nil && c.APIUID != 0 {
			return c.APIUID
		}
	}
	return DefaultAPIUID
}

// WriteAPIUID lưu uid thật của api vào config/hostlink.json (0600).
func WriteAPIUID(installDir string, uid uint32) error {
	path := hostlinkConfigPath(installDir)
	if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
		return err
	}
	b, err := json.MarshalIndent(hostlinkConfig{APIUID: uid}, "", "  ")
	if err != nil {
		return err
	}
	return writeFileAtomicMode(path, append(b, '\n'), 0o600)
}

// RunPermSpec là đầu vào EnsureRunPerms.
type RunPermSpec struct {
	InstallDir string
	// Image là ảnh api (compose "image:"); rỗng → hỏi docker container api đang
	// chạy (PsArgs rồi `docker inspect`).
	Image string
	// PsArgs: tham số `docker` liệt kê ID container api (vd compose -f X ps -q api).
	PsArgs []string
	Env    []string // biến môi trường thêm cho lệnh docker (compose cần mật khẩu)
	Dir    string   // thư mục làm việc lệnh docker
	Runner dockercli.Runner
	// Cho test: GOOS ("" = runtime.GOOS), Getuid (nil = os.Getuid), Chown (nil = os.Chown).
	GOOS   string
	Getuid func() int
	Chown  func(name string, uid, gid int) error
}

func dockerCmd(args []string, s RunPermSpec) dockercli.Cmd {
	return dockercli.Cmd{Name: "docker", Args: args, Env: s.Env, Dir: s.Dir}
}

// resolveAPIImage: ảnh compose khai báo, không có thì ảnh của container api đang chạy.
func resolveAPIImage(ctx context.Context, s RunPermSpec) (string, error) {
	if s.Image != "" {
		return s.Image, nil
	}
	if len(s.PsArgs) == 0 {
		return "", errors.New("không xác định được ảnh api")
	}
	out, err := s.Runner.Output(ctx, dockerCmd(s.PsArgs, s))
	if err != nil {
		return "", fmt.Errorf("tìm container api: %w", err)
	}
	id := strings.TrimSpace(strings.SplitN(strings.TrimSpace(string(out)), "\n", 2)[0])
	if id == "" {
		return "", errors.New("container api chưa chạy")
	}
	img, err := s.Runner.Output(ctx, dockerCmd([]string{"inspect", "--format", "{{.Image}}", id}, s))
	if err != nil {
		return "", fmt.Errorf("đọc ảnh container api: %w", err)
	}
	ref := strings.TrimSpace(string(img))
	if ref == "" {
		return "", errors.New("không đọc được ảnh container api")
	}
	return ref, nil
}

// Info là nội dung genh.json.
type Info struct {
	Version string `json:"version"`
	// Updater là cơ chế nhận yêu cầu từ Console: "systemd", "cron", "launchd"
	// hoặc "" (chưa có — Console hiện lệnh để Owner tự chạy).
	Updater string `json:"updater"`
	// Requests: loại yêu cầu watcher nhận (rỗng khi chưa có watcher).
	Requests []string `json:"requests,omitempty"`
	// AutoUpdateEnabled (v0.1.33): lịch tự cập nhật đêm (~03:00) đang bật hay
	// tắt — ghi lúc cài/update và khi `genh auto-update enable|disable`. nil
	// (không có khoá) = không rõ (genh cũ / không đọc được) ⇒ Console không
	// hứa "Tự cài đêm …".
	AutoUpdateEnabled *bool `json:"auto_update_enabled,omitempty"`
	// RunMode (v0.1.45): quyền hộp thư run/ — "restricted" (2770 nhóm 10001),
	// "open" (0777, siết không được) hoặc "n/a" (macOS/Windows). Rỗng = chưa rõ.
	RunMode   string `json:"run_mode,omitempty"`
	WrittenAt string `json:"written_at"`
}

// Status là nội dung update-status.json.
type Status struct {
	State      string `json:"state"` // running | done | failed
	From       string `json:"from,omitempty"`
	To         string `json:"to,omitempty"`
	Message    string `json:"message,omitempty"`
	StartedAt  string `json:"started_at,omitempty"`
	FinishedAt string `json:"finished_at,omitempty"`
	// PID (v0.1.37) là PID tiến trình genh NGOÀI CÙNG (tiến trình giữ khoá và
	// ghi nhịp sống genh-heartbeat.json) — Console đối chiếu với nhịp sống.
	PID int `json:"pid,omitempty"`
	// BootID (v0.1.37): /proc/sys/kernel/random/boot_id lúc Start ("" ngoài
	// Linux) — khác boot_id hiện tại ⇒ máy đã khởi động lại, lần chạy đã chết.
	BootID string `json:"boot_id,omitempty"`
	// RequestedAt (v0.1.53): requested_at của yêu cầu không xoá được (GH-E94C) —
	// chỉ để genh nhận ra "đã báo lỗi cho đúng yêu cầu này rồi" (idempotent).
	RequestedAt string `json:"requested_at,omitempty"`
}

func now() string { return time.Now().UTC().Format(time.RFC3339) }

func writeJSON(path string, v any) error {
	b, err := json.MarshalIndent(v, "", "  ")
	if err != nil {
		return err
	}
	return writeFileAtomic(path, append(b, '\n'))
}

// writeFileAtomic ghi data ra path qua tệp tạm TÊN NGẪU NHIÊN (os.CreateTemp —
// O_EXCL, không đi theo symlink) rồi rename, quyền 0644. Thư mục run/ được
// bind-mount vào container api (ghi được): tên tạm cố định (<tệp>.tmp) cho phép
// api cài sẵn symlink để genh (có thể chạy bằng root) ghi đè tệp ngoài.
// rename thay chính đường dẫn đích (kể cả khi đích là symlink) chứ không đi theo.
func writeFileAtomic(path string, data []byte) error {
	return writeFileAtomicMode(path, data, 0o644)
}

func writeFileAtomicMode(path string, data []byte, mode os.FileMode) error {
	f, err := os.CreateTemp(filepath.Dir(path), filepath.Base(path)+".*.tmp")
	if err != nil {
		return err
	}
	tmp := f.Name()
	done := false
	defer func() {
		if !done {
			_ = os.Remove(tmp)
		}
	}()
	if _, err := f.Write(data); err != nil {
		_ = f.Close()
		return err
	}
	// CreateTemp tạo 0600 — api (uid khác) phải đọc được tệp trạng thái (0644).
	if err := f.Chmod(mode); err != nil {
		_ = f.Close()
		return err
	}
	if err := f.Close(); err != nil {
		return err
	}
	if err := os.Rename(tmp, path); err != nil {
		return err
	}
	done = true
	return nil
}

// WriteInfo ghi phiên bản genh đang chạy + cơ chế nhận yêu cầu + trạng thái
// lịch tự cập nhật đêm (autoUpdate nil = không rõ, bỏ khoá).
func WriteInfo(installDir, version, updater string, autoUpdate *bool) error {
	if err := EnsureDir(installDir); err != nil {
		return err
	}
	info := Info{Version: version, Updater: updater, AutoUpdateEnabled: autoUpdate, WrittenAt: now()}
	if old, err := ReadInfo(installDir); err == nil {
		info.RunMode = old.RunMode // giữ kết quả siết quyền run/ lần gần nhất
	}
	if updater != "" {
		info.Requests = Requests
	}
	return writeJSON(filepath.Join(Dir(installDir), InfoFile), info)
}

// SetAutoUpdate chỉ đổi trạng thái lịch tự cập nhật đêm trong genh.json (giữ
// nguyên version/updater/requests đã ghi). Chưa có genh.json (hoặc hỏng) thì
// ghi mới với version cho trước, chưa có watcher.
func SetAutoUpdate(installDir, version string, enabled bool) error {
	if err := EnsureDir(installDir); err != nil {
		return err
	}
	info, err := ReadInfo(installDir)
	if err != nil {
		info = Info{Version: version}
	}
	info.AutoUpdateEnabled = &enabled
	info.WrittenAt = now()
	return writeJSON(filepath.Join(Dir(installDir), InfoFile), info)
}

// SetRunMode ghi run_mode (quyền hộp thư run/) vào genh.json, giữ nguyên các
// trường khác; chưa có genh.json (hoặc hỏng) thì ghi mới chỉ với run_mode.
func SetRunMode(installDir, mode string) error {
	if err := EnsureDir(installDir); err != nil {
		return err
	}
	info, err := ReadInfo(installDir)
	if err != nil {
		info = Info{}
	}
	info.RunMode = mode
	info.WrittenAt = now()
	return writeJSON(filepath.Join(Dir(installDir), InfoFile), info)
}

// ReadInfo đọc genh.json (lỗi nếu chưa có) — AN TOÀN (readStateFile).
func ReadInfo(installDir string) (Info, error) {
	var i Info
	b, err := readStateFile(filepath.Join(Dir(installDir), InfoFile), false)
	if err != nil {
		return i, err
	}
	return i, json.Unmarshal(b, &i)
}

// BadUpdateRequestMessage là thông điệp update-status "failed" khi request/
// update.json là tệp lạ (symlink, nhiều liên kết, quá lớn, sai chủ sở hữu).
const BadUpdateRequestMessage = "Yêu cầu cập nhật không hợp lệ (tệp lạ trong hộp thư)"

// checkUpdateRequest: present = có gì ở request/update.json (Lstat — symlink cũng
// tính); valid = tệp thường, 1 liên kết, nhỏ, do api hoặc genh sở hữu.
func checkUpdateRequest(installDir string) (present, valid bool) {
	fi, err := os.Lstat(RequestPath(installDir))
	if err != nil {
		return false, false
	}
	return true, requestInfoOK(installDir, fi)
}

// rejectUpdateRequest xoá tệp yêu cầu lạ (Remove không theo symlink) và báo
// update-status "failed" — yêu cầu bị BỎ QUA.
func rejectUpdateRequest(installDir string) {
	_ = removeFile(RequestPath(installDir))
	_ = Finish(installDir, "failed", "", BadUpdateRequestMessage)
}

// HasRequest báo Console có đang yêu cầu cập nhật không. Tệp lạ (v0.1.45) bị
// xoá, ghi update-status failed và coi như KHÔNG có yêu cầu.
func HasRequest(installDir string) bool {
	present, valid := checkUpdateRequest(installDir)
	if present && !valid {
		rejectUpdateRequest(installDir)
		return false
	}
	return present
}

// ConsumeRequest xoá tệp yêu cầu (gọi khi bắt đầu cập nhật) — consumed=true nếu
// trước đó có yêu cầu hợp lệ và nó đã biến mất (xoá được, hoặc đã bị xoá mất
// ENOENT). Xoá TRƯỚC khi chạy để watcher không kích lặp lại. Tệp lạ: xoá, ghi
// update-status failed, (false, nil) — bỏ qua yêu cầu. Xoá LỖI (quyền…): (false,
// err) với err bọc ErrRequestUndeletable — caller KHÔNG được làm yêu cầu này
// (v0.1.53, F-97: bản cũ coi lỗi xoá là "đã nhận" ⇒ path unit kích lặp tới
// start-limit-hit).
func ConsumeRequest(installDir string) (consumed bool, err error) {
	present, valid := checkUpdateRequest(installDir)
	if !present {
		return false, nil
	}
	if !valid {
		rejectUpdateRequest(installDir)
		return false, nil
	}
	if rerr := removeFile(RequestPath(installDir)); rerr != nil {
		if errors.Is(rerr, os.ErrNotExist) {
			return true, nil
		}
		return false, fmt.Errorf("%w: %w", ErrRequestUndeletable, rerr)
	}
	return true, nil
}

// RequestDirWritable dò TRƯỚC khi làm yêu cầu có xoá được tệp trong run/request không
// (tạo rồi xoá một tệp thăm dò — quyền thư mục sai/ổ chỉ-đọc thì xoá yêu cầu cũng
// không được). nil = xoá được.
func RequestDirWritable(installDir string) error {
	f, err := os.CreateTemp(RequestDirPath(installDir), ".probe-*")
	if err != nil {
		return fmt.Errorf("%w: %w", ErrRequestUndeletable, err)
	}
	name := f.Name()
	_ = f.Close()
	if err := removeFile(name); err != nil {
		_ = os.Remove(name)
		return fmt.Errorf("%w: %w", ErrRequestUndeletable, err)
	}
	return nil
}

// requestStamp: dấu nhận dạng một yêu cầu (requested_at, không có thì id) đọc AN
// TOÀN từ tệp yêu cầu JSON — "" nếu không đọc được. Dùng cho idempotency của
// trạng thái failed GH-E94C (cùng yêu cầu ⇒ không ghi lại).
func requestStamp(installDir, path string) string {
	b, err := readRequestFile(installDir, path)
	if err != nil {
		return ""
	}
	var r struct {
		ID          string `json:"id"`
		RequestedAt string `json:"requested_at"`
	}
	if json.Unmarshal(b, &r) != nil {
		return ""
	}
	if r.RequestedAt != "" {
		return cleanText(r.RequestedAt, maxAlertShortStr)
	}
	return cleanText(r.ID, maxAlertShortStr)
}

// UpdateRequestStamp là requestStamp của request/update.json.
func UpdateRequestStamp(installDir string) string {
	return requestStamp(installDir, RequestPath(installDir))
}

// UpdateUndeletableReported: update-status đã là failed mang mã code (GH-E94C) cho
// ĐÚNG yêu cầu đang nằm trong hộp thư (cùng requested_at) — không cần báo/làm lại.
func UpdateUndeletableReported(installDir, code string) bool {
	st, err := ReadStatus(installDir)
	return err == nil && st.State == "failed" && strings.Contains(st.Message, code) && st.RequestedAt == UpdateRequestStamp(installDir)
}

// FailUndeletableUpdate ghi update-status "failed" cho yêu cầu cập nhật KHÔNG xoá
// được (message đã có mã GH-E94C — code là chuỗi mã để nhận ra lần ghi trước).
// Idempotent theo requested_at: update-status đã là failed mang code cho cùng
// yêu cầu thì KHÔNG ghi lại (wrote=false) — path unit kích lặp thì tệp
// trạng thái không bị làm mới mỗi lần.
func FailUndeletableUpdate(installDir, from, code, message string) (wrote bool, err error) {
	if UpdateUndeletableReported(installDir, code) {
		return false, nil
	}
	stamp := UpdateRequestStamp(installDir)
	if err := EnsureDir(installDir); err != nil {
		return false, err
	}
	t := now()
	return true, writeJSON(filepath.Join(Dir(installDir), StatusFile), Status{
		State: "failed", From: from, To: from, Message: message, StartedAt: t, FinishedAt: t, RequestedAt: stamp,
	})
}

// Start ghi trạng thái "running" kèm PID tiến trình này (tiến trình NGOÀI CÙNG
// — chỉ nó gọi Start) và boot_id của máy.
func Start(installDir, from string) error {
	if err := EnsureDir(installDir); err != nil {
		return err
	}
	return writeJSON(filepath.Join(Dir(installDir), StatusFile), Status{
		State: "running", From: from, StartedAt: now(), PID: os.Getpid(), BootID: BootID(),
	})
}

// Finish ghi trạng thái cuối (done/failed) giữ nguyên From/StartedAt/PID/
// BootID của lần chạy đang dở nếu có.
func Finish(installDir, state, to, message string) error {
	if err := EnsureDir(installDir); err != nil {
		return err
	}
	st, _ := ReadStatus(installDir)
	st.State, st.To, st.Message, st.FinishedAt = state, to, message, now()
	return writeJSON(filepath.Join(Dir(installDir), StatusFile), st)
}

// SnapshotStatus chụp update-status.json hiện có (ok=false nếu chưa có, hoặc
// tệp không an toàn/hỏng) — để RestoreStatusSnapshot trả hộp thư về như cũ.
// KHÔNG chép nguyên byte: run/ container api ghi được nên tệp có thể là
// symlink/hard link tới bí mật của người chạy genh hoặc /dev/zero — chỉ đọc tệp
// thường nhỏ (readStateFile), parse thành Status rồi ghi lại đúng các trường
// đó (cùng định dạng writeJSON — tệp genh tự ghi thì trùng từng byte).
func SnapshotStatus(installDir string) (raw []byte, ok bool) {
	b, err := readStateFile(filepath.Join(Dir(installDir), StatusFile), false)
	if err != nil {
		return nil, false
	}
	var st Status
	if err := json.Unmarshal(b, &st); err != nil {
		return nil, false
	}
	out, err := json.MarshalIndent(st, "", "  ")
	if err != nil {
		return nil, false
	}
	return append(out, '\n'), true
}

// RestoreStatusSnapshot ghi lại update-status.json đúng từng byte như lúc
// SnapshotStatus (ok=false → xoá tệp, như trước đó chưa có). Dùng khi một lần
// chạy quyết định KHÔNG làm gì sau khi đã báo "running" (lịch đêm gặp bản bị
// chặn): không làm mới finished_at, không ghi đè thông điệp lỗi gốc.
func RestoreStatusSnapshot(installDir string, raw []byte, ok bool) error {
	path := filepath.Join(Dir(installDir), StatusFile)
	if !ok {
		err := os.Remove(path)
		if err != nil && !errors.Is(err, os.ErrNotExist) {
			return err
		}
		return nil
	}
	return writeFileAtomic(path, raw)
}

// ReadStatus đọc update-status.json (Status rỗng nếu chưa có).
// Chỉ đọc tệp thường nhỏ (readStateFile) — xem SnapshotStatus.
func ReadStatus(installDir string) (Status, error) {
	var s Status
	b, err := readStateFile(filepath.Join(Dir(installDir), StatusFile), false)
	if err != nil {
		return s, err
	}
	return s, json.Unmarshal(b, &s)
}

// ─── Khôi phục từ Console (v0.1.20) ─────────────────────────────────────────

// RestoreRequestPath là tệp yêu cầu khôi phục api ghi.
func RestoreRequestPath(installDir string) string {
	return filepath.Join(RequestDirPath(installDir), RestoreRequestFile)
}

// RestoreRequest là nội dung request/restore.json.
type RestoreRequest struct {
	ID          string `json:"id,omitempty"`
	Key         string `json:"key"`
	RequestedAt string `json:"requested_at,omitempty"`
	By          string `json:"by,omitempty"`
}

// RestoreStatus là nội dung restore-status.json.
type RestoreStatus struct {
	State      string `json:"state"` // running | done | failed
	Key        string `json:"key,omitempty"`
	SafetyKey  string `json:"safety_key,omitempty"`
	Message    string `json:"message,omitempty"`
	StartedAt  string `json:"started_at,omitempty"`
	FinishedAt string `json:"finished_at,omitempty"`
	// RequestedAt (v0.1.53): như Status.RequestedAt — idempotency của GH-E94C.
	RequestedAt string `json:"requested_at,omitempty"`
}

// HasRestoreRequest báo Console có đang yêu cầu khôi phục không (Lstat —
// symlink cũng tính để genh dọn nó đi).
func HasRestoreRequest(installDir string) bool {
	_, err := os.Lstat(RestoreRequestPath(installDir))
	return err == nil
}

// ConsumeRestoreRequest đọc AN TOÀN (readRequestFile — không theo symlink, đúng
// chủ sở hữu) rồi XOÁ yêu cầu khôi phục (xoá trước khi chạy để watcher không
// kích lặp). Tệp hỏng/lạ vẫn bị xoá, trả lỗi. Xoá không được (quyền…) ⇒ lỗi bọc
// ErrRequestUndeletable và KHÔNG khôi phục (v0.1.53, F-97).
func ConsumeRestoreRequest(installDir string) (RestoreRequest, error) {
	var r RestoreRequest
	path := RestoreRequestPath(installDir)
	b, err := readRequestFile(installDir, path)
	if err != nil && errors.Is(err, os.ErrNotExist) {
		return r, err
	}
	if rerr := removeFile(path); rerr != nil && !errors.Is(rerr, os.ErrNotExist) {
		return r, fmt.Errorf("%w: %w", ErrRequestUndeletable, rerr)
	}
	if err != nil {
		return r, err
	}
	if err := json.Unmarshal(b, &r); err != nil {
		return r, err
	}
	if r.Key == "" {
		return r, errors.New("yêu cầu khôi phục thiếu khoá bản sao lưu")
	}
	return r, nil
}

// RestoreRequestStamp là requestStamp của request/restore.json.
func RestoreRequestStamp(installDir string) string {
	return requestStamp(installDir, RestoreRequestPath(installDir))
}

// RestoreUndeletableReported như UpdateUndeletableReported cho restore-status.json.
func RestoreUndeletableReported(installDir, code string) bool {
	st, err := ReadRestoreStatus(installDir)
	return err == nil && st.State == "failed" && strings.Contains(st.Message, code) && st.RequestedAt == RestoreRequestStamp(installDir)
}

// FailUndeletableRestore như FailUndeletableUpdate cho restore-status.json.
func FailUndeletableRestore(installDir, code, message string) (wrote bool, err error) {
	if RestoreUndeletableReported(installDir, code) {
		return false, nil
	}
	stamp := RestoreRequestStamp(installDir)
	if err := EnsureDir(installDir); err != nil {
		return false, err
	}
	t := now()
	return true, writeJSON(filepath.Join(Dir(installDir), RestoreStatusFile), RestoreStatus{
		State: "failed", Message: message, StartedAt: t, FinishedAt: t, RequestedAt: stamp,
	})
}

// StartRestore ghi trạng thái khôi phục "running".
func StartRestore(installDir, key string) error {
	if err := EnsureDir(installDir); err != nil {
		return err
	}
	return writeJSON(filepath.Join(Dir(installDir), RestoreStatusFile), RestoreStatus{State: "running", Key: key, StartedAt: now()})
}

// FinishRestore ghi trạng thái khôi phục cuối (done/failed), giữ Key/StartedAt.
func FinishRestore(installDir, state, safetyKey, message string) error {
	if err := EnsureDir(installDir); err != nil {
		return err
	}
	st, _ := ReadRestoreStatus(installDir)
	st.State, st.SafetyKey, st.Message, st.FinishedAt = state, safetyKey, message, now()
	return writeJSON(filepath.Join(Dir(installDir), RestoreStatusFile), st)
}

// ReadRestoreStatus đọc restore-status.json.
func ReadRestoreStatus(installDir string) (RestoreStatus, error) {
	var s RestoreStatus
	b, err := readStateFile(filepath.Join(Dir(installDir), RestoreStatusFile), false)
	if err != nil {
		return s, err
	}
	return s, json.Unmarshal(b, &s)
}

// Pending cho biết watcher cần làm việc gì: "update" (ưu tiên — cập nhật đã
// tự sao lưu trước), "restore", "offsite" (v0.1.40 — bản sao ngoài máy),
// "doctor" (v0.1.44 — gói chẩn đoán), "watchdog" (v0.1.44 — "Gửi thử", làm sau
// cùng), hoặc "" khi hộp thư trống.
func Pending(installDir string) string {
	switch {
	case HasRequest(installDir):
		return "update"
	case HasRestoreRequest(installDir):
		return "restore"
	case HasOffsiteRequest(installDir):
		return "offsite"
	case HasDoctorRequest(installDir):
		return "doctor"
	case HasWatchdogRequest(installDir):
		return "watchdog"
	default:
		return ""
	}
}
