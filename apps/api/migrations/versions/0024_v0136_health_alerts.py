"""v0.1.36 (F-6) — ops.health_alerts: sự cố đang mở theo key (chuông tự khử trùng lặp + dải "Cần Sếp xử lý").

Revision ID: 0024
Revises: 0023
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlfile import run_file  # noqa: E402

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_file("0024_v0136_health_alerts.sql")


def downgrade() -> None:
    pass  # schema chỉ thêm; hạ cấp = khôi phục bản sao lưu
