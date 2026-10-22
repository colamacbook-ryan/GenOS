package autoupdate

import "syscall"

// probeInotifyInstances mở thử n inotify instance (đúng thứ systemd làm cho n dòng Path*= của
// .path) rồi đóng ngay. false CHỈ khi nhân báo hết hạn mức/tài nguyên (EMFILE, ENFILE, ENOMEM);
// lỗi khác (seccomp, ENOSYS…) coi như "không biết" ⇒ true để restart quyết định.
func probeInotifyInstances(n int) bool {
	if n < 1 {
		n = 1
	}
	fds := make([]int, 0, n)
	defer func() {
		for _, fd := range fds {
			_ = syscall.Close(fd)
		}
	}()
	for i := 0; i < n; i++ {
		fd, err := syscall.InotifyInit1(syscall.IN_CLOEXEC)
		if err != nil {
			switch err {
			case syscall.EMFILE, syscall.ENFILE, syscall.ENOMEM:
				return false
			}
			return true
		}
		fds = append(fds, fd)
	}
	return true
}
