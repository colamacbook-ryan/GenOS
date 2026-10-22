"""v0.1.26 — Đợt D1: agent.hub_links (nối Gen-hub, Gen đọc Kho chỉ-đọc) + RLS.

Revision ID: 0020
Revises: 0019
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlfile import run_file  # noqa: E402

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_file("0020_v0126_hub_link.sql")


def downgrade() -> None:
    pass  # schema chỉ thêm; hạ cấp = khôi phục bản sao lưu
