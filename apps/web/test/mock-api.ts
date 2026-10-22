/**
 * In-memory mock of the API (docs/api/phase-1.md + phase-2.md) for
 * `npm run dev:mock` and the Playwright tests. Not shipped in the bundle.
 *
 *   owner@genesis.local / matkhau-rat-dai-2026, PIN 246810    (role owner)
 *   operator@genesis.local / matkhau-rat-dai-2026, PIN 135790 (role operator, fewer screens)
 *   auditor@genesis.local / matkhau-rat-dai-2026, PIN 975310  (role auditor, read-only)
 *   manager@genesis.local / matkhau-rat-dai-2026, PIN 864202  (role manager, team scope)
 *   setup token: GH-SETUP-7Q4K-2M9X
 *
 * MOCK_SETUP=fresh starts at step 1; MOCK_LATENCY=<ms> delays every response;
 * MOCK_SIMULATE=0 turns off the live raw-message simulation; MOCK_MUST_CHANGE=1 makes the Owner
 * face the forced "Đặt mật khẩu mới" screen (as after `genh reset-password`).
 * `/api/v1/ws` is served by `upgrade()` (see vite.config.ts).
 */
import type { IncomingMessage, ServerResponse } from 'node:http';
import type { Duplex } from 'node:stream';
import { randomUUID } from 'node:crypto';
import type { AccessInfo, AgentIdentity, BossCheck, DraftDetail, HealthIssue, HubLink, Match, SocialAccount, SystemHealth, SystemUpdate } from '@gen-harness/contracts';
import { createPhase2, maskText, seedRows, type P2Ctx } from './mock-phase2';
import { createMock as createP3Core } from './mock-p3-core';
import { createMock as createP3Queue } from './mock-p3-queue';
import { createMock as createP3Relations } from './mock-p3-relations';
import { createMock as createP3Graph } from './mock-p3-graph';
import { createMock as createP3Market } from './mock-p3-market';
import { createMock as createP3People } from './mock-p3-people';
import { createMock as createP4Agents } from './mock-p4-agents';
import { AGENT_IDS, USER_IDS } from './mock-ids';
import { createMock as createP4Api } from './mock-p4-api';
import { createMock as createP4Mcp } from './mock-p4-mcp';
import { createMock as createSocial } from './mock-social';
import { createMock as createBossChecks, isLocalHost } from './mock-boss-checks';
import { createMock as createTelegram, type TelegramOutcome } from './mock-telegram';
import { createMock as createDiagnostics } from './mock-diagnostics';
import { createMock as createP4Plugins } from './mock-p4-plugins';
import { createMock as createP4System } from './mock-p4-system';
import { createMock as createGen } from './mock-gen';
import { createMock as createGenV0150, type KhoWriteReq } from './mock-gen-v0150';
import { createMock as createGenCoach, type GenCoachOptions } from './mock-gen-coach';
import { createMock as createDefaults } from './mock-defaults';
import { createMock as createOwnerChannel } from './mock-owner-channel';
import { createMock as createOwner, type OwnerDataMode } from './mock-owner';
import { acceptWebSocket, type MockSocket } from './mock-ws';
import { buildScreenTree, SCREEN_BY_KEY } from '../../../packages/contracts/src/screens';
import type { NavDomain, NavItem, SetupState } from '../../../packages/contracts/src/schema';

type Next = (err?: unknown) => void;

export const MOCK_TOKEN = 'GH-SETUP-7Q4K-2M9X';
export const MOCK_OWNER = { email: 'owner@genesis.local', password: 'matkhau-rat-dai-2026', pin: '246810' };

/** Badge values shown in the design, served so the shell can be compared with it. */
const DESIGN_BADGES: Record<string, { value: string; tone: 'ok' | 'warn' | 'bad' }> = {
  overview: { value: '9', tone: 'bad' },
  inbox: { value: '28', tone: 'warn' },
  workbench: { value: '6', tone: 'warn' },
  opportunity: { value: '41', tone: 'ok' },
  supply: { value: '27', tone: 'ok' },
  raw: { value: '18k', tone: 'warn' },
  identity: { value: '12', tone: 'warn' },
  mcp: { value: '7', tone: 'ok' },
  plugins: { value: '11', tone: 'ok' },
};

interface User {
  id: string;
  email: string;
  password: string;
  pin: string;
  display_name: string;
  role: { code: RoleCode; name: string };
  hidden: Set<string>;
  /** v0.1.19: mật khẩu do hệ thống đặt (genh reset-password) → Console buộc đặt mật khẩu mới. */
  mustChange?: boolean;
  createdAt?: string;
  /** v0.1.22: tài khoản bị khoá (Người dùng › Khoá) — không đăng nhập được. */
  inactive?: boolean;
  lastLoginAt?: string | null;
}

const MOCK_HARD_BOUNDARIES = [
  'Chỉ lưu tin của nhóm Sếp đã chọn nghe',
  'Hệ thống không tự ra quyết định nhân sự',
  'Gửi ra ngoài, vượt ngưỡng tiền, liên quan nhân sự → luôn chờ duyệt ở Bàn làm việc',
];

export interface MockOptions {
  setup?: 'fresh' | 'finished';
  latencyMs?: number;
  badges?: boolean;
  /** Live raw-message / QR-scan simulation (default on; MOCK_SIMULATE=0 turns it off). */
  simulate?: boolean;
  /** Let `PUT /setup/steps/12` finish without steps 8–9 (the real API refuses in phase 2). */
  allowFinish?: boolean;
  /** Test-only (e2e giai đoạn 4.6): đánh dấu sẵn các bước 1..n-1 là 'done', `current_step = n`, tạo và đăng
   * nhập sẵn tài khoản Owner — để test thẳng bước 10/11 mà không phải đi lại QR/CLI/refinery từ đầu. */
  startAtStep?: number;
  /** `/system/update` báo có bản mới (thẻ "Có bản mới" ở Tổng quan). */
  updateAvailable?: boolean;
  /** v0.1.19: Owner vừa được genh reset-password → `must_change_password` (MOCK_MUST_CHANGE=1). */
  mustChangePassword?: boolean;
  /** v0.1.42: đã có ít nhất 1 nhân viên (mặc định true) — false thì Đánh giá/Chăm sóc ẩn trên thanh bên. */
  staff?: boolean;
  /**
   * v0.1.59 (G1): chế độ dữ liệu Mặt tiền `/owner/*` — 'full' (mặc định) · 'empty' (chưa có tin, insights.state='empty') ·
   * 'collecting' (insights.state='collecting', còn 5 ngày) · 'nochannel' (channels_connected=0). `vite.config.ts` đọc
   * `MOCK_OWNER_DATA`; `__mock/reset` dựng lại theo giá trị này; đổi lúc chạy bằng `__mock/p3/owner/data {mode}`.
   */
  ownerData?: OwnerDataMode;
}

// RBAC as apps/api gh/auth/rbac.py seeds it: Owner, Manager, Operator, Agent NV, Auditor.
export type RoleCode = 'owner' | 'manager' | 'operator' | 'agent_staff' | 'auditor';
export const ROLE_ORDER: RoleCode[] = ['owner', 'manager', 'operator', 'agent_staff', 'auditor'];
/** Nguồn dữ liệu duy nhất cho `Me.permissions` VÀ `GET/PATCH /permissions` (PLAN 4.5) — `mock-p4-system.ts`
 * sửa thẳng object này (không copy) để đổi ma trận ở màn Quyền hạn cũng đổi luôn năng lực thật của vai trò,
 * giống hệt cách `role_permissions` là một bảng duy nhất ở backend thật. */
export const MATRIX: Record<string, [string, string, string, string, string]> = {
  'overview.read': ['all', 'team', 'assigned', 'none', 'all'],
  'queue.read': ['all', 'team', 'all', 'assigned', 'all'],
  'queue.act': ['all', 'team', 'all', 'assigned', 'none'],
  'profile.read': ['all', 'team', 'all', 'assigned', 'all'],
  'profile.write': ['all', 'team', 'all', 'assigned', 'none'],
  'people_review.read': ['all', 'none', 'none', 'none', 'none'],
  'people_review.write': ['all', 'none', 'none', 'none', 'none'],
  'care.read': ['all', 'none', 'none', 'none', 'none'],
  'opportunity.read': ['all', 'team', 'all', 'assigned', 'all'],
  'opportunity.write': ['all', 'team', 'all', 'assigned', 'none'],
  'action.draft': ['all', 'team', 'assigned', 'assigned', 'none'],
  'action.approve': ['all', 'team', 'none', 'none', 'none'],
  'audit.read': ['all', 'team', 'none', 'none', 'all'],
  'data.read': ['all', 'none', 'none', 'none', 'all'],
  'data.manage': ['all', 'none', 'none', 'none', 'none'],
  'system.read': ['all', 'none', 'none', 'none', 'all'],
  'system.manage': ['all', 'none', 'none', 'none', 'none'],
  'roles.manage': ['all', 'none', 'none', 'none', 'none'],
};
/** Bản gốc của MATRIX — `__mock/reset` khôi phục (ma trận sửa ở màn Quyền hạn / `__mock/perm` không rò sang test sau). */
const MATRIX_DEFAULT: Record<string, [string, string, string, string, string]> = JSON.parse(JSON.stringify(MATRIX));
function restoreMatrix(): void {
  for (const k of Object.keys(MATRIX)) if (!(k in MATRIX_DEFAULT)) delete MATRIX[k];
  for (const [k, v] of Object.entries(MATRIX_DEFAULT)) MATRIX[k] = [...v] as [string, string, string, string, string];
}
const SCREEN_PERMISSION: Record<string, string[]> = {
  overview: ['overview.read'], inbox: ['queue.read'], workbench: ['action.draft', 'action.approve'],
  directory: ['profile.read'], graph: ['profile.read'], profile: ['profile.read'], notebook: ['profile.read'],
  documents: ['profile.read'],
  opportunity: ['opportunity.read'], supply: ['opportunity.read'], search: ['opportunity.read'],
  people: ['people_review.read'], care: ['care.read'],
  listenlog: ['data.read'], raw: ['data.read'], rules: ['data.read'], clean: ['data.read'], identity: ['data.read'],
  agents: ['system.read'], api: ['system.read'], mcp: ['system.read'], plugins: ['system.read'],
  system: ['system.read', 'audit.read'],
  // v0.1.42: Kết nối (system.read), Đội ngũ (roles.manage).
  connections: ['system.read'], team: ['roles.manage'],
};
export function permissionsOf(role: RoleCode): Record<string, string> {
  const i = ROLE_ORDER.indexOf(role);
  return Object.fromEntries(Object.entries(MATRIX).map(([k, v]) => [k, v[i]]));
}
export function hiddenScreens(role: RoleCode): Set<string> {
  const perms = permissionsOf(role);
  const hidden = new Set(Object.entries(SCREEN_PERMISSION).filter(([, need]) => !need.some((p) => perms[p] !== 'none')).map(([k]) => k));
  // Q4 (docs/PLAN.md): Auditor VẪN thấy màn "Đánh giá con người" trong danh mục (nhánh log — nhật ký ai đã
  // xem), dù phạm vi chung `people_review.read` của Auditor là 'none' như Manager. Đây là ngoại lệ nội dung
  // (che điểm/chứng cứ ở tầng route), không phải ẩn hẳn màn như Manager — khác `care` (Q4 không nói tới `care`).
  if (role === 'auditor') hidden.delete('people');
  return hidden;
}
/** Which realtime events a connection may receive (docs/api/phase-2.md § WebSocket). */
const EVENT_PERMISSION: Array<[string, string | null]> = [
  ['social.', 'system.manage'],
  ['raw.', 'data.read'],
  ['listen.', 'data.read'],
  ['refinery.', 'data.read'],
  ['channel.', 'system.read'],
  ['cli.', 'system.manage'],
  ['mcp.', 'system.read'],
  ['plugin.', 'system.read'],
  ['header', null],
];

/**
 * GET /navigation như API thật (v0.1.42): `hidden` (tham số) = màn vai trò KHÔNG được thấy (bỏ khỏi cây);
 * node `hidden: true` = có trong cây, có route, nhưng không hiện thanh bên (màn `navHidden`, và `needsStaff` khi
 * chưa có nhân viên). `count` = số khoá màn không ẩn trong domain; domain có `collapsed` theo DOMAINS.
 */
export function buildNavigation(
  hidden: Set<string> = new Set(),
  badges = true,
  opts: { hasStaff?: boolean } = {},
): NavDomain[] {
  const hasStaff = opts.hasStaff ?? true;
  const navHidden = (key: string) => {
    const m = SCREEN_BY_KEY[key];
    return !!m?.navHidden || (!!m?.needsStaff && !hasStaff);
  };
  const leaf = (key: string, name: string, en: string, icon: string): NavItem => ({
    key,
    name,
    en,
    icon,
    badge: badges ? (DESIGN_BADGES[key] ?? null) : null,
    children: [],
    ...(navHidden(key) ? { hidden: true } : {}),
  });
  const visible = (it: NavItem) => !it.hidden;
  return buildScreenTree()
    .map((d) => {
      const groups: NavItem[] = [];
      for (const e of d.entries) {
        if (e.kind === 'screen') {
          if (!hidden.has(e.screen.key)) groups.push(leaf(e.screen.key, e.screen.name, e.screen.en, e.screen.icon));
          continue;
        }
        const g = e.group;
        const children = g.children.filter((c) => !hidden.has(c.key)).map((c) => leaf(c.key, c.name, c.en, c.icon));
        const self = g.key && !hidden.has(g.key) ? leaf(g.key, g.name, SCREEN_BY_KEY[g.key].en, g.icon) : null;
        if (self) {
          groups.push({ ...self, children });
        } else if (children.length) {
          groups.push({ key: null, name: g.name, icon: g.icon, badge: null, children });
        }
      }
      const count = groups.reduce(
        (n, g) => n + (g.key && visible(g) ? 1 : 0) + (g.children?.filter((c) => c.key && visible(c)).length ?? 0),
        0,
      );
      return {
        domain: d.id,
        label: d.label,
        crumb: d.crumb,
        icon: d.icon,
        tone: d.id === 'business' ? ('ok' as const) : ('accent' as const),
        collapsed: d.collapsedByDefault,
        count,
        groups,
      };
    })
    .filter((d) => d.groups.length > 0);
}

/** [key, title, required, available] — phase 2 serves 1–7 and 12. */
const STEP_DEFS: Array<[string, string, boolean, boolean]> = [
  ['welcome', 'Chào mừng', true, true],
  ['owner', 'Tài khoản Owner', true, true],
  ['org', 'Tổ chức & xưng hô', true, true],
  ['brain', 'Bộ não AI', false, true], // v0.1.29: "Để sau" được (hộp cảnh báo + dải "Chưa có model")
  ['channels', 'Kết nối kênh', false, true],
  ['groups', 'Chọn nhóm lắng nghe', false, true],
  ['refinery', 'Sàng lọc dữ liệu', false, true],
  ['agent', 'Agent đầu tiên', false, true],
  ['autonomy', 'Tự trị & ranh giới', false, true],
  ['team', 'Mời đội ngũ', false, true],
  ['backup', 'Sao lưu', false, true],
  ['finish', 'Hoàn tất', true, true],
];

interface AuditRow {
  id: string;
  at: string;
  actor_type: string;
  actor_id: string | null;
  actor_label: string | null;
  action: string;
  target_type: string | null;
  target_id: string | null;
  target_label: string | null;
  autonomy_level: number | null;
  result: string;
  detail: unknown;
}

/** Số mức tự trị trong nhãn thiết kế ("mức 4" → 4), hoặc null khi không phải hành động của agent. */
const AUTONOMY_RE = /mức\s+(\d)/;
const RESULT_TONE: Record<string, string> = { g: 'ok', w: 'held', b: 'blocked' };

/** Nạp `docs/design/seed-data.json` `auditLog` (10 dòng) vào mảng `audit` dùng chung — cho tab Nhật ký của
 * Điều khiển hệ thống có gì đó để xem/xuất CSV ngay cả trước khi có sự kiện PIN thật nào (`record()` unshift
 * lên trên, nên sự kiện thật luôn mới hơn các dòng seed này). */
function seedAuditLog(audit: AuditRow[], fresh: boolean) {
  if (fresh) return;
  const rows = seedRows('auditLog');
  rows.forEach((row, i) => {
    const who = String(row.who ?? 'Hệ thống');
    const bot = row.bot === true;
    const autonomyMatch = AUTONOMY_RE.exec(String(row.level ?? ''));
    audit.push({
      id: randomUUID(),
      at: new Date(Date.now() - (i + 1) * 7 * 60_000).toISOString(),
      actor_type: bot ? 'agent' : who === 'Hệ thống' || who === 'Policy Engine' ? 'system' : 'user',
      actor_id: null,
      actor_label: who,
      action: String(row.action ?? ''),
      target_type: null,
      target_id: null,
      target_label: row.target != null ? String(row.target) : null,
      autonomy_level: autonomyMatch ? Number(autonomyMatch[1]) : null,
      result: RESULT_TONE[String(row.tone ?? '')] ?? 'ok',
      detail: null,
    });
  });
}

/**
 * v0.1.36 (F-6): nhãn mặc định từng kind sự cố (như `ops.health_alerts` của API: khoá, mức, đường dẫn, nút) — hook
 * `__mock/health` chỉ cần đưa `kind` (+ tuỳ chọn title/body/key) là ra đúng khuôn `HealthIssue`.
 */
const HEALTH_KIND_DEFAULTS: Record<string, Omit<HealthIssue, 'raised_at' | 'body'> & { body: string }> = {
  // Chữ/khoá chép đúng từ API: gh/data/ingest.py (channel.down), gh/health.py (raise_model_expired, _eval_update,
  // _eval_backup, _eval_worker, _eval_disk). Phần có số (phút, GB, bản) do healthView tính khi suy từ trạng thái.
  'channel.down': { key: 'channel.down:zalo', kind: 'channel.down', severity: 'bad', title: 'Kênh Zalo đã ngắt kết nối', body: 'Zalo Sếp: phiên đã hết hạn — đăng nhập lại để tiếp tục nhận tin.', link: '/system?tab=channels', action: 'Đăng nhập lại' },
  'model.auth_expired': { key: 'model.auth_expired:7d1c3a52-5b0e-4c1f-9a8e-2f6b1d4c9e03', kind: 'model.auth_expired', severity: 'warn', title: 'Model Claude cần đăng nhập lại', body: 'Gen và sàng lọc tin có thể dừng nếu không còn model khác. Bấm để đăng nhập lại.', link: '/system?tab=brain', action: 'Đăng nhập lại model' },
  'update.failed': { key: 'update.failed', kind: 'update.failed', severity: 'bad', title: 'Cập nhật lên bản mới chưa thành công', body: 'Hệ thống đã tự quay về bản cũ, dữ liệu an toàn. Bấm để xem và thử lại.', link: '/system?tab=storage', action: 'Xem & thử lại' },
  'backup.stale': { key: 'backup.stale', kind: 'backup.stale', severity: 'bad', title: 'Đã hơn 36 giờ chưa có bản sao lưu mới', body: 'Chưa có bản nào. Mở mục Sao lưu và bấm Sao lưu ngay để giữ an toàn dữ liệu.', link: '/system?tab=storage&focus=backup', action: 'Mở mục Sao lưu' },
  'worker.silent': { key: 'worker.silent', kind: 'worker.silent', severity: 'bad', title: 'Bộ xử lý nền đã ngừng 12 phút', body: 'Sàng lọc tin, nhắc việc và sao lưu theo lịch đang dừng. Bấm để xem cách khởi động lại.', link: '/system?tab=storage', action: 'Xem sức khoẻ' },
  'disk.low': { key: 'disk.low', kind: 'disk.low', severity: 'bad', title: 'Ổ đĩa sắp hết chỗ', body: 'Còn 3,0 GB trống, cần tối thiểu 5,0 GB — cập nhật tự động đang tạm dừng.', link: '/system?tab=storage', action: 'Xem cách giải phóng' },
  // v0.1.37 (F-73) — gh/health.py _eval_autostart (AUTOSTART_TITLE/AUTOSTART_FIX/AUTOSTART_DONE): đích là thẻ Sức khoẻ
  // (hướng dẫn từng bước, lệnh dạng mã) — cả dòng dải lẫn chuông.
  // v0.1.40 (F-12, F-2) — gh/health.py: bản sao ngoài máy quá 7 ngày (bad khi > 30 ngày) / lần gần nhất lỗi; việc nền
  // chạy quá giờ (key `job.timeout:<tên hàm>`, worker mở/đóng). Đích offsite là thẻ "Bản sao ngoài máy" (focus=offsite).
  // Chữ chép đúng gh/health.py (_eval_offsite, ACTIONS, OFFSITE_FAILED_BODY) — title "đã cũ N ngày" ghép khi suy sự cố.
  'offsite.stale': { key: 'offsite.stale', kind: 'offsite.stale', severity: 'warn', title: 'Bản sao ngoài máy đã cũ 8 ngày', body: "Cắm ổ USB/NAS rồi bấm 'Sao lưu ra ổ ngoài ngay' để có bản sao mới ngoài máy chủ", link: '/system?tab=storage&focus=offsite', action: 'Chọn nơi lưu / sao lưu ngay' },
  'offsite.failed': { key: 'offsite.failed', kind: 'offsite.failed', severity: 'warn', title: 'Sao lưu ra ổ ngoài chưa thành công', body: "Chưa thấy ổ USB/NAS — cắm lại ổ rồi bấm 'Sao lưu ra ổ ngoài ngay'", link: '/system?tab=storage&focus=offsite', action: 'Xem bản sao ngoài máy' },
  'job.timeout': { key: 'job.timeout:retention_sweep', kind: 'job.timeout', severity: 'warn', title: 'Việc nền "dọn dữ liệu theo hạn lưu" chạy quá giờ', body: 'Việc đã bị dừng và sẽ chạy lại ở lần sau. Lặp lại nhiều lần thì gửi kèm khi báo lỗi.', link: '/system?tab=storage', action: 'Xem sức khoẻ' },
  // v0.1.46 (F-21) — gh/health.py _eval_network: chép đúng OPEN_LAN_TITLE/OPEN_LAN_BODY/ACCESS_LINK; thẻ đích focus=access.
  'network.open_lan': { key: 'network.open_lan', kind: 'network.open_lan', severity: 'warn', title: 'Cổng đang mở cho cả mạng', body: 'Mọi máy cùng mạng (Wi-Fi văn phòng, khách…) đều thấy trang đăng nhập Gen-Harness. Bấm để xem lệnh chọn cách truy cập (chạy trên máy chủ): Tailscale (khuyên dùng), chỉ máy này, hoặc giữ mở cho mạng nội bộ.', link: '/system?tab=storage&focus=access', action: 'Chọn cách truy cập' },
  // v0.1.53 (F-99) — gh/health.py _eval_nightly (NIGHTLY_BODY/NIGHTLY_LINGER/NIGHTLY_ENABLE, HEALTH_LINK, ACTIONS): thân ghép
  // từ chuỗi cố định; số ngày / bước linger do healthView tính khi suy từ khối `nightly`.
  'host.nightly': { key: 'host.nightly', kind: 'host.nightly', severity: 'warn', title: 'Lịch tự cập nhật đêm chưa chạy 3 ngày', body: 'Máy chủ không tự lên bản mới. Trên máy chủ chạy: genh auto-update status · Sau đó bật lại lịch: genh auto-update enable', link: '/system?tab=storage&focus=health', action: 'Xem cách bật lại' },
  'host.autostart': { key: 'host.autostart', kind: 'host.autostart', severity: 'warn', title: 'Máy chủ có thể không tự chạy lại Gen-Harness khi bật lại máy', body: 'Docker chưa bật tự chạy khi mở máy — chạy một lần trên máy chủ: sudo systemctl enable docker · Lịch tự cập nhật và nút Cập nhật ngay chỉ chạy khi có người đăng nhập — chạy một lần: sudo loginctl enable-linger $USER · Chạy xong thì chạy genh status để cảnh báo tự hết', link: '/system?tab=storage', action: 'Xem cách bật' },
};

/** Hostname của một URL (rỗng/hỏng ⇒ ''). */
function hostOfUrl(url: string): string {
  try {
    return new URL(url).hostname;
  } catch {
    return '';
  }
}

/** v0.1.46 (F-21): `__mock/access` ghi đè `GET /system/access` (mặc định như GH_PUBLIC_URL compose: localhost, chế độ 'local'). */
export interface MockAccessOverride {
  public_url?: string;
  mode?: AccessInfo['mode'];
  bind_addr?: AccessInfo['bind_addr'];
  site_address?: string | null;
  checked_at?: string | null;
}

export interface MockHealthOverride {
  issues?: Array<Partial<HealthIssue>>;
  worker?: Partial<SystemHealth['worker']>;
  backup?: Partial<SystemHealth['backup']>;
  disk?: Partial<SystemHealth['disk']>;
  update?: Partial<SystemHealth['update']>;
  /** v0.1.37 (F-73): khối `autostart` (thiếu ⇒ không có khối, như api không có hộp thư với genh). */
  autostart?: SystemHealth['autostart'];
  /** v0.1.40 (F-12): ghi đè khối `offsite` (mặc định suy từ mock Bản sao ngoài máy); `null` = bỏ khối. */
  offsite?: SystemHealth['offsite'] | null;
  /** v0.1.53 (F-99): khối `nightly` (thiếu/`null` ⇒ không có khối, như api không có hộp thư với genh); `warn` mở sự cố host.nightly. */
  nightly?: SystemHealth['nightly'] | null;
}

/**
 * v0.1.53 (F-99): `__mock/nightly` đặt lịch tự cập nhật đêm / linger cho cả hai nơi Console đọc:
 *  - `update` trộn vào `GET /system/update` (vd `{ state: 'stalled', stalled_reason: 'linger_off' }`, `nightly`,
 *    `nightly_candidates`, `auto_update_enabled`, `latest`, `published_at`…);
 *  - `health` đặt khối `nightly` của `GET /system/health` (`null` = bỏ khối).
 * `__mock/reset` khôi phục mặc định (không có khối nightly, không có ứng viên).
 */
export interface MockNightlyOverride {
  update?: Partial<SystemUpdate>;
  health?: SystemHealth['nightly'] | null;
}

/** gh/health.py WORKER_SILENT_MINUTES. */
const WORKER_SILENT_MIN = 10;

function healthIssue(i: Partial<HealthIssue>, now: string): HealthIssue {
  const d = HEALTH_KIND_DEFAULTS[String(i.kind ?? '')] ?? { key: String(i.kind ?? 'other'), kind: String(i.kind ?? 'other'), severity: 'warn' as const, title: 'Sự cố', body: '', link: null, action: 'Xem' };
  return { ...d, raised_at: now, ...i } as HealthIssue;
}

function createMockState(opts: MockOptions = {}, broadcast: (type: string, data: unknown, toUser?: string) => void = () => {}) {
  const latency = opts.latencyMs ?? Number(process.env.MOCK_LATENCY ?? 0);
  /** Test-only: let step 12 finish although 8–9 (not built in phase 2) are missing. */
  const mockAllowFinish = opts.allowFinish ?? false;
  /** `/system/update`: mặc định đã mới nhất (thẻ ẩn); `updateAvailable` bật thẻ "Có bản mới". */
  const sysUpdate = {
    current: 'v0.1.16', latest: opts.updateAvailable ? 'v0.1.17' : 'v0.1.16', updater: 'systemd', linked: true, can_request: true,
    state: 'idle', message: null as string | null, from: null as string | null, to: null as string | null, started_at: null as string | null,
    finished_at: null as string | null, requested_at: null as string | null,
    release_url: 'https://github.com/Genesis-ryan-84-0567536339/Gen-Harness/releases', release_notes: '- Nút Cập nhật ngay trong Console',
    checked_at: new Date().toISOString() as string | null,
    // v0.1.53 (F-99, F-96): như api mới — chưa có nguyên nhân treo, chưa có ứng viên lịch đêm, genh chưa ghi nightly-status.json.
    stalled_reason: null as SystemUpdate['stalled_reason'],
    nightly_candidates: [] as NonNullable<SystemUpdate['nightly_candidates']>,
    nightly: null as SystemUpdate['nightly'],
  };
  /** v0.1.36 (F-6): `__mock/health` ghi đè trạng thái `/system/health` (mặc định khoẻ; `__mock/reset` khôi phục). */
  let healthOverride: MockHealthOverride = {};
  /** v0.1.46 (F-21): `__mock/access` ghi đè `/system/access` (reset tạo lại state ⇒ khôi phục mặc định). */
  let accessOverride: MockAccessOverride = {};
  const accessView = (isOwner: boolean): AccessInfo => {
    const publicUrl = (accessOverride.public_url ?? 'https://localhost:8443').replace(/\/+$/, '');
    return {
      public_url: publicUrl, login_url: `${publicUrl}/login`, public_url_local: isLocalHost(hostOfUrl(publicUrl)),
      mode: accessOverride.mode ?? 'local', bind_addr: accessOverride.bind_addr === undefined ? '127.0.0.1' : accessOverride.bind_addr,
      site_address: accessOverride.site_address ?? null, checked_at: accessOverride.checked_at ?? null, can_manage: isOwner,
    };
  };
  const phase2 = createPhase2({
    fresh: opts.setup === 'fresh',
    simulate: opts.simulate ?? process.env.MOCK_SIMULATE !== '0',
    emit: broadcast,
  });
  /** Dùng chung với `/audit` VÀ `/audit-log` (`system.handle`) — khai báo trước `phase3` để truyền tham chiếu. */
  const audit: AuditRow[] = [];
  seedAuditLog(audit, opts.setup === 'fresh');

  /** Giai đoạn 3: mỗi cụm màn một mock riêng (test/mock-p3-*.ts), hỏi lần lượt sau phase 2. */
  const p3Core = createP3Core({ fresh: opts.setup === 'fresh', emit: broadcast });
  const p4Agents = createP4Agents({ fresh: opts.setup === 'fresh', emit: broadcast, getChannels: phase2.hooks.channels });
  // v0.1.35: gán BOT kiểm agent có thật ở Danh tính Agent — đọc CHUNG mảng của p4Agents (không copy).
  const agentList = () => p4Agents.hooks.list() as AgentIdentity[];
  const p3Relations = createP3Relations({
    fresh: opts.setup === 'fresh', emit: broadcast,
    findAgent: (id) => agentList().find((a) => a.id === id),
  });
  // Giao việc / gán người xử lý kiểm người dùng đang hoạt động (như API: UUID lạ hoặc bị khoá → 404).
  const findUser = (id: string) => users.find((u) => u.id === id && !u.inactive);
  // v0.1.50 (F-81, QD-18): Gen nhớ (/gen/memory) + đề xuất Ghi nhớ / Ghi vào Kho dữ liệu (kịch bản + xác nhận cho mock-gen). Lời gọi ghi
  // Kho duy nhất đi qua mock-p4-mcp (`khoWrite` = POST /hub/kho/write), khai báo bên dưới — gọi lúc chạy.
  const genV0150 = createGenV0150({
    kho: { write: (req: KhoWriteReq) => (mcp.hooks.khoWrite as (r: KhoWriteReq) => ReturnType<Parameters<typeof createGenV0150>[0]['kho']['write']>)(req) },
  });
  // v0.1.21 Gen: kịch bản cố định (test/mock-gen.ts); `features.gen` của /auth/me đọc cờ ở đây.
  const genMock = createGen({
    extra: genV0150.extra,
    emit: broadcast,
    // v0.1.47 (F-79): xác nhận đề xuất gửi Facebook tạo việc `write` ở mock-social (khai báo bên dưới — gọi lúc chạy).
    social: { writeContext: () => social.writeContext(), createWrite: (req) => social.createWrite(req, 'gen') },
    // v0.1.43 (F-24): xác nhận nháp tin của Gen tạo nháp thật ở Bàn làm việc.
    pushDraft: p3Core.hooks.push as (d: unknown) => unknown,
    notifyOwners: (kind, title, body, link) => {
      for (const u of users.filter((x) => x.role.code === 'owner')) notify(u.id, kind, title, body, link);
    },
  });
  const gen = { enabled: () => genMock.hooks.settings().enabled };
  // v0.1.29 — Tài khoản mạng xã hội (chỉ Owner, chỉ đọc).
  const social = createSocial({ fresh: opts.setup === 'fresh', emit: broadcast });
  /** v0.1.39: đọc danh sách tài khoản qua chính route `GET /social/accounts` của mock-social (không chép state). */
  const socialAccounts = (): SocialAccount[] => {
    let out: { items?: SocialAccount[] } | null = null;
    social.handle({
      method: 'GET', path: '/social/accounts', url: new URL('http://mock.local/api/v1/social/accounts'), body: {}, perms: {},
      reply: (_status, b) => {
        out = b as { items?: SocialAccount[] };
        return true;
      },
      problem: () => true, text: () => true, needPin: () => false, userLabel: 'mock', owner: true, role: 'owner',
    });
    return (out as { items?: SocialAccount[] } | null)?.items ?? [];
  };
  // MCP Hub (PLAN 4.3): tool ghi tạo bản nháp qua p3Core.hooks.push — cùng cơ chế create_draft dùng chung.
  const mcp = createP4Mcp({
    fresh: opts.setup === 'fresh', emit: broadcast,
    getAgents: p4Agents.hooks.list as () => AgentIdentity[],
    pushDraft: p3Core.hooks.push as (d: Record<string, unknown>) => unknown,
    // v0.1.50 (F-81): lần ghi Kho thật đầu tiên ⇒ dòng 9 "Gen ghi Kho" ở Việc Sếp cần làm đạt (gắn muộn: bossChecks tạo sau).
    onKhoWritten: () => recordKhoWrite(),
  });
  const hubLinkOf = mcp.hooks.hubLink as () => HubLink;
  // v0.1.44 (F-8c) — Kết nối › Telegram; Gửi thử ghi vào boss_checks (gắn sau khi tạo bossChecks bên dưới).
  let recordKhoWrite: () => void = () => {};
  let recordTelegram: (o: TelegramOutcome) => BossCheck = () => {
    throw new Error('bossChecks chưa sẵn sàng');
  };
  let forgetTelegram: () => void = () => {};
  const telegram = createTelegram({
    fresh: opts.setup === 'fresh', emit: broadcast, record: (o) => recordTelegram(o), forget: () => forgetTelegram(),
  });
  const bossChecks = createBossChecks({
    fresh: opts.setup === 'fresh', emit: broadcast,
    hubLink: hubLinkOf,
    hubTest: mcp.hooks.hubTest as () => { ok: boolean; error: string | null; error_code: string | null },
    socialAccounts,
    cliProfiles: phase2.hooks.cliProfiles,
    activateCli: phase2.hooks.activateCli,
    providers: phase2.hooks.providers,
    onCliLogin: phase2.hooks.onCliLogin as (fn: (kind: string, ok: boolean, email: string | null) => void) => void,
    telegramTest: telegram.runTest,
  });
  recordKhoWrite = bossChecks.hooks.recordKhoWrite as () => void;
  recordTelegram = bossChecks.hooks.recordTelegram as (o: TelegramOutcome) => BossCheck;
  forgetTelegram = bossChecks.hooks.forgetTelegram as () => void;
  // v0.1.59: tạo trước `phase3` để mock Mặt tiền đọc được gợi ý ghép (match-1…) — cùng một đối tượng với `phase3.market`.
  const p3Market = createP3Market({ fresh: opts.setup === 'fresh', emit: broadcast, pushDraft: p3Core.hooks.push as (d: unknown) => unknown, findUser });
  const phase3 = {
    // v0.1.55 (G5) + v0.1.59 (G1): Mặt tiền Owner (/owner/*, chỉ đọc, chỉ Owner) — tiến độ lấy từ "Việc Sếp cần làm"; nháp chờ
    // duyệt lấy từ mock core (duyệt xong thì biến khỏi bản tin / dòng tin / số đếm); gợi ý ghép lấy từ mock market. Đứng TRƯỚC `gen`
    // vì còn trả lời `POST /gen/proposals/{prop-…}/confirm|cancel` của chính nó (mock-gen trả 404 cho mọi đường `/gen/*` lạ).
    owner: createOwner({
      fresh: opts.setup === 'fresh',
      emit: broadcast,
      boss: bossChecks.hooks.overview as never,
      ownerData: opts.ownerData,
      drafts: () => (p3Core.hooks.drafts as () => DraftDetail[])(),
      pushDraft: (d) => (p3Core.hooks.push as (d: DraftDetail) => unknown)(d),
      matches: () => (p3Market.hooks.matches as () => Match[])(),
    }),
    // v0.1.50: `/gen/memory*` TRƯỚC `gen` — mock-gen trả 404 cho mọi đường `/gen/*` lạ.
    genMemory: genV0150,
    // v0.1.54: Gen hướng dẫn (/gen/coach/*) TRƯỚC `gen` — mock-gen trả 404 cho mọi đường `/gen/*` lạ. Trạng thái ở bộ nhớ module (mock-gen-coach.ts).
    genCoach: createGenCoach({ boss: bossChecks.hooks.overview as NonNullable<GenCoachOptions['boss']>, genEnabled: gen.enabled, notifyOwners: (k, t, b, l) => { for (const u of users.filter((x) => x.role.code === 'owner')) notify(u.id, k, t, b, l); } }),
    gen: genMock,
    social,
    // v0.1.39 (F-74) — "Việc Sếp cần làm" (chỉ Owner; PIN cho hub/agy_switch). v0.1.44: dòng 6 Telegram.
    bossChecks,
    // v0.1.44 (F-8c) — Kết nối › Telegram ("Báo động & bản tin"), chỉ Owner; PIN cho Lưu/Tắt.
    telegram,
    // v0.1.44 (F-4b) — Gói chẩn đoán (chỉ Owner, PIN) + POST /client-errors (gọi trước cổng đăng nhập).
    diagnostics: createDiagnostics({ fresh: opts.setup === 'fresh', emit: broadcast }),
    // v0.1.55 (G1): "Chế độ tiêu chuẩn" / "Về mặc định" (/defaults*, chỉ Owner).
    defaults: createDefaults({ fresh: opts.setup === 'fresh', emit: broadcast }),
    // v0.1.72: Bot tài khoản riêng — kênh quản trị, Chế độ hỗ trợ nhóm, sổ tin bot tự gửi (/owner-channel, /auto-sends, /groups/{id}/support).
    ownerChannel: createOwnerChannel({ fresh: opts.setup === 'fresh', emit: broadcast }),
    // agents TRƯỚC core: `GET /agents/decisions` cần trả dữ liệu thật ("agent đã nói gì") — core.handle() có
    // một stub rỗng cho cùng đường (chưa màn nào dùng tới trước giai đoạn 4) nên phải chặn trước nó.
    agents: p4Agents,
    // people trước core: `GET /explain/review/{id}` cần gác cổng riêng theo Q4 (Auditor không vào được chuỗi
    // chứng cứ) — core.handle() nuốt mọi `/explain/{kind}/{id}` không phân biệt kind nên phải chặn trước nó.
    people: createP3People({ fresh: opts.setup === 'fresh', emit: broadcast }),
    core: p3Core,
    queue: createP3Queue({
      fresh: opts.setup === 'fresh', emit: broadcast, findUser,
      // v0.1.55 (G4): Bật Jev 1 chạm ⇒ nguồn Jev (system_one) hiện trong `/providers` như API thật.
      onJevEnabled: (j) => {
        const list = phase2.hooks.providers() as unknown as Array<Record<string, unknown>>;
        if (list.some((x) => x.kind === 'system_one')) return;
        list.push({
          id: j.provider_id, kind: 'system_one', name: 'Jev', endpoint: j.endpoint, failover_rank: list.length + 1,
          enabled: true, auth_state: 'ok',
          keys: [{ id: `${j.provider_id}-k1`, label: 'JEV-KEY-01', last4: j.key_tail, enabled: true, cooldown_until: null, quota_left_pct: null }],
          models: [{ id: `${j.provider_id}-m1`, model_name: j.model, daily_quota: null, used_today: 0 }],
        });
      },
    }),
    relations: p3Relations,
    graph: createP3Graph({ fresh: opts.setup === 'fresh', emit: broadcast }),
    // market "Giới thiệu hai bên" tạo bản nháp thật qua core.hooks.push — cùng cơ chế create_draft dùng chung ở backend.
    market: p3Market,
    // api & model (PLAN 4.2): bindings cần biết danh sách agent (p4Agents) + model theo provider (phase2 dùng chung).
    api: createP4Api({
      fresh: opts.setup === 'fresh', emit: broadcast,
      getAgents: p4Agents.hooks.list as () => AgentIdentity[],
      getProviders: phase2.hooks.providers as Parameters<typeof createP4Api>[0]['getProviders'],
    }),
    mcp,
    // Plugin & Tiện ích (PLAN 4.4).
    plugins: createP4Plugins({ fresh: opts.setup === 'fresh', emit: broadcast }),
    // Điều khiển hệ thống — Bộ não AI đã có mock đủ ở phase2/api; đây chỉ Quyền hạn/Nhật ký/Dữ liệu (PLAN 4.5/4.6).
    system: createP4System({
      fresh: opts.setup === 'fresh', emit: broadcast,
      getGroups: phase2.hooks.groups as Parameters<typeof createP4System>[0]['getGroups'],
      matrix: MATRIX,
      roleOrder: ROLE_ORDER,
      auditLog: audit,
      getPersons: p3Relations.hooks.people as Parameters<typeof createP4System>[0]['getPersons'],
    }),
  };
  // v0.1.23 (B6) — chuông thông báo, như gh/notifications.py: mỗi người một danh sách, WS chỉ tới người nhận.
  interface MockNotification { id: string; kind: string; title: string; body: string; link: string | null; created_at: string; read: boolean }
  const notifications = new Map<string, MockNotification[]>();
  const notifsOf = (userId: string): MockNotification[] => {
    let list = notifications.get(userId);
    if (!list) {
      list = [];
      notifications.set(userId, list);
      // Dữ liệu mẫu cho Owner của bản đã thiết lập: một chưa đọc, một đã đọc.
      const u = users.find((x) => x.id === userId);
      if (u?.role.code === 'owner' && opts.setup !== 'fresh') {
        const ago = (m: number) => new Date(Date.now() - m * 60_000).toISOString();
        list.push(
          { id: randomUUID(), kind: 'backup.done', title: 'Sao lưu đã xong', body: 'Bản sao lưu hằng ngày đã được lưu.', link: '/system?tab=storage', created_at: ago(35), read: false },
          { id: randomUUID(), kind: 'user.reactivated', title: 'Chào mừng tới Gen-Harness', body: 'Thiết lập đã xong — thông báo mới sẽ hiện ở chuông này.', link: null, created_at: ago(60 * 26), read: true },
        );
      }
    }
    return list;
  };
  /** Như `GET /system/health` (gh/system_api): mặc định khoẻ; sự cố suy từ trạng thái + `issues` của hook. */
  const healthView = (isOwner = true): SystemHealth => {
    const nowMs = Date.now();
    const now = new Date(nowMs).toISOString();
    const o = healthOverride;
    const worker: SystemHealth['worker'] = { state: 'ok', alive: true, last_seen_at: new Date(nowMs - 40_000).toISOString(), silent_minutes: null, ...o.worker };
    const latest = phase3.system.latestBackupAt();
    const configured = phase3.system.backupConfigured();
    const age = latest ? Math.max(0, Math.round(((nowMs - new Date(latest).getTime()) / 3_600_000) * 10) / 10) : null;
    const backup: SystemHealth['backup'] = {
      configured, latest_at: latest, age_hours: age, stale: configured && age != null && age > 36,
      frequency: configured ? 'daily' : null, stale_after: configured ? '36 giờ' : null, ...o.backup,
    };
    // Như gh/health._update_failed_recent: lỗi chỉ còn là sự cố trong 24 giờ sau finished_at.
    const recentFail = sysUpdate.state === 'failed' && !!sysUpdate.finished_at && nowMs - Date.parse(sysUpdate.finished_at) < 24 * 3_600_000;
    const update: SystemHealth['update'] = { state: sysUpdate.state, failed: recentFail, blocked_version: null, finished_at: sysUpdate.finished_at, ...o.update };
    const disk: SystemHealth['disk'] = { state: 'ok', free_bytes: 42 * 1024 ** 3, min_bytes: 5 * 1024 ** 3, checked_at: new Date(nowMs - 5 * 60_000).toISOString(), ...o.disk };
    const derived: Array<Partial<HealthIssue>> = [];
    const gb = (n: number | null) => (n == null ? '?' : (n / 1024 ** 3).toFixed(1).replace('.', ','));
    if (worker.state === 'silent') derived.push({ kind: 'worker.silent', title: `Bộ xử lý nền đã ngừng ${worker.silent_minutes ?? WORKER_SILENT_MIN} phút` });
    if (backup.stale) derived.push({ kind: 'backup.stale', title: `Đã hơn ${backup.stale_after ?? '36 giờ'} chưa có bản sao lưu mới` });
    // Như gh/health._eval_update: GH-E94B dừng gọn (`interrupted`) ⇒ 'warn' "bị dừng giữa chừng", không đỏ.
    if (update.failed && update.interrupted) {
      derived.push({
        kind: 'update.failed', severity: 'warn', title: `Cập nhật lên ${sysUpdate.to ?? 'bản mới'} bị dừng giữa chừng`,
        body: update.interrupted === 'resume'
          ? 'Máy tắt giữa lúc cập nhật — cần chạy lại để hoàn tất. Bấm để thử lại ngay.'
          : 'Bản đang dùng vẫn chạy bình thường — lịch đêm sẽ tự thử lại, hoặc bấm để thử lại ngay.',
      });
    } else if (update.failed) derived.push({ kind: 'update.failed', title: `Cập nhật lên ${sysUpdate.to ?? 'bản mới'} chưa thành công` });
    if (disk.state === 'low') derived.push({ kind: 'disk.low', body: `Còn ${gb(disk.free_bytes)} GB trống, cần tối thiểu ${gb(disk.min_bytes)} GB — cập nhật tự động đang tạm dừng.` });
    if (o.autostart?.state === 'warn') derived.push({ kind: 'host.autostart' });
    // v0.1.53 (F-99): như gh/health._eval_nightly — tiêu đề theo `reason` (api cũ: theo số ngày); thân ghép từ chuỗi cố
    // định (+ bước linger khi 'no'); Sếp đã tắt mà lịch vẫn bật ⇒ thân hai lựa chọn.
    const nightly = o.nightly ?? undefined;
    if (nightly?.state === 'warn' && nightly.reason === 'opted_out_running') {
      derived.push({
        kind: 'host.nightly',
        title: 'Sếp đã tắt tự cập nhật đêm nhưng lịch vẫn bật',
        body: 'Máy chủ vẫn tự lên bản mới khoảng 03:00. Trên máy chủ chạy: genh auto-update status · Muốn tắt hẳn: genh auto-update disable · Muốn giữ tự cập nhật: genh auto-update enable',
      });
    } else if (nightly?.state === 'warn') {
      const days = nightly.days_since;
      const disabled = nightly.reason === 'disabled' || (nightly.reason == null && (days == null || days < 0));
      derived.push({
        kind: 'host.nightly',
        title: disabled ? 'Lịch tự cập nhật đêm đang tắt' : days == null || days < 2 ? 'Lịch tự cập nhật đêm đã hơn 1 ngày chưa chạy' : `Lịch tự cập nhật đêm chưa chạy ${days} ngày`,
        body: [
          'Máy chủ không tự lên bản mới. Trên máy chủ chạy: genh auto-update status',
          ...(nightly.linger === 'no' ? ['Tiến trình nền chỉ chạy khi có người đăng nhập — chạy một lần: sudo loginctl enable-linger $USER'] : []),
          'Sau đó bật lại lịch: genh auto-update enable',
        ].join(' · '),
      });
    }
    // v0.1.40 (F-12): như gh/health._eval_offsite — chỉ khi đã chọn nơi lưu; > 30 ngày ⇒ bad.
    const offsite = o.offsite === null ? undefined : (o.offsite ?? (phase3.system.offsiteHealth() as SystemHealth['offsite']));
    // Chưa chọn nơi lưu cũng mở offsite.stale (API thật: tổ chức tạo quá 7 ngày — mock "đã thiết lập" coi như đủ cũ).
    // Nhãn nút theo vai trò như gh/health.NON_OWNER_ACTIONS: không phải Owner thì không hứa nút "Chọn nơi lưu".
    const staleAction = isOwner ? 'Chọn nơi lưu / sao lưu ngay' : 'Xem bản sao ngoài máy';
    if (offsite?.configured && (offsite.state === 'failed' || offsite.state === 'not_mounted')) derived.push({ kind: 'offsite.failed' });
    else if (offsite && !offsite.configured && opts.setup !== 'fresh') {
      derived.push({
        kind: 'offsite.stale', severity: 'warn', title: 'Chưa có bản sao ngoài máy', action: staleAction,
        // Như gh/health.NON_OWNER_BODIES: không phải Owner thì nhờ Owner (không bảo bấm nút chỉ Owner có).
        body: isOwner
          ? "Hỏng ổ đĩa là mất hết dữ liệu. Cắm ổ USB hoặc chọn thư mục NAS rồi bấm 'Chọn nơi lưu bản sao ngoài máy'"
          : 'Hỏng ổ đĩa là mất hết dữ liệu. Nhờ Owner cắm ổ USB/NAS và chọn nơi lưu bản sao ngoài máy',
      });
    } else if (offsite?.configured && offsite.stale) {
      const days = offsite.age_days != null ? Math.floor(offsite.age_days) : null;
      derived.push(
        days == null
          ? { kind: 'offsite.stale', severity: 'warn', title: 'Chưa có bản sao ngoài máy', action: staleAction,
              body: "Đã chọn nơi lưu nhưng chưa có lần nào thành công — cắm ổ rồi bấm 'Sao lưu ra ổ ngoài ngay'" }
          : { kind: 'offsite.stale', severity: days > 30 ? 'bad' : 'warn', title: `Bản sao ngoài máy đã cũ ${days} ngày`, action: staleAction },
      );
    }
    const issues: HealthIssue[] = [];
    for (const i of [...(o.issues ?? []), ...derived].map((x) => healthIssue(x, now))) if (!issues.some((y) => y.key === i.key)) issues.push(i);
    // v0.1.46 (F-21): như gh/health.NON_OWNER_ACTIONS — người không phải Owner không có lệnh `genh remote` ⇒ "Nhờ Owner xử lý".
    if (!isOwner)
      for (const i of issues)
        if (i.kind === 'network.open_lan') {
          i.action = 'Nhờ Owner xử lý';
          i.body = 'Cổng Console đang mở cho cả mạng — nhờ Owner chọn cách truy cập từ xa.'; // = gh/health.NON_OWNER_BODIES
        }
    const overall = issues.some((i) => i.severity === 'bad') ? 'bad' : issues.length ? 'warn' : 'ok';
    return {
      checked_at: now, overall, worker,
      browser: { state: 'ok', last_heartbeat_at: new Date(nowMs - 20_000).toISOString() },
      queues: [{ stream: 'gh:raw', dlq: 0 }, { stream: 'gh:refinery', dlq: 0 }],
      crons: [
        { name: 'backup_scheduled', last_at: latest, ok: latest ? true : null },
        { name: 'task_reminders', last_at: new Date(nowMs - 60_000).toISOString(), ok: true },
        { name: 'health_watch', last_at: new Date(nowMs - 60_000).toISOString(), ok: true },
      ],
      backup, update, disk, issues,
      ...(o.autostart ? { autostart: o.autostart } : {}),
      ...(nightly ? { nightly } : {}),
      ...(offsite ? { offsite } : {}),
    };
  };
  const notify = (userId: string, kind: string, title: string, body = '', link: string | null = null) => {
    const item: MockNotification = { id: randomUUID(), kind, title, body, link, created_at: new Date().toISOString(), read: false };
    notifsOf(userId).unshift(item);
    broadcast('notification.new', item, userId);
    return item;
  };
  const record = (user: User | undefined, action: string, result = 'ok', detail: unknown = null) =>
    audit.unshift({
      id: randomUUID(),
      at: new Date().toISOString(),
      actor_type: 'user',
      actor_id: user?.id ?? null,
      actor_label: user?.display_name ?? null,
      action,
      target_type: 'user',
      target_id: user?.id ?? null,
      target_label: user?.email ?? null,
      autonomy_level: null,
      result,
      detail,
    });
  const users: User[] = [];
  const addOwner = (email = MOCK_OWNER.email, password = MOCK_OWNER.password, pin = MOCK_OWNER.pin, name = 'Anh Nguyễn Văn A (Chủ)') =>
    users.push({
      // UUID cố định cho Owner seed (test/mock-ids.ts) — e2e so khớp id lấy từ /pickers/users.
      id: users.some((u) => u.id === USER_IDS.owner) ? randomUUID() : USER_IDS.owner,
      email,
      password,
      pin,
      display_name: name,
      role: { code: 'owner', name: 'Owner — Sếp' },
      hidden: hiddenScreens('owner'),
      mustChange: opts.mustChangePassword ?? false,
      createdAt: '2026-05-04T02:15:00.000Z',
    });

  const setup: SetupState & { org: { name: string; timezone: string; currency: string }; addressing: { self: string; bot_calls_me: string } } = {
    finished: opts.setup !== 'fresh',
    current_step: opts.setup === 'fresh' ? 1 : 12,
    steps: STEP_DEFS.map(([key, title, required, available], i) => ({
      n: i + 1,
      key,
      title,
      required,
      available,
      status: opts.setup === 'fresh' ? (i === 0 ? 'doing' : 'todo') : 'done',
    })),
    org: { name: 'Genesis Trading', timezone: 'Asia/Ho_Chi_Minh', currency: 'VND' },
    addressing: { self: 'Anh', bot_calls_me: 'Sếp' },
  };
  // Agent của bước 8 — bước 9 đặt mức tự trị cho ĐÚNG agent này (như `_setup_agent` của API thật).
  let setupAgent: { id: string; name: string; autonomy_level: number } | null =
    opts.setup === 'fresh' ? null : { id: AGENT_IDS.tls, name: 'Trợ lý thương mại', autonomy_level: 4 };
  if (opts.startAtStep && opts.startAtStep > 1) {
    const n = Math.min(12, Math.max(2, opts.startAtStep));
    setup.finished = false;
    setup.current_step = n;
    setup.steps.forEach((s) => {
      s.status = s.n < n ? 'done' : s.n === n ? 'doing' : 'todo';
    });
  }
  if (opts.setup !== 'fresh' || opts.startAtStep) {
    addOwner();
  }
  if (opts.setup !== 'fresh') {
    users.push({
      id: USER_IDS.lan,
      email: 'operator@genesis.local',
      password: MOCK_OWNER.password,
      pin: '135790',
      display_name: 'Chị Lan Phạm',
      role: { code: 'operator', name: 'Operator · vận hành' },
      hidden: hiddenScreens('operator'),
    });
    users.push({
      id: USER_IDS.minh,
      email: 'auditor@genesis.local',
      password: MOCK_OWNER.password,
      pin: '975310',
      display_name: 'Anh Minh Kiểm',
      role: { code: 'auditor', name: 'Auditor · kiểm toán' },
      hidden: hiddenScreens('auditor'),
    });
    // Q4 (docs/PLAN.md): cần một tài khoản Manager thật để e2e xác nhận nhánh "Manager không thấy" của
    // Đánh giá con người (trước đây chỉ owner/operator/auditor được seed, chưa cụm nào cần Manager tới giờ).
    users.push({
      id: USER_IDS.hong,
      email: 'manager@genesis.local',
      password: MOCK_OWNER.password,
      pin: '864202',
      display_name: 'Chị Hồng Quản',
      role: { code: 'manager', name: 'Manager · quản lý team' },
      hidden: hiddenScreens('manager'),
    });
  }

  interface MockSession { userId: string; pinUntil: number | null; createdAt: string; lastSeenAt: string; ip: string | null; ua: string | null }
  const sessions = new Map<string, MockSession>();
  if (opts.setup !== 'fresh' && users[0]) {
    // Owner có sẵn 2 phiên ở thiết bị khác — màn "Tài khoản của tôi" có gì để xem / đăng xuất.
    const ago = (m: number) => new Date(Date.now() - m * 60_000).toISOString();
    sessions.set(randomUUID(), { userId: users[0].id, pinUntil: null, createdAt: ago(3 * 24 * 60), lastSeenAt: ago(95), ip: '113.161.42.7', ua: 'Mozilla/5.0 (iPhone; CPU iPhone OS 18_5 like Mac OS X) AppleWebKit/605.1.15 Mobile Safari/604.1' });
    sessions.set(randomUUID(), { userId: users[0].id, pinUntil: null, createdAt: ago(9 * 24 * 60), lastSeenAt: ago(2 * 24 * 60), ip: '14.232.18.90', ua: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/140.0 Safari/537.36' });
  }
  const pinFails = new Map<string, { count: number; lockedUntil: number | null }>();

  const stateView = (): SetupState => ({ finished: setup.finished, current_step: setup.current_step, steps: setup.steps });
  const advance = (n: number, status: 'done' | 'skipped') => {
    const s = setup.steps[n - 1];
    s.status = status;
    const next = setup.steps.find((x) => x.status === 'todo' || x.status === 'doing');
    setup.current_step = next ? next.n : 12;
    setup.steps.forEach((x) => {
      if (x.status === 'doing') x.status = 'todo';
    });
    const cur = setup.steps[setup.current_step - 1];
    if (cur.status === 'todo') cur.status = 'doing';
  };

  function sessionUser(req: IncomingMessage) {
    const sid = parseCookies(req).gh_session;
    const session = sid ? sessions.get(sid) : undefined;
    const user = session ? users.find((u) => u.id === session.userId) : undefined;
    return user && session ? { user, session } : null;
  }

  function parseCookies(req: IncomingMessage): Record<string, string> {
    const out: Record<string, string> = {};
    for (const part of (req.headers.cookie ?? '').split(';')) {
      const [k, ...v] = part.trim().split('=');
      if (k) out[k] = decodeURIComponent(v.join('='));
    }
    return out;
  }

  async function readBody(req: IncomingMessage): Promise<Record<string, unknown>> {
    const chunks: Buffer[] = [];
    for await (const c of req) chunks.push(c as Buffer);
    const text = Buffer.concat(chunks).toString('utf8');
    if (!text) return {};
    try {
      return JSON.parse(text) as Record<string, unknown>;
    } catch {
      return {};
    }
  }

  /** v0.1.44 (F-4b): "Mã yêu cầu" của từng phản hồi (header X-Request-ID + `request_id` trong mọi problem+json). */
  const requestIds = new WeakMap<ServerResponse, string>();
  const requestIdOf = (res: ServerResponse) => requestIds.get(res) ?? null;

  function send(res: ServerResponse, status: number, body?: unknown, cookies: string[] = []) {
    const headers: Record<string, string | string[]> = { 'Cache-Control': 'no-store' };
    const rid = requestIdOf(res);
    if (rid) headers['X-Request-ID'] = rid;
    if (cookies.length) headers['Set-Cookie'] = cookies;
    if (status === 204 || body === undefined) {
      res.writeHead(status, headers);
      res.end();
      return;
    }
    headers['Content-Type'] = status >= 400 ? 'application/problem+json' : 'application/json';
    res.writeHead(status, headers);
    res.end(JSON.stringify(body));
  }
  const problem = (res: ServerResponse, status: number, code: string, title: string, extra: Record<string, unknown> = {}) =>
    send(res, status, { type: `https://gen-harness.local/errors/${code.toLowerCase()}`, title, status, code, ...extra, request_id: requestIdOf(res) ?? undefined });

  function me(user: User, pinUntil: number | null) {
    return {
      id: user.id,
      email: user.email,
      display_name: user.display_name,
      role: user.role,
      org: { id: 'org-1', ...setup.org },
      addressing: setup.addressing,
      pin_verified_until: pinUntil && pinUntil > Date.now() ? new Date(pinUntil).toISOString() : null,
      permissions: permissionsOf(user.role.code),
      must_change_password: user.mustChange ?? false,
      // v0.1.21: Gen (cờ gen.enabled) — như gh/gen/store.py: mặc định chỉ Owner.
      features: { gen: user.role.code === 'owner' && gen.enabled() },
    };
  }

  type AcctResult = { status: number; body?: unknown; code?: string; title?: string; extra?: Record<string, unknown> };
  const invalid = (errors: Record<string, string>): AcctResult => ({ status: 422, code: 'VALIDATION', title: 'Dữ liệu chưa hợp lệ', extra: { errors } });
  const WRONG_PW = 'Mật khẩu hiện tại không đúng';
  function accountView(user: User, sid: string) {
    const mine = [...sessions.entries()].filter(([, s]) => s.userId === user.id)
      .map(([id, s]) => ({ id, created_at: s.createdAt, last_seen_at: s.lastSeenAt, ip: s.ip, user_agent: s.ua,
        expires_at: new Date(Date.now() + 7 * 24 * 3600_000).toISOString(), current: id === sid }))
      .sort((a, b) => Number(b.current) - Number(a.current) || b.last_seen_at.localeCompare(a.last_seen_at));
    return { display_name: user.display_name, email: user.email, role: user.role, created_at: user.createdAt ?? '2026-05-04T02:15:00.000Z',
      must_change_password: user.mustChange ?? false, has_pin: user.role.code === 'owner', sessions: mine };
  }
  /** Như gh/auth/account.py (v0.1.19) — "Tài khoản của tôi". */
  function accountRoute(user: User, sid: string, method: string, path: string, body: Record<string, unknown>): AcctResult {
    const revokeOthers = () => {
      let n = 0;
      for (const [id, s] of sessions) {
        if (s.userId === user.id && id !== sid) {
          sessions.delete(id);
          n += 1;
        }
      }
      return n;
    };
    if (path === '/account' && method === 'GET') return { status: 200, body: accountView(user, sid) };
    if (path === '/account' && method === 'PATCH') {
      const errors: Record<string, string> = {};
      let name: string | null = null;
      let email: string | null = null;
      if (typeof body.display_name === 'string') {
        name = body.display_name.split(/\s+/).filter(Boolean).join(' ');
        if (!name) errors.display_name = 'Tên hiển thị không được để trống';
        else if (name.length > 100) errors.display_name = 'Tên hiển thị tối đa 100 ký tự';
      }
      if (typeof body.email === 'string') {
        email = body.email.trim().toLowerCase();
        if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email)) errors.email = 'Email chưa đúng định dạng';
        else if (email === user.email) email = null;
        else if (users.some((u) => u.id !== user.id && u.email === email)) errors.email = 'Email này đã có tài khoản khác dùng';
        else if (!body.current_password) errors.current_password = 'Nhập mật khẩu hiện tại để đổi email';
      }
      if (Object.keys(errors).length) return invalid(errors);
      if (email && body.current_password !== user.password) {
        record(user, 'account.password_check_failed', 'failed');
        return invalid({ current_password: WRONG_PW });
      }
      if (name) user.display_name = name;
      if (email) user.email = email;
      record(user, 'account.profile_updated');
      return { status: 200, body: accountView(user, sid) };
    }
    if (path === '/account/password' && method === 'POST') {
      const next = String(body.new_password ?? '');
      if (next.length < 12) return invalid({ new_password: 'Mật khẩu mới cần ít nhất 12 ký tự' });
      if (body.current_password !== user.password) {
        record(user, 'account.password_check_failed', 'failed');
        return invalid({ current_password: WRONG_PW });
      }
      if (next === user.password) return invalid({ new_password: 'Mật khẩu mới phải khác mật khẩu hiện tại' });
      user.password = next;
      user.mustChange = false;
      const n = revokeOthers();
      record(user, 'account.password_changed');
      return { status: 200, body: { sessions_revoked: n } };
    }
    if (path === '/account/pin' && method === 'POST') {
      if (user.role.code !== 'owner') return { status: 409, code: 'NO_PIN', title: 'Tài khoản này không dùng mã PIN' };
      const pin = String(body.new_pin ?? '');
      if (!/^\d{6}$/.test(pin)) return invalid({ new_pin: 'PIN gồm đúng 6 chữ số' });
      if (body.new_pin_confirm !== pin) return invalid({ new_pin_confirm: 'Hai lần nhập mã PIN chưa khớp' });
      if (body.current_password !== user.password) {
        record(user, 'account.password_check_failed', 'failed');
        return invalid({ current_password: WRONG_PW });
      }
      user.pin = pin;
      record(user, 'account.pin_changed');
      return { status: 204 };
    }
    if (path === '/account/sessions/revoke-others' && method === 'POST') {
      const n = revokeOthers();
      record(user, 'account.sessions_revoked');
      return { status: 200, body: { sessions_revoked: n } };
    }
    const one = /^\/account\/sessions\/([^/]+)$/.exec(path);
    if (one && method === 'DELETE') {
      const id = decodeURIComponent(one[1]);
      if (id === sid) return { status: 409, code: 'CURRENT_SESSION', title: 'Đây là phiên đang dùng — hãy bấm Đăng xuất' };
      const s = sessions.get(id);
      if (!s || s.userId !== user.id) return { status: 404, code: 'NOT_FOUND', title: 'Phiên đăng nhập không tồn tại' };
      sessions.delete(id);
      record(user, 'account.session_revoked');
      return { status: 204 };
    }
    return { status: 404, code: 'NOT_FOUND', title: 'Không tồn tại' };
  }

  const middleware = async (req: IncomingMessage, res: ServerResponse, next: Next) => {
    const url = new URL(req.url ?? '/', 'http://mock.local');
    if (!url.pathname.startsWith('/api/v1/')) return next();
    if (latency) await new Promise((r) => setTimeout(r, latency));
    const path = url.pathname.slice('/api/v1'.length);
    // v0.1.44 (F-4b): mọi phản hồi có X-Request-ID (nhận của client nếu đúng khuôn, không thì sinh 16 hex) — như api.
    const incoming = String(req.headers['x-request-id'] ?? '');
    const rid = /^[A-Za-z0-9_-]{8,64}$/.test(incoming) ? incoming : randomUUID().replace(/-/g, '').slice(0, 16);
    requestIds.set(res, rid);
    res.setHeader('X-Request-ID', rid);
    // Như proxy Caddy (`?X-Frame-Options DENY` + CSP `frame-ancestors 'none'` khi upstream chưa đặt) và api
    // (gh.middleware.SameOriginFrame: riêng gói mang đi cho khung cùng gốc) — e2e bắt được trang lỗi bị chặn trong khung.
    const framable = path === '/system/offsite/portable';
    res.setHeader('X-Frame-Options', framable ? 'SAMEORIGIN' : 'DENY');
    res.setHeader('Content-Security-Policy', framable ? "default-src 'none'; frame-ancestors 'self'" : "frame-ancestors 'none'");
    const method = (req.method ?? 'GET').toUpperCase();
    const cookies = parseCookies(req);
    const setCookies: string[] = [];

    // CSRF double-submit: issue gh_csrf when missing, check it on writes.
    let csrf = cookies.gh_csrf;
    if (!csrf) {
      csrf = randomUUID();
      setCookies.push(`gh_csrf=${csrf}; Path=/; SameSite=Strict`);
    }
    const reply = (status: number, body?: unknown, extra: string[] = []) => send(res, status, body, [...setCookies, ...extra]);
    if (method !== 'GET' && method !== 'HEAD' && req.headers['x-csrf-token'] !== cookies.gh_csrf) {
      return problem(res, 403, 'CSRF_FAILED', 'CSRF token không khớp');
    }

    const sid = cookies.gh_session;
    const session = sid ? sessions.get(sid) : undefined;
    const user = session ? users.find((u) => u.id === session.userId) : undefined;
    // v0.1.45 (F-20): Hướng dẫn bước 9/10 sau Hoàn tất cần phiên PIN (như API thật).
    const setupNeedPin = () => !session?.pinUntil || session.pinUntil < Date.now();
    const login = (u: User) => {
      const id = randomUUID();
      const now = new Date().toISOString();
      sessions.set(id, { userId: u.id, pinUntil: null, createdAt: now, lastSeenAt: now, ip: '127.0.0.1', ua: String(req.headers['user-agent'] ?? '') || null });
      return `gh_session=${id}; Path=/; HttpOnly; SameSite=Strict`;
    };
    const body = method === 'GET' ? {} : await readBody(req);

    // ── health / setup (no auth) ──
    if (path === '/health') return reply(200, { status: 'ok' });
    if (path === '/ready') return reply(200, { db: 'ok', redis: 'ok', objects: 'skip', bridge: 'down' });
    // v0.1.44 (F-4b): báo lỗi giao diện — không cần đăng nhập, miễn cổng thiết lập (như api).
    if (path === '/client-errors' && method === 'POST') {
      const r = phase3.diagnostics.recordClientError(body);
      if (r.status >= 400) return problem(res, r.status, String(r.body.code), String(r.body.title), r.body);
      return reply(r.status, r.body);
    }
    if (path === '/setup/state' && method === 'GET') return reply(200, stateView());
    if (path.startsWith('/setup/')) {
      if (path === '/setup/rule-presets' && method === 'GET') return reply(200, phase2.rulePresets());
      if (path === '/setup/first-run' && method === 'GET') return reply(200, phase2.firstRunView());
      if (path === '/setup/hard-boundaries' && method === 'GET') return reply(200, MOCK_HARD_BOUNDARIES);
      if (path === '/setup/steps/9' && method === 'GET') return reply(200, { agent: setupAgent });
      if (path === '/setup/follow-up' && method === 'GET') {
        // Như API thật: mọi bước tuỳ chọn 5–11; `done` = đã xong trong trình thiết lập (dữ liệu thật: mock bỏ qua).
        // v0.1.29: bước 4 cũng có ("Chưa có model") — xong theo dữ liệu thật: có nguồn dùng được đang có model.
        const hasModel = phase2.hooks.providers().some((p) => p.enabled && p.kind !== 'system_one' && p.auth_state === 'ok' && p.models.length > 0);
        // v0.1.39 (F-28): 13 Facebook đã từng đăng nhập, 14 Gen-hub đã Kiểm tra xanh ít nhất một lần (không phải bước).
        const fbDone = socialAccounts().some((a) => a.status === 'active' || a.status === 'paused' || a.status === 'needs_login');
        return reply(200, [
          ...setup.steps.filter((x) => !x.required && x.n >= 4 && x.n <= 11)
            .map((x) => ({ n: x.n, key: x.key, title: x.title, status: x.status,
              done: x.n === 4 ? hasModel : x.status === 'done' || (x.n === 11 && phase3.system.backupConfigured()) || (x.n === 7 && phase2.hooks.rulesEnabled()) })),
          { n: 13, key: 'social', title: 'Kết nối Facebook', status: 'todo', done: fbDone },
          { n: 14, key: 'hub', title: 'Nối Gen-hub', status: 'todo', done: !!hubLinkOf().last_ok_at },
        ]);
      }
      // Như API thật: sau Hoàn tất vẫn lưu lại được bước tuỳ chọn 4–11 (trang Hướng dẫn thiết lập), còn lại 409.
      const optionalPut = /^\/setup\/steps\/([4-9]|1[01])$/.test(path) && method === 'PUT';
      if (setup.finished && !optionalPut) return problem(res, 409, 'CONFLICT', 'Thiết lập đã hoàn tất');
      const skip = /^\/setup\/steps\/(\d+)\/skip$/.exec(path);
      if (skip && method === 'POST') {
        const n = Number(skip[1]);
        const step = setup.steps[n - 1];
        if (!step || step.required) return problem(res, 409, 'CONFLICT', 'Bước này bắt buộc');
        if (step.status !== 'done') {
          if (n === 7) phase2.seedDefaultRules();
          if (n === 11 && !phase3.system.backupConfigured()) phase3.system.defaultBackup();
        }
        advance(n, 'skipped');
        return reply(200, stateView());
      }
      const m = /^\/setup\/steps\/(\d+)$/.exec(path);
      if (m && method === 'PUT') {
        const n = Number(m[1]);
        if (n === 1 || n === 2) {
          if (body.token !== MOCK_TOKEN) return problem(res, 403, 'SETUP_TOKEN_INVALID', 'Mã thiết lập không hợp lệ');
        }
        if (n === 1) {
          advance(1, 'done');
          return reply(200, stateView());
        }
        if (n === 2) {
          const errors: Record<string, string> = {};
          if (String(body.password ?? '').length < 12) errors.password = 'Mật khẩu cần ít nhất 12 ký tự.';
          if (!/^\d{6}$/.test(String(body.pin ?? ''))) errors.pin = 'PIN gồm đúng 6 chữ số.';
          if (body.pin !== body.pin_confirm) errors.pin_confirm = 'Hai lần nhập mã PIN chưa khớp.';
          if (users.some((u) => u.email === body.email)) errors.email = 'Email đã được dùng.';
          if (Object.keys(errors).length) return problem(res, 422, 'VALIDATION_ERROR', 'Dữ liệu chưa hợp lệ', { errors });
          addOwner(String(body.email), String(body.password), String(body.pin), String(body.display_name));
          advance(2, 'done');
          return reply(200, stateView(), [login(users[users.length - 1])]);
        }
        if (n === 3) {
          if (!user) return problem(res, 401, 'UNAUTHENTICATED', 'Chưa đăng nhập');
          setup.org = { name: String(body.org_name), timezone: String(body.timezone), currency: String(body.currency) };
          setup.addressing = { self: String(body.self_name), bot_calls_me: String(body.bot_calls_me) };
          advance(3, 'done');
          return reply(200, stateView());
        }
        if ([4, 5, 6, 7, 12].includes(n)) {
          if (!user) return problem(res, 401, 'UNAUTHENTICATED', 'Chưa đăng nhập');
          if (setup.current_step < 4) return problem(res, 409, 'STEP_ORDER', 'Làm các bước trước trước');
          const r = phase2.setupStep(n, body);
          if (!('ok' in r)) return problem(res, r.status, r.code, r.title, r.extra ?? {});
          if (n === 12) {
            // As the API: finishing needs every required step; phase 2 has no 8–9 yet → 409 naming them.
            const missing = setup.steps.filter((x) => x.required && x.n !== 12 && x.status !== 'done').map((x) => x.n);
            if (missing.length && !mockAllowFinish) {
              return problem(res, 409, 'STEP_INCOMPLETE', `Còn bước bắt buộc chưa xong: ${missing.join(', ')}`);
            }
            setup.steps[11].status = 'done';
            setup.finished = true;
            setup.current_step = 12;
          } else advance(n, 'done');
          return reply(200, stateView());
        }
        if (n === 8 || n === 9) {
          if (!user) return problem(res, 401, 'UNAUTHENTICATED', 'Chưa đăng nhập');
          if (n === 8) {
            if (!String(body.name ?? '').trim() || !String(body.role_desc ?? '').trim()) {
              return problem(res, 422, 'VALIDATION_ERROR', 'Dữ liệu chưa hợp lệ', { errors: { name: 'Nhập tên agent' } });
            }
            advance(8, 'done');
            const agent = { id: randomUUID(), name: String(body.name), try_reply: typeof body.try_message === 'string' && body.try_message.trim() ? `Chào Sếp, tôi là ${String(body.name)}.` : null, try_error: null }; // v0.1.55: chỉ trả lời thử khi có try_message (như API)
            setupAgent = { id: agent.id, name: agent.name, autonomy_level: 4 };
            return reply(200, { ...stateView(), agent });
          }
          if (setup.steps[7].status !== 'done') return problem(res, 409, 'STEP_INCOMPLETE', 'Cần hoàn thành bước 8 trước');
          if (!body.ack_boundaries) return problem(res, 422, 'VALIDATION_ERROR', 'Dữ liệu chưa hợp lệ', { errors: { ack_boundaries: 'Cần xác nhận đã đọc ranh giới' } });
          // v0.1.45 (F-20): như API — sau Hoàn tất ĐỔI mức tự trị cần phiên PIN `policy.change` (đang thiết lập thì không);
          // gửi lại đúng mức đang có / null (giữ nguyên) thì không hỏi PIN.
          const cur = setupAgent?.autonomy_level ?? 4;
          const level = typeof body.autonomy_level === 'number' ? body.autonomy_level : cur;
          if (setup.finished && level !== cur && setupNeedPin()) return problem(res, 423, 'PIN_REQUIRED', 'Thao tác này cần nhập mã PIN', { detail: { operation: 'policy.change' } });
          if (setupAgent) setupAgent.autonomy_level = level;
          advance(9, 'done');
          return reply(200, { ...stateView(), hard_boundaries: MOCK_HARD_BOUNDARIES, agent: setupAgent });
        }
        if (n === 10 || n === 11) {
          // Như `_owner_step` thật (khác `_owner_step_after` của bước 8–9): không đòi các bước trước phải xong.
          if (!user) return problem(res, 401, 'UNAUTHENTICATED', 'Chưa đăng nhập');
          if (n === 10) {
            // v0.1.45 (F-20): sau Hoàn tất, mời người (danh sách khác rỗng) cần phiên PIN `user.manage`.
            const invites = Array.isArray(body.invites) ? body.invites : [];
            if (setup.finished && invites.length > 0 && setupNeedPin()) {
              return problem(res, 423, 'PIN_REQUIRED', 'Thao tác này cần nhập mã PIN', { detail: { operation: 'user.manage' } });
            }
            const r = phase3.system.step10(body, new Set(users.map((u) => u.email)));
            if (!r.ok) return problem(res, r.status, r.code, r.title, r.extra ?? {});
            for (const inv of r.value.invited) {
              const roleName = { manager: 'Manager · quản lý team', operator: 'Operator · vận hành', agent_staff: 'Agent nhân viên', auditor: 'Auditor · kiểm toán' }[inv.role] ?? inv.role;
              users.push({ id: inv.id, email: inv.email, password: inv.temp_password, pin: '000000', display_name: inv.display_name, role: { code: inv.role as RoleCode, name: roleName }, hidden: hiddenScreens(inv.role as RoleCode), mustChange: true });
            }
            advance(10, 'done');
            return reply(200, { ...stateView(), invited: r.value.invited });
          }
          const r = phase3.system.step11(body);
          if (!r.ok) return problem(res, r.status, r.code, r.title, r.extra ?? {});
          advance(11, 'done');
          return reply(200, { ...stateView(), backup: r.value.backup });
        }
        return problem(res, 409, 'CONFLICT', 'Bước này làm ở giai đoạn sau');
      }
      return problem(res, 404, 'NOT_FOUND', 'Không tồn tại');
    }

    // ── auth ──
    if (path === '/auth/login' && method === 'POST') {
      const u = users.find((x) => x.email === body.email && x.password === body.password && !x.inactive);
      if (u) u.lastLoginAt = new Date().toISOString();
      if (!u) return problem(res, 401, 'INVALID_CREDENTIALS', 'Email hoặc mật khẩu không đúng');
      if (!setup.finished && setup.current_step < 3) return problem(res, 428, 'SETUP_REQUIRED', 'Chưa thiết lập xong');
      return reply(200, me(u, null), [login(u)]);
    }
    if (path === '/auth/logout' && method === 'POST') {
      if (sid) sessions.delete(sid);
      return reply(204, undefined, ['gh_session=; Path=/; Max-Age=0']);
    }

    // Giống API thật (gh/middleware.py): trước khi xong bước 1–3, mọi route Console kể cả /auth/me trả 428.
    if (!setup.finished && setup.current_step <= 3 && !path.startsWith('/auth/pin')) {
      return problem(res, 428, 'SETUP_REQUIRED', 'Chưa thiết lập xong');
    }
    if (!user || !session) return problem(res, 401, 'UNAUTHENTICATED', 'Chưa đăng nhập hoặc phiên đã hết hạn');

    if (path === '/auth/me' && method === 'GET') return reply(200, me(user, session.pinUntil));
    if (path === '/auth/pin/verify' && method === 'POST') {
      const f = pinFails.get(user.id) ?? { count: 0, lockedUntil: null };
      if (f.lockedUntil && f.lockedUntil > Date.now()) {
        const lockedUntil = new Date(f.lockedUntil).toISOString();
        record(user, 'auth.pin_attempt_while_locked', 'denied');
        return problem(res, 423, 'PIN_LOCKED', 'PIN bị khoá', { detail: { locked_until: lockedUntil } });
      }
      if (body.pin !== user.pin) {
        f.count += 1;
        if (f.count >= 5) {
          f.lockedUntil = Date.now() + 15 * 60_000;
          f.count = 0;
          pinFails.set(user.id, f);
          record(user, 'auth.pin_locked', 'denied');
          return problem(res, 423, 'PIN_LOCKED', 'PIN bị khoá', { detail: { locked_until: new Date(f.lockedUntil).toISOString() } });
        }
        pinFails.set(user.id, f);
        record(user, 'auth.pin_failed', 'denied', { attempts_left: 5 - f.count });
        return problem(res, 401, 'PIN_INVALID', 'PIN không đúng', { attempts_left: 5 - f.count });
      }
      pinFails.delete(user.id);
      session.pinUntil = Date.now() + 30 * 60_000;
      record(user, 'auth.pin_verified');
      return reply(200, { pin_verified_until: new Date(session.pinUntil).toISOString() });
    }
    const needPin = () => !session.pinUntil || session.pinUntil < Date.now();
    if (path === '/auth/pin' && method === 'PUT') {
      if (needPin()) return problem(res, 423, 'PIN_REQUIRED', 'Cần phiên PIN', { detail: { operation: 'pin.change' } });
      if (body.current_pin !== user.pin) {
        record(user, 'auth.pin_failed', 'denied');
        return problem(res, 401, 'PIN_INVALID', 'PIN hiện tại không đúng');
      }
      if (!/^\d{6}$/.test(String(body.new_pin ?? ''))) {
        return problem(res, 422, 'VALIDATION_ERROR', 'Dữ liệu chưa hợp lệ', { errors: { new_pin: 'PIN gồm đúng 6 chữ số.' } });
      }
      user.pin = String(body.new_pin);
      record(user, 'auth.pin_changed');
      return reply(204);
    }

    if (path === '/account' || path.startsWith('/account/')) {
      const acct = accountRoute(user, sid!, method, path, body);
      if (acct.status === 204) return reply(204);
      if (acct.status >= 400) return problem(res, acct.status, acct.code!, acct.title!, acct.extra ?? {});
      return reply(acct.status, acct.body);
    }

    // v0.1.20: như gh/auth/deps.py current_user — mật khẩu tạm thì mọi route khác /auth/* và /account trả 403.
    if (user.mustChange) return problem(res, 403, 'PASSWORD_CHANGE_REQUIRED', 'Cần đặt mật khẩu mới trước khi tiếp tục');

    if (path === '/navigation' && method === 'GET') return reply(200, buildNavigation(user.hidden, opts.badges ?? true, { hasStaff: opts.staff ?? true }));
    // v0.1.22 (Đợt B1–B3) — như gh/auth/users.py + gh/system_api/org.py.
    if (path === '/system/about' && method === 'GET') {
      // v0.1.36 (F-46): như gh/system_api/org.py — version = genh_version ?? image_version (giữ tương thích).
      return reply(200, { version: sysUpdate.current, image_version: sysUpdate.current ?? 'v0.1.36-dev', genh_version: sysUpdate.current,
        org_name: setup.org.name, timezone: setup.org.timezone, role: user.role });
    }
    // v0.1.36 (F-6): sức khoẻ hệ thống — cùng quyền `system.read` như /system/org.
    if (path === '/system/health' && method === 'GET') {
      if ((permissionsOf(user.role.code)['system.read'] ?? 'none') === 'none') return problem(res, 403, 'FORBIDDEN', 'Không có quyền');
      return reply(200, healthView(user.role.code === 'owner'));
    }
    // v0.1.46 (F-21): Truy cập từ xa — cùng quyền `system.read`.
    if (path === '/system/access' && method === 'GET') {
      if ((permissionsOf(user.role.code)['system.read'] ?? 'none') === 'none') return problem(res, 403, 'FORBIDDEN', 'Không có quyền');
      return reply(200, accessView(user.role.code === 'owner'));
    }
    // v0.1.46 (F-21): dòng 7 "Truy cập từ xa" quyết theo header Origin (POST luôn có; không có thì Host) — chặn ở đây vì
    // `P2Ctx` không mang header. Chỉ Owner (như mock-boss-checks).
    if (path === '/boss-checks/remote_access/run' && method === 'POST') {
      if (user.role.code !== 'owner') return problem(res, 403, 'FORBIDDEN', 'Vai trò của bạn không có quyền thao tác này');
      const origin = String(req.headers.origin ?? '');
      const host = hostOfUrl(origin && origin !== 'null' ? origin : `http://${String(req.headers.host ?? '')}`);
      const a = accessView(true);
      const c = (phase3.bossChecks.hooks.recordRemote as (h: string, l: boolean, m: string) => BossCheck)(host, a.public_url_local, a.mode);
      broadcast('boss_checks.update', { key: 'remote_access' });
      return reply(200, c);
    }
    if (path === '/system/org') {
      if ((permissionsOf(user.role.code)['system.read'] ?? 'none') === 'none') return problem(res, 403, 'FORBIDDEN', 'Không có quyền');
      const view = () => ({ org_name: setup.org.name, timezone: setup.org.timezone, currency: setup.org.currency, self_name: setup.addressing.self,
        bot_calls_me: setup.addressing.bot_calls_me, currencies: ['VND', 'USD', 'EUR', 'JPY', 'SGD', 'THB', 'CNY', 'KRW'], can_edit: user.role.code === 'owner' });
      if (method === 'GET') return reply(200, view());
      if (method === 'PATCH') {
        if (user.role.code !== 'owner') return problem(res, 403, 'FORBIDDEN', 'Không có quyền');
        const v = { org_name: String(body.org_name ?? '').trim(), timezone: String(body.timezone ?? '').trim(), currency: String(body.currency ?? '').trim().toUpperCase(),
          self_name: String(body.self_name ?? '').trim(), bot_calls_me: String(body.bot_calls_me ?? '').trim() };
        const errors: Record<string, string> = {};
        if (!v.org_name) errors.org_name = 'Nhập tên tổ chức';
        if (!/^[A-Za-z]+(\/[A-Za-z_+-]+)+$|^UTC$/.test(v.timezone)) errors.timezone = 'Múi giờ không hợp lệ';
        if (!view().currencies.includes(v.currency)) errors.currency = 'Tiền tệ chưa hỗ trợ';
        if (!v.self_name) errors.self_name = 'Nhập cách Sếp tự xưng';
        if (!v.bot_calls_me) errors.bot_calls_me = 'Nhập cách agent gọi Sếp';
        if (Object.keys(errors).length) return problem(res, 422, 'VALIDATION_ERROR', 'Dữ liệu chưa hợp lệ', { errors });
        setup.org = { name: v.org_name, timezone: v.timezone, currency: v.currency };
        setup.addressing = { self: v.self_name, bot_calls_me: v.bot_calls_me };
        record(user, 'org.updated');
        return reply(200, view());
      }
    }
    if (path === '/users' || path.startsWith('/users/')) {
      if (user.role.code !== 'owner') return problem(res, 403, 'FORBIDDEN', 'Không có quyền');
      const ROLE_NAME: Record<string, string> = { manager: 'Manager · quản lý team', operator: 'Operator · vận hành', agent_staff: 'Agent nhân viên', auditor: 'Auditor · kiểm toán' };
      const out = (u: User) => ({ id: u.id, display_name: u.display_name, email: u.email, role: u.role, status: u.inactive ? 'inactive' : 'active',
        must_change_password: u.mustChange ?? false, last_login_at: u.lastLoginAt ?? (u.mustChange ? null : '2026-09-28T01:10:00.000Z'),
        created_at: u.createdAt ?? '2026-05-04T02:15:00.000Z', is_self: u.id === user.id });
      const tempPw = () => randomUUID().replace(/-/g, '').slice(0, 14);
      const revokeAll = (id: string) => {
        for (const [k, s] of sessions) if (s.userId === id) sessions.delete(k);
      };
      if (path === '/users' && method === 'GET') {
        const items = [...users].sort((a, b) => Number(!!a.inactive) - Number(!!b.inactive) || Number(b.role.code === 'owner') - Number(a.role.code === 'owner') || a.display_name.localeCompare(b.display_name, 'vi'));
        return reply(200, { items: items.map(out), roles: ROLE_ORDER.map((c) => ({ code: c, name: c === 'owner' ? 'Owner — Sếp' : ROLE_NAME[c], meta: '', assignable: c !== 'owner' })) });
      }
      if (needPin()) return problem(res, 423, 'PIN_REQUIRED', 'Cần phiên PIN', { detail: { operation: 'user.manage' } });
      if (path === '/users' && method === 'POST') {
        const name = String(body.display_name ?? '').trim().replace(/\s+/g, ' ');
        const email = String(body.email ?? '').trim().toLowerCase();
        const role = String(body.role ?? '');
        const errors: Record<string, string> = {};
        if (!name) errors.display_name = 'Nhập tên hiển thị';
        if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) errors.email = 'Email chưa đúng định dạng';
        else if (users.some((u) => u.email === email)) errors.email = 'Email này đã có tài khoản';
        if (!ROLE_NAME[role]) errors.role = 'Vai trò không hợp lệ';
        if (Object.keys(errors).length) return problem(res, 422, 'VALIDATION_ERROR', 'Dữ liệu chưa hợp lệ', { errors });
        const pw = tempPw();
        const u: User = { id: randomUUID(), email, password: pw, pin: '000000', display_name: name, role: { code: role as RoleCode, name: ROLE_NAME[role] },
          hidden: hiddenScreens(role as RoleCode), mustChange: true, createdAt: new Date().toISOString(), lastLoginAt: null };
        users.push(u);
        record(user, 'user.invited', 'ok', { role });
        return reply(201, { user: out(u), temp_password: pw });
      }
      const m = /^\/users\/([^/]+)\/(role|deactivate|reactivate|reset-password)$/.exec(path);
      const target = m ? users.find((u) => u.id === m[1]) : undefined;
      if (!m || !target) return problem(res, 404, 'NOT_FOUND', 'Không tìm thấy người dùng');
      const op = m[2];
      if (target.id === user.id && op !== 'reactivate') return problem(res, 409, 'SELF_CHANGE', 'Không đổi tài khoản của chính mình ở đây — dùng Tài khoản của tôi');
      const lastOwner = () => target.role.code === 'owner' && !target.inactive && !users.some((u) => u.id !== target.id && u.role.code === 'owner' && !u.inactive);
      if (op === 'role' && method === 'PATCH') {
        const role = String(body.role ?? '');
        if (!ROLE_NAME[role]) return problem(res, 422, 'VALIDATION_ERROR', 'Dữ liệu chưa hợp lệ', { errors: { role: 'Vai trò không hợp lệ' } });
        if (lastOwner()) return problem(res, 409, 'LAST_OWNER', 'Đây là Owner cuối cùng — tổ chức phải luôn có ít nhất một Owner');
        const from = target.role.code;
        target.role = { code: role as RoleCode, name: ROLE_NAME[role] };
        target.hidden = hiddenScreens(role as RoleCode);
        record(user, 'user.role_changed', 'ok', { from, to: role });
        notify(target.id, 'user.role_changed', 'Vai trò của bạn đã đổi', `${user.display_name} đã đổi vai trò của bạn thành ${target.role.name}.`, '/account');
        return reply(200, out(target));
      }
      if (method !== 'POST') return problem(res, 405, 'METHOD_NOT_ALLOWED', 'Sai phương thức');
      if (op === 'deactivate') {
        if (lastOwner()) return problem(res, 409, 'LAST_OWNER', 'Đây là Owner cuối cùng — tổ chức phải luôn có ít nhất một Owner');
        target.inactive = true;
        revokeAll(target.id);
        record(user, 'user.deactivated');
        return reply(200, out(target));
      }
      if (op === 'reactivate') {
        target.inactive = false;
        record(user, 'user.reactivated');
        notify(target.id, 'user.reactivated', 'Tài khoản đã được mở khoá', `${user.display_name} đã mở khoá tài khoản của bạn.`);
        return reply(200, out(target));
      }
      const pw = tempPw();
      target.password = pw;
      target.mustChange = true;
      revokeAll(target.id);
      record(user, 'user.password_reset');
      notify(target.id, 'user.password_reset', 'Mật khẩu đã được đặt lại', `${user.display_name} đã đặt lại mật khẩu của bạn.`, '/account');
      return reply(200, { user: out(target), temp_password: pw });
    }
    if (path === '/system/update/check' && method === 'POST') {
      // v0.1.30: "Kiểm tra bản mới" — mock coi như vừa hỏi GitHub xong.
      if ((permissionsOf(user.role.code)['system.manage'] ?? 'none') === 'none') return problem(res, 403, 'FORBIDDEN', 'Không có quyền');
      sysUpdate.checked_at = new Date().toISOString();
      return reply(200, { ...sysUpdate, update_available: sysUpdate.latest !== sysUpdate.current, throttled: false });
    }
    if (path === '/system/update') {
      // Như gh/system_api/update.py: system.manage; mock mô phỏng genh trên máy chủ — mỗi lần hỏi tiến một bước
      // requested → running → done (current = latest).
      if ((permissionsOf(user.role.code)['system.manage'] ?? 'none') === 'none') return problem(res, 403, 'FORBIDDEN', 'Không có quyền');
      const u = sysUpdate;
      if (method === 'GET') {
        if (u.state === 'requested') u.state = 'running';
        else if (u.state === 'running') Object.assign(u, { state: 'done', current: u.latest, to: u.latest, finished_at: new Date().toISOString() });
      } else if (method === 'POST') {
        if (!u.can_request) return problem(res, 409, 'UPDATER_UNAVAILABLE', 'Máy chủ chưa bật nhận yêu cầu cập nhật từ Console');
        if (u.state === 'requested' || u.state === 'running') return problem(res, 409, 'UPDATE_IN_PROGRESS', 'Đang cập nhật');
        Object.assign(u, { state: 'requested', requested_at: new Date().toISOString() });
        return reply(202, { ...u, update_available: u.latest !== u.current });
      } else return problem(res, 405, 'METHOD_NOT_ALLOWED', 'Không hỗ trợ');
      return reply(200, { ...u, update_available: u.latest !== u.current });
    }
    if (path === '/notifications' && method === 'GET') {
      const limit = Math.min(100, Math.max(1, Number(url.searchParams.get('limit') ?? 20)));
      const offset = Math.max(0, Number(url.searchParams.get('offset') ?? 0));
      const list = notifsOf(user.id);
      return reply(200, { items: list.slice(offset, offset + limit), unread: list.filter((n) => !n.read).length, total: list.length });
    }
    if (path === '/notifications/read' && method === 'POST') {
      const ids = Array.isArray(body.ids) ? (body.ids as string[]) : null;
      const list = notifsOf(user.id);
      for (const n of list) if (!ids || !ids.length || ids.includes(n.id)) n.read = true;
      return reply(200, { unread: list.filter((n) => !n.read).length });
    }
    // v0.1.35 (F-1) — như gh/biz/core/pickers.py: chỉ id + tên (không email/vai trò), người đang đăng nhập `me`.
    if (path === '/pickers/users' && method === 'GET') {
      const perms = permissionsOf(user.role.code);
      if (['queue.act', 'opportunity.write', 'profile.read'].every((k) => (perms[k] ?? 'none') === 'none')) {
        return problem(res, 403, 'FORBIDDEN', 'Vai trò của bạn không có quyền thao tác này', { detail: 'queue.act' });
      }
      const items = users
        .filter((u) => !u.inactive)
        .sort((a, b) => a.display_name.localeCompare(b.display_name, 'vi'))
        .map((u) => ({ id: u.id, name: u.display_name, me: u.id === user.id }));
      return reply(200, { items });
    }
    if (path === '/pickers/agents' && method === 'GET') {
      if ((permissionsOf(user.role.code)['profile.write'] ?? 'none') === 'none') {
        return problem(res, 403, 'FORBIDDEN', 'Vai trò của bạn không có quyền thao tác này', { detail: 'profile.write' });
      }
      const items = agentList()
        .filter((a) => a.is_enabled)
        .sort((a, b) => a.name.localeCompare(b.name, 'vi'))
        .map((a) => ({ id: a.id, name: a.name }));
      return reply(200, { items });
    }
    // v0.1.47 (F-79): ảnh chụp bằng chứng — JPEG nhị phân, no-store, chỉ Owner (mock-social giữ trạng thái).
    const proof = /^\/social\/jobs\/([^/]+)\/proof$/.exec(path);
    if (proof && method === 'GET') {
      if (user.role.code !== 'owner') return problem(res, 403, 'FORBIDDEN', 'Vai trò của bạn không có quyền thao tác này');
      const jpeg = social.proofOf(proof[1]);
      if (!jpeg) return problem(res, 404, 'NOT_FOUND', 'Không có ảnh chụp cho việc này');
      res.writeHead(200, { 'Content-Type': 'image/jpeg', 'Cache-Control': 'no-store' });
      res.end(jpeg);
      return;
    }
    if (path === '/header' && method === 'GET') {
      return reply(200, { channels_live: 4, channels_connected: 4, groups_listening: 42, autonomy_level: 4, data_confidence: 0.78 });
    }
    // GET /audit: `system.handle` (mock-p4-system.ts) — cùng bộ lọc/phân trang với máy chủ thật.
    if (path === '/audit/verify' && method === 'GET') return reply(200, { ok: true, checked: audit.length, broken_at: null });

    const ctx: P2Ctx = {
      method,
      path,
      url,
      body,
      perms: permissionsOf(user.role.code),
      reply: (status, b) => {
        reply(status, b);
        return true;
      },
      problem: (status, code, title, extra) => {
        problem(res, status, code, title, extra);
        return true;
      },
      text: (status, contentType, text, filename) => {
        const headers: Record<string, string | string[]> = { 'Content-Type': contentType, 'Cache-Control': 'no-store' };
        if (filename) headers['Content-Disposition'] = `attachment; filename="${filename}"`;
        if (setCookies.length) headers['Set-Cookie'] = setCookies;
        res.writeHead(status, headers);
        res.end(text);
        return true;
      },
      needPin,
      userLabel: user.display_name,
      owner: user.role.code === 'owner',
      role: user.role.code,
    };
    if (phase2.handle(ctx)) return;
    for (const m of Object.values(phase3)) if (m.handle(ctx)) return;

    return problem(res, 404, 'NOT_FOUND', 'Không tồn tại');
  };

  const setHealth = (o: MockHealthOverride) => {
    healthOverride = { ...healthOverride, ...o };
  };
  const setAccess = (o: MockAccessOverride) => {
    accessOverride = { ...accessOverride, ...o };
  };
  /** v0.1.53 (F-99): `__mock/nightly` — xem `MockNightlyOverride`. */
  const setNightly = (o: MockNightlyOverride) => {
    if (o.update) Object.assign(sysUpdate, o.update);
    if ('health' in o) healthOverride = { ...healthOverride, nightly: o.health ?? null };
  };
  return { middleware, setup, users, sessions, phase2, phase3, sessionUser, notify, setHealth, setAccess, setNightly };
}

/**
 * Mock API with test-only hooks (all POST, JSON body):
 *   /api/v1/__mock/reset    {"setup":"fresh"|"finished","simulate":bool,"allowFinish":bool,"staff":bool} rebuilds the state
 *   /api/v1/__mock/emit     {"type","data"} broadcasts one realtime frame
 *   /api/v1/__mock/notify   {"title","body","link","kind"} gives every Owner one notification (notification.new)
 *   /api/v1/__mock/raw      {} pushes one simulated raw message (raw.new → raw.state)
 *   /api/v1/__mock/scan     {"type":"zalo"} simulates the phone scanning the QR
 *   /api/v1/__mock/discover {"type"?:"zalo"} 3 nhóm MỚI vào `pending` + WS `group.discovered` (v0.1.65)
 *   /api/v1/__mock/simulate {"on":bool} toggles the background simulation
 *   /api/v1/__mock/bridge   {"online":bool} makes channel login answer 503 BRIDGE_OFFLINE
 *   /api/v1/__mock/pin_expire {} ends every PIN session (v0.1.45 — kiểm lại hộp PIN của thao tác kế tiếp)
 *   /api/v1/__mock/perm    {"role","permission","scope"} đặt một ô MATRIX (kể cả cột không sửa được ở Quyền hạn, vd system.manage)
 *   /api/v1/__mock/access  {"public_url"?,"mode"?,"bind_addr"?,"site_address"?} ghi đè `GET /system/access` (v0.1.46; reset khôi phục localhost/'local')
 *   /api/v1/__mock/health  {"issues"?,"worker"?,"backup"?,"disk"?,"update"?,"autostart"?,"offsite"?} ghi đè `GET /system/health` (v0.1.36;
 *                           `issues` chỉ cần `kind` — nhãn/nút/đường dẫn mặc định theo kind; reset khôi phục khoẻ;
 *                           v0.1.53: thêm `nightly` = khối lịch tự cập nhật đêm, `warn` mở sự cố host.nightly)
 *   /api/v1/__mock/nightly {"update"?,"health"?} v0.1.53 (F-99): `update` trộn vào `GET /system/update` (state/stalled_reason/nightly/
 *                           nightly_candidates/auto_update_enabled/latest/published_at…), `health` đặt khối nightly của `GET /system/health`
 *   /api/v1/__mock/p3/{cụm}/{hook}  body → `phase3[cụm].hooks[hook](body)`; trả JSON kết quả (404 nếu không có)
 *       v0.1.59 (G1) Mặt tiền Owner: `/__mock/p3/owner/data {"mode":"full"|"empty"|"collecting"|"nochannel"}` đổi chế độ dữ liệu
 *       lúc chạy (gọi bằng `p3Hook(request, 'owner', 'data', { mode })`); `/__mock/p3/owner/scenario {"mode":"data"|"empty"|"error",
 *       "only"?}` ép một đường rỗng/lỗi 500; `/__mock/p3/owner/state` đọc số lần gọi; `/__mock/p3/owner/reset`. Chế độ ban đầu theo
 *       `createMockApi({ ownerData })` (`MOCK_OWNER_DATA=empty|collecting|nochannel` ở vite.config.ts); `__mock/reset` dựng lại theo đó.
 */
export function createMockApi(opts: MockOptions = {}) {
  const clients = new Set<MockSocket>();
  const allowed = (ws: MockSocket, type: string) => {
    const perms = (ws.meta.perms ?? {}) as Record<string, string>;
    const rule = EVENT_PERMISSION.find(([prefix]) => type.startsWith(prefix));
    if (!rule) return true;
    return rule[1] === null || (!!perms[rule[1]] && perms[rule[1]] !== 'none');
  };
  const broadcast = (type: string, data: unknown, toUser?: string) => {
    const at = new Date().toISOString();
    const frame = JSON.stringify({ type, data, at });
    let masked: string | null = null;
    for (const ws of clients) {
      if (!ws.open || !allowed(ws, type)) continue;
      // Như gh/realtime.py `to_user`: sự kiện riêng (thông báo) chỉ tới đúng người.
      if (toUser && ws.meta.userId !== toUser) continue;
      // As the API (gh/realtime.py): raw.new text is masked for roles below Owner.
      if (type === 'raw.new' && ws.meta.role !== 'owner') {
        const d = data as { text?: string | null };
        masked ??= JSON.stringify({ type, data: { ...d, text: maskText(d.text ?? null) }, at });
        ws.send(masked);
      } else ws.send(frame);
    }
  };
  let current = createMockState(opts, broadcast);

  const readJson = async (req: IncomingMessage): Promise<Record<string, unknown>> => {
    const chunks: Buffer[] = [];
    for await (const c of req) chunks.push(c as Buffer);
    try {
      return JSON.parse(Buffer.concat(chunks).toString('utf8') || '{}') as Record<string, unknown>;
    } catch {
      return {};
    }
  };
  const done = (res: ServerResponse, status = 204, body?: unknown) => {
    res.writeHead(status, body === undefined ? {} : { 'Content-Type': 'application/json' });
    res.end(body === undefined ? undefined : JSON.stringify(body));
  };

  const middleware = async (req: IncomingMessage, res: ServerResponse, next: Next) => {
    const p3 = /^\/api\/v1\/__mock\/p3\/(\w+)\/(\w+)/.exec(req.url ?? '');
    if (p3 && req.method === 'POST') {
      const body = await readJson(req);
      const fn = (current.phase3 as unknown as Record<string, { hooks: Record<string, (b: unknown) => unknown> }>)[p3[1]]?.hooks[p3[2]];
      if (!fn) return done(res, 404);
      return done(res, 200, fn(body) ?? null);
    }
    const hook = /^\/api\/v1\/__mock\/(\w+)/.exec(req.url ?? '');
    if (hook && req.method === 'POST') {
      const body = await readJson(req);
      switch (hook[1]) {
        case 'reset':
          current.phase2.dispose();
          for (const m of Object.values(current.phase3)) m.dispose();
          for (const ws of clients) ws.close(4401, 'reset');
          clients.clear();
          restoreMatrix();
          current = createMockState({ ...opts, ...(body as MockOptions) }, broadcast);
          return done(res);
        case 'perm': {
          const i = ROLE_ORDER.indexOf(String(body.role) as RoleCode);
          const row = MATRIX[String(body.permission)];
          if (i < 0 || !row) return done(res, 404);
          row[i] = String(body.scope);
          // Danh mục màn của vai trò đó tính lại theo ma trận mới (như `hiddenScreens` lúc tạo người dùng).
          for (const u of current.users) if (u.role.code === ROLE_ORDER[i]) u.hidden = hiddenScreens(ROLE_ORDER[i]);
          return done(res);
        }
        case 'emit':
          broadcast(String(body.type), body.data);
          return done(res);
        case 'notify': {
          // {"title","body"?,"link"?,"kind"?} → thông báo cho mọi Owner (như sao lưu xong ở API thật).
          const owners = current.users.filter((u) => u.role.code === 'owner');
          const items = owners.map((u) =>
            current.notify(u.id, String(body.kind ?? 'backup.done'), String(body.title ?? 'Thông báo'), String(body.body ?? ''), (body.link as string | undefined) ?? null),
          );
          return done(res, 200, items);
        }
        case 'health':
          current.setHealth(body as MockHealthOverride);
          return done(res);
        case 'nightly':
          // v0.1.53 (F-99): {update?, health?} — đặt trạng thái lịch tự cập nhật đêm / linger cho thẻ cập nhật và thẻ Sức khoẻ.
          current.setNightly(body as MockNightlyOverride);
          return done(res);
        case 'access':
          // v0.1.46 (F-21): {public_url?, mode?, bind_addr?, site_address?} — ghi đè GET /system/access; reset khôi phục.
          current.setAccess(body as MockAccessOverride);
          return done(res);
        case 'raw':
          return done(res, 200, current.phase2.hooks.pushRaw());
        case 'listenevent':
          // v0.1.68: thêm một sự kiện kênh vào Nhật ký lắng nghe + WS `listen.event`.
          return done(res, 200, current.phase2.hooks.pushEvent());
        case 'scan':
          return done(res, current.phase2.hooks.scan(String(body.type ?? 'zalo')) ? 204 : 409);
        case 'discover':
          // v0.1.65: {"type"?:"zalo","ask"?:true} — bridge phát hiện nhóm mới: 3 nhóm + WS `group.discovered`. Nhóm vào `silent`
          // (QD 17/10, mặc định) hay `pending` (ask: true ⇒ mô phỏng Sếp đặt «Nhóm mới: Hỏi Sếp»).
          return done(res, 200, { added: current.phase2.hooks.discover(body.type === 'whatsapp' ? 'whatsapp' : 'zalo', body.ask === true) });
        case 'simulate':
          current.phase2.hooks.setSimulation(Boolean(body.on));
          return done(res);
        case 'bridge':
          current.phase2.hooks.setBridge(Boolean(body.online));
          return done(res);
        case 'pin_expire':
          // v0.1.45: cho mọi phiên PIN hết hạn (như hết 30 phút) — e2e kiểm lại hộp PIN của thao tác sau.
          for (const s of current.sessions.values()) s.pinUntil = null;
          return done(res);
        default:
          return done(res, 404);
      }
    }
    return current.middleware(req, res, next);
  };

  /** HTTP upgrade handler for `/api/v1/ws` (other paths are left alone, e.g. Vite HMR). */
  const upgrade = (req: IncomingMessage, socket: Duplex) => {
    const url = new URL(req.url ?? '/', 'http://mock.local');
    // v0.1.29 — cửa sổ đăng nhập mạng xã hội (khung hình + chuột/phím), như `WS /social/login/{ticket}`.
    const live = /^\/api\/v1\/social\/login\/([^/]+)$/.exec(url.pathname);
    if (live) {
      const state = current;
      const auth = state.sessionUser(req);
      const ticket = decodeURIComponent(live[1]);
      const ws = acceptWebSocket(req, socket, (conn, text) => state.phase3.social.liveInput(ticket, conn, text), () => undefined);
      if (ws) state.phase3.social.liveSocket(ticket, ws, auth?.user.role.code === 'owner');
      return true;
    }
    if (url.pathname !== '/api/v1/ws') return false;
    const state = current;
    const auth = state.sessionUser(req);
    const ws = acceptWebSocket(
      req,
      socket,
      (conn, text) => {
        try {
          const msg = JSON.parse(text) as { type?: string };
          if (msg.type === 'ping') conn.send(JSON.stringify({ type: 'pong', data: null, at: new Date().toISOString() }));
        } catch {
          /* ignore */
        }
      },
      (conn) => clients.delete(conn),
    );
    if (!ws) return true;
    if (!state.setup.finished && state.setup.current_step <= 3) {
      ws.close(4428, 'SETUP_REQUIRED');
      return true;
    }
    if (!auth) {
      ws.close(4401, 'UNAUTHENTICATED');
      return true;
    }
    ws.meta.perms = permissionsOf(auth.user.role.code);
    ws.meta.role = auth.user.role.code;
    ws.meta.userId = auth.user.id;
    clients.add(ws);
    return true;
  };

  return {
    middleware,
    upgrade,
    get state() {
      return current;
    },
  };
}
