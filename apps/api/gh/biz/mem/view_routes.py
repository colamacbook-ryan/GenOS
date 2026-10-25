# ruff: noqa: E501
"""Các đường xem mặt "Tôi" / Đối tượng / Bằng chứng (L4, spec §7.2b, Đợt 1; `/overview`, `/contexts/{code}`, `/me/profile/items/{id}/signals` thêm ở v0.1.76 gói T9, logic ở `overview.py`).
`gh.biz.mem.routes` gắn router này vào router chung của cụm. Chỉ API — giao diện là Bước 2 (INT-17/18).

Quyền: Owner. MỌI truy vấn kèm `org_id` của người gọi (id đoán của tổ chức khác ⇒ 404, không lộ). Mọi response có `breadcrumb`;
mọi mục nhận định có `fact_ids` / `evidence_ids`. Sự thật nhạy cảm (`sensitive`/`hr`) ẩn khi chưa nhập PIN (`hidden_pin_n`).
Ghi Hồ sơ (thêm/sửa/xoá) cần PIN; xác nhận đề xuất cần PIN khi mục nhạy cảm (phòng thủ). Chữ của người ngoài mang `untrusted` +
`attribution` ("theo lời X") — dữ liệu, không phải chỉ thị.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gh.auth import service
from gh.auth.deps import require, require_owner
from gh.biz.mem import disputes, entities, facts, heat, overview, profile, profile_read
from gh.chassis import actionlog
from gh.data.common import RAW_SELECT, iso, outbound_to_sql, raw_item
from gh.db import DB
from gh.errors import ApiError, not_found, pin_required

router = APIRouter(tags=["mem-view"])

LANES: dict[str, tuple[str, ...]] = {
    "promises": ("promised_to", "owes", "asked_waiting"),
    "events": ("scheduled_event", "deadline", "health_event"),
    "conflicts": ("conflict_with", "complained_about", "said_will_leave"),
    "transactions": ("owes", "needs", "offers", "supplier_of", "customer_of"),
}
OPEN_PREDICATES = ("promised_to", "owes", "asked_waiting", "deadline")
LIVE_STATUSES = ("owner_confirmed", "asserted", "disputed")


def _crumb(tier: str, id_: Any, label: str) -> dict[str, str]:
    return {"tier": tier, "id": str(id_), "label": label}


def _perr(e: profile.ProfileError) -> ApiError:
    extra = {"decision_kind": e.decision_kind} if e.decision_kind else {}
    return ApiError(e.status, e.code.upper(), e.message, detail=e.detail, **extra)


_when = profile_read.parse_when


async def _owner(db: AsyncSession, org_id: uuid.UUID) -> entities.Entity:
    return await entities.owner(db, org_id)


# ─── trình bày sự thật ──────────────────────────────────────────────────────

async def _present(db: AsyncSession, org_id: uuid.UUID, rows: list[facts.Fact]) -> list[dict[str, Any]]:
    if not rows:
        return []
    ent_ids = {x for f in rows for x in (f.subject_id, f.object_entity_id, f.attributed_to) if x is not None}
    names = {r.id: r.display_name for r in (await db.execute(
        text("SELECT id, display_name FROM mem.entities WHERE org_id = :o AND id = ANY(:i)"), {"o": org_id, "i": list(ent_ids)})).all()}
    ev = await profile._evidence(db, [f.id for f in rows])
    out = []
    for f in rows:
        ext = f.source_class in facts.EXTERNAL_CLASSES
        who = None if f.attributed_to is None else {"id": str(f.attributed_to), "label": names.get(f.attributed_to, "")}
        val = names.get(f.object_entity_id, "") if f.object_entity_id is not None else profile_read.value_text(f.object_value)
        out.append({"fact_id": str(f.id), "fact_ids": [str(f.id)], "evidence_ids": [e["id"] for e in ev.get(f.id, [])],
                    "subject": {"id": str(f.subject_id), "label": names.get(f.subject_id, "")}, "predicate": f.predicate, "text": val,
                    "object_entity_id": None if f.object_entity_id is None else str(f.object_entity_id), "status": f.status,
                    "valid_from": f.valid_from, "valid_to": f.valid_to, "recorded_at": f.recorded_at, "source_class": f.source_class,
                    "untrusted": ext, "attributed_to": who,
                    "attribution": (f"theo lời {(who or {}).get('label') or 'người ngoài'}" if ext else None),
                    "confidence_content": f.confidence_content, "sensitivity": f.sensitivity,
                    "dispute_id": None if f.dispute_id is None else str(f.dispute_id)})
    return out


async def _facts_rows(db: AsyncSession, org_id: uuid.UUID, where: str, params: dict[str, Any], *, limit: int = 200) -> list[facts.Fact]:
    cols = ", ".join("f." + c.strip() for c in facts._COLS.split(","))
    rows = (await db.execute(text(f"SELECT {cols} FROM mem.facts f WHERE f.org_id = :o AND {where} ORDER BY f.valid_from DESC, f.id LIMIT :lim"),  # noqa: S608
                             {"o": org_id, "lim": limit, **params})).all()
    return [facts._fact(r) for r in rows]


def _pin_filter(user: service.CurrentUser, rows: list[facts.Fact]) -> tuple[list[facts.Fact], int]:
    if user.pin_active():
        return rows, 0
    shown = [f for f in rows if f.sensitivity == "normal"]
    return shown, len(rows) - len(shown)


# ─── Hồ sơ Sếp ──────────────────────────────────────────────────────────────

@router.get("/me/profile")
async def get_profile(user: service.CurrentUser = Depends(require_owner), db: AsyncSession = DB) -> dict[str, Any]:
    return await profile.read(db, user.org_id)


class ItemIn(BaseModel):
    section: Literal["goals", "resources", "key_relations", "values_limits", "rhythm", "report_prefs"]
    text: str | None = Field(default=None, max_length=2000)
    object_entity_id: uuid.UUID | None = None

    @model_validator(mode="after")
    def _one(self) -> ItemIn:
        if (self.text is None) == (self.object_entity_id is None):
            raise ValueError("cần đúng một trong: text, object_entity_id")
        return self


class ItemPatch(BaseModel):
    text: str | None = Field(default=None, max_length=2000)
    object_entity_id: uuid.UUID | None = None

    @model_validator(mode="after")
    def _one(self) -> ItemPatch:
        if (self.text is None) == (self.object_entity_id is None):
            raise ValueError("cần đúng một trong: text, object_entity_id")
        return self


class DecideIn(BaseModel):
    decision: Literal["confirm", "reject"]


async def _item_body(db: AsyncSession, org_id: uuid.UUID, res: profile.ItemResult, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    owner = await entities.owner(db, org_id)
    f = res.fact
    ver = (await db.execute(text("SELECT version FROM mem.owner_profile WHERE org_id = :o"), {"o": org_id})).scalar_one_or_none()
    item = (await _present(db, org_id, [f]))[0]
    section = profile.PREDICATE_SECTION.get(f.predicate)
    return {"breadcrumb": [_crumb("me", owner.id, "Tôi"), _crumb("evidence", f.id, f.predicate)], "outcome": res.outcome, "section": section,
            "item": item, "fact_ids": [str(f.id)], "evidence_ids": item["evidence_ids"],
            "replaced_fact_id": None if res.replaced_fact_id is None else str(res.replaced_fact_id), "version": int(ver or 0), **(extra or {})}


@router.post("/me/profile/items", status_code=201)
async def add_item(body: ItemIn, user: service.CurrentUser = Depends(require_owner), db: AsyncSession = DB) -> dict[str, Any]:
    if not user.pin_active():                                       # PIN TRƯỚC mọi lần ghi
        raise pin_required()
    clean = None
    if body.text is not None:
        try:
            clean = profile.clean_text(body.text)
        except profile.ProfileError as e:
            raise _perr(e) from e
    quiet = clean is not None and profile.is_quiet_hours(body.section, clean)
    log_id = await actionlog.record(db, org_id=user.org_id, actor_type="user", actor_id=user.actor_id, action="mem.profile_add",
                                    target_type="owner_profile", target_id=body.section, target_label=body.section,
                                    detail={"section": body.section, "quiet_hours": quiet}, ip=user.ip)
    try:
        res = await profile.add_item(db, user.org_id, user_id=user.id, section=body.section, text_value=clean,
                                     object_entity_id=body.object_entity_id, action_log_id=log_id, via="console")
    except profile.ProfileError as e:
        raise _perr(e) from e
    return await _item_body(db, user.org_id, res, {"notify_secondary": quiet})


@router.patch("/me/profile/items/{fact_id}")
async def edit_item(fact_id: uuid.UUID, body: ItemPatch, user: service.CurrentUser = Depends(require_owner),
                    db: AsyncSession = DB) -> dict[str, Any]:
    if not user.pin_active():
        raise pin_required()
    try:
        old = await profile._profile_fact(db, user.org_id, fact_id)   # 404 sớm khi không phải mục Hồ sơ của tổ chức này
        clean = None if body.text is None else profile.clean_text(body.text)
    except profile.ProfileError as e:
        raise _perr(e) from e
    quiet = clean is not None and profile.is_quiet_hours(old.owner_section, clean)
    log_id = await actionlog.record(db, org_id=user.org_id, actor_type="user", actor_id=user.actor_id, action="mem.profile_edit",
                                    target_type="owner_profile", target_id=str(fact_id), target_label="owner_profile",
                                    detail={"section": old.owner_section, "quiet_hours": quiet}, ip=user.ip)
    try:
        res = await profile.edit_item(db, user.org_id, fact_id, user_id=user.id, text_value=clean,
                                      object_entity_id=body.object_entity_id, action_log_id=log_id)
    except profile.ProfileError as e:
        raise _perr(e) from e
    return await _item_body(db, user.org_id, res, {"notify_secondary": quiet})


@router.delete("/me/profile/items/{fact_id}")
async def remove_item(fact_id: uuid.UUID, user: service.CurrentUser = Depends(require_owner), db: AsyncSession = DB) -> dict[str, Any]:
    if not user.pin_active():
        raise pin_required()
    try:
        await profile._profile_fact(db, user.org_id, fact_id)
    except profile.ProfileError as e:
        raise _perr(e) from e
    log_id = await actionlog.record(db, org_id=user.org_id, actor_type="user", actor_id=user.actor_id, action="mem.profile_remove",
                                    target_type="owner_profile", target_id=str(fact_id), target_label="owner_profile", detail={}, ip=user.ip)
    try:
        res = await profile.remove_item(db, user.org_id, fact_id, user_id=user.id, action_log_id=log_id)
    except profile.ProfileError as e:
        raise _perr(e) from e
    return await _item_body(db, user.org_id, res)


@router.post("/me/profile/proposals/{fact_id}/decide")
async def decide_proposal(fact_id: uuid.UUID, body: DecideIn, user: service.CurrentUser = Depends(require_owner),
                          db: AsyncSession = DB) -> dict[str, Any]:
    """Cùng đường với ask `source='profile'` (Đợt 2 chỉ gắn thêm hàng ask). Xác nhận mục nhạy cảm (phòng thủ) cần PIN; từ chối không cần."""
    try:
        prop = await profile.get_for_decision(db, user.org_id, fact_id)
    except profile.ProfileError as e:
        raise _perr(e) from e
    if body.decision == "confirm" and profile.decision_needs_pin(prop) and not user.pin_active():
        raise pin_required()
    log_id = await actionlog.record(db, org_id=user.org_id, actor_type="user", actor_id=user.actor_id, action="mem.profile_decide",
                                    target_type="owner_profile", target_id=str(fact_id), target_label=prop.owner_section,
                                    detail={"decision": body.decision, "section": prop.owner_section}, ip=user.ip)
    try:
        res = await profile.decide(db, user.org_id, fact_id, body.decision, user_id=user.id, action_log_id=log_id)
    except profile.ProfileError as e:
        raise _perr(e) from e
    quiet = body.decision == "confirm" and profile.is_quiet_hours(prop.owner_section, profile._row_text(prop))
    return await _item_body(db, user.org_id, res, {"notify_secondary": quiet})


# ─── nghĩa vụ, quan hệ, thời gian ───────────────────────────────────────────

@router.get("/me/obligations")
async def get_obligations(user: service.CurrentUser = Depends(require_owner), db: AsyncSession = DB) -> dict[str, Any]:
    """Lời hứa/nghĩa vụ Sếp đang mang (`mine`) và người khác nợ Sếp (`owed_to_me`: `object_value.to_entity_id` trỏ Sếp), theo hạn."""
    org = user.org_id
    owner = await _owner(db, org)
    members = await entities.members(db, owner.id)
    base = "f.predicate IN ('promised_to', 'owes') AND f.status IN ('owner_confirmed', 'asserted', 'disputed') AND (f.valid_to IS NULL OR f.valid_to > now())"
    mine = await _facts_rows(db, org, f"{base} AND f.subject_id = ANY(:m)", {"m": members})
    owed = await _facts_rows(db, org, f"{base} AND NOT (f.subject_id = ANY(:m)) AND jsonb_typeof(f.object_value) = 'object' "
                                      "AND (f.object_value->>'to_entity_id') = ANY(:ms)", {"m": members, "ms": [str(x) for x in members]})
    now = datetime.now(UTC)

    async def shape(rows: list[facts.Fact]) -> list[dict[str, Any]]:
        out = await _present(db, org, rows)
        for f, o in zip(rows, out, strict=True):
            due = _when(f.object_value.get("due_at")) if isinstance(f.object_value, dict) else None
            o["due_at"], o["overdue"] = due, due is not None and due < now
            o["due_soon"] = due is not None and now <= due <= now + timedelta(hours=48)
        out.sort(key=lambda x: (x["due_at"] is None, x["due_at"] or now, x["fact_id"]))
        return out

    return {"breadcrumb": [_crumb("me", owner.id, "Tôi")], "mine": await shape(mine), "owed_to_me": await shape(owed)}


@router.get("/me/relations")
async def get_relations(top: int = Query(20, ge=1, le=100), user: service.CurrentUser = Depends(require_owner),
                        db: AsyncSession = DB) -> dict[str, Any]:
    """Quan hệ quan trọng (`owner_key_relation`, kể cả "em đoán") + chuỗi độ nóng 12 tuần; còn chỗ thì thêm người/nhóm nóng nhất gần đây."""
    org = user.org_id
    owner = await _owner(db, org)
    key = (await db.execute(text("""
        SELECT v.fact_id, v.status, coalesce(e.merged_into, e.id) AS eid
        FROM mem.v_owner_profile v JOIN mem.entities e ON e.id = v.object_entity_id
        WHERE v.org_id = :o AND v.predicate = 'owner_key_relation' AND e.forgotten_at IS NULL AND e.org_id = :o
        ORDER BY (v.status = 'owner_confirmed') DESC, v.valid_from LIMIT :n"""), {"o": org, "n": top})).all()
    picked: dict[uuid.UUID, dict[str, Any]] = {}
    for r in key:
        picked.setdefault(r.eid, {"kind": "key_relation", "status": r.status, "fact_ids": [str(r.fact_id)]})
    if len(picked) < top:
        rest = (await db.execute(text("""
            SELECT h.entity_b FROM mem.relation_heat_weekly h JOIN mem.entities e ON e.id = h.entity_b
            WHERE h.org_id = :o AND h.entity_a = :a AND h.week >= :w AND e.forgotten_at IS NULL AND e.merged_into IS NULL
            GROUP BY h.entity_b ORDER BY sum(h.heat) DESC, h.entity_b LIMIT :n"""),
                                  {"o": org, "a": owner.id, "w": heat.week_start(datetime.now(UTC)) - timedelta(weeks=3), "n": top * 2})).scalars().all()
        for b in rest:
            if len(picked) >= top:
                break
            picked.setdefault(b, {"kind": "active", "status": None, "fact_ids": []})
    ids = list(picked)
    names = {r.id: r.display_name for r in (await db.execute(text("SELECT id, display_name FROM mem.entities WHERE org_id = :o AND id = ANY(:i)"),
                                                              {"o": org, "i": ids})).all()} if ids else {}
    ser = await heat.series_many(db, org, owner.id, ids, heat.DEFAULT_WEEKS) if ids else {}
    ev = await profile._evidence(db, [uuid.UUID(f) for p in picked.values() for f in p["fact_ids"]])
    items = []
    for eid, p in picked.items():
        line = ser.get(eid, [])
        items.append({"entity_id": str(eid), "label": names.get(eid, ""), **p,
                      "evidence_ids": [e["id"] for f in p["fact_ids"] for e in ev.get(uuid.UUID(f), [])],
                      "heat": line, "state": line[-1]["state"] if line else "cold", "cooling": heat.cooling(line)})
    return {"breadcrumb": [_crumb("me", owner.id, "Tôi")], "weeks": heat.DEFAULT_WEEKS, "relations": items}


@router.get("/me/time")
async def get_time(days: int = Query(14, ge=1, le=90), user: service.CurrentUser = Depends(require_owner),
                   db: AsyncSession = DB) -> dict[str, Any]:
    """Dịp, hạn, lịch hẹn trong `days` ngày tới (facts `scheduled_event`, `deadline`; giờ lấy từ `object_value.at|due_at|when`)."""
    org = user.org_id
    owner = await _owner(db, org)
    now = datetime.now(UTC)
    rows = await _facts_rows(db, org, "f.predicate IN ('scheduled_event', 'deadline') AND f.status IN ('owner_confirmed', 'asserted', 'disputed') "
                                      "AND (f.valid_to IS NULL OR f.valid_to > now())", {}, limit=500)
    keep: list[tuple[datetime, facts.Fact]] = []
    for f in rows:
        if isinstance(f.object_value, dict):
            t = next((x for x in (_when(f.object_value.get(k)) for k in ("at", "due_at", "when")) if x is not None), None)
            if t is not None and now <= t <= now + timedelta(days=days):
                keep.append((t, f))
    keep.sort(key=lambda x: (x[0], str(x[1].id)))
    shown = await _present(db, org, [f for _, f in keep])
    for (t, _), o in zip(keep, shown, strict=True):
        o["at"] = t
    return {"breadcrumb": [_crumb("me", owner.id, "Tôi")], "days": days, "items": shown}


# ─── Đối tượng ──────────────────────────────────────────────────────────────

async def _entity(db: AsyncSession, org_id: uuid.UUID, entity_id: uuid.UUID) -> entities.Entity:
    ent = await entities.get(db, entity_id)
    if ent is None or ent.org_id != org_id or ent.forgotten:
        raise not_found("Đối tượng")
    if ent.merged_into is not None:                              # id cũ của thực thể đã gộp ⇒ trả thực thể đích
        ent = await entities.get(db, ent.merged_into)
        if ent is None or ent.org_id != org_id or ent.forgotten:
            raise not_found("Đối tượng")
    return ent


@router.get("/entities/{entity_id}")
async def get_entity(entity_id: uuid.UUID, user: service.CurrentUser = Depends(require_owner), db: AsyncSession = DB) -> dict[str, Any]:
    """Tầng Đối tượng: sự thật đang hiệu lực + lịch sử đã đóng, tóm tắt (mỗi dòng trỏ `fact_ids`), việc đang mở, độ nóng với Sếp."""
    org = user.org_id
    ent = await _entity(db, org, entity_id)
    members = await entities.members(db, ent.id)
    live = await _facts_rows(db, org, "f.subject_id = ANY(:m) AND f.status IN ('owner_confirmed', 'asserted', 'disputed') AND f.evidence_n >= 1 "
                                      "AND f.valid_from <= now() AND (f.valid_to IS NULL OR f.valid_to > now())", {"m": members})
    closed = await _facts_rows(db, org, "f.subject_id = ANY(:m) AND (f.status IN ('superseded', 'retracted') OR "
                                        "(f.status IN ('owner_confirmed', 'asserted') AND f.valid_to IS NOT NULL AND f.valid_to <= now()))",
                               {"m": members}, limit=100)
    live, h1 = _pin_filter(user, live)
    closed, h2 = _pin_filter(user, closed)
    opened = [f for f in live if f.predicate in OPEN_PREDICATES]
    summ = (await db.execute(text("SELECT summary, fact_ids, built_at FROM mem.entity_summaries WHERE entity_id = :e AND org_id = :o"),
                             {"e": ent.id, "o": org})).one_or_none()
    owner = await _owner(db, org)
    line: list[dict[str, Any]] = []
    if ent.id != owner.id:
        line = await heat.series(db, org, owner.id, ent.id, heat.DEFAULT_WEEKS)
    open_disputes = [{"id": str(d.id), "predicate": d.predicate, "fact_ids": [str(x) for x in d.fact_ids], "opened_at": d.opened_at}
                     for d in await disputes.list(db, org, open_only=True, subject_id=ent.id)]
    summary = None
    if summ is not None:
        lines = summ.summary.split("\n")
        summary = {"built_at": summ.built_at, "lines": [{"text": t, "fact_ids": [str(f)]} for t, f in zip(lines, summ.fact_ids, strict=False)],
                   "fact_ids": [str(f) for f in summ.fact_ids]}
    return {"breadcrumb": [_crumb("object", ent.id, ent.display_name)],
            "entity": {"id": str(ent.id), "type": ent.entity_type, "display_name": ent.display_name, "is_owner": ent.is_owner},
            "facts": await _present(db, org, live), "history": await _present(db, org, closed), "open_items": await _present(db, org, opened),
            "open_disputes": open_disputes, "summary": summary,
            "heat": {"series": line, "state": line[-1]["state"] if line else None, "cooling": heat.cooling(line)} if line else None,
            "hidden_pin_n": h1 + h2}


@router.get("/entities/{entity_id}/timeline")
async def get_timeline(entity_id: uuid.UUID, lanes: str | None = None, from_: datetime | None = Query(None, alias="from"),
                       to: datetime | None = None, user: service.CurrentUser = Depends(require_owner),
                       db: AsyncSession = DB) -> dict[str, Any]:
    """Các làn thời gian kiểu LifeLines (`promises,events,conflicts,transactions`), lọc theo khoảng `from`–`to` (giao với hiệu lực)."""
    org = user.org_id
    ent = await _entity(db, org, entity_id)
    want = [x for x in (lanes.split(",") if lanes else list(LANES)) if x]
    bad = [x for x in want if x not in LANES]
    if bad:
        raise ApiError(422, "VALIDATION", "Làn thời gian không hợp lệ", detail=f"lanes lạ: {bad}; hợp lệ: {list(LANES)}")
    if from_ is not None and to is not None and to < from_:
        raise ApiError(422, "VALIDATION", "Khoảng thời gian không hợp lệ", detail="to < from")
    members = await entities.members(db, ent.id)
    preds = sorted({p for w in want for p in LANES[w]})
    rows = await _facts_rows(db, org, "f.subject_id = ANY(:m) AND f.predicate = ANY(:p) AND f.status IN ('owner_confirmed', 'asserted', 'disputed') "
                                      "AND (CAST(:to AS timestamptz) IS NULL OR f.valid_from <= CAST(:to AS timestamptz)) "
                                      "AND (CAST(:fr AS timestamptz) IS NULL OR f.valid_to IS NULL OR f.valid_to >= CAST(:fr AS timestamptz))",
                             {"m": members, "p": preds, "to": to, "fr": from_}, limit=500)
    rows, hidden = _pin_filter(user, rows)
    shaped = {f.id: o for f, o in zip(rows, await _present(db, org, rows), strict=True)}
    out = {w: sorted((shaped[f.id] for f in rows if f.predicate in LANES[w]), key=lambda x: (x["valid_from"], x["fact_id"])) for w in want}
    return {"breadcrumb": [_crumb("object", ent.id, ent.display_name)], "entity_id": str(ent.id), "from": from_, "to": to,
            "lanes": out, "hidden_pin_n": hidden}


# ─── Bằng chứng ─────────────────────────────────────────────────────────────

@router.get("/evidence/raw/{raw_id}")
async def get_evidence_raw(raw_id: uuid.UUID, around: int = Query(5, ge=0, le=20), user: service.CurrentUser = Depends(require_owner),
                           db: AsyncSession = DB) -> dict[str, Any]:
    """Tầng Bằng chứng: tin gốc + đoạn tô sáng (span đã kiểm) + ±`around` tin cùng luồng + các sự thật dựa vào tin (để nút "Sai / Sửa sự thật"
    gọi `POST /facts/{id}/correct`). Chỉ tin của tổ chức người gọi."""
    org = user.org_id
    row = (await db.execute(text(RAW_SELECT + " WHERE e.org_id = :o AND e.id = :i"), {"o": org, "i": raw_id})).one_or_none()
    if row is None:
        raise not_found("Tin gốc")
    anchor = raw_item(row)
    own = (await db.execute(text("SELECT e.occurred_at, e.channel_id, e.group_id, e.direction, e.sender_identity_id, e.payload "
                                 "FROM raw.events e WHERE e.org_id = :o AND e.id = :i"), {"o": org, "i": raw_id})).one()
    ctx_where, ctx_params = "FALSE", {}
    if own.group_id is not None:
        ctx_where, ctx_params = "e.group_id = :grp", {"grp": own.group_id}
    else:
        p = own.payload if isinstance(own.payload, dict) else {}
        raw_key = p.get("key")
        key: dict[str, Any] = raw_key if isinstance(raw_key, dict) else {}
        person = None
        if own.direction == "inbound" and own.sender_identity_id is not None:
            person = (await db.execute(text("SELECT person_id FROM core.person_identities WHERE id = :i"), {"i": own.sender_identity_id})).scalar_one_or_none()
        else:
            ks = [str(v).split("@", 1)[0] for v in (p.get("threadId"), key.get("remoteJid"), key.get("remoteJidAlt"), p.get("recipient")) if v]
            if ks:
                person = (await db.execute(text("SELECT person_id FROM core.person_identities WHERE channel_id = :c AND external_id = ANY(:k) LIMIT 1"),
                                           {"c": own.channel_id, "k": ks})).scalar_one_or_none()
        if person is not None:
            idrows = (await db.execute(text("SELECT id, external_id FROM core.person_identities WHERE person_id = :p AND channel_id = :c"),
                                       {"p": person, "c": own.channel_id})).all()
            ctx_params = {"ids": [r.id for r in idrows], "ext": [r.external_id for r in idrows], "ch": own.channel_id}
            ctx_where = ("e.channel_id = :ch AND e.group_id IS NULL AND ((e.direction = 'inbound' AND e.sender_identity_id = ANY(CAST(:ids AS uuid[]))) OR "
                         + outbound_to_sql("e", "CAST(:ids AS uuid[])", "CAST(:ext AS text[])") + ")")
    before: list[dict[str, Any]] = []
    after: list[dict[str, Any]] = []
    if around and ctx_where != "FALSE":
        b = (await db.execute(text(RAW_SELECT + f" WHERE e.org_id = :o AND ({ctx_where}) AND (e.occurred_at, e.id) < (:t, :i) "
                                                "ORDER BY e.occurred_at DESC, e.id DESC LIMIT :n"),  # noqa: S608
                              {"o": org, "t": own.occurred_at, "i": raw_id, "n": around, **ctx_params})).all()
        a = (await db.execute(text(RAW_SELECT + f" WHERE e.org_id = :o AND ({ctx_where}) AND (e.occurred_at, e.id) > (:t, :i) "
                                                "ORDER BY e.occurred_at, e.id LIMIT :n"),  # noqa: S608
                              {"o": org, "t": own.occurred_at, "i": raw_id, "n": around, **ctx_params})).all()
        before = [raw_item(r) for r in reversed(b)]
        after = [raw_item(r) for r in a]
    hl: list[dict[str, Any]] = []
    fx = (await db.execute(text("""
        SELECT x.id AS evidence_id, x.fact_id, x.quote_status, x.span, f.predicate, f.status, f.sensitivity
        FROM mem.fact_evidence x JOIN mem.facts f ON f.id = x.fact_id
        WHERE x.org_id = :o AND f.org_id = :o AND x.raw_event_id = :r AND f.status NOT IN ('retracted') ORDER BY x.created_at, x.id"""),
                           {"o": org, "r": raw_id})).all()
    hidden = 0
    fact_refs: dict[str, dict[str, Any]] = {}
    for r in fx:
        if r.sensitivity != "normal" and not user.pin_active():
            hidden += 1
            continue
        sp = r.span or {}
        hl.append({"source": "fact", "fact_id": str(r.fact_id), "evidence_id": str(r.evidence_id), "source_field": sp.get("source_field"),
                   "span_start": sp.get("span_start"), "span_end": sp.get("span_end"), "quote_status": r.quote_status})
        fact_refs.setdefault(str(r.fact_id), {"fact_id": str(r.fact_id), "predicate": r.predicate, "status": r.status, "can_correct": True})
    for r in (await db.execute(text("""
        SELECT ev.meaning_unit_id, ev.source_field, ev.span_start, ev.span_end, ev.quote_status
        FROM clean.evidence ev JOIN clean.meaning_units u ON u.id = ev.meaning_unit_id AND u.observed_at = ev.meaning_observed_at
        WHERE ev.raw_event_id = :r AND u.org_id = :o"""), {"r": raw_id, "o": org})).all():
        hl.append({"source": "meaning_unit", "meaning_unit_id": str(r.meaning_unit_id), "source_field": r.source_field,
                   "span_start": r.span_start, "span_end": r.span_end, "quote_status": r.quote_status})
    return {"breadcrumb": [_crumb("evidence", raw_id, anchor.get("code") or str(raw_id))], "message": anchor, "highlights": hl,
            "context": {"before": before, "after": after}, "facts": list(fact_refs.values()),
            "fact_ids": list(fact_refs), "evidence_ids": [h["evidence_id"] for h in hl if h.get("evidence_id")], "hidden_pin_n": hidden,
            "occurred_at": iso(own.occurred_at)}


# ─── Toàn cảnh / Bối cảnh / tín hiệu của mục Hồ sơ (T9) ─────────────────────
# LƯU Ý đường dẫn: spec ghi `GET /overview`, nhưng `/overview` đã là Tổng quan Console (`biz/queue/routes.py`, quyền `overview.read`) ⇒ Toàn cảnh đặt ở `/overview/map`.
# Quyền đọc như `/signals` (`queue.read`, mọi vai): vai khác Owner chỉ thấy nhóm theo `view_scope`, không thấy `console_pin`, không có vòng người.

@router.get("/overview/map")
async def get_overview(weeks: int = Query(default=8, ge=1, le=heat.MAX_WEEKS), user: service.CurrentUser = Depends(require("queue.read")),
                       db: AsyncSession = DB) -> dict[str, Any]:
    return await overview.overview(db, user, weeks)


@router.get("/contexts/{code}")
async def get_context(code: str, user: service.CurrentUser = Depends(require("queue.read")), db: AsyncSession = DB) -> dict[str, Any]:
    return await overview.context_detail(db, user, code)


@router.get("/me/profile/items/{fact_id}/signals")
async def get_profile_item_signals(fact_id: uuid.UUID, user: service.CurrentUser = Depends(require_owner), db: AsyncSession = DB) -> dict[str, Any]:
    return await overview.profile_item_signals(db, user, fact_id)
