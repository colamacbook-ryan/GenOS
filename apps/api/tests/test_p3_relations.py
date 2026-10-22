"""Quan hệ & Đối tượng: Nhóm & Con người (5 hàng bộ lọc, BOT + tự trị riêng), Hồ sơ sống (danh tính đa kênh, 5
điểm + vì sao, mức tự trị, ghi chú tay/hệ thống), Sổ tay nhận thức (ghim/sửa/xoá/nén ngay/đặt lại, lịch sử nén,
đã nén vẫn truy được), Tài liệu (ACL theo vai trò/cá nhân/nhóm)."""

import base64
import uuid

import pytest
from sqlalchemy import text

from gh.memory import notebook
from tests.conftest import Api, verify_pin
from tests.phase2 import listen, msg, org_id, put
from tests.test_rbac_api import login_as


async def _person_of(db, raw_id) -> uuid.UUID:  # type: ignore[no-untyped-def]
    return (await db.execute(text("""SELECT pi.person_id FROM raw.events e
                                     JOIN core.person_identities pi ON pi.id = e.sender_identity_id
                                     WHERE e.id = :r"""), {"r": raw_id})).scalar_one()


async def assign_scope(db, email: str, subject_type: str, subject_id) -> None:  # type: ignore[no-untyped-def]
    await db.execute(text("""INSERT INTO core.assignments (org_id, user_id, subject_type, subject_id)
                             SELECT org_id, id, :t, :s FROM core.users WHERE email = :e"""),
                     {"t": subject_type, "s": subject_id, "e": email})
    await db.commit()


async def set_heat(db, org, subject_type: str, subject_id, value: float) -> None:  # type: ignore[no-untyped-def]
    snap = (await db.execute(text("""INSERT INTO clean.score_snapshots (org_id, subject_type, subject_id,
                                                                        dimension, value, confidence, explanation)
        VALUES (:o, :t, :s, 'heat', :v, 0.9, '{}') RETURNING id"""),
        {"o": org, "t": subject_type, "s": subject_id, "v": value})).scalar_one()
    await db.execute(text("""INSERT INTO clean.current_scores (subject_type, subject_id, dimension, value, trend,
                                                                snapshot_id, updated_at)
        VALUES (:t, :s, 'heat', :v, 'up', :snap, now())"""),
        {"t": subject_type, "s": subject_id, "v": value, "snap": snap})


@pytest.fixture
async def world(app, db, redis, owner_api):  # type: ignore[no-untyped-def]
    """Tổ chức đã khởi tạo, nhóm g1, hai khách hàng: A (nóng, có cơ hội lớn, than phiền → P1), B (nguội, không
    cơ hội, không tín hiệu → P3 mặc định)."""
    from gh.db import sessionmaker

    org = await org_id(db)
    gid = await listen(db, org, "g1")
    sm = sessionmaker()
    [raw_a] = await put(sm, org, msg("Sao chưa ai trả lời, chán quá", sender="a1", name="Chị Lan"))
    [raw_b] = await put(sm, org, msg("Chào shop", sender="b1", name="Anh Bình"))
    await db.commit()
    pa = await _person_of(db, raw_a)
    pb = await _person_of(db, raw_b)
    await db.execute(text("""UPDATE core.persons SET person_type = 'customer', relation_to_owner = 'direct'
                             WHERE id = :p"""), {"p": pa})
    await db.execute(text("""UPDATE core.persons SET person_type = 'customer', relation_to_owner = 'stranger'
                             WHERE id = :p"""), {"p": pb})
    await db.execute(text("""INSERT INTO clean.meaning_units (org_id, observed_at, group_id, person_id, event_type,
                                                               conclusion, confidence, run_id)
        VALUES (:o, now(), :g, :p, 'Complained', 'Than phiền chưa ai trả lời', 0.9, core.uuid_v7())"""),
        {"o": org, "g": gid, "p": pa})
    opp_code = (await db.execute(text("SELECT core.next_code('OPP')"))).scalar_one()
    await db.execute(text("""INSERT INTO biz.opportunities (org_id, code, person_id, need, stage, value_vnd,
                                                             confidence, first_signal_at)
        VALUES (:o, :c, :p, 'Mua thép cuộn', 'validated', 600000000, 'high', now())"""),
        {"o": org, "c": opp_code, "p": pa})
    await set_heat(db, org, "person", pa, 87.0)
    await db.commit()
    return {"org": org, "group": gid, "pa": pa, "pb": pb}


# ═══ Nhóm & Con người ════════════════════════════════════════════════════════

async def test_people_five_filter_rows_and_buckets(world, owner_api: Api) -> None:  # type: ignore[no-untyped-def]
    body = (await owner_api.get("/directory/people")).json()
    by_id = {i["id"]: i for i in body["items"]}
    a, b = by_id[str(world["pa"])], by_id[str(world["pb"])]
    assert a["relation"] == "direct" and a["heat"] == 87.0 and a["value_vnd"] == 600_000_000
    assert a["priority"] == "P1"                              # than phiền → P1 qua biz.inbox_items
    assert b["relation"] == "stranger" and b["heat"] is None and b["value_vnd"] is None and b["priority"] == "P3"

    high_heat = (await owner_api.get("/directory/people?heat=high")).json()
    assert {i["id"] for i in high_heat["items"]} == {str(world["pa"])}
    cold = (await owner_api.get("/directory/people?heat=cold")).json()
    assert {i["id"] for i in cold["items"]} == {str(world["pb"])}

    high_value = (await owner_api.get("/directory/people?value=high")).json()
    assert {i["id"] for i in high_value["items"]} == {str(world["pa"])}
    unknown_value = (await owner_api.get("/directory/people?value=unknown")).json()
    assert {i["id"] for i in unknown_value["items"]} == {str(world["pb"])}

    p1 = (await owner_api.get("/directory/people?priority=P1")).json()
    assert {i["id"] for i in p1["items"]} == {str(world["pa"])}

    direct = (await owner_api.get("/directory/people?relation=direct")).json()
    assert {i["id"] for i in direct["items"]} == {str(world["pa"])}

    unassigned = (await owner_api.get("/directory/people?bot=unassigned")).json()
    assert {str(world["pa"]), str(world["pb"])} <= {i["id"] for i in unassigned["items"]}


async def test_people_scope_by_role(world, owner_api: Api, client, db) -> None:  # type: ignore[no-untyped-def]
    staff = await login_as(client, db, "agent_staff")
    empty = (await staff.get("/directory/people")).json()
    assert empty["total"] == 0
    await assign_scope(db, "agent_staff@example.vn", "person", world["pa"])
    scoped = (await staff.get("/directory/people")).json()
    assert {i["id"] for i in scoped["items"]} == {str(world["pa"])}


async def test_directory_bot_assignment_person_and_group(world, owner_api: Api, db) -> None:  # type: ignore[no-untyped-def]
    agent_id = (await db.execute(text("""INSERT INTO agent.identities (org_id, name, role_desc, template,
        addressing, voice, speak_when, forbidden, autonomy_level, is_enabled)
        VALUES (:o, 'Trợ lý thương mại', 'Báo giá', 'commercial', '{}', 'lễ phép', 'khi được tag', '{}', 3, true)
        RETURNING id"""), {"o": world["org"]})).scalar_one()
    await db.commit()
    await verify_pin(owner_api)   # review v0.1.65 P2-3: gán BOT cho người cần Owner + PIN policy.change

    r = await owner_api.send("POST", f"/directory/people/{world['pa']}/bot",
                             {"agent_id": str(agent_id), "autonomy_level": 4})
    assert r.status_code == 200, r.text
    row = (await owner_api.get("/directory/people?bot=assigned")).json()
    [item] = [i for i in row["items"] if i["id"] == str(world["pa"])]
    assert item["bot"]["id"] == str(agent_id) and item["autonomy_level"] == 4

    gr = await owner_api.send("POST", f"/directory/groups/{world['group']}/bot", {"agent_id": str(agent_id)})
    assert gr.status_code == 200, gr.text
    groups = (await owner_api.get("/directory/groups")).json()
    [g] = [x for x in groups["items"] if x["id"] == str(world["group"])]
    assert g["bot"]["id"] == str(agent_id)

    log = (await db.execute(text("SELECT action FROM ops.action_log WHERE action LIKE 'directory.%' ORDER BY at"))
          ).scalars().all()
    assert log == ["directory.bot_assigned", "directory.group_bot_set"]


# ═══ Hồ sơ sống ══════════════════════════════════════════════════════════════

async def test_profile_identities_scores_autonomy_and_note(world, owner_api: Api) -> None:  # type: ignore[no-untyped-def]
    r = await owner_api.get(f"/profile/{world['pa']}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["person"]["id"] == str(world["pa"])
    assert [i["channel"]["type"] for i in body["identities"]] == ["zalo"]
    heat = next(s for s in body["scores"] if s["dimension"] == "heat")
    assert heat["value"] == 87.0 and heat["label"] == "độ nóng"
    assert body["autonomy_level"] is None and body["owner_note"] is None
    assert body["summary"] and body["summary"][0]["tone"] == "bad"          # Complained → bad
    assert [t["event_type"] for t in body["timeline"]] == ["Complained"]

    patched = await owner_api.send("PATCH", f"/profile/{world['pa']}",
                                   {"autonomy_level": 5, "note": "Khách VIP, ưu tiên gọi trước khi nhắn"})
    assert patched.status_code == 200, patched.text
    assert patched.json()["autonomy_level"] == 5
    assert patched.json()["owner_note"] == "Khách VIP, ưu tiên gọi trước khi nhắn"

    cleared = await owner_api.send("PATCH", f"/profile/{world['pa']}", {"note": None})
    assert cleared.json()["owner_note"] is None


async def test_profile_owner_assignment_needs_active_user_and_out_of_scope_404(  # type: ignore[no-untyped-def]
        world, owner_api: Api, client, db) -> None:
    bad = await owner_api.send("PATCH", f"/profile/{world['pa']}", {"owner_user_id": str(uuid.uuid4())})
    assert bad.status_code == 404
    staff = await login_as(client, db, "agent_staff")
    assert (await staff.get(f"/profile/{world['pb']}")).status_code == 404
    assert (await staff.send("PATCH", f"/profile/{world['pb']}", {"autonomy_level": 2})).status_code == 404


async def test_profile_merge_history(world, owner_api: Api, db) -> None:  # type: ignore[no-untyped-def]
    """Hồ sơ hợp nhất: `core.identity_merge_log` của người còn sống hiện đủ, hai chiều from/to."""
    await db.execute(text("""INSERT INTO core.identity_merge_log (org_id, op, from_person, to_person, identities, at)
        VALUES (:o, 'merge', :b, :a, '{}', now())"""), {"o": world["org"], "b": world["pb"], "a": world["pa"]})
    await db.execute(text("UPDATE core.persons SET merged_into_id = :a WHERE id = :b"),
                     {"a": world["pa"], "b": world["pb"]})
    await db.commit()
    body = (await owner_api.get(f"/profile/{world['pa']}")).json()
    [m] = body["merge_history"]
    assert m["from_person"] == str(world["pb"]) and m["to_person"] == str(world["pa"]) and m["op"] == "merge"


async def test_profile_touchpoints_from_action_log(world, owner_api: Api, db) -> None:  # type: ignore[no-untyped-def]
    await owner_api.send("PATCH", f"/profile/{world['pa']}", {"autonomy_level": 3})   # ghi action log target=person
    body = (await owner_api.get(f"/profile/{world['pa']}")).json()
    owner_id = (await db.execute(text("SELECT id FROM core.users WHERE email = 'owner@example.vn'"))).scalar_one()
    assert any(t["id"] == str(owner_id) for t in body["touchpoints"])


# ═══ Sổ tay nhận thức ════════════════════════════════════════════════════════

async def test_notebook_subjects_and_payload(world, owner_api: Api) -> None:  # type: ignore[no-untyped-def]
    r = await owner_api.send("POST", f"/notebook/person/{world['pa']}/entries",
                             {"section": "preferences", "body": "Thích trao đổi qua Zalo buổi sáng", "pinned": True})
    assert r.status_code == 201, r.text
    lst = (await owner_api.get("/notebook/subjects?type=person")).json()
    assert any(i["id"] == str(world["pa"]) and i["entries"] == 1 for i in lst["items"])

    nb = (await owner_api.get(f"/notebook/person/{world['pa']}")).json()
    assert nb["subject"]["id"] == str(world["pa"])
    prefs = next(s for s in nb["sections"] if s["key"] == "preferences")
    assert prefs["entries"][0]["pinned"] is True and prefs["entries"][0]["editable"] is True
    assert prefs["entries"][0]["author"]["type"] == "user"


async def test_notebook_system_entries_readonly_but_pinnable(world, owner_api: Api, db) -> None:  # type: ignore[no-untyped-def]
    eid = await notebook.append(db, world["org"], "person", world["pa"], "rolling_context",
                                "Khách hỏi giá thép hôm qua", [], "agent:core.refinery")
    await db.commit()
    body = (await owner_api.get(f"/notebook/person/{world['pa']}")).json()
    ctx = next(s for s in body["sections"] if s["key"] == "rolling_context")
    assert ctx["entries"][0]["editable"] is False

    blocked = await owner_api.send("PATCH", f"/notebook/person/{world['pa']}/entries/{eid}", {"body": "sửa lại"})
    assert blocked.status_code == 409 and blocked.json()["code"] == "SYSTEM_ENTRY_READONLY"
    deleted = await owner_api.send("DELETE", f"/notebook/person/{world['pa']}/entries/{eid}")
    assert deleted.status_code == 409

    pinned = await owner_api.send("PATCH", f"/notebook/person/{world['pa']}/entries/{eid}", {"pinned": True})
    assert pinned.status_code == 200                                   # ghim được dù do agent ghi
    after = (await owner_api.get(f"/notebook/person/{world['pa']}")).json()
    ctx2 = next(s for s in after["sections"] if s["key"] == "rolling_context")
    assert ctx2["entries"][0]["pinned"] is True


async def test_notebook_edit_creates_new_entry_and_delete_archives(world, owner_api: Api) -> None:  # type: ignore[no-untyped-def]
    created = await owner_api.send("POST", f"/notebook/person/{world['pa']}/entries",
                                   {"section": "open_threads", "body": "Chờ gửi báo giá"})
    eid = created.json()["id"]
    edited = await owner_api.send("PATCH", f"/notebook/person/{world['pa']}/entries/{eid}",
                                  {"body": "Đã gửi báo giá, chờ phản hồi"})
    assert edited.status_code == 200
    new_id = edited.json()["id"]
    assert new_id != eid
    body = (await owner_api.get(f"/notebook/person/{world['pa']}")).json()
    thread = next(s for s in body["sections"] if s["key"] == "open_threads")
    assert [e["id"] for e in thread["entries"]] == [new_id]

    gone = await owner_api.send("DELETE", f"/notebook/person/{world['pa']}/entries/{new_id}")
    assert gone.status_code == 204
    after = (await owner_api.get(f"/notebook/person/{world['pa']}")).json()
    assert not next(s for s in after["sections"] if s["key"] == "open_threads")["entries"]


async def test_notebook_compact_history_and_dropped_still_reachable(world, owner_api: Api) -> None:  # type: ignore[no-untyped-def]
    for _ in range(3):
        r = await owner_api.send("POST", f"/notebook/person/{world['pa']}/entries",
                                 {"section": "rolling_context", "body": "x" * 1800})
        assert r.status_code == 201
    compacted = await owner_api.send("POST", f"/notebook/person/{world['pa']}/compact", None)
    assert compacted.status_code == 200, compacted.text
    assert compacted.json()["compaction_no"] == 1

    hist = (await owner_api.get(f"/notebook/person/{world['pa']}/history")).json()
    assert len(hist) == 1 and hist[0]["compaction_no"] == 1 and hist[0]["archived"] >= 1

    dropped = (await owner_api.get(f"/notebook/person/{world['pa']}/dropped")).json()
    assert len(dropped["items"]) >= 1                                   # đã nén khỏi ngữ cảnh, vẫn truy được


async def test_notebook_reset_keeps_pinned_and_guardrails(world, owner_api: Api, db) -> None:  # type: ignore[no-untyped-def]
    pinned_id = await notebook.append(db, world["org"], "person", world["pa"], "attention_now", "Việc gấp",
                                      [], "user:owner", pinned=True)
    guard_id = await notebook.append(db, world["org"], "person", world["pa"], "guardrails", "Không tự cam kết giá",
                                     [], "user:owner")
    plain_id = await notebook.append(db, world["org"], "person", world["pa"], "open_threads", "Việc bình thường",
                                     [], "user:owner")
    await db.commit()

    r = await owner_api.send("POST", f"/notebook/person/{world['pa']}/reset", None)
    assert r.status_code == 200, r.text
    body = r.json()
    ids = {e["id"] for s in body["sections"] for e in s["entries"]}
    assert str(pinned_id) in ids and str(guard_id) in ids and str(plain_id) not in ids
    log = (await db.execute(text("SELECT detail FROM ops.action_log WHERE action = 'notebook.reset'"))).scalar_one()
    assert log["archived"] == 1


# ═══ Tài liệu ═════════════════════════════════════════════════════════════════

async def test_documents_upload_download_and_default_acl(world, owner_api: Api, db) -> None:  # type: ignore[no-untyped-def]
    content = base64.b64encode(b"noi dung hop dong").decode()
    r = await owner_api.send("POST", "/documents", {"title": "Hợp đồng A", "filename": "hd.txt", "mime": "text/plain",
                                                     "content_base64": content,
                                                     "owner_person_id": str(world["pa"])})
    assert r.status_code == 201, r.text
    doc = r.json()
    assert doc["bytes"] == len(b"noi dung hop dong") and doc["source"] == "tay"
    principals = {a["principal"] for a in doc["acl"]}
    assert "role:owner" in principals

    dl = await owner_api.get(f"/documents/{doc['id']}/content")
    assert dl.status_code == 200 and dl.content == b"noi dung hop dong"

    lst = (await owner_api.get(f"/documents?owner_person_id={world['pa']}")).json()
    assert doc["id"] in {i["id"] for i in lst["items"]}

    deleted = await owner_api.send("DELETE", f"/documents/{doc['id']}", None)
    assert deleted.status_code == 204
    assert (await owner_api.get(f"/documents/{doc['id']}")).status_code == 404


async def _upload(owner_api: Api, mime: str, body: bytes, filename: str = "f.bin", title: str = "Tệp thử") -> str:
    r = await owner_api.send("POST", "/documents", {"title": title, "filename": filename, "mime": mime,
                                                     "content_base64": base64.b64encode(body).decode()})
    assert r.status_code == 201, r.text
    return str(r.json()["id"])


def _assert_sandboxed(dl) -> None:  # type: ignore[no-untyped-def]
    csp = dl.headers["content-security-policy"]
    assert "sandbox" in csp and "default-src 'none'" in csp
    assert dl.headers["x-content-type-options"] == "nosniff"
    assert dl.headers["cross-origin-resource-policy"] == "same-origin"
    assert "no-store" in dl.headers["cache-control"]


@pytest.mark.parametrize(("mime", "body"), [
    ("text/html", b"<script>alert(1)</script>"),
    ("application/javascript", b"alert(1)"),
    ("image/svg+xml", b'<svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)"/>'),
    ("text/xml", b"<x/>"),
])
async def test_documents_active_content_forced_download(  # type: ignore[no-untyped-def]
        world, owner_api: Api, mime: str, body: bytes) -> None:
    """F-5: tệp có thể chạy mã (html/js/svg/xml) không bao giờ hiển thị inline cùng origin."""
    doc_id = await _upload(owner_api, mime, body)
    dl = await owner_api.get(f"/documents/{doc_id}/content")
    assert dl.status_code == 200 and dl.content == body
    assert dl.headers["content-type"].startswith("application/octet-stream")
    assert dl.headers["content-disposition"].startswith("attachment")
    _assert_sandboxed(dl)


@pytest.mark.parametrize("mime", ["image/png", "image/jpeg", "image/webp", "image/gif"])
async def test_documents_safe_mime_inline_still_sandboxed(  # type: ignore[no-untyped-def]
        world, owner_api: Api, mime: str) -> None:
    doc_id = await _upload(owner_api, mime, b"\x89PNG-or-%PDF")
    dl = await owner_api.get(f"/documents/{doc_id}/content")
    assert dl.status_code == 200
    assert dl.headers["content-type"].startswith(mime)
    assert dl.headers["content-disposition"].startswith("inline")
    assert "filename*=UTF-8''T%E1%BB%87p%20th%E1%BB%AD." in dl.headers["content-disposition"]
    _assert_sandboxed(dl)
    assert dl.headers["content-security-policy"] == "sandbox; default-src 'none'"


async def test_documents_pdf_inline_not_sandboxed(world, owner_api: Api) -> None:  # type: ignore[no-untyped-def]
    """PDF inline: KHÔNG sandbox, object-src 'self' (trình xem PDF của Chromium là plugin nhúng cùng URL — sandbox
    hoặc object-src 'none' chặn nó); vẫn default-src 'none', không cho nhúng khung, nosniff, CORP, no-store."""
    doc_id = await _upload(owner_api, "application/pdf", b"%PDF-1.4")
    dl = await owner_api.get(f"/documents/{doc_id}/content")
    assert dl.status_code == 200
    assert dl.headers["content-type"].startswith("application/pdf")
    assert dl.headers["content-disposition"].startswith("inline")
    csp = dl.headers["content-security-policy"]
    assert csp == "default-src 'none'; object-src 'self'; frame-ancestors 'none'"
    assert "sandbox" not in csp and "object-src 'none'" not in csp
    assert dl.headers["x-content-type-options"] == "nosniff"
    assert dl.headers["cross-origin-resource-policy"] == "same-origin"
    assert "no-store" in dl.headers["cache-control"]


async def test_documents_legacy_mime_with_params_forced_download(  # type: ignore[no-untyped-def]
        world, owner_api: Api, db) -> None:
    """Dòng cũ đã lưu mime viết hoa có tham số vẫn bị buộc tải xuống (thi hành lúc phục vụ)."""
    doc_id = await _upload(owner_api, "text/plain", b"<script>alert(1)</script>")
    await db.execute(text("UPDATE biz.documents SET mime = 'TEXT/HTML; charset=utf-8' WHERE id = :i"),
                     {"i": doc_id})
    await db.commit()
    dl = await owner_api.get(f"/documents/{doc_id}/content")
    assert dl.status_code == 200
    assert dl.headers["content-type"].startswith("application/octet-stream")
    assert dl.headers["content-disposition"].startswith("attachment")
    _assert_sandboxed(dl)


@pytest.mark.parametrize(("mime", "filename", "title", "expected"), [
    ("application/vnd.openxmlformats-officedocument.wordprocessingml.document", "hd.docx", "Hợp đồng",
     "H%E1%BB%A3p%20%C4%91%E1%BB%93ng.docx"),
    ("application/zip", "goi.zip", "Gói v1.2", "G%C3%B3i%20v1.2.zip"),
    ("text/html", "trang.html", "Trang.HTML", "Trang.HTML"),
])
async def test_documents_download_keeps_extension(  # type: ignore[no-untyped-def]
        world, owner_api: Api, mime: str, filename: str, title: str, expected: str) -> None:
    """F-5: tải về octet-stream thì trình duyệt không tự thêm đuôi — tên tải về phải giữ đuôi của tệp gốc."""
    doc_id = await _upload(owner_api, mime, b"PK\x03\x04", filename=filename, title=title)
    dl = await owner_api.get(f"/documents/{doc_id}/content")
    assert dl.status_code == 200
    assert dl.headers["content-type"].startswith("application/octet-stream")
    assert dl.headers["content-disposition"] == f"attachment; filename*=UTF-8''{expected}"


def test_download_name_pure() -> None:
    from gh.biz.relations.routes import _download_name

    assert _download_name("Hợp đồng", "", "org-1/abc/hd.docx") == "Hợp đồng.docx"
    assert _download_name("hd.DOCX", "", "org-1/abc/hd.docx") == "hd.DOCX"
    assert _download_name("Báo cáo", "", "org-1/abc/file") == "Báo cáo"  # không có đuôi, không có MIME
    assert _download_name("Ghi chú", "text/plain", "org-1/abc/file") == "Ghi chú.txt"  # đoán từ MIME
    assert _download_name("  ", "", "org-1/abc/a.pdf") == "tep.pdf"


def test_content_headers_pure() -> None:
    from gh.biz.relations.routes import _content_headers

    assert _content_headers(" Image/PNG ; x=1", "a")[0] == "image/png"
    for bad in ("image/svg+xml", "text/html", "text/plain", "application/x-javascript", "", None):
        media, headers = _content_headers(bad, "a b")
        assert media == "application/octet-stream"
        assert headers["Content-Disposition"].startswith("attachment; filename*=UTF-8''a%20b")
        assert headers["Content-Security-Policy"] == "sandbox; default-src 'none'"
    # Bảng chốt header theo từng MIME: chỉ PDF bỏ sandbox; ảnh và mọi thứ khác giữ sandbox.
    pdf_csp = "default-src 'none'; object-src 'self'; frame-ancestors 'none'"
    expected = {
        "application/pdf": ("application/pdf", "inline", pdf_csp),
        "image/png": ("image/png", "inline", "sandbox; default-src 'none'"),
        "image/jpeg": ("image/jpeg", "inline", "sandbox; default-src 'none'"),
        "image/webp": ("image/webp", "inline", "sandbox; default-src 'none'"),
        "image/gif": ("image/gif", "inline", "sandbox; default-src 'none'"),
        "image/svg+xml": ("application/octet-stream", "attachment", "sandbox; default-src 'none'"),
        "text/html": ("application/octet-stream", "attachment", "sandbox; default-src 'none'"),
        "application/x-pdf; x=1": ("application/octet-stream", "attachment", "sandbox; default-src 'none'"),
    }
    for mime, (media_exp, disp, csp) in expected.items():
        media, headers = _content_headers(mime, "a")
        assert media == media_exp, mime
        assert headers["Content-Disposition"].startswith(disp), mime
        assert headers["Content-Security-Policy"] == csp, mime
        assert headers["X-Content-Type-Options"] == "nosniff"
        assert headers["Cross-Origin-Resource-Policy"] == "same-origin"


async def test_documents_acl_by_role_and_scope(world, owner_api: Api, client, db) -> None:  # type: ignore[no-untyped-def]
    """Tài liệu gắn với `pb` — Agent nhân viên (mặc định phạm vi `assigned`, chưa được phân `pb`) không thấy;
    ACL cấp thêm quyền cho đúng người đó (không phải team/role) — đọc được nhưng không sửa được (`can_write`
    vẫn `false`); phạm vi mặc định (`assigned`) khi được phân trực tiếp vẫn cho sửa toàn quyền như mọi đối tượng
    khác trong hệ thống (test upload/download ở trên)."""
    content = base64.b64encode(b"bao gia").decode()
    r = await owner_api.send("POST", "/documents", {"title": "Báo giá B", "filename": "bg.txt", "mime": "text/plain",
                                                     "content_base64": content,
                                                     "owner_person_id": str(world["pb"])})
    doc_id = r.json()["id"]

    staff = await login_as(client, db, "agent_staff")
    assert (await staff.get(f"/documents/{doc_id}")).status_code == 404
    assert (await staff.send("PATCH", f"/documents/{doc_id}", {"title": "không được"})).status_code == 404

    staff_id = await _uid(db, "agent_staff@example.vn")
    granted = await owner_api.send("PUT", f"/documents/{doc_id}/acl",
                                   [{"principal": f"user:{staff_id}", "can_read": True, "can_write": False}])
    assert granted.status_code == 200, granted.text
    read_ok = await staff.get(f"/documents/{doc_id}")
    assert read_ok.status_code == 200                                   # ACL cấp thêm quyền đọc ngoài phạm vi mặc định
    still_blocked = await staff.send("PATCH", f"/documents/{doc_id}", {"title": "không được"})
    assert still_blocked.status_code == 404                             # chỉ can_read, không sửa được

    auditor = await login_as(client, db, "auditor")
    assert (await auditor.get(f"/documents/{doc_id}")).status_code == 200      # Auditor đọc toàn bộ (is_all)
    assert (await auditor.send("POST", "/documents", {"title": "x", "filename": "x.txt", "mime": "text/plain",
                                                       "content_base64": content})).status_code == 403


async def _uid(db, email: str) -> str:  # type: ignore[no-untyped-def]
    return str((await db.execute(text("SELECT id FROM core.users WHERE email = :e"), {"e": email})).scalar_one())
