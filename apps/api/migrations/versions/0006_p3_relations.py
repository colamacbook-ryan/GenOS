"""Giai đoạn 3: Quan hệ & Đối tượng.

Revision ID: 0006
Revises: 0005
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlfile import run_file  # noqa: E402

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_file("0006_p3_relations.sql")


def downgrade() -> None:
    pass  # schema giai đoạn 3 chỉ thêm; hạ cấp = khôi phục bản sao lưu
