"""Giai đoạn 4.3 (MCP Hub) + 4.4 (Plugin & Tiện ích).

Revision ID: 0011
Revises: 0010
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlfile import run_file  # noqa: E402

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_file("0011_p4_mcp.sql")


def downgrade() -> None:
    pass  # schema giai đoạn 4 chỉ thêm; hạ cấp = khôi phục bản sao lưu
