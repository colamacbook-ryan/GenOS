# ruff: noqa: E501
"""v0.1.76 (gói T1) — `digest.build` chỉ THỰC THI `Level.delivery` đã lưu (thứ tự: đứng đầu bản tin ⇒ lừa đảo/an toàn trước; ⚪ gộp dòng
cụm; không có push_now/merge/none/off; kênh chat bỏ console_pin; `refs` cho "1 ok / sai 2") và đường nhanh `raw_tail` (luật cứng ⇒ tín
hiệu ngay, chạy lại không trùng, lượt model sau gộp vào CÙNG tín hiệu)."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Any

import orjson
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gh.biz.intel import digest, levels, raw_tail, signals
from tests.mem_helpers import mk_org

NOW = datetime(2026, 10, 23, 14, 0, tzinfo=levels.VN)


async def _install(db: AsyncSession, org: uuid.UUID) -> None:
    s = {"installed_at": "2026-09-01T00:00:00+07:00", "m1_passed_gates": list(levels.REGULAR_GATES)}
    await db.execute(text("UPDATE core.organizations SET settings = jsonb_build_object('intel', CAST(:s AS jsonb)) WHERE id = :o"),
                     {"s": orjson.dumps(s).decode(), "o": org})


async def _raw(db: AsyncSession, org: uuid.UUID, body: str = "tin", group: uuid.UUID | None = None, at: datetime = NOW) -> tuple[uuid.UUID, datetime]:
    ch = (await db.execute(text("SELECT id FROM core.channels WHERE org_id = :o LIMIT 1"), {"o": org})).scalar_one_or_none()
    if ch is None:
        ch = (await db.execute(text("INSERT INTO core.channels (org_id, type, name) VALUES (:o, 'zalo', 'z') RETURNING id"), {"o": org})).scalar_one()
    r = (await db.execute(text("""INSERT INTO raw.events (org_id, occurred_at, channel_id, external_msg_id, kind, body_text, payload, content_hash, group_id)
                                  VALUES (:o, :at, :c, :x, 'text', :b, '{}', '\\x01', :g) RETURNING id, received_at"""),
                          {"o": org, "at": at, "c": ch, "x": uuid.uuid4().hex, "b": body, "g": group})).one()
    return r.id, r.received_at


async def _sig(db: AsyncSession, org: uuid.UUID, kind: str, ck: str, *, privacy: str = "chat_ok", **kw: Any) -> signals.Signal:
    raw, _ = await _raw(db, org)
    data: dict[str, Any] = {"kind": kind, "privacy": privacy, "msg_at": NOW - timedelta(hours=1), "harm_what": "thiệt hại",
                            "thread": {"type": "group", "size": 20, "owner_in_thread": True}, "victims": [{"ref": "v", "team": True}],
                            "source_class": "insider", "confidence_exist": 1.0, "confidence_content": 0.8}
    data.update(kw)
    li = levels.SignalIn.model_validate(data)
    return await signals.upsert(db, org, signals.UpsertIn(kind=kind, title=f"{kind} {ck}", detector="t", level_input=li, case_key=ck,
                                                          evidence=[signals.EvidenceIn(type="raw_event", id=raw, source_field="body",
                                                                                       span_start=0, span_end=3, quote="abc")]), NOW)


@pytest.mark.usefixtures("regular_push_on")
async def test_intel_digest_executes_delivery_order_and_refs(db: AsyncSession) -> None:
    org = await mk_org(db)
    await _install(db, org)
    red_push = await _sig(db, org, "xung_dot_khieu_nai", "c-red", customer_of_owner=True, flags={"public": True})
    expired = await _sig(db, org, "xung_dot_khieu_nai", "c-q15", msg_at=NOW - timedelta(hours=30), customer_of_owner=True,
                         flags={"public": True})
    fraud_floor = await _sig(db, org, "rui_ro_lua_dao", "fr-floor", flags={"credentials_entered": True}, confidence_exist=0.5,
                             d2={"verdict": "disagree"})
    amber = await _sig(db, org, "cau_hoi_cho_xu_ly", "q1", impact_base=2, deadline_at=NOW + timedelta(hours=10))
    g1 = await _sig(db, org, "su_kien_dip", "e1")
    g2 = await _sig(db, org, "su_kien_dip", "e2")
    priv = await _sig(db, org, "cau_hoi_cho_xu_ly", "q-priv", privacy="console_pin", impact_base=2, deadline_at=NOW + timedelta(hours=10))
    assert red_push.delivery == "push_now" and expired.delivery == "digest_top" and fraud_floor.delivery == "digest_top"
    assert amber.delivery == "digest" and g1.delivery == "digest_low"
    d = await digest.build(db, org, NOW, audience="chat")
    ids = [it.signal_id for it in d.items]
    assert str(red_push.id) not in ids and str(priv.id) not in ids
    assert ids[:2] == [str(fraud_floor.id), str(expired.id)]             # đứng đầu bản tin; lừa đảo/an toàn đầu nhóm
    assert ids[2] == str(amber.id)
    cluster = d.items[-1]
    assert cluster.delivery == "digest_low" and set(cluster.cluster_ids) == {str(g1.id), str(g2.id)}
    assert [r["n"] for r in d.refs()] == list(range(1, len(d.items) + 1))
    assert d.items[0].quote == "abc"
    owner = await digest.build(db, org, NOW, audience="owner")
    assert str(priv.id) in [it.signal_id for it in owner.items]
    await digest.mark_sent(db, org, d, NOW)
    assert [it.signal_id for it in (await digest.build(db, org, NOW, audience="chat")).items] == []


async def _state(db: AsyncSession, org: uuid.UUID, raw: uuid.UUID, recv: datetime, cands: list[dict[str, Any]], tk: str | None) -> None:
    detail = {"scanned": True, "thread_key": tk, "gate_class": "hard", "cands": cands}
    await db.execute(text("""INSERT INTO refinery.event_state (event_id, event_received_at, org_id, state, detail)
                             VALUES (:e, :r, :o, 'pending', CAST(:d AS jsonb))
                             ON CONFLICT (event_id, event_received_at) DO UPDATE SET detail = EXCLUDED.detail"""),
                     {"e": raw, "r": recv, "o": org, "d": orjson.dumps(detail).decode()})


async def test_intel_raw_tail_hard_rule_signal_once_then_model_merges(db: AsyncSession) -> None:
    org = await mk_org(db)
    await _install(db, org)
    g = uuid.uuid4()
    now = datetime.now(levels.VN)
    raw, recv = await _raw(db, org, "em lỡ nhập OTP rồi", group=g, at=now - timedelta(minutes=2))
    cand = {"flag": "credentials_entered", "field": "body", "span": [3, 17], "kind": "rui_ro_lua_dao", "gate": "lua_dao_dang_dien_ra",
            "hard": "credentials_entered", "first_person": True}
    await _state(db, org, raw, recv, [cand], f"g:{g}")
    out = await raw_tail.tail(db, org, now)
    assert len(out) == 1 and out[0].kind == "rui_ro_lua_dao" and out[0].level in ("red", "amber")
    assert out[0].gate == levels.G1 or out[0].candidate == levels.G1
    assert await raw_tail.tail(db, org, now) == []                         # chạy lại: không trùng
    li = levels.SignalIn(kind="rui_ro_lua_dao", msg_at=now - timedelta(minutes=2), flags=levels.Flags(credentials_entered=True),
                         victims=[levels.PersonRel(ref="u_x", team=True)], harm_what="mất tiền", confidence_exist=1.0,
                         thread=levels.Thread(size=10, owner_in_thread=True))
    s2 = await signals.upsert(db, org, signals.UpsertIn(
        kind="rui_ro_lua_dao", title="t", detector="fraud", level_input=li, case_key="rui_ro_lua_dao:stk|zzz",
        evidence=[signals.EvidenceIn(type="raw_event", id=raw, source_field="body", span_start=3, span_end=17)]), now)
    assert s2.id == out[0].id
    assert (await db.execute(text("SELECT count(*) FROM biz.alerts WHERE org_id = :o"), {"o": org})).scalar_one() == 1


async def test_intel_raw_tail_money_request_alone_waits_for_model(db: AsyncSession) -> None:
    org = await mk_org(db)
    now = datetime.now(levels.VN)
    raw, recv = await _raw(db, org, "ck 5tr giúp em", at=now - timedelta(minutes=1))
    await _state(db, org, raw, recv, [{"flag": "money_request", "field": "body", "span": [0, 5], "kind": None, "hard": "x"}], None)
    assert await raw_tail.tail(db, org, now) == []
