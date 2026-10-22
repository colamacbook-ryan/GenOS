"""v0.1.45 (F-57) — nhật ký MCP không lưu nguyên văn tham số: `agent.mcp_calls.args` + sự kiện WS `mcp.call` chỉ còn
dấu vết {sha256, keys, bytes}; `result_summary` đã che (số dài, email, khoá/token); job dọn dẹp chuyển dòng cũ."""

import hashlib
import time
from typing import Any

import httpx
import orjson
import pytest
from sqlalchemy import text

from gh import realtime, retention
from gh.db import admin_sessionmaker, sessionmaker
from gh.mcp_api import invoke
from tests.conftest import Api
from tests.phase2 import org_id
from tests.test_p4_agents import _seed_agent
from tests.test_p4_mcp import _create_server, _exposed_tool

SECRET_ARGS = {"so_tk": "0123456789012", "email": "a@b.vn", "token": "sk-abcdefghijklmnop123456"}
LEAKS = ("0123456789012", "a@b.vn", "sk-abcdefghijklmnop123456")


def _echo_transport() -> httpx.MockTransport:
    def handler(req: httpx.Request) -> httpx.Response:
        body = orjson.loads(req.content)
        if body["method"] == "tools/list":
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": {"tools": [
                {"name": "send_email", "inputSchema": {}},
                {"name": "get_status", "inputSchema": {}, "annotations": {"readOnlyHint": True}}]}})
        if body["params"]["name"] == "boom":
            return httpx.Response(500, text=f"lỗi với {orjson.dumps(body['params']['arguments']).decode()}")
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"],
                                         "result": {"echo": body["params"]["arguments"],
                                                    "note": "liên hệ chu.tk@example.com, STK 9704 1234 5678 9012"}})

    return httpx.MockTransport(handler)


@pytest.fixture
def ws_events(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    seen: list[dict[str, Any]] = []
    orig = realtime.publish

    async def fake(redis: Any, type: str, data: dict[str, Any], **kw: Any) -> None:
        seen.append({"type": type, "data": data})
        await orig(redis, type, data, **kw)

    monkeypatch.setattr(realtime, "publish", fake)
    return seen


def _no_leak(blob: str) -> None:
    for leak in LEAKS:
        assert leak not in blob, leak


def test_args_digest_shape() -> None:
    d = invoke.args_digest(SECRET_ARGS)
    raw = orjson.dumps(SECRET_ARGS, option=orjson.OPT_SORT_KEYS)
    assert d == {"sha256": hashlib.sha256(raw).hexdigest(), "keys": ["email", "so_tk", "token"], "bytes": len(raw)}
    many = {f"k{i:02d}": i for i in range(30)}
    assert len(invoke.args_digest(many)["keys"]) == 20
    _no_leak(orjson.dumps(d).decode())


async def test_read_call_logs_digest_only(owner_api: Api, app: Any, db: Any,
                                          ws_events: list[dict[str, Any]]) -> None:
    tool, _ = await _exposed_tool(owner_api, app, "get_status", grant_to="core.reply")
    app.state.mcp_transport = _echo_transport()
    r = await owner_api.send("POST", f"/mcp/tools/{tool['id']}/call", {"agent_key": "core.reply", "args": SECRET_ARGS})
    assert r.status_code == 200, r.text
    row = (await db.execute(text("SELECT args, result_summary FROM agent.mcp_calls WHERE outcome = 'ok'"))).one()
    assert set(row.args) == {"sha256", "keys", "bytes"} and row.args["keys"] == ["email", "so_tk", "token"]
    _no_leak(orjson.dumps(row.args).decode())
    _no_leak(row.result_summary)
    assert "chu.tk@example.com" not in row.result_summary and "5678 9012" not in row.result_summary
    calls = [e for e in ws_events if e["type"] == "mcp.call"]
    assert calls and calls[-1]["data"]["args"] == row.args
    _no_leak(orjson.dumps(calls).decode())
    listed = (await owner_api.get("/mcp/calls")).json()["items"]
    assert listed[0]["args"] == row.args
    _no_leak(orjson.dumps(listed).decode())


async def test_blocked_held_error_also_digest(owner_api: Api, app: Any, db: Any,
                                              ws_events: list[dict[str, Any]]) -> None:
    s = await _create_server(owner_api)
    # blocked: tool chưa mở
    app.state.mcp_transport = _echo_transport()
    out = (await owner_api.send("POST", f"/mcp/servers/{s['id']}/discover", {})).json()
    tid = next(t["id"] for t in out["tools"] if t["name"] == "get_status")
    r = await owner_api.send("POST", f"/mcp/tools/{tid}/call", {"agent_key": "core.reply", "args": SECRET_ARGS})
    assert r.status_code == 403
    _no_leak(r.text)
    # held_for_approval: tool ghi → bản nháp (giữ tham số cho người duyệt nhưng đã che)
    org = await org_id(db)
    agent_id = await _seed_agent(db, org, name="Trợ lý")
    wtool, _ = await _exposed_tool(owner_api, app, "send_email", grant_to=f"agent:{agent_id}", server_id=s["id"])
    r = await owner_api.send("POST", f"/mcp/tools/{wtool['id']}/call",
                             {"agent_key": f"agent:{agent_id}", "args": SECRET_ARGS})
    assert r.status_code == 200 and r.json()["outcome"] in ("held_for_approval", "blocked"), r.text
    body = (await db.execute(text("SELECT body->>'text' FROM biz.action_drafts WHERE kind = 'mcp_write'"))).scalar()
    assert "so_tk" in body and "email" in body
    _no_leak(body)
    # error: máy chủ phản chiếu tham số trong thân lỗi
    async with admin_sessionmaker()() as adb:
        await adb.execute(text("""INSERT INTO agent.mcp_tools (server_id, name, access, is_exposed, schema)
                                  VALUES (:s, 'boom', 'read', true, '{}')"""), {"s": s["id"]})
        bid = (await adb.execute(text("SELECT id FROM agent.mcp_tools WHERE name = 'boom'"))).scalar()
        await adb.execute(text("INSERT INTO agent.mcp_grants (tool_id, agent_key) VALUES (:t, 'core.reply')"),
                          {"t": bid})
        await adb.commit()
    app.state.mcp_transport = _echo_transport()   # _exposed_tool → _discover đã thay transport
    r = await owner_api.send("POST", f"/mcp/tools/{bid}/call", {"agent_key": "core.reply", "args": SECRET_ARGS})
    assert r.status_code == 409, r.text
    _no_leak(r.text)
    rows = (await db.execute(text("SELECT outcome, args, result_summary FROM agent.mcp_calls"))).all()
    assert {x.outcome for x in rows} >= {"blocked", "error"}
    assert any(x.outcome in ("held_for_approval", "blocked") and x.args.get("keys") == ["email", "so_tk", "token"]
               for x in rows)
    for x in rows:
        assert set(x.args) == {"sha256", "keys", "bytes"}, x
        _no_leak(orjson.dumps(x.args).decode() + (x.result_summary or ""))
    _no_leak(orjson.dumps([e for e in ws_events if e["type"] == "mcp.call"]).decode())


async def test_retention_converts_legacy_rows(owner_api: Api, app: Any, db: Any) -> None:
    tool, _ = await _exposed_tool(owner_api, app, "get_status", grant_to="core.reply")
    org = await org_id(db)
    async with admin_sessionmaker()() as adb:
        # Tham số tool thật có khoá `sha256` ở cấp đầu vẫn phải được đổi (dấu "đã đổi" chặt: đúng 3 khoá).
        for args in (SECRET_ARGS, {"b": 1, "a": [1, 2]}, {}, {"sha256": "khong-phai-dau-vet", "q": "x"}):
            await adb.execute(text("""INSERT INTO agent.mcp_calls (org_id, tool_id, agent_key, args, outcome)
                                      VALUES (:o, :t, 'core.reply', CAST(:a AS jsonb), 'ok')"""),
                              {"o": org, "t": tool["id"], "a": orjson.dumps(args).decode()})
        # Dòng mới (đã là dấu vết) — không bị đụng.
        await adb.execute(text("""INSERT INTO agent.mcp_calls (org_id, tool_id, agent_key, args, outcome)
                                  VALUES (:o, :t, 'core.reply', CAST(:a AS jsonb), 'ok')"""),
                          {"o": org, "t": tool["id"], "a": orjson.dumps(invoke.args_digest({"x": 1})).decode()})
        await adb.commit()
    async with sessionmaker()() as s1:
        n = await retention.digest_mcp_call_args(s1, time.monotonic() + 60)
    assert n == 4
    rows = (await db.execute(text("SELECT args FROM agent.mcp_calls"))).scalars().all()
    assert all(set(a) == {"sha256", "keys", "bytes"} for a in rows)
    _no_leak(orjson.dumps(rows).decode())
    keys = sorted(tuple(a["keys"]) for a in rows)
    assert keys == [(), ("a", "b"), ("email", "so_tk", "token"), ("q", "sha256"), ("x",)]
    before = orjson.dumps(sorted(rows, key=lambda a: a["sha256"])).decode()
    async with sessionmaker()() as s2:
        assert await retention.digest_mcp_call_args(s2, time.monotonic() + 60) == 0
    await db.rollback()
    after = (await db.execute(text("SELECT args FROM agent.mcp_calls"))).scalars().all()
    assert orjson.dumps(sorted(after, key=lambda a: a["sha256"])).decode() == before
    out = await retention.retention_sweep({"redis": app.state.redis})
    assert out["datasets"][retention.MCP_ARGS_DIGEST] == {"mode": "batch", "deleted": 0, "ok": True}
    # Lượt trọn ⇒ cờ "đã xong"; có cờ thì lượt sau KHÔNG quét (dòng cũ chèn sau vẫn nguyên tới khi cờ hết hạn).
    assert await app.state.redis.get(retention.MCP_ARGS_DONE_KEY)
    async with admin_sessionmaker()() as adb:
        await adb.execute(text("""INSERT INTO agent.mcp_calls (org_id, tool_id, agent_key, args, outcome)
                                  VALUES (:o, :t, 'core.reply', CAST(:a AS jsonb), 'ok')"""),
                          {"o": org, "t": tool["id"], "a": orjson.dumps({"q": "y"}).decode()})
        await adb.commit()
    async with sessionmaker()() as s3:
        assert await retention.digest_mcp_call_args_once(s3, time.monotonic() + 60, app.state.redis) == 0
        await app.state.redis.delete(retention.MCP_ARGS_DONE_KEY)
        assert await retention.digest_mcp_call_args_once(s3, time.monotonic() + 60, app.state.redis) == 1
