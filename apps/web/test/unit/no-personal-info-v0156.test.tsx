/**
 * v0.1.56 — web giao cho Owner khác không được mang địa chỉ Gen-hub / tên riêng của chủ Gen-hub.
 *
 * - `KHO_LABEL` (contracts) = "Kho dữ liệu" và khớp `KHO_LABEL` của API (đọc tệp `apps/api/gh/hub_link/__init__.py`);
 * - chuỗi lỗi địa chỉ Gen-hub KHỚP CHỮ `ENDPOINT_INVALID_MSG` của API (đọc `hub_link/service.py`), chỉ mô tả dạng https://<máy-chủ>/mcp;
 * - ô nhập địa chỉ ở thẻ Gen-hub (Kết nối) và ở Việc Sếp cần làm dùng chỗ giữ chỗ chung;
 * - tiêu đề thẻ đề xuất / câu cảnh báo ghi Kho dùng tên Kho chung.
 */
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';
import { GEN_TARGETS, KHO_LABEL } from '@gen-harness/contracts';
import { BOSS_ERROR_TEXT } from '../../src/guide/bossChecksModel';
import { GUIDE } from '../../src/guide/guideContent';
import { KHO_WRITE_WARNING } from '../../src/gen/khoWriteModel';
import { PROPOSAL_TITLE } from '../../src/gen/proposalModel';

const API = resolve(__dirname, '../../../api/gh/hub_link');
const SRC = resolve(__dirname, '../../src');
const PLACEHOLDER = 'https://<địa-chỉ-gen-hub-của-bạn>/mcp';
// v0.1.57: KHÔNG ghi tên riêng / tên miền riêng thật vào test (repo công khai). Tên cũ lấy từ câu khớp của migration 0035
// (nguồn khớp duy nhất, đã mang chú thích allow-personal-info); mẫu riêng khác lấy từ GH_PERSONAL_PATTERNS (CI nạp từ secret,
// mỗi dòng một regex; trống thì bỏ qua) và KHÔNG bao giờ in ra — các kiểm bên dưới chỉ trả đúng/sai.
const SQL_0035 = readFileSync(resolve(__dirname, '../../../../db/sql/0035_v0156_hub_note_generic.sql'), 'utf8');
const OLD_NAME = SQL_0035.match(/AND note = '[^']*Gen đọc (.+?), lịch/)?.[1] ?? '';
const PRIVATE = (process.env.GH_PERSONAL_PATTERNS ?? '')
  .split('\n')
  .map((l) => l.trim())
  .filter((l) => l !== '' && !l.startsWith('#'))
  .map((l) => new RegExp(l, 'i'));
// Địa chỉ Gen-hub thật = tên miền có chấm ngay trước /mcp mà không phải example.* (giống quy tắc chung của CI).
const REAL_MCP = /https?:\/\/((?:[a-z0-9-]+\.)+[a-z]{2,})(?::\d+)?\/mcp\b/gi;
const hasRealHost = (text: string): boolean =>
  [...text.matchAll(REAL_MCP)].some((m) => !/(?:^|\.)example\.(?:test|com|org|net)$/i.test(m[1]));
const hasOldName = (text: string): boolean => OLD_NAME !== '' && text.toLowerCase().includes(OLD_NAME.toLowerCase());
const hasPrivate = (text: string): boolean => PRIVATE.some((r) => r.test(text));

describe('v0.1.56 — không lộ địa chỉ Gen-hub / tên riêng', () => {
  it('tên cũ lấy được từ migration 0035 (nếu không, các kiểm "không lộ tên" bên dưới thành vô nghĩa)', () => {
    expect(OLD_NAME).not.toBe('');
    expect(OLD_NAME).not.toBe(KHO_LABEL);
  });

  it('KHO_LABEL = "Kho dữ liệu" và khớp hằng của API', () => {
    expect(KHO_LABEL).toBe('Kho dữ liệu');
    const py = readFileSync(resolve(API, '__init__.py'), 'utf8');
    expect(py.match(/^KHO_LABEL\s*=\s*"([^"]+)"/m)?.[1]).toBe(KHO_LABEL);
  });

  it('câu lỗi HUB_ENDPOINT_INVALID khớp chữ ENDPOINT_INVALID_MSG của API, không nêu máy chủ thật', () => {
    const py = readFileSync(resolve(API, 'service.py'), 'utf8');
    const apiMsg = py.match(/^ENDPOINT_INVALID_MSG\s*=\s*"([^"]+)"/m)?.[1];
    expect(apiMsg).toBe('Địa chỉ Gen-hub không hợp lệ — kiểm tra lại (dạng https://<máy-chủ>/mcp)');
    expect(BOSS_ERROR_TEXT.HUB_ENDPOINT_INVALID).toBe(apiMsg);
    expect(hasRealHost(BOSS_ERROR_TEXT.HUB_ENDPOINT_INVALID) || hasPrivate(BOSS_ERROR_TEXT.HUB_ENDPOINT_INVALID)).toBe(false);
  });

  it('thẻ đề xuất và câu cảnh báo ghi Kho dùng tên Kho chung', () => {
    expect(PROPOSAL_TITLE.kho_create).toBe(`Ghi vào ${KHO_LABEL}`);
    expect(PROPOSAL_TITLE.kho_update).toBe(`Ghi vào ${KHO_LABEL}`);
    expect(KHO_WRITE_WARNING).toContain(KHO_LABEL);
    expect(hasOldName(KHO_WRITE_WARNING)).toBe(false);
  });

  it('hướng dẫn bước 14 và mục tiêu Gen không nêu máy chủ thật / tên riêng', () => {
    const step14 = GUIDE.find((g) => g.n === 14)!;
    const text = JSON.stringify(step14);
    expect(hasRealHost(text) || hasPrivate(text)).toBe(false);
    expect(text).toContain('https://…/mcp');
    expect(hasOldName(JSON.stringify(GEN_TARGETS))).toBe(false);
  });

  it('ô nhập địa chỉ Gen-hub dùng chỗ giữ chỗ chung ở cả hai nơi', () => {
    for (const rel of ['screens/mcp/HubLinkCard.tsx', 'guide/BossChecksPage.tsx']) {
      const src = readFileSync(resolve(SRC, rel), 'utf8');
      expect(src, rel).toContain(`placeholder="${PLACEHOLDER}"`);
      expect(hasRealHost(src) || hasPrivate(src), rel).toBe(false);
    }
  });
});
