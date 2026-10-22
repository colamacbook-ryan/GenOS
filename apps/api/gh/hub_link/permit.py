"""Giấy phép ghi Kho (permit) — v0.1.50 (F-81, QD-18).

Luồng: Gen chỉ ĐỀ XUẤT; Sếp bấm Xác nhận + nhập PIN → `gh.gen.proposals.plan_call` (gọi trong `confirm_proposal`, SAU
khi qua kiểm quyền + PIN — không bao giờ ở bước dựng thẻ) ký MỘT permit gắn đúng đề xuất + tool + sha256 tham số →
`POST /hub/kho/write` → `hub_link.service.write_kho` kiểm lại. Không permit hợp lệ = không có lời gọi ghi nào tới
Gen-hub, kể cả khi ai đó gọi thẳng endpoint (Owner + PIN vẫn không đủ).

Tái dùng `gh.social.protocol.sign/verify` (HMAC-SHA256 trên JSON chuẩn hoá) nhưng khoá con RIÊNG: mục đích
`hub_write_permit` của khoá chủ — permit Facebook (khoá browser) và permit Kho không dùng chéo được. Hết hạn sau 5
phút; nonce dùng MỘT lần (Redis SET NX `gh:hub:permit:{nonce}`, giữ 10 phút).
"""

import hashlib
import hmac
import secrets
import time
from typing import Any

import orjson

from gh import crypto
from gh.social import protocol

PURPOSE = "hub_write_permit"
VERSION = 1
PERMIT_TTL_S = 300
NONCE_TTL_S = 600
NONCE_PREFIX = "gh:hub:permit:"


def args_sha256(args: Any) -> str:
    return hashlib.sha256(orjson.dumps(args, option=orjson.OPT_SORT_KEYS)).hexdigest()


def issue(org_id: Any, user_id: Any, proposal_id: Any, tool: str, args: dict[str, Any],
          now: int | None = None) -> dict[str, Any]:
    iat = int(time.time()) if now is None else int(now)
    claims = {"v": VERSION, "nonce": secrets.token_hex(16), "org_id": str(org_id), "confirmed_by": str(user_id),
              "proposal_id": str(proposal_id), "tool": tool, "args_sha256": args_sha256(args), "iat": iat,
              "exp": iat + PERMIT_TTL_S}
    return protocol.sign(crypto.master_key(), PURPOSE, claims)


async def verify(permit: Any, *, org_id: Any, user_id: Any, proposal_id: Any, tool: str, args: dict[str, Any],
                 redis: Any, now: int | None = None) -> str | None:
    """None = hợp lệ (và nonce đã bị tiêu); ngược lại PERMIT_MISSING | PERMIT_BAD_SIG | PERMIT_EXPIRED |
    PERMIT_MISMATCH | PERMIT_USED. Không có Redis ⇒ không bảo đảm được "dùng một lần" ⇒ từ chối (PERMIT_USED)."""
    if not isinstance(permit, dict) or not permit:
        return "PERMIT_MISSING"
    claims = protocol.verify(crypto.master_key(), PURPOSE, permit)
    if claims is None:
        return "PERMIT_BAD_SIG"
    ts = int(time.time()) if now is None else int(now)
    exp = claims.get("exp")
    if not isinstance(exp, int) or isinstance(exp, bool) or ts > exp:
        return "PERMIT_EXPIRED"
    expect = {"v": VERSION, "org_id": str(org_id), "confirmed_by": str(user_id), "proposal_id": str(proposal_id),
              "tool": tool, "args_sha256": args_sha256(args)}
    for k, v in expect.items():
        if not hmac.compare_digest(str(claims.get(k)), str(v)):
            return "PERMIT_MISMATCH"
    nonce = claims.get("nonce")
    if not isinstance(nonce, str) or not nonce or redis is None:
        return "PERMIT_USED"
    if not await redis.set(f"{NONCE_PREFIX}{nonce}", "1", nx=True, ex=NONCE_TTL_S):
        return "PERMIT_USED"
    return None
