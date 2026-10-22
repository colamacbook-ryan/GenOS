"""/owner — API Mặt tiền Owner (v0.1.55, gói G5; mở rộng v0.1.59, gói G1). CHỈ ĐỌC, CHỈ Owner (vai trò khác → 403
FORBIDDEN).

- `GET /today` — Hôm nay: Cần Sếp duyệt (≤ 10), 4 số, Bản tin mới nhất, giá trị bộ lọc, gợi ý, tiến độ việc bắt buộc, và
  `nav` (số đếm trên thanh điều hướng: tin Khẩn đang mở, việc cần quyết).
- `GET /brief?id=` — Bản tin cho Tình báo: bản đã lưu (`latest`) + số liệu đọc lúc này (`live`), cảnh báo, lịch sử.
- `GET /feed?view=&offset=&limit=` — dòng tin "Thông tin" xếp theo mức Khẩn / Quan trọng / Thường; `GET
  /feed/{id}/context`
  — hội thoại quanh một tin.
- `GET /relations?list=hot|cooling|bridges|matches|care&limit=20` — Quan hệ: 5 danh sách, mỗi dòng mở Hồ sơ sống
  (limit chặn ≤ 50).
- `GET /insights?range=week|month&only=` — Phân tích 9 chỉ số tính thẳng từ dữ liệu đang có.
- `GET /proposals` — đề xuất của Gen còn hiệu lực của chính Owner (`state: "unavailable"` khi Redis không đọc được).
- `GET /tasks` — Việc: Hộp thư đã lọc, Bàn làm việc, Việc & Nhắc hẹn (đếm + vài dòng + link sâu).

KHÔNG có POST/PUT/PATCH/DELETE: mọi thao tác ghi đi qua luồng sẵn có (duyệt nháp, đề xuất Gen + mã PIN, Bộ não AI) —
test `test_owner_v0155` kiểm không có decorator ghi nào trong tệp này. Không gọi model, không ghi Action Log.
"""

import logging
import uuid
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query, Request
from redis.exceptions import RedisError
from sqlalchemy.ext.asyncio import AsyncSession

from gh.auth import service
from gh.auth.deps import require_owner
from gh.db import DB
from gh.owner import brief as brief_mod
from gh.owner import feed as feed_mod
from gh.owner import insights as insights_mod
from gh.owner import proposals_view
from gh.owner import service as svc

router = APIRouter(prefix="/owner", tags=["owner"])
log = logging.getLogger("gh.owner.routes")


async def _proposals(request: Request, db: AsyncSession, org_id: uuid.UUID,
                     user_id: uuid.UUID) -> tuple[list[dict[str, Any]], bool]:
    """Đề xuất Gen còn hiệu lực + cờ "đọc được". Redis chỉ là nguồn xác nhận đề xuất: Redis chậm / hỏng thì phần đề xuất
    rỗng và cờ = False, KHÔNG làm hỏng cả `/today` (thanh điều hướng, Tình báo) hay `/proposals`. Chỉ ghi loại lỗi
    (không ghi nội dung)."""
    try:
        return await proposals_view.list_proposals(db, request.app.state.redis, org_id, user_id), True
    except (RedisError, OSError, TimeoutError) as e:     # CancelledError là BaseException ⇒ vẫn đi qua
        log.warning("owner.proposals_unavailable: %s", type(e).__name__)
        return [], False


@router.get("/today")
async def get_today(request: Request, user: service.CurrentUser = Depends(require_owner),
                    db: AsyncSession = DB) -> dict[str, Any]:
    data = await svc.today(db, user.org_id, user.id)
    props, ok = await _proposals(request, db, user.org_id, user.id)
    # `urgent_open` đếm bằng CÙNG hàm với `feed.counts.urgent_open`; `decide` = nháp chờ duyệt (mọi tuổi) + đề xuất
    # Gen hợp lệ. Redis không đọc được ⇒ `decide` chỉ đếm nháp và có thêm `proposals_unavailable: true`.
    data["nav"] = {"urgent_open": await feed_mod.urgent_open(db, user.org_id),
                   "decide": await svc.pending_drafts(db, user.org_id) + len(props)}
    if not ok:
        data["nav"]["proposals_unavailable"] = True
    return data


@router.get("/brief")
async def get_brief(id: str | None = Query(None, description="Mã bản tin (hội thoại) trong `history`"),  # noqa: A002
                    user: service.CurrentUser = Depends(require_owner), db: AsyncSession = DB) -> dict[str, Any]:
    return await brief_mod.brief(db, user.org_id, user.id, id)


@router.get("/feed")
async def get_feed(view: Literal["important", "all", "opportunity", "alert", "approval"] = Query("important"),
                   offset: int = Query(0, description="Chặn ≥ 0"),
                   limit: int = Query(feed_mod.DEFAULT_LIMIT, description="Chặn trong [1, 50]"),
                   user: service.CurrentUser = Depends(require_owner), db: AsyncSession = DB) -> dict[str, Any]:
    return await feed_mod.feed(db, user.org_id, view, offset, feed_mod.clamp_limit(limit))


@router.get("/feed/{item_id}/context")
async def get_feed_context(item_id: str, user: service.CurrentUser = Depends(require_owner),
                           db: AsyncSession = DB) -> dict[str, Any]:
    return await feed_mod.context(db, user.org_id, item_id)


@router.get("/relations")
async def get_relations(list: Literal["hot", "cooling", "bridges", "matches", "care"] = Query("hot"),  # noqa: A002
                        limit: int = Query(svc.DEFAULT_LIMIT, description="Chặn trong [1, 50]"),
                        user: service.CurrentUser = Depends(require_owner), db: AsyncSession = DB) -> dict[str, Any]:
    return await svc.relations(db, user.org_id, list, svc.clamp_limit(limit))


@router.get("/insights")
async def get_insights(range_: Literal["week", "month"] = Query("week", alias="range"),
                       only: str | None = Query(None, description="Khoá chỉ số, cách nhau dấu phẩy"),
                       user: service.CurrentUser = Depends(require_owner), db: AsyncSession = DB) -> dict[str, Any]:
    return await insights_mod.insights(db, user.org_id, range_, insights_mod.parse_only(only))


@router.get("/proposals")
async def get_proposals(request: Request, user: service.CurrentUser = Depends(require_owner),
                        db: AsyncSession = DB) -> dict[str, Any]:
    items, ok = await _proposals(request, db, user.org_id, user.id)
    return {"items": items} if ok else {"items": [], "state": "unavailable"}


@router.get("/tasks")
async def get_tasks(user: service.CurrentUser = Depends(require_owner), db: AsyncSession = DB) -> dict[str, Any]:
    return await svc.tasks(db, user.org_id)
