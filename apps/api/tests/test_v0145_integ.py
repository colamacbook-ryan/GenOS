"""v0.1.45 — kiểm tích hợp sau khi gộp 4 gói (pin-rbac-cli + mcp-ssrf-log + run-pg-secrets + ws-people-help).

- Kiểm địa chỉ nhà cung cấp lúc GHI (gói pin-rbac-cli) + cấm tên dịch vụ compose (gói mcp-ssrf-log): POST /providers
  openai_compat 'https://db/v1' kèm khoá ⇒ 422, không dòng nào được ghi.
- Route mới PATCH /mcp/tools/{id} (gói mcp-ssrf-log) theo luật `system.manage` luôn cần phạm vi ALL (gói pin-rbac-cli):
  Manager có system.manage='team' ⇒ 403; Auditor ⇒ 403 trước 423.
"""

import uuid
from typing import Any

import httpx
import pytest
from sqlalchemy import text

from gh.auth import rbac
from gh.db import admin_sessionmaker
from tests.conftest import Api, verify_pin
from tests.test_mcp_ssrf_v0145 import Recorder, _write_tool
from tests.test_rbac_api import login_as
from tests.test_rbac_system_manage_v0145 import _set_manager_scope

KEY = "sk-test-khoa-tich-hop-0145"


@pytest.fixture
def rec(app: Any) -> Recorder:
    r = Recorder()
    app.state.mcp_transport = r.transport()
    return r


@pytest.mark.parametrize("host", ["db", "redis", "api", "gen-harness-db-1", "DB."])
async def test_provider_compose_service_name_rejected_at_write(owner_api: Api, host: str) -> None:
    await verify_pin(owner_api)
    body = {"kind": "openai_compat", "name": "OpenAI tương thích", "endpoint": f"https://{host}/v1", "keys": [KEY],
            "models": ["gpt-x"]}
    r = await owner_api.send("POST", "/providers", body)
    assert r.status_code == 422, r.text
    assert "vùng mạng bị cấm" in r.text
    assert KEY not in r.text
    async with admin_sessionmaker()() as s:
        n = (await s.execute(text("SELECT count(*) FROM agent.providers WHERE endpoint = :e"),
                             {"e": f"https://{host}/v1"})).scalar_one()
    assert n == 0


async def test_mcp_tool_access_needs_system_manage_all(client: httpx.AsyncClient, db: Any, owner_api: Api, app: Any,
                                                       rec: Recorder) -> None:
    tid = await _write_tool(owner_api, app)
    await _set_manager_scope(rbac.TEAM)
    mgr = await login_as(client, db, "manager")
    try:
        for body in ({"access": "read"}, {"access": "write"}):
            r = await mgr.send("PATCH", f"/mcp/tools/{tid}", body)
            assert r.status_code == 403 and r.json()["code"] == "FORBIDDEN", r.text
        r = await mgr.send("PATCH", f"/agents/{uuid.uuid4()}", {"autonomy_level": 1})
        assert r.status_code == 403, r.text
    finally:
        await mgr.c.aclose()
    auditor = await login_as(client, db, "auditor")
    try:
        r = await auditor.send("PATCH", f"/mcp/tools/{tid}", {"access": "read"})
        assert r.status_code == 403, r.text
    finally:
        await auditor.c.aclose()
    acc = (await db.execute(text("SELECT access FROM agent.mcp_tools WHERE id = :i"), {"i": tid})).scalar()
    assert acc == "write"
