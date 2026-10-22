/**
 * v0.1.59 (G4) — `/owner/quan-he`: màn Quan hệ. Câu hỏi của Sếp: "Khách nào cần chăm, ai nối với ai?"
 *
 * Bốn danh sách chọn bằng nút lọc (có số đếm): Cần chăm (mặc định) · Khách nóng · Cầu nối · Cung–cầu — lấy từ `GET /owner/relations?list=…`.
 * Máy tính (≥ 1024px): bảng 8/12 cột + hồ sơ sống của dòng đang chọn 4/12 cột. Máy tính bảng / điện thoại: bảng tự xếp chồng thành danh sách;
 * bấm một dòng mở hồ sơ trong ngăn chi tiết (toàn màn hình trên điện thoại, có "Quay lại").
 * Địa chỉ: `?nguoi=<person_id>` chọn sẵn một người (hợp đồng với màn Thông tin "Mở hồ sơ"); `?list=care|hot|bridges|matches` chọn danh sách
 * (`cooling` — link cũ từ Hôm nay — về "Cần chăm").
 *
 * Gen chỉ ĐỀ XUẤT: "Nhờ Gen soạn tin" chỉ mở ngăn Hỏi Gen với câu điền sẵn; "Giới thiệu" chỉ tạo nháp chờ duyệt. Không có đường gửi thẳng.
 * Trạng thái: chưa có kênh ⇒ đúng MỘT thẻ "Nối Zalo" · có kênh mà chưa có khách ⇒ một câu · Cần chăm rỗng ⇒ "Không khách nào cần chăm gấp."
 * Mỗi khối lỗi riêng (danh sách, hồ sơ) — khối khác vẫn chạy. Không vẽ bản đồ quan hệ (chỉ liên kết sang Console).
 */
import { useEffect } from 'react';
import { useSearchParams } from 'react-router-dom';
import type { OwnerRelationRow } from '@gen-harness/contracts';
import { Card, DetailSheet, useMediaQuery } from '@gen-harness/ui';
import { asText } from '../ownerModel';
import { FirstRunCard, ListSkeleton, OwnerEmpty, OwnerError } from '../parts';
import { useOwnerInsights, useOwnerRelations } from '../queries';
import { BridgeList } from './BridgeList';
import { CareTable } from './CareTable';
import { MatchTable } from './MatchTable';
import { ProfilePanel } from './ProfilePanel';
import {
  DEFAULT_RELATION_TAB,
  RELATION_TABS,
  careHeadline,
  countLabel,
  parseListParam,
  personKey,
  relationTabDef,
  type RelationTabKey,
} from './relationsModel';
import './relations.css';

/** Số dòng mỗi danh sách (mặc định của `useOwnerRelations`); đủ trang thì số đếm ghi "20+". */
const LIMIT = 20;
const DESKTOP = '(min-width: 1024px)';

const EMPTY_ROWS: readonly OwnerRelationRow[] = [];
const rowsOf = (data: { items?: unknown } | undefined): readonly OwnerRelationRow[] => (Array.isArray(data?.items) ? (data.items as OwnerRelationRow[]) : EMPTY_ROWS);

export function RelationsScreen(): JSX.Element {
  const [params, setParams] = useSearchParams();
  const wide = useMediaQuery(DESKTOP);
  const list = parseListParam(params.get('list'));
  const nguoi = params.get('nguoi');

  // 4 danh sách cùng tải để nút lọc có số đếm (đều là GET đọc, nhẹ). Danh sách đang xem dùng đúng truy vấn của nó.
  const care = useOwnerRelations('care', LIMIT);
  const hot = useOwnerRelations('hot', LIMIT);
  const bridges = useOwnerRelations('bridges', LIMIT);
  const matches = useOwnerRelations('matches', LIMIT);
  // `channels_connected` chỉ có ở brief / feed / insights — dùng insights `only=messages_in` (truy vấn nhẹ, cùng khoá với Tình báo).
  const insights = useOwnerInsights('week', ['messages_in']);

  useEffect(() => {
    document.title = 'Quan hệ · Gen-Harness';
  }, []);

  const queries = { care, hot, bridges, matches } as const;
  const active = queries[list];
  const rows = list === 'matches' ? rowsOf(active.data).filter((r) => asText(r.match_id)) : rowsOf(active.data);
  const allRows = [...rowsOf(care.data), ...rowsOf(hot.data), ...rowsOf(bridges.data), ...rowsOf(matches.data)];
  const channelsKnown = !insights.isPending;
  const firstRun = insights.data?.channels_connected === 0;
  const allSettled = RELATION_TABS.every((t) => !queries[t.list].isPending);
  const allEmpty = allSettled && RELATION_TABS.every((t) => !queries[t.list].isError) && allRows.length === 0;

  // Người đang chọn: `?nguoi=` hoặc (máy tính) dòng đầu tiên có người của danh sách đang xem.
  const firstPerson = rows.map((r) => (asText(r.person_id) || (list === 'matches' ? '' : personKey(r)))).find(Boolean) ?? null;
  const selectedPerson = nguoi || (wide ? firstPerson : null);
  const selectedRow = selectedPerson ? (rows.find((r) => personKey(r) === selectedPerson) ?? allRows.find((r) => personKey(r) === selectedPerson)) : undefined;

  const edit = (fn: (n: URLSearchParams) => void, replace: boolean) =>
    setParams(
      (prev) => {
        const n = new URLSearchParams(prev);
        fn(n);
        return n;
      },
      { replace },
    );
  const pickList = (key: RelationTabKey) =>
    edit((n) => {
      if (key === DEFAULT_RELATION_TAB) n.delete('list');
      else n.set('list', key);
      n.delete('nguoi');
    }, true);
  const select = (personId: string) => edit((n) => n.set('nguoi', personId), false);
  const closeProfile = () => edit((n) => n.delete('nguoi'), true);

  if (firstRun) {
    return (
      <div className="owner-screen rel" data-testid="owner-frame-relations">
        <FirstRunCard />
      </div>
    );
  }

  const tab = relationTabDef(list);
  const head = list === 'care' ? careHeadline(rows) : { total: rows.length, breakdown: '' };
  const side = wide ? selectedPerson : null;

  const body = active.isPending ? (
    <ListSkeleton rows={5} label="Đang tải danh sách" />
  ) : active.isError ? (
    <OwnerError error={active.error} onRetry={() => void active.refetch()} retrying={active.isFetching} />
  ) : rows.length === 0 ? (
    channelsKnown ? (
      <div data-testid="rel-empty">
        <OwnerEmpty
          icon="ph ph-users-three"
          title={allEmpty ? 'Gen chưa nhận diện khách nào. Khi có tin từ Zalo, khách sẽ tự hiện ở đây.' : tab.empty}
        />
      </div>
    ) : (
      <ListSkeleton rows={3} label="Đang tải danh sách" />
    )
  ) : list === 'bridges' ? (
    <BridgeList rows={rows} selectedPerson={selectedPerson} onSelect={select} />
  ) : list === 'matches' ? (
    <MatchTable rows={rows} selectedPerson={selectedPerson} onSelect={select} />
  ) : (
    <CareTable variant={list} rows={rows} selectedPerson={selectedPerson} onSelect={select} />
  );

  return (
    <div className="owner-screen rel" data-testid="owner-frame-relations">
      <div className="kit-grid">
        <div className={side ? 'kit-c8' : 'kit-c12'}>
          <Card padded={false} aria-label={`Danh sách ${tab.label}`} className="rel-card" data-testid="rel-list-card">
            <div className="rel-head">
              {active.isSuccess && rows.length > 0 ? (
                <p className="rel-head__line" data-testid="rel-headline">
                  <span className="rel-head__n">{head.total}</span>
                  <span className="rel-head__t">{tab.noun}</span>
                  {head.breakdown ? <span className="rel-head__d">{head.breakdown}</span> : null}
                </p>
              ) : null}
              <div className="kit-chips" role="group" aria-label="Chọn danh sách quan hệ">
                {RELATION_TABS.map((t) => {
                  const q = queries[t.list];
                  const n = q.isSuccess ? (t.list === 'matches' ? rowsOf(q.data).filter((r) => asText(r.match_id)).length : rowsOf(q.data).length) : undefined;
                  return (
                    <button
                      key={t.list}
                      type="button"
                      className="kit-chip"
                      aria-pressed={t.list === list}
                      data-testid={`rel-chip-${t.list}`}
                      onClick={() => pickList(t.list)}
                    >
                      {t.label}
                      {n !== undefined ? <span className="kit-chip__n">{countLabel(n, LIMIT)}</span> : null}
                    </button>
                  );
                })}
              </div>
            </div>
            <div className="rel-body" data-testid="rel-body" data-list={list}>
              {body}
            </div>
            {rows.length > 0 ? <p className="rel-foot">{tab.footnote}</p> : null}
          </Card>
        </div>
        {side ? (
          <div className="kit-c4 rel-side">
            <Card padded={false} className="rel-card" data-testid="rel-side-card">
              <ProfilePanel key={side} personId={side} row={selectedRow} />
            </Card>
          </div>
        ) : null}
      </div>

      {!wide ? (
        <DetailSheet
          open={!!nguoi}
          title="Hồ sơ khách"
          onClose={closeProfile}
          className="rel-sheet"
          data-testid="rel-sheet"
        >
          {nguoi ? (
            <div className="kit-scope rel-sheet__body">
              <ProfilePanel key={nguoi} personId={nguoi} row={selectedRow} />
            </div>
          ) : null}
        </DetailSheet>
      ) : null}
    </div>
  );
}
