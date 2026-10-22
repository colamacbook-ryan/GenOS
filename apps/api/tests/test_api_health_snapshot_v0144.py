"""v0.1.44 (F-6b) — ảnh chụp sức khoẻ run/api-health.json cho Trực canh máy chủ (genh watchdog).

Sau mỗi lượt `watch_loop` (đang giữ khoá Redis) api ghi nguyên tử: {schema, written_at, version, public_url,
alerts[key, kind, severity, title, body, fingerprint, raised_at], latest_backup_at, backup_stale_limit_hours}.
api KHÔNG gửi Telegram cho sự cố (đường cảnh báo duy nhất là genh watchdog)."""

import asyncio
import json
import os
import stat
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import text

from gh import __version__, health
from gh.config import get_settings
from gh.db import sessionmaker
from tests.phase2 import org_id

ALERT_KEYS = {"key", "kind", "severity", "title", "body", "fingerprint", "raised_at"}
TOP_KEYS = {"schema", "written_at", "version", "public_url", "alerts", "latest_backup_at", "backup_stale_limit_hours"}


@pytest.fixture
def host(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    d = tmp_path / "run"
    d.mkdir()
    monkeypatch.setattr(get_settings(), "host_link_dir", str(d))
    return d


@pytest.fixture
def backup_at(monkeypatch: pytest.MonkeyPatch) -> datetime:
    at = datetime(2026, 10, 2, 20, 0, tzinfo=UTC)

    async def latest() -> datetime:
        return at

    monkeypatch.setattr(health, "_latest_backup", latest)
    return at


async def test_snapshot_contract(owner_api: Any, db: Any, host: Path, backup_at: datetime) -> None:
    org = await org_id(db)
    await health.raise_once(db, org, key="channel.down:zalo", kind="channel.down", severity="bad",
                            title="Zalo đã đăng xuất", body="Bấm để đăng nhập lại.", link="/channels")
    await health.raise_once(db, org, key="telegram.failed", kind="telegram.failed", severity="warn",
                            title="Gen chưa gửi được tin Telegram cho Sếp", body="x", link="/connections#telegram",
                            fingerprint="TELEGRAM_BOT_BLOCKED")
    await db.execute(text("""UPDATE core.organizations SET settings = settings || '{"backup": {"frequency": "weekly"}}'
                             WHERE id = :o"""), {"o": org})
    await db.commit()
    now = datetime.now(UTC)
    assert await health.write_host_snapshot(db, org, now=now) is True
    f = host / "api-health.json"
    assert stat.S_IMODE(os.stat(f).st_mode) == 0o644
    data = json.loads(f.read_text())
    assert set(data) == TOP_KEYS
    assert data["schema"] == 1 and data["version"] == __version__
    assert data["public_url"] == "https://localhost:8443" and data["written_at"].endswith("Z")
    assert data["latest_backup_at"] == "2026-10-02T20:00:00Z"
    assert data["backup_stale_limit_hours"] == 7 * 24 + 12
    assert [a["key"] for a in data["alerts"]] == ["channel.down:zalo", "telegram.failed"]   # 'bad' trước
    for a in data["alerts"]:
        assert set(a) == ALERT_KEYS and a["raised_at"].endswith("Z")
    assert data["alerts"][1]["fingerprint"] == "TELEGRAM_BOT_BLOCKED" and data["alerts"][0]["fingerprint"] == ""
    assert not [p for p in host.iterdir() if p.name.endswith(".tmp")]  # noqa: ASYNC240
    # Đóng sự cố ⇒ lần ghi sau không còn.
    await health.clear(db, org, "channel.down:zalo")
    await db.commit()
    await health.write_host_snapshot(db, org, now=now)
    assert [a["key"] for a in json.loads(f.read_text())["alerts"]] == ["telegram.failed"]


async def test_snapshot_skips_without_host_dir(owner_api: Any, db: Any, tmp_path: Path,
                                               monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "host_link_dir", str(tmp_path / "khong-co"))
    assert await health.write_host_snapshot(db, await org_id(db), now=datetime.now(UTC)) is False


async def test_watch_loop_writes_snapshot(owner_api: Any, db: Any, redis: Any, host: Path,
                                          backup_at: datetime, monkeypatch: pytest.MonkeyPatch) -> None:
    """Một lượt watch_loop ⇒ có run/api-health.json; không có lệnh gọi Telegram nào từ health."""

    def no_http(req: httpx.Request) -> httpx.Response:
        raise AssertionError(f"health không được gọi mạng: {req.url.host}")

    from gh.telegram import client as tg

    monkeypatch.setattr(tg, "client_for", lambda transport=None: tg.TelegramClient(httpx.MockTransport(no_http)))
    await redis.delete(health.WATCH_LOCK_KEY)
    stop = asyncio.Event()
    task = asyncio.create_task(health.watch_loop(sessionmaker(), redis, stop, interval=0.05,
                                                 started_at=datetime.now(UTC)))
    f = host / "api-health.json"
    for _ in range(200):
        if f.exists():
            break
        await asyncio.sleep(0.05)
    stop.set()
    await asyncio.wait_for(task, 5)
    data = json.loads(f.read_text())
    assert set(data) == TOP_KEYS and data["latest_backup_at"] == "2026-10-02T20:00:00Z"
    assert data["backup_stale_limit_hours"] == 36
