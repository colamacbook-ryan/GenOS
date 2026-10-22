"""Giai đoạn 3: Con người & Chất lượng.

Revision ID: 0009
Revises: 0008
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlfile import run_file  # noqa: E402

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_file("0009_p3_people.sql")


def downgrade() -> None:
    pass  # schema giai đoạn 3 chỉ thêm; hạ cấp = khôi phục bản sao lưu
