/**
 * v0.1.59 (G4) — hàm THUẦN của màn Thông tin (owner/feed/feedModel.ts) và Quan hệ (owner/relations/relationsModel.ts): câu chữ đời thường,
 * không bịa số, số điện thoại luôn che, chuỗi lạ (object) không bao giờ lọt ra chữ. Luồng giao diện do e2e owner-feed-v0159 /
 * owner-relations-v0159 kiểm.
 */
import { describe, expect, it } from 'vitest';
import type { FeedItem, InboxDetail, OwnerRelationRow, Profile } from '@gen-harness/contracts';
import {
  FEED_VIEWS,
  channelLabel,
  detailHead,
  draftDoneText,
  emptyText,
  evidenceLines,
  filteredText,
  headSubtitle,
  headline,
  isFeedView,
  mergePages,
  profileLink,
  rowMeta,
  rowTime,
  safeCount,
  whenText,
} from '../../src/owner/feed/feedModel';
import {
  askGenPrompt,
  bridgeCount,
  careHeadline,
  channelLines,
  countLabel,
  lastContactText,
  maskPhone,
  openValueText,
  parseListParam,
  parseMatch,
  personKey,
  profileSubtitle,
  reasonBadge,
  recentEvents,
  relationLabel,
  scoreLines,
  scoreSentence,
  summaryLines,
  tenureText,
  trendFromMetric,
} from '../../src/owner/relations/relationsModel';

const NOW = Date.parse('2026-10-16T05:00:00Z'); // 12:00 giờ VN
const ago = (ms: number) => new Date(NOW - ms).toISOString();
const H = 3_600_000;
const D = 24 * H;

const unit = (over: Partial<FeedItem> = {}): FeedItem => ({
  id: 'fd-1', item_type: 'unit', title: 'Than phiền', summary: 'Giao trễ', importance: 'Khẩn', event_type: 'Complained', created_at: ago(H),
  person: { id: 'p-bao', name: 'Nguyễn Văn Bảo' }, group: { id: 'g-1', name: 'Nhóm Thành Phát', channel: 'zalo' }, suggested_action: null, ...over,
});

describe('Thông tin — đầu danh sách, nút lọc', () => {
  it('5 mục lọc đúng thứ tự, "Quan trọng" đầu tiên; isFeedView chỉ nhận 5 mục đó', () => {
    expect(FEED_VIEWS.map((d) => d.label)).toEqual(['Quan trọng', 'Tất cả', 'Cơ hội', 'Cảnh báo', 'Chờ duyệt']);
    expect(FEED_VIEWS.map((d) => d.view)).toEqual(['important', 'all', 'opportunity', 'alert', 'approval']);
    expect(FEED_VIEWS.every((d) => isFeedView(d.view))).toBe(true);
    for (const bad of ['', 'priority', undefined, null, 5, {}]) expect(isFeedView(bad)).toBe(false);
  });

  it('headline: Quan trọng = important − urgent_open; bỏ phần bằng 0; số lạ ⇒ 0', () => {
    expect(headline({ important: 7, urgent_open: 1 })).toEqual({ total: 7, breakdown: '1 Khẩn, 6 Quan trọng' });
    expect(headline({ important: 3, urgent_open: 3 })).toEqual({ total: 3, breakdown: '3 Khẩn' });
    expect(headline({ important: 4, urgent_open: 0 })).toEqual({ total: 4, breakdown: '4 Quan trọng' });
    expect(headline({ important: 0, urgent_open: 0 })).toEqual({ total: 0, breakdown: '' });
    expect(headline({ important: 2, urgent_open: 9 })).toEqual({ total: 2, breakdown: '2 Khẩn' }); // Khẩn không vượt tổng
    expect(headline(undefined)).toEqual({ total: 0, breakdown: '' });
    expect(headline({ important: Number.NaN, urgent_open: -3 })).toEqual({ total: 0, breakdown: '' });
    expect(safeCount('7')).toBe(0);
    expect(safeCount(3.9)).toBe(3);
  });

  it('chân danh sách và câu rỗng: "Gen đã lọc N tin" chỉ khi N > 0', () => {
    expect(filteredText(124)).toBe('Gen đã lọc 124 tin rác/trùng trong 7 ngày');
    expect(filteredText(0)).toBeNull();
    expect(filteredText(undefined)).toBeNull();
    expect(emptyText('important', 124)).toBe('Không có tin cần Sếp để ý. Gen đã lọc 124 tin.');
    expect(emptyText('important', 0)).toBe('Không có tin cần Sếp để ý.');
    for (const v of ['all', 'opportunity', 'alert', 'approval'] as const) expect(emptyText(v, 5)).not.toContain('Gen đã lọc');
  });
});

describe('Thông tin — dòng tin', () => {
  it('rowMeta: người · nhóm · kênh; tin 1-1 ghi "Nhắn riêng"; cảnh báo không nhóm bỏ phần nhóm; kênh lạ bỏ', () => {
    expect(rowMeta(unit())).toBe('Nguyễn Văn Bảo · Nhóm Thành Phát · Zalo');
    expect(rowMeta(unit({ group: null }))).toBe('Nguyễn Văn Bảo · Nhắn riêng');
    expect(rowMeta(unit({ item_type: 'alert', group: null }))).toBe('Nguyễn Văn Bảo');
    expect(rowMeta(unit({ group: { id: 'g', name: 'Nhóm A', channel: 'fax' } }))).toBe('Nguyễn Văn Bảo · Nhóm A');
    expect(rowMeta(unit({ person: null, group: null, item_type: 'draft' }))).toBe('');
    expect(channelLabel('whatsapp')).toBe('WhatsApp');
    expect(channelLabel({})).toBe('');
  });

  it('rowTime / whenText theo giờ VN: hôm nay "HH:mm", hôm qua, ngày cũ "dd/MM"; thiếu/sai ⇒ rỗng', () => {
    expect(rowTime(ago(H), NOW)).toBe('11:00');
    expect(rowTime(ago(D + H), NOW)).toBe('Hôm qua');
    expect(rowTime(ago(5 * D), NOW)).toBe('11/10');
    expect(rowTime(null, NOW)).toBe('');
    expect(rowTime('không phải giờ', NOW)).toBe('');
    expect(whenText(ago(H), NOW)).toBe('hôm nay 11:00');
    expect(whenText(ago(D + H), NOW)).toBe('hôm qua 11:00');
    expect(whenText(ago(5 * D), NOW)).toBe('11/10 12:00');
  });

  it('mergePages: nối các trang, bỏ dòng trùng mã, bỏ dòng hỏng', () => {
    const a = unit({ id: 'a' });
    const b = unit({ id: 'b' });
    expect(mergePages([{ items: [a, b] }, { items: [b, unit({ id: 'c' })] }]).map((i) => i.id)).toEqual(['a', 'b', 'c']);
    expect(mergePages(undefined)).toEqual([]);
    expect(mergePages([{ items: 'x' as never }, { items: [null as never, a] }]).map((i) => i.id)).toEqual(['a']);
  });
});

describe('Thông tin — khung ngữ cảnh', () => {
  it('detailHead lấy từ dòng tin; mở thẳng bằng ?tin= thì lấy từ chi tiết Hộp thư (người khác nhóm)', () => {
    const fromItem = detailHead(unit(), undefined)!;
    expect(fromItem).toMatchObject({ itemType: 'unit', title: 'Than phiền', importance: 'Khẩn', personId: 'p-bao', personName: 'Nguyễn Văn Bảo', groupName: 'Nhóm Thành Phát', channel: 'Zalo' });
    expect(headSubtitle(fromItem, NOW)).toBe('Nhóm Thành Phát · Zalo · hôm nay 11:00');
    expect(detailHead(undefined, undefined)).toBeNull();
    const detail = {
      id: 'fd-3', item_type: 'unit', title: 'Hỏi giá', summary: 'Hỏi giá 3 cont', created_at: ago(H), suggested_action: null,
      subject: { id: 'p-hau', code: 'PER-1', name: 'Trần Văn Hậu', type: 'customer', org_name: null }, group: null, units: [],
    } as unknown as InboxDetail;
    const fromDetail = detailHead(undefined, detail)!;
    expect(fromDetail).toMatchObject({ personId: 'p-hau', personName: 'Trần Văn Hậu', groupName: '', importance: '' });
    expect(headSubtitle(fromDetail, NOW)).toBe('Nhắn riêng · hôm nay 11:00');
    // chủ thể là nhóm (có `channel`) thì không nhầm thành người
    const groupDetail = { ...detail, subject: { id: 'g-9', code: 'GRP', name: 'Nhóm Gỗ', channel: 'zalo' } } as unknown as InboxDetail;
    expect(detailHead(undefined, groupDetail)).toMatchObject({ personId: null, personName: '', groupName: 'Nhóm Gỗ', channel: 'Zalo' });
  });

  it('gợi ý lấy từ dòng tin trước, rồi chi tiết; chuỗi lạ (object) không lọt ra', () => {
    expect(detailHead(unit({ suggested_action: 'Gọi hỏi thăm' }), undefined)!.suggestedAction).toBe('Gọi hỏi thăm');
    expect(detailHead(unit(), { suggested_action: 'Nhắn lại' } as InboxDetail)!.suggestedAction).toBe('Nhắn lại');
    const weird = detailHead(unit({ title: { x: 1 } as never, summary: ['a'] as never }), undefined)!;
    expect(weird.title).toBe('');
    expect(weird.summary).toBe('');
  });

  it('evidenceLines: câu kết luận, bỏ rỗng/trùng, tối đa 6', () => {
    const units = Array.from({ length: 9 }, (_, i) => ({ conclusion: i === 2 ? '  ' : i === 3 ? 'Ý 0' : `Ý ${i}` }));
    expect(evidenceLines({ units: units as never })).toEqual(['Ý 0', 'Ý 1', 'Ý 4', 'Ý 5', 'Ý 6', 'Ý 7']);
    expect(evidenceLines(undefined)).toEqual([]);
    expect(evidenceLines({ units: [{ conclusion: { a: 1 } }] as never })).toEqual([]);
  });

  it('câu báo sau khi xử lý nháp; liên kết hồ sơ mã hoá mã người', () => {
    expect(draftDoneText('sent')).toBe('Đã duyệt và gửi bản nháp trả lời.');
    expect(draftDoneText('approved')).toBe('Đã duyệt và gửi bản nháp trả lời.');
    expect(draftDoneText('rejected')).toBe('Đã từ chối bản nháp trả lời.');
    expect(draftDoneText('failed')).toContain('chưa gửi được');
    expect(profileLink('p-bao')).toBe('/owner/quan-he?nguoi=p-bao');
    expect(profileLink('a b&c')).toBe('/owner/quan-he?nguoi=a%20b%26c');
  });
});

const row = (over: Partial<OwnerRelationRow> = {}): OwnerRelationRow => ({ id: 'p-1', name: 'Bùi Đức Thắng', subtitle: 'Chưa phân loại', metric_text: '', to: '/profile?id=p-1', ...over });

describe('Quan hệ — danh sách, cột', () => {
  it('parseListParam: 4 danh sách; cooling (link cũ) và giá trị lạ về "Cần chăm"', () => {
    expect(parseListParam('hot')).toBe('hot');
    expect(parseListParam('bridges')).toBe('bridges');
    expect(parseListParam('matches')).toBe('matches');
    expect(parseListParam('care')).toBe('care');
    for (const v of ['cooling', '', null, undefined, 'x', 3]) expect(parseListParam(v)).toBe('care');
  });

  it('careHeadline đếm theo lý do; countLabel ghi "20+" khi đủ trang; personKey ưu tiên person_id', () => {
    const rows = [row({ reason: 'churn' }), row({ id: 'p-2', reason: 'churn' }), row({ id: 'p-3', reason: 'silent' })];
    expect(careHeadline(rows)).toEqual({ total: 3, breakdown: '2 sắp rời, 1 lâu chưa liên lạc' });
    expect(careHeadline([])).toEqual({ total: 0, breakdown: '' });
    expect(countLabel(4, 20)).toBe('4');
    expect(countLabel(20, 20)).toBe('20+');
    expect(countLabel(undefined, 20)).toBe('');
    expect(personKey(row({ person_id: 'p-9' }))).toBe('p-9');
    expect(personKey(row())).toBe('p-1');
  });

  it('lần cuối liên lạc, cơ hội mở, lý do, xu hướng — không bịa số', () => {
    expect(lastContactText(0)).toBe('Hôm nay');
    expect(lastContactText(1)).toBe('Hôm qua');
    expect(lastContactText(18)).toBe('18 ngày trước');
    for (const v of [undefined, null, -1, Number.NaN, '5']) expect(lastContactText(v)).toBe('Chưa có');
    expect(openValueText(180_000_000)).toBe('180 triệu ₫');
    for (const v of [0, undefined, null, -5, '9']) expect(openValueText(v)).toBe('Chưa có'); // không "0 ₫"
    expect(reasonBadge('churn')).toEqual({ label: 'Sắp rời', tone: 'warn' });
    expect(reasonBadge('silent')).toEqual({ label: 'Lâu chưa liên lạc', tone: 'neutral' });
    expect(reasonBadge(undefined)).toBeNull();
    expect(trendFromMetric('Độ nóng 91 · đang tăng')).toBe('up');
    expect(trendFromMetric('Độ nóng 84 · đang giảm')).toBe('down');
    expect(trendFromMetric('Có thể sắp rời · 6 ngày chưa liên lạc')).toBeNull();
  });

  it('Cung–cầu: tách "ai cần / cần gì / ai có / khớp"; không đọc được thì giữ nguyên chữ', () => {
    expect(parseMatch(row({ name: 'Ván MDF E1 ↔ Ván E1 17mm kho Bình Dương', subtitle: 'Trần Văn Hậu cần · Lâm Văn Được có', metric_text: 'Khớp 94%' }))).toEqual({
      who_needs: 'Trần Văn Hậu', need: 'Ván MDF E1', who_has: 'Lâm Văn Được', supply: 'Ván E1 17mm kho Bình Dương', percent: 94,
    });
    // cùng mặt hàng ⇒ không lặp ở dòng phụ
    expect(parseMatch(row({ name: 'Kho lạnh ↔ Kho lạnh', subtitle: 'A cần · B có', metric_text: 'Khớp 81,6%' }))).toMatchObject({ supply: '', percent: 82 });
    expect(parseMatch(row({ name: 'Chỉ một vế', subtitle: 'Chữ lạ', metric_text: 'Không rõ' }))).toEqual({ who_needs: 'Chữ lạ', need: 'Chỉ một vế', who_has: '', supply: '', percent: null });
    expect(parseMatch(row({ name: { a: 1 } as never, subtitle: null as never, metric_text: undefined as never }))).toEqual({ who_needs: '', need: '', who_has: '', supply: '', percent: null });
  });

  it('Cầu nối: "Nối 5 cặp nhóm" → "5 cặp nhóm"; chữ khác giữ nguyên', () => {
    expect(bridgeCount('Nối 5 cặp nhóm')).toBe('5 cặp nhóm');
    expect(bridgeCount('Người nối các nhóm')).toBe('Người nối các nhóm');
    expect(bridgeCount(undefined)).toBe('');
  });

  it('câu điền sẵn khi nhờ Gen soạn tin', () => {
    expect(askGenPrompt('Bùi Đức Thắng')).toBe('Soạn tin hỏi thăm Bùi Đức Thắng');
  });
});

describe('Quan hệ — hồ sơ sống', () => {
  it('maskPhone che giữa, luôn che; số +84 đọc theo dạng trong nước; quá ngắn che hết', () => {
    expect(maskPhone('+84903123418')).toBe('09•• ••• •18');
    expect(maskPhone('+84903xxx118')).toBe('09•• ••• •18');
    expect(maskPhone('0903 123 418')).toBe('09•• ••• •18');
    expect(maskPhone('+1 415 555 0132')).toBe('14•• ••• •32');
    expect(maskPhone('123')).toBe('••••••');
    expect(maskPhone(null)).toBe('••••••');
    for (const raw of ['+84903123418', '0903123418']) expect(maskPhone(raw)).not.toMatch(/\d{4}/);
  });

  it('quan hệ với Sếp, thời gian quen: chữ đời thường', () => {
    expect(relationLabel('direct')).toBe('Quen trực tiếp với Sếp');
    expect(relationLabel('via_staff')).toBe('Quen qua nhân viên');
    expect(relationLabel('stranger')).toBe('Chưa quen trực tiếp');
    expect(relationLabel('staff')).toBe('Nhân sự của Sếp');
    expect(relationLabel('Qua Trần Minh Khoa')).toBe('Qua Trần Minh Khoa'); // máy chủ cũ trả sẵn câu
    expect(relationLabel({ a: 1 })).toBe('');
    expect(tenureText(ago(10 * D), NOW)).toBe('mới quen');
    expect(tenureText(ago(200 * D), NOW)).toBe('quen 6 tháng');
    expect(tenureText(ago(3 * 365 * D), NOW)).toBe('quen 3 năm');
    expect(tenureText('hỏng', NOW)).toBe('');
    expect(tenureText(new Date(NOW + D).toISOString(), NOW)).toBe('');
  });

  const profile = (over: Partial<Profile> = {}): Profile =>
    ({
      person: { id: 'p-bao', code: 'PER-1', name: 'Nguyễn Văn Bảo', type: 'customer', org_name: null, title: null, relation_to_owner: 'direct', owner: null },
      autonomy_level: null, bot: null, owner_note: null,
      identities: [
        { id: 'i-1', channel: { type: 'zalo', name: 'Zalo' }, external_id: 'z', handle: null, phone_e164: null, first_seen_at: ago(400 * D) },
        { id: 'i-2', channel: { type: 'whatsapp', name: 'WhatsApp' }, external_id: 'w', handle: null, phone_e164: '+84903123418', first_seen_at: ago(90 * D) },
      ],
      scores: [
        { dimension: 'care', label: 'Điểm chăm sóc', value: 48, trend: 'down', updated_at: ago(H) },
        { dimension: 'potential', label: 'Tiềm năng', value: 74, trend: 'flat', updated_at: ago(H) },
        { dimension: 'churn_risk', label: 'Rủi ro churn', value: 81, trend: 'up', updated_at: ago(H) },
        { dimension: 'heat', label: 'Độ nóng', value: 87, trend: 'up', updated_at: ago(H) },
      ],
      summary: Array.from({ length: 7 }, (_, i) => ({ text: i === 1 ? '   ' : `Dòng ${i}`, tone: 'neutral', evidence: { type: 'meaning_unit', id: 'm' } })),
      timeline: Array.from({ length: 7 }, (_, i) => ({
        id: `t-${i}`, event_type: 'X', conclusion: `Việc ${i}`, confidence: 0.9, observed_at: ago((i + 1) * D), group: null, evidence: { type: 'meaning_unit', id: 'm' },
      })),
      documents: [], touchpoints: [], merge_history: [], ...over,
    }) as Profile;

  it('scoreLines: đúng 3 điểm, thứ tự độ nóng → rủi ro rời bỏ → tiềm năng, nhãn đời thường (không "churn"), kèm lời xu hướng', () => {
    const lines = scoreLines(profile().scores);
    expect(lines.map((l) => l.dimension)).toEqual(['heat', 'churn_risk', 'potential']);
    expect(lines.map(scoreSentence)).toEqual(['Độ nóng 87 · đang tăng', 'Rủi ro rời bỏ 81 · đang tăng', 'Tiềm năng 74 · giữ nguyên']);
    expect(lines.map((l) => l.tone)).toEqual(['ok', 'warn', 'neutral']); // rủi ro rời bỏ tăng = xấu
    expect(JSON.stringify(lines)).not.toContain('churn ');
    expect(scoreLines([{ dimension: 'heat', label: 'x', value: 40.4, trend: 'down', updated_at: '' }])[0]).toMatchObject({ value: 40, tone: 'warn', word: 'đang giảm' });
    expect(scoreLines([{ dimension: 'heat', label: 'x', value: 40, trend: null, updated_at: '' }])[0]).toMatchObject({ word: '', tone: 'neutral' });
  });

  it('scoreLines: hồ sơ thiếu điểm thì lấy độ nóng / rủi ro của dòng danh sách; không có gì ⇒ rỗng', () => {
    expect(scoreLines([], { heat: 72, churn_risk: 82 }).map(scoreSentence)).toEqual(['Độ nóng 72', 'Rủi ro rời bỏ 82']);
    expect(scoreLines(undefined, { heat: 72 }).map(scoreSentence)).toEqual(['Độ nóng 72']);
    expect(scoreLines(undefined)).toEqual([]);
    expect(scoreLines([{ dimension: 'heat', label: 'x', value: Number.NaN as never, trend: null, updated_at: '' }])).toEqual([]);
  });

  it('tóm tắt ≤ 5 dòng, 5 sự việc gần nhất mới trước, kênh có số đã che', () => {
    expect(summaryLines(profile()).map((l) => l.text)).toEqual(['Dòng 0', 'Dòng 2', 'Dòng 3', 'Dòng 4', 'Dòng 5']);
    expect(summaryLines(undefined)).toEqual([]);
    expect(recentEvents(profile()).map((e) => e.text)).toEqual(['Việc 0', 'Việc 1', 'Việc 2', 'Việc 3', 'Việc 4']);
    expect(channelLines(profile())).toEqual([{ id: 'i-1', text: 'Zalo' }, { id: 'i-2', text: 'WhatsApp · 09•• ••• •18' }]);
    expect(channelLines(undefined)).toEqual([]);
  });

  it('dòng phụ dưới tên: loại + thời gian quen (quan hệ với Sếp có dòng riêng); chưa tải ⇒ dòng của danh sách', () => {
    expect(profileSubtitle(profile(), 'x', NOW)).toBe('Khách hàng · quen 1 năm');
    expect(profileSubtitle(undefined, 'Chưa phân loại', NOW)).toBe('Chưa phân loại');
  });
});
