# ruff: noqa: E501, F811
"""v0.1.76 (gói T2) — «OK #MÃ» gửi tin cho khách trong kênh quản trị phải qua XÁC NHẬN MẠNH (spec r6 §8.1):
mở khoá hôm nay (Console + PIN) hoặc mã kép qua kênh phụ («OK #MÃ 4821»), sai 3 lần khoá 1 giờ — không lách bằng đổi kênh/phiên.
Phòng thủ chiều sâu: phiên `chat_ok` duyệt nháp (`POST /drafts/{id}/approve`) phải mang dấu "đã qua xác nhận mạnh" (một lần)."""

from __future__ import annotations

import re
import time
import uuid
from typing import Any

import pytest
from sqlalchemy import text

from gh.biz.intel import strong_confirm as sc
from gh.gen import chat_pending, proposals
from gh.owner_channel import chat_confirm
from gh.owner_channel import inbound as oin
from tests.conftest import Api
from tests.owner_helpers import OWNER_ZALO, ago, bot_world
from tests.test_chat_confirm_v0173 import Chat, chat, count, outbound_proposal  # noqa: F401 — fixture `chat`
from tests.test_p3_core import _outbound, world  # noqa: F401 — fixture `world`
from tests.test_strong_confirm_v0176 import last_code, security_rows, set_quiet, wrong
from tests.test_watchdog_backup_v0176 import _telegram


@pytest.fixture
async def calm(chat: Chat, db: Any) -> Chat:
    """Chat ngoài giờ yên lặng (test không phụ thuộc giờ chạy)."""
    await set_quiet(db, chat.org, now_quiet=False)
    return chat


async def owner_id(db: Any, org: uuid.UUID) -> uuid.UUID:
    return (await db.execute(text("SELECT owner_user_id FROM ops.owner_channels WHERE org_id = :o AND channel_type = 'zalo'"),  # type: ignore[no-any-return]
                             {"o": org})).scalar_one()


async def unlock(chat: Chat, db: Any) -> None:
    assert await sc.unlock_today(db, chat.org, user_id=await owner_id(db, chat.org), pin_verified=True)
    await db.commit()


# ─── phân tích ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,expect", [
    ("OK #A3F9C2 4821", ("ok", "A3F9C2", "4821")), ("ok a3f9c2 0042", ("ok", "A3F9C2", "0042")),
    ("OK #A3F9C2", ("ok", "A3F9C2", None)), ("ok", ("ok", None, None)),
    ("OK #A3F9C2 4821 9999", None), ("OK #A3F9C2 482", None), ("huỷ #A3F9C2 4821", None),
])
def test_parse_double_code(raw: str, expect: tuple[str, str | None, str | None] | None) -> None:
    got = chat_confirm.parse_decision(raw)
    assert (None if got is None else (got.verb, got.code, got.pin2)) == expect


# ─── mã kép ──────────────────────────────────────────────────────────────────

async def test_ok_code_sends_double_code_on_backup_then_right_code_sends(calm: Chat, world: dict[str, Any], db: Any) -> None:
    await _telegram(db, calm.org)
    p = await outbound_proposal(calm, world)
    code = chat_pending.code_of(p["id"])
    reply = await calm.say(f"OK {code}")
    assert "mã 4 số" in reply and f"OK {code} " in reply and "Telegram" in reply
    assert await _outbound(calm.redis) == [] and await count("biz.action_drafts") == 0
    pin = await last_code(db, calm.org)
    assert pin not in reply
    bad = await calm.say(f"OK {code} {wrong(pin)}")
    assert "chưa đúng" in bad and "2" in bad and await _outbound(calm.redis) == []
    ok = await calm.say(f"OK {code} {pin}")
    assert ok.startswith(f"Đã gửi {code}"), ok
    [d] = await _outbound(calm.redis)
    assert d["text"] == "Dạ chào chị Lan, bên em báo giá 12 triệu ạ."


async def test_three_wrong_codes_across_zalo_and_whatsapp_lock_and_right_code_is_refused(calm: Chat, world: dict[str, Any], db: Any, app: Any, redis: Any) -> None:
    await _telegram(db, calm.org)
    p = await outbound_proposal(calm, world)
    code = chat_pending.code_of(p["id"])
    await calm.say(f"OK {code}")
    pin = await last_code(db, calm.org)
    await calm.say(f"OK {code} {wrong(pin)}")
    await calm.say(f"OK {code} {wrong(pin)}")
    # đổi kênh (WhatsApp, phiên khác) không làm mới số lần sai
    wa_sid = await bot_world(db, calm.org, "whatsapp")
    wa = Chat(app, redis, calm.org, wa_sid, "whatsapp")
    await chat_pending.register(redis, calm.org, "whatsapp", p["id"])
    locked = await wa.say(f"OK {code} {wrong(pin)}")
    assert "tạm khoá" in locked
    assert "tạm khoá" in await calm.say(f"OK {code} {pin}")
    assert await _outbound(calm.redis) == [] and await count("biz.action_drafts") == 0
    assert any("sai mã kép 3 lần" in r.text for r in await security_rows(db, calm.org))
    assert (await proposals.load(redis, p["id"]))["status"] == "pending"


async def test_code_for_one_proposal_does_not_send_another(calm: Chat, world: dict[str, Any], db: Any) -> None:
    await _telegram(db, calm.org)
    a = await outbound_proposal(calm, world, "Tin A", "A")
    b = await outbound_proposal(calm, world, "Tin B", "B")
    await calm.say(f"OK {chat_pending.code_of(a['id'])}")
    pin_a = await last_code(db, calm.org)
    reply = await calm.say(f"OK {chat_pending.code_of(b['id'])} {pin_a}")
    assert "mã 4 số" in reply and await _outbound(calm.redis) == []       # B chưa có mã ⇒ gửi mã mới, không gửi tin
    assert await last_code(db, calm.org) is not None


async def test_unlock_today_lets_ok_code_send_directly(calm: Chat, world: dict[str, Any], db: Any) -> None:
    await _telegram(db, calm.org)
    await unlock(calm, db)
    p = await outbound_proposal(calm, world)
    code = chat_pending.code_of(p["id"])
    assert (await calm.say(f"OK {code}")).startswith(f"Đã gửi {code}")
    assert len(await _outbound(calm.redis)) == 1


async def test_no_backup_and_not_unlocked_points_to_console_and_sends_nothing(calm: Chat, world: dict[str, Any]) -> None:
    p = await outbound_proposal(calm, world)
    reply = await calm.say(f"OK {chat_pending.code_of(p['id'])}")
    assert "Mở khoá hôm nay" in reply and await _outbound(calm.redis) == [] and await count("biz.action_drafts") == 0


async def test_ok_code_in_quiet_hours_locks_outbound(chat: Chat, world: dict[str, Any], db: Any) -> None:
    await set_quiet(db, chat.org, now_quiet=True)
    await unlock(chat, db)
    p = await outbound_proposal(chat, world)
    assert "tạm khoá" in await chat.say(f"OK {chat_pending.code_of(p['id'])}")
    assert await _outbound(chat.redis) == [] and await sc.outbound_locked_until(db, chat.org) is not None


async def test_bare_ok_still_refused_for_outbound_even_when_unlocked(calm: Chat, world: dict[str, Any], db: Any) -> None:
    await unlock(calm, db)
    await outbound_proposal(calm, world)
    assert "cần Sếp trả lời kèm mã" in await calm.say("OK")
    assert await _outbound(calm.redis) == []


async def test_expired_proposal_with_code_does_nothing(calm: Chat, world: dict[str, Any], db: Any) -> None:
    await unlock(calm, db)
    p = await outbound_proposal(calm, world)
    await calm.redis.delete(proposals.key(p["id"]))
    reply = await calm.say(f"OK {chat_pending.code_of(p['id'])} 1234")
    assert "hết hạn" in reply and await _outbound(calm.redis) == []


async def test_proposal_of_another_user_cannot_be_confirmed(calm: Chat, world: dict[str, Any], db: Any) -> None:
    await unlock(calm, db)
    p = await outbound_proposal(calm, world)
    p["user_id"] = str(uuid.uuid4())
    await proposals.save(calm.redis, p)
    assert "không thấy đề xuất" in await calm.say(f"OK {chat_pending.code_of(p['id'])}")
    assert await _outbound(calm.redis) == []


async def test_forwarded_or_same_name_stranger_ok_with_code_never_reaches_consumer(calm: Chat, world: dict[str, Any], db: Any, redis: Any) -> None:
    from gh.db import sessionmaker

    p = await outbound_proposal(calm, world)
    body = f"OK {chat_pending.code_of(p['id'])} 1234"
    base = {"channel": "zalo", "session_id": str(calm.sid), "occurred_at": ago(seconds=1), "kind": "text", "body_text": body,
            "sender_name": "Anh Cơ"}
    async with sessionmaker()() as s:
        assert not await oin.handle_owner_dm(s, redis, calm.org, {**base, "external_msg_id": "fw", "sender_external_id": OWNER_ZALO, "forwarded": True})
        assert not await oin.handle_owner_dm(s, redis, calm.org, {**base, "external_msg_id": "st", "sender_external_id": "999999"})
        assert oin.take_pending(s) == []


# ─── phòng thủ chiều sâu ở route duyệt nháp ──────────────────────────────────

async def _pending_draft(owner_api: Api, world: dict[str, Any]) -> str:
    r = await owner_api.send("POST", "/drafts", {"kind": "message", "title": "Báo giá", "text": "Dạ em gửi anh báo giá.",
                                                 "target": {"channel": "zalo", "thread_type": "group", "group_id": str(world["group"])}})
    assert r.status_code == 201, r.text
    return str(r.json()["id"])


async def test_chat_ok_session_cannot_approve_draft_without_strong_pass(calm: Chat, world: dict[str, Any], owner_api: Api, db: Any, app: Any) -> None:
    from gh.db import sessionmaker

    did = await _pending_draft(owner_api, world)
    oid = await owner_id(db, calm.org)
    ses = await chat_confirm._mint(sessionmaker(), calm.org, oid, "zalo", pin=True)
    try:
        status, body = await chat_confirm._call(app, ses, "POST", f"/drafts/{did}/approve", {})
        assert status == 423 and body["code"] == "STRONG_CONFIRM_REQUIRED", body
        assert await _outbound(calm.redis) == []
        await calm.redis.set(sc.pass_key(ses.id), "1", ex=60)
        status, body = await chat_confirm._call(app, ses, "POST", f"/drafts/{did}/approve", {})
        assert status == 200, body
        assert not await calm.redis.exists(sc.pass_key(ses.id))          # dấu dùng một lần
    finally:
        await chat_confirm._revoke(sessionmaker(), calm.org, ses.id)
    assert len(await _outbound(calm.redis)) == 1


async def test_console_owner_with_pin_still_approves_normally(world: dict[str, Any], owner_api: Api) -> None:
    from tests.conftest import verify_pin

    did = await _pending_draft(owner_api, world)
    await verify_pin(owner_api)
    r = await owner_api.send("POST", f"/drafts/{did}/approve", {})
    assert r.status_code == 200, r.text


def test_reply_texts_never_contain_a_code_placeholder() -> None:
    for s in (chat_confirm.CODE_SENT, chat_confirm.WRONG_CODE):
        assert "{pin}" not in s
    assert re.search(r"\{code\}", chat_confirm.CODE_SENT)
    assert time.time() > 0


async def test_ok_b_then_ok_a_invalidates_b_code(calm: Chat, world: dict[str, Any], db: Any) -> None:
    await _telegram(db, calm.org)
    a = await outbound_proposal(calm, world, "Tin A", "A")
    b = await outbound_proposal(calm, world, "Tin B", "B")
    await calm.say(f"OK {chat_pending.code_of(b['id'])}")
    pin_b = await last_code(db, calm.org)
    await calm.say(f"OK {chat_pending.code_of(a['id'])}")
    reply = await calm.say(f"OK {chat_pending.code_of(b['id'])} {pin_b}")
    assert "mã 4 số" in reply and await _outbound(calm.redis) == []
    from tests.test_strong_confirm_v0176 import last_code_text

    t = await last_code_text(db, calm.org)
    assert "Nhóm g1" in t and "Tin B" in t
