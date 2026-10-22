"""v0.1.61 (gói A1; F071, F097, F088, F115) — mã PIN cho người được mời + phiên PIN hết hạn thật.

Mọi test dùng NGƯỜI ĐƯỢC MỜI THẬT (Owner mời qua POST /users, người đó đăng nhập bằng mật khẩu tạm) — KHÔNG chèn
`pin_hash` bằng SQL (đó là cách test cũ che mất lỗi: người được mời không có đường nào đặt PIN)."""

import uuid
from datetime import timedelta
from typing import Any

import httpx
from sqlalchemy import text

from gh.auth import service
from gh.db import admin_sessionmaker
from tests.conftest import OWNER, Api, verify_pin

NEW_PASSWORD = "mat-khau-rieng-cua-quan-ly-77"
NEW_PIN = "135790"
NOT_SET = "Tài khoản chưa có mã PIN — đặt ở Tài khoản của tôi"


async def _invite_manager(owner_api: Api, app: object,
                          email: str = "quanly@example.vn") -> tuple[httpx.AsyncClient, Api]:
    """Owner mời Quản lý → Quản lý đăng nhập bằng mật khẩu tạm và đổi mật khẩu (luồng thật, không đụng SQL)."""
    await verify_pin(owner_api)
    r = await owner_api.send("POST", "/users", {"display_name": "Chị Quản lý", "email": email, "role": "manager"})
    assert r.status_code == 201, r.text
    temp = r.json()["temp_password"]
    c = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")  # type: ignore[arg-type]
    api = Api(c)
    r = await api.send("POST", "/auth/login", {"email": email, "password": temp})
    assert r.status_code == 200, r.text
    r = await api.send("POST", "/account/password", {"current_password": temp, "new_password": NEW_PASSWORD})
    assert r.status_code == 200, r.text
    return c, api


async def _sql_one(sql: str, params: dict[str, Any] | None = None) -> Any:
    async with admin_sessionmaker()() as db:
        return (await db.execute(text(sql), params or {})).one()


async def _actions(action: str) -> list[Any]:
    async with admin_sessionmaker()() as db:
        return list((await db.execute(text(
            "SELECT result, detail, target_label FROM ops.action_log WHERE action = :a ORDER BY at"),
            {"a": action})).all())


# ─── F071 / F097: người được mời đặt PIN lần đầu ───────────────────────────────────────────────────

async def test_nguoi_duoc_moi_chua_co_pin_hop_pin_noi_ro_va_khong_dem_sai(owner_api: Api, app: object) -> None:
    """Người mời thật: ban đầu KHÔNG có PIN; hộp PIN phải nói rõ "chưa có mã PIN" và không đếm sai / không khoá."""
    c, qly = await _invite_manager(owner_api, app)
    try:
        assert (await qly.get("/account")).json()["has_pin"] is False
        # Thao tác cần PIN → 423 (hộp PIN mở), nhưng gõ gì cũng KHÔNG được báo "không đúng".
        r = await qly.send("POST", f"/drafts/{uuid.uuid4()}/reject", {})
        assert r.status_code == 423 and r.json()["code"] == "PIN_REQUIRED"
        for _ in range(7):  # nhiều hơn ngưỡng khoá (5)
            r = await qly.send("POST", "/auth/pin/verify", {"pin": "123456"})
            assert r.status_code == 409, r.text
            assert r.json()["code"] == "PIN_NOT_SET" and r.json()["title"] == NOT_SET
            assert "attempts_left" not in r.json()
        row = await _sql_one("SELECT pin_failed, pin_locked_until FROM core.users WHERE email = 'quanly@example.vn'")
        assert row.pin_failed == 0 and row.pin_locked_until is None
        assert await _actions("auth.pin_locked") == []
        assert await _actions("auth.pin_failed") == []
    finally:
        await c.aclose()


async def test_dat_pin_lan_dau_bang_mat_khau_roi_duyet_duoc(owner_api: Api, app: object) -> None:
    c, qly = await _invite_manager(owner_api, app)
    try:
        body = {"current_password": NEW_PASSWORD, "new_pin": NEW_PIN, "new_pin_confirm": NEW_PIN}
        # Sai mật khẩu → 422 theo ô, vẫn chưa có PIN, ghi nhật ký thất bại.
        r = await qly.send("POST", "/account/pin", {**body, "current_password": "sai-mat-khau-nhe"})
        assert r.status_code == 422 and r.json()["errors"]["current_password"] == "Mật khẩu hiện tại không đúng"
        assert (await qly.get("/account")).json()["has_pin"] is False
        # PIN không đủ 6 số / hai lần không khớp → 422 theo ô.
        r = await qly.send("POST", "/account/pin", {**body, "new_pin": "12ab", "new_pin_confirm": "12ab"})
        assert r.status_code == 422 and "new_pin" in r.json()["errors"]
        r = await qly.send("POST", "/account/pin", {**body, "new_pin_confirm": "999999"})
        assert r.status_code == 422 and "new_pin_confirm" in r.json()["errors"]
        assert (await qly.get("/account")).json()["has_pin"] is False

        r = await qly.send("POST", "/account/pin", body)
        assert r.status_code == 204, r.text
        assert (await qly.get("/account")).json()["has_pin"] is True
        # Nhật ký: đặt lần đầu là account.pin_set (không phải "đổi"), KHÔNG lộ PIN / mật khẩu ở bất kỳ cột nào.
        sets = await _actions("account.pin_set")
        assert [s.result for s in sets] == ["ok"] and sets[0].target_label == "quanly@example.vn"
        assert await _actions("account.pin_changed") == []
        async with admin_sessionmaker()() as db:
            blob = "\n".join(str(r) for r in (await db.execute(text("SELECT * FROM ops.action_log"))).all())
        assert NEW_PIN not in blob and NEW_PASSWORD not in blob

        # PIN mới dùng được ngay: sai → đếm; đúng → mở phiên PIN → duyệt không còn bị chặn 423.
        r = await qly.send("POST", "/auth/pin/verify", {"pin": "000000"})
        assert r.status_code == 401 and r.json()["code"] == "PIN_INVALID" and r.json()["attempts_left"] == 4
        r = await qly.send("POST", "/auth/pin/verify", {"pin": NEW_PIN})
        assert r.status_code == 200, r.text
        r = await qly.send("POST", f"/drafts/{uuid.uuid4()}/reject", {})
        assert r.status_code == 404, r.text  # qua cổng PIN, chỉ không có bản nháp này

        # Lần sau là ĐỔI PIN (vẫn bằng mật khẩu) → nhật ký account.pin_changed.
        r = await qly.send("POST", "/account/pin", {"current_password": NEW_PASSWORD, "new_pin": "864209",
                                                    "new_pin_confirm": "864209"})
        assert r.status_code == 204, r.text
        assert [s.result for s in await _actions("account.pin_changed")] == ["ok"]
        assert len(await _actions("account.pin_set")) == 1
    finally:
        await c.aclose()


async def test_chua_doi_mat_khau_tam_thi_chua_dat_duoc_pin(owner_api: Api, app: object) -> None:
    """Mật khẩu tạm chưa đổi → mọi route ngoài /account/password vẫn bị chặn, kể cả đặt PIN lần đầu."""
    await verify_pin(owner_api)
    r = await owner_api.send("POST", "/users",
                             {"display_name": "Anh Tạm", "email": "tam@example.vn", "role": "manager"})
    temp = r.json()["temp_password"]
    c = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")  # type: ignore[arg-type]
    try:
        api = Api(c)
        assert (await api.send("POST", "/auth/login", {"email": "tam@example.vn", "password": temp})).status_code == 200
        r = await api.send("POST", "/account/pin", {"current_password": temp, "new_pin": NEW_PIN,
                                                    "new_pin_confirm": NEW_PIN})
        assert r.status_code == 403 and r.json()["code"] == "PASSWORD_CHANGE_REQUIRED"
        row = await _sql_one("SELECT pin_hash IS NULL AS no_pin FROM core.users WHERE email = 'tam@example.vn'")
        assert row.no_pin
    finally:
        await c.aclose()


async def test_owner_van_doi_pin_nhu_cu(owner_api: Api) -> None:
    r = await owner_api.send("POST", "/account/pin", {"current_password": OWNER["password"], "new_pin": NEW_PIN,
                                                      "new_pin_confirm": NEW_PIN})
    assert r.status_code == 204, r.text
    assert (await owner_api.send("POST", "/auth/pin/verify", {"pin": OWNER["pin"]})).status_code == 401
    assert (await owner_api.send("POST", "/auth/pin/verify", {"pin": NEW_PIN})).status_code == 200
    assert [s.result for s in await _actions("account.pin_changed")] == ["ok"]
    assert await _actions("account.pin_set") == []


# ─── F088: chỉ còn một đường đổi PIN ──────────────────────────────────────────────────────────────

async def test_khong_con_put_auth_pin(owner_api: Api) -> None:
    await verify_pin(owner_api)
    r = await owner_api.send("PUT", "/auth/pin", {"current_pin": OWNER["pin"], "new_pin": NEW_PIN})
    assert r.status_code in (404, 405), r.text  # đường PUT /auth/pin đã gỡ
    assert (await owner_api.send("POST", "/auth/pin/verify", {"pin": OWNER["pin"]})).status_code == 200  # PIN không đổi
    assert "pin.change" not in service.PIN_OPERATIONS
    assert await _actions("auth.pin_changed") == []


# ─── F115: phiên PIN chỉ gia hạn bởi thao tác ghi do người bấm ────────────────────────────────────────

def _token(api: Api) -> str:
    tok = api.c.cookies.get(service.SESSION_COOKIE)
    assert tok
    return tok


async def _pin_until(api: Api) -> Any:
    return (await _sql_one("SELECT pin_verified_until AS p FROM core.sessions WHERE token_hash = :h",
                           {"h": service.token_digest(_token(api))})).p


async def _set_pin_until(api: Api, minutes: int) -> None:
    async with admin_sessionmaker()() as db:
        await db.execute(text("UPDATE core.sessions SET pin_verified_until = now() + make_interval(mins => :m) "
                              "WHERE token_hash = :h"), {"m": minutes, "h": service.token_digest(_token(api))})
        await db.commit()


async def test_get_tu_lam_moi_khong_gia_han_phien_pin(owner_api: Api) -> None:
    """Giao diện tự gọi GET /header (60 giây), /auth/me, thông báo… — không được giữ phiên PIN sống mãi."""
    await verify_pin(owner_api)
    await _set_pin_until(owner_api, 1)
    before = await _pin_until(owner_api)
    for url in ("/header", "/auth/me", "/account"):
        assert (await owner_api.get(url)).status_code == 200, url
    assert await _pin_until(owner_api) == before


async def test_thao_tac_ghi_cua_nguoi_dung_van_gia_han_phien_pin(owner_api: Api) -> None:
    await verify_pin(owner_api)
    await _set_pin_until(owner_api, 1)
    before = await _pin_until(owner_api)
    r = await owner_api.send("PATCH", "/account", {"display_name": "Anh Cơ Mới"})
    assert r.status_code == 200, r.text
    after = await _pin_until(owner_api)
    assert after - before > timedelta(minutes=20)  # gần +30 phút kể từ bây giờ


async def test_phien_pin_het_han_that_khi_chi_co_poll(owner_api: Api) -> None:
    await verify_pin(owner_api)
    await _set_pin_until(owner_api, -1)  # đã quá hạn
    assert (await owner_api.get("/header")).status_code == 200  # poll không hồi sinh
    r = await owner_api.send("POST", "/users", {"display_name": "X", "email": "x@example.vn", "role": "operator"})
    assert r.status_code == 423  # phải nhập lại PIN
