"""Mặt tiền Owner v2 (v0.1.59, G1) — `GET /owner/insights?range=week|month&only=k1,k2`: Phân tích chạy ngay trên
dữ liệu đang có (đúng bảng 9 chỉ số của tài liệu thiết kế mục 6).

CHỈ ĐỌC. Không REFRESH, không đọc view tổng hợp, không job nền, không gọi model. Quy ước:

- Ngày theo giờ VN: `(ts AT TIME ZONE 'Asia/Ho_Chi_Minh')::date`. `week` = 7 ngày gần nhất (gồm hôm nay) so với 7 ngày
  liền trước; `month` = 30 so với 30. Chuỗi theo ngày tô 0 ngày trống bằng `generate_series`.
- Truy vấn thẳng bảng gốc, luôn có `WHERE org_id` + khoảng thời gian. Bảng thô `raw.events` chia phân vùng theo
  `received_at` ⇒ kèm cận dưới `received_at` (nhận không bao giờ sớm hơn lúc xảy ra quá 1 ngày) để cắt phân vùng.
- MỖI chỉ số và MỖI khối breakdown chạy trong savepoint riêng với `statement_timeout` 5 giây; lỗi/quá giờ thì chỉ
  chỉ số (khối) đó `state: 'error'` (kiểu `briefing.collect`), phần còn lại vẫn có.
- `delta_pct = (kỳ này − kỳ trước) / kỳ trước × 100`; kỳ trước = 0 ⇒ `null` (web hiện "Mới").
- `reply_fast_pct` / `unanswered`: CTE riêng theo kiểu ghép LATERAL của `gh.biz.people.service.PAIR_CTE` ("tin đến →
  tin đi sớm nhất cùng luồng") nhưng tin đi phải GỬI CHO ĐÚNG khách đó (cùng điều kiện `feed.outbound_to_sql` mà ngữ
  cảnh hội thoại ở Thông tin dùng — người nhận trong payload, hoặc tin đến 1-1 gần nhất trước nó là của khách), và chỉ
  tin 1-1 (`group_id IS NULL`) từ người KHÔNG phải nhân viên — tin nhóm Zalo
  đông người sinh hàng nghìn tin "chưa ai trả lời" giả. Hai chỉ số dùng chung một lần truy vấn.
- `ai_cost_vnd`: `gh.ai_cost.daily_totals` rồi cắt 2 kỳ, CHỈ model đã có giá; `ai_cost.unpriced_calls` = số lượt
  chưa đặt giá (web không bao giờ hiện "0 ₫" khi còn lượt chưa đặt giá).
- Trạng thái: `empty` (tổ chức chưa từng có tin) · `collecting` (tin đầu tiên chưa đủ 2 kỳ; `days_until_compare` =
  2×kỳ − số ngày từ tin đầu) · `ok`.
"""

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gh import ai_cost
from gh.errors import field_errors
from gh.owner import feed
from gh.owner import service as svc
from gh.refinery import triage

log = logging.getLogger("gh.owner.insights")

TZ = ai_cost.TIMEZONE
STATEMENT_TIMEOUT = "5s"
RANGES = {"week": 7, "month": 30}
FAST_MINUTES = 15
#: Kết quả `raw.events.received_at` có thể sớm hơn `occurred_at` tối đa chừng này (lệch đồng hồ) — dùng cho cận dưới.
RECEIVED_SLACK = timedelta(days=1)
#: "Tin đầu tiên": lấy trong số bấy nhiêu tin nhận sớm nhất (không quét hết bảng thô chỉ để biết ngày bắt đầu).
FIRST_SCAN = 1000
TOP_EVENT_TYPES = 5

#: (key, nhãn, đơn vị, chiều tốt). Thứ tự này cũng là thứ tự trả về.
METRICS: tuple[tuple[str, str, str, str], ...] = (
    ("messages_in", "Tin nhận", "count", "up"),
    ("signals", "Tín hiệu đáng chú ý", "count", "up"),
    ("new_contacts", "Người mới liên hệ", "count", "up"),
    ("opps_new", "Cơ hội mới", "count", "up"),
    ("opps_won", "Cơ hội thắng", "count", "up"),
    ("reply_fast_pct", "Trả lời nhanh", "pct", "up"),
    ("unanswered", "Tin khách chờ trả lời", "count", "down"),
    ("ai_cost_vnd", "Chi phí trợ lý Gen", "vnd", "down"),
    ("filtered", "Gen lọc giúp", "count", "up"),
)
METRIC_KEYS = tuple(m[0] for m in METRICS)

STAGE_ORDER = ("raw_signal", "validated", "matched", "approaching", "negotiating", "handed_off", "won", "lost",
               "dormant")
STAGE_LABELS = {
    "raw_signal": "Tín hiệu mới", "validated": "Đã xác thực", "matched": "Đã ghép cung cầu",
    "approaching": "Đang tiếp cận", "negotiating": "Đang đàm phán", "handed_off": "Đã chuyển nội bộ",
    "won": "Thắng", "lost": "Trượt", "dormant": "Tạm gác",
}


@dataclass(frozen=True)
class Window:
    n: int
    to: date
    from_: date
    prev_to: date
    prev_from: date

    @property
    def start(self) -> datetime:           # 00:00 giờ VN của ngày đầu kỳ trước
        return ai_cost.day_bounds(self.prev_from)[0]

    @property
    def end(self) -> datetime:             # 00:00 giờ VN sau ngày cuối kỳ này
        return ai_cost.day_bounds(self.to)[1]

    @property
    def cur_start(self) -> datetime:
        return ai_cost.day_bounds(self.from_)[0]

    @property
    def recv_start(self) -> datetime:
        return self.start - RECEIVED_SLACK

    def params(self, org_id: Any) -> dict[str, Any]:
        return {"o": org_id, "s": self.start, "e": self.end, "rs": self.recv_start, "df": self.prev_from,
                "cnt": 2 * self.n - 1}


def window(range_: str, today: date) -> Window:
    n = RANGES[range_]
    from_ = today - timedelta(days=n - 1)
    prev_to = from_ - timedelta(days=1)
    return Window(n=n, to=today, from_=from_, prev_to=prev_to, prev_from=prev_to - timedelta(days=n - 1))


@dataclass
class Raw:
    """Kết quả một chỉ số: chuỗi 2 kỳ (cũ → mới) + tổng từng kỳ."""
    series: list[tuple[date, float]]
    total: float
    prev_total: float
    value_vnd: int | None = None


@dataclass
class Ctx:
    """Bộ nhớ trong MỘT request: chia sẻ kết quả truy vấn đắt giữa các chỉ số (trả lời nhanh + chưa trả lời)."""
    cache: dict[str, Any] = field(default_factory=dict)
    ai: dict[str, int] = field(default_factory=lambda: {"calls": 0, "unpriced_calls": 0})


@asynccontextmanager
async def guarded(db: AsyncSession) -> AsyncIterator[None]:
    """Savepoint riêng + `statement_timeout` 5 giây. Lỗi ⇒ rollback về savepoint (giao dịch chính còn dùng được) và ném
    lại để người gọi đánh dấu `error`. Xong thì trả timeout về mặc định cho phần còn lại của giao dịch."""
    async with db.begin_nested():
        await db.execute(text(f"SET LOCAL statement_timeout = '{STATEMENT_TIMEOUT}'"))
        yield
    await db.execute(text("SET LOCAL statement_timeout = DEFAULT"))


# ─── chuỗi theo ngày ─────────────────────────────────────────────────────────────────────────────────────────────

_DAILY_SQL = """
WITH days AS (SELECT CAST(:df AS date) + g AS d FROM generate_series(0, :cnt) g),
agg AS (SELECT ({ts} AT TIME ZONE 'Asia/Ho_Chi_Minh')::date AS d, count(*) AS n, {v} AS v
        FROM {table} WHERE {where} AND {ts} >= :s AND {ts} < :e GROUP BY 1)
SELECT days.d, COALESCE(agg.n, 0) AS n, COALESCE(agg.v, 0) AS v FROM days LEFT JOIN agg ON agg.d = days.d
ORDER BY days.d"""

#: Chỉ số đếm thẳng từ một bảng: (bảng + bí danh, cột thời gian, điều kiện, biểu thức giá trị phụ).
_SIMPLE: dict[str, tuple[str, str, str, str]] = {
    "messages_in": ("raw.events e", "e.occurred_at",
                    "e.org_id = :o AND e.direction = 'inbound' AND e.received_at >= :rs", "0"),
    "signals": ("clean.meaning_units mu", "mu.observed_at", "mu.org_id = :o AND mu.superseded_by IS NULL", "0"),
    # người mới = mọi người chưa xoá/gộp trừ nhân viên (người mới từ Zalo mặc định là `unknown`, chưa phải `customer`)
    "new_contacts": ("core.persons p", "p.created_at",
                     "p.org_id = :o AND p.deleted_at IS NULL AND p.merged_into_id IS NULL "
                     "AND p.person_type <> 'staff'", "0"),
    "opps_new": ("biz.opportunities o", "o.first_signal_at", "o.org_id = :o", "0"),
    "opps_won": ("biz.opportunities o", "o.closed_at", "o.org_id = :o AND o.stage = 'won'",
                 "COALESCE(sum(o.value_vnd), 0)"),
}


def _split(rows: list[tuple[date, float]], w: Window) -> Raw:
    cur = sum(v for d, v in rows if d >= w.from_)
    return Raw(rows, cur, sum(v for d, v in rows if d < w.from_))


async def _simple(key: str, db: AsyncSession, org_id: Any, w: Window, ctx: Ctx) -> Raw:
    table, ts, where, val = _SIMPLE[key]
    rows = (await db.execute(text(_DAILY_SQL.format(table=table, ts=ts, where=where, v=val)),  # noqa: S608
                             w.params(org_id))).all()
    raw = _split([(r.d, float(r.n)) for r in rows], w)
    if key == "opps_won":
        raw.value_vnd = int(sum(r.v for r in rows if r.d >= w.from_) or 0)
    return raw


# ─── trả lời nhanh / chưa trả lời (CTE riêng, chỉ tin 1-1 từ người không phải nhân viên) ─────────────────────────

_REPLY_SQL = f"""
WITH inbound AS (
  SELECT e.id, e.channel_id, e.occurred_at,
         -- định danh của CHÍNH khách này (mọi định danh của họ trên kênh) để chỉ ghép tin đi gửi đúng họ
         ARRAY(SELECT x.id FROM core.person_identities x
               WHERE x.person_id = pi.person_id AND x.channel_id = e.channel_id) AS ids,
         ARRAY(SELECT NULLIF(split_part(x.external_id, '@', 1), '') FROM core.person_identities x
               WHERE x.person_id = pi.person_id AND x.channel_id = e.channel_id) AS ext
  FROM raw.events e
  LEFT JOIN core.person_identities pi ON pi.id = e.sender_identity_id
  LEFT JOIN core.persons sp ON sp.id = pi.person_id
  WHERE e.org_id = :o AND e.direction = 'inbound' AND e.group_id IS NULL
    AND e.received_at >= :rs AND e.occurred_at >= :s AND e.occurred_at < :e
    AND COALESCE(sp.person_type, '') <> 'staff'
),
paired AS (
  SELECT i.id, i.occurred_at AS asked_at, o.occurred_at AS replied_at
  FROM inbound i
  LEFT JOIN LATERAL (
    SELECT e2.occurred_at FROM raw.events e2
    WHERE e2.org_id = :o AND e2.channel_id = i.channel_id AND e2.group_id IS NULL AND e2.direction = 'outbound'
      AND e2.received_at >= :rs AND e2.occurred_at > i.occurred_at
      AND {feed.outbound_to_sql("e2", "i.ids", "i.ext")}
    ORDER BY e2.occurred_at LIMIT 1
  ) o ON true
),
days AS (SELECT CAST(:df AS date) + g AS d FROM generate_series(0, :cnt) g),
agg AS (
  SELECT (asked_at AT TIME ZONE 'Asia/Ho_Chi_Minh')::date AS d,
         count(*) FILTER (WHERE replied_at IS NOT NULL) AS answered,
         count(*) FILTER (WHERE replied_at IS NOT NULL
                          AND replied_at - asked_at < make_interval(mins => :fast)) AS fast,
         count(*) FILTER (WHERE replied_at IS NULL) AS unanswered
  FROM paired GROUP BY 1)
SELECT days.d, COALESCE(agg.answered, 0) AS answered, COALESCE(agg.fast, 0) AS fast,
       COALESCE(agg.unanswered, 0) AS unanswered
FROM days LEFT JOIN agg ON agg.d = days.d ORDER BY days.d"""


async def _reply_rows(db: AsyncSession, org_id: Any, w: Window, ctx: Ctx) -> list[Any]:
    if "reply" not in ctx.cache:
        try:
            ctx.cache["reply"] = list((await db.execute(text(_REPLY_SQL), {**w.params(org_id),
                                                                         "fast": FAST_MINUTES})).all())
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — nhớ lỗi để chỉ số kia không chạy lại truy vấn đắt
            ctx.cache["reply"] = exc
    got = ctx.cache["reply"]
    if isinstance(got, Exception):
        raise got
    return got  # type: ignore[no-any-return]


async def _reply_fast_pct(db: AsyncSession, org_id: Any, w: Window, ctx: Ctx) -> Raw:
    rows = await _reply_rows(db, org_id, w, ctx)
    series = [(r.d, float(round(100 * r.fast / r.answered)) if r.answered else 0.0) for r in rows]

    def pct(sel: Callable[[Any], bool]) -> float:
        answered = sum(r.answered for r in rows if sel(r))
        return float(round(100 * sum(r.fast for r in rows if sel(r)) / answered)) if answered else 0.0

    return Raw(series, pct(lambda r: r.d >= w.from_), pct(lambda r: r.d < w.from_))


async def _unanswered(db: AsyncSession, org_id: Any, w: Window, ctx: Ctx) -> Raw:
    rows = await _reply_rows(db, org_id, w, ctx)
    return _split([(r.d, float(r.unanswered)) for r in rows], w)


# ─── chi phí trợ lý Gen / Gen lọc giúp ───────────────────────────────────────────────────────────────────────────

async def _ai_cost_vnd(db: AsyncSession, org_id: Any, w: Window, ctx: Ctx) -> Raw:
    rows = (await ai_cost.daily_totals(db, org_id, w.to, days=2 * w.n))[-2 * w.n:]
    raw = _split([(date.fromisoformat(str(r["date"])), float(r["total_vnd"])) for r in rows], w)
    calls = (await db.execute(text(f"""
        SELECT count(*) AS calls, count(*) FILTER (WHERE {ai_cost._UNPRICED_EXPR}) AS unpriced
        {ai_cost._CALLS_FROM}
        WHERE c.org_id = :o AND c.status = 'ok' AND c.at >= :cs AND c.at < :e"""),  # noqa: S608
                              {"o": org_id, "cs": w.cur_start, "e": w.end, "cli": list(ai_cost.CLI_KINDS)})).one()
    ctx.ai = {"calls": int(calls.calls or 0), "unpriced_calls": int(calls.unpriced or 0)}
    return raw


async def _filtered(db: AsyncSession, org_id: Any, w: Window, ctx: Ctx) -> Raw:
    """Tổng tin rác/trùng Gen đã lọc — không chuỗi ngày. Kỳ trước = (2×kỳ) − kỳ này."""
    cur = int((await triage.value_summary(db, org_id, days=w.n)).get("filtered") or 0)
    both = int((await triage.value_summary(db, org_id, days=2 * w.n)).get("filtered") or 0)
    return Raw([], float(cur), float(max(both - cur, 0)))


Compute = Callable[[AsyncSession, Any, Window, Ctx], Awaitable[Raw]]


def _simple_for(key: str) -> Compute:
    async def run(db: AsyncSession, org_id: Any, w: Window, ctx: Ctx) -> Raw:
        return await _simple(key, db, org_id, w, ctx)

    return run


COMPUTE: dict[str, Compute] = {
    **{k: _simple_for(k) for k in _SIMPLE},
    "reply_fast_pct": _reply_fast_pct,
    "unanswered": _unanswered,
    "ai_cost_vnd": _ai_cost_vnd,
    "filtered": _filtered,
}


# ─── hình dạng trả về ────────────────────────────────────────────────────────────────────────────────────────────

def _num(v: float) -> int | float:
    return int(v) if float(v).is_integer() else round(float(v), 2)


def delta_pct(total: float, prev: float) -> float | None:
    return None if not prev else round((total - prev) / prev * 100, 1)


def _metric(key: str, label: str, unit: str, good: str, raw: Raw | None, w: Window) -> dict[str, Any]:
    if raw is None:                                   # lỗi riêng chỉ số này
        return {"key": key, "label": label, "unit": unit, "total": 0, "prev_total": 0, "delta_pct": None,
                "good": good, "series": [], "prev_series": [], "state": "error"}
    out: dict[str, Any] = {
        "key": key, "label": label, "unit": unit, "total": _num(raw.total), "prev_total": _num(raw.prev_total),
        "delta_pct": delta_pct(raw.total, raw.prev_total), "good": good,
        "series": [{"d": d.isoformat(), "v": _num(v)} for d, v in raw.series if d >= w.from_],
        "prev_series": [{"d": d.isoformat(), "v": _num(v)} for d, v in raw.series if d < w.from_]}
    if raw.value_vnd is not None:
        out["value_vnd"] = raw.value_vnd
    return out


async def _first_day(db: AsyncSession, org_id: Any) -> date | None:
    """Ngày VN của tin đầu tiên (xấp xỉ: nhỏ nhất trong `FIRST_SCAN` tin nhận sớm nhất). None = chưa từng có tin."""
    at = (await db.execute(text("""
        SELECT min(x.occurred_at) FROM (SELECT occurred_at FROM raw.events WHERE org_id = :o
                                        ORDER BY received_at LIMIT :n) x"""), {"o": org_id, "n": FIRST_SCAN})
          ).scalar_one_or_none()
    return at.astimezone(ai_cost.VN_TZ).date() if at is not None else None


async def _event_types(db: AsyncSession, org_id: Any, w: Window) -> list[dict[str, Any]]:
    rows = (await db.execute(text("""
        SELECT event_type, count(*) AS n FROM clean.meaning_units
        WHERE org_id = :o AND observed_at >= :cs AND observed_at < :e AND superseded_by IS NULL
        GROUP BY event_type ORDER BY n DESC, event_type LIMIT :top"""),
                             {"o": org_id, "cs": w.cur_start, "e": w.end, "top": TOP_EVENT_TYPES})).all()
    return [{"event_type": r.event_type, "label": feed.EVENT_TITLES.get(r.event_type, feed.OTHER_TITLE),
             "units": int(r.n)} for r in rows]


async def _funnel(db: AsyncSession, org_id: Any, w: Window) -> list[dict[str, Any]]:
    rows = (await db.execute(text("""
        SELECT stage, count(*) AS n, COALESCE(sum(value_vnd), 0) AS v FROM biz.opportunities
        WHERE org_id = :o AND (closed_at IS NULL OR (closed_at >= :cs AND closed_at < :e)) GROUP BY stage"""),
                             {"o": org_id, "cs": w.cur_start, "e": w.end})).all()
    by = {r.stage: r for r in rows}
    ordered = [s for s in STAGE_ORDER if s in by] + sorted(s for s in by if s not in STAGE_ORDER)
    return [{"stage": s, "label": STAGE_LABELS.get(s, s), "n": int(by[s].n), "value_vnd": int(by[s].v or 0)}
            for s in ordered]


async def _guarded_or_none(db: AsyncSession, what: str, fn: Callable[[], Awaitable[Any]]) -> Any:
    try:
        async with guarded(db):
            return await fn()
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 — một khối lỗi/quá giờ không làm hỏng cả trang
        log.warning("Phân tích: %s lỗi (%s)", what, type(exc).__name__)
        return None


def parse_only(only: str | None) -> list[str] | None:
    if only is None or not only.strip():
        return None
    keys = [k.strip() for k in only.split(",") if k.strip()]
    bad = [k for k in keys if k not in METRIC_KEYS]
    if bad:
        raise field_errors({"only": f"Chỉ số không có: {', '.join(bad)}"})
    return keys


async def insights(db: AsyncSession, org_id: Any, range_: str, only: list[str] | None = None,
                   *, now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    w = window(range_, ai_cost.today_vn(now))
    wanted = [m for m in METRICS if only is None or m[0] in only]
    ctx = Ctx()
    metrics: list[dict[str, Any]] = []
    for key, label, unit, good in wanted:
        async def run(k: str = key) -> Raw:
            return await COMPUTE[k](db, org_id, w, ctx)

        raw = await _guarded_or_none(db, f"chỉ số {key}", run)
        metrics.append(_metric(key, label, unit, good, raw, w))

    breakdown: dict[str, Any] | None = None
    if only is None:
        breakdown = {
            "event_types": await _guarded_or_none(db, "loại tin", lambda: _event_types(db, org_id, w)),
            "funnel": await _guarded_or_none(db, "phễu cơ hội", lambda: _funnel(db, org_id, w)),
        }

    first = await _guarded_or_none(db, "tin đầu tiên", lambda: _first_day(db, org_id))
    state, until = "ok", None
    if first is None and not await _has_events(db, org_id):
        state = "empty"
    elif first is not None:
        have = (w.to - first).days + 1
        if have < 2 * w.n:
            state, until = "collecting", 2 * w.n - have

    wants_cost = only is None or "ai_cost_vnd" in only
    feedback = None
    if range_ == "week" and wants_cost:
        feedback = await _guarded_or_none(db, "đánh giá Gen", lambda: ai_cost.feedback_7d(db, org_id))
    return {
        "range": range_, "from": w.from_.isoformat(), "to": w.to.isoformat(), "prev_from": w.prev_from.isoformat(),
        "prev_to": w.prev_to.isoformat(), "generated_at": now.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        "state": state, "days_until_compare": until,
        "channels_connected": await svc.channels_connected(db, org_id),
        "metrics": metrics, "breakdown": breakdown,
        "ai_cost": {**ctx.ai, "budget_vnd": await ai_cost.get_budget(db, org_id) if wants_cost else None},
        "feedback": ({"helpful": feedback["helpful"], "not_helpful": feedback["not_helpful"]}
                     if feedback is not None else None),
    }


async def _has_events(db: AsyncSession, org_id: Any) -> bool:
    """Phòng khi truy vấn "tin đầu tiên" lỗi: có ít nhất một tin nào không (một dòng, chỉ mục org_id)."""
    try:
        async with guarded(db):
            return (await db.execute(text("SELECT 1 FROM raw.events WHERE org_id = :o LIMIT 1"),
                                     {"o": org_id})).first() is not None
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001
        return True
