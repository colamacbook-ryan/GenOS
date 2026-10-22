# API giai đoạn 1 — hợp đồng giữa web và api

Tiền tố `/api/v1`. JSON. Cookie phiên `gh_session` (HttpOnly). CSRF double-submit: cookie `gh_csrf` (đọc được bằng JS) phải khớp header `X-CSRF-Token` ở mọi request không phải GET/HEAD. Mọi request ghi có thể gửi `Idempotency-Key` (chưa thi hành — server không đọc header này).

Lỗi theo RFC 7807: `{"type": "...", "title": "...", "status": 4xx, "code": "PIN_REQUIRED", "detail": "..."}`.

| Mã | Ý nghĩa |
|---|---|
| 401 `UNAUTHENTICATED` | chưa đăng nhập / phiên hết hạn |
| 403 `FORBIDDEN` | vai trò không có quyền |
| 404 `NOT_FOUND` | không tồn tại hoặc ngoài phạm vi dữ liệu |
| 409 `CONFLICT` | ví dụ gỡ plugin nền |
| 423 `PIN_REQUIRED` | cần phiên PIN (UI bật PinDialog rồi gửi lại request) |
| 423 `PIN_LOCKED` | PIN bị khoá, `detail` có `locked_until` |
| 428 `SETUP_REQUIRED` | hệ thống chưa thiết lập xong, UI chuyển tới `/setup` |

## Auth

- `POST /auth/login` `{email, password}` → `200 Me` + set cookie. Sai → 401 `INVALID_CREDENTIALS`.
- `POST /auth/logout` → 204.
- `GET /auth/me` → `Me`:
  ```json
  {
    "id": "uuid", "email": "…", "display_name": "…",
    "role": {"code": "owner", "name": "Owner — Sếp"},
    "org": {"id": "uuid", "name": "…", "timezone": "Asia/Ho_Chi_Minh", "currency": "VND"},
    "addressing": {"self": "Anh", "bot_calls_me": "Sếp"},
    "pin_verified_until": "2026-09-23T15:30:00Z" | null,
    "permissions": {"overview.read": "all", "people_review.read": "none", "…": "…"}
  }
  ```
- `POST /auth/pin/verify` `{pin}` → `{pin_verified_until}`. Sai → 401 `PIN_INVALID` + `attempts_left`. Khoá → 423 `PIN_LOCKED`.
- `PUT /auth/pin` 🔒 `{current_pin, new_pin}` → 204.

## Khung

- `GET /navigation` → cây danh mục đã lọc theo quyền — **cây chuẩn v0.1.42** (`gh/shell/navigation.py`, giống hệt
  `apps/web/src/screens.ts`): domain `business` "Việc hằng ngày" (6 mục cấp 1: Hôm nay · Hộp thư & Việc · Khách & Cơ hội
  · Kết nối · Đội ngũ · Cài đặt) và `tech` "Nâng cao" (Tầng dữ liệu · Agent & Model · Bản đồ quan hệ · Cung ↔ Cầu ·
  Plugin & Tiện ích):
  ```json
  [{"domain": "business", "label": "Việc hằng ngày", "crumb": "HẰNG NGÀY", "icon": "ph-fill ph-briefcase", "tone": "ok",
    "collapsed": false, "count": 12,
    "groups": [
      {"key": "overview", "name": "Hôm nay", "en": "Cần Sếp xử lý · 4 số chính", "icon": "ph ph-sun-horizon", "badge": {"value": "9", "tone": "bad"} | null, "children": []},
      {"key": null, "name": "Hộp thư & Việc", "icon": "ph ph-tray", "children": [ {"key": "inbox", …} ]},
      {"key": null, "name": "Khách & Cơ hội", "icon": "ph ph-address-book", "children": [ …,
        {"key": "profile", "name": "Hồ sơ sống", …, "hidden": true} ]},
      {"key": "team", "name": "Đội ngũ", …, "children": [ {"key": "people", …, "hidden": true}, {"key": "care", …, "hidden": true} ]}
    ]}]
  ```
  `tone` ∈ `ok | warn | bad | accent`. `badge` là số thật từ API (giai đoạn 1 trả `null` khi chưa có dữ liệu).
  **v0.1.42:** domain có `collapsed: bool` (`business` false, `tech` true — "Nâng cao" mặc định gập). Node có thêm
  `"hidden": true` **chỉ khi** ẩn (node hiện bình thường không có khoá này): màn ẩn vẫn có trong cây và có route, chỉ
  không hiện ở thanh bên, và **không bao giờ có badge**. Luôn ẩn: `profile` (F-65, mở từ danh sách), `plugins` (F-41,
  đóng băng — `GET /plugins` vẫn chạy). `people`/`care` ẩn khi tổ chức chưa có nhân viên (`has_staff` = tồn tại
  `core.persons` `person_type='staff'` chưa xoá, chưa gộp). `count` = số khoá màn **không ẩn** trong domain (mọi cấp):
  Owner chưa có nhân viên `[12, 10]`, có nhân viên `[14, 10]`. Màn mới: `connections` "Kết nối" (quyền `system.read`),
  `team` "Đội ngũ" (quyền `roles.manage`); `system` đổi tên "Cài đặt" (quyền `system.read` hoặc `audit.read`).
- `GET /header` → `{"channels_live": 0, "channels_connected": 0, "groups_listening": 0, "autonomy_level": 4, "data_confidence": null}`. `channels_connected` (v0.1.43): số kênh đã từng đăng nhập thành công, kể cả khi phiên đã hết hạn.
- `GET /health` → `{"status": "ok"}`; `GET /ready` → `{"db": "ok", "redis": "ok", "objects": "ok"|"skip", "bridge": "ok"|"down"}`.
- `GET /system/health` (v0.1.36, F-6; quyền `system.read`) — thẻ "Sức khoẻ hệ thống" và dải "Cần Sếp xử lý". Đọc thuần,
  không gửi chuông; **KHÔNG thuộc `/ready`** (genh dùng `/ready` để quyết rollback — bộ xử lý nền im không được làm
  hỏng một bản cập nhật tốt). Trả `{checked_at, overall: ok|warn|bad, worker: {state: ok|silent|unknown, alive,
  last_seen_at, silent_minutes}, browser: {state: ok|silent|off, last_heartbeat_at}, queues: [{stream, dlq}],
  crons: [{name, last_at, ok}], backup: {configured, latest_at, age_hours, stale}, update: {state, failed,
  blocked_version, finished_at}, disk: {state: ok|low|unknown, free_bytes, min_bytes, checked_at}, issues: [{key, kind,
  severity: bad|warn, title, body, link, action, raised_at}]}` — mọi trường là chuỗi/số/bool/null. `issues` là các dòng
  `ops.health_alerts` đang mở (migration 0024); chuông của cùng một sự cố chỉ gửi MỘT lần (`gh/health.py::raise_once`).
  Nguồn đọc lỗi ⇒ phần đó `unknown`, không 500.

  **v0.1.37:** `update` thêm `stalled_reason: not_picked_up|process_gone|null` (lý do khi `state` = `stalled`) và
  `interrupted: rolled_back|resume|null` (lần lỗi trong 24 giờ là do genh nhận tín hiệu dừng — GH-E94B — mà không dở
  dang: `rolled_back` = chưa đụng gì/đã tự quay về, `resume` = máy tắt sau khi đã đổi CSDL, chạy lại để đi tiếp; khi khác
  null thì sự cố `update.failed` là `warn` "bị dừng giữa chừng", không phải `bad`). Thêm khối `autostart` (chỉ khi api có
  hộp thư với genh): `{state: ok|warn|unknown, linger: yes|no|unknown|not_applicable, linger_required: bool|null,
  docker_enabled: yes|no|unknown|not_applicable, docker_mode: system|rootless|desktop|unknown, checked_at}` — `ok` chỉ
  khi `docker_enabled` ∈ yes/not_applicable và linger ổn (yes/not_applicable hoặc không cần). `issues[].kind` thêm
  `host.autostart` (`severity: warn`, nút "Xem cách bật", đích `/system?tab=storage`).
- `GET /system/update` (quyền `system.read`; `POST` cần `system.manage`) — trạng thái nút "Cập nhật ngay": `{current,
  latest, update_available, updater, linked, can_request, state: idle|requested|running|done|failed|stalled, message,
  from, to, started_at, finished_at, requested_at, release_url, release_notes, published_at, auto_update_enabled,
  blocked_version, blocked_rollback_failed}`. **v0.1.37:** thêm `stalled_reason: not_picked_up|process_gone|null`
  (`process_gone` = `running` mà tiến trình genh đã chết — nhịp sống `run/genh-heartbeat.json` là nguồn chính, boot_id chỉ
  phụ), `host_busy: bool` (yêu cầu đang xếp hàng sau một lần genh khác còn nhịp sống — vẫn `requested`, không phải
  `not_picked_up`) và `interrupted: rolled_back|resume|null` (như trên). Xem `packages/contracts/src/p4-system.ts`.

## Thiết lập Owner (`/setup`)

- `GET /setup/state` → `{"finished": false, "current_step": 1, "steps": [{"n": 1, "key": "welcome", "title": "Chào mừng", "required": true, "status": "todo|doing|done|skipped"} …12]}`. Không cần đăng nhập khi chưa xong. Khi `finished=true`, mọi route `/setup/*` khác trả 409.
- `PUT /setup/steps/1` `{token, language: "vi"|"en", mode: "empty"|"sample"}` → state. Token sai → 403 `SETUP_TOKEN_INVALID`.
- `PUT /setup/steps/2` `{token, display_name, email, password, pin, pin_confirm}` → state + đăng nhập luôn (set cookie). Mật khẩu ≥ 12 ký tự; PIN đúng 6 chữ số, hai lần khớp. Lỗi trường → 422 `{"errors": {"field": "message"}}`.
- `PUT /setup/steps/3` (đã đăng nhập, Owner) `{org_name, timezone, currency, self_name, bot_calls_me}` → state.
- Bước 4–12 làm ở các giai đoạn sau; giai đoạn 1 cho phép `POST /setup/steps/{n}/skip` với bước không bắt buộc, và web hiện bước chưa làm với trạng thái "Sắp có".

## Nhật ký & plugin (phục vụ test giai đoạn 1; màn hình đầy đủ ở GĐ 4)

- `GET /audit?cursor=&limit=&actor_type=&action=` → `{"items": [{"id","at","actor_type","actor_id","actor_label","action","target_type","target_id","target_label","autonomy_level","result","detail"}], "next_cursor": "…"|null}`.
- `GET /audit/verify` → `{"ok": true, "checked": 1234, "broken_at": null}`.
- `GET /plugins` → danh sách `ops.plugins` + sức khoẻ + breaker.
- `PATCH /plugins/{package}/toggle` 🔒 `{enabled}`; `DELETE /plugins/{package}` 🔒 (409 với plugin nền).
