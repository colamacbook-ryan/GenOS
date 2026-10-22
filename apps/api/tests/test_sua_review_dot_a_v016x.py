"""Sửa phát hiện review đợt A (v0.1.61):

- gộp người không làm bản nháp 1-1 đang chờ mất đích gửi (và đảo ngược gộp thì đích về lại);
- tin 1-1 trả lời đúng TÀI KHOẢN đã nhắn (không phải tài khoản cũ nhất), Sếp thấy tài khoản nhận trước khi duyệt;
- "Gửi lại" nối bản mới về bản lỗi cũ: bản cũ ra khỏi thẻ/bộ đếm "Gửi lỗi", bấm lần hai bị chặn, huỷ bản mới thì
  bản cũ về lại;
- Bàn làm việc lọc "Gửi lỗi" ở máy chủ (`status=failed`);
- KPI "Báo giá đã gửi" dẫn vào đúng bộ lọc đã gửi;
- tiêu đề chuông "Tin chưa gửi được" được che số nhạy cảm với vai không phải Owner.
"""
# ruff: noqa: F811 — fixture `world` nhập từ test_p3_core rồi nhận làm tham số (cách dùng chung của repo)

import uuid
from typing import Any

from sqlalchemy import text

from gh.biz.core import drafts
from gh.db import sessionmaker
from gh.identity import service as ident
from tests.conftest import OWNER, Api
from tests.phase2 import msg, org_id, put
from tests.test_gop_nguoi_v016x import _detect, _merge_pending, _two_people, _user
from tests.test_nhap_trung_thuc_v016x import _dm_unit
from tests.test_p3_core import _outbound, world  # noqa: F401 — fixture `world` dùng chung


async def _session_zalo(db: Any) -> None:
    await db.execute(text("""INSERT INTO core.channel_sessions (channel_id, org_id, account_label, state, started_at)
                             SELECT id, org_id, 'Zalo Sếp', 'active', now() FROM core.channels WHERE type = 'zalo'"""))
    await db.commit()


async def _ext_of(db: Any, person: uuid.UUID) -> str:
    return (await db.execute(text("SELECT external_id FROM core.person_identities WHERE person_id = :p"),
                             {"p": person})).scalar_one()  # type: ignore[no-any-return]


async def _dm_draft(owner_api: Api, person: uuid.UUID, title: str = "Trả lời khách") -> str:
    r = await owner_api.send("POST", "/drafts", {
        "kind": "message", "title": title, "text": "Dạ em báo giá ngay ạ",
        "target": {"channel": "zalo", "thread_type": "user", "person_id": str(person)},
        "subject": {"type": "person", "id": str(person)}})
    assert r.status_code == 201, r.text
    return r.json()["id"]  # type: ignore[no-any-return]


async def _approve(owner_api: Api, did: str) -> dict[str, Any]:
    await owner_api.send("POST", "/auth/pin/verify", {"pin": OWNER["pin"]})
    r = await owner_api.send("POST", f"/drafts/{did}/approve", {})
    assert r.status_code == 200, r.text
    return r.json()  # type: ignore[no-any-return]


# ─── gộp người vs đích gửi của bản nháp 1-1 ───────────────────────────────────

async def test_pending_dm_draft_still_sends_after_target_person_is_merged_away(  # type: ignore[no-untyped-def]
        app, db, redis, owner_api: Api) -> None:
    org = await org_id(db)
    pa, pb = await _two_people(db, org)
    ext = {pa: await _ext_of(db, pa), pb: await _ext_of(db, pb)}
    drafts_of = {p: await _dm_draft(owner_api, p) for p in (pa, pb)}
    await _session_zalo(db)
    assert await _detect(org) == 1
    out = await _merge_pending(db, org)
    kept, dropped = uuid.UUID(out["person"]["id"]), uuid.UUID(out["merged"]["id"])
    # Sau gộp hồ sơ bị gộp không còn giữ tài khoản nào — vậy mà bản nháp soạn cho NÓ vẫn phải đi được.
    assert (await db.execute(text("SELECT count(*) FROM core.person_identities WHERE person_id = :p"),
                             {"p": dropped})).scalar_one() == 0
    res = await _approve(owner_api, drafts_of[dropped])
    assert res["status"] == "approved", res                      # không còn failed/TARGET_NOT_FOUND
    assert res["send_result"] is None
    res = await _approve(owner_api, drafts_of[kept])
    assert res["status"] == "approved"
    sent = {s["thread_id"] for s in await _outbound(redis)}
    assert sent == {ext[pa], ext[pb]}                            # mỗi tin đi đúng tài khoản của người đó


async def test_revert_merge_sends_dm_to_original_profile_again(app, db, redis, owner_api: Api) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    pa, pb = await _two_people(db, org)
    await _session_zalo(db)
    assert await _detect(org) == 1
    out = await _merge_pending(db, org)
    dropped = uuid.UUID(out["merged"]["id"])
    log =(await db.execute(text("SELECT id, snapshot FROM core.identity_merge_log WHERE op = 'merge'"))).one()
    moved_identity = log.snapshot["identities"][0]
    ext = (await db.execute(text("SELECT external_id FROM core.person_identities WHERE id = :i"),
                            {"i": moved_identity})).scalar_one()
    did = await _dm_draft(owner_api, dropped)
    async with sessionmaker()() as s:
        await ident.revert(s, org, log.id, await _user(db))
        await s.commit()
    assert await _ext_of(db, dropped) == ext                     # tài khoản về lại hồ sơ cũ
    res = await _approve(owner_api, did)
    assert res["status"] == "approved"
    assert [s["thread_id"] for s in await _outbound(redis)] == [ext]


# ─── tin 1-1 đúng tài khoản đã nhắn ───────────────────────────────────────────

async def _second_account(db: Any, org: uuid.UUID, pid: uuid.UUID) -> tuple[uuid.UUID, Any]:
    """Người có THÊM một tài khoản Zalo mới hơn (cảnh sau khi gộp hồ sơ): tài khoản nhắn một tin riêng rồi được chuyển
    sang hồ sơ `pid`. Trả (id tài khoản mới, dòng raw.events của tin đó)."""
    [raw_id] = await put(sessionmaker(), org, msg("Em nhắn bằng số mới", group=None, sender="khach1-moi",
                                                  name="Chị Hoa (số mới)"))
    ident_id = (await db.execute(text("""
        UPDATE core.person_identities SET person_id = :p, first_seen_at = now() + interval '1 hour'
        WHERE external_id = 'khach1-moi' RETURNING id"""), {"p": pid})).scalar_one()
    raw = (await db.execute(text("SELECT id, received_at FROM raw.events WHERE id = :r"), {"r": raw_id})).one()
    await db.commit()
    return ident_id, raw


async def test_dm_reply_goes_to_the_account_that_wrote_not_the_oldest(app, db, redis, owner_api: Api) -> None:  # type: ignore[no-untyped-def]
    org, pid, uid = await _dm_unit(db)
    new_identity, raw = await _second_account(db, org, pid)
    # Tin gốc do tài khoản MỚI nhắn: bằng chứng (clean.evidence → raw.events.sender_identity_id) trỏ vào nó.
    await db.execute(text("""INSERT INTO clean.evidence (meaning_unit_id, meaning_observed_at, raw_event_id,
                                                raw_received_at)
                             SELECT id, observed_at, :r, :t FROM clean.meaning_units WHERE id = :u"""),
                     {"r": raw.id, "t": raw.received_at, "u": uid})
    await db.commit()
    r = await owner_api.send("POST", f"/inbox/{uid}/act", {"text": "Dạ em báo giá ngay ạ"})
    assert r.status_code == 200, r.text
    did = r.json()["draft"]["id"]
    d = (await owner_api.get(f"/drafts/{did}")).json()
    assert d["target"]["account"] == {"label": "Chị Hoa (số mới)"}       # Sếp thấy tài khoản nhận trước khi duyệt
    stored = (await db.execute(text("SELECT body->'target'->>'identity_id' FROM biz.action_drafts WHERE id = :i"),
                               {"i": did})).scalar_one()
    assert stored == str(new_identity)
    await _approve(owner_api, did)
    assert [s["thread_id"] for s in await _outbound(redis)] == ["khach1-moi"]


async def test_dm_without_evidence_picks_newest_account_and_old_drafts_without_identity_still_resolve(  # type: ignore[no-untyped-def]
        app, db, redis, owner_api: Api) -> None:
    org, pid, uid = await _dm_unit(db)
    new_identity, _ = await _second_account(db, org, pid)
    acct = await drafts.person_reply_account(db, org, pid, uid)
    assert acct == ("zalo", new_identity)                               # không có bằng chứng ⇒ tài khoản mới nhất
    # Bản nháp cũ (trước bản này) chỉ có person_id: vẫn gửi được, theo tài khoản cũ nhất của hồ sơ sống.
    did = await _dm_draft(owner_api, pid)
    d = (await owner_api.get(f"/drafts/{did}")).json()
    assert d["target"]["account"] == {"label": "Chị Hoa"}
    await _approve(owner_api, did)
    assert [s["thread_id"] for s in await _outbound(redis)] == ["khach1"]


# ─── gửi lại nối về bản lỗi cũ ───────────────────────────────────────────────

async def _failed_draft(db: Any, owner_api: Api) -> tuple[str, uuid.UUID]:
    _, pid, _ = await _dm_unit(db)
    await db.execute(text("DELETE FROM core.channel_sessions"))     # kênh ngắt ⇒ duyệt xong báo SESSION_NOT_ACTIVE
    await db.commit()
    did = await _dm_draft(owner_api, pid)
    res = await _approve(owner_api, did)
    assert res["status"] == "failed" and res["send_result"]["error"] == "SESSION_NOT_ACTIVE"
    return did, pid


def _resend_body(d: dict[str, Any], pid: uuid.UUID, old: str) -> dict[str, Any]:
    return {"kind": d["kind"], "title": d["title"], "text": d["text"], "resend_of": old,
            "target": {"channel": "zalo", "thread_type": "user", "person_id": str(pid)},
            "subject": {"type": "person", "id": str(pid)}}


async def test_resend_links_to_old_draft_and_removes_it_from_failed_list(app, db, redis, owner_api: Api) -> None:  # type: ignore[no-untyped-def]
    old, pid = await _failed_draft(db, owner_api)
    failed = (await owner_api.get("/drafts?status=failed")).json()
    assert [i["id"] for i in failed["items"]] == [old] and failed["total"] == 1
    assert failed["items"][0]["resent_by"] is None
    d = (await owner_api.get(f"/drafts/{old}")).json()

    r = await owner_api.send("POST", "/drafts", _resend_body(d, pid, old))
    assert r.status_code == 201, r.text
    new = r.json()
    assert new["status"] == "pending"
    parent = (await db.execute(text("SELECT parent_draft_id FROM biz.action_drafts WHERE id = :i"),
                               {"i": new["id"]})).scalar_one()
    assert str(parent) == old
    # Bản cũ ra khỏi thẻ "Gửi lỗi" + bộ đếm, và ghi rõ đã được gửi lại bằng bản nào.
    failed = (await owner_api.get("/drafts?status=failed")).json()
    assert failed["items"] == [] and failed["total"] == 0
    decided = {i["id"]: i for i in (await owner_api.get("/drafts?status=decided")).json()["items"]}
    assert decided[old]["status"] == "failed" and decided[old]["resent_by"] == {"id": new["id"], "code": new["code"]}
    assert (await owner_api.get(f"/drafts/{old}")).json()["resent_by"]["code"] == new["code"]

    # Bấm "Gửi lại" lần nữa: bị chặn, KHÔNG sinh thêm nháp trùng.
    r2 = await owner_api.send("POST", "/drafts", _resend_body(d, pid, old))
    assert r2.status_code == 409 and r2.json()["code"] == "ALREADY_RESENT" and new["code"] in r2.json()["title"]
    n = (await db.execute(text("SELECT count(*) FROM biz.action_drafts WHERE parent_draft_id = :i"),
                          {"i": old})).scalar_one()
    assert n == 1

    # Huỷ bản gửi lại ⇒ bản lỗi cũ về lại thẻ "Gửi lỗi", gửi lại được lần nữa.
    await owner_api.send("POST", "/auth/pin/verify", {"pin": OWNER["pin"]})
    assert (await owner_api.send("POST", f"/drafts/{new['id']}/reject", {})).status_code == 200
    failed = (await owner_api.get("/drafts?status=failed")).json()
    assert [i["id"] for i in failed["items"]] == [old] and failed["total"] == 1
    assert (await owner_api.send("POST", "/drafts", _resend_body(d, pid, old))).status_code == 201


async def test_resend_of_must_be_a_failed_draft_of_same_kind(app, db, redis, owner_api: Api) -> None:  # type: ignore[no-untyped-def]
    old, pid = await _failed_draft(db, owner_api)
    d = (await owner_api.get(f"/drafts/{old}")).json()
    pending = await _dm_draft(owner_api, pid, "Đang chờ")
    r = await owner_api.send("POST", "/drafts", _resend_body(d, pid, pending))
    assert r.status_code == 409 and r.json()["code"] == "NOT_RESENDABLE"
    r = await owner_api.send("POST", "/drafts", {**_resend_body(d, pid, old), "kind": "quotation"})
    assert r.status_code == 409 and r.json()["code"] == "NOT_RESENDABLE"
    r = await owner_api.send("POST", "/drafts", _resend_body(d, pid, str(uuid.uuid4())))
    assert r.status_code == 404


async def test_side_action_child_does_not_count_as_resend(app, db, redis, owner_api: Api) -> None:  # type: ignore[no-untyped-def]
    """Bản con khác loại (hành động kèm theo mcp_write/report) nối bằng cùng parent_draft_id, KHÔNG phải gửi lại."""
    old, _ = await _failed_draft(db, owner_api)
    org = await org_id(db)
    await drafts.create_draft(db, org_id=org, kind="mcp_write", title="Ghi hệ thống ngoài", body_text="x",
                              action_key="mcp.write", created_by=await _user(db), parent_draft_id=uuid.UUID(old))
    await db.commit()
    failed = (await owner_api.get("/drafts?status=failed")).json()
    assert [i["id"] for i in failed["items"]] == [old] and failed["items"][0]["resent_by"] is None


# ─── KPI Báo giá đã gửi ──────────────────────────────────────────────────────

async def test_quotations_kpi_links_to_sent_quotations_only(app, db, owner_api: Api) -> None:  # type: ignore[no-untyped-def]
    r = await owner_api.get("/overview")
    assert r.status_code == 200, r.text
    kpi = next(k for k in r.json()["kpis"] if k["key"] == "quotations_sent")
    assert kpi["filter"] == {"screen": "workbench", "filters": {"kind": "quotation", "status": "sent"}}, kpi


# ─── chuông Gửi lỗi che số nhạy cảm với vai không phải Owner ──────────────────

async def test_failure_bell_masks_long_numbers_for_non_owner_recipients(app, db, redis, owner_api: Api) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    uid = await _user(db)
    other = (await db.execute(text("""
        INSERT INTO core.users (org_id, email, display_name, password_hash, is_active)
        VALUES (:o, 'nv@example.test', 'Nhân viên', 'x', true) RETURNING id"""), {"o": org})).scalar_one()
    role = (await db.execute(text("SELECT id FROM core.roles WHERE org_id = :o AND code = 'operator'"),
                             {"o": org})).scalar_one()
    await db.execute(text("INSERT INTO core.user_roles (user_id, role_id) VALUES (:u, :r)"), {"u": other, "r": role})
    await db.commit()
    title = "Trả lời khách 0901234567890"
    await drafts.report_send_failure(db, redis, org_id=org, draft_id=uuid.uuid4(), code="SEND_FAILED", title=title,
                                     draft_code="ACT-1", decided_by=other, created_by=None)
    await db.commit()
    rows = {r.user_id: r.title for r in (await db.execute(text(
        "SELECT user_id, title FROM core.notifications WHERE kind = 'draft.failed'"))).all()}
    assert "0901234567890" in rows[uid]                                  # Owner thấy nguyên văn
    assert "0901234567890" not in rows[other] and rows[other].startswith("Tin chưa gửi được: ")


# ─── đích nháp: identity_id phải thuộc đúng người (review bảo mật) ────────────

async def _identity_of(db: Any, person: uuid.UUID) -> uuid.UUID:
    return (await db.execute(text("SELECT id FROM core.person_identities WHERE person_id = :p ORDER BY id LIMIT 1"),
                             {"p": person})).scalar_one()  # type: ignore[no-any-return]


def _dm_body(person: uuid.UUID, identity: uuid.UUID) -> dict[str, Any]:
    return {"kind": "message", "title": "Trả lời khách", "text": "Dạ em báo giá ngay ạ",
            "target": {"channel": "zalo", "thread_type": "user", "person_id": str(person),
                       "identity_id": str(identity)},
            "subject": {"type": "person", "id": str(person)}}


async def test_draft_target_rejects_identity_of_another_person(app, db, redis, owner_api: Api) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    pa, pb = await _two_people(db, org)
    r = await owner_api.send("POST", "/drafts", _dm_body(pa, await _identity_of(db, pb)))
    assert r.status_code == 422, r.text
    assert r.json()["code"] == "TARGET_IDENTITY_MISMATCH"
    assert "không thuộc người" in r.json()["errors"]["target"]
    assert (await db.execute(text("SELECT count(*) FROM biz.action_drafts"))).scalar_one() == 0
    # Có identity_id mà không có person_id thì cũng không nhận (không có người để đối chiếu).
    body = _dm_body(pa, await _identity_of(db, pa))
    del body["target"]["person_id"]
    r = await owner_api.send("POST", "/drafts", body)
    assert r.status_code == 422 and r.json()["code"] == "TARGET_IDENTITY_MISMATCH"


async def test_draft_target_accepts_identity_of_the_same_person(app, db, redis, owner_api: Api) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    pa, pb = await _two_people(db, org)
    ident_a = await _identity_of(db, pa)
    r = await owner_api.send("POST", "/drafts", _dm_body(pa, ident_a))
    assert r.status_code == 201, r.text
    stored = (await db.execute(text("SELECT body->'target'->>'identity_id' FROM biz.action_drafts WHERE id = :i"),
                               {"i": r.json()["id"]})).scalar_one()
    assert stored == str(ident_a)
    # Người bị gộp: tài khoản đã đi theo hồ sơ sống, nháp soạn cho người bị gộp vẫn nhận đúng tài khoản đó.
    await _session_zalo(db)
    assert await _detect(org) == 1
    out = await _merge_pending(db, org)
    dropped = uuid.UUID(out["merged"]["id"])
    kept = uuid.UUID(out["person"]["id"])
    r = await owner_api.send("POST", "/drafts", _dm_body(dropped, await _identity_of(db, kept)))
    assert r.status_code == 201, r.text


async def test_staff_cannot_aim_draft_at_unassigned_persons_account(world, owner_api: Api, client, db) -> None:  # type: ignore[no-untyped-def]
    from tests.test_p3_core import assign
    from tests.test_rbac_api import login_as

    org = world["org"]
    pa = world["person"]
    pb = (await db.execute(text("""INSERT INTO core.persons (org_id, code, display_name)
                                   VALUES (:o, core.next_code('PER'), 'Người khác') RETURNING id"""),
                           {"o": org})).scalar_one()
    ch = (await db.execute(text("SELECT id FROM core.channels WHERE org_id = :o AND type = 'zalo'"),
                           {"o": org})).scalar_one()
    ident_b = (await db.execute(text("""INSERT INTO core.person_identities (person_id, channel_id, external_id)
                                        VALUES (:p, :c, 'nguoi-khac') RETURNING id"""),
                                {"c": ch, "p": pb})).scalar_one()
    await db.commit()
    staff = await login_as(client, db, "agent_staff")
    await assign(db, "agent_staff@example.vn", "person", pa)
    r = await staff.send("POST", "/drafts", _dm_body(pa, ident_b))
    assert r.status_code == 422 and r.json()["code"] == "TARGET_IDENTITY_MISMATCH", r.text


async def test_user_account_ignores_identity_not_owned_by_target_person(app, db, redis, owner_api: Api) -> None:  # type: ignore[no-untyped-def]
    """Lớp phòng thủ thứ hai: nháp đã lưu `identity_id` của người khác không gửi sang tài khoản đó."""
    org = await org_id(db)
    pa, pb = await _two_people(db, org)
    tgt = {"channel": "zalo", "thread_type": "user", "person_id": str(pa),
           "identity_id": str(await _identity_of(db, pb))}
    acc = await drafts.user_account(db, org, tgt)
    assert acc is not None and acc.external_id == await _ext_of(db, pa)
