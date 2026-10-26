"""v0.1.77 — ưu tiên (gói P2, spec r7.2 §4.2.1 (h)): biz.signal_feedback + reason/rule_version; settings intel.work_start/work_end/
ramp_started_at (jsonb_set chỉ khi chưa có) và đặt lại ramp_gates một lần. Chỉ thêm, mọi câu lệnh chạy lại an toàn.

Revision ID: 0045
Revises: 0044
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlfile import run_file  # noqa: E402

revision = "0045"
down_revision = "0044"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_file("0045_v0177_uu_tien.sql")


def downgrade() -> None:
    pass  # chỉ thêm cột/khoá jsonb; hạ cấp = khôi phục bản sao lưu
