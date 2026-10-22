import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import type { GroupSupportBody, GroupSupportView, SupportLimits } from '@gen-harness/contracts';
import { Button, Dialog, Icon, Switch, TextField } from '@gen-harness/ui';
import { api } from '../../lib/api';
import { errorDetail } from '../../lib/errorText';
import { fmtDMClock } from '../../lib/format';
import { useOrgTimezone } from '../../lib/permissions';
import { toast } from '../../lib/toast';
import { CardError, InlineError, SkeletonLines } from '../common';
import {
  GROUP_SUPPORT_KEY,
  SUPPORT_HOURS_OPTIONS,
  SUPPORT_LIMIT_FIELDS,
  SUPPORT_NOTE,
  addedByLabel,
  groupSupportKey,
  ownerErrorText,
  ownerChannelKey,
  parseLimit,
} from '../connections/ownerChannelModel';

/**
 * v0.1.72 — Danh bạ nhóm › «Chế độ hỗ trợ» của MỘT nhóm (CHỈ Owner). Bật cần: kênh chạy bằng tài khoản riêng của bot,
 * bot còn trong nhóm, nhóm đang nghe, nhóm do Sếp thêm bot hoặc Sếp đã duyệt nghe — chưa đủ thì công tắc khoá và hiện lý do
 * máy chủ gửi (`blocked_reasons`). Bật/tắt cần mã PIN (client tự mở hộp PIN khi 423). Tắt luôn được.
 */
export function GroupSupportDialog({ groupId, groupName, onClose }: { groupId: string; groupName: string; onClose: () => void }) {
  const q = useQuery({ queryKey: groupSupportKey(groupId), queryFn: ({ signal }) => api.ownerChannel.getSupport(groupId, signal) });
  return (
    <Dialog open onClose={onClose} width={500} title="Chế độ hỗ trợ" kicker={groupName}>
      {q.isPending ? (
        <SkeletonLines rows={4} />
      ) : q.isError ? (
        <CardError error={q.error} onRetry={() => void q.refetch()} retrying={q.isFetching} />
      ) : (
        <SupportBody view={q.data} />
      )}
    </Dialog>
  );
}

function SupportBody({ view }: { view: GroupSupportView }) {
  const qc = useQueryClient();
  const tz = useOrgTimezone();
  const [hours, setHours] = useState(24);
  const [raw, setRaw] = useState<Record<string, string>>(() =>
    Object.fromEntries(SUPPORT_LIMIT_FIELDS.map((f) => [f.key, String(view.limits?.[f.key] ?? '')])),
  );
  const [fieldErr, setFieldErr] = useState<Record<string, string>>({});
  const save = useMutation({
    mutationFn: (body: GroupSupportBody) => api.ownerChannel.setSupport(view.group.id, body),
    onSuccess: (next) => {
      qc.setQueryData(groupSupportKey(view.group.id), next);
      void qc.invalidateQueries({ queryKey: GROUP_SUPPORT_KEY });
      void qc.invalidateQueries({ queryKey: ownerChannelKey(view.group.channel) });
      toast(next.enabled ? `Đã bật chế độ hỗ trợ cho ${view.group.name}` : `Đã tắt chế độ hỗ trợ của ${view.group.name}`, 'ok');
    },
  });

  const turnOn = () => {
    const limits: Partial<SupportLimits> = {};
    const errs: Record<string, string> = {};
    for (const f of SUPPORT_LIMIT_FIELDS) {
      const n = parseLimit(raw[f.key] ?? '', f.max);
      if (n === null) errs[f.key] = `Nhập số từ 0 đến ${f.max}.`;
      else if (n !== view.limits?.[f.key]) limits[f.key] = n;
    }
    setFieldErr(errs);
    if (Object.keys(errs).length) return;
    save.mutate({ enabled: true, until_hours: hours || null, ...(Object.keys(limits).length ? { limits } : {}) });
  };

  const locked = !view.enabled && !view.can_enable;
  return (
    <div className="dlg-fields" data-testid="support-dialog">
      <p className="muted-note" data-testid="support-added-by">
        {`Ai thêm bot vào nhóm: ${addedByLabel(view.group.added_by_kind)}`}
      </p>
      <div className="triage-row">
        <Switch
          checked={view.enabled}
          label="Chế độ hỗ trợ"
          disabled={locked || save.isPending}
          onChange={(on) => (on ? turnOn() : save.mutate({ enabled: false }))}
        />
        <span data-testid="support-state">
          {view.enabled
            ? `Đang bật${view.since ? ` từ ${fmtDMClock(view.since, tz)}` : ''} · ${
                view.until_at ? `hết hạn ${fmtDMClock(view.until_at, tz)}` : 'không hết hạn'
              }`
            : 'Đang tắt — bot không trả lời thành viên trong nhóm này'}
        </span>
      </div>
      {locked ? (
        <div className="risk-box" role="note" data-testid="support-blocked">
          <Icon name="ph ph-lock" size={14} color="var(--color-warn)" />
          <div className="risk-box__text">
            <div>Chưa bật được vì:</div>
            <ul className="risk-list">
              {view.blocked_reasons.map((r) => (
                <li key={r}>{r}</li>
              ))}
            </ul>
          </div>
        </div>
      ) : null}
      {!view.enabled && view.can_enable ? (
        <>
          <label className="owner-ch__field" htmlFor="support-hours">
            <span>Tự tắt sau</span>
            <select id="support-hours" className="mini-select" value={hours} onChange={(e) => setHours(Number(e.target.value))}>
              {SUPPORT_HOURS_OPTIONS.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </select>
          </label>
          {SUPPORT_LIMIT_FIELDS.map((f) => (
            <TextField
              key={f.key}
              label={f.label}
              inputMode="numeric"
              value={raw[f.key] ?? ''}
              maxLength={5}
              error={fieldErr[f.key]}
              onChange={(e) => setRaw((p) => ({ ...p, [f.key]: e.target.value }))}
            />
          ))}
        </>
      ) : null}
      <p className="muted-note" role="note">
        <Icon name="ph ph-lifebuoy" size={12} /> {SUPPORT_NOTE} Bật/tắt cần mã PIN.
      </p>
      {save.isError ? <InlineError detail={errorDetail(save.error)}>{ownerErrorText(save.error)}</InlineError> : null}
      {!view.enabled && view.can_enable ? (
        <div className="conn-card__actions">
          <Button variant="primary" icon="ph ph-lifebuoy" loading={save.isPending} onClick={turnOn}>
            Bật chế độ hỗ trợ
          </Button>
        </div>
      ) : null}
    </div>
  );
}
