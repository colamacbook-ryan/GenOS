import { useEffect, useRef, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { PinCancelledError, type SystemHealth } from '@gen-harness/contracts';
import { Button } from '@gen-harness/ui';
import { errorDetail, errorText } from '../../lib/errorText';
import { useCan, useOrgTimezone } from '../../lib/permissions';
import { useMe } from '../../lib/queries';
import { useNow } from '../../lib/useNow';
import { CardError, InlineError, Panel, SkeletonLines } from '../common';
import { TONE_COLOR, healthQueues, healthRows, healthTechRows, healthTips, type HealthQueue } from './healthModel';
import { useClearQueue, useReplayQueue, useSystemHealth } from './queries';

/**
 * v0.1.36 (F-6): thẻ "Sức khoẻ hệ thống" đầu Điều khiển hệ thống › Dữ liệu & lưu trữ — Bộ xử lý nền, Trình duyệt nền,
 * hàng lỗi (DLQ), sao lưu, cập nhật, ổ đĩa, tự cập nhật đêm (`GET /system/health`); "Chi tiết kỹ thuật" liệt kê lịch
 * chạy và từng hàng lỗi. Chỉ vai trò có `system.read`. Hướng dẫn từng bước (`healthTips`, kể cả "Cách bật lại lịch tự
 * cập nhật đêm" — đích của nút "Xem cách bật lại") hiện ngay dưới các dòng.
 */
export function HealthCard() {
  const canRead = useCan('system.read');
  const tz = useOrgTimezone();
  const now = useNow(30_000);
  const q = useSystemHealth(canRead);
  // Dải "Cần Sếp xử lý" (job.timeout → `/system?tab=storage&focus=health`, nút "Xem sức khoẻ"): cuộn tới thẻ này.
  const [params] = useSearchParams();
  const focusHealth = params.get('focus') === 'health';
  const focused = useRef(false);
  useEffect(() => {
    if (!focusHealth || focused.current || !q.data) return;
    focused.current = true;
    const panel = document.querySelector<HTMLElement>('[data-gen-target="system.storage.health"]');
    if (!panel) return;
    panel.scrollIntoView?.({ block: 'start' });
    panel.setAttribute('tabindex', '-1');
    panel.focus({ preventScroll: true });
  }, [focusHealth, q.data]);
  if (!canRead) return null;
  return (
    <Panel genTarget="system.storage.health" title="Sức khoẻ hệ thống" kicker="Tự kiểm mỗi phút" label="Sức khoẻ hệ thống" bodyClass="health-card" className="health-card-panel">
      {q.isPending ? (
        <SkeletonLines rows={6} padding="0" />
      ) : q.isError ? (
        <CardError error={q.error} onRetry={() => void q.refetch()} retrying={q.isFetching} />
      ) : (
        <HealthBody data={q.data} now={now} tz={tz} />
      )}
    </Panel>
  );
}

function HealthBody({ data, now, tz }: { data: SystemHealth; now: number; tz: string }) {
  const canManage = useCan('system.manage');
  const isOwner = useMe().data?.role?.code === 'owner';
  const rows = healthRows(data, now, tz, { isOwner, canManage });
  const tech = healthTechRows(data, tz);
  const tips = healthTips(data);
  const queues = healthQueues(data);
  return (
    <>
      <ul className="health-card__rows">
        {rows.map((r) => (
          <li className="health-card__row" key={r.key} data-tone={r.tone} data-testid={`health-${r.key}`}>
            <span className="health-card__dot" style={{ background: TONE_COLOR[r.tone] }} aria-hidden />
            <span className="health-card__label">{r.label}</span>
            <span className="health-card__value" style={r.tone === 'bad' || r.tone === 'warn' ? { color: TONE_COLOR[r.tone] } : undefined}>
              {r.value}
            </span>
            {r.hint ? <span className="health-card__hint">{r.hint}</span> : null}
          </li>
        ))}
      </ul>
      {queues.length > 0 ? <QueueActions queues={queues} canManage={canManage} /> : null}
      {tips.map((t) => (
        <div className="health-card__tip" key={t.key} data-testid={`health-tip-${t.key}`} role="note" aria-label={t.title}>
          <div className="health-card__tip-title">{t.title}</div>
          <ol className="health-card__tip-steps">
            {t.steps.map((st, i) => (
              <li key={i}>
                {st.text}
                {st.cmd ? (
                  <>
                    {' '}
                    <code className="mono">{st.cmd}</code>
                  </>
                ) : null}
              </li>
            ))}
          </ol>
          {t.warning ? <p className="health-card__tip-warn">{t.warning}</p> : null}
        </div>
      ))}
      <details className="tech-detail health-card__tech">
        <summary>Chi tiết kỹ thuật</summary>
        <div className="health-card__tech-title">Lịch chạy</div>
        {tech.crons.length === 0 ? (
          <p className="muted-note">Chưa có lịch chạy nào ghi nhận.</p>
        ) : (
          <ul className="health-card__tech-list">
            {tech.crons.map((c) => (
              <li key={c.name}>
                <code className="mono">{c.name}</code>
                <span style={{ color: TONE_COLOR[c.tone] }}>{c.value}</span>
              </li>
            ))}
          </ul>
        )}
        <div className="health-card__tech-title">Hàng lỗi (DLQ)</div>
        {tech.queues.length === 0 ? (
          <p className="muted-note">Không có hàng lỗi.</p>
        ) : (
          <ul className="health-card__tech-list">
            {tech.queues.map((x) => (
              <li key={x.name}>
                <code className="mono">{x.name}</code>
                <span style={{ color: TONE_COLOR[x.tone] }}>{x.value}</span>
              </li>
            ))}
          </ul>
        )}
      </details>
    </>
  );
}

/**
 * F064/F102: hàng lỗi có lối xử lý — theo loại. Tin khách vào: "Phát lại" (nhập kho lại); mọi hàng: "Đã xem, xoá" (hỏi lại
 * một bước vì không hoàn tác). Cần `system.manage` + mã PIN (hộp PIN tự mở); người không có quyền chỉ thấy số lượng.
 */
function QueueActions({ queues, canManage }: { queues: HealthQueue[]; canManage: boolean }) {
  const replay = useReplayQueue();
  const clear = useClearQueue();
  const [confirming, setConfirming] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);
  const [failed, setFailed] = useState<unknown>(null);
  const busy = replay.isPending || clear.isPending;

  const run = (kind: 'replay' | 'clear', q: HealthQueue) => {
    setDone(null);
    setFailed(null);
    setConfirming(null);
    const onError = (e: unknown) => setFailed(e);
    if (kind === 'replay') {
      replay.mutate(q.stream, {
        onSuccess: (r) =>
          setDone(r.remaining > 0 ? `Đã phát lại ${r.replayed} tin, còn ${r.remaining} tin — bấm Phát lại tiếp.` : `Đã phát lại ${r.replayed} tin — hệ thống sẽ nhập kho lại ngay.`),
        onError,
      });
    } else {
      clear.mutate(q.stream, { onSuccess: (r) => setDone(`Đã xoá ${r.cleared} việc lỗi khỏi hàng "${q.label}".`), onError });
    }
  };

  return (
    <div className="health-card__queues" data-testid="health-queues" role="group" aria-label="Việc nền bị lỗi theo loại">
      {queues.map((q) => (
        <div className="health-card__queue" key={q.stream} data-testid={`health-queue-${q.stream}`}>
          <span className="health-card__queue-label">
            {q.label} <b>{q.count}</b>
          </span>
          {canManage ? (
            <span className="health-card__queue-actions">
              {q.replayable ? (
                <Button size="sm" variant="primary" icon="ph ph-arrow-counter-clockwise" loading={replay.isPending && replay.variables === q.stream} disabled={busy} onClick={() => run('replay', q)}>
                  Phát lại
                </Button>
              ) : null}
              {confirming === q.stream ? (
                <>
                  <span className="muted-note">Xoá {q.count} việc, không hoàn tác?</span>
                  <Button size="sm" variant="secondary" loading={clear.isPending && clear.variables === q.stream} disabled={busy} onClick={() => run('clear', q)}>
                    Xoá luôn
                  </Button>
                  <Button size="sm" variant="secondary" disabled={busy} onClick={() => setConfirming(null)}>
                    Giữ lại
                  </Button>
                </>
              ) : (
                <Button size="sm" variant="secondary" icon="ph ph-check" disabled={busy} onClick={() => setConfirming(q.stream)}>
                  Đã xem, xoá
                </Button>
              )}
            </span>
          ) : null}
        </div>
      ))}
      {!canManage ? <p className="muted-note">Nhờ người có quyền quản lý hệ thống phát lại hoặc dọn các việc này.</p> : null}
      {done ? (
        <p className="muted-note" role="status" data-testid="health-queue-done">
          {done}
        </p>
      ) : null}
      {failed ? (
        <InlineError detail={errorDetail(failed)}>
          {failed instanceof PinCancelledError ? 'Chưa làm — cần nhập mã PIN.' : errorText(failed)}
        </InlineError>
      ) : null}
    </div>
  );
}
