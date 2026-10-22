"""Gói sửa review v0.1.63 — các mục P3 của hộp riêng Antigravity (giao thức, server, client)."""

import json
from typing import Any

import httpx
import pytest

from gh.agybox import protocol, server
from tests.test_agybox_v0163 import KEY, SESSION, URL, _body, _raw, box  # noqa: F401 — fixture dùng chung


def _signed(box: Any, rid: str) -> tuple[bytes, dict[str, str]]:  # noqa: F811
    body = json.dumps({"request_id": rid, "session": SESSION}).encode()
    return body, protocol.sign_request(KEY, "POST", "/v1/models", body, box.clock())


# ─── P3: bộ nhớ chữ ký chống phát lại không được "đầy rồi thôi ghi" ───────────────────────────────────────────

async def test_replay_cache_full_rejects_instead_of_forgetting(box, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    """Trước: đủ 4096 chữ ký còn hạn thì NGỪNG ghi ⇒ chữ ký mới phát lại được. Nay: đầy ⇒ từ chối (bận, thử lại),
    chữ ký đã nhận luôn bị nhớ tới khi hết cửa sổ."""
    monkeypatch.setattr(server, "SEEN_MAX", 2)
    app = server.create_app(box.cfg, clock=box.clock)
    t = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=t, base_url=URL) as c:
        for rid in ("a-1", "a-2"):
            body, h = _signed(box, rid)
            assert (await c.post("/v1/models", content=body, headers=h)).status_code == 200
        body3, h3 = _signed(box, "a-3")
        full = await c.post("/v1/models", content=body3, headers=h3)
        assert full.status_code == 429 and full.json()["error"]["code"] == "busy"
        again = await c.post("/v1/models", content=body3, headers=h3)
        assert again.status_code == 429                                      # chưa từng chạy ⇒ không bao giờ "lọt"
        box.clock.t += 2 * protocol.MAX_SKEW_S + 1                           # hết cửa sổ ⇒ mục cũ được dọn
        body4, h4 = _signed(box, "a-4")
        assert (await c.post("/v1/models", content=body4, headers=h4)).status_code == 200
        assert (await c.post("/v1/models", content=body4, headers=h4)).status_code == 401   # phát lại ⇒ 401


# ─── P3: chữ ký có ký tự ngoài ASCII ⇒ 401 (không ném lỗi), client so chữ ký thời gian hằng ─────────────────────

def test_verify_request_non_ascii_signature_is_bad_signature() -> None:
    now = 1_800_000_000.0
    h = protocol.sign_request(KEY, "POST", "/v1/models", b"{}", now)
    h[protocol.SIG_HEADER] = "é" + h[protocol.SIG_HEADER][1:]
    assert protocol.verify_request(KEY, "POST", "/v1/models", b"{}", h, now) == "bad_signature"
    h[protocol.TS_HEADER] = "١٨٠٠٠٠٠٠٠٠"                                   # chữ số Ả Rập: không nhận
    assert protocol.verify_request(KEY, "POST", "/v1/models", b"{}", h, now) == "bad_timestamp"


async def test_server_answers_401_for_non_ascii_signature(box) -> None:  # noqa: F811
    body, h = _signed(box, "n-1")
    h[protocol.SIG_HEADER] = "é" * 64
    async with httpx.AsyncClient(transport=box.transport, base_url=URL) as c:
        r = await c.post("/v1/models", content=body, headers={k: v.encode("latin-1") for k, v in h.items()})
    assert r.status_code == 401 and r.json()["error"]["code"] == "unauthorized"


async def test_client_rejects_non_ascii_response_signature(box) -> None:  # noqa: F811
    from gh.agybox.client import AgyBoxClient
    from gh.providers.clients import ProviderError

    inner = box.transport

    class Evil(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            r = await inner.handle_async_request(request)
            body = await r.aread()
            headers = [(k, v) for k, v in r.headers.raw if k.lower() not in (b"x-gh-sig", b"content-length")]
            return httpx.Response(r.status_code, headers=[*headers, (b"x-gh-sig", "é".encode("latin-1") * 64)],
                                  content=body)

    c = AgyBoxClient(URL, str(box.keyfile), 20, cli_home=str(box.home), transport=Evil(), clock=box.clock)
    with pytest.raises(ProviderError, match="chữ ký"):
        await c.list_models()


def test_client_compares_signatures_in_constant_time() -> None:
    import inspect

    from gh.agybox import client as boxclient

    src = inspect.getsource(boxclient.AgyBoxClient._call)
    assert "signature_equal" in src and "sig == P.response_signature" not in src
    assert "compare_digest" in inspect.getsource(protocol.signature_equal)


# ─── P3: client ngắt ⇒ dừng agy; mỗi lúc chỉ MỘT lượt agy trong hộp ─────────────────────────────────────────────

async def test_client_disconnect_kills_agy(box, tmp_path) -> None:  # noqa: F811
    import asyncio
    import os
    import time

    pidfile = tmp_path / "agy.pid"
    slow = tmp_path / "bin" / "agy-slow"
    slow.parent.mkdir(exist_ok=True)
    slow.write_text('#!/bin/sh\ncase "$1" in --version) echo 1.2.9; exit 0;; esac\n'
                    f'echo $$ > {pidfile}\nexec sleep 30\n')
    slow.chmod(0o755)
    box.cfg.cli_binary = str(slow)
    app = server.create_app(box.cfg, clock=box.clock)
    body = _body(prompt="lâu", timeout_s=60)
    headers = protocol.sign_request(KEY, "POST", "/v1/generate", body, box.clock())
    sent: list[dict[str, Any]] = []
    gone = asyncio.Event()
    msgs = [{"type": "http.request", "body": body, "more_body": False}]

    async def receive() -> dict[str, Any]:
        if msgs:
            return msgs.pop(0)
        await gone.wait()
        return {"type": "http.disconnect"}

    async def send(m: dict[str, Any]) -> None:
        sent.append(m)

    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "POST", "scheme": "http",
             "path": "/v1/generate", "raw_path": b"/v1/generate", "query_string": b"", "root_path": "",
             "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()]
             + [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())],
             "client": ("127.0.0.1", 5555), "server": ("agy-box", 8090)}
    task = asyncio.create_task(app(scope, receive, send))
    for _ in range(100):
        if pidfile.exists() and pidfile.read_text().strip():
            break
        await asyncio.sleep(0.05)
    pid = int(pidfile.read_text())
    started = time.monotonic()
    gone.set()                                                          # api ngắt (hết giờ / huỷ lượt)
    await asyncio.wait_for(task, 10)
    assert time.monotonic() - started < 5

    def alive(p: int) -> bool:
        try:
            os.kill(p, 0)
        except ProcessLookupError:
            return False
        try:
            return open(f"/proc/{p}/stat").read().split()[2] != "Z"
        except OSError:
            return False

    for _ in range(50):
        if not alive(pid):
            break
        await asyncio.sleep(0.1)
    assert not alive(pid)                                               # agy đã bị dừng, không chạy tiếp vô ích


def test_max_jobs_is_one() -> None:
    """Hai lượt agy cùng uid trong hộp đọc được /proc và HOME tạm của nhau ⇒ mỗi lúc một lượt."""
    assert server.Config(key_file="k").max_jobs == 1
    assert server.load_config({"GH_AGYBOX_KEY_FILE": "k"}).max_jobs == 1
    assert server.load_config({"GH_AGYBOX_KEY_FILE": "k", "GH_AGYBOX_MAX_JOBS": "4"}).max_jobs == 1


def test_compose_box_runs_one_job() -> None:
    from pathlib import Path

    import yaml

    doc = yaml.safe_load((Path(__file__).resolve().parents[3] / "deploy" / "compose.yaml").read_text(encoding="utf-8"))
    assert doc["services"]["agy-box"]["environment"]["GH_AGYBOX_MAX_JOBS"] == "1"
