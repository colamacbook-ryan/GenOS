"""/auth — đăng nhập, đăng xuất, thông tin người dùng, PIN."""

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gh.auth import login_guard, service
from gh.auth.deps import client_ip, current_user
from gh.chassis import actionlog
from gh.config import get_settings
from gh.db import DB
from gh.errors import ApiError
from gh.gen import store as gen_store

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginIn(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=1024)


class PinIn(BaseModel):
    pin: str = Field(max_length=16)


def set_session_cookies(response: Response, s: service.NewSession) -> None:
    secure = get_settings().cookie_secure
    response.set_cookie(service.SESSION_COOKIE, s.token, expires=s.expires_at, httponly=True, secure=secure,
                        samesite="strict", path="/")
    response.set_cookie(service.CSRF_COOKIE, s.csrf, expires=s.expires_at, httponly=False, secure=secure,
                        samesite="strict", path="/")


def clear_session_cookies(response: Response) -> None:
    response.delete_cookie(service.SESSION_COOKIE, path="/")
    response.delete_cookie(service.CSRF_COOKIE, path="/")


async def me_payload(db: AsyncSession, user: service.CurrentUser) -> dict[str, Any]:
    org = (await db.execute(text("SELECT id, name, timezone, currency FROM core.organizations WHERE id = :o"),
                            {"o": user.org_id})).one()
    return {
        "id": str(user.id), "email": user.email, "display_name": user.display_name,
        "role": {"code": user.role_code, "name": user.role_name},
        "org": {"id": str(org.id), "name": org.name, "timezone": org.timezone, "currency": org.currency.strip()},
        "addressing": user.addressing,
        "pin_verified_until": _iso(user.pin_verified_until),
        "permissions": user.permissions,
        "must_change_password": user.must_change_password,
        # v0.1.21: tính năng theo người (Gen — cờ `gen.enabled`, v1 chỉ Owner; gh.gen.store).
        "features": {"gen": gen_store.available(await gen_store.get_settings(db, user.org_id), user)},
    }


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat().replace("+00:00", "Z") if dt else None


def _mask_email(raw: str) -> str | None:
    """v0.1.35 (F-43): Action Log đăng nhập sai không ghi email thô. Có '@' → `o***@example.vn`; không có '@'
    (người dùng gõ nhầm mật khẩu vào ô email) → không ghi gì."""
    v = raw.strip().lower()
    local, at, domain = v.partition("@")
    if not at or not local or not domain:
        return None
    return f"{local[0]}***@{domain}"


def _pin_locked(locked_until: datetime | None) -> ApiError:
    until = _iso(locked_until)
    # v0.1.35: detail KHÔNG chứa giờ ISO UTC thô (web hiện thẳng detail) — giờ mở khoá ở khoá ngoài `locked_until`,
    # web tự định dạng theo múi giờ tổ chức.
    return ApiError(423, "PIN_LOCKED", "Mã PIN đang bị khoá do nhập sai nhiều lần", "Thử lại sau ít phút",
                    locked_until=until)


#: Đường dẫn đầy đủ (genh có thể không nằm trong PATH) — khớp RESET_PASSWORD_COMMAND của apps/web LoginPage.
RESET_PASSWORD_COMMAND = "~/.gen-harness/bin/genh reset-password"


@router.post("/login")
async def login(body: LoginIn, request: Request, response: Response,
                db: AsyncSession = DB) -> dict[str, Any]:
    ip = client_ip(request)
    redis = request.app.state.redis
    hit = await login_guard.blocked(redis, ip, body.email)
    if hit is not None:
        retry, scope = hit
        org_id = (await db.execute(text("SELECT id FROM core.organizations ORDER BY created_at LIMIT 1"))).scalar()
        if org_id is not None and await login_guard.log_once(redis, body.email):
            await actionlog.record(db, org_id=org_id, actor_type="system", actor_id="system:auth",
                                   action="auth.login_rate_limited", result="blocked",
                                   detail={"email_masked": _mask_email(body.email), "scope": scope}, ip=ip)
            await db.commit()
        minutes = max(1, -(-retry // 60))
        if scope == "ip":
            # Bộ đếm IP chung cả mạng (docker-proxy/Tailscale Serve): Owner bấm Đặt lại mật khẩu cho nhân viên KHÔNG
            # gỡ được — chỉ đợi, hoặc Owner chạy genh reset-password (xoá mọi bộ đếm IP).
            raise ApiError(429, "LOGIN_RATE_LIMITED",
                           f"Có quá nhiều lần đăng nhập sai từ cùng mạng — đợi khoảng {minutes} phút rồi thử lại",
                           f"Đợi khoảng {minutes} phút. Owner: có thể gỡ ngay bằng lệnh {RESET_PASSWORD_COMMAND} trên "
                           "máy chủ (lệnh này cấp mật khẩu tạm MỚI cho Owner và đăng xuất mọi phiên Owner).",
                           retry_after_s=retry, scope=scope)
        raise ApiError(429, "LOGIN_RATE_LIMITED",
                       f"Đăng nhập sai quá nhiều lần — đợi khoảng {minutes} phút rồi thử lại",
                       "Nhân viên: nhờ Owner bấm Đặt lại mật khẩu. Owner: chạy "
                       f"{RESET_PASSWORD_COMMAND} trên máy chủ (lệnh này cấp mật khẩu tạm MỚI cho Owner).",
                       retry_after_s=retry, scope=scope)
    found = await service.login(db, body.email, body.password)
    if found is None:
        await login_guard.record_failure(redis, ip, body.email)
        org_id = (await db.execute(text("SELECT id FROM core.organizations ORDER BY created_at LIMIT 1"))).scalar()
        if org_id is not None:
            await actionlog.record(db, org_id=org_id, actor_type="system", actor_id="system:auth",
                                   action="auth.login_failed", result="failed",
                                   detail={"email_masked": _mask_email(body.email)}, ip=ip)
            await db.commit()
        raise ApiError(401, "INVALID_CREDENTIALS", "Email hoặc mật khẩu không đúng")
    await login_guard.clear_email(redis, body.email)
    new = await service.create_session(db, found["id"], ip=ip, user_agent=request.headers.get("user-agent"))
    user = await service.load_session(db, new.token)
    assert user is not None
    await actionlog.record(db, org_id=user.org_id, actor_type="user", actor_id=user.actor_id,
                           action="auth.login", ip=ip)
    set_session_cookies(response, new)
    return await me_payload(db, user)


@router.post("/logout", status_code=204)
async def logout(response: Response, user: service.CurrentUser = Depends(current_user),
                 db: AsyncSession = DB) -> Response:
    await service.revoke_session(db, user.session_id)
    await actionlog.record(db, org_id=user.org_id, actor_type="user", actor_id=user.actor_id,
                           action="auth.logout", ip=user.ip)
    response.status_code = 204
    clear_session_cookies(response)
    return response


@router.get("/me")
async def me(user: service.CurrentUser = Depends(current_user),
             db: AsyncSession = DB) -> dict[str, Any]:
    return await me_payload(db, user)


@router.post("/pin/verify")
async def pin_verify(body: PinIn, user: service.CurrentUser = Depends(current_user),
                     db: AsyncSession = DB) -> dict[str, Any]:
    result = await service.verify_pin(db, user, body.pin)
    if result.ok:
        return {"pin_verified_until": _iso(result.pin_verified_until)}
    await db.commit()  # số lần sai và dòng nhật ký phải được lưu dù trả lỗi
    if result.no_pin:
        raise ApiError(409, "PIN_NOT_SET", service.PIN_NOT_SET)
    if result.locked_until is not None:
        raise _pin_locked(result.locked_until)
    raise ApiError(401, "PIN_INVALID", "Mã PIN không đúng", attempts_left=result.attempts_left)
