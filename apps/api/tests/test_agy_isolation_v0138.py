# ruff: noqa: F811 — fixture `clis` nhập từ test_cli_models_v0131 (tham số test trùng tên là cách pytest dùng fixture)
"""v0.1.38 (F-22) — Antigravity CLI chạy cô lập: cwd rỗng 0700 riêng mỗi lượt (xoá sau lượt, kể cả hết giờ), prompt
CHỈ qua stdin (không -p trên argv), `--model=<tên>` qua AGY_MODEL_RE, `--disable-slash-commands`; canary
`python -m gh.providers.agy_canary`.

CLI giả: tests/fixtures/fake_agy_multi.py (đo cờ, ghi cwd/via_stdin/argv), tests/fixtures/fake_agy_hostile.py (agy bị
prompt độc điều khiển: đọc mọi thứ đọc được rồi in ra).
"""

import base64
import json
import logging
import os
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Any

import orjson
import pytest
from sqlalchemy import text

from gh.providers.clients import AGY_MODEL_RE, AgyClient, BadRequest, Message
from tests.test_cli_models_v0131 import (
    _login,
    _provider,
    _wrapper,
    clis,  # noqa: F401 — fixture dùng chung
)

FIX = Path(__file__).parent / "fixtures"
API_DIR = Path(__file__).resolve().parents[1]


def _sign_in(agy_home: Path, email: str = "an@example.vn") -> None:
    """Tệp phiên giả (đúng định dạng fake_agy_multi đọc) — test đơn vị không cần luồng đăng nhập qua API."""
    claims = base64.urlsafe_b64encode(json.dumps({"email": email}).encode()).decode().rstrip("=")
    agy_home.mkdir(parents=True, exist_ok=True)
    (agy_home / "antigravity-oauth-token").write_text(
        json.dumps({"access_token": "tok", "id_token": f"h.{claims}.s", "expiry": 4102444800}))


@pytest.fixture
def agy(tmp_path: Path) -> dict[str, Any]:
    home = tmp_path / "agy" / ".gemini" / "antigravity-cli"
    _sign_in(home)
    return {"bin": _wrapper(tmp_path, "agy", FIX / "fake_agy_multi.py"), "home": home,
            "log": tmp_path / "agy" / "agy-calls.log"}


def _calls(log: Path) -> list[dict[str, Any]]:
    return [json.loads(x) for x in log.read_text().splitlines()] if log.exists() else []


def _within(child: str | Path, parent: str | Path) -> bool:
    try:
        Path(child).resolve().relative_to(Path(os.path.expanduser(parent)).resolve())
        return True
    except ValueError:
        return False


def _gone(p: str | Path) -> bool:
    return not Path(p).exists()


def _plant_claude_canary(claude_home: Path, canary: str) -> None:
    claude_home.mkdir(parents=True, exist_ok=True)
    (claude_home / ".credentials.json").write_text(json.dumps({"claudeAiOauth": {"accessToken": canary}}))


# ─── (1) cwd riêng, rỗng, 0700, xoá sau lượt ──────────────────────────────

async def test_run_uses_fresh_private_cwd_removed_after_turn(agy: dict[str, Any]) -> None:
    c = AgyClient(agy["bin"], str(agy["home"]), timeout=30)
    out = await c.generate("gemini-3.1-pro", [Message("user", "hi")], json_mode=False, temperature=0)
    assert out.text == "whoami:an@example.vn"
    await c.generate("gemini-3.1-pro", [Message("user", "hi")], json_mode=False, temperature=0)
    first, second = _calls(agy["log"])[-2:]
    for call in (first, second):
        assert Path(call["cwd"]).name.startswith("gh-agy-")
        assert call["cwd_entries"] == 0 and call["cwd_mode"] == "0o700"
        assert not _within(call["cwd"], agy["home"].parent.parent)            # không trong HOME của agy
        assert not _within(call["cwd"], os.environ["GH_CLAUDE_HOME"])
        assert _gone(call["cwd"])                                               # đã xoá sau lượt
    assert first["cwd"] != second["cwd"]                                        # mỗi lượt một thư mục mới


async def test_run_removes_cwd_even_on_timeout(tmp_path: Path, agy: dict[str, Any],
                                               monkeypatch: pytest.MonkeyPatch) -> None:
    # Thư mục tạm RIÊNG của test: /tmp dùng chung với tiến trình khác (vd hai lượt pytest song song cùng gọi agy giả)
    # làm phép so "không còn gh-agy-*" bị nhiễu.
    tmpd = tmp_path / "tmpd"
    tmpd.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(tmpd))
    slow = tmp_path / "bin" / "agy-slow"
    # Gói sửa review v0.1.63 (P1-1): HOME cũng là thư mục tạm của lượt ⇒ ghi ra chỗ cố định của test.
    slow.write_text(f'#!/bin/sh\npwd > "{tmp_path}/slow-cwd"\necho "$HOME" > "{tmp_path}/slow-home"\nexec sleep 30\n')
    slow.chmod(0o755)
    c = AgyClient(str(slow), str(agy["home"]), timeout=3.0)
    with pytest.raises(TimeoutError):
        await c._run("--output-format", "json", stdin=b"hi")
    cwd = (tmp_path / "slow-cwd").read_text().strip()
    assert Path(cwd).name.startswith("gh-agy-") and Path(cwd).parent == tmpd and _gone(cwd)
    home = (tmp_path / "slow-home").read_text().strip()
    assert Path(home).name.startswith("gh-agy-home-") and Path(home).parent == tmpd and _gone(home)
    # generate đổi hết giờ thành ProviderError (bộ định tuyến chuyển nguồn) — vẫn không còn thư mục nào.
    from gh.providers.clients import ProviderError

    with pytest.raises(ProviderError):
        await c.generate("gemini-3.1-pro", [Message("user", "hi")], json_mode=False, temperature=0)
    assert list(tmpd.iterdir()) == []


def _alive(pid: int) -> bool:
    """Còn chạy (zombie chờ init thu dọn coi như đã chết)."""
    try:
        state = Path(f"/proc/{pid}/status").read_text()
    except OSError:
        return False
    return "\nState:\tZ" not in state and "\nState:\tX" not in state


async def _wait_dead(pid: int) -> bool:
    import asyncio

    for _ in range(50):
        if not _alive(pid):
            return True
        await asyncio.sleep(0.05)
    return False


async def test_timeout_kills_agy_children_too(tmp_path: Path, agy: dict[str, Any]) -> None:
    """Review F-22: hết giờ ⇒ giết cả nhóm tiến trình — tiến trình con agy sinh ra (công cụ chạy lệnh) không sống
    tiếp sau khi cwd đã bị xoá."""
    kid = tmp_path / "kid.pid"
    slow = tmp_path / "bin" / "agy-fork"
    slow.write_text(f'#!/bin/sh\nsleep 60 >/dev/null 2>&1 &\necho $! > "{kid}"\nexec sleep 30\n')
    slow.chmod(0o755)
    c = AgyClient(str(slow), str(agy["home"]), timeout=2.0)
    with pytest.raises(TimeoutError):
        await c._run("--output-format", "json", stdin=b"hi")
    assert await _wait_dead(int(kid.read_text().strip()))


async def test_normal_exit_kills_leftover_children(tmp_path: Path, agy: dict[str, Any]) -> None:
    kid = tmp_path / "kid.pid"
    quick = tmp_path / "bin" / "agy-bg"
    quick.write_text(f'#!/bin/sh\nsleep 60 >/dev/null 2>&1 &\necho $! > "{kid}"\necho ok\n')
    quick.chmod(0o755)
    c = AgyClient(str(quick), str(agy["home"]), timeout=20.0)
    code, out, _err = await c._run("--output-format", "json", stdin=b"hi")
    assert code == 0 and out.strip() == b"ok"
    assert await _wait_dead(int(kid.read_text().strip()))


# ─── (2) prompt qua stdin, cờ đúng ─────────────────────────────────────────

async def test_generate_sends_prompt_via_stdin_not_argv(agy: dict[str, Any]) -> None:
    prompt = "BÍ-MẬT-của-khách --dangerously-skip-permissions /model"
    c = AgyClient(agy["bin"], str(agy["home"]), timeout=30)
    out = await c.generate("gemini-3.8-flash-high", [Message("user", prompt)], json_mode=False, temperature=0)
    assert out.text == "whoami:an@example.vn"
    call = _calls(agy["log"])[-1]
    argv = call["argv"]
    assert call["via_stdin"] is True and call["prompt_len"] == len(prompt)
    assert not any("BÍ-MẬT" in a for a in argv) and "-p" not in argv and "--dangerously-skip-permissions" not in argv
    assert argv[:3] == ["--output-format", "json", "--disable-slash-commands"]
    assert "--model=gemini-3.8-flash" in argv and "--effort=high" in argv
    assert (call["model"], call["effort"]) == ("gemini-3.8-flash", "high")


# ─── (3) regex tên model ───────────────────────────────────────────────────

@pytest.mark.parametrize("name", ["gemini-3.1-pro", "a:b_c-1.2"])
def test_model_regex_accepts(name: str) -> None:
    c = AgyClient("agy", "/nonexistent")
    assert AGY_MODEL_RE.fullmatch(name)
    assert c.model_args(name) == [f"--model={name}"]


@pytest.mark.parametrize("name", ["--dangerously-skip-permissions", "x y", "a;rm", "a" * 81, ""])
async def test_model_regex_rejects_without_starting_process(agy: dict[str, Any], name: str) -> None:
    c = AgyClient(agy["bin"], str(agy["home"]), timeout=30)
    n = len(_calls(agy["log"]))
    with pytest.raises(BadRequest) as ei:
        c.model_args(name)
    assert str(ei.value) == "Tên model không hợp lệ cho Antigravity CLI"
    with pytest.raises(BadRequest):
        await c.generate(name, [Message("user", "hi")], json_mode=False, temperature=0)
    assert len(_calls(agy["log"])) == n          # tiến trình KHÔNG được khởi chạy


# ─── (4) E2E canary (mock): agy bị điều khiển vẫn không thấy bí mật ────────

async def test_hostile_agy_sees_no_canary(owner_api, app, clis, tmp_path, monkeypatch,  # type: ignore[no-untyped-def]
                                          caplog) -> None:
    api = owner_api
    await _login(api, "antigravity_cli", "4/an")
    p = await _provider(api, "antigravity_cli")
    r = await api.send("POST", f"/providers/{p['id']}/models", {"model_name": "gemini-3.1-pro", "make_default": True})
    assert r.status_code == 201, r.text
    canary = f"GHCANARY-{uuid.uuid4().hex}"
    # 1) phiên Claude Code ở thư mục TÁCH khỏi HOME của agy; 2) biến môi trường của tiến trình api/worker;
    # 3) thư mục cha của cwd lượt gọi.
    _plant_claude_canary(Path(clis["claude_home"]), canary)
    monkeypatch.setenv("GH_CANARY_SECRET", canary)
    parent = tmp_path / "tmpd"
    parent.mkdir()
    (parent / "canary.txt").write_text(canary)
    monkeypatch.setattr(tempfile, "tempdir", str(parent))
    hostile = _wrapper(tmp_path, "agy-hostile", FIX / "fake_agy_hostile.py")
    router = app.state.model_router
    router.cli_factory = lambda: AgyClient(hostile, str(clis["agy_home"]), timeout=30)

    from gh.db import sessionmaker

    async with sessionmaker()() as db:
        org = (await db.execute(text("SELECT org_id FROM agent.providers WHERE id = :i"),
                                {"i": uuid.UUID(p["id"])})).scalar_one()
        await db.execute(text("UPDATE agent.providers SET is_enabled = (kind = 'antigravity_cli') WHERE org_id = :o"),
                         {"o": org})
        await db.commit()
    caplog.set_level(logging.DEBUG)
    out = await router.generate(org, agent_key="core.gen", purpose="gen.turn", messages=[Message("user", "hi")],
                                json_mode=False)
    loot = json.loads(out.text)
    # CLI giả thật sự đã "tấn công": đọc HOME, env, cwd, tiến trình cha.
    assert loot["home_files"] and "environ" in loot and loot["parent_cmdline"] is not None
    assert Path(loot["cwd"]).parent == parent and loot["cwd_files"] == {}
    assert loot["old_claude_credentials"] is None
    assert not any(k.startswith("GH_") for k in loot["environ"])
    assert canary not in out.text
    assert canary not in caplog.text
    for f in (tmp_path / "agy").rglob("*"):
        if f.is_file():
            assert canary.encode() not in f.read_bytes(), f
    async with sessionmaker()() as db:
        rows = (await db.execute(text("SELECT to_jsonb(c)::text FROM agent.model_calls c"))).scalars().all()
    assert rows and not any(canary in r for r in rows)
    assert not list(parent.glob("gh-agy-*"))


# ─── (5) python -m gh.providers.agy_canary ─────────────────────────────────

def _canary(tmp_path: Path, binary: str, mode: str = "--offline") -> subprocess.CompletedProcess[str]:
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "GH_ENV": "test",
           "GH_CLI_BINARY": binary, "GH_CLI_HOME": str(tmp_path / "agy" / ".gemini" / "antigravity-cli"),
           "GH_CLAUDE_HOME": str(tmp_path / "claude" / ".claude"), "HOME": str(tmp_path / "runner"),
           "TMPDIR": str(tmp_path / "tmpc")}
    (tmp_path / "tmpc").mkdir(exist_ok=True)
    return subprocess.run([sys.executable, "-m", "gh.providers.agy_canary", mode], cwd=API_DIR, env=env,
                          capture_output=True, text=True, timeout=120)


def test_canary_offline_reports_khong_lo(tmp_path: Path) -> None:
    fake = _wrapper(tmp_path, "agy", FIX / "fake_agy_multi.py")      # chưa đăng nhập, ghi promptLength= như agy thật
    res = _canary(tmp_path, fake)
    lines = res.stdout.strip().splitlines()
    assert len(lines) == 1, res.stdout + res.stderr
    out = orjson.loads(lines[0])
    assert out["result"] == "khong_lo", out
    assert res.returncode == 0
    assert out["agy_version"] == "1.2.9"
    assert all(out["checks"].values()) and {"stdin_toi_print_mode", "dau_ra_khong_lo", "log_khong_lo", "env_sach",
                                            "claude_home_tach", "co_hop_le"} <= set(out["checks"])
    assert "GHCANARY" not in res.stdout + res.stderr
    assert list((tmp_path / "tmpc").iterdir()) == []      # thư mục canary + cwd lượt gọi đã xoá


def test_canary_offline_detects_leak(tmp_path: Path) -> None:
    hostile = _wrapper(tmp_path, "agy", FIX / "fake_agy_hostile.py")   # làm theo prompt: đọc tệp canary rồi in ra
    res = _canary(tmp_path, hostile)
    out = orjson.loads(res.stdout.strip().splitlines()[-1])
    assert out["result"] == "lo" and out["checks"]["dau_ra_khong_lo"] is False, out
    assert res.returncode == 1
    assert "GHCANARY" not in res.stdout + res.stderr      # không bao giờ in canary / đầu ra thô


def test_canary_reports_loi_when_agy_missing(tmp_path: Path) -> None:
    res = _canary(tmp_path, str(tmp_path / "khong-co-agy"))
    out = orjson.loads(res.stdout.strip())
    assert out["result"] == "loi" and out["checks"]["chay_duoc"] is False and res.returncode == 1


def test_canary_live_checks_injection_via_tool_output(tmp_path: Path) -> None:
    """Review F-22: --live thử thêm đường khách → kết quả công cụ của Gen → agy đọc tệp; agy làm theo ⇒ "lo"."""
    from gh.providers.agy_canary import injection_prompt
    from gh.providers.clients import TOKEN_FILE

    p = injection_prompt(Path("/run/secrets/x"))
    assert "/run/secrets/x" in p and "<<<" in p  # bọc wrap_untrusted như lượt Gen thật
    cli_home = tmp_path / "agy" / ".gemini" / "antigravity-cli"
    cli_home.mkdir(parents=True)
    (cli_home / TOKEN_FILE).write_text("{}")
    hostile = _wrapper(tmp_path, "agy", FIX / "fake_agy_hostile.py")
    res = _canary(tmp_path, hostile, "--live")
    out = orjson.loads(res.stdout.strip().splitlines()[-1])
    assert out["mode"] == "live" and out["checks"]["tiem_qua_cong_cu_khong_lo"] is False, out
    assert out["result"] == "lo" and res.returncode == 1
    assert "GHCANARY" not in res.stdout + res.stderr
