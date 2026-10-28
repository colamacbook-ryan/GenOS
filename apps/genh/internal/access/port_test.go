package access

import (
	"strings"
	"testing"
)

func TestSetPort_WritesPortAndPublicURL(t *testing.T) {
	cp := composeIn(t)
	if _, _, err := Ensure(cp, true); err != nil {
		t.Fatal(err)
	}
	changed, err := SetPort(cp, 18478)
	if err != nil || !changed {
		t.Fatalf("SetPort: %v changed=%v", err, changed)
	}
	got := readEnv(t, cp)
	if !strings.Contains(got, "GH_PORT=18478\n") || !strings.Contains(got, "GH_PUBLIC_URL=https://localhost:18478\n") {
		t.Errorf(".env = %q", got)
	}
	if p := ReadPort(cp); p != 18478 {
		t.Errorf("ReadPort = %d", p)
	}
	if changed, _ := SetPort(cp, 18478); changed {
		t.Error("ghi lại cùng cổng không được báo changed")
	}
}

func TestSetPort_DefaultOnFreshFileWritesNothing(t *testing.T) {
	cp := composeIn(t)
	_, _, _ = Ensure(cp, true)
	changed, err := SetPort(cp, DefaultPort)
	if err != nil || changed {
		t.Fatalf("changed=%v err=%v", changed, err)
	}
	if strings.Contains(readEnv(t, cp), "GH_PORT") {
		t.Error("cổng mặc định không cần GH_PORT")
	}
}

func TestWrite_KeepsPortWhenStateHasNone(t *testing.T) {
	cp := composeIn(t)
	_, _, _ = Ensure(cp, true)
	_, _ = SetPort(cp, 18478)
	// như genh remote: dựng State mới không có Port
	ns := State{Mode: ModeLAN, BindAddr: BindAll, SiteAddress: "gen.local", PublicURL: PublicURL(ModeLAN, "gen.local", 18478)}
	if err := Write(cp, ns); err != nil {
		t.Fatal(err)
	}
	if p := ReadPort(cp); p != 18478 {
		t.Errorf("Write làm rơi GH_PORT: %d", p)
	}
}
