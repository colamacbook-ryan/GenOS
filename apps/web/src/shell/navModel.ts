import type { NavDomain, NavItem, Tone } from '@gen-harness/contracts';

/**
 * Pure sidebar logic, ported from the design's `renderVals()` (navDomains).
 * Rendering only what GET /navigation returns is how role filtering works.
 */

export interface ActiveLocation {
  domain: NavDomain;
  /** Level-1 entry that is or contains the active screen. */
  group: NavItem;
  /** The active screen's own entry. */
  item: NavItem;
  /** true when the screen is a child of `group`. */
  isChild: boolean;
}

export function findActive(nav: NavDomain[] | undefined, key: string | null | undefined): ActiveLocation | null {
  if (!nav || !key) return null;
  for (const domain of nav) {
    for (const group of domain.groups) {
      if (group.key === key) return { domain, group, item: group, isChild: false };
      const child = group.children?.find((c) => c.key === key);
      if (child) return { domain, group, item: child, isChild: true };
    }
  }
  return null;
}

/** Every screen key present in the (role-filtered) tree. */
export function screenKeys(nav: NavDomain[] | undefined): Set<string> {
  const out = new Set<string>();
  nav?.forEach((d) =>
    d.groups.forEach((g) => {
      if (g.key) out.add(g.key);
      g.children?.forEach((c) => c.key && out.add(c.key));
    }),
  );
  return out;
}

/** v0.1.42: con KHÔNG ẩn (node `hidden: true` có route nhưng không hiện thanh bên). */
export function visibleChildren(g: NavItem): NavItem[] {
  return (g.children ?? []).filter((c) => !c.hidden);
}

export function hasChildren(g: NavItem): boolean {
  return visibleChildren(g).length > 0;
}

/**
 * v0.1.42: các mục cấp 1 hiện trên thanh bên — bỏ node ẩn; nhóm thuần chỉ còn con ẩn thì bỏ luôn.
 */
export function visibleGroups(dm: NavDomain): NavItem[] {
  return dm.groups.filter((g) => !g.hidden && (g.key ? true : hasChildren(g)));
}

/**
 * v0.1.42 (F-26): màn đầu tiên KHÔNG ẩn trong cây vai trò được thấy — duyệt domain → mục cấp 1: khoá của mục trước,
 * rồi con không ẩn đầu tiên. `null` khi vai trò chưa được cấp màn nào (trang chủ "/" chuyển về đây).
 */
export function firstScreenKey(nav: NavDomain[] | undefined): string | null {
  for (const d of nav ?? []) {
    for (const g of d.groups) {
      if (g.hidden) continue;
      if (g.key) return g.key;
      const kid = visibleChildren(g).find((c) => !!c.key);
      if (kid?.key) return kid.key;
    }
  }
  return null;
}

/**
 * v0.1.42: domain thu gọn (Nâng cao) mở hay đóng — người dùng bấm thì theo lựa chọn đó (không lưu, mở lại trang là
 * thu gọn); chưa bấm thì mở khi domain không thu gọn mặc định hoặc màn đang mở thuộc domain này.
 */
export function domainOpen(dm: NavDomain, activeKey: string | null | undefined, override: boolean | undefined): boolean {
  if (!dm.collapsed) return true;
  if (override !== undefined) return override;
  return !!activeKey && dm.groups.some((g) => g.key === activeKey || !!g.children?.some((c) => c.key === activeKey));
}

/** Group key used for the open/closed override map (design uses the label). */
export const groupId = (g: NavItem): string => g.name;

export interface GroupView {
  /** The entry is the active screen itself. */
  self: boolean;
  /** One of its children is the active screen. */
  activeKid: boolean;
  /** Draw the 2px indicator + bright label. */
  on: boolean;
  /** Children are shown. */
  open: boolean;
  showCaret: boolean;
}

/**
 * Design: `open = wide && kids ? (navOpen[label] ?? (activeKid || self)) : false`.
 * A group containing the active screen expands by itself unless the user
 * explicitly collapsed it.
 */
export function groupView(
  g: NavItem,
  activeKey: string | null | undefined,
  navOpen: Record<string, boolean>,
  wide: boolean,
): GroupView {
  const kids = hasChildren(g);
  const self = !!g.key && g.key === activeKey;
  // Màn ẩn đang mở (vd. Hồ sơ sống) vẫn tô sáng nhóm cha.
  const activeKid = !!activeKey && !!g.children?.some((c) => c.key === activeKey);
  const open = wide && kids ? (navOpen[groupId(g)] ?? (activeKid || self)) : false;
  return { self, activeKid, on: self || activeKid, open, showCaret: wide && kids };
}

export type GroupAction =
  | { type: 'navigate'; key: string }
  | { type: 'toggle'; group: string; open: boolean }
  | { type: 'none' };

/**
 * Design `act`:
 * - an entry that is itself a screen (overview, Bản đồ quan hệ…) opens it — and
 *   since it becomes active, its children expand;
 * - a pure group toggles in full mode (from its current auto state);
 * - in rail mode a pure group opens its first child.
 */
export function groupAction(
  g: NavItem,
  activeKey: string | null | undefined,
  navOpen: Record<string, boolean>,
  wide: boolean,
): GroupAction {
  if (g.key) return { type: 'navigate', key: g.key };
  const kids = visibleChildren(g);
  if (!kids.length) return { type: 'none' };
  if (wide) {
    const activeKid = !!activeKey && !!g.children?.some((c) => c.key === activeKey);
    const current = navOpen[groupId(g)] ?? activeKid;
    return { type: 'toggle', group: groupId(g), open: !current };
  }
  const first = kids.find((c) => !!c.key);
  return first?.key ? { type: 'navigate', key: first.key } : { type: 'none' };
}

/** Domain label colour: Việc hằng ngày = OK green, Nâng cao = accent-400 (docs/01). */
export function domainColor(tone: Tone | string | undefined): string {
  switch (tone) {
    case 'ok':
      return 'var(--color-domain-business)';
    case 'accent':
      return 'var(--color-domain-tech)';
    case 'warn':
      return 'var(--color-warn)';
    case 'bad':
      return 'var(--color-bad)';
    default:
      return 'var(--color-neutral-500)';
  }
}

/** Tooltip / title text: "name — en" (design `railLeaf.title`). */
export function itemTitle(it: NavItem): string {
  return it.en ? `${it.name} — ${it.en}` : it.name;
}
