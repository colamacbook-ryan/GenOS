"""v0.1.49 (F-83) — ngắt mạch riêng của Gen-hub + chuông "không trả lời hơn 15 phút" + hàm đọc cho Bản tin.

Đồng hồ tiêm được `hub._clock` (monkeypatch): 3 lỗi mạng/timeout/5xx/429 liên tiếp ⇒ mở 60 giây (không gọi mạng);
sau 60 giây nửa mở — lỗi 1 lần ⇒ mở lại ngay; 401/403 không tính; đệm vẫn trả khi mở; mở quá 15 phút ⇒ sự cố
`hub.breaker` + MỘT chuông Owner; gọi lại được ⇒ tự đóng. `briefing_read` không bao giờ ném."""

from datetime import UTC, datetime
from typing import Any

import orjson
import pytest
from sqlalchemy import text

from gh import health
from gh.db import admin_sessionmaker, sessionmaker
from gh.hub_link import service as hub
from gh.worker import JOB_LABELS, WorkerSettings
from tests.conftest import Api
from tests.test_gen import _user_of
from tests.test_hub_google_v0149 import (
    EMAIL,
    GG,
    KEY,
    MAIL_ID,
    PHONE,
    TOKEN,
    FakeHub,
    _linked,
    _pin,
)
from tests.test_rbac_api import login_as

T0 = 1_800_000_000.0
SEARCH = "/hub/google/mail/search"


class Clock:
    def __init__(self) -> None:
        self.now = T0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def fake_hub(app: Any) -> FakeHub:
    h = FakeHub()
    app.state.mcp_transport = h.transport()
    return h


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> Clock:
    c = Clock()
    monkeypatch.setattr(hub, "_clock", c)
    return c


async def _db_text(sql: str) -> str:
    async with admin_sessionmaker()() as db:
        return orjson.dumps((await db.execute(text(sql))).all(), default=str).decode()


async def _keys(redis: Any) -> list[str]:
    return sorted(k.decode() for k in [k async for k in redis.scan_iter(match="gh:hub:brk:*")])


async def _count(sql: str) -> int:
    async with admin_sessionmaker()() as db:
        return int((await db.execute(text(sql))).scalar_one())


async def _n_calls() -> int:
    return await _count("SELECT count(*) FROM agent.mcp_calls")


async def _open_breaker(api: Api, fake: FakeHub, *, mode: str = "timeout", path: str = SEARCH) -> None:
    fake.mode = mode
    for i in range(3):
        r = await api.get(path, params={"q": f"lỗi {i}"} if path == SEARCH else None)
        assert r.status_code == 409 and r.json()["code"] == "HUB_UNAVAILABLE", r.text


async def _alerts() -> list[Any]:
    async with admin_sessionmaker()() as db:
        return list((await db.execute(text("""SELECT key, kind, severity, fingerprint, cleared_at, title
                                              FROM ops.health_alerts WHERE key = 'hub.breaker'"""))).all())


async def _bells() -> int:
    return await _count("SELECT count(*) FROM core.notifications WHERE kind = 'hub.unreachable'")


# ─── 1. 3 lỗi ⇒ mở; lần 4 không gọi mạng ────────────────────────────────────────

async def test_three_timeouts_open_breaker(owner_api: Api, fake_hub: FakeHub, clock: Clock, redis: Any) -> None:
    await _linked(owner_api)
    base = fake_hub.requests
    fake_hub.mode = "timeout"
    for i in range(3):
        r = await owner_api.get(SEARCH, params={"q": f"lần {i}"})
        assert r.status_code == 409 and r.json()["code"] == "HUB_UNAVAILABLE"
        assert fake_hub.requests == base + i + 1
    org = (await _user_of(owner_api))[0].org_id
    assert [k.split(":")[3] for k in await _keys(redis)] == ["down_since", "half", "open_until"]
    n_logs = await _n_calls()
    r = await owner_api.get(SEARCH, params={"q": "lần 4"})
    assert r.status_code == 409 and r.json()["code"] == "HUB_BREAKER_OPEN", r.text
    assert "Gen-hub tạm không trả lời" in r.json()["title"] and "60 giây" in r.json()["detail"]
    assert fake_hub.requests == base + 3  # KHÔNG gọi mạng
    assert await _n_calls() == n_logs  # không ghi mcp_calls
    state = await hub.breaker_state(redis, org)
    assert state["open"] is True and 0 < state["retry_in_s"] <= 60 and state["down_since"].startswith("2027-01-1")
    link = (await owner_api.get("/hub/link")).json()
    assert link["breaker"]["open"] is True and link["breaker"]["retry_in_s"] == 60
    # Kho cũng bị chặn (cùng breaker); nút Kiểm tra KHÔNG bị chặn.
    assert (await owner_api.get("/hub/kho/summary")).json()["code"] == "HUB_BREAKER_OPEN"
    await _pin(owner_api)
    r = await owner_api.send("POST", "/hub/link/test", {})
    assert r.status_code == 200 and r.json()["ok"] is False and fake_hub.requests > base + 3


async def test_5xx_and_429_count_too(owner_api: Api, fake_hub: FakeHub, clock: Clock, redis: Any) -> None:
    await _linked(owner_api)
    fake_hub.mode = "500"
    for i in range(2):
        assert (await owner_api.get(SEARCH, params={"q": f"a{i}"})).json()["code"] == "HUB_UNAVAILABLE"
    fake_hub.mode = "429"  # lỗi khác loại cộng dồn vào cùng bộ đếm
    assert (await owner_api.get(SEARCH, params={"q": "b"})).json()["code"] == "HUB_UNAVAILABLE"
    assert (await owner_api.get(SEARCH, params={"q": "c"})).json()["code"] == "HUB_BREAKER_OPEN"


# ─── 2. 61 giây sau: nửa mở, thành công ⇒ đóng + xoá khoá ───────────────────────────

async def test_half_open_success_closes_and_clears_keys(owner_api: Api, fake_hub: FakeHub, clock: Clock,
                                                        redis: Any) -> None:
    await _linked(owner_api)
    await _open_breaker(owner_api, fake_hub)
    clock.now += 30
    assert (await owner_api.get(SEARCH, params={"q": "x"})).json()["code"] == "HUB_BREAKER_OPEN"
    clock.now += 31  # +61 giây
    fake_hub.mode = "ok"
    base = fake_hub.requests
    r = await owner_api.get(SEARCH, params={"q": "x"})
    assert r.status_code == 200 and fake_hub.requests == base + 1
    assert await _keys(redis) == []
    assert (await hub.breaker_state(redis, (await _user_of(owner_api))[0].org_id)) == {
        "open": False, "retry_in_s": None, "down_since": None}
    assert (await owner_api.get("/hub/link")).json()["breaker"]["open"] is False


# ─── 3. Nửa mở mà lỗi ⇒ mở lại ngay sau 1 lỗi ──────────────────────────────────────

async def test_half_open_failure_reopens_immediately(owner_api: Api, fake_hub: FakeHub, clock: Clock,
                                                     redis: Any) -> None:
    await _linked(owner_api)
    await _open_breaker(owner_api, fake_hub)
    clock.now += 61
    base = fake_hub.requests
    r = await owner_api.get(SEARCH, params={"q": "thử lại"})
    assert r.json()["code"] == "HUB_UNAVAILABLE" and fake_hub.requests == base + 1
    clock.now += 1
    r = await owner_api.get(SEARCH, params={"q": "thử lại"})
    assert r.json()["code"] == "HUB_BREAKER_OPEN" and fake_hub.requests == base + 1  # đã mở lại sau ĐÚNG 1 lỗi
    org = (await _user_of(owner_api))[0].org_id
    assert 55 <= (await hub.breaker_state(redis, org))["retry_in_s"] <= 60


# ─── 4. 401 / 403 không làm mở breaker ──────────────────────────────────────────────

async def test_401_does_not_open_breaker(owner_api: Api, fake_hub: FakeHub, clock: Clock, redis: Any) -> None:
    await _linked(owner_api)
    fake_hub.mode = "401"
    base = fake_hub.requests
    for i in range(6):
        r = await owner_api.get(SEARCH, params={"q": f"t{i}"})
        assert r.status_code == 409 and r.json()["code"] == "HUB_UNAVAILABLE" and "hết hạn" in r.json()["detail"]
    assert fake_hub.requests == base + 6 and await _keys(redis) == []
    assert (await owner_api.get("/hub/link")).json()["status"] == "expired"
    assert hub._counts_for_breaker(Exception("403: forbidden")) is False
    assert hub._counts_for_breaker(Exception("404: không thấy")) is False  # lỗi cấu hình, không phải mất kết nối
    assert hub._counts_for_breaker(Exception("{'code': -32000, 'message': 'tool lỗi'}")) is False
    for yes in ("mạng: hết giờ", "500: x", "503: x", "429: x", "408: x", "phản hồi không phải JSON: <html>"):
        assert hub._counts_for_breaker(Exception(yes)) is True, yes


# ─── 5. Đệm vẫn trả khi breaker mở ──────────────────────────────────────────────────

async def test_cache_still_served_while_open(owner_api: Api, fake_hub: FakeHub, clock: Clock) -> None:
    await _linked(owner_api)
    r = await owner_api.get(SEARCH, params={"q": "đã đệm"})
    assert r.status_code == 200 and r.json()["cached"] is False
    await _open_breaker(owner_api, fake_hub)
    base = fake_hub.requests
    r = await owner_api.get(SEARCH, params={"q": "đã đệm"})
    assert r.status_code == 200 and r.json()["cached"] is True
    assert (await owner_api.get(SEARCH, params={"q": "chưa đệm"})).json()["code"] == "HUB_BREAKER_OPEN"
    assert fake_hub.requests == base


# ─── 6. breaker_watch: 15 phút + đã mở ⇒ đúng 1 sự cố + 1 chuông ───────────────────────

async def test_breaker_watch_raises_once_and_clears(owner_api: Api, fake_hub: FakeHub, clock: Clock,
                                                    redis: Any, db: Any) -> None:
    await _linked(owner_api)
    await _open_breaker(owner_api, fake_hub)
    sm = sessionmaker()
    clock.now = T0 + 10 * 60
    assert await hub.breaker_watch(sm, redis) == 0 and await _alerts() == []  # mới 10 phút
    clock.now = T0 + 16 * 60
    assert await hub.breaker_watch(sm, redis) == 1
    [alert] = await _alerts()
    assert alert.kind == "hub.unreachable" and alert.severity == "warn" and alert.fingerprint == "open"
    assert alert.cleared_at is None and alert.title == "Gen-hub không trả lời hơn 15 phút"
    assert await _bells() == 1
    assert await hub.breaker_watch(sm, redis) == 0  # gọi lại: không chuông thứ hai
    assert len(await _alerts()) == 1 and await _bells() == 1
    bells = (await owner_api.get("/notifications")).json()
    items = bells["items"] if isinstance(bells, dict) else bells
    assert any(i["kind"] == "hub.unreachable" and i["link"] == "/connections#genhub" for i in items)
    # Dải "Cần Sếp xử lý": nhãn nút theo vai trò.
    user = (await _user_of(owner_api))[0]
    [issue] = await health.active_issues(db, user.org_id)
    assert issue["kind"] == "hub.unreachable" and issue["action"] == "Mở thẻ Gen-hub"
    assert "Kết nối › Gen-hub" in issue["body"]
    [other] = await health.active_issues(db, user.org_id, is_owner=False)
    assert other["action"] == "Nhờ Owner xử lý" and "nhờ Owner kiểm tra ở Kết nối › Gen-hub" in other["body"]
    # Gọi được lại ⇒ tự đóng.
    fake_hub.mode = "ok"
    clock.now += 61
    assert (await owner_api.get(SEARCH, params={"q": "ổn rồi"})).status_code == 200
    [alert] = await _alerts()
    assert alert.cleared_at is not None and await _keys(redis) == []
    # Sự cố quay lại sau khi đã đóng ⇒ chuông mới.
    fake_hub.mode = "timeout"
    clock.now += 100
    await _open_breaker(owner_api, fake_hub)
    clock.now += 16 * 60
    assert await hub.breaker_watch(sm, redis) == 1 and await _bells() == 2


async def test_failure_after_15_minutes_raises_without_waiting_for_cron(owner_api: Api, fake_hub: FakeHub,
                                                                       clock: Clock, redis: Any) -> None:
    await _linked(owner_api)
    await _open_breaker(owner_api, fake_hub)
    clock.now = T0 + 16 * 60
    assert (await owner_api.get(SEARCH, params={"q": "vẫn lỗi"})).json()["code"] == "HUB_UNAVAILABLE"  # nửa mở, lỗi
    [alert] = await _alerts()
    assert alert.cleared_at is None and await _bells() == 1
    clock.now += 61
    assert (await owner_api.get(SEARCH, params={"q": "vẫn lỗi 2"})).json()["code"] == "HUB_UNAVAILABLE"
    assert await _bells() == 1  # cùng fingerprint ⇒ không chuông thứ hai


async def test_isolated_failure_then_outage_hours_later_no_false_bell(owner_api: Api, fake_hub: FakeHub, clock: Clock,
                                                                       redis: Any) -> None:
    """Một lỗi lẻ lúc 08:00 rồi im lặng (bộ đếm 5 phút hết hạn), 17:00 mới 3 lỗi liên tiếp ⇒ ngắt mạch mở nhưng đợt im
    mới được VÀI GIÂY — không được chuông "không trả lời hơn 15 phút" ngay (mốc down_since phải là 17:00, không
    08:00)."""
    await _linked(owner_api)
    org = (await _user_of(owner_api))[0].org_id
    sm = sessionmaker()
    fake_hub.mode = "timeout"
    assert (await owner_api.get(SEARCH, params={"q": "lẻ"})).json()["code"] == "HUB_UNAVAILABLE"
    assert float(await redis.get(hub._bk("down_since", org))) == T0
    # Chưa mở ngắt mạch ⇒ mốc chỉ sống cùng bộ đếm (5 phút), không treo 24 giờ.
    assert 0 < await redis.ttl(hub._bk("down_since", org)) <= 300
    # Đồng hồ Redis là giờ thật: giả lập 9 giờ trôi qua bằng cách để bộ đếm "hết hạn" (khoá down_since cũ — như bản
    # trước đây đặt 24 giờ — vẫn còn, để chắc chắn lỗi đầu của chuỗi mới GHI ĐÈ mốc cũ).
    await redis.delete(hub._bk("fails", org))
    await redis.set(hub._bk("down_since", org), repr(T0), ex=86400)
    clock.now = T0 + 9 * 3600
    await _open_breaker(owner_api, fake_hub)
    assert (await hub.breaker_state(redis, org))["open"] is True
    assert float(await redis.get(hub._bk("down_since", org))) == T0 + 9 * 3600
    assert await redis.ttl(hub._bk("down_since", org)) > 300  # đã mở ⇒ giữ mốc tới khi gọi lại được
    assert await _alerts() == [] and await _bells() == 0
    assert await hub.breaker_watch(sm, redis) == 0 and await _bells() == 0
    # Im thật 16 phút kể từ 17:00 ⇒ lúc đó mới chuông.
    clock.now = T0 + 9 * 3600 + 16 * 60
    assert await hub.breaker_watch(sm, redis) == 1 and await _bells() == 1


async def test_breaker_watch_clears_when_down_since_gone_and_ignores_unopened(owner_api: Api, fake_hub: FakeHub,
                                                                              clock: Clock, redis: Any) -> None:
    await _linked(owner_api)
    org = (await _user_of(owner_api))[0].org_id
    sm = sessionmaker()
    # Có down_since cũ nhưng CHƯA từng mở (không có half) ⇒ không báo.
    await redis.set(hub._bk("down_since", org), repr(T0 - 3600), ex=3600)
    assert await hub.breaker_watch(sm, redis, now=T0) == 0 and await _alerts() == []
    await redis.set(hub._bk("half", org), "1", ex=3600)
    assert await hub.breaker_watch(sm, redis, now=datetime.fromtimestamp(T0, UTC)) == 1  # nhận cả datetime
    assert (await _alerts())[0].cleared_at is None
    await redis.delete(hub._bk("down_since", org), hub._bk("half", org))  # khoá hết hạn/xoá ⇒ đóng
    assert await hub.breaker_watch(sm, redis) == 0
    assert (await _alerts())[0].cleared_at is not None
    assert await hub.breaker_watch(sm, None) == 0  # Redis None ⇒ tắt, không lỗi


async def test_redis_none_disables_breaker(owner_api: Api, fake_hub: FakeHub, db: Any) -> None:
    await _linked(owner_api)
    user, _ = await _user_of(owner_api)
    fake_hub.mode = "timeout"
    client = hub.client_for(fake_hub.transport())
    for _ in range(5):
        with pytest.raises(Exception) as e:
            await hub.call_hub(db, None, client, user=user, suffix="tasks_list", args={})
        assert getattr(e.value, "code", None) == "HUB_UNAVAILABLE"  # không bao giờ HUB_BREAKER_OPEN
    assert await hub.breaker_state(None, user.org_id) == {"open": False, "retry_in_s": None, "down_since": None}


# ─── 7. briefing_read: không bao giờ ném ────────────────────────────────────────────

NOW = datetime(2026, 10, 9, 0, 30, tzinfo=UTC)  # 07:30 giờ VN


async def _brief(redis: Any, org: Any, kind: str, fake: FakeHub | None = None) -> dict[str, Any]:
    transport = fake.transport() if fake is not None else None
    return await hub.briefing_read(sessionmaker(), redis, org_id=org, kind=kind, now=NOW, transport=transport)


async def test_briefing_read_off_when_not_linked(owner_api: Api, fake_hub: FakeHub, redis: Any) -> None:
    org = (await _user_of(owner_api))[0].org_id
    for kind, scope in (("calendar_today", "calendar"), ("mail_reply", "mail"), ("tasks_open", "tasks")):
        out = await _brief(redis, org, kind, fake_hub)
        assert out == {"state": "off", "items": [], "error_code": "HUB_LINK_OFF", "detail": out["detail"],
                       "scope": scope}
    assert fake_hub.requests == 0
    assert (await _brief(redis, org, "không có"))["state"] == "error"


async def test_briefing_read_missing_scope_and_breaker(owner_api: Api, fake_hub: FakeHub, clock: Clock,
                                                       redis: Any) -> None:
    fake_hub.drop = {"calendar_list_events", "gmail_search", "gmail_read_message"}
    await _linked(owner_api)
    org = (await _user_of(owner_api))[0].org_id
    cal = await _brief(redis, org, "calendar_today", fake_hub)
    assert cal["state"] == "missing_scope" and cal["scope"] == "calendar" and cal["items"] == []
    assert cal["error_code"] == "HUB_TOOL_MISSING" and "tick thêm quyền" in cal["detail"]
    mail = await _brief(redis, org, "mail_reply", fake_hub)
    assert mail["state"] == "missing_scope" and mail["scope"] == "mail"
    assert (await _brief(redis, org, "tasks_open", fake_hub))["state"] == "ok"
    # Breaker mở (từ lỗi mạng) ⇒ breaker_open, không gọi mạng.
    await _open_breaker(owner_api, fake_hub, path="/hub/google/drive/search?q=a")
    base = fake_hub.requests
    out = await _brief(redis, org, "mail_reply", fake_hub)
    assert out["state"] == "breaker_open" and out["error_code"] == "HUB_BREAKER_OPEN" and out["scope"] == "mail"
    assert "Gen-hub tạm không trả lời" in out["detail"]
    assert (await _brief(redis, org, "tasks_open", fake_hub))["state"] == "ok"  # đệm vẫn trả khi breaker mở
    assert fake_hub.requests == base


async def test_briefing_read_ok_items_masked_and_system_actor(owner_api: Api, fake_hub: FakeHub, redis: Any) -> None:
    await _linked(owner_api)
    org = (await _user_of(owner_api))[0].org_id
    fake_hub.calls.clear()
    cal = await _brief(redis, org, "calendar_today", fake_hub)
    assert cal["state"] == "ok" and cal["error_code"] is None and cal["scope"] == "calendar"
    assert [i["all_day"] for i in cal["items"]] == [False, True]
    assert all(set(i) == {"start", "all_day", "title"} for i in cal["items"])
    args = fake_hub.args[GG + "calendar_list_events"][-1]
    assert args == {"timeMin": "2026-10-09T00:00:00+07:00", "timeMax": "2026-10-09T23:59:59+07:00",
                    "maxResults": 10}
    mail = await _brief(redis, org, "mail_reply", fake_hub)
    assert mail["state"] == "ok" and mail["items"][0]["id"] == MAIL_ID
    assert set(mail["items"][0]) == {"id", "from", "subject", "date"}
    assert fake_hub.args[GG + "gmail_search"][-1] == {"query": hub.MAIL_REPLY_QUERY, "maxResults": 10}
    assert hub.MAIL_REPLY_QUERY == ("is:unread in:inbox newer_than:3d -category:promotions -category:social "
                                    "-category:updates")
    tasks = await _brief(redis, org, "tasks_open", fake_hub)
    assert tasks["state"] == "ok" and [t["id"] for t in tasks["items"]] == ["task-123456"]
    assert fake_hub.args[GG + "tasks_list"][-1] == {}
    body = orjson.dumps([cal, mail, tasks]).decode()
    for leaked in (PHONE, EMAIL, KEY, TOKEN, "snippet", "nội dung thư riêng tư"):
        assert leaked not in body, leaked
    # Đệm: lần hai cùng tham số không gọi thêm.
    n = len(fake_hub.calls)
    assert (await _brief(redis, org, "mail_reply", fake_hub))["items"] == mail["items"] and len(fake_hub.calls) == n
    # Action Log ghi actor hệ thống, không phải 'user'; mcp_calls chỉ siêu dữ liệu.
    rows = await _db_text("""SELECT actor_type, actor_id FROM ops.action_log
                             WHERE action = 'mcp.call_ok' AND actor_type = 'system'""")
    assert "system:gen.briefing" in rows
    user_rows = await _db_text("SELECT actor_type FROM ops.action_log WHERE action = 'mcp.call_ok'")
    assert user_rows.count("system") == 3 and "user" in user_rows  # 3 lần đọc của Bản tin + 1 lần Kiểm tra của Owner
    assert "Họp" not in await _db_text("SELECT result_summary FROM agent.mcp_calls")


async def test_briefing_read_plain_text_and_structured(owner_api: Api, fake_hub: FakeHub, redis: Any) -> None:
    await _linked(owner_api)
    org = (await _user_of(owner_api))[0].org_id
    fake_hub.style = "plain"
    cal = await _brief(redis, org, "calendar_today", fake_hub)
    assert cal["state"] == "ok" and [i["start"] for i in cal["items"]] == ["2026-10-09 09:00", ""]
    assert PHONE not in orjson.dumps(cal).decode()
    fake_hub.style = "structured"
    tasks = await _brief(redis, org, "tasks_open", fake_hub)
    assert tasks["state"] == "ok" and tasks["items"][0]["title"].startswith("Gọi anh Tuấn")


async def test_briefing_read_network_error_never_raises(owner_api: Api, fake_hub: FakeHub, clock: Clock,
                                                        redis: Any, caplog: Any) -> None:
    await _linked(owner_api)
    org = (await _user_of(owner_api))[0].org_id
    for mode in ("timeout", "echo", "401"):
        fake_hub.mode = mode
        out = await _brief(redis, org, "tasks_open", fake_hub)
        assert out["state"] == "error" and out["items"] == [] and out["error_code"] == "HUB_UNAVAILABLE", mode
        assert out["detail"] and len(out["detail"]) <= 200 and TOKEN not in out["detail"] and TOKEN not in str(out)
    assert TOKEN not in caplog.text
    # Hỏng ở tầng phiên CSDL ⇒ vẫn trả state error, không ném.

    class Boom:
        def __call__(self) -> Any:
            raise RuntimeError(f"hỏng {TOKEN}")

    out = await hub.briefing_read(Boom(), redis, org_id=org, kind="tasks_open", now=NOW)
    assert out["state"] == "error" and out["error_code"] == "HUB_ERROR" and TOKEN not in str(out)


async def test_briefing_read_needs_an_owner(owner_api: Api, fake_hub: FakeHub, redis: Any) -> None:
    await _linked(owner_api)
    org = (await _user_of(owner_api))[0].org_id
    async with admin_sessionmaker()() as adm:
        await adm.execute(text("UPDATE core.users SET is_active = false"))
        await adm.commit()
    fake_hub.calls.clear()
    out = await _brief(redis, org, "tasks_open", fake_hub)
    assert out["state"] == "off" and out["error_code"] == "HUB_NO_OWNER" and fake_hub.calls == []


# ─── 8. Worker, nhãn sức khoẻ, GET /hub/link ──────────────────────────────────────────

def test_worker_has_hub_breaker_watch_cron() -> None:
    crons = {c.name.removeprefix("cron:"): c for c in WorkerSettings.cron_jobs}
    assert crons["hub_breaker_watch"].minute == set(range(0, 60, 5))
    assert JOB_LABELS["hub_breaker_watch"] == "Theo dõi Gen-hub"
    assert any(f.__name__ == "hub_breaker_watch" for f in WorkerSettings.functions)


def test_health_labels() -> None:
    assert health.ACTIONS["hub.unreachable"] == "Mở thẻ Gen-hub"
    assert health.NON_OWNER_ACTIONS["hub.unreachable"] == "Nhờ Owner xử lý"
    assert health.NON_OWNER_KIND_BODIES["hub.unreachable"] == (
        "Gen-hub của Owner tạm không trả lời — nhờ Owner kiểm tra ở Kết nối › Gen-hub.")


async def test_hub_link_has_read_scopes_and_breaker(owner_api: Api, fake_hub: FakeHub, client: Any, db: Any,
                                                    redis: Any) -> None:
    off = (await owner_api.get("/hub/link")).json()
    assert off["read_scopes"] is None  # chưa nối ⇒ "Chưa kiểm", không phải 4 quyền False
    assert off["breaker"] == {"open": False, "retry_in_s": None, "down_since": None}
    await _linked(owner_api)
    own = (await owner_api.get("/hub/link")).json()
    assert own["read_scopes"] == {"calendar": True, "mail": True, "tasks": True, "drive": True}
    assert set(own["breaker"]) == {"open", "retry_in_s", "down_since"}
    auditor = await login_as(client, db, "auditor")  # system.read: xem trạng thái nhưng không phải Owner
    try:
        other = (await auditor.get("/hub/link")).json()
        assert other["read_scopes"] == own["read_scopes"] and other["breaker"] == {"open": False}
    finally:
        await auditor.c.aclose()
