/**
 * v0.1.42 (F-61): mỗi thẻ một chỗ — quét tĩnh apps/web/src: thẻ cập nhật, tài khoản CLI, mã PIN, Gen-hub chỉ được
 * render ở đúng một file; thuật ngữ cũ "Chuỗi ưu tiên", "Hộp thư ý nghĩa" không còn trong chữ của app.
 */
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join, relative, resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

const SRC = resolve(__dirname, '../../src');
const CONTRACTS = resolve(__dirname, '../../../../packages/contracts/src');

function files(dir: string): string[] {
  return readdirSync(dir).flatMap((f) => {
    const p = join(dir, f);
    return statSync(p).isDirectory() ? files(p) : /\.tsx?$/.test(f) ? [p] : [];
  });
}

/** Các file (đường dẫn tương đối `apps/web/src`) có chứa `needle`. */
function where(needle: string | RegExp, root = SRC): string[] {
  return files(root)
    .filter((f) => {
      const text = readFileSync(f, 'utf8');
      return typeof needle === 'string' ? text.includes(needle) : needle.test(text);
    })
    .map((f) => relative(root, f).split('\\').join('/'))
    .sort();
}

describe('mỗi thẻ một chỗ', () => {
  it('<UpdateCard chỉ ở Cài đặt › Sao lưu & cập nhật (StorageTab)', () => {
    expect(where(/<UpdateCard\b/)).toEqual(['screens/system/StorageTab.tsx']);
  });

  it('<CliCard chỉ ở Kết nối', () => {
    expect(where(/<CliCard\b/)).toEqual(['screens/connections/ConnectionsScreen.tsx']);
  });

  it('<PinCard chỉ ở Tài khoản của tôi', () => {
    expect(where(/<PinCard\b/)).toEqual(['account/AccountPage.tsx']);
  });

  it('<HubLinkCard chỉ ở Kết nối', () => {
    expect(where(/<HubLinkCard\b/)).toEqual(['screens/connections/ConnectionsScreen.tsx']);
  });

  it('Tổng quan có dòng báo bản mới (UpdateNotice), không có thẻ cập nhật', () => {
    expect(where(/<UpdateNotice\b/)).toEqual(['screens/queue/OverviewScreen.tsx']);
  });
});

describe('thuật ngữ mới', () => {
  it('"Chuỗi ưu tiên" không còn ở đâu (→ "Chuỗi chuyển hướng")', () => {
    expect(where(/chuỗi ưu tiên/i)).toEqual([]);
    expect(where(/chuỗi ưu tiên/i, CONTRACTS)).toEqual([]);
  });

  it('"Hộp thư ý nghĩa" không còn trong apps/web/src và packages/contracts/src (→ "Hộp thư")', () => {
    expect(where(/hộp thư ý nghĩa/i)).toEqual([]);
    expect(where(/hộp thư ý nghĩa/i, CONTRACTS)).toEqual([]);
  });
});

/** Bỏ comment dòng và comment khối (kể cả trong JSX) — chỉ còn chữ app hiện ra và mã. */
function stripComments(src: string): string {
  return src.replace(/\/\*[\s\S]*?\*\//g, '').replace(/(^|[^:'"`])\/\/.*$/gm, '$1');
}

/** Các file có `needle` trong phần KHÔNG phải comment. */
function whereInCode(needle: string, root = SRC): string[] {
  return files(root)
    .filter((f) => stripComments(readFileSync(f, 'utf8')).includes(needle))
    .map((f) => relative(root, f).split('\\').join('/'))
    .sort();
}

describe('tên menu cũ không còn trong chữ của app (review v0.1.42)', () => {
  const OLD = ['Điều khiển hệ thống', 'Dữ liệu & lưu trữ', 'Tổng quan điều hành', 'Nhóm & Con người'];
  for (const t of OLD) {
    it(`"${t}"`, () => {
      expect(whereInCode(t)).toEqual([]);
      expect(whereInCode(t, CONTRACTS)).toEqual([]);
    });
  }

  it('bộ lọc comment không nuốt chữ thật', () => {
    expect(stripComments("const a = 'x'; // Dữ liệu & lưu trữ\n/* Điều khiển hệ thống */ const b = 'Nhóm & Con người';")).toBe(
      "const a = 'x'; \n const b = 'Nhóm & Con người';",
    );
    expect(stripComments("const u = 'https://a.b/c';")).toBe("const u = 'https://a.b/c';");
  });
});

