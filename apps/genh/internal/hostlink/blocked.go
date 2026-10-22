package hostlink

import (
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
)

// UpdateBlockedFile ghi lại bản genh đã lỗi ở lần cập nhật trước, ở bước
// migrate trở đi (có hoặc không đụng CSDL — F-33), và genh đã tự quay về bản cũ
// (hoặc thử mà thất bại: rollback_failed) — lịch đêm (`genh update --yes`, không
// --if-requested) đọc tệp này để không thử lại đúng bản đó mỗi đêm. Nút "Cập
// nhật ngay" và gõ tay không bị chặn; cập nhật thành công thì genh xoá tệp.
const UpdateBlockedFile = "update-blocked.json"

// UpdateBlocked là nội dung update-blocked.json (không chứa bí mật).
type UpdateBlocked struct {
	Version   string `json:"version"`
	BlockedAt string `json:"blocked_at"`
	Code      string `json:"code,omitempty"`
	// BackupKey: bản sao lưu CẦN khôi phục khi xử lý tay — chỉ ghi khi CSDL đã
	// bị đụng (DBTouched). CSDL chưa bị đụng thì để trống: khôi phục bản sao lưu
	// sẽ xoá mọi ghi chép của worker/bridge/api từ lúc sao lưu.
	BackupKey string `json:"backup_key,omitempty"`
	// DBTouched: bản lỗi đã chạy migration (CSDL có thể đã đổi). Tệp cũ không
	// có khoá → false; khi đó có BackupKey thì coi như đã đụng (tệp cũ chỉ ghi
	// sau khi đụng CSDL).
	DBTouched bool   `json:"db_touched"`
	Message   string `json:"message,omitempty"`
	// RollbackFailed: tự quay về bản cũ CŨNG thất bại (khôi phục CSDL hoặc khởi
	// động lại lỗi) — máy cần xử lý tay. Không có khoá (tệp cũ) = quay về ổn.
	RollbackFailed bool `json:"rollback_failed,omitempty"`
}

func updateBlockedPath(installDir string) string {
	return filepath.Join(Dir(installDir), UpdateBlockedFile)
}

// WriteUpdateBlocked ghi update-blocked.json (BlockedAt rỗng → thời điểm hiện tại).
func WriteUpdateBlocked(installDir string, b UpdateBlocked) error {
	if err := EnsureDir(installDir); err != nil {
		return err
	}
	if b.BlockedAt == "" {
		b.BlockedAt = now()
	}
	return writeJSON(updateBlockedPath(installDir), b)
}

// ReadUpdateBlocked đọc update-blocked.json — không có tệp → (rỗng, false, nil).
//
// Tệp nằm trong run/ (0777, container api ghi được) và quyết định lịch đêm có
// cài một bản hay không (kể cả bản vá bảo mật) — chỉ tin tệp thường, không theo
// symlink, do CHÍNH uid đang chạy genh ghi (readStateFile requireOwner). Tệp
// không đạt → lỗi, coi như không bị chặn.
func ReadUpdateBlocked(installDir string) (UpdateBlocked, bool, error) {
	var b UpdateBlocked
	raw, err := readStateFile(updateBlockedPath(installDir), true)
	if err != nil {
		if errors.Is(err, os.ErrNotExist) {
			return b, false, nil
		}
		return b, false, err
	}
	if err := json.Unmarshal(raw, &b); err != nil {
		return b, false, err
	}
	return b, true, nil
}

// ClearUpdateBlocked xoá update-blocked.json — không có tệp → nil.
func ClearUpdateBlocked(installDir string) error {
	err := os.Remove(updateBlockedPath(installDir))
	if err != nil && !errors.Is(err, os.ErrNotExist) {
		return err
	}
	return nil
}

// UpdateInProgressFile đánh dấu một lần `genh update` đã bắt đầu đổi máy (ghi
// ngay TRƯỚC khi đồng bộ compose.yaml) mà chưa xong. genh bị tắt/máy khởi động
// lại giữa chừng thì compose.yaml đã là bản mới trong khi container còn cũ —
// còn tệp này thì lần sau KHÔNG được coi "đã khớp bản nhúng" (ops.UpdateNeeded).
const UpdateInProgressFile = "update-inprogress.json"

// UpdateInProgress là nội dung update-inprogress.json (không chứa bí mật).
type UpdateInProgress struct {
	Version   string `json:"version"`
	BackupKey string `json:"backup_key,omitempty"`
	StartedAt string `json:"started_at"`
}

func updateInProgressPath(installDir string) string {
	return filepath.Join(Dir(installDir), UpdateInProgressFile)
}

// MarkUpdateInProgress ghi update-inprogress.json (StartedAt rỗng → bây giờ).
func MarkUpdateInProgress(installDir string, p UpdateInProgress) error {
	if err := EnsureDir(installDir); err != nil {
		return err
	}
	if p.StartedAt == "" {
		p.StartedAt = now()
	}
	return writeJSON(updateInProgressPath(installDir), p)
}

// UpdateInProgressExists báo còn dấu "đang cập nhật dở" không.
func UpdateInProgressExists(installDir string) bool {
	_, err := os.Lstat(updateInProgressPath(installDir))
	return err == nil
}

// ClearUpdateInProgress xoá update-inprogress.json — không có tệp → nil.
func ClearUpdateInProgress(installDir string) error {
	err := os.Remove(updateInProgressPath(installDir))
	if err != nil && !errors.Is(err, os.ErrNotExist) {
		return err
	}
	return nil
}
