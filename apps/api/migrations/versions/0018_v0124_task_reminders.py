"""v0.1.24 — Đợt A4 (Gen v2): biz.tasks.reminded_at + chỉ mục nhắc việc đến giờ.

Revision ID: 0018
Revises: 0017
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlfile import run_file  # noqa: E402

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_file("0018_v0124_task_reminders.sql")


def downgrade() -> None:
    pass  # schema chỉ thêm; hạ cấp = khôi phục bản sao lưu
