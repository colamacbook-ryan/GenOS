# ruff: noqa: E501, F811
"""v0.1.76 (gói T2) — `/viec` liệt kê hàng Cần quyết (asks mở: mã, tóm tắt, loại quyết định) và «hoàn tác #MÃ» (spec r4 §4.4, W8):
chỉ ask `executed` ∧ `decision_kind='self'` ∧ quyết trong 10 phút, lấy id tài nguyên từ `ask.result`, đi đúng đường cũ
(`PATCH /tasks/{id}` status=cancelled · `DELETE /gen/memory/{id}`) bằng phiên `chat_ok` ngắn hạn.
Tấn công: hoàn tác hai lần, quá 10 phút, ask không phải `self`, mã của tổ chức khác ⇒ không làm gì."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import text

from gh.owner_channel import chat_confirm
from tests.mem_helpers import mk_org
from tests.test_ask_codes_v0176 import asks_of
from tests.test_chat_confirm_v0173 import MEMORY, Chat, chat, count, reminder  # noqa: F401 — fixture `chat`
from tests.test_p3_core import world  # noqa: F401 — fixture `world`
from tests.test_telegram_commands_v0164 import Router


async def done_reminder(chat: Chat, db: Any) -> str:
    await chat.say("/nhap nhắc tôi gọi anh Nam", Router(reminder("Gọi anh Nam")))
    [a] = await asks_of(db, chat.org, "proposal")
    assert (await chat.say(f"OK #{a.code}")).startswith("Đã làm xong")
    return str(a.code)


async def task_status(db: Any) -> str:
    await db.rollback()
    return (await db.execute(text("SELECT status FROM biz.tasks ORDER BY created_at DESC LIMIT 1"))).scalar_one()  # type: ignore[no-any-return]


def test_parse_undo() -> None:
    d = chat_confirm.parse_decision("hoàn tác #K7QX4")
    assert d is not None and (d.verb, d.code) == ("undo", "K7QX4")
    assert chat_confirm.parse_decision("hoàn tác") is None or chat_confirm.parse_decision("hoàn tác").code is None  # type: ignore[union-attr]


async def test_viec_lists_open_asks_with_code_and_kind(chat: Chat, db: Any) -> None:
    await chat.say("/nhap nhắc tôi gọi anh Nam", Router(reminder("Gọi anh Nam")))
    [a] = await asks_of(db, chat.org, "proposal")
    reply = await chat.say("/viec")
    assert "Chờ Sếp quyết (1)" in reply and f"#{a.code}" in reply and "Gọi anh Nam" in reply and "việc riêng" in reply


async def test_viec_without_asks_is_unchanged(chat: Chat) -> None:
    assert "Chờ Sếp quyết" not in await chat.say("/viec")


async def test_undo_reminder_within_ten_minutes_cancels_task_once(chat: Chat, db: Any) -> None:
    code = await done_reminder(chat, db)
    assert await task_status(db) == "todo"
    reply = await chat.say(f"hoàn tác #{code}")
    assert reply.startswith(f"Đã hoàn tác #{code}"), reply
    assert await task_status(db) == "cancelled"
    assert "không hoàn tác được" in await chat.say(f"hoàn tác #{code}")           # lần hai: không làm gì
    await db.rollback()
    [log] = (await db.execute(text("SELECT detail FROM ops.action_log WHERE action = 'gen.proposal_chat_undone'"))).scalars().all()
    assert log["via"] == "zalo" and log["type"] == "task"


async def test_undo_memory_note_deletes_it(chat: Chat, db: Any) -> None:
    await chat.say("/nhap nhớ giúp tôi thích báo cáo ngắn", Router(MEMORY))
    [a] = await asks_of(db, chat.org, "proposal")
    await chat.say(f"OK #{a.code}")
    assert await count("agent.gen_memory_notes") == 1
    assert (await chat.say(f"hoàn tác #{a.code}")).startswith("Đã hoàn tác")
    assert await count("agent.gen_memory_notes") == 0


async def test_undo_after_ten_minutes_is_refused(chat: Chat, db: Any) -> None:
    code = await done_reminder(chat, db)
    await db.execute(text("UPDATE ops.asks SET decided_at = now() - interval '11 minutes' WHERE code = :c"), {"c": code})
    await db.commit()
    assert "không hoàn tác được" in await chat.say(f"hoàn tác #{code}")
    assert await task_status(db) == "todo"


async def test_undo_non_self_ask_is_refused(chat: Chat, db: Any) -> None:
    code = await done_reminder(chat, db)
    await db.execute(text("UPDATE ops.asks SET decision_kind = 'internal' WHERE code = :c"), {"c": code})
    await db.commit()
    assert "không hoàn tác được" in await chat.say(f"hoàn tác #{code}")
    assert await task_status(db) == "todo"


async def test_undo_code_of_other_org_is_refused(chat: Chat, db: Any) -> None:
    code = await done_reminder(chat, db)
    other = await mk_org(db)
    await db.execute(text("""INSERT INTO ops.asks (org_id, code, source, ref_id, decision_kind, summary, state, decided_at, result)
                             VALUES (:o, 'ZZ9ZZ', 'proposal', :r, 'self', 'x', 'executed', now(), '{"type": "task"}')"""),
                     {"o": other, "r": uuid.uuid4()})
    await db.commit()
    assert "không hoàn tác được" in await chat.say("hoàn tác #ZZ9ZZ")
    assert await task_status(db) == "todo" and code
