"""Giai đoạn 3: Hàng đợi & Hành động.

Revision ID: 0005
Revises: 0004
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlfile import run_file  # noqa: E402

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_file("0005_p3_queue.sql")


def downgrade() -> None:
    pass  # schema giai đoạn 3 chỉ thêm; hạ cấp = khôi phục bản sao lưu
