"""Chuyển nội dung bot KHÔNG được tự đăng cho Sếp duyệt: tạo bản nháp ở Bàn làm việc (đường CŨ: Xác nhận + mã
PIN). Module này
chỉ TẠO nháp — không duyệt, không gửi, không ký permit (test AST: chỉ được import `create_draft`,
`effective_level`, `Target`
từ `gh.biz.core.drafts`)."""

import logging
import uuid
from typing import Any

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from gh.biz.core.drafts import Target, create_draft, effective_level

log = logging.getLogger("gh.owner_channel.draft_hand")


async def group_level(db: AsyncSession, org_id: uuid.UUID, group_id: uuid.UUID) -> int:
    """Mức tự trị hiệu lực của nhóm (tổ chức → nhóm). ≤ 2 = «chỉ ghi nhận / tóm tắt / chấm điểm»."""
    return int(await effective_level(db, org_id, group_id=group_id))


async def hand_to_workbench(db: AsyncSession, redis: Redis | None, *, org_id: uuid.UUID, channel: str,
                            group_id: uuid.UUID, text: str, created_by: uuid.UUID | None,
                            title: str = "Trả lời trong nhóm (bot không tự đăng)") -> str | None:
    """Tạo bản nháp tin vào nhóm, chờ Sếp Xác nhận + PIN. Trả mã nháp (ACT-…) hoặc None nếu không tạo được."""
    try:
        res: dict[str, Any] = await create_draft(
            db, org_id=org_id, kind="message", title=title, body_text=text,
            target=Target(channel, "group", group_id=group_id), created_by=created_by,
            subject=("group", group_id), redis=redis)
    except Exception:  # noqa: BLE001 — không tạo được nháp thì chỉ báo Sếp nội dung
        log.warning("không tạo được nháp bàn giao", exc_info=True)
        return None
    return str(res["code"]) if res.get("code") else None
