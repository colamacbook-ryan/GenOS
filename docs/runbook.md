# Sổ tay vận hành Gen-Harness

Dành cho Sếp hoặc người trực máy chủ. Lệnh `genh` chạy **trên máy chủ** (không phải trong Console). `genh` nằm ở `~/.gen-harness/bin/genh`
(Windows: `%LOCALAPPDATA%\GenHarness\bin\`) và đã được thêm vào PATH; mọi lệnh nhận cờ chung `--install-dir DIR` và `--port N`.
Lệnh đầy đủ: `genh help`; đặc tả chi tiết: [docs/handoff/05-installer.md](handoff/05-installer.md). Việc đang treo của Sếp: [HANDOFF](reports/HANDOFF-v0.1.1.md).
Mục này không thay README: cài đặt lần đầu và dựng từ mã nguồn ở [README](../README.md).

## 1. Kiểm nhanh khi nghi có chuyện

1. `genh status` — bảng dịch vụ (`proxy web api worker bridge browser browser-redis browser-egress db redis`), "healthy", phiên bản, dung lượng. Dịch vụ `migrate` chạy một lần rồi thoát — không phải lỗi.
2. Mở Console, vào **Cài đặt** (Mặt tiền › **Thêm** › Cài đặt nâng cao › **Mở Console đầy đủ** › **Cài đặt** › **Sao lưu & cập nhật**): thẻ **Sức khoẻ hệ thống** ở đầu trang (cập nhật, sao lưu, ổ đĩa, bộ xử lý nền, kênh, Gen-hub…) — mỗi sự cố có nút việc cần làm.
   Sự cố đang mở cũng hiện ở màn **Hôm nay** (dải **Cần Sếp xử lý**) và ở danh sách **Việc nên làm tiếp** của Mặt tiền › Thêm.
3. `genh logs api -f` (hoặc `worker`, `bridge`, `browser`…; bỏ tên để xem mọi dịch vụ, 200 dòng cuối; `-f` để theo dõi). Log là JSON có `ts`, `error_id`, `request_id`.
4. Dịch vụ dừng: `genh start`. Mở Console: `genh open`.
5. Vẫn chưa rõ → làm **gói chẩn đoán** (mục 6) và gửi cho Claude.

## 2. Cập nhật

- **Tự động mỗi đêm ~03:00** (`genh auto-update status|enable|disable`). Lịch đêm chọn bản *chính thức* **cao nhất đã đủ 24 giờ** trong 10 bản gần nhất — bản mới có thể đợi 1–2 đêm.
  Từ v0.1.53 lịch **tự lành**: `genh update` bật lại lịch đêm nếu unit mất/tắt/không chạy, trừ khi Sếp đã chủ động tắt bằng `genh auto-update disable` (genh nhớ dấu `config/auto-update-disabled.json`).
- **Kiểm lịch đêm**: chạy `genh auto-update status` — phải thấy dòng đầu **BẬT**, "Linger: có" (linger = tiến trình nền chạy cả khi không ai đăng nhập) và một "Lần kế tiếp" (~03:00). Thấy **BẬT NHƯNG KHÔNG CHẠY** hoặc **TẮT** (mà Sếp không tắt): `genh auto-update enable`; Linger KHÔNG thì chạy trước: `sudo loginctl enable-linger $USER` (máy hỏi mật khẩu đăng nhập máy).
  Xem timer bằng tay: `systemctl --user list-timers --all | grep gen-harness` (tên unit là `gen-harness-update.timer`; `grep genh` không khớp). Thẻ Sức khoẻ hệ thống ở **Cài đặt › Sao lưu & cập nhật** (`/system?tab=storage&focus=health`) có dòng **Tự cập nhật đêm**; lịch tắt hoặc im quá 36 giờ thì có chuông **"Lịch tự cập nhật đêm chưa chạy N ngày"** (nút Xem cách bật lại).
- **Ngay lập tức**: Console → **Cài đặt › Sao lưu & cập nhật** → **Cập nhật ngay** (Trợ giúp › Cập nhật phần mềm chỉ là liên kết tới đúng trang này), hoặc `genh update` (không kèm `--yes` thì bỏ qua thời gian chín).
- Thứ tự an toàn của `genh update`: tự thay binary `genh` (kiểm SHA-256, chạy lại bằng bản mới) → kiểm đĩa → tải ảnh bản mới *trước* → sao lưu (`pre-update`) → migrate → khởi động lại → kiểm sẵn sàng.
  Lỗi ở bất kỳ bước nào → tự quay về bản cũ. Dịch vụ đã khớp bản này thì **bỏ qua** (không sao lưu, không tải).
- Tuỳ chọn: `--yes --quiet` (cách lịch đêm gọi), `--no-self-update` (chỉ nâng dịch vụ, không đụng binary `genh`).
- Có gì mới ở mỗi bản: [CHANGELOG.md](../CHANGELOG.md).

## 3. Quay về bản cũ (rollback)

`genh` **không có lệnh `rollback`**. Quay về bản cũ xảy ra **tự động** khi cập nhật lỗi từ bước migrate trở đi: dựng lại ảnh cũ, và nếu CSDL đã bị migrate thì khôi phục bản sao lưu `pre-update`
bằng một container tạm từ ảnh cũ. Sau đó lịch đêm **không thử lại** bản bị chặn (`run/update-blocked.json`) cho tới khi có bản mới hơn.

- Bản mới *chạy được* nhưng có lỗi nghiệp vụ → báo Claude (kèm gói chẩn đoán); sẽ có bản sửa. Không tự hạ bản bằng tay: CSDL đã migrate tới lược đồ mới.
- Chỉ muốn lấy lại **dữ liệu** của một thời điểm cũ → mục 4 (khôi phục bản sao lưu).

## 4. Sao lưu và khôi phục

- **Tự sao lưu** theo lịch ở Cài đặt (bước 11 của trình thiết lập); giữ 7 bản hằng ngày + 4 hằng tuần + 12 hằng tháng. Danh sách và nút **Khôi phục** (nhập PIN): Console → Cài đặt › Sao lưu & cập nhật.
- `genh backup [--to <đường dẫn>]` — sao lưu ngay (`--to`: chép thêm ra máy chủ). `genh restore <khoá bản sao lưu>` — tự sao lưu an toàn, dừng api/worker, khôi phục, migrate, khởi động lại;
  lỗi → quay về bản an toàn. Mã bản sao lưu lấy ở danh sách trong Console.
- **Bản sao ngoài máy** (khuyên bật): `genh offsite set <thư mục ổ USB/NAS đã gắn>` → lịch Chủ nhật ~05:30 xuất gói mã hoá, tự kiểm đọc lại, giữ 4 bản; `genh offsite run` xuất ngay;
  `genh offsite status` xem tình trạng; `genh offsite disable` tắt. Ổ chưa gắn ⇒ không ghi gì (lỗi `GH-EB01`), Console có chuông `offsite.stale` / `offsite.failed`. Cài đặt có **Bộ khôi phục** (in/QR) và
  **Tải gói mang đi** (nhập PIN).
- **Khoá chỉ nằm trên máy này** (`GH_MASTER_KEY`, `GH_BACKUP_KEY`): mất máy mà không có `genh export`/`offsite` ở nơi khác = mất mọi bản sao lưu. Đừng cất mật khẩu gói cùng chỗ với gói.

## 5. Chuyển sang máy khác (export / import)

- Máy cũ: `genh export --to <tệp.ghbundle>` — hỏi mật khẩu gói 2 lần (ẩn; ≥ 12 ký tự; hoặc biến `GH_BUNDLE_PASSWORD` cho script). Gói chứa CSDL + tệp + bí mật.
- Máy mới: cài Gen-Harness bình thường, rồi `genh import <tệp.ghbundle> [--yes]` — **GHI ĐÈ dữ liệu hiện tại** (hỏi xác nhận trừ khi `--yes`), tự sao lưu an toàn trước, mã hoá lại mọi bí mật bằng khoá của máy mới.
  Mã thoát của bước Python: `0` ok · `2` sai mật khẩu hoặc gói hỏng · `3` gói không tương thích · `1` lỗi khác.
- Phiên Facebook cũng đi theo gói (mã hoá lại); phiên không mở được → thẻ báo "Cần đăng nhập lại".

## 6. Gói chẩn đoán (gửi cho Claude)

- Console → **Trợ giúp → Tạo gói chẩn đoán** (nhập PIN) → đợi → **Tải gói chẩn đoán** (tệp zip). Gói đã lọc mật khẩu/khoá/token; xoá tay `run/diagnostics/*.zip` sau khi dùng nếu máy nhiều người dùng.
- Không mở được Console: `genh doctor --out report.zip` (chẩn đoán runtime, cổng, chứng chỉ, dung lượng, đồng hồ, kết nối kênh).
- Khi báo lỗi nhớ kèm **mã lỗi** (ERR-… hoặc GH-E…) và **Mã yêu cầu** (`X-Request-ID`) hiện cạnh lỗi — tra được đúng dòng log máy chủ.

## 7. Telegram báo động và trực canh máy chủ

- **Nối bot một lần** (Console → **Kết nối › Telegram**, có hướng dẫn từng bước): Telegram → @BotFather → `/newbot` → chép mã bot → bấm Bắt đầu với bot, gửi "chào" → dán mã vào Console, **Tìm chat_id**, chọn tên Sếp, **Lưu** (PIN) →
  **Gửi thử** (điện thoại nhận 2 tin: một từ Console, một từ trực canh). Dòng 6 ở Hướng dẫn › Việc Sếp cần làm chuyển **Đạt**. Không gửi mã bot cho ai.
- **Trực canh** chạy mỗi 12 phút (`genh watchdog status|enable|disable`; mặc định bật sau cài/cập nhật): đo dịch vụ/api/đĩa/sao lưu/nhịp worker-bridge, tự khởi động lại dịch vụ chết (tối đa 1 lần/giờ), báo Telegram **chống spam**
  (một tin CẢNH BÁO + một tin ĐÃ ỔN mỗi đợt). Thử tay: `genh doctor --notify --test`. Chạy được cả khi api chết.
- Linux: nếu Console nhắc "máy chủ có thể không tự chạy lại khi bật máy", chạy một lần: `sudo loginctl enable-linger $USER` (máy hỏi mật khẩu đăng nhập máy; từ v0.1.53 genh hỏi lại linger sau khi bật và cảnh báo nổi bật nếu vẫn tắt). Máy tắt hẳn/mất điện thì chỉ báo được khi máy bật lại ("Máy chủ vừa khởi động lại").
- `genh stop` cho trực canh **tạm nghỉ** (không tự khởi động lại, không báo động) tới khi `genh start`.
- Bản tin Gen 07:30/17:30 và nhắc việc cũng đi qua Telegram (một chiều, Telegram chỉ nhận số đếm của mục Gen-hub — không tiêu đề mail/lịch).

## 8. Ngắt mạch Gen-hub

Gen-hub (Kho dữ liệu, lịch, mail, việc, Drive) có **ngắt mạch riêng** để Gen-Harness không bị treo khi Gen-hub chập chờn.

- **Cơ chế**: 3 lỗi liên tiếp (mạng/timeout, 5xx, 429/408, phản hồi không phải JSON) trong 5 phút ⇒ mở **60 giây**: Gen và Bản tin trả "Gen-hub tạm không trả lời" mà không gọi mạng; hết 60 giây cho thử lại 1 lần
  (thành công ⇒ đóng, lỗi ⇒ mở tiếp). Lỗi 401/403 *không* tính (đó là token hết hạn/bị thu hồi — trạng thái `expired`). Gen vẫn trả bản đệm ≤ 5 phút nếu có.
- **Báo động**: ngắt mạch mở quá **15 phút** ⇒ một sự cố + một chuông "Gen-hub không trả lời hơn 15 phút" (nút **Mở thẻ Gen-hub**); tự đóng khi gọi lại được.
- **Xử lý**: (1) kiểm Gen-hub có chạy/mở được không; (2) Console → **Kết nối › Gen-hub → Kiểm tra** (nhập PIN) — nút này không bị ngắt mạch chặn, **xanh là đóng ngắt mạch**; (3) 401: token hết hạn (90 ngày; có chuông nhắc trước 14 ngày) →
  tạo token mới ở Gen-hub, dán vào thẻ (PIN), thu hồi token cũ.
- **Tắt khẩn**: tắt liên kết ở thẻ Gen-hub (PIN) — Gen thôi đọc Kho/lịch/mail. **Chỉ chặn việc ghi Kho**: bỏ tick `kho_create`, `kho_update` ở Gen-hub rồi bấm Kiểm tra (thẻ **Ghi vào Kho dữ liệu** khoá nút Xác nhận); đường ghi chỉ chạy khi Sếp Xác nhận + mã PIN nên không tự ghi.
- Ghi sai vào Kho: báo lỗi `HUB_WRITE_UNCERTAIN` nghĩa là không chắc đã ghi — **mở Kho kiểm trước khi thử lại**: đã có thì bấm Huỷ trên thẻ, chưa có thì bấm Xác nhận lại; sửa bản ghi bằng một đề xuất `kho_update` mới hoặc trực tiếp trong Kho.
- `HUB_WRITE_HIDDEN`: Sếp đã tự đóng `kho_create`/`kho_update` (hoặc gỡ cấp Gen) ở MCP Hub — mở lại ở MCP Hub; tick ở Gen-hub + Kiểm tra không mở lại.

## 9. Khi cập nhật kẹt

Bắt đầu bằng `genh status`, `genh logs -f`, rồi tìm mã trong Console (thẻ cập nhật, "Chi tiết kỹ thuật") hoặc trong `<gốc cài>/run/update-status.json` (gốc cài mặc định `~/.gen-harness`).

| Thấy gì | Nghĩa là | Làm gì |
|---|---|---|
| Thẻ "Cập nhật bị dừng giữa chừng" (vàng) + nút **Thử lại**; mã `GH-E94B` | máy tắt hoặc tiến trình bị tín hiệu dừng | bấm **Thử lại** hoặc `genh update`. Nếu máy tắt sau khi đã đổi CSDL, chạy lại để hoàn tất |
| "Đang cập nhật" quá 60 phút, nhịp sống `run/genh-heartbeat.json` cũ | `genh` đã chết (trạng thái *stalled*) | `genh status`; chạy lại `genh update`. Không xoá `genh.lock` bằng tay |
| `GH-E94A` "đang có lần cập nhật/khôi phục khác chạy" | khoá loại trừ `<gốc cài>/genh.lock` đang bị giữ | đợi vài phút; lịch đêm tự bỏ qua và thử lại đêm sau |
| `GH-E945` "bản mới lỗi, đã quay về bản cũ" | rollback thành công; lịch đêm không thử lại bản đó | không cần làm gì; báo Claude kèm gói chẩn đoán, chờ bản sửa |
| "tự quay về bản cũ CŨNG THẤT BẠI — cần xử lý tay" (`rollback_failed`) | dịch vụ có thể đang dừng | `genh restore <khoá>` bằng `backup_key` trong `run/update-blocked.json` nếu CSDL đã bị đụng; nếu chưa (`db_touched=false`) chỉ cần `docker compose up -d --remove-orphans`. Rồi báo Claude |
| `GH-E948` "ổ đĩa sắp đầy — chưa đụng gì" | thiếu chỗ trống (< 5 GB) | dọn đĩa (log Docker, tệp lớn) rồi cập nhật lại. `genh update` tự dọn ảnh cũ của Gen-Harness, giữ 2 bản |
| `GH-E940`/`GH-E941`/`GH-E900`/`GH-E901` "chưa đụng gì" | tải hoặc sao lưu lỗi trước khi đổi gì | thử lại sau; kiểm mạng và chỗ trống |
| Thẻ "Máy chủ chưa nhận yêu cầu cập nhật" kèm "chỉ chạy khi có người đăng nhập — cần bật linger" (`stalled_reason: linger_off`) | linger tắt nên trình nhận yêu cầu (nút Cập nhật ngay) và lịch đêm không chạy khi không ai đăng nhập | chạy một lần trên máy chủ `sudo loginctl enable-linger $USER` rồi bấm **Thử lại** |
| Thẻ "Trình nhận yêu cầu trên máy chủ đang lỗi" (`stalled_reason: watcher_failed`) | `gen-harness-update-request.path`/`.service` ở trạng thái lỗi (vd start-limit) | chạy `genh update` (tự `reset-failed` và bật lại) rồi bấm **Thử lại** |
| `GH-E94C` "Máy chủ không xoá được tệp yêu cầu — chưa đụng gì" | genh không xoá được tệp trong `run/request` (quyền thư mục) nên không làm yêu cầu, để tránh chạy lặp | kiểm quyền thư mục `<gốc cài>/run/request` (genh phải xoá được tệp trong đó) rồi bấm **Thử lại** |
| Chuông "Lịch tự cập nhật đêm chưa chạy N ngày" / "đang tắt" (`host.nightly`) | lịch đêm tắt hoặc im quá 36 giờ và Sếp không chủ động tắt | bấm **Xem cách bật lại**: `genh auto-update status`, nếu Linger KHÔNG thì `sudo loginctl enable-linger $USER`, rồi `genh auto-update enable` |
| Dịch vụ dừng hết sau khi máy khởi động lại | Docker chưa tự lên | `genh start`; `genh status` để xem cảnh báo khởi động cùng máy (`host.autostart`) |

Vẫn kẹt → gói chẩn đoán (mục 6) + nhắn Claude. **Đừng** chạy `docker image prune -a` (xoá cả ảnh bản liền trước, mất khả năng quay về).

## 10. Việc lặt vặt khác

- **Quên mật khẩu Owner**: `genh reset-password` (in email + mật khẩu tạm mới, giữ nguyên dữ liệu, đăng xuất các phiên cũ, gỡ chặn đăng nhập sai nhiều lần).
- **Mất mã thiết lập**: `genh reset-setup`. **Hết cảnh báo "Not secure"**: `genh trust-ca`.
- **Đổi cách truy cập (điện thoại, nhân viên)**: `genh remote` (xem), `genh remote tailscale` (khuyên dùng), `genh remote cloudflare --hostname <tên>`, `genh remote lan` (cần cài CA trên từng điện thoại), `genh remote local` (chỉ máy chủ).
- **Dừng khẩn tài khoản mạng xã hội**: Console → **Tài khoản mạng xã hội → Dừng tất cả** (chặn cả việc gửi); bật lại cần PIN. Phiên Facebook hết hạn → chuông + Telegram → **Đăng nhập lại**.
- **Gỡ cài đặt**: `genh uninstall` (mặc định *giữ* dữ liệu); `--delete-data` mới xoá dữ liệu (gõ "XOÁ DỮ LIỆU" hoặc kèm `--yes`).
- **Phát hành (người bảo trì)**: tăng `VERSION` → CI → Release bản thử → E2E cài thật → bản chính thức. Báo "đã phát hành" chỉ sau khi `releases/latest` đúng tag mới và `genh` tải về khớp checksum/phiên bản
  ([installer](handoff/05-installer.md) › "Cổng phát hành").
