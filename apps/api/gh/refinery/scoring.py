"""Chấm điểm theo trọng số (refinery.scoring_weights) — không chứng cứ thì không có điểm (ARCHITECTURE §9).

Điểm một đơn vị ý nghĩa: mỗi chiều 0–100 (tín hiệu model + cộng dồn từ quy tắc + gắn kết + độ tin), tổng = Σ w·s.
Điểm của người / nhóm: mỗi chiều = max theo thời gian có suy giảm (nửa đời 7 ngày) trên các đơn vị 30 ngày gần nhất;
ghi snapshot mới (không ghi đè) kèm giải thích trỏ tới đơn vị ý nghĩa, rồi cập nhật bảng đọc nhanh current_scores.
"""

import math
import uuid
from datetime import UTC, datetime
from typing import Any

import orjson
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

DIMENSIONS = ("heat", "potential", "churn_risk", "fit", "engagement", "data_confidence")
SIGNAL_DIMS = ("heat", "potential", "churn_risk", "fit")
HALF_LIFE_DAYS = 7.0
WINDOW_DAYS = 30


async def weights(db: AsyncSession, org_id: uuid.UUID) -> dict[str, float]:
    rows = (await db.execute(text("""
        SELECT DISTINCT ON (dimension) dimension, weight FROM refinery.scoring_weights
        WHERE org_id = :o AND valid_from <= now() ORDER BY dimension, valid_from DESC"""), {"o": org_id})).all()
    return {r.dimension: float(r.weight) for r in rows}


def unit_scores(signals: dict[str, Any], adds: dict[str, float], confidence: float,
                engagement: float) -> dict[str, float]:
    out: dict[str, float] = {}
    for d in SIGNAL_DIMS:
        try:
            base = float(signals.get(d, 0) or 0)
        except (TypeError, ValueError):
            base = 0.0
        out[d] = max(0.0, min(100.0, base + adds.get(d, 0.0)))
    out["engagement"] = max(0.0, min(100.0, engagement))
    out["data_confidence"] = round(max(0.0, min(1.0, confidence)) * 100, 1)
    return out


def total(scores: dict[str, float], w: dict[str, float]) -> int:
    s = sum(w.get(d, 0.0) * scores.get(d, 0.0) for d in DIMENSIONS)
    norm = sum(w.get(d, 0.0) for d in DIMENSIONS) or 1.0
    return int(round(s / norm))


async def engagement(db: AsyncSession, person_id: uuid.UUID | None) -> float:
    if person_id is None:
        return 0.0
    n = (await db.execute(text("""
        SELECT count(*) FROM raw.events e JOIN core.person_identities pi ON pi.id = e.sender_identity_id
        WHERE pi.person_id = :p AND e.received_at > now() - interval '30 days'"""), {"p": person_id})).scalar_one()
    return min(100.0, float(n) * 5)


async def refresh_subject(db: AsyncSession, org_id: uuid.UUID, subject_type: str, subject_id: uuid.UUID,
                          w: dict[str, float]) -> None:
    col = "person_id" if subject_type == "person" else "group_id"
    units = (await db.execute(text(f"""
        SELECT id, observed_at, event_type, conclusion, scores, confidence FROM clean.meaning_units
        WHERE org_id = :o AND {col} = :s AND superseded_by IS NULL
          AND observed_at > now() - interval '{WINDOW_DAYS} days'
        ORDER BY observed_at DESC LIMIT 200"""), {"o": org_id, "s": subject_id})).all()  # noqa: S608
    if not units:
        return    # không chứng cứ → không điểm
    now = datetime.now(UTC)
    for d in DIMENSIONS:
        best, best_unit = 0.0, None
        contributors = []
        for u in units:
            v = float((u.scores or {}).get(d, 0) or 0)
            if v <= 0:
                continue
            age = max(0.0, (now - u.observed_at).total_seconds() / 86400)
            dv = v * math.pow(0.5, age / HALF_LIFE_DAYS)
            contributors.append((dv, u))
            if dv > best:
                best, best_unit = dv, u
        if best_unit is None:
            continue
        contributors.sort(key=lambda t: -t[0])
        factors = [{"label": f"{u.event_type}: {u.conclusion[:120]}", "value": round(v, 1),
                    "evidence": [{"type": "meaning_unit", "id": str(u.id)}]} for v, u in contributors[:3]]
        conf = sum(float(u.confidence) for _, u in contributors[:3]) / min(3, len(contributors))
        prev = (await db.execute(text("""SELECT value FROM clean.current_scores
                                         WHERE subject_type = :t AND subject_id = :s AND dimension = :d"""),
                                 {"t": subject_type, "s": subject_id, "d": d})).scalar_one_or_none()
        value = round(best, 2)
        trend = None if prev is None else ("up" if value > float(prev) + 1 else "down" if value < float(prev) - 1
                                           else "flat")
        snap = (await db.execute(text("""
            INSERT INTO clean.score_snapshots (org_id, subject_type, subject_id, dimension, value, confidence,
                                               explanation)
            VALUES (:o, :t, :s, :d, :v, :c, CAST(:e AS jsonb)) RETURNING id"""),
            {"o": org_id, "t": subject_type, "s": subject_id, "d": d, "v": value, "c": round(conf, 3),
             "e": orjson.dumps({"method": "rules+model", "weight": w.get(d), "factors": factors}).decode()}
        )).scalar_one()
        await db.execute(text("""
            INSERT INTO clean.current_scores (subject_type, subject_id, dimension, value, trend, snapshot_id,
                                              updated_at)
            VALUES (:t, :s, :d, :v, :tr, :sid, now())
            ON CONFLICT (subject_type, subject_id, dimension) DO UPDATE
              SET value = EXCLUDED.value, trend = EXCLUDED.trend, snapshot_id = EXCLUDED.snapshot_id,
                  updated_at = EXCLUDED.updated_at"""),
            {"t": subject_type, "s": subject_id, "d": d, "v": value, "tr": trend, "sid": snap})
