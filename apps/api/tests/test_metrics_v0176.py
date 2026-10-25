# ruff: noqa: E501
"""v0.1.76 (gói T9) — chỉ số Tình báo (`pipes/metrics.py`): Wilson, báo nhầm/ngày, recall có trọng số (+ bootstrap, n hiệu dụng), chi phí +
% giãn nhịp theo luồng, hành động; báo cáo tuần qua Zalo (chỉ số tổng hợp, qua reports, khoá trợ lý chặn được); riêng tư + chỉ-đọc."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gh.biz.intel import levels
from gh.biz.intel.pipes import metrics
from gh.db import sessionmaker
from tests.conftest import Api, verify_pin
from tests.mem_helpers import mk_org
from tests.owner_helpers import bot_world
from tests.phase2 import org_id
from tests.test_intel_delivery_reports_v0176 import _lock
from tests.test_intel_digest_rawtail_v0176 import _sig
from tests.test_rbac_api import login_as

SM = sessionmaker
SECRET = "BÍ-MẬT nội dung riêng tư"


async def _bot(db: AsyncSession, org: uuid.UUID) -> None:
    await db.execute(text("INSERT INTO core.channels (org_id, type, name) VALUES (:o, 'zalo', :n)"), {"o": org, "n": uuid.uuid4().hex[:6]})
    await db.commit()
    await bot_world(db, org)


async def _fb(db: AsyncSession, org: uuid.UUID, sid: uuid.UUID, verdict: str, gate: str | None, *, note: str | None = None,
              ago_h: float = 1.0) -> None:
    await db.execute(text("""INSERT INTO biz.signal_feedback (org_id, signal_id, verdict, verdict_by, gate, note, at)
                             VALUES (:o, :s, :v, 'owner', :g, :n, :t)"""),
                     {"o": org, "s": sid, "v": verdict, "g": gate, "n": note, "t": datetime.now(UTC) - timedelta(hours=ago_h)})
    await db.commit()


async def _alert(db: AsyncSession, org: uuid.UUID, ck: str, *, level: str = "red", gate: str | None = None, **upd: Any) -> uuid.UUID:
    s = await _sig(db, org, "xung_dot_khieu_nai", ck, customer_of_owner=True, flags={"public": True})
    sets = {"level": level, "gate": gate, "title": SECRET, "summary": SECRET, **upd}
    await db.execute(text("UPDATE biz.alerts SET " + ", ".join(f"{k} = :{k}" for k in sets) + " WHERE id = :i"), {**sets, "i": s.id})  # noqa: S608
    await db.commit()
    return s.id


def test_wilson_interval_known_values() -> None:
    w = metrics.wilson(8, 10)
    assert round(w["p"], 3) == 0.8 and 0.49 < w["lo95"] < 0.50 and 0.94 < w["hi95"] < 0.95 and w["n"] == 10
    z = metrics.wilson(0, 0)
    assert z["p"] is None and z["lo95"] is None and z["n"] == 0
    assert metrics.wilson(15, 15)["hi95"] == 1.0 or metrics.wilson(15, 15)["hi95"] > 0.99


def test_kish_effective_n_and_bootstrap_deterministic() -> None:
    assert metrics.n_eff([1.0, 1.0, 1.0, 1.0]) == 4.0
    assert metrics.n_eff([10.0, 1.0, 1.0]) < 3.0                      # trọng số lệch ⇒ n hiệu dụng nhỏ hơn n thật
    assert metrics.n_eff([]) == 0.0
    days = [(5, 1.0), (4, 0.0), (6, 2.0), (5, 0.0)]
    a = metrics.recall_ci(days)
    assert a == metrics.recall_ci(days)                               # hạt giống cố định
    assert a is not None and a["lo95"] <= a["est"] <= a["hi95"] and round(a["est"], 3) == round(20 / 23, 3)
    assert metrics.recall_ci([(0, 0.0)]) is None


async def test_metrics_precision_by_gate_with_ci_and_latest_verdict(db: AsyncSession) -> None:
    org = await mk_org(db)
    g3 = levels.G3
    for i in range(12):
        sid = await _alert(db, org, f"g3-{i}", gate=g3)
        await _fb(db, org, sid, "correct" if i < 10 else "wrong", g3)
    flip = await _alert(db, org, "flip", gate=g3)
    await _fb(db, org, flip, "wrong", g3, ago_h=5)
    await _fb(db, org, flip, "correct", g3, ago_h=1)                      # bản mới nhất thắng
    for i in range(4):
        sid = await _alert(db, org, f"amb-{i}", level="amber", gate=None)
        await _fb(db, org, sid, "correct" if i < 3 else "wrong", None)
    async with SM()() as s:
        m = await metrics.compute(s, org, days=14)
    row = next(r for r in m["precision_ci"] if r["gate"] == g3)
    assert (row["k"], row["n"]) == (11, 13) and row["lo95"] < row["p"] < row["hi95"] and row["m1_ok"] is False   # 11/13=84% nhưng lo95 < 60% ⇒ chưa đủ n≥15
    assert next(r for r in m["precision_ci"] if r["gate"] == levels.G1)["n"] == 0
    assert m["amber"]["n"] == 4 and m["amber"]["k"] == 3


async def test_metrics_false_alarms_per_day_and_cap(db: AsyncSession) -> None:
    org = await mk_org(db)
    for i in range(3):
        sid = await _alert(db, org, f"w{i}", gate=levels.G3)
        await _fb(db, org, sid, "wrong", levels.G3, ago_h=2 + i)
    ok = await _alert(db, org, "ok", gate=levels.G3)
    await _fb(db, org, ok, "correct", levels.G3)
    await _alert(db, org, "cap", gate=levels.G4, capped_at=datetime.now(UTC) - timedelta(hours=3))
    async with SM()() as s:
        m = await metrics.compute(s, org, days=14)
    fa = m["false_alarms"]
    assert fa["total"] == 3 and abs(fa["per_day_avg"] - 3 / 14) < 1e-9 and fa["capped_per_day"] > 0
    assert sum(d["n"] for d in fa["by_day"]) == 3


async def test_metrics_recall_weighted_by_tier_probability(db: AsyncSession) -> None:
    org = await mk_org(db)
    for i in range(8):
        await _alert(db, org, f"d{i}", level="red" if i % 2 else "amber", gate=None)
    # hai «sót» ở tầng t3 chọn với p=0,25 (mỗi cái ≙ 4 sót thật) + một «em sót» tự báo (p=1) + một sót lừa đảo
    miss = []
    for note in ("mẫu tầng t3 p=0.25", "mẫu tầng t3 p=0.25", None):
        miss.append(await _alert(db, org, f"m{len(miss)}", level="grey", gate=None, status="dismissed"))
        await _fb(db, org, miss[-1], "missed", None, note=note)
    crit = await _alert(db, org, "crit", level="grey", gate=levels.G1, status="dismissed")
    await _fb(db, org, crit, "missed", levels.G1, note="[rui_ro_lua_dao] em sót")
    pack_refs = [{"n": i, "tier": "t3", "p": 0.25, "signal_id": None, "raw_event_id": None} for i in range(1, 5)]
    await db.execute(text("""INSERT INTO ops.owner_outbox (org_id, channel_type, kind, severity, dedupe_key, text, refs, created_at, not_before, next_attempt_at)
                             VALUES (:o, 'zalo', 'ask', 'important', 'shadow-pack:x', 'gói', CAST(:r AS jsonb), now(), now(), now())"""),
                     {"o": org, "r": json.dumps(pack_refs)})
    await db.commit()
    async with SM()() as s:
        m = await metrics.compute(s, org, days=14)
    r = m["recall"]
    assert r["detected_n"] == 8 and r["missed_n"] == 4 and abs(r["missed_est"] - (4 + 4 + 1 + 1)) < 1e-9
    assert abs(r["est"] - 8 / (8 + 10)) < 1e-9 and r["lo95"] <= r["est"] <= r["hi95"]
    assert 0 < r["n_eff"] <= 4 and r["critical_missed_n"] == 1


async def test_metrics_cost_by_thread_with_stretch_percent(db: AsyncSession) -> None:
    org = await mk_org(db)
    prov = (await db.execute(text("INSERT INTO agent.providers (org_id, kind, name) VALUES (:o, 'openai', 'p') RETURNING id"), {"o": org})).scalar_one()
    mid = (await db.execute(text("INSERT INTO agent.models (provider_id, model_name) VALUES (:p, 'm') RETURNING id"), {"p": prov})).scalar_one()
    tk = "g:11111111-1111-1111-1111-111111111111"
    for cost, ti, to in ((1500, 100, 50), (500, 10, 5)):
        await db.execute(text("""INSERT INTO agent.model_calls (org_id, model_id, agent_key, purpose, tokens_in, tokens_out, status, thread_key, cost_vnd)
                                 VALUES (:o, :m, 'core.intel', 'refinery', :ti, :to, 'ok', :t, :c)"""), {"o": org, "m": mid, "ti": ti, "to": to, "t": tk, "c": cost})
    await db.execute(text("""INSERT INTO refinery.group_watermarks (org_id, thread_key, stretched_minutes, capped_minutes)
                             VALUES (:o, 'g:11111111-1111-1111-1111-111111111111', 1440, 144)"""), {"o": org})
    await db.commit()
    async with SM()() as s:
        m = await metrics.compute(s, org, days=7)
    c = m["cost"]
    t = next(x for x in c["by_thread"] if x["thread_key"].startswith("g:1111"))
    assert t["cost_vnd"] == 2000 and t["calls"] == 2 and t["tokens_in"] == 110 and t["tokens_out"] == 55
    assert abs(t["stretched_pct"] - 1440 / (7 * 1440) * 100) < 0.01 and abs(t["capped_pct"] - 144 / (7 * 1440) * 100) < 0.01
    assert c["total_vnd"] == 2000


async def test_metrics_output_has_ids_and_numbers_only_never_content(db: AsyncSession) -> None:
    org = await mk_org(db)
    sid = await _alert(db, org, "p1", gate=levels.G3, privacy="console_pin")
    await _fb(db, org, sid, "wrong", levels.G3, note=SECRET)
    miss = await _alert(db, org, "p2", level="grey", status="dismissed")
    await _fb(db, org, miss, "missed", None, note=f"mẫu tầng t2 p=0.5 {SECRET}")
    async with SM()() as s:
        m = await metrics.compute(s, org, days=14)
        txt = metrics.weekly_text(m)
    assert SECRET not in json.dumps(m, default=str) and "BÍ-MẬT" not in txt and "console_pin" not in txt


async def test_metrics_compute_is_read_only_and_never_touches_gates(db: AsyncSession) -> None:
    """Ngưỡng/giảm báo KHÔNG tự chỉnh từ metrics: nhiều «không cần báo» ở cổng lừa đảo/an toàn không đổi mức, mute hay chính sách."""
    org = await mk_org(db)
    for g in (levels.G1, levels.G2):
        for i in range(12):
            sid = await _alert(db, org, f"{g}-{i}", gate=g)
            await _fb(db, org, sid, "unimportant", g)
            await _fb(db, org, sid, "wrong", g)
    snap = "SELECT (SELECT count(*) FROM biz.signal_mutes), (SELECT string_agg(id::text || level || delivery || status, ',' ORDER BY id) FROM biz.alerts), (SELECT count(*) FROM biz.signal_feedback), (SELECT count(*) FROM ops.owner_outbox), (SELECT count(*) FROM ops.action_log)"
    before = tuple((await db.execute(text(snap))).one())
    async with SM()() as s:
        await metrics.compute(s, org, days=14)
        await s.commit()
    assert tuple((await db.execute(text(snap))).one()) == before


async def test_metrics_org_isolation(db: AsyncSession) -> None:
    a, b = await mk_org(db), await mk_org(db, "B")
    sid = await _alert(db, b, "b1", gate=levels.G3)
    await _fb(db, b, sid, "wrong", levels.G3)
    async with SM()() as s:
        m = await metrics.compute(s, a, days=14)
    assert m["false_alarms"]["total"] == 0 and all(r["n"] == 0 for r in m["precision_ci"])


async def test_metrics_full_route_owner_only(owner_api: Api, client: Any, db: AsyncSession) -> None:
    org = await org_id(db)
    sid = await _alert(db, org, "r1", gate=levels.G3)
    await _fb(db, org, sid, "correct", levels.G3)
    r = await owner_api.get("/intel/metrics/full?days=14")
    assert r.status_code == 200 and {"precision_ci", "false_alarms", "recall", "cost", "acted"} <= set(r.json())
    assert (await owner_api.get("/intel/metrics/full?days=3")).status_code == 422
    mgr = await login_as(client, db, "manager")
    await verify_pin(mgr, "112233")
    assert (await mgr.get("/intel/metrics/full")).status_code == 403


# ─── báo cáo tuần ────────────────────────────────────────────────────────────

SUNDAY = datetime(2026, 10, 25, 18, 30, tzinfo=levels.VN)


async def test_weekly_report_aggregates_only_via_reports_and_dedupes(db: AsyncSession) -> None:
    org = await mk_org(db)
    await _bot(db, org)
    sid = await _alert(db, org, "w1", gate=levels.G3)
    await _fb(db, org, sid, "wrong", levels.G3)
    async with SM()() as s:
        assert await metrics.deliver_weekly(s, org, SUNDAY) == "sent"
        await s.commit()
        assert await metrics.deliver_weekly(s, org, SUNDAY) == "already_sent"
    row = (await db.execute(text("SELECT kind, text, severity, dedupe_key FROM ops.owner_outbox WHERE dedupe_key LIKE 'intel-weekly:%'"))).one()
    assert row.kind == "reply" and "Tình báo tuần" in row.text and SECRET not in row.text
    assert len(row.text) <= 1500


async def test_weekly_report_blocked_by_assistant_lock_and_no_channel(db: AsyncSession) -> None:
    org = await mk_org(db)
    async with SM()() as s:
        assert await metrics.deliver_weekly(s, org, SUNDAY) == "no_channel"
    await _bot(db, org)
    await _lock(db, org)
    async with SM()() as s:
        assert await metrics.deliver_weekly(s, org, SUNDAY) == "locked"
        await s.commit()
    assert (await db.execute(text("SELECT count(*) FROM ops.owner_outbox"))).scalar_one() == 0


async def test_weekly_job_runs_only_sunday_evening_vn(db: AsyncSession, monkeypatch: Any) -> None:
    org = await mk_org(db)
    await _bot(db, org)
    from gh.refinery import clock

    monkeypatch.setattr(clock, "now", lambda: SUNDAY - timedelta(days=1))             # thứ Bảy
    assert (await metrics.intel_weekly({}))["sent"] == 0
    monkeypatch.setattr(clock, "now", lambda: SUNDAY)
    assert (await metrics.intel_weekly({}))["sent"] >= 1
    assert any(j[0] is metrics.intel_weekly for j in metrics.JOBS)
