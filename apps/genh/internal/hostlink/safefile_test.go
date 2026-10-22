//go:build !windows

// Hai test dưới ép đúng cửa sổ race "thay tệp giữa Lstat và Open". Trên Windows
// os.SameFile nạp mã định danh tệp LƯỜI (mở lại theo đường dẫn lúc so sánh), nên
// FileInfo từ Lstat sẽ khớp với tệp MỚI và phép kiểm "tệp bị thay giữa chừng"
// không bao giờ kích hoạt ở đó (hạn chế có từ trước, không do bước thử lại):
// đọc chỉ đơn giản trả về nội dung mới. Vì vậy chỉ chạy trên Unix.

package hostlink

import (
	"errors"
	"strings"
	"testing"
)

// Hotfix sau v0.1.49: người ghi rename tệp đè đúng khoảng giữa Lstat và Open của
// người đọc ⇒ inode đổi ⇒ trước đây trả "tệp bị thay giữa chừng" (test heartbeat
// chập chờn trên CI). Thay thế nguyên tử là hợp lệ: đọc phải thử lại và thành công.
func TestReadHeartbeat_ThayNguyenTuGiuaLstatVaOpen_ThuLaiThanhCong(t *testing.T) {
	dir := t.TempDir()
	if err := EnsureDir(dir); err != nil {
		t.Fatal(err)
	}
	path := HeartbeatPath(dir)
	if err := writeJSON(path, Heartbeat{PID: 1, Op: "restore"}); err != nil {
		t.Fatal(err)
	}

	calls := 0
	afterLstatHook = func() {
		calls++
		if calls == 1 { // đúng một lần rename đè ngay sau Lstat
			if err := writeJSON(path, Heartbeat{PID: 2, Op: "restore"}); err != nil {
				t.Errorf("ghi lại: %v", err)
			}
		}
	}
	defer func() { afterLstatHook = nil }()

	hb, err := ReadHeartbeat(dir)
	if err != nil {
		t.Fatalf("ReadHeartbeat phải thử lại và thành công, được: %v", err)
	}
	if hb.PID != 2 || calls != 2 {
		t.Fatalf("phải đọc bản mới ở lần thử thứ 2: pid=%d, số lần Lstat=%d", hb.PID, calls)
	}
}

// Nếu đích bị tráo LIÊN TỤC (không phải một lần ghi nguyên tử) thì vẫn trả lỗi
// an toàn sau đúng stateFileReadAttempts lần — không thử lại vô hạn.
func TestReadStateFile_BiThayLienTuc_VanTuChoiSauSoLanToiDa(t *testing.T) {
	dir := t.TempDir()
	if err := EnsureDir(dir); err != nil {
		t.Fatal(err)
	}
	path := HeartbeatPath(dir)
	if err := writeJSON(path, Heartbeat{PID: 1}); err != nil {
		t.Fatal(err)
	}

	calls := 0
	afterLstatHook = func() {
		calls++
		_ = writeJSON(path, Heartbeat{PID: calls})
	}
	defer func() { afterLstatHook = nil }()

	_, err := readStateFile(path, false)
	if !errors.Is(err, errUnsafeStateFile) || !errors.Is(err, errStateFileReplaced) {
		t.Fatalf("phải từ chối bằng errUnsafeStateFile (tệp bị thay giữa chừng), được: %v", err)
	}
	if !strings.Contains(err.Error(), "tệp bị thay giữa chừng") {
		t.Errorf("thông báo phải giữ nguyên: %v", err)
	}
	if calls != stateFileReadAttempts {
		t.Fatalf("phải dừng sau %d lần thử, được %d", stateFileReadAttempts, calls)
	}
}
