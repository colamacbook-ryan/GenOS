"""POST /client-errors — web báo lỗi giao diện (Mã lỗi ERR-…) về máy chủ (v0.1.44, F-4b).

Không cần đăng nhập và được miễn SetupGate (màn thiết lập cũng có thể lỗi). Thân giới hạn chặt (pydantic); tối đa
20 lần/phút/IP và 200 lần/phút chung mọi IP qua Redis (không có Redis ⇒ cho qua). Ghi MỘT dòng log logger `gh.client`
mức WARNING — dòng log mang `request_id` của chính request này (lọc log JSON theo Mã yêu cầu), cùng
`client_request_id` (Mã yêu cầu của lần gọi API hỏng mà web đang hiện). Thông điệp/stack đi qua bộ che bí mật của log
trước khi ghi. Không ghi Action Log.
"""

import logging
from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field

from gh.auth import service
from gh.auth.deps import client_ip
from gh.chassis import actionlog
from gh.db import DB
from gh.errors import ApiError, request_id_var

router = APIRouter(tags=["system"])
log = logging.getLogger("gh.client")

RATE_LIMIT = 20
RATE_WINDOW_S = 60
RATE_KEY = "gh:client-errors:{}"
#: Trần CHUNG mọi IP mỗi phút — IP lấy từ X-Forwarded-For (trái nhất) giả được bằng cách đổi header mỗi lần, nên giới
#: hạn theo IP một mình không chặn được ai cố ghi log vô hạn (mỗi dòng tới ~10 KB).
GLOBAL_RATE_LIMIT = 200
GLOBAL_RATE_KEY = "gh:client-errors:_all"
REQUEST_ID_PATTERN = r"^[A-Za-z0-9_-]{8,64}$"


class ClientErrorIn(BaseModel):
    model_config = ConfigDict(extra="ignore")

    error_id: str = Field(pattern=r"^ERR-[0-9A-Z]{1,8}-[0-9A-Z]{4}$", max_length=20)
    message: str = Field(max_length=1000)
    name: str | None = Field(None, max_length=100)
    stack: str | None = Field(None, max_length=4000)
    component_stack: str | None = Field(None, max_length=4000)
    path: str = Field(max_length=300)
    request_id: str | None = Field(None, max_length=64, pattern=REQUEST_ID_PATTERN)
    app_version: str | None = Field(None, max_length=40)


def _clean(value: str | None) -> str | None:
    from gh.app import _redact_log

    return _redact_log(value) if value else value


async def _count(redis: Any, key: str) -> int:
    n = int(await redis.incr(key))
    if n == 1:
        await redis.expire(key, RATE_WINDOW_S)
    return n


async def _rate_limited(request: Request) -> bool:
    redis = getattr(request.app.state, "redis", None)
    if redis is None:
        return False
    try:
        if await _count(redis, GLOBAL_RATE_KEY) > GLOBAL_RATE_LIMIT:
            return True
        n = await _count(redis, RATE_KEY.format(client_ip(request) or "unknown"))
    except Exception:  # noqa: BLE001 — Redis lỗi: không chặn báo lỗi
        return False
    return n > RATE_LIMIT


async def _user_id(request: Request, db: Any) -> str | None:
    """Có phiên hợp lệ ⇒ id người dùng (để tra log). Không kiểm CSRF: chỉ đọc phiên, không đổi dữ liệu."""
    token = request.cookies.get(service.SESSION_COOKIE)
    if not token:
        return None
    try:
        user = await service.load_session(db, token)
        await db.commit()
    except Exception:  # noqa: BLE001
        await db.rollback()
        return None
    return str(user.id) if user else None


@router.post("/client-errors", status_code=202)
async def client_error(body: ClientErrorIn, request: Request, db: Any = DB) -> dict[str, Any]:
    actionlog.exempt()
    if await _rate_limited(request):
        raise ApiError(429, "CLIENT_ERRORS_RATE_LIMITED", "Gửi báo lỗi quá nhiều lần — thử lại sau một phút")
    extra: dict[str, Any] = {
        "client_error_id": body.error_id, "client_request_id": body.request_id, "path": body.path[:300],
        # `name`/`message` là thuộc tính dành riêng của LogRecord ⇒ khoá có tiền tố client_.
        "client_name": _clean(body.name), "client_message": _clean(body.message), "client_stack": _clean(body.stack),
        "client_component_stack": _clean(body.component_stack), "app_version": body.app_version,
    }
    uid = await _user_id(request, db)
    if uid:
        extra["user_id"] = uid
    log.warning("Lỗi giao diện %s", body.error_id, extra=extra)
    return {"ok": True, "request_id": request_id_var.get()}
