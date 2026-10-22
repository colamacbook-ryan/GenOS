package ops

import (
	"context"
	"errors"
	"testing"

	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/dockercli/fake"
)

func TestCheckDiskFree_TakesMinOfInstallDirAndDockerRoot(t *testing.T) {
	installDir := t.TempDir()
	dockerRoot := t.TempDir()
	fr := &fake.Runner{Responses: []fake.Response{
		{Match: exactArgs("info"), Output: []byte(dockerRoot + "\n")},
	}}
	deps := UpdateDeps{DiskFree: func(p string) (uint64, error) {
		switch p {
		case installDir:
			return 50 << 30, nil
		case dockerRoot:
			return 3 << 30, nil
		}
		return 0, errors.New("đường lạ " + p)
	}}
	free, path, err := checkDiskFree(context.Background(), fr, &Env{InstallDir: installDir}, deps)
	if err != nil {
		t.Fatal(err)
	}
	if free != 3<<30 || path != dockerRoot {
		t.Errorf("muốn số nhỏ hơn (DockerRootDir 3 GB), được %d tại %s", free, path)
	}
}

func TestCheckDiskFree_DockerInfoFails_OnlyInstallDir(t *testing.T) {
	installDir := t.TempDir()
	fr := &fake.Runner{Responses: []fake.Response{
		{Match: exactArgs("info"), Err: errors.New("docker không chạy")},
	}}
	var probed []string
	deps := UpdateDeps{DiskFree: func(p string) (uint64, error) {
		probed = append(probed, p)
		return 42 << 30, nil
	}}
	free, path, err := checkDiskFree(context.Background(), fr, &Env{InstallDir: installDir}, deps)
	if err != nil || free != 42<<30 || path != installDir {
		t.Fatalf("muốn 42 GB tại %s, được %d tại %s (%v)", installDir, free, path, err)
	}
	if len(probed) != 1 {
		t.Errorf("chỉ đo gốc cài đặt, được %v", probed)
	}
}

// Docker Desktop: DockerRootDir là đường trong máy ảo, không có trên máy chủ.
func TestCheckDiskFree_DockerRootMissingOnHost_Ignored(t *testing.T) {
	installDir := t.TempDir()
	fr := &fake.Runner{Responses: []fake.Response{
		{Match: exactArgs("info"), Output: []byte("/khong/ton/tai/docker-root\n")},
	}}
	deps := UpdateDeps{DiskFree: func(string) (uint64, error) { return 10 << 30, nil }}
	_, path, err := checkDiskFree(context.Background(), fr, &Env{InstallDir: installDir}, deps)
	if err != nil || path != installDir {
		t.Fatalf("muốn chỉ đo %s, được %s (%v)", installDir, path, err)
	}
}

func TestCheckDiskFree_AllProbesFail_ReturnsError(t *testing.T) {
	fr := &fake.Runner{Responses: []fake.Response{{Match: exactArgs("info"), Err: errors.New("x")}}}
	deps := UpdateDeps{DiskFree: func(string) (uint64, error) { return 0, errors.New("statfs lỗi") }}
	if _, _, err := checkDiskFree(context.Background(), fr, &Env{InstallDir: t.TempDir()}, deps); err == nil {
		t.Fatal("đo lỗi cả hai thì phải trả lỗi")
	}
}
