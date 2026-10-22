/**
 * Hợp đồng API giai đoạn 4 · MCP Hub (PLAN 4.3, ARCHITECTURE §10, `apps/api/gh/mcp_api/routes.py`).
 *
 * Chỉ khai báo đúng những route đã có thật ở backend — `apps/api/gh/mcp_api/routes.py` KHÔNG có
 * `/mcp/stats`, `/mcp/guards` hay `/mcp/market` (khác bảng liệt kê tổng quan ở ARCHITECTURE §13, vốn là dự kiến
 * ban đầu); mọi số liệu tổng hợp (máy chủ đang bật, tool đã mở, lượt gọi/bị chặn) và mô tả rào chắn khoá cứng ở
 * màn `mcp` đều tính/hiển thị PHÍA CLIENT từ `servers` + `tools` + `calls` đã có, không bịa endpoint.
 */
import type { ApiClient } from './client';

export const MCP_TRANSPORTS = ['stdio', 'http+sse', 'streamable_http'] as const;
export type McpTransport = (typeof MCP_TRANSPORTS)[number];

export type McpToolAccess = 'read' | 'write';
export type McpCallOutcome = 'ok' | 'held_for_approval' | 'blocked' | 'error';

export interface McpServer {
  id: string;
  name: string;
  transport: McpTransport;
  endpoint: string;
  has_auth: boolean;
  is_enabled: boolean;
  health: string;
  note: string | null;
  allow_public_network: boolean;
  tool_count: number;
  exposed_count: number;
}

export interface McpServerCreateBody {
  name: string;
  transport: McpTransport;
  endpoint: string;
  auth_token?: string | null;
  allow_public_network?: boolean;
  note?: string | null;
}

export interface McpServerPatchBody {
  name?: string;
  endpoint?: string;
  auth_token?: string | null;
  allow_public_network?: boolean;
  is_enabled?: boolean;
  note?: string | null;
}

export interface McpTool {
  id: string;
  server_id: string;
  server_name: string;
  name: string;
  access: McpToolAccess;
  is_exposed: boolean;
  schema: Record<string, unknown>;
  grants: string[];
}

export interface McpDiscoveredTool {
  id: string;
  name: string;
  access: McpToolAccess;
  is_exposed: boolean;
  is_new: boolean;
}

export interface McpDiscoverResult {
  tools: McpDiscoveredTool[];
}

/** Dấu vết tham số lượt gọi tool (v0.1.45): sha256 của JSON sắp khoá, tên khoá cấp 1 (≤ 20), số byte. */
export interface McpArgsDigest {
  sha256: string;
  keys: string[];
  bytes: number;
}

export interface McpCall {
  id: string;
  at: string;
  tool_id: string;
  tool_name: string;
  access: McpToolAccess;
  server_name: string;
  agent_key: string;
  /** v0.1.45 (F-57): máy chủ chỉ lưu DẤU VẾT tham số (không nguyên văn); dòng rất cũ chưa qua job dọn dẹp có thể
   *  còn dạng object tự do. */
  args: McpArgsDigest | Record<string, unknown>;
  result_summary: string;
  latency_ms: number | null;
  outcome: McpCallOutcome;
  draft_id: string | null;
}

export interface McpCallPage {
  items: McpCall[];
  next_cursor: string | null;
}

export interface McpCallBody {
  agent_key: string;
  args?: Record<string, unknown>;
}

/** `write`: tạo bản nháp chờ duyệt (`held_for_approval`) hoặc `blocked`. `read` ok: kết quả tool thật. */
export type McpCallResult =
  | { outcome: 'ok'; result: unknown; call: McpCall }
  | { outcome: 'held_for_approval' | 'blocked'; draft: unknown; call: McpCall };

/**
 * v0.1.26 (Đợt D1) — liên kết Gen-hub để Gen đọc Kho dữ liệu (`apps/api/gh/hub_link/routes.py`). Token Gen-hub CHỈ GHI:
 * API không bao giờ trả lại token (chỉ `has_token`). Liên kết tắt (`enabled=false`) tới khi Owner bấm "Kiểm tra" xanh.
 */
export type HubLinkStatus = 'off' | 'ok' | 'expiring' | 'expired' | 'error';

/** v0.1.49 (QD-16): quyền ĐỌC thêm của token Gen-hub (tuỳ chọn) — Gen chỉ đọc, không bao giờ ghi. */
export type HubReadScopes = { calendar: boolean; mail: boolean; tasks: boolean; drive: boolean };

/**
 * v0.1.50 (F-81, QD-18): quyền GHI Kho của token Gen-hub (tuỳ chọn), THEO TỪNG tool: `kho_create`, `kho_update` (tool có trên Gen-hub,
 * đang mở và đã cấp cho Gen); `kho` = có cả hai (giữ cho bên đọc cũ). Máy chủ cũ chỉ gửi `kho` ⇒ hai dòng theo `kho`. Gen KHÔNG tự
 * ghi — chỉ khi Sếp bấm Xác nhận + nhập mã PIN trên thẻ đề xuất "Ghi vào Kho dữ liệu".
 */
export type HubWriteScopes = { kho: boolean; kho_create?: boolean; kho_update?: boolean };

export interface HubLink {
  configured: boolean;
  enabled: boolean;
  status: HubLinkStatus;
  server_id: string | null;
  endpoint: string | null;
  has_token: boolean;
  allow_public_network: boolean;
  token_expires_at: string | null;
  days_left: number | null;
  last_ok_at: string | null;
  /** Lỗi thô (đã lọc token) chỉ Owner thấy; vai trò khác nhận một câu chung (v0.1.27). */
  last_error: string | null;
  health: string | null;
  /**
   * v0.1.49: quyền đọc thêm đã thấy ở lần Kiểm tra xanh gần nhất. `null` (hoặc máy chủ cũ không gửi) ⇒ "Chưa kiểm": chưa
   * nối, chưa từng Kiểm tra xanh, hoặc vừa đổi địa chỉ/token (liên kết tắt chờ kiểm lại).
   */
  read_scopes?: HubReadScopes | null;
  /** v0.1.49 (F-83): bộ ngắt riêng của Gen-hub — `open` = 3 lỗi liên tiếp, tạm dừng gọi 60 giây. */
  breaker?: { open: boolean; retry_in_s?: number | null };
  /**
   * v0.1.50: quyền ghi Kho thấy ở lần Kiểm tra xanh gần nhất. `null` / vắng (máy chủ cũ) ⇒ "Chưa kiểm" — cùng quy tắc `read_scopes`.
   */
  write_scopes?: HubWriteScopes | null;
  /**
   * v0.1.50: hậu tố tool ghi Kho ('kho_create' | 'kho_update') Owner đã TỰ đóng (hoặc gỡ cấp Gen) ở MCP Hub — máy chủ tính lại mỗi lần
   * đọc, nên tải lại trang vẫn nói đúng "Sếp đã tự đóng…" thay vì giục tick ở Gen-hub. `null` / vắng ⇒ chưa kiểm (máy chủ cũ).
   */
  write_hidden?: string[] | null;
  /**
   * v0.1.57 (Nợ #30): "Tên Kho" hiệu lực của tổ chức — tên Owner tự đặt (≤ `kho_label_max` ký tự) hoặc mặc định "Kho dữ liệu".
   * Ai đọc được thẻ cũng nhận. Máy chủ cũ không gửi ⇒ vắng (web dùng mặc định).
   */
  kho_label?: string;
  /** Owner đã đổi tên Kho (khác mặc định). */
  kho_label_custom?: boolean;
  /** Tên mặc định ("Kho dữ liệu"). */
  kho_label_default?: string;
  /** Độ dài tối đa của tên Kho (40). */
  kho_label_max?: number;
}

/** `PATCH /hub/link` — Owner + PIN `hub.link`. `enabled` chỉ nhận `false` (bật = bấm Kiểm tra). */
export interface HubLinkPatchBody {
  endpoint?: string;
  token?: string;
  token_expires_at?: string | null;
  allow_public_network?: boolean;
  enabled?: false;
  /** v0.1.57: tên Kho (≤ 40 ký tự); chuỗi rỗng ⇒ về mặc định. Chỉ gửi mỗi trường này thì không đụng liên kết (kể cả khi chưa nối). */
  kho_label?: string;
}

export interface HubLinkTestResult {
  ok: boolean;
  error: string | null;
  /** v0.1.39: mã lỗi thống nhất (MCP_NETWORK_BLOCKED, HUB_TOKEN_REJECTED…) — null khi ok. */
  error_code?: string | null;
  latency_ms: number;
  exposed_tools: string[];
  missing_tools: string[];
  /** v0.1.49: quyền đọc thêm của token; thiếu quyền đọc mới KHÔNG làm `ok=false`. */
  read_scopes?: HubReadScopes;
  /** v0.1.49: nhãn quyền đọc còn thiếu (vd 'đọc lịch', 'đọc mail'); rỗng = đủ. */
  read_missing?: string[];
  /** v0.1.49: tool GHI mà token đang có (nếu có) — Gen không dùng, nhưng nên tắt cho an toàn. */
  write_tools?: string[];
  /** v0.1.50: quyền ghi Kho của token; thiếu quyền ghi KHÔNG làm `ok=false`. */
  write_scopes?: HubWriteScopes;
  /** v0.1.50: nhãn quyền ghi còn thiếu ở Gen-hub, nêu đúng tool (vd 'ghi Kho (kho_update)'); rỗng = đủ. */
  write_missing?: string[];
  /** v0.1.50: tool GHI Kho vừa mở cho Gen (nằm trong `exposed_tools`) — để thẻ đếm riêng "tool đọc" và "tool ghi Kho". */
  exposed_write_tools?: string[];
  /**
   * v0.1.50: hậu tố tool ghi Kho ('kho_create' | 'kho_update') Owner đã TỰ đóng (hoặc gỡ cấp Gen) ở MCP Hub — Kiểm tra không mở
   * lại; muốn bật lại thì mở ở MCP Hub.
   */
  write_hidden?: string[];
  link: HubLink;
}

const enc = encodeURIComponent;

/** `GET/POST /mcp/servers`, `/tools*`, `/calls` + `/hub/link*` (v0.1.26) — không có route nào khác ngoài các route này. */
export function mcpEndpoints(r: ApiClient['request']) {
  return {
    mcp: {
      servers: {
        list: (signal?: AbortSignal) => r<McpServer[]>('/mcp/servers', { signal }),
        create: (body: McpServerCreateBody) => r<McpServer>('/mcp/servers', { method: 'POST', body }),
        update: (id: string, body: McpServerPatchBody) => r<McpServer>(`/mcp/servers/${enc(id)}`, { method: 'PATCH', body }),
        remove: (id: string) => r<void>(`/mcp/servers/${enc(id)}`, { method: 'DELETE' }),
        discover: (id: string) => r<McpDiscoverResult>(`/mcp/servers/${enc(id)}/discover`, { method: 'POST' }),
      },
      tools: {
        list: (serverId?: string, signal?: AbortSignal) =>
          r<McpTool[]>('/mcp/tools', { signal, query: serverId ? { server_id: serverId } : undefined }),
        setAccess: (id: string, access: McpToolAccess) => r<McpTool>(`/mcp/tools/${enc(id)}`, { method: 'PATCH', body: { access } }),
        expose: (id: string, isExposed: boolean) =>
          r<McpTool>(`/mcp/tools/${enc(id)}/expose`, { method: 'PATCH', body: { is_exposed: isExposed } }),
        grant: (id: string, agentKey: string) => r<McpTool>(`/mcp/tools/${enc(id)}/grants`, { method: 'POST', body: { agent_key: agentKey } }),
        ungrant: (id: string, agentKey: string) => r<void>(`/mcp/tools/${enc(id)}/grants/${enc(agentKey)}`, { method: 'DELETE' }),
        call: (id: string, body: McpCallBody) => r<McpCallResult>(`/mcp/tools/${enc(id)}/call`, { method: 'POST', body }),
      },
      calls: (q: { cursor?: string; limit?: number; outcome?: string } = {}, signal?: AbortSignal) =>
        r<McpCallPage>('/mcp/calls', { signal, query: q }),
    },
    hub: {
      link: {
        get: (signal?: AbortSignal) => r<HubLink>('/hub/link', { signal }),
        update: (body: HubLinkPatchBody) => r<HubLink>('/hub/link', { method: 'PATCH', body }),
        test: () => r<HubLinkTestResult>('/hub/link/test', { method: 'POST' }),
      },
    },
  };
}
