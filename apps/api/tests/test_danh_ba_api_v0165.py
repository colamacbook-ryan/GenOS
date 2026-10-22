"""v0.1.65 (F1b) — API chế độ nghe: một nguồn hằng, làm mới danh sách nhóm (`directory.sync`), chọn chế độ cho nhóm mới,
lọc `pending`, nhóm đã rời không đếm "đang nghe", tin 1-1 ba mức, một định nghĩa "đang nghe" ở mọi chỗ đếm."""

import re
from pathlib import Path
from typing import Any, get_args

import orjson
from sqlalchemy import text

from gh.biz.graph import jobs as graph_jobs
from gh.chassis.bus import BRIDGE_CONTROL
from gh.data.common import ALL_LISTEN_MODES, LISTENING_MODES, NOT_LISTENING_MODES, SETTABLE_LISTEN_MODES
from gh.data.ingest import handle_directory
from gh.db import sessionmaker
from gh.system_api.routes import GroupPatch
from tests.conftest import Api, verify_pin
from tests.phase2 import msg, org_id, put
from tests.test_rbac_api import login_as

ROOT = Path(__file__).resolve().parents[3]


async def _control(redis: Any) -> list[dict[str, Any]]:
    return [{"type": f[b"type"].decode(), **orjson.loads(f[b"payload"])} for _, f in await redis.xrange(BRIDGE_CONTROL)]


async def _active_session(db: Any, org: Any, type_: str = "zalo") -> Any:
    ch = (await db.execute(text("SELECT id FROM core.channels WHERE org_id = :o AND type = :t"),
                           {"o": org, "t": type_})).scalar_one()
    sid = (await db.execute(text("""INSERT INTO core.channel_sessions
                                      (channel_id, org_id, account_label, state, started_at)
                                    VALUES (:c, :o, 'Zalo Sếp', 'active', now()) RETURNING id"""),
                            {"c": ch, "o": org})).scalar_one()
    await db.commit()
    return sid


async def _groups(db: Any, redis: Any, org: Any, modes: dict[str, str], *, type_: str = "zalo") -> dict[str, str]:
    """Dựng nhóm qua danh bạ (vào `pending`) rồi đặt chế độ; trả {external_id: id}."""
    await handle_directory(db, redis, org, {"channel": type_, "groups": [{"external_id": k, "name": f"Nhóm {k}",
                                                                          "member_count": 3} for k in modes]})
    for k, m in modes.items():
        await db.execute(text("UPDATE core.groups SET listen_mode = :m WHERE external_id = :x"), {"m": m, "x": k})
    await db.commit()
    rows = (await db.execute(text("SELECT external_id, id FROM core.groups"))).all()
    return {r.external_id: str(r.id) for r in rows}


# ─── một nguồn hằng ────────────────────────────────────────────────────────────────────────────────────────────

def test_listen_mode_constants_are_one_source() -> None:
    assert set(ALL_LISTEN_MODES) == set(LISTENING_MODES) | set(NOT_LISTENING_MODES)
    assert not set(LISTENING_MODES) & set(NOT_LISTENING_MODES)
    assert set(SETTABLE_LISTEN_MODES) == set(ALL_LISTEN_MODES) - {"pending"}
    assert "paused" in NOT_LISTENING_MODES and "pending" in NOT_LISTENING_MODES     # F066
    # API nhận đúng bộ chế độ đó (pending chỉ để echo từ web cũ — update_group chặn đổi sang pending)
    assert set(get_args(GroupPatch.model_fields["listen_mode"].annotation)[0].__args__) == set(ALL_LISTEN_MODES)


def test_web_listen_mode_types_match_api() -> None:
    ts = (ROOT / "packages" / "contracts" / "src" / "phase2.ts").read_text(encoding="utf-8")
    union = re.search(r"export type ListenMode = ([^;]+);", ts)
    assert union is not None
    assert set(re.findall(r"'([a-z_]+)'", union.group(1))) == set(ALL_LISTEN_MODES)
    model = (ROOT / "apps" / "web" / "src" / "screens" / "system" / "systemModel.ts").read_text(encoding="utf-8")
    block = model.split("export const LISTEN_MODES", 1)[1].split("];", 1)[0]
    assert re.findall(r"value: '([a-z_]+)'", block) == ["off", "tagged_only", "silent", "proactive", "paused"]
    assert set(re.findall(r"value: '([a-z_]+)'", block)) == set(SETTABLE_LISTEN_MODES)
    listening = re.search(r"LISTENING_MODE_VALUES[^=]*= \[([^\]]+)\]", model)
    assert listening is not None and set(re.findall(r"'([a-z_]+)'", listening.group(1))) == set(LISTENING_MODES)


def test_no_wait_qr_label_and_new_tag_label_in_web_sources() -> None:
    for name in ("screens/system/systemModel.ts", "screens/relations/relationsModel.ts"):
        src = (ROOT / "apps" / "web" / "src" / name).read_text(encoding="utf-8")
        assert "Tạm dừng · chờ QR" not in src and "'Chỉ khi được tag'" not in src
    rel = (ROOT / "apps" / "web" / "src" / "screens" / "relations" / "relationsModel.ts").read_text(encoding="utf-8")
    assert "Nghe hết, chỉ trả lời khi được tag" in rel


# ─── danh sách nhóm, lọc pending, cờ đã rời ─────────────────────────────────────────────────────────────────────

async def test_groups_listing_pending_filter_and_left_flag(owner_api: Api, db, redis) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    ids = await _groups(db, redis, org, {"gP": "pending", "gS": "silent", "gO": "off", "gL": "pending"})
    await db.execute(text("UPDATE core.groups SET left_at = now() WHERE external_id = 'gL'"))
    await db.commit()
    allg = {g["name"]: g for g in (await owner_api.get("/channels/zalo/groups")).json()}
    assert allg["Nhóm gP"]["listen_mode"] == "pending" and allg["Nhóm gP"]["left"] is False
    assert allg["Nhóm gL"]["left"] is True and allg["Nhóm gS"]["left"] is False
    only = (await owner_api.get("/channels/zalo/groups?pending=1")).json()
    assert [g["name"] for g in only] == ["Nhóm gP"]                 # nhóm đã rời không còn chờ chọn
    card = next(c for c in (await owner_api.get("/channels")).json() if c["type"] == "zalo")
    assert card["groups_pending"] == 1 and ids["gP"]


# ─── chọn chế độ cho nhóm mới ──────────────────────────────────────────────────────────────────────────────────

async def test_set_group_mode_for_pending_group(owner_api: Api, db, redis) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    ids = await _groups(db, redis, org, {"gP": "pending", "gQ": "pending"})
    r = await owner_api.send("PATCH", f"/channels/zalo/groups/{ids['gP']}/mode", {"mode": "silent"})
    assert r.status_code == 200, r.text
    assert r.json()["listen_mode"] == "silent" and r.json()["id"] == ids["gP"]
    assert await redis.smembers("gh:bridge:listen:zalo") == {b"gP"}          # bridge bắt đầu nhận tin của nhóm đó
    r = await owner_api.send("PATCH", f"/channels/zalo/groups/{ids['gQ']}/mode", {"mode": "off"})
    assert r.status_code == 200 and r.json()["listen_mode"] == "off"
    assert await redis.smembers("gh:bridge:listen:zalo") == {b"gP"}          # "Không nghe" thì không vào tập nghe
    assert (await owner_api.get("/channels/zalo/groups?pending=1")).json() == []
    log = (await db.execute(text("SELECT detail FROM ops.action_log WHERE action = 'group.updated' ORDER BY at"))).all()
    assert [d.detail["to"] for d in log] == [{"listen_mode": "silent"}, {"listen_mode": "off"}]
    # tin của nhóm vừa chọn "Im lặng" được lưu; nhóm "Không nghe" thì không
    [a, b] = await put(sessionmaker(), org, msg("Báo giá container", group="gP"), msg("Báo giá container", group="gQ"))
    assert a is not None and b is None


async def test_set_group_mode_validation_and_permissions(owner_api: Api, db, redis, client) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    ids = await _groups(db, redis, org, {"gP": "pending"})
    url = f"/channels/zalo/groups/{ids['gP']}/mode"
    assert (await owner_api.send("PATCH", url, {"mode": "pending"})).status_code == 422
    assert (await owner_api.send("PATCH", url, {"mode": "bậy"})).status_code == 422
    assert (await owner_api.send("PATCH", url, {})).status_code == 422
    # nhóm của kênh khác / không có ⇒ 404
    other = f"/channels/whatsapp/groups/{ids['gP']}/mode"
    assert (await owner_api.send("PATCH", other, {"mode": "silent"})).status_code == 404
    nil = "/channels/zalo/groups/00000000-0000-0000-0000-000000000000/mode"
    assert (await owner_api.send("PATCH", nil, {"mode": "silent"})).status_code == 404
    bad = "/channels/zalo/groups/không-phải-uuid/mode"
    assert (await owner_api.send("PATCH", bad, {"mode": "silent"})).status_code in (404, 422)
    viewer = await login_as(client, db, "auditor")
    assert (await viewer.send("PATCH", url, {"mode": "silent"})).status_code == 403
    assert (await owner_api.get("/channels/zalo/groups?pending=1")).json()[0]["id"] == ids["gP"]   # vẫn chờ


async def test_old_web_echoing_pending_is_accepted_but_cannot_reset_to_pending(owner_api: Api, db, redis) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    ids = await _groups(db, redis, org, {"gP": "pending", "gS": "silent"})
    # web cũ gửi lại nguyên chế độ hiện tại của mọi nhóm (kể cả pending) ở bước 6: không được lỗi
    r = await owner_api.send("PATCH", f"/groups/{ids['gP']}", {"listen_mode": "pending", "view_scope": "owner"})
    assert r.status_code == 200 and r.json()["listen_mode"] == "pending"
    # nhưng không ai đặt một nhóm đã chọn về lại "pending"
    r = await owner_api.send("PATCH", f"/groups/{ids['gS']}", {"listen_mode": "pending"})
    assert r.status_code == 422 and "listen_mode" in r.json()["errors"]


# ─── làm mới danh sách nhóm ────────────────────────────────────────────────────────────────────────────────────

async def test_refresh_groups_sends_directory_sync_with_friendly_errors(owner_api: Api, db, redis) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    r = await owner_api.send("POST", "/channels/zalo/groups/refresh")
    assert r.status_code == 503 and r.json()["code"] == "BRIDGE_OFFLINE"          # bridge tắt
    await redis.set("gh:bridge:heartbeat", "1")
    r = await owner_api.send("POST", "/channels/zalo/groups/refresh")
    assert r.status_code == 409 and r.json()["code"] == "CHANNEL_NOT_ACTIVE"       # chưa đăng nhập
    assert "đăng nhập" in r.json()["title"].lower()
    sid = await _active_session(db, org)
    r = await owner_api.send("POST", "/channels/zalo/groups/refresh")
    assert r.status_code == 202, r.text
    assert r.json() == {"session_id": str(sid), "requested": True}
    assert [c for c in await _control(redis) if c["type"] == "directory.sync"] == [
        {"type": "directory.sync", "channel": "zalo", "session_id": str(sid)}]
    # bấm liền lần nữa: giãn cách, KHÔNG gửi thêm lệnh (tránh dồn dập lên tài khoản cá nhân)
    r = await owner_api.send("POST", "/channels/zalo/groups/refresh")
    assert r.status_code == 429 and r.json()["code"] == "REFRESH_TOO_SOON"
    assert len([c for c in await _control(redis) if c["type"] == "directory.sync"]) == 1
    log = (await db.execute(text("""SELECT target_id, result FROM ops.action_log
                                    WHERE action = 'channel.groups_refresh'"""))).all()
    assert [(x.target_id, x.result) for x in log] == [("zalo", "ok")]


async def test_refresh_groups_rejects_non_qr_channels_and_viewers(owner_api: Api, db, redis, client) -> None:  # type: ignore[no-untyped-def]
    await redis.set("gh:bridge:heartbeat", "1")
    assert (await owner_api.send("POST", "/channels/linkedin/groups/refresh")).status_code == 404
    assert (await owner_api.send("POST", "/channels/khong-co/groups/refresh")).status_code == 404
    viewer = await login_as(client, db, "auditor")
    assert (await viewer.send("POST", "/channels/zalo/groups/refresh")).status_code == 403


# ─── một định nghĩa "đang nghe" ở mọi chỗ đếm ──────────────────────────────────────────────────────────────────

async def test_one_definition_of_listening_across_counters(owner_api: Api, db, redis) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    await _groups(db, redis, org, {"gT": "tagged_only", "gS": "silent", "gP": "proactive", "gPause": "paused",
                                   "gOff": "off", "gPend": "pending", "gGone": "silent"})
    await db.execute(text("UPDATE core.groups SET left_at = now() WHERE external_id = 'gGone'"))   # rời ⇒ không tính
    await db.commit()
    card = next(c for c in (await owner_api.get("/channels")).json() if c["type"] == "zalo")
    assert card["groups_listening"] == 3
    names = {g["name"] for g in (await owner_api.get("/listening-groups")).json()}
    assert names == {"Nhóm gT", "Nhóm gS", "Nhóm gP"}            # không gồm paused/pending/off/đã rời
    assert (await owner_api.get("/header")).json()["groups_listening"] == 3
    pipe = (await owner_api.get("/data/pipeline")).json()
    assert pipe["groups_listening"] == 3
    ov = await owner_api.get("/overview")
    assert ov.status_code == 200, ov.text
    assert ov.json()["health"]["tech"]["groups_listening"] == 3


async def test_setup_step6_needs_a_really_listening_group(owner_api: Api, db, redis) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    ids = await _groups(db, redis, org, {"gPend": "pending", "gPause": "paused"})
    r = await owner_api.send("PUT", "/setup/steps/6", {"groups": [
        {"id": ids["gPend"], "listen_mode": "pending"}, {"id": ids["gPause"], "listen_mode": "paused"}]})
    assert r.status_code == 409, r.text                      # pending / paused không tính là đang nghe
    r = await owner_api.send("PUT", "/setup/steps/6", {"groups": [{"id": ids["gPend"], "listen_mode": "silent"}]})
    assert r.status_code == 200, r.text


# ─── tin 1-1 ba mức ────────────────────────────────────────────────────────────────────────────────────────────

async def test_channel_direct_mode_roundtrip(owner_api: Api, db, redis) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    card = next(c for c in (await owner_api.get("/channels")).json() if c["type"] == "zalo")
    assert card["direct_mode"] == "silent" and card["listen_direct"] is True       # mặc định im lặng
    assert (await owner_api.send("PATCH", "/channels/zalo", {"direct_mode": "reply"})).status_code == 423   # cần PIN
    await verify_pin(owner_api)
    r = await owner_api.send("PATCH", "/channels/zalo", {"direct_mode": "reply"})
    assert r.status_code == 200 and r.json()["direct_mode"] == "reply" and r.json()["listen_direct"] is True
    assert await redis.get("gh:bridge:listen_direct:zalo") == b"1"
    r = await owner_api.send("PATCH", "/channels/zalo", {"direct_mode": "off"})
    assert r.json()["direct_mode"] == "off" and r.json()["listen_direct"] is False
    assert await redis.get("gh:bridge:listen_direct:zalo") == b"0"
    [dropped] = await put(sessionmaker(), org, msg("Chào anh", group=None))
    assert dropped is None
    # khoá cũ vẫn dùng được (web cũ): true ⇒ reply, false ⇒ off
    assert (await owner_api.send("PATCH", "/channels/zalo", {"listen_direct": True})).json()["direct_mode"] == "reply"
    assert (await owner_api.send("PATCH", "/channels/zalo", {"listen_direct": False})).json()["direct_mode"] == "off"
    assert (await owner_api.send("PATCH", "/channels/zalo", {"direct_mode": "bậy"})).status_code == 422
    assert (await owner_api.send("PATCH", "/channels/zalo", {})).status_code == 422
    log = (await db.execute(text("""SELECT detail FROM ops.action_log WHERE action = 'channel.listen_direct_changed'
                                    ORDER BY at"""))).all()
    assert [d.detail["direct_mode"] for d in log] == ["reply", "off", "reply", "off"]


# ─── bản đồ quan hệ không tính nhóm đã rời ──────────────────────────────────────────────────────────────────────

async def test_graph_ignores_groups_the_boss_left(app, db, redis) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    members = [{"external_id": "a", "name": "A", "role": "admin"}, {"external_id": "b", "name": "B"}]
    await handle_directory(db, redis, org, {"channel": "zalo", "groups": [
        {"external_id": "g1", "name": "N1", "member_count": 2, "members": members},
        {"external_id": "g2", "name": "N2", "member_count": 2, "members": members}]})
    await db.commit()
    before = await graph_jobs.recompute_org(db, org)
    await db.commit()
    assert before["shares_members"] == 1 and before["owns"] == 2
    await db.execute(text("UPDATE core.groups SET left_at = now() WHERE external_id = 'g2'"))
    await db.commit()
    after = await graph_jobs.recompute_org(db, org)
    await db.commit()
    assert after["shares_members"] == 0 and after["owns"] == 1   # chỉ còn g1; cầu nối không dựa vào nhóm đã rời
    assert after["bridges"] == 0
