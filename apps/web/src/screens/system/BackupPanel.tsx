import { useEffect, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useSearchParams } from 'react-router-dom';
import { ApiError, type BackupItem, type BackupSchedule, type BackupsPage } from '@gen-harness/contracts';
import { Button, Dialog, EmptyState, Icon, Segmented, TextField } from '@gen-harness/ui';
import { DefaultControls } from '../../defaults/ResetButton';
import { api } from '../../lib/api';
import { downloadBlob } from '../../lib/download';
import { errorText } from '../../lib/errorText';
import { fmtDM, fmtHM } from '../../lib/format';
import { useCan, useOrgTimezone } from '../../lib/permissions';
import { useMe } from '../../lib/queries';
import { toast } from '../../lib/toast';
import { CardError, InlineError, Panel, SkeletonLines } from '../common';
import { fmtBytes } from '../relations/relationsModel';
import {
  BACKUPS_KEY,
  ENABLE_COMMAND,
  FREQUENCY_OPTIONS,
  RESTORE_CONFIRM_TEXT,
  TIME_RE,
  TRIGGER_ICON,
  downloadName,
  jobView,
  restoreView,
  scheduleText,
  triggerLabel,
} from './backupModel';

const when = (iso: string, tz: string) => `${fmtDM(iso, tz)} ${fmtHM(iso, tz)}`;

/**
 * Sao lưu & khôi phục (v0.1.20, gh/system_api/backups.py): danh sách bản sao lưu, "Sao lưu ngay" (worker), tải về
 * và khôi phục (chỉ Owner, cần PIN), lịch tự động. Khôi phục do genh trên máy chủ làm (api dừng tạm) — trong lúc
 * đó lỗi mạng khi hỏi trạng thái là "đang khởi động lại"; xong thì tự tải lại trang.
 */
export function BackupPanel() {
  const canManage = useCan('system.manage');
  const me = useMe();
  const isOwner = me.data?.role?.code === 'owner';
  const tz = useOrgTimezone();
  const [waiting, setWaiting] = useState<string | null>(null);
  const [target, setTarget] = useState<BackupItem | null>(null);
  const reloaded = useRef(false);
  const jobWatched = useRef(false);
  const queryClient = useQueryClient();

  const q = useQuery({
    queryKey: BACKUPS_KEY,
    queryFn: ({ signal }) => api.backups.list(signal),
    enabled: canManage,
    retry: waiting ? false : 1,
    refetchInterval: (query) => {
      const d = query.state.data;
      const busyJob = d?.job?.state === 'queued' || d?.job?.state === 'running';
      const busyRestore = d?.restore.state === 'requested' || d?.restore.state === 'running';
      return waiting || busyRestore ? 4000 : busyJob ? 2000 : false;
    },
  });

  // v0.1.36 (F-6): dải "Cần Sếp xử lý" (backup.stale → `/system?tab=storage&focus=backup`) — mục Sao lưu nằm dưới
  // thẻ Sức khoẻ và thẻ Cập nhật, nên cuộn tới và đặt con trỏ vào nút "Sao lưu ngay" (nút hứa gì, trang đích có nấy).
  const [params] = useSearchParams();
  const focusBackup = params.get('focus') === 'backup';
  const focused = useRef(false);
  useEffect(() => {
    if (!focusBackup || focused.current || !q.data) return;
    focused.current = true;
    const panel = document.querySelector<HTMLElement>('[data-gen-target="system.backup.panel"]');
    panel?.scrollIntoView?.({ block: 'start' });
    panel?.querySelector<HTMLElement>('[data-gen-target="system.backup.now"]')?.focus({ preventScroll: true });
  }, [focusBackup, q.data]);

  const runNow = useMutation({
    mutationFn: () => api.backups.runNow(),
    onSuccess: (d) => {
      queryClient.setQueryData(BACKUPS_KEY, d);
      jobWatched.current = true;
    },
  });

  // "Sao lưu ngay" xong → báo một lần.
  const jobState = q.data?.job?.state;
  useEffect(() => {
    if (!jobWatched.current) return;
    if (jobState === 'done') {
      jobWatched.current = false;
      toast('Đã sao lưu xong.', 'ok');
    } else if (jobState === 'failed') {
      jobWatched.current = false;
    }
  }, [jobState]);

  const offline = !!waiting && q.isError && q.error instanceof ApiError && (q.error.status === 0 || q.error.status >= 500);
  const rv = restoreView(q.data?.restore, { waiting: !!waiting, offline, label: waiting ?? undefined });

  useEffect(() => {
    if (rv.kind === 'finished' && !reloaded.current) {
      reloaded.current = true;
      window.setTimeout(() => window.location.reload(), 1500);
    }
  }, [rv.kind]);

  if (!canManage) {
    return (
      <Panel title="Sao lưu & khôi phục" label="Sao lưu & khôi phục" bodyClass="bk">
        <p className="muted-note">Cần quyền quản lý hệ thống để xem và chạy sao lưu.</p>
      </Panel>
    );
  }

  const d = q.data as BackupsPage | undefined;
  const busyJob = d?.job?.state === 'queued' || d?.job?.state === 'running';
  const restoring = rv.kind === 'working' || rv.kind === 'finished';
  const jv = jobView(d?.job);

  return (
    <Panel
      title="Sao lưu & khôi phục"
      genTarget="system.backup.panel"
      kicker={d ? `${scheduleText(d.schedule)} · mã hoá, lưu ngay trong máy chủ` : 'Bản sao lưu CSDL đã mã hoá'}
      label="Sao lưu & khôi phục"
      bodyClass="bk"
      aside={
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
          {/* v0.1.55: lịch sao lưu về mặc định (hằng ngày 02:00, giữ 7 bản) — chỉ Owner thấy chip + nút. */}
          {isOwner ? <DefaultControls itemKey="backup" /> : null}
          <Button
            variant="primary"
            icon="ph ph-floppy-disk"
            className="btn-30"
            data-gen-target="system.backup.now"
            loading={runNow.isPending || busyJob}
            disabled={!d || restoring}
            onClick={() => runNow.mutate()}
          >
            {busyJob ? 'Đang sao lưu…' : 'Sao lưu ngay'}
          </Button>
        </span>
      }
    >
      {rv.kind !== 'hidden' ? (
        <div className={`bk-restore bk-restore--${rv.tone}`} role="status" aria-live="polite">
          <div className="bk-restore__title">{rv.title}</div>
          {rv.steps.length ? (
            <ol className="upd-steps">
              {rv.steps.map((s) => (
                <li key={s.label} data-state={s.state}>
                  <Icon name={s.state === 'done' ? 'ph ph-check' : s.state === 'active' ? 'ph ph-circle-notch' : 'ph ph-circle'} size={13} />
                  {s.label}
                </li>
              ))}
            </ol>
          ) : null}
          {rv.body ? <p className="upd-body">{rv.body}</p> : null}
          {rv.technical ? (
            <details className="tech-detail">
              <summary>Chi tiết kỹ thuật</summary>
              <code>{rv.technical}</code>
            </details>
          ) : null}
          {rv.command ? <code className="mono bk-cmd">{rv.command}</code> : null}
        </div>
      ) : null}

      {q.isPending ? (
        <SkeletonLines rows={4} padding="4px 0" />
      ) : q.isError && !waiting ? (
        <CardError error={q.error} onRetry={() => void q.refetch()} retrying={q.isFetching} />
      ) : d ? (
        <>
          {jv.kind === 'working' ? (
            <p className="bk-job" role="status">
              <Icon name="ph ph-circle-notch" size={13} className="spin" /> {jv.text}
            </p>
          ) : jv.kind !== 'none' ? (
            <InlineError detail={jv.kind === 'failed' ? jv.technical : null}>{jv.text}</InlineError>
          ) : null}
          {runNow.isError ? <InlineError>{errorText(runNow.error)}</InlineError> : null}

          <ScheduleRow schedule={d.schedule} timezone={d.timezone} />

          {isOwner && !d.restore.can_request ? (
            // v0.1.28 (UX N4): lời thường cho Sếp — sao lưu vẫn chạy; nút Khôi phục cần một việc một lần trên máy chủ.
            <div className="upd-cmd" data-testid="restore-disabled">
              <span>
                Sao lưu vẫn tự chạy bình thường. Riêng nút <b>Khôi phục</b> chưa dùng được trên máy này vì máy chủ chưa bật trình
                khôi phục. Nhờ người cài đặt hệ thống chạy lệnh dưới đây một lần trên máy chủ (lệnh cập nhật này bật luôn khôi phục) —
                sau đó nút Khôi phục tự mở.
              </span>
              <code className="mono">{ENABLE_COMMAND}</code>
            </div>
          ) : null}

          {d.items.length === 0 ? (
            <EmptyState icon="ph ph-database" title="Chưa có bản sao lưu nào" description="Bấm Sao lưu ngay để tạo bản đầu tiên." />
          ) : (
            <div className="retention-wrap">
              <table className="retention-table bk-table">
                <thead>
                  <tr>
                    <th>Thời điểm</th>
                    <th>Nguồn</th>
                    <th>Dung lượng</th>
                    <th>Mã hoá</th>
                    {isOwner ? <th /> : null}
                  </tr>
                </thead>
                <tbody>
                  {d.items.map((b) => (
                    <BackupRow key={b.key} b={b} tz={tz} isOwner={isOwner} canRestore={d.restore.can_request && !restoring} onRestore={() => setTarget(b)} />
                  ))}
                </tbody>
              </table>
            </div>
          )}
          <p className="muted-note bk-note">
            Giữ tự động {d.retention.daily} bản theo ngày, {d.retention.weekly} theo tuần, {d.retention.monthly} theo tháng và mọi bản trong{' '}
            {d.retention.recent_hours} giờ qua. Tệp tải về đã mã hoá — chỉ khôi phục được trên máy chủ này.
          </p>
        </>
      ) : null}

      {target ? (
        <RestoreDialog
          item={target}
          tz={tz}
          onClose={() => setTarget(null)}
          onRequested={(page) => {
            queryClient.setQueryData(BACKUPS_KEY, page);
            setWaiting(when(target.taken_at, tz));
            setTarget(null);
          }}
        />
      ) : null}
    </Panel>
  );
}

function BackupRow({ b, tz, isOwner, canRestore, onRestore }: { b: BackupItem; tz: string; isOwner: boolean; canRestore: boolean; onRestore: () => void }) {
  const download = useMutation({
    mutationFn: () => api.backups.download(b.key),
    onSuccess: (blob) => downloadBlob(blob, downloadName(b.key)),
    onError: (e) => toast(errorText(e), 'bad'),
  });
  return (
    <tr>
      <td>
        <div className="retention-table__name">{when(b.taken_at, tz)}</div>
        <div className="mono retention-table__code">{b.key.replace('backups/', '')}</div>
      </td>
      <td>
        <span className="bk-trigger">
          <Icon name={b.trigger ? TRIGGER_ICON[b.trigger] : 'ph ph-question'} size={13} />
          {triggerLabel(b.trigger)}
        </span>
      </td>
      <td className="mono">{fmtBytes(b.size_bytes)}</td>
      <td>
        {b.encrypted ? (
          <span className="bk-enc" title={b.key_id === 'backup' ? 'Mã hoá bằng khoá sao lưu riêng' : 'Mã hoá bằng khoá chính'}>
            <Icon name="ph ph-lock-simple" size={12} /> Đã mã hoá
          </span>
        ) : (
          '—'
        )}
      </td>
      {isOwner ? (
        <td className="retention-table__actions">
          <Button variant="ghost" className="btn-27" icon="ph ph-download-simple" loading={download.isPending} onClick={() => download.mutate()} aria-label={`Tải về bản ${when(b.taken_at, tz)}`}>
            Tải về
          </Button>
          <Button variant="ghost" className="btn-27" icon="ph ph-clock-counter-clockwise" disabled={!canRestore} title={canRestore ? undefined : 'Máy chủ chưa bật khôi phục bằng nút bấm — xem hướng dẫn phía trên'} onClick={onRestore} aria-label={`Khôi phục bản ${when(b.taken_at, tz)}`}>
            Khôi phục
          </Button>
        </td>
      ) : null}
    </tr>
  );
}

function ScheduleRow({ schedule, timezone }: { schedule: BackupSchedule | null; timezone: string }) {
  const queryClient = useQueryClient();
  const [editing, setEditing] = useState(false);
  const [frequency, setFrequency] = useState<BackupSchedule['frequency']>(schedule?.frequency ?? 'daily');
  const [time, setTime] = useState(schedule?.time_of_day ?? '02:00');
  const save = useMutation({
    mutationFn: () => api.backups.schedule({ frequency, time_of_day: time }),
    onSuccess: (page) => {
      queryClient.setQueryData(BACKUPS_KEY, page);
      setEditing(false);
      toast('Đã lưu lịch sao lưu.', 'ok');
    },
  });
  const start = () => {
    setFrequency(schedule?.frequency ?? 'daily');
    setTime(schedule?.time_of_day ?? '02:00');
    save.reset();
    setEditing(true);
  };
  const timeInvalid = !TIME_RE.test(time);

  if (!editing) {
    return (
      <div className="bk-schedule" data-gen-target="system.backup.schedule">
        <Icon name="ph ph-calendar-check" size={14} />
        <span className="bk-schedule__text">
          Lịch tự động: <strong>{scheduleText(schedule)}</strong>
          {schedule ? <span className="bk-schedule__tz"> (giờ {timezone})</span> : null}
        </span>
        <Button variant="ghost" className="btn-27" icon="ph ph-pencil-simple" onClick={start}>
          {schedule ? 'Sửa lịch' : 'Đặt lịch'}
        </Button>
      </div>
    );
  }
  return (
    <div className="bk-schedule bk-schedule--edit">
      <div className="seg-field">
        <span className="seg-field__label">Tần suất</span>
        <Segmented label="Tần suất sao lưu" value={frequency} onChange={(v) => setFrequency(v)} options={FREQUENCY_OPTIONS} />
      </div>
      <TextField
        label="Giờ chạy (HH:MM)"
        value={time}
        onChange={(e) => setTime(e.target.value)}
        error={timeInvalid ? 'Giờ chạy dạng HH:MM (00:00–23:59)' : null}
        placeholder="02:00"
        maxLength={5}
      />
      <div className="bk-schedule__actions">
        <Button variant="ghost" className="btn-27" onClick={() => setEditing(false)}>
          Huỷ
        </Button>
        <Button variant="primary" className="btn-27" disabled={timeInvalid} loading={save.isPending} onClick={() => save.mutate()}>
          Lưu lịch
        </Button>
      </div>
      {save.isError ? <InlineError>{errorText(save.error)}</InlineError> : null}
    </div>
  );
}

function RestoreDialog({ item, tz, onClose, onRequested }: { item: BackupItem; tz: string; onClose: () => void; onRequested: (p: BackupsPage) => void }) {
  const [confirm, setConfirm] = useState('');
  const restore = useMutation({
    mutationFn: () => api.backups.restore(item.key, confirm.trim()),
    onSuccess: onRequested,
  });
  const ok = confirm.trim() === RESTORE_CONFIRM_TEXT;
  return (
    <Dialog
      open
      onClose={onClose}
      width={480}
      title={`Khôi phục bản sao lưu ${when(item.taken_at, tz)}?`}
      kicker={`${triggerLabel(item.trigger)} · ${fmtBytes(item.size_bytes)}`}
      actions={
        <>
          <Button variant="secondary" onClick={onClose}>
            Huỷ
          </Button>
          <Button variant="primary" icon="ph ph-clock-counter-clockwise" disabled={!ok} loading={restore.isPending} onClick={() => restore.mutate()}>
            Khôi phục
          </Button>
        </>
      }
    >
      <ul className="upd-confirm">
        <li>
          Toàn bộ dữ liệu hiện tại sẽ được <strong>thay bằng dữ liệu lúc {when(item.taken_at, tz)}</strong> — mọi thay đổi sau thời điểm đó mất.
        </li>
        <li>Hệ thống tự sao lưu trạng thái hiện tại trước (nguồn "Trước khi khôi phục") để quay lại được.</li>
        <li>Console tạm ngắt khoảng 2–5 phút rồi tự tải lại; lỗi giữa chừng thì tự quay về như cũ.</li>
      </ul>
      <div className="bk-confirm">
        <TextField
          label={`Gõ ${RESTORE_CONFIRM_TEXT} để xác nhận`}
          value={confirm}
          onChange={(e) => setConfirm(e.target.value)}
          placeholder={RESTORE_CONFIRM_TEXT}
          autoComplete="off"
          autoFocus
        />
      </div>
      {restore.isError ? <p className="upd-error">{errorText(restore.error)}</p> : null}
    </Dialog>
  );
}
