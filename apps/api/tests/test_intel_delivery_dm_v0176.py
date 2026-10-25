# ruff: noqa: E501, F811
"""v0.1.76 (gói T3) — lệnh tình báo trong kênh quản trị 1-1 (`dm.py`): «sai N / đúng N / không cần báo N», «em sót: …», «sửa N: …»,
mute (hạ phòng thủ ⇒ `strong_confirm.require` ⇒ chỉ Console), «tắt bản tin»/đổi giờ yên lặng (báo kênh phụ), «chạy thử» (chỉ Console),
đầu ra gen_chat che STK/SĐT "0123…789", đang khoá trợ lý ⇒ im."""

from __future__ import annotations

import time
import uuid
from datetime import timedelta
from typing import Any

import orjson
import pytest
from sqlalchemy import text

from gh.biz.mem import facts
from gh.chassis.bus import BRIDGE_OUTBOUND
from gh.db import sessionmaker
from gh.owner_channel import dm, gen_chat
from gh.owner_cmd import core as owner_core
from tests.mem_helpers import mk_entity, mkfact
from tests.owner_helpers import ago
from tests.test_chat_confirm_v0173 import Chat, chat  # noqa: F401 — fixture `chat`
from tests.test_intel_delivery_reports_v0176 import _lock
from tests.test_intel_digest_rawtail_v0176 import NOW, _install, _sig
from tests.test_p3_core import world  # noqa: F401 — fixture `world`
from tests.test_strong_confirm_v0176 import set_quiet
from tests.test_watchdog_backup_v0176 import _telegram

SM = sessionmaker


async def put_refs(db: Any, org: uuid.UUID, ids: list[uuid.UUID], kind: str = "digest") -> None:
    refs = [{"n": i, "signal_id": str(s), "ask_id": None, "ask_code": None, "cluster_ids": None} for i, s in enumerate(ids, 1)]
    await db.execute(text("""INSERT INTO ops.owner_outbox (org_id, channel_type, kind, severity, dedupe_key, text, created_at, sent_at, refs)
                             VALUES (:o, 'zalo', :k, 'important', :d, 'bản tin', now(), now(), CAST(:r AS jsonb))"""),
                     {"o": org, "k": kind, "d": uuid.uuid4().hex, "r": orjson.dumps(refs).decode()})
    await db.commit()


async def feedback(db: Any) -> list[Any]:
    return list((await db.execute(text("SELECT verdict, gate, verdict_by FROM biz.signal_feedback ORDER BY at"))).all())


async def sig_row(db: Any, sid: uuid.UUID) -> Any:
    return (await db.execute(text("SELECT status, level, delivery, gate FROM biz.alerts WHERE id = :i"), {"i": sid})).one()


async def setup(chat: Chat, db: Any) -> dict[str, Any]:
    await _install(db, chat.org)
    await set_quiet(db, chat.org, now_quiet=False)
    amber = await _sig(db, chat.org, "cau_hoi_cho_xu_ly", "q1", impact_base=2, deadline_at=NOW + timedelta(hours=10))
    fraud = await _sig(db, chat.org, "rui_ro_lua_dao", "fr", flags={"credentials_entered": True})
    await put_refs(db, chat.org, [fraud.id, amber.id])
    return {"amber": amber, "fraud": fraud}


# ─── Đúng / Sai / không cần báo ──────────────────────────────────────────────

async def test_wrong_and_correct_by_number(chat: Chat, db: Any) -> None:
    s = await setup(chat, db)
    assert "ghi nhận" in (await chat.say("sai 2")).lower()
    assert (await sig_row(db, s["amber"].id)).status == "dismissed"
    await chat.say("1 đúng")
    assert [f.verdict for f in await feedback(db)] == ["wrong", "correct"]
    assert "không có mục số 9" in (await chat.say("sai 9")).lower()
    assert len(await feedback(db)) == 2


async def test_wrong_or_not_needed_never_closes_or_lowers_fraud_gate_from_chat(chat: Chat, db: Any) -> None:
    """TẤN CÔNG: «sai» / «không cần báo» lên vụ lừa đảo 🔴 qua chat không đóng, không hạ, không mute, không ghi phản hồi (cần Console + PIN)."""
    s = await setup(chat, db)
    before = await sig_row(db, s["fraud"].id)
    assert before.level == "red"
    for cmd in ("không cần báo 1", "1 không cần báo", "sai 1", "1 sai"):
        reply = await chat.say(cmd)
        assert "lừa đảo" in reply and "Console" in reply, (cmd, reply)
        after = await sig_row(db, s["fraud"].id)
        assert (after.status, after.level, after.delivery, after.gate) == (before.status, before.level, before.delivery, before.gate), cmd
    assert before.status == "open"
    assert (await db.execute(text("SELECT count(*) FROM biz.signal_mutes"))).scalar_one() == 0
    assert await feedback(db) == []
    assert "ghi nhận" in (await chat.say("1 đúng")).lower()           # «đúng» không hạ gì ⇒ được
    reply = await chat.say("2 không cần báo")
    assert "ghi nhận" in reply.lower() and (await sig_row(db, s["amber"].id)).status == "open"


async def test_feedback_cannot_reach_other_orgs_signals(chat: Chat, db: Any) -> None:
    """IDOR: tham chiếu trong bản tin chỉ trỏ tín hiệu CỦA tổ chức (refs lấy theo org; tín hiệu tổ chức khác không bao giờ bị đổi)."""
    s = await setup(chat, db)
    other = (await db.execute(text("INSERT INTO core.organizations (name) VALUES ('Khác') RETURNING id"))).scalar_one()
    await db.commit()
    foreign = await _sig(db, other, "cau_hoi_cho_xu_ly", "qx", impact_base=2, deadline_at=NOW + timedelta(hours=10))
    await put_refs(db, other, [foreign.id])
    await chat.say("sai 2")                                     # refs mới nhất của tổ chức MÌNH (amber #2)
    assert (await sig_row(db, foreign.id)).status == "open"
    assert (await sig_row(db, s["amber"].id)).status == "dismissed"


# ─── em sót ──────────────────────────────────────────────────────────────────

async def test_missed_command_records_masked_and_flags_fraud(chat: Chat, db: Any) -> None:
    reply = await chat.say("em sót lừa đảo: có người đòi chuyển tiền vào 0123456789012")
    assert "ghi" in reply.lower()
    [f] = await feedback(db)
    assert (f.verdict, f.gate) == ("missed", "lua_dao_dang_dien_ra")
    assert "0123456789012" not in (await db.execute(text("SELECT summary FROM biz.alerts"))).scalar_one()
    await chat.say("em sót: khách doạ bỏ đi")
    assert [x.gate for x in await feedback(db)] == ["lua_dao_dang_dien_ra", None]
    assert "nói rõ" in (await chat.say("em sót:")).lower() and len(await feedback(db)) == 2


# ─── mute ────────────────────────────────────────────────────────────────────

async def test_mute_defensive_from_chat_is_refused_need_console(chat: Chat, db: Any) -> None:
    """TẤN CÔNG: kẻ chiếm tài khoản Zalo không tắt được cảnh báo lừa đảo/an toàn từ chat."""
    await set_quiet(db, chat.org, now_quiet=False)
    for cmd in ("tắt báo loại lừa đảo", "đừng báo loại an toàn nữa", "tắt báo loại rui_ro_lua_dao", "tắt báo loại sức khoẻ"):
        reply = await chat.say(cmd)
        assert "Console" in reply, (cmd, reply)
    assert (await db.execute(text("SELECT count(*) FROM biz.signal_mutes"))).scalar_one() == 0
    n = (await db.execute(text("SELECT count(*) FROM ops.action_log WHERE action = 'strong_confirm.chat_command' AND detail->>'target' = 'mute_defensive'"))).scalar_one()
    assert n == 4                                               # mọi lần thử đều qua strong_confirm.require và có Nhật ký


async def test_mute_regular_kind_and_group_record_scope_and_source_chat(chat: Chat, db: Any, world: dict[str, Any]) -> None:
    await set_quiet(db, chat.org, now_quiet=False)
    assert "đã tắt" in (await chat.say("tắt báo loại sự kiện")).lower()
    code = (await db.execute(text("SELECT code FROM core.groups WHERE id = :g"), {"g": world["group"]})).scalar_one()
    assert "đã tắt" in (await chat.say(f"tắt báo nhóm {code}")).lower()
    rows = (await db.execute(text("SELECT scope_type, scope_value, source FROM biz.signal_mutes ORDER BY created_at"))).all()
    assert [(r.scope_type, r.source) for r in rows] == [("kind", "chat"), ("group", "chat")]
    assert rows[0].scope_value == "su_kien_dip" and rows[1].scope_value == str(world["group"])
    assert "không tìm thấy" in (await chat.say("tắt báo nhóm KHONGCO-9")).lower()


# ─── tắt bản tin / giờ yên lặng / chạy thử ───────────────────────────────────

async def test_digest_off_and_quiet_hours_change_notify_backup_channel(chat: Chat, db: Any) -> None:
    await set_quiet(db, chat.org, now_quiet=False)
    await _telegram(db, chat.org)
    assert "đã tắt bản tin" in (await chat.say("tắt bản tin")).lower()
    assert (await db.execute(text("SELECT settings->'intel'->>'digest_off' FROM core.organizations WHERE id = :o"), {"o": chat.org})).scalar_one() == "true"
    assert "đã bật lại" in (await chat.say("bật bản tin")).lower()
    assert "22:30" in await chat.say("giờ yên lặng 22:30-06:45")
    s = (await db.execute(text("SELECT settings->'intel' FROM core.organizations WHERE id = :o"), {"o": chat.org})).scalar_one()
    assert (s["quiet_start"], s["quiet_end"], s["digest_off"]) == ("22:30", "06:45", False)
    sec = (await db.execute(text("SELECT text FROM ops.backup_outbox WHERE kind = 'security' ORDER BY created_at"))).scalars().all()
    assert any("tắt bản tin" in t for t in sec) and any("giờ yên lặng" in t for t in sec)


@pytest.mark.parametrize("cmd", ["giờ yên lặng 25:00-07:00", "giờ yên lặng sáng", "giờ yên lặng 22:00"])
async def test_quiet_hours_bad_input_changes_nothing(chat: Chat, db: Any, cmd: str) -> None:
    await set_quiet(db, chat.org, now_quiet=False)
    before = (await db.execute(text("SELECT settings->'intel' FROM core.organizations WHERE id = :o"), {"o": chat.org})).scalar_one()
    assert "Cách dùng" in await chat.say(cmd)
    assert (await db.execute(text("SELECT settings->'intel' FROM core.organizations WHERE id = :o"), {"o": chat.org})).scalar_one() == before


async def test_shadow_mode_never_changes_from_chat(chat: Chat, db: Any) -> None:
    await set_quiet(db, chat.org, now_quiet=False)
    for cmd in ("chạy thử bật", "chế độ bóng", "chạy thật"):
        assert "Console" in await chat.say(cmd)
    assert (await db.execute(text("SELECT settings->'intel'->>'mode' FROM core.organizations WHERE id = :o"), {"o": chat.org})).scalar_one() is None


# ─── sửa ─────────────────────────────────────────────────────────────────────

async def test_correct_single_fact_by_number(chat: Chat, db: Any) -> None:
    await _install(db, chat.org)
    await set_quiet(db, chat.org, now_quiet=False)
    lan = await mk_entity(db, chat.org, "Lan")
    f = await facts.assert_fact(db, mkfact(chat.org, lan, "Hà Nội", predicate="lives_in", speaker=lan))
    await db.commit()
    s = await _sig(db, chat.org, "cau_hoi_cho_xu_ly", "q1", impact_base=2, deadline_at=NOW + timedelta(hours=10))
    await db.execute(text("UPDATE biz.alerts SET fact_ids = ARRAY[CAST(:f AS uuid)] WHERE id = :i"), {"f": f.id, "i": s.id})
    await db.commit()
    await put_refs(db, chat.org, [s.id])
    assert "đã sửa" in (await chat.say("sửa 1: Đà Nẵng")).lower()
    row = (await db.execute(text("SELECT status, object_value FROM mem.facts WHERE subject_id = :s AND predicate = 'lives_in' ORDER BY recorded_at"), {"s": lan})).all()
    assert [(r.status, r.object_value) for r in row] == [("superseded", "Hà Nội"), ("owner_confirmed", "Đà Nẵng")]
    assert "nói rõ" in (await chat.say("sửa: Lan ở Huế")).lower()
    assert "không có mục số 7" in (await chat.say("sửa 7: x")).lower()


async def test_correct_sensitive_fact_needs_console(chat: Chat, db: Any) -> None:
    await _install(db, chat.org)
    await set_quiet(db, chat.org, now_quiet=False)
    lan = await mk_entity(db, chat.org, "Lan")
    f = await facts.assert_fact(db, mkfact(chat.org, lan, "0901234567", predicate="contact_phone", speaker=lan))
    await db.commit()
    s = await _sig(db, chat.org, "cau_hoi_cho_xu_ly", "q1", impact_base=2, deadline_at=NOW + timedelta(hours=10))
    await db.execute(text("UPDATE biz.alerts SET fact_ids = ARRAY[CAST(:f AS uuid)] WHERE id = :i"), {"f": f.id, "i": s.id})
    await db.commit()
    await put_refs(db, chat.org, [s.id])
    assert "Console" in await chat.say("sửa 1: 0999999999")
    assert (await db.execute(text("SELECT count(*) FROM mem.facts WHERE subject_id = :s AND status = 'superseded'"), {"s": lan})).scalar_one() == 0


# ─── che đầu ra gen_chat + khoá trợ lý ───────────────────────────────────────

async def test_free_text_reply_masks_account_and_phone(chat: Chat, db: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake(cmd: Any, ctx: Any) -> owner_core.Reply:
        return owner_core.Reply("Khách Lan: STK 0123456789012, SĐT 0901 234 567, tiền 25.000.000đ, mã OK #A3F9C2.")

    monkeypatch.setattr(gen_chat, "hoi", fake)
    reply = await chat.say("khách Lan có STK nào?")
    assert "0123456789012" not in reply and "0123…012" in reply
    assert "0901 234 567" not in reply and "0901…567" in reply
    assert "25.000.000đ" in reply and "#A3F9C2" in reply


async def test_locked_assistant_sends_nothing_even_for_queued_dm(chat: Chat, db: Any) -> None:
    await _lock(db, chat.org)
    mid = uuid.uuid4().hex
    await chat.redis.set(f"gh:owner:dm:zalo:{mid}", str(int(time.time())), ex=600)
    p = {"channel": "zalo", "session_id": str(chat.sid), "external_msg_id": mid, "text": "em sót: x", "occurred_at": ago(seconds=1)}
    res = await dm.handle_dm(SM(), chat.redis, chat.bus, None, chat.org, p, chat.app)
    assert res == "ignored:locked" and not chat.bus.on(BRIDGE_OUTBOUND)
    assert await feedback(db) == []
