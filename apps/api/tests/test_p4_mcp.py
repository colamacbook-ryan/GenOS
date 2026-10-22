"""Giai đoạn 4.3 (MCP Hub, ARCHITECTURE §10): máy chủ, tool tự khám phá, khoá cứng #4 (tool chưa mở → chặn +
ghi log; tool ghi → luôn qua bản nháp), guard mạng công cộng mặc định tắt (Owner bật được từng máy chủ)."""

from typing import Any

import httpx
import orjson
from sqlalchemy import text

from tests.conftest import OWNER, Api
from tests.test_p4_agents import _seed_agent


def _mock_transport() -> httpx.MockTransport:
    def handler(req: httpx.Request) -> httpx.Response:
        body = orjson.loads(req.content)
        method = body.get("method")
        if method == "tools/list":
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": {"tools": [
                {"name": "send_email", "description": "Gửi email", "inputSchema": {}},
                {"name": "get_status", "description": "Xem trạng thái đơn hàng", "inputSchema": {},
                 "annotations": {"readOnlyHint": True}},
            ]}})
        if method == "tools/call":
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"],
                                             "result": {"ok": True, "echo": body["params"]["arguments"]}})
        return httpx.Response(400, json={"error": "phương thức lạ"})

    return httpx.MockTransport(handler)


async def _create_server(api: Api, *, endpoint: str = "http://127.0.0.1:9999/rpc",
                         allow_public_network: bool = False) -> dict[str, Any]:
    r = await api.send("POST", "/mcp/servers", {"name": "Kho hàng nội bộ", "transport": "streamable_http",
                                                "endpoint": endpoint, "auth_token": "secret-token-xyz",
                                                "allow_public_network": allow_public_network})
    assert r.status_code == 201, r.text
    return r.json()  # type: ignore[no-any-return]


async def _discover(api: Api, app: Any, server_id: str) -> dict[str, Any]:
    app.state.mcp_transport = _mock_transport()
    r = await api.send("POST", f"/mcp/servers/{server_id}/discover", {})
    assert r.status_code == 200, r.text
    return r.json()  # type: ignore[no-any-return]


async def _pin(api: Api) -> None:
    r = await api.send("POST", "/auth/pin/verify", {"pin": OWNER["pin"]})
    assert r.status_code == 200, r.text


# ─── máy chủ + khám phá tool ────────────────────────────────────────────────

async def test_server_crud_hides_secret(owner_api) -> None:  # type: ignore[no-untyped-def]
    api: Api = owner_api
    s = await _create_server(api)
    assert s["has_auth"] is True and "auth_token" not in s and "auth_enc" not in s
    assert s["allow_public_network"] is False and s["is_enabled"] is True and s["health"] == "unknown"
    r = await api.send("PATCH", f"/mcp/servers/{s['id']}", {"note": "kho hàng chính"})
    assert r.status_code == 200 and r.json()["note"] == "kho hàng chính"
    assert (await api.get("/mcp/servers")).json()[0]["id"] == s["id"]
    assert (await api.send("DELETE", f"/mcp/servers/{s['id']}")).status_code == 204
    assert (await api.get("/mcp/servers")).json() == []


async def test_discover_new_tools_closed_by_default(owner_api, app, db) -> None:  # type: ignore[no-untyped-def]
    api: Api = owner_api
    s = await _create_server(api)
    out = await _discover(api, app, s["id"])
    tools = {t["name"]: t for t in out["tools"]}
    assert set(tools) == {"send_email", "get_status"}
    # Chưa annotate readOnlyHint → mặc định an toàn access='write'; có readOnlyHint=true → 'read'.
    assert tools["send_email"]["access"] == "write" and tools["get_status"]["access"] == "read"
    assert all(t["is_exposed"] is False for t in tools.values())    # khoá cứng #4: tool mới luôn đóng
    assert all(t["is_new"] for t in tools.values())
    rows = (await db.execute(text("SELECT is_exposed FROM agent.mcp_tools"))).scalars().all()
    assert all(v is False for v in rows)
    # Khám phá lại: tool đã biết không bị mở lại/mất trạng thái.
    out2 = await _discover(api, app, s["id"])
    assert all(not t["is_new"] for t in out2["tools"])


async def test_discover_blocked_when_server_disabled(owner_api, app) -> None:  # type: ignore[no-untyped-def]
    api: Api = owner_api
    s = await _create_server(api)
    await api.send("PATCH", f"/mcp/servers/{s['id']}", {"is_enabled": False})
    app.state.mcp_transport = _mock_transport()
    r = await api.send("POST", f"/mcp/servers/{s['id']}/discover", {})
    assert r.status_code == 409 and r.json()["code"] == "MCP_SERVER_DISABLED"


# ─── mở tool (PIN), cấp quyền ───────────────────────────────────────────────

async def test_expose_tool_requires_pin(owner_api, app, db) -> None:  # type: ignore[no-untyped-def]
    api: Api = owner_api
    s = await _create_server(api)
    out = await _discover(api, app, s["id"])
    tid = next(t["id"] for t in out["tools"] if t["name"] == "get_status")
    r = await api.send("PATCH", f"/mcp/tools/{tid}/expose", {"is_exposed": True})
    assert r.status_code == 423
    await _pin(api)
    r = await api.send("PATCH", f"/mcp/tools/{tid}/expose", {"is_exposed": True})
    assert r.status_code == 200 and r.json()["is_exposed"] is True
    log = (await db.execute(text("SELECT action FROM ops.action_log WHERE action = 'mcp.tool_exposed'"))).all()
    assert len(log) == 1


async def test_grants_add_and_remove(owner_api, app) -> None:  # type: ignore[no-untyped-def]
    api: Api = owner_api
    s = await _create_server(api)
    out = await _discover(api, app, s["id"])
    tid = next(t["id"] for t in out["tools"] if t["name"] == "get_status")
    r = await api.send("POST", f"/mcp/tools/{tid}/grants", {"agent_key": "core.reply"})
    assert r.status_code == 201 and r.json()["grants"] == ["core.reply"]
    r = await api.send("DELETE", f"/mcp/tools/{tid}/grants/core.reply")
    assert r.status_code == 204
    tools = await api.get("/mcp/tools")
    assert next(t for t in tools.json() if t["id"] == tid)["grants"] == []


# ─── gọi tool: khoá cứng #4 ─────────────────────────────────────────────────

async def _exposed_tool(api: Api, app: Any, name: str, *, grant_to: str | None = None,
                        server_id: str | None = None) -> tuple[dict[str, Any], str]:
    if server_id is None:
        s = await _create_server(api)
        server_id = s["id"]
    out = await _discover(api, app, server_id)
    tool = next(t for t in out["tools"] if t["name"] == name)
    await _pin(api)
    r = await api.send("PATCH", f"/mcp/tools/{tool['id']}/expose", {"is_exposed": True})
    assert r.status_code == 200
    if grant_to:
        r = await api.send("POST", f"/mcp/tools/{tool['id']}/grants", {"agent_key": grant_to})
        assert r.status_code == 201
    return tool, server_id


async def test_call_blocked_when_not_exposed_and_logged(owner_api, app, db) -> None:  # type: ignore[no-untyped-def]
    api: Api = owner_api
    s = await _create_server(api)
    out = await _discover(api, app, s["id"])
    tid = next(t["id"] for t in out["tools"] if t["name"] == "get_status")
    r = await api.send("POST", f"/mcp/tools/{tid}/call", {"agent_key": "core.reply", "args": {}})
    assert r.status_code == 403 and r.json()["code"] == "MCP_TOOL_NOT_EXPOSED"
    rows = (await db.execute(text("SELECT outcome, result_summary FROM agent.mcp_calls"))).all()
    assert [(x.outcome,) for x in rows] == [("blocked",)]
    log = (await db.execute(text("SELECT action, result FROM ops.action_log WHERE action = 'mcp.call_blocked'"))
          ).all()
    assert len(log) == 1 and log[0].result == "blocked"


async def test_call_blocked_when_exposed_but_not_granted(owner_api, app, db) -> None:  # type: ignore[no-untyped-def]
    api: Api = owner_api
    tool, _ = await _exposed_tool(api, app, "get_status")   # không grant_to
    r = await api.send("POST", f"/mcp/tools/{tool['id']}/call", {"agent_key": "core.reply", "args": {}})
    assert r.status_code == 403 and r.json()["code"] == "MCP_TOOL_NOT_GRANTED"
    n = (await db.execute(text("SELECT count(*) FROM agent.mcp_calls WHERE outcome = 'blocked'"))).scalar()
    assert n == 1


async def test_write_tool_always_goes_through_draft(owner_api, app, db) -> None:  # type: ignore[no-untyped-def]
    api: Api = owner_api
    org = (await db.execute(text("SELECT id FROM core.organizations"))).scalar_one()
    agent_id = await _seed_agent(db, org, name="Trợ lý bán hàng")
    tool, _ = await _exposed_tool(api, app, "send_email", grant_to=f"agent:{agent_id}")
    assert tool["access"] == "write"
    r = await api.send("POST", f"/mcp/tools/{tool['id']}/call",
                       {"agent_key": f"agent:{agent_id}", "args": {"to": "khach@vi.dụ"}})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["outcome"] == "held_for_approval" and out["draft"]["status"] == "pending"
    draft = (await db.execute(text("SELECT kind, action_key, agent_id FROM biz.action_drafts WHERE id = :i"),
                              {"i": out["draft"]["id"]})).one()
    assert draft.kind == "mcp_write" and draft.action_key == "mcp.write" and str(draft.agent_id) == str(agent_id)
    call = (await db.execute(text("SELECT outcome, draft_id FROM agent.mcp_calls WHERE id = :i"),
                             {"i": out["call"]["id"]})).one()
    assert call.outcome == "held_for_approval" and str(call.draft_id) == out["draft"]["id"]


async def test_read_tool_executes_when_exposed_and_granted(owner_api, app, db) -> None:  # type: ignore[no-untyped-def]
    api: Api = owner_api
    tool, _ = await _exposed_tool(api, app, "get_status", grant_to="core.reply")
    assert tool["access"] == "read"
    r = await api.send("POST", f"/mcp/tools/{tool['id']}/call", {"agent_key": "core.reply", "args": {"id": "DH-1"}})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["outcome"] == "ok" and out["result"]["echo"] == {"id": "DH-1"}
    n = (await db.execute(text("SELECT count(*) FROM agent.mcp_calls WHERE outcome = 'ok'"))).scalar()
    assert n == 1


async def test_read_tool_blocked_when_autonomy_too_low(owner_api, app, db) -> None:  # type: ignore[no-untyped-def]
    api: Api = owner_api
    org = (await db.execute(text("SELECT id FROM core.organizations"))).scalar_one()
    await db.execute(text("UPDATE core.organizations SET settings = jsonb_set(settings, '{autonomy_level}', '0')"
                          " WHERE id = :o"), {"o": org})
    await db.commit()
    tool, _ = await _exposed_tool(api, app, "get_status", grant_to="core.reply")
    r = await api.send("POST", f"/mcp/tools/{tool['id']}/call", {"agent_key": "core.reply", "args": {}})
    assert r.status_code == 403 and r.json()["code"] == "MCP_AUTONOMY_TOO_LOW"


# ─── guard mạng công cộng (mặc định tắt, Owner bật được TỪNG máy chủ) ───────

async def test_public_network_blocked_by_default(owner_api, app, db) -> None:  # type: ignore[no-untyped-def]
    api: Api = owner_api
    s = await _create_server(api, endpoint="https://8.8.8.8/rpc", allow_public_network=False)  # token ⇒ https
    app.state.mcp_transport = _mock_transport()
    r = await api.send("POST", f"/mcp/servers/{s['id']}/discover", {})
    assert r.status_code == 409 and r.json()["code"] == "MCP_NETWORK_BLOCKED"
    row = (await db.execute(text("SELECT health FROM agent.mcp_servers WHERE id = :i"), {"i": s["id"]})).scalar_one()
    assert row == "blocked"


async def test_owner_can_allow_public_network_per_server(owner_api, app) -> None:  # type: ignore[no-untyped-def]
    api: Api = owner_api
    s = await _create_server(api, endpoint="https://8.8.8.8/rpc", allow_public_network=False)  # token ⇒ https
    r = await api.send("PATCH", f"/mcp/servers/{s['id']}", {"allow_public_network": True})
    assert r.status_code == 200 and r.json()["allow_public_network"] is True
    out = await _discover(api, app, s["id"])   # không còn bị chặn
    assert len(out["tools"]) == 2


async def test_internal_endpoint_not_blocked_without_flag(owner_api, app) -> None:  # type: ignore[no-untyped-def]
    api: Api = owner_api
    s = await _create_server(api, endpoint="http://127.0.0.1:9999/rpc", allow_public_network=False)
    out = await _discover(api, app, s["id"])
    assert len(out["tools"]) == 2


# ─── nhật ký LIVE ────────────────────────────────────────────────────────────

async def test_calls_list_and_filter(owner_api, app) -> None:  # type: ignore[no-untyped-def]
    api: Api = owner_api
    s = await _create_server(api)
    out = await _discover(api, app, s["id"])
    tid = next(t["id"] for t in out["tools"] if t["name"] == "get_status")
    await api.send("POST", f"/mcp/tools/{tid}/call", {"agent_key": "core.reply", "args": {}})   # blocked (chưa mở)
    r = await api.get("/mcp/calls", params={"outcome": "blocked"})
    assert r.status_code == 200
    items = r.json()["items"]
    assert len(items) == 1 and items[0]["outcome"] == "blocked" and items[0]["tool_id"] == tid
