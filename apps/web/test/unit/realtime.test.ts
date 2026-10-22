import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { QueryClient, type InfiniteData } from '@tanstack/react-query';
import type { Channel, CliLoginEvent, CursorPage, Pipeline, RawItem, RealtimeEvent, RefineryRun } from '@gen-harness/contracts';
import { RealtimeClient, WS_CLOSE_FORBIDDEN, applyEvent, backoffDelay, onSocketStatus, parseFrame, rawMatches, wsUrl, type SocketLike } from '../../src/lib/realtime';
import { qk2 } from '../../src/lib/dataQueries';
import { qk } from '../../src/lib/queries';

class FakeSocket implements SocketLike {
  readyState = 0;
  sent: string[] = [];
  closed: number | null = null;
  onopen: ((ev: unknown) => void) | null = null;
  onmessage: ((ev: { data: unknown }) => void) | null = null;
  onclose: ((ev: { code: number; reason?: string }) => void) | null = null;
  onerror: ((ev: unknown) => void) | null = null;
  constructor(readonly url: string) {}
  send(data: string) {
    this.sent.push(data);
  }
  close(code = 1000) {
    this.closed = code;
  }
  open() {
    this.readyState = 1;
    this.onopen?.({});
  }
  message(frame: unknown) {
    this.onmessage?.({ data: JSON.stringify(frame) });
  }
  serverClose(code: number, reason?: string) {
    this.readyState = 3;
    this.onclose?.({ code, reason });
  }
}

describe('RealtimeClient', () => {
  let sockets: FakeSocket[];
  const factory = (url: string) => {
    const s = new FakeSocket(url);
    sockets.push(s);
    return s;
  };
  beforeEach(() => {
    sockets = [];
    vi.useFakeTimers();
  });
  afterEach(() => vi.useRealTimers());

  it('builds the same-origin /api/v1/ws URL', () => {
    expect(wsUrl({ protocol: 'https:', host: 'console.genesis.vn' })).toBe('wss://console.genesis.vn/api/v1/ws');
    expect(wsUrl({ protocol: 'http:', host: 'localhost:5173' })).toBe('ws://localhost:5173/api/v1/ws');
  });

  it('backoff doubles from 1 s up to 30 s with ±20 % jitter', () => {
    const mid = () => 0.5;
    expect(backoffDelay(0, mid)).toBe(1000);
    expect(backoffDelay(1, mid)).toBe(2000);
    expect(backoffDelay(3, mid)).toBe(8000);
    expect(backoffDelay(10, mid)).toBe(30000);
    expect(backoffDelay(0, () => 0)).toBe(800);
    expect(backoffDelay(0, () => 1)).toBe(1200);
  });

  it('dispatches frames, pings, ignores pong, and reconnects with backoff', () => {
    const events: RealtimeEvent[] = [];
    const c = new RealtimeClient({ url: () => 'ws://x/api/v1/ws', onEvent: (e) => events.push(e), factory, pingMs: 1000, random: () => 0.5 });
    c.start();
    expect(sockets).toHaveLength(1);
    sockets[0].open();
    expect(c.status).toBe('open');
    sockets[0].message({ type: 'header', data: { channels_live: 4 }, at: 'now' });
    sockets[0].message({ type: 'pong', data: null });
    expect(events.map((e) => e.type)).toEqual(['header']);
    vi.advanceTimersByTime(1000);
    expect(sockets[0].sent).toContain(JSON.stringify({ type: 'ping' }));

    sockets[0].serverClose(1006);
    expect(c.status).toBe('reconnecting');
    vi.advanceTimersByTime(999);
    expect(sockets).toHaveLength(1);
    vi.advanceTimersByTime(1);
    expect(sockets).toHaveLength(2);
    // Second failure before open → 2 s.
    sockets[1].serverClose(1006);
    vi.advanceTimersByTime(1999);
    expect(sockets).toHaveLength(2);
    vi.advanceTimersByTime(1);
    expect(sockets).toHaveLength(3);
    c.stop();
    expect(c.status).toBe('stopped');
  });

  it('close 4401 → onUnauthenticated, 4428 → onSetupRequired; neither reconnects', () => {
    const onUnauthenticated = vi.fn();
    const onSetupRequired = vi.fn();
    const a = new RealtimeClient({ url: () => 'u', onEvent: () => {}, factory, onUnauthenticated, onSetupRequired });
    a.start();
    sockets[0].open();
    sockets[0].serverClose(4401);
    expect(onUnauthenticated).toHaveBeenCalledOnce();
    vi.advanceTimersByTime(60_000);
    expect(sockets).toHaveLength(1);

    const b = new RealtimeClient({ url: () => 'u', onEvent: () => {}, factory, onUnauthenticated, onSetupRequired });
    b.start();
    sockets[1].serverClose(4428);
    expect(onSetupRequired).toHaveBeenCalledOnce();
    vi.advanceTimersByTime(60_000);
    expect(sockets).toHaveLength(2);
  });

  it('close 4403 (Origin sai / cần đổi mật khẩu) → dừng hẳn, không tạo socket mới', () => {
    const onForbidden = vi.fn();
    const onUnauthenticated = vi.fn();
    const c = new RealtimeClient({ url: () => 'u', onEvent: () => {}, factory, onForbidden, onUnauthenticated, random: () => 0.5 });
    c.start();
    sockets[0].open();
    sockets[0].serverClose(WS_CLOSE_FORBIDDEN, 'origin');
    expect(c.status).toBe('stopped');
    expect(onForbidden).toHaveBeenCalledWith('origin');
    expect(onUnauthenticated).not.toHaveBeenCalled();
    vi.advanceTimersByTime(10 * 60_000);
    expect(sockets).toHaveLength(1);
  });

  it('close 4401 giữa chừng (phiên bị thu hồi) → gọi chuyển trang đăng nhập, không kết nối lại', () => {
    const onUnauthenticated = vi.fn();
    const c = new RealtimeClient({ url: () => 'u', onEvent: () => {}, factory, onUnauthenticated, random: () => 0.5 });
    c.start();
    sockets[0].open();
    sockets[0].message({ type: 'header', data: {} });
    sockets[0].serverClose(4401, 'session_revoked');
    expect(onUnauthenticated).toHaveBeenCalledOnce();
    expect(c.status).toBe('stopped');
    vi.advanceTimersByTime(10 * 60_000);
    expect(sockets).toHaveLength(1);
  });

  it('close 1011 (lỗi máy chủ) vẫn kết nối lại', () => {
    const c = new RealtimeClient({ url: () => 'u', onEvent: () => {}, factory, random: () => 0.5 });
    c.start();
    sockets[0].open();
    sockets[0].serverClose(1011);
    vi.advanceTimersByTime(1000);
    expect(sockets).toHaveLength(2);
    c.stop();
  });

  it('drops a stale connection that stopped answering pings', () => {
    const c = new RealtimeClient({ url: () => 'u', onEvent: () => {}, factory, pingMs: 1000, staleMs: 2500, random: () => 0.5 });
    c.start();
    sockets[0].open();
    vi.advanceTimersByTime(3000);
    expect(sockets[0].closed).toBe(4000);
    vi.advanceTimersByTime(1000);
    expect(sockets).toHaveLength(2);
    c.stop();
  });

  it('parseFrame rejects junk', () => {
    expect(parseFrame('nope')).toBeNull();
    expect(parseFrame(42)).toBeNull();
    expect(parseFrame(JSON.stringify({ data: {} }))).toBeNull();
    expect(parseFrame(JSON.stringify({ type: 'raw.new', data: { id: 1 } }))?.type).toBe('raw.new');
  });
});

// ── dispatch into the query cache ─────────────────────────────────────────
const row = (over: Partial<RawItem> = {}): RawItem => ({
  id: 'r1', code: 'RAW-1', received_at: '2026-09-21T08:11:44Z', occurred_at: '2026-09-21T08:11:42Z',
  channel: { type: 'zalo', name: 'Zalo' }, group: { id: 'g1', code: 'GRP-ZL-0114', name: 'Vận hành' },
  person: { id: 'p1', code: 'PER-0042', name: 'Bảo' }, direction: 'inbound', kind: 'text', text: 'Chào',
  label: null, confidence: null, state: 'pending', ...over,
});
type Pages = InfiniteData<CursorPage<RawItem>, string | null>;
const pages = (items: RawItem[]): Pages => ({ pages: [{ items, next_cursor: null, total: items.length }], pageParams: [null] });
const ev = <T extends RealtimeEvent['type']>(type: T, data: Extract<RealtimeEvent, { type: T }>['data']) =>
  ({ type, data, at: '2026-09-21T08:11:44Z' }) as RealtimeEvent;

describe('applyEvent', () => {
  let qc: QueryClient;
  beforeEach(() => {
    qc = new QueryClient();
  });

  it('raw.new prepends to matching lists only, once, and bumps the pipeline', () => {
    qc.setQueryData(qk2.raw({}), pages([row({ id: 'old' })]));
    qc.setQueryData(qk2.raw({ channel: 'whatsapp' }), pages([]));
    qc.setQueryData<Pipeline>(qk2.pipeline, {
      channels_live: 4, groups_listening: 42, raw_total: 18412, raw_pending: 4204, interval_seconds: 900, count_threshold: 500, clean_total: 14208, raw_error: 0,
    });
    applyEvent(qc, ev('raw.new', row({ id: 'new' })));
    applyEvent(qc, ev('raw.new', row({ id: 'new' })));
    const all = qc.getQueryData<Pages>(qk2.raw({}))!;
    expect(all.pages[0].items.map((r) => r.id)).toEqual(['new', 'old']);
    expect(all.pages[0].total).toBe(2);
    expect(qc.getQueryData<Pages>(qk2.raw({ channel: 'whatsapp' }))!.pages[0].items).toHaveLength(0);
    expect(qc.getQueryData<Pipeline>(qk2.pipeline)!.raw_total).toBe(18414);
  });

  it('raw.state patches the row and decrements pending once', () => {
    qc.setQueryData(qk2.raw({}), pages([row()]));
    qc.setQueryData<Pipeline>(qk2.pipeline, {
      channels_live: 4, groups_listening: 42, raw_total: 10, raw_pending: 5, interval_seconds: 900, count_threshold: 500, clean_total: 1, raw_error: 0,
    });
    applyEvent(qc, ev('raw.state', { id: 'r1', state: 'clean', label: 'AskedPrice', confidence: 0.94 }));
    const r = qc.getQueryData<Pages>(qk2.raw({}))!.pages[0].items[0];
    expect(r).toMatchObject({ state: 'clean', label: 'AskedPrice', confidence: 0.94 });
    expect(qc.getQueryData<Pipeline>(qk2.pipeline)!.raw_pending).toBe(4);
  });

  it('rawMatches honours filters', () => {
    expect(rawMatches(row(), { channel: 'zalo', group_id: 'g1' })).toBe(true);
    expect(rawMatches(row(), { state: 'clean' })).toBe(false);
    expect(rawMatches(row({ confidence: 0.5 }), { min_confidence: 0.6 })).toBe(false);
  });

  it('channel.qr shows the QR; channel.status marks scanned and clears it when active', () => {
    const zalo = { type: 'zalo', name: 'Zalo', state: 'logged_out', qr: null } as unknown as Channel;
    qc.setQueryData(qk2.channels, [zalo]);
    applyEvent(qc, ev('channel.qr', { type: 'zalo', session_id: 's', image: 'data:image/png;base64,AA', expires_at: '2026-09-21T08:12:44Z' }));
    let c = qc.getQueryData<Channel[]>(qk2.channels)![0];
    expect(c.state).toBe('pending_qr');
    expect(c.qr).toMatchObject({ session_id: 's', scanned: false });
    applyEvent(qc, ev('channel.status', { type: 'zalo', state: 'pending_qr', account_label: null, scanned: true }));
    c = qc.getQueryData<Channel[]>(qk2.channels)![0];
    expect(c.qr?.scanned).toBe(true);
    applyEvent(qc, ev('channel.status', { type: 'zalo', state: 'active', account_label: 'iPhone của Sếp', scanned: true }));
    c = qc.getQueryData<Channel[]>(qk2.channels)![0];
    expect(c).toMatchObject({ state: 'active', qr: null, account_label: 'iPhone của Sếp' });
  });

  it('channel.status error làm mới truy vấn kênh (resumable do máy chủ tính, không đoán ở trình duyệt)', () => {
    const zalo = { type: 'zalo', name: 'Zalo', state: 'active', resumable: false, qr: null } as unknown as Channel;
    qc.setQueryData(qk2.channels, [zalo]);
    const spy = vi.spyOn(qc, 'invalidateQueries');
    applyEvent(qc, ev('channel.status', { type: 'zalo', state: 'error', account_label: 'Zalo Sếp', scanned: false, error: 'NETWORK_UNAVAILABLE' }));
    const keys = spy.mock.calls.map((c) => JSON.stringify((c[0] as { queryKey: unknown }).queryKey));
    expect(keys).toContain(JSON.stringify(qk2.channels));
    expect(qc.getQueryData<Channel[]>(qk2.channels)![0]).toMatchObject({ state: 'error', error: 'NETWORK_UNAVAILABLE' });
    // Trạng thái chờ quét không cần làm mới (đã có sự kiện channel.qr riêng).
    spy.mockClear();
    applyEvent(qc, ev('channel.status', { type: 'zalo', state: 'pending_qr', account_label: null, scanned: true }));
    expect(spy).not.toHaveBeenCalled();
  });

  it('cli.login is stored per login id', () => {
    const e: CliLoginEvent = { login_id: 'L1', status: 'waiting_code', url: 'https://accounts.google.com/x' };
    applyEvent(qc, ev('cli.login', e));
    expect(qc.getQueryData(qk2.cliLogin('L1'))).toEqual(e);
  });

  it('refinery.run upserts the run list; progress is kept as latest', () => {
    const run = (id: string, status: RefineryRun['status'], at: string): RefineryRun => ({
      id, trigger: 'manual', started_at: at, finished_at: null, input_count: 250, clean_count: 0, lowconf_count: 0, noise_count: 0, error_count: 0, status,
    });
    qc.setQueryData(qk2.runs, [run('a', 'done', '2026-09-21T08:00:00Z')]);
    applyEvent(qc, ev('refinery.run', run('b', 'queued', '2026-09-21T08:10:00Z')));
    applyEvent(qc, ev('refinery.run', run('b', 'running', '2026-09-21T08:10:00Z')));
    const runs = qc.getQueryData<RefineryRun[]>(qk2.runs)!;
    expect(runs.map((r) => `${r.id}:${r.status}`)).toEqual(['b:running', 'a:done']);
    const p = { run_id: 'b', processed: 125, total: 250, clean: 80, lowconf: 5, noise: 40, errors: 0, status: 'running' as const };
    applyEvent(qc, ev('refinery.progress', p));
    expect(qc.getQueryData(qk2.latestProgress)).toEqual(p);
  });
});

describe('onSocketStatus', () => {
  it('ổ cắm mở (lần đầu / nối lại) ⇒ chuông thông báo bị đánh dấu cũ để tải lại; trạng thái khác thì không', () => {
    const qc = new QueryClient();
    qc.setQueryData(qk.notifications, { items: [], unread: 0 });
    for (const st of ['connecting', 'reconnecting', 'stopped', 'idle'] as const) {
      onSocketStatus(qc, st);
      expect(qc.getQueryState(qk.notifications)?.isInvalidated).toBe(false);
    }
    onSocketStatus(qc, 'open');
    expect(qc.getQueryState(qk.notifications)?.isInvalidated).toBe(true);
  });
});
