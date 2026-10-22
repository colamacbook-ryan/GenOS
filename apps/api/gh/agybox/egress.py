"""Proxy lối ra của hộp riêng Antigravity: `python -m gh.agybox.egress` (dịch vụ `agy-egress`, review v0.1.63 P2-2).

Trước: hộp ở mạng `agy-out` (bridge thường) ⇒ tới được gateway Docker và mọi dịch vụ trong LAN của máy chủ
(Gen-hub, router, NAS…) trong khi hộp giữ token Google của Sếp. Sau: hộp CHỈ ở mạng internal; lối ra duy nhất là
proxy này (cùng ảnh api, không thêm ảnh), nằm ở mạng internal của hộp + `agy-out`:
- chỉ nhận `CONNECT host:port` (HTTPS); cổng phải nằm trong danh sách cổng (`GH_AGY_EGRESS_PORTS`, mặc định 443);
- tuỳ chọn danh sách tên miền (`GH_AGY_EGRESS_ALLOW`, phân cách dấu phẩy; RỖNG = mọi tên miền công khai — Nợ:
  allowlist chặt khi đã đo đủ tên miền agy dùng với phiên thật). Có danh sách thì IP trần cũng bị chặn;
- phân giải DNS MỘT lần, MỌI địa chỉ phải là IP định tuyến toàn cầu (chặn loopback, RFC1918, link-local, CGNAT
  100.64/10, multicast, dự trữ, IPv6 nội bộ / ULA, IPv4-mapped, 6to4/NAT64 nhúng IP nội bộ) — gateway của mạng
  Docker và IP LAN của máy chủ đều là IP riêng nên bị chặn; rồi nối THẲNG IP đã kiểm (chống DNS rebinding);
- không đọc nội dung (TLS đầu-cuối), không ghi prompt/token; log chỉ tên miền bị chặn;
- review lần 2 (P3-3): tối đa `DEFAULT_MAX_CONNS` kết nối cùng lúc (đầy ⇒ 503), hết giờ rỗi tính theo cả đường hầm,
  kết nối đích luôn được đóng khi đường hầm kết thúc (kể cả client bỏ đi ngay sau "200"), chặn fec0::/10.
Chỉ stdlib — không import gh.config/DB/Redis (chạy cùng ảnh nhưng không có khoá nào)."""

import asyncio
import contextlib
import ipaddress
import logging
import os
import socket
from collections.abc import Awaitable, Callable

log = logging.getLogger("agy-egress")

ENV_ALLOW = "GH_AGY_EGRESS_ALLOW"
ENV_PORTS = "GH_AGY_EGRESS_PORTS"
ENV_LISTEN_PORT = "GH_AGY_EGRESS_PORT"
DEFAULT_LISTEN_PORT = 3128
HEAD_LIMIT = 8192
#: Kết nối đồng thời tối đa (hộp chạy MỘT lượt agy mỗi lúc — vài kết nối là đủ; giới hạn để agy bị lừa không mở hàng
#: nghìn đường hầm làm cạn bộ nhớ 128 MiB / số tiến trình của proxy).
DEFAULT_MAX_CONNS = 64
Resolver = Callable[[str, int], Awaitable[list[str]]]
_NAT64 = ipaddress.ip_network("64:ff9b::/96")


def ip_forbidden(ip: str) -> bool:
    """IP không định tuyến toàn cầu (nội bộ, loopback, link-local, CGNAT, multicast, dự trữ…) ⇒ cấm."""
    try:
        addr = ipaddress.ip_address(ip.split("%", 1)[0])
    except ValueError:
        return True
    if isinstance(addr, ipaddress.IPv6Address):
        if addr in _NAT64:                  # 64:ff9b::/96 nhúng IPv4 ở 32 bit cuối — xét IPv4 đó
            addr = ipaddress.IPv4Address(int(addr) & 0xFFFFFFFF)
        mapped = addr.ipv4_mapped or addr.sixtofour if isinstance(addr, ipaddress.IPv6Address) else None
        if mapped is not None:
            addr = mapped
        elif isinstance(addr, ipaddress.IPv6Address) and addr.teredo is not None:
            return True
    if isinstance(addr, ipaddress.IPv6Address) and addr.is_site_local:
        return True                         # fec0::/10 (site-local cũ, đã bỏ) — `ipaddress` vẫn coi là global
    return (not addr.is_global or addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_multicast
            or addr.is_reserved or addr.is_unspecified)


def host_allowed(host: str, allow: tuple[str, ...]) -> bool:
    """Danh sách rỗng ⇒ mọi tên miền; có danh sách ⇒ đúng tên miền hoặc tên miền con, IP trần không được."""
    if not allow:
        return True
    h = host.lower().rstrip(".")
    with contextlib.suppress(ValueError):
        ipaddress.ip_address(h)
        return False
    return any(h == d or h.endswith("." + d) for d in allow)


async def system_resolve(host: str, port: int) -> list[str]:
    infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return list(dict.fromkeys(str(i[4][0]) for i in infos))


def parse_target(line: str) -> tuple[str, int] | None:
    """`CONNECT host:port HTTP/1.1` → (host, port); sai khuôn ⇒ None. IPv6 dạng `[::1]:443`."""
    parts = line.split()
    if len(parts) != 3 or parts[0].upper() != "CONNECT" or not parts[2].upper().startswith("HTTP/1."):
        return None
    host, sep, port_s = parts[1].rpartition(":")
    if not sep or not port_s.isdigit() or not host:
        return None
    if host.startswith("["):
        if not host.endswith("]"):
            return None
        host = host[1:-1]
    port = int(port_s)
    if not 0 < port < 65536 or len(host) > 253 or any(c.isspace() or ord(c) < 33 for c in host):
        return None
    return host.lower().rstrip("."), port


class Egress:
    def __init__(self, allow: tuple[str, ...] = (), ports: frozenset[int] = frozenset({443}), *,
                 resolver: Resolver = system_resolve, allow_private: bool = False, connect_timeout: float = 10.0,
                 upstream_port: int | None = None, idle_timeout: float = 600.0, max_conns: int = DEFAULT_MAX_CONNS):
        # allow_private / upstream_port: CHỈ dùng trong test (máy chủ giả ở 127.0.0.1) — main() không bao giờ đặt.
        self.allow, self.ports, self.resolver, self.allow_private = allow, ports, resolver, allow_private
        self.connect_timeout, self.upstream_port, self.idle_timeout = connect_timeout, upstream_port, idle_timeout
        self.max_conns, self.active = max_conns, 0

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        if self.active >= self.max_conns:
            log.warning("chặn kết nối mới: đã đủ %d kết nối đồng thời", self.max_conns)
            await self._deny(writer, 503, "proxy đang quá tải")
            return
        self.active += 1
        try:
            await self._handle(reader, writer)
        finally:
            self.active -= 1

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=10)
        except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, TimeoutError, ConnectionError):
            writer.close()
            return
        line = head.split(b"\r\n", 1)[0].decode("latin-1")
        target = parse_target(line)
        if target is None:
            await self._deny(writer, 405, "chỉ nhận CONNECT host:port")
            return
        host, port = target
        if port not in self.ports or not host_allowed(host, self.allow):
            log.info("chặn %s:%d (tên miền / cổng không được phép)", host[:120], port)
            await self._deny(writer, 403, "đích không được phép")
            return
        try:
            ips = await asyncio.wait_for(self.resolver(host, port), timeout=self.connect_timeout)
        except (OSError, TimeoutError):
            await self._deny(writer, 502, "không phân giải được")
            return
        if not ips or (not self.allow_private and any(ip_forbidden(ip) for ip in ips)):
            log.warning("chặn %s: là / phân giải ra IP nội bộ", host[:120])
            await self._deny(writer, 403, "IP nội bộ")
            return
        upstream: tuple[asyncio.StreamReader, asyncio.StreamWriter] | None = None
        for ip in ips:                      # nối THẲNG IP đã kiểm — không phân giải lại (chống DNS rebinding)
            with contextlib.suppress(OSError, TimeoutError):
                upstream = await asyncio.wait_for(asyncio.open_connection(ip, self.upstream_port or port),
                                                  timeout=self.connect_timeout)
                break
        if upstream is None:
            await self._deny(writer, 502, "không nối được")
            return
        up_r, up_w = upstream
        try:
            writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            await writer.drain()
            last = [asyncio.get_running_loop().time()]       # lần cuối có dữ liệu ở BẤT KỲ chiều nào
            await asyncio.gather(self._pipe(reader, up_w, last), self._pipe(up_r, writer, last),
                                 return_exceptions=True)
        except (ConnectionError, OSError):
            pass                                             # client bỏ đi ngay sau "200"
        finally:
            for w in (up_w, writer):
                with contextlib.suppress(Exception):
                    w.close()

    async def _pipe(self, src: asyncio.StreamReader, dst: asyncio.StreamWriter, last: list[float]) -> None:
        """Chép một chiều. Hết giờ rỗi tính theo CẢ đường hầm (`last` dùng chung hai chiều): tải xuống dài mà client
        im lặng không bị cắt; chỉ khi cả hai chiều im quá `idle_timeout` mới đóng."""
        loop = asyncio.get_running_loop()
        try:
            while True:
                remaining = self.idle_timeout - (loop.time() - last[0])
                if remaining <= 0:
                    return
                try:
                    data = await asyncio.wait_for(src.read(65536), timeout=remaining)
                except TimeoutError:
                    continue                                 # xét lại: chiều kia có thể vừa có dữ liệu
                if not data:
                    return
                last[0] = loop.time()
                dst.write(data)
                await dst.drain()
        finally:
            with contextlib.suppress(Exception):
                dst.close()

    @staticmethod
    async def _deny(writer: asyncio.StreamWriter, code: int, why: str) -> None:
        reason = {403: "Forbidden", 405: "Method Not Allowed", 502: "Bad Gateway",
                  503: "Service Unavailable"}.get(code, "Error")
        body = why.encode()
        writer.write(f"HTTP/1.1 {code} {reason}\r\nContent-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode()
                     + body)
        with contextlib.suppress(Exception):
            await writer.drain()
            writer.close()


def config_from_env(env: dict[str, str] | None = None) -> tuple[tuple[str, ...], frozenset[int], int]:
    e = dict(os.environ if env is None else env)
    allow = tuple(x.strip().lower().lstrip("*.").rstrip(".") for x in e.get(ENV_ALLOW, "").split(",") if x.strip())
    try:
        ports = frozenset(int(x) for x in (e.get(ENV_PORTS) or "443").split(",") if x.strip())
        listen = int(e.get(ENV_LISTEN_PORT) or DEFAULT_LISTEN_PORT)
    except ValueError as exc:
        raise RuntimeError(f"{ENV_PORTS} / {ENV_LISTEN_PORT} phải là số nguyên") from exc
    if not ports or any(not 0 < p < 65536 for p in ports):
        raise RuntimeError(f"{ENV_PORTS} không hợp lệ")
    return allow, ports, listen


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    allow, ports, listen = config_from_env()
    eg = Egress(allow, ports)
    server = await asyncio.start_server(eg.handle, "0.0.0.0", listen, limit=HEAD_LIMIT)  # noqa: S104 — mạng internal
    log.info("agy-egress nghe :%d, cổng %s, tên miền: %s", listen, sorted(ports),
             ", ".join(allow) if allow else "mọi tên miền công khai (chặn IP nội bộ)")
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
