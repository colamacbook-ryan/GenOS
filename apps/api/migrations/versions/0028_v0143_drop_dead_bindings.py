"""v0.1.43 — dọn agent.bindings của khoá lõi đã bỏ (core.intent/core.scoring/core.indexing) (F-25).

Revision ID: 0028
Revises: 0027
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlfile import run_file  # noqa: E402

revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_file("0028_v0143_drop_dead_bindings.sql")


def downgrade() -> None:
    pass  # chỉ xoá hàng mồ côi; hạ cấp = khôi phục bản sao lưu
