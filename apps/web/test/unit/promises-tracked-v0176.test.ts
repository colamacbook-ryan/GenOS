import { describe, expect, it } from 'vitest';
import { PROMISES_TRACKED } from '../../src/lib/features';
import { kpiCards } from '../../src/owner/ownerModel';

/** v0.1.76 (T11): cờ web khớp máy chủ (`gh/biz/queue/service.py::PROMISES_TRACKED = True`, detector W1 của T4 ghi `biz.promises`). */
describe('PROMISES_TRACKED bật từ v0.1.76', () => {
  it('cờ bật và Hôm nay có 4 số kể cả «Lời hứa quá hạn»', () => {
    expect(PROMISES_TRACKED).toBe(true);
    const cards = kpiCards({ hot: 3, cooling: 2, open_opps: 12, open_value_vnd: 2_400_000_000, overdue_promises: 7 });
    expect(cards.map((c) => c.key)).toContain('overdue_promises');
    expect(cards).toHaveLength(4);
  });
});
