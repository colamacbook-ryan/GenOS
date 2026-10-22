"""v0.1.41 (F-84): đánh giá "Hữu ích / Không hữu ích" cho câu trả lời Gen và Bản tin Gen (agent.gen_feedback — 0027).

- PUT /gen/feedback lưu một dòng mỗi (người, lượt); chấm lại thì đổi đánh giá; DELETE bỏ chấm.
- Chỉ chấm được câu trả lời trong hội thoại của chính mình; lượt không có ⇒ 404; rating lạ ⇒ 422.
- Tin content kind='briefing' ⇒ kind 'briefing'; danh sách hội thoại gắn nhãn 'briefing' / 'chat'.
- Xoá hội thoại ⇒ đánh giá xoá theo (CASCADE). SQL 0027 chạy lại an toàn.
"""

import uuid
from pathlib import Path
from typing import Any

import orjson
import psycopg
from sqlalchemy import text

from gh.db import admin_sessionmaker
from tests.conftest import OWNER, PG, Api

SQL_FILE = Path(__file__).resolve().parents[3] / "db" / "sql" / "0027_v0141_gen_feedback_costs.sql"


async def _conversation(content: dict[str, Any], *, email: str = OWNER["email"], title: str = "Hỏi Gen"
                        ) -> tuple[str, str]:
    """Tạo hội thoại + một tin hỏi + một tin trả lời (turn_id chung) cho người dùng `email`. Trả (conversation_id,
    turn_id)."""
    turn = uuid.uuid4()
    async with admin_sessionmaker()() as db:
        org, uid = (await db.execute(text("SELECT org_id, id FROM core.users WHERE email = :e"), {"e": email})).one()
        cid = (await db.execute(text("""INSERT INTO agent.gen_conversations (org_id, user_id, title)
                                        VALUES (:o, :u, :t) RETURNING id"""),
                                {"o": org, "u": uid, "t": title})).scalar_one()
        for role, body in (("user", {"text": "Hôm nay thế nào?"}), ("assistant", content)):
            await db.execute(text("""INSERT INTO agent.gen_messages (org_id, conversation_id, turn_id, role, content)
                                     VALUES (:o, :c, :t, :r, CAST(:x AS jsonb))"""),
                             {"o": org, "c": cid, "t": turn, "r": role, "x": orjson.dumps(body).decode()})
        await db.commit()
    return str(cid), str(turn)


async def _rows() -> list[Any]:
    async with admin_sessionmaker()() as db:
        return list((await db.execute(text("SELECT turn_id, kind, rating FROM agent.gen_feedback"))).all())


def _feedback_of(msgs: list[dict[str, Any]], role: str) -> Any:
    return next(m["feedback"] for m in msgs if m["role"] == role)


async def test_put_change_and_delete_feedback(owner_api: Api) -> None:
    cid, turn = await _conversation({"steps": [{"kind": "say", "text": "Dạ ổn ạ"}]})
    r = await owner_api.send("PUT", "/gen/feedback", {"conversation_id": cid, "turn_id": turn, "rating": "helpful"})
    assert r.status_code == 200, r.text
    assert r.json() == {"turn_id": turn, "rating": "helpful", "kind": "reply"}
    rows = await _rows()
    assert len(rows) == 1 and rows[0].rating == "helpful" and rows[0].kind == "reply"

    msgs = (await owner_api.get(f"/gen/conversations/{cid}/messages")).json()
    assert _feedback_of(msgs, "assistant") == "helpful"
    assert _feedback_of(msgs, "user") is None  # chỉ tin trả lời mang đánh giá

    r = await owner_api.send("PUT", "/gen/feedback",
                             {"conversation_id": cid, "turn_id": turn, "rating": "not_helpful"})
    assert r.status_code == 200 and r.json()["rating"] == "not_helpful"
    rows = await _rows()
    assert len(rows) == 1 and rows[0].rating == "not_helpful"
    msgs = (await owner_api.get(f"/gen/conversations/{cid}/messages")).json()
    assert _feedback_of(msgs, "assistant") == "not_helpful"

    r = await owner_api.send("DELETE", f"/gen/feedback/{turn}")
    assert r.status_code == 204
    assert await _rows() == []
    msgs = (await owner_api.get(f"/gen/conversations/{cid}/messages")).json()
    assert _feedback_of(msgs, "assistant") is None
    # Bỏ chấm lần nữa (không còn gì) vẫn 204.
    assert (await owner_api.send("DELETE", f"/gen/feedback/{turn}")).status_code == 204


async def test_feedback_is_private_metric_not_action_log(owner_api: Api) -> None:
    """Số đo riêng tư (như đánh dấu đã đọc thông báo) — không ghi dòng `http.*` vào Nhật ký."""
    cid, turn = await _conversation({"steps": []})
    async with admin_sessionmaker()() as db:
        before = (await db.execute(text("SELECT count(*) FROM ops.action_log"))).scalar_one()
    r = await owner_api.send("PUT", "/gen/feedback", {"conversation_id": cid, "turn_id": turn, "rating": "helpful"})
    assert r.status_code == 200
    assert (await owner_api.send("DELETE", f"/gen/feedback/{turn}")).status_code == 204
    async with admin_sessionmaker()() as db:
        after = (await db.execute(text("SELECT count(*) FROM ops.action_log"))).scalar_one()
    assert after == before


async def test_feedback_rejects_foreign_missing_and_bad_rating(owner_api: Api) -> None:
    async with admin_sessionmaker()() as db:
        org = (await db.execute(text("SELECT id FROM core.organizations"))).scalar_one()
        await db.execute(text("""INSERT INTO core.users (org_id, email, display_name, password_hash)
                                 VALUES (:o, 'khac@example.vn', 'Khác', 'x')"""), {"o": org})
        await db.commit()
    foreign_cid, foreign_turn = await _conversation({"steps": []}, email="khac@example.vn")
    r = await owner_api.send("PUT", "/gen/feedback",
                             {"conversation_id": foreign_cid, "turn_id": foreign_turn, "rating": "helpful"})
    assert r.status_code == 404 and r.json()["code"] == "NOT_FOUND"
    assert "Hội thoại" in r.json()["title"]

    cid, turn = await _conversation({"steps": []})
    r = await owner_api.send("PUT", "/gen/feedback",
                             {"conversation_id": cid, "turn_id": str(uuid.uuid4()), "rating": "helpful"})
    assert r.status_code == 404 and "Câu trả lời" in r.json()["title"]
    # turn_id của hội thoại KHÁC (của chính mình) cũng không chấm qua hội thoại này được.
    _, turn2 = await _conversation({"steps": []})
    r = await owner_api.send("PUT", "/gen/feedback", {"conversation_id": cid, "turn_id": turn2, "rating": "helpful"})
    assert r.status_code == 404

    r = await owner_api.send("PUT", "/gen/feedback", {"conversation_id": cid, "turn_id": turn, "rating": "tuyet"})
    assert r.status_code == 422
    # Xoá đánh giá của người khác: không chạm dòng của họ.
    async with admin_sessionmaker()() as db:
        org, other = (await db.execute(text("SELECT org_id, id FROM core.users WHERE email = 'khac@example.vn'"))
                      ).one()
        await db.execute(text("""INSERT INTO agent.gen_feedback (org_id, user_id, conversation_id, turn_id, kind,
                                                                 rating)
                                 VALUES (:o, :u, :c, :t, 'reply', 'helpful')"""),
                         {"o": org, "u": other, "c": foreign_cid, "t": foreign_turn})
        await db.commit()
    assert (await owner_api.send("DELETE", f"/gen/feedback/{foreign_turn}")).status_code == 204
    assert len(await _rows()) == 1


async def test_briefing_kind_and_conversation_label(owner_api: Api) -> None:
    chat_cid, _ = await _conversation({"steps": [{"kind": "say", "text": "Dạ"}]})
    brief_cid, brief_turn = await _conversation(
        {"kind": "briefing", "slot": "2026-10-02T07:30:00+07:00", "slot_label": "sáng 02/10",
         "summary_source": "none", "needs_api_key": True, "sections": [],
         "steps": [{"kind": "tool", "name": "briefing.sources"}]},
        title="Bản tin Gen · sáng 02/10")
    r = await owner_api.send("PUT", "/gen/feedback",
                             {"conversation_id": brief_cid, "turn_id": brief_turn, "rating": "helpful"})
    assert r.status_code == 200 and r.json()["kind"] == "briefing"
    rows = await _rows()
    assert rows[0].kind == "briefing"

    convs = {c["id"]: c for c in (await owner_api.get("/gen/conversations")).json()}
    assert convs[brief_cid]["kind"] == "briefing"
    assert convs[chat_cid]["kind"] == "chat"


async def test_delete_conversation_cascades_feedback(owner_api: Api) -> None:
    cid, turn = await _conversation({"steps": []})
    r = await owner_api.send("PUT", "/gen/feedback", {"conversation_id": cid, "turn_id": turn, "rating": "helpful"})
    assert r.status_code == 200
    assert len(await _rows()) == 1
    assert (await owner_api.send("DELETE", f"/gen/conversations/{cid}")).status_code == 204
    assert await _rows() == []


async def test_migration_0027_is_rerunnable(fresh_db: str) -> None:
    sql = SQL_FILE.read_text(encoding="utf-8")
    with psycopg.connect(f"{PG}/{fresh_db}", autocommit=True) as c:
        c.execute(sql)  # type: ignore[call-overload]
        c.execute(sql)  # type: ignore[call-overload]
        n = c.execute("""SELECT count(*) FROM pg_policies
                         WHERE tablename IN ('gen_feedback', 'model_prices') AND policyname = 'org_isolation'"""
                      ).fetchone()
    assert n is not None and n[0] == 2
