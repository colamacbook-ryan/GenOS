package hostlink

import (
	"errors"
	"os"
	"path/filepath"
	"testing"
)

func TestDoctorRequest_ConsumeVaKiemRequestID(t *testing.T) {
	root := t.TempDir()
	_ = EnsureDir(root)
	_ = os.WriteFile(DoctorRequestPath(root), []byte(`{"schema":1,"request_id":"0123456789abcdef","requested_at":"2026-10-03T00:00:00Z"}`), 0o666)
	if !HasDoctorRequest(root) {
		t.Fatal("phải thấy yêu cầu")
	}
	r, err := ConsumeDoctorRequest(root)
	if err != nil || r.RequestID != "0123456789abcdef" || HasDoctorRequest(root) {
		t.Fatalf("Consume = %+v, %v", r, err)
	}
	for _, bad := range []string{`{"request_id":"../../etc"}`, `{"request_id":"0123456789ABCDEF"}`, `{"request_id":"0123"}`, `nope`} {
		_ = os.WriteFile(DoctorRequestPath(root), []byte(bad), 0o666)
		if _, err := ConsumeDoctorRequest(root); !errors.Is(err, ErrBadDoctorRequest) || HasDoctorRequest(root) {
			t.Fatalf("%s: phải ErrBadDoctorRequest và xoá tệp, được %v", bad, err)
		}
	}
	if err := WriteDoctorStatus(root, DoctorStatus{RequestID: "0123456789abcdef", State: "running"}); err != nil {
		t.Fatal(err)
	}
	st, err := ReadDoctorStatus(root)
	if err != nil || st.State != "running" || st.Schema != 1 {
		t.Fatalf("doctor-status = %+v, %v", st, err)
	}
	if DiagnosticsDirPath(root) != filepath.Join(root, "run", "diagnostics") {
		t.Fatal(DiagnosticsDirPath(root))
	}
}
