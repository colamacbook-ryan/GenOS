"""v0.1.23 (Đợt B6) — /notifications: chuông thông báo, chỉ đọc/đánh dấu của chính mình, WS chỉ tới người nhận."""

import uuid
from typing import Any

import httpx
import orjson
from sqlalchemy import text

from gh import notifications, realtime
from gh.auth import service
from gh.db import admin_sessionmaker
from tests.conftest import OWNER, Api


async def _pin(api: Api) -> None:
    assert (await api.send("POST", "/auth/pin/verify", {"pin": OWNER["pin"]})).status_code == 200


async def _count_log(action: str) -> int:
    async with admin_sessionmaker()() as db:
        n: int = (await db.execute(text("SELECT count(*) FROM ops.action_log WHERE action = :a"),
                                   {"a": action})).scalar_one()
    return n


async def test_empty_then_role_change_notifies_target_only(owner_api: Api, app: Any) -> None:
    r = await owner_api.get("/notifications")
    assert r.status_code == 200, r.text
    assert r.json() == {"items": [], "unread": 0, "total": 0}

    await _pin(owner_api)
    r = await owner_api.send("POST", "/users", {"display_name": "Lan", "email": "lan@example.vn", "role": "operator"})
    assert r.status_code == 201, r.text
    lan_id, temp = r.json()["user"]["id"], r.json()["temp_password"]
    r = await owner_api.send("PATCH", f"/users/{lan_id}/role", {"role": "manager"})
    assert r.status_code == 200, r.text
    r = await owner_api.send("POST", f"/users/{lan_id}/reset-password")
    assert r.status_code == 200, r.text
    temp = r.json()["temp_password"]

    # Owner (người thao tác) không nhận thông báo của Lan.
    assert (await owner_api.get("/notifications")).json()["unread"] == 0

    c = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")
    try:
        lan = Api(c)
        assert (await lan.send("POST", "/auth/login", {"email": "lan@example.vn", "password": temp})).status_code == 200
        assert (await lan.send("POST", "/account/password", {"current_password": temp,
                                                             "new_password": "mat-khau-moi-cua-lan-1"})).status_code \
            in (200, 204)
        body = (await lan.get("/notifications")).json()
        assert body["unread"] == 2
        kinds = [x["kind"] for x in body["items"]]
        assert kinds == ["user.password_reset", "user.role_changed"]  # mới nhất trước
        role_item = body["items"][1]
        assert role_item["read"] is False and "Quản lý" in role_item["body"]
        assert OWNER["display_name"] in role_item["body"]
        assert role_item["link"] == "/account"

        before = await _count_log("http.post")
        r = await lan.send("POST", "/notifications/read", {"ids": [role_item["id"]]})
        assert r.status_code == 200 and r.json() == {"unread": 1}
        # id của người khác / không tồn tại: không lỗi, không đổi gì.
        r = await lan.send("POST", "/notifications/read", {"ids": [str(uuid.uuid4())]})
        assert r.json() == {"unread": 1}
        r = await lan.send("POST", "/notifications/read", {})
        assert r.json() == {"unread": 0}
        assert all(x["read"] for x in (await lan.get("/notifications")).json()["items"])
        # Đánh dấu đã đọc là thao tác riêng tư — không ghi dòng http.post chung vào Nhật ký.
        assert await _count_log("http.post") == before
    finally:
        await c.aclose()


async def test_needs_login(client: httpx.AsyncClient, owner_api: Api) -> None:
    anon = httpx.AsyncClient(transport=client._transport, base_url="http://test")  # noqa: SLF001
    try:
        assert (await anon.get("/api/v1/notifications")).status_code == 401
    finally:
        await anon.aclose()


async def test_notify_helper_owners_and_limit(owner_api: Api) -> None:
    async with admin_sessionmaker()() as db:
        org = (await db.execute(text("SELECT id FROM core.organizations"))).scalar_one()
        owners = await notifications.owner_ids(db, org)
        assert len(owners) == 1
        for i in range(3):
            await notifications.notify(db, org, owners + owners, kind="backup.done", title=f"Sao lưu {i}",
                                       body="x" * 5000, link="/system?tab=storage")
        await db.commit()
    body = (await owner_api.get("/notifications", params={"limit": 2})).json()
    assert body["unread"] == 3  # trùng người nhận trong một lần gọi chỉ ghi một dòng
    assert [x["title"] for x in body["items"]] == ["Sao lưu 2", "Sao lưu 1"]
    assert len(body["items"][0]["body"]) == notifications.MAX_BODY
    assert (await owner_api.get("/notifications", params={"limit": 0})).status_code == 422


class FakeWs:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send_text(self, t: str) -> None:
        self.sent.append(t)


async def test_ws_notification_only_to_recipient() -> None:
    hub = realtime.Hub(None)  # type: ignore[arg-type]
    org = uuid.uuid4()

    def user(uid: uuid.UUID) -> service.CurrentUser:
        return service.CurrentUser(id=uid, org_id=org, email="", display_name="", role_code="operator", role_name="",
                                   role_id=uuid.uuid4(), team_id=None, session_id=uuid.uuid4(),
                                   pin_verified_until=None, addressing={}, permissions={})

    a, b = uuid.uuid4(), uuid.uuid4()
    wa, wb = FakeWs(), FakeWs()
    hub.clients = {wa: user(a), wb: user(b)}  # type: ignore[dict-item]
    await hub.dispatch({"type": notifications.EVENT, "data": {"id": "1"}, "org_id": str(org), "to_user": str(a)})
    assert len(wa.sent) == 1 and wb.sent == []
    assert orjson.loads(wa.sent[0])["type"] == "notification.new"
    await hub.dispatch({"type": notifications.EVENT, "data": {}, "org_id": str(org), "to_user": None})
    assert len(wa.sent) == 1 and wb.sent == []  # thiếu người nhận → bỏ, không phát cả tổ chức


class FakeRedis:
    def __init__(self) -> None:
        self.published: list[dict[str, Any]] = []

    async def publish(self, channel: str, payload: bytes) -> None:
        self.published.append(orjson.loads(payload))


async def test_ws_publish_only_after_commit(owner_api: Api) -> None:
    import asyncio

    redis = FakeRedis()
    async with admin_sessionmaker()() as db:
        org = (await db.execute(text("SELECT id FROM core.organizations"))).scalar_one()
        owners = await notifications.owner_ids(db, org)
        await notifications.notify(db, org, owners, kind="backup.done", title="Rollback", redis=redis)
        await db.rollback()
        await asyncio.sleep(0.05)
        assert redis.published == []  # rollback → không đẩy
        await notifications.notify(db, org, owners, kind="backup.done", title="Commit", redis=redis)
        await asyncio.sleep(0.05)
        assert redis.published == []  # chưa commit → chưa đẩy
        await db.commit()
        await asyncio.sleep(0.05)
    assert [m["data"]["title"] for m in redis.published] == ["Commit"]
    assert redis.published[0]["to_user"] == str(owners[0])
    assert [x["title"] for x in (await owner_api.get("/notifications")).json()["items"]] == ["Commit"]
