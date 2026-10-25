# ruff: noqa: E501
"""T8 — sửa theo review (exploit đã dựng thành test có khẳng định): lộ `reliability` qua /explain/score, agent trực nhóm nhận điểm đánh giá người,
sự kiện lừa đảo/sức khoẻ trong bản tóm tắt, tóm tắt còn dòng sự thật đã `retracted`, `payment_account` qua fact.search, `complaint.repeated`."""

import uuid
from types import SimpleNamespace
from typing import Any

from sqlalchemy import text

from gh.biz.duty import context as duty_context
from gh.biz.mem import consolidate, entities, facts
from gh.biz.people import jobs
from gh.gen.tools import TOOLS, ToolRunner
from tests.conftest import Api, verify_pin
from tests.mem_helpers import T, mkfact
from tests.phase2 import org_id
from tests.test_gen import _user_of
from tests.test_intel_reliability_t8 import NOW, person, world
from tests.test_owner_profile_support import link_owner_user, mk_person, mk_raw
from tests.test_rbac_api import login_as


async def _scored(db: Any) -> tuple[Any, uuid.UUID]:
    w = await world(db)
    p = await person(db, w, "khachr")
    assert await jobs.recompute_reliability_org(db, w.org, now=NOW) == 1
    await db.commit()
    return w, p


# ─── B1: /explain/score reliability chỉ Owner + PIN ───────────────────────────────────────────────────────────

async def test_explain_score_reliability_only_owner_with_pin(owner_api: Api, db: Any, client: Any) -> None:
    _, p = await _scored(db)
    path = f"/explain/score/person:{p}:reliability"
    for role in ("manager", "operator", "auditor"):
        api = await login_as(client, db, role)
        r = await api.get(path)
        assert r.status_code in (403, 404) and "tin cậy" not in r.text and "hứa" not in r.text, (role, r.status_code, r.text)
    r = await owner_api.get(path)
    assert r.status_code == 423, r.text                        # Owner chưa PIN
    await verify_pin(owner_api)
    r = await owner_api.get(path)
    assert r.status_code == 200, r.text
    assert (await owner_api.get(f"/explain/score/person:{p}:heat")).status_code in (200, 404)   # dimension khác không đổi


# ─── B2: agent trực nhóm không nhận điểm đánh giá người ───────────────────────────────────────────────────────

async def test_group_duty_agent_context_has_no_person_evaluation_scores(owner_api: Api, db: Any) -> None:
    w, p = await _scored(db)
    for dim in ("heat", "performance", "risk"):
        await db.execute(text("""INSERT INTO clean.current_scores (subject_type, subject_id, dimension, value, trend, snapshot_id, updated_at)
                                 VALUES ('person', :p, :d, 33, 'up', :s, now())"""), {"p": p, "d": dim, "s": uuid.uuid4()})
    ident = (await db.execute(text("SELECT id FROM core.person_identities WHERE person_id = :p"), {"p": p})).scalar_one()
    raw = await mk_raw(db, w.org, w.channel, identity=ident, body="ai mua gạo", group=w.group)
    mu = uuid.uuid4()
    await db.execute(text("""INSERT INTO clean.meaning_units (id, org_id, observed_at, group_id, person_id, event_type, conclusion, confidence, run_id)
                             VALUES (:m, :o, now(), :g, :p, 'AskedPrice', 'hỏi giá gạo', 0.9, core.uuid_v7())"""), {"m": mu, "o": w.org, "g": w.group, "p": p})
    await db.execute(text("""INSERT INTO clean.evidence (meaning_unit_id, meaning_observed_at, raw_event_id, raw_received_at)
                             SELECT :m, now(), :r, received_at FROM raw.events WHERE id = :r"""), {"m": mu, "r": raw})
    await db.commit()
    cand = duty_context.Candidate(uuid.uuid4(), mu, NOW, w.group, p, w.channel, "zalo", None, False, False)
    ctx = await duty_context.build(db, w.org, SimpleNamespace(), cand, 1)
    dims = {i.data.get("dimension") for i in ctx.items if i.kind == "score"}
    assert "reliability" not in dims and "performance" not in dims and "risk" not in dims, dims
    assert "heat" in dims                                       # điểm thương mại thường vẫn có


# ─── M2: events trong bản tóm tắt ─────────────────────────────────────────────────────────────────────────────

async def _units(db: Any, w: Any, p: uuid.UUID) -> None:
    for kind, text_ in (("rui_ro_lua_dao", "Anh K đòi chuyển 50 triệu vào STK lạ — nghi lừa đảo"),
                        ("suc_khoe_an_toan", "Anh K nói vợ đang điều trị ung thư"), (None, "Anh K hỏi giá xi măng")):
        await db.execute(text("""INSERT INTO clean.meaning_units (org_id, observed_at, group_id, person_id, event_type, conclusion, confidence, run_id, kind_hint)
                                 VALUES (:o, now(), :g, :p, 'X', :c, 0.9, core.uuid_v7(), :k)"""), {"o": w.org, "g": w.group, "p": p, "c": text_, "k": kind})
    await db.commit()


async def test_brief_events_drop_fraud_and_health_units(owner_api: Api, db: Any, app: Any) -> None:
    w = await link_owner_user(db, await org_id(db))
    p, _ = await mk_person(db, w.org, w.channel, "Anh K", "k1")
    await _units(db, w, p)
    u, tok = await _user_of(owner_api)
    r = await ToolRunner(app, u, tok).run("profile.get", {"id": str(p)})
    assert r.ok and "xi măng" in r.text
    assert "lừa đảo" not in r.text and "50 triệu" not in r.text and "ung thư" not in r.text, r.text
    assert "có cảnh báo cần xem" in r.text                         # chỉ còn câu cảnh báo, không có nội dung
    await verify_pin(owner_api)
    u, tok = await _user_of(owner_api)
    r = await ToolRunner(app, u, tok).run("profile.get", {"id": str(p)})
    assert "lừa đảo" not in r.text and "50 triệu" not in r.text    # lừa đảo: không bao giờ tóm tắt ở chat, kể cả có PIN
    assert "ung thư" in r.text                                      # sức khoẻ: có PIN thì thấy


async def test_explain_get_meaning_unit_fraud_and_health_blocked_in_chat(owner_api: Api, db: Any, app: Any) -> None:
    w = await link_owner_user(db, await org_id(db))
    p, _ = await mk_person(db, w.org, w.channel, "Anh K", "k1")
    await _units(db, w, p)
    ids = {r.kind_hint: r.id for r in (await db.execute(text("SELECT id, kind_hint FROM clean.meaning_units WHERE person_id = :p"), {"p": p})).all()}
    u, tok = await _user_of(owner_api)
    run = ToolRunner(app, u, tok)
    for kind in ("rui_ro_lua_dao", "suc_khoe_an_toan"):
        r = await run.run("explain.get", {"kind": "meaning_unit", "id": str(ids[kind])})
        assert r.error == "FORBIDDEN" and "lừa đảo" not in r.text and "ung thư" not in r.text, (kind, r.text)
    await verify_pin(owner_api)
    u, tok = await _user_of(owner_api)
    run = ToolRunner(app, u, tok)
    assert (await run.run("explain.get", {"kind": "meaning_unit", "id": str(ids["rui_ro_lua_dao"])})).error == "FORBIDDEN"   # lừa đảo luôn chặn ở chat
    assert (await run.run("explain.get", {"kind": "meaning_unit", "id": str(ids["suc_khoe_an_toan"])})).error != "FORBIDDEN"
    assert (await run.run("explain.get", {"kind": "meaning_unit", "id": str(ids[None])})).error != "FORBIDDEN"


# ─── minor ────────────────────────────────────────────────────────────────────────────────────────────────────

async def test_brief_summary_drops_retracted_facts(owner_api: Api, db: Any) -> None:
    w = await link_owner_user(db, await org_id(db))
    pid, _ = await mk_person(db, w.org, w.channel, "Chị Lan", "lan1")
    ent = (await entities.ensure_person(db, w.org, pid)).id
    f = await facts.assert_fact(db, mkfact(w.org, ent, {"text": "gửi mẫu xi măng"}, T(2), predicate="promised_to"))
    await consolidate.rebuild_summaries(db, w.org)
    await db.execute(text("UPDATE mem.facts SET status = 'retracted' WHERE id = :i"), {"i": f.id})
    await db.commit()
    b = (await owner_api.get(f"/profile/{pid}/brief")).json()
    assert "xi măng" not in str(b["summary"]) and "xi măng" not in str(b["facts"])


async def test_fact_search_payment_account_never_returns_account_from_facts(owner_api: Api, db: Any, app: Any) -> None:
    w = await link_owner_user(db, await org_id(db))
    pid, _ = await mk_person(db, w.org, w.channel, "Chị Lan", "lan1")
    ent = (await entities.ensure_person(db, w.org, pid)).id
    await facts.assert_fact(db, mkfact(w.org, ent, {"digest": "b" * 64, "last3": "654", "bank": "MBB"}, T(4), predicate="payment_account", cls="owner"))
    await db.commit()
    await verify_pin(owner_api)
    u, tok = await _user_of(owner_api)
    run = ToolRunner(app, u, tok)
    for args in ({"subject_id": str(ent), "predicate": "payment_account"}, {"subject_id": str(ent)}):
        r = await run.run("fact.search", args)
        assert "MBB" not in r.text and '"last3"' not in r.text, r.text
    r = await run.run("entity.timeline", {"id": str(ent), "lanes": "transactions,promises,events,conflicts"})
    assert "MBB" not in r.text
    b = await run.run("person.brief", {"id": str(pid)})
    assert "MBB" not in b.text


async def test_complaint_repeated_owner_only(owner_api: Api, db: Any, client: Any) -> None:
    assert TOOLS["complaint.repeated"].owner_only
