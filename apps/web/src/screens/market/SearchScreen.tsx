import { useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import type { SearchBulkAction } from '@gen-harness/contracts';
import { Button, Dialog, EmptyState, Icon } from '@gen-harness/ui';
import { CardError, InlineError, ScreenHead, SkeletonLines } from '../common';
import { errorText } from '../../lib/errorText';
import { fmtAgo, fmtInt } from '../../lib/format';
import { useUrlState } from '../../lib/uiStore';
import { BULK_ACTION_LABEL, CHANNEL_LABEL, eventTypeLabel } from './marketModel';
import { useSearch, useSearchBulk } from './queries';

const TIME_OPTIONS: Array<{ value: string; label: string }> = [
  { value: '', label: 'Mọi lúc' },
  { value: '7', label: '7 ngày' },
  { value: '30', label: '30 ngày' },
  { value: '90', label: '90 ngày' },
  { value: '180', label: '6 tháng' },
];

export function SearchScreen() {
  const [q, setQ] = useUrlState<string>('q', '');
  const [eventType, setEventType] = useUrlState<string>('et', '');
  const [channel, setChannel] = useUrlState<string>('ch', '');
  const [since, setSince] = useUrlState<string>('since', '');
  const [qDraft, setQDraft] = useState(q);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [bulkOpen, setBulkOpen] = useState(false);

  const dateFrom = useMemo(() => {
    if (!since) return undefined;
    const d = new Date(Date.now() - Number(since) * 86_400_000);
    return d.toISOString();
  }, [since]);

  const search = useSearch({ q: q || undefined, event_type: eventType || undefined, channel: channel || undefined, date_from: dateFrom, limit: 50 });
  const items = search.data?.items ?? [];
  const selectedItems = items.filter((it) => selected.has(it.person.id));

  const toggle = (id: string) =>
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  const toggleAll = () =>
    setSelected((prev) => (prev.size === items.length ? new Set() : new Set(items.map((it) => it.person.id))));

  return (
    <div className="screen">
      <ScreenHead
        title="Kho hội thoại"
        description="Tìm theo ý định, người, ngành hàng, khoảng giá, thời gian — không chỉ theo chữ. Mục đích là tìm ra mẫu, không chỉ tìm ra câu."
        maxWidth={700}
      />

      <div className="gh-card src-box">
        <form
          className="src-search"
          onSubmit={(e) => {
            e.preventDefault();
            setQ(qDraft.trim());
          }}
        >
          <Icon name="ph ph-brain" size={17} className="src-search__icon" />
          <input
            className="src-search__input"
            value={qDraft}
            onChange={(e) => setQDraft(e.target.value)}
            placeholder="những khách từng hỏi ván MDF trên 500 triệu nhưng chưa chốt…"
            aria-label="Tìm kiếm ngôn ngữ tự nhiên"
          />
          {search.data ? <span className="src-search__meta">{fmtInt(search.data.total)} kết quả</span> : null}
          <Button type="submit" variant="secondary" size="sm">
            Tìm
          </Button>
        </form>

        <div className="src-facets" role="group" aria-label="Bộ lọc facet">
          <FacetGroup label="Ý định" active={eventType} options={(search.data?.facets.event_type ?? []).map((f) => ({ value: f.value, label: `${eventTypeLabel(f.value)} · ${f.count}` }))} onChange={setEventType} />
          <FacetGroup label="Kênh" active={channel} options={(search.data?.facets.channel ?? []).map((f) => ({ value: f.value, label: `${CHANNEL_LABEL[f.value] ?? f.value} · ${f.count}` }))} onChange={setChannel} />
          <FacetGroup label="Thời gian" active={since} options={TIME_OPTIONS.filter((o) => o.value !== '')} onChange={setSince} />
        </div>
      </div>

      <section className="gh-card" aria-label="Kết quả tìm kiếm">
        <div className="gh-card__header">
          <div>
            <div className="gh-card__title">{search.data ? `${fmtInt(search.data.total)} đối tượng khớp` : 'Đang tìm…'}</div>
            <div className="gh-card__kicker">Kết quả là người và mẫu hành vi, không phải danh sách tin nhắn</div>
          </div>
          <Button variant="secondary" icon="ph ph-list-checks" disabled={selected.size === 0} onClick={() => setBulkOpen(true)}>
            Hành động hàng loạt ({selected.size})
          </Button>
        </div>
        {search.isPending ? (
          <SkeletonLines rows={6} />
        ) : search.isError ? (
          <CardError error={search.error} onRetry={() => void search.refetch()} retrying={search.isFetching} />
        ) : items.length === 0 ? (
          <EmptyState icon="ph ph-magnifying-glass" title="Không tìm thấy ai khớp" description="Thử bỏ bớt facet hoặc đổi từ khoá." />
        ) : (
          <div className="gh-table-scroll">
            <table className="gh-table w920" aria-label="Người khớp tìm kiếm">
              <thead>
                <tr>
                  <th style={{ width: 34 }}>
                    <input type="checkbox" checked={selected.size > 0 && selected.size === items.length} onChange={toggleAll} aria-label="Chọn tất cả" />
                  </th>
                  <th>Đối tượng</th>
                  <th style={{ width: 150 }}>Ý định gần nhất</th>
                  <th style={{ width: 90 }}>Lần khớp</th>
                  <th style={{ width: 110 }}>Gần nhất</th>
                  <th>Trích đoạn</th>
                </tr>
              </thead>
              <tbody>
                {items.map((it) => (
                  <tr key={it.person.id}>
                    <td>
                      <input type="checkbox" checked={selected.has(it.person.id)} onChange={() => toggle(it.person.id)} aria-label={`Chọn ${it.person.name}`} />
                    </td>
                    <td>
                      <Link to={`/profile?id=${encodeURIComponent(it.person.id)}`} className="dir-person-cell__name">
                        {it.person.name}
                      </Link>
                      {it.person.org_name ? <div className="dir-person-cell__org">{it.person.org_name}</div> : null}
                    </td>
                    <td>
                      <span className="mono-tag">{eventTypeLabel(it.last_event_type)}</span>
                    </td>
                    <td className="td-id">{fmtInt(it.match_count)}</td>
                    <td className="td-id">{fmtAgo(it.last_at)}</td>
                    <td className="src-snippet">{it.last_snippet ?? '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {bulkOpen ? <BulkDialog personIds={selectedItems.map((it) => it.person.id)} onClose={() => setBulkOpen(false)} onDone={() => setSelected(new Set())} /> : null}
    </div>
  );
}

function FacetGroup({ label, active, options, onChange }: { label: string; active: string; options: Array<{ value: string; label: string }>; onChange: (v: string) => void }) {
  if (options.length === 0) return null;
  return (
    <div className="src-facet-group" role="group" aria-label={label}>
      <span className="src-facet-group__label">{label}</span>
      {options.map((o) => (
        <button key={o.value} type="button" className="src-facet-chip" aria-pressed={active === o.value} onClick={() => onChange(active === o.value ? '' : o.value)}>
          {o.label}
        </button>
      ))}
    </div>
  );
}

function BulkDialog({ personIds, onClose, onDone }: { personIds: string[]; onClose: () => void; onDone: () => void }) {
  const bulk = useSearchBulk();
  const [action, setAction] = useState<SearchBulkAction>('tag');
  const [text, setText] = useState('');
  const [priority, setPriority] = useState('P3');

  return (
    <Dialog
      open
      onClose={onClose}
      width={420}
      title="Hành động hàng loạt"
      kicker={`${personIds.length} đối tượng đang chọn`}
      actions={
        <>
          <Button variant="secondary" onClick={onClose}>
            Huỷ
          </Button>
          <Button
            variant="primary"
            icon="ph ph-check"
            loading={bulk.isPending}
            disabled={!text.trim()}
            onClick={() =>
              bulk.mutate(
                { person_ids: personIds, action, text, priority: action === 'task' ? priority : undefined },
                {
                  onSuccess: () => {
                    onDone();
                    onClose();
                  },
                },
              )
            }
          >
            Áp dụng cho {personIds.length} người
          </Button>
        </>
      }
    >
      <div className="dlg-fields">
        <div className="dlg-list" role="list">
          {(Object.keys(BULK_ACTION_LABEL) as SearchBulkAction[]).map((a) => (
            <button key={a} type="button" className="sv-open" aria-pressed={action === a} onClick={() => setAction(a)}>
              <span className="sv-open__name">{BULK_ACTION_LABEL[a]}</span>
              {action === a ? <Icon name="ph ph-check" size={14} /> : null}
            </button>
          ))}
        </div>
        <label className="gh-field">
          <span className="gh-field__label">{action === 'tag' ? 'Nội dung ghi vào sổ tay' : 'Nội dung việc cần theo dõi'}</span>
          <textarea className="wb-textarea src-bulk-textarea" value={text} onChange={(e) => setText(e.target.value)} rows={3} />
        </label>
        {action === 'task' ? (
          <div className="dir-filter-row">
            <span className="dir-filter-row__label">Ưu tiên</span>
            <div className="dir-filter-row__opts" role="group" aria-label="Ưu tiên">
              {['P1', 'P2', 'P3'].map((p) => (
                <button key={p} type="button" className="dir-filter-row__opt" aria-pressed={priority === p} onClick={() => setPriority(p)}>
                  {p}
                </button>
              ))}
            </div>
          </div>
        ) : null}
        {bulk.isError ? <InlineError>{errorText(bulk.error)}</InlineError> : null}
      </div>
    </Dialog>
  );
}
