import type { JevBenchmark, Provider, SkippedPage, SkippedReason, ValueSummary } from '@gen-harness/contracts';

/**
 * v0.1.55 (G4) — phần thuần của thẻ Jev: preset "Bật Jev 1 chạm", cảnh báo QD-12, nguồn OpenRouter đang có, chữ cho
 * bảng 12 câu mẫu / số đo giá trị / "Tin đã bỏ qua". Không React, không gọi mạng.
 */

/** Preset điền sẵn (khớp `gh.gen.jev.PRESET` ở máy chủ). Địa chỉ/model chỉ chỉnh được ở khối "Nâng cao". */
export const JEV_PRESET = { endpoint: 'https://openrouter.ai/api/v1', model: 'typesafe/jev-1.13', endpointLabel: 'OpenRouter' } as const;

/** QD-12: một dòng cảnh báo — tin gửi sang OpenRouter đã được che số điện thoại/email (máy chủ che, không có cờ tắt). */
export const JEV_PRIVACY_WARNING = 'Tin đã che số điện thoại/email được gửi sang OpenRouter';

export const JEV_KEY_MIN = 8;

export function jevKeyValid(key: string): boolean {
  return key.trim().length >= JEV_KEY_MIN;
}

/** Nguồn OpenRouter đang có (không phải Jev) và còn ít nhất một khoá — để hiện nút "Dùng khoá OpenRouter đang có". */
export function findOpenRouterSource(providers: readonly Provider[] | undefined): Provider | undefined {
  return (providers ?? []).find((p) => {
    if (p.kind === 'system_one' || p.kind === 'antigravity_cli' || p.kind === 'claude_code_cli') return false;
    if (!p.keys?.length) return false;
    try {
      const host = new URL(p.endpoint ?? '').hostname.toLowerCase();
      return host === 'openrouter.ai' || host.endsWith('.openrouter.ai');
    } catch {
      return false;
    }
  });
}

// ─── bảng 12 câu mẫu ────────────────────────────────────────────────────────────

export function isBenchmark(x: unknown): x is JevBenchmark {
  if (!x || typeof x !== 'object') return false;
  const b = x as Partial<JevBenchmark>;
  return typeof b.total === 'number' && typeof b.correct === 'number' && Array.isArray(b.items);
}

/** "Đúng 10/12 · trung bình 420 ms". */
export function benchmarkSummary(b: JevBenchmark): string {
  const avg = b.avg_latency_ms == null ? 'chưa có số đo độ trễ' : `trung bình ${b.avg_latency_ms} ms`;
  return `Đúng ${b.correct}/${b.total} · ${avg}`;
}

// ─── số đo giá trị ──────────────────────────────────────────────────────────────

export function isValueSummary(x: unknown): x is ValueSummary {
  if (!x || typeof x !== 'object') return false;
  const v = x as Partial<ValueSummary>;
  return typeof v.filtered === 'number' && typeof v.spam_blocked === 'number' && typeof v.calls_saved === 'number';
}

/** Dòng số đo 7 ngày — chỉ đếm số lần (không quy ra tiền). null khi chưa có gì đáng nói. */
export function valueSummaryText(v: ValueSummary, days = 7): string | null {
  if (!v.filtered && !v.spam_blocked && !v.calls_saved) return null;
  return `${days} ngày qua: lọc ${v.filtered} tin rác/trùng · chặn ${v.spam_blocked} tin rác · tiết kiệm ${v.calls_saved} lượt gọi model`;
}

// ─── Tin đã bỏ qua ──────────────────────────────────────────────────────────────

export function isSkippedPage(x: unknown): x is SkippedPage {
  if (!x || typeof x !== 'object') return false;
  const p = x as Partial<SkippedPage>;
  return Array.isArray(p.items) && typeof p.total === 'number';
}

export const SKIPPED_REASON_TEXT: Record<SkippedReason, string> = {
  exact_dup: 'Trùng hẳn một tin đã có',
  spam_rule_jev: 'Rác — quy tắc và Jev cùng chấm rác',
  spam_rule_nojev: 'Rác — quy tắc chấm rác (chưa có Jev)',
};

export function skippedReasonText(reason: string, fromServer?: string): string {
  return fromServer?.trim() || SKIPPED_REASON_TEXT[reason as SkippedReason] || 'Đã bỏ qua trước khi trích xuất';
}

export function skippedLinkLabel(total: number): string {
  return `Tin đã bỏ qua (${total})`;
}
