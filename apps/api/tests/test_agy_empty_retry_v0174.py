"""v0.1.74 (bản thật 19/10): agy đôi khi tự gọi công cụ, bị chặn (`denied_actions`) rồi trả `response` RỖNG dù thoát 0.
AgyClient hỏi lại MỘT lần khi rỗng, dặn «chỉ trả lời bằng chữ»; vẫn rỗng thì trả rỗng
(router xử như lỗi nguồn)."""

from typing import Any

import orjson
import pytest

from gh.providers import clients
from gh.providers.clients import AgyClient, Message


def _client(monkeypatch: pytest.MonkeyPatch, outs: list[dict[str, Any]], seen: list[bytes]) -> AgyClient:
    c = AgyClient(binary="agy-gia", cli_home="/tmp/khong-dung")
    monkeypatch.setattr(c, "_load_token", lambda: b"{}")

    async def fake_run(*args: str, stdin: bytes | None = None) -> tuple[int, bytes, bytes]:
        seen.append(stdin or b"")
        return 0, orjson.dumps(outs.pop(0)), b""

    monkeypatch.setattr(c, "_run", fake_run)
    return c


async def test_empty_response_is_retried_once_with_text_only_reminder(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[bytes] = []
    c = _client(monkeypatch, [{"status": "SUCCESS", "response": "", "denied_actions": [{"tool": "run_command"}]},
                              {"status": "SUCCESS", "response": "Dạ nhóm A sôi nổi nhất ạ", "usage": {}}], seen)
    r = await c.generate("gemini-3.8-flash", [Message("system", "s"), Message("user", "hỏi")], json_mode=False,
                         temperature=0.2, effort="medium")
    assert r.text == "Dạ nhóm A sôi nổi nhất ạ" and len(seen) == 2
    assert clients.AGY_TEXT_ONLY.encode() not in seen[0]                   # lần đầu: lời nhắc nguyên như cũ
    assert clients.AGY_TEXT_ONLY_RETRY.encode() in seen[1] and clients.AGY_TEXT_ONLY.encode() in seen[1]


async def test_still_empty_after_one_retry_returns_empty_no_third_call(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[bytes] = []
    c = _client(monkeypatch, [{"status": "SUCCESS", "response": ""}, {"status": "SUCCESS", "response": ""}], seen)
    r = await c.generate("gemini-3.8-flash", [Message("user", "hỏi")], json_mode=True, temperature=0.2,
                         effort="medium")
    assert r.text == "" and len(seen) == 2


async def test_non_empty_first_answer_is_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[bytes] = []
    c = _client(monkeypatch, [{"status": "SUCCESS", "response": "ok"}], seen)
    r = await c.generate("gemini-3.8-flash", [Message("user", "hỏi")], json_mode=False, temperature=0.2,
                         effort="medium")
    assert r.text == "ok" and len(seen) == 1
