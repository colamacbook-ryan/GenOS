# ruff: noqa: E501
"""v0.1.76 (gói T6) — W4 Cơ hội & kết nối: khớp cầu của khách với hàng của Sếp (catalog + `owner_resource`), chặn bởi `owner_value_limit`,
tắt theo bối cảnh (INT-16), cổng 🔴 `co_hoi_lon_sap_troi` (spec r6.6.1 §4.2, §4.2.1, §4B.3, §4C). Chạy trên PostgreSQL thật.

Detector không gán mức: chỉ dựng `levels.SignalIn` rồi `signals.upsert`; mức do `levels.compute` quyết."""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

import orjson
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gh.biz.hooks import HookCtx
from gh.biz.intel import catalog, levels
from gh.biz.intel.detectors import catalog_match
from gh.biz.market import service as msvc
from gh.biz.market.jobs import market_signal_capture
from gh.biz.mem import contexts, facts
from gh.db import sessionmaker
from gh.refinery import clock
from tests.mem_helpers import T, mk_org, mkfact
from tests.test_owner_profile_support import mk_person, mk_raw, mk_world

QUOTE_S22 = "Cần gấp 50 tấn thép phi 16 giao Cần Thơ"


# ─── hàm thuần: adapter catalog → cung, luật Hồ sơ ───────────────────────────

def _item(name: str = "thép phi 16", unit: str | None = "tấn", price: int | None = 14_600_000) -> catalog.Item:
    return catalog.Item(uuid.uuid4(), name, None, unit, price, [], "active", "owner", clock.now())


def test_adapter_catalog_to_supply_s22_scores_at_least_75() -> None:
    demand = {"item": "thép phi 16", "quantity": 50.0, "unit": "tấn", "value_vnd": 730_000_000.0, "location": "Cần Thơ"}
    score, reasons = msvc.score_match(demand, msvc.catalog_as_supply(_item(), demand))
    assert score >= 75 and any("Cùng mặt hàng" in r for r in reasons)


def test_adapter_unrelated_goods_do_not_match() -> None:
    demand = {"item": "thợ sơn", "quantity": 2.0, "unit": "người", "value_vnd": None, "location": None}
    assert msvc.score_match(demand, msvc.catalog_as_supply(_item(), demand))[0] == 0.0


def test_adapter_does_not_invent_value_when_units_differ_or_price_missing() -> None:
    demand = {"item": "thép phi 16", "quantity": 50.0, "unit": "cây", "value_vnd": 730_000_000.0, "location": None}
    assert msvc.catalog_as_supply(_item(unit="tấn"), demand)["value_vnd"] is None
    assert msvc.catalog_as_supply(_item(price=None), {**demand, "unit": "tấn"})["value_vnd"] is None


def test_parse_value_limits_min_value_and_forbidden_goods() -> None:
    lim = catalog_match.parse_value_limits(["không nhận đơn dưới 10 triệu", "không làm hàng xi măng", "không bàn chính trị"])
    assert lim.min_value_vnd == 10_000_000
    assert catalog_match.touches_forbidden(lim, "xi măng PCB40") is True
    assert catalog_match.touches_forbidden(lim, "thép phi 16") is False
    assert catalog_match.below_min(lim, 8_000_000) is True and catalog_match.below_min(lim, 10_000_000) is False
    assert catalog_match.below_min(lim, None) is False                       # không biết giá trị ⇒ không tự kết luận trái hồ sơ


# ─── DB ──────────────────────────────────────────────────────────────────────

async def _setup(db: AsyncSession, *, ctx: str | None = "kinh_doanh", items: tuple[tuple[str, str, int], ...] = (("thép phi 16", "tấn", 14_600_000),)) -> Any:
    w = await mk_world(db, await mk_org(db))
    s = {"installed_at": "2026-09-01T00:00:00+07:00", "m1_passed_gates": list(levels.REGULAR_GATES)}
    await db.execute(text("UPDATE core.organizations SET settings = settings || jsonb_build_object('intel', CAST(:s AS jsonb)) WHERE id = :o"),
                     {"s": orjson.dumps(s).decode(), "o": w.org})
    await db.execute(text("UPDATE core.groups SET member_count = 20 WHERE id = :g"), {"g": w.group})
    if ctx:
        await contexts.set_group_contexts(db, w.org, w.group, [{"context": ctx}], user_id=w.user)
    for name, unit, price in items:
        await catalog.create(db, w.org, name=name, user_id=w.user, unit=unit, price_vnd=price)
    await db.commit()
    return w


async def _owner_fact(db: AsyncSession, w: Any, predicate: str, value: str) -> None:
    await facts.assert_fact(db, mkfact(w.org, w.owner, value, T(1), predicate=predicate, cls="owner", confirmed_by=w.user))
    await db.commit()


async def _demand(db: AsyncSession, w: Any, *, product: str = "thép phi 16", qty: float = 50, unit: str = "tấn", budget: int | None = 730_000_000,
                  body: str = QUOTE_S22, needed_in_days: int | None = None, person: uuid.UUID | None = None, name: str = "Anh Khoa") -> tuple[uuid.UUID, uuid.UUID]:
    """Một tin ĐẾN của khách trong nhóm w.group ⇒ đơn vị cầu có trích dẫn đã kiểm ⇒ chạy hook `market_signal_capture` (đường thật).
    Trả (unit_id, person_id)."""
    now = clock.now()
    if person is None:
        person, iid = await mk_person(db, w.org, w.channel, name)
    else:
        iid = (await db.execute(text("SELECT id FROM core.person_identities WHERE person_id = :p"), {"p": person})).scalar_one()
    at = now - timedelta(hours=1)
    raw = await mk_raw(db, w.org, w.channel, identity=iid, body=body, at=at, group=w.group)
    ent: dict[str, Any] = {"product": product, "qty": qty, "unit": unit, "place": "Cần Thơ"}
    if budget is not None:
        ent["budget_vnd"] = budget
    if needed_in_days is not None:
        ent["deadline"] = (now.astimezone(levels.VN).date() + timedelta(days=needed_in_days)).isoformat()
    unit_id = (await db.execute(text("""
        INSERT INTO clean.meaning_units (org_id, observed_at, group_id, person_id, event_type, side, conclusion, entities, confidence, run_id)
        VALUES (:o, :t, :g, :p, 'AskedPrice', 'demand', :c, CAST(:e AS jsonb), 0.9, core.uuid_v7()) RETURNING id"""),
                               {"o": w.org, "t": at, "g": w.group, "p": person, "c": body, "e": orjson.dumps(ent).decode()})).scalar_one()
    await db.execute(text("""
        INSERT INTO clean.evidence (meaning_unit_id, meaning_observed_at, raw_event_id, raw_received_at, quote, quote_status)
        SELECT :u, :t, e.id, e.received_at, :q, 'verified' FROM raw.events e WHERE e.id = :r"""), {"u": unit_id, "t": at, "r": raw, "q": body})
    await db.commit()
    await market_signal_capture(HookCtx(org_id=w.org, unit_ids=[unit_id], run_id=None, sm=sessionmaker(), redis=None, bus=None, router=None))  # type: ignore[arg-type]
    return unit_id, person


async def _alerts(db: AsyncSession, org: uuid.UUID) -> list[Any]:
    return list((await db.execute(text("""SELECT id, level, gate, candidate, delivery, reasons, case_key, level_input, facts FROM biz.alerts
                                          WHERE org_id = :o AND kind = 'co_hoi_ket_noi' ORDER BY created_at"""), {"o": org})).all())


async def _one(db: AsyncSession, org: uuid.UUID) -> Any:
    rows = await _alerts(db, org)
    assert len(rows) == 1, rows
    return rows[0]


# ─── cổng 🔴 co_hoi_lon_sap_troi: chỉ khi thoả ĐỦ điều kiện ───────────────────

async def test_s22_regression_red_via_gate_and_catalog_match_saved(db) -> None:  # type: ignore[no-untyped-def]
    w = await _setup(db)
    await _demand(db, w)
    a = await _one(db, w.org)
    assert a.level == "red" and a.gate == levels.G5 and a.delivery == "push_now"
    m = (await db.execute(text("""SELECT m.score, s.meaning_unit_id FROM biz.matches m JOIN biz.market_signals s ON s.id = m.supply_id
                                  WHERE m.org_id = :o"""), {"o": w.org})).all()
    assert len(m) == 1 and float(m[0].score) >= 75
    assert m[0].meaning_unit_id == (await db.execute(text("SELECT id FROM biz.catalog_items WHERE org_id = :o"), {"o": w.org})).scalar_one()


async def test_gate_needs_both_big_and_about_to_slip(db) -> None:  # type: ignore[no-untyped-def]
    w = await _setup(db)
    await _demand(db, w, body="Cần 50 tấn thép phi 16 giao Cần Thơ", name="A")             # lớn, không gấp, không hạn ⇒ không cổng
    r = await _one(db, w.org)
    assert r.level == "amber" and r.gate is None
    w2 = await _setup(db)
    await _demand(db, w2, body="Cần 50 tấn thép phi 16 giao Cần Thơ", needed_in_days=1)    # hạn ngày mai (< 48 giờ) ⇒ cổng
    r2 = await _one(db, w2.org)
    assert r2.level == "red" and r2.gate == levels.G5
    w3 = await _setup(db)
    await _demand(db, w3, body="Cần 50 tấn thép phi 16 giao Cần Thơ", needed_in_days=4)    # còn xa ⇒ chưa sắp trôi
    r3 = await _one(db, w3.org)
    assert r3.level != "red" and r3.gate is None
    w4 = await _setup(db)
    await _demand(db, w4, body="Cần 50 tấn thép phi 16 giao Cần Thơ", needed_in_days=-1)   # hạn đã qua ⇒ đã trôi ⇒ không cổng
    r4 = await _one(db, w4.org)
    assert r4.level != "red" and r4.gate is None


async def test_gate_not_for_small_match_even_if_urgent(db) -> None:  # type: ignore[no-untyped-def]
    """Gấp nhưng khớp yếu (thiếu ngân sách/số lượng ⇒ < 75 điểm) ⇒ không `impact 3` ⇒ không cổng."""
    w = await _setup(db)
    await _demand(db, w, qty=0, budget=None, body="Cần gấp thép phi 16")
    r = await _one(db, w.org)
    assert r.level != "red" and r.gate is None


async def test_unconfirmed_group_catalog_alone_is_not_enough_but_confirmed_owner_resource_is(db) -> None:  # type: ignore[no-untyped-def]
    w = await _setup(db, ctx=None)                                                           # nhóm chưa xác nhận nhãn
    await _demand(db, w)
    r = await _one(db, w.org)
    assert r.level != "red" and r.gate is None                                              # catalog ≥ 75 một mình không đủ (A19)
    w2 = await _setup(db, ctx=None)
    await _owner_fact(db, w2, "owner_resource", "thép phi 16")                               # Sếp đã xác nhận Nguồn lực khớp
    await _demand(db, w2)
    r2 = await _one(db, w2.org)
    assert r2.level == "red" and r2.gate == levels.G5


# ─── tắt theo bối cảnh (INT-16) ──────────────────────────────────────────────

@pytest.mark.parametrize("ctx", ["gia_dinh", "ban_be"])
async def test_opportunity_off_in_family_and_friends_groups(db, ctx: str) -> None:  # type: ignore[no-untyped-def]
    w = await _setup(db, ctx=ctx)
    await _demand(db, w)
    r = await _one(db, w.org)
    assert r.level == "off" and r.gate is None and r.delivery == "none"


async def test_family_group_stays_off_even_with_confirmed_resource_match(db) -> None:  # type: ignore[no-untyped-def]
    w = await _setup(db, ctx="gia_dinh")
    await _owner_fact(db, w, "owner_resource", "thép phi 16")
    await _demand(db, w)
    r = await _one(db, w.org)
    assert r.level == "off" and r.gate is None                                              # gia_dinh tắt hẳn cổng (A18)


async def test_friends_group_with_confirmed_resource_match_at_most_amber(db) -> None:  # type: ignore[no-untyped-def]
    w = await _setup(db, ctx="ban_be")
    await _owner_fact(db, w, "owner_resource", "thép phi 16")
    await _demand(db, w)
    r = await _one(db, w.org)
    assert r.level in ("amber", "grey") and r.gate is None


# ─── trái Hồ sơ Sếp ⇒ không được báo ─────────────────────────────────────────

async def test_opportunity_below_owner_value_limit_is_never_reported(db) -> None:  # type: ignore[no-untyped-def]
    w = await _setup(db, items=(("xi măng PCB40", "bao", 90_000),))
    await _owner_fact(db, w, "owner_value_limit", "không nhận đơn dưới 10 triệu")
    await _demand(db, w, product="xi măng PCB40", qty=50, unit="bao", budget=5_000_000, body="Cần gấp 50 bao xi măng PCB40")   # 5 triệu < 10 triệu
    r = await _one(db, w.org)
    assert r.level in ("grey", "off") and r.gate is None and r.delivery not in ("push_now", "digest_top", "digest", "digest_morning")
    assert levels.SignalIn.model_validate(r.level_input).opportunity.touches_value_limit is True


async def test_opportunity_at_or_above_value_limit_still_reported(db) -> None:  # type: ignore[no-untyped-def]
    w = await _setup(db, items=(("xi măng PCB40", "bao", 90_000),))
    await _owner_fact(db, w, "owner_value_limit", "không nhận đơn dưới 10 triệu")
    await _demand(db, w, product="xi măng PCB40", qty=500, unit="bao", budget=45_000_000, body="Cần gấp 500 bao xi măng PCB40")
    r = await _one(db, w.org)
    assert levels.SignalIn.model_validate(r.level_input).opportunity.touches_value_limit is False and r.level in ("amber", "red")


async def test_forbidden_goods_opportunity_is_not_created_at_all(db) -> None:  # type: ignore[no-untyped-def]
    w = await _setup(db, items=(("xi măng PCB40", "bao", 90_000),))
    await _owner_fact(db, w, "owner_value_limit", "không làm hàng xi măng")
    await _demand(db, w, product="xi măng PCB40", qty=500, unit="bao", budget=45_000_000, body="Cần gấp 500 bao xi măng PCB40")
    assert await _alerts(db, w.org) == []


async def test_unconfirmed_value_limit_is_not_used(db) -> None:  # type: ignore[no-untyped-def]
    """Chỉ `owner_confirmed` mới chấm mức (§4C): giới hạn mới ở dạng `proposed` không chặn cơ hội."""
    w = await _setup(db, items=(("xi măng PCB40", "bao", 90_000),))
    await facts.assert_fact(db, mkfact(w.org, w.owner, "không nhận đơn dưới 10 triệu", T(1), predicate="owner_value_limit", cls="owner", proposed=True))
    await db.commit()
    await _demand(db, w, product="xi măng PCB40", qty=50, unit="bao", budget=5_000_000, body="Cần gấp 50 bao xi măng PCB40")
    r = await _one(db, w.org)
    assert levels.SignalIn.model_validate(r.level_input).opportunity.touches_value_limit is False


# ─── không báo một cơ hội nhiều lần (gộp theo case_key) ──────────────────────

async def test_same_demand_processed_twice_is_one_signal(db) -> None:  # type: ignore[no-untyped-def]
    w = await _setup(db)
    uid, _ = await _demand(db, w)
    sid = (await db.execute(text("SELECT id FROM biz.market_signals WHERE meaning_unit_id = :u"), {"u": uid})).scalar_one()
    for _ in range(2):
        await catalog_match.process_demand(db, w.org, sid, clock.now())
    await catalog_match.recompute_org(db, w.org, clock.now())
    await db.commit()
    await market_signal_capture(HookCtx(org_id=w.org, unit_ids=[uid], run_id=None, sm=sessionmaker(), redis=None, bus=None, router=None))  # type: ignore[arg-type]
    a = await _one(db, w.org)
    assert a.case_key.startswith("co_hoi_ket_noi:demand|")
    assert (await db.execute(text("SELECT count(*) FROM biz.matches WHERE org_id = :o"), {"o": w.org})).scalar_one() == 1


async def test_two_catalog_items_matching_one_demand_still_one_signal(db) -> None:  # type: ignore[no-untyped-def]
    w = await _setup(db, items=(("thép phi 16", "tấn", 14_600_000), ("thép phi 16 cao cấp", "tấn", 14_000_000)))
    await _demand(db, w)
    await _one(db, w.org)


async def test_same_customer_repeating_same_need_merges_into_open_signal(db) -> None:  # type: ignore[no-untyped-def]
    w = await _setup(db)
    _, person = await _demand(db, w)
    await _demand(db, w, person=person, body="Anh ơi thép phi 16 50 tấn nhé, gấp")
    a = await _one(db, w.org)
    assert (await db.execute(text("SELECT evidence_n FROM biz.alerts WHERE id = :i"), {"i": a.id})).scalar_one() == 2   # bằng chứng gắn thêm, không thẻ mới


async def test_different_customers_same_goods_are_separate_opportunities(db) -> None:  # type: ignore[no-untyped-def]
    w = await _setup(db)
    await _demand(db, w, name="Anh Khoa")
    await _demand(db, w, name="Chị Lan")
    assert len(await _alerts(db, w.org)) == 2


# ─── không ghép bừa, không báo tin của mình ──────────────────────────────────

async def test_unrelated_demand_creates_no_signal(db) -> None:  # type: ignore[no-untyped-def]
    w = await _setup(db)
    await _demand(db, w, product="thợ sơn", qty=2, unit="người", budget=None, body="Cần gấp thợ sơn nhà")
    assert await _alerts(db, w.org) == []


async def test_archived_or_suggested_catalog_item_is_not_a_resource(db) -> None:  # type: ignore[no-untyped-def]
    w = await _setup(db)
    iid = (await db.execute(text("SELECT id FROM biz.catalog_items WHERE org_id = :o"), {"o": w.org})).scalar_one()
    await catalog.update(db, w.org, iid, state="suggested")
    await db.commit()
    await _demand(db, w)
    assert await _alerts(db, w.org) == []


async def test_rerun_after_catalog_archived_drops_open_suggestions(db) -> None:  # type: ignore[no-untyped-def]
    w = await _setup(db)
    await _demand(db, w)
    iid = (await db.execute(text("SELECT id FROM biz.catalog_items WHERE org_id = :o"), {"o": w.org})).scalar_one()
    await catalog.archive(db, w.org, iid)
    await catalog_match.recompute_org(db, w.org, clock.now())
    await db.commit()
    assert (await db.execute(text("SELECT count(*) FROM biz.matches WHERE org_id = :o AND status = 'suggested'"), {"o": w.org})).scalar_one() == 0


# ─── GET /matches?source= ────────────────────────────────────────────────────

async def test_matches_endpoint_source_filter(app, db, owner_api) -> None:  # type: ignore[no-untyped-def]
    from tests.test_owner_profile_support import link_owner_user

    org = (await db.execute(text("SELECT id FROM core.organizations LIMIT 1"))).scalar_one()
    w = await link_owner_user(db, org)
    await catalog.create(db, org, name="thép phi 16", user_id=w.user, unit="tấn", price_vnd=14_600_000)
    await db.commit()
    await _demand(db, w)
    r = await owner_api.get("/matches?source=catalog")
    assert r.status_code == 200 and r.json()["total"] == 1
    assert (await owner_api.get("/matches?source=market")).json()["total"] == 0
    assert (await owner_api.get("/matches")).json()["total"] == 1
    assert (await owner_api.get("/matches?source=bua")).status_code == 422


async def test_catalog_supply_hidden_from_non_owner_but_owner_sees(app, db, client, owner_api) -> None:  # type: ignore[no-untyped-def]
    """Bảng giá nội bộ: tín hiệu cung tổng hợp từ catalog không lưu giá, và `/supply`, `/matches`, `?source=catalog`, `/opportunities` ẩn với
    vai không phải Owner (kể cả vai phạm vi ALL như operator/auditor)."""
    from tests.test_owner_profile_support import link_owner_user
    from tests.test_rbac_api import login_as

    org = (await db.execute(text("SELECT id FROM core.organizations LIMIT 1"))).scalar_one()
    w = await link_owner_user(db, org)
    await catalog.create(db, org, name="thép phi 16", user_id=w.user, unit="tấn", price_vnd=14_600_000)
    await db.commit()
    await _demand(db, w)
    assert (await db.execute(text("SELECT count(*) FROM biz.market_signals WHERE org_id = :o AND side = 'supply' AND value_vnd IS NOT NULL"),
                             {"o": org})).scalar_one() == 0                                    # không lưu giá bán của Sếp
    cat_supply = (await db.execute(text("SELECT id FROM biz.market_signals WHERE org_id = :o AND side = 'supply'"), {"o": org})).scalar_one()
    mid = (await db.execute(text("SELECT id FROM biz.matches WHERE org_id = :o"), {"o": org})).scalar_one()

    owner_supply = (await owner_api.get("/supply?side=supply")).json()
    assert owner_supply["total"] == 1 and "thép phi 16" in owner_supply["items"][0]["item"]
    assert (await owner_api.get("/matches?source=catalog")).json()["total"] == 1

    for role in ("operator", "auditor"):
        api = await login_as(client, db, role)
        sup = await api.get("/supply?side=supply")
        assert sup.status_code == 200 and sup.json()["total"] == 0 and "thép phi 16" not in sup.text and "14600000" not in sup.text
        assert (await api.get(f"/supply/{cat_supply}")).status_code == 404
        m = await api.get("/matches")
        assert m.status_code == 200 and m.json()["total"] == 0 and "thép phi 16" not in m.text and "14.600.000" not in m.text
        assert (await api.get("/matches?source=catalog")).status_code == 403
        assert (await api.get("/matches?source=market")).json()["total"] == 0
        assert "thép phi 16" not in (await api.get("/opportunities")).text
        assert (await api.send("POST", f"/matches/{mid}/reject")).status_code in (403, 404)   # auditor không có quyền ghi
