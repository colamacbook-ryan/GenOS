"""Gói A3 (đợt sửa sau kiểm kê v0.1.60): bản nháp & gửi tin TRUNG THỰC.

- F048: gửi lỗi thì Sếp được báo (chuông + sự kiện `draft.failed`) bằng câu tiếng Việt theo mã, mã gốc đi kèm.
- F049: trả lời tin RIÊNG (1-1) từ Hộp thư dựng được đích gửi (Target người), không còn NO_TARGET.
- F074 (Q8-1): duyệt "Ghi hệ thống ngoài" (mcp_write) KHÔNG được báo "đã thực hiện" khi chưa gọi tool nào.
"""
# ruff: noqa: F811 — fixture `world` nhập từ test_p3_core rồi nhận làm tham số (cách dùng chung của repo)

import uuid

import pytest
from sqlalchemy import text

from gh import realtime
from gh.biz.core import drafts
from gh.data.ingest import handle_status
from gh.db import sessionmaker
from tests.conftest import OWNER, Api
from tests.phase2 import msg, org_id, put
from tests.test_p3_core import _claims, _outbound, world  # noqa: F401 — fixture `world` dùng chung
from tests.test_p4_agents import _seed_agent
from tests.test_p4_mcp import _exposed_tool


@pytest.fixture
def published(monkeypatch):  # type: ignore[no-untyped-def]
    """Gom mọi sự kiện WebSocket đã phát (không cần kết nối thật)."""
    events: list[tuple[str, dict]] = []  # type: ignore[type-arg]
    orig = realtime.publish

    async def capture(redis, type, data, **kw):  # type: ignore[no-untyped-def]
        events.append((type, data))
        await orig(redis, type, data, **kw)

    monkeypatch.setattr(realtime, "publish", capture)
    return events


async def _bells(db, kind: str = "draft.failed"):  # type: ignore[no-untyped-def]
    return (await db.execute(text("""SELECT title, body, link FROM core.notifications WHERE kind = :k
                                     ORDER BY created_at"""), {"k": kind})).all()


# ─── F048: gửi lỗi có báo ─────────────────────────────────────────────────────

async def test_failed_send_rings_bell_and_publishes_draft_failed(world, owner_api: Api, db, published) -> None:  # type: ignore[no-untyped-def]
    await owner_api.send("POST", "/auth/pin/verify", {"pin": OWNER["pin"]})
    d = (await owner_api.send("POST", "/drafts", {"kind": "message", "title": "Trả lời chị Lan", "text": "y"})).json()
    r = await owner_api.send("POST", f"/drafts/{d['id']}/approve", {})
    assert r.json()["status"] == "failed" and r.json()["send_result"]["error"] == "NO_TARGET"
    assert "chưa có nơi nhận" in r.json()["send_result"]["message"]       # câu tiếng Việt lưu cùng kết quả
    [bell] = await _bells(db)
    assert "Trả lời chị Lan" in bell.title and "chưa có nơi nhận" in bell.body and "Gửi lỗi" in bell.body
    assert bell.link == f"/workbench?status=failed&id={d['id']}"
    assert "NO_TARGET" not in bell.body                                  # mã kỹ thuật không lộ ra câu chuông
    [(_, ev)] = [e for e in published if e[0] == "draft.failed"]
    assert ev["id"] == d["id"] and ev["error"] == "NO_TARGET" and "chưa có nơi nhận" in ev["message"]


async def test_bridge_send_result_failure_reports_in_vietnamese(world, owner_api: Api, db, redis, app,  # type: ignore[no-untyped-def]
                                                                published) -> None:
    await owner_api.send("POST", "/auth/pin/verify", {"pin": OWNER["pin"]})
    target = {"channel": "zalo", "thread_type": "group", "group_id": str(world["group"])}
    ids = []
    for code in ("SEND_FAILED", "MÃ_LẠ_CỦA_NỀN_TẢNG"):
        d = (await owner_api.send("POST", "/drafts", {"kind": "message", "title": f"Tin {code[:4]}", "text": "x",
                                                       "target": target})).json()
        assert (await owner_api.send("POST", f"/drafts/{d['id']}/approve", {})).json()["status"] == "approved"
        ids.append((d["id"], code))
    sends = {_claims(s["permit"])["draft_id"]: s for s in await _outbound(redis)}
    for did, code in ids:
        s = sends[did]
        async with sessionmaker()() as ses:
            await handle_status(ses, redis, app.state.bus, world["org"], "send.result",
                                {"session_id": s["session_id"], "nonce": _claims(s["permit"])["nonce"],
                                 "draft_id": did, "channel": "zalo", "ok": False, "error": code})
            await ses.commit()
    bells = await _bells(db)
    assert len(bells) == 2
    assert any("Nền tảng chat từ chối" in b.body for b in bells)         # SEND_FAILED → câu riêng
    assert any(drafts.SEND_ERROR_FALLBACK in b.body for b in bells)      # mã lạ → câu chung, không để trống
    assert all("MÃ_LẠ" not in b.body and "SEND_FAILED" not in b.body for b in bells)
    got = (await owner_api.get(f"/drafts/{ids[0][0]}")).json()
    assert got["status"] == "failed" and got["send_result"]["error"] == "SEND_FAILED"
    assert "Nền tảng chat từ chối" in got["send_result"]["message"]
    assert {e[1]["error"] for e in published if e[0] == "draft.failed"} == {"SEND_FAILED", "MÃ_LẠ_CỦA_NỀN_TẢNG"}


async def test_expired_permit_sweep_also_reports(world, owner_api: Api, db, redis, published) -> None:  # type: ignore[no-untyped-def]
    await owner_api.send("POST", "/auth/pin/verify", {"pin": OWNER["pin"]})
    target = {"channel": "zalo", "thread_type": "group", "group_id": str(world["group"])}
    d = (await owner_api.send("POST", "/drafts", {"kind": "message", "title": "Kẹt", "text": "x",
                                                   "target": target})).json()
    await owner_api.send("POST", f"/drafts/{d['id']}/approve", {})
    await db.execute(text("UPDATE biz.action_drafts SET permit_expires_at = now() - interval '1 second' "
                          "WHERE id = :i"), {"i": d["id"]})
    await db.commit()
    assert await drafts.expire_stale_permits(db, redis) == [uuid.UUID(d["id"])]
    await db.commit()
    [bell] = await _bells(db)
    assert "Kẹt" in bell.title and "gửi lại" in bell.body
    assert [e[1]["error"] for e in published if e[0] == "draft.failed"] == ["PERMIT_EXPIRED"]
    got = (await owner_api.get(f"/drafts/{d['id']}")).json()
    assert "xác nhận" in got["send_result"]["message"]


async def test_successful_send_rings_no_failure_bell(world, owner_api: Api, db, redis, app) -> None:  # type: ignore[no-untyped-def]
    await owner_api.send("POST", "/auth/pin/verify", {"pin": OWNER["pin"]})
    target = {"channel": "zalo", "thread_type": "group", "group_id": str(world["group"])}
    d = (await owner_api.send("POST", "/drafts", {"kind": "message", "title": "OK", "text": "x",
                                                   "target": target})).json()
    await owner_api.send("POST", f"/drafts/{d['id']}/approve", {})
    [s] = await _outbound(redis)
    async with sessionmaker()() as ses:
        await handle_status(ses, redis, app.state.bus, world["org"], "send.result",
                            {"session_id": s["session_id"], "nonce": _claims(s["permit"])["nonce"],
                             "draft_id": d["id"], "channel": "zalo", "ok": True, "external_msg_id": "m1"})
        await ses.commit()
    assert await _bells(db) == []


def test_every_known_bridge_error_has_a_vietnamese_sentence() -> None:
    for code in ("NO_TARGET", "TARGET_NOT_FOUND", "SESSION_NOT_ACTIVE", "PERMIT_EXPIRED", "PERMIT_INVALID",
                 "PERMIT_MISMATCH", "PERMIT_REUSED", "NONCE_CHECK_FAILED", "SEND_FAILED"):
        assert drafts.send_error_message(code) != drafts.SEND_ERROR_FALLBACK, code
    assert drafts.send_error_message(None) == drafts.SEND_ERROR_FALLBACK


# ─── F049: tin 1-1 có đích ────────────────────────────────────────────────────

async def _dm_unit(db, *, with_identity: bool = True):  # type: ignore[no-untyped-def]
    org = await org_id(db)
    await db.execute(text("""UPDATE core.organizations
                             SET settings = settings || '{"listen_direct": {"zalo": true}}'::jsonb
                             WHERE id = :o"""), {"o": org})
    await db.commit()
    [raw] = await put(sessionmaker(), org, msg("Cho mình hỏi giá 3 container", group=None, sender="khach1",
                                               name="Chị Hoa"))
    pid = (await db.execute(text("""SELECT pi.person_id FROM raw.events e JOIN core.person_identities pi
                                    ON pi.id = e.sender_identity_id WHERE e.id = :r"""), {"r": raw})).scalar_one()
    if not with_identity:
        await db.execute(text("DELETE FROM core.person_identities WHERE person_id = :p"), {"p": pid})
    uid = (await db.execute(text("""INSERT INTO clean.meaning_units (org_id, observed_at, group_id, person_id,
                                    event_type, conclusion, confidence, run_id)
                                    VALUES (:o, now(), NULL, :p, 'AskedPrice', 'Khách hỏi giá 3 container', 0.9,
                                            gen_random_uuid()) RETURNING id"""), {"o": org, "p": pid})).scalar_one()
    await db.execute(text("""INSERT INTO core.channel_sessions (channel_id, org_id, account_label, state, started_at)
                             SELECT id, org_id, 'Zalo Sếp', 'active', now() FROM core.channels WHERE type = 'zalo'"""))
    await db.commit()
    return org, pid, uid


async def test_dm_reply_from_inbox_builds_person_target_and_sends(app, db, redis, owner_api: Api) -> None:  # type: ignore[no-untyped-def]
    _, pid, uid = await _dm_unit(db)
    r = await owner_api.send("POST", f"/inbox/{uid}/act", {"text": "Dạ em báo giá ngay ạ"})
    assert r.status_code == 200, r.text
    did = r.json()["draft"]["id"]
    d = (await owner_api.get(f"/drafts/{did}")).json()
    assert d["target"]["thread_type"] == "user" and d["target"]["channel"] == "zalo"
    assert d["target"]["person"]["id"] == str(pid) and d["target"]["group"] is None
    assert d["approve_label"] == "Duyệt và gửi qua Zalo"                  # nhãn nút theo đích
    await owner_api.send("POST", "/auth/pin/verify", {"pin": OWNER["pin"]})
    ok = await owner_api.send("POST", f"/drafts/{did}/approve", {})
    assert ok.json()["status"] == "approved", ok.text                     # không còn failed/NO_TARGET
    [send] = await _outbound(redis)
    assert send["thread_type"] == "user" and send["thread_id"] == "khach1" and send["text"] == "Dạ em báo giá ngay ạ"
    assert _claims(send["permit"])["thread_type"] == "user"


async def test_dm_reply_without_any_channel_is_refused_up_front(app, db, owner_api: Api) -> None:  # type: ignore[no-untyped-def]
    _, _, uid = await _dm_unit(db, with_identity=False)
    r = await owner_api.send("POST", f"/inbox/{uid}/act", {"text": "Dạ ạ"})
    assert r.status_code == 409 and r.json()["code"] == "NO_TARGET"
    assert "kênh nào" in r.json()["title"]
    n = (await db.execute(text("SELECT count(*) FROM biz.action_drafts"))).scalar_one()
    assert n == 0                                                         # không tạo bản nháp không gửi được


def test_reply_target_group_wins_else_person() -> None:
    g, p = uuid.uuid4(), uuid.uuid4()
    assert drafts.reply_target("zalo", group_id=g, person_id=p) == drafts.Target("zalo", "group", group_id=g)
    assert drafts.reply_target("whatsapp", person_id=p) == drafts.Target("whatsapp", "user", person_id=p)


# ─── F074: mcp_write không giả "đã làm" ───────────────────────────────────────

async def _mcp_write_draft(db, org):  # type: ignore[no-untyped-def]
    made = await drafts.create_draft(db, org_id=org, kind="mcp_write", title="Gọi tool send_email",
                                     body_text="Gọi tool MCP ghi 'send_email'", action_key="mcp.write",
                                     created_by=(await db.execute(text("SELECT id FROM core.users LIMIT 1"))
                                                 ).scalar_one())
    await db.commit()
    assert made["status"] == "pending"
    return made["id"]


async def test_approving_mcp_write_is_refused_and_never_marked_sent(world, owner_api: Api, db) -> None:  # type: ignore[no-untyped-def]
    did = await _mcp_write_draft(db, world["org"])
    await owner_api.send("POST", "/auth/pin/verify", {"pin": OWNER["pin"]})
    for path, body in (("approve", {}), ("edit-send", {"text": "đổi"})):
        r = await owner_api.send("POST", f"/drafts/{did}/{path}", body)
        assert r.status_code == 409 and r.json()["code"] == "MCP_WRITE_UNSUPPORTED", r.text
        assert "Chưa hỗ trợ thực thi tool ghi" in r.json()["title"] and "đã giữ yêu cầu" in r.json()["title"]
    row = (await db.execute(text("SELECT status, sent_at, send_result, decided_by FROM biz.action_drafts "
                                 "WHERE id = :i"), {"i": did})).one()
    assert row.status == "pending" and row.sent_at is None and row.send_result is None and row.decided_by is None
    log = (await db.execute(text("SELECT action FROM ops.action_log WHERE target_id = :i"), {"i": str(did)})
           ).scalars().all()
    assert "draft.approved" not in log and "draft.edited" not in log     # chưa quyết gì thì không ghi "đã duyệt"
    assert (await owner_api.get("/drafts")).json()["total"] == 1          # vẫn nằm ở Chờ duyệt


async def test_rejecting_mcp_write_still_works(world, owner_api: Api, db) -> None:  # type: ignore[no-untyped-def]
    did = await _mcp_write_draft(db, world["org"])
    await owner_api.send("POST", "/auth/pin/verify", {"pin": OWNER["pin"]})
    r = await owner_api.send("POST", f"/drafts/{did}/reject", {"reason": "chưa cần"})
    assert r.status_code == 200 and r.json()["status"] == "rejected"


async def test_calling_a_write_tool_says_honestly_that_nothing_ran(owner_api: Api, app, db) -> None:  # type: ignore[no-untyped-def]
    org = (await db.execute(text("SELECT id FROM core.organizations"))).scalar_one()
    agent_id = await _seed_agent(db, org, name="Trợ lý bán hàng")
    tool, _ = await _exposed_tool(owner_api, app, "send_email", grant_to=f"agent:{agent_id}")
    r = await owner_api.send("POST", f"/mcp/tools/{tool['id']}/call",
                             {"agent_key": f"agent:{agent_id}", "args": {"to": "khach@vi.dụ"}})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["outcome"] == "held_for_approval"
    assert "chưa hỗ trợ thực thi tool ghi" in out["call"]["result_summary"]      # nhật ký cuộc gọi nói thật
    body = (await db.execute(text("SELECT body->>'text' FROM biz.action_drafts WHERE id = :i"),
                             {"i": out["draft"]["id"]})).scalar_one()
    assert "chưa hỗ trợ thực thi tool ghi" in body
    n = (await db.execute(text("SELECT count(*) FROM agent.mcp_calls WHERE outcome = 'ok'"))).scalar_one()
    assert n == 0                                                                 # không có lần gọi thật nào
