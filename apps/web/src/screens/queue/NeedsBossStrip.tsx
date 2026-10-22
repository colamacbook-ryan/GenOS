import { Link } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { Icon } from '@gen-harness/ui';
import { BOSS_CHECKS_KEY, BOSS_CHECKS_PATH } from '../../guide/bossChecksModel';
import { FOLLOW_UP_KEY } from '../../guide/guideContent';
import { api } from '../../lib/api';
import { useCan } from '../../lib/permissions';
import { useMe } from '../../lib/queries';
import { AI_SOURCE_PATH } from '../../setup/steps';
import { sortIssues } from '../system/healthModel';
import { useSystemHealth } from '../system/queries';
import { NoModelNotice } from './NoModelBanner';
import { WARN } from './queueModel';

/**
 * v0.1.36 (F-6): dải "Cần Sếp xử lý" — ĐẦU Tổng quan, gom mọi việc Sếp phải tự tay làm vào một chỗ:
 * - "Chưa có model" (bước 4 của việc thiết lập tiếp chưa xong — API `GET /setup/follow-up` chỉ trả cho Owner);
 * - sự cố đang mở của `GET /system/health` (`issues` — kênh rớt, model hết đăng nhập, cập nhật lỗi, sao lưu quá hạn,
 *   Bộ xử lý nền im, ổ đĩa sắp đầy), 'bad' trước 'warn', mỗi dòng một nút đi thẳng tới chỗ sửa.
 *   v0.1.37 (F-73): `host.autostart` (máy có thể không tự chạy lại khi bật lại máy) — lệnh chạy trên máy chủ nằm trong
 *   `body`, nút "Xem cách bật" tới thẻ "Sức khoẻ hệ thống" (hướng dẫn từng bước, lệnh dạng mã chép được).
 * Chỉ vai trò có `system.manage` (Owner): mọi nút ở đây dẫn tới chỗ CHỈ người quản lý hệ thống làm được (Sao lưu ngay,
 * đăng nhập lại kênh/model, thử lại cập nhật). Vai trò chỉ có `system.read` (Auditor) không thấy dải — tình trạng vẫn
 * xem được ở thẻ "Sức khoẻ hệ thống" (Dữ liệu & lưu trữ), không có nút chết.
 * Không có gì ⇒ chỉ một dòng "Không có việc nào cần Sếp xử lý" (v0.1.68, F039 — đích làm sáng của bài học N07 luôn có mặt;
 * dòng "Đã đạt x/N việc bắt buộc" của v0.1.54 là phần riêng, xem `BossProgressLine`). Lỗi tải sức khoẻ không chặn Tổng quan: bỏ qua phần đó (thẻ "Sức khoẻ hệ thống" ở
 * Dữ liệu & lưu trữ báo lỗi kèm "Chi tiết kỹ thuật").
 */
export function NeedsBossStrip() {
  const canManage = useCan('system.manage');
  // Chỉ vai quản lý hệ thống (Owner) — API `GET /setup/follow-up` chỉ trả cho Owner; vai khác không gọi (v0.1.42 F-26, nay ở đây).
  const followUp = useQuery({ queryKey: FOLLOW_UP_KEY, queryFn: ({ signal }) => api.setup.followUp(signal), enabled: canManage });
  const health = useSystemHealth(canManage);
  const step4 = Array.isArray(followUp.data) ? followUp.data.find((s) => s.n === 4) : undefined;
  const noModel = !!step4 && !step4.done;
  const issues = Array.isArray(health.data?.issues) ? sortIssues(health.data.issues) : [];
  // v0.1.68 (F039): phần tử `overview.needs_boss` (đích "Làm thử" của bài học N07) LUÔN có mặt — lúc khoẻ chỉ còn một dòng báo
  // yên tâm. Chỉ nói "không có việc" khi cả hai nguồn đã tải xong và thành công (lỗi tải ≠ khoẻ).
  const calm = !noModel && issues.length === 0 && canManage && followUp.isSuccess && health.isSuccess;
  return (
    <>
      {noModel || issues.length > 0 ? <StripBody noModel={noModel} issues={issues} /> : calm ? <StripCalm /> : null}
      <BossProgressLine />
    </>
  );
}

/** Lúc không có sự cố / chưa chọn model: một dòng gọn, vẫn gắn đích làm sáng của dải. */
function StripCalm() {
  return (
    <section className="needs-boss-calm" aria-label="Cần Sếp xử lý" data-testid="needs-boss-calm" data-gen-target="overview.needs_boss">
      <Icon name="ph ph-check-circle" size={14} color="var(--color-ok, currentColor)" />
      <span>Không có việc nào cần Sếp xử lý</span>
    </section>
  );
}

/**
 * v0.1.54 (Gen hướng dẫn): dòng "Đã đạt x/N việc bắt buộc → Xem" — dẫn tới "Việc Sếp cần làm" (`/guide/viec-sep`). Dùng
 * chính truy vấn `boss-checks` của trang đó (CHỈ Owner — API trả 403 cho vai trò khác nên không gọi); chỉ hiện khi x < N.
 * Không nằm trong dải đỏ "Cần Sếp xử lý": thiếu việc kết nối là việc nên làm dần, chưa phải sự cố.
 */
function BossProgressLine() {
  const isOwner = useMe().data?.role?.code === 'owner';
  const q = useQuery({ queryKey: BOSS_CHECKS_KEY, queryFn: ({ signal }) => api.bossChecks.list(signal), enabled: isOwner });
  const done = q.data?.required_done;
  const total = q.data?.required_total;
  if (!isOwner || typeof done !== 'number' || typeof total !== 'number' || total <= 0 || done >= total) return null;
  return (
    <div className="needs-boss-progress" data-testid="boss-progress">
      <Icon name="ph ph-list-checks" size={14} />
      <span className="needs-boss-progress__text">
        Đã đạt {done}/{total} việc bắt buộc
      </span>
      <Link to={BOSS_CHECKS_PATH} className="gh-btn gh-btn--secondary btn-24">
        Xem
        <Icon name="ph ph-arrow-right" size={11} />
      </Link>
    </div>
  );
}

function StripBody({ noModel, issues }: { noModel: boolean; issues: ReturnType<typeof sortIssues> }) {
  return (
    <section className="needs-boss" aria-label="Cần Sếp xử lý" data-testid="needs-boss" data-gen-target="overview.needs_boss">
      <h2 className="needs-boss__title">
        <Icon name="ph ph-hand-palm" size={14} />
        Cần Sếp xử lý
      </h2>
      {noModel ? <NoModelNotice to={AI_SOURCE_PATH} /> : null}
      {issues.length ? (
        <ul className="needs-boss__list">
          {issues.map((i) => (
            <li className="needs-boss__row" key={i.key} data-severity={i.severity} data-testid="needs-boss-row">
              <Icon
                name={i.severity === 'bad' ? 'ph ph-warning-octagon' : 'ph ph-warning'}
                size={16}
                color={i.severity === 'bad' ? 'var(--color-bad)' : WARN}
              />
              <div className="needs-boss__body">
                <div className="needs-boss__row-title">{String(i.title ?? '')}</div>
                {i.body ? <p className="needs-boss__text">{String(i.body)}</p> : null}
              </div>
              {typeof i.link === 'string' && i.link.startsWith('/') ? (
                <Link to={i.link} className={i.severity === 'bad' ? 'gh-btn gh-btn--primary btn-24' : 'gh-btn gh-btn--secondary btn-24'}>
                  {String(i.action || 'Xem')}
                  <Icon name="ph ph-arrow-right" size={11} />
                </Link>
              ) : null}
            </li>
          ))}
        </ul>
      ) : null}
    </section>
  );
}
