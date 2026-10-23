"""v0.1.75 (review bảo mật 22/10): đếm sai mật khẩu ở /account, thiết bị tin cậy, khoá PIN tăng dần, đổi PIN đóng mọi
cửa sổ PIN, bộ đếm lùi trong bộ nhớ khi Redis lỗi."""

import time
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import httpx
import pytest
from redis.asyncio import Redis
from redis.exceptions import ConnectionError as RedisConnectionError
from sqlalchemy import text

from gh.auth import login_guard, service
from gh.config import get_settings
from gh.db import admin_sessionmaker
from tests.conftest import OWNER, Api, verify_pin

IP_A = {"X-Forwarded-For": "203.0.113.7"}
IP_B = {"X-Forwarded-For": "203.0.113.8"}
NEW_PW = "mat-khau-moi-rat-dai-456"


class BrokenRedis:
    async def get(self, *a: Any, **k: Any) -> Any:
        raise RedisConnectionError("down")

    incr = ttl = expire = delete = set = get

    def pipeline(self, *a: Any, **k: Any) -> Any:
        raise RedisConnectionError("down")


@pytest.fixture(autouse=True)
def _clean_memory() -> None:
    login_guard.reset_memory()


async def _login(
    app: object,
    email: str,
    password: str,
    headers: dict[str, str] | None = None,
    client: httpx.AsyncClient | None = None,
) -> httpx.Response:
    if client is not None:
        return await client.post(
            "/api/v1/auth/login", json={"email": email, "password": password}, headers=headers or {}
        )
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:  # type: ignore[arg-type]
        return await c.post("/api/v1/auth/login", json={"email": email, "password": password}, headers=headers or {})


async def _second_session(app: object) -> tuple[httpx.AsyncClient, Api]:
    c = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")  # type: ignore[arg-type]
    api = Api(c)
    r = await api.send("POST", "/auth/login", {"email": OWNER["email"], "password": OWNER["password"]})
    assert r.status_code == 200, r.text
    return c, api


# ─── Không cần Postgres / Redis ─────────────────────────────────────────────────────────────────


def test_device_token_gan_voi_user_phien_ban_mat_khau_va_co_han(monkeypatch: pytest.MonkeyPatch) -> None:
    uid, other = uuid4(), uuid4()
    tok = login_guard.device_token(uid, "hash-cu")
    assert login_guard.device_ok(tok, uid, "hash-cu")
    assert not login_guard.device_ok(tok, other, "hash-cu")  # user khác
    assert not login_guard.device_ok(tok, uid, "hash-moi")  # đổi/đặt lại mật khẩu → cookie cũ vô hiệu
    assert not login_guard.device_ok(None, uid, "hash-cu")
    assert not login_guard.device_ok("rac", uid, "hash-cu")
    exp, _, sig = tok.partition(".")
    assert not login_guard.device_ok(f"{int(exp) + 1}.{sig}", uid, "hash-cu")  # sửa hạn → sai chữ ký
    monkeypatch.setattr(time, "time", lambda: float(int(exp) + 1))
    assert not login_guard.device_ok(tok, uid, "hash-cu")  # hết hạn


def test_pin_lock_minutes_tang_dan() -> None:
    assert [service.pin_lock_minutes(n) for n in (1, 2, 3, 9)] == [get_settings().pin_lock_minutes, 60, 1440, 1440]


async def test_redis_loi_dung_bo_dem_bo_nho_van_chan() -> None:
    broken = BrokenRedis()
    limit = get_settings().login_fail_limit
    for _ in range(limit):
        assert await login_guard.blocked(broken, None, "a@example.vn") is None
        await login_guard.record_failure(broken, None, "a@example.vn")
    hit = await login_guard.blocked(broken, None, "a@example.vn")
    assert hit is not None and hit[1] == "email" and hit[0] > 0
    assert await login_guard.blocked(broken, None, "b@example.vn") is None  # email khác không bị
    await login_guard.clear_email(broken, "a@example.vn")
    assert await login_guard.blocked(broken, None, "a@example.vn") is None


async def test_redis_loi_dem_sai_theo_user_trong_bo_nho() -> None:
    broken, uid = BrokenRedis(), uuid4()
    n = get_settings().account_pw_fail_limit
    for i in range(n):
        assert await login_guard.user_blocked(broken, uid) is None
        assert await login_guard.reserve_user_attempt(broken, uid) == i + 1
    assert await login_guard.user_blocked(broken, uid) is not None


async def test_dem_sai_theo_user_tren_redis_that(redis: Redis) -> None:
    uid = uuid4()
    for _ in range(get_settings().account_pw_fail_limit):
        await login_guard.reserve_user_attempt(redis, uid)
    assert await login_guard.user_blocked(redis, uid) is not None
    assert 0 < await redis.ttl(login_guard.user_key(uid)) <= get_settings().login_fail_window_seconds
    await login_guard.clear_user(redis, uid)
    assert await login_guard.user_blocked(redis, uid) is None


# ─── Cần Postgres + Redis (CI) ──────────────────────────────────────────────────────────────────


async def test_account_password_do_mat_khau_bi_chan_va_thu_hoi_phien(owner_api: Api, app: object) -> None:
    limit = get_settings().account_pw_fail_limit
    for _ in range(limit - 1):
        r = await owner_api.send(
            "POST", "/account/password", {"current_password": "sai-roi-nhe-12", "new_password": NEW_PW}
        )
        assert r.status_code == 422
    r = await owner_api.send(
        "POST", "/account/password", {"current_password": "sai-roi-nhe-12", "new_password": NEW_PW}
    )
    assert r.status_code == 429 and r.json()["code"] == "ACCOUNT_RATE_LIMITED"
    assert "quá nhiều lần" in r.json()["title"]
    # Phiên đã bị thu hồi: cả mật khẩu đúng cũng không còn dùng được với cookie này.
    assert (await owner_api.get("/account")).status_code == 401
    r = await owner_api.send(
        "POST", "/account/password", {"current_password": OWNER["password"], "new_password": NEW_PW}
    )
    assert r.status_code == 401
    # Đăng nhập lại bình thường (ngưỡng đăng nhập theo email chưa chạm).
    assert (await _login(app, OWNER["email"], OWNER["password"])).status_code == 200


async def test_account_pin_do_mat_khau_bi_chan(owner_api: Api) -> None:
    body = {"current_password": "sai-roi-nhe-12", "new_pin": "135790", "new_pin_confirm": "135790"}
    for _ in range(get_settings().account_pw_fail_limit - 1):
        assert (await owner_api.send("POST", "/account/pin", body)).status_code == 422
    assert (await owner_api.send("POST", "/account/pin", body)).status_code == 429
    assert (await owner_api.get("/account")).status_code == 401


async def test_mat_khau_dung_xoa_bo_dem_account(owner_api: Api) -> None:
    body = {"current_password": "sai-roi-nhe-12", "new_password": NEW_PW}
    for _ in range(get_settings().account_pw_fail_limit - 1):
        assert (await owner_api.send("POST", "/account/password", body)).status_code == 422
    r = await owner_api.send(
        "POST", "/account/password", {"current_password": OWNER["password"], "new_password": NEW_PW}
    )
    assert r.status_code == 200, r.text
    assert (
        await owner_api.send("POST", "/account/password", {**body, "current_password": "van-sai-12345"})
    ).status_code == 422  # đếm lại từ 1


async def test_thiet_bi_tin_cay_van_dang_nhap_duoc_khi_email_bi_khoa(owner_api: Api, app: object) -> None:
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as mine:  # type: ignore[arg-type]
        r = await _login(app, OWNER["email"], OWNER["password"], IP_A, client=mine)
        assert r.status_code == 200 and mine.cookies.get(login_guard.DEVICE_COOKIE)
        for _ in range(get_settings().login_fail_limit):  # người lạ khoá email Owner
            assert (await _login(app, OWNER["email"], "sai-mat-khau", IP_B)).status_code == 401
        stranger = await _login(app, OWNER["email"], OWNER["password"], IP_B)
        assert stranger.status_code == 429 and stranger.json()["scope"] == "email"
        # Thiết bị cũ của Owner: vẫn vào được nếu đúng mật khẩu, và mật khẩu sai vẫn bị từ chối.
        assert (await _login(app, OWNER["email"], "sai-mat-khau", IP_A, client=mine)).status_code == 401
        assert (await _login(app, OWNER["email"], OWNER["password"], IP_A, client=mine)).status_code == 200


async def test_cookie_thiet_bi_khong_vuot_duoc_khoa_ip_hay_email_khac(
    owner_api: Api, app: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    await verify_pin(owner_api)
    r = await owner_api.send("POST", "/users", {"display_name": "Lan", "email": "lan@example.vn", "role": "operator"})
    assert r.status_code == 201, r.text
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as mine:  # type: ignore[arg-type]
        assert (await _login(app, OWNER["email"], OWNER["password"], IP_A, client=mine)).status_code == 200
        for _ in range(get_settings().login_fail_limit):
            await _login(app, "lan@example.vn", "sai-mat-khau", IP_B)
        # Cookie của Owner không mở khoá email của Lan.
        r = await _login(app, "lan@example.vn", r.json()["temp_password"], IP_B, client=mine)
        assert r.status_code == 429 and r.json()["scope"] == "email"
        # Khoá IP vẫn áp cho thiết bị tin cậy.
        monkeypatch.setattr(get_settings(), "login_ip_fail_limit", 3)
        for i in range(3):
            await _login(app, f"khong{i}@example.vn", "x" * 12, IP_B)
        r = await _login(app, OWNER["email"], OWNER["password"], IP_B, client=mine)
        assert r.status_code == 429 and r.json()["scope"] == "ip"


async def _bad_pins(api: Api, n: int = 5) -> httpx.Response:
    r: httpx.Response | None = None
    for _ in range(n):
        r = await api.send("POST", "/auth/pin/verify", {"pin": "000000"})
    assert r is not None
    return r


async def _locked_minutes() -> float:
    async with admin_sessionmaker()() as db:
        until = (
            await db.execute(text("SELECT pin_locked_until FROM core.users WHERE email = :e"), {"e": OWNER["email"]})
        ).scalar()
    assert until is not None
    return (until - datetime.now(UTC)).total_seconds() / 60


async def _expire_lock() -> None:
    async with admin_sessionmaker()() as db:
        await db.execute(text("UPDATE core.users SET pin_locked_until = now() - interval '1 second'"))
        await db.commit()


async def test_khoa_pin_tang_dan_15p_1h_24h_va_chuong_cho_owner(owner_api: Api) -> None:
    r = await _bad_pins(owner_api)
    assert r.status_code == 423
    assert 14 < await _locked_minutes() <= 15
    for expect in (60, 1440):
        await _expire_lock()
        assert (await _bad_pins(owner_api)).status_code == 423
        assert expect - 1 < await _locked_minutes() <= expect
    async with admin_sessionmaker()() as db:
        n = (await db.execute(text("SELECT count(*) FROM core.notifications WHERE kind = 'alert.pin_locked'"))).scalar()
    assert n == 3
    notes = (await owner_api.get("/notifications")).json()
    assert any(i["kind"] == "alert.pin_locked" for i in notes["items"]), notes


async def test_nhap_dung_pin_dat_lai_cap_khoa(owner_api: Api, redis: Redis) -> None:
    await _bad_pins(owner_api)
    await _expire_lock()
    assert int(await redis.get(login_guard.pin_level_key(await _owner_id()))) == 1
    await verify_pin(owner_api)
    assert await redis.get(login_guard.pin_level_key(await _owner_id())) is None
    await _bad_pins(owner_api)
    assert 14 < await _locked_minutes() <= 15  # lại bắt đầu từ 15 phút


async def _owner_id() -> Any:
    async with admin_sessionmaker()() as db:
        return (await db.execute(text("SELECT id FROM core.users WHERE email = :e"), {"e": OWNER["email"]})).scalar()


async def test_doi_pin_dong_cua_so_pin_o_phien_khac(owner_api: Api, app: object) -> None:
    c, other = await _second_session(app)
    try:
        await verify_pin(other)
        await verify_pin(owner_api)
        assert (await other.get("/auth/me")).json()["pin_verified_until"] is not None
        r = await owner_api.send(
            "POST",
            "/account/pin",
            {"current_password": OWNER["password"], "new_pin": "135790", "new_pin_confirm": "135790"},
        )
        assert r.status_code == 204, r.text
        assert (await other.get("/auth/me")).json()["pin_verified_until"] is None
        assert (await owner_api.get("/auth/me")).json()["pin_verified_until"] is None
    finally:
        await c.aclose()


async def test_redis_loi_van_gioi_han_dang_nhap_sai(owner_api: Api, app: object) -> None:
    real = app.state.redis  # type: ignore[attr-defined]
    app.state.redis = BrokenRedis()  # type: ignore[attr-defined]
    try:
        for _ in range(get_settings().login_fail_limit):
            assert (await _login(app, OWNER["email"], "sai-mat-khau", IP_A)).status_code == 401
        assert (await _login(app, OWNER["email"], OWNER["password"], IP_A)).status_code == 429
    finally:
        app.state.redis = real  # type: ignore[attr-defined]


async def test_thiet_bi_tin_cay_van_chiu_nguong_email_nho(owner_api: Api, app: object) -> None:
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as mine:  # type: ignore[arg-type]
        assert (await _login(app, OWNER["email"], OWNER["password"], IP_A, client=mine)).status_code == 200
        for _ in range(get_settings().login_trusted_email_fail_limit):  # cookie bị lộ dùng để dò mật khẩu
            assert (await _login(app, OWNER["email"], "sai-mat-khau", IP_A, client=mine)).status_code == 401
        r = await _login(app, OWNER["email"], OWNER["password"], IP_A, client=mine)
        assert r.status_code == 429 and r.json()["scope"] == "email"


async def test_cookie_thiet_bi_cu_vo_hieu_sau_doi_mat_khau_va_reset(owner_api: Api, app: object) -> None:
    from gh.auth.reset_owner import reset_owner_password

    async def _locked_out_but_cookie_works(client: httpx.AsyncClient, password: str) -> bool:
        """Khoá email (10 lần sai) rồi thử đăng nhập đúng bằng cookie: 200 ⇔ cookie còn được tin."""
        for _ in range(get_settings().login_fail_limit):
            await _login(app, OWNER["email"], "sai-mat-khau", IP_B)
        r = await _login(app, OWNER["email"], password, IP_A, client=client)
        await login_guard.clear_email(app.state.redis, OWNER["email"])  # type: ignore[attr-defined]
        return r.status_code == 200

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as mine:  # type: ignore[arg-type]
        assert (await _login(app, OWNER["email"], OWNER["password"], IP_A, client=mine)).status_code == 200
        assert await _locked_out_but_cookie_works(mine, OWNER["password"])  # đối chứng: cookie còn hiệu lực
        # 1) đổi mật khẩu qua /account
        r = await owner_api.send(
            "POST", "/account/password", {"current_password": OWNER["password"], "new_password": NEW_PW}
        )
        assert r.status_code == 200, r.text
        assert not await _locked_out_but_cookie_works(mine, NEW_PW)
        # đăng nhập lại có cookie mới → hiệu lực; rồi reset (genh reset-password) → vô hiệu
        assert (await _login(app, OWNER["email"], NEW_PW, IP_A, client=mine)).status_code == 200
        assert await _locked_out_but_cookie_works(mine, NEW_PW)
        async with admin_sessionmaker()() as db:
            result = await reset_owner_password(db)
            await db.commit()
        assert not await _locked_out_but_cookie_works(mine, result.temp_password)


async def test_song_song_khong_vuot_nguong_account(owner_api: Api, monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio

    from gh.auth import account

    calls = 0
    real = account.verify_secret

    def counting(h: str, p: str) -> bool:
        nonlocal calls
        calls += 1
        return real(h, p)

    monkeypatch.setattr(account, "verify_secret", counting)
    body = {"current_password": "sai-roi-nhe-12", "new_password": NEW_PW}
    rs = await asyncio.gather(*[owner_api.send("POST", "/account/password", body) for _ in range(20)])
    assert calls <= get_settings().account_pw_fail_limit, calls
    assert all(r.status_code in (422, 429, 401) for r in rs)
    assert any(r.status_code == 429 for r in rs)
