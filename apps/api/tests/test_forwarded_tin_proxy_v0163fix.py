"""Gói sửa review v0.1.63 — P3: api chỉ tin header chuyển tiếp (X-Forwarded-For, X-Gh-Upstream-Xff, Cf-Connecting-IP)
khi kết nối đến TỪ proxy (Caddy, dịch vụ `proxy`) hoặc loopback. Trước: uvicorn `forwarded_allow_ips="*"` và
`client_ip` tin mọi kết nối ⇒ hộp agy (mạng agy-bus, tới được api:8000) tự đặt IP giả — vượt bộ đếm đăng nhập theo IP,
ghi IP giả vào Action Log."""

import ast
from pathlib import Path
from typing import Any

import pytest
from starlette.requests import Request

from gh.auth import deps
from gh.config import get_settings

API_DIR = Path(__file__).resolve().parents[1]


def _req(headers: dict[str, str], client: str | None) -> Any:
    raw = [(k.lower().encode(), v.encode()) for k, v in headers.items()]
    scope: dict[str, Any] = {"type": "http", "method": "POST", "path": "/", "headers": raw, "query_string": b""}
    if client is not None:
        scope["client"] = (client, 1234)
    return Request(scope)


@pytest.fixture(autouse=True)
def _fresh(monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    deps.reset_trusted_proxy_cache()
    monkeypatch.setattr(get_settings(), "access_mode", "")
    monkeypatch.setattr(get_settings(), "bind_addr", "")
    yield
    deps.reset_trusted_proxy_cache()


def test_untrusted_peer_cannot_spoof_ip(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "trusted_proxies", "172.18.0.5")
    box = "172.21.0.3"                                                   # hộp agy trên agy-bus
    assert deps.client_ip(_req({"X-Forwarded-For": "203.0.113.7"}, box)) == box
    monkeypatch.setattr(get_settings(), "bind_addr", "127.0.0.1")
    monkeypatch.setattr(get_settings(), "access_mode", "cloudflare")
    assert deps.client_ip(_req({"Cf-Connecting-IP": "198.51.100.9", "X-Gh-Upstream-Xff": "1.2.3.4"}, box)) == box


def test_trusted_proxy_and_loopback_keep_old_behaviour(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "trusted_proxies", "172.18.0.0/16")
    assert deps.client_ip(_req({"X-Forwarded-For": "203.0.113.7"}, "172.18.0.5")) == "203.0.113.7"
    assert deps.client_ip(_req({"X-Forwarded-For": "203.0.113.8"}, "127.0.0.1")) == "203.0.113.8"   # gọi nội bộ ASGI
    assert deps.client_ip(_req({}, "172.18.0.5")) == "172.18.0.5"


def test_trusted_proxy_by_hostname(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "trusted_proxies", "proxy")
    monkeypatch.setattr(deps, "_resolve", lambda host: ["172.18.0.9"] if host == "proxy" else [])
    deps.refresh_trusted_proxies()      # P3-2 (review lần 2): tên được phân giải ở luồng nền / lúc khởi động
    assert deps.client_ip(_req({"X-Forwarded-For": "203.0.113.7"}, "172.18.0.9")) == "203.0.113.7"
    assert deps.client_ip(_req({"X-Forwarded-For": "203.0.113.7"}, "172.18.0.10")) == "172.18.0.10"


def test_unresolvable_proxy_trusts_only_loopback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "trusted_proxies", "proxy")

    def boom(host: str) -> list[str]:
        raise OSError("không phân giải được")

    monkeypatch.setattr(deps, "_resolve", boom)
    assert deps.client_ip(_req({"X-Forwarded-For": "203.0.113.7"}, "172.18.0.9")) == "172.18.0.9"
    assert deps.client_ip(_req({"X-Forwarded-For": "203.0.113.7"}, None)) is None


def test_uvicorn_does_not_trust_every_forwarder() -> None:
    tree = ast.parse((API_DIR / "gh" / "main.py").read_text(encoding="utf-8"))
    kws = {k.arg: ast.unparse(k.value) for n in ast.walk(tree) if isinstance(n, ast.Call)
           and ast.unparse(n.func).endswith("uvicorn.run") for k in n.keywords}
    assert kws.get("proxy_headers") == "False" and "forwarded_allow_ips" not in kws
