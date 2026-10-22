/**
 * Hợp đồng v0.1.55 (G1) — "Chế độ tiêu chuẩn" + "Về mặc định" (`apps/api/gh/defaults/routes.py`, CHỈ Owner).
 *
 * - `GET /defaults` → {@link DefaultsResponse}: sổ mặc định trong mã (mỗi mục có `default_text` / `current_text` là CHỮ cho người
 *   đọc — máy chủ không bao giờ trả giá trị thô dạng object); `customized` do máy chủ TÍNH RA từ dữ liệu thật.
 * - `POST /defaults/{key}/reset` {confirm: true} — Về mặc định một mục. 404 DEFAULTS_KEY_UNKNOWN, 409 DEFAULTS_NOT_RESETTABLE.
 * - `POST /defaults/apply-standard` {confirm: true} — "Áp model chuẩn theo vai": xoá dòng gán model lõi, giữ khoá/nguồn.
 * - `POST /defaults/reset-all` {confirm: true} — cần phiên mã PIN `defaults.reset_all` (423 PIN_REQUIRED ⇒ hộp PIN tự mở).
 *   Không bao giờ chạm khoá API, phiên CLI, mã PIN, mật khẩu, token Gen-hub/Telegram, tài khoản Facebook, ranh giới cứng.
 */
import type { ApiClient } from './client';

export type DefaultScope = 'org' | 'user';

/** Khoá bốn dòng gán model lõi mà "Áp model chuẩn theo vai" xoá (= `registry.CORE_BINDING_KEYS` của máy chủ); `agent:*` giữ nguyên. */
export const CORE_BINDING_KEYS = ['core.gen', 'core.briefing', 'core.refinery', 'core.reply'] as const;
/** Khoá mục sổ "Mức tự trị của tổ chức" — nằm trong "Về mặc định tất cả", Về mặc định riêng mục này cũng cần mã PIN. */
export const AUTONOMY_KEY = 'autonomy';

export interface DefaultItem {
  /** `gen` | `coach` | `triage` | `refinery.schedule` | `jev.preset` | `ai_cost` | `backup` | `autonomy` | `binding:core.gen` | `binding:agent:<id>`… */
  key: string;
  label: string;
  /** `org`: cả tổ chức; `user`: riêng người đang đăng nhập (vd tuỳ chọn Gen hướng dẫn). */
  scope: DefaultScope;
  /** Nhóm hiển thị (Gen, Bộ não AI, Chi phí AI, Sao lưu, Gán model). */
  group: string;
  default_text: string;
  current_text: string;
  /** Máy chủ tính: khác mặc định. Mặc định (hoặc chưa đụng) ⇒ false. */
  customized: boolean;
  /** false = chỉ để xem (vd `jev.preset`: đổi nguồn model ở thẻ Jev, cần mã PIN). */
  resettable: boolean;
}

export type DefaultSuggestionKey = 'apply_standard' | 'background_key_missing';

export interface DefaultSuggestion {
  key: DefaultSuggestionKey;
  title: string;
  body: string;
  /** Đường dẫn trong Console, vd `/system?tab=brain#chuan`. */
  to: string;
}

export interface DefaultsResponse {
  items: DefaultItem[];
  /** Số mục "Đã đổi" mà "Về mặc định tất cả" sẽ đưa về mặc định (mục chỉ-xem không tính). */
  customized_count: number;
  suggestions: DefaultSuggestion[];
}

export interface DefaultsResetResult {
  key: string;
  reset: true;
  customized: boolean;
  current_text: string;
}

const enc = encodeURIComponent;
const CONFIRM = { confirm: true } as const;

/** `/defaults*` — gắn vào `endpoints.ts` (`defaults: defaultsEndpoints(r)`) khi tích hợp. */
export function defaultsEndpoints(r: ApiClient['request']) {
  return {
    list: (signal?: AbortSignal) => r<DefaultsResponse>('/defaults', { signal }),
    reset: (key: string) => r<DefaultsResetResult>(`/defaults/${enc(key)}/reset`, { method: 'POST', body: CONFIRM }),
    applyStandard: () => r<{ removed: number }>('/defaults/apply-standard', { method: 'POST', body: CONFIRM }),
    resetAll: () => r<{ reset: number }>('/defaults/reset-all', { method: 'POST', body: CONFIRM }),
  };
}
