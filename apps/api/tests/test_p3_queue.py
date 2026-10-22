"""Hàng đợi & Hành động: Tổng quan (KPI F4), Hộp thư (ưu tiên, tab, giao, im lặng, phạm vi), Việc & Nhắc
hẹn (hạn quá đỏ, lời hứa), cảnh báo sớm (quét ngưỡng, chống trùng)."""

import uuid
from datetime import UTC, datetime, timedelta

import orjson
import pytest
from sqlalchemy import text

from gh.biz.queue import jobs as queue_jobs
from gh.db import sessionmaker
from gh.providers.router import raise_alert
from tests.conftest import Api
from tests.phase2 import install_presets, listen, msg, org_id, put
from tests.test_rbac_api import login_as


async def _unit(db, org, *, group_id=None, person_id=None, event_type: str, conclusion: str, confidence: float = 0.9,
                raw_id=None, entities=None) -> uuid.UUID:  # type: ignore[no-untyped-def]
    uid = (await db.execute(text("""
        INSERT INTO clean.meaning_units (org_id, observed_at, group_id, person_id, event_type, conclusion, entities,
                                         confidence, run_id)
        VALUES (:o, now(), :g, :p, :et, :c, CAST(:e AS jsonb), :conf, core.uuid_v7()) RETURNING id"""),
        {"o": org, "g": group_id, "p": person_id, "et": event_type, "c": conclusion,
         "e": orjson.dumps(entities or {}).decode(), "conf": confidence})).scalar_one()
    if raw_id is not None:
        await db.execute(text("""
            INSERT INTO clean.evidence (meaning_unit_id, meaning_observed_at, raw_event_id, raw_received_at, quote)
            SELECT mu.id, mu.observed_at, e.id, e.received_at, e.body_text
            FROM clean.meaning_units mu, raw.events e WHERE mu.id = :u AND e.id = :r"""),
            {"u": uid, "r": raw_id})
    return uid


async def _person_of(db, raw_id) -> uuid.UUID:  # type: ignore[no-untyped-def]
    return (await db.execute(text("""SELECT pi.person_id FROM raw.events e
                                     JOIN core.person_identities pi ON pi.id = e.sender_identity_id
                                     WHERE e.id = :r"""), {"r": raw_id})).scalar_one()


async def assign_scope(db, email: str, subject_type: str, subject_id) -> None:  # type: ignore[no-untyped-def]
    await db.execute(text("""INSERT INTO core.assignments (org_id, user_id, subject_type, subject_id)
                             SELECT org_id, id, :t, :s FROM core.users WHERE email = :e"""),
                     {"t": subject_type, "s": subject_id, "e": email})
    await db.commit()


@pytest.fixture
async def world(app, db, redis, owner_api):  # type: ignore[no-untyped-def]
    """Tổ chức đã khởi tạo, nhóm g1 đang nghe, 3 người + 3 đơn vị ý nghĩa (hỏi giá/than phiền/ứng viên), một
    cảnh báo đang mở, một bản nháp chờ duyệt, một việc quá hạn + một việc sắp đến hạn, một lời hứa sắp vỡ."""
    org = await org_id(db)
    await install_presets(db, org)
    gid = await listen(db, org, "g1")
    sm = sessionmaker()
    [raw_ask] = await put(sm, org, msg("Cần 3 container thép, giá bao nhiêu?", sender="u1", name="Chị Lan"))
    [raw_complain] = await put(sm, org, msg("Sao chưa ai trả lời, chán quá, chắc chuyển bên khác",
                                            sender="u2", name="Anh Bình"))
    [raw_candidate] = await put(sm, org, msg("Em có 5 năm kinh nghiệm sale, đang tìm cơ hội mới",
                                             sender="u3", name="Em Chi"))
    await db.commit()
    p_ask, p_complain, p_candidate = [await _person_of(db, r) for r in (raw_ask, raw_complain, raw_candidate)]
    await db.execute(text("UPDATE core.persons SET person_type = 'candidate' WHERE id = :p"), {"p": p_candidate})
    await db.execute(text("UPDATE core.persons SET person_type = 'customer' WHERE id IN (:a, :b)"),
                     {"a": p_ask, "b": p_complain})
    u_ask = await _unit(db, org, group_id=gid, person_id=p_ask, event_type="AskedPrice",
                        conclusion="Hỏi giá 3 container thép", confidence=0.9, raw_id=raw_ask,
                        entities={"product": "thép cuộn"})
    u_complain = await _unit(db, org, group_id=gid, person_id=p_complain, event_type="Complained",
                             conclusion="Than phiền chưa ai trả lời", confidence=0.95, raw_id=raw_complain)
    u_candidate = await _unit(db, org, group_id=gid, person_id=p_candidate, event_type="AskedPrice",
                              conclusion="Hỏi giá nhưng là ứng viên", confidence=0.7, raw_id=raw_candidate)
    await raise_alert(db, org, alert_type="customer_cooling", priority="P1", title="Khách Lan có thể đang lạnh",
                      summary="14 ngày im ắng", suggested="Gọi hỏi thăm", subject_type="person", subject_id=p_ask,
                      evidence=[{"type": "meaning_unit", "id": str(u_ask)}])
    alert_row = (await db.execute(text("SELECT id FROM biz.alerts WHERE title LIKE 'Khách Lan%'"))).scalar_one()
    from gh.biz.core import drafts
    draft = await drafts.create_draft(db, org_id=org, kind="message", title="Trả lời chị Lan", body_text="Dạ vâng ạ",
                                      target=drafts.Target(channel="zalo", thread_type="group", group_id=gid),
                                      subject=("person", p_ask))
    overdue_code = (await db.execute(text("SELECT core.next_code('TSK')"))).scalar_one()
    overdue_task = (await db.execute(text("""INSERT INTO biz.tasks (org_id, code, title, priority, status,
                                                                    subject_type, subject_id, due_at, source)
        VALUES (:o, :c, 'Gửi hợp đồng', 'P1', 'todo', 'person', :p, now() - interval '1 day', 'manual')
        RETURNING id"""), {"o": org, "c": overdue_code, "p": p_ask})).scalar_one()
    soon_code = (await db.execute(text("SELECT core.next_code('TSK')"))).scalar_one()
    soon_task = (await db.execute(text("""INSERT INTO biz.tasks (org_id, code, title, priority, status,
                                                                 subject_type, subject_id, due_at, source)
        VALUES (:o, :c, 'Gọi lại', 'P2', 'todo', 'person', :p, now() + interval '1 day', 'manual')
        RETURNING id"""), {"o": org, "c": soon_code, "p": p_complain})).scalar_one()
    promise_id = (await db.execute(text("""INSERT INTO biz.promises (org_id, promiser_person_id, text, due_at)
        VALUES (:o, :p, 'Sẽ gửi báo giá trong hôm nay', now() + interval '1 day') RETURNING id"""),
        {"o": org, "p": p_ask})).scalar_one()
    await db.commit()
    return {"org": org, "group": gid, "p_ask": p_ask, "p_complain": p_complain, "p_candidate": p_candidate,
            "u_ask": u_ask, "u_complain": u_complain, "u_candidate": u_candidate, "alert": alert_row,
            "draft": draft["id"], "overdue_task": overdue_task, "soon_task": soon_task, "promise": promise_id}


# ─── Hộp thư ────────────────────────────────────────────────────────────────

async def test_inbox_priority_tab_and_counts(world, owner_api: Api) -> None:  # type: ignore[no-untyped-def]
    r = await owner_api.get("/inbox?tab=all")
    assert r.status_code == 200, r.text
    body = r.json()
    by_id = {i["id"]: i for i in body["items"]}
    assert by_id[str(world["u_ask"])]["tab"] == "opportunity" and by_id[str(world["u_ask"])]["priority"] == "P2"
    assert by_id[str(world["u_complain"])]["tab"] == "reply" and by_id[str(world["u_complain"])]["priority"] == "P1"
    assert by_id[str(world["u_candidate"])]["tab"] == "candidate"      # ứng viên thắng dù event_type = AskedPrice
    assert by_id[str(world["alert"])]["tab"] == "alert" and by_id[str(world["alert"])]["priority"] == "P1"
    assert by_id[str(world["draft"])]["tab"] == "approval"
    assert body["counts"] == {"all": 5, "opportunity": 1, "alert": 1, "approval": 1, "reply": 1, "candidate": 1}

    only_opp = (await owner_api.get("/inbox?tab=opportunity")).json()
    assert [i["id"] for i in only_opp["items"]] == [str(world["u_ask"])]
    # total = toàn hàng đợi (không riêng tab đang chọn).
    assert only_opp["total"] == 5 and only_opp["counts"]["opportunity"] == 1

    by_intent = (await owner_api.get("/inbox?intent=Complained")).json()
    assert [i["id"] for i in by_intent["items"]] == [str(world["u_complain"])]


async def test_inbox_scope_by_role(world, owner_api: Api, client, db) -> None:  # type: ignore[no-untyped-def]
    staff = await login_as(client, db, "agent_staff")
    empty = (await staff.get("/inbox")).json()
    assert empty["total"] == 0
    await assign_scope(db, "agent_staff@example.vn", "person", world["p_ask"])
    scoped = (await staff.get("/inbox")).json()
    # Mọi item gắn với người được phân (đơn vị, cảnh báo, bản nháp cùng subject) — không chỉ đơn vị.
    assert {i["id"] for i in scoped["items"]} == {str(world["u_ask"]), str(world["alert"]), str(world["draft"])}

    # Giao thẳng một đơn vị của người NGOÀI phạm vi cho staff qua core.assignments(subject_type='queue') —
    # staff thấy được item dù không được phân người/nhóm liên quan.
    staff_id = (await db.execute(text("SELECT id FROM core.users WHERE email = 'agent_staff@example.vn'"))).scalar_one()
    still_hidden = await staff.get(f"/inbox/{world['u_complain']}")
    assert still_hidden.status_code == 404
    r = await owner_api.send("POST", f"/inbox/{world['u_complain']}/assign", {"user_id": str(staff_id)})
    assert r.status_code == 200 and r.json()["assigned_to"]["id"] == str(staff_id)
    now_visible = await staff.get(f"/inbox/{world['u_complain']}")
    assert now_visible.status_code == 200

    auditor = await login_as(client, db, "auditor")
    assert (await auditor.get("/inbox")).json()["total"] == 5           # Auditor xem toàn bộ
    assert (await auditor.send("POST", f"/inbox/{world['u_ask']}/silence", {})).status_code == 403


async def test_inbox_silence_hides_item_until_it_expires(world, owner_api: Api, db) -> None:  # type: ignore[no-untyped-def]
    r = await owner_api.send("POST", f"/inbox/{world['u_candidate']}/silence", {"reason": "Đã xem, chưa cần"})
    assert r.status_code == 200
    after = (await owner_api.get("/inbox")).json()
    assert world["u_candidate"] and str(world["u_candidate"]) not in {i["id"] for i in after["items"]}
    assert after["total"] == 4
    log = (await db.execute(text("SELECT action FROM ops.action_log WHERE action = 'queue.silenced'"))).scalars().all()
    assert log == ["queue.silenced"]
    # Silence đã hết hạn (trong quá khứ) thì không còn tác dụng.
    await owner_api.send("POST", f"/inbox/{world['u_ask']}/silence",
                         {"until": "2000-01-01T00:00:00Z"})
    still_there = (await owner_api.get("/inbox")).json()
    assert str(world["u_ask"]) in {i["id"] for i in still_there["items"]}


async def test_inbox_act_unit_creates_draft_alert_acknowledges_draft_needs_workbench(
        world, owner_api: Api, db) -> None:  # type: ignore[no-untyped-def]
    r = await owner_api.send("POST", f"/inbox/{world['u_ask']}/act", {"text": "Dạ em gửi báo giá liền ạ"})
    assert r.status_code == 200, r.text
    assert r.json()["draft"]["code"].startswith("ACT-")
    body = (await db.execute(text("SELECT body->>'text' FROM biz.action_drafts WHERE id = :i"),
                             {"i": r.json()["draft"]["id"]})).scalar_one()
    assert body == "Dạ em gửi báo giá liền ạ"

    r = await owner_api.send("POST", f"/inbox/{world['alert']}/act", {"create_task": True})
    assert r.status_code == 200 and r.json()["status"] == "acknowledged"
    status = (await db.execute(text("SELECT status FROM biz.alerts WHERE id = :i"), {"i": world["alert"]})).scalar_one()
    assert status == "acknowledged"
    task = (await db.execute(text("SELECT source FROM biz.tasks WHERE source = 'alert'"))).scalar_one()
    assert task == "alert"
    # Đã xử lý xong thì rời khỏi hàng đợi đang mở (view chỉ giữ alert status='open') → item không còn thấy nữa.
    again = await owner_api.send("POST", f"/inbox/{world['alert']}/act", {})
    assert again.status_code == 404

    blocked = await owner_api.send("POST", f"/inbox/{world['draft']}/act", {})
    assert blocked.status_code == 409 and blocked.json()["code"] == "USE_WORKBENCH"


async def test_inbox_item_detail_has_evidence_chain(world, owner_api: Api) -> None:  # type: ignore[no-untyped-def]
    r = await owner_api.get(f"/inbox/{world['u_ask']}")
    assert r.status_code == 200
    assert [u["id"] for u in r.json()["units"]] == [str(world["u_ask"])]
    a = await owner_api.get(f"/inbox/{world['alert']}")
    assert a.json()["status"] == "open" and [u["id"] for u in a.json()["units"]] == [str(world["u_ask"])]
    assert (await owner_api.get(f"/inbox/{uuid.uuid4()}")).status_code == 404


# ─── Tổng quan điều hành ────────────────────────────────────────────────────

async def test_overview_kpis_cover_f4_and_link_to_filtered_lists(world, owner_api: Api) -> None:  # type: ignore[no-untyped-def]
    r = await owner_api.get("/overview")
    assert r.status_code == 200, r.text
    body = r.json()
    # F-64 (v0.1.42): đúng 4 số kinh doanh, một hàng, đúng thứ tự.
    assert [k["key"] for k in body["kpis"]] == ["opportunity_claim_rate", "time_to_contact", "quotations_sent",
                                                "pending_ratio"]
    assert all(k["row"] == 1 for k in body["kpis"])
    keys = {k["key"] for k in body["kpis"]}
    assert not keys & {"chassis_latency", "plugins_health", "active_profiles", "channels_live"}
    for k in body["kpis"]:
        assert k["filter"] and k["filter"]["screen"]                # mỗi ô dẫn tới màn đã lọc
    by_key = {k["key"]: k for k in body["kpis"]}
    assert by_key["opportunity_claim_rate"]["filter"] == {"screen": "opportunity", "filters": {"owner": "none"}}
    # Cùng điều kiện với số đếm (đã gửi) để đối chiếu được; bộ lọc ngày 30 ngày còn là Nợ.
    assert by_key["quotations_sent"]["filter"] == {"screen": "workbench",
                                                   "filters": {"kind": "quotation", "status": "sent"}}
    assert "plugins" not in body["health"]                          # F-41: không đếm ops.plugins nữa
    tech = body["health"]["tech"]
    assert set(tech) == {"channels_live", "groups_listening", "events_today", "processing_latency_s"}
    assert tech["groups_listening"] == 1
    assert isinstance(tech["channels_live"], int) and isinstance(tech["events_today"], int)
    pending = by_key["pending_ratio"]["value"]
    assert pending == 100.0                                          # 1 bản nháp, đang pending
    kinds = {i["kind"] for i in body["queue"]}
    assert {"alert", "draft", "due"} <= kinds
    due_ids = {i["id"] for i in body["queue"] if i["kind"] == "due"}
    assert str(world["overdue_task"]) in due_ids and str(world["soon_task"]) in due_ids


async def test_overview_operator_scope_limits_only_the_queue_block(world, owner_api: Api, client, db) -> None:  # type: ignore[no-untyped-def]
    operator = await login_as(client, db, "operator")
    full = (await owner_api.get("/overview")).json()
    scoped = (await operator.get("/overview")).json()
    assert {k["key"]: k["value"] for k in scoped["kpis"]} == {k["key"]: k["value"] for k in full["kpis"]}
    assert scoped["queue"] == []                                     # chưa được giao gì
    await assign_scope(db, "operator@example.vn", "person", world["p_ask"])
    now_visible = (await operator.get("/overview")).json()
    assert any(i["id"] == str(world["u_ask"]) for i in now_visible["queue"]) or \
        any(i["id"] == str(world["alert"]) for i in now_visible["queue"])


# ─── Việc & Nhắc hẹn ────────────────────────────────────────────────────────

async def test_tasks_overdue_flag_and_lifecycle(world, owner_api: Api) -> None:  # type: ignore[no-untyped-def]
    r = await owner_api.get("/tasks?overdue=true")
    assert r.status_code == 200
    assert [i["id"] for i in r.json()["items"]] == [str(world["overdue_task"])]
    assert r.json()["items"][0]["overdue"] is True

    soon = await owner_api.get(f"/tasks/{world['soon_task']}")
    assert soon.json()["overdue"] is False

    created = await owner_api.send("POST", "/tasks", {"title": "Gọi báo giá", "priority": "P2",
                                                       "subject": {"type": "person", "id": str(world["p_ask"])}})
    assert created.status_code == 201 and created.json()["source"] == "manual"
    tid = created.json()["id"]
    done = await owner_api.send("PATCH", f"/tasks/{tid}", {"status": "done"})
    assert done.status_code == 200 and done.json()["status"] == "done" and done.json()["completed_at"]


async def test_tasks_due_before_filters_by_deadline_on_the_server(world, owner_api: Api, db) -> None:  # type: ignore[no-untyped-def]
    """F-60-R14 (sau /code-review): "Việc hôm nay" của Mặt tiền không được rơi việc quá hạn vì 50 việc mới nhất.
    60 việc MỚI hơn (hạn xa / không hạn) không che việc quá hạn tạo sớm khi lọc `due_before`."""
    await db.execute(text("""
        INSERT INTO biz.tasks (org_id, code, title, priority, status, due_at, source, created_at)
        SELECT :o, 'TSK-N' || g, 'Việc mới ' || g, 'P3', 'todo',
               CASE WHEN g % 2 = 0 THEN now() + interval '10 days' END, 'manual', now() + make_interval(secs => g)
        FROM generate_series(1, 60) g"""), {"o": world["org"]})
    await db.commit()
    plain = (await owner_api.get("/tasks?status=todo")).json()
    assert str(world["overdue_task"]) not in [i["id"] for i in plain["items"]]          # lỗi cũ: rơi khỏi trang 50
    cut = (datetime.now(UTC) + timedelta(hours=12)).isoformat().replace("+00:00", "Z")
    r = await owner_api.get("/tasks", params={"status": "todo", "due_before": cut})
    assert r.status_code == 200, r.text
    assert [i["id"] for i in r.json()["items"]] == [str(world["overdue_task"])] and r.json()["total"] == 1
    later = (datetime.now(UTC) + timedelta(days=2)).isoformat().replace("+00:00", "Z")   # gồm cả việc hạn +1 ngày
    both = (await owner_api.get("/tasks", params={"status": "todo", "due_before": later})).json()
    assert {i["id"] for i in both["items"]} == {str(world["overdue_task"]), str(world["soon_task"])}
    naive = (await owner_api.get("/tasks", params={"due_before": "2999-01-01T00:00:00"})).json()   # không múi giờ ⇒ UTC
    assert naive["total"] == 2 + 30                    # 2 việc cũ + 30 việc có hạn +10 ngày
    assert (await owner_api.get("/tasks?due_before=khong-phai-gio")).status_code == 422


async def test_tasks_scope_by_assignee_and_subject(world, owner_api: Api, client, db) -> None:  # type: ignore[no-untyped-def]
    staff = await login_as(client, db, "agent_staff")
    assert (await staff.get("/tasks")).json()["total"] == 0
    staff_id = (await db.execute(text("SELECT id FROM core.users WHERE email = 'agent_staff@example.vn'"))).scalar_one()
    await owner_api.send("PATCH", f"/tasks/{world['overdue_task']}", {"assignee_user_id": str(staff_id)})
    mine = (await staff.get("/tasks")).json()
    assert [i["id"] for i in mine["items"]] == [str(world["overdue_task"])]


async def test_promises_upcoming_overdue_and_kept(world, owner_api: Api) -> None:  # type: ignore[no-untyped-def]
    upcoming = await owner_api.get("/tasks/promises?status=upcoming")
    assert upcoming.status_code == 200, upcoming.text
    assert [p["id"] for p in upcoming.json()["items"]] == [str(world["promise"])]
    assert upcoming.json()["items"][0]["broken"] is False

    kept = await owner_api.send("PATCH", f"/tasks/promises/{world['promise']}", {"kept": True})
    assert kept.status_code == 200
    assert (await owner_api.get("/tasks/promises?status=upcoming")).json()["total"] == 0
    assert (await owner_api.get("/tasks/promises?status=kept")).json()["total"] == 1


# ─── Cảnh báo sớm (quét ngưỡng) ──────────────────────────────────────────────

async def test_early_warning_scan_generates_and_dedupes(world, db, redis) -> None:  # type: ignore[no-untyped-def]
    import datetime as dt

    org = world["org"]
    # Khách lạnh: cần ≥ 2 đơn vị và đơn vị mới nhất quá 14 ngày → thêm một đơn vị nữa cho p_ask rồi đẩy lùi cả hai.
    await _unit(db, org, group_id=world["group"], person_id=world["p_ask"], event_type="AskedPrice",
               conclusion="Hỏi giá lần trước")
    await db.execute(text("UPDATE clean.meaning_units SET observed_at = now() - interval '20 days' "
                          "WHERE person_id = :p"), {"p": world["p_ask"]})
    # Lời hứa đã quá hạn (world fixture đặt nó "sắp đến hạn" cho test khác; đẩy về quá khứ ở đây).
    await db.execute(text("UPDATE biz.promises SET due_at = now() - interval '2 hours' WHERE id = :i"),
                     {"i": world["promise"]})
    # Cơ hội chưa ai nhận, tạo hơn 24 giờ trước.
    opp_code = (await db.execute(text("SELECT core.next_code('OPP')"))).scalar_one()
    await db.execute(text("""INSERT INTO biz.opportunities (org_id, code, person_id, need, stage, confidence,
                                                            first_signal_at, created_at)
        VALUES (:o, :c, :p, 'Mua thép cuộn', 'validated', 'high', now() - interval '2 days',
                now() - interval '2 days')"""), {"o": org, "c": opp_code, "p": world["p_ask"]})
    await db.commit()
    # Tin đến chưa được trả lời hơn 60 phút, có người phụ trách (raw.events chỉ INSERT nên tạo tin mới đã cũ sẵn).
    sm = sessionmaker()
    old_ts = (dt.datetime.now(dt.UTC) - dt.timedelta(hours=2)).isoformat()
    [raw_slow] = await put(sm, org, {**msg("Còn hàng không ạ, mình cần gấp", sender="u4", name="Chị Hoa"),
                                     "occurred_at": old_ts})
    p_slow = await _person_of(db, raw_slow)
    # v0.1.67 (F050): chỉ tin có kết luận "đang chờ trả lời" mới sinh cảnh báo Chưa trả lời.
    await _unit(db, org, group_id=world["group"], person_id=p_slow, event_type="AskedStatus",
                conclusion="Hỏi còn hàng, cần gấp", raw_id=raw_slow)
    owner_id = (await db.execute(text("SELECT id FROM core.users WHERE email = 'owner@example.vn'"))).scalar_one()
    await db.execute(text("UPDATE core.persons SET owner_user_id = :u WHERE id = :p"),
                     {"u": owner_id, "p": p_slow})
    # Đối thủ xuất hiện (người khác p_ask — nếu cùng người thì tin mới lại làm p_ask hết "lạnh").
    u_comp = await _unit(db, org, group_id=world["group"], person_id=world["p_complain"],
                         event_type="MentionsCompetitor", conclusion="Nhắc tới bên ABC rẻ hơn 10%")
    await db.commit()

    out = await queue_jobs.early_warning_scan({"redis_bus": redis})
    # world["alert"] đã là một cảnh báo customer_cooling có sẵn cho p_ask (chống trùng nên lượt quét này không
    # tạo thêm — đúng ý nghĩa "chống trùng"); 4 loại còn lại là mới.
    assert out[str(org)] >= 4

    types = {r.alert_type for r in (await db.execute(
        text("SELECT alert_type FROM biz.alerts WHERE org_id = :o"), {"o": org})).all()}
    assert {"customer_cooling", "unclaimed_opportunity", "slow_response", "competitor",
            "forgotten_deadline"} <= types

    slow = (await db.execute(text("SELECT personnel_related FROM biz.alerts WHERE alert_type = 'slow_response'"))
           ).scalar_one()
    assert slow is True                                              # có owner_user_id cụ thể

    broken = (await db.execute(text("SELECT broken FROM biz.promises WHERE id = :i"),
                               {"i": world["promise"]})).scalar_one()
    assert broken is True

    before = (await db.execute(text("SELECT count(*) FROM biz.alerts WHERE org_id = :o"), {"o": org})).scalar_one()
    await queue_jobs.early_warning_scan({"redis_bus": redis})
    after = (await db.execute(text("SELECT count(*) FROM biz.alerts WHERE org_id = :o"), {"o": org})).scalar_one()
    assert after == before                                           # chạy lại không sinh trùng
    _ = u_comp


async def _walk_pages(api: Api, path: str, limit: int) -> list[str]:
    """Đi hết các trang bằng next_cursor, trả id theo thứ tự nhận."""
    ids: list[str] = []
    cursor: str | None = None
    for _ in range(40):
        params: dict[str, str | int] = {"limit": limit}
        if cursor:
            params["cursor"] = cursor
        r = await api.get(path, params=params)
        assert r.status_code == 200, r.text
        body = r.json()
        ids += [i["id"] for i in body["items"]]
        cursor = body["next_cursor"]
        if not cursor:
            return ids
    raise AssertionError("con trỏ không dừng")


async def test_inbox_cursor_keeps_rows_sharing_created_at(world, owner_api: Api, db) -> None:  # type: ignore[no-untyped-def]
    """P2-3 (A4): một tin sinh nhiều đơn vị ý nghĩa cùng observed_at ⇒ con trỏ `created_at < :c` làm rơi dòng cùng
    thời điểm ở ranh giới trang. Con trỏ nay kèm khoá phụ (item_type, item_id)."""
    org = world["org"]
    gid = (await db.execute(text("SELECT id FROM core.groups LIMIT 1"))).scalar_one()
    same = datetime(2031, 1, 1, 12, 0, 0, tzinfo=UTC)          # mới nhất ⇒ nằm đầu danh sách
    ids = set()
    for k in range(7):
        uid = (await db.execute(text("""
            INSERT INTO clean.meaning_units (org_id, observed_at, group_id, event_type, conclusion, confidence, run_id)
            VALUES (:o, :t, :g, 'Complained', :c, 0.9, core.uuid_v7()) RETURNING id"""),
            {"o": org, "t": same, "g": gid, "c": f"Than phiền số {k}"})).scalar_one()
        ids.add(str(uid))
    await db.commit()
    everything = (await owner_api.get("/inbox", params={"limit": 200})).json()
    expect = [i["id"] for i in everything["items"]]
    assert ids <= set(expect)
    for limit in (1, 2, 3):                                     # ranh giới trang rơi giữa nhóm dòng cùng thời điểm
        walked = await _walk_pages(owner_api, "/inbox", limit)
        assert len(walked) == len(set(walked)), f"trùng dòng, limit={limit}"
        assert set(walked) == set(expect), f"rơi dòng, limit={limit}"
    old_style = (await owner_api.get("/inbox", params={"limit": 5, "cursor": "2030-01-01T00:00:00Z"})).json()
    assert old_style["items"]                                   # con trỏ kiểu cũ (chỉ thời điểm) vẫn nhận
    assert (await owner_api.get("/inbox", params={"cursor": "hỏng|xx"})).status_code == 422


async def test_tasks_cursor_keeps_rows_sharing_created_at(world, owner_api: Api, db) -> None:  # type: ignore[no-untyped-def]
    """P2-3 (A4): nhập hàng loạt trong một giao dịch ⇒ nhiều việc cùng created_at; con trỏ kèm id."""
    await db.execute(text("""
        INSERT INTO biz.tasks (org_id, code, title, priority, status, source, created_at)
        SELECT :o, 'TSK-S' || g, 'Việc cùng giờ ' || g, 'P3', 'todo', 'manual', timestamptz '2031-01-01 12:00:00+00'
        FROM generate_series(1, 9) g"""), {"o": world["org"]})
    await db.commit()
    expect = {i["id"] for i in (await owner_api.get("/tasks", params={"limit": 200})).json()["items"]}
    for limit in (1, 2, 4):
        walked = await _walk_pages(owner_api, "/tasks", limit)
        assert len(walked) == len(set(walked)), f"trùng dòng, limit={limit}"
        assert set(walked) == expect, f"rơi dòng, limit={limit}"
    old_style = (await owner_api.get("/tasks", params={"cursor": "2030-01-01T00:00:00Z"})).json()
    assert isinstance(old_style["items"], list)
    assert (await owner_api.get("/tasks", params={"cursor": "hỏng|xx"})).status_code == 422
