"""API Bản đồ quan hệ (docs/api/phase-3-graph.md): 4 chế độ trên **một** màn (`graph`) — danh sách + bộ lọc
mạnh, đồ thị Người↔Người, đồ thị Nhóm↔Nhóm, Luồng chủ đề — cộng vị trí node đã lưu và một điểm bấm tay để
dựng lại đồ thị. Dữ liệu cạnh (`clean.relationships`) do `gh.biz.graph.jobs.recompute_org` tính — xem đó để
biết công thức trọng số/cầu nối/lạnh."""

import uuid
from datetime import datetime
from typing import Any, Literal

import orjson
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gh.auth import rbac, service
from gh.auth.deps import require
from gh.biz.core.scope import scope_for
from gh.biz.graph import jobs as gjobs
from gh.biz.graph.service import MAX_NODES, build_graph
from gh.chassis import actionlog
from gh.data.common import iso, mask_text, parse_cursor
from gh.db import DB

router = APIRouter(tags=["graph"])

READ = require("profile.read")
WRITE = require("profile.write")

GraphMode = Literal["people", "groups", "topics"]
CAND_EDGES = 3000                                # đủ lớn để bộ dựng đồ thị (build_graph) có chỗ chọn top-weight


def _cold(last_at: datetime | None) -> str:
    return "cold" if gjobs.edge_state(last_at) == "cold" else "active"


# ═══ danh sách (bộ lọc mạnh) ═════════════════════════════════════════════════

_LIST_CTE = """
WITH rows AS (
  SELECT p.id, p.code, p.display_name AS name, p.person_type AS type, p.organization_name AS org_name,
         p.owner_user_id, p.created_at, p.relation_to_owner AS relation,
         cs_heat.value AS heat, cs_pot.value AS potential, cs_risk.value AS churn_risk,
         la.last_at AS last_interaction_at,
         COALESCE(deg.n, 0) AS degree, COALESCE(deg.total_weight, 0) AS total_weight,
         COALESCE(br.bridge_score, 0) AS bridge_score,
         COALESCE(ch.channels, '{{}}') AS channels
  FROM core.persons p
  LEFT JOIN clean.current_scores cs_heat ON cs_heat.subject_type = 'person' AND cs_heat.subject_id = p.id
                                            AND cs_heat.dimension = 'heat'
  LEFT JOIN clean.current_scores cs_pot ON cs_pot.subject_type = 'person' AND cs_pot.subject_id = p.id
                                           AND cs_pot.dimension = 'potential'
  LEFT JOIN clean.current_scores cs_risk ON cs_risk.subject_type = 'person' AND cs_risk.subject_id = p.id
                                            AND cs_risk.dimension = 'churn_risk'
  LEFT JOIN LATERAL (
    SELECT max(e.occurred_at) AS last_at FROM raw.events e
    JOIN core.person_identities pi2 ON pi2.id = e.sender_identity_id
    WHERE pi2.person_id = p.id AND e.direction = 'inbound'
  ) la ON true
  LEFT JOIN LATERAL (
    SELECT count(*) AS n, sum(r.weight) AS total_weight FROM clean.relationships r
    WHERE r.org_id = p.org_id AND ((r.from_type = 'person' AND r.from_id = p.id)
                                    OR (r.to_type = 'person' AND r.to_id = p.id))
  ) deg ON true
  LEFT JOIN LATERAL (
    SELECT max(r.weight) AS bridge_score FROM clean.relationships r
    WHERE r.org_id = p.org_id AND r.kind = 'bridges' AND r.from_type = 'person' AND r.from_id = p.id
  ) br ON true
  LEFT JOIN LATERAL (
    SELECT array_agg(DISTINCT ch2.type) AS channels FROM core.person_identities pi3
    JOIN core.channels ch2 ON ch2.id = pi3.channel_id WHERE pi3.person_id = p.id
  ) ch ON true
  WHERE p.org_id = :o AND p.deleted_at IS NULL AND p.merged_into_id IS NULL AND {scope}
)
"""


def _list_row(r: Any, *, owner: bool) -> dict[str, Any]:
    return {"id": str(r.id), "code": r.code, "name": mask_text(r.name, owner), "type": r.type,
            "org_name": r.org_name, "relation": r.relation, "channels": list(r.channels or []),
            "heat": float(r.heat) if r.heat is not None else None,
            "potential": float(r.potential) if r.potential is not None else None,
            "risk": float(r.churn_risk) if r.churn_risk is not None else None,
            "owner_user_id": str(r.owner_user_id) if r.owner_user_id else None,
            "last_interaction_at": iso(r.last_interaction_at), "state": _cold(r.last_interaction_at),
            "degree": r.degree, "total_weight": float(r.total_weight) if r.total_weight else 0.0,
            "bridge_score": int(r.bridge_score or 0)}


@router.get("/graph/list")
async def list_graph(type: str | None = None, channel: str | None = None,  # noqa: A002
                     heat: Literal["high", "mid", "cold"] | None = None,
                     potential: Literal["high", "mid", "low"] | None = None,
                     risk: Literal["high", "mid", "low"] | None = None,
                     owner_user_id: uuid.UUID | None = None, state: Literal["active", "cold"] | None = None,
                     relation: str | None = None, cursor: str | None = None,
                     limit: int = Query(50, ge=1, le=200), user: service.CurrentUser = Depends(READ),
                     db: AsyncSession = DB) -> dict[str, Any]:
    """Chế độ **danh sách**: 8 bộ lọc của spec (loại người, kênh, độ nóng, tiềm năng, rủi ro, người phụ trách,
    thời gian tương tác gần nhất [= `state`], giai đoạn quan hệ [= `relation`, `core.persons.relation_to_owner`
    — quyết định tự đưa ra: spec không định nghĩa "giai đoạn quan hệ" là trường nào, `relation_to_owner`
    (direct/via_staff/stranger/staff) là trường gần nghĩa nhất đã có, tránh thêm cột trùng lặp)."""
    sc = await scope_for(db, user, "profile.read")
    where, params = sc.person_sql("p")
    cte = _LIST_CTE.format(scope=where)
    conds = ["TRUE"]
    if type:
        conds.append("type = :type")
    if channel:
        conds.append(":channel = ANY(channels)")
    if relation:
        conds.append("relation = :relation")
    if heat == "high":
        conds.append("heat >= 80")
    elif heat == "mid":
        conds.append("heat >= 50 AND heat < 80")
    elif heat == "cold":
        conds.append("(heat IS NULL OR heat < 50)")
    if potential == "high":
        conds.append("potential >= 80")
    elif potential == "mid":
        conds.append("potential >= 50 AND potential < 80")
    elif potential == "low":
        conds.append("(potential IS NULL OR potential < 50)")
    if risk == "high":
        conds.append("churn_risk >= 80")
    elif risk == "mid":
        conds.append("churn_risk >= 50 AND churn_risk < 80")
    elif risk == "low":
        conds.append("(churn_risk IS NULL OR churn_risk < 50)")
    if owner_user_id:
        conds.append("owner_user_id = :owner")
    if state == "active":
        conds.append("last_interaction_at IS NOT NULL AND last_interaction_at > now() - interval '30 days'")
    elif state == "cold":
        conds.append("(last_interaction_at IS NULL OR last_interaction_at <= now() - interval '30 days')")
    if cursor:
        conds.append("created_at < :c")
    filt = " AND ".join(conds)
    base = {"o": user.org_id, "type": type, "channel": channel, "relation": relation, "owner": owner_user_id,
            "c": parse_cursor(cursor), **params}
    total = (await db.execute(text(cte + f" SELECT count(*) FROM rows WHERE {filt}"), base)).scalar_one()  # noqa: S608
    rows = (await db.execute(text(cte + f" SELECT * FROM rows WHERE {filt} ORDER BY created_at DESC LIMIT :n"),  # noqa: S608
                             {**base, "n": limit + 1})).all()
    owner = user.role_code == rbac.OWNER
    items = [_list_row(r, owner=owner) for r in rows[:limit]]
    return {"items": items, "next_cursor": iso(rows[limit - 1].created_at) if len(rows) > limit else None,
            "total": total}


# ═══ Người↔Người ══════════════════════════════════════════════════════════════

_PEOPLE_EDGE_SELECT = """
SELECT r.from_id AS a_id, r.to_id AS b_id, r.weight, r.interactions, r.last_at, r.state, r.topic,
       pa.code AS a_code, pa.display_name AS a_name, pa.person_type AS a_type,
       pb.code AS b_code, pb.display_name AS b_name, pb.person_type AS b_type
FROM clean.relationships r
JOIN core.persons pa ON pa.id = r.from_id JOIN core.persons pb ON pb.id = r.to_id
WHERE r.org_id = :o AND r.kind = 'interacts' AND pa.deleted_at IS NULL AND pb.deleted_at IS NULL
  AND pa.merged_into_id IS NULL AND pb.merged_into_id IS NULL AND {scope_a} AND {scope_b} {extra}
ORDER BY r.weight DESC LIMIT :cand
"""


async def _people_graph(db: AsyncSession, user: service.CurrentUser, *, node_id: uuid.UUID | None,
                        min_weight: float, topic: str | None, node_limit: int) -> dict[str, Any]:
    sc = await scope_for(db, user, "profile.read")
    wa, pa_params = sc.person_sql("pa")
    wb, pb_params = sc.person_sql("pb")
    extra = ""
    params: dict[str, Any] = {"o": user.org_id, "cand": CAND_EDGES, **pa_params, **pb_params}
    if node_id:
        extra += " AND (r.from_id = :focus OR r.to_id = :focus)"
        params["focus"] = node_id
    if min_weight:
        extra += " AND r.weight >= :mw"
        params["mw"] = min_weight
    if topic:
        extra += " AND r.topic = :topic"
        params["topic"] = topic
    sql = _PEOPLE_EDGE_SELECT.format(scope_a=wa, scope_b=wb, extra=extra)
    total_edges = (await db.execute(text(f"""
        SELECT count(*) FROM clean.relationships r
        JOIN core.persons pa ON pa.id = r.from_id JOIN core.persons pb ON pb.id = r.to_id
        WHERE r.org_id = :o AND r.kind = 'interacts' AND pa.deleted_at IS NULL AND pb.deleted_at IS NULL
          AND pa.merged_into_id IS NULL AND pb.merged_into_id IS NULL AND {wa} AND {wb} {extra}"""),  # noqa: S608
        params)).scalar_one()
    rows = (await db.execute(text(sql), params)).all()  # noqa: S608 — {scope_a}/{scope_b}/{extra} là biểu thức nội bộ
    owner = user.role_code == rbac.OWNER

    def node_a(r: Any) -> dict[str, Any]:
        return {"id": str(r.a_id), "code": r.a_code, "name": mask_text(r.a_name, owner), "type": r.a_type}

    def node_b(r: Any) -> dict[str, Any]:
        return {"id": str(r.b_id), "code": r.b_code, "name": mask_text(r.b_name, owner), "type": r.b_type}

    def edge_of(r: Any) -> dict[str, Any]:
        return {"from": str(r.a_id), "to": str(r.b_id), "weight": float(r.weight), "interactions": r.interactions,
                "last_at": iso(r.last_at), "state": r.state, "topic": r.topic}

    nodes, edges, truncated = build_graph(rows, node_a, node_b, edge_of, node_limit)
    out = {"nodes": nodes, "edges": edges, "node_limit": node_limit, "total_edges": total_edges,
           "truncated": truncated}
    if truncated:
        out["hint"] = "Quá nhiều node cho một lượt — đang hiển thị theo trọng số cạnh cao nhất; thu hẹp bằng " \
                      "node_id (xem quanh một người) hoặc min_weight để thấy hết"
    return out


@router.get("/graph/people")
async def graph_people(node_id: uuid.UUID | None = None, min_weight: float = 0.0,
                       node_limit: int = Query(MAX_NODES, ge=1, le=MAX_NODES),
                       user: service.CurrentUser = Depends(READ), db: AsyncSession = DB) -> dict[str, Any]:
    return await _people_graph(db, user, node_id=node_id, min_weight=min_weight, topic=None, node_limit=node_limit)


# ═══ Nhóm↔Nhóm ════════════════════════════════════════════════════════════════

_GROUPS_EDGE_SELECT = """
SELECT r.from_id AS a_id, r.to_id AS b_id, r.weight, r.interactions, r.last_at, r.state,
       ga.code AS a_code, ga.name AS a_name, ga.kind AS a_kind, ga.member_count AS a_members,
       gb.code AS b_code, gb.name AS b_name, gb.kind AS b_kind, gb.member_count AS b_members,
       (SELECT array_agg(DISTINCT p.code) FROM clean.relationships b1 JOIN clean.relationships b2
          ON b2.from_id = b1.from_id AND b2.kind = 'bridges' AND b2.to_id = r.to_id
        JOIN core.persons p ON p.id = b1.from_id
        WHERE b1.kind = 'bridges' AND b1.to_id = r.from_id) AS bridge_person_codes
FROM clean.relationships r
JOIN core.groups ga ON ga.id = r.from_id JOIN core.groups gb ON gb.id = r.to_id
WHERE r.org_id = :o AND r.kind = 'shares_members' AND {scope_a} AND {scope_b} {extra}
ORDER BY r.weight DESC LIMIT :cand
"""


@router.get("/graph/groups")
async def graph_groups(node_id: uuid.UUID | None = None, min_weight: float = 0.0,
                       node_limit: int = Query(MAX_NODES, ge=1, le=MAX_NODES),
                       user: service.CurrentUser = Depends(READ), db: AsyncSession = DB) -> dict[str, Any]:
    sc = await scope_for(db, user, "profile.read")
    wa, pa_params = sc.group_sql("ga")
    wb, pb_params = sc.group_sql("gb")
    extra = ""
    params: dict[str, Any] = {"o": user.org_id, "cand": CAND_EDGES, **pa_params, **pb_params}
    if node_id:
        extra += " AND (r.from_id = :focus OR r.to_id = :focus)"
        params["focus"] = node_id
    if min_weight:
        extra += " AND r.weight >= :mw"
        params["mw"] = min_weight
    sql = _GROUPS_EDGE_SELECT.format(scope_a=wa, scope_b=wb, extra=extra)
    total_edges = (await db.execute(text(f"""
        SELECT count(*) FROM clean.relationships r
        JOIN core.groups ga ON ga.id = r.from_id JOIN core.groups gb ON gb.id = r.to_id
        WHERE r.org_id = :o AND r.kind = 'shares_members' AND {wa} AND {wb} {extra}"""),  # noqa: S608
        params)).scalar_one()
    rows = (await db.execute(text(sql), params)).all()  # noqa: S608 — {scope_a}/{scope_b}/{extra} là biểu thức nội bộ

    def node_a(r: Any) -> dict[str, Any]:
        return {"id": str(r.a_id), "code": r.a_code, "name": r.a_name, "kind": r.a_kind,
                "member_count": r.a_members}

    def node_b(r: Any) -> dict[str, Any]:
        return {"id": str(r.b_id), "code": r.b_code, "name": r.b_name, "kind": r.b_kind,
                "member_count": r.b_members}

    def edge_of(r: Any) -> dict[str, Any]:
        return {"from": str(r.a_id), "to": str(r.b_id), "weight": float(r.weight), "interactions": r.interactions,
                "last_at": iso(r.last_at), "state": r.state,
                "bridge_person_codes": list(r.bridge_person_codes or [])}

    nodes, edges, truncated = build_graph(rows, node_a, node_b, edge_of, node_limit)
    out = {"nodes": nodes, "edges": edges, "node_limit": node_limit, "total_edges": total_edges,
           "truncated": truncated}
    if truncated:
        out["hint"] = "Quá nhiều node cho một lượt — đang hiển thị theo trọng số cạnh cao nhất; thu hẹp bằng " \
                      "node_id hoặc min_weight để thấy hết"
    return out


# ═══ Luồng chủ đề ═════════════════════════════════════════════════════════════

_TOPICS_SQL = """
WITH edges AS (
  SELECT r.topic, r.from_id, r.to_id, r.weight, r.last_at
  FROM clean.relationships r
  JOIN core.persons pa ON pa.id = r.from_id JOIN core.persons pb ON pb.id = r.to_id
  WHERE r.org_id = :o AND r.kind = 'interacts' AND r.topic IS NOT NULL AND pa.deleted_at IS NULL
    AND pb.deleted_at IS NULL AND pa.merged_into_id IS NULL AND pb.merged_into_id IS NULL
    AND {scope_a} AND {scope_b}
),
people AS (SELECT topic, from_id AS person_id FROM edges UNION SELECT topic, to_id FROM edges)
SELECT e.topic, count(*) AS edges, sum(e.weight) AS total_weight, max(e.last_at) AS last_at,
       (SELECT count(DISTINCT p.person_id) FROM people p WHERE p.topic = e.topic) AS people
FROM edges e GROUP BY e.topic ORDER BY sum(e.weight) DESC LIMIT :n
"""


@router.get("/graph/topics")
async def graph_topics(limit: int = Query(50, ge=1, le=200), user: service.CurrentUser = Depends(READ),
                       db: AsyncSession = DB) -> dict[str, Any]:
    """Danh sách luồng chủ đề — mỗi luồng gộp mọi cạnh `interacts` cùng `topic` (sản phẩm nhắc nhiều nhất
    trong (các) nhóm chung của cặp người, xem `gh.biz.graph.jobs`). Bấm vào một luồng: `GET
    /graph/topics/{topic}` để xem đồ thị người tham gia luồng đó."""
    sc = await scope_for(db, user, "profile.read")
    wa, pa_params = sc.person_sql("pa")
    wb, pb_params = sc.person_sql("pb")
    rows = (await db.execute(text(_TOPICS_SQL.format(scope_a=wa, scope_b=wb)),
                             {"o": user.org_id, "n": limit, **pa_params, **pb_params})).all()
    return {"items": [{"topic": r.topic, "edges": r.edges, "people": r.people,
                       "total_weight": float(r.total_weight), "last_at": iso(r.last_at),
                       "state": _cold(r.last_at)} for r in rows]}


@router.get("/graph/topics/{topic}")
async def graph_topic_thread(topic: str, node_limit: int = Query(MAX_NODES, ge=1, le=MAX_NODES),
                             user: service.CurrentUser = Depends(READ), db: AsyncSession = DB) -> dict[str, Any]:
    return await _people_graph(db, user, node_id=None, min_weight=0.0, topic=topic, node_limit=node_limit)


# ═══ vị trí node đã lưu ═══════════════════════════════════════════════════════

def _layout_name(mode: GraphMode) -> str:
    return f"layout:{mode}"


@router.get("/graph/layout/{mode}")
async def get_layout(mode: GraphMode, user: service.CurrentUser = Depends(READ),
                     db: AsyncSession = DB) -> dict[str, Any]:
    row = (await db.execute(text("""SELECT filters FROM ops.saved_views
                                     WHERE user_id = :u AND screen = 'graph' AND name = :n"""),
                            {"u": user.id, "n": _layout_name(mode)})).scalar_one_or_none()
    return {"positions": (row or {}).get("positions", {})}


class Position(BaseModel):
    x: float
    y: float


class LayoutIn(BaseModel):
    positions: dict[str, Position] = Field(default_factory=dict)


@router.put("/graph/layout/{mode}")
async def put_layout(mode: GraphMode, body: LayoutIn, user: service.CurrentUser = Depends(WRITE),
                     db: AsyncSession = DB) -> dict[str, Any]:
    """Lưu vị trí node đã kéo trên đồ thị — tái dùng `ops.saved_views` (đã có sẵn cho "góc nhìn đã lưu", `screen
    + filters jsonb`) thay vì thêm bảng mới: một tên riêng theo quy ước (`layout:<mode>`) giữ vị trí như một góc
    nhìn không tên hiển thị, autosave (ghi đè, không kiểm trùng tên như `POST /views`). Quyết định tự đưa ra vì
    spec chỉ nói "lưu vị trí", không nói bảng nào."""
    filters = {"positions": {k: v.model_dump() for k, v in body.positions.items()}}
    await db.execute(text("""
        INSERT INTO ops.saved_views (org_id, user_id, screen, name, filters)
        VALUES (:o, :u, 'graph', :n, CAST(:f AS jsonb))
        ON CONFLICT (user_id, screen, lower(name)) DO UPDATE SET filters = EXCLUDED.filters"""),
        {"o": user.org_id, "u": user.id, "n": _layout_name(mode), "f": orjson.dumps(filters).decode()})
    await actionlog.record(db, org_id=user.org_id, actor_type="user", actor_id=user.actor_id,
                           action="graph.layout_saved", target_type="graph", target_id=mode, result="ok",
                           detail={"nodes": len(body.positions)}, ip=user.ip)
    return {"ok": True}


# ═══ dựng lại đồ thị (tay) ════════════════════════════════════════════════════

@router.post("/graph/recompute")
async def recompute(user: service.CurrentUser = Depends(WRITE), db: AsyncSession = DB) -> dict[str, Any]:
    """Dựng lại `clean.relationships` ngay theo yêu cầu (ngoài lịch chạy nền `gh.biz.graph.jobs.graph_recompute`
    mỗi 15 phút) — dùng khi vừa nạp dữ liệu và muốn thấy đồ thị mới nhất ngay, không đợi lượt quét kế tiếp."""
    counts = await gjobs.recompute_org(db, user.org_id)
    await actionlog.record(db, org_id=user.org_id, actor_type="user", actor_id=user.actor_id,
                           action="graph.recomputed", target_type="graph", target_id="all", result="ok",
                           detail=counts, ip=user.ip)
    return {"ok": True, "counts": counts}
