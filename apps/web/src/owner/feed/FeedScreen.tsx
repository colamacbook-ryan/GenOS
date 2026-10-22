/**
 * v0.1.59 (G4) — `/owner/thong-tin`: màn Thông tin. Câu hỏi của Sếp: "Tin nào quan trọng, trả lời thế nào?"
 *
 * Máy tính (≥ 1024px): trái 5/12 cột danh sách, phải 7/12 cột ngữ cảnh của tin đang chọn (FeedDetail). Máy tính bảng / điện thoại: chỉ danh sách;
 * bấm một dòng mở chi tiết trong ngăn (toàn màn hình trên điện thoại, nút "Quay lại").
 * Địa chỉ: `?tin=<feed item id>` mở sẵn một tin (hợp đồng với Tình báo) — đóng ngăn thì bỏ tham số.
 *
 * Danh sách (`GET /owner/feed`): nút lọc Quan trọng (mặc định) · Tất cả · Cơ hội · Cảnh báo · Chờ duyệt, số đếm lấy từ `counts`. Mỗi dòng: huy hiệu
 * MỨC (Khẩn / Quan trọng / Thường — máy chủ tự tính, không theo `priority`) · nhãn việc · người · nhóm · tóm tắt · kênh + giờ. "Xem thêm" theo
 * `next_offset`. Chân: "Gen đã lọc N tin rác/trùng trong 7 ngày".
 * Trạng thái: chưa có kênh ⇒ đúng MỘT thẻ "Nối Zalo" · mục Quan trọng rỗng ⇒ "Không có tin cần Sếp để ý. Gen đã lọc N tin." + nút "Xem tất cả".
 * Gen chỉ ĐỀ XUẤT: Trả lời chỉ tạo nháp, gửi thật qua "Duyệt và gửi" có mã PIN (xem FeedDetail / ReplyBox).
 */
import { useEffect, useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import type { FeedItem, FeedView } from '@gen-harness/contracts';
import { Badge, Card, DetailSheet, Icon, ListRow, Skeleton, useMediaQuery } from '@gen-harness/ui';
import { asText } from '../ownerModel';
import { FirstRunCard, ListSkeleton, OwnerEmpty, OwnerError } from '../parts';
import { useOwnerFeed } from '../queries';
import { FeedDetail } from './FeedDetail';
import { FEED_VIEWS, emptyText, filteredText, headline, mergePages, rowMeta, rowTime, safeCount } from './feedModel';
import './feed.css';

const DESKTOP = '(min-width: 1024px)';
const NOTE_MS = 8_000;

function FeedRow({ item, selected, onSelect }: { item: FeedItem; selected: boolean; onSelect: (id: string) => void }) {
  const summary = asText(item.summary);
  const meta = rowMeta(item);
  return (
    <ListRow
      as="li"
      data-testid="feed-row"
      selected={selected}
      chevron={false}
      onClick={() => onSelect(asText(item.id))}
      title={
        <span className="feed-row__head">
          <Badge kind="level" level={item.importance}>
            {asText(item.importance)}
          </Badge>
          <span className="feed-row__label">{asText(item.title)}</span>
        </span>
      }
      meta={
        <>
          {summary ? <span className="feed-row__summary" title={summary}>{summary}</span> : null}
          {meta ? <span className="feed-row__meta">{meta}</span> : null}
        </>
      }
      right={<span className="feed-row__time">{rowTime(item.created_at)}</span>}
    />
  );
}

export function FeedScreen(): JSX.Element {
  const [params, setParams] = useSearchParams();
  const wide = useMediaQuery(DESKTOP);
  const tin = params.get('tin');
  const [view, setView] = useState<FeedView>('important');
  const [note, setNote] = useState('');
  const [hidden, setHidden] = useState<ReadonlySet<string>>(() => new Set());
  const [autoId, setAutoId] = useState<string | null>(null);
  const feed = useOwnerFeed(view);

  useEffect(() => {
    document.title = 'Thông tin · Gen-Harness';
  }, []);
  useEffect(() => {
    if (!note) return;
    const t = setTimeout(() => setNote(''), NOTE_MS);
    return () => clearTimeout(t);
  }, [note]);

  const pages = feed.data?.pages;
  const first = pages?.[0];
  const items = useMemo(() => mergePages(pages).filter((i) => !hidden.has(asText(i.id))), [pages, hidden]);

  // Máy tính: chọn sẵn tin đầu tiên (giữ nguyên khi có tin mới chen lên đầu — để Sếp không mất ô đang soạn); tin được chọn biến mất thì chọn lại.
  useEffect(() => {
    if (!wide || tin || !feed.isSuccess) return;
    if (autoId && items.some((i) => asText(i.id) === autoId)) return;
    setAutoId(items[0] ? asText(items[0].id) : null);
  }, [wide, tin, feed.isSuccess, items, autoId]);

  const selectedId = tin ?? (wide ? autoId : null);
  const selectedItem = selectedId ? items.find((i) => asText(i.id) === selectedId) : undefined;

  const editTin = (id: string | null, replace: boolean) =>
    setParams(
      (prev) => {
        const n = new URLSearchParams(prev);
        if (id) n.set('tin', id);
        else n.delete('tin');
        return n;
      },
      { replace },
    );
  const pickView = (v: FeedView) => {
    setView(v);
    setAutoId(null);
    if (tin) editTin(null, true);
  };
  const resolve = (id: string, message: string) => {
    setHidden((prev) => new Set(prev).add(id));
    setNote(message);
    setAutoId(null);
    if (tin) editTin(null, true);
  };

  if (first?.channels_connected === 0) {
    return (
      <div className="owner-screen feed" data-testid="owner-frame-feed">
        <FirstRunCard />
      </div>
    );
  }

  const h = headline(first?.counts);
  const filtered = safeCount(first?.filtered_7d?.filtered);
  const foot = filteredText(first?.filtered_7d?.filtered);
  const showSide = wide && (!!selectedId || feed.isPending);

  const list = feed.isPending ? (
    <ListSkeleton rows={6} label="Đang tải dòng tin" />
  ) : feed.isError ? (
    <OwnerError error={feed.error} onRetry={() => void feed.refetch()} retrying={feed.isFetching} />
  ) : items.length === 0 ? (
    <div data-testid="feed-empty">
      <OwnerEmpty
        icon="ph ph-chat-text"
        title={emptyText(view, filtered)}
        actions={
          view === 'important' ? (
            <button type="button" className="kit-btn kit-btn--secondary" data-testid="feed-show-all" onClick={() => pickView('all')}>
              Xem tất cả
            </button>
          ) : undefined
        }
      />
    </div>
  ) : (
    <>
      <ul className="kit-list feed-list" aria-label="Dòng tin" data-testid="feed-list">
        {items.map((it) => (
          <FeedRow key={asText(it.id)} item={it} selected={asText(it.id) === selectedId} onSelect={(id) => editTin(id, false)} />
        ))}
      </ul>
      {feed.hasNextPage ? (
        <div className="feed-more">
          <button type="button" className="kit-btn kit-btn--secondary" data-testid="feed-more" disabled={feed.isFetchingNextPage} onClick={() => void feed.fetchNextPage()}>
            {feed.isFetchingNextPage ? 'Đang tải…' : 'Xem thêm'}
          </button>
        </div>
      ) : null}
    </>
  );

  return (
    <div className="owner-screen feed" data-testid="owner-frame-feed">
      <div className="kit-grid">
        <div className={showSide ? 'kit-c5' : 'kit-c12'}>
          <Card padded={false} className="feed-card" aria-label="Dòng tin" data-testid="feed-list-card">
            <div className="feed-head">
              {first ? (
                <p className="feed-head__line" data-testid="feed-headline">
                  <span className="feed-head__n">{h.total}</span>
                  <span className="feed-head__t">tin cần Sếp để ý</span>
                  {h.breakdown ? <span className="feed-head__d">{h.breakdown}</span> : null}
                </p>
              ) : feed.isPending ? (
                <Skeleton width="60%" height={28} />
              ) : null}
              <div className="kit-chips" role="group" aria-label="Lọc tin">
                {FEED_VIEWS.map((d) => {
                  const n = first?.counts ? safeCount(first.counts[d.countKey]) : undefined;
                  return (
                    <button
                      key={d.view}
                      type="button"
                      className="kit-chip"
                      aria-pressed={d.view === view}
                      data-testid={`feed-chip-${d.view}`}
                      onClick={() => pickView(d.view)}
                    >
                      {d.label}
                      {n !== undefined ? <span className="kit-chip__n">{n}</span> : null}
                    </button>
                  );
                })}
              </div>
              {note ? (
                <p className="feed-note" role="status" data-testid="feed-list-note">
                  {note}
                </p>
              ) : null}
            </div>
            <div className="feed-body">{list}</div>
            {foot ? (
              <p className="feed-foot" data-testid="feed-footer">
                <Icon name="ph ph-check" size={16} />
                {foot}
              </p>
            ) : null}
          </Card>
        </div>
        {showSide ? (
          <div className="kit-c7 feed-side">
            {selectedId ? (
              <Card padded={false} className="feed-card feed-pane" data-testid="feed-pane">
                <FeedDetail key={selectedId} id={selectedId} item={selectedItem} onResolved={(m) => resolve(selectedId, m)} />
              </Card>
            ) : (
              <Card state="loading" className="feed-card" />
            )}
          </div>
        ) : null}
      </div>

      {!wide ? (
        <DetailSheet
          open={!!tin}
          title={asText(selectedItem?.title) || 'Chi tiết tin'}
          onClose={() => editTin(null, true)}
          className="feed-sheet"
          data-testid="feed-sheet"
        >
          {tin ? (
            <div className="kit-scope feed-sheet__body">
              <FeedDetail key={tin} id={tin} item={selectedItem} onResolved={(m) => resolve(tin, m)} />
            </div>
          ) : null}
        </DetailSheet>
      ) : null}
    </div>
  );
}
