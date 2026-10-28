/**
 * v0.1.78 (G0) — bảng chuyển hướng: MỌI đường dẫn cũ (Mặt tiền `/owner/*` và route Console theo khoá màn `/inbox`, `/raw`…)
 * → trang mới của MỘT shell (`/intel`, `/decide`, `/work`, `/panorama`, `/me`, `/team`, `/admin/*`). Link cũ — trong thông báo
 * đã lưu, Gen, bản tin, bookmark — không bao giờ chết. Hàm THUẦN (không React): test được, và test bảng
 * (test/unit/legacy-redirects-v0178.test.ts) bảo đảm mọi đích đều là route có thật và không đích nào lại chuyển hướng tiếp.
 *
 * Quy tắc hợp query: giữ mọi tham số của link cũ (`?tin=`, `?nguoi=`, `?id=`, `?gen=`, bộ lọc…) và băm `#neo`; riêng `tab` của link cũ
 * bị bỏ (trang mới tự đặt `?tab=`) — trừ khi màn đích tự dùng `tab` cho việc khác (`keepTab`: Hộp thư, Plugin).
 */

export interface LegacyTarget {
  /** Đường dẫn đích (không query). */
  to: string;
  /** Tab của trang đích (`?tab=`); vắng ⇒ tab mặc định. */
  tab?: string;
  /** Giữ `tab` của link cũ (màn nhúng ở đích tự đọc `?tab=`). */
  keepTab?: boolean;
}

const ADV = '/admin/advanced';

/** Đường dẫn cũ → đích. Khoá là pathname chính xác, KHÔNG có dấu `/` ở cuối. */
export const LEGACY_REDIRECTS: Readonly<Record<string, LegacyTarget>> = {
  // ── Mặt tiền Owner (v0.1.55–59) ──
  '/owner': { to: '/intel' },
  '/owner/thong-tin': { to: '/intel', tab: 'signals' },
  '/owner/quan-he': { to: '/panorama', tab: 'relations' },
  '/owner/phan-tich': { to: '/panorama', tab: 'trends' },
  '/owner/viec': { to: '/decide' },
  '/owner/gen': { to: '/intel' },
  '/owner/them': { to: '/admin/start' },
  // ── Console theo khoá màn (v0.1.42): Việc hằng ngày ──
  '/overview': { to: '/intel', tab: 'overview' },
  '/inbox': { to: '/intel/inbox', keepTab: true },
  '/workbench': { to: '/decide', tab: 'drafts' },
  '/tasks': { to: '/work' },
  '/directory': { to: '/panorama', tab: 'people' },
  '/opportunity': { to: '/panorama', tab: 'opportunity' },
  '/deals': { to: '/panorama', tab: 'deals' },
  '/documents': { to: '/panorama', tab: 'documents' },
  '/search': { to: '/panorama', tab: 'search' },
  '/profile': { to: '/panorama', tab: 'profile' },
  '/graph': { to: '/panorama', tab: 'graph' },
  '/notebook': { to: '/panorama', tab: 'notebook' },
  '/supply': { to: '/panorama', tab: 'supply' },
  '/people': { to: '/team', tab: 'reviews' },
  '/care': { to: '/team', tab: 'care' },
  // ── Quản trị ──
  '/connections': { to: '/admin/connections' },
  '/guide': { to: '/admin/start' },
  '/raw': { to: ADV, tab: 'raw' },
  '/listenlog': { to: ADV, tab: 'listenlog' },
  '/rules': { to: ADV, tab: 'rules' },
  '/clean': { to: ADV, tab: 'clean' },
  '/identity': { to: ADV, tab: 'identity' },
  '/agents': { to: ADV, tab: 'agents' },
  '/api': { to: ADV, tab: 'api' },
  '/mcp': { to: ADV, tab: 'mcp' },
  '/plugins': { to: `${ADV}/plugins`, keepTab: true },
  // Khoá màn mới của miền admin mà Gen/bản tin có thể ghi dạng `/<khoá>` (GEN_SCREENS dùng `/${key}`).
  '/start': { to: '/admin/start' },
  '/access': { to: '/admin/access' },
  '/backup': { to: '/admin/backup' },
  '/org': { to: '/admin/org' },
  '/log': { to: '/admin/log' },
  '/advanced': { to: ADV },
};

/** `/system?tab=<tab>` (Cài đặt cũ, 5 tab + 2 tab đã chuyển ở v0.1.42) → trang mới. */
export const SYSTEM_TAB_TARGETS: Readonly<Record<string, LegacyTarget>> = {
  storage: { to: '/admin/backup' },
  org: { to: '/admin/org' },
  brain: { to: ADV, tab: 'brain' },
  roles: { to: '/admin/access', tab: 'roles' },
  log: { to: '/admin/log' },
  channels: { to: '/admin/connections' },
  users: { to: '/admin/access' },
};

/** Tab mặc định của `/system` khi link cũ không ghi tab: đầu tiên vai trò được phép (Sao lưu; Nhật ký nếu chỉ có `audit.read`). */
export function systemDefaultTarget(canSystem: boolean): LegacyTarget {
  return canSystem ? { to: '/admin/backup' } : { to: '/admin/log' };
}

const trim = (p: string): string => (p.length > 1 ? p.replace(/\/+$/, '') : p);

/** Đường lạ dưới `/owner` (vd `/owner/xyz`) về Tình báo — như `<Navigate to="/owner">` cũ. */
const OWNER_FALLBACK: LegacyTarget = { to: '/intel' };

/**
 * Đích của một đường dẫn cũ, hoặc `null` nếu không phải đường cũ (trang hiện hành). `canSystem` chỉ dùng cho `/system` không ghi tab.
 */
export function legacyTarget(pathname: string, canSystem = true, search = ''): LegacyTarget | null {
  const path = trim(pathname);
  if (path === '/system') {
    const tab = new URLSearchParams(search).get('tab') ?? '';
    return SYSTEM_TAB_TARGETS[tab] ?? systemDefaultTarget(canSystem);
  }
  const hit = LEGACY_REDIRECTS[path];
  if (hit) return hit;
  if (path.startsWith('/owner/')) return OWNER_FALLBACK;
  return null;
}

/** Ghép đích + query/băm của link cũ thành chuỗi `to` cho `<Navigate>`. */
export function resolveLegacy(pathname: string, search: string, hash: string, canSystem = true): string | null {
  const t = legacyTarget(pathname, canSystem, search);
  if (!t) return null;
  const params = new URLSearchParams(search);
  if (!t.keepTab) params.delete('tab');
  // Trang mới tự đặt `?tab=` — đặt trước để tab hiện đầu chuỗi (dễ đọc), phần còn lại giữ nguyên thứ tự của link cũ.
  const out = new URLSearchParams();
  if (t.tab) out.set('tab', t.tab);
  params.forEach((v, k) => out.append(k, v));
  const qs = out.toString();
  return `${t.to}${qs ? `?${qs}` : ''}${hash}`;
}

/** Mọi pathname cần một route chuyển hướng (bảng + `/system`; `/owner/*` do route riêng). */
export const LEGACY_PATHS: readonly string[] = [...Object.keys(LEGACY_REDIRECTS), '/system'];
