"""v0.1.70 (dọn sót v0.1.69, F098): MỌI chỗ đóng sự cố phải truyền `redis` cho `health.clear` — có redis thì chuông đang
mở đổi ngay sang "Đã xử lý" (WebSocket `notification.resolved`), không phải đợi 120 giây tải lại.

Chỗ còn sót: Gen-hub (ngắt mạch đóng / hết dấu im), Telegram (xoá cấu hình, Gửi thử qua, flush hộp thư đi), phiên Claude
Code chung chỗ với agy (providers/cli.py)."""

import uuid
from typing import Any

import httpx
import pytest
from sqlalchemy import text

from gh import health
from gh.db import sessionmaker
from gh.hub_link import service as hub
from gh.providers import cli as climod
from gh.telegram import client as tg
from gh.telegram import service as tsvc
from tests.phase2 import org_id
from tests.test_telegram_outbox_v0144 import Sink, _configure, _enqueue


class Spy:
    """Ghi `redis` mỗi lần `health.clear` được gọi (vẫn chạy bản thật)."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.calls: list[tuple[str, Any]] = []
        real = health.clear

        async def clear(db: Any, org: uuid.UUID, key: str, **kw: Any) -> bool:
            self.calls.append((key, kw.get("redis")))
            return await real(db, org, key, **kw)

        monkeypatch.setattr(health, "clear", clear)

    def redis_of(self, key: str) -> list[Any]:
        return [r for k, r in self.calls if k == key]


async def _open(db: Any, org: uuid.UUID, key: str) -> None:
    await health.raise_once(db, org, key=key, kind=key, severity="warn", title="Sự cố thử", body="x", link=None)
    await db.commit()


async def test_hub_breaker_ok_and_watch_pass_redis(owner_api: Any, db: Any, redis: Any,
                                                   monkeypatch: pytest.MonkeyPatch) -> None:
    org = await org_id(db)
    spy = Spy(monkeypatch)
    await hub._breaker_ok(db, redis, org)
    assert await hub._watch_org(db, redis, org, 1_800_000_000.0) is False   # không còn dấu "bắt đầu im" ⇒ đóng
    assert spy.redis_of(hub.BREAKER_KEY) == [redis, redis]


async def test_telegram_delete_config_passes_redis(owner_api: Any, db: Any, redis: Any,
                                                   monkeypatch: pytest.MonkeyPatch) -> None:
    org = await _configure(db)
    spy = Spy(monkeypatch)
    await tsvc.delete_config(db, org, redis=redis)
    assert spy.redis_of(tsvc.ALERT_KEY) == [redis]


async def test_telegram_test_send_pass_passes_redis(owner_api: Any, db: Any, redis: Any,
                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    org = await _configure(db)

    def handle(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True, "result": {}})

    spy = Spy(monkeypatch)
    client = tg.client_for(httpx.MockTransport(handle))
    rec, _ = await tsvc.run_test(db, org, None, client=client, redis=redis)
    assert rec["status"] == "pass" and spy.redis_of(tsvc.ALERT_KEY) == [redis]


async def test_telegram_flush_closes_incident_with_redis(owner_api: Any, db: Any, redis: Any,
                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    org = await _configure(db)
    await _enqueue(db, org, 1)
    await _open(db, org, tsvc.ALERT_KEY)
    spy = Spy(monkeypatch)
    out = await tsvc.flush_outbox(sessionmaker(), transport=httpx.MockTransport(Sink().handle), redis=redis)
    assert out["sent"] == 1 and spy.redis_of(tsvc.ALERT_KEY) == [redis]


async def test_claude_home_not_shared_closes_incident_with_redis(owner_api: Any, db: Any, redis: Any,
                                                                  monkeypatch: pytest.MonkeyPatch) -> None:
    org = await org_id(db)
    await _open(db, org, climod.CLAUDE_HOME_SHARED_KEY)
    spy = Spy(monkeypatch)
    await climod._report_claude_home(sessionmaker(), False, redis=redis)
    assert spy.redis_of(climod.CLAUDE_HOME_SHARED_KEY) == [redis]
    await db.rollback()
    cleared = (await db.execute(text("SELECT cleared_at FROM ops.health_alerts WHERE key = :k"),
                                {"k": climod.CLAUDE_HOME_SHARED_KEY})).scalar_one()
    assert cleared is not None
