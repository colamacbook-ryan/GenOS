# ruff: noqa: E501
"""API Tình báo (spec r4 §7.2 + r6 §7.2) — phần nền của T1.

Quyền: đọc tín hiệu theo `queue.read` (mọi vai), nhưng `privacy='console_pin'` (đánh giá người/nhân sự) CHỈ Owner có phiên PIN: vai khác
không thấy (danh sách lọc, đọc đích danh ⇒ 404), Owner chưa nhập PIN ⇒ danh sách ẩn (`hidden_pin_n`), đọc đích danh ⇒ 423. Ghi (Đúng/Sai,
đổi trạng thái, mute, catalog) chỉ Owner; mute lừa đảo/an toàn cần PIN (§8.1 hạ phòng thủ). Mọi truy vấn lọc `org_id` của người gọi
(không đọc/ghi chéo tổ chức). Không trả chữ tin thô: trích dẫn chỉ `quote_display` (đã che).
"""

from __future__ import annotations

import uuid
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gh.auth import rbac, service
from gh.auth.deps import current_user, require, require_owner
from gh.biz.intel import access, catalog, digest, levels, mutes, signals
from gh.chassis import actionlog
from gh.db import DB
from gh.errors import ApiError, not_found, pin_required
from gh.refinery import clock

router = APIRouter(tags=["intel"])

_LIST_COLS = """a.id, a.code, a.kind, a.level, a.gate, a.candidate, a.delivery, a.status, a.lane, a.privacy, a.title, a.summary,
                a.suggested_action, a.impact, a.urgency, a.confidence_level, a.reasons, a.created_at, a.pushed_at, a.would_push_at,
                a.capped_at, a.due_at, a.group_id, a.thread_key, a.owner_fact_ids, a.evidence_n, a.feedback_verdict"""


def _can_see_private(user: service.CurrentUser) -> bool:
    return access.can_see_private(user)


def _item(r: Any) -> dict[str, Any]:
    iso = (lambda d: d.isoformat() if d else None)  # noqa: E731
    return {"id": str(r.id), "code": r.code, "kind": r.kind, "level": r.level, "gate": r.gate, "candidate": r.candidate,
            "delivery": r.delivery, "status": r.status, "lane": r.lane, "privacy": r.privacy, "title": r.title, "summary": r.summary,
            "suggested_action": r.suggested_action, "impact": r.impact, "urgency": r.urgency, "confidence_level": r.confidence_level,
            "reasons": list(r.reasons or []), "created_at": iso(r.created_at), "pushed_at": iso(r.pushed_at),
            "would_push_at": iso(r.would_push_at), "capped_at": iso(r.capped_at), "due_at": iso(r.due_at),
            "group_id": str(r.group_id) if r.group_id else None, "owner_fact_ids": [str(x) for x in r.owner_fact_ids or []],
            "evidence_n": r.evidence_n, "feedback_verdict": r.feedback_verdict}


@router.get("/signals")
async def list_signals(kind: str | None = None, level: str | None = None, status: str | None = Query(default="open"),
                       context: str | None = None, limit: int = Query(default=50, ge=1, le=200),
                       user: service.CurrentUser = Depends(require("queue.read")), db: AsyncSession = DB) -> dict[str, Any]:
    if kind is not None and kind not in levels.KINDS:
        raise ApiError(422, "VALIDATION", "Loại tín hiệu không hợp lệ", errors={"kind": "Không có trong 10 loại"})
    if level is not None and level not in levels.LEVELS:
        raise ApiError(422, "VALIDATION", "Mức không hợp lệ", errors={"level": "red | amber | grey | off"})
    private = _can_see_private(user)
    params: dict[str, Any] = {"o": user.org_id, "k": kind, "l": level, "s": status, "c": context, "n": limit}
    where = """a.org_id = :o AND a.kind NOT IN ('system', 'legacy') AND (CAST(:k AS text) IS NULL OR a.kind = :k)
               AND (CAST(:l AS text) IS NULL OR a.level = :l) AND (CAST(:s AS text) IS NULL OR a.status = :s)
               AND (CAST(:c AS text) IS NULL OR EXISTS (SELECT 1 FROM mem.group_contexts gc WHERE gc.org_id = a.org_id
                    AND gc.group_id = a.group_id AND gc.context = :c AND gc.source = 'owner'))"""
    rows = (await db.execute(text(f"""SELECT {_LIST_COLS} FROM biz.alerts a WHERE {where}
                                      AND {access.alert_visible_sql(user, 'a')} ORDER BY a.created_at DESC LIMIT :n"""),  # noqa: S608
                             params)).all()
    hidden = 0
    if not private and user.role_code == rbac.OWNER:
        hidden = int((await db.execute(text(f"SELECT count(*) FROM biz.alerts a WHERE {where} AND a.privacy = 'console_pin'"),  # noqa: S608
                                       params)).scalar_one())
    return {"items": [_item(r) for r in rows], "hidden_pin_n": hidden}


async def _load(db: AsyncSession, user: service.CurrentUser, signal_id: uuid.UUID) -> Any:
    await access.require_alert(db, user, signal_id)        # console_pin / nhóm ẩn — một cổng với /inbox, /explain
    r = (await db.execute(text(f"SELECT {_LIST_COLS}, a.evidence, a.fact_ids FROM biz.alerts a WHERE a.id = :i AND a.org_id = :o"),  # noqa: S608
                          {"i": signal_id, "o": user.org_id})).one_or_none()
    if r is None or r.kind in ("system", "legacy"):
        raise not_found("Tín hiệu")
    if r.privacy == "console_pin":
        if user.role_code != rbac.OWNER:
            raise not_found("Tín hiệu")
        if not user.pin_active():
            raise pin_required()
    return r


@router.get("/signals/{signal_id}")
async def get_signal(signal_id: uuid.UUID, user: service.CurrentUser = Depends(require("queue.read")),
                     db: AsyncSession = DB) -> dict[str, Any]:
    r = await _load(db, user, signal_id)
    facts = (await db.execute(text("""SELECT id, predicate, status, attributed_to, valid_from, valid_to FROM mem.facts
                                      WHERE org_id = :o AND id = ANY(:f)"""), {"o": user.org_id, "f": list(r.fact_ids or [])})).all()
    ev = [{k: e.get(k) for k in ("type", "id", "quote_status", "quote_display", "received_at", "raw_event_id")}
          for e in (r.evidence or []) if isinstance(e, dict)]
    return {**_item(r), "evidence": ev,
            "facts": [{"id": str(f.id), "predicate": f.predicate, "status": f.status,
                       "attributed_to": str(f.attributed_to) if f.attributed_to else None,
                       "valid_from": f.valid_from.isoformat() if f.valid_from else None,
                       "valid_to": f.valid_to.isoformat() if f.valid_to else None} for f in facts]}


class FeedbackIn(BaseModel):
    verdict: Literal["correct", "wrong", "unimportant", "missed"]
    note: str | None = Field(default=None, max_length=500)


@router.post("/signals/{signal_id}/feedback")
async def signal_feedback(signal_id: uuid.UUID, body: FeedbackIn, user: service.CurrentUser = Depends(require_owner),
                          db: AsyncSession = DB) -> dict[str, Any]:
    await _load(db, user, signal_id)
    sig = await signals.feedback(db, user.org_id, signal_id, verdict=body.verdict, verdict_by="owner", now=clock.now(), user_id=user.id,
                                 note=body.note)
    await actionlog.record(db, org_id=user.org_id, actor_type="user", actor_id=user.actor_id, action="intel.signal.feedback",
                           target_type="signal", target_id=str(signal_id), result="ok", detail={"verdict": body.verdict})
    return {"ok": True, "status": sig.status, "level": sig.level}


class TransitionIn(BaseModel):
    to: Literal["acknowledged", "resolved", "dismissed"]


@router.post("/signals/{signal_id}/transition")
async def signal_transition(signal_id: uuid.UUID, body: TransitionIn, user: service.CurrentUser = Depends(require_owner),
                            db: AsyncSession = DB) -> dict[str, Any]:
    await _load(db, user, signal_id)
    try:
        sig = await signals.transition(db, user.org_id, signal_id, body.to, now=clock.now(), actor_id=user.actor_id,
                                       by_owner=body.to == "resolved")
    except signals.SignalError as e:
        raise ApiError(409, "SIGNAL_STATE", "Tín hiệu này đã được xử lý", detail=str(e)) from None
    return {"ok": True, "status": sig.status, "level": sig.level, "delivery": sig.delivery}


@router.get("/asks")
async def list_asks(user: service.CurrentUser = Depends(require_owner), db: AsyncSession = DB) -> dict[str, Any]:
    """Hàng Cần quyết đang mở. Ask gắn tín hiệu `console_pin` / nhóm ẩn, và ask nói về người (`profile`, `identity`) chỉ hiện khi
    đã mở PIN — còn lại chỉ báo số ẩn (`hidden_pin_n`), như `/signals`."""
    base = """FROM ops.asks q LEFT JOIN biz.alerts a ON a.id = q.signal_id AND a.org_id = q.org_id
              WHERE q.org_id = :o AND q.state = 'open' AND (q.expires_at IS NULL OR q.expires_at > now())"""
    vis = access.ask_visible_sql(user, "q", "a")
    rows = (await db.execute(text(f"""SELECT q.id, q.code, q.source, q.decision_kind, q.summary, q.signal_id, q.expires_at {base}
                                      AND {vis} ORDER BY q.created_at DESC LIMIT 50"""), {"o": user.org_id})).all()  # noqa: S608
    hidden = int((await db.execute(text(f"SELECT count(*) {base} AND NOT {vis}"), {"o": user.org_id})).scalar_one())  # noqa: S608
    return {"items": [{"id": str(a.id), "code": a.code, "source": a.source, "decision_kind": a.decision_kind, "summary": a.summary,
                       "signal_id": str(a.signal_id) if a.signal_id else None,
                       "expires_at": a.expires_at.isoformat() if a.expires_at else None} for a in rows],
            "hidden_pin_n": hidden}


class MuteIn(BaseModel):
    scope_type: Literal["kind", "group", "person", "detector"]
    scope_value: str = Field(min_length=1, max_length=100)


@router.post("/intel/mutes")
async def create_mute(body: MuteIn, user: service.CurrentUser = Depends(require_owner), db: AsyncSession = DB) -> dict[str, Any]:
    """Mute. Hạ phòng thủ (§8.1) = kind lừa đảo/an toàn (chỉ khi có PIN) HOẶC mọi mute phạm vi khác lưu `source="pin"` (nhóm/người/detector —
    `levels` cho mute PIN hạ cả vụ lừa đảo/an toàn xuống ⚪) ⇒ qua `strong_confirm` (Nhật ký + báo kênh phụ). Mute cùng phạm vi + nguồn đang hiệu lực
    ⇒ trả bản cũ, không báo lần hai."""
    from gh.biz.intel import strong_confirm

    if body.scope_type == "kind" and body.scope_value not in levels.KINDS:
        raise ApiError(422, "VALIDATION", "Loại tín hiệu không hợp lệ")
    kind_defensive = body.scope_type == "kind" and body.scope_value in mutes.DEFENSIVE_KINDS
    if kind_defensive and not user.pin_active():
        raise pin_required()
    source = "pin" if user.pin_active() else "chat"
    # mọi mute PIN đều hạ được vụ fail-high (G1/G2 bắt ở mọi kind) ⇒ luôn qua strong_confirm + báo kênh phụ
    defensive = source == "pin"
    old = await mutes.find_active(db, user.org_id, scope_type=body.scope_type, scope_value=body.scope_value, source=source)  # type: ignore[arg-type]
    if old is not None:
        return {"id": str(old.id), "source": old.source, "already": True}
    if defensive:
        await strong_confirm.require(db, user.org_id, "mute_defensive", via="console", pin_verified=True)    # báo kênh phụ
    m = await mutes.create(db, user.org_id, scope_type=body.scope_type, scope_value=body.scope_value, source=source, user_id=user.id)  # type: ignore[arg-type]
    await actionlog.record(db, org_id=user.org_id, actor_type="user", actor_id=user.actor_id, action="intel.mute.created",
                           target_type="mute", target_id=str(m.id), result="ok", detail={"scope": body.scope_type, "source": m.source})
    return {"id": str(m.id), "source": m.source}


@router.delete("/intel/mutes/{mute_id}")
async def delete_mute(mute_id: uuid.UUID, user: service.CurrentUser = Depends(require_owner), db: AsyncSession = DB) -> dict[str, Any]:
    if not await mutes.revoke(db, user.org_id, mute_id):
        raise not_found("Mute")
    await actionlog.record(db, org_id=user.org_id, actor_type="user", actor_id=user.actor_id, action="intel.mute.revoked",
                           target_type="mute", target_id=str(mute_id), result="ok")
    return {"ok": True}


@router.get("/intel/metrics")
async def intel_metrics(user: service.CurrentUser = Depends(require_owner), db: AsyncSession = DB) -> dict[str, Any]:
    """Khung chỉ số (T9 làm đủ: recall, chi phí, báo nhầm/ngày…). Ở T1: đếm theo mức, precision + cận dưới Wilson theo cổng, bóng."""
    rows = (await db.execute(text("""SELECT level, count(*) AS n FROM biz.alerts WHERE org_id = :o AND kind NOT IN ('system', 'legacy')
                                     AND status IN ('open', 'acknowledged') GROUP BY level"""), {"o": user.org_id})).all()
    counts: dict[str, int] = {str(r.level): int(r.n) for r in rows}
    would = int((await db.execute(text("SELECT count(*) FROM biz.alerts WHERE org_id = :o AND would_push_at IS NOT NULL"),
                                  {"o": user.org_id})).scalar_one())
    gates = [await signals.precision(db, user.org_id, g) for g in levels.GATES]
    return {"open_by_level": {k: int(v) for k, v in counts.items()}, "precision_ci": gates, "shadow": {"would_push_n": would}}


@router.get("/intel/digest/preview")
async def digest_preview(user: service.CurrentUser = Depends(require_owner), db: AsyncSession = DB) -> dict[str, Any]:
    d = await digest.build(db, user.org_id, clock.now(), audience="owner" if user.pin_active() else "chat", only_new=False)
    return {"items": [it.__dict__ for it in d.items], "counts": d.counts, "system_question": d.system_question, "refs": d.refs()}


# ─── catalog (Nguồn lực: hàng/dịch vụ của Sếp) ───────────────────────────────

class CatalogIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    unit: str | None = Field(default=None, max_length=50)
    price_vnd: int | None = Field(default=None, ge=0)
    tags: list[str] = Field(default_factory=list, max_length=20)


class CatalogPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    unit: str | None = Field(default=None, max_length=50)
    price_vnd: int | None = Field(default=None, ge=0)
    tags: list[str] | None = Field(default=None, max_length=20)
    state: Literal["active", "suggested", "archived"] | None = None


@router.get("/catalog")
async def list_catalog(state: str | None = "active", user: service.CurrentUser = Depends(current_user), db: AsyncSession = DB) -> dict[str, Any]:
    if user.role_code != rbac.OWNER:
        raise ApiError(403, "FORBIDDEN", "Vai trò của bạn không có quyền thao tác này")
    return {"items": [i.to_dict() for i in await catalog.list_items(db, user.org_id, state=state)]}


@router.post("/catalog")
async def create_catalog(body: CatalogIn, user: service.CurrentUser = Depends(require_owner), db: AsyncSession = DB) -> dict[str, Any]:
    it = await catalog.create(db, user.org_id, name=body.name, user_id=user.id, description=body.description, unit=body.unit,
                              price_vnd=body.price_vnd, tags=body.tags)
    await actionlog.record(db, org_id=user.org_id, actor_type="user", actor_id=user.actor_id, action="intel.catalog.created",
                           target_type="catalog_item", target_id=str(it.id), result="ok")
    return it.to_dict()


@router.patch("/catalog/{item_id}")
async def patch_catalog(item_id: uuid.UUID, body: CatalogPatch, user: service.CurrentUser = Depends(require_owner),
                        db: AsyncSession = DB) -> dict[str, Any]:
    it = await catalog.update(db, user.org_id, item_id, **body.model_dump(exclude_none=True))
    if it is None:
        raise not_found("Mặt hàng")
    return it.to_dict()


@router.delete("/catalog/{item_id}")
async def delete_catalog(item_id: uuid.UUID, user: service.CurrentUser = Depends(require_owner), db: AsyncSession = DB) -> dict[str, Any]:
    if not await catalog.archive(db, user.org_id, item_id):
        raise not_found("Mặt hàng")
    await actionlog.record(db, org_id=user.org_id, actor_type="user", actor_id=user.actor_id, action="intel.catalog.archived",
                           target_type="catalog_item", target_id=str(item_id), result="ok")
    return {"ok": True}


def _include_console() -> None:
    from gh.biz.intel import console

    router.include_router(console.router)


def _include_subrouters() -> None:
    """Route của module con trong `detectors/`, `pipes/` (T3, T9, T10…) gắn vào cùng router — gói đó không sửa tệp này."""
    from gh.biz.intel.jobs import SUBMODULES

    for m in SUBMODULES:
        sub = getattr(m, "router", None)
        if sub is not None:
            router.include_router(sub)


_include_console()
_include_subrouters()
