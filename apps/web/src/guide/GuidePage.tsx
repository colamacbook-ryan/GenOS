import { useEffect } from 'react';
import { Link } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { EmptyState, Icon } from '@gen-harness/ui';
import { useMe } from '../lib/queries';
import { api } from '../lib/api';
import { CardError, SkeletonLines } from '../screens/common';
import { ScreenTitle } from '../screens/ScreenPage';
import { BOSS_CHECKS_KEY, BOSS_CHECKS_PATH } from './bossChecksModel';
import { FOLLOW_UP_KEY, GUIDE, guideOrdinal, type GuideItem } from './guideContent';

/**
 * Trang "Hướng dẫn thiết lập" (`/guide`; v0.1.68: mọi việc ở đây đều KHÔNG bắt buộc, việc bắt buộc duy nhất — nguồn AI — nằm ở "Việc Sếp cần làm"): mọi việc tuỳ chọn 5–11 + (v0.1.39) Kết nối Facebook, Nối Gen-hub theo thứ tự, mỗi việc có vì sao cần, chuẩn bị gì,
 * các bước bấm theo đúng nhãn nút, và dấu hiệu đã xong. Xong hay chưa lấy từ `GET /setup/follow-up` (suy từ dữ
 * liệu thật) — làm xong ở đâu (form hướng dẫn hay màn Console) thì việc cũng tự đánh dấu xong. Số hiển thị là số thứ
 * tự trong danh sách (01…), không phải số bước. Đầu trang: thẻ "Việc Sếp cần làm" (`/guide/viec-sep`, F-74).
 */
export function GuidePage() {
  const me = useMe();
  // v0.1.28 (UX N9): việc thiết lập chỉ Owner làm — vai trò khác thấy lời giải thích, không phải "Không tải được" + Thử lại.
  const role = me.data?.role?.code;
  const nonOwner = !!role && role !== 'owner';
  const q = useQuery({ queryKey: FOLLOW_UP_KEY, queryFn: ({ signal }) => api.setup.followUp(signal), enabled: !!me.data && !nonOwner });
  const boss = useQuery({ queryKey: BOSS_CHECKS_KEY, queryFn: ({ signal }) => api.bossChecks.list(signal), enabled: !!me.data && !nonOwner });

  useEffect(() => {
    document.title = 'Hướng dẫn thiết lập · Gen-Harness';
  }, []);

  const doneOf = new Map((q.data ?? []).map((i) => [i.n, i.done]));
  const doneCount = GUIDE.filter((g) => doneOf.get(g.n)).length;
  const firstOpen = GUIDE.find((g) => !doneOf.get(g.n))?.n;
  // v0.1.68 (F031/F138): MỘT cách đếm — dòng bắt buộc (nguồn AI) là x/N của "Việc Sếp cần làm"; 9 việc dưới đây đều TUỲ CHỌN.
  // Chỉ nói "sẵn sàng" khi nguồn AI cũng đạt; chưa biết kết quả (đang tải / lỗi) thì không khẳng định gì.
  const aiKnown = typeof boss.data?.required_done === 'number' && typeof boss.data?.required_total === 'number';
  const aiOk = aiKnown && (boss.data?.required_done ?? 0) >= (boss.data?.required_total ?? 1);
  const allDone = doneCount === GUIDE.length;

  return (
    <div className="screen guide">
      <ScreenTitle
        title="Hướng dẫn thiết lập"
        description="Làm lần lượt từ trên xuống. Mỗi việc có nút mở đúng form cần điền; làm xong việc nào, việc đó tự đánh dấu — kể cả khi Sếp làm ở màn Console."
        maxWidth={640}
      />
      {nonOwner ? (
        <div className="gh-card">
          <EmptyState
            icon="ph ph-lock-simple"
            title="Việc kết nối do Owner làm"
            description="Kết nối kênh, chọn nhóm, sao lưu… chỉ Owner cài đặt được. Cần thêm gì, hãy nhắn Owner."
          />
        </div>
      ) : q.isPending ? (
        <div className="gh-card">
          <SkeletonLines rows={6} />
        </div>
      ) : q.isError ? (
        <div className="gh-card">
          <CardError error={q.error} onRetry={() => void q.refetch()} retrying={q.isFetching} />
        </div>
      ) : (
        <>
          <Link to={BOSS_CHECKS_PATH} className="guide-boss gh-card">
            <Icon name="ph ph-rocket-launch" size={18} className="guide-boss__icon" />
            <span className="guide-boss__body">
              <span className="guide-boss__title">Việc Sếp cần làm — nguồn AI chạy thật</span>
              <span className="guide-boss__sub">
                {typeof boss.data?.required_done === 'number' ? `Đã đạt ${boss.data.required_done}/${boss.data.required_total} dòng bắt buộc` : 'Chỉ cần ít nhất một nguồn AI chạy được — các kết nối khác không bắt buộc, kết quả được lưu lại'}
              </span>
            </span>
            <Icon name="ph ph-arrow-right" size={14} />
          </Link>
          <div className="guide-progress" role="status" data-gen-target="guide.progress">
            <div
              className="guide-progress__bar"
              role="progressbar"
              aria-label="Tiến độ kết nối"
              aria-valuemin={0}
              aria-valuemax={GUIDE.length}
              aria-valuenow={doneCount}
            >
              <span style={{ width: `${(doneCount / GUIDE.length) * 100}%` }} />
            </div>
            <span className="guide-progress__text">
              {allDone && aiOk ? 'Đã xong tất cả — hệ thống sẵn sàng làm việc.' : `Đã xong ${doneCount}/${GUIDE.length} việc`}
            </span>
            <span className="guide-progress__note">
              {allDone && aiKnown && !aiOk
                ? 'Mọi việc tuỳ chọn đã xong, nhưng nguồn AI chưa chạy thật — xem "Việc Sếp cần làm" ở trên.'
                : 'Các việc này đều không bắt buộc.'}
            </span>
          </div>
          <ol className="guide-list">
            {GUIDE.map((g) => (
              <GuideCard key={g.n} g={g} done={!!doneOf.get(g.n)} open={g.n === firstOpen} afterDone={g.after ? !!doneOf.get(g.after) : true} />
            ))}
          </ol>
        </>
      )}
    </div>
  );
}

function GuideCard({ g, done, open, afterDone }: { g: GuideItem; done: boolean; open: boolean; afterDone: boolean }) {
  return (
    <li className="guide-card gh-card" data-done={done || undefined} data-gen-target={`guide.item:${g.n}`}>
      <details open={open}>
        <summary className="guide-card__head">
          <span className="guide-card__num mono">{guideOrdinal(g.n)}</span>
          <span className="guide-card__title">{g.title}</span>
          {done ? (
            <span className="guide-chip guide-chip--done">
              <Icon name="ph ph-check" size={12} /> Đã xong
            </span>
          ) : (
            <span className="guide-chip">Chưa làm</span>
          )}
          <span className="guide-chip guide-chip--opt">Không bắt buộc</span>
          <Icon name="ph ph-caret-down" size={13} className="guide-card__caret" />
        </summary>
        <div className="guide-card__body">
          <p className="guide-card__why">{g.why}</p>
          {!afterDone && !done ? (
            <p className="guide-card__note" role="note">
              <Icon name="ph ph-info" size={13} /> Nên làm việc {guideOrdinal(g.after ?? g.n)} trước.
            </p>
          ) : null}
          <div className="guide-card__section">Cần chuẩn bị</div>
          <ul className="guide-card__prep">
            {g.prepare.map((p) => (
              <li key={p}>{p}</li>
            ))}
          </ul>
          <div className="guide-card__section">Các bước</div>
          <ol className="guide-card__steps">
            {g.steps.map((s) => (
              <li key={s}>{s}</li>
            ))}
          </ol>
          <p className="guide-card__done">
            <Icon name="ph ph-flag" size={13} /> <strong>Xong khi:</strong> {g.doneWhen}
          </p>
          <div className="guide-card__actions">
            <Link to={g.doTo ?? `/guide/${g.n}`} className={`gh-btn ${done ? 'gh-btn--secondary' : 'gh-btn--primary'} btn-30`} data-gen-target={`guide.item.do:${g.n}`}>
              {g.doTo ? (g.doLabel ?? 'Mở màn làm việc này') : done ? 'Làm lại / chỉnh' : 'Làm bước này'}
              <Icon name="ph ph-arrow-right" size={13} />
            </Link>
            {g.doTo === g.console.to ? null : (
              <Link to={g.console.to} className="guide-card__alt">
                Hoặc làm ở {g.console.label}
              </Link>
            )}
          </div>
        </div>
      </details>
    </li>
  );
}
