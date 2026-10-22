/**
 * v0.1.49 (QD-16) — chip "Đã tra …" trong khung Gen: mọi tool dữ liệu máy chủ có thể gọi (`DATA_TOOL_NAMES` trong
 * apps/api/gh/gen/envelope.py) phải có nhãn tiếng Việt — Sếp hỏi "Hôm nay tôi có lịch gì?" không được thấy
 * "Đã tra hub.calendar" / "Đã tra deal.list".
 */
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';
import { TOOL_LABEL, toolLabel } from '../../src/gen/toolLabels';

const ENVELOPE = resolve(__dirname, '../../../api/gh/gen/envelope.py');

/** Tên trong tuple `DATA_TOOL_NAMES = (...)` của envelope.py (bỏ dòng chú thích `# …`). */
function serverToolNames(): string[] {
  const src = readFileSync(ENVELOPE, 'utf8');
  const start = src.indexOf('DATA_TOOL_NAMES = (');
  const end = src.indexOf('DataToolName = Literal[', start);
  expect(start).toBeGreaterThanOrEqual(0);
  expect(end).toBeGreaterThan(start);
  const body = src
    .slice(start, end)
    .split('\n')
    .map((l) => l.replace(/#.*$/, ''))
    .join('\n');
  return [...body.matchAll(/"([a-z_]+\.[a-z_]+)"/g)].map((m) => m[1] as string);
}

describe('Nhãn bước tool của khung Gen', () => {
  it('phủ mọi tên trong DATA_TOOL_NAMES (envelope.py) — gồm 11 tool mới của v0.1.49', () => {
    const names = serverToolNames();
    expect(names.length).toBeGreaterThanOrEqual(31);
    for (const n of ['hub.calendar', 'hub.tasks', 'hub.mail_search', 'hub.mail_read', 'hub.drive_search', 'document.list', 'document.get', 'deal.list', 'deal.get', 'case.list', 'case.get']) {
      expect(names).toContain(n);
    }
    const missing = names.filter((n) => !(n in TOOL_LABEL));
    expect(missing).toEqual([]);
  });

  it('v0.1.54: coach.status (Gen hướng dẫn) có nhãn tiếng Việt cho Sếp', () => {
    expect(serverToolNames()).toContain('coach.status');
    expect(TOOL_LABEL).toHaveProperty(['coach.status']);
    expect(toolLabel('coach.status')).toBe('Việc cần làm & bài học');
  });

  it('nhãn là chữ thường cho Sếp, không phải tên kỹ thuật', () => {
    for (const [name, label] of Object.entries(TOOL_LABEL)) {
      expect(label.trim()).not.toBe('');
      expect(label).not.toContain(name);
      expect(label).not.toMatch(/^[a-z_]+\.[a-z_]+$/);
    }
    expect(toolLabel('hub.calendar')).toBe('lịch (Gen-hub)');
    expect(toolLabel('hub.mail_read')).toBe('mail (Gen-hub)');
    expect(toolLabel('deal.list')).toBe('deal');
    expect(toolLabel('case.get')).toBe('vụ việc');
    // Tên lạ (máy chủ mới hơn web) ⇒ câu chung, không lộ tên kỹ thuật.
    expect(toolLabel('hub.something_new')).toBe('dữ liệu');
  });
});
