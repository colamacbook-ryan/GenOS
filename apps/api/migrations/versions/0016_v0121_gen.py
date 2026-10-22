"""v0.1.21 — Gen v1: agent.gen_conversations + agent.gen_messages (khung chat Gen).

Revision ID: 0016
Revises: 0015
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlfile import run_file  # noqa: E402

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_file("0016_v0121_gen.sql")


def downgrade() -> None:
    pass  # schema chỉ thêm; hạ cấp = khôi phục bản sao lưu
