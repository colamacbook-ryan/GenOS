import { useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { EmptyState } from '@gen-harness/ui';
import { OwnerEmbed } from '../shell/OwnerEmbed';
import { BriefHero } from '../owner/intel/BriefBlocks';
import { DEFAULT_TZ } from '../lib/format';
import { useOrgTimezone } from '../lib/permissions';
import { useOwnerBrief } from '../owner/queries';
import { FirstRunCard } from '../owner/parts';
import { CardError, Panel, SkeletonLines } from '../screens/common';
import { useIntelSetup } from './intelQueries';
import { setupPendingText } from './intelModel';
import {
  groupByLevel,
  redCount,
  signalKindLabel,
  txt,
  when,
  type AskItem,
  type SignalItem,
} from './intelHubModel';
import { useAsks, useSignals } from './intelHubQueries';
import { SignalDrawer } from './SignalDrawer';
import { SignalVerdict } from './SignalVerdict';

const OPEN_FILTER = { level: '', status: 'open', kind: '' };

/**
 * v0.1.79 (G1) — tab "Hôm nay" của Sếp, trang chủ: (1) Thiết lập chưa xong thì nhắc một dòng, (2) 3 dòng đầu của Cần quyết kèm link sang
 * /decide, (3) tín hiệu đang mở theo mức 🔴 Khẩn · 🟠 Đầu bản tin · ⚪ Để biết — mỗi dòng có "Việc Sếp cần làm" (dòng hành động của tín hiệu)
 * và nút Đúng/Sai. Bấm dòng mở ngăn chi tiết (`?tin=`). Sự cố hệ thống KHÔNG nằm ở đây (máy chủ đã lọc): chúng ở chip sức khoẻ và chuông.
 */
export function TodayTab() {
  const [params, setParams] = useSearchParams();
  const selected = params.get('tin');
  const tz = useOrgTimezone() || DEFAULT_TZ;
  const [briefId, setBriefId] = useState<string | undefined>(undefined);
  const picked = useOwnerBrief(briefId);
  const latest = useOwnerBrief();
  const signals = useSignals(OPEN_FILTER);
  const items = signals.data?.items ?? [];
  const firstRun = latest.data?.channels_connected === 0 && signals.isSuccess && items.length === 0;
  const select = (id: string | null) => {
    const next = new URLSearchParams(params);
    if (id) next.set('tin', id);
    else next.delete('tin');
    setParams(next);
  };
  return (
    <div className={`ih-split${selected ? ' ih-split--open' : ''}`} data-testid="intel-today">
      <div className="ih-stack">
        <OwnerEmbed>
          <BriefHero brief={picked} selectedId={briefId} onSelect={setBriefId} tz={tz} />
        </OwnerEmbed>
        <SetupBanner />
        <DecideTop />
        {firstRun ? (
          <OwnerEmbed>
            <FirstRunCard />
          </OwnerEmbed>
        ) : (
          <Panel
            title="Tín hiệu đang mở"
            kicker={signals.isSuccess ? (items.length ? `${items.length} tín hiệu${redCount(items) ? ` · ${redCount(items)} khẩn` : ''}` : 'Chưa có gì cần để ý') : 'Gen nghe được từ nhóm chat'}
            label="Tín hiệu đang mở"
            genTarget="intel.today.signals"
            flush
            aside={
              <Link to="/intel?tab=signals" className="gh-btn gh-btn--secondary btn-27">
                Xem mọi tín hiệu
              </Link>
            }
          >
            {signals.isPending ? (
              <SkeletonLines rows={5} />
            ) : signals.isError ? (
              <div className="ih-pad">
                <CardError error={signals.error} onRetry={() => void signals.refetch()} retrying={signals.isFetching} />
              </div>
            ) : items.length === 0 ? (
              <EmptyState
                icon="ph ph-shield-check"
                title="Không có tín hiệu nào đang mở."
                description={signals.data.hidden_pin_n > 0 ? `Còn ${signals.data.hidden_pin_n} tín hiệu riêng tư — nhập mã PIN để xem.` : 'Khi Gen nghe thấy điều đáng để ý, nó hiện ở đây.'}
              />
            ) : (
              groupByLevel(items).map((g) => (
                <LevelGroup key={g.level} g={g} selected={selected} onOpen={select} />
              ))
            )}
          </Panel>
        )}
      </div>
      {selected ? <SignalDrawer key={selected} id={selected} onClose={() => select(null)} /> : null}
    </div>
  );
}

/** Nhắc một dòng khi "Thiết lập Tình báo" chưa xong — dẫn sang tab Thiết lập. Lỗi đọc trạng thái thì im (tab Thiết lập tự báo lỗi). */
export function SetupBanner() {
  const q = useIntelSetup(true);
  if (!q.data || q.data.ready) return null;
  return (
    <div className="intel-setup-banner" role="status" data-testid="intel-setup-banner" data-gen-target="intel.today.setup_banner">
      <span className="intel-setup-banner__text">
        Thiết lập Tình báo chưa xong: {setupPendingText(q.data)}. Nhóm chưa gán bối cảnh được xử lý thận trọng (ít báo); lừa đảo và an toàn vẫn
        báo bình thường.
      </span>
      <Link to="/intel?tab=setup" className="gh-btn gh-btn--secondary btn-27">
        Làm ngay
      </Link>
    </div>
  );
}

/** Ba dòng đầu của hàng Cần quyết (`GET /asks`) + link sang /decide. Chỉ đọc — duyệt/xử lý ở trang Cần quyết. */
function DecideTop() {
  const q = useAsks();
  const items = q.data?.items ?? [];
  const total = items.length + (q.data?.hidden_pin_n ?? 0);
  return (
    <Panel
      title={`Cần Sếp quyết${q.isSuccess ? ` (${total})` : ''}`}
      kicker="Ba việc đầu đang chờ chữ ký của Sếp"
      label="Cần Sếp quyết"
      genTarget="intel.today.decide"
      flush
      aside={
        <Link to="/decide" className="gh-btn gh-btn--secondary btn-27" data-testid="intel-decide-all">
          Xem tất cả
        </Link>
      }
    >
      {q.isPending ? (
        <SkeletonLines rows={2} />
      ) : q.isError ? (
        <div className="ih-pad">
          <CardError error={q.error} onRetry={() => void q.refetch()} retrying={q.isFetching} />
        </div>
      ) : items.length === 0 ? (
        <p className="muted-note ih-pad">{q.data.hidden_pin_n > 0 ? `Còn ${q.data.hidden_pin_n} mục riêng tư — nhập mã PIN ở Cần quyết để xem.` : 'Không có gì đang chờ Sếp quyết.'}</p>
      ) : (
        <ul className="ih-list" aria-label="Cần Sếp quyết">
          {items.slice(0, 3).map((a) => (
            <AskRow key={a.id} a={a} />
          ))}
        </ul>
      )}
    </Panel>
  );
}

function AskRow({ a }: { a: AskItem }) {
  return (
    <li className="ih-item" data-testid="intel-ask-row">
      <div className="ih-item__main">
        <span className="ih-item__title">{txt(a.summary) || 'Việc cần quyết'}</span>
        <span className="muted-note">{[txt(a.code), when(a.expires_at) ? `hạn ${when(a.expires_at)}` : ''].filter(Boolean).join(' · ')}</span>
      </div>
      <Link to="/decide" className="gh-btn gh-btn--secondary btn-27">
        Xem
      </Link>
    </li>
  );
}

function LevelGroup({ g, selected, onOpen }: { g: ReturnType<typeof groupByLevel>[number]; selected: string | null; onOpen: (id: string) => void }) {
  if (g.items.length === 0) return null;
  return (
    <section className="ih-level" aria-label={`${g.label} (${g.items.length})`} data-testid={`intel-level-${g.level}`}>
      <h3 className="ih-level__head">
        <span aria-hidden>{g.dot}</span> {g.label} ({g.items.length}) <span className="muted-note ih-level__hint">{g.hint}</span>
      </h3>
      <ul className="ih-list">
        {g.items.map((s) => (
          <SignalLine key={s.id} s={s} active={s.id === selected} onOpen={() => onOpen(s.id)} />
        ))}
      </ul>
    </section>
  );
}

function SignalLine({ s, active, onOpen }: { s: SignalItem; active: boolean; onOpen: () => void }) {
  return (
    <li className={`ih-item${active ? ' ih-item--active' : ''}`} data-testid="intel-signal-line">
      <div className="ih-item__main">
        <button type="button" className="ih-linkbtn ih-item__title" onClick={onOpen}>
          {txt(s.title)}
        </button>
        <span className="muted-note">{[signalKindLabel(s.kind), when(s.created_at)].filter(Boolean).join(' · ')}</span>
        {txt(s.suggested_action) ? (
          <span className="ih-action">
            <strong>Việc Sếp cần làm:</strong> {txt(s.suggested_action)}
          </span>
        ) : null}
      </div>
      <SignalVerdict id={s.id} title={txt(s.title)} verdict={s.feedback_verdict ?? null} compact />
    </li>
  );
}
