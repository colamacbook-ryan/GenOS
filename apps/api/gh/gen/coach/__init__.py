"""Gen hướng dẫn (v0.1.54, g1-api) — thẻ "Hôm nay của Sếp": việc vận hành cần làm ngay, mẹo "Sếp biết chưa?",
"Bài học hôm nay · k/19", chuông `gen.coach` và dòng "Việc bắt buộc: đã đạt x/N" trong Bản tin.

Bốn lớp, từ thuần tới có I/O:
- `signals`  — đọc tín hiệu hệ thống (CHỈ ĐỌC, cache 60 giây) + bộ quy tắc việc, đích làm sáng, câu hậu quả tĩnh.
- `lessons`  — nội dung mẹo / bài học (content/*.json) + 9 bài sinh từ registry + biểu thức điều kiện.
- `engine`   — thuần: dựng thẻ từ tín hiệu + tuỳ chọn + trạng thái mục + đồng hồ truyền vào; mốc ổn định; ý định.
- `store`, `routes`, `cron` — CSDL (agent.gen_coach_prefs / gen_coach_items), API `/gen/coach/*`, job `gen_coach`.

Luật cứng: KHÔNG gọi model, KHÔNG ghi gen_messages ở bất kỳ đâu trong gói này; payload chỉ gồm khoá, tiêu đề tĩnh và số
đếm — không bao giờ có chi tiết sự cố, thông điệp, email hay token. Gen khuyên chứ không ép (QD-12).
"""
