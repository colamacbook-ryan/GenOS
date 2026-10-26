import { useState } from 'react';
import { Badge, Button, Switch } from '@gen-harness/ui';
import { errorDetail, errorText } from '../../lib/errorText';
import { useMe } from '../../lib/queries';
import { toast } from '../../lib/toast';
import { personTypeLabel } from '../people/peopleModel';
import { CardError, InlineError, Panel, SkeletonLines } from '../common';
import { useBulkContexts, useConfirmStaff, useIntelSetup, useSetStaff } from './intelQueries';
import { contextLabel, vnTime, type IntelSetup } from './intelModel';

/** Số dòng hiện sẵn mỗi bước; còn lại mở bằng nút "Xem thêm". */
const SHOW = 8;
export const INTEL_SETUP_ANCHOR = 'intel-setup';

/**
 * v0.1.76 (T14, Ryan chốt D3 2026-10-25 "hỏi lúc cài"): thẻ "Thiết lập Tình báo" — hai bước để Tình báo đầy đủ chạy đúng: (1) gán bối cảnh
 * cho từng nhóm (API hàng loạt sẵn có), (2) đánh dấu ai là nhân viên (đường "Đánh dấu là nhân viên" của hồ sơ) rồi bấm "Đã khai xong".
 * Không chặn app: chưa xong thì các loại cần biết khách/nhân sự vẫn thận trọng; lừa đảo và an toàn luôn chạy. Chỉ Owner thấy.
 */
export function IntelSetupCard() {
  const me = useMe();
  const isOwner = me.data?.role?.code === 'owner';
  const q = useIntelSetup(isOwner);
  if (!isOwner) return null;
  const s = q.data;
  return (
    <div id={INTEL_SETUP_ANCHOR} className="intel-setup-anchor">
      <Panel
        title="Thiết lập Tình báo"
        kicker={s?.ready ? 'Đã thiết lập xong' : 'Hai bước để Gen hiểu đúng nhóm và người của Sếp'}
        aside={s ? <StepRail s={s} /> : undefined}
        label="Thiết lập Tình báo"
        bodyClass="intel-setup"
      >
        {q.isPending ? (
          <SkeletonLines rows={4} padding="0" />
        ) : q.isError ? (
          <CardError error={q.error} onRetry={() => void q.refetch()} retrying={q.isFetching} />
        ) : s ? (
          <SetupBody s={s} />
        ) : null}
      </Panel>
    </div>
  );
}

function StepRail({ s }: { s: IntelSetup }) {
  const n = Number(s.groups.done) + Number(s.staff.done);
  return (
    <div className="intel-setup__rail" role="img" aria-label={`Đã xong ${n} trên 2 bước`}>
      <span className="intel-setup__seg" data-done={s.groups.done || undefined} />
      <span className="intel-setup__seg" data-done={s.staff.done || undefined} />
      <span className="intel-setup__rail-text">{n}/2</span>
    </div>
  );
}

function Err({ e }: { e: unknown }) {
  return e ? <InlineError detail={errorDetail(e)}>{errorText(e)}</InlineError> : null;
}

function SetupBody({ s }: { s: IntelSetup }) {
  if (s.ready) {
    return (
      <>
        <p className="intel-setup__lead" data-testid="intel-setup-ready">
          Đã gán bối cảnh cho cả {s.groups.total} nhóm và khai {s.staff.staff_count} nhân viên. Gen dùng các nhãn này để nhận ra khách, lời hứa và việc của
          Sếp.
        </p>
        <details className="intel-setup__more">
          <summary>Sửa danh sách nhân viên</summary>
          <StaffStep s={s} />
        </details>
      </>
    );
  }
  return (
    <>
      <ol className="intel-setup__steps">
        <GroupsStep s={s} />
        <StaffStep s={s} asStep />
      </ol>
      <p className="muted-note intel-setup__foot">
        Những loại theo bối cảnh nhóm ({s.gated_kinds.join(', ')}) chỉ chạy ở nhóm đã gán bối cảnh; nhóm chưa gán được xử lý thận trọng (ít báo).
        Danh sách nhân viên giúp Gen phân biệt khách và nhân viên chính xác hơn. Lừa đảo và an toàn luôn báo, không phụ thuộc hai bước này.
      </p>
    </>
  );
}

function StepHead({ n, title, done, state }: { n: number; title: string; done: boolean; state: string }) {
  return (
    <div className="intel-setup__head">
      <span className="intel-setup__num" data-done={done || undefined} aria-hidden="true">
        {done ? '✓' : n}
      </span>
      <span className="intel-setup__title">{title}</span>
      <Badge kind="status" tone={done ? 'ok' : 'warn'}>
        {state}
      </Badge>
    </div>
  );
}

function GroupsStep({ s }: { s: IntelSetup }) {
  const bulk = useBulkContexts();
  const [picks, setPicks] = useState<Record<string, string>>({});
  const [all, setAll] = useState(false);
  const [err, setErr] = useState<unknown>(null);
  const rows = s.groups.unlabeled;
  const shown = all ? rows : rows.slice(0, SHOW);
  const chosen = rows.filter((g) => picks[g.id]);
  const save = async () => {
    setErr(null);
    const byCtx = new Map<string, string[]>();
    for (const g of chosen) byCtx.set(picks[g.id], [...(byCtx.get(picks[g.id]) ?? []), g.id]);
    try {
      for (const [context, ids] of byCtx) await bulk.mutateAsync({ group_ids: ids, labels: [{ context, is_primary: true }] });
      toast(`Đã gán bối cảnh cho ${chosen.length} nhóm`);
      setPicks({});
    } catch (e) {
      setErr(e);
    }
  };
  return (
    <li className="intel-setup__step" data-testid="intel-setup-groups">
      <StepHead n={1} title="Gán bối cảnh cho nhóm" done={s.groups.done} state={s.groups.done ? 'Xong' : `Còn ${s.groups.unlabeled_count} nhóm`} />
      {s.groups.done ? (
        <p className="muted-note">Mọi nhóm đã có bối cảnh. Đổi bối cảnh một nhóm ở thẻ Tình báo.</p>
      ) : (
        <>
          <p className="muted-note">Nhóm khách, nhóm nhân viên hay nhóm gia đình — Gen cần biết để không báo nhầm và không bỏ sót lời hứa với khách.</p>
          <ul className="intel-setup__list">
            {shown.map((g) => (
              <li key={g.id} className="intel-setup__row">
                <span className="intel-setup__name">
                  {g.name}
                  {g.member_count != null ? <span className="intel-setup__meta">{g.member_count} thành viên</span> : null}
                </span>
                <select
                  className="gh-input intel-setup__pick"
                  aria-label={`Bối cảnh của nhóm ${g.name}`}
                  value={picks[g.id] ?? ''}
                  onChange={(e) => setPicks((p) => ({ ...p, [g.id]: e.target.value }))}
                >
                  <option value="">Chọn bối cảnh</option>
                  {s.contexts.map((c) => (
                    <option key={c} value={c}>
                      {contextLabel(c)}
                    </option>
                  ))}
                </select>
              </li>
            ))}
          </ul>
          {rows.length > SHOW ? (
            <Button variant="ghost" size="sm" onClick={() => setAll((x) => !x)}>
              {all ? 'Thu gọn' : `Xem thêm ${rows.length - SHOW} nhóm`}
            </Button>
          ) : null}
          <Button variant="primary" size="sm" disabled={chosen.length === 0} loading={bulk.isPending} onClick={() => void save()}>
            {chosen.length ? `Lưu bối cảnh cho ${chosen.length} nhóm` : 'Chọn bối cảnh để lưu'}
          </Button>
          <Err e={err} />
        </>
      )}
    </li>
  );
}

function StaffStep({ s, asStep }: { s: IntelSetup; asStep?: boolean }) {
  const setStaff = useSetStaff();
  const confirm = useConfirmStaff();
  const [all, setAll] = useState(false);
  const rows = s.staff.people;
  const shown = all ? rows : rows.slice(0, SHOW);
  const body = (
    <>
      <p className="muted-note">
        Bật cho người làm cho Sếp (cần mã PIN — Gen báo kênh phụ mỗi lần đổi). Lời hứa và câu trả lời của nhân viên được tính là phía Sếp; khách của
        Sếp sẽ không bị nhầm thành nhân viên.
      </p>
      {rows.length === 0 ? (
        <p className="muted-note">Chưa thấy ai trong các nhóm. Không có nhân viên thì bấm “Đã khai xong” luôn.</p>
      ) : (
        <ul className="intel-setup__list">
          {shown.map((p) => {
            const on = p.person_type === 'staff';
            return (
              <li key={p.id} className="intel-setup__row">
                <span className="intel-setup__name">
                  {p.name}
                  <span className="intel-setup__meta">{personTypeLabel(p.person_type)}</span>
                </span>
                {p.needs_reconfirm ? (
                  <span className="intel-setup__reconfirm">
                    <Badge kind="status" tone="warn">Cần xác nhận lại</Badge>
                    <Button
                      variant="secondary"
                      size="sm"
                      aria-label={`Xác nhận lại ${p.name} là nhân viên (cần mã PIN)`}
                      disabled={setStaff.isPending}
                      onClick={() => setStaff.mutate({ id: p.id, staff: true }, { onSuccess: () => toast(`Đã xác nhận ${p.name} là nhân viên`) })}
                    >
                      Xác nhận
                    </Button>
                  </span>
                ) : null}
                <Switch
                  checked={on}
                  label={`${p.name} là nhân viên`}
                  disabled={setStaff.isPending}
                  onChange={(v) => setStaff.mutate({ id: p.id, staff: v, prevType: p.prev_type }, { onSuccess: () => toast(v ? `Đã đánh dấu ${p.name} là nhân viên` : `Đã bỏ đánh dấu ${p.name}`) })}
                />
              </li>
            );
          })}
        </ul>
      )}
      {rows.length > SHOW ? (
        <Button variant="ghost" size="sm" onClick={() => setAll((x) => !x)}>
          {all ? 'Thu gọn' : `Xem thêm ${rows.length - SHOW} người`}
        </Button>
      ) : null}
      {s.staff.done ? (
        <p className="muted-note" role="status">
          Đã khai xong{s.staff.confirmed_at ? ` lúc ${vnTime(s.staff.confirmed_at)}` : ''} — {s.staff.staff_count} nhân viên. Bật/tắt ở trên vẫn có hiệu lực ngay.
        </p>
      ) : (
        <Button variant="primary" size="sm" loading={confirm.isPending} onClick={() => confirm.mutate(undefined, { onSuccess: () => toast('Đã ghi nhận danh sách nhân viên') })}>
          {s.staff.staff_count ? `Đã khai xong (${s.staff.staff_count} nhân viên)` : 'Đã khai xong — không có nhân viên'}
        </Button>
      )}
      <Err e={setStaff.error ?? confirm.error} />
    </>
  );
  if (!asStep) return <div className="intel-setup__step">{body}</div>;
  return (
    <li className="intel-setup__step" data-testid="intel-setup-staff">
      <StepHead n={2} title="Khai nhân viên" done={s.staff.done} state={s.staff.done ? 'Xong' : s.staff.staff_count ? `${s.staff.staff_count} người, chưa xác nhận` : 'Chưa khai'} />
      {body}
    </li>
  );
}
