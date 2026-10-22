import { useEffect, useRef, useState, type KeyboardEvent } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import type { NotificationItem, NotificationsPage } from '@gen-harness/contracts';
import { Icon, Skeleton, Tooltip } from '@gen-harness/ui';
import { api } from '../lib/api';
import { errorText } from '../lib/errorText';
import { fmtAgo } from '../lib/format';
import { qk, useAllNotifications, useNotifications } from '../lib/queries';
import { toast } from '../lib/toast';
import { useNow } from '../lib/useNow';
import { badgeText } from './headerModel';
import { HEALTH_KINDS, qkSystem } from '../screens/system/queries';
import { COACH_TODAY_KEY } from '../gen/coachModel';

const KIND_ICON: Record<string, string> = {
  'user.role_changed': 'ph ph-user-switch',
  'user.password_reset': 'ph ph-key',
  'user.reactivated': 'ph ph-lock-open',
  'backup.done': 'ph ph-database',
  'backup.failed': 'ph ph-warning-circle',
  'task.reminder': 'ph ph-alarm',
  // v0.1.71 (P3-11): cảnh báo P1 (phàn nàn nặng, cơ hội nóng chưa ai nhận…) rung chuông của Owner.
  'alert.p1': 'ph ph-warning-octagon',
  'hub.token_expiring': 'ph ph-key',
  'social.read': 'ph ph-facebook-logo',
  'social.paused': 'ph ph-shield-warning',
  // v0.1.38 (F-17): phiên mạng xã hội không mở được trên máy này — cần đăng nhập lại.
  'social.needs_login': 'ph ph-sign-in',
  // v0.1.36 (F-6): sự cố sức khoẻ (khử trùng lặp ở API — mỗi sự cố một chuông tới khi hết).
  'channel.down': 'ph ph-plugs',
  'model.auth_expired': 'ph ph-brain',
  'update.failed': 'ph ph-arrow-counter-clockwise',
  'backup.stale': 'ph ph-clock-countdown',
  'worker.silent': 'ph ph-pulse',
  // v0.1.62 (F051, F064): bộ nhận tin Zalo/WhatsApp im; tin khách nằm trong hàng lỗi.
  'bridge.silent': 'ph ph-plugs',
  'dlq.inbound': 'ph ph-tray',
  'disk.low': 'ph ph-hard-drives',
  // v0.1.37 (F-73): máy chủ chưa tự chạy lại Gen-Harness khi bật máy.
  'host.autostart': 'ph ph-power',
  // v0.1.53 (F-99): lịch tự cập nhật đêm không chạy.
  'host.nightly': 'ph ph-calendar-x',
  // v0.1.40 (F-12, F-2): bản sao ngoài máy quá hạn / lỗi; việc nền chạy quá giờ.
  'offsite.stale': 'ph ph-hard-drives',
  'offsite.failed': 'ph ph-warning-circle',
  'job.timeout': 'ph ph-hourglass-high',
  // v0.1.41 (F-8, F-86): Bản tin Gen sáng/chiều; vượt trần chi phí AI; việc nền không có nguồn AI dùng được.
  'gen.briefing': 'ph ph-newspaper',
  'ai.budget_exceeded': 'ph ph-currency-circle-dollar',
  'ai.background_no_source': 'ph ph-brain',
  // v0.1.49 (F-83): Gen-hub không trả lời hơn 15 phút.
  'hub.unreachable': 'ph ph-plugs',
  // v0.1.50 (F-87): Gen đề xuất ghi Phiên của bản phát hành vào Kho dữ liệu (link `/overview?gen=<hội thoại>`).
  'gen.kho_proposal': 'ph ph-database',
  // v0.1.54: Gen hướng dẫn nhắc việc / bài học (link `/overview?gen=coach` khi Gen bật, `/guide/viec-sep` khi Gen tắt).
  'gen.coach': 'ph ph-chalkboard-teacher',
};

/**
 * v0.1.36 (F-6): `notification.new` (lib/realtime.ts chèn thẳng vào bộ đệm chuông) thuộc kind sự cố sức khoẻ ⇒ làm mới
 * `['system','health']` để dải "Cần Sếp xử lý" / thẻ "Sức khoẻ hệ thống" đổi ngay. Lần tải đầu chỉ ghi nhận, không làm mới.
 */
function useHealthRefreshOnNotify(items: NotificationItem[] | undefined) {
  const qc = useQueryClient();
  const seen = useRef<Set<string> | null>(null);
  useEffect(() => {
    if (!items) return;
    if (!seen.current) {
      seen.current = new Set(items.map((n) => n.id));
      return;
    }
    const known = seen.current;
    const fresh = items.filter((n) => !known.has(n.id));
    for (const n of fresh) known.add(n.id);
    if (fresh.some((n) => HEALTH_KINDS.has(n.kind))) void qc.invalidateQueries({ queryKey: qkSystem.health });
    // v0.1.54: chuông `gen.coach` (Gen hướng dẫn có việc / bài học mới) ⇒ làm mới ngay để chấm đỏ ở nút Gen bật lên.
    if (fresh.some((n) => n.kind === 'gen.coach')) void qc.invalidateQueries({ queryKey: COACH_TODAY_KEY });
  }, [items, qc]);
}

/**
 * v0.1.23 (Đợt B6) — chuông thông báo ở header: số chưa đọc, danh sách 20 thông báo gần nhất của CHÍNH người đang
 * đăng nhập, cập nhật ngay qua WebSocket (`notification.new`). Bấm một thông báo → đánh dấu đã đọc + mở trang liên quan.
 */
export function NotificationBell() {
  const [open, setOpen] = useState(false);
  const q = useNotifications();
  const unread = q.data?.unread ?? 0;
  useHealthRefreshOnNotify(q.data?.items);
  const wrap = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (!wrap.current?.contains(e.target as Node)) setOpen(false);
    };
    // Esc đóng cả khi tiêu điểm đã rời bảng (vd nút vừa bị vô hiệu sau "Đánh dấu đã đọc hết").
    const onKey = (e: globalThis.KeyboardEvent) => {
      if (e.key !== 'Escape' || e.defaultPrevented) return;
      setOpen(false);
      trigger.current?.focus();
    };
    document.addEventListener('mousedown', onDown);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDown);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);

  const label = unread ? `Thông báo — ${unread} chưa đọc` : 'Thông báo';
  return (
    <div className="hd-bell-wrap" ref={wrap}>
      <Tooltip content={label} disabled={open}>
        <button
          ref={trigger}
          type="button"
          className="gh-btn gh-btn--secondary gh-btn--icon hd-bell"
          aria-label={label}
          aria-haspopup="dialog"
          aria-expanded={open}
          onClick={() => setOpen((o) => !o)}
        >
          <Icon name={unread ? 'ph ph-bell-ringing' : 'ph ph-bell'} size={15} />
          {unread ? (
            <span className="hd-bell__badge" aria-hidden>
              {badgeText(unread)}
            </span>
          ) : null}
        </button>
      </Tooltip>
      {open ? (
        <NotificationPanel
          onClose={(refocus) => {
            setOpen(false);
            if (refocus) trigger.current?.focus();
          }}
        />
      ) : null}
    </div>
  );
}

function NotificationPanel({ onClose }: { onClose: (refocus: boolean) => void }) {
  const q = useNotifications();
  const qc = useQueryClient();
  const navigate = useNavigate();
  const now = useNow(30_000);
  const panel = useRef<HTMLDivElement>(null);
  const [busy, setBusy] = useState(false);
  // v0.1.69 (F098): "Xem thêm" — danh sách đầu chỉ 20 mục, số chưa đọc thì đếm hết.
  const [expanded, setExpanded] = useState(false);
  const all = useAllNotifications(expanded);

  useEffect(() => {
    panel.current?.focus();
  }, []);

  const markRead = async (ids?: string[]) => {
    const prev = qc.getQueryData<NotificationsPage>(qk.notifications);
    if (prev) {
      const hit = (n: NotificationItem) => !ids || ids.includes(n.id);
      const items = prev.items.map((n) => (hit(n) ? { ...n, read: true } : n));
      const cleared = prev.items.filter((n) => hit(n) && !n.read).length;
      qc.setQueryData<NotificationsPage>(qk.notifications, { ...prev, items, unread: Math.max(0, prev.unread - (ids ? cleared : prev.unread)) });
    }
    try {
      const r = await api.notifications.markRead(ids);
      qc.setQueryData<NotificationsPage>(qk.notifications, (old) => (old ? { ...old, unread: r.unread } : old));
      if (expanded) void qc.invalidateQueries({ queryKey: [...qk.notifications, 'all'] });
    } catch (e) {
      if (prev) qc.setQueryData(qk.notifications, prev);
      toast(errorText(e), 'bad');
    }
  };

  const openItem = (n: NotificationItem) => {
    if (!n.read) void markRead([n.id]);
    if (n.link) {
      onClose(false);
      navigate(n.link);
    }
  };

  const onKeyDown = (e: KeyboardEvent) => {
    if (e.key === 'Escape') {
      e.preventDefault();
      onClose(true);
      return;
    }
    if (e.key !== 'ArrowDown' && e.key !== 'ArrowUp') return;
    const items = Array.from(panel.current?.querySelectorAll<HTMLElement>('.nt-item') ?? []);
    if (!items.length) return;
    e.preventDefault();
    const i = items.indexOf(document.activeElement as HTMLElement);
    const next = e.key === 'ArrowDown' ? (i + 1) % items.length : (i - 1 + items.length) % items.length;
    items[i < 0 ? 0 : next]?.focus();
  };

  const unread = q.data?.unread ?? 0;
  // Đã mở "Xem thêm": gộp các trang đã tải (offset có thể lệch khi có thông báo mới ⇒ bỏ trùng theo id).
  const loaded = expanded && all.data ? [...new Map(all.data.pages.flatMap((p) => p.items).map((n) => [n.id, n])).values()] : null;
  const shown = loaded ?? q.data?.items ?? [];
  const total = q.data?.total ?? shown.length;
  const hasMore = expanded ? all.hasNextPage && !all.isFetchingNextPage : total > shown.length;
  return (
    <div className="nt-panel" role="dialog" aria-label="Thông báo" tabIndex={-1} ref={panel} onKeyDown={onKeyDown}>
      <div className="nt-head">
        <span className="nt-head__title">Thông báo</span>
        {unread ? <span className="nt-head__count">{unread} chưa đọc</span> : null}
        <button
          type="button"
          className="nt-head__all"
          disabled={!unread || busy}
          onClick={async () => {
            setBusy(true);
            await markRead();
            setBusy(false);
          }}
        >
          Đánh dấu đã đọc hết
        </button>
      </div>
      <div className="nt-list">
        {q.isPending ? (
          <div className="nt-state" aria-busy="true" aria-label="Đang tải thông báo">
            <Skeleton width="70%" height={11} />
            <Skeleton width="90%" height={9} style={{ marginTop: 8 }} />
          </div>
        ) : q.isError ? (
          <div className="nt-state">
            Không tải được thông báo.{' '}
            <button type="button" className="nt-link" onClick={() => void q.refetch()}>
              Thử lại
            </button>
          </div>
        ) : shown.length === 0 ? (
          <div className="nt-state">
            <Icon name="ph ph-bell-slash" size={18} />
            <span>Chưa có thông báo nào.</span>
          </div>
        ) : (
          <ul className="nt-items">
            {shown.map((n) => (
              <li key={n.id}>
                <button
                  type="button"
                  className="nt-item"
                  data-unread={!n.read || undefined}
                  data-resolved={n.resolved || undefined}
                  onClick={() => openItem(n)}
                >
                  <span className="nt-item__icon" aria-hidden>
                    <Icon name={KIND_ICON[n.kind] ?? 'ph ph-bell'} size={14} />
                  </span>
                  <span className="nt-item__text">
                    <span className="nt-item__title">
                      {n.title}
                      {!n.read ? <span className="visually-hidden"> (chưa đọc)</span> : null}
                      {n.resolved ? <span className="nt-item__resolved">Đã xử lý</span> : null}
                    </span>
                    {n.body ? <span className="nt-item__body">{n.body}</span> : null}
                    <span className="nt-item__time">{fmtAgo(n.created_at, now)}</span>
                  </span>
                  {!n.read ? <span className="nt-item__dot" aria-hidden /> : null}
                </button>
              </li>
            ))}
          </ul>
        )}
        {hasMore ? (
          <button type="button" className="nt-more" onClick={() => (expanded ? void all.fetchNextPage() : setExpanded(true))}>
            Xem thêm ({Math.max(0, total - shown.length)})
          </button>
        ) : null}
        {expanded && (all.isPending || all.isFetchingNextPage) ? (
          <div className="nt-state" aria-busy="true" aria-label="Đang tải thêm thông báo">
            <Skeleton width="80%" height={9} />
          </div>
        ) : null}
        {expanded && all.isError ? (
          <div className="nt-state">
            Không tải được thêm thông báo.{' '}
            <button type="button" className="nt-link" onClick={() => void (all.data ? all.fetchNextPage() : all.refetch())}>
              Thử lại
            </button>
          </div>
        ) : null}
      </div>
    </div>
  );
}
