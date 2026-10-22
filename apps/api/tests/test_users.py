"""v0.1.22 (Đợt B1–B3) — /users (Quản lý người dùng), /system/org (Tổ chức), /system/about (Trợ giúp)."""

from pathlib import Path

import httpx
import pytest
from sqlalchemy import text

from gh.config import get_settings
from gh.db import admin_sessionmaker
from tests.conftest import OWNER, Api


async def _log(action: str) -> list[dict[str, object]]:
    async with admin_sessionmaker()() as db:
        rows = (await db.execute(text(
            "SELECT result, target_label, detail FROM ops.action_log WHERE action = :a ORDER BY at"),
            {"a": action})).all()
    return [{"result": r.result, "target": r.target_label, "detail": r.detail} for r in rows]


async def _pin(api: Api, pin: str = OWNER["pin"]) -> None:
    assert (await api.send("POST", "/auth/pin/verify", {"pin": pin})).status_code == 200


async def _login(app: object, email: str, password: str) -> tuple[httpx.AsyncClient, Api, httpx.Response]:
    c = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")  # type: ignore[arg-type]
    api = Api(c)
    r = await api.send("POST", "/auth/login", {"email": email, "password": password})
    return c, api, r


async def _invite(api: Api, email: str = "lan@example.vn", role: str = "operator") -> dict[str, object]:
    r = await api.send("POST", "/users", {"display_name": "  Chị   Lan ", "email": email, "role": role})
    assert r.status_code == 201, r.text
    return r.json()  # type: ignore[no-any-return]


async def test_list_and_invite_needs_pin_and_logs(owner_api: Api, app: object) -> None:
    r = await owner_api.get("/users")
    assert r.status_code == 200, r.text
    body = r.json()
    assert [u["email"] for u in body["items"]] == [OWNER["email"]]
    me = body["items"][0]
    assert me["is_self"] is True and me["role"]["code"] == "owner" and me["status"] == "active"
    assert me["last_login_at"] is not None
    assert {x["code"]: x["assignable"] for x in body["roles"]}["owner"] is False

    r = await owner_api.send("POST", "/users", {"display_name": "Lan", "email": "lan@example.vn", "role": "operator"})
    assert r.status_code == 423  # PIN
    await _pin(owner_api)
    r = await owner_api.send("POST", "/users", {"display_name": " ", "email": "sai", "role": "operator"})
    assert r.status_code == 422 and set(r.json()["errors"]) == {"display_name", "email"}
    r = await owner_api.send("POST", "/users", {"display_name": "X", "email": "a@b.vn", "role": "owner"})
    assert r.status_code == 422  # không mời Owner
    out = await _invite(owner_api)
    assert out["user"]["display_name"] == "Chị Lan" and out["user"]["must_change_password"] is True  # type: ignore[index]
    temp = str(out["temp_password"])
    assert len(temp) >= 10
    r = await owner_api.send("POST", "/users", {"display_name": "Lan 2", "email": "LAN@example.vn", "role": "auditor"})
    assert r.status_code == 422 and "email" in r.json()["errors"]
    assert [x["target"] for x in await _log("user.invited")] == ["lan@example.vn"]

    # Người được mời đăng nhập bằng mật khẩu tạm → bị buộc đổi mật khẩu.
    c, lan, r = await _login(app, "lan@example.vn", temp)
    try:
        assert r.status_code == 200
        assert (await lan.get("/overview")).json()["code"] == "PASSWORD_CHANGE_REQUIRED"
        assert (await lan.get("/account")).status_code == 200
    finally:
        await c.aclose()
    items = (await owner_api.get("/users")).json()["items"]
    assert {u["email"]: u["last_login_at"] is not None for u in items}["lan@example.vn"] is True


async def test_invited_user_logs_in_with_mixed_case_email(owner_api: Api, app: object) -> None:
    await _pin(owner_api)
    out = await _invite(owner_api, "mixed.case@example.vn")
    pw = out["temp_password"]  # type: ignore[index]
    c, _api, r = await _login(app, "  Mixed.CASE@Example.vn ", pw)  # type: ignore[arg-type]
    await c.aclose()
    assert r.status_code == 200, r.text


async def test_member_cannot_manage_users(owner_api: Api, app: object) -> None:
    await _pin(owner_api)
    out = await _invite(owner_api, role="manager")
    c, lan, _ = await _login(app, "lan@example.vn", str(out["temp_password"]))
    try:
        r = await lan.send("POST", "/account/password", {"current_password": out["temp_password"],
                                                          "new_password": "mat-khau-cua-lan-123"})
        assert r.status_code == 200, r.text
        assert (await lan.get("/users")).status_code == 403
        assert (await lan.send("POST", "/users", {"display_name": "A", "email": "a@b.vn",
                                                  "role": "auditor"})).status_code == 403
        assert (await lan.get("/system/about")).status_code == 200
        assert (await lan.send("PATCH", "/system/org", {"org_name": "X", "timezone": "UTC", "currency": "USD",
                                                        "self_name": "A", "bot_calls_me": "B"})).status_code == 403
    finally:
        await c.aclose()


async def test_change_role_deactivate_reactivate_reset(owner_api: Api, app: object) -> None:
    await _pin(owner_api)
    out = await _invite(owner_api)
    uid = out["user"]["id"]  # type: ignore[index]
    me_id = (await owner_api.get("/users")).json()["items"][0]["id"]

    # Không đổi vai trò / khoá / đặt lại mật khẩu của chính mình.
    for method, path, body in (("PATCH", f"/users/{me_id}/role", {"role": "manager"}),
                               ("POST", f"/users/{me_id}/deactivate", None),
                               ("POST", f"/users/{me_id}/reset-password", None)):
        r = await owner_api.send(method, path, body)
        assert r.status_code == 409 and r.json()["code"] == "SELF_CHANGE", (path, r.text)

    r = await owner_api.send("PATCH", f"/users/{uid}/role", {"role": "auditor"})
    assert r.status_code == 200 and r.json()["role"]["code"] == "auditor"
    assert (await _log("user.role_changed"))[0]["detail"] == {"from": "operator", "to": "auditor"}

    c, lan, r = await _login(app, "lan@example.vn", str(out["temp_password"]))
    try:
        assert r.status_code == 200
        r = await owner_api.send("POST", f"/users/{uid}/deactivate")
        assert r.status_code == 200 and r.json()["status"] == "inactive"
        assert (await _log("user.deactivated"))[0]["detail"] == {"sessions_revoked": 1}
        assert (await lan.get("/account")).status_code == 401  # phiên bị thu hồi
    finally:
        await c.aclose()
    _, _, r = await _login(app, "lan@example.vn", str(out["temp_password"]))
    assert r.status_code == 401  # khoá rồi không đăng nhập được

    r = await owner_api.send("POST", f"/users/{uid}/reactivate")
    assert r.status_code == 200 and r.json()["status"] == "active"
    r = await owner_api.send("POST", f"/users/{uid}/reset-password")
    assert r.status_code == 200, r.text
    new_temp = r.json()["temp_password"]
    assert new_temp != out["temp_password"] and r.json()["user"]["must_change_password"] is True
    _, _, r = await _login(app, "lan@example.vn", str(out["temp_password"]))
    assert r.status_code == 401
    _, _, r = await _login(app, "lan@example.vn", new_temp)
    assert r.status_code == 200
    assert [x["result"] for x in await _log("user.reactivated")] == ["ok"]
    assert [x["result"] for x in await _log("user.password_reset")] == ["ok"]
    assert (await owner_api.send("POST", "/users/00000000-0000-0000-0000-000000000000/deactivate")).status_code == 404


async def test_last_owner_is_protected(owner_api: Api, app: object) -> None:
    """Chỉ chạm được khi vai trò khác cũng có roles.manage (Owner sửa DB) — tổ chức vẫn không được mất Owner."""
    await _pin(owner_api)
    out = await _invite(owner_api, role="manager")
    async with admin_sessionmaker()() as db:
        await db.execute(text("""UPDATE core.role_permissions SET scope = 'all' WHERE permission_code = 'roles.manage'
                                 AND role_id IN (SELECT id FROM core.roles WHERE code = 'manager')"""))
        await db.commit()
    owner_id = (await owner_api.get("/users")).json()["items"][0]["id"]
    c, lan, r = await _login(app, "lan@example.vn", str(out["temp_password"]))
    try:
        # Người mời THẬT: đổi mật khẩu tạm rồi tự đặt PIN lần đầu (v0.1.61) — không chèn pin_hash bằng SQL.
        new_pw = "mat-khau-moi-cua-lan-88"
        r = await lan.send("POST", "/account/password",
                           {"current_password": out["temp_password"], "new_password": new_pw})
        assert r.status_code == 200, r.text
        r = await lan.send("POST", "/account/pin", {"current_password": new_pw, "new_pin": "135790",
                                                    "new_pin_confirm": "135790"})
        assert r.status_code == 204, r.text
        await _pin(lan, "135790")
        r = await lan.send("PATCH", f"/users/{owner_id}/role", {"role": "manager"})
        assert r.status_code == 409 and r.json()["code"] == "LAST_OWNER"
        r = await lan.send("POST", f"/users/{owner_id}/deactivate")
        assert r.status_code == 409 and r.json()["code"] == "LAST_OWNER"
    finally:
        await c.aclose()


async def test_org_settings_reuse_step3_validation(owner_api: Api) -> None:
    r = await owner_api.get("/system/org")
    assert r.status_code == 200
    assert r.json() | {"currencies": None} == {"org_name": "Genesis Việt", "timezone": "Asia/Ho_Chi_Minh",
                                               "currency": "VND", "self_name": "Anh", "bot_calls_me": "Sếp",
                                               "currencies": None, "can_edit": True}
    bad = {"org_name": " ", "timezone": "Hà Nội", "currency": "abc", "self_name": "", "bot_calls_me": " "}
    r = await owner_api.send("PATCH", "/system/org", bad)
    assert r.status_code == 422
    assert set(r.json()["errors"]) == {"org_name", "timezone", "currency", "self_name", "bot_calls_me"}
    good = {"org_name": " Genesis Trading ", "timezone": "Asia/Singapore", "currency": "usd", "self_name": "Tôi",
            "bot_calls_me": "Sếp A"}
    r = await owner_api.send("PATCH", "/system/org", good)
    assert r.status_code == 200, r.text
    assert r.json()["org_name"] == "Genesis Trading" and r.json()["currency"] == "USD"
    me = (await owner_api.get("/auth/me")).json()
    assert me["org"]["name"] == "Genesis Trading" and me["org"]["timezone"] == "Asia/Singapore"
    assert me["addressing"]["bot_calls_me"] == "Sếp A"
    logs = await _log("org.updated")
    assert len(logs) == 1 and logs[0]["detail"]["fields"] == [  # type: ignore[index]
        "bot_calls_me", "currency", "org_name", "self_name", "timezone"]
    # Không đổi gì → không ghi thêm.
    assert (await owner_api.send("PATCH", "/system/org", good)).status_code == 200
    assert len(await _log("org.updated")) == 1


async def test_about_reads_genh_version(owner_api: Api, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "host_link_dir", str(tmp_path))
    r = await owner_api.get("/system/about")
    # v0.1.36 (F-46): không có genh.json ⇒ genh_version null, version rơi về image_version (gh.__version__).
    assert r.status_code == 200 and r.json()["genh_version"] is None and r.json()["org_name"] == "Genesis Việt"
    assert r.json()["version"] == r.json()["image_version"]
    (tmp_path / "genh.json").write_text('{"version": "v0.1.22"}', encoding="utf-8")
    body = (await owner_api.get("/system/about")).json()
    assert body["version"] == "v0.1.22" and body["role"]["code"] == "owner" and body["timezone"] == "Asia/Ho_Chi_Minh"
