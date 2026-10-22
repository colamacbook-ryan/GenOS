package secretgen

import (
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/pem"
	"fmt"
	"math/big"
	"os"
	"path/filepath"
	"time"
)

const (
	caCertFileName = "ca.crt"
	caKeyFileName  = "ca.key"

	// caValidity: CA tự ký cho môi trường nội bộ (localhost:8443) — 10 năm
	// là thời hạn hợp lý, không cần renew trong vòng đời thường gặp của một
	// máy cài Gen-Harness.
	caValidity = 10 * 365 * 24 * time.Hour
)

// EnsureCA trả về chứng chỉ + khoá CA nội bộ tại dir, đọc lại nếu đã có
// (idempotent) hoặc sinh mới tự ký nếu chưa có. generated=true nếu vừa
// sinh mới trong lần gọi này.
func EnsureCA(dir string) (certPEM, keyPEM []byte, generated bool, err error) {
	certPath := filepath.Join(dir, caCertFileName)
	keyPath := filepath.Join(dir, caKeyFileName)

	certExists := fileExists(certPath)
	keyExists := fileExists(keyPath)
	if certExists && keyExists {
		certPEM, err = os.ReadFile(certPath)
		if err != nil {
			return nil, nil, false, err
		}
		keyPEM, err = os.ReadFile(keyPath)
		if err != nil {
			return nil, nil, false, err
		}
		return certPEM, keyPEM, false, nil
	}

	certPEM, keyPEM, err = generateCA()
	if err != nil {
		return nil, nil, false, err
	}
	if err := writeFileAtomic(certPath, certPEM, filePerm); err != nil {
		return nil, nil, false, err
	}
	if err := writeFileAtomic(keyPath, keyPEM, filePerm); err != nil {
		return nil, nil, false, err
	}
	return certPEM, keyPEM, true, nil
}

func fileExists(path string) bool {
	_, err := os.Stat(path)
	return err == nil
}

// generateCA tạo một CA tự ký ECDSA P-256 để Caddy dùng phát hành chứng chỉ
// TLS cho https://localhost:8443, và để genh hướng dẫn Owner tin cậy CA này
// (Bước 8 — Hoàn tất).
func generateCA() (certPEM, keyPEM []byte, err error) {
	priv, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		return nil, nil, fmt.Errorf("sinh khoá CA: %w", err)
	}

	serial, err := rand.Int(rand.Reader, new(big.Int).Lsh(big.NewInt(1), 128))
	if err != nil {
		return nil, nil, fmt.Errorf("sinh số hiệu chứng chỉ: %w", err)
	}

	now := time.Now()
	tmpl := &x509.Certificate{
		SerialNumber: serial,
		Subject: pkix.Name{
			CommonName:   "Gen-Harness Local CA",
			Organization: []string{"Gen-Harness"},
		},
		NotBefore:             now.Add(-time.Hour),
		NotAfter:              now.Add(caValidity),
		KeyUsage:              x509.KeyUsageCertSign | x509.KeyUsageDigitalSignature | x509.KeyUsageCRLSign,
		BasicConstraintsValid: true,
		IsCA:                  true,
	}

	der, err := x509.CreateCertificate(rand.Reader, tmpl, tmpl, &priv.PublicKey, priv)
	if err != nil {
		return nil, nil, fmt.Errorf("tạo chứng chỉ CA: %w", err)
	}
	certPEM = pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: der})

	keyDER, err := x509.MarshalECPrivateKey(priv)
	if err != nil {
		return nil, nil, fmt.Errorf("mã hoá khoá CA: %w", err)
	}
	keyPEM = pem.EncodeToMemory(&pem.Block{Type: "EC PRIVATE KEY", Bytes: keyDER})

	return certPEM, keyPEM, nil
}
