# ruff: noqa: E501
"""Chỉ số Tình báo + báo cáo tuần (v0.1.76, gói T9 — spec r6 §2.2 M1/M1b/M2/M5/M11/M13, W9).

`compute(db, org, days)` CHỈ ĐỌC, lọc `org_id`, trả số + mã (không bao giờ chữ tin / tiêu đề / ghi chú của Sếp):
- `precision_ci`: theo cổng, bản Đúng/Sai mới nhất mỗi tín hiệu, Wilson 95% (`m1_ok` khi n ≥ 15, điểm ≥ 80%, cận dưới ≥ 60%); `amber` = M1b (không cổng).
- `false_alarms`: M2 — 🔴 bị Sếp chấm Sai / ngày (trung bình `days`) + số 🔴 bị hạ do chạm trần / ngày.
- `recall`: M11 — recall = phát hiện / (phát hiện + sót ước tính); sót ước tính = Σ 1/p của «sót» trong mẫu giàu tín hiệu (p lấy từ ghi chú
  «mẫu tầng tX p=…» T3 ghi; «em sót» tự báo p=1); khoảng tin cậy bootstrap 1.000 lần trên các ngày (hạt giống cố định); `n_eff` Kish trên mẫu đã gửi.
- `cost`: M13 — token/VND/lượt theo luồng từ `refinery.group_budget` + % thời gian luồng bị giãn nhịp / chạm trần (`group_watermarks`, số phút TÍCH LUỸ
  chia cửa sổ, kẹp 100%).
- `acted`: M5 — tỉ lệ 🔴/🟠 đã gửi mà `outcome='acted'`, theo kết quả.
Không có đường nào ở đây ghi vào tín hiệu / mute / chính sách: ngưỡng KHÔNG tự chỉnh, nên cổng lừa đảo/an toàn không thể bị hạ từ chỉ số.
Báo cáo tuần: `deliver_weekly` — một tin `reports.enqueue(kind='reply')` mỗi tuần (Chủ nhật 18:30 VN), CHỈ số tổng hợp; khoá trợ lý chặn được.
Route: `GET /intel/metrics/full?days=` (Owner).
"""

from __future__ import annotations

import logging
import math
import random
import re
import uuid
from datetime import date, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gh.auth import service
from gh.auth.deps import require_owner
from gh.biz.hooks import CronJob
from gh.biz.intel import levels
from gh.biz.intel.pipes import coverage, delivery
from gh.db import DB
from gh.owner_channel import reports
from gh.refinery import budget, clock
from gh.telegram import backup

log = logging.getLogger("gh.intel.metrics")
router = APIRouter(tags=["intel"])

BOOTSTRAP_N = 1000
SEED = 20261025
_P_RE = re.compile(r"\bp=([0-9]*\.?[0-9]+)")
VN = levels.VN
CRITICAL_GATES = (levels.G1, levels.G2)


# ─── thống kê thuần ──────────────────────────────────────────────────────────

def wilson(k: int, n: int, z: float = 1.96) -> dict[str, Any]:
    if n <= 0:
        return {"p": None, "lo95": None, "hi95": None, "n": 0}
    p = k / n
    den = 1 + z * z / n
    centre = p + z * z / (2 * n)
    rad = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return {"p": p, "lo95": max(0.0, (centre - rad) / den), "hi95": min(1.0, (centre + rad) / den), "n": n}


def n_eff(weights: list[float]) -> float:
    """Cỡ mẫu hiệu dụng Kish: (Σw)² / Σw²."""
    s2 = sum(w * w for w in weights)
    return 0.0 if s2 <= 0 else sum(weights) ** 2 / s2


def recall_ci(days: list[tuple[int, float]]) -> dict[str, float] | None:
    """`days` = [(số phát hiện, sót ước tính có trọng số)] theo từng ngày. None khi không có gì để đo."""
    d, m = sum(x[0] for x in days), sum(x[1] for x in days)
    if d + m <= 0:
        return None
    est = d / (d + m)
    rnd = random.Random(SEED)
    k = len(days)
    samples: list[float] = []
    for _ in range(BOOTSTRAP_N):
        picks = [days[rnd.randrange(k)] for _ in range(k)]
        dd, mm = sum(x[0] for x in picks), sum(x[1] for x in picks)
        if dd + mm > 0:
            samples.append(dd / (dd + mm))
    samples.sort()
    lo = samples[int(0.025 * (len(samples) - 1))] if samples else est
    hi = samples[int(0.975 * (len(samples) - 1))] if samples else est
    return {"est": est, "lo95": min(lo, est), "hi95": max(hi, est)}


def _weight(note: str | None) -> float:
    m = _P_RE.search(note or "")
    if m is None:
        return 1.0
    p = float(m.group(1))
    return 1.0 / p if 0 < p <= 1 else 1.0


# ─── đọc số liệu ─────────────────────────────────────────────────────────────

_LATEST = """SELECT DISTINCT ON (f.signal_id) f.signal_id, f.gate, f.verdict, f.at FROM biz.signal_feedback f
             JOIN biz.alerts a ON a.id = f.signal_id AND a.org_id = f.org_id
             WHERE f.org_id = :o AND f.verdict_by = 'owner' AND f.verdict IN ('correct', 'wrong') AND f.at >= :s
               AND a.kind NOT IN ('system', 'legacy') ORDER BY f.signal_id, f.at DESC"""


async def _precision(db: AsyncSession, org_id: uuid.UUID, since: datetime) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows = (await db.execute(text(f"SELECT gate, count(*) FILTER (WHERE verdict = 'correct') AS k, count(*) AS n FROM ({_LATEST}) t GROUP BY gate"),  # noqa: S608
                             {"o": org_id, "s": since})).all()
    got = {r.gate: (int(r.k), int(r.n)) for r in rows}
    out = []
    for g in levels.GATES:
        k, n = got.get(g, (0, 0))
        w = wilson(k, n)
        out.append({"gate": g, "k": k, **w, "m1_ok": bool(n >= 15 and w["p"] >= 0.8 and w["lo95"] >= 0.6) if n else False})
    k, n = got.get(None, (0, 0))
    amber = {"k": k, **wilson(k, n)}
    amber["m1b_ok"] = bool(n and amber["p"] >= 0.7)
    return out, amber


async def _false_alarms(db: AsyncSession, org_id: uuid.UUID, since: datetime, days: int) -> dict[str, Any]:
    rows = (await db.execute(text(f"""SELECT (at AT TIME ZONE 'Asia/Ho_Chi_Minh')::date AS d, count(*) AS n FROM ({_LATEST}) t
                                      WHERE verdict = 'wrong' AND gate = ANY(:g) GROUP BY 1 ORDER BY 1"""),  # noqa: S608
                              {"o": org_id, "s": since, "g": list(levels.GATES)})).all()
    capped = int((await db.execute(text("SELECT count(*) FROM biz.alerts WHERE org_id = :o AND capped_at >= :s AND kind NOT IN ('system', 'legacy')"),
                                   {"o": org_id, "s": since})).scalar_one())
    total = sum(int(r.n) for r in rows)
    return {"total": total, "per_day_avg": total / days, "by_day": [{"day": r.d.isoformat(), "n": int(r.n)} for r in rows],
            "capped_per_day": capped / days}


async def _recall(db: AsyncSession, org_id: uuid.UUID, since: datetime, days: int, now: datetime) -> dict[str, Any]:
    det = {r.d: int(r.n) for r in (await db.execute(text("""
        SELECT (created_at AT TIME ZONE 'Asia/Ho_Chi_Minh')::date AS d, count(*) AS n FROM biz.alerts
        WHERE org_id = :o AND created_at >= :s AND level IN ('red', 'amber') AND kind NOT IN ('system', 'legacy')
          AND feedback_verdict IS DISTINCT FROM 'wrong' GROUP BY 1"""), {"o": org_id, "s": since})).all()}
    miss_rows = (await db.execute(text("""
        SELECT (at AT TIME ZONE 'Asia/Ho_Chi_Minh')::date AS d, gate, note FROM biz.signal_feedback
        WHERE org_id = :o AND verdict = 'missed' AND verdict_by = 'owner' AND at >= :s"""), {"o": org_id, "s": since})).all()
    miss_by_day: dict[date, float] = {}
    for r in miss_rows:
        miss_by_day[r.d] = miss_by_day.get(r.d, 0.0) + _weight(r.note)
    today = now.astimezone(VN).date()
    cal = [today - timedelta(days=i) for i in range(days)]
    series = [(det.get(d, 0), miss_by_day.get(d, 0.0)) for d in cal]
    outside = sum(v for d, v in miss_by_day.items() if d not in cal) + 0.0                 # ranh giới múi giờ: không bỏ sót
    outside_d = sum(v for d, v in det.items() if d not in cal)
    if outside or outside_d:
        series[0] = (series[0][0] + outside_d, series[0][1] + outside)
    refs = (await db.execute(text("""SELECT refs FROM ops.owner_outbox WHERE org_id = :o AND kind = 'ask' AND dedupe_key LIKE 'shadow-pack:%'
                                     AND created_at >= :s"""), {"o": org_id, "s": since})).scalars().all()
    ws = [1.0 / float(x["p"]) for rr in refs for x in (rr or []) if isinstance(x, dict) and x.get("tier") != "shadow" and x.get("p")]
    ci = recall_ci(series)
    return {"est": ci["est"] if ci else None, "lo95": ci["lo95"] if ci else None, "hi95": ci["hi95"] if ci else None,
            "detected_n": sum(x[0] for x in series), "missed_n": len(miss_rows), "missed_est": sum(x[1] for x in series),
            "n_eff": n_eff(ws), "sample_n": len(ws), "critical_missed_n": sum(1 for r in miss_rows if r.gate in CRITICAL_GATES)}


async def _cost(db: AsyncSession, org_id: uuid.UUID, days: int, now: datetime) -> dict[str, Any]:
    first = now.astimezone(VN).date() - timedelta(days=days - 1)
    rows = (await db.execute(text("""SELECT thread_key, day, tokens_in, tokens_out, cost_vnd, calls FROM refinery.group_budget
                                     WHERE org_id = :o AND day >= :d"""), {"o": org_id, "d": first})).all()
    wm = {r.thread_key: r for r in (await db.execute(text("""SELECT thread_key, stretched_minutes, capped_minutes, stretched_at, capped_at
                                                              FROM refinery.group_watermarks WHERE org_id = :o"""), {"o": org_id})).all()}
    agg: dict[str, dict[str, Any]] = {}
    per_day: dict[date, float] = {}
    for r in rows:
        a = agg.setdefault(r.thread_key, {"tokens_in": 0, "tokens_out": 0, "cost_vnd": 0.0, "calls": 0})
        a["tokens_in"] += int(r.tokens_in)
        a["tokens_out"] += int(r.tokens_out)
        a["cost_vnd"] += float(r.cost_vnd)
        a["calls"] += int(r.calls)
        per_day[r.day] = per_day.get(r.day, 0.0) + float(r.cost_vnd)
    window = days * 1440

    def pct(mins: int, since: datetime | None) -> float:
        extra = 0 if since is None else max(0, int((now - since).total_seconds() // 60))
        return min(100.0, (mins + extra) / window * 100)

    out = []
    for tk in sorted(set(agg) | set(wm)):
        a = agg.get(tk, {"tokens_in": 0, "tokens_out": 0, "cost_vnd": 0.0, "calls": 0})
        w = wm.get(tk)
        out.append({"thread_key": tk, **a, "stretched_pct": pct(int(w.stretched_minutes), w.stretched_at) if w else 0.0,
                    "capped_pct": pct(int(w.capped_minutes), w.capped_at) if w else 0.0})
    caps = await budget.load_caps(db, org_id)
    over = sum(1 for v in per_day.values() if v > caps.budget_vnd) if caps.budget_vnd > 0 else None
    return {"total_vnd": sum(x["cost_vnd"] for x in out), "tokens_in": sum(x["tokens_in"] for x in out),
            "tokens_out": sum(x["tokens_out"] for x in out), "calls": sum(x["calls"] for x in out), "days_over_cap": over,
            "by_thread": out}


async def _acted(db: AsyncSession, org_id: uuid.UUID, since: datetime) -> dict[str, Any]:
    r = (await db.execute(text("""
        SELECT count(*) AS delivered, count(*) FILTER (WHERE outcome = 'acted') AS acted,
               count(*) FILTER (WHERE outcome_result = 'saved') AS saved, count(*) FILTER (WHERE outcome_result = 'lost') AS lost,
               count(*) FILTER (WHERE outcome = 'ignored') AS ignored, coalesce(sum(outcome_value_vnd) FILTER (WHERE outcome_result = 'saved'), 0) AS saved_vnd
        FROM biz.alerts WHERE org_id = :o AND created_at >= :s AND level IN ('red', 'amber') AND kind NOT IN ('system', 'legacy')
          AND (pushed_at IS NOT NULL OR digest_at IS NOT NULL)"""), {"o": org_id, "s": since})).one()
    d = int(r.delivered)
    return {"delivered": d, "acted": int(r.acted), "rate": (int(r.acted) / d) if d else None, "saved": int(r.saved), "lost": int(r.lost),
            "ignored": int(r.ignored), "saved_value_vnd": int(r.saved_vnd)}


async def compute(db: AsyncSession, org_id: uuid.UUID, *, days: int = 14, now: datetime | None = None) -> dict[str, Any]:
    now = now or clock.now()
    since = now - timedelta(days=days)
    prec, amber = await _precision(db, org_id, since)
    would = int((await db.execute(text("SELECT count(*) FROM biz.alerts WHERE org_id = :o AND would_push_at >= :s"),
                                  {"o": org_id, "s": since})).scalar_one())
    return {"days": days, "precision_ci": prec, "amber": amber, "false_alarms": await _false_alarms(db, org_id, since, days),
            "recall": await _recall(db, org_id, since, days, now), "cost": await _cost(db, org_id, days, now),
            "acted": await _acted(db, org_id, since), "shadow": {"would_push_n": would},
            "coverage_line": await coverage.line(db, org_id, now)}


@router.get("/intel/metrics/full")
async def metrics_full(days: int = Query(default=14, ge=7, le=30), user: service.CurrentUser = Depends(require_owner),
                       db: AsyncSession = DB) -> dict[str, Any]:
    """Chỉ số đầy đủ (Owner). Chỉ số + mã, không chữ tin. `/intel/metrics` (T1) giữ khung cũ."""
    return await compute(db, user.org_id, days=days)


# ─── báo cáo tuần ────────────────────────────────────────────────────────────

def _pct(x: float | None) -> str:
    return "chưa đủ dữ liệu" if x is None else f"{round(x * 100)}%"


def weekly_text(m: dict[str, Any]) -> str:
    """CHỈ số tổng hợp (không tiêu đề/trích/tên người/nhóm)."""
    k = sum(r["k"] for r in m["precision_ci"])
    n = sum(r["n"] for r in m["precision_ci"])
    w = wilson(k, n)
    rc, c, a = m["recall"], m["cost"], m["acted"]
    lines = [f"Tình báo tuần qua ({m['days']} ngày)",
             f"• 🔴 đúng: {k}/{n} ({_pct(w['p'])}" + (f", khoảng {_pct(w['lo95'])}–{_pct(w['hi95'])})" if n else ")"),
             f"• 🔴 báo nhầm: {m['false_alarms']['per_day_avg']:.2f}/ngày · hạ do trần {m['false_alarms']['capped_per_day']:.2f}/ngày",
             f"• Bỏ sót ước tính: recall {_pct(rc['est'])}" + (f" (khoảng {_pct(rc['lo95'])}–{_pct(rc['hi95'])})" if rc["est"] is not None else "")
             + f" · Sếp báo sót {rc['missed_n']}, lừa đảo/an toàn sót {rc['critical_missed_n']}",
             f"• Đã hành động: {a['acted']}/{a['delivered']} ({_pct(a['rate'])})",
             f"• Chi phí: {round(c['total_vnd']):,} ₫".replace(",", ".") + f" · {c['calls']} lượt"]
    if c["by_thread"]:
        lines.append(f"• Giãn nhịp tối đa {max(x['stretched_pct'] for x in c['by_thread']):.0f}% thời gian ở một luồng")
    return "\n".join(lines)[: reports.TEXT_MAX]


async def deliver_weekly(db: AsyncSession, org_id: uuid.UUID, now: datetime) -> str:
    """no_channel | locked | already_sent | sent | not_queued. Bên gọi commit."""
    ch = await delivery._channel(db, org_id)
    if ch is None:
        return "no_channel"
    if await backup.is_locked(db, org_id):
        return "locked"
    iso = now.astimezone(VN).isocalendar()
    key = f"intel-weekly:{iso.year}-W{iso.week:02d}"
    if (await db.execute(text("SELECT 1 FROM ops.owner_outbox WHERE org_id = :o AND channel_type = :c AND dedupe_key = :k"),
                         {"o": org_id, "c": ch, "k": key})).first() is not None:
        return "already_sent"
    body = weekly_text(await compute(db, org_id, days=7, now=now))
    ok = await reports.enqueue(db, org_id, ch, kind="reply", severity="important", text_=body, dedupe_key=key, now=now)
    return "sent" if ok else "not_queued"


async def intel_weekly(ctx: dict[str, Any]) -> dict[str, int]:
    from gh.db import sessionmaker

    now = clock.now()
    out = {"sent": 0, "errors": 0}
    local = now.astimezone(VN)
    if local.weekday() != 6 or local.hour != 18:
        return out
    sm = sessionmaker()
    async with sm() as db:
        orgs = (await db.execute(text("SELECT id FROM core.organizations"))).scalars().all()
    for org in orgs:
        try:
            async with sm() as db:
                if await deliver_weekly(db, org, now) == "sent":
                    out["sent"] += 1
                await db.commit()
        except Exception:                                       # noqa: BLE001 — một tổ chức lỗi không dừng tổ chức khác
            log.exception("intel_weekly lỗi ở một tổ chức")
            out["errors"] += 1
    return out


JOBS: list[CronJob] = [(intel_weekly, {"minute": {30, 35, 40}})]
__all__ = ["JOBS", "compute", "deliver_weekly", "n_eff", "recall_ci", "weekly_text", "wilson"]
