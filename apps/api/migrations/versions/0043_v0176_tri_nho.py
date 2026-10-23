"""v0.1.76 — lõi trí nhớ 5 lớp (gói L1): schema mem, sự thật hai thời gian, bối cảnh nhóm, hợp đồng bộ não.
Extension unaccent/btree_gist, mem.fold, biz.signal_kinds, cột mới của clean.evidence/meaning_units, clean.media_texts,
refinery.group_watermarks/analyses/fact_retry, view refinery.group_budget, raw.attachments.org_id; RLS + GRANT.
Chỉ thêm, mọi câu lệnh chạy lại an toàn.

Revision ID: 0043
Revises: 0042
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlfile import run_file  # noqa: E402

revision = "0043"
down_revision = "0042"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_file("0043_v0176_tri_nho.sql")


def downgrade() -> None:
    pass  # chỉ thêm bảng/cột/hàm; hạ cấp = khôi phục bản sao lưu
