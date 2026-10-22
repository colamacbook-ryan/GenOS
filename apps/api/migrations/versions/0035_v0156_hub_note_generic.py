"""v0.1.56 — ghi chú máy chủ MCP của liên kết Gen-hub dùng tên Kho chung (UPDATE ... WHERE note = chuỗi cũ, chạy lại an toàn).

Revision ID: 0035
Revises: 0034
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlfile import run_file  # noqa: E402

revision = "0035"
down_revision = "0034"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_file("0035_v0156_hub_note_generic.sql")


def downgrade() -> None:
    pass  # chỉ đổi chữ ghi chú; hạ cấp = khôi phục bản sao lưu
