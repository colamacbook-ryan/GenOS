"""v0.1.19 — /account ("Tài khoản của tôi") + buộc đổi mật khẩu sau genh reset-password."""

import httpx
from sqlalchemy import text

from gh.auth.reset_owner import reset_owner_password
from gh.db import admin_sessionmaker
from tests.conftest import OWNER, Api

NEW_PW = "mat-khau-moi-rat-dai-456"


async def _actions(action: str) -> list[str]:
    async with admin_sessionmaker()() as db:
        return [r.result for r in (await db.execute(text(
            "SELECT result FROM ops.action_log WHERE action = :a ORDER BY at"), {"a": action})).all()]


async def _second_login(app: object) -> tuple[httpx.AsyncClient, Api]:
    c = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")  # type: ignore[arg-type]
    api = Api(c)
    r = await api.send("POST", "/auth/login", {"email": OWNER["email"], "password": OWNER["password"]})
    assert r.status_code == 200, r.text
    return c, api


async def test_get_account_lists_sessions(owner_api: Api, app: object) -> None:
    c, other = await _second_login(app)
    try:
        r = await owner_api.get("/account")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["email"] == OWNER["email"] and body["display_name"] == OWNER["display_name"]
        assert body["role"]["code"] == "owner" and body["has_pin"] is True
        assert body["must_change_password"] is False and body["created_at"]
        assert len(body["sessions"]) == 2
        assert body["sessions"][0]["current"] is True and body["sessions"][1]["current"] is False
        me = (await owner_api.get("/auth/me")).json()
        assert me["must_change_password"] is False
    finally:
        await c.aclose()


async def test_update_display_name_and_email(owner_api: Api) -> None:
    r = await owner_api.send("PATCH", "/account", {"display_name": "  Nguyễn   Văn A  "})
    assert r.status_code == 200, r.text
    assert r.json()["display_name"] == "Nguyễn Văn A"
    assert (await owner_api.get("/auth/me")).json()["display_name"] == "Nguyễn Văn A"
    # Đổi email cần mật khẩu hiện tại.
    r = await owner_api.send("PATCH", "/account", {"email": "moi@example.vn"})
    assert r.status_code == 422 and "current_password" in r.json()["errors"]
    r = await owner_api.send("PATCH", "/account", {"email": "moi@example.vn", "current_password": "sai-roi-nhe"})
    assert r.status_code == 422 and r.json()["errors"]["current_password"] == "Mật khẩu hiện tại không đúng"
    assert await _actions("account.password_check_failed") == ["failed"]
    r = await owner_api.send("PATCH", "/account", {"email": "khong-hop-le", "current_password": OWNER["password"]})
    assert r.status_code == 422 and "email" in r.json()["errors"]
    r = await owner_api.send("PATCH", "/account", {"email": "Moi@Example.vn", "current_password": OWNER["password"]})
    assert r.status_code == 200 and r.json()["email"] == "moi@example.vn"
    assert await _actions("account.profile_updated") == ["ok", "ok"]
    r = await owner_api.send("POST", "/auth/login", {"email": "moi@example.vn", "password": OWNER["password"]})
    assert r.status_code == 200


async def test_email_must_be_unique_in_org(owner_api: Api) -> None:
    async with admin_sessionmaker()() as db:
        await db.execute(text("""INSERT INTO core.users (org_id, email, display_name, password_hash)
                                 SELECT org_id, 'khac@example.vn', 'Khác', 'x' FROM core.users LIMIT 1"""))
        await db.commit()
    r = await owner_api.send("PATCH", "/account", {"email": "KHAC@example.vn", "current_password": OWNER["password"]})
    assert r.status_code == 422 and "email" in r.json()["errors"]


async def test_change_password_rules_and_revokes_other_sessions(owner_api: Api, app: object) -> None:
    c, other = await _second_login(app)
    try:
        r = await owner_api.send("POST", "/account/password", {"current_password": OWNER["password"],
                                                               "new_password": "ngan"})
        assert r.status_code == 422 and "new_password" in r.json()["errors"]
        r = await owner_api.send("POST", "/account/password", {"current_password": "sai-mat-khau-roi",
                                                               "new_password": NEW_PW})
        assert r.status_code == 422 and "current_password" in r.json()["errors"]
        r = await owner_api.send("POST", "/account/password", {"current_password": OWNER["password"],
                                                               "new_password": OWNER["password"]})
        assert r.status_code == 422 and r.json()["errors"]["new_password"] == "Mật khẩu mới phải khác mật khẩu hiện tại"
        assert (await other.get("/auth/me")).status_code == 200
        r = await owner_api.send("POST", "/account/password", {"current_password": OWNER["password"],
                                                               "new_password": NEW_PW})
        assert r.status_code == 200 and r.json() == {"sessions_revoked": 1}
        assert (await owner_api.get("/auth/me")).status_code == 200  # phiên hiện tại giữ nguyên
        assert (await other.get("/auth/me")).status_code == 401  # phiên khác bị thu hồi
        assert await _actions("account.password_changed") == ["ok"]
        r = await owner_api.send("POST", "/auth/login", {"email": OWNER["email"], "password": OWNER["password"]})
        assert r.status_code == 401
        r = await owner_api.send("POST", "/auth/login", {"email": OWNER["email"], "password": NEW_PW})
        assert r.status_code == 200
    finally:
        await c.aclose()


async def test_change_pin(owner_api: Api) -> None:
    base = {"current_password": OWNER["password"], "new_pin": "135790", "new_pin_confirm": "135790"}
    r = await owner_api.send("POST", "/account/pin", {**base, "new_pin": "12ab56"})
    assert r.status_code == 422 and "new_pin" in r.json()["errors"]
    r = await owner_api.send("POST", "/account/pin", {**base, "new_pin_confirm": "135791"})
    assert r.status_code == 422 and "new_pin_confirm" in r.json()["errors"]
    r = await owner_api.send("POST", "/account/pin", {**base, "current_password": "sai-mat-khau-roi"})
    assert r.status_code == 422 and "current_password" in r.json()["errors"]
    r = await owner_api.send("POST", "/account/pin", base)
    assert r.status_code == 204, r.text
    assert (await owner_api.send("POST", "/auth/pin/verify", {"pin": OWNER["pin"]})).status_code == 401
    assert (await owner_api.send("POST", "/auth/pin/verify", {"pin": "135790"})).status_code == 200
    assert await _actions("account.pin_changed") == ["ok"]


async def test_set_first_pin_when_account_has_none(owner_api: Api) -> None:
    """v0.1.61 (F071/F097): tài khoản chưa có PIN đặt PIN lần đầu bằng mật khẩu (không còn 409 NO_PIN).
    Luồng người được mời thật nằm ở tests/test_pin_nguoi_duoc_moi.py."""
    async with admin_sessionmaker()() as db:
        await db.execute(text("UPDATE core.users SET pin_hash = NULL"))
        await db.commit()
    assert (await owner_api.get("/account")).json()["has_pin"] is False
    r = await owner_api.send("POST", "/account/pin", {"current_password": OWNER["password"], "new_pin": "135790",
                                                      "new_pin_confirm": "135790"})
    assert r.status_code == 204, r.text
    assert (await owner_api.get("/account")).json()["has_pin"] is True
    assert (await owner_api.send("POST", "/auth/pin/verify", {"pin": "135790"})).status_code == 200
    assert await _actions("account.pin_set") == ["ok"]
    assert await _actions("account.pin_changed") == []


async def test_revoke_sessions(owner_api: Api, app: object) -> None:
    c1, s1 = await _second_login(app)
    c2, s2 = await _second_login(app)
    try:
        sessions = (await owner_api.get("/account")).json()["sessions"]
        assert len(sessions) == 3
        current = next(s for s in sessions if s["current"])
        r = await owner_api.send("DELETE", f"/account/sessions/{current['id']}")
        assert r.status_code == 409
        target = next(s for s in sessions if not s["current"])
        r = await owner_api.send("DELETE", f"/account/sessions/{target['id']}")
        assert r.status_code == 204
        assert (await owner_api.send("DELETE", f"/account/sessions/{target['id']}")).status_code == 404
        still = [(await s.get("/auth/me")).status_code for s in (s1, s2)]
        assert sorted(still) == [200, 401]
        r = await owner_api.send("POST", "/account/sessions/revoke-others")
        assert r.status_code == 200 and r.json() == {"sessions_revoked": 1}
        assert [(await s.get("/auth/me")).status_code for s in (s1, s2)] == [401, 401]
        assert len((await owner_api.get("/account")).json()["sessions"]) == 1
        assert await _actions("account.session_revoked") == ["ok"]
        assert await _actions("account.sessions_revoked") == ["ok"]
    finally:
        await c1.aclose()
        await c2.aclose()


async def test_reset_owner_forces_password_change(owner_api: Api) -> None:
    async with admin_sessionmaker()() as db:
        result = await reset_owner_password(db)
        await db.commit()
    r = await owner_api.send("POST", "/auth/login", {"email": OWNER["email"], "password": result.temp_password})
    assert r.status_code == 200 and r.json()["must_change_password"] is True
    assert (await owner_api.get("/account")).json()["must_change_password"] is True
    r = await owner_api.send("POST", "/account/password", {"current_password": result.temp_password,
                                                           "new_password": NEW_PW})
    assert r.status_code == 200, r.text
    assert (await owner_api.get("/auth/me")).json()["must_change_password"] is False
    async with admin_sessionmaker()() as db:
        detail = (await db.execute(text("SELECT detail FROM ops.action_log WHERE action = 'account.password_changed'"
                                        ))).scalar()
    assert detail["was_forced"] is True


async def test_account_requires_login(client: httpx.AsyncClient, owner_api: Api) -> None:
    await owner_api.send("POST", "/auth/logout")
    assert (await owner_api.get("/account")).status_code == 401
