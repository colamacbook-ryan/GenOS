/**
 * v0.1.70 (E1): câu chữ sạch ở web — tên màn "API & Model" (không chỉ đường bằng tên nhóm menu "Agent & Model"),
 * «khoá» (không «khóa»), Sức khoẻ hệ thống ở Hôm nay dùng tên kênh đúng và bỏ kênh chỉ đọc, Hạn lưu dữ liệu
 * không dùng chữ "Kho" của Kho dữ liệu cho tin nhắn, múi giờ nói đúng việc nào theo giờ Việt Nam.
 */
import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { MODEL_UNAVAILABLE_TEXT } from '../../src/lib/friendlyError';
import { EMPTY_AGENTS_TEXT, EMPTY_AGENTS_TEXT_ASK } from '../../src/lib/pickers';
import { healthChannelRows } from '../../src/screens/queue/queueModel';
import { RETENTION_LABEL } from '../../src/screens/system/systemModel';
import { TOOL_LABEL, toolLabel } from '../../src/gen/toolLabels';
import { SETUP_STEPS, STEP_DESCRIPTIONS } from '../../src/setup/steps';
import { WRITE_MISSING_TEXT } from '../../src/screens/mcp/mcpModel';

const SRC = resolve(__dirname, '../../src');
const read = (rel: string) => readFileSync(resolve(SRC, rel), 'utf8');
/** Mã nguồn bỏ chú thích (chỉ chữ hiển thị mới tính — giống `check_terms.py`, không quét chú thích). */
const readCode = (rel: string) =>
  read(rel)
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .replace(/(^|\s)\/\/\s.*$/gm, '$1')
    .replace(/\{\/\*[\s\S]*?\*\/\}/g, '');

describe('tên màn "API & Model"', () => {
  it('câu lỗi và câu chỉ đường không còn "Agent & Model"', () => {
    for (const t of [MODEL_UNAVAILABLE_TEXT, EMPTY_AGENTS_TEXT, EMPTY_AGENTS_TEXT_ASK]) expect(t).not.toContain('Agent & Model');
    expect(MODEL_UNAVAILABLE_TEXT).toContain('API & Model');
    expect(EMPTY_AGENTS_TEXT).toContain('Danh tính Agent');
  });
});

describe('«khoá», không «khóa»', () => {
  it('thẻ Jev', () => {
    expect(read('screens/system/JevCard.tsx')).not.toMatch(/[Kk]hóa/);
    expect(read('screens/system/jevModel.ts')).not.toMatch(/[Kk]hóa/);
  });
});

describe('Hôm nay › Sức khoẻ hệ thống — dòng kênh', () => {
  it('tên đẹp cho mọi kênh, bỏ kênh chỉ đọc (LinkedIn), giữ thứ tự máy chủ trả', () => {
    const rows = healthChannelRows([
      { type: 'zalo', active: 1 },
      { type: 'whatsapp', active: 0 },
      { type: 'telegram', active: 0 },
      { type: 'linkedin', active: 0 },
    ]);
    expect(rows.map((r) => r.name)).toEqual(['Kênh Zalo', 'Kênh WhatsApp', 'Kênh Telegram']);
    expect(rows.map((r) => r.active)).toEqual([1, 0, 0]);
  });
  it('kênh lạ: viết hoa chữ đầu, không in khoá thô chữ thường', () => {
    expect(healthChannelRows([{ type: 'viber', active: 0 }]).map((r) => r.name)).toEqual(['Kênh Viber']);
  });
});

describe('Hạn lưu dữ liệu', () => {
  it('nhãn không mượn chữ "Kho" (Kho dữ liệu của Sếp là thứ khác)', () => {
    for (const label of Object.values(RETENTION_LABEL)) expect(label).not.toMatch(/\bKho\b/);
    expect(RETENTION_LABEL['raw.events']).toMatch(/^Tin gốc/);
    expect(RETENTION_LABEL['clean.meaning_units']).toMatch(/^Tin đã lọc/);
  });
});

describe('Cài đặt › Tổ chức', () => {
  it('nói rõ Bản tin, chi phí AI và dọn dữ liệu luôn theo giờ Việt Nam', () => {
    expect(read('screens/system/OrgTab.tsx')).toMatch(/Bản tin Gen[^<]*chi phí AI[^<]*giờ Việt Nam/);
  });
});

// ─── E2: Hướng dẫn và Gen nói đúng chữ ───────────────────────────────────────────────────────────────────────────
const E2_FILES = [
  'guide/guideContent.ts', 'guide/bossChecksModel.ts', 'setup/steps.ts', 'setup/Step11Backup.tsx', 'setup/Step1Welcome.tsx',
  'setup/validation.ts', 'setup/one/StepAccount.tsx', 'screens/mcp/HubLinkCard.tsx', 'screens/mcp/mcpModel.ts', 'gen/toolLabels.ts',
  'screens/system/PinCard.tsx', 'account/AccountPage.tsx',
];

describe('thuật ngữ chuẩn trong Hướng dẫn, thiết lập, thẻ Gen-hub', () => {
  it.each(E2_FILES)('%s: không còn "Kho tri thức", "nhập PIN", "(TUI)", "Nghe im lặng", "giữ 7 bản gần nhất"', (rel) => {
    const src = readCode(rel);
    expect(src).not.toMatch(/Kho tri thức/);
    expect(src).not.toMatch(/\b[Nn]hập PIN\b/);
    expect(src).not.toMatch(/\(TUI\)/);
    expect(src).not.toMatch(/(?<!Lắng )[Nn]ghe im lặng/);
    expect(src).not.toMatch(/giữ 7 bản gần nhất/);
  });

  it('chữ chính không lộ tên quyền kho_create / kho_update / mã PHIEN-… (tên kỹ thuật chỉ nằm trong ngoặc)', () => {
    for (const rel of ['guide/bossChecksModel.ts', 'guide/guideContent.ts']) {
      const src = readCode(rel).replace(/\([^()]*kho_create[^()]*\)/g, '');
      expect(src, rel).not.toMatch(/kho_create|kho_update|PHIEN/);
    }
    expect(WRITE_MISSING_TEXT).toMatch(/quyền ghi Kho/);
    expect(WRITE_MISSING_TEXT.replace(/\([^()]*\)/g, '')).not.toMatch(/kho_create|kho_update/);
  });

  it('nhãn bước tra Kho theo tên Kho Sếp đặt (relabel), mặc định "Kho dữ liệu"', () => {
    for (const n of ['hub.kho_summary', 'hub.kho_search', 'hub.kho_get'] as const) {
      expect(TOOL_LABEL[n]).toBe('Kho dữ liệu (Gen-hub)');
      expect(toolLabel(n)).toBe('Kho dữ liệu (Gen-hub)');
      expect(toolLabel(n, 'Kho của Ryan')).toBe('Kho của Ryan (Gen-hub)');
      expect(toolLabel(n, '   ')).toBe('Kho dữ liệu (Gen-hub)');
    }
    expect(toolLabel('deal.list', 'Kho X')).toBe('deal');
  });

  it('một câu sao lưu duy nhất ở thiết lập (đúng vòng đời 7 ngày + 4 tuần + 12 tháng)', () => {
    const SENTENCE = 'giữ 7 bản theo ngày, 4 theo tuần, 12 theo tháng';
    const step = SETUP_STEPS.find((s) => s.n === 11);
    expect(step?.content).toContain(SENTENCE);
    expect(STEP_DESCRIPTIONS[11]).toContain(SENTENCE);
    expect(read('setup/Step11Backup.tsx')).toContain(SENTENCE);
    expect(read('guide/guideContent.ts')).toContain(SENTENCE);
  });

  it('chế độ nghe ở bước 6 gọi "Lắng nghe im lặng"', () => {
    expect(JSON.stringify(SETUP_STEPS)).toContain('Lắng nghe im lặng');
  });
});
