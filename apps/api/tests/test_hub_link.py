"""v0.1.26 — Đợt D1: nối Gen-hub, Gen đọc Kho dữ liệu (docs/design/gen-hub-link.md §3).

Gen-hub `/mcp` giả bằng `httpx.MockTransport`: tên tool có tiền tố connector, Bearer bắt buộc, 401/429/timeout,
tool lạ + tool ghi. Kiểm: chỉ Owner, PIN, token không bao giờ lộ, danh sách cho phép theo hậu tố, che dữ liệu trước
khi sang model, đệm Redis 5 phút, tool ghi → bản nháp, RLS, nhắc token sắp hết hạn."""

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import orjson
import pytest
from sqlalchemy import text

from gh.db import admin_sessionmaker
from gh.gen.tools import TOOLS, ToolRunner
from gh.hub_link import service as hub
from tests.conftest import OWNER, Api
from tests.phase2 import org_id
from tests.test_gen import _user_of
from tests.test_rbac_api import login_as
from tests.test_rls import _as_low_priv

TOKEN = "ghtok_SieuBiMat_1234567890abcdef"
ENDPOINT = "http://127.0.0.1:9911/mcp"
PREFIX = "mcp-58450__"
KHO_TEXT = ("VIEC-3 Đang làm: gọi anh Tuấn 0912 345 678, email tuan.nguyen@example.com, khoá sk-abcdefghij1234567890, "
            "STK 190312345678901; hạn 2026-09-27")


class FakeHub:
    """Gen-hub `/mcp` giả — đếm lượt gọi để kiểm đệm; `mode` giả lỗi."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.mode = "ok"
        self.auth_seen: list[str | None] = []

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def handle(self, req: httpx.Request) -> httpx.Response:
        body = orjson.loads(req.content)
        self.auth_seen.append(req.headers.get("authorization"))
        if self.mode == "timeout":
            raise httpx.ReadTimeout("hết giờ", request=req)
        if req.headers.get("authorization") != f"Bearer {TOKEN}" or self.mode == "401":
            return httpx.Response(401, json={"error": "unauthorized"})
        if self.mode == "429":
            return httpx.Response(429, json={"error": "rate limited"})
        if self.mode == "echo" and body["method"] == "tools/call":
            # Máy chủ ác ý / lỗi lặp lại header trong thân lỗi.
            return httpx.Response(500, text=f"boom authorization={req.headers.get('authorization')}")
        if body["method"] == "tools/list":
            ro = {"readOnlyHint": True}
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": {"tools": [
                *({"name": PREFIX + n, "description": n, "inputSchema": {}, "annotations": ro}
                  for n in ("kho_tom_tat", "kho_search", "kho_get", "kho_find_by_id", "kho_list")),
                {"name": PREFIX + "kho_create", "inputSchema": {}, "annotations": {"readOnlyHint": False}},
                {"name": "vault__vault-35323", "inputSchema": {}, "annotations": ro},
            ]}})
        name = body["params"]["name"]
        self.calls.append(name)
        args = body["params"]["arguments"]
        if name.endswith("kho_find_by_id"):
            payload: Any = {"id": args["id"], "Tiêu đề": "Nối Gen-hub", "api_key": "abc-123-xyz"}
        else:
            payload = {"content": [{"type": "text", "text": KHO_TEXT}], "args": args}
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": payload})


@pytest.fixture
def fake_hub(app: Any) -> FakeHub:
    h = FakeHub()
    app.state.mcp_transport = h.transport()
    return h


async def _pin(api: Api) -> None:
    r = await api.send("POST", "/auth/pin/verify", {"pin": OWNER["pin"]})
    assert r.status_code == 200, r.text


async def _configure(api: Api, **extra: Any) -> dict[str, Any]:
    await _pin(api)
    r = await api.send("PATCH", "/hub/link", {"endpoint": ENDPOINT, "token": TOKEN, **extra})
    assert r.status_code == 200, r.text
    return r.json()  # type: ignore[no-any-return]


async def _linked(api: Api) -> dict[str, Any]:
    await _configure(api)
    r = await api.send("POST", "/hub/link/test", {})
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True, r.json()
    return r.json()  # type: ignore[no-any-return]


async def _db_text(sql: str) -> str:
    async with admin_sessionmaker()() as db:
        return orjson.dumps((await db.execute(text(sql))).all(), default=str).decode()


# ─── cấu hình: tắt mặc định, PIN, token không bao giờ lộ ───────────────────────

async def test_off_by_default(owner_api: Api, fake_hub: FakeHub) -> None:
    r = await owner_api.get("/hub/link")
    assert r.status_code == 200 and r.json()["configured"] is False and r.json()["status"] == "off"
    r = await owner_api.get("/hub/kho/summary")
    assert r.status_code == 409 and r.json()["code"] == "HUB_LINK_OFF"
    assert fake_hub.calls == []


async def test_patch_needs_pin_and_token_never_exposed(owner_api: Api, fake_hub: FakeHub, app: Any) -> None:
    r = await owner_api.send("PATCH", "/hub/link", {"endpoint": ENDPOINT, "token": TOKEN})
    assert r.status_code == 423
    link = await _configure(owner_api, token_expires_at=(datetime.now(UTC) + timedelta(days=90)).isoformat())
    assert link["configured"] is True and link["enabled"] is False and link["has_token"] is True
    assert link["status"] == "off" and link["days_left"] in (89, 90)
    assert TOKEN not in orjson.dumps(link).decode()
    # Bật thẳng qua PATCH không được — phải Kiểm tra xanh.
    r = await owner_api.send("PATCH", "/hub/link", {"enabled": True})
    assert r.status_code == 422
    await owner_api.send("POST", "/hub/link/test", {})
    for path in ("/hub/link", "/mcp/servers", "/mcp/calls", "/mcp/tools", "/audit?limit=100"):
        assert TOKEN not in (await owner_api.get(path)).text, path
    # CSDL: chỉ bản mã hoá; Action Log, mcp_calls, hub_links không chứa token.
    blob = await _db_text("SELECT encode(auth_enc, 'escape') FROM agent.mcp_servers")
    assert TOKEN not in blob
    for sql in ("SELECT * FROM ops.action_log", "SELECT * FROM agent.mcp_calls", "SELECT * FROM agent.hub_links"):
        assert TOKEN not in await _db_text(sql), sql
    acts = await _db_text("SELECT action FROM ops.action_log WHERE action LIKE 'hub.%'")
    assert "hub.link_updated" in acts and "hub.link_tested" in acts
    assert all(a == f"Bearer {TOKEN}" for a in fake_hub.auth_seen)


async def test_validation(owner_api: Api, fake_hub: FakeHub) -> None:
    await _pin(owner_api)
    r = await owner_api.send("PATCH", "/hub/link", {"endpoint": "ftp://x/mcp", "token": TOKEN})
    assert r.status_code == 422
    r = await owner_api.send("PATCH", "/hub/link", {"endpoint": ENDPOINT, "token": "ngan"})
    assert r.status_code == 422
    r = await owner_api.send("PATCH", "/hub/link", {"token_expires_at": None})
    assert r.status_code == 422  # lần đầu cần địa chỉ + token
    r = await owner_api.send("POST", "/hub/link/test", {})
    assert r.status_code == 409 and r.json()["code"] == "HUB_LINK_NOT_CONFIGURED"


# ─── kiểm tra: chỉ mở đúng tool đọc Kho ─────────────────────────────────────────

async def test_test_exposes_only_whitelisted_read_tools(owner_api: Api, fake_hub: FakeHub, db: Any) -> None:
    out = await _linked(owner_api)
    assert out["link"]["enabled"] is True and out["link"]["status"] == "ok" and out["missing_tools"] == []
    rows = (await db.execute(text("""
        SELECT t.name, t.access, t.is_exposed, array_remove(array_agg(g.agent_key), NULL) AS grants
        FROM agent.mcp_tools t LEFT JOIN agent.mcp_grants g ON g.tool_id = t.id GROUP BY t.id"""))).all()
    by = {r.name: r for r in rows}
    for n in ("kho_tom_tat", "kho_search", "kho_get", "kho_find_by_id", "kho_list"):
        assert by[PREFIX + n].is_exposed is True and by[PREFIX + n].grants == ["core.gen"]
    # v0.1.50 (F-81): kho_create (tool GHI Kho) giờ được mở + cấp cho core.gen để `write_kho` đi được — KHÔNG bị gọi
    # khi kiểm, và đường đọc (suffix_of / call_kho) vẫn từ chối nó (test_suffix_whitelist, test_unknown_suffix_refused).
    assert by[PREFIX + "kho_create"].access == "write"
    assert by[PREFIX + "kho_create"].is_exposed is True and by[PREFIX + "kho_create"].grants == ["core.gen"]
    assert by["vault__vault-35323"].is_exposed is False and by["vault__vault-35323"].grants == []
    assert fake_hub.calls == [PREFIX + "kho_tom_tat"]
    # Máy chủ giả này chỉ có kho_create (thiếu kho_update) ⇒ chưa đủ quyền ghi Kho; thiếu quyền ghi KHÔNG làm ok=false.
    assert out["ok"] is True and out["write_scopes"] == {"kho": False, "kho_create": True, "kho_update": False}
    assert out["write_missing"] == ["ghi Kho (kho_update)"] and out["exposed_write_tools"] == [PREFIX + "kho_create"]
    assert out["write_hidden"] == []


def test_suffix_whitelist() -> None:
    assert hub.suffix_of("mcp-58450__kho_tom_tat") == "kho_tom_tat"
    assert hub.suffix_of("kho_search") == "kho_search"
    for bad in ("mcp-1__kho_create", "kho_update", "vault__vault-1", "evil_kho_tom_tat", "mcp__kho_tom_tat_x"):
        assert hub.suffix_of(bad) is None
    # v0.1.50: hậu tố GHI Kho có hàm riêng (chỉ cho write_kho) — suffix_of vẫn chỉ-đọc.
    assert hub.write_suffix_of("mcp-1__kho_create") == "kho_create"
    assert hub.write_suffix_of("kho_update") == "kho_update"
    for bad in ("kho_delete", "gmail_send", "kho_tom_tat", "evil_kho_create", "mcp__kho_update_x"):
        assert hub.write_suffix_of(bad) is None
    assert hub.KHO_WRITE_SUFFIXES == ("kho_create", "kho_update")


async def test_unknown_suffix_refused(owner_api: Api, fake_hub: FakeHub, db: Any, redis: Any) -> None:
    await _linked(owner_api)
    user, _ = await _user_of(owner_api)
    with pytest.raises(Exception) as e:
        await hub.call_kho(db, redis, hub.client_for(fake_hub.transport()), user=user, suffix="kho_create",
                           args={})
    assert getattr(e.value, "code", None) == "HUB_TOOL_NOT_ALLOWED"


# ─── đọc Kho: che, đệm, quyền ───────────────────────────────────────────────────

def test_mask_for_model() -> None:
    out = hub.mask_for_model({"text": KHO_TEXT, "Mật khẩu": "abc", "token": "x-1", "n": 5, "list": [TOKEN]},
                             secrets=(TOKEN,))
    s = orjson.dumps(out).decode()
    for leaked in ("0912 345 678", "tuan.nguyen@example.com", "sk-abcdefghij1234567890", "190312345678901", TOKEN,
                   '"abc"', "x-1"):
        assert leaked not in s, leaked
    assert "2026-09-27" in s and "VIEC-3" in s and "t•••@example.com" in s and "678" in s and out["n"] == 5


async def test_kho_reads_masked_and_cached(owner_api: Api, fake_hub: FakeHub, redis: Any) -> None:
    await _linked(owner_api)
    fake_hub.calls.clear()
    r = await owner_api.get("/hub/kho/summary")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["cached"] is False and body["source"] == "Kho dữ liệu qua Gen-hub"
    for leaked in ("0912 345 678", "tuan.nguyen@example.com", "sk-abcdefghij", "190312345678901"):
        assert leaked not in r.text
    assert "VIEC-3" in r.text and "2026-09-27" in r.text
    r2 = await owner_api.get("/hub/kho/summary")
    assert r2.json()["cached"] is True and r2.json()["data"] == body["data"]
    assert fake_hub.calls == [PREFIX + "kho_tom_tat"]
    assert 0 < await redis.ttl(hub.cache_key((await _user_of(owner_api))[0].org_id, "kho_tom_tat", {})) <= 300
    r = await owner_api.get("/hub/kho/search", params={"q": "Jev", "bang": "Quyết định"})
    assert r.status_code == 200 and r.json()["data"]["args"] == {"text": "Jev", "bang": "Quyết định"}
    r = await owner_api.get("/hub/kho/records/viec-12")
    assert r.status_code == 200 and r.json()["data"]["id"] == "VIEC-12" and r.json()["data"]["api_key"] == hub.MASK
    assert (await owner_api.get("/hub/kho/records/VIEC-12;DROP")).status_code == 422
    # mcp_calls.result_summary cũng là bản đã che.
    assert "0912 345 678" not in await _db_text("SELECT result_summary FROM agent.mcp_calls")
    # Đổi token → xoá đệm, tắt liên kết tới khi Kiểm tra lại.
    await owner_api.send("PATCH", "/hub/link", {"token": TOKEN})
    assert [k async for k in redis.scan_iter(match="gh:hub:kho:*")] == []
    assert (await owner_api.get("/hub/kho/summary")).json()["code"] == "HUB_LINK_OFF"


async def test_non_owner_forbidden(owner_api: Api, fake_hub: FakeHub, client: httpx.AsyncClient, db: Any,
                                   app: Any) -> None:
    await _linked(owner_api)
    auditor = await login_as(client, db, "auditor")
    try:
        assert (await auditor.get("/hub/link")).status_code == 200  # xem trạng thái (system.read)
        for path in ("/hub/kho/summary", "/hub/kho/search?q=a", "/hub/kho/records/VIEC-1"):
            assert (await auditor.get(path)).status_code == 403, path
        assert (await auditor.send("PATCH", "/hub/link", {"enabled": False})).status_code == 403
        assert (await auditor.send("POST", "/hub/link/test", {})).status_code == 403
        a, atok = await _user_of(auditor)
        assert (await ToolRunner(app, a, atok).run("hub.kho_summary", {})).error == "FORBIDDEN"
    finally:
        await auditor.c.aclose()


async def test_gen_tools_read_kho(owner_api: Api, fake_hub: FakeHub, app: Any) -> None:
    assert {"hub.kho_summary", "hub.kho_search", "hub.kho_get"} <= set(TOOLS)
    o, tok = await _user_of(owner_api)
    run = ToolRunner(app, o, tok)
    off = await run.run("hub.kho_summary", {})
    assert off.error == "HUB_LINK_OFF"
    await _linked(owner_api)
    res = await run.run("hub.kho_get", {"ma": "qd-3"})
    assert res.ok and "QD-3" in res.text and "abc-123-xyz" not in res.text
    assert (await run.run("hub.kho_get", {"ma": "../../x"})).error == "BAD_ARGS"
    s = await run.run("hub.kho_summary", {})
    assert s.ok and "0912 345 678" not in s.text and "VIEC-3" in s.text


# ─── hỏng thì sao ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize(("mode", "status", "needle"), [("401", "expired", "hết hạn"), ("429", "error", "giới hạn"),
                                                       ("timeout", "error", "mạng")])
async def test_failures(owner_api: Api, fake_hub: FakeHub, mode: str, status: str, needle: str) -> None:
    await _linked(owner_api)
    fake_hub.mode = mode
    r = await owner_api.get("/hub/kho/search", params={"q": "mới"})
    assert r.status_code == 409 and r.json()["code"] == "HUB_UNAVAILABLE" and needle in r.json()["detail"]
    link = (await owner_api.get("/hub/link")).json()
    assert link["status"] == status and TOKEN not in orjson.dumps(link).decode()
    # Kiểm tra lại cũng không ném lỗi — trả ok=false để thẻ hiện lý do.
    r = await owner_api.send("POST", "/hub/link/test", {})
    assert r.status_code == 200 and r.json()["ok"] is False and TOKEN not in r.text


async def test_write_tool_goes_to_draft(owner_api: Api, fake_hub: FakeHub, db: Any) -> None:
    await _linked(owner_api)
    fake_hub.calls.clear()
    tid = (await db.execute(text("SELECT id FROM agent.mcp_tools WHERE name = :n"),
                            {"n": PREFIX + "kho_tom_tat"})).scalar_one()
    assert (await owner_api.send("PATCH", f"/mcp/tools/{tid}", {"access": "write"})).status_code == 200
    r = await owner_api.get("/hub/kho/summary")
    assert r.status_code == 409 and r.json()["code"] == "HUB_TOOL_HELD"
    assert fake_hub.calls == []
    n = (await db.execute(text("SELECT count(*) FROM biz.action_drafts WHERE kind = 'mcp_write'"))).scalar_one()
    assert n == 1


async def test_disable(owner_api: Api, fake_hub: FakeHub) -> None:
    await _linked(owner_api)
    r = await owner_api.send("PATCH", "/hub/link", {"enabled": False})
    assert r.status_code == 200 and r.json()["enabled"] is False and r.json()["status"] == "off"
    assert (await owner_api.get("/hub/kho/summary")).json()["code"] == "HUB_LINK_OFF"
    assert "hub.link_disabled" in await _db_text("SELECT action FROM ops.action_log")


# ─── nhắc token + RLS ──────────────────────────────────────────────────────────

async def test_expiry_status_and_reminder(owner_api: Api, fake_hub: FakeHub, db: Any) -> None:
    await _configure(owner_api, token_expires_at=(datetime.now(UTC) + timedelta(days=10)).isoformat())
    await owner_api.send("POST", "/hub/link/test", {})
    assert (await owner_api.get("/hub/link")).json()["status"] == "expiring"
    assert await hub.expiry_scan(db) == 1
    await db.commit()
    assert await hub.expiry_scan(db) == 0  # một lần mỗi token
    await db.commit()
    rows = (await owner_api.get("/notifications")).json()
    items = rows["items"] if isinstance(rows, dict) else rows
    assert any(i["kind"] == "hub.token_expiring" and i["link"] == "/connections#genhub" for i in items)
    # Đổi hạn (token mới) → nhắc lại được.
    await owner_api.send("PATCH", "/hub/link", {"token_expires_at": (datetime.now(UTC) - timedelta(days=1))
                                                .isoformat()})
    assert (await owner_api.get("/hub/link")).json()["status"] == "expired"
    assert await hub.expiry_scan(db) == 1


async def test_hub_links_rls_isolates_orgs(app: Any, db: Any, redis: Any) -> None:
    org_a = await org_id(db)
    org_b = (await db.execute(text(
        "INSERT INTO core.organizations (name) VALUES ('Tổ chức B (RLS hub)') RETURNING id"))).scalar_one()
    org_c = (await db.execute(text(
        "INSERT INTO core.organizations (name) VALUES ('Tổ chức C (RLS hub)') RETURNING id"))).scalar_one()
    for org in (org_a, org_b):
        await db.execute(text("INSERT INTO agent.hub_links (org_id) VALUES (:o)"), {"o": org})
    await _as_low_priv(db, "agent.hub_links")
    await db.execute(text("SELECT set_config('app.org_id', :o, true)"), {"o": str(org_a)})
    assert (await db.execute(text("SELECT org_id FROM agent.hub_links"))).scalars().all() == [org_a]
    with pytest.raises(Exception, match="row-level security|row_level_security"):
        await db.execute(text("INSERT INTO agent.hub_links (org_id) VALUES (:o)"), {"o": org_c})


async def test_closed_tool_blocked_by_mcp_guard(owner_api: Api, fake_hub: FakeHub, db: Any) -> None:
    await _linked(owner_api)
    fake_hub.calls.clear()
    tid = (await db.execute(text("SELECT id FROM agent.mcp_tools WHERE name = :n"),
                            {"n": PREFIX + "kho_search"})).scalar_one()
    assert (await owner_api.send("PATCH", f"/mcp/tools/{tid}/expose", {"is_exposed": False})).status_code == 200
    r = await owner_api.get("/hub/kho/search", params={"q": "x"})
    assert r.status_code == 409 and r.json()["code"] == "HUB_BLOCKED" and "chưa được Owner mở" in r.json()["detail"]
    assert fake_hub.calls == []
    assert "blocked" in await _db_text("SELECT outcome FROM agent.mcp_calls")


# ─── review: SSRF siêu dữ liệu, token lặp trong thân lỗi, mcp_calls không chứa nội dung Kho ─────

async def test_metadata_endpoint_rejected(owner_api: Api, fake_hub: FakeHub) -> None:
    await _pin(owner_api)
    for ep in ("http://169.254.169.254/latest/meta-data", "http://0.0.0.0:9911/mcp", "http://[fe80::1]/mcp"):
        r = await owner_api.send("PATCH", "/hub/link", {"endpoint": ep, "token": TOKEN})
        assert r.status_code == 422, (ep, r.text)
    assert hub.endpoint_forbidden(ENDPOINT) is False


async def test_error_echoing_token_is_redacted(owner_api: Api, fake_hub: FakeHub) -> None:
    await _linked(owner_api)
    fake_hub.mode = "echo"
    r = await owner_api.get("/hub/kho/search?q=zz")
    assert r.status_code == 409 and TOKEN not in r.text
    for sql in ("SELECT * FROM ops.action_log", "SELECT * FROM agent.mcp_calls", "SELECT * FROM agent.hub_links"):
        assert TOKEN not in await _db_text(sql), sql
    for path in ("/hub/link", "/mcp/calls", "/audit?limit=100"):
        assert TOKEN not in (await owner_api.get(path)).text, path


async def test_mcp_calls_summary_has_no_kho_content(owner_api: Api, fake_hub: FakeHub) -> None:
    await _linked(owner_api)
    assert (await owner_api.get("/hub/kho/summary")).status_code == 200
    rows = await _db_text("SELECT result_summary FROM agent.mcp_calls WHERE outcome = 'ok'")
    assert "VIEC-3" not in rows and "Tuấn" not in rows and "nội dung không lưu" in rows


async def test_lan_http_with_token_ok_public_http_rejected(owner_api: Api, fake_hub: FakeHub) -> None:
    """Sửa review v0.1.45: một quy tắc cho cả lúc lưu và lúc gọi — Gen-hub trong LAN qua http:// + token vẫn chạy
    (Kiểm tra + đọc Kho); http:// tới IP công cộng bị chặn NGAY lúc lưu (422), không đợi "Kiểm tra"."""
    await _pin(owner_api)
    r = await owner_api.send("PATCH", "/hub/link", {"endpoint": "http://10.20.30.40:9911/mcp", "token": TOKEN})
    assert r.status_code == 200, r.text
    r = await owner_api.send("POST", "/hub/link/test", {})
    assert r.status_code == 200 and r.json()["ok"] is True, r.text
    assert (await owner_api.get("/hub/kho/summary")).status_code == 200
    assert fake_hub.auth_seen[-1] == f"Bearer {TOKEN}"
    # Đổi sang http công cộng (token đã lưu) → 422 trên endpoint, cấu hình giữ nguyên.
    r = await owner_api.send("PATCH", "/hub/link", {"endpoint": "http://hub.example.com/mcp"})
    assert r.status_code == 422 and "https://" in r.json()["errors"]["endpoint"], r.text
    assert (await owner_api.get("/hub/link")).json()["endpoint"] == "http://10.20.30.40:9911/mcp"
    r = await owner_api.send("PATCH", "/hub/link", {"endpoint": "https://hub.example.com/mcp"})
    assert r.status_code == 200, r.text
