"""Đổi tài khoản Google của Antigravity CLI (v0.1.30 — Boss: "chức năng đổi tài khoản google không hoạt động").

CLI giả nhiều tài khoản: tests/fixtures/fake_agy_multi.py. Lỗi gốc tái hiện ở đây:
1. "Thêm tài khoản" khi ĐANG đăng nhập: CLI thật thấy tệp phiên nên vào thẳng chat, không bao giờ in link; CLI làm
   mới token (ghi lại tệp) → Console tưởng đã đăng nhập xong và lưu lại CHÍNH tài khoản cũ → không thêm được ai.
2. Không có cách hỏi trạng thái đăng nhập ngoài WebSocket → mất WS là UI kẹt "Đang mở phiên…".
"""

import asyncio
import sys
from pathlib import Path
from typing import Any

import pytest

from gh.providers import cli as climod
from gh.providers.clients import AgyClient, Message
from tests.conftest import OWNER, verify_pin

FAKE = Path(__file__).parent / "fixtures" / "fake_agy_multi.py"


@pytest.fixture
def cli_home(tmp_path, monkeypatch, app):  # type: ignore[no-untyped-def]
    home = tmp_path / ".gemini" / "antigravity-cli"
    monkeypatch.setenv("GH_CLI_HOME", str(home))
    from gh.config import get_settings

    get_settings.cache_clear()
    app.state.cli_logins.argv = [sys.executable, str(FAKE)]
    yield home
    get_settings.cache_clear()


async def _wait(api: Any, login_id: str, until: tuple[str, ...]) -> dict[str, Any]:
    st: dict[str, Any] = {}
    for _ in range(100):
        r = await api.get(f"/cli/login/{login_id}")
        assert r.status_code == 200, r.text
        st = r.json()
        if st["status"] in until:
            break
        await asyncio.sleep(0.1)
    return st


async def _login(api: Any, name: str) -> dict[str, Any]:
    await verify_pin(api)  # v0.1.45 (F-20): thêm tài khoản CLI cần PIN
    r = await api.send("POST", "/cli/login")
    assert r.status_code == 202, r.text
    login_id = r.json()["login_id"]
    st = await _wait(api, login_id, ("waiting_code", "failed", "done"))
    assert st["status"] == "waiting_code", st
    assert st["url"] and "accounts.google.com" in st["url"]
    r = await api.send("POST", f"/cli/login/{login_id}/code", {"code": f"4/{name}"})
    assert r.status_code == 202, r.text
    st = await _wait(api, login_id, ("done", "failed"))
    assert st["status"] == "done", st
    return st


async def _end_pin() -> None:
    """v0.1.45: thêm tài khoản (POST /cli/login) mở phiên PIN — cho phiên PIN hết hạn để kiểm lại PIN của bước đổi."""
    from sqlalchemy import text

    from gh.db import admin_sessionmaker

    async with admin_sessionmaker()() as s:
        await s.execute(text("UPDATE core.sessions SET pin_verified_until = NULL"))
        await s.commit()


async def _whoami(home: Path) -> str:
    """Một lượt gọi model như worker (AgyClient, prompt qua stdin), với CLI giả trả về email của tệp phiên đang dùng."""
    c = AgyClient(sys.executable, str(home))
    orig = c._run

    async def run(*args: str, stdin: bytes | None = None) -> tuple[int, bytes, bytes]:
        # v0.1.38 (F-22): AgyClient gửi prompt qua stdin — chuyển tiếp stdin cho CLI giả.
        return await orig(str(FAKE), *args, stdin=stdin)

    c._run = run  # type: ignore[method-assign]
    out = await c.generate("gemini-2.5-pro", [Message("user", "hi")], json_mode=False, temperature=0)
    return out.text


async def test_add_second_account_then_switch_back_and_forth(owner_api, cli_home) -> None:  # type: ignore[no-untyped-def]
    api = owner_api
    await _login(api, "an")
    st = await _login(api, "binh")          # lỗi cũ: không bao giờ có link / "xong" với chính an@
    assert st["profile"]["email"] == "binh@example.vn"
    profs = (await api.get("/cli/profiles")).json()
    assert {p["email"]: p["active"] for p in profs} == {"an@example.vn": False, "binh@example.vn": True}
    assert await _whoami(cli_home) == "whoami:binh@example.vn"
    assert not list(cli_home.glob("*.before-login"))

    an = next(p for p in profs if p["email"] == "an@example.vn")
    await _end_pin()   # phiên PIN của lượt thêm tài khoản đã hết → đổi tài khoản phải hỏi lại PIN
    r = await api.send("POST", f"/cli/profiles/{an['id']}/activate")
    assert r.status_code == 423 and r.json()["code"] == "PIN_REQUIRED"
    await api.send("POST", "/auth/pin/verify", {"pin": OWNER["pin"]})
    r = await api.send("POST", f"/cli/profiles/{an['id']}/activate")
    assert r.status_code == 200, r.text
    assert r.json()["email"] == "an@example.vn" and r.json()["active"] is True
    # Lượt gọi model kế tiếp (worker đọc cùng thư mục cấu hình CLI) dùng đúng tài khoản vừa chọn.
    assert await _whoami(cli_home) == "whoami:an@example.vn"
    provs = (await api.get("/providers")).json()
    cli_p = next(p for p in provs if p["kind"] == "antigravity_cli")
    assert cli_p["auth_state"] == "ok"

    binh = next(p for p in profs if p["email"] == "binh@example.vn")
    r = await api.send("POST", f"/cli/profiles/{binh['id']}/activate")
    assert r.status_code == 200 and r.json()["email"] == "binh@example.vn"
    assert await _whoami(cli_home) == "whoami:binh@example.vn"


async def test_cancelled_add_account_keeps_current_account(owner_api, app, cli_home) -> None:  # type: ignore[no-untyped-def]
    api = owner_api
    await _login(api, "an")
    await verify_pin(api)  # v0.1.45 (F-20): thêm tài khoản CLI cần PIN
    r = await api.send("POST", "/cli/login")
    login_id = r.json()["login_id"]
    st = await _wait(api, login_id, ("waiting_code", "failed", "done"))
    assert st["status"] == "waiting_code", st
    # Đang đăng nhập tài khoản mới: không cho đổi tài khoản giữa chừng (tệp phiên đang để trống cho CLI).
    await api.send("POST", "/auth/pin/verify", {"pin": OWNER["pin"]})
    an = (await api.get("/cli/profiles")).json()[0]
    r = await api.send("POST", f"/cli/profiles/{an['id']}/activate")
    assert r.status_code == 409 and r.json()["code"] == "CLI_LOGIN_IN_PROGRESS", r.text
    assert (await api.send("POST", f"/cli/login/{login_id}/cancel")).status_code == 204
    task = app.state.cli_logins.sessions[login_id].task
    with pytest.raises(asyncio.CancelledError):
        await task
    assert (await api.get(f"/cli/login/{login_id}")).json()["status"] == "failed"
    assert await _whoami(cli_home) == "whoami:an@example.vn"
    assert not list(cli_home.glob("*.before-login"))


async def test_login_status_unknown_id_is_404(owner_api, cli_home) -> None:  # type: ignore[no-untyped-def]
    assert (await owner_api.get("/cli/login/khong-co")).status_code == 404


async def test_activate_profile_without_saved_session_is_refused(owner_api, cli_home) -> None:  # type: ignore[no-untyped-def]
    await _login(owner_api, "an")
    from sqlalchemy import text

    from gh.db import sessionmaker

    async with sessionmaker()() as db:
        row = (await db.execute(text("SELECT org_id, provider_id FROM agent.cli_profiles LIMIT 1"))).one()
        new = (await db.execute(text("""INSERT INTO agent.cli_profiles (org_id, provider_id, email)
                                        VALUES (:o, :p, 'trong@example.vn') RETURNING id"""),
                                {"o": row.org_id, "p": row.provider_id})).scalar_one()
        await db.commit()
    await owner_api.send("POST", "/auth/pin/verify", {"pin": OWNER["pin"]})
    r = await owner_api.send("POST", f"/cli/profiles/{new}/activate")
    assert r.status_code == 409 and r.json()["code"] == "CLI_PROFILE_NO_SESSION", r.text
    assert await _whoami(cli_home) == "whoami:an@example.vn"
    assert [p["email"] for p in (await owner_api.get("/cli/profiles")).json() if p["active"]] == ["an@example.vn"]


async def test_old_bug_without_parking_cli_never_asks_for_the_new_account(owner_api, cli_home, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Tái hiện lỗi v0.1.29: không gửi tạm tệp phiên thì CLI (đã đăng nhập) không in link; lần ghi lại tệp khi làm
    mới token bị hiểu là "đăng nhập xong" → Console báo xong với CHÍNH tài khoản cũ, tài khoản mới không bao giờ có."""
    await _login(owner_api, "an")
    monkeypatch.setattr(climod, "park_token", lambda *a: None)
    await verify_pin(owner_api)  # v0.1.45 (F-20): thêm tài khoản CLI cần PIN
    r = await owner_api.send("POST", "/cli/login")
    st = await _wait(owner_api, r.json()["login_id"], ("waiting_code", "done", "failed"))
    assert st["status"] == "done" and st["url"] is None
    assert st["profile"]["email"] == "an@example.vn"
    assert [p["email"] for p in (await owner_api.get("/cli/profiles")).json()] == ["an@example.vn"]


def test_unpark_restores_token_left_by_interrupted_login(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """api chết giữa lúc đăng nhập: tệp phiên cũ đang "gửi tạm" → khởi động lại thì trả về chỗ cũ."""
    monkeypatch.setenv("GH_CLI_HOME", str(tmp_path))
    from gh.config import get_settings

    get_settings.cache_clear()
    climod.backup_path().write_bytes(b'{"access_token":"x"}')
    assert climod.unpark_token() is True
    assert climod.token_path().read_bytes() == b'{"access_token":"x"}'
    assert not climod.backup_path().exists()
    get_settings.cache_clear()


async def test_worker_restart_does_not_touch_a_parked_token(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("GH_CLI_HOME", str(tmp_path))
    from gh.config import get_settings

    get_settings.cache_clear()
    climod.backup_path().write_bytes(b'{"access_token":"x"}')
    await climod.restore_active(None, owns_logins=False)  # type: ignore[arg-type]
    assert not climod.token_path().exists() and climod.backup_path().exists()
    get_settings.cache_clear()


async def test_api_restart_prefers_parked_token_over_half_finished_login(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """api chết SAU khi CLI đã ghi tệp của lượt đăng nhập dở: bản gửi tạm (tài khoản đang hoạt động) thắng, không
    nằm lại để lượt đăng nhập sau ghi đè mất."""
    monkeypatch.setenv("GH_CLI_HOME", str(tmp_path))
    from gh.config import get_settings

    get_settings.cache_clear()
    climod.backup_path().write_bytes(b'{"access_token":"old"}')
    climod.token_path().write_bytes(b'{"access_token":"half"}')
    await climod.restore_active(None, owns_logins=True)  # type: ignore[arg-type]
    assert climod.token_path().read_bytes() == b'{"access_token":"old"}'
    assert not climod.backup_path().exists()
    get_settings.cache_clear()
