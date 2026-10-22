"""v0.1.49 (QD-16) — Gen ĐỌC lịch/mail/việc/Drive Google qua Gen-hub, tuyệt đối không ghi.

Gen-hub `/mcp` giả bằng `httpx.MockTransport` (sao mẫu tests/test_hub_link.py): tool Kho + tool Google đọc
(readOnlyHint) + tool Google ghi (readOnlyHint=false). Kiểm: Kiểm tra chỉ mở đúng tool đọc, thiếu quyền đọc không làm
ok=false, tham số ngày VN, che dữ liệu (id kỹ thuật giữ nguyên), đệm 5 phút, tool ghi bị từ chối kể cả Owner qua route
MCP chung, chỉ Owner, token không lộ, danh sách hậu tố + `keep_keys` của lớp che."""

import logging
from datetime import timedelta
from typing import Any

import httpx
import orjson
import pytest
from sqlalchemy import text

from gh.db import admin_sessionmaker
from gh.hub_link import service as hub
from tests.conftest import OWNER, Api
from tests.test_gen import _user_of
from tests.test_rbac_api import login_as

TOKEN = "ghtok_SieuBiMat_1234567890abcdef"
ENDPOINT = "http://127.0.0.1:9911/mcp"
KHO = "mcp-58450__"
GG = "mcp-46634__"
GOOGLE_READ = ("calendar_list_events", "tasks_list", "gmail_search", "gmail_read_message", "drive_search")
GOOGLE_WRITE = ("gmail_send", "calendar_create_event", "drive_create_file", "tasks_create", "docs_edit",
                "sheets_write")
MAIL_ID = "18c2f41234567890"
THREAD_ID = "18c2f40000000001"
DRIVE_ID = "1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgvE2upms"
PHONE, EMAIL, KEY = "0912 345 678", "tuan.nguyen@example.com", "sk-abcdefghij1234567890"
LEAKS = (PHONE, EMAIL, KEY, TOKEN, "190312345678901")
BODY = f"Anh Tuấn gọi {PHONE}, email {EMAIL}, khoá {KEY}. " + "Nội dung dài. " * 600


def payload_of(suffix: str) -> Any:
    if suffix == "calendar_list_events":
        return {"events": [
            {"id": "ev2026100912345678", "summary": f"Họp với anh Tuấn {PHONE}", "start": {"dateTime":
             "2026-10-09T09:00:00+07:00"}, "end": {"dateTime": "2026-10-09T10:00:00+07:00"},
             "attendees": [{"email": EMAIL}], "description": f"khoá {KEY}", "calendarId": EMAIL},
            {"id": "ev2", "summary": "Nghỉ lễ", "start": {"date": "2026-10-09"}}]}
    if suffix == "gmail_search":
        return {"messages": [{"id": MAIL_ID, "threadId": THREAD_ID, "from": f"Anh Tuấn <{EMAIL}>",
                              "subject": f"Báo giá gấp, gọi {PHONE}", "date": "Fri, 09 Oct 2026 08:15:00 +0700",
                              "snippet": f"nội dung thư riêng tư {KEY}"}]}
    if suffix == "gmail_read_message":
        return {"id": MAIL_ID, "threadId": THREAD_ID, "from": f"Anh Tuấn <{EMAIL}>", "subject": "Báo giá",
                "body": BODY}
    if suffix == "tasks_list":
        return {"items": [
            {"id": "task-123456", "title": f"Gọi anh Tuấn {PHONE}", "due": "2026-10-10T00:00:00.000Z",
             "status": "needsAction"},
            {"id": "task-done", "title": "Việc đã xong", "status": "completed"}]}
    if suffix == "drive_search":
        return {"files": [{"id": DRIVE_ID, "name": "Hợp đồng 190312345678901.pdf", "mimeType": "application/pdf"}]}
    return {"ok": True}


class FakeHub:
    """Gen-hub `/mcp` giả có thêm tool Google. `drop` = hậu tố Gen-hub không liệt kê (token thiếu quyền);
    `flag_write` = hậu tố bị Gen-hub đánh dấu GHI; `style` = hình dạng kết quả; `mode` giả lỗi."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.args: dict[str, list[dict[str, Any]]] = {}
        self.requests = 0
        self.mode = "ok"
        self.drop: set[str] = set()
        self.flag_write: set[str] = set()
        self.style = "text"  # text = content[].text là JSON · structured = structuredContent · plain = văn bản thường
        self.auth_seen: list[str | None] = []

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def wrap(self, suffix: str) -> dict[str, Any]:
        data = payload_of(suffix)
        if self.style == "structured":
            return {"content": [{"type": "text", "text": "ok"}], "structuredContent": data}
        if self.style == "plain":
            lines = {"calendar_list_events": [f"2026-10-09 09:00 Họp với anh Tuấn {PHONE}", "Nghỉ lễ"],
                     "gmail_search": [f"Báo giá gấp {EMAIL}"], "tasks_list": [f"- Gọi anh Tuấn {PHONE}"]}
            return {"content": [{"type": "text", "text": "\n".join(lines.get(suffix, ["ok"]))}]}
        return {"content": [{"type": "text", "text": orjson.dumps(data).decode()}]}

    def handle(self, req: httpx.Request) -> httpx.Response:
        self.requests += 1
        body = orjson.loads(req.content)
        self.auth_seen.append(req.headers.get("authorization"))
        if self.mode == "timeout":
            raise httpx.ReadTimeout("hết giờ", request=req)
        if req.headers.get("authorization") != f"Bearer {TOKEN}" or self.mode == "401":
            return httpx.Response(401, json={"error": "unauthorized"})
        if self.mode == "429":
            return httpx.Response(429, json={"error": "rate limited"})
        if self.mode == "500":
            return httpx.Response(503, text="Gen-hub bảo trì")
        if self.mode == "echo" and body["method"] == "tools/call":
            return httpx.Response(500, text=f"boom authorization={req.headers.get('authorization')}")
        if body["method"] == "tools/list":
            ro, rw = {"readOnlyHint": True}, {"readOnlyHint": False}
            tools = [{"name": KHO + n, "description": n, "inputSchema": {}, "annotations": ro}
                     for n in hub.KHO_READ_SUFFIXES]
            tools += [{"name": KHO + "kho_create", "inputSchema": {}, "annotations": rw}]
            tools += [{"name": GG + n, "description": n, "inputSchema": {},
                       "annotations": rw if n in self.flag_write else ro}
                      for n in GOOGLE_READ if n not in self.drop]
            tools += [{"name": GG + n, "inputSchema": {}, "annotations": rw} for n in GOOGLE_WRITE]
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": {"tools": tools}})
        name = body["params"]["name"]
        self.calls.append(name)
        self.args.setdefault(name, []).append(body["params"]["arguments"])
        suffix = name.rsplit("__", 1)[-1]
        if suffix.startswith("kho_"):
            result: Any = {"content": [{"type": "text", "text": "VIEC-3 Đang làm — hạn 2026-09-27"}]}
        else:
            result = self.wrap(suffix)
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": result})


@pytest.fixture
def fake_hub(app: Any) -> FakeHub:
    h = FakeHub()
    app.state.mcp_transport = h.transport()
    return h


async def _pin(api: Api) -> None:
    r = await api.send("POST", "/auth/pin/verify", {"pin": OWNER["pin"]})
    assert r.status_code == 200, r.text


async def _linked(api: Api) -> dict[str, Any]:
    await _pin(api)
    r = await api.send("PATCH", "/hub/link", {"endpoint": ENDPOINT, "token": TOKEN})
    assert r.status_code == 200, r.text
    r = await api.send("POST", "/hub/link/test", {})
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True, r.json()
    return r.json()  # type: ignore[no-any-return]


async def _db_text(sql: str) -> str:
    async with admin_sessionmaker()() as db:
        return orjson.dumps((await db.execute(text(sql))).all(), default=str).decode()


async def _tools(db: Any) -> dict[str, Any]:
    await db.rollback()
    rows = (await db.execute(text("""
        SELECT t.id, t.name, t.access, t.is_exposed, array_remove(array_agg(g.agent_key), NULL) AS grants
        FROM agent.mcp_tools t LEFT JOIN agent.mcp_grants g ON g.tool_id = t.id GROUP BY t.id"""))).all()
    return {r.name: r for r in rows}


# ─── 1. Kiểm tra: chỉ mở đúng tool đọc, KHÔNG gọi tool Google ────────────────────

async def test_test_exposes_google_read_tools_only(owner_api: Api, fake_hub: FakeHub, db: Any) -> None:
    out = await _linked(owner_api)
    by = await _tools(db)
    for n in GOOGLE_READ:
        assert by[GG + n].is_exposed is True and by[GG + n].grants == ["core.gen"], n
    for n in GOOGLE_WRITE:
        assert by[GG + n].is_exposed is False and by[GG + n].grants == [], n
    # v0.1.50 (F-81): kho_create (tool GHI Kho) được mở + cấp cho write_kho — không phải đường đọc, không bị gọi.
    assert by[KHO + "kho_create"].is_exposed is True and by[KHO + "kho_create"].grants == ["core.gen"]
    assert out["write_scopes"] == {"kho": False, "kho_create": True, "kho_update": False}
    assert out["write_missing"] == ["ghi Kho (kho_update)"]
    assert out["read_scopes"] == {"calendar": True, "mail": True, "tasks": True, "drive": True}
    assert out["read_missing"] == [] and out["write_tools"] == []
    assert fake_hub.calls == [KHO + "kho_tom_tat"]  # không gọi tool Google khi kiểm
    assert await hub.read_scopes(db, (await _user_of(owner_api))[0].org_id) == out["read_scopes"]


async def test_google_tool_flagged_write_is_listed_not_opened(owner_api: Api, fake_hub: FakeHub, db: Any) -> None:
    fake_hub.flag_write = {"drive_search"}
    out = await _linked(owner_api)
    by = await _tools(db)
    assert by[GG + "drive_search"].is_exposed is False and by[GG + "drive_search"].grants == []
    assert out["write_tools"] == [GG + "drive_search"]
    assert out["read_scopes"]["drive"] is False and out["read_missing"] == ["tìm tệp Drive"]
    assert out["ok"] is True


# ─── 2. Thiếu quyền đọc thêm không làm ok=false ─────────────────────────────────

async def test_missing_google_scopes_keep_ok_and_say_how_to_fix(owner_api: Api, fake_hub: FakeHub) -> None:
    fake_hub.drop = {"calendar_list_events", "gmail_search", "gmail_read_message"}
    out = await _linked(owner_api)
    assert out["ok"] is True and out["read_missing"] == ["đọc lịch", "đọc mail"]
    assert out["read_scopes"] == {"calendar": False, "mail": False, "tasks": True, "drive": True}
    assert out["link"]["status"] == "ok"
    fake_hub.calls.clear()
    before = fake_hub.requests
    r = await owner_api.get("/hub/google/calendar")
    assert r.status_code == 409 and r.json()["code"] == "HUB_TOOL_MISSING"
    assert "tick thêm quyền" in r.json()["title"] and "đọc lịch" in r.json()["title"]
    assert "Kết nối › Gen-hub" in r.json()["title"]
    r = await owner_api.get("/hub/google/mail/search", params={"q": "báo giá"})
    assert r.status_code == 409 and "đọc mail" in r.json()["title"]
    assert fake_hub.requests == before and fake_hub.calls == []  # thiếu quyền ⇒ không gọi ra ngoài
    assert (await owner_api.get("/hub/google/tasks")).status_code == 200  # quyền còn lại vẫn dùng được
    link = (await owner_api.get("/hub/link")).json()
    assert link["read_scopes"] == out["read_scopes"]


async def test_scope_revoked_in_gen_hub_is_closed_on_next_test(owner_api: Api, fake_hub: FakeHub, db: Any) -> None:
    await _linked(owner_api)
    assert (await owner_api.get("/hub/link")).json()["read_scopes"]["calendar"] is True
    fake_hub.drop = {"calendar_list_events"}  # Boss bỏ tick quyền lịch trong Gen-hub
    r = await owner_api.send("POST", "/hub/link/test", {})
    assert r.json()["ok"] is True and r.json()["read_missing"] == ["đọc lịch"]
    by = await _tools(db)
    assert by[GG + "calendar_list_events"].is_exposed is False and by[GG + "calendar_list_events"].grants == []
    assert (await owner_api.get("/hub/link")).json()["read_scopes"]["calendar"] is False
    r = await owner_api.get("/hub/google/calendar")
    assert r.status_code == 409 and r.json()["code"] == "HUB_TOOL_MISSING"
    assert "hub.link_tested" in await _db_text("SELECT action FROM ops.action_log")
    # Đọc TÊN khoá (không in cả detail: repr dòng SQLAlchemy cắt giữa giá trị dài, khoá có thể rơi vào đoạn bị cắt).
    keys = await _db_text(
        "SELECT DISTINCT jsonb_object_keys(detail) FROM ops.action_log WHERE action = 'hub.link_tested'")
    assert "read_scopes" in keys


async def test_link_read_scopes_null_until_green_check(owner_api: Api, fake_hub: FakeHub) -> None:
    """`GET /hub/link`: chưa nối / đã lưu mà chưa Kiểm tra xanh / vừa đổi token ⇒ `read_scopes: null` ("Chưa kiểm") —
    KHÔNG phải 4 quyền False (thẻ sẽ giục "Còn thiếu quyền… bấm Kiểm tra lại" khi Sếp còn chưa nối)."""
    assert (await owner_api.get("/hub/link")).json()["read_scopes"] is None
    await _pin(owner_api)
    r = await owner_api.send("PATCH", "/hub/link", {"endpoint": ENDPOINT, "token": TOKEN})
    assert r.status_code == 200, r.text
    assert (await owner_api.get("/hub/link")).json()["read_scopes"] is None   # đã lưu, chưa kiểm
    assert (await owner_api.send("POST", "/hub/link/test", {})).json()["ok"] is True
    full = {"calendar": True, "mail": True, "tasks": True, "drive": True}
    assert (await owner_api.get("/hub/link")).json()["read_scopes"] == full
    # Đổi token ⇒ liên kết tắt chờ kiểm lại ⇒ quyền cũ không còn được coi là đã kiểm.
    r = await owner_api.send("PATCH", "/hub/link", {"token": TOKEN})
    assert r.status_code == 200, r.text
    assert (await owner_api.get("/hub/link")).json()["read_scopes"] is None
    assert (await owner_api.send("POST", "/hub/link/test", {})).json()["ok"] is True
    assert (await owner_api.get("/hub/link")).json()["read_scopes"] == full


async def test_scope_revoked_clears_cache_even_if_test_fails(owner_api: Api, fake_hub: FakeHub, redis: Any) -> None:
    """Owner bỏ quyền lịch trong Gen-hub rồi bấm Kiểm tra mà lượt kiểm ĐỎ (Kho lỗi) ⇒ tool lịch đã bị thu hồi thì đệm
    5 phút cũng phải bỏ ngay — Gen không được đọc lịch cũ từ đệm nữa."""
    await _linked(owner_api)
    r = await owner_api.get("/hub/google/calendar")
    assert r.status_code == 200 and r.json()["cached"] is False
    assert (await owner_api.get("/hub/google/calendar")).json()["cached"] is True
    fake_hub.drop = {"calendar_list_events"}
    fake_hub.mode = "echo"  # tools/list được, tools/call kho_tom_tat lỗi 500 ⇒ Kiểm tra đỏ
    out = (await owner_api.send("POST", "/hub/link/test", {})).json()
    assert out["ok"] is False
    fake_hub.mode = "ok"
    r = await owner_api.get("/hub/google/calendar")
    assert r.status_code == 409 and r.json()["code"] == "HUB_TOOL_MISSING", r.text


# ─── 3. Đọc lịch/mail/việc/Drive: tham số, che, id giữ nguyên ──────────────────────

@pytest.mark.parametrize("style", ["text", "structured"])
async def test_google_reads_masked_and_ids_kept(owner_api: Api, fake_hub: FakeHub, style: str) -> None:
    fake_hub.style = style
    await _linked(owner_api)
    fake_hub.calls.clear()
    day = hub.vn_today()
    r = await owner_api.get("/hub/google/calendar")
    assert r.status_code == 200, r.text
    assert r.json()["source"] == "Lịch Google qua Gen-hub" and r.json()["tool"] == "calendar_list_events"
    assert fake_hub.args[GG + "calendar_list_events"][-1] == {
        "timeMin": f"{day.isoformat()}T00:00:00+07:00", "timeMax": f"{day.isoformat()}T23:59:59+07:00",
        "maxResults": 20}
    cal = r.text
    r = await owner_api.get("/hub/google/calendar", params={"day": "tomorrow"})
    nxt = day + timedelta(days=1)
    assert fake_hub.args[GG + "calendar_list_events"][-1]["timeMin"] == f"{nxt.isoformat()}T00:00:00+07:00"
    assert (await owner_api.get("/hub/google/calendar", params={"day": "yesterday"})).status_code == 422

    r = await owner_api.get("/hub/google/mail/search", params={"q": "báo giá", "limit": 5})
    assert r.status_code == 200 and r.json()["source"] == "Gmail qua Gen-hub"
    assert fake_hub.args[GG + "gmail_search"][-1] == {"query": "báo giá", "maxResults": 5}
    mail = r.text
    r = await owner_api.get("/hub/google/mail/message", params={"id": MAIL_ID})
    assert r.status_code == 200 and fake_hub.args[GG + "gmail_read_message"][-1] == {"messageId": MAIL_ID}
    msg = r.text
    r = await owner_api.get("/hub/google/tasks")
    assert r.status_code == 200 and r.json()["source"] == "Google Tasks qua Gen-hub"
    assert fake_hub.args[GG + "tasks_list"][-1] == {}
    tasks = r.text
    r = await owner_api.get("/hub/google/drive/search", params={"q": "hợp đồng"})
    assert r.status_code == 200 and r.json()["source"] == "Google Drive qua Gen-hub"
    assert fake_hub.args[GG + "drive_search"][-1] == {"query": "hợp đồng", "maxResults": 10}
    drive = r.text
    for body in (cal, mail, msg, tasks, drive):
        for leaked in LEAKS:
            assert leaked not in body, leaked
    assert MAIL_ID in mail and MAIL_ID in msg and THREAD_ID in mail  # id kỹ thuật giữ nguyên
    assert "ev2026100912345678" in cal and "task-123456" in tasks and DRIVE_ID in drive
    assert "t•••@example.com" in mail  # email vẫn che (kể cả calendarId dạng email)
    assert "a•••" not in cal and EMAIL.split("@")[0] not in cal


async def test_mail_message_validation_and_truncation(owner_api: Api, fake_hub: FakeHub) -> None:
    await _linked(owner_api)
    for bad in ("abc", "x" * 65, "18c2f4;DROP", "../etc"):
        r = await owner_api.get("/hub/google/mail/message", params={"id": bad})
        assert r.status_code == 422 and "id" in r.json()["errors"], bad
    assert (await owner_api.get("/hub/google/mail/message")).status_code == 422
    r = await owner_api.get("/hub/google/mail/message", params={"id": MAIL_ID})
    body = r.json()["data"]["content"][0]["text"]
    assert 0 < len(body) <= 4000 and "Nội dung dài" in body and PHONE not in body
    # đệm lưu bản đầy đủ đã che; lần hai cũng cắt ≤ 4000
    r2 = await owner_api.get("/hub/google/mail/message", params={"id": MAIL_ID})
    assert r2.json()["cached"] is True and len(r2.json()["data"]["content"][0]["text"]) <= 4000
    assert (await owner_api.get("/hub/google/mail/search", params={"q": "x", "limit": 11})).status_code == 422
    assert (await owner_api.get("/hub/google/mail/search", params={"q": "   "})).status_code == 422
    assert (await owner_api.get("/hub/google/drive/search", params={"q": "x" * 201})).status_code == 422


# ─── 4. Đệm 5 phút ──────────────────────────────────────────────────────────────

async def test_google_cache_five_minutes(owner_api: Api, fake_hub: FakeHub, redis: Any) -> None:
    await _linked(owner_api)
    fake_hub.calls.clear()
    r1 = await owner_api.get("/hub/google/mail/search", params={"q": "báo giá"})
    r2 = await owner_api.get("/hub/google/mail/search", params={"q": "báo giá"})
    assert r1.json()["cached"] is False and r2.json()["cached"] is True and r1.json()["data"] == r2.json()["data"]
    assert fake_hub.calls == [GG + "gmail_search"]
    await owner_api.get("/hub/google/mail/search", params={"q": "hợp đồng"})
    assert fake_hub.calls == [GG + "gmail_search"] * 2
    org = (await _user_of(owner_api))[0].org_id
    key = hub.cache_key(org, "gmail_search", {"query": "báo giá", "maxResults": 10})
    assert 0 < await redis.ttl(key) <= 300
    assert hub.CACHE_TTL_S == 300
    # Đệm chỉ chứa bản đã che.
    assert PHONE not in (await redis.get(key)).decode() and EMAIL not in (await redis.get(key)).decode()
    # Đổi token → xoá đệm, liên kết tắt tới khi Kiểm tra lại.
    await _pin(owner_api)
    await owner_api.send("PATCH", "/hub/link", {"token": TOKEN})
    assert [k async for k in redis.scan_iter(match="gh:hub:kho:*")] == []


# ─── 5. Tool ghi bị từ chối — kể cả Owner, kể cả route MCP chung ────────────────────

async def test_write_tools_denied_everywhere(owner_api: Api, fake_hub: FakeHub, db: Any, redis: Any) -> None:
    await _linked(owner_api)
    user, _ = await _user_of(owner_api)
    client = hub.client_for(fake_hub.transport())
    for suffix in hub.WRITE_SUFFIXES_DENY:
        with pytest.raises(Exception) as e:
            await hub.call_hub(db, redis, client, user=user, suffix=suffix, args={})
        assert getattr(e.value, "code", None) == "HUB_TOOL_NOT_ALLOWED", suffix
    # Ép mở + cấp tool ghi trong CSDL (như Owner lỡ tay) — vẫn bị chặn ở route chung, không tạo bản nháp.
    by = await _tools(db)
    for n in ("gmail_send", "calendar_create_event", "drive_create_file"):
        await db.execute(text("UPDATE agent.mcp_tools SET is_exposed = true WHERE id = :i"), {"i": by[GG + n].id})
        await db.execute(text("INSERT INTO agent.mcp_grants (tool_id, agent_key) VALUES (:i, 'core.gen')"),
                         {"i": by[GG + n].id})
    await db.commit()
    fake_hub.calls.clear()
    for access in ("write", "read"):  # tool ghi và cả tool bị đổi nhãn thành "đọc"
        for n in ("gmail_send", "calendar_create_event", "drive_create_file"):
            await db.execute(text("UPDATE agent.mcp_tools SET access = :a WHERE id = :i"),
                             {"a": access, "i": by[GG + n].id})
        await db.commit()
        for n in ("gmail_send", "calendar_create_event", "drive_create_file"):
            r = await owner_api.send("POST", f"/mcp/tools/{by[GG + n].id}/call",
                                     {"agent_key": "core.gen", "args": {"to": "a@b.com", "body": "x"}})
            assert r.status_code == 403 and r.json()["code"] == "HUB_TOOL_NOT_ALLOWED", (access, n, r.text)
            assert "chỉ đọc qua Gen-hub" in r.json()["detail"]
    assert fake_hub.calls == [] and not any(c.rsplit("__", 1)[-1] in hub.WRITE_SUFFIXES_DENY for c in fake_hub.calls)
    n_drafts = (await db.execute(text("SELECT count(*) FROM biz.action_drafts WHERE kind = 'mcp_write'"))).scalar_one()
    assert n_drafts == 0
    blocked = await _db_text("SELECT outcome FROM agent.mcp_calls WHERE outcome = 'blocked'")
    assert blocked.count("blocked") == 6
    log = await _db_text("SELECT detail FROM ops.action_log WHERE action = 'mcp.call_blocked'")
    assert "HUB_TOOL_NOT_ALLOWED" in log
    # Tool đọc hợp lệ qua route chung vẫn đi đúng đường (đã che, siêu dữ liệu).
    r = await owner_api.send("POST", f"/mcp/tools/{by[GG + 'gmail_search'].id}/call",
                             {"agent_key": "core.gen", "args": {"query": "x"}})
    assert r.status_code == 200, r.text
    for leaked in LEAKS:
        assert leaked not in r.text
    assert MAIL_ID in r.text and "nội dung không lưu" in r.json()["call"]["result_summary"]


# ─── 6. Chỉ Owner ───────────────────────────────────────────────────────────────

GOOGLE_PATHS = ("/hub/google/calendar", "/hub/google/tasks", "/hub/google/mail/search?q=a",
                f"/hub/google/mail/message?id={MAIL_ID}", "/hub/google/drive/search?q=a")


@pytest.mark.parametrize("role", ["manager", "operator", "auditor", "agent_staff"])
async def test_google_reads_owner_only(owner_api: Api, fake_hub: FakeHub, client: httpx.AsyncClient, db: Any,
                                       role: str) -> None:
    await _linked(owner_api)
    fake_hub.calls.clear()
    other = await login_as(client, db, role)
    try:
        for path in GOOGLE_PATHS:
            assert (await other.get(path)).status_code == 403, (role, path)
        if role == "auditor":  # system.read: xem trạng thái, thấy quyền đọc; ngắt mạch chỉ {open}
            link = (await other.get("/hub/link")).json()
            assert set(link["read_scopes"]) == {"calendar", "mail", "tasks", "drive"}
            assert link["breaker"] == {"open": False}
            assert set((await owner_api.get("/hub/link")).json()["breaker"]) == {"open", "retry_in_s", "down_since"}
    finally:
        await other.c.aclose()
    assert fake_hub.calls == []


# ─── 7. Token không lộ ở đâu cả; mcp_calls chỉ siêu dữ liệu ─────────────────────────

async def test_token_and_mail_content_never_stored(owner_api: Api, fake_hub: FakeHub, caplog: Any) -> None:
    caplog.set_level(logging.DEBUG)
    await _linked(owner_api)
    seen: list[str] = []
    for path in GOOGLE_PATHS:
        r = await owner_api.get(path)
        assert r.status_code == 200, (path, r.text)
        seen.append(r.text)
    for mode in ("echo", "401", "429", "500", "timeout"):
        fake_hub.mode = mode
        r = await owner_api.get("/hub/google/mail/search", params={"q": f"lỗi {mode}"})
        assert r.status_code == 409, (mode, r.text)
        seen.append(r.text)
    fake_hub.mode = "ok"
    seen.append((await owner_api.get("/hub/link")).text)
    seen.append((await owner_api.get("/mcp/calls")).text)
    seen.append((await owner_api.get("/audit?limit=100")).text)
    for body in seen:
        assert TOKEN not in body
    for sql in ("SELECT * FROM agent.mcp_calls", "SELECT * FROM ops.action_log", "SELECT * FROM ops.health_alerts",
                "SELECT * FROM agent.hub_links", "SELECT * FROM core.notifications"):
        assert TOKEN not in await _db_text(sql), sql
    assert TOKEN not in caplog.text
    rows = await _db_text("SELECT result_summary, args FROM agent.mcp_calls WHERE outcome = 'ok'")
    for content in ("Báo giá", "Tuấn", "Họp", "Hợp đồng", "báo giá", PHONE, EMAIL, KEY):
        assert content not in rows, content
    assert "nội dung không lưu" in rows and "Đọc Gen-hub (gmail_search)" in rows


# ─── 8. Danh sách hậu tố + lớp che ───────────────────────────────────────────────

def test_suffix_whitelist_google() -> None:
    for n in hub.GOOGLE_READ_SUFFIXES:
        assert hub.suffix_of(n) == n and hub.suffix_of(f"mcp-46634__{n}") == n
    assert set(hub.READ_SUFFIXES) == set(hub.KHO_READ_SUFFIXES) | set(hub.GOOGLE_READ_SUFFIXES)
    assert len(hub.READ_SUFFIXES) == 10
    for bad in (*hub.WRITE_SUFFIXES_DENY, "mcp-46634__gmail_send", "mcp-46634__calendar_create_event",
                "mcp-46634__drive_create_file", "mcp-46634__drive_read_file", "mcp-46634__sheets_read",
                "mcp-46634__contacts_search", "evil_gmail_search", "mcp__gmail_search_x", "gmail_search__x"):
        assert hub.suffix_of(bad) is None, bad
    assert hub.WRITE_SUFFIXES_DENY == ("gmail_send", "gmail_create_draft", "calendar_create_event", "drive_create_file",
                                       "drive_share_file", "tasks_create", "docs_edit", "sheets_write",
                                       "slides_add_slide", "kho_create", "kho_update")
    assert not set(hub.WRITE_SUFFIXES_DENY) & set(hub.READ_SUFFIXES)
    assert hub.call_kho is hub.call_hub
    assert hub.SCOPE_LABELS == {"calendar": "đọc lịch", "mail": "đọc mail", "tasks": "đọc việc (Google Tasks)",
                                "drive": "tìm tệp Drive"}


def test_mask_keep_keys() -> None:
    data = {"id": MAIL_ID, "messageId": "18c2f49999999999", "calendarId": EMAIL, "other": MAIL_ID,
            "nested": [{"id": EMAIL}, {"threadId": THREAD_ID, "id": f"{KEY}"}], "n": 7,
            "code": "HUB_X", "id2": 5}
    kept = hub.mask_for_model(data, keep_keys=hub.HUB_KEEP_KEYS)
    assert kept["id"] == MAIL_ID and kept["messageId"] == "18c2f49999999999" and kept["code"] == "HUB_X"
    # `code` KHÔNG phải khoá id: mã đặt chỗ / OTP / SĐT viết liền dưới `code` vẫn bị che số dài.
    assert "code" not in hub.HUB_KEEP_KEYS
    for raw in ("0912345678", "190312345678901"):
        masked = hub.mask_for_model({"code": raw}, keep_keys=hub.HUB_KEEP_KEYS)["code"]
        assert masked != raw and "•" in masked, raw
    assert kept["nested"][1]["threadId"] == THREAD_ID
    assert EMAIL not in orjson.dumps(kept).decode() and kept["nested"][0]["id"] == "t•••@example.com"  # có '@' ⇒ che
    assert kept["calendarId"] == "t•••@example.com" and KEY not in orjson.dumps(kept).decode()
    assert kept["other"] != MAIL_ID and "•" in kept["other"]  # khoá ngoài keep_keys vẫn che số dài
    # Mặc định không keep_keys = hành vi cũ: id số dài cũng bị che.
    old = hub.mask_for_model(data)
    assert old["id"] != MAIL_ID and "•" in old["id"]
    assert hub.mask_for_model({"x": MAIL_ID}, keep_keys=frozenset()) == hub.mask_for_model({"x": MAIL_ID})
    # id chứa token của liên kết thì không được giữ.
    assert TOKEN not in orjson.dumps(hub.mask_for_model({"id": TOKEN}, secrets=(TOKEN,),
                                                         keep_keys=hub.HUB_KEEP_KEYS)).decode()
    # Chuỗi quá dài / ký tự lạ ⇒ che như thường.
    assert hub.mask_for_model({"id": "a b 0912 345 678"}, keep_keys=hub.HUB_KEEP_KEYS)["id"] != "a b 0912 345 678"
    # id tệp Drive (44 ký tự) giữ nguyên.
    assert hub.mask_for_model({"fileId": DRIVE_ID}, keep_keys=hub.HUB_KEEP_KEYS)["fileId"] == DRIVE_ID


def test_mask_hub_keeps_ids_inside_json_text() -> None:
    result = {"content": [{"type": "text", "text": orjson.dumps(payload_of("gmail_search")).decode()}]}
    masked = hub.mask_hub(result, secrets=(TOKEN,), suffix="gmail_search")
    text_ = masked["content"][0]["text"]
    assert isinstance(text_, str) and MAIL_ID in text_ and orjson.loads(text_)["messages"][0]["id"] == MAIL_ID
    for leaked in LEAKS:
        assert leaked not in text_
    # Kho giữ nguyên hành vi cũ (không keep_keys, văn bản không phân tích).
    old = hub.mask_hub({"content": [{"type": "text", "text": f'{{"id": "{MAIL_ID}"}}'}]}, suffix="kho_search")
    assert MAIL_ID not in old["content"][0]["text"]


def test_truncate_text() -> None:
    out = hub.truncate_text({"a": "x" * 5000, "b": ["y" * 10, {"c": "z" * 4001}], "n": 1}, 4000)
    assert len(out["a"]) == 4000 and out["b"][0] == "y" * 10 and len(out["b"][1]["c"]) == 4000 and out["n"] == 1


# ─── normalize_items: nhiều dạng kết quả ──────────────────────────────────────────

def test_normalize_items_shapes() -> None:
    ev = hub.normalize_items("calendar_today", {"events": payload_of("calendar_list_events")["events"]})
    assert ev == [{"start": "2026-10-09T09:00:00+07:00", "all_day": False, "title": ev[0]["title"]},
                  {"start": "2026-10-09", "all_day": True, "title": "Nghỉ lễ"}]
    assert PHONE not in ev[0]["title"] and "Họp với anh Tuấn" in ev[0]["title"]
    # structuredContent là list; content[].text là JSON; văn bản thường (mỗi dòng một mục).
    assert hub.normalize_items("calendar_today", {"structuredContent": [{"summary": "A", "start": "2026-10-09"}]}) \
        == [{"start": "2026-10-09", "all_day": True, "title": "A"}]
    as_text = {"content": [{"type": "text", "text": orjson.dumps(payload_of("gmail_search")).decode()}]}
    mail = hub.normalize_items("mail_reply", as_text)
    assert mail == [{"id": MAIL_ID, "from": mail[0]["from"], "subject": mail[0]["subject"],
                     "date": "Fri, 09 Oct 2026 08:15:00 +0700"}]
    assert EMAIL not in mail[0]["from"] and "snippet" not in mail[0] and KEY not in orjson.dumps(mail).decode()
    plain = hub.normalize_items("calendar_today", {"content": [{"type": "text", "text": "09:30 Họp A\nNghỉ"}]})
    assert plain == [{"start": "09:30", "all_day": False, "title": "Họp A"},
                     {"start": "", "all_day": False, "title": "Nghỉ"}]
    tasks = hub.normalize_items("tasks_open", {"structuredContent": payload_of("tasks_list")})
    assert [t["title"] for t in tasks] == [tasks[0]["title"]] and tasks[0]["id"] == "task-123456"  # bỏ việc đã xong
    assert tasks[0]["due"] == "2026-10-10T00:00:00.000Z" and PHONE not in tasks[0]["title"]
    nested = hub.normalize_items("tasks_open", {"tasklists": [{"tasks": [{"id": "t1", "title": "X"}]}]})
    assert nested == [{"id": "t1", "title": "X", "due": ""}]
    assert hub.normalize_items("tasks_open", "- việc 1\n- việc 2") == [
        {"id": "", "title": "việc 1", "due": ""}, {"id": "", "title": "việc 2", "due": ""}]
    # Giới hạn 10 mục, loại lạ ⇒ rỗng, rác ⇒ rỗng.
    many = {"items": [{"id": f"t{i}", "title": f"Việc {i}"} for i in range(30)]}
    assert len(hub.normalize_items("tasks_open", many)) == 10
    assert hub.normalize_items("khác", many) == [] and hub.normalize_items("tasks_open", None) == []
    assert hub.normalize_items("mail_reply", {"messages": []}) == []
    # Thân thư (body) không bao giờ vào mục.
    full = hub.normalize_items("mail_reply", {"messages": [{"id": MAIL_ID, "subject": "S", "body": "RIÊNG TƯ",
                                                            "snippet": "RIÊNG TƯ"}]})
    assert "RIÊNG TƯ" not in orjson.dumps(full, option=orjson.OPT_NON_STR_KEYS).decode()
