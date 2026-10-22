"""v0.1.45 (F-58) — `system.manage` luôn cần phạm vi ALL.

Cấu hình hệ thống là của cả tổ chức: vai trò có `system.manage` = team/assigned KHÔNG được ghi bất kỳ route nào dùng
quyền này (kể cả các file vẫn gọi `require("system.manage")` với phạm vi mặc định — backups/offsite/update/health),
vì `gh.auth.deps.require()` ép phạm vi ALL cho mọi quyền thuộc `ALL_ONLY`. Có `all` thì không bị 403 (route trả mã
khác: 202/409/422/423 tuỳ thân yêu cầu)."""

import uuid

import pytest
from sqlalchemy import text

from gh.auth import deps, rbac, service
from gh.db import admin_sessionmaker
from gh.errors import ApiError
from tests.conftest import Api
from tests.test_rbac_api import login_as

ROUTES: list[tuple[str, str, object]] = [
    ("POST", "/providers", {}),
    ("PATCH", "/providers/chain", {}),
    ("POST", "/channels/zalo/login", {}),
    ("PATCH", "/boundaries/khong-co", {}),
    ("PATCH", "/retention-policies", {}),
    ("POST", "/system/backups", None),                      # Sao lưu ngay (system_api/backups.py)
    ("POST", "/system/update", None),                       # Cập nhật (system_api/update.py)
    ("POST", "/system/offsite/run", None),                  # Sao lưu ra ổ ngoài ngay (system_api/offsite.py)
    ("PUT", "/system/ai-cost/budget", {}),                  # Trần chi phí (system_api/health.py)
    ("PUT", f"/system/ai-cost/prices/{uuid.uuid4()}", {}),  # Giá model (system_api/health.py)
    ("POST", "/mcp/servers", {}),
    ("POST", "/plugins/local", {}),
    ("POST", "/agents", {}),
]


async def _set_manager_scope(scope: str) -> None:
    async with admin_sessionmaker()() as s:
        await s.execute(text("""INSERT INTO core.role_permissions (role_id, permission_code, scope)
                                SELECT id, p, :s FROM core.roles, unnest(ARRAY['system.read', 'system.manage']) p
                                WHERE code = 'manager'
                                ON CONFLICT (role_id, permission_code) DO UPDATE SET scope = :s"""), {"s": scope})
        await s.commit()


@pytest.mark.parametrize("scope", [rbac.TEAM, rbac.ASSIGNED])
async def test_system_manage_below_all_is_forbidden_everywhere(client, db, owner_api: Api, scope: str) -> None:  # type: ignore[no-untyped-def]
    await _set_manager_scope(scope)
    mgr = await login_as(client, db, "manager")
    for method, path, body in ROUTES:
        r = await mgr.send(method, path, body)
        assert r.status_code == 403, (method, path, r.status_code, r.text)
        assert r.json()["code"] == "FORBIDDEN", (method, path)


async def test_system_manage_all_is_not_forbidden(client, db, owner_api: Api) -> None:  # type: ignore[no-untyped-def]
    await _set_manager_scope(rbac.ALL)
    mgr = await login_as(client, db, "manager")
    for method, path, body in ROUTES:
        r = await mgr.send(method, path, body)
        assert r.status_code != 403, (method, path, r.status_code, r.text)


def _user(perm: str, scope: str) -> service.CurrentUser:
    return service.CurrentUser(id=uuid.uuid4(), org_id=uuid.uuid4(), email="x@example.vn", display_name="x",
                               role_code="manager", role_name="Manager", role_id=uuid.uuid4(), team_id=None,
                               session_id=uuid.uuid4(), pin_verified_until=None, addressing={},
                               permissions={perm: scope})


async def _status(perm: str, required: str, have: str) -> int:
    dep = deps.require(perm, required)
    try:
        await dep(user=_user(perm, have))
    except ApiError as e:
        return e.status
    return 200


async def test_require_forces_all_scope_for_system_manage() -> None:
    """Đơn vị: `require('system.manage', rbac.ASSIGNED)` vẫn đòi ALL; quyền khác giữ phạm vi truyền vào."""
    assert "system.manage" in deps.ALL_ONLY and "system.read" not in deps.ALL_ONLY
    for required in (rbac.ASSIGNED, rbac.TEAM, rbac.ALL):
        for have in (rbac.ASSIGNED, rbac.TEAM):
            assert await _status("system.manage", required, have) == 403
        assert await _status("system.manage", required, rbac.ALL) == 200
    # Quyền không thuộc ALL_ONLY: ASSIGNED vẫn đủ cho yêu cầu ASSIGNED (không đổi hành vi cũ).
    assert await _status("profile.write", rbac.ASSIGNED, rbac.ASSIGNED) == 200
    assert await _status("system.read", rbac.ASSIGNED, rbac.TEAM) == 200
