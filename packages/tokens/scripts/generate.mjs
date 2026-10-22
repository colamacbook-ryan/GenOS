#!/usr/bin/env node
// Generates packages/tokens/tokens.css and packages/tokens/src/index.ts from
//   docs/design/tokens.json                       (product tokens, DTCG format)
//   docs/design/_ds/nocturne-*/styles.css :root   (Nocturne base ramps, spacing, radii, shadows)
// plus a small, documented set of values the Console design uses inline that
// have no token of their own yet (see EXTRAS). Components must reference the
// resulting CSS variables only — never raw hex.
//
// Run: npm run generate -w @gen-harness/tokens
import { readFileSync, readdirSync, writeFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const pkg = resolve(here, '..');
const repo = resolve(pkg, '../..');
const designDir = join(repo, 'docs/design');

const tokensJson = JSON.parse(readFileSync(join(designDir, 'tokens.json'), 'utf8'));
const dsDir = readdirSync(join(designDir, '_ds')).find((d) => d.startsWith('nocturne-'));
if (!dsDir) throw new Error('Nocturne design system folder not found in docs/design/_ds');
const nocturneCss = readFileSync(join(designDir, '_ds', dsDir, 'styles.css'), 'utf8');

// ── 1. Nocturne :root variables (verbatim order) ────────────────────────────
const rootBlock = /:root\s*\{([\s\S]*?)\n\}/.exec(nocturneCss);
if (!rootBlock) throw new Error('No :root block in Nocturne styles.css');
const nocturne = new Map();
const declRe = /(--[\w-]+)\s*:\s*([^;]+);/g;
const stripComments = rootBlock[1].replace(/\/\*[\s\S]*?\*\//g, '');
for (const m of stripComments.matchAll(declRe)) nocturne.set(m[1], m[2].trim());
// The Console mono accent drops the "accent-2" stand-in roles and the deck-only
// section grounds; they are kept for completeness but not used by components.

// ── 2. Product tokens from tokens.json ──────────────────────────────────────
const v = (node) => (node && typeof node === 'object' && '$value' in node ? node.$value : node);
const c = tokensJson.color;
const product = new Map([
  ['--color-bg', v(c.bg)],
  ['--rail-bg', v(c.railBg)],
  ['--color-surface', v(c.surface)],
  ['--surface-raised', v(c.surfaceRaised)],
  ['--row-hover', v(c.rowHover)],
  ['--nav-active', v(c.navActive)],
  ['--nav-hover', v(c.navHover)],
  // tokens.json gives the divider as rgba(); Nocturne writes the same colour
  // with color-mix(). Keep the product value so it renders identically everywhere.
  ['--color-divider', v(c.divider)],
  ['--color-text', v(c.text)],
  ['--color-accent', v(c.accent.base)],
  ['--color-ok', v(c.status.ok)],
  ['--color-warn', v(c.status.warn)],
  ['--color-bad', v(c.status.bad)],
  ['--color-ok-tint', v(c.status.okTint)],
  ['--color-warn-tint', v(c.status.warnTint)],
  ['--color-bad-tint', v(c.status.badTint)],
  ['--color-domain-business', v(c.domain.business)],
  ['--color-domain-tech', v(c.domain.tech)],
  ['--font-sans', v(tokensJson.font.sans)],
  ['--font-mono', v(tokensJson.font.mono)],
]);
for (const [step, node] of Object.entries(c.neutral)) product.set(`--color-neutral-${step}`, v(node));
for (const [step, node] of Object.entries(c.accent)) if (step !== 'base') product.set(`--color-accent-${step}`, v(node));
const kebab = (s) => s.replace(/[A-Z]/g, (ch) => '-' + ch.toLowerCase());
for (const [k, node] of Object.entries(tokensJson.fontSize)) product.set(`--font-size-${kebab(k)}`, v(node));
for (const [k, val] of Object.entries(tokensJson.space)) product.set(`--space-${kebab(k)}`, val);
for (const [k, val] of Object.entries(tokensJson.radius)) product.set(`--radius-${k}`, val);
for (const [k, val] of Object.entries(tokensJson.shadow)) product.set(`--shadow-${k}`, val);
product.set('--sidebar-full', tokensJson.layout.sidebarFull);
product.set('--sidebar-rail', tokensJson.layout.sidebarRail);
product.set('--header-height', tokensJson.layout.header);
product.set('--motion-live-duration', tokensJson.motion.livePulse.duration);
product.set('--motion-live-easing', tokensJson.motion.livePulse.easing);

// ── 3. Extras: literal values the Console design uses inline ────────────────
// Each is copied from docs/design/Gen-Harness Console.dc.html and named here so
// components never write the literal.
const EXTRAS = [
  ['--rule', '#2f3242', 'decorative faded rule (logo divider, domain label rule) — docs/02 "Đường kẻ trang trí"'],
  ['--rule-nav-children', '#2a2d3c', 'vertical 1px rule left of nav children'],
  ['--rule-fade', 'linear-gradient(90deg, transparent, var(--rule) 22%, var(--rule) 78%, transparent)', 'the full faded rule'],
  ['--color-warn-icon', 'oklch(0.79 0.12 82)', 'data-confidence shield icon in the header pill'],
  ['--glow-accent', '0 0 18px -6px var(--color-accent)', 'logo tile glow'],
  ['--radius-nav', '7px', 'level-1 nav item radius'],
  ['--radius-nav-child', '6px', 'nav child radius'],
  ['--font-size-nav', '12px', 'level-1 nav item'],
  ['--font-size-nav-child', '11.5px', 'nav child'],
  ['--font-size-domain', '9.5px', 'domain label'],
  ['--font-size-screen-desc', '12.5px', 'screen-title description'],
  ['--scrollbar-thumb', 'var(--color-neutral-800)', 'webkit scrollbar thumb'],
];

// ── 3b. Mặt tiền kit (v0.1.59, G2): chữ 12/14/18/28, lưới 8pt, bán kính, bố cục, màu ngữ nghĩa sáng/tối ─────
// Chỉ THÊM biến mới (tiền tố --fs- --lh- --fw- --sp- --r- và tên màu ngữ nghĩa); không đổi/xoá biến cũ. Phần không đổi theo
// giao diện (chữ, lưới, bố cục) vào :root; màu có cặp sáng/tối: TỐI là mặc định trong :root, SÁNG ghi đè trong
// :root[data-theme='light'] — cùng cơ chế với apps/web/src/styles/theme.css.
const kit = tokensJson.kit;
if (!kit) throw new Error('docs/design/tokens.json thiếu nhóm "kit"');
const kitStatic = new Map();
for (const [k, val] of Object.entries(kit.fontSize)) kitStatic.set(`--fs-${k}`, val);
for (const [k, val] of Object.entries(kit.lineHeight)) kitStatic.set(`--lh-${k}`, val);
for (const [k, val] of Object.entries(kit.fontWeight)) kitStatic.set(`--fw-${k}`, val);
for (const [k, val] of Object.entries(kit.space)) kitStatic.set(`--sp-${k}`, val);
for (const [k, val] of Object.entries(kit.radius)) kitStatic.set(`--r-${k}`, val);
for (const [k, val] of Object.entries(kit.layout)) kitStatic.set(`--${kebab(k)}`, val);
const kitDark = new Map();
const kitLight = new Map();
const pair = (name, node) => {
  if (typeof node?.dark !== 'string' || typeof node?.light !== 'string') throw new Error(`kit.color ${name}: cần cả "dark" và "light"`);
  kitDark.set(`--${name}`, node.dark);
  kitLight.set(`--${name}`, node.light);
};
for (const [step, node] of Object.entries(kit.color.neutral)) pair(`neutral-${step}`, node);
for (const group of ['semantic', 'surface']) for (const [name, node] of Object.entries(kit.color[group])) pair(name, node);
// Vai trò (bg, text, border…) là bí danh của thang neutral — giống nhau ở hai giao diện, nên chỉ cần khai báo ở :root.
for (const [name, target] of Object.entries(kit.color.alias)) {
  if (!kitDark.has(`--${target}`)) throw new Error(`kit.color.alias ${name}: không có ${target}`);
  kitStatic.set(`--${name}`, `var(--${target})`);
}
const BREAKPOINTS = { md: kit.breakpoint.md, lg: kit.breakpoint.lg };

// ── 4. Merge: Nocturne first, product overrides, extras last ────────────────
const merged = new Map(nocturne);
merged.set('--color-divider', product.get('--color-divider'));
for (const [k, val] of product) merged.set(k, val);

const header = `/* GENERATED by packages/tokens/scripts/generate.mjs — do not edit by hand.
 * Sources: docs/design/tokens.json · docs/design/_ds/${dsDir}/styles.css
 * Rule (docs/handoff/02): components reference these variables only, never raw hex.
 * --color-neutral-700 is for icons, carets and borders only — never text (2.3:1).
 */
`;
let css = header + ':root {\n';
for (const [k, val] of merged) css += `  ${k}: ${val};\n`;
css += '\n  /* Console extras — literal values from the design, named */\n';
for (const [k, val, note] of EXTRAS) css += `  ${k}: ${val}; /* ${note} */\n`;
css += '\n  /* Mặt tiền kit (v0.1.59) — chữ 12/14/18/28 · lưới 8pt · bán kính · bố cục: không đổi theo giao diện */\n';
for (const [k, val] of kitStatic) css += `  ${k}: ${val};\n`;
css += '\n  /* Mặt tiền kit — màu ngữ nghĩa, bản TỐI (mặc định) */\n';
for (const [k, val] of kitDark) css += `  ${k}: ${val};\n`;
css += '}\n\n/* Mặt tiền kit — màu ngữ nghĩa, bản SÁNG (data-theme đặt trên <html> bởi lib/theme.ts) */\n:root[data-theme=\'light\'] {\n';
for (const [k, val] of kitLight) css += `  ${k}: ${val};\n`;
css += '}\n';
writeFileSync(join(pkg, 'tokens.css'), css);

// ── 5. TS export ────────────────────────────────────────────────────────────
const all = [...merged.entries(), ...EXTRAS.map(([k, val]) => [k, val]), ...kitStatic.entries(), ...kitDark.entries()];
const toCamel = (name) => name.replace(/^--/, '').replace(/-([a-z0-9])/g, (_, ch) => ch.toUpperCase());
let ts = `// GENERATED by packages/tokens/scripts/generate.mjs — do not edit by hand.\n`;
ts += `// Raw token values (for canvas/SVG code that cannot read CSS variables) and\n// helpers that return var() references for inline styles.\n\n`;
ts += 'export const tokenValues = {\n';
for (const [k, val] of all) ts += `  ${JSON.stringify(toCamel(k))}: ${JSON.stringify(val)},\n`;
ts += '} as const;\n\n';
ts += 'export const tokenNames = {\n';
for (const [k] of all) ts += `  ${JSON.stringify(toCamel(k))}: ${JSON.stringify(k)},\n`;
ts += '} as const;\n\n';
ts += `export type TokenName = keyof typeof tokenNames;\n\n`;
ts += `/** \`cssVar('colorBg')\` → \`'var(--color-bg)'\` */\nexport function cssVar(name: TokenName): string {\n  return \`var(\${tokenNames[name]})\`;\n}\n\n`;
ts += `export const layout = {\n  sidebarFull: ${parseFloat(tokensJson.layout.sidebarFull)},\n  sidebarRail: ${parseFloat(tokensJson.layout.sidebarRail)},\n  header: ${parseFloat(tokensJson.layout.header)},\n} as const;\n\n`;
ts += `/** Accent options the design exposes as a tweak (docs/02). */\nexport const accentOptions = ['#9184d9', '#7fb3c8', '#c2a06b', '#8fbf9f'] as const;\n\n`;
ts += `/** Mặt tiền kit (v0.1.59): điểm gãy — CHỈ để tham chiếu, media query viết số literal. */\nexport const kitBreakpoints = ${JSON.stringify(BREAKPOINTS)} as const;\n\n`;
ts += '/** Mặt tiền kit: màu bản SÁNG (tokenValues giữ bản TỐI mặc định). */\nexport const kitLightValues = {\n';
for (const [k, val] of kitLight) ts += `  ${JSON.stringify(toCamel(k))}: ${JSON.stringify(val)},\n`;
ts += '} as const;\n';
writeFileSync(join(pkg, 'src/index.ts'), ts);

console.log(`tokens: wrote ${all.length} variables → tokens.css, src/index.ts`);
