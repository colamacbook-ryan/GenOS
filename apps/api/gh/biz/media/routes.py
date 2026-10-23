# ruff: noqa: E501
"""API đọc trạng thái media (L5): từng tin (đang chờ / đã đọc / lỗi + lý do thân thiện) và độ phủ cho dòng "12 voice đã
chép · 3 ảnh chưa đọc ở nhóm X". Chỉ ĐỌC; không có đường nào nhận URL hay file từ người dùng."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from gh.auth import service
from gh.auth.deps import require_owner
from gh.biz.media import status
from gh.db import DB
from gh.errors import not_found

router = APIRouter(tags=["media"])


@router.get("/media/events/{raw_event_id}")
async def media_event(raw_event_id: uuid.UUID, user: service.CurrentUser = Depends(require_owner),
                      db: AsyncSession = DB) -> dict[str, Any]:
    got = await status.event_status(db, user.org_id, [raw_event_id])
    if raw_event_id not in got:
        raise not_found("Tin media")
    return got[raw_event_id].as_dict()


@router.get("/media/coverage")
async def media_coverage(hours: int = Query(24, ge=1, le=24 * 14), group_id: uuid.UUID | None = None,
                         user: service.CurrentUser = Depends(require_owner), db: AsyncSession = DB) -> dict[str, Any]:
    return await status.coverage(db, user.org_id, hours=hours, group_id=group_id)
