"""Giao thức api ↔ browser-worker qua Redis (docs/api/browser-protocol.md) — BẢN SAO có chủ ý của
`apps/api/gh/social/protocol.py` (worker là ảnh riêng, không có mã `gh`, không DB). Hai bên kiểm cùng một bộ vectơ thử
(`apps/api/tests/test_social.py::test_protocol_vectors` và `apps/browser/tests/test_protocol.py`) để không lệch nhau.

- Mọi thông điệp (việc, kết quả, lệnh điều khiển, sự kiện chuột/phím) là JSON ký HMAC-SHA256 bằng khoá con
  `sha256(browser_key ‖ "gh-browser:" ‖ mục đích)`; chuỗi ký = JSON chuẩn hoá (khoá sắp xếp, không khoảng trắng, UTF-8)
  của mọi trường trừ `sig`. Sai chữ ký / hết hạn / nonce dùng lại → bỏ, không làm gì.
- Phiên đăng nhập (storageState) đi qua Redis dạng `AES-256-GCM(khoá con "transport")`, AAD = `<org_id>:<account_id>`.
"""

import base64
import hashlib
import hmac
import os
from typing import Any

import orjson
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

VERSION = 1
JOBS_STREAM = "gh:browser:jobs"
RESULTS_STREAM = "gh:browser:results"
CONTROL_CHANNEL = "gh:browser:control"
HALT_KEY = "gh:browser:halt"
HEARTBEAT_KEY = "gh:browser:heartbeat"
LOCK_PREFIX = "gh:browser:lock:"
NONCE_PREFIX = "gh:browser:nonce:"
# api đặt khi huỷ một việc (Owner tạm dừng/gỡ tài khoản, rút đồng ý gửi): việc còn nằm trong hàng đợi thì worker
# chưa có nó trong `running` nên lệnh pub/sub `cancel` bị bỏ qua — worker kiểm khoá này trước khi chạy và
# ngay trước khi gửi.
CANCELLED_PREFIX = "gh:browser:cancelled:"
PERMIT_NONCE_PREFIX = "gh:browser:permit:"
FRAMES_PREFIX = "gh:browser:frames:"
INPUT_PREFIX = "gh:browser:input:"
RESULTS_GROUP = "api"

P_JOB, P_RESULT, P_CONTROL, P_INPUT, P_FRAME, P_TRANSPORT = "job", "result", "control", "input", "frame", "transport"
P_PERMIT = "permit"


def subkey(key: bytes, purpose: str) -> bytes:
    return hashlib.sha256(key + b"gh-browser:" + purpose.encode()).digest()


def canonical(obj: dict[str, Any]) -> bytes:
    body = {k: v for k, v in obj.items() if k != "sig"}
    return orjson.dumps(body, option=orjson.OPT_SORT_KEYS)


def _b64u(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def signature(key: bytes, purpose: str, obj: dict[str, Any]) -> str:
    return _b64u(hmac.new(subkey(key, purpose), canonical(obj), hashlib.sha256).digest())


def sign(key: bytes, purpose: str, obj: dict[str, Any]) -> dict[str, Any]:
    body = {k: v for k, v in obj.items() if k != "sig"}
    return {**body, "sig": signature(key, purpose, body)}


def verify(key: bytes, purpose: str, obj: Any) -> dict[str, Any] | None:
    if not isinstance(obj, dict) or not isinstance(obj.get("sig"), str):
        return None
    if not hmac.compare_digest(signature(key, purpose, obj), obj["sig"]):
        return None
    return {k: v for k, v in obj.items() if k != "sig"}


def seal(key: bytes, plaintext: bytes, aad: str) -> str:
    nonce = os.urandom(12)
    ct = AESGCM(subkey(key, P_TRANSPORT)).encrypt(nonce, plaintext, aad.encode())
    return base64.b64encode(nonce + ct).decode()


def unseal(key: bytes, blob: str, aad: str) -> bytes:
    raw = base64.b64decode(blob)
    return AESGCM(subkey(key, P_TRANSPORT)).decrypt(raw[:12], raw[12:], aad.encode())


def account_aad(org_id: Any, account_id: Any) -> str:
    return f"{org_id}:{account_id}"
