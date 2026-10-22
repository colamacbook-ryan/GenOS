"""v0.1.50 — Gen nhớ (agent.gen_memory_notes) + đề xuất ghi Phiên vào Kho mỗi bản mới (agent.hub_release_proposals)
(F-81, F-87, QD-18).

Revision ID: 0032
Revises: 0031
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlfile import run_file  # noqa: E402

revision = "0032"
down_revision = "0031"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_file("0032_v0150_gen_memory_kho_write.sql")


def downgrade() -> None:
    pass  # schema chỉ thêm; hạ cấp = khôi phục bản sao lưu
