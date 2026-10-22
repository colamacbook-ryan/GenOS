"""v0.1.40 — hạn lưu thật (F-2) + job nặng (F-16): chỉ mục trigram tên, ops.job_watermarks, chỉ mục dọn theo lô.

Revision ID: 0026
Revises: 0025
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlfile import run_file  # noqa: E402

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_file("0026_v0140_retention_jobs.sql")


def downgrade() -> None:
    pass  # schema chỉ thêm; hạ cấp = khôi phục bản sao lưu
