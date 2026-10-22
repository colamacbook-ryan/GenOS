"""Chứng cứ — "Vì sao hệ thống nghĩ vậy" (ARCHITECTURE §9, docs/api/phase-3.md).

Chuỗi: điểm → đơn vị ý nghĩa → trích dẫn → bản ghi thô. Không chứng cứ thì không kết luận.

Core giải thích `meaning_unit`, `score`, `alert`, `draft`, `raw`. Cụm màn khác đăng ký loại của mình
(`opportunity`, `task`, `review`…) bằng `register(kind, permission, fn)`; `fn` trả dict cùng hình dạng, thường
dựng bằng `units_payload(...)` + `payload(...)`.
"""

import re
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gh.auth import rbac, service
from gh.biz.core.scope import Scope, direct_visible, direct_visible_sql, group_view_sql, not_found, scope_for
from gh.data.common import RAW_SELECT, iso, mask_text, raw_code, raw_item

MAX_UNITS = 20
BUSINESS_READ = ("queue.read", "profile.read", "opportunity.read")


@dataclass
class Explainer:
    permission: str | tuple[str, ...]   # một trong các quyền này (phạm vi lấy theo quyền đầu tiên có)
    fn: Callable[[AsyncSession, service.CurrentUser, Scope, str], Awaitable[dict[str, Any]]]


_REGISTRY: dict[str, Explainer] = {}


def register(kind: str, permission: str | tuple[str, ...],
             fn: Callable[[AsyncSession, service.CurrentUser, Scope, str], Awaitable[dict[str, Any]]]) -> None:
    _REGISTRY[kind] = Explainer(permission, fn)


def kinds() -> list[str]:
    return sorted(_REGISTRY)


def payload(kind: str, id: str, title: str, statement: str, *, method: str | None = None,
            factors: list[dict[str, Any]] | None = None, units: list[dict[str, Any]] | None = None,
            history: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    units = units or []
    return {"kind": kind, "id": id, "title": title,
            "statement": statement if units or factors else f"{statement} · Chưa có chứng cứ",
            "method": method, "factors": factors or [], "units": units, "history": history or []}


def _person(r: Any, prefix: str) -> dict[str, Any] | None:
    pid = getattr(r, f"{prefix}_id")
    if pid is None:
        return None
    return {"id": str(pid), "code": getattr(r, f"{prefix}_code"), "name": getattr(r, f"{prefix}_name"),
            "type": getattr(r, f"{prefix}_type", None), "org_name": getattr(r, f"{prefix}_org", None)}


async def units_payload(db: AsyncSession, unit_ids: list[uuid.UUID], *,
                        viewer: service.CurrentUser) -> list[dict[str, Any]]:
    """Đơn vị ý nghĩa (mới nhất trước, tối đa 20) kèm trích dẫn tới bản ghi thô. Đơn vị từ tin 1-1 chỉ trả cho Owner
    hoặc người được giao đúng người đó (review v0.1.65 P2-2) — chỗ chung cho Hộp thư, chứng cứ, cảnh báo, bản nháp."""
    if not unit_ids:
        return []
    is_owner = viewer.role_code == rbac.OWNER
    dm_sql, dm_params = direct_visible_sql(viewer, "u.group_id", "u.person_id")
    rows = (await db.execute(text(f"""
        SELECT u.id, u.event_type, u.conclusion, u.confidence, u.observed_at,
               g.id AS g_id, g.code AS g_code, g.name AS g_name, gc.type AS g_channel,
               p.id AS p_id, p.code AS p_code, p.display_name AS p_name, p.person_type AS p_type,
               p.organization_name AS p_org
        FROM clean.meaning_units u
        LEFT JOIN core.groups g ON g.id = u.group_id LEFT JOIN core.channels gc ON gc.id = g.channel_id
        LEFT JOIN core.persons p ON p.id = u.person_id
        WHERE u.id = ANY(:ids) AND {dm_sql} ORDER BY u.observed_at DESC LIMIT :n"""),  # noqa: S608 — biểu thức nội bộ
        {"ids": list(dict.fromkeys(unit_ids)), "n": MAX_UNITS, **dm_params})).all()
    quotes = (await db.execute(text("""
        SELECT ev.meaning_unit_id, ev.quote, e.id AS raw_id, e.seq, e.occurred_at, c.type AS channel,
               sp.id AS s_id, sp.code AS s_code, sp.display_name AS s_name, sp.person_type AS s_type,
               sp.organization_name AS s_org, e.body_text
        FROM clean.evidence ev
        JOIN raw.events e ON e.id = ev.raw_event_id AND e.received_at = ev.raw_received_at
        JOIN core.channels c ON c.id = e.channel_id
        LEFT JOIN core.person_identities pi ON pi.id = e.sender_identity_id
        LEFT JOIN core.persons sp ON sp.id = pi.person_id
        WHERE ev.meaning_unit_id = ANY(:ids) ORDER BY e.occurred_at"""),
        {"ids": [r.id for r in rows]})).all()
    by_unit: dict[uuid.UUID, list[dict[str, Any]]] = {}
    for q in quotes:
        by_unit.setdefault(q.meaning_unit_id, []).append({
            "raw_id": str(q.raw_id), "raw_code": raw_code(q.seq) if q.seq is not None else None,
            "quote": mask_text(q.quote or q.body_text, is_owner), "occurred_at": iso(q.occurred_at),
            "channel": q.channel, "sender": _person(q, "s")})
    return [{"id": str(r.id), "event_type": r.event_type, "conclusion": mask_text(r.conclusion, is_owner),
             "confidence": float(r.confidence), "observed_at": iso(r.observed_at),
             "group": {"id": str(r.g_id), "code": r.g_code, "name": r.g_name, "channel": r.g_channel}
             if r.g_id else None,
             "person": _person(r, "p"), "quotes": by_unit.get(r.id, [])} for r in rows]


def unit_ids_of(refs: list[dict[str, Any]] | None) -> list[uuid.UUID]:
    out = []
    for ref in refs or []:
        if isinstance(ref, dict) and ref.get("type") == "meaning_unit":
            try:
                out.append(uuid.UUID(str(ref["id"])))
            except (KeyError, ValueError):
                continue
    return out


async def evidence_unit_ids(db: AsyncSession, org_id: uuid.UUID, refs: list[dict[str, Any]] | None) -> list[uuid.UUID]:
    """Như `unit_ids_of` nhưng hiểu cả chứng cứ loại `raw` (v0.1.71): cảnh báo «Phản hồi chậm» gắn thẳng tin bị bỏ quên
    (`{"type": "raw", "id": …}`) ⇒ đổi sang các đơn vị ý nghĩa trích dẫn tin đó để lộ ra câu chữ và trích dẫn."""
    ids = unit_ids_of(refs)
    raws: list[uuid.UUID] = []
    for ref in refs or []:
        if isinstance(ref, dict) and ref.get("type") == "raw":
            try:
                raws.append(uuid.UUID(str(ref["id"])))
            except (KeyError, ValueError):
                continue
    if raws:
        rows = (await db.execute(text("""
            SELECT DISTINCT ev.meaning_unit_id FROM clean.evidence ev
            JOIN clean.meaning_units u ON u.id = ev.meaning_unit_id AND u.observed_at = ev.meaning_observed_at
            WHERE ev.raw_event_id = ANY(:r) AND u.org_id = :o AND u.superseded_by IS NULL"""),
            {"r": raws, "o": org_id})).scalars().all()
        ids.extend(rows)
    return list(dict.fromkeys(ids))


async def _group_view_ok(db: AsyncSession, sc: Scope, group_id: Any) -> bool:
    """«Quyền xem» của nhóm (v0.1.71) có cho vai của `sc` đọc nội dung nhóm này không."""
    if group_id is None or sc.role == rbac.OWNER:
        return True
    where = group_view_sql(sc.role, "CAST(:vg AS uuid)")
    return bool((await db.execute(text(f"SELECT {where}"), {"vg": group_id})).scalar())  # noqa: S608


async def _evidence_in_hidden_group(db: AsyncSession, sc: Scope, refs: list[dict[str, Any]] | None) -> bool:
    """Chứng cứ (đơn vị ý nghĩa / tin thô) của cảnh báo có thuộc nhóm mà vai này không được xem (v0.1.71)."""
    if sc.role == rbac.OWNER:
        return False
    units = unit_ids_of(refs)
    raws = [uuid.UUID(str(r["id"])) for r in refs or [] if isinstance(r, dict) and r.get("type") == "raw"
            and re.fullmatch(r"[0-9a-fA-F-]{36}", str(r.get("id")))]
    if not units and not raws:
        return False
    rows = (await db.execute(text("""
        SELECT group_id FROM clean.meaning_units WHERE id = ANY(:u) AND group_id IS NOT NULL
        UNION SELECT group_id FROM raw.events WHERE id = ANY(:r) AND group_id IS NOT NULL"""),
        {"u": units, "r": raws})).scalars().all()
    for gid in set(rows):
        if not await _group_view_ok(db, sc, gid):
            return True
    return False


async def _unit_visible(db: AsyncSession, sc: Scope, person_id: Any, group_id: Any) -> bool:
    if not await _group_view_ok(db, sc, group_id):     # nhóm «Chỉ Sếp»: ẩn dù người có trong phạm vi
        return False
    if sc.is_all:
        return True
    if person_id is not None:
        where, params = sc.person_id_sql(":pid")
        if (await db.execute(text(f"SELECT {where}"), {"pid": person_id, **params})).scalar():  # noqa: S608
            return True
    if group_id is not None:
        where, params = sc.group_id_sql(":gid")
        if (await db.execute(text(f"SELECT {where}"), {"gid": group_id, **params})).scalar():  # noqa: S608
            return True
    return False


# ─── các loại của core ────────────────────────────────────────────────────────

async def _meaning_unit(db: AsyncSession, user: service.CurrentUser, sc: Scope, id: str) -> dict[str, Any]:
    try:
        uid = uuid.UUID(id)
    except ValueError as e:
        raise not_found("Đơn vị ý nghĩa") from e
    r = (await db.execute(text("""SELECT id, event_type, conclusion, confidence, person_id, group_id
                                   FROM clean.meaning_units WHERE id = :i AND org_id = :o"""),
                          {"i": uid, "o": user.org_id})).one_or_none()
    if r is None or not await _unit_visible(db, sc, r.person_id, r.group_id) \
            or not await direct_visible(db, user, r.group_id, r.person_id):
        raise not_found("Đơn vị ý nghĩa")
    return payload("meaning_unit", id, r.event_type, f"{r.event_type} · tin cậy {float(r.confidence):.2f}",
                   method="rules+model", units=await units_payload(db, [uid], viewer=user))


SUBJECT_NAME = {
    "person": "SELECT display_name FROM core.persons WHERE id = :s",
    "group": "SELECT name FROM core.groups WHERE id = :s",
}
DIMENSION_LABELS = {"heat": "độ nóng", "potential": "tiềm năng", "churn_risk": "rủi ro mất khách", "fit": "mức phù hợp",
                    "engagement": "mức gắn kết", "data_confidence": "độ tin cậy dữ liệu", "care": "điểm chăm sóc",
                    "performance": "hiệu suất", "risk": "rủi ro"}


async def _score(db: AsyncSession, user: service.CurrentUser, sc: Scope, id: str) -> dict[str, Any]:
    try:
        subject_type, sid, dimension = id.split(":", 2)
        subject_id = uuid.UUID(sid)
    except ValueError as e:
        raise not_found("Điểm") from e
    if subject_type == "person":
        visible = await _unit_visible(db, sc, subject_id, None)
    elif subject_type == "group":
        visible = await _unit_visible(db, sc, None, subject_id)
    else:
        visible = sc.is_all
    if not visible:
        raise not_found("Điểm")
    snaps = (await db.execute(text("""
        SELECT s.value, s.confidence, s.explanation, s.computed_at, s.overridden_by, u.id AS by_id,
               u.display_name AS by_name
        FROM clean.score_snapshots s LEFT JOIN core.users u ON u.id = s.overridden_by
        WHERE s.org_id = :o AND s.subject_type = :t AND s.subject_id = :s AND s.dimension = :d
        ORDER BY s.computed_at DESC LIMIT 30"""),
        {"o": user.org_id, "t": subject_type, "s": subject_id, "d": dimension})).all()
    name = None
    if subject_type in SUBJECT_NAME:
        name = (await db.execute(text(SUBJECT_NAME[subject_type]), {"s": subject_id})).scalar_one_or_none()
    label = DIMENSION_LABELS.get(dimension, dimension)
    title = f"{name} — {label}" if name else label
    if not snaps:
        return payload("score", id, title, "Chưa có điểm")
    cur = snaps[0]
    exp = cur.explanation or {}
    factors = exp.get("factors") or []
    ids: list[uuid.UUID] = []
    for f in factors:
        ids.extend(unit_ids_of(f.get("evidence")))
    owner = user.role_code == rbac.OWNER
    statement = f"{float(cur.value):.0f}/100"
    if cur.confidence is not None:
        statement += f" · tin cậy {float(cur.confidence):.2f}".replace(".", ",")
    history = [{"value": float(s.value), "computed_at": iso(s.computed_at),
                "method": (s.explanation or {}).get("method"),
                "by": {"id": str(s.by_id), "name": s.by_name} if s.by_id else None} for s in snaps]
    return payload("score", id, title, statement, method=exp.get("method"),
                   factors=[{"label": mask_text(f.get("label"), owner), "value": f.get("value"),
                             "evidence": f.get("evidence") or []} for f in factors],
                   units=await units_payload(db, ids, viewer=user), history=history)


async def _alert(db: AsyncSession, user: service.CurrentUser, sc: Scope, id: str) -> dict[str, Any]:
    try:
        aid = uuid.UUID(id)
    except ValueError as e:
        raise not_found("Cảnh báo") from e
    r = (await db.execute(text("""SELECT id, code, title, summary, priority, subject_type, subject_id, evidence,
                                         personnel_related, recipient_user_id
                                  FROM biz.alerts WHERE id = :i AND org_id = :o"""),
                          {"i": aid, "o": user.org_id})).one_or_none()
    if r is None:
        raise not_found("Cảnh báo")
    if r.personnel_related and user.permissions.get("people_review.read", rbac.NONE) == rbac.NONE:
        raise not_found("Cảnh báo")
    if (not sc.is_all and r.recipient_user_id not in sc.users and not await _unit_visible(
            db, sc, r.subject_id if r.subject_type == "person" else None,
            r.subject_id if r.subject_type == "group" else None)) \
            or (r.subject_type == "group" and not await _group_view_ok(db, sc, r.subject_id)):
        raise not_found("Cảnh báo")
    if await _evidence_in_hidden_group(db, sc, r.evidence):
        raise not_found("Cảnh báo")
    owner = user.role_code == rbac.OWNER
    return payload("alert", id, f"{r.code} · {mask_text(r.title, owner)}", f"Ưu tiên {r.priority}",
                   method="rule", units=await units_payload(db, await evidence_unit_ids(db, user.org_id, r.evidence),
                                             viewer=user))


async def _draft(db: AsyncSession, user: service.CurrentUser, sc: Scope, id: str) -> dict[str, Any]:
    try:
        did = uuid.UUID(id)
    except ValueError as e:
        raise not_found("Bản nháp") from e
    r = (await db.execute(text("""SELECT id, code, title, sources, subject_type, subject_id, created_by, group_id
                                  FROM biz.action_drafts WHERE id = :i AND org_id = :o"""),
                          {"i": did, "o": user.org_id})).one_or_none()
    if r is None:
        raise not_found("Bản nháp")
    if (not sc.is_all and r.created_by not in sc.users and not await _unit_visible(
            db, sc, r.subject_id if r.subject_type == "person" else None,
            r.subject_id if r.subject_type == "group" else None)) \
            or (r.subject_type == "group" and not await _group_view_ok(db, sc, r.subject_id)) \
            or not await _group_view_ok(db, sc, r.group_id):
        raise not_found("Bản nháp")
    decision = (await db.execute(text("SELECT context_refs FROM agent.decisions WHERE draft_id = :d LIMIT 1"),
                                 {"d": did})).scalar_one_or_none()
    refs = [s.get("ref") for s in (r.sources or []) if isinstance(s, dict)] + list(decision or [])
    return payload("draft", id, f"{r.code} · {r.title or ''}", "Dữ liệu agent đã dùng", method="agent",
                   units=await units_payload(db, await evidence_unit_ids(db, user.org_id, [x for x in refs if x]),
                                             viewer=user))


async def raw_evidence(db: AsyncSession, user: service.CurrentUser, raw_id: uuid.UUID) -> dict[str, Any]:
    """Nguyên văn một bản ghi thô cho màn kinh doanh: chỉ khi nó là chứng cứ của một đơn vị trong phạm vi."""
    perm = next((p for p in BUSINESS_READ if user.permissions.get(p, rbac.NONE) != rbac.NONE), None)
    if perm is None:
        raise not_found("Bản ghi")
    sc = await scope_for(db, user, perm)
    units = (await db.execute(text("""
        SELECT u.person_id, u.group_id FROM clean.evidence ev
        JOIN clean.meaning_units u ON u.id = ev.meaning_unit_id AND u.observed_at = ev.meaning_observed_at
        WHERE ev.raw_event_id = :r AND u.org_id = :o"""), {"r": raw_id, "o": user.org_id})).all()
    if not units:
        raise not_found("Bản ghi")
    for u in units:
        if await _unit_visible(db, sc, u.person_id, u.group_id) \
                and await direct_visible(db, user, u.group_id, u.person_id):
            break
    else:
        raise not_found("Bản ghi")
    row = (await db.execute(text(RAW_SELECT + " WHERE e.id = :i"), {"i": raw_id})).one_or_none()
    if row is None:
        raise not_found("Bản ghi")
    item = raw_item(row)
    owner = user.role_code == rbac.OWNER
    item["text"] = mask_text(item.get("text"), owner)
    return item


register("meaning_unit", BUSINESS_READ, _meaning_unit)
register("score", ("profile.read", "people_review.read"), _score)
register("alert", "queue.read", _alert)
register("draft", ("action.approve", "action.draft"), _draft)


async def explain(db: AsyncSession, user: service.CurrentUser, kind: str, id: str) -> dict[str, Any]:
    e = _REGISTRY.get(kind)
    if e is None:
        raise not_found("Loại chứng cứ")
    perms = (e.permission,) if isinstance(e.permission, str) else e.permission
    perm = next((p for p in perms if user.permissions.get(p, rbac.NONE) != rbac.NONE), perms[0])
    sc = await scope_for(db, user, perm)      # 403 nếu không có quyền nào
    return await e.fn(db, user, sc, id)
