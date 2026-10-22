"""Proxy ra ngoài cho browser-worker: `python -m ghb.egress` (dịch vụ `browser-egress`).

browser-worker nằm trên mạng nội bộ `browser` (không có đường ra Internet); lối ra DUY NHẤT là proxy này:
- chỉ nhận `CONNECT host:443` (HTTPS) — http thường, cổng khác → 403;
- `host` phải thuộc danh sách tên miền nền tảng (`GH_EGRESS_ALLOW`, mặc định tên miền Facebook);
- phân giải DNS MỘT lần, MỌI IP phải là IP công cộng (chặn 10.x, 127.x, 169.254.x, 172.16–31.x, 192.168.x, IPv6 nội bộ,
  IPv4-mapped…), rồi nối thẳng IP đã kiểm (chống DNS rebinding) — giống ghim DNS Gen-hub (v0.1.27);
- không ghi nội dung, chỉ log tên miền bị chặn.
Không xoay IP, không chuỗi proxy — lưu lượng đi ra từ đúng IP máy chủ của Owner.
"""

import asyncio
import contextlib
import logging
import os
import socket
from collections.abc import Awaitable, Callable

from ghb.guard import host_allowed, ip_forbidden

log = logging.getLogger("ghb.egress")

DEFAULT_ALLOW = ("facebook.com", "fbcdn.net", "facebook.net", "fbsbx.com", "messenger.com")
Resolver = Callable[[str, int], Awaitable[list[str]]]


async def system_resolve(host: str, port: int) -> list[str]:
    infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return list(dict.fromkeys(str(i[4][0]) for i in infos))


class Egress:
    def __init__(self, allow: tuple[str, ...], *, resolver: Resolver = system_resolve, allow_private: bool = False,
                 connect_timeout: float = 10.0, upstream_port: int = 443):
        # allow_private / upstream_port: CHỈ dùng trong test (máy chủ giả ở 127.0.0.1) — main() không bao giờ đặt.
        self.allow, self.resolver, self.allow_private = allow, resolver, allow_private
        self.connect_timeout, self.upstream_port = connect_timeout, upstream_port

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=10)
        except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, TimeoutError):
            writer.close()
            return
        line = head.split(b"\r\n", 1)[0].decode("latin-1")
        parts = line.split()
        if len(parts) != 3 or parts[0].upper() != "CONNECT":
            await self._deny(writer, 405, "chỉ nhận CONNECT")
            return
        host, _, port_s = parts[1].rpartition(":")
        host = host.strip("[]").lower()
        if port_s != "443" or not host_allowed(host, self.allow):
            log.info("chặn %s", parts[1][:120])
            await self._deny(writer, 403, "tên miền không được phép")
            return
        try:
            ips = await self.resolver(host, 443)
        except OSError:
            await self._deny(writer, 502, "không phân giải được")
            return
        if not ips or (not self.allow_private and any(ip_forbidden(ip) for ip in ips)):
            log.warning("chặn %s: phân giải ra IP nội bộ", host)
            await self._deny(writer, 403, "IP nội bộ")
            return
        upstream: tuple[asyncio.StreamReader, asyncio.StreamWriter] | None = None
        for ip in ips:
            with contextlib.suppress(OSError, TimeoutError):
                upstream = await asyncio.wait_for(asyncio.open_connection(ip, self.upstream_port),
                                                  timeout=self.connect_timeout)
                break
        if upstream is None:
            await self._deny(writer, 502, "không nối được")
            return
        writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
        await writer.drain()
        up_r, up_w = upstream
        await asyncio.gather(self._pipe(reader, up_w), self._pipe(up_r, writer), return_exceptions=True)

    @staticmethod
    async def _pipe(src: asyncio.StreamReader, dst: asyncio.StreamWriter) -> None:
        try:
            while data := await src.read(65536):
                dst.write(data)
                await dst.drain()
        finally:
            with contextlib.suppress(Exception):
                dst.close()

    @staticmethod
    async def _deny(writer: asyncio.StreamWriter, code: int, why: str) -> None:
        reason = {403: "Forbidden", 405: "Method Not Allowed", 502: "Bad Gateway"}.get(code, "Error")
        body = why.encode()
        writer.write(f"HTTP/1.1 {code} {reason}\r\nContent-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode()
                     + body)
        with contextlib.suppress(Exception):
            await writer.drain()
            writer.close()


def allow_list() -> tuple[str, ...]:
    raw = os.environ.get("GH_EGRESS_ALLOW", "")
    items = tuple(x.strip().lower() for x in raw.split(",") if x.strip())
    return items or DEFAULT_ALLOW


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    port = int(os.environ.get("GH_EGRESS_PORT", "3128"))
    eg = Egress(allow_list())
    server = await asyncio.start_server(eg.handle, "0.0.0.0", port)
    log.info("browser-egress nghe :%d, cho phép %s", port, ", ".join(eg.allow))
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
