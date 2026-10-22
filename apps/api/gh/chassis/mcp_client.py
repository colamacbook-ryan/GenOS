"""Client gọi máy chủ MCP ngoài qua JSON-RPC (ARCHITECTURE §10).

Phạm vi: chỉ hỗ trợ thật hai transport HTTP (`http+sse`, `streamable_http`) — gọi `tools/list` / `tools/call`
qua một POST JSON-RPC 2.0, đúng cách streamable-http MCP làm việc. `stdio` (chạy tiến trình con của một máy chủ
MCP bất kỳ do Owner khai báo) cần một lớp cách ly riêng như `gh.chassis.sandbox` làm cho plugin — không có
trong phạm vi cụm này; gọi `list_tools`/`call_tool` với transport này ném `McpTransportUnsupported` (ghi rõ
trong nhật ký, không thử chạy mã không kiểm soát).

Guard "Cho phép máy chủ MCP ngoài mạng nội bộ" (mặc định tắt — `agent.mcp_servers.allow_public_network`,
ARCHITECTURE §10): trước MỌI lời gọi mạng, `pin_endpoint` phân giải host MỘT lần, chặn nếu ra IP công khai và cờ
đang tắt, rồi kết nối thẳng IP đã kiểm. Áp dụng ở đây (không chỉ ở tầng route) để không có đường nào gọi thẳng bỏ
qua guard.

v0.1.45 (F-49): MỌI lời gọi MCP đều ghim DNS (trước đây chỉ Gen-hub); cấm luôn tên dịch vụ compose của chính
Gen-Harness (`db`, `redis`, `gen-harness-api-1`…) trước cả khi phân giải; có token thì phải `https://` (trừ loopback).
`pinned_request` dùng chung cho nhà cung cấp AI lúc gọi (gh.providers.clients, gh.gen.jev).
"""

import asyncio
import ipaddress
import re
import socket
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import urlparse, urlunparse

import httpx

from gh.chassis.masking import mask_error

HTTP_TRANSPORTS = ("http+sse", "streamable_http")


class McpError(Exception):
    """Lỗi khi gọi máy chủ MCP (mạng, JSON-RPC báo lỗi, HTTP lỗi)."""


class McpBlockedNetwork(McpError):
    """Máy chủ ở mạng công cộng nhưng `allow_public_network` đang tắt (mặc định)."""


class McpTransportUnsupported(McpError):
    """Transport chưa hỗ trợ gọi thật trong phạm vi này (stdio)."""


class ServerLike(Protocol):
    transport: str
    endpoint: str
    allow_public_network: bool


@dataclass
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]
    read_only: bool | None = None   # từ annotations.readOnlyHint nếu máy chủ khai — không phải chuẩn bắt buộc


def resolves_public(host: str) -> bool:
    """True nếu host phân giải ra ít nhất một IP KHÔNG riêng/loopback/link-local (tức mạng công cộng).

    Không phân giải được → coi là rủi ro (True) để đi theo nhánh an toàn (chặn), không âm thầm cho qua.
    """
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return True
    for info in infos:
        addr = info[4][0]
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            continue
        if not (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast):
            return True
    return False


def _is_public(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return not (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast)


def _token_needs_https(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Quy tắc "có token thì phải https" chỉ áp cho IP định tuyến TOÀN CẦU (`is_global`): 100.64.0.0/10 (CGNAT /
    Tailscale — đường đã mã hoá WireGuard, `*.ts.net`) là mạng riêng của Owner nên http + token vẫn được."""
    return ip.is_global and not ip.is_multicast


def _unmap(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    """`::ffff:a.b.c.d` → `a.b.c.d`: Python 3.11 coi mọi IPv4-mapped là "private" — không chuẩn hoá thì
    `[::ffff:8.8.8.8]` lọt qua công tắc mạng công cộng."""
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        return ip.ipv4_mapped
    return ip


# Tên dịch vụ trong deploy/compose.yaml — trong mạng docker, tên này phân giải ra container NỘI BỘ của Gen-Harness
# (Postgres, Redis, api…) và không bao giờ là máy chủ MCP / nhà cung cấp AI hợp lệ. Thêm service vào compose.yaml thì
# PHẢI thêm tên ở đây (kèm tên container mặc định `gen-harness-<svc>-<n>` được nhận ra theo mẫu bên dưới).
COMPOSE_SERVICE_NAMES = frozenset({"proxy", "web", "migrate", "api", "worker", "bridge", "browser", "browser-redis",
                                   "browser-egress", "db", "redis"})
# Tên container compose `<project>-<svc>-<n>` (v2) / `<project>_<svc>_<n>` (v1) — với MỌI tên project
# (COMPOSE_PROJECT_NAME có thể khác `gen-harness`); chỉ áp cho tên MỘT nhãn (không có dấu chấm — chỉ DNS nhúng của
# Docker mới phân giải được).
_CONTAINER_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*[-_](?:"
                           + "|".join(re.escape(n) for n in sorted(COMPOSE_SERVICE_NAMES, key=len, reverse=True))
                           + r")[-_]\d+$")


def forbidden_host(host: str) -> bool:
    """True nếu `host` là tên dịch vụ nội bộ của Gen-Harness: tên service compose (`db`, `redis`…), tên container
    (`gen-harness-api-1`, `gen-harness_db_1`, `<project>-db-1`), tên kèm mạng docker (`db.gen-harness_default` —
    DNS nhúng của Docker phân giải `<container>.<network>`; tên mạng compose có `_`, không phải tên miền công cộng)
    hoặc `localhost.localdomain`. KHÔNG cấm `localhost`/127.x/LAN — Gen-hub, Ollama trong LAN vẫn hợp lệ.

    Chỉ chặn THEO TÊN: IP riêng 172.x của container vẫn đi qua (mạng nội bộ được phép theo thiết kế)."""
    h = (host or "").strip().rstrip(".").lower()
    if h.startswith("[") and h.endswith("]"):
        h = h[1:-1]
    if h in COMPOSE_SERVICE_NAMES or h == "localhost.localdomain":
        return True
    first, dot, rest = h.partition(".")
    if dot and "_" in rest and (first in COMPOSE_SERVICE_NAMES or forbidden_host(first)):
        return True
    if dot:
        return False
    return bool(_CONTAINER_RE.match(h))


def always_forbidden(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Địa chỉ không bao giờ được gọi khi ghim DNS (siêu dữ liệu đám mây 169.254.x / fe80::, 0.0.0.0, multicast)."""
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return bool(ip.is_link_local or ip.is_unspecified or ip.is_multicast)


@dataclass(frozen=True)
class PinnedTarget:
    """Đích đã phân giải MỘT lần và đã kiểm: `url` trỏ thẳng IP, `host` giữ Host gốc, `sni` = tên máy cho TLS."""
    url: str
    host: str
    sni: str | None
    ip: str
    fallbacks: tuple[str, ...] = ()


async def _default_getaddrinfo(host: str, port: int) -> list[Any]:
    return await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)


# Điểm phân giải DUY NHẤT của `pin_endpoint` — test thay bằng hàm giả (tests/conftest.py) để không phụ thuộc DNS thật.
_getaddrinfo: Callable[[str, int], Awaitable[list[Any]]] = _default_getaddrinfo

# Trung tính (dùng chung MCP, Gen-hub, nhà cung cấp AI) — bên gọi nhận ra qua "không hợp lệ".
INVALID_ENDPOINT_MSG = "Địa chỉ không hợp lệ (cần dạng http(s)://<máy chủ>)"
FORBIDDEN_SERVICE_MSG = "Địa chỉ trỏ vào tên dịch vụ nội bộ của Gen-Harness"
HTTPS_REQUIRED_MSG = ("Có token mà máy chủ ở mạng công cộng thì phải dùng https:// (http:// chỉ dùng được với máy "
                      "trong mạng nội bộ hoặc cùng máy)")


def _loopback_host(host: str) -> bool:
    if host.lower().rstrip(".") == "localhost":
        return True
    try:
        return _unmap(ipaddress.ip_address(host)).is_loopback
    except ValueError:
        return False


async def pin_endpoint(endpoint: str, allow_public_network: bool, *, has_token: bool = False) -> PinnedTarget:
    """Chống DNS rebinding (v0.1.27): phân giải host MỘT lần, kiểm TẤT CẢ IP (cấm link-local/0.0.0.0/multicast;
    IP công cộng khi công tắc mạng công cộng tắt), rồi kết nối thẳng tới IP đã kiểm — không có lần phân giải thứ
    hai giữa lúc kiểm và lúc kết nối. TLS vẫn xác thực chứng chỉ theo tên máy gốc (SNI + kiểm hostname).

    v0.1.45 (F-49): tên dịch vụ nội bộ (`forbidden_host`) bị cấm TRƯỚC khi phân giải; `has_token` (gửi kèm token /
    header Authorization) qua `http://` tới một IP CÔNG CỘNG → chặn (token đi rõ trên Internet). Loopback và mạng
    nội bộ (10.x, 192.168.x, 172.16–31.x, `host.docker.internal`…) vẫn được — cùng quy tắc cho máy chủ MCP, liên kết
    Gen-hub và nhà cung cấp AI (Ollama/LM Studio trong LAN)."""
    u = urlparse(endpoint)
    host = u.hostname
    if not host or u.scheme not in ("http", "https"):
        raise McpBlockedNetwork(INVALID_ENDPOINT_MSG)
    try:
        port = u.port or (443 if u.scheme == "https" else 80)
    except ValueError as e:  # cổng ngoài 0–65535
        raise McpBlockedNetwork(INVALID_ENDPOINT_MSG) from e
    if forbidden_host(host):
        raise McpBlockedNetwork(f"{FORBIDDEN_SERVICE_MSG} ({host}) — vùng mạng bị cấm")
    literal = True
    try:
        ips: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = [_unmap(ipaddress.ip_address(host))]
    except ValueError:
        literal = False
        try:
            infos = await _getaddrinfo(host, port)
        except OSError as e:
            raise McpError(f"mạng: không phân giải được {host}") from e
        ips = []
        for info in infos:
            try:
                ip = _unmap(ipaddress.ip_address(str(info[4][0]).split("%", 1)[0]))
            except ValueError:
                continue
            if ip not in ips:
                ips.append(ip)
    if not ips:
        raise McpError(f"mạng: không phân giải được {host}")
    for ip in ips:
        if always_forbidden(ip):
            raise McpBlockedNetwork(f"Máy chủ MCP ({host}) phân giải ra vùng mạng bị cấm (link-local/siêu dữ liệu)")
    if has_token and u.scheme != "https" and not _loopback_host(host) and any(_token_needs_https(ip) for ip in ips):
        raise McpBlockedNetwork(HTTPS_REQUIRED_MSG)
    if not allow_public_network and any(_is_public(ip) for ip in ips):
        raise McpBlockedNetwork(
            f"Máy chủ MCP ở mạng công cộng ({host}) — Owner chưa bật 'Cho phép máy chủ MCP ngoài mạng nội bộ'")
    host_header = u.netloc.rsplit("@", 1)[-1]
    sni = host if (u.scheme == "https" and not literal) else None

    def url_for(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> str:
        ip_host = f"[{ip}]" if isinstance(ip, ipaddress.IPv6Address) else str(ip)
        return urlunparse(u._replace(netloc=ip_host + (f":{u.port}" if u.port else "")))

    # `fallbacks`: các IP còn lại (ĐÃ kiểm cùng lượt) — thử lần lượt khi IP đầu không kết nối được (vd bản ghi AAAA
    # của Cloudflare trên máy không có IPv6), như trình kết nối "happy eyeballs" bình thường.
    return PinnedTarget(url=url_for(ips[0]), host=host_header, sni=sni, ip=str(ips[0]),
                        fallbacks=tuple(url_for(ip) for ip in ips[1:]))


async def pinned_request(client: httpx.AsyncClient, method: str, url: str, *, allow_public_network: bool = True,
                         has_token: bool = False, headers: dict[str, str] | None = None,
                         extensions: dict[str, Any] | None = None, **kw: Any) -> httpx.Response:
    """Một request HTTP ghim DNS (v0.1.45, dùng chung MCP + nhà cung cấp AI): `pin_endpoint` (ném `McpBlockedNetwork`
    / `McpError`) rồi gửi thẳng tới IP đã kiểm với Host gốc + `sni_hostname`; IP đầu không kết nối được → thử IP kế
    (đã kiểm cùng lượt). `client` phải tạo với `trust_env=False` + `follow_redirects=False` (`pinned_client`): proxy
    môi trường sẽ tự phân giải lại tên máy — mất tác dụng ghim (cùng hành vi Gen-hub v0.1.27)."""
    target = await pin_endpoint(url, allow_public_network, has_token=has_token)
    hdrs = {k: v for k, v in (headers or {}).items() if k.lower() != "host"}
    hdrs["host"] = target.host
    ext = dict(extensions or {})
    if target.sni:
        ext["sni_hostname"] = target.sni
    urls = [target.url, *target.fallbacks]
    for i, u in enumerate(urls):
        try:
            return await client.request(method, u, headers=hdrs, extensions=ext or None, **kw)
        except httpx.ConnectError:
            if i == len(urls) - 1:
                raise
    raise AssertionError("unreachable")  # pragma: no cover


def pinned_client(transport: httpx.AsyncBaseTransport | None, timeout: float) -> httpx.AsyncClient:
    """Client cho `pinned_request`: không đọc proxy môi trường (HTTPS_PROXY/HTTP_PROXY/ALL_PROXY), không tự theo
    chuyển hướng (đích mới chưa qua kiểm IP)."""
    return httpx.AsyncClient(transport=transport, timeout=timeout, trust_env=False, follow_redirects=False)


def check_network_guard(endpoint: str, allow_public_network: bool) -> None:
    """Giữ cho tương thích import (v0.1.45: McpClient luôn ghim DNS qua `pin_endpoint`, không còn gọi hàm này)."""
    if allow_public_network:
        return
    host = urlparse(endpoint).hostname or endpoint
    if resolves_public(host):
        raise McpBlockedNetwork(
            f"Máy chủ MCP ở mạng công cộng ({host}) — Owner chưa bật 'Cho phép máy chủ MCP ngoài mạng nội bộ'")


class McpClient:
    """Transport HTTP tiêm được (`transport=httpx.MockTransport(...)` trong test), cùng cách gh.providers làm."""

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None, timeout: float = 20.0,
                 pin_dns: bool = True):
        """Ghim DNS (v0.1.27 cho Gen-hub; v0.1.45 mặc định cho MỌI máy chủ MCP): phân giải một lần + kết nối thẳng IP
        đã kiểm (`pin_endpoint`), bỏ qua proxy môi trường (proxy sẽ tự phân giải lại tên máy — mất tác dụng ghim).
        Có proxy HTTPS_PROXY/HTTP_PROXY/ALL_PROXY thì vẫn kiểm `pin_endpoint` (cấm vùng xấu) và vẫn đi thẳng như hub.
        `pin_dns=False` chỉ còn cho tương thích: vẫn kiểm `pin_endpoint` nhưng kết nối theo tên máy."""
        self._transport, self._timeout, self._pin = transport, timeout, pin_dns

    async def _rpc(self, server: ServerLike, method: str, params: dict[str, Any],
                   headers: dict[str, str]) -> Any:
        body = {"jsonrpc": "2.0", "id": "gh-1", "method": method, "params": params}
        hdrs = {**headers, "content-type": "application/json"}
        has_token = any(k.lower() == "authorization" and v for k, v in headers.items())
        token = next((v.split(" ", 1)[-1] for k, v in headers.items() if k.lower() == "authorization" and v), "")
        try:
            if self._pin:
                async with pinned_client(self._transport, self._timeout) as c:
                    resp = await pinned_request(c, "POST", server.endpoint, json=body, headers=hdrs,
                                                allow_public_network=server.allow_public_network,
                                                has_token=has_token)
            else:
                await pin_endpoint(server.endpoint, server.allow_public_network, has_token=has_token)
                async with httpx.AsyncClient(transport=self._transport, timeout=self._timeout,
                                             trust_env=False, follow_redirects=False) as c:
                    resp = await c.post(server.endpoint, json=body, headers=hdrs)
        except httpx.HTTPError as e:
            raise McpError(f"mạng: {mask_error(str(e), secrets=(token,))}") from e
        secrets = (token,) if token else ()
        if resp.status_code >= 400:
            # Thân lỗi có thể phản chiếu header/tham số — che + cắt 200 ký tự trước khi vào McpError (log, WS).
            raise McpError(f"{resp.status_code}: {mask_error(resp.text, secrets=secrets)}")
        try:
            data = resp.json()
        except ValueError as e:
            raise McpError(f"phản hồi không phải JSON: {mask_error(resp.text, secrets=secrets)}") from e
        if isinstance(data, dict) and data.get("error"):
            raise McpError(mask_error(str(data["error"]), secrets=secrets, limit=300))
        return data.get("result") if isinstance(data, dict) else data

    def _headers(self, server: ServerLike, auth_token: str | None) -> dict[str, str]:
        return {"authorization": f"Bearer {auth_token}"} if auth_token else {}

    def _check(self, server: ServerLike) -> None:
        if server.transport not in HTTP_TRANSPORTS:
            raise McpTransportUnsupported(
                f"Transport '{server.transport}' chưa hỗ trợ gọi trực tiếp trong phạm vi này")

    async def list_tools(self, server: ServerLike, auth_token: str | None = None) -> list[ToolSpec]:
        self._check(server)
        result = await self._rpc(server, "tools/list", {}, self._headers(server, auth_token))
        tools = (result or {}).get("tools", []) if isinstance(result, dict) else (result or [])
        out = []
        for t in tools:
            ann = t.get("annotations") or {}
            out.append(ToolSpec(name=t["name"], description=t.get("description") or "",
                                input_schema=t.get("inputSchema") or {}, read_only=ann.get("readOnlyHint")))
        return out

    async def call_tool(self, server: ServerLike, tool_name: str, args: dict[str, Any],
                        auth_token: str | None = None) -> dict[str, Any]:
        self._check(server)
        result = await self._rpc(server, "tools/call", {"name": tool_name, "arguments": args},
                                 self._headers(server, auth_token))
        return result if isinstance(result, dict) else {"result": result}


__all__ = ["COMPOSE_SERVICE_NAMES", "McpClient", "McpError", "McpBlockedNetwork", "McpTransportUnsupported",
           "PinnedTarget", "ToolSpec", "always_forbidden", "check_network_guard", "forbidden_host", "pin_endpoint",
           "pinned_client", "pinned_request", "resolves_public"]
