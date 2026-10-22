"""v0.1.49 (QD-16) — kiểm tích hợp ba gói: gen-cong-cu (tool Gen), hub-doc-google (đọc Google qua Gen-hub, che,
đệm, ngắt mạch) và ban-tin-hub (Bản tin Gen có lịch/mail/việc). Gen-hub `/mcp` giả là `FakeHub` của
tests/test_hub_google_v0149.py, gắn vào `app.state.mcp_transport` (route/ToolRunner) và `briefing.HUB_TRANSPORT`
(worker Bản tin). Không có lần gọi tool GHI nào tới Gen-hub giả."""

import base64
from typing import Any

import httpx
import orjson
import pytest
from sqlalchemy import text

from gh.db import sessionmaker
from gh.gen import briefing
from gh.gen.tools import ToolRunner
from gh.hub_link import service as hub
from tests.conftest import Api
from tests.phase2 import org_id
from tests.test_briefing_v0141 import _messages, _router, _today
from tests.test_gen import _user_of
from tests.test_hub_google_v0149 import EMAIL, GG, GOOGLE_WRITE, KEY, PHONE, TOKEN, FakeHub, _linked
from tests.test_rbac_api import login_as

LEAKS = (PHONE, EMAIL, KEY, TOKEN)


@pytest.fixture
def hub_fake(app: Any, monkeypatch: pytest.MonkeyPatch) -> FakeHub:
    h = FakeHub()
    app.state.mcp_transport = h.transport()
    monkeypatch.setattr(briefing, "HUB_TRANSPORT", h.transport())
    return h


def _clean(s: str) -> None:
    for leak in LEAKS:
        assert leak not in s, leak


def _no_write_calls(h: FakeHub) -> None:
    assert not [c for c in h.calls if c.rsplit("__", 1)[-1] in GOOGLE_WRITE], h.calls


async def test_owner_tools_read_google_masked(owner_api: Api, hub_fake: FakeHub, app: Any) -> None:
    await _linked(owner_api)
    user, token = await _user_of(owner_api)
    run = ToolRunner(app, user, token)
    cal = await run.run("hub.calendar", {"day": "today"})
    assert cal.ok, cal.text
    assert "Họp với anh Tuấn" in cal.text
    _clean(cal.text)
    mail = await run.run("hub.mail_search", {"q": "is:unread"})
    assert mail.ok, mail.text
    assert "Báo giá gấp" in mail.text and "18c2f41234567890" in mail.text   # id Gmail giữ nguyên
    _clean(mail.text)
    assert GG + "calendar_list_events" in hub_fake.calls and GG + "gmail_search" in hub_fake.calls
    _no_write_calls(hub_fake)


async def test_operator_forbidden_hub_not_called(owner_api: Api, hub_fake: FakeHub, client: httpx.AsyncClient,
                                                 db: Any, app: Any) -> None:
    await _linked(owner_api)
    base = len(hub_fake.calls)
    op = await login_as(client, db, "operator")
    try:
        user, token = await _user_of(op)
        run = ToolRunner(app, user, token)
        for name, args in (("hub.calendar", {"day": "today"}), ("hub.mail_search", {"q": "x"}),
                           ("document.list", {})):
            res = await run.run(name, args)
            assert not res.ok and res.error == "FORBIDDEN", name
    finally:
        await op.c.aclose()
    assert len(hub_fake.calls) == base


async def test_owner_document_list(owner_api: Api, app: Any) -> None:
    r = await owner_api.send("POST", "/documents", {
        "title": "Báo giá ván MDF", "description": f"Gọi {PHONE}, mail {EMAIL}", "filename": "bg.txt",
        "mime": "text/plain", "content_base64": base64.b64encode(b"noi dung").decode()})
    assert r.status_code == 201, r.text
    user, token = await _user_of(owner_api)
    res = await ToolRunner(app, user, token).run("document.list", {})
    assert res.ok, res.text
    assert "Báo giá ván MDF" in res.text and r.json()["id"] in res.text
    _clean(res.text)


async def test_real_briefing_with_fake_hub(owner_api: Api, hub_fake: FakeHub, db: Any, redis: Any) -> None:
    await _linked(owner_api)
    hub_fake.calls.clear()
    out = await briefing.run_briefing(sessionmaker(), redis, _router(redis), now=_today(7, 31))
    assert out[str(await org_id(db))] == "sent"
    c = (await _messages(db))[0]
    secs = {s["key"]: s for s in c["sections"]}
    assert [s["key"] for s in c["sections"]][4:7] == ["calendar_today", "mail_reply", "gtasks_open"]
    cal = secs["calendar_today"]
    assert cal["title"] == "Lịch hôm nay" and cal["state"] == "ok" and cal["count"] == 2
    assert cal["lines"][0].startswith("09:00 · Họp với anh Tuấn") and cal["lines"][1] == "Cả ngày · Nghỉ lễ"
    assert secs["mail_reply"]["count"] == 1 and "Báo giá gấp" in secs["mail_reply"]["lines"][0]
    tasks = secs["gtasks_open"]["lines"]
    assert len(tasks) == 1 and tasks[0].startswith("Gọi anh Tuấn") and tasks[0].endswith("— hạn 10/10")
    assert c["hub_hint"] is None
    body = orjson.dumps(c).decode()
    _clean(body)
    assert "nội dung thư riêng tư" not in body                             # không lấy snippet
    assert sorted(hub_fake.calls) == sorted([GG + "calendar_list_events", GG + "gmail_search", GG + "tasks_list"])
    _no_write_calls(hub_fake)
    # Lần đọc của bản tin ghi actor hệ thống (đọc nhân danh tổ chức, chỉ để gửi Owner).
    await db.rollback()
    actors = (await db.execute(text("""SELECT DISTINCT actor_id FROM ops.action_log
                                       WHERE action = 'mcp.call_ok' AND actor_type = 'system'"""))).scalars().all()
    assert actors == ["system:gen.briefing"]


async def test_write_tool_never_reaches_hub(owner_api: Api, hub_fake: FakeHub, db: Any, redis: Any) -> None:
    await _linked(owner_api)
    user, _ = await _user_of(owner_api)
    for suffix in GOOGLE_WRITE:
        with pytest.raises(Exception) as ei:
            await hub.call_hub(db, redis, hub.client_for(hub_fake.transport()), user=user, suffix=suffix, args={})
        assert getattr(ei.value, "code", None) == "HUB_TOOL_NOT_ALLOWED", suffix
    _no_write_calls(hub_fake)
