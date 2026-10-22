//go:build !linux

package autoupdate

// probeInotifyInstances: inotify chỉ có trên Linux (người gác .path chỉ áp dụng ở đó) ⇒ không chặn gì.
func probeInotifyInstances(int) bool { return true }
