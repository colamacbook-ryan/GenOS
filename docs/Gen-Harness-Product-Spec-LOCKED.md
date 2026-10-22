# GEN-HARNESS
## Product Specification LOCKED v2.2 — Genesis Harness OS

**Phiên bản:** 2.2 LOCKED  
**Ngày:** 21/09/2026  
**Tên sản phẩm chính thức:** Gen-Harness (Genesis Harness OS)  
**Tên cũ (deprecated):** Heo-Harness / Bé Heo Chassis  
**Định hướng sản phẩm:** Sếp (Owner)  
**Trạng thái:** ĐÃ CHỐT — dùng làm tài liệu định hướng chính thức.  
**Tài liệu này thay thế:** Spec v1.0, v2.0 và v2.1  

**Quyết định tên (21/09/2026):** Đổi tên dự án từ Heo-Harness thành **Gen-Harness**, tên đầy đủ **Genesis Harness OS**.  

**Quyết định danh tính Agent (21/09/2026):** Agent Bot **không còn mặc định là Bé Heo**. Danh xưng, vai trò, giọng nói và persona do user tùy chỉnh theo nhu cầu sử dụng. “Bé Heo” nếu còn thì chỉ là một mẫu persona tùy chọn, không phải bản sắc hệ thống.

---

# A. TẦM NHÌN THẬT SỰ

## A1. Câu định vị

Gen-Harness không phải chatbot.  
Không phải CRM.  
Không phải tool nghe lén.  
Không phải “AI thông minh nhất”.

**Gen-Harness là bộ não quan sát + bảng điều khiển trung tâm** giúp một người hoặc một tổ chức **làm chủ toàn bộ dòng chảy hội thoại số** — nơi công việc thật đang diễn ra.

Nơi đó gồm: Zalo, WhatsApp, Telegram, Facebook, LinkedIn và các kênh chat khác.  
Đó là nơi khách hỏi giá, nhân viên chăm sóc, đối tác đàm phán, ứng viên trả lời, học viên trao đổi, cơ hội xuất hiện rồi biến mất trong vài phút.

Vấn đề không phải thiếu AI.  
Vấn đề là **toàn bộ trí tuệ thực chiến đang bị chôn trong chat**, không ai nhìn thấy toàn cảnh, không ai thống kê được, không ai đánh giá được, không ai ráp nối được.

Gen-Harness tồn tại để biến hỗn loạn hội thoại thành:

- tài sản dữ liệu
- bản đồ quan hệ
- tín hiệu cơ hội
- thước đo con người
- cảnh báo sớm
- hành động có kiểm soát

## A2. Vì sao kỹ thuật không phải điểm mạnh chính

Hàm lượng kỹ thuật của dự án **không cần quá cao**.  
Các thành phần kỹ thuật (plugin, channel connector, LLM, MCP, circuit breaker) chỉ là khung sườn đủ dùng.

**Điểm đột phá nằm ở tư duy vận dụng + Console.**

Người dùng thắng không phải vì hệ thống “code đẹp”, mà vì họ có thể:

1. Nhìn thấy những gì người khác chỉ cảm nhận mơ hồ.
2. Quản lý hàng trăm / hàng nghìn luồng chat như quản lý một nhà máy.
3. Biết ai đang mạnh, ai đang yếu, khách nào đáng theo, cơ hội nào đang trôi.
4. Ra lệnh cho hệ thống làm việc hộ mà vẫn nắm được logic phía sau.

Nếu Console yếu, toàn bộ hệ thống chỉ là một đống bot.  
Nếu Console mạnh, hệ thống trở thành **trung tâm điều hành trí tuệ giao tiếp** của tổ chức.

## A3. Ẩn dụ đúng

Gen-Harness giống:

- **Phòng điều khiển** hơn là nhân viên chat
- **Hệ thống radar** hơn là loa phát thanh
- **Sổ cái quan hệ** hơn là hộp thư
- **Bộ lọc cơ hội** hơn là máy tìm kiếm
- **Khung đánh giá động** hơn là form KPI giấy

Nó không thay thế người giỏi quan hệ.  
Nó làm cho một người giỏi quan hệ **nhìn được nhiều hơn, nhớ được nhiều hơn, ra quyết định nhanh hơn, sai sót ít hơn**.

## A4. Bắc cực (North Star)

> Một chủ doanh nghiệp hoặc một cá nhân đứng trước Console trong 10 phút là hiểu được:  
> hôm nay có gì đang xảy ra, ai cần chú ý, cơ hội nào đáng bắt, rủi ro nào đang nhen nhóm, nhân sự nào đang lệch nhịp, và hệ thống nên làm gì tiếp theo.

Nếu 10 phút đó không tạo ra sự rõ ràng, sản phẩm thất bại — dù backend có bao nhiêu plugin.

---

# B. VẤN ĐỀ GỐC

## B1. Thế giới thật đang chạy trên chat

Doanh nghiệp Việt Nam và nhiều tổ chức thương mại quốc tế đang vận hành trên nhóm chat, tin nhắn 1-1, group ngành, group nội bộ, group đối tác. CRM thường được cập nhật sau, hoặc không cập nhật. ERP thì đứng riêng. Não bộ thật sự của công ty nằm trong Zalo/WhatsApp.

Hậu quả:

- Cơ hội xuất hiện trong group rồi trôi mất.
- Sale giỏi thì nhớ khách trong đầu; sale yếu thì bỏ sót.
- Sếp không biết nhân viên đang chăm sóc khách thế nào, trừ khi ngồi soi từng đoạn chat.
- Báo giá, hợp đồng, lịch giao hàng nằm rải rác.
- Không có “bộ nhớ tổ chức”.
- Không có cách đánh giá khách, nhân viên, ứng viên, học viên từ hành vi thật.
- Không có cách ráp nối cung–cầu trên quy mô lớn.

## B2. Các công cụ hiện có đều thiếu một lớp

| Loại công cụ | Làm được | Thiếu |
|---|---|---|
| Chatbot | Trả lời | Không thấy toàn cảnh, không quản trị dữ liệu |
| CRM | Lưu hồ sơ | Không sống cùng hội thoại thật |
| ERP | Kho / đơn / tiền | Không hiểu ngữ cảnh con người |
| Social listening | Theo dõi public | Không quản được hội thoại kinh doanh riêng |
| BI dashboard | Thống kê số liệu cứng | Không hiểu mối quan hệ và diễn biến chat |
| Agent coding tool | Viết code / chạy lệnh | Không phải hệ điều hành quan hệ |

**Lớp còn thiếu:** lớp **quan sát – tổ chức – chấm điểm – ráp nối – điều khiển** đặt lên trên dòng hội thoại đa kênh.

Gen-Harness chính là lớp đó.

---

# C. LUẬT CHƠI CỦA SẢN PHẨM

## C1. Chu trình vận hành (Operating Loop)

Mọi năng lực đều đi theo một vòng lặp:

**LISTEN → STRUCTURE → SCORE → MATCH → ACT → LEARN**

1. **LISTEN** — lắng nghe đa kênh, có lọc, có ranh giới.
2. **STRUCTURE** — biến tin nhắn thành đối tượng: người, mối quan hệ, sự kiện, cơ hội, cảnh báo, tác vụ.
3. **SCORE** — chấm điểm tiềm năng, rủi ro, hiệu suất, mức độ phù hợp, độ nóng của cơ hội.
4. **MATCH** — ráp cung với cầu, người với việc, khách với nhân viên, ứng viên với vị trí.
5. **ACT** — soạn thảo, nhắc việc, báo giá, ghi CRM/ERP, đề xuất hành động, hoặc im lặng có chủ đích.
6. **LEARN** — kết quả hành động quay lại làm giàu hồ sơ và chỉnh logic đánh giá.

Console phải cho người dùng nhìn và can thiệp được cả 6 bước này.

## C2. Ba lớp đối tượng trung tâm

Mọi thứ trên Console xoay quanh 3 lớp:

1. **Người**  
   Khách hàng, nhân viên, đối tác, ứng viên, học viên, người lạ có tín hiệu, cộng đồng.

2. **Quan hệ**  
   Ai nói với ai, trên kênh nào, về chủ đề gì, với mật độ nào, ở giai đoạn nào, ai đang nắm ball.

3. **Sự kiện**  
   Một tin nhắn, một hỏi giá, một than phiền, một lịch hẹn, một đơn hàng, một cảnh báo, một cơ hội.

Nếu Console chỉ hiện tin nhắn, sản phẩm vẫn là chat.  
Nếu Console hiện được Người – Quan hệ – Sự kiện, sản phẩm trở thành hệ điều hành.

## C3. Nguyên tắc bất biến

1. Mọi thứ là plugin, nhưng **dữ liệu phải là một nguồn sự thật duy nhất** (SSOT).
2. Một plugin lỗi không được kéo sập hệ thống.
3. Bot không được nói nhiều hơn mức cần thiết. Im lặng đúng lúc cũng là năng lực.
4. Mọi điểm số đều phải **giải thích được** trên Console (vì sao khách này 82 điểm, vì sao nhân viên này bị cảnh báo).
5. Người dùng phải luôn có quyền xem nguồn gốc dữ liệu (drill-down về đoạn hội thoại gốc).
6. Quyền hạn phải phân tầng. Không phải ai cũng được nhìn mọi thứ.
7. Hệ thống phục vụ ra quyết định, không phải thay thế trách nhiệm con người ở các quyết định nhạy cảm.

---

# D. ĐỐI TƯỢNG DÙNG & VIỆC HỌ THUÊ SẢN PHẨM LÀM

## D1. Doanh nghiệp

### Chủ / Giám đốc
Thuê Gen-Harness để:
- Nhìn toàn công ty qua một màn hình
- Biết hôm nay có bao nhiêu cơ hội, bao nhiêu rủi ro
- Biết ai đang làm tốt, ai đang làm ẩu
- Không phải soi từng đoạn chat

### Trưởng sale / Key Account
Thuê để:
- Phân bổ khách
- Theo dõi pipeline đang sống trên chat
- Không để khách “chết vì không ai follow”
- Chuẩn hóa cách chăm sóc

### Trợ lý thương mại / Admin hậu cần
Thuê để:
- Làm báo giá, soạn hồ sơ, nhắc lịch, theo dõi chứng từ và tiến độ
- Giảm việc copy-paste giữa chat và Excel

### Nhân sự / Đào tạo
Thuê để:
- Quan sát ứng viên và học viên qua tương tác thật
- Phát hiện người phù hợp / không phù hợp sớm hơn form đánh giá

### CSKH
Thuê để:
- Bắt tín hiệu bất mãn
- Đo chất lượng chăm sóc
- Giữ khách trước khi mất

## D2. Cá nhân

Freelancer, chủ hộ, sales tự do thuê Gen-Harness để:
- Không để mối quan hệ chết trong 20 group
- Nhớ ai đã hứa gì
- Lọc việc / khách / đối tác từ lượng chat lớn
- Có một thư ký số + bộ nhớ thứ hai + radar cơ hội

## D3. Việc “thay nhân sự” phải hiểu đúng

Gen-Harness **không thay** một sales closer giỏi hay một key account senior ở việc chốt deal tế nhị.

Nó **có thể gánh** phần lớn việc của:
- sale junior đi canh group / hỏi thăm / gửi báo giá / follow máy móc
- trợ lý thương mại soạn thảo – nhắc việc – cập nhật
- admin hậu cần thông tin
- thư ký theo dõi lịch và hồ sơ

Giá trị thật: **một người mạnh + Gen-Harness = sức phủ của 2 đến 3 người vận hành**.

---

# E. BẢN ĐỒ NĂNG LỰC ĐẦY ĐỦ

Đây là phần v1 còn hẹp. Dưới đây là bản đồ đầy đủ theo đúng hướng bạn đang nghĩ.

## E1. Radar đa kênh
- Zalo, WhatsApp, Telegram, Facebook, LinkedIn
- Có thể mở thêm: Discord, Email, Webhook, nội bộ
- Mỗi kênh là plugin
- Quy tắc lắng nghe khác nhau theo kênh / group / người
- Có chế độ: chỉ tag mới trả lời / lắng nghe im lặng / chủ động bắt tín hiệu

## E2. Nhà máy dữ liệu hội thoại (Conversation Data Factory)
Không chỉ lưu tin nhắn. Phải tách được:
- thực thể (người, công ty, hàng hóa, địa điểm, mức giá)
- ý định (hỏi giá, than phiền, tìm đối tác, xin việc, chốt lịch…)
- sự kiện (đề nghị, từ chối, hẹn, giao hàng, thanh toán)
- cảm xúc / thái độ (ở mức tín hiệu, không phải kết luận tuyệt đối)
- chủ đề và ngữ cảnh dài

Kết quả: kho hội thoại có thể lọc, tìm, thống kê, chấm điểm — không phải đống log.

## E3. Opportunity Engine — khai thác và ráp nối cơ hội
Lắng nghe số lượng lớn kênh để:
- phát hiện người đang cần hàng / cần đối tác / cần dịch vụ / cần việc / cần đầu ra
- xếp hạng độ nóng và độ tin cậy của tín hiệu
- ráp cung với cầu
- tạo hàng đợi cơ hội cho người dùng xử lý
- tránh spam, tránh tiếp cận bừa

Đây là năng lực “big data thương mại” đúng nghĩa: không phải thu thập cho nhiều, mà **lọc ra thứ đáng tiền**.

## E4. Relationship Graph — bản đồ quan hệ
Không phải danh bạ. Là đồ thị sống:
- node: người / tổ chức / group
- edge: tần suất, chiều tương tác, chủ đề, giai đoạn quan hệ, ai đang nắm
- trọng số thay đổi theo thời gian
- nhìn được “ai là cầu nối”, “khách nào đang lạnh”, “nhân viên nào đang ôm quá nhiều ball”

## E5. Living Profiles — hồ sơ động
Mỗi người có một hồ sơ sống, tự cập nhật từ hành vi:
- Khách hàng: nhu cầu, ngân sách ước lượng, lịch sử deal, phong cách giao tiếp, rủi ro churn, tiềm năng
- Nhân viên: tốc độ phản hồi, chất lượng chăm sóc, kiểu nói chuyện, điểm mạnh/yếu, khách đang phụ trách
- Ứng viên: tín hiệu phù hợp vị trí, thái độ, tính nhất quán
- Học viên: mức tham gia, tiến bộ, điểm kẹt
- Đối tác: điều khoản hay đàm phán, độ uy tín qua hành vi

Hồ sơ phải luôn có nút **“vì sao hệ thống nghĩ vậy”**.

## E6. Evaluation Fabric — khung đánh giá
Một lớp chấm điểm dùng chung, gắn được cho nhiều đối tượng:
- tiềm năng
- mức độ gắn kết
- rủi ro
- hiệu suất
- mức phù hợp
- độ nóng
- độ tin cậy dữ liệu (data confidence)

Quan trọng: điểm số là **công cụ hỗ trợ quản lý**, không phải bản án. Console phải cho sửa tay, gắn nhãn, giải thích, và xem lại lịch sử thay đổi điểm.

## E7. People Observation — quan sát con người trong tổ chức
Dùng cho:
- đánh giá nhân viên sale / CSKH qua cách họ thật sự nói với khách
- phát hiện người đang quá tải, đang thờ ơ, đang tạo rủi ro
- hỗ trợ coaching, không chỉ để “bắt lỗi”
- tuyển dụng: sàng tín hiệu từ tương tác thật thay vì chỉ CV
- đào tạo: theo dõi học viên qua trao đổi, bài tập, thái độ

## E8. Care Pattern Intelligence — phân tích cách chăm sóc
Hệ thống không chỉ biết “đã nhắn”, mà biết **cách nhắn**:
- follow quá sớm / quá muộn
- hứa nhưng không giữ
- giọng điệu gây khó chịu
- quên khách sau báo giá
- chăm sóc tốt rồi lại biến mất
- kịch bản nào đang chuyển thành deal

Từ đó đưa ra cảnh báo và gợi ý chuẩn hóa cách sale chăm sóc.

## E9. Early Warning System — cảnh báo sớm
Các loại cảnh báo:
- khách đang lạnh / sắp mất
- nhân viên phản hồi chậm bất thường
- than phiền lặp lại
- cơ hội nóng nhưng chưa ai nhận
- đối thủ xuất hiện trong hội thoại
- deadline giao hàng / thanh toán / đàm phán bị bỏ quên
- dữ liệu mâu thuẫn giữa chat và CRM/ERP

Cảnh báo phải có mức ưu tiên, người nhận, và hành động đề xuất.

## E10. Commercial Copilot — trợ lý mậu dịch
Đúng các ví dụ bạn nêu, nhưng là một lớp chứ không phải toàn bộ sản phẩm:
- tham gia group đàm phán
- phiên dịch
- soạn hợp đồng / biên bản / báo giá
- nhắc lịch sự kiện
- hỗ trợ hậu cần thông tin hai bên
- đóng vai key account junior + admin

Lớp này **ăn dữ liệu từ hồ sơ + ERP/CRM**, không bịa.

## E11. System of Action — hành động có kiểm soát
Hành động gồm:
- nhắn
- im lặng
- soạn sẵn để người duyệt
- tạo task
- tạo nhắc
- ghi CRM
- tạo đơn nháp ERP
- gán người phụ trách
- mở hồ sơ
- tạo báo cáo

Mọi hành động quan trọng nên có mức tự trị:
- Chỉ quan sát
- Gợi ý
- Soạn sẵn chờ duyệt
- Tự làm trong phạm vi hẹp
- Tự làm rồi báo cáo

Console phải chỉnh được mức tự trị theo plugin, theo kênh, theo loại việc.

## E12. MCP / Enterprise Connect
Nối ERP, CRM, HRM, lịch, kho tài liệu.
Mục tiêu không phải “có MCP cho vui”, mà để hội thoại và hệ thống vận hành **không còn sống hai thế giới riêng**.

Chat nói “còn hàng không?” thì hệ thống phải hỏi được tồn kho.  
Chat nói “khách này cũ” thì hồ sơ CRM phải hiện.  
Báo giá xong thì pipeline phải được ghi.

## E13. Agent Identity — danh tính bot do user định nghĩa
Agent Bot là lớp mặt tiền có thể thay, không phải linh hồn cố định của hệ thống.

Nguyên tắc:
- **Không có mặc định bắt buộc là Bé Heo.**
- User tự đặt: tên gọi, vai trò, xưng hô, giọng điệu, phạm vi việc được làm, ngôn ngữ, mức chủ động.
- Một tổ chức có thể có nhiều agent identity khác nhau trên cùng chassis: trợ lý thương mại, admin hậu cần, CSKH, recruiter, thư ký cá nhân…
- Mỗi identity gắn với persona, rule lắng nghe, mức tự trị, và quyền hành động riêng.
- Console phải cho tạo / sửa / nhân bản / tắt identity mà không đụng vào lõi dữ liệu.

Bộ trường tối thiểu của một Agent Identity:
- Tên hiển thị
- Vai trò (role)
- Persona / giọng
- Phạm vi kênh được xuất hiện
- Khi nào được nói / khi nào chỉ quan sát
- Mức tự trị
- Cấm làm gì
- Chữ ký / cách xưng hô với khách và nội bộ

Mẫu có sẵn (optional templates), không phải bản sắc hệ thống:
- Trợ lý thương mại
- Key Account junior
- Admin hậu cần
- CSKH
- Recruiter
- Thư ký cá nhân
- Bé Heo (chỉ là template hoài niệm, tắt mặc định)

## E14. Personal Command Center
Cùng một khung, thu nhỏ cho cá nhân:
- radar việc / khách / mối quan hệ
- bộ nhớ thứ hai
- thư ký
- lọc cơ hội cho bản thân
- quản lý đời sống công việc rải trên nhiều group
- tự đặt tên và tính cách agent theo cách mình làm việc

---

# F. CONSOLE — ĐÂY MỚI LÀ SẢN PHẨM

## F1. Tư duy thiết kế Console

Console không phải trang quản trị plugin.  
Đó chỉ là một góc kỹ thuật.

Console là **nơi người dùng nghĩ**.

Cấu trúc tư duy của màn hình phải theo câu hỏi quản trị, không theo cấu trúc code:

1. Đang xảy ra gì?
2. Cái gì quan trọng nhất lúc này?
3. Ai đang liên quan?
4. Vì sao hệ thống nghĩ vậy?
5. Tôi nên làm gì / giao hệ thống làm gì?
6. Kết quả sau đó ra sao?

Mọi trang đều phải cho phép:
- lọc
- tìm
- sắp xếp
- drill-down về chứng cứ gốc
- hành động ngay
- lưu góc nhìn (saved views)

## F2. Kiến trúc thông tin Console

### 1) Command Overview — Màn hình 10 phút
Mục đích: người đứng đầu nhìn một lần là nắm ngày hôm nay.

Khối thông tin:
- Sức khỏe hệ thống (plugin, kênh, nghẽn, lỗi đã cách ly)
- Nhiệt kế hoạt động: số hội thoại, số người chủ động, số group đang nóng
- Hàng đợi cần xử lý: cơ hội / cảnh báo / việc đến hạn / tin chờ duyệt
- 5 đối tượng đáng chú ý nhất hôm nay (khách, nhân viên, deal, ứng viên…)
- 5 tín hiệu thị trường hoặc chủ đề đang nổi
- Mức tin cậy dữ liệu (hôm nay hệ thống chắc chắn đến đâu)

Nguyên tắc: **không trang trí bằng chart vô nghĩa**. Mỗi ô bấm vào phải ra việc.

### 2) Inbox of Meaning — Hộp thư ý nghĩa
Không phải inbox tin nhắn thô.  
Là hàng đợi các **đơn vị ý nghĩa**:
- cơ hội mới
- hỏi giá
- than phiền
- hẹn
- tài liệu cần soạn
- tin chờ duyệt
- cảnh báo
- ứng viên cần xem

Mỗi item gồm: nguồn, đối tượng, mức ưu tiên, điểm số, tóm tắt 2 câu, hành động đề xuất, chứng cứ.

Đây là nơi làm việc hàng ngày của trợ lý / trưởng nhóm.

### 3) Relationship Map — Bản đồ quan hệ
Hai chế độ:
- danh sách + bộ lọc mạnh
- đồ thị quan hệ

Lọc theo:
- loại người
- kênh
- độ nóng
- tiềm năng
- rủi ro
- người phụ trách
- thời gian tương tác gần nhất
- giai đoạn quan hệ

Bấm một node ra Living Profile.

### 4) Living Profile — Hồ sơ sống
Một trang hồ sơ phải có:
- danh tính đa kênh (cùng một người trên Zalo + WhatsApp + LinkedIn)
- tóm tắt 8–12 dòng do hệ thống viết, luôn cập nhật
- điểm số + lý do
- timeline sự kiện
- tài liệu đã trao đổi (báo giá, HĐ, file)
- task / hẹn đang mở
- người nội bộ từng chạm
- ghi chú tay của chủ
- mức tự trị được phép với đối tượng này

### 5) Opportunity Board — Bảng cơ hội
Giống pipeline nhưng sống từ chat, không phải form CRM chết.
Cột / giai đoạn ví dụ:
- Tín hiệu thô
- Đã xác thực
- Đã ráp khớp
- Đang tiếp cận
- Đang đàm phán
- Đã chuyển nội bộ
- Thắng / Trượt / Ngủ đông

Mỗi card cơ hội phải trả lời:
- ai cần gì
- độ nóng
- độ tin
- nên ghép với ai / hàng gì / nhân viên nào
- rủi ro nếu không làm gì

### 6) People Review — Đánh giá con người
Các board riêng:
- nhân viên
- khách hàng
- ứng viên
- học viên

Không phải bảng điểm khô. Mỗi dòng có:
- điểm
- xu hướng (đang lên / xuống)
- tín hiệu nổi bật tuần này
- khuyến nghị coaching hoặc hành động
- nút xem chứng cứ

### 7) Care Quality — Chất lượng chăm sóc
Nhìn theo nhân viên, theo team, theo khách:
- tốc độ phản hồi theo khung giờ
- tỷ lệ follow sau báo giá
- tỷ lệ hứa rồi quên
- khách bị bỏ rơi
- kịch bản thắng
- kịch bản mất khách

### 8) Knowledge & Search — Kho hội thoại có não
Search không chỉ theo chữ.
Phải search được theo:
- ý định
- người
- hàng / ngành
- khoảng giá
- thời gian
- cảm xúc
- “những khách từng hỏi X nhưng chưa chốt”
- “nhân viên nào thường xử lý tốt dạng việc này”

Đây là lớp Big Data thực dụng: tìm ra mẫu, không chỉ tìm ra câu.

### 9) Workbench — Bàn soạn thảo & hành động
Nơi:
- soạn báo giá / HĐ / tin nhắn
- dịch
- tạo task
- gửi hoặc chờ duyệt
- kéo dữ liệu ERP/CRM vào ngữ cảnh

### 10) System Control — Điều khiển máy
Đây mới là chỗ plugin, **Agent Identity**, persona, rule, MCP, quyền hạn, log, circuit breaker.
Người kỹ thuật và admin vào đây.  
Chủ doanh nghiệp không bị bắt đầu từ đây.

Màn hình Agent Identity trong System Control phải cho:
- tạo agent mới
- đổi tên / vai trò / giọng
- gán kênh và group
- chỉnh mức tự trị
- bật/tắt từng agent
- xem agent nào đã nói gì, nhân danh gì

## F3. Logic điều hướng

Người dùng luôn đi theo hình phễu:

**Tổng quan → Hàng đợi ý nghĩa → Đối tượng / Cơ hội → Chứng cứ gốc → Hành động → Kết quả quay lại tổng quan**

Không được thiết kế theo kiểu: vào Dashboard plugin rồi lạc trong setting.

## F4. Chỉ số bắt buộc trên Console

### Vận hành hệ thống
- số kênh sống
- số group đang lắng nghe
- số sự kiện/ngày
- số plugin healthy / degraded / isolated
- độ trễ xử lý
- tỷ lệ hành động chờ duyệt

### Kinh doanh
- số tín hiệu cơ hội mới
- số cơ hội đã xác thực
- tỷ lệ cơ hội được nhận xử lý
- thời gian từ tín hiệu → tiếp cận
- số báo giá gửi
- số deal đang sống trên chat
- giá trị pipeline ước lượng (nếu có dữ liệu)

### Quan hệ
- số hồ sơ active
- số quan hệ đang lạnh
- số khách không có người phụ trách
- độ phủ follow-up

### Con người
- tốc độ phản hồi trung vị theo nhân viên
- tỷ lệ khách bị bỏ rơi theo nhân viên
- xu hướng điểm chăm sóc
- số cảnh báo nhân sự đang mở

### Chất lượng dữ liệu
- % hồ sơ còn thiếu danh tính
- % điểm số có độ tin thấp
- % sự kiện chưa gắn đối tượng

Nếu thiếu nhóm chỉ số cuối, hệ thống sẽ tự tin sai — rất nguy hiểm.

---

# G. MÔ HÌNH DỮ LIỆU MÀ CONSOLE PHẢI QUẢN ĐƯỢC

## G1. Thực thể gốc
- Identity (một người thật)
- Channel Account (tài khoản trên từng kênh)
- Organization
- Group / Space
- Relationship
- Conversation Thread
- Message / Event
- Intent
- Opportunity
- Deal / Case
- Document
- Task / Reminder
- Evaluation Snapshot
- Alert
- Action Log
- Plugin / Connector State

## G2. Nguyên tắc hợp nhất danh tính
Cùng một người xuất hiện trên nhiều kênh phải gộp được.  
Đây là việc sống còn. Nếu không gộp được, Big Data sẽ vỡ thành mảnh.

Console cần có màn hình **Identity Resolution**:
- gợi ý hai tài khoản có thể là một người
- cho phép gộp / tách tay
- giữ lịch sử

## G3. Sự kiện là đơn vị nguyên tử
Đừng xây hệ thống quanh “đoạn chat”.  
Xây quanh **sự kiện có nghĩa**.

Ví dụ sự kiện:
- AskedPrice
- RequestedPartnership
- Complained
- PromisedDelivery
- ScheduledMeeting
- SentQuotation
- MentionsCompetitor
- WentSilent

Từ sự kiện mới suy ra điểm số, cảnh báo, task.

## G4. SSOT
Plugin có thể đến từ nhiều nguồn, nhưng hồ sơ, điểm số, cơ hội, task phải về một kho logic.
Nếu mỗi plugin tự giữ sự thật riêng, Console sẽ nói nhiều thứ trái nhau.

---

# H. MỨC TỰ TRỊ & PHÂN QUYỀN

## H1. Thang tự trị
0. Chỉ ghi nhận  
1. Tóm tắt  
2. Chấm điểm + giải thích  
3. Gợi ý hành động  
4. Soạn sẵn chờ duyệt  
5. Tự thực thi việc thấp rủi ro  
6. Tự thực thi việc đã được whitelist

Mặc định an toàn: 3 hoặc 4.  
Không bao giờ để hệ thống tự đàm phán điều khoản lớn nếu chưa được mở quyền.

## H2. Phân quyền Console
- Owner: thấy toàn cảnh
- Manager: thấy team
- Operator: thấy hàng đợi việc
- Agent nhân viên: chỉ thấy khách mình được phân
- Auditor: xem log, không hành động

Dữ liệu đánh giá nhân sự và ứng viên phải bị khoá chặt hơn dữ liệu cơ hội.

---

# I. RANH GIỚI PHÁP LÝ & THIẾT KẾ CÓ TRÁCH NHIỆM

Lắng nghe số lượng lớn kênh chat và đánh giá con người là năng lực mạnh, nhưng dễ sai mục đích.

Spec bắt buộc phải có lớp này:

- Chỉ lắng nghe các kênh / group mà tổ chức có quyền hợp lệ.
- Công khai nội bộ khi dùng để đánh giá nhân sự, nếu pháp luật hoặc văn hóa tổ chức yêu cầu.
- Không biến sản phẩm thành công cụ soi mói đời tư.
- Mọi đánh giá nhân sự / ứng viên / học viên phải có chứng cứ, có chỗ phản biện, có người chịu trách nhiệm.
- Có chế độ ẩn dữ liệu nhạy cảm.
- Có quyền xóa / xuất / giới hạn lưu trữ theo chính sách.
- Cảnh báo chỉ là tín hiệu, không phải kết luận kỷ luật tự động.

Sản phẩm muốn sống lâu thì phải mạnh ở quan sát, nhưng **không được thiết kế như máy kết án**.

---

# J. KIẾN TRÚC KỸ THUẬT ĐỦ DÙNG

Kỹ thuật phục vụ tầm nhìn, không dẫn dắt tầm nhìn.

- Chassis: Event Bus, Plugin Manager, Circuit Breaker, Policy Engine, Store
- Channel plugins
- Provider plugins (Antigravity/Gemini, DeepSeek, model khác)
- Intelligence plugins (intent, score, match, warning)
- Action plugins (office, media, translator, quotation, scheduler)
- MCP connectors
- Console UI
- Identity resolution
- Search / index

Ưu tiên:
1. không sập
2. dữ liệu không vỡ
3. Console đủ để nghĩ
4. plugin thêm được
5. model thay được

Không ưu tiên:
- tự xây foundation model
- kỹ thuật cầu kỳ vì thích cầu kỳ
- marketplace trước khi Console chưa dùng được

---

# K. SẢN PHẨM NÀY KHÔNG PHẢI LÀ GÌ

- Không phải ChatGPT bọc giao diện
- Không phải Zalo bot trả lời vui
- Không phải CRM truyền thống
- Không phải phần mềm nghe lén
- Không phải hệ thống HR chấm công
- Không phải công cụ coding agent
- Không phải dashboard chart cho đẹp

Nếu một tính năng không giúp người dùng **thấy rõ hơn, quản được hơn, quyết nhanh hơn**, thì không phải ưu tiên.

---

# L. LỘ TRÌNH THEO GIÁ TRỊ, KHÔNG THEO PLUGIN

## Phase 0 — Khung sống
Chassis ổn, plugin không làm sập máy, Console mở được, log đọc được.

## Phase 1 — Nhìn thấy được
Zalo + 1 kênh nữa.  
Hội thoại được cấu trúc thành người / sự kiện / hàng đợi ý nghĩa.  
Overview + Inbox of Meaning dùng được hàng ngày.

## Phase 2 — Hiểu được
Hồ sơ động, gộp danh tính, search có não, điểm số có giải thích.  
Cảnh báo khách lạnh và việc bị quên.

## Phase 3 — Quản được
Bản đồ quan hệ, đánh giá nhân viên/khách, quality chăm sóc, opportunity board.

## Phase 4 — Làm được
Báo giá, soạn thảo, nhắc việc, MCP-CRM/ERP, mức tự trị 4–5.

## Phase 5 — Phủ được
Đủ kênh lớn. Ráp nối cơ hội trên quy mô nhiều group. Tuyển dụng / học viên. Marketplace chọn lọc.

Mỗi phase phải có một câu kiểm định:
- Phase 1: “Tôi không còn sợ sót tin quan trọng.”
- Phase 2: “Tôi hiểu khách/nhân viên này đang ở trạng thái nào.”
- Phase 3: “Tôi điều phối được cả bàn cờ quan hệ.”
- Phase 4: “Hệ thống làm hộ việc lặp lại mà tôi vẫn kiểm soát.”
- Phase 5: “Hệ thống bắt đầu tạo ra cơ hội và tiết kiệm nhân sự thật.”

---

# M. TIÊU CHÍ THÀNH CÔNG

Sản phẩm thành công không phải khi đủ 7 plugin mặc định.

Thành công khi:

1. Một người không rành code dùng Console mỗi ngày như dùng Zalo.
2. Chủ doanh nghiệp nhìn Overview 10 phút ra được việc.
3. Không còn cảnh “cơ hội chết trong group vì không ai thấy”.
4. Hồ sơ khách / nhân viên giàu hơn CRM thông thường vì sống từ hội thoại thật.
5. Cảnh báo sớm đúng hơn cảm tính, và giải thích được.
6. Việc lặp lại của trợ lý / sale junior giảm rõ.
7. Hệ thống vẫn sống khi một kênh hoặc một model chết.
8. Người dùng tin dữ liệu đủ để quyết, vì luôn soi được chứng cứ gốc.

---

# N. TẦM NHÌN DÀI

Dài hạn, Gen-Harness không bán một con bot mang sẵn tính cách cố định.  
Nó bán **năng lực làm chủ không gian hội thoại**, với agent mặt tiền do chính user định hình.

Tổ chức nào sống trên chat mà không có lớp này sẽ luôn bị:
- mù thông tin
- phụ thuộc trí nhớ cá nhân
- mất cơ hội
- đánh giá người bằng cảm tính
- nhân sự vận hành phình to nhưng hiệu quả không tăng

Tổ chức nào có lớp này sẽ bắt đầu có:
- trí nhớ tập thể
- radar cơ hội
- thước đo hành vi thật
- khả năng một người điều khiển nhiều luồng quan hệ

Đó mới là đột phá.  
Đột phá ở **cách nhìn và cách điều hành**, không ở framework.

---

# O. QUYẾT ĐỊNH SẢN PHẨM — TRẠNG THÁI HIỆN HÀNH (17/10/2026)

Năm câu hỏi chốt của bản v2.0 đã được trả lời trong quá trình làm; trạng thái thật (nguồn: mã và `docs/ROADMAP.md`):

1. **Đối tượng ra quyết định chính**: Chủ doanh nghiệp (Owner). Mỗi bản cài một tổ chức; Gen và các thao tác ghi ra ngoài (Xác nhận + mã PIN) dành cho Owner trước, vai khác (Quản lý, Vận hành, Nhân viên phụ trách, Kiểm soát) chỉ thấy phần việc được giao theo ma trận quyền.
2. **Use-case đầu tiên**: chưa chốt riêng bằng văn bản; bản đang chạy lấy thương mại nội địa làm mẫu mặc định (mẫu agent "Trợ lý thương mại").
3. **Mức tự trị mặc định**: 4 "Soạn sẵn chờ duyệt" (agent soạn, Sếp duyệt rồi mới gửi ra ngoài); chọn được 3 "Gợi ý hành động" hoặc 4; các ranh giới khoá cứng không tắt được.
4. **Bộ chỉ số tối thiểu**: chưa chốt riêng bằng văn bản; hiện theo các chỉ số của màn Hôm nay (`docs/design/screens.json`).
5. **Ranh giới dữ liệu**: thu thành viên của mọi nhóm đã nối; tin riêng 1-1 lưu im lặng, không tự trả lời; nhóm mới Sếp chọn Im lặng / Không nghe (mặc định đã chọn, xem ROADMAP › Câu hỏi mở).

Câu 2 và câu 4 còn mở — Sếp chốt khi cần.

---

*Gen-Harness Product Spec v2.2 LOCKED — tài liệu định hướng sản phẩm. Ưu tiên cập nhật phần Console, dữ liệu và vòng lặp Listen–Structure–Score–Match–Act–Learn mỗi khi tầm nhìn được làm sắc hơn.*
