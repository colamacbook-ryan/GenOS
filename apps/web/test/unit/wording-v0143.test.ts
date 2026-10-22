import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { GEN_TARGETS, SCREENS, type NavItem } from '@gen-harness/contracts';
import { triageBadges } from '../../src/screens/queue/queueModel';
import { itemTitle } from '../../src/shell/navModel';
import { readableNotes } from '../../src/update/updateModel';
import { buildNavigation } from '../mock-api';
import { SETUP_STEPS, STEP_DESCRIPTIONS } from '../../src/setup/steps';

/**
 * v0.1.43 (F-62): tooltip thanh bên ("tên — mô tả") và nhãn màn phải tiếng Việt — không còn từ tiếng Anh nội bộ
 * (SSOT, bridge, tên màn trong thiết kế gốc…). Ghi chú phát hành bỏ tiền tố conventional commit.
 */
const BLOCKED = [
  'SSOT', 'bridge', 'Workbench', 'Overview', 'Inbox', 'Tasks', 'Reminders', 'Pipeline', 'Raw', 'Lake', 'Clean',
  'Rules', 'Refinery', 'Identity', 'Resolution', 'Review', 'Care', 'Quality', 'Deals', 'Cases', 'Documents',
  'Knowledge', 'Search', 'Settings', 'Connections', 'Team', 'Opportunity', 'Board', 'Living', 'Relationship',
  'Supply', 'Backend', 'persona', 'whitelist', 'Template', 'Endpoint',
];
const BLOCKED_RE = new RegExp(`(?<![\\p{L}\\p{N}_])(${BLOCKED.join('|')})(?![\\p{L}\\p{N}_])`, 'iu');

function englishIn(text: string): string | null {
  return BLOCKED_RE.exec(text)?.[1] ?? null;
}

function walk(items: NavItem[], out: NavItem[] = []): NavItem[] {
  for (const it of items) {
    out.push(it);
    walk(it.children ?? [], out);
  }
  return out;
}

describe('tooltip thanh bên không còn tiếng Anh (F-62)', () => {
  it('mọi mục SCREENS', () => {
    for (const s of SCREENS) {
      const title = itemTitle({ key: s.key, name: s.name, en: s.en, icon: s.icon, badge: null, children: [] });
      expect(englishIn(title), `${s.key}: "${title}"`).toBeNull();
    }
  });

  it('mọi mục cây GET /navigation (mock)', () => {
    const items = buildNavigation().flatMap((d) => walk(d.groups));
    expect(items.length).toBeGreaterThan(10);
    for (const it of items) {
      const title = itemTitle(it);
      expect(englishIn(title), `${it.key ?? it.name}: "${title}"`).toBeNull();
    }
  });

  it('danh sách chặn bắt đúng theo ranh giới từ, không phân biệt hoa thường', () => {
    expect(englishIn('Kho sạch SSOT')).toBe('SSOT');
    expect(englishIn('Tin gốc Bridge gom về')).toBe('Bridge');
    expect(englishIn('Tin gốc các kênh gom về')).toBeNull();
    expect(englishIn('Cung ↔ Cầu — careful')).toBeNull();
  });

  it('Kho sạch: tên mới khớp ở contracts, API và mô tả Hộp thư đúng câu chung', () => {
    const clean = SCREENS.find((s) => s.key === 'clean')!;
    expect(clean.name).toBe('Kho sạch');
    expect(clean.subtitle).toBe('Dữ liệu đã phân loại và trí nhớ tạm theo ID');
    const py = readFileSync(resolve(__dirname, '../../../api/gh/shell/navigation.py'), 'utf8');
    expect(py).toContain('"Kho sạch", "Dữ liệu đã lọc & bộ nhớ làm việc"');
    expect(py).toContain('"Tin gốc các kênh gom về"');
    expect(py).not.toMatch(/SSOT|bridge/);
    const inbox = SCREENS.find((s) => s.key === 'inbox')!;
    expect(inbox.description).toBe(
      'Không phải tin nhắn thô. Mỗi dòng là một ý chính rút ra từ tin nhắn: nguồn, người liên quan, điểm ưu tiên, tóm tắt hai câu, gợi ý việc nên làm và tin gốc làm chứng cứ.',
    );
  });
});

/** Bỏ comment (khối và dòng) để chỉ còn chuỗi hiển thị/code. */
function stripComments(src: string): string {
  return src.replace(/\/\*[\s\S]*?\*\//g, '').replace(/(^|[^:'"`])\/\/.*$/gm, '$1');
}

const PACKAGE_FILES = [
  '../../../../packages/contracts/src/screens.ts',
  '../../src/shell/navModel.ts',
  '../../src/shell/Sidebar.tsx',
  '../../src/screens/data/dataModel.ts',
  '../../src/screens/data/PipelineStrip.tsx',
  '../../src/screens/core/Evidence.tsx',
  '../../src/screens/system/systemModel.ts',
  '../../src/screens/system/TriageCard.tsx',
  '../../src/screens/system/BrainTab.tsx',
  '../../src/setup/Step7Refinery.tsx',
  '../../src/gen/GenPanel.tsx',
  '../../src/gen/ProposalCard.tsx',
  '../../src/help/HelpPage.tsx',
];

describe('grep tĩnh: không còn "Kho sạch SSOT" / "đơn vị ý nghĩa" (F-62)', () => {
  it.each(PACKAGE_FILES)('%s', (rel) => {
    const code = stripComments(readFileSync(resolve(__dirname, rel), 'utf8'));
    expect(code).not.toContain('Kho sạch SSOT');
    expect(code).not.toContain('đơn vị ý nghĩa');
    expect(code).not.toContain('Clean SSOT');
  });
});

describe('readableNotes bỏ tiền tố conventional commit (F-62)', () => {
  it('gạch đầu dòng có phạm vi + số PR', () => {
    expect(readableNotes('- feat(providers): thêm nguồn X (#12)')).toBe('- Thêm nguồn X');
  });
  it('không gạch đầu dòng, không phạm vi', () => {
    expect(readableNotes('fix: sửa lỗi')).toBe('Sửa lỗi');
  });
  it('dấu * và breaking "!"', () => {
    expect(readableNotes('* refactor(api)!: đổi tên trường')).toBe('* Đổi tên trường');
  });
  it('dòng thường giữ nguyên', () => {
    expect(readableNotes('## Điểm mới\n- Nút cập nhật\n- fixed bug ở đâu đó')).toBe('## Điểm mới\n- Nút cập nhật\n- fixed bug ở đâu đó');
    expect(readableNotes('Ghi chú: feat: không ở đầu dòng')).toBe('Ghi chú: feat: không ở đầu dòng');
  });
  it('nhiều dòng trộn lẫn', () => {
    expect(readableNotes("## What's Changed\n- feat(web): lọc tin by @a in https://github.com/x/y/pull/3\n- chore: dọn dẹp")).toBe(
      '## Điểm mới\n- Lọc tin\n- Dọn dẹp',
    );
  });
});

describe('thiết lập không hứa điều không còn (F-23)', () => {
  it('mô tả bước 1 không còn nhắc ô ngôn ngữ / "cách bắt đầu" đã gỡ', () => {
    expect(STEP_DESCRIPTIONS[1]).toBe('Mã thiết lập chứng minh Sếp là người vừa chạy trình cài trên máy này.');
    expect(STEP_DESCRIPTIONS[1]).not.toMatch(/cách bắt đầu|ngôn ngữ/i);
  });

  it('nội dung bước 9 nói theo thang 3 mức, không còn "0–6, mặc định 4"', () => {
    const step9 = SETUP_STEPS.find((s) => s.n === 9)!;
    expect(step9.content).not.toMatch(/0–6|mặc định 4/);
    expect(step9.content).toMatch(/Gợi ý hoặc Soạn sẵn chờ duyệt/);
  });
});

describe('thẻ "Lọc tin" — Gen và huy hiệu dùng đúng tên mới', () => {
  it('đích Gen system.brain.triage, nhãn công cụ Gen và huy hiệu Hộp thư không còn "Lọc đầu"', () => {
    const t = GEN_TARGETS.find((x) => x.id === 'system.brain.triage')!;
    expect(t.label).toBe('Thẻ "Lọc tin"');
    expect(t.description).toMatch(/Thấp\/Vừa\/Cao/);
    expect(t.description).not.toMatch(/ngưỡng điểm chất lượng, dùng Jev/);
    const badges = triageBadges({ duplicate_of: null, duplicate_kind: null, spam: false, spam_reason: null, score: 82, low_score: false, reason: 'ok', source: 'rules' } as never);
    expect(badges.map((b) => b.label)).toEqual(['Điểm lọc 82']);
    // v0.1.49: nhãn bước tool của khung Gen chuyển từ GenPanel.tsx sang toolLabels.ts.
    const labels = readFileSync(resolve(__dirname, '../../src/gen/toolLabels.ts'), 'utf8');
    expect(labels).toContain("'refinery.summary': 'lọc tin'");
    const tools = readFileSync(resolve(__dirname, '../../../api/gh/gen/tools.py'), 'utf8');
    expect(tools).not.toMatch(/Lọc đầu Hộp thư/);
  });
});
