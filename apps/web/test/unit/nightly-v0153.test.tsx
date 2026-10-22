import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { QueryClientProvider } from '@tanstack/react-query';
import type { SystemHealth, SystemUpdate } from '@gen-harness/contracts';
import { UpdateCard } from '../../src/update/UpdateCard';
import { UPDATE_COMMAND, autoInstallHint, updateView } from '../../src/update/updateModel';
import { healthRows, healthTips } from '../../src/screens/system/healthModel';
import { GENH_COMMANDS } from '../../src/help/helpModel';
import { HEALTH_KINDS } from '../../src/screens/system/queries';
import { queryClient } from '../../src/lib/queryClient';

/**
 * v0.1.53 (F-99, F-96): thẻ cập nhật nói thẳng NGUYÊN NHÂN "máy chủ chưa nhận yêu cầu" (linger tắt / trình nhận yêu cầu
 * lỗi / không xoá được tệp yêu cầu GH-E94C), thẻ Sức khoẻ có dòng "Tự cập nhật đêm" + hướng dẫn bật lại, gợi ý "Tự cài…"
 * theo cách lịch đêm chọn bản đủ 24 giờ, và Trợ giúp có lệnh `genh auto-update status`.
 */

const NOW = Date.parse('2026-10-09T10:00:00Z');
const base: SystemUpdate = {
  current: 'v0.1.52', latest: 'v0.1.53', update_available: true, updater: 'systemd', linked: true, can_request: true,
  state: 'idle', stalled_reason: null, message: null, from: null, to: null, started_at: null, finished_at: null,
  requested_at: null, release_url: null, release_notes: '- Cảnh báo lịch đêm',
};
const opts = { waitingFor: null, offline: false, now: NOW };

describe('updateView — nguyên nhân chưa nhận yêu cầu', () => {
  it('linger_off: nói thẳng tiến trình nền chỉ chạy khi có người đăng nhập + lệnh enable-linger chép được', () => {
    const v = updateView({ ...base, state: 'stalled', stalled_reason: 'linger_off' }, opts);
    if (v.kind !== 'stalled') throw new Error(v.kind);
    expect(v.title).toBe('Máy chủ chưa nhận yêu cầu cập nhật');
    expect(v.kicker).toBe('Tiến trình nền trên máy chủ chỉ chạy khi có người đăng nhập — cần bật linger');
    expect(v.kicker).toContain('chỉ chạy khi có người đăng nhập');
    expect(v.body).toBe('Chạy một lần lệnh dưới đây trên máy chủ (máy hỏi mật khẩu đăng nhập máy), rồi bấm Thử lại.');
    expect(v.command).toBe('sudo loginctl enable-linger $USER');
    expect(v.showCommand).toBe(true);
    // Lệnh không có dấu chấm dính phía sau (Sếp chép nguyên dòng).
    expect(v.command?.endsWith('.')).toBe(false);
    expect(v.tone).toBe('warn');
  });

  it('watcher_failed: trình nhận yêu cầu lỗi + lệnh cập nhật tay để bật lại', () => {
    const v = updateView({ ...base, state: 'stalled', stalled_reason: 'watcher_failed' }, opts);
    if (v.kind !== 'stalled') throw new Error(v.kind);
    expect(v.title).toBe('Máy chủ chưa nhận yêu cầu cập nhật');
    expect(v.kicker).toBe('Trình nhận yêu cầu trên máy chủ đang lỗi');
    expect(v.body).toBe('Chạy lệnh dưới đây trên máy chủ một lần để bật lại, rồi bấm Thử lại.');
    expect(v.command).toBe(UPDATE_COMMAND);
    expect(v.showCommand).toBe(true);
  });

  it('not_picked_up (và api cũ không có lý do): giữ lời cũ, dùng lệnh mặc định', () => {
    for (const reason of ['not_picked_up', null, undefined] as const) {
      const v = updateView({ ...base, state: 'stalled', stalled_reason: reason }, opts);
      if (v.kind !== 'stalled') throw new Error(v.kind);
      expect(v.kicker).toBe('Đã quá 15 phút mà chưa bắt đầu — có thể máy chủ đang tắt tiến trình nhận yêu cầu');
      expect(v.command).toBeUndefined();
      expect(v.showCommand).toBe(true);
    }
  });

  it('GH-E94C: không xoá được tệp yêu cầu — chưa đụng gì, nhắc kiểm quyền run/request, thông điệp genh ở Chi tiết kỹ thuật', () => {
    const message = 'Không xoá được tệp yêu cầu trong run/request nên chưa làm gì — kiểm quyền thư mục rồi thử lại (GH-E94C)';
    const v = updateView({ ...base, state: 'failed', message, finished_at: '2026-10-09T09:50:00Z' }, opts);
    if (v.kind !== 'failed') throw new Error(v.kind);
    expect(v.tone).toBe('warn');
    expect(v.kicker).toBe('Máy chủ không xoá được tệp yêu cầu — chưa đụng gì');
    expect(v.body).toMatch(/quyền thư mục run\/request/);
    expect(v.body).toMatch(/bấm Thử lại/);
    expect(v.body).not.toMatch(/quay về/);
    expect(v.detail).toBe(message);
    expect(v.showCommand).toBe(false);
    // Không có nút Thử lại (máy chủ chưa nhận yêu cầu từ nút bấm) ⇒ chỉ lệnh chạy tay.
    const manual = updateView({ ...base, can_request: false, state: 'failed', message, finished_at: '2026-10-09T09:50:00Z' }, opts);
    if (manual.kind !== 'failed') throw new Error(manual.kind);
    expect(manual.body).toMatch(/chạy lệnh bên dưới trên máy chủ/);
    expect(manual.showCommand).toBe(true);
  });
});

describe('<UpdateCard> — nguyên nhân chưa nhận yêu cầu', () => {
  beforeEach(() => {
    queryClient.clear();
    vi.unstubAllGlobals();
  });
  const renderWith = async (data: SystemUpdate) => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify(data), { status: 200, headers: { 'Content-Type': 'application/json' } })));
    return render(
      <QueryClientProvider client={queryClient}>
        <UpdateCard />
      </QueryClientProvider>,
    );
  };

  it('linger_off: thẻ hiện câu, lệnh enable-linger (không phải lệnh cập nhật) và nút Thử lại', async () => {
    const { container } = await renderWith({ ...base, state: 'stalled', stalled_reason: 'linger_off', requested_at: new Date(NOW).toISOString() });
    expect(await screen.findByText('Máy chủ chưa nhận yêu cầu cập nhật')).toBeInTheDocument();
    expect(screen.getByText(/chỉ chạy khi có người đăng nhập/)).toBeInTheDocument();
    expect(screen.getByText('sudo loginctl enable-linger $USER')).toBeInTheDocument();
    expect(screen.queryByText(UPDATE_COMMAND)).toBeNull();
    expect(screen.getByRole('button', { name: /Thử lại/ })).toBeInTheDocument();
    expect(container.textContent).not.toContain('[object Object]');
  });

  it('watcher_failed: thẻ hiện lệnh cập nhật để bật lại trình nhận yêu cầu', async () => {
    await renderWith({ ...base, state: 'stalled', stalled_reason: 'watcher_failed' });
    expect(await screen.findByText('Trình nhận yêu cầu trên máy chủ đang lỗi')).toBeInTheDocument();
    expect(screen.getByText(UPDATE_COMMAND)).toBeInTheDocument();
    expect(screen.queryByText('sudo loginctl enable-linger $USER')).toBeNull();
  });

  it('not_picked_up: vẫn là lệnh cập nhật như trước', async () => {
    await renderWith({ ...base, state: 'stalled', stalled_reason: 'not_picked_up' });
    expect(await screen.findByText(/Đã quá 15 phút mà chưa bắt đầu/)).toBeInTheDocument();
    expect(screen.getByText(UPDATE_COMMAND)).toBeInTheDocument();
  });

  it('GH-E94C: nguyên văn thông điệp genh nằm trong Chi tiết kỹ thuật, không render object', async () => {
    const message = 'Không xoá được tệp yêu cầu trong run/request nên chưa làm gì (GH-E94C)';
    const { container } = await renderWith({ ...base, state: 'failed', message, finished_at: new Date().toISOString(), to: 'v0.1.53' });
    expect(await screen.findByText('Máy chủ không xoá được tệp yêu cầu — chưa đụng gì')).toBeInTheDocument();
    expect(screen.getByText('Chi tiết kỹ thuật')).toBeInTheDocument();
    expect(screen.getByText(message)).toBeInTheDocument();
    expect(container.textContent).not.toContain('[object Object]');
  });
});

const healthBase = (nightly?: SystemHealth['nightly']): SystemHealth => ({
  checked_at: '2026-10-09T10:00:00Z', overall: 'ok',
  worker: { state: 'ok', alive: true, last_seen_at: null, silent_minutes: null },
  browser: { state: 'off', last_heartbeat_at: null }, queues: [], crons: [],
  backup: { configured: true, latest_at: '2026-10-09T02:00:00Z', age_hours: 8, stale: false },
  update: { state: 'idle', failed: false, blocked_version: null, finished_at: null },
  disk: { state: 'unknown', free_bytes: null, min_bytes: null, checked_at: null },
  issues: [],
  ...(nightly ? { nightly } : {}),
});
const nightly = (over: Partial<NonNullable<SystemHealth['nightly']>> = {}): NonNullable<SystemHealth['nightly']> => ({
  state: 'warn', last_run_at: '2026-10-06T20:00:00Z', next_run_at: '2026-10-10T20:00:00Z', days_since: 3, opted_out: false,
  linger: 'yes', checked_at: '2026-10-09T09:55:00Z', ...over,
});
const row = (h: SystemHealth) => healthRows(h, NOW, 'Asia/Ho_Chi_Minh').find((r) => r.key === 'nightly');

describe('healthRows — Tự cập nhật đêm', () => {
  it('không có khối nightly (api cũ / không có hộp thư) ⇒ không có dòng', () => {
    expect(row(healthBase())).toBeUndefined();
  });

  it('warn ⇒ "Chưa chạy 3 ngày" (vàng)', () => {
    const r = row(healthBase(nightly()));
    expect(r).toMatchObject({ key: 'nightly', label: 'Tự cập nhật đêm', value: 'Chưa chạy 3 ngày', tone: 'warn' });
  });

  it('warn mà chưa tròn ngày (lịch đang tắt) ⇒ "Đang tắt"', () => {
    expect(row(healthBase(nightly({ days_since: 0 })))).toMatchObject({ value: 'Đang tắt', tone: 'warn' });
    expect(row(healthBase(nightly({ days_since: null })))).toMatchObject({ value: 'Đang tắt', tone: 'warn' });
  });

  it('theo `reason` của api — cùng cách nói với chuông host.nightly', () => {
    // Lịch đang tắt mà lần chạy cuối đã cũ: chuông "đang tắt" ⇒ thẻ cũng "Đang tắt" (không "Chưa chạy N ngày").
    expect(row(healthBase(nightly({ reason: 'disabled', days_since: 5 })))).toMatchObject({ value: 'Đang tắt', tone: 'warn' });
    // 36–48 giờ: chuông "đã hơn 1 ngày chưa chạy" ⇒ thẻ "Hơn 1 ngày chưa chạy" (không "Chưa chạy 1 ngày").
    expect(row(healthBase(nightly({ reason: 'stale', days_since: 1 })))).toMatchObject({ value: 'Hơn 1 ngày chưa chạy', tone: 'warn' });
    expect(row(healthBase(nightly({ reason: 'stale', days_since: 4 })))).toMatchObject({ value: 'Chưa chạy 4 ngày', tone: 'warn' });
    // api cũ (không reason): 1 ngày cũng nói "Hơn 1 ngày chưa chạy".
    expect(row(healthBase(nightly({ days_since: 1 })))).toMatchObject({ value: 'Hơn 1 ngày chưa chạy' });
  });

  it('Sếp đã tắt mà lịch vẫn bật ⇒ nói thật (vàng), không "Tắt (Sếp đã tắt)"', () => {
    const r = row(healthBase(nightly({ reason: 'opted_out_running', opted_out: true, days_since: 0 })));
    expect(r).toMatchObject({ value: 'Sếp đã tắt nhưng lịch vẫn bật — máy vẫn tự lên bản mới', tone: 'warn' });
    expect(r?.value).not.toMatch(/^Tắt/);
  });

  it('other ⇒ lịch đêm do bản cài khác quản lý (xám, không cảnh báo)', () => {
    expect(row(healthBase(nightly({ state: 'other', reason: null, days_since: null })))).toMatchObject({
      value: 'Lịch đêm do bản cài khác trên máy này quản lý', tone: 'muted',
    });
  });

  it('ok ⇒ "Bình thường · chạy lần cuối dd/mm HH:MM" theo múi giờ tổ chức', () => {
    // 2026-10-09T20:00:00Z = 03:00 ngày 10/10 giờ Việt Nam.
    const r = row(healthBase(nightly({ state: 'ok', days_since: 0, last_run_at: '2026-10-09T20:00:00Z' })));
    expect(r).toMatchObject({ value: 'Bình thường · chạy lần cuối 10/10 03:00', tone: 'ok' });
    const utc = healthRows(healthBase(nightly({ state: 'ok', days_since: 0, last_run_at: '2026-10-09T20:00:00Z' })), NOW, 'UTC').find((x) => x.key === 'nightly');
    expect(utc?.value).toBe('Bình thường · chạy lần cuối 09/10 20:00');
    // Vừa bật, chưa tới giờ chạy lần đầu ⇒ vẫn bình thường.
    expect(row(healthBase(nightly({ state: 'ok', days_since: 0, last_run_at: null })))).toMatchObject({ value: 'Bình thường · chưa tới giờ chạy lần đầu', tone: 'ok' });
  });

  it('off ⇒ "Tắt (Sếp đã tắt)" (xám); unknown ⇒ "Chưa rõ"', () => {
    expect(row(healthBase(nightly({ state: 'off', opted_out: true, days_since: null })))).toMatchObject({ value: 'Tắt (Sếp đã tắt)', tone: 'muted' });
    expect(row(healthBase(nightly({ state: 'unknown', days_since: null, last_run_at: null, linger: 'unknown' })))).toMatchObject({ value: 'Chưa rõ', tone: 'muted' });
  });
});

describe('healthTips — Cách bật lại lịch tự cập nhật đêm', () => {
  const tip = (h: SystemHealth) => healthTips(h).find((t) => t.key === 'nightly');

  it('warn, linger có ⇒ 2 lệnh: xem tình trạng rồi bật lại', () => {
    const t = tip(healthBase(nightly()));
    expect(t?.title).toBe('Cách bật lại lịch tự cập nhật đêm');
    expect(t?.steps.map((s) => s.cmd)).toEqual(['genh auto-update status', 'genh auto-update enable']);
    expect(t?.steps[0].text).toBe('Trên máy chủ, xem tình trạng lịch:');
    expect(t?.steps[1].text).toBe('Sau đó bật lại lịch:');
  });

  it('warn, linger tắt ⇒ 3 lệnh, bước enable-linger ở giữa', () => {
    const t = tip(healthBase(nightly({ linger: 'no' })));
    expect(t?.steps.map((s) => s.cmd)).toEqual(['genh auto-update status', 'sudo loginctl enable-linger $USER', 'genh auto-update enable']);
    expect(t?.steps.length).toBeGreaterThanOrEqual(2);
    expect(t?.steps.length).toBeLessThanOrEqual(3);
  });

  it('không có dấu chấm dính sau lệnh', () => {
    for (const linger of ['yes', 'no'] as const) {
      for (const s of tip(healthBase(nightly({ linger })))?.steps ?? []) expect(s.cmd?.endsWith('.')).toBe(false);
    }
  });

  it('ok / off / other / unknown / không có khối ⇒ không có hướng dẫn (other: enable ở bản này bị từ chối)', () => {
    for (const state of ['ok', 'off', 'other', 'unknown'] as const) expect(tip(healthBase(nightly({ state })))).toBeUndefined();
    expect(tip(healthBase())).toBeUndefined();
  });

  it('linger không áp dụng (crontab/launchd/schtasks) ⇒ không nhắc enable-linger', () => {
    const t = tip(healthBase(nightly({ linger: 'not_applicable' })));
    expect(t?.steps.map((s) => s.cmd)).toEqual(['genh auto-update status', 'genh auto-update enable']);
  });

  it('Sếp đã tắt mà lịch vẫn bật ⇒ hai lựa chọn: tắt hẳn hoặc giữ tự cập nhật', () => {
    const t = tip(healthBase(nightly({ reason: 'opted_out_running', opted_out: true })));
    expect(t?.title).toBe('Sếp đã tắt tự cập nhật đêm nhưng lịch vẫn bật');
    expect(t?.steps.map((s) => s.cmd)).toEqual(['genh auto-update status', 'genh auto-update disable', 'genh auto-update enable']);
    for (const st of t?.steps ?? []) expect(st.cmd?.endsWith('.')).toBe(false);
  });
});

describe('autoInstallHint — chọn bản đủ 24 giờ', () => {
  // Tháng 10/2026, giờ trình duyệt (lịch đêm chạy ~03:00 theo giờ máy).
  const local = (d: number, h: number, m = 0) => new Date(2026, 9, d, h, m, 0).getTime();
  const iso = (ms: number) => new Date(ms).toISOString();
  // Bây giờ: 10/10 10:00 (giờ trình duyệt). v0.1.53 ra 25 giờ trước (đủ hạn), v0.1.54 ra 1 giờ trước (còn 23 giờ).
  const T = local(10, 10);
  const c53 = { tag: 'v0.1.53', eligible_at: iso(T - 60 * 60_000) };
  const c54 = { tag: 'v0.1.54', eligible_at: iso(T + 23 * 3600_000) };

  it('ứng viên đủ hạn ≠ bản mới nhất ⇒ nêu cả hai: cài v0.1.53 đêm nay, v0.1.54 đêm sau', () => {
    expect(autoInstallHint([c53, c54], 'v0.1.54', iso(T - 3600_000), T)).toBe(
      'Tự cài v0.1.53 đêm 11/10 (~03:00) — v0.1.54 tự cài sau khi đủ 24 giờ (đêm 12/10)',
    );
  });

  it('bản mới nhất cũng đủ hạn trước lần ~03:00 đầu tiên ⇒ như cũ, chỉ nêu đêm', () => {
    const early = { tag: 'v0.1.54', eligible_at: iso(local(11, 1)) };
    expect(autoInstallHint([c53, early], 'v0.1.54', iso(local(10, 1)), T)).toBe('Tự cài đêm 11/10 (~03:00)');
    expect(autoInstallHint([early], 'v0.1.54', iso(local(10, 1)), T)).toBe('Tự cài đêm 11/10 (~03:00)');
  });

  it('chưa ứng viên nào đủ hạn đêm nay ⇒ tìm tới lần ~03:00 đầu tiên có ứng viên đủ hạn', () => {
    const later = { tag: 'v0.1.54', eligible_at: iso(local(11, 5)) };
    expect(autoInstallHint([later], 'v0.1.54', iso(T), T)).toBe('Tự cài đêm 12/10 (~03:00)');
  });

  it('sau 03:00 trong ngày thì "đêm nay" là đêm của ngày kế tiếp; trước 03:00 thì chính 03:00 hôm nay', () => {
    const ready = { tag: 'v0.1.53', eligible_at: iso(local(9, 3)) };
    expect(autoInstallHint([ready], 'v0.1.53', iso(local(8, 3)), local(10, 4))).toBe('Tự cài đêm 11/10 (~03:00)');
    expect(autoInstallHint([ready], 'v0.1.53', iso(local(8, 3)), local(10, 1))).toBe('Tự cài đêm 10/10 (~03:00)');
  });

  it('bản mới nhất không có trong ứng viên ⇒ đêm của nó tính theo published_at + 24 giờ', () => {
    const pub = iso(T - 3600_000);
    expect(autoInstallHint([c53], 'v0.1.54', pub, T)).toBe('Tự cài v0.1.53 đêm 11/10 (~03:00) — v0.1.54 tự cài sau khi đủ 24 giờ (đêm 12/10)');
    // Không biết published_at ⇒ chỉ nêu bản sẽ cài.
    expect(autoInstallHint([c53], 'v0.1.54', null, T)).toBe('Tự cài v0.1.53 đêm 11/10 (~03:00)');
  });

  it('candidates rỗng / không có (api cũ) ⇒ hành vi cũ theo published_at', () => {
    const pub = iso(local(9, 8));
    const legacy = autoInstallHint(pub, T);
    expect(legacy).toBe('Tự cài đêm 11/10 (~03:00)');
    expect(autoInstallHint([], 'v0.1.54', pub, T)).toBe(legacy);
    expect(autoInstallHint(undefined, 'v0.1.54', pub, T)).toBe(legacy);
    expect(autoInstallHint(null, 'v0.1.54', pub, T)).toBe(legacy);
    expect(autoInstallHint([], 'v0.1.54', null, T)).toBeNull();
  });

  it('ứng viên hỏng (thiếu tag, ngày không đọc được) bị bỏ qua, không ném lỗi', () => {
    const junk = [{ tag: '', eligible_at: iso(T) }, { tag: 'v0.1.53', eligible_at: 'hôm qua' }, null, 42] as unknown as Array<{ tag: string; eligible_at: string }>;
    expect(autoInstallHint(junk, 'v0.1.54', iso(local(9, 8)), T)).toBe('Tự cài đêm 11/10 (~03:00)');
    expect(autoInstallHint('không-phải-ngày', T)).toBeNull();
  });

  it('ứng viên đủ hạn đêm nay là bản BỊ CHẶN (lỗi lần trước) ⇒ đêm đó không cài gì: hứa đêm bản kế tiếp đủ hạn', () => {
    // genh chọn bản cao nhất đủ hạn (v0.1.53) rồi bỏ qua vì bị chặn ⇒ không phải "Tự cài v0.1.53 đêm 11/10".
    expect(autoInstallHint([c53, c54], 'v0.1.54', iso(T - 3600_000), T, 'v0.1.53')).toBe('Tự cài đêm 12/10 (~03:00)');
    // Bản cũ hơn đủ hạn không được hứa thay bản bị chặn (genh không lùi về bản thấp hơn).
    const c52b = { tag: 'v0.1.52', eligible_at: iso(T - 48 * 3600_000) };
    expect(autoInstallHint([c52b, c53, c54], 'v0.1.54', iso(T - 3600_000), T, 'v0.1.53')).toBe('Tự cài đêm 12/10 (~03:00)');
    // Không bị chặn ⇒ như cũ.
    expect(autoInstallHint([c53, c54], 'v0.1.54', iso(T - 3600_000), T, null)).toBe(
      'Tự cài v0.1.53 đêm 11/10 (~03:00) — v0.1.54 tự cài sau khi đủ 24 giờ (đêm 12/10)',
    );
  });

  it('updateView: chỉ hứa khi lịch đêm BẬT và bản mới nhất không bị chặn; có ứng viên khác bản mới nhất thì nêu cả hai', () => {
    const d: SystemUpdate = {
      ...base, current: 'v0.1.52', latest: 'v0.1.54', auto_update_enabled: true,
      published_at: iso(T - 3600_000), nightly_candidates: [c53, c54],
    };
    const kicker = (x: SystemUpdate) => {
      const v = updateView(x, { waitingFor: null, offline: false, now: T });
      return v.kind === 'available' ? v.kicker : v.kind;
    };
    expect(kicker(d)).toBe('Đang dùng v0.1.52 · Tự cài v0.1.53 đêm 11/10 (~03:00) — v0.1.54 tự cài sau khi đủ 24 giờ (đêm 12/10) — hoặc bấm Cập nhật ngay');
    expect(kicker({ ...d, auto_update_enabled: false })).toBe('Đang dùng v0.1.52 · bấm Cập nhật ngay (mất khoảng 2–5 phút, tự sao lưu trước)');
    expect(kicker({ ...d, auto_update_enabled: null })).toBe('Đang dùng v0.1.52 · bấm Cập nhật ngay (mất khoảng 2–5 phút, tự sao lưu trước)');
    expect(kicker({ ...d, blocked_version: 'v0.1.54' })).toMatch(/lịch đêm không tự cài lại/);
    // Bản bị chặn là ứng viên CŨ hơn bản mới nhất: không hứa cài nó.
    expect(kicker({ ...d, blocked_version: 'v0.1.53' })).toBe('Đang dùng v0.1.52 · Tự cài đêm 12/10 (~03:00) — hoặc bấm Cập nhật ngay');
    // api cũ (không có nightly_candidates): cách cũ theo published_at.
    const { nightly_candidates: _omit, ...old } = d;
    void _omit;
    expect(kicker(old as SystemUpdate)).toBe('Đang dùng v0.1.52 · Tự cài đêm 12/10 (~03:00) — hoặc bấm Cập nhật ngay');
  });
});

describe('Trợ giúp — genh auto-update status', () => {
  it('có lệnh trong danh sách, không có dấu chấm dính sau lệnh', () => {
    const c = GENH_COMMANDS.find((x) => x.cmd === 'genh auto-update status');
    expect(c?.what).toBe('Xem lịch tự cập nhật đêm có đang chạy không: lần chạy gần nhất, lần kế tiếp, linger. Có cảnh báo thì làm đúng lệnh nó in ra.');
    expect(c?.cmd.endsWith('.')).toBe(false);
  });
});

describe('Chuông — sự cố host.nightly', () => {
  it('HEALTH_KINDS có host.nightly ⇒ dải "Cần Sếp xử lý" và thẻ Sức khoẻ đổi ngay khi chuông nhận sự cố (không chờ 60 giây)', () => {
    expect(HEALTH_KINDS.has('host.nightly')).toBe(true);
  });
});
