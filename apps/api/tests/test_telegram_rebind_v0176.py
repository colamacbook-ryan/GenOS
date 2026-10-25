# ruff: noqa: E501
"""v0.1.76 (T11, review bảo mật M2) — đổi / gỡ / ghép lại kênh phụ phải báo NGAY tới kênh CŨ (kẻ có phiên PIN không thay kênh trong im lặng).
Tin tới đúng chat đã ghép lúc xếp hàng, không theo cấu hình mới; Nhật ký có `strong_confirm.console`."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from sqlalchemy import text

from gh.db import admin_sessionmaker, sessionmaker
from gh.telegram import backup, poller
from tests.conftest import Api, verify_pin
from tests.test_telegram_inbound_v0164 import OWNER_TG, FakeBot, _org, _pair, msg
from tests.test_telegram_v0144 import CHAT, TOKEN2


@pytest.fixture
def bot(app: Any) -> FakeBot:
    f = FakeBot()
    app.state.telegram_transport = httpx.MockTransport(f.handle)
    return f


def old_notices(bot: FakeBot) -> list[dict[str, Any]]:
    return [m for m in bot.sent() if str(m["chat_id"]) == str(OWNER_TG) and "kênh phụ" in m["text"]]


async def _sc_count() -> int:
    async with admin_sessionmaker()() as s:
        return int((await s.execute(text("SELECT count(*) FROM ops.action_log WHERE action = 'strong_confirm.console'"))).scalar_one())


async def test_delete_config_notifies_old_channel(owner_api: Api, bot: FakeBot, redis: Any) -> None:
    await _pair(owner_api, bot, redis)
    bot.calls.clear()
    before = await _sc_count()
    r = await owner_api.send("DELETE", "/notify/telegram")
    assert r.status_code == 200, r.text
    got = old_notices(bot)
    assert len(got) == 1 and "khoá trợ lý" in got[0]["text"]
    assert await _sc_count() == before + 1


async def test_unpair_notifies_old_channel(owner_api: Api, bot: FakeBot, redis: Any) -> None:
    await _pair(owner_api, bot, redis)
    bot.calls.clear()
    assert (await owner_api.send("DELETE", "/notify/telegram/pair")).status_code == 200
    assert len(old_notices(bot)) == 1


async def test_changing_chat_or_token_notifies_old_channel(owner_api: Api, bot: FakeBot, redis: Any) -> None:
    await _pair(owner_api, bot, redis)
    bot.calls.clear()
    r = await owner_api.send("PUT", "/notify/telegram", {"token": TOKEN2, "chat_id": "424242"})
    assert r.status_code == 200, r.text
    assert len(old_notices(bot)) == 1


async def test_pair_start_notifies_old_channel(owner_api: Api, bot: FakeBot, redis: Any) -> None:
    await _pair(owner_api, bot, redis)
    bot.calls.clear()
    assert (await owner_api.send("POST", "/notify/telegram/pair/start", {})).status_code == 200
    assert len(old_notices(bot)) == 1


async def test_harmless_flag_change_does_not_notify(owner_api: Api, bot: FakeBot, redis: Any) -> None:
    await _pair(owner_api, bot, redis)
    bot.calls.clear()
    before = await _sc_count()
    r = await owner_api.send("PUT", "/notify/telegram", {"briefing": False})
    assert r.status_code == 200, r.text
    assert old_notices(bot) == [] and await _sc_count() == before


async def test_first_setup_has_no_old_channel_to_notify(owner_api: Api, bot: FakeBot) -> None:
    await verify_pin(owner_api)
    r = await owner_api.send("PUT", "/notify/telegram", {"token": TOKEN2, "chat_id": CHAT})
    assert r.status_code == 200, r.text
    assert [m for m in bot.sent() if "kênh phụ" in m["text"]] == []


async def test_repair_to_other_chat_queues_notice_to_old_chat(owner_api: Api, bot: FakeBot, redis: Any) -> None:
    """Ghép xong sang chat KHÁC: tin báo xếp hàng tới chat CŨ (đích lưu theo dòng), flush gửi tới đó chứ không tới chat mới."""
    await _pair(owner_api, bot, redis)
    code = (await owner_api.send("POST", "/notify/telegram/pair/start", {})).json()["pair"]["code"]
    bot.calls.clear()
    new_chat = 555000111
    bot.updates = [msg(50, f"/start {code}", chat_id=new_chat, user_id=new_chat, date=datetime.now(UTC))]
    out = await poller.poll_org(sessionmaker(), redis, None, await _org(), transport=httpx.MockTransport(bot.handle))
    assert out["results"] == ["paired"], out
    await backup.flush(sessionmaker(), transport=httpx.MockTransport(bot.handle))
    to_old = [m for m in bot.sent() if str(m["chat_id"]) == str(OWNER_TG) and "kênh phụ" in m["text"]]
    assert len(to_old) >= 1
    assert [m for m in bot.sent() if str(m["chat_id"]) == str(new_chat) and "kênh phụ" in m["text"]] == []     # không rơi sang chat mới
