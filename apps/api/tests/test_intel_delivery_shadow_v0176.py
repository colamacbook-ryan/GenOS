# ruff: noqa: E501, F811
"""v0.1.76 (gói T3) — gói gán nhãn hằng ngày khi chạy bóng (`pipes/shadow.py`): chỉ khi `intel.mode=shadow`, ~20 mục lấy mẫu theo tầng có
xác suất `p` trong `refs`, chỉ chat_ok, ổn định theo ngày, khoá trợ lý ⇒ im; «N sót» trên mục mẫu ghi sót kèm tầng + p."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import text

from gh.biz.intel.pipes import shadow
from gh.db import sessionmaker
from tests.mem_helpers import mk_org
from tests.owner_helpers import bot_world
from tests.test_chat_confirm_v0173 import Chat, chat  # noqa: F401
from tests.test_intel_delivery_reports_v0176 import _lock
from tests.test_intel_digest_rawtail_v0176 import NOW, _install, _raw, _sig
from tests.test_p3_core import world  # noqa: F401

SM = sessionmaker


async def mode(db: Any, org: uuid.UUID, m: str) -> None:
    await db.execute(text("""UPDATE core.organizations SET settings = settings || jsonb_build_object('intel',
                               COALESCE(settings->'intel', '{}'::jsonb) || jsonb_build_object('mode', CAST(:m AS text))) WHERE id = :o"""), {"o": org, "m": m})
    await db.commit()


async def units(db: Any, org: uuid.UUID, n: int, *, hint: str | None, level: str | None) -> None:
    for i in range(n):
        await db.execute(text("""INSERT INTO clean.meaning_units (org_id, observed_at, event_type, conclusion, confidence, run_id, kind_hint, level_hint)
                                 VALUES (:o, now(), 'Said', :c, 0.5, :r, :h, :l)"""), {"o": org, "c": f"đơn vị {hint}/{level} #{i} STK 0123456789012", "r": uuid.uuid4(), "h": hint, "l": level})
    await db.commit()


async def seeded(db: Any) -> uuid.UUID:
    org = await mk_org(db)
    await _install(db, org)
    for i in range(3):
        s = await _sig(db, org, "xung_dot_khieu_nai", f"c{i}", customer_of_owner=True, flags={"public": True})
        await db.execute(text("UPDATE biz.alerts SET would_push_at = now() WHERE id = :i"), {"i": s.id})
    priv = await _sig(db, org, "xung_dot_khieu_nai", "cp", privacy="console_pin", customer_of_owner=True, flags={"public": True})
    await db.execute(text("UPDATE biz.alerts SET would_push_at = now(), title = 'RIÊNG TƯ nhân sự' WHERE id = :i"), {"i": priv.id})
    await units(db, org, 10, hint="viec_han", level="grey")
    await units(db, org, 6, hint=None, level="grey")
    for i in range(5):
        await _raw(db, org, body=f"tin ngẫu nhiên số {i} đủ dài", at=datetime.now(UTC))
    await bot_world(db, org)
    return org


async def test_pack_is_off_in_live_mode(db: Any) -> None:
    org = await seeded(db)
    async with SM()() as s:
        assert await shadow.deliver_pack(s, org, NOW) == "off"


async def test_pack_tiers_probabilities_privacy_and_masking(db: Any) -> None:
    org = await seeded(db)
    await mode(db, org, "shadow")
    now = (await db.execute(text("SELECT now()"))).scalar_one()
    async with SM()() as s:
        assert await shadow.deliver_pack(s, org, now) == "sent"
        await s.commit()
        assert await shadow.deliver_pack(s, org, now) == "already_sent"                  # một tin mỗi ngày
    row = (await db.execute(text("SELECT text, refs, kind FROM ops.owner_outbox"))).one()
    assert row.kind == "ask" and "RIÊNG TƯ" not in row.text and "0123456789012" not in row.text
    by = {}
    for r in row.refs:
        by.setdefault(r["tier"], []).append(r)
    assert len(by["shadow"]) == 3 and all(r["p"] == 1.0 for r in by["shadow"])
    assert len(by["t2"]) == 5 and all(r["p"] == 0.5 for r in by["t2"])                     # 5 / 10
    assert len(by["t3"]) == 3 and all(r["p"] == 0.5 for r in by["t3"])                     # 3 / 6
    assert len(by["t4"]) == 2 and all(r["p"] == 0.4 and r["raw_event_id"] for r in by["t4"])   # 2 / 5
    assert [r["n"] for r in row.refs] == list(range(1, 14))


async def test_pack_is_stable_within_a_day(db: Any) -> None:
    org = await seeded(db)
    now = (await db.execute(text("SELECT now()"))).scalar_one()
    async with SM()() as s:
        a = await shadow.build_pack(s, org, now)
        b = await shadow.build_pack(s, org, now + timedelta(hours=1))
    assert [x["text"] for x in a] == [x["text"] for x in b]


async def test_pack_locked_sends_nothing(db: Any) -> None:
    org = await seeded(db)
    await mode(db, org, "shadow")
    await _lock(db, org)
    async with SM()() as s:
        assert await shadow.deliver_pack(s, org, NOW) == "locked"
    assert (await db.execute(text("SELECT count(*) FROM ops.owner_outbox"))).scalar_one() == 0


async def test_sample_item_missed_records_tier_and_p_and_rejects_foreign_raw(chat: Chat, db: Any) -> None:
    org = chat.org
    raw, _ = await _raw(db, org, body="khách nói sẽ bỏ đi", at=NOW)
    other = await mk_org(db, "Khác")
    foreign, _ = await _raw(db, other, body="tin tổ chức khác")
    refs = [{"n": 1, "signal_id": None, "raw_event_id": str(raw), "tier": "t4", "p": 0.4}, {"n": 2, "signal_id": None, "raw_event_id": str(foreign), "tier": "t4", "p": 0.4}]
    import orjson

    await db.execute(text("""INSERT INTO ops.owner_outbox (org_id, channel_type, kind, severity, dedupe_key, text, sent_at, refs)
                             VALUES (:o, 'zalo', 'ask', 'important', 'x', 'gói', now(), CAST(:r AS jsonb))"""), {"o": org, "r": orjson.dumps(refs).decode()})
    await db.commit()
    assert "ghi nhận" in (await chat.say("1 sót")).lower()
    f = (await db.execute(text("SELECT verdict, note FROM biz.signal_feedback"))).one()
    assert f.verdict == "missed" and "t4" in f.note and "0.4" in f.note
    assert "không có mục" in (await chat.say("2 sót")).lower()                       # IDOR: tin tổ chức khác không ghi được
    assert (await db.execute(text("SELECT count(*) FROM biz.signal_feedback"))).scalar_one() == 1
    assert "mẫu kiểm tra" in (await chat.say("1 đúng"))
