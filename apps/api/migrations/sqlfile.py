"""Chạy một tệp SQL trong db/sql bằng kết nối DBAPI (hỗ trợ khối DO $$ … $$ nhiều câu lệnh)."""

import os
from pathlib import Path

from alembic import op


def sql_dir() -> Path:
    env = os.environ.get("GH_SQL_DIR")
    if env:
        return Path(env)
    for parent in Path(__file__).resolve().parents:
        if (parent / "db" / "sql").is_dir():
            return parent / "db" / "sql"
    raise FileNotFoundError("Không tìm thấy db/sql (đặt GH_SQL_DIR)")


def run_sql(sql: str) -> None:
    raw = op.get_bind().connection.dbapi_connection
    assert raw is not None
    with raw.cursor() as cur:
        cur.execute(sql)


def run_file(name: str) -> None:
    run_sql((sql_dir() / name).read_text(encoding="utf-8"))
