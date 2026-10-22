import { IconButton } from '@gen-harness/ui';
import { useMe } from '../lib/queries';
import { showCoachDot } from './coachModel';
import { useCoachAudience, useCoachToday } from './coachQueries';
import { useGenStore } from './genStore';

/**
 * Nút bật/tắt khung Gen ở Header — chỉ hiện khi Gen bật cho người này (`Me.features.gen`).
 * v0.1.54 (Gen hướng dẫn): chấm đỏ khi `GET /gen/coach/today` báo `unseen` (có việc / bài học mới Sếp chưa thấy). Chấm
 * KHÔNG tự mở khung. Tải khi mở app, hỏi lại mỗi 30 phút; chuông `gen.coach` làm mới ngay. Không phải Owner ⇒ không gọi API.
 */
export function GenToggle() {
  const me = useMe();
  const id = me.data?.id;
  const open = useGenStore((s) => (id ? !!s.openByUser[id] : false));
  const setOpen = useGenStore((s) => s.setOpen);
  const coachOn = useCoachAudience();
  const dot = showCoachDot(useCoachToday(coachOn).data);
  if (!id || !me.data?.features?.gen) return null;
  return (
    <span className="hd-gen-wrap">
      <IconButton
        icon="ph ph-sparkle"
        label={open ? 'Đóng Gen' : dot ? 'Hỏi Gen — trợ lý quản trị (có việc mới)' : 'Hỏi Gen — trợ lý quản trị'}
        className="hd-gen"
        aria-pressed={open}
        onClick={() => setOpen(id, !open)}
      />
      {dot ? <span className="hd-gen__dot" aria-hidden data-testid="gen-coach-dot" /> : null}
    </span>
  );
}
