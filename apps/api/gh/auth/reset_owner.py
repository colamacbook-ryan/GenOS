"""CLI `python -m gh.auth.reset_owner` — đặt lại mật khẩu Owner khi Owner quên (genh reset-password gọi vào).

Chạy trong container api (`docker compose exec -T api …`), KHÔNG đụng dữ liệu nào khác:
  - tìm Owner (vai trò `owner`) của tổ chức đầu tiên;
  - đặt mật khẩu tạm mới (tự sinh, hoặc đọc 1 dòng từ stdin với `--password-stdin`), băm bằng đúng hàm
    đăng nhập dùng (gh.crypto.hash_secret);
  - thu hồi mọi phiên đang mở của Owner, gỡ khoá PIN (nếu đang bị khoá do nhập sai);
  - bật cờ must_change_password (v0.1.19) — Console buộc đặt mật khẩu mới ngay lần đăng nhập kế tiếp;
  - ghi Action Log (actor_type "system");
  - in JSON {"email", "temp_password"} ra stdout.

Mã thoát: 0 xong · 2 chưa có Owner (chưa qua bước 2 trình thiết lập) · 3 mật khẩu stdin quá ngắn.
"""

import argparse
import asyncio
import json
import logging
import sys
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gh.auth import login_guard
from gh.chassis import actionlog
from gh.config import get_settings
from gh.crypto import hash_secret, new_token
from gh.db import admin_sessionmaker, dispose_engine

log = logging.getLogger("gh.auth.reset_owner")

MIN_PASSWORD_LEN = 12  # cùng ngưỡng bước 2 trình thiết lập (gh/setup/routes.py)


class NoOwnerError(Exception):
    """Chưa có Owner nào — trình thiết lập chưa qua bước 2."""


@dataclass
class ResetResult:
    email: str
    temp_password: str
    sessions_revoked: int


async def reset_owner_password(db: AsyncSession, password: str | None = None) -> ResetResult:
    """Đặt lại mật khẩu Owner trong transaction `db` (caller commit)."""
    row = (await db.execute(text("""
        SELECT u.id, u.org_id, u.email FROM core.users u
        JOIN core.user_roles ur ON ur.user_id = u.id
        JOIN core.roles r ON r.id = ur.role_id
        JOIN core.organizations o ON o.id = u.org_id
        WHERE r.code = 'owner' AND u.deleted_at IS NULL
        ORDER BY o.created_at, u.created_at LIMIT 1"""))).one_or_none()
    if row is None:
        raise NoOwnerError
    temp = password if password is not None else new_token(12)
    await db.execute(text("""
        UPDATE core.users SET password_hash = :h, is_active = true, pin_failed = 0, pin_locked_until = NULL,
            must_change_password = true, updated_at = now()
        WHERE id = :u"""), {"h": hash_secret(temp), "u": row.id})
    revoked = (await db.execute(text(
        "UPDATE core.sessions SET revoked_at = now() WHERE user_id = :u AND revoked_at IS NULL"),
        {"u": row.id})).rowcount or 0  # type: ignore[attr-defined]
    await actionlog.record(db, org_id=row.org_id, actor_type="system", actor_id="system:genh",
                           action="auth.password_reset", target_type="user", target_id=str(row.id),
                           target_label=row.email, detail={"sessions_revoked": revoked, "via": "genh reset-password"})
    return ResetResult(email=row.email, temp_password=temp, sessions_revoked=revoked)


async def _clear_login_counter(email: str) -> None:
    """v0.1.46: gỡ khoá đăng nhập — bộ đếm email Owner và mọi bộ đếm IP (người dùng chung IP sau Tailscale Serve).
    Best-effort — Redis lỗi chỉ log, không làm hỏng lệnh."""
    from redis.asyncio import Redis

    try:
        r = Redis.from_url(get_settings().redis_url)
        try:
            await login_guard.clear_for_owner_reset(r, email)
        finally:
            await r.aclose()
    except Exception as e:  # noqa: BLE001 — best-effort
        log.warning("Không xoá được bộ đếm đăng nhập (%s)", type(e).__name__)


async def _main() -> int:
    parser = argparse.ArgumentParser(description="Đặt lại mật khẩu Owner (không mất dữ liệu)")
    parser.add_argument("--password-stdin", action="store_true", help="đọc mật khẩu mới từ 1 dòng stdin")
    args = parser.parse_args()
    password: str | None = None
    if args.password_stdin:
        password = sys.stdin.readline().rstrip("\r\n")
        if len(password) < MIN_PASSWORD_LEN:
            print(f"Mật khẩu cần ít nhất {MIN_PASSWORD_LEN} ký tự", file=sys.stderr)
            return 3
    try:
        async with admin_sessionmaker()() as db:
            try:
                result = await reset_owner_password(db, password)
            except NoOwnerError:
                print("Chưa có tài khoản Owner — hãy hoàn tất bước 2 trình thiết lập", file=sys.stderr)
                return 2
            await db.commit()
        await _clear_login_counter(result.email)
    finally:
        await dispose_engine()
    print(json.dumps({"email": result.email, "temp_password": result.temp_password}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))
