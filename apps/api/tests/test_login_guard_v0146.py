"""v0.1.46: giới hạn đăng nhập sai (10 lần / 15 phút theo email, 100 theo IP — chống dội), argon2 giả, fail-open khi
Redis lỗi."""

import hashlib
from typing import Any

import httpx
import pytest
from redis.asyncio import Redis
from redis.exceptions import ConnectionError as RedisConnectionError
from sqlalchemy import text

from gh.auth import login_guard
from gh.config import get_settings
from gh.db import admin_sessionmaker
from tests.conftest import OWNER, Api, verify_pin

IP_A = {"X-Forwarded-For": "203.0.113.7"}
IP_B = {"X-Forwarded-For": "203.0.113.8"}


async def _login(app: object, email: str, password: str, headers: dict[str, str] | None = None) -> httpx.Response:
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:  # type: ignore[arg-type]
        return await c.post("/api/v1/auth/login", json={"email": email, "password": password}, headers=headers or {})


async def _count(redis: Redis, key: str) -> int:
    v = await redis.get(key)
    return int(v) if v is not None else 0


async def test_eleventh_attempt_blocked_even_with_correct_password(owner_api: Api, app: object) -> None:
    for _ in range(10):
        assert (await _login(app, OWNER["email"], "sai-mat-khau", IP_A)).status_code == 401
    r = await _login(app, OWNER["email"], OWNER["password"], IP_A)
    assert r.status_code == 429, r.text
    body = r.json()
    assert body["code"] == "LOGIN_RATE_LIMITED" and body["retry_after_s"] > 0 and body["scope"] == "email"
    assert isinstance(body["detail"], str) and "~/.gen-harness/bin/genh reset-password" in body["detail"]


async def test_ip_limit_across_unknown_emails(owner_api: Api, app: object, monkeypatch: pytest.MonkeyPatch) -> None:
    """Ngưỡng IP (chống dội) vẫn chặn khi vượt — hạ ngưỡng xuống 12 để test nhanh (mặc định 100)."""
    monkeypatch.setattr(get_settings(), "login_ip_fail_limit", 12)
    for i in range(12):
        assert (await _login(app, f"khong{i}@example.vn", "x" * 12, IP_A)).status_code == 401
    r = await _login(app, "khong12@example.vn", "x" * 12, IP_A)
    assert r.status_code == 429 and r.json()["code"] == "LOGIN_RATE_LIMITED"
    assert r.json()["scope"] == "ip"
    # Câu cho scope=ip không hứa "nhờ Owner bấm Đặt lại mật khẩu" (không gỡ được bộ đếm IP chung).
    assert "Đặt lại mật khẩu" not in r.json()["detail"]
    assert "~/.gen-harness/bin/genh reset-password" in r.json()["detail"]
    assert "mật khẩu tạm MỚI" in r.json()["detail"]
    assert (await _login(app, "khong12@example.vn", "x" * 12, IP_B)).status_code == 401


async def test_shared_ip_failures_on_one_account_do_not_block_another(owner_api: Api, app: object) -> None:
    """Sau docker-proxy/Tailscale Serve mọi người chung một IP: 10 lần sai ở tài khoản A từ IP X không được chặn mật
    khẩu đúng của tài khoản B (Owner) từ chính IP X."""
    await verify_pin(owner_api)
    r = await owner_api.send("POST", "/users", {"display_name": "Lan", "email": "lan@example.vn", "role": "operator"})
    assert r.status_code == 201, r.text
    for _ in range(10):
        assert (await _login(app, "lan@example.vn", "sai-mat-khau", IP_A)).status_code == 401
    r = await _login(app, "lan@example.vn", "sai-mat-khau", IP_A)
    assert r.status_code == 429 and r.json()["scope"] == "email"
    assert (await _login(app, OWNER["email"], OWNER["password"], IP_A)).status_code == 200


async def test_counter_keys_always_have_ttl(owner_api: Api, app: object, redis: Redis) -> None:
    await _login(app, "ai-do@example.vn", "x" * 12, IP_A)
    for k in (login_guard.email_key("ai-do@example.vn"), login_guard.ip_key("203.0.113.7")):
        assert 0 < await redis.ttl(k) <= get_settings().login_fail_window_seconds


async def test_blocked_restores_missing_ttl(redis: Redis) -> None:
    """Khoá lỡ mất TTL (tiến trình chết giữa chừng ở bản cũ) không được khoá vĩnh viễn."""
    k = login_guard.email_key("mat-ttl@example.vn")
    await redis.set(k, 10)
    assert await redis.ttl(k) == -1
    hit = await login_guard.blocked(redis, None, "mat-ttl@example.vn")
    assert hit is not None and hit[1] == "email"
    assert 0 < await redis.ttl(k) <= get_settings().login_fail_window_seconds


async def test_email_limit_holds_across_ips(owner_api: Api, app: object) -> None:
    for _ in range(10):
        await _login(app, OWNER["email"], "sai-mat-khau", IP_A)
    r = await _login(app, OWNER["email"], OWNER["password"], IP_B)
    assert r.status_code == 429


async def test_success_clears_email_counter_not_ip(owner_api: Api, app: object, redis: Redis) -> None:
    for _ in range(5):
        assert (await _login(app, OWNER["email"], "sai-mat-khau", IP_A)).status_code == 401
    assert await _count(redis, login_guard.email_key(OWNER["email"])) == 5
    assert (await _login(app, OWNER["email"], OWNER["password"], IP_A)).status_code == 200
    assert await _count(redis, login_guard.email_key(OWNER["email"])) == 0
    assert await _count(redis, login_guard.ip_key("203.0.113.7")) == 5


async def test_unknown_email_still_runs_verify_secret(owner_api: Api, app: object,
                                                      monkeypatch: pytest.MonkeyPatch) -> None:
    from gh.auth import service

    calls: list[tuple[Any, ...]] = []
    real = service.verify_secret

    def spy(stored: str | None, value: str) -> bool:
        calls.append((stored, value))
        return real(stored, value)

    monkeypatch.setattr("gh.auth.service.verify_secret", spy)
    r = await _login(app, "khong-co@example.vn", "mat-khau-bat-ky-1")
    assert r.status_code == 401 and len(calls) == 1 and calls[0][0]


async def test_inactive_user_still_runs_verify_secret(owner_api: Api, app: object,
                                                      monkeypatch: pytest.MonkeyPatch) -> None:
    async with admin_sessionmaker()() as db:
        await db.execute(text("UPDATE core.users SET is_active = false"))
        await db.commit()
    calls: list[str] = []
    from gh.auth import service

    real = service.verify_secret

    def spy(stored: str | None, value: str) -> bool:
        calls.append(value)
        return real(stored, value)

    monkeypatch.setattr("gh.auth.service.verify_secret", spy)
    r = await _login(app, OWNER["email"], OWNER["password"])
    assert r.status_code == 401 and len(calls) == 1


async def test_redis_down_fails_open(owner_api: Api, app: object) -> None:
    class Broken:
        async def get(self, *a: Any, **k: Any) -> Any:
            raise RedisConnectionError("down")

        incr = ttl = expire = delete = set = get

        def pipeline(self, *a: Any, **k: Any) -> Any:
            raise RedisConnectionError("down")

    real = app.state.redis  # type: ignore[attr-defined]
    app.state.redis = Broken()  # type: ignore[attr-defined]
    try:
        assert (await _login(app, OWNER["email"], OWNER["password"])).status_code == 200
        assert (await _login(app, OWNER["email"], "sai-mat-khau")).status_code == 401
    finally:
        app.state.redis = real  # type: ignore[attr-defined]


async def test_redis_keys_hold_no_raw_email(owner_api: Api, app: object, redis: Redis) -> None:
    await _login(app, "Nguoi.La@Example.vn ", "x" * 12, IP_A)
    keys = [k.decode() for k in await redis.keys("gh:login:*")]
    assert keys and all("example" not in k.lower() and "nguoi" not in k.lower() for k in keys)
    digest = hashlib.sha256(b"nguoi.la@example.vn").hexdigest()[:32]
    assert f"gh:login:fail:email:{digest}" in keys


async def test_owner_reset_password_clears_staff_counter(owner_api: Api, app: object, redis: Redis) -> None:
    await verify_pin(owner_api)
    r = await owner_api.send("POST", "/users", {"display_name": "Lan", "email": "lan@example.vn", "role": "operator"})
    assert r.status_code == 201, r.text
    uid = r.json()["user"]["id"]
    for _ in range(3):
        await _login(app, "lan@example.vn", "sai-mat-khau", IP_A)
    assert await _count(redis, login_guard.email_key("lan@example.vn")) == 3
    r = await owner_api.send("POST", f"/users/{uid}/reset-password")
    assert r.status_code == 200, r.text
    assert await _count(redis, login_guard.email_key("lan@example.vn")) == 0


async def test_rate_limited_actionlog_single_row(owner_api: Api, app: object) -> None:
    for _ in range(10):
        await _login(app, OWNER["email"], "sai-mat-khau", IP_A)
    for _ in range(4):
        assert (await _login(app, OWNER["email"], "sai-mat-khau", IP_A)).status_code == 429
    async with admin_sessionmaker()() as db:
        n = (await db.execute(text("SELECT count(*) FROM ops.action_log WHERE action = 'auth.login_rate_limited'"))
             ).scalar()
    assert n == 1


async def test_genh_reset_password_unblocks_owner_shared_ip(owner_api: Api, app: object, redis: Redis,
                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    """Sau Tailscale Serve mọi người chung một IP: đủ lần sai của nhiều người chạm ngưỡng IP (chống dội) khoá luôn
    Owner — `genh reset-password` phải gỡ cả bộ đếm IP (không chỉ email Owner), không động tới khoá khác trong Redis."""
    from gh.auth.reset_owner import _clear_login_counter

    monkeypatch.setattr(get_settings(), "login_ip_fail_limit", 12)
    for i in range(12):
        assert (await _login(app, f"nv{i}@example.vn", "x" * 12, IP_A)).status_code == 401
    for _ in range(3):
        await _login(app, OWNER["email"], "sai-mat-khau", IP_B)
    assert (await _login(app, OWNER["email"], OWNER["password"], IP_A)).status_code == 429
    await redis.set("gh:khac:giu-nguyen", "1")
    await _clear_login_counter(OWNER["email"])
    assert await _count(redis, login_guard.ip_key("203.0.113.7")) == 0
    assert await _count(redis, login_guard.ip_key("203.0.113.8")) == 0
    assert await _count(redis, login_guard.email_key(OWNER["email"])) == 0
    assert await redis.get("gh:khac:giu-nguyen") == b"1"
    assert (await _login(app, OWNER["email"], OWNER["password"], IP_A)).status_code == 200


async def test_record_failure_restores_missing_ttl_atomically(redis: Redis) -> None:
    """Review v0.1.46 (nit): SET NX EX + INCR + EXPIRE NX trong một MULTI — khoá đếm lỡ mất TTL (bản cũ) được chốt lại
    TTL ngay lần sai kế tiếp, không đếm dồn qua nhiều ngày."""
    k = login_guard.email_key("dem-don@example.vn")
    await redis.set(k, 3)
    assert await redis.ttl(k) == -1
    await login_guard.record_failure(redis, None, "dem-don@example.vn")
    assert await _count(redis, k) == 4
    assert 0 < await redis.ttl(k) <= get_settings().login_fail_window_seconds


def _req(headers: dict[str, str], client: str = "172.18.0.1") -> Any:
    from starlette.requests import Request

    raw = [(k.lower().encode(), v.encode()) for k, v in headers.items()]
    return Request({"type": "http", "method": "POST", "path": "/", "headers": raw, "client": (client, 1234),
                    "query_string": b""})


@pytest.mark.parametrize(("mode", "bind", "headers", "want"), [
    # Cổng nghe 127.0.0.1 + Cloudflare: Cf-Connecting-IP là IP khách thật.
    ("cloudflare", "127.0.0.1", {"Cf-Connecting-IP": "198.51.100.9", "X-Forwarded-For": "172.18.0.1"},
     "198.51.100.9"),
    # Cloudflare nhưng Cf-Connecting-IP hỏng → phần tử PHẢI NHẤT chuỗi XFF gốc (proxy cục bộ nối vào).
    ("cloudflare", "127.0.0.1", {"Cf-Connecting-IP": "khong-phai-ip", "X-Gh-Upstream-Xff": "1.2.3.4, 198.51.100.9",
                                 "X-Forwarded-For": "172.18.0.1"}, "198.51.100.9"),
    # Tailscale: phần tử phải nhất (tailscaled nối vào); bên trái do khách tự gửi — không tin.
    ("tailscale", "127.0.0.1", {"X-Gh-Upstream-Xff": "10.9.9.9, 100.64.0.7", "X-Forwarded-For": "172.18.0.1"},
     "100.64.0.7"),
    # Tailscale KHÔNG tin Cf-Connecting-IP (người trong tailnet tự đặt được).
    ("tailscale", "127.0.0.1", {"Cf-Connecting-IP": "198.51.100.9", "X-Forwarded-For": "172.18.0.1"}, "172.18.0.1"),
    # Owner ngồi tại máy chủ (không header proxy) → IP Caddy báo.
    ("local", "127.0.0.1", {"X-Gh-Upstream-Xff": "", "X-Forwarded-For": "172.18.0.1"}, "172.18.0.1"),
    # LAN (0.0.0.0): ai trong mạng cũng gửi được header tuỳ ý → bỏ qua cả hai.
    ("lan", "0.0.0.0", {"Cf-Connecting-IP": "198.51.100.9", "X-Gh-Upstream-Xff": "198.51.100.9",
                        "X-Forwarded-For": "192.168.1.20"}, "192.168.1.20"),
    # Chạy ngoài compose (không có GH_BIND_ADDR) → như cũ.
    ("", "", {"Cf-Connecting-IP": "198.51.100.9", "X-Forwarded-For": "203.0.113.7"}, "203.0.113.7"),
])
def test_client_ip_trusts_local_proxy_only_when_bound_to_loopback(monkeypatch: pytest.MonkeyPatch, mode: str,
                                                                  bind: str, headers: dict[str, str],
                                                                  want: str) -> None:
    from gh.auth.deps import client_ip

    monkeypatch.setattr(get_settings(), "access_mode", mode)
    monkeypatch.setattr(get_settings(), "bind_addr", bind)
    monkeypatch.setattr(get_settings(), "trusted_proxies", "172.18.0.1")   # kết nối từ Caddy (review v0.1.63, P3)
    from gh.auth import deps

    deps.reset_trusted_proxy_cache()
    assert client_ip(_req(headers)) == want


async def test_cloudflare_stranger_cannot_lock_out_owner(owner_api: Api, app: object,
                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    """Review v0.1.46 (should-fix): chế độ Cloudflare, cổng 127.0.0.1 — người lạ trên Internet gửi đủ lần sai để chạm
    ngưỡng IP chỉ khoá IP của chính họ; Owner tại máy chủ (và người khác qua Cloudflare) vẫn đăng nhập được."""
    monkeypatch.setattr(get_settings(), "access_mode", "cloudflare")
    monkeypatch.setattr(get_settings(), "bind_addr", "127.0.0.1")
    monkeypatch.setattr(get_settings(), "login_ip_fail_limit", 12)
    gw = {"X-Forwarded-For": "172.18.0.1"}  # Caddy luôn ghi đè XFF bằng IP gateway docker-proxy
    attacker = gw | {"Cf-Connecting-IP": "198.51.100.66", "X-Gh-Upstream-Xff": "198.51.100.66"}
    for i in range(12):
        assert (await _login(app, f"do{i}@example.vn", "x" * 12, attacker)).status_code == 401
    r = await _login(app, "do12@example.vn", "x" * 12, attacker)
    assert r.status_code == 429 and r.json()["scope"] == "ip"
    assert (await _login(app, OWNER["email"], OWNER["password"], gw)).status_code == 200
    other = gw | {"Cf-Connecting-IP": "203.0.113.50", "X-Gh-Upstream-Xff": "203.0.113.50"}
    assert (await _login(app, OWNER["email"], OWNER["password"], other)).status_code == 200
