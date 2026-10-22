"""Việc nền của cụm Cơ hội & Thị trường:

- `market_signal_capture` (hook sau sàng lọc, `gh.clean.ready`): mỗi đơn vị ý nghĩa mới có `side` (`demand` |
  `supply` — do quy tắc R-01/R-02 của `gh.refinery.presets` gán) sinh một dòng `biz.market_signals`. Tín hiệu
  **cầu** của một người (nếu chưa có cơ hội đang mở nào gần đây cho cùng người) còn mở một dòng
  `biz.opportunities` ở giai đoạn `raw_signal` — đúng E3 "phát hiện người đang cần hàng… tạo hàng đợi cơ hội".
  Idempotent qua `ON CONFLICT (meaning_unit_id) DO NOTHING` (chỉ mục thêm ở `0008_p3_market.sql`) — hook chạy
  lại (retry/DLQ) không sinh tín hiệu trùng.
- `matches_recompute`: chấm lại điểm ghép Cung↔Cầu định kỳ cho mọi tín hiệu còn `open` (thuật toán ở
  `gh.biz.market.service.score_match`) — cũng gọi tay được qua `POST /matches/recompute`.

`HOOKS` / `JOBS`: xem `gh.biz.hooks`.
"""

import uuid
from typing import Any

import orjson
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gh.biz.hooks import CronJob, Hook, HookCtx
from gh.biz.market.service import (
    MIN_MATCH_SCORE,
    confidence_bucket,
    num_or_none,
    score_match,
    signal_from_entities,
)

DEMAND_DEDUPE_DAYS = 30   # không mở cơ hội mới cho cùng người nếu đã có một cái còn sống trong ngần ấy ngày
MATCH_CANDIDATES = 500    # trần số tín hiệu mỗi phía đưa vào chấm lại một lượt (đủ cho quy mô một tổ chức)


async def _capture_one(db: AsyncSession, org_id: uuid.UUID, unit_id: uuid.UUID) -> None:
    u = (await db.execute(text("""
        SELECT mu.id, mu.person_id, mu.group_id, mu.side, mu.conclusion, mu.entities, mu.confidence, mu.observed_at,
               -- v0.1.71 (P3-5): đơn vị CHỈ trích tin ĐI (nhân viên / tài khoản của Sếp nhắn) — không phải khách nói
               COALESCE((SELECT bool_and(e.direction = 'outbound') FROM clean.evidence ev
                         JOIN raw.events e ON e.id = ev.raw_event_id AND e.received_at = ev.raw_received_at
                         WHERE ev.meaning_unit_id = mu.id), false) AS from_us
        FROM clean.meaning_units mu WHERE mu.id = :i AND mu.org_id = :o AND mu.side IN ('demand', 'supply')"""),
        {"i": unit_id, "o": org_id})).one_or_none()
    if u is None or u.from_us:
        return
    sig = signal_from_entities(u.entities, u.conclusion)
    signal_id: uuid.UUID | None = None
    if sig is not None:
        row = (await db.execute(text("""
            INSERT INTO biz.market_signals (org_id, side, person_id, group_id, item, category, quantity, unit,
                                            value_vnd, location, needed_by, heat, status, meaning_unit_id)
            VALUES (:o, :side, :p, :g, :item, :cat, :qty, :unit, :val, :loc, :nb, :heat, 'open', :mu)
            ON CONFLICT (meaning_unit_id) WHERE meaning_unit_id IS NOT NULL DO NOTHING
            RETURNING id"""),
            {"o": org_id, "side": u.side, "p": u.person_id, "g": u.group_id, "item": sig["item"],
             "cat": sig["category"], "qty": sig["quantity"], "unit": sig["unit"], "val": sig["value_vnd"],
             "loc": sig["location"], "nb": sig["needed_by"], "heat": round(float(u.confidence) * 100, 2),
             "mu": unit_id})).one_or_none()
        if row is None:
            return
        signal_id = row.id
    # Thiếu tên hàng (entities.product/category) → `sig is None`: không có căn cứ ghép Cung ↔ Cầu nên KHÔNG tạo
    # tín hiệu (không bao giờ lấy cả câu kết luận làm `item`). Nhu cầu của khách vẫn là một việc cần người xử
    # lý, nên cầu của một người vẫn mở/cộng dồn cơ hội `raw_signal` (không gắn tín hiệu vì không có).
    if u.side != "demand" or u.person_id is None:
        return
    value_vnd = sig["value_vnd"] if sig else num_or_none((u.entities or {}).get("budget_vnd"))
    need = sig["item"] if sig else (u.conclusion or "")[:200]
    existing = (await db.execute(text("""
        SELECT id FROM biz.opportunities WHERE org_id = :o AND person_id = :p AND stage NOT IN ('won', 'lost',
        'dormant') AND created_at > now() - make_interval(days => :d) LIMIT 1"""),
        {"o": org_id, "p": u.person_id, "d": DEMAND_DEDUPE_DAYS})).one_or_none()
    if existing is not None:
        # Cùng người đang có cơ hội mở gần đây: cộng dồn giá trị thay vì mở trùng (tránh vỡ "một hàng đợi cơ
        # hội" thành nhiều thẻ cho cùng một nhu cầu đang nói tiếp).
        if value_vnd:
            await db.execute(text("""UPDATE biz.opportunities SET value_vnd = GREATEST(COALESCE(value_vnd, 0),
                                     :v), updated_at = now() WHERE id = :i"""),
                             {"v": value_vnd, "i": existing.id})
        return
    code = (await db.execute(text("SELECT core.next_code('OPP')"))).scalar_one()
    opp = (await db.execute(text("""
        INSERT INTO biz.opportunities (org_id, code, person_id, source_group_id, need, stage, value_vnd,
                                       confidence, first_signal_at, attrs)
        VALUES (:o, :c, :p, :g, :need, 'raw_signal', :v, :conf, :fs, CAST(:attrs AS jsonb))
        RETURNING id"""),
        {"o": org_id, "c": code, "p": u.person_id, "g": u.group_id, "need": need or u.conclusion,
         "v": value_vnd, "conf": confidence_bucket(float(u.confidence)), "fs": u.observed_at,
         "attrs": orjson.dumps({"demand_signal_id": str(signal_id)} if signal_id else {}).decode()})).one()
    await db.execute(text("""INSERT INTO biz.opportunity_stage_history (opportunity_id, from_stage, to_stage,
                             actor) VALUES (:i, NULL, 'raw_signal', 'system')"""), {"i": opp.id})


async def market_signal_capture(ctx: HookCtx) -> None:
    async with ctx.sm() as db:
        for unit_id in ctx.unit_ids:
            await _capture_one(db, ctx.org_id, unit_id)
        await db.commit()


async def recompute_matches_org(db: AsyncSession, org_id: uuid.UUID) -> int:
    """Chấm lại điểm ghép cho mọi cặp (tín hiệu cầu mở) × (tín hiệu cung mở); chỉ lưu khi điểm ≥
    `MIN_MATCH_SCORE`. Không đụng tới cặp đã `introduced`/`accepted`/`rejected` (`ON CONFLICT … WHERE status =
    'suggested'`) — chấm lại không được âm thầm đảo ngược quyết định người dùng đã làm."""
    demands = (await db.execute(text("""
        SELECT id, item, category, quantity, unit, value_vnd, location FROM biz.market_signals
        WHERE org_id = :o AND side = 'demand' AND status = 'open' ORDER BY created_at DESC LIMIT :n"""),
        {"o": org_id, "n": MATCH_CANDIDATES})).all()
    supplies = (await db.execute(text("""
        SELECT id, item, category, quantity, unit, value_vnd, location FROM biz.market_signals
        WHERE org_id = :o AND side = 'supply' AND status = 'open' ORDER BY created_at DESC LIMIT :n"""),
        {"o": org_id, "n": MATCH_CANDIDATES})).all()
    n = 0
    kept: set[tuple[uuid.UUID, uuid.UUID]] = set()
    for d in demands:
        d_sig = {"item": d.item, "category": d.category, "quantity": float(d.quantity) if d.quantity else None,
                 "unit": d.unit, "value_vnd": float(d.value_vnd) if d.value_vnd else None, "location": d.location}
        for s in supplies:
            s_sig = {"item": s.item, "category": s.category,
                     "quantity": float(s.quantity) if s.quantity else None, "unit": s.unit,
                     "value_vnd": float(s.value_vnd) if s.value_vnd else None, "location": s.location}
            score, reasons = score_match(d_sig, s_sig)
            if score < MIN_MATCH_SCORE:
                continue
            await db.execute(text("""
                INSERT INTO biz.matches (org_id, demand_id, supply_id, score, reasons, status)
                VALUES (:o, :d, :s, :sc, CAST(:r AS jsonb), 'suggested')
                ON CONFLICT (demand_id, supply_id) DO UPDATE SET score = EXCLUDED.score, reasons = EXCLUDED.reasons,
                    updated_at = now() WHERE biz.matches.status = 'suggested'"""),
                {"o": org_id, "d": d.id, "s": s.id, "sc": score, "r": orjson.dumps(reasons).decode()})
            kept.add((d.id, s.id))
            n += 1
    # Gợi ý cũ (`suggested`) của cặp trong cửa sổ chấm lần này mà nay điểm < ngưỡng (vd sinh ra bởi cách so từ
    # thô trước đây, hoặc dữ liệu cung/cầu đã đổi) thì gỡ đi — để Sếp không còn thấy gợi ý ghép hàng không liên
    # quan. Cặp đã `introduced`/`accepted`/`rejected` giữ nguyên.
    if demands and supplies:
        d_ids, s_ids = [d.id for d in demands], {s.id for s in supplies}
        stale = (await db.execute(text("""
            SELECT id, demand_id, supply_id FROM biz.matches
            WHERE org_id = :o AND status = 'suggested' AND demand_id = ANY(:ds)"""),
            {"o": org_id, "ds": d_ids})).all()
        stale_ids = [m.id for m in stale if m.supply_id in s_ids and (m.demand_id, m.supply_id) not in kept]
        if stale_ids:
            await db.execute(text("DELETE FROM biz.matches WHERE id = ANY(:ids) AND status = 'suggested'"),
                             {"ids": stale_ids})
    return n


async def matches_recompute(ctx: dict[str, Any]) -> dict[str, int]:
    from gh.db import sessionmaker

    sm = sessionmaker()
    out: dict[str, int] = {}
    async with sm() as db:
        orgs = (await db.execute(text("SELECT id FROM core.organizations"))).scalars().all()
    for org in orgs:
        async with sm() as db:
            n = await recompute_matches_org(db, org)
            await db.commit()
        out[str(org)] = n
    return out


HOOKS: list[Hook] = [Hook(name="market_signal_capture", fn=market_signal_capture)]
JOBS: list[CronJob] = [(matches_recompute, {"minute": set(range(11, 60, 15))})]
