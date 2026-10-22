# Gen-Harness — ghi chú cho Claude

Từ 16/10/2026 Sếp tạm ngưng quy trình Gen-hub (Kho dữ liệu) và Brain trong việc thi công: không đọc/ghi Kho, không dùng Brain.
Tiến độ và việc dở ghi ở `docs/ROADMAP.md` và `docs/reports/HANDOFF-v0.1.1.md`.

## Cách làm việc (QD-2, QD-3, QD-4, QD-14)
- Việc nặng ngữ cảnh (đọc log CI, dò nhiều file, review diff lớn, chạy test/e2e, chụp màn hình) → giao sub agent
  (công cụ Agent); ngữ cảnh chính chỉ giữ quyết định, kết luận, báo cáo Boss. Việc 1–2 thao tác thì làm thẳng.
- Phân vai model: Sonnet thi công (dò/sửa code, chạy test) · Opus thiết kế + review trước merge · Haiku đọc log,
  kiểm trạng thái PR/release · Fable chỉ khi Opus đã bí.
- Kỹ năng chính hãng Anthropic (Sếp chốt 16/10, xem `.claude/skills/README.md`): làm giao diện theo `frontend-design`;
  kiểm trên ứng dụng chạy thật (bấm thử, lỗi console, ảnh 390/1440 trước khi báo Sếp) theo `webapp-testing` hoặc `/run`;
  review trước merge bằng `/code-review`, thêm `/security-review` khi chạm mã PIN, khoá, quyền, bí mật; `/simplify` khi diff lớn.
- Dự phóng trước khi thi công (QD-14): việc cỡ một bản thì lập kế hoạch + chia gói việc KHÔNG chồng file (mỗi gói ghi rõ
  file được sửa, hợp đồng API giữa các gói) rồi mới giao thi công song song, mỗi gói một worktree/nhánh `claude/wip/<bản>/<gói>`.
- Trả lời Boss (QD-4): tiếng Việt có dấu, THẬT NGẮN, đi thẳng vào việc Boss cần làm (hoặc "không cần làm gì");
  không giải thích lòng vòng, không kể lý do kỹ thuật trừ khi Boss hỏi; "💡 Học nhanh" tối đa 1–2 dòng.

## Quy trình repo
- Nhánh làm việc → PR vào main; CI xanh thì tự merge squash (Boss đã cho phép). Sau đó NỐI lịch sử main vào nhánh bằng merge,
  KHÔNG reset/force-push. Khuôn (xem `git log --merges -3 origin/claude/v0149`):
  `git merge -s ours origin/main -m "merge: nối main (<sha> = PR #N vX.Y.Z squash, cây trùng hệt điểm tách <sha> ⇒ -s ours, cây mã không đổi)"`.
  Chỉ dùng `-s ours` khi cây main trùng hệt cây nhánh tại điểm gửi PR (`git diff <điểm-tách> origin/main` rỗng); main có thêm
  bản vá nóng thì merge thường và giải xung đột (bài học v0.1.41: `-s ours` làm mất bản vá chỉ có ở nhánh cũ).
- Không mở PR khi chưa có workflow kiểm tương ứng (CI phải kiểm đúng thứ PR đổi). PR chỉ sửa tài liệu vẫn phải merge được:
  không thêm `paths-ignore` cho `ci.yml`.
- Cổng phát hành: tăng `VERSION` ⇒ `release.yml`: CI → Release bản thử (prerelease) → E2E cài thật → bản chính thức (latest)
  → lịch đêm đợi thêm 24 giờ (thời gian chín). Báo Boss "đã phát hành" chỉ sau khi `releases/latest` đúng tag mới VÀ genh tải về
  khớp checksum/version. Promote tay (`skip_e2e`) chỉ khi E2E lỗi ngoài mã; E2E đỏ vì lỗi mã ⇒ sửa rồi tăng `VERSION`.
- Ghi mỗi bản: `CHANGELOG.md` (3–5 dòng, mới nhất trên cùng) + `docs/releases/vX.Y.Z.md` (chi tiết, có mục "Boss phải làm").
  `docs/reports/HANDOFF-v0.1.1.md` CHỈ giữ hiện trạng + việc dở, ≤ 200 dòng. Tiến độ + mục Nợ: `docs/ROADMAP.md`.
  Trước khi commit tài liệu: `python3 .github/scripts/check_doc_links.py` (link chết) và `python3 -m unittest discover -s .github/scripts`.
- Sửa CLAUDE.md cần Boss duyệt (bản 09/10/2026 đã duyệt — QD-18; bổ sung 16/10/2026 theo chỉ đạo Sếp: tạm ngưng Gen-hub/Brain, dùng kỹ năng chính hãng; bổ sung 18/10/2026 Sếp duyệt: ngoại lệ bot tự gửi ba trường hợp ở Luật cứng; bổ sung 19/10/2026 Sếp duyệt: xác nhận trong chat kênh quản trị).

## Luật cứng
- Gen chỉ ĐỀ XUẤT. Không ghi Kho/Gen-hub khi chưa có Xác nhận + mã PIN của Sếp (QD-18: chỉ Kho — Phiên, Việc; kanban, warroom,
  mail, lịch vẫn không ghi). Trong test chỉ dùng MCP giả.
- Tin gửi cho người khác luôn chờ Xác nhận + mã PIN của Sếp trên Console. Ngoại lệ duy nhất (Sếp duyệt 18/10/2026, v0.1.72),
  chỉ khi kênh Zalo/WhatsApp chạy bằng tài khoản riêng của bot: bot tự gửi, không qua PIN, trong đúng ba trường hợp —
  (1) trả lời và báo cáo cho chính Sếp qua kênh quản trị 1-1 đã ghép bằng mã (chỉ báo khi có sự kiện đặc biệt, không báo định kỳ);
  (2) trả lời tại chỗ trong nhóm khi chính Sếp (nhận diện theo ID tài khoản, không theo tên) tag bot;
  (3) trả lời thành viên trong nhóm Sếp đã bật Chế độ hỗ trợ, sau bộ lọc an toàn (không giá/tiền/hợp đồng, không thông tin người khác,
  không nhân sự, có giới hạn tin/giờ, câu khó chuyển Sếp), model chạy trong hộp riêng agy. Cả ba đi qua một cửa `send_auto`,
  ghi `ops.auto_sends` và Nhật ký; thêm đường gửi thứ tư phải được Sếp duyệt và có test chứng minh.
- Xác nhận trong chat (Sếp duyệt 19/10/2026, v0.1.73): trong kênh quản trị 1-1 đã ghép bằng mã (nhận diện Sếp theo ID tài khoản),
  Sếp trả lời «OK» cho một đề xuất cụ thể = Xác nhận thay cho Console + PIN với việc nội bộ (ghi nhớ, việc, nhắc hẹn, ghi Kho).
  Gửi tin cho khách/người ngoài vẫn phải «OK» riêng từng tin, nêu rõ người nhận và nội dung. Mỗi xác nhận ghi Nhật ký; đề xuất có hạn.
- Token/khoá/bí mật không vào log, Action Log, kết quả, thân lỗi hay repo. Không tạo tài khoản giả, không né chống bot.
- Không skip/quarantine test để xanh. Mọi thay đổi hành vi có test; migration chạy lại an toàn.
- Lỗi hiển thị cho người dùng = chuỗi thân thiện + "Chi tiết kỹ thuật"; không bao giờ render object vào JSX.
- Tiếng Việt có dấu trong UI, tài liệu, thông báo lỗi. Thuật ngữ: Sếp, Gen nhớ, Ghi nhớ, Ghi vào Kho dữ liệu, Kho dữ liệu (Kho của Sếp), Gen-hub,
  mã PIN, Việc Sếp cần làm.
