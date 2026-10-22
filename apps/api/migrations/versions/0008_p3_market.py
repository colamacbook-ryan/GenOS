"""Giai đoạn 3: Cơ hội & Thị trường.

Revision ID: 0008
Revises: 0007
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlfile import run_file  # noqa: E402

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_file("0008_p3_market.sql")


def downgrade() -> None:
    pass  # schema giai đoạn 3 chỉ thêm; hạ cấp = khôi phục bản sao lưu
