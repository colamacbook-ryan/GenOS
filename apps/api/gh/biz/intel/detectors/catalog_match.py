# ruff: noqa: E501
"""W4 — Cơ hội & kết nối `co_hoi_ket_noi`: khớp cầu của khách với hàng của Sếp + cổng `co_hoi_lon_sap_troi` (spec r6.6.1 §4.2, §4.2.1, §4B.3, §4C; T6).

Detector KHÔNG gán mức: chỉ dựng `levels.SignalIn` (điểm khớp catalog, Nguồn lực đã xác nhận, chạm `owner_value_limit`, hạn, `explicit_urgent`)
rồi gọi `signals.upsert`; mức, tắt theo bối cảnh (`gia_dinh`/`ban_be`, nhóm chưa xác nhận) và cổng 5 do `levels.compute` quyết.

- Hàng của Sếp (`biz.catalog_items` active) được phản chiếu thành tín hiệu cung tổng hợp trong `biz.market_signals` với `meaning_unit_id` = id
  hàng (không cần cột mới; `/matches?source=catalog` nhận ra qua đó) rồi chấm bằng `score_match` qua adapter `market.service.catalog_as_supply`.
- Nguồn lực: catalog một mình là suy luận (≥ 75 điểm); `resource_owner_confirmed` chỉ khi có fact `owner_resource` (owner_confirmed) khớp mặt hàng.
- Hồ sơ Sếp: chỉ `owner_confirmed` (snapshot). `owner_value_limit` dạng "không nhận đơn dưới N triệu" ⇒ giá trị cầu < N thì `touches_value_limit`
  (trần impact 1, không cổng); dạng "không làm hàng X" và cầu cùng mặt hàng ⇒ KHÔNG tạo cơ hội (trái Hồ sơ, không báo).
- Một cơ hội một thẻ: `case_key` = `levels.case_key(demand_id)`; cùng khách nhắc lại cùng nhu cầu trong 7 ngày ⇒ gộp vào thẻ đang mở (bằng chứng gắn thêm).
"""

from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from typing import Any

import orjson
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gh.biz.intel import catalog, levels, signals
from gh.biz.market import service as msvc
from gh.biz.mem import entities, profile_read, vi_time

log = logging.getLogger("gh.intel.catalog_match")

KIND = "co_hoi_ket_noi"
MERGE_DAYS = 7
RECENT_DEMAND_DAYS = 14
MAX_DEMANDS = 500
_MONEY = re.compile(r"(\d+(?:[.,]\d+)*)\s*(tỷ|tỉ|triệu|tr|nghìn|ngàn|k|đồng|đ)\b", re.I)
_UNIT = {"tỷ": 1e9, "tỉ": 1e9, "triệu": 1e6, "tr": 1e6, "nghìn": 1e3, "ngàn": 1e3, "k": 1e3, "đồng": 1.0, "đ": 1.0}
_BELOW = re.compile(r"(?:dưới|ít hơn|nhỏ hơn|thấp hơn|<)\s*(\d+(?:[.,]\d+)*)\s*(tỷ|tỉ|triệu|tr|nghìn|ngàn|k|đồng|đ)\b", re.I)
_FORBID = re.compile(r"không\s+(?:làm|bán|nhận|kinh doanh|buôn)\s+(?:mặt\s+)?hàng\s+(.+)$", re.I)


@dataclass(frozen=True)
class ValueLimits:
    min_value_vnd: int | None = None
    forbidden: tuple[frozenset[str], ...] = field(default_factory=tuple)


def _amount(num: str, unit: str) -> int:
    n = num.strip()
    n = n.replace(".", "") if re.fullmatch(r"\d{1,3}(\.\d{3})+", n) else n.replace(",", ".")
    return int(float(n) * _UNIT[unit.lower()])


def parse_value_limits(limit_texts: list[str]) -> ValueLimits:
    """Giới hạn số / mặt hàng từ chữ của `owner_value_limit`. Câu không hiểu (vd "không bàn chính trị") bị bỏ qua — không tự suy diễn."""
    mins: list[int] = []
    forbidden: list[frozenset[str]] = []
    for t in limit_texts:
        m = _BELOW.search(t)
        if m:
            mins.append(_amount(m.group(1), m.group(2)))
            continue
        f = _FORBID.search(t.strip())
        if f:
            words = frozenset(msvc._norm_words(f.group(1)))
            if words:
                forbidden.append(words)
    return ValueLimits(max(mins) if mins else None, tuple(forbidden))


def touches_forbidden(lim: ValueLimits, item: str) -> bool:
    words = msvc._norm_words(item)
    return any(msvc._same_item(words, set(f)) for f in lim.forbidden)


def below_min(lim: ValueLimits, value_vnd: float | None) -> bool:
    """Chỉ kết luận "dưới ngưỡng" khi biết giá trị (không biết ⇒ không tự cho là trái Hồ sơ)."""
    return lim.min_value_vnd is not None and value_vnd is not None and value_vnd > 0 and value_vnd < lim.min_value_vnd


# ─── đồng bộ hàng của Sếp → tín hiệu cung tổng hợp ───────────────────────────

async def sync_supply(db: AsyncSession, org_id: uuid.UUID) -> dict[uuid.UUID, uuid.UUID]:
    """{catalog_item_id: supply_signal_id} cho hàng `active`; hàng khác (gợi ý chưa duyệt / lưu trữ) ⇒ đóng tín hiệu cung và gỡ gợi ý còn `suggested`."""
    items = await catalog.list_items(db, org_id, state=None)
    out: dict[uuid.UUID, uuid.UUID] = {}
    for it in items:
        if it.state == "active":
            out[it.id] = (await db.execute(text("""
                INSERT INTO biz.market_signals (org_id, side, item, quantity, unit, value_vnd, status, meaning_unit_id)
                VALUES (:o, 'supply', :n, NULL, :u, :p, 'open', :i)
                ON CONFLICT (meaning_unit_id) WHERE meaning_unit_id IS NOT NULL
                DO UPDATE SET item = EXCLUDED.item, unit = EXCLUDED.unit, value_vnd = EXCLUDED.value_vnd, status = 'open'
                RETURNING id"""), {"o": org_id, "n": it.name[:300], "u": it.unit, "p": it.price_vnd, "i": it.id})).scalar_one()
    inactive = [it.id for it in items if it.state != "active"]
    if inactive:
        await db.execute(text("UPDATE biz.market_signals SET status = 'closed' WHERE org_id = :o AND side = 'supply' AND meaning_unit_id = ANY(:i)"),
                         {"o": org_id, "i": inactive})
        await db.execute(text("""DELETE FROM biz.matches WHERE org_id = :o AND status = 'suggested' AND supply_id IN
                                 (SELECT id FROM biz.market_signals WHERE org_id = :o AND meaning_unit_id = ANY(:i))"""), {"o": org_id, "i": inactive})
    return out


# ─── một tín hiệu cầu ────────────────────────────────────────────────────────

async def _merge_key(db: AsyncSession, org_id: uuid.UUID, person_id: uuid.UUID, key: str, now: datetime) -> str | None:
    """`case_key` của thẻ cơ hội đang mở cùng khách + cùng nhu cầu trong 7 ngày (để gộp, không báo lại)."""
    return (await db.execute(text("""
        SELECT case_key FROM biz.alerts WHERE org_id = :o AND kind = :k AND status IN ('open', 'acknowledged') AND created_at > :t
          AND facts ->> 'match_key' = :mk AND facts -> 'person_ids' ? CAST(:p AS text) ORDER BY created_at LIMIT 1"""),
                             {"o": org_id, "k": KIND, "t": now - timedelta(days=MERGE_DAYS), "mk": key, "p": str(person_id)})).scalar_one_or_none()


def _deadline(needed_by: Any) -> datetime | None:
    """`needed_by` là ngày ⇒ hạn hết ngày đó theo giờ VN."""
    if needed_by is None:
        return None
    return datetime.combine(needed_by, time(23, 59, 59), tzinfo=levels.VN)


async def process_demand(db: AsyncSession, org_id: uuid.UUID, demand_id: uuid.UUID, now: datetime) -> list[signals.Signal]:
    """Chấm tín hiệu cầu với hàng của Sếp; ghi `biz.matches` (cung tổng hợp từ catalog) và, nếu khớp Nguồn lực, một tín hiệu `co_hoi_ket_noi`."""
    d = (await db.execute(text("""
        SELECT s.id, s.item, s.category, s.quantity, s.unit, s.value_vnd, s.location, s.needed_by, s.meaning_unit_id, s.created_at,
               mu.person_id, mu.group_id, mu.observed_at, mu.confidence
        FROM biz.market_signals s LEFT JOIN clean.meaning_units mu ON mu.id = s.meaning_unit_id
        WHERE s.id = :i AND s.org_id = :o AND s.side = 'demand' AND s.status = 'open'"""), {"i": demand_id, "o": org_id})).one_or_none()
    if d is None:
        return []
    supply_ids = await sync_supply(db, org_id)
    prof = await profile_read.snapshot(db, org_id)
    lim = parse_value_limits([i.text for i in prof.items if i.predicate == "owner_value_limit"])
    if touches_forbidden(lim, d.item):
        return []                                                                   # trái Hồ sơ ("không làm hàng X") ⇒ không ghép, không báo
    demand = {"item": d.item, "category": d.category, "quantity": float(d.quantity) if d.quantity else None, "unit": d.unit,
              "value_vnd": float(d.value_vnd) if d.value_vnd else None, "location": d.location}
    scored: list[tuple[float, list[str], catalog.Item]] = []
    for it in await catalog.list_items(db, org_id, state="active"):
        score, reasons = msvc.score_match(demand, msvc.catalog_as_supply(it, demand))
        if score >= msvc.MIN_MATCH_SCORE:
            scored.append((score, reasons, it))
            await db.execute(text("""
                INSERT INTO biz.matches (org_id, demand_id, supply_id, score, reasons, status) VALUES (:o, :d, :s, :sc, CAST(:r AS jsonb), 'suggested')
                ON CONFLICT (demand_id, supply_id) DO UPDATE SET score = EXCLUDED.score, reasons = EXCLUDED.reasons, updated_at = now()
                WHERE biz.matches.status = 'suggested'"""),
                             {"o": org_id, "d": d.id, "s": supply_ids[it.id], "sc": score, "r": _json(reasons)})
    keep = [supply_ids[it.id] for _, _, it in scored]
    await db.execute(text("""DELETE FROM biz.matches WHERE org_id = :o AND demand_id = :d AND status = 'suggested' AND supply_id IN
                             (SELECT id FROM biz.market_signals WHERE org_id = :o AND meaning_unit_id IN (SELECT id FROM biz.catalog_items WHERE org_id = :o))
                             AND NOT (supply_id = ANY(:k))"""), {"o": org_id, "d": d.id, "k": keep})
    dwords = msvc._norm_words(d.item)
    res_items = [i for i in prof.items if i.predicate == "owner_resource" and msvc._same_item(dwords, msvc._norm_words(i.text))]
    if (not scored and not res_items) or d.person_id is None or d.meaning_unit_id is None:
        return []
    best = max(scored, key=lambda x: x[0]) if scored else None
    ev = (await db.execute(text("""SELECT raw_event_id, raw_received_at, quote, quote_status FROM clean.evidence WHERE meaning_unit_id = :u
                                   ORDER BY raw_received_at"""), {"u": d.meaning_unit_id})).all()
    if not ev:
        return []
    verified = all(e.quote_status == "verified" for e in ev)
    quote = " ".join(e.quote or "" for e in ev) if verified else ""
    value = demand["value_vnd"]
    below = below_min(lim, value)
    key = str(best[2].id) if best else "resource:" + ",".join(sorted(dwords))
    ent = (await entities.ensure_person(db, org_id, d.person_id)).id
    msg_at = d.observed_at
    case_key = await _merge_key(db, org_id, d.person_id, key, now) or levels.case_key(KIND, at=msg_at, demand_id=str(d.id))
    members = int((await db.execute(text("SELECT member_count FROM core.groups WHERE id = :g AND org_id = :o"), {"g": d.group_id, "o": org_id})).scalar_one_or_none() or 0) if d.group_id else 0
    thread = levels.Thread(type="group" if d.group_id else "dm_owner", size=members, owner_in_thread=True, agent_in_thread=True)
    resource_ok = bool(res_items)
    li = levels.SignalIn(
        kind=KIND, msg_at=msg_at, event_at=msg_at, deadline_at=_deadline(d.needed_by),
        flags=levels.Flags(explicit_urgent=bool(quote) and vi_time.has_urgent(quote)),
        gate_candidate=levels.G5 if (resource_ok or (best and best[0] >= 75)) and not below else None,   # chạm giới hạn ⇒ không đề cử cổng (7d sẽ đẩy lên 🟠)
        harm_what=f"có thể mất cơ hội bán {d.item}", thread=thread, source_class="insider", confidence_exist=0.8, confidence_content=0.765,
        opportunity=levels.Opportunity(catalog_score=best[0] if best else None, resource_owner_confirmed=resource_ok, touches_value_limit=below),
        money_vnd=int(value) if value else None, money_basis="estimated" if value else None,
        impact_base=levels.impact_base(KIND, important=True), subject_entity_id=ent)
    first = ev[0]
    evidence = [signals.EvidenceIn(type="meaning_unit", id=d.meaning_unit_id, quote_status="verified" if verified else "unverifiable",
                                   raw_event_id=first.raw_event_id, received_at=first.raw_received_at, quote=(first.quote or None) if verified else None)]
    facts = {"value_vnd": int(value) if value else None, "match_score": best[0] if best else None, "match_key": key,
             "catalog_item": best[2].name if best else None, "resource_owner_confirmed": resource_ok, "touches_value_limit": below}
    owner_facts = [i.fact_id for i in res_items] + ([i.fact_id for i in prof.items if i.predicate == "owner_value_limit"] if below else [])
    thread_key = f"g:{d.group_id}" if d.group_id else None
    # chính sách bối cảnh do `relane` đọc từ thread_key (contexts.policy); không đọc ở đây để không có hai nơi quyết
    try:
        sig = await signals.upsert(db, org_id, signals.UpsertIn(
            kind=KIND, title=f"Có người cần {d.item}"[:300], suggested_action="Soạn tin chào hàng cho người cần", detector="catalog_match",
            detector_version="1", level_input=li, evidence=evidence, case_key=case_key, thread_key=thread_key, group_id=d.group_id,
            subject_type="person", subject_id=d.person_id, person_ids=[d.person_id], owner_fact_ids=owner_facts, facts=facts), now)
    except signals.UnverifiedQuote:
        log.info("catalog_match: bằng chứng chưa kiểm ⇒ không tạo tín hiệu (demand %s)", d.id)
        return []
    return [sig]


def _json(v: Any) -> str:
    return orjson.dumps(v).decode()


async def recompute_org(db: AsyncSession, org_id: uuid.UUID, now: datetime) -> int:
    """Chấm lại mọi tín hiệu cầu còn mở gần đây (hàng của Sếp đổi: thêm/lưu trữ/đổi giá). Chạy lại an toàn (khoá `case_key`, ON CONFLICT)."""
    await sync_supply(db, org_id)
    ids = (await db.execute(text("""SELECT id FROM biz.market_signals WHERE org_id = :o AND side = 'demand' AND status = 'open'
                                    AND created_at > :t ORDER BY created_at DESC LIMIT :n"""),
                            {"o": org_id, "t": now - timedelta(days=RECENT_DEMAND_DAYS), "n": MAX_DEMANDS})).scalars().all()
    n = 0
    for i in ids:
        n += len(await process_demand(db, org_id, i, now))
    return n

