"""v0.1.61 — danh mục lệch: tên vai trò tiếng Việt (core.roles.name) và khoá mẫu agent bước 8 (sales→commercial, ops→admin).
UPDATE ... WHERE giá trị cũ, chạy lại an toàn.

Revision ID: 0036
Revises: 0035
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlfile import run_file  # noqa: E402

revision = "0036"
down_revision = "0035"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_file("0036_v016x_danh_muc.sql")


def downgrade() -> None:
    pass  # chỉ đổi chữ tên vai trò và khoá mẫu; hạ cấp = khôi phục bản sao lưu
