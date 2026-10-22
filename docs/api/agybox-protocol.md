# Hộp riêng Antigravity (`agy-box`) — hợp đồng gọi (v0.1.63, gói H1)

Mục đích: các việc có người ngoài (soạn trả lời khách, sàng lọc tin người lạ) chạy `agy` trong container riêng, không có khoá
master, DB hay Redis chính. Mã: `apps/api/gh/agybox/` (`protocol.py` hợp đồng + ký, `server.py` dịch vụ, `client.py` phía api/worker).
Cấu hình: `deploy/compose.yaml` (dịch vụ `agy-box` chỉ ở mạng nội bộ `agy-bus`, ra Internet qua proxy `agy-egress` — mục Nợ / ghi chú vận hành;
secret `gh_agy_key`).

## Cho H2 (router/placement) dùng

```python
from gh.agybox.client import AgyBoxClient, box_status, BoxUnavailable, BoxUnauthorized

client = AgyBoxClient.from_env(timeout=240, cli_home=settings.cli_home)   # None nếu hộp chưa cài (thiếu URL / tệp khoá)
# hoặc AgyBoxClient("http://agy-box:8090", "/run/secrets/gh_agy_key", 240)
await client.generate(model, messages, json_mode=..., temperature=..., effort=None)  # -> Completion
await client.discover_models()   # -> list[dict]; client.last_models_raw (đã che)
await client.list_models()       # -> list[str]
await client.diagnose(model, effort, prompt)   # chỉ trạng thái hộp + `agy models`, KHÔNG gọi thử lượt
client.kind == "antigravity_cli"; client.embed(...) -> BadRequest

st = await box_status()          # cache 10 giây; không tham số = đọc GH_AGYBOX_URL / GH_AGYBOX_KEY_FILE
# {"state": "ok"|"down"|"not_installed", "checked_at": "…Z", "agy_version": "1.2.9"|None, "cli_logged_in": None}
# (hộp không giữ phiên từ gói sửa review — `cli_logged_in` luôn None; có phiên hay không do api tự biết)
```

- `not_installed`: chưa cấu hình URL/khoá (dev, bản cài cũ trước `genh update`). `down`: đã cấu hình mà `/healthz` không trả lời.
- `timeout` của client = thời gian tối đa agy được chạy (gửi sang hộp thành `timeout_s`); client chờ thêm 10 giây cho hộp kịp báo lỗi.
- Ngoại lệ (đều là `ProviderError`): `BoxUnavailable` (không nối được), `BoxUnauthorized` (sai khoá `gh_agy_key` hoặc đồng hồ lệch
  quá 60 giây — KHÔNG phải `AuthFailed`, đừng đánh dấu phiên agy hết hạn), `AuthFailed` (agy chưa đăng nhập trong hộp),
  `RateLimited` (hết hạn mức, hoặc hộp bận: `retry_after` = 5 giây), `ModelRejected`, `BadRequest` (prompt > 120 000 byte, tên model sai),
  `ProviderError` thường (lỗi agy, quá giờ — "CLI quá Ns").
- Hộp tắt ⇒ vai ngoại giao KHÔNG rơi về `AgyClient` trong tiến trình (quyết định của H2). Prompt phải qua `mask_for_model` trước khi gọi.

### Vai nào qua hộp (gói H2 — `gh/providers/placement.py` + `gh/providers/agy_policy.py`)

| Vai | Nơi chạy agy |
|---|---|
| `core.gen` của Owner, `agent:<id>` nội bộ (không gắn kênh / nhóm / người) | trong tiến trình (`AgyClient`) |
| `core.briefing` (gói sửa review P2-3: prompt có tên hiển thị khách, tiêu đề việc), `core.reply`, `core.refinery`, `agent:<id>` ngoại giao (có `channel_scopes` / BOT nhóm / BOT người), `core.gen` của nhân viên, lượt thử agent (`purpose="setup_agent_try"`), trực việc (`purpose="duty_decide"`), khoá lạ | qua hộp (`AgyBoxClient`) |

- Một điểm quyết định: `agy_policy.decide(role, owner=, diplomatic=, box=, purpose=)` = `placement.placement(...)` ⊕ `box_status()["state"]`;
  bên gọi có DB dùng `verdict_for` / `verdicts_for`. `AgyVerdict{allowed, where, code, reason}`.
- Hộp `down` / `not_installed` ⇒ lượt thuộc vai qua hộp bỏ Antigravity khỏi chuỗi, dùng nguồn AI kế, lý do tiếng Việt vào `reasons`
  (`REASON_BOX_DOWN`, `REASON_BOX_NOT_INSTALLED`); không bao giờ tạo `AgyClient` trong tiến trình thay cho hộp. Lỗi hộp có ngắt mạch RIÊNG
  (`breaker_key(id, "box")`) và không đổi `auth_state` của nguồn agy.
- Cho gói B2 (readiness `box_down`): `placement.box_required(role, agent=None, *, owner=True, purpose=None) -> bool`
  (`agent`: `AgentRef(diplomatic=…)` hoặc `bool`); vai có `box_required` mà `box_status()["state"] != "ok"` ⇒ `box_down`.

## Giao thức HTTP (`http://agy-box:8090`)

| Đường | Ký | Yêu cầu | Trả lời 200 |
|---|---|---|---|
| `GET /healthz` | không | — | `{ok, service:"agy-box", protocol:2, agy_version, cli_logged_in:null}` |
| `POST /v1/generate` | có | `{request_id, model, effort\|null, prompt, json_mode, timeout_s, session}` | `{request_id, text, tokens_in, tokens_out, raw}` |
| `POST /v1/models` | có | `{request_id, timeout_s?, session}` | `{request_id, models:[{id,…}], raw_text}` |

- **Định dạng thật của tệp token agy 1.2.9** (kiểm trên phiên đăng nhập thật, v0.1.65; chỉ ghi tên trường, không ghi giá trị): JSON `{"token": {"access_token", "token_type", "refresh_token", "expiry"}, "auth_method", "id_token"}`; hạn truy cập là `token.expiry` (chuỗi RFC 3339, có phần giây lẻ tới nano giây), trùng hạn `exp` của `id_token`.
- `session` (protocol 2, gói sửa review v0.1.63 P1-1): nội dung tệp token agy (`antigravity-oauth-token`) của hồ sơ đang hoạt động — chuỗi JSON
  đối tượng ≤ 64 KiB. Client đọc từ kho phiên của api/worker (`GH_CLI_HOME`, mở `O_NOFOLLOW`, tệp thường, đã kiểm khuôn); không có ⇒ `AuthFailed`
  ngay ở client (không gọi hộp), đang gửi tạm khi thêm tài khoản ⇒ `ProviderError`. Hộp thiếu / sai khuôn ⇒ `400 bad_request`. Hộp đặt nó vào HOME
  tạm 0700 của đúng lượt đó (tmpfs, xoá sau lượt), KHÔNG lưu và KHÔNG trả lại — token agy làm mới trong hộp bị bỏ cùng HOME tạm.
- `request_id`: `[A-Za-z0-9_-]{1,64}`. `timeout_s`: 1–600 (mặc định 300; models 60). Prompt ghép như `AgyClient` (`[role]\n…`).
- **Ký**: header `X-GH-Ts` (giây UNIX) và `X-GH-Sig` = hex HMAC-SHA256 bằng subkey `sha256(gh_agy_key ‖ "gh-agybox:" ‖ "request")`
  trên `"gh-agybox-v1\n" + METHOD + "\n" + PATH + "\n" + TS + "\n" + thân`. Lệch giờ > 60 giây, thiếu/sai chữ ký, hoặc dùng lại đúng
  chữ ký ⇒ `401`. Trả lời đã xác thực mang `X-GH-Sig` (subkey `"response"`, trên `chữ-ký-yêu-cầu + "\n" + status + "\n" + thân`);
  client bỏ trả lời sai chữ ký. `401` không ký.
- **Lỗi**: `{"error": {"code", "message", …}, "request_id"?}`. Mã → HTTP: `bad_request` 400 · `model_rejected` 400 (+`what`, `available`, `raw`) ·
  `unauthorized` 401 · `cli_auth_failed` 403 · `not_found` 404 · `method_not_allowed` 405 · `too_large` 413 (thân > 1 MiB hoặc prompt > 120 000 byte) ·
  `rate_limited` 429 (+`retry_after`) · `busy` 429 (+`retry_after`; hộp đang chạy một lượt — `GH_AGYBOX_MAX_JOBS` luôn 1 từ gói sửa review; hoặc bộ nhớ chống phát lại đầy) · `internal` 500 ·
  `provider_error` 502 · `timeout` 504. Thông điệp lỗi đã che bí mật.
- Hộp KHÔNG gọi ngược api; kết quả là văn bản không tin cậy (parse như đầu ra agy trực tiếp).
- Client ngắt kết nối trước khi lượt xong ⇒ hộp huỷ lượt (giết nhóm tiến trình agy, xoá HOME tạm), không trả lời.
- **Dọn sau MỖI lượt** (review lần 2, P2-1; `gh.agybox.reaper`, bật bằng `GH_AGYBOX_REAP=1`): sau mọi lần chạy agy — `generate`, `models`, cả
  `agy --version` của `/healthz`, kể cả lượt lỗi / hết giờ / client ngắt — hộp `kill(-1, SIGSTOP)` (Linux: tới mọi tiến trình cùng uid trừ chính
  server và PID 1 — tini; tiến trình đang fork không lọt), duyệt `/proc`: tiến trình do `docker exec` đưa vào (healthcheck, `genh doctor`, e2e —
  tổ tiên có ppid 0) được `SIGCONT` trả lại, mọi tiến trình khác (agy để lại, kể cả đã `setsid nohup … &` thoát nhóm và được tini nhận nuôi) bị
  `SIGKILL`; rồi xoá mọi thứ trong `/tmp` và `/dev/shm` (không theo liên kết tượng trưng). `killpg` của `AgyClient` chỉ trúng nhóm của agy nên trước
  bản sửa tiến trình `setsid` sống qua các lượt: canh `/tmp/gh-agy-home-*` của lượt sau, đọc `/proc/<agy>/fd/0` (prompt khách khác), ghi
  `/proc/<agy>/fd/1`. Mọi lần chạy agy và lần dọn giữ một khoá: `/healthz` giữa lượt trả phiên bản gần nhất, không chạy `agy --version` chen vào.
  Server chỉ dọn khi là tiến trình chính của container (pid 1 hoặc con của init) — chạy tay ngoài container thì `kill(-1, …)` không bao giờ được gọi.

## Cô lập (tóm tắt) và cách kiểm

- Hộp chỉ có: secret `gh_agy_key`, env `GH_AGYBOX_KEY_FILE`, `GH_CLI_BINARY`, `AGY_CLI_DISABLE_AUTO_UPDATE`, `GH_AGYBOX_MAX_JOBS`,
  `GH_AGYBOX_REAP` (cộng biến proxy lối ra khi có). **Không mount volume nào** (từ gói sửa review, P1-1 — trước đó mount `agy_state` ghi được, dùng chung với agy
  trong tiến trình api/worker: agy trong hộp bị lừa có thể sửa `settings.json`, `mcp/`, `customizations/`, lịch sử mà agy của Gen Sếp đọc lại).
  Chỗ ghi được chỉ có tmpfs `/tmp` (HOME tạm mỗi lượt) và `/dev/shm` của Docker — cả hai bị xoá sạch sau mỗi lượt (mục "Dọn sau MỖI lượt"). `read_only`, `cap_drop: [ALL]`, `no-new-privileges`, uid `gh`, không `default`/`browser-bus`.
  Tiến trình hộp tự xoá mọi biến `GH_*` ngoài danh sách và không import `gh.config`/DB/Redis/crypto.
- Test: `apps/api/tests/test_agybox_v0163.py` (giao thức, server, client, sức khoẻ), `test_agybox_compose_v0163.py` (YAML cô lập).
- Kiểm thật (project riêng, không đụng bản cài): dựng `docker compose -p <tên riêng> -f deploy/compose.yaml up -d --no-deps agy-box`; trong hộp `ls /run/secrets` chỉ
  thấy `gh_agy_key`, `env` không có biến DB/Redis/khoá, ghi `/app` bị từ chối, `CapEff=0`, `NoNewPrivs=1`, không phân giải `db`/`redis`/`api`,
  không nối được IP của container ở mạng khác; một container tạm ở `agy-bus` gọi được hộp với HMAC đúng và nhận `401` khi sai khoá, lệch giờ, phát lại.

## Nợ / ghi chú vận hành

- **Lối ra (gói sửa review, P2-2)**: hộp CHỈ ở `agy-bus` (internal). Đường ra duy nhất là dịch vụ `agy-egress` (`python -m gh.agybox.egress`, cùng ảnh
  api, không khoá, không volume, mạng `agy-bus` + `agy-out`); hộp đặt `HTTPS_PROXY`/`HTTP_PROXY=http://agy-egress:3128` và `NO_PROXY=127.0.0.1,localhost`
  (healthcheck gọi 127.0.0.1:8090 phải đi thẳng — thiếu thì hộp "unhealthy", phát hiện khi kiểm thật), `AgyClient` chuyển các biến này vào môi trường
  sạch của agy. Proxy chỉ nhận `CONNECT host:443`, phân giải tên MỘT lần, mọi IP phải định tuyến toàn cầu (chặn loopback, RFC1918, link-local,
  CGNAT 100.64/10, gateway Docker, IPv6 nội bộ/ULA, IPv4-mapped, 6to4/NAT64 nhúng IP nội bộ) rồi nối THẲNG IP đã kiểm (chống DNS rebinding). Trước đây hộp ở
  `agy-out` (bridge thường) nên tới được gateway và mọi dịch vụ trong LAN của máy chủ.
- Allowlist tên miền: `GH_AGY_EGRESS_ALLOW` (trong `.env`, phân cách dấu phẩy, tên miền con tính theo) — **mặc định rỗng = mọi tên miền công khai**
  (chỉ chặn IP nội bộ). Đã đo (agy 1.2.9, token giả): agy có theo `HTTPS_PROXY`, thấy CONNECT tới `antigravity-unleash.goog:443`, `play.googleapis.com:443`.
  Danh sách theo quan sát + đoán cho phiên thật: `googleapis.com, accounts.google.com, antigravity-unleash.goog` — chưa đo đường gọi model/OAuth thật nên
  chưa bật mặc định (bật sai thì agy trong hộp hỏng). Nợ #44: bật allowlist chặt khi có phiên đăng nhập để ghi đủ tên miền.
- **Rủi ro còn lại — token Google của Sếp nằm trong hộp**: mỗi lượt hộp nhận tệp token OAuth (access + refresh token) của tài khoản Google đang đăng nhập
  Antigravity. Tin khách lừa được agy trong hộp chạy lệnh thì đọc được token đó và gửi ra ngoài qua proxy tới bất kỳ tên miền công khai nào (allowlist
  chưa bật) — token có quyền của tài khoản Google đó trong phạm vi OAuth agy xin. Gợi ý: dùng **một tài khoản Google riêng** cho Antigravity (không phải
  tài khoản Gmail/Drive chính của Sếp), và thu hồi phiên ở myaccount.google.com › Bảo mật nếu nghi lộ.
- **HOME tạm mỗi lượt (gói sửa review, P1-1)**: mọi lượt agy — trong hộp lẫn trong tiến trình api/worker — chạy với HOME là thư mục mới 0700
  (`clients.make_turn_home`, xoá sau lượt) chỉ chứa tệp token, và `TMPDIR` là thư mục riêng trong HOME tạm đó (kiểm thật: agy ghi bộ đệm
  `unleash-repo-schema-…json` vào thư mục tạm của hệ thống — dùng chung giữa các lượt nếu để `/tmp`); không chép `settings.json`/`mcp`/`customizations`/lịch sử. Cấu hình agy là mặc
  định của bản cài (chưa sinh `settings.json` an toàn từ mã: tên/giá trị khoá chưa kiểm được trên phiên thật — Nợ). Trong tiến trình, token agy
  làm mới được chép ngược về kho phiên bởi `clients.sync_token_back`: chỉ đúng tệp token, đối tượng JSON ≤ 64 KiB, cùng email (id_token) với bản
  đã đưa vào, dưới `flock`, so-và-đổi (tệp trong kho phải còn đúng bản đã đưa vào), tệp tạm `O_EXCL|O_NOFOLLOW` + `rename`. Lượt trong cửa sổ làm
  mới vẫn giữ khoá độc quyền cả lượt như v0.1.62. Hộp không chép ngược gì.
- Rủi ro chấp nhận: hộp và api có thể cùng làm mới access token từ một refresh token. Google OAuth không xoay vòng refresh token khi làm mới
  (theo trí nhớ, chưa kiểm tra trên phiên agy thật); nếu agy thật có xoay vòng, lượt hộp làm mới sẽ làm phiên của api hỏng ⇒ thẻ báo hết hạn —
  ghi vào Nợ #46 để kiểm khi Sếp đăng nhập.
  Định dạng trường hạn của tệp token agy thật chưa xác minh (ROADMAP Nợ #46).
- Cổng nội bộ 8090 không publish ra máy chủ; `agy-bus` là mạng `internal`.
