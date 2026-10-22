"""v0.1.41 — đánh giá Hữu ích/Không hữu ích của Gen (agent.gen_feedback) + bảng giá model (agent.model_prices) (F-84).

Revision ID: 0027
Revises: 0026
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlfile import run_file  # noqa: E402

revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_file("0027_v0141_gen_feedback_costs.sql")


def downgrade() -> None:
    pass  # schema chỉ thêm; hạ cấp = khôi phục bản sao lưu
