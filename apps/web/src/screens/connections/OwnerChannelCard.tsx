import { useEffect, useState } from 'react';
import { useMutation, useQuery } from '@tanstack/react-query';
import type { OwnerChannelView, OwnerPairStart, OwnerReportPrefs, ReportSeverity } from '@gen-harness/contracts';
import { ApiError } from '@gen-harness/contracts';
import { Button, Dialog, Icon, Switch, TextField } from '@gen-harness/ui';
import { api } from '../../lib/api';
import { errorDetail } from '../../lib/errorText';
import { fmtDMClock } from '../../lib/format';
import { useOrgTimezone } from '../../lib/permissions';
import { queryClient } from '../../lib/queryClient';
import { toast } from '../../lib/toast';
import { useNow } from '../../lib/useNow';
import { Bar, CardError, InlineError, Panel, SkeletonLines } from '../common';
import { ConnectionStatusPill } from './ConnectionStatusPill';
import type { ConnStatus } from './connectionsModel';
import {
  HOUR_OPTIONS,
  ONLY_SPECIAL_NOTE,
  OWNER_CHANNEL_TITLE,
  SEVERITY_OPTIONS,
  asOwnerChannelView,
  channelName,
  ownerChannelKey,
  ownerErrorText,
  ownerPairPollMs,
  ownerStatusText,
  pairCountdown,
  pairInstruction,
  parseMaxPerHour,
  quietText,
  spacedCode,
} from './ownerChannelModel';

/**
 * v0.1.72 — Kết nối › «Kênh quản trị» cho Zalo/WhatsApp (khuôn TelegramCard), CHỈ Owner. Kênh chạy bằng tài khoản riêng
 * của bot: Sếp lấy mã ghép 6 số (PIN, sống 10 phút) rồi nhắn mã đó cho bot từ tài khoản của mình — bot nhận ra Sếp theo ID
 * tài khoản. Đã ghép: ID che + Huỷ ghép (PIN), công tắc bật/tắt (PIN), cài đặt báo cáo chủ động CHỈ gồm mức tối thiểu, giờ
 * yên lặng, số tin/giờ — bot chỉ nhắn Sếp khi có việc đặc biệt (không có báo cáo định kỳ). Mã ghép chỉ ở state của thẻ.
 */
export function OwnerChannelCard({ ch }: { ch: string }) {
  const q = useQuery({
    queryKey: ownerChannelKey(ch),
    queryFn: ({ signal }) => api.ownerChannel.get(ch, signal),
    refetchInterval: (query) => ownerPairPollMs(asOwnerChannelView(query.state.data)),
  });
  const v = asOwnerChannelView(q.data);
  const name = channelName(ch);
  // Máy chủ cũ chưa có API kênh quản trị (404) ⇒ không hiện thẻ.
  if (q.error instanceof ApiError && q.error.status === 404) return null;
  const status: ConnStatus | null = v ? (v.paired && v.enabled ? 'running' : v.bot_account ? 'needs_boss' : 'not_connected') : null;
  return (
    <Panel
      title={`${OWNER_CHANNEL_TITLE} ${name}`}
      label={`${OWNER_CHANNEL_TITLE} ${name}`}
      kicker={`Sếp nhắn bot ${name} 1-1 · bot chỉ nhắn Sếp khi có việc đặc biệt`}
      aside={status ? <ConnectionStatusPill status={status} /> : undefined}
      bodyClass="conn-card__body"
    >
      {q.isPending ? (
        <SkeletonLines rows={2} padding="0" />
      ) : q.isError ? (
        <CardError error={q.error} onRetry={() => void q.refetch()} retrying={q.isFetching} />
      ) : v ? (
        <OwnerChannelBody ch={ch} v={v} />
      ) : null}
    </Panel>
  );
}

function applyView(ch: string, v: OwnerChannelView) {
  queryClient.setQueryData(ownerChannelKey(ch), v);
  void queryClient.invalidateQueries({ queryKey: ownerChannelKey(ch) });
}

function OwnerChannelBody({ ch, v }: { ch: string; v: OwnerChannelView }) {
  const tz = useOrgTimezone();
  const [pair, setPair] = useState<OwnerPairStart['pair'] | null>(null);
  const [confirmUnpair, setConfirmUnpair] = useState(false);
  const start = useMutation({
    mutationFn: () => api.ownerChannel.pairStart(ch),
    onSuccess: (r) => {
      const { pair: p, ...view } = r; // mã ghép chỉ ở state thẻ — không vào cache react-query
      setPair(p);
      applyView(ch, view);
    },
  });
  const toggle = useMutation({
    mutationFn: (enabled: boolean) => api.ownerChannel.update(ch, { enabled }),
    onSuccess: (r) => applyView(ch, r),
  });
  // Đã ghép xong ⇒ bỏ mã khỏi màn hình.
  useEffect(() => {
    if (v.paired) setPair(null);
  }, [v.paired]);
  const showCode = pair !== null && !v.paired;
  const err = toggle.error ?? start.error;
  return (
    <div className="owner-ch" data-testid={`owner-channel-${ch}`} role="group" aria-label={`${OWNER_CHANNEL_TITLE} ${channelName(ch)}`}>
      <p className="muted-note" data-testid="owner-status">
        {ownerStatusText(v, (iso) => fmtDMClock(iso, tz))}
      </p>
      {v.bot_account ? (
        <div className="triage-row">
          <Switch
            checked={v.enabled}
            label={`Bật ${OWNER_CHANNEL_TITLE}`}
            disabled={!v.paired || toggle.isPending}
            onChange={(on) => toggle.mutate(on)}
          />
          <span>{`Bật ${OWNER_CHANNEL_TITLE}`}</span>
        </div>
      ) : null}
      {showCode && pair ? <PairCode ch={ch} pair={pair} onExpired={() => setPair(null)} /> : null}
      {v.bot_account ? (
        <div className="conn-card__actions">
          <Button variant="secondary" className="btn-27" icon="ph ph-key" loading={start.isPending} onClick={() => start.mutate()}>
            {v.paired ? 'Ghép lại' : showCode ? 'Lấy mã mới' : 'Lấy mã ghép'}
          </Button>
          {v.paired ? (
            <Button variant="ghost" className="btn-27" icon="ph ph-link-break" onClick={() => setConfirmUnpair(true)}>
              Huỷ ghép
            </Button>
          ) : null}
        </div>
      ) : null}
      {err ? <InlineError detail={errorDetail(err)}>{ownerErrorText(err)}</InlineError> : null}
      {v.bot_account ? <ReportPrefsForm ch={ch} prefs={v.report_prefs} /> : null}
      {confirmUnpair ? <UnpairDialog ch={ch} v={v} onClose={() => setConfirmUnpair(false)} onDone={() => setPair(null)} /> : null}
    </div>
  );
}

/** Mã 6 số + đếm ngược 10 phút + câu «Từ Zalo của Sếp nhắn cho bot: 123456». Hết giờ ⇒ báo, gợi ý lấy mã mới. */
function PairCode({ ch, pair, onExpired }: { ch: string; pair: OwnerPairStart['pair']; onExpired: () => void }) {
  const now = useNow(1000);
  const left = pairCountdown(pair.expires_at, now);
  const expired = left.ms <= 0;
  if (expired) {
    return (
      <div className="owner-ch__pair owner-ch__pair--expired" role="status" data-testid="owner-pair-expired">
        <span>Mã ghép đã hết hạn — bấm «Lấy mã mới».</span>
        <Button variant="ghost" size="sm" onClick={onExpired}>
          Đóng
        </Button>
      </div>
    );
  }
  return (
    <div className="owner-ch__pair" role="status" data-testid="owner-pair">
      <div className="owner-ch__code mono" data-testid="owner-pair-code">
        {spacedCode(pair.code)}
      </div>
      <p className="owner-ch__instr" data-testid="owner-pair-instruction">
        {pairInstruction(ch, pair.code)}
      </p>
      <div className="qr-count">
        <Bar pct={left.pct} tone="var(--color-warn)" height={4} />
        <span className="qr-count__val" aria-live="polite" aria-atomic="true" data-testid="owner-pair-countdown">
          {`còn ${left.label}`}
        </span>
      </div>
      <p className="muted-note">Mã sống 10 phút, dùng một lần. Chỉ nhắn đúng 6 số, không gửi mã cho ai khác.</p>
    </div>
  );
}

function UnpairDialog({ ch, v, onClose, onDone }: { ch: string; v: OwnerChannelView; onClose: () => void; onDone: () => void }) {
  const del = useMutation({
    mutationFn: () => api.ownerChannel.unpair(ch),
    onSuccess: (r) => {
      applyView(ch, r);
      toast('Đã huỷ ghép kênh quản trị.', 'ok');
      onDone();
      onClose();
    },
  });
  return (
    <Dialog
      open
      onClose={onClose}
      width={440}
      title={`Huỷ ghép ${OWNER_CHANNEL_TITLE} ${channelName(ch)}?`}
      kicker="Cần mã PIN"
      actions={
        <>
          <Button variant="secondary" onClick={onClose}>
            Giữ lại
          </Button>
          <Button variant="primary" loading={del.isPending} onClick={() => del.mutate()}>
            Huỷ ghép
          </Button>
        </>
      }
    >
      <p className="help-text">
        {`Bot thôi nhận lệnh và thôi nhắn Sếp qua ${channelName(ch)}. Kênh quản trị tắt${
          v.support_groups > 0 ? ` và ${v.support_groups} nhóm đang hỗ trợ sẽ tắt chế độ hỗ trợ` : ' cùng mọi chế độ hỗ trợ của kênh'
        }. Muốn dùng lại, Sếp lấy mã ghép mới.`}
      </p>
      {del.isError ? <InlineError detail={errorDetail(del.error)}>{ownerErrorText(del.error)}</InlineError> : null}
    </Dialog>
  );
}

/** Báo cáo chủ động: CHỈ mức tối thiểu, giờ yên lặng, số tin/giờ (không có tóm tắt/gộp — QĐ Sếp 18/10). Lưu cần PIN. */
function ReportPrefsForm({ ch, prefs }: { ch: string; prefs: OwnerReportPrefs }) {
  const [sev, setSev] = useState<ReportSeverity>(prefs.min_severity);
  const [qs, setQs] = useState(prefs.quiet_start);
  const [qe, setQe] = useState(prefs.quiet_end);
  const [maxRaw, setMaxRaw] = useState(String(prefs.max_per_hour));
  const [maxErr, setMaxErr] = useState<string | null>(null);
  const save = useMutation({
    mutationFn: (p: OwnerReportPrefs) => api.ownerChannel.update(ch, { report_prefs: p }),
    onSuccess: (r) => {
      applyView(ch, r);
      toast('Đã lưu cài đặt báo cáo.', 'ok');
    },
  });
  const dirty = sev !== prefs.min_severity || qs !== prefs.quiet_start || qe !== prefs.quiet_end || maxRaw.trim() !== String(prefs.max_per_hour);
  const submit = () => {
    const n = parseMaxPerHour(maxRaw);
    if (n === null) {
      setMaxErr('Nhập số từ 1 đến 60.');
      return;
    }
    setMaxErr(null);
    save.mutate({ min_severity: sev, quiet_start: qs, quiet_end: qe, max_per_hour: n });
  };
  const id = `owner-prefs-${ch}`;
  return (
    <div className="owner-ch__prefs" data-testid="owner-prefs" role="group" aria-label="Báo cáo chủ động">
      <div className="gh-card__kicker">Báo cáo chủ động</div>
      <p className="owner-ch__only" role="note" data-testid="owner-only-special">
        <Icon name="ph ph-bell-ringing" size={12} /> {ONLY_SPECIAL_NOTE}
      </p>
      <label className="owner-ch__field" htmlFor={`${id}-sev`}>
        <span>Mức tối thiểu để bot nhắn</span>
        <select id={`${id}-sev`} className="mini-select" value={sev} onChange={(e) => setSev(e.target.value as ReportSeverity)}>
          {SEVERITY_OPTIONS.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </select>
      </label>
      <div className="owner-ch__field">
        <span>Giờ yên lặng</span>
        <span className="owner-ch__quiet">
          <select aria-label="Giờ yên lặng bắt đầu" className="mini-select" value={qs} onChange={(e) => setQs(Number(e.target.value))}>
            {HOUR_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>
          <span aria-hidden>→</span>
          <select aria-label="Giờ yên lặng kết thúc" className="mini-select" value={qe} onChange={(e) => setQe(Number(e.target.value))}>
            {HOUR_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>
        </span>
      </div>
      <p className="muted-note" data-testid="owner-quiet-text">
        {quietText({ quiet_start: qs, quiet_end: qe })}
      </p>
      <TextField
        label="Số tin tối đa mỗi giờ (việc khẩn không tính)"
        inputMode="numeric"
        value={maxRaw}
        maxLength={2}
        error={maxErr ?? undefined}
        onChange={(e) => setMaxRaw(e.target.value)}
      />
      <div className="conn-card__actions">
        <Button variant="secondary" className="btn-27" icon="ph ph-floppy-disk" disabled={!dirty} loading={save.isPending} onClick={submit}>
          Lưu cài đặt báo cáo
        </Button>
      </div>
      {save.isError ? <InlineError detail={errorDetail(save.error)}>{ownerErrorText(save.error)}</InlineError> : null}
    </div>
  );
}
