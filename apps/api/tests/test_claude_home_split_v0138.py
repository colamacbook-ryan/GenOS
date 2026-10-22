"""F-22 (v0.1.38): phiên Claude Code tách khỏi HOME của Antigravity CLI.

≤ v0.1.37: GH_CLAUDE_HOME=/var/lib/gh/agy/claude/.claude nằm TRONG HOME của agy (/var/lib/gh/agy) ⇒ agy (có công cụ
đọc tệp) đọc được `.credentials.json` của Claude. v0.1.38: volume claude_state riêng (/var/lib/gh/claude/.claude); api
tự chuyển tệp từ đường dẫn cũ khi khởi động. Review v0.1.63 (P1-1): bỏ hẳn bước chuyển đó (mọi bản cài đã qua v0.1.38) —
api không còn chép gì từ volume agy_state sang claude_state."""

import logging
import re
import stat
from pathlib import Path

import pytest
import yaml

from gh.config import get_settings
from gh.providers import cli as climod

REPO = Path(__file__).resolve().parents[3]
CRED = b'{"claudeAiOauth":{"accessToken":"gia-khong-that"}}'
STATE = b'{"oauthAccount":{"emailAddress":"gia@example.invalid"}}'


@pytest.fixture
def homes(tmp_path, monkeypatch):  # type: ignore[no-untyped-def]
    """HOME agy tạm (<tmp>/agy, GH_CLI_HOME=<tmp>/agy/.gemini/antigravity-cli) + GH_CLAUDE_HOME tách riêng."""
    agy_home = tmp_path / "agy"
    (agy_home / ".gemini" / "antigravity-cli").mkdir(parents=True)
    target = tmp_path / "claude" / ".claude"
    monkeypatch.setenv("GH_CLI_HOME", str(agy_home / ".gemini" / "antigravity-cli"))
    monkeypatch.setenv("GH_CLAUDE_HOME", str(target))
    get_settings.cache_clear()
    legacy = agy_home / "claude" / ".claude"
    legacy.mkdir(parents=True)
    (legacy / ".credentials.json").write_bytes(CRED)
    (legacy / ".claude.json").write_bytes(STATE)
    (agy_home / "claude" / "work").mkdir()
    yield agy_home, legacy, target
    get_settings.cache_clear()


def _mode(p: Path) -> int:
    return stat.S_IMODE(p.stat().st_mode)


async def test_claude_home_inside_agy_home_logs_error(tmp_path, monkeypatch, caplog) -> None:  # type: ignore[no-untyped-def]
    agy_home = tmp_path / "agy"
    (agy_home / ".gemini" / "antigravity-cli").mkdir(parents=True)
    monkeypatch.setenv("GH_CLI_HOME", str(agy_home / ".gemini" / "antigravity-cli"))
    monkeypatch.setenv("GH_CLAUDE_HOME", str(agy_home / "claude" / ".claude"))
    get_settings.cache_clear()
    try:
        with caplog.at_level(logging.ERROR, logger="gh.cli"):
            await climod.restore_active(None)
        assert "GH_CLAUDE_HOME nằm trong HOME của Antigravity CLI — agy có thể đọc phiên Claude" in [
            r.getMessage() for r in caplog.records if r.levelno == logging.ERROR]
    finally:
        get_settings.cache_clear()


def test_compose_and_dockerfile_split_claude_volume() -> None:
    doc = yaml.safe_load((REPO / "deploy" / "compose.yaml").read_text(encoding="utf-8"))
    assert "claude_state" in doc["volumes"]
    assert "claude_state:/var/lib/gh/claude" in doc["services"]["api"]["volumes"]
    worker = [v for v in doc["services"]["worker"]["volumes"] if isinstance(v, dict) and v["source"] == "claude_state"]
    assert worker and worker[0]["target"] == "/var/lib/gh/claude" and worker[0]["volume"]["nocopy"] is True
    dockerfile = (REPO / "deploy" / "images" / "api.Dockerfile").read_text(encoding="utf-8")
    assert "GH_CLAUDE_HOME=/var/lib/gh/claude/.claude" in dockerfile
    homes = re.findall(r"GH_CLAUDE_HOME=(\S+)", dockerfile)
    assert homes and all(not h.startswith("/var/lib/gh/agy") for h in homes)
    assert "GH_CLAUDE_LEGACY_HOME=" not in dockerfile     # review v0.1.63 (P1-1): không còn chuyển từ volume agy


async def test_shared_home_opens_incident_then_clears(owner_api, app, tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Có sẵn gh.health (v0.1.36) ⇒ GH_CLAUDE_HOME trong HOME agy mở sự cố `cli.claude_home_shared`; tách thì đóng."""
    from sqlalchemy import text

    from gh.db import sessionmaker

    agy_home = tmp_path / "agy"
    (agy_home / ".gemini" / "antigravity-cli").mkdir(parents=True)
    monkeypatch.setenv("GH_CLI_HOME", str(agy_home / ".gemini" / "antigravity-cli"))
    monkeypatch.setenv("GH_CLAUDE_HOME", str(agy_home / "claude" / ".claude"))
    get_settings.cache_clear()
    sql = text("SELECT count(*) FROM ops.health_alerts WHERE key = 'cli.claude_home_shared' AND cleared_at IS NULL")
    try:
        await climod.restore_active(sessionmaker())
        async with sessionmaker()() as s:
            assert (await s.execute(sql)).scalar_one() == 1
        monkeypatch.setenv("GH_CLAUDE_HOME", str(tmp_path / "claude" / ".claude"))
        get_settings.cache_clear()
        await climod.restore_active(sessionmaker())
        async with sessionmaker()() as s:
            assert (await s.execute(sql)).scalar_one() == 0
    finally:
        get_settings.cache_clear()


async def test_dev_shared_home_warns_without_incident(owner_api, app, tmp_path, monkeypatch, caplog) -> None:  # type: ignore[no-untyped-def]
    """Review: dev ngoài Docker (HOME agy = ~) ⇒ chỉ cảnh báo trong log, không mở sự cố đỏ mỗi lần khởi động."""
    from sqlalchemy import text

    from gh.db import sessionmaker

    agy_home = tmp_path / "agy"
    (agy_home / ".gemini" / "antigravity-cli").mkdir(parents=True)
    monkeypatch.setenv("GH_CLI_HOME", str(agy_home / ".gemini" / "antigravity-cli"))
    monkeypatch.setenv("GH_CLAUDE_HOME", str(agy_home / "claude" / ".claude"))
    monkeypatch.setenv("GH_ENV", "development")
    get_settings.cache_clear()
    sql = text("SELECT count(*) FROM ops.health_alerts WHERE key = 'cli.claude_home_shared' AND cleared_at IS NULL")
    try:
        with caplog.at_level(logging.WARNING, logger="gh.cli"):
            await climod.restore_active(sessionmaker())
        assert not [r for r in caplog.records if r.levelno >= logging.ERROR]
        assert any(climod.CLAUDE_HOME_SHARED_MSG in r.getMessage() for r in caplog.records)
        async with sessionmaker()() as s:
            assert (await s.execute(sql)).scalar_one() == 0
    finally:
        get_settings.cache_clear()


async def test_legacy_dir_in_agy_volume_is_never_copied(homes) -> None:  # type: ignore[no-untyped-def]
    """Review v0.1.63 (P1-1): thư mục `claude/.claude` trong volume của agy (ai đó cùng uid có thể đặt vào) KHÔNG được
    api chép sang phiên Claude lúc khởi động — kể cả khi đích còn trống."""
    agy_home, legacy, target = homes
    await climod.restore_active(None)
    assert not (target / ".credentials.json").exists() and not (target / ".claude.json").exists()
    assert (legacy / ".credentials.json").read_bytes() == CRED
