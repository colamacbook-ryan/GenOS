"""v0.1.67 — Agent làm và nói thật: chỉ thêm cột agent.identities.archived_at (Lưu trữ agent, F085).
IF NOT EXISTS, chạy lại an toàn.

Revision ID: 0039
Revises: 0038 (Telegram 0037 + F1 0038 đã có trong chuỗi bản)
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlfile import run_file  # noqa: E402

revision = "0039"
down_revision = "0038"   # sau Telegram (0037) + F1 (0038)
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_file("0039_v0167_agent_that.sql")


def downgrade() -> None:
    pass  # chỉ thêm cột; hạ cấp = khôi phục bản sao lưu
