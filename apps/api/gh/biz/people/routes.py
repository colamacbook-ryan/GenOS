"""API Con người & Chất lượng (docs/api/phase-3-people.md): Đánh giá con người + Phản biện (spec I), Chất lượng
chăm sóc.

Quy tắc bắt buộc (PLAN §3.11, Q4 — bảng quyết định của chủ dự án):
- **Chỉ Owner thấy nội dung** (điểm, tín hiệu, khuyến nghị, chứng cứ) của Đánh giá con người, khoá mức Owner.
- **Auditor gọi API vẫn `200`** nhưng nội dung bị ẩn — chỉ thấy "đã có đánh giá" + nhật ký ai đã xem; mỗi lần
  Auditor xem cũng tự ghi thêm một dòng vào chính nhật ký đó.
- **Manager mặc định `403`** (ẩn hẳn) như mọi vai trò khác không có quyền — cho tới khi Owner tự cấp thêm trong
  Quyền hạn (`people_review.read` khác `none` cho vai trò đó, giai đoạn 4 mới có màn chỉnh ma trận).
- **Không có hành động kỷ luật tự động**: mọi PATCH/POST ở đây chỉ ghi lại quyết định của NGƯỜI (sửa điểm tay,
  giải quyết phản biện) — không có chỗ nào tự đổi quyền, tự khoá tài khoản hay tự tạo việc kỷ luật.

`_access_mode()` quyết định 3 nhánh trên dựa trên `people_review.read` của ma trận **và** vai trò Auditor cụ thể
(không suy từ scope chung, vì Auditor không có quyền `people_review.read` nào để `scope_for` chấp nhận — nhánh
"thấy nhật ký" là hành vi riêng của đúng vai trò Auditor, không phải một mức phạm vi).
"""

import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any, Literal

import orjson
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gh.auth import rbac, service
from gh.auth.deps import current_user, require, require_pin
from gh.biz.core import explain
from gh.biz.core.scope import Scope, not_found, scope_for
from gh.biz.people import service as psvc
from gh.biz.queue import service as qsvc
from gh.chassis import actionlog
from gh.data.common import iso, mask_text, parse_cursor
from gh.db import DB
from gh.errors import ApiError, field_errors, forbidden, model_unavailable, pin_required
from gh.providers.clients import Message
from gh.providers.router import ModelUnavailable

router = APIRouter(tags=["people"])

CARE_READ = require("care.read")
PIN_OP = "people_review.read"          # danh mục PIN chung cho mọi thao tác xem/sửa đánh giá nhân sự


def _access_mode(user: service.CurrentUser) -> Literal["full", "log", "none"]:
    if user.permissions.get("people_review.read", rbac.NONE) != rbac.NONE:
        return "full"
    if user.role_code == rbac.AUDITOR:
        return "log"
    return "none"


def _masked_person_ref(r: Any, prefix: str, owner: bool) -> dict[str, Any] | None:
    ref = psvc.person_ref(r, prefix)
    if ref is not None:
        ref["name"] = mask_text(ref["name"], owner)
    return ref


# ═══ Đánh giá con người ═══════════════════════════════════════════════════════

_REVIEW_SELECT_BASE = """
SELECT DISTINCT ON (r.person_id, r.period_start, r.period_end)
    r.id, r.person_id, r.period_start, r.period_end, r.score, r.trend, r.signal, r.recommendation, r.evidence,
    r.visibility, r.created_at, r.overridden_by, r.overridden_at, r.override_reason, r.supersedes_id,
    r.suspicious, r.suspicious_reason, r.suspicious_cleared_by, r.suspicious_cleared_at, r.suspicious_cleared_reason,
    cb.display_name AS cb_name, cbr.code AS cb_role,
    p.id AS p_id, p.code AS p_code, p.display_name AS p_name, p.person_type AS p_type,
    p.organization_name AS p_org, ob.display_name AS ob_name, obr.code AS ob_role
FROM biz.people_reviews r
JOIN core.persons p ON p.id = r.person_id
LEFT JOIN core.users ob ON ob.id = r.overridden_by
""" + psvc.USER_ROLE_JOIN.format(alias="ob", out="obr") + """
LEFT JOIN core.users cb ON cb.id = r.suspicious_cleared_by
""" + psvc.USER_ROLE_JOIN.format(alias="cb", out="cbr") + "\n"

_DISPUTE_SELECT = """
SELECT d.id, d.review_id, d.raised_by, d.body, d.status, d.resolution, d.resolved_by, d.resolved_at, d.created_at,
       ru.display_name AS raised_name, rur.code AS raised_role,
       su.display_name AS resolved_name, sur.code AS resolved_role
FROM biz.review_disputes d
JOIN core.users ru ON ru.id = d.raised_by
""" + psvc.USER_ROLE_JOIN.format(alias="ru", out="rur") + """
LEFT JOIN core.users su ON su.id = d.resolved_by
""" + psvc.USER_ROLE_JOIN.format(alias="su", out="sur") + "\n"


def _review_sql(where: str) -> str:
    return (_REVIEW_SELECT_BASE + f"WHERE {where} "
            "ORDER BY r.person_id, r.period_start, r.period_end, r.created_at DESC")


def _review_item(r: Any) -> dict[str, Any]:
    return {"id": str(r.id), "person": psvc.person_ref(r, "p"), "period_start": r.period_start.isoformat(),
            "period_end": r.period_end.isoformat(), "score": float(r.score), "trend": r.trend, "signal": r.signal,
            "recommendation": r.recommendation, "evidence": list(r.evidence or []), "visibility": r.visibility,
            "created_at": iso(r.created_at), "overridden": r.overridden_by is not None,
            "overridden_by": psvc.user_ref(r.overridden_by, r.ob_name, r.ob_role),
            "overridden_at": iso(r.overridden_at), "override_reason": r.override_reason,
            "supersedes_id": str(r.supersedes_id) if r.supersedes_id else None,
            # v0.1.45 (F-60): cờ 'Đáng ngờ' — chỉ cảnh báo, không đổi điểm. `suspicious` là cờ ĐANG hiệu lực (đã bỏ
            # cờ → false); `suspicious_cleared` cho biết ai bỏ cờ, lúc nào, vì sao (lý do quét gốc vẫn giữ).
            "suspicious": bool(r.suspicious) and r.suspicious_cleared_at is None,
            "suspicious_reason": r.suspicious_reason,
            "suspicious_cleared": None if r.suspicious_cleared_at is None else {
                "by": psvc.user_ref(r.suspicious_cleared_by, r.cb_name, r.cb_role),
                "at": iso(r.suspicious_cleared_at), "reason": r.suspicious_cleared_reason}}


def _dispute_item(r: Any) -> dict[str, Any]:
    return {"id": str(r.id), "review_id": str(r.review_id),
            "raised_by": psvc.user_ref(r.raised_by, r.raised_name, r.raised_role), "body": r.body,
            "status": r.status, "resolution": r.resolution,
            "resolved_by": psvc.user_ref(r.resolved_by, r.resolved_name, r.resolved_role),
            "resolved_at": iso(r.resolved_at), "created_at": iso(r.created_at)}


async def _in_scope(db: AsyncSession, sc: Scope, person_id: uuid.UUID) -> bool:
    if sc.is_all:
        return True
    where, params = sc.person_sql("sp")
    row = (await db.execute(text(f"SELECT 1 FROM core.persons sp WHERE sp.id = :pid AND {where}"),  # noqa: S608
                            {"pid": person_id, **params})).first()
    return row is not None


async def _review_or_404(db: AsyncSession, sc: Scope, org_id: uuid.UUID, review_id: uuid.UUID) -> Any:
    row = (await db.execute(text(_review_sql("r.id = :i AND r.org_id = :o")),
                            {"i": review_id, "o": org_id})).one_or_none()
    if row is None:
        raise not_found("Đánh giá")
    if not await _in_scope(db, sc, row.person_id):
        raise not_found("Đánh giá")
    return row


async def _history(db: AsyncSession, person_id: uuid.UUID, ps: date, pe: date) -> list[dict[str, Any]]:
    rows = (await db.execute(text("""
        SELECT r.id, r.score, r.trend, r.created_at, r.overridden_by, r.override_reason, u.display_name AS by_name
        FROM biz.people_reviews r LEFT JOIN core.users u ON u.id = r.overridden_by
        WHERE r.person_id = :p AND r.period_start = :ps AND r.period_end = :pe
        ORDER BY r.created_at DESC"""), {"p": person_id, "ps": ps, "pe": pe})).all()
    return [{"id": str(r.id), "score": float(r.score), "trend": r.trend, "created_at": iso(r.created_at),
             "overridden_by": psvc.user_ref(r.overridden_by, r.by_name), "override_reason": r.override_reason}
            for r in rows]


async def _disputes_of(db: AsyncSession, review_id: uuid.UUID) -> list[dict[str, Any]]:
    rows = (await db.execute(text(_DISPUTE_SELECT + "WHERE d.review_id = :r ORDER BY d.created_at DESC"),
                             {"r": review_id})).all()
    return [_dispute_item(r) for r in rows]


VIEW_ACTIONS = ("people_review.viewed", "people_review.explained")


async def _viewer_log(db: AsyncSession, org_id: uuid.UUID, review_id: uuid.UUID,
                      limit: int = 10) -> list[dict[str, Any]]:
    rows = (await db.execute(text("""
        SELECT a.at, a.action, u.display_name, ur.code AS role
        FROM ops.action_log a
        LEFT JOIN core.users u ON a.actor_id = 'user:' || u.id::text
        """ + psvc.USER_ROLE_JOIN.format(alias="u", out="ur") + """
        WHERE a.org_id = :o AND a.target_type = 'people_review' AND a.target_id = :i
          AND a.action = ANY(:actions)
        ORDER BY a.at DESC LIMIT :n"""),
        {"o": org_id, "i": str(review_id), "actions": list(VIEW_ACTIONS), "n": limit})).all()
    return [{"who": r.display_name or "(đã rời tổ chức)", "role": r.role, "action": r.action, "at": iso(r.at)}
            for r in rows]


def _review_where(user: service.CurrentUser, board: str | None, person_id: uuid.UUID | None,
                  period_start: str | None, period_end: str | None) -> tuple[str, dict[str, Any]]:
    conds = ["r.org_id = :o"]
    params: dict[str, Any] = {"o": user.org_id}
    if board:
        conds.append("p.person_type = :pt")
        params["pt"] = psvc.BOARD_PERSON_TYPE[board]
    if person_id:
        conds.append("r.person_id = :pid")
        params["pid"] = person_id
    if period_start:
        conds.append("r.period_start = :ps")
        params["ps"] = date.fromisoformat(period_start)
    if period_end:
        conds.append("r.period_end = :pe")
        params["pe"] = date.fromisoformat(period_end)
    return " AND ".join(conds), params


async def _distinct_total(db: AsyncSession, where: str, params: dict[str, Any]) -> int:
    return (await db.execute(text(f"""SELECT count(*) FROM (
        SELECT DISTINCT r.person_id, r.period_start, r.period_end FROM biz.people_reviews r
        JOIN core.persons p ON p.id = r.person_id WHERE {where}) t"""), params)).scalar_one()  # noqa: S608


@router.get("/people/reviews")
async def list_reviews(board: Literal["employee", "customer", "candidate", "student"] | None = None,
                       person_id: uuid.UUID | None = None, period_start: str | None = None,
                       period_end: str | None = None, cursor: str | None = None,
                       limit: int = Query(50, ge=1, le=200), user: service.CurrentUser = Depends(current_user),
                       db: AsyncSession = DB) -> dict[str, Any]:
    mode = _access_mode(user)
    if mode == "none":
        raise forbidden(PIN_OP)
    if mode == "full" and not user.pin_active():
        raise pin_required()
    where, params = _review_where(user, board, person_id, period_start, period_end)
    total = await _distinct_total(db, where, params)
    cursor_cond = ""
    if cursor:
        cursor_cond = "WHERE latest.created_at < :c"
        params["c"] = parse_cursor(cursor)
    rows = (await db.execute(text(f"SELECT * FROM ({_review_sql(where)}) latest {cursor_cond} "
                                  f"ORDER BY latest.created_at DESC LIMIT :n"),  # noqa: S608
                             {**params, "n": limit + 1})).all()

    if mode == "log":
        items = []
        for r in rows[:limit]:
            items.append({"id": str(r.id), "person": psvc.person_ref(r, "p"),
                         "period_start": r.period_start.isoformat(), "period_end": r.period_end.isoformat(),
                         "created_at": iso(r.created_at), "has_content": True,
                         "viewed_by": await _viewer_log(db, user.org_id, r.id)})
        await actionlog.record(db, org_id=user.org_id, actor_type="user", actor_id=user.actor_id,
                               action="people_review.audit_viewed", target_type="people_review", target_id=None,
                               result="ok", detail={"count": len(items)}, ip=user.ip)
        return {"items": items, "next_cursor": iso(rows[limit - 1].created_at) if len(rows) > limit else None,
                "total": total}

    # mode == "full": danh sách cần phiên PIN còn hiệu lực (đã kiểm ở trên, ARCHITECTURE §8.2 "xem dữ liệu đánh
    # giá nhân sự"). KHÔNG ghi Action Log mỗi lần tải trang danh sách (quá dày) — chỉ ghi khi xem một đánh giá cụ
    # thể (GET /people/reviews/{id}) hoặc chứng cứ (`GET /explain/review/{id}`) — quyết định tự đưa ra.
    items = [_review_item(r) for r in rows[:limit]]
    return {"items": items, "next_cursor": iso(rows[limit - 1].created_at) if len(rows) > limit else None,
            "total": total}


@router.get("/people/reviews/{review_id}")
async def get_review(review_id: uuid.UUID, user: service.CurrentUser = Depends(current_user),
                     db: AsyncSession = DB) -> dict[str, Any]:
    mode = _access_mode(user)
    if mode == "none":
        raise forbidden(PIN_OP)
    row = (await db.execute(text(_review_sql("r.id = :i AND r.org_id = :o")),
                            {"i": review_id, "o": user.org_id})).one_or_none()
    if row is None:
        raise not_found("Đánh giá")

    if mode == "log":
        # Auditor: phạm vi toàn tổ chức (cùng `audit.read = all` của ma trận) — không lọc theo scope, vì nhật
        # ký "ai đã xem" vốn dùng để giám sát toàn bộ, không riêng phần được phân.
        dispute_count = (await db.execute(text("SELECT count(*) FROM biz.review_disputes WHERE review_id = :i"),
                                          {"i": review_id})).scalar_one()
        await actionlog.record(db, org_id=user.org_id, actor_type="user", actor_id=user.actor_id,
                               action="people_review.audit_viewed", target_type="people_review",
                               target_id=str(review_id), target_label=f"{row.p_name} · {row.period_start}–"
                               f"{row.period_end}", result="ok", ip=user.ip)
        return {"id": str(review_id), "person": psvc.person_ref(row, "p"),
                "period_start": row.period_start.isoformat(), "period_end": row.period_end.isoformat(),
                "created_at": iso(row.created_at), "has_content": True, "dispute_count": dispute_count,
                "viewed_by": await _viewer_log(db, user.org_id, review_id)}

    sc = await scope_for(db, user, "people_review.read")
    if not await _in_scope(db, sc, row.person_id):
        raise not_found("Đánh giá")
    if not user.pin_active():
        raise pin_required()
    item = _review_item(row)
    item["history"] = await _history(db, row.person_id, row.period_start, row.period_end)
    item["disputes"] = await _disputes_of(db, review_id)
    await actionlog.record(db, org_id=user.org_id, actor_type="user", actor_id=user.actor_id,
                           action="people_review.viewed", target_type="people_review", target_id=str(review_id),
                           target_label=f"{row.p_name} · {row.period_start}–{row.period_end}", result="ok",
                           ip=user.ip)
    return item


class ReviewEditIn(BaseModel):
    score: float = Field(ge=0, le=100)
    reason: str = Field(min_length=1, max_length=1000)
    evidence: list[dict[str, Any]] = Field(min_length=1, max_length=20)
    trend: Literal["up", "down", "flat"] | None = None
    signal: str | None = Field(default=None, max_length=500)
    recommendation: str | None = Field(default=None, max_length=1000)


@router.patch("/people/reviews/{review_id}")
async def edit_review_score(review_id: uuid.UUID, body: ReviewEditIn,
                            user: service.CurrentUser = Depends(require("people_review.write")),
                            _pin: service.CurrentUser = Depends(require_pin(PIN_OP)),
                            db: AsyncSession = DB) -> dict[str, Any]:
    """Sửa điểm tay — KHÔNG ghi đè: chèn một dòng mới (`supersedes_id` trỏ về dòng cũ, `overridden_by` = mình),
    dòng cũ vẫn còn nguyên trong `GET /people/reviews/{id}.history` (PLAN §3.11 "sửa điểm tay giữ lịch sử")."""
    for e in body.evidence:
        if not isinstance(e, dict) or "type" not in e or "id" not in e:
            raise field_errors({"evidence": 'Mỗi mục cần {"type", "id"}'})
    sc = await scope_for(db, user, "people_review.write")
    old = await _review_or_404(db, sc, user.org_id, review_id)
    new_id = (await db.execute(text("""
        INSERT INTO biz.people_reviews (org_id, person_id, period_start, period_end, score, trend, signal,
                                        recommendation, evidence, visibility, supersedes_id, overridden_by,
                                        overridden_at, override_reason, suspicious, suspicious_reason,
                                        suspicious_cleared_by, suspicious_cleared_at, suspicious_cleared_reason)
        VALUES (:o, :p, :ps, :pe, :sc, :tr, :sig, :rec, CAST(:ev AS jsonb), :vis, :sup, :by, now(), :reason,
                :sus, :sus_r, :cl_by, :cl_at, :cl_r)
        RETURNING id"""),
        {"o": user.org_id, "p": old.person_id, "ps": old.period_start, "pe": old.period_end, "sc": body.score,
         "tr": body.trend or old.trend, "sig": body.signal or old.signal,
         "rec": body.recommendation or old.recommendation, "ev": orjson.dumps(body.evidence).decode(),
         "vis": old.visibility, "sup": review_id, "by": user.id, "reason": body.reason,
         # Sửa tay không xoá cờ 'Đáng ngờ' của dòng bị thay (F-60) — bỏ cờ là thao tác riêng có lý do; đã bỏ thì
         # bản sửa tay mang theo dấu "đã bỏ cờ".
         "sus": bool(old.suspicious), "sus_r": old.suspicious_reason, "cl_by": old.suspicious_cleared_by,
         "cl_at": old.suspicious_cleared_at, "cl_r": old.suspicious_cleared_reason})).scalar_one()
    await actionlog.record(db, org_id=user.org_id, actor_type="user", actor_id=user.actor_id,
                           action="people_review.score_edited", target_type="people_review", target_id=str(new_id),
                           target_label=f"{old.p_name} · {old.period_start}–{old.period_end}", result="ok",
                           detail={"from_score": float(old.score), "to_score": body.score, "reason": body.reason,
                                  "previous_review_id": str(review_id)}, ip=user.ip)
    return await get_review(new_id, user, db)


class SuspiciousClearIn(BaseModel):
    cleared_reason: str = Field(min_length=3, max_length=1000)


@router.patch("/people/reviews/{review_id}/suspicious")
async def clear_review_suspicious(review_id: uuid.UUID, body: SuspiciousClearIn,
                                  user: service.CurrentUser = Depends(require("people_review.write")),
                                  _pin: service.CurrentUser = Depends(require_pin(PIN_OP)),
                                  db: AsyncSession = DB) -> dict[str, Any]:
    """Bỏ cờ 'Đáng ngờ' sau khi đã xem chứng cứ (F-60) — bắt buộc có lý do, ghi Nhật ký; giữ lý do quét gốc và ghi
    ai bỏ cờ. Không đổi điểm. Job chạy lại không gắn lại cờ đã bỏ."""
    reason = body.cleared_reason.strip()
    if len(reason) < 3:
        raise field_errors({"cleared_reason": "Ghi rõ lý do bỏ cờ (đã xem chứng cứ gì)"})
    sc = await scope_for(db, user, "people_review.write")
    row = await _review_or_404(db, sc, user.org_id, review_id)
    if not row.suspicious or row.suspicious_cleared_at is not None:
        raise ApiError(409, "NOT_SUSPICIOUS", "Đánh giá này không còn cờ 'Đáng ngờ' để bỏ")
    await db.execute(text("""UPDATE biz.people_reviews SET suspicious_cleared_by = :u, suspicious_cleared_at = now(),
                                    suspicious_cleared_reason = :r WHERE id = :i AND org_id = :o"""),
                     {"u": user.id, "r": reason, "i": review_id, "o": user.org_id})
    await actionlog.record(db, org_id=user.org_id, actor_type="user", actor_id=user.actor_id,
                           action="people_review.suspicious_cleared", target_type="people_review",
                           target_id=str(review_id),
                           target_label=f"{row.p_name} · {row.period_start}–{row.period_end}", result="ok",
                           detail={"reason": reason, "flag_reason": row.suspicious_reason}, ip=user.ip)
    return await get_review(review_id, user, db)


class DisputeIn(BaseModel):
    body: str = Field(min_length=1, max_length=2000)


@router.post("/people/reviews/{review_id}/disputes", status_code=201)
async def create_dispute(review_id: uuid.UUID, body: DisputeIn,
                         user: service.CurrentUser = Depends(require("people_review.write")),
                         _pin: service.CurrentUser = Depends(require_pin(PIN_OP)),
                         db: AsyncSession = DB) -> dict[str, Any]:
    sc = await scope_for(db, user, "people_review.write")
    row = await _review_or_404(db, sc, user.org_id, review_id)
    did = (await db.execute(text("""INSERT INTO biz.review_disputes (review_id, raised_by, body, status)
                                    VALUES (:r, :u, :b, 'open') RETURNING id"""),
                            {"r": review_id, "u": user.id, "b": body.body})).scalar_one()
    await actionlog.record(db, org_id=user.org_id, actor_type="user", actor_id=user.actor_id,
                           action="review_dispute.created", target_type="review_dispute", target_id=str(did),
                           target_label=f"{row.p_name} · {row.period_start}–{row.period_end}", result="ok",
                           ip=user.ip)
    r = (await db.execute(text(_DISPUTE_SELECT + "WHERE d.id = :i"), {"i": did})).one()
    return _dispute_item(r)


class DisputeResolveIn(BaseModel):
    status: Literal["resolved", "rejected"]
    resolution: str = Field(min_length=1, max_length=2000)


@router.patch("/people/reviews/disputes/{dispute_id}")
async def resolve_dispute(dispute_id: uuid.UUID, body: DisputeResolveIn,
                          user: service.CurrentUser = Depends(require("people_review.write")),
                          _pin: service.CurrentUser = Depends(require_pin(PIN_OP)),
                          db: AsyncSession = DB) -> dict[str, Any]:
    """Giải quyết Phản biện — chỉ ghi `status`/`resolution`. KHÔNG tự đổi điểm hay bất kỳ trạng thái nhân sự nào
    (khoá cứng 2 "hệ thống không tự ra quyết định nhân sự"); Owner muốn sửa điểm thì gọi riêng
    `PATCH /people/reviews/{id}` — một hành động tường minh, có lý do, giữ lịch sử của chính nó."""
    sc = await scope_for(db, user, "people_review.write")
    row = (await db.execute(text("""SELECT d.id, d.status, d.review_id, r.org_id, r.person_id, r.period_start,
                                           r.period_end, p.display_name AS p_name
                                    FROM biz.review_disputes d JOIN biz.people_reviews r ON r.id = d.review_id
                                    JOIN core.persons p ON p.id = r.person_id WHERE d.id = :i"""),
                            {"i": dispute_id})).one_or_none()
    if row is None or row.org_id != user.org_id:
        raise not_found("Phản biện")
    if not await _in_scope(db, sc, row.person_id):
        raise not_found("Phản biện")
    if row.status != "open":
        raise ApiError(409, "DISPUTE_DECIDED", "Phản biện này đã được xử lý")
    await db.execute(text("""UPDATE biz.review_disputes SET status = :s, resolution = :r, resolved_by = :by,
                             resolved_at = now() WHERE id = :i"""),
                     {"s": body.status, "r": body.resolution, "by": user.id, "i": dispute_id})
    await actionlog.record(db, org_id=user.org_id, actor_type="user", actor_id=user.actor_id,
                           action="review_dispute.resolved", target_type="review_dispute", target_id=str(dispute_id),
                           target_label=f"{row.p_name} · {row.period_start}–{row.period_end}", result="ok",
                           detail={"status": body.status, "resolution": body.resolution}, ip=user.ip)
    r = (await db.execute(text(_DISPUTE_SELECT + "WHERE d.id = :i"), {"i": dispute_id})).one()
    return _dispute_item(r)


async def _explain_review(db: AsyncSession, user: service.CurrentUser, sc: Scope, id: str) -> dict[str, Any]:
    try:
        rid = uuid.UUID(id)
    except ValueError as e:
        raise not_found("Đánh giá") from e
    r = (await db.execute(text("""SELECT r.id, r.person_id, r.score, r.evidence, r.created_at, r.overridden_by,
                                         p.display_name FROM biz.people_reviews r JOIN core.persons p
                                         ON p.id = r.person_id WHERE r.id = :i AND r.org_id = :o"""),
                          {"i": rid, "o": user.org_id})).one_or_none()
    if r is None:
        raise not_found("Đánh giá")
    if not await _in_scope(db, sc, r.person_id):
        raise not_found("Đánh giá")
    if not user.pin_active():
        raise pin_required()
    ids = explain.unit_ids_of(r.evidence)
    payload = explain.payload("review", id, f"{r.display_name} — đánh giá {r.created_at.date().isoformat()}",
                              f"{float(r.score):.0f}/100", method="manual" if r.overridden_by else "rules+model",
                              units=await explain.units_payload(db, ids, viewer=user))
    await actionlog.record(db, org_id=user.org_id, actor_type="user", actor_id=user.actor_id,
                           action="people_review.explained", target_type="people_review", target_id=str(rid),
                           result="ok", ip=user.ip)
    return payload


explain.register("review", "people_review.read", _explain_review)


# ═══ Chất lượng chăm sóc ═══════════════════════════════════════════════════════

DEFAULT_WINDOW_DAYS = 30
SCENARIO_WINDOW_DAYS = 14


def _date_range(date_from: str | None, date_to: str | None) -> tuple[datetime, datetime]:
    dt = parse_cursor(date_to) or datetime.now(UTC)
    df = parse_cursor(date_from) or (dt - timedelta(days=DEFAULT_WINDOW_DAYS))
    return df, dt


def _bucket_item(r: Any, owner: bool) -> dict[str, Any]:
    total = r.fast + r.normal + r.slow
    return {"staff": _masked_person_ref(r, "p", owner), "fast": r.fast, "normal": r.normal, "slow": r.slow,
            "total_answered": total, "fast_pct": round(100 * r.fast / total, 1) if total else None,
            "avg_minutes": round(float(r.avg_seconds) / 60, 1) if r.avg_seconds is not None else None}


def _sum_buckets(items: list[dict[str, Any]]) -> dict[str, Any]:
    fast = sum(i["fast"] for i in items)
    normal = sum(i["normal"] for i in items)
    slow = sum(i["slow"] for i in items)
    total = fast + normal + slow
    return {"fast": fast, "normal": normal, "slow": slow, "total_answered": total,
            "fast_pct": round(100 * fast / total, 1) if total else None}


@router.get("/care/response-times")
async def care_response_times(date_from: str | None = None, date_to: str | None = None,
                              person_id: uuid.UUID | None = None,
                              user: service.CurrentUser = Depends(CARE_READ), db: AsyncSession = DB) -> dict[str, Any]:
    """Lưới phản hồi theo khung giờ (<15 / 15–60 / >60 phút, PLAN §3.12), theo từng nhân viên — ghép "tin đến →
    tin đi cùng luồng" của `gh.biz.people.service.PAIR_CTE` (cùng cách `queue.jobs._scan_slow_response` đã ghép
    cho cảnh báo `slow_response`, chỉ khác là lấy hết thay vì chỉ phần chưa trả lời). `unattended` (SỐ KHÁCH có tin
    chưa ai trả lời — đếm người, không đếm tin, v0.1.71) không gắn được với một nhân viên cụ thể (không ai đã trả lời
    thì không biết quy cho ai) nên tách thành một số riêng ở ngoài lưới, không phải một cột của từng nhân viên —
    quyết định tự đưa ra."""
    sc = await scope_for(db, user, "care.read")
    df, dt = _date_range(date_from, date_to)
    params: dict[str, Any] = {"o": user.org_id, "df": df, "dt": dt}
    scope_cond, scope_params = ("TRUE", {}) if sc.is_all else sc.person_id_sql("customer_id")
    params.update(scope_params)

    conds = ["staff_id IS NOT NULL", scope_cond]
    if person_id:
        conds.append("staff_id = :pid")
        params["pid"] = person_id
    where_sql = " AND ".join(conds)
    rows = (await db.execute(text(psvc.PAIR_CTE + f"""
        SELECT staff_id AS p_id, stf.code AS p_code, stf.display_name AS p_name, stf.person_type AS p_type,
               stf.organization_name AS p_org, {psvc.BUCKET_SELECT}
        FROM paired LEFT JOIN core.persons stf ON stf.id = staff_id
        WHERE {where_sql}
        GROUP BY staff_id, stf.code, stf.display_name, stf.person_type, stf.organization_name
        ORDER BY count(*) DESC"""), params)).all()  # noqa: S608

    unattended = (await db.execute(text(psvc.PAIR_CTE + f"""
        SELECT count(DISTINCT COALESCE(customer_id::text, id::text)) FROM paired
        WHERE replied_at IS NULL AND {scope_cond}"""),  # noqa: S608
        {"o": user.org_id, "df": df, "dt": dt, **scope_params})).scalar_one()

    owner = user.role_code == rbac.OWNER
    items = [_bucket_item(r, owner) for r in rows]
    return {"from": iso(df), "to": iso(dt), "items": items, "totals": _sum_buckets(items), "unattended": unattended}


@router.get("/care/repeated-issues")
async def repeated_issues(date_from: str | None = None, date_to: str | None = None,
                          issue_type: Literal["broken_promise", "abandoned_customer"] | None = None,
                          limit: int = Query(50, ge=1, le=200), user: service.CurrentUser = Depends(CARE_READ),
                          db: AsyncSession = DB) -> dict[str, Any]:
    """Lỗi chăm sóc lặp lại (PLAN §3.12): "hứa rồi quên" (`biz.promises.broken`, do cụm Hàng đợi đánh dấu khi
    lời hứa quá hạn) và "khách bị bỏ rơi" (tin đến không có tin đi cùng luồng trong `ABANDON_HOURS` giờ). Không
    phải danh sách phân trang con trỏ thật (bảng tổng hợp theo chủ thể, số dòng bị chặn bởi `limit` — cùng cách
    `opportunities/pipeline` không theo khuôn `items/next_cursor/total` đầy đủ; ở đây vẫn giữ hình dạng chung
    cho quen tay nhưng `next_cursor` luôn `null`) — quyết định tự đưa ra vì PLAN không tả rõ."""
    sc = await scope_for(db, user, "care.read")
    df, dt = _date_range(date_from, date_to)
    owner = user.role_code == rbac.OWNER
    items: list[dict[str, Any]] = []

    if issue_type in (None, "broken_promise"):
        conds = ["pr.org_id = :o", "pr.broken = true", "pr.due_at >= :df", "pr.due_at <= :dt"]
        params: dict[str, Any] = {"o": user.org_id, "df": df, "dt": dt}
        if not sc.is_all:
            pw, pp = sc.person_id_sql("pr.promiser_person_id")
            tw, tp = sc.person_id_sql("pr.to_person_id")
            conds.append(f"({pw} OR (pr.to_person_id IS NOT NULL AND {tw}))")
            params.update(pp)
            params.update(tp)
        where_sql = " AND ".join(conds)
        rows = (await db.execute(text(f"""
            SELECT pr.promiser_person_id AS p_id, p.code AS p_code, p.display_name AS p_name,
                   p.person_type AS p_type, p.organization_name AS p_org, count(*) AS n, max(pr.due_at) AS last_at
            FROM biz.promises pr JOIN core.persons p ON p.id = pr.promiser_person_id
            WHERE {where_sql}
            GROUP BY pr.promiser_person_id, p.code, p.display_name, p.person_type, p.organization_name
            ORDER BY n DESC LIMIT :lim"""), {**params, "lim": limit})).all()  # noqa: S608
        items.extend({"kind": "broken_promise", "subject": _masked_person_ref(r, "p", owner), "count": r.n,
                     "repeated": r.n >= psvc.REPEAT_THRESHOLD, "last_at": iso(r.last_at)} for r in rows)

    if issue_type in (None, "abandoned_customer"):
        # Chỉ tính "bỏ rơi" khi CHƯA từng trả lời và đã trôi qua đủ lâu **tính tới hiện tại** (không phải tới
        # `dt` — một tin gần đây trong `date_to` quá khứ, thật ra sau đó có thể đã được trả lời ngoài cửa sổ
        # đang xem; dùng `now()` để "bỏ rơi" phản ánh đúng thực trạng lúc gọi API, không lệ thuộc bộ lọc ngày).
        conds2 = ["customer_id IS NOT NULL", "replied_at IS NULL",
                  f"now() - asked_at > interval '{psvc.ABANDON_HOURS} hours'"]
        params2: dict[str, Any] = {"o": user.org_id, "df": df, "dt": dt}
        if not sc.is_all:
            cw, cp = sc.person_id_sql("customer_id")
            conds2.append(cw)
            params2.update(cp)
        where2 = " AND ".join(conds2)
        rows2 = (await db.execute(text(psvc.PAIR_CTE + f"""
            SELECT customer_id AS p_id, p.code AS p_code, p.display_name AS p_name, p.person_type AS p_type,
                   p.organization_name AS p_org, count(*) AS n, max(asked_at) AS last_at
            FROM paired JOIN core.persons p ON p.id = customer_id
            WHERE {where2} GROUP BY customer_id, p.code, p.display_name, p.person_type, p.organization_name
            ORDER BY n DESC LIMIT :lim"""), {**params2, "lim": limit})).all()  # noqa: S608
        items.extend({"kind": "abandoned_customer", "subject": _masked_person_ref(r, "p", owner), "count": r.n,
                     "repeated": r.n >= psvc.REPEAT_THRESHOLD, "last_at": iso(r.last_at)} for r in rows2)

    items.sort(key=lambda i: -i["count"])
    items = items[:limit]
    return {"items": items, "next_cursor": None, "total": len(items)}


async def _response_stats_for(db: AsyncSession, org_id: uuid.UUID, person_id: uuid.UUID, df: datetime,
                              dt: datetime) -> dict[str, Any]:
    row = (await db.execute(text(psvc.PAIR_CTE + f"SELECT {psvc.BUCKET_SELECT} FROM paired WHERE "
                                 "customer_id = :cid"), {"o": org_id, "df": df, "dt": dt, "cid": person_id})  # noqa: S608
          ).one()
    total = row.fast + row.normal + row.slow
    return {"fast": row.fast, "normal": row.normal, "slow": row.slow, "unanswered": row.unanswered,
            "fast_pct": round(100 * row.fast / total, 1) if total else None,
            "avg_minutes": round(float(row.avg_seconds) / 60, 1) if row.avg_seconds is not None else None}


async def _broken_count_for(db: AsyncSession, org_id: uuid.UUID, person_id: uuid.UUID, df: datetime,
                            dt: datetime) -> int:
    return (await db.execute(text("""SELECT count(*) FROM biz.promises WHERE org_id = :o AND to_person_id = :p
                                     AND broken = true AND due_at >= :df AND due_at <= :dt"""),
                             {"o": org_id, "p": person_id, "df": df, "dt": dt})).scalar_one()


def _scenario_note(status: str, stats: dict[str, Any] | None, broken: int) -> str:
    if stats is None:
        return "Không có dữ liệu hội thoại để đối chiếu"
    fast_pct = stats.get("fast_pct")
    parts = [f"phản hồi nhanh {fast_pct:g}%" if fast_pct is not None and fast_pct >= 60 else
             (f"phản hồi chậm ({fast_pct:g}% nhanh)" if fast_pct is not None else "không có lượt phản hồi nào")]
    if qsvc.PROMISES_TRACKED:     # chưa theo dõi lời hứa thì không nói «không có lời hứa bị vỡ» (v0.1.71)
        parts.append("không có lời hứa bị vỡ" if broken == 0 else f"{broken} lời hứa bị vỡ")
    kind = "Kịch bản thắng" if status == "won" else "Kịch bản mất khách"
    return f"{kind}: {', '.join(parts)}."


def _scenario_summary(items: list[dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for st in ("won", "lost"):
        rows = [i for i in items if i["deal"]["status"] == st]
        fast_pcts = [i["response"]["fast_pct"] for i in rows
                    if i["response"] and i["response"]["fast_pct"] is not None]
        broken = [i["broken_promises"] for i in rows]
        out[st] = {"count": len(rows),
                  "avg_fast_pct": round(sum(fast_pcts) / len(fast_pcts), 1) if fast_pcts else None,
                  "avg_broken_promises": round(sum(broken) / len(broken), 2) if broken else None}
    return out


@router.get("/care/scenarios")
async def care_scenarios(status: Literal["won", "lost"] | None = None, cursor: str | None = None,
                         limit: int = Query(20, ge=1, le=100), user: service.CurrentUser = Depends(CARE_READ),
                         db: AsyncSession = DB) -> dict[str, Any]:
    """Kịch bản thắng/mất khách (PLAN §3.12): mỗi deal thắng/thua (`biz.deals.status`) kèm cách chăm sóc khách
    đó trong `SCENARIO_WINDOW_DAYS` ngày trước khi chốt — lưới phản hồi + số lời hứa bị vỡ. `summary` so sánh
    won/lost tính trên **trang hiện tại** (không quét lại toàn bộ tổ chức mỗi lượt gọi) — đủ cho một bảng so
    sánh nhanh, quyết định tự đưa ra để tránh N+1 hai lần trên toàn bộ deal."""
    sc = await scope_for(db, user, "care.read")
    conds = ["d.org_id = :o", "d.status IN ('won', 'lost')"]
    params: dict[str, Any] = {"o": user.org_id}
    if status:
        conds.append("d.status = :status")
        params["status"] = status
    if not sc.is_all:
        pw, pp = sc.person_id_sql("d.person_id")
        conds.append(pw)
        params.update(pp)
    if cursor:
        conds.append("COALESCE(d.won_at, d.updated_at) < :c")
        params["c"] = parse_cursor(cursor)
    where_sql = " AND ".join(conds)
    total = (await db.execute(text(f"SELECT count(*) FROM biz.deals d WHERE {where_sql}"), params)  # noqa: S608
            ).scalar_one()
    rows = (await db.execute(text(f"""
        SELECT d.id, d.code, d.amount_vnd, d.status, d.won_at, d.updated_at, d.opportunity_id, d.person_id,
               p.id AS p_id, p.code AS p_code, p.display_name AS p_name, p.person_type AS p_type,
               p.organization_name AS p_org
        FROM biz.deals d LEFT JOIN core.persons p ON p.id = d.person_id
        WHERE {where_sql} ORDER BY COALESCE(d.won_at, d.updated_at) DESC LIMIT :n"""),  # noqa: S608
        {**params, "n": limit + 1})).all()
    owner = user.role_code == rbac.OWNER
    items = []
    for r in rows[:limit]:
        close_at = r.won_at or r.updated_at
        stats = broken = None
        if r.person_id is not None:
            window_df = close_at - timedelta(days=SCENARIO_WINDOW_DAYS)
            stats = await _response_stats_for(db, user.org_id, r.person_id, window_df, close_at)
            broken = await _broken_count_for(db, user.org_id, r.person_id, window_df, close_at)
        items.append({"deal": {"id": str(r.id), "code": r.code, "amount_vnd": int(r.amount_vnd),
                               "status": r.status, "won_at": iso(r.won_at),
                               "opportunity_id": str(r.opportunity_id) if r.opportunity_id else None},
                     "person": _masked_person_ref(r, "p", owner), "response": stats,
                     "broken_promises": broken or 0, "note": _scenario_note(r.status, stats, broken or 0)})
    next_cursor = None
    if len(rows) > limit:
        last = rows[limit - 1]
        next_cursor = iso(last.won_at or last.updated_at)
    return {"items": items, "next_cursor": next_cursor, "total": total, "summary": _scenario_summary(items)}


# ═══ Thử trò chuyện với agent (bước 8 trình thiết lập) ═════════════════════════
# Đặt ở đây (không phải `gh/setup/routes.py`) vì gọi model — `gh.setup.routes` chỉ điều phối trạng thái bước,
# không tự gọi `ModelRouter`; `gh.setup.routes.step8` import `try_chat` từ đây.

async def try_chat(request_app_state: Any, org_id: uuid.UUID, agent_id: uuid.UUID, *, name: str, role_desc: str,
                   voice: str, message: str) -> str:
    router_ = request_app_state.model_router
    system = (f"Bạn là \"{name}\", một trợ lý AI của doanh nghiệp. Vai trò: {role_desc}. Giọng điệu: {voice}. "
              "Đây là một lượt thử trong trình thiết lập, không phải hội thoại thật với khách — chỉ trả lời "
              "ngắn gọn, tự giới thiệu đúng vai trò trên.")
    try:
        routed = await router_.generate(org_id, agent_key=f"agent:{agent_id}", purpose="setup_agent_try",
                                        json_mode=False, temperature=0.4,
                                        messages=[Message("system", system), Message("user", message)])
    except ModelUnavailable as e:
        raise model_unavailable("Chưa có model nào chạy được để thử trò chuyện", e.reasons,
                                no_chain=e.no_chain) from e
    return routed.text.strip()
