"""v0.1.39 (F-31) — lỗi Gen-hub bị guard mạng MCP Hub chặn phải chỉ ĐÚNG công tắc trong thẻ Gen-hub.

Trước đây: địa chỉ Gen-hub ở mạng công cộng mà công tắc tắt → thẻ hiện câu thô "Owner chưa bật 'Cho phép máy chủ MCP
ngoài mạng nội bộ'" (tên công tắc của trang MCP Hub, không có trong thẻ Gen-hub). Server KHÔNG tự bật công tắc."""

from typing import Any

from sqlalchemy import text

from gh.chassis import mcp_client
from gh.db import admin_sessionmaker
from gh.hub_link import service as hub
from tests.conftest import Api
from tests.test_hub_link import TOKEN, FakeHub, _pin, fake_hub  # noqa: F401 — fixture dùng lại


def test_classify_network_blocked_public_points_to_card_switch() -> None:
    msg = "Máy chủ MCP ở mạng công cộng (8.8.8.8) — Owner chưa bật 'Cho phép máy chủ MCP ngoài mạng nội bộ'"
    assert hub._classify(msg, code="MCP_NETWORK_BLOCKED") == hub.PUBLIC_NET_HINT
    assert hub.PUBLIC_NET_HINT == "Bật 'Cho phép Gen-hub ở mạng công cộng' ngay trong thẻ này."


def test_classify_network_blocked_link_local_is_forbidden() -> None:
    msg = "Máy chủ MCP (169.254.169.254) phân giải ra vùng mạng bị cấm (link-local/siêu dữ liệu)"
    assert hub._classify(msg, code="MCP_NETWORK_BLOCKED") == hub.ENDPOINT_FORBIDDEN_MSG
    assert hub._error_code(hub.ENDPOINT_FORBIDDEN_MSG, code="MCP_NETWORK_BLOCKED") == "HUB_ENDPOINT_FORBIDDEN"


def test_classify_network_blocked_invalid_endpoint_is_not_forbidden() -> None:
    msg = mcp_client.INVALID_ENDPOINT_MSG
    got = hub._classify(msg, code="MCP_NETWORK_BLOCKED")
    assert got == hub.ENDPOINT_INVALID_MSG and got != hub.ENDPOINT_FORBIDDEN_MSG
    assert hub._error_code(got, code="MCP_NETWORK_BLOCKED") == "HUB_ENDPOINT_INVALID"


def test_classify_keeps_old_branches() -> None:
    assert hub._classify("401: unauthorized") == (
        "401: Token Gen-hub hết hạn hoặc đã bị thu hồi — tạo token mới trong Gen-hub rồi dán lại")
    assert hub._classify("403: x").startswith("403: Token Gen-hub")
    assert hub._classify("429: chậm lại").startswith("429:")
    assert hub._classify("mạng: hết giờ") == "Không kết nối được Gen-hub (mạng/timeout)"
    assert hub._classify("lỗi khác") == "lỗi khác"
    # Không có mã MCP_NETWORK_BLOCKED → câu "mạng công cộng" giữ nguyên (không đoán).
    assert hub._classify("mạng công cộng gì đó") == "mạng công cộng gì đó"
    codes = {m: hub._error_code(hub._classify(m)) for m in ("401: a", "403: b", "429: c", "mạng: d", "khác")}
    assert codes == {"401: a": "HUB_TOKEN_REJECTED", "403: b": "HUB_TOKEN_REJECTED", "429: c": "HUB_RATE_LIMITED",
                     "mạng: d": "HUB_UNREACHABLE", "khác": "HUB_ERROR"}


async def test_public_endpoint_blocked_hint_and_switch_untouched(owner_api: Api, fake_hub: FakeHub) -> None:  # noqa: F811
    await _pin(owner_api)
    r = await owner_api.send("PATCH", "/hub/link", {"endpoint": "https://8.8.8.8/mcp", "token": TOKEN,
                                                    "allow_public_network": False})
    assert r.status_code == 200, r.text
    r = await owner_api.send("POST", "/hub/link/test", {})
    assert r.status_code == 200, r.text
    body: dict[str, Any] = r.json()
    assert body["ok"] is False
    assert body["error"] == hub.PUBLIC_NET_HINT
    assert body["error_code"] == "MCP_NETWORK_BLOCKED"
    assert fake_hub.calls == [] and fake_hub.auth_seen == []          # không gọi ra mạng công cộng
    async with admin_sessionmaker()() as db:
        allow = (await db.execute(text("SELECT allow_public_network FROM agent.mcp_servers"))).scalar_one()
    assert allow is False                                              # server KHÔNG tự bật công tắc


async def test_ok_and_token_rejected_have_error_code(owner_api: Api, fake_hub: FakeHub) -> None:  # noqa: F811
    from tests.test_hub_link import _configure

    await _configure(owner_api)
    r = await owner_api.send("POST", "/hub/link/test", {})
    assert r.json()["ok"] is True and r.json()["error_code"] is None
    fake_hub.mode = "401"
    r = await owner_api.send("POST", "/hub/link/test", {})
    assert r.json()["ok"] is False and r.json()["error_code"] == "HUB_TOKEN_REJECTED"
