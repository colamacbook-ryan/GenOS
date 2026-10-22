# Báo cáo giai đoạn 5.4 — Chịu lỗi (spec M7)

Nhánh `claude/zen-lovelace-ph1qa2` · 25/09/2026 · môi trường sandbox: Postgres 16 + pgvector + pg_partman và
Redis chạy **local** (không Docker), 15 GB RAM / 4 CPU / 29 GB đĩa trống.

Mọi ca dưới đây dừng/khởi động lại **tiến trình thật** (`pg_ctlcluster 16 main stop|start`,
`redis-cli shutdown nosave` + `redis-server`) — không mock. Test tương ứng nằm ở
`apps/api/tests/test_resilience.py` (đánh dấu `@pytest.mark.slow`, loại khỏi `pytest -q` mặc định vì làm gián
đoạn Postgres/Redis dùng chung; chạy riêng bằng `pytest -m slow -q tests/test_resilience.py`), cộng thêm hai
test không cần dừng dịch vụ thật ở `apps/api/tests/test_p3_duty.py` và `test_p3_core.py` (chuỗi model thật hết
hạn, bridge rớt giữa chừng khi đang gửi).

## Bảng kết quả

| Ca lỗi | Cách kiểm | Hành vi quan sát được | Không sập, không mất dữ liệu? |
|---|---|---|---|
| **Postgres dừng giữa lúc có request đang chờ** | `pg_ctlcluster 16 main stop -m fast` trong lúc `owner_api.get("/auth/me")` đang gọi; app chạy đầy đủ lifespan (worker nền cũng đang chạy) | Kết nối MỚI thất bại bằng `ConnectionRefusedError` (chưa từng có kết nối để asyncpg/SQLAlchemy bọc thành `DBAPIError`) → bắt được nhờ exception handler mới cho `OSError` (Starlette đối chiếu theo MRO) → **503 `SERVICE_UNAVAILABLE`**, JSON rõ ràng, không phải 500 không rõ nguyên nhân. Tiến trình test (đại diện tiến trình API) tiếp tục nhận và xử lý request tiếp theo bình thường (không treo, không crash). | ĐẠT |
| **Postgres khởi động lại — hệ thống tự phục hồi** | Sau khi `pg_ctlcluster 16 main start` + `pg_isready`, gọi lại `/auth/me` liên tục (tối đa 15s) trên **cùng** tiến trình/`AsyncEngine`, không khởi động lại app | Request thành công (200) trong vòng vài giây, không cần restart app. `pool_pre_ping=True` (`gh/db.py`) đã đủ: dò kết nối cũ/chết trong pool trước khi phát, tự mở kết nối mới khi cần. | ĐẠT |
| **Postgres dừng khi worker nền đang chạy** | Gọi trực tiếp một vòng thật của `gh.app._permit_sweep_loop` (vòng quét permit hết hạn, mẫu chung với mọi consumer `EventBus.run`) trong lúc Postgres tắt hẳn | Vòng lặp bắt lỗi bằng `except Exception`, ghi log (`"quét permit hết hạn lỗi"`), **không crash task**, thử lại ở vòng sau. Task vẫn `not done()` sau 1.5s tắt Postgres. | ĐẠT |
| **Redis dừng giữa lúc consumer group đang chạy** | `redis-cli shutdown nosave` trong lúc `EventBus.run()` đang lặp tiêu thụ một stream test | Mỗi vòng `process_once` lỗi được `run()` bắt (`except Exception: log.error(...); await asyncio.sleep(1)`), task không chết (`not done()` sau 2s Redis tắt) | ĐẠT |
| **Redis khởi động lại — consumer tự nối lại, không mất tin** | Publish tin `n=1` trước khi tắt Redis (đã được xử lý), tắt Redis, khởi động lại `redis-server`, publish tiếp tin `n=2` | Consumer group tự nối lại (không cần khởi động lại consumer/task), xử lý `n=2` bình thường; `n=1` vẫn nằm trong danh sách đã nhận trước đó (không mất) | ĐẠT |
| **Bridge rớt giữa chừng một phiên đang gửi tin** (permit đã cấp, tin đã lên `gh.bridge.outbound`, nhưng bridge mất kết nối TRƯỚC khi báo `send.result`) | Test thật: duyệt 2 bản nháp (permit cấp, tin lên hàng đợi), giả lập permit của một bản hết hạn (lùi `permit_expires_at`), gọi vòng quét | **Trước khi sửa: bản nháp kẹt vĩnh viễn ở `approved`** — không rõ đã gửi hay chưa. Đã thêm `gh.biz.core.drafts.expire_stale_permits()` + vòng quét định kỳ 15s (`gh.app._permit_sweep_loop`): chuyển sang `failed` với lý do `PERMIT_EXPIRED`, ghi Action Log, đẩy WS. Bản nháp còn hạn không bị đụng tới. `send.result` tới trễ sau khi đã hết hạn KHÔNG ghi đè lại (permit dùng một lần, điều kiện `permit_used_at IS NULL` đã không còn đúng). | ĐẠT (sau khi vá — xem "Phát hiện & sửa" bên dưới) |
| **Tất cả provider (model) trong chuỗi đều lỗi** | Test thật dùng `ModelRouter` thật (không giả lập) với 2 provider cùng trả 500 liên tiếp, qua `gh.biz.duty.engine.handle()` | Ném `ModelUnavailable` (đã có từ giai đoạn 2); đơn vị ý nghĩa **không** có quyết định/bản nháp rác, tin Redis Streams vẫn trong PEL (chưa ack) → không mất; khoá claim (`gh:duty:claim:*`) được giải phóng ở `finally` → không kẹt, lượt sau retry được; cảnh báo `model_chain_exhausted` ghi vào `biz.alerts` (tối đa 1 lần/giờ, đã có từ giai đoạn 2). Khi một provider hồi phục ở lượt sau, xử lý tiếp bình thường, không mất tin. | ĐẠT (cơ chế đã có sẵn từ giai đoạn 2; bổ sung test dùng chuỗi thật thay vì `FakeRouter`) |

## Phát hiện & sửa trong lúc làm

1. **Bản nháp kẹt "approved" mãi khi bridge rớt giữa chừng** (lỗ hổng thật, không chỉ thiếu test) — đã vá bằng
   `expire_stale_permits()` + vòng quét nền, xem commit "fix(5.4): permit gửi tin hết hạn...".
2. **`ConnectionRefusedError` lọt qua thành 500 không rõ nguyên nhân** khi Postgres/Redis tắt hẳn (kết nối MỚI
   thất bại trước khi asyncpg/SQLAlchemy kịp bọc thành `DBAPIError` — điều đó chỉ xảy ra cho lỗi SAU khi đã có
   kết nối). Đã thêm hai exception handler ở `gh/errors.py` (`db_error_handler` cho `DBAPIError` → 503
   `DB_UNAVAILABLE`, `infra_error_handler` cho `OSError` → 503 `SERVICE_UNAVAILABLE`, Starlette đối chiếu theo
   MRO nên bắt được `ConnectionRefusedError`/`ConnectionResetError`).

## Giới hạn của môi trường này

- Không có docker compose 8 dịch vụ thật (daemon Docker không chạy trong sandbox) — Postgres/Redis là dịch vụ
  local thật, không phải container, nên các lệnh dừng/khởi động dùng `pg_ctlcluster`/`redis-cli` thay vì
  `docker compose stop`. Hành vi ứng dụng quan sát được (pool_pre_ping, EventBus retry, exception handler) không
  phụ thuộc Docker nên kết luận vẫn áp dụng khi triển khai thật.
- Không kiểm được kịch bản mất mạng/đĩa đầy trong sandbox này (ngoài phạm vi 5.4 theo yêu cầu).
