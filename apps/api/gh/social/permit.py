"""Giấy phép gửi (permit) — thao tác GHI trên mạng xã hội (trả lời bình luận, nhắn tin).

Luồng (docs/design/gen-browser-agent.md §3.5): Gen chỉ ĐỀ XUẤT; Owner bấm Xác nhận + nhập PIN →
`social.service.request_write` tạo việc rồi `issue()` ký permit bằng khoá browser, gắn vào đúng việc đó. Worker kiểm
permit TRƯỚC khi mở trình duyệt: chữ ký đúng, chưa hết hạn, khớp job/tài khoản/hành động, hash đích + nội dung
khớp, nonce dùng một lần (SET NX ở Redis của worker). Không permit = không ghi, kể cả khi model bị lừa.

`verify()` ở đây là bản tham chiếu cho test và cho `apps/browser/ghb/permit.py` — KHÔNG kiểm nonce (nonce do worker).
"""

import hashlib
import hmac
import secrets
import time
from typing import Any

from gh import crypto
from gh.social import protocol

WRITE_KINDS = ("reply_comment", "send_message")
PROPOSAL_ACTION = {"social_reply": "reply_comment", "social_dm": "send_message"}
PERMIT_TTL_S = 300


def _sha(v: str) -> str:
    return hashlib.sha256(v.encode()).hexdigest()


def issue(*, job_id: Any, org_id: Any, account_id: Any, action: str, target_url: str, text: str, confirmed_by: Any,
          now: int | None = None) -> dict[str, Any]:
    if action not in WRITE_KINDS:
        raise ValueError("action không hợp lệ")
    iat = int(time.time()) if now is None else int(now)
    claims = {"v": protocol.VERSION, "nonce": secrets.token_hex(16), "job_id": str(job_id), "org_id": str(org_id),
              "account_id": str(account_id), "action": action, "target_url_sha256": _sha(target_url),
              "body_sha256": _sha(text), "iat": iat, "exp": iat + PERMIT_TTL_S, "confirmed_by": str(confirmed_by)}
    return protocol.sign(crypto.browser_key(), protocol.P_PERMIT, claims)


def verify(permit: Any, *, job_id: Any, org_id: Any, account_id: Any, action: str, target_url: str, text: str,
           now: int | None = None) -> str | None:
    """None = hợp lệ; ngược lại mã lỗi PERMIT_MISSING | PERMIT_BAD_SIG | PERMIT_EXPIRED | PERMIT_MISMATCH."""
    if not isinstance(permit, dict) or not permit:
        return "PERMIT_MISSING"
    claims = protocol.verify(crypto.browser_key(), protocol.P_PERMIT, permit)
    if claims is None:
        return "PERMIT_BAD_SIG"
    ts = int(time.time()) if now is None else int(now)
    exp = claims.get("exp")
    if not isinstance(exp, int) or isinstance(exp, bool) or ts > exp:
        return "PERMIT_EXPIRED"
    expect = {"v": protocol.VERSION, "job_id": str(job_id), "org_id": str(org_id), "account_id": str(account_id),
              "action": action, "target_url_sha256": _sha(target_url), "body_sha256": _sha(text)}
    for k, v in expect.items():
        if not hmac.compare_digest(str(claims.get(k)), str(v)):
            return "PERMIT_MISMATCH"
    return None
