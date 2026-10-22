"""Làm mới cache tín hiệu của Gen hướng dẫn (v0.1.59, g6) — thẻ "Hôm nay của Sếp" đọc lại NGAY sau khi Sếp đổi
cấu hình.

`signals.collect` cache tín hiệu một tổ chức 60 giây (`CACHE_KEY`). Mọi nơi vừa làm đổi thứ thẻ đó đọc (gọi thử
nguồn AI, thêm / sửa / xoá nhà cung cấp, khoá API, model, gán model, kiểm "Việc Sếp cần làm") gọi
`commit_and_invalidate` (hoặc `invalidate` khi đã tự commit) để lượt mở thẻ kế tiếp dựng lại từ CSDL — nếu không,
thẻ còn báo "Gen chưa có model" tới 60 giây dù Sếp vừa Kiểm tra xanh.

`invalidate` tăng bộ đếm phiên bản (`CACHE_VER_KEY`) RỒI xoá khoá: lượt `collect` đã đọc CSDL trước lúc commit sẽ
thấy bộ đếm đã đổi và KHÔNG ghi bản cũ trở lại cache (xem `signals._SET_IF_VER`). Không đọc / ghi nội dung cache.
Redis vắng hoặc lỗi thì bỏ qua (cache tự hết hạn sau 60 giây): làm mới thẻ là việc phụ, không bao giờ được làm hỏng
thao tác chính của Sếp.
"""

import logging
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from gh.gen.coach.signals import CACHE_KEY, CACHE_VER_KEY, CACHE_VER_TTL_S

log = logging.getLogger("gh.gen.coach")


async def invalidate(redis: Any, org_id: uuid.UUID) -> None:
    """Làm mất hiệu lực cache tín hiệu coach của `org_id`. `redis` None hoặc lỗi ⇒ bỏ qua + ghi cảnh báo, không ném.

    PHẢI gọi SAU khi dữ liệu mới đã commit (lượt `collect` kế tiếp đọc CSDL bằng phiên khác)."""
    if redis is None:
        log.warning("Không làm mới được cache Gen hướng dẫn (%s): chưa có Redis", org_id)
        return
    try:
        ver_key = CACHE_VER_KEY.format(org_id)
        await redis.incr(ver_key)               # trước: chặn lượt gom đang chạy ghi bản cũ sau lệnh xoá
        await redis.expire(ver_key, CACHE_VER_TTL_S)
        await redis.delete(CACHE_KEY.format(org_id))
    except Exception as exc:  # noqa: BLE001 — cache chỉ là tối ưu; lỗi Redis không được chặn thao tác của Sếp
        log.warning("Không làm mới được cache Gen hướng dẫn (%s): %s", org_id, type(exc).__name__)


async def commit_and_invalidate(db: AsyncSession, redis: Any, org_id: uuid.UUID) -> None:
    """Commit RỒI làm mới cache — thứ tự bắt buộc (xoá trước commit thì lượt mở thẻ kế tiếp dựng lại từ dữ liệu cũ).

    Gọi ở CUỐI route, sau mọi lệnh đọc / ghi CSDL của yêu cầu (commit kết thúc giao dịch nên `app.org_id` của RLS
    mất theo)."""
    await db.commit()
    await invalidate(redis, org_id)
