"""Giai đoạn 3: agent trực kênh.

Revision ID: 0010
Revises: 0009
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlfile import run_file  # noqa: E402

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_file("0010_p3_duty.sql")


def downgrade() -> None:
    pass  # schema giai đoạn 3 chỉ thêm; hạ cấp = khôi phục bản sao lưu
