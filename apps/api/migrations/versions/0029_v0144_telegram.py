"""v0.1.44 — ops.notify_channels (Telegram mã hoá) + ops.telegram_outbox (bản tin/nhắc việc một chiều) (F-8c).

Revision ID: 0029
Revises: 0028
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlfile import run_file  # noqa: E402

revision = "0029"
down_revision = "0028"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_file("0029_v0144_telegram.sql")


def downgrade() -> None:
    pass  # schema chỉ thêm; hạ cấp = khôi phục bản sao lưu
