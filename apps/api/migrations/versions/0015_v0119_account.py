"""v0.1.19 — core.users.must_change_password (buộc đổi mật khẩu sau genh reset-password).

Revision ID: 0015
Revises: 0014
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlfile import run_file  # noqa: E402

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_file("0015_v0119_account.sql")


def downgrade() -> None:
    pass  # schema chỉ thêm; hạ cấp = khôi phục bản sao lưu
