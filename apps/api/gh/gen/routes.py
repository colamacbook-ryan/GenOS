# ruff: noqa: E501
"""/gen — khung chat Gen (docs/design/gen-v1.md §3.2). Chỉ người có Gen (cờ `gen.enabled` + vai trò) dùng được.

Truyền kết quả: WS sẵn có (`gen.step`/`gen.done`, lọc `to_user`) + `GET /gen/turns/{id}` dự phòng — lý do chọn WS
thay SSE ghi ở docs/reports/HANDOFF-v0.1.1.md mục v0.1.21.

v0.1.49 (QD-16): `/gen/sources/*` — đường ĐỌC Tài liệu, Deal, Vụ việc cho tool `document.*`/`deal.*`/`case.*` của Gen.
Chỉ Owner; tái dùng nguyên hàm danh sách/chi tiết của cụm Quan hệ & Thị trường (cùng phạm vi, cùng ACL) rồi che kết
quả bằng `mask_for_model` trước khi tới model đám mây (Owner nhận dữ liệu KHÔNG che từ endpoint gốc).

v0.1.50 (QD-18): `/gen/memory` — "Gen nhớ" (GET/POST/PATCH/DELETE, CHỈ Owner, không PIN; gh.gen.memory_notes) và
`confirm_proposal` / `cancel_proposal` cho ba loại đề xuất mới (`memory_note`, `kho_create`, `kho_update`). Đề xuất ghi
Phiên của job `gh.gen.kho_release` mang khoá meta `release_version`: xác nhận claim dòng `agent.hub_release_proposals`
(pending → writing → written; lỗi → pending; huỷ → cancelled) nên mỗi bản chỉ ghi vào Kho MỘT lần.

v0.1.55 (G3): `TurnIn.model_choice` {tier: auto|fast|balanced|deep, effort?: low|medium|high} — Sếp chọn model / mức
suy nghĩ ngay trong khung chat (giá trị lạ ⇒ 422 MODEL_CHOICE_INVALID); `GET /gen/settings` trả thêm `model_options`
(tầng nào dùng được cho người này). Việc hạ về Tự động + định tuyến ý định (J3) nằm ở `gh.gen.engine`.
"""

import asyncio
import uuid
from datetime import datetime
from typing import Any, Literal

import orjson
from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import BaseModel, Field, ValidationError, ValidatorFunctionWrapHandler, field_validator
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gh.auth import rbac, service
from gh.auth.deps import current_user, require, require_owner
from gh.biz.market import routes as market_routes
from gh.biz.relations import routes as relations_routes
from gh.chassis import actionlog
from gh.chassis.masking import MASK, is_secret_key, mask_for_model
from gh.db import DB, sessionmaker
from gh.errors import ApiError, conflict, field_errors, forbidden, not_found, pin_required
from gh.gen import engine, memory_notes, proposals, store

router = APIRouter(prefix="/gen", tags=["gen"])

#: Khoá phụ của lỗi endpoint nội bộ được chuyển tiếp nguyên cho web khi xác nhận đề xuất Gen thất bại.
_PASSTHROUGH_ERROR_KEYS = ("error_id", "locked_until", "attempts_left")


async def _decider_kind(db: AsyncSession, org_id: uuid.UUID) -> str:
    has = (await db.execute(text("""SELECT 1 FROM agent.providers p
                                    JOIN agent.provider_keys k ON k.provider_id = p.id AND k.is_enabled
                                    WHERE p.org_id = :o AND p.kind = 'system_one' AND p.is_enabled LIMIT 1"""),
                            {"o": org_id})).first()
    return "jev" if has else "llm"


async def gen_user(user: service.CurrentUser = Depends(current_user), db: AsyncSession = DB) -> service.CurrentUser:
    if not store.available(await store.get_settings(db, user.org_id), user):
        raise ApiError(403, "GEN_DISABLED", "Gen chưa bật cho vai trò này")
    return user


async def _settings_out(db: AsyncSession, user: service.CurrentUser) -> dict[str, Any]:
    cfg = await store.get_settings(db, user.org_id)
    # v0.1.55 (G3): tầng + mức suy nghĩ khung chat được mời chọn (hợp đồng G1 `choice_options`). Gói G: nhân viên không
    # tính nguồn Antigravity CLI (Gen của nhân viên không dùng agy) ⇒ tầng chỉ khả dụng khi có nguồn khác.
    options = await engine.model_options(db, user.org_id, owner=user.role_code == rbac.OWNER)
    return {**cfg, "available": store.available(cfg, user), "decider": await _decider_kind(db, user.org_id),
            "model_options": options}


@router.get("/settings")
async def get_settings(user: service.CurrentUser = Depends(current_user), db: AsyncSession = DB) -> dict[str, Any]:
    return await _settings_out(db, user)


class SettingsPatch(BaseModel):
    enabled: bool | None = None
    retention_days: int | None = Field(default=None, ge=store.MIN_RETENTION, le=store.MAX_RETENTION)


@router.patch("/settings")
async def patch_settings(body: SettingsPatch, user: service.CurrentUser = Depends(require_owner),
                         db: AsyncSession = DB) -> dict[str, Any]:
    cfg = await store.get_settings(db, user.org_id)
    changes = body.model_dump(exclude_none=True)
    cfg.update(changes)
    await store.save_settings(db, user.org_id, cfg)
    await actionlog.record(db, org_id=user.org_id, actor_type="user", actor_id=user.actor_id,
                           action="gen.settings_changed", target_type="settings", target_id="gen",
                           detail=changes, ip=user.ip)
    return await _settings_out(db, user)


@router.get("/conversations")
async def conversations(user: service.CurrentUser = Depends(gen_user),
                        db: AsyncSession = DB) -> list[dict[str, Any]]:
    return await store.list_conversations(db, user)


@router.get("/conversations/{cid}/messages")
async def messages(cid: uuid.UUID, before: datetime | None = None, limit: int = Query(200, ge=1, le=500),
                   user: service.CurrentUser = Depends(gen_user), db: AsyncSession = DB) -> list[dict[str, Any]]:
    if not await store.owned(db, user, cid):
        raise not_found("Hội thoại")
    return await store.list_messages(db, cid, limit, before)


@router.delete("/conversations/{cid}", status_code=204)
async def delete_conversation(cid: uuid.UUID, user: service.CurrentUser = Depends(gen_user),
                              db: AsyncSession = DB) -> Response:
    if not await store.owned(db, user, cid):
        raise not_found("Hội thoại")
    await store.delete_conversation(db, cid)
    await actionlog.record(db, org_id=user.org_id, actor_type="user", actor_id=user.actor_id,
                           action="gen.conversation_deleted", target_type="gen_conversation", target_id=str(cid),
                           ip=user.ip)
    return Response(status_code=204)


class FeedbackIn(BaseModel):
    conversation_id: uuid.UUID
    turn_id: uuid.UUID
    rating: Literal["helpful", "not_helpful"]


@router.put("/feedback")
async def put_feedback(body: FeedbackIn, user: service.CurrentUser = Depends(gen_user),
                       db: AsyncSession = DB) -> dict[str, Any]:
    """v0.1.41 (F-84): "Hữu ích / Không hữu ích" cho một câu trả lời (hoặc Bản tin Gen) trong hội thoại của chính
    mình — chấm lại thì đổi đánh giá. Số đo riêng tư ⇒ không ghi Nhật ký (như đánh dấu đã đọc thông báo)."""
    if not await store.owned(db, user, body.conversation_id):
        raise not_found("Hội thoại")
    kind = await store.set_feedback(db, user, body.conversation_id, body.turn_id, body.rating)
    if kind is None:
        raise not_found("Câu trả lời")
    actionlog.exempt()
    return {"turn_id": str(body.turn_id), "rating": body.rating, "kind": kind}


@router.delete("/feedback/{turn_id}", status_code=204)
async def delete_feedback(turn_id: uuid.UUID, user: service.CurrentUser = Depends(gen_user),
                          db: AsyncSession = DB) -> Response:
    """Bỏ chấm (chỉ đánh giá của chính mình)."""
    await store.delete_feedback(db, user, turn_id)
    actionlog.exempt()
    return Response(status_code=204)


class TurnContextIn(BaseModel):
    route: str = Field(default="/", max_length=300)
    screen_key: str | None = Field(default=None, max_length=40)
    visible_targets: list[str] = Field(default_factory=list, max_length=200)


MODEL_CHOICE_INVALID_TITLE = "Lựa chọn model không hợp lệ — em dùng chế độ Tự động nhé"


class ModelChoiceIn(BaseModel):
    """v0.1.55 (G3): lựa chọn model của khung chat. 'deep' (Kỹ hơn) ↔ tầng 'strong' của bộ định tuyến; `effort` chỉ
    có nghĩa khi tầng được chọn hỗ trợ (engine.resolve_model_choice bỏ mức không hỗ trợ)."""

    tier: Literal["auto", "fast", "balanced", "deep"] = "auto"
    effort: Literal["low", "medium", "high"] | None = None


class TurnIn(BaseModel):
    conversation_id: uuid.UUID | None = None
    text: str = Field(min_length=1, max_length=4000)
    context: TurnContextIn = Field(default_factory=TurnContextIn)
    model_choice: ModelChoiceIn | None = None

    @field_validator("model_choice", mode="wrap")
    @classmethod
    def _friendly_model_choice(cls, v: Any, handler: ValidatorFunctionWrapHandler) -> ModelChoiceIn | None:
        """Giá trị lạ ⇒ 422 MODEL_CHOICE_INVALID (problem+json qua handler ApiError) thay vì VALIDATION chung. Chỉ nêu
        TÊN trường sai — không lặp lại giá trị người gửi."""
        try:
            return handler(v)  # type: ignore[no-any-return]
        except ValidationError as e:
            bad = sorted({str(err["loc"][0]) for err in e.errors() if err.get("loc")}) or ["model_choice"]
            raise ApiError(422, "MODEL_CHOICE_INVALID", MODEL_CHOICE_INVALID_TITLE,
                           "Trường không hợp lệ: " + ", ".join(bad) + ". tier ∈ auto | fast | balanced | deep; "
                           "effort ∈ low | medium | high (hoặc bỏ trống).") from e


RATE_WINDOW_S, RATE_MAX_TURNS = 300, 20


def _tasks(app: Any) -> set[asyncio.Task[None]]:
    if not hasattr(app.state, "gen_tasks"):
        app.state.gen_tasks = set()
    tasks: set[asyncio.Task[None]] = app.state.gen_tasks
    return tasks


@router.post("/turns", status_code=202)
async def create_turn(body: TurnIn, request: Request, user: service.CurrentUser = Depends(gen_user),
                      db: AsyncSession = DB) -> dict[str, Any]:
    q = body.text.strip()
    if not q:
        raise field_errors({"text": "Nhập câu hỏi"})
    redis = request.app.state.redis
    rate_key = f"gen:rate:{user.id}"
    n = await redis.incr(rate_key)
    if n == 1:
        await redis.expire(rate_key, RATE_WINDOW_S)
    if n > RATE_MAX_TURNS:
        raise ApiError(429, "GEN_RATE_LIMITED", "Hỏi hơi nhanh — đợi vài phút rồi hỏi tiếp")
    lock_key = engine.lock_key(user.id)
    if not await redis.set(lock_key, "1", nx=True, ex=engine.LOCK_TTL_S):
        raise ApiError(409, "GEN_BUSY", "Gen đang trả lời câu trước — đợi xong rồi hỏi tiếp")
    try:
        return await _start_turn(body, q, request, user, db, lock_key)
    except BaseException:
        await redis.delete(lock_key)
        raise


async def _start_turn(body: TurnIn, q: str, request: Request, user: service.CurrentUser, db: AsyncSession,
                      lock_key: str) -> dict[str, Any]:
    if body.conversation_id is not None:
        if not await store.owned(db, user, body.conversation_id):
            raise not_found("Hội thoại")
        cid = body.conversation_id
    else:
        cid = await store.create_conversation(db, user, q[:60])
    turn_id = uuid.uuid4()
    await store.add_message(db, user.org_id, cid, "user", {"text": q}, turn_id)
    await actionlog.record(db, org_id=user.org_id, actor_type="agent", actor_id="gen", action="gen.turn",
                           target_type="gen_conversation", target_id=str(cid), autonomy_level=1,
                           detail={"on_behalf_of": str(user.id), "conversation_id": str(cid),
                                   "turn_id": str(turn_id), "text_digest": engine.digest(q),
                                   "screen": body.context.screen_key}, ip=user.ip)
    await db.commit()
    app = request.app
    token = request.cookies.get(service.SESSION_COOKIE, "")
    inp = engine.TurnInput(turn_id=turn_id, conversation_id=cid, text=q, route=body.context.route,
                           screen_key=body.context.screen_key, visible_targets=body.context.visible_targets,
                           model_choice=body.model_choice.model_dump() if body.model_choice else None)
    await app.state.redis.set(engine.turn_key(turn_id), orjson.dumps(
        {"turn_id": str(turn_id), "conversation_id": str(cid), "user_id": str(user.id), "status": "running",
         "steps": []}), ex=engine.TURN_TTL_S)
    task = asyncio.create_task(engine.run_turn(
        app=app, sm=sessionmaker(), redis=app.state.redis, router=app.state.model_router, user=user,
        session_token=token, inp=inp, decider=getattr(app.state, "gen_decider", None)), name=f"gen:{turn_id}")
    tasks = _tasks(app)
    tasks.add(task)
    task.add_done_callback(tasks.discard)
    return {"turn_id": str(turn_id), "conversation_id": str(cid)}


async def _turn_state(request: Request, user: service.CurrentUser, turn_id: uuid.UUID) -> dict[str, Any]:
    raw = await request.app.state.redis.get(engine.turn_key(turn_id))
    if raw is None:
        raise not_found("Lượt trả lời")
    state: dict[str, Any] = orjson.loads(raw)
    if state.get("user_id") != str(user.id):
        raise not_found("Lượt trả lời")
    return state


@router.get("/turns/{turn_id}")
async def get_turn(turn_id: uuid.UUID, request: Request,
                   user: service.CurrentUser = Depends(gen_user)) -> dict[str, Any]:
    state = await _turn_state(request, user, turn_id)
    return {k: state[k] for k in ("turn_id", "conversation_id", "status", "steps")}


class AckIn(BaseModel):
    step: int = Field(ge=0, le=20)
    outcome: Literal["done", "skipped", "target_missing"]


@router.post("/turns/{turn_id}/ack", status_code=204)
async def ack(turn_id: uuid.UUID, body: AckIn, request: Request, user: service.CurrentUser = Depends(gen_user),
              db: AsyncSession = DB) -> Response:
    """Web báo kết quả từng bước tour (làm xong / bỏ qua / không thấy phần tử) — số đo tỉ lệ `target_missing`."""
    state = await _turn_state(request, user, turn_id)
    await actionlog.record(db, org_id=user.org_id, actor_type="agent", actor_id="gen", action="gen.tour_step",
                           target_type="gen_turn", target_id=str(turn_id), autonomy_level=1,
                           result="failed" if body.outcome == "target_missing" else "ok",
                           detail={"on_behalf_of": str(user.id), "conversation_id": state["conversation_id"],
                                   "turn_id": str(turn_id), "step": body.step, "outcome": body.outcome}, ip=user.ip)
    return Response(status_code=204)


# ── v0.1.50 (QD-18): Gen nhớ — ghi chú sở thích Sếp (chỉ Owner, không PIN) ──

class MemoryIn(BaseModel):
    text: str = Field(min_length=1, max_length=2000)  # độ dài thật (≤ 280) kiểm SAU khi làm sạch → 422 có thông điệp
    reason: str | None = Field(default=None, max_length=2000)
    proposal_id: uuid.UUID | None = None


class MemoryPatch(BaseModel):
    text: str | None = Field(default=None, min_length=1, max_length=2000)
    reason: str | None = Field(default=None, max_length=2000)


def _memory_detail(via: str, text_: str, proposal_id: uuid.UUID | None = None) -> dict[str, Any]:
    """Action Log KHÔNG ghi nguyên văn ghi chú — chỉ độ dài + dấu vết (text_digest)."""
    detail: dict[str, Any] = {"via": via, "length": len(text_), "text_digest": engine.digest(text_)}
    if proposal_id is not None:
        detail["proposal_id"] = str(proposal_id)
    return detail


def _memory_422(field: str, e: ValueError) -> ApiError:
    return field_errors({field: str(e)})


@router.get("/memory")
async def get_memory(user: service.CurrentUser = Depends(require_owner), db: AsyncSession = DB) -> dict[str, Any]:
    return {"items": await memory_notes.list_notes(db, user.org_id), "limit": memory_notes.MAX_NOTES,
            "max_len": memory_notes.MAX_LEN, "reason_max": memory_notes.REASON_MAX}


@router.post("/memory", status_code=201)
async def create_memory(body: MemoryIn, user: service.CurrentUser = Depends(require_owner),
                        db: AsyncSession = DB) -> dict[str, Any]:
    source = "gen" if body.proposal_id is not None else "owner"
    try:
        note = await memory_notes.create(db, user, body.text, body.reason, source, body.proposal_id)
    except ValueError as e:
        raise _memory_422("text", e) from e
    await actionlog.record(db, org_id=user.org_id, actor_type="user", actor_id=user.actor_id,
                           action="gen.memory_saved", target_type="gen_memory_note", target_id=note["id"],
                           detail=_memory_detail("gen" if source == "gen" else "settings", note["text"],
                                                 body.proposal_id), ip=user.ip)
    return note


@router.patch("/memory/{note_id}")
async def update_memory(note_id: uuid.UUID, body: MemoryPatch, user: service.CurrentUser = Depends(require_owner),
                        db: AsyncSession = DB) -> dict[str, Any]:
    if not body.model_fields_set & {"text", "reason"}:
        raise field_errors({"text": "Nhập nội dung cần sửa"})
    try:
        note = await memory_notes.update(db, user, note_id, body.text, body.reason,
                                         set_reason="reason" in body.model_fields_set)
    except ValueError as e:
        raise _memory_422("text", e) from e
    await actionlog.record(db, org_id=user.org_id, actor_type="user", actor_id=user.actor_id,
                           action="gen.memory_updated", target_type="gen_memory_note", target_id=note["id"],
                           detail=_memory_detail("settings", note["text"]), ip=user.ip)
    return note


@router.delete("/memory/{note_id}", status_code=204)
async def delete_memory(note_id: uuid.UUID, user: service.CurrentUser = Depends(require_owner),
                        db: AsyncSession = DB) -> Response:
    cur = await memory_notes.get(db, user.org_id, note_id)
    await memory_notes.delete(db, user.org_id, note_id)
    await actionlog.record(db, org_id=user.org_id, actor_type="user", actor_id=user.actor_id,
                           action="gen.memory_deleted", target_type="gen_memory_note", target_id=str(note_id),
                           detail=_memory_detail("settings", cur.text), ip=user.ip)
    return Response(status_code=204)


# ── Gen v2 (A4): đề xuất thao tác có xác nhận — gh.gen.proposals ──

@router.get("/assignees")
async def assignees(user: service.CurrentUser = Depends(gen_user), db: AsyncSession = DB) -> dict[str, Any]:
    """Người có thể được giao việc (tên + vai trò, KHÔNG email) — cho tool `staff.list` và ô chọn trên thẻ đề xuất."""
    if user.permissions.get("queue.act", rbac.NONE) == rbac.NONE:
        raise forbidden("queue.act")
    rows = (await db.execute(text("""
        SELECT u.id, u.display_name, r.name AS role_name FROM core.users u
        LEFT JOIN core.user_roles ur ON ur.user_id = u.id LEFT JOIN core.roles r ON r.id = ur.role_id
        WHERE u.org_id = :o AND u.is_active AND u.deleted_at IS NULL
        ORDER BY lower(u.display_name) LIMIT 200"""), {"o": user.org_id})).all()
    return {"items": [{"id": str(r.id), "name": r.display_name, "role": r.role_name, "me": r.id == user.id}
                      for r in rows]}


async def _owned_proposal(request: Request, user: service.CurrentUser, pid: uuid.UUID,
                          *, allow_cancelled: bool = False) -> dict[str, Any]:
    p = await proposals.load(request.app.state.redis, pid)
    if p is None or p.get("user_id") != str(user.id) or p.get("org_id") != str(user.org_id):
        raise not_found("Đề xuất (có thể đã hết hạn)")
    if allow_cancelled and p.get("status") == "cancelled":
        return p
    if p.get("status") != "pending":
        raise conflict("GEN_PROPOSAL_DECIDED", "Đề xuất này đã được xác nhận hoặc đã huỷ")
    return p


async def _log_apart(user: service.CurrentUser, action: str, result: str, p: dict[str, Any],
                     **detail: Any) -> None:
    """Ghi Action Log ở transaction riêng — request đang lỗi (rollback) vẫn để lại dấu vết bị chặn/thất bại."""
    async with sessionmaker()() as s:
        await s.execute(text("SELECT set_config('app.org_id', :o, true)"), {"o": str(user.org_id)})
        await actionlog.record(s, org_id=user.org_id, actor_type="user", actor_id=user.actor_id, action=action,
                               target_type="gen_proposal", target_id=p["id"], result=result,
                               detail={"via": "gen", "proposal_id": p["id"], "type": p["type"],
                                       "turn_id": p["turn_id"], **detail}, ip=user.ip)
        await s.commit()


# ── F-87: đề xuất ghi Phiên của bản mới — mỗi (tổ chức, phiên bản) ghi Kho ĐÚNG MỘT lần, dù có nhiều Owner ──
# pending → writing (một Owner đang ghi) → written | pending (lỗi CHẮC CHẮN chưa ghi) | uncertain (502
# HUB_WRITE_UNCERTAIN: có thể đã ghi — chỉ Owner đó bấm lại hoặc huỷ, sau khi thẻ cảnh báo mở Kho kiểm trước) |
# cancelled. Khi bản đã ghi / đã huỷ / ghi chưa chắc, thẻ của các Owner KHÁC được đóng (Redis + tin đã lưu + chuông) để
# không còn nút Xác nhận chết.

RELEASE_DECIDED_MSG = "Bản này đã được ghi vào Kho hoặc đã huỷ — thẻ đã đóng"
RELEASE_BUSY_MSG = "Owner khác đang ghi bản này vào Kho — chưa ghi gì, thử lại sau ít phút"
RELEASE_RETRY_MSG = "Bản này vừa đổi trạng thái (một lần ghi vừa lỗi) — chưa ghi gì, Sếp bấm Xác nhận lại"
RELEASE_CLOSED_WRITTEN = "Owner khác đã ghi bản này vào Kho"
RELEASE_CLOSED_CANCELLED = "Owner khác đã huỷ ghi bản này"
RELEASE_CLOSED_UNCERTAIN = ("Owner khác vừa ghi bản này nhưng chưa chắc đã vào Kho — Owner đó kiểm Kho rồi bấm lại "
                            "hoặc huỷ; Sếp không cần ghi")
_RELEASE_CLOSED = {"written": RELEASE_CLOSED_WRITTEN, "cancelled": RELEASE_CLOSED_CANCELLED,
                   "uncertain": RELEASE_CLOSED_UNCERTAIN, "expired": RELEASE_CLOSED_CANCELLED}


async def _release_claim(db: AsyncSession, user: service.CurrentUser, version: str, p: dict[str, Any],
                         redis: Any) -> None:
    """Giành quyền ghi Phiên của bản `version` (→ writing): từ `pending`, hoặc từ `uncertain` khi CHÍNH Owner này là
    người vừa ghi chưa chắc. Không giành được → 409 TRƯỚC khi gọi Gen-hub: bản đã ghi / huỷ / hết hạn, hoặc Owner KHÁC
    vừa ghi chưa chắc ⇒ GEN_PROPOSAL_DECIDED và đóng luôn thẻ này (transaction riêng — request sắp rollback); Owner khác
    đang ghi (writing) ⇒ GEN_PROPOSAL_BUSY. Đua: UPDATE trượt vì dòng đang `writing`, rồi lần ghi đó lỗi CHẮC CHẮN và
    trả dòng về `pending` (hoặc `uncertain` của chính Owner này) trước câu SELECT ⇒ bản VẪN ghi được: GEN_PROPOSAL_BUSY
    (bấm lại), KHÔNG đóng thẻ."""
    row = (await db.execute(text("""UPDATE agent.hub_release_proposals SET status = 'writing'
                                    WHERE org_id = :o AND version = :v
                                      AND (status = 'pending' OR (status = 'uncertain' AND uncertain_by = :u))
                                    RETURNING version"""),
                            {"o": user.org_id, "v": version, "u": user.id})).first()
    if row is not None:
        return
    cur = (await db.execute(text("""SELECT status, uncertain_by FROM agent.hub_release_proposals
                                    WHERE org_id = :o AND version = :v"""),
                            {"o": user.org_id, "v": version})).first()
    status = str(cur.status) if cur is not None else None
    if status == "writing":
        raise conflict("GEN_PROPOSAL_BUSY", RELEASE_BUSY_MSG)
    if status == "pending" or (status == "uncertain" and cur is not None and cur.uncertain_by == user.id):
        raise conflict("GEN_PROPOSAL_BUSY", RELEASE_RETRY_MSG)
    # Đã ghi / đã huỷ / hết hạn / Owner KHÁC ghi chưa chắc: thẻ này không bao giờ ghi được nữa ⇒ đóng nó luôn.
    async with sessionmaker()() as s:
        await s.execute(text("SELECT set_config('app.org_id', :o, true)"), {"o": str(user.org_id)})
        await _close_release_cards(s, redis, user.org_id, [p["id"]],
                                   _RELEASE_CLOSED.get(str(status), RELEASE_CLOSED_CANCELLED))
        await s.commit()
    raise conflict("GEN_PROPOSAL_DECIDED", RELEASE_DECIDED_MSG)


async def _release_set(user: service.CurrentUser, version: str, status: str, *, kho_ma: str | None = None,
                       from_: tuple[str, ...] = ("writing",), redis: Any = None, self_id: str | None = None) -> bool:
    """Đặt trạng thái dòng `hub_release_proposals` ở transaction riêng (kết quả ghi Kho không được mất nếu bước sau
    lỗi). `status='revert'`: lỗi chắc chắn chưa ghi ⇒ về `pending`, nhưng về `uncertain` nếu trước đó đã có lần ghi chưa
    chắc (`uncertain_by`). `uncertain` ghi lại Owner vừa ghi. Chuyển từ `uncertain` chỉ khi đúng Owner đó. Trả True khi
    đã đổi. Có `redis` + `self_id` và trạng thái mới là written / cancelled / uncertain ⇒ đóng luôn thẻ của các Owner
    KHÁC (cùng transaction riêng này — request ngoài có thể sắp rollback vì lỗi)."""
    decided = status in ("written", "cancelled")
    async with sessionmaker()() as s:
        await s.execute(text("SELECT set_config('app.org_id', :o, true)"), {"o": str(user.org_id)})
        row = (await s.execute(text("""
            UPDATE agent.hub_release_proposals
            SET status = CASE WHEN CAST(:s AS text) = 'revert'
                              THEN (CASE WHEN uncertain_by IS NULL THEN 'pending' ELSE 'uncertain' END)
                              ELSE CAST(:s AS text) END,
                kho_ma = COALESCE(CAST(:m AS text), kho_ma),
                uncertain_by = CASE WHEN CAST(:s AS text) = 'uncertain' THEN CAST(:u AS uuid) ELSE uncertain_by END,
                decided_at = CASE WHEN :d THEN now() ELSE decided_at END,
                decided_by = CASE WHEN :d THEN CAST(:u AS uuid) ELSE decided_by END
            WHERE org_id = :o AND version = :v AND status = ANY(CAST(:f AS text[]))
              AND (status <> 'uncertain' OR uncertain_by = CAST(:u AS uuid))
            RETURNING status, proposal_ids"""),
            {"s": status, "m": kho_ma, "d": decided, "u": user.id, "o": user.org_id, "v": version,
             "f": list(from_)})).first()
        note = _RELEASE_CLOSED.get(str(row.status)) if row is not None else None
        if row is not None and redis is not None and self_id is not None and note and row.status != "expired":
            if row.status == "written" and kho_ma:
                note = f"{note} ({kho_ma})"
            others = [str(x) for x in (row.proposal_ids or []) if str(x) != self_id]
            await _close_release_cards(s, redis, user.org_id, others, note)
        await s.commit()
    return row is not None


async def _close_release_cards(db: AsyncSession, redis: Any, org_id: uuid.UUID, pids: list[str], note: str) -> int:
    """Đóng các thẻ đề xuất ghi Phiên còn `pending` (Redis + tin đã lưu trong hội thoại) với lời ghi chú
    `labels.closed`, và đánh dấu đã đọc chuông `gen.kho_proposal` của các thẻ đó. Bên gọi đã đặt app.org_id và tự
    commit."""
    closed = 0
    for pid in pids:
        q = await proposals.load(redis, pid)
        if q is None or q.get("status") != "pending" or q.get("org_id") != str(org_id):
            continue
        labels = {**(q.get("labels") or {}), "closed": note}
        q.update({"status": "cancelled", "labels": labels})
        await redis.set(proposals.key(pid), orjson.dumps(q), keepttl=True)
        await store.update_proposal_step(db, uuid.UUID(q["conversation_id"]), uuid.UUID(q["turn_id"]), pid,
                                         {"status": "cancelled", "labels": labels})
        await db.execute(text("""UPDATE core.notifications SET read_at = now()
                                 WHERE org_id = :o AND kind = 'gen.kho_proposal' AND link = :l AND read_at IS NULL"""),
                         {"o": org_id, "l": f"/overview?gen={q['conversation_id']}"})
        closed += 1
    return closed


async def _mark_uncertain(redis: Any, pid: str) -> None:
    """502 HUB_WRITE_UNCERTAIN: gắn nhãn `uncertain='1'` vào đề xuất (Redis, giữ TTL) — thẻ vẫn chờ (Sếp mở Kho kiểm
    rồi bấm lại hoặc Huỷ); nếu sau đó Huỷ, thẻ KHÔNG được nói "không ghi gì vào Kho" (Kho có thể đã có bản ghi). Xác
    nhận lại thành công thay toàn bộ nhãn ⇒ nhãn này tự mất."""
    q = await proposals.load(redis, pid)
    if q is None or q.get("status") != "pending":
        return
    q["labels"] = {**(q.get("labels") or {}), "uncertain": "1"}
    await redis.set(proposals.key(pid), orjson.dumps(q), keepttl=True)


class ConfirmIn(BaseModel):
    """Các trường người dùng đã sửa trên thẻ (bỏ trống = giữ nguyên như Gen đề xuất)."""
    fields: dict[str, Any] = Field(default_factory=dict)


@router.post("/proposals/{pid}/confirm")
async def confirm_proposal(pid: uuid.UUID, request: Request, body: ConfirmIn | None = None,
                           user: service.CurrentUser = Depends(gen_user), db: AsyncSession = DB) -> dict[str, Any]:
    body = body or ConfirmIn()
    redis = request.app.state.redis
    p = await _owned_proposal(request, user, pid)
    ptype: str = p["type"]
    spec = proposals.SPECS[ptype]
    locked = {k for k, v in body.fields.items() if k not in spec.editable and p["fields"].get(k) != v}
    if locked:
        raise field_errors({k: "Không sửa được trường này" for k in sorted(locked)})
    merged = {**p["fields"], **{k: v for k, v in body.fields.items() if k in spec.editable}}
    tz = await proposals.org_tz(db, user.org_id)
    try:
        fields = proposals.normalize(ptype, merged, tz, confirming=True)
    except ValueError as e:
        raise field_errors({"fields": str(e)}) from e
    target = proposals.target_of(ptype, fields)
    err = proposals.permission_error(user.permissions, ptype, target, user.role_code,
                                     await proposals.kho_for(db, user.org_id, ptype))
    if err:
        await _log_apart(user, "gen.proposal_confirmed", "blocked", p, reason=err)
        raise forbidden(err)
    if proposals.requires_pin(target) and not user.pin_active():
        raise pin_required()
    try:
        lab = await proposals.labels(db, user, ptype, fields, redis, request.app)
    except ValueError as e:
        raise field_errors({"fields": str(e)}) from e
    if p.get("release_version"):
        lab = {**lab, "release": str(p["release_version"])}  # F-87: giữ nhãn "bản" của thẻ ghi Phiên
    if not await redis.set(proposals.claim_key(pid), "1", nx=True, ex=proposals.CLAIM_TTL_S):
        raise conflict("GEN_PROPOSAL_BUSY", "Đề xuất đang được thực hiện")
    release: str | None = p.get("release_version")
    claimed = False
    calling = False
    try:
        # Permit ghi Kho (nếu có) được phát TRONG plan_call — sau khi đã qua kiểm quyền + PIN ở trên.
        call = await proposals.plan_call(db, user, ptype, fields, p["id"])
        if release:
            await _release_claim(db, user, release, p, redis)
            claimed = True
        # Đóng transaction của request ngoài TRƯỚC khi gọi nội bộ: request ngoài có thể đang giữ khoá dòng
        # core.sessions (gia hạn phiên / trượt phiên PIN trong load_session) mà request nội bộ cũng UPDATE → hai bên
        # chờ nhau (Postgres không thấy vòng chờ qua ứng dụng). Commit cũng trả connection về pool trong lúc chờ.
        await db.commit()
        calling = True
        status, res = await proposals.call_as_user(request.app, dict(request.cookies),
                                                   request.headers.get("x-csrf-token", ""), call, ip=user.ip)
        calling = False
        # Transaction mới cho phần ghi còn lại → đặt lại biến RLS (set_config(..., true) chỉ sống trong 1 transaction).
        await db.execute(text("SELECT set_config('app.org_id', :o, true)"), {"o": str(user.org_id)})
    except BaseException:
        await redis.delete(proposals.claim_key(pid))
        if claimed:
            # Lỗi giữa lúc đang gọi ghi (không có phản hồi) ⇒ có thể đã ghi: giữ cho đúng Owner này kiểm, như 502.
            await _release_set(user, str(release), "uncertain" if calling else "revert", redis=redis,
                               self_id=p["id"])
        raise
    if status >= 400:
        await redis.delete(proposals.claim_key(pid))
        err_body = res if isinstance(res, dict) else {}
        uncertain = status == 502 and err_body.get("code") == "HUB_WRITE_UNCERTAIN"
        if uncertain:
            await _mark_uncertain(redis, p["id"])
        if claimed:
            # 502 HUB_WRITE_UNCERTAIN: CÓ THỂ đã ghi ⇒ không trả về 'pending' (Owner khác sẽ ghi trùng) — chỉ Owner này
            # bấm lại / huỷ sau khi mở Kho kiểm. Lỗi khác: chắc chắn chưa ghi ⇒ Sếp bấm Xác nhận lại được.
            await _release_set(user, str(release), "uncertain" if uncertain else "revert", redis=redis,
                               self_id=p["id"])
        await _log_apart(user, "gen.proposal_confirmed", "blocked" if status in (403, 404, 423) else "failed", p,
                         endpoint=f"{call.method} {call.path}", status=status, code=err_body.get("code"))
        extra = {"errors": err_body["errors"]} if isinstance(err_body.get("errors"), dict) else {}
        # Giữ các khoá phụ người dùng cần thấy: mã lỗi của 500 INTERNAL (gửi hỗ trợ), giờ mở khoá/số lần còn lại
        # của PIN. Chỉ danh sách trắng kiểu vô hướng — không chuyển tiếp khoá lạ từ phản hồi nội bộ.
        extra.update({k: err_body[k] for k in _PASSTHROUGH_ERROR_KEYS
                      if isinstance(err_body.get(k), (str, int)) and not isinstance(err_body.get(k), bool)})
        raise ApiError(status, str(err_body.get("code") or "GEN_PROPOSAL_FAILED"),
                       str(err_body.get("title") or "Không thực hiện được đề xuất"), err_body.get("detail"), **extra)
    result = proposals.result_of(call, fields, res)
    if claimed:
        await _release_set(user, str(release), "written", kho_ma=result.get("code"), redis=redis, self_id=p["id"])
    edited = fields != p["fields"]
    patch = {"status": "confirmed", "fields": fields, "labels": lab,
             "summary": proposals.summary(ptype, fields, lab, tz), "result": result}
    p.update(patch)
    await proposals.save(redis, p)
    await store.update_proposal_step(db, uuid.UUID(p["conversation_id"]), uuid.UUID(p["turn_id"]), p["id"], patch)
    label = fields.get("title") or lab.get("item") or (lab.get("target") if ptype in proposals.KHO_TYPES else "")
    detail = {"via": "gen", "proposal_id": p["id"], "type": ptype, "turn_id": p["turn_id"],
              "conversation_id": p["conversation_id"], "edited": edited,
              "endpoint": f"{call.method} {call.path}", "fields_digest": engine.digest(fields)}
    if release:
        detail["release_version"] = release
    await actionlog.record(db, org_id=user.org_id, actor_type="user", actor_id=user.actor_id,
                           action="gen.proposal_confirmed", target_type=result["type"], target_id=result.get("id"),
                           target_label=str(label or "")[:200] or None, detail=detail, ip=user.ip)
    return proposals.public(p)


@router.post("/proposals/{pid}/cancel")
async def cancel_proposal(pid: uuid.UUID, request: Request, user: service.CurrentUser = Depends(gen_user),
                          db: AsyncSession = DB) -> dict[str, Any]:
    redis = request.app.state.redis
    # Thẻ đã đóng (bấm Huỷ lần nữa, hoặc thẻ ghi Phiên vừa được đóng vì Owner khác đã ghi / huỷ) ⇒ trả trạng thái hiện
    # tại để thẻ trên màn cập nhật theo — không 409.
    p = await _owned_proposal(request, user, pid, allow_cancelled=True)
    if p["status"] == "cancelled":
        return proposals.public(p)
    if await redis.exists(proposals.claim_key(pid)):
        raise conflict("GEN_PROPOSAL_BUSY", "Đề xuất đang được thực hiện")
    p["status"] = "cancelled"
    step: dict[str, Any] = {"status": "cancelled"}
    if (p.get("labels") or {}).get("uncertain"):
        step["labels"] = p["labels"]  # huỷ sau lần ghi chưa chắc: mở lại hội thoại vẫn thấy đúng lời "đã đóng"
    if p.get("release_version"):
        # F-87: huỷ một đề xuất ghi Phiên của bản mới = huỷ cho cả tổ chức (mỗi bản chỉ ghi một lần): khi bản còn
        # chờ, hoặc khi CHÍNH Owner này vừa ghi chưa chắc (đã mở Kho kiểm). Thẻ của Owner khác đóng theo.
        version = str(p["release_version"])
        await _release_set(user, version, "cancelled", from_=("pending", "uncertain"), redis=redis, self_id=p["id"])
    await proposals.save(redis, p)
    await store.update_proposal_step(db, uuid.UUID(p["conversation_id"]), uuid.UUID(p["turn_id"]), p["id"], step)
    await actionlog.record(db, org_id=user.org_id, actor_type="user", actor_id=user.actor_id,
                           action="gen.proposal_cancelled", target_type="gen_proposal", target_id=p["id"],
                           detail={"via": "gen", "proposal_id": p["id"], "type": p["type"], "turn_id": p["turn_id"],
                                   "conversation_id": p["conversation_id"]}, ip=user.ip)
    return proposals.public(p)


# ── v0.1.49 (QD-16): nguồn dữ liệu nội bộ cho Gen — Tài liệu, Deal, Vụ việc (chỉ Owner, chỉ đọc, đã che) ──

#: Khoá có giá trị là định danh / mã / thời điểm / số tiền / enum — KHÔNG phải dữ liệu cá nhân và phải giữ nguyên:
#: regex số dài của `mask_for_model` sẽ cắt nát đuôi UUID (vd 446655440000) làm validator không còn khớp id.
KEEP_RAW = frozenset({"id", "code", "person_id", "opportunity_id", "subject_id", "status", "priority", "kind", "mime",
                      "source", "created_at", "updated_at", "opened_at", "resolved_at", "won_at", "bytes",
                      "amount_vnd"})


def _for_model(data: Any) -> Any:
    """Che kết quả trước khi sang model: khoá kiểu mật khẩu/token/api_key/pin (cùng quy tắc `mask_for_model`) bị thay
    hẳn — kể cả giá trị ngắn hay số; khoá trong KEEP_RAW (giá trị vô hướng) giữ nguyên; mọi giá trị khác — chuỗi →
    `mask_for_model` (email, SĐT/số dài, khoá bí mật), list/dict → đệ quy."""
    if isinstance(data, dict):
        out: dict[str, Any] = {}
        for k, v in data.items():
            if is_secret_key(str(k)) and isinstance(v, str | int) and not isinstance(v, bool) and v != "":
                out[k] = MASK
            elif k in KEEP_RAW and not isinstance(v, dict | list):
                out[k] = v
            else:
                out[k] = _for_model(v)
        return out
    if isinstance(data, list):
        return [_for_model(v) for v in data]
    return mask_for_model(data)


def _doc_for_model(item: dict[str, Any]) -> dict[str, Any]:
    """Bỏ `created_by` (dạng `user:<id>`) — `source` đã nói đủ nguồn tài liệu; không đưa principal cho model."""
    return {k: v for k, v in item.items() if k != "created_by"}


@router.get("/sources/documents")
async def source_documents(source: Literal["channel", "agent", "tay"] | None = None,
                           limit: int = Query(20, ge=1, le=20), user: service.CurrentUser = Depends(require_owner),
                           db: AsyncSession = DB) -> Any:
    """Siêu dữ liệu tài liệu (tiêu đề, mô tả, người/nhóm gắn, nguồn) — KHÔNG đọc nội dung tệp."""
    res = await relations_routes.list_documents(owner_person_id=None, owner_group_id=None, source=source,
                                                cursor=None, limit=limit, user=user, db=db)
    return _for_model({**res, "items": [_doc_for_model(i) for i in res["items"]]})


@router.get("/sources/documents/{document_id}")
async def source_document(document_id: uuid.UUID, user: service.CurrentUser = Depends(require_owner),
                          db: AsyncSession = DB) -> Any:
    res = await relations_routes.get_document(document_id, user=user, db=db)
    acl = res.get("acl") or []
    return _for_model({**_doc_for_model({k: v for k, v in res.items() if k != "acl"}), "acl_count": len(acl)})


@router.get("/sources/deals")
async def source_deals(status: Literal["open", "won", "lost"] | None = None, limit: int = Query(20, ge=1, le=20),
                       user: service.CurrentUser = Depends(require_owner), db: AsyncSession = DB) -> Any:
    return _for_model(await market_routes.list_deals(status=status, person_id=None, cursor=None, limit=limit,
                                                      user=user, db=db))


@router.get("/sources/deals/{deal_id}")
async def source_deal(deal_id: uuid.UUID, user: service.CurrentUser = Depends(require_owner),
                      db: AsyncSession = DB) -> Any:
    return _for_model(await market_routes.get_deal(deal_id, user=user, db=db))


@router.get("/sources/cases")
async def source_cases(status: str | None = Query(None, max_length=40),
                       priority: Literal["P1", "P2", "P3"] | None = None, limit: int = Query(20, ge=1, le=20),
                       user: service.CurrentUser = Depends(require_owner), db: AsyncSession = DB) -> Any:
    return _for_model(await market_routes.list_cases(status=status, assignee_user_id=None, priority=priority,
                                                      cursor=None, limit=limit, user=user, db=db))


@router.get("/sources/cases/{case_id}")
async def source_case(case_id: uuid.UUID, user: service.CurrentUser = Depends(require_owner),
                      db: AsyncSession = DB) -> Any:
    return _for_model(await market_routes.get_case(case_id, user=user, db=db))


# ─── v0.1.74: danh bạ nhóm / người + tin nhóm gần đây (tool group.list / person.list / group.recent) ───────────────────
# Chỉ ĐỌC, áp đúng phạm vi `profile.read` + «Quyền xem» của nhóm (gh.biz.core.scope), che bằng `_for_model` trước khi tới model.

_READ_PROFILE = require("profile.read")
SNIPPET_MAX = 160


@router.get("/sources/groups")
async def source_groups(q: str | None = Query(None, max_length=100), limit: int = Query(10, ge=1, le=20),
                        user: service.CurrentUser = Depends(_READ_PROFILE), db: AsyncSession = DB) -> Any:
    res = await relations_routes.list_groups(channel_id=None, kind=None, listen_mode=None, q=q, cursor=None,
                                             limit=limit, user=user, db=db)
    items = [{"id": g["id"], "name": g["name"], "kind": g["kind"], "listen_mode": g["listen_mode"],
              "member_count": g["member_count"], "events_24h": g["events_24h"],
              "channel": (g.get("channel") or {}).get("type")} for g in res["items"]]
    return _for_model({"items": items, "total": res["total"]})


@router.get("/sources/people")
async def source_people(q: str | None = Query(None, max_length=100), limit: int = Query(10, ge=1, le=20),
                        user: service.CurrentUser = Depends(_READ_PROFILE), db: AsyncSession = DB) -> Any:
    res = await relations_routes.list_people(relation=None, heat=None, value=None, priority=None, bot=None,
                                             channel=None, q=q, cursor=None, limit=limit, user=user, db=db)
    items = [{"id": p["id"], "name": p["name"], "relation": p.get("relation"), "channels": p.get("channels")}
             for p in res["items"]]
    return _for_model({"items": items, "total": res["total"]})


@router.get("/sources/groups/recent")
async def source_groups_recent(group_id: uuid.UUID | None = None, hours: int = Query(24, ge=1, le=168),
                               limit: int = Query(10, ge=1, le=20),
                               user: service.CurrentUser = Depends(_READ_PROFILE), db: AsyncSession = DB) -> Any:
    """Nhóm nào có tin mới: mỗi nhóm (có tin trong `hours` giờ) kèm số tin, giờ tin cuối và vài đoạn trích mới nhất; có
    `group_id` ⇒ chỉ nhóm đó với tới `limit` tin gần nhất. Chỉ nhóm người hỏi được xem (phạm vi + «Quyền xem»)."""
    from gh.biz.core.scope import not_found as scope_not_found
    from gh.biz.core.scope import scope_for
    from gh.data.common import iso

    sc = await scope_for(db, user, "profile.read")
    where, params = sc.group_sql("g")
    base: dict[str, Any] = {"o": user.org_id, "h": hours, **params}
    gcond = ""
    if group_id is not None:
        gcond = " AND g.id = :gid"
        base["gid"] = group_id
    rows = (await db.execute(text(f"""
        SELECT g.id, g.name, c.type AS channel, g.listen_mode, count(*) AS n, max(e.occurred_at) AS last_at
        FROM raw.events e JOIN core.groups g ON g.id = e.group_id AND g.org_id = :o
        JOIN core.channels c ON c.id = g.channel_id
        WHERE e.org_id = :o AND e.occurred_at > now() - make_interval(hours => :h) AND {where}{gcond}
        GROUP BY g.id, g.name, c.type, g.listen_mode ORDER BY max(e.occurred_at) DESC LIMIT :lim"""),  # noqa: S608
        {**base, "lim": 1 if group_id else min(limit, 8)})).all()
    if group_id is not None and not rows:
        visible = (await db.execute(text(f"SELECT 1 FROM core.groups g WHERE g.org_id = :o AND g.id = :gid AND {where}"),  # noqa: S608
                                    {"o": user.org_id, "gid": group_id, **params})).first()
        if not visible:
            raise scope_not_found("Nhóm")
    out = []
    for r in rows:
        msgs = (await db.execute(text("""
            SELECT e.occurred_at, e.direction, e.body_text, p.display_name AS who
            FROM raw.events e LEFT JOIN core.person_identities pi ON pi.id = e.sender_identity_id
            LEFT JOIN core.persons p ON p.id = pi.person_id
            WHERE e.org_id = :o AND e.group_id = :g AND e.kind = 'text' AND COALESCE(e.body_text, '') <> ''
              AND e.occurred_at > now() - make_interval(hours => :h)
            ORDER BY e.occurred_at DESC LIMIT :n"""),
                                   {"o": user.org_id, "g": r.id, "h": hours, "n": limit if group_id else 3})).all()
        out.append({"id": str(r.id), "name": r.name, "channel": r.channel, "listen_mode": r.listen_mode,
                    "so_tin": r.n, "tin_cuoi": iso(r.last_at),
                    "tin_moi": [{"luc": iso(m.occurred_at), "tu": "bot" if m.direction == "outbound" else (m.who or "?"),
                                 "noi_dung": str(m.body_text)[:SNIPPET_MAX]} for m in reversed(msgs)]})
    return _for_model({"hours": hours, "items": out})
