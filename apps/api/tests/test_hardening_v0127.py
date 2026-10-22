"""v0.1.27 — gia cố sau review: ghim DNS cho Gen-hub, `last_error` chỉ Owner, `/mcp/tools/{id}/call` không lộ
Kho, số liệu lọc đầu theo phạm vi `queue.read`, trần so trùng 3000, hạn lưu chuông, nhắc việc chịu lỗi từng dòng."""

import asyncio
import socket
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import orjson
import pytest
from sqlalchemy import text

from gh import notifications
from gh.biz.queue.jobs import due_reminders
from gh.chassis import mcp_client
from gh.chassis.mcp_client import McpBlockedNetwork, McpClient, pin_endpoint
from gh.db import sessionmaker
from gh.hub_link import service as hub
from gh.refinery import triage
from tests.conftest import Api
from tests.phase2 import org_id
from tests.test_actionlog_db import _set_scope
from tests.test_gen_proposals import _me, _soon
from tests.test_hub_link import PREFIX, TOKEN, FakeHub, _linked
from tests.test_rbac_api import login_as
from tests.test_triage import LONG, _person, _unit


@pytest.fixture
def hub_fake(app: Any) -> FakeHub:
    h = FakeHub()
    app.state.mcp_transport = h.transport()
    return h


class _Server:
    def __init__(self, endpoint: str, allow_public_network: bool = False) -> None:
        self.transport, self.endpoint, self.allow_public_network = "streamable_http", endpoint, allow_public_network


def _fake_resolver(monkeypatch: pytest.MonkeyPatch, answers: list[list[str]]) -> list[str]:
    """Mỗi lần phân giải trả phần tử kế tiếp của `answers` (giả DNS rebinding: lần sau đổi IP)."""
    asked: list[str] = []

    async def fake(host: str, port: Any) -> list[Any]:
        asked.append(host)
        ips = answers[min(len(asked) - 1, len(answers) - 1)]
        return [(socket.AF_INET6 if ":" in ip else socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port))
                for ip in ips]

    # v0.1.45: `pin_endpoint` phân giải qua `mcp_client._getaddrinfo` (conftest thay mặc định bằng DNS giả).
    monkeypatch.setattr(mcp_client, "_getaddrinfo", fake)
    return asked


# ─── 1a: ghim DNS ─────────────────────────────────────────────────────────────

async def test_pin_endpoint_rules(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_resolver(monkeypatch, [["10.1.2.3"]])
    t = await pin_endpoint("https://hub.noi-bo.vn:8443/mcp?x=1", False)
    assert t.url == "https://10.1.2.3:8443/mcp?x=1" and t.host == "hub.noi-bo.vn:8443" and t.sni == "hub.noi-bo.vn"
    lit = await pin_endpoint("http://127.0.0.1:9911/mcp", False)
    assert lit.url == "http://127.0.0.1:9911/mcp" and lit.sni is None
    v6 = await pin_endpoint("http://[::1]:9911/mcp", False)
    assert v6.url == "http://[::1]:9911/mcp"
    for bad in ("http://169.254.169.254/latest", "http://0.0.0.0/mcp", "http://[fe80::1]/mcp",
                "http://[::ffff:169.254.169.254]/mcp"):
        with pytest.raises(McpBlockedNetwork):
            await pin_endpoint(bad, True)
    _fake_resolver(monkeypatch, [["93.184.216.34"]])
    with pytest.raises(McpBlockedNetwork, match="mạng công cộng"):
        await pin_endpoint("https://hub.example.test/mcp", False)
    assert (await pin_endpoint("https://hub.example.test/mcp", True)).url == "https://93.184.216.34/mcp"
    # Một trong các IP là link-local → chặn cả (không chọn IP "đẹp" rồi để lần sau trúng IP xấu).
    _fake_resolver(monkeypatch, [["10.0.0.5", "169.254.169.254"]])
    with pytest.raises(McpBlockedNetwork, match="link-local"):
        await pin_endpoint("http://hub.noi-bo.vn/mcp", True)


async def test_pinned_client_connects_to_checked_ip_not_rebound(monkeypatch: pytest.MonkeyPatch) -> None:
    """DNS rebinding: lần kiểm trả IP nội bộ, lần sau trả 169.254.169.254 — mỗi lời gọi chỉ phân giải MỘT lần và
    kết nối đúng IP vừa kiểm; lời gọi sau (IP xấu) bị chặn TRƯỚC khi có request nào ra ngoài."""
    asked = _fake_resolver(monkeypatch, [["10.0.0.5"], ["169.254.169.254"]])
    seen: list[httpx.Request] = []

    def handle(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": "gh-1", "result": {"ok": True}})

    client = McpClient(transport=httpx.MockTransport(handle), pin_dns=True)
    server = _Server("https://hub.noi-bo.vn/mcp")
    assert await client.call_tool(server, "kho_tom_tat", {}, TOKEN) == {"ok": True}
    assert len(asked) == 1 and len(seen) == 1
    req = seen[0]
    assert req.url.host == "10.0.0.5" and req.headers["host"] == "hub.noi-bo.vn"
    assert req.extensions.get("sni_hostname") == "hub.noi-bo.vn"
    with pytest.raises(McpBlockedNetwork):
        await client.call_tool(server, "kho_tom_tat", {}, TOKEN)
    assert len(seen) == 1
    # v0.1.45 (F-49): mọi McpClient mặc định ghim DNS; client của liên kết Gen-hub luôn ghim.
    assert McpClient()._pin is True and hub.client_for(None)._pin is True


# ─── 1b: last_error chỉ Owner ─────────────────────────────────────────────────

async def test_last_error_owner_only(owner_api: Api, hub_fake: FakeHub, client: httpx.AsyncClient,
                                     db: Any) -> None:
    await _linked(owner_api)
    hub_fake.mode = "429"
    assert (await owner_api.get("/hub/kho/search", params={"q": "x"})).status_code == 409
    own = (await owner_api.get("/hub/link")).json()
    assert own["last_error"].startswith("429") and own["status"] == "error"
    auditor = await login_as(client, db, "auditor")
    try:
        other = (await auditor.get("/hub/link")).json()
        assert other["status"] == "error" and other["last_error"] == hub.LAST_ERROR_HIDDEN
        hub_fake.mode = "ok"
        assert (await owner_api.get("/hub/kho/search", params={"q": "y"})).status_code == 200
        assert (await auditor.get("/hub/link")).json()["last_error"] is None
    finally:
        await auditor.c.aclose()


# ─── 1d: route MCP chung không lộ Kho ─────────────────────────────────────────

async def test_generic_call_on_hub_server_owner_only_and_masked(owner_api: Api, hub_fake: FakeHub,
                                                                client: httpx.AsyncClient, db: Any) -> None:
    await _linked(owner_api)
    tid = (await db.execute(text("SELECT id FROM agent.mcp_tools WHERE name = :n"),
                            {"n": PREFIX + "kho_tom_tat"})).scalar_one()
    hub_fake.calls.clear()
    r = await owner_api.send("POST", f"/mcp/tools/{tid}/call", {"agent_key": "core.gen", "args": {}})
    assert r.status_code == 200, r.text
    body = r.text
    for leaked in ("0912 345 678", "tuan.nguyen@example.com", "sk-abcdefghij", "190312345678901", TOKEN):
        assert leaked not in body, leaked
    assert "VIEC-3" in body and "nội dung không lưu" in r.json()["call"]["result_summary"]
    assert hub_fake.calls == [PREFIX + "kho_tom_tat"]
    # Vai trò khác có system.manage (tuỳ biến) vẫn không gọi được máy chủ Gen-hub qua route chung.
    await _set_scope(db, "auditor", "system.manage", "all")
    await db.commit()
    auditor = await login_as(client, db, "auditor")
    try:
        r = await auditor.send("POST", f"/mcp/tools/{tid}/call", {"agent_key": "core.gen", "args": {}})
        assert r.status_code == 403 and r.json()["code"] == "HUB_OWNER_ONLY"
        assert "VIEC-3" not in r.text
    finally:
        await auditor.c.aclose()
    assert hub_fake.calls == [PREFIX + "kho_tom_tat"]
    rows = (await db.execute(text("SELECT outcome, result_summary FROM agent.mcp_calls ORDER BY at"))).all()
    assert rows[-1].outcome == "blocked" and "chỉ Owner" in rows[-1].result_summary
    assert all("VIEC-3" not in (x.result_summary or "") for x in rows)


# ─── 1c: số liệu lọc đầu theo phạm vi queue.read ──────────────────────────────

async def test_triage_summary_respects_queue_scope(owner_api: Api, client: httpx.AsyncClient, db: Any) -> None:
    org = await org_id(db)
    p1, p2 = await _person(db, org, "Chị Lan"), await _person(db, org, "Anh Bình")
    await _unit(db, org, LONG, person_id=p1, minutes_ago=30)
    await _unit(db, org, LONG, person_id=p2, minutes_ago=20)
    await _unit(db, org, "KHUYẾN MÃI SỐC!!! click ngay http://abc.xyz www.win.top", person_id=p2,
                event_type="OfferedSupply", minutes_ago=10)
    await db.commit()
    await triage.run_org(sessionmaker(), org)
    full = (await owner_api.get("/refinery/triage/summary")).json()
    assert full["scope"] == "all" and full["total"] == 3 and full["duplicates"] == 1
    staff = await login_as(client, db, "agent_staff")
    try:
        mine = (await staff.get("/refinery/triage/summary")).json()
        assert mine["scope"] == "assigned" and mine["total"] == 0 and mine["duplicates"] == 0
        # Được phân khách p2 → chỉ đếm mục của p2 (bản trùng + rác), không đếm mục của p1.
        uid = (await db.execute(text("SELECT id FROM core.users WHERE email = 'agent_staff@example.vn'"))).scalar_one()
        await db.execute(text("UPDATE core.persons SET owner_user_id = :u WHERE id = :p"), {"u": uid, "p": p2})
        await db.commit()
        mine = (await staff.get("/refinery/triage/summary")).json()
        assert mine["total"] == 2 and mine["duplicates"] == 1 and mine["spam"] == 1
    finally:
        await staff.c.aclose()


# ─── 3: trần so trùng — trùng y hệt không bị trần giới hạn ────────────────────

async def test_exact_duplicate_found_beyond_candidate_cap(owner_api: Api, db: Any,
                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    org = await org_id(db)
    p = await _person(db, org, "Khách cũ")
    orig = await _unit(db, org, LONG, person_id=p, minutes_ago=600)
    for i in range(6):
        await _unit(db, org, f"Tin khác số {i} về lịch giao hàng tuần sau nhé anh chị ơi, cảm ơn nhiều", person_id=p,
                    minutes_ago=500 - i)
    await db.commit()
    assert await triage.run_org(sessionmaker(), org) == 7
    monkeypatch.setattr(triage, "CANDIDATES_LIMIT", 3)  # 3 mục mới nhất không chứa bản gốc
    late = await _unit(db, org, LONG, person_id=p, minutes_ago=1)
    await db.commit()
    assert await triage.run_org(sessionmaker(), org) == 1
    m = (await db.execute(text("SELECT duplicate_of, duplicate_kind FROM refinery.item_marks WHERE item_id = :i"),
                          {"i": late})).one()
    assert m.duplicate_of == orig and m.duplicate_kind == "exact"


# ─── 2: hạn lưu thông báo + nhắc việc chịu lỗi từng dòng ──────────────────────

async def test_purge_notifications_retention(owner_api: Api, db: Any) -> None:
    org = await org_id(db)
    uid = uuid.UUID((await _me(owner_api))["id"])
    now = datetime.now(UTC)
    cases = {"read_31": (31, True), "read_10": (10, True), "unread_60": (60, False), "unread_91": (91, False),
             "read_91": (91, True)}
    for title, (days, read) in cases.items():
        await db.execute(text("""
            INSERT INTO core.notifications (org_id, user_id, kind, title, created_at, read_at)
            VALUES (:o, :u, 'test', :t, :c, :r)"""),
            {"o": org, "u": uid, "t": title, "c": now - timedelta(days=days), "r": now if read else None})
    await db.commit()
    async with sessionmaker()() as s:
        assert await notifications.purge_old(s, batch=1) == 3  # xoá theo lô nhỏ vẫn hết
        await s.commit()
    left = set((await db.execute(text("SELECT title FROM core.notifications WHERE kind = 'test'"))).scalars().all())
    assert left == {"read_10", "unread_60"}


async def test_reminder_bad_row_does_not_block_batch(owner_api: Api, monkeypatch: pytest.MonkeyPatch) -> None:
    me = await _me(owner_api)
    for title in ("Việc A", "Hỏng dữ liệu", "Việc C"):
        r = await owner_api.send("POST", "/tasks", {"title": title, "assignee_user_id": me["id"],
                                                   "remind_at": _soon(-5)})
        assert r.status_code in (200, 201), r.text
    real = notifications.notify

    async def flaky(db: Any, org: Any, to: Any, **kw: Any) -> Any:
        if "Hỏng" in kw["title"]:
            await db.execute(text("SELECT 1 / 0"))  # lỗi CSDL thật → transaction hỏng nếu không có savepoint
        return await real(db, org, to, **kw)

    monkeypatch.setattr(notifications, "notify", flaky)
    async with sessionmaker()() as db:
        assert await due_reminders(db) == 2
        await db.commit()
    async with sessionmaker()() as db:
        assert await due_reminders(db) == 0  # dòng lỗi không lặp mỗi phút
    items = (await owner_api.get("/notifications")).json()["items"]
    titles = {i["title"] for i in items if i["kind"] == "task.reminder"}
    assert titles == {"Nhắc việc: Việc A", "Nhắc việc: Việc C"}
    assert orjson.dumps(items).decode().count("Hỏng") == 0


# ─── rà soát PR #30 ───────────────────────────────────────────────────────────

async def test_pin_mapped_ipv4_and_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    # `::ffff:8.8.8.8` là IP công cộng (Python 3.11 coi IPv4-mapped là "private") → vẫn bị công tắc chặn.
    with pytest.raises(McpBlockedNetwork, match="mạng công cộng"):
        await pin_endpoint("http://[::ffff:8.8.8.8]/mcp", False)
    _fake_resolver(monkeypatch, [["::ffff:93.184.216.34"]])
    with pytest.raises(McpBlockedNetwork, match="mạng công cộng"):
        await pin_endpoint("https://hub.example.test/mcp", False)
    with pytest.raises(McpBlockedNetwork):
        await pin_endpoint("http://hub.noi-bo.vn:99999/mcp", True)
    # IP đầu (vd AAAA trên máy không có IPv6) không kết nối được → thử IP kế đã kiểm, không phân giải lại.
    asked = _fake_resolver(monkeypatch, [["2606:4700::1", "104.16.0.1"]])
    seen: list[str] = []

    def handle(req: httpx.Request) -> httpx.Response:
        seen.append(req.url.host)
        if req.url.host == "2606:4700::1":
            raise httpx.ConnectError("no route", request=req)
        assert req.headers["host"] == "hub.example.test" and req.extensions.get("sni_hostname") == "hub.example.test"
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": "gh-1", "result": {"ok": True}})

    client = McpClient(transport=httpx.MockTransport(handle), pin_dns=True)
    assert await client.call_tool(_Server("https://hub.example.test/mcp", True), "t", {}, TOKEN) == {"ok": True}
    assert seen == ["2606:4700::1", "104.16.0.1"] and len(asked) == 1


async def test_generic_server_routes_cannot_touch_hub_server(owner_api: Api, hub_fake: FakeHub,
                                                             client: httpx.AsyncClient, db: Any) -> None:
    """Vai trò tuỳ biến có system.manage không được đổi endpoint / khám phá / xoá máy chủ Gen-hub (đổi endpoint rồi
    khám phá = gửi token Kho tới nơi khác)."""
    await _linked(owner_api)
    sid = (await owner_api.get("/hub/link")).json()["server_id"]
    await _set_scope(db, "auditor", "system.manage", "all")
    await db.commit()
    auditor = await login_as(client, db, "auditor")
    try:
        r = await auditor.send("PATCH", f"/mcp/servers/{sid}", {"endpoint": "https://evil.example/mcp"})
        assert r.status_code == 403 and r.json()["code"] == "HUB_OWNER_ONLY", r.text
        assert (await auditor.send("POST", f"/mcp/servers/{sid}/discover", {})).status_code == 403
        assert (await auditor.send("DELETE", f"/mcp/servers/{sid}")).status_code == 403
    finally:
        await auditor.c.aclose()
    assert (await owner_api.get("/hub/link")).json()["endpoint"] != "https://evil.example/mcp"
    n = (await db.execute(text("SELECT count(*) FROM ops.action_log WHERE action = 'mcp.server_blocked'"))).scalar()
    assert n == 3
    r = await owner_api.send("POST", f"/mcp/servers/{sid}/discover", {})
    assert r.status_code == 200, r.text


async def test_reminder_ws_events_only_after_outer_commit(owner_api: Api, monkeypatch: pytest.MonkeyPatch) -> None:
    """Savepoint RELEASE bắn after_commit (SQLAlchemy 2) — không được đẩy WS trước khi transaction ngoài commit; dòng
    lỗi phía sau không được xoá sự kiện của dòng trước đã thành công."""
    me = await _me(owner_api)
    for title in ("Việc A", "Hỏng dữ liệu", "Việc C"):
        await owner_api.send("POST", "/tasks", {"title": title, "assignee_user_id": me["id"], "remind_at": _soon(-5)})
    real = notifications.notify
    pushed: list[str] = []

    async def fake_publish(redis: Any, batch: Any) -> None:
        pushed.extend(it["title"] for _, _, _, it in batch)

    async def flaky(db: Any, org: Any, to: Any, **kw: Any) -> Any:
        if "Hỏng" in kw["title"]:
            await db.execute(text("SELECT 1 / 0"))
        return await real(db, org, to, **kw)

    monkeypatch.setattr(notifications, "notify", flaky)
    monkeypatch.setattr(notifications, "_publish_all", fake_publish)
    async with sessionmaker()() as db:
        assert await due_reminders(db, redis=object()) == 2
        await asyncio.sleep(0)
        assert pushed == []  # chưa commit → chưa đẩy
        await db.commit()
    for _ in range(5):
        await asyncio.sleep(0)
    assert sorted(pushed) == ["Nhắc việc: Việc A", "Nhắc việc: Việc C"]
