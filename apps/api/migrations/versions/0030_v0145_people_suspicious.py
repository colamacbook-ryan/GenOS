"""v0.1.45 — cờ "Đáng ngờ" cho điểm đánh giá nhân sự (biz.people_reviews.suspicious*, bỏ cờ: suspicious_cleared_*) (F-60).

Revision ID: 0030
Revises: 0029
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlfile import run_file  # noqa: E402

revision = "0030"
down_revision = "0029"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_file("0030_v0145_people_suspicious.sql")


def downgrade() -> None:
    pass  # schema chỉ thêm; hạ cấp = khôi phục bản sao lưu
