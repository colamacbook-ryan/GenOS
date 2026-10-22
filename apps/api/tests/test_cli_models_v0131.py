"""v0.1.31 — Boss 01/10: "Không thấy model và nhóm model nào để chọn" (Antigravity CLI chỉ hiện 1 model), thẻ tài khoản
"Hết hạn" mà dòng nguồn "Gọi thử OK", và chưa có Claude Code CLI (gói Claude Pro/Max).

CLI giả: tests/fixtures/fake_agy_multi.py (thêm `agy models`, từ chối model lạ) và tests/fixtures/fake_claude.py.
"""

import asyncio
import sys
import time
import uuid
from pathlib import Path
from typing import Any

import orjson
import pytest
from sqlalchemy import text

from gh import crypto
from gh.providers import catalog
from gh.providers import cli as climod
from gh.providers.clients import AgyClient, ClaudeCodeClient, Message
from tests.conftest import OWNER, verify_pin

FIX = Path(__file__).parent / "fixtures"


def _wrapper(tmp: Path, name: str, script: Path) -> str:
    """Tệp chạy giả (như /usr/local/bin/agy, /usr/local/bin/claude) bọc script Python của fixture."""
    (tmp / "bin").mkdir(exist_ok=True)
    path = tmp / "bin" / name
    # Gói sửa review v0.1.63 (P1-1): mỗi lượt agy có HOME tạm (xoá sau lượt) ⇒ CLI giả ghi nhật ký lượt gọi vào chỗ
    # cố định của test (<tmp>/agy/agy-calls.log, như trước khi HOME còn là thư mục cha của GH_CLI_HOME).
    calls = tmp / "agy" / "agy-calls.log"
    path.write_text(f"#!/bin/sh\nFAKE_AGY_CALLS={calls} exec {sys.executable} {script} \"$@\"\n")
    path.chmod(0o755)
    return str(path)


@pytest.fixture
def clis(tmp_path, monkeypatch, app):  # type: ignore[no-untyped-def]
    agy_home = tmp_path / "agy" / ".gemini" / "antigravity-cli"
    claude_home = tmp_path / "claude" / ".claude"
    agy = _wrapper(tmp_path, "agy", FIX / "fake_agy_multi.py")
    claude = _wrapper(tmp_path, "claude", FIX / "fake_claude.py")
    monkeypatch.setenv("GH_CLI_HOME", str(agy_home))
    monkeypatch.setenv("GH_CLAUDE_HOME", str(claude_home))
    monkeypatch.setenv("GH_CLI_BINARY", agy)
    monkeypatch.setenv("GH_CLAUDE_BINARY", claude)
    from gh.config import get_settings

    get_settings.cache_clear()
    app.state.cli_logins.argv = [agy]
    app.state.cli_logins.claude_argv = [claude]
    router = app.state.model_router
    old = router.cli_factory, router.claude_factory
    router.cli_factory = lambda: AgyClient(agy, str(agy_home), timeout=30)
    router.claude_factory = lambda: ClaudeCodeClient(claude, str(claude_home), timeout=30)
    yield {"agy_home": agy_home, "claude_home": claude_home, "agy": agy, "claude": claude}
    router.cli_factory, router.claude_factory = old
    get_settings.cache_clear()


async def _wait(api: Any, login_id: str, until: tuple[str, ...]) -> dict[str, Any]:
    st: dict[str, Any] = {}
    for _ in range(150):
        st = (await api.get(f"/cli/login/{login_id}")).json()
        if st["status"] in until:
            break
        await asyncio.sleep(0.1)
    return st


async def _login(api: Any, kind: str, code: str) -> dict[str, Any]:
    await verify_pin(api)  # v0.1.45 (F-20): thêm tài khoản CLI cần PIN
    r = await api.send("POST", f"/cli/login?kind={kind}")
    assert r.status_code == 202, r.text
    login_id = r.json()["login_id"]
    st = await _wait(api, login_id, ("waiting_code", "failed", "done"))
    assert st["status"] == "waiting_code", st
    r = await api.send("POST", f"/cli/login/{login_id}/code", {"code": code})
    assert r.status_code == 202, r.text
    st = await _wait(api, login_id, ("done", "failed"))
    assert st["status"] == "done", st
    return st


async def _provider(api: Any, kind: str) -> dict[str, Any]:
    return next(p for p in (await api.get("/providers")).json() if p["kind"] == kind)


# ─── danh mục / bộ đọc `agy models` ────────────────────────────────────────

def test_parse_agy_models_reads_slugs_and_display_names_anywhere_in_line() -> None:
    out = ("Fetching available models...\nAvailable models:\n"
           "  Gemini 3.8 Flash (High)   gemini-3.8-flash-high (current)\n"
           "* gemini-3.1-pro-low  Gemini 3.1 Pro (Low)\n"
           "  \x1b[1mClaude Sonnet 4.6 (Thinking)\x1b[0m  claude-sonnet-4-6\n"
           "  Gemini 3.8 Flash (Low)\n")
    got = catalog.parse_agy_models(out)
    # v0.1.32: biến thể gộp về model gốc; "High"/"Low" là mức suy nghĩ, không phải tên model.
    assert [m["id"] for m in got] == ["gemini-3.8-flash", "gemini-3.1-pro", "claude-sonnet-4-6"]
    assert got[0]["current"] and got[0]["label"] == "Gemini 3.8 Flash"
    assert got[0]["efforts"] == ["low", "high"] and got[0]["current_effort"] == "high"
    # Lỗi cũ (v0.1.30): chỉ nhận TỪ ĐẦU dòng có dấu "-" → dòng "Tên hiển thị  mã" bị bỏ, còn đúng 1 model.
    old = [ln.strip().split()[0] for ln in out.splitlines() if ln.strip() and "-" in ln.strip().split()[0]]
    assert len(old) <= 1


def test_build_groups_with_hints_and_fallback() -> None:
    built = catalog.build("antigravity_cli", ["gemini-3.8-flash-low", "claude-opus-4-6", "gemini-embedding-001"])
    assert built["models_source"] == "cli"
    assert [g["label"] for g in built["model_groups"]] == ["Gemini", "Claude (qua Antigravity)"]
    flash = built["model_groups"][0]["models"][0]
    assert flash["id"] == "gemini-3.8-flash" and flash["efforts"] == ["low"]
    # v0.1.66 (F025): MỘT bảng tầng — flash = Cân bằng (như hồ sơ chuẩn), "nhanh, rẻ" chỉ cho flash-lite / haiku…
    assert flash["tier"] == "balanced" and flash["hint"] == "cân bằng"
    assert built["model_groups"][1]["models"][0]["tier"] == "strong"
    fb = catalog.build("antigravity_cli", [])
    assert fb["models_source"] == "catalog" and "gemini-3.8-flash" in fb["models"]
    assert not [m for m in fb["models"] if m.endswith(("-low", "-medium", "-high"))]
    cc = catalog.build("claude_code_cli", None)
    assert cc["models"] == ["haiku", "sonnet", "opus", "fable"]
    assert [g["label"] for g in cc["model_groups"]] == ["Claude"]


def test_profile_state_single_truth() -> None:
    from datetime import UTC, datetime, timedelta

    past = datetime.now(UTC) - timedelta(hours=2)
    # Boss 01/10: token truy cập ngắn hạn quá hạn nhưng CLI tự làm mới → không còn "Hết hạn".
    assert climod.profile_state(past, refreshable=True) == "ok"
    assert climod.profile_state(past, refreshable=False) == "expired"
    # Lượt gọi thật báo lỗi xác thực → "Hết hạn" dù còn refresh token.
    assert climod.profile_state(past, refreshable=True, auth_state="expired", active=True) == "expired"


# ─── Antigravity CLI ────────────────────────────────────────────────────────

async def test_agy_models_grouped_probe_and_validated_choice(owner_api, clis) -> None:  # type: ignore[no-untyped-def]
    api = owner_api
    await _login(api, "antigravity_cli", "4/an")
    p = await _provider(api, "antigravity_cli")
    r = await api.send("POST", f"/providers/{p['id']}/test")
    t = r.json()
    assert t["ok"] is True, t
    assert t["models_source"] == "cli"
    assert t["models"] == ["gemini-3.8-flash", "gemini-3.1-pro", "claude-sonnet-4-6-thinking"]
    assert [g["label"] for g in t["model_groups"]] == ["Gemini", "Claude (qua Antigravity)"]
    # model "(current)" của CLI, gọi bằng --model gốc + --effort (v0.1.32)
    assert (t["probe_model"], t["probe_effort"]) == ("gemini-3.8-flash", "high")
    # Chọn model nhóm Claude: gọi thử thật rồi mới lưu, thành model mặc định của nguồn.
    r = await api.send("POST", f"/providers/{p['id']}/models",
                       {"model_name": "claude-sonnet-4-6-thinking", "make_default": True})
    assert r.status_code == 201, r.text
    # Model CLI không nhận (tên suy từ dòng chỉ có tên hiển thị) → 422, câu lỗi dễ hiểu, KHÔNG lưu.
    r = await api.send("POST", f"/providers/{p['id']}/models", {"model_name": "claude-opus-4.6-thinking"})
    assert r.status_code == 422, r.text
    assert "không nhận model" in r.text
    # v0.1.58: "Kiểm tra kết nối" xanh khi nguồn chưa có model ⇒ tự lưu model vừa gọi thử được (không đặt mặc định);
    # model Claude không nhận (422) thì KHÔNG được lưu thêm.
    names = [m["model_name"] for m in (await _provider(api, "antigravity_cli"))["models"]]
    # v0.1.66 (F132): lưu sẵn mọi model agy liệt kê thật.
    assert names == ["gemini-3.8-flash", "gemini-3.1-pro", "claude-sonnet-4-6-thinking"]
    # Sửa hạn mức của model ĐÃ có thì không gọi thử lại.
    r = await api.send("POST", f"/providers/{p['id']}/models",
                       {"model_name": "claude-sonnet-4-6-thinking", "daily_quota": 100})
    assert r.status_code == 201, r.text


async def test_agy_expired_session_never_reports_ok(owner_api, clis) -> None:  # type: ignore[no-untyped-def]
    api = owner_api
    await _login(api, "antigravity_cli", "4/an")
    p = await _provider(api, "antigravity_cli")
    (clis["agy_home"] / "antigravity-oauth-token").unlink()     # phiên mất / bị thu hồi
    t = (await api.send("POST", f"/providers/{p['id']}/test")).json()
    assert t["ok"] is False and "Đăng nhập lại" in t["error"], t
    assert t["models_source"] == "catalog" and t["model_groups"]   # vẫn thấy danh sách sẽ chọn được
    assert (await _provider(api, "antigravity_cli"))["auth_state"] == "expired"
    prof = (await api.get("/cli/profiles")).json()[0]
    assert prof["state"] == "expired"


async def test_agy_refreshable_token_past_expiry_shows_active(owner_api, clis) -> None:  # type: ignore[no-untyped-def]
    """Thẻ tài khoản và dòng nguồn cùng một sự thật: access token quá hạn + refresh token → "ok"."""
    api = owner_api
    await _login(api, "antigravity_cli", "4/an")
    from gh.db import sessionmaker

    raw = orjson.dumps({"access_token": "x", "refresh_token": "r", "expiry": int(time.time()) - 7200})
    async with sessionmaker()() as db:
        await db.execute(text("UPDATE agent.cli_profiles SET token_enc = :t, expires_at = now() - interval '2 hours'"),
                         {"t": crypto.encrypt(raw, climod.CLI_AAD)})
        await db.commit()
    prof = (await api.get("/cli/profiles")).json()[0]
    assert prof["state"] == "ok" and prof["refreshable"] is True


# ─── Claude Code CLI ────────────────────────────────────────────────────────

async def test_claude_code_login_models_switch_and_invoke(owner_api, app, clis) -> None:  # type: ignore[no-untyped-def]
    api = owner_api
    assert (await api.get("/cli/profiles?kind=claude_code_cli")).json() == []
    # Chưa đăng nhập: chưa có nguồn Claude Code nào (tắt tới khi Owner đăng nhập).
    assert not [p for p in (await api.get("/providers")).json() if p["kind"] == "claude_code_cli"]
    await verify_pin(api)  # v0.1.45 (F-20): thêm tài khoản CLI cần PIN
    r = await api.send("POST", "/cli/login?kind=claude_code_cli")
    login_id = r.json()["login_id"]
    st = await _wait(api, login_id, ("waiting_code", "failed"))
    assert st["status"] == "waiting_code" and st["url"].startswith("https://claude.com/cai/oauth/authorize"), st
    assert st["kind"] == "claude_code_cli"
    await api.send("POST", f"/cli/login/{login_id}/code", {"code": "c/boss"})
    st = await _wait(api, login_id, ("done", "failed"))
    assert st["status"] == "done", st
    assert st["profile"]["email"] == "boss@example.vn" and st["profile"]["plan_label"] == "Claude Max"
    assert st["profile"]["state"] == "ok"          # access token quá hạn nhưng có refresh token
    # Hồ sơ Claude tách khỏi hồ sơ Google (Antigravity).
    assert (await api.get("/cli/profiles")).json() == []

    p = await _provider(api, "claude_code_cli")
    assert p["name"] == "Claude Code CLI" and p["models"] == []
    t = (await api.send("POST", f"/providers/{p['id']}/test")).json()
    assert t["ok"] is True, t
    assert t["models_source"] == "catalog" and t["models"] == ["haiku", "sonnet", "opus", "fable"]
    assert t["probe_model"] == "haiku"
    r = await api.send("POST", f"/providers/{p['id']}/models", {"model_name": "claude-sonnet-9", "make_default": True})
    assert r.status_code == 422 and "không nhận model" in r.text
    r = await api.send("POST", f"/providers/{p['id']}/models", {"model_name": "opus", "make_default": True})
    assert r.status_code == 201, r.text

    # Gọi qua bộ định tuyến như worker: đúng model mặc định, đúng tài khoản; công cụ của Claude Code bị tắt.
    from gh.db import sessionmaker

    async with sessionmaker()() as db:
        org = (await db.execute(text("SELECT org_id FROM agent.providers WHERE id = :i"),
                                {"i": uuid.UUID(p["id"])})).scalar_one()
        await db.execute(text("UPDATE agent.providers SET is_enabled = (kind = 'claude_code_cli') WHERE org_id = :o"),
                         {"o": org})
        await db.commit()
    out = await app.state.model_router.generate(org, agent_key="core.refinery", purpose="test",
                                                messages=[Message("system", "ngắn"), Message("user", "hi")],
                                                json_mode=False)
    assert out.text == "whoami:boss@example.vn|opus" and out.provider == "Claude Code CLI"

    # Thêm tài khoản thứ hai rồi đổi lại (cần PIN), lượt gọi kế tiếp dùng tài khoản vừa chọn.
    await _login(api, "claude_code_cli", "c/an")
    profs = (await api.get("/cli/profiles?kind=claude_code_cli")).json()
    assert {x["email"]: x["active"] for x in profs} == {"boss@example.vn": False, "an@example.vn": True}
    boss = next(x for x in profs if x["email"] == "boss@example.vn")
    await api.send("POST", "/auth/pin/verify", {"pin": OWNER["pin"]})
    r = await api.send("POST", f"/cli/profiles/{boss['id']}/activate")
    assert r.status_code == 200 and r.json()["email"] == "boss@example.vn" and r.json()["kind"] == "claude_code_cli"
    c = ClaudeCodeClient(clis["claude"], str(clis["claude_home"]), timeout=30)
    got = await c.generate("sonnet", [Message("user", "hi")], json_mode=False, temperature=0)
    assert got.text == "whoami:boss@example.vn|sonnet"
    assert not list(clis["claude_home"].glob("*.before-login"))


async def test_claude_code_not_logged_in_is_auth_failure(clis) -> None:  # type: ignore[no-untyped-def]
    from gh.providers.clients import AuthFailed

    c = ClaudeCodeClient(clis["claude"], str(clis["claude_home"]), timeout=30)
    with pytest.raises(AuthFailed):
        await c.generate("sonnet", [Message("user", "hi")], json_mode=False, temperature=0)
    with pytest.raises(AuthFailed):
        await c.list_models()


async def test_claude_code_cli_missing_shows_friendly_message(owner_api, app, clis) -> None:  # type: ignore[no-untyped-def]
    app.state.cli_logins.claude_argv = ["/khong/co/claude"]
    await verify_pin(owner_api)  # v0.1.45 (F-20): thêm tài khoản CLI cần PIN
    r = await owner_api.send("POST", "/cli/login?kind=claude_code_cli")
    st = await _wait(owner_api, r.json()["login_id"], ("failed", "done"))
    assert st["status"] == "failed" and "chưa cài Claude Code CLI" in st["message"]


async def test_claude_code_hardening_probe_limit_and_model_name(owner_api, clis, tmp_path,
                                                                monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Review v0.1.31: lời nhắn hệ thống qua tệp tạm 0600 (xoá ngay), tên model kiểu cờ bị chặn, gọi thử có giới hạn."""
    import tempfile

    api = owner_api
    await _login(api, "claude_code_cli", "c/boss")
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    c = ClaudeCodeClient(clis["claude"], str(clis["claude_home"]), timeout=30)
    got = await c.generate("sonnet", [Message("system", "bí mật hệ thống " * 8_000), Message("user", "hi")],
                           json_mode=False, temperature=0)
    assert got.text == "whoami:boss@example.vn|sonnet"
    assert not list(tmp_path.glob("gh-claude-sys-*"))

    p = await _provider(api, "claude_code_cli")
    r = await api.send("POST", f"/providers/{p['id']}/models", {"model_name": "--dangerously-skip-permissions"})
    assert r.status_code == 422
    from gh.system_api import routes as sysroutes

    codes = [(await api.send("POST", f"/providers/{p['id']}/models", {"model_name": f"m-{i}"})).status_code
             for i in range(sysroutes.PROBE_LIMIT + 1)]
    assert codes[:-1] == [422] * sysroutes.PROBE_LIMIT and codes[-1] == 429
