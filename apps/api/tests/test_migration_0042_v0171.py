"""v0.1.71 (G1 `luoc-do`) — migration 0042: lược đồ «bot tài khoản riêng, kênh quản trị Zalo/WhatsApp, chế độ hỗ trợ».

Kiểm: chạy lại an toàn (hai lần liên tiếp, không đổi dữ liệu), một head = 0042 nối sau 0041, các cột/bảng đúng tên
(tên là hợp đồng giữa các gói G2–G8), ràng buộc CHECK/UNIQUE, RLS cô lập tổ chức dưới role ứng dụng `gh_app`, sổ
`ops.auto_sends` chỉ-thêm (cấm DELETE/TRUNCATE, UPDATE chỉ 4 cột kết quả), mặc định `settings.*` chỉ ghi khi thiếu khoá,
và bản cài cũ (`account_kind` NULL, nhóm cũ không có người thêm) giữ nguyên hành vi v0.1.70.
"""

import json
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
SQL_0042 = ROOT / "db" / "sql" / "0042_v0171_kenh_quan_tri.sql"
NEW_TABLES = ("owner_channels", "group_support", "auto_sends", "owner_outbox", "owner_questions")


def _cols(c: psycopg.Connection[Any], schema: str, table: str) -> dict[str, tuple[str, str]]:
    rows = c.execute(
        """SELECT column_name, data_type, is_nullable FROM information_schema.columns
                        WHERE table_schema = %s AND table_name = %s""",
        (schema, table),
    ).fetchall()
    return {r[0]: (r[1], r[2]) for r in rows}


@contextmanager
def _conn(db_name: str) -> Iterator[psycopg.Connection[Any]]:
    with psycopg.connect(f"{PG}/{db_name}", autocommit=True) as c:
        yield c


def _org(c: psycopg.Connection[Any], name: str = "Org") -> uuid.UUID:
    return c.execute("INSERT INTO core.organizations (name) VALUES (%s) RETURNING id", (name,)).fetchone()[0]  # type: ignore[index, no-any-return]


def _channel(c: psycopg.Connection[Any], org: uuid.UUID, kind: str = "zalo") -> uuid.UUID:
    return c.execute(
        """INSERT INTO core.channels (org_id, type, name) VALUES (%s, %s, %s) RETURNING id""",
        (org, kind, f"{kind} {uuid.uuid4().hex[:6]}"),
    ).fetchone()[0]  # type: ignore[index, no-any-return]


def _session(c: psycopg.Connection[Any], channel: uuid.UUID) -> uuid.UUID:
    return c.execute(
        """INSERT INTO core.channel_sessions (channel_id, account_label, state)
                        VALUES (%s, 'Tài khoản thử', 'active') RETURNING id""",
        (channel,),
    ).fetchone()[0]  # type: ignore[index, no-any-return]


def _group(c: psycopg.Connection[Any], org: uuid.UUID, channel: uuid.UUID, ext: str = "g1") -> uuid.UUID:
    return c.execute(
        """INSERT INTO core.groups (org_id, code, channel_id, external_id, name)
                        VALUES (%s, %s, %s, %s, 'Nhóm thử') RETURNING id""",
        (org, f"GRP-{uuid.uuid4().hex[:6]}", channel, ext),
    ).fetchone()[0]  # type: ignore[index, no-any-return]


def _auto_send(c: psycopg.Connection[Any], org: uuid.UUID, ref: str = "r1", basis: str = "owner_dm") -> uuid.UUID:
    return c.execute(
        """INSERT INTO ops.auto_sends (org_id, basis, channel_type, thread_type, thread_id, trigger_kind,
                                                    trigger_ref, body_sha256, body_len, guard)
                        VALUES (%s, %s, 'zalo', 'user', 'u1', 'owner_message', %s, 'abc', 12, '{}') RETURNING id""",
        (org, basis, ref),
    ).fetchone()[0]  # type: ignore[index, no-any-return]


# ─── cây migration ──────────────────────────────────────────────────────────


def test_migration_0042_follows_0041_and_is_the_single_head() -> None:
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    api_dir = ROOT / "apps" / "api"
    cfg = Config(str(api_dir / "alembic.ini"))
    cfg.set_main_option("script_location", str(api_dir / "migrations"))
    script = ScriptDirectory.from_config(cfg)
    rev = script.get_revision("0042")
    assert rev is not None and rev.down_revision == "0041"
    assert script.get_heads() == ["0042"]
    assert SQL_0042.is_file()


# ─── chạy lại an toàn + lược đồ ─────────────────────────────────────────────


async def test_rerun_twice_is_safe_and_changes_nothing(fresh_db: str) -> None:
    sql = SQL_0042.read_text(encoding="utf-8")
    with _conn(fresh_db) as c:
        org = _org(c)
        ch = _channel(c, org)
        sess = _session(c, ch)
        grp = _group(c, org, ch)
        c.execute(
            "INSERT INTO ops.owner_channels (org_id, channel_type, session_id, owner_external_id) "
            "VALUES (%s, 'zalo', %s, '123')",
            (org, sess),
        )
        c.execute("UPDATE core.channel_sessions SET account_kind = 'bot' WHERE id = %s", (sess,))
        c.execute("UPDATE core.groups SET added_by_kind = 'owner', added_by_external_id = '123' WHERE id = %s", (grp,))
        c.execute(
            'UPDATE core.organizations SET settings = settings || \'{"owner_report": {"min_severity": "urgent"}}\''
        )

        def snap() -> Any:
            return (
                c.execute(
                    "SELECT org_id, channel_type, session_id, owner_external_id, enabled FROM ops.owner_channels"
                ).fetchall(),
                c.execute("SELECT id, account_kind FROM core.channel_sessions").fetchall(),
                c.execute("SELECT id, added_by_kind, added_by_external_id FROM core.groups").fetchall(),
                c.execute("SELECT settings FROM core.organizations").fetchall(),
                c.execute(
                    "SELECT count(*) FROM pg_policies WHERE schemaname = 'ops' AND tablename = ANY(%s)",
                    (list(NEW_TABLES),),
                ).fetchone(),
            )

        c.execute(sql)  # type: ignore[call-overload]       # lần chạy lại đầu: ghi mặc định còn thiếu cho tổ chức tạo sau migrate
        before = snap()
        c.execute(sql)  # type: ignore[call-overload]
        c.execute(sql)  # type: ignore[call-overload]
        assert snap() == before
        assert before[3][0][0]["owner_report"] == {"min_severity": "urgent"}  # khoá Sếp đã đặt không bị đè


async def test_columns_added_with_exact_names(fresh_db: str) -> None:
    with _conn(fresh_db) as c:
        sess = _cols(c, "core", "channel_sessions")
        assert sess["account_kind"] == ("text", "YES")
        groups = _cols(c, "core", "groups")
        assert groups["added_by_external_id"] == ("text", "YES")
        assert groups["added_by_kind"] == ("text", "YES")
        assert groups["added_at"] == ("timestamp with time zone", "YES")


async def test_new_tables_have_contract_columns(fresh_db: str) -> None:
    expect = {
        "owner_channels": {
            "org_id",
            "channel_type",
            "session_id",
            "owner_user_id",
            "owner_external_id",
            "owner_external_alt",
            "owner_thread_id",
            "owner_display",
            "paired_at",
            "enabled",
            "pair_code_hash",
            "pair_expires_at",
            "pair_attempts",
            "pair_user_id",
            "report_prefs",
            "updated_at",
            "updated_by",
        },
        "group_support": {
            "id",
            "org_id",
            "group_id",
            "enabled_at",
            "enabled_by",
            "enabled_via",
            "agent_id",
            "limits",
            "until_at",
            "disabled_at",
            "disabled_by",
            "disabled_reason",
        },
        "auto_sends": {
            "id",
            "org_id",
            "basis",
            "channel_type",
            "session_id",
            "thread_type",
            "thread_id",
            "group_id",
            "trigger_kind",
            "trigger_ref",
            "body_sha256",
            "body_len",
            "guard",
            "status",
            "error",
            "created_at",
            "sent_at",
            "external_msg_id",
        },
        "owner_outbox": {
            "id",
            "org_id",
            "channel_type",
            "kind",
            "severity",
            "dedupe_key",
            "text",
            "buttons",
            "created_at",
            "not_before",
            "next_attempt_at",
            "attempts",
            "sent_at",
            "failed_code",
        },
        "owner_questions": {
            "id",
            "org_id",
            "short_code",
            "kind",
            "subject_id",
            "payload",
            "created_at",
            "expires_at",
            "answered_at",
            "answer",
        },
    }
    with _conn(fresh_db) as c:
        for table, names in expect.items():
            assert set(_cols(c, "ops", table)) == names, table


# ─── bản cài cũ = hành vi v0.1.70 ───────────────────────────────────────────


async def test_old_install_defaults_are_null_and_features_off(fresh_db: str) -> None:
    with _conn(fresh_db) as c:
        org = _org(c)
        ch = _channel(c, org)
        sess = _session(c, ch)
        grp = _group(c, org, ch)
        assert c.execute("SELECT account_kind FROM core.channel_sessions WHERE id = %s", (sess,)).fetchone() == (None,)
        assert c.execute(
            "SELECT added_by_external_id, added_by_kind, added_at FROM core.groups WHERE id = %s", (grp,)
        ).fetchone() == (None, None, None)
        # Chạy lại migration trên dữ liệu cũ KHÔNG backfill gì.
        c.execute(SQL_0042.read_text(encoding="utf-8"))  # type: ignore[call-overload]
        assert c.execute("SELECT account_kind FROM core.channel_sessions WHERE id = %s", (sess,)).fetchone() == (None,)
        assert c.execute("SELECT added_by_kind FROM core.groups WHERE id = %s", (grp,)).fetchone() == (None,)
        for t in NEW_TABLES:
            assert c.execute(f"SELECT count(*) FROM ops.{t}").fetchone() == (0,)  # type: ignore[call-overload]


async def test_account_kind_and_added_by_kind_checks(fresh_db: str) -> None:
    with _conn(fresh_db) as c:
        org = _org(c)
        ch = _channel(c, org)
        sess = _session(c, ch)
        grp = _group(c, org, ch)
        for ok in ("owner_self", "bot", None):
            c.execute("UPDATE core.channel_sessions SET account_kind = %s WHERE id = %s", (ok, sess))
        with pytest.raises(pgerr.CheckViolation):
            c.execute("UPDATE core.channel_sessions SET account_kind = 'khac' WHERE id = %s", (sess,))
        for ok in ("owner", "other", "unknown", None):
            c.execute("UPDATE core.groups SET added_by_kind = %s WHERE id = %s", (ok, grp))
        with pytest.raises(pgerr.CheckViolation):
            c.execute("UPDATE core.groups SET added_by_kind = 'khac' WHERE id = %s", (grp,))


# ─── ràng buộc từng bảng ────────────────────────────────────────────────────


async def test_owner_channels_constraints(fresh_db: str) -> None:
    with _conn(fresh_db) as c:
        org = _org(c)
        c.execute("INSERT INTO ops.owner_channels (org_id, channel_type) VALUES (%s, 'zalo')", (org,))
        row = c.execute("SELECT enabled, pair_attempts, report_prefs FROM ops.owner_channels").fetchone()
        assert row == (False, 0, {})  # mặc định: chưa bật, chưa ghép
        with pytest.raises(pgerr.UniqueViolation):  # một dòng / (tổ chức, kênh)
            c.execute("INSERT INTO ops.owner_channels (org_id, channel_type) VALUES (%s, 'zalo')", (org,))
        with pytest.raises(pgerr.CheckViolation):  # chỉ zalo | whatsapp
            c.execute("INSERT INTO ops.owner_channels (org_id, channel_type) VALUES (%s, 'telegram')", (org,))
        with pytest.raises(pgerr.CheckViolation):  # ID nền tảng ≤ 64 ký tự
            c.execute(
                "INSERT INTO ops.owner_channels (org_id, channel_type, owner_external_id) VALUES (%s, 'whatsapp', %s)",
                (org, "9" * 65),
            )
        c.execute(
            "INSERT INTO ops.owner_channels (org_id, channel_type, owner_external_id) VALUES (%s, 'whatsapp', %s)",
            (org, "9" * 64),
        )


async def test_group_support_one_active_row_per_group_and_history_kept(fresh_db: str) -> None:
    with _conn(fresh_db) as c:
        org = _org(c)
        ch = _channel(c, org)
        g1, g2 = _group(c, org, ch, "g1"), _group(c, org, ch, "g2")
        ins = "INSERT INTO ops.group_support (org_id, group_id, enabled_at, enabled_via) VALUES (%s, %s, now(), %s)"
        c.execute(ins, (org, g1, "zalo"))
        c.execute(ins, (org, g2, "console"))
        with pytest.raises(pgerr.UniqueViolation):  # tối đa một lượt đang bật / nhóm
            c.execute(ins, (org, g1, "whatsapp"))
        c.execute(
            "UPDATE ops.group_support SET disabled_at = now(), disabled_reason = 'owner' WHERE group_id = %s", (g1,)
        )
        c.execute(ins, (org, g1, "zalo"))  # tắt rồi bật lại = lượt mới, lịch sử còn
        assert c.execute("SELECT count(*) FROM ops.group_support WHERE group_id = %s", (g1,)).fetchone() == (2,)
        assert c.execute("SELECT limits FROM ops.group_support LIMIT 1").fetchone() == ({},)
        with pytest.raises(pgerr.CheckViolation):
            c.execute(ins, (org, _group(c, org, ch, "g3"), "telegram"))
        with pytest.raises(pgerr.CheckViolation):
            c.execute(
                "UPDATE ops.group_support SET disabled_at = now(), disabled_reason = 'la' "
                "WHERE group_id = %s AND disabled_at IS NULL",
                (g1,),
            )
        for reason in (
            "owner",
            "bot_removed",
            "group_left",
            "session_changed",
            "account_kind_changed",
            "abuse_guard",
            "expired",
        ):
            gx = _group(c, org, ch, f"x-{reason}")
            c.execute(ins, (org, gx, "console"))
            c.execute(
                "UPDATE ops.group_support SET disabled_at = now(), disabled_reason = %s WHERE group_id = %s",
                (reason, gx),
            )


async def test_auto_sends_unique_trigger_and_checks(fresh_db: str) -> None:
    with _conn(fresh_db) as c:
        org = _org(c)
        _auto_send(c, org, "r1", "owner_dm")
        with pytest.raises(pgerr.UniqueViolation):  # mỗi tin kích hoạt chỉ một trả lời / basis
            _auto_send(c, org, "r1", "owner_dm")
        _auto_send(c, org, "r1", "owner_tag")  # basis khác cùng ref: được
        with pytest.raises(pgerr.CheckViolation):
            _auto_send(c, org, "r2", "draft")  # chỉ 3 basis
        assert c.execute(
            "SELECT status FROM ops.auto_sends WHERE trigger_ref = 'r1' AND basis = 'owner_dm'"
        ).fetchone() == ("issued",)
        with pytest.raises(pgerr.CheckViolation):
            c.execute("UPDATE ops.auto_sends SET status = 'khac'")


async def test_auto_sends_is_append_only_except_result_columns(fresh_db: str) -> None:
    with _conn(fresh_db) as c:
        org = _org(c)
        sid = _auto_send(c, org)
        c.execute(
            """UPDATE ops.auto_sends SET status = 'sent', error = NULL, sent_at = now(), external_msg_id = 'm1'
                     WHERE id = %s""",
            (sid,),
        )  # 4 cột kết quả: được đổi
        for col, val in (
            ("body_sha256", "'x'"),
            ("thread_id", "'u2'"),
            ("basis", "'support'"),
            ("trigger_ref", "'r9'"),
            ("body_len", "1"),
            ("guard", "'{\"a\":1}'"),
            ("created_at", "now() - interval '1 day'"),
            ("org_id", "gen_random_uuid()"),
        ):
            with pytest.raises(psycopg.Error, match="append-only|foreign key"):
                c.execute(f"UPDATE ops.auto_sends SET {col} = {val} WHERE id = %s", (sid,))  # type: ignore[call-overload]
        with pytest.raises(psycopg.Error, match="append-only"):
            c.execute("DELETE FROM ops.auto_sends WHERE id = %s", (sid,))
        with pytest.raises(psycopg.Error, match="append-only"):
            c.execute("TRUNCATE ops.auto_sends")
        assert c.execute("SELECT count(*), min(status) FROM ops.auto_sends").fetchone() == (1, "sent")


async def test_owner_outbox_dedupe_and_checks(fresh_db: str) -> None:
    with _conn(fresh_db) as c:
        org = _org(c)
        ins = """INSERT INTO ops.owner_outbox (org_id, channel_type, kind, severity, dedupe_key, text)
                 VALUES (%s, %s, %s, %s, %s, 'Nội dung')"""
        c.execute(ins, (org, "zalo", "alert", "important", "k1"))
        row = c.execute("SELECT buttons, attempts, sent_at, failed_code FROM ops.owner_outbox").fetchone()
        assert row == ([], 0, None, None)
        with pytest.raises(pgerr.UniqueViolation):
            c.execute(ins, (org, "zalo", "alert", "important", "k1"))
        c.execute(ins, (org, "whatsapp", "alert", "important", "k1"))  # khác kênh: được
        for kind in ("ask", "alert", "digest", "reply"):
            c.execute(ins, (org, "zalo", kind, "info", f"k-{kind}"))
        with pytest.raises(pgerr.CheckViolation):
            c.execute(ins, (org, "zalo", "khac", "info", "kx"))
        with pytest.raises(pgerr.CheckViolation):
            c.execute(ins, (org, "zalo", "ask", "cao", "ky"))
        with pytest.raises(pgerr.CheckViolation):
            c.execute(ins, (org, "telegram", "ask", "info", "kz"))


async def test_owner_questions_short_code_alphabet_and_open_unique(fresh_db: str) -> None:
    with _conn(fresh_db) as c:
        org = _org(c)
        ins = """INSERT INTO ops.owner_questions (org_id, short_code, kind, subject_id, expires_at)
                 VALUES (%s, %s, %s, gen_random_uuid(), now() + interval '1 day')"""
        c.execute(ins, (org, "K7Q2", "group_added"))
        with pytest.raises(pgerr.UniqueViolation):  # mã đang mở không trùng
            c.execute(ins, (org, "K7Q2", "listen_confirm"))
        c.execute("UPDATE ops.owner_questions SET answered_at = now(), answer = 'nghe' WHERE short_code = 'K7Q2'")
        c.execute(ins, (org, "K7Q2", "support_confirm"))  # đã trả lời ⇒ mã dùng lại được
        for bad in ("K7Q", "K7Q22", "I7Q2", "O7Q2", "0ABC", "1ABC", "k7q2", "K7-2"):
            with pytest.raises(pgerr.CheckViolation):
                c.execute(ins, (org, bad, "group_added"))
        with pytest.raises(pgerr.CheckViolation):
            c.execute(ins, (org, "ZZ99", "khac"))


# ─── RLS + quyền ────────────────────────────────────────────────────────────


async def test_rls_enabled_policy_and_gh_app_grants(fresh_db: str) -> None:
    with _conn(fresh_db) as c:
        for t in NEW_TABLES:
            assert c.execute(
                "SELECT relrowsecurity FROM pg_class WHERE oid = %s::regclass", (f"ops.{t}",)
            ).fetchone() == (True,), t
            pol = c.execute(
                "SELECT count(*) FROM pg_policies "
                "WHERE schemaname = 'ops' AND tablename = %s AND policyname = 'org_isolation'",
                (t,),
            ).fetchone()
            assert pol == (1,), t

        def can(priv: str, t: str) -> bool:
            return bool(c.execute("SELECT has_table_privilege('gh_app', %s, %s)", (f"ops.{t}", priv)).fetchone()[0])  # type: ignore[index]

        for t in NEW_TABLES:
            assert can("SELECT", t) and can("INSERT", t) and can("UPDATE", t), t
        for t in NEW_TABLES:
            assert can("DELETE", t) == (t != "auto_sends"), t  # sổ tự gửi: không cấp DELETE
        assert not any(can("TRUNCATE", t) for t in NEW_TABLES)
        for col in ("account_kind",):
            assert c.execute(
                "SELECT has_column_privilege('gh_app', 'core.channel_sessions', %s, 'UPDATE')", (col,)
            ).fetchone() == (True,)


async def test_rls_isolates_orgs_under_app_role(fresh_db: str) -> None:
    with _conn(fresh_db) as c:
        a, b = _org(c, "A"), _org(c, "B")
        chs = {o: _channel(c, o) for o in (a, b)}
        for o in (a, b):
            c.execute(
                "INSERT INTO ops.owner_channels (org_id, channel_type, owner_display) VALUES (%s, 'zalo', %s)",
                (o, f"org-{o}"),
            )
            c.execute(
                """INSERT INTO ops.owner_outbox (org_id, channel_type, kind, severity, dedupe_key, text)
                         VALUES (%s, 'zalo', 'alert', 'important', 'k', 'x')""",
                (o,),
            )
            c.execute(
                "INSERT INTO ops.owner_questions (org_id, short_code, kind, expires_at) "
                "VALUES (%s, 'AB23', 'group_added', now() + interval '1 day')",
                (o,),
            )
            c.execute(
                "INSERT INTO ops.group_support (org_id, group_id, enabled_at, enabled_via) "
                "VALUES (%s, %s, now(), 'console')",
                (o, _group(c, o, chs[o])),
            )
            _auto_send(c, o)
        c.execute("SET ROLE gh_app")
        try:
            c.execute("SELECT set_config('app.org_id', %s, false)", (str(a),))
            for t in NEW_TABLES:
                orgs = {r[0] for r in c.execute(f"SELECT org_id FROM ops.{t}").fetchall()}  # type: ignore[call-overload]
                assert orgs == {a}, t
            with pytest.raises(pgerr.InsufficientPrivilege):  # ghi sang tổ chức khác bị RLS chặn
                c.execute("INSERT INTO ops.owner_channels (org_id, channel_type) VALUES (%s, 'whatsapp')", (b,))
            with pytest.raises(psycopg.Error, match="append-only|permission denied"):
                c.execute("DELETE FROM ops.auto_sends")
            c.execute("INSERT INTO ops.owner_channels (org_id, channel_type) VALUES (%s, 'whatsapp')", (a,))
            c.execute("UPDATE ops.auto_sends SET status = 'sent'")  # gh_app đổi được cột kết quả
            assert c.execute("SELECT count(*) FROM ops.auto_sends WHERE status = 'sent'").fetchone() == (1,)
        finally:
            c.execute("RESET ROLE")


# ─── mặc định settings ──────────────────────────────────────────────────────


async def test_settings_defaults_written_only_when_missing(fresh_db: str) -> None:
    sql = SQL_0042.read_text(encoding="utf-8")
    with _conn(fresh_db) as c:
        fresh, custom = _org(c, "Mới"), _org(c, "Đã chỉnh")
        c.execute(
            """UPDATE core.organizations SET settings = '{"owner_report": {"min_severity": "urgent"},
                                                                 "support_limits": {"per_group_hour": 3}, "khac": 1}'
                     WHERE id = %s""",
            (custom,),
        )
        c.execute(sql)  # type: ignore[call-overload]
        c.execute(sql)  # type: ignore[call-overload]
        s_new = c.execute("SELECT settings FROM core.organizations WHERE id = %s", (fresh,)).fetchone()[0]  # type: ignore[index]
        s_old = c.execute("SELECT settings FROM core.organizations WHERE id = %s", (custom,)).fetchone()[0]  # type: ignore[index]
        # Quyết định Sếp 18/10: không báo cáo định kỳ/gộp — chỉ sự kiện từ mức «quan trọng».
        assert s_new["owner_report"]["min_severity"] == "important"
        assert s_new["owner_report"]["batch_minutes"] == 0 and s_new["owner_report"]["digest"] is False
        assert (s_new["owner_report"]["quiet_start"], s_new["owner_report"]["quiet_end"]) == (21, 7)
        assert s_new["support_limits"] == {
            "per_group_hour": 20,
            "per_member_hour": 5,
            "min_gap_s": 20,
            "per_channel_day": 150,
            "max_len": 600,
            "max_age_s": 300,
        }
        assert s_old["owner_report"] == {"min_severity": "urgent"}  # giá trị Sếp đã đặt không bị đè
        assert s_old["support_limits"] == {"per_group_hour": 3} and s_old["khac"] == 1
        json.dumps(s_new)  # là JSON hợp lệ
