"""Khung Console: danh mục theo quyền, thanh trạng thái đầu trang, health/ready."""

from typing import Any

from fastapi import APIRouter, Depends, Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gh.auth import service
from gh.auth.deps import current_user
from gh.chassis import policy
from gh.data.common import LISTENING_MODES
from gh.db import DB, sessionmaker
from gh.errors import JsonResponse
from gh.shell import navigation

router = APIRouter(tags=["shell"])


async def _badges(db: AsyncSession, user: service.CurrentUser) -> dict[str, int | None]:
    # Nguồn số thật của từng màn được thêm ở giai đoạn làm màn đó. v0.1.42 (F-41): màn Plugin đóng băng,
    # ẩn khỏi thanh bên ⇒ không đếm ops.plugins nữa.
    return {}


async def has_staff(db: AsyncSession, org_id: Any) -> bool:
    """Tổ chức đã có nhân viên (person_type='staff' còn hiệu lực) chưa — quyết định hiện Đánh giá/Chăm sóc."""
    return bool((await db.execute(text("""
        SELECT EXISTS(SELECT 1 FROM core.persons WHERE org_id = :o AND person_type = 'staff'
                      AND deleted_at IS NULL AND merged_into_id IS NULL)"""), {"o": org_id})).scalar_one())


@router.get("/navigation")
async def get_navigation(user: service.CurrentUser = Depends(current_user),
                         db: AsyncSession = DB) -> list[dict[str, Any]]:
    return navigation.build(user.permissions, await _badges(db, user), has_staff=await has_staff(db, user.org_id))


async def header_payload(db: AsyncSession, org_id: Any) -> dict[str, Any]:
    channels_live = (await db.execute(text("""
        SELECT count(DISTINCT c.id) FROM core.channels c JOIN core.channel_sessions s ON s.channel_id = c.id
        WHERE c.org_id = :o AND s.state = 'active' AND s.ended_at IS NULL"""), {"o": org_id})).scalar_one()
    # v0.1.43 (F-29): kênh ĐÃ TỪNG đăng nhập thành công (phiên có started_at) — tách "chưa nối kênh" với "kênh mất
    # phiên, cần quét lại QR" ở trạng thái trống. Dòng core.channels luôn có sẵn từ bootstrap nên không đếm bảng đó.
    channels_connected = (await db.execute(text("""
        SELECT count(DISTINCT c.id) FROM core.channels c JOIN core.channel_sessions s ON s.channel_id = c.id
        WHERE c.org_id = :o AND s.started_at IS NOT NULL"""), {"o": org_id})).scalar_one()
    groups = (await db.execute(text("""
        SELECT count(*) FROM core.groups WHERE org_id = :o AND left_at IS NULL AND listen_mode = ANY(:m)"""),
        {"o": org_id, "m": list(LISTENING_MODES)})).scalar_one()
    org = (await db.execute(text("SELECT settings, timezone FROM core.organizations WHERE id = :o"),
                            {"o": org_id})).one()
    settings = org.settings or {}
    return {"channels_live": channels_live, "channels_connected": channels_connected, "groups_listening": groups,
            "autonomy_level": int(settings.get("autonomy_level", policy.DEFAULT_AUTONOMY)),
            "data_confidence": await data_confidence_today(db, org_id, org.timezone)}


async def data_confidence_today(db: AsyncSession, org_id: Any, tz: str | None) -> float | None:
    """Owner chốt 24/09/2026: tin sàng lọc hôm nay vào thẳng Kho sạch ÷ (sạch + tin cậy thấp), không tính nhiễu.

    "Hôm nay" theo múi giờ của tổ chức. Chưa sàng lọc tin nào hôm nay → None (header hiện "—").
    """
    r = (await db.execute(text("""
        SELECT count(*) FILTER (WHERE state = 'clean') AS clean,
               count(*) FILTER (WHERE state = 'lowconf') AS lowconf
        FROM refinery.event_state
        WHERE org_id = :o AND state IN ('clean', 'lowconf')
          AND updated_at >= date_trunc('day', now() AT TIME ZONE :tz) AT TIME ZONE :tz"""),
        {"o": org_id, "tz": tz or "Asia/Ho_Chi_Minh"})).one()
    total = r.clean + r.lowconf
    return round(r.clean / total, 4) if total else None


async def publish_header(db: AsyncSession, redis: Any, org_id: Any) -> None:
    """Đẩy số kênh/nhóm mới lên thanh đầu trang của mọi Console đang mở."""
    from gh import realtime

    await realtime.publish(redis, "header", await header_payload(db, org_id), org_id=org_id)


@router.get("/header")
async def get_header(user: service.CurrentUser = Depends(current_user),
                     db: AsyncSession = DB) -> dict[str, Any]:
    return await header_payload(db, user.org_id)


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/ready")
async def ready(request: Request) -> JsonResponse:
    out: dict[str, str] = {}
    try:
        async with sessionmaker()() as db:
            await db.execute(text("SELECT 1"))
        out["db"] = "ok"
    except Exception:  # noqa: BLE001
        out["db"] = "down"
    redis = getattr(request.app.state, "redis", None)
    try:
        out["redis"] = "ok" if redis is not None and await redis.ping() else "down"
    except Exception:  # noqa: BLE001
        out["redis"] = "down"
    out["objects"] = "skip"
    try:
        beat = await redis.get("gh:bridge:heartbeat") if redis is not None else None
        out["bridge"] = "ok" if beat else "down"
    except Exception:  # noqa: BLE001
        out["bridge"] = "down"
    healthy = out["db"] == "ok" and out["redis"] == "ok"
    return JsonResponse(out, status_code=200 if healthy else 503)
