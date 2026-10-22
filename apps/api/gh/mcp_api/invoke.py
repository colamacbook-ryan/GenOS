"""Lõi gọi tool MCP dùng chung (khoá cứng #4) — tách từ `gh.mcp_api.routes.call_tool` (v0.1.26, không đổi hành vi).

Route `POST /mcp/tools/{id}/call` và liên kết Gen-hub (`gh.hub_link`) cùng gọi `invoke_tool()` — KHÔNG có đường gọi
MCP thứ hai. Thứ tự kiểm không cài đặt nào tắt được:

máy chủ đang bật → mở (`is_exposed`) → được cấp (`agent.mcp_grants`) → guard mạng công cộng → `access`:
`write` luôn tạo `biz.action_drafts(kind='mcp_write')` rồi DỪNG (không gọi ra ngoài); `read` gọi ngay nếu mức tự trị
hiệu lực > 1. Mọi nhánh — kể cả bị chặn — ghi một dòng `agent.mcp_calls` và một dòng Action Log.

v0.1.45 (F-57): `agent.mcp_calls.args` và sự kiện WS `mcp.call` (đi tới mọi vai trò `system.read`) chỉ còn DẤU VẾT
tham số (`args_digest`: sha256 + tên khoá cấp 1 + số byte) — không lưu nguyên văn; `result_summary` và lỗi đi qua
`gh.chassis.masking.mask_for_model` (che số dài, email, khoá/token). Bản nháp `mcp_write` giữ tham số (người duyệt cần
thấy) nhưng cũng qua lớp che.

v0.1.49: `actor` có thể là actor hệ thống của việc nền (`gh.hub_link.service.SystemActor`, Bản tin Gen) — Action Log ghi
`actor_type = getattr(actor, "actor_type", "user")` ('system' cho việc nền); không đổi hành vi nào khác.

v0.1.50 (F-81): kwarg `approved_write` (mặc định False) — xem docstring `invoke_tool`; duy nhất
`hub_link.service.write_kho` truyền True.
"""

import hashlib
import time
import uuid
from collections.abc import Callable
from typing import Any, Protocol

import orjson
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gh import crypto, realtime
from gh.biz.core.drafts import create_draft, effective_level
from gh.chassis import actionlog
from gh.chassis.masking import mask_for_model
from gh.chassis.mcp_client import McpBlockedNetwork, McpClient, McpError, McpTransportUnsupported
from gh.data.common import iso
from gh.errors import ApiError, conflict, not_found

MCP_AAD = b"mcp_server_auth"


class Actor(Protocol):
    """Người/hệ thống đứng sau một lời gọi tool: `service.CurrentUser` (người dùng) hoặc actor hệ thống của việc nền
    (`gh.hub_link.service.SystemActor`, v0.1.49). Chỉ cần định danh để ghi Action Log; `actor_type` mặc định 'user'."""

    @property
    def actor_id(self) -> str: ...

    @property
    def ip(self) -> str | None: ...


SERVER_SELECT = """
SELECT s.*, (SELECT count(*) FROM agent.mcp_tools t WHERE t.server_id = s.id) AS tool_count,
       (SELECT count(*) FROM agent.mcp_tools t WHERE t.server_id = s.id AND t.is_exposed) AS exposed_count
FROM agent.mcp_servers s
"""

TOOL_SELECT = """
SELECT t.id, t.server_id, t.name, t.access, t.is_exposed, t.schema, s.name AS server_name, s.org_id
FROM agent.mcp_tools t JOIN agent.mcp_servers s ON s.id = t.server_id
"""


async def get_server(db: AsyncSession, org_id: uuid.UUID, server_id: uuid.UUID) -> Any:
    r = (await db.execute(text(SERVER_SELECT + " WHERE s.id = :i AND s.org_id = :o"),
                          {"i": server_id, "o": org_id})).one_or_none()
    if r is None:
        raise not_found("Máy chủ MCP")
    return r


async def get_tool(db: AsyncSession, org_id: uuid.UUID, tool_id: uuid.UUID) -> Any:
    r = (await db.execute(text(TOOL_SELECT + " WHERE t.id = :i AND s.org_id = :o"),
                          {"i": tool_id, "o": org_id})).one_or_none()
    if r is None:
        raise not_found("Tool MCP")
    return r


async def grants_of(db: AsyncSession, tool_id: uuid.UUID) -> list[str]:
    rows = (await db.execute(text("SELECT agent_key FROM agent.mcp_grants WHERE tool_id = :t ORDER BY agent_key"),
                             {"t": tool_id})).scalars().all()
    return list(rows)


async def auth_token(db: AsyncSession, server_id: uuid.UUID) -> str | None:
    """Giải mã token máy chủ — chỉ dùng để gắn header khi gọi ra ngoài; KHÔNG log, KHÔNG trả qua API."""
    blob = (await db.execute(text("SELECT auth_enc FROM agent.mcp_servers WHERE id = :i"),
                             {"i": server_id})).scalar_one_or_none()
    return crypto.decrypt(bytes(blob), MCP_AAD).decode() if blob is not None else None


def encrypt_token(token: str) -> bytes:
    return crypto.encrypt(token.encode(), MCP_AAD)


async def set_health(db: AsyncSession, redis: Any, org_id: uuid.UUID, server_id: uuid.UUID, health: str) -> None:
    await db.execute(text("UPDATE agent.mcp_servers SET health = :h WHERE id = :i"), {"h": health, "i": server_id})
    if redis is not None:
        await realtime.publish(redis, "mcp.server_health", {"server_id": str(server_id), "health": health},
                               org_id=org_id)


DIGEST_MAX_KEYS = 20


def args_digest(args: Any) -> dict[str, Any]:
    """Dấu vết tham số thay nguyên văn (v0.1.45, F-57): `sha256` của JSON sắp khoá (đối chiếu được hai lần gọi cùng
    tham số), `keys` = tên khoá cấp 1 (tối đa 20, khoá kiểu bí mật vẫn hiện TÊN — giá trị không bao giờ lưu),
    `bytes` = độ dài JSON."""
    raw = orjson.dumps(args, option=orjson.OPT_SORT_KEYS)
    keys = sorted(str(k) for k in args)[:DIGEST_MAX_KEYS] if isinstance(args, dict) else []
    return {"sha256": hashlib.sha256(raw).hexdigest(), "keys": keys, "bytes": len(raw)}


async def log_call(db: AsyncSession, redis: Any, *, org_id: uuid.UUID, tool_id: uuid.UUID, agent_key: str,
                   args: dict[str, Any], outcome: str, result_summary: str, latency_ms: int,
                   draft_id: uuid.UUID | None = None) -> dict[str, Any]:
    """Ghi `agent.mcp_calls` + phát WS `mcp.call`. `args` chỉ lưu DẤU VẾT (`args_digest`) — mọi nhánh."""
    digest = args_digest(args)
    row = (await db.execute(text("""
        INSERT INTO agent.mcp_calls (org_id, tool_id, agent_key, args, result_summary, latency_ms, outcome,
                                     draft_id)
        VALUES (:o, :t, :a, CAST(:args AS jsonb), :rs, :lat, :out, :d)
        RETURNING id, at"""),
        {"o": org_id, "t": tool_id, "a": agent_key, "args": orjson.dumps(digest).decode(), "rs": result_summary,
         "lat": latency_ms, "out": outcome, "d": draft_id})).one()
    item = {"id": str(row.id), "at": iso(row.at), "tool_id": str(tool_id), "agent_key": agent_key,
            "args": digest, "outcome": outcome, "result_summary": result_summary, "latency_ms": latency_ms,
            "draft_id": str(draft_id) if draft_id else None}
    if redis is not None:
        await realtime.publish(redis, "mcp.call", item, org_id=org_id)
    return item


def agent_uuid(agent_key: str) -> uuid.UUID | None:
    if agent_key.startswith("agent:"):
        try:
            return uuid.UUID(agent_key[len("agent:"):])
        except ValueError:
            return None
    return None


class McpCallFailed(ApiError):
    """Máy chủ trả lỗi / mạng lỗi khi gọi tool đọc (409 `MCP_CALL_FAILED`); `cause` giữ lỗi gốc cho bên gọi."""

    def __init__(self, cause: McpError, message: str | None = None):
        super().__init__(409, "MCP_CALL_FAILED", message if message is not None else str(cause))
        self.cause = cause


# Q8-1 (F074): tool ghi mới dừng ở bản nháp — chưa có bước gọi tool sau khi duyệt. Câu này nói thật điều đó
# (duyệt ở Bàn làm việc cũng bị chặn, xem `drafts.MCP_WRITE_UNSUPPORTED`), không hứa "sẽ thực hiện".
HELD_WRITE_SUMMARY = "Chờ duyệt ở Bàn làm việc — chưa hỗ trợ thực thi tool ghi nên hệ thống ngoài chưa bị gọi"


def redact(message: str, token: str | None) -> str:
    """Máy chủ ngoài có thể lặp lại header Authorization trong thân lỗi — xoá token trước khi log / trả / phát WS."""
    return message.replace(token, "[đã che]") if token else message


def _default_summary(result: Any, token: str | None = None) -> str:
    """`result_summary` mặc định: kết quả ĐÃ CHE (số dài, email, khoá/token, token máy chủ), cắt 500 ký tự."""
    return orjson.dumps(mask_for_model(result, secrets=(token,) if token else ())).decode()[:500]


def _mask_err(message: str, token: str | None) -> str:
    return str(mask_for_model(redact(message, token), secrets=(token,) if token else ()))


async def invoke_tool(db: AsyncSession, redis: Any, client: McpClient, *, org_id: uuid.UUID, tool: Any,
                      agent_key: str, args: dict[str, Any], actor: Actor,
                      summarize: Callable[[Any], str] | None = None, approved_write: bool = False) -> dict[str, Any]:
    """Gọi `tool` (dòng từ `get_tool`) nhân danh `agent_key`. Trả `{"outcome": "ok", "result", "call"}` hoặc
    `{"outcome": "held_for_approval"|"blocked", "draft", "call"}` (tool ghi); ném `ApiError` 403 khi bị chặn (đã
    COMMIT log trước khi ném — `DB` rollback cả phiên khi route ném lỗi) và `McpCallFailed` khi máy chủ lỗi.
    `summarize` quyết định chuỗi lưu ở `mcp_calls.result_summary` (hub link truyền bản chỉ siêu dữ liệu); mặc định
    là kết quả đã qua `mask_for_model`.

    `approved_write` (v0.1.50, F-81): mặc định False — tool `write` luôn tạo bản nháp `mcp_write` rồi DỪNG. Chỉ khi True
    VÀ `tool.access == 'write'` mới bỏ nhánh bản nháp và đi tiếp đường gọi (vẫn kiểm máy chủ bật, tool mở, được cấp,
    mức tự trị như tool đọc; vẫn ghi `mcp_calls` + Action Log `mcp.call_ok` / `mcp.call_error`). CHỈ
    `gh.hub_link.service.write_kho` được truyền True — sau khi Owner đã Xác nhận + nhập PIN và permit ký còn hiệu lực.
    Tuyệt đối không truyền True từ route chung hay từ dữ liệu người dùng."""
    t = tool
    tool_id = t.id
    atype = getattr(actor, "actor_type", "user")  # v0.1.49: việc nền (SystemActor) ghi đúng 'system'
    started = time.monotonic()

    def elapsed() -> int:
        return int((time.monotonic() - started) * 1000)

    async def blocked(code: str, msg: str) -> ApiError:
        item = await log_call(db, redis, org_id=org_id, tool_id=tool_id, agent_key=agent_key, args=args,
                              outcome="blocked", result_summary=msg, latency_ms=elapsed())
        await actionlog.record(db, org_id=org_id, actor_type=atype, actor_id=actor.actor_id,
                               action="mcp.call_blocked", target_type="mcp_tool", target_id=str(tool_id),
                               target_label=f"{t.server_name} · {t.name}", result="blocked",
                               detail={"code": code, "reason": msg, "agent_key": agent_key}, ip=actor.ip)
        await db.commit()
        return ApiError(403, code, "Bị chặn", msg, call=item)

    server = await get_server(db, org_id, t.server_id)
    if not server.is_enabled:
        raise await blocked("MCP_SERVER_DISABLED", "Bị chặn: máy chủ MCP đang tắt")
    if not t.is_exposed:
        raise await blocked("MCP_TOOL_NOT_EXPOSED", "Bị chặn: tool chưa được Owner mở")
    if agent_key not in await grants_of(db, tool_id):
        raise await blocked("MCP_TOOL_NOT_GRANTED", "Bị chặn: agent chưa được cấp tool này")

    if t.access == "write" and not approved_write:
        draft = await create_draft(db, org_id=org_id, kind="mcp_write", title=f"Gọi tool {t.name}",
                                   body_text=f"Gọi tool MCP ghi '{t.name}' trên máy chủ '{t.server_name}' với "
                                   f"tham số {orjson.dumps(mask_for_model(args)).decode()}. "
                                   "Lưu ý: hệ thống chưa hỗ trợ thực thi tool ghi — bản nháp này chỉ để xem và huỷ.",
                                   action_key="mcp.write",
                                   agent_id=agent_uuid(agent_key),
                                   sources=[{"label": t.server_name, "ref": {"type": "mcp_server",
                                            "id": str(t.server_id)}}], redis=redis)
        outcome = "held_for_approval" if draft["status"] == "pending" else "blocked"
        summary = HELD_WRITE_SUMMARY if outcome == "held_for_approval" else (draft["hold_reason"] or
                  "Bị chặn bởi chính sách")
        item = await log_call(db, redis, org_id=org_id, tool_id=tool_id, agent_key=agent_key, args=args,
                              outcome=outcome, result_summary=summary, latency_ms=elapsed(), draft_id=draft["id"])
        return {"outcome": outcome, "draft": draft, "call": item}

    level = await effective_level(db, org_id, agent_id=agent_uuid(agent_key))
    if level <= 1:
        raise await blocked("MCP_AUTONOMY_TOO_LOW", f"Bị chặn: mức tự trị hiện tại ({level}) không cho gọi tool")
    token = await auth_token(db, server.id)
    try:
        result = await client.call_tool(server, t.name, args, token)
    except McpBlockedNetwork as e:
        await set_health(db, redis, org_id, server.id, "blocked")
        raise await blocked("MCP_NETWORK_BLOCKED", str(e)) from e
    except McpTransportUnsupported as e:
        raise await blocked("MCP_TRANSPORT_UNSUPPORTED", str(e)) from e
    except McpError as e:
        err = _mask_err(str(e), token)
        await set_health(db, redis, org_id, server.id, "error")
        await log_call(db, redis, org_id=org_id, tool_id=tool_id, agent_key=agent_key, args=args, outcome="error",
                       result_summary=err[:500], latency_ms=elapsed())
        await actionlog.record(db, org_id=org_id, actor_type=atype, actor_id=actor.actor_id,
                               action="mcp.call_error", target_type="mcp_tool", target_id=str(tool_id),
                               target_label=f"{t.server_name} · {t.name}", result="failed",
                               detail={"error": err[:500]}, ip=actor.ip)
        await db.commit()
        raise McpCallFailed(e, err) from e
    await set_health(db, redis, org_id, server.id, "healthy")
    item = await log_call(db, redis, org_id=org_id, tool_id=tool_id, agent_key=agent_key, args=args, outcome="ok",
                          result_summary=(summarize(result) if summarize is not None
                                          else _default_summary(result, token)), latency_ms=elapsed())
    await actionlog.record(db, org_id=org_id, actor_type=atype, actor_id=actor.actor_id, action="mcp.call_ok",
                           target_type="mcp_tool", target_id=str(tool_id), target_label=f"{t.server_name} · {t.name}",
                           detail={"agent_key": agent_key}, ip=actor.ip)
    return {"outcome": "ok", "result": result, "call": item}


async def discover(db: AsyncSession, redis: Any, client: McpClient, *, org_id: uuid.UUID, server: Any,
                   actor: Actor) -> list[dict[str, Any]]:
    """Khám phá tool qua `tools/list`. Tool MỚI luôn vào với `is_exposed=false` (mặc định đóng, khoá cứng #4) —
    tool đã biết giữ nguyên trạng thái mở/đóng + phạm vi cấp hiện có, chỉ cập nhật mô tả/schema."""
    if not server.is_enabled:
        raise conflict("MCP_SERVER_DISABLED", "Máy chủ đang tắt")
    atype = getattr(actor, "actor_type", "user")
    token = await auth_token(db, server.id)
    try:
        tools = await client.list_tools(server, token)
    except McpBlockedNetwork as e:
        await set_health(db, redis, org_id, server.id, "blocked")
        await db.commit()
        raise conflict("MCP_NETWORK_BLOCKED", str(e)) from e
    except McpTransportUnsupported as e:
        raise conflict("MCP_TRANSPORT_UNSUPPORTED", str(e)) from e
    except McpError as e:
        await set_health(db, redis, org_id, server.id, "error")
        await db.commit()
        raise conflict("MCP_DISCOVER_FAILED", _mask_err(str(e), token)) from e
    found = []
    for t in tools:
        row = (await db.execute(text("""
            INSERT INTO agent.mcp_tools (server_id, name, access, schema)
            VALUES (:s, :n, :a, CAST(:sc AS jsonb))
            ON CONFLICT (server_id, name) DO UPDATE SET schema = EXCLUDED.schema
            RETURNING id, access, is_exposed, (xmax = 0) AS is_new"""),
            {"s": server.id, "n": t.name, "a": "read" if t.read_only else "write",
             "sc": orjson.dumps(t.input_schema).decode()})).one()
        found.append({"id": str(row.id), "name": t.name, "access": row.access, "is_exposed": row.is_exposed,
                      "is_new": row.is_new})
    await set_health(db, redis, org_id, server.id, "healthy")
    await actionlog.record(db, org_id=org_id, actor_type=atype, actor_id=actor.actor_id,
                           action="mcp.server_discovered", target_type="mcp_server", target_id=str(server.id),
                           target_label=server.name, detail={"tool_count": len(found),
                           "new": sum(1 for f in found if f["is_new"])}, ip=actor.ip)
    return found


__all__ = ["MCP_AAD", "McpCallFailed", "agent_uuid", "args_digest", "auth_token", "discover", "encrypt_token",
           "get_server", "get_tool", "grants_of", "invoke_tool", "log_call", "redact", "set_health"]
