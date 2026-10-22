package runtime

import (
	"context"
	"errors"
	"os/exec"
	"testing"

	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/dockercli"
	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/dockercli/fake"
)

func TestEngineMeetsMinimum(t *testing.T) {
	cases := []struct {
		version string
		want    bool
	}{
		{"27.1.1", true},
		{"24.0.9", true},
		{"v25.0.3", true},
		{"23.0.6", false},
		{"9.9.9", false},
		{"rác", false},
		{"", false},
	}
	for _, c := range cases {
		if got := EngineMeetsMinimum(c.version); got != c.want {
			t.Errorf("EngineMeetsMinimum(%q) = %v, muốn %v", c.version, got, c.want)
		}
	}
}

func TestParseServerVersion(t *testing.T) {
	data := []byte(`{"Client":{"Version":"27.1.1"},"Server":{"Version":"27.1.1"}}`)
	v, err := parseServerVersion(data)
	if err != nil {
		t.Fatalf("parseServerVersion: %v", err)
	}
	if v != "27.1.1" {
		t.Errorf("version = %q, muốn 27.1.1", v)
	}
}

func TestParseComposeVersion(t *testing.T) {
	v, err := parseComposeVersion([]byte(`{"version":"v2.29.1"}`))
	if err != nil {
		t.Fatalf("parseComposeVersion: %v", err)
	}
	if v != "v2.29.1" {
		t.Errorf("version = %q, muốn v2.29.1", v)
	}
}

func TestDetect_DockerNotFound(t *testing.T) {
	r := &fake.Runner{Responses: []fake.Response{
		{Match: fake.MatchArgsContain("version"), Err: exec.ErrNotFound},
	}}
	info := Detect(context.Background(), r)
	if info.Available {
		t.Error("Available phải false khi docker không có trên PATH")
	}
	if info.Reason == "" {
		t.Error("Reason không được rỗng")
	}
}

func TestDetect_DaemonNotRunning(t *testing.T) {
	r := &fake.Runner{Responses: []fake.Response{
		{Match: fake.MatchArgsContain("version"), Err: errors.New("Cannot connect to the Docker daemon")},
	}}
	info := Detect(context.Background(), r)
	if info.Available {
		t.Error("Available phải false khi daemon không chạy")
	}
}

func TestDetect_EngineTooOld(t *testing.T) {
	r := &fake.Runner{Responses: []fake.Response{
		{Match: fake.MatchArgsContain("version"), Output: []byte(`{"Server":{"Version":"20.10.5"}}`)},
	}}
	info := Detect(context.Background(), r)
	if !info.Available {
		t.Fatal("Available phải true, daemon phản hồi được")
	}
	if info.EngineOK {
		t.Error("EngineOK phải false, 20.10 < 24")
	}
	if info.Ready() {
		t.Error("Ready() phải false")
	}
}

func TestDetect_ComposeMissing(t *testing.T) {
	r := &fake.Runner{Responses: []fake.Response{
		{Match: fake.MatchArgsContain("compose", "version"), Err: errors.New("docker: 'compose' is not a docker command")},
		{Match: fake.MatchArgsContain("version"), Output: []byte(`{"Server":{"Version":"27.1.1"}}`)},
	}}
	info := Detect(context.Background(), r)
	if !info.EngineOK {
		t.Fatal("EngineOK phải true")
	}
	if info.ComposeOK {
		t.Error("ComposeOK phải false")
	}
	if info.Ready() {
		t.Error("Ready() phải false khi thiếu compose")
	}
}

func TestDetect_ReadyWhenBothOK(t *testing.T) {
	r := &fake.Runner{Responses: []fake.Response{
		{Match: fake.MatchArgsContain("compose", "version"), Output: []byte(`{"version":"v2.29.1"}`)},
		{Match: fake.MatchArgsContain("version"), Output: []byte(`{"Server":{"Version":"27.1.1"}}`)},
	}}
	info := Detect(context.Background(), r)
	if !info.Ready() {
		t.Errorf("Ready() phải true, info=%+v", info)
	}
	if info.EngineVersion != "27.1.1" || info.ComposeVersion != "v2.29.1" {
		t.Errorf("thiếu version trong Info: %+v", info)
	}
}

// interface compile-time check — Detect chỉ nên phụ thuộc dockercli.Runner.
var _ dockercli.Runner = (*fake.Runner)(nil)
