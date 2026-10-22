# Giao thức api ↔ browser-worker (Redis, v0.1.29)

browser-worker (`apps/browser`, ảnh `deploy/images/browser.Dockerfile`, dịch vụ `browser`) là tiến trình duy nhất chạy
Chromium (Playwright). **Không** truy cập PostgreSQL, **không** giữ khoá master, **không** gọi model, **không** thấy
Redis chính. Chỉ ở mạng nội bộ `browser` (không ra Internet); lối ra duy nhất là `browser-egress` (chỉ `CONNECT :443` tới
tên miền nền tảng, chặn IP nội bộ, ghim DNS; bản thân egress chỉ ở `browser` + `browser-out`, cũng không thấy Redis
chính/Postgres/api). Mã hai phía: `apps/api/gh/social/protocol.py` = `apps/browser/ghb/protocol.py` (bản sao, cùng
vectơ thử).

## Kênh Redis riêng (`browser-redis`)

Chromium chạy trang không tin cậy với sandbox Chromium TẮT, nên container `browser` coi như có thể bị chiếm. Vì vậy mọi
khoá `gh:browser:*` bên dưới nằm ở một Redis RIÊNG `browser-redis` (`GH_BROWSER_REDIS_URL`), không phải Redis chính
(hàng đợi arq của worker giữ khoá master, khoá phiên, realtime…):

| mạng | thành viên |
|---|---|
| `browser` (internal) | browser, browser-egress, browser-redis |
| `browser-bus` (internal) | browser-redis, api, worker |
| `browser-out` | browser-egress (ra Internet) |
| `default` | api, worker, redis, db, bridge, web, proxy |

- browser-redis: không lưu đĩa, `maxmemory 64mb noeviction`, ACL mặc định `+@all -@dangerous -@scripting` (không
  EVAL/FUNCTION, CONFIG, KEYS, FLUSH*, DEBUG, MODULE, SAVE, REPLICAOF…), rootfs chỉ đọc, `cap_drop ALL` (+SETUID/SETGID
  để hạ quyền về user redis). Không có hàng đợi arq hay khoá nào khác trên đó.
- Cờ Dừng tất cả GỐC ở Redis chính (browser không xoá được); api chép sang `browser-redis` khi bật/tắt và mỗi ~30 s
  (browser-redis khởi động lại thì nhóm consumer + bản sao cờ được tạo lại). Vé đăng nhập (`gh:social:ticket:*`) và khoá
  lịch (`gh:social:sched:*`) ở Redis chính.
- Dev/test không đặt `GH_BROWSER_REDIS_URL` → dùng chung `GH_REDIS_URL`.
- arq mã hoá việc/kết quả bằng JSON (`gh/jobcodec.py`), không pickle — lớp phòng thủ thứ hai nếu Redis chính bị ghi bậy.
- Sandbox Chromium: ưu tiên BẬT bằng user namespace không đặc quyền + hồ sơ seccomp riêng (seccomp + AppArmor mặc định của
  Docker chặn `unshare(CLONE_NEWUSER)`; Ubuntu ≥ 23.10 còn hạn chế userns). Chế độ `auto` thử bật và tự lùi về tắt khi máy
  chủ không cho; kết quả thật nằm ở trường `sandbox` của nhịp tim. Khi tắt, việc GHI bị khoá (409 `SOCIAL_WRITE_LOCKED`)
  cho tới khi Owner đồng ý rủi ro; vẫn bù bằng cách ly container/mạng.

## Khoá browser

Secret riêng `gh_browser_key` (32 byte hex/base64; `GH_BROWSER_KEY_FILE`), chung cho api, worker (arq) và browser-worker.
`genh` tự sinh (cài mới + mọi lệnh vận hành trên bản cài cũ). Khoá con = `sha256(key ‖ "gh-browser:" ‖ mục đích)`.

- **Chữ ký**: mọi thông điệp là JSON có `sig = base64url(HMAC-SHA256(khoá con, JSON chuẩn hoá mọi trường trừ sig))`
  (orjson, khoá sắp xếp). Mục đích: `job` · `result` · `control` · `input` · `frame` · `permit` (`P_PERMIT`).
- **Phiên khi truyền**: `seal = base64(nonce[12] ‖ AES-256-GCM(khoá con "transport"))`, AAD `<org_id>:<account_id>`.
  API giải rồi mã hoá phong bì bằng khoá master (`gh.crypto.encrypt`, AAD `social:<org>:<account>`) vào
  `core.social_accounts.state_enc`. Worker chỉ giữ phiên trong RAM trong lúc chạy việc.

## api → worker

`gh:browser:jobs` (stream, nhóm `browser`), trường `m`:
`{v:1, id, kind: login|health|read|write, org_id, account_id, platform, domains[], nonce, exp, payload, sig}`

| kind | payload |
|---|---|
| `login` | `{ticket, login_url, timeout_s}` — không có phiên |
| `health` | `{state: <seal>}` |
| `read` | `{state: <seal>, what: ["notifications","inbox"], limits: {max_pages ≤ 40, max_items ≤ 30}}` |
| `write` | `{state: <seal>, action: "reply_comment"\|"send_message", target_url, text, permit, timeout_s: 180}` |

Worker bỏ im lặng khi: sai chữ ký · `exp` đã qua · `SET gh:browser:nonce:<nonce> NX EX 3600` thất bại (dùng lại).
Khoá 1 việc / tài khoản: `SET gh:browser:lock:<account_id> <job_id> NX EX (timeout+120)` — bận → kết quả `failed BUSY`.

`gh:browser:control` (pub/sub, đã ký): `{type:"halt"}` → huỷ mọi việc, đóng trình duyệt ngay · `{type:"cancel", job_id}`.
`gh:browser:halt` (key): còn thì worker không chạy việc nào (kiểm trước mỗi bước).

## worker → api

`gh:browser:results` (stream, nhóm `api` — consumer trong tiến trình api), trường `m`:
`{v:1, job_id, account_id, org_id, type, data, state?: <seal>, ts, sig}`

| type | data |
|---|---|
| `started` | — |
| `login.done` | `{handle}` + `state` |
| `done` | read: `{items:[{kind, who, text, time, unread, link}], pages, page_state, cost}` + `state` (phiên làm mới) |
| `done` | write: `{action, sent, confirmed, proof, proof_sha256, trace[≤30], cost}` + `state` (xem mục "Ghi") |
| `failed` / `login.failed` | `{code: CHECKPOINT|CAPTCHA|LOGGED_OUT|BLOCKED_URL|SELECTOR|LOGIN_TIMEOUT|CANCELLED|BUSY|ERROR` (việc write thêm `PERMIT_INVALID|TARGET_NOT_FOUND|WRITE_UNSUPPORTED` — chỉ TRƯỚC khi bấm gửi)}` |
| `halted` | — |

API: việc đã đóng (huỷ/dừng/hết hạn) → bỏ kết quả đến muộn (kể cả phiên). `CHECKPOINT`/`CAPTCHA` → tài khoản `paused`
+ chuông Owner; `LOGGED_OUT` → `needs_login`; 3 lỗi liên tiếp → `paused`. Nội dung đọc được làm sạch (ký tự điều khiển,
độ dài, link ngoài tên miền) + gắn cờ `suspicious`; với Gen luôn nằm trong khối DỮ LIỆU KHÔNG TIN CẬY.

`gh:browser:heartbeat` (key, TTL 45 s, JSON không ký): `{version, at, running, sandbox}` với
`sandbox = {enabled: true|false|null, mode: "auto"|"on"|"off", reason: str|null, checked_at: iso|null}` — `null` khi chưa
dò. API coi sandbox **bật CHỈ khi nhịp tim còn VÀ `enabled === true`** (nhịp tim tắt/vắng ⇒ coi như chưa bật).

## Cửa sổ đăng nhập (chỉ lúc đăng nhập)

- Web mở `WS /api/v1/social/login/{ticket}` (chỉ đúng Owner đã mở vé; vé 32 byte, hết hạn 12 phút).
- Worker → `gh:browser:frames:<ticket>` (đã ký): `{t:"frame", data: <jpeg base64>, w, h}` (CDP `Page.startScreencast`,
  ≤ ~8 khung/giây) · `{t:"status", state: waiting|logged_in|cancelled|timeout, message}`. API chuyển cho web.
- Web → API → `gh:browser:input:<ticket>` (API lọc + ký): `mouse {action, x, y, button}` · `wheel {dx, dy, x, y}` ·
  `key {action, key ∈ danh sách}` · `text ≤ 256` · `nav back|reload` · `done` · `cancel`. Không lưu, không ghi log.
- Worker thấy đã đăng nhập (cookie `c_user`, không ở trang checkpoint/đăng nhập) → `login.done` kèm phiên.

## Ghi (việc `write`)

Trả lời bình luận / Nhắn tin đi qua: đề xuất Gen → thẻ Xác nhận + PIN → endpoint write (giới hạn lượt/ngày, cổng sandbox,
Dừng tất cả) → **permit** ký bằng khoá browser (`gh/social/permit.py`) → việc `write` → worker kiểm permit → gửi → ảnh chụp.
Đăng bài (`post`) chưa có (để lát sau).

### Permit

`protocol.sign(browser_key, "permit", claims)` (`P_PERMIT = "permit"`) với `claims` ĐÚNG các khoá sau (không thừa, không thiếu):

```json
{"v": 1, "nonce": "<hex32>", "job_id": "<id>", "org_id": "<id>", "account_id": "<id>",
 "action": "reply_comment|send_message",
 "target_url_sha256": "<sha256 hex của target_url, UTF-8>", "body_sha256": "<sha256 hex của text, UTF-8>",
 "iat": 1700000000, "exp": 1700000300, "confirmed_by": "<user_id>"}
```

- `exp = iat + 300` (TTL 5 phút, `PERMIT_TTL_S`). Chữ ký `sig` như mọi thông điệp (HMAC-SHA256, JSON chuẩn hoá).
- **Một lần**: worker `SET gh:browser:permit:<nonce> NX EX 3600` (`PERMIT_NONCE_PREFIX = "gh:browser:permit:"`). Dùng lại ⇒ từ chối.
- Hai bản `protocol.py` (api, browser) phải trùng, kể cả vectơ thử chung cho `P_PERMIT` (`test_protocol_vectors` và
  `apps/browser/tests/test_worker.py`).

### Payload việc `write`

`{state: <seal như read>, action, target_url, text, permit, timeout_s: 180}`. `target_url` phải thuộc tên miền nền tảng
(ngoài danh sách ⇒ `BLOCKED_URL`).

### Worker

1. Kiểm permit **TRƯỚC khi mở trình duyệt**: chữ ký, `exp`, `job_id`/`org_id`/`account_id`/`action` khớp việc,
   `sha256(target_url)` và `sha256(text)` khớp payload, nonce chưa dùng. Sai bất kỳ ⇒ `failed` `PERMIT_INVALID`
   (chưa mở trình duyệt, chưa chạm nền tảng).
2. Mở trang đích từ phiên đã lưu; không thấy mục ⇒ `TARGET_NOT_FOUND`; checkpoint/CAPTCHA ⇒ dừng, không gửi. Trả lời bình
   luận chọn bình luận theo `comment_id` của `target_url` (đã ký) — KHÔNG từ `page.url`; thiếu `comment_id` hoặc không thấy
   đúng bình luận ⇒ `TARGET_NOT_FOUND` (không đoán sang bình luận khác). API cũng từ chối đích trả lời không có `comment_id`.
3. Gõ `text` bằng MỘT lần chèn (`insert_text`); trễ cố định 3 giây giữa các thao tác (`GH_BROWSER_DELAY`).
4. **Kiểm Dừng tất cả lần cuối NGAY TRƯỚC bấm gửi** (cờ `gh:browser:halt` + `control`). Từ lúc bấm gửi, MỌI kết cục đều
   là `done` (không bao giờ `failed`/`halted` — tránh Owner gửi lại thành hai lần): không thấy nội dung hiện ra ⇒
   `confirmed: false`; lỗi ngay sau khi bấm ⇒ thêm `send_error: true`.
5. Chụp ảnh sau khi gửi (không dùng Playwright tracing). Đã gửi mà không có ảnh ⇒ vẫn báo `done` với `proof: null`
   (api ghi `proof_error`); mã `PROOF_MISSING` báo việc kết thúc mà không có ảnh bằng chứng.

### Kết quả `done` của write

`data = {action, sent, confirmed, proof, proof_sha256, trace, cost, send_error?}`:

- `proof` = `seal(key, jpeg, aad=f"{org_id}:{account_id}:proof:{job_id}")` hoặc `null`; ảnh JPEG ≤ 2 MB. API giải, **mã hoá
  lại bằng khoá master** khi lưu, kiểm `proof_sha256`; giữ 90 ngày; phục vụ ở `GET /social/jobs/{id}/proof`
  (`image/jpeg`, `no-store`, Owner).
- `trace` = `[{step, ms, ok}]`, tối đa 30 phần tử; không chứa nội dung trang/cookie.
- `sent` = đã bấm gửi; `confirmed` = thấy nội dung xuất hiện sau khi gửi.
- Đã bấm gửi thì vẫn chụp và báo `done` **kể cả khi Dừng tất cả vừa bật**: api chấp nhận `done` muộn của việc `write` đã
  đóng `halted` (đặt `after_halt = true`) hoặc `cancelled`/`failed` — tạm dừng, gỡ tài khoản, `WORKER_TIMEOUT` (đặt
  `after_cancel = true`); vẫn ghi Action Log + ảnh và tính vào trần gửi/ngày. Tài khoản đã gỡ: chỉ ghi Action Log. Các kết quả
  muộn khác vẫn bị bỏ.
- Action Log chỉ lưu sha256 của đích và nội dung — không lưu nguyên văn, không lưu permit.
