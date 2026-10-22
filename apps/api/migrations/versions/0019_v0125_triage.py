"""v0.1.25 — Đợt C1: refinery.item_marks (lọc đầu: trùng, rác, điểm chất lượng) + RLS.

Revision ID: 0019
Revises: 0018
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlfile import run_file  # noqa: E402

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_file("0019_v0125_triage.sql")


def downgrade() -> None:
    pass  # schema chỉ thêm; hạ cấp = khôi phục bản sao lưu
