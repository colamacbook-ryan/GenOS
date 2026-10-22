import { useEffect, useRef } from 'react';
import { useSearchParams } from 'react-router-dom';
import type { AccessInfo } from '@gen-harness/contracts';
import { Button } from '@gen-harness/ui';
import { useCan } from '../../lib/permissions';
import { toast } from '../../lib/toast';
import { CardError, InlineError, Panel, SkeletonLines } from '../common';
import { ACCESS_COMMANDS, COPY_FAILED_TEXT, modeLabel, modeWarning } from './accessModel';
import { useAccess } from './queries';

function copyText(text: string, ok: string) {
  // Console mở qua http (không an toàn) ⇒ không có navigator.clipboard — vẫn phải báo, không im lặng.
  if (!navigator.clipboard?.writeText) {
    toast(COPY_FAILED_TEXT, 'warn');
    return;
  }
  navigator.clipboard.writeText(text).then(
    () => toast(ok, 'ok'),
    () => toast(COPY_FAILED_TEXT, 'warn'),
  );
}

/**
 * v0.1.46 (F-21): thẻ "Truy cập từ xa" trong Cài đặt › Sao lưu & cập nhật (sau "Sức khoẻ hệ thống"): chế độ hiện tại,
 * địa chỉ đăng nhập gửi cho nhân viên, cảnh báo khi cổng mở cho cả mạng. Owner thấy lệnh `genh remote …` chép được —
 * KHÔNG có nút đổi một chạm (Owner đang dùng điện thoại có thể tự cắt truy cập); đổi chế độ bằng lệnh trên máy chủ.
 * Chuông "Cổng đang mở cho cả mạng" dẫn tới đây bằng `?focus=access`.
 */
export function RemoteAccessCard() {
  const canRead = useCan('system.read');
  const q = useAccess();
  const [params] = useSearchParams();
  const focusAccess = params.get('focus') === 'access';
  const ref = useRef<HTMLDivElement>(null);
  const focused = useRef(false);
  useEffect(() => {
    if (!focusAccess || focused.current || !q.data) return;
    focused.current = true;
    const panel = ref.current?.closest('section') ?? ref.current;
    if (!panel) return;
    panel.scrollIntoView?.({ block: 'start' });
    panel.setAttribute('tabindex', '-1');
    (panel as HTMLElement).focus({ preventScroll: true });
  }, [focusAccess, q.data]);
  if (!canRead) return null;
  return (
    <Panel title="Truy cập từ xa" genTarget="system.remote_access" kicker="Cách nhân viên và điện thoại mở Console" label="Truy cập từ xa" bodyClass="health-card">
      <div ref={ref}>
        {q.isPending ? (
          <SkeletonLines rows={3} padding="0" />
        ) : q.isError ? (
          <CardError error={q.error} onRetry={() => void q.refetch()} retrying={q.isFetching} />
        ) : (
          <AccessBody data={q.data} />
        )}
      </div>
    </Panel>
  );
}

function AccessBody({ data }: { data: AccessInfo }) {
  const warning = modeWarning(data);
  const danger = data.mode === 'lan_legacy';
  return (
    <>
      <ul className="health-card__rows">
        <li className="health-card__row" data-testid="access-mode">
          <span className="health-card__label">Chế độ</span>
          <span className="health-card__value">{modeLabel(data.mode)}</span>
        </li>
        <li className="health-card__row" data-testid="access-login-url">
          <span className="health-card__label">Địa chỉ đăng nhập</span>
          <code className="mono">{data.login_url}</code>
          <Button variant="ghost" className="btn-27" icon="ph ph-copy" onClick={() => copyText(data.login_url, 'Đã chép địa chỉ đăng nhập.')} aria-label="Chép địa chỉ đăng nhập">
            Chép
          </Button>
        </li>
      </ul>
      {data.mode === 'unknown' ? (
        <p className="muted-note" role="note" data-testid="access-unknown">
          {data.can_manage ? (
            <>
              Chưa đọc được chế độ hiện tại — chạy <code className="mono">genh remote status</code> trên máy chủ để cập nhật.
            </>
          ) : (
            'Chưa đọc được chế độ hiện tại — nhờ Owner kiểm tra trên máy chủ.'
          )}
        </p>
      ) : null}
      {warning ? (
        danger ? (
          <InlineError>{warning}</InlineError>
        ) : (
          <p className="muted-note" role="note" data-testid="access-warning">
            {warning}
          </p>
        )
      ) : null}
      {data.can_manage ? (
        <div className="health-card__tip" role="note" aria-label="Đổi cách truy cập" data-testid="access-commands">
          <div className="health-card__tip-title">Đổi cách truy cập — chạy trên máy chủ</div>
          <ul className="health-card__tech-list">
            {ACCESS_COMMANDS.map((c) => (
              <li key={c.key} style={{ flexDirection: 'column' }}>
                <span>{c.title}</span>
                <span className="offsite-cmd__row">
                  <code className="mono">{c.cmd}</code>
                  <Button variant="ghost" className="btn-27" icon="ph ph-copy" onClick={() => copyText(c.copy, c.copy === c.cmd ? 'Đã chép lệnh.' : 'Đã chép phần đầu lệnh — gõ tiếp tên miền rồi chạy.')} aria-label={`Chép lệnh ${c.cmd}`}>
                    Chép
                  </Button>
                </span>
                <span className="muted-note">{c.note}</span>
              </li>
            ))}
          </ul>
        </div>
      ) : (
        <p className="muted-note">Nhờ Owner chọn cách truy cập từ xa.</p>
      )}
    </>
  );
}
