import { useEffect, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useSearchParams } from 'react-router-dom';
import { ApiError, PinCancelledError, type OffsiteState } from '@gen-harness/contracts';
import { Button, Dialog, Icon, TextField } from '@gen-harness/ui';
import { api } from '../../lib/api';
import { errorText } from '../../lib/errorText';
import { useCan, useOrgTimezone } from '../../lib/permissions';
import { usePinStore } from '../../lib/pinStore';
import { useMe } from '../../lib/queries';
import { toast } from '../../lib/toast';
import { useNow } from '../../lib/useNow';
import { CardError, InlineError, Panel, SkeletonLines } from '../common';
import { RecoveryKitDialog } from './RecoveryKitDialog';
import {
  MANUAL_COMMAND_FALLBACK,
  MANUAL_COMMAND_INTRO,
  OFFSITE_KEY,
  OFFSITE_KEY_MISSING_TEXT,
  OFFSITE_PATH_HINTS,
  clearPortablePreparing,
  errorCodeOf,
  manualCommandOf,
  offsiteApiErrorText,
  offsiteBusy,
  offsiteRequestView,
  offsiteView,
  portablePreparing,
  startPortableDownload,
  subscribePortable,
} from './offsiteModel';

/** Lỗi API ⇒ câu thân thiện + "Chi tiết kỹ thuật" (mã, chuỗi) — không bao giờ render object. */
function ErrorBlock({ error }: { error: unknown }) {
  if (error instanceof PinCancelledError) return <InlineError>Chưa làm — cần nhập mã PIN.</InlineError>;
  const code = errorCodeOf(error);
  const unavailable = error instanceof ApiError && error.code === 'OFFSITE_UNAVAILABLE';
  return (
    <InlineError>
      {offsiteApiErrorText(error) ?? errorText(error)}
      {unavailable ? ` ${MANUAL_COMMAND_FALLBACK}` : null}
      {code ? (
        <details className="tech-detail">
          <summary>Chi tiết kỹ thuật</summary>
          <code className="mono">{code}</code>
        </details>
      ) : null}
    </InlineError>
  );
}

/** Lệnh chạy một lần trên máy chủ, dạng mã + nút Chép. */
function CommandBlock({ command, intro }: { command: string; intro: string }) {
  const copy = () => {
    // Console mở qua http (không an toàn) ⇒ không có navigator.clipboard — vẫn phải báo, không im lặng.
    if (!navigator.clipboard?.writeText) {
      toast('Không chép được — bôi đen lệnh rồi chép tay.', 'bad');
      return;
    }
    navigator.clipboard.writeText(command).then(
      () => toast('Đã chép lệnh.', 'ok'),
      () => toast('Không chép được — bôi đen lệnh rồi chép tay.', 'bad'),
    );
  };
  return (
    <div className="offsite-cmd" data-testid="offsite-manual-command">
      <span>{intro}</span>
      <div className="offsite-cmd__row">
        <code className="mono">{command}</code>
        <Button variant="ghost" className="btn-27" icon="ph ph-copy" onClick={copy} aria-label="Chép lệnh">
          Chép
        </Button>
      </div>
    </div>
  );
}

/** Có phiên PIN còn ít nhất 1 phút (hỏi lại /auth/me — không tin bộ đệm). */
async function hasPinSession(): Promise<boolean> {
  try {
    const me = await api.auth.me();
    const until = me.pin_verified_until ? Date.parse(me.pin_verified_until) : NaN;
    return Number.isFinite(until) && until - Date.now() > 60_000;
  } catch {
    return false;
  }
}

/** Lượt "Tải gói mang đi" đang chuẩn bị (cờ cấp module — sống qua đóng hộp thoại). */
function usePortablePreparing(): boolean {
  const [on, setOn] = useState(() => portablePreparing());
  useEffect(() => subscribePortable(setOn), []);
  return on;
}

/** Lỗi "Tải gói mang đi" giữ trên thẻ (không chỉ toast tự tắt): câu thân thiện + mã cho "Chi tiết kỹ thuật". */
interface PortableError {
  text: string;
  code: string;
}

/**
 * v0.1.40 (F-12): "Bản sao ngoài máy" — bản sao dữ liệu ra ổ USB/NAS cắm vào máy chủ (genh làm thật, lịch Chủ nhật
 * ~05:30). Hiện lần gần nhất + cảnh báo > 7 ngày; Owner chọn nơi lưu (PIN), tải gói mang đi (PIN) và xem Bộ khôi phục
 * (PIN); `system.manage` bấm "Sao lưu ra ổ ngoài ngay". Vai trò khác chỉ thấy trạng thái.
 */
export function OffsitePanel() {
  const canRead = useCan('system.read');
  const canManage = useCan('system.manage');
  const me = useMe();
  const isOwner = me.data?.role?.code === 'owner';
  const tz = useOrgTimezone();
  const now = useNow(60_000);
  const queryClient = useQueryClient();
  const [choosing, setChoosing] = useState(false);
  const [kitOpen, setKitOpen] = useState(false);
  const [portableOpen, setPortableOpen] = useState(false);
  const [portableError, setPortableError] = useState<PortableError | null>(null);
  const preparing = usePortablePreparing();

  const q = useQuery({
    queryKey: OFFSITE_KEY,
    queryFn: ({ signal }) => api.offsite.get(signal),
    enabled: canRead,
    refetchInterval: (query) => (offsiteBusy(query.state.data) ? 5000 : false),
  });

  // Dải "Cần Sếp xử lý" (offsite.stale/offsite.failed → `/system?tab=storage&focus=offsite`): cuộn tới thẻ và đặt
  // con trỏ vào thẻ (nút hứa gì, trang đích có nấy) — cùng khuôn BackupPanel với focus=backup.
  const [params] = useSearchParams();
  const focusOffsite = params.get('focus') === 'offsite';
  const focused = useRef(false);
  useEffect(() => {
    if (!focusOffsite || focused.current || !q.data) return;
    focused.current = true;
    const panel = document.querySelector<HTMLElement>('[data-gen-target="system.storage.offsite"]');
    if (!panel) return;
    panel.scrollIntoView?.({ block: 'start' });
    panel.setAttribute('tabindex', '-1');
    panel.focus({ preventScroll: true });
  }, [focusOffsite, q.data]);

  const runNow = useMutation({
    mutationFn: () => api.offsite.runNow(),
    onSuccess: (d) => {
      queryClient.setQueryData(OFFSITE_KEY, d);
      toast('Đã gửi yêu cầu sao lưu ra ổ ngoài.', 'ok');
    },
  });

  if (!canRead) return null;
  const d = q.data as OffsiteState | undefined;
  const v = d ? offsiteView(d, now, tz, { isOwner, canManage }) : null;
  const keyMissing = d?.key_present === false;
  const rq = offsiteRequestView(d, now);
  const busy = rq.kind === 'waiting' || rq.kind === 'running';
  const runCmd = manualCommandOf(runNow.error);

  return (
    <Panel
      title="Bản sao ngoài máy"
      genTarget="system.storage.offsite"
      kicker="Ổ USB/NAS cắm vào máy chủ — phòng khi hỏng ổ đĩa chính"
      label="Bản sao ngoài máy"
      bodyClass="offsite"
      aside={
        canManage ? (
          <Button
            variant="primary"
            icon="ph ph-hard-drives"
            className="btn-30"
            data-gen-target="system.offsite.run"
            loading={runNow.isPending || rq.kind === 'running'}
            disabled={!d || !d.configured || busy}
            title={d && !d.configured ? (isOwner ? 'Chọn nơi lưu bản sao ngoài máy trước' : 'Owner chưa chọn nơi lưu bản sao ngoài máy') : undefined}
            onClick={() => runNow.mutate()}
          >
            Sao lưu ra ổ ngoài ngay
          </Button>
        ) : undefined
      }
    >
      {q.isPending ? (
        <SkeletonLines rows={3} padding="4px 0" />
      ) : q.isError ? (
        <CardError error={q.error} onRetry={() => void q.refetch()} retrying={q.isFetching} />
      ) : d && v ? (
        <>
          {rq.kind !== 'none' ? (
            <div className={`offsite-req offsite-req--${rq.kind}`} role="status" aria-live="polite" data-testid="offsite-request">
              <Icon name={rq.kind === 'stalled' ? 'ph ph-warning' : 'ph ph-circle-notch'} size={13} className={rq.kind === 'stalled' ? undefined : 'spin'} /> {rq.text}
            </div>
          ) : null}
          {rq.kind === 'stalled' && canManage ? (
            d.manual_command ? (
              <CommandBlock command={d.manual_command} intro={MANUAL_COMMAND_INTRO} />
            ) : (
              <p className="muted-note" data-testid="offsite-manual-fallback">
                {MANUAL_COMMAND_FALLBACK}
              </p>
            )
          ) : null}

          <p className="offsite-latest" data-testid="offsite-latest">
            <Icon name={v.hasCopy ? 'ph ph-hard-drives' : 'ph ph-hard-drive'} size={14} />
            <span>{v.headline}</span>
          </p>

          {v.warning ? (
            <div className="offsite-warn" data-tone={v.tone} role="note" data-testid="offsite-warning">
              <Icon name="ph ph-warning" size={14} />
              <span>{v.warning}</span>
            </div>
          ) : null}

          {v.error ? (
            <div className="offsite-error" data-testid="offsite-error">
              <span>{v.error.text}</span>
              <details className="tech-detail">
                <summary>Chi tiết kỹ thuật</summary>
                <code className="mono">{v.error.code}</code>
              </details>
            </div>
          ) : null}

          <dl className="offsite-meta">
            <div>
              <dt>Nơi lưu</dt>
              <dd className={v.dest ? 'mono' : undefined}>{v.dest || 'Chưa chọn'}</dd>
            </div>
            <div>
              <dt>Lịch</dt>
              <dd>{v.schedule}</dd>
            </div>
            {v.keyId ? (
              <div>
                <dt>Khoá khôi phục</dt>
                <dd>
                  mã <span className="mono">{v.keyId}</span>
                </dd>
              </div>
            ) : null}
          </dl>

          {runNow.isError ? (
            runCmd ? (
              <CommandBlock command={runCmd} intro={`${offsiteApiErrorText(runNow.error) ?? errorText(runNow.error)} ${MANUAL_COMMAND_INTRO}`} />
            ) : (
              <ErrorBlock error={runNow.error} />
            )
          ) : null}

          {isOwner ? (
            <div className="offsite-actions">
              <Button variant="secondary" className="btn-30" icon="ph ph-folder-open" data-gen-target="system.offsite.choose" disabled={busy} onClick={() => setChoosing(true)}>
                Chọn nơi lưu bản sao ngoài máy
              </Button>
              <Button
                variant="secondary"
                className="btn-30"
                icon="ph ph-download-simple"
                data-gen-target="system.offsite.portable"
                disabled={keyMissing || preparing}
                loading={preparing}
                title={keyMissing ? OFFSITE_KEY_MISSING_TEXT : preparing ? 'Đang chuẩn bị gói mang đi' : undefined}
                onClick={() => {
                  setPortableError(null);
                  setPortableOpen(true);
                }}
              >
                Tải gói mang đi
              </Button>
              <Button
                variant="secondary"
                className="btn-30"
                icon="ph ph-key"
                data-gen-target="system.offsite.kit"
                disabled={keyMissing}
                title={keyMissing ? OFFSITE_KEY_MISSING_TEXT : undefined}
                onClick={() => setKitOpen(true)}
              >
                Bộ khôi phục
              </Button>
            </div>
          ) : null}
          {isOwner && keyMissing ? (
            <p className="muted-note offsite-note" data-testid="offsite-key-missing">
              {OFFSITE_KEY_MISSING_TEXT}
            </p>
          ) : null}
          {isOwner && preparing ? (
            <div className="offsite-req offsite-req--waiting" role="status" aria-live="polite" data-testid="offsite-portable-preparing">
              <Icon name="ph ph-circle-notch" size={13} className="spin" />{' '}
              <span>
                Đang chuẩn bị gói mang đi (có thể tới 30 phút) — đừng tải lại hay đóng trang cho tới khi trình duyệt bắt đầu tải.
              </span>{' '}
              <Button variant="ghost" className="btn-27" onClick={clearPortablePreparing}>
                Trình duyệt đã bắt đầu tải
              </Button>
            </div>
          ) : null}
          {isOwner && portableError ? (
            <div className="offsite-error" role="alert" data-testid="offsite-portable-error">
              <span>{portableError.text}</span>
              {portableError.code ? (
                <details className="tech-detail">
                  <summary>Chi tiết kỹ thuật</summary>
                  <code className="mono">{portableError.code}</code>
                </details>
              ) : null}
            </div>
          ) : null}
          {isOwner && !keyMissing ? <p className="muted-note offsite-note">Gói mang đi đã mã hoá — mở bằng Bộ khôi phục.</p> : null}
        </>
      ) : null}

      {choosing && d ? (
        <ChooseDestinationDialog
          current={d.dest ?? ''}
          onClose={() => setChoosing(false)}
          onSaved={(page) => {
            queryClient.setQueryData(OFFSITE_KEY, page);
            setChoosing(false);
            toast('Đã gửi nơi lưu cho máy chủ.', 'ok');
          }}
        />
      ) : null}
      {kitOpen ? <RecoveryKitDialog onClose={() => setKitOpen(false)} /> : null}
      {portableOpen ? <PortableDialog onClose={() => setPortableOpen(false)} onError={setPortableError} /> : null}
    </Panel>
  );
}

function ChooseDestinationDialog({ current, onClose, onSaved }: { current: string; onClose: () => void; onSaved: (p: OffsiteState) => void }) {
  const [path, setPath] = useState(current);
  const save = useMutation({
    mutationFn: () => api.offsite.setDestination({ path: path.trim() }),
    onSuccess: onSaved,
  });
  const err = save.error;
  const fieldError = err instanceof ApiError && err.status === 422 ? (err.fieldErrors.path ?? null) : null;
  const cmd = manualCommandOf(err);
  return (
    <Dialog
      open
      onClose={onClose}
      width={520}
      title="Chọn nơi lưu bản sao ngoài máy"
      kicker="Đường dẫn trên MÁY CHỦ — không phải máy đang mở Console"
      actions={
        <>
          <Button variant="secondary" onClick={onClose}>
            Huỷ
          </Button>
          <Button variant="primary" icon="ph ph-floppy-disk" disabled={!path.trim()} loading={save.isPending} onClick={() => save.mutate()}>
            Lưu
          </Button>
        </>
      }
    >
      <p className="offsite-dialog__lead">Ổ phải đang cắm/đã mount; hệ thống không ghi vào ổ chính.</p>
      <TextField
        label="Đường dẫn ổ USB/NAS trên máy chủ"
        value={path}
        onChange={(e) => setPath(e.target.value)}
        placeholder="/media/sep/USB"
        autoComplete="off"
        spellCheck={false}
        autoFocus
        error={typeof fieldError === 'string' ? fieldError : null}
      />
      <ul className="offsite-hints">
        {OFFSITE_PATH_HINTS.map((h) => (
          <li key={h.os}>
            <span className="offsite-hints__os">{h.os}:</span> <code className="mono">{h.example}</code>
          </li>
        ))}
      </ul>
      <p className="muted-note">Cần mã PIN. Máy chủ nhận yêu cầu trong khoảng 1 phút rồi kiểm ổ và chạy bản sao đầu tiên.</p>
      {save.isError && !fieldError ? (
        cmd ? (
          <CommandBlock command={cmd} intro={`${offsiteApiErrorText(err) ?? errorText(err)} ${MANUAL_COMMAND_INTRO}`} />
        ) : (
          <ErrorBlock error={err} />
        )
      ) : null}
    </Dialog>
  );
}

function PortableDialog({ onClose, onError }: { onClose: () => void; onError: (e: PortableError) => void }) {
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const start = async () => {
    setPending(true);
    setError(null);
    try {
      if (!(await hasPinSession())) {
        const ok = await usePinStore.getState().request();
        if (!ok) {
          setError('Chưa tải — cần nhập mã PIN.');
          return;
        }
      }
      let retried = false;
      const go = (): boolean =>
        startPortableDownload(api.offsite.portableUrl, (code, title) => {
          if (code === 'PIN_REQUIRED' && !retried) {
            retried = true;
            void usePinStore
              .getState()
              .request()
              .then((ok) => (ok ? go() : onError({ text: 'Chưa tải — cần nhập mã PIN.', code: '' })));
            return;
          }
          const friendly = offsiteApiErrorText(new ApiError(409, { code, title })) ?? (title || 'Chưa tải được gói mang đi.');
          onError({ text: friendly, code });
        });
      if (!go()) {
        setError('Đang chuẩn bị một gói mang đi — chờ trình duyệt bắt đầu tải.');
        return;
      }
      toast('Đang chuẩn bị gói mang đi — gói lớn có thể mất tới 30 phút mới bắt đầu tải.', 'ok');
      onClose();
    } finally {
      setPending(false);
    }
  };

  return (
    <Dialog
      open
      onClose={onClose}
      width={480}
      title="Tải gói mang đi?"
      kicker="Toàn bộ dữ liệu trong một tệp .ghbundle đã mã hoá"
      actions={
        <>
          <Button variant="secondary" onClick={onClose}>
            Huỷ
          </Button>
          <Button variant="primary" icon="ph ph-download-simple" loading={pending} onClick={() => void start()}>
            Tải về
          </Button>
        </>
      }
    >
      <ul className="upd-confirm">
        <li>Gói được mã hoá bằng Khoá khôi phục — mở bằng Bộ khôi phục.</li>
        <li>Máy chủ có thể mất tới 30 phút chuẩn bị gói. Đừng tải lại hay đóng trang cho tới khi trình duyệt bắt đầu tải; sau đó trình duyệt tự lưu vào thư mục Tải về.</li>
        <li>Cần mã PIN. Cất gói và Bộ khôi phục ở hai nơi khác nhau.</li>
      </ul>
      {error ? <InlineError>{error}</InlineError> : null}
    </Dialog>
  );
}
