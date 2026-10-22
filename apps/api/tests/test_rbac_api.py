"""Phân quyền theo vai trò ở backend: danh mục, nhật ký, plugin (khoá cứng: Owner xem đánh giá nhân sự)."""

import pytest
from sqlalchemy import text

from gh.auth import rbac
from gh.crypto import hash_secret
from gh.shell import navigation
from tests.conftest import Api

PASSWORD = "mat-khau-nhan-vien-01"
PIN = "112233"


async def add_user(db, role: str) -> str:  # type: ignore[no-untyped-def]
    org = (await db.execute(text("SELECT id FROM core.organizations"))).scalar_one()
    email = f"{role}@example.vn"
    # v0.1.61 (F071): KHÔNG chèn pin_hash bằng SQL — cách đó che mất lỗi "người được mời không có đường đặt PIN".
    # Người dùng tạo không PIN, rồi tự đặt PIN lần đầu qua POST /account/pin (xem login_as).
    uid = (await db.execute(text("""INSERT INTO core.users (org_id, email, display_name, password_hash)
                                    VALUES (:o, :e, :n, :p) RETURNING id"""),
                            {"o": org, "e": email, "n": role, "p": hash_secret(PASSWORD)})).scalar_one()
    await db.execute(text("""INSERT INTO core.user_roles (user_id, role_id)
                             SELECT :u, id FROM core.roles WHERE code = :r"""), {"u": uid, "r": role})
    await db.commit()
    return email


async def login_as(client, db, role: str) -> Api:  # type: ignore[no-untyped-def]
    email = await add_user(db, role)
    c = client.__class__(transport=client._transport, base_url="http://test")
    api = Api(c)
    r = await api.send("POST", "/auth/login", {"email": email, "password": PASSWORD})
    assert r.status_code == 200, r.text
    r = await api.send("POST", "/account/pin", {"current_password": PASSWORD, "new_pin": PIN, "new_pin_confirm": PIN})
    assert r.status_code == 204, r.text  # đặt PIN lần đầu bằng mật khẩu — đường thật mọi vai trò dùng
    return api


def screens(nav: list[dict]) -> set[str]:  # type: ignore[type-arg]
    out: set[str] = set()

    def walk(nodes: list[dict]) -> None:  # type: ignore[type-arg]
        for n in nodes:
            if n["key"]:
                out.add(n["key"])
            walk(n["children"])

    for d in nav:
        walk(d["groups"])
    return out


BIZ_EXTRA = {"tasks", "documents", "deals"}
_MANAGER = BIZ_EXTRA | {"overview", "inbox", "workbench", "tasks", "directory", "documents", "profile", "opportunity",
                        "deals", "search", "graph", "notebook", "supply", "system"}
_OPERATOR = _MANAGER - {"system"}
EXPECTED = {
    "owner": set(navigation.all_screen_keys()),
    "manager": _MANAGER,
    "operator": _OPERATOR,
    "agent_staff": _OPERATOR - {"overview"},
    "auditor": BIZ_EXTRA | {"overview", "inbox", "directory", "graph", "profile", "notebook", "opportunity", "supply",
                            "search", "connections", "listenlog", "raw", "rules", "clean", "identity", "agents", "api",
                            "mcp", "plugins", "system"},
}


def test_matrix_people_review_is_owner_only() -> None:
    for role in ("manager", "operator", "agent_staff", "auditor"):
        assert rbac.DEFAULT_MATRIX[role]["people_review.read"] == rbac.NONE
        assert rbac.DEFAULT_MATRIX[role]["care.read"] == rbac.NONE
    assert all(rbac.DEFAULT_MATRIX["auditor"][p] == rbac.NONE for p in rbac.WRITE_PERMISSIONS)
    assert all(v == rbac.ALL for v in rbac.DEFAULT_MATRIX["owner"].values())


@pytest.mark.parametrize("role", list(EXPECTED))
async def test_navigation_filtered_by_role(owner_api, client, db, role: str) -> None:  # type: ignore[no-untyped-def]
    api = owner_api if role == "owner" else await login_as(client, db, role)
    nav = (await api.get("/navigation")).json()
    assert screens(nav) == EXPECTED[role]
    assert "people" not in screens(nav) or role == "owner"
    if role == "owner":
        # v0.1.42: số màn KHÔNG ẩn; DB test chưa có nhân viên ⇒ Đánh giá/Chăm sóc ẩn (14 → 12).
        # v0.1.68: thêm Nhật ký lắng nghe ⇒ Nâng cao 10 → 11.
        assert [d["count"] for d in nav] == [12, 11]
    assert "team" not in screens(nav) or role == "owner"


@pytest.mark.parametrize("role,audit,verify,plugins", [
    ("manager", 200, 403, 403), ("operator", 403, 403, 403), ("agent_staff", 403, 403, 403),
    ("auditor", 200, 200, 200),
])
async def test_endpoint_permissions(owner_api, client, db, role, audit, verify, plugins) -> None:  # type: ignore[no-untyped-def]
    api = await login_as(client, db, role)
    assert (await api.get("/audit")).status_code == audit
    assert (await api.get("/audit/verify")).status_code == verify
    assert (await api.get("/plugins")).status_code == plugins
    # Không ai ngoài Owner bật/tắt plugin — kể cả khi đã nhập PIN.
    await api.send("POST", "/auth/pin/verify", {"pin": PIN})
    r = await api.send("PATCH", "/plugins/@gen/chassis-bus/toggle", {"enabled": False})
    assert r.status_code == 403


async def test_manager_sees_only_team_audit(owner_api, client, db) -> None:  # type: ignore[no-untyped-def]
    api = await login_as(client, db, "manager")
    items = (await api.get("/audit")).json()["items"]
    me = (await api.get("/auth/me")).json()
    assert items and all(i["actor_id"] == f"user:{me['id']}" for i in items)
    owner_items = (await owner_api.get("/audit")).json()["items"]
    assert any(i["action"] == "setup.owner_created" for i in owner_items)
