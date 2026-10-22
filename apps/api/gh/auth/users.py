"""/users — Quản lý người dùng (v0.1.22, Đợt B1): Đội ngũ.

Chỉ vai trò có `roles.manage` (mặc định Owner). Mọi thao tác ghi cần phiên PIN (`user.manage`, đổi vai trò dùng
`roles.change` như ma trận quyền) và ghi Action Log `user.*`. Bất biến:
  - không đổi vai trò / khoá / đặt lại mật khẩu của CHÍNH mình (dùng "Tài khoản của tôi");
  - không bao giờ để tổ chức mất Owner cuối cùng còn hoạt động;
  - chỉ mời / gán các vai trò dưới Owner — Owner (cần mã PIN riêng) chỉ tạo ở trình thiết lập.
Mời và đặt lại mật khẩu trả mật khẩu tạm ĐÚNG MỘT LẦN (chưa có SMTP) + bật `must_change_password`; khoá tài khoản
và đặt lại mật khẩu thu hồi mọi phiên đang mở của người đó.
"""

import uuid
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gh import notifications
from gh.auth import login_guard, rbac, service
from gh.auth.account import EMAIL_RE
from gh.auth.deps import require, require_pin
from gh.chassis import actionlog
from gh.crypto import hash_secret
from gh.crypto import temp_password as new_temp_password
from gh.db import DB
from gh.errors import ApiError, field_errors, not_found

router = APIRouter(prefix="/users", tags=["users"])
MANAGE = require("roles.manage")

InviteRole = Literal["manager", "operator", "agent_staff", "auditor"]
ASSIGNABLE_ROLES: tuple[str, ...] = ("manager", "operator", "agent_staff", "auditor")
MAX_NAME_LEN = 100


class InviteIn(BaseModel):
    display_name: str = Field(max_length=200)
    email: str = Field(max_length=320)
    role: InviteRole


class RoleIn(BaseModel):
    role: InviteRole


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat().replace("+00:00", "Z") if dt else None


USER_SELECT = """
    SELECT u.id, u.display_name, u.email, u.is_active, u.must_change_password, u.last_login_at, u.created_at,
           r.code AS role_code, r.name AS role_name
    FROM core.users u
    JOIN core.user_roles ur ON ur.user_id = u.id
    JOIN core.roles r ON r.id = ur.role_id
    WHERE u.org_id = :o AND u.deleted_at IS NULL"""


def _out(r: Any, me: service.CurrentUser) -> dict[str, Any]:
    return {"id": str(r.id), "display_name": r.display_name, "email": r.email,
            "role": {"code": r.role_code, "name": r.role_name},
            "status": "active" if r.is_active else "inactive",
            "must_change_password": bool(r.must_change_password),
            "last_login_at": _iso(r.last_login_at), "created_at": _iso(r.created_at), "is_self": r.id == me.id}


async def _one(db: AsyncSession, me: service.CurrentUser, user_id: uuid.UUID) -> Any:
    row = (await db.execute(text(USER_SELECT + " AND u.id = :u"), {"o": me.org_id, "u": user_id})).first()
    if row is None:
        raise not_found("Người dùng")
    return row


def _not_self(me: service.CurrentUser, row: Any, what: str) -> None:
    if row.id == me.id:
        raise ApiError(409, "SELF_CHANGE", f"Không {what} của chính mình ở đây — dùng Tài khoản của tôi")


async def _guard_last_owner(db: AsyncSession, me: service.CurrentUser, row: Any) -> None:
    """Người này là Owner đang hoạt động cuối cùng → từ chối (tổ chức không bao giờ mất Owner)."""
    if row.role_code != rbac.OWNER or not row.is_active:
        return
    # Khoá các dòng Owner đang hoạt động để hai thao tác song song không cùng thấy "còn Owner khác".
    await db.execute(text("""
        SELECT u.id FROM core.users u JOIN core.user_roles ur ON ur.user_id = u.id
        JOIN core.roles r ON r.id = ur.role_id
        WHERE u.org_id = :o AND r.code = 'owner' AND u.is_active AND u.deleted_at IS NULL
        ORDER BY u.id FOR UPDATE OF u"""), {"o": me.org_id})
    others = (await db.execute(text("""
        SELECT count(*) FROM core.users u JOIN core.user_roles ur ON ur.user_id = u.id
        JOIN core.roles r ON r.id = ur.role_id
        WHERE u.org_id = :o AND r.code = 'owner' AND u.is_active AND u.deleted_at IS NULL AND u.id <> :u"""),
        {"o": me.org_id, "u": row.id})).scalar_one()
    if not others:
        raise ApiError(409, "LAST_OWNER", "Đây là Owner cuối cùng — tổ chức phải luôn có ít nhất một Owner")


async def _revoke_all(db: AsyncSession, user_id: uuid.UUID) -> int:
    return (await db.execute(text(
        "UPDATE core.sessions SET revoked_at = now() WHERE user_id = :u AND revoked_at IS NULL "
        "AND expires_at > now()"), {"u": user_id})).rowcount or 0  # type: ignore[attr-defined]


async def _log(db: AsyncSession, me: service.CurrentUser, action: str, row: Any,
               detail: dict[str, Any] | None = None) -> None:
    await actionlog.record(db, org_id=me.org_id, actor_type="user", actor_id=me.actor_id, action=action,
                           target_type="user", target_id=str(row.id), target_label=row.email, detail=detail,
                           ip=me.ip)


async def _role_id(db: AsyncSession, org_id: uuid.UUID, code: str) -> uuid.UUID:
    rid: uuid.UUID = (await db.execute(text("SELECT id FROM core.roles WHERE org_id = :o AND code = :c"),
                                       {"o": org_id, "c": code})).scalar_one()
    return rid


@router.get("")
async def list_users(me: service.CurrentUser = Depends(MANAGE), db: AsyncSession = DB) -> dict[str, Any]:
    rows = (await db.execute(text(USER_SELECT + """
        ORDER BY u.is_active DESC, (r.code = 'owner') DESC, lower(u.display_name)"""), {"o": me.org_id})).all()
    return {"items": [_out(r, me) for r in rows],
            "roles": [{"code": rd.code, "name": rd.name, "meta": rd.meta, "assignable": rd.code in ASSIGNABLE_ROLES}
                      for rd in rbac.ROLES]}


@router.post("", status_code=201)
async def invite_user(body: InviteIn, me: service.CurrentUser = Depends(MANAGE),
                      _pin: Any = Depends(require_pin("user.manage")), db: AsyncSession = DB) -> dict[str, Any]:
    # Cùng quy tắc bước 10 của trình thiết lập (gh/setup/routes.py::step10) và Tài khoản của tôi.
    errors: dict[str, str] = {}
    name, email = " ".join(body.display_name.split()), body.email.strip().lower()
    if not name:
        errors["display_name"] = "Nhập tên hiển thị"
    elif len(name) > MAX_NAME_LEN:
        errors["display_name"] = f"Tên hiển thị tối đa {MAX_NAME_LEN} ký tự"
    if not EMAIL_RE.match(email):
        errors["email"] = "Email chưa đúng định dạng"
    elif (await db.execute(text("SELECT 1 FROM core.users WHERE org_id = :o AND email = :e"),
                           {"o": me.org_id, "e": email})).scalar():
        errors["email"] = "Email này đã có tài khoản"
    if errors:
        raise field_errors(errors)
    temp_password = new_temp_password()
    uid = (await db.execute(text("""
        INSERT INTO core.users (org_id, email, display_name, password_hash, must_change_password)
        VALUES (:o, :e, :n, :p, true) RETURNING id"""),
        {"o": me.org_id, "e": email, "n": name, "p": hash_secret(temp_password)})).scalar_one()
    await db.execute(text("INSERT INTO core.user_roles (user_id, role_id) VALUES (:u, :r)"),
                     {"u": uid, "r": await _role_id(db, me.org_id, body.role)})
    row = await _one(db, me, uid)
    await _log(db, me, "user.invited", row, {"role": body.role})
    return {"user": _out(row, me), "temp_password": temp_password}


@router.patch("/{user_id}/role")
async def change_role(user_id: uuid.UUID, body: RoleIn, request: Request, me: service.CurrentUser = Depends(MANAGE),
                      _pin: Any = Depends(require_pin("roles.change")), db: AsyncSession = DB) -> dict[str, Any]:
    row = await _one(db, me, user_id)
    _not_self(me, row, "đổi vai trò")
    if row.role_code == body.role:
        return _out(row, me)
    await _guard_last_owner(db, me, row)
    await db.execute(text("DELETE FROM core.user_roles WHERE user_id = :u"), {"u": row.id})
    await db.execute(text("INSERT INTO core.user_roles (user_id, role_id) VALUES (:u, :r)"),
                     {"u": row.id, "r": await _role_id(db, me.org_id, body.role)})
    await db.execute(text("UPDATE core.users SET updated_at = now() WHERE id = :u"), {"u": row.id})
    await _log(db, me, "user.role_changed", row, {"from": row.role_code, "to": body.role})
    new_name = next((rd.name for rd in rbac.ROLES if rd.code == body.role), body.role)
    await notifications.notify(db, me.org_id, [row.id], kind="user.role_changed", title="Vai trò của bạn đã đổi",
                               body=f"{me.display_name} đã đổi vai trò của bạn thành {new_name}.", link="/account",
                               redis=request.app.state.redis)
    return _out(await _one(db, me, user_id), me)


@router.post("/{user_id}/deactivate")
async def deactivate_user(user_id: uuid.UUID, me: service.CurrentUser = Depends(MANAGE),
                          _pin: Any = Depends(require_pin("user.manage")), db: AsyncSession = DB) -> dict[str, Any]:
    row = await _one(db, me, user_id)
    _not_self(me, row, "khoá tài khoản")
    if not row.is_active:
        return _out(row, me)
    await _guard_last_owner(db, me, row)
    await db.execute(text("UPDATE core.users SET is_active = false, updated_at = now() WHERE id = :u"),
                     {"u": row.id})
    revoked = await _revoke_all(db, row.id)
    await _log(db, me, "user.deactivated", row, {"sessions_revoked": revoked})
    return _out(await _one(db, me, user_id), me)


@router.post("/{user_id}/reactivate")
async def reactivate_user(user_id: uuid.UUID, request: Request, me: service.CurrentUser = Depends(MANAGE),
                          _pin: Any = Depends(require_pin("user.manage")), db: AsyncSession = DB) -> dict[str, Any]:
    row = await _one(db, me, user_id)
    if row.is_active:
        return _out(row, me)
    await db.execute(text("UPDATE core.users SET is_active = true, updated_at = now() WHERE id = :u"),
                     {"u": row.id})
    await _log(db, me, "user.reactivated", row)
    await notifications.notify(db, me.org_id, [row.id], kind="user.reactivated", title="Tài khoản đã được mở khoá",
                               body=f"{me.display_name} đã mở khoá tài khoản của bạn.", redis=request.app.state.redis)
    return _out(await _one(db, me, user_id), me)


@router.post("/{user_id}/reset-password")
async def reset_password(user_id: uuid.UUID, request: Request, me: service.CurrentUser = Depends(MANAGE),
                         _pin: Any = Depends(require_pin("user.manage")), db: AsyncSession = DB) -> dict[str, Any]:
    row = await _one(db, me, user_id)
    _not_self(me, row, "đặt lại mật khẩu")
    temp_password = new_temp_password()
    await db.execute(text("UPDATE core.users SET password_hash = :h, must_change_password = true, "
                          "updated_at = now() WHERE id = :u"), {"h": hash_secret(temp_password), "u": row.id})
    revoked = await _revoke_all(db, row.id)
    await login_guard.clear_email(request.app.state.redis, row.email)  # v0.1.46: gỡ khoá đăng nhập (best-effort)
    await _log(db, me, "user.password_reset", row, {"sessions_revoked": revoked})
    await notifications.notify(db, me.org_id, [row.id], kind="user.password_reset", title="Mật khẩu đã được đặt lại",
                               body=f"{me.display_name} đã đặt lại mật khẩu của bạn. Nếu không phải bạn yêu cầu, "
                                    "hãy báo Owner.", link="/account", redis=request.app.state.redis)
    return {"user": _out(await _one(db, me, user_id), me), "temp_password": temp_password}
