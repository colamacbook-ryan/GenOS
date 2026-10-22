"""Baseline schema (docs/handoff/schema.sql — đã bỏ ở v0.1.48, xem lịch sử git — + sửa pg_partman 5).

Revision ID: 0001
Revises:
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlfile import run_file, run_sql  # noqa: E402

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_file("0001_baseline.sql")


def downgrade() -> None:
    run_sql("""
        DELETE FROM partman.part_config WHERE parent_table LIKE 'raw.%' OR parent_table LIKE 'ops.%'
            OR parent_table LIKE 'clean.%' OR parent_table LIKE 'agent.%' OR parent_table LIKE 'memory.%'
            OR parent_table LIKE 'analytics.%' OR parent_table LIKE 'refinery.%' OR parent_table LIKE 'biz.%';
        DO $$ DECLARE t text; BEGIN
          FOR t IN SELECT tablename FROM pg_tables WHERE schemaname = 'partman' AND tablename LIKE 'template_%' LOOP
            EXECUTE format('DROP TABLE partman.%I', t);
          END LOOP;
        END $$;
        DROP SCHEMA IF EXISTS analytics, ops, agent, biz, memory, clean, refinery, raw CASCADE;
        DROP SCHEMA IF EXISTS core CASCADE;
    """)
