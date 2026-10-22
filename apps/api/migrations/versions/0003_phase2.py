"""Giai đoạn 2: tầng dữ liệu, kênh, bộ não AI.

Revision ID: 0003
Revises: 0002
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlfile import run_file, run_sql  # noqa: E402

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    run_file("0003_phase2.sql")


def downgrade() -> None:
    run_sql("""
        DELETE FROM core.lookup WHERE kind IN ('event_type', 'rule_kind', 'score_dimension', 'channel_type');
        DROP INDEX IF EXISTS agent.model_calls_org_id_at_idx;
        ALTER TABLE agent.models DROP COLUMN IF EXISTS is_enabled;
        ALTER TABLE agent.providers DROP COLUMN IF EXISTS last_test, DROP COLUMN IF EXISTS created_at;
        ALTER TABLE core.channel_sessions DROP COLUMN IF EXISTS external_account,
            DROP COLUMN IF EXISTS risk_accepted_by, DROP COLUMN IF EXISTS org_id;
        ALTER TABLE memory.notebooks DROP COLUMN IF EXISTS created_at;
        ALTER TABLE memory.entries DROP COLUMN IF EXISTS tokens, DROP COLUMN IF EXISTS replaced_by;
        ALTER TABLE core.identity_merge_log DROP COLUMN IF EXISTS reverted_by, DROP COLUMN IF EXISTS reverted_at,
            DROP COLUMN IF EXISTS snapshot, DROP COLUMN IF EXISTS candidate_id, DROP COLUMN IF EXISTS org_id;
        DROP INDEX IF EXISTS core.identity_merge_candidates_least_greatest_idx;
        ALTER TABLE core.identity_merge_candidates DROP COLUMN IF EXISTS evidence, DROP COLUMN IF EXISTS basis_text;
        ALTER TABLE clean.meaning_units DROP COLUMN IF EXISTS rule_codes, DROP COLUMN IF EXISTS scores,
            DROP COLUMN IF EXISTS score;
        DROP TABLE IF EXISTS refinery.rule_hits_hourly;
        ALTER TABLE refinery.rules DROP COLUMN IF EXISTS updated_at, DROP COLUMN IF EXISTS created_at;
        ALTER TABLE refinery.runs DROP COLUMN IF EXISTS error, DROP COLUMN IF EXISTS processed,
            DROP COLUMN IF EXISTS requested_by;
        ALTER TABLE refinery.event_state DROP COLUMN IF EXISTS detail, DROP COLUMN IF EXISTS attempts,
            DROP COLUMN IF EXISTS fast, DROP COLUMN IF EXISTS confidence, DROP COLUMN IF EXISTS label;
        DROP TABLE IF EXISTS raw.event_keys;
        ALTER TABLE raw.events DROP COLUMN IF EXISTS mentions_agent, DROP COLUMN IF EXISTS direction,
            DROP COLUMN IF EXISTS seq;
        DROP SEQUENCE IF EXISTS raw.events_seq;
    """)
