/**
 * v0.1.28 (UX N2): lỗi kỹ thuật thô từ máy chủ ("mạng: All connection attempts failed", "[Errno 2] No such file or
 * directory", "HTTP 401 …") → câu tiếng Việt có việc cần làm tiếp. Chuỗi gốc giữ lại làm "chi tiết kỹ thuật" (ẩn,
 * bấm mới hiện) để người hỗ trợ vẫn đọc được.
 */
export interface FriendlyError {
  message: string;
  /** Chuỗi gốc khi nó khác câu hiển thị; null = không có gì thêm để xem. */
  detail: string | null;
}

const RULES: Array<[RegExp, string]> = [
  [
    /errno 2|no such file|chưa cài antigravity|chưa cài công cụ đăng nhập|command not found/i,
    'Máy chủ chưa cài công cụ đăng nhập Google (Antigravity CLI) — dùng khoá API thay thế.',
  ],
  [
    /connection attempts failed|connecterror|connection refused|name or service not known|getaddrinfo|nodename|no route to host|network is unreachable|^mạng:/i,
    'Không gọi được địa chỉ này — kiểm tra lại Địa chỉ gọi (Endpoint) hoặc mạng của máy chủ.',
  ],
  [/timed? ?out|timeout|quá \d+ ?s\b/i, 'Nguồn này trả lời quá chậm — thử lại sau ít phút.'],
  [
    /\b401\b|\b403\b|xác thực|unauthori[sz]ed|invalid api key|api key not valid|permission denied|forbidden/i,
    'Khoá API không đúng hoặc đã bị thu hồi — kiểm tra lại khoá.',
  ],
  [/\b429\b|quota|rate.?limit|resource_exhausted|hết hạn mức/i, 'Khoá này đang hết hạn mức — thử lại sau hoặc thêm khoá khác.'],
  [/\b404\b|not found/i, 'Không tìm thấy dịch vụ ở địa chỉ này — kiểm tra lại Endpoint (thường kết thúc bằng /v1).'],
  [/\bssl\b|certificate|\btls\b/i, 'Kết nối bảo mật tới địa chỉ này bị lỗi — kiểm tra lại Endpoint (https).'],
  [/\b5\d\d\b|internal server error|bad gateway|service unavailable/i, 'Nhà cung cấp đang gặp sự cố — thử lại sau.'],
];

/** Dấu hiệu chuỗi là lỗi kỹ thuật (tiếng Anh / mã lỗi) chứ không phải câu đã viết cho người dùng. */
const TECHNICAL = /[[\]{}]|errno|exception|traceback|error:|http\s?\d{3}|\b[a-z]+error\b|failed|refused|not found|https?:\/\//i;

export const MODEL_UNAVAILABLE_TEXT = 'Chưa có model AI hoạt động — chọn hoặc sửa model ở API & Model (Hướng dẫn bước 4).';

/** v0.1.67 (F011): ĐÃ có model nhưng lượt gọi lỗi (hết hạn mức, mạng) — không bảo Sếp chọn lại model. */
export const MODEL_DOWN_TEXT = 'Nguồn AI đang lỗi hoặc hết hạn mức — thử lại sau ít phút (không cần chọn lại model).';

// v0.1.62: luật v0.1.38 (F-22) "Antigravity CLI chỉ dùng cho Gen của Sếp" đã gỡ — máy chủ không còn trả câu/lý do riêng
// cho "chuỗi chỉ có agy" nên web bỏ luôn các dấu nhận diện (AGY_ONLY_*). v0.1.63: vai có người ngoài chạy agy qua hộp riêng;
// hộp chưa chạy thì máy chủ trả lý do "Hộp riêng Antigravity chưa chạy / chưa được cài" ở `reasons` / `agy_blocked_reason`.

/** Lý do kỹ thuật dạng `{reasons: [...]}` (máy chủ ≤ v0.1.29) hoặc mảng chuỗi — null nếu không phải. */
export function reasonsOf(value: unknown): string[] | null {
  const raw = Array.isArray(value) ? null : value && typeof value === 'object' ? (value as { reasons?: unknown }).reasons : undefined;
  if (!Array.isArray(raw)) return null;
  return raw.filter((r): r is string => typeof r === 'string' && r.trim() !== '');
}

/**
 * v0.1.30: chuyển `detail`/lỗi BẤT KỲ kiểu (chuỗi, đối tượng, mảng lỗi kiểm tra FastAPI, `{code,message}`,
 * `{reasons}`) thành chuỗi — KHÔNG BAO GIỜ trả đối tượng (vẽ đối tượng làm React child là sập màn, React error #31).
 */
export function detailToText(value: unknown): string {
  if (value === null || value === undefined) return '';
  if (typeof value === 'string') return value;
  if (typeof value === 'number' || typeof value === 'boolean') return String(value);
  if (value instanceof Error) return value.message;
  if (Array.isArray(value)) {
    return value
      .map((x) => {
        if (x && typeof x === 'object' && !Array.isArray(x)) {
          const o = x as { msg?: unknown; loc?: unknown };
          if (typeof o.msg === 'string') {
            const loc = Array.isArray(o.loc) ? o.loc.filter((p) => p !== 'body' && p !== 'query' && p !== 'path').join('.') : '';
            return loc ? `${loc}: ${o.msg}` : o.msg;
          }
        }
        return detailToText(x);
      })
      .filter(Boolean)
      .join('; ');
  }
  if (typeof value === 'object') {
    const reasons = reasonsOf(value);
    if (reasons) return reasons.length ? `${MODEL_UNAVAILABLE_TEXT} (${reasons.join('; ')})` : MODEL_UNAVAILABLE_TEXT;
    const o = value as Record<string, unknown>;
    for (const k of ['message', 'msg', 'detail', 'title', 'error']) {
      const t = detailToText(o[k]);
      if (t) return t;
    }
    try {
      return JSON.stringify(value).slice(0, 300);
    } catch {
      return '';
    }
  }
  return '';
}

export function friendlyError(raw: unknown, fallback = 'Không kiểm tra được nguồn này — thử lại sau.'): FriendlyError {
  // `{reasons}` = không model nào chạy được → câu cố định, lý do kỹ thuật ẩn trong "Chi tiết kỹ thuật".
  const reasons = reasonsOf(raw);
  if (reasons) return { message: MODEL_UNAVAILABLE_TEXT, detail: reasons.length ? reasons.join('; ') : null };
  const text = detailToText(raw).trim();
  if (!text) return { message: fallback, detail: null };
  for (const [re, message] of RULES) if (re.test(text)) return { message, detail: text === message ? null : text };
  if (TECHNICAL.test(text)) return { message: fallback, detail: text };
  return { message: text, detail: null };
}
