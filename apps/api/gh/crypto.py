"""Băm mật khẩu/PIN (argon2id), token ngẫu nhiên, mã hoá phong bì AES-256-GCM cho bí mật."""

import base64
import binascii
import hashlib
import hmac
import os
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from gh.config import get_settings

_hasher = PasswordHasher()
_dev_master_key: bytes | None = None


def hash_secret(value: str) -> str:
    return _hasher.hash(value)


def verify_secret(stored_hash: str | None, value: str) -> bool:
    if not stored_hash:
        return False
    try:
        return _hasher.verify(stored_hash, value)
    except (VerificationError, InvalidHashError):
        return False


def new_token(nbytes: int = 32) -> str:
    return secrets.token_urlsafe(nbytes)


# v0.1.28 (UX V4): mật khẩu tạm đọc/gõ lại được — bỏ ký tự dễ nhầm (0/O/o, 1/l/I, 5/S…), chia nhóm bằng "-".
TEMP_PASSWORD_ALPHABET = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKMNPQRTUVWXYZ2346789"


def temp_password(groups: int = 3, size: int = 4) -> str:
    """Mật khẩu tạm dạng `xxxx-xxxx-xxxx` (≈ 68 bit) — không có ký tự dễ nhìn nhầm khi Owner đọc cho nhân viên."""
    return "-".join("".join(secrets.choice(TEMP_PASSWORD_ALPHABET) for _ in range(size)) for _ in range(groups))


def token_digest(token: str) -> bytes:
    """Băm token phiên/CSRF/setup để lưu DB (không lưu token gốc)."""
    return hashlib.sha256(token.encode()).digest()


def decode_key(raw: str) -> bytes:
    """Giải mã khoá 32 byte dạng hex (64 ký tự — cách `genh` sinh ở
    secretgen.randomHex) hoặc base64 (cách `make secrets` sinh cho dev).

    Trước đây chỉ nhận base64 nên mọi bản cài bằng genh (khoá hex) hỏng ở lần
    đầu mã hoá/giải mã (vd `genh backup`: "GH_MASTER_KEY phải là 32 byte
    base64" — phát hiện ở e2e cài thật). Nhận cả hai để bản cài cũ không phải
    đổi khoá (đổi khoá = mất mọi dữ liệu đã mã hoá).
    """
    raw = raw.strip()
    if len(raw) == 64:
        try:
            return bytes.fromhex(raw)
        except ValueError:
            pass
    try:
        key = base64.b64decode(raw, validate=True)
    except (ValueError, binascii.Error) as e:
        raise ValueError("khoá phải là 32 byte dạng hex (64 ký tự) hoặc base64") from e
    if len(key) != 32:
        raise ValueError("khoá phải là 32 byte dạng hex (64 ký tự) hoặc base64")
    return key


def master_key() -> bytes:
    global _dev_master_key
    s = get_settings()
    raw = s.master_key
    if s.master_key_file:
        with open(s.master_key_file, encoding="utf-8") as f:
            raw = f.read().strip()
    if raw:
        return decode_key(raw)
    if s.is_production:
        raise RuntimeError("Thiếu GH_MASTER_KEY ở production")
    if _dev_master_key is None:
        _dev_master_key = os.urandom(32)
    return _dev_master_key


def encrypt(plaintext: bytes, associated: bytes = b"", *, key: bytes | None = None) -> bytes:
    """Mã hoá phong bì: khoá dữ liệu ngẫu nhiên mã hoá nội dung, khoá master mã hoá khoá dữ liệu.

    Định dạng: b"GH1" | nonce_k(12) | enc_dek(48) | nonce_d(12) | ciphertext

    `key`: khoá master tường minh (32 byte) thay cho `master_key()` của cấu hình hiện hành — chỉ dùng khi
    `gh.bundle` cần mã hoá lại bí mật bằng khoá master của MÁY KHÁC (nhập gói hồ sơ vào máy mới), mọi lời gọi
    khác trong hệ thống đều bỏ trống để dùng đúng khoá master đang cấu hình.
    """
    mk = key if key is not None else master_key()
    dek = AESGCM.generate_key(bit_length=256)
    nonce_k, nonce_d = os.urandom(12), os.urandom(12)
    enc_dek = AESGCM(mk).encrypt(nonce_k, dek, b"dek")
    ct = AESGCM(dek).encrypt(nonce_d, plaintext, associated)
    return b"GH1" + nonce_k + enc_dek + nonce_d + ct


def decrypt(blob: bytes, associated: bytes = b"", *, key: bytes | None = None) -> bytes:
    """`key`: xem docstring `encrypt` — dùng để giải mã bằng khoá master CŨ đi kèm trong gói hồ sơ."""
    if blob[:3] != b"GH1":
        raise ValueError("Định dạng bí mật không hợp lệ")
    mk = key if key is not None else master_key()
    nonce_k, enc_dek, nonce_d, ct = blob[3:15], blob[15:63], blob[63:75], blob[75:]
    dek = AESGCM(mk).decrypt(nonce_k, enc_dek, b"dek")
    return AESGCM(dek).decrypt(nonce_d, ct, associated)


_dev_bridge_key: bytes | None = None


def bridge_key() -> bytes:
    """Khoá chung với bridge — tách khỏi khoá master để bridge không bao giờ giữ khoá master."""
    global _dev_bridge_key
    s = get_settings()
    raw = s.bridge_key
    if s.bridge_key_file:
        with open(s.bridge_key_file, encoding="utf-8") as f:
            raw = f.read().strip()
    if raw:
        return decode_key(raw)
    if s.is_production:
        raise RuntimeError("Thiếu GH_BRIDGE_KEY ở production")
    if _dev_bridge_key is None:
        _dev_bridge_key = os.urandom(32)
    return _dev_bridge_key


def _bridge_subkey(purpose: bytes) -> bytes:
    return hashlib.sha256(bridge_key() + purpose).digest()


def hmac_sign(message: bytes) -> bytes:
    """Chữ ký permit (bridge kiểm bằng cùng khoá con)."""
    return hmac.new(_bridge_subkey(b"permit"), message, hashlib.sha256).digest()


def hmac_verify(message: bytes, signature: bytes) -> bool:
    return hmac.compare_digest(hmac_sign(message), signature)


def keyed_digest(purpose: bytes, message: bytes) -> bytes:
    """HMAC-SHA256 bằng khoá con của khoá MASTER (tách theo `purpose`) — cho băm của giá trị ngắn/đoán được (mã 6 số):
    lộ DB mà thiếu khoá master thì không dò được. Khoá master không rời máy chủ (bridge không giữ)."""
    subkey = hashlib.sha256(master_key() + b"gh-keyed-digest:" + purpose).digest()
    return hmac.new(subkey, message, hashlib.sha256).digest()


def transport_encrypt(plaintext: bytes, aad: str) -> str:
    nonce = os.urandom(12)
    ct = AESGCM(_bridge_subkey(b"transport")).encrypt(nonce, plaintext, aad.encode())
    return base64.b64encode(nonce + ct).decode()


def transport_decrypt(blob: str, aad: str) -> bytes:
    raw = base64.b64decode(blob)
    return AESGCM(_bridge_subkey(b"transport")).decrypt(raw[:12], raw[12:], aad.encode())


_dev_browser_key: bytes | None = None


def browser_key() -> bytes:
    """Khoá chung với browser-worker (v0.1.29) — tách khỏi khoá master và khoá bridge: worker trình duyệt chỉ giữ khoá
    này, không bao giờ giữ khoá master (giải mã phiên đã lưu) hay khoá bridge (ký permit gửi tin Zalo/WhatsApp)."""
    global _dev_browser_key
    s = get_settings()
    raw = s.browser_key
    if s.browser_key_file:
        with open(s.browser_key_file, encoding="utf-8") as f:
            raw = f.read().strip()
    if raw:
        return decode_key(raw)
    if s.is_production:
        raise RuntimeError("Thiếu GH_BROWSER_KEY ở production")
    if _dev_browser_key is None:
        _dev_browser_key = os.urandom(32)
    return _dev_browser_key
