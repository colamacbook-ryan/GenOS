import { describe, expect, it } from 'vitest';
import type { Channel, Notebook, NotebookSection, RefineryRun, Rule } from '@gen-harness/contracts';
import { fmtClock, fmtDec, fmtInt, fmtInterval, fmtLatency, fmtPct, fmtSessionAge, minutesValue } from '../../src/lib/format';
import {
  RAW_STATE_LABEL,
  cleanTone,
  confidenceTone,
  fmtConfidence,
  idStatCards,
  outputLabel,
  pipelineCards,
  rawStateLabel,
  runLine,
  sectionAge,
  weightsMessage,
  weightsSum,
  weightsValid,
} from '../../src/screens/data/dataModel';
import { draftToBody, ruleToDraft, validateRuleDraft, draftValid } from '../../src/screens/data/ruleForm';
import {
  CHANNEL_STATE,
  channelAction,
  channelMeta,
  channelState,
  channelStats,
  cliChip,
  qrRemaining,
} from '../../src/screens/system/systemModel';
import { anyListening, applyGroupDraft, firstRunCounters, nearest, providerReady } from '../../src/setup/phase2Model';

describe('number & time formatting (vi-VN, org timezone)', () => {
  it('integers use "." thousands, decimals use ","', () => {
    expect(fmtInt(18412)).toBe('18.412');
    expect(fmtInt(1244)).toBe('1.244');
    expect(fmtInt(0)).toBe('0');
    expect(fmtInt(null)).toBe('—');
    expect(fmtDec(0.94)).toBe('0,94');
    expect(fmtConfidence(0.9)).toBe('0,90');
    expect(fmtConfidence(null)).toBe('—');
    expect(fmtPct(99.9)).toBe('99,9%');
    expect(fmtLatency(420)).toBe('0,42s');
  });

  it('intervals and the pipeline minutes value', () => {
    expect(fmtInterval(900)).toBe('15 phút');
    expect(fmtInterval(3600)).toBe('1 giờ');
    expect(fmtInterval(45)).toBe('45 giây');
    expect(minutesValue(900)).toBe('15');
    expect(minutesValue(90)).toBe('1,5');
  });

  it('times are shown in the org timezone', () => {
    expect(fmtClock('2026-09-21T08:11:44Z', 'Asia/Ho_Chi_Minh')).toBe('15:11:44');
    expect(fmtClock('2026-09-21T08:11:44Z', 'UTC')).toBe('08:11:44');
    const now = Date.parse('2026-09-21T08:00:00Z');
    expect(fmtSessionAge('2026-09-07T01:19:00Z', now)).toBe('14 ngày 06:41');
  });
});

describe('data layer model', () => {
  it('raw state labels', () => {
    expect(RAW_STATE_LABEL.pending).toBe('Chờ chu kỳ tới');
    expect(rawStateLabel('processing')).toBe('Đang phân loại');
    expect(rawStateLabel('clean')).toBe('Đã vào kho sạch');
    expect(rawStateLabel('lowconf')).toBe('Tin cậy thấp');
    expect(rawStateLabel('discarded')).toBe('Loại — nhiễu');
    expect(rawStateLabel('something_new')).toBe('something_new');
  });

  it('confidence tone thresholds', () => {
    expect(confidenceTone(0.94)).toBe('var(--color-ok)');
    expect(confidenceTone(0.61)).toBe('var(--color-warn)');
    expect(confidenceTone(0.2)).toBe('var(--color-bad)');
  });

  it('weights must be integers summing to exactly 100', () => {
    const ws = [30, 25, 20, 12, 8, 5].map((value) => ({ value }));
    expect(weightsSum(ws)).toBe(100);
    expect(weightsValid(ws)).toBe(true);
    expect(weightsMessage(ws)).toBeNull();
    const over = [...ws.slice(0, 5), { value: 9 }];
    expect(weightsValid(over)).toBe(false);
    expect(weightsMessage(over)).toBe('Tổng trọng số phải bằng 100% — hiện 104% (thừa 4%).');
    expect(weightsMessage([{ value: 50 }, { value: 40 }])).toContain('thiếu 10');
    expect(weightsValid([{ value: 50.5 }, { value: 49.5 }])).toBe(false);
    expect(weightsValid([])).toBe(false);
  });

  it('run lines: queued looks like running, fast has its own label', () => {
    const base: RefineryRun = {
      id: 'r', trigger: 'schedule', started_at: '2026-09-21T08:00:00Z', finished_at: null, input_count: 486,
      clean_count: 0, lowconf_count: 0, noise_count: 0, error_count: 0, status: 'queued',
    };
    const queued = runLine(base);
    const running = runLine({ ...base, status: 'running' });
    expect(queued.tone).toBe(running.tone);
    expect(queued.meta).toBe('486 bản ghi · đang chờ');
    expect(runLine({ ...base, status: 'done', trigger: 'fast' }).meta).toBe('486 bản ghi · đường nhanh');
    expect(runLine({ ...base, status: 'done', lowconf_count: 2 }).meta).toBe('486 bản ghi · 2 tin cậy thấp');
    expect(runLine({ ...base, status: 'done' }).meta).toBe('486 bản ghi · 0 lỗi');
  });

  it('pipeline strip formats raw numbers', () => {
    const cards = pipelineCards(
      { channels_live: 4, groups_listening: 42, raw_total: 18412, raw_pending: 4204, interval_seconds: 900, count_threshold: 500, clean_total: 14208, raw_error: 0 },
      'raw',
    );
    const text = JSON.stringify(cards);
    expect(text).toContain('18.412');
    expect(text).toContain('14.208');
  });

  it('clean tones follow the design', () => {
    expect(cleanTone('WentSilent', 10)).toBe('var(--color-bad)');
    expect(cleanTone('Complained', 80)).toBe('var(--color-bad)');
    expect(cleanTone('Complained', 60)).toBe('var(--color-warn)');
    expect(cleanTone('AskedPrice', 78)).toBe('var(--color-ok)');
    expect(cleanTone('SentDocument', 40)).toBe('var(--color-neutral-400)');
  });

  it('memory section notes', () => {
    const nb = { compaction_no: 14, last_compacted_at: '2026-09-21T08:00:00Z' } as Notebook;
    const tz = 'Asia/Ho_Chi_Minh';
    const now = Date.parse('2026-09-21T08:11:44Z');
    const sec = (key: string, extra: Partial<NotebookSection> = {}) => ({ key, title: key, updated_at: null, entries: [], ...extra }) as NotebookSection;
    expect(sectionAge(sec('rolling_context'), nb, tz, now)).toBe('nén lần 14 · 15:00');
    const pinned = { id: 'e', body: 'x', refs: [], author: { type: 'user' as const, label: 'Sếp' }, pinned: true, created_at: '2026-09-12T03:00:00Z' };
    expect(sectionAge(sec('guardrails', { entries: [pinned] }), nb, tz, now)).toBe('Sếp ghim · 12/09');
    expect(sectionAge(sec('attention_now', { updated_at: '2026-09-21T08:09:44Z' }), nb, tz, now)).toBe('cập nhật 2 phút trước');
  });

  it('identity stat cards', () => {
    const cards = idStatCards({ merged_people: 136, live_profiles: 148, pending_pairs: 12, manual_splits: 4, unlinked_accounts: 31 });
    expect(cards.map((c) => c.value)).toEqual([136, 12, 4, 31]);
    expect(cards[0].sub).toBe('trên 148 hồ sơ đang sống');
  });
});

describe('rule editor round-trip', () => {
  const r06: Pick<Rule, 'name' | 'kind' | 'threshold' | 'prompt_hint' | 'conditions' | 'outputs'> = {
    name: 'Loại nhiễu',
    kind: 'hygiene',
    threshold: 0.4,
    prompt_hint: null,
    conditions: [
      { type: 'max_words', n: 3, no_entity: true, label: 'dưới 4 từ và không có thực thể' },
      { type: 'kind_in', values: ['sticker', 'image'], label: 'sticker' },
    ],
    outputs: [
      { set: 'label', value: 'Noise', label: 'label = Noise' },
      { discard: true, label: 'không ghi vào kho sạch' },
      { alert: 'P1', label: 'đẩy cảnh báo P1' },
    ],
  };

  it('keeps unknown condition fields and the preset output encoding', () => {
    const body = draftToBody(ruleToDraft(r06));
    expect(body.conditions[0]).toMatchObject({ type: 'max_words', n: 3, no_entity: true });
    expect(body.conditions[1]).toMatchObject({ type: 'kind_in', values: ['sticker', 'image'] });
    expect(body.outputs).toEqual([
      { set: 'label', value: 'Noise', label: 'label = Noise' },
      { discard: true, label: 'không ghi vào kho sạch' },
      { alert: 'P1', label: 'đẩy cảnh báo P1' },
    ]);
    expect(body.threshold).toBe(0.4);
  });

  it('labels outputs as the design writes them', () => {
    expect(outputLabel({ alert: 'P1' })).toBe('đẩy cảnh báo P1');
    expect(outputLabel({ set: 'intent', value: 'AskedPrice', label: 'intent = AskedPrice' })).toBe('intent = AskedPrice');
  });

  it('validation rejects an empty name', () => {
    const d = ruleToDraft({ ...r06, name: '' });
    expect(draftValid(validateRuleDraft(d))).toBe(false);
    expect(draftValid(validateRuleDraft(ruleToDraft(r06)))).toBe(true);
  });
});

describe('channels model', () => {
  const channel = (over: Partial<Channel>): Channel => ({
    type: 'zalo', name: 'Zalo', installed: true, id: 'c', state: 'active', account_label: 'iPhone của Sếp',
    started_at: '2026-09-07T01:19:00Z', groups_listening: 38, outbound_queued: 0, last_heartbeat_at: null,
    stats: { msgs_24h: 1244, tagged_24h: 31, latency_ms: 420, uptime_pct: 99.9 }, qr: null, ...over,
  });

  it('state labels', () => {
    expect(CHANNEL_STATE.active.label).toBe('Đang kết nối');
    expect(channelState('expired').label).toBe('Phiên hết hạn');
    expect(channelState('pending_qr').label).toBe('Chờ quét mã');
    expect(channelState('not_installed').label).toBe('Chưa cài');
  });

  it('card actions per state', () => {
    expect(channelAction(channel({})).label).toBe('Đăng xuất');
    expect(channelAction(channel({ type: 'whatsapp', state: 'expired' })).label).toBe('Quét lại QR');
    expect(channelAction(channel({ state: 'logged_out' })).label).toBe('Tạo mã QR');
    expect(channelAction(channel({ type: 'telegram', state: 'not_installed' }))).toMatchObject({ action: 'unavailable', label: 'Chưa có trong bản này' });
    expect(channelMeta(channel({ type: 'telegram', state: 'not_installed' })).before).toBe('Kênh này chưa có trong bản đang chạy');
    expect(channelAction(channel({ type: 'linkedin', state: 'identity_only' })).label).toBe('Cấu hình');
  });

  it('meta line and stats format the raw numbers', () => {
    const now = Date.parse('2026-09-21T08:00:00Z');
    const m = channelMeta(channel({}), now, 'Asia/Ho_Chi_Minh');
    expect(`${m.before}${m.groups}${m.after}`).toContain('iPhone của Sếp');
    expect(m.groups).toBe('38 nhóm lắng nghe');
    expect(channelStats(channel({})).map((s) => s.value)).toEqual(['1.244', '31', '0,42s', '99,9%']);
    const li = channelStats(channel({ type: 'linkedin', state: 'identity_only', stats: { msgs_24h: null, tagged_24h: null, latency_ms: null, uptime_pct: null, identities: 34, merged: 12 } }));
    expect(li.map((s) => s.value)).toEqual(['—', '34', '12', '—']);
  });

  it('QR countdown follows expires_at (Zalo 100 s, WhatsApp 60 s)', () => {
    const now = Date.parse('2026-09-21T08:00:00Z');
    const q = qrRemaining('2026-09-21T08:00:41Z', now);
    expect(q.label).toBe('41 giây');
    expect(Math.round(q.pct)).toBe(41);
    expect(Math.round(qrRemaining('2026-09-21T08:00:41Z', now, 60_000).pct).toString()).toBe('68');
    expect(qrRemaining('2026-09-21T07:59:00Z', now).ms).toBe(0);
  });

  it('CLI chip', () => {
    expect(cliChip(undefined).label).toBe('Chưa đăng nhập');
    expect(cliChip({ id: 'p', email: 'a@b', plan_label: '', active: true, expires_at: null, state: 'ok' }).label).toBe('Đang hoạt động');
    expect(cliChip({ id: 'p', email: 'a@b', plan_label: '', active: true, expires_at: null, state: 'expired' }).label).toBe('Hết hạn');
  });
});

describe('setup 4–7, 12 model', () => {
  it('provider readiness', () => {
    const p = { id: 'a', kind: 'gemini', enabled: true, auth_state: 'unconfigured' } as Parameters<typeof providerReady>[0];
    expect(providerReady(p, {}, false)).toBe(false);
    expect(providerReady(p, { a: { ok: true, latency_ms: 1, models: [], error: null } }, false)).toBe(true);
    expect(providerReady({ ...p, kind: 'antigravity_cli' }, {}, true)).toBe(true);
    // v0.1.68 (F003): nguồn đã tắt không sẵn sàng dù vừa gọi thử xanh hoặc CLI còn phiên.
    expect(providerReady({ ...p, enabled: false }, { a: { ok: true, latency_ms: 1, models: [], error: null } }, false)).toBe(false);
    expect(providerReady({ ...p, kind: 'antigravity_cli', enabled: false }, {}, true)).toBe(false);
  });

  it('group draft overlays and "at least one listening"', () => {
    const gs = [
      { id: '1', code: 'G1', name: 'a', members: 1, kind: 'internal', listen_mode: 'off' as const, view_scope: 'owner' as const },
      { id: '2', code: 'G2', name: 'b', members: 1, kind: 'internal', listen_mode: 'off' as const, view_scope: 'owner' as const },
    ];
    expect(anyListening(gs)).toBe(false);
    const next = applyGroupDraft(gs, { '2': { listen_mode: 'silent', view_scope: 'manager' } });
    expect(next[1]).toMatchObject({ listen_mode: 'silent', view_scope: 'manager' });
    expect(anyListening(next)).toBe(true);
  });

  it('nearest option', () => {
    expect(nearest([5, 15, 30, 60], 14, 15)).toBe(15);
    expect(nearest([5, 15, 30, 60], undefined, 15)).toBe(15);
  });

  it('first-run counters merge the poll with live progress', () => {
    const c = firstRunCounters(
      { raw_collected: 1244, classifying: 250, clean: 812, lowconf: 31, discarded: 120, run: null },
      { run_id: 'r', processed: 125, total: 250, clean: 80, lowconf: 5, noise: 40, errors: 0, status: 'running' },
    );
    expect(c.raw).toBe(1244);
    expect(c.pct).toBe(50);
    expect(c.status).toBe('running');
  });
});
