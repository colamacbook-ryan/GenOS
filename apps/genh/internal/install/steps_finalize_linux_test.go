// Tệp chỉ biên dịch trên Linux (hậu tố _linux_test.go): addToNSSDB chỉ được
// gọi trên Linux (trustBrowserOS trả errBrowserTrustUnsupported ở nền tảng
// khác) và cần certutil của NSS (libnss3-tools). Trên Windows, "certutil" trong
// PATH là CertUtil của Windows — một chương trình KHÁC hẳn ("Unknown arg: -d"),
// nên chạy test này ở đó là kiểm sai đối tượng chứ không phải lỗi của genh.

package install

import (
	"context"
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/pem"
	"math/big"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

// Chạy certutil thật (nếu máy có) trên một NSS db tạm: tạo db mới rồi thêm CA.
func TestAddToNSSDB_RealCertutil(t *testing.T) {
	if !lookPath("certutil") {
		t.Skip("máy không có certutil")
	}
	dir := filepath.Join(t.TempDir(), "nssdb")
	cert := filepath.Join(t.TempDir(), "ca.crt")
	if err := os.WriteFile(cert, realCAPEM(t), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := addToNSSDB(context.Background(), dir, cert); err != nil {
		t.Fatalf("addToNSSDB: %v", err)
	}
	out, err := exec.Command("certutil", "-d", "sql:"+dir, "-L").CombinedOutput()
	if err != nil || !strings.Contains(string(out), "Gen-Harness local CA") {
		t.Fatalf("CA không có trong db: %v\n%s", err, out)
	}
}

func realCAPEM(t *testing.T) []byte {
	t.Helper()
	key, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	tmpl := &x509.Certificate{
		SerialNumber: big.NewInt(1), Subject: pkix.Name{CommonName: "Gen-Harness test CA"},
		NotBefore: time.Now(), NotAfter: time.Now().Add(time.Hour),
		IsCA: true, BasicConstraintsValid: true, KeyUsage: x509.KeyUsageCertSign,
	}
	der, err := x509.CreateCertificate(rand.Reader, tmpl, tmpl, &key.PublicKey, key)
	if err != nil {
		t.Fatal(err)
	}
	return pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: der})
}
