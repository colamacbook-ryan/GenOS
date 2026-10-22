"""v0.1.45 (F-56) — mã đăng nhập CLI chỉ nhận đúng lớp ký tự (`gh.providers.cli.CLI_CODE_RE`).

Hai lớp: `CodeIn` (422 với câu tiếng Việt) và `CliLogins.submit()` (ValueError trước khi vào hàng đợi) — không đường
nào ghi khoảng trắng giữa mã / ký tự điều khiển (ESC, CR/LF) vào PTY của CLI. Đối chiếu dạng mã thật v0.1.39: Claude
'c/boss-Ab9_x', Google '4/0AbCdEf-12_xyZ', Claude Code thật '<mã>#<state>'."""

import asyncio
import os
import sys
from pathlib import Path
from typing import Any

import pytest

from gh.providers import cli as climod
from tests.conftest import Api, verify_pin

FAKE = Path(__file__).parent / "fixtures" / "fake_agy.py"

GOOD = ["c/boss-Ab9_x", "4/0AbCdEf-12_xyZ", "AbC-12_xYz9#st4te-Q1_w", "  4/0Ab-x_y\n", "abcd", "a" * 500]
BAD = ["abc", "ab cd", "abc\x1b[2J", "abc\r\ny", "mã;rm", "a" * 501, "abc\tdef", "abc$(id)", "abc;ls", "  ab  "]


@pytest.mark.parametrize("code", GOOD)
def test_regex_accepts_real_code_shapes(code: str) -> None:
    assert climod.CLI_CODE_RE.fullmatch(code.strip())


@pytest.mark.parametrize("code", BAD)
def test_regex_rejects_spaces_controls_and_odd_chars(code: str) -> None:
    assert not climod.CLI_CODE_RE.fullmatch(code.strip())


async def test_submit_rejects_bad_code_before_queue() -> None:
    """Phòng thủ lớp 2: gọi thẳng submit() (bỏ qua API) với mã chứa ký tự điều khiển → ValueError, hàng đợi trống."""
    s = climod.LoginSession.__new__(climod.LoginSession)
    s.code = asyncio.Queue()
    with pytest.raises(ValueError):
        await climod.CliLogins.submit(None, s, "abc\x1b[2J")  # type: ignore[arg-type]
    assert s.code.qsize() == 0
    await climod.CliLogins.submit(None, s, " 4/0Ab-x_y\n")  # type: ignore[arg-type]
    assert s.code.get_nowait() == "4/0Ab-x_y"


async def _start(api: Api, app: Any, tmp_path: Path, monkeypatch: Any) -> tuple[str, Any]:
    monkeypatch.setenv("GH_CLI_HOME", str(tmp_path / ".gemini" / "antigravity-cli"))
    from gh.config import get_settings

    get_settings.cache_clear()
    logins = app.state.cli_logins
    logins.argv = [sys.executable, str(FAKE)]
    await verify_pin(api)
    r = await api.send("POST", "/cli/login")
    assert r.status_code == 202, r.text
    s = next(iter(logins.sessions.values()))
    for _ in range(100):
        if s.status != "starting":
            break
        await asyncio.sleep(0.1)
    assert s.status == "waiting_code", s.message
    return r.json()["login_id"], s


@pytest.mark.parametrize("code", ["ab cd 12", "abcd\x1b[2J", "abcd\r\nrm -rf", "4/0Ab;id", "ab\x03cdef"])
async def test_api_rejects_bad_code_and_writes_nothing_to_pty(owner_api: Api, app, tmp_path, monkeypatch,  # type: ignore[no-untyped-def]
                                                             code: str) -> None:
    writes: list[bytes] = []
    real_write = os.write

    def spy(fd: int, data: bytes) -> int:  # type: ignore[override]
        writes.append(bytes(data))
        return real_write(fd, data)

    login_id, s = await _start(owner_api, app, tmp_path, monkeypatch)
    monkeypatch.setattr(os, "write", spy)
    r = await owner_api.send("POST", f"/cli/login/{login_id}/code", {"code": code})
    assert r.status_code == 422, r.text
    assert "Mã đăng nhập chỉ gồm chữ, số" in r.text
    await asyncio.sleep(0.3)
    assert s.code.qsize() == 0
    assert s.status == "waiting_code"
    assert not any(code.encode() in w or b"\x1b[2J" in w or b"\x03" in w for w in writes)
    assert (await owner_api.send("POST", f"/cli/login/{login_id}/cancel")).status_code == 204
    from gh.config import get_settings

    get_settings.cache_clear()


async def test_api_accepts_code_with_trailing_newline(owner_api: Api, app, tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Dán kèm xuống dòng → bỏ khoảng trắng hai đầu, đăng nhập vẫn xong (CLI giả nhận mã)."""
    login_id, s = await _start(owner_api, app, tmp_path, monkeypatch)
    r = await owner_api.send("POST", f"/cli/login/{login_id}/code", {"code": "  4/0AbCdEf-12_xyZ\n"})
    assert r.status_code == 202, r.text
    for _ in range(100):
        if s.status in ("done", "failed"):
            break
        await asyncio.sleep(0.1)
    assert s.status == "done", s.message
    from gh.config import get_settings

    get_settings.cache_clear()
