/**
 * v0.1.59 (G2) — bộ thành phần Mặt tiền của @gen-harness/ui (CSS: owner-kit.css):
 *  - DataTable: dưới 760px (matchMedia không khớp / không có) ⇒ ListRow xếp chồng; ≥ 760px ⇒ <table>; 760–1023 bỏ cột priority 3.
 *  - TrendChart: chuỗi rỗng / toàn 0 / dữ liệu bẩn không ném lỗi, không "NaN"; có aria-label; tooltip bằng bàn phím/chạm.
 *  - StatTile (pct null → "Mới", màu theo nghĩa tốt/xấu), Badge (mức Khẩn → bad), Sparkline, HBar, ListRow, KitLink.
 *  - Card và EmptyState với props CŨ vẫn vẽ đúng như trước; prop mới (state, footer, action) hoạt động.
 *  - DetailSheet: Esc gọi onClose, hộp thoại khác ở trên thì Esc không đóng ngăn, tiêu điểm vào/ra.
 * Không thành phần nào để lộ `[object Object]`.
 */
import { afterEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {
  Badge,
  Card,
  DataTable,
  DetailSheet,
  EmptyState,
  HBar,
  KitLink,
  ListRow,
  Sparkline,
  StatTile,
  TrendChart,
  setKitNavigator,
  useMediaQuery,
  type DataColumn,
} from '@gen-harness/ui';

/** Giả `matchMedia`: `minWidths` = các ngưỡng min-width đang khớp (vd [760] = tablet, [760, 1024] = máy tính). */
function stubMedia(minWidths: number[]) {
  vi.stubGlobal('matchMedia', (q: string) => {
    const m = /min-width:\s*(\d+)px/.exec(q);
    return {
      matches: !!m && minWidths.includes(Number(m[1])), media: q, onchange: null,
      addEventListener: () => undefined, removeEventListener: () => undefined,
      addListener: () => undefined, removeListener: () => undefined, dispatchEvent: () => false,
    };
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
  setKitNavigator(null);
});

const noObject = (el: Element | Document = document) => expect(el.textContent ?? '').not.toContain('[object Object]');

// ── DataTable ───────────────────────────────────────────────────────────────────────────────────────────────────────

interface Row {
  id: string;
  name: string;
  channel: string;
  heat: number;
  note: unknown;
}
const ROWS: Row[] = [
  { id: 'a', name: 'Trần Văn Hậu', channel: 'Zalo', heat: 91, note: 'hỏi giá ván MDF' },
  { id: 'b', name: 'Hoàng Thị Lan', channel: 'Facebook', heat: 40, note: { oops: true } },
];
const COLS: DataColumn<Row>[] = [
  { key: 'name', label: 'Khách' },
  { key: 'channel', label: 'Kênh', priority: 2 },
  { key: 'heat', label: 'Độ nóng', align: 'right', priority: 2 },
  { key: 'note', label: 'Ghi chú', priority: 3 },
  { key: 'act', label: '', action: true, render: (r) => <button type="button">Tạo nhắc {r.id}</button> },
];

describe('DataTable', () => {
  it('dưới 760px (matchMedia không khớp): danh sách ListRow xếp chồng, KHÔNG có <table>; cột đầu là tiêu đề', () => {
    stubMedia([]);
    const { container } = render(<DataTable columns={COLS} rows={ROWS} />);
    expect(container.querySelector('table')).toBeNull();
    const rows = screen.getAllByTestId('kit-list-row');
    expect(rows).toHaveLength(2);
    expect(container.querySelector('[data-layout="stack"]')).not.toBeNull();
    expect(rows[0]).toHaveTextContent('Trần Văn Hậu');
    // cột priority ≤ 2 thành dòng phụ "Nhãn giá trị"; priority 3 (Ghi chú) không hiện khi xếp chồng
    expect(rows[0]).toHaveTextContent('KênhZalo');
    expect(rows[0]).toHaveTextContent('Độ nóng91');
    expect(rows[0]).not.toHaveTextContent('hỏi giá ván MDF');
    // nút hành động nằm NGOÀI vùng bấm của dòng (không lồng nút trong nút)
    const btn = within(rows[0]).getByRole('button', { name: 'Tạo nhắc a' });
    expect(btn.closest('.kit-row__main')).toBeNull();
    noObject();
  });

  it('không có window.matchMedia (như jsdom) ⇒ coi là dưới 760px ⇒ xếp chồng', () => {
    const { container } = render(<DataTable columns={COLS} rows={ROWS} />);
    expect(typeof window.matchMedia).toBe('undefined');
    expect(container.querySelector('table')).toBeNull();
    expect(screen.getAllByTestId('kit-list-row')).toHaveLength(2);
  });

  it('≥ 760px: <table> có tiêu đề cột; 760–1023 bỏ cột priority 3, ≥ 1024 đủ cột', () => {
    stubMedia([760]);
    const tablet = render(<DataTable columns={COLS} rows={ROWS} />);
    expect(tablet.container.querySelector('table')).not.toBeNull();
    expect(screen.queryAllByTestId('kit-list-row')).toHaveLength(0);
    expect(screen.getAllByRole('columnheader').map((h) => h.textContent)).toEqual(['Khách', 'Kênh', 'Độ nóng', '']);
    tablet.unmount();
    vi.unstubAllGlobals();
    stubMedia([760, 1024]);
    render(<DataTable columns={COLS} rows={ROWS} />);
    expect(screen.getAllByRole('columnheader').map((h) => h.textContent)).toEqual(['Khách', 'Kênh', 'Độ nóng', 'Ghi chú', '']);
    expect(screen.getAllByRole('row')).toHaveLength(3);                                  // 1 dòng tiêu đề + 2 dòng dữ liệu
    expect(screen.getByRole('cell', { name: 'Trần Văn Hậu' })).toBeInTheDocument();
    expect(screen.getByText('91').closest('td')).toHaveAttribute('data-align', 'right');
    noObject();                                                                           // note là object ⇒ ô trống, không vẽ object
  });

  it('onRowClick: bấm dòng / Enter (bảng) và bấm vùng dòng (xếp chồng); nút trong ô không kích hoạt dòng', async () => {
    stubMedia([760, 1024]);
    const onRow = vi.fn();
    const table = render(<DataTable columns={COLS} rows={ROWS} onRowClick={onRow} selectedKey="b" />);
    await userEvent.click(screen.getByText('Trần Văn Hậu'));
    expect(onRow).toHaveBeenCalledWith(ROWS[0], 0);
    onRow.mockClear();
    await userEvent.click(screen.getByRole('button', { name: 'Tạo nhắc b' }));
    expect(onRow).not.toHaveBeenCalled();
    const second = screen.getAllByRole('row')[2];
    expect(second).toHaveAttribute('aria-selected', 'true');
    second.focus();
    await userEvent.keyboard('{Enter}');
    expect(onRow).toHaveBeenCalledWith(ROWS[1], 1);
    table.unmount();
    vi.unstubAllGlobals();
    stubMedia([]);
    onRow.mockClear();
    render(<DataTable columns={COLS} rows={ROWS} onRowClick={onRow} />);
    await userEvent.click(within(screen.getAllByTestId('kit-list-row')[1]).getByText('Hoàng Thị Lan'));
    expect(onRow).toHaveBeenCalledWith(ROWS[1], 1);
  });

  it('không có dòng: hiện `empty` (hoặc một câu ngắn mặc định), cả hai kiểu bố cục', () => {
    stubMedia([760]);
    const a = render(<DataTable columns={COLS} rows={[]} empty={<p>Chưa có khách nào</p>} />);
    expect(screen.getByText('Chưa có khách nào')).toBeInTheDocument();
    expect(a.container.querySelector('table')).toBeNull();
    a.unmount();
    vi.unstubAllGlobals();
    render(<DataTable columns={COLS} rows={[]} />);
    expect(screen.getByText('Chưa có dòng nào để hiện.')).toBeInTheDocument();
  });

  it('khoá dòng: rowKey là tên trường hoặc hàm; dòng thiếu id vẫn vẽ', () => {
    stubMedia([760]);
    const rows = [{ code: 'x1', name: 'A' }, { name: 'B' }];
    render(<DataTable columns={[{ key: 'name', label: 'Tên' }]} rows={rows} rowKey="code" />);
    expect(screen.getAllByRole('row')).toHaveLength(3);
  });
});

// ── TrendChart ──────────────────────────────────────────────────────────────────────────────────────────────────────

function expectSafe(container: HTMLElement) {
  expect(container.innerHTML).not.toMatch(/NaN|undefined|Infinity/);
  for (const p of container.querySelectorAll('path')) expect(p.getAttribute('d') ?? '').not.toContain('NaN');
}

describe('TrendChart', () => {
  it.each(['bar', 'line'] as const)('chuỗi rỗng (%s): không ném lỗi, không NaN, có aria-label và dòng "Chưa có số liệu"', (kind) => {
    const { container } = render(<TrendChart kind={kind} series={[]} ariaLabel="Tin nhận theo ngày, chưa có số liệu" />);
    const svg = container.querySelector('svg');
    expect(svg).toHaveAttribute('aria-label', 'Tin nhận theo ngày, chưa có số liệu');
    expect(svg).toHaveAttribute('role', 'img');
    expect(screen.getByText('Chưa có số liệu trong kỳ này')).toBeInTheDocument();
    expectSafe(container);
  });

  it.each(['bar', 'line'] as const)('toàn 0 (%s): trục Y vẫn 3 vạch, không NaN', (kind) => {
    const { container } = render(<TrendChart kind={kind} series={[[0, 0, 0, 0, 0, 0, 0]]} prev={[0, 0, 0, 0, 0, 0, 0]} ariaLabel="Toàn 0" />);
    expectSafe(container);
    expect(container.querySelectorAll('.kit-chart__grid, .kit-chart__base')).toHaveLength(3);
    expect(container.querySelectorAll('.kit-chart__t').length).toBeGreaterThanOrEqual(3 + 1);
    expect(screen.getByText('Chưa có số liệu trong kỳ này')).toBeInTheDocument();
  });

  it('dữ liệu bẩn (NaN, Infinity, chuỗi, null, object lạ) không làm vỡ biểu đồ', () => {
    const dirty = [1, Number.NaN, Infinity, '5', null, undefined, 4] as unknown as number[];
    const { container } = render(
      <TrendChart kind="bar" series={[dirty, { name: 'Tuần trước', values: dirty }, [9, 9]]} labels={['a', 'b']} threshold={Number.NaN} yFormat={(v) => `${v}`} ariaLabel="Dữ liệu bẩn" />,
    );
    expectSafe(container);
    expect(container.querySelectorAll('.kit-chart__hit')).toHaveLength(7);
    // tối đa 2 chuỗi: chuỗi thứ ba bị bỏ (đỉnh 9 không kéo trục Y)
    expect(container.textContent).not.toContain('10');
    const odd = render(<TrendChart kind="line" series={'chuỗi' as unknown as number[][]} ariaLabel="Lạ" />);
    expectSafe(odd.container);
  });

  it('cột: mỗi điểm một cột bo góc + vùng chạm có aria-label "nhãn: giá trị"; trục X 3–4 nhãn; legend khi có kỳ trước và trần', () => {
    const cur = [3, 8, 5, 10, 2, 7, 6, 9, 4, 1];
    const prev = [2, 7, 4, 9, 3, 6, 5, 8, 3, 2];
    const labels = cur.map((_, i) => `${i + 1}/10`);
    const { container } = render(
      <TrendChart
        kind="bar"
        series={[{ name: 'Tuần này', values: cur }, { name: 'Tuần trước', values: prev }]}
        labels={labels}
        threshold={9}
        thresholdLabel="Trần 9"
        yFormat={(v) => `${v} tin`}
        ariaLabel="Tin nhận theo ngày"
      />,
    );
    expectSafe(container);
    expect(container.querySelectorAll('path.kit-chart__bar')).toHaveLength(10);
    expect(container.querySelectorAll('path.kit-chart__prev')).toHaveLength(1);
    expect(container.querySelectorAll('.kit-chart__thr')).toHaveLength(1);
    const hits = container.querySelectorAll('.kit-chart__hit');
    expect(hits).toHaveLength(10);
    expect(hits[3]).toHaveAttribute('aria-label', '4/10: 10 tin');
    const xLabels = [...container.querySelectorAll('text.kit-chart__t')].map((t) => t.textContent).filter((t) => /\//.test(t ?? ''));
    expect(xLabels.length).toBeGreaterThanOrEqual(3);
    expect(xLabels.length).toBeLessThanOrEqual(4);
    const legend = container.querySelector('.kit-chart__legend');
    expect(legend).toHaveTextContent('Tuần này');
    expect(legend).toHaveTextContent('Tuần trước');
    expect(legend).toHaveTextContent('Trần 9');
  });

  it('đường: một <path> cho kỳ này + một cho kỳ trước; tooltip hiện khi focus bằng bàn phím và khi chạm', async () => {
    const { container } = render(<TrendChart kind="line" series={[[1, 4, 2, 6], [2, 3, 3, 5]]} labels={['T2', 'T3', 'T4', 'T5']} ariaLabel="Đường" />);
    expect(container.querySelectorAll('path.kit-chart__line')).toHaveLength(1);
    expect(container.querySelectorAll('path.kit-chart__prev')).toHaveLength(1);
    expect(container.querySelector('.kit-chart__tip')).toBeNull();
    await userEvent.tab();                                                       // bàn phím tới được từng điểm
    const first = container.querySelector('.kit-chart__hit') as SVGElement;
    expect(document.activeElement).toBe(first);
    fireEvent.focus(first);                                                      // jsdom không tự bắn focusin cho phần tử SVG
    const tip = container.querySelector('.kit-chart__tip');
    expect(tip).not.toBeNull();
    expect(tip).toHaveTextContent('T2');
    expect(tip).toHaveTextContent('Kỳ trước');
    // chạm một điểm khác: tooltip đổi sang điểm đó, chạm lại thì tắt
    const hit3 = container.querySelectorAll('.kit-chart__hit')[2] as SVGElement;
    await userEvent.click(hit3);
    expect(container.querySelector('.kit-chart__tip')).toHaveTextContent('T4');
    expectSafe(container);
  });

  it('chạm (touch): chạm điểm hiện tooltip, chạm lại đúng điểm đó thì tắt, chạm ra ngoài biểu đồ cũng tắt', () => {
    const { container } = render(<TrendChart kind="bar" series={[[4, 8, 6]]} labels={['a', 'b', 'c']} ariaLabel="Chạm" />);
    const hit = container.querySelectorAll('.kit-chart__hit')[1] as SVGElement;
    const tap = () => {
      fireEvent.pointerDown(hit, { pointerType: 'touch' });
      fireEvent.click(hit);
    };
    tap();
    expect(container.querySelector('.kit-chart__tip')).toHaveTextContent('b');
    tap();
    expect(container.querySelector('.kit-chart__tip')).toBeNull();
    tap();
    expect(container.querySelector('.kit-chart__tip')).not.toBeNull();
    fireEvent.pointerDown(document.body, { pointerType: 'touch' });
    expect(container.querySelector('.kit-chart__tip')).toBeNull();
  });

  it('series là mảng phẳng các số cũng được hiểu là một chuỗi', () => {
    const { container } = render(<TrendChart kind="bar" series={[1, 2, 3] as unknown as number[][]} ariaLabel="Phẳng" />);
    expect(container.querySelectorAll('path.kit-chart__bar')).toHaveLength(3);
    expectSafe(container);
  });
});

// ── StatTile / Sparkline / HBar ─────────────────────────────────────────────────────────────────────────────────────

describe('StatTile', () => {
  it('delta.pct = null ⇒ huy hiệu "Mới" kèm câu giải thích, không có phần trăm', () => {
    render(<StatTile label="Tin mới hôm nay" value={128} delta={{ pct: null, good: 'up' }} />);
    expect(screen.getByText('Mới')).toBeInTheDocument();
    expect(screen.getByText(/chưa có kỳ trước/)).toBeInTheDocument();
    expect(document.body.textContent).not.toContain('%');
  });

  it('giá trị 28px (lớp kit-stat__value), nhóm hàng nghìn kiểu Việt, đơn vị và ghi chú', () => {
    render(<StatTile label="Cơ hội đang mở" value={12345} unit="cơ hội" note="tổng 2,4 tỷ ₫" />);
    const v = document.querySelector('.kit-stat__value');
    expect(v).toHaveTextContent('12.345 cơ hội');
    expect(screen.getByText('tổng 2,4 tỷ ₫')).toBeInTheDocument();
  });

  it('màu delta theo NGHĨA tốt/xấu: tăng + "tăng là tốt" ⇒ ok; tăng + "giảm là tốt" ⇒ warn; không đổi ⇒ trung tính; luôn có mũi tên và chữ', () => {
    const { rerender } = render(<StatTile label="Tin nhận" value={10} delta={{ pct: 12, good: 'up', compare: 'so với tuần trước' }} />);
    let pill = document.querySelector('.kit-delta');
    expect(pill).toHaveClass('kit-delta--ok');
    expect(pill).toHaveTextContent('Tăng 12%');
    expect(screen.getByText('so với tuần trước')).toBeInTheDocument();
    rerender(<StatTile label="Chi phí" value={10} delta={{ pct: 12, good: 'down' }} />);
    pill = document.querySelector('.kit-delta');
    expect(pill).toHaveClass('kit-delta--warn');
    expect(screen.getByText('so với kỳ trước')).toBeInTheDocument();
    rerender(<StatTile label="Chi phí" value={10} delta={{ pct: -8.5, good: 'down' }} />);
    pill = document.querySelector('.kit-delta');
    expect(pill).toHaveClass('kit-delta--ok');
    expect(pill).toHaveTextContent('Giảm 8,5%');
    rerender(<StatTile label="Tin" value={10} delta={{ pct: 0, good: 'up' }} />);
    expect(screen.getByText('Không đổi')).toBeInTheDocument();
    expect(document.querySelector('.kit-delta')).toHaveClass('kit-delta--flat');
  });

  it('value lạ (null, NaN, object) ⇒ "—", không bao giờ render object', () => {
    for (const bad of [null, undefined, Number.NaN, { a: 1 }, ['x']]) {
      const { unmount } = render(<StatTile label="X" value={bad as never} />);
      expect(document.querySelector('.kit-stat__value')).toHaveTextContent('—');
      noObject();
      unmount();
    }
  });

  it('có `to` ⇒ cả ô là liên kết; có spark ⇒ vẽ Sparkline 80×24', () => {
    const { container } = render(<StatTile label="Khách nóng" value={3} to="/owner/quan-he?list=hot" spark={[1, 2, 3, 2, 5]} data-testid="tile" />);
    const tile = screen.getByTestId('tile');
    expect(tile.tagName).toBe('A');
    expect(tile).toHaveAttribute('href', '/owner/quan-he?list=hot');
    const svg = container.querySelector('svg.kit-spark');
    expect(svg).toHaveAttribute('width', '80');
    expect(svg).toHaveAttribute('height', '24');
  });
});

describe('Sparkline', () => {
  it('rỗng / một điểm / toàn bằng nhau / dữ liệu bẩn: vẽ được, không NaN', () => {
    for (const values of [[], [5], [3, 3, 3], [1, Number.NaN, 4], 'x' as unknown as number[]]) {
      const { container, unmount } = render(<Sparkline values={values} />);
      expect(container.innerHTML).not.toContain('NaN');
      unmount();
    }
    const { container } = render(<Sparkline values={[1, 3, 2]} ariaLabel="Xu hướng 3 ngày" />);
    expect(container.querySelector('svg')).toHaveAttribute('aria-label', 'Xu hướng 3 ngày');
    expect(container.querySelector('path')?.getAttribute('d')).toMatch(/^M[\d.]+,[\d.]+ L[\d.]+,[\d.]+ L/);
    expect(container.querySelectorAll('circle')).toHaveLength(1);
  });
});

describe('HBar', () => {
  it('thanh tỉ lệ theo giá trị lớn nhất, hiện số bằng chữ; toàn 0 không chia cho 0', () => {
    const { container } = render(
      <HBar items={[{ label: 'Hỏi giá', value: 40 }, { label: 'Khiếu nại', value: 10, display: '10 · 3,5 triệu ₫' }, { label: 'Lạ', value: Number.NaN }]} ariaLabel="Loại tin" />,
    );
    const fills = [...container.querySelectorAll<HTMLElement>('.kit-hbar__fill')].map((f) => f.style.width);
    expect(fills).toEqual(['100%', '25%', '0%']);
    expect(screen.getByText('10 · 3,5 triệu ₫')).toBeInTheDocument();
    expect(screen.getByText('40')).toBeInTheDocument();
    const zero = render(<HBar items={[{ label: 'a', value: 0 }]} />);
    expect(zero.container.innerHTML).not.toContain('NaN');
    expect(render(<HBar items={undefined as never} />).container.querySelectorAll('li')).toHaveLength(0);
  });
});

// ── Badge ───────────────────────────────────────────────────────────────────────────────────────────────────────────

describe('Badge', () => {
  it('mức: Khẩn → bad, Quan trọng → warn, Thường → neutral (chữ nội dung, `level` hoặc `tone` đều được)', () => {
    const { rerender } = render(<Badge kind="level">Khẩn</Badge>);
    expect(screen.getByText('Khẩn')).toHaveClass('kit-badge--bad');
    rerender(<Badge kind="level">Quan trọng</Badge>);
    expect(screen.getByText('Quan trọng')).toHaveClass('kit-badge--warn');
    rerender(<Badge kind="level">Thường</Badge>);
    expect(screen.getByText('Thường')).toHaveClass('kit-badge--neutral');
    rerender(<Badge kind="level" level="Khẩn" />);
    expect(screen.getByText('Khẩn')).toHaveClass('kit-badge--bad');
    rerender(<Badge kind="level" tone="Quan trọng">Quan trọng</Badge>);
    expect(screen.getByText('Quan trọng')).toHaveClass('kit-badge--warn');
    rerender(<Badge kind="level">Không rõ</Badge>);
    expect(screen.getByText('Không rõ')).toHaveClass('kit-badge--neutral');       // mức lạ không bao giờ đỏ nhầm
    rerender(<Badge kind="level">constructor</Badge>);
    expect(screen.getByText('constructor')).toHaveClass('kit-badge--neutral');
  });

  it('trạng thái nhận tone; đếm là viên tròn accent, chạm trần hiện 999+', () => {
    const { rerender } = render(<Badge kind="status" tone="ok">Đã gửi</Badge>);
    expect(screen.getByText('Đã gửi')).toHaveClass('kit-badge--status', 'kit-badge--ok');
    rerender(<Badge kind="count" count={7} />);
    expect(screen.getByText('7')).toHaveClass('kit-badge--count', 'kit-badge--accent');
    rerender(<Badge kind="count" count={1500} />);
    expect(screen.getByText('999+')).toBeInTheDocument();
    rerender(<Badge>mặc định</Badge>);
    expect(screen.getByText('mặc định')).toHaveClass('kit-badge--status', 'kit-badge--neutral');
  });
});

// ── ListRow / KitLink / useMediaQuery ──────────────────────────────────────────────────────────────────────────────

describe('ListRow', () => {
  it('dòng liên kết / nút / tĩnh; avatar chữ cái đầu; actions nằm ngoài vùng bấm', async () => {
    const onClick = vi.fn();
    render(
      <ul>
        <ListRow as="li" title="Trần Văn Hậu" meta="Khách hàng · Zalo" avatar="Trần Văn Hậu" right={<Badge kind="level">Khẩn</Badge>} to="/profile?id=p-hau" data-testid="r1" />
        <ListRow as="li" title="Mở chi tiết" onClick={onClick} actions={<button type="button">Duyệt</button>} data-testid="r2" />
        <ListRow as="li" title="Chỉ đọc" data-testid="r3" />
      </ul>,
    );
    const r1 = screen.getByTestId('r1');
    expect(r1.querySelector('a.kit-row__main')).toHaveAttribute('href', '/profile?id=p-hau');
    expect(r1.querySelector('.kit-avatar')).toHaveTextContent('TH');
    expect(r1).toHaveTextContent('Khẩn');
    const r2 = screen.getByTestId('r2');
    await userEvent.click(within(r2).getByText('Mở chi tiết'));
    expect(onClick).toHaveBeenCalledTimes(1);
    onClick.mockClear();
    await userEvent.click(within(r2).getByRole('button', { name: 'Duyệt' }));
    expect(onClick).not.toHaveBeenCalled();
    expect(within(r2).getByRole('button', { name: 'Duyệt' }).closest('.kit-row__main')).toBeNull();
    const r3 = screen.getByTestId('r3');
    expect(r3.querySelector('a, button')).toBeNull();
    expect(r3.querySelector('.kit-row__go')).toBeNull();
  });
});

describe('KitLink', () => {
  it('bấm trái chuyển trang trong ứng dụng qua setKitNavigator; Ctrl/giữa/đích ngoài để trình duyệt xử lý', async () => {
    const nav = vi.fn();
    setKitNavigator(nav);
    render(<KitLink to="/owner/viec">Việc</KitLink>);
    await userEvent.click(screen.getByRole('link', { name: 'Việc' }));
    expect(nav).toHaveBeenCalledWith('/owner/viec');
    nav.mockClear();
    const link = screen.getByRole('link', { name: 'Việc' });
    fireEvent.click(link, { ctrlKey: true });                 // mở tab mới
    fireEvent.click(link, { metaKey: true });
    fireEvent.click(link, { shiftKey: true });
    fireEvent.click(link, { button: 1 });                     // nút giữa
    expect(nav).not.toHaveBeenCalled();
  });

  it('chưa đăng ký bộ điều hướng ⇒ <a> thường; đích không an toàn (javascript:, //evil, https:) ⇒ không có href', () => {
    render(
      <>
        <KitLink to="/x">ok</KitLink>
        <KitLink to="javascript:alert(1)">js</KitLink>
        <KitLink to="//evil.example">proto</KitLink>
        <KitLink to="https://evil.example">ext</KitLink>
        <KitLink to="?tab=2">query</KitLink>
      </>,
    );
    expect(screen.getByText('ok')).toHaveAttribute('href', '/x');
    expect(screen.getByText('js')).not.toHaveAttribute('href');
    expect(screen.getByText('proto')).not.toHaveAttribute('href');
    expect(screen.getByText('ext')).not.toHaveAttribute('href');
    expect(screen.getByText('query')).toHaveAttribute('href', '?tab=2');
  });
});

describe('useMediaQuery', () => {
  function Probe() {
    return <span data-testid="p">{String(useMediaQuery('(min-width: 760px)'))}</span>;
  }
  it('không có matchMedia ⇒ false; có thì theo kết quả', () => {
    const a = render(<Probe />);
    expect(screen.getByTestId('p')).toHaveTextContent('false');
    a.unmount();
    stubMedia([760]);
    render(<Probe />);
    expect(screen.getByTestId('p')).toHaveTextContent('true');
  });
});

// ── Card / EmptyState: props cũ chạy y như trước, prop mới hoạt động ───────────────────────────────────────────────────

describe('Card', () => {
  it('props cũ (title, kicker, actions, padded, as, className, thuộc tính HTML) vẽ đúng như trước', () => {
    const { container } = render(
      <Card title="Kết nối" kicker="Một dòng mỗi dịch vụ" actions={<button type="button">Làm mới</button>} className="x" data-testid="c1" aria-label="thẻ">
        <p>nội dung</p>
      </Card>,
    );
    const card = container.firstElementChild as HTMLElement;
    expect(card.tagName).toBe('SECTION');
    expect(card.className).toBe('gh-card x');
    expect(card).not.toHaveAttribute('data-state');
    expect(card).toHaveAttribute('data-testid', 'c1');
    expect(card.querySelector('.gh-card__header .gh-card__title')).toHaveTextContent('Kết nối');
    expect(card.querySelector('.gh-card__header .gh-card__kicker')).toHaveTextContent('Một dòng mỗi dịch vụ');
    expect(within(card.querySelector('.gh-card__header') as HTMLElement).getByRole('button', { name: 'Làm mới' })).toBeInTheDocument();
    expect(card.querySelector('.gh-card__body p')).toHaveTextContent('nội dung');
    expect(card.querySelector('.kit-card__footer')).toBeNull();
    expect(card.querySelector('.kit-card__state')).toBeNull();
  });

  it('không tiêu đề/không actions ⇒ không có header; padded={false} ⇒ không bọc thân; as="div"', () => {
    const { container } = render(
      <Card as="div" padded={false}>
        <span>thân</span>
      </Card>,
    );
    const card = container.firstElementChild as HTMLElement;
    expect(card.tagName).toBe('DIV');
    expect(card.querySelector('.gh-card__header')).toBeNull();
    expect(card.querySelector('.gh-card__body')).toBeNull();
    expect(card.querySelector(':scope > span')).toHaveTextContent('thân');
  });

  it('title chỉ là chuỗi rỗng "" vẫn có header (như cũ: title !== undefined)', () => {
    const { container } = render(<Card title="">x</Card>);
    expect(container.querySelector('.gh-card__header')).not.toBeNull();
  });

  it('prop mới: action (số ít) cùng chỗ với actions, footer có vạch ngăn', () => {
    const { container } = render(
      <Card title="Cần chăm" action={<a href="/owner/quan-he">Xem tất cả</a>} footer={<span>Cập nhật 09:12</span>}>
        nội dung
      </Card>,
    );
    expect(within(container.querySelector('.gh-card__header') as HTMLElement).getByRole('link', { name: 'Xem tất cả' })).toBeInTheDocument();
    expect(container.querySelector('.kit-card__footer')).toHaveTextContent('Cập nhật 09:12');
  });

  it('state: loading vẽ khung chờ (role status, aria-busy); error/empty hiện children hoặc câu mặc định', () => {
    const { container, rerender } = render(<Card title="Tình hình" state="loading">KHÔNG HIỆN</Card>);
    const sk = container.querySelector('.kit-card__state--loading') as HTMLElement;
    expect(sk).toHaveAttribute('aria-busy', 'true');
    expect(sk).toHaveAttribute('role', 'status');
    expect(container.querySelectorAll('.gh-skeleton').length).toBeGreaterThanOrEqual(2);
    expect(container.textContent).not.toContain('KHÔNG HIỆN');
    expect(container.firstElementChild).toHaveAttribute('data-state', 'loading');
    rerender(<Card title="Tình hình" state="error" />);
    expect(screen.getByRole('alert')).toHaveTextContent('Chưa tải được khối này');
    rerender(<Card title="Tình hình" state="empty" />);
    expect(container.querySelector('.kit-card__state--empty')).toHaveTextContent('Chưa có gì để hiện.');
    rerender(<Card title="Tình hình" state="error"><span>Lỗi riêng của khối</span></Card>);
    expect(screen.getByRole('alert')).toHaveTextContent('Lỗi riêng của khối');
  });
});

describe('EmptyState', () => {
  it('props cũ (icon, title, description, actions) vẽ đúng như trước', () => {
    const { container } = render(<EmptyState icon="ph ph-tray" title="Chưa có tin" description="Khi có tin em sẽ báo" actions={<button type="button">Nối Zalo</button>} />);
    const root = container.firstElementChild as HTMLElement;
    expect(root.className).toBe('gh-state gh-state--empty');
    expect(root.querySelector('.gh-state__tile svg')).not.toBeNull();
    expect(root.querySelector('.gh-state__title')).toHaveTextContent('Chưa có tin');
    expect(root.querySelector('.gh-state__desc')).toHaveTextContent('Khi có tin em sẽ báo');
    expect(root.querySelector('.gh-state__actions')).toContainElement(screen.getByRole('button', { name: 'Nối Zalo' }));
  });

  it('chỉ title: không có mô tả, không có khối nút; icon mặc định', () => {
    const { container } = render(<EmptyState title="Đang hoàn thiện" />);
    expect(container.querySelector('.gh-state__desc')).toBeNull();
    expect(container.querySelector('.gh-state__actions')).toBeNull();
    expect(container.querySelector('.gh-state__tile svg')).not.toBeNull();
  });

  it('prop mới: action (một nút) cùng chỗ với actions; className thêm vào', () => {
    const { container } = render(<EmptyState title="Chưa có việc" action={<button type="button">Tạo nhắc</button>} className="tuy-chinh" />);
    expect((container.firstElementChild as HTMLElement).className).toBe('gh-state gh-state--empty tuy-chinh');
    expect(container.querySelector('.gh-state__actions')).toContainElement(screen.getByRole('button', { name: 'Tạo nhắc' }));
  });
});

// ── DetailSheet ─────────────────────────────────────────────────────────────────────────────────────────────────────

describe('DetailSheet', () => {
  it('đóng ⇒ không vẽ gì; mở ⇒ hộp thoại có tên là tiêu đề, nút Quay lại (điện thoại) và Đóng (máy tính)', () => {
    const { rerender } = render(<DetailSheet open={false} title="Chi tiết tin" onClose={() => undefined}>nội dung</DetailSheet>);
    expect(screen.queryByRole('dialog')).toBeNull();
    rerender(<DetailSheet open title="Chi tiết tin" onClose={() => undefined}>nội dung</DetailSheet>);
    const dlg = screen.getByRole('dialog', { name: 'Chi tiết tin' });
    expect(dlg).toHaveAttribute('aria-modal', 'true');
    expect(within(dlg).getByText('nội dung')).toBeInTheDocument();
    expect(within(dlg).getByRole('button', { name: /Quay lại/ })).toBeInTheDocument();
    expect(within(dlg).getByRole('button', { name: 'Đóng' })).toBeInTheDocument();
  });

  it('Esc gọi onClose (kể cả khi tiêu điểm đang ở <body>); chỉ ngăn trên cùng nhận Esc', async () => {
    const outer = vi.fn();
    const inner = vi.fn();
    render(
      <>
        <DetailSheet open title="Ngăn dưới" onClose={outer}>a</DetailSheet>
        <DetailSheet open title="Ngăn trên" onClose={inner}>b</DetailSheet>
      </>,
    );
    await userEvent.keyboard('{Escape}');
    expect(inner).toHaveBeenCalledTimes(1);
    expect(outer).not.toHaveBeenCalled();
  });

  it('Esc: không đóng khi `escapeEnabled` trả false, hoặc khi Esc xảy ra trong hộp thoại khác (hộp PIN)', async () => {
    const onClose = vi.fn();
    let allow = false;
    render(
      <>
        <DetailSheet open title="Ngăn" onClose={onClose} escapeEnabled={() => allow}>
          <button type="button">trong ngăn</button>
        </DetailSheet>
        <div role="dialog" aria-label="Mã PIN xác nhận thao tác">
          <button type="button">trong hộp PIN</button>
        </div>
      </>,
    );
    await userEvent.keyboard('{Escape}');
    expect(onClose).not.toHaveBeenCalled();                                    // escapeEnabled = false
    allow = true;
    screen.getByRole('button', { name: 'trong hộp PIN' }).focus();
    await userEvent.keyboard('{Escape}');
    expect(onClose).not.toHaveBeenCalled();                                    // Esc thuộc hộp PIN
    screen.getByRole('button', { name: 'trong ngăn' }).focus();
    await userEvent.keyboard('{Escape}');
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('nút Quay lại, nút Đóng và bấm nền mờ đều gọi onClose', async () => {
    const onClose = vi.fn();
    render(<DetailSheet open title="Ngăn" onClose={onClose}>x</DetailSheet>);
    await userEvent.click(screen.getByRole('button', { name: /Quay lại/ }));
    await userEvent.click(screen.getByRole('button', { name: 'Đóng' }));
    const scrim = document.querySelector('.kit-sheet__scrim') as HTMLElement;
    await userEvent.pointer([{ target: scrim }, { keys: '[MouseLeft]', target: scrim }]);
    expect(onClose).toHaveBeenCalledTimes(3);
  });

  it('tiêu điểm vào ngăn khi mở và trở về chỗ cũ khi đóng; Tab bị bẫy trong ngăn', async () => {
    function Host() {
      return (
        <>
          <button type="button">mở</button>
          <DetailSheet open title="Ngăn" onClose={() => undefined}>
            <input aria-label="ô một" />
          </DetailSheet>
        </>
      );
    }
    const opener = document.createElement('button');
    document.body.appendChild(opener);
    opener.focus();
    const { unmount } = render(<Host />);
    await vi.waitFor(() => expect(screen.getByRole('dialog')).toContainElement(document.activeElement as HTMLElement));
    // 3 phần tử focus được: Quay lại, Đóng, ô một — Tab vòng lại, không rời ngăn
    for (let i = 0; i < 5; i++) {
      await userEvent.tab();
      expect(screen.getByRole('dialog')).toContainElement(document.activeElement as HTMLElement);
    }
    unmount();
    expect(opener).toHaveFocus();
    opener.remove();
  });

  it('bare: không có thanh tiêu đề, tên ngăn vẫn có cho trình đọc màn hình; data-spot ẩn ngăn khi Gen chỉ phần tử', () => {
    render(<DetailSheet open bare title="Hỏi Gen" onClose={() => undefined} data-spot="on" data-testid="sh">khung chat</DetailSheet>);
    expect(screen.getByRole('dialog', { name: 'Hỏi Gen' })).toBeInTheDocument();
    expect(document.querySelector('.kit-sheet__head')).toBeNull();
    expect(screen.getByTestId('sh')).toHaveAttribute('data-spot', 'on');
  });
});
