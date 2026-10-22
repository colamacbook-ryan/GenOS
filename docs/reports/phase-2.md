# Báo cáo giai đoạn 2: Dữ liệu

Nhánh `claude/project-thread-bnesk5` · PR nháp #1 · 24/09/2026

## Bạn đã quyết (24/09/2026)

1. **Nghe tin nhắn 1-1:** giữ mặc định **tắt**. Owner bật riêng từng kênh bằng công tắc "Nghe tin nhắn 1-1", có hỏi PIN.
2. **Độ tin cậy dữ liệu trên header:** số tin sàng lọc hôm nay vào thẳng Kho sạch ÷ (sạch + tin cậy thấp), không tính tin nhiễu, theo múi giờ của tổ chức. Chưa sàng lọc tin nào trong ngày thì hiện "—". Header cập nhật sau mỗi lượt sàng lọc.
3. **Icon logo:** đầu heo vui vẻ, nét vẽ cùng phong cách Phosphor, dùng cho ô logo và favicon.

## Đã xong

| PLAN | Nội dung | Kiểm chứng |
|---|---|---|
| 2.1 | Bridge Zalo (zca-js) và WhatsApp (Baileys), viết lại từ logic của dự án cũ. Đăng nhập bằng quét QR tài khoản thật, phiên được mã hoá và tự đăng nhập lại khi khởi động. Bridge có khoá riêng, không giữ khoá master. Chỉ gửi tin khi có giấy phép HMAC dùng một lần, gắn đúng nội dung và đích gửi. Có cảnh báo rủi ro tài khoản cá nhân trước khi hiện QR. | 50 test bridge. Vector mã hoá dùng chung được kiểm ở cả Node và Python. |
| 2.2 | Nhận tin: stream → Kho thô và trạng thái sàng lọc, cùng một transaction. Tin trùng bị bỏ. Nhóm mới luôn ở "Không nghe" (khoá cứng), tin từ nhóm chưa bật bị bỏ. Kho thô chỉ cho phép INSERT. | Test tin trùng, nhóm chưa bật, tin 1-1, chặn sửa và xoá. |
| 2.3 | Bộ định tuyến model: Gemini, API tương thích OpenAI, DeepSeek, Antigravity CLI (bản cài chính hãng 1.2.9, có kiểm SHA256). Gặp 429 thì chuyển khoá; lỗi 3 lần liền thì ngắt mạch 60 giây. Có cảnh báo khi còn dưới 20% hạn mức và khi hết cả chuỗi (1 lần/giờ). Mọi lượt gọi được ghi lại. | Test xoay khoá, hết chuỗi, hạn mức, ngắt mạch, model gán riêng. |
| 2.4 | Sàng lọc: chạy khi tới chu kỳ **hoặc** khi đủ ngưỡng số tin, cái nào tới trước. Tin gắn thẻ agent và tin 1-1 đi đường nhanh. Có nút chạy ngay. Quy tắc có phiên bản, bộ khởi đầu R-01…R-06. Model chỉ thấy mã E1…En; kết luận nào trích mã bịa bị loại. Ngưỡng tin cậy quyết định tin vào `clean` hay `lowconf`. Chấm điểm theo trọng số (tổng 100%). Chạy lại thì bản mới thay bản cũ qua `superseded_by`. | Test chỉ chu kỳ, chỉ ngưỡng, đường nhanh, 4 lượt song song không trùng tin, mã bịa bị loại, model chết thì tin vẫn chờ. |
| 2.5 | Hợp nhất danh tính: đề xuất có % và cơ sở (trùng SĐT, tên gần giống, chung nhóm). Gộp và tách cần PIN. Có lịch sử; đảo ngược phải theo đúng thứ tự. | Test: dò → gộp → tách → đảo ngược trả về đúng trạng thái. |
| 2.6 | Sổ tay nhận thức: sàng lọc ghi thêm dần. Tự nén khi đạt 90% ngân sách hoặc sau 24 giờ. Mục ghim và mục "Giới hạn cho agent" không bao giờ bị nén. Nội dung cũ được lưu trữ, không xoá cứng. | Test nén, ghim, lịch sử nén. |
| 2.7 | Màn Kho dữ liệu thô (nhận trực tiếp qua WebSocket, xuất CSV có PIN), Quy tắc sàng lọc (phiên bản, trọng số 100%, thử trên một tin và 100 tin), Kho sạch (trí nhớ tạm, tham số agent, chứng cứ), Hợp nhất danh tính. | So pixel với thiết kế ở 1440 và 1280: lệch 0,01–1,04% vùng đầu màn, không cuộn ngang. |
| 2.8 | Điều khiển hệ thống › Kênh & đăng nhập: 4 thẻ kênh, QR đếm ngược 60 giây, báo bridge offline, chỉnh chế độ nghe của nhóm, thẻ PIN, tài khoản Antigravity CLI (mở link, dán mã, đổi tài khoản), dòng "Khoá & phiên". | e2e trên mock và trên hệ thống thật. |
| 2.9 | Trình thiết lập bước 4–7 và 12. Bước 12 hiện tiến độ lần sàng lọc đầu tiên theo thời gian thực. | e2e trên hệ thống thật (bên dưới). |

**Chạy thử toàn hệ thống thật** (`apps/web/e2e-live/run.sh`), đạt 4 lần liền. Gồm: API và worker thật trên Postgres + Redis, bridge giả phát QR / "đã quét" / danh bạ nhóm / tin nhắn, model giả tương thích OpenAI. Luồng chạy:

1. Thiết lập bước 4–7 bằng giao diện.
2. Nhắn 4 tin, trong đó 1 tin ở nhóm chưa bật: tin này không được lưu.
3. Sàng lọc cho kết quả 1 tin vào Kho sạch, 1 tin tin cậy thấp, 1 tin nhiễu.
4. Bước 12 hiện đúng số liệu.
5. Tin mới hiện trên Kho thô ngay, không phải tải lại trang.

Hai lỗi chỉ lộ ra khi chạy thật, đã sửa và có test chặn tái phát:

- Nhập PIN đúng nhưng đôi khi vẫn bị hỏi lại. Nguyên nhân: CSDL commit sau khi đã gửi phản hồi. Nay commit xong mới trả lời, áp dụng cho mọi route.
- Sự kiện kết thúc lượt sàng lọc gửi sai dạng dữ liệu, khiến bước 12 hiện toàn số 0.

Số test: API 685, bridge 50, web 98 unit + 29 e2e (mock) + 1 e2e toàn hệ thống. CI trên PR xanh.

## Còn lại / chưa kiểm được trong giai đoạn này

- **Chưa quét QR với tài khoản Zalo/WhatsApp thật.** Môi trường làm việc không có điện thoại hay tài khoản. Phần giao tiếp với thư viện đã có test bằng dữ liệu giả lập; cần thử trên máy thật lúc triển khai.
- **Đăng nhập Antigravity CLI chưa chạy thật.** Không được phép chạy tệp `agy` trong môi trường làm việc. Luồng mở link → dán mã → lưu phiên → đổi tài khoản làm theo dự án cũ và tài liệu chính hãng, cần thử trên máy thật. Tên trường `id_token` trong tệp phiên chưa có tài liệu xác nhận; nếu thiếu, hệ thống lấy email qua Google userinfo.
- **Zalo không có đăng xuất phía máy chủ:** Đăng xuất trong Console chỉ xoá phiên đã lưu. Bạn nên gỡ thiết bị trong ứng dụng Zalo.
- Báo khoá PIN qua Zalo và gửi tin ra ngoài: đường gửi (giấy phép dùng một lần) đã có ở bridge. Phần tạo bản nháp và duyệt thuộc giai đoạn 3.
- **Chưa hoàn tất được trình thiết lập:** bước 12 báo "Còn bước bắt buộc chưa xong: 8, 9" vì Agent đầu tiên và Tự trị làm ở giai đoạn 3. Console vẫn dùng được từ bước 3.
- Embedding chỉ chạy khi có model tên chứa "embedding" (Gemini hoặc API tương thích OpenAI). Không có thì sàng lọc vẫn chạy, chỉ thiếu vector.
- Các tab khác của Điều khiển hệ thống (Bộ não AI, Quyền hạn, Nhật ký) làm ở giai đoạn 4, đúng PLAN.

## Lệch so với spec / thiết kế và lý do

1. **Thẻ WhatsApp hết hạn:** thiết kế hiện sẵn QR. Spec yêu cầu cảnh báo rủi ro tài khoản cá nhân trước, nên QR chỉ hiện sau khi Bạn đọc cảnh báo và nhập PIN.
2. **Chỉnh chế độ nghe nhóm và công tắc tin 1-1** không có trong thiết kế. Chúng nằm trong hộp thoại mở từ dòng "N nhóm lắng nghe", nên thẻ kênh giữ nguyên như thiết kế.
3. **Nút "Sửa" lịch sàng lọc** ở màn Kho thô mở hộp thoại chỉnh chu kỳ/ngưỡng; thiết kế chỉ có dòng hiển thị. Thêm thông báo nhỏ (toast) sau khi lưu.
4. **Kho sạch:** mặc định chọn dòng đầu tiên (thiết kế tô dòng thứ ba chỉ để minh hoạ). Các chip lọc bắt đầu ở trạng thái tắt.
5. **Bảng Kho thô** giữ đúng 8 cột của thiết kế. Ở 1440px cột "Trạng thái" bị che một phần giống hệt thiết kế.
6. **Xuất CSV:** cần quyền quản lý dữ liệu và PIN (thao tác `data.export`), thay cho tên `data.export_delete` trong bản nháp hợp đồng.

## Tự kiểm tra

```
make secrets && make up                      # https://localhost:8443/setup
make api-test                                # Postgres 16 (pgvector, pg_partman) + Redis
npm test && npm run -w apps/web test:e2e     # web trên mock
cd apps/web && bash e2e-live/run.sh          # toàn hệ thống thật, bridge và model giả
```
