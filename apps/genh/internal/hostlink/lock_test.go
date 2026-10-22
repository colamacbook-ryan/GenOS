package hostlink

import (
	"bufio"
	"context"
	"errors"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
	"time"
)

// TestMain: khi chạy với GENH_LOCK_HELPER=1 thì tiến trình test này đóng vai
// "một genh khác" — giữ khoá ở GENH_LOCK_DIR, in "LOCKED", chờ stdin đóng rồi thoát.
func TestMain(m *testing.M) {
	if os.Getenv("GENH_LOCK_HELPER") == "1" {
		l, err := AcquireLock(os.Getenv("GENH_LOCK_DIR"))
		if err != nil {
			fmt.Println("ERR", err)
			os.Exit(3)
		}
		fmt.Println("LOCKED")
		_, _ = io.Copy(io.Discard, os.Stdin)
		l.Release()
		os.Exit(0)
	}
	os.Exit(m.Run())
}

func TestAcquireLock_CungTienTrinh_LanHaiBan_ReleaseLayLaiDuoc(t *testing.T) {
	dir := t.TempDir()
	l1, err := AcquireLock(dir)
	if err != nil {
		t.Fatalf("lần 1: %v", err)
	}
	if _, err := os.Stat(filepath.Join(dir, "genh.lock")); err != nil {
		t.Fatalf("khoá phải nằm ở <gốc cài đặt>/genh.lock: %v", err)
	}
	if _, err := os.Stat(filepath.Join(Dir(dir), "genh.lock")); err == nil {
		t.Fatal("khoá KHÔNG được nằm trong run/")
	}
	if runtime.GOOS != "windows" {
		fi, _ := os.Stat(LockPath(dir))
		if fi.Mode().Perm() != 0o600 {
			t.Errorf("tệp khoá phải 0600, được %v", fi.Mode().Perm())
		}
	}
	_, err = AcquireLock(dir)
	if !errors.Is(err, ErrLockBusy) {
		t.Fatalf("lần 2 (mở tệp riêng) phải bận, được %v", err)
	}
	l1.Release()
	l1.Release() // gọi lại vô hại
	l2, err := AcquireLock(dir)
	if err != nil {
		t.Fatalf("sau Release phải lấy lại được: %v", err)
	}
	l2.Release()
	var nilLock *Lock
	nilLock.Release()
}

func TestAcquireLock_BanKemPIDTuNhipSong(t *testing.T) {
	dir := t.TempDir()
	l, err := AcquireLock(dir)
	if err != nil {
		t.Fatal(err)
	}
	defer l.Release()
	stop := StartHeartbeat(dir, "update")
	defer stop()
	_, err = AcquireLock(dir)
	if BusyPID(err) != os.Getpid() {
		t.Fatalf("lỗi bận phải kèm PID chủ khoá %d, được %v", os.Getpid(), err)
	}
	if want := fmt.Sprintf("(PID %d)", os.Getpid()); !strings.Contains(err.Error(), want) {
		t.Errorf("thông điệp thiếu %q: %q", want, err.Error())
	}
}

// Khác tiến trình: test tự exec chính nó (GENH_LOCK_HELPER=1) giữ khoá.
func TestAcquireLock_KhacTienTrinh(t *testing.T) {
	dir := t.TempDir()
	cmd := exec.Command(os.Args[0], "-test.run=^$")
	cmd.Env = append(os.Environ(), "GENH_LOCK_HELPER=1", "GENH_LOCK_DIR="+dir)
	stdin, err := cmd.StdinPipe()
	if err != nil {
		t.Fatal(err)
	}
	stdout, err := cmd.StdoutPipe()
	if err != nil {
		t.Fatal(err)
	}
	if err := cmd.Start(); err != nil {
		t.Fatal(err)
	}
	line, _ := bufio.NewReader(stdout).ReadString('\n')
	if line != "LOCKED\n" {
		_ = stdin.Close()
		_ = cmd.Wait()
		t.Fatalf("tiến trình phụ không giữ được khoá: %q", line)
	}
	if _, err := AcquireLock(dir); !errors.Is(err, ErrLockBusy) {
		t.Fatalf("tiến trình khác đang giữ khoá — phải bận, được %v", err)
	}
	_ = stdin.Close() // tiến trình phụ nhả khoá và thoát
	if err := cmd.Wait(); err != nil {
		t.Fatalf("tiến trình phụ: %v", err)
	}
	l, err := AcquireLock(dir)
	if err != nil {
		t.Fatalf("tiến trình phụ đã thoát — phải lấy được khoá: %v", err)
	}
	l.Release()
}

func TestAcquireLockWait_CtxHuy_TraNhanh(t *testing.T) {
	dir := t.TempDir()
	l, err := AcquireLock(dir)
	if err != nil {
		t.Fatal(err)
	}
	defer l.Release()
	ctx, cancel := context.WithCancel(context.Background())
	go func() { time.Sleep(50 * time.Millisecond); cancel() }()
	start := time.Now()
	_, err = AcquireLockWait(ctx, dir, time.Hour)
	if !errors.Is(err, context.Canceled) {
		t.Fatalf("ctx huỷ phải trả context.Canceled, được %v", err)
	}
	if d := time.Since(start); d > time.Second {
		t.Fatalf("ctx huỷ phải trả nhanh, mất %v", d)
	}
}

func TestAcquireLockWait_ChoKhoaNha(t *testing.T) {
	old := lockRetryEvery
	lockRetryEvery = 20 * time.Millisecond
	defer func() { lockRetryEvery = old }()
	dir := t.TempDir()
	l, err := AcquireLock(dir)
	if err != nil {
		t.Fatal(err)
	}
	go func() { time.Sleep(100 * time.Millisecond); l.Release() }()
	l2, err := AcquireLockWait(context.Background(), dir, 5*time.Second)
	if err != nil {
		t.Fatalf("khoá đã nhả — phải lấy được: %v", err)
	}
	l2.Release()

	// Hết max mà vẫn bận → lỗi bận.
	l3, _ := AcquireLock(dir)
	defer l3.Release()
	if _, err := AcquireLockWait(context.Background(), dir, 60*time.Millisecond); !errors.Is(err, ErrLockBusy) {
		t.Fatalf("hết thời gian chờ phải trả bận, được %v", err)
	}
}

// genh.lock là symlink (ai đó cài sẵn): KHÔNG đi theo — không tạo/đụng tệp đích.
func TestAcquireLock_SymlinkKhongDiTheo(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("symlink kiểu Unix")
	}
	dir := t.TempDir()
	target := filepath.Join(t.TempDir(), "dich")
	if err := os.Symlink(target, LockPath(dir)); err != nil {
		t.Fatal(err)
	}
	if l, err := AcquireLock(dir); err == nil {
		l.Release()
		t.Fatal("genh.lock là symlink — phải từ chối")
	}
	if _, err := os.Stat(target); !errors.Is(err, os.ErrNotExist) {
		t.Fatalf("không được tạo tệp đích của symlink: %v", err)
	}
	// Symlink tới tệp có sẵn: tệp đó không bị khoá/đổi quyền.
	existing := filepath.Join(t.TempDir(), "co-san")
	if err := os.WriteFile(existing, []byte("x"), 0o644); err != nil {
		t.Fatal(err)
	}
	_ = os.Remove(LockPath(dir))
	if err := os.Symlink(existing, LockPath(dir)); err != nil {
		t.Fatal(err)
	}
	if l, err := AcquireLock(dir); err == nil {
		l.Release()
		t.Fatal("genh.lock là symlink tới tệp có sẵn — phải từ chối")
	}
	if fi, _ := os.Stat(existing); fi.Mode().Perm() != 0o644 {
		t.Errorf("tệp đích bị đổi quyền: %v", fi.Mode().Perm())
	}
}
