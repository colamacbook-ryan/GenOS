"""API Quan hệ & Đối tượng (docs/api/phase-3-relations.md): Nhóm & Con người, Hồ sơ sống, Sổ tay nhận thức,
Tài liệu. Nền chung: `docs/api/phase-3.md` (PersonRef/GroupRef/Score/EvidenceRef, ScopeFilter, chứng cứ).

Sổ tay nhận thức: lõi ghi/nén đã có ở `gh.memory.notebook` (giai đoạn 2) — cụm này chỉ là lớp API đọc/ghim/
sửa/xoá/nén ngay/đặt lại, có kiểm phạm vi (`gh.biz.core.scope`), khác với `gh.data_api.routes` (kỹ thuật, chỉ
`data.read`/không lọc phạm vi theo người/nhóm) — hai lớp API cùng đọc một engine cho hai màn khác nhau.
"""

import base64
import mimetypes
import re
import urllib.parse
import uuid
from collections.abc import Sequence
from datetime import datetime
from typing import Any, Literal

import orjson
from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gh.auth import rbac, service
from gh.auth.deps import require, require_owner, require_pin
from gh.biz.core import explain
from gh.biz.core.scope import (
    Scope,
    direct_visible_sql,
    ensure_active_user,
    ensure_group,
    ensure_person,
    not_found,
    scope_for,
)
from gh.biz.relations import service as rsvc
from gh.chassis import actionlog
from gh.chassis.objects import ObjectNotFound, content_hash, get_object_store, new_key
from gh.data.common import iso, live_person, mask_text, parse_cursor
from gh.db import DB
from gh.errors import ApiError
from gh.memory import notebook

router = APIRouter(tags=["relations"])

READ = require("profile.read")
WRITE = require("profile.write")


# ═══ Nhóm & Con người (directory) ═══════════════════════════════════════════

@router.get("/directory/channels")
async def list_channels(user: service.CurrentUser = Depends(READ), db: AsyncSession = DB) -> list[dict[str, Any]]:
    rows = (await db.execute(text("""
        SELECT c.id, c.type, c.name,
               (SELECT s.state FROM core.channel_sessions s WHERE s.channel_id = c.id
                ORDER BY COALESCE(s.started_at, s.qr_issued_at) DESC NULLS LAST LIMIT 1) AS state,
               (SELECT count(*) FROM core.groups g WHERE g.channel_id = c.id) AS group_count,
               (SELECT count(*) FROM raw.events e WHERE e.channel_id = c.id
                AND e.occurred_at > now() - interval '24 hours') AS events_24h
        FROM core.channels c WHERE c.org_id = :o ORDER BY c.type"""), {"o": user.org_id})).all()
    return [{"id": str(r.id), "type": r.type, "name": r.name, "state": r.state, "group_count": r.group_count,
             "events_24h": r.events_24h} for r in rows]


# ── Con trỏ phân trang (created_at, id) + tìm tên ────────────────────────────
# Con trỏ chỉ theo created_at làm RƠI mọi dòng cùng thời điểm tạo (nhập danh bạ hàng loạt trong một giao dịch ⇒
# trang 2 trống). Khoá phụ `id` làm thứ tự toàn phần: `ORDER BY created_at DESC, id DESC` và so sánh theo cặp.
_MAX_UUID = uuid.UUID(int=(1 << 128) - 1)
_CURSOR_ERR = "Con trỏ phân trang không hợp lệ — hãy tải lại danh sách"


def _page_cursor(cursor: str | None) -> tuple[datetime, uuid.UUID] | None:
    """`<created_at ISO>|<id>`. Con trỏ kiểu cũ (chỉ thời điểm) vẫn nhận: id = lớn nhất ⇒ lấy mọi dòng cũ hơn hoặc
    bằng thời điểm đó (không bỏ sót dòng nào). Hỏng ⇒ 422 (không để thành 500)."""
    if not cursor:
        return None
    ts, _, rid = cursor.partition("|")
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")), (uuid.UUID(rid) if rid else _MAX_UUID)
    except ValueError:
        raise ApiError(422, "BAD_CURSOR", _CURSOR_ERR) from None


def _next_cursor(rows: Sequence[Any], limit: int) -> str | None:
    if len(rows) <= limit:
        return None
    last = rows[limit - 1]
    return f"{iso(last.created_at)}|{last.id}"


def _like_pattern(q: str) -> str:
    """Ký tự đặc biệt của LIKE (% _ \\) hiểu theo nghĩa đen — gõ "100%" tìm đúng "100%"."""
    esc = q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{esc}%"


_GROUP_SELECT = """
SELECT g.id, g.code, g.name, g.kind, g.listen_mode, g.member_count, g.created_at, g.added_by_kind,
       c.type AS channel_type, c.name AS channel_name, ag.id AS agent_id, ag.name AS agent_name,
       (SELECT count(*) FROM raw.events e WHERE e.group_id = g.id
        AND e.occurred_at > now() - interval '24 hours') AS events_24h,
       cs.value AS heat
FROM core.groups g
JOIN core.channels c ON c.id = g.channel_id
LEFT JOIN agent.identities ag ON ag.id = g.assigned_agent_id
LEFT JOIN clean.current_scores cs ON cs.subject_type = 'group' AND cs.subject_id = g.id AND cs.dimension = 'heat'
"""


def _group_payload(r: Any) -> dict[str, Any]:
    return {"id": str(r.id), "code": r.code, "name": r.name, "kind": r.kind, "listen_mode": r.listen_mode,
            "member_count": r.member_count, "events_24h": r.events_24h,
            "heat": float(r.heat) if r.heat is not None else None,
            "channel": {"type": r.channel_type, "name": r.channel_name},
            "bot": rsvc.agent_ref(r.agent_id, r.agent_name), "added_by_kind": r.added_by_kind,
            "created_at": iso(r.created_at)}


@router.get("/directory/groups")
async def list_groups(channel_id: uuid.UUID | None = None, kind: str | None = None, listen_mode: str | None = None,
                      q: str | None = Query(None, max_length=100), cursor: str | None = None,
                      limit: int = Query(50, ge=1, le=200),
                      user: service.CurrentUser = Depends(READ), db: AsyncSession = DB) -> dict[str, Any]:
    sc = await scope_for(db, user, "profile.read")
    where, params = sc.group_sql("g")
    conds = [f"g.org_id = :o AND {where}"]
    if channel_id:
        conds.append("g.channel_id = :ch")
    if kind:
        conds.append("g.kind = :k")
    if listen_mode:
        conds.append("g.listen_mode = :lm")
    if q and q.strip():
        conds.append("(g.name ILIKE :q ESCAPE '\\' OR g.code ILIKE :q ESCAPE '\\')")
    base: dict[str, Any] = {"o": user.org_id, "ch": channel_id, "k": kind, "lm": listen_mode, **params}
    if q and q.strip():
        base["q"] = _like_pattern(q)
    sql_where = " AND ".join(conds)
    # `total` đếm theo bộ lọc, KHÔNG theo con trỏ — nếu không, sang trang 2 tổng tụt theo (nút Xem thêm sai số còn lại).
    total = (await db.execute(text(f"SELECT count(*) FROM core.groups g WHERE {sql_where}"),  # noqa: S608
                              base)).scalar_one()
    cur = _page_cursor(cursor)
    page_where = sql_where
    if cur:
        page_where += " AND (g.created_at, g.id) < (:c, :cid)"
        base |= {"c": cur[0], "cid": cur[1]}
    order = " ORDER BY g.created_at DESC, g.id DESC LIMIT :n"
    rows = (await db.execute(text(_GROUP_SELECT + f" WHERE {page_where}" + order),  # noqa: S608
                             {**base, "n": limit + 1})).all()
    items = [_group_payload(r) for r in rows[:limit]]
    return {"items": items, "next_cursor": _next_cursor(rows, limit), "total": total}


class GroupBotIn(BaseModel):
    agent_id: uuid.UUID | None = None
    autonomy_level: int | None = Field(default=None, ge=0, le=6)


@router.post("/directory/groups/{group_id}/bot")
async def set_group_bot(group_id: uuid.UUID, body: GroupBotIn, user: service.CurrentUser = Depends(WRITE),
                        db: AsyncSession = DB) -> dict[str, Any]:
    sc = await scope_for(db, user, "profile.write")
    await ensure_group(db, sc, group_id)
    await rsvc.set_group_bot(db, group_id, agent_id=body.agent_id, autonomy_level=body.autonomy_level,
                             set_autonomy="autonomy_level" in body.model_fields_set)
    await actionlog.record(db, org_id=user.org_id, actor_type="user", actor_id=user.actor_id,
                           action="directory.group_bot_set", target_type="group", target_id=str(group_id),
                           result="ok", detail={"agent_id": str(body.agent_id) if body.agent_id else None,
                                                 "autonomy_level": body.autonomy_level}, ip=user.ip)
    return {"ok": True}


_PEOPLE_CTE = """
WITH rows AS (
  SELECT p.id, p.code, p.display_name AS name, p.person_type AS type, p.organization_name AS org_name,
         p.relation_to_owner AS relation, p.attrs, p.created_at, p.owner_user_id,
         cs.value AS heat, cs.trend AS heat_trend,
         COALESCE(ov.value_vnd, 0) AS value_vnd,
         COALESCE(pr.pri_rank, 3) AS pri_rank,
         COALESCE(ch.channels, '{{}}') AS channels,
         ag.id AS agent_id, ag.name AS agent_name
  FROM core.persons p
  LEFT JOIN clean.current_scores cs ON cs.subject_type = 'person' AND cs.subject_id = p.id AND cs.dimension = 'heat'
  LEFT JOIN LATERAL (SELECT sum(o.value_vnd) AS value_vnd FROM biz.opportunities o
                      WHERE o.person_id = p.id AND o.closed_at IS NULL) ov ON true
  LEFT JOIN LATERAL (SELECT min(CASE i.priority WHEN 'P1' THEN 1 WHEN 'P2' THEN 2 ELSE 3 END) AS pri_rank
                      FROM biz.inbox_items i WHERE i.person_id = p.id) pr ON true
  LEFT JOIN LATERAL (SELECT array_agg(DISTINCT ch2.type) AS channels FROM core.person_identities pi2
                      JOIN core.channels ch2 ON ch2.id = pi2.channel_id WHERE pi2.person_id = p.id) ch ON true
  LEFT JOIN agent.identities ag ON (p.attrs->>'agent_id') IS NOT NULL AND ag.id = (p.attrs->>'agent_id')::uuid
  WHERE p.org_id = :o AND p.deleted_at IS NULL AND p.merged_into_id IS NULL AND {scope}
)
"""
PRI_LABEL = {1: "P1", 2: "P2", 3: "P3"}


def _person_row(r: Any, *, owner: bool) -> dict[str, Any]:
    autonomy = r.attrs.get("autonomy_level") if r.attrs else None
    return {"id": str(r.id), "code": r.code, "name": mask_text(r.name, owner), "type": r.type,
            "org_name": r.org_name, "relation": r.relation, "channels": list(r.channels or []),
            "heat": float(r.heat) if r.heat is not None else None, "heat_trend": r.heat_trend,
            "value_vnd": int(r.value_vnd) if r.value_vnd else None, "priority": PRI_LABEL[r.pri_rank],
            "bot": rsvc.agent_ref(r.agent_id, r.agent_name), "autonomy_level": autonomy,
            "owner_user_id": str(r.owner_user_id) if r.owner_user_id else None}


class PeopleFilter(BaseModel):
    """Bộ lọc danh sách người — dùng chung cho GET (query string) và thao tác hàng loạt (thân JSON), để "những người
    khớp bộ lọc" luôn do MÁY CHỦ xác định, không phải phần giao diện đã tải."""
    relation: str | None = None
    heat: Literal["high", "mid", "cold"] | None = None
    value: Literal["high", "mid", "unknown"] | None = None
    priority: Literal["P1", "P2", "P3"] | None = None
    bot: Literal["assigned", "unassigned"] | None = None
    channel: str | None = Field(default=None, max_length=32, pattern=r"^[a-z][a-z0-9_]*$")
    q: str | None = Field(default=None, max_length=100)


def _people_filter(f: PeopleFilter, *, owner: bool) -> tuple[str, dict[str, Any]]:
    """WHERE trên CTE `rows` (chưa gồm phạm vi) + tham số."""
    conds = ["TRUE"]
    params: dict[str, Any] = {}
    if f.relation:
        conds.append("relation = :rel")
        params["rel"] = f.relation
    if f.heat == "high":
        conds.append("heat >= 80")
    elif f.heat == "mid":
        conds.append("heat >= 50 AND heat < 80")
    elif f.heat == "cold":
        conds.append("(heat IS NULL OR heat < 50)")
    if f.value == "high":
        conds.append("value_vnd >= 500000000")
    elif f.value == "mid":
        conds.append("value_vnd >= 100000000 AND value_vnd < 500000000")
    elif f.value == "unknown":
        conds.append("value_vnd = 0")
    if f.priority:
        conds.append("pri_rank = :prirank")
        params["prirank"] = {"P1": 1, "P2": 2, "P3": 3}[f.priority]
    if f.bot == "assigned":
        conds.append("(attrs->>'agent_id') IS NOT NULL")
    elif f.bot == "unassigned":
        conds.append("(attrs->>'agent_id') IS NULL")
    if f.channel:
        conds.append("CAST(:chan AS text) = ANY(channels)")
        params["chan"] = f.channel
    if f.q and f.q.strip():
        # Tên có dãy số dài bị che với vai dưới Owner (khoá cứng 8) nhưng mask_text vẫn để lộ 3 số cuối ⇒ vai đó
        # không được tìm theo tên khi q có BẤT KỲ chữ số nào (dò từng cửa sổ 1–3 số sẽ dựng lại được số đã che).
        # Tìm theo mã người vẫn dùng được.
        has_digit = any(c.isdigit() for c in f.q)
        by_name = "name ILIKE :q ESCAPE '\\'" if owner or not has_digit else "FALSE"
        conds.append(f"({by_name} OR code ILIKE :q ESCAPE '\\')")
        params["q"] = _like_pattern(f.q)
    return " AND ".join(conds), params


@router.get("/directory/people")
async def list_people(relation: str | None = None, heat: Literal["high", "mid", "cold"] | None = None,
                      value: Literal["high", "mid", "unknown"] | None = None,
                      priority: Literal["P1", "P2", "P3"] | None = None,
                      bot: Literal["assigned", "unassigned"] | None = None,
                      channel: str | None = Query(None, max_length=32, pattern=r"^[a-z][a-z0-9_]*$"),
                      q: str | None = Query(None, max_length=100), cursor: str | None = None,
                      limit: int = Query(50, ge=1, le=200), user: service.CurrentUser = Depends(READ),
                      db: AsyncSession = DB) -> dict[str, Any]:
    sc = await scope_for(db, user, "profile.read")
    where, params = sc.person_sql("p")
    owner = user.role_code == rbac.OWNER
    filt, fparams = _people_filter(PeopleFilter(relation=relation, heat=heat, value=value, priority=priority,
                                                bot=bot, channel=channel, q=q), owner=owner)
    base: dict[str, Any] = {"o": user.org_id, **fparams, **params}
    cte = _PEOPLE_CTE.format(scope=where)
    # `total` theo bộ lọc, không theo con trỏ (xem list_groups).
    total = (await db.execute(text(cte + f" SELECT count(*) FROM rows WHERE {filt}"), base)).scalar_one()  # noqa: S608
    cur = _page_cursor(cursor)
    page_filt = filt
    if cur:
        page_filt += " AND (created_at, id) < (:c, :cid)"
        base |= {"c": cur[0], "cid": cur[1]}
    order = " ORDER BY created_at DESC, id DESC LIMIT :n"
    rows = (await db.execute(text(cte + f" SELECT * FROM rows WHERE {page_filt}" + order),  # noqa: S608
                             {**base, "n": limit + 1})).all()
    items = [_person_row(r, owner=owner) for r in rows[:limit]]
    return {"items": items, "next_cursor": _next_cursor(rows, limit), "total": total}


class BulkBotIn(BaseModel):
    filter: PeopleFilter = Field(default_factory=PeopleFilter)
    agent_id: uuid.UUID | None = None
    autonomy_level: int | None = Field(default=None, ge=0, le=6)
    expected_total: int | None = Field(default=None, ge=0)


@router.post("/directory/people/bulk-bot")
async def set_people_bot_bulk(body: BulkBotIn, user: service.CurrentUser = Depends(require_owner),
                              _pin: Any = Depends(require_pin("policy.change")),
                              db: AsyncSession = DB) -> dict[str, Any]:
    """Gán BOT / mức tự trị cho MỌI người khớp bộ lọc (trong phạm vi ghi của người gọi) — một câu lệnh, một giao
    dịch (đủ hoặc không gì cả). Chỉ Owner + mã PIN `policy.change` (review v0.1.65 P2-3): gán BOT cho một người làm
    tin 1-1 của họ đi thẳng tới BOT dù kênh để im lặng — là đổi mức tự trị, không phải sửa hồ sơ thường.
    `expected_total` là số giao diện đã hiện cho Sếp: lệch (có người mới vào/ra khỏi bộ lọc) ⇒ 409, không đổi gì —
    Sếp thấy số mới rồi quyết lại."""
    fields = body.model_fields_set
    set_agent, set_autonomy = "agent_id" in fields, "autonomy_level" in fields
    if not set_agent and not set_autonomy:
        raise ApiError(422, "VALIDATION_ERROR", "Chưa chọn thay đổi nào",
                       errors={"agent_id": "Chọn BOT hoặc mức tự trị cần đổi"})
    sc = await scope_for(db, user, "profile.write")
    if set_agent and body.agent_id is not None:
        ok = (await db.execute(text("SELECT 1 FROM agent.identities WHERE id = :a AND org_id = :o "
                                    "AND archived_at IS NULL"), {"a": body.agent_id, "o": user.org_id})).first()
        if ok is None:
            raise not_found("Agent")
    where, params = sc.person_sql("p")
    filt, fparams = _people_filter(body.filter, owner=user.role_code == rbac.OWNER)
    cte = _PEOPLE_CTE.format(scope=where)
    base: dict[str, Any] = {"o": user.org_id, **fparams, **params}
    if body.expected_total is not None:
        now_total = (await db.execute(text(cte + f" SELECT count(*) FROM rows WHERE {filt}"),  # noqa: S608
                                      base)).scalar_one()
        if now_total != body.expected_total:
            raise ApiError(409, "BULK_FILTER_CHANGED", "Danh sách đã thay đổi",
                           f"Bộ lọc hiện khớp {now_total} người (không phải {body.expected_total}). "
                           "Hãy xem lại số rồi áp dụng lại.")
    expr = "COALESCE(p.attrs, '{}'::jsonb)"
    if set_agent:
        expr = f"({expr} - 'agent_id')"
        if body.agent_id is not None:
            expr += " || jsonb_build_object('agent_id', CAST(:aid AS text))"
            base["aid"] = str(body.agent_id)
    if set_autonomy:
        expr = f"({expr} - 'autonomy_level')"
        if body.autonomy_level is not None:
            expr += " || jsonb_build_object('autonomy_level', CAST(:lvl AS int))"
            base["lvl"] = body.autonomy_level
    done = (await db.execute(text(cte.rstrip() + f""",
        targets AS (SELECT id FROM rows WHERE {filt})
        UPDATE core.persons p SET attrs = {expr}, updated_at = now()
        FROM targets t WHERE p.id = t.id AND p.org_id = :o RETURNING p.id"""), base)).all()  # noqa: S608
    detail: dict[str, Any] = {"updated": len(done), "filter": body.filter.model_dump(exclude_none=True)}
    if set_agent:
        detail["agent_id"] = str(body.agent_id) if body.agent_id else None
    if set_autonomy:
        detail["autonomy_level"] = body.autonomy_level
    await actionlog.record(db, org_id=user.org_id, actor_type="user", actor_id=user.actor_id,
                           action="directory.bulk_bot_set", target_type="person", target_id=None,
                           result="ok", detail=detail, ip=user.ip)
    return {"ok": True, "updated": len(done)}


class PersonBotIn(BaseModel):
    agent_id: uuid.UUID | None = None
    autonomy_level: int | None = Field(default=None, ge=0, le=6)


@router.post("/directory/people/{person_id}/bot")
async def set_person_bot(person_id: uuid.UUID, body: PersonBotIn, user: service.CurrentUser = Depends(require_owner),
                         _pin: Any = Depends(require_pin("policy.change")),
                         db: AsyncSession = DB) -> dict[str, Any]:
    """Gán/bỏ BOT hoặc đổi mức tự trị cho MỘT người — chỉ Owner + mã PIN `policy.change` (review v0.1.65 P2-3), cùng
    lý do với bulk-bot: gán BOT bỏ qua chế độ im lặng của tin 1-1."""
    sc = await scope_for(db, user, "profile.write")
    await ensure_person(db, sc, person_id)
    await rsvc.set_person_bot(db, person_id, agent_id=body.agent_id, set_agent="agent_id" in body.model_fields_set,
                              autonomy_level=body.autonomy_level,
                              set_autonomy="autonomy_level" in body.model_fields_set)
    await actionlog.record(db, org_id=user.org_id, actor_type="user", actor_id=user.actor_id,
                           action="directory.bot_assigned", target_type="person", target_id=str(person_id),
                           result="ok", detail={"agent_id": str(body.agent_id) if body.agent_id else None,
                                                 "autonomy_level": body.autonomy_level}, ip=user.ip)
    return {"ok": True}


# ═══ Hồ sơ sống (profile) ════════════════════════════════════════════════════

async def _person_or_404(db: AsyncSession, sc: Scope, person_id: uuid.UUID) -> Any:
    await ensure_person(db, sc, person_id)
    r = (await db.execute(text("""
        SELECT p.id, p.code, p.display_name AS name, p.person_type AS type, p.organization_name AS org_name,
               p.title, p.relation_to_owner, p.attrs, p.owner_user_id, u.display_name AS owner_name,
               ro.code AS owner_role
        FROM core.persons p LEFT JOIN core.users u ON u.id = p.owner_user_id
        LEFT JOIN LATERAL (SELECT r2.code FROM core.user_roles ur2 JOIN core.roles r2 ON r2.id = ur2.role_id
                            WHERE ur2.user_id = u.id ORDER BY r2.code LIMIT 1) ro ON true
        WHERE p.id = :i"""), {"i": person_id})).one_or_none()
    if r is None:
        raise not_found("Người")
    return r


@router.get("/profile/{person_id}", response_model=None)
async def get_profile(person_id: uuid.UUID, request: Request, user: service.CurrentUser = Depends(READ),
                      db: AsyncSession = DB) -> dict[str, Any] | Response:
    sc = await scope_for(db, user, "profile.read")
    r = await _person_or_404(db, sc, person_id)
    # Hồ sơ đã gộp (`merged_into_id`, chỉ đọc ở đây) không còn là hồ sơ sống: đưa người xem tới hồ sơ gốc thay vì
    # hiện một vỏ rỗng. 307 (không phải 308) vì tách danh tính có thể trả hồ sơ này về trạng thái sống. Đích ngoài
    # phạm vi của người xem ⇒ 404 như mọi hồ sơ không thấy được.
    live = await live_person(db, person_id)
    if live != person_id:
        await ensure_person(db, sc, live)
        return RedirectResponse(f"{request.url.path.rsplit('/', 1)[0]}/{live}", status_code=307)
    return await _profile_payload(person_id, user, db, r)


async def _profile_payload(person_id: uuid.UUID, user: service.CurrentUser, db: AsyncSession, r: Any) -> dict[str, Any]:
    owner = user.role_code == rbac.OWNER

    identities = (await db.execute(text("""
        SELECT pi.id, pi.external_id, pi.handle, pi.phone_e164, pi.first_seen_at, c.type AS channel_type,
               c.name AS channel_name
        FROM core.person_identities pi JOIN core.channels c ON c.id = pi.channel_id
        WHERE pi.person_id = :p ORDER BY pi.first_seen_at"""), {"p": person_id})).all()

    scores = (await db.execute(text("""
        SELECT dimension, value, trend, updated_at FROM clean.current_scores
        WHERE subject_type = 'person' AND subject_id = :p ORDER BY dimension"""), {"p": person_id})).all()

    dm_sql, dm_params = direct_visible_sql(user, "mu.group_id", "mu.person_id")   # tin 1-1: chỉ Owner/người được giao
    timeline = (await db.execute(text(f"""
        SELECT mu.id, mu.event_type, mu.conclusion, mu.confidence, mu.observed_at,
               g.id AS g_id, g.code AS g_code, g.name AS g_name, gc.type AS g_channel
        FROM clean.meaning_units mu LEFT JOIN core.groups g ON g.id = mu.group_id
        LEFT JOIN core.channels gc ON gc.id = g.channel_id
        WHERE mu.org_id = :o AND mu.person_id = :p AND mu.superseded_by IS NULL AND {dm_sql}
        ORDER BY mu.observed_at DESC LIMIT 30"""),  # noqa: S608 — biểu thức nội bộ
        {"o": user.org_id, "p": person_id, **dm_params})).all()

    docs = (await db.execute(text("""
        SELECT id, title, mime, bytes, created_at FROM biz.documents
        WHERE org_id = :o AND owner_person_id = :p AND deleted_at IS NULL
        ORDER BY created_at DESC LIMIT 20"""), {"o": user.org_id, "p": person_id})).all()

    touchpoints = (await db.execute(text("""
        SELECT DISTINCT u.id, u.display_name, ro.code AS role_code
        FROM ops.action_log a JOIN core.users u ON ('user:' || u.id::text) = a.actor_id
        LEFT JOIN core.user_roles ur ON ur.user_id = u.id LEFT JOIN core.roles ro ON ro.id = ur.role_id
        WHERE a.org_id = :o AND a.target_type = 'person' AND a.target_id = :p
        ORDER BY u.display_name LIMIT 12"""), {"o": user.org_id, "p": str(person_id)})).all()

    merges = (await db.execute(text("""
        SELECT id, op, from_person, to_person, identities, at, reverted_at
        FROM core.identity_merge_log WHERE org_id = :o AND (from_person = :p OR to_person = :p)
        ORDER BY at DESC LIMIT 20"""), {"o": user.org_id, "p": person_id})).all()

    def tone(event_type: str) -> str:
        if event_type in ("Complained", "MentionsCompetitor", "WentSilent"):
            return "bad"
        if event_type in ("AskedPrice", "OfferedSupply", "SentQuotation", "DealWon"):
            return "ok"
        return "neutral"

    return {
        "person": {"id": str(r.id), "code": r.code, "name": mask_text(r.name, owner), "type": r.type,
                   "org_name": r.org_name, "title": r.title, "relation_to_owner": r.relation_to_owner,
                   "owner": rsvc.user_ref(r.owner_user_id, r.owner_name, r.owner_role)},
        "autonomy_level": r.attrs.get("autonomy_level") if r.attrs else None,
        "bot": rsvc.agent_ref(uuid.UUID(r.attrs["agent_id"]), None) if r.attrs and r.attrs.get("agent_id") else None,
        "owner_note": r.attrs.get("owner_note") if r.attrs else None,
        "identities": [{"id": str(i.id), "channel": {"type": i.channel_type, "name": i.channel_name},
                        "external_id": mask_text(i.external_id, owner), "handle": i.handle,
                        "phone_e164": mask_text(i.phone_e164, owner), "first_seen_at": iso(i.first_seen_at)}
                       for i in identities],
        "scores": [{"dimension": s.dimension, "label": explain.DIMENSION_LABELS.get(s.dimension, s.dimension),
                    "value": float(s.value), "trend": s.trend, "updated_at": iso(s.updated_at)} for s in scores],
        "summary": [{"text": mask_text(t.conclusion, owner), "tone": tone(t.event_type),
                     "evidence": {"type": "meaning_unit", "id": str(t.id)}} for t in timeline[:5]],
        "timeline": [{"id": str(t.id), "event_type": t.event_type, "conclusion": mask_text(t.conclusion, owner),
                      "confidence": float(t.confidence), "observed_at": iso(t.observed_at),
                      "group": rsvc.group_ref(t, "g"),
                      "evidence": {"type": "meaning_unit", "id": str(t.id)}} for t in timeline],
        "documents": [{"id": str(d.id), "title": d.title, "mime": d.mime, "bytes": d.bytes,
                       "created_at": iso(d.created_at)} for d in docs],
        "touchpoints": [rsvc.user_ref(t.id, t.display_name, t.role_code) for t in touchpoints],
        "merge_history": [{"id": str(m.id), "op": m.op, "from_person": str(m.from_person),
                           "to_person": str(m.to_person), "identities": [str(i) for i in (m.identities or [])],
                           "at": iso(m.at), "reverted_at": iso(m.reverted_at)} for m in merges],
    }


class ProfilePatch(BaseModel):
    owner_user_id: uuid.UUID | None = None
    autonomy_level: int | None = Field(default=None, ge=0, le=6)
    note: str | None = Field(default=None, max_length=4000)
    # v0.1.64 (F045, F141): Sếp tự đặt loại người ("Đánh dấu là nhân viên") và mức "Liên quan Sếp". null = không đổi.
    person_type: Literal["customer", "partner", "staff", "candidate", "learner", "supplier", "unknown"] | None = None
    relation_to_owner: Literal["direct", "via_staff", "staff", "stranger"] | None = None


@router.patch("/profile/{person_id}")
async def patch_profile(person_id: uuid.UUID, body: ProfilePatch, user: service.CurrentUser = Depends(WRITE),
                        db: AsyncSession = DB) -> dict[str, Any]:
    sc = await scope_for(db, user, "profile.write")
    await ensure_person(db, sc, person_id)
    fields = body.model_fields_set
    detail: dict[str, Any] = {}
    if "owner_user_id" in fields:
        await ensure_active_user(db, user.org_id, body.owner_user_id)
        await db.execute(text("UPDATE core.persons SET owner_user_id = :u, updated_at = now() WHERE id = :i"),
                         {"u": body.owner_user_id, "i": person_id})
        detail["owner_user_id"] = str(body.owner_user_id) if body.owner_user_id else None
    if "autonomy_level" in fields:
        await rsvc.set_person_bot(db, person_id, agent_id=None, set_agent=False,
                                  autonomy_level=body.autonomy_level, set_autonomy=True)
        detail["autonomy_level"] = body.autonomy_level
    if body.person_type is not None:
        # Khoá `person_type_locked`: loại do Sếp đặt thắng quy tắc sàng lọc (runner chỉ nâng từ 'unknown' chưa khoá).
        await db.execute(text("""UPDATE core.persons SET person_type = :t, updated_at = now(),
                                 attrs = COALESCE(attrs, '{}'::jsonb) || '{"person_type_locked": true}'::jsonb
                                 WHERE id = :i"""), {"t": body.person_type, "i": person_id})
        detail["person_type"] = body.person_type
    if body.relation_to_owner is not None:
        await db.execute(text("UPDATE core.persons SET relation_to_owner = :r, updated_at = now() WHERE id = :i"),
                         {"r": body.relation_to_owner, "i": person_id})
        detail["relation_to_owner"] = body.relation_to_owner
    if "note" in fields:
        row = (await db.execute(text("SELECT attrs FROM core.persons WHERE id = :i"), {"i": person_id})).scalar_one()
        attrs = dict(row or {})
        if body.note is None:
            attrs.pop("owner_note", None)
        else:
            attrs["owner_note"] = body.note
        await db.execute(text("UPDATE core.persons SET attrs = CAST(:a AS jsonb), updated_at = now() WHERE id = :i"),
                         {"a": orjson.dumps(attrs).decode(), "i": person_id})
        detail["note_changed"] = True
    if detail:
        await actionlog.record(db, org_id=user.org_id, actor_type="user", actor_id=user.actor_id,
                               action="profile.updated", target_type="person", target_id=str(person_id),
                               result="ok", detail=detail, ip=user.ip)
    return await _profile_payload(person_id, user, db, await _person_or_404(db, sc, person_id))


# ═══ Sổ tay nhận thức (notebook) ═════════════════════════════════════════════

async def _ensure_subject(db: AsyncSession, sc: Scope, type_: str, sid: uuid.UUID) -> dict[str, Any]:
    if type_ == "person":
        await ensure_person(db, sc, sid)
        table, col = "core.persons", "display_name"
    else:
        await ensure_group(db, sc, sid)
        table, col = "core.groups", "name"
    r = (await db.execute(text(f"SELECT id, code, {col} AS name FROM {table} WHERE id = :i AND org_id = :o"),  # noqa: S608
                          {"i": sid, "o": sc.org_id})).one_or_none()
    if r is None:
        raise not_found("Hồ sơ" if type_ == "person" else "Nhóm")
    return {"type": type_, "id": str(r.id), "code": r.code, "name": r.name}


@router.get("/notebook/subjects")
async def notebook_subjects(type: Literal["person", "group"] = "person", cursor: str | None = None,  # noqa: A002
                            limit: int = Query(50, ge=1, le=200), user: service.CurrentUser = Depends(READ),
                            db: AsyncSession = DB) -> dict[str, Any]:
    sc = await scope_for(db, user, "profile.read")
    table, col = ("core.persons", "display_name") if type == "person" else ("core.groups", "name")
    where, params = (sc.person_sql("s") if type == "person" else sc.group_sql("s"))
    conds = [f"s.org_id = :o AND {where}"]
    if type == "person":
        conds.append("s.deleted_at IS NULL AND s.merged_into_id IS NULL")
    if cursor:
        conds.append("n.last_compacted_at < :c OR (n.last_compacted_at IS NULL AND n.created_at < :c)")
    sql_where = " AND ".join(conds)
    base = {"o": user.org_id, "t": type, "c": parse_cursor(cursor), **params}
    q = f"""
        SELECT s.id, s.code, s.{col} AS name, n.token_used, n.token_budget, n.last_compacted_at, n.created_at,
               (SELECT count(*) FROM memory.entries e WHERE e.notebook_id = n.id AND e.archived_at IS NULL) AS n_entries
        FROM {table} s JOIN memory.notebooks n ON n.subject_type = :t AND n.subject_id = s.id
        WHERE {sql_where}
        ORDER BY COALESCE(n.last_compacted_at, n.created_at) DESC LIMIT :n"""  # noqa: S608
    rows = (await db.execute(text(q), {**base, "n": limit + 1})).all()
    items = [{"id": str(r.id), "code": r.code, "name": r.name, "entries": r.n_entries,
             "token_used": r.token_used, "token_budget": r.token_budget,
             "updated_at": iso(r.last_compacted_at or r.created_at)} for r in rows[:limit]]
    nxt = None
    if len(rows) > limit:
        last = rows[limit - 1]
        nxt = iso(last.last_compacted_at or last.created_at)
    return {"items": items, "next_cursor": nxt}


async def _notebook_payload(db: AsyncSession, org_id: uuid.UUID, type_: str, sid: uuid.UUID,
                            subject: dict[str, Any]) -> dict[str, Any]:
    nb = await notebook.ensure(db, org_id, type_, sid)
    rows = (await db.execute(text("""
        SELECT e.id, e.section, e.body, e.refs, e.author, e.is_pinned, e.created_at, u.display_name AS user_name
        FROM memory.entries e
        LEFT JOIN core.users u ON e.author LIKE 'user:%' AND u.id::text = substring(e.author from 6)
        WHERE e.notebook_id = :n AND e.archived_at IS NULL ORDER BY e.is_pinned DESC, e.created_at DESC"""),
        {"n": nb.id})).all()
    refs: list[dict[str, Any]] = []
    seen = set()
    for r in rows:
        for ref in (r.refs or []):
            key = (ref.get("type"), ref.get("id"))
            if key not in seen:
                seen.add(key)
                refs.append(ref)
    sections = []
    for sec_key, title in notebook.SECTIONS.items():
        entries = [r for r in rows if r.section == sec_key]
        sections.append({"key": sec_key, "title": title,
                         "entries": [{"id": str(e.id), "body": e.body, "refs": e.refs, "pinned": e.is_pinned,
                                      "editable": e.author.startswith("user:"),
                                      "author": {"type": "user" if e.author.startswith("user:") else "agent",
                                                 "label": e.user_name or "Core agent"},
                                      "created_at": iso(e.created_at)} for e in entries]})
    return {"subject": subject, "token_used": nb.token_used, "token_budget": nb.token_budget,
            "compaction_no": nb.compaction_no, "last_compacted_at": iso(nb.last_compacted_at),
            "sections": sections, "refs": refs[:40]}


@router.get("/notebook/{type_}/{sid}")
async def get_notebook(type_: Literal["person", "group"], sid: uuid.UUID, user: service.CurrentUser = Depends(READ),
                       db: AsyncSession = DB) -> dict[str, Any]:
    sc = await scope_for(db, user, "profile.read")
    subject = await _ensure_subject(db, sc, type_, sid)
    return await _notebook_payload(db, user.org_id, type_, sid, subject)


class NotebookEntryIn(BaseModel):
    section: str
    body: str = Field(min_length=1, max_length=2000)
    pinned: bool = False
    refs: list[dict[str, Any]] = Field(default_factory=list)


@router.post("/notebook/{type_}/{sid}/entries", status_code=201)
async def add_notebook_entry(type_: Literal["person", "group"], sid: uuid.UUID, body: NotebookEntryIn,
                             user: service.CurrentUser = Depends(WRITE), db: AsyncSession = DB) -> dict[str, Any]:
    if body.section not in notebook.SECTIONS:
        raise ApiError(422, "VALIDATION", "Mục sổ tay không hợp lệ", errors={"section": "Không hợp lệ"})
    sc = await scope_for(db, user, "profile.write")
    subject = await _ensure_subject(db, sc, type_, sid)
    eid = await notebook.append(db, user.org_id, type_, sid, body.section, body.body, body.refs,
                                f"user:{user.id}", body.pinned)
    await actionlog.record(db, org_id=user.org_id, actor_type="user", actor_id=user.actor_id,
                           action="notebook.entry_added", target_type=type_, target_id=subject["code"],
                           result="ok", detail={"entry_id": str(eid), "section": body.section,
                                                 "pinned": body.pinned}, ip=user.ip)
    return {"id": str(eid)}


async def _notebook_entry(db: AsyncSession, org_id: uuid.UUID, type_: str, sid: uuid.UUID, eid: uuid.UUID) -> Any:
    r = (await db.execute(text("""
        SELECT e.* FROM memory.entries e JOIN memory.notebooks n ON n.id = e.notebook_id
        WHERE e.id = :e AND n.org_id = :o AND n.subject_type = :t AND n.subject_id = :s AND e.archived_at IS NULL"""),
        {"e": eid, "o": org_id, "t": type_, "s": sid})).one_or_none()
    if r is None:
        raise not_found("Mục sổ tay")
    return r


class NotebookEntryPatch(BaseModel):
    body: str | None = Field(default=None, min_length=1, max_length=2000)
    pinned: bool | None = None


@router.patch("/notebook/{type_}/{sid}/entries/{eid}")
async def patch_notebook_entry(type_: Literal["person", "group"], sid: uuid.UUID, eid: uuid.UUID,
                               body: NotebookEntryPatch, user: service.CurrentUser = Depends(WRITE),
                               db: AsyncSession = DB) -> dict[str, Any]:
    sc = await scope_for(db, user, "profile.write")
    await _ensure_subject(db, sc, type_, sid)
    e = await _notebook_entry(db, user.org_id, type_, sid, eid)
    new_id = eid
    if body.body is not None and body.body != e.body:
        if not e.author.startswith("user:"):
            raise ApiError(409, "SYSTEM_ENTRY_READONLY", "Mục do hệ thống/agent ghi — không sửa nội dung được")
        new_id = (await db.execute(text("""
            INSERT INTO memory.entries (notebook_id, section, body, refs, author, is_pinned, tokens, created_at)
            VALUES (:n, :s, :b, CAST(:r AS jsonb), :a, :p, :t, now()) RETURNING id"""),
            {"n": e.notebook_id, "s": e.section, "b": body.body, "r": orjson.dumps(e.refs).decode(),
             "a": f"user:{user.id}", "p": body.pinned if body.pinned is not None else e.is_pinned,
             "t": notebook.estimate_tokens(body.body)})).scalar_one()
        await db.execute(text("UPDATE memory.entries SET archived_at = now(), replaced_by = :n WHERE id = :e"),
                         {"n": new_id, "e": eid})
    elif body.pinned is not None:
        # Ghim/bỏ ghim cho phép trên mọi mục (kể cả do agent ghi) — đây là cách Owner giữ một quan sát quan
        # trọng khỏi bị nén, không phải sửa nội dung.
        await db.execute(text("UPDATE memory.entries SET is_pinned = :p WHERE id = :e"), {"p": body.pinned, "e": eid})
    await notebook.recount(db, e.notebook_id)
    await actionlog.record(db, org_id=user.org_id, actor_type="user", actor_id=user.actor_id,
                           action="notebook.entry_edited", target_type=type_, target_id=str(sid),
                           result="ok", detail={"entry_id": str(eid), "new_entry_id": str(new_id),
                                                 "pinned": body.pinned}, ip=user.ip)
    return {"id": str(new_id)}


@router.delete("/notebook/{type_}/{sid}/entries/{eid}", status_code=204)
async def delete_notebook_entry(type_: Literal["person", "group"], sid: uuid.UUID, eid: uuid.UUID,
                                user: service.CurrentUser = Depends(WRITE), db: AsyncSession = DB) -> Response:
    sc = await scope_for(db, user, "profile.write")
    await _ensure_subject(db, sc, type_, sid)
    e = await _notebook_entry(db, user.org_id, type_, sid, eid)
    if not e.author.startswith("user:"):
        raise ApiError(409, "SYSTEM_ENTRY_READONLY", "Mục do hệ thống/agent ghi — không xoá được")
    await db.execute(text("UPDATE memory.entries SET archived_at = now() WHERE id = :e"), {"e": eid})
    await notebook.recount(db, e.notebook_id)
    await actionlog.record(db, org_id=user.org_id, actor_type="user", actor_id=user.actor_id,
                           action="notebook.entry_archived", target_type=type_, target_id=str(sid),
                           result="ok", detail={"entry_id": str(eid)}, ip=user.ip)
    return Response(status_code=204)


@router.post("/notebook/{type_}/{sid}/compact")
async def compact_notebook(type_: Literal["person", "group"], sid: uuid.UUID,
                           user: service.CurrentUser = Depends(WRITE), db: AsyncSession = DB) -> dict[str, Any]:
    sc = await scope_for(db, user, "profile.write")
    subject = await _ensure_subject(db, sc, type_, sid)
    nb = await notebook.ensure(db, user.org_id, type_, sid)
    info = await notebook.compact(db, nb.id, reason="manual")
    await actionlog.record(db, org_id=user.org_id, actor_type="user", actor_id=user.actor_id,
                           action="notebook.compacted", target_type=type_, target_id=str(sid), result="ok",
                           detail=info or {}, ip=user.ip)
    return await _notebook_payload(db, user.org_id, type_, sid, subject)


@router.post("/notebook/{type_}/{sid}/reset")
async def reset_notebook(type_: Literal["person", "group"], sid: uuid.UUID,
                         user: service.CurrentUser = Depends(WRITE), db: AsyncSession = DB) -> dict[str, Any]:
    """Đặt lại: lưu trữ toàn bộ mục chưa ghim (trừ "Giới hạn cho agent", như nén) mà **không** sinh mục tóm tắt —
    khác `compact()` (nén giữ tóm tắt), đây là dọn sạch hẳn để agent bắt đầu lại từ số 0 cho chủ thể này."""
    sc = await scope_for(db, user, "profile.write")
    subject = await _ensure_subject(db, sc, type_, sid)
    nb = await notebook.ensure(db, user.org_id, type_, sid)
    ids = (await db.execute(text("""
        SELECT id FROM memory.entries WHERE notebook_id = :n AND archived_at IS NULL AND NOT is_pinned
          AND section <> ALL(:never)"""), {"n": nb.id, "never": list(notebook.NEVER_COMPACT)})).scalars().all()
    if ids:
        await db.execute(text("UPDATE memory.entries SET archived_at = now() WHERE id = ANY(:ids)"), {"ids": ids})
    await notebook.recount(db, nb.id)
    await actionlog.record(db, org_id=user.org_id, actor_type="user", actor_id=user.actor_id,
                           action="notebook.reset", target_type=type_, target_id=str(sid), result="ok",
                           detail={"archived": len(ids)}, ip=user.ip)
    return await _notebook_payload(db, user.org_id, type_, sid, subject)


@router.get("/notebook/{type_}/{sid}/history")
async def notebook_history(type_: Literal["person", "group"], sid: uuid.UUID,
                           user: service.CurrentUser = Depends(READ), db: AsyncSession = DB) -> list[dict[str, Any]]:
    sc = await scope_for(db, user, "profile.read")
    await _ensure_subject(db, sc, type_, sid)
    rows = (await db.execute(text("""
        SELECT c.compaction_no, c.at, c.tokens_before, c.tokens_after, cardinality(c.archived_entries) AS n, c.summary
        FROM memory.compactions c JOIN memory.notebooks n ON n.id = c.notebook_id
        WHERE n.org_id = :o AND n.subject_type = :t AND n.subject_id = :s ORDER BY c.compaction_no DESC"""),
        {"o": user.org_id, "t": type_, "s": sid})).all()
    return [{"compaction_no": r.compaction_no, "at": iso(r.at), "tokens_before": r.tokens_before,
             "tokens_after": r.tokens_after, "archived": r.n, "summary": r.summary} for r in rows]


@router.get("/notebook/{type_}/{sid}/dropped")
async def notebook_dropped(type_: Literal["person", "group"], sid: uuid.UUID, cursor: str | None = None,
                           limit: int = Query(50, ge=1, le=200), user: service.CurrentUser = Depends(READ),
                           db: AsyncSession = DB) -> dict[str, Any]:
    """Mục đã nén khỏi ngữ cảnh — vẫn truy được (handoff/01 §notebook `[nbDropped]`)."""
    sc = await scope_for(db, user, "profile.read")
    await _ensure_subject(db, sc, type_, sid)
    nb = await notebook.ensure(db, user.org_id, type_, sid)
    conds = ["e.notebook_id = :n", "e.archived_at IS NOT NULL"]
    if cursor:
        conds.append("e.archived_at < :c")
    where = " AND ".join(conds)
    rows = (await db.execute(text(f"""
        SELECT e.id, e.section, e.body, e.refs, e.author, e.archived_at
        FROM memory.entries e WHERE {where} ORDER BY e.archived_at DESC LIMIT :lim"""),  # noqa: S608
        {"n": nb.id, "c": parse_cursor(cursor), "lim": limit + 1})).all()
    items = [{"id": str(r.id), "section": r.section, "body": r.body, "refs": r.refs,
             "author": {"type": "user" if r.author.startswith("user:") else "agent"},
             "archived_at": iso(r.archived_at)} for r in rows[:limit]]
    return {"items": items, "next_cursor": iso(rows[limit - 1].archived_at) if len(rows) > limit else None}


# ═══ Tài liệu (documents) ════════════════════════════════════════════════════
# ACL (`biz.document_acl.principal`): `role:<code>` (vai trò) | `user:<id>` (cá nhân) | `group:<id>` (một
# `core.teams` — nhóm nhân viên nội bộ, khác `core.groups` là nhóm chat của khách) | `agent:<id>`. ACL là danh
# sách **cấp thêm** quyền (không phải danh sách chặn): một tài liệu luôn thấy được theo phạm vi mặc định của
# người/nhóm sở hữu (`owner_person_id`/`owner_group_id`, như mọi đối tượng nghiệp vụ khác — docs/api/phase-3.md
# §Phạm vi), **cộng thêm** bất kỳ dòng ACL nào khớp vai trò/cá nhân/team của người gọi — dùng để chia sẻ một
# tài liệu ra ngoài phạm vi mặc định (vd cho một vai trò hoặc một team cụ thể xem/sửa dù không sở hữu người/
# nhóm gắn với tài liệu).

MAX_DOC_BYTES = 20 * 1024 * 1024


def _principals(user: service.CurrentUser) -> list[str]:
    out = [f"role:{user.role_code}", f"user:{user.id}"]
    if user.team_id:
        out.append(f"group:{user.team_id}")
    return out


def _doc_default_scope_sql(sc: Scope) -> tuple[str, dict[str, Any]]:
    """Phạm vi mặc định (không có dòng ACL nào cho tài liệu): theo người/nhóm sở hữu, như mọi đối tượng khác."""
    if sc.is_all:       # vẫn theo «Quyền xem» của nhóm sở hữu (v0.1.71)
        return sc.group_id_sql("d.owner_group_id")
    pw, pp = sc.person_id_sql("d.owner_person_id")
    gw, gp = sc.group_id_sql("d.owner_group_id")
    return f"(({pw}) OR ({gw}) OR (d.owner_person_id IS NULL AND d.owner_group_id IS NULL))", {**pp, **gp}


def _doc_visible_sql(sc: Scope, user: service.CurrentUser, *, need_write: bool = False) -> tuple[str, dict[str, Any]]:
    """Một dòng `biz.documents d` thấy được (hoặc sửa được, `need_write`) — dùng chung cho danh sách và chi
    tiết: trong phạm vi mặc định (người/nhóm sở hữu) **hoặc** có dòng ACL cấp quyền cho vai trò/cá nhân/team của
    người gọi (xem chú thích ACL ở trên — cấp thêm, không phải chặn bớt)."""
    if sc.is_all:
        return _doc_default_scope_sql(sc)
    flag = "a.can_write" if need_write else "(a.can_read OR a.can_write)"
    acl_grant = (f"EXISTS (SELECT 1 FROM biz.document_acl a WHERE a.document_id = d.id "
                f"AND a.principal = ANY(:doc_principals) AND {flag})")
    default_where, default_params = _doc_default_scope_sql(sc)
    return f"(({acl_grant}) OR ({default_where}))", {"doc_principals": _principals(user), **default_params}


_DOC_SELECT = """
SELECT d.id, d.title, d.description, d.storage_key, d.mime, d.bytes, d.owner_group_id, d.owner_person_id,
       d.created_by, d.raw_event_id, d.created_at, d.updated_at,
       p.id AS p_id, p.code AS p_code, p.display_name AS p_name, p.person_type AS p_type, p.organization_name AS p_org,
       g.id AS g_id, g.code AS g_code, g.name AS g_name, gc.type AS g_channel
FROM biz.documents d
LEFT JOIN core.persons p ON p.id = d.owner_person_id
LEFT JOIN core.groups g ON g.id = d.owner_group_id LEFT JOIN core.channels gc ON gc.id = g.channel_id
WHERE d.deleted_at IS NULL
"""
_SOURCE_SQL = {"agent": "d.created_by LIKE 'agent:%'", "tay": "d.created_by LIKE 'user:%'",
              "channel": "d.created_by NOT LIKE 'agent:%' AND d.created_by NOT LIKE 'user:%'"}


def _doc_source(created_by: str) -> str:
    if created_by.startswith("agent:"):
        return "agent"
    if created_by.startswith("user:"):
        return "tay"
    return "channel"


def _doc_payload(r: Any) -> dict[str, Any]:
    return {"id": str(r.id), "title": r.title, "description": r.description, "mime": r.mime, "bytes": r.bytes,
            "owner": rsvc.person_ref(r, "p") or rsvc.group_ref(r, "g"), "source": _doc_source(r.created_by),
            "created_by": r.created_by, "created_at": iso(r.created_at), "updated_at": iso(r.updated_at)}


async def _visible_document(db: AsyncSession, sc: Scope, user: service.CurrentUser, doc_id: uuid.UUID,
                            *, need_write: bool = False) -> Any:
    where, params = _doc_visible_sql(sc, user, need_write=need_write)
    r = (await db.execute(text(_DOC_SELECT + f" AND d.id = :i AND d.org_id = :o AND {where}"),  # noqa: S608
                          {"i": doc_id, "o": user.org_id, **params})).one_or_none()
    if r is None:
        raise not_found("Tài liệu")
    return r


@router.get("/documents")
async def list_documents(owner_person_id: uuid.UUID | None = None, owner_group_id: uuid.UUID | None = None,
                         source: Literal["channel", "agent", "tay"] | None = None, cursor: str | None = None,
                         limit: int = Query(50, ge=1, le=200), user: service.CurrentUser = Depends(READ),
                         db: AsyncSession = DB) -> dict[str, Any]:
    sc = await scope_for(db, user, "profile.read")
    dw, dp = _doc_visible_sql(sc, user)
    conds = [f"d.org_id = :o AND {dw}"]
    if owner_person_id:
        conds.append("d.owner_person_id = :op")
    if owner_group_id:
        conds.append("d.owner_group_id = :og")
    if source:
        conds.append(_SOURCE_SQL[source])
    if cursor:
        conds.append("d.created_at < :c")
    sql_where = " AND ".join(conds)
    base = {"o": user.org_id, "op": owner_person_id, "og": owner_group_id, "c": parse_cursor(cursor), **dp}
    total = (await db.execute(text(f"SELECT count(*) FROM biz.documents d WHERE d.deleted_at IS NULL "  # noqa: S608
                                   f"AND {sql_where}"), base)).scalar_one()
    rows = (await db.execute(text(_DOC_SELECT + f" AND {sql_where} ORDER BY d.created_at DESC LIMIT :n"),  # noqa: S608
                             {**base, "n": limit + 1})).all()
    items = [_doc_payload(r) for r in rows[:limit]]
    return {"items": items, "next_cursor": iso(rows[limit - 1].created_at) if len(rows) > limit else None,
            "total": total}


@router.get("/documents/{document_id}")
async def get_document(document_id: uuid.UUID, user: service.CurrentUser = Depends(READ),
                       db: AsyncSession = DB) -> dict[str, Any]:
    sc = await scope_for(db, user, "profile.read")
    r = await _visible_document(db, sc, user, document_id)
    acl = (await db.execute(text("SELECT principal, can_read, can_write FROM biz.document_acl WHERE document_id = :d"),
                            {"d": document_id})).all()
    out = _doc_payload(r)
    out["acl"] = [{"principal": a.principal, "can_read": a.can_read, "can_write": a.can_write} for a in acl]
    return out


class AclIn(BaseModel):
    principal: str = Field(min_length=1, max_length=200)
    can_read: bool = True
    can_write: bool = False


class DocumentIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    filename: str = Field(min_length=1, max_length=200)
    mime: str = Field(min_length=1, max_length=100)
    content_base64: str = Field(min_length=1, max_length=30_000_000)
    owner_person_id: uuid.UUID | None = None
    owner_group_id: uuid.UUID | None = None
    acl: list[AclIn] = Field(default_factory=list)


@router.post("/documents", status_code=201)
async def create_document(body: DocumentIn, user: service.CurrentUser = Depends(WRITE),
                          db: AsyncSession = DB) -> dict[str, Any]:
    sc = await scope_for(db, user, "profile.write")
    if body.owner_person_id:
        await ensure_person(db, sc, body.owner_person_id)
    if body.owner_group_id:
        await ensure_group(db, sc, body.owner_group_id)
    try:
        data = base64.b64decode(body.content_base64, validate=True)
    except Exception as e:
        raise ApiError(422, "VALIDATION", "Nội dung tệp không phải base64 hợp lệ",
                       errors={"content_base64": "Không hợp lệ"}) from e
    if len(data) > MAX_DOC_BYTES:
        raise ApiError(422, "VALIDATION", "Tệp vượt giới hạn 20MB", errors={"content_base64": "Quá lớn"})
    key = new_key(user.org_id, body.filename)
    await get_object_store().put(key, data)
    row = (await db.execute(text("""
        INSERT INTO biz.documents (org_id, title, description, storage_key, mime, bytes, owner_group_id,
                                   owner_person_id, created_by)
        VALUES (:o, :t, :d, :k, :m, :b, :og, :op, :cb) RETURNING id"""),
        {"o": user.org_id, "t": body.title, "d": body.description, "k": key, "m": body.mime, "b": len(data),
         "og": body.owner_group_id, "op": body.owner_person_id, "cb": user.actor_id})).one()
    acl_rows = [(row.id, f"role:{rbac.OWNER}", True, True), (row.id, user.actor_id, True, True)]
    acl_rows += [(row.id, a.principal, a.can_read, a.can_write) for a in body.acl]
    for did, principal, can_read, can_write in acl_rows:
        await db.execute(text("""INSERT INTO biz.document_acl (document_id, principal, can_read, can_write)
                                 VALUES (:d, :p, :r, :w)
                                 ON CONFLICT (document_id, principal) DO UPDATE
                                   SET can_read = EXCLUDED.can_read, can_write = EXCLUDED.can_write"""),
                         {"d": did, "p": principal, "r": can_read, "w": can_write})
    await actionlog.record(db, org_id=user.org_id, actor_type="user", actor_id=user.actor_id,
                           action="document.uploaded", target_type="document", target_id=str(row.id),
                           target_label=body.title, result="ok",
                           detail={"bytes": len(data), "mime": body.mime, "hash": content_hash(data)}, ip=user.ip)
    return await get_document(row.id, user, db)


# F-5: chỉ các định dạng không chạy được mã mới hiển thị inline; còn lại (html, svg, xml, js, text…) buộc tải
# xuống dạng octet-stream. Thi hành lúc PHỤC VỤ để cả dòng cũ (mime tuỳ ý đã lưu) cũng an toàn.
INLINE_SAFE_MIME = frozenset({"application/pdf", "image/png", "image/jpeg", "image/webp", "image/gif"})

#: CSP mặc định cho mọi tệp tài liệu: tài liệu bị sandbox (nguồn gốc rỗng, không script/plugin), không nạp gì thêm.
DOC_CSP = "sandbox; default-src 'none'"
#: v0.1.35: PDF inline KHÔNG được sandbox — trình xem PDF của Chromium là plugin (MimeHandlerView) nhúng cùng URL;
#: tài liệu bị sandbox không được nạp plugin và `object-src 'none'` chặn chính trình xem → "This page has been
#: blocked by Chrome"/trang trắng. Vẫn chặn mọi nạp khác (`default-src 'none'`), chỉ cho nhúng chính nó
#: (`object-src 'self'`), không cho nhúng vào khung trang khác; nosniff + CORP giữ nguyên.
PDF_CSP = "default-src 'none'; object-src 'self'; frame-ancestors 'none'"


_EXT_RE = re.compile(r"\.([A-Za-z0-9]{1,10})$")


def _download_name(title: str, mime: str, storage_key: str | None) -> str:
    """Tên tệp tải về = `title` + đuôi của tệp gốc (nếu `title` chưa có đúng đuôi đó).

    `title` là tên người dùng gõ, thường không có đuôi ("Hợp đồng khung"); tệp gửi dạng octet-stream thì trình
    duyệt không tự thêm đuôi → máy không mở được. Đuôi lấy từ phần cuối `storage_key` (`new_key` giữ tên tệp gốc,
    vd `…/hd.docx`), không có thì đoán từ MIME đã lưu.
    """
    ext = ""
    if storage_key:
        hit = _EXT_RE.search(storage_key.rsplit("/", 1)[-1])
        if hit:
            ext = "." + hit.group(1).lower()
    if not ext and mime:
        ext = (mimetypes.guess_extension(mime, strict=False) or "").lower()
    name = title.strip() or "tep"
    if ext and not name.lower().endswith(ext):
        name += ext
    return name


def _content_headers(mime: str | None, title: str, storage_key: str | None = None) -> tuple[str, dict[str, str]]:
    m = (mime or "").split(";")[0].strip().lower()
    inline = m in INLINE_SAFE_MIME
    # Header HTTP chỉ nhận latin-1 — tên tài liệu tiếng Việt phải mã hoá theo RFC 5987 (filename*=UTF-8''…).
    filename = urllib.parse.quote(_download_name(title, m, storage_key))
    disposition = "inline" if inline else "attachment"
    return (m if inline else "application/octet-stream"), {
        "Content-Disposition": f"{disposition}; filename*=UTF-8''{filename}",
        "Content-Security-Policy": PDF_CSP if inline and m == "application/pdf" else DOC_CSP,
        "X-Content-Type-Options": "nosniff",
        "Cross-Origin-Resource-Policy": "same-origin",
        "Cache-Control": "private, no-store",
    }


@router.get("/documents/{document_id}/content")
async def download_document(document_id: uuid.UUID, user: service.CurrentUser = Depends(READ),
                            db: AsyncSession = DB) -> Response:
    sc = await scope_for(db, user, "profile.read")
    r = await _visible_document(db, sc, user, document_id)
    try:
        data = await get_object_store().get(r.storage_key)
    except ObjectNotFound as e:
        raise not_found("Tệp") from e
    await actionlog.record(db, org_id=user.org_id, actor_type="user", actor_id=user.actor_id,
                           action="document.downloaded", target_type="document", target_id=str(document_id),
                           target_label=r.title, result="ok", ip=user.ip)
    media_type, headers = _content_headers(r.mime, r.title, r.storage_key)
    return Response(content=data, media_type=media_type, headers=headers)


class DocumentPatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    owner_person_id: uuid.UUID | None = None
    owner_group_id: uuid.UUID | None = None


@router.patch("/documents/{document_id}")
async def patch_document(document_id: uuid.UUID, body: DocumentPatch, user: service.CurrentUser = Depends(WRITE),
                         db: AsyncSession = DB) -> dict[str, Any]:
    sc = await scope_for(db, user, "profile.write")
    await _visible_document(db, sc, user, document_id, need_write=True)
    fields: list[str] = []
    params: dict[str, Any] = {"i": document_id}
    if body.title is not None:
        fields.append("title = :title")
        params["title"] = body.title
    if "description" in body.model_fields_set:
        fields.append("description = :description")
        params["description"] = body.description
    if "owner_person_id" in body.model_fields_set:
        if body.owner_person_id:
            await ensure_person(db, sc, body.owner_person_id)
        fields.append("owner_person_id = :op")
        params["op"] = body.owner_person_id
    if "owner_group_id" in body.model_fields_set:
        if body.owner_group_id:
            await ensure_group(db, sc, body.owner_group_id)
        fields.append("owner_group_id = :og")
        params["og"] = body.owner_group_id
    if fields:
        fields.append("updated_at = now()")
        await db.execute(text(f"UPDATE biz.documents SET {', '.join(fields)} WHERE id = :i"), params)  # noqa: S608
        await actionlog.record(db, org_id=user.org_id, actor_type="user", actor_id=user.actor_id,
                               action="document.updated", target_type="document", target_id=str(document_id),
                               result="ok", ip=user.ip)
    return await get_document(document_id, user, db)


@router.put("/documents/{document_id}/acl")
async def put_document_acl(document_id: uuid.UUID, body: list[AclIn], user: service.CurrentUser = Depends(WRITE),
                           db: AsyncSession = DB) -> dict[str, Any]:
    sc = await scope_for(db, user, "profile.write")
    await _visible_document(db, sc, user, document_id, need_write=True)
    await db.execute(text("DELETE FROM biz.document_acl WHERE document_id = :d"), {"d": document_id})
    for a in body:
        await db.execute(text("""INSERT INTO biz.document_acl (document_id, principal, can_read, can_write)
                                 VALUES (:d, :p, :r, :w)"""),
                         {"d": document_id, "p": a.principal, "r": a.can_read, "w": a.can_write})
    await actionlog.record(db, org_id=user.org_id, actor_type="user", actor_id=user.actor_id,
                           action="document.acl_updated", target_type="document", target_id=str(document_id),
                           result="ok", detail={"acl": [a.model_dump() for a in body]}, ip=user.ip)
    return await get_document(document_id, user, db)


@router.delete("/documents/{document_id}", status_code=204)
async def delete_document(document_id: uuid.UUID, user: service.CurrentUser = Depends(WRITE),
                          db: AsyncSession = DB) -> Response:
    sc = await scope_for(db, user, "profile.write")
    r = await _visible_document(db, sc, user, document_id, need_write=True)
    await db.execute(text("UPDATE biz.documents SET deleted_at = now() WHERE id = :i"), {"i": document_id})
    try:
        await get_object_store().delete(r.storage_key)
    except Exception:  # noqa: BLE001 — xoá blob là best-effort, dòng metadata đã xoá mềm là nguồn sự thật
        pass
    await actionlog.record(db, org_id=user.org_id, actor_type="user", actor_id=user.actor_id,
                           action="document.deleted", target_type="document", target_id=str(document_id),
                           target_label=r.title, result="ok", ip=user.ip)
    return Response(status_code=204)


# ═══ chứng cứ: điểm của người/nhóm dùng chung registry "score" của core (đã đăng ký) ════════════════════════
