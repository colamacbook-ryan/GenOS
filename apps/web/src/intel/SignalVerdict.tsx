import { useState } from 'react';
import { Badge, Button } from '@gen-harness/ui';
import { errorDetail, errorText } from '../lib/errorText';
import { toast } from '../lib/toast';
import { InlineError } from '../screens/common';
import { WRONG_REASONS, type WrongReason } from './intelModel';
import type { SignalVerdict as Verdict } from './intelHubModel';
import { useSignalFeedback } from './intelQueries';

const DONE_TEXT: Record<string, string> = { correct: 'Đã chấm Đúng', wrong: 'Đã chấm Sai', unimportant: 'Đã chấm: không cần báo', missed: 'Đã ghi: báo sót' };

/**
 * v0.1.79 (G1) — nút chấm tín hiệu: **Đúng** · **Sai** (chọn 1 trong 3 lý do như bản tin: khách vẫn mua · không gấp · NV lo rồi) ·
 * **Không cần báo**. Các lượt chấm này nuôi M1 (chạy thử 14 ngày). Đã chấm ⇒ chỉ còn nhãn. `compact` = hàng danh sách (tên nút kèm tiêu đề
 * để đọc màn hình phân biệt các hàng); không compact = ngăn chi tiết. Chỉ Owner (máy chủ kiểm).
 */
export function SignalVerdict({ id, title, verdict, compact = false }: { id: string; title: string; verdict: Verdict | null; compact?: boolean }) {
  const fb = useSignalFeedback();
  const [asking, setAsking] = useState(false);
  const send = (v: 'correct' | 'wrong' | 'unimportant', reason?: WrongReason) =>
    fb.mutate(
      { id, verdict: v, ...(reason ? { reason } : {}) },
      { onSuccess: () => { setAsking(false); toast(v === 'correct' ? 'Đã ghi: báo đúng' : v === 'wrong' ? 'Đã ghi: báo sai' : 'Đã ghi: không cần báo'); } },
    );
  const name = (base: string) => (compact ? { 'aria-label': `${base}: ${title}` } : {});
  const err = fb.error ? <InlineError detail={errorDetail(fb.error)}>{errorText(fb.error)}</InlineError> : null;
  if (verdict && DONE_TEXT[verdict]) {
    return <Badge kind="status" tone={verdict === 'correct' ? 'ok' : 'warn'}>{DONE_TEXT[verdict]}</Badge>;
  }
  if (asking) {
    return (
      <span className="ih-verdict">
        <span className="ih-verdict__group" role="group" aria-label={compact ? `Vì sao báo sai: ${title}` : 'Vì sao báo sai?'}>
          <span className="muted-note">Vì sao sai?</span>
          {WRONG_REASONS.map((r) => (
            <Button key={r.value} variant="secondary" size="sm" aria-label={`Sai: ${r.label}${compact ? ` — ${title}` : ''}`} disabled={fb.isPending} onClick={() => send('wrong', r.value)}>
              {r.label}
            </Button>
          ))}
          <Button variant="ghost" size="sm" onClick={() => setAsking(false)}>Huỷ</Button>
        </span>
        {err}
      </span>
    );
  }
  return (
    <span className="ih-verdict">
      <span className="ih-verdict__group">
        <Button variant="secondary" size="sm" disabled={fb.isPending} {...name('Báo đúng')} onClick={() => send('correct')}>Đúng</Button>
        <Button variant="secondary" size="sm" disabled={fb.isPending} {...name('Báo sai')} onClick={() => setAsking(true)}>Sai ▾</Button>
        <Button variant="ghost" size="sm" disabled={fb.isPending} {...name('Không cần báo')} onClick={() => send('unimportant')}>Không cần báo</Button>
      </span>
      {err}
    </span>
  );
}
