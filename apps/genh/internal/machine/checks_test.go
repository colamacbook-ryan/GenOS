package machine

import (
	"errors"
	"testing"
	"time"
)

func TestCheckOS(t *testing.T) {
	cases := []struct {
		goos, goarch string
		want         Status
	}{
		{"linux", "amd64", StatusOK},
		{"linux", "arm64", StatusOK},
		{"darwin", "amd64", StatusOK},
		{"darwin", "arm64", StatusOK},
		{"windows", "amd64", StatusOK},
		{"windows", "arm64", StatusOK},
		{"linux", "386", StatusFail},
		{"plan9", "amd64", StatusFail},
	}
	for _, c := range cases {
		got := CheckOS(c.goos, c.goarch)
		if got.Status != c.want {
			t.Errorf("CheckOS(%s,%s) = %v, want %v", c.goos, c.goarch, got.Status, c.want)
		}
	}
}

func TestCheckRAM(t *testing.T) {
	cases := []struct {
		name  string
		bytes uint64
		want  Status
	}{
		{"dưới 4GB -> fail", 2 * 1024 * 1024 * 1024, StatusFail},
		{"đúng 4GB -> warn (dưới khuyến nghị)", 4 * 1024 * 1024 * 1024, StatusWarn},
		{"6GB -> warn", 6 * 1024 * 1024 * 1024, StatusWarn},
		{"đúng 8GB -> ok", 8 * 1024 * 1024 * 1024, StatusOK},
		{"16GB -> ok", 16 * 1024 * 1024 * 1024, StatusOK},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			got := CheckRAM(c.bytes)
			if got.Status != c.want {
				t.Errorf("CheckRAM(%d) = %v, want %v (detail=%q)", c.bytes, got.Status, c.want, got.Detail)
			}
		})
	}
}

func TestCheckDisk(t *testing.T) {
	const gb = 1024 * 1024 * 1024
	cases := []struct {
		free uint64
		want Status
	}{
		{3 * gb, StatusFail},
		{7 * gb, StatusWarn},
		{17 * gb, StatusOK}, // máy Fedora thật của Owner: 17 GB trống bị chặn oan ở v0.1.10
		{500 * gb, StatusOK},
	}
	for _, c := range cases {
		if got := CheckDisk(c.free).Status; got != c.want {
			t.Errorf("%d GB trống: got %v, want %v", c.free/gb, got, c.want)
		}
	}
}

func TestCheckPort(t *testing.T) {
	free := CheckPort(8443, false, "", 0)
	if free.Status != StatusOK {
		t.Errorf("port free: got %v, want ok", free.Status)
	}
	if free.Detail != "rảnh" {
		t.Errorf("port free detail = %q", free.Detail)
	}

	busy := CheckPort(8443, true, "nginx", 4412)
	if busy.Status != StatusFail {
		t.Errorf("port busy: got %v, want fail", busy.Status)
	}
	if busy.Detail == "" {
		t.Error("port busy detail rỗng")
	}
}

func TestCheckNetwork(t *testing.T) {
	if got := CheckNetwork(true, nil).Status; got != StatusOK {
		t.Errorf("network reachable: got %v, want ok", got)
	}
	if got := CheckNetwork(false, errors.New("timeout")).Status; got != StatusFail {
		t.Errorf("network unreachable: got %v, want fail", got)
	}
}

func TestCheckClock(t *testing.T) {
	if got := CheckClock(0).Status; got != StatusOK {
		t.Errorf("no drift: got %v, want ok", got)
	}
	if got := CheckClock(30 * time.Second).Status; got != StatusOK {
		t.Errorf("30s drift: got %v, want ok", got)
	}
	if got := CheckClock(10 * time.Minute).Status; got != StatusWarn {
		t.Errorf("10min drift: got %v, want warn", got)
	}
	if got := CheckClock(-10 * time.Minute).Status; got != StatusWarn {
		t.Errorf("-10min drift: got %v, want warn (giá trị tuyệt đối)", got)
	}
}

func TestOverall(t *testing.T) {
	results := []CheckResult{
		{Status: StatusOK},
		{Status: StatusWarn},
		{Status: StatusOK},
	}
	if got := Overall(results); got != StatusWarn {
		t.Errorf("Overall = %v, want warn", got)
	}

	results = append(results, CheckResult{Status: StatusFail})
	if got := Overall(results); got != StatusFail {
		t.Errorf("Overall = %v, want fail", got)
	}

	if got := Overall(nil); got != StatusOK {
		t.Errorf("Overall(nil) = %v, want ok", got)
	}
}

func TestProbeDiskFree_TempDir(t *testing.T) {
	free, err := ProbeDiskFree(t.TempDir())
	if err != nil {
		t.Fatalf("ProbeDiskFree: %v", err)
	}
	if free == 0 {
		t.Error("dung lượng trống phải > 0")
	}
}
