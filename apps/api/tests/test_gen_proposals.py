"""v0.1.24 — Gen v2 (A4): đề xuất thao tác có xác nhận (nháp tin, nhắc việc, gán người).

Gen chỉ đề xuất (bước `proposal`); ghi thật chỉ khi người dùng xác nhận qua endpoint sẵn có, đúng quyền + PIN,
Action Log actor=user, via=gen. Nhắc việc đến giờ → thông báo chuông (job task_reminder_scan).
"""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import orjson
from sqlalchemy import text

from gh.biz.queue.jobs import due_reminders
from gh.db import admin_sessionmaker, sessionmaker
from gh.gen import proposals, store
from tests.conftest import OWNER, Api
from tests.test_gen import FakeRouter, ask, kinds
from tests.test_rbac_api import login_as


def _soon(minutes: int = 60) -> str:
    return (datetime.now(UTC) + timedelta(minutes=minutes)).isoformat()


def _proposals(turn: dict[str, Any]) -> list[dict[str, Any]]:
    return [s["step"]["proposal"] for s in turn["steps"] if s["step"]["kind"] == "proposal"]


async def _log(action: str) -> list[Any]:
    async with admin_sessionmaker()() as db:
        return (await db.execute(text("""SELECT action, result, actor_type, actor_id, target_type, target_id, detail
                                         FROM ops.action_log WHERE action = :a ORDER BY at, id"""),
                                 {"a": action})).all()


async def _me(api: Api) -> dict[str, Any]:
    me: dict[str, Any] = (await api.get("/auth/me")).json()
    return me


async def _clear_pin(api: Api) -> None:
    me = await _me(api)
    async with admin_sessionmaker()() as db:
        await db.execute(text("UPDATE core.sessions SET pin_verified_until = NULL WHERE user_id = :u"),
                         {"u": me["id"]})
        await db.commit()


async def test_reminder_proposal_confirm_edit_and_log(owner_api: Api, app: Any) -> None:
    me = await _me(owner_api)
    router = FakeRouter([{"steps": [
        {"kind": "say", "text": "Em đề xuất tạo nhắc việc, Sếp xem rồi xác nhận nhé."},
        {"kind": "propose", "proposal": {"type": "reminder", "fields": {
            "title": "Gọi lại anh Bình", "remind_at": _soon(), "priority": "P2"}}}]}])
    t = await ask(owner_api, app, router, "Nhắc tôi gọi lại anh Bình sau 1 tiếng", screen="tasks")
    assert kinds(t) == ["say", "proposal"]
    p = _proposals(t)[0]
    assert p["type"] == "reminder" and p["status"] == "pending" and p["requires_pin"] is False
    assert p["target"] == "tasks.new" and p["fields"]["assignee_user_id"] == me["id"]  # mặc định nhắc người hỏi
    assert "Gọi lại anh Bình" in p["summary"] and "Anh Cơ" in p["summary"]
    assert "user_id" not in p and "org_id" not in p
    # Chưa xác nhận → chưa có việc nào.
    assert (await owner_api.get("/tasks")).json()["total"] == 0
    # Hệ thống viết prompt có giờ hiện tại + mẫu propose.
    assert "Bây giờ:" in router.calls[0][0].content and '"type":"reminder"' in router.calls[0][0].content

    r = await owner_api.send("POST", f"/gen/proposals/{p['id']}/confirm",
                             {"fields": {"title": "Gọi lại anh Bình (đã sửa)", "priority": "P1"}})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["status"] == "confirmed" and out["result"]["type"] == "task" and out["result"]["code"].startswith("TSK")
    task = (await owner_api.get(f"/tasks/{out['result']['id']}")).json()
    assert task["title"] == "Gọi lại anh Bình (đã sửa)" and task["priority"] == "P1"
    assert task["remind_at"] is not None and task["assignee"]["id"] == me["id"]
    # Action Log: người bấm là actor, via=gen; endpoint sẵn có vẫn ghi task.created.
    rows = await _log("gen.proposal_confirmed")
    assert len(rows) == 1 and rows[0].actor_type == "user" and rows[0].actor_id == f"user:{me['id']}"
    assert rows[0].detail["via"] == "gen" and rows[0].detail["edited"] is True and rows[0].target_type == "task"
    assert rows[0].detail["endpoint"] == "POST /tasks"
    assert len(await _log("task.created")) == 1
    proposed = await _log("gen.propose")
    assert proposed[0].actor_type == "agent" and proposed[0].detail["on_behalf_of"] == me["id"]
    # Không xác nhận lần hai.
    again = await owner_api.send("POST", f"/gen/proposals/{p['id']}/confirm", {})
    assert again.status_code == 409 and again.json()["code"] == "GEN_PROPOSAL_DECIDED"
    # Hội thoại đã lưu mang trạng thái mới (mở lại không hiện nút Xác nhận).
    msgs = (await owner_api.get(f"/gen/conversations/{t['conversation_id']}/messages")).json()
    saved = [s for s in msgs[-1]["content"]["steps"] if s["kind"] == "proposal"][0]["proposal"]
    assert saved["status"] == "confirmed" and saved["result"]["id"] == out["result"]["id"]


async def test_reminder_job_notifies_once_and_again_after_reschedule(owner_api: Api, app: Any) -> None:
    me = await _me(owner_api)
    r = await owner_api.send("POST", "/tasks", {"title": "Chốt báo giá", "assignee_user_id": me["id"],
                                               "remind_at": _soon(-5)})
    tid = r.json()["id"]
    await owner_api.send("POST", "/tasks", {"title": "Chưa tới giờ", "remind_at": _soon(120)})
    async with sessionmaker()() as db:
        assert await due_reminders(db) == 1
        await db.commit()
    async with sessionmaker()() as db:
        assert await due_reminders(db) == 0  # mỗi mốc nhắc một lần
    n = (await owner_api.get("/notifications")).json()
    assert n["unread"] == 1 and n["items"][0]["kind"] == "task.reminder"
    assert n["items"][0]["title"] == "Nhắc việc: Chốt báo giá" and n["items"][0]["link"] == "/tasks"
    await owner_api.send("PATCH", f"/tasks/{tid}", {"remind_at": _soon(-1)})
    async with sessionmaker()() as db:
        assert await due_reminders(db) == 1
        await db.commit()
    await owner_api.send("PATCH", f"/tasks/{tid}", {"status": "done", "remind_at": _soon(-1)})
    async with sessionmaker()() as db:
        assert await due_reminders(db) == 0  # việc đã xong không nhắc


async def test_draft_proposal_needs_pin_and_stays_pending(owner_api: Api, app: Any) -> None:
    router = FakeRouter([{"steps": [{"kind": "propose", "proposal": {"type": "draft_message", "fields": {
        "title": "Báo giá ván MDF", "text": "Chào anh, em gửi báo giá ván MDF 18 ly như đã hẹn."}}}]}])
    t = await ask(owner_api, app, router, "Soạn giúp tin báo giá ván MDF")
    p = _proposals(t)[0]
    # workbench.drafts nhạy cảm (tin gửi ra ngoài) → cần PIN; tóm tắt do hệ thống viết.
    assert p["requires_pin"] is True and p["target"] == "workbench.drafts"
    assert "chưa gửi đi" in p["summary"]
    await _clear_pin(owner_api)
    r = await owner_api.send("POST", f"/gen/proposals/{p['id']}/confirm", {})
    assert r.status_code == 423 and r.json()["code"] == "PIN_REQUIRED"
    assert (await owner_api.send("POST", "/auth/pin/verify", {"pin": OWNER["pin"]})).status_code == 200
    r = await owner_api.send("POST", f"/gen/proposals/{p['id']}/confirm", {})
    assert r.status_code == 200, r.text
    draft = (await owner_api.get(f"/drafts/{r.json()['result']['id']}")).json()
    assert draft["status"] == "pending" and draft["title"] == "Báo giá ván MDF"  # chờ duyệt, KHÔNG gửi
    # Không có đối tượng ⇒ không có nơi gửi: web không được hứa "Duyệt & gửi" (duyệt sẽ NO_TARGET).
    assert r.json()["result"]["sendable"] is False
    assert draft["target"] is None and draft["approve_label"] == "Duyệt và thực hiện"
    row = (await _log("gen.proposal_confirmed"))[0]
    assert row.target_type == "draft" and row.detail["via"] == "gen" and row.detail["endpoint"] == "POST /drafts"


async def test_assign_task_needs_seen_ids(owner_api: Api, app: Any, db: Any) -> None:
    staff = await login_as(owner_api.c, db, "operator")
    await staff.c.aclose()
    tid = (await owner_api.send("POST", "/tasks", {"title": "Kiểm kho"})).json()["id"]
    staff_id = str((await db.execute(text("SELECT id FROM core.users WHERE email = 'operator@example.vn'")))
                   .scalar_one())
    fake = str(uuid.uuid4())
    router = FakeRouter([
        {"steps": [{"kind": "tool", "name": "task.list", "args": {"status": "todo"}},
                   {"kind": "tool", "name": "staff.list", "args": {}}]},
        {"steps": [{"kind": "propose", "proposal": {"type": "assign", "fields": {
                       "item_type": "task", "item_id": fake, "user_id": staff_id}}},
                   {"kind": "propose", "proposal": {"type": "assign", "fields": {
                       "item_type": "task", "item_id": tid, "user_id": staff_id}}},
                   {"kind": "done"}]},
    ])
    t = await ask(owner_api, app, router, "Giao việc Kiểm kho cho operator", screen="tasks")
    ps = _proposals(t)
    assert len(ps) == 1 and ps[0]["fields"]["item_id"] == tid  # id bịa bị chặn
    assert ps[0]["labels"] == {"user": "operator", "item": ps[0]["labels"]["item"]}
    assert "Kiểm kho" in ps[0]["labels"]["item"] and ps[0]["target"] == f"tasks.row:{tid}"
    blocked = [r for r in await _log("gen.propose") if r.result == "blocked"]
    assert len(blocked) == 1 and fake in blocked[0].detail["reason"]
    # Không sửa được trường khoá (item_id) khi xác nhận.
    bad = await owner_api.send("POST", f"/gen/proposals/{ps[0]['id']}/confirm", {"fields": {"item_id": fake}})
    assert bad.status_code == 422
    r = await owner_api.send("POST", f"/gen/proposals/{ps[0]['id']}/confirm", {})
    assert r.status_code == 200, r.text
    assert (await owner_api.get(f"/tasks/{tid}")).json()["assignee"]["id"] == staff_id
    row = (await _log("gen.proposal_confirmed"))[0]
    assert row.detail["endpoint"] == f"PATCH /tasks/{tid}" and row.target_id == tid


async def test_cancel_and_foreign_proposal(owner_api: Api, app: Any, db: Any) -> None:
    router = FakeRouter([{"steps": [{"kind": "propose", "proposal": {"type": "reminder", "fields": {
        "title": "Họp giao ban", "remind_at": _soon(30)}}}]}])
    p = _proposals(await ask(owner_api, app, router, "Nhắc họp"))[0]
    r = await owner_api.send("POST", f"/gen/proposals/{p['id']}/cancel")
    assert r.status_code == 200 and r.json()["status"] == "cancelled"
    assert (await owner_api.send("POST", f"/gen/proposals/{p['id']}/confirm", {})).status_code == 409
    row = (await _log("gen.proposal_cancelled"))[0]
    assert row.actor_type == "user" and row.detail["via"] == "gen"
    assert (await owner_api.get("/tasks")).json()["total"] == 0
    # Đề xuất của người khác → 404; không tồn tại → 404.
    other = {**p, "id": str(uuid.uuid4()), "status": "pending", "user_id": str(uuid.uuid4()),
             "org_id": (await _me(owner_api))["org"]["id"], "turn_id": str(uuid.uuid4()),
             "conversation_id": str(uuid.uuid4())}
    await proposals.save(app.state.redis, other)
    assert (await owner_api.send("POST", f"/gen/proposals/{other['id']}/confirm", {})).status_code == 404
    assert (await owner_api.send("POST", f"/gen/proposals/{uuid.uuid4()}/confirm", {})).status_code == 404


async def test_permission_denials(owner_api: Api, app: Any, db: Any) -> None:
    operator = await login_as(owner_api.c, db, "operator")
    auditor = await login_as(owner_api.c, db, "auditor")
    try:
        # Gen chỉ bật cho Owner (quyết định §9.1): vai trò khác không xác nhận/không xem người giao được.
        assert (await operator.send("POST", f"/gen/proposals/{uuid.uuid4()}/confirm", {})).json()["code"] \
            == "GEN_DISABLED"
        assert (await operator.get("/gen/assignees")).status_code == 403
        # Mở Gen cho Kiểm toán (không có queue.act / action.draft) → đề xuất ghi bị chặn ngay ở server.
        org = (await _me(owner_api))["org"]["id"]
        async with sessionmaker()() as s:
            cfg = await store.get_settings(s, uuid.UUID(org))
            await store.save_settings(s, uuid.UUID(org), {**cfg, "roles": ["owner", "auditor"]})
            await s.commit()
        router = FakeRouter([{"steps": [
            {"kind": "propose", "proposal": {"type": "reminder", "fields": {"title": "x", "remind_at": _soon()}}},
            {"kind": "propose", "proposal": {"type": "draft_message", "fields": {"title": "x", "text": "y"}}}]},
            {"steps": [{"kind": "say", "text": "Em không có quyền tạo việc cho vai trò này."}]}])
        t = await ask(auditor, app, router, "Nhắc tôi việc x")
        assert _proposals(t) == [] and "đề xuất bị chặn" in router.calls[1][-1].content
        assert "queue.act" in router.calls[1][-1].content and "action.draft" in router.calls[1][-1].content
        assert (await auditor.get("/gen/assignees")).status_code == 403
        # Đề xuất lọt vào Redis (vd lúc còn quyền) vẫn bị kiểm lại lúc xác nhận → 403 + ghi "blocked".
        aud = await _me(auditor)
        forged = {"id": str(uuid.uuid4()), "type": "reminder", "status": "pending", "user_id": aud["id"],
                  "org_id": org, "turn_id": str(uuid.uuid4()), "conversation_id": str(uuid.uuid4()),
                  "fields": {"title": "x", "remind_at": _soon(), "priority": "P3", "assignee_user_id": aud["id"],
                             "due_at": None, "subject": None}, "labels": {}, "target": "tasks.new",
                  "summary": "x", "requires_pin": False}
        await app.state.redis.set(proposals.key(forged["id"]), orjson.dumps(forged))
        r = await auditor.send("POST", f"/gen/proposals/{forged['id']}/confirm", {})
        assert r.status_code == 403
        denied = [x for x in await _log("gen.proposal_confirmed") if x.result == "blocked"]
        assert len(denied) == 1 and denied[0].actor_id == f"user:{aud['id']}"
        assert (await owner_api.get("/tasks")).json()["total"] == 0
        # Tắt Gen → kể cả Owner cũng không xác nhận được đề xuất cũ.
        p = _proposals(await ask(owner_api, app, FakeRouter([{"steps": [{"kind": "propose", "proposal": {
            "type": "reminder", "fields": {"title": "y", "remind_at": _soon()}}}]}]), "nhắc y"))[0]
        await owner_api.send("PATCH", "/gen/settings", {"enabled": False})
        r = await owner_api.send("POST", f"/gen/proposals/{p['id']}/confirm", {})
        assert r.status_code == 403 and r.json()["code"] == "GEN_DISABLED"
    finally:
        await operator.c.aclose()
        await auditor.c.aclose()


async def test_endpoint_denial_is_propagated(owner_api: Api, app: Any, db: Any, monkeypatch: Any) -> None:
    """Endpoint sẵn có từ chối → lỗi của nó trả về nguyên, đề xuất vẫn chờ (thử lại được), ghi 'blocked'."""
    tid = (await owner_api.send("POST", "/tasks", {"title": "Sẽ bị xoá"})).json()["id"]
    me = await _me(owner_api)
    router = FakeRouter([
        {"steps": [{"kind": "tool", "name": "task.list", "args": {}}]},
        {"steps": [{"kind": "propose", "proposal": {"type": "assign", "fields": {
            "item_type": "task", "item_id": tid, "user_id": me["id"]}}}]}])
    p = _proposals(await ask(owner_api, app, router, "giao cho tôi", screen="tasks"))[0]
    missing = uuid.uuid4()

    async def plan(*_: Any) -> proposals.Call:
        return proposals.Call("POST", f"/inbox/{missing}/assign", {"user_id": me["id"]}, "inbox_item")

    monkeypatch.setattr(proposals, "plan_call", plan)
    r = await owner_api.send("POST", f"/gen/proposals/{p['id']}/confirm", {})
    assert r.status_code == 404 and r.json()["code"] == "NOT_FOUND"
    assert (await proposals.load(app.state.redis, p["id"]))["status"] == "pending"
    assert await app.state.redis.get(proposals.claim_key(p["id"])) is None
    row = [x for x in await _log("gen.proposal_confirmed") if x.result == "blocked"][0]
    assert row.detail["status"] == 404 and row.detail["via"] == "gen"
    monkeypatch.undo()
    # Việc bị xoá giữa chừng → không thực hiện (422), vẫn chờ.
    async with admin_sessionmaker()() as s:
        await s.execute(text("DELETE FROM biz.tasks WHERE id = :i"), {"i": tid})
        await s.commit()
    r = await owner_api.send("POST", f"/gen/proposals/{p['id']}/confirm", {})
    assert r.status_code == 422
    assert (await proposals.load(app.state.redis, p["id"]))["status"] == "pending"


async def test_past_reminder_rejected_and_proposal_limit(owner_api: Api, app: Any) -> None:
    router = FakeRouter([{"steps": [
        {"kind": "propose", "proposal": {"type": "reminder", "fields": {"title": "cũ", "remind_at": _soon(-60)}}},
        *[{"kind": "propose", "proposal": {"type": "reminder", "fields": {"title": f"n{i}", "remind_at": _soon()}}}
          for i in range(4)]]},
        {"steps": [{"kind": "say", "text": "Dạ."}]}])
    t = await ask(owner_api, app, router, "nhắc nhiều thứ")
    assert [p["fields"]["title"] for p in _proposals(t)] == ["n0", "n1", "n2"]
    fb = router.calls[1][-1].content
    assert "giờ nhắc đã qua" in fb and "tối đa 3 đề xuất" in fb


async def test_assign_inbox_item_shows_which_item(owner_api: Api, app: Any) -> None:
    """Thẻ giao mục Hộp thư phải nêu đúng mục (mã + tiêu đề), không ghi chung chung."""
    d = (await owner_api.send("POST", "/drafts", {"kind": "message", "title": "Báo giá gỗ thông",
                                                 "text": "nội dung"})).json()
    items = (await owner_api.get("/inbox", params={"tab": "all"})).json()["items"]
    item = next(i for i in items if i["title"] == "Báo giá gỗ thông")
    me = await _me(owner_api)
    router = FakeRouter([
        {"steps": [{"kind": "tool", "name": "queue.list", "args": {"tab": "all"}}]},
        {"steps": [{"kind": "propose", "proposal": {"type": "assign", "fields": {
            "item_type": "inbox", "item_id": item["id"], "user_id": me["id"]}}}]}])
    ps = _proposals(await ask(owner_api, app, router, "giao mục này cho tôi", screen="inbox"))
    assert len(ps) == 1, d
    assert "Báo giá gỗ thông" in ps[0]["labels"]["item"] and "Báo giá gỗ thông" in ps[0]["summary"]


async def test_confirm_does_not_block_on_session_row_lock(owner_api: Api, app: Any) -> None:
    """Phiên sắp hết hạn → request ngoài UPDATE core.sessions (gia hạn phiên); lời gọi nội bộ cũng tải đúng phiên
    đó. Nếu transaction ngoài còn mở (giữ khoá dòng) lúc gọi nội bộ thì hai bên chờ nhau tới hết timeout."""
    router = FakeRouter([{"steps": [{"kind": "propose", "proposal": {"type": "reminder", "fields": {
        "title": "Gọi lại", "remind_at": _soon(), "priority": "P2"}}}]}])
    p = _proposals(await ask(owner_api, app, router, "Nhắc tôi gọi lại", screen="tasks"))[0]
    me = await _me(owner_api)
    async with admin_sessionmaker()() as db:
        await db.execute(text("UPDATE core.sessions SET expires_at = now() + interval '1 minute' WHERE user_id = :u"),
                         {"u": me["id"]})
        await db.commit()
    r = await asyncio.wait_for(owner_api.send("POST", f"/gen/proposals/{p['id']}/confirm", {}), 10)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "confirmed"
    assert len(await _log("gen.proposal_confirmed")) == 1


async def test_confirm_failure_passes_error_id_and_pin_lock(owner_api: Api, app: Any, monkeypatch: Any) -> None:
    """Endpoint nội bộ lỗi → web vẫn nhận mã lỗi (error_id) của 500 và giờ mở khoá của PIN_LOCKED; khoá lạ bị bỏ."""
    tid = (await owner_api.send("POST", "/tasks", {"title": "Thử lỗi"})).json()["id"]
    me = await _me(owner_api)
    router = FakeRouter([
        {"steps": [{"kind": "tool", "name": "task.list", "args": {}}]},
        {"steps": [{"kind": "propose", "proposal": {"type": "assign", "fields": {
            "item_type": "task", "item_id": tid, "user_id": me["id"]}}}]}])
    p = _proposals(await ask(owner_api, app, router, "giao cho tôi", screen="tasks"))[0]
    replies: list[tuple[int, dict[str, Any]]] = [
        (500, {"code": "INTERNAL", "title": "Hệ thống gặp lỗi", "detail": "Mã lỗi abcd1234",
               "error_id": "abcd1234", "sql": "SELECT bi_mat"}),
        (423, {"code": "PIN_LOCKED", "title": "Mã PIN đang bị khoá", "detail": "Thử lại sau ít phút",
               "locked_until": "2026-10-02T10:00:00+00:00"}),
        (401, {"code": "PIN_INVALID", "title": "Mã PIN không đúng", "attempts_left": 2}),
    ]

    async def fake_call(*_: Any, **__: Any) -> tuple[int, dict[str, Any]]:
        return replies.pop(0)

    monkeypatch.setattr(proposals, "call_as_user", fake_call)
    r = await owner_api.send("POST", f"/gen/proposals/{p['id']}/confirm", {})
    assert r.status_code == 500 and r.json()["code"] == "INTERNAL" and r.json()["error_id"] == "abcd1234"
    assert "sql" not in r.json() and "bi_mat" not in r.text
    r = await owner_api.send("POST", f"/gen/proposals/{p['id']}/confirm", {})
    assert r.status_code == 423 and r.json()["locked_until"] == "2026-10-02T10:00:00+00:00"
    r = await owner_api.send("POST", f"/gen/proposals/{p['id']}/confirm", {})
    assert r.status_code == 401 and r.json()["attempts_left"] == 2
    assert (await proposals.load(app.state.redis, p["id"]))["status"] == "pending"


def test_result_of_draft_sendable_only_with_target() -> None:
    """v0.1.43 (F-24): `sendable` theo đúng body gửi /drafts — chỉ nháp cho NHÓM (có target) mới gửi được."""
    from gh.gen.proposals import Call, result_of

    group = Call("POST", "/drafts", {"kind": "message", "title": "x", "text": "y",
                                     "subject": {"type": "group", "id": "g"},
                                     "target": {"channel": "zalo", "thread_type": "group", "group_id": "g"}}, "draft")
    person = Call("POST", "/drafts", {"kind": "message", "title": "x", "text": "y",
                                      "subject": {"type": "person", "id": "p"}}, "draft")
    assert result_of(group, {}, {"id": "d1", "code": "ACT-1"})["sendable"] is True
    assert result_of(person, {}, {"id": "d2", "code": "ACT-2"})["sendable"] is False
    assert "sendable" not in result_of(Call("POST", "/tasks", {}, "task"), {}, {"id": "t1"})
