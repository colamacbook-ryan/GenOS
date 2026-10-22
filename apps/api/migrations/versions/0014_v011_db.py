"""v0.1.1 mục 1a — chỉ mục còn thiếu + vai trò gh_app (ARCHITECTURE §8.3, PLAN §5.6 lỗi 🟠/🟡).

Revision ID: 0014
Revises: 0013

Mật khẩu `gh_app` lấy từ biến môi trường `GH_APP_DB_PASSWORD` LÚC CHẠY MIGRATION (không lưu vào SQL thuần
db/sql — file đó chỉ tạo role NOLOGIN idempotent, xem 0014_v011_db.sql). Thiếu biến môi trường → giữ NOLOGIN
và cảnh báo (an toàn hơn NOLOGIN có mật khẩu đoán được); role đã có sẵn → ALTER mật khẩu (không tạo lại).
"""

import logging
import os
import sys
from pathlib import Path

from alembic import op
from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlfile import run_file  # noqa: E402

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None

log = logging.getLogger("alembic.gh_app")


def _pg_quote_literal(value: str) -> str:
    """Quote một chuỗi thành literal SQL an toàn (nhân đôi dấu nháy đơn) — CREATE/ALTER ROLE ... PASSWORD chỉ
    nhận Sconst (literal), Postgres KHÔNG cho tham số hoá ($1) trong lệnh DDL này nên phải tự quote ở đây thay
    vì nối chuỗi thô."""
    return "'" + value.replace("'", "''") + "'"


def upgrade() -> None:
    run_file("0014_v011_db.sql")  # 2 chỉ mục + role NOLOGIN idempotent + GRANT/ALTER DEFAULT PRIVILEGES

    password = os.environ.get("GH_APP_DB_PASSWORD")
    bind = op.get_bind()
    if password:
        bind.execute(text(f"ALTER ROLE gh_app LOGIN PASSWORD {_pg_quote_literal(password)}"))
        log.info("Đã đặt/ALTER mật khẩu gh_app từ GH_APP_DB_PASSWORD (role LOGIN).")
    else:
        log.warning(
            "GH_APP_DB_PASSWORD không đặt lúc migrate — role gh_app giữ NOLOGIN. Trình cài (giai đoạn 6) hoặc "
            "vận hành viên cần ALTER ROLE gh_app LOGIN PASSWORD '...' thủ công trước khi container api/worker "
            "dùng role này (xem GH_ADMIN_DATABASE_URL / GH_APP_DB_PASSWORD trong docs/reports/HANDOFF-v0.1.1.md)."
        )


def downgrade() -> None:
    pass  # schema giai đoạn 5 trở đi chỉ thêm; hạ cấp = khôi phục bản sao lưu (theo 0012/0013)
