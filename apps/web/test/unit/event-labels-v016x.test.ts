/**
 * v0.1.69 (D5; F062): một bảng nhãn loại sự kiện (15 loại, tiếng Việt) cho mọi màn — `packages/contracts/src/eventTypes.ts`,
 * bằng `core.lookup` / `EVENT_LABELS` của máy chủ (test_event_labels_v016x.py giữ hai phía khớp nhau).
 */
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';
import { EVENT_INTENT_OPTIONS, EVENT_TYPES, NOISE_CODE, eventTypeLabel } from '@gen-harness/contracts';

const SRC = resolve(__dirname, '../../src');
const read = (rel: string) => readFileSync(resolve(SRC, rel), 'utf8');

describe('F062 — một bảng nhãn loại sự kiện', () => {
  it('đủ 15 loại, mỗi loại một nhãn tiếng Việt khác mã, không trùng nhau', () => {
    expect(EVENT_TYPES).toHaveLength(15);
    const labels = EVENT_TYPES.map((e) => e.label);
    expect(new Set(labels).size).toBe(15);
    for (const e of EVENT_TYPES) {
      expect(e.label).not.toBe(e.code);
      expect(e.label).not.toMatch(/^[A-Za-z]+$/);
      expect(eventTypeLabel(e.code)).toBe(e.label);
    }
    expect(eventTypeLabel('Complained')).toBe('Phàn nàn');
    expect(labels).not.toContain('Than phiền');
  });

  it('mã lạ ⇒ "Tin khác" (không lộ mã tiếng Anh); nhãn "Noise" của Kho thô ⇒ "Tin rác"', () => {
    expect(eventTypeLabel('SomethingNew')).toBe('Tin khác');
    expect(eventTypeLabel('')).toBe('Tin khác');
    expect(eventTypeLabel(NOISE_CODE)).toBe('Tin rác');
  });

  it('bộ lọc "Ý định" chọn được cả 15 loại, đứng sau "Tất cả"', () => {
    expect(EVENT_INTENT_OPTIONS[0]).toEqual({ value: '', label: 'Tất cả' });
    expect(EVENT_INTENT_OPTIONS.slice(1).map((o) => o.value)).toEqual(EVENT_TYPES.map((e) => e.code));
    expect(EVENT_INTENT_OPTIONS.slice(1).map((o) => o.label)).toEqual(EVENT_TYPES.map((e) => e.label));
  });

  it('các màn không còn bảng nhãn riêng và không in thẳng mã loại sự kiện', () => {
    expect(read('screens/market/marketModel.ts')).not.toMatch(/Complained: '|AskedPrice: '/);
    expect(read('screens/queue/InboxScreen.tsx')).not.toMatch(/label: 'Than phiền'|value: 'AskedPrice'/);
    expect(read('screens/data/RawScreen.tsx')).not.toMatch(/KNOWN_LABELS = \['AskedPrice'/);
    const rawCode = /\{(?:r|tl|u|item)\.event_type\}/;
    for (const f of ['screens/data/CleanScreen.tsx', 'screens/relations/ProfileScreen.tsx', 'screens/core/Evidence.tsx']) {
      expect(read(f).split('\n').filter((l) => rawCode.test(l)), f).toEqual([]);
    }
    expect(read('screens/data/CleanScreen.tsx')).toMatch(/eventTypeLabel\(r\.event_type\)/);
    expect(read('screens/relations/ProfileScreen.tsx')).toMatch(/eventTypeLabel\(tl\.event_type\)/);
    expect(read('screens/core/Evidence.tsx')).toMatch(/eventTypeLabel\(u\.event_type\)/);
  });
});
