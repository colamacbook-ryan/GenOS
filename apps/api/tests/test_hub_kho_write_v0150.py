"""v0.1.50 (F-81, QD-18) — Gen ĐỀ XUẤT ghi Kho dữ liệu (kho_create / kho_update — bảng Phiên, Việc); ghi thật chỉ khi
Sếp bấm Xác nhận + nhập mã PIN, qua MỘT đường: confirm_proposal phát permit ký → POST /hub/kho/write → write_kho.

Gen-hub `/mcp` giả (httpx.MockTransport) đếm MỌI lời gọi tools/call — bất biến cứng: không Xác nhận + PIN (+ permit
hợp lệ) thì KHÔNG có lời gọi kho_create / kho_update nào; token Gen-hub không bao giờ vào log / Action Log /
mcp_calls / lỗi."""

import ast
import logging
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
import orjson
import pytest
from sqlalchemy import text

from gh.db import admin_sessionmaker
from gh.gen import proposals
from gh.hub_link import kho_write
from gh.hub_link import permit as hub_permit
from gh.hub_link import service as hub
from tests.conftest import OWNER, Api
from tests.test_actionlog_db import _set_scope
from tests.test_gen import FakeRouter, ask, kinds
from tests.test_gen import _user_of as user_of
from tests.test_gen_proposals import _clear_pin, _log, _me, _proposals
from tests.test_rbac_api import login_as

TOKEN = "ghtok_SieuBiMat_1234567890abcdef"
ENDPOINT = "http://127.0.0.1:9911/mcp"
PREFIX = "mcp-58450__"
GG = "mcp-46634__"
WRITE_TOOLS = ("kho_create", "kho_update")
MISSING_MSG = ("Gen-hub chưa cấp quyền ghi Kho — vào Kết nối › Gen-hub tick kho_create, kho_update cho token rồi bấm "
               "Kiểm tra")
UNCERTAIN_MSG = ("Chưa chắc đã ghi — Sếp mở Kho kiểm trước khi bấm lại: Kho đã có bản ghi thì bấm Huỷ; chưa có thì bấm "
                 "Xác nhận lại")
HIDDEN_MSG = ("Sếp đã tự đóng kho_create ở MCP Hub — chưa ghi gì vào Kho. Muốn Gen ghi thì mở lại tool đó "
              "(và cấp cho Gen) ở MCP Hub; Kiểm tra ở Gen-hub không tự mở lại")
PHIEN = {"Chủ đề": "Họp chốt kế hoạch v0.1.50", "Đã chốt": "Ra mắt Gen nhớ và ghi Kho có PIN"}
# Quyền ghi Kho theo TỪNG tool + `kho` (= có cả hai).
W_ALL = {"kho": True, "kho_create": True, "kho_update": True}
W_NO_UPDATE = {"kho": False, "kho_create": True, "kho_update": False}


class FakeHub:
    """Gen-hub `/mcp` giả: Kho (đọc + ghi) và gmail_send. `calls`/`args` ghi MỌI tools/call.

    `drop` = hậu tố Gen-hub không liệt kê · `mode` = giả lỗi CHỈ ở lời gọi ghi Kho: `write_timeout`, `write_echo` (500
    lặp lại header Authorization), `write_error` (Kho báo isError và chữ lỗi có chứa token), `write_401` (token bị thu
    hồi giữa chừng) · `find_style` = hình dạng kết quả kho_find_by_id (`dict` | `text` — JSON nằm trong
    content[].text, mã ở khoá "Mã ID" | `plain` — chữ thường không có tên trường | `error` — Kho báo isError) ·
    `create_style` = kết quả kho_create (`json` — có khoá "Mã ID" | `echo` — chữ lặp lại nội dung + mã mới, không khoá |
    `echo_only` — chữ chỉ lặp lại nội dung, không có mã mới)."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.args: list[dict[str, Any]] = []
        self.mode = "ok"
        self.drop: set[str] = set()
        self.find_style = "dict"
        self.create_style = "json"
        self.next_ma = 12
        self.auth_seen: list[str | None] = []

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def writes(self) -> list[str]:
        return [c for c in self.calls if c.rsplit("__", 1)[-1] in (*WRITE_TOOLS, "gmail_send", "kho_delete")]

    def handle(self, req: httpx.Request) -> httpx.Response:
        body = orjson.loads(req.content)
        self.auth_seen.append(req.headers.get("authorization"))
        if req.headers.get("authorization") != f"Bearer {TOKEN}":
            return httpx.Response(401, json={"error": "unauthorized"})
        if body["method"] == "tools/list":
            ro, rw = {"readOnlyHint": True}, {"readOnlyHint": False}
            tools = [{"name": PREFIX + n, "description": n, "inputSchema": {}, "annotations": ro}
                     for n in ("kho_tom_tat", "kho_search", "kho_get", "kho_find_by_id", "kho_list")]
            tools += [{"name": PREFIX + n, "inputSchema": {}, "annotations": rw} for n in WRITE_TOOLS
                      if n not in self.drop]
            tools += [{"name": GG + "gmail_send", "inputSchema": {}, "annotations": rw}]
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": {"tools": tools}})
        name = body["params"]["name"]
        args = body["params"]["arguments"]
        self.calls.append(name)
        self.args.append(args)
        suffix = name.rsplit("__", 1)[-1]
        if suffix in WRITE_TOOLS and self.mode == "write_timeout":
            raise httpx.ReadTimeout("hết giờ", request=req)
        if suffix in WRITE_TOOLS and self.mode == "write_echo":
            return httpx.Response(500, text=f"boom authorization={req.headers.get('authorization')}")
        if suffix in WRITE_TOOLS and self.mode == "write_401":
            return httpx.Response(401, json={"error": "token revoked"})
        if suffix in WRITE_TOOLS and self.mode == "write_error":
            return self.ok(body, {"isError": True, "content": [{
                "type": "text", "text": f"Lỗi: giá trị 'Trạng thái' không hợp lệ (Bearer {TOKEN})"}]})
        if suffix == "kho_create":
            ma = f"{_prefix(args['bang'])}-{self.next_ma}"
            self.next_ma += 1
            echo = "; ".join(f"{k}: {v}" for k, v in args["fields"].items())
            if self.create_style == "echo":
                return self.ok(body, {"content": [{"type": "text", "text": f"Đã tạo bản ghi — {echo} — mã {ma}"}]})
            if self.create_style == "echo_only":
                return self.ok(body, {"content": [{"type": "text", "text": f"Đã tạo bản ghi — {echo}"}]})
            return self.ok(body, {"content": [{"type": "text", "text": orjson.dumps(
                {"Mã ID": ma, **args["fields"]}).decode()}]})
        if suffix == "kho_update":
            return self.ok(body, {"content": [{"type": "text", "text": f"Đã cập nhật {args['id']}"}]})
        if suffix == "kho_find_by_id":
            code = args["id"]
            rec = ({"Tiêu đề": "Nối Gen-hub", "Trạng thái": "Chờ", "Ưu tiên": "P2"} if code.startswith("VIEC")
                   else {"Chủ đề": "Phiên cũ", "Đã chốt": "Bản 0.1.49"})
            if self.find_style == "text":
                text_ = orjson.dumps({"Mã ID": code, **rec}).decode()
                return self.ok(body, {"content": [{"type": "text", "text": text_}]})
            if self.find_style == "plain":
                return self.ok(body, {"content": [{"type": "text", "text": f"Bản ghi {code}: Nối Gen-hub, đang chờ"}]})
            if self.find_style == "error":
                return self.ok(body, {"isError": True, "content": [{"type": "text", "text": f"Không đọc được {code}"}]})
            return self.ok(body, {"id": code, **rec})
        return self.ok(body, {"content": [{"type": "text", "text": "VIEC-3 Đang làm — hạn 2026-09-27"}]})

    @staticmethod
    def ok(body: dict[str, Any], result: Any) -> httpx.Response:
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": result})


def _prefix(bang: str) -> str:
    return "PHIEN" if bang == "Phiên" else "VIEC"


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


def _create(record: dict[str, Any] | None = None, bang: str = "Phiên") -> dict[str, Any]:
    return {"steps": [{"kind": "say", "text": "LỜI MODEL: cứ bấm Xác nhận, em ghi hết vào Kho cho"},
                      {"kind": "propose", "proposal": {"type": "kho_create", "fields": {
                          "bang": bang, "record": record if record is not None else dict(PHIEN)}}}]}


def _read(ma: str = "VIEC-12") -> dict[str, Any]:
    return {"steps": [{"kind": "tool", "name": "hub.kho_get", "args": {"ma": ma}}]}


def _update(ma: str = "VIEC-12", record: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"steps": [{"kind": "propose", "proposal": {"type": "kho_update", "fields": {
        "ma": ma, "record": record if record is not None else {"Trạng thái": "Xong", "Ngày xong": "2026-10-09"}}}}]}


async def _propose_create(api: Api, app: Any, record: dict[str, Any] | None = None) -> dict[str, Any]:
    t = await ask(api, app, FakeRouter([_create(record)]), "Ghi phiên họp hôm nay vào Kho giúp tôi")
    ps = _proposals(t)
    assert len(ps) == 1, t
    return ps[0]


async def _propose_update(api: Api, app: Any, ma: str = "VIEC-12",
                          record: dict[str, Any] | None = None) -> dict[str, Any]:
    t = await ask(api, app, FakeRouter([_read(ma), _update(ma, record)]), f"Chuyển {ma} sang Xong giúp tôi")
    ps = _proposals(t)
    assert len(ps) == 1, t
    return ps[0]


# ─── 1. Kiểm tra: mở + cấp kho_create / kho_update, KHÔNG gọi chúng ──────────────────────────────────────────────

async def test_test_opens_and_grants_kho_write_tools(owner_api: Api, fake_hub: FakeHub, db: Any) -> None:
    assert (await owner_api.get("/hub/link")).json().get("write_scopes") is None        # chưa nối ⇒ null
    out = await _linked(owner_api)
    by = await _tools(db)
    for n in WRITE_TOOLS:
        assert by[PREFIX + n].access == "write"
        assert by[PREFIX + n].is_exposed is True and by[PREFIX + n].grants == ["core.gen"], n
    assert by[GG + "gmail_send"].is_exposed is False and by[GG + "gmail_send"].grants == []   # gmail_send vẫn đóng
    assert out["write_scopes"] == W_ALL and out["write_missing"] == []
    assert out["exposed_write_tools"] == [PREFIX + "kho_create", PREFIX + "kho_update"] and out["write_hidden"] == []
    assert out["read_scopes"] == {"calendar": False, "mail": False, "tasks": False, "drive": False}
    assert fake_hub.calls == [PREFIX + "kho_tom_tat"]                                   # kiểm không gọi tool ghi
    assert (await owner_api.get("/hub/link")).json()["write_scopes"] == W_ALL
    user, _ = await user_of(owner_api)
    assert await hub.write_scopes(db, user.org_id) == W_ALL
    log = await _db_text("SELECT detail FROM ops.action_log WHERE action IN ('mcp.tool_exposed', 'mcp.grant_added')")
    assert "hub_link_write" in log
    assert TOKEN not in await _db_text("SELECT * FROM ops.action_log")


async def test_missing_write_tool_keeps_ok_and_revokes_when_gone(owner_api: Api, fake_hub: FakeHub, db: Any) -> None:
    fake_hub.drop = {"kho_update"}
    out = await _linked(owner_api)
    assert out["ok"] is True and out["write_scopes"] == W_NO_UPDATE
    assert out["write_missing"] == ["ghi Kho (kho_update)"]
    assert out["link"]["status"] == "ok"
    assert (await owner_api.get("/hub/link")).json()["write_scopes"] == W_NO_UPDATE
    # Gen-hub liệt kê đủ rồi lại bỏ kho_update: thu hồi (đóng + gỡ cấp), giống tool Google.
    fake_hub.drop = set()
    assert (await owner_api.send("POST", "/hub/link/test", {})).json()["write_scopes"] == W_ALL
    fake_hub.drop = {"kho_update"}
    out = await owner_api.send("POST", "/hub/link/test", {})
    assert out.json()["ok"] is True and out.json()["write_scopes"] == W_NO_UPDATE
    by = await _tools(db)
    assert by[PREFIX + "kho_update"].is_exposed is False and by[PREFIX + "kho_update"].grants == []
    assert by[PREFIX + "kho_create"].is_exposed is True
    assert fake_hub.writes() == []


# ─── 2. Đề xuất: chưa có lời gọi ghi nào; huỷ → 0; Xác nhận không PIN → 423 ─────────────────────────────────────

async def test_proposal_cancel_and_no_pin_make_no_write_calls(owner_api: Api, fake_hub: FakeHub, app: Any) -> None:
    await _linked(owner_api)
    p = await _propose_create(owner_api, app)
    assert p["type"] == "kho_create" and p["status"] == "pending" and p["requires_pin"] is True
    assert p["target"] == "hub.kho_write:Phiên"
    today = kho_write.vn_today().isoformat()
    assert p["fields"] == {"bang": "Phiên", "record": {"Chủ đề": PHIEN["Chủ đề"], "Ngày": today,      # Ngày mặc định
                                                       "Đã chốt": PHIEN["Đã chốt"]}}
    assert list(p["fields"]["record"]) == ["Chủ đề", "Ngày", "Đã chốt"]                   # thứ tự trường của KHO_FIELDS
    assert p["labels"] == {"bang": "Phiên", "target": "Tạo mới ở bảng Phiên", "write_scope": "ok"}
    # Tóm tắt do HỆ THỐNG viết: đúng bảng + từng trường + câu kết.
    assert p["summary"].startswith(
        "Tạo bản ghi mới ở bảng Phiên của Kho dữ liệu: Chủ đề = “Họp chốt kế hoạch v0.1.50”; ")
    assert "Ngày = “" + today + "”" in p["summary"] and "Đã chốt = “Ra mắt Gen nhớ và ghi Kho có PIN”" in p["summary"]
    assert p["summary"].endswith("Chỉ ghi khi Sếp bấm Xác nhận và nhập mã PIN (qua Gen-hub).")
    assert "LỜI MODEL" not in p["summary"] and "user_id" not in p and "org_id" not in p
    # Permit KHÔNG bao giờ được phát ở bước dựng thẻ.
    saved = orjson.dumps(await proposals.load(app.state.redis, p["id"])).decode()
    assert "permit" not in saved and "sig" not in saved
    assert fake_hub.writes() == []
    # Huỷ → 0 lời gọi; huỷ rồi không xác nhận được.
    r = await owner_api.send("POST", f"/gen/proposals/{p['id']}/cancel", {})
    assert r.status_code == 200 and r.json()["status"] == "cancelled" and fake_hub.writes() == []
    r = await owner_api.send("POST", f"/gen/proposals/{p['id']}/confirm", {})
    assert r.status_code == 409 and r.json()["code"] == "GEN_PROPOSAL_DECIDED" and fake_hub.writes() == []
    # Đề xuất khác: Xác nhận mà không có phiên PIN → 423, đề xuất vẫn chờ, 0 lời gọi.
    p2 = await _propose_create(owner_api, app)
    await _clear_pin(owner_api)
    r = await owner_api.send("POST", f"/gen/proposals/{p2['id']}/confirm", {})
    assert r.status_code == 423 and r.json()["code"] == "PIN_REQUIRED"
    assert fake_hub.writes() == [] and (await proposals.load(app.state.redis, p2["id"]))["status"] == "pending"
    assert await _log("hub.kho_written") == []


# ─── 3. Xác nhận + PIN → đúng MỘT lời gọi ───────────────────────────────────────────────────────────────────────

async def test_confirm_with_pin_writes_exactly_once(owner_api: Api, fake_hub: FakeHub, app: Any, redis: Any) -> None:
    await _linked(owner_api)
    assert (await owner_api.get("/hub/kho/summary")).status_code == 200                # điền đệm đọc
    assert [k async for k in redis.scan_iter(match="gh:hub:kho:*")] != []
    p = await _propose_create(owner_api, app)
    me = await _me(owner_api)
    await _pin(owner_api)
    fake_hub.calls.clear()
    r = await owner_api.send("POST", f"/gen/proposals/{p['id']}/confirm", {})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["status"] == "confirmed"
    assert out["result"] == {"type": "kho_record", "id": None, "code": "PHIEN-12", "screen": None, "bang": "Phiên"}
    assert fake_hub.calls == [PREFIX + "kho_create"]
    sent = fake_hub.args[-1]
    assert sent == {"bang": "Phiên", "fields": p["fields"]["record"]}
    assert [k async for k in redis.scan_iter(match="gh:hub:kho:*")] == []              # đệm đọc được xoá sau khi ghi
    # Action Log: hub.kho_written (1) + gen.proposal_confirmed (1) + mcp.call_ok.
    written = await _log("hub.kho_written")
    assert len(written) == 1
    w = written[0]
    assert w.actor_type == "user" and w.actor_id == f"user:{me['id']}" and w.target_type == "kho_record"
    assert w.target_id == "PHIEN-12" and w.result == "ok"
    assert w.detail["tool"] == "kho_create" and w.detail["bang"] == "Phiên" and w.detail["proposal_id"] == p["id"]
    assert w.detail["field_keys"] == ["Chủ đề", "Ngày", "Đã chốt"] and len(w.detail["fields_digest"]) == 16
    assert "Họp chốt kế hoạch" not in orjson.dumps(w.detail).decode()                   # không ghi nội dung
    label = await _db_text("SELECT target_label FROM ops.action_log WHERE action = 'hub.kho_written'")
    assert "Phiên · Họp chốt kế hoạch v0.1.50" in label
    conf = await _log("gen.proposal_confirmed")
    assert len(conf) == 1 and conf[0].detail["via"] == "gen" and conf[0].detail["endpoint"] == "POST /hub/kho/write"
    assert conf[0].target_type == "kho_record" and conf[0].target_id is None
    assert "mcp.call_ok" in await _db_text("SELECT action FROM ops.action_log")
    calls_log = await _db_text("SELECT outcome, result_summary FROM agent.mcp_calls WHERE outcome = 'ok'")
    assert "Họp chốt" not in calls_log and "nội dung không lưu" in calls_log
    # Dòng 9 "Gen ghi Kho" đạt sau lần ghi thật đầu tiên.
    ov = (await owner_api.get("/boss-checks")).json()
    assert ov["results"]["kho_write"]["status"] == "pass"
    assert next(x for x in ov["rows"] if x["key"] == "kho_write")["done"] is True
    # Xác nhận lại → 409, vẫn đúng một lời gọi ghi.
    again = await owner_api.send("POST", f"/gen/proposals/{p['id']}/confirm", {})
    assert again.status_code == 409 and again.json()["code"] == "GEN_PROPOSAL_DECIDED"
    assert fake_hub.writes() == [PREFIX + "kho_create"]
    # Thẻ lưu trong hội thoại mang trạng thái mới.
    cid = (await owner_api.get("/gen/conversations")).json()[0]["id"]
    msgs = (await owner_api.get(f"/gen/conversations/{cid}/messages")).json()
    saved = [s for s in msgs[-1]["content"]["steps"] if s["kind"] == "proposal"][0]["proposal"]
    assert saved["status"] == "confirmed" and saved["result"]["code"] == "PHIEN-12"
    # Ghi lần hai (đề xuất khác): thêm một lời gọi, "pass" của dòng 9 không bị ghi lặp.
    p2 = await _propose_create(owner_api, app, {"Chủ đề": "Phiên thứ hai"})
    assert (await owner_api.send("POST", f"/gen/proposals/{p2['id']}/confirm", {})).status_code == 200
    assert fake_hub.writes() == [PREFIX + "kho_create"] * 2
    async with admin_sessionmaker()() as adm:
        n_pass = (await adm.execute(text("SELECT count(*) FROM ops.boss_checks WHERE check_key = 'kho_write'"))
                  ).scalar_one()
    assert n_pass == 1


async def test_confirm_can_edit_record_only(owner_api: Api, fake_hub: FakeHub, app: Any) -> None:
    await _linked(owner_api)
    p = await _propose_create(owner_api, app)
    await _pin(owner_api)
    for bad in ({"bang": "Việc"}, {"ma": "PHIEN-1"}):          # bảng / mã giữ nguyên như lúc đề xuất
        r = await owner_api.send("POST", f"/gen/proposals/{p['id']}/confirm", {"fields": bad})
        assert r.status_code == 422, bad
    r = await owner_api.send("POST", f"/gen/proposals/{p['id']}/confirm", {"fields": {"record": {"Người làm": "agy"}}})
    assert r.status_code == 422 and "Người làm" in r.text and fake_hub.writes() == []
    r = await owner_api.send("POST", f"/gen/proposals/{p['id']}/confirm", {"fields": {"record": {"Ngày": "09/10/2026",
                                                                                              "Chủ đề": "x"}}})
    assert r.status_code == 422 and fake_hub.writes() == []
    r = await owner_api.send("POST", f"/gen/proposals/{p['id']}/confirm",
                             {"fields": {"record": {"Chủ đề": "Tên do Sếp sửa", "Ngày": "2026-10-09"}}})
    assert r.status_code == 200, r.text
    assert fake_hub.args[-1] == {"bang": "Phiên", "fields": {"Chủ đề": "Tên do Sếp sửa", "Ngày": "2026-10-09"}}
    assert len(fake_hub.writes()) == 1


async def test_update_flow_labels_current_value_and_writes_once(owner_api: Api, fake_hub: FakeHub, app: Any) -> None:
    await _linked(owner_api)
    p = await _propose_update(owner_api, app)
    assert p["type"] == "kho_update" and p["target"] == "hub.kho_write:VIEC-12" and p["requires_pin"] is True
    assert p["fields"] == {"ma": "VIEC-12", "record": {"Trạng thái": "Xong", "Ngày xong": "2026-10-09"}}
    assert p["labels"] == {"bang": "Việc", "target": "VIEC-12 · Nối Gen-hub", "write_scope": "ok",
                           "cur:Trạng thái": "Chờ", "cur:Ngày xong": ""}
    assert p["summary"] == ("Cập nhật VIEC-12 (bảng Việc) ở Kho dữ liệu: Trạng thái = “Xong”; "
                            "Ngày xong = “2026-10-09”. Chỉ ghi khi Sếp bấm Xác nhận và nhập mã PIN (qua Gen-hub).")
    assert fake_hub.writes() == []
    await _pin(owner_api)
    r = await owner_api.send("POST", f"/gen/proposals/{p['id']}/confirm", {})
    assert r.status_code == 200, r.text
    assert r.json()["result"] == {"type": "kho_record", "id": None, "code": "VIEC-12", "screen": None, "bang": "Việc"}
    assert fake_hub.writes() == [PREFIX + "kho_update"]
    assert fake_hub.args[-1] == {"id": "VIEC-12", "fields": {"Trạng thái": "Xong", "Ngày xong": "2026-10-09"}}
    w = (await _log("hub.kho_written"))[0]
    assert w.target_id == "VIEC-12" and w.detail["tool"] == "kho_update" and w.detail["bang"] == "Việc"


async def test_update_labels_when_kho_returns_json_text_with_ma_id_key(owner_api: Api, fake_hub: FakeHub,
                                                                       app: Any) -> None:
    """Kho gói JSON trong content[].text, mã ở khoá "Mã ID": mã vẫn được tính là đã thấy; nhãn đọc được giá trị cũ."""
    fake_hub.find_style = "text"
    await _linked(owner_api)
    p = await _propose_update(owner_api, app, "VIEC-12", {"Trạng thái": "Đang làm"})
    assert p["labels"]["target"] == "VIEC-12 · Nối Gen-hub" and p["labels"]["cur:Trạng thái"] == "Chờ"


@pytest.mark.parametrize("style", ["plain", "error"])
async def test_update_blocked_when_current_value_unreadable(owner_api: Api, fake_hub: FakeHub, app: Any,
                                                            style: str) -> None:
    """Kho trả chữ thường / Markdown (không nhận ra tên trường) hoặc báo isError khi đọc bản ghi ⇒ không dựng thẻ sửa:
    thẻ trống cột "Hiện tại" sẽ khiến Sếp tưởng trường đang trống và ghi đè mà không thấy giá trị cũ."""
    await _linked(owner_api)
    fake_hub.find_style = style
    router = FakeRouter([_read("VIEC-12"), _update("VIEC-12"),
                         {"steps": [{"kind": "say", "text": "Em chưa đọc được giá trị hiện tại."}]}])
    t = await ask(owner_api, app, router, "Chuyển VIEC-12 sang Xong giúp tôi")
    assert _proposals(t) == [] and fake_hub.writes() == []
    blocked = [r for r in await _log("gen.propose") if r.result == "blocked"]
    assert len(blocked) == 1 and "VIEC-12" in blocked[0].detail["reason"]
    assert "giá trị hiện tại" in blocked[0].detail["reason"]


# ─── 4. Chống bịa: mã chưa đọc, trường lạ, giá trị sai ────────────────────────────────────────────────────────────

async def test_update_needs_ma_seen_in_this_turn(owner_api: Api, fake_hub: FakeHub, app: Any) -> None:
    await _linked(owner_api)
    router = FakeRouter([_update("VIEC-99"), {"steps": [{"kind": "say", "text": "Em chưa đọc bản ghi đó."}]}])
    t = await ask(owner_api, app, router, "Chuyển VIEC-99 sang Xong")
    assert _proposals(t) == []
    feedback = router.calls[1][-1].content
    assert "VIEC-99" in feedback and "không có trong kết quả hub.kho_*" in feedback
    # Đọc VIEC-12 rồi đề xuất sửa VIEC-13 (chưa đọc) → cũng bị chặn.
    t = await ask(owner_api, app, FakeRouter([_read("VIEC-12"), _update("VIEC-13")]), "sửa VIEC-13")
    assert _proposals(t) == []
    blocked = [r for r in await _log("gen.propose") if r.result == "blocked"]
    assert len(blocked) == 2 and fake_hub.writes() == []


@pytest.mark.parametrize("record", [
    {"Chủ đề": "x", "Công cụ": "Claude"},                 # Công cụ / Người làm: giá trị lựa chọn chưa biết
    {"Chủ đề": "x", "Trường lạ": "y"},                     # ngoài KHO_FIELDS
    {"Chủ đề": "x", "Ngày": "2026-13-45"},
    {"Chủ đề": "x", "Tiêu đề": "z"},                       # trường của bảng Việc, không phải Phiên
    {"Đã chốt": "thiếu chủ đề"},                            # thiếu trường bắt buộc
])
async def test_bad_phien_record_is_blocked(owner_api: Api, fake_hub: FakeHub, app: Any, record: dict[str, Any]) -> None:
    await _linked(owner_api)
    t = await ask(owner_api, app, FakeRouter([_create(record)]), "ghi phiên")
    assert _proposals(t) == [] and "proposal" not in kinds(t)
    assert fake_hub.writes() == []
    assert len([r for r in await _log("gen.propose") if r.result == "blocked"]) == 1


@pytest.mark.parametrize("record", [
    {"Tiêu đề": "x", "Trạng thái": "Đã xong"},
    {"Tiêu đề": "x", "Ưu tiên": "P4"},
    {"Tiêu đề": "x", "Link Issue/PR": "http://github.com/a/b/pull/1"},
    {"Tiêu đề": "x", "Người làm": "agy"},
])
async def test_bad_viec_record_is_blocked(owner_api: Api, fake_hub: FakeHub, app: Any, record: dict[str, Any]) -> None:
    await _linked(owner_api)
    t = await ask(owner_api, app, FakeRouter([_create(record, "Việc")]), "tạo việc")
    assert _proposals(t) == [] and fake_hub.writes() == []


def test_validate_record_rules() -> None:
    ok = kho_write.validate_record("Việc", {"Tiêu đề": "  Sửa lỗi  PIN ", "Trạng thái": "Chờ duyệt", "Ưu tiên": "P1",
                                            "Hạn": "2026-10-31", "Link Issue/PR": "https://github.com/o/r/issues/5",
                                            "Ngày bắt đầu": "2026-10-09", "Ngày xong": "2026-10-10"}, create=True)
    assert ok["Tiêu đề"] == "Sửa lỗi PIN" and list(ok) == list(kho_write.KHO_FIELDS["Việc"])
    assert kho_write.validate_record("Phiên", {"Chủ đề": "a"}, create=True) == {"Chủ đề": "a"}
    assert kho_write.validate_record("Phiên", {"Cảnh báo": "x"}, create=False) == {"Cảnh báo": "x"}
    for bang, rec, create in [("Dự án", {"Tên": "x"}, True), ("Phiên", {}, True), ("Phiên", {}, False),
                              ("Phiên", {"Chủ đề": "x" * 201}, True), ("Phiên", {"Chủ đề": "a", "Đã chốt": "x" * 2001},
                                                                     True),
                              ("Phiên", {"Chủ đề": "a", "Cảnh báo": "x" * 1001}, True),
                              ("Việc", {"Tiêu đề": "a", "Link Issue/PR": "ftp://x"}, True),
                              ("Việc", {"Tiêu đề": "a", "Hạn": "2026-02-30"}, True),
                              ("Việc", {"Tiêu đề": "a", "Công cụ": "Claude"}, True),
                              ("Việc", {"Tiêu đề": "a", "Trạng thái": 3}, True),
                              ("Việc", {"Tiêu đề": "   "}, True), ("Việc", "không phải dict", True),
                              ("Phiên", {f"k{i}": "v" for i in range(9)}, True)]:
        with pytest.raises(ValueError):
            kho_write.validate_record(bang, rec, create=create)
    assert kho_write.fill_defaults("Phiên", {"Chủ đề": "a"}, datetime(2026, 10, 9, 12, 0))["Ngày"] == "2026-10-09"
    assert "Ngày" not in kho_write.fill_defaults("Việc", {"Tiêu đề": "a"})
    assert kho_write.tool_args("kho_create", "Phiên", {"Chủ đề": "a"}) == {"bang": "Phiên", "fields": {"Chủ đề": "a"}}
    assert kho_write.tool_args("kho_update", "VIEC-1", {"Trạng thái": "Xong"}) == {
        "id": "VIEC-1", "fields": {"Trạng thái": "Xong"}}
    assert kho_write.bang_of_ma("VIEC-12") == "Việc" and kho_write.bang_of_ma("QD-3") is None
    assert kho_write.KHO_FIELDS == {"Phiên": ("Chủ đề", "Ngày", "Đã chốt", "Đang bàn", "Việc tiếp", "Cảnh báo"),
                                    "Việc": ("Tiêu đề", "Trạng thái", "Ưu tiên", "Hạn", "Link Issue/PR", "Ngày bắt đầu",
                                             "Ngày xong")}


# ─── 5. POST /hub/kho/write trực tiếp: permit bắt buộc ─────────────────────────────────────────────────────────────

async def _direct(api: Api, permit: Any, *, pid: str | None = None, tool: str = "kho_create",
                  args: dict[str, Any] | None = None) -> httpx.Response:
    return await api.send("POST", "/hub/kho/write", {
        "proposal_id": pid or str(uuid.uuid4()), "tool": tool,
        "args": args if args is not None else {"bang": "Phiên", "fields": {"Chủ đề": "Ghi thẳng"}},
        "permit": permit})


async def test_direct_write_needs_valid_permit(owner_api: Api, fake_hub: FakeHub, app: Any, redis: Any) -> None:
    await _linked(owner_api)
    await _pin(owner_api)
    user, _ = await user_of(owner_api)
    pid = str(uuid.uuid4())
    args = {"bang": "Phiên", "fields": {"Chủ đề": "Ghi thẳng"}}
    fake_hub.calls.clear()
    # a) không permit
    r = await _direct(owner_api, None, pid=pid)
    assert r.status_code == 403 and r.json()["code"] == "HUB_WRITE_PERMIT" and r.json()["detail"] == "PERMIT_MISSING"
    r = await owner_api.send("POST", "/hub/kho/write", {"proposal_id": pid, "tool": "kho_create", "args": args})
    assert r.status_code == 403 and r.json()["code"] == "HUB_WRITE_PERMIT"
    # b) hết hạn
    old = hub_permit.issue(user.org_id, user.id, pid, "kho_create", args, now=int(time.time()) - 400)
    r = await _direct(owner_api, old, pid=pid, args=args)
    assert r.status_code == 403 and r.json()["code"] == "HUB_WRITE_PERMIT" and r.json()["detail"] == "PERMIT_EXPIRED"
    # c) permit cho tham số khác / đề xuất khác / người khác / tool khác
    good = hub_permit.issue(user.org_id, user.id, pid, "kho_create", args)
    other_args = {"bang": "Phiên", "fields": {"Chủ đề": "Tham số đã bị đổi"}}
    r = await _direct(owner_api, good, pid=pid, args=other_args)
    assert r.status_code == 403 and r.json()["detail"] == "PERMIT_MISMATCH"
    r = await _direct(owner_api, good, pid=str(uuid.uuid4()), args=args)
    assert r.status_code == 403 and r.json()["detail"] == "PERMIT_MISMATCH"
    r = await _direct(owner_api, hub_permit.issue(user.org_id, uuid.uuid4(), pid, "kho_create", args), pid=pid,
                      args=args)
    assert r.status_code == 403 and r.json()["detail"] == "PERMIT_MISMATCH"
    r = await _direct(owner_api, hub_permit.issue(user.org_id, user.id, pid, "kho_update", args), pid=pid, args=args)
    assert r.status_code == 403 and r.json()["detail"] == "PERMIT_MISMATCH"
    # d) chữ ký sai (sửa claim sau khi ký; permit ký bằng khoá khác mục đích)
    forged = {**good, "args_sha256": hub_permit.args_sha256(other_args)}
    r = await _direct(owner_api, forged, pid=pid, args=other_args)
    assert r.status_code == 403 and r.json()["detail"] == "PERMIT_BAD_SIG"
    r = await _direct(owner_api, {**good, "sig": "AAAA"}, pid=pid, args=args)
    assert r.status_code == 403 and r.json()["detail"] == "PERMIT_BAD_SIG"
    assert fake_hub.writes() == []                                                        # TẤT CẢ ở trên: 0 lời gọi
    # e) dùng đúng một lần
    r = await _direct(owner_api, good, pid=pid, args=args)
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True, "tool": "kho_create", "bang": "Phiên", "ma": "PHIEN-12"}
    assert fake_hub.writes() == [PREFIX + "kho_create"]
    r = await _direct(owner_api, good, pid=pid, args=args)
    assert r.status_code == 403 and r.json()["code"] == "HUB_WRITE_PERMIT" and r.json()["detail"] == "PERMIT_USED"
    assert fake_hub.writes() == [PREFIX + "kho_create"]
    blocked = await _log("hub.kho_write_blocked")
    assert len(blocked) >= 9 and all(b.result == "blocked" for b in blocked)
    assert all(b.detail["code"] == "HUB_WRITE_PERMIT" for b in blocked if b.detail["tool"] == "kho_create")
    # Tham số hợp lệ về permit nhưng sai về dữ liệu (trường ngoài KHO_FIELDS): kiểm lại ở write_kho.
    bad_args = {"bang": "Phiên", "fields": {"Chủ đề": "a", "Người làm": "agy"}}
    p2 = str(uuid.uuid4())
    r = await _direct(owner_api, hub_permit.issue(user.org_id, user.id, p2, "kho_create", bad_args), pid=p2,
                      args=bad_args)
    assert r.status_code == 422 and fake_hub.writes() == [PREFIX + "kho_create"]
    assert r.json()["code"] == "HUB_WRITE_INVALID" and "Người làm" in r.json()["errors"]["args"]
    assert r.json()["title"] == hub.WRITE_INVALID_MSG
    # Cần phiên PIN: thiếu → 423 (trước cả permit).
    await _clear_pin(owner_api)
    r = await _direct(owner_api, good)
    assert r.status_code == 423 and r.json()["code"] == "PIN_REQUIRED"


def test_permit_unit() -> None:
    org, uid, pid = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    args = {"id": "VIEC-1", "fields": {"Trạng thái": "Xong"}}
    p = hub_permit.issue(org, uid, pid, "kho_update", args, now=1000)
    assert p["exp"] == 1300 and p["v"] == 1 and p["tool"] == "kho_update" and p["proposal_id"] == str(pid)
    assert p["org_id"] == str(org) and p["confirmed_by"] == str(uid) and len(p["nonce"]) == 32
    assert p["args_sha256"] == hub_permit.args_sha256({"fields": {"Trạng thái": "Xong"}, "id": "VIEC-1"})  # sắp khoá


# ─── 6. Tool ngoài danh sách / route MCP chung vẫn chặn ───────────────────────────────────────────────────────

async def test_other_tools_and_generic_route_refused(owner_api: Api, fake_hub: FakeHub, db: Any, redis: Any) -> None:
    await _linked(owner_api)
    await _pin(owner_api)
    user, _ = await user_of(owner_api)
    fake_hub.calls.clear()
    for tool in ("gmail_send", "kho_delete", "kho_tom_tat", PREFIX + "kho_create"):
        pid = str(uuid.uuid4())
        args = {"bang": "Phiên", "fields": {"Chủ đề": "x"}}
        r = await _direct(owner_api, hub_permit.issue(user.org_id, user.id, pid, tool, args), pid=pid, tool=tool,
                          args=args)
        assert r.status_code == 403 and r.json()["code"] == "HUB_TOOL_NOT_ALLOWED", tool
    assert fake_hub.calls == []
    # Đường ĐỌC từ chối tool ghi (kể cả gọi thẳng service).
    with pytest.raises(Exception) as e:
        await hub.call_hub(db, redis, hub.client_for(fake_hub.transport()), user=user, suffix="kho_create", args={})
    assert getattr(e.value, "code", None) == "HUB_TOOL_NOT_ALLOWED"
    # Route MCP chung: Owner gọi kho_create / kho_update → 403 HUB_TOOL_NOT_ALLOWED, không tạo bản nháp mcp_write.
    by = await _tools(db)
    for access in ("write", "read"):
        for n in WRITE_TOOLS:
            await db.execute(text("UPDATE agent.mcp_tools SET access = :a WHERE id = :i"),
                             {"a": access, "i": by[PREFIX + n].id})
        await db.commit()
        for n in WRITE_TOOLS:
            r = await owner_api.send("POST", f"/mcp/tools/{by[PREFIX + n].id}/call",
                                     {"agent_key": "core.gen", "args": {"bang": "Phiên", "fields": {"Chủ đề": "x"}}})
            assert r.status_code == 403 and r.json()["code"] == "HUB_TOOL_NOT_ALLOWED", (access, n, r.text)
            assert "Xác nhận" in r.json()["detail"] and "mã PIN" in r.json()["detail"]
    assert fake_hub.calls == []
    assert (await db.execute(text("SELECT count(*) FROM biz.action_drafts WHERE kind = 'mcp_write'"))).scalar_one() == 0
    assert hub.suffix_of(PREFIX + "kho_create") is None and hub.suffix_of("kho_update") is None
    assert "kho_create" in hub.WRITE_SUFFIXES_DENY and not set(hub.KHO_WRITE_SUFFIXES) & set(hub.READ_SUFFIXES)


# ─── 7. Token không bao giờ lọt ra ngoài ────────────────────────────────────────────────────────────────────────────

async def test_token_never_leaks(owner_api: Api, fake_hub: FakeHub, app: Any, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    await _linked(owner_api)
    bodies: list[str] = []
    # Kho báo lỗi nghiệp vụ và chữ lỗi chứa token → 409 HUB_WRITE_REJECTED, token bị che.
    fake_hub.mode = "write_error"
    p = await _propose_create(owner_api, app)
    await _pin(owner_api)
    r = await owner_api.send("POST", f"/gen/proposals/{p['id']}/confirm", {})
    bodies.append(r.text)
    assert r.status_code == 409 and r.json()["code"] == "HUB_WRITE_REJECTED", r.text
    assert "không hợp lệ" in r.json()["title"] and TOKEN not in r.text
    assert (await proposals.load(app.state.redis, p["id"]))["status"] == "pending"        # chưa ghi ⇒ vẫn chờ
    assert await _log("hub.kho_written") == []
    # Kho lỗi 5xx và lặp lại header Authorization trong thân lỗi → chưa chắc đã ghi, token bị che.
    fake_hub.mode = "write_echo"
    r = await owner_api.send("POST", f"/gen/proposals/{p['id']}/confirm", {})
    bodies.append(r.text)
    assert r.status_code == 502 and r.json()["code"] == "HUB_WRITE_UNCERTAIN" and r.json()["title"] == UNCERTAIN_MSG
    # Thành công.
    fake_hub.mode = "ok"
    await _clear_breaker(app)
    r = await owner_api.send("POST", f"/gen/proposals/{p['id']}/confirm", {})
    bodies.append(r.text)
    assert r.status_code == 200, r.text
    for path in ("/hub/link", "/mcp/servers", "/mcp/calls", "/mcp/tools", "/audit?limit=200", "/boss-checks"):
        bodies.append((await owner_api.get(path)).text)
    assert all(TOKEN not in b for b in bodies)
    for sql in ("SELECT * FROM ops.action_log", "SELECT * FROM agent.mcp_calls", "SELECT * FROM agent.hub_links",
                "SELECT * FROM ops.boss_checks", "SELECT * FROM agent.gen_messages"):
        assert TOKEN not in await _db_text(sql), sql
    assert TOKEN not in caplog.text
    assert all(a == f"Bearer {TOKEN}" for a in fake_hub.auth_seen)                       # token chỉ đi qua header


async def _clear_breaker(app: Any) -> None:
    async for k in app.state.redis.scan_iter(match="gh:hub:brk:*"):
        await app.state.redis.delete(k)


# ─── 8. Không phải Owner ────────────────────────────────────────────────────────────────────────────────────────────

async def test_non_owner_cannot_write(owner_api: Api, fake_hub: FakeHub, app: Any, client: httpx.AsyncClient,
                                      db: Any) -> None:
    await _linked(owner_api)
    await _set_scope(db, "manager", "system.manage", "all")           # có system.manage vẫn không phải Owner
    await db.commit()
    await db.execute(text("""UPDATE core.organizations SET settings = jsonb_set(
        COALESCE(settings, '{}'::jsonb), '{gen}', COALESCE(settings->'gen', '{}'::jsonb)
        || '{"enabled": true, "roles": ["owner", "manager"]}'::jsonb)"""))
    await db.commit()
    mgr = await login_as(client, db, "manager")
    try:
        r = await _direct(mgr, None)
        assert r.status_code == 403
        t = await ask(mgr, app, FakeRouter([_create()]), "ghi phiên vào Kho")
        assert _proposals(t) == []                                                      # đề xuất bị chặn: chỉ Owner
        blocked = [r_ for r_ in await _log("gen.propose") if r_.result == "blocked"]
        assert blocked and "Owner" in blocked[-1].detail["reason"]
        me = await _me(mgr)
        org = (await db.execute(text("SELECT org_id FROM core.users WHERE id = :u"), {"u": me["id"]})).scalar_one()
        forged = {"id": str(uuid.uuid4()), "type": "kho_create", "status": "pending", "user_id": me["id"],
                  "org_id": str(org), "turn_id": str(uuid.uuid4()), "conversation_id": str(uuid.uuid4()),
                  "fields": {"bang": "Phiên", "record": dict(PHIEN)}, "labels": {}, "target": "hub.kho_write:Phiên",
                  "summary": "x", "requires_pin": True}
        await app.state.redis.set(proposals.key(forged["id"]), orjson.dumps(forged))
        r = await mgr.send("POST", f"/gen/proposals/{forged['id']}/confirm", {})
        assert r.status_code == 403
    finally:
        await mgr.c.aclose()
    assert fake_hub.writes() == []


# ─── 9. Lỗi mạng / thiếu quyền / liên kết tắt / ngắt mạch ───────────────────────────────────────────────────────────

async def test_network_error_is_uncertain_and_proposal_stays_pending(owner_api: Api, fake_hub: FakeHub,
                                                                     app: Any) -> None:
    await _linked(owner_api)
    p = await _propose_create(owner_api, app)
    await _pin(owner_api)
    fake_hub.mode = "write_timeout"
    r = await owner_api.send("POST", f"/gen/proposals/{p['id']}/confirm", {})
    assert r.status_code == 502 and r.json()["code"] == "HUB_WRITE_UNCERTAIN" and r.json()["title"] == UNCERTAIN_MSG
    assert TOKEN not in r.text and fake_hub.writes() == [PREFIX + "kho_create"]
    stored = await proposals.load(app.state.redis, p["id"])
    assert stored["status"] == "pending" and stored["labels"]["uncertain"] == "1"     # thẻ nhớ: lần ghi chưa chắc
    assert await app.state.redis.get(proposals.claim_key(p["id"])) is None            # nhả khoá để Sếp bấm lại
    assert await _log("hub.kho_written") == []
    failed = await _log("hub.kho_write_failed")
    assert len(failed) == 1 and failed[0].result == "failed" and failed[0].detail["code"] == "HUB_WRITE_UNCERTAIN"
    ov = (await owner_api.get("/boss-checks")).json()
    assert ov["results"]["kho_write"] is None                                            # chưa ghi được lần nào
    # Mạng hồi lại → xác nhận lại ghi được (Sếp đã được cảnh báo kiểm Kho trước).
    fake_hub.mode = "ok"
    await _clear_breaker(app)
    r = await owner_api.send("POST", f"/gen/proposals/{p['id']}/confirm", {})
    assert r.status_code == 200, r.text
    assert "uncertain" not in r.json()["labels"]                                         # đã ghi chắc ⇒ nhãn mất
    assert (await owner_api.get("/boss-checks")).json()["results"]["kho_write"]["status"] == "pass"


async def test_cancel_after_uncertain_write_says_kho_may_have_it(owner_api: Api, fake_hub: FakeHub, app: Any) -> None:
    """502 HUB_WRITE_UNCERTAIN rồi Sếp mở Kho thấy đã có ⇒ bấm Huỷ: thẻ đóng mang nhãn `uncertain` (Redis + tin đã lưu)
    để web KHÔNG nói "không ghi gì vào Kho" (bản ghi có thể đã nằm trong Kho)."""
    await _linked(owner_api)
    p = await _propose_create(owner_api, app)
    await _pin(owner_api)
    fake_hub.mode = "write_timeout"
    r = await owner_api.send("POST", f"/gen/proposals/{p['id']}/confirm", {})
    assert r.status_code == 502 and r.json()["code"] == "HUB_WRITE_UNCERTAIN"
    r = await owner_api.send("POST", f"/gen/proposals/{p['id']}/cancel", {})
    assert r.status_code == 200 and r.json()["status"] == "cancelled" and r.json()["labels"]["uncertain"] == "1"
    stored = await proposals.load(app.state.redis, p["id"])
    msgs = (await owner_api.get(f"/gen/conversations/{stored['conversation_id']}/messages")).json()
    saved = [st["proposal"] for m in msgs for st in (m["content"].get("steps") or [])
             if st.get("kind") == "proposal" and st["proposal"]["id"] == p["id"]]
    assert len(saved) == 1 and saved[0]["status"] == "cancelled" and saved[0]["labels"]["uncertain"] == "1"
    assert fake_hub.writes() == [PREFIX + "kho_create"]
    # Thẻ thường (không có lần ghi chưa chắc) huỷ ⇒ không có nhãn.
    p2 = await _propose_create(owner_api, app)
    r = await owner_api.send("POST", f"/gen/proposals/{p2['id']}/cancel", {})
    assert r.status_code == 200 and "uncertain" not in r.json()["labels"]


async def test_three_failures_open_breaker_and_block_calls(owner_api: Api, fake_hub: FakeHub, app: Any) -> None:
    await _linked(owner_api)
    p = await _propose_create(owner_api, app)
    await _pin(owner_api)
    fake_hub.mode = "write_timeout"
    for _ in range(3):
        r = await owner_api.send("POST", f"/gen/proposals/{p['id']}/confirm", {})
        assert r.status_code == 502
    assert len(fake_hub.writes()) == 3
    r = await owner_api.send("POST", f"/gen/proposals/{p['id']}/confirm", {})
    assert r.status_code == 409 and r.json()["code"] == "HUB_BREAKER_OPEN" and len(fake_hub.writes()) == 3


async def test_missing_write_scope_is_409_with_no_call(owner_api: Api, fake_hub: FakeHub, app: Any) -> None:
    fake_hub.drop = {"kho_update"}
    await _linked(owner_api)
    p = await _propose_create(owner_api, app)
    assert p["labels"]["write_scope"] == "ok"                # quyền theo ĐÚNG tool: thiếu kho_update không khoá thẻ tạo
    await _pin(owner_api)
    r = await owner_api.send("POST", f"/gen/proposals/{p['id']}/confirm", {})
    assert r.status_code == 200, r.text                      # quyền kiểm theo từng tool: kho_create vẫn có ⇒ ghi được
    # kho_update thiếu: đề xuất sửa → 409 HUB_WRITE_MISSING, 0 lời gọi.
    fake_hub.calls.clear()
    p2 = await _propose_update(owner_api, app)
    assert p2["labels"]["write_scope"] == "missing"
    r = await owner_api.send("POST", f"/gen/proposals/{p2['id']}/confirm", {})
    assert r.status_code == 409 and r.json()["code"] == "HUB_WRITE_MISSING" and r.json()["title"] == MISSING_MSG
    assert PREFIX + "kho_update" not in fake_hub.calls
    assert (await proposals.load(app.state.redis, p2["id"]))["status"] == "pending"


async def test_link_off_blocks_write(owner_api: Api, fake_hub: FakeHub, app: Any) -> None:
    await _linked(owner_api)
    p = await _propose_create(owner_api, app)
    await _pin(owner_api)
    assert (await owner_api.send("PATCH", "/hub/link", {"enabled": False})).status_code == 200
    r = await owner_api.send("POST", f"/gen/proposals/{p['id']}/confirm", {})
    assert r.status_code == 409 and r.json()["code"] == "HUB_LINK_OFF" and fake_hub.writes() == []
    assert (await proposals.load(app.state.redis, p["id"]))["status"] == "pending"


async def test_token_rejected_on_write_has_its_own_code(owner_api: Api, fake_hub: FakeHub, app: Any) -> None:
    """401/403 lúc GHI: token hết hạn / bị thu hồi — mã riêng HUB_TOKEN_REJECTED (thẻ dẫn tới Kết nối › Gen-hub), không
    phải HUB_WRITE_REJECTED ("Bấm Sửa các trường" là sai hướng)."""
    await _linked(owner_api)
    p = await _propose_create(owner_api, app)
    await _pin(owner_api)
    fake_hub.mode = "write_401"
    r = await owner_api.send("POST", f"/gen/proposals/{p['id']}/confirm", {})
    assert r.status_code == 409 and r.json()["code"] == "HUB_TOKEN_REJECTED", r.text
    assert r.json()["title"] == hub.WRITE_TOKEN_MSG and r.json()["detail"].startswith("401")
    assert TOKEN not in r.text
    assert (await proposals.load(app.state.redis, p["id"]))["status"] == "pending"
    failed = await _log("hub.kho_write_failed")
    assert len(failed) == 1 and failed[0].detail["code"] == "HUB_TOKEN_REJECTED"
    assert (await owner_api.get("/hub/link")).json()["status"] == "expired"               # thẻ Gen-hub báo token


async def test_create_code_ignores_codes_echoed_from_content(owner_api: Api, fake_hub: FakeHub, app: Any) -> None:
    """Kho không trả khoá "Mã ID" mà lặp lại nội dung vừa ghi: mã trong nội dung (VIEC-7 ở 'Việc tiếp', PHIEN-3) không
    được nhận nhầm là mã bản ghi mới; chỉ nhận mã đúng bảng; không còn mã nào ⇒ None (không đoán)."""
    await _linked(owner_api)
    await _pin(owner_api)
    fake_hub.create_style = "echo"
    p = await _propose_create(owner_api, app, {"Chủ đề": "Họp", "Việc tiếp": "Làm tiếp VIEC-7 rồi xem PHIEN-3"})
    r = await owner_api.send("POST", f"/gen/proposals/{p['id']}/confirm", {})
    assert r.status_code == 200, r.text
    assert r.json()["result"]["code"] == "PHIEN-12"
    assert (await _log("hub.kho_written"))[-1].target_id == "PHIEN-12"
    # Việc có tiêu đề nhắc VIEC-12, Kho chỉ lặp lại nội dung (không có mã mới) ⇒ không có mã.
    fake_hub.create_style = "echo_only"
    t = await ask(owner_api, app, FakeRouter([_create({"Tiêu đề": "Làm tiếp VIEC-12"}, "Việc")]), "tạo việc")
    p2 = _proposals(t)[0]
    r = await owner_api.send("POST", f"/gen/proposals/{p2['id']}/confirm", {})
    assert r.status_code == 200, r.text
    assert r.json()["result"] == {"type": "kho_record", "id": None, "code": None, "screen": None, "bang": "Việc"}
    assert (await _log("hub.kho_written"))[-1].target_id is None
    assert len(fake_hub.writes()) == 2


def test_ma_from_result_rules() -> None:
    rec = {"Chủ đề": "Họp", "Việc tiếp": "Làm tiếp VIEC-7"}
    # Khoá "Mã ID" được ưu tiên, nhưng phải đúng bảng.
    assert hub.ma_from_result({"Mã ID": "PHIEN-5", "x": "VIEC-7"}, "Phiên", rec.values()) == "PHIEN-5"
    assert hub.ma_from_result({"Mã ID": "VIEC-7"}, "Phiên", rec.values()) is None
    assert hub.ma_from_result({"content": [{"type": "text", "text": '{"ma": "viec-9"}'}]}, "Việc") == "VIEC-9"
    # Theo chữ: bỏ mã lặp lại từ nội dung vừa gửi, bỏ mã khác bảng.
    assert hub.ma_from_result("Đã tạo PHIEN-13; Việc tiếp: Làm tiếp VIEC-7", "Phiên", rec.values()) == "PHIEN-13"
    assert hub.ma_from_result("Việc tiếp: Làm tiếp VIEC-7", "Việc", rec.values()) is None
    assert hub.ma_from_result("Đã tạo PHIEN-13 (liên quan PHIEN-12)", "Phiên") is None          # mơ hồ ⇒ không đoán
    assert hub.ma_from_result("Đã tạo PHIEN-13, PHIEN-13", "Phiên") == "PHIEN-13"
    assert hub.ma_from_result({"isError": False, "content": []}, "Phiên") is None


async def test_owner_hidden_write_tool_is_not_reopened_by_test(owner_api: Api, fake_hub: FakeHub, db: Any,
                                                                app: Any) -> None:
    """Owner TỰ đóng kho_create ở MCP Hub (PIN mcp.expose) để tắt Gen ghi Kho ⇒ Kiểm tra (nút ở thẻ Gen-hub, hay dòng
    'Gen-hub' ở Việc Sếp cần làm) không lặng lẽ mở lại; mở lại ở MCP Hub thì Kiểm tra cấp lại bình thường."""
    await _linked(owner_api)
    by = await _tools(db)
    tid = by[PREFIX + "kho_create"].id
    assert (await owner_api.send("PATCH", f"/mcp/tools/{tid}/expose", {"is_exposed": False})).status_code == 200
    out = (await owner_api.send("POST", "/hub/link/test", {})).json()
    assert out["ok"] is True and out["write_hidden"] == ["kho_create"]
    assert out["write_scopes"] == {"kho": False, "kho_create": False, "kho_update": True}
    assert out["write_missing"] == []                         # Gen-hub vẫn cấp đủ — chỉ Owner tự đóng ở MCP Hub
    assert PREFIX + "kho_create" not in out["exposed_write_tools"]
    assert (await _tools(db))[PREFIX + "kho_create"].is_exposed is False
    # Tải lại trang: GET /hub/link vẫn biết tool đóng là do Sếp (tính lại từ Action Log, không chỉ lúc Kiểm tra).
    link = (await owner_api.get("/hub/link")).json()
    assert link["write_hidden"] == ["kho_create"] and link["write_scopes"]["kho_create"] is False
    # Ghi bằng tool Sếp đã đóng ⇒ 409 HUB_WRITE_HIDDEN chỉ đúng MCP Hub (không bảo tick ở Gen-hub — vô ích), 0 lời gọi.
    p = await _propose_create(owner_api, app)
    assert p["labels"]["write_scope"] == "missing"
    await _pin(owner_api)
    r = await owner_api.send("POST", f"/gen/proposals/{p['id']}/confirm", {})
    assert r.status_code == 409 and r.json()["code"] == "HUB_WRITE_HIDDEN" and r.json()["title"] == HIDDEN_MSG
    assert (await proposals.load(app.state.redis, p["id"]))["status"] == "pending"
    # Gỡ cấp core.gen (thay vì đóng) cũng được tôn trọng.
    tid_u = by[PREFIX + "kho_update"].id
    assert (await owner_api.send("DELETE", f"/mcp/tools/{tid_u}/grants/core.gen")).status_code == 204
    out = (await owner_api.send("POST", "/hub/link/test", {})).json()
    assert sorted(out["write_hidden"]) == ["kho_create", "kho_update"] and out["write_scopes"]["kho"] is False
    assert (await _tools(db))[PREFIX + "kho_update"].grants == []
    # Mở lại ở MCP Hub ⇒ Kiểm tra mở + cấp lại như thường.
    assert (await owner_api.send("PATCH", f"/mcp/tools/{tid}/expose", {"is_exposed": True})).status_code == 200
    r = await owner_api.send("POST", f"/mcp/tools/{tid_u}/grants", {"agent_key": "core.gen"})
    assert r.status_code == 201
    out = (await owner_api.send("POST", "/hub/link/test", {})).json()
    assert out["write_hidden"] == [] and out["write_scopes"] == W_ALL
    assert (await owner_api.get("/hub/link")).json()["write_hidden"] == []
    assert fake_hub.writes() == []


def test_approved_write_true_only_in_write_kho() -> None:
    """Bất biến khoá cứng #4: ngoại lệ "tool ghi không thành bản nháp" (lời gọi có `approved_write=` khác False) CHỈ có
    trong `hub_link.service.write_kho` — duyệt cây cú pháp mọi tệp gh/ (chú thích / docstring không tính)."""
    gh_dir = Path(hub.__file__).resolve().parents[1]
    hits: list[tuple[str, str]] = []
    for f in sorted(gh_dir.rglob("*.py")):
        tree = ast.parse(f.read_text(encoding="utf-8"))
        for fn in ast.walk(tree):
            if not isinstance(fn, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            for node in ast.walk(fn):
                if isinstance(node, ast.Call) and any(
                        k.arg == "approved_write" and not (isinstance(k.value, ast.Constant) and k.value.value is False)
                        for k in node.keywords):
                    hits.append((str(f.relative_to(gh_dir)), fn.name))
    assert hits == [("hub_link/service.py", "write_kho")]


async def test_invoke_tool_default_still_drafts_write_tools(owner_api: Api, fake_hub: FakeHub, db: Any) -> None:
    """`approved_write` mặc định False: tool ghi qua đường chung vẫn là bản nháp, không gọi ra ngoài."""
    from gh.mcp_api import invoke

    await _linked(owner_api)
    user, _ = await user_of(owner_api)
    by = await _tools(db)
    row = await invoke.get_tool(db, user.org_id, by[PREFIX + "kho_create"].id)
    out = await invoke.invoke_tool(db, None, hub.client_for(fake_hub.transport()), org_id=user.org_id, tool=row,
                                   agent_key="core.gen", args={"bang": "Phiên", "fields": {"Chủ đề": "x"}}, actor=user)
    assert out["outcome"] in ("held_for_approval", "blocked") and fake_hub.writes() == []
    await db.commit()
