"""API nền chung giai đoạn 3 (docs/api/phase-3.md): chứng cứ, góc nhìn đã lưu, Bàn làm việc, quyết định của agent."""

import uuid
from datetime import UTC, datetime
from typing import Any, Literal

import orjson
from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gh.auth import rbac, service
from gh.auth.deps import current_user, require, require_pin
from gh.biz.core import drafts, explain
from gh.biz.core.scope import Scope, ensure_group, ensure_person, group_view_sql, not_found, scope_for
from gh.biz.queue import service as qsvc
from gh.chassis import actionlog
from gh.data.common import has_masked_number, iso, mask_text, parse_cursor
from gh.db import DB
from gh.errors import ApiError, forbidden, model_unavailable
from gh.providers.clients import Message
from gh.providers.router import ModelUnavailable
from gh.shell import navigation

router = APIRouter(tags=["core"])


# ─── chứng cứ ─────────────────────────────────────────────────────────────────

@router.get("/explain/raw/{raw_id}")
async def explain_raw(raw_id: uuid.UUID, user: service.CurrentUser = Depends(current_user),
                      db: AsyncSession = DB) -> dict[str, Any]:
    return await explain.raw_evidence(db, user, raw_id)


@router.get("/explain/{kind}/{id}")
async def explain_one(kind: str, id: str, user: service.CurrentUser = Depends(current_user),
                      db: AsyncSession = DB) -> dict[str, Any]:
    return await explain.explain(db, user, kind, id)


# ─── góc nhìn đã lưu ──────────────────────────────────────────────────────────

def _view(r: Any) -> dict[str, Any]:
    return {"id": str(r.id), "screen": r.screen, "name": r.name, "filters": r.filters, "created_at": iso(r.created_at)}


@router.get("/views")
async def list_views(screen: str | None = None, user: service.CurrentUser = Depends(current_user),
                     db: AsyncSession = DB) -> list[dict[str, Any]]:
    rows = (await db.execute(text("""SELECT id, screen, name, filters, created_at FROM ops.saved_views
                                     WHERE user_id = :u AND (CAST(:s AS text) IS NULL OR screen = :s)
                                     ORDER BY screen, created_at"""), {"u": user.id, "s": screen})).all()
    return [_view(r) for r in rows]


class ViewIn(BaseModel):
    screen: str = Field(min_length=1, max_length=40)
    name: str = Field(min_length=1, max_length=80)
    filters: dict[str, Any] = Field(default_factory=dict)


@router.post("/views", status_code=201)
async def create_view(body: ViewIn, user: service.CurrentUser = Depends(current_user),
                      db: AsyncSession = DB) -> dict[str, Any]:
    if body.screen not in navigation.all_screen_keys():
        raise ApiError(422, "VALIDATION", "Màn hình không hợp lệ", errors={"screen": "Màn hình không tồn tại"})
    exists = (await db.execute(text("""SELECT 1 FROM ops.saved_views WHERE user_id = :u AND screen = :s
                                       AND lower(name) = lower(:n)"""),
                               {"u": user.id, "s": body.screen, "n": body.name.strip()})).first()
    if exists:
        raise ApiError(409, "VIEW_EXISTS", "Đã có góc nhìn cùng tên trên màn này")
    r = (await db.execute(text("""
        INSERT INTO ops.saved_views (org_id, user_id, screen, name, filters)
        VALUES (:o, :u, :s, :n, CAST(:f AS jsonb)) RETURNING id, screen, name, filters, created_at"""),
        {"o": user.org_id, "u": user.id, "s": body.screen, "n": body.name.strip(),
         "f": orjson.dumps(body.filters).decode()})).one()
    return _view(r)


@router.delete("/views/{view_id}", status_code=204)
async def delete_view(view_id: uuid.UUID, user: service.CurrentUser = Depends(current_user),
                      db: AsyncSession = DB) -> Response:
    n = (await db.execute(text("DELETE FROM ops.saved_views WHERE id = :i AND user_id = :u RETURNING id"),
                          {"i": view_id, "u": user.id})).first()
    if n is None:
        raise not_found("Góc nhìn")
    return Response(status_code=204)


# ─── Bàn làm việc ─────────────────────────────────────────────────────────────

async def _drafts_scope(db: AsyncSession, user: service.CurrentUser) -> Scope:
    """Phạm vi rộng nhất giữa quyền soạn và quyền duyệt."""
    a = user.permissions.get("action.approve", rbac.NONE)
    d = user.permissions.get("action.draft", rbac.NONE)
    return await scope_for(db, user, "action.approve" if rbac.at_least(a, d) else "action.draft")


def _scope_where(sc: Scope) -> tuple[str, dict[str, Any]]:
    # «Quyền xem» của nhóm (v0.1.71): nháp gắn với nhóm «Chỉ Sếp» (đối tượng hoặc nhóm đích) ẩn với vai khác.
    gv = group_view_sql(sc.role, "d.group_id")
    gate = "TRUE" if gv == "TRUE" else f"(d.group_id IS NULL OR {gv})"
    if sc.is_all:
        sw, sp = sc.subject_sql("d.subject_type", "d.subject_id")
        return f"({sw} AND {gate})", sp
    sw, sp = sc.subject_sql("d.subject_type", "d.subject_id")
    uw, up = sc.user_sql("d.created_by")
    return f"(({sw} OR {uw}) AND {gate})", {**sp, **up}


async def _visible(db: AsyncSession, user: service.CurrentUser, draft_id: uuid.UUID) -> Any:
    sc = await _drafts_scope(db, user)
    where, params = _scope_where(sc)
    r = (await db.execute(text(drafts._SELECT + f" WHERE d.id = :i AND d.org_id = :o AND {where}"),  # noqa: S608
                          {"i": draft_id, "o": user.org_id, **params})).one_or_none()
    if r is None:
        raise not_found("Bản nháp")
    return r


@router.get("/drafts")
async def list_drafts(status: Literal["pending", "decided", "failed", "all"] = "pending", kind: str | None = None,
                      hide_silenced: bool = Query(False, description="Bỏ nháp đã Tắt tiếng (Mặt tiền Owner)"),
                      cursor: str | None = None, limit: int = Query(50, ge=1, le=200),
                      user: service.CurrentUser = Depends(current_user), db: AsyncSession = DB) -> dict[str, Any]:
    sc = await _drafts_scope(db, user)
    where, params = _scope_where(sc)
    conds = [f"d.org_id = :o AND {where}"]
    if status == "pending":
        conds.append("d.status = 'pending'")
    elif status == "decided":
        conds.append("d.status <> 'pending'")
    elif status == "failed":
        # Thẻ "Gửi lỗi — cần gửi lại": lọc ở máy chủ (không quét trang), bỏ bản đã có bản gửi lại còn hiệu lực.
        conds.append(f"d.status = 'failed' AND NOT {drafts.RESENT_SQL}")
    if kind:
        conds.append("d.kind = :k")
    if hide_silenced:
        conds.append(qsvc.not_silenced_sql("d", item_type="draft", id_col="id"))
    if cursor:
        conds.append("d.created_at < :c")
    sql_where = " AND ".join(conds)
    base = {"o": user.org_id, "k": kind, "c": parse_cursor(cursor), **params}
    total = (await db.execute(text(f"SELECT count(*) FROM biz.action_drafts d WHERE {sql_where}"),  # noqa: S608
                              base)).scalar_one()
    rows = (await db.execute(text(drafts._SELECT + f" WHERE {sql_where} ORDER BY d.created_at DESC LIMIT :n"),  # noqa: S608
                             {**base, "n": limit + 1})).all()
    owner = user.role_code == rbac.OWNER
    items = [{**drafts.item_payload(r), "title": mask_text(r.title, owner)} for r in rows[:limit]]
    return {"items": items, "next_cursor": iso(rows[limit - 1].created_at) if len(rows) > limit else None,
            "total": total}


async def _context(db: AsyncSession, r: Any, owner: bool) -> list[dict[str, Any]]:
    """Ngữ cảnh đối tượng (wbContext): hồ sơ + điểm hiện tại + ghi chú Sếp."""
    out: list[dict[str, Any]] = []
    if r.p_id:
        label = r.p_name + (f" · {r.p_org}" if r.p_org else "")
        out.append({"key": "đối tượng", "value": label, "ref": {"type": "person", "id": str(r.p_id), "code": r.p_code}})
        scores = (await db.execute(text("""SELECT dimension, value FROM clean.current_scores
                                           WHERE subject_type = 'person' AND subject_id = :s"""),
                                   {"s": r.p_id})).all()
        for s in scores:
            if s.dimension in ("heat", "churn_risk", "potential"):
                out.append({"key": explain.DIMENSION_LABELS[s.dimension], "value": f"{float(s.value):.0f}",
                            "ref": {"type": "score", "id": f"person:{r.p_id}:{s.dimension}"}})
        opp = (await db.execute(text("""SELECT code, stage FROM biz.opportunities WHERE person_id = :p
                                        AND closed_at IS NULL ORDER BY updated_at DESC LIMIT 1"""),
                                {"p": r.p_id})).one_or_none()
        if opp:
            out.append({"key": "giai đoạn", "value": f"{opp.stage} · {opp.code}", "ref": None})
        attrs = (await db.execute(text("SELECT attrs FROM core.persons WHERE id = :p"), {"p": r.p_id})).scalar_one()
        if attrs and attrs.get("style"):
            out.append({"key": "phong cách", "value": attrs["style"], "ref": None})
        if attrs and attrs.get("owner_note"):
            out.append({"key": "ghi chú Sếp", "value": mask_text(attrs["owner_note"], owner), "ref": None})
    elif r.g_id:
        out.append({"key": "nhóm", "value": r.g_name, "ref": {"type": "group", "id": str(r.g_id), "code": r.g_code}})
    return out


@router.get("/drafts/{draft_id}")
async def get_draft(draft_id: uuid.UUID, user: service.CurrentUser = Depends(current_user),
                    db: AsyncSession = DB) -> dict[str, Any]:
    r = await _visible(db, user, draft_id)
    owner = user.role_code == rbac.OWNER
    body = r.body or {}
    target = body.get("target")
    tgt = None
    if target:
        g = p = None
        if target.get("group_id"):
            g = (await db.execute(text("""SELECT g.id, g.code, g.name, c.type AS channel FROM core.groups g
                                          JOIN core.channels c ON c.id = g.channel_id WHERE g.id = :i"""),
                                  {"i": target["group_id"]})).one_or_none()
        if target.get("person_id"):
            p = (await db.execute(text("""SELECT id, code, display_name AS name, person_type AS type,
                                                 organization_name AS org_name FROM core.persons WHERE id = :i"""),
                                  {"i": target["person_id"]})).one_or_none()
        acc = None
        if target.get("thread_type") == "user":
            acc = await drafts.user_account(db, user.org_id, target)
        tgt = {"channel": target["channel"], "thread_type": target["thread_type"],
               "group": {"id": str(g.id), "code": g.code, "name": g.name, "channel": g.channel} if g else None,
               "person": {"id": str(p.id), "code": p.code, "name": p.name, "type": p.type, "org_name": p.org_name}
               if p else None,
               "identity_id": target.get("identity_id"),
               # Tài khoản sẽ nhận tin 1-1 (Sếp thấy trước khi duyệt): tên hiển thị, không có thì mã tài khoản đã che.
               "account": {"label": mask_text(acc.handle or acc.external_id, owner) or ""} if acc is not None else None}
    text_ = mask_text(body.get("text", ""), owner) or ""
    flags = r.flags or {}
    channel = (target or {}).get("channel")
    approve_label = (f"Duyệt và gửi qua {drafts.CHANNEL_LABELS.get(channel, channel)}"
                     if r.kind in drafts.SENDABLE and channel else "Duyệt và thực hiện")
    return {**drafts.item_payload(r), "title": mask_text(r.title, owner), "paragraphs": drafts._paragraphs(text_),
            "text": text_, "lang": body.get("lang", "vi"), "target": tgt, "amount_vnd": body.get("amount_vnd"),
            "autonomy_level": r.autonomy_level,
            "flags": {"writes_external": bool(flags.get("writes_external")),
                      "personnel_related": bool(flags.get("personnel_related")),
                      "over_threshold": bool(flags.get("over_threshold"))},
            "approve_label": approve_label,
            "sources": [{"label": s.get("label", ""), "ref": s.get("ref")} for s in (r.sources or [])],
            "context": await _context(db, r, owner),
            "side_actions": [{"key": a.get("key"), "label": a.get("label"), "on": bool(a.get("on"))}
                             for a in (r.side_actions or [])],
            "decision": {"by": {"id": str(r.decided_by), "name": r.decider_name}, "at": iso(r.decided_at),
                         "reason": r.decision_reason} if r.decided_by else None,
            "send_result": r.send_result,
            "versions": [{**v, "text": mask_text(v.get("text"), owner)} for v in body.get("versions") or []]}


class DraftIn(BaseModel):
    kind: Literal["message", "quotation", "contract", "reminder", "report"]
    title: str = Field(min_length=1, max_length=200)
    text: str = Field(min_length=1, max_length=20000)
    target: dict[str, Any] | None = None       # {"channel","thread_type","group_id"|"person_id"}
    amount_vnd: int | None = Field(default=None, ge=0)
    subject: dict[str, Any] | None = None      # {"type": "person|group", "id"}
    sources: list[dict[str, Any]] = Field(default_factory=list)
    #: "Gửi lại": id bản nháp GỬI LỖI mà bản này thay thế (nối thành `parent_draft_id`). Bản lỗi cũ ra khỏi thẻ
    #: "Gửi lỗi", bấm lần hai không sinh thêm một nháp trùng.
    resend_of: uuid.UUID | None = None


def _target(t: dict[str, Any] | None) -> drafts.Target | None:
    if not t:
        return None
    try:
        return drafts.Target(channel=str(t["channel"]), thread_type=str(t["thread_type"]),
                             group_id=uuid.UUID(t["group_id"]) if t.get("group_id") else None,
                             person_id=uuid.UUID(t["person_id"]) if t.get("person_id") else None,
                             identity_id=uuid.UUID(t["identity_id"]) if t.get("identity_id") else None)
    except (KeyError, ValueError) as e:
        raise ApiError(422, "VALIDATION", "Đích gửi không hợp lệ", errors={"target": "Đích gửi không hợp lệ"}) from e


@router.post("/drafts", status_code=201)
async def create(body: DraftIn, request: Request, user: service.CurrentUser = Depends(require("action.draft")),
                 db: AsyncSession = DB) -> dict[str, Any]:
    sc = await scope_for(db, user, "action.draft")
    subject = None
    if body.subject:
        try:
            subject = (str(body.subject["type"]), uuid.UUID(str(body.subject["id"])))
        except (KeyError, ValueError) as e:
            raise ApiError(422, "VALIDATION", "Đối tượng không hợp lệ") from e
    tgt = _target(body.target)
    if subject and subject[0] == "person":
        await ensure_person(db, sc, subject[1])
    if subject and subject[0] == "group":
        await ensure_group(db, sc, subject[1])
    if tgt and tgt.person_id:
        await ensure_person(db, sc, tgt.person_id)
    if tgt and tgt.group_id:
        await ensure_group(db, sc, tgt.group_id)
    if tgt and tgt.identity_id and not (
            tgt.person_id and await drafts.identity_of_person(db, user.org_id, tgt.identity_id, tgt.person_id)):
        # Không tin `identity_id` do máy khách gửi: tài khoản nhận phải thuộc đúng người đã được kiểm quyền ở trên.
        raise ApiError(422, "TARGET_IDENTITY_MISMATCH", "Tài khoản nhận không thuộc người được chọn làm đích gửi",
                       errors={"target": "Tài khoản nhận không thuộc người được chọn làm đích gửi"})
    if body.resend_of is not None:
        await _visible(db, user, body.resend_of)      # không thấy được bản lỗi này ⇒ 404 như mọi nơi khác
        # Khoá dòng bản lỗi: hai lần bấm "Gửi lại" cùng lúc chỉ một lần thành công.
        old =(await db.execute(text("""SELECT d.id, d.kind, d.status, d.code FROM biz.action_drafts d
                                        WHERE d.id = :i AND d.org_id = :o FOR UPDATE"""),
                                {"i": body.resend_of, "o": user.org_id})).one_or_none()
        if old is None:
            raise not_found("Bản nháp")
        if old.status != drafts.FAILED or old.kind != body.kind:
            raise ApiError(409, "NOT_RESENDABLE", "Chỉ gửi lại được bản nháp gửi lỗi, cùng loại với bản mới")
        done = (await db.execute(text("""SELECT rc.code FROM biz.action_drafts rc WHERE rc.parent_draft_id = :i
                                         AND rc.kind = :k AND rc.status <> 'rejected' LIMIT 1"""),
                                 {"i": old.id, "k": old.kind})).scalar_one_or_none()
        if done is not None:
            raise ApiError(409, "ALREADY_RESENT", f"Bản này đã được gửi lại ({done}) — mở bản mới để duyệt")
    res = await drafts.create_draft(db, org_id=user.org_id, kind=body.kind, title=body.title, body_text=body.text,
                                    target=tgt, created_by=user.id, subject=subject, sources=body.sources,
                                    amount_vnd=body.amount_vnd, parent_draft_id=body.resend_of,
                                    redis=request.app.state.redis)
    return await get_draft(res["id"], user, db)


class DecideIn(BaseModel):
    side_actions: dict[str, bool] = Field(default_factory=dict)
    text: str | None = Field(default=None, max_length=20000)
    reason: str | None = Field(default=None, max_length=500)


async def _decide(request: Request, draft_id: uuid.UUID, user: service.CurrentUser, db: AsyncSession,
                  verdict: str, body: DecideIn) -> dict[str, Any]:
    sc = await scope_for(db, user, "action.approve")
    where, params = _scope_where(sc)
    ok = (await db.execute(text(f"SELECT 1 FROM biz.action_drafts d WHERE d.id = :i AND d.org_id = :o AND {where}"),  # noqa: S608
                           {"i": draft_id, "o": user.org_id, **params})).first()
    if ok is None:
        raise not_found("Bản nháp")
    if verdict == "edit" and user.role_code != rbac.OWNER and has_masked_number(body.text):
        # Review v0.1.67 P3: vai dưới Owner chỉ thấy số đã che (•••••678); "Khôi phục bản trước" / "Sửa rồi gửi" /
        # dùng bản dịch đưa chuỗi che đó vào ô sửa — gửi đi là gửi khách số sai và ghi đè mất số thật trong nháp.
        raise ApiError(422, "MASKED_TEXT", "Nội dung có số đã che nên chưa gửi được",
                       "Chỗ số bị che (•••) chỉ Sếp thấy đủ. Hãy xoá hoặc viết lại phần đó, hoặc nhờ Sếp duyệt.")
    try:
        await drafts.decide(db, draft_id=draft_id, org_id=user.org_id, user_id=user.id, verdict=verdict,
                            text_override=body.text, reason=body.reason, side_actions=body.side_actions,
                            bus=request.app.state.bus, redis=request.app.state.redis)
    except drafts.DraftError as e:
        raise ApiError(e.status, e.code, e.title) from e
    return await get_draft(draft_id, user, db)


@router.post("/drafts/{draft_id}/approve")
async def approve(draft_id: uuid.UUID, request: Request, body: DecideIn | None = None,
                  user: service.CurrentUser = Depends(require("action.approve")),
                  _pin: service.CurrentUser = Depends(require_pin("draft.decide")),
                  db: AsyncSession = DB) -> dict[str, Any]:
    return await _decide(request, draft_id, user, db, "approve", body or DecideIn())


@router.post("/drafts/{draft_id}/edit-send")
async def edit_send(draft_id: uuid.UUID, body: DecideIn, request: Request,
                    user: service.CurrentUser = Depends(require("action.approve")),
                    _pin: service.CurrentUser = Depends(require_pin("draft.decide")),
                    db: AsyncSession = DB) -> dict[str, Any]:
    return await _decide(request, draft_id, user, db, "edit", body)


@router.post("/drafts/{draft_id}/reject")
async def reject(draft_id: uuid.UUID, request: Request, body: DecideIn | None = None,
                 user: service.CurrentUser = Depends(require("action.approve")),
                 _pin: service.CurrentUser = Depends(require_pin("draft.decide")),
                 db: AsyncSession = DB) -> dict[str, Any]:
    return await _decide(request, draft_id, user, db, "reject", body or DecideIn())


LANGS = {"vi": "tiếng Việt", "en": "tiếng Anh", "zh": "tiếng Trung", "ja": "tiếng Nhật", "ko": "tiếng Hàn"}


class TranslateIn(BaseModel):
    lang: Literal["vi", "en", "zh", "ja", "ko"]


def _owner_reasons(user: service.CurrentUser, reasons: list[str]) -> list[str]:
    """Lý do kỹ thuật từng nhà cung cấp (tên nguồn, nhãn khoá, lỗi gốc) chỉ cho Owner; vai trò khác thấy câu chung."""
    return reasons if user.role_code == rbac.OWNER else []


def _agent_key(r: Any) -> str:
    # F-25: nháp không gắn agent dùng model gán cho 'Soạn lại / dịch nháp' (core.reply).
    return f"agent:{r.agent_id}" if r.agent_id else "core.reply"


@router.post("/drafts/{draft_id}/translate")
async def translate(draft_id: uuid.UUID, body: TranslateIn, request: Request,
                    user: service.CurrentUser = Depends(require("action.draft")),
                    db: AsyncSession = DB) -> dict[str, Any]:
    r = await _visible(db, user, draft_id)
    src = (r.body or {}).get("text", "")
    try:
        routed = await request.app.state.model_router.generate(
            user.org_id, agent_key=_agent_key(r), purpose="draft_translate", json_mode=False, temperature=0.1,
            messages=[Message("system", f"Dịch nguyên văn sang {LANGS[body.lang]}. Giữ số liệu, tên riêng, "
                                        "định dạng đoạn. Chỉ trả bản dịch, không thêm lời."),
                      Message("user", src)])
    except ModelUnavailable as e:
        raise model_unavailable("Chưa có model nào chạy được để dịch", _owner_reasons(user, e.reasons),
                                no_chain=e.no_chain) from e
    owner = user.role_code == rbac.OWNER
    return {"lang": body.lang, "text": mask_text(routed.text.strip(), owner)}


class RegenerateIn(BaseModel):
    instruction: str | None = Field(default=None, max_length=1000)


@router.post("/drafts/{draft_id}/regenerate")
async def regenerate(draft_id: uuid.UUID, request: Request, body: RegenerateIn | None = None,
                     user: service.CurrentUser = Depends(require("action.draft")),
                     db: AsyncSession = DB) -> dict[str, Any]:
    r = await _visible(db, user, draft_id)
    if r.status != drafts.PENDING:
        raise ApiError(409, "DRAFT_DECIDED", "Bản nháp này đã được quyết định")
    cur = r.body or {}
    ask = (body.instruction if body else None) or "Viết lại gọn hơn, giữ nguyên mọi số liệu và cam kết."
    ctx = "\n".join(f"- {s.get('label')}" for s in (r.sources or []))
    system = ("Bạn soạn lại bản nháp tin nhắn công việc. Chỉ dùng dữ kiện có trong bản "
              "nháp và danh sách nguồn; không bịa số liệu. Chỉ trả nội dung mới.")
    if r.agent_id:
        # v0.1.67 (F081): nháp của agent phải soạn lại ĐÚNG xưng hô, giọng và điều cấm của agent đó (cùng khối như lúc
        # agent soạn — `engine.identity_block`), không chỉ chọn model theo agent.
        from gh.biz.duty import engine

        agent = (await db.execute(text("""SELECT name, role_desc, addressing, voice, speak_when, forbidden
                                          FROM agent.identities WHERE id = :a AND org_id = :o"""),
                                  {"a": r.agent_id, "o": user.org_id})).one_or_none()
        if agent is not None:
            system += ("\n\nBản nháp này do agent sau soạn. Giữ đúng xưng hô, giọng điệu; TUYỆT ĐỐI không vi phạm "
                       f"điều cấm:\n{engine.identity_block(agent)}")
    prompt = (f"Bản nháp:\n{cur.get('text', '')}\n\nNguồn đã dùng:\n{ctx}\n\nYêu cầu: {ask}")
    try:
        routed = await request.app.state.model_router.generate(
            user.org_id, agent_key=_agent_key(r), purpose="draft_regenerate", json_mode=False, temperature=0.3,
            messages=[Message("system", system), Message("user", prompt)])
    except ModelUnavailable as e:
        raise model_unavailable("Chưa có model nào chạy được để soạn lại", _owner_reasons(user, e.reasons),
                                no_chain=e.no_chain) from e
    new_text = routed.text.strip()
    if not new_text:
        raise ApiError(502, "MODEL_BAD_OUTPUT", "Model trả về bản trống — bản nháp giữ nguyên, thử lại hoặc sửa tay",
                       "Phản hồi của model rỗng.")
    from gh.biz.duty.context import mentioned_ids

    invented = sorted(mentioned_ids(new_text) - mentioned_ids(prompt))
    if invented:
        raise ApiError(502, "MODEL_BAD_OUTPUT",
                       "Bản soạn lại nhắc tới mã không có trong nguồn — đã bỏ, bản nháp giữ nguyên",
                       f"Mã không có trong bản nháp/nguồn: {', '.join(invented[:5])}. Thử lại hoặc sửa tay.")
    versions = [*list(cur.get("versions") or []),
                {"at": iso(datetime.now(UTC)),
                 "by": "agent" if r.agent_id else "user", "text": cur.get("text", "")}]
    new_body = {**cur, "text": new_text, "versions": versions}
    await db.execute(text("UPDATE biz.action_drafts SET body = CAST(:b AS jsonb), updated_at = now() WHERE id = :i"),
                     {"b": orjson.dumps(new_body).decode(), "i": draft_id})
    await actionlog.record(db, org_id=user.org_id, actor_type="user", actor_id=user.actor_id,
                           action="draft.regenerated", target_type="draft", target_id=str(draft_id),
                           target_label=r.code, autonomy_level=r.autonomy_level, detail={"instruction": ask})
    return await get_draft(draft_id, user, db)


# ─── quyết định của agent ─────────────────────────────────────────────────────

def _proposal_text(proposal: Any, owner: bool) -> str | None:
    t = proposal.get("text") if isinstance(proposal, dict) else None
    return mask_text(t.strip()[:2000], owner) if isinstance(t, str) and t.strip() else None


async def _readable_draft_ids(db: AsyncSession, user: service.CurrentUser, ids: list[uuid.UUID]) -> set[uuid.UUID]:
    """Trong `ids`, những bản nháp người xem được đọc: `system.read` mức ALL (Owner, Auditor) đọc hết; còn lại theo
    phạm vi soạn/duyệt (`_drafts_scope`); không có quyền nháp nào ⇒ không đọc được bản nào."""
    if not ids:
        return set()
    if user.permissions.get("system.read", rbac.NONE) == rbac.ALL:
        return set(ids)
    try:
        sc = await _drafts_scope(db, user)
    except ApiError:
        return set()
    where, params = _scope_where(sc)
    rows = (await db.execute(text(f"SELECT d.id FROM biz.action_drafts d WHERE d.org_id = :o AND d.id = ANY(:ids) "  # noqa: S608
                                  f"AND {where}"), {"o": user.org_id, "ids": ids, **params})).all()
    return {r.id for r in rows}


@router.get("/agents/decisions")
async def agent_decisions(agent_id: uuid.UUID | None = None, decision: str | None = None, cursor: str | None = None,
                          limit: int = Query(50, ge=1, le=200), user: service.CurrentUser = Depends(current_user),
                          db: AsyncSession = DB) -> dict[str, Any]:
    if user.permissions.get("system.read", rbac.NONE) == rbac.NONE and \
            user.permissions.get("action.approve", rbac.NONE) == rbac.NONE:
        raise forbidden("system.read")
    conds = ["d.org_id = :o"]
    gv = group_view_sql(user.role_code, "tu.group_id")
    if gv != "TRUE":       # «Quyền xem» của nhóm (v0.1.71): quyết định về tin nhóm «Chỉ Sếp» ẩn với vai khác
        conds.append("NOT EXISTS (SELECT 1 FROM clean.meaning_units tu WHERE tu.id = d.trigger_unit_id "
                     f"AND tu.group_id IS NOT NULL AND NOT {gv})")
    if agent_id:
        conds.append("d.agent_id = :a")
    if decision:
        conds.append("d.decision = :k")
    if cursor:
        conds.append("d.at < :c")
    where = " AND ".join(conds)
    params = {"o": user.org_id, "a": agent_id, "k": decision, "c": parse_cursor(cursor)}
    total = (await db.execute(text(f"SELECT count(*) FROM agent.decisions d WHERE {where}"), params)).scalar_one()  # noqa: S608
    rows = (await db.execute(text(f"""
        SELECT d.*, a.name AS agent_name, ad.code AS draft_code, ad.status AS draft_status FROM agent.decisions d
        JOIN agent.identities a ON a.id = d.agent_id LEFT JOIN biz.action_drafts ad ON ad.id = d.draft_id
        WHERE {where} ORDER BY d.at DESC LIMIT :n"""), {**params, "n": limit + 1})).all()  # noqa: S608
    owner = user.role_code == rbac.OWNER
    readable = await _readable_draft_ids(db, user, [r.draft_id for r in rows[:limit] if r.draft_id])
    items = [{"id": str(r.id), "at": iso(r.at), "agent": {"id": str(r.agent_id), "name": r.agent_name},
              "decision": r.decision, "rationale": mask_text(r.rationale, owner), "trigger": r.trigger_ref,
              "context_refs": r.context_refs or [],
              # v0.1.67 (F076): `outcome` (held = nháp chờ duyệt, suggested, noted, blocked, rejected, none) + trạng
              # thái nháp thật — web chỉ ghi "Đã gửi" khi nháp `sent`. (F075): nội dung gợi ý/nháp agent đã soạn
              # (che số nhạy cảm).
              "outcome": r.outcome,
              # Review v0.1.67 P2-1: toàn văn nháp gắn với một bản nháp chỉ hiện khi bản nháp đó nằm trong phạm vi
              # của người xem (cùng `_scope_where` như /drafts) — Quản lý (TEAM) không đọc nháp cả tổ chức.
              "proposal_text": (_proposal_text(r.proposal, owner)
                                if r.draft_id is None or r.draft_id in readable else None),
              "draft": {"id": str(r.draft_id), "code": r.draft_code, "status": r.draft_status}
              if r.draft_id else None}
             for r in rows[:limit]]
    return {"items": items, "next_cursor": iso(rows[limit - 1].at) if len(rows) > limit else None, "total": total}



# ─── ô chọn người / trợ lý (v0.1.35, F-1) ─────────────────────────────────────
from gh.biz.core import pickers  # noqa: E402

router.include_router(pickers.router)
