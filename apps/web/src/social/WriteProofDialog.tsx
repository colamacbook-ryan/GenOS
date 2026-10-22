/** v0.1.47 (F-79) — "Xem ảnh chụp": ảnh chụp bằng chứng của một lần gửi lên Facebook + khối lỗi thân thiện dùng chung. */
import { useState } from 'react';
import { Button, Dialog } from '@gen-harness/ui';
import { api } from '../lib/api';
import { errorDetail, errorText } from '../lib/errorText';

export function WriteProofDialog({ jobId, open, onClose }: { jobId: string; open: boolean; onClose: () => void }) {
  const [failed, setFailed] = useState(false);
  return (
    <Dialog
      open={open}
      onClose={onClose}
      width={720}
      title="Ảnh chụp bằng chứng lần gửi"
      kicker="Chụp ngay sau khi gửi — lưu 90 ngày, mã hoá"
      actions={
        <Button variant="secondary" onClick={onClose}>
          Đóng
        </Button>
      }
    >
      {failed ? (
        <p className="risk-box__text" role="alert">
          Không mở được ảnh chụp (ảnh có thể đã hết hạn lưu hoặc không đọc được).
        </p>
      ) : (
        <img className="write-proof__img" src={api.social.proofUrl(jobId)} alt="Ảnh chụp bằng chứng lần gửi" onError={() => setFailed(true)} />
      )}
    </Dialog>
  );
}

/** Lỗi cho người dùng: câu thân thiện (luôn là chuỗi) + "Chi tiết kỹ thuật" (mã lỗi, mã yêu cầu). */
export function ErrorWithDetail({ error, text, detail, className }: { error?: unknown; text?: string; detail?: string | null; className?: string }) {
  const tech = detail ?? (error === undefined ? null : errorDetail(error));
  return (
    <div className={className ?? 'write-error'} role="alert">
      <p className="write-error__text">{text ?? errorText(error)}</p>
      {tech ? (
        <details className="write-error__detail">
          <summary>Chi tiết kỹ thuật</summary>
          <code>{tech}</code>
        </details>
      ) : null}
    </div>
  );
}
