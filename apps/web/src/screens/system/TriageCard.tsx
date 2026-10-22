import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { Button, Switch, TextField } from '@gen-harness/ui';
import { DefaultControls } from '../../defaults/ResetButton';
import { errorText } from '../../lib/errorText';
import { useMe } from '../../lib/queries';
import { CardError, InlineError, Panel, SkeletonLines } from '../common';
import { useSetTriageSettings, useTriageSettings, useTriageSummary } from '../queue/queries';
import { TRIAGE_LEVELS, triageLevelOf, type TriageLevelKey } from './triageModel';

/**
 * Lọc đầu Hộp thư (v0.1.25, ROADMAP Đợt C1): job nền đánh dấu mục trùng, rác và chấm điểm 0–100 (Jev khi có, quy tắc
 * khi không). Owner bật/tắt, đặt ngưỡng điểm, chọn có dùng Jev; vai trò khác chỉ xem. Mọi thay đổi ghi Nhật ký hành động.
 * v0.1.43 (F-30): thẻ "Lọc tin" — mức Thấp/Vừa/Cao (min_score 15/30/50); Jev và ngưỡng điểm số vào "Nâng cao".
 */
export function TriageCard() {
  const me = useMe();
  const isOwner = me.data?.role?.code === 'owner';
  const settings = useTriageSettings();
  const summary = useTriageSummary(7);
  const save = useSetTriageSettings();
  const [score, setScore] = useState('');
  useEffect(() => {
    if (settings.data) setScore(String(settings.data.min_score));
  }, [settings.data]);
  const n = Number(score);
  const scoreValid = score.trim() !== '' && Number.isInteger(n) && n >= 0 && n <= 100;
  const s = summary.data;

  const level = settings.data ? triageLevelOf(settings.data.min_score) : null;
  // "Chỉ hiển thị không bao giờ ghi": chỉ PATCH khi Owner chọn mức KHÁC mức đang nhấn.
  const pickLevel = (key: TriageLevelKey) => {
    const target = TRIAGE_LEVELS.find((l) => l.key === key);
    if (!isOwner || !target || key === level || save.isPending) return;
    save.mutate({ min_score: target.min_score });
  };

  return (
    <Panel title="Lọc tin" kicker="Đánh dấu tin trùng, rác, điểm thấp — bật 'Ẩn rác & trùng' ở Hộp thư để ẩn" label="Lọc tin" genTarget="system.brain.triage" bodyClass="jev-body" aside={isOwner ? <DefaultControls itemKey="triage" /> : undefined}>
      {settings.isPending ? (
        <SkeletonLines rows={3} padding="0" />
      ) : settings.isError ? (
        <CardError error={settings.error} onRetry={() => void settings.refetch()} retrying={settings.isFetching} />
      ) : (
        <>
          <div className="triage-row">
            <Switch
              checked={settings.data.enabled}
              label="Bật lọc tin"
              disabled={!isOwner || save.isPending}
              onChange={(v) => save.mutate({ enabled: v })}
            />
            <span>Bật lọc tin</span>
          </div>
          <div className="triage-row" role="group" aria-label="Mức lọc">
            <span>Mức lọc:</span>
            {TRIAGE_LEVELS.map((l) => (
              <Button
                key={l.key}
                size="sm"
                variant={level === l.key ? 'primary' : 'secondary'}
                aria-pressed={level === l.key}
                title={`${l.hint} (từ ${l.min_score} điểm)`}
                disabled={!isOwner || save.isPending}
                onClick={() => pickLevel(l.key)}
              >
                {l.label}
              </Button>
            ))}
          </div>
          {/* Lọc chỉ ĐÁNH DẤU; Hộp thư chỉ ẩn khi bật công tắc "Ẩn rác & trùng" (mặc định tắt) — dẫn thẳng tới đó. */}
          <p className="muted-note">
            Lọc tin chỉ đánh dấu, không tự ẩn.{' '}
            <Link to="/inbox?hide=1" className="sys-link">
              Mở Hộp thư với "Ẩn rác &amp; trùng" đang bật
            </Link>
          </p>
          {level === 'custom' ? (
            <p className="muted-note">Đang dùng ngưỡng tuỳ chỉnh ({settings.data.min_score} điểm) — xem Nâng cao</p>
          ) : null}
          {!isOwner ? <p className="muted-note">Chỉ Sếp (Owner) thay đổi được cấu hình này.</p> : null}
          {save.isError ? <InlineError>{errorText(save.error)}</InlineError> : null}
          {s ? (
            <p className="jev-note" role="status">
              7 ngày: {s.total} mục đã lọc · {s.duplicates} trùng · {s.spam} rác · {s.low_score} điểm thấp · {s.pending} chờ lọc
            </p>
          ) : null}
          <details className="brain-advanced">
            <summary>Nâng cao — Jev và ngưỡng điểm</summary>
            <div className="triage-row">
              <Switch
                checked={settings.data.use_jev}
                label="Dùng Jev để chấm"
                disabled={!isOwner || save.isPending}
                onChange={(v) => save.mutate({ use_jev: v })}
              />
              <span>Dùng Jev để chấm (không có Jev thì dùng quy tắc)</span>
            </div>
            <form
              className="jev-actions"
              onSubmit={(e) => {
                e.preventDefault();
                if (scoreValid && isOwner) save.mutate({ min_score: n });
              }}
            >
              <TextField label="Ngưỡng điểm (0–100)" type="number" value={score} disabled={!isOwner} onChange={(e) => setScore(e.target.value)} />
              {isOwner ? (
                <Button variant="secondary" type="submit" className="btn-27" disabled={!scoreValid || n === settings.data.min_score} loading={save.isPending}>
                  Lưu ngưỡng
                </Button>
              ) : null}
            </form>
            {s?.jev.count ? (
              <p className="jev-note">
                7 ngày: Jev {s.jev.count} lượt{s.jev.avg_latency_ms != null ? `, ~${s.jev.avg_latency_ms} ms` : ''}
              </p>
            ) : null}
          </details>
        </>
      )}
    </Panel>
  );
}
