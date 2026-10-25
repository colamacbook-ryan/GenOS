# ruff: noqa: E501, F811
"""v0.1.76 (gói T2) — đường cũ đóng ask CÙNG transaction (spec r4 §4.4 "Luật Ask", M7):
`POST /gen/proposals/{id}/confirm|cancel` ⇒ ask `proposal` `executed|cancelled`; `POST /drafts/{id}/approve|reject` ⇒ ask `draft`
`executed|cancelled`. IDOR: ask cùng `ref_id` của tổ chức khác không bị đụng."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import text

from gh.biz.intel import asks
from gh.gen import chat_pending
from tests.conftest import Api, verify_pin
from tests.mem_helpers import mk_org
from tests.test_chat_confirm_v0173 import Chat, chat, reminder  # noqa: F401 — fixture `chat`
from tests.test_p3_core import world  # noqa: F401 — fixture `world`
from tests.test_telegram_commands_v0164 import Router


async def ask_state(db: Any, org: uuid.UUID, source: str, ref: str | uuid.UUID) -> str:
    await db.rollback()
    return (await db.execute(text("SELECT state FROM ops.asks WHERE org_id = :o AND source = :s AND ref_id = :r"),  # type: ignore[no-any-return]
                             {"o": org, "s": source, "r": uuid.UUID(str(ref))})).scalar_one()


async def mk_ask(db: Any, org: uuid.UUID, source: str, ref: str | uuid.UUID, kind: str = "internal") -> None:
    await asks.create(db, org, source=source, ref_id=uuid.UUID(str(ref)), decision_kind=kind, summary="Việc thử")
    await db.commit()


async def test_confirm_proposal_closes_its_ask_executed_and_other_org_untouched(chat: Chat, db: Any) -> None:
    await chat.say("/nhap nhắc tôi gọi anh Nam", Router(reminder("Gọi anh Nam")))
    [pid] = await chat_pending.entries(chat.redis, chat.org, "zalo")
    await mk_ask(db, chat.org, "proposal", pid)
    other = await mk_org(db)
    await mk_ask(db, other, "proposal", pid)
    assert (await chat.say("OK")).startswith("Đã làm xong")
    assert await ask_state(db, chat.org, "proposal", pid) == "executed"
    assert await ask_state(db, other, "proposal", pid) == "open"


async def test_cancel_proposal_closes_its_ask_cancelled(chat: Chat, db: Any) -> None:
    await chat.say("/nhap nhắc tôi gọi anh Nam", Router(reminder("Gọi anh Nam")))
    [pid] = await chat_pending.entries(chat.redis, chat.org, "zalo")
    await mk_ask(db, chat.org, "proposal", pid)
    assert (await chat.say("huỷ")).startswith("Đã huỷ")
    assert await ask_state(db, chat.org, "proposal", pid) == "cancelled"


async def _draft(owner_api: Api, world: dict[str, Any]) -> str:
    r = await owner_api.send("POST", "/drafts", {"kind": "message", "title": "Báo giá", "text": "Dạ em gửi anh báo giá.",
                                                 "target": {"channel": "zalo", "thread_type": "group", "group_id": str(world["group"])}})
    assert r.status_code == 201, r.text
    return str(r.json()["id"])


async def test_draft_approve_and_reject_close_their_asks(world: dict[str, Any], owner_api: Api, db: Any) -> None:
    a, b = await _draft(owner_api, world), await _draft(owner_api, world)
    for d in (a, b):
        await mk_ask(db, world["org"], "draft", d, "outbound")
    await verify_pin(owner_api)
    assert (await owner_api.send("POST", f"/drafts/{a}/approve", {})).status_code == 200
    assert (await owner_api.send("POST", f"/drafts/{b}/reject", {"reason": "không gửi"})).status_code == 200
    assert await ask_state(db, world["org"], "draft", a) == "executed"
    assert await ask_state(db, world["org"], "draft", b) == "cancelled"


async def test_failed_approve_leaves_ask_open(world: dict[str, Any], owner_api: Api, db: Any) -> None:
    a = await _draft(owner_api, world)
    await mk_ask(db, world["org"], "draft", a, "outbound")
    r = await owner_api.send("POST", f"/drafts/{a}/approve", {})           # chưa PIN ⇒ 423, ask vẫn mở
    assert r.status_code == 423
    assert await ask_state(db, world["org"], "draft", a) == "open"
