/**
 * v0.1.42 (F-61) — thang tự trị 0–6 khai một nơi: `packages/contracts/src/autonomy.ts`.
 * Khớp chéo với `gh.chassis.policy.LEVELS` phía API (đọc tệp Python, không chạy).
 */
import { createElement, createRef } from 'react';
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join, relative, resolve } from 'node:path';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { AUTONOMY_LEVELS, AUTONOMY_MAX, autonomyLabel } from '@gen-harness/contracts';
import { autonomyTooltip } from '../../src/shell/headerModel';
import { AUTONOMY_LEVELS as AGENT_LEVELS } from '../../src/screens/agents/agentsModel';
import { Step9Autonomy } from '../../src/setup/Step9Autonomy';
import { SETUP_STEPS } from '../../src/setup/steps';

const ROOT = resolve(__dirname, '../../../..');
const POLICY = resolve(ROOT, 'apps/api/gh/chassis/policy.py');

function files(dir: string): string[] {
  return readdirSync(dir).flatMap((f) => {
    const p = join(dir, f);
    return statSync(p).isDirectory() ? files(p) : /\.tsx?$/.test(f) ? [p] : [];
  });
}

afterEach(() => vi.unstubAllGlobals());

describe('Thang tự trị — một nguồn', () => {
  it('nhãn mức 6 chỉ xuất hiện ở đúng packages/contracts/src/autonomy.ts', () => {
    const hits = [...files(resolve(ROOT, 'packages/contracts/src')), ...files(resolve(ROOT, 'apps/web/src'))]
      .filter((f) => readFileSync(f, 'utf8').includes('Tự làm việc đã whitelist'))
      .map((f) => relative(ROOT, f).split('\\').join('/'));
    expect(hits).toEqual(['packages/contracts/src/autonomy.ts']);
  });

  it('khớp gh.chassis.policy.LEVELS (7 nhãn 0–6)', () => {
    const src = readFileSync(POLICY, 'utf8');
    const block = /^LEVELS\s*=\s*\{([\s\S]*?)^\}/m.exec(src)?.[1] ?? '';
    const levels = [...block.matchAll(/^\s*(\d+)\s*:\s*"([^"]+)"/gm)].map((m) => [Number(m[1]), m[2]] as const);
    expect(levels.map(([n]) => n)).toEqual([0, 1, 2, 3, 4, 5, 6]);
    expect(levels.map(([, label]) => label)).toEqual([...AUTONOMY_LEVELS]);
    expect(AUTONOMY_MAX).toBe(AUTONOMY_LEVELS.length - 1);
  });

  it('autonomyLabel: trong thang trả nhãn, ngoài thang trả null', () => {
    expect(autonomyLabel(0)).toBe('Chỉ ghi nhận');
    expect(autonomyLabel(AUTONOMY_MAX)).toBe(AUTONOMY_LEVELS[6]);
    expect(autonomyLabel(7)).toBeNull();
    expect(autonomyLabel(-1)).toBeNull();
    expect(autonomyLabel(2.5)).toBeNull();
  });
});

describe('Nơi dùng giữ nguyên hành vi', () => {
  it('tooltip header: nhãn 3 mức + mức thật trên thang 0–6 (v0.1.43, F-30)', () => {
    expect(autonomyTooltip(4)).toBe('Mức tự trị chung: Soạn sẵn chờ duyệt (mức 4/6)');
    expect(autonomyTooltip(6)).toBe('Mức tự trị chung: Tự làm (đặt ở Nâng cao) (mức 6/6)');
    expect(autonomyTooltip(9)).toBe('Mức tự trị chung: mức 9 (ngoài thang 0–6)');
  });

  it('Danh tính Agent: bảng nhãn 0–6 vẫn dẫn xuất từ contracts (title của viên tự trị)', () => {
    expect(Object.keys(AGENT_LEVELS)).toHaveLength(7);
    expect(AGENT_LEVELS[3]).toBe('Gợi ý hành động');
  });

  it('bước 9 vẫn chỉ cho chọn mức 3 hoặc 4 (nhãn 3 mức, không tiền tố số)', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response('[]', { status: 200, headers: { 'Content-Type': 'application/json' } })));
    const meta = SETUP_STEPS.find((s) => s.n === 9)!;
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      createElement(
        QueryClientProvider,
        { client: qc },
        createElement(Step9Autonomy, {
          meta,
          description: '',
          status: 'todo',
          token: '',
          setToken: () => undefined,
          onSaved: () => undefined,
          onNext: () => undefined,
          formRef: createRef<HTMLFormElement>(),
        }),
      ),
    );
    // Ô chọn mức hiện sau khi đọc xong mức hiện tại của agent (GET /setup/steps/9).
    const radios = (await screen.findAllByRole('radio')).map((r) => r.textContent?.trim());
    expect(radios).toEqual(['Gợi ý', 'Soạn sẵn chờ duyệt']);
    expect(screen.getAllByRole('radio').map((r) => r.getAttribute('aria-checked'))).toEqual(['false', 'true']);
  });
});
