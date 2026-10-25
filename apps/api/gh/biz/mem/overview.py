# ruff: noqa: E501
"""Tầng "Toàn cảnh" và "Bối cảnh" của thang 4 tầng + tín hiệu của một mục Hồ sơ (v0.1.76, gói T9 — spec r6 §7.2b, INT-18).

Riêng tư (một cổng với `/signals`, `gh.biz.intel.access`):
- tín hiệu: `access.alert_visible_sql` — `console_pin` (nhân sự, đánh giá người) chỉ Owner có PIN; nhóm «Chỉ Sếp»/«Sếp + quản lý» theo `view_scope`
  (cả `group_id`, chủ thể nhóm lẫn bằng chứng). Số bị ẩn do thiếu PIN báo qua `hidden_pin_n` CHỈ cho Owner (vai khác: 0, không lộ số lượng).
- nhóm: chỉ nhóm vai này được xem (`group_view_sql`); tên/độ nóng nhóm khác không bao giờ vào kết quả.
- vòng người (ego ring): CHỈ Owner; bối cảnh có `people_eval_privacy='console_pin'` (nhân viên…) cần PIN. Vòng 2 chỉ đếm, không lộ tên.
- ask: chỉ Owner; ask về người cần PIN (`access.ask_visible_sql`).
Mọi truy vấn kèm `org_id` của người gọi. Không ghi gì (GET thuần đọc).
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gh.auth import rbac, service
from gh.biz.core.scope import group_view_sql
from gh.biz.intel import access
from gh.biz.mem import heat
from gh.errors import not_found

UNCONFIRMED = "chua_xac_nhan"
UNCONFIRMED_LABEL = "Chưa xác nhận bối cảnh"
OPEN = ("open", "acknowledged")
OPPORTUNITY_KINDS = ("co_hoi_ket_noi", "thi_truong_thong_tin")
RISK_KINDS = ("rui_ro_lua_dao", "suc_khoe_an_toan", "xung_dot_khieu_nai")
LEVEL_RANK = {"red": 0, "amber": 1, "grey": 2, "off": 3}
RING_WEEKS = 4
HOTSPOTS = 3
LIST_MAX = 50


def _crumb(tier: str, id_: Any, label: str) -> dict[str, str]:
    return {"tier": tier, "id": str(id_), "label": label}


def _is_owner(user: service.CurrentUser) -> bool:
    return user.role_code == rbac.OWNER


async def _owner_entity(db: AsyncSession, org_id: uuid.UUID) -> uuid.UUID | None:
    return (await db.execute(text("SELECT id FROM mem.entities WHERE org_id = :o AND is_owner"), {"o": org_id})).scalar_one_or_none()  # type: ignore[no-any-return]


async def _context_types(db: AsyncSession) -> list[tuple[str, str]]:
    rows = (await db.execute(text("SELECT code, label_vi FROM mem.context_types ORDER BY code"))).all()
    return [(r.code, r.label_vi) for r in rows] + [(UNCONFIRMED, UNCONFIRMED_LABEL)]


async def _visible_groups(db: AsyncSession, user: service.CurrentUser) -> dict[uuid.UUID, dict[str, Any]]:
    """Nhóm vai này được xem (view_scope) cùng các nhãn bối cảnh Sếp đã xác nhận (không có ⇒ `chua_xac_nhan`)."""
    rows = (await db.execute(text(f"""
        SELECT g.id, g.name, g.kind, g.member_count,
               coalesce((SELECT array_agg(gc.context ORDER BY gc.context) FROM mem.group_contexts gc
                         WHERE gc.org_id = g.org_id AND gc.group_id = g.id AND gc.source = 'owner'), '{{}}') AS ctx
        FROM core.groups g WHERE g.org_id = :o AND {group_view_sql(user.role_code, 'g.id')}"""), {"o": user.org_id})).all()  # noqa: S608
    return {r.id: {"id": r.id, "name": r.name, "kind": r.kind, "member_count": r.member_count, "ctx": list(r.ctx) or [UNCONFIRMED]} for r in rows}


async def _hidden_pin(db: AsyncSession, user: service.CurrentUser, where: str, params: dict[str, Any]) -> int:
    """Số tín hiệu `console_pin` bị ẩn vì chưa PIN — chỉ báo cho Owner chưa PIN (vai khác: 0)."""
    if not _is_owner(user) or access.can_see_private(user):
        return 0
    return int((await db.execute(text(f"SELECT count(*) FROM biz.alerts a WHERE {where} AND a.privacy = 'console_pin'"), params)).scalar_one())  # noqa: S608


def _week_list(weeks: int, now: datetime) -> list[date]:
    cur = heat.week_start(now)
    return [cur - timedelta(weeks=weeks - 1 - i) for i in range(weeks)]


# ─── Toàn cảnh ───────────────────────────────────────────────────────────────

async def overview(db: AsyncSession, user: service.CurrentUser, weeks: int, now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    org = user.org_id
    wks = _week_list(weeks, now)
    groups = await _visible_groups(db, user)
    gids = list(groups)
    since = datetime.combine(wks[0], datetime.min.time(), tzinfo=heat.VN)
    p = {"o": org, "g": gids, "s": since}
    base = "a.org_id = :o AND a.group_id = ANY(:g) AND a.created_at >= :s AND a.kind NOT IN ('system', 'legacy')"
    rows = (await db.execute(text(f"""
        SELECT a.group_id, (date_trunc('week', a.created_at AT TIME ZONE 'Asia/Ho_Chi_Minh'))::date AS wk, a.level, (a.status = ANY(:op)) AS is_open, count(*) AS n
        FROM biz.alerts a WHERE {base} AND {access.alert_visible_sql(user, 'a')} GROUP BY 1, 2, 3, 4"""), {**p, "op": list(OPEN)})).all()  # noqa: S608
    by_group: dict[uuid.UUID, dict[date, dict[str, int]]] = {}
    for r in rows:
        cell = by_group.setdefault(r.group_id, {}).setdefault(r.wk, {"red": 0, "amber": 0, "grey": 0, "off": 0, "open_items": 0})
        cell[r.level] = cell.get(r.level, 0) + int(r.n)
        if r.is_open:
            cell["open_items"] += int(r.n)
    heat_by: dict[uuid.UUID, dict[date, float]] = {}
    owner_ent = await _owner_entity(db, org)
    if owner_ent is not None and gids:
        for r in (await db.execute(text("""
            SELECT e.group_id, h.week, h.heat FROM mem.relation_heat_weekly h JOIN mem.entities e ON e.id = h.entity_b
            WHERE h.org_id = :o AND h.entity_a = :a AND e.group_id = ANY(:g) AND h.week >= :w"""),
                                  {"o": org, "a": owner_ent, "g": gids, "w": wks[0]})).all():
            heat_by.setdefault(r.group_id, {})[r.week] = float(r.heat)
    contexts = []
    for code, label in await _context_types(db):
        members = [g for g in groups.values() if code in g["ctx"]]
        cells = []
        for w in wks:
            agg = {"red": 0, "amber": 0, "grey": 0, "off": 0, "open_items": 0}
            for g in members:
                for k, v in by_group.get(g["id"], {}).get(w, {}).items():
                    agg[k] += v
            hv = sum(heat_by.get(g["id"], {}).get(w, 0.0) for g in members)
            cells.append({"week": w.isoformat(), **agg, "heat_avg": round(hv / len(members), 2) if members else 0.0})
        spots = []
        for g in members:
            tot = {"red": 0, "amber": 0, "open_items": 0}
            for cell in by_group.get(g["id"], {}).values():
                for k in tot:
                    tot[k] += cell[k]
            score = tot["red"] * 3 + tot["amber"] * 2 + tot["open_items"]
            if score > 0:
                spots.append((score, {"group_id": str(g["id"]), "label": g["name"], **tot,
                                      "heat_last": heat_by.get(g["id"], {}).get(wks[-1], 0.0)}))
        spots.sort(key=lambda x: (-x[0], x[1]["label"]))
        contexts.append({"code": code, "label": label, "weeks": cells, "hotspots": [s for _, s in spots[:HOTSPOTS]]})
    asks_open, asks_hidden = 0, 0
    if _is_owner(user):
        ab = """FROM ops.asks q LEFT JOIN biz.alerts a ON a.id = q.signal_id AND a.org_id = q.org_id
                WHERE q.org_id = :o AND q.state = 'open' AND (q.expires_at IS NULL OR q.expires_at > now())"""
        vis = access.ask_visible_sql(user, "q", "a")
        asks_open = int((await db.execute(text(f"SELECT count(*) {ab} AND {vis}"), {"o": org})).scalar_one())  # noqa: S608
        asks_hidden = int((await db.execute(text(f"SELECT count(*) {ab} AND NOT {vis}"), {"o": org})).scalar_one())  # noqa: S608
    return {"breadcrumb": [_crumb("overview", "overview", "Toàn cảnh")], "weeks": weeks, "contexts": contexts, "asks_open_n": asks_open,
            "asks_hidden_pin_n": asks_hidden, "hidden_pin_n": await _hidden_pin(db, user, base, p)}


# ─── Bối cảnh ────────────────────────────────────────────────────────────────

async def _ring(db: AsyncSession, user: service.CurrentUser, code: str, gids: list[uuid.UUID], now: datetime) -> dict[str, Any]:
    hidden = {"visible": False, "hidden_pin": False, "ring1": [], "ring2_n": 0}
    if not _is_owner(user):
        return hidden
    pe = (await db.execute(text("SELECT policy->>'people_eval_privacy' FROM mem.context_types WHERE code = :c"), {"c": code})).scalar_one_or_none()
    if pe == "console_pin" and not access.can_see_private(user):
        return {**hidden, "hidden_pin": True}
    owner_ent = await _owner_entity(db, user.org_id)
    persons = (await db.execute(text("""SELECT DISTINCT gm.person_id FROM core.group_members gm JOIN core.groups g ON g.id = gm.group_id
                                        WHERE g.org_id = :o AND gm.group_id = ANY(:g) AND gm.left_at IS NULL"""),
                                {"o": user.org_id, "g": gids})).scalars().all() if gids else []
    ents = (await db.execute(text("""SELECT id, display_name, person_id FROM mem.entities WHERE org_id = :o AND person_id = ANY(:p)
                                     AND forgotten_at IS NULL AND merged_into IS NULL AND NOT is_owner"""), {"o": user.org_id, "p": list(persons)})).all() if persons else []
    ring1: list[dict[str, Any]] = []
    if owner_ent is not None and ents:
        ser = await heat.series_many(db, user.org_id, owner_ent, [e.id for e in ents], RING_WEEKS, now=now)
        for e in ents:
            line = ser.get(e.id) or []
            if any(x["heat"] > 0 for x in line):
                last = next(x for x in reversed(line) if x["heat"] > 0)
                shared = int((await db.execute(text("""SELECT count(*) FROM core.group_members WHERE person_id = :p AND group_id = ANY(:g) AND left_at IS NULL"""),
                                               {"p": e.person_id, "g": gids})).scalar_one())
                ring1.append({"entity_id": str(e.id), "label": e.display_name, "heat": last["heat"], "state": last["state"], "shared_groups": shared})
    ring1.sort(key=lambda x: (-x["heat"], x["label"]))
    owner_person = (await db.execute(text("SELECT person_id FROM mem.entities WHERE org_id = :o AND is_owner"), {"o": user.org_id})).scalar_one_or_none()
    in1 = {e.person_id for e in ents if str(e.id) in {x["entity_id"] for x in ring1}}
    ring2 = set(persons) - in1 - ({owner_person} if owner_person else set())
    return {"visible": True, "hidden_pin": False, "ring1": ring1[:20], "ring2_n": len(ring2)}


async def context_detail(db: AsyncSession, user: service.CurrentUser, code: str, now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    types = dict(await _context_types(db))
    if code not in types:
        raise not_found("Bối cảnh")
    groups = {gid: g for gid, g in (await _visible_groups(db, user)).items() if code in g["ctx"]}
    gids = list(groups)
    base = "a.org_id = :o AND a.group_id = ANY(:g) AND a.kind NOT IN ('system', 'legacy') AND a.status = ANY(:op)"
    p = {"o": user.org_id, "g": gids, "op": list(OPEN)}
    rows = (await db.execute(text(f"""SELECT a.id, a.kind, a.level, a.status, a.title, a.group_id, a.created_at, a.due_at FROM biz.alerts a
                                      WHERE {base} AND {access.alert_visible_sql(user, 'a')} ORDER BY a.created_at DESC LIMIT 200"""), p)).all() if gids else []  # noqa: S608
    rows = sorted(rows, key=lambda r: (LEVEL_RANK.get(r.level, 9), -r.created_at.timestamp()))
    sig = [{"id": str(r.id), "kind": r.kind, "level": r.level, "status": r.status, "title": r.title, "group_id": str(r.group_id),
            "created_at": r.created_at.isoformat(), "due_at": r.due_at.isoformat() if r.due_at else None} for r in rows]
    per_group: dict[uuid.UUID, dict[str, int]] = {}
    for r in rows:
        c = per_group.setdefault(r.group_id, {"red": 0, "amber": 0, "open_items": 0})
        c["open_items"] += 1
        if r.level in ("red", "amber"):
            c[r.level] += 1
    heat_last: dict[uuid.UUID, dict[str, Any]] = {}
    owner_ent = await _owner_entity(db, user.org_id)
    if owner_ent is not None and gids:
        ge = (await db.execute(text("SELECT id, group_id FROM mem.entities WHERE org_id = :o AND group_id = ANY(:g)"), {"o": user.org_id, "g": gids})).all()
        ser = await heat.series_many(db, user.org_id, owner_ent, [e.id for e in ge], RING_WEEKS, now=now) if ge else {}
        for e in ge:
            line = ser.get(e.id) or []
            if line:
                heat_last[e.group_id] = {"heat": line[-1]["heat"], "state": line[-1]["state"]}
    out_groups = [{"id": str(g["id"]), "label": g["name"], "kind": g["kind"], "member_count": g["member_count"],
                   **per_group.get(gid, {"red": 0, "amber": 0, "open_items": 0}), "heat": heat_last.get(gid)}
                  for gid, g in sorted(groups.items(), key=lambda kv: kv[1]["name"])]
    opp = [s for s in sig if s["kind"] in OPPORTUNITY_KINDS]
    risk = [s for s in sig if s["kind"] in RISK_KINDS]
    return {"breadcrumb": [_crumb("overview", "overview", "Toàn cảnh"), _crumb("context", code, types[code])], "code": code, "label": types[code],
            "groups": out_groups, "signals": sig[:LIST_MAX], "opportunities": {"n": len(opp), "signal_ids": [s["id"] for s in opp[:10]]},
            "risks": {"n": len(risk), "signal_ids": [s["id"] for s in risk[:10]]}, "ring": await _ring(db, user, code, gids, now),
            "hidden_pin_n": await _hidden_pin(db, user, base, p) if gids else 0}


# ─── tín hiệu của một mục Hồ sơ ──────────────────────────────────────────────

async def profile_item_signals(db: AsyncSession, user: service.CurrentUser, fact_id: uuid.UUID) -> dict[str, Any]:
    """Tín hiệu có `owner_fact_ids ∋ fact_id` (liên kết hai chiều INT-18). Sự thật của tổ chức khác ⇒ 404."""
    if (await db.execute(text("SELECT 1 FROM mem.facts WHERE id = :f AND org_id = :o"), {"f": fact_id, "o": user.org_id})).first() is None:
        raise not_found("Mục Hồ sơ")
    base = "a.org_id = :o AND :f = ANY(a.owner_fact_ids) AND a.kind NOT IN ('system', 'legacy')"
    p = {"o": user.org_id, "f": fact_id}
    rows = (await db.execute(text(f"""SELECT a.id, a.kind, a.level, a.status, a.title, a.created_at FROM biz.alerts a
                                      WHERE {base} AND {access.alert_visible_sql(user, 'a')} ORDER BY a.created_at DESC LIMIT 100"""), p)).all()  # noqa: S608
    return {"breadcrumb": [_crumb("me", "me", "Tôi"), _crumb("evidence", fact_id, "Mục Hồ sơ")], "fact_id": str(fact_id),
            "items": [{"id": str(r.id), "kind": r.kind, "level": r.level, "status": r.status, "title": r.title,
                       "created_at": r.created_at.isoformat()} for r in rows],
            "hidden_pin_n": await _hidden_pin(db, user, base, p)}
