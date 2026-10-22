"""Nền chung giai đoạn 3: phạm vi dữ liệu (ngoài phạm vi → 404), chứng cứ, góc nhìn đã lưu, Bàn làm việc
(duyệt cần PIN, gửi qua bridge bằng permit dùng một lần, quyết định một lần)."""

import base64
import hashlib
import json
import uuid

import orjson
import pytest
from sqlalchemy import text

from gh import crypto
from gh.biz.core import drafts
from gh.chassis.bus import BRIDGE_OUTBOUND
from gh.data.ingest import handle_status
from gh.db import sessionmaker
from gh.refinery.runner import Refinery
from tests.conftest import OWNER, Api
from tests.phase2 import install_presets, listen, msg, org_id, put
from tests.test_rbac_api import login_as
from tests.test_refinery import BUY, by_text


@pytest.fixture
async def world(app, db, redis, owner_api):  # type: ignore[no-untyped-def]
    """Tổ chức đã khởi tạo, nhóm g1 đang nghe, một tin hỏi mua đã sàng thành đơn vị + điểm, Zalo đang chạy."""
    org = await org_id(db)
    await install_presets(db, org)
    gid = await listen(db, org, "g1")
    sm = sessionmaker()
    [raw_id] = await put(sm, org, msg(BUY))
    await Refinery(sm, redis, by_text()).run(org, "manual")  # type: ignore[arg-type]
    u = (await db.execute(text("SELECT id, person_id FROM clean.meaning_units"))).one()
    ch = (await db.execute(text("SELECT id FROM core.channels WHERE org_id = :o AND type = 'zalo'"),
                           {"o": org})).scalar()
    await db.execute(text("""INSERT INTO core.channel_sessions (channel_id, org_id, account_label, state, started_at)
                             VALUES (:c, :o, 'Zalo Sếp', 'active', now())"""), {"c": ch, "o": org})
    await db.commit()
    return {"org": org, "group": gid, "raw": raw_id, "unit": u.id, "person": u.person_id}


async def assign(db, email: str, subject_type: str, subject_id) -> None:  # type: ignore[no-untyped-def]
    await db.execute(text("""INSERT INTO core.assignments (org_id, user_id, subject_type, subject_id)
                             SELECT org_id, id, :t, :s FROM core.users WHERE email = :e"""),
                     {"t": subject_type, "s": subject_id, "e": email})
    await db.commit()


# ─── chứng cứ ─────────────────────────────────────────────────────────────────

async def test_explain_unit_score_and_raw_quote(world, owner_api: Api) -> None:  # type: ignore[no-untyped-def]
    r = await owner_api.get(f"/explain/meaning_unit/{world['unit']}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["kind"] == "meaning_unit" and body["title"] == "AskedPrice"
    [unit] = body["units"]
    [quote] = unit["quotes"]
    assert quote["quote"] == BUY and quote["raw_id"] == str(world["raw"]) and quote["raw_code"].startswith("RAW-")
    assert unit["group"]["id"] == str(world["group"]) and unit["person"]["id"] == str(world["person"])

    r = await owner_api.get(f"/explain/score/person:{world['person']}:heat")
    assert r.status_code == 200, r.text
    s = r.json()
    assert s["statement"].endswith("/100 · tin cậy 0,90") and s["method"] == "rules+model"
    assert s["factors"][0]["evidence"] == [{"type": "meaning_unit", "id": str(world["unit"])}]
    assert [u["id"] for u in s["units"]] == [str(world["unit"])] and len(s["history"]) == 1

    r = await owner_api.get(f"/explain/raw/{world['raw']}")
    assert r.status_code == 200 and r.json()["text"] == BUY

    assert (await owner_api.get("/explain/score/person:not-a-uuid:heat")).status_code == 404
    assert (await owner_api.get(f"/explain/khong-co/{world['unit']}")).status_code == 404


async def test_out_of_scope_is_404_until_assigned(world, client, db) -> None:  # type: ignore[no-untyped-def]
    staff = await login_as(client, db, "agent_staff")
    urls = [f"/explain/meaning_unit/{world['unit']}", f"/explain/score/person:{world['person']}:heat",
            f"/explain/raw/{world['raw']}"]
    for url in urls:
        r = await staff.get(url)
        assert r.status_code == 404 and r.json()["code"] == "NOT_FOUND", (url, r.text)   # không lộ là có tồn tại
    await assign(db, "agent_staff@example.vn", "person", world["person"])
    for url in urls:
        assert (await staff.get(url)).status_code == 200, url


async def test_auditor_sees_evidence_but_score_of_people_review_is_hidden(world, client, db) -> None:  # type: ignore[no-untyped-def]
    auditor = await login_as(client, db, "auditor")
    assert (await auditor.get(f"/explain/meaning_unit/{world['unit']}")).status_code == 200
    # Người không có quyền nào trong danh sách của loại chứng cứ → 403, không phải rỗng.
    r = await auditor.get("/explain/draft/00000000-0000-0000-0000-000000000000")
    assert r.status_code == 403


# ─── góc nhìn đã lưu ──────────────────────────────────────────────────────────

async def test_saved_views_are_per_user(owner_api: Api, client, db) -> None:  # type: ignore[no-untyped-def]
    r = await owner_api.send("POST", "/views", {"screen": "directory", "name": "Khách nóng", "filters": {"heat": 70}})
    assert r.status_code == 201, r.text
    vid = r.json()["id"]
    dup = await owner_api.send("POST", "/views", {"screen": "directory", "name": "khách NÓNG"})
    assert dup.status_code == 409 and dup.json()["code"] == "VIEW_EXISTS"
    bad = await owner_api.send("POST", "/views", {"screen": "khong-co", "name": "x"})
    assert bad.status_code == 422
    assert [v["name"] for v in (await owner_api.get("/views?screen=directory")).json()] == ["Khách nóng"]

    other = await login_as(client, db, "manager")
    assert (await other.get("/views?screen=directory")).json() == []
    assert (await other.send("DELETE", f"/views/{vid}")).status_code == 404
    assert (await owner_api.send("DELETE", f"/views/{vid}")).status_code == 204
    assert (await owner_api.get("/views?screen=directory")).json() == []


# ─── Bàn làm việc ─────────────────────────────────────────────────────────────

def _claims(token: str) -> dict:  # type: ignore[type-arg]
    head, sig = token.split(".")
    pad = lambda s: s + "=" * (-len(s) % 4)  # noqa: E731
    assert crypto.hmac_verify(head.encode(), base64.urlsafe_b64decode(pad(sig)))
    return json.loads(base64.urlsafe_b64decode(pad(head)))  # type: ignore[no-any-return]


async def _outbound(redis) -> list[dict]:  # type: ignore[no-untyped-def, type-arg]
    rows = await redis.xrange(BRIDGE_OUTBOUND)
    return [{"type": f[b"type"].decode(), **orjson.loads(f[b"payload"])} for _, f in rows]


async def test_drafts_cursor_pagination_reaches_second_page(world, owner_api: Api) -> None:  # type: ignore[no-untyped-def]
    """`created_at < :c` với `:c` lấy từ `next_cursor` của trang trước — asyncpg từ chối bind chuỗi ISO trực
    tiếp vào một cột `timestamptz` nếu không ép kiểu ở Python trước (`gh.data.common.parse_cursor`)."""
    for i in range(3):
        r = await owner_api.send("POST", "/drafts", {"kind": "message", "title": f"D{i}", "text": "nội dung"})
        assert r.status_code == 201, r.text
    page1 = (await owner_api.get("/drafts?limit=2")).json()
    assert len(page1["items"]) == 2 and page1["total"] == 3 and page1["next_cursor"]
    page2 = (await owner_api.get(f"/drafts?limit=2&cursor={page1['next_cursor']}")).json()
    assert len(page2["items"]) == 1 and page2["next_cursor"] is None
    assert {i["id"] for i in page1["items"]} != {i["id"] for i in page2["items"]}


async def test_draft_approve_needs_pin_then_sends_with_single_use_permit(world, owner_api: Api, db, redis, app) -> None:  # type: ignore[no-untyped-def]
    text_ = "Dạ em gửi anh báo giá 3 container thép cuộn ạ."
    r = await owner_api.send("POST", "/drafts", {
        "kind": "message", "title": "Trả lời hỏi giá", "text": text_,
        "target": {"channel": "zalo", "thread_type": "group", "group_id": str(world["group"])},
        "subject": {"type": "person", "id": str(world["person"])},
        "sources": [{"label": "Hỏi giá 3 container", "ref": {"type": "meaning_unit", "id": str(world["unit"])}}]})
    assert r.status_code == 201, r.text
    d = r.json()
    assert d["status"] == "pending" and d["flags"]["writes_external"] is True     # ghi ra ngoài: luôn chờ duyệt
    assert d["approve_label"] == "Duyệt và gửi qua Zalo" and d["target"]["group"]["id"] == str(world["group"])
    assert any(c["key"] == "đối tượng" for c in d["context"])
    listed = (await owner_api.get("/drafts")).json()
    assert [i["id"] for i in listed["items"]] == [d["id"]] and listed["total"] == 1

    ex = (await owner_api.get(f"/explain/draft/{d['id']}")).json()
    assert [u["id"] for u in ex["units"]] == [str(world["unit"])]

    r = await owner_api.send("POST", f"/drafts/{d['id']}/approve", {})
    assert r.status_code == 423 and r.json()["code"] == "PIN_REQUIRED"
    assert (await owner_api.send("POST", "/auth/pin/verify", {"pin": OWNER["pin"]})).status_code == 200
    r = await owner_api.send("POST", f"/drafts/{d['id']}/approve", {})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "approved" and r.json()["decision"]["by"]["name"] == OWNER["display_name"]

    [send] = await _outbound(redis)
    assert send["type"] == "message.send" and send["text"] == text_ and send["thread_id"] == "g1"
    claims = _claims(send["permit"])
    assert claims["draft_id"] == d["id"] and claims["thread_type"] == "group"
    assert claims["body_sha256"] == hashlib.sha256(text_.encode()).hexdigest()
    stored = (await db.execute(text("SELECT permit_hash FROM biz.action_drafts WHERE id = :i"),
                               {"i": d["id"]})).scalar_one()
    assert drafts.permit_matches(send["permit"], bytes(stored))

    # Quyết định lần hai (vd. người khác bấm cùng lúc) → 409.
    again = await owner_api.send("POST", f"/drafts/{d['id']}/reject", {})
    assert again.status_code == 409 and again.json()["code"] == "DRAFT_DECIDED"

    result = {"session_id": send["session_id"], "nonce": claims["nonce"], "draft_id": d["id"], "channel": "zalo",
              "ok": True, "external_msg_id": "zmsg-1"}
    async with sessionmaker()() as s:
        await handle_status(s, redis, app.state.bus, world["org"], "send.result", result)
        await handle_status(s, redis, app.state.bus, world["org"], "send.result", {**result, "ok": False})
        await s.commit()
    done = (await owner_api.get(f"/drafts/{d['id']}")).json()
    assert done["status"] == "sent" and done["send_result"]["external_msg_id"] == "zmsg-1"   # lần sau không đè
    log = (await db.execute(text("""SELECT action FROM ops.action_log WHERE target_id = :i ORDER BY at"""),
                            {"i": d["id"]})).scalars().all()
    assert log[0] == "draft.created" and "draft.approved" in log and "draft.sent" in log
    assert "draft.failed" not in log                        # báo cáo trùng của bridge không đổi trạng thái
    assert (await owner_api.get("/drafts")).json()["total"] == 0
    assert (await owner_api.get("/drafts?status=decided")).json()["total"] == 1


async def test_edit_send_keeps_versions_and_reject_sends_nothing(world, owner_api: Api, redis) -> None:  # type: ignore[no-untyped-def]
    await owner_api.send("POST", "/auth/pin/verify", {"pin": OWNER["pin"]})
    target = {"channel": "zalo", "thread_type": "group", "group_id": str(world["group"])}
    a = (await owner_api.send("POST", "/drafts", {"kind": "message", "title": "A", "text": "Bản gốc",
                                                    "target": target})).json()
    b = (await owner_api.send("POST", "/drafts", {"kind": "message", "title": "B", "text": "Không gửi",
                                                    "target": target})).json()
    empty = await owner_api.send("POST", f"/drafts/{a['id']}/edit-send", {"text": "  "})
    assert empty.status_code == 422
    r = await owner_api.send("POST", f"/drafts/{a['id']}/edit-send", {"text": "Bản đã sửa"})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "edited" and r.json()["text"] == "Bản đã sửa"
    assert [v["text"] for v in r.json()["versions"]] == ["Bản gốc"]
    r = await owner_api.send("POST", f"/drafts/{b['id']}/reject", {"reason": "Sai giá"})
    assert r.json()["status"] == "rejected" and r.json()["decision"]["reason"] == "Sai giá"
    sends = await _outbound(redis)
    assert [s["text"] for s in sends] == ["Bản đã sửa"]


async def test_send_fails_cleanly_without_active_session_or_target(world, owner_api: Api, db, redis) -> None:  # type: ignore[no-untyped-def]
    await owner_api.send("POST", "/auth/pin/verify", {"pin": OWNER["pin"]})
    no_target = (await owner_api.send("POST", "/drafts", {"kind": "message", "title": "x", "text": "y"})).json()
    r = await owner_api.send("POST", f"/drafts/{no_target['id']}/approve", {})
    assert r.json()["status"] == "failed" and r.json()["send_result"]["error"] == "NO_TARGET"

    await db.execute(text("UPDATE core.channel_sessions SET state = 'logged_out', ended_at = now()"))
    await db.commit()
    d = (await owner_api.send("POST", "/drafts", {
        "kind": "message", "title": "x", "text": "y",
        "target": {"channel": "zalo", "thread_type": "group", "group_id": str(world["group"])}})).json()
    r = await owner_api.send("POST", f"/drafts/{d['id']}/approve", {})
    assert r.json()["status"] == "failed" and r.json()["send_result"]["error"] == "SESSION_NOT_ACTIVE"
    assert await _outbound(redis) == []


async def test_bridge_dropping_mid_send_expires_the_permit_instead_of_hanging(world, owner_api: Api, db,  # type: ignore[no-untyped-def]
                                                                               redis, app) -> None:
    """Giai đoạn 5.4: bridge rớt kết nối SAU khi nhận lệnh gửi (permit đã cấp, tin đã lên BRIDGE_OUTBOUND) nhưng
    TRƯỚC khi báo `send.result` — mô phỏng thật, không mock quyết định. Bản nháp không được phép kẹt "approved"
    (trông như đang chờ) mãi mãi: quét permit hết hạn (`expire_stale_permits`) phải chuyển nó sang `failed` rõ
    ràng, ghi Action Log, và không đụng tới các bản nháp permit còn hạn."""
    await owner_api.send("POST", "/auth/pin/verify", {"pin": OWNER["pin"]})
    target = {"channel": "zalo", "thread_type": "group", "group_id": str(world["group"])}
    stuck = (await owner_api.send("POST", "/drafts", {"kind": "message", "title": "Kẹt", "text": "Đang gửi dở",
                                                       "target": target})).json()
    fresh = (await owner_api.send("POST", "/drafts", {"kind": "message", "title": "Còn hạn", "text": "Chưa hết hạn",
                                                       "target": target})).json()
    assert (await owner_api.send("POST", f"/drafts/{stuck['id']}/approve", {})).json()["status"] == "approved"
    assert (await owner_api.send("POST", f"/drafts/{fresh['id']}/approve", {})).json()["status"] == "approved"
    assert len(await _outbound(redis)) == 2                 # tin đã lên hàng đợi bridge — bridge rớt SAU đây

    # Bridge không bao giờ gọi lại `send.result` cho "stuck" (mất kết nối giữa chừng). Mô phỏng hết hạn permit
    # (PERMIT_TTL_S = 300s) bằng cách lùi mốc hết hạn về quá khứ thay vì chờ thật.
    await db.execute(text("UPDATE biz.action_drafts SET permit_expires_at = now() - interval '1 second' "
                          "WHERE id = :i"), {"i": stuck["id"]})
    await db.commit()

    expired = await drafts.expire_stale_permits(db, redis)
    await db.commit()
    assert expired == [uuid.UUID(stuck["id"])]

    got_stuck = (await owner_api.get(f"/drafts/{stuck['id']}")).json()
    assert got_stuck["status"] == "failed" and got_stuck["send_result"]["error"] == "PERMIT_EXPIRED"
    got_fresh = (await owner_api.get(f"/drafts/{fresh['id']}")).json()
    assert got_fresh["status"] == "approved"                 # còn hạn: không đụng tới, có thể vẫn đang gửi thật
    log = (await db.execute(text("SELECT action, result FROM ops.action_log WHERE target_id = :i ORDER BY at"),
                            {"i": stuck["id"]})).all()
    assert ("draft.failed", "failed") in [(row.action, row.result) for row in log]

    # Bridge (hoặc thao tác thủ công) gửi lại `send.result` trễ cho tin đã bị đánh hết hạn: KHÔNG được ghi đè —
    # `on_send_result` chỉ áp dụng khi status còn 'approved'/'edited' và permit_used_at IS NULL.
    stuck_send = next(s for s in await _outbound(redis) if _claims(s["permit"])["draft_id"] == stuck["id"])
    claims = _claims(stuck_send["permit"])
    result = {"session_id": stuck_send["session_id"], "nonce": claims["nonce"], "draft_id": stuck["id"],
              "channel": "zalo", "ok": True, "external_msg_id": "zmsg-trễ"}
    async with sessionmaker()() as s:
        await handle_status(s, redis, app.state.bus, world["org"], "send.result", result)
        await s.commit()
    still_failed = (await owner_api.get(f"/drafts/{stuck['id']}")).json()
    assert still_failed["status"] == "failed"                # không bị đè lại thành "sent" trễ


async def test_internal_reminder_draft_creates_task_on_approve(world, owner_api: Api, db) -> None:  # type: ignore[no-untyped-def]
    await owner_api.send("POST", "/auth/pin/verify", {"pin": OWNER["pin"]})
    d = (await owner_api.send("POST", "/drafts", {"kind": "reminder", "title": "Gọi lại chị Lan thứ 5",
                                                    "text": "Gọi lại", "subject": {"type": "person",
                                                                                   "id": str(world["person"])}})).json()
    assert d["status"] == "pending" and d["approve_label"] == "Duyệt và thực hiện"
    r = await owner_api.send("POST", f"/drafts/{d['id']}/approve", {})
    assert r.json()["status"] == "sent"
    task = (await db.execute(text("SELECT title, source FROM biz.tasks"))).one()
    assert task.title == "Gọi lại chị Lan thứ 5" and task.source == "draft"


async def test_staff_only_sees_drafts_in_scope_and_cannot_approve(world, owner_api: Api, client, db) -> None:  # type: ignore[no-untyped-def]
    d = (await owner_api.send("POST", "/drafts", {"kind": "message", "title": "x", "text": "y",
                                                    "subject": {"type": "person", "id": str(world["person"])}})).json()
    staff = await login_as(client, db, "agent_staff")
    assert (await staff.get("/drafts")).json()["total"] == 0
    assert (await staff.get(f"/drafts/{d['id']}")).status_code == 404
    # Tạo bản nháp cho người ngoài phạm vi → 404.
    r = await staff.send("POST", "/drafts", {"kind": "message", "title": "x", "text": "y",
                                              "subject": {"type": "person", "id": str(world["person"])}})
    assert r.status_code == 404
    await assign(db, "agent_staff@example.vn", "person", world["person"])
    assert (await staff.get("/drafts")).json()["total"] == 1
    await staff.send("POST", "/auth/pin/verify", {"pin": "112233"})
    assert (await staff.send("POST", f"/drafts/{d['id']}/approve", {})).status_code == 403


async def test_translate_and_regenerate_use_model_router(world, owner_api: Api, app) -> None:  # type: ignore[no-untyped-def]
    from tests.phase2 import FakeRouter

    d = (await owner_api.send("POST", "/drafts", {"kind": "message", "title": "x", "text": "Chào anh"})).json()
    app.state.model_router = FakeRouter(lambda m: "Hello")
    r = await owner_api.send("POST", f"/drafts/{d['id']}/translate", {"lang": "en"})
    assert r.status_code == 200 and r.json() == {"lang": "en", "text": "Hello"}
    r = await owner_api.send("POST", f"/drafts/{d['id']}/regenerate", {})
    assert r.status_code == 200 and r.json()["text"] == "Hello"
    assert [v["text"] for v in r.json()["versions"]] == ["Chào anh"]
    app.state.model_router = FakeRouter(down=True)
    r = await owner_api.send("POST", f"/drafts/{d['id']}/translate", {"lang": "en"})
    assert r.status_code == 503 and r.json()["code"] == "MODEL_UNAVAILABLE"


async def test_model_unavailable_reasons_are_hidden_from_non_owner(world, owner_api: Api, client, db,  # type: ignore[no-untyped-def]
                                                                  app) -> None:
    """v0.1.30: lý do kỹ thuật (tên nguồn, nhãn khoá, lỗi gốc) chỉ trả cho Owner; nhân viên thấy câu chung."""
    from tests.phase2 import FakeRouter

    d = (await owner_api.send("POST", "/drafts", {"kind": "message", "title": "x", "text": "y",
                                                    "subject": {"type": "person", "id": str(world["person"])}})).json()
    staff = await login_as(client, db, "agent_staff")
    await assign(db, "agent_staff@example.vn", "person", world["person"])
    app.state.model_router = FakeRouter(down=True)
    for path, payload in ((f"/drafts/{d['id']}/translate", {"lang": "en"}), (f"/drafts/{d['id']}/regenerate", {})):
        r = await staff.send("POST", path, payload)
        assert r.status_code == 503 and r.json()["code"] == "MODEL_UNAVAILABLE"
        assert r.json()["reasons"] == [] and isinstance(r.json()["detail"], str)


async def test_note_side_action_defaults_to_a_valid_notebook_section(world, db) -> None:  # type: ignore[no-untyped-def]
    await drafts._execute_internal(db, world["org"], None, "note.write", "Khách thích gọi buổi sáng",
                                   ("person", world["person"]), None)
    await db.commit()
    row = (await db.execute(text("""SELECT e.section FROM memory.entries e
                                    JOIN memory.notebooks n ON n.id = e.notebook_id
                                    WHERE n.subject_id = :p AND e.body = :b"""),
                            {"p": world["person"], "b": "Khách thích gọi buổi sáng"})).one()
    assert row.section == "rolling_context"
