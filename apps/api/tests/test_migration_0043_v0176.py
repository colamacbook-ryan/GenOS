# ruff: noqa: E501
"""v0.1.76 (gói L1) — migration 0043 «lõi trí nhớ»: chạy lại an toàn (DB trống và DB có dữ liệu cũ nâng từ 0042), lược đồ đúng tên
(hợp đồng giữa các gói L2–L6), ràng buộc, RLS + quyền gh_app, extension unaccent/btree_gist, vòng pg_dump → pg_restore dựng lại
cột sinh `alias_folded` và chỉ mục `mem.fold` (CNS-066)."""

import asyncio
import os
import subprocess
import sys
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import psycopg
import pytest
from psycopg import errors as pgerr

from gh.backup import restore_backup, run_backup
from gh.chassis.objects import LocalObjectStore
from gh.config import get_settings
from tests.conftest import PG

ROOT = Path(__file__).resolve().parents[3]
API_DIR = ROOT / "apps" / "api"
SQL_0043 = ROOT / "db" / "sql" / "0043_v0176_tri_nho.sql"

RLS_TABLES = ("mem.entities", "mem.aliases", "mem.mentions", "mem.group_contexts", "mem.disputes", "mem.facts",
              "mem.fact_evidence", "mem.fact_status_events", "mem.relation_heat_weekly", "mem.entity_summaries",
              "mem.owner_profile", "clean.media_texts", "refinery.group_watermarks", "refinery.analyses",
              "refinery.fact_retry", "raw.attachments")


@contextmanager
def _conn(db_name: str) -> Iterator[psycopg.Connection[Any]]:
    with psycopg.connect(f"{PG}/{db_name}", autocommit=True) as c:
        yield c


def _admin(sql: str) -> None:
    with psycopg.connect(f"{PG}/postgres", autocommit=True) as c:
        c.execute(sql)  # type: ignore[call-overload]


async def _alembic(target: str, env: dict[str, str]) -> None:
    await asyncio.to_thread(subprocess.run, [sys.executable, "-m", "alembic", "upgrade", target], cwd=API_DIR, env=env, check=True,
                            capture_output=True)


def _org(c: psycopg.Connection[Any], name: str = "Org") -> uuid.UUID:
    return c.execute("INSERT INTO core.organizations (name) VALUES (%s) RETURNING id", (name,)).fetchone()[0]  # type: ignore[index, no-any-return]


def _cols(c: psycopg.Connection[Any], schema: str, table: str) -> dict[str, str]:
    rows = c.execute("""SELECT column_name, data_type FROM information_schema.columns
                        WHERE table_schema = %s AND table_name = %s""", (schema, table)).fetchall()
    return {r[0]: r[1] for r in rows}


def _entity(c: psycopg.Connection[Any], org: uuid.UUID, name: str = "Lan", etype: str = "person") -> uuid.UUID:
    return c.execute("INSERT INTO mem.entities (org_id, entity_type, display_name) VALUES (%s, %s, %s) RETURNING id",
                     (org, etype, name)).fetchone()[0]  # type: ignore[index, no-any-return]


def _fact(c: psycopg.Connection[Any], org: uuid.UUID, subject: uuid.UUID, *, ev: str | None = "verified", **kw: Any) -> uuid.UUID:
    """Một sự thật + (mặc định) một bằng chứng đã kiểm trong CÙNG giao dịch (kiểm bằng chứng hoãn tới lúc commit)."""
    v = {"predicate": "contact_phone", "value": '"0901"', "key": "k1", "vf": "2026-10-01", "vt": None, "status": "asserted",
         "single": True, "cls": "insider", **kw}
    with c.transaction():
        fid = c.execute(
            """INSERT INTO mem.facts (org_id, subject_id, predicate, object_value, object_key, valid_from, valid_to, status, is_single,
                                      source_class, source_reliability, confidence_exist, confidence_content, created_by)
               VALUES (%s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s, %s, 0.8, 0.5, 0.5, 'extractor') RETURNING id""",
            (org, subject, v["predicate"], v["value"], v["key"], v["vf"], v["vt"], v["status"], v["single"], v["cls"]),
        ).fetchone()[0]  # type: ignore[index]
        if ev is not None:
            _evidence(c, org, fid, ev)
    return fid  # type: ignore[no-any-return]


def _fact_raw(c: psycopg.Connection[Any], org: uuid.UUID, subject: uuid.UUID, **kw: Any) -> uuid.UUID:
    """Như `_fact` nhưng KHÔNG tự mở giao dịch/không tự thêm bằng chứng (test kiểm bằng chứng hoãn dùng giao dịch của chính nó)."""
    v = {"predicate": "contact_phone", "value": '"0901"', "key": "k1", "vf": "2026-10-01", "vt": None, "status": "asserted",
         "single": True, "cls": "insider", **kw}
    return c.execute(
        """INSERT INTO mem.facts (org_id, subject_id, predicate, object_value, object_key, valid_from, valid_to, status, is_single,
                                  source_class, source_reliability, confidence_exist, confidence_content, created_by)
           VALUES (%s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s, %s, 0.8, 0.5, 0.5, 'extractor') RETURNING id""",
        (org, subject, v["predicate"], v["value"], v["key"], v["vf"], v["vt"], v["status"], v["single"], v["cls"]),
    ).fetchone()[0]  # type: ignore[index, no-any-return]


def _evidence(c: psycopg.Connection[Any], org: uuid.UUID, fact: uuid.UUID, status: str = "verified") -> None:
    c.execute("""INSERT INTO mem.fact_evidence (org_id, fact_id, kind, raw_event_id, quote_status)
                 VALUES (%s, %s, 'raw_event', gen_random_uuid(), %s)""", (org, fact, status))


# ─── cây migration ──────────────────────────────────────────────────────────

def test_migration_0043_follows_0042_and_is_the_single_head() -> None:
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    cfg = Config(str(API_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(API_DIR / "migrations"))
    script = ScriptDirectory.from_config(cfg)
    rev = script.get_revision("0043")
    assert rev is not None and rev.down_revision == "0042"
    assert script.get_heads() == ["0043"]
    assert SQL_0043.is_file()


# ─── chạy lại an toàn ───────────────────────────────────────────────────────

async def test_migration_0043_rerun_twice_on_empty_db_changes_nothing(fresh_db: str) -> None:
    sql = SQL_0043.read_text(encoding="utf-8")

    def snap(c: psycopg.Connection[Any]) -> Any:
        return (c.execute("SELECT count(*) FROM mem.predicates").fetchone(), c.execute("SELECT count(*) FROM mem.context_types").fetchone(),
                c.execute("SELECT count(*) FROM biz.signal_kinds").fetchone(),
                c.execute("SELECT count(*) FROM pg_policies WHERE schemaname IN ('mem', 'clean', 'refinery', 'raw')").fetchone(),
                c.execute("SELECT count(*) FROM pg_indexes WHERE schemaname = 'mem'").fetchone(),
                c.execute("SELECT count(*) FROM pg_trigger t JOIN pg_class k ON k.oid = t.tgrelid JOIN pg_namespace n ON n.oid = k.relnamespace "
                          "WHERE n.nspname = 'mem' AND NOT t.tgisinternal").fetchone(),
                c.execute("SELECT count(*) FROM pg_constraint c JOIN pg_namespace n ON n.oid = c.connamespace WHERE n.nspname = 'mem'").fetchone())

    with _conn(fresh_db) as c:
        before = snap(c)
        c.execute(sql)  # type: ignore[call-overload]
        c.execute(sql)  # type: ignore[call-overload]
        assert snap(c) == before
        assert c.execute("SELECT count(*) FROM mem.predicates").fetchone() == (29,)
        assert c.execute("SELECT count(*) FROM mem.context_types").fetchone() == (8,)


async def test_migration_0043_upgrade_from_0042_with_legacy_data_backfills_and_rerun_is_stable() -> None:
    """DB có dữ liệu cũ (người, nhóm, tin, tệp đính kèm, đơn vị ý nghĩa, chứng cứ) ở 0042 → nâng lên 0043: backfill thực thể,
    org_id của tệp đính kèm, mặc định cột mới; chạy lại lần nữa không nhân đôi gì (mô phỏng "bản sao dữ liệu staging")."""
    name = f"gh_mig43_{uuid.uuid4().hex[:8]}"
    _admin(f"CREATE DATABASE {name}")
    env = {**os.environ, "GH_DATABASE_URL": f"{PG.replace('postgresql://', 'postgresql+asyncpg://')}/{name}", "GH_ADMIN_DATABASE_URL": ""}
    try:
        await _alembic("0042", env)
        with _conn(name) as c:
            assert c.execute("SELECT to_regclass('mem.facts')").fetchone() == (None,)
            org = _org(c)
            ch = c.execute("INSERT INTO core.channels (org_id, type, name) VALUES (%s, 'zalo', 'z') RETURNING id", (org,)).fetchone()[0]  # type: ignore[index]
            p_live = c.execute("INSERT INTO core.persons (org_id, code, display_name) VALUES (%s, 'PER-1', 'Chị Tư') RETURNING id", (org,)).fetchone()[0]  # type: ignore[index]
            p_merged = c.execute("INSERT INTO core.persons (org_id, code, display_name, merged_into_id) VALUES (%s, 'PER-2', 'Tư B', %s) RETURNING id",
                                 (org, p_live)).fetchone()[0]  # type: ignore[index]
            c.execute("INSERT INTO core.persons (org_id, code, display_name, deleted_at) VALUES (%s, 'PER-3', 'Đã xoá', now())", (org,))
            c.execute("INSERT INTO core.groups (org_id, code, channel_id, external_id, name) VALUES (%s, 'GRP-1', %s, 'g1', 'Nhóm kho')", (org, ch))
            ev = c.execute("""INSERT INTO raw.events (org_id, occurred_at, channel_id, external_msg_id, kind, body_text, payload, content_hash)
                              VALUES (%s, now(), %s, 'm1', 'text', 'xin chào', '{}', '\\x01') RETURNING id, received_at""", (org, ch)).fetchone()
            assert ev is not None
            att = c.execute("""INSERT INTO raw.attachments (event_id, event_received_at, storage_key, mime, bytes, sha256)
                               VALUES (%s, %s, 'k', 'image/png', 3, '\\x02') RETURNING id""", (ev[0], ev[1])).fetchone()[0]  # type: ignore[index]
            unit = c.execute("""INSERT INTO clean.meaning_units (org_id, observed_at, event_type, conclusion, confidence, run_id)
                                VALUES (%s, now(), 'AskedPrice', 'Hỏi giá', 0.9, core.uuid_v7()) RETURNING id, observed_at""", (org,)).fetchone()
            assert unit is not None
            c.execute("INSERT INTO clean.evidence (meaning_unit_id, meaning_observed_at, raw_event_id, raw_received_at, quote) VALUES (%s, %s, %s, %s, 'xin chào')",
                      (unit[0], unit[1], ev[0], ev[1]))
        await _alembic("head", env)
        with _conn(name) as c:
            assert c.execute("SELECT count(*) FROM mem.entities WHERE entity_type = 'person'").fetchone() == (1,)    # không gộp, không xoá
            assert c.execute("SELECT person_id FROM mem.entities WHERE entity_type = 'person'").fetchone() == (p_live,)
            assert c.execute("SELECT count(*) FROM mem.entities WHERE person_id = %s", (p_merged,)).fetchone() == (0,)
            assert c.execute("SELECT count(*), min(display_name) FROM mem.entities WHERE entity_type = 'group'").fetchone() == (1, "Nhóm kho")
            assert c.execute("SELECT org_id FROM raw.attachments WHERE id = %s", (att,)).fetchone() == (org,)        # backfill từ raw.events
            assert c.execute("SELECT quote_status, extra_spans FROM clean.evidence").fetchone() == ("unverifiable", [])
            assert c.execute("SELECT kind_hint, flags, gate_candidate, harm, extractor_version FROM clean.meaning_units").fetchone() == (None, {}, None, None, None)
            before = (c.execute("SELECT count(*) FROM mem.entities").fetchone(), c.execute("SELECT count(*) FROM mem.aliases").fetchone())
            sql = SQL_0043.read_text(encoding="utf-8")
            c.execute(sql)  # type: ignore[call-overload]
            c.execute(sql)  # type: ignore[call-overload]
            assert (c.execute("SELECT count(*) FROM mem.entities").fetchone(), c.execute("SELECT count(*) FROM mem.aliases").fetchone()) == before
            assert c.execute("SELECT alias_folded FROM mem.aliases ORDER BY alias_folded").fetchall() == [("chi tu",), ("nhom kho",)]
    finally:
        _admin(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)")


# ─── lược đồ ────────────────────────────────────────────────────────────────

async def test_migration_0043_extensions_functions_and_added_columns(fresh_db: str) -> None:
    with _conn(fresh_db) as c:
        ext = dict(c.execute("SELECT e.extname, n.nspname FROM pg_extension e JOIN pg_namespace n ON n.oid = e.extnamespace "
                             "WHERE e.extname IN ('unaccent', 'btree_gist')").fetchall())
        assert ext == {"unaccent": "public", "btree_gist": "public"}
        assert c.execute("SELECT mem.fold('Đồng Nai')").fetchone() == ("dong nai",)
        assert set(_cols(c, "clean", "evidence")) >= {"quote_status", "source_field", "span_start", "span_end", "extra_spans",
                                                     "verified_at", "extractor_version"}
        assert set(_cols(c, "clean", "meaning_units")) >= {"extractor_version", "kind_hint", "analysis_id", "flags", "gate_candidate",
                                                          "level_hint", "harm"}
        assert {"thread_key", "analysis_id"} <= set(_cols(c, "agent", "model_calls"))
        assert "org_id" in _cols(c, "raw", "attachments")
        assert c.execute("SELECT count(*) FROM pg_indexes WHERE indexname = 'meaning_units_conclusion_fold_idx'").fetchone() == (1,)
        # chỉ mục chữ bỏ dấu dùng được (cột phân vùng)
        assert c.execute("SELECT count(*) FROM pg_indexes WHERE tablename LIKE 'meaning_units%' AND indexdef LIKE '%mem.fold%'").fetchone()[0] >= 1  # type: ignore[index]


async def test_migration_0043_tables_have_contract_columns(fresh_db: str) -> None:
    expect = {
        ("mem", "entities"): {"id", "org_id", "entity_type", "person_id", "group_id", "display_name", "is_owner", "merged_into", "attrs",
                              "created_at", "forgotten_at"},
        ("mem", "aliases"): {"id", "org_id", "entity_id", "alias", "alias_folded", "scope_group_id", "source", "confidence",
                             "first_seen_at", "last_seen_at", "evidence_raw_id"},
        ("mem", "mentions"): {"id", "org_id", "meaning_unit_id", "meaning_observed_at", "raw_event_id", "surface", "entity_id", "method",
                              "confidence", "status"},
        ("mem", "context_types"): {"code", "label_vi", "policy"},
        ("mem", "group_contexts"): {"org_id", "group_id", "context", "is_primary", "source", "confidence", "confirmed_by",
                                    "confirmed_at", "evidence"},
        ("mem", "predicates"): {"code", "cardinality", "subject_types", "object_kind", "sensitivity", "dispute_signal_kind",
                                "owner_section", "auto_supersede", "description_vi"},
        ("mem", "facts"): {"id", "org_id", "subject_id", "predicate", "object_entity_id", "object_value", "object_key", "valid_from",
                           "valid_to", "recorded_at", "closed_recorded_at", "status", "is_single", "source_class", "source_reliability",
                           "agreement", "confidence_exist", "confidence_content", "attributed_to", "context_code", "sensitivity",
                           "source_raw_event_id", "supersedes", "superseded_by", "dispute_id", "evidence_n", "extractor_version",
                           "created_by", "confirmed_by", "confirmed_at"},
        ("mem", "fact_evidence"): {"id", "org_id", "fact_id", "kind", "raw_event_id", "raw_received_at", "meaning_unit_id",
                                   "action_log_id", "quote_status", "span", "created_at"},
        ("mem", "fact_status_events"): {"id", "org_id", "fact_id", "from_status", "to_status", "valid_to_before", "valid_to_after", "at",
                                        "by", "reason"},
        ("mem", "disputes"): {"id", "org_id", "subject_id", "predicate", "fact_ids", "dispute_signal_kind", "opened_at", "resolved_at",
                              "resolved_by", "winner_fact_id", "projected_signal_id", "note"},
        ("mem", "relation_heat_weekly"): {"org_id", "entity_a", "entity_b", "week", "msgs_ab", "msgs_ba", "initiated_a", "initiated_b",
                                          "reply_median_s", "heat", "state", "projection_version"},
        ("mem", "entity_summaries"): {"entity_id", "org_id", "summary", "fact_ids", "projection_version", "built_at"},
        ("mem", "owner_profile"): {"org_id", "owner_entity_id", "version", "updated_at"},
        ("clean", "media_texts"): {"id", "org_id", "raw_event_id", "raw_received_at", "attachment_id", "kind", "text", "lang", "engine",
                                   "engine_version", "confidence", "status", "error_code", "created_at"},
        ("refinery", "group_watermarks"): {"org_id", "thread_key", "group_id", "last_received_at", "last_event_id", "last_analysis_at",
                                           "pending_n", "priority", "stretched_at", "capped_at", "stretched_minutes", "capped_minutes"},
        ("refinery", "analyses"): {"id", "org_id", "thread_key", "run_id", "window_from", "window_to", "new_n", "context_n", "facts_in_n",
                                   "profile_version", "contract_version", "model", "tokens_in", "tokens_out", "cache_read", "stage",
                                   "status", "errors", "started_at", "finished_at"},
        ("refinery", "fact_retry"): {"id", "org_id", "analysis_id", "fact_in", "error_code", "attempts", "next_at", "created_at"},
        ("refinery", "group_budget"): {"org_id", "thread_key", "day", "tokens_in", "tokens_out", "cost_vnd", "calls"},
        ("biz", "signal_kinds"): {"code", "label_vi", "description_vi", "on_intel", "sort_order"},
    }
    with _conn(fresh_db) as c:
        for (schema, table), names in expect.items():
            assert set(_cols(c, schema, table)) == names, f"{schema}.{table}"
        assert c.execute("SELECT count(*) FROM information_schema.views WHERE table_schema = 'mem' AND table_name = 'v_owner_profile'").fetchone() == (1,)


async def test_migration_0043_seed_values(fresh_db: str) -> None:
    with _conn(fresh_db) as c:
        assert c.execute("SELECT count(*) FROM biz.signal_kinds WHERE on_intel").fetchone() == (10,)
        assert [r[0] for r in c.execute("SELECT code FROM mem.context_types ORDER BY code").fetchall()] == sorted(
            ["kinh_doanh", "cong_viec", "hoc_tap", "gia_dinh", "ban_be", "nhan_vien", "giao_thuong", "khac"])
        assert c.execute("SELECT count(*) FROM mem.context_types").fetchone() == (8,)
        assert c.execute("SELECT auto_supersede FROM mem.predicates WHERE code = 'payment_account'").fetchone() == (False,)
        assert c.execute("SELECT count(*) FROM mem.predicates WHERE auto_supersede").fetchone() == (28,)
        assert c.execute("SELECT code FROM mem.predicates WHERE cardinality = 'single' ORDER BY code").fetchall() == [
            ("contact_phone",), ("lives_in",), ("payment_account",), ("role_in_group",), ("works_at",)]
        assert c.execute("SELECT dispute_signal_kind FROM mem.predicates WHERE code = 'payment_account'").fetchone() == ("rui_ro_lua_dao",)
        assert c.execute("SELECT count(*) FROM mem.predicates WHERE code LIKE 'owner\\_%'").fetchone() == (6,)


# ─── ràng buộc ──────────────────────────────────────────────────────────────

async def test_migration_0043_facts_exclude_check_and_evidence_trigger(fresh_db: str) -> None:
    with _conn(fresh_db) as c:
        org = _org(c)
        e = _entity(c, org)
        # EXCLUDE hai thời gian: hai dòng đơn trị hiệu lực chồng khoảng bị chặn (kể cả dòng đã đóng)
        a = _fact(c, org, e, key="a", vf="2026-10-01", vt="2026-10-05")
        with pytest.raises(pgerr.ExclusionViolation):
            _fact(c, org, e, key="b", vf="2026-10-03", vt=None)
        _fact(c, org, e, key="b", vf="2026-10-05", vt=None)                      # nối tiếp (khoảng nửa mở) ⇒ được
        _fact(c, org, e, key="c", vf="2026-10-03", status="disputed")           # disputed ngoài vùng EXCLUDE
        _fact(c, org, e, key="d", vf="2026-10-03", single=False, predicate="prefers")   # đa trị không bị EXCLUDE
        _fact(c, org, e, key="d2", vf="2026-10-03", single=False, predicate="prefers")
        # CHECK
        with pytest.raises(pgerr.CheckViolation):                                # valid_to < valid_from
            _fact(c, org, e, key="x", vf="2026-10-05", vt="2026-10-01", status="proposed")
        with pytest.raises(pgerr.CheckViolation):                                # owner_confirmed cần confirmed_by
            _fact(c, org, e, key="x", vf="2026-11-01", status="owner_confirmed")
        with pytest.raises(pgerr.CheckViolation):                                # outsider không được proposed
            _fact(c, org, e, key="x", vf="2026-11-01", status="proposed", cls="outsider", predicate="needs", single=False)
        with pytest.raises(pgerr.CheckViolation), c.transaction():               # đúng một trong entity/value
            c.execute("""INSERT INTO mem.facts (org_id, subject_id, predicate, object_key, valid_from, status, is_single, source_class,
                         source_reliability, confidence_exist, confidence_content, created_by)
                         VALUES (%s, %s, 'prefers', 'k', now(), 'proposed', false, 'insider', 0.5, 0.5, 0.5, 'extractor')""", (org, e))
        assert a is not None


async def test_migration_0043_deferred_evidence_constraint_trigger(fresh_db: str) -> None:
    with psycopg.connect(f"{PG}/{fresh_db}") as c:                           # giao dịch thật (không autocommit)
        org = _org(c)
        e = _entity(c, org)
        c.commit()
        tg = c.execute("SELECT tgdeferrable, tginitdeferred FROM pg_trigger WHERE tgname = 'mem_facts_need_evidence'").fetchone()
        assert tg == (True, True)                                               # DEFERRABLE INITIALLY DEFERRED
        # fact + bằng chứng cùng giao dịch ⇒ qua khi commit (bằng chứng nằm ở bảng con, CNS-063)
        f = _fact_raw(c, org, e, key="a", vt="2026-10-02")
        _evidence(c, org, f)
        c.commit()
        assert c.execute("SELECT evidence_n FROM mem.facts WHERE id = %s", (f,)).fetchone() == (1,)
        # thiếu bằng chứng ⇒ lỗi lúc commit
        _fact_raw(c, org, e, key="b", vf="2026-11-01")
        with pytest.raises(pgerr.CheckViolation, match="thiếu bằng chứng"):
            c.commit()
        c.rollback()
        # SET CONSTRAINTS IMMEDIATE bắt được ngay (đường assert_fact)
        _fact_raw(c, org, e, key="b", vf="2026-11-01")
        with pytest.raises(pgerr.CheckViolation):
            c.execute("SET CONSTRAINTS mem.mem_facts_need_evidence IMMEDIATE")
        c.rollback()
        # bằng chứng chưa kiểm không đủ cho asserted nhưng đủ cho proposed
        g = _fact_raw(c, org, e, key="p", vf="2026-12-01", status="proposed", single=False, predicate="prefers")
        _evidence(c, org, g, "unverifiable")
        c.commit()
        assert c.execute("SELECT evidence_n FROM mem.facts WHERE id = %s", (g,)).fetchone() == (0,)
        h = _fact_raw(c, org, e, key="q", vf="2026-12-01", single=False, predicate="prefers")
        _evidence(c, org, h, "unverifiable")
        with pytest.raises(pgerr.CheckViolation):
            c.commit()
        c.rollback()


async def test_migration_0043_unique_indexes_and_owner_rule(fresh_db: str) -> None:
    with _conn(fresh_db) as c:
        org = _org(c)
        e = _entity(c, org, "Sếp")
        c.execute("UPDATE mem.entities SET is_owner = true WHERE id = %s", (e,))
        with pytest.raises(pgerr.UniqueViolation):                               # một Sếp / tổ chức
            c.execute("INSERT INTO mem.entities (org_id, entity_type, display_name, is_owner) VALUES (%s, 'person', 'B', true)", (org,))
        c.execute("INSERT INTO mem.aliases (org_id, entity_id, alias, source) VALUES (%s, %s, 'Anh Bảy', 'mention')", (org, e))
        with pytest.raises(pgerr.UniqueViolation):                               # cùng (entity, alias_folded, scope)
            c.execute("INSERT INTO mem.aliases (org_id, entity_id, alias, source) VALUES (%s, %s, 'anh bay', 'mention')", (org, e))
        with pytest.raises(pgerr.GeneratedAlways):
            c.execute("UPDATE mem.aliases SET alias_folded = 'x'")
        _fact(c, org, e, key="a", single=False, predicate="prefers")
        with pytest.raises(pgerr.UniqueViolation):                               # bằng chứng cùng (fact, kind, nguồn)
            c.execute("""INSERT INTO mem.fact_evidence (org_id, fact_id, kind, raw_event_id, quote_status)
                         SELECT org_id, fact_id, kind, raw_event_id, quote_status FROM mem.fact_evidence""")
        raw = uuid.uuid4()
        c.execute("""UPDATE mem.facts SET source_raw_event_id = %s WHERE false""", (raw,))
        # idempotent index tồn tại
        assert c.execute("SELECT count(*) FROM pg_indexes WHERE indexname = 'facts_idempotent_uq'").fetchone() == (1,)


# ─── RLS + quyền ────────────────────────────────────────────────────────────

async def test_migration_0043_rls_enabled_and_grants(fresh_db: str) -> None:
    with _conn(fresh_db) as c:
        for t in RLS_TABLES:
            assert c.execute("SELECT relrowsecurity FROM pg_class WHERE oid = %s::regclass", (t,)).fetchone() == (True,), t
            schema, name = t.split(".")
            assert c.execute("SELECT count(*) FROM pg_policies WHERE schemaname = %s AND tablename = %s AND policyname = 'org_isolation'",
                             (schema, name)).fetchone() == (1,), t

        def can(priv: str, t: str) -> bool:
            return bool(c.execute("SELECT has_table_privilege('gh_app', %s, %s)", (t, priv)).fetchone()[0])  # type: ignore[index]

        for t in RLS_TABLES:
            assert can("SELECT", t) and can("INSERT", t), t
        assert not any(can(p, "mem.predicates") for p in ("INSERT", "UPDATE", "DELETE")) and can("SELECT", "mem.predicates")
        assert not any(can(p, "biz.signal_kinds") for p in ("INSERT", "UPDATE", "DELETE"))
        assert can("UPDATE", "mem.context_types") and not can("INSERT", "mem.context_types") and not can("DELETE", "mem.context_types")
        assert not can("UPDATE", "mem.fact_status_events") and can("DELETE", "mem.fact_status_events")   # trigger chặn xoá lẻ
        assert can("SELECT", "refinery.group_budget")
        assert c.execute("SELECT has_schema_privilege('gh_app', 'mem', 'USAGE')").fetchone() == (True,)
        assert c.execute("SELECT has_function_privilege('gh_app', 'mem.fold(text)', 'EXECUTE')").fetchone() == (True,)


async def test_migration_0043_rls_isolates_orgs_under_app_role(fresh_db: str) -> None:
    with _conn(fresh_db) as c:
        a, b = _org(c, "A"), _org(c, "B")
        for o in (a, b):
            e = _entity(c, o, f"nguoi-{o}")
            _fact(c, o, e, key=str(o))
            c.execute("INSERT INTO mem.aliases (org_id, entity_id, alias, source) VALUES (%s, %s, 'bi danh', 'owner')", (o, e))
            c.execute("INSERT INTO refinery.group_watermarks (org_id, thread_key) VALUES (%s, 'g:1')", (o,))
        c.execute("SET ROLE gh_app")
        try:
            c.execute("SELECT set_config('app.org_id', %s, false)", (str(a),))
            for t in ("mem.entities", "mem.facts", "mem.fact_evidence", "mem.fact_status_events", "refinery.group_watermarks", "mem.aliases"):
                orgs = {r[0] for r in c.execute(f"SELECT org_id FROM {t}").fetchall()}  # type: ignore[call-overload]
                assert orgs == {a}, t
            with pytest.raises(pgerr.InsufficientPrivilege):                     # ghi sang tổ chức khác bị RLS chặn
                c.execute("INSERT INTO mem.entities (org_id, entity_type, display_name) VALUES (%s, 'person', 'x')", (b,))
            with pytest.raises(pgerr.InsufficientPrivilege):                     # ứng dụng không thêm vị từ (chỉ migration)
                c.execute("INSERT INTO mem.predicates (code, cardinality, subject_types, object_kind, description_vi) "
                          "VALUES ('x', 'multi', '{person}', 'value', 'x')")
        finally:
            c.execute("RESET ROLE")


async def test_migration_0043_group_budget_view_one_source_with_ai_cost(fresh_db: str) -> None:
    from gh.providers.cli import CLI_KINDS

    with _conn(fresh_db) as c:
        src = c.execute("SELECT pg_get_viewdef('refinery.group_budget'::regclass)").fetchone()[0]  # type: ignore[index]
        for k in CLI_KINDS:                                                       # danh sách CLI thuê bao khớp ai_cost
            assert k in src
        assert c.execute("SELECT reloptions FROM pg_class WHERE oid = 'refinery.group_budget'::regclass").fetchone() == (["security_invoker=true"],)
        org = _org(c)
        prov = c.execute("""INSERT INTO agent.providers (org_id, kind, name) VALUES (%s, 'openai', 'p') RETURNING id""", (org,)).fetchone()[0]  # type: ignore[index]
        m = c.execute("INSERT INTO agent.models (provider_id, model_name) VALUES (%s, 'm') RETURNING id", (prov,)).fetchone()[0]  # type: ignore[index]
        c.execute("INSERT INTO agent.model_prices (model_id, org_id, in_vnd_per_mtok, out_vnd_per_mtok) VALUES (%s, %s, 1000000, 2000000)", (m, org))
        for tk, st in (("g:1", "ok"), ("g:1", "ok"), ("g:1", "error"), ("g:2", "ok"), (None, "ok")):
            c.execute("""INSERT INTO agent.model_calls (org_id, model_id, agent_key, purpose, tokens_in, tokens_out, status, thread_key)
                         VALUES (%s, %s, 'core.intel', 'refinery', 1000000, 500000, %s, %s)""", (org, m, st, tk))
        got = {r[0]: r[1:] for r in c.execute("SELECT thread_key, tokens_in, tokens_out, cost_vnd, calls FROM refinery.group_budget").fetchall()}
        assert got["g:1"] == (2_000_000, 1_000_000, 4_000_000, 2) and got["g:2"][3] == 1 and len(got) == 2   # lượt lỗi/không thẻ luồng không tính


# ─── pg_dump → pg_restore ───────────────────────────────────────────────────

async def test_migration_0043_pg_dump_restore_rebuilds_generated_column_and_fold_index(fresh_db: str, tmp_path: Path) -> None:
    """CNS-066: backup (`gh.backup`: pg_dump -Fc, restore đặt search_path rỗng) rồi khôi phục ⇒ cột sinh STORED `alias_folded`,
    chỉ mục biểu thức `mem.fold` và mọi sự thật dựng lại đúng; tìm theo chữ bỏ dấu vẫn chạy."""
    get_settings.cache_clear()
    with _conn(fresh_db) as c:
        org = _org(c)
        e = _entity(c, org, "Đặng Thùy Dương")
        c.execute("INSERT INTO mem.aliases (org_id, entity_id, alias, source) VALUES (%s, %s, 'chị Dương Đồng Nai', 'mention')", (org, e))
        _fact(c, org, e, key="a", vf="2026-10-01", vt="2026-10-05")
        c.execute("""INSERT INTO clean.meaning_units (org_id, observed_at, event_type, conclusion, confidence, run_id)
                     VALUES (%s, now(), 'AskedPrice', 'Chị Dương ở Đồng Nai hỏi giá thép', 0.9, core.uuid_v7())""", (org,))
    store = LocalObjectStore(root=str(tmp_path / "objects"))
    src = f"{PG}/{fresh_db}"
    entry = await run_backup(database_url=src, store=store)
    target = f"gh_mig43_restore_{uuid.uuid4().hex[:8]}"
    _admin(f"CREATE DATABASE {target}")
    try:
        await restore_backup(entry.key, database_url=src, target_database=target, store=store)
        with _conn(target) as c:
            assert c.execute("SELECT alias_folded FROM mem.aliases ORDER BY alias_folded").fetchall() == [("chi duong dong nai",)]
            assert c.execute("SELECT mem.fold('Đồng Nai')").fetchone() == ("dong nai",)
            # cột sinh vẫn là cột sinh, chỉ mục biểu thức còn đó và dùng được
            assert c.execute("SELECT attgenerated FROM pg_attribute WHERE attrelid = 'mem.aliases'::regclass AND attname = 'alias_folded'").fetchone() == ("s",)
            defs = " ".join(r[0] for r in c.execute("SELECT indexdef FROM pg_indexes WHERE indexname IN "
                                                    "('meaning_units_conclusion_fold_idx', 'entities_name_trgm_idx') "
                                                    "OR indexname LIKE 'meaning_units%conclusion_fold%'").fetchall())
            assert "mem.fold" in defs
            c.execute("SET enable_seqscan = off")
            hit = c.execute("SELECT count(*) FROM clean.meaning_units WHERE mem.fold(conclusion) LIKE %s", ("%dong nai%",)).fetchone()
            assert hit == (1,)
            c.execute("INSERT INTO mem.aliases (org_id, entity_id, alias, source) SELECT org_id, id, 'Mới Thêm', 'owner' FROM mem.entities")
            assert c.execute("SELECT alias_folded FROM mem.aliases WHERE alias = 'Mới Thêm'").fetchone() == ("moi them",)
            # sự thật + trigger + ràng buộc còn nguyên sau restore
            assert c.execute("SELECT status, valid_to IS NOT NULL, evidence_n FROM mem.facts").fetchone() == ("asserted", True, 1)
            org2 = c.execute("SELECT org_id FROM mem.entities").fetchone()[0]  # type: ignore[index]
            e2 = c.execute("SELECT id FROM mem.entities").fetchone()[0]  # type: ignore[index]
            with pytest.raises(pgerr.ExclusionViolation):
                _fact(c, org2, e2, key="b", vf="2026-10-03", vt=None)
            with pytest.raises(pgerr.IntegrityConstraintViolation):
                c.execute("UPDATE mem.facts SET object_key = 'x'")
            assert c.execute("SELECT count(*) FROM pg_trigger WHERE tgname = 'mem_facts_need_evidence'").fetchone() == (1,)
    finally:
        _admin(f"DROP DATABASE IF EXISTS {target} WITH (FORCE)")


async def test_migration_0043_rerun_upgrades_old_resolved_by_check(fresh_db: str) -> None:
    """DB đã có bản 0043 CŨ (CHECK resolved_by chưa có 'unmerge'): chạy lại migration đổi constraint, chạy tiếp lần nữa vẫn yên."""
    sql = SQL_0043.read_text(encoding="utf-8")
    with _conn(fresh_db) as c:
        org = _org(c)
        e = _entity(c, org)
        for name in [r[0] for r in c.execute("SELECT conname FROM pg_constraint WHERE conrelid = 'mem.disputes'::regclass "
                                             "AND contype = 'c' AND pg_get_constraintdef(oid) LIKE '%expiry%'").fetchall()]:
            c.execute(f'ALTER TABLE mem.disputes DROP CONSTRAINT "{name}"')  # type: ignore[call-overload]
        c.execute("ALTER TABLE mem.disputes ADD CONSTRAINT disputes_resolved_by_check "
                  "CHECK (resolved_by IS NULL OR resolved_by IN ('owner', 'consolidator', 'expiry'))")
        ins = ("INSERT INTO mem.disputes (org_id, subject_id, predicate, resolved_at, resolved_by) "
               "VALUES (%s, %s, 'contact_phone', now(), 'unmerge')")
        with pytest.raises(pgerr.CheckViolation):
            c.execute(ins, (org, e))
        c.execute(sql)  # type: ignore[call-overload]
        c.execute(ins, (org, e))                                                  # nay được
        c.execute(sql)  # type: ignore[call-overload]
        n = c.execute("SELECT count(*) FROM pg_constraint WHERE conrelid = 'mem.disputes'::regclass AND contype = 'c' "
                      "AND pg_get_constraintdef(oid) LIKE '%expiry%'").fetchone()
        assert n == (1,)
