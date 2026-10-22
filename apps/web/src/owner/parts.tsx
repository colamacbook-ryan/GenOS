/**
 * v0.1.55 (G5) — mảnh dùng chung của các màn Mặt tiền: trạng thái tải (Skeleton), rỗng (EmptyState chữ đời thường), lỗi
 * (ErrorState: câu thân thiện + "Chi tiết kỹ thuật" — không bao giờ render object) và dòng danh sách bấm được.
 *
 * v0.1.59 (G2): thêm `FirstRunCard` — thẻ NGÀY ĐẦU dùng chung Tình báo, Thông tin, Quan hệ, Phân tích (khi channels_connected = 0).
 * Các mảnh cũ (ListSkeleton, OwnerError, OwnerEmpty, RowLink, IconTile, Avatar) giữ nguyên để G3/G4/G5 dùng tiếp; dòng danh sách
 * mới nên dùng `ListRow` của @gen-harness/ui (gộp RowLink + Avatar).
 */
import { useId, type ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { Card, EmptyState, Icon, Skeleton, cx } from '@gen-harness/ui';
import { errorDetail } from '../lib/errorText';
import { CardError, InlineError } from '../screens/common';
import { openAskGen } from './AskGenScreen';
import { OWNER_PATHS, asText, avatarText, safeLink } from './ownerModel';

/** Khung chờ cùng hình với danh sách thật: `rows` dòng (ô tròn + hai dòng chữ). */
export function ListSkeleton({ rows = 4, label = 'Đang tải' }: { rows?: number; label?: string }) {
  return (
    <div className="owner-skel" aria-busy="true" aria-label={label} data-testid="owner-loading">
      {Array.from({ length: rows }, (_, i) => (
        <div className="owner-skel__row" key={i}>
          <Skeleton width={34} height={34} radius="50%" />
          <div className="owner-skel__text">
            <Skeleton width={`${55 + ((i * 17) % 35)}%`} height={11} />
            <Skeleton width={`${30 + ((i * 13) % 30)}%`} height={9} />
          </div>
        </div>
      ))}
    </div>
  );
}

/** Lỗi tải: câu thân thiện + "Chi tiết kỹ thuật" (mã HTTP/mã lỗi, chuỗi) + nút Thử lại. */
export function OwnerError({ error, onRetry, retrying }: { error: unknown; onRetry?: () => void; retrying?: boolean }) {
  return (
    <div className="owner-error" data-testid="owner-error">
      <CardError error={error} onRetry={onRetry} retrying={retrying} />
    </div>
  );
}

/**
 * Một PHẦN dữ liệu không tải được trong khi phần còn lại vẫn hiện: câu thân thiện + "Chi tiết kỹ thuật" + nút Thử lại
 * (luật cứng: lỗi hiển thị luôn có chi tiết kỹ thuật). `error` có thể là lỗi truy vấn hoặc `null` (khi máy chủ báo `unavailable`
 * mà không có lỗi), lúc đó `detail` dùng chuỗi mô tả thay thế.
 */
export function PartialLoadNote({
  children,
  error,
  detail,
  onRetry,
  retrying,
  testId = 'owner-partial-error',
}: {
  children: ReactNode;
  error?: unknown;
  detail?: string | null;
  onRetry?: () => void;
  retrying?: boolean;
  testId?: string;
}) {
  return (
    <div className="owner-partial" data-testid={testId}>
      <InlineError detail={detail ?? (error ? errorDetail(error) : null)}>{children}</InlineError>
      {onRetry ? (
        <button type="button" className="kit-btn kit-btn--secondary" onClick={onRetry} disabled={retrying} data-testid={`${testId}-retry`}>
          Thử lại
        </button>
      ) : null}
    </div>
  );
}

export function OwnerEmpty({ icon, title, hint, actions }: { icon: string; title: string; hint?: string; actions?: ReactNode }) {
  return (
    <div className="owner-empty" data-testid="owner-empty">
      <EmptyState icon={icon} title={title} description={hint} actions={actions} />
    </div>
  );
}

/** Một dòng danh sách là một liên kết (cả dòng bấm được). `to` đi qua `safeLink` — máy chủ lạ trả link ngoài thì về Hôm nay. */
export function RowLink({
  to,
  title,
  meta,
  right,
  lead,
  tone,
  testId,
}: {
  to: unknown;
  title: unknown;
  meta?: unknown;
  right?: unknown;
  lead?: ReactNode;
  tone?: 'accent' | 'warn' | 'bad' | 'ok';
  testId?: string;
}) {
  const metaText = asText(meta);
  const rightText = asText(right);
  return (
    <Link to={safeLink(to)} className={cx('owner-row', tone && `owner-row--${tone}`)} data-testid={testId}>
      {lead ?? null}
      <span className="owner-row__body">
        <span className="owner-row__title">{asText(title)}</span>
        {metaText ? <span className="owner-row__meta">{metaText}</span> : null}
      </span>
      {rightText ? <span className="owner-row__right">{rightText}</span> : null}
      <Icon className="owner-row__go" name="ph ph-caret-right" size={14} />
    </Link>
  );
}

export function IconTile({ icon, tone }: { icon: string; tone?: 'accent' | 'warn' | 'bad' | 'ok' }) {
  return (
    <span className={cx('owner-tile', tone && `owner-tile--${tone}`)} aria-hidden>
      <Icon name={icon} size={16} />
    </span>
  );
}

export function Avatar({ name }: { name: unknown }) {
  return (
    <span className="owner-avatar" aria-hidden>
      {avatarText(name)}
    </span>
  );
}

/** Đích nút "Nối Zalo": bước hướng dẫn nối kênh Zalo (GuideStepPage n=5). */
const CONNECT_ZALO_PATH = '/guide/5';

/**
 * Thẻ ngày đầu — `channels_connected = 0`: thay TOÀN BỘ phần số/danh sách của Tình báo, Thông tin, Quan hệ, Phân tích bằng ĐÚNG MỘT
 * thẻ này (không chồng EmptyState). Nút chính "Nối Zalo" → /guide/5; liên kết phụ "Xem Gen làm được gì" mở ngăn Hỏi Gen.
 */
export function FirstRunCard() {
  const titleId = useId();
  return (
    <Card as="section" className="owner-firstrun" data-testid="first-run-card" aria-labelledby={titleId}>
      <div className="owner-firstrun__body">
        <span className="owner-firstrun__icon" aria-hidden>
          <Icon name="ph ph-plugs-connected" size={24} />
        </span>
        <h2 className="owner-firstrun__title" id={titleId}>
          Nối Zalo để Gen bắt đầu đọc tin
        </h2>
        <p className="owner-firstrun__text">Sau khi nối, ở đây Sếp sẽ thấy tin quan trọng, khách cần chăm và số liệu tuần</p>
        <div className="owner-firstrun__actions">
          <Link to={CONNECT_ZALO_PATH} className="kit-btn kit-btn--primary" data-testid="first-run-connect">
            Nối Zalo
          </Link>
          <Link
            to={OWNER_PATHS.gen}
            className="kit-link"
            data-testid="first-run-ask-gen"
            onClick={(e) => {
              e.preventDefault();
              openAskGen();
            }}
          >
            Xem Gen làm được gì
          </Link>
        </div>
      </div>
    </Card>
  );
}
