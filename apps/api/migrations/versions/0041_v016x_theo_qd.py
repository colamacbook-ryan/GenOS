"""v0.1.69 — chuông có vòng đời (C4: cột core.notifications.resolved_at, incident_key) và dữ liệu/mặc định đúng quyết định Sếp (D3).
Mọi câu lệnh chạy lại an toàn.

Revision ID: 0041
Revises: 0040
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlfile import run_file  # noqa: E402

revision = "0041"
# Sau 0037 (Telegram), 0038 (F1), 0039 (C6), 0040 (Nhật ký lắng nghe, v0.1.68) — alembic chỉ còn một head
# (test_migrations_heads).
down_revision = "0040"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_file("0041_v016x_theo_qd.sql")


def downgrade() -> None:
    pass  # chỉ thêm cột NULL và sửa chữ/mặc định; hạ cấp = khôi phục bản sao lưu
