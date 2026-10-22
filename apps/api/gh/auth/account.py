"""/account — "Tài khoản của tôi" (v0.1.19): hồ sơ, mật khẩu, PIN, phiên đăng nhập của CHÍNH người đang đăng nhập.

Mọi vai trò đều dùng được (không cần quyền RBAC riêng) nhưng chỉ đụng tới tài khoản của mình. Mọi thay đổi ghi
Action Log. Mã PIN: người được mời (chưa có PIN) tự đặt lần đầu bằng mật khẩu đăng nhập; Owner đổi PIN cùng
đường đó. Mật khẩu/PIN băm và kiểm bằng đúng hàm đăng nhập dùng (gh.crypto.hash_secret / verify_secret).
Sai mật khẩu hiện tại → 422 lỗi theo ô (không phải 401, để Console không hiểu nhầm là mất phiên) và vẫn ghi
một dòng nhật ký `result=failed` (commit trước khi trả lỗi).
"""

import re
import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gh.auth import service
from gh.auth.deps import current_user
from gh.chassis import actionlog
from gh.crypto import hash_secret, verify_secret
from gh.db import DB
from gh.errors import ApiError, field_errors, not_found

router = APIRouter(prefix="/account", tags=["account"])

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")  # cùng quy tắc trình thiết lập (gh/setup/routes.py)
MIN_PASSWORD_LEN = 12
MAX_NAME_LEN = 100
WRONG_PASSWORD = "Mật khẩu hiện tại không đúng"


class ProfileIn(BaseModel):
    display_name: str | None = Field(default=None, max_length=200)
    email: str | None = Field(default=None, max_length=320)
    current_password: str | None = Field(default=None, max_length=1024)


class PasswordIn(BaseModel):
    current_password: str = Field(max_length=1024)
    new_password: str = Field(max_length=1024)


class PinIn(BaseModel):
    current_password: str = Field(max_length=1024)
    new_pin: str = Field(max_length=16)
    new_pin_confirm: str = Field(max_length=16)


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat().replace("+00:00", "Z") if dt else None


async def _log(db: AsyncSession, user: service.CurrentUser, action: str, *, result: str = "ok",
               detail: dict[str, Any] | None = None) -> None:
    await actionlog.record(db, org_id=user.org_id, actor_type="user", actor_id=user.actor_id, action=action,
                           target_type="user", target_id=str(user.id), target_label=user.email, result=result,
                           detail=detail, ip=user.ip)


async def _check_password(db: AsyncSession, user: service.CurrentUser, password: str | None, *, purpose: str) -> None:
    """Sai → ghi nhật ký thất bại, commit, trả 422 ở ô current_password."""
    h = (await db.execute(text("SELECT password_hash FROM core.users WHERE id = :u"), {"u": user.id})).scalar()
    if password and h is not None and verify_secret(h, password):
        return
    await _log(db, user, "account.password_check_failed", result="failed", detail={"purpose": purpose})
    await db.commit()
    raise field_errors({"current_password": WRONG_PASSWORD})


async def _revoke_others(db: AsyncSession, user: service.CurrentUser) -> int:
    return (await db.execute(text(
        "UPDATE core.sessions SET revoked_at = now() WHERE user_id = :u AND id <> :s AND revoked_at IS NULL "
        "AND expires_at > now()"), {"u": user.id, "s": user.session_id})).rowcount or 0  # type: ignore[attr-defined]


async def account_payload(db: AsyncSession, user: service.CurrentUser) -> dict[str, Any]:
    u = (await db.execute(text("""
        SELECT display_name, email, created_at, must_change_password, pin_hash IS NOT NULL AS has_pin
        FROM core.users WHERE id = :u"""), {"u": user.id})).one()
    rows = (await db.execute(text("""
        SELECT id, created_at, last_seen_at, host(ip) AS ip, user_agent, expires_at FROM core.sessions
        WHERE user_id = :u AND revoked_at IS NULL AND expires_at > now()
        ORDER BY (id = :s) DESC, coalesce(last_seen_at, created_at) DESC"""),
        {"u": user.id, "s": user.session_id})).all()
    return {
        "display_name": u.display_name, "email": u.email,
        "role": {"code": user.role_code, "name": user.role_name},
        "created_at": _iso(u.created_at), "must_change_password": bool(u.must_change_password),
        "has_pin": bool(u.has_pin),
        "sessions": [{"id": str(r.id), "created_at": _iso(r.created_at), "last_seen_at": _iso(r.last_seen_at),
                      "ip": r.ip, "user_agent": r.user_agent, "expires_at": _iso(r.expires_at),
                      "current": r.id == user.session_id} for r in rows],
    }


@router.get("")
async def get_account(user: service.CurrentUser = Depends(current_user), db: AsyncSession = DB) -> dict[str, Any]:
    return await account_payload(db, user)


@router.patch("")
async def update_profile(body: ProfileIn, user: service.CurrentUser = Depends(current_user),
                         db: AsyncSession = DB) -> dict[str, Any]:
    errors: dict[str, str] = {}
    changes: dict[str, Any] = {}
    if body.display_name is not None:
        name = " ".join(body.display_name.split())
        if not name:
            errors["display_name"] = "Tên hiển thị không được để trống"
        elif len(name) > MAX_NAME_LEN:
            errors["display_name"] = f"Tên hiển thị tối đa {MAX_NAME_LEN} ký tự"
        elif name != user.display_name:
            changes["display_name"] = name
    if body.email is not None:
        email = body.email.strip().lower()
        if not EMAIL_RE.match(email):
            errors["email"] = "Email chưa đúng định dạng"
        elif email != user.email.lower():
            taken = (await db.execute(text("SELECT 1 FROM core.users WHERE org_id = :o AND email = :e AND id <> :u"),
                                      {"o": user.org_id, "e": email, "u": user.id})).scalar()
            if taken:
                errors["email"] = "Email này đã có tài khoản khác dùng"
            else:
                changes["email"] = email
    if "email" in changes and not body.current_password:
        errors["current_password"] = "Nhập mật khẩu hiện tại để đổi email"
    if errors:
        raise field_errors(errors)
    if "email" in changes:
        await _check_password(db, user, body.current_password, purpose="email.change")
    if changes:
        sets = ", ".join(f"{k} = :{k}" for k in changes)
        await db.execute(text(f"UPDATE core.users SET {sets}, updated_at = now() WHERE id = :u"),  # noqa: S608
                         {**changes, "u": user.id})
        detail: dict[str, Any] = {"fields": sorted(changes)}
        if "email" in changes:
            detail["old_email"] = user.email
            user.email = changes["email"]
        if "display_name" in changes:
            detail["old_display_name"] = user.display_name
            user.display_name = changes["display_name"]
        await _log(db, user, "account.profile_updated", detail=detail)
    return await account_payload(db, user)


@router.post("/password")
async def change_password(body: PasswordIn, user: service.CurrentUser = Depends(current_user),
                          db: AsyncSession = DB) -> dict[str, Any]:
    if len(body.new_password) < MIN_PASSWORD_LEN:
        raise field_errors({"new_password": f"Mật khẩu mới cần ít nhất {MIN_PASSWORD_LEN} ký tự"})
    await _check_password(db, user, body.current_password, purpose="password.change")
    if body.new_password == body.current_password:
        raise field_errors({"new_password": "Mật khẩu mới phải khác mật khẩu hiện tại"})
    await db.execute(text("UPDATE core.users SET password_hash = :h, must_change_password = false, "
                          "updated_at = now() WHERE id = :u"), {"h": hash_secret(body.new_password), "u": user.id})
    revoked = await _revoke_others(db, user)
    await _log(db, user, "account.password_changed",
               detail={"sessions_revoked": revoked, "was_forced": user.must_change_password})
    return {"sessions_revoked": revoked}


@router.post("/pin", status_code=204)
async def change_pin(body: PinIn, response: Response, user: service.CurrentUser = Depends(current_user),
                     db: AsyncSession = DB) -> Response:
    # v0.1.61 (F071/F097): người chưa có PIN (Quản lý được mời…) ĐẶT PIN LẦN ĐẦU ở đây, cũng bằng mật khẩu đăng nhập
    # (như Owner đổi PIN). Trước đây trả 409 NO_PIN nên người được giao quyền duyệt không có đường nào đặt PIN.
    had_pin = bool((await db.execute(text("SELECT pin_hash IS NOT NULL FROM core.users WHERE id = :u"),
                                     {"u": user.id})).scalar())
    errors: dict[str, str] = {}
    if not service.valid_pin(body.new_pin):
        errors["new_pin"] = "PIN gồm đúng 6 chữ số"
    elif body.new_pin_confirm != body.new_pin:
        errors["new_pin_confirm"] = "Hai lần nhập mã PIN chưa khớp"
    if errors:
        raise field_errors(errors)
    await _check_password(db, user, body.current_password, purpose="pin.change")
    await service.set_pin(db, user.id, body.new_pin)
    await _log(db, user, "account.pin_changed" if had_pin else "account.pin_set")  # không ghi PIN / mật khẩu
    response.status_code = 204
    return response


@router.post("/sessions/revoke-others")
async def revoke_other_sessions(user: service.CurrentUser = Depends(current_user),
                                db: AsyncSession = DB) -> dict[str, Any]:
    revoked = await _revoke_others(db, user)
    await _log(db, user, "account.sessions_revoked", detail={"sessions_revoked": revoked, "scope": "others"})
    return {"sessions_revoked": revoked}


@router.delete("/sessions/{session_id}", status_code=204)
async def revoke_one_session(session_id: uuid.UUID, response: Response,
                             user: service.CurrentUser = Depends(current_user), db: AsyncSession = DB) -> Response:
    if session_id == user.session_id:
        raise ApiError(409, "CURRENT_SESSION", "Đây là phiên đang dùng — hãy bấm Đăng xuất")
    done = (await db.execute(text(
        "UPDATE core.sessions SET revoked_at = now() WHERE id = :s AND user_id = :u AND revoked_at IS NULL "
        "RETURNING id"), {"s": session_id, "u": user.id})).scalar()
    if done is None:
        raise not_found("Phiên đăng nhập")
    await _log(db, user, "account.session_revoked", detail={"session_id": str(session_id)})
    response.status_code = 204
    return response
