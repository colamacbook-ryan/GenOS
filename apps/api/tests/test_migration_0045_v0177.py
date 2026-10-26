# ruff: noqa: E501
"""v0.1.77 (gói P2) — migration 0045 «ưu tiên»: nâng từ 0044 có dữ liệu, chạy lại an toàn, `signal_feedback.reason/rule_version`,
settings `intel.work_start/work_end/ramp_started_at` (jsonb_set chỉ khi chưa có) và đặt lại `ramp_gates` đúng một lần (spec r7.2 §4.2.1 (h))."""

import asyncio
import json
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
SQL_0045 = ROOT / "db" / "sql" / "0045_v0177_uu_tien.sql"


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


def _org(c: psycopg.Connection[Any], settings: dict[str, Any] | None = None) -> uuid.UUID:
    return c.execute("INSERT INTO core.organizations (name, settings) VALUES ('Org', %s::jsonb) RETURNING id",
                     (json.dumps(settings or {}),)).fetchone()[0]  # type: ignore[index, no-any-return]


def _intel(c: psycopg.Connection[Any], org: uuid.UUID) -> dict[str, Any]:
    return c.execute("SELECT settings -> 'intel' FROM core.organizations WHERE id = %s", (org,)).fetchone()[0]  # type: ignore[index, no-any-return]


def _feedback(c: psycopg.Connection[Any], org: uuid.UUID, alert: uuid.UUID, **extra: Any) -> uuid.UUID:
    cols = ", ".join(["org_id", "signal_id", "verdict", "verdict_by", *extra])
    ph = ", ".join(["%s"] * (4 + len(extra)))
    return c.execute(f"INSERT INTO biz.signal_feedback ({cols}) VALUES ({ph}) RETURNING id",  # noqa: S608
                     (org, alert, "wrong", "owner", *extra.values())).fetchone()[0]  # type: ignore[index, no-any-return]


def _alert(c: psycopg.Connection[Any], org: uuid.UUID) -> uuid.UUID:
    return c.execute("""INSERT INTO biz.alerts (org_id, code, alert_type, priority, title, evidence, status)
                        VALUES (%s, 'ALR-' || substr(md5(random()::text), 1, 8), 'slow_response', 'P2', 't', '[]'::jsonb, 'open') RETURNING id""",
                     (org,)).fetchone()[0]  # type: ignore[index, no-any-return]


def test_migration_0045_follows_0044() -> None:
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    cfg = Config(str(API_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(API_DIR / "migrations"))
    script = ScriptDirectory.from_config(cfg)
    rev = script.get_revision("0045")
    assert rev is not None and rev.down_revision == "0044"
    assert script.get_heads() == ["0045"]
    assert SQL_0045.is_file()


async def test_migration_0045_upgrade_from_0044_then_rerun_twice_changes_nothing() -> None:
    name = f"gh_mig45_{uuid.uuid4().hex[:8]}"
    _admin(f"CREATE DATABASE {name}")
    env = {**os.environ, "GH_DATABASE_URL": f"{PG.replace('postgresql://', 'postgresql+asyncpg://')}/{name}", "GH_ADMIN_DATABASE_URL": ""}
    try:
        await _alembic("0044", env)
        with _conn(name) as c:
            bare = _org(c)                                                          # settings rỗng
            custom = _org(c, {"intel": {"work_start": "07:00", "mode": "live", "ramp_gates": "push"}, "other": 1})
            odd = _org(c, {"intel": "hỏng"})                                        # `intel` không phải object ⇒ thay bằng object
            old_alert = _alert(c, bare)
            old_fb = _feedback(c, bare, old_alert)                                  # trước migrate
        await _alembic("head", env)
        with _conn(name) as c:
            b, cu, od = _intel(c, bare), _intel(c, custom), _intel(c, odd)
            for s in (b, od):
                assert (s["work_start"], s["work_end"], s["ramp_gates"]) == ("08:00", "17:30", "digest_top")
                assert s["ramp_started_at"] and s["ramp_reset_at"]
            assert cu["work_start"] == "07:00" and cu["work_end"] == "17:30"      # khoá đã có KHÔNG bị ghi đè
            assert cu["mode"] == "live" and cu["ramp_gates"] == "digest_top"      # đặt lại một lần; khoá khác giữ
            assert c.execute("SELECT settings ->> 'other' FROM core.organizations WHERE id = %s", (custom,)).fetchone()[0] == "1"  # type: ignore[index]
            assert c.execute("SELECT rule_version, reason FROM biz.signal_feedback WHERE id = %s", (old_fb,)).fetchone() == ("r6", None)
            new_fb = _feedback(c, bare, old_alert)
            assert c.execute("SELECT rule_version FROM biz.signal_feedback WHERE id = %s", (new_fb,)).fetchone()[0] == "r7"  # type: ignore[index]
            # Sếp chọn push + đổi giờ làm sau migrate; chạy lại migration không đặt lại lần hai
            c.execute("UPDATE core.organizations SET settings = jsonb_set(jsonb_set(settings, '{intel,ramp_gates}', '\"push\"'), "
                      "'{intel,work_end}', '\"18:00\"') WHERE id = %s", (bare,))
            before = c.execute("SELECT id, settings FROM core.organizations ORDER BY id").fetchall()
            cols = c.execute("SELECT count(*) FROM information_schema.columns WHERE table_schema = 'biz'").fetchone()
            fb = c.execute("SELECT * FROM biz.signal_feedback ORDER BY id").fetchall()
            sql = SQL_0045.read_text(encoding="utf-8")
            c.execute(sql)  # type: ignore[call-overload]
            c.execute(sql)  # type: ignore[call-overload]
            assert c.execute("SELECT id, settings FROM core.organizations ORDER BY id").fetchall() == before
            assert c.execute("SELECT count(*) FROM information_schema.columns WHERE table_schema = 'biz'").fetchone() == cols
            assert c.execute("SELECT * FROM biz.signal_feedback ORDER BY id").fetchall() == fb
            assert _intel(c, bare)["ramp_gates"] == "push" and _intel(c, bare)["work_end"] == "18:00"
    finally:
        _admin(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)")


async def test_migration_0045_reason_check_and_append_only_kept(fresh_db: str) -> None:
    with _conn(fresh_db) as c:
        org = _org(c)
        a = _alert(c, org)
        for r in ("still_buying", "not_urgent", "staff_handled", None):
            _feedback(c, org, a, reason=r)
        with pytest.raises(pgerr.CheckViolation):
            _feedback(c, org, a, reason="khac")
        fb = _feedback(c, org, a)
        with pytest.raises(pgerr.RaiseException):                                   # bảng vẫn chỉ-thêm
            c.execute("UPDATE biz.signal_feedback SET reason = 'not_urgent' WHERE id = %s", (fb,))
