"""v0.1.41 (F-84): "Chi phí AI hôm nay" theo agent (giờ VN) + bảng giá theo model + "Trần chi phí mỗi ngày".

Số liệu mẫu cố định, tính tay (giá VND / 1 triệu token):
- m1 (OpenRouter, giá Owner 10.000 vào / 40.000 ra), m2 (giá 20.000 / 80.000), m3 (chưa có giá), m4 (Claude Code
  CLI — trả theo gói).
- Ngày D = 15/09/2026 giờ VN = [14/09 17:00 UTC, 15/09 17:00 UTC).
  · core.refinery m1 100.000 vào + 50.000 ra lúc 03:00 UTC  ⇒ 1.000 + 2.000 = 3.000 ₫
  · core.refinery m3 1.000 + 1.000 (chưa có giá)            ⇒ 0 ₫, unpriced 1
  · core.gen m2 200.000 + 25.000 lúc 14/09 23:30 UTC (= 06:30 VN hôm D) ⇒ 4.000 + 2.000 = 6.000 ₫
  · core.gen m4 (CLI) 500.000 + 500.000                     ⇒ 0 ₫ (subscription, không tính unpriced)
  · core.gen m1 status 'error' 1.000.000 vào                ⇒ không tính
  · core.refinery m1 1.000.000 vào lúc 15/09 17:30 UTC (= 00:30 VN ngày 16/09) ⇒ thuộc ngày D+1: 10.000 ₫
  Tổng D = 9.000 ₫ (refinery 3.000, gen 6.000).
"""

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import text

from gh import ai_cost, health
from gh.db import admin_sessionmaker, sessionmaker
from tests.conftest import Api
from tests.test_rbac_api import login_as

D = date(2026, 9, 15)


def _utc(y: int, mo: int, d: int, h: int, mi: int = 0) -> datetime:
    return datetime(y, mo, d, h, mi, tzinfo=UTC)


async def _org() -> Any:
    async with admin_sessionmaker()() as db:
        return (await db.execute(text("SELECT id FROM core.organizations"))).scalar_one()


async def _provider(db: Any, org: Any, kind: str, name: str, models: list[str]) -> list[Any]:
    pid = (await db.execute(text("""INSERT INTO agent.providers (org_id, kind, name, auth_state)
                                    VALUES (:o, :k, :n, 'ok') RETURNING id"""),
                            {"o": org, "k": kind, "n": name})).scalar_one()
    out = []
    for m in models:
        out.append((await db.execute(text("""INSERT INTO agent.models (provider_id, model_name)
                                             VALUES (:p, :m) RETURNING id"""), {"p": pid, "m": m})).scalar_one())
    return out


async def _call(db: Any, org: Any, model: Any, agent_key: str, at: datetime, tin: int, tout: int,
                status: str = "ok") -> None:
    await db.execute(text("""INSERT INTO agent.model_calls (org_id, at, model_id, agent_key, purpose, tokens_in,
                                                            tokens_out, latency_ms, status)
                             VALUES (:o, :at, :m, :a, 'test', :ti, :to, 10, :s)"""),
                     {"o": org, "at": at, "m": model, "a": agent_key, "ti": tin, "to": tout, "s": status})


async def _price(db: Any, org: Any, model: Any, pin: float, pout: float) -> None:
    await db.execute(text("""INSERT INTO agent.model_prices (model_id, org_id, in_vnd_per_mtok, out_vnd_per_mtok)
                             VALUES (:m, :o, :i, :x)"""), {"m": model, "o": org, "i": pin, "x": pout})


async def _seed() -> dict[str, Any]:
    org = await _org()
    async with admin_sessionmaker()() as db:
        m1, m2, m3 = await _provider(db, org, "openai_compat", "OpenRouter", ["m1", "m2", "m3"])
        (m4,) = await _provider(db, org, "claude_code_cli", "Claude Code", ["sonnet"])
        await _price(db, org, m1, 10_000, 40_000)
        await _price(db, org, m2, 20_000, 80_000)
        await _call(db, org, m1, "core.refinery", _utc(2026, 9, 15, 3), 100_000, 50_000)
        await _call(db, org, m3, "core.refinery", _utc(2026, 9, 15, 5), 1_000, 1_000)
        await _call(db, org, m2, "core.gen", _utc(2026, 9, 14, 23, 30), 200_000, 25_000)
        await _call(db, org, m4, "core.gen", _utc(2026, 9, 15, 8), 500_000, 500_000)
        await _call(db, org, m1, "core.gen", _utc(2026, 9, 15, 9), 1_000_000, 0, status="error")
        await _call(db, org, m1, "core.refinery", _utc(2026, 9, 15, 17, 30), 1_000_000, 0)
        # Tổ chức khác: model + lượt gọi không được lọt vào số của tổ chức này.
        other = uuid.uuid4()
        (mx,) = await _provider(db, other, "openai_compat", "Khác", ["x"])
        await _price(db, other, mx, 10_000, 10_000)
        await _call(db, other, mx, "core.refinery", _utc(2026, 9, 15, 3), 1_000_000, 1_000_000)
        await db.commit()
    return {"org": org, "m1": m1, "m2": m2, "m3": m3, "m4": m4, "mx": mx}


def _plain(value: Any) -> None:
    """JSON không chứa Decimal/object lạ: chỉ dict/list/str/int/float/bool/None."""
    if isinstance(value, dict):
        for k, v in value.items():
            assert isinstance(k, str)
            _plain(v)
    elif isinstance(value, list):
        for v in value:
            _plain(v)
    else:
        assert value is None or isinstance(value, str | int | float | bool), type(value)
        assert not isinstance(value, Decimal)


async def test_day_cost_matches_hand_computed(owner_api: Api) -> None:
    ids = await _seed()
    r = await owner_api.get(f"/system/ai-cost?date={D.isoformat()}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["date"] == "2026-09-15" and body["timezone"] == "Asia/Ho_Chi_Minh"
    assert body["total_vnd"] == 9_000
    assert body["unpriced_calls"] == 1
    assert body["budget_vnd"] is None and body["over_budget"] is False
    agents = {a["agent_key"]: a for a in body["agents"]}
    assert set(agents) == {"core.refinery", "core.gen"}
    assert agents["core.refinery"] == {"agent_key": "core.refinery", "label": "Sàng lọc & suy luận chính",
                                       "calls": 2, "tokens_in": 101_000, "tokens_out": 51_000, "cost_vnd": 3_000,
                                       "unpriced_calls": 1}
    assert agents["core.gen"] == {"agent_key": "core.gen", "label": "Gen — trợ lý quản trị", "calls": 2,
                                  "tokens_in": 700_000, "tokens_out": 525_000, "cost_vnd": 6_000,
                                  "unpriced_calls": 0}
    assert body["agents"][0]["agent_key"] == "core.gen"  # tốn nhiều nhất trước

    models = {m["model_id"]: m for m in body["models"]}
    assert str(ids["mx"]) not in models  # model tổ chức khác không hiện
    assert models[str(ids["m1"])]["price_source"] == "owner"
    assert models[str(ids["m1"])]["in_vnd_per_mtok"] == 10_000 and models[str(ids["m1"])]["out_vnd_per_mtok"] == 40_000
    assert models[str(ids["m1"])]["calls_today"] == 1  # lượt 'error' và lượt sang ngày D+1 không tính
    assert models[str(ids["m3"])]["price_source"] == "none" and models[str(ids["m3"])]["in_vnd_per_mtok"] is None
    assert models[str(ids["m4"])]["price_source"] == "subscription"
    assert models[str(ids["m4"])]["provider_kind"] == "claude_code_cli"
    assert models[str(ids["m4"])]["provider_name"] == "Claude Code" and models[str(ids["m4"])]["model_name"] == "sonnet"

    days = body["last_7_days"]
    assert [d["date"] for d in days] == [f"2026-09-{n:02d}" for n in range(9, 16)]
    assert days[-1]["total_vnd"] == 9_000 and days[-2]["total_vnd"] == 0  # 23:30 UTC hôm trước thuộc ngày D
    assert body["feedback_7d"] == {"helpful": 0, "not_helpful": 0, "briefing_helpful": 0, "briefing_not_helpful": 0}
    _plain(body)

    nxt = (await owner_api.get("/system/ai-cost?date=2026-09-16")).json()
    assert nxt["total_vnd"] == 10_000 and nxt["last_7_days"][-2] == {"date": "2026-09-15", "total_vnd": 9_000}
    assert (await owner_api.get("/system/ai-cost?date=15-09-2026")).status_code == 422


async def test_summary_has_no_decimal(owner_api: Api) -> None:
    ids = await _seed()
    async with admin_sessionmaker()() as db:
        await db.execute(text("UPDATE agent.model_prices SET in_vnd_per_mtok = 12345.67 WHERE model_id = :m"),
                         {"m": ids["m1"]})
        await db.commit()
    async with sessionmaker()() as db:
        s = await ai_cost.summary(db, ids["org"], D)
    _plain(s)
    assert isinstance(s["total_vnd"], int)
    # 100.000 × 12.345,67 / 1e6 = 1.234,567 + 2.000 = 3.234,567 ⇒ làm tròn 3.235; tổng 9.234,567 ⇒ 9.235.
    assert s["total_vnd"] == 9_235
    assert next(a for a in s["agents"] if a["agent_key"] == "core.refinery")["cost_vnd"] == 3_235
    assert ai_cost.fmt_vnd(12_500) == "12.500 ₫" and ai_cost.fmt_vnd(0) == "0 ₫"


async def test_put_prices(owner_api: Api, client: Any, db: Any) -> None:
    ids = await _seed()
    url = f"/system/ai-cost/prices/{ids['m1']}"
    r = await owner_api.send("PUT", url, {"in_vnd_per_mtok": 20_000, "out_vnd_per_mtok": 40_000})
    assert r.status_code == 200, r.text
    # Giá áp lại cho lịch sử: refinery m1 = 2.000 + 2.000 = 4.000 ⇒ tổng ngày D = 10.000.
    body = (await owner_api.get(f"/system/ai-cost?date={D.isoformat()}")).json()
    assert body["total_vnd"] == 10_000
    # Đặt giá cho m3 ⇒ hết lượt chưa có giá: 1.000 × 1.000.000 / 1e6 × 2 = 2.000.
    r = await owner_api.send("PUT", f"/system/ai-cost/prices/{ids['m3']}",
                             {"in_vnd_per_mtok": 1_000_000, "out_vnd_per_mtok": 1_000_000})
    assert r.status_code == 200
    body = (await owner_api.get(f"/system/ai-cost?date={D.isoformat()}")).json()
    assert body["total_vnd"] == 12_000 and body["unpriced_calls"] == 0
    # Cả hai null ⇒ xoá giá ⇒ về "chưa có giá".
    r = await owner_api.send("PUT", f"/system/ai-cost/prices/{ids['m3']}",
                             {"in_vnd_per_mtok": None, "out_vnd_per_mtok": None})
    assert r.status_code == 200
    m3 = next(m for m in r.json()["models"] if m["model_id"] == str(ids["m3"]))
    assert m3["price_source"] == "none" and m3["in_vnd_per_mtok"] is None
    async with admin_sessionmaker()() as s:
        assert (await s.execute(text("SELECT count(*) FROM agent.model_prices WHERE model_id = :m"),
                                {"m": ids["m3"]})).scalar_one() == 0
        logged = (await s.execute(text("SELECT count(*) FROM ops.action_log WHERE action = 'ai.price_changed'"))
                  ).scalar_one()
    assert logged == 3

    r = await owner_api.send("PUT", f"/system/ai-cost/prices/{ids['mx']}", {"in_vnd_per_mtok": 1})
    assert r.status_code == 404 and r.json()["code"] == "NOT_FOUND"
    r = await owner_api.send("PUT", f"/system/ai-cost/prices/{uuid.uuid4()}", {"in_vnd_per_mtok": 1})
    assert r.status_code == 404
    r = await owner_api.send("PUT", url, {"in_vnd_per_mtok": -1, "out_vnd_per_mtok": 1})
    assert r.status_code == 422
    r = await owner_api.send("PUT", url, {"in_vnd_per_mtok": 1, "out_vnd_per_mtok": 2e9})
    assert r.status_code == 422

    mgr = await login_as(client, db, "manager")
    assert (await mgr.send("PUT", url, {"in_vnd_per_mtok": 1})).status_code == 403
    assert (await mgr.send("PUT", "/system/ai-cost/budget", {"daily_budget_vnd": 1})).status_code == 403


async def test_put_budget(owner_api: Api) -> None:
    await _seed()
    r = await owner_api.send("PUT", "/system/ai-cost/budget", {"daily_budget_vnd": 5_000})
    assert r.status_code == 200, r.text
    assert r.json()["budget_vnd"] == 5_000
    body = (await owner_api.get(f"/system/ai-cost?date={D.isoformat()}")).json()
    assert body["budget_vnd"] == 5_000 and body["over_budget"] is True  # 9.000 > 5.000
    assert (await owner_api.send("PUT", "/system/ai-cost/budget", {"daily_budget_vnd": -1})).status_code == 422
    assert (await owner_api.send("PUT", "/system/ai-cost/budget",
                                 {"daily_budget_vnd": 10_000_000_001})).status_code == 422
    r = await owner_api.send("PUT", "/system/ai-cost/budget", {"daily_budget_vnd": None})
    assert r.status_code == 200 and r.json()["budget_vnd"] is None and r.json()["over_budget"] is False
    async with admin_sessionmaker()() as s:
        n = (await s.execute(text("SELECT count(*) FROM ops.action_log WHERE action = 'ai.budget_changed'"))
             ).scalar_one()
    assert n == 2


async def _evaluate(redis: Any, org: Any, now: datetime) -> None:
    async with sessionmaker()() as s:
        await health.evaluate(s, redis, org, now=now, started_at=now)
        await s.commit()


async def _budget_bells() -> list[Any]:
    async with admin_sessionmaker()() as s:
        return list((await s.execute(text("""SELECT title, body, link FROM core.notifications
                                             WHERE kind = 'ai.budget_exceeded' ORDER BY created_at"""))).all())


async def _alert() -> Any:
    async with admin_sessionmaker()() as s:
        return (await s.execute(text("""SELECT kind, fingerprint, cleared_at, link FROM ops.health_alerts
                                        WHERE key = 'ai.budget'"""))).one_or_none()


async def test_budget_bell_once_per_day(owner_api: Api, redis: Any) -> None:
    org = await _org()
    async with admin_sessionmaker()() as db:
        (m1,) = await _provider(db, org, "openai_compat", "OpenRouter", ["m1"])
        await _price(db, org, m1, 10_000, 0)
        await _call(db, org, m1, "core.gen", _utc(2026, 9, 15, 3), 150_000, 0)  # 1.500 ₫ ngày 15/09
        await db.commit()
    r = await owner_api.send("PUT", "/system/ai-cost/budget", {"daily_budget_vnd": 1_000})
    assert r.status_code == 200
    now = _utc(2026, 9, 15, 10)  # 17:00 VN 15/09
    await _evaluate(redis, org, now)
    bells = await _budget_bells()
    assert len(bells) == 1
    assert bells[0].title == "Chi phí AI hôm nay vượt trần 1.000 ₫"
    assert bells[0].body.startswith("Đã dùng 1.500 ₫.")
    assert bells[0].link == "/overview?focus=ai-cost"
    alert = await _alert()
    assert alert.kind == "ai.budget_exceeded" and alert.fingerprint == "2026-09-15" and alert.cleared_at is None
    issues = (await owner_api.get("/system/health")).json()["issues"]
    assert next(i for i in issues if i["kind"] == "ai.budget_exceeded")["action"] == "Xem chi phí AI"

    await _evaluate(redis, org, _utc(2026, 9, 15, 11))
    assert len(await _budget_bells()) == 1  # cùng ngày ⇒ không chuông thứ hai

    async with admin_sessionmaker()() as db:
        await _call(db, org, m1, "core.gen", _utc(2026, 9, 16, 2), 150_000, 0)  # 1.500 ₫ ngày 16/09
        await db.commit()
    await _evaluate(redis, org, _utc(2026, 9, 16, 3))
    assert len(await _budget_bells()) == 2  # ngày mới vẫn vượt ⇒ chuông mới
    assert (await _alert()).fingerprint == "2026-09-16"

    r = await owner_api.send("PUT", "/system/ai-cost/budget", {"daily_budget_vnd": 2_000})
    assert r.status_code == 200
    await _evaluate(redis, org, _utc(2026, 9, 16, 4))
    assert (await _alert()).cleared_at is not None  # dưới trần ⇒ sự cố đóng
    assert len(await _budget_bells()) == 2

    # Hạ trần dưới mức đã dùng ⇒ sự cố mở lại (chuông mới); bỏ trần ⇒ đóng.
    await owner_api.send("PUT", "/system/ai-cost/budget", {"daily_budget_vnd": 500})
    await _evaluate(redis, org, _utc(2026, 9, 16, 5))
    assert (await _alert()).cleared_at is None and len(await _budget_bells()) == 3
    await owner_api.send("PUT", "/system/ai-cost/budget", {"daily_budget_vnd": None})
    await _evaluate(redis, org, _utc(2026, 9, 16, 6))
    assert (await _alert()).cleared_at is not None


async def _budget_bell_flags() -> list[tuple[bool, bool]]:
    async with admin_sessionmaker()() as s:
        return [(r.read, r.resolved) for r in (await s.execute(text("""
            SELECT read_at IS NOT NULL AS read, resolved_at IS NOT NULL AS resolved FROM core.notifications
            WHERE kind = 'ai.budget_exceeded' ORDER BY created_at"""))).all()]


async def test_budget_bell_new_day_reads_without_resolved_label(owner_api: Api, redis: Any) -> None:
    """Review P3: sang ngày mới tổng chi về 0 ⇒ sự cố tự hết, KHÔNG phải Sếp xử lý ⇒ chuông được đọc nhưng không gắn
    "Đã xử lý". Sếp nâng/bỏ trần ngay trong ngày thì mới là "Đã xử lý"."""
    org = await _org()
    async with admin_sessionmaker()() as db:
        (m1,) = await _provider(db, org, "openai_compat", "OpenRouter", ["m1"])
        await _price(db, org, m1, 10_000, 0)
        await _call(db, org, m1, "core.gen", _utc(2026, 9, 15, 3), 150_000, 0)  # 1.500 ₫ ngày 15/09
        await db.commit()
    await owner_api.send("PUT", "/system/ai-cost/budget", {"daily_budget_vnd": 1_000})
    await _evaluate(redis, org, _utc(2026, 9, 15, 10))  # 17:00 VN 15/09: vượt trần
    assert await _budget_bell_flags() == [(False, False)]
    await _evaluate(redis, org, _utc(2026, 9, 16, 3))  # 10:00 VN 16/09: ngày mới, chưa tiêu gì
    assert (await _alert()).cleared_at is not None
    assert await _budget_bell_flags() == [(True, False)]


async def test_budget_bell_raised_budget_same_day_is_resolved(owner_api: Api, redis: Any) -> None:
    org = await _org()
    async with admin_sessionmaker()() as db:
        (m1,) = await _provider(db, org, "openai_compat", "OpenRouter", ["m1"])
        await _price(db, org, m1, 10_000, 0)
        await _call(db, org, m1, "core.gen", _utc(2026, 9, 15, 3), 150_000, 0)
        await db.commit()
    await owner_api.send("PUT", "/system/ai-cost/budget", {"daily_budget_vnd": 1_000})
    await _evaluate(redis, org, _utc(2026, 9, 15, 10))
    await owner_api.send("PUT", "/system/ai-cost/budget", {"daily_budget_vnd": 5_000})
    await _evaluate(redis, org, _utc(2026, 9, 15, 11))
    assert await _budget_bell_flags() == [(True, True)]


def test_actions_labels() -> None:
    assert health.ACTIONS["ai.budget_exceeded"] == "Xem chi phí AI"
    assert health.ACTIONS["ai.background_no_source"] == "Mở Bộ não AI"


def test_price_source_and_day_bounds() -> None:
    assert ai_cost.price_source("antigravity_cli", True) == "subscription"
    assert ai_cost.price_source("claude_code_cli", False) == "subscription"
    assert ai_cost.price_source("openai_compat", True) == "owner"
    assert ai_cost.price_source("gemini", False) == "none"
    start, end = ai_cost.day_bounds(D)
    assert start.astimezone(UTC) == _utc(2026, 9, 14, 17) and end.astimezone(UTC) == _utc(2026, 9, 15, 17)
    assert ai_cost.today_vn(_utc(2026, 9, 14, 17, 30)) == D
