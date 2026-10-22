"""F-15 (v0.1.35): /notebooks (Kho sạch) không lọc theo phạm vi → chỉ quyền Kho (data.read / data.manage) được dùng.
Sổ tay có phạm vi cho từng người/nhóm vẫn là /notebook của relations (không đổi)."""

import uuid

import pytest

from tests.conftest import Api
from tests.test_p3_relations import assign_scope, world  # noqa: F401 — fixture dùng chung
from tests.test_rbac_api import login_as

ENTRY = {"section": "preferences", "body": "Ghi chú thử phạm vi"}


async def _write_routes_forbidden(api: Api, pid: str, eid: str) -> None:
    base = f"/notebooks/person/{pid}"
    assert (await api.send("POST", f"{base}/entries", ENTRY)).status_code == 403
    assert (await api.send("PATCH", f"{base}/entries/{eid}", {"pinned": True})).status_code == 403
    assert (await api.send("DELETE", f"{base}/entries/{eid}", None)).status_code == 403
    assert (await api.send("POST", f"{base}/compact", None)).status_code == 403


@pytest.mark.parametrize("role", ["agent_staff", "manager", "operator"])
async def test_notebooks_forbidden_without_data_permission(  # type: ignore[no-untyped-def]
        world, owner_api: Api, client, db, role: str) -> None:  # noqa: F811
    pid = str(world["pb"])  # khách B chưa được phân cho ai → ngoài phạm vi AgentNV/Manager
    seeded = await owner_api.send("POST", f"/notebooks/person/{pid}/entries", ENTRY)
    assert seeded.status_code == 201, seeded.text
    eid = seeded.json()["id"]
    api = await login_as(client, db, role)
    assert (await api.get(f"/notebooks/person/{pid}")).status_code == 403
    assert (await api.get(f"/notebooks/person/{pid}/compactions")).status_code == 403
    assert (await api.get(f"/notebooks/group/{world['group']}")).status_code == 403
    await _write_routes_forbidden(api, pid, eid)
    if role == "agent_staff":
        # Kể cả đối tượng TRONG phạm vi: /notebooks vẫn đóng; sổ tay có phạm vi là /notebook (relations).
        await assign_scope(db, "agent_staff@example.vn", "person", world["pb"])
        assert (await api.get(f"/notebooks/person/{pid}")).status_code == 403
        assert (await api.get(f"/notebook/person/{pid}")).status_code == 200


async def test_notebooks_auditor_read_only(  # type: ignore[no-untyped-def]
        world, owner_api: Api, client, db) -> None:  # noqa: F811
    pid = str(world["pa"])
    seeded = await owner_api.send("POST", f"/notebooks/person/{pid}/entries", ENTRY)
    assert seeded.status_code == 201, seeded.text
    auditor = await login_as(client, db, "auditor")
    assert (await auditor.get(f"/notebooks/person/{pid}")).status_code == 200
    assert (await auditor.get(f"/notebooks/person/{pid}/compactions")).status_code == 200
    await _write_routes_forbidden(auditor, pid, seeded.json()["id"])


async def test_notebooks_owner_full_access(world, owner_api: Api) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
    pid = str(world["pa"])
    assert (await owner_api.get(f"/notebooks/person/{pid}")).status_code == 200
    added = await owner_api.send("POST", f"/notebooks/person/{pid}/entries", ENTRY)
    assert added.status_code == 201, added.text
    eid = added.json()["id"]
    patched = await owner_api.send("PATCH", f"/notebooks/person/{pid}/entries/{eid}", {"pinned": True})
    assert patched.status_code == 200, patched.text
    assert (await owner_api.send("POST", f"/notebooks/person/{pid}/compact", None)).status_code == 200
    assert (await owner_api.send("DELETE", f"/notebooks/person/{pid}/entries/{eid}", None)).status_code == 204
    assert (await owner_api.get(f"/notebooks/person/{uuid.uuid4()}")).status_code == 404


async def test_relations_notebook_still_scoped(  # type: ignore[no-untyped-def]
        world, owner_api: Api, client, db) -> None:  # noqa: F811
    staff = await login_as(client, db, "agent_staff")
    assert (await staff.get(f"/notebook/person/{world['pb']}")).status_code == 404
    assert (await staff.send("POST", f"/notebook/person/{world['pb']}/entries", ENTRY)).status_code == 404
    await assign_scope(db, "agent_staff@example.vn", "person", world["pa"])
    assert (await staff.get(f"/notebook/person/{world['pa']}")).status_code == 200
    assert (await staff.send("POST", f"/notebook/person/{world['pa']}/entries", ENTRY)).status_code == 201
