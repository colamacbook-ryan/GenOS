"""v0.1.29 — Đợt D3 lát đầu: core.social_accounts + agent.browser_jobs (Gen đọc mạng xã hội, chỉ đọc) + RLS.

Revision ID: 0021
Revises: 0020
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlfile import run_file  # noqa: E402

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_file("0021_v0129_social.sql")


def downgrade() -> None:
    pass  # schema chỉ thêm; hạ cấp = khôi phục bản sao lưu
