"""API Cơ hội & Thị trường (docs/api/phase-3-market.md): Bảng cơ hội, Cung ↔ Cầu, Kho hội thoại, Deal & Vụ việc.

Tín hiệu cung/cầu và cơ hội "tín hiệu thô" sinh tự động từ sàng lọc — xem `gh.biz.market.jobs`. Thuật toán chấm
điểm ghép ở `gh.biz.market.service.score_match`.
"""

import uuid
from datetime import UTC, datetime
from typing import Any, Literal

import orjson
from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gh.auth import rbac, service
from gh.auth.deps import require
from gh.biz.core import explain
from gh.biz.core.drafts import Target, _execute_internal, create_draft
from gh.biz.core.scope import (
    Scope,
    direct_visible_sql,
    ensure_active_user,
    ensure_group,
    ensure_person,
    group_view_sql,
    not_found,
    scope_for,
)
from gh.biz.market import service as msvc
from gh.biz.market.jobs import recompute_matches_org
from gh.biz.market.service import CLOSED_STAGES, OPEN_STAGES, STAGES, person_or_group_scope_sql
from gh.chassis import actionlog
from gh.data.common import iso, mask_text, parse_cursor
from gh.db import DB
from gh.errors import ApiError, field_errors

router = APIRouter(tags=["market"])

READ = require("opportunity.read")
WRITE = require("opportunity.write")


# ═══ Bảng cơ hội ══════════════════════════════════════════════════════════════

def _opp_select(role: str) -> str:
    """`_OPP_SELECT` cho một vai: bản cầu–cung gợi ý cũng theo «Quyền xem» của nhóm phía cung (v0.1.71)."""
    gate = group_view_sql(role, "ms.group_id")
    return _OPP_SELECT.replace("{SM_GATE}", "TRUE" if gate == "TRUE" else f"(ms.group_id IS NULL OR {gate})")


_OPP_SELECT = f"""
SELECT o.id, o.code, o.need, o.stage, o.value_vnd, o.confidence, o.owner_user_id, o.first_signal_at,
       o.first_contact_at, o.closed_at, o.created_at, o.updated_at, o.person_id, o.attrs,
       p.id AS p_id, p.code AS p_code, p.display_name AS p_name, p.person_type AS p_type,
       p.organization_name AS p_org,
       g.id AS g_id, g.code AS g_code, g.name AS g_name, gc.type AS g_channel,
       u.id AS u_id, u.display_name AS u_name, uro.code AS u_role,
       cs.value AS heat,
       sm.score AS sm_score, sm.item AS sm_item, sm.sp_id, sm.sp_code, sm.sp_name, sm.sg_id, sm.sg_code, sm.sg_name
FROM biz.opportunities o
LEFT JOIN core.persons p ON p.id = o.person_id
LEFT JOIN core.groups g ON g.id = o.source_group_id
LEFT JOIN core.channels gc ON gc.id = g.channel_id
LEFT JOIN core.users u ON u.id = o.owner_user_id
{msvc.USER_ROLE_JOIN.format(alias="u", out="uro")}
LEFT JOIN clean.current_scores cs ON cs.subject_type = 'person' AND cs.subject_id = o.person_id
                                     AND cs.dimension = 'heat'
LEFT JOIN LATERAL (
  SELECT m.score, ms.item, sp.id AS sp_id, sp.code AS sp_code, sp.display_name AS sp_name,
         sg.id AS sg_id, sg.code AS sg_code, sg.name AS sg_name
  FROM biz.matches m JOIN biz.market_signals ms ON ms.id = m.supply_id
  LEFT JOIN core.persons sp ON sp.id = ms.person_id LEFT JOIN core.groups sg ON sg.id = ms.group_id
  WHERE m.demand_id = NULLIF(o.attrs->>'demand_signal_id', '')::uuid AND m.status = 'suggested' AND {{SM_GATE}}
  ORDER BY m.score DESC LIMIT 1
) sm ON true
"""

STALE_HOURS = 24
STALE_DAYS = 7


def _opp_item(r: Any, *, owner: bool) -> dict[str, Any]:
    risk_note = None
    if r.stage not in CLOSED_STAGES:
        now = datetime.now(UTC)
        if r.first_contact_at is None and (now - r.first_signal_at.astimezone(UTC)).total_seconds() > \
                STALE_HOURS * 3600:
            risk_note = f"Chưa tiếp cận sau {STALE_HOURS} giờ kể từ tín hiệu đầu tiên — dễ mất vào tay đối thủ"
        elif r.updated_at and (now - r.updated_at.astimezone(UTC)).days >= STALE_DAYS:
            risk_note = f"Đứng yên hơn {STALE_DAYS} ngày ở giai đoạn này — nên chủ động follow lại"
    suggested = None
    if r.sp_id or r.sg_id:
        suggested = {"item": r.sm_item, "score": float(r.sm_score),
                     "person": msvc.person_ref(r, "sp"), "group": msvc.group_ref(r, "sg")}
    return {"id": str(r.id), "code": r.code, "need": mask_text(r.need, owner), "stage": r.stage,
            "value_vnd": int(r.value_vnd) if r.value_vnd is not None else None, "confidence": r.confidence,
            "heat": float(r.heat) if r.heat is not None else None,
            "person": msvc.person_ref(r, "p"), "group": msvc.group_ref(r, "g"),
            "owner": msvc.user_ref(r.u_id, r.u_name, r.u_role),
            "first_signal_at": iso(r.first_signal_at), "first_contact_at": iso(r.first_contact_at),
            "closed_at": iso(r.closed_at), "created_at": iso(r.created_at), "updated_at": iso(r.updated_at),
            "suggested_match": suggested, "risk_note": risk_note}


@router.get("/opportunities")
async def list_opportunities(stage: str | None = None, owner_user_id: uuid.UUID | None = None,
                             confidence: Literal["high", "medium", "low"] | None = None, cursor: str | None = None,
                             limit: int = Query(50, ge=1, le=200), user: service.CurrentUser = Depends(READ),
                             db: AsyncSession = DB) -> dict[str, Any]:
    sc = await scope_for(db, user, "opportunity.read")
    scope_sql, scope_params = _opp_scope_sql(sc)
    conds = ["o.org_id = :o", scope_sql]
    params: dict[str, Any] = {"o": user.org_id, **scope_params}
    if stage:
        conds.append("o.stage = :stage")
        params["stage"] = stage
    if owner_user_id:
        conds.append("o.owner_user_id = :owner")
        params["owner"] = owner_user_id
    if confidence:
        conds.append("o.confidence = :conf")
        params["conf"] = confidence
    if cursor:
        conds.append("o.created_at < :c")
        params["c"] = parse_cursor(cursor)
    where = " AND ".join(conds)
    total = (await db.execute(text(f"SELECT count(*) FROM biz.opportunities o WHERE {where}"), params)  # noqa: S608
             ).scalar_one()
    rows = (await db.execute(text(_opp_select(user.role_code) + f" WHERE {where} ORDER BY o.created_at DESC LIMIT :n"),  # noqa: S608
                             {**params, "n": limit + 1})).all()
    owner = user.role_code == rbac.OWNER
    items = [_opp_item(r, owner=owner) for r in rows[:limit]]
    return {"items": items, "next_cursor": iso(rows[limit - 1].created_at) if len(rows) > limit else None,
            "total": total}


def _opp_scope_sql(sc: Scope) -> tuple[str, dict[str, Any]]:
    """Cơ hội thấy được khi người của nó nằm trong phạm vi, **hoặc** chính cơ hội được giao cho mình/team mình
    (`owner_user_id`) — hai điều kiện độc lập vì người phụ trách cơ hội có thể khác người phụ trách khách hàng
    (docs/api/phase-3.md: "Cơ hội… đi theo người/nhóm mà chúng gắn vào (hoặc người phụ trách = mình)")."""
    gv = group_view_sql(sc.role, "o.source_group_id")           # nhóm nguồn «Chỉ Sếp» ẩn với vai khác (v0.1.71)
    gate = "TRUE" if gv == "TRUE" else f"(o.source_group_id IS NULL OR {gv})"
    if sc.is_all:
        return gate, {}
    pw, pp = sc.person_id_sql("o.person_id")
    uw, up = sc.user_sql("o.owner_user_id")
    return f"(({pw} OR {uw}) AND {gate})", {**pp, **up}


@router.get("/opportunities/pipeline")
async def opportunities_pipeline(user: service.CurrentUser = Depends(READ), db: AsyncSession = DB) -> dict[str, Any]:
    """Tổng giá trị pipeline theo giai đoạn — `won`/`lost`/`dormant` không tính vào pipeline **đang mở** (đã
    chốt/đóng, không còn là cơ hội đang chạy) — quyết định tự đưa ra vì spec (PLAN §3.8) chỉ nói "tổng pipeline"
    mà không định nghĩa có tính giai đoạn đóng hay không; giữ lại số của từng giai đoạn (kể cả đóng) để đối
    chiếu tỉ lệ thắng/thua, chỉ loại khỏi `open_pipeline_value_vnd`."""
    sc = await scope_for(db, user, "opportunity.read")
    scope_sql, scope_params = _opp_scope_sql(sc)
    rows = (await db.execute(text(f"""
        SELECT stage, count(*) AS n, COALESCE(sum(value_vnd), 0) AS v FROM biz.opportunities o
        WHERE org_id = :o AND {scope_sql} GROUP BY stage"""),  # noqa: S608
        {"o": user.org_id, **scope_params})).all()
    by_stage = {r.stage: {"count": r.n, "value_vnd": int(r.v)} for r in rows}
    stages = [{"stage": s, "count": by_stage.get(s, {}).get("count", 0),
               "value_vnd": by_stage.get(s, {}).get("value_vnd", 0)} for s in STAGES]
    open_value = sum(s["value_vnd"] for s in stages if s["stage"] in OPEN_STAGES)
    open_count = sum(s["count"] for s in stages if s["stage"] in OPEN_STAGES)
    return {"stages": stages, "open_pipeline_value_vnd": open_value, "open_pipeline_count": open_count}


@router.get("/opportunities/{opportunity_id}")
async def get_opportunity(opportunity_id: uuid.UUID, user: service.CurrentUser = Depends(READ),
                          db: AsyncSession = DB) -> dict[str, Any]:
    sc = await scope_for(db, user, "opportunity.read")
    scope_sql, scope_params = _opp_scope_sql(sc)
    r = (await db.execute(text(_opp_select(user.role_code) + " WHERE o.id = :i AND o.org_id = :o AND " + scope_sql),  # noqa: S608
                          {"i": opportunity_id, "o": user.org_id, **scope_params})).one_or_none()
    if r is None:
        raise not_found("Cơ hội")
    owner = user.role_code == rbac.OWNER
    hist = (await db.execute(text("""SELECT from_stage, to_stage, actor, at FROM biz.opportunity_stage_history
                                     WHERE opportunity_id = :i ORDER BY at DESC"""),
                             {"i": opportunity_id})).all()
    item = _opp_item(r, owner=owner)
    item["stage_history"] = [{"from_stage": h.from_stage, "to_stage": h.to_stage, "actor": h.actor,
                              "at": iso(h.at)} for h in hist]
    return item


class OpportunityIn(BaseModel):
    person_id: uuid.UUID
    need: str = Field(min_length=1, max_length=500)
    value_vnd: int | None = Field(default=None, ge=0)
    confidence: Literal["high", "medium", "low"] = "medium"


@router.post("/opportunities", status_code=201)
async def create_opportunity(body: OpportunityIn, user: service.CurrentUser = Depends(WRITE),
                             db: AsyncSession = DB) -> dict[str, Any]:
    """Mở cơ hội tay (ngoài luồng tự động từ tín hiệu — vd nhân viên biết tin ngoài chat)."""
    sc = await scope_for(db, user, "opportunity.write")
    await ensure_person(db, sc, body.person_id)
    code = (await db.execute(text("SELECT core.next_code('OPP')"))).scalar_one()
    row = (await db.execute(text("""
        INSERT INTO biz.opportunities (org_id, code, person_id, need, stage, value_vnd, confidence,
                                       owner_user_id, first_signal_at)
        VALUES (:o, :c, :p, :n, 'raw_signal', :v, :conf, :u, now()) RETURNING id"""),
        {"o": user.org_id, "c": code, "p": body.person_id, "n": body.need, "v": body.value_vnd,
         "conf": body.confidence, "u": user.id})).one()
    await db.execute(text("""INSERT INTO biz.opportunity_stage_history (opportunity_id, from_stage, to_stage,
                             actor) VALUES (:i, NULL, 'raw_signal', :a)"""),
                     {"i": row.id, "a": user.actor_id})
    await actionlog.record(db, org_id=user.org_id, actor_type="user", actor_id=user.actor_id,
                           action="opportunity.created", target_type="opportunity", target_id=str(row.id),
                           target_label=f"{code} · {body.need}", result="ok", ip=user.ip)
    return await get_opportunity(row.id, user, db)


class StageIn(BaseModel):
    to_stage: str


@router.patch("/opportunities/{opportunity_id}/stage")
async def change_stage(opportunity_id: uuid.UUID, body: StageIn, user: service.CurrentUser = Depends(WRITE),
                       db: AsyncSession = DB) -> dict[str, Any]:
    """Kéo thả đổi giai đoạn — ghi `biz.opportunity_stage_history` (from/to/actor/at, PLAN §3.8)."""
    if body.to_stage not in STAGES:
        raise field_errors({"to_stage": f"Chỉ nhận một trong: {', '.join(STAGES)}"})
    sc = await scope_for(db, user, "opportunity.write")
    scope_sql, scope_params = _opp_scope_sql(sc)
    r = (await db.execute(text(f"""SELECT id, stage, first_contact_at FROM biz.opportunities o
                                   WHERE id = :i AND org_id = :o AND {scope_sql}"""),  # noqa: S608
                          {"i": opportunity_id, "o": user.org_id, **scope_params})).one_or_none()
    if r is None:
        raise not_found("Cơ hội")
    from_stage = r.stage
    if from_stage == body.to_stage:
        return await get_opportunity(opportunity_id, user, db)
    first_contact = r.first_contact_at
    if first_contact is None and from_stage == "raw_signal" and body.to_stage != "raw_signal":
        first_contact = datetime.now(UTC)
    closed_at = "now()" if body.to_stage in CLOSED_STAGES else "NULL"
    await db.execute(text(f"""UPDATE biz.opportunities SET stage = :s, updated_at = now(),
                             first_contact_at = COALESCE(first_contact_at, :fc), closed_at = {closed_at}
                             WHERE id = :i"""),  # noqa: S608 — closed_at là hằng nội bộ, không do người dùng gửi
                     {"s": body.to_stage, "fc": first_contact, "i": opportunity_id})
    await db.execute(text("""INSERT INTO biz.opportunity_stage_history (opportunity_id, from_stage, to_stage,
                             actor) VALUES (:i, :fs, :ts, :a)"""),
                     {"i": opportunity_id, "fs": from_stage, "ts": body.to_stage, "a": user.actor_id})
    await actionlog.record(db, org_id=user.org_id, actor_type="user", actor_id=user.actor_id,
                           action="opportunity.stage_changed", target_type="opportunity",
                           target_id=str(opportunity_id), result="ok",
                           detail={"from": from_stage, "to": body.to_stage}, ip=user.ip)
    return await get_opportunity(opportunity_id, user, db)


async def _explain_opportunity(db: AsyncSession, user: service.CurrentUser, sc: Scope, id: str) -> dict[str, Any]:
    try:
        oid = uuid.UUID(id)
    except ValueError as e:
        raise not_found("Cơ hội") from e
    scope_sql, scope_params = _opp_scope_sql(sc)
    r = (await db.execute(text(f"""SELECT id, code, need FROM biz.opportunities o
                                   WHERE id = :i AND org_id = :o AND {scope_sql}"""),  # noqa: S608
                          {"i": oid, "o": user.org_id, **scope_params})).one_or_none()
    if r is None:
        raise not_found("Cơ hội")
    owner = user.role_code == rbac.OWNER
    return explain.payload("opportunity", id, f"{r.code} · {mask_text(r.need, owner)}", "Bảng cơ hội",
                           method="rules+model")


explain.register("opportunity", "opportunity.read", _explain_opportunity)


# ═══ Cung ↔ Cầu ═══════════════════════════════════════════════════════════════

_SIGNAL_SELECT = """
SELECT s.id, s.side, s.item, s.category, s.quantity, s.unit, s.value_vnd, s.location, s.needed_by, s.heat,
       s.status, s.created_at, s.meaning_unit_id,
       p.id AS p_id, p.code AS p_code, p.display_name AS p_name, p.person_type AS p_type,
       p.organization_name AS p_org,
       g.id AS g_id, g.code AS g_code, g.name AS g_name, gc.type AS g_channel
FROM biz.market_signals s
LEFT JOIN core.persons p ON p.id = s.person_id
LEFT JOIN core.groups g ON g.id = s.group_id
LEFT JOIN core.channels gc ON gc.id = g.channel_id
"""


def _signal_item(r: Any, *, owner: bool) -> dict[str, Any]:
    return {"id": str(r.id), "side": r.side, "item": mask_text(r.item, owner), "category": r.category,
            "quantity": float(r.quantity) if r.quantity is not None else None, "unit": r.unit,
            "value_vnd": int(r.value_vnd) if r.value_vnd is not None else None, "location": r.location,
            "needed_by": r.needed_by.isoformat() if r.needed_by else None,
            "heat": float(r.heat) if r.heat is not None else None, "status": r.status,
            "created_at": iso(r.created_at), "person": msvc.person_ref(r, "p"), "group": msvc.group_ref(r, "g")}


@router.get("/supply")
async def list_supply(side: Literal["demand", "supply"] | None = None,
                      status: Literal["open", "matched", "closed", "ignored"] | None = None,
                      category: str | None = None, cursor: str | None = None,
                      limit: int = Query(50, ge=1, le=200), user: service.CurrentUser = Depends(READ),
                      db: AsyncSession = DB) -> dict[str, Any]:
    sc = await scope_for(db, user, "opportunity.read")
    scope_sql, scope_params = person_or_group_scope_sql(sc, "s.person_id", "s.group_id")
    conds = ["s.org_id = :o", scope_sql]
    params: dict[str, Any] = {"o": user.org_id, **scope_params}
    if side:
        conds.append("s.side = :side")
        params["side"] = side
    if status:
        conds.append("s.status = :status")
        params["status"] = status
    if category:
        conds.append("s.category = :cat")
        params["cat"] = category
    if cursor:
        conds.append("s.created_at < :c")
        params["c"] = parse_cursor(cursor)
    where = " AND ".join(conds)
    total = (await db.execute(text(f"SELECT count(*) FROM biz.market_signals s WHERE {where}"), params)  # noqa: S608
             ).scalar_one()
    rows = (await db.execute(text(_SIGNAL_SELECT + f" WHERE {where} ORDER BY s.created_at DESC LIMIT :n"),  # noqa: S608
                             {**params, "n": limit + 1})).all()
    owner = user.role_code == rbac.OWNER
    items = [_signal_item(r, owner=owner) for r in rows[:limit]]
    return {"items": items, "next_cursor": iso(rows[limit - 1].created_at) if len(rows) > limit else None,
            "total": total}


async def _signal_or_404(db: AsyncSession, sc: Scope, org_id: uuid.UUID, signal_id: uuid.UUID) -> Any:
    scope_sql, scope_params = person_or_group_scope_sql(sc, "s.person_id", "s.group_id")
    r = (await db.execute(text(_SIGNAL_SELECT + f" WHERE s.id = :i AND s.org_id = :o AND {scope_sql}"),  # noqa: S608
                          {"i": signal_id, "o": org_id, **scope_params})).one_or_none()
    if r is None:
        raise not_found("Tín hiệu")
    return r


@router.get("/supply/{signal_id}")
async def get_supply(signal_id: uuid.UUID, user: service.CurrentUser = Depends(READ),
                     db: AsyncSession = DB) -> dict[str, Any]:
    sc = await scope_for(db, user, "opportunity.read")
    r = await _signal_or_404(db, sc, user.org_id, signal_id)
    owner = user.role_code == rbac.OWNER
    other_side = "supply_id" if r.side == "demand" else "demand_id"
    same_side = "demand_id" if r.side == "demand" else "supply_id"
    og_view = group_view_sql(user.role_code, "os.group_id")      # bên kia nằm trong nhóm «Chỉ Sếp» ⇒ ẩn (v0.1.71)
    other_gate = "TRUE" if og_view == "TRUE" else f"(os.group_id IS NULL OR {og_view})"
    matches = (await db.execute(text(f"""
        SELECT m.id, m.score, m.reasons, m.status, os.item AS other_item, os.side AS other_side,
               op.id AS op_id, op.code AS op_code, op.display_name AS op_name, op.person_type AS op_type,
               og.id AS og_id, og.code AS og_code, og.name AS og_name
        FROM biz.matches m JOIN biz.market_signals os ON os.id = m.{other_side}
        LEFT JOIN core.persons op ON op.id = os.person_id LEFT JOIN core.groups og ON og.id = os.group_id
        WHERE m.{same_side} = :i AND {other_gate} ORDER BY m.score DESC LIMIT 50"""),  # noqa: S608 — hằng nội bộ
        {"i": signal_id})).all()
    item = _signal_item(r, owner=owner)
    item["matches"] = [{"id": str(m.id), "score": float(m.score), "reasons": list(m.reasons or []),
                        "status": m.status, "item": mask_text(m.other_item, owner),
                        "person": msvc.person_ref(m, "op"), "group": msvc.group_ref(m, "og")} for m in matches]
    return item


_MATCH_SELECT = """
SELECT m.id, m.score, m.reasons, m.status, m.opportunity_id, m.created_at, m.updated_at,
       d.id AS d_id, d.item AS d_item, d.person_id AS d_person_id, d.group_id AS d_group_id,
       dp.id AS dp_id, dp.code AS dp_code, dp.display_name AS dp_name,
       dg.id AS dg_id, dg.code AS dg_code, dg.name AS dg_name,
       s.id AS s_id, s.item AS s_item, s.person_id AS s_person_id, s.group_id AS s_group_id,
       s.quantity AS s_quantity, s.unit AS s_unit, s.value_vnd AS s_value_vnd, s.location AS s_location,
       sp.id AS sp_id, sp.code AS sp_code, sp.display_name AS sp_name,
       sg.id AS sg_id, sg.code AS sg_code, sg.name AS sg_name
FROM biz.matches m
JOIN biz.market_signals d ON d.id = m.demand_id JOIN biz.market_signals s ON s.id = m.supply_id
LEFT JOIN core.persons dp ON dp.id = d.person_id LEFT JOIN core.groups dg ON dg.id = d.group_id
LEFT JOIN core.persons sp ON sp.id = s.person_id LEFT JOIN core.groups sg ON sg.id = s.group_id
"""


def _match_item(r: Any, *, owner: bool) -> dict[str, Any]:
    return {"id": str(r.id), "score": float(r.score), "reasons": list(r.reasons or []), "status": r.status,
            "opportunity_id": str(r.opportunity_id) if r.opportunity_id else None, "created_at": iso(r.created_at),
            "demand": {"id": str(r.d_id), "item": mask_text(r.d_item, owner),
                      "person": msvc.person_ref(r, "dp"), "group": msvc.group_ref(r, "dg")},
            "supply": {"id": str(r.s_id), "item": mask_text(r.s_item, owner),
                      "person": msvc.person_ref(r, "sp"), "group": msvc.group_ref(r, "sg")}}


def _supply_gate(sc: Scope) -> str:
    """Phía cung của gợi ý ghép nằm trong nhóm «Chỉ Sếp» ⇒ ẩn cả gợi ý với vai khác (v0.1.71)."""
    gv = group_view_sql(sc.role, "s.group_id")
    return "TRUE" if gv == "TRUE" else f"(s.group_id IS NULL OR {gv})"


@router.get("/matches")
async def list_matches(status: Literal["suggested", "introduced", "accepted", "rejected"] | None = None,
                       min_score: float = 0.0, cursor: str | None = None,
                       limit: int = Query(50, ge=1, le=200), user: service.CurrentUser = Depends(READ),
                       db: AsyncSession = DB) -> dict[str, Any]:
    """Danh sách gợi ý ghép — phạm vi theo phía **cầu** (khách hàng của mình, `docs/api/phase-3-market.md`)."""
    sc = await scope_for(db, user, "opportunity.read")
    scope_sql, scope_params = person_or_group_scope_sql(sc, "d.person_id", "d.group_id")
    conds = ["d.org_id = :o", scope_sql, _supply_gate(sc), "m.score >= :min_score"]
    params: dict[str, Any] = {"o": user.org_id, "min_score": min_score, **scope_params}
    if status:
        conds.append("m.status = :status")
        params["status"] = status
    if cursor:
        conds.append("m.created_at < :c")
        params["c"] = parse_cursor(cursor)
    where = " AND ".join(conds)
    total = (await db.execute(text(f"""SELECT count(*) FROM biz.matches m
                                       JOIN biz.market_signals d ON d.id = m.demand_id
                                       WHERE {where}"""), params)).scalar_one()  # noqa: S608
    rows = (await db.execute(text(_MATCH_SELECT + f" WHERE {where} ORDER BY m.created_at DESC LIMIT :n"),  # noqa: S608
                             {**params, "n": limit + 1})).all()
    owner = user.role_code == rbac.OWNER
    items = [_match_item(r, owner=owner) for r in rows[:limit]]
    return {"items": items, "next_cursor": iso(rows[limit - 1].created_at) if len(rows) > limit else None,
            "total": total}


@router.post("/matches/recompute")
async def matches_recompute_now(user: service.CurrentUser = Depends(WRITE), db: AsyncSession = DB) -> dict[str, Any]:
    """Chấm lại điểm ghép ngay theo yêu cầu (ngoài lịch chạy nền `gh.biz.market.jobs.matches_recompute` mỗi 15
    phút) — cùng cách `POST /graph/recompute` của cụm Bản đồ quan hệ."""
    n = await recompute_matches_org(db, user.org_id)
    await actionlog.record(db, org_id=user.org_id, actor_type="user", actor_id=user.actor_id,
                           action="matches.recomputed", target_type="matches", target_id="all", result="ok",
                           detail={"matches": n}, ip=user.ip)
    return {"ok": True, "matches": n}


async def _match_or_404(db: AsyncSession, sc: Scope, org_id: uuid.UUID, match_id: uuid.UUID) -> Any:
    scope_sql, scope_params = person_or_group_scope_sql(sc, "d.person_id", "d.group_id")
    where = f"m.id = :i AND d.org_id = :o AND {scope_sql} AND {_supply_gate(sc)}"
    r = (await db.execute(text(_MATCH_SELECT + f" WHERE {where}"),  # noqa: S608
                          {"i": match_id, "o": org_id, **scope_params})).one_or_none()
    if r is None:
        raise not_found("Gợi ý ghép")
    return r


@router.post("/matches/{match_id}/introduce")
async def introduce_match(match_id: uuid.UUID, user: service.CurrentUser = Depends(WRITE),
                          db: AsyncSession = DB) -> dict[str, Any]:
    """"Giới thiệu hai bên": tạo bản nháp tin nhắn giới thiệu tín hiệu cung cho người bên cầu (chờ duyệt ở Bàn
    làm việc — luật cứng Q2, mọi tin ra ngoài đều dừng ở đây), mở (hoặc gắn vào) một cơ hội ở giai đoạn
    `matched`, và chuyển `matches.status` → `introduced`. Quyết định tự đưa ra: hướng giới thiệu là cầu→cung
    (báo cho khách đang cần biết có nguồn cung phù hợp) vì đó là chiều có người liên hệ trong hệ thống của
    mình; không có kênh xác định cho phía cung → vẫn tạo bản nháp loại `report` (nội bộ) để không chặn luồng."""
    sc = await scope_for(db, user, "opportunity.write")
    r = await _match_or_404(db, sc, user.org_id, match_id)
    if r.status != "suggested":
        raise ApiError(409, "MATCH_DECIDED", "Gợi ý ghép này đã được quyết định")
    opp_id = r.opportunity_id
    if opp_id is None:
        if r.d_person_id is None:
            raise ApiError(422, "VALIDATION", "Tín hiệu cầu chưa gắn với người — không mở được cơ hội")
        code = (await db.execute(text("SELECT core.next_code('OPP')"))).scalar_one()
        opp = (await db.execute(text("""
            INSERT INTO biz.opportunities (org_id, code, person_id, source_group_id, need, stage, value_vnd,
                                           confidence, owner_user_id, first_signal_at, first_contact_at, attrs)
            VALUES (:o, :c, :p, :g, :need, 'matched', NULL, 'medium', :u, now(), now(), CAST(:attrs AS jsonb))
            RETURNING id"""),
            {"o": user.org_id, "c": code, "p": r.d_person_id, "g": r.d_group_id, "need": r.d_item, "u": user.id,
             "attrs": orjson.dumps({"demand_signal_id": str(r.d_id), "match_id": str(match_id)}).decode()}
            )).one()
        opp_id = opp.id
        await db.execute(text("""INSERT INTO biz.opportunity_stage_history (opportunity_id, from_stage, to_stage,
                                 actor) VALUES (:i, NULL, 'matched', :a)"""), {"i": opp_id, "a": user.actor_id})
    else:
        row = (await db.execute(text("SELECT stage FROM biz.opportunities WHERE id = :i"), {"i": opp_id})
              ).one_or_none()
        if row is not None and row.stage in ("raw_signal", "validated"):
            await db.execute(text("""UPDATE biz.opportunities SET stage = 'matched', updated_at = now()
                                     WHERE id = :i"""), {"i": opp_id})
            await db.execute(text("""INSERT INTO biz.opportunity_stage_history (opportunity_id, from_stage,
                                     to_stage, actor) VALUES (:i, :fs, 'matched', :a)"""),
                             {"i": opp_id, "fs": row.stage, "a": user.actor_id})
    await db.execute(text("""UPDATE biz.matches SET status = 'introduced', opportunity_id = :opp, updated_at = now()
                             WHERE id = :i"""), {"opp": opp_id, "i": match_id})

    person_id = r.d_person_id
    target = None
    if person_id is not None:
        chan = (await db.execute(text("""SELECT c.type AS channel, pi.person_id FROM core.person_identities pi
                                         JOIN core.channels c ON c.id = pi.channel_id
                                         WHERE pi.person_id = :p ORDER BY pi.first_seen_at LIMIT 1"""),
                                 {"p": person_id})).one_or_none()
        if chan is not None:
            target = Target(channel=chan.channel, thread_type="user", person_id=person_id)
    offer = msvc.supply_line(r.s_item, r.s_quantity, r.s_unit, r.s_value_vnd, r.s_location)
    text_body = (f"Chào anh/chị, bên em có nguồn cung phù hợp với nhu cầu \"{r.d_item}\":\n\n{offer}\n\n"
                f"Anh/chị có muốn em gửi thêm chi tiết không ạ?")
    sources = [{"label": f"Tín hiệu cầu · {r.d_item}", "ref": {"type": "opportunity", "id": str(opp_id)}}]
    result = await create_draft(db, org_id=user.org_id, kind="message" if target else "report",
                                title=f"Giới thiệu nguồn cung cho {r.dp_name or r.dg_name or 'khách'}",
                                body_text=text_body, target=target, created_by=user.id,
                                subject=("person", person_id) if person_id else None, sources=sources,
                                amount_vnd=None)
    await actionlog.record(db, org_id=user.org_id, actor_type="user", actor_id=user.actor_id,
                           action="match.introduced", target_type="match", target_id=str(match_id), result="ok",
                           detail={"opportunity_id": str(opp_id), "draft_id": str(result["id"]) if result["id"]
                                  else None}, ip=user.ip)
    return {"ok": True, "opportunity_id": str(opp_id), "draft": result}


@router.post("/matches/{match_id}/reject")
async def reject_match(match_id: uuid.UUID, user: service.CurrentUser = Depends(WRITE),
                       db: AsyncSession = DB) -> dict[str, Any]:
    sc = await scope_for(db, user, "opportunity.write")
    r = await _match_or_404(db, sc, user.org_id, match_id)
    if r.status != "suggested":
        raise ApiError(409, "MATCH_DECIDED", "Gợi ý ghép này đã được quyết định")
    await db.execute(text("UPDATE biz.matches SET status = 'rejected', updated_at = now() WHERE id = :i"),
                     {"i": match_id})
    await actionlog.record(db, org_id=user.org_id, actor_type="user", actor_id=user.actor_id,
                           action="match.rejected", target_type="match", target_id=str(match_id), result="ok",
                           ip=user.ip)
    return {"ok": True}


# ═══ Kho hội thoại ════════════════════════════════════════════════════════════

SEMANTIC_CANDIDATES = 80   # top-K đơn vị gần nghĩa nhất đưa vào gộp cùng kết quả từ khoá


async def _semantic_unit_ids(request: Request, db: AsyncSession, org_id: uuid.UUID, q: str, where: str,
                             params: dict[str, Any]) -> list[uuid.UUID]:
    """Ngữ nghĩa: embedding câu tìm rồi xếp theo khoảng cách cosine với `clean.meaning_units.embedding` (đã
    sinh sẵn từ giai đoạn 2, `gh.refinery.runner._embed`). Không có model embedding, hoặc lỗi gọi model → trả
    rỗng, im lặng quay về chỉ từ khoá (không chặn tìm kiếm — cùng nguyên tắc "embedding không được làm hỏng"
    mà `_embed` đã áp dụng ở sàng lọc)."""
    router_ = getattr(request.app.state, "model_router", None)
    if router_ is None:
        return []
    try:
        vecs = await router_.embed(org_id, [q])
    except Exception:  # noqa: BLE001 — model lỗi không được chặn tìm kiếm từ khoá
        return []
    if not vecs:
        return []
    qvec = "[" + ",".join(f"{x:.6f}" for x in vecs[0]) + "]"
    rows = (await db.execute(text(f"""
        SELECT mu.id FROM clean.meaning_units mu
        LEFT JOIN core.groups g ON g.id = mu.group_id LEFT JOIN core.channels gc ON gc.id = g.channel_id
        WHERE {where} AND mu.embedding IS NOT NULL
        ORDER BY mu.embedding <=> CAST(:qvec AS vector) LIMIT :n"""),  # noqa: S608
        {**params, "qvec": qvec, "n": SEMANTIC_CANDIDATES})).all()
    return [r.id for r in rows]


@router.get("/search")
async def search_conversations(request: Request, q: str | None = None, event_type: str | None = None,
                               channel: str | None = None, date_from: str | None = None, date_to: str | None = None,
                               cursor: str | None = None, limit: int = Query(20, ge=1, le=100),
                               user: service.CurrentUser = Depends(READ), db: AsyncSession = DB) -> dict[str, Any]:
    """Tìm ngôn ngữ tự nhiên (từ khoá trên kết luận + thực thể) + facet (loại sự kiện, kênh, thời gian) + ngữ
    nghĩa (`_semantic_unit_ids`). Kết quả **là người**: gộp các đơn vị khớp theo người, mới nhất trước; đơn vị
    không gắn với người nào bị bỏ qua (quyết định tự đưa ra — màn này hiển thị "danh sách người", một đơn vị
    chỉ thuộc về nhóm không dựng được một dòng người)."""
    sc = await scope_for(db, user, "opportunity.read")
    scope_sql, scope_params = person_or_group_scope_sql(sc, "mu.person_id", "mu.group_id")
    dm_sql, dm_params = direct_visible_sql(user, "mu.group_id", "mu.person_id")   # tin 1-1: chỉ Owner/người được giao
    base_conds = ["mu.org_id = :o", "mu.superseded_by IS NULL", "mu.person_id IS NOT NULL", scope_sql, dm_sql]
    params: dict[str, Any] = {"o": user.org_id, **scope_params, **dm_params}
    if event_type:
        base_conds.append("mu.event_type = :et")
        params["et"] = event_type
    if channel:
        base_conds.append("gc.type = :ch")
        params["ch"] = channel
    if date_from:
        base_conds.append("mu.observed_at >= :df")
        params["df"] = parse_cursor(date_from)
    if date_to:
        base_conds.append("mu.observed_at <= :dt")
        params["dt"] = parse_cursor(date_to)
    base_where = " AND ".join(base_conds)

    facet_rows = (await db.execute(text(f"""
        SELECT mu.event_type, gc.type AS channel, count(*) AS n FROM clean.meaning_units mu
        LEFT JOIN core.groups g ON g.id = mu.group_id LEFT JOIN core.channels gc ON gc.id = g.channel_id
        WHERE {base_where} GROUP BY mu.event_type, gc.type"""), params)).all()  # noqa: S608
    event_facet: dict[str, int] = {}
    channel_facet: dict[str, int] = {}
    for f in facet_rows:
        if f.event_type:
            event_facet[f.event_type] = event_facet.get(f.event_type, 0) + f.n
        if f.channel:
            channel_facet[f.channel] = channel_facet.get(f.channel, 0) + f.n

    if q:
        kw_where = base_where + " AND (mu.conclusion ILIKE :q OR mu.entities::text ILIKE :q)"
        kw_params = {**params, "q": f"%{q}%"}
        keyword_rows = (await db.execute(text(f"""
            SELECT mu.id FROM clean.meaning_units mu
            LEFT JOIN core.groups g ON g.id = mu.group_id LEFT JOIN core.channels gc ON gc.id = g.channel_id
            WHERE {kw_where} ORDER BY mu.observed_at DESC LIMIT 500"""), kw_params)).all()  # noqa: S608
        semantic_ids = await _semantic_unit_ids(request, db, user.org_id, q, base_where, params)
        unit_ids = list(dict.fromkeys([r.id for r in keyword_rows] + semantic_ids))
    else:
        recent = (await db.execute(text(f"""
            SELECT mu.id FROM clean.meaning_units mu
            LEFT JOIN core.groups g ON g.id = mu.group_id LEFT JOIN core.channels gc ON gc.id = g.channel_id
            WHERE {base_where} ORDER BY mu.observed_at DESC LIMIT 500"""), params)).all()  # noqa: S608
        unit_ids = [r.id for r in recent]

    person_rows = [] if not unit_ids else (await db.execute(text("""
        SELECT p.id, p.code, p.display_name, p.person_type, p.organization_name,
               count(*) AS match_count, max(mu.observed_at) AS last_at,
               (array_agg(mu.id ORDER BY mu.observed_at DESC))[1] AS last_unit_id,
               (array_agg(mu.conclusion ORDER BY mu.observed_at DESC))[1] AS last_conclusion,
               (array_agg(mu.event_type ORDER BY mu.observed_at DESC))[1] AS last_event_type
        FROM clean.meaning_units mu JOIN core.persons p ON p.id = mu.person_id
        WHERE mu.id = ANY(CAST(:ids AS uuid[])) AND p.deleted_at IS NULL AND p.merged_into_id IS NULL
        GROUP BY p.id, p.code, p.display_name, p.person_type, p.organization_name
        ORDER BY max(mu.observed_at) DESC LIMIT :n"""),
        {"ids": unit_ids, "n": limit + 1})).all()
    owner = user.role_code == rbac.OWNER
    items = [{"person": {"id": str(r.id), "code": r.code, "name": mask_text(r.display_name, owner),
                        "type": r.person_type, "org_name": r.organization_name},
             "match_count": r.match_count, "last_at": iso(r.last_at),
             "last_snippet": mask_text(r.last_conclusion, owner), "last_event_type": r.last_event_type,
             "evidence": {"type": "meaning_unit", "id": str(r.last_unit_id)}} for r in person_rows[:limit]]
    return {"items": items, "next_cursor": iso(person_rows[limit - 1].last_at) if len(person_rows) > limit else None,
            "total": len(items), "facets": {
                "event_type": [{"value": k, "count": v} for k, v in sorted(event_facet.items(),
                                                                           key=lambda kv: -kv[1])],
                "channel": [{"value": k, "count": v} for k, v in sorted(channel_facet.items(),
                                                                        key=lambda kv: -kv[1])]}}


class BulkActionIn(BaseModel):
    person_ids: list[uuid.UUID] = Field(min_length=1, max_length=200)
    action: Literal["tag", "task"]
    text: str = Field(min_length=1, max_length=2000)
    due_at: str | None = None
    priority: str = "P3"


@router.post("/search/bulk")
async def search_bulk_action(body: BulkActionIn, user: service.CurrentUser = Depends(WRITE),
                             db: AsyncSession = DB) -> dict[str, Any]:
    """Hành động hàng loạt trên kết quả Kho hội thoại — "gắn nhãn" (ghi sổ tay) và "giao việc" (`biz.tasks`) là
    việc **nội bộ**, không ghi ra ngoài, nên làm trực tiếp (không qua Bàn làm việc) qua đúng cơ chế hành động
    nội bộ đã có (`gh.biz.core.drafts._execute_internal`) — cùng chữ ký spec đã dùng cho "Tạo kèm theo" của bản
    nháp (docs/api/phase-3.md §Bàn làm việc)."""
    sc = await scope_for(db, user, "opportunity.write")
    action_key = "note.write" if body.action == "tag" else "task.create"
    params = {"section": "rolling_context"} if body.action == "tag" else {
        "due_at": body.due_at, "priority": body.priority}
    done = 0
    for pid in body.person_ids:
        await ensure_person(db, sc, pid)
        await _execute_internal(db, user.org_id, None, action_key, body.text, ("person", pid), user.id, params)
        done += 1
    await actionlog.record(db, org_id=user.org_id, actor_type="user", actor_id=user.actor_id,
                           action="search.bulk_action", target_type="person", target_id=None, result="ok",
                           detail={"action": body.action, "count": done}, ip=user.ip)
    return {"ok": True, "count": done}


# ═══ Deal & Vụ việc ═══════════════════════════════════════════════════════════

_DEAL_SELECT = """
SELECT d.id, d.code, d.opportunity_id, d.person_id, d.amount_vnd, d.status, d.won_at, d.erp_ref, d.created_at,
       d.updated_at, p.id AS p_id, p.code AS p_code, p.display_name AS p_name, p.person_type AS p_type,
       p.organization_name AS p_org
FROM biz.deals d LEFT JOIN core.persons p ON p.id = d.person_id
"""


def _deal_item(r: Any, *, owner: bool) -> dict[str, Any]:
    return {"id": str(r.id), "code": r.code, "opportunity_id": str(r.opportunity_id) if r.opportunity_id else None,
            "person": msvc.person_ref(r, "p"), "amount_vnd": int(r.amount_vnd), "status": r.status,
            "won_at": iso(r.won_at), "erp_ref": r.erp_ref if owner else mask_text(r.erp_ref, owner),
            "created_at": iso(r.created_at), "updated_at": iso(r.updated_at)}


@router.get("/deals")
async def list_deals(status: Literal["open", "won", "lost"] | None = None, person_id: uuid.UUID | None = None,
                     cursor: str | None = None, limit: int = Query(50, ge=1, le=200),
                     user: service.CurrentUser = Depends(READ), db: AsyncSession = DB) -> dict[str, Any]:
    sc = await scope_for(db, user, "opportunity.read")
    scope_sql, scope_params = sc.person_id_sql("d.person_id")
    conds = ["d.org_id = :o", scope_sql]
    params: dict[str, Any] = {"o": user.org_id, **scope_params}
    if status:
        conds.append("d.status = :status")
        params["status"] = status
    if person_id:
        conds.append("d.person_id = :pid")
        params["pid"] = person_id
    if cursor:
        conds.append("d.created_at < :c")
        params["c"] = parse_cursor(cursor)
    where = " AND ".join(conds)
    total = (await db.execute(text(f"SELECT count(*) FROM biz.deals d WHERE {where}"), params)).scalar_one()  # noqa: S608
    rows = (await db.execute(text(_DEAL_SELECT + f" WHERE {where} ORDER BY d.created_at DESC LIMIT :n"),  # noqa: S608
                             {**params, "n": limit + 1})).all()
    owner = user.role_code == rbac.OWNER
    items = [_deal_item(r, owner=owner) for r in rows[:limit]]
    return {"items": items, "next_cursor": iso(rows[limit - 1].created_at) if len(rows) > limit else None,
            "total": total}


@router.get("/deals/{deal_id}")
async def get_deal(deal_id: uuid.UUID, user: service.CurrentUser = Depends(READ),
                   db: AsyncSession = DB) -> dict[str, Any]:
    sc = await scope_for(db, user, "opportunity.read")
    scope_sql, scope_params = sc.person_id_sql("d.person_id")
    r = (await db.execute(text(_DEAL_SELECT + f" WHERE d.id = :i AND d.org_id = :o AND {scope_sql}"),  # noqa: S608
                          {"i": deal_id, "o": user.org_id, **scope_params})).one_or_none()
    if r is None:
        raise not_found("Deal")
    return _deal_item(r, owner=user.role_code == rbac.OWNER)


class DealIn(BaseModel):
    person_id: uuid.UUID
    amount_vnd: int = Field(ge=0)
    opportunity_id: uuid.UUID | None = None
    erp_ref: str | None = None


@router.post("/deals", status_code=201)
async def create_deal(body: DealIn, user: service.CurrentUser = Depends(WRITE),
                      db: AsyncSession = DB) -> dict[str, Any]:
    sc = await scope_for(db, user, "opportunity.write")
    await ensure_person(db, sc, body.person_id)
    code = (await db.execute(text("SELECT core.next_code('DEA')"))).scalar_one()
    row = (await db.execute(text("""
        INSERT INTO biz.deals (org_id, code, opportunity_id, person_id, amount_vnd, status, erp_ref)
        VALUES (:o, :c, :opp, :p, :amt, 'open', :erp) RETURNING id"""),
        {"o": user.org_id, "c": code, "opp": body.opportunity_id, "p": body.person_id, "amt": body.amount_vnd,
         "erp": body.erp_ref})).one()
    await actionlog.record(db, org_id=user.org_id, actor_type="user", actor_id=user.actor_id,
                           action="deal.created", target_type="deal", target_id=str(row.id),
                           target_label=code, result="ok", ip=user.ip)
    return await get_deal(row.id, user, db)


class DealPatch(BaseModel):
    status: Literal["open", "won", "lost"] | None = None
    amount_vnd: int | None = Field(default=None, ge=0)
    erp_ref: str | None = None


@router.patch("/deals/{deal_id}")
async def patch_deal(deal_id: uuid.UUID, body: DealPatch, user: service.CurrentUser = Depends(WRITE),
                     db: AsyncSession = DB) -> dict[str, Any]:
    sc = await scope_for(db, user, "opportunity.write")
    scope_sql, scope_params = sc.person_id_sql("d.person_id")
    r = (await db.execute(text(f"SELECT id FROM biz.deals d WHERE id = :i AND org_id = :o AND {scope_sql}"),  # noqa: S608
                          {"i": deal_id, "o": user.org_id, **scope_params})).one_or_none()
    if r is None:
        raise not_found("Deal")
    fields = body.model_fields_set
    detail: dict[str, Any] = {}
    if "amount_vnd" in fields:
        await db.execute(text("UPDATE biz.deals SET amount_vnd = :v, updated_at = now() WHERE id = :i"),
                         {"v": body.amount_vnd, "i": deal_id})
        detail["amount_vnd"] = body.amount_vnd
    if "erp_ref" in fields:
        await db.execute(text("UPDATE biz.deals SET erp_ref = :v, updated_at = now() WHERE id = :i"),
                         {"v": body.erp_ref, "i": deal_id})
        detail["erp_ref"] = body.erp_ref
    if "status" in fields:
        won_at = "now()" if body.status == "won" else "NULL"
        await db.execute(text(f"""UPDATE biz.deals SET status = :s, won_at = {won_at}, updated_at = now()
                                 WHERE id = :i"""), {"s": body.status, "i": deal_id})  # noqa: S608
        detail["status"] = body.status
        if body.status in ("won", "lost"):
            opp = (await db.execute(text("SELECT opportunity_id FROM biz.deals WHERE id = :i"),
                                    {"i": deal_id})).scalar_one_or_none()
            if opp is not None:
                await db.execute(text("""UPDATE biz.opportunities SET stage = :s, closed_at = now(),
                                         updated_at = now() WHERE id = :i AND stage NOT IN ('won', 'lost')"""),
                                 {"s": body.status, "i": opp})
    if detail:
        await actionlog.record(db, org_id=user.org_id, actor_type="user", actor_id=user.actor_id,
                               action="deal.updated", target_type="deal", target_id=str(deal_id), result="ok",
                               detail=detail, ip=user.ip)
    return await get_deal(deal_id, user, db)


_CASE_SELECT = """
SELECT c.id, c.code, c.kind, c.priority, c.subject_type, c.subject_id, c.title, c.status, c.assignee_user_id,
       c.opened_at, c.resolved_at, c.updated_at,
       u.id AS u_id, u.display_name AS u_name, uro.code AS u_role,
       p.id AS p_id, p.code AS p_code, p.display_name AS p_name, p.person_type AS p_type,
       p.organization_name AS p_org,
       g.id AS g_id, g.code AS g_code, g.name AS g_name, gc.type AS g_channel
FROM biz.cases c
LEFT JOIN core.users u ON u.id = c.assignee_user_id
""" + msvc.USER_ROLE_JOIN.format(alias="u", out="uro") + """
LEFT JOIN core.persons p ON c.subject_type = 'person' AND p.id = c.subject_id
LEFT JOIN core.groups g ON c.subject_type = 'group' AND g.id = c.subject_id
LEFT JOIN core.channels gc ON gc.id = g.channel_id
"""


def _case_item(r: Any, *, owner: bool) -> dict[str, Any]:
    return {"id": str(r.id), "code": r.code, "kind": r.kind, "priority": r.priority, "title": mask_text(r.title,
            owner), "status": r.status, "assignee": msvc.user_ref(r.u_id, r.u_name, r.u_role),
            "subject": msvc.person_ref(r, "p") or msvc.group_ref(r, "g"), "opened_at": iso(r.opened_at),
            "resolved_at": iso(r.resolved_at), "updated_at": iso(r.updated_at)}


def _case_scope_sql(sc: Scope) -> tuple[str, dict[str, Any]]:
    """Vụ việc thấy được khi đối tượng nó gắn vào trong phạm vi, hoặc người xử lý là mình/team mình — cùng mẫu
    `biz.tasks` của cụm Hàng đợi (`docs/api/phase-3-queue.md`: "một việc thấy được khi… hoặc người phụ trách")."""
    if sc.is_all:       # vẫn theo «Quyền xem» của nhóm nếu vụ việc gắn với một nhóm (v0.1.71)
        return sc.subject_sql("c.subject_type", "c.subject_id")
    sw, sp = sc.subject_sql("c.subject_type", "c.subject_id")
    uw, up = sc.user_sql("c.assignee_user_id")
    gv = group_view_sql(sc.role, "c.subject_id")
    gate = "TRUE" if gv == "TRUE" else f"(c.subject_type IS DISTINCT FROM 'group' OR {gv})"
    return f"(({sw} OR {uw}) AND {gate})", {**sp, **up}


@router.get("/cases")
async def list_cases(status: str | None = None, assignee_user_id: uuid.UUID | None = None,
                     priority: str | None = None, cursor: str | None = None,
                     limit: int = Query(50, ge=1, le=200), user: service.CurrentUser = Depends(READ),
                     db: AsyncSession = DB) -> dict[str, Any]:
    """Chỉ `kind = 'complaint'` ("Vụ việc" của cụm này) — `kind IN ('alert','system')` thuộc luồng khác (cảnh
    báo sớm ghi thẳng `biz.alerts`, không dùng `biz.cases`), không hiện ở đây."""
    sc = await scope_for(db, user, "opportunity.read")
    scope_sql, scope_params = _case_scope_sql(sc)
    conds = ["c.org_id = :o", "c.kind = 'complaint'", scope_sql]
    params: dict[str, Any] = {"o": user.org_id, **scope_params}
    if status:
        conds.append("c.status = :status")
        params["status"] = status
    if assignee_user_id:
        conds.append("c.assignee_user_id = :a")
        params["a"] = assignee_user_id
    if priority:
        conds.append("c.priority = :p")
        params["p"] = priority
    if cursor:
        conds.append("c.opened_at < :c")
        params["c"] = parse_cursor(cursor)
    where = " AND ".join(conds)
    total = (await db.execute(text(f"SELECT count(*) FROM biz.cases c WHERE {where}"), params)).scalar_one()  # noqa: S608
    rows = (await db.execute(text(_CASE_SELECT + f" WHERE {where} ORDER BY c.opened_at DESC LIMIT :n"),  # noqa: S608
                             {**params, "n": limit + 1})).all()
    owner = user.role_code == rbac.OWNER
    items = [_case_item(r, owner=owner) for r in rows[:limit]]
    return {"items": items, "next_cursor": iso(rows[limit - 1].opened_at) if len(rows) > limit else None,
            "total": total}


@router.get("/cases/{case_id}")
async def get_case(case_id: uuid.UUID, user: service.CurrentUser = Depends(READ),
                   db: AsyncSession = DB) -> dict[str, Any]:
    sc = await scope_for(db, user, "opportunity.read")
    scope_sql, scope_params = _case_scope_sql(sc)
    r = (await db.execute(text(_CASE_SELECT + f" WHERE c.id = :i AND c.org_id = :o AND c.kind = 'complaint' "
                               f"AND {scope_sql}"), {"i": case_id, "o": user.org_id, **scope_params})  # noqa: S608
        ).one_or_none()
    if r is None:
        raise not_found("Vụ việc")
    return _case_item(r, owner=user.role_code == rbac.OWNER)


class CaseIn(BaseModel):
    title: str = Field(min_length=1, max_length=500)
    priority: Literal["P1", "P2", "P3"] = "P2"
    subject: dict[str, Any] | None = None
    assignee_user_id: uuid.UUID | None = None


@router.post("/cases", status_code=201)
async def create_case(body: CaseIn, user: service.CurrentUser = Depends(WRITE),
                      db: AsyncSession = DB) -> dict[str, Any]:
    sc = await scope_for(db, user, "opportunity.write")
    subject_type = subject_id = None
    if body.subject:
        subject_type = body.subject.get("type")
        subject_id = body.subject.get("id")
        if subject_type not in ("person", "group") or not subject_id:
            raise field_errors({"subject": "Cần {\"type\": \"person|group\", \"id\": …}"})
        subject_id = uuid.UUID(str(subject_id))
        if subject_type == "person":
            await ensure_person(db, sc, subject_id)
        else:
            await ensure_group(db, sc, subject_id)
    await ensure_active_user(db, user.org_id, body.assignee_user_id)
    code = (await db.execute(text("SELECT core.next_code('CAS')"))).scalar_one()
    row = (await db.execute(text("""
        INSERT INTO biz.cases (org_id, code, kind, priority, subject_type, subject_id, title, status,
                               assignee_user_id)
        VALUES (:o, :c, 'complaint', :pr, :st, :si, :ti, 'open', :a) RETURNING id"""),
        {"o": user.org_id, "c": code, "pr": body.priority, "st": subject_type, "si": subject_id,
         "ti": body.title, "a": body.assignee_user_id})).one()
    await actionlog.record(db, org_id=user.org_id, actor_type="user", actor_id=user.actor_id,
                           action="case.created", target_type="case", target_id=str(row.id),
                           target_label=f"{code} · {body.title}", result="ok", ip=user.ip)
    return await get_case(row.id, user, db)


class CasePatch(BaseModel):
    status: Literal["open", "in_progress", "resolved", "closed"] | None = None
    assignee_user_id: uuid.UUID | None = None
    priority: Literal["P1", "P2", "P3"] | None = None


@router.patch("/cases/{case_id}")
async def patch_case(case_id: uuid.UUID, body: CasePatch, user: service.CurrentUser = Depends(WRITE),
                     db: AsyncSession = DB) -> dict[str, Any]:
    sc = await scope_for(db, user, "opportunity.write")
    scope_sql, scope_params = _case_scope_sql(sc)
    r = (await db.execute(text(f"SELECT id, status FROM biz.cases c WHERE id = :i AND org_id = :o AND "
                               f"kind = 'complaint' AND {scope_sql}"),  # noqa: S608
                          {"i": case_id, "o": user.org_id, **scope_params})).one_or_none()
    if r is None:
        raise not_found("Vụ việc")
    fields = body.model_fields_set
    detail: dict[str, Any] = {}
    if "assignee_user_id" in fields:
        await ensure_active_user(db, user.org_id, body.assignee_user_id)
        await db.execute(text("UPDATE biz.cases SET assignee_user_id = :a, updated_at = now() WHERE id = :i"),
                         {"a": body.assignee_user_id, "i": case_id})
        detail["assignee_user_id"] = str(body.assignee_user_id) if body.assignee_user_id else None
    if "priority" in fields:
        await db.execute(text("UPDATE biz.cases SET priority = :p, updated_at = now() WHERE id = :i"),
                         {"p": body.priority, "i": case_id})
        detail["priority"] = body.priority
    if "status" in fields:
        resolved = "now()" if body.status in ("resolved", "closed") else "NULL"
        await db.execute(text(f"""UPDATE biz.cases SET status = :s, resolved_at = {resolved}, updated_at = now()
                                 WHERE id = :i"""), {"s": body.status, "i": case_id})  # noqa: S608
        detail["status"] = body.status
    if detail:
        await actionlog.record(db, org_id=user.org_id, actor_type="user", actor_id=user.actor_id,
                               action="case.updated", target_type="case", target_id=str(case_id), result="ok",
                               detail=detail, ip=user.ip)
    return await get_case(case_id, user, db)
