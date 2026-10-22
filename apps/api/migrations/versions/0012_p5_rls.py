"""Giai đoạn 5.5 — Row-Level Security theo org_id (ARCHITECTURE §8.3, lớp phòng thủ thứ hai).

Revision ID: 0012
Revises: 0011
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlfile import run_file  # noqa: E402

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_file("0012_p5_rls.sql")


def downgrade() -> None:
    pass  # schema giai đoạn 5 chỉ thêm; hạ cấp = khôi phục bản sao lưu
