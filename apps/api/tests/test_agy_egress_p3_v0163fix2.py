"""Review lần 2 gói sửa v0.1.63 — P3-3: proxy lối ra `agy-egress`.

- giới hạn số kết nối đồng thời (đầy ⇒ 503 ngay, không mở thêm kết nối ra ngoài);
- `writer.drain()` sau "200 Connection Established" lỗi (client bỏ đi) ⇒ kết nối đích vẫn được đóng (trước: rò);
- hết giờ rỗi tính theo CẢ đường hầm (trước: từng chiều — tải xuống dài mà client im thì đường hầm bị cắt giữa chừng);
- chặn fec0::/10 (site-local IPv6 cũ — `ipaddress` coi là "global")."""

import asyncio
import contextlib
from typing import Any

import pytest

from gh.agybox.egress import Egress, ip_forbidden


async def _resolver(host: str, port: int) -> list[str]:
    return ["127.0.0.1"]


async def _proxy(eg: Egress) -> tuple[asyncio.Server, int]:
    srv = await asyncio.start_server(eg.handle, "127.0.0.1", 0)
    return srv, srv.sockets[0].getsockname()[1]


async def _connect(port: int) -> tuple[bytes, asyncio.StreamReader, asyncio.StreamWriter]:
    r, w = await asyncio.open_connection("127.0.0.1", port)
    w.write(b"CONNECT accounts.google.com:443 HTTP/1.1\r\n\r\n")
    await w.drain()
    first = await asyncio.wait_for(r.readuntil(b"\r\n\r\n"), 5)
    return first, r, w


@pytest.mark.parametrize("ip", ["fec0::1", "fec0:1234::5", "feff:ffff::1"])
def test_site_local_ipv6_forbidden(ip: str) -> None:
    assert ip_forbidden(ip)


async def test_concurrent_connection_limit() -> None:
    held: list[asyncio.StreamWriter] = []

    async def hold(r: asyncio.StreamReader, w: asyncio.StreamWriter) -> None:
        held.append(w)
        await r.read()                                               # giữ tới khi phía kia đóng

    up = await asyncio.start_server(hold, "127.0.0.1", 0)
    eg = Egress(resolver=_resolver, allow_private=True, upstream_port=up.sockets[0].getsockname()[1], max_conns=2)
    srv, port = await _proxy(eg)
    a = await _connect(port)
    b = await _connect(port)
    assert a[0].startswith(b"HTTP/1.1 200") and b[0].startswith(b"HTTP/1.1 200")
    c = await _connect(port)
    assert c[0].startswith(b"HTTP/1.1 503")
    assert len(held) == 2                                            # không mở thêm kết nối ra ngoài
    a[2].close()
    for _ in range(50):                                              # chỗ được trả khi đường hầm đóng
        if eg.active < 2:
            break
        await asyncio.sleep(0.05)
    d = await _connect(port)
    assert d[0].startswith(b"HTTP/1.1 200")
    for x in (b, c, d):
        x[2].close()
    srv.close()
    up.close()


class _GoneClient:
    """Client nhận "200" rồi bỏ đi: drain() lỗi."""

    def __init__(self) -> None:
        self.closed = False

    def write(self, data: bytes) -> None:
        pass

    async def drain(self) -> None:
        raise ConnectionResetError("client bỏ đi")

    def close(self) -> None:
        self.closed = True

    def get_extra_info(self, name: str, default: Any = None) -> Any:
        return default


async def test_upstream_closed_when_drain_after_200_fails() -> None:
    eof = asyncio.Event()

    async def watch(r: asyncio.StreamReader, w: asyncio.StreamWriter) -> None:
        with contextlib.suppress(ConnectionError):
            await r.read()
        eof.set()

    up = await asyncio.start_server(watch, "127.0.0.1", 0)
    eg = Egress(resolver=_resolver, allow_private=True, upstream_port=up.sockets[0].getsockname()[1])
    reader = asyncio.StreamReader()
    reader.feed_data(b"CONNECT accounts.google.com:443 HTTP/1.1\r\n\r\n")
    client = _GoneClient()
    with contextlib.suppress(ConnectionError):
        await eg.handle(reader, client)  # type: ignore[arg-type]
    await asyncio.wait_for(eof.wait(), 3)                            # kết nối đích đã đóng, không rò
    assert client.closed and eg.active == 0
    up.close()


async def test_idle_timeout_is_per_tunnel_not_per_direction() -> None:
    """Đích gửi dần 8 mảnh trong ~1,6 giây, client im lặng; idle 0,6 giây: đường hầm còn hoạt động ⇒ không bị cắt."""
    async def stream(r: asyncio.StreamReader, w: asyncio.StreamWriter) -> None:
        for i in range(8):
            w.write(b"m%d;" % i)
            await w.drain()
            await asyncio.sleep(0.2)
        w.close()

    up = await asyncio.start_server(stream, "127.0.0.1", 0)
    eg = Egress(resolver=_resolver, allow_private=True, upstream_port=up.sockets[0].getsockname()[1],
                idle_timeout=0.6)
    srv, port = await _proxy(eg)
    first, r, w = await _connect(port)
    assert first.startswith(b"HTTP/1.1 200")
    got = b""
    while chunk := await asyncio.wait_for(r.read(100), 5):
        got += chunk
    assert got == b"".join(b"m%d;" % i for i in range(8))
    w.close()
    srv.close()
    up.close()


async def test_silent_tunnel_is_closed_after_idle_timeout() -> None:
    async def silent(r: asyncio.StreamReader, w: asyncio.StreamWriter) -> None:
        await r.read()

    up = await asyncio.start_server(silent, "127.0.0.1", 0)
    eg = Egress(resolver=_resolver, allow_private=True, upstream_port=up.sockets[0].getsockname()[1],
                idle_timeout=0.4)
    srv, port = await _proxy(eg)
    first, r, w = await _connect(port)
    assert first.startswith(b"HTTP/1.1 200")
    assert await asyncio.wait_for(r.read(100), 3) == b""             # cả hai chiều im ⇒ đóng
    w.close()
    srv.close()
    up.close()
