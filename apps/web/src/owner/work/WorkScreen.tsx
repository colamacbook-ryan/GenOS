/**
 * v0.1.59 (G3) — `/owner/viec`: màn VIỆC. Máy tính: trái 7 cột "Cần Sếp quyết" (nháp chờ duyệt + đề xuất của Gen), phải 5 cột
 * "Việc hôm nay" và dưới là "Gen đề xuất". Điện thoại: `Segmented` "Cần quyết (n) · Hôm nay (n) · Gen đề xuất (n)", mỗi lần một mục.
 * Khối nào lỗi thì chỉ khối đó báo (OwnerError), khối khác vẫn chạy. Gen chỉ ĐỀ XUẤT — duyệt/xác nhận đi qua DraftDecide/ProposalDecide
 * (mã PIN do client tự hỏi); "Tạo nhắc" chỉ tạo việc của Sếp.
 */
import { useEffect, useState, type ReactNode } from 'react';
import { Segmented, useMediaQuery } from '@gen-harness/ui';
import { DEFAULT_TZ } from '../../lib/format';
import { useOrgTimezone } from '../../lib/permissions';
import { useDrafts } from '../../screens/core/queries';
import { dayHeading } from '../intel/BriefBlocks';
import { useOwnerProposals, useOwnerRelations } from '../queries';
import { DecideList, decideCounts } from './DecideList';
import { GenSuggest, TodayTasks, suggestionRows, useTodayData } from './TodayTasks';
import './work.css';

type Tab = 'decide' | 'today' | 'suggest';

/**
 * "Cần quyết (4)" — số đếm nằm trong span cỡ 12px để nhãn luôn MỘT dòng (ba tab cùng chiều cao ở 390px). Tên truy cập vẫn là
 * "Cần quyết (4)". Chưa biết số (đang tải) thì chỉ có chữ.
 */
const tabLabel = (text: string, n: number | null): ReactNode =>
  n === null ? (
    text
  ) : (
    <>
      {text} <span className="work-seg__n">({n})</span>
    </>
  );

export function WorkScreen(): JSX.Element {
  useEffect(() => {
    document.title = 'Việc · Gen-Harness';
  }, []);
  const tz = useOrgTimezone() || DEFAULT_TZ;
  const wide = useMediaQuery('(min-width: 760px)');
  const [tab, setTab] = useState<Tab>('decide');
  const drafts = useDrafts('pending', undefined, { hideSilenced: true });
  const proposals = useOwnerProposals();
  const today = useTodayData(tz);
  const care = useOwnerRelations('care', 5);

  const nDecide = drafts.data || proposals.data ? decideCounts(drafts, proposals).total : null;
  const nToday = today.isPending ? null : today.items.length;
  const nSuggest = care.data ? suggestionRows(care).length : null;

  const decide = <DecideList drafts={drafts} proposals={proposals} tz={tz} />;
  const todayBlock = <TodayTasks data={today} tz={tz} kicker={nToday === null ? undefined : `${nToday} việc · ${dayHeading(Date.now(), tz)}`} />;
  const suggest = <GenSuggest q={care} />;

  return (
    <div className="owner-screen work" data-testid="owner-frame-work">
      {wide ? (
        <div className="kit-grid" data-testid="work-grid">
          <div className="kit-c7">{decide}</div>
          <div className="kit-c5 kit-stack">
            {todayBlock}
            {suggest}
          </div>
        </div>
      ) : (
        <>
          <Segmented<Tab>
            label="Chọn mục việc"
            block
            value={tab}
            onChange={setTab}
            options={[
              { value: 'decide', label: tabLabel('Cần quyết', nDecide) },
              { value: 'today', label: tabLabel('Hôm nay', nToday) },
              { value: 'suggest', label: tabLabel('Gen đề xuất', nSuggest) },
            ]}
          />
          {tab === 'decide' ? decide : tab === 'today' ? todayBlock : suggest}
        </>
      )}
    </div>
  );
}
