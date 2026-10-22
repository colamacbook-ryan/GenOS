"""v0.1.45 (F-49, F-20) — MCP chống SSRF: ghim DNS mọi lời gọi, cấm link-local/0.0.0.0/tên dịch vụ compose ngay lúc
ghi cấu hình, có token thì phải https; đổi tool ghi → đọc cần mã PIN."""

import socket
from typing import Any

import httpx
import orjson
import pytest
from sqlalchemy import text

from gh.chassis import mcp_client
from gh.chassis.mcp_client import McpBlockedNetwork, forbidden_host, pin_endpoint
from gh.db import admin_sessionmaker
from tests.conftest import FAKE_PUBLIC_IP, Api, verify_pin
from tests.phase2 import org_id
from tests.test_p4_mcp import _discover
from tests.test_rbac_api import login_as

TOKEN = "mcp-tok-SieuBiMat-12345"


class Recorder:
    """MockTransport đếm request — mọi ca bị chặn phải KHÔNG có request nào tới đây."""

    def __init__(self) -> None:
        self.seen: list[httpx.Request] = []

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def handle(self, req: httpx.Request) -> httpx.Response:
        self.seen.append(req)
        body = orjson.loads(req.content)
        if body["method"] == "tools/list":
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": {"tools": [
                {"name": "get_status", "inputSchema": {}, "annotations": {"readOnlyHint": True}},
                {"name": "send_email", "inputSchema": {}}]}})
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": {"ok": True}})


@pytest.fixture
def rec(app: Any) -> Recorder:
    r = Recorder()
    app.state.mcp_transport = r.transport()
    return r


async def _post_server(api: Api, endpoint: str, *, token: str | None = None, public: bool = True) -> httpx.Response:
    body: dict[str, Any] = {"name": "Máy chủ thử", "transport": "streamable_http", "endpoint": endpoint,
                            "allow_public_network": public}
    if token:
        body["auth_token"] = token
    return await api.send("POST", "/mcp/servers", body)


# ─── danh sách cấm chung ────────────────────────────────────────────────────

def test_forbidden_host_names() -> None:
    for h in ("db", "redis", "API", "browser-redis", "browser-egress", "db.", "gen-harness-api-1",
              "gen-harness_db_1", "gen-harness-browser-redis-2", "localhost.localdomain",
              # Tên kèm mạng docker và project khác `gen-harness` (review v0.1.45).
              "db.gen-harness_default", "redis.myproj_default", "gen-harness-api-1.gen-harness_default",
              "myproj-db-1", "other_redis_1"):
        assert forbidden_host(h), h
    for h in ("localhost", "127.0.0.1", "10.0.0.5", "hub.example.test", "mcp.example.com", "dbx", "gen-harness-foo-1",
              "ollama.lan", "api.openai.com", "web.example.com", "db.example.com", "redis.corp.lan",
              "mcp-db-1.example.com"):
        assert not forbidden_host(h), h


async def test_pin_endpoint_rules_v0145() -> None:
    with pytest.raises(McpBlockedNetwork, match="dịch vụ nội bộ"):
        await pin_endpoint("http://db:5432/rpc", True)
    with pytest.raises(McpBlockedNetwork, match="https"):
        await pin_endpoint("http://mcp.example.com/rpc", True, has_token=True)
    # Loopback không rời máy — http + token vẫn được (Gen-hub/MCP chạy cùng máy).
    assert (await pin_endpoint("http://127.0.0.1:9911/mcp", False, has_token=True)).ip == "127.0.0.1"
    # Mạng nội bộ (LAN, host.docker.internal) — http + token vẫn được; công cộng thì không (kể cả công tắc bật).
    assert (await pin_endpoint("http://10.1.2.3:8080/mcp", False, has_token=True)).ip == "10.1.2.3"
    assert (await pin_endpoint("http://192.168.1.9/mcp", True, has_token=True)).ip == "192.168.1.9"
    with pytest.raises(McpBlockedNetwork, match="https"):
        await pin_endpoint("http://93.184.216.34/mcp", True, has_token=True)
    # Tailscale / CGNAT 100.64.0.0/10 — đường riêng của Owner (WireGuard): http + token vẫn được khi bật mạng công cộng.
    assert (await pin_endpoint("http://100.101.102.103:8080/mcp", True, has_token=True)).ip == "100.101.102.103"
    with pytest.raises(McpBlockedNetwork, match="https"):
        await pin_endpoint("http://8.8.8.8/mcp", True, has_token=True)
    t = await pin_endpoint("https://mcp.example.com/rpc", True, has_token=True)
    assert t.ip == FAKE_PUBLIC_IP and t.host == "mcp.example.com" and t.sni == "mcp.example.com"


# ─── ghi cấu hình: 422 trên trường endpoint ──────────────────────────────────

@pytest.mark.parametrize("endpoint", [
    "http://169.254.169.254/latest/meta-data", "http://[fe80::1]/rpc", "http://0.0.0.0:9000/rpc",
    "http://db:5432/rpc", "http://redis:6379", "http://gen-harness-api-1:8000"])
async def test_create_server_forbidden_endpoint(owner_api: Api, rec: Recorder, endpoint: str) -> None:
    r = await _post_server(owner_api, endpoint)
    assert r.status_code == 422, r.text
    assert "endpoint" in r.json()["errors"] and "vùng mạng bị cấm" in r.json()["errors"]["endpoint"]
    assert (await owner_api.get("/mcp/servers")).json() == []
    assert rec.seen == []


async def test_create_server_token_requires_https(owner_api: Api, rec: Recorder) -> None:
    r = await _post_server(owner_api, "http://mcp.example.com/rpc", token=TOKEN)
    assert r.status_code == 422 and "https://" in r.json()["errors"]["endpoint"], r.text
    assert TOKEN not in r.text
    r = await _post_server(owner_api, "https://mcp.example.com/rpc", token=TOKEN)
    assert r.status_code == 201, r.text
    # Không token thì http vẫn được (máy chủ MCP trong LAN không cần xác thực).
    r = await _post_server(owner_api, "http://mcp.example.com/rpc")
    assert r.status_code == 201, r.text
    # Có token + http trong mạng nội bộ → được (token không ra Internet).
    r = await _post_server(owner_api, "http://10.0.0.9:8811/rpc", token=TOKEN)
    assert r.status_code == 201, r.text


async def test_create_server_unresolvable_allowed(owner_api: Api, rec: Recorder,
                                                  monkeypatch: pytest.MonkeyPatch) -> None:
    async def nx(host: str, port: int) -> list[Any]:
        raise socket.gaierror("không có")

    monkeypatch.setattr(mcp_client, "_getaddrinfo", nx)
    r = await _post_server(owner_api, "https://chua-chay.example/rpc", token=TOKEN)
    assert r.status_code == 201, r.text


async def test_patch_server_endpoint_and_token(owner_api: Api, rec: Recorder, db: Any) -> None:
    s = (await _post_server(owner_api, "https://mcp.example.com/rpc", token=TOKEN)).json()
    r = await owner_api.send("PATCH", f"/mcp/servers/{s['id']}", {"endpoint": "http://169.254.169.254/rpc"})
    assert r.status_code == 422 and "endpoint" in r.json()["errors"]
    # Đang có token (auth_enc) → đổi sang http cũng bị chặn.
    r = await owner_api.send("PATCH", f"/mcp/servers/{s['id']}", {"endpoint": "http://mcp.example.com/rpc"})
    assert r.status_code == 422 and "https://" in r.json()["errors"]["endpoint"]
    ep = (await db.execute(text("SELECT endpoint FROM agent.mcp_servers WHERE id = :i"), {"i": s["id"]})).scalar()
    assert ep == "https://mcp.example.com/rpc"
    # Máy chủ http không token: đặt token → kiểm lại theo endpoint hiện có.
    s2 = (await _post_server(owner_api, "http://mcp2.example.com/rpc")).json()
    r = await owner_api.send("PATCH", f"/mcp/servers/{s2['id']}", {"auth_token": TOKEN})
    assert r.status_code == 422 and "https://" in r.json()["errors"]["endpoint"]
    r = await owner_api.send("PATCH", f"/mcp/servers/{s2['id']}", {"note": "ghi chú"})
    assert r.status_code == 200
    assert rec.seen == []


# ─── dòng cũ (chèn thẳng DB) — chặn lúc gọi, không request nào ra ngoài ─────────

async def _legacy_server(db: Any, endpoint: str, *, token: bool = False) -> str:
    from gh.mcp_api import invoke

    org = await org_id(db)
    async with admin_sessionmaker()() as adb:
        sid = (await adb.execute(text("""
            INSERT INTO agent.mcp_servers (org_id, name, transport, endpoint, auth_enc, allow_public_network)
            VALUES (:o, 'Dòng cũ', 'streamable_http', :e, :a, true) RETURNING id"""),
            {"o": org, "e": endpoint, "a": invoke.encrypt_token(TOKEN) if token else None})).scalar_one()
        await adb.execute(text("""INSERT INTO agent.mcp_tools (server_id, name, access, is_exposed, schema)
                                  VALUES (:s, 'get_status', 'read', true, '{}')"""), {"s": sid})
        tid = (await adb.execute(text("SELECT id FROM agent.mcp_tools WHERE server_id = :s"), {"s": sid})).scalar()
        await adb.execute(text("INSERT INTO agent.mcp_grants (tool_id, agent_key) VALUES (:t, 'core.reply')"),
                          {"t": tid})
        await adb.commit()
    return str(sid)


async def _tool_of(db: Any, sid: str) -> str:
    return str((await db.execute(text("SELECT id FROM agent.mcp_tools WHERE server_id = :s"), {"s": sid})).scalar())


@pytest.mark.parametrize("endpoint,token", [
    ("http://169.254.169.254/latest/meta-data", False), ("http://redis:6379/", False),
    ("http://mcp.example.com/rpc", True)])
async def test_legacy_row_blocked_at_call(owner_api: Api, rec: Recorder, db: Any, endpoint: str,
                                          token: bool) -> None:
    sid = await _legacy_server(db, endpoint, token=token)
    r = await owner_api.send("POST", f"/mcp/servers/{sid}/discover", {})
    assert r.status_code == 409 and r.json()["code"] == "MCP_NETWORK_BLOCKED", r.text
    tid = await _tool_of(db, sid)
    r = await owner_api.send("POST", f"/mcp/tools/{tid}/call", {"agent_key": "core.reply", "args": {}})
    assert r.status_code == 403 and r.json()["code"] == "MCP_NETWORK_BLOCKED", r.text
    assert TOKEN not in r.text
    assert rec.seen == []


async def test_dns_rebinding_blocked(owner_api: Api, rec: Recorder, db: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    sid = await _legacy_server(db, "https://mcp.example.com/rpc", token=True)
    tid = await _tool_of(db, sid)
    r = await owner_api.send("POST", f"/mcp/tools/{tid}/call", {"agent_key": "core.reply", "args": {}})
    assert r.status_code == 200, r.text
    assert len(rec.seen) == 1
    req = rec.seen[0]
    assert req.url.host == FAKE_PUBLIC_IP and req.headers["host"] == "mcp.example.com"
    assert req.extensions.get("sni_hostname") == "mcp.example.com"

    async def rebound(host: str, port: int) -> list[Any]:
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("169.254.169.254", port))]

    monkeypatch.setattr(mcp_client, "_getaddrinfo", rebound)
    r = await owner_api.send("POST", f"/mcp/tools/{tid}/call", {"agent_key": "core.reply", "args": {}})
    assert r.status_code == 403 and r.json()["code"] == "MCP_NETWORK_BLOCKED"
    assert len(rec.seen) == 1


async def test_http_error_body_masked(owner_api: Api, app: Any, db: Any) -> None:
    def echo(req: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text=f"boom auth={req.headers.get('authorization')} email=khach.hang@vi-du.vn "
                                        f"stk=0123456789012 " + "x" * 400)

    app.state.mcp_transport = httpx.MockTransport(echo)
    sid = await _legacy_server(db, "https://mcp.example.com/rpc", token=True)
    tid = await _tool_of(db, sid)
    r = await owner_api.send("POST", f"/mcp/tools/{tid}/call", {"agent_key": "core.reply", "args": {}})
    assert r.status_code == 409 and r.json()["code"] == "MCP_CALL_FAILED"
    summary = (await db.execute(text("SELECT result_summary FROM agent.mcp_calls WHERE outcome = 'error'"))).scalar()
    for leak in (TOKEN, "khach.hang@vi-du.vn", "0123456789012"):
        assert leak not in r.text and leak not in summary, leak
    assert len(summary) < 260


# ─── F-20(2): đổi tool ghi → đọc cần mã PIN ──────────────────────────────────

async def _write_tool(api: Api, app: Any) -> str:
    s = (await _post_server(api, "http://127.0.0.1:9999/rpc", public=False)).json()
    out = await _discover(api, app, s["id"])
    return next(t["id"] for t in out["tools"] if t["access"] == "write")


async def test_write_to_read_requires_pin(owner_api: Api, app: Any, db: Any, client: httpx.AsyncClient) -> None:
    tid = await _write_tool(owner_api, app)
    r = await owner_api.send("PATCH", f"/mcp/tools/{tid}", {"access": "read"})
    assert r.status_code == 423 and r.json()["code"] == "PIN_REQUIRED", r.text
    acc = (await db.execute(text("SELECT access FROM agent.mcp_tools WHERE id = :i"), {"i": tid})).scalar()
    assert acc == "write"
    # Giữ nguyên loại → không cần PIN.
    assert (await owner_api.send("PATCH", f"/mcp/tools/{tid}", {"access": "write"})).status_code == 200
    # Sai giá trị → 422 trước 423.
    assert (await owner_api.send("PATCH", f"/mcp/tools/{tid}", {"access": "x"})).status_code == 422
    manager = await login_as(client, db, "manager")
    try:
        r = await manager.send("PATCH", f"/mcp/tools/{tid}", {"access": "read"})
        assert r.status_code == 403, r.text
    finally:
        await manager.c.aclose()
    await verify_pin(owner_api)
    r = await owner_api.send("PATCH", f"/mcp/tools/{tid}", {"access": "read"})
    assert r.status_code == 200 and r.json()["access"] == "read"


async def test_read_to_write_no_pin(owner_api: Api, app: Any) -> None:
    s = (await _post_server(owner_api, "http://127.0.0.1:9999/rpc", public=False)).json()
    out = await _discover(owner_api, app, s["id"])
    tid = next(t["id"] for t in out["tools"] if t["access"] == "read")
    r = await owner_api.send("PATCH", f"/mcp/tools/{tid}", {"access": "write"})
    assert r.status_code == 200 and r.json()["access"] == "write"
