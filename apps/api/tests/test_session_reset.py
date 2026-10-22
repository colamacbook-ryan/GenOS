"""v0.1.18: phiên đăng nhập trượt 7 ngày + `python -m gh.auth.reset_owner` (genh reset-password)."""

import json
import os
import subprocess
import sys
from pathlib import Path

from sqlalchemy import text

from gh.auth.reset_owner import reset_owner_password
from gh.config import get_settings
from gh.db import admin_sessionmaker, sessionmaker
from tests.conftest import OWNER, Api

API_DIR = Path(__file__).resolve().parents[1]


async def test_default_ttl_is_seven_days(owner_api: Api) -> None:
    assert get_settings().session_ttl_hours == 168
    async with admin_sessionmaker()() as db:
        left = (await db.execute(text(
            "SELECT extract(epoch FROM max(expires_at) - now()) FROM core.sessions"))).scalar()
    assert 167 * 3600 < float(left) <= 168 * 3600


async def test_session_slides_when_less_than_half_ttl_left(owner_api: Api) -> None:
    async with admin_sessionmaker()() as db:
        await db.execute(text("UPDATE core.sessions SET expires_at = now() + interval '10 hours' "
                              "WHERE revoked_at IS NULL"))
        await db.commit()
    r = await owner_api.get("/auth/me")
    assert r.status_code == 200
    cookies = r.headers.get_list("set-cookie")
    assert any(c.startswith("gh_session=") for c in cookies) and any(c.startswith("gh_csrf=") for c in cookies)
    async with admin_sessionmaker()() as db:
        left = (await db.execute(text(
            "SELECT extract(epoch FROM max(expires_at) - now()) FROM core.sessions WHERE revoked_at IS NULL"))).scalar()
    assert float(left) > 167 * 3600
    # Còn hơn nửa TTL → không đụng tới, không đặt lại cookie.
    r = await owner_api.get("/auth/me")
    assert not r.headers.get_list("set-cookie")
    # CSRF vẫn khớp sau khi gia hạn (cookie đặt lại cùng giá trị).
    assert (await owner_api.send("POST", "/auth/logout")).status_code == 204


async def test_reset_owner_password_keeps_data_and_revokes_sessions(owner_api: Api) -> None:
    async with admin_sessionmaker()() as db:
        await db.execute(text("UPDATE core.users SET pin_failed = 3, pin_locked_until = now() + interval '1 hour'"))
        result = await reset_owner_password(db)
        await db.commit()
    assert result.email == OWNER["email"] and len(result.temp_password) >= 12 and result.sessions_revoked == 1
    # Phiên cũ bị thu hồi.
    assert (await owner_api.get("/auth/me")).status_code == 401
    # Mật khẩu cũ hết dùng được, mật khẩu tạm đăng nhập được.
    r = await owner_api.send("POST", "/auth/login", {"email": OWNER["email"], "password": OWNER["password"]})
    assert r.status_code == 401
    r = await owner_api.send("POST", "/auth/login", {"email": OWNER["email"], "password": result.temp_password})
    assert r.status_code == 200, r.text
    async with sessionmaker()() as db:
        row = (await db.execute(text("SELECT pin_failed, pin_locked_until FROM core.users WHERE email = :e"),
                                {"e": OWNER["email"]})).one()
        assert row.pin_failed == 0 and row.pin_locked_until is None
        org = (await db.execute(text("SELECT name FROM core.organizations"))).scalar()
        assert org == "Genesis Việt"  # dữ liệu còn nguyên
    async with admin_sessionmaker()() as db:
        log = (await db.execute(text("SELECT actor_type, actor_id FROM ops.action_log "
                                     "WHERE action = 'auth.password_reset'"))).one()
    assert log.actor_type == "system"


def _run_cli(*args: str, stdin: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, "-m", "gh.auth.reset_owner", *args], cwd=API_DIR, env=dict(os.environ),
                          input=stdin, capture_output=True, text=True, timeout=60)


async def test_cli_prints_json_and_accepts_stdin(owner_api: Api) -> None:
    p = _run_cli()
    assert p.returncode == 0, p.stderr
    out = json.loads(p.stdout)
    assert out["email"] == OWNER["email"] and len(out["temp_password"]) >= 12
    p = _run_cli("--password-stdin", stdin="mat-khau-moi-cua-owner\n")
    assert p.returncode == 0, p.stderr
    assert json.loads(p.stdout)["temp_password"] == "mat-khau-moi-cua-owner"
    r = await owner_api.send("POST", "/auth/login", {"email": OWNER["email"], "password": "mat-khau-moi-cua-owner"})
    assert r.status_code == 200
    assert _run_cli("--password-stdin", stdin="ngan\n").returncode == 3


async def test_cli_without_owner_exits_2(fresh_db: str) -> None:
    p = _run_cli()
    assert p.returncode == 2 and "bước 2" in p.stderr
