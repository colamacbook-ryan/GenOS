# ruff: noqa: E501
"""API trí nhớ — phần của L1 (spec §7.2b): sự thật hai thời gian, sửa sự thật, nhãn bối cảnh nhóm.

Quyền: Owner. Mọi response có `breadcrumb: [{tier: me|overview|context|object|evidence, id, label}]` để giao diện lên/xuống tầng
không mất ngữ cảnh; mọi mục nhận định có `fact_ids`/`evidence_ids`. Sự thật `hr` (đánh giá nhân sự) cần PIN: truy vấn đích danh
mà thiếu PIN ⇒ 423, danh sách chung thì ẩn và báo `hidden_pin_n`. `view_routes` (L4) được gắn vào router này.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gh.auth import service
from gh.auth.deps import require_owner
from gh.biz.mem import contexts, entities, facts, predicates, view_routes
from gh.chassis import actionlog
from gh.db import DB
from gh.errors import ApiError, not_found, pin_required

router = APIRouter(tags=["mem"])
router.include_router(view_routes.router)


def _crumb(tier: str, id_: Any, label: str) -> dict[str, str]:
    return {"tier": tier, "id": str(id_), "label": label}


def _write_error(e: facts.FactWriteError) -> ApiError:
    status = 409 if e.code in (facts.ERR_CONFLICT, facts.ERR_DEADLOCK) else 422
    return ApiError(status, e.code.upper(), "Không ghi được sự thật", detail=e.detail or e.code)


async def _evidence_ids(db: AsyncSession, fact_ids: list[uuid.UUID]) -> dict[uuid.UUID, list[str]]:
    if not fact_ids:
        return {}
    rows = (await db.execute(text("""SELECT fact_id, id FROM mem.fact_evidence WHERE fact_id = ANY(:f) ORDER BY created_at"""),
                             {"f": fact_ids})).all()
    out: dict[uuid.UUID, list[str]] = {}
    for r in rows:
        out.setdefault(r.fact_id, []).append(str(r.id))
    return out


@router.get("/facts")
async def query_facts(subject_id: uuid.UUID, predicate: str | None = None, at: datetime | None = None,
                      known_at: datetime | None = None, user: service.CurrentUser = Depends(require_owner),
                      db: AsyncSession = DB) -> dict[str, Any]:
    """Truy vấn hai thời gian: sự thật đúng TẠI thời điểm `at` (mặc định bây giờ); `known_at` = những gì hệ thống đã biết
    vào lúc đó."""
    ent = await entities.get(db, subject_id)
    if ent is None or ent.org_id != user.org_id:
        raise not_found("Đối tượng")
    if predicate is not None:
        try:
            await predicates.get(db, predicate)
        except predicates.UnknownPredicate:
            raise ApiError(422, "UNKNOWN_PREDICATE", "Vị từ không có trong danh mục") from None
    rows = await facts.at(db, user.org_id, subject_id, predicate, at, known_at=known_at)
    hidden = 0
    if not user.pin_active():
        shown = [f for f in rows if f.sensitivity != "hr"]
        hidden = len(rows) - len(shown)
        if hidden and predicate is not None:
            raise pin_required()
        rows = shown
    ev = await _evidence_ids(db, [f.id for f in rows])
    items = [{**f.to_dict(), "fact_ids": [str(f.id)], "evidence_ids": ev.get(f.id, [])} for f in rows]
    canon = await entities.canonical(db, subject_id)
    return {"breadcrumb": [_crumb("object", canon, ent.display_name)], "subject_id": str(canon), "at": at,
            "facts": items, "hidden_pin_n": hidden}


class CorrectIn(BaseModel):
    new_value: Any = None
    new_object_id: uuid.UUID | None = None
    retract: bool = False

    @model_validator(mode="after")
    def _one(self) -> CorrectIn:
        n = int(self.retract) + int(self.new_value is not None) + int(self.new_object_id is not None)
        if n != 1:
            raise ValueError("cần đúng một trong: retract, new_value, new_object_id")
        return self


@router.post("/facts/{fact_id}/correct")
async def correct_fact(fact_id: uuid.UUID, body: CorrectIn, user: service.CurrentUser = Depends(require_owner),
                       db: AsyncSession = DB) -> dict[str, Any]:
    """Sếp sửa nội dung sai ("sửa: Lan là em họ") hoặc xoá (`retract`: đóng hiệu lực, không xoá dòng). Bản cũ `superseded`,
    bản mới `owner_confirmed` với bằng chứng `owner_edit`."""
    old = await facts.get(db, user.org_id, fact_id)
    if old is None:
        raise not_found("Sự thật")
    subject = await entities.get(db, old.subject_id)
    label = subject.display_name if subject else str(old.subject_id)
    log_id = await actionlog.record(db, org_id=user.org_id, actor_type="user", actor_id=user.actor_id,
                                    action="mem.fact_correct", target_type="fact", target_id=str(fact_id),
                                    target_label=old.predicate,
                                    detail={"retract": body.retract, "predicate": old.predicate}, ip=user.ip)
    try:
        new = await facts.correct(db, user.org_id, fact_id, actor_user_id=user.id, new_value=body.new_value,
                                  new_object_id=body.new_object_id, retract=body.retract, action_log_id=log_id)
    except facts.FactWriteError as e:
        raise _write_error(e) from e
    ev = await _evidence_ids(db, [new.id])
    return {"breadcrumb": [_crumb("object", old.subject_id, label), _crumb("evidence", new.id, new.predicate)],
            "fact": new.to_dict(), "fact_ids": [str(new.id)], "evidence_ids": ev.get(new.id, []),
            "replaced_fact_id": None if body.retract else str(fact_id)}


async def _group(db: AsyncSession, org_id: uuid.UUID, group_id: uuid.UUID) -> str:
    name = (await db.execute(text("SELECT name FROM core.groups WHERE id = :g AND org_id = :o"),
                             {"g": group_id, "o": org_id})).scalar_one_or_none()
    if name is None:
        raise not_found("Nhóm")
    return str(name)


def _ctx_json(g: contexts.GroupContext) -> dict[str, Any]:
    return {"context": g.context, "is_primary": g.is_primary, "source": g.source, "confirmed": g.source == "owner",
            "confidence": g.confidence, "confirmed_at": g.confirmed_at}


async def _contexts_body(db: AsyncSession, org_id: uuid.UUID, group_id: uuid.UUID, name: str) -> dict[str, Any]:
    labels = await contexts.list_group_contexts(db, org_id, group_id)
    pol = await contexts.policy(db, org_id, f"g:{group_id}")
    return {"breadcrumb": [_crumb("context", group_id, name)], "group_id": str(group_id), "labels": [_ctx_json(x) for x in labels],
            "confirmed": pol.confirmed, "effective_context": pol.effective_context, "policy_prompt": contexts.render_for_prompt(pol)}


@router.get("/groups/{group_id}/contexts")
async def get_group_contexts(group_id: uuid.UUID, user: service.CurrentUser = Depends(require_owner),
                             db: AsyncSession = DB) -> dict[str, Any]:
    return await _contexts_body(db, user.org_id, group_id, await _group(db, user.org_id, group_id))


class LabelIn(BaseModel):
    context: str
    is_primary: bool = False


class ContextsIn(BaseModel):
    labels: list[LabelIn] = Field(min_length=1, max_length=len(contexts.CONTEXT_CODES))


@router.put("/groups/{group_id}/contexts")
async def put_group_contexts(group_id: uuid.UUID, body: ContextsIn, user: service.CurrentUser = Depends(require_owner),
                             db: AsyncSession = DB) -> dict[str, Any]:
    name = await _group(db, user.org_id, group_id)
    try:
        await contexts.set_group_contexts(db, user.org_id, group_id, [x.model_dump() for x in body.labels], user_id=user.id)
    except ValueError as e:
        raise ApiError(422, "VALIDATION", "Nhãn bối cảnh chưa hợp lệ", detail=str(e)) from e
    await actionlog.record(db, org_id=user.org_id, actor_type="user", actor_id=user.actor_id, action="mem.group_contexts_set",
                           target_type="group", target_id=str(group_id), target_label=name,
                           detail={"labels": [x.context for x in body.labels]}, ip=user.ip)
    return await _contexts_body(db, user.org_id, group_id, name)


__all__ = ["router"]
