"""v0.1.54 (g1-api) — Gen hướng dẫn: agent.gen_coach_prefs (tuỳ chọn + mốc ổn định/chuông của từng Owner) +
agent.gen_coach_items (trạng thái từng việc / mẹo / bài học theo Owner).

Revision ID: 0033
Revises: 0032
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlfile import run_file  # noqa: E402

revision = "0033"
down_revision = "0032"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_file("0033_v0154_gen_coach.sql")


def downgrade() -> None:
    pass  # schema chỉ thêm; hạ cấp = khôi phục bản sao lưu
