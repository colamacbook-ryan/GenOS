"""Bổ sung giai đoạn 1 (ARCHITECTURE §12).

Revision ID: 0002
Revises: 0001
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlfile import run_file, run_sql  # noqa: E402

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_file("0002_phase1.sql")


def downgrade() -> None:
    run_sql("""
        ALTER TABLE ops.plugins DROP COLUMN IF EXISTS settings, DROP COLUMN IF EXISTS manifest;
        ALTER TABLE core.sessions DROP COLUMN IF EXISTS last_seen_at, DROP COLUMN IF EXISTS csrf_hash;
        ALTER TABLE biz.action_drafts DROP COLUMN IF EXISTS permit_used_at, DROP COLUMN IF EXISTS permit_expires_at,
            DROP COLUMN IF EXISTS permit_hash, DROP COLUMN IF EXISTS flags;
        DROP TABLE IF EXISTS agent.cli_profiles, biz.alerts, agent.decisions, core.assignments;
        DROP TRIGGER IF EXISTS action_log_no_truncate ON ops.action_log;
        DROP TRIGGER IF EXISTS raw_events_no_truncate ON raw.events;
        DROP FUNCTION IF EXISTS core.forbid_truncate();
    """)
