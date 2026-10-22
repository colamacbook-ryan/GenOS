"""Gói sửa review v0.1.63 — P2-2: proxy lối ra `agy-egress` của hộp riêng Antigravity.

Hộp chỉ còn mạng internal; lối ra duy nhất là proxy CONNECT (gh.agybox.egress, cùng ảnh api): chặn đích là /
phân giải ra IP nội bộ (loopback, RFC1918, link-local, CGNAT, gateway, IPv6 nội bộ), nối thẳng IP đã kiểm (chống
DNS rebinding), tuỳ chọn danh sách tên miền; hộp đặt HTTPS_PROXY tới proxy và agy nhận biến đó qua `cli_env`."""

import asyncio
from pathlib import Path
from typing import Any

import pytest
import yaml

from gh.agybox import egress
from gh.agybox.egress import Egress, host_allowed, ip_forbidden, parse_target
from gh.providers.clients import cli_env

REPO = Path(__file__).resolve().parents[3]


@pytest.mark.parametrize("ip", [
    "127.0.0.1", "10.0.0.5", "172.17.0.1", "172.31.255.1", "192.168.1.10", "169.254.169.254", "100.64.0.1",
    "100.127.255.254", "0.0.0.0", "224.0.0.1", "240.0.0.1", "::1", "fe80::1", "fc00::1", "fd12:3456::1",
    "::ffff:192.168.1.1", "::ffff:127.0.0.1", "2002:c0a8:0101::1", "64:ff9b::a00:1", "khong-phai-ip", "192.0.2.1",
    "198.18.0.1"])
def test_internal_ips_forbidden(ip: str) -> None:
    assert ip_forbidden(ip)


@pytest.mark.parametrize("ip", ["142.250.72.14", "8.8.8.8", "2607:f8b0:4004:c07::64", "::ffff:142.250.72.14"])
def test_public_ips_allowed(ip: str) -> None:
    assert not ip_forbidden(ip)


def test_parse_target() -> None:
    assert parse_target("CONNECT accounts.google.com:443 HTTP/1.1") == ("accounts.google.com", 443)
    assert parse_target("CONNECT [2607:f8b0::1]:443 HTTP/1.1") == ("2607:f8b0::1", 443)
    assert parse_target("CONNECT Accounts.Google.COM.:443 HTTP/1.1") == ("accounts.google.com", 443)
    for bad in ("GET http://x/ HTTP/1.1", "CONNECT x HTTP/1.1", "CONNECT x:abc HTTP/1.1", "CONNECT :443 HTTP/1.1",
                "CONNECT x:0 HTTP/1.1", "CONNECT x:70000 HTTP/1.1", "CONNECT [::1:443 HTTP/1.1",
                "CONNECT x:443 SPDY/3"):
        assert parse_target(bad) is None, bad


def test_host_allowlist() -> None:
    assert host_allowed("anything.example", ())
    allow = ("googleapis.com", "accounts.google.com")
    assert host_allowed("oauth2.googleapis.com", allow) and host_allowed("accounts.google.com", allow)
    assert not host_allowed("evilgoogleapis.com", allow) and not host_allowed("google.com", allow)
    assert not host_allowed("142.250.72.14", allow)                   # có danh sách ⇒ IP trần bị chặn


def test_config_from_env() -> None:
    assert egress.config_from_env({}) == ((), frozenset({443}), 3128)
    allow, ports, listen = egress.config_from_env({"GH_AGY_EGRESS_ALLOW": " *.googleapis.com, accounts.google.com ,",
                                                   "GH_AGY_EGRESS_PORTS": "443,8443", "GH_AGY_EGRESS_PORT": "3129"})
    assert allow == ("googleapis.com", "accounts.google.com") and ports == frozenset({443, 8443}) and listen == 3129
    with pytest.raises(RuntimeError):
        egress.config_from_env({"GH_AGY_EGRESS_PORTS": "x"})


# ─── proxy thật trên 127.0.0.1 ────────────────────────────────────────────────────────────────────────────────

async def _upstream() -> tuple[asyncio.Server, int]:
    async def echo(r: asyncio.StreamReader, w: asyncio.StreamWriter) -> None:
        data = await r.read(100)
        w.write(b"pong:" + data)
        await w.drain()
        w.close()

    srv = await asyncio.start_server(echo, "127.0.0.1", 0)
    return srv, srv.sockets[0].getsockname()[1]


async def _ask(eg: Egress, line: bytes) -> tuple[bytes, Any]:
    srv = await asyncio.start_server(eg.handle, "127.0.0.1", 0)
    port = srv.sockets[0].getsockname()[1]
    r, w = await asyncio.open_connection("127.0.0.1", port)
    w.write(line + b"\r\n\r\n")
    await w.drain()
    first = await asyncio.wait_for(r.readuntil(b"\r\n\r\n"), 5)
    return first, (srv, r, w)


async def _close(ctx: Any) -> None:
    srv, _r, w = ctx
    w.close()
    srv.close()


async def test_connect_to_public_name_pins_checked_ip() -> None:
    up, up_port = await _upstream()
    asked: list[str] = []

    async def resolver(host: str, port: int) -> list[str]:
        asked.append(host)
        return ["127.0.0.1"]

    eg = Egress((), frozenset({443}), resolver=resolver, allow_private=True, upstream_port=up_port)
    first, ctx = await _ask(eg, b"CONNECT accounts.google.com:443 HTTP/1.1")
    assert first.startswith(b"HTTP/1.1 200")
    _srv, r, w = ctx
    w.write(b"ping")
    await w.drain()
    assert await asyncio.wait_for(r.read(100), 5) == b"pong:ping"
    assert asked == ["accounts.google.com"]                            # phân giải đúng một lần
    await _close(ctx)
    up.close()


@pytest.mark.parametrize("line", [b"CONNECT 192.168.1.10:443 HTTP/1.1", b"CONNECT 10.0.0.1:443 HTTP/1.1",
                                  b"CONNECT 172.17.0.1:443 HTTP/1.1", b"CONNECT 127.0.0.1:443 HTTP/1.1",
                                  b"CONNECT [::1]:443 HTTP/1.1", b"CONNECT 169.254.169.254:443 HTTP/1.1",
                                  b"CONNECT 100.100.100.100:443 HTTP/1.1"])
async def test_connect_to_internal_ip_refused(line: bytes) -> None:
    first, ctx = await _ask(Egress(), line)
    assert first.startswith(b"HTTP/1.1 403")
    await _close(ctx)


async def test_name_resolving_to_internal_ip_refused_even_if_one_is_public() -> None:
    async def rebinding(host: str, port: int) -> list[str]:
        return ["142.250.72.14", "192.168.1.1"]

    first, ctx = await _ask(Egress(resolver=rebinding), b"CONNECT rebind.example:443 HTTP/1.1")
    assert first.startswith(b"HTTP/1.1 403")
    await _close(ctx)


async def test_other_port_and_plain_http_refused() -> None:
    for line, code in ((b"CONNECT accounts.google.com:22 HTTP/1.1", b"403"),
                       (b"GET http://accounts.google.com/ HTTP/1.1", b"405")):
        first, ctx = await _ask(Egress(), line)
        assert first.startswith(b"HTTP/1.1 " + code), line
        await _close(ctx)


async def test_allowlist_blocks_other_domains() -> None:
    async def never(host: str, port: int) -> list[str]:
        raise AssertionError("không được phân giải tên miền ngoài danh sách")

    first, ctx = await _ask(Egress(("googleapis.com",), resolver=never), b"CONNECT example.com:443 HTTP/1.1")
    assert first.startswith(b"HTTP/1.1 403")
    await _close(ctx)


# ─── nối dây: hộp dùng proxy, agy nhận biến proxy ───────────────────────────────────────────────────────────

def test_cli_env_passes_proxy_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HTTPS_PROXY", "http://agy-egress:3128")
    monkeypatch.setenv("GH_MASTER_KEY_FILE", "/run/secrets/x")
    env = cli_env("/x/.gemini/antigravity-cli", home="/tmp/h")
    assert env["HTTPS_PROXY"] == "http://agy-egress:3128" and env["HOME"] == "/tmp/h"
    assert not [k for k in env if k.startswith("GH_")]


def test_compose_box_only_internal_and_egress_wired() -> None:
    doc = yaml.safe_load((REPO / "deploy" / "compose.yaml").read_text(encoding="utf-8"))
    svc, nets = doc["services"], doc["networks"]
    box, eg = svc["agy-box"], svc["agy-egress"]
    assert set(box["networks"]) == {"agy-bus"} and nets["agy-bus"]["internal"] is True
    assert set(eg["networks"]) == {"agy-bus", "agy-out"} and not nets["agy-out"].get("internal")
    assert box["environment"]["HTTPS_PROXY"] == "http://agy-egress:3128"
    assert box["environment"]["HTTP_PROXY"] == "http://agy-egress:3128"
    # Kiểm thật (project ghboxfix): thiếu NO_PROXY thì healthcheck urllib tới 127.0.0.1:8090 cũng đi qua proxy ⇒ 405
    # ⇒ hộp "unhealthy". Địa chỉ nội bộ của chính hộp phải đi thẳng.
    no_proxy = {x.strip() for x in str(box["environment"]["NO_PROXY"]).split(",")}
    assert {"127.0.0.1", "localhost"} <= no_proxy
    assert eg["command"] == ["python", "-m", "gh.agybox.egress"]
    assert eg["build"]["dockerfile"] == "deploy/images/api.Dockerfile"            # cùng ảnh api — không thêm ảnh
    assert "secrets" not in eg and "volumes" not in eg and eg["read_only"] is True and eg["cap_drop"] == ["ALL"]
    assert {k for k in eg.get("environment", {}) if k.startswith("GH_")} <= {"GH_AGY_EGRESS_ALLOW",
                                                                               "GH_AGY_EGRESS_PORTS"}
    assert box["depends_on"]["agy-egress"] == {"condition": "service_started"}
