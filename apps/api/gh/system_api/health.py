"""GET /system/health — thẻ "Sức khoẻ hệ thống" (Điều khiển hệ thống › Dữ liệu & lưu trữ) và dải "Cần Sếp xử lý".

v0.1.36 (F-6): đọc thuần — không gửi chuông (chuông do vòng theo dõi `gh.health.watch_loop` và các sự kiện gửi).
KHÔNG thuộc `/ready`: genh dùng `/ready` để quyết rollback khi cập nhật.

v0.1.41 (F-84): `/system/ai-cost` — "Chi phí AI hôm nay" theo agent (giờ VN), bảng giá theo model (VND / 1 triệu
token) và "Trần chi phí mỗi ngày" (gh/ai_cost.py). Đọc cần `system.read`; đổi giá/trần cần `system.manage`.
"""

import uuid
from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from gh import ai_cost, health
from gh.auth import rbac, service
from gh.auth.deps import require, require_pin
from gh.chassis import actionlog
from gh.db import DB
from gh.errors import ApiError, not_found

router = APIRouter(tags=["system"])


@router.get("/system/health")
async def system_health(request: Request, db: AsyncSession = DB,
                        user: service.CurrentUser = Depends(require("system.read"))) -> dict[str, Any]:
    """Bộ xử lý nền, trình duyệt nền, hàng đợi lỗi, lịch chạy, sao lưu, cập nhật, ổ đĩa và các sự cố đang mở."""
    return await health.collect(db, request.app.state.redis, user.org_id,
                                started_at=getattr(request.app.state, "health_started_at", None),
                                is_owner=user.role_code == rbac.OWNER)


@router.get("/system/ai-cost")
async def get_ai_cost(day: date | None = Query(None, alias="date"), db: AsyncSession = DB,
                      user: service.CurrentUser = Depends(require("system.read"))) -> dict[str, Any]:
    """Chi phí AI của ngày `date` (YYYY-MM-DD, giờ VN; mặc định hôm nay) theo agent, giá từng model, 7 ngày gần nhất,
    đánh giá Hữu ích 7 ngày."""
    return await ai_cost.summary(db, user.org_id, day)


# ─── hàng lỗi (DLQ): phát lại / đã xem, xoá (v0.1.62 — F064/F102) ────────────────────────────────────────────────

def _dlq_error(e: health.DlqError) -> ApiError:
    if e.code == "not_replayable":
        return ApiError(409, "QUEUE_NOT_REPLAYABLE", "Hàng này không phát lại được — chỉ xem rồi bấm Đã xem, xoá")
    return not_found("Hàng việc lỗi")


@router.post("/system/queues/{stream}/replay")
async def replay_queue(stream: str, request: Request, db: AsyncSession = DB,
                       user: service.CurrentUser = Depends(require("system.manage", rbac.ALL)),
                       _pin: Any = Depends(require_pin("queue.manage"))) -> dict[str, Any]:
    """"Phát lại": đưa các tin khách nằm trong hàng lỗi `gh.bridge.inbound` về đường vào để nhập kho lại (tối đa 500 tin
    mỗi lần). Cần `system.manage` + mã PIN; ghi Action Log (chỉ số lượng, không nội dung tin)."""
    redis = request.app.state.redis
    try:
        out = await health.dlq_replay(redis, stream)
    except health.DlqError as e:
        raise _dlq_error(e) from e
    await actionlog.record(db, org_id=user.org_id, actor_type="user", actor_id=user.actor_id,
                           action="queue.replayed", target_type="queue", target_id=stream, detail=out, ip=user.ip)
    await health.sync_dlq_alert(db, user.org_id, redis)
    return {"stream": stream, **out}


@router.post("/system/queues/{stream}/clear")
async def clear_queue(stream: str, request: Request, db: AsyncSession = DB,
                      user: service.CurrentUser = Depends(require("system.manage", rbac.ALL)),
                      _pin: Any = Depends(require_pin("queue.manage"))) -> dict[str, Any]:
    """"Đã xem, xoá": xoá các việc lỗi có tới lúc bấm (không hoàn tác; việc lỗi mới hơn vẫn còn). Cần `system.manage` +
    mã PIN; ghi Action Log (chỉ số lượng)."""
    redis = request.app.state.redis
    try:
        out = await health.dlq_clear(redis, stream)
    except health.DlqError as e:
        raise _dlq_error(e) from e
    await actionlog.record(db, org_id=user.org_id, actor_type="user", actor_id=user.actor_id,
                           action="queue.cleared", target_type="queue", target_id=stream, detail=out, ip=user.ip)
    await health.sync_dlq_alert(db, user.org_id, redis)
    return {"stream": stream, **out}


class BudgetIn(BaseModel):
    daily_budget_vnd: int | None = Field(default=None, ge=0, le=ai_cost.MAX_BUDGET_VND)


@router.put("/system/ai-cost/budget")
async def put_ai_budget(body: BudgetIn, db: AsyncSession = DB,
                        user: service.CurrentUser = Depends(require("system.manage", rbac.ALL))) -> dict[str, Any]:
    """Đặt/bỏ "Trần chi phí mỗi ngày" (null = bỏ trần). Chuông vượt trần do vòng theo dõi sức khoẻ gửi."""
    before = await ai_cost.get_budget(db, user.org_id)
    await ai_cost.set_budget(db, user.org_id, body.daily_budget_vnd)
    await actionlog.record(db, org_id=user.org_id, actor_type="user", actor_id=user.actor_id,
                           action="ai.budget_changed", target_type="settings", target_id="ai_cost",
                           detail={"from": before, "to": body.daily_budget_vnd}, ip=user.ip)
    return await ai_cost.summary(db, user.org_id)


class PriceIn(BaseModel):
    in_vnd_per_mtok: float | None = Field(default=None, ge=0, le=ai_cost.MAX_PRICE_PER_MTOK)
    out_vnd_per_mtok: float | None = Field(default=None, ge=0, le=ai_cost.MAX_PRICE_PER_MTOK)


@router.put("/system/ai-cost/prices/{model_id}")
async def put_ai_price(model_id: uuid.UUID, body: PriceIn, db: AsyncSession = DB,
                       user: service.CurrentUser = Depends(require("system.manage", rbac.ALL))) -> dict[str, Any]:
    """Giá model (VND / 1 triệu token vào/ra). Cả hai null ⇒ xoá giá (model về "chưa có giá"). Giá áp lại cho cả
    lịch sử (tính lúc đọc)."""
    if not await ai_cost.model_in_org(db, user.org_id, model_id):
        raise not_found("Model")
    await ai_cost.set_price(db, user.org_id, model_id, body.in_vnd_per_mtok, body.out_vnd_per_mtok, user.id)
    await actionlog.record(db, org_id=user.org_id, actor_type="user", actor_id=user.actor_id,
                           action="ai.price_changed", target_type="model", target_id=str(model_id),
                           detail={"in_vnd_per_mtok": body.in_vnd_per_mtok,
                                   "out_vnd_per_mtok": body.out_vnd_per_mtok}, ip=user.ip)
    return await ai_cost.summary(db, user.org_id)
