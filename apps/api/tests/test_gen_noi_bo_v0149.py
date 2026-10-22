"""v0.1.49 (QD-16) — Gen đọc Tài liệu, Deal, Vụ việc nội bộ: tool `document.*`/`deal.*`/`case.*` + 6 endpoint
`/gen/sources/*` (chỉ Owner, chỉ đọc). Nội dung sang model đám mây ⇒ phải đã che email/SĐT/số dài, nhưng id/mã/thời
điểm giữ nguyên để validator còn làm sáng được dòng có thật."""

import base64
import uuid
from typing import Any

import pytest
from sqlalchemy import text

from gh.chassis.masking import MASK, is_secret_key, mask_for_model
from gh.db import sessionmaker
from gh.gen import envelope
from gh.gen.routes import KEEP_RAW, _for_model
from gh.gen.tools import TOOLS, ToolRunner, tools_for
from tests.conftest import Api
from tests.phase2 import listen, msg, org_id, put
from tests.test_gen import FakeRouter, _user_of, ask, gen_log, kinds
from tests.test_p3_relations import _person_of
from tests.test_rbac_api import login_as

DOC_DESC = "Báo giá gửi khách, gọi 0912 345 678, mail tuan.nguyen@example.com"
CASE_TITLE = "Khách phàn nàn giao trễ, gọi lại 0987 654 321"
ERP_REF = "ERP-190312345678"
RAW_SECRETS = ("0912 345 678", "tuan.nguyen@", "190312345678", "0987 654 321")

SIX_TOOLS = ("document.list", "document.get", "deal.list", "deal.get", "case.list", "case.get")


@pytest.fixture
async def seed(app, db, redis, owner_api: Api) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    """Một khách + 1 tài liệu (mô tả có SĐT/email) + 1 deal open (erp_ref có số dài) + 1 vụ việc complaint P1
    (tiêu đề có SĐT)."""
    org = await org_id(db)
    await listen(db, org, "g1")
    [raw] = await put(sessionmaker(), org, msg("Cần báo giá thép cuộn", sender="a1", name="Chị Lan"))
    await db.commit()
    person = await _person_of(db, raw)
    r = await owner_api.send("POST", "/documents", {
        "title": "Báo giá thép cuộn Q4", "description": DOC_DESC, "filename": "bg.txt", "mime": "text/plain",
        "content_base64": base64.b64encode(b"noi dung bao gia - khong duoc doc").decode(),
        "owner_person_id": str(person)})
    assert r.status_code == 201, r.text
    doc = r.json()
    r = await owner_api.send("POST", "/deals", {"person_id": str(person), "amount_vnd": 1_150_000_000,
                                                "erp_ref": ERP_REF})
    assert r.status_code == 201, r.text
    deal = r.json()
    r = await owner_api.send("POST", "/cases", {"title": CASE_TITLE, "priority": "P1",
                                                "subject": {"type": "person", "id": str(person)}})
    assert r.status_code == 201, r.text
    case = r.json()
    return {"person": str(person), "doc": doc, "deal": deal, "case": case}


def _clean(text_: str) -> None:
    for secret in RAW_SECRETS:
        assert secret not in text_, secret


# ═══ Owner: đọc được, đã che, id nguyên vẹn ═══════════════════════════════════

async def test_owner_reads_documents_deals_cases_masked(owner_api: Api, app: Any, seed: dict[str, Any]) -> None:
    user, token = await _user_of(owner_api)
    run = ToolRunner(app, user, token)
    doc, deal, case = seed["doc"], seed["deal"], seed["case"]

    r = await run.run("document.list", {})
    assert r.ok, r.text
    assert "Báo giá thép cuộn Q4" in r.text and doc["id"] in r.text
    _clean(r.text)
    r = await run.run("document.list", {"source": "tay"})
    assert r.ok and doc["id"] in r.text
    r = await run.run("document.list", {"source": "agent"})
    assert r.ok and doc["id"] not in r.text
    r = await run.run("document.get", {"id": doc["id"]})
    assert r.ok and "Báo giá thép cuộn Q4" in r.text and doc["id"] in r.text
    _clean(r.text)
    assert r.data["acl_count"] >= 1 and "acl" not in r.data, r.data       # không đưa principal user:<id> cho model
    assert "user:" not in r.text and "role:" not in r.text
    assert "noi dung bao gia" not in r.text                                  # chỉ siêu dữ liệu, không đọc nội dung tệp

    r = await run.run("deal.list", {"status": "open"})
    assert r.ok and deal["code"] in r.text and deal["id"] in r.text and "1150000000" in r.text
    _clean(r.text)
    assert deal["id"] not in (await run.run("deal.list", {"status": "won"})).text
    r = await run.run("deal.get", {"id": deal["id"]})
    assert r.ok and deal["code"] in r.text
    _clean(r.text)

    r = await run.run("case.list", {"priority": "P1"})
    assert r.ok and case["code"] in r.text and case["id"] in r.text and "Khách phàn nàn giao trễ" in r.text
    _clean(r.text)
    assert case["id"] not in (await run.run("case.list", {"priority": "P3"})).text
    assert case["id"] in (await run.run("case.list", {"status": "open"})).text
    r = await run.run("case.get", {"id": case["id"]})
    assert r.ok and case["code"] in r.text
    _clean(r.text)

    # id UUID nguyên vẹn → validator làm sáng dòng được (id đúng bằng id đã seed, không bị regex số dài cắt đuôi).
    assert {doc["id"], deal["id"], case["id"], deal["code"], case["code"]} <= run.seen_ids
    assert seed["person"] in run.seen_ids


async def test_not_found_and_bad_args(owner_api: Api, app: Any, seed: dict[str, Any]) -> None:
    user, token = await _user_of(owner_api)
    run = ToolRunner(app, user, token)
    assert (await run.run("deal.list", {"status": "xyz"})).error == "BAD_ARGS"
    assert (await run.run("document.get", {"id": "abc"})).error == "BAD_ARGS"
    assert (await run.run("document.list", {"source": "mail"})).error == "BAD_ARGS"
    assert (await run.run("case.list", {"priority": "P9"})).error == "BAD_ARGS"
    assert (await run.run("deal.get", {})).error == "BAD_ARGS"
    assert (await run.run("case.get", {"id": "không-phải-uuid"})).error == "BAD_ARGS"
    ghost = str(uuid.uuid4())
    for name in ("document.get", "deal.get", "case.get"):
        assert (await run.run(name, {"id": ghost})).error == "NOT_FOUND", name


# ═══ Vai trò khác: 403 / FORBIDDEN, không thấy trong bộ tool ═══════════════════

@pytest.mark.parametrize("role", ["manager", "operator", "agent_staff", "auditor"])
async def test_non_owner_forbidden(owner_api: Api, client: Any, db: Any, app: Any, seed: dict[str, Any],
                                   role: str) -> None:
    api = await login_as(client, db, role)
    try:
        user, token = await _user_of(api)
        run = ToolRunner(app, user, token)
        ids = {"id": seed["doc"]["id"]}
        for name in SIX_TOOLS:
            res = await run.run(name, ids if name.endswith(".get") else {})
            assert not res.ok and res.error == "FORBIDDEN", (role, name)
        assert run.seen_ids == set()
        names = {t.name for t in tools_for(user)}
        assert not names & set(SIX_TOOLS), (role, names & set(SIX_TOOLS))
        for path in ("/gen/sources/documents", "/gen/sources/deals", "/gen/sources/cases",
                     f"/gen/sources/documents/{seed['doc']['id']}", f"/gen/sources/deals/{seed['deal']['id']}",
                     f"/gen/sources/cases/{seed['case']['id']}"):
            r = await api.get(path)
            assert r.status_code == 403 and r.json()["code"] == "FORBIDDEN", (role, path, r.text)
    finally:
        await api.c.aclose()


async def test_owner_has_all_six_tools_owner_only(owner_api: Api) -> None:
    user, _ = await _user_of(owner_api)
    assert set(SIX_TOOLS) <= {t.name for t in tools_for(user)}
    assert all(TOOLS[n].owner_only for n in SIX_TOOLS)


# ═══ HTTP /gen/sources/*: Owner 200 và đã che ═════════════════════════════════

async def test_owner_http_sources_masked(owner_api: Api, seed: dict[str, Any]) -> None:
    r = await owner_api.get("/gen/sources/deals")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 1 and body["items"][0]["id"] == seed["deal"]["id"]
    assert body["items"][0]["amount_vnd"] == 1_150_000_000
    _clean(r.text)
    # Endpoint gốc trả KHÔNG che cho Owner — chính là lý do cần lớp che riêng.
    raw = (await owner_api.get("/deals")).json()
    assert raw["items"][0]["erp_ref"] == ERP_REF

    r = await owner_api.get("/gen/sources/documents")
    assert r.status_code == 200 and r.json()["items"][0]["id"] == seed["doc"]["id"]
    _clean(r.text)
    assert "created_by" not in r.json()["items"][0] and r.json()["items"][0]["source"] == "tay"
    assert DOC_DESC in (await owner_api.get("/documents")).text

    r = await owner_api.get(f"/gen/sources/documents/{seed['doc']['id']}")
    assert r.status_code == 200
    d = r.json()
    assert "acl" not in d and d["acl_count"] >= 1 and d["id"] == seed["doc"]["id"]
    _clean(r.text)

    r = await owner_api.get("/gen/sources/cases", params={"priority": "P1"})
    assert r.status_code == 200 and r.json()["items"][0]["id"] == seed["case"]["id"]
    _clean(r.text)
    assert (await owner_api.get(f"/gen/sources/deals/{seed['deal']['id']}")).status_code == 200
    assert (await owner_api.get(f"/gen/sources/cases/{seed['case']['id']}")).status_code == 200

    # Tham số sai bị chặn ở endpoint (limit 1..20, enum).
    assert (await owner_api.get("/gen/sources/deals", params={"limit": 21})).status_code == 422
    assert (await owner_api.get("/gen/sources/deals", params={"status": "xyz"})).status_code == 422
    assert (await owner_api.get("/gen/sources/cases", params={"priority": "P9"})).status_code == 422
    assert (await owner_api.get("/gen/sources/documents", params={"source": "mail"})).status_code == 422


# ═══ Lớp che `_for_model` (không cần CSDL) ════════════════════════════════════

def test_for_model_keeps_ids_masks_text() -> None:
    uid = "550e8400-e29b-41d4-a716-446655440000"
    # mask_for_model một mình cắt nát đuôi UUID (số dài 12 chữ số) — vì vậy khoá định danh phải giữ nguyên.
    assert mask_for_model(uid) != uid
    out = _for_model({"id": uid, "code": "DEA-0001", "amount_vnd": 1_150_000_000, "status": "open",
                      "created_at": "2026-10-09T01:02:03+00:00", "title": "gọi 0912 345 678",
                      "erp_ref": "ERP-190312345678", "note": "tuan.nguyen@example.com",
                      "person": {"id": uid, "name": "Chị Lan 0912345678", "org_name": None},
                      "items": [{"id": uid, "description": "SĐT 0912 345 678"}]})
    assert out["id"] == uid and out["code"] == "DEA-0001" and out["amount_vnd"] == 1_150_000_000
    assert out["status"] == "open" and out["created_at"] == "2026-10-09T01:02:03+00:00"
    assert "0912 345 678" not in out["title"] and "190312345678" not in out["erp_ref"]
    assert "tuan.nguyen@" not in out["note"]
    assert out["person"]["id"] == uid and out["person"]["org_name"] is None
    assert "0912345678" not in out["person"]["name"]
    assert out["items"][0]["id"] == uid and "0912 345 678" not in out["items"][0]["description"]


def test_for_model_does_not_trust_containers_under_raw_keys() -> None:
    # Khoá KEEP_RAW mà giá trị là dict/list (không phải vô hướng) vẫn được che đệ quy.
    out = _for_model({"source": {"phone": "0912 345 678"}, "kind": ["a@b.vn"]})
    assert "0912 345 678" not in str(out) and "a@b.vn" not in str(out)
    assert {"id", "code", "status", "priority", "amount_vnd", "bytes"} <= KEEP_RAW


def test_for_model_masks_secret_named_keys() -> None:
    """Khoá kiểu mật khẩu/token/api_key/pin bị thay hẳn bằng [đã che] — kể cả giá trị ngắn hay số (regex số dài không
    bắt được), ở mọi cấp; khoá KEEP_RAW và giá trị rỗng / bool giữ nguyên như `mask_for_model`."""
    out = _for_model({"id": "DEA-1", "pin": 1234, "api_key": "abc", "wifi_password": "12345678",
                      "meta": {"access_token": "x1", "Mật khẩu": "123"}, "items": [{"secret": 42}],
                      "token_hint": "", "pin_required": True})
    assert out["id"] == "DEA-1"
    assert out["pin"] == MASK and out["api_key"] == MASK and out["wifi_password"] == MASK
    assert out["meta"] == {"access_token": MASK, "Mật khẩu": MASK} and out["items"] == [{"secret": MASK}]
    assert out["token_hint"] == "" and out["pin_required"] is True


def test_is_secret_key_public_helper() -> None:
    """`_for_model` và `mask_for_model` dùng chung quy tắc tên khoá qua hàm công khai `is_secret_key`."""
    for k in ("pin", "api_key", "apikey", "wifi_password", "access_token", "Mật khẩu", "mat_khau", "client-secret"):
        assert is_secret_key(k), k
    for k in ("id", "pinned", "tokens_used", "spin", "description", "code"):
        assert not is_secret_key(k), k


def test_envelope_accepts_new_tools() -> None:
    names = [*SIX_TOOLS, "hub.calendar", "hub.tasks", "hub.mail_search", "hub.mail_read", "hub.drive_search"]
    env = envelope.parse('{"steps": [' + ",".join(f'{{"kind":"tool","name":"{n}","args":{{}}}}' for n in names) + "]}")
    assert [s.name for s in env.steps] == names  # type: ignore[union-attr]
    assert set(names) <= set(envelope.DATA_TOOL_NAMES) <= set(TOOLS)
    with pytest.raises(envelope.EnvelopeError):
        envelope.parse('{"steps": [{"kind":"tool","name":"gmail.send","args":{}}]}')


async def test_gen_turn_reads_deals_and_opens_screen(owner_api: Api, app: Any, seed: dict[str, Any]) -> None:
    """Cả vòng: model gọi deal.list → kết quả (đã che, bọc không tin cậy) quay lại model → mở màn Deal."""
    router = FakeRouter([
        {"steps": [{"kind": "say", "text": "Để em xem Deal."},
                   {"kind": "tool", "name": "deal.list", "args": {"status": "open"}}]},
        {"steps": [{"kind": "say", "text": f"Có 1 deal mở: {seed['deal']['code']}."},
                   {"kind": "ui", "action": {"type": "navigate", "screen": "deals"}}, {"kind": "done"}]},
    ])
    t = await ask(owner_api, app, router, "Deal nào đang mở?")
    assert t["status"] == "done" and kinds(t) == ["say", "tool", "say", "ui:navigate"]
    seen_by_model = router.calls[1][-1].content
    assert "[kết quả deal.list]" in seen_by_model and seed["deal"]["code"] in seen_by_model
    assert seed["deal"]["id"] in seen_by_model
    _clean(seen_by_model)
    q = next(r for r in await gen_log() if r.action == "gen.query")
    assert q.result == "ok" and q.detail["tool"] == "deal.list"
    assert "open" not in str(q.detail.get("args_digest"))           # chỉ lưu dấu vân tay tham số, không lưu nguyên văn


async def test_sources_do_not_write_action_log(owner_api: Api, seed: dict[str, Any]) -> None:
    """Không ghi actionlog riêng cho endpoint nguồn (engine đã ghi `gen.query` mỗi lần Gen gọi tool)."""
    sql = "SELECT count(*) FROM ops.action_log"
    before = (await _sql_rows(sql))[0][0]
    for path in ("/gen/sources/documents", "/gen/sources/deals", "/gen/sources/cases",
                 f"/gen/sources/documents/{seed['doc']['id']}", f"/gen/sources/deals/{seed['deal']['id']}",
                 f"/gen/sources/cases/{seed['case']['id']}"):
        assert (await owner_api.get(path)).status_code == 200, path
    assert (await _sql_rows(sql))[0][0] == before


async def _sql_rows(sql: str) -> list[Any]:
    async with sessionmaker()() as s:
        return list((await s.execute(text(sql))).all())
