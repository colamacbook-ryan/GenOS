"""Giai đoạn 5.5 — chỉ mục (org_id, occurred_at) trên raw.events cho benchmark Tổng quan.

Revision ID: 0013
Revises: 0012
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlfile import run_file  # noqa: E402

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_file("0013_p5_perf.sql")


def downgrade() -> None:
    pass  # schema giai đoạn 5 chỉ thêm; hạ cấp = khôi phục bản sao lưu
