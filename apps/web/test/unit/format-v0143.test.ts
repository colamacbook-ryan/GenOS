import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { describe, expect, it } from 'vitest';
import { fmtVnd, initialsOf } from '../../src/lib/format';
import * as graphModel from '../../src/screens/graph/graphModel';
import * as marketModel from '../../src/screens/market/marketModel';
import * as peopleModel from '../../src/screens/people/peopleModel';
import * as queueModel from '../../src/screens/queue/queueModel';
import * as relationsModel from '../../src/screens/relations/relationsModel';

// F-38 (v0.1.43): lib/format.ts là nơi duy nhất định nghĩa initialsOf / fmtVnd; các *Model.ts chỉ re-export.

describe('lib/format — initialsOf, fmtVnd (F-38)', () => {
  it('initialsOf: chữ đầu của từ đầu + từ cuối, viết hoa, rỗng → "·"', () => {
    expect(initialsOf('Nguyễn Văn An')).toBe('NA');
    expect(initialsOf('  đặng   thị  ánh ')).toBe('ĐÁ');
    expect(initialsOf('  ')).toBe('·');
    expect(initialsOf('')).toBe('·');
    // Tên một từ (tên Zalo hay gặp): 2 ký tự đầu, không lặp chữ ("LL").
    expect(initialsOf('Lan')).toBe('LA');
    expect(initialsOf(' minh ')).toBe('MI');
    expect(initialsOf('Á')).toBe('Á');
  });

  it('fmtVnd: null/undefined → "—", còn lại phân cách nghìn kiểu Việt + " ₫"', () => {
    expect(fmtVnd(null)).toBe('—');
    expect(fmtVnd(undefined)).toBe('—');
    expect(fmtVnd(1500000)).toBe('1.500.000 ₫');
    expect(fmtVnd(0)).toBe('0 ₫');
  });

  it('các *Model.ts re-export đúng hàm chung (màn của gói khác không phải đổi import)', () => {
    for (const m of [graphModel, relationsModel, queueModel, peopleModel, marketModel]) {
      expect(m.initialsOf).toBe(initialsOf);
    }
    for (const m of [graphModel, relationsModel, peopleModel, marketModel]) {
      expect(m.fmtVnd).toBe(fmtVnd);
    }
  });

  it('không còn định nghĩa trùng initialsOf / fmtVnd dưới src/screens', () => {
    const root = resolve(__dirname, '../../src/screens');
    const files: string[] = [];
    const walk = (dir: string) => {
      for (const name of readdirSync(dir)) {
        const p = join(dir, name);
        if (statSync(p).isDirectory()) walk(p);
        else if (/\.tsx?$/.test(name)) files.push(p);
      }
    };
    walk(root);
    expect(files.length).toBeGreaterThan(10);
    const offenders = files.filter((f) => {
      const src = readFileSync(f, 'utf8');
      return /function\s+(initialsOf|fmtVnd)\b/.test(src) || /(const|let)\s+(initialsOf|fmtVnd)\s*=/.test(src);
    });
    expect(offenders).toEqual([]);
  });
});
