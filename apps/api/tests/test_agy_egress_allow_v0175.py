"""v0.1.75 (review bảo mật 22/10/2026, Nợ #44) — proxy lối ra của hộp agy mặc định CHỈ cho tên miền agy / Google cần.

Trước: `GH_AGY_EGRESS_ALLOW` rỗng = mọi tên miền công khai, trong khi hộp nhận token OAuth Google mỗi lượt ⇒ agy bị
lừa có thể gửi token tới tên miền của kẻ tấn công. Nay rỗng = `DEFAULT_ALLOW`; biến môi trường THÊM tên miền; `*` = mở
hết (cách cũ)."""

import asyncio
import logging
from pathlib import Path

import pytest
import yaml

from gh.agybox import egress
from gh.agybox.egress import Egress, host_allowed

REPO = Path(__file__).resolve().parents[3]


def test_default_allowlist_covers_measured_agy_hosts_only() -> None:
    allow, _, _ = egress.config_from_env({})
    # Đã đo (agy 1.2.9) + đường OAuth / model của Google.
    for host in ("antigravity-unleash.goog", "play.googleapis.com", "oauth2.googleapis.com",
                 "cloudcode-pa.googleapis.com", "openidconnect.googleapis.com", "accounts.google.com"):
        assert host_allowed(host, allow), host
    # Tên miền tuỳ ý / giống tên Google / IP trần đều bị chặn.
    for host in ("evil.example", "webhook.site", "pastebin.com", "evilgoogleapis.com", "google.com",
                 "mail.google.com", "accounts.google.com.evil.example", "142.250.72.14"):
        assert not host_allowed(host, allow), host


def test_env_extends_default_and_star_opens_all() -> None:
    allow, _, _ = egress.config_from_env({"GH_AGY_EGRESS_ALLOW": "gstatic.com, *.example.org."})
    assert allow[: len(egress.DEFAULT_ALLOW)] == egress.DEFAULT_ALLOW
    assert host_allowed("www.gstatic.com", allow) and host_allowed("a.example.org", allow)
    assert host_allowed("play.googleapis.com", allow)                 # thêm, không thay thế mặc định
    assert egress.config_from_env({"GH_AGY_EGRESS_ALLOW": "*"})[0] == ()
    assert host_allowed("anything.example", egress.config_from_env({"GH_AGY_EGRESS_ALLOW": " * "})[0])
    assert egress.config_from_env({"GH_AGY_EGRESS_ALLOW": " , ,"})[0] == egress.DEFAULT_ALLOW


async def test_blocked_host_is_denied_and_logged(caplog: pytest.LogCaptureFixture) -> None:
    allow, _, _ = egress.config_from_env({})

    async def never(host: str, port: int) -> list[str]:  # không được phân giải đích bị chặn
        raise AssertionError("không được phân giải tên miền bị chặn")

    eg = Egress(allow, resolver=never)
    srv = await asyncio.start_server(eg.handle, "127.0.0.1", 0)
    port = srv.sockets[0].getsockname()[1]
    caplog.set_level(logging.WARNING, logger="agy-egress")
    try:
        r, w = await asyncio.open_connection("127.0.0.1", port)
        w.write(b"CONNECT attacker.example:443 HTTP/1.1\r\n\r\n")
        await w.drain()
        head = await asyncio.wait_for(r.read(200), timeout=5)
        w.close()
    finally:
        srv.close()
        await srv.wait_closed()
    assert head.startswith(b"HTTP/1.1 403")
    assert any("attacker.example" in m and "GH_AGY_EGRESS_ALLOW" in m for m in caplog.messages)


@pytest.mark.parametrize("path", ["deploy/compose.yaml", "apps/genh/internal/compose/embedded_compose.yaml"])
def test_compose_leaves_default_to_code(path: str) -> None:
    """compose chỉ chuyển giá trị .env (mặc định rỗng ⇒ allowlist mặc định của mã); không đặt cứng `*`."""
    svc = yaml.safe_load((REPO / path).read_text())["services"]["agy-egress"]
    assert svc["environment"]["GH_AGY_EGRESS_ALLOW"] == "${GH_AGY_EGRESS_ALLOW:-}"
