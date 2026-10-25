import { useState } from 'react';
import { Link } from 'react-router-dom';
import { Badge, Button, SelectField, Segmented, Switch } from '@gen-harness/ui';
import { errorDetail, errorText } from '../../lib/errorText';
import { useMe } from '../../lib/queries';
import { toast } from '../../lib/toast';
import { CardError, InlineError, Panel, SkeletonLines } from '../common';
import {
  useBulkContexts,
  useIntelConsole,
  useIntelSettings,
  useLockAssistant,
  useMuteKind,
  useUnlockAssistant,
  useUnlockToday,
  useUnmute,
  useVerifyPayee,
} from './intelQueries';
import { DEFENSIVE_KINDS, contextLabel, vnTime, type IntelConsole, type IntelMode, type IntelRamp } from './intelModel';

/**
 * v0.1.76 (T11, CNS-077): thẻ "Tình báo" tối thiểu — nơi DUY NHẤT trên Console làm các thao tác cần mã PIN của Tình báo: khoá / mở khoá
 * trợ lý, mở khoá hôm nay (cho «OK» gửi tin khách), chạy thật / chạy thử, ghép kênh phụ, tắt báo lừa đảo-an toàn, xác minh số tài khoản,
 * gán bối cảnh nhóm hàng loạt. Chỉ Owner thấy. Việc cần PIN: máy chủ trả 423 ⇒ hộp PIN chung tự mở rồi gửi lại (không tự dựng ô PIN).
 */
export function IntelCard() {
  const me = useMe();
  const isOwner = me.data?.role?.code === 'owner';
  const q = useIntelConsole(isOwner);
  if (!isOwner) return null;
  return (
    <Panel title="Tình báo" kicker="Thao tác cần mã PIN của Tình báo" label="Tình báo" bodyClass="intel-card">
      {q.isPending ? (
        <SkeletonLines rows={5} padding="0" />
      ) : q.isError ? (
        <CardError error={q.error} onRetry={() => void q.refetch()} retrying={q.isFetching} />
      ) : (
        <IntelBody s={q.data} />
      )}
    </Panel>
  );
}

function Row({ title, state, children, testId }: { title: string; state?: React.ReactNode; children?: React.ReactNode; testId?: string }) {
  return (
    <div className="intel-row" data-testid={testId}>
      <div className="intel-row__head">
        <span className="intel-row__title">{title}</span>
        {state}
      </div>
      {children ? <div className="intel-row__body">{children}</div> : null}
    </div>
  );
}

function Err({ e }: { e: unknown }) {
  return e ? <InlineError detail={errorDetail(e)}>{errorText(e)}</InlineError> : null;
}

function IntelBody({ s }: { s: IntelConsole }) {
  return (
    <>
      <AssistantRow s={s} />
      <UnlockTodayRow s={s} />
      <ModeRow s={s} />
      <BackupRow s={s} />
      <MutesRow s={s} />
      <PayeesRow s={s} />
      <GroupsRow s={s} />
    </>
  );
}

function AssistantRow({ s }: { s: IntelConsole }) {
  const lock = useLockAssistant();
  const unlock = useUnlockAssistant();
  const locked = s.assistant.locked;
  return (
    <Row
      title="Trợ lý"
      testId="intel-assistant"
      state={<Badge kind="status" tone={locked ? 'bad' : 'ok'}>{locked ? `Đang khoá${s.assistant.locked_at ? ` từ ${vnTime(s.assistant.locked_at)}` : ''}` : 'Đang hoạt động'}</Badge>}
    >
      <p className="muted-note">
        {locked
          ? 'Kênh quản trị và chế độ hỗ trợ nhóm đã tắt. Mở khoá chỉ gỡ cờ khoá — Sếp tự bật lại từng phần ở Kết nối.'
          : 'Khoá khi nghi ngờ tài khoản bị chiếm: tắt kênh quản trị, tắt hỗ trợ nhóm, huỷ tin đang chờ gửi. Khoá không cần mã PIN.'}
      </p>
      {locked ? (
        <Button
          variant="secondary"
          size="sm"
          loading={unlock.isPending}
          onClick={() => {
            if (window.confirm('Mở khoá trợ lý? Kênh quản trị và hỗ trợ nhóm vẫn tắt cho tới khi Sếp bật lại. Chỉ làm khi chắc tài khoản ghép an toàn.')) unlock.mutate(undefined, { onSuccess: () => toast('Đã mở khoá trợ lý') });
          }}
        >
          Mở khoá trợ lý (cần mã PIN)
        </Button>
      ) : (
        <Button variant="secondary" size="sm" loading={lock.isPending} onClick={() => lock.mutate(undefined, { onSuccess: () => toast('Đã khoá trợ lý', 'warn') })}>
          Khoá trợ lý
        </Button>
      )}
      <Err e={lock.error ?? unlock.error} />
    </Row>
  );
}

function UnlockTodayRow({ s }: { s: IntelConsole }) {
  const m = useUnlockToday();
  const until = vnTime(s.outbound.locked_until);
  return (
    <Row
      title="Gửi tin cho khách"
      testId="intel-unlock-today"
      state={<Badge kind="status" tone={s.outbound.unlocked_today ? 'ok' : 'neutral'}>{s.outbound.unlocked_today ? 'Đã mở khoá hôm nay' : 'Cần mã kép hoặc mở khoá'}</Badge>}
    >
      <p className="muted-note">
        «OK #MÃ» gửi tin cho khách trong chat chỉ hợp lệ khi Sếp mở khoá hôm nay (đúng ngày hôm nay, giờ VN) hoặc nhập mã kép qua kênh phụ.
      </p>
      {until ? <p className="muted-note" role="status">«OK» gửi tin khách đang tạm khoá tới {until} vì có dấu hiệu bất thường — mở khoá hôm nay không gỡ khoá này.</p> : null}
      <Button
        variant="secondary"
        size="sm"
        disabled={s.outbound.unlocked_today || s.assistant.locked}
        loading={m.isPending}
        onClick={() => m.mutate(undefined, { onSuccess: () => toast('Đã mở khoá hôm nay') })}
      >
        Mở khoá hôm nay (cần mã PIN)
      </Button>
      {s.assistant.locked ? <p className="muted-note">Trợ lý đang khoá — mở khoá trợ lý trước.</p> : null}
      <Err e={m.error} />
    </Row>
  );
}

function ModeRow({ s }: { s: IntelConsole }) {
  const m = useIntelSettings();
  const setMode = (mode: IntelMode) =>
    mode !== s.mode &&
    (mode !== 'shadow' || window.confirm('Chuyển sang chạy thử? Bốn cổng thường sẽ không nhắn riêng nữa (lừa đảo và an toàn vẫn báo thật). Gen báo kênh phụ về việc này.')) &&
    m.mutate({ mode }, { onSuccess: () => toast(mode === 'shadow' ? 'Đã chuyển sang chạy thử' : 'Đã chuyển sang chạy thật') });
  const setRamp = (ramp_gates: IntelRamp) => ramp_gates !== s.ramp_gates && m.mutate({ ramp_gates }, { onSuccess: () => toast('Đã đổi cách báo trong 14 ngày đầu') });
  return (
    <Row title="Cách chạy" testId="intel-mode" state={<Badge kind="status" tone={s.mode === 'shadow' ? 'warn' : 'ok'}>{s.mode === 'shadow' ? 'Chạy thử' : 'Chạy thật'}</Badge>}>
      <Segmented<IntelMode>
        label="Chạy thật hoặc chạy thử"
        value={s.mode}
        onChange={setMode}
        options={[
          { value: 'live', label: 'Chạy thật' },
          { value: 'shadow', label: 'Chạy thử' },
        ]}
      />
      <p className="muted-note">Chạy thử: bốn cổng thường chỉ ghi “đáng lẽ báo”, không nhắn riêng; lừa đảo và an toàn vẫn báo thật. Đổi cách chạy cần mã PIN.</p>
      <Segmented<IntelRamp>
        label="Bốn cổng thường trong 14 ngày đầu"
        value={s.ramp_gates}
        onChange={setRamp}
        options={[
          { value: 'digest_top', label: 'Đầu bản tin (14 ngày đầu)' },
          { value: 'push', label: 'Nhắn ngay từ sớm' },
        ]}
      />
      <Err e={m.error} />
    </Row>
  );
}

function BackupRow({ s }: { s: IntelConsole }) {
  const paired = s.backup_channel.paired;
  return (
    <Row title="Kênh phụ" testId="intel-backup" state={<Badge kind="status" tone={paired ? 'ok' : 'warn'}>{paired ? 'Đã ghép Telegram' : 'Chưa có kênh phụ'}</Badge>}>
      <p className="muted-note">
        {paired
          ? 'Kênh phụ nhận báo sức khoẻ hệ thống và mã kép. Đổi, gỡ hoặc ghép lại cần mã PIN, và Gen báo ngay tới kênh hiện tại trước khi đổi.'
          : 'Chưa có kênh phụ: Sếp chưa nhận được báo sức khoẻ và mã kép, nên tin gửi khách chỉ mở được bằng «Mở khoá hôm nay». Nên ghép Telegram.'}
      </p>
      <Link to="/connections" className="sys-link">
        {paired ? 'Đổi / ghép lại kênh phụ ở Kết nối' : 'Ghép kênh phụ ở Kết nối'}
      </Link>
    </Row>
  );
}

function MutesRow({ s }: { s: IntelConsole }) {
  const mute = useMuteKind();
  const unmute = useUnmute();
  return (
    <Row title="Cảnh báo lừa đảo và an toàn" testId="intel-mutes">
      <p className="muted-note">Hai loại này luôn báo thật. Tắt chỉ làm được ở đây (cần mã PIN), không làm được qua chat.</p>
      {DEFENSIVE_KINDS.map(({ kind, label }) => {
        const m = s.mutes.find((x) => x.scope_value === kind);
        return (
          <div className="triage-row" key={kind}>
            <Switch
              checked={!m}
              label={`Báo ${label}`}
              disabled={mute.isPending || unmute.isPending}
              onChange={(on) => {
                if (on) {
                  if (m) unmute.mutate(m.id, { onSuccess: () => toast(`Đã bật lại báo ${label}`) });
                } else if (window.confirm(`Tắt báo "${label}"? Đây là loại cảnh báo bảo vệ Sếp; Gen báo kênh phụ về việc này.`)) {
                  mute.mutate(kind, { onSuccess: () => toast(`Đã tắt báo ${label}`, 'warn') });
                }
              }}
            />
            <span>{label}</span>
            {m ? <Badge kind="status" tone="bad">Đang tắt</Badge> : null}
          </div>
        );
      })}
      <Err e={mute.error ?? unmute.error} />
    </Row>
  );
}

function PayeesRow({ s }: { s: IntelConsole }) {
  const verify = useVerifyPayee();
  return (
    <Row title="Số tài khoản chờ xác minh" testId="intel-payees" state={<Badge kind="count" count={s.payees.length} />}>
      {s.payees.length === 0 ? (
        <p className="muted-note">Không có số tài khoản nào đang chờ Sếp xác minh.</p>
      ) : (
        <ul className="intel-list">
          {s.payees.map((p) => (
            <li key={p.signal_id} className="intel-list__item">
              <span className="intel-list__text">
                {p.title}
                <span className="mono muted-note"> · {p.bank ? `${p.bank} ` : ''}…{p.last3 ?? '???'}</span>
              </span>
              <Button
                variant="secondary"
                size="sm"
                aria-label={`Xác minh số tài khoản ${p.bank ?? ''} …${p.last3 ?? ''}`}
                loading={verify.isPending && verify.variables === p.signal_id}
                onClick={() => verify.mutate(p.signal_id, { onSuccess: () => toast('Đã xác minh số tài khoản') })}
              >
                Đúng số của người quen (PIN)
              </Button>
            </li>
          ))}
        </ul>
      )}
      <Err e={verify.error} />
    </Row>
  );
}

function GroupsRow({ s }: { s: IntelConsole }) {
  const bulk = useBulkContexts();
  const [picked, setPicked] = useState<string[]>([]);
  const [ctx, setCtx] = useState(s.contexts[0] ?? 'khac');
  const toggle = (id: string) => setPicked((p) => (p.includes(id) ? p.filter((x) => x !== id) : [...p, id]));
  const apply = () =>
    bulk.mutate(
      { group_ids: picked, labels: [{ context: ctx, is_primary: true }] },
      {
        onSuccess: () => {
          toast(`Đã gán “${contextLabel(ctx)}” cho ${picked.length} nhóm`);
          setPicked([]);
        },
      },
    );
  return (
    <Row title="Bối cảnh nhóm" testId="intel-groups" state={<Badge kind="count" count={s.groups.length} />}>
      {s.groups.length === 0 ? (
        <p className="muted-note">Chưa có nhóm nào.</p>
      ) : (
        <>
          <p className="muted-note">Chọn nhiều nhóm rồi gán chung một bối cảnh — việc này THAY TOÀN BỘ bối cảnh Sếp đã gán cho các nhóm đã chọn. Nhóm chưa gán được xử lý thận trọng (như “Khác”).</p>
          <ul className="intel-list intel-list--scroll">
            {s.groups.map((g) => (
              <li key={g.id} className="intel-list__item">
                <label className="intel-check">
                  <input type="checkbox" checked={picked.includes(g.id)} onChange={() => toggle(g.id)} />
                  <span className="intel-list__text">{g.name}</span>
                </label>
                <span className="muted-note">{g.labels.length ? g.labels.map((l) => contextLabel(l.context)).join(', ') : 'Chưa gán'}</span>
              </li>
            ))}
          </ul>
          <div className="intel-bulk">
            <SelectField label="Bối cảnh" value={ctx} onChange={(e) => setCtx(e.target.value)} options={s.contexts.map((c) => ({ value: c, label: contextLabel(c) }))} />
            <Button variant="secondary" size="sm" disabled={picked.length === 0} loading={bulk.isPending} onClick={apply}>
              {picked.length ? `Gán cho ${picked.length} nhóm` : 'Gán cho nhóm đã chọn'}
            </Button>
          </div>
        </>
      )}
      <Err e={bulk.error} />
    </Row>
  );
}
