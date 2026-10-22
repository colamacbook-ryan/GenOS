"""Adapter Jev (gh.gen.jev) + JevDecider — chạy trên một máy chủ HTTP giả thật (localhost), không gọi mạng ngoài.

Schema `/v1/systemone` là GIẢ ĐỊNH (TODO trong gh/gen/jev.py) — các test này khoá lại giả định đó để khi có tài liệu
chính thức, đổi một chỗ và thấy ngay chỗ nào vỡ.
"""

import json
import threading
import time
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest

from gh.gen import decider, jev, registry


class FakeJev:
    def __init__(self) -> None:
        self.requests: list[tuple[str, dict[str, Any], dict[str, str]]] = []
        self.reply: Any = {}
        self.status = 200
        self.delay = 0.0


@pytest.fixture
def fake() -> Iterator[tuple[FakeJev, str]]:
    state = FakeJev()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            body = json.loads(self.rfile.read(int(self.headers["content-length"])))
            state.requests.append((self.path, body, dict(self.headers)))
            if state.delay:
                time.sleep(state.delay)
            out = json.dumps(state.reply).encode()
            self.send_response(state.status)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)

        def log_message(self, *a: Any) -> None:
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield state, f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


def chat(content: str) -> dict[str, Any]:
    return {"choices": [{"message": {"role": "assistant", "content": content}}]}


def test_mode_detection() -> None:
    assert jev.mode_for("https://openrouter.ai/api/v1") == "chat"
    assert jev.mode_for("https://api.typesafe.ai") == "systemone"
    assert jev.mode_for("http://127.0.0.1:9/v1/systemone") == "systemone"
    assert jev._systemone_url("https://api.typesafe.ai") == "https://api.typesafe.ai/v1/systemone"
    assert jev._systemone_url("https://x/v1") == "https://x/v1/systemone"


async def test_chat_mode_choice(fake: tuple[FakeJev, str]) -> None:
    state, base = fake
    state.reply = chat('{"choice": "dẫn đường", "confidence": 0.91}')
    c = jev.JevClient(f"{base}/api/v1", "k-123", timeout=3)
    got = await c.choose("Chỉ tôi thêm khoá", ["dữ liệu", "dẫn đường"])
    assert got.label == "dẫn đường" and got.confidence == pytest.approx(0.91)
    path, body, headers = state.requests[0]
    assert path == "/api/v1/chat/completions" and body["model"] == "typesafe/jev-1.13"
    assert headers["authorization"] == "Bearer k-123" and body["response_format"] == {"type": "json_object"}
    # Trả lời trần (không JSON) cũng nhận nếu đúng nguyên văn một lựa chọn.
    state.reply = chat("dữ liệu")
    assert (await c.choose("?", ["dữ liệu", "dẫn đường"])).label == "dữ liệu"


async def test_systemone_mode_assumed_schema(fake: tuple[FakeJev, str]) -> None:
    state, base = fake
    c = jev.JevClient(f"{base}/v1/systemone", "k", "jev-1.13", timeout=3)
    for reply in ({"choice": "b", "confidence": 0.8}, {"output": {"label": "b", "score": 0.8}},
                  {"result": {"choice": "B", "probability": 2}}):
        state.reply = reply
        got = await c.choose("q", ["a", "b"], "ctx")
        assert got.label == "b" and got.confidence in (0.8, 1.0)
    path, body, _ = state.requests[0]
    assert path == "/v1/systemone"
    assert body == {"model": "jev-1.13", "task": "choice", "input": "q", "context": "ctx", "options": ["a", "b"]}


async def test_errors_raise_jev_error(fake: tuple[FakeJev, str]) -> None:
    state, base = fake
    c = jev.JevClient(f"{base}/v1/systemone", "k", timeout=3)
    state.reply = {"choice": "không có trong danh sách"}
    with pytest.raises(jev.JevError, match="ngoài danh sách"):
        await c.choose("q", ["a", "b"])
    state.reply, state.status = {"error": "x"}, 500
    with pytest.raises(jev.JevError, match="HTTP 500"):
        await c.choose("q", ["a", "b"])
    with pytest.raises(jev.JevError, match="mạng"):
        await jev.JevClient("http://127.0.0.1:9", "k", timeout=1).choose("q", ["a"])


async def test_decider_timeout_and_low_confidence_fall_back(fake: tuple[FakeJev, str]) -> None:
    state, base = fake
    d = decider.JevDecider(jev.JevClient(f"{base}/v1/systemone", "k", timeout=5), timeout=0.3)
    state.reply = {"choice": decider.INTENTS["guide"], "confidence": 0.9}
    got = await d.intent("Chỉ tôi cách thêm khoá Gemini")
    assert got is not None and got.value == "guide" and got.source == "jev"
    state.reply = {"choice": decider.INTENTS["data"], "confidence": 0.2}
    assert await d.intent("?") is None and "thấp" in (d.last_error or "")
    state.reply, state.delay = {"choice": decider.INTENTS["data"], "confidence": 0.9}, 0.6
    t0 = time.monotonic()
    assert await d.intent("?") is None
    assert time.monotonic() - t0 < 0.55  # không chờ quá trần thời gian
    state.delay = 0
    targets = registry.targets_for("system")
    state.reply = {"choice": next(f"{t.label} — {t.description}" for t in targets if t.id == "system.brain.jev")}
    pick = await d.next_target("Thêm Jev ở đâu?", targets)
    assert pick is not None and pick.value == "system.brain.jev"
    assert await decider.LlmDecider().intent("x") is None
