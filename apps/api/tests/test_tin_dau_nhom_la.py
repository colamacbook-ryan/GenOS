"""QD 17/10 (Sếp: «thu hết tình báo»): tin ĐẦU TIÊN của một nhóm LẠ không được mất.

Bridge không XADD tin của nhóm chưa có trong tập nghe mà báo `group.discovered` kèm chính tin đó (`first_message`).
Lõi tạo nhóm rồi: `silent` (mặc định) ⇒ lưu luôn tin đầu; `ask` (pending) / `off` ⇒ KHÔNG lưu nội dung (giữ đúng
«chưa lưu tin trước khi Sếp chọn»). Không lưu trùng (raw.event_keys idempotent)."""

from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import text

from gh.data.ingest import Ingest, ingest_message
from gh.db import sessionmaker
from tests.phase2 import msg, org_id
from tests.test_nhom_moi_v0165 import _mode_of, _set_mode


def _discovered(first: dict[str, Any] | None, group: str = "gN") -> SimpleNamespace:
    payload: dict[str, Any] = {"channel": "zalo", "session_id": "s1", "group_id": group, "name": "",
                               "member_count": None}
    if first is not None:
        payload["first_message"] = first
    return SimpleNamespace(type="group.discovered", payload=payload)


async def _ingest(redis: Any, org: Any) -> Ingest:
    return Ingest(None, redis, org, sessionmaker())  # type: ignore[arg-type]


async def _count(db: Any, sql: str = "SELECT count(*) FROM raw.events") -> int:
    return (await db.execute(text(sql))).scalar_one()  # type: ignore[no-any-return]


async def test_silent_new_group_stores_first_message(app, db, redis) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    first = msg("tin đầu của nhóm lạ", group="gN", msg_id="first-1")
    await (await _ingest(redis, org)).on_directory(_discovered(first))
    assert await _mode_of(db, "gN") == "silent"
    row = (await db.execute(text("SELECT body_text, direction FROM raw.events"))).one()
    assert tuple(row) == ("tin đầu của nhóm lạ", "inbound")
    assert await _count(db, "SELECT count(*) FROM refinery.event_state") == 1
    assert await redis.smembers("gh:bridge:listen:zalo") == {b"gN"}


async def test_silent_first_message_is_not_duplicated_on_redelivery_or_later_inbound(app, db, redis) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    first = msg("tin đầu", group="gN", msg_id="first-2")
    ing = await _ingest(redis, org)
    await ing.on_directory(_discovered(first))
    await ing.on_directory(_discovered(first))                                  # bus giao lại
    assert await ingest_message(db, org, first) is None                         # đường inbound thường gặp lại cùng tin
    await db.commit()
    assert await _count(db) == 1
    assert await _count(db, "SELECT count(*) FROM raw.event_keys WHERE external_msg_id = 'first-2'") == 1


@pytest.mark.parametrize("value", ["ask", "off"])
async def test_ask_and_off_new_group_do_not_store_first_message(app, db, redis, value: str) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    await _set_mode(db, "zalo", value)
    first = msg("bí mật chờ Sếp chọn", group="gN", msg_id="first-3")
    await (await _ingest(redis, org)).on_directory(_discovered(first))
    assert await _mode_of(db, "gN") == ("pending" if value == "ask" else "off")
    assert await _count(db) == 0
    assert await _count(db, "SELECT count(*) FROM raw.event_keys") == 0         # cũng không để dấu vết khoá tin
    assert await _count(db, "SELECT count(*) FROM core.person_identities") == 0  # và không tạo người gửi
    assert await redis.smembers("gh:bridge:listen:zalo") == set()


async def test_discovered_without_or_with_foreign_first_message_stores_nothing(app, db, redis) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    ing = await _ingest(redis, org)
    await ing.on_directory(_discovered(None, "gA"))                              # bản bridge cũ: không kèm tin
    await ing.on_directory(_discovered(msg("của nhóm khác", group="gZ", msg_id="x-1"), "gB"))  # không thuộc nhóm báo
    await ing.on_directory(_discovered(msg("tin 1-1", group=None, msg_id="x-2"), "gC"))        # không phải tin nhóm
    await ing.on_directory(_discovered({"body_text": "thiếu mã tin"}, "gD"))                   # hỏng
    assert await _count(db) == 0
    ids = {r[0] for r in (await db.execute(text("SELECT external_id FROM core.groups"))).all()}
    assert ids == {"gA", "gB", "gC", "gD"}


async def test_existing_pending_or_off_group_still_drops_first_message(app, db, redis) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    ing = await _ingest(redis, org)
    await ing.on_directory(_discovered(None, "gP"))
    await db.execute(text("UPDATE core.groups SET listen_mode = 'pending' WHERE external_id = 'gP'"))
    await db.commit()
    await ing.on_directory(_discovered(msg("sau khi bridge khởi động lại", group="gP", msg_id="p-1"), "gP"))
    assert await _mode_of(db, "gP") == "pending" and await _count(db) == 0


async def test_first_message_failure_does_not_lose_the_new_group(app, db, redis, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Review v0.1.65 P3: lưu tin đầu lỗi (lỗi DB) chạy trong savepoint ⇒ chỉ mất tin đầu; nhóm vừa tạo, tập nghe và
    bộ đếm «N nhóm mới» vẫn được commit (trước đây cả giao dịch bị huỷ ⇒ bus giao lại 5 lần rồi vào DLQ, nhóm không
    có)."""
    import gh.data.ingest as ingest_mod

    async def boom(db_: Any, org_: Any, p: Any) -> None:
        await db_.execute(text("SELECT 1/0"))

    monkeypatch.setattr(ingest_mod, "ingest_discovered_message", boom)
    org = await org_id(db)
    await (await _ingest(redis, org)).on_directory(_discovered(msg("tin lỗi", group="gE", msg_id="e-1"), "gE"))
    assert await _mode_of(db, "gE") == "silent"
    assert await _count(db) == 0
    assert await redis.smembers("gh:bridge:listen:zalo") == {b"gE"}
    n = (await db.execute(text("SELECT settings #>> '{new_groups_silent,zalo}' FROM core.organizations"))).scalar_one()
    assert n == "1"
