import { Icon } from '@gen-harness/ui';
import { useToasts } from '../lib/toast';

const ICON = { ok: 'ph ph-check-circle', warn: 'ph ph-warning-circle', bad: 'ph ph-warning-circle', neutral: 'ph ph-info' } as const;

/** Bottom-right stack of transient confirmations. */
export function ToastHost() {
  const toasts = useToasts((s) => s.toasts);
  const dismiss = useToasts((s) => s.dismiss);
  return (
    <div className="toasts" role="status" aria-live="polite">
      {toasts.map((t) => (
        <div key={t.id} className="toast" data-tone={t.tone}>
          <Icon name={ICON[t.tone]} size={14} className="toast__icon" />
          <span className="toast__text">{t.text}</span>
          <button type="button" className="toast__close" aria-label="Đóng thông báo" onClick={() => dismiss(t.id)}>
            <Icon name="ph ph-x" size={12} />
          </button>
        </div>
      ))}
    </div>
  );
}
