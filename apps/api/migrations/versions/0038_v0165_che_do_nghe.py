"""v0.1.65 — chế độ nghe & danh bạ: core.groups.left_at (nhóm đã rời) + settings.direct_mode (tin 1-1 mặc định im lặng).
ADD COLUMN IF NOT EXISTS + chỉ ghi khoá còn thiếu, chạy lại an toàn.

Revision ID: 0038
Revises: 0037
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlfile import run_file  # noqa: E402

revision = "0038"
down_revision = "0037"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_file("0038_v0165_che_do_nghe.sql")


def downgrade() -> None:
    pass  # chỉ thêm cột và khoá cài đặt; hạ cấp = khôi phục bản sao lưu
