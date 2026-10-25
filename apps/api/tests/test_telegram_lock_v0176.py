# ruff: noqa: E501
"""v0.1.76 (gói T2, Gen giao) — lệnh khẩn "khoá trợ lý" từ kênh phụ Telegram (spec r6 §8.1): CHỈ khoá (mở khoá chỉ Console + PIN).
Đi qua `backup.lock_assistant(via="telegram")` ⇒ `_authorize` so `from.id` + `chat.id` với tài khoản đã ghép (không theo tên).
Tấn công: người lạ / nhóm / tin chuyển tiếp không khoá được (bị bỏ ở `evaluate`), không có lệnh mở khoá qua Telegram."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from sqlalchemy import text

from gh.db import admin_sessionmaker, sessionmaker
from gh.telegram import backup, poller
from tests.conftest import Api
from tests.test_telegram_inbound_v0164 import OWNER_TG, FakeBot, _org, _pair, msg


@pytest.fixture
def bot(app: Any) -> FakeBot:
    f = FakeBot()
    app.state.telegram_transport = httpx.MockTransport(f.handle)
    return f


async def locked() -> bool:
    async with admin_sessionmaker()() as s:
        return await backup.is_locked(s, await _org())


async def run(bot: FakeBot, redis: Any, *updates: dict[str, Any]) -> list[str]:
    bot.updates = list(updates)
    out = await poller.poll_org(sessionmaker(), redis, None, await _org(), transport=httpx.MockTransport(bot.handle))
    return out["results"]  # type: ignore[no-any-return]


@pytest.mark.parametrize("body", ["/khoa_tro_ly", "khoá trợ lý", "Khoa tro ly!"])
async def test_paired_owner_can_lock_from_telegram(owner_api: Api, bot: FakeBot, redis: Any, body: str) -> None:
    await _pair(owner_api, bot, redis, update_id=1)
    bot.calls.clear()
    res = await run(bot, redis, msg(2, body, date=datetime.now(UTC)))
    assert res == ["handled:khoa_tro_ly"], res
    assert await locked()
    assert "KHOÁ" in bot.sent()[-1]["text"] or "khoá" in bot.sent()[-1]["text"]
    async with admin_sessionmaker()() as s:
        via = (await s.execute(text("SELECT detail->>'via' FROM ops.action_log WHERE action = 'assistant.lock'"))).scalar_one()
    assert via == "telegram"


async def test_second_lock_says_already_locked(owner_api: Api, bot: FakeBot, redis: Any) -> None:
    await _pair(owner_api, bot, redis, update_id=1)
    now = datetime.now(UTC)
    await run(bot, redis, msg(2, "/khoa_tro_ly", date=now))
    bot.calls.clear()
    await run(bot, redis, msg(3, "/khoa_tro_ly", date=now))
    assert "đã khoá" in bot.sent()[-1]["text"]


@pytest.mark.parametrize("over", [{"user_id": 111}, {"chat_id": 222}, {"chat_type": "group"}, {"forward_date": 1}])
async def test_stranger_group_or_forwarded_cannot_lock(owner_api: Api, bot: FakeBot, redis: Any, over: dict[str, Any]) -> None:
    await _pair(owner_api, bot, redis, update_id=1)
    res = await run(bot, redis, msg(2, "/khoa_tro_ly", date=datetime.now(UTC), **over))
    assert res[0].startswith("rejected:") and not await locked()


@pytest.mark.parametrize("body", ["/mo_khoa_tro_ly", "mở khoá trợ lý", "/mo_khoa"])
async def test_no_unlock_via_telegram(owner_api: Api, bot: FakeBot, redis: Any, body: str) -> None:
    await _pair(owner_api, bot, redis, update_id=1)
    now = datetime.now(UTC)
    await run(bot, redis, msg(2, "/khoa_tro_ly", date=now))
    bot.calls.clear()
    await run(bot, redis, msg(3, body, date=now))
    assert await locked()
    assert OWNER_TG


# ─── công tắc "Nhận tin từ Sếp" TẮT: chỉ lệnh khoá (chiều an toàn) được đi qua — Gen quyết ─────────

async def _inbound_off() -> None:
    async with admin_sessionmaker()() as s:
        await s.execute(text("UPDATE ops.notify_channels SET inbound_enabled = false"))
        await s.commit()


async def test_inbound_off_owner_lock_still_works(owner_api: Api, bot: FakeBot, redis: Any) -> None:
    await _pair(owner_api, bot, redis, update_id=1)
    await _inbound_off()
    res = await run(bot, redis, msg(2, "/khoa_tro_ly", date=datetime.now(UTC)))
    assert res == ["handled:khoa_tro_ly"], res
    assert await locked()


async def test_inbound_off_stranger_lock_is_rejected(owner_api: Api, bot: FakeBot, redis: Any) -> None:
    await _pair(owner_api, bot, redis, update_id=1)
    await _inbound_off()
    res = await run(bot, redis, msg(2, "/khoa_tro_ly", date=datetime.now(UTC), user_id=111, chat_id=111))
    assert res and res[0].startswith("rejected:") and not await locked()


async def test_inbound_off_other_commands_still_rejected_disabled(owner_api: Api, bot: FakeBot, redis: Any) -> None:
    await _pair(owner_api, bot, redis, update_id=1)
    await _inbound_off()
    bot.calls.clear()
    res = await run(bot, redis, msg(2, "/hom_nay", date=datetime.now(UTC)), msg(3, "khoá trợ lý giúp em với", date=datetime.now(UTC)))
    assert res == ["rejected:disabled", "rejected:disabled"], res
    assert bot.sent() == [] and not await locked()
    async with admin_sessionmaker()() as s:
        n = (await s.execute(text("SELECT count(*) FROM ops.action_log WHERE action = 'telegram.rejected' AND detail->>'reason' = 'disabled'"))).scalar_one()
    assert n == 2
