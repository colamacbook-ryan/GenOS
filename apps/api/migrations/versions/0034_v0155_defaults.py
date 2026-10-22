"""v0.1.55 (g1-mac-dinh) — hồ sơ mặc định tiêu chuẩn: agent.bindings.effort (mức suy nghĩ riêng của dòng gán model).

Revision ID: 0034
Revises: 0033
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlfile import run_file  # noqa: E402

revision = "0034"
down_revision = "0033"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_file("0034_v0155_defaults.sql")


def downgrade() -> None:
    pass  # schema chỉ thêm; hạ cấp = khôi phục bản sao lưu
