"""Giới hạn đăng nhập sai (v0.1.46), đếm bằng Redis trong cửa sổ `login_fail_window_seconds` (15 phút):

- theo email: tối đa `login_fail_limit` (10) lần sai — lớp chính, chặn dò mật khẩu một tài khoản từ mọi nơi;
- theo IP: tối đa `login_ip_fail_limit` (100) lần sai — chỉ là chống dội (flood guard).

Quá ngưỡng → 429 LOGIN_RATE_LIMITED, kể cả khi mật khẩu đúng; `blocked()` trả kèm `scope` ("email" | "ip") để màn
đăng nhập nói đúng cách gỡ.

Vì sao ngưỡng IP cao hơn hẳn: ở chế độ local/tailscale/cloudflare cổng chỉ nghe 127.0.0.1 — mọi người (Tailscale
Serve, cloudflared, Owner ngồi tại máy chủ) tới Caddy qua docker-proxy từ CÙNG một IP gateway, nên bộ đếm IP thực
chất là bộ đếm chung cả tổ chức. Từ v0.1.46 (sau review) `client_ip` lấy IP thật do proxy cục bộ báo khi cổng nghe
127.0.0.1 (Cf-Connecting-IP ở chế độ cloudflare, phần tử phải nhất X-Forwarded-For của tailscaled — gh/auth/deps.py),
nên người lạ trên Internet chỉ khoá được IP của chính họ; ngưỡng cao vẫn giữ cho trường hợp còn chung IP (Owner ngồi
tại máy chủ, LAN qua Docker rootless/Docker Desktop). 10 lần sai ở tài khoản A từ IP X KHÔNG được chặn mật khẩu đúng
của tài khoản B từ IP X.

Khoá: `gh:login:fail:ip:<ip>` và `gh:login:fail:email:<sha256(email chuẩn hoá)[:32]>` — không lưu email thô.
Mật khẩu đúng chỉ xoá bộ đếm email (`clear_email`), KHÔNG xoá bộ đếm IP: kẻ có một tài khoản thật không được tự
xoá bộ đếm IP của mình để dò tiếp tài khoản khác. Owner gỡ khoá bằng `genh reset-password` (xoá bộ đếm email Owner
VÀ mọi bộ đếm IP — `clear_for_owner_reset`) hoặc đợi hết cửa sổ. Owner bấm "Đặt lại mật khẩu" cho nhân viên chỉ xoá
bộ đếm email của nhân viên đó.

Mỗi khoá luôn có TTL: `SET NX EX` + `INCR` + `EXPIRE NX` trong một MULTI/EXEC (không thể hết hạn chen giữa, tiến
trình chết giữa chừng không để lại khoá vĩnh viễn); khoá nào lỡ mất TTL (-1, bản cũ) thì `blocked()` đặt lại.

Redis lỗi → (v0.1.75) lớp lùi: bộ đếm trong bộ nhớ TIẾN TRÌNH (cùng ngưỡng/cửa sổ, mất khi khởi động lại, không chia
sẻ giữa các worker — chỉ để dò mật khẩu không thành miễn phí khi Redis chết). Log một dòng, không kèm email.

Thiết bị tin cậy (v0.1.75): đăng nhập đúng được cookie `gh_device` ký HMAC (khoá master, gắn với user id +
băm password_hash hiện tại ⇒ đổi/đặt lại mật khẩu thu hồi mọi cookie cũ, hạn 30 ngày).
Email đang bị khoá nhưng trình duyệt có cookie hợp lệ của CHÍNH tài khoản đó → được ngưỡng email nới (20 thay vì
10; vẫn phải đúng mật khẩu, vẫn chịu ngưỡng IP) ⇒ người lạ biết email Owner không khoá được Owner ra ngoài.

Tài khoản (v0.1.75): `/account/password`, `/account/pin` có cookie phiên cũng đếm sai theo user_id
(`account_pw_fail_limit`, mặc định 5 / cửa sổ trên) — vượt ngưỡng thu hồi chính phiên đó.

TOTP/2FA: để sau (chưa làm trong đợt này).
"""

import hashlib
import hmac
import logging
import time
import uuid
from typing import Any

from redis.exceptions import RedisError

from gh import crypto
from gh.config import get_settings

log = logging.getLogger("gh.auth.login_guard")

_FAIL_OPEN = (RedisError, OSError)

DEVICE_COOKIE = "gh_device"
DEVICE_TTL_S = 30 * 24 * 3600

# Lớp lùi khi Redis lỗi: khoá -> (số lần, hạn theo time.monotonic()).
_mem: dict[str, tuple[int, float]] = {}
_MEM_MAX = 20000


def reset_memory() -> None:
    _mem.clear()


def _mem_get(k: str) -> tuple[int, int]:
    """(số lần, giây còn lại) — hết hạn thì xoá."""
    v = _mem.get(k)
    if v is None:
        return 0, 0
    left = v[1] - time.monotonic()
    if left <= 0:
        _mem.pop(k, None)
        return 0, 0
    return v[0], max(1, int(left))


def _mem_incr(k: str, window: int) -> int:
    if len(_mem) >= _MEM_MAX:
        now = time.monotonic()
        for dead in [x for x, (_, exp) in _mem.items() if exp <= now]:
            _mem.pop(dead, None)
        if len(_mem) >= _MEM_MAX:  # vẫn đầy (bị dội) — bỏ hết còn hơn phình bộ nhớ
            _mem.clear()
    n, left = _mem_get(k)
    _mem[k] = (n + 1, time.monotonic() + (left if n else window))
    return n + 1


def _device_sig(user_id: uuid.UUID | str, cred: str, exp: int) -> str:
    """Chữ ký gắn với user id VÀ phiên bản thông tin đăng nhập (băm của password_hash hiện tại): đổi/đặt lại mật khẩu
    (kể cả `genh reset-password`, Owner đặt lại cho nhân viên) ⇒ password_hash đổi ⇒ mọi cookie cũ mất hiệu lực."""
    cred_h = hashlib.sha256(cred.encode()).hexdigest()
    return crypto.keyed_digest(b"login-device-v2", f"{user_id}:{cred_h}:{exp}".encode()).hex()


def device_token(user_id: uuid.UUID | str, cred: str) -> str:
    exp = int(time.time()) + DEVICE_TTL_S
    return f"{exp}.{_device_sig(user_id, cred, exp)}"


def device_ok(token: str | None, user_id: uuid.UUID | str, cred: str) -> bool:
    """Cookie thiết bị tin cậy còn hạn, đúng chữ ký, đúng user này và đúng mật khẩu HIỆN TẠI của user."""
    if not token or "." not in token:
        return False
    exp_s, _, sig = token.partition(".")
    if not exp_s.isdigit() or int(exp_s) < time.time():
        return False
    return hmac.compare_digest(_device_sig(user_id, cred, int(exp_s)), sig)


def _email_hash(email: str) -> str:
    return hashlib.sha256(email.strip().lower().encode()).hexdigest()[:32]


def email_key(email: str) -> str:
    return "gh:login:fail:email:" + _email_hash(email)


def ip_key(ip: str) -> str:
    return "gh:login:fail:ip:" + ip[:64]


def _limits(ip: str | None, email: str) -> list[tuple[str, str, int]]:
    """(khoá, scope, ngưỡng) — IP trước để khi cả hai cùng chạm ngưỡng thì báo scope "ip" (lời khuyên an toàn hơn)."""
    s = get_settings()
    out: list[tuple[str, str, int]] = []
    if ip:
        out.append((ip_key(ip), "ip", s.login_ip_fail_limit))
    out.append((email_key(email), "email", s.login_fail_limit))
    return out


def keys(ip: str | None, email: str) -> list[str]:
    return [k for k, _, _ in _limits(ip, email)]


async def _blocked_limits(redis: Any, limits: list[tuple[str, str, int]]) -> tuple[int, str] | None:
    window = get_settings().login_fail_window_seconds
    try:
        worst: tuple[int, str] | None = None
        for k, scope, limit in limits:
            raw = await redis.get(k)
            if raw is not None and int(raw) >= limit:
                ttl = int(await redis.ttl(k))
                if ttl == -1:  # khoá lỡ mất TTL — đặt lại để không khoá vĩnh viễn
                    await redis.expire(k, window)
                left = max(1, ttl if ttl > 0 else window)
                if worst is None:
                    worst = (left, scope)
                else:
                    worst = (max(worst[0], left), worst[1])
        return worst
    except _FAIL_OPEN as e:
        log.warning("Redis lỗi khi kiểm giới hạn đăng nhập — dùng bộ đếm trong bộ nhớ (%s)", type(e).__name__)
        worst = None
        for k, scope, limit in limits:
            n, left = _mem_get(k)
            if n >= limit:
                worst = (left, scope) if worst is None else (max(worst[0], left), worst[1])
        return worst


async def blocked(redis: Any, ip: str | None, email: str, *, trusted_device: bool = False) -> tuple[int, str] | None:
    """(số giây còn lại — tối thiểu 1, scope "ip" | "email") nếu IP hoặc email đã chạm ngưỡng; không thì None.
    `trusted_device=True` (cookie thiết bị hợp lệ): ngưỡng email nới lên `login_trusted_email_fail_limit`, vẫn áp
    ngưỡng IP."""
    limits = _limits(ip, email)
    if trusted_device:  # ngưỡng email nới ra (không bỏ): cookie bị lộ không thành đường dò mật khẩu vô hạn
        limits = [(k, sc, get_settings().login_trusted_email_fail_limit if sc == "email" else lim)
                  for k, sc, lim in limits]
    return await _blocked_limits(redis, limits)


async def _incr_keys(redis: Any, ks: list[str]) -> list[int]:
    """Tăng các khoá (MULTI/EXEC) — trả số đếm mới theo thứ tự `ks`. Redis lỗi → đếm trong bộ nhớ."""
    window = get_settings().login_fail_window_seconds
    try:
        # Một MULTI/EXEC cho mọi khoá: SET NX EX + INCR + EXPIRE NX chạy liền khối — khoá không thể hết hạn giữa SET và
        # INCR (INCR tạo lại khoá KHÔNG TTL ⇒ đếm dồn nhiều ngày); EXPIRE NX (Redis ≥ 7) chốt lại TTL nếu vẫn thiếu.
        async with redis.pipeline(transaction=True) as pipe:
            for k in ks:
                pipe.set(k, 0, nx=True, ex=window)
                pipe.incr(k)
                pipe.expire(k, window, nx=True)
            res = await pipe.execute()
        return [int(res[i * 3 + 1]) for i in range(len(ks))]
    except _FAIL_OPEN as e:
        log.warning("Redis lỗi khi ghi lần đăng nhập sai — dùng bộ đếm trong bộ nhớ (%s)", type(e).__name__)
        return [_mem_incr(k, window) for k in ks]


async def record_failure(redis: Any, ip: str | None, email: str) -> None:
    await _incr_keys(redis, keys(ip, email))


# ─── Đếm sai mật khẩu ở /account (cookie phiên) theo user_id ────────────────

def user_key(user_id: uuid.UUID) -> str:
    return "gh:account:fail:user:" + str(user_id)


async def user_blocked(redis: Any, user_id: uuid.UUID) -> int | None:
    """Số giây còn lại nếu user đã chạm ngưỡng sai mật khẩu ở /account; không thì None."""
    hit = await _blocked_limits(redis, [(user_key(user_id), "user", get_settings().account_pw_fail_limit)])
    return hit[0] if hit else None


async def reserve_user_attempt(redis: Any, user_id: uuid.UUID) -> int:
    """ĐẶT CHỖ một lần thử TRƯỚC khi verify (nguyên tử: MULTI/EXEC SET NX EX + INCR + EXPIRE NX; Redis lỗi → bộ nhớ
    tiến trình, không có `await` giữa đọc và ghi nên nguyên tử trong asyncio). Trả số đếm mới: > ngưỡng ⇒ từ chối,
    không verify. Nhiều request song song không vượt ngưỡng được vì mỗi request nhận một số đếm riêng."""
    return (await _incr_keys(redis, [user_key(user_id)]))[0]


async def clear_user(redis: Any, user_id: uuid.UUID) -> None:
    _mem.pop(user_key(user_id), None)
    try:
        await redis.delete(user_key(user_id))
    except _FAIL_OPEN as e:
        log.warning("Redis lỗi khi xoá bộ đếm tài khoản (%s)", type(e).__name__)


# ─── Khoá PIN tăng dần ──────────────────────────────────────────────────────

PIN_LEVEL_TTL_S = 7 * 24 * 3600


def pin_level_key(user_id: uuid.UUID) -> str:
    return "gh:pin:lock-level:" + str(user_id)


async def bump_pin_lock_level(redis: Any, user_id: uuid.UUID) -> int:
    """Số lần PIN bị khoá liên tiếp (1, 2, 3…; hết sau 7 ngày không bị khoá lại hoặc khi nhập đúng PIN)."""
    k = pin_level_key(user_id)
    try:
        n = int(await redis.incr(k))
        await redis.expire(k, PIN_LEVEL_TTL_S)
        return n
    except _FAIL_OPEN as e:
        log.warning("Redis lỗi khi đếm cấp khoá PIN — dùng bộ nhớ (%s)", type(e).__name__)
        return _mem_incr(k, PIN_LEVEL_TTL_S)


async def clear_pin_lock_level(redis: Any, user_id: uuid.UUID) -> None:
    k = pin_level_key(user_id)
    _mem.pop(k, None)
    try:
        await redis.delete(k)
    except _FAIL_OPEN as e:
        log.warning("Redis lỗi khi xoá cấp khoá PIN (%s)", type(e).__name__)


async def clear_email(redis: Any, email: str) -> None:
    """Xoá bộ đếm theo email (KHÔNG xoá khoá IP). Redis lỗi chỉ log."""
    _mem.pop(email_key(email), None)
    try:
        await redis.delete(email_key(email))
    except _FAIL_OPEN as e:
        log.warning("Redis lỗi khi xoá bộ đếm đăng nhập (%s)", type(e).__name__)


async def clear_for_owner_reset(redis: Any, email: str) -> None:
    """`genh reset-password` (chạy trên máy chủ ⇒ Owner thật): xoá bộ đếm email Owner và MỌI bộ đếm IP — sau
    Tailscale Serve/Docker rootless mọi người chung một IP nguồn, chỉ xoá email thì Owner vẫn bị khoá theo IP.
    Redis lỗi chỉ log."""
    for k in [k for k in _mem if k.startswith("gh:login:fail:ip:")] + [email_key(email)]:
        _mem.pop(k, None)
    try:
        doomed = [email_key(email)]
        async for k in redis.scan_iter(match="gh:login:fail:ip:*", count=500):
            doomed.append(k)
        await redis.delete(*doomed)
    except _FAIL_OPEN as e:
        log.warning("Redis lỗi khi gỡ khoá đăng nhập (%s)", type(e).__name__)


async def log_once(redis: Any, email: str) -> bool:
    """True nếu đây là lần chặn đầu trong cửa sổ (để chỉ ghi Action Log một dòng). Redis lỗi → False."""
    try:
        ok = await redis.set("gh:login:blocked-log:" + _email_hash(email), "1", nx=True,
                             ex=get_settings().login_fail_window_seconds)
        return bool(ok)
    except _FAIL_OPEN:
        return False
