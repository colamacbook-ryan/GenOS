-- Gen-Harness · v0.1.43 (F-25): dọn hàng agent.bindings của các khoá lõi đã bỏ (core.intent/core.scoring/core.indexing).
-- Không nơi nào gọi model bằng các khoá này; API đã ẩn chúng và PUT/DELETE trả 422, nên hàng cũ (bước 4 thiết lập trước
-- v0.1.43 tự tạo) thành dữ liệu mồ côi giữ khoá ngoại RESTRICT tới agent.models. Chạy lại an toàn (DELETE idempotent).
DELETE FROM agent.bindings WHERE agent_key IN ('core.intent', 'core.scoring', 'core.indexing');
