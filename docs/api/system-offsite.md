# Bản sao ngoài máy — `/api/v1/system/offsite` (v0.1.40, F-12)

Một gói `.ghbundle` mã hoá được genh chép ra **ổ USB/NAS** mỗi tuần (Chủ nhật ~05:30 giờ máy, lịch
`gen-harness-offsite`, bù lần lỡ), kiểm đọc lại bằng `python -m gh.bundle verify`, giữ 4 bản gần nhất. Container
api không thấy ổ ngoài: Console chỉ để lại **yêu cầu** trong hộp thư chung `<gốc cài>/run` (bind mount
`GH_HOST_LINK_DIR`) và đọc lại **trạng thái** genh ghi — cùng cơ chế với "Cập nhật ngay" và "Khôi phục".

Mã nguồn: `apps/api/gh/system_api/offsite.py`, chuông ở `apps/api/gh/health.py` (`_eval_offsite`), kiểm gói ở
`apps/api/gh/bundle.py` (`verify`).

## Endpoint

| Phương thức & đường dẫn | Quyền | Mô tả |
|---|---|---|
| `GET /system/offsite` | `system.read` | Trạng thái (xem khuôn bên dưới). |
| `PUT /system/offsite/destination` `{path}` | Owner + PIN `offsite.destination` | "Chọn nơi lưu bản sao ngoài máy" — ghi yêu cầu `set`. 202. |
| `POST /system/offsite/run` | `system.manage` | "Sao lưu ra ổ ngoài ngay" — ghi yêu cầu `run`. 202. |
| `POST /system/offsite/disable` | Owner + PIN `offsite.destination` | Tắt bản sao ngoài máy — ghi yêu cầu `disable`. 202. |
| `GET /system/offsite/recovery-kit` | Owner + PIN `offsite.recovery_kit` | "Bộ khôi phục": Khoá khôi phục + các bước. `Cache-Control: no-store`. |
| `GET /system/offsite/portable` | Owner + PIN `offsite.portable` | "Tải gói mang đi": tệp `.ghbundle` mã hoá bằng Khoá khôi phục. |

Ba endpoint ghi yêu cầu trả lại đúng khuôn `GET /system/offsite`.

### `GET /system/offsite`

```json
{
  "configured": true,
  "dest": "/media/usb/gen-harness",
  "state": "ok",
  "error_code": null,
  "message": "Bản sao ngoài máy gần nhất đã kiểm đọc lại được",
  "last_attempt_at": "2026-10-04T05:31:02Z",
  "last_success_at": "2026-10-04T05:31:02Z",
  "age_days": 0,
  "stale": false,
  "last_size_bytes": 123456789,
  "verified": true,
  "key_id": "0badc0de",
  "schedule": "systemd",
  "request": {"state": "idle", "action": null, "requested_at": null},
  "can_request": true,
  "manual_command": null,
  "key_present": true
}
```

- `state`: `ok | failed | not_mounted | not_configured | running | skipped_busy | unknown` — giá trị lạ trong tệp ⇒
  `unknown`; thiếu `offsite-status.json` ⇒ `not_configured`, `configured=false`.
- `error_code`: `null` hoặc `GH-EB00…GH-EB07`; mã lạ ⇒ `"unknown"`.
- `message`: API tự ghép theo `state`/`error_code` từ bảng tiếng Việt cố định (KHÔNG lấy chữ từ tệp run/).
- `dest`: bỏ ký tự điều khiển, cắt ≤ 200 ký tự; rỗng ⇒ `null`.
- `stale`: chưa có lần thành công hoặc `last_success_at` cũ hơn 7 ngày 12 giờ (`health.OFFSITE_STALE_AFTER` = lịch tuần
  + ân hạn: `last_success_at` là giờ BẮT ĐẦU lượt trước, timer trễ ngẫu nhiên tới 30 phút ⇒ không báo cũ giả mỗi tuần).
- `schedule`: `systemd | cron | launchd | schtasks` hoặc `null`; `key_id`: 8 hex hoặc `null`.
- `request.state`: `idle | requested | stalled` (yêu cầu nằm > 15 phút mà genh không giữ khoá ⇒ `stalled`).
- `can_request`: `genh.json` có `updater` VÀ `"offsite"` trong `requests` VÀ `run/request/` ghi được.
- `manual_command`: lệnh Owner tự chạy trên máy chủ khi chưa có watcher (Windows / máy cài trước v0.1.40) — CHỈ khi
  đang có yêu cầu chờ (`request.state` = `requested|stalled`), theo `action`/`path` của chính yêu cầu đó:
  `genh offsite run`, `genh offsite disable`, `genh offsite set "<đường dẫn>"`. `null` khi không có yêu cầu, hoặc
  đường dẫn không ghép an toàn được (có `"`, `$`, `` ` ``, ký tự điều khiển, không tuyệt đối, > 400 byte) — không bao giờ
  trả lệnh chứa chỗ giữ chỗ.
- `key_present`: có tệp Khoá khôi phục (`GH_OFFSITE_KEY_FILE`, mặc định `/run/secrets/gh_offsite_key`).

### `PUT /system/offsite/destination`

Kiểm sơ bộ `path` (genh kiểm thật: ổ đã gắn, khác ổ máy chủ, ghi được) ⇒ 422 `VALIDATION`, `errors.path`:

- tuyệt đối: POSIX `/…`, Windows `X:\…` (hoặc `X:/…`), UNC `\\…`;
- ≤ 400 byte UTF-8 (như genh — chữ có dấu tính 2–3 byte); không xuống dòng / ký tự điều khiển; không rỗng.

`allow_same_disk` (lưu ngay trên ổ máy chủ) **chỉ** đặt được từ CLI genh (Owner tự quyết rủi ro, có cảnh
báo) — Console không bao giờ gửi trường này.

### `GET /system/offsite/recovery-kit`

```json
{"key": "ABCDE-FGHIJ-KLMNO-PQRST-UVWXY-23456", "key_id": "1a2b3c4d", "created_hint": "2026-10-01",
 "steps": ["Cài Gen-Harness trên máy mới …", "Cắm ổ USB …", "Chạy trên máy mới: genh import --yes <tệp .ghbundle>",
           "Khi được hỏi mật khẩu gói, nhập Khoá khôi phục này …", "Đăng nhập Console …"],
 "warning": "Cất Bộ khôi phục TÁCH khỏi ổ USB: ai có cả hai sẽ đọc được toàn bộ dữ liệu"}
```

`key_id` = 8 ký tự hex đầu `sha256(khoá)`; `created_hint` = ngày sửa tệp khoá (YYYY-MM-DD) hoặc `null`. Nhật ký
thao tác `offsite.recovery_kit_viewed` chỉ ghi `key_id` — KHÔNG bao giờ ghi khoá.

### `GET /system/offsite/portable`

Chạy `python -m gh.bundle export --out <tệp tạm>` (tiến trình con, `GH_BUNDLE_PASSWORD` = Khoá khôi phục qua biến
môi trường — không qua argv), khoá Redis `gh:offsite:portable:lock` (NX, TTL 2 giờ), giới hạn 30 phút. Trả
`FileResponse` stream (không nạp cả gói vào RAM), `Content-Disposition: attachment;
filename="gen-harness-mang-di-YYYYMMDD-HHMM.ghbundle"` (giờ theo múi giờ tổ chức), `Cache-Control: no-store`; tác vụ
nền xoá tệp tạm và nhả khoá sau khi gửi. Nhật ký `offsite.portable_downloaded` {size_bytes, key_id}. Mở trên máy mới
bằng `genh import` + Khoá khôi phục (Bộ khôi phục).

## Mã lỗi API

| HTTP | `code` | Khi nào |
|---|---|---|
| 409 | `OFFSITE_UNAVAILABLE` | Máy chủ chưa nhận lệnh từ Console. Kèm `manual_command` theo đúng việc (`run`/`disable`/`set "<path>"`); `null` khi `path` không ghép an toàn được. |
| 409 | `OFFSITE_IN_PROGRESS` | Đang có yêu cầu chờ genh, hoặc `state=running` bắt đầu chưa quá 2 giờ. |
| 409 | `UPDATE_IN_PROGRESS` / `RESTORE_IN_PROGRESS` | Đang cập nhật / khôi phục (thứ tự xử lý: update > restore > offsite). |
| 409 | `OFFSITE_KEY_MISSING` | Thiếu tệp Khoá khôi phục (recovery-kit, portable). |
| 409 | `PORTABLE_IN_PROGRESS` | Đang tạo một gói mang đi khác (khoá Redis). |
| 500 | `PORTABLE_FAILED` | Xuất gói lỗi/quá 30 phút — `title` "Không tạo được gói mang đi", `detail` là Chi tiết kỹ thuật (không chứa khoá). |
| 422 | `VALIDATION` | `errors.path` tiếng Việt. |
| 423 | `PIN_REQUIRED` | Chưa nhập PIN. |

## Hợp đồng tệp với genh (run/ là 0777 — API coi là KHÔNG tin cậy)

### `run/offsite-status.json` (genh ghi nguyên tử)

```json
{"schema": 1, "configured": true, "dest": "/media/usb/gen-harness",
 "state": "ok|failed|not_mounted|not_configured|running|skipped_busy",
 "error_code": "GH-EBxx" , "last_attempt_at": "RFC3339 Z", "last_success_at": "RFC3339 Z",
 "last_file": "…", "last_size_bytes": 0, "verified": true, "kept": 4,
 "schedule": "systemd|cron|launchd|schtasks", "key_id": "8 hex"}
```

Chuỗi rỗng `""` cho mã lỗi/thời điểm/lịch khi chưa có.

### `run/request/offsite.json` (API ghi nguyên tử: tệp `.tmp` rồi đổi tên; genh xoá trước khi làm)

```json
{"id": "uuid", "action": "set|run|disable", "path": "chỉ khi set", "requested_at": "RFC3339 Z", "by": "actor_id"}
```

`run/genh.json` có `"offsite"` trong `"requests"` khi watcher nhận loại yêu cầu này.

### Mã lỗi genh

| Mã | Ý nghĩa | Thông điệp Console |
|---|---|---|
| GH-EB00 | chưa chọn nơi lưu | Chưa chọn nơi lưu bản sao ngoài máy |
| GH-EB01 | chưa thấy ổ USB/NAS | Chưa thấy ổ USB/NAS — cắm lại ổ rồi bấm 'Sao lưu ra ổ ngoài ngay' |
| GH-EB02 | xuất gói lỗi | Không xuất được gói dữ liệu — thử lại sau ít phút |
| GH-EB03 | kiểm gói lỗi (= chưa có bản sao) | Bản sao vừa tạo không đọc lại được — chưa có bản sao ngoài máy |
| GH-EB04 | không ghi được vào đích | Không ghi được vào nơi lưu — kiểm tra ổ còn chỗ trống và cho phép ghi |
| GH-EB05 | bận cập nhật/khôi phục | Máy chủ đang cập nhật/khôi phục — lần sao lưu ra ổ ngoài sẽ thử lại sau |
| GH-EB06 | dịch vụ chưa chạy | Dịch vụ Gen-Harness chưa chạy — bật lại rồi thử lại |
| GH-EB07 | đích không hợp lệ | Nơi lưu không hợp lệ — chọn lại thư mục trên ổ USB/NAS |

### Khoá khôi phục

`secrets/gh_offsite_key`: mật khẩu gói `.ghbundle` của bản sao ngoài máy — 6 nhóm × 5 ký tự base32 HOA nối `-`,
không xuống dòng (API vẫn `strip()`). Docker secret chỉ mount vào `api` tại `/run/secrets/gh_offsite_key`
(`GH_OFFSITE_KEY_FILE`). Không log/in/commit khoá; chỉ rời máy chủ khi Owner + PIN.

## `python -m gh.bundle verify --in <path|->`

Mật khẩu qua `GH_BUNDLE_PASSWORD`. KHÔNG đụng CSDL. Giải mã → giải nén an toàn vào thư mục tạm (dọn trong `finally`)
→ manifest (`package_version`, `sha256(db.dump)`, số object trong `objects/`) → `pg_restore --list db.dump`.

- Thoát `0`: stdout đúng một dòng
  `{"ok":true,"alembic_revision":"…","objects":N,"db_dump_bytes":N,"created_at":"…"}` (không bí mật, không keys.json).
- `2`: sai khoá / gói hỏng (GCM, tar, manifest, sha256, số object) / `pg_restore --list` lỗi ("không đọc được bản
  CSDL"). `3`: magic / phiên bản phong bì / `package_version` không tương thích. `1`: lỗi khác (thiếu mật khẩu…).
- Log ra stderr.

## `/system/health` và dải "Cần Sếp xử lý"

Khối `offsite` **chỉ** có khi có hộp thư run/:
`{state, configured, last_success_at, age_days, stale, error_code, schedule}`. Ở đây `stale` không tính tổ chức mới
tạo trong `OFFSITE_STALE_AFTER` chưa có lần nào; `stale` ⇒ `overall` tối đa `warn`.

Vòng theo dõi (`health.evaluate`, phần `offsite`, savepoint riêng) mở/đóng `ops.health_alerts` (key = kind),
link `/system?tab=storage&focus=offsite`:

| kind | Khi nào | Mức | fingerprint |
|---|---|---|---|
| `offsite.stale` | chưa cấu hình và tổ chức > 7 ngày 12 giờ — "Chưa có bản sao ngoài máy" | warn | `not_configured` |
| `offsite.stale` | đã cấu hình, chưa có lần thành công và tổ chức > 7 ngày 12 giờ — "Chưa có bản sao ngoài máy" | warn | `never` |
| `offsite.stale` | `last_success_at` cũ hơn 7 ngày 12 giờ — "Bản sao ngoài máy đã cũ N ngày" | warn; bad khi > 30 ngày | mức (`warn`/`bad`) |
| `offsite.failed` | `state` failed/not_mounted và `last_attempt_at` > `last_success_at` — thân theo mã lỗi | warn | `last_attempt_at|error_code` |

Hết điều kiện ⇒ đóng từng key (`offsite.failed` để nguyên khi `state=running`). Nhãn nút (`health.ACTIONS`):
`offsite.stale` "Chọn nơi lưu / sao lưu ngay", `offsite.failed` "Xem bản sao ngoài máy", `job.timeout` "Xem sức khoẻ"
(job.timeout do worker mở/đóng, link `/system?tab=storage&focus=health`). Người xem KHÔNG phải Owner: nhãn
`offsite.stale` thành "Xem bản sao ngoài máy" (`NON_OWNER_ACTIONS`) và thân sự cố bảo bấm nút chỉ Owner có
(`offsite.stale` `not_configured`, `offsite.failed` GH-EB00/GH-EB07) đổi sang câu "nhờ Owner…" (`NON_OWNER_BODIES`,
chọn theo fingerprint).

`GET /system/offsite/portable` tải trong khung ẩn cùng gốc: api đặt `X-Frame-Options: SAMEORIGIN` + CSP
`default-src 'none'; frame-ancestors 'self'` cho riêng đường này (cả phản hồi lỗi) — proxy Caddy chỉ đặt DENY /
`frame-ancestors 'none'` khi upstream chưa có (`?`), nên web đọc được mã lỗi JSON trong khung.
