import type { CoachDismissedItem, CoachPrefs, CoachPrefsPatch } from '@gen-harness/contracts';
import { Button, Icon, SelectField, Switch } from '@gen-harness/ui';
import { DefaultControls } from '../../defaults/ResetButton';
import { COACH_DISMISSED_TITLE, COACH_PREFS_TITLE, HOURS, dismissedItemKey, hourLabel, levelMeta } from '../../gen/coachModel';
import { useCoachItemAction, useCoachPrefs, useIsOwner, usePatchCoachPrefs } from '../../gen/coachQueries';
import { errorDetail, errorText } from '../../lib/errorText';
import { toast } from '../../lib/toast';
import { CardError, InlineError, Panel, SkeletonLines } from '../common';

const LESSON_OPTIONS = [0, 1, 2].map((n) => ({ value: String(n), label: n === 0 ? '0 — không có bài học' : `${n} bài` }));
const HOUR_OPTIONS = HOURS.map((h) => ({ value: String(h), label: hourLabel(h) }));

/**
 * v0.1.54 — Cài đặt › Bộ não AI › "Gen hướng dẫn": công tắc Bật hướng dẫn, Chuông nhắc, Số bài mỗi ngày (0–2), Giờ yên lặng
 * (từ/đến, giờ của tổ chức) và danh sách "Việc Sếp đã chọn không dùng" — mỗi dòng có "Bật lại". CHỈ Owner (vai trò khác không
 * vẽ, không gọi `/gen/coach/prefs`); không cần mã PIN. Mỗi thay đổi lưu ngay (PATCH).
 */
export function GenCoachCard() {
  if (!useIsOwner()) return null;
  return <GenCoachPanel />;
}

function GenCoachPanel() {
  const q = useCoachPrefs();
  return (
    <div id="gen-coach" className="gen-coach-wrap">
      <Panel
        title={COACH_PREFS_TITLE}
        genTarget="system.brain.coach"
        kicker="Việc cần làm, mẹo và bài học Gen nhắc Sếp mỗi ngày — chỉ khuyên, không ép"
        label={COACH_PREFS_TITLE}
        bodyClass="gh-card__body"
        aside={<DefaultControls itemKey="coach" />}
      >
        {q.isPending ? (
          <SkeletonLines rows={4} padding="0" />
        ) : q.isError ? (
          <CardError error={q.error} onRetry={() => void q.refetch()} retrying={q.isFetching} />
        ) : (
          <CoachPrefsForm prefs={q.data} />
        )}
      </Panel>
    </div>
  );
}

function CoachPrefsForm({ prefs }: { prefs: CoachPrefs }) {
  const patch = usePatchCoachPrefs();
  const save = (body: CoachPrefsPatch) => {
    patch.reset();
    patch.mutate(body, { onSuccess: () => toast('Đã lưu cài đặt Gen hướng dẫn', 'ok') });
  };
  return (
    <div className="coach-set">
      {patch.isError ? <InlineError detail={errorDetail(patch.error)}>{errorText(patch.error)}</InlineError> : null}

      <div className="coach-set__row">
        <span className="coach-set__label">
          Bật hướng dẫn
          <span className="coach-set__hint">Thẻ “Hôm nay của Sếp” ở đầu khung Gen và chấm đỏ ở nút Gen</span>
        </span>
        <Switch checked={prefs.enabled} label="Bật hướng dẫn" disabled={patch.isPending} onChange={(v) => save({ enabled: v })} />
      </div>

      <div className="coach-set__row">
        <span className="coach-set__label">
          Chuông nhắc
          <span className="coach-set__hint">Mỗi ngày tối đa một thông báo; không đẩy sang Telegram</span>
        </span>
        <Switch checked={prefs.bell} label="Chuông nhắc" disabled={patch.isPending} onChange={(v) => save({ bell: v })} />
      </div>

      <SelectField
        label="Số bài mỗi ngày"
        hint="0 = chỉ nhắc việc, không có bài học"
        value={String(prefs.lessons_per_day)}
        options={LESSON_OPTIONS}
        disabled={patch.isPending}
        onChange={(e) => save({ lessons_per_day: Number(e.target.value) })}
      />

      <div className="coach-set__quiet" role="group" aria-label="Giờ yên lặng">
        <SelectField
          label="Giờ yên lặng từ"
          value={String(prefs.quiet_start)}
          options={HOUR_OPTIONS}
          disabled={patch.isPending}
          onChange={(e) => save({ quiet_start: Number(e.target.value) })}
        />
        <SelectField
          label="đến"
          hint="Giờ của tổ chức; trong giờ yên lặng Gen không gửi chuông"
          value={String(prefs.quiet_end)}
          options={HOUR_OPTIONS}
          disabled={patch.isPending}
          onChange={(e) => save({ quiet_end: Number(e.target.value) })}
        />
      </div>

      <DismissedList items={prefs.dismissed} />
    </div>
  );
}

function DismissedList({ items }: { items: CoachDismissedItem[] }) {
  const restore = useCoachItemAction();
  const list = Array.isArray(items) ? items : [];
  return (
    <div className="coach-set__block" data-testid="coach-dismissed">
      <h3 className="coach-set__sub">{COACH_DISMISSED_TITLE}</h3>
      {restore.isError ? <InlineError detail={errorDetail(restore.error)}>{errorText(restore.error)}</InlineError> : null}
      {list.length === 0 ? (
        <p className="coach-set__hint">Chưa có việc nào bị tắt — Gen sẽ nhắc mọi việc cần thiết.</p>
      ) : (
        <ul className="coach-set__dismissed">
          {list.map((d) => (
            <li key={d.key} data-testid="coach-dismissed-row">
              <span className="coach-set__dismissed-title">
                {d.title} <span className="coach-set__hint">· {levelMeta(d.level).label}</span>
              </span>
              <Button
                variant="secondary"
                size="sm"
                icon="ph ph-arrow-counter-clockwise"
                disabled={restore.isPending}
                onClick={() => {
                  restore.reset();
                  restore.mutate({ itemKey: dismissedItemKey(d), body: { action: 'restore' } });
                }}
              >
                Bật lại
              </Button>
            </li>
          ))}
        </ul>
      )}
      <p className="coach-set__hint">
        <Icon name="ph ph-info" size={12} /> Việc khẩn (P0) không tắt được.
      </p>
    </div>
  );
}
