"""v0.1.31 — model mặc định của mỗi nguồn (agent.models.is_default) cho "Dùng model này" (nguồn CLI nhiều model).

Revision ID: 0022
Revises: 0021
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlfile import run_file  # noqa: E402

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_file("0022_v0131_cli_models.sql")


def downgrade() -> None:
    pass  # schema chỉ thêm; hạ cấp = khôi phục bản sao lưu
