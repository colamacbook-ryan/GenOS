import { useState } from 'react';
import { Link } from 'react-router-dom';
import { Badge, Button, SelectField, Segmented, Switch } from '@gen-harness/ui';
import { errorDetail, errorText } from '../lib/errorText';
import { useMe } from '../lib/queries';
import { toast } from '../lib/toast';
import { CardError, InlineError, Panel, SkeletonLines } from '../screens/common';
import {
  useBulkContexts,
  useIntelConsole,
  useIntelSettings,
  useLockAssistant,
  useMuteKind,
  useSignalFeedback,
  useUnlockAssistant,
  useUnlockToday,
  useUnmute,
  useVerifyPayee,
} from './intelQueries';
import {
  DEFENSIVE_KINDS,
  WRONG_REASONS,
  contextLabel,
  kindLabel,
  rampBannerText,
  reasonText,
  vnTime,
  type IntelConsole,
  type IntelMode,
  type IntelRamp,
  type WrongReason,
} from './intelModel';
import { GATE_LABEL, REGULAR_GATES } from './intelHubModel';
import './intelP4.css';

/**
 * v0.1.76 (T11, CNS-077) — thao tác cần mã PIN của Tình báo; v0.1.79 (G1): tách đôi cho trang Tình báo (`/intel`), chỉ Owner thấy:
 *  - `part="trial"` → tab "Chạy thử 14 ngày": banner R4, tình trạng từng cổng + M1, dòng bóng chờ chấm Đúng/Sai, "Cách chạy" (chạy thật / chạy thử);
 *  - `part="controls"` → tab "Thiết lập" (dưới các bước): khoá / mở khoá trợ lý, mở khoá hôm nay (cho «OK» gửi tin khách), ghép kênh phụ, tắt báo
 *    lừa đảo-an toàn, xác minh số tài khoản, gán bối cảnh nhóm hàng loạt.
 * Việc cần PIN: máy chủ trả 423 ⇒ hộp PIN chung tự mở rồi gửi lại (không tự dựng ô PIN).
 */
const PARTS = {
  trial: {
    title: 'Chạy thử 14 ngày',
    kicker: 'Bốn cổng thường chạy thử trước khi Gen được nhắn riêng ngay',
    genTarget: 'intel.trial.card',
  },
  controls: {
    title: 'Khoá, xác minh và cảnh báo',
    kicker: 'Thao tác cần mã PIN của Tình báo',
    genTarget: 'intel.setup.controls',
  },
} as const;

export function IntelCard({ part }: { part: 'trial' | 'controls' }) {
  const me = useMe();
  const isOwner = me.data?.role?.code === 'owner';
  const q = useIntelConsole(isOwner);
  if (!isOwner) return null;
  const p = PARTS[part];
  return (
    <Panel title={p.title} kicker={p.kicker} label={p.title} bodyClass="intel-card" genTarget={p.genTarget}>
      {q.isPending ? (
        <SkeletonLines rows={5} padding="0" />
      ) : q.isError ? (
        <CardError error={q.error} onRetry={() => void q.refetch()} retrying={q.isFetching} />
      ) : part === 'trial' ? (
        <TrialBody s={q.data} />
      ) : (
        <ControlsBody s={q.data} />
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

/** v0.1.77 (R4): bốn cổng thường chạy thử lại 14 ngày — nói rõ từ ngày nào, còn mấy ngày, đã chấm mấy lượt. Hết thử/đạt M1 thì biến mất. */
function RampBanner({ s }: { s: IntelConsole }) {
  const text = rampBannerText(s.ramp);
  if (!text) return null;
  return (
    <div className="intel-ramp-banner" role="status" data-testid="intel-ramp-banner">
      <span className="intel-ramp-banner__text">{text}</span>
    </div>
  );
}

/**
 * v0.1.77: dòng bóng "đáng lẽ báo ngay" của bốn cổng thường. Sếp chấm Đúng, hoặc Sai kèm một trong ba lý do (khách vẫn mua · không gấp · NV lo rồi) —
 * chính các lượt chấm này tính M1. Chỉ Owner (máy chủ kiểm); mục riêng tư cần PIN không nằm trong danh sách khi chưa mở PIN.
 */
function ShadowRow({ s }: { s: IntelConsole }) {
  const fb = useSignalFeedback();
  const [asking, setAsking] = useState<string | null>(null);
  const items = s.shadow_items ?? [];
  if (!s.shadow_items) return null;
  const send = (id: string, verdict: 'correct' | 'wrong', reason?: WrongReason) =>
    fb.mutate({ id, verdict, ...(reason ? { reason } : {}) }, { onSuccess: () => { setAsking(null); toast(verdict === 'correct' ? 'Đã ghi: báo đúng' : 'Đã ghi: báo sai'); } });
  return (
    <Row title="Chạy thử: đáng lẽ báo ngay" testId="intel-shadow" state={<Badge kind="count" count={items.length} />}>
      {items.length === 0 ? (
        <p className="muted-note">Chưa có mục nào đang chờ Sếp chấm. Mỗi lần Gen đáng lẽ báo ngay, mục đó hiện ở đây để Sếp chấm Đúng hoặc Sai.</p>
      ) : (
        <>
          <p className="muted-note">Gen chưa nhắn riêng các mục này. Sếp chấm Đúng hoặc Sai để Gen biết khi nào đủ chính xác để nhắn ngay.</p>
          <ul className="intel-list">
            {items.map((it) => {
              const why = reasonText(it.reasons, s.ramp?.red_cap_per_day);
              return (
                <li key={it.id} className="intel-list__item intel-shadow__item">
                  <span className="intel-list__text">
                    <span className="intel-shadow__kind">{kindLabel(it.kind)}</span> {it.title}
                    <span className="muted-note intel-shadow__why">
                      {it.would_push_at ? `Đáng lẽ báo ngay lúc ${vnTime(it.would_push_at)}` : 'Đáng lẽ báo ngay'}
                      {why.length ? ` · ${why.join(' · ')}` : ''}
                    </span>
                  </span>
                  {it.verdict === 'correct' || it.verdict === 'wrong' ? (
                    <Badge kind="status" tone={it.verdict === 'correct' ? 'ok' : 'warn'}>{it.verdict === 'correct' ? 'Đã chấm Đúng' : 'Đã chấm Sai'}</Badge>
                  ) : asking === it.id ? (
                    <span className="intel-shadow__why-pick" role="group" aria-label="Vì sao báo sai?">
                      <span className="muted-note">Vì sao sai?</span>
                      {WRONG_REASONS.map((r) => (
                        <Button key={r.value} variant="secondary" size="sm" aria-label={`Sai: ${r.label}`} disabled={fb.isPending} onClick={() => send(it.id, 'wrong', r.value)}>
                          {r.label}
                        </Button>
                      ))}
                      <Button variant="ghost" size="sm" onClick={() => setAsking(null)}>
                        Huỷ
                      </Button>
                    </span>
                  ) : (
                    <span className="intel-shadow__verdict">
                      <Button variant="secondary" size="sm" aria-label={`Báo đúng: ${it.title}`} disabled={fb.isPending} onClick={() => send(it.id, 'correct')}>
                        Đúng
                      </Button>
                      <Button variant="secondary" size="sm" aria-label={`Báo sai: ${it.title}`} disabled={fb.isPending} onClick={() => setAsking(it.id)}>
                        Sai
                      </Button>
                    </span>
                  )}
                </li>
              );
            })}
          </ul>
        </>
      )}
      <Err e={fb.error} />
    </Row>
  );
}

function TrialBody({ s }: { s: IntelConsole }) {
  return (
    <>
      <RampBanner s={s} />
      <GatesRow s={s} />
      <ShadowRow s={s} />
      <ModeRow s={s} />
    </>
  );
}

function ControlsBody({ s }: { s: IntelConsole }) {
  return (
    <>
      <AssistantRow s={s} />
      <UnlockTodayRow s={s} />
      <BackupRow s={s} />
      <MutesRow s={s} />
      <PayeesRow s={s} />
      <GroupsRow s={s} />
    </>
  );
}

/**
 * v0.1.79 (G1): "loại nào đã đủ để đẩy thật" — bốn cổng thường: đã qua M1 (đủ lượt chấm và đủ đúng) / chấm Sai nhiều / còn thiếu lượt chấm,
 * cùng số lượt Sếp đã chấm (M1 gộp n/min_n). Chỉ đọc từ `GET /intel/console` (`ramp`); máy chủ cũ không gửi ⇒ không hiện.
 */
function GatesRow({ s }: { s: IntelConsole }) {
  const r = s.ramp;
  if (!r) return null;
  const passed = new Set(r.passed_gates);
  const low = new Set(r.m1.low_precision_gates);
  const pct = r.m1.p == null ? null : Math.round(r.m1.p * 100);
  return (
    <Row
      title="Đã chấm bao nhiêu, loại nào đủ để nhắn ngay"
      testId="intel-gates"
      state={<Badge kind="status" tone={r.m1.ok ? 'ok' : 'neutral'}>{`Đã chấm ${r.m1.n}/${r.m1.min_n} lượt`}</Badge>}
    >
      <p className="muted-note">
        {pct == null ? 'Chưa có lượt chấm nào.' : `Trong ${r.m1.n} lượt Sếp chấm, ${r.m1.k} lượt Đúng (${pct}%).`}{' '}
        {r.m1.n >= r.m1.min_n ? '' : `Cần ít nhất ${r.m1.min_n} lượt để Gen biết khi nào đủ chính xác.`}
        {r.days_left != null && r.in_trial ? ` Còn ${r.days_left} ngày chạy thử.` : ''}
      </p>
      <ul className="intel-list">
        {REGULAR_GATES.map((g) => (
          <li key={g} className="intel-list__item" data-testid={`intel-gate-${g}`}>
            <span className="intel-list__text">{GATE_LABEL[g]}</span>
            {passed.has(g) ? (
              <Badge kind="status" tone="ok">Đủ để nhắn ngay</Badge>
            ) : low.has(g) ? (
              <Badge kind="status" tone="warn">Chấm Sai nhiều</Badge>
            ) : (
              <Badge kind="status" tone="neutral">Chưa đủ lượt chấm</Badge>
            )}
          </li>
        ))}
      </ul>
    </Row>
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
      {s.regular_push_enabled === false ? (
        <div className="intel-ramp-locked">
          <span className="intel-row__title">Bốn cổng thường</span>
          <Badge kind="status" tone="neutral">Đầu bản tin (bản này)</Badge>
          <p className="muted-note">
            Bản này khách chê/doạ bỏ, lời hứa sắp vỡ, cơ hội sắp trôi và sự cố việc chỉ lên đầu bản tin (“đáng lẽ báo ngay”), chưa nhắn riêng.
            Lừa đảo và an toàn vẫn báo ngay.
          </p>
        </div>
      ) : (
        <Segmented<IntelRamp>
          label="Bốn cổng thường trong 14 ngày đầu"
          value={s.ramp_gates}
          onChange={setRamp}
          options={[
            { value: 'digest_top', label: 'Đầu bản tin (14 ngày đầu)' },
            { value: 'push', label: 'Nhắn ngay từ sớm' },
          ]}
        />
      )}
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
