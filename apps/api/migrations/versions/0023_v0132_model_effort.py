"""v0.1.32 — mức suy nghĩ (effort) tách khỏi tên model CLI (agent.models.effort) + chuyển dữ liệu cũ.

Revision ID: 0023
Revises: 0022
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlfile import run_file  # noqa: E402

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_file("0023_v0132_model_effort.sql")


def downgrade() -> None:
    pass  # schema chỉ thêm; hạ cấp = khôi phục bản sao lưu
