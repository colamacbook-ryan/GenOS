"""Agent trực kênh: phạm vi, năm quyết định qua policy, gửi ở mức 6 vẫn chờ duyệt, ngữ cảnh = nguồn bản nháp,
chống trùng, model chết không mất đơn vị, loại ID bịa, không lấy dữ liệu ngoài phạm vi."""

import asyncio
import uuid
from collections.abc import Callable
from typing import Any

import orjson
import pytest
from sqlalchemy import text

from gh.biz.duty import engine, jobs
from gh.biz.duty.jobs import duty_hook, duty_sweep
from gh.biz.hooks import HookCtx, context_from
from gh.chassis.bus import BRIDGE_OUTBOUND, CLEAN_READY, Deferred, EventBus
from gh.db import sessionmaker
from gh.providers.clients import Message
from gh.providers.router import ModelRouter
from gh.refinery.runner import Refinery
from tests.conftest import Api
from tests.phase2 import FakeRouter, install_presets, listen, msg, org_id, put
from tests.test_model_router import provider, transport
from tests.test_refinery import BUY, by_text

REPLY = "Dạ em chào anh/chị, em gửi báo giá 3 container thép cuộn trong hôm nay ạ."
CTX_HEAD = "Ngữ cảnh (mỗi dòng: mã rồi JSON):\n"


# ─── dựng bối cảnh ────────────────────────────────────────────────────────────

@pytest.fixture
async def w(app, db, redis, owner_api):  # type: ignore[no-untyped-def]
    """Tổ chức đã khởi tạo; g1 (chủ động), g2 (chủ động) đang nghe; Zalo đang chạy."""
    org = await org_id(db)
    await install_presets(db, org)
    g1 = await listen(db, org, "g1", mode="proactive")
    g2 = await listen(db, org, "g2", mode="proactive")
    ch = (await db.execute(text("SELECT id FROM core.channels WHERE org_id = :o AND type = 'zalo'"),
                           {"o": org})).scalar()
    await db.execute(text("""INSERT INTO core.channel_sessions (channel_id, org_id, account_label, state, started_at)
                             VALUES (:c, :o, 'Zalo Sếp', 'active', now())"""), {"c": ch, "o": org})
    await db.commit()
    return {"org": org, "g1": g1, "g2": g2, "channel": ch, "sm": sessionmaker(), "api": owner_api}


async def refine(w: dict[str, Any], redis, *payloads: dict[str, Any]) -> list[uuid.UUID]:  # type: ignore[no-untyped-def]
    """Nhận tin rồi sàng lọc thật; trả ID đơn vị ý nghĩa theo đúng thứ tự tin (tin phải có chữ "container")."""
    raws = await put(w["sm"], w["org"], *payloads)
    await Refinery(w["sm"], redis, by_text()).run(w["org"], "manual")  # type: ignore[arg-type]
    out = []
    async with w["sm"]() as db:
        for r in raws:
            out.append((await db.execute(text("SELECT meaning_unit_id FROM clean.evidence WHERE raw_event_id = :r"),
                                         {"r": r})).scalar_one())
    return out


async def make_agent(db, w: dict[str, Any], scopes: list[uuid.UUID | None] | None = None, *, level: int = 4,  # type: ignore[no-untyped-def]
                     enabled: bool = True, limits: dict[str, int] | None = None, name: str = "Trợ lý thương mại",
                     assigned: uuid.UUID | None = None) -> uuid.UUID:
    """`scopes`: danh sách group_id (None = cả kênh Zalo)."""
    aid = (await db.execute(text("""
        INSERT INTO agent.identities (org_id, name, role_desc, template, addressing, voice, speak_when, forbidden,
                                      autonomy_level, is_enabled, limits)
        VALUES (:o, :n, 'Báo giá, hợp đồng, follow khách', 'commercial',
                '{"owner": "Sếp", "self": "em", "customer": "anh/chị"}', 'lễ phép, ngắn gọn',
                'khi được tag, hoặc khi khách hỏi giá', ARRAY['không tự cam kết giá'], :l, :e, CAST(:lim AS jsonb))
        RETURNING id"""), {"o": w["org"], "n": name, "l": level, "e": enabled,
                            "lim": orjson.dumps(limits or {}).decode()})).scalar_one()
    for g in scopes if scopes is not None else [w["g1"]]:
        await db.execute(text("INSERT INTO agent.channel_scopes (agent_id, channel_id, group_id) VALUES (:a, :c, :g)"),
                         {"a": aid, "c": w["channel"], "g": g})
    if assigned:
        await db.execute(text("UPDATE core.groups SET assigned_agent_id = :a WHERE id = :g"), {"a": aid, "g": assigned})
    await db.commit()
    return aid  # type: ignore[no-any-return]


async def set_org_level(db, org: uuid.UUID, level: int) -> None:  # type: ignore[no-untyped-def]
    await db.execute(text("""UPDATE core.organizations
                             SET settings = settings || jsonb_build_object('autonomy_level', CAST(:l AS int))
                             WHERE id = :o"""),
                     {"l": level, "o": org})
    await db.commit()


def items_in(messages: list[Message]) -> dict[str, dict[str, Any]]:
    body = messages[-1].content.split(CTX_HEAD, 1)[1]
    out = {}
    for line in body.splitlines():
        handle, js = line.split(" ", 1)
        out[handle] = orjson.loads(js)
    return out


def decide(decision: str = "draft", *, refs: tuple[str, ...] | Callable[[list[Message]], list[str]] = ("C1",),
           body: str = REPLY, **extra: Any) -> FakeRouter:
    def reply(m: list[Message]) -> Any:
        return {"decision": decision, "rationale": "Khách hỏi giá 3 container, cần phản hồi trong ngày",
                "context_refs": refs(m) if callable(refs) else list(refs), "text": body,
                "note": {"section": "attention_now", "text": "Khách đang chờ báo giá 3 container"}, **extra}
    return FakeRouter(reply)


async def hook(w: dict[str, Any], redis, router: FakeRouter, units: list[uuid.UUID]) -> None:  # type: ignore[no-untyped-def]
    await duty_hook(HookCtx(w["org"], units, None, w["sm"], redis, EventBus(redis), router))


async def decisions(db, agent: uuid.UUID | None = None) -> list[Any]:  # type: ignore[no-untyped-def]
    return list((await db.execute(text("""SELECT * FROM agent.decisions
                                          WHERE (CAST(:a AS uuid) IS NULL OR agent_id = :a) ORDER BY at"""),
                                  {"a": agent})).all())


async def agent_drafts(db) -> list[Any]:  # type: ignore[no-untyped-def]
    return list((await db.execute(text("SELECT * FROM biz.action_drafts WHERE agent_id IS NOT NULL ORDER BY created_at")
                                  )).all())


# ─── phạm vi ──────────────────────────────────────────────────────────────────

async def test_scope_only_enabled_agents_on_their_groups(w, db, redis) -> None:  # type: ignore[no-untyped-def]
    a1 = await make_agent(db, w, [w["g1"]])
    off = await make_agent(db, w, [w["g1"]], enabled=False, name="Agent tắt")
    wide = await make_agent(db, w, [None], name="Admin hậu cần")            # cả kênh Zalo
    by_assign = await make_agent(db, w, [], name="CSKH", assigned=w["g2"])   # nhóm gán cho agent
    u1, u2 = await refine(w, redis, msg(BUY, group="g1"), msg(BUY, group="g2", sender="u2"))
    router = decide("silent", refs=())
    await hook(w, redis, router, [u1, u2])
    got = {(d.agent_id, d.trigger_unit_id) for d in await decisions(db)}
    assert got == {(a1, u1), (wide, u1), (wide, u2), (by_assign, u2)}
    assert not any(d[0] == off for d in got)
    assert len(router.calls) == 4
    for d in await decisions(db):
        assert d.trigger_ref == {"type": "meaning_unit", "id": str(d.trigger_unit_id)}


async def test_tagged_only_group_needs_a_tag_and_off_group_is_ignored(w, db, redis) -> None:  # type: ignore[no-untyped-def]
    a = await make_agent(db, w, [w["g1"], w["g2"]])
    await db.execute(text("UPDATE core.groups SET listen_mode = 'tagged_only' WHERE id = :g"), {"g": w["g1"]})
    await db.commit()
    plain, tagged, other = await refine(w, redis, msg(BUY, group="g1"), msg(BUY, group="g1", mentions=True),
                                        msg(BUY, group="g2"))
    await db.execute(text("UPDATE core.groups SET listen_mode = 'paused' WHERE id = :g"), {"g": w["g2"]})
    await db.commit()
    await hook(w, redis, decide("silent", refs=()), [plain, tagged, other])
    assert [d.trigger_unit_id for d in await decisions(db, a)] == [tagged]


async def test_direct_message_needs_channel_wide_scope(w, db, redis) -> None:  # type: ignore[no-untyped-def]
    await db.execute(text("""UPDATE core.organizations SET settings = settings || '{"listen_direct": {"zalo": true}}'
                             WHERE id = :o"""), {"o": w["org"]})
    await db.commit()
    grp = await make_agent(db, w, [w["g1"]], name="Theo nhóm")
    wide = await make_agent(db, w, [None], name="Cả kênh")
    [u] = await refine(w, redis, msg(BUY, group=None, sender="u9", name="Anh Tùng"))
    await hook(w, redis, decide("draft"), [u])
    assert [d.agent_id for d in await decisions(db)] == [wide]
    [d] = await agent_drafts(db)
    assert d.agent_id == wide and d.group_id is None and d.body["target"]["thread_type"] == "user"
    assert d.body["target"]["person_id"] == str(d.subject_id) and d.subject_type == "person"
    assert grp not in {x.agent_id for x in await decisions(db)}


async def test_silent_direct_message_is_not_given_to_duty_agent(w, db, redis) -> None:  # type: ignore[no-untyped-def]
    """v0.1.65: tin 1-1 mặc định IM LẶNG — lưu nhưng agent trực (dù có phạm vi cả kênh) không được đưa; chỉ khi Sếp
    đặt kênh sang `reply` (hoặc khoá cũ `listen_direct` bật) mới có quyết định."""
    wide = await make_agent(db, w, [None], name="Cả kênh")
    [u] = await refine(w, redis, msg(BUY, group=None, sender="u9", name="Anh Tùng"))
    await hook(w, redis, decide("draft"), [u])
    assert await decisions(db) == [] and await agent_drafts(db) == []
    await db.execute(text("""UPDATE core.organizations SET settings = settings || '{"direct_mode": {"zalo": "reply"}}'
                             WHERE id = :o"""), {"o": w["org"]})
    await db.commit()
    await hook(w, redis, decide("draft"), [u])
    assert [d.agent_id for d in await decisions(db)] == [wide]


# ─── từng quyết định ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("choice", ["silent", "note", "suggest", "draft", "send"])
async def test_each_decision_goes_through_policy(w, db, redis, choice: str) -> None:  # type: ignore[no-untyped-def]
    a = await make_agent(db, w, level=4)
    [u] = await refine(w, redis, msg(BUY))
    await hook(w, redis, decide(choice, refs=() if choice == "silent" else ("C1",)), [u])
    [d] = await decisions(db, a)
    assert d.decision == choice and d.requested == choice and d.autonomy_level == 4
    assert d.outcome == {"silent": "none", "note": "noted", "suggest": "suggested", "draft": "held",
                         "send": "held"}[choice]
    drafts_ = await agent_drafts(db)
    notes = (await db.execute(text("SELECT id, section, body, refs FROM memory.entries WHERE author = :a"),
                              {"a": f"agent:{a}"})).all()
    if choice in ("draft", "send"):
        [dr] = drafts_
        assert d.draft_id == dr.id and dr.status == "pending" and dr.kind == "message"
        assert dr.body["text"] == REPLY and dr.group_id == w["g1"] and dr.autonomy_level == 4
        assert "ghi ra ngoài phải chờ duyệt" in dr.hold_reason
    else:
        assert drafts_ == [] and d.draft_id is None
    if choice == "note":
        [n] = notes
        assert n.section == "attention_now" and d.proposal["note_entry_id"] == str(n.id)
        assert {"type": "meaning_unit", "id": str(u)} in n.refs
    else:
        assert notes == []
    if choice == "suggest":
        assert d.proposal == {"text": REPLY}
    log = (await db.execute(text("""SELECT actor_type, actor_id, target_id, autonomy_level, result, detail
                                     FROM ops.action_log WHERE action = 'agent.decided'"""))).one()
    assert (log.actor_type, log.actor_id, log.target_id, log.autonomy_level) == ("agent", f"agent:{a}", str(u), 4)
    assert log.detail["decision"] == choice and log.result == ("held" if choice in ("draft", "send") else "ok")


async def test_send_at_level_6_is_still_a_pending_draft(w, db, redis) -> None:  # type: ignore[no-untyped-def]
    await set_org_level(db, w["org"], 6)
    a = await make_agent(db, w, level=6)
    [u] = await refine(w, redis, msg(BUY))
    await hook(w, redis, decide("send"), [u])
    [d] = await decisions(db, a)
    [dr] = await agent_drafts(db)
    assert (d.decision, d.outcome, d.autonomy_level) == ("send", "held", 6)
    assert dr.status == "pending" and dr.autonomy_level == 6 and dr.flags["writes_external"] is True
    assert "ghi ra ngoài phải chờ duyệt" in dr.hold_reason
    assert await redis.xlen(BRIDGE_OUTBOUND) == 0                  # không có lệnh gửi nào tới bridge
    logged = (await db.execute(text("SELECT action, result FROM ops.action_log WHERE target_id = :d"),
                               {"d": str(dr.id)})).all()
    assert [(x.action, x.result) for x in logged] == [("draft.created", "held")]


async def test_lower_levels_downgrade(w, db, redis) -> None:  # type: ignore[no-untyped-def]
    a3 = await make_agent(db, w, level=3, name="Key Account junior")
    a2 = await make_agent(db, w, level=2, name="Quan sát")
    [u] = await refine(w, redis, msg(BUY))
    await hook(w, redis, decide("draft"), [u])
    d3, d2 = (await decisions(db, a3))[0], (await decisions(db, a2))[0]
    assert (d3.decision, d3.requested, d3.outcome) == ("suggest", "draft", "suggested")
    assert (d2.decision, d2.requested, d2.outcome) == ("silent", "draft", "blocked") and "mức 2" in d2.rationale
    assert await agent_drafts(db) == []


async def test_silent_listening_group_is_not_given_to_the_agent_even_when_tagged(w, db, redis) -> None:  # type: ignore[no-untyped-def]
    """QD 17/10 + mặc định #22: nhóm «Lắng nghe im lặng» không bao giờ trả lời, tin tag cũng vậy — và không vào ứng viên
    agent trực nên không tốn lượt LLM (xem test_qd_17_10_nghe_im_lang)."""
    a = await make_agent(db, w)
    await db.execute(text("UPDATE core.groups SET listen_mode = 'silent' WHERE id = :g"), {"g": w["g1"]})
    await db.commit()
    plain, tagged = await refine(w, redis, msg(BUY), msg(BUY, sender="u2", mentions=True))
    router = decide("draft")
    await hook(w, redis, router, [plain, tagged])
    assert router.calls == [] and await decisions(db, a) == [] and await agent_drafts(db) == []


async def test_one_pending_draft_per_place(w, db, redis) -> None:  # type: ignore[no-untyped-def]
    a = await make_agent(db, w)
    u1, u2 = await refine(w, redis, msg(BUY), msg(BUY, sender="u2"))
    await hook(w, redis, decide("draft"), [u1, u2])
    d1, d2 = await decisions(db, a)
    [dr] = await agent_drafts(db)
    assert d1.draft_id == dr.id and d2.decision == "suggest" and dr.code in d2.rationale


# ─── ngữ cảnh = nguồn của bản nháp ───────────────────────────────────────────

async def test_context_refs_match_draft_sources_and_are_traceable(w, db, redis) -> None:  # type: ignore[no-untyped-def]
    a = await make_agent(db, w)
    u0, u1 = await refine(w, redis, msg("Tuần trước mình lấy 2 container ván MDF"), msg(BUY))
    await db.execute(text("""INSERT INTO biz.tasks (org_id, code, title, subject_type, subject_id, status)
                             SELECT org_id, 'TSK-0901', 'Gọi lại chị Lan', 'person', person_id, 'todo'
                             FROM clean.meaning_units WHERE id = :u"""), {"u": u1})
    await db.commit()
    await hook(w, redis, decide("draft", refs=("C1", "C3")), [u1])
    [d] = await decisions(db, a)
    [dr] = await agent_drafts(db)
    assert d.context_refs == [s["ref"] for s in dr.sources]            # đúng những gì đã đưa vào ngữ cảnh
    assert all(s["label"] for s in dr.sources)
    assert d.cited_refs == [d.context_refs[0], d.context_refs[2]]
    types = [r["type"] for r in d.context_refs]
    assert types[:3] == ["meaning_unit", "group", "person"] and {"score", "notebook_entry", "task"} <= set(types)
    assert {"type": "meaning_unit", "id": str(u0)} in d.context_refs      # dữ liệu sạch liên quan cùng người
    tables = {"meaning_unit": "clean.meaning_units", "group": "core.groups", "person": "core.persons",
              "notebook_entry": "memory.entries", "task": "biz.tasks", "draft": "biz.action_drafts",
              "opportunity": "biz.opportunities"}
    for ref in d.context_refs:                                           # không mục nào không truy được
        if ref["type"] == "score":
            st, sid, dim = ref["id"].split(":")
            q, p = ("SELECT 1 FROM clean.current_scores WHERE subject_type = :t AND subject_id = :s AND dimension = :d",
                    {"t": st, "s": uuid.UUID(sid), "d": dim})
        else:
            q, p = f"SELECT 1 FROM {tables[ref['type']]} WHERE id = :i", {"i": uuid.UUID(ref["id"])}
        assert (await db.execute(text(q), p)).first(), ref

    api: Api = w["api"]
    body = (await api.get(f"/drafts/{dr.id}")).json()
    assert [s["ref"] for s in body["sources"]] == d.context_refs and body["agent"]["id"] == str(a)
    listed = (await api.get(f"/agents/decisions?agent_id={a}")).json()
    assert listed["items"][0]["draft"] == {"id": str(dr.id), "code": dr.code, "status": "pending"}  # F076
    assert listed["items"][0]["context_refs"] == d.context_refs
    ex = (await api.get(f"/explain/draft/{dr.id}")).json()
    assert {x["id"] for x in ex["units"]} >= {str(u0), str(u1)}


# ─── chống trùng, lỗi, model chết ─────────────────────────────────────────────

async def test_one_decision_per_unit_per_agent(w, db, redis) -> None:  # type: ignore[no-untyped-def]
    a = await make_agent(db, w)
    [u] = await refine(w, redis, msg(BUY))
    router = decide("draft")
    res = await asyncio.gather(hook(w, redis, router, [u]), hook(w, redis, router, [u]), return_exceptions=True)
    assert all(r is None or isinstance(r, Deferred) for r in res)
    await hook(w, redis, router, [u])                                 # sự kiện gửi lại
    jobs.SWEEP_MIN_AGE_S, old = 0, jobs.SWEEP_MIN_AGE_S
    try:
        await duty_sweep({"redis_bus": redis, "model_router": router})
    finally:
        jobs.SWEEP_MIN_AGE_S = old
    assert len(await decisions(db, a)) == 1 and len(await agent_drafts(db)) == 1
    assert len(router.calls) == 1


async def test_model_down_keeps_event_pending_then_decides(w, db, redis) -> None:  # type: ignore[no-untyped-def]
    a = await make_agent(db, w)
    [u] = await refine(w, redis, msg(BUY))
    bus = EventBus(redis)
    await bus.publish(CLEAN_READY, "meaning_units", {"ids": [str(u)]}, actor="agent:core.refinery", org_id=w["org"])

    def handler(router: FakeRouter):  # type: ignore[no-untyped-def]
        async def run(ev):  # type: ignore[no-untyped-def]
            await duty_hook(context_from(ev, sm=w["sm"], redis=redis, bus=bus, router=router))
        return run

    down = FakeRouter(down=True)
    for _ in range(6):                                                # hoãn nhiều lần vẫn không vào DLQ
        assert await bus.process_once(CLEAN_READY, "hook:duty", "w1", handler(down), block_ms=None,
                                      own_pending=True) == 0
    assert len(down.calls) == 6
    assert (await redis.xpending(CLEAN_READY, "hook:duty"))["pending"] == 1
    assert await redis.xlen(f"{CLEAN_READY}.dlq") == 0
    assert await decisions(db) == [] and await agent_drafts(db) == []   # không bản nháp rác

    assert await bus.process_once(CLEAN_READY, "hook:duty", "w1", handler(decide("draft")), block_ms=None,
                                  own_pending=True) == 1
    assert (await redis.xpending(CLEAN_READY, "hook:duty"))["pending"] == 0
    [d] = await decisions(db, a)
    assert d.decision == "draft" and d.draft_id is not None


async def test_all_real_providers_failing_leaves_unit_pending_and_releases_claim(w, db, redis) -> None:  # type: ignore[no-untyped-def]
    """Giai đoạn 5.4: chuỗi chuyển hướng THẬT (không FakeRouter) — mọi nhà cung cấp đều lỗi 500 liên tiếp.

    Kỳ vọng: không mất tin (message vẫn PEL, chưa ack), không có quyết định/bản nháp rác, khoá claim được
    giải phóng (lượt sau retry được, không kẹt "pending" mãi), và Sếp được báo qua biz.alerts."""
    a = await make_agent(db, w)
    [u] = await refine(w, redis, msg(BUY))
    org = w["org"]
    await provider(db, org, "alpha", 1, ["sk-alpha-aaaa"])
    await provider(db, org, "beta", 2, ["sk-beta-bbbb"])
    down = transport(lambda host, key: (500, {"error": "provider lỗi"}))
    router = ModelRouter(w["sm"], redis, transport=down)
    bus = EventBus(redis)
    await bus.publish(CLEAN_READY, "meaning_units", {"ids": [str(u)]}, actor="agent:core.refinery", org_id=org)

    async def run(ev):  # type: ignore[no-untyped-def]
        await duty_hook(context_from(ev, sm=w["sm"], redis=redis, bus=bus, router=router))

    assert await bus.process_once(CLEAN_READY, "hook:duty", "w1", run, block_ms=None, own_pending=True) == 0
    assert await decisions(db) == [] and await agent_drafts(db) == []
    assert (await redis.xpending(CLEAN_READY, "hook:duty"))["pending"] == 1
    assert not [k async for k in redis.scan_iter("gh:duty:claim:*")]        # khoá không kẹt lại
    assert (await db.execute(text(
        "SELECT count(*) FROM biz.alerts WHERE alert_type = 'model_chain_exhausted'"))).scalar() == 1
    # Lượt sau, một nhà cung cấp hồi phục → xử lý bình thường, tin cũ không mất.
    ok = transport(lambda host, key: (200, {"choices": [{"message": {"content":
                   '{"decision":"silent","outcome":"resolved","rationale":"ok"}'}}],
                   "usage": {"prompt_tokens": 5, "completion_tokens": 3}}) if host == "beta.test"
                   else (500, {"error": "vẫn lỗi"}))
    router2 = ModelRouter(w["sm"], redis, transport=ok)

    async def run2(ev):  # type: ignore[no-untyped-def]
        await duty_hook(context_from(ev, sm=w["sm"], redis=redis, bus=bus, router=router2))

    assert await bus.process_once(CLEAN_READY, "hook:duty", "w1", run2, block_ms=None, own_pending=True) == 1
    assert (await redis.xpending(CLEAN_READY, "hook:duty"))["pending"] == 0
    [d] = await decisions(db, a)
    assert d.decision == "silent"


async def test_sweep_picks_up_units_the_hook_missed(w, db, redis) -> None:  # type: ignore[no-untyped-def]
    a = await make_agent(db, w)
    u1, u2 = await refine(w, redis, msg(BUY), msg(BUY, group="g2"))
    jobs.SWEEP_MIN_AGE_S, old = 0, jobs.SWEEP_MIN_AGE_S
    try:
        out = await duty_sweep({"redis_bus": redis, "model_router": decide("silent", refs=())})
    finally:
        jobs.SWEEP_MIN_AGE_S = old
    assert out == {str(w["org"]): 1}
    assert [d.trigger_unit_id for d in await decisions(db, a)] == [u1]      # g2 ngoài phạm vi


async def test_processing_error_is_isolated_then_settled(w, db, redis) -> None:  # type: ignore[no-untyped-def]
    a = await make_agent(db, w)
    bad, good = await refine(w, redis, msg(BUY), msg(BUY, sender="u2", name="Anh Hùng"))

    def reply(m: list[Message]) -> Any:
        if "Anh Hùng" not in m[-1].content:
            raise RuntimeError("lỗi giả lập")
        return {"decision": "silent", "rationale": "", "context_refs": []}

    router = FakeRouter(reply)
    for _ in range(engine.MAX_FAILS):
        with pytest.raises(RuntimeError):
            await hook(w, redis, router, [bad, good])
    got = {d.trigger_unit_id: d for d in await decisions(db, a)}
    assert got[good].decision == "silent" and got[good].outcome == "none"      # cặp khác vẫn chạy
    assert got[bad].outcome == "rejected" and "5 lần lỗi" in got[bad].rationale
    await hook(w, redis, router, [bad, good])                                   # đã chốt: không thử mãi


async def test_rate_limit_defers_but_tag_goes_first(w, db, redis) -> None:  # type: ignore[no-untyped-def]
    a = await make_agent(db, w, limits={"decisions_per_min": 1})
    u1, u2, tagged = await refine(w, redis, msg(BUY), msg(BUY, sender="u2"), msg(BUY, sender="u3", mentions=True))
    router = decide("silent", refs=())
    with pytest.raises(Deferred):
        await hook(w, redis, router, [u1, u2, tagged])
    assert [d.trigger_unit_id for d in await decisions(db, a)] == [tagged, u1]
    for k in await redis.keys("gh:duty:rate:*"):
        await redis.delete(k)
    await hook(w, redis, router, [u1, u2, tagged])                    # lượt sau: phần bị hoãn được xử lý
    assert {d.trigger_unit_id for d in await decisions(db, a)} == {u1, u2, tagged}


# ─── phản hồi model sai / bịa ────────────────────────────────────────────────

@pytest.mark.parametrize("reply, why", [
    ({"decision": "draft", "rationale": "x", "context_refs": ["C1", "C99"], "text": REPLY}, "C99"),
    ("đây không phải JSON", "không phải JSON"),
    ({"decision": "shout", "context_refs": ["C1"], "text": REPLY}, "không hợp lệ"),
    ({"decision": "draft", "rationale": "x", "context_refs": ["C1"], "text": "Theo đơn OPP-9999 em gửi lại ạ"},
     "OPP-9999"),
    ({"decision": "draft", "rationale": "hồ sơ 0190a1b2-0000-7000-8000-000000000001", "context_refs": ["C1"],
      "text": REPLY}, "0190a1b2"),
    ({"decision": "draft", "rationale": "x", "context_refs": [], "text": REPLY}, "không dẫn ngữ cảnh"),
    ({"decision": "note", "rationale": "x", "context_refs": ["C1"]}, "thiếu nội dung"),
])
async def test_invalid_or_invented_reply_is_rejected_as_silent(w, db, redis, reply: Any, why: str) -> None:  # type: ignore[no-untyped-def]
    a = await make_agent(db, w)
    [u] = await refine(w, redis, msg(BUY))
    await hook(w, redis, FakeRouter(lambda _m: reply), [u])
    [d] = await decisions(db, a)
    assert (d.decision, d.outcome, d.draft_id) == ("silent", "rejected", None)
    assert why in d.rationale and d.cited_refs == [] and len(d.context_refs) >= 3
    assert await agent_drafts(db) == []
    assert (await db.execute(text("SELECT count(*) FROM memory.entries WHERE author = :a"),
                             {"a": f"agent:{a}"})).scalar() == 0
    res = (await db.execute(text("SELECT result FROM ops.action_log WHERE action = 'agent.decided'"))).scalar()
    assert res == "failed"


# ─── không lấy dữ liệu ngoài phạm vi ─────────────────────────────────────────

async def test_context_never_includes_out_of_scope_groups(w, db, redis) -> None:  # type: ignore[no-untyped-def]
    a = await make_agent(db, w, [w["g1"]])
    # Cùng một người nói ở g2 (ngoài phạm vi agent) rồi ở g1.
    outside, inside = await refine(w, redis, msg("Bên em cần 5 container giấy kraft gấp", group="g2"), msg(BUY))
    await db.execute(text("UPDATE clean.meaning_units SET embedding = array_fill(0.1::real, ARRAY[768])::vector"))
    await db.commit()
    router = decide("draft", refs=lambda m: list(items_in(m)))
    await hook(w, redis, router, [outside, inside])
    [d] = await decisions(db, a)
    assert d.trigger_unit_id == inside
    ids = {r["id"] for r in d.context_refs}
    assert str(outside) not in ids and str(w["g2"]) not in ids
    g2 = (await db.execute(text("SELECT code FROM core.groups WHERE id = :g"), {"g": w["g2"]})).scalar()
    [call] = router.calls
    prompt = "\n".join(m.content for m in call)
    assert g2 not in prompt and "giấy kraft" not in prompt
    leaked = (await db.execute(text("""SELECT e.id FROM memory.entries e WHERE e.refs @> CAST(:r AS jsonb)"""),
                               {"r": orjson.dumps([{"type": "group", "id": str(w["g2"])}]).decode()})).scalars().all()
    assert leaked and not ({str(x) for x in leaked} & ids)            # mục sổ tay nhắc g2 bị loại
    [dr] = await agent_drafts(db)
    assert str(outside) not in {s["ref"]["id"] for s in dr.sources}
