"""v0.1.68 — Nhật ký lắng nghe: raw.events.listen_mode (chế độ nghe lúc tin vào) + ops.listen_events (sự kiện kênh).
Chạy lại an toàn (IF NOT EXISTS). Chuỗi migration: 0037 Telegram → 0038 chế độ nghe → 0039 agent thật → 0040 (tệp này);
v0.1.69 nối tiếp bằng 0041.

Revision ID: 0040
Revises: 0039
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlfile import run_file  # noqa: E402

revision = "0040"
down_revision = "0039"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_file("0040_v0168_nhat_ky_lang_nghe.sql")


def downgrade() -> None:
    pass  # chỉ thêm cột NULL và bảng mới; hạ cấp = khôi phục bản sao lưu
