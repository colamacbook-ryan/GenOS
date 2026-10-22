//go:build !windows

package hostlink

import (
	"context"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"syscall"
)

// ensureRunDirs tạo run/ và run/request nếu CHƯA có. Linux (v0.1.45): tạo 0770
// (không cho "người khác") và KHÔNG chmod lại thư mục đã có — EnsureRunPerms
// (chạy sau `docker compose up`) mới đặt nhóm 10001 + 2770; mở lại 0777 mỗi lần
// ghi trạng thái sẽ xoá công siết quyền. macOS: giữ như cũ (0777 — Docker
// Desktop tự ánh xạ quyền thư mục chia sẻ, không có nhóm 10001 trên máy).
func ensureRunDirs(dirs []string) error {
	if runtime.GOOS != "linux" {
		for _, d := range dirs {
			if err := os.MkdirAll(d, 0o777); err != nil {
				return err
			}
			if err := os.Chmod(d, 0o777); err != nil {
				return err
			}
		}
		return nil
	}
	for _, d := range dirs {
		if _, err := os.Lstat(d); err == nil {
			continue
		} else if !errors.Is(err, os.ErrNotExist) {
			return err
		}
		if err := os.MkdirAll(d, 0o770); err != nil {
			return err
		}
		// MkdirAll chịu umask — đặt đúng 0770 cho thư mục vừa tạo.
		if err := os.Chmod(d, 0o770); err != nil {
			return err
		}
	}
	return nil
}

func fileUID(fi os.FileInfo) (uint32, bool) {
	st, ok := fi.Sys().(*syscall.Stat_t)
	if !ok {
		return 0, false
	}
	return st.Uid, true
}

// ownerAllowed: uid chủ tệp yêu cầu có được tin không — người chạy genh hoặc uid
// tiến trình api. Biến gói để test thay (giả "uid lạ") mà không cần root.
var ownerAllowed = func(installDir string, uid uint32) bool {
	return uid == uint32(os.Getuid()) || uid == APIUID(installDir)
}

func requestOwnerOK(installDir string, fi os.FileInfo) bool {
	uid, ok := fileUID(fi)
	return ok && ownerAllowed(installDir, uid)
}

// ─── Siết quyền hộp thư run/ (v0.1.45) ─────────────────────────────────────

// runPermScript chạy trong container phụ (ảnh api, --user 0:0, --network none,
// run/ gắn ở /r): chỉ siết khi ảnh có nhóm gh gid 10001 (ảnh ≥ v0.1.45 — quay về
// ảnh cũ thì api chạy gid khác, siết sẽ khoá api ngoài hộp thư ⇒ báo
// runPermOldImage để genh mở lại 0777). -h: không đi theo symlink api cài sẵn.
// .uid-probe: tệp do uid 10001 TRONG container sở hữu — genh Lstat để biết uid
// thật trên máy chủ (Docker rootless ánh xạ sang subuid).
const runPermScript = `g=$(id -g gh 2>/dev/null || true); ` +
	`if [ "$g" != "10001" ]; then echo ` + runPermOldImage + `; exit 0; fi; ` +
	`chgrp 10001 /r /r/request && chmod 2770 /r /r/request && ` +
	`{ chgrp -h 10001 /r/* /r/request/* 2>/dev/null; ` +
	`rm -f /r/.uid-probe; touch /r/.uid-probe && chown -h 10001:10001 /r/.uid-probe; ` +
	`echo ` + runPermOK + `; }; true`

const (
	runPermOldImage = "GH_RUNPERM_OLD_IMAGE"
	runPermOK       = "GH_RUNPERM_OK"
	uidProbeFile    = ".uid-probe"
)

// errRunPermOldImage: ảnh api đang chạy chưa có nhóm gid 10001 (bản cũ).
var errRunPermOldImage = errors.New("ảnh api đang chạy chưa có nhóm gid 10001 (bản cũ) — giữ run/ mở")

// RunPermCommand là lệnh `docker` chạy container phụ siết quyền run/ (xuất cho test).
func RunPermCommand(runDir, image string) []string {
	return []string{
		"run", "--rm", "--network", "none", "--user", "0:0",
		"-v", runDir + ":/r", "--entrypoint", "/bin/sh", image, "-c", runPermScript,
	}
}

// EnsureRunPerms siết quyền hộp thư run/ (+ run/request) về 2770 nhóm 10001 —
// chỉ người chạy genh (chủ thư mục) và tiến trình api (gid 10001 trong ảnh) ghi
// được. GỌI SAU `docker compose up` (ảnh mới đã có gid 10001).
//
//   - không phải Linux (s.GOOS): RunModeNA, không đổi gì.
//   - genh chạy bằng root: có docker + ảnh api → container phụ như dưới (kiểm ảnh cũ);
//     không có docker/ảnh → os.Chown(nhóm 10001) + chmod 02770 trực tiếp.
//   - còn lại: một container phụ bằng ảnh api (RunPermCommand) — Docker rootless
//     tự đúng vì container phụ chạy cùng user namespace; dò uid thật của api.
//
// Thất bại → chmod 0777 như cũ (không phá cập nhật), trả RunModeOpen kèm lỗi để
// bên gọi in cảnh báo. Ngoại lệ: docker lỗi nhưng thư mục ĐÃ 2770 (lần trước
// siết thành công) → giữ nguyên, RunModeRestricted.
func EnsureRunPerms(ctx context.Context, s RunPermSpec) (string, error) {
	goos := s.GOOS
	if goos == "" {
		goos = runtime.GOOS
	}
	if goos != "linux" {
		return RunModeNA, nil
	}
	getuid := s.Getuid
	if getuid == nil {
		getuid = os.Getuid
	}
	chown := s.Chown
	if chown == nil {
		chown = os.Chown
	}
	dirs := []string{Dir(s.InstallDir), RequestDirPath(s.InstallDir)}
	if err := EnsureDir(s.InstallDir); err != nil {
		return openRunDirs(dirs), err
	}

	if getuid() == 0 {
		// Sửa review v0.1.45: root CŨNG phải kiểm ảnh api đang chạy có gid 10001 không — quay về ảnh cũ
		// (rollbackAndWrap sau cập nhật lỗi 0.1.44→0.1.45) mà vẫn khoá 2770 nhóm 10001 thì api cũ hết đọc/ghi
		// run/ (Console cập nhật/khôi phục/chẩn đoán chết). Có docker + xác định được ảnh → đi đường container phụ
		// (báo GH_RUNPERM_OLD_IMAGE ⇒ mở 0777); không có docker/ảnh hoặc container phụ lỗi → siết trực tiếp.
		if s.Runner != nil {
			if image, err := resolveAPIImage(ctx, s); err == nil {
				mode, herr := runPermViaHelper(ctx, s, dirs, image)
				if mode == RunModeRestricted || errors.Is(herr, errRunPermOldImage) {
					return mode, herr
				}
				// Container phụ lỗi (không phải "ảnh cũ") → root siết trực tiếp như trước.
			}
		}
		for _, d := range dirs {
			if err := chown(d, -1, APIGID); err != nil {
				return openRunDirs(dirs), fmt.Errorf("đổi nhóm %s: %w", d, err)
			}
			if err := os.Chmod(d, os.ModeSetgid|0o770); err != nil {
				return openRunDirs(dirs), fmt.Errorf("chmod 2770 %s: %w", d, err)
			}
		}
		return RunModeRestricted, nil
	}

	if s.Runner == nil {
		return keepOrOpen(dirs, errors.New("không có trình chạy docker"))
	}
	image, err := resolveAPIImage(ctx, s)
	if err != nil {
		return keepOrOpen(dirs, err)
	}
	return runPermViaHelper(ctx, s, dirs, image)
}

// runPermViaHelper: container phụ bằng ảnh api siết run/ (RunPermCommand) — ảnh cũ (không có gid 10001) ⇒ mở 0777.
func runPermViaHelper(ctx context.Context, s RunPermSpec, dirs []string, image string) (string, error) {
	out, err := s.Runner.Output(ctx, dockerCmd(RunPermCommand(Dir(s.InstallDir), image), s))
	if err != nil {
		return keepOrOpen(dirs, fmt.Errorf("container phụ siết quyền run/: %w", err))
	}
	if strings.Contains(string(out), runPermOldImage) {
		return openRunDirs(dirs), errRunPermOldImage
	}
	if !strings.Contains(string(out), runPermOK) {
		return keepOrOpen(dirs, errors.New("container phụ siết quyền run/ không báo xong"))
	}
	probeAPIUID(s.InstallDir)
	if !dirsRestricted(dirs) {
		return openRunDirs(dirs), errors.New("run/ chưa về 2770 sau khi siết")
	}
	return RunModeRestricted, nil
}

// probeAPIUID đọc uid thật của tệp .uid-probe (do uid 10001 trong container
// sở hữu) rồi lưu config/hostlink.json — Docker rootless ánh xạ 10001 sang
// subuid. Tệp lạ (symlink, nhiều liên kết) → bỏ qua, giữ giá trị cũ.
func probeAPIUID(installDir string) {
	path := filepath.Join(Dir(installDir), uidProbeFile)
	defer func() { _ = os.Remove(path) }()
	fi, err := os.Lstat(path)
	if err != nil || checkStateFileInfo(fi, false) != nil {
		return
	}
	uid, ok := fileUID(fi)
	if !ok || uid == 0 || uid == uint32(os.Getuid()) {
		return
	}
	_ = WriteAPIUID(installDir, uid)
}

// dirsRestricted: mọi thư mục đều là thư mục thật, đã được siết từ trước
// (chmod 2770: setgid + nhóm rwx, không bit "khác"). setgid là dấu của
// EnsureRunPerms — EnsureDir chỉ tạo 0770 với nhóm của người chạy genh (api
// chưa ghi được), không được coi là đã siết.
func dirsRestricted(dirs []string) bool {
	for _, d := range dirs {
		fi, err := os.Lstat(d)
		if err != nil || !fi.IsDir() || fi.Mode()&os.ModeSymlink != 0 {
			return false
		}
		if perm := fi.Mode().Perm(); perm&0o007 != 0 || perm&0o070 != 0o070 {
			return false
		}
		// macOS (chỉ gặp trong test ép GOOS=linux) có thể bỏ bit setgid của thư mục.
		if runtime.GOOS == "linux" && fi.Mode()&os.ModeSetgid == 0 {
			return false
		}
	}
	return true
}

// keepOrOpen: lỗi khi siết — thư mục đã 2770 từ lần trước thì giữ, không thì mở 0777.
func keepOrOpen(dirs []string, err error) (string, error) {
	if dirsRestricted(dirs) {
		return RunModeRestricted, nil
	}
	return openRunDirs(dirs), err
}

// openRunDirs mở lại 0777 như trước v0.1.45 (api chạy uid/gid khác vẫn ghi được).
func openRunDirs(dirs []string) string {
	for _, d := range dirs {
		_ = os.Chmod(d, 0o777)
	}
	return RunModeOpen
}
