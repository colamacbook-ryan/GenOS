package secretgen

import (
	"crypto/x509"
	"encoding/pem"
	"os"
	"path/filepath"
	"runtime"
	"testing"
)

func TestEnsure_FirstRunGeneratesEverything(t *testing.T) {
	dir := filepath.Join(t.TempDir(), "config")

	res, err := Ensure(dir)
	if err != nil {
		t.Fatalf("Ensure: %v", err)
	}
	if !res.GeneratedNew {
		t.Error("lần chạy đầu phải báo GeneratedNew=true")
	}

	b := res.Bundle
	for name, v := range map[string]string{
		"MasterKey":     b.MasterKey,
		"DBPassword":    b.DBPassword,
		"AppDBPassword": b.AppDBPassword,
		"BackupKey":     b.BackupKey,
		"SetupToken":    b.SetupToken,
	} {
		if v == "" {
			t.Errorf("%s rỗng sau lần sinh đầu tiên", name)
		}
	}
	if b.CreatedAt.IsZero() {
		t.Error("CreatedAt chưa được gán")
	}

	if _, err := os.Stat(res.CACertPath); err != nil {
		t.Errorf("thiếu tệp CA cert: %v", err)
	}
	if _, err := os.Stat(res.CAKeyPath); err != nil {
		t.Errorf("thiếu tệp CA key: %v", err)
	}

	block, _ := pem.Decode(res.CACertPEM)
	if block == nil {
		t.Fatal("CACertPEM không giải mã được PEM")
	}
	cert, err := x509.ParseCertificate(block.Bytes)
	if err != nil {
		t.Fatalf("ParseCertificate: %v", err)
	}
	if !cert.IsCA {
		t.Error("chứng chỉ sinh ra không phải CA")
	}
	if cert.NotAfter.Before(cert.NotBefore) {
		t.Error("NotAfter phải sau NotBefore")
	}
}

func TestEnsure_IdempotentOnSecondRun(t *testing.T) {
	dir := filepath.Join(t.TempDir(), "config")

	first, err := Ensure(dir)
	if err != nil {
		t.Fatalf("Ensure (1): %v", err)
	}

	second, err := Ensure(dir)
	if err != nil {
		t.Fatalf("Ensure (2): %v", err)
	}
	if second.GeneratedNew {
		t.Error("lần chạy thứ hai không được sinh lại bí mật (GeneratedNew phải là false)")
	}

	if first.Bundle != second.Bundle {
		t.Errorf("bí mật thay đổi giữa hai lần chạy:\n1: %+v\n2: %+v", first.Bundle, second.Bundle)
	}
	if string(first.CACertPEM) != string(second.CACertPEM) {
		t.Error("CA cert bị sinh lại ở lần chạy thứ hai")
	}
}

func TestEnsure_FillsOnlyMissingFields(t *testing.T) {
	dir := filepath.Join(t.TempDir(), "config")

	first, err := Ensure(dir)
	if err != nil {
		t.Fatalf("Ensure (1): %v", err)
	}

	// Giả lập secrets.json cũ, thiếu trường backup_key (ví dụ nâng cấp từ
	// bản trước khi có tính năng sao lưu) — Ensure phải giữ nguyên các
	// trường đã có và chỉ sinh thêm trường thiếu.
	partial := first.Bundle
	partial.BackupKey = ""
	if err := saveBundle(dir, partial); err != nil {
		t.Fatalf("saveBundle: %v", err)
	}

	res, err := Ensure(dir)
	if err != nil {
		t.Fatalf("Ensure (2): %v", err)
	}
	if !res.GeneratedNew {
		t.Error("phải báo GeneratedNew=true vì backup_key vừa được điền")
	}
	if res.Bundle.BackupKey == "" {
		t.Error("backup_key vẫn rỗng sau khi Ensure điền lại")
	}
	if res.Bundle.MasterKey != first.Bundle.MasterKey {
		t.Error("master_key không được đổi khi chỉ backup_key bị thiếu")
	}
	if res.Bundle.SetupToken != first.Bundle.SetupToken {
		t.Error("setup_token không được đổi khi chỉ backup_key bị thiếu")
	}
}

// TestEnsure_UpgradesOldBundleMissingAppDBPassword mô phỏng đúng bản cài cũ
// (secrets.json ghi trước khi có trường app_db_password/role gh_app):
// Ensure phải tự sinh bổ sung, không lỗi/không đè các bí mật khác đã có.
func TestEnsure_UpgradesOldBundleMissingAppDBPassword(t *testing.T) {
	dir := filepath.Join(t.TempDir(), "config")

	first, err := Ensure(dir)
	if err != nil {
		t.Fatalf("Ensure (1): %v", err)
	}

	old := first.Bundle
	old.AppDBPassword = "" // secrets.json bản cũ không có trường này
	if err := saveBundle(dir, old); err != nil {
		t.Fatalf("saveBundle: %v", err)
	}

	res, err := Ensure(dir)
	if err != nil {
		t.Fatalf("Ensure (2): %v", err)
	}
	if !res.GeneratedNew {
		t.Error("phải báo GeneratedNew=true vì app_db_password vừa được điền")
	}
	if res.Bundle.AppDBPassword == "" {
		t.Error("app_db_password vẫn rỗng sau khi Ensure điền lại")
	}
	if res.Bundle.MasterKey != first.Bundle.MasterKey || res.Bundle.DBPassword != first.Bundle.DBPassword {
		t.Error("các bí mật khác không được đổi khi chỉ app_db_password bị thiếu")
	}

	reloaded, err := Load(dir)
	if err != nil {
		t.Fatalf("Load sau khi nâng cấp: %v", err)
	}
	if reloaded.AppDBPassword != res.Bundle.AppDBPassword {
		t.Error("app_db_password vừa sinh phải được ghi xuống đĩa, đọc lại phải khớp")
	}
}

// TestLoadFillingMissing_UpgradesV010SecretsWithoutRegeneratingCall tái hiện
// đúng sự cố nâng cấp máy v0.1.0 → v0.1.2 (docs/reports/HANDOFF-v0.1.1.md,
// mục "Lỗi cần sửa" #1 của v0.1.2): secrets.json kiểu v0.1.0 không có trường
// app_db_password/backup_key (JSON chỉ có master_key/db_password/
// setup_token/created_at) — LoadFillingMissing phải bổ sung cả hai trường
// còn thiếu, GHI LẠI ngay xuống đĩa, và giữ nguyên các trường đã có.
func TestLoadFillingMissing_UpgradesV010SecretsWithoutRegeneratingCall(t *testing.T) {
	dir := filepath.Join(t.TempDir(), "config")
	if err := os.MkdirAll(dir, 0o700); err != nil {
		t.Fatalf("MkdirAll: %v", err)
	}

	// secrets.json kiểu v0.1.0 thật — JSON không hề có 2 khoá app_db_password/
	// backup_key (khác việc lưu chuỗi rỗng: v0.1.0 chưa từng biết tới các
	// trường này).
	v010JSON := `{
  "master_key": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "db_password": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
  "setup_token": "K7QF-2MXD-9PLA",
  "created_at": "2026-01-01T00:00:00Z"
}`
	if err := os.WriteFile(filepath.Join(dir, secretsFileName), []byte(v010JSON), filePerm); err != nil {
		t.Fatalf("WriteFile: %v", err)
	}

	b, err := LoadFillingMissing(dir)
	if err != nil {
		t.Fatalf("LoadFillingMissing: %v", err)
	}
	if b.AppDBPassword == "" {
		t.Error("app_db_password vẫn rỗng sau LoadFillingMissing — genh update vẫn sẽ lỗi ${GH_APP_DB_PASSWORD:?...}")
	}
	if b.BackupKey == "" {
		t.Error("backup_key vẫn rỗng sau LoadFillingMissing")
	}
	if b.MasterKey != "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" {
		t.Error("master_key đã có không được đổi")
	}
	if b.DBPassword != "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb" {
		t.Error("db_password đã có không được đổi")
	}
	if b.SetupToken != "K7QF-2MXD-9PLA" {
		t.Error("setup_token đã có không được đổi")
	}

	// Phải GHI LẠI xuống đĩa ngay lần gọi này — lần gọi sau (kể cả Load
	// thường, không bổ sung) phải đọc lại đúng giá trị vừa sinh, không sinh
	// lại giá trị khác.
	reloaded, err := Load(dir)
	if err != nil {
		t.Fatalf("Load sau LoadFillingMissing: %v", err)
	}
	if reloaded.AppDBPassword != b.AppDBPassword || reloaded.BackupKey != b.BackupKey {
		t.Error("giá trị vừa bổ sung phải được ghi xuống đĩa ngay, đọc lại phải khớp")
	}

	// Gọi lại LoadFillingMissing lần hai: không còn gì thiếu, phải trả đúng
	// y hệt, không sinh lại giá trị khác.
	again, err := LoadFillingMissing(dir)
	if err != nil {
		t.Fatalf("LoadFillingMissing (lần 2): %v", err)
	}
	if again.AppDBPassword != b.AppDBPassword || again.BackupKey != b.BackupKey {
		t.Error("lần gọi thứ hai (không còn thiếu gì) không được sinh lại giá trị khác")
	}
}

// TestLoadFillingMissing_ErrorsWhenNeverInstalled giữ đúng hợp đồng như Load:
// không tự "cài" (không sinh secrets.json từ đầu) nếu máy chưa từng chạy
// `genh install`.
func TestLoadFillingMissing_ErrorsWhenNeverInstalled(t *testing.T) {
	dir := filepath.Join(t.TempDir(), "config")
	if _, err := LoadFillingMissing(dir); err == nil {
		t.Fatal("muốn lỗi khi secrets.json chưa từng tồn tại")
	}
	if _, err := os.Stat(filepath.Join(dir, secretsFileName)); err == nil {
		t.Error("LoadFillingMissing không được tự tạo secrets.json khi chưa cài")
	}
}

func TestEnsure_FilePermissions(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("bit quyền POSIX (0600/0700) không áp dụng trên Windows")
	}

	dir := filepath.Join(t.TempDir(), "config")
	res, err := Ensure(dir)
	if err != nil {
		t.Fatalf("Ensure: %v", err)
	}

	dirInfo, err := os.Stat(dir)
	if err != nil {
		t.Fatalf("stat dir: %v", err)
	}
	if perm := dirInfo.Mode().Perm(); perm != dirPerm {
		t.Errorf("quyền thư mục config = %o, muốn %o", perm, dirPerm)
	}

	for _, p := range []string{
		filepath.Join(dir, secretsFileName),
		res.CACertPath,
		res.CAKeyPath,
	} {
		info, err := os.Stat(p)
		if err != nil {
			t.Fatalf("stat %s: %v", p, err)
		}
		if perm := info.Mode().Perm(); perm != filePerm {
			t.Errorf("quyền %s = %o, muốn %o", p, perm, filePerm)
		}
	}
}

func TestRandomSetupCode_Format(t *testing.T) {
	code, err := randomSetupCode()
	if err != nil {
		t.Fatalf("randomSetupCode: %v", err)
	}
	// XXXX-XXXX-XXXX
	if len(code) != 14 {
		t.Fatalf("độ dài mã thiết lập = %d, muốn 14 (XXXX-XXXX-XXXX): %q", len(code), code)
	}
	for i, c := range code {
		if i == 4 || i == 9 {
			if c != '-' {
				t.Errorf("vị trí %d phải là '-', được %q trong %q", i, c, code)
			}
			continue
		}
		if !containsRune(setupCodeAlphabet, c) {
			t.Errorf("ký tự %q ngoài bảng chữ cái mã thiết lập trong %q", c, code)
		}
	}
}

func containsRune(s string, r rune) bool {
	for _, c := range s {
		if c == r {
			return true
		}
	}
	return false
}
