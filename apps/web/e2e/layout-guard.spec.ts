import { execSync } from 'node:child_process';
import { mkdirSync, readdirSync, rmSync } from 'node:fs';
import { join } from 'node:path';
import { expect, test, type Page } from '@playwright/test';
import { SCREENS } from '@gen-harness/contracts';
import { OWNER, SETUP_TOKEN, apiCall, loginAsOwner, mockHook, resetMock, resultsDir } from './support';

/**
 * Lính gác bố cục (sau v0.1.49 — "nội dung dính biên khung"): duyệt MỌI màn (lấy từ packages/contracts/src/screens.ts)
 * + các tab chính + trang phụ (Hướng dẫn, Trợ giúp, Tài khoản, Mạng xã hội) ở 1920/1440/1024/390 với Owner — MỖI CỠ MÀN
 * MỘT TEST (một route chập chờn chỉ hỏng một cỡ, retry chỉ chạy lại cỡ đó) — cộng một test trang ngoài (Đăng nhập,
 * Thiết lập bước 4). Đo trong <main> (bỏ header `.hd` và thanh bên; trang Đăng nhập không có <main> ⇒ đo trong `.login`):
 *
 *  - `dinh-bien` (chữ dính mép khung) — đo HỘP CHỮ THẬT: với mọi text node hiển thị (chữ sau trim dài > 1, không nằm
 *    trong script/style/select/textarea, không display:none/visibility:hidden, không position:fixed), lấy
 *    `Range.selectNodeContents(textNode).getBoundingClientRect()` (hộp glyph, đã cắt theo các tổ tiên overflow ≠ visible
 *    nằm giữa chữ và khung — chữ bị ellipsis tính tới mép vùng cắt; phía giáp mép một vùng CUỘN ngang còn nội dung khuất
 *    thì bỏ — bảng rộng đang cuộn, cột kế tiếp chỉ khuất, không phải dính mép). "Khung" = tổ tiên gần nhất có
 *    border-left-width > 0, hoặc (border-radius > 0 và nền không trong suốt), hoặc gốc đo. `<table>` KHÔNG tự là khung: bảng không viền/nền nằm
 *    trong thân thẻ có đệm thì chữ cách mép thẻ đúng bằng đệm thân — mắt người thấy vậy; bảng có viền/nền đã được hai
 *    luật trên bắt. Khoảng cách hộp chữ tới mép trái/phải của khung < 6px ⇒ phát hiện. Không tính khung nhỏ ≤ 48×48px
 *    (avatar/huy hiệu chữ viết tắt căn giữa — chữ cố ý chiếm gần hết khung) — đây là quy tắc đo, không phải allowlist.
 *  - `tran-ngang` — `document.documentElement.scrollWidth > clientWidth`, hoặc vùng nội dung `.content` (overflow-x:auto)
 *    phải cuộn ngang, hoặc phần tử có `getBoundingClientRect().right > bề rộng màn + 1` (báo phần tử ngoài cùng gây tràn).
 *  - `bi-cat-khung` — phần tử overflow-x: hidden|clip (không phải chữ ellipsis) có scrollWidth > clientWidth + 2: nội dung
 *    bị cắt mất mép phải mà không cuộn được (vd bảng tool MCP rộng 443px trong thẻ `overflow:hidden` ở 390px).
 *  - `chu-bi-cat` — phần tử một dòng (white-space: nowrap hoặc text-overflow: ellipsis) có scrollWidth > clientWidth + 2
 *    mà không có `title` (trên nó hoặc tổ tiên). Bỏ qua vùng cuộn có chủ đích (overflow-x: auto|scroll) và chữ ẩn cho
 *    trình đọc màn hình (rộng < 4px).
 *  - `chu-bi-ep` — cột chữ bị ép, CÓ title cũng tính: chữ một dòng bị cắt còn < 64px và thấy < 55% (vd tên "Ngu…"), hoặc
 *    một đoạn chữ vỡ ≥ 3 dòng mà dòng rộng nhất < 48px (chữ xếp dọc từng tiếng một).
 *  - `cuon-an` — vùng overflow-x: auto|scroll đang có nội dung khuất (scrollWidth > clientWidth + 1) mà ẩn thanh cuộn
 *    (`scrollbar-width: none`) và không có mép mờ (`mask-image`) — người dùng không biết còn nội dung để cuộn.
 *  - `icon-rot-dong` — Icon (svg/span `data-icon`) display:block trong cha KHÔNG flex/grid mà cha có chữ ⇒ icon một dòng,
 *    chữ dòng dưới (đúng lỗi khối "Máy chủ chưa nhận yêu cầu cập nhật — xem ở Cài đặt" vỡ 3 dòng ở v0.1.44–v0.1.49).
 *  - `icon-lech` — Icon là nội dung DUY NHẤT của một hộp khối không flex/grid (đi lên qua các span inline chỉ bọc mỗi
 *    icon) mà hộp cao hơn icon > 2px: Icon inline-block ⇒ hộp dòng (strut theo line-height) đội hộp cao lên và đẩy icon
 *    lệch xuống (vd cột kênh /raw 14→20px, vòng trạng thái /tasks 18→21px). Khung chỉ-icon phải là flex/inline-flex/grid.
 *
 * Ảnh: chỉ chụp khi route có phát hiện — ảnh cả trang `<w>_<route>.png` + ảnh phóng ±40px quanh từng phần tử
 * (`…__zoomN.png`) vào `$LAYOUT_GUARD_DIR/<nhánh>/` (mặc định test-results/layout-guard). `LAYOUT_GUARD_SHOTS=all` ⇒
 * chụp cả trang mọi route (để xem bằng mắt). Đầu mỗi test xoá ảnh cũ của chính test đó. Lỗi ⇒ in bảng phát hiện gom theo
 * (loại, selector, route) kèm các cỡ màn gặp lỗi.
 *
 * Kịch bản dữ liệu: mock "finished" + ép thẻ cập nhật "Máy chủ chưa nhận yêu cầu cập nhật" (GET /system/update
 * stalled/linger_off — v0.1.53, có thêm câu nguyên nhân + khối lệnh enable-linger) và vài sự cố ở dải "Cần Sếp xử lý"
 * (kể cả "Lịch tự cập nhật đêm chưa chạy N ngày" kèm thân dài ba lệnh) — đúng các thẻ trong ảnh Boss. Một test riêng quét
 * các biến thể còn lại của thẻ cập nhật (not_picked_up, watcher_failed, GH-E94C, gợi ý "Tự cài…" dài) ở Hôm nay và Cài đặt.
 */

type Kind = 'dinh-bien' | 'tran-ngang' | 'bi-cat-khung' | 'chu-bi-cat' | 'chu-bi-ep' | 'cuon-an' | 'icon-rot-dong' | 'icon-lech';

interface Finding {
  kind: Kind;
  selector: string;
  text: string;
  gap: number;
  rect: { x: number; y: number; width: number; height: number };
}

type Hit = Finding & { route: string; w: number };

/**
 * ALLOWLIST — MẶC ĐỊNH RỖNG. Chỉ thêm khi phát hiện đã được người xem ảnh xác nhận KHÔNG phải lỗi (dương tính giả của
 * phép đo, vd chữ cố ý tràn trong một hiệu ứng) hoặc đã có việc sửa riêng được ghi trong docs/reports/HANDOFF-v0.1.1.md
 * — luôn kèm `reason` nói rõ vì sao và đến khi nào. Khớp khi `kind` bằng nhau, `selector` chứa `selector` (chuỗi con)
 * và (nếu có) `route` khớp đúng route.
 */
const ALLOWLIST: Array<{ kind: Kind; selector: string; route?: string; reason: string }> = [];

const VIEWPORTS = [
  { width: 1920, height: 1080 },
  { width: 1440, height: 900 },
  { width: 1024, height: 800 },
  { width: 390, height: 844 },
] as const;

/** Tab/biến thể chính của từng màn (khoá URL thật: useUrlState trong từng màn). */
const EXTRA_ROUTES = [
  '/inbox?tab=opportunity',
  '/inbox?tab=alert',
  '/inbox?tab=approval',
  '/inbox?tab=reply',
  '/tasks?ptab=overdue',
  '/tasks?ptab=all',
  '/directory?dt=people',
  '/deals?dtab=cases',
  '/people?board=customer',
  '/people?board=candidate',
  '/people?board=student',
  '/system?tab=storage',
  '/system?tab=org',
  '/system?tab=brain',
  '/system?tab=roles',
  '/system?tab=log',
  '/guide',
  '/guide/4',
  '/guide/5',
  '/guide/6',
  '/guide/10',
  '/guide/viec-sep',
  '/help',
  '/account',
  '/social',
  '/social/ghi-facebook',
  // v0.1.55 (G5): Mặt tiền Owner — mobile-first (không thuộc screens.json nên liệt kê tay).
  // v0.1.59 (G2): 5 trụ cột (Tình báo, Thông tin, Quan hệ, Phân tích, Việc) + Hỏi Gen (link cũ) + Cài đặt. ALLOWLIST RỖNG cho mọi đường này.
  '/owner',
  '/owner/thong-tin',
  '/owner/quan-he',
  '/owner/phan-tich',
  '/owner/viec',
  '/owner/gen',
  '/owner/them',
];

/** Màn trong cây SCREENS (trừ Hồ sơ sống — cần ?id=, thêm riêng bên dưới). */
const SCREEN_ROUTES = SCREENS.filter((s) => s.key !== 'profile').map((s) => `/${s.key}`);

/**
 * Trạng thái cập nhật "máy chủ chưa nhận yêu cầu" (như ảnh Boss) — hiện UpdateNotice ở Hôm nay và thẻ ở Cài đặt.
 * v0.1.53 (F-99): nguyên nhân `linger_off` (linger tắt) — thẻ dài nhất: tiêu đề + câu nguyên nhân + lời dẫn + khối lệnh.
 */
const UPDATE_STALLED = {
  current: 'v0.1.48',
  latest: 'v0.1.49',
  update_available: true,
  updater: 'systemd',
  linked: true,
  can_request: true,
  state: 'stalled',
  stalled_reason: 'linger_off',
  message: null,
  from: 'v0.1.48',
  to: 'v0.1.49',
  started_at: null,
  finished_at: null,
  requested_at: new Date(Date.now() - 20 * 60_000).toISOString(),
  release_url: null,
  release_notes: null,
  published_at: null,
  auto_update_enabled: true,
};

/** Khối `nightly` của /system/health: lịch đêm im 3 ngày, linger tắt (thẻ Sức khoẻ có dòng + hướng dẫn 3 lệnh, dải có sự cố). */
const NIGHTLY_WARN = {
  state: 'warn',
  last_run_at: new Date(Date.now() - 3 * 24 * 3600_000).toISOString(),
  next_run_at: new Date(Date.now() + 14 * 3600_000).toISOString(),
  days_since: 3,
  opted_out: false,
  linger: 'no',
  checked_at: new Date(Date.now() - 4 * 60_000).toISOString(),
};

/** Các biến thể còn lại của thẻ cập nhật (v0.1.53) — mỗi biến thể một bố cục khác (câu nguyên nhân, lệnh, chi tiết kỹ thuật, gợi ý dài). */
const UPDATE_VARIANTS: Array<{ name: string; state: Record<string, unknown> }> = [
  { name: 'not_picked_up', state: { ...UPDATE_STALLED, stalled_reason: 'not_picked_up' } },
  { name: 'watcher_failed', state: { ...UPDATE_STALLED, stalled_reason: 'watcher_failed' } },
  {
    name: 'GH-E94C',
    state: {
      ...UPDATE_STALLED, state: 'failed', stalled_reason: null, finished_at: new Date(Date.now() - 5 * 60_000).toISOString(),
      message: 'Không xoá được tệp yêu cầu trong run/request nên chưa làm gì — kiểm quyền thư mục run/request rồi thử lại (GH-E94C)',
    },
  },
  {
    name: 'tự cài (ứng viên 25 giờ)',
    state: {
      ...UPDATE_STALLED, state: 'idle', stalled_reason: null, requested_at: null, latest: 'v0.1.50', auto_update_enabled: true,
      published_at: new Date(Date.now() - 3600_000).toISOString(),
      nightly_candidates: [
        { tag: 'v0.1.49', eligible_at: new Date(Date.now() - 3600_000).toISOString() },
        { tag: 'v0.1.50', eligible_at: new Date(Date.now() + 23 * 3600_000).toISOString() },
      ],
    },
  },
];

function branchName(): string {
  const env = process.env.GITHUB_HEAD_REF || process.env.GITHUB_REF_NAME;
  if (env) return env.replace(/[^\w.-]+/g, '-');
  try {
    return execSync('git rev-parse --abbrev-ref HEAD', { stdio: ['ignore', 'pipe', 'ignore'] })
      .toString()
      .trim()
      .replace(/[^\w.-]+/g, '-');
  } catch {
    return 'local';
  }
}

const SHOT_DIR = join(process.env.LAYOUT_GUARD_DIR || join(resultsDir, 'layout-guard'), branchName());
const SHOT_ALL = process.env.LAYOUT_GUARD_SHOTS === 'all';
const slug = (route: string) => route.replace(/^\//, '').replace(/[^\w-]+/g, '_') || 'root';

/** Xoá ảnh lượt trước có tiền tố này — ảnh cũ không còn đúng, thư mục chỉ chứa bằng chứng của lượt này. */
function clearShots(prefix: string): void {
  mkdirSync(SHOT_DIR, { recursive: true });
  for (const f of readdirSync(SHOT_DIR)) if (f.startsWith(prefix)) rmSync(join(SHOT_DIR, f));
}

/** Chạy trong trang: trả mọi phát hiện trong gốc đo (bỏ header .hd). Giữ thuần JS — không đóng gói hàm ngoài vào. */
function scanLayout(rootSel: string): Finding[] {
  const out: Finding[] = [];
  const main = document.querySelector(rootSel);
  if (!main) return out;
  const header = main.querySelector('header.hd');
  const vw = document.documentElement.clientWidth;
  const MIN_GAP = 6;

  const r4 = (r: DOMRect | { x: number; y: number; width: number; height: number }) => ({
    x: Math.round(r.x),
    y: Math.round(r.y + window.scrollY),
    width: Math.round(r.width),
    height: Math.round(r.height),
  });
  const short = (el: Element): string => {
    const one = (e: Element) => {
      const cls = Array.from(e.classList).filter((c) => !/^(ph|ph-\w+)$/.test(c)).slice(0, 2);
      return e.tagName.toLowerCase() + (cls.length ? `.${cls.join('.')}` : '');
    };
    const p = el.parentElement;
    return p && p !== main ? `${one(p)} > ${one(el)}` : one(el);
  };
  const clean = (s: string) => s.replace(/\s+/g, ' ').trim().slice(0, 40);
  const transparent = (c: string) => c === 'transparent' || /rgba\([^)]*,\s*0\)$/.test(c);
  const skipped = (el: Element) => (header ? header.contains(el) : false);
  const round1 = (n: number) => Math.round(n * 10) / 10;

  const frameOf = new Map<Element, boolean>();
  const isFrame = (el: Element): boolean => {
    const hit = frameOf.get(el);
    if (hit !== undefined) return hit;
    let v = false;
    if (el === main) v = true;
    else {
      const cs = getComputedStyle(el);
      if (parseFloat(cs.borderLeftWidth) > 0 && cs.borderLeftStyle !== 'none') v = true;
      else if (parseFloat(cs.borderTopLeftRadius) > 0 && !transparent(cs.backgroundColor)) v = true;
    }
    frameOf.set(el, v);
    return v;
  };

  // ── (a) dính biên: hộp chữ thật (Range) so với khung gần nhất; chữ xếp dọc (cột bị ép) ──
  const walker = document.createTreeWalker(main, NodeFilter.SHOW_TEXT);
  const seen = new Set<string>();
  for (let n = walker.nextNode(); n; n = walker.nextNode()) {
    const text = (n.nodeValue ?? '').trim();
    if (text.length <= 1) continue;
    const parent = n.parentElement;
    if (!parent || skipped(parent)) continue;
    if (parent.closest('script,style,noscript,select,option,textarea,svg')) continue;
    if (!parent.checkVisibility({ visibilityProperty: true } as CheckVisibilityOptions)) continue;
    // Chữ chỉ dành cho trình đọc màn hình (.kit-sr / .sr-only: hộp 1×1, clip) không hiện ra ⇒ không thể "dính biên".
    if (parent.closest('.kit-sr, .sr-only, .visually-hidden')) continue;
    const range = document.createRange();
    range.selectNodeContents(n);
    const tr = range.getBoundingClientRect();
    if (tr.width <= 0 || tr.height <= 0) continue;
    const lines = Array.from(range.getClientRects()).filter((r) => r.width > 0 && r.height > 0);
    if (lines.length >= 3 && text.length >= 8 && Math.max(...lines.map((r) => r.width)) < 48) {
      const sel = short(parent);
      const key = `ep|${sel}|${clean(text)}`;
      if (!seen.has(key)) {
        seen.add(key);
        out.push({ kind: 'chu-bi-ep', selector: sel, text: `${clean(text)} (${lines.length} dòng)`, gap: round1(tr.width), rect: r4(tr) });
      }
    }
    let left = tr.left;
    let right = tr.right;
    let frame: Element | null = null;
    let fixed = false;
    // Phía chữ giáp mép một vùng CUỘN (overflow-x auto|scroll) còn nội dung khuất: không phải dính mép (xem dưới).
    let scrolledL = false;
    let scrolledR = false;
    for (let a: Element | null = parent; a; a = a.parentElement) {
      const cs = getComputedStyle(a);
      if (cs.position === 'fixed') {
        fixed = true;
        break;
      }
      if (cs.overflowX !== 'visible' && a !== main) {
        // chữ bị cắt bởi vùng này (ellipsis, cuộn ngang) — chỉ phần nhìn thấy (hộp nội dung) mới tính
        const cr = a.getBoundingClientRect();
        const cl = cr.left + a.clientLeft + parseFloat(cs.paddingLeft);
        const crr = cr.left + a.clientLeft + a.clientWidth - parseFloat(cs.paddingRight);
        // Mép của vùng cuộn mà bên kia còn nội dung khuất (bảng rộng cuộn ngang) là mép CUỘN, không phải mép khung: chữ
        // sát đó (hoặc bị nó cắt) chỉ là cột kế tiếp đang khuất — cuộn là ra. Phía đó không đo.
        if (cs.overflowX === 'auto' || cs.overflowX === 'scroll') {
          if (left < cl - 0.5 || a.scrollLeft > 1) scrolledL = true;
          if (right > crr + 0.5 || a.scrollLeft + a.clientWidth < a.scrollWidth - 1) scrolledR = true;
        }
        left = Math.max(left, cl);
        right = Math.min(right, crr);
      }
      if (isFrame(a)) {
        frame = a;
        break;
      }
    }
    if (fixed || !frame || right <= left) continue;
    const fr = frame.getBoundingClientRect();
    if (fr.width <= 48 && fr.height <= 48) continue; // avatar / huy hiệu chữ viết tắt căn giữa
    const gl = scrolledL ? Infinity : left - fr.left;
    const gr = scrolledR ? Infinity : fr.right - right;
    const gap = Math.min(gl, gr);
    if (gap < MIN_GAP) {
      const sel = `${short(frame)} » ${short(parent)} (${gl < gr ? 'trái' : 'phải'})`;
      const key = `${sel}|${clean(text)}`;
      if (seen.has(key)) continue;
      seen.add(key);
      out.push({ kind: 'dinh-bien', selector: sel, text: clean(text), gap: round1(gap), rect: r4(new DOMRect(left, tr.top, right - left, tr.height)) });
    }
  }

  // ── (b) tràn ngang ──
  const de = document.documentElement;
  if (de.scrollWidth > de.clientWidth + 1) {
    out.push({ kind: 'tran-ngang', selector: 'html', text: `trang cuộn ngang ${de.scrollWidth - de.clientWidth}px`, gap: de.clientWidth - de.scrollWidth, rect: r4(new DOMRect(0, 0, vw, 1)) });
  }
  const content = main.querySelector('.content') ?? main;
  const contentRight = content.getBoundingClientRect().left + content.clientLeft + content.clientWidth;
  const limit = Math.min(vw, contentRight) + 1;
  if (content.scrollWidth > content.clientWidth + 1 || de.scrollWidth > de.clientWidth + 1) {
    const culprits: Element[] = [];
    const visit = (el: Element) => {
      for (const c of Array.from(el.children)) {
        if (skipped(c)) continue;
        const r = c.getBoundingClientRect();
        if (r.width > 0 && r.right > limit) {
          const cs = getComputedStyle(c);
          // con của c còn tràn hơn c? đi xuống tìm phần tử ngoài cùng gây tràn
          if (cs.overflowX === 'visible' && Array.from(c.children).some((g) => g.getBoundingClientRect().right > limit && g.getBoundingClientRect().right >= r.right - 1)) visit(c);
          else culprits.push(c);
        } else if (getComputedStyle(c).overflowX === 'visible') visit(c);
      }
    };
    visit(content);
    for (const c of culprits.slice(0, 5)) {
      const r = c.getBoundingClientRect();
      out.push({ kind: 'tran-ngang', selector: short(c), text: clean(c.textContent ?? ''), gap: Math.round(limit - 1 - r.right), rect: r4(r) });
    }
    if (!culprits.length && content.scrollWidth > content.clientWidth + 1) {
      out.push({ kind: 'tran-ngang', selector: '.content', text: `vùng nội dung cuộn ngang ${content.scrollWidth - content.clientWidth}px`, gap: content.clientWidth - content.scrollWidth, rect: r4(content.getBoundingClientRect()) });
    }
  }

  // ── (c) chữ một dòng bị cắt / bị ép, vùng bị cắt mép, vùng cuộn ẩn; (d) icon rớt dòng / lệch trong khung chỉ-icon ──
  const onlyIcon = (e: Element) => e.children.length === 1 && !(e.textContent ?? '').trim();
  for (const el of Array.from(main.querySelectorAll('*'))) {
    if (skipped(el)) continue;
    if (el.closest('select,textarea,input,option')) continue;
    if (el.hasAttribute('data-icon')) {
      const p = el.parentElement;
      if (!p) continue;
      const ir = el.getBoundingClientRect();
      if (ir.width <= 0) continue;
      const pd = getComputedStyle(p).display;
      const name = el.getAttribute('data-icon');
      if (getComputedStyle(el).display === 'block' && !/flex|grid/.test(pd) && (p.textContent ?? '').trim().length > 1) {
        out.push({ kind: 'icon-rot-dong', selector: `${short(p)} > [icon ${name}]`, text: clean(p.textContent ?? ''), gap: 0, rect: r4(p.getBoundingClientRect()) });
      }
      // Khung chỉ-icon: đi lên qua các span inline chỉ bọc mỗi icon tới hộp khối đầu tiên (ô bảng: chiều cao theo hàng — bỏ).
      if (onlyIcon(p)) {
        let box: Element = p;
        let bd = pd;
        while (bd === 'inline' && box.parentElement && box.parentElement !== main && onlyIcon(box.parentElement)) {
          box = box.parentElement;
          bd = getComputedStyle(box).display;
        }
        if (/^(block|inline-block|list-item|flow-root)$/.test(bd)) {
          const bcs = getComputedStyle(box);
          const inner = (box as HTMLElement).clientHeight - parseFloat(bcs.paddingTop) - parseFloat(bcs.paddingBottom);
          if (inner > ir.height + 2) {
            out.push({ kind: 'icon-lech', selector: `${short(box)} > [icon ${name}]`, text: `khung ${round1(inner)}px, icon ${round1(ir.height)}px`, gap: round1(ir.height - inner), rect: r4(box.getBoundingClientRect()) });
          }
        }
      }
      continue;
    }
    if (!(el instanceof HTMLElement)) continue;
    const he = el;
    if (he.clientWidth < 4) continue;
    const cs = getComputedStyle(he);
    const over = he.scrollWidth - he.clientWidth;
    if (cs.overflowX === 'auto' || cs.overflowX === 'scroll') {
      const hidden = cs.getPropertyValue('scrollbar-width') === 'none';
      const faded = cs.getPropertyValue('mask-image') !== 'none' || (cs.getPropertyValue('-webkit-mask-image') || 'none') !== 'none';
      if (over > 1 && hidden && !faded) {
        out.push({ kind: 'cuon-an', selector: short(he), text: `khuất ${over}px, ẩn thanh cuộn, không mép mờ`, gap: -over, rect: r4(he.getBoundingClientRect()) });
      }
      continue;
    }
    if ((cs.overflowX === 'hidden' || cs.overflowX === 'clip') && cs.textOverflow !== 'ellipsis' && over > 2) {
      const r = he.getBoundingClientRect();
      const cut = Array.from(he.children).find((c) => c.getBoundingClientRect().right > r.right + 1);
      out.push({ kind: 'bi-cat-khung', selector: cut ? `${short(he)} ⊃ ${short(cut)}` : short(he), text: clean((cut ?? he).textContent ?? ''), gap: -over, rect: r4(r) });
      continue;
    }
    if (cs.whiteSpace !== 'nowrap' && cs.textOverflow !== 'ellipsis') continue;
    if (!(he.textContent ?? '').trim() || over <= 2) continue;
    if (!he.closest('[title]')) {
      out.push({ kind: 'chu-bi-cat', selector: short(he), text: clean(he.textContent ?? ''), gap: -over, rect: r4(he.getBoundingClientRect()) });
    } else if (he.clientWidth < 64 && he.clientWidth < he.scrollWidth * 0.55) {
      out.push({ kind: 'chu-bi-ep', selector: short(he), text: `${clean(he.textContent ?? '')} (thấy ${he.clientWidth}/${he.scrollWidth}px)`, gap: -over, rect: r4(he.getBoundingClientRect()) });
    }
  }
  return out;
}

async function settled(page: Page, root = 'main'): Promise<void> {
  await page.locator(root).first().waitFor();
  await page
    .waitForFunction(() => !document.querySelector('main .gh-skeleton, main [aria-busy="true"]'), undefined, { timeout: 5_000 })
    .catch(() => undefined);
  await page.evaluate(() => document.fonts.ready.then(() => undefined));
  await page.waitForTimeout(120);
}

/** Nhập PIN khi hộp PIN tự mở (Đánh giá con người đòi phiên PIN cho mọi lượt đọc). */
async function pinIfAsked(page: Page): Promise<void> {
  const dlg = page.getByRole('dialog', { name: 'Mã PIN xác nhận thao tác' });
  if (!(await dlg.isVisible().catch(() => false))) return;
  await page.getByLabel('Mã PIN — chữ số 1/6').click();
  await page.keyboard.type(OWNER.pin);
  await expect(dlg).toBeHidden();
  await settled(page);
}

/** Điều hướng phía trình duyệt (router lắng nghe popstate) — nhanh hơn tải lại cả trang dev. */
async function go(page: Page, route: string): Promise<void> {
  await page.evaluate((url) => {
    window.history.pushState({}, '', url);
    window.dispatchEvent(new PopStateEvent('popstate'));
  }, route);
  // Không đợi URL khớp tuyệt đối: vài route tự chuyển hướng (vd tab đã dời chỗ) — đợi vẽ xong là đủ.
  await settled(page);
  await pinIfAsked(page);
}

/** Đo trang đang mở; chỉ chụp ảnh khi có phát hiện (hoặc LAYOUT_GUARD_SHOTS=all). `prefix` tách ảnh của từng test. */
async function scanHere(page: Page, prefix: string, w: number, route: string, root = 'main'): Promise<Hit[]> {
  await page.evaluate(() => window.scrollTo(0, 0));
  const found = (await page.evaluate(scanLayout, root)).filter(
    (f) => !ALLOWLIST.some((a) => a.kind === f.kind && f.selector.includes(a.selector) && (!a.route || a.route === route)),
  );
  const base = `${prefix}${w}_${slug(route)}`;
  if (found.length || SHOT_ALL) await page.screenshot({ path: join(SHOT_DIR, `${base}.png`), fullPage: true });
  for (const [i, f] of found.slice(0, 8).entries()) {
    const pageH = await page.evaluate(() => document.documentElement.scrollHeight);
    const x = Math.max(0, f.rect.x - 40);
    const y = Math.max(0, f.rect.y - 40);
    const width = Math.min(w - x, f.rect.width + 80);
    const height = Math.min(pageH - y, f.rect.height + 80);
    if (width > 0 && height > 0) {
      await page.screenshot({ path: join(SHOT_DIR, `${base}__zoom${i + 1}.png`), fullPage: true, clip: { x, y, width, height } }).catch(() => undefined);
    }
  }
  return found.map((f) => ({ ...f, route, w }));
}

/** In bảng phát hiện gom theo (loại, route, selector) kèm các cỡ màn, rồi đòi rỗng. */
function expectClean(all: Hit[]): void {
  if (all.length) {
    const groups = new Map<string, { kind: string; route: string; selector: string; text: string; gap: number; ws: Set<number> }>();
    for (const f of all) {
      const k = `${f.kind}|${f.route}|${f.selector}`;
      const g = groups.get(k) ?? { kind: f.kind, route: f.route, selector: f.selector, text: f.text, gap: f.gap, ws: new Set<number>() };
      g.ws.add(f.w);
      g.gap = Math.min(g.gap, f.gap);
      groups.set(k, g);
    }
    const rows = [...groups.values()].map((g) =>
      [g.kind.padEnd(13), g.route.padEnd(28), [...g.ws].join('/').padEnd(19), String(g.gap).padStart(6), ` ${g.selector}  «${g.text}»`].join(' '),
    );
    console.log(
      `\nLính gác bố cục: ${groups.size} phát hiện (${all.length} lượt) — ảnh: ${SHOT_DIR}\n` +
        `${'loại'.padEnd(13)} ${'route'.padEnd(28)} ${'cỡ màn'.padEnd(19)} ${'gap'.padStart(6)}  selector «chữ»\n` +
        rows.join('\n'),
    );
  }
  expect(all.map((f) => `${f.kind} ${f.route} @${f.w}: ${f.selector} «${f.text}» gap=${f.gap}`)).toEqual([]);
}

test.describe('Lính gác bố cục — dính biên, tràn/cắt ngang, chữ bị cắt/ép, cuộn ẩn, icon rớt dòng/lệch', () => {
  for (const vp of VIEWPORTS) {
    test(`mọi màn @${vp.width}px không có phát hiện`, async ({ page }) => {
      test.setTimeout(180_000);
      clearShots(`${vp.width}_`);
      await resetMock(page.request, 'finished');
      await mockHook(page.request, 'health', { issues: [{ kind: 'backup.stale' }, { kind: 'channel.down' }], nightly: NIGHTLY_WARN });
      await loginAsOwner(page);
      await page.route('**/api/v1/system/update', (route) =>
        route.request().method() === 'GET' ? route.fulfill({ json: UPDATE_STALLED }) : route.fallback(),
      );
      await page.setViewportSize(vp);
      await page.goto('/directory?dt=people');
      await settled(page);
      const profileHref = await page.locator('main a[href*="/profile?id="]').first().getAttribute('href');
      // Hồ sơ đầu danh sách + hồ sơ mock đủ dữ liệu (tóm tắt, dòng sự kiện, tài liệu) — p-bao.
      const routes = [...SCREEN_ROUTES, ...EXTRA_ROUTES, ...new Set([...(profileHref ? [profileHref] : []), '/profile?id=p-bao'])];

      const all: Hit[] = [];
      for (const route of routes) {
        const t0 = Date.now();
        await go(page, route);
        const found = await scanHere(page, '', vp.width, route);
        all.push(...found);
        if (process.env.LAYOUT_GUARD_VERBOSE) console.log(`${vp.width} ${route} ${found.length} phát hiện ${Date.now() - t0}ms`);
      }
      expectClean(all);
    });
  }

  for (const vp of VIEWPORTS) {
    test(`thẻ cập nhật — biến thể nguyên nhân/lỗi/gợi ý @${vp.width}px không có phát hiện`, async ({ page }) => {
      test.setTimeout(120_000);
      clearShots(`cn${vp.width}_`);
      await resetMock(page.request, 'finished');
      await mockHook(page.request, 'health', { nightly: NIGHTLY_WARN });
      await loginAsOwner(page);
      let state: Record<string, unknown> = UPDATE_STALLED;
      await page.route('**/api/v1/system/update', (route) =>
        route.request().method() === 'GET' ? route.fulfill({ json: state }) : route.fallback(),
      );
      await page.setViewportSize(vp);
      const all: Hit[] = [];
      for (const variant of UPDATE_VARIANTS) {
        state = variant.state;
        for (const route of ['/overview', '/system?tab=storage']) {
          await page.goto(route);
          await settled(page);
          all.push(...(await scanHere(page, `cn${vp.width}_${variant.name.replace(/\W+/g, '')}_`, vp.width, `${route} [${variant.name}]`)));
        }
      }
      expectClean(all);
    });
  }

  test('trang ngoài (Đăng nhập, Thiết lập 3 bước, /setup?buoc=nguon-ai) × 1920/1440/1024/390 không có phát hiện', async ({ page }) => {
    test.setTimeout(120_000);
    clearShots('ngoai_');
    const all: Hit[] = [];
    // Đăng nhập — chưa có phiên (gốc đo `.login`, trang không có <main>).
    await resetMock(page.request, 'finished');
    await page.context().clearCookies();
    await page.goto('/login');
    for (const vp of VIEWPORTS) {
      await page.setViewportSize(vp);
      await settled(page, '.login');
      all.push(...(await scanHere(page, 'ngoai_', vp.width, '/login', '.login')));
    }
    // v0.1.59: Thiết lập MỘT màn, 3 bước — mock "fresh". Tài khoản (chưa gõ gì, "Tuỳ chỉnh" mở để đo cả phần ẩn).
    await resetMock(page.request, 'fresh');
    await page.goto('/setup');
    await expect(page.getByRole('heading', { name: 'Tạo tài khoản Sếp' })).toBeVisible();
    await page.getByText('Tuỳ chỉnh').click();
    for (const vp of VIEWPORTS) {
      await page.setViewportSize(vp);
      await settled(page);
      all.push(...(await scanHere(page, 'ngoai_', vp.width, '/setup (tài khoản)')));
    }
    // Nguồn AI (thẻ Antigravity chưa đăng nhập, "Nâng cao" mở: Claude Code CLI + tương thích OpenAI) — bước 1–3 qua API.
    await apiCall(page, 'PUT', '/setup/steps/1', { token: SETUP_TOKEN, language: 'vi', mode: 'empty' });
    await apiCall(page, 'PUT', '/setup/steps/2', {
      token: SETUP_TOKEN, display_name: 'Anh Cơ', email: 'owner@example.test', password: 'mot-cau-rat-dai-de-nho-2026', pin: OWNER.pin, pin_confirm: OWNER.pin,
    });
    await apiCall(page, 'PUT', '/setup/steps/3', { org_name: 'Genesis Trading', timezone: 'Asia/Ho_Chi_Minh', currency: 'VND', self_name: 'Anh', bot_calls_me: 'Sếp' });
    await page.goto('/setup');
    await expect(page.getByRole('heading', { name: 'Nối nguồn AI cho Gen' })).toBeVisible();
    await page.getByText('Nâng cao', { exact: true }).click();
    for (const vp of VIEWPORTS) {
      await page.setViewportSize(vp);
      await settled(page);
      all.push(...(await scanHere(page, 'ngoai_', vp.width, '/setup (nguồn AI)')));
    }
    // Xong — bước 4 để sau (máy chủ ghi "skipped") ⇒ /setup tới thẳng bước Xong.
    await apiCall(page, 'POST', '/setup/steps/4/skip');
    await page.goto('/setup');
    await expect(page.getByRole('heading', { name: 'Xong rồi, thưa Sếp' })).toBeVisible();
    for (const vp of VIEWPORTS) {
      await page.setViewportSize(vp);
      await settled(page);
      all.push(...(await scanHere(page, 'ngoai_', vp.width, '/setup (xong)')));
    }
    // v0.1.59: đích duy nhất của "Chọn model" là /setup?buoc=nguon-ai — đã xong vẫn mở được bước Nguồn AI. Allowlist rỗng.
    await page.goto('/setup?buoc=nguon-ai');
    await expect(page.getByRole('heading', { name: 'Nối nguồn AI cho Gen' })).toBeVisible();
    for (const vp of VIEWPORTS) {
      await page.setViewportSize(vp);
      await settled(page);
      all.push(...(await scanHere(page, 'ngoai_', vp.width, '/setup?buoc=nguon-ai')));
    }
    expectClean(all);
  });
});

/**
 * v0.1.54 (Gen hướng dẫn) — thẻ "Hôm nay của Sếp" MỞ RỘNG (đủ 3 khối: việc cần làm ngay, Sếp biết chưa?, Bài học hôm nay,
 * có cả việc khẩn P0 và việc có cảnh báo dài) và HỘP CẢNH BÁO "Không dùng việc này" ở 1440 và 390: khung Gen (gốc đo
 * `.gen-panel`) và hộp thoại (gốc đo `.gh-dialog`) không có phát hiện, vùng cuộn của khung không cuộn ngang.
 */
test.describe('Gen hướng dẫn — thẻ mở rộng và hộp cảnh báo', () => {
  for (const vp of [{ width: 1440, height: 900 }, { width: 390, height: 844 }] as const) {
    test(`thẻ + hộp cảnh báo @${vp.width}px không có phát hiện`, async ({ page }) => {
      test.setTimeout(90_000);
      const prefix = `coach_${vp.width}_`;
      clearShots(prefix);
      await resetMock(page.request, 'finished');
      await page.request.post('/api/v1/__mock/p3/genCoach/scenario', { data: { extras: ['health.channel.down', 'backup.unset'] } });
      await loginAsOwner(page);
      await page.setViewportSize(vp);
      await page.goto('/overview');
      await page.getByRole('button', { name: /Hỏi Gen/ }).click();
      const card = page.getByRole('region', { name: 'Hôm nay của Sếp' });
      await expect(card).toBeVisible();
      await expect(card.getByRole('group', { name: 'Việc cần làm ngay' })).toBeVisible();
      await expect(card.getByRole('group', { name: 'Sếp biết chưa?' })).toBeVisible();
      await expect(card.getByRole('group', { name: 'Bài học hôm nay · 1/19' })).toBeVisible();
      await expect(card.getByTestId('coach-todo')).toHaveCount(3);
      await settled(page, '.gen-panel');
      const all: Hit[] = [...(await scanHere(page, prefix, vp.width, '/overview (thẻ Hôm nay của Sếp)', '.gen-panel'))];
      // Vùng cuộn của khung không được cuộn ngang (thẻ không tràn ở 390px).
      const over = await page.evaluate(() => {
        const list = document.querySelector('.gen-panel__list') as HTMLElement | null;
        return list ? list.scrollWidth - list.clientWidth : 0;
      });
      expect(over, 'khung Gen không cuộn ngang').toBeLessThanOrEqual(1);

      // Hộp cảnh báo của việc có cảnh báo (backup.unset).
      await card.getByTestId('coach-todo').filter({ hasText: 'sao lưu' }).getByRole('button', { name: 'Không dùng việc này' }).click();
      const dlg = page.getByRole('dialog', { name: /Không dùng việc này\?/ });
      await expect(dlg).toBeVisible();
      await expect(dlg.getByTestId('coach-dismiss-warning')).toBeVisible();
      await page.waitForTimeout(150);
      all.push(...(await scanHere(page, prefix, vp.width, '/overview (hộp cảnh báo)', '.gh-dialog')));
      const dialogOver = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
      expect(dialogOver, 'trang không cuộn ngang khi hộp mở').toBeLessThanOrEqual(0);
      expectClean(all);
    });
  }
});
