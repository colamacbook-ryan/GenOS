import { useState } from 'react';
import { useMutation } from '@tanstack/react-query';
import { ApiError, type Channel, type ChannelGroup, type NewGroupMode, type SettableListenMode } from '@gen-harness/contracts';
import { Button, Icon } from '@gen-harness/ui';
import { api } from '../../lib/api';
import { qk2, usePendingGroups } from '../../lib/dataQueries';
import { errorText } from '../../lib/errorText';
import { fmtInt } from '../../lib/format';
import { queryClient } from '../../lib/queryClient';
import { toast } from '../../lib/toast';
import { ErrorWithDetail } from '../../social/WriteProofDialog';
import { BRIDGE_OFFLINE_TEXT, BRIDGE_OFFLINE_TITLE, NEW_GROUP_MODES, isQrChannel, newGroupModeOf } from './systemModel';

/** Tối đa bấy nhiêu nhóm hiện thẳng trong thẻ; còn lại mở bảng nhóm đầy đủ. */
const SHOW_MAX = 4;

/** Hai lựa chọn của nhóm đang chờ (chỉ khi Sếp đặt «Nhóm mới: Hỏi Sếp»): Lắng nghe im lặng = lưu tin, không bao giờ trả lời · Không nghe = không lưu. */
const CHOICES: Array<{ mode: SettableListenMode; label: string; icon: string }> = [
  { mode: 'silent', label: 'Lắng nghe im lặng', icon: 'ph ph-eye' },
  { mode: 'off', label: 'Không nghe', icon: 'ph ph-bell-slash' },
];

/**
 * Hộp "Nhóm mới — chọn Lắng nghe im lặng/Không nghe": hiện khi có nhóm CHỜ chọn (`listen_mode = pending`) — nhóm đó phát sinh
 * khi Sếp đặt «Nhóm mới: Hỏi Sếp» (QD 17/10 — mặc định là tự vào Lắng nghe im lặng, không hỏi) và còn đó cả khi Sếp đổi
 * sang chế độ khác sau đó, cho tới khi hết nhóm chờ. Chưa chọn thì Gen không lưu tin nào của
 * nhóm đó; dù chọn gì, danh sách thành viên vẫn được ghi nhận (để hợp nhất danh tính, không lưu tin). Máy chủ cũ (không có
 * `new_group_mode`) vẫn hiện hộp khi có nhóm chờ.
 */
export function NewGroupsBox({ channel, canManage, onOpenAll }: { channel: Channel; canManage: boolean; onOpenAll: () => void }) {
  // Còn nhóm CHỜ chọn thì hộp vẫn hiện dù Sếp đã đổi «Nhóm mới» sang Lắng nghe im lặng / Không nghe (đổi chính sách chỉ áp
  // cho nhóm phát hiện SAU, không tự đổi nhóm đang chờ): ẩn hộp sẽ để các nhóm đó kẹt ở «Chờ Sếp chọn», không lưu tin.
  // Máy chủ cũ không gửi `groups_pending`: chỉ hiện khi còn đang «Hỏi Sếp» (hoặc chưa biết) như trước.
  const asks = channel.new_group_mode === undefined || channel.new_group_mode === 'ask';
  const hasPending = channel.groups_pending === undefined ? asks : channel.groups_pending > 0;
  const enabled = isQrChannel(channel) && channel.installed && channel.state === 'active' && hasPending;
  const pending = usePendingGroups(channel.type, enabled);
  const [failed, setFailed] = useState<unknown>(null);
  // F1d — ô «Áp dụng cho các nhóm mới sau này»: chỉ hiện khi máy chủ có `new_group_mode`. QD 17/10: KHÔNG còn bật sẵn — Sếp
  // đã chủ động chọn «Hỏi Sếp»; tick ô này mới đổi mặc định của nhóm sau (cần mã PIN).
  const canApply = channel.new_group_mode !== undefined;
  const [applyPick, setApplyPick] = useState<boolean>(false);
  const apply = canApply && applyPick;

  const choose = useMutation({
    mutationFn: async ({ ids, mode, applyToNew }: { ids: string[]; mode: SettableListenMode; applyToNew: boolean }) => {
      for (const id of ids) await api.channels.setGroupMode(channel.type, id, mode, applyToNew);
      return ids.length;
    },
    onMutate: (v) => {
      setFailed(null);
      setApplyPick(v.applyToNew); // giữ nguyên lựa chọn cho các nhóm còn lại (máy chủ đánh dấu «đã hỏi» sau lượt đầu)
    },
    onSuccess: (n, v) => {
      const what = v.mode === 'silent' ? 'Lắng nghe im lặng — lưu tin, không bao giờ trả lời' : 'Không nghe — không lưu tin';
      const later = v.applyToNew ? `. Nhóm mới sau này cũng vào ${v.mode === 'silent' ? 'Lắng nghe im lặng' : 'Không nghe'}` : '';
      toast(`${n === 1 ? 'Đã chọn' : `Đã chọn cho ${fmtInt(n)} nhóm`}: ${what}${later}`, 'neutral');
    },
    onError: (e) => setFailed(e),
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: qk2.channelGroups(channel.type) });
      void queryClient.invalidateQueries({ queryKey: qk2.channels });
      void queryClient.invalidateQueries({ queryKey: qk2.pipeline });
    },
  });

  // Phòng thủ: máy chủ lạ/cũ trả không phải mảng thì coi như không có nhóm chờ (không làm sập thẻ kênh).
  const rows: ChannelGroup[] = enabled && Array.isArray(pending.data) ? pending.data : [];
  if (rows.length === 0) return null;
  const shown = rows.slice(0, SHOW_MAX);
  const more = rows.length - shown.length;
  const busy = choose.isPending;
  return (
    <section className="ch-newgroups" aria-label={`Nhóm mới của ${channel.name} đang chờ chọn`} data-testid={`newgroups-${channel.type}`}>
      <div className="ch-newgroups__head">
        <Icon name="ph ph-users-three" size={16} />
        <div style={{ flex: 1, minWidth: 0 }}>
          <div className="ch-alert__title">
            Nhóm mới — chọn Lắng nghe im lặng hay Không nghe ({fmtInt(rows.length)})
          </div>
          <div className="ch-alert__text">
            Gen chưa lưu tin nào của các nhóm này cho tới khi Sếp chọn. <b>Lắng nghe im lặng</b>: lưu hết tin, không bao giờ trả lời (kể cả khi được tag). <b>Không nghe</b>: không lưu tin.
            Dù chọn gì, danh sách thành viên vẫn được ghi nhận để hợp nhất danh tính.
          </div>
        </div>
      </div>
      <ul className="ch-newgroups__list">
        {shown.map((g) => (
          <li key={g.id} className="ch-newgroups__row">
            <span className="ch-newgroups__name">
              {g.name}
              <span className="ch-newgroups__count">{fmtInt(g.members)} thành viên</span>
            </span>
            {canManage ? (
              <span className="ch-newgroups__btns">
                {CHOICES.map((c) => (
                  <Button
                    key={c.mode}
                    variant="secondary"
                    size="sm"
                    icon={c.icon}
                    disabled={busy}
                    aria-label={`${c.label} — nhóm ${g.name}`}
                    onClick={() => choose.mutate({ ids: [g.id], mode: c.mode, applyToNew: apply })}
                  >
                    {c.label}
                  </Button>
                ))}
              </span>
            ) : null}
          </li>
        ))}
      </ul>
      {canManage && canApply ? (
        <label className="gh-check ch-newgroups__apply" data-testid={`newgroups-apply-${channel.type}`}>
          <input type="checkbox" checked={apply} disabled={busy} onChange={(e) => setApplyPick(e.target.checked)} />
          <span>
            Áp dụng cho các nhóm mới sau này
            <span className="ch-newgroups__count">
              {' '}
              — nhóm phát hiện sau sẽ tự vào đúng chế độ Sếp chọn bây giờ (Lắng nghe im lặng hoặc Không nghe), không hỏi lại. Cần mã PIN.
            </span>
          </span>
        </label>
      ) : null}
      {more > 0 || (canManage && rows.length > 1) ? (
        <div className="ch-newgroups__foot">
          {more > 0 ? (
            <Button variant="ghost" size="sm" onClick={onOpenAll}>
              Xem cả {fmtInt(rows.length)} nhóm
            </Button>
          ) : null}
          {canManage && rows.length > 1 ? (
            <span className="ch-newgroups__btns">
              {CHOICES.map((c) => (
                <Button
                  key={c.mode}
                  variant="secondary"
                  size="sm"
                  icon={c.icon}
                  disabled={busy}
                  loading={busy && choose.variables?.mode === c.mode && choose.variables.ids.length > 1}
                  onClick={() => choose.mutate({ ids: rows.map((g) => g.id), mode: c.mode, applyToNew: apply })}
                >
                  Tất cả {fmtInt(rows.length)} nhóm: {c.label}
                </Button>
              ))}
            </span>
          ) : null}
        </div>
      ) : null}
      {failed ? <ErrorWithDetail className="write-error" error={failed} /> : null}
    </section>
  );
}

/**
 * Ô chỉnh «Nhóm mới» trên thẻ kênh (F1d): Lắng nghe im lặng (mặc định) / Hỏi Sếp / Không nghe — áp cho nhóm phát hiện SAU này,
 * không đổi nhóm đã có. Đổi cần mã PIN như đổi tin 1-1 (api client tự hỏi PIN rồi gửi lại). Máy chủ cũ không có `new_group_mode` ⇒ ẩn.
 */
export function NewGroupModeSelect({ channel, canManage }: { channel: Channel; canManage: boolean }) {
  const mode = newGroupModeOf(channel);
  const save = useMutation({
    mutationFn: (v: NewGroupMode) => api.channels.update(channel.type, { new_group_mode: v }),
    onSuccess: (next) => {
      queryClient.setQueryData<Channel[]>(qk2.channels, (old) => old?.map((x) => (x.type === channel.type ? next : x)));
      const m = newGroupModeOf(next);
      toast(
        m === 'silent'
          ? `${channel.name}: nhóm mới tự vào Lắng nghe im lặng — lưu hết tin, không bao giờ trả lời`
          : m === 'off'
            ? `${channel.name}: nhóm mới tự vào Không nghe — không lưu tin`
            : `${channel.name}: nhóm mới chờ Sếp chọn (Lắng nghe im lặng hay Không nghe)`,
        'neutral',
      );
    },
    onError: (e) => {
      void queryClient.invalidateQueries({ queryKey: qk2.channels });
      toast(errorText(e), 'bad');
    },
  });
  if (mode === undefined || !isQrChannel(channel) || !channel.installed) return null;
  return (
    <div className="ch-newmode" data-testid={`newmode-${channel.type}`}>
      <span className="ch-direct__label">
        Nhóm mới
        <span className="ch-direct__hint" data-testid={`newmode-hint-${channel.type}`}>
          {NEW_GROUP_MODES.find((d) => d.value === mode)?.hint} Chỉ áp cho nhóm phát hiện sau này. Đổi cần mã PIN.
        </span>
      </span>
      <select
        className="mini-select"
        aria-label={`Nhóm mới trên ${channel.name}`}
        value={mode}
        disabled={!canManage || save.isPending}
        onChange={(e) => save.mutate(e.target.value as NewGroupMode)}
      >
        {NEW_GROUP_MODES.map((d) => (
          <option key={d.value} value={d.value}>
            {d.label}
          </option>
        ))}
      </select>
    </div>
  );
}

/**
 * Khối nhẹ trên thẻ kênh (QD 17/10): «N nhóm mới đã vào Lắng nghe im lặng» — để Sếp biết Gen đang nghe những nhóm nào vừa
 * được add, dù không hỏi. «Đã biết» đặt lại bộ đếm (không cần PIN). Máy chủ cũ không gửi `new_groups_silent` ⇒ không hiện.
 */
export function NewSilentGroupsNotice({ channel, canManage, onOpenAll }: { channel: Channel; canManage: boolean; onOpenAll: () => void }) {
  const n = channel.new_groups_silent ?? 0;
  const seen = useMutation({
    mutationFn: () => api.channels.groupsSeen(channel.type),
    onSuccess: (next) => {
      queryClient.setQueryData<Channel[]>(qk2.channels, (old) => old?.map((x) => (x.type === channel.type ? next : x)));
    },
    onError: (e) => toast(errorText(e), 'bad'),
  });
  if (!isQrChannel(channel) || !channel.installed || n <= 0) return null;
  return (
    <section className="ch-newsilent" role="status" aria-label={`Nhóm mới của ${channel.name} đã vào Lắng nghe im lặng`} data-testid={`newsilent-${channel.type}`}>
      <Icon name="ph ph-bell-ringing" size={16} />
      <div style={{ flex: 1, minWidth: 0 }}>
        <div className="ch-alert__title">{fmtInt(n)} nhóm mới đã vào Lắng nghe im lặng</div>
        <div className="ch-alert__text">Gen lưu hết tin của các nhóm này nhưng không bao giờ trả lời (kể cả khi được tag). Muốn đổi, chọn chế độ khác cho từng nhóm.</div>
      </div>
      <span className="ch-newgroups__btns">
        <Button variant="ghost" size="sm" onClick={onOpenAll}>
          Xem nhóm
        </Button>
        {canManage ? (
          <Button variant="secondary" size="sm" loading={seen.isPending} onClick={() => seen.mutate()}>
            Đã biết
          </Button>
        ) : null}
      </span>
    </section>
  );
}

/** Tin nhắn thân thiện khi bấm "Cập nhật danh sách nhóm" không được (kèm "Chi tiết kỹ thuật" ở chỗ hiển thị). */
function refreshErrorText(e: unknown): string {
  if (e instanceof ApiError && e.code === 'BRIDGE_OFFLINE') return `${BRIDGE_OFFLINE_TITLE}. ${BRIDGE_OFFLINE_TEXT}`;
  return errorText(e);
}

/**
 * "Cập nhật danh sách nhóm" (F056): bảo bộ nhận tin quét lại ngay thay vì chờ chu kỳ — nhóm vừa vào hiện sau vài giây.
 * Lỗi hiện ngay dưới nút bằng câu thân thiện + "Chi tiết kỹ thuật", không phải toast mất đi.
 */
export function RefreshGroupsButton({ channel, size = 'sm' }: { channel: Pick<Channel, 'type' | 'name'>; size?: 'sm' | 'md' }) {
  const [error, setError] = useState<unknown>(null);
  const refresh = useMutation({
    mutationFn: () => api.channels.refreshGroups(channel.type),
    onMutate: () => setError(null),
    onSuccess: () => {
      toast(`Đang cập nhật danh sách nhóm ${channel.name} — vài giây nữa nhóm mới sẽ hiện`, 'neutral');
      // Bridge quét xong mới đẩy danh bạ; tải lại sau vài nhịp để Sếp thấy kết quả mà không cần bấm thêm.
      for (const ms of [4000, 12000]) {
        setTimeout(() => {
          void queryClient.invalidateQueries({ queryKey: qk2.channelGroups(channel.type) });
          void queryClient.invalidateQueries({ queryKey: qk2.channels });
        }, ms);
      }
    },
    onError: (e) => setError(e),
  });
  return (
    <>
      <Button
        variant="secondary"
        size={size}
        icon="ph ph-arrows-clockwise"
        loading={refresh.isPending}
        onClick={() => refresh.mutate()}
        data-testid={`refresh-groups-${channel.type}`}
      >
        Cập nhật danh sách nhóm
      </Button>
      {error ? <ErrorWithDetail className="write-error" text={refreshErrorText(error)} error={error} /> : null}
    </>
  );
}
