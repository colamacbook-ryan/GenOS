"""Review v0.1.65 P2-1 — `first_message` (nội dung tin của nhóm lạ) không được nằm lại trong stream Redis `directory`
(AOF bật) hay `.dlq` khi lõi không lưu nó: lõi XDEL bản ghi `group.discovered` sau khi commit; đưa vào DLQ thì bỏ
`first_message`; cờ `gh:bridge:new_group_mode:<kênh>` cho bridge quyết định có gắn tin đầu hay không."""

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import text

from gh.chassis.bus import BRIDGE_DIRECTORY, Event, EventBus
from gh.data.common import set_channel_setting
from gh.data.ingest import Ingest, sync_listen_sets
from gh.db import sessionmaker
from tests.phase2 import msg, org_id
from tests.test_nhom_moi_v0165 import _mode_of, _set_mode

FLAG = "gh:bridge:new_group_mode:zalo"
#: Fixture `app` đã chạy consumer thật trên `gh.bridge.directory`: các test gọi `on_directory` tự đọc một stream riêng
#: (cùng khuôn bản ghi) để không tranh tin với consumer đó.
DIR = "gh.test.directory"


def _payload(first: dict[str, Any] | None, group: str = "gN") -> dict[str, Any]:
    p: dict[str, Any] = {"channel": "zalo", "session_id": "s1", "group_id": group, "name": "", "member_count": None}
    if first is not None:
        p["first_message"] = first
    return p


async def _consume(bus: EventBus, ing: Ingest) -> int:
    return await bus.process_once(DIR, "ingest", "c1", ing.on_directory, block_ms=50)


@pytest.mark.parametrize("mode", ["silent", "ask", "off"])
async def test_discovered_entry_is_xdeleted_after_commit(app, db, redis, mode: str) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    await _set_mode(db, "zalo", mode)
    bus = EventBus(redis)
    first = msg("nội dung nhạy cảm của nhóm lạ", group="gN", msg_id="x-1")
    await bus.publish(DIR, "group.discovered", _payload(first), actor="bridge:zalo")
    assert await redis.xlen(DIR) == 1
    assert await _consume(bus, Ingest(bus, redis, org, sessionmaker())) == 1
    assert await redis.xlen(DIR) == 0                    # bản ghi chứa nội dung tin đã bị xoá khỏi stream
    assert (await redis.xpending(DIR, "ingest"))["pending"] == 0
    stored = (await db.execute(text("SELECT count(*) FROM raw.events"))).scalar_one()
    assert stored == (1 if mode == "silent" else 0)                    # silent lưu vào Kho thô; ask/off bỏ hẳn nội dung
    assert await _mode_of(db, "gN") == ("pending" if mode == "ask" else mode)


async def test_other_directory_entries_are_kept(app, db, redis) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    bus = EventBus(redis)
    await bus.publish(DIR, "groups", {"channel": "zalo", "groups": []}, actor="bridge:zalo")
    await bus.publish(DIR, "group.discovered", _payload(None, "gM"), actor="bridge:zalo")
    ing = Ingest(bus, redis, org, sessionmaker())
    await _consume(bus, ing)
    assert [f[b"type"] for _, f in await redis.xrange(DIR)] == [b"groups"]


async def test_failed_handler_keeps_entry_until_it_commits_then_deletes(app, db, redis) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    bus = EventBus(redis)
    await bus.publish(DIR, "group.discovered", _payload(msg("x", group="gN", msg_id="x-2")),
                      actor="bridge:zalo")
    ing = Ingest(bus, redis, org, sessionmaker())

    async def boom(e: Event) -> None:
        raise ValueError("hỏng giữa chừng")

    await bus.process_once(DIR, "ingest", "c1", boom, block_ms=50, max_deliveries=5)
    assert await redis.xlen(DIR) == 1                    # chưa xử lý được ⇒ còn để giao lại
    await asyncio.sleep(0.01)
    await bus.process_once(DIR, "ingest", "c1", ing.on_directory, block_ms=50, reclaim_idle_ms=0)
    assert await redis.xlen(DIR) == 0


async def test_dlq_copy_and_original_drop_first_message(redis) -> None:  # type: ignore[no-untyped-def]
    bus = EventBus(redis)
    await bus.publish(BRIDGE_DIRECTORY, "group.discovered", _payload(msg("bí mật", group="gN", msg_id="x-3")),
                      actor="bridge:zalo", org_id="org-1")

    async def boom(e: Event) -> None:
        raise ValueError("hỏng")

    for _ in range(3):
        await bus.process_once(BRIDGE_DIRECTORY, "ingest", "c", boom, block_ms=50, reclaim_idle_ms=0, max_deliveries=3)
        await asyncio.sleep(0.01)
    [(_, fields)] = await redis.xrange(f"{BRIDGE_DIRECTORY}.dlq")
    assert "bí mật".encode() not in fields[b"payload"] and b"first_message" not in fields[b"payload"]
    assert b'"group_id":"gN"' in fields[b"payload"]                   # vẫn đủ để điều tra: nhóm nào, kênh nào
    assert fields[b"error"] == "hỏng".encode()
    assert await redis.xlen(BRIDGE_DIRECTORY) == 0                    # bản gốc (chứa nội dung) cũng bị xoá


async def test_dlq_keeps_other_streams_untouched(redis) -> None:  # type: ignore[no-untyped-def]
    bus = EventBus(redis)
    await bus.publish("gh.test.stream", "t", {"n": 1, "first_message": "giữ nguyên"}, actor="test")

    async def boom(e: Event) -> None:
        raise ValueError("hỏng")

    await bus.process_once("gh.test.stream", "g", "c", boom, block_ms=50, reclaim_idle_ms=0, max_deliveries=1)
    [(_, fields)] = await redis.xrange("gh.test.stream.dlq")
    assert "giữ nguyên".encode() in fields[b"payload"]               # chỉ stream directory bị lọc


async def test_fake_event_without_message_id_still_works(app, db, redis) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    ev = SimpleNamespace(type="group.discovered", payload=_payload(msg("tin", group="gN", msg_id="x-4")))
    await Ingest(None, redis, org, sessionmaker()).on_directory(ev)  # type: ignore[arg-type]
    assert await _mode_of(db, "gN") == "silent"


# ─── cờ gh:bridge:new_group_mode:<kênh> ────────────────────────────────────────────────────────────────────────────

async def test_listen_sync_writes_new_group_mode_flag_with_default_silent(app, db, redis) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    await sync_listen_sets(db, redis, org)
    assert await redis.get(FLAG) == b"silent"                          # bản cài cũ chưa có khoá ⇒ mặc định silent
    for mode in ("ask", "off", "silent"):
        await _set_mode(db, "zalo", mode)
        await sync_listen_sets(db, redis, org)
        assert await redis.get(FLAG) == mode.encode()
    assert await redis.get("gh:bridge:new_group_mode:whatsapp") == b"silent"   # theo từng kênh


async def test_patch_channel_and_box_apply_refresh_the_flag(owner_api, db, redis) -> None:  # type: ignore[no-untyped-def]
    from tests.conftest import verify_pin
    from tests.test_nhom_moi_v0165 import _pending

    await verify_pin(owner_api)
    r = await owner_api.send("PATCH", "/channels/zalo", {"new_group_mode": "off"})
    assert r.status_code == 200 and await redis.get(FLAG) == b"off"
    await _set_mode(db, "zalo", "ask")
    ids = await _pending(db, redis, "g1")
    r = await owner_api.send("PATCH", f"/channels/zalo/groups/{ids['g1']}/mode", {"mode": "silent"})
    assert r.status_code == 200 and await redis.get(FLAG) == b"ask"   # chọn riêng nhóm này: nhóm sau giữ nguyên
    # nhóm đã ở đúng chế độ rồi vẫn phải làm mới cờ khi Sếp tick «Áp dụng cho các nhóm mới»
    url = f"/channels/zalo/groups/{ids['g1']}/mode"
    r = await owner_api.send("PATCH", url, {"mode": "silent", "apply_to_new": True})
    assert r.status_code == 200 and await redis.get(FLAG) == b"silent"


async def test_account_change_reset_refreshes_the_flag(owner_api, db, redis, app) -> None:  # type: ignore[no-untyped-def]
    from tests.test_doi_tai_khoan_nhom_moi_v0165 import _active
    from tests.test_kenh_ben_v016x import _session

    org = await org_id(db)
    await set_channel_setting(db, org, "new_group_mode", "zalo", "off")
    await db.commit()
    await _active(app, redis, org, await _session(db, org, state="pending_qr", started_ago_h=None, cred=False,
                                                  qr_ago_h=0.01), "A1")
    await redis.set(FLAG, "off")
    await _active(app, redis, org, await _session(db, org, state="pending_qr", started_ago_h=None, cred=False,
                                                  qr_ago_h=0.01), "B2")
    assert await redis.get(FLAG) == b"silent"                          # về mặc định kèm cờ cho bridge
