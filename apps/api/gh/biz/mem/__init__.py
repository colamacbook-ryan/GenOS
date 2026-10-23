# ruff: noqa: E501
"""Lõi trí nhớ 5 lớp (spec Bước 1 r6.1 §4A) — cụm `mem`.

- `entities` / `aliases` / `mentions`: ai là ai (L2). `entities.canonical` luôn một bậc nhờ nén đường đi khi gộp.
- `facts`: sổ sự thật HAI THỜI GIAN (L3), `assert_fact` là MỘT nơi duy nhất ghi `mem.facts`. `disputes`: hàng chờ tranh chấp.
- `predicates`, `contexts` (chính sách theo bối cảnh nhóm, một nguồn), `profile_read` (Hồ sơ Sếp, chỉ `owner_confirmed`).
- `retrieve`: gói ngữ cảnh cho lượt phân tích; `forget`: quên người (xoá thật, chỉ qua Console + PIN).
- `routes` (API), `jobs` (tự gom `job_*.py`), `view_routes` (khung rỗng, L4 sở hữu).
ERD: docs/design/loi-tri-nho-erd.md.
"""
