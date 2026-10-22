"""v0.1.71 — bot tài khoản riêng, kênh quản trị Zalo/WhatsApp, chế độ hỗ trợ nhóm (G1 «lược đồ»).
Cột core.channel_sessions.account_kind, core.groups.added_by_*; bảng ops.owner_channels, group_support, auto_sends,
owner_outbox, owner_questions (RLS + GRANT); mặc định settings.owner_report / support_limits. Mọi câu lệnh chạy lại an toàn.

Revision ID: 0042
Revises: 0041
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlfile import run_file  # noqa: E402

revision = "0042"
# Sau 0041 (v0.1.69) — alembic chỉ còn một head (test_migrations_heads).
down_revision = "0041"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_file("0042_v0171_kenh_quan_tri.sql")


def downgrade() -> None:
    pass  # chỉ thêm cột NULL / bảng trống / khoá cài đặt mặc định; hạ cấp = khôi phục bản sao lưu
