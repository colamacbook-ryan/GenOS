# ruff: noqa: E501
"""v0.1.76 (gói T1) — migration 0044 «tín hiệu»: chạy lại an toàn (DB trống và DB có cảnh báo cũ nâng từ 0043), backfill theo bảng
alert_type §4B.2, CHECK level/delivery đúng §4.2.1 (c), khoá chống trùng (case_key, kind) chỉ trên vụ đang mở, RLS + quyền gh_app."""

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

from tests.conftest import PG

ROOT = Path(__file__).resolve().parents[3]
API_DIR = ROOT / "apps" / "api"
SQL_0044 = ROOT / "db" / "sql" / "0044_v0176_tin_hieu.sql"
RLS_TABLES = ("biz.signal_links", "biz.signal_feedback", "biz.signal_mutes", "ops.asks", "biz.payment_identifiers",
              "biz.catalog_items", "ops.backup_outbox", "biz.alerts")


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


def _org(c: psycopg.Connection[Any]) -> uuid.UUID:
    return c.execute("INSERT INTO core.organizations (name) VALUES ('Org') RETURNING id").fetchone()[0]  # type: ignore[index, no-any-return]


def _alert(c: psycopg.Connection[Any], org: uuid.UUID, alert_type: str, *, evidence: str = '[{"type":"meaning_unit","id":"x"}]',
           priority: str = "P2", age: str = "1 hour", status: str = "open") -> uuid.UUID:
    return c.execute(  # type: ignore[index, no-any-return]
        """INSERT INTO biz.alerts (org_id, code, alert_type, priority, title, evidence, status, created_at)
           VALUES (%s, 'ALR-' || substr(md5(random()::text), 1, 8), %s, %s, 't', %s::jsonb, %s, now() - %s::interval) RETURNING id""",
        (org, alert_type, priority, evidence, status, age)).fetchone()[0]


def test_migration_0044_follows_0043() -> None:
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    cfg = Config(str(API_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(API_DIR / "migrations"))
    script = ScriptDirectory.from_config(cfg)
    rev = script.get_revision("0044")
    assert rev is not None and rev.down_revision == "0043"
    assert len(script.get_heads()) == 1   # v0.1.77: đầu cây đã lên 0045; chỉ cần còn đúng một head
    assert SQL_0044.is_file()


async def test_migration_0044_rerun_twice_on_empty_db_changes_nothing(fresh_db: str) -> None:
    sql = SQL_0044.read_text(encoding="utf-8")

    def snap(c: psycopg.Connection[Any]) -> Any:
        return (c.execute("""SELECT count(*) FROM information_schema.columns WHERE table_schema IN ('biz', 'ops')""").fetchone(),
                c.execute("SELECT count(*) FROM pg_constraint WHERE conrelid = 'biz.alerts'::regclass").fetchone(),
                c.execute("SELECT count(*) FROM pg_indexes WHERE schemaname IN ('biz', 'ops')").fetchone())

    with _conn(fresh_db) as c:
        before = snap(c)
        c.execute(sql)  # type: ignore[call-overload]
        c.execute(sql)  # type: ignore[call-overload]
        assert snap(c) == before


async def test_migration_0044_upgrade_from_0043_backfills_legacy_alerts_and_rerun_is_stable() -> None:
    name = f"gh_mig44_{uuid.uuid4().hex[:8]}"
    _admin(f"CREATE DATABASE {name}")
    env = {**os.environ, "GH_DATABASE_URL": f"{PG.replace('postgresql://', 'postgresql+asyncpg://')}/{name}", "GH_ADMIN_DATABASE_URL": ""}
    try:
        await _alembic("0043", env)
        with _conn(name) as c:
            org = _org(c)
            cool = _alert(c, org, "customer_cooling")
            people = _alert(c, org, "people_signal", priority="P1")
            noev = _alert(c, org, "repeated_complaint", evidence="[]")
            old = _alert(c, org, "slow_response", age="10 days")
            sysa = _alert(c, org, "model_quota_low", evidence="[]")
            odd = _alert(c, org, "loai_la")
            staff = _alert(c, org, "slow_response")
            c.execute("UPDATE biz.alerts SET personnel_related = true WHERE id = %s", (staff,))
        await _alembic("head", env)
        with _conn(name) as c:
            def row(i: uuid.UUID) -> Any:
                return c.execute("SELECT kind, privacy, impact, urgency, status, evidence_n, pushed_at = created_at, detector, delivery "
                                 "FROM biz.alerts WHERE id = %s", (i,)).fetchone()
            assert row(cool)[:4] == ("quan_he_nong_nguoi", "chat_ok", 2, 1)
            assert row(people)[:2] == ("legacy", "console_pin")               # people_signal: không bao giờ lên Zalo
            assert row(noev)[0] == "legacy"                                    # thiếu bằng chứng ⇒ legacy
            assert row(old)[4] == "expired"                                    # open > 7 ngày ⇒ expired
            assert row(sysa)[0] == "system" and row(sysa)[2] is None
            assert row(odd)[:2] == ("legacy", "console_pin")
            assert row(staff)[:2] == ("cau_hoi_cho_xu_ly", "console_pin")    # liên quan nhân sự ⇒ console_pin (khớp record_legacy)
            assert all(row(i)[6] and row(i)[7] == "backfill_0044" and row(i)[8] == "none" for i in (cool, people, old))
            # chạy lại: không đổi gì, dòng mới (detector khác) không bị backfill đè
            new = c.execute("""INSERT INTO biz.alerts (org_id, code, alert_type, title, evidence, kind, level, detector, evidence_n, created_at)
                               VALUES (%s, 'ALR-NEW', 'intel', 't', '[{"type":"raw_event","id":"r"}]', 'rui_ro_lua_dao', 'red', 'intel', 1,
                                       now() - interval '20 days') RETURNING id""", (org,)).fetchone()[0]  # type: ignore[index]
            before = c.execute("SELECT * FROM biz.alerts ORDER BY id").fetchall()
            c.execute(SQL_0044.read_text(encoding="utf-8"))  # type: ignore[call-overload]
            assert c.execute("SELECT * FROM biz.alerts ORDER BY id").fetchall()[:len(before)] == before
            assert row(new)[4] == "open"
    finally:
        _admin(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)")


async def test_migration_0044_checks_and_case_key_unique_only_while_open(fresh_db: str) -> None:
    with _conn(fresh_db) as c:
        org = _org(c)
        with pytest.raises(pgerr.CheckViolation):
            c.execute("""INSERT INTO biz.alerts (org_id, code, alert_type, title, evidence, kind, level, evidence_n)
                         VALUES (%s, 'A1', 'intel', 't', '[{}]', 'viec_han', 'purple', 1)""", (org,))
        with pytest.raises(pgerr.CheckViolation):
            c.execute("""INSERT INTO biz.alerts (org_id, code, alert_type, title, evidence, kind, delivery, evidence_n)
                         VALUES (%s, 'A2', 'intel', 't', '[{}]', 'viec_han', 'sms', 1)""", (org,))
        with pytest.raises(pgerr.CheckViolation):            # kind tình báo bắt buộc ≥ 1 bằng chứng
            c.execute("""INSERT INTO biz.alerts (org_id, code, alert_type, title, kind)
                         VALUES (%s, 'A3', 'intel', 't', 'viec_han')""", (org,))
        ins = """INSERT INTO biz.alerts (org_id, code, alert_type, title, evidence, kind, evidence_n, case_key, status)
                 VALUES (%s, %s, 'intel', 't', '[{}]', 'xung_dot_khieu_nai', 1, 'case3:c1|x', %s)"""
        c.execute(ins, (org, "B1", "open"))
        with pytest.raises(pgerr.UniqueViolation):
            c.execute(ins, (org, "B2", "open"))
        c.execute("UPDATE biz.alerts SET status = 'resolved' WHERE code = 'B1'")
        c.execute(ins, (org, "B3", "open"))                    # vụ đã đóng ⇒ chê lại là vụ mới


async def test_migration_0044_rls_and_grants(fresh_db: str) -> None:
    with _conn(fresh_db) as c:
        for t in RLS_TABLES:
            assert c.execute("SELECT relrowsecurity FROM pg_class WHERE oid = %s::regclass", (t,)).fetchone() == (True,), t
        assert c.execute("SELECT has_table_privilege('gh_app', 'ops.asks', 'INSERT')").fetchone() == (True,)
        assert c.execute("SELECT has_table_privilege('gh_app', 'biz.signals', 'SELECT')").fetchone() == (True,)
        assert c.execute("SELECT has_table_privilege('gh_app', 'biz.signal_feedback', 'UPDATE')").fetchone() == (False,)
        # VIEW biz.signals chạy với quyền người gọi (RLS của biz.alerts vẫn áp)
        opts = c.execute("SELECT reloptions FROM pg_class WHERE oid = 'biz.signals'::regclass").fetchone()
        assert opts is not None and "security_invoker=true" in (opts[0] or [])
