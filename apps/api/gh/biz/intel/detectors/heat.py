# ruff: noqa: E501
"""W3 — quan hệ nóng/nguội `quan_he_nong_nguoi` từ `mem.relation_heat_weekly` + nhịp đặt hàng riêng (spec §4B.1, W3; T5).

Hai cách "nguội", đều tính từ METADATA (không đọc nội dung tin để chấm):
- nhịp tin của quan hệ (`mem.heat.cooling(heat.series(...))`: 2 tuần gần nhất < 50% các tuần trước);
- nhịp ĐẶT HÀNG của khách (`biz.deals` thắng): im quá `max(MIN_SILENT_DAYS, SILENT_FACTOR × khoảng cách trung vị)` ngày — vd khách đặt đều 6 ngày
  một lần mà 15 ngày chưa đặt. Điểm yếu G-KB (b): khách "im lặng" theo nhịp đặt hàng phải sinh tín hiệu mà không cần tin mới.

Áp cho khách của Sếp và người trong `owner_key_relation` ở mọi bối cảnh (policy chỉ hạ/tắt trong `levels.compute`). Việc nền `intel_heat_scan`
chạy sau job dựng lại độ nóng 01:40 của L4 (`mem_heat_weekly`) và vài lần trong ngày; chạy lại an toàn (cùng `case_key` ⇒ gộp). Quan hệ hồi phục ⇒ đóng.
Khách doạ bỏ đi qua cổng `khach_che_cong_khai_doa_bo` ở `conflicts.py`, không ở đây.
"""

from __future__ import annotations

import logging
import statistics
import uuid
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gh.biz.hooks import CronJob, Hook
from gh.biz.intel import levels, person_role, signals
from gh.biz.mem import entities, heat, profile_read

log = logging.getLogger("gh.intel.heat")

KIND = "quan_he_nong_nguoi"
MIN_ORDERS = 3
MIN_SILENT_DAYS = 7
SILENT_FACTOR = 2.0
MAX_SILENT_DAYS = 120                     # im quá lâu là đã mất, không còn là "nguội dần"
HEAT_WEEKS = 12
SCAN_LIMIT = 500


def order_silence(won_at: list[datetime], now: datetime) -> tuple[int, float] | None:
    """(số ngày chưa đặt, khoảng cách trung vị giữa các đơn) nếu khách im quá nhịp của chính họ; không thì None. Hàm thuần."""
    ds = sorted(levels._aware(d) for d in won_at)
    if len(ds) < MIN_ORDERS:
        return None
    gaps = [(b - a).total_seconds() / 86400 for a, b in zip(ds, ds[1:], strict=False)]
    med = statistics.median(gaps)
    silent = (levels._aware(now) - ds[-1]).total_seconds() / 86400
    if MIN_SILENT_DAYS <= max(MIN_SILENT_DAYS, SILENT_FACTOR * med) <= silent <= MAX_SILENT_DAYS:
        return int(silent), round(med, 1)
    return None


async def _candidates(db: AsyncSession, org_id: uuid.UUID, key_rel: frozenset[uuid.UUID]) -> dict[uuid.UUID, dict[str, Any]]:
    """person_id → {entity, name, customer, key}. Khách (person_type customer / customer_of) hoặc người quan trọng của Sếp."""
    rows = (await db.execute(text("""
        SELECT p.id AS person_id, p.display_name, e.id AS entity_id,
               (p.person_type = 'customer' OR EXISTS (SELECT 1 FROM mem.facts f WHERE f.org_id = :o AND f.subject_id = e.id AND f.predicate = 'customer_of'
                                                       AND f.valid_to IS NULL AND f.status IN ('asserted', 'owner_confirmed'))) AS customer
        FROM core.persons p JOIN mem.entities e ON e.person_id = p.id AND e.org_id = p.org_id AND e.merged_into IS NULL AND e.forgotten_at IS NULL
        WHERE p.org_id = :o AND p.deleted_at IS NULL AND p.merged_into_id IS NULL AND NOT e.is_owner
          AND (e.id = ANY(:key) OR p.person_type = 'customer' OR EXISTS (SELECT 1 FROM mem.facts f WHERE f.org_id = :o AND f.subject_id = e.id
               AND f.predicate = 'customer_of' AND f.valid_to IS NULL AND f.status IN ('asserted', 'owner_confirmed')))
        ORDER BY e.id LIMIT :n"""),                                      # lọc khách/người quan trọng TRONG SQL rồi mới cắt (không bỏ sót vì LIMIT)
                            {"o": org_id, "n": SCAN_LIMIT, "key": list(key_rel)})).all()
    return {r.person_id: {"entity": r.entity_id, "name": r.display_name, "customer": bool(r.customer), "key": r.entity_id in key_rel}
            for r in rows}


async def _last_message(db: AsyncSession, org_id: uuid.UUID, person_id: uuid.UUID) -> tuple[uuid.UUID, datetime, uuid.UUID | None] | None:
    r = (await db.execute(text("""
        SELECT e.id, e.occurred_at, e.group_id FROM raw.events e JOIN core.person_identities pi ON pi.id = e.sender_identity_id
        WHERE e.org_id = :o AND pi.person_id = :p AND e.body_text IS NOT NULL AND e.body_text <> '' ORDER BY e.occurred_at DESC LIMIT 1"""),
                          {"o": org_id, "p": person_id})).one_or_none()
    return (r.id, r.occurred_at, r.group_id) if r else None


async def scan(db: AsyncSession, org_id: uuid.UUID, now: datetime) -> dict[str, int]:
    """Quét một tổ chức: sinh/gộp tín hiệu cho quan hệ nguội, đóng tín hiệu khi quan hệ hồi phục. Bên gọi commit."""
    owner = await entities.owner(db, org_id)
    snap = await profile_read.snapshot(db, org_id)
    cands = await _candidates(db, org_id, snap.key_relation_entity_ids())
    out = {"checked": len(cands), "signals": 0, "resolved": 0}
    if not cands:
        return out
    deals: dict[uuid.UUID, list[datetime]] = {}
    for r in (await db.execute(text("SELECT person_id, won_at FROM biz.deals WHERE org_id = :o AND status = 'won' AND won_at IS NOT NULL "
                                    "AND person_id = ANY(:p)"), {"o": org_id, "p": list(cands)})).all():
        deals.setdefault(r.person_id, []).append(r.won_at)
    lines = await heat.series_many(db, org_id, owner.id, [c["entity"] for c in cands.values()], HEAT_WEEKS, now=now)
    for pid, c in cands.items():
        silence = order_silence(deals.get(pid, []), now) if c["customer"] else None
        cooling = heat.cooling(lines.get(c["entity"], []))
        case_key = levels.case_key(KIND, at=now, entity_id=str(c["entity"]))
        if not (silence or cooling):
            out["resolved"] += await _resolve_recovered(db, org_id, pid, now)
            continue
        last = await _last_message(db, org_id, pid)
        if last is None:
            continue                                   # không có bằng chứng đã kiểm để trích ⇒ không thành tín hiệu (§8.1)
        legacy = (await db.execute(text("""SELECT 1 FROM biz.alerts WHERE org_id = :o AND kind = :k AND subject_id = :p AND status IN ('open', 'acknowledged')
                                           AND split_part(COALESCE(case_key, ''), '|#', 1) IS DISTINCT FROM :ck AND created_at > :t LIMIT 1"""),
                                   {"o": org_id, "k": KIND, "p": pid, "ck": case_key, "t": now - timedelta(days=14)})).first()
        if legacy:
            continue                                   # đã có cảnh báo "khách lạnh" cũ cho người này — không nhân đôi
        why = []
        if silence:
            why.append(f"{silence[0]} ngày chưa đặt (nhịp thường {silence[1]:g} ngày)")
        if cooling:
            why.append("nhịp trao đổi giảm mạnh so với các tuần trước")
        aud: int | None = None
        if last[2] is not None:                                # khán giả nhóm (r7 (c′)): code đếm khách KHÁC người này; không đếm được ⇒ None
            aud = await person_role.audience_n(db, org_id, last[2], speaker_person_id=pid, speaker_entity_id=c["entity"])
        li = levels.SignalIn(kind=KIND, msg_at=last[1], event_at=now, ongoing=True, customer_of_owner=c["customer"], source_class="rule",
                             confidence_exist=1.0, confidence_content=0.8, subject_entity_id=c["entity"],
                             impact_base=levels.impact_base(KIND, important=c["customer"] or c["key"]),
                             thread=levels.Thread(type="group" if last[2] else "dm_owner", owner_in_thread=True, agent_in_thread=True,
                                                  customer_audience_n=aud))
        up = signals.UpsertIn(kind=KIND, title=f"{c['name']} đang nguội đi", summary="; ".join(why), detector="heat", detector_version="1",
                              suggested_action="Chủ động hỏi thăm hoặc gọi lại", level_input=li, case_key=case_key,
                              evidence=[signals.EvidenceIn(type="raw_event", id=last[0], source_field="body", span_start=0, span_end=1)],
                              subject_type="person", subject_id=pid, person_ids=[pid], group_id=last[2],
                              thread_key=f"g:{last[2]}" if last[2] else None,   # bối cảnh nhóm Sếp đã xác nhận quyết `quan_he` có bật không (policy)
                              facts={"order_silent_days": silence[0] if silence else None, "median_gap_days": silence[1] if silence else None,
                                     "heat_cooling": cooling, "customer": c["customer"], "key_relation": c["key"]})
        await signals.upsert(db, org_id, up, now)
        out["signals"] += 1
    return out


async def _resolve_recovered(db: AsyncSession, org_id: uuid.UUID, person_id: uuid.UUID, now: datetime) -> int:
    """Đóng THEO NGƯỜI (không theo khoá cửa sổ 14 ngày): tín hiệu mở từ cửa sổ trước vẫn đóng khi quan hệ hồi phục."""
    rows = (await db.execute(text("SELECT id FROM biz.alerts WHERE org_id = :o AND subject_id = :p AND kind = :k AND detector = 'heat' "
                                  "AND status IN ('open', 'acknowledged')"), {"o": org_id, "p": person_id, "k": KIND})).scalars().all()
    for i in rows:
        await signals.transition(db, org_id, i, "resolved", now=now)
    return len(rows)


async def intel_heat_scan(ctx: dict[str, Any]) -> dict[str, int]:
    from gh.db import sessionmaker
    from gh.refinery import clock

    sm = sessionmaker()
    tot = {"signals": 0, "resolved": 0, "errors": 0}
    async with sm() as db:
        orgs = (await db.execute(text("SELECT id FROM core.organizations"))).scalars().all()
    for org in orgs:
        try:
            async with sm() as db:
                r = await scan(db, org, clock.now())
                await db.commit()
            tot["signals"] += r["signals"]
            tot["resolved"] += r["resolved"]
        except Exception:                                       # noqa: BLE001 — một tổ chức lỗi không dừng tổ chức khác
            log.exception("intel_heat_scan lỗi ở một tổ chức")
            tot["errors"] += 1
    return tot


HOOKS: list[Hook] = []
# 01:55 (sau `mem_heat_weekly` 01:40 của L4), 07:55 (trước giờ làm: khách im theo nhịp đặt hàng ra tín hiệu ngay đầu ngày) và 19:55.
# Không chạy 08:00–18:00: job quét mọi khách theo tổ chức là job nặng theo giờ cố định (test_worker_schedule_v0136).
JOBS: list[CronJob] = [(intel_heat_scan, {"hour": {1, 7, 19}, "minute": {55}})]
