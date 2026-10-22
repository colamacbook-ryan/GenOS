import { Link } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { Icon } from '@gen-harness/ui';
import { SETUP_FOLLOWUP_ITEM, snoozeActive } from '../../gen/coachModel';
import { useCoachItemAction, useCoachPrefs } from '../../gen/coachQueries';
import { FOLLOW_UP_KEY, GUIDE_BY_N, guideOrdinal } from '../../guide/guideContent';
import { api } from '../../lib/api';
import { errorDetail, errorText } from '../../lib/errorText';
import { useMe } from '../../lib/queries';
import { InlineError, Panel } from '../common';


/**
 * "Việc thiết lập tiếp": (v0.1.68: TẤT CẢ đều không bắt buộc — số việc BẮT BUỘC chỉ đếm ở "Đã đạt x/N" của Việc Sếp cần làm)
 * các việc tuỳ chọn 5–11 (+ 13 Facebook, 14 Gen-hub từ v0.1.39) chưa xong. Mỗi mục mở thẳng
 * form/màn làm việc đó (`/guide/:n` hoặc `doTo`), đầu thẻ dẫn tới trang Hướng dẫn thiết lập. Xong hay chưa do API suy từ dữ liệu thật (`GET /setup/follow-up`)
 * — làm xong ở form hướng dẫn hay ở màn Console thì mục cũng tự biến mất, không cần bấm tay.
 *
 * v0.1.54 (Gen hướng dẫn): bỏ nút "Ẩn" lưu trên trình duyệt; thay bằng "Để sau 7 ngày" — lưu Ở MÁY CHỦ
 * (`POST /gen/coach/items/card:setup_followup` snooze 7 ngày), nên ẩn đúng trên mọi máy của Sếp. Thẻ ẩn khi
 * `GET /gen/coach/prefs` báo `followup_snoozed_until` còn ở tương lai; hết hạn là hiện lại.
 * v0.1.70 (F031/F138): hoãn thẻ cũng ẩn mọi việc P3 trên thẻ "Hôm nay của Sếp" (máy chủ), và các việc thiết lập 5–10 của Gen
 * gộp thành MỘT dòng trỏ vào thẻ này (`data-gen-target="overview.setup_followup"`) — một cách đếm, không đứng riêng.
 */
export function SetupFollowUp() {
  const me = useMe();
  const isOwner = me.data?.role?.code === 'owner';
  // Chỉ Owner (API /setup/* trả 403 cho vai trò khác) — không gọi thừa.
  const q = useQuery({ queryKey: FOLLOW_UP_KEY, queryFn: ({ signal }) => api.setup.followUp(signal), enabled: isOwner });
  const prefs = useCoachPrefs(isOwner);
  const snooze = useCoachItemAction();
  // Bước 4 (chưa có model) có dải cảnh báo riêng ở đầu Tổng quan — không lặp ở đây.
  const items = (q.data ?? []).filter((s) => !s.done && s.n !== 4 && GUIDE_BY_N[s.n]);
  // Hoãn thành công thì ẩn ngay (không chờ tải lại cài đặt); lỗi tải cài đặt ⇒ coi như chưa hoãn (thẻ vẫn hiện).
  const hidden = snooze.isSuccess || snoozeActive(prefs.data?.followup_snoozed_until);
  if (!isOwner || items.length === 0 || hidden) return null;

  return (
    <Panel
      title="Việc thiết lập tiếp"
      kicker={`${items.length} việc tuỳ chọn — không bắt buộc, làm khi sẵn sàng, xong sẽ tự biến mất`}
      label="Việc thiết lập tiếp"
      genTarget="overview.setup_followup"
      bodyClass="ov-followup"
      aside={
        <span className="ov-followup__aside">
          <Link to="/guide" className="gh-btn gh-btn--primary btn-24">
            <Icon name="ph ph-list-checks" size={12} />
            Hướng dẫn thiết lập
          </Link>
          <button
            type="button"
            className="gh-btn gh-btn--ghost btn-24"
            title="Ẩn thẻ này 7 ngày trên mọi máy của Sếp — vẫn mở được Hướng dẫn thiết lập ở menu tài khoản hoặc thanh bên"
            disabled={snooze.isPending}
            onClick={() => {
              snooze.reset();
              snooze.mutate({ itemKey: SETUP_FOLLOWUP_ITEM, body: { action: 'snooze', days: 7 } });
            }}
          >
            Để sau 7 ngày
          </button>
        </span>
      }
    >
      {snooze.isError ? <InlineError detail={errorDetail(snooze.error)}>{errorText(snooze.error)}</InlineError> : null}
      <ul className="ov-followup__list">
        {items.map((s) => (
          <li key={s.n} className="ov-followup__item">
            <span className="ov-followup__num mono">{guideOrdinal(s.n)}</span>
            <div className="ov-followup__body">
              <div className="ov-followup__title">{GUIDE_BY_N[s.n].title}</div>
              <div className="ov-followup__hint">{GUIDE_BY_N[s.n].why}</div>
            </div>
            <Link to={GUIDE_BY_N[s.n].doTo ?? `/guide/${s.n}`} className="gh-btn gh-btn--secondary btn-24">
              Làm ngay
              <Icon name="ph ph-arrow-right" size={12} />
            </Link>
          </li>
        ))}
      </ul>
    </Panel>
  );
}
