# ruff: noqa: E501
"""Trần tình báo `settings.ai_cost.intel_*` (spec §4D.5, CNS-100): MỘT nguồn chi phí (`gh/ai_cost.py`), route `PUT` đặt trần, gắn thẻ
`thread_key`/`analysis_id` cho `agent.model_calls`; không có bảng giá hay sổ riêng."""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import text

from gh import ai_cost
from gh.db import sessionmaker
from tests.conftest import Api, verify_pin
from tests.phase2 import org_id
from tests.refinery_group_helpers import add_call, mk_model, set_ai_cost
from tests.test_rbac_api import login_as


def test_defaults_match_the_spec_table() -> None:
    d = ai_cost.INTEL_DEFAULTS
    assert d["intel_daily_call_cap"] == 400 and d["intel_safety_call_cap"] == 60 and d["intel_gate_call_cap"] == 40
    assert d["intel_daily_budget_vnd"] == 0 and d["intel_safety_budget_vnd"] == 0 and d["intel_gate_budget_vnd"] == 0   # chế độ thuê bao (Q14)
    assert d["intel_media_daily_budget_vnd"] == 26_000
    assert set(ai_cost.INTEL_KEYS) == set(d)


async def test_get_and_set_intel_keep_other_ai_cost_keys(app, db) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    assert await ai_cost.get_intel(db, org) == ai_cost.INTEL_DEFAULTS
    await ai_cost.set_budget(db, org, 5_000)                                        # khoá cũ daily_budget_vnd
    await ai_cost.set_intel(db, org, {"intel_daily_call_cap": 100, "intel_media_daily_budget_vnd": 0})
    await db.commit()
    got = await ai_cost.get_intel(db, org)
    assert got["intel_daily_call_cap"] == 100 and got["intel_media_daily_budget_vnd"] == 0 and got["intel_safety_call_cap"] == 60
    assert await ai_cost.get_budget(db, org) == 5_000                                # không đụng khoá cũ


async def test_get_intel_ignores_bad_values_in_db(app, db) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    await set_ai_cost(db, org, intel_daily_call_cap=-5, intel_safety_call_cap=True, intel_gate_call_cap="x", intel_daily_budget_vnd=12.0)   # type: ignore[arg-type]
    got = await ai_cost.get_intel(db, org)
    assert got["intel_daily_call_cap"] == 400 and got["intel_safety_call_cap"] == 60 and got["intel_gate_call_cap"] == 40
    assert got["intel_daily_budget_vnd"] == 12


@pytest.mark.parametrize("bad", [{"nope": 1}, {"intel_daily_call_cap": -1}, {"intel_daily_call_cap": True}, {"intel_daily_call_cap": 100_001},
                                 {"intel_daily_budget_vnd": 10_000_000_001}, {"intel_gate_call_cap": 1.5}])
async def test_set_intel_rejects_invalid(app, db, bad: dict[str, Any]) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(ValueError):
        await ai_cost.set_intel(db, await org_id(db), bad)


async def test_put_intel_route_sets_resets_and_audits(owner_api: Api, db) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    r = await owner_api.send("GET", "/system/ai-cost/intel")
    assert r.status_code == 200 and r.json()["values"] == ai_cost.INTEL_DEFAULTS and r.json()["defaults"] == ai_cost.INTEL_DEFAULTS
    r = await owner_api.send("PUT", "/system/ai-cost/intel", {"intel_daily_call_cap": 250})
    assert r.status_code == 423, r.text                                                                   # cần phiên PIN như các route nhạy cảm cùng file
    await verify_pin(owner_api)
    r = await owner_api.send("PUT", "/system/ai-cost/intel", {"intel_daily_call_cap": 250, "intel_safety_call_cap": 30})
    assert r.status_code == 200, r.text
    assert r.json()["values"]["intel_daily_call_cap"] == 250 and r.json()["values"]["intel_gate_call_cap"] == 40
    assert (await ai_cost.get_intel(db, org))["intel_safety_call_cap"] == 30
    r = await owner_api.send("PUT", "/system/ai-cost/intel", {"intel_daily_call_cap": None})            # null ⇒ về mặc định
    assert r.json()["values"]["intel_daily_call_cap"] == 400 and r.json()["values"]["intel_safety_call_cap"] == 30
    r = await owner_api.send("PUT", "/system/ai-cost/intel", {})                                         # không có khoá ⇒ không đổi, không ghi nhật ký
    assert r.status_code == 200 and r.json()["values"]["intel_safety_call_cap"] == 30
    rows = (await db.execute(text("SELECT detail FROM ops.action_log WHERE action = 'ai.intel_budget_changed' ORDER BY at"))).scalars().all()
    assert len(rows) == 2 and rows[0] == {"from": {"intel_daily_call_cap": 400, "intel_safety_call_cap": 60},
                                          "to": {"intel_daily_call_cap": 250, "intel_safety_call_cap": 30}}


async def test_put_intel_route_validation_and_permissions(owner_api: Api, client: Any, db: Any) -> None:
    await verify_pin(owner_api)
    for body in ({"intel_daily_call_cap": -1}, {"intel_daily_call_cap": 100_001}, {"intel_daily_budget_vnd": 10_000_000_001},
                 {"khong_co_khoa": 1}, {"intel_gate_call_cap": "abc"}):
        assert (await owner_api.send("PUT", "/system/ai-cost/intel", body)).status_code == 422, body
    mgr = await login_as(client, db, "manager")
    assert (await mgr.send("PUT", "/system/ai-cost/intel", {"intel_daily_call_cap": 1})).status_code == 403
    assert (await ai_cost.get_intel(db, await org_id(db)))["intel_daily_call_cap"] == 400
    anon = Api(client.__class__(transport=client._transport, base_url="http://test"))
    assert (await anon.send("PUT", "/system/ai-cost/intel", {"intel_daily_call_cap": 1})).status_code in (401, 403)


async def test_tag_calls_tags_only_untagged_rows_of_that_purpose_and_window(app, db) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    m = await mk_model(db, org)
    t0 = datetime.now(UTC)
    await add_call(db, org, m, "refinery.group", at=t0 - timedelta(minutes=10))                         # trước cửa sổ
    await add_call(db, org, m, "refinery.group", at=t0)                                                 # trong cửa sổ
    await add_call(db, org, m, "refinery.group", at=t0 + timedelta(seconds=1))
    await add_call(db, org, m, "refinery.safety", at=t0)                                                # khác purpose
    await add_call(db, org, m, "refinery.group", at=t0, thread_key="g:da-co-the")                       # đã có thẻ
    aid = uuid.uuid4()
    n = await ai_cost.tag_calls(db, org, thread_key="g:abc", analysis_id=aid, purpose="refinery.group", since=t0 - timedelta(seconds=1))
    await db.commit()
    assert n == 2
    rows = (await db.execute(text("SELECT purpose, thread_key, analysis_id FROM agent.model_calls ORDER BY at, purpose, thread_key NULLS FIRST"))).all()
    tagged = [(r.purpose, r.thread_key) for r in rows if r.analysis_id == aid]
    assert tagged == [("refinery.group", "g:abc")] * 2
    assert sum(1 for r in rows if r.thread_key is None) == 2                                            # dòng cũ + dòng khác purpose còn nguyên
    assert sum(1 for r in rows if r.thread_key == "g:da-co-the" and r.analysis_id is None) == 1


async def test_tag_calls_does_not_change_cost_or_totals(app, db) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    paid = await mk_model(db, org, kind="openrouter", price=(10_000.0, 40_000.0))
    t0 = datetime.now(UTC)
    await add_call(db, org, paid, "refinery.group", at=t0, tin=100_000, tout=50_000)                    # 3.000 ₫
    before = await ai_cost.day_cost(db, org, ai_cost.today_vn(t0))
    await ai_cost.tag_calls(db, org, thread_key="g:abc", analysis_id=uuid.uuid4(), purpose="refinery.group", since=t0 - timedelta(seconds=1))
    await db.commit()
    after = await ai_cost.day_cost(db, org, ai_cost.today_vn(t0))
    assert before == after and after["total_vnd"] == 3000
    v = (await db.execute(text("SELECT thread_key, calls, cost_vnd FROM refinery.group_budget"))).one()
    assert v.thread_key == "g:abc" and v.calls == 1 and ai_cost.vnd(v.cost_vnd) == 3000                  # view theo luồng khớp sổ ngày


async def test_no_second_cost_ledger_or_price_table_was_added(app, db) -> None:  # type: ignore[no-untyped-def]
    n = (await db.execute(text("""SELECT count(*) FROM information_schema.columns WHERE table_schema = 'refinery'
                                  AND (column_name ILIKE '%price%' OR column_name ILIKE '%vnd_per%')"""))).scalar_one()
    assert n == 0
    assert sessionmaker() is not None
