"""v0.1.39 (F-77) — đăng nhập Claude Code trong app đi tới bước CUỐI và ghi lại DẠNG mã (không bao giờ giá trị mã).

CLI giả tests/fixtures/fake_claude.py với `FAKE_CLAUDE_CONFIRM=1`: sau khi nhận mã in "Login successful. Press Enter to
continue…" và chỉ ghi `.credentials.json` sau khi nhận Enter (như claude thật) — chứng minh luồng in-app tự bấm Enter
và hoàn tất. `FAKE_CLAUDE_FAIL=1`: CLI in lại mã trong câu lỗi rồi thoát sớm. CLI chạy với môi trường SẠCH (claude_env)
nên biến chế độ được đặt trong tệp chạy bọc."""

import sys
from pathlib import Path
from typing import Any

import orjson
from sqlalchemy import text

from gh.boss_checks import service as boss
from gh.db import admin_sessionmaker
from tests.conftest import Api, verify_pin
from tests.test_cli_models_v0131 import FIX, _wait, clis  # noqa: F401 — fixture dùng lại

CLAUDE = "claude_code_cli"
CODE = "c/boss-Ab9_x"


def _mode_wrapper(tmp: Path, name: str, env: str) -> str:
    path = tmp / "bin" / name
    path.parent.mkdir(exist_ok=True)
    path.write_text(f"#!/bin/sh\n{env} exec {sys.executable} {FIX / 'fake_claude.py'} \"$@\"\n")
    path.chmod(0o755)
    return str(path)


async def _db_text(sql: str) -> str:
    async with admin_sessionmaker()() as db:
        return orjson.dumps((await db.execute(text(sql))).all(), default=str).decode()


async def _start_and_submit(api: Api, code: str) -> dict[str, Any]:
    await verify_pin(api)  # v0.1.45 (F-20): thêm tài khoản CLI cần PIN
    r = await api.send("POST", f"/cli/login?kind={CLAUDE}")
    assert r.status_code == 202, r.text
    login_id = r.json()["login_id"]
    st = await _wait(api, login_id, ("waiting_code", "failed", "done"))
    assert st["status"] == "waiting_code", st
    r = await api.send("POST", f"/cli/login/{login_id}/code", {"code": code})
    assert r.status_code == 202, r.text
    return await _wait(api, login_id, ("done", "failed"))


def test_code_shape_units() -> None:
    assert boss.code_shape(CODE) == {"length": 12, "classes": ["digit", "lower", "symbol", "upper"],
                                     "symbols": "-/_", "has_space": False}
    google = "4/0AbCdEf-12_xyZ"
    assert boss.code_shape(google) == {"length": 16, "classes": ["digit", "lower", "symbol", "upper"],
                                       "symbols": "-/_", "has_space": False}
    assert boss.code_shape(" 4/0Ab x\n")["has_space"] is True
    assert boss.code_shape(" 4/0Ab x\n")["symbols"] == "/"            # khoảng trắng không tính là ký hiệu
    assert boss.code_shape("") == {"length": 0, "classes": [], "symbols": "", "has_space": False}


async def test_in_app_claude_login_reaches_final_step(owner_api: Api, clis: Any, app: Any, tmp_path: Path) -> None:  # noqa: F811
    api = owner_api
    app.state.cli_logins.claude_argv = [_mode_wrapper(tmp_path, "claude-confirm", "FAKE_CLAUDE_CONFIRM=1")]
    st = await _start_and_submit(api, CODE)
    assert st["status"] == "done", st
    assert "code_shape" not in st                                      # không có trong public()
    assert (clis["claude_home"] / ".credentials.json").exists()
    agy_root = clis["agy_home"].parent.parent                          # HOME của agy
    assert not list(agy_root.rglob(".credentials.json"))              # phiên Claude không nằm chung chỗ agy
    ov = (await api.get("/boss-checks")).json()
    login = ov["results"]["claude_login"]
    assert login["status"] == "pass", login
    assert login["detail"]["code_shape"] == {"length": 12, "classes": ["digit", "lower", "symbol", "upper"],
                                             "symbols": "-/_", "has_space": False}
    assert login["detail"]["credentials_file"] is True
    assert login["detail"]["account_masked"] == "b***@example.vn"
    for sql in ("SELECT * FROM ops.boss_checks", "SELECT * FROM ops.action_log"):
        assert CODE not in await _db_text(sql), sql
    assert CODE not in (await api.get("/audit?limit=200")).text
    async with admin_sessionmaker()() as db:
        logged = (await db.execute(text("SELECT detail FROM ops.action_log WHERE action = 'cli.logged_in'"))
                  ).scalar_one()
    assert logged["code_shape"] == boss.code_shape(CODE) and logged["kind"] == CLAUDE
    out = (await api.send("POST", "/boss-checks/claude_call/run", {})).json()
    assert out["status"] == "pass" and out["account"] == "boss-Ab9_x@example.vn", out
    assert out["detail"]["account_masked"] == "b***@example.vn"
    ov = (await api.get("/boss-checks")).json()
    assert next(r for r in ov["rows"] if r["key"] == "claude")["done"] is True
    assert "boss-Ab9_x@example.vn" not in await _db_text("SELECT message, detail::text FROM ops.boss_checks")


async def test_claude_cli_exits_early_records_fail_with_shape(owner_api: Api, clis: Any, app: Any,  # noqa: F811
                                                               tmp_path: Path) -> None:
    api = owner_api
    out = (await api.send("POST", "/boss-checks/claude_call/run", {})).json()
    assert out["status"] == "fail" and out["error_code"] == "CLAUDE_NOT_LOGGED_IN"
    app.state.cli_logins.claude_argv = [_mode_wrapper(tmp_path, "claude-fail", "FAKE_CLAUDE_FAIL=1")]
    st = await _start_and_submit(api, CODE)
    assert st["status"] == "failed", st
    assert CODE not in (st["message"] or "")                           # CLI in lại mã → đã che
    assert not (clis["claude_home"] / ".credentials.json").exists()
    res = (await api.get("/boss-checks")).json()["results"]["claude_login"]
    assert res["status"] == "fail" and res["error_code"] == "CLI_LOGIN_FAILED", res
    assert res["detail"] == {"code_shape": boss.code_shape(CODE)}
    assert res["message"].startswith("Đăng nhập chưa xong")
    for sql in ("SELECT * FROM ops.boss_checks", "SELECT * FROM ops.action_log"):
        assert CODE not in await _db_text(sql), sql
    failed = await _db_text("SELECT detail FROM ops.action_log WHERE action = 'cli.login_failed'")
    assert "***" in failed


async def test_claude_cli_missing_records_cli_missing(owner_api: Api, clis: Any, app: Any,  # noqa: F811
                                                       tmp_path: Path) -> None:
    app.state.cli_logins.claude_argv = [str(tmp_path / "khong-co-claude")]
    await verify_pin(owner_api)  # v0.1.45 (F-20): thêm tài khoản CLI cần PIN
    r = await owner_api.send("POST", f"/cli/login?kind={CLAUDE}")
    st = await _wait(owner_api, r.json()["login_id"], ("failed", "done"))
    assert st["status"] == "failed"
    res = (await owner_api.get("/boss-checks")).json()["results"]["claude_login"]
    assert res["status"] == "fail" and res["error_code"] == "CLI_MISSING" and res["detail"] == {}


async def test_existing_claude_session_counts_as_logged_in_after_call(owner_api: Api, clis: Any, app: Any,  # noqa: F811
                                                                      tmp_path: Path) -> None:
    """Phiên Claude có từ trước v0.1.39 (tự chuyển khi cập nhật): không có bản `claude_login` nào. Gọi thử ĐẠT → máy chủ
    ghi `claude_login` 'pass' (login_source=existing_session) để dòng 4 thành "Xong" mà không bắt đăng nhập lại."""
    api = owner_api
    app.state.cli_logins.claude_argv = [_mode_wrapper(tmp_path, "claude-confirm", "FAKE_CLAUDE_CONFIRM=1")]
    assert (await _start_and_submit(api, CODE))["status"] == "done"
    async with admin_sessionmaker()() as db:   # như máy cập nhật từ bản cũ: chưa từng có bản ghi claude_login
        await db.execute(text("DELETE FROM ops.boss_checks WHERE check_key = 'claude_login'"))
        await db.commit()
    ov = (await api.get("/boss-checks")).json()
    assert ov["results"]["claude_login"] is None
    assert next(r for r in ov["rows"] if r["key"] == "claude")["done"] is False
    out = (await api.send("POST", "/boss-checks/claude_call/run", {})).json()
    assert out["status"] == "pass", out
    ov = (await api.get("/boss-checks")).json()
    login = ov["results"]["claude_login"]
    assert login["status"] == "pass" and login["runs"] == 1, login
    assert login["detail"] == {"login_source": "existing_session", "account_masked": "b***@example.vn",
                               "credentials_file": True}
    assert next(r for r in ov["rows"] if r["key"] == "claude")["done"] is True
    # Đã đạt rồi thì gọi thử lần nữa không ghi thêm bản đăng nhập.
    assert (await api.send("POST", "/boss-checks/claude_call/run", {})).json()["status"] == "pass"
    assert (await api.get("/boss-checks")).json()["results"]["claude_login"]["runs"] == 1
    assert "boss-Ab9_x@example.vn" not in await _db_text("SELECT message, detail::text FROM ops.boss_checks")
