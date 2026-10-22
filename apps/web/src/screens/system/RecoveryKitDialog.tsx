import { useEffect, useMemo, useRef, useState } from 'react';
import { useMutation } from '@tanstack/react-query';
import qrcode from 'qrcode-generator';
import { PinCancelledError, type RecoveryKit } from '@gen-harness/contracts';
import { Button, Dialog, Icon } from '@gen-harness/ui';
import { api } from '../../lib/api';
import { errorText } from '../../lib/errorText';
import { InlineError, SkeletonLines } from '../common';
import { errorCodeOf, keyCreatedText, offsiteApiErrorText } from './offsiteModel';

/** Chế độ mã QR: base32 HOA + '-' nằm trọn trong bảng Alphanumeric (gọn hơn Byte); chữ khác thì dùng Byte. */
const ALNUM_RE = /^[0-9A-Z $%*+\-./:]+$/;

/**
 * Mã QR của CHÍNH chuỗi khoá, vẽ SVG ngay trong trình duyệt (`qrcode-generator`, không gọi dịch vụ ngoài, không
 * dangerouslySetInnerHTML — mỗi ô tối là một đoạn path).
 */
export function QrSvg({ text, size = 168, label }: { text: string; size?: number; label: string }) {
  const { path, dim } = useMemo(() => {
    const qr = qrcode(0, 'M');
    qr.addData(text, ALNUM_RE.test(text) ? 'Alphanumeric' : 'Byte');
    qr.make();
    const n = qr.getModuleCount();
    let d = '';
    for (let r = 0; r < n; r += 1) for (let c = 0; c < n; c += 1) if (qr.isDark(r, c)) d += `M${c + 4} ${r + 4}h1v1h-1z`;
    return { path: d, dim: n + 8 };
  }, [text]);
  return (
    <svg className="rk-qr" width={size} height={size} viewBox={`0 0 ${dim} ${dim}`} role="img" aria-label={label} shapeRendering="crispEdges">
      <rect width={dim} height={dim} fill="#fff" />
      <path d={path} fill="#000" />
    </svg>
  );
}

/**
 * v0.1.40 (F-12): "Bộ khôi phục" — Khoá khôi phục mở bản sao ngoài máy / gói mang đi. Chỉ Owner + PIN
 * (`GET /system/offsite/recovery-kit`, no-store). Khoá chỉ sống trong state của hộp này: gọi bằng useMutation
 * (gcTime 0, reset ngay khi nhận) — không vào bộ đệm truy vấn, không log, không localStorage; đóng hộp ⇒ xoá khoá.
 */
export function RecoveryKitDialog({ onClose }: { onClose: () => void }) {
  const [kit, setKit] = useState<RecoveryKit | null>(null);
  const started = useRef(false);
  const load = useMutation({
    mutationFn: () => api.offsite.recoveryKit(),
    gcTime: 0,
    onSuccess: (k) => {
      setKit(k);
      // Không để khoá nằm lại trong MutationCache.
      load.reset();
    },
  });

  useEffect(() => {
    if (started.current) return;
    started.current = true;
    load.mutate();
  }, [load]);

  // In: chỉ in hộp này (CSS @media print theo lớp trên body).
  useEffect(() => {
    document.body.classList.add('rk-open');
    return () => document.body.classList.remove('rk-open');
  }, []);

  const close = () => {
    setKit(null);
    onClose();
  };

  return (
    <Dialog
      open
      onClose={close}
      width={560}
      className="rk-dialog"
      title="Bộ khôi phục"
      kicker="Khoá khôi phục mở bản sao ngoài máy và gói mang đi"
      actions={
        <>
          <Button variant="secondary" icon="ph ph-printer" disabled={!kit} onClick={() => window.print()}>
            In
          </Button>
          <Button variant="primary" icon="ph ph-check" onClick={close}>
            Đã cất xong
          </Button>
        </>
      }
    >
      {kit ? (
        <div className="rk-body" data-testid="recovery-kit">
          <div className="rk-key-row">
            <div className="rk-key-col">
              <div className="rk-label">Khoá khôi phục</div>
              <div className="rk-key mono" data-testid="recovery-key">
                {kit.key}
              </div>
              <div className="rk-keyid">
                Mã nhận diện khoá: <span className="mono">{kit.key_id}</span>
                {keyCreatedText(kit.created_hint) ? <span data-testid="recovery-key-created"> · {keyCreatedText(kit.created_hint)}</span> : null}
              </div>
            </div>
            <QrSvg text={kit.key} label="Mã QR của Khoá khôi phục" />
          </div>
          <div className="rk-warn" role="note">
            <Icon name="ph ph-warning" size={14} />
            <span>
              <strong>Cất TÁCH khỏi ổ USB.</strong> {typeof kit.warning === 'string' ? kit.warning : ''}
            </span>
          </div>
          {kit.steps.length ? (
            <>
              <div className="rk-label">Cách khôi phục khi máy chủ hỏng</div>
              <ol className="rk-steps">
                {kit.steps.map((s, i) => (
                  <li key={i}>{typeof s === 'string' ? s : ''}</li>
                ))}
              </ol>
            </>
          ) : null}
        </div>
      ) : load.isError ? (
        <RecoveryKitError error={load.error} onRetry={() => load.mutate()} />
      ) : (
        <SkeletonLines rows={4} padding="0" />
      )}
    </Dialog>
  );
}

/** Lỗi mở Bộ khôi phục: câu thân thiện + "Chi tiết kỹ thuật" CHỈ khi có mã (huỷ PIN/lỗi mạng thì không có) + Thử lại. */
function RecoveryKitError({ error, onRetry }: { error: unknown; onRetry: () => void }) {
  const code = error instanceof PinCancelledError ? '' : errorCodeOf(error);
  return (
    <InlineError>
      {error instanceof PinCancelledError ? 'Chưa mở — cần nhập mã PIN.' : (offsiteApiErrorText(error) ?? errorText(error))}
      {code ? (
        <details className="tech-detail">
          <summary>Chi tiết kỹ thuật</summary>
          <code className="mono">{code}</code>
        </details>
      ) : null}
      <div className="rk-retry">
        <Button variant="secondary" className="btn-27" icon="ph ph-arrow-clockwise" onClick={onRetry}>
          Thử lại
        </Button>
      </div>
    </InlineError>
  );
}
