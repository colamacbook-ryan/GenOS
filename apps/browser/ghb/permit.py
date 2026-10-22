"""Kiểm permit ghi (ký bằng khoá browser) TRƯỚC khi mở bất kỳ ngữ cảnh trình duyệt nào.

Sai bất kỳ điều kiện nào → JobError('PERMIT_INVALID'); không có đường ghi nào đi quanh hàm này.
"""

import hashlib
import time
from typing import Any

from redis.asyncio import Redis

from ghb import protocol
from ghb.adapters import ADAPTERS
from ghb.config import Config
from ghb.errors import JobError

NONCE_TTL_S = 3600
CLOCK_SKEW_S = 60


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


async def check(cfg: Config, redis: Redis, job: dict[str, Any]) -> None:
    p = job.get("payload") or {}
    permit = p.get("permit")
    if not permit:
        raise JobError("PERMIT_INVALID", "thiếu permit")
    claims = protocol.verify(cfg.key, protocol.P_PERMIT, permit)
    if claims is None:
        raise JobError("PERMIT_INVALID", "chữ ký")
    now = int(time.time())
    try:
        if int(claims["exp"]) < now:
            raise JobError("PERMIT_INVALID", "hết hạn")
        if int(claims["iat"]) > now + CLOCK_SKEW_S:
            raise JobError("PERMIT_INVALID", "iat tương lai")
    except (KeyError, TypeError, ValueError) as e:
        raise JobError("PERMIT_INVALID", "thời hạn") from e
    if (claims.get("job_id"), claims.get("org_id"), claims.get("account_id")) != (
            job.get("id"), job.get("org_id"), job.get("account_id")):
        raise JobError("PERMIT_INVALID", "không đúng việc")
    action = p.get("action")
    adapter = ADAPTERS.get(job.get("platform", ""))
    if (not isinstance(action, str) or claims.get("action") != action or adapter is None
            or action not in adapter.write_kinds):
        raise JobError("PERMIT_INVALID", "hành động")
    target, text = p.get("target_url"), p.get("text")
    if not isinstance(target, str) or not isinstance(text, str):
        raise JobError("PERMIT_INVALID", "thiếu nội dung")
    if claims.get("target_url_sha256") != _sha(target) or claims.get("body_sha256") != _sha(text):
        raise JobError("PERMIT_INVALID", "nội dung đã đổi")
    nonce = claims.get("nonce")
    if not isinstance(nonce, str) or not nonce:
        raise JobError("PERMIT_INVALID", "thiếu nonce")
    if await redis.set(protocol.PERMIT_NONCE_PREFIX + nonce, "1", nx=True, ex=NONCE_TTL_S) is None:
        raise JobError("PERMIT_INVALID", "nonce đã dùng")
