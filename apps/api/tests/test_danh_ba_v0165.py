"""v0.1.65 (F1a) — chế độ nghe & danh bạ: nhóm mới CHỜ Sếp chọn (`pending`, chưa lưu tin, vẫn thu thành viên), nhóm cũ
giữ nguyên chế độ, `group.discovered`, thành viên rời (`left_at`) + nhóm đã rời + vai trò admin, tin 1-1 mặc định im
lặng
(lưu, không tự trả lời), migration 0038 chạy lại an toàn (bản cài cũ: nhóm 'off' vẫn 'off')."""

from pathlib import Path
from typing import Any

import psycopg
import pytest
from sqlalchemy import text

from gh.data.common import DEFAULT_DIRECT_MODE, direct_mode
from gh.data.ingest import handle_directory, handle_group_discovered, sync_listen_sets
from gh.db import sessionmaker
from tests.conftest import PG
from tests.phase2 import listen, msg, org_id, put

ROOT = Path(__file__).resolve().parents[3]
SQL_0038 = ROOT / "db" / "sql" / "0038_v0165_che_do_nghe.sql"


def grp(ext: str, *members: dict[str, Any], count: int | None = None, complete: bool | None = None) -> dict[str, Any]:
    g: dict[str, Any] = {"external_id": ext, "name": f"Nhóm {ext}", "member_count": count or len(members),
                         "members": list(members)}
    if complete is not None:
        g["members_complete"] = complete
    return g


def mem(ext: str, name: str = "", role: str | None = None, phone: str | None = None) -> dict[str, Any]:
    m: dict[str, Any] = {"external_id": ext, "name": name or f"Người {ext}"}
    if role:
        m["role"] = role
    if phone:
        m["phone"] = phone
    return m


async def directory(db: Any, redis: Any, org: Any, *groups: dict[str, Any], complete: bool | None = None,
                    channel: str = "zalo") -> None:
    p: dict[str, Any] = {"channel": channel, "groups": list(groups)}
    if complete is not None:
        p["groups_complete"] = complete
    await handle_directory(db, redis, org, p)
    await db.commit()


async def members_of(db: Any, ext: str) -> dict[str, Any]:
    rows = (await db.execute(text("""SELECT pi.external_id, gm.role, gm.left_at FROM core.group_members gm
                                     JOIN core.groups g ON g.id = gm.group_id
                                     JOIN core.person_identities pi ON pi.person_id = gm.person_id
                                     WHERE g.external_id = :x"""), {"x": ext})).all()
    return {r.external_id: (r.role, r.left_at is not None) for r in rows}


async def mode_of(db: Any, ext: str) -> str:
    row = await db.execute(text("SELECT listen_mode FROM core.groups WHERE external_id = :x"), {"x": ext})
    return row.scalar_one()


# ─── nhóm mới: CHỜ chọn, chưa lưu tin, vẫn thu thành viên ────────────────────────────────────────────────────

async def _ask(db: Any) -> None:
    """Sếp đặt «Nhóm mới: Hỏi Sếp» (mặc định từ QD 17/10 là silent) — nhóm mới mới vào `pending`."""
    await db.execute(text("""UPDATE core.organizations SET settings = settings
                             || '{"new_group_mode": {"zalo": "ask"}}'::jsonb"""))
    await db.commit()


async def test_new_group_from_directory_is_silent_by_default_and_collects_members(app, db, redis) -> None:  # type: ignore[no-untyped-def]
    """QD 17/10: nhóm mới mặc định «Lắng nghe im lặng» — vào tập nghe ngay, lưu tin, vẫn thu thành viên."""
    org = await org_id(db)
    await directory(db, redis, org, grp("gN", mem("u1", phone="0901234567"), mem("u2")))
    assert await mode_of(db, "gN") == "silent"
    assert set(await members_of(db, "gN")) == {"u1", "u2"}
    assert await redis.smembers("gh:bridge:listen:zalo") == {b"gN"}
    [eid] = await put(sessionmaker(), org, msg("Còn hàng không chị?", group="gN"))
    assert eid is not None
    assert (await db.execute(text("SELECT count(*) FROM raw.events"))).scalar() == 1


async def test_new_group_from_directory_with_ask_is_pending_and_collects_members(app, db, redis) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    await _ask(db)
    await directory(db, redis, org, grp("gN", mem("u1", phone="0901234567"), mem("u2")))
    assert await mode_of(db, "gN") == "pending"
    assert set(await members_of(db, "gN")) == {"u1", "u2"}        # Q5=2: thu thành viên dù chưa chọn
    # chưa vào tập nghe ⇒ bridge bỏ, API cũng bỏ: không lưu tin nào
    assert await redis.smembers("gh:bridge:listen:zalo") == set()
    [eid] = await put(sessionmaker(), org, msg("Còn hàng không chị?", group="gN"))
    assert eid is None
    assert (await db.execute(text("SELECT count(*) FROM raw.events"))).scalar() == 0


async def test_existing_groups_keep_their_mode_on_resync(app, db, redis) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    await directory(db, redis, org, grp("gOld"), grp("gSil"), grp("gTag"))
    for ext, mode in (("gOld", "off"), ("gSil", "silent"), ("gTag", "tagged_only")):
        await db.execute(text("UPDATE core.groups SET listen_mode = :m WHERE external_id = :x"), {"m": mode, "x": ext})
    await db.commit()
    await directory(db, redis, org, grp("gOld", mem("u1")), grp("gSil"), grp("gTag"), grp("gNew"))
    got = [await mode_of(db, x) for x in ("gOld", "gSil", "gTag", "gNew")]
    assert got == ["off", "silent", "tagged_only", "silent"]        # nhóm cũ "off" giữ nguyên; chỉ nhóm MỚI vào silent
    # nhóm "Không nghe" vẫn thu thành viên (Q5=2) nhưng KHÔNG vào tập nghe
    assert set(await members_of(db, "gOld")) == {"u1"}
    assert await redis.smembers("gh:bridge:listen:zalo") == {b"gSil", b"gTag", b"gNew"}


async def test_group_discovered_creates_silent_once_and_never_changes_mode(app, db, redis) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    await _ask(db)                       # với «Hỏi Sếp» nhóm vừa phát hiện vào pending (mặc định thì vào silent)
    row, fresh = await handle_group_discovered(db, redis, org, {"channel": "zalo", "group_id": "gD",
                                                                "name": "Khách mới", "member_count": 12})
    await db.commit()
    assert fresh is True and row.listen_mode == "pending"
    g = (await db.execute(text("""SELECT name, member_count, listen_mode FROM core.groups
                                  WHERE external_id = 'gD'"""))).one()
    assert (g.name, g.member_count, g.listen_mode) == ("Khách mới", 12, "pending")
    await db.execute(text("UPDATE core.groups SET listen_mode = 'silent' WHERE external_id = 'gD'"))
    await db.commit()
    row2, fresh2 = await handle_group_discovered(db, redis, org,
                                                 {"channel": "zalo", "group_id": "gD", "name": "Khách mới"})
    await db.commit()
    assert fresh2 is False and row2.listen_mode == "silent"
    assert await handle_group_discovered(db, redis, org, {"channel": "zalo", "group_id": ""}) is None
    assert await handle_group_discovered(db, redis, org, {"channel": "khong-co", "group_id": "x"}) is None


# ─── thành viên rời, nhóm đã rời, vai trò admin ─────────────────────────────────────────────────────────────

async def test_absent_members_get_left_at_only_when_list_is_complete(app, db, redis) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    await directory(db, redis, org, grp("g1", mem("a"), mem("b"), mem("c"), complete=True))
    assert {k: v[1] for k, v in (await members_of(db, "g1")).items()} == {"a": False, "b": False, "c": False}
    # danh sách ĐỦ, vắng c ⇒ c rời
    await directory(db, redis, org, grp("g1", mem("a"), mem("b"), complete=True))
    assert {k: v[1] for k, v in (await members_of(db, "g1")).items()} == {"a": False, "b": False, "c": True}
    # danh sách KHÔNG đủ (bridge không chắc) ⇒ không ai bị đánh dấu rời thêm
    await directory(db, redis, org, grp("g1", mem("a"), complete=False))
    await directory(db, redis, org, grp("g1", mem("a")))
    assert {k: v[1] for k, v in (await members_of(db, "g1")).items()} == {"a": False, "b": False, "c": True}
    # danh sách rỗng (nền tảng không cho lấy) ⇒ không đánh dấu ai rời
    await directory(db, redis, org, grp("g1", complete=True))
    assert {k: v[1] for k, v in (await members_of(db, "g1")).items()} == {"a": False, "b": False, "c": True}
    # c quay lại ⇒ xoá left_at
    await directory(db, redis, org, grp("g1", mem("a"), mem("b"), mem("c"), complete=True))
    assert {k: v[1] for k, v in (await members_of(db, "g1")).items()} == {"a": False, "b": False, "c": False}


async def test_absent_group_is_flagged_left_and_not_counted_listening(app, db, redis) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    await directory(db, redis, org, grp("gKeep"), grp("gGone"))
    await db.execute(text("UPDATE core.groups SET listen_mode = 'silent'"))
    await db.commit()
    await directory(db, redis, org, grp("gKeep"), complete=False)           # không đủ ⇒ không đánh dấu
    left = (await db.execute(text("SELECT external_id FROM core.groups WHERE left_at IS NOT NULL"))).scalars().all()
    assert left == []
    await directory(db, redis, org, grp("gKeep"), complete=True)
    left = (await db.execute(text("SELECT external_id FROM core.groups WHERE left_at IS NOT NULL"))).scalars().all()
    assert left == ["gGone"]
    assert await mode_of(db, "gGone") == "silent"                           # chế độ của Sếp không bị đổi
    await directory(db, redis, org, grp("gKeep"), grp("gGone"), complete=True)   # vào lại nhóm ⇒ hết cờ
    assert (await db.execute(text("SELECT count(*) FROM core.groups WHERE left_at IS NOT NULL"))).scalar() == 0
    # danh sách nhóm rỗng không được coi là "rời hết"
    await directory(db, redis, org, complete=True)
    assert (await db.execute(text("SELECT count(*) FROM core.groups WHERE left_at IS NOT NULL"))).scalar() == 0


async def test_admin_role_is_stored_and_kept_when_platform_does_not_say(app, db, redis) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    await directory(db, redis, org, grp("g1", mem("boss", role="admin"), mem("m1", role="member"), mem("m2")))
    got = await members_of(db, "g1")
    assert (got["boss"][0], got["m1"][0], got["m2"][0]) == ("admin", "member", None)
    await directory(db, redis, org, grp("g1", mem("boss"), mem("m1"), mem("m2")))       # nền tảng không nói vai trò
    assert (await members_of(db, "g1"))["boss"][0] == "admin"
    await directory(db, redis, org, grp("g1", mem("boss", role="member")))               # bị hạ quyền
    assert (await members_of(db, "g1"))["boss"][0] == "member"
    # tin nhắn của một thành viên không làm mất vai trò đã biết
    await directory(db, redis, org, grp("g1", mem("boss", role="admin")))
    await db.execute(text("UPDATE core.groups SET listen_mode = 'silent'"))
    await db.commit()
    await put(sessionmaker(), org, msg("xin chào", group="g1", sender="boss"))
    assert (await members_of(db, "g1"))["boss"][0] == "admin"


# ─── tin 1-1: mặc định lưu im lặng ─────────────────────────────────────────────────────────────────────────

def test_direct_mode_resolution_order() -> None:
    assert DEFAULT_DIRECT_MODE == "silent"
    assert direct_mode({}, "zalo") == "silent"
    assert direct_mode(None, "zalo") == "silent"
    assert direct_mode({"listen_direct": {"zalo": False}}, "zalo") == "silent"      # bản cũ đang tắt ⇒ im lặng
    assert direct_mode({"listen_direct": {"zalo": True}}, "zalo") == "reply"        # bản cũ đang bật ⇒ giữ trả lời
    assert direct_mode({"listen_direct": {"zalo": True}, "direct_mode": {"zalo": "off"}}, "zalo") == "off"
    assert direct_mode({"direct_mode": {"zalo": "bậy"}}, "zalo") == "silent"
    assert direct_mode({"direct_mode": {"whatsapp": "off"}}, "zalo") == "silent"    # theo từng kênh


async def test_direct_messages_stored_silently_by_default(app, db, redis) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    await sync_listen_sets(db, redis, org)
    assert await redis.get("gh:bridge:listen_direct:zalo") == b"1"
    [eid] = await put(sessionmaker(), org, msg("Chào anh, em hỏi giá", group=None, sender="khach1"))
    assert eid is not None
    row = (await db.execute(text("""SELECT e.group_id, e.direction, s.fast FROM raw.events e
                                    JOIN refinery.event_state s ON s.event_id = e.id"""))).one()
    assert row.group_id is None and row.direction == "inbound" and row.fast is False
    await db.execute(text("""UPDATE core.organizations
                             SET settings = settings || '{"direct_mode": {"zalo": "off"}}'"""))
    await db.commit()
    await sync_listen_sets(db, redis, org)
    assert await redis.get("gh:bridge:listen_direct:zalo") == b"0"
    assert await redis.get("gh:bridge:listen_direct:whatsapp") == b"1"             # theo từng kênh


# ─── migration 0038 ────────────────────────────────────────────────────────────────────────────────────────────

async def test_migration_0038_keeps_old_groups_and_maps_direct_mode_rerunnable(app, db, redis, fresh_db: str) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    await directory(db, redis, org, grp("gOff"), grp("gSil"))
    await db.execute(text("UPDATE core.groups SET listen_mode = 'off' WHERE external_id = 'gOff'"))
    await db.execute(text("UPDATE core.groups SET listen_mode = 'silent' WHERE external_id = 'gSil'"))
    # dựng lại trạng thái bản ≤ 0.1.64: chỉ có `listen_direct` (zalo bật, whatsapp tắt), chưa có `direct_mode`
    await db.execute(text("""UPDATE core.organizations SET settings = (settings - 'direct_mode')
                             || '{"listen_direct": {"zalo": true, "whatsapp": false}}'::jsonb"""))
    await db.commit()
    sql = SQL_0038.read_text(encoding="utf-8")
    with psycopg.connect(f"{PG}/{fresh_db}", autocommit=True) as c:
        def one(q: str) -> Any:
            return c.execute(q).fetchone()[0]  # type: ignore[index, call-overload]

        for _ in range(2):                               # chạy hai lần: lần hai không đổi gì
            c.execute(sql)  # type: ignore[call-overload]
            assert one("SELECT settings -> 'direct_mode' FROM core.organizations") == {"zalo": "reply",
                                                                                     "whatsapp": "silent"}
            legacy = one("SELECT settings -> 'listen_direct' FROM core.organizations")
            assert legacy == {"zalo": True, "whatsapp": False}           # khoá cũ giữ nguyên
            modes = c.execute("SELECT external_id, listen_mode FROM core.groups ORDER BY 1").fetchall()
            assert modes == [("gOff", "off"), ("gSil", "silent")]        # nhóm cũ KHÔNG tự bật nghe
        # Sếp đã chọn trước thì migration không ghi đè
        c.execute("""UPDATE core.organizations SET settings = jsonb_set(settings, '{direct_mode,zalo}', '"off"')""")
        c.execute(sql)  # type: ignore[call-overload]
        assert one("SELECT settings #>> '{direct_mode,zalo}' FROM core.organizations") == "off"
        assert one("""SELECT count(*) FROM information_schema.columns WHERE table_schema = 'core'
                      AND table_name = 'groups' AND column_name = 'left_at'""") == 1


def test_migration_0038_follows_0037_and_single_head() -> None:
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    api_dir = ROOT / "apps" / "api"
    cfg = Config(str(api_dir / "alembic.ini"))
    cfg.set_main_option("script_location", str(api_dir / "migrations"))
    script = ScriptDirectory.from_config(cfg)
    rev = script.get_revision("0038")
    assert rev is not None and rev.down_revision == "0037"
    assert len(script.get_heads()) == 1   # v0.1.67+: đầu cây đã lên 0039–0041; chỉ cần còn đúng một head


@pytest.mark.parametrize("name", ["tagged_only", "silent", "proactive"])
async def test_every_listening_mode_stores_messages(app, db, name: str) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    await listen(db, org, "g1", mode=name)
    [eid] = await put(sessionmaker(), org, msg("Báo giá container", group="g1"))
    assert eid is not None


@pytest.mark.parametrize("name", ["pending", "off", "paused"])
async def test_non_listening_modes_drop_messages(app, db, name: str) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    await listen(db, org, "g1", mode=name)
    [eid] = await put(sessionmaker(), org, msg("Báo giá container", group="g1"))
    assert eid is None
