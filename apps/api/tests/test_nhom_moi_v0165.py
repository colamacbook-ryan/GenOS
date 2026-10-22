"""v0.1.65 (F1d) — "Nhóm mới" có mặc định cho các nhóm SAU: `settings.new_group_mode.<kênh>` ∈ ask | silent | off
(mặc định `silent` từ QD 17/10 = nhóm mới vào Lắng nghe im lặng; `ask` = nhóm mới vào `pending` + hộp hỏi). Ingest đọc
thiết lập này; đổi thiết lập KHÔNG đổi nhóm đã có; đổi ở thẻ kênh cần mã PIN (như tin 1-1); ô «Áp dụng cho các nhóm
mới sau này» ở hộp hỏi (chỉ khi `ask`) lưu cùng lúc với lựa chọn."""

from typing import Any

import pytest
from sqlalchemy import text

from gh.data.common import DEFAULT_NEW_GROUP_MODE, NEW_GROUP_MODES, new_group_mode
from gh.data.ingest import handle_directory, handle_group_discovered
from gh.db import sessionmaker
from tests.conftest import Api, verify_pin
from tests.phase2 import msg, org_id, put
from tests.test_rbac_api import login_as


async def _set_mode(db: Any, channel: str, mode: str) -> None:
    await db.execute(text("""UPDATE core.organizations SET settings = jsonb_set(
        CASE WHEN jsonb_typeof(settings -> 'new_group_mode') = 'object' THEN settings
             ELSE settings || '{"new_group_mode": {}}'::jsonb END,
        ARRAY['new_group_mode', :t], to_jsonb(CAST(:v AS text)))"""), {"t": channel, "v": mode})
    await db.commit()


async def _mode_of(db: Any, ext: str) -> str:
    return (await db.execute(text("SELECT listen_mode FROM core.groups WHERE external_id = :x"),
                             {"x": ext})).scalar_one()  # type: ignore[no-any-return]


async def _members(db: Any, ext: str) -> int:
    return (await db.execute(text("""SELECT count(*) FROM core.group_members gm JOIN core.groups g ON g.id = gm.group_id
                                     WHERE g.external_id = :x"""), {"x": ext})).scalar_one()  # type: ignore[no-any-return]


def _grp(ext: str, *members: str) -> dict[str, Any]:
    return {"external_id": ext, "name": f"Nhóm {ext}", "member_count": len(members),
            "members": [{"external_id": m, "name": f"Người {m}"} for m in members]}


def _card(cards: list[dict[str, Any]], type_: str = "zalo") -> dict[str, Any]:
    return next(c for c in cards if c["type"] == type_)


# ─── hằng + đọc thiết lập ────────────────────────────────────────────────────────────────────────────────────────

def test_new_group_mode_reader_defaults_to_silent_and_ignores_garbage() -> None:
    assert NEW_GROUP_MODES == ("ask", "silent", "off") and DEFAULT_NEW_GROUP_MODE == "silent"
    assert new_group_mode(None, "zalo") == "silent"
    assert new_group_mode({}, "zalo") == "silent"                                    # bản cài cũ: không có khoá
    assert new_group_mode({"new_group_mode": {"zalo": "ask"}}, "zalo") == "ask"
    assert new_group_mode({"new_group_mode": {"zalo": "ask"}}, "whatsapp") == "silent"   # theo từng kênh
    assert new_group_mode({"new_group_mode": {"zalo": "bậy"}}, "zalo") == "silent"
    assert new_group_mode({"new_group_mode": "ask"}, "zalo") == "silent"             # sai kiểu ⇒ mặc định


# ─── ingest đọc thiết lập ────────────────────────────────────────────────────────────────────────────────────────

async def test_ingest_default_is_silent_for_legacy_install_and_ask_is_pending(app, db, redis) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    assert "new_group_mode" not in (await db.execute(text("SELECT settings FROM core.organizations"))).scalar_one()
    [eid] = await put(sessionmaker(), org, msg("Còn hàng không chị?", group="gA"))
    assert eid is not None and await _mode_of(db, "gA") == "silent"        # QD 17/10: mặc định lắng nghe im lặng
    await _set_mode(db, "zalo", "ask")
    [eid] = await put(sessionmaker(), org, msg("Alo", group="gB"))
    assert eid is None and await _mode_of(db, "gB") == "pending"           # Sếp chọn Hỏi Sếp ⇒ chờ, chưa lưu tin


async def test_ingest_silent_stores_the_first_message_without_pending(app, db, redis) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    await _set_mode(db, "zalo", "silent")
    [eid] = await put(sessionmaker(), org, msg("Em hỏi giá lô này", group="gS"))
    assert eid is not None
    assert await _mode_of(db, "gS") == "silent"
    assert (await db.execute(text("SELECT body_text FROM raw.events"))).scalar_one() == "Em hỏi giá lô này"
    # không trả lời: tin im lặng không đi đường nhanh
    assert (await db.execute(text("SELECT fast FROM refinery.event_state"))).scalar_one() is False


async def test_ingest_off_drops_messages_but_still_collects_members(app, db, redis) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    await _set_mode(db, "zalo", "off")
    [eid] = await put(sessionmaker(), org, msg("Tin này không được lưu", group="gO"))
    assert eid is None and await _mode_of(db, "gO") == "off"
    assert (await db.execute(text("SELECT count(*) FROM raw.events"))).scalar_one() == 0
    await handle_directory(db, redis, org, {"channel": "zalo", "groups": [_grp("gO2", "u1", "u2")]})
    await db.commit()
    assert await _mode_of(db, "gO2") == "off" and await _members(db, "gO2") == 2     # vẫn thu thành viên
    assert await redis.smembers("gh:bridge:listen:zalo") == set()


@pytest.mark.parametrize("value", ["ask", "silent", "off"])
async def test_directory_and_discovered_follow_the_setting(app, db, redis, value: str) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    await _set_mode(db, "zalo", value)
    await handle_directory(db, redis, org, {"channel": "zalo", "groups": [_grp("gD", "u1")]})
    row, fresh = await handle_group_discovered(db, redis, org, {"channel": "zalo", "group_id": "gF", "name": "Mới"})
    await db.commit()
    want = "pending" if value == "ask" else value
    assert await _mode_of(db, "gD") == want and row.listen_mode == want and fresh is True
    listened = await redis.smembers("gh:bridge:listen:zalo")
    assert listened == ({b"gD", b"gF"} if value == "silent" else set())


async def test_setting_is_per_channel_and_never_changes_existing_groups(app, db, redis) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    await _set_mode(db, "zalo", "ask")
    await handle_directory(db, redis, org, {"channel": "zalo", "groups": [_grp("gOld")]})
    await db.commit()
    assert await _mode_of(db, "gOld") == "pending"
    await _set_mode(db, "zalo", "silent")
    await handle_directory(db, redis, org, {"channel": "zalo", "groups": [_grp("gOld", "u1"), _grp("gNew")]})
    await db.commit()
    assert [await _mode_of(db, "gOld"), await _mode_of(db, "gNew")] == ["pending", "silent"]   # nhóm cũ giữ nguyên
    await _set_mode(db, "zalo", "off")
    await put(sessionmaker(), org, msg("x", group="gOld"))
    assert await _mode_of(db, "gOld") == "pending"
    await handle_directory(db, redis, org, {"channel": "whatsapp", "groups": [_grp("wa1")]})    # kênh khác: silent
    await db.commit()
    assert await _mode_of(db, "wa1") == "silent"


# ─── PATCH /channels/{type}: new_group_mode (cần PIN như tin 1-1) ──────────────────────────────────────────────────

async def test_patch_new_group_mode_needs_pin_and_roundtrips(owner_api: Api, db, redis) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    card = _card((await owner_api.get("/channels")).json())
    assert card["new_group_mode"] == "silent" and card["new_group_asked"] is False     # mặc định (QD 17/10)
    r = await owner_api.send("PATCH", "/channels/zalo", {"new_group_mode": "ask"})
    assert r.status_code == 423 and r.json()["code"] == "PIN_REQUIRED"
    assert _card((await owner_api.get("/channels")).json())["new_group_mode"] == "silent"   # chưa đổi
    await verify_pin(owner_api)
    r = await owner_api.send("PATCH", "/channels/zalo", {"new_group_mode": "ask"})
    assert r.status_code == 200, r.text
    assert r.json()["new_group_mode"] == "ask" and r.json()["new_group_asked"] is True
    assert r.json()["direct_mode"] == "silent"                                          # không đụng tin 1-1
    assert _card((await owner_api.get("/channels")).json(), "whatsapp")["new_group_mode"] == "silent"
    [eid] = await put(sessionmaker(), org, msg("Chào cả nhà", group="gAsk"))
    assert eid is None and await _mode_of(db, "gAsk") == "pending"
    r = await owner_api.send("PATCH", "/channels/zalo", {"new_group_mode": "silent"})
    assert r.json()["new_group_mode"] == "silent"
    [eid] = await put(sessionmaker(), org, msg("Chào cả nhà", group="gNew"))
    assert eid is not None and await _mode_of(db, "gNew") == "silent"
    r = await owner_api.send("PATCH", "/channels/zalo", {"new_group_mode": "off", "direct_mode": "reply"})
    assert r.json()["new_group_mode"] == "off" and r.json()["direct_mode"] == "reply"   # đổi được cả hai một lượt
    r = await owner_api.send("PATCH", "/channels/zalo", {"new_group_mode": "ask"})
    assert r.json()["new_group_mode"] == "ask"
    assert (await owner_api.send("PATCH", "/channels/zalo", {"new_group_mode": "bậy"})).status_code == 422
    assert (await owner_api.send("PATCH", "/channels/zalo", {})).status_code == 422
    assert (await owner_api.send("PATCH", "/channels/linkedin", {"new_group_mode": "silent"})).status_code == 404
    log = (await db.execute(text("""SELECT detail FROM ops.action_log WHERE action = 'channel.new_group_mode_changed'
                                    ORDER BY at"""))).all()
    assert [(d.detail["new_group_mode"], d.detail["from"]) for d in log] == [
        ("ask", "silent"), ("silent", "ask"), ("off", "silent"), ("ask", "off")]


async def test_patch_new_group_mode_does_not_touch_existing_groups(owner_api: Api, db, redis) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    await _set_mode(db, "zalo", "ask")
    await handle_directory(db, redis, org, {"channel": "zalo", "groups": [_grp("g1"), _grp("g2")]})
    await db.execute(text("UPDATE core.groups SET listen_mode = 'tagged_only' WHERE external_id = 'g2'"))
    await db.commit()
    await verify_pin(owner_api)
    assert (await owner_api.send("PATCH", "/channels/zalo", {"new_group_mode": "silent"})).status_code == 200
    assert [await _mode_of(db, "g1"), await _mode_of(db, "g2")] == ["pending", "tagged_only"]


async def test_patch_new_group_mode_viewer_forbidden(owner_api: Api, db, client) -> None:  # type: ignore[no-untyped-def]
    viewer = await login_as(client, db, "auditor")
    assert (await viewer.send("PATCH", "/channels/zalo", {"new_group_mode": "silent"})).status_code == 403


# ─── hộp hỏi: chọn nhóm + «Áp dụng cho các nhóm mới sau này» ───────────────────────────────────────────────────────

async def _pending(db: Any, redis: Any, *exts: str) -> dict[str, str]:
    org = await org_id(db)
    await _set_mode(db, "zalo", "ask")                                         # hộp hỏi chỉ có khi Sếp chọn «Hỏi Sếp»
    await handle_directory(db, redis, org, {"channel": "zalo", "groups": [_grp(e, "u1") for e in exts]})
    await db.commit()
    return {r.external_id: str(r.id) for r in (await db.execute(text("SELECT external_id, id FROM core.groups"))).all()}


async def test_box_choice_with_apply_saves_default_for_future_groups(owner_api: Api, db, redis) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    ids = await _pending(db, redis, "g1", "g2")
    # áp dụng cho nhóm sau = đổi chính sách ⇒ cần PIN, và KHÔNG đổi gì khi chưa có PIN (kể cả nhóm đang chọn)
    r = await owner_api.send("PATCH", f"/channels/zalo/groups/{ids['g1']}/mode",
                             {"mode": "silent", "apply_to_new": True})
    assert r.status_code == 423 and r.json()["code"] == "PIN_REQUIRED"
    assert await _mode_of(db, "g1") == "pending"
    await verify_pin(owner_api)
    r = await owner_api.send("PATCH", f"/channels/zalo/groups/{ids['g1']}/mode",
                             {"mode": "silent", "apply_to_new": True})
    assert r.status_code == 200 and r.json()["listen_mode"] == "silent"
    card = _card((await owner_api.get("/channels")).json())
    assert card["new_group_mode"] == "silent" and card["new_group_asked"] is True
    assert await _mode_of(db, "g2") == "pending"                                       # nhóm khác không bị đổi
    [eid] = await put(sessionmaker(), org, msg("Nhóm sau tự im lặng", group="gLater"))
    assert eid is not None and await _mode_of(db, "gLater") == "silent"
    # chọn "Không nghe" + áp dụng ⇒ nhóm sau vào off
    r = await owner_api.send("PATCH", f"/channels/zalo/groups/{ids['g2']}/mode", {"mode": "off", "apply_to_new": True})
    assert r.status_code == 200
    assert _card((await owner_api.get("/channels")).json())["new_group_mode"] == "off"
    await put(sessionmaker(), org, msg("Không lưu", group="gLater2"))
    assert await _mode_of(db, "gLater2") == "off"
    log = (await db.execute(text("""SELECT detail FROM ops.action_log WHERE action = 'channel.new_group_mode_changed'
                                    ORDER BY at"""))).all()
    assert [d.detail["new_group_mode"] for d in log] == ["silent", "off"]


async def test_box_choice_without_apply_needs_no_pin_and_keeps_ask(owner_api: Api, db, redis) -> None:  # type: ignore[no-untyped-def]
    ids = await _pending(db, redis, "g1")
    r = await owner_api.send("PATCH", f"/channels/zalo/groups/{ids['g1']}/mode", {"mode": "silent"})
    assert r.status_code == 200, r.text                                                # như cũ: không PIN
    r = await owner_api.send("PATCH", f"/channels/zalo/groups/{ids['g1']}/mode", {"mode": "off", "apply_to_new": False})
    assert r.status_code == 200
    card = _card((await owner_api.get("/channels")).json())
    assert card["new_group_mode"] == "ask" and card["new_group_asked"] is True      # đã chọn ở hộp hỏi; vẫn «Hỏi Sếp»


async def test_box_apply_only_for_silent_or_off(owner_api: Api, db, redis) -> None:  # type: ignore[no-untyped-def]
    ids = await _pending(db, redis, "g1")
    await verify_pin(owner_api)
    r = await owner_api.send("PATCH", f"/channels/zalo/groups/{ids['g1']}/mode",
                             {"mode": "proactive", "apply_to_new": True})
    assert r.status_code == 422, r.text
    assert await _mode_of(db, "g1") == "pending"
    assert _card((await owner_api.get("/channels")).json())["new_group_mode"] == "ask"


async def test_reconnecting_the_channel_resets_the_first_ask_flag(owner_api: Api, db, redis) -> None:  # type: ignore[no-untyped-def]
    await verify_pin(owner_api)
    r = await owner_api.send("PATCH", "/channels/zalo", {"new_group_mode": "ask"})
    assert r.json()["new_group_asked"] is True
    await redis.set("gh:bridge:heartbeat", "1")
    r = await owner_api.send("POST", "/channels/zalo/login", {"accept_risk": True})
    assert r.status_code == 202, r.text
    card = _card((await owner_api.get("/channels")).json())
    assert card["new_group_asked"] is False and card["new_group_mode"] == "ask"          # lần hỏi đầu sau khi nối lại
