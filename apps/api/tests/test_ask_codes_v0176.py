# ruff: noqa: E501, F811
"""v0.1.76 (gói T2) — một hàng Cần quyết (spec r4 §4.4 / §4.6): mỗi đề xuất (kênh quản trị + Telegram), mỗi nháp chờ duyệt và mỗi câu hỏi
`owner_questions` sinh MỘT ask `ops.asks` với mã ngẫu nhiên 5 ký tự (`A-HJ-NP-Z2-9`). Kênh chat hiện mã ask; «OK #MÃ» nhận mã ask
(mã hex cũ vẫn nhận trong bản chuyển tiếp). Ánh xạ nháp → `decision_kind` theo bảng §4.4."""

from __future__ import annotations

import re
import uuid
from typing import Any

import pytest
from sqlalchemy import text

from gh.biz.core import drafts
from gh.biz.intel import asks
from gh.gen import chat_pending, proposals
from gh.owner_channel import chat_confirm, questions
from tests.conftest import Api
from tests.mem_helpers import mk_org
from tests.test_chat_confirm_v0173 import MEMORY, Chat, chat, count, reminder  # noqa: F401 — fixture `chat`
from tests.test_p3_core import world  # noqa: F401 — fixture `world`
from tests.test_telegram_commands_v0164 import Router, _reminder, bot, cmd, ctx_for  # noqa: F401 — fixture `bot`

ASK_CODE = re.compile(r"#([A-HJ-NP-Z2-9]{5})\b")


async def asks_of(db: Any, org: uuid.UUID, source: str | None = None) -> list[Any]:
    await db.rollback()
    return list((await db.execute(text("""SELECT code, source, ref_id, decision_kind, state, summary, created_via FROM ops.asks
                                          WHERE org_id = :o AND (CAST(:s AS text) IS NULL OR source = :s) ORDER BY created_at"""),
                                  {"o": org, "s": source})).all())


# ─── phân tích mã ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,expect", [
    ("OK #K7QX4", ("ok", "K7QX4")), ("ok k7qx4", ("ok", "K7QX4")), ("huỷ #K7QX4", ("no", "K7QX4")),
    ("ok #ABCDE", ("ok", "ABCDE")), ("ok nhanh", None), ("ok luon", ("ok", None)), ("ok xong", None),
    ("OK #A3F9C2", ("ok", "A3F9C2")),
])
def test_parse_accepts_ask_codes(raw: str, expect: tuple[str, str | None] | None) -> None:
    got = chat_confirm.parse_decision(raw)
    assert (None if got is None else (got.verb, got.code)) == expect


# ─── đề xuất ──────────────────────────────────────────────────────────────────

async def test_chat_proposal_creates_ask_and_reply_shows_its_code(chat: Chat, db: Any) -> None:
    reply = await chat.say("/nhap nhắc tôi gọi anh Nam", Router(reminder("Gọi anh Nam")))
    [a] = await asks_of(db, chat.org, "proposal")
    assert a.state == "open" and a.created_via == "zalo" and a.decision_kind == "self"
    assert f"#{a.code}" in reply and ASK_CODE.search(reply)
    pid = (await chat_pending.entries(chat.redis, chat.org, "zalo"))[0]
    assert str(a.ref_id) == pid and (await proposals.load(chat.redis, pid))["ask_code"] == a.code
    assert (await chat.say(f"OK #{a.code}")).startswith(f"Đã làm xong #{a.code}")
    [a2] = await asks_of(db, chat.org, "proposal")
    assert a2.state == "executed"


async def test_legacy_hex_code_still_confirms(chat: Chat, db: Any) -> None:
    await chat.say("/nhap nhắc tôi gọi anh Nam", Router(reminder("Gọi anh Nam")))
    pid = (await chat_pending.entries(chat.redis, chat.org, "zalo"))[0]
    hexcode = "#" + pid.replace("-", "")[:6].upper()
    assert (await chat.say(f"OK {hexcode}")).startswith("Đã làm xong")
    assert await count("biz.tasks") == 1


async def test_ask_code_of_other_org_does_not_confirm(chat: Chat, db: Any) -> None:
    await chat.say("/nhap nhắc tôi gọi anh Nam", Router(reminder("Gọi anh Nam")))
    other = await mk_org(db)
    a = await asks.create(db, other, source="signal", ref_id=uuid.uuid4(), decision_kind="internal", summary="lạ")
    await db.commit()
    assert "không thấy" in await chat.say(f"OK #{a.code}")
    assert await count("biz.tasks") == 0


async def test_memory_note_proposal_is_self_kind(chat: Chat, db: Any) -> None:
    await chat.say("/nhap nhớ giúp tôi thích báo cáo ngắn", Router(MEMORY))
    [a] = await asks_of(db, chat.org, "proposal")
    assert a.decision_kind == "self"


async def test_telegram_nhap_creates_ask_too(owner_api: Api, bot: Any, redis: Any, db: Any) -> None:
    ctx = await ctx_for(owner_api, bot, redis, Router(_reminder()))
    from gh.telegram import commands

    reply, _ = await commands.handle(cmd("nhap", "nhắc tôi gọi anh Nam"), ctx)
    [a] = await asks_of(db, ctx.org_id, "proposal")
    assert a.created_via == "telegram" and a.state == "open" and reply.startswith("Đã soạn đề xuất #")


# ─── nháp ─────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("kind,body_len,expect", [("message", 100, "outbound"), ("message", 1600, "console"),
                                                  ("quotation", 100, "console"), ("reminder", 50, "internal")])
async def test_pending_draft_creates_ask_with_mapped_decision_kind(world: dict[str, Any], owner_api: Api, db: Any,
                                                                  kind: str, body_len: int, expect: str) -> None:
    body: dict[str, Any] = {"kind": kind, "title": "Nháp", "text": "x" * body_len}
    if kind == "message":
        body["target"] = {"channel": "zalo", "thread_type": "group", "group_id": str(world["group"])}
    r = await owner_api.send("POST", "/drafts", body)
    assert r.status_code == 201, r.text
    d = r.json()
    rows = [a for a in await asks_of(db, world["org"], "draft") if str(a.ref_id) == d["id"]]
    if d["status"] != drafts.PENDING:
        assert rows == []
        return
    [a] = rows
    assert a.decision_kind == expect and a.state == "open" and "x" * 20 not in a.summary


def test_draft_decision_kind_table() -> None:
    f = drafts.ask_decision_kind
    assert f("message", has_target=True, text_len=1500, over_threshold=False) == "outbound"
    assert f("message", has_target=True, text_len=1501, over_threshold=False) == "console"
    assert f("message", has_target=True, text_len=10, over_threshold=True) == "console"
    assert f("message", has_target=False, text_len=10, over_threshold=False) == "console"
    for k in ("quotation", "contract", "mcp_write", "report"):
        assert f(k, has_target=True, text_len=10, over_threshold=False) == "console"
    assert f("reminder", has_target=False, text_len=10, over_threshold=False) == "internal"


# ─── câu hỏi owner_questions ─────────────────────────────────────────────────

async def test_owner_question_creates_ask_and_answer_closes_it(db: Any) -> None:
    org = await mk_org(db)
    q = await questions.create(db, org, kind="listen_confirm", subject_id=None, payload={})
    await db.commit()
    [a] = await asks_of(db, org, "question")
    assert a.ref_id == q.id and a.state == "open" and len(a.code) == 5
    assert await questions.answer(db, q.id, "nghe")
    await db.commit()
    [a] = await asks_of(db, org, "question")
    assert a.state == "executed"
