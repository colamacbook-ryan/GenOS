"""Luồng thiết lập Owner bước 1–3, đăng nhập, CSRF, PIN qua API thật."""

from sqlalchemy import text

from tests.conftest import OWNER, Api, do_setup


async def test_console_blocked_until_setup(client) -> None:  # type: ignore[no-untyped-def]
    api = Api(client)
    r = await api.get("/navigation")
    assert r.status_code == 428 and r.json()["code"] == "SETUP_REQUIRED"
    assert (await api.get("/auth/me")).status_code == 428      # web đi thẳng tới /setup, không qua /login
    r = await api.get("/setup/state")
    assert r.status_code == 200
    s = r.json()
    assert s["current_step"] == 1 and not s["finished"] and len(s["steps"]) == 12
    assert [st["n"] for st in s["steps"] if st["available"]] == [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]
    assert (await api.get("/health")).json() == {"status": "ok"}


async def test_setup_token_is_required_and_single_use(client) -> None:  # type: ignore[no-untyped-def]
    api = Api(client)
    r = await api.send("PUT", "/setup/steps/1", {"token": "sai", "language": "vi", "mode": "empty"})
    assert r.status_code == 403 and r.json()["code"] == "SETUP_TOKEN_INVALID"
    r = await api.send("PUT", "/setup/steps/2", {"token": "test-setup-token", **OWNER})
    assert r.status_code == 409 and r.json()["code"] == "STEP_ORDER"
    await do_setup(api)
    # Mã đã hết hiệu lực sau khi có Owner; không tạo được Owner thứ hai.
    fresh = Api(client.__class__(transport=client._transport, base_url="http://test"))
    r = await fresh.send("PUT", "/setup/steps/2", {"token": "test-setup-token", **OWNER, "email": "b@x.vn"})
    assert r.status_code == 409 and r.json()["code"] == "OWNER_EXISTS"
    r = await fresh.send("PUT", "/setup/steps/1", {"token": "test-setup-token"})
    assert r.status_code == 401


async def test_step2_field_validation(client) -> None:  # type: ignore[no-untyped-def]
    api = Api(client)
    await api.send("PUT", "/setup/steps/1", {"token": "test-setup-token"})
    r = await api.send("PUT", "/setup/steps/2", {"token": "test-setup-token", "display_name": " ",
                                                  "email": "khong-phai-email", "password": "ngan",
                                                  "pin": "12345a", "pin_confirm": "12345a"})
    assert r.status_code == 422
    assert set(r.json()["errors"]) == {"display_name", "email", "password", "pin"}
    r = await api.send("PUT", "/setup/steps/2", {"token": "test-setup-token", **OWNER, "pin_confirm": "000000"})
    assert r.json()["errors"] == {"pin_confirm": "Hai lần nhập mã PIN không khớp"}


async def test_full_setup_then_me_and_state_resume(client, app) -> None:  # type: ignore[no-untyped-def]
    api = Api(client)
    await do_setup(api)
    me = (await api.get("/auth/me")).json()
    assert me["role"]["code"] == "owner" and me["org"]["name"] == "Genesis Việt"
    assert me["addressing"] == {"self": "Anh", "bot_calls_me": "Sếp"}
    assert me["permissions"]["people_review.read"] == "all"
    assert me["pin_verified_until"] is None
    s = (await api.get("/setup/state")).json()
    assert s["console_ready"] and s["current_step"] == 4
    assert [st["status"] for st in s["steps"][:4]] == ["done", "done", "done", "doing"]
    assert (await api.get("/navigation")).status_code == 200
    # Bỏ qua: chỉ bước không bắt buộc (v0.1.29: bước 4 "Để sau" được, bước 1–3 thì không).
    assert (await api.send("POST", "/setup/steps/3/skip")).json()["code"] == "STEP_REQUIRED"
    r = await api.send("POST", "/setup/steps/11/skip")
    assert r.status_code == 200 and r.json()["steps"][10]["status"] == "skipped"


async def test_login_logout_and_csrf(owner_api) -> None:  # type: ignore[no-untyped-def]
    api: Api = owner_api
    c = api.c
    assert (await api.send("POST", "/auth/logout")).status_code == 204
    assert (await api.get("/auth/me")).status_code == 401
    r = await api.send("POST", "/auth/login", {"email": OWNER["email"], "password": "sai-mat-khau-roi"})
    assert r.status_code == 401 and r.json()["code"] == "INVALID_CREDENTIALS"
    r = await api.send("POST", "/auth/login", {"email": "OWNER@example.vn", "password": OWNER["password"]})
    assert r.status_code == 200 and r.json()["email"] == OWNER["email"]
    # Request ghi thiếu / sai CSRF bị chặn.
    r = await c.post("/api/v1/auth/logout")
    assert r.status_code == 403 and r.json()["code"] == "CSRF_INVALID"
    r = await c.post("/api/v1/auth/logout", headers={"X-CSRF-Token": "gia-mao"})
    assert r.status_code == 403
    assert (await api.send("POST", "/auth/logout")).status_code == 204


async def test_pin_flow_and_lockout(owner_api, db) -> None:  # type: ignore[no-untyped-def]
    api: Api = owner_api
    r = await api.send("PATCH", "/plugins/@gen/chassis-bus/toggle", {"enabled": False})
    assert r.status_code == 423 and r.json()["code"] == "PIN_REQUIRED"
    r = await api.send("POST", "/auth/pin/verify", {"pin": "000000"})
    assert r.status_code == 401 and r.json()["attempts_left"] == 4
    r = await api.send("POST", "/auth/pin/verify", {"pin": OWNER["pin"]})
    assert r.status_code == 200 and r.json()["pin_verified_until"]
    assert (await api.get("/auth/me")).json()["pin_verified_until"]
    for left in (4, 3, 2, 1):
        r = await api.send("POST", "/auth/pin/verify", {"pin": "111111"})
        assert r.json()["attempts_left"] == left
    r = await api.send("POST", "/auth/pin/verify", {"pin": "111111"})
    assert r.status_code == 423 and r.json()["code"] == "PIN_LOCKED" and r.json()["locked_until"]
    # Khi khoá: PIN đúng cũng bị từ chối, phiên PIN đã bị thu hồi.
    r = await api.send("POST", "/auth/pin/verify", {"pin": OWNER["pin"]})
    assert r.status_code == 423
    assert (await api.get("/auth/me")).json()["pin_verified_until"] is None
    actions = (await db.execute(text("SELECT action, result FROM ops.action_log WHERE action LIKE 'auth.pin%' "
                                     "ORDER BY at"))).all()
    assert [a.action for a in actions] == ["auth.pin_failed", "auth.pin_verified", "auth.pin_failed",
                                           "auth.pin_failed", "auth.pin_failed", "auth.pin_failed",
                                           "auth.pin_locked", "auth.pin_attempt_while_locked"]


async def test_pin_change(owner_api) -> None:  # type: ignore[no-untyped-def]
    """v0.1.61 (F088): chỉ còn MỘT đường đổi PIN — POST /account/pin bằng mật khẩu (PUT /auth/pin đã gỡ)."""
    api: Api = owner_api
    await api.send("POST", "/auth/pin/verify", {"pin": OWNER["pin"]})
    r = await api.send("POST", "/account/pin", {"current_password": OWNER["password"], "new_pin": "13579",
                                                "new_pin_confirm": "13579"})
    assert r.status_code == 422
    r = await api.send("POST", "/account/pin", {"current_password": OWNER["password"], "new_pin": "135790",
                                                "new_pin_confirm": "135790"})
    assert r.status_code == 204
    r = await api.send("POST", "/auth/pin/verify", {"pin": "135790"})
    assert r.status_code == 200
