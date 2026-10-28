import { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { Icon, Pill } from '@gen-harness/ui';
import { useCan } from '../lib/permissions';
import { sortIssues } from '../screens/system/healthModel';
import { useSystemHealth } from '../screens/system/queries';
import { healthChipView, healthIssueLink } from './headerModel';

const TONE = { ok: 'var(--color-ok)', warn: 'var(--color-warn)', bad: 'var(--color-bad)' } as const;

/**
 * v0.1.78 (G0): chip sức khoẻ hệ thống ở header — sự cố hệ thống (kênh im, hết chuỗi model, cập nhật lỗi…) hiện ở ĐÂY, bấm mở
 * danh sách + nút tới chỗ xử lý; không trộn vào cảnh báo tình báo. Chỉ vai có `system.read`. Lỗi tải ⇒ không vẽ (không báo sai là "ổn").
 */
export function HealthChip() {
  const canRead = useCan('system.read');
  const health = useSystemHealth(canRead);
  const [open, setOpen] = useState(false);
  const wrap = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (!wrap.current?.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false);
    };
    document.addEventListener('mousedown', onDown);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDown);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);
  if (!canRead || !health.isSuccess) return null;
  const issues = Array.isArray(health.data?.issues) ? sortIssues(health.data.issues) : [];
  const view = healthChipView(issues);
  return (
    <div className="hd-health" ref={wrap}>
      <button
        type="button"
        className="hd-pill-btn"
        data-testid="health-chip"
        data-tone={view.tone}
        aria-haspopup="true"
        aria-expanded={open}
        aria-label={`Sức khoẻ hệ thống: ${view.text}`}
        onClick={() => setOpen((o) => !o)}
      >
        <Pill icon={view.tone === 'ok' ? 'ph ph-check-circle' : 'ph ph-warning-circle'} iconColor={TONE[view.tone]}>
          <span className="hd-health__txt">{view.text}</span>
        </Pill>
      </button>
      {open ? (
        <div className="hd-health__menu" role="region" aria-label="Sự cố hệ thống" data-testid="health-menu">
          {issues.length === 0 ? (
            <p className="hd-health__empty">Không có sự cố nào. Kênh, model, sao lưu và ổ đĩa đều ổn.</p>
          ) : (
            issues.map((i) => (
              <div key={i.key} className="hd-health__item" data-severity={i.severity}>
                <Icon name="ph ph-warning-circle" size={14} color={TONE[i.severity === 'bad' ? 'bad' : 'warn']} />
                <div className="hd-health__text">
                  <div className="hd-health__title">{String(i.title)}</div>
                  <Link className="hd-health__link" to={healthIssueLink(i.link)} onClick={() => setOpen(false)}>
                    {i.action ? String(i.action) : 'Xem'}
                  </Link>
                </div>
              </div>
            ))
          )}
          <Link className="hd-health__all" to={HEALTH_ALL} onClick={() => setOpen(false)}>
            Mở Sức khoẻ hệ thống
          </Link>
        </div>
      ) : null}
    </div>
  );
}

const HEALTH_ALL = '/admin/backup?focus=health';
