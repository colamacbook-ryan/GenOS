"""v0.1.47 — ghi mạng xã hội có xác nhận: việc 'write' (cột action + ảnh chụp bằng chứng), trần lượt gửi/ngày,
bảng ops.risk_consents (đồng ý rủi ro theo chủ đề) (F-79, F-85).

Revision ID: 0031
Revises: 0030
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlfile import run_file  # noqa: E402

revision = "0031"
down_revision = "0030"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_file("0031_v0147_social_write.sql")


def downgrade() -> None:
    pass  # schema chỉ thêm; hạ cấp = khôi phục bản sao lưu
