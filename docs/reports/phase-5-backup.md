# Báo cáo giai đoạn 5.6 — Backup/restore

Nhánh `claude/zen-lovelace-ph1qa2` · 25/09/2026 · môi trường sandbox: Postgres 16.13 + Redis local thật (không
Docker/MinIO thật — xem "Giới hạn môi trường" bên dưới).

## Cơ chế

Module mới: `apps/api/gh/backup.py` (CLI: `python -m gh.backup run|list|prune|restore`, bọc bởi `make backup` /
`make backup-list` / `make restore BACKUP=<khoá>`).

1. **`pg_dump --format=custom --no-owner`** thật (nén sẵn, có thể khôi phục lại chọn lọc), ra tệp tạm.
2. **Mã hoá**: tái dùng nguyên `gh.crypto.encrypt`/`decrypt` — đúng phong bì AES-256-GCM (DEK ngẫu nhiên mã
   hoá nội dung, khoá master `GH_MASTER_KEY`/`GH_MASTER_KEY_FILE` mã hoá DEK) đã dùng cho bí mật ở giai đoạn 1,
   chỉ đổi `associated data` (`b"gh-backup-v1"`) để tách bối cảnh — không tự chế thuật toán mới.
3. **Lưu trữ**: qua `gh.chassis.objects.ObjectStore` (điểm nối MinIO thật dựng ở giai đoạn 3 cho Tài liệu, tái
   dùng nguyên, không viết client MinIO riêng cho backup). Một danh mục JSON tự quản trong chính `ObjectStore`
   (`backups/manifest.json`: khoá, thời điểm, CSDL nguồn, kích thước, sha256) đóng vai trò "list" — vì giao
   diện `ObjectStore` chỉ có `put/get/delete`, không mở rộng nó chỉ để phục vụ backup.
4. **Vòng đời GFS 7 ngày/4 tuần/12 tháng**: `select_retained()` — hàm thuần, không đụng DB/đĩa, tự chạy sau
   mỗi lần backup mới. Thuật toán: giữ bản mới nhất của 7 ngày dương lịch gần nhất; trong phần còn lại, giữ
   bản mới nhất của 4 tuần ISO gần nhất; trong phần còn lại nữa, giữ bản mới nhất của 12 tháng dương lịch gần
   nhất; phần dư bị xoá khỏi `ObjectStore`. Docstring của hàm mô tả đầy đủ, `tests/test_backup.py` kiểm bằng
   lịch sử tổng hợp 400+ ngày (không cần dựng dữ liệu thật kéo dài cả năm) — xác nhận đúng 23 bản còn lại,
   7 bản gần nhất chắc chắn còn, phần dư bị dọn thật.
5. **Restore**: giải mã, `pg_restore --clean --if-exists --no-owner` thật vào CSDL đích — mặc định CSDL đang
   cấu hình (`GH_DATABASE_URL`), nhận `target_database` để trỏ CSDL khác (dùng khi test, không đụng CSDL đang
   dùng — đúng yêu cầu nhiệm vụ).
6. **Lịch chạy thật**: `scheduled_backup_scan` đăng ký vào `JOBS` theo đúng mẫu `CronJob` của
   `gh/biz/hooks.py`/`gh/biz/queue/jobs.py`, nối vào `WorkerSettings.cron_jobs` ở `gh/worker.py`, quét mỗi 15
   phút. Đọc `settings->'backup'` (bước 11 trình thiết lập: `frequency`, `time_of_day`) của TỔ CHỨC ĐẦU TIÊN đã
   cấu hình — quyết định tự đưa ra, có lý do: `pg_dump` sao lưu TOÀN CỤM CSDL dùng chung giữa mọi tổ chức
   (multi-tenant qua RLS, không phải một CSDL riêng mỗi tổ chức), nên lịch dùng chung khớp mô hình triển khai
   thật (một tổ chức một bản cài `genh`, giai đoạn 6). `is_due()` là hàm thuần (giờ hiện tại nằm trong cửa sổ
   15 phút quanh `time_of_day` cấu hình, VÀ chưa có bản backup nào trong chu kỳ hiện tại theo `frequency`) —
   kiểm không cần chờ đồng hồ thật trôi qua ngày/tuần/tháng. `retention_count` ở bước 11 KHÔNG dùng cho vòng
   đời GFS này (spec 5.6 khoá cứng 7/4/12) — chỉ còn ý nghĩa hiển thị/tương thích ngược với trình thiết lập.

## Kết quả test round-trip THẬT

`apps/api/tests/test_backup.py` — 16 test, chạy trong `pytest -q` mặc định (không đánh dấu `slow`, không làm
gián đoạn Postgres/Redis dùng chung, mỗi test tạo/xoá CSDL riêng của chính nó):

- 6 test thuật toán `select_retained` thuần (rỗng, dưới ngưỡng, 400 ngày liên tục → đúng 23 bản, trùng ngày chỉ
  giữ bản mới nhất, 8 ngày liên tục chưa vượt tổng 23 nên giữ hết, lịch sử thưa 45 bản trải 900 ngày → có dọn
  thật và 7 bản gần nhất luôn còn).
- 8 test `is_due` (hằng ngày/tuần/tháng, trong/ngoài cửa sổ 15 phút, đã chạy trong chu kỳ hiện tại/chưa).
- **`test_backup_restore_direct_with_clean_overwrite_and_real_encryption`**: `pg_dump` một CSDL có 3 dòng →
  xác nhận bản lưu trong `ObjectStore` là bytes **mã hoá thật** (`GH1...`, không chứa header `PGDMP` của
  `pg_dump`) → khôi phục vào một CSDL ĐÍCH đã có sẵn dữ liệu KHÁC → `pg_restore --clean --if-exists` xoá sạch
  dữ liệu cũ trước khi khôi phục → CSDL đích chỉ còn đúng 3 dòng gốc.
- **`test_backup_restore_round_trip_preserves_seed_demo_data`**: seed dữ liệu mẫu thật qua `gh.seed_demo`
  (đi qua đúng luồng raw → refinery → clean, có cơ hội/cảnh báo/agent thật) → `run_backup()` → khôi phục vào
  một CSDL **mới hoàn toàn trống** (tương đương "xoá sạch rồi khôi phục" — chọn tạo CSDL trống thay vì xoá
  ngay CSDL đang chạy của fixture, để không đụng pool kết nối SQLAlchemy đang mở trong môi trường dùng chung)
  → đối chiếu **6 bảng chính** (`raw.events`, `clean.meaning_units`, `biz.opportunities`,
  `biz.market_signals`, `biz.alerts`, `agent.identities`) có SỐ DÒNG khớp tuyệt đối trước/sau, cộng **giá trị
  cụ thể**: cơ hội MDF của "Trần Văn Hậu" đúng `value_vnd = 1.200.000.000`, cảnh báo `repeated_complaint` của
  "Nguyễn Văn Bảo" đúng `priority = P1` — không chỉ đếm tổng.

Toàn bộ 16 test XANH. Chạy `pg_dump`/`pg_restore` thật trên Postgres 16.13 (đúng bản trong PATH sandbox).

## Giới hạn của môi trường này

- **Không có Docker/MinIO thật chạy sẵn trong sandbox** — `ObjectStore` dùng cài đặt mặc định `LocalObjectStore`
  (đĩa cục bộ, dựng ở giai đoạn 3), y như module Tài liệu. Khi cài đặt thật bằng `docker compose` (dịch vụ
  `objects` trong `deploy/compose.yaml` đã chạy MinIO), chỉ cần thêm một lớp `ObjectStore` mới (vd
  `MinioObjectStore`) và đổi `get_object_store()` — `gh/backup.py` không cần đổi gì vì chỉ gọi qua giao diện
  `ObjectStore`. **Khuyến nghị cho giai đoạn 6** (trình cài `genh`): cấu hình MinIO thật đúng điểm nối này khi
  dựng trình cài một lệnh.
- Test round-trip khôi phục vào CSDL **mới trống** (không phải xoá sạch rồi khôi phục lại đúng CSDL cũ) — lựa
  chọn có chủ đích để tránh làm gián đoạn pool kết nối của các test khác chạy trên cùng Postgres dùng chung;
  cơ chế `pg_restore --clean --if-exists` (đã kiểm ở test đầu tiên, khôi phục đè lên CSDL có dữ liệu khác) bảo
  đảm hành vi tương đương khi áp dụng thật vào một CSDL cần "xoá sạch rồi khôi phục".
- Lịch backup định kỳ (`scheduled_backup_scan`) kiểm bằng test đơn vị thuần cho `is_due()` (không chờ đồng hồ
  thật trôi ngày/tuần/tháng thật trong CI) — chưa có test tích hợp chạy `arq` worker thật qua nhiều ngày.
