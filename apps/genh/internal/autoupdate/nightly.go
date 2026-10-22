package autoupdate

import (
	"context"
	"fmt"
	"os"
	"path/filepath"
	"strings"
)

// nightlyServiceRunning: gen-harness-update.service đang chạy (activating/active/reloading)?
// Khi đó timer ở trạng thái "running" và NextElapse rỗng là bình thường (systemd tính lại
// lần kế tiếp lúc service xong).
func nightlyServiceRunning(ctx context.Context, deps Deps) bool {
	out, _ := deps.runner().Output(ctx, "systemctl", []string{"--user", "is-active", TaskName + ".service"})
	if f := strings.Fields(string(out)); len(f) > 0 {
		switch f[0] {
		case "activating", "active", "reloading":
			return true
		}
	}
	return false
}

// RequestWatcherState hỏi trình nhận yêu cầu (gen-harness-update-request.path)
// đang chạy không: active | failed (vd start-limit-hit — `genh update` chữa) |
// inactive | unknown (không phải Linux systemd --user, hoặc không hỏi được).
// Chỉ đọc, không đổi gì.
func RequestWatcherState(ctx context.Context, deps Deps) string {
	if deps.goos() != "linux" {
		return "unknown"
	}
	out, _ := deps.runner().Output(ctx, "systemctl", []string{"--user", "is-active", RequestTaskName + ".path"})
	if f := strings.Fields(string(out)); len(f) > 0 {
		switch f[0] {
		case "active", "failed", "inactive":
			return f[0]
		}
	}
	return "unknown"
}

// ─── Lịch đêm tự lành (v0.1.53, F-93) ───────────────────────────────────────
//
// Bằng chứng máy Sếp: logs/auto-update.log dừng ngay sau đêm 03/10 (nâng
// v0.1.37 → v0.1.44), `genh auto-update status` in TẮT suốt 10 ngày mà không ai
// biết, nút Console "Máy chủ chưa nhận yêu cầu". Dù nguyên nhân gốc là gì (xem
// owner.go — gỡ/cài bản phụ xoá/ghi đè lịch dùng chung), lịch đêm KHÔNG ĐƯỢC im
// lặng mất: mỗi lần genh chạy (update, lịch đêm, trực canh…) kiểm lại và BẬT LẠI
// nếu unit vắng/tắt/không chạy — trừ khi Sếp đã chủ động tắt
// (ops.AutoUpdateOptedOut — cmd kiểm trước khi gọi).

// HealedMessage là dòng log khi EnsureNightly đã bật lại lịch đêm.
const HealedMessage = "genh: lịch tự cập nhật đêm đã bị tắt/mất — đã bật lại (~03:00). Muốn tắt hẳn: genh auto-update disable"

// RearmedMessage là dòng log khi timer đang active mà không có lần chạy kế tiếp
// (mất lịch sau daemon-reload) và đã được khởi động lại.
const RearmedMessage = "genh: lịch tự cập nhật đêm đang chạy nhưng chưa có lần kế tiếp — đã khởi động lại timer (tự lành)."

// nightlyHealthy: lịch đêm đã cài, bật VÀ đang chạy.
func nightlyHealthy(st Status) bool {
	switch st.Mechanism {
	case ScheduleSystemd:
		return st.UnitPresent && st.UnitFileState == "enabled" && st.Active == "active"
	case "":
		return false
	default:
		return st.UnitPresent && st.Enabled
	}
}

// EnsureNightly kiểm lịch đêm và TỰ LÀNH. Khoẻ (unit có + enabled + active, hoặc
// dòng cron/LaunchAgent/Task còn) ⇒ KHÔNG gọi lệnh ghi nào (chỉ đọc trạng thái).
// Unit vắng HOẶC UnitFileState≠enabled HOẶC ActiveState≠active ⇒ Enable (ghi lại
// unit theo deps.Nightly + enable --now) ⇒ healed=true, msg=HealedMessage.
// Timer active mà không có lần kế tiếp (H-b: daemon-reload/enable từ bên trong
// gen-harness-update.service từng làm mất lịch?) ⇒ `systemctl --user restart
// gen-harness-update.timer`, msg=RearmedMessage. Idempotent: gọi lần 2 khi đã
// khoẻ không ghi gì. Lịch thuộc bản cài khác còn sống ⇒ không đụng: khoẻ thì
// (false, "", nil), hỏng thì lỗi *OwnedByOtherError (caller cảnh báo một dòng).
//
// Caller (cmd/genh) KHÔNG gọi hàm này khi Sếp đã chủ động tắt (ops.AutoUpdateOptedOut).
func EnsureNightly(ctx context.Context, deps Deps) (healed bool, msg string, err error) {
	if deps.GenhPath == "" {
		return false, "", fmt.Errorf("thiếu đường dẫn binary genh")
	}
	st, err := GetStatus(ctx, deps)
	if err != nil {
		return false, "", err
	}
	if nightlyHealthy(st) {
		if st.OwnedByOther {
			return false, "", nil
		}
		// Timer đang "running" (service lịch đêm đang chạy — kể cả chính genh này) thì systemd
		// cố ý để lần kế tiếp rỗng cho tới khi service xong: không phải mất lịch.
		if st.Mechanism == ScheduleSystemd && st.NextRun.IsZero() && !nightlyServiceRunning(ctx, deps) {
			if _, err := deps.runner().Output(ctx, "systemctl", []string{"--user", "restart", TaskName + ".timer"}); err != nil {
				return false, "", fmt.Errorf("systemctl --user restart %s.timer: %w", TaskName, err)
			}
			return true, RearmedMessage, nil
		}
		return false, "", nil
	}
	if st.OwnedByOther {
		return false, "", &OwnedByOtherError{Other: st.Owner, Self: deps.guardDir()}
	}
	if deps.LogFile != "" {
		_ = os.MkdirAll(filepath.Dir(deps.LogFile), 0o755)
	}
	res, err := Enable(ctx, deps)
	if err != nil {
		return false, "", err
	}
	msg = HealedMessage
	if res.Warning != "" {
		msg += "\n" + res.Warning
	}
	return true, msg, nil
}
