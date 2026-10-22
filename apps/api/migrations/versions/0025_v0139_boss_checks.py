"""v0.1.39 — ops.boss_checks: kết quả kiểm thật từng dòng "Việc Sếp cần làm" (Gen-hub, Facebook, agy, Claude, Jev).

Revision ID: 0025
Revises: 0024
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlfile import run_file  # noqa: E402

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_file("0025_v0139_boss_checks.sql")


def downgrade() -> None:
    pass  # schema chỉ thêm; hạ cấp = khôi phục bản sao lưu
