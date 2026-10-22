# 04 · Kiến trúc

## Monorepo

```
Gen-Harness/
├─ apps/
│  ├─ web/              React 18 + TypeScript + Vite · Console + trình thiết lập Owner
│  ├─ api/              Python 3.12 · FastAPI · SQLAlchemy 2 · Alembic · Pydantic v2
│  ├─ worker/           Python · cùng mã với api · refinery, scoring, compaction, partman, refresh MV, backup
│  └─ bridge/           Node 20 · TypeScript · mỗi kênh một adapter (zalo, whatsapp, telegram…)
├─ installer/
│  ├─ bootstrap/        install.sh (POSIX sh) · install.ps1 (PowerShell 5.1+)
│  └─ genh/             Go 1.22 · Bubble Tea + Lip Gloss · binary tĩnh, đa nền tảng
├─ deploy/
│  ├─ compose.yaml      nhúng vào genh lúc build (go:embed)
│  ├─ caddy/            reverse proxy + TLS nội bộ tự ký
│  └─ images/           Dockerfile từng dịch vụ (multi-stage, distroless khi có thể)
├─ packages/
│  ├─ ui/               component React theo token Nocturne
│  ├─ tokens/           sinh từ design/tokens.json → CSS variables + TS
│  └─ contracts/        OpenAPI sinh từ api → client TS (openapi-typescript)
├─ db/                  migration Alembic + seed
└─ docs/handoff/        gói bàn giao này
```

## Dịch vụ trong compose

| Dịch vụ | Image | Vai trò | Cổng nội bộ |
|---|---|---|---|
| `proxy` | caddy:2 | TLS, định tuyến `/` → web, `/api` → api, `/ws` → api | 8443 ra ngoài |
| `web` | nginx-unprivileged + bundle tĩnh | Console | 8080 |
| `api` | gen-harness/api | REST + WebSocket, auth, RBAC, policy | 8000 |
| `worker` | gen-harness/api (lệnh khác) | job nền: refinery, scoring, compaction, MV, partman, backup | — |
| `bridge` | gen-harness/bridge | kết nối kênh, QR, gửi/nhận | 7000 |
| `db` | postgres:16 + pgvector + pg_partman | SSOT | 5432 |
| `redis` | redis:7 | hàng đợi job, pub/sub realtime, rate-limit, khoá phân tán | 6379 |
| `objects` | minio | tệp đính kèm, tài liệu, bản lưu trữ lạnh, backup | 9000 |
| `mcp-*` | tuỳ | máy chủ MCP stdio chạy như sidecar khi Owner bật | — |

Chỉ `proxy` mở cổng ra host. Mọi dịch vụ khác nằm trong mạng `internal` của compose. Healthcheck cho từng dịch vụ; `api` chờ `db` và `redis` healthy.

## Luồng chính

```
Zalo/WhatsApp ⇄ bridge ──INSERT──▶ raw.events ──NOTIFY──▶ worker.refinery
                  ▲                                         │
                  │                          clean.* · memory.* · biz.* · analytics
                  │                                         │
      agent trực kênh ◀──đọc ngữ cảnh ID nhóm + ID người────┘
                  │
                  ├─ tự trị đủ mức ─▶ bridge gửi ─▶ action_log
                  └─ cần duyệt ─────▶ biz.action_drafts ─▶ Bàn làm việc ─▶ Owner duyệt ─▶ bridge gửi
```

- **Refinery**: kích hoạt khi `interval_seconds` hết HOẶC số bản ghi `pending` ≥ `count_threshold`. Lấy lô bằng `FOR UPDATE SKIP LOCKED` để chạy song song an toàn. Mỗi lượt ghi `refinery.runs`.
- **Agent trực kênh**: dựng prompt từ (1) danh tính agent, (2) sổ tay của ID người + ID nhóm, (3) tối đa N `meaning_units` liên quan theo thời gian và độ tương đồng, (4) điểm hiện tại. Kết quả qua **policy engine** trước khi thực thi.
- **Compaction**: khi `token_used ≥ 90% token_budget` hoặc mỗi 24 giờ, worker nén `memory.entries` không ghim thành bản tóm tắt, ghi `memory.compactions`, đánh `archived_at` cho mục bị nén.

## Plugin

- Plugin là gói có `manifest.json`: `package`, `version`, `layer`, `entry`, `permissions[]`, `events.subscribe[]`, `events.publish[]`, `settings_schema`, `sandbox`.
- **Plugin nền** (`origin=core`, layer `chassis`) nạp đầu tiên theo `load_order`, không gỡ được, chỉ tắt được nếu manifest cho phép.
- **Plugin cài thêm** (`origin=marketplace|local_file`) chạy trong tiến trình con cách ly (Python subprocess hoặc container riêng tuỳ `sandbox.mode`), giới hạn RAM/timeout/mạng, giao tiếp qua event bus (Redis Streams). Khi cài: kiểm chữ ký, hiện danh sách quyền xin, yêu cầu PIN.
- **Circuit breaker** mỗi plugin: mở khi tỉ lệ lỗi > ngưỡng trong cửa sổ, nửa mở sau thời gian nghỉ, ghi `ops.breaker_events`. Lỗi một plugin không làm sập tiến trình chính.

## API

- REST JSON dưới `/api/v1`, OpenAPI tự sinh; client TS sinh từ OpenAPI — không viết tay kiểu dữ liệu ở frontend.
- WebSocket `/ws` cho: hàng đợi cần xử lý, log plugin LIVE, nhật ký MCP LIVE, tiến độ refinery, trạng thái phiên QR, tiến độ thiết lập.
- Phân trang con trỏ (`?cursor=&limit=`) cho mọi danh sách lớn.
- Mọi endpoint ghi nhận `Idempotency-Key`.
- Thao tác nhạy cảm (đăng xuất kênh, đổi tài khoản CLI, cài plugin, đổi quyền, xem khoá) yêu cầu header phiên PIN còn hạn; hết hạn trả `423 PIN_REQUIRED` → UI bật hộp nhập PIN.

Nhóm endpoint theo màn (`design/screens.json` → `key`): `/overview`, `/inbox`, `/drafts`, `/groups`, `/persons`, `/graph`, `/profiles/{id}`, `/notebooks/{type}/{id}`, `/opportunities`, `/market`, `/search`, `/reviews`, `/care`, `/raw`, `/rules`, `/clean`, `/identity`, `/agents`, `/providers`, `/models`, `/mcp`, `/plugins`, `/channels`, `/roles`, `/audit`, `/setup`.

## Frontend

- React Router: một route mỗi `key` trong `design/screens.json`, lồng theo `parent` để breadcrumb và danh mục phân cấp sinh từ cùng một cây (không khai báo hai nơi).
- TanStack Query cho dữ liệu server; Zustand cho trạng thái UI (danh mục mở/đóng, tab, bộ lọc — lưu vào URL query để chia sẻ được).
- `packages/ui`: Button, IconButton, Tag/Chip, Switch, Tabs, Segmented, Card, Table (ảo hoá khi >200 dòng), KPI, ProgressBar, Heatmap, GraphCanvas (SVG + d3-force hoặc elkjs; vị trí node lưu được), PinDialog, EmptyState, ErrorState, Skeleton.
- Icon `@phosphor-icons/react`. Font Inter tự host.
- i18n: `vi` mặc định, khoá dịch cho mọi chuỗi; phụ đề tiếng Anh dưới tiêu đề là một tuỳ chọn hiển thị (`showEnglish`).

## Bảo mật

- Mật khẩu và PIN băm argon2id. PIN sai 5 lần → khoá 15 phút + báo Owner qua kênh riêng.
- Phiên cookie `HttpOnly; Secure; SameSite=Strict`; CSRF token cho request ghi.
- RBAC kiểm ở tầng service, không chỉ ở route. Dữ liệu `people_review.*` chỉ Owner (và vai trò được cấp rõ).
- Mã hoá phong bì cho mọi bí mật; khoá master ở Docker secret `gh_master_key`.
- TLS tự ký cho `https://localhost:8443`, trình cài đưa CA nội bộ vào kho tin cậy của hệ điều hành nếu người dùng đồng ý; hỗ trợ domain thật + Let's Encrypt qua Caddy.
- MCP: mặc định chặn mạng công cộng; tool `write` luôn tạo `action_drafts` trước.

## Quan sát

- Log JSON có `trace_id`; OpenTelemetry tuỳ chọn.
- `/api/health` (liveness) và `/api/ready` (DB, Redis, objects, bridge) — trình cài và `genh status` dùng chung.
- Các chỉ số màn Sức khoẻ hệ thống lấy từ `ops.breaker_events`, `agent.model_calls`, heartbeat bridge.

## Backup

- `worker` chạy `pg_dump` định dạng custom + snapshot MinIO theo lịch Owner đặt ở bước 11, mã hoá bằng khoá backup riêng, giữ theo vòng (7 ngày / 4 tuần / 12 tháng).
- `genh backup` / `genh restore <file>` dùng cùng cơ chế, chạy bên trong container nên máy host không cần `pg_dump`.
