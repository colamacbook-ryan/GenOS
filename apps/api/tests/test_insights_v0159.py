"""v0.1.59 (G1) — Mặt tiền Owner v2: `GET /owner/insights?range=week|month&only=` (9 chỉ số tính thẳng từ dữ liệu
đang có).

- Tổ chức rỗng ⇒ `empty` + chuỗi đủ ngày; seed 20 ngày ⇒ `collecting` (kỳ 30 ngày); đủ 2 kỳ ⇒ `ok`.
- `delta_pct` đúng; kỳ trước 0 ⇒ null. Tin 23:30 UTC rơi vào ngày VN hôm sau.
- `only=messages_in` ⇒ 1 chỉ số, breakdown null. `only` lạ ⇒ 422.
- 200 tin nhóm không trả lời + 10 tin 1-1 ⇒ `unanswered` / `reply_fast_pct` chỉ tính 10 tin 1-1 (loại cả tin của
  nhân viên).
- `new_contacts` đếm người `unknown`, không đếm `staff`. Một chỉ số lỗi (monkeypatch / SQL hỏng) ⇒ chỉ nó `state:
  'error'`.
- `ai_cost`: chỉ model có giá; lượt chưa đặt giá đếm riêng (`unpriced_calls`).

Chạy cả quyền superuser và `GH_TEST_APP_ROLE=1` (gh_app): `pytest -q tests/test_insights_v0159.py`.
"""

from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import text

from gh import ai_cost
from gh.owner import insights
from gh.refinery import triage
from tests.conftest import Api
from tests.test_owner_v0155 import _org, _person
from tests.test_owner_v0159 import channel, event, identity, unit


@pytest.fixture
async def owner(owner_api: Api) -> Api:
    return owner_api


def today() -> date:
    return ai_cost.today_vn()


def vn_noon(d: date, hour: int = 12, minute: int = 0) -> datetime:
    """Giờ VN `hour:minute` của ngày VN `d` (UTC+7, không có giờ mùa hè)."""
    return datetime(d.year, d.month, d.day, hour, minute, tzinfo=ai_cost.VN_TZ).astimezone(UTC)


async def _get(owner: Api, **q: Any) -> dict[str, Any]:
    qs = "&".join(f"{k}={v}" for k, v in q.items())
    r = await owner.get("/owner/insights" + (f"?{qs}" if qs else ""))
    assert r.status_code == 200, r.text
    return r.json()  # type: ignore[no-any-return]


def by_key(d: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {m["key"]: m for m in d["metrics"]}


async def _messages(db: Any, org: Any, per_day: dict[int, int], *, direction: str = "inbound") -> None:
    """`per_day[k]` tin đến vào ngày VN (hôm nay − k)."""
    ch = await channel(db, org)
    for back, n in per_day.items():
        for i in range(n):
            await event(db, org, ch, vn_noon(today() - timedelta(days=back), 9, i % 50), direction=direction)


# ─── trạng thái + hình dạng ───────────────────────────────────────────────────────────────────────────────────────

async def test_empty_org_has_full_zero_series(owner: Api) -> None:
    d = await _get(owner)
    assert d["state"] == "empty" and d["days_until_compare"] is None and d["range"] == "week"
    assert d["from"] == (today() - timedelta(days=6)).isoformat() and d["to"] == today().isoformat()
    assert d["prev_to"] == (today() - timedelta(days=7)).isoformat()
    assert d["prev_from"] == (today() - timedelta(days=13)).isoformat()
    assert d["generated_at"].endswith("Z") and d["channels_connected"] == 0
    assert [m["key"] for m in d["metrics"]] == list(insights.METRIC_KEYS) and len(d["metrics"]) == 9
    assert set(d) == {"range", "from", "to", "prev_from", "prev_to", "generated_at", "state", "days_until_compare",
                      "channels_connected", "metrics", "breakdown", "ai_cost", "feedback"}
    for m in d["metrics"]:
        assert m["total"] == 0 and m["prev_total"] == 0 and m["delta_pct"] is None and "state" not in m
        assert m["good"] == ("down" if m["key"] in ("ai_cost_vnd", "unanswered") else "up")
        if m["key"] == "filtered":
            assert m["series"] == [] and m["prev_series"] == []
        else:
            assert len(m["series"]) == 7 and len(m["prev_series"]) == 7              # đủ ngày, tô 0
            assert all(p["v"] == 0 for p in m["series"] + m["prev_series"])
    days = [p["d"] for p in by_key(d)["messages_in"]["series"]]
    assert days == [(today() - timedelta(days=6 - i)).isoformat() for i in range(7)]
    assert d["breakdown"] == {"event_types": [], "funnel": []}
    assert d["ai_cost"] == {"calls": 0, "unpriced_calls": 0, "budget_vnd": None}
    assert d["feedback"] == {"helpful": 0, "not_helpful": 0}
    month = await _get(owner, range="month")
    assert len(by_key(month)["signals"]["series"]) == 30 and len(by_key(month)["signals"]["prev_series"]) == 30
    assert month["feedback"] is None                              # đánh giá chỉ ở chế độ 7 ngày
    assert units_label(by_key(d)) == {"messages_in": "count", "reply_fast_pct": "pct", "ai_cost_vnd": "vnd"}
    assert (await owner.get("/owner/insights?range=year")).status_code == 422


def units_label(m: dict[str, dict[str, Any]]) -> dict[str, str]:
    return {k: m[k]["unit"] for k in ("messages_in", "reply_fast_pct", "ai_cost_vnd")}


async def test_collecting_until_two_periods_then_ok(owner: Api, db: Any) -> None:
    org = await _org(db)
    await _messages(db, org, {k: 1 for k in range(20)})           # tin đầu tiên cách đây 19 ngày (20 ngày gồm hôm nay)
    await db.commit()
    month = await _get(owner, range="month")
    assert month["state"] == "collecting" and month["days_until_compare"] == 60 - 20
    assert by_key(month)["messages_in"]["total"] == 20 and by_key(month)["messages_in"]["prev_total"] == 0
    week = await _get(owner)
    assert week["state"] == "ok" and week["days_until_compare"] is None            # 20 ≥ 2 × 7
    assert by_key(week)["messages_in"]["total"] == 7 and by_key(week)["messages_in"]["prev_total"] == 7
    await _messages(db, org, {59: 1})                              # tin đầu tiên cách đây 59 ngày ⇒ đủ 2 kỳ 30 ngày
    await db.commit()
    assert (await _get(owner, range="month"))["state"] == "ok"


async def test_delta_pct_and_null_when_previous_is_zero(owner: Api, db: Any) -> None:
    org = await _org(db)
    await _messages(db, org, {**{k: 3 for k in range(7)}, **{k: 1 for k in range(7, 14)}})          # 21 vs 7
    cur = [await _person(db, org, f"P-{i}", f"Khách {i}", ptype="unknown") for i in range(3)]
    for i, pid in enumerate(cur):                                  # người mới: 3 trong kỳ này, 0 kỳ trước
        await db.execute(text("UPDATE core.persons SET created_at = :t WHERE id = :i"),
                         {"t": vn_noon(today() - timedelta(days=i)), "i": pid})
    await db.commit()
    m = by_key(await _get(owner))
    assert m["messages_in"]["total"] == 21 and m["messages_in"]["prev_total"] == 7
    assert m["messages_in"]["delta_pct"] == 200.0
    assert m["new_contacts"]["total"] == 3 and m["new_contacts"]["prev_total"] == 0
    assert m["new_contacts"]["delta_pct"] is None                  # kỳ trước 0 ⇒ null ("Mới")
    assert [p["v"] for p in m["messages_in"]["series"]] == [3] * 7
    assert [p["v"] for p in m["messages_in"]["prev_series"]] == [1] * 7
    assert insights.delta_pct(15, 10) == 50.0 and insights.delta_pct(5, 10) == -50.0
    assert insights.delta_pct(3, 0) is None


async def test_utc_2330_lands_on_the_next_vietnam_day(owner: Api, db: Any) -> None:
    org = await _org(db)
    ch = await channel(db, org)
    utc_day = today() - timedelta(days=4)
    # 06:30 hôm sau (VN)
    await event(db, org, ch, datetime(utc_day.year, utc_day.month, utc_day.day, 23, 30, tzinfo=UTC))
    await db.commit()
    series = {p["d"]: p["v"] for p in by_key(await _get(owner))["messages_in"]["series"]}
    assert series[(utc_day + timedelta(days=1)).isoformat()] == 1 and series[utc_day.isoformat()] == 0
    assert sum(series.values()) == 1


async def test_only_limits_metrics_and_drops_breakdown(owner: Api, db: Any) -> None:
    org = await _org(db)
    await _messages(db, org, {0: 2})
    await db.commit()
    d = await _get(owner, only="messages_in")
    assert [m["key"] for m in d["metrics"]] == ["messages_in"] and d["metrics"][0]["total"] == 2
    assert d["breakdown"] is None and d["feedback"] is None
    assert d["ai_cost"] == {"calls": 0, "unpriced_calls": 0, "budget_vnd": None}
    two = await _get(owner, only="unanswered, messages_in")
    assert [m["key"] for m in two["metrics"]] == ["messages_in", "unanswered"]            # theo thứ tự cố định
    r = await owner.get("/owner/insights?only=messages_in,khong_co")
    assert r.status_code == 422


# ─── trả lời nhanh / chưa trả lời: chỉ tin 1-1 từ người không phải nhân viên ─────────────────────────────────────

async def test_reply_metrics_count_only_the_ten_one_to_one_messages(owner: Api, db: Any) -> None:
    org = await _org(db)
    ch = await channel(db, org)
    lan = await _person(db, org, "PER-1", "Chị Lan")
    staff = await _person(db, org, "NV-1", "Nhân viên", ptype="staff")
    s_lan, s_staff = await identity(db, lan, ch), await identity(db, staff, ch)
    t0 = today()

    async def block(back: int, asks: list[int], reply_min: int | None) -> None:
        for m in asks:                                              # phút sau 09:00 giờ VN
            await event(db, org, ch, vn_noon(t0 - timedelta(days=back), 9, m), sender=s_lan)
        if reply_min is not None:
            await event(db, org, ch, vn_noon(t0 - timedelta(days=back), 9, reply_min), direction="outbound")

    await block(9, [0, 1, 2], 5)         # 3 tin trả lời trong 5 phút          ⇒ nhanh
    await block(8, [0, 1, 2], 10)        # 3 tin trả lời trong ≤ 10 phút        ⇒ nhanh
    await block(7, [0, 1], 59)           # 2 tin trả lời sau ~1 giờ            ⇒ chậm
    await block(6, [0, 30], None)        # 2 tin không ai trả lời              ⇒ chưa trả lời
    # tin của nhân viên gửi đến 1-1 (không phải khách) — không tính
    await event(db, org, ch, vn_noon(t0 - timedelta(days=6), 10, 0), sender=s_staff)
    # 200 tin nhóm Zalo không ai trả lời — không được làm "tin chờ trả lời" phình lên
    g = (await db.execute(text("""INSERT INTO core.groups (org_id, code, channel_id, external_id, name)
                                  VALUES (:o, 'GRP-1', :c, 'g1', 'Nhóm đông') RETURNING id"""),
                          {"o": org, "c": ch})).scalar_one()
    await db.execute(text("""
        INSERT INTO raw.events (org_id, received_at, occurred_at, channel_id, group_id, sender_identity_id,
                                external_msg_id, kind, body_text, payload, content_hash, direction)
        SELECT :o, CAST(:t AS timestamptz) + make_interval(secs => g),
               CAST(:t AS timestamptz) + make_interval(secs => g),
               :c, :g, :s, 'grp-' || g, 'text', 'x',
               CAST('{}' AS jsonb), decode('00', 'hex'), 'inbound'
        FROM generate_series(1, 200) g"""), {"o": org, "t": vn_noon(t0 - timedelta(days=6), 11), "c": ch, "g": g,
                                             "s": s_lan})
    await db.commit()
    d = await _get(owner, range="month")
    m = by_key(d)
    assert m["unanswered"]["total"] == 2                           # không phải 202
    assert m["reply_fast_pct"]["total"] == 75                      # 6 nhanh / 8 đã trả lời
    assert m["messages_in"]["total"] == 10 + 1 + 200               # tin nhận vẫn đếm đủ mọi tin đến
    fast = {p["d"]: p["v"] for p in m["reply_fast_pct"]["series"]}
    assert fast[(t0 - timedelta(days=9)).isoformat()] == 100 and fast[(t0 - timedelta(days=7)).isoformat()] == 0
    assert {p["d"]: p["v"] for p in m["unanswered"]["series"]}[(t0 - timedelta(days=6)).isoformat()] == 2
    assert m["unanswered"]["good"] == "down" and m["reply_fast_pct"]["unit"] == "pct"


async def test_reply_metrics_do_not_pair_a_message_with_a_reply_to_another_customer(owner: Api, db: Any) -> None:
    """F-60-R9 (sau /code-review): A nhắn, nhân viên nhắn B ⇒ A vẫn là CHƯA trả lời (trước đây tin đi kế tiếp trên cùng
    kênh bị ghép cho A dù gửi khách khác). Tin đi ghi người nhận (threadId) hoặc nối theo tin đến 1-1 gần nhất."""
    org = await _org(db)
    ch = await channel(db, org)
    a = await _person(db, org, "PER-A", "Chị A")
    b = await _person(db, org, "PER-B", "Anh B")
    s_a, s_b = await identity(db, a, ch, "zl-a"), await identity(db, b, ch, "zl-b")
    day = today() - timedelta(days=2)
    await event(db, org, ch, vn_noon(day, 9, 0), sender=s_a)                          # A hỏi lúc 09:00
    await event(db, org, ch, vn_noon(day, 9, 1), sender=s_b)                          # B hỏi lúc 09:01
    await event(db, org, ch, vn_noon(day, 9, 2), direction="outbound", payload='{"threadId": "zl-b"}')   # trả B
    await event(db, org, ch, vn_noon(day, 9, 3), sender=s_b)                          # B hỏi tiếp
    await event(db, org, ch, vn_noon(day, 9, 4), direction="outbound")                # trả B (không ghi người nhận)
    await db.commit()
    m = by_key(await _get(owner))
    assert m["unanswered"]["total"] == 1                       # chỉ A; trước đây A bị tính đã trả lời ⇒ 0
    assert m["reply_fast_pct"]["total"] == 100                 # 2 tin của B, đều trả lời trong 1 phút
    # A được trả lời đúng người (remoteJid của A) 20 phút sau ⇒ A đã có trả lời, nhưng chậm
    await event(db, org, ch, vn_noon(day, 9, 20), direction="outbound", payload='{"key": {"remoteJid": "zl-a@s.x"}}')
    await db.commit()
    m = by_key(await _get(owner))
    assert m["unanswered"]["total"] == 0
    assert m["reply_fast_pct"]["total"] == 67                  # 2 nhanh (B) / 3 đã trả lời


# ─── người mới / cơ hội / tín hiệu ────────────────────────────────────────────────────────────────────────────────

async def test_new_contacts_counts_unknown_but_not_staff_or_removed(owner: Api, db: Any) -> None:
    org = await _org(db)
    await _person(db, org, "P-1", "Người lạ từ Zalo", ptype="unknown")
    await _person(db, org, "P-2", "Khách", ptype="customer")
    await _person(db, org, "P-3", "Nhà cung cấp", ptype="supplier")
    await _person(db, org, "P-4", "Nhân viên", ptype="staff")
    gone = await _person(db, org, "P-5", "Đã xoá", ptype="unknown")
    merged = await _person(db, org, "P-6", "Đã gộp", ptype="unknown")
    await db.execute(text("UPDATE core.persons SET deleted_at = now() WHERE id = :i"), {"i": gone})
    await db.execute(text("UPDATE core.persons SET merged_into_id = :m WHERE id = :i"), {"m": gone, "i": merged})
    await db.commit()
    assert by_key(await _get(owner))["new_contacts"]["total"] == 3


async def test_opportunities_signals_and_breakdown(owner: Api, db: Any) -> None:
    org = await _org(db)
    t0 = today()
    types = ["AskedPrice"] * 4 + ["Complained"] * 3 + ["OfferedSupply"] * 2
    types += ["Greeting", "WentSilent", "SentQuotation"]
    for k, et in enumerate(types):
        await unit(db, org, vn_noon(t0 - timedelta(days=k % 5)), et)
    await unit(db, org, vn_noon(t0 - timedelta(days=9)), "AskedPrice")                 # kỳ trước: không vào breakdown
    rows = [("OPP-1", "negotiating", 1_000_000, 1, None), ("OPP-2", "raw_signal", None, 2, None),
            ("OPP-3", "won", 9_000_000, 3, 1), ("OPP-4", "won", 4_000_000, 10, 8), ("OPP-5", "lost", 7_000_000, 4, 2),
            ("OPP-6", "lost", 1_000, 40, 30), ("OPP-7", "negotiating", 500_000, 50, None)]
    for code, stage, val, first, closed in rows:
        await db.execute(text("""INSERT INTO biz.opportunities (org_id, code, need, stage, value_vnd, confidence,
                                                                first_signal_at, closed_at)
                                 VALUES (:o, :c, 'MDF', :s, :v, 'high', :f, :x)"""),
                         {"o": org, "c": code, "s": stage, "v": val, "f": vn_noon(t0 - timedelta(days=first)),
                          "x": vn_noon(t0 - timedelta(days=closed)) if closed else None})
    await db.commit()
    d = await _get(owner)
    m = by_key(d)
    assert m["signals"]["total"] == 12 and m["signals"]["prev_total"] == 1
    assert m["opps_new"]["total"] == 4 and m["opps_new"]["prev_total"] == 1                 # OPP-1..3,5 / OPP-4
    assert m["opps_won"]["total"] == 1 and m["opps_won"]["value_vnd"] == 9_000_000
    assert m["opps_won"]["prev_total"] == 1
    et = d["breakdown"]["event_types"]
    assert [(e["event_type"], e["units"]) for e in et] == [("AskedPrice", 4), ("Complained", 3), ("OfferedSupply", 2),
                                                           # top 5, cùng số thì theo tên
                                                           ("Greeting", 1), ("SentQuotation", 1)]
    assert et[0]["label"] == "Hỏi giá / nhu cầu mua" and et[1]["label"] == "Phàn nàn" and et[3]["label"] == "Tin khác"
    fn = {f["stage"]: f for f in d["breakdown"]["funnel"]}
    assert [f["stage"] for f in d["breakdown"]["funnel"]] == ["raw_signal", "negotiating", "won", "lost"]
    assert fn["negotiating"] == {"stage": "negotiating", "label": "Đang đàm phán", "n": 2, "value_vnd": 1_500_000}
    # OPP-4 thắng ở kỳ trước ⇒ ngoài phễu kỳ này
    assert fn["won"]["n"] == 1 and fn["won"]["value_vnd"] == 9_000_000
    assert fn["lost"]["n"] == 1                                                  # OPP-6 đóng 30 ngày trước ⇒ ngoài kỳ


# ─── lỗi cô lập ───────────────────────────────────────────────────────────────────────────────────────────────────

async def test_one_metric_failure_only_marks_that_metric(owner: Api, db: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    org = await _org(db)
    await _messages(db, org, {0: 4})
    await db.commit()

    async def boom(*a: Any, **k: Any) -> Any:
        raise RuntimeError("hỏng giả")

    monkeypatch.setitem(insights.COMPUTE, "signals", boom)
    d = await _get(owner)
    m = by_key(d)
    assert m["signals"]["state"] == "error" and m["signals"]["total"] == 0 and m["signals"]["series"] == []
    assert m["messages_in"]["total"] == 4 and "state" not in m["messages_in"]
    assert all("state" not in m[k] for k in m if k != "signals")
    assert len(d["metrics"]) == 9 and d["breakdown"] is not None


async def test_sql_error_rolls_back_to_savepoint_and_later_metrics_still_run(
        owner: Api, db: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    org = await _org(db)
    await _messages(db, org, {0: 2})
    await db.commit()

    async def bad_sql(db_: Any, org_id: Any, w: Any, ctx: Any) -> Any:
        await db_.execute(text("SELECT * FROM bang_khong_ton_tai"))

    monkeypatch.setitem(insights.COMPUTE, "messages_in", bad_sql)
    m = by_key(await _get(owner))
    assert m["messages_in"]["state"] == "error"
    assert m["opps_new"]["total"] == 0 and "state" not in m["opps_new"] and "state" not in m["unanswered"]


async def test_guarded_sets_the_timeout_then_resets_it_and_survives_errors(db: Any) -> None:
    async with insights.guarded(db):
        assert (await db.execute(text("SHOW statement_timeout"))).scalar_one() == "5s"
    # trả về mặc định sau mỗi chỉ số
    assert (await db.execute(text("SHOW statement_timeout"))).scalar_one() == "0"
    with pytest.raises(Exception, match="bang_khong_ton_tai"):
        async with insights.guarded(db):
            await db.execute(text("SELECT * FROM bang_khong_ton_tai"))
    assert (await db.execute(text("SELECT 1"))).scalar_one() == 1                        # giao dịch chính còn dùng được
    assert (await db.execute(text("SHOW statement_timeout"))).scalar_one() == "0"


async def test_a_metric_that_takes_too_long_is_cut_off(owner: Api, db: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(insights, "STATEMENT_TIMEOUT", "100ms")

    async def slow(db_: Any, org_id: Any, w: Any, ctx: Any) -> Any:
        await db_.execute(text("SELECT pg_sleep(2)"))

    monkeypatch.setitem(insights.COMPUTE, "signals", slow)
    m = by_key(await _get(owner))
    assert m["signals"]["state"] == "error" and "state" not in m["messages_in"] and "state" not in m["unanswered"]


# ─── chi phí trợ lý Gen / Gen lọc giúp ───────────────────────────────────────────────────────────────────────────

async def test_ai_cost_counts_only_priced_models_and_reports_unpriced_calls(owner: Api, db: Any) -> None:
    org = await _org(db)
    pid = (await db.execute(text("""INSERT INTO agent.providers (org_id, kind, name, auth_state)
                                    VALUES (:o, 'openai_compat', 'OpenRouter', 'ok') RETURNING id"""),
                            {"o": org})).scalar_one()
    priced = (await db.execute(text("INSERT INTO agent.models (provider_id, model_name) VALUES (:p, 'co-gia') "
                                    "RETURNING id"), {"p": pid})).scalar_one()
    unpriced = (await db.execute(text("INSERT INTO agent.models (provider_id, model_name) VALUES (:p, 'chua-gia') "
                                      "RETURNING id"), {"p": pid})).scalar_one()
    await db.execute(text("""INSERT INTO agent.model_prices (model_id, org_id, in_vnd_per_mtok, out_vnd_per_mtok)
                             VALUES (:m, :o, 10000, 40000)"""), {"m": priced, "o": org})
    t0 = today()
    for back, model, tin, tout in ((0, priced, 100_000, 50_000), (2, priced, 200_000, 0), (3, unpriced, 1_000, 1_000),
                                   (9, priced, 1_000_000, 0)):
        await db.execute(text("""INSERT INTO agent.model_calls (org_id, at, model_id, agent_key, purpose, tokens_in,
                                                                tokens_out, latency_ms, status)
                                 VALUES (:o, :at, :m, 'core.gen', 't', :ti, :to, 10, 'ok')"""),
                         {"o": org, "at": vn_noon(t0 - timedelta(days=back)), "m": model, "ti": tin, "to": tout})
    await ai_cost.set_budget(db, org, 50_000)
    await db.commit()
    d = await _get(owner)
    m = by_key(d)["ai_cost_vnd"]
    # kỳ này: 100k×10 + 50k×40 = 3.000 ₫ ; 200k×10 = 2.000 ₫ ; lượt chưa đặt giá KHÔNG tính tiền
    assert m["total"] == 5_000 and m["prev_total"] == 10_000 and m["delta_pct"] == -50.0 and m["good"] == "down"
    assert m["unit"] == "vnd" and len(m["series"]) == 7
    assert d["ai_cost"] == {"calls": 3, "unpriced_calls": 1, "budget_vnd": 50_000}


async def test_filtered_uses_value_summary_for_both_periods(owner: Api, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[int] = []

    async def fake(db_: Any, org_id: Any, days: int = 7) -> dict[str, Any]:
        seen.append(days)
        n = {7: 10, 14: 25, 30: 40, 60: 100}[days]
        return {"filtered": n, "spam_blocked": 1, "calls_saved": 0, "jev_on": False}

    monkeypatch.setattr(triage, "value_summary", fake, raising=False)
    f = by_key(await _get(owner))["filtered"]
    assert f["total"] == 10 and f["prev_total"] == 15 and f["delta_pct"] == -33.3 and f["series"] == []
    assert sorted(seen) == [7, 14]
    seen.clear()
    f = by_key(await _get(owner, range="month", only="filtered"))["filtered"]
    assert f["total"] == 40 and f["prev_total"] == 60 and sorted(seen) == [30, 60]


async def test_no_model_calls_and_no_writes(owner: Api, db: Any) -> None:
    org = await _org(db)
    await _messages(db, org, {0: 1})
    await db.commit()
    before = (await db.execute(text("SELECT (SELECT count(*) FROM agent.model_calls), "
                                    "(SELECT count(*) FROM ops.action_log)"))).one()
    for q in ({}, {"range": "month"}, {"only": "messages_in"}):
        await _get(owner, **q)
    await db.rollback()
    after = (await db.execute(text("SELECT (SELECT count(*) FROM agent.model_calls), "
                                   "(SELECT count(*) FROM ops.action_log)"))).one()
    assert tuple(before) == tuple(after)
