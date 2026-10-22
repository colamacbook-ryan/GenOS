/**
 * v0.1.59 (G4) — HỒ SƠ SỐNG của một khách (`GET /profile/{person_id}`), chỉ đọc. Dùng ở màn Quan hệ (cột phải 4/12 trên máy tính,
 * trong ngăn chi tiết trên điện thoại) và — phần "Về khách này" — ở màn Thông tin.
 *
 * Tên, quan hệ với Sếp · 3 điểm có xu hướng kèm lời ("Độ nóng 87 · đang tăng") · tóm tắt 5 dòng · 5 sự việc gần nhất · kênh (số điện
 * thoại đã che) · ghi chú của Sếp (chỉ đọc) · "Nhờ Gen soạn tin" / "Tạo nhắc" · "Mở hồ sơ đầy đủ" (sửa ở Console).
 * Gen chỉ đề xuất: "Nhờ Gen soạn tin" chỉ mở ngăn Hỏi Gen với câu điền sẵn. Lỗi từng khối riêng: không tải được hồ sơ thì chỉ khối này báo lỗi.
 */
import { Link } from 'react-router-dom';
import type { OwnerRelationRow, Profile } from '@gen-harness/contracts';
import { Icon, Skeleton } from '@gen-harness/ui';
import { fmtDM } from '../../lib/format';
import { openAskGen } from '../AskGenScreen';
import { asText, avatarText } from '../ownerModel';
import { OwnerError } from '../parts';
import { RemindButton } from './RemindButton';
import {
  askGenPrompt,
  channelLines,
  lastContactText,
  openValueText,
  profileSubtitle,
  relationLabel,
  recentEvents,
  scoreLines,
  scoreSentence,
  summaryLines,
  useOwnerProfile,
  type ScoreLine,
} from './relationsModel';
import './relations.css';

/** Ba điểm (độ nóng, rủi ro rời bỏ, tiềm năng): nhãn · số lớn · lời xu hướng (mũi tên + chữ — màu không đứng một mình). */
export function ScoreRows({ lines }: { lines: readonly ScoreLine[] }) {
  if (lines.length === 0) return null;
  return (
    <ul className="rel-scores" data-testid="profile-scores">
      {lines.map((s) => (
        <li key={s.dimension} className="rel-score" data-tone={s.tone} aria-label={scoreSentence(s)}>
          <span className="rel-score__label">{s.label}</span>
          <span className="rel-score__value">{s.value}</span>
          <span className="rel-score__trend">
            {s.trend === 'up' ? <Icon name="ph ph-arrow-up" size={12} /> : s.trend === 'down' ? <Icon name="ph ph-arrow-down" size={12} /> : s.trend === 'flat' ? <Icon name="ph ph-arrow-right" size={12} /> : null}
            {s.word}
          </span>
        </li>
      ))}
    </ul>
  );
}

/** Tóm tắt ≤ 5 dòng (chấm đầu dòng). */
export function SummaryList({ profile, testId = 'profile-summary' }: { profile: Profile | undefined; testId?: string }) {
  const lines = summaryLines(profile);
  if (lines.length === 0) return null;
  return (
    <ul className="rel-bullets" data-testid={testId}>
      {lines.map((l, i) => (
        <li key={i}>{l.text}</li>
      ))}
    </ul>
  );
}

function PanelSkeleton() {
  return (
    <div className="rel-panel__skel" aria-busy="true" aria-label="Đang tải hồ sơ" data-testid="profile-loading">
      <Skeleton width="55%" height={18} />
      <Skeleton width="100%" height={44} />
      <Skeleton width="100%" height={44} />
      <Skeleton width="100%" height={96} />
    </div>
  );
}

export interface ProfilePanelProps {
  personId: string;
  /** Dòng của danh sách đang chọn (tên hiện ngay khi hồ sơ còn tải; lần cuối liên lạc, cơ hội mở). */
  row?: OwnerRelationRow;
}

export function ProfilePanel({ personId, row }: ProfilePanelProps) {
  const q = useOwnerProfile(personId);
  const profile = q.data;
  const name = asText(profile?.person?.name) || asText(row?.name) || 'Khách';
  const subtitle = profileSubtitle(profile, asText(row?.subtitle));
  const scores = scoreLines(profile?.scores, row);
  const events = recentEvents(profile);
  const channels = channelLines(profile);
  const note = asText(profile?.owner_note).trim();
  const relation = relationLabel(profile?.person?.relation_to_owner);

  return (
    <section className="rel-panel" aria-label={`Hồ sơ của ${name}`} data-testid="profile-panel" data-person={personId}>
      <header className="rel-panel__head">
        <span className="kit-avatar rel-panel__avatar" aria-hidden>
          {avatarText(name)}
        </span>
        <div className="rel-panel__who">
          <h2 className="rel-panel__name">{name}</h2>
          {subtitle ? <span className="rel-panel__sub">{subtitle}</span> : null}
        </div>
      </header>

      {q.isPending ? (
        <PanelSkeleton />
      ) : q.isError ? (
        <OwnerError error={q.error} onRetry={() => void q.refetch()} retrying={q.isFetching} />
      ) : (
        <>
          <ScoreRows lines={scores} />

          {summaryLines(profile).length > 0 ? (
            <div className="rel-section">
              <h3 className="rel-section__title">Tóm tắt</h3>
              <SummaryList profile={profile} />
            </div>
          ) : null}

          {events.length > 0 ? (
            <div className="rel-section">
              <h3 className="rel-section__title">Gần đây</h3>
              <ol className="rel-timeline" data-testid="profile-timeline">
                {events.map((e) => (
                  <li key={e.id}>
                    <span className="rel-timeline__at">{fmtDM(e.at)}</span>
                    <span>{e.text}</span>
                  </li>
                ))}
              </ol>
            </div>
          ) : null}

          <dl className="rel-facts">
            {relation ? (
              <div className="rel-fact" data-testid="profile-relation">
                <dt>Quan hệ với Sếp</dt>
                <dd>{relation}</dd>
              </div>
            ) : null}
            {channels.length > 0 ? (
              <div className="rel-fact" data-testid="profile-channels">
                <dt>Kênh</dt>
                <dd>
                  {channels.map((c) => (
                    <span key={c.id}>{c.text}</span>
                  ))}
                </dd>
              </div>
            ) : null}
            {row ? (
              <>
                <div className="rel-fact">
                  <dt>Lần cuối liên lạc</dt>
                  <dd>{lastContactText(row.last_contact_days)}</dd>
                </div>
                <div className="rel-fact">
                  <dt>Cơ hội mở</dt>
                  <dd>{openValueText(row.open_value_vnd)}</dd>
                </div>
              </>
            ) : null}
          </dl>

          {note ? (
            <div className="rel-section" data-testid="profile-note">
              <h3 className="rel-section__title">Ghi chú của Sếp</h3>
              <p className="rel-note-text">{note}</p>
            </div>
          ) : null}
        </>
      )}

      <div className="rel-panel__actions">
        <button type="button" className="kit-btn kit-btn--primary" data-testid="profile-ask" onClick={() => openAskGen(askGenPrompt(name))}>
          Nhờ Gen soạn tin
        </button>
        <RemindButton title={`Liên hệ lại ${name}`} subject={{ type: 'person', id: personId }} testId="profile-remind" />
      </div>
      <Link to={`/profile?id=${encodeURIComponent(personId)}`} className="kit-link rel-panel__full" data-testid="profile-open-full">
        Mở hồ sơ đầy đủ
        <Icon name="ph ph-caret-right" size={14} />
      </Link>
    </section>
  );
}
