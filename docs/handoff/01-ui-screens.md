# 01 · Màn hình

Mỗi khối ghi `[tậpDữLiệu]` — khoá trong `design/seed-data.json` chứa dữ liệu mẫu hiển thị ở khối đó. Token màu/chữ ở `docs/02`. Khi mô tả và thiết kế khác nhau, **thiết kế thắng** — mở `design/Gen-Harness Console.dc.html` và bấm tới màn đó.

Quy ước chung cho mọi màn:

- **Hàng tiêu đề màn**: khối chữ (tiêu đề 20px/500 + mô tả 12.5px neutral-400, max-width 700–760px) bên trái; cụm điều khiển bên phải, căn đáy. Hàng `flex-wrap`, khối chữ `flex: 1 1 320px` — khi hẹp, cụm điều khiển rơi xuống dòng dưới. Khi khối chữ nằm một mình trong cột, không dùng `flex-basis` (tránh bị kéo cao).
- **Thẻ**: header 12px 16px (tiêu đề 13px/600 + nhãn phụ 10px UPPERCASE 0.1em neutral-500), viền dưới divider, thân tuỳ nội dung.
- **Lưới**: `repeat(auto-fit, minmax(min(100%, Xpx), 1fr))`, gap 12–16px. Bảng rộng cuộn ngang trong thẻ.
- **Mọi điểm số** có đường tới "vì sao" / "chứng cứ".

---

## Khung ứng dụng

### Sidebar (244px · thanh icon 60px)
- Logo: ô 30px viền accent + glow, `ph-radar`; "GEN‑HARNESS" 12.5px/600 0.13em; "Genesis Harness OS · v2.2" 9.5px UPPERCASE.
- Đường kẻ gradient mờ hai đầu.
- **Danh mục hai miền** (`design/screens.json`):
  - Nhãn miền: icon fill + tên 9.5px/700 0.18em UPPERCASE, màu miền (Kinh doanh = OK xanh, Kỹ thuật = accent-400), đường kẻ mờ, số màn mono 9px.
  - Mục cấp 1 cao 32px: thanh chỉ 2px bên trái khi chọn, icon 16px, tên 12px, badge đếm, caret nếu có con.
  - Mục có con **vừa là màn** (Bản đồ quan hệ) → bấm mở màn và bung con. Mục chỉ là nhóm (Hàng đợi & Hành động…) → bấm chỉ bung/thu.
  - Con thụt 16px + đường dọc 1px, cao 29px, chấm 5px (accent khi chọn), 11.5px.
  - Nhóm chứa màn đang mở tự bung.
  - Chế độ thanh icon: ẩn chữ, ẩn nhãn miền, thay bằng đường kẻ giữa hai miền; bấm nhóm → mở con đầu tiên; tooltip = tên + tên tiếng Anh.
- Chân: avatar chữ cái 29px + tên + vai trò + caret mở menu tài khoản.

### Header (58px, sticky)
- Trái: chip miền (KINH DOANH / KỸ THUẬT) · `›` · tên nhóm cha (nếu là màn con) · `›` · tiêu đề 14px/600 + phụ đề tiếng Anh 10px UPPERCASE (chỉ ở màn cấp cha, tắt được). Khối trái có sàn 380px, tiêu đề sàn 190px.
- Phải: pill "4 kênh · 42 nhóm" có chấm LIVE; pill `tự trị 4` (tooltip giải thích mức); pill `78%` tin cậy dữ liệu (tooltip); nút icon Góc nhìn đã lưu; nút chính icon Tìm theo ý định. Cụm phải co được, không đẩy khối trái.

---

## KINH DOANH

### overview · Tổng quan điều hành
**Mục đích:** màn hình 10 phút mỗi sáng — hôm nay có gì cần Sếp.
- KPI 6 ô `[kpis]`: nhãn, icon, số lớn màu theo trạng thái, đơn vị, dòng phụ, thanh 2px đáy theo %. Bấm → màn liên quan.
- Hàng hai cột 1.5 : 1
  - **Hàng đợi cần xử lý** `[queue]`: hàng lưới `34px | 1fr | auto` — chip P1/P2/P3, chip loại (Cơ hội/Cảnh báo/Chờ duyệt/Đến hạn), tiêu đề, dòng mono mã · nguồn · tuổi; điểm + nút hành động. Nút header "Mở hộp thư ý nghĩa".
  - **5 đối tượng đáng chú ý** `[spotlight]` → mở Hồ sơ sống. **Chủ đề đang nổi** `[signals]` thanh + delta %.
- Hàng ba cột: **Sức khoẻ hệ thống** `[health]`, **Chất lượng dữ liệu** `[dataQuality]` (nút Sửa), **Nhiệt kế hoạt động** `[hourly]` 24 cột, cột > 80 màu WARN, trục 00/06/12/18/hiện tại.
- Bổ sung khi dựng (spec F4 — thiếu trong thiết kế): KPI "tín hiệu → tiếp cận (trung vị phút)", "báo giá đã gửi", "tỉ lệ cơ hội được nhận", "độ trễ xử lý chassis", "hồ sơ active". Đặt vào hàng KPI thứ hai cùng kiểu.

### inbox · Hộp thư ý nghĩa (con của Hàng đợi & Hành động)
**Mục đích:** mỗi dòng là một đơn vị ý nghĩa đã cấu trúc, không phải tin nhắn thô.
- Tiêu đề + ô lọc theo ý định + nút Bộ lọc.
- Tab `[inboxTabs]` Tất cả/Cơ hội/Cảnh báo/Chờ duyệt/Cần soạn/Ứng viên kèm đếm.
- Thẻ `[meaningItems]`, viền trái 2px màu trạng thái: chip loại · mã · kênh + nguồn · tuổi; tiêu đề 14px/500; tóm tắt 2 câu; bên phải điểm lớn mono `/100` + nhãn điểm + chip tin cậy (cao/trung bình/thấp). Ô trích dẫn chứng cứ gốc + "Xem chứng cứ gốc". Hàng nút: hành động chính (primary) · Giao cho người khác · Im lặng có chủ đích · "Vì sao hệ thống nghĩ vậy" (ghost, phải).

### workbench · Bàn làm việc (con của Hàng đợi & Hành động)
**Mục đích:** duyệt mọi bản nháp agent soạn khi vượt mức tự trị.
- Lưới 3 cột `300px | 1fr | 300px`.
- Trái: **Chờ Sếp duyệt** `[drafts]` — chip loại, mã, tiêu đề, agent, tuổi; dòng đang chọn viền trái accent.
- Giữa: tiêu đề bản nháp + mã/agent/tuổi; nút Dịch, Soạn lại. Thân thư `[draftBody]` trong thẻ nền bg. Khối "Dữ liệu agent đã dùng — không có chỗ nào bịa" `[draftSources]` chip nguồn. Chân: **Duyệt và gửi qua Zalo** (primary) · Sửa rồi gửi · Huỷ · lý do giữ lại (vượt ngưỡng).
- Phải: **Ngữ cảnh đối tượng** `[wbContext]`, **Tạo kèm theo** `[wbSideActions]` công tắc.
- Duyệt/huỷ yêu cầu PIN nếu phiên PIN hết hạn; mọi quyết định ghi action log.

### directory · Nhóm & Con người
**Mục đích:** danh sách nhóm theo kênh và danh sách người lọc theo quan hệ, nhiệt, giá trị, ưu tiên → gán BOT.
- Segmented `[dirTabs]` Nhóm theo kênh / Con người.
- **Nhóm theo kênh** `[channelGroups]`: mỗi kênh một thẻ (icon, tên, chip trạng thái phiên, tóm tắt); bảng nhóm: mã GRP mono, tên, chip loại, thành viên, tin 24h, nhiệt (thanh + số), chip chế độ nghe, BOT đang gán. Kênh chưa cài → trạng thái trống + nút cài plugin.
- **Con người** `[peopleFilters]` 5 hàng bộ lọc dạng nút chọn nhiều (Liên quan Sếp, Độ nhiệt, Giá trị, Ưu tiên, BOT); bảng `[peopleRows]`: avatar + tên + tổ chức, mã PER, kênh (icon), chip quan hệ, nhiệt, giá trị, chip ưu tiên, BOT (icon fill nếu đã gán), mức tự trị, nút **Thiết lập BOT** → hộp chọn agent + mức tự trị cho riêng người đó. Bấm dòng → Hồ sơ sống.

### graph · Bản đồ quan hệ
**Mục đích:** ai đang là cầu nối, khách nào lạnh, ai ôm quá nhiều việc.
- Segmented `[graphModes]` Danh sách · Người ↔ Người · Nhóm ↔ Nhóm · Luồng chủ đề; nút "7 bộ lọc"; hàng chip lọc `[graphFilters]`.
- **Danh sách** `[nodes]`: bảng đối tượng/loại/giai đoạn/kênh/phụ trách (đỏ nếu chưa có)/độ nóng (thanh)/tiềm năng/rủi ro/chạm gần nhất (đỏ nếu > 30 ngày). Bấm → Hồ sơ sống.
- **Người ↔ Người / Nhóm ↔ Nhóm**: khung vẽ cao 470px, nền bg; node `[nodePoints]` (vị trí %, bán kính theo trọng số, viền màu theo loại: owner accent, nhân sự accent-400, đối tác/nóng OK, rủi ro BAD, lạnh neutral-700) + nhãn tên/meta dưới; cạnh `[nodeEdges]` SVG (dày theo loại: mạnh 2.5 OK, vừa 1.5 accent-800, rủi ro 2.5 BAD, lạnh 1 mờ .45), tooltip nội dung cạnh. Panel phụ: **Người là cầu nối / Nhóm có trọng số cao nhất** `[nodeStats]`, **Hệ thống nhận ra** `[nodeInsights]`. Khung vẽ sàn 520px; hẹp hơn → panel phụ rơi xuống dưới.
- **Luồng chủ đề** `[graphRows]`: from → cạnh gradient dày theo trọng số + chủ đề + số → to. Panel **Người đang là cầu nối** `[bridges]`, **Tải quan hệ theo người** `[ballLoad]`.
- Ở bản sản phẩm: đồ thị tương tác thật (kéo node, zoom, bấm node mở hồ sơ, bấm cạnh xem chứng cứ), bố cục d3-force hoặc elkjs, tối đa 200 node hiển thị theo trọng số.

### profile · Hồ sơ sống (con của Bản đồ quan hệ)
**Mục đích:** một đối tượng, mọi thứ hệ thống hiểu, và vì sao.
- Nút quay lại bản đồ. Đầu hồ sơ: avatar 52px, tên 20px, chip loại, chip rủi ro; hàng danh tính đa kênh + "Xem lịch sử hợp nhất"; nút Gán phụ trách, Mở bàn làm việc.
- 5 thẻ điểm `[profileScores]` + mũi tên xu hướng + ghi chú + "Vì sao".
- Trái: **Hệ thống hiểu gì** `[profileSummary]` chấm màu + câu; **Dòng sự kiện** `[timeline]` thời gian mono · chip sự kiện mono · chi tiết · "Chứng cứ".
- Phải: **Mức tự trị với đối tượng này** `[autonomySteps]` 7 ô 0–6, mức đang đặt tô accent; **Tài liệu đã trao đổi** `[profileDocs]`; **Người nội bộ từng chạm** `[touchpoints]`; **Ghi chú tay của Sếp** (hệ thống không sửa).

### notebook · Sổ tay nhận thức (con của Bản đồ quan hệ)
**Mục đích:** trí nhớ tạm lũy tiến của AI-trợ lý cho từng ID người / ID nhóm — dạng compact context window để nhớ nhanh ID dữ liệu liên quan.
- Segmented `[nbTabs]` Theo người / Theo nhóm.
- Trái: danh sách chủ thể `[nbSubjects]` (tên, mã, số mục, tuổi cập nhật).
- Giữa: đầu sổ `[nbCurrent]`; ngân sách token `[nbBudget]` (thanh dùng/tối đa, lần nén thứ n, nén tiếp khi…); các mục `[nbSections]` — Cần chú ý ngay · Ngữ cảnh lũy tiến · Giới hạn cho agent · Sở thích · Việc dở; mỗi dòng có tham chiếu ID (chip mono bấm được), tác giả (agent/Sếp), ghim.
- Phải: **ID liên quan** `[nbRefs]`, **Owner có thể** `[nbOwnerActions]` (ghim, sửa, xoá mục, nén ngay, đặt lại), **Lịch sử nén** `[nbHistory]`, **Đã nén khỏi ngữ cảnh** `[nbDropped]` (vẫn truy được trong kho sạch).

### opportunity · Bảng cơ hội (con của Cơ hội & Thị trường)
- Tiêu đề + tổng pipeline mono + Bộ lọc.
- Kanban 7 cột `[oppColumns]`: Tín hiệu thô · Đã xác thực · Đã ráp khớp · Đang tiếp cận · Đang đàm phán · Đã chuyển nội bộ · Thắng/Trượt/Ngủ đông. Đầu cột: chấm, tên, đếm, tổng giá trị. Thẻ: chip độ nóng · mã, nhu cầu, người, giá trị + chip tin cậy, dòng ráp khớp (icon link/link-break), hộp rủi ro nếu có. Kéo thả giữa cột → ghi `opportunity_stage_history`.

### supply · Cung ↔ Cầu (con của Cơ hội & Thị trường)
- Hai thẻ song song `[marketSides]`: **Đang cần nguồn hàng** (CẦU) và **Đang cần bán** (CUNG); mỗi dòng: chip nhiệt, mặt hàng, giá trị, người · tuổi, dòng khớp, nút hành động (Ghép/Báo giá/Tìm/Làm ấm/Giữ/Bỏ qua).
- **Cặp ghép đề xuất** `[matches]`: cầu ↔ cung, chip điểm, lý do, giá trị, nút Giới thiệu hai bên (tạo bản nháp chờ duyệt).

### search · Kho hội thoại (con của Cơ hội & Thị trường)
- Ô tìm ngôn ngữ tự nhiên viền accent + số kết quả + thời gian; chip facet `[searchFacets]`; câu hỏi hay dùng `[savedQueries]`.
- Bảng kết quả là **người**, không phải tin nhắn `[searchResults]`: đối tượng, ý định gần nhất (mono), khoảng giá, số lần hỏi, chip thái độ, chip tiềm năng. Lưu góc nhìn.
- **Mẫu hệ thống nhận ra** `[patterns]`; **Hành động hàng loạt** `[bulkActions]` (vẫn qua duyệt).

### people · Đánh giá con người (con của Con người & Chất lượng)
- Chip "Dữ liệu khoá ở cấp Owner". Tab `[peopleTabs]` Nhân viên/Khách hàng/Ứng viên/Học viên.
- Thẻ hàng `[reviewRows]` lưới `212px | 96px | 1fr | 260px | auto`: người, điểm lớn + xu hướng, tín hiệu nổi bật tuần, khuyến nghị (icon), Xem chứng cứ · Sửa điểm tay.
- Khung chú thích nét đứt về trách nhiệm.
- Bổ sung khi dựng (spec mục I — thiếu trong thiết kế): nút **Phản biện** cho người được đánh giá (vai trò có quyền), luồng xử lý phản biện ghi `biz.review_disputes`.

### care · Chất lượng chăm sóc (con của Con người & Chất lượng)
- KPI 5 ô `[careKpis]`.
- **Tốc độ phản hồi theo khung giờ**: lưới heatmap `120px | 8 cột` — hàng người `[careGrid]`, cột giờ `[careHours]`, ô có số phút, màu theo ngưỡng (<15 OK, 15–60 WARN, >60 BAD, "—" không hoạt động), chú giải.
- **Lỗi chăm sóc lặp lại** `[carePatterns]`; **Kịch bản thắng và mất khách** `[scripts]`.

---

## KỸ THUẬT · BACKEND

Ba màn Tầng dữ liệu (raw, rules, clean) có **dải pipeline** 4 bước ở đầu `[pipeline]`: Bridge lắng nghe → Kho thô → Core agent sàng lọc → Kho sạch SSOT; bước của màn hiện tại viền accent; mũi tên giữa các bước.

### raw · Kho dữ liệu thô
- Chip lọc `[rawFilters]` (kênh, nhóm, trạng thái, thời gian, nhãn, tin cậy).
- Bảng `[rawRows]`: giờ mono, kênh (icon), GRP mono, PER mono, nội dung nguyên văn (2 dòng), nhãn đề xuất mono, tin cậy (màu), chip trạng thái (Đã vào kho sạch/Đang phân loại/Tin cậy thấp/Loại — nhiễu/Chờ chu kỳ tới). Cuộn vô hạn, LIVE qua WebSocket.
- Phải: **Kích hoạt sàng lọc** `[triggerConfig]` (chu kỳ, ngưỡng số lượng, lô, ngưỡng tin cậy — thanh + ghi chú), nút Chạy ngay; **Kho thô theo nhóm** `[rawByGroup]`; **Lượt chạy gần nhất** `[refineryRuns]`.

### rules · Quy tắc sàng lọc
- Danh sách thẻ `[refineryRules]`: mã, tên, chip loại, lượt khớp 24h, ngưỡng, công tắc; hai cột **Khi** (điều kiện) / **Thì** (đầu ra). Sửa → tạo phiên bản mới.
- **Trọng số chấm điểm** `[weights]` tổng 100%.
- **Thử quy tắc trên một tin nhắn**: ô nhập + kết quả `[testOutput]` (intent, person_id, group_id, entities, scores, confidence, actions).

### clean · Kho sạch SSOT
- Chip lọc `[cleanFilters]`. Bảng `[cleanRows]`: GRP, PER, chip sự kiện mono, kết luận, điểm, chu kỳ; dòng chọn có vạch accent.
- Phải: **Trí nhớ tạm của ID đang chọn** `[memory]` (4 khối: cần chú ý, ngữ cảnh lũy tiến, giới hạn, đã nén) + **Tham số agent trực kênh dùng** `[agentParams]`.

### identity · Hợp nhất danh tính
- KPI 4 ô `[idStats]`.
- Thẻ cặp `[idPairs]`: danh tính A (icon kênh, tên, meta mono) · giữa chip khớp % + cơ sở · danh tính B · Gộp (primary) / Không phải / Chứng cứ. Gộp/tách yêu cầu PIN, ghi `identity_merge_log`.

### agents · Danh tính Agent (con của Agent & Model)
- Lưới thẻ `[agents]`: icon, tên, vai trò, công tắc; trường Xưng hô/Giọng/Kênh/Được nói/Cấm; chip tự trị, số phát ngôn, Sửa.
- **Agent đã nói gì, nhân danh gì** `[agentLog]`; **Mẫu có sẵn** `[agentTemplates]` ("Bé Heo" chỉ là mẫu hoài niệm, tắt mặc định).

### api · API & Model (con của Agent & Model)
- Thẻ provider `[apiProviders]`: icon, tên, vai trò, chip trạng thái; trường endpoint/khoá (ẩn, nút hiện yêu cầu PIN)/model/giới hạn; Kiểm tra kết nối.
- **Gán model cho từng agent** `[agentBindings]`: agent, model (chọn), bộ quy tắc, nhiệt độ, ngữ cảnh, tự trị.
- **Tham số core agent** `[coreParams]`, **Giới hạn tốc độ** `[rateLimits]`.
- Hạn mức theo model (bảng quota, chuỗi chuyển hướng, quy tắc) nằm ở Điều khiển hệ thống › tab Bộ não AI — giữ nguyên vị trí như thiết kế.

### mcp · MCP Hub (con của Agent & Model)
- KPI 4 ô `[mcpStats]`.
- Danh sách máy chủ `[mcpServers]` **thu gọn mặc định**: hàng 1 dòng — caret, icon, tên, chip trạng thái, transport mono, "n tool · m agent", gọi 24h, p95, công tắc. Bấm hàng → bung: endpoint mono, **Công cụ đã mở** (chip mono, icon mắt = đọc, bút = ghi màu WARN), **Agent được phép gọi**, ghi chú, Xem nhật ký gọi · Cấu hình quyền.
- **Nhật ký gọi MCP** LIVE `[mcpLog]`: lúc, agent, công cụ, tham số & kết quả (2 dòng), thời gian, chip kết quả (OK/Chờ duyệt/Lỗi/Bị chặn).
- **Ranh giới gọi ra ngoài** `[mcpGuards]` (mục khoá mờ); **Chợ máy chủ MCP** `[mcpMarket]`.

### plugins · Plugin & Tiện ích
- KPI `[plugKpis]`. Tab `[plugTabs]` **Plugin nền** (chassis, không gỡ được) / **Plugin cài thêm**.
- Bảng `[plugRows]`: icon, tên + package mono, lớp, nguồn, phiên bản, chip trạng thái, chỉ số, breaker 24h, công tắc (plugin nền: công tắc khoá hoặc ẩn nút gỡ).
- **Thứ tự nạp** `[loadOrder]`, **Sự kiện plugin** LIVE `[plugEvents]`, **Chợ tiện ích** `[plugMarket]`; nút Nạp plugin từ tệp (PIN + hiện quyền xin).

### system · Điều khiển hệ thống
Tab `[sysTabs]`:
- **Kênh & đăng nhập**: thẻ kênh `[channels]` (trạng thái, meta, nút Đăng xuất/Quét lại QR/Cài plugin/Cấu hình; khối QR 88px + đếm ngược khi cần; 4 ô chỉ số). **Mã PIN** 6 ô `[pinDigits]` + quy tắc `[pinRules]` + Đổi PIN / Lịch sử nhập. **Tài khoản Antigravity CLI** (email, gói, hiệu lực, Đổi tài khoản) + khoá khác `[creds]`.
- **Bộ não AI**: **Hạn mức theo model** `[models]` (model mono + vai trò, provider, thanh dùng + số, còn lại, p95, chip trạng thái) + giờ đặt lại; **Chuỗi chuyển hướng** `[providers]` kéo thả; **Quy tắc chuyển hướng** `[failoverRules]`.
- **Quyền hạn**: ma trận `[permCols]` × `[permRows]` (✓ toàn quyền / – giới hạn / ✕ không); **Nhóm đang lắng nghe** `[listenGroups]`; **Ranh giới có trách nhiệm** `[boundaries]` (mục khoá mờ).
- **Nhật ký**: bảng `[auditLog]` thời điểm, ai/agent (agent màu accent-300), hành động mono, đối tượng, tự trị, chip kết quả; tìm + Xuất CSV.
- Bổ sung khi dựng (spec mục I): tab **Dữ liệu & lưu trữ** — chính sách lưu theo tập dữ liệu (`ops.retention_policies`), yêu cầu xuất/xoá/giới hạn của một người (`ops.data_requests`), xoá dữ liệu mẫu.

---

## Màn còn thiếu so với spec (dựng thêm theo cùng ngôn ngữ)

Spec mục G1 có các thực thể chưa có màn riêng trong thiết kế. Dựng thêm, đặt vào danh mục như sau, dùng đúng khung bảng + panel phải của các màn đã có:

| Màn | Vị trí danh mục | Nội dung tối thiểu |
|---|---|---|
| Việc & Nhắc hẹn | Kinh doanh › Hàng đợi & Hành động | Bảng `biz.tasks`: mã, tiêu đề, ưu tiên, trạng thái, phụ trách, hạn (đỏ khi quá), nguồn (lời hứa/bản nháp/tay); lịch tuần; lời hứa sắp đến hạn |
| Tài liệu | Kinh doanh › Quan hệ & Đối tượng (cạnh Nhóm & Con người) | Kho `biz.documents` lọc theo nhóm sở hữu / người sở hữu, ACL, nguồn (kênh/agent/tay), xem trước |
| Deal & Vụ việc | Kinh doanh › Cơ hội & Thị trường | `biz.deals` đã chốt + `biz.cases` (cảnh báo, khiếu nại) với trạng thái và người xử lý |

Hỏi Owner xác nhận vị trí trước khi dựng.
