import { useState, type MouseEvent } from 'react';
import { Link } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import type { RefineryProgress, SetupState } from '@gen-harness/contracts';
import { Button, Icon } from '@gen-harness/ui';
import { api } from '../../lib/api';
import { qk2 } from '../../lib/dataQueries';
import { errorDetail } from '../../lib/errorText';
import { fmtInt } from '../../lib/format';
import { qk } from '../../lib/queries';
import { queryClient } from '../../lib/queryClient';
import { InlineError } from '../../screens/common';
import { firstRunCounters } from '../phase2Model';
import { aiRunning } from '../steps';
import { describeError } from '../types';
import { AI_LATER_TEXT } from './StepAiSource';

/** Đích của các nút ở bước Xong (đều ghi PUT /setup/steps/12 trước khi đi). */
const DONE_TARGETS = {
  front: '/owner',
  zalo: '/guide/5',
  telegram: '/connections#telegram',
  team: '/guide/10',
} as const;

/** Bước tuỳ chọn 4–11 chưa xong và chưa để sau — "Hoàn tất" ghi chúng là "để sau" (dùng mặc định của máy chủ). */
function pendingOptional(state: SetupState): number[] {
  return state.steps.filter((x) => x.n >= 4 && x.n <= 11 && x.status !== 'done' && x.status !== 'skipped').map((x) => x.n);
}

export interface StepDoneProps {
  state: SetupState;
  /** Sếp đã chọn "Làm sau" ở bước Nguồn AI (sessionStorage). */
  aiLater: boolean;
  /** PUT /setup/steps/12 đã thành công: SetupPage ghi trạng thái và đi tới `to`. */
  onFinished: (s: SetupState, to: string) => void;
  /** Quay lại bước Nguồn AI. */
  onBack: () => void;
}

/**
 * Bước 3 của Thiết lập một màn — Xong. Ba dòng tóm tắt (tài khoản · nguồn AI · lần lọc tin đầu), nút chính "Vào Mặt tiền" và
 * nút phụ nổi bật "Nối Zalo ngay" (Zalo là nguồn tin chính). Mọi nút/link đi tiếp đều ghi PUT /setup/steps/12 trước (chỉ bắt buộc
 * bước 1–3); lỗi hiện câu thân thiện + "Chi tiết kỹ thuật".
 */
export function StepDone({ state, aiLater, onFinished, onBack }: StepDoneProps) {
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [detail, setDetail] = useState<string | null>(null);

  const me = useQuery({ queryKey: qk.me, queryFn: ({ signal }) => api.auth.me(signal), retry: false });
  const live = useQuery<RefineryProgress | null>({ queryKey: qk2.latestProgress, queryFn: () => null, enabled: false, initialData: null });
  const first = useQuery({
    queryKey: qk2.firstRun,
    queryFn: ({ signal }) => api.setup.firstRun(signal),
    // Phòng khi socket rớt: hỏi lại tới khi lần lọc đầu tiên xong; tiến độ realtime (WS) cập nhật xen giữa.
    refetchInterval: (q) => (q.state.data?.run?.status === 'done' ? false : 5000),
  });
  const c = firstRunCounters(first.data, live.data);

  const aiReady = aiRunning(state);
  const who = me.data?.display_name;
  const org = me.data?.org?.name;

  const finish = async (to: string) => {
    setBusy(to);
    setError(null);
    setDetail(null);
    try {
      // "Để sau" = dùng mặc định (API v0.1.28): bước 4–11 chưa làm được ghi "để sau" TRƯỚC khi Hoàn tất — máy chủ nạp bộ quy tắc
      // khởi đầu (bước 7), lịch sao lưu 02:00 (bước 11) và chép model đã gọi thử (bước 4). Việc còn lại hiện ở "Việc thiết lập
      // tiếp" / Cài đặt. Lỗi ở đây không chặn Hoàn tất (máy chủ chỉ bắt buộc bước 1–3).
      for (const n of pendingOptional(state)) {
        try {
          await api.setup.skip(n);
        } catch {
          /* tuỳ chọn — bỏ qua */
        }
      }
      const s = await api.setup.step12();
      void queryClient.invalidateQueries({ queryKey: qk.me });
      void queryClient.invalidateQueries({ queryKey: qk.navigation });
      onFinished(s, to);
    } catch (e) {
      setError(describeError(e));
      setDetail(errorDetail(e));
      setBusy(null);
    }
  };
  const go = (to: string) => (e: MouseEvent) => {
    e.preventDefault();
    if (!busy) void finish(to);
  };

  const firstRunText =
    first.isError
      ? 'Chưa đọc được tiến độ lọc tin — Gen vẫn tự lọc khi có tin về.'
      : c.status === 'done'
        ? `Gen đã lọc xong tin lần đầu · ${fmtInt(c.clean)} tin vào kho sạch`
        : c.status === 'failed'
          ? 'Lần lọc tin đầu tiên gặp lỗi — xem ở Kho dữ liệu thô.'
          : c.status === 'running'
            ? `Gen đang lọc tin lần đầu · ${c.pct}%`
            : 'Gen sẽ lọc tin lần đầu ngay khi có tin về';

  return (
    <section className="setup-one__panel" aria-labelledby="setup-one-title" data-testid="setup-done">
      <h1 className="setup-one__title" id="setup-one-title">
        Xong rồi, thưa Sếp
      </h1>
      <p className="setup-one__lead">
        {aiReady ? 'Gen đã sẵn sàng. Đây là những gì đã bật:' : 'Tài khoản đã xong. Nối nguồn AI lúc nào cũng được.'}
      </p>

      <ul className="setup-one__sum" aria-label="Tóm tắt thiết lập">
        <li className="setup-one__sum-row" data-testid="setup-done-account">
          <Icon name="ph ph-check-circle" size={16} color="var(--color-ok)" className="setup-one__sum-icon" />
          <div className="setup-one__sum-body">
            <span className="setup-one__sum-k">Tài khoản</span>
            <span className="setup-one__sum-v">{who ? `${who}${org ? ` · ${org}` : ''}` : 'Tài khoản Sếp đã tạo'}</span>
          </div>
        </li>
        <li className="setup-one__sum-row" data-testid="setup-done-ai" data-state={aiReady ? 'ok' : 'later'}>
          <Icon
            name={aiReady ? 'ph ph-check-circle' : 'ph ph-clock'}
            size={16}
            color={aiReady ? 'var(--color-ok)' : 'var(--color-warn)'}
            className="setup-one__sum-icon"
          />
          <div className="setup-one__sum-body">
            <span className="setup-one__sum-k">Nguồn AI</span>
            <span className="setup-one__sum-v">{aiReady ? 'Đã chạy' : `Làm sau — ${AI_LATER_TEXT}`}</span>
            {!aiReady && !aiLater ? <span className="setup-one__muted">Sếp nối lúc nào cũng được ở Cài đặt › Nguồn AI › Đổi nguồn AI.</span> : null}
          </div>
        </li>
        <li className="setup-one__sum-row" data-testid="setup-done-first-run" aria-live="polite">
          <Icon
            name={c.status === 'done' ? 'ph ph-check-circle' : c.status === 'failed' ? 'ph ph-warning' : 'ph ph-funnel'}
            size={16}
            color={c.status === 'done' ? 'var(--color-ok)' : c.status === 'failed' ? 'var(--color-bad)' : 'var(--color-accent-300)'}
            className="setup-one__sum-icon"
          />
          <div className="setup-one__sum-body">
            <span className="setup-one__sum-k">Lọc tin</span>
            <span className="setup-one__sum-v">{firstRunText}</span>
          </div>
        </li>
      </ul>

      {error ? <InlineError detail={detail}>{error}</InlineError> : null}

      <div className="setup-one__cta">
        <Button variant="primary" iconRight="ph ph-arrow-right" loading={busy === DONE_TARGETS.front} disabled={!!busy} onClick={() => void finish(DONE_TARGETS.front)}>
          Vào Mặt tiền
        </Button>
        <Button variant="secondary" icon="ph ph-chat-circle-dots" loading={busy === DONE_TARGETS.zalo} disabled={!!busy} onClick={() => void finish(DONE_TARGETS.zalo)} data-testid="setup-done-zalo">
          Nối Zalo ngay
        </Button>
        <p className="setup-one__cta-why">Zalo là nguồn tin chính — chưa nối thì Mặt tiền còn trống.</p>
      </div>

      <div className="setup-one__links">
        <Link to={DONE_TARGETS.telegram} className="setup-one__link" onClick={go(DONE_TARGETS.telegram)}>
          Nhận bản tin qua Telegram
        </Link>
        <Link to={DONE_TARGETS.team} className="setup-one__link" onClick={go(DONE_TARGETS.team)}>
          Mời đội
        </Link>
        <button type="button" className="setup-one__link setup-one__link--quiet" onClick={onBack} disabled={!!busy}>
          Quay lại nguồn AI
        </button>
      </div>
    </section>
  );
}
