// Package secretgen cài Bước 4 (Sinh bí mật & cấu hình) của trình cài genh:
// khoá master, mật khẩu DB (superuser + role gh_app hạn quyền), khoá backup,
// CA TLS nội bộ, mã thiết lập một lần — ghi vào thư mục cấu hình với quyền
// 0600/0700, và idempotent: chạy lại không sinh lại bí mật đã có.
package secretgen

import (
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"math/big"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"time"
)

const (
	secretsFileName = "secrets.json"

	// filePerm/dirPerm: yêu cầu của docs/handoff/05-installer.md — ghi
	// ~/.gen-harness/config/ quyền 0600. os.Chmod trên Windows chỉ điều
	// khiển được bit ghi-được (read-only hay không), không có ý nghĩa
	// đầy đủ như trên POSIX; các lời gọi vẫn được thực hiện, chỉ không
	// kiểm tra kết quả bit-for-bit trong test khi GOOS=windows.
	filePerm = 0o600
	dirPerm  = 0o700
)

// Bundle là toàn bộ bí mật/cấu hình sinh ra ở Bước 4.
type Bundle struct {
	MasterKey  string `json:"master_key"`
	DBPassword string `json:"db_password"`
	// AppDBPassword là mật khẩu role `gh_app` — role hạn quyền (không
	// superuser, không BYPASSRLS) mà api/worker dùng để RLS thật sự có hiệu
	// lực (xem migration 0014, docs/reports/HANDOFF-v0.1.1.md mục "Hợp đồng
	// chung"), KHÁC DBPassword ở trên (mật khẩu superuser `gh`, chỉ migrate/
	// backup/bảo trì phân vùng dùng qua GH_ADMIN_DATABASE_URL). Bản cài cũ từ
	// trước khi có trường này thiếu app_db_password trong secrets.json —
	// fillMissing tự sinh + Ensure tự ghi lại lần chạy đầu tiên sau khi nâng
	// cấp genh, không làm hỏng các trường khác đã có.
	AppDBPassword string    `json:"app_db_password"`
	BackupKey     string    `json:"backup_key"`
	SetupToken    string    `json:"setup_token"`
	CreatedAt     time.Time `json:"created_at"`
}

// Result là kết quả của Ensure: bí mật đầy đủ, đường dẫn CA, và cờ cho biết
// lần chạy này có sinh mới thứ gì không (để bước cài log đúng "đã có sẵn"
// hay "vừa sinh").
type Result struct {
	Bundle       Bundle
	CACertPath   string
	CAKeyPath    string
	CACertPEM    []byte
	GeneratedNew bool
}

// Ensure đảm bảo thư mục cấu hình dir có đủ bí mật + CA nội bộ, sinh mới
// đúng những gì còn thiếu và giữ nguyên phần đã có — an toàn để gọi lại
// nhiều lần (idempotent), kể cả khi tệp secrets.json bị thiếu vài trường do
// phiên bản cũ hơn.
func Ensure(dir string) (Result, error) {
	if err := os.MkdirAll(dir, dirPerm); err != nil {
		return Result{}, fmt.Errorf("tạo thư mục cấu hình %s: %w", dir, err)
	}
	_ = os.Chmod(dir, dirPerm)

	bundle, existed, err := loadBundle(dir)
	if err != nil {
		return Result{}, fmt.Errorf("đọc bí mật hiện có: %w", err)
	}

	generated := !existed
	if err := fillMissing(&bundle); err != nil {
		return Result{}, err
	}
	if bundle.dirty {
		generated = true
	}

	if generated {
		if bundle.CreatedAt.IsZero() {
			bundle.CreatedAt = time.Now().UTC()
		}
		if err := saveBundle(dir, bundle.Bundle); err != nil {
			return Result{}, fmt.Errorf("ghi bí mật: %w", err)
		}
	}

	certPEM, keyPEM, caGenerated, err := EnsureCA(dir)
	if err != nil {
		return Result{}, fmt.Errorf("sinh CA nội bộ: %w", err)
	}
	_ = keyPEM

	return Result{
		Bundle:       bundle.Bundle,
		CACertPath:   filepath.Join(dir, caCertFileName),
		CAKeyPath:    filepath.Join(dir, caKeyFileName),
		CACertPEM:    certPEM,
		GeneratedNew: generated || caGenerated,
	}, nil
}

// mutableBundle theo dõi việc có trường nào vừa được sinh mới hay không,
// để Ensure biết có cần ghi lại tệp hay không.
type mutableBundle struct {
	Bundle
	dirty bool
}

func fillMissing(b *mutableBundle) error {
	fields := []struct {
		value *string
		gen   func() (string, error)
	}{
		{&b.MasterKey, func() (string, error) { return randomHex(32) }},
		{&b.DBPassword, func() (string, error) { return randomHex(24) }},
		{&b.AppDBPassword, func() (string, error) { return randomHex(24) }},
		{&b.BackupKey, func() (string, error) { return randomHex(32) }},
		{&b.SetupToken, randomSetupCode},
	}
	for _, f := range fields {
		if *f.value != "" {
			continue
		}
		v, err := f.gen()
		if err != nil {
			return fmt.Errorf("sinh bí mật: %w", err)
		}
		*f.value = v
		b.dirty = true
	}
	return nil
}

// Exists báo secrets.json đã tồn tại tại dir hay chưa — KHÔNG đọc/phân tích
// nội dung (khác Load/LoadFillingMissing), chỉ dùng để hỏi nhanh "máy này đã
// từng chạy xong Bước 4 (Sinh bí mật) chưa" mà không cần bí mật thật, ví dụ
// `genh install` tự phát hiện máy đã cài (xem internal/install.
// DetectExistingInstall, docs/reports/HANDOFF-v0.1.1.md mục "Lỗi cần sửa" #2
// của v0.1.3).
func Exists(dir string) bool {
	_, err := os.Stat(filepath.Join(dir, secretsFileName))
	return err == nil
}

// Load đọc bí mật đã sinh tại dir mà KHÔNG sinh mới bất kỳ trường nào còn
// thiếu (khác Ensure) — dùng cho các lệnh vận hành (genh status/open/…,
// internal/ops) chỉ cần ĐỌC LẠI bí mật của một bản cài đã có, không nên tự
// tạo bí mật mới nếu máy chưa từng chạy `genh install`. Trả lỗi rõ ràng nếu
// chưa có secrets.json tại dir.
//
// LƯU Ý: hàm này CỐ Ý không tự bổ sung trường thiếu — dùng LoadFillingMissing
// khi cần hành vi đó (xem docs/reports/HANDOFF-v0.1.1.md mục "Lỗi cần sửa"
// #1 của v0.1.2: một bản cài từ v0.1.0 nâng cấp genh lên có secrets.json
// thiếu app_db_password/backup_key — ops.Env.LoadSecrets dùng
// LoadFillingMissing, không dùng Load).
func Load(dir string) (Bundle, error) {
	b, existed, err := loadBundle(dir)
	if err != nil {
		return Bundle{}, err
	}
	if !existed {
		return Bundle{}, fmt.Errorf("chưa có bí mật tại %s — chạy `genh install` trước", dir)
	}
	return b.Bundle, nil
}

// LoadFillingMissing đọc bí mật đã sinh tại dir NHƯ Load (trả cùng lỗi rõ
// ràng nếu secrets.json chưa từng tồn tại — không tự "cài" một bản cài chưa
// từng chạy `genh install`), nhưng nếu tệp ĐÃ có mà THIẾU một vài trường
// (bản cài từ một phiên bản genh cũ hơn, trước khi trường đó tồn tại — ví dụ
// AppDBPassword/BackupKey chỉ có từ v0.1.1), tự sinh bổ sung đúng những
// trường còn thiếu (tái dùng fillMissing — cùng logic Ensure dùng ở Bước 4
// cài đặt) và GHI LẠI secrets.json ngay, để các lần chạy sau không phải sinh
// lại. Các trường đã có giữ nguyên giá trị cũ — không bao giờ sinh đè.
//
// Dùng ở ops.Env.LoadSecrets (mọi lệnh vận hành `genh ...` đọc bí mật qua
// đây) — nếu không bổ sung, một bản cài v0.1.0 nâng cấp genh lên sẽ có
// AppDBPassword rỗng mãi mãi, khiến MỌI lệnh `docker compose` báo lỗi ở
// ${GH_APP_DB_PASSWORD:?...} (xem HANDOFF-v0.1.1.md).
func LoadFillingMissing(dir string) (Bundle, error) {
	b, existed, err := loadBundle(dir)
	if err != nil {
		return Bundle{}, err
	}
	if !existed {
		return Bundle{}, fmt.Errorf("chưa có bí mật tại %s — chạy `genh install` trước", dir)
	}

	if err := fillMissing(&b); err != nil {
		return Bundle{}, err
	}
	if b.dirty {
		if err := saveBundle(dir, b.Bundle); err != nil {
			return Bundle{}, fmt.Errorf("ghi bí mật đã bổ sung: %w", err)
		}
	}
	return b.Bundle, nil
}

// RegenerateSetupToken sinh một mã thiết lập MỚI cho bundle đã có tại dir
// (dùng cho `genh reset-setup`), làm mã cũ hết hiệu lực ngay — trả về mã mới.
//
// LƯU Ý về CreatedAt: trường Bundle.CreatedAt hiện chỉ có một ý nghĩa duy
// nhất trong toàn bộ codebase (xem cmd/genh/main.go buildFinishInfo): tính
// hạn 24 giờ của CHÍNH mã thiết lập (CodeExpiresIn = CreatedAt + 24h). Không
// có nơi nào khác đọc CreatedAt như "tuổi của bundle bí mật nói chung" (tất
// cả các bí mật khác — MasterKey, DBPassword… — không có hạn dùng). Vì vậy
// việc RegenerateSetupToken cập nhật lại CreatedAt = now là ĐÚNG với ý nghĩa
// duy nhất mà trường này đang mang, không cần thêm trường
// SetupTokenCreatedAt riêng (sẽ chỉ là một alias không cần thiết của
// CreatedAt cho tới khi có một ý nghĩa THỨ HAI thật sự cần tách ra).
func RegenerateSetupToken(dir string) (string, error) {
	b, existed, err := loadBundle(dir)
	if err != nil {
		return "", fmt.Errorf("đọc bí mật hiện có: %w", err)
	}
	if !existed {
		return "", fmt.Errorf("chưa có bí mật tại %s — chạy `genh install` trước", dir)
	}

	newToken, err := randomSetupCode()
	if err != nil {
		return "", fmt.Errorf("sinh mã thiết lập mới: %w", err)
	}
	b.SetupToken = newToken
	b.CreatedAt = time.Now().UTC()

	if err := saveBundle(dir, b.Bundle); err != nil {
		return "", fmt.Errorf("ghi bí mật: %w", err)
	}
	return newToken, nil
}

func loadBundle(dir string) (mutableBundle, bool, error) {
	path := filepath.Join(dir, secretsFileName)
	data, err := os.ReadFile(path)
	if os.IsNotExist(err) {
		return mutableBundle{}, false, nil
	}
	if err != nil {
		return mutableBundle{}, false, err
	}
	var b Bundle
	if err := json.Unmarshal(data, &b); err != nil {
		return mutableBundle{}, false, fmt.Errorf("secrets.json hỏng: %w", err)
	}
	return mutableBundle{Bundle: b}, true, nil
}

func saveBundle(dir string, b Bundle) error {
	data, err := json.MarshalIndent(b, "", "  ")
	if err != nil {
		return err
	}
	path := filepath.Join(dir, secretsFileName)
	return writeFileAtomic(path, data, filePerm)
}

// writeFileAtomic ghi qua tệp tạm rồi rename, tránh để lại tệp bí mật nửa
// vời nếu tiến trình bị ngắt giữa chừng.
func writeFileAtomic(path string, data []byte, perm os.FileMode) error {
	tmp := path + ".tmp"
	if err := os.WriteFile(tmp, data, perm); err != nil {
		return err
	}
	if err := os.Chmod(tmp, perm); err != nil && runtime.GOOS != "windows" {
		return err
	}
	return os.Rename(tmp, path)
}

func randomHex(nBytes int) (string, error) {
	buf := make([]byte, nBytes)
	if _, err := rand.Read(buf); err != nil {
		return "", err
	}
	return hex.EncodeToString(buf), nil
}

// setupCodeAlphabet loại bỏ các ký tự dễ nhầm khi gõ tay (0/O, 1/I).
const setupCodeAlphabet = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"

// randomSetupCode sinh mã thiết lập một lần dạng "K7QF-2MXD-9PLA", dùng cả
// làm mã hiển thị cho Owner lẫn giá trị token trong /setup?token=.
func randomSetupCode() (string, error) {
	const groups, groupLen = 3, 4
	var sb strings.Builder
	for g := 0; g < groups; g++ {
		if g > 0 {
			sb.WriteByte('-')
		}
		for i := 0; i < groupLen; i++ {
			idx, err := randIndex(len(setupCodeAlphabet))
			if err != nil {
				return "", err
			}
			sb.WriteByte(setupCodeAlphabet[idx])
		}
	}
	return sb.String(), nil
}

func randIndex(n int) (int, error) {
	v, err := rand.Int(rand.Reader, big.NewInt(int64(n)))
	if err != nil {
		return 0, err
	}
	return int(v.Int64()), nil
}
