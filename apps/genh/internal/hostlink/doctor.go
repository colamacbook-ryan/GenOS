package hostlink

import (
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"regexp"
)

// ─── Gói chẩn đoán qua hộp thư (v0.1.44, F-4b) ──────────────────────────────
//
//	run/request/doctor.json  ← api ghi khi Owner bấm "Gói chẩn đoán": {schema, request_id, requested_at}
//	run/doctor-status.json   ← genh ghi: running → done {file, size_bytes, sha256} | failed
//	run/diagnostics/         ← genh ghi zip đã LỌC BÍ MẬT (0755/0644 để api đọc), giữ 3 bản

const (
	DoctorRequestFile = "doctor.json"
	DoctorStatusFile  = "doctor-status.json"
	DiagnosticsDir    = "diagnostics"
)

// DoctorStatusSchema là phiên bản khuôn doctor-status.json.
const DoctorStatusSchema = 1

// requestIDRe: mã yêu cầu api sinh (16 hex thường) — mọi thứ khác bị bỏ.
var requestIDRe = regexp.MustCompile(`^[a-f0-9]{16}$`)

// ErrBadDoctorRequest: tệp yêu cầu hỏng hoặc request_id sai dạng.
var ErrBadDoctorRequest = errors.New("yêu cầu gói chẩn đoán không hợp lệ")

// DoctorRequest là nội dung run/request/doctor.json (KHÔNG tin cậy).
type DoctorRequest struct {
	Schema      int    `json:"schema"`
	RequestID   string `json:"request_id"`
	RequestedAt string `json:"requested_at"`
}

// DoctorStatus là nội dung run/doctor-status.json — KHÔNG omitempty.
type DoctorStatus struct {
	Schema     int    `json:"schema"`
	RequestID  string `json:"request_id"`
	State      string `json:"state"` // running | done | failed
	StartedAt  string `json:"started_at"`
	FinishedAt string `json:"finished_at"`
	File       string `json:"file"`
	SizeBytes  int64  `json:"size_bytes"`
	SHA256     string `json:"sha256"`
	ErrorCode  string `json:"error_code"`
	Message    string `json:"message"`
}

// DoctorRequestPath là đường dẫn run/request/doctor.json.
func DoctorRequestPath(installDir string) string {
	return filepath.Join(RequestDirPath(installDir), DoctorRequestFile)
}

// DoctorStatusPath là đường dẫn run/doctor-status.json.
func DoctorStatusPath(installDir string) string {
	return filepath.Join(Dir(installDir), DoctorStatusFile)
}

// DiagnosticsDirPath là thư mục chứa zip gói chẩn đoán (run/diagnostics).
func DiagnosticsDirPath(installDir string) string {
	return filepath.Join(Dir(installDir), DiagnosticsDir)
}

// HasDoctorRequest: Console có đang yêu cầu gói chẩn đoán không (Lstat —
// symlink cũng tính để genh dọn nó đi).
func HasDoctorRequest(installDir string) bool {
	_, err := os.Lstat(DoctorRequestPath(installDir))
	return err == nil
}

// ConsumeDoctorRequest đọc AN TOÀN (readRequestFile — đúng chủ sở hữu) rồi XOÁ yêu cầu (xoá cả khi hỏng — watcher
// không kích lặp); trả ErrBadDoctorRequest khi request_id không khớp ^[a-f0-9]{16}$.
func ConsumeDoctorRequest(installDir string) (DoctorRequest, error) {
	var r DoctorRequest
	path := DoctorRequestPath(installDir)
	b, err := readRequestFile(installDir, path)
	_ = os.Remove(path)
	if err != nil {
		return r, err
	}
	if err := json.Unmarshal(b, &r); err != nil {
		return DoctorRequest{}, ErrBadDoctorRequest
	}
	if !requestIDRe.MatchString(r.RequestID) {
		return DoctorRequest{}, ErrBadDoctorRequest
	}
	r.RequestedAt = cleanText(r.RequestedAt, maxAlertShortStr)
	return r, nil
}

// WriteDoctorStatus ghi nguyên tử run/doctor-status.json (0644).
func WriteDoctorStatus(installDir string, st DoctorStatus) error {
	if err := EnsureDir(installDir); err != nil {
		return err
	}
	st.Schema = DoctorStatusSchema
	return writeJSON(DoctorStatusPath(installDir), st)
}

// ReadDoctorStatus đọc run/doctor-status.json AN TOÀN.
func ReadDoctorStatus(installDir string) (DoctorStatus, error) {
	var st DoctorStatus
	b, err := readStateFile(DoctorStatusPath(installDir), false)
	if err != nil {
		return st, err
	}
	return st, json.Unmarshal(b, &st)
}

// errUnsafeDiagDir: run/diagnostics không phải thư mục thật của người chạy genh.
var errUnsafeDiagDir = errors.New("run/diagnostics không an toàn (symlink/không phải thư mục/không thuộc người chạy genh)")

// EnsureDiagnosticsDir tạo (nếu chưa có) run/diagnostics 0755 và KIỂM nó là
// thư mục thật thuộc người chạy genh — api ghi được run/ nên có thể cài
// sẵn symlink/thư mục của nó để genh ghi zip ra chỗ khác. Bên gọi ghi zip qua
// tệp tạm O_EXCL (os.CreateTemp) trong thư mục này rồi rename.
func EnsureDiagnosticsDir(installDir string) (string, error) {
	if err := EnsureDir(installDir); err != nil {
		return "", err
	}
	dir := DiagnosticsDirPath(installDir)
	if err := os.Mkdir(dir, 0o755); err != nil && !errors.Is(err, os.ErrExist) {
		return "", err
	}
	fi, err := os.Lstat(dir)
	if err != nil {
		return "", err
	}
	if !fi.IsDir() || fi.Mode()&os.ModeSymlink != 0 || !ownedBySelf(fi) {
		return "", errUnsafeDiagDir
	}
	if err := os.Chmod(dir, 0o755); err != nil {
		return "", err
	}
	return dir, nil
}

// ReadStateFileSafe đọc một tệp trạng thái trong run/ AN TOÀN (không theo
// symlink, một liên kết, ≤ 64 KiB) — dùng cho gói chẩn đoán chép host/*.json.
func ReadStateFileSafe(path string) ([]byte, error) { return readStateFile(path, false) }
