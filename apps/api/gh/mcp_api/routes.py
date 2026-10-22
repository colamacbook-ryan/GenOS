"""MCP Hub (PLAN 4.3, ARCHITECTURE §10): máy chủ MCP, tool tự khám phá, cấp quyền, gọi tool, nhật ký LIVE.

Khoá cứng #4 (ARCHITECTURE §7.4): tool ghi (`access='write'`) luôn qua `biz.action_drafts` trước khi thực thi
thật (tái dùng `gh.biz.core.drafts.create_draft`, KHÔNG viết lại luồng duyệt); agent chỉ gọi được tool đã được
Owner mở (`is_exposed=true`, cần PIN `mcp.expose`) VÀ được cấp (`agent.mcp_grants`) — gọi tool chưa mở/chưa cấp
luôn bị chặn và ghi vào `agent.mcp_calls`, không có cài đặt nào tắt được kiểm tra này. v0.1.45 (F-20): đổi tool
ghi → đọc cũng cần PIN `mcp.expose` (tool đọc chạy KHÔNG qua duyệt — đổi loại = bỏ qua khoá cứng #4).

Guard mạng: `agent.mcp_servers.allow_public_network` (mặc định `false` theo cột DB) — Owner bật được TỪNG máy
chủ (không phải một trong 8 khoá cứng liệt kê ở §7.4, nên không dùng `ops.policy_boundaries`); mọi lời gọi
mạng thật (khám phá tool, gọi tool đọc) ghim DNS qua `gh.chassis.mcp_client.pin_endpoint` trước khi ra ngoài.
v0.1.45 (F-49): lúc GHI cấu hình (tạo/sửa máy chủ) cũng kiểm `pin_endpoint` — cấm link-local/siêu dữ liệu,
0.0.0.0, multicast, tên dịch vụ compose của Gen-Harness; có token thì phải `https://` (422 trên trường `endpoint`).

Không thuộc `gh/biz/*` — quyền theo vai trò hệ thống `system.read` / `system.manage`, cùng cách
`gh.agents_api.routes` / `gh.plugins_api.routes` dùng.
"""

import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gh import realtime
from gh.auth import rbac, service
from gh.auth.deps import require, require_pin
from gh.chassis import actionlog
from gh.chassis.mcp_client import HTTP_TRANSPORTS, McpBlockedNetwork, McpClient, McpError, pin_endpoint
from gh.data.common import iso
from gh.db import DB
from gh.errors import field_errors, pin_required
from gh.hub_link import service as hub
from gh.mcp_api import invoke

router = APIRouter(prefix="/mcp", tags=["mcp"])
READ = require("system.read", rbac.ALL)
MANAGE = require("system.manage", rbac.ALL)

MCP_AAD = invoke.MCP_AAD
TRANSPORTS = ("stdio", "http+sse", "streamable_http")
ACCESS_KINDS = ("read", "write")

realtime.register_event("mcp.call", "system.read")
realtime.register_event("mcp.server_health", "system.read")


def _client(request: Request) -> McpClient:
    return McpClient(transport=getattr(request.app.state, "mcp_transport", None))


# ─── máy chủ MCP ──────────────────────────────────────────────────────────────

async def _check_server_endpoint(endpoint: str, transport: str, *, has_token: bool) -> None:
    """v0.1.45 (F-49): kiểm địa chỉ lúc GHI cấu hình — cùng `pin_endpoint` như lúc gọi. `allow_public_network=True`
    ở đây: công tắc mạng công cộng vẫn chỉ áp lúc gọi (không đổi hành vi tạo máy chủ). Không phân giải được → cho
    qua (máy chủ có thể chưa chạy); lúc gọi sẽ kiểm lại trên đúng IP kết nối."""
    if transport not in HTTP_TRANSPORTS:
        return
    try:
        await pin_endpoint(endpoint, True, has_token=has_token)
    except McpBlockedNetwork as e:
        msg = str(e)
        if "không hợp lệ" in msg:
            msg = "Địa chỉ máy chủ MCP không hợp lệ — dạng https://<máy chủ>/mcp"
        elif "https://" in msg:
            msg = ("Có token mà máy chủ MCP ở mạng công cộng thì phải dùng https:// (token không được đi rõ trên "
                   "Internet; http:// chỉ dùng được trong mạng nội bộ)")
        else:
            msg = ("Địa chỉ máy chủ MCP trỏ vào vùng mạng bị cấm (siêu dữ liệu đám mây, 0.0.0.0, multicast hoặc dịch "
                   "vụ nội bộ của Gen-Harness)")
        raise field_errors({"endpoint": msg}) from e
    except McpError:
        return


class ServerIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    transport: str
    endpoint: str = Field(min_length=1, max_length=2000)
    auth_token: str | None = Field(default=None, max_length=4000)
    allow_public_network: bool = False
    note: str | None = Field(default=None, max_length=500)

    def check(self) -> None:
        if self.transport not in TRANSPORTS:
            raise field_errors({"transport": f"Chỉ nhận {', '.join(TRANSPORTS)}"})


class ServerPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    endpoint: str | None = Field(default=None, min_length=1, max_length=2000)
    auth_token: str | None = Field(default=None, max_length=4000)
    allow_public_network: bool | None = None
    is_enabled: bool | None = None
    note: str | None = Field(default=None, max_length=500)


def _server_out(r: Any) -> dict[str, Any]:
    return {"id": str(r.id), "name": r.name, "transport": r.transport, "endpoint": r.endpoint,
            "has_auth": r.auth_enc is not None, "is_enabled": r.is_enabled, "health": r.health, "note": r.note,
            "allow_public_network": r.allow_public_network, "tool_count": r.tool_count,
            "exposed_count": r.exposed_count}


SERVER_SELECT = invoke.SERVER_SELECT


_server = invoke.get_server


@router.get("/servers")
async def list_servers(user: service.CurrentUser = Depends(READ), db: AsyncSession = DB) -> list[dict[str, Any]]:
    rows = (await db.execute(text(SERVER_SELECT + " WHERE s.org_id = :o ORDER BY s.name"),
                             {"o": user.org_id})).all()
    return [_server_out(r) for r in rows]


@router.post("/servers", status_code=201)
async def create_server(body: ServerIn, user: service.CurrentUser = Depends(MANAGE),
                        db: AsyncSession = DB) -> dict[str, Any]:
    body.check()
    await _check_server_endpoint(body.endpoint.strip(), body.transport, has_token=bool(body.auth_token))
    auth_enc = invoke.encrypt_token(body.auth_token) if body.auth_token else None
    sid = (await db.execute(text("""
        INSERT INTO agent.mcp_servers (org_id, name, transport, endpoint, auth_enc, allow_public_network, note)
        VALUES (:o, :n, :t, :e, :a, :pub, :note) RETURNING id"""),
        {"o": user.org_id, "n": body.name.strip(), "t": body.transport, "e": body.endpoint.strip(),
         "a": auth_enc, "pub": body.allow_public_network, "note": body.note})).scalar_one()
    await actionlog.record(db, org_id=user.org_id, actor_type="user", actor_id=user.actor_id,
                           action="mcp.server_created", target_type="mcp_server", target_id=str(sid),
                           target_label=body.name, detail={"transport": body.transport,
                           "allow_public_network": body.allow_public_network}, ip=user.ip)
    return _server_out(await _server(db, user.org_id, sid))


@router.patch("/servers/{server_id}")
async def patch_server(server_id: uuid.UUID, body: ServerPatch, user: service.CurrentUser = Depends(MANAGE),
                       db: AsyncSession = DB) -> dict[str, Any]:
    cur = await _server(db, user.org_id, server_id)
    await hub.guard_server_admin(db, user=user, server_id=server_id, action="update")
    if body.endpoint is not None or body.auth_token:
        has_token = bool(body.auth_token) if body.auth_token is not None else cur.auth_enc is not None
        endpoint = body.endpoint.strip() if body.endpoint is not None else cur.endpoint
        await _check_server_endpoint(endpoint, cur.transport, has_token=has_token)
    sets: list[str] = []
    params: dict[str, Any] = {"i": server_id}
    changed: dict[str, Any] = {}
    for k in ("name", "endpoint", "note"):
        v = getattr(body, k)
        if v is not None:
            sets.append(f"{k} = :{k}")
            params[k] = v.strip() if isinstance(v, str) else v
            changed[k] = v
    if body.allow_public_network is not None:
        sets.append("allow_public_network = :pub")
        params["pub"] = body.allow_public_network
        changed["allow_public_network"] = body.allow_public_network
    if body.is_enabled is not None:
        sets.append("is_enabled = :en")
        params["en"] = body.is_enabled
        changed["is_enabled"] = body.is_enabled
    if body.auth_token is not None:
        sets.append("auth_enc = :a")
        params["a"] = invoke.encrypt_token(body.auth_token) if body.auth_token else None
        changed["auth_token"] = "(đã đổi)"
    if sets:
        await db.execute(text(f"UPDATE agent.mcp_servers SET {', '.join(sets)} WHERE id = :i"), params)  # noqa: S608
        await actionlog.record(db, org_id=user.org_id, actor_type="user", actor_id=user.actor_id,
                               action="mcp.server_updated", target_type="mcp_server", target_id=str(server_id),
                               target_label=cur.name, detail={"changed": changed}, ip=user.ip)
    return _server_out(await _server(db, user.org_id, server_id))


@router.delete("/servers/{server_id}", status_code=204)
async def delete_server(server_id: uuid.UUID, user: service.CurrentUser = Depends(MANAGE),
                        db: AsyncSession = DB) -> Response:
    cur = await _server(db, user.org_id, server_id)
    await hub.guard_server_admin(db, user=user, server_id=server_id, action="delete")
    await db.execute(text("DELETE FROM agent.mcp_servers WHERE id = :i"), {"i": server_id})
    await actionlog.record(db, org_id=user.org_id, actor_type="user", actor_id=user.actor_id,
                           action="mcp.server_deleted", target_type="mcp_server", target_id=str(server_id),
                           target_label=cur.name, ip=user.ip)
    return Response(status_code=204)


@router.post("/servers/{server_id}/discover")
async def discover_tools(server_id: uuid.UUID, request: Request, user: service.CurrentUser = Depends(MANAGE),
                         db: AsyncSession = DB) -> dict[str, Any]:
    """Khám phá tool qua `tools/list`. Tool MỚI luôn vào với `is_exposed=false` (mặc định đóng, khoá cứng #4) —
    tool đã biết giữ nguyên trạng thái mở/đóng + phạm vi cấp hiện có, chỉ cập nhật mô tả/schema."""
    cur = await _server(db, user.org_id, server_id)
    # Máy chủ Gen-hub: chỉ Owner; đi client ghim DNS như mọi lời gọi Gen-hub khác (v0.1.27).
    is_hub = await hub.guard_server_admin(db, user=user, server_id=server_id, action="discover")
    client = hub.client_for(getattr(request.app.state, "mcp_transport", None)) if is_hub else _client(request)
    found = await invoke.discover(db, request.app.state.redis, client, org_id=user.org_id, server=cur,
                                  actor=user)
    return {"tools": found}


# ─── tool: liệt kê, đổi loại, mở/đóng, cấp quyền ──────────────────────────────

TOOL_SELECT = invoke.TOOL_SELECT


def _tool_out(r: Any, grants: list[str] | None = None) -> dict[str, Any]:
    return {"id": str(r.id), "server_id": str(r.server_id), "server_name": r.server_name, "name": r.name,
            "access": r.access, "is_exposed": r.is_exposed, "schema": r.schema or {},
            "grants": grants if grants is not None else []}


_tool = invoke.get_tool
_grants_of = invoke.grants_of


@router.get("/tools")
async def list_tools(server_id: uuid.UUID | None = None, user: service.CurrentUser = Depends(READ),
                     db: AsyncSession = DB) -> list[dict[str, Any]]:
    where = " WHERE s.org_id = :o" + (" AND t.server_id = :s" if server_id else "")
    rows = (await db.execute(text(TOOL_SELECT + where + " ORDER BY s.name, t.name"),
                             {"o": user.org_id, **({"s": server_id} if server_id else {})})).all()
    return [_tool_out(r, await _grants_of(db, r.id)) for r in rows]


class ToolAccessPatch(BaseModel):
    access: str


@router.patch("/tools/{tool_id}")
async def patch_tool_access(tool_id: uuid.UUID, body: ToolAccessPatch, user: service.CurrentUser = Depends(MANAGE),
                            db: AsyncSession = DB) -> dict[str, Any]:
    """Đổi loại tool. v0.1.45 (F-20): đổi `write` → `read` là bỏ qua duyệt (tool đọc gọi ngay, không qua bản nháp —
    khoá cứng #4) nên cần phiên PIN `mcp.expose` (423 PIN_REQUIRED nếu chưa). `read` → `write` (chặt hơn) và giữ
    nguyên loại không cần PIN. Thứ tự: 403 (MANAGE) → 404 → 422 → 423."""
    if body.access not in ACCESS_KINDS:
        raise field_errors({"access": f"Chỉ nhận {', '.join(ACCESS_KINDS)}"})
    cur = await _tool(db, user.org_id, tool_id)
    if cur.access == "write" and body.access == "read" and not user.pin_active():
        raise pin_required()
    await db.execute(text("UPDATE agent.mcp_tools SET access = :a WHERE id = :i"), {"a": body.access, "i": tool_id})
    await actionlog.record(db, org_id=user.org_id, actor_type="user", actor_id=user.actor_id,
                           action="mcp.tool_access_changed", target_type="mcp_tool", target_id=str(tool_id),
                           target_label=f"{cur.server_name} · {cur.name}",
                           detail={"from": cur.access, "to": body.access}, ip=user.ip)
    return _tool_out(await _tool(db, user.org_id, tool_id), await _grants_of(db, tool_id))


class ExposeIn(BaseModel):
    is_exposed: bool


@router.patch("/tools/{tool_id}/expose")
async def expose_tool(tool_id: uuid.UUID, body: ExposeIn, _: service.CurrentUser = Depends(MANAGE),
                      user: service.CurrentUser = Depends(require_pin("mcp.expose")),
                      db: AsyncSession = DB) -> dict[str, Any]:
    """Chỉ Owner (PIN `mcp.expose`) mở/đóng một tool — mặc định `is_exposed=false` (khoá cứng #4)."""
    cur = await _tool(db, user.org_id, tool_id)
    await db.execute(text("UPDATE agent.mcp_tools SET is_exposed = :e WHERE id = :i"),
                     {"e": body.is_exposed, "i": tool_id})
    await actionlog.record(db, org_id=user.org_id, actor_type="user", actor_id=user.actor_id,
                           action="mcp.tool_exposed" if body.is_exposed else "mcp.tool_hidden",
                           target_type="mcp_tool", target_id=str(tool_id),
                           target_label=f"{cur.server_name} · {cur.name}", ip=user.ip)
    return _tool_out(await _tool(db, user.org_id, tool_id), await _grants_of(db, tool_id))


class GrantIn(BaseModel):
    agent_key: str = Field(min_length=1, max_length=200)


@router.post("/tools/{tool_id}/grants", status_code=201)
async def add_grant(tool_id: uuid.UUID, body: GrantIn, user: service.CurrentUser = Depends(MANAGE),
                    db: AsyncSession = DB) -> dict[str, Any]:
    cur = await _tool(db, user.org_id, tool_id)
    await db.execute(text("""INSERT INTO agent.mcp_grants (tool_id, agent_key) VALUES (:t, :a)
                             ON CONFLICT DO NOTHING"""), {"t": tool_id, "a": body.agent_key})
    await actionlog.record(db, org_id=user.org_id, actor_type="user", actor_id=user.actor_id,
                           action="mcp.grant_added", target_type="mcp_tool", target_id=str(tool_id),
                           target_label=f"{cur.server_name} · {cur.name}", detail={"agent_key": body.agent_key},
                           ip=user.ip)
    return _tool_out(cur, await _grants_of(db, tool_id))


@router.delete("/tools/{tool_id}/grants/{agent_key}", status_code=204)
async def remove_grant(tool_id: uuid.UUID, agent_key: str, user: service.CurrentUser = Depends(MANAGE),
                       db: AsyncSession = DB) -> Response:
    cur = await _tool(db, user.org_id, tool_id)
    await db.execute(text("DELETE FROM agent.mcp_grants WHERE tool_id = :t AND agent_key = :a"),
                     {"t": tool_id, "a": agent_key})
    await actionlog.record(db, org_id=user.org_id, actor_type="user", actor_id=user.actor_id,
                           action="mcp.grant_removed", target_type="mcp_tool", target_id=str(tool_id),
                           target_label=f"{cur.server_name} · {cur.name}", detail={"agent_key": agent_key},
                           ip=user.ip)
    return Response(status_code=204)


# ─── gọi tool: khoá cứng #4 ────────────────────────────────────────────────────

OUTCOMES = ("ok", "held_for_approval", "blocked", "error")


class CallIn(BaseModel):
    agent_key: str = Field(min_length=1, max_length=200)
    args: dict[str, Any] = Field(default_factory=dict)


@router.post("/tools/{tool_id}/call")
async def call_tool(tool_id: uuid.UUID, body: CallIn, request: Request, user: service.CurrentUser = Depends(MANAGE),
                    db: AsyncSession = DB) -> dict[str, Any]:
    """Gọi một tool MCP nhân danh `agent_key`. Đúng thứ tự kiểm (khoá cứng #4, không cài đặt nào tắt được):

    máy chủ đang bật → mở (`is_exposed`) → được cấp (`agent.mcp_grants`) → guard mạng công cộng → `access`:
    `write` luôn tạo `biz.action_drafts(kind='mcp_write')` rồi DỪNG (không gọi ra ngoài); `read` gọi ngay nếu
    mức tự trị hiệu lực > 1 (không phải "chỉ ghi nhận/tóm tắt"). Mọi nhánh — kể cả bị chặn — ghi một dòng
    `agent.mcp_calls` và một dòng Action Log; không có đường nào bỏ qua log này.

    Ngoại lệ DUY NHẤT của "tool ghi luôn thành bản nháp" (v0.1.50, F-81, QD-18) KHÔNG đi qua route này:
    `gh.hub_link.service.write_kho` (POST /hub/kho/write — kho_create / kho_update của Gen-hub) gọi
    `invoke_tool(approved_write=True)` sau khi Owner đã Xác nhận + nhập mã PIN và permit ký còn hiệu lực — đó chính là
    bước duyệt. Route này gọi kho_create / kho_update → 403 HUB_TOOL_NOT_ALLOWED (`hub.generic_call`).
    """
    t = await _tool(db, user.org_id, tool_id)
    # Máy chủ của liên kết Gen-hub (Kho dữ liệu): chỉ Owner, ghim DNS, kết quả đã che (v0.1.27).
    hub_out = await hub.generic_call(db, request.app.state.redis, getattr(request.app.state, "mcp_transport", None),
                                     user=user, tool=t, agent_key=body.agent_key, args=body.args)
    if hub_out is not None:
        return hub_out
    # Máy chủ lỗi → `invoke.McpCallFailed` (409 MCP_CALL_FAILED, cùng mã/tiêu đề như trước khi tách lõi).
    return await invoke.invoke_tool(db, request.app.state.redis, _client(request), org_id=user.org_id, tool=t,
                                    agent_key=body.agent_key, args=body.args, actor=user)


# ─── nhật ký LIVE ──────────────────────────────────────────────────────────────

CALL_SELECT = """
SELECT c.id, c.at, c.tool_id, c.agent_key, c.args, c.result_summary, c.latency_ms, c.outcome, c.draft_id,
       t.name AS tool_name, t.access, s.name AS server_name
FROM agent.mcp_calls c JOIN agent.mcp_tools t ON t.id = c.tool_id JOIN agent.mcp_servers s ON s.id = t.server_id
"""


def _call_out(r: Any) -> dict[str, Any]:
    return {"id": str(r.id), "at": iso(r.at), "tool_id": str(r.tool_id), "tool_name": r.tool_name,
            "access": r.access, "server_name": r.server_name, "agent_key": r.agent_key, "args": r.args,
            "result_summary": r.result_summary, "latency_ms": r.latency_ms, "outcome": r.outcome,
            "draft_id": str(r.draft_id) if r.draft_id else None}


@router.get("/calls")
async def list_calls(cursor: str | None = None, limit: int = Query(50, ge=1, le=200), outcome: str | None = None,
                     user: service.CurrentUser = Depends(READ), db: AsyncSession = DB) -> dict[str, Any]:
    where = ["c.org_id = :o"]
    params: dict[str, Any] = {"o": user.org_id}
    if outcome:
        if outcome not in OUTCOMES:
            raise field_errors({"outcome": f"Chỉ nhận {', '.join(OUTCOMES)}"})
        where.append("c.outcome = :out")
        params["out"] = outcome
    if cursor:
        try:
            ts, cid = cursor.split("|")
            params["cts"], params["cid"] = datetime.fromisoformat(ts), uuid.UUID(cid)
        except ValueError as e:
            raise field_errors({"cursor": "Con trỏ không hợp lệ"}) from e
        where.append("(c.at, c.id) < (:cts, :cid)")
    w = " WHERE " + " AND ".join(where)
    rows = (await db.execute(text(CALL_SELECT + w + " ORDER BY c.at DESC, c.id DESC LIMIT :lim"),
                             {**params, "lim": limit + 1})).all()
    more = len(rows) > limit
    rows = rows[:limit]
    nxt = f"{rows[-1].at.isoformat()}|{rows[-1].id}" if more else None
    return {"items": [_call_out(r) for r in rows], "next_cursor": nxt}


__all__ = ["router"]
