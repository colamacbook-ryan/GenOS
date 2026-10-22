import { useEffect, useRef, useState } from 'react';
import { useMutation, useQuery } from '@tanstack/react-query';
import { ApiError, type SystemUpdate } from '@gen-harness/contracts';
import { Button, Dialog, Icon } from '@gen-harness/ui';
import { api } from '../lib/api';
import { errorText } from '../lib/errorText';
import { fmtDMClock } from '../lib/format';
import { queryClient } from '../lib/queryClient';
import { Panel, SkeletonLines } from '../screens/common';
import { toast } from '../lib/toast';
import { COMMAND_LABEL_SERVER, UPDATED_FLAG, UPDATE_COMMAND, UPDATE_KEY, canClickUpdate, readableNotes, updatePollMs, updateView } from './updateModel';

/**
 * Thẻ "Có bản mới" ở Tổng quan: bấm "Cập nhật ngay" để genh trên máy chủ tự sao lưu → tải bản mới → khởi động lại
 * (gh/system_api/update.py). Trong lúc cập nhật api khởi động lại nên các lần hỏi trạng thái có thể lỗi mạng — coi là
 * "đang khởi động lại", không phải lỗi. Xong thì tự tải lại trang để chạy giao diện bản mới.
 */
export function UpdateCard({ always = false, hideFailed = false }: { always?: boolean; hideFailed?: boolean } = {}) {
  /** Bản Owner đã bấm cập nhật lên (giữ qua lúc api tắt/bật). */
  const [waitingFor, setWaitingFor] = useState<string | null>(null);
  const [confirm, setConfirm] = useState(false);
  const reloaded = useRef(false);
  const q = useQuery({
    queryKey: UPDATE_KEY,
    queryFn: ({ signal }) => api.systemUpdate.get(signal),
    retry: false,
    refetchInterval: (query) => {
      const s = query.state.data?.state;
      return waitingFor ? 4000 : updatePollMs(s);
    },
  });
  const request = useMutation({
    mutationFn: () => api.systemUpdate.request(),
    onSuccess: (data) => {
      queryClient.setQueryData(UPDATE_KEY, data);
      setWaitingFor(data.latest ?? 'bản mới');
      setConfirm(false);
    },
  });

  // v0.1.30: "Kiểm tra bản mới" — hỏi GitHub ngay (máy chủ đệm 10 phút, nút này bỏ qua bộ đệm).
  const check = useMutation({
    mutationFn: () => api.systemUpdate.check(),
    onSuccess: (data) => {
      queryClient.setQueryData(UPDATE_KEY, data);
      if (data.update_available) toast(`Có bản mới ${data.latest}.`);
      else if (data.throttled) toast('Vừa kiểm tra xong — thử lại sau ít giây.', 'warn');
      else if (data.latest) toast(`Đang dùng bản mới nhất (${data.current ?? data.latest}).`);
      else toast('Chưa hỏi được máy chủ phát hành — thử lại sau.', 'warn');
    },
    onError: (e) => toast(errorText(e), 'bad'),
  });

  const view = updateView(q.data, { waitingFor, offline: !!waitingFor && q.isError });

  const currentVersion = q.data?.current;
  useEffect(() => {
    if (view.kind === 'finished' && waitingFor && !reloaded.current) {
      reloaded.current = true;
      try {
        window.sessionStorage.setItem(UPDATED_FLAG, currentVersion ?? waitingFor);
      } catch {
        /* chế độ riêng tư: bỏ qua thông báo sau tải lại */
      }
      window.setTimeout(() => window.location.reload(), 1500);
    }
  }, [view.kind, waitingFor, currentVersion]);

  // v0.1.28 (UX V11): sau khi tự tải lại — báo rõ đã lên bản nào (một lần).
  useEffect(() => {
    try {
      const v = window.sessionStorage.getItem(UPDATED_FLAG);
      if (v) {
        window.sessionStorage.removeItem(UPDATED_FLAG);
        toast(`Đã cập nhật lên ${v}.`);
      }
    } catch {
      /* không có sessionStorage */
    }
  }, []);

  // Không phải Owner/quản trị (403) hay api chưa có tính năng này: im lặng.
  if (q.isError && !waitingFor && !(q.error instanceof ApiError && q.error.status === 0)) return null;
  // v0.1.30: `always` = mục "Cập nhật phần mềm" cố định (Điều khiển hệ thống › Dữ liệu & lưu trữ, Trợ giúp) — không
  // bao giờ biến mất như thẻ Tổng quan (chỉ hiện khi biết có bản mới).
  if (view.kind === 'hidden' && !always) return null;
  // v0.1.36 (F-6): Tổng quan — cập nhật lỗi đã có một dòng trong dải "Cần Sếp xử lý"; không lặp lại thẻ ở đây.
  // "Máy chủ chưa nhận yêu cầu" (stalled) KHÔNG có dòng sự cố trong dải nên vẫn hiện thẻ (có nút Thử lại).
  if (hideFailed && view.kind === 'failed') return null;
  if (always && q.isPending) {
    return (
      <Panel title="Cập nhật phần mềm" label="Cập nhật phần mềm" bodyClass="upd">
        <SkeletonLines rows={3} padding="0" />
      </Panel>
    );
  }
  const d = q.data as SystemUpdate | undefined;
  const idle = view.kind === 'hidden';
  const title = idle ? 'Cập nhật phần mềm' : view.title;
  const kicker = idle
    ? d?.linked === false
      ? 'Bản này không cài bằng genh — cập nhật theo cách đã cài'
      : d?.latest
        ? 'Đang dùng bản mới nhất'
        : 'Chưa hỏi được máy chủ phát hành — bấm Kiểm tra bản mới'
    : view.kicker;

  return (
    <Panel
      title={title}
      kicker={kicker}
      label={always ? 'Cập nhật phần mềm' : 'Cập nhật phiên bản'}
      bodyClass="upd"
      className={idle ? 'upd-card' : `upd-card upd-card--${view.tone}`}
      aside={
        view.kind === 'available' && d && canClickUpdate(d) ? (
          <Button variant="primary" icon="ph ph-arrow-circle-up" className="btn-30" onClick={() => setConfirm(true)}>
            Cập nhật ngay
          </Button>
        ) : view.kind === 'failed' || view.kind === 'stalled' ? (
          d?.can_request ? (
            <Button variant="secondary" icon="ph ph-arrow-clockwise" className="btn-30" loading={request.isPending} onClick={() => request.mutate()}>
              Thử lại
            </Button>
          ) : null
        ) : null
      }
    >
      {always ? (
        <div className="upd-info" data-testid="update-section">
          <div className="summary">
            <span className="summary__k">đang dùng</span>
            <span className="summary__v mono">{d?.current ?? 'bản phát triển'}</span>
            <span className="summary__k">bản mới nhất</span>
            <span className="summary__v mono">{d?.latest ?? '—'}</span>
            <span className="summary__k">kiểm tra lúc</span>
            <span className="summary__v">{d?.checked_at ? fmtDMClock(d.checked_at) : 'chưa kiểm tra'}</span>
          </div>
          <Button variant="secondary" size="sm" icon="ph ph-arrow-clockwise" loading={check.isPending} disabled={d?.linked === false} onClick={() => check.mutate()}>
            Kiểm tra bản mới
          </Button>
        </div>
      ) : null}
      {view.kind === 'working' ? (
        <ol className="upd-steps" aria-live="polite">
          {view.steps.map((s) => (
            <li key={s.label} data-state={s.state}>
              <Icon name={s.state === 'done' ? 'ph ph-check' : s.state === 'active' ? 'ph ph-circle-notch' : 'ph ph-circle'} size={13} />
              {s.label}
            </li>
          ))}
        </ol>
      ) : null}
      {view.block ? (
        // v0.1.55: nút "Cập nhật ngay" bị ẩn ⇒ lý do + việc cần làm; lệnh (nếu có) luôn đi SAU câu lý do.
        <div className="upd-block" data-testid="update-block-reason" data-reason={d?.request_block_reason ?? undefined}>
          <p className="upd-body">
            <strong>{view.block.title}</strong>
          </p>
          <p className="upd-body">{view.block.body}</p>
          <p className="upd-body">{view.block.action}</p>
          {view.block.command ? (
            <div className="upd-cmd">
              <span>{COMMAND_LABEL_SERVER}</span>
              <code className="mono">{view.block.command}</code>
            </div>
          ) : null}
        </div>
      ) : null}
      {view.kind !== 'hidden' && view.body ? <p className="upd-body">{view.body}</p> : null}
      {view.kind !== 'hidden' && view.detail ? (
        <details className="upd-notes">
          <summary>Chi tiết kỹ thuật</summary>
          <pre>{view.detail}</pre>
        </details>
      ) : null}
      {view.kind !== 'hidden' && view.showCommand ? (
        <div className="upd-cmd">
          <span>{view.commandLabel ?? 'Chạy lệnh này một lần trên máy chủ (lần sau chỉ cần bấm nút ở đây):'}</span>
          <code className="mono">{view.command ?? UPDATE_COMMAND}</code>
        </div>
      ) : null}
      {view.kind === 'available' && d?.release_notes ? (
        <details className="upd-notes">
          <summary>Có gì mới trong {d.latest}</summary>
          <pre>{readableNotes(d.release_notes)}</pre>
        </details>
      ) : null}
      {request.isError ? <p className="upd-error">{errorText(request.error)}</p> : null}
      <Dialog
        open={confirm}
        onClose={() => setConfirm(false)}
        width={460}
        title={`Cập nhật lên ${d?.latest ?? 'bản mới'}?`}
        kicker={`Đang dùng ${d?.current ?? '—'}`}
        actions={
          <>
            <Button variant="secondary" onClick={() => setConfirm(false)}>
              Để sau
            </Button>
            <Button variant="primary" icon="ph ph-arrow-circle-up" loading={request.isPending} onClick={() => request.mutate()}>
              Cập nhật ngay
            </Button>
          </>
        }
      >
        <ul className="upd-confirm">
          <li>Hệ thống tự sao lưu dữ liệu trước, rồi tải bản mới và khởi động lại.</li>
          <li>Mất khoảng 2–5 phút; trong lúc đó Console tạm ngắt — trang tự tải lại khi xong.</li>
          <li>Nếu có lỗi, hệ thống tự quay về bản đang dùng, dữ liệu giữ nguyên.</li>
        </ul>
        {request.isError ? <p className="upd-error">{errorText(request.error)}</p> : null}
      </Dialog>
    </Panel>
  );
}
