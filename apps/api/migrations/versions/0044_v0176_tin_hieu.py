"""v0.1.76 — nền tín hiệu + 6 cổng INT-19 + một hàng Cần quyết (gói T1): biz.alerts mở rộng thành bảng tín hiệu (cột level/
gate/candidate/delivery/reasons/privacy/case_key…, VIEW biz.signals), backfill theo alert_type, signal_links/feedback/mutes,
ops.asks, promises/payment_identifiers/catalog_items, owner_outbox.refs, ops.backup_outbox; RLS + GRANT.
Chỉ thêm, mọi câu lệnh chạy lại an toàn.

Revision ID: 0044
Revises: 0043
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlfile import run_file  # noqa: E402

revision = "0044"
down_revision = "0043"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_file("0044_v0176_tin_hieu.sql")


def downgrade() -> None:
    pass  # chỉ thêm bảng/cột; hạ cấp = khôi phục bản sao lưu
