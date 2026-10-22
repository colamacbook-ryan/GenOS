"""v0.1.64 — Telegram 2 chiều: cột ghép tài khoản Sếp, công tắc nhận tin, offset getUpdates, mã ghép 6 số (băm) trên
ops.notify_channels. ADD COLUMN IF NOT EXISTS, chạy lại an toàn.

Revision ID: 0037
Revises: 0036
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlfile import run_file  # noqa: E402

revision = "0037"
down_revision = "0036"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_file("0037_v0164_telegram_inbound.sql")


def downgrade() -> None:
    pass  # schema chỉ thêm cột; hạ cấp = khôi phục bản sao lưu
