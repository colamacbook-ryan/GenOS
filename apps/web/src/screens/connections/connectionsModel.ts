import type { Channel, CliProfile, HubLink, McpServer, Provider, SocialAccount, TelegramHostStatus, TelegramLastTest } from '@gen-harness/contracts';

/**
 * v0.1.42 (F-7): Kết nối — mỗi thứ một thẻ, CÙNG một kiểu trạng thái (viên trạng thái):
 * Đang chạy · Cần Sếp xử lý · Chưa nối. Hàm thuần, không gọi API — trang chỉ đưa dữ liệu đã tải vào.
 */
export type ConnStatus = 'running' | 'needs_boss' | 'not_connected';

export const CONN_STATUS_LABEL: Record<ConnStatus, string> = {
  running: 'Đang chạy',
  needs_boss: 'Cần Sếp xử lý',
  not_connected: 'Chưa nối',
};

/** Kênh: active → Đang chạy; hết phiên / lỗi / đang chờ quét QR → Cần Sếp xử lý; còn lại → Chưa nối. */
export function channelConnStatus(c: Pick<Channel, 'state'>): ConnStatus {
  switch (c.state) {
    case 'active':
      return 'running';
    case 'expired':
    case 'error':
    case 'pending_qr':
      return 'needs_boss';
    default:
      // logged_out, not_installed, identity_only (và trạng thái lạ) — chưa nhận tin.
      return 'not_connected';
  }
}

/** Trạng thái tài khoản CLI đang dùng (null = chưa có tài khoản nào). */
export type CliState = CliProfile['state'] | null;

/** Thẻ tài khoản CLI: chưa có tài khoản đang dùng → Chưa nối; còn hạn / sắp hết hạn → Đang chạy; hết hạn → Cần Sếp xử lý. */
export function cliConnStatus(active: Pick<CliProfile, 'state'> | undefined): ConnStatus {
  if (!active) return 'not_connected';
  return active.state === 'ok' || active.state === 'expiring' ? 'running' : 'needs_boss';
}

/** Hồ sơ CLI đang dùng → trạng thái gọn cho `brainStatus`. */
export function activeCliState(profiles: CliProfile[] | undefined): CliState {
  return profiles?.find((p) => p.active)?.state ?? null;
}

/**
 * Bộ não AI: có nguồn sinh chữ dùng được (khoá API bật, đăng nhập ổn; hoặc tài khoản CLI còn hạn) và đã chọn model →
 * Đang chạy; chưa có nguồn nào → Chưa nối; còn lại (chưa chọn model, nguồn bật mà hết hạn/lỗi, CLI hết hạn) → Cần Sếp
 * xử lý.
 */
export function brainStatus({
  providers,
  cliStatus = null,
  noModel = false,
}: {
  providers: Pick<Provider, 'kind' | 'enabled' | 'auth_state'>[] | undefined;
  cliStatus?: CliState | CliState[];
  noModel?: boolean;
}): ConnStatus {
  const gen = (providers ?? []).filter((p) => p.kind !== 'system_one');
  const enabled = gen.filter((p) => p.enabled);
  const clis = (Array.isArray(cliStatus) ? cliStatus : [cliStatus]).filter((s): s is NonNullable<CliState> => s !== null);
  const usableProvider = enabled.some((p) => p.auth_state === 'ok' || p.auth_state === 'expiring');
  const usableCli = clis.some((s) => s === 'ok' || s === 'expiring');
  if (!gen.length && !clis.length) return 'not_connected';
  if (noModel) return 'needs_boss';
  if (!usableProvider && !usableCli) return 'needs_boss';
  const broken = enabled.some((p) => p.auth_state === 'expired' || p.auth_state === 'error') || clis.some((s) => s === 'expired');
  return broken ? 'needs_boss' : 'running';
}

/**
 * Facebook (Tài khoản mạng xã hội): chưa có tài khoản → Chưa nối; Sếp đã dừng khẩn hoặc có tài khoản cần đăng nhập
 * lại / tạm dừng → Cần Sếp xử lý; có tài khoản đang đọc → Đang chạy.
 */
export function facebookStatus({
  accounts,
  halted = false,
}: {
  accounts: Pick<SocialAccount, 'platform' | 'status'>[] | undefined;
  halted?: boolean;
}): ConnStatus {
  const fb = (accounts ?? []).filter((a) => a.platform === 'facebook' && a.status !== 'revoked');
  if (!fb.length) return 'not_connected';
  if (halted) return 'needs_boss';
  if (fb.some((a) => a.status === 'needs_login' || a.status === 'paused' || a.status === 'pending_login')) return 'needs_boss';
  return fb.some((a) => a.status === 'active') ? 'running' : 'not_connected';
}

/**
 * Gen-hub: đang nối → Đang chạy; token sắp/đã hết hạn hoặc lỗi → Cần Sếp xử lý; chưa điền địa chỉ/token → Chưa nối.
 * Đã điền (configured) mà còn tắt (vd vừa sửa địa chỉ/token, chờ "Kiểm tra") → Cần Sếp xử lý — việc Sếp đang làm dở.
 */
export function hubStatus(link: Pick<HubLink, 'status' | 'configured'> | undefined): ConnStatus {
  if (!link || !link.configured) return 'not_connected';
  switch (link.status) {
    case 'ok':
      return 'running';
    case 'expiring':
    case 'expired':
    case 'error':
      return 'needs_boss';
    default:
      return 'needs_boss';
  }
}

/** MCP Hub: chưa có máy chủ nào bật → Chưa nối; máy chủ đang bật mà lỗi → Cần Sếp xử lý; còn lại → Đang chạy. */
export function mcpStatus(servers: Pick<McpServer, 'is_enabled' | 'health'>[] | undefined): ConnStatus {
  const on = (servers ?? []).filter((s) => s.is_enabled);
  if (!on.length) return 'not_connected';
  return on.some((s) => s.health === 'error' || s.health === 'unreachable') ? 'needs_boss' : 'running';
}

/** Số máy chủ MCP đang bật (dòng meta của thẻ MCP). */
export function mcpEnabledCount(servers: Pick<McpServer, 'is_enabled'>[] | undefined): number {
  return (servers ?? []).filter((s) => s.is_enabled).length;
}

/**
 * v0.1.44 (F-8c) — Telegram ("Báo động & bản tin"): chưa cấu hình → Chưa nối; đã cấu hình mà Gửi thử lỗi, hoặc Trực
 * canh máy chủ báo gửi lỗi / không đọc được cấu hình (failed | key_mismatch) → Cần Sếp xử lý; đã cấu hình + bật + Gửi
 * thử đạt → Đang chạy; còn lại (chưa Gửi thử, đang tắt) → Cần Sếp xử lý (việc Sếp đang làm dở).
 */
export function telegramConnStatus(
  t:
    | {
        configured?: boolean;
        enabled?: boolean;
        last_test?: Pick<TelegramLastTest, 'status'> | null;
        host?: Pick<TelegramHostStatus, 'telegram'> | null;
      }
    | null
    | undefined,
): ConnStatus {
  if (!t || t.configured !== true) return 'not_connected';
  const host = t.host?.telegram;
  if (t.last_test?.status === 'fail' || host === 'failed' || host === 'key_mismatch') return 'needs_boss';
  if (t.enabled && t.last_test?.status === 'pass') return 'running';
  return 'needs_boss';
}

/**
 * v0.1.68 (F017, F130): nguồn "khoá API" = nguồn sinh chữ dán khoá (Gemini, DeepSeek, tương thích OpenAI…). KHÔNG tính tài
 * khoản CLI (Google Antigravity, Claude Code — đăng nhập, không có khoá, có thẻ riêng) và Jev / embedding (không sinh chữ).
 */
export function isKeySource(p: Pick<Provider, 'kind'>): boolean {
  const k: string = p.kind;
  return k !== 'antigravity_cli' && k !== 'claude_code_cli' && k !== 'system_one' && k !== 'embedding';
}

/**
 * v0.1.68 (F060, F135): thẻ kênh nhắn tin chỉ hiện kênh có việc để làm ở đây. Telegram đã có thẻ riêng "Báo động & bản tin"
 * (TelegramCard, nhập token + Gửi thử) — thẻ kênh "Telegram · Chưa có trong bản này" nói ngược lại nên ẩn; LinkedIn mới chỉ
 * dùng để hợp nhất danh tính (`identity_only`), chưa có đường nhập nên "Cấu hình" mở hộp thoại trống — ẩn tới khi có.
 */
export function visibleChannels<T extends Pick<Channel, 'type' | 'state'>>(channels: T[]): T[] {
  return channels.filter((c) => c.type !== 'telegram' && !(c.type === 'linkedin' && c.state === 'identity_only'));
}

/** Thứ tự thẻ kênh: Zalo · WhatsApp, rồi các kênh khác API trả (giữ thứ tự API). Telegram có thẻ riêng (`visibleChannels`). */
export const CHANNEL_ORDER = ['zalo', 'whatsapp', 'telegram'];

export function orderChannels<T extends Pick<Channel, 'type'>>(channels: T[]): T[] {
  const rank = (t: string) => {
    const i = CHANNEL_ORDER.indexOf(t);
    return i < 0 ? CHANNEL_ORDER.length : i;
  };
  return channels
    .map((c, i) => ({ c, i }))
    .sort((a, b) => rank(a.c.type) - rank(b.c.type) || a.i - b.i)
    .map((x) => x.c);
}

/**
 * v0.1.72: kênh có thẻ «Kênh quản trị» — Zalo, WhatsApp đã cài trong bản đang chạy (theo thứ tự Zalo · WhatsApp). Thẻ tự
 * nói rõ khi kênh chạy bằng tài khoản cá nhân (chưa dùng được).
 */
export function ownerChannelTypes(channels: Array<Pick<Channel, 'type' | 'installed'>> | undefined): Array<'zalo' | 'whatsapp'> {
  const ok = new Set((channels ?? []).filter((c) => c.installed).map((c) => c.type as string));
  return (['zalo', 'whatsapp'] as const).filter((t) => ok.has(t));
}
