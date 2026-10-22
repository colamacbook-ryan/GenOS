/**
 * v0.1.59 (G2) — LÍNH GÁC THIẾT KẾ Mặt tiền. Quét CHỈ CSS của Mặt tiền: `apps/web/src/owner/**\/*.css` và
 * `packages/ui/src/owner-kit.css` (packages/ui/src/styles.css của Console để nguyên — ROADMAP › Nợ "chuẩn hoá chữ Console").
 * Fail (kèm `tệp:dòng`) khi gặp:
 *   - `text-transform: uppercase`
 *   - `font-size` ngoài {12, 14, 18, 28}px hoặc var(--fs-12|14|18|28)   (cả cỡ trong shorthand `font:`)
 *   - mã màu hex (#fff, #1b1d27…), `rgb()` / `hsl()` (và rgba/hsla/hwb/lab/lch/oklab/oklch) viết thẳng
 *   - `@media` (và `@container`) có số khác 760 / 1024 (px) hoặc dùng max-width / max-*
 *   - `letter-spacing` khác 0
 *   - `font-weight` ngoài 400 / 600 (hai độ đậm)
 * Có ca tự kiểm: CSS mẫu cố ý vi phạm PHẢI bị bắt, CSS sạch (kể cả hex/uppercase nằm trong chú thích) KHÔNG bị bắt.
 * Phần cuối kiểm token mới (tokens.css sinh đúng từ tokens.json, cặp sáng/tối) và việc nối owner-kit.css vào ứng dụng.
 */
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join, relative, resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

const ROOT = resolve(__dirname, '../../../..');

// ── bộ quét ─────────────────────────────────────────────────────────────────────────────────────────────────────────

interface Decl {
  prop: string;
  value: string;
  line: number;
}
interface AtRule {
  name: string;
  params: string;
  line: number;
}

/** Bóc khai báo + at-rule @media/@container, bỏ chú thích (giữ nguyên số dòng). Biết chuỗi và ngoặc ( ) — dấu ; trong đó không cắt. */
function parseCss(css: string): { decls: Decl[]; atRules: AtRule[] } {
  const src = css.replace(/\/\*[\s\S]*?\*\//g, (m) => m.replace(/[^\n]/g, ' '));
  const decls: Decl[] = [];
  const atRules: AtRule[] = [];
  let line = 1;
  let buf = '';
  let start = 1;
  let paren = 0;
  let quote: string | null = null;
  const take = () => {
    const text = buf.trim();
    const at = start;
    buf = '';
    return { text, at };
  };
  for (const ch of src) {
    if (ch === '\n') line++;
    if (quote) {
      buf += ch;
      if (ch === quote) quote = null;
      continue;
    }
    if (ch === '"' || ch === "'") {
      if (!buf.trim()) start = line;
      quote = ch;
      buf += ch;
      continue;
    }
    if (ch === '(') paren++;
    else if (ch === ')') paren = Math.max(0, paren - 1);
    if (paren === 0 && (ch === '{' || ch === '}' || ch === ';')) {
      const { text, at } = take();
      if (ch === '{') {
        const m = /^@(media|container)\b([\s\S]*)$/i.exec(text);
        if (m) atRules.push({ name: m[1].toLowerCase(), params: m[2].trim(), line: at });
      } else if (text.includes(':') && !text.startsWith('@')) {
        const i = text.indexOf(':');
        decls.push({ prop: text.slice(0, i).trim().toLowerCase(), value: text.slice(i + 1).trim(), line: at });
      }
      continue;
    }
    if (!buf.trim() && ch.trim()) start = line;
    buf += ch;
  }
  return { decls, atRules };
}

const SIZES = new Set(['12px', '14px', '18px', '28px', 'var(--fs-12)', 'var(--fs-14)', 'var(--fs-18)', 'var(--fs-28)', 'inherit']);
const FS_SHORTHAND_SIZE = /(?:^|\s)(\d+(?:\.\d+)?(?:px|rem|em|%|pt))(?=\/|\s|$)/g;
const HEX = /#(?:[0-9a-f]{8}|[0-9a-f]{6}|[0-9a-f]{3,4})(?![\w-])/i;
const COLOR_FN = /\b(?:rgba?|hsla?|hwb|lab|lch|oklab|oklch)\s*\(/i;
const WEIGHTS = new Set(['400', '600', 'normal', 'inherit', 'var(--fw-400)', 'var(--fw-600)']);
const SPACING = new Set(['0', '0px', '0em', 'normal', 'inherit']);

/** Trả danh sách vi phạm dạng "tệp:dòng — lý do". Rỗng = sạch. */
function checkDesignCss(file: string, css: string): string[] {
  const { decls, atRules } = parseCss(css);
  const out: string[] = [];
  const bad = (line: number, msg: string) => out.push(`${file}:${line} — ${msg}`);
  for (const d of decls) {
    const v = d.value.replace(/\s*!important\s*$/i, '').trim();
    if (d.prop === 'text-transform' && /\buppercase\b/i.test(v)) bad(d.line, `text-transform: ${v} (không chữ hoa toàn bộ)`);
    if (d.prop === 'font-size' && !SIZES.has(v)) bad(d.line, `font-size: ${v} (chỉ 12/14/18/28px hoặc var(--fs-12|14|18|28))`);
    if (d.prop === 'font') {
      for (const m of v.matchAll(FS_SHORTHAND_SIZE)) {
        if (!SIZES.has(m[1])) bad(d.line, `font: ${v} — cỡ ${m[1]} ngoài thang 12/14/18/28px`);
      }
    }
    if (d.prop === 'letter-spacing' && !SPACING.has(v)) bad(d.line, `letter-spacing: ${v} (chỉ 0)`);
    if (d.prop === 'font-weight' && !WEIGHTS.has(v)) bad(d.line, `font-weight: ${v} (chỉ 400 / 600)`);
    const hex = HEX.exec(v);
    if (hex) bad(d.line, `mã màu hex ${hex[0]} trong "${d.prop}: ${v}" (dùng var(--token))`);
    const fn = COLOR_FN.exec(v);
    if (fn) bad(d.line, `màu viết thẳng ${fn[0]}…) trong "${d.prop}: ${v}" (dùng var(--token))`);
  }
  for (const a of atRules) {
    if (/\bmax-/i.test(a.params)) bad(a.line, `@${a.name} ${a.params} — không dùng max-width (mobile-first, chỉ min-width)`);
    for (const m of a.params.matchAll(/(\d+(?:\.\d+)?)\s*(px|em|rem|dppx|dpi)?/gi)) {
      const ok = (m[1] === '760' || m[1] === '1024') && (m[2] ?? 'px').toLowerCase() === 'px';
      if (!ok) bad(a.line, `@${a.name} ${a.params} — điểm gãy ${m[0]} (chỉ min-width 760px và 1024px)`);
    }
  }
  return out;
}

function cssFiles(dir: string): string[] {
  const out: string[] = [];
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) out.push(...cssFiles(p));
    else if (name.endsWith('.css')) out.push(p);
  }
  return out;
}

const TARGETS = [...cssFiles(join(ROOT, 'apps/web/src/owner')), join(ROOT, 'packages/ui/src/owner-kit.css')];
const rel = (p: string) => relative(ROOT, p);

// ── lính gác chạy trên CSS thật ─────────────────────────────────────────────────────────────────────────────────────

describe('lính gác thiết kế — CSS thật của Mặt tiền', () => {
  it('phạm vi quét: owner/**/*.css + owner-kit.css, KHÔNG gồm styles.css / tokens.css / các tệp Console', () => {
    const names = TARGETS.map(rel);
    expect(names).toContain('apps/web/src/owner/owner.css');
    expect(names).toContain('packages/ui/src/owner-kit.css');
    expect(names.some((n) => n.endsWith('ui/src/styles.css'))).toBe(false);
    expect(names.some((n) => n.includes('tokens.css'))).toBe(false);
    expect(names.every((n) => n.startsWith('apps/web/src/owner/') || n === 'packages/ui/src/owner-kit.css')).toBe(true);
  });

  it('không uppercase, không cỡ chữ ngoài thang, không hex/rgb()/hsl(), không điểm gãy lạ, letter-spacing 0, hai độ đậm', () => {
    const violations = TARGETS.flatMap((f) => checkDesignCss(rel(f), readFileSync(f, 'utf8')));
    expect(violations, `\nVi phạm lính gác thiết kế:\n${violations.join('\n')}\n`).toEqual([]);
  });

  it('mọi var(--x) trong CSS Mặt tiền đều được định nghĩa (tokens.css hoặc ngay trong tệp quét) — bắt lỗi gõ nhầm token', () => {
    const defined = new Set<string>();
    const tokens = readFileSync(join(ROOT, 'packages/tokens/tokens.css'), 'utf8');
    const theme = readFileSync(join(ROOT, 'apps/web/src/styles/theme.css'), 'utf8');
    const mine = TARGETS.map((f) => readFileSync(f, 'utf8'));
    for (const css of [tokens, theme, ...mine]) for (const m of css.matchAll(/(--[\w-]+)\s*:/g)) defined.add(m[1]);
    const missing: string[] = [];
    TARGETS.forEach((f, i) => {
      const stripped = mine[i].replace(/\/\*[\s\S]*?\*\//g, (c) => c.replace(/[^\n]/g, ' '));
      stripped.split('\n').forEach((text, n) => {
        for (const m of text.matchAll(/var\(\s*(--[\w-]+)/g)) if (!defined.has(m[1])) missing.push(`${rel(f)}:${n + 1} — ${m[1]} chưa được định nghĩa`);
      });
    });
    expect(missing).toEqual([]);
  });
});

// ── ca tự kiểm: lính gác phải bắt đúng, không bắt nhầm ─────────────────────────────────────────────────────────────

describe('lính gác thiết kế — ca tự kiểm', () => {
  const BAD = [
    '.a { text-transform: uppercase; }', //                                   1
    '.b { font-size: 11px; }', //                                             2
    '.c { color: #fff; }', //                                                 3
    '@media (min-width: 800px) {', //                                         4
    '  .d { margin: 0; }', //                                                 5
    '}', //                                                                   6
  ].join('\n');

  it('bắt uppercase, 11px, #fff, @media 800px — mỗi vi phạm kèm "tệp:dòng" đúng', () => {
    const v = checkDesignCss('mau.css', BAD);
    expect(v).toHaveLength(4);
    expect(v[0]).toMatch(/^mau\.css:1 — text-transform: uppercase/);
    expect(v[1]).toMatch(/^mau\.css:2 — font-size: 11px/);
    expect(v[2]).toMatch(/^mau\.css:3 — mã màu hex #fff/);
    expect(v[3]).toMatch(/^mau\.css:4 — @media .*800px/);
  });

  it('bắt rgb()/hsl()/oklch() viết thẳng, letter-spacing khác 0, font-weight ngoài 400/600, font: shorthand lạ', () => {
    const css = [
      '.a { background: rgb(0 0 0 / 50%); }',
      '.b { color: hsl(210, 20%, 40%); border-color: rgba(0,0,0,.2); }',
      '.c { color: oklch(0.7 0.1 240); }',
      '.d { letter-spacing: 0.04em; }',
      '.e { font-weight: 500; }',
      '.f { font-weight: bold; }',
      '.g { font: 600 13px/1.2 Inter, sans-serif; }',
      '.h { font-size: 1rem; }',
      '.i { font-size: calc(14px + 1px); }',
      '.j { font-size: var(--fs-13); }',
    ].join('\n');
    const v = checkDesignCss('x.css', css);
    const lines = v.map((s) => Number(/:(\d+) —/.exec(s)?.[1]));
    expect(lines).toEqual([1, 2, 2, 3, 4, 5, 6, 7, 8, 9, 10]);
    expect(v.join('\n')).toMatch(/letter-spacing: 0.04em/);
    expect(v.join('\n')).toMatch(/font-weight: 500/);
    expect(v.join('\n')).toMatch(/cỡ 13px/);
  });

  it('bắt max-width, đơn vị em/rem, @container lạ, min-width khác 760/1024', () => {
    const css = [
      '@media (max-width: 760px) { .a { margin: 0; } }',
      '@media (min-width: 48em) { .b { margin: 0; } }',
      '@media (min-width: 760px) and (max-width: 1023px) { .c { margin: 0; } }',
      '@media (min-width: 1360px) { .d { margin: 0; } }',
      '@container (min-width: 500px) { .e { margin: 0; } }',
    ].join('\n');
    const v = checkDesignCss('m.css', css);
    expect(v.map((s) => Number(/:(\d+) —/.exec(s)?.[1]))).toEqual([1, 2, 3, 3, 4, 5]);
    expect(v[0]).toMatch(/max-width/);
    expect(v[1]).toMatch(/48em/);
  });

  it('KHÔNG bắt: token, 12/14/18/28px, inherit, 400/600, letter-spacing 0, @media 760/1024, chú thích chứa hex/uppercase', () => {
    const css = `
/* ví dụ trong chú thích: color: #fff; text-transform: uppercase; font-size: 11px; @media (max-width: 500px) */
.ok {
  color: var(--text); background: var(--surface); border: 1px solid var(--border);
  font-size: var(--fs-14); line-height: var(--lh-14); font-weight: var(--fw-600); letter-spacing: 0;
  text-transform: none; box-shadow: var(--shadow-pop);
  background: color-mix(in srgb, var(--accent) 12%, transparent);
}
.ok2 { font-size: 12px; font-weight: 400; font: inherit; font-size: inherit; content: ';'; }
.ok3 { font: inherit; }
.ok4 { font-size: 28px }
@media (min-width: 760px) { .x { font-size: 18px; } }
@media (min-width: 1024px) { .x { margin: 0 } }
@media (prefers-reduced-motion: reduce) { .x { transition: none; } }
a[href='#top'] { color: var(--accent); }
`;
    expect(checkDesignCss('ok.css', css)).toEqual([]);
  });

  it('số dòng đúng khi khai báo trải nhiều dòng, nằm trong @media lồng, hoặc thiếu dấu ; ở cuối khối', () => {
    const css = ['@media (min-width: 760px) {', '  .a {', '    color: red;', '    font-size:', '      13px', '  }', '  .b { text-transform: uppercase }', '}'].join('\n');
    const v = checkDesignCss('n.css', css);
    expect(v.map((s) => Number(/:(\d+) —/.exec(s)?.[1]))).toEqual([4, 7]);
  });
});

// ── token mới + nối vào ứng dụng ────────────────────────────────────────────────────────────────────────────────────

describe('token Mặt tiền (docs/design/tokens.json → packages/tokens/tokens.css)', () => {
  const tokensCss = readFileSync(join(ROOT, 'packages/tokens/tokens.css'), 'utf8');
  const json = JSON.parse(readFileSync(join(ROOT, 'docs/design/tokens.json'), 'utf8')) as {
    kit: {
      breakpoint: { md: number; lg: number };
      color: { neutral: Record<string, { dark: string; light: string }>; semantic: Record<string, { dark: string; light: string }> };
    };
  };
  const lightBlock = /:root\[data-theme='light'\]\s*\{([\s\S]*?)\n\}/.exec(tokensCss)?.[1] ?? '';
  const rootBlock = /:root\s*\{([\s\S]*?)\n\}/.exec(tokensCss)?.[1] ?? '';
  const val = (block: string, name: string) => new RegExp(`${name}:\\s*([^;]+);`).exec(block)?.[1].trim();

  it('thang chữ 12/14/18/28 + line-height 16/20/24/32 + hai độ đậm 400/600', () => {
    for (const [n, px] of [['12', '12px'], ['14', '14px'], ['18', '18px'], ['28', '28px']] as const) expect(val(rootBlock, `--fs-${n}`)).toBe(px);
    for (const [n, px] of [['12', '16px'], ['14', '20px'], ['18', '24px'], ['28', '32px']] as const) expect(val(rootBlock, `--lh-${n}`)).toBe(px);
    expect(val(rootBlock, '--fw-400')).toBe('400');
    expect(val(rootBlock, '--fw-600')).toBe('600');
  });

  it('lưới 8pt --sp-1..6 = 4/8/16/24/32/48; bán kính 8 và 999', () => {
    ['4px', '8px', '16px', '24px', '32px', '48px'].forEach((px, i) => expect(val(rootBlock, `--sp-${i + 1}`)).toBe(px));
    expect(val(rootBlock, '--r-8')).toBe('8px');
    expect(val(rootBlock, '--r-999')).toBe('999px');
  });

  it('5 màu ngữ nghĩa (+ nền nhạt -soft) và --neutral-50…900 có cặp TỐI (:root) / SÁNG (data-theme="light")', () => {
    const names = ['accent', 'ok', 'warn', 'bad', 'info'].flatMap((c) => [c, `${c}-soft`]);
    for (const n of names) {
      expect(val(rootBlock, `--${n}`), `tối --${n}`).toBeTruthy();
      expect(val(lightBlock, `--${n}`), `sáng --${n}`).toBeTruthy();
      expect(val(rootBlock, `--${n}`)).not.toBe(val(lightBlock, `--${n}`));
    }
    for (const step of ['50', '100', '200', '300', '400', '500', '600', '700', '800', '900']) {
      expect(val(rootBlock, `--neutral-${step}`), `tối --neutral-${step}`).toMatch(/^#[0-9a-f]{6}$/);
      expect(val(lightBlock, `--neutral-${step}`), `sáng --neutral-${step}`).toMatch(/^#[0-9a-f]{6}$/);
    }
  });

  it('tokens.css khớp tokens.json: mọi màu kit đủ cả hai giao diện, không lệch giá trị (nhắc chạy `npm run tokens`)', () => {
    for (const group of [json.kit.color.neutral, json.kit.color.semantic]) {
      for (const [name, pair] of Object.entries(group)) {
        const css = name.match(/^\d+$/) ? `--neutral-${name}` : `--${name}`;
        expect(val(rootBlock, css), `tokens.css (tối) ${css} — chạy npm run tokens`).toBe(pair.dark);
        expect(val(lightBlock, css), `tokens.css (sáng) ${css} — chạy npm run tokens`).toBe(pair.light);
      }
    }
  });

  it('điểm gãy 760/1024 ghi trong tokens.json để tham chiếu; token cũ của Console không đổi', () => {
    expect(json.kit.breakpoint).toMatchObject({ md: 760, lg: 1024 });
    for (const old of ['--color-bg: #161826', '--color-accent: #9184d9', '--space-1: 2.8px', '--font-size-kpi: 26px', '--radius-pill: 999px', '--sidebar-full: 244px']) {
      expect(tokensCss).toContain(old);
    }
  });
});

describe('nối owner-kit.css vào ứng dụng', () => {
  it('@gen-harness/ui xuất ./owner-kit.css; main.tsx import ngay sau styles.css', () => {
    const pkg = JSON.parse(readFileSync(join(ROOT, 'packages/ui/package.json'), 'utf8')) as { exports: Record<string, string> };
    expect(pkg.exports['./owner-kit.css']).toBe('./src/owner-kit.css');
    expect(pkg.exports['./styles.css']).toBe('./src/styles.css');
    const lines = readFileSync(join(ROOT, 'apps/web/src/main.tsx'), 'utf8').split('\n');
    const i = lines.findIndex((l) => l.includes("'@gen-harness/ui/styles.css'"));
    expect(i).toBeGreaterThan(0);
    expect(lines[i + 1].trim()).toBe("import '@gen-harness/ui/owner-kit.css';");
  });
});
