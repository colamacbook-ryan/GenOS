import { useEffect, useState, type ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { AUTONOMY_AUTO_LABEL, AUTONOMY_CHOICES, AUTONOMY_LEVELS, autonomyChoice, autonomyPatch, eventTypeLabel, type DirRelation, type PersonType, type Profile } from '@gen-harness/contracts';
import { Button, Card, Dialog, EmptyState, Icon, SelectField, TextField } from '@gen-harness/ui';
import { fmtDMClock } from '../../lib/format';
import { useCan } from '../../lib/permissions';
import { useUrlState } from '../../lib/uiStore';
import { CardError, InlineError, Panel, SkeletonLines } from '../common';
import { AutonomySelect } from '../AutonomySelect';
import { WhyButton } from '../core/Evidence';
import { errorText } from '../../lib/errorText';
import { PERSON_TYPE_LIST, PERSON_TYPE_LABEL, personTypeLabel, personTypePatch, staffPatch } from '../people/peopleModel';
import { RELATION_LABEL, SUMMARY_TONE, channelIcon, channelTone, eventTone, fmtBytes, initialsOf } from './relationsModel';
import { useProfile, useUpdateProfile } from './queries';

export function ProfileScreen() {
  const [id, setId] = useUrlState<string>('id', '');
  const q = useProfile(id || null);
  // Hồ sơ đã gộp: máy chủ chuyển hướng tới hồ sơ gốc ⇒ đổi `?id=` sang hồ sơ đang hiện, để sửa/ghi chú đi vào ĐÚNG hồ sơ sống.
  const liveId = q.data?.person.id;
  useEffect(() => {
    if (liveId && id && liveId !== id) setId(liveId);
  }, [liveId, id, setId]);

  if (!id) {
    return (
      <div className="screen">
        <Card>
          <EmptyState
            icon="ph ph-identification-card"
            title="Chưa chọn hồ sơ"
            description="Mở một hồ sơ từ Khách & Nhóm."
            actions={
              <Link to="/directory" className="gh-btn gh-btn--primary btn-24">
                <Icon name="ph ph-address-book" size={12} />
                Mở Khách &amp; Nhóm
              </Link>
            }
          />
        </Card>
      </div>
    );
  }
  if (q.isPending) {
    return (
      <div className="screen">
        <SkeletonLines rows={8} />
      </div>
    );
  }
  if (q.isError) {
    return (
      <div className="screen">
        <CardError error={q.error} onRetry={() => void q.refetch()} retrying={q.isFetching} />
      </div>
    );
  }
  return <ProfileBody id={id} p={q.data} />;
}

function ProfileBody({ id, p }: { id: string; p: Profile }) {
  const [noteOpen, setNoteOpen] = useState(false);
  const [autonomyOpen, setAutonomyOpen] = useState(false);
  const [typeOpen, setTypeOpen] = useState(false);
  const markStaff = useUpdateProfile();
  // Đổi mức tự trị cần profile.write — vai trò chỉ đọc (Auditor) không thấy nút (mở ra chỉ gặp 403), như Danh bạ.
  const canWrite = useCan('profile.write');

  return (
    <div className="screen">
      <Link to="/directory" className="gh-btn gh-btn--ghost pf-back">
        <Icon name="ph ph-arrow-left" size={13} />
        Quay lại Khách &amp; Nhóm
      </Link>

      <div className="pf-head">
        <span className="pf-head__av" aria-hidden>
          {initialsOf(p.person.name)}
        </span>
        <div className="pf-head__body">
          <div className="pf-head__row">
            <span className="pf-head__name">{p.person.name}</span>
            <span className="mono-tag">{personTypeLabel(p.person.type)}</span>
            {relationText(p.person.relation_to_owner) ? <span className="mono-tag">{relationText(p.person.relation_to_owner)}</span> : null}
          </div>
          <div className="pf-head__identities">
            {p.identities.map((idn) => (
              <span className="pf-identity" key={idn.id}>
                <Icon name={channelIcon(idn.channel.type)} size={13} color={channelTone(idn.channel.type)} />
                {idn.channel.name} · {idn.handle ?? idn.phone_e164 ?? idn.external_id}
              </span>
            ))}
            {p.merge_history.length > 0 ? (
              <Button variant="ghost" size="sm" icon="ph ph-git-merge">
                Xem lịch sử hợp nhất ({p.merge_history.length})
              </Button>
            ) : null}
          </div>
        </div>
        <div className="pf-head__actions">
          <Button variant="secondary" icon="ph ph-user-plus" onClick={() => setNoteOpen(true)}>
            Gán phụ trách / ghi chú
          </Button>
          {canWrite && p.person.type !== 'staff' ? (
            <Button variant="secondary" icon="ph ph-identification-badge" loading={markStaff.isPending} onClick={() => markStaff.mutate({ id, body: staffPatch() })}>
              Đánh dấu là nhân viên
            </Button>
          ) : null}
          {canWrite ? (
            <Button variant="ghost" icon="ph ph-tag" onClick={() => setTypeOpen(true)}>
              Sửa loại người
            </Button>
          ) : null}
          <Link to="/workbench" className="gh-btn gh-btn--primary pf-head__workbench">
            <Icon name="ph ph-pen-nib" size={14} />
            Mở bàn làm việc
          </Link>
        </div>
      </div>

      {markStaff.isError ? <InlineError>{errorText(markStaff.error)}</InlineError> : null}

      <div className="pf-scores">
        {p.scores.map((s) => (
          <div className="pf-score" key={s.dimension}>
            <div className="pf-score__head">
              <span className="pf-score__label">{s.label}</span>
              {s.trend ? <Icon name={s.trend === 'up' ? 'ph ph-trend-up' : s.trend === 'down' ? 'ph ph-trend-down' : 'ph ph-minus'} size={13} /> : null}
            </div>
            <div className="pf-score__value">{Math.round(s.value)}</div>
            <div className="pf-score__updated">cập nhật {fmtDMClock(s.updated_at)}</div>
            <WhyButton kind="score" id={`person:${id}:${s.dimension}`} icon={null} iconRight="ph ph-arrow-right" size="sm" className="pf-score__why">
              Vì sao
            </WhyButton>
          </div>
        ))}
      </div>

      <div className="pf-grid">
        <div className="pf-col">
          <Panel flush title="Hệ thống hiểu gì về đối tượng này" kicker="Tóm tắt tự cập nhật">
            {p.summary.length === 0 ? (
              <EmptyState icon="ph ph-brain" title="Chưa có tóm tắt" description="Cần thêm tin nhắn có nội dung để hệ thống tóm tắt." />
            ) : (
              <div className="pf-summary">
                {p.summary.map((ln, i) => (
                  <div className="pf-summary__row" key={i}>
                    <span className="pf-summary__dot" style={{ background: SUMMARY_TONE[ln.tone] }} />
                    <span className="pf-summary__text">{ln.text}</span>
                  </div>
                ))}
              </div>
            )}
          </Panel>

          <Panel flush title="Dòng sự kiện" kicker="Sự kiện có nghĩa, không phải log tin nhắn">
            {p.timeline.length === 0 ? (
              <EmptyState icon="ph ph-clock-counter-clockwise" title="Chưa có sự kiện nào" />
            ) : (
              <div className="pf-timeline">
                {p.timeline.map((tl) => (
                  <div className="pf-timeline__row" key={tl.id}>
                    <span className="pf-timeline__time">{fmtDMClock(tl.observed_at)}</span>
                    <span className="mono-tag" style={{ color: eventTone(tl.event_type) }}>
                      {eventTypeLabel(tl.event_type)}
                    </span>
                    <span className="pf-timeline__detail" title={tl.conclusion}>
                      {tl.conclusion}
                    </span>
                    <WhyButton kind="meaning_unit" id={tl.id} icon={null} size="sm">
                      Chứng cứ
                    </WhyButton>
                  </div>
                ))}
              </div>
            )}
          </Panel>
        </div>

        <div className="pf-col">
          <Panel flush title="Mức tự trị với đối tượng này" kicker="Trợ lý được làm tới đâu với người này">
            <div className="pf-autonomy">
              <AutonomySteps level={p.autonomy_level} />
              <p className="pf-autonomy__desc">
                {autonomyChoice(p.autonomy_level)
                  ? `Đang đặt: ${autonomyChoice(p.autonomy_level)?.label}.`
                  : 'Chưa đặt mức tự trị riêng cho đối tượng này — dùng mức mặc định.'}
              </p>
              {canWrite ? (
                <Button variant="ghost" size="sm" icon="ph ph-sliders-horizontal" onClick={() => setAutonomyOpen(true)}>
                  Đổi mức tự trị
                </Button>
              ) : null}
            </div>
          </Panel>

          <Panel flush title="Tài liệu đã trao đổi" kicker="Tài liệu">
            {p.documents.length === 0 ? (
              <EmptyState icon="ph ph-files" title="Chưa có tài liệu nào" />
            ) : (
              <div className="pf-list">
                {p.documents.map((d) => (
                  <div className="pf-list__row" key={d.id}>
                    <Icon name="ph ph-file-text" size={15} color="var(--color-neutral-400)" />
                    <div style={{ minWidth: 0, flex: 1 }}>
                      <div className="pf-list__title">{d.title}</div>
                      <div className="pf-list__meta">{fmtDMClock(d.created_at)} · {fmtBytes(d.bytes)}</div>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </Panel>

          <Panel flush title="Người nội bộ từng chạm" kicker="Người trong công ty">
            {p.touchpoints.length === 0 ? (
              <EmptyState icon="ph ph-users-three" title="Chưa ai từng chạm hồ sơ này" />
            ) : (
              <div className="pf-list">
                {p.touchpoints.map((u) => (
                  <div className="pf-list__row" key={u.id}>
                    <span className="pf-touch-av">{initialsOf(u.name)}</span>
                    <span className="pf-list__title" style={{ flex: 1 }}>{u.name}</span>
                  </div>
                ))}
              </div>
            )}
          </Panel>

          <Panel flush title="Ghi chú tay của Sếp" kicker="Hệ thống không sửa">
            {p.owner_note ? <p className="pf-note">{p.owner_note}</p> : <EmptyState icon="ph ph-note-pencil" title="Chưa có ghi chú" />}
          </Panel>
        </div>
      </div>

      {noteOpen ? <NoteDialog id={id} p={p} onClose={() => setNoteOpen(false)} /> : null}
      {typeOpen && canWrite ? <PersonTypeDialog id={id} p={p} onClose={() => setTypeOpen(false)} /> : null}
      {autonomyOpen && canWrite ? <AutonomyDialog id={id} p={p} onClose={() => setAutonomyOpen(false)} /> : null}
    </div>
  );
}

/** F-30: 3 bước theo AUTONOMY_CHOICES (bước đã đạt tô sáng) + bước "Tự làm" khi mức đang lưu là 5/6. */
function AutonomySteps({ level }: { level: number | null }) {
  const active = autonomyChoice(level);
  const idx = active ? (active.key === 'auto' ? AUTONOMY_CHOICES.length : AUTONOMY_CHOICES.findIndex((c) => c.key === active.key)) : -1;
  const stepStyle = { height: 'auto', minHeight: 22, padding: '3px 4px', textAlign: 'center' as const, lineHeight: 1.25, fontFamily: 'inherit' };
  return (
    <div className="pf-autonomy__steps" role="list" aria-label="Các mức tự trị">
      {AUTONOMY_CHOICES.map((c, i) => (
        <span key={c.key} role="listitem" className="pf-autonomy__step" style={stepStyle} data-on={i <= idx} aria-current={active?.key === c.key || undefined} title={c.hint}>
          {c.label}
        </span>
      ))}
      {active?.key === 'auto' ? (
        <span role="listitem" className="pf-autonomy__step" style={stepStyle} data-on aria-current title={`Mức ${level} — ${AUTONOMY_LEVELS[level ?? 0]}`}>
          {AUTONOMY_AUTO_LABEL}
        </span>
      ) : null}
    </div>
  );
}

function AutonomyDialog({ id, p, onClose }: { id: string; p: Profile; onClose: () => void }) {
  const update = useUpdateProfile();
  // F-30: `null` = giữ nguyên — không đổi thì nút Lưu tắt, không phát PATCH.
  const [picked, setPicked] = useState<number | null>(null);
  const patch = autonomyPatch(p.autonomy_level, picked);
  const changed = 'autonomy_level' in patch;
  return (
    <DialogShell
      title="Mức tự trị với đối tượng này"
      kicker={p.person.name}
      onClose={onClose}
      busy={update.isPending}
      saveDisabled={!changed}
      error={update.isError ? update.error : null}
      onSave={() => {
        if (!changed) return onClose();
        update.mutate({ id, body: patch }, { onSuccess: onClose });
      }}
    >
      <AutonomySelect current={p.autonomy_level} value={picked} onChange={setPicked} />
    </DialogShell>
  );
}

/** Nhãn "Liên quan Sếp"; giá trị lạ/trống → không hiện (không lộ mã kỹ thuật). */
function relationText(r: string | null | undefined): string {
  return r && r in RELATION_LABEL ? RELATION_LABEL[r as DirRelation] : '';
}

/** v0.1.64 (F045): Sếp tự sửa loại người và mức "Liên quan Sếp" — loại Sếp đặt luôn thắng quy tắc sàng lọc. */
function PersonTypeDialog({ id, p, onClose }: { id: string; p: Profile; onClose: () => void }) {
  const update = useUpdateProfile();
  const [type, setType] = useState<PersonType | null>(null);
  const [relation, setRelation] = useState<DirRelation | null>(null);
  const current = { type: p.person.type, relation: p.person.relation_to_owner };
  const patch = personTypePatch(current, type, relation);
  const changed = Object.keys(patch).length > 0;
  const shownType = type ?? p.person.type ?? 'unknown';
  const shownRelation = relation ?? (relationText(current.relation) ? (current.relation as DirRelation) : 'stranger');
  return (
    <DialogShell
      title="Loại người và quan hệ với Sếp"
      kicker={p.person.name}
      onClose={onClose}
      busy={update.isPending}
      saveDisabled={!changed}
      error={update.isError ? update.error : null}
      onSave={() => {
        if (!changed) return onClose();
        update.mutate({ id, body: patch }, { onSuccess: onClose });
      }}
    >
      <SelectField
        label="Loại người"
        value={shownType}
        onChange={(e) => setType(e.target.value as PersonType)}
        options={PERSON_TYPE_LIST.map((t) => ({ value: t, label: PERSON_TYPE_LABEL[t] }))}
      />
      <SelectField
        label="Liên quan Sếp"
        value={shownRelation}
        onChange={(e) => setRelation(e.target.value as DirRelation)}
        options={(Object.keys(RELATION_LABEL) as DirRelation[]).map((r) => ({ value: r, label: RELATION_LABEL[r] }))}
      />
    </DialogShell>
  );
}

function NoteDialog({ id, p, onClose }: { id: string; p: Profile; onClose: () => void }) {
  const update = useUpdateProfile();
  const [note, setNote] = useState(p.owner_note ?? '');
  return (
    <DialogShell
      title="Gán phụ trách / ghi chú tay"
      kicker={p.person.name}
      onClose={onClose}
      busy={update.isPending}
      error={update.isError ? update.error : null}
      onSave={() => update.mutate({ id, body: { note: note.trim() || null } }, { onSuccess: onClose })}
    >
      <TextField label="Ghi chú tay của Sếp" value={note} onChange={(e) => setNote(e.target.value)} maxLength={2000} />
    </DialogShell>
  );
}

function DialogShell({
  title, kicker, onClose, onSave, busy, error, children, saveDisabled = false,
}: {
  title: string; kicker: string; onClose: () => void; onSave: () => void; busy: boolean; error: unknown; children: ReactNode;
  saveDisabled?: boolean;
}) {
  return (
    <Dialog
      open
      onClose={onClose}
      width={420}
      title={title}
      kicker={kicker}
      actions={
        <>
          <Button variant="secondary" onClick={onClose}>
            Huỷ
          </Button>
          <Button variant="primary" icon="ph ph-check" loading={busy} disabled={saveDisabled} onClick={onSave}>
            Lưu
          </Button>
        </>
      }
    >
      <div className="dlg-fields">
        {children}
        {error ? <InlineError>{errorText(error)}</InlineError> : null}
      </div>
    </Dialog>
  );
}
