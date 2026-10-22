"""Con người & Chất lượng: Đánh giá con người + Phản biện (khoá mức Owner, Q4 — Auditor thấy nhật ký không thấy
nội dung, Manager bị chặn hẳn; sửa điểm tay giữ lịch sử), Chất lượng chăm sóc (lưới phản hồi theo khung giờ,
lỗi lặp lại, kịch bản thắng/mất), trình thiết lập bước 8–9."""

import uuid
from datetime import UTC, datetime, time, timedelta

import pytest
from sqlalchemy import text

from gh.biz.people.jobs import recompute_people_reviews_org
from gh.db import sessionmaker
from tests.conftest import OWNER, Api
from tests.phase2 import listen, msg, org_id, put
from tests.test_rbac_api import login_as

# Neo cố định trong quá khứ (đủ xa "bây giờ" để mọi cửa sổ mặc định — 30 ngày của care, 7 ngày của job — đều phủ
# được, và để "chưa trả lời" tự nhiên vượt ngưỡng 24 giờ "bỏ rơi" mà không cần chờ thật).
BASE_DATE = (datetime.now(UTC) - timedelta(days=3)).date()
BASE = datetime.combine(BASE_DATE, time(9, 0), tzinfo=UTC)


def dt(mins: float) -> datetime:
    return BASE + timedelta(minutes=mins)


async def _person_of(db, raw_id):  # type: ignore[no-untyped-def]
    return (await db.execute(text("""SELECT pi.person_id FROM raw.events e
                                     JOIN core.person_identities pi ON pi.id = e.sender_identity_id
                                     WHERE e.id = :r"""), {"r": raw_id})).scalar_one()


async def _set_type(db, person_id, person_type: str) -> None:  # type: ignore[no-untyped-def]
    await db.execute(text("UPDATE core.persons SET person_type = :t WHERE id = :i"), {"t": person_type, "i": person_id})


async def _unit(db, org, *, group_id, person_id, event_type: str, conclusion: str, observed_at: datetime,
                raw_id=None) -> uuid.UUID:  # type: ignore[no-untyped-def]
    uid = (await db.execute(text("""
        INSERT INTO clean.meaning_units (org_id, observed_at, group_id, person_id, event_type, conclusion,
                                         entities, confidence, run_id)
        VALUES (:o, :obs, :g, :p, :et, :c, '{}'::jsonb, 0.9, core.uuid_v7()) RETURNING id"""),
        {"o": org, "obs": observed_at, "g": group_id, "p": person_id, "et": event_type, "c": conclusion})
         ).scalar_one()
    if raw_id is not None:
        await db.execute(text("""
            INSERT INTO clean.evidence (meaning_unit_id, meaning_observed_at, raw_event_id, raw_received_at, quote)
            SELECT mu.id, mu.observed_at, e.id, e.received_at, e.body_text
            FROM clean.meaning_units mu, raw.events e WHERE mu.id = :u AND e.id = :r"""), {"u": uid, "r": raw_id})
    return uid


@pytest.fixture
async def world(app, db, redis, owner_api):  # type: ignore[no-untyped-def]
    """Tổ chức đã khởi tạo, nhóm g1 đang nghe. Một nhân viên (An) trả lời 3 khách với 3 tốc độ khác nhau (nhanh
    <15p / vừa 15–60p / chậm >60p) + một khách bị bỏ rơi (không trả lời). Một lời hứa của An với Bình bị vỡ. Một
    deal đã thắng của Lan trong cửa sổ 14 ngày trước khi chốt trùng với lượt phản hồi nhanh."""
    org = await org_id(db)
    gid = await listen(db, org, "g1")
    sm = sessionmaker()

    def inbound(body, *, sender, name, mins):  # type: ignore[no-untyped-def]
        p = msg(body, sender=sender, name=name)
        p["occurred_at"] = dt(mins).isoformat()
        return p

    def outbound(body, *, sender, name, mins):  # type: ignore[no-untyped-def]
        p = msg(body, sender=sender, name=name)
        p["direction"], p["occurred_at"] = "outbound", dt(mins).isoformat()
        return p

    [raw_ask1] = await put(sm, org, inbound("Cho hỏi giá thép?", sender="lan", name="Chị Lan", mins=0))
    [raw_reply1] = await put(sm, org, outbound("Dạ giá 20 triệu/tấn ạ", sender="an", name="An CSKH", mins=5))
    [raw_ask2] = await put(sm, org, inbound("Bao giờ giao hàng?", sender="binh", name="Anh Bình", mins=120))
    [raw_reply2] = await put(sm, org, outbound("Dạ giao trong tuần này ạ", sender="an", name="An CSKH", mins=150))
    [raw_ask3] = await put(sm, org, inbound("Giá gỗ MDF bao nhiêu?", sender="lan", name="Chị Lan", mins=300))
    [raw_reply3] = await put(sm, org, outbound("Dạ để em kiểm tra rồi báo lại ạ", sender="an", name="An CSKH",
                                               mins=400))
    [raw_ask4] = await put(sm, org, inbound("Anh chị ơi có ai không ạ?", sender="chi", name="Chị Chi", mins=500))
    await db.commit()

    p_lan = await _person_of(db, raw_ask1)
    p_binh = await _person_of(db, raw_ask2)
    p_chi = await _person_of(db, raw_ask4)
    p_an = await _person_of(db, raw_reply1)
    await _set_type(db, p_an, "staff")
    for pid in (p_lan, p_binh, p_chi):
        await _set_type(db, pid, "customer")

    await _unit(db, org, group_id=gid, person_id=p_lan, event_type="AskedPrice", conclusion="Hỏi giá thép",
               observed_at=dt(0), raw_id=raw_ask1)
    await _unit(db, org, group_id=gid, person_id=p_binh, event_type="AskedPrice", conclusion="Hỏi giao hàng",
               observed_at=dt(120), raw_id=raw_ask2)
    await _unit(db, org, group_id=gid, person_id=p_lan, event_type="AskedPrice", conclusion="Hỏi giá gỗ",
               observed_at=dt(300), raw_id=raw_ask3)

    await db.execute(text("""INSERT INTO biz.promises (org_id, promiser_person_id, to_person_id, text, due_at,
                             broken) VALUES (:o, :p, :t, 'Sẽ gọi lại trong ngày', :due, true)"""),
                     {"o": org, "p": p_an, "t": p_binh, "due": dt(200)})
    await db.commit()

    code = (await db.execute(text("SELECT core.next_code('DEA')"))).scalar_one()
    deal_id = (await db.execute(text("""INSERT INTO biz.deals (org_id, code, person_id, amount_vnd, status,
                                        won_at) VALUES (:o, :c, :p, 500000000, 'won', :w) RETURNING id"""),
                                {"o": org, "c": code, "p": p_lan, "w": dt(600)})).scalar_one()
    await db.commit()

    n = await recompute_people_reviews_org(db, org, today=BASE_DATE + timedelta(days=1))
    await db.commit()

    review_id = (await db.execute(text("SELECT id FROM biz.people_reviews WHERE person_id = :p"),
                                  {"p": p_an})).scalar_one()
    return {"org": org, "group": gid, "p_lan": p_lan, "p_binh": p_binh, "p_chi": p_chi, "p_an": p_an,
            "deal_id": deal_id, "review_id": review_id, "recomputed": n}


async def _pin(api: Api) -> None:
    r = await api.send("POST", "/auth/pin/verify", {"pin": OWNER["pin"]})
    assert r.status_code == 200, r.text


# ═══ Đánh giá con người: job tự động + khoá mức Owner (Q4) ════════════════════

async def test_job_computes_score_with_evidence_and_no_disciplinary_action(world, db) -> None:  # type: ignore[no-untyped-def]
    assert world["recomputed"] >= 1
    row = (await db.execute(text("""SELECT score, trend, signal, recommendation, evidence, visibility
                                    FROM biz.people_reviews WHERE id = :i"""), {"i": world["review_id"]})).one()
    assert 0 <= float(row.score) <= 100
    assert row.trend == "flat"          # không có kỳ trước để so sánh
    assert row.visibility == "owner"
    assert len(row.evidence) > 0        # khoá cứng 7: không chứng cứ thì không ghi điểm
    assert "vỡ" in row.signal or "chậm" in row.signal or "nhanh" in row.signal
    # Không có hành động kỷ luật tự động: job không đụng gì tới quyền, alert, hay việc của ai.
    n_alerts = (await db.execute(text("SELECT count(*) FROM biz.alerts WHERE org_id = :o"),
                                 {"o": world["org"]})).scalar_one()
    assert n_alerts == 0

    # Chạy lại cùng kỳ không sinh thêm dòng (UPDATE tại-chỗ dòng hệ thống).
    before = (await db.execute(text("SELECT count(*) FROM biz.people_reviews WHERE person_id = :p"),
                               {"p": world["p_an"]})).scalar_one()
    await recompute_people_reviews_org(db, world["org"], today=BASE_DATE + timedelta(days=1))
    await db.commit()
    after = (await db.execute(text("SELECT count(*) FROM biz.people_reviews WHERE person_id = :p"),
                              {"p": world["p_an"]})).scalar_one()
    assert before == after


async def test_owner_sees_full_content_with_pin(world, owner_api: Api) -> None:  # type: ignore[no-untyped-def]
    r = await owner_api.get("/people/reviews")
    assert r.status_code == 423 and r.json()["code"] == "PIN_REQUIRED"   # chưa mở phiên PIN
    await _pin(owner_api)
    r = await owner_api.get("/people/reviews")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] >= 1
    item = next(i for i in body["items"] if i["id"] == str(world["review_id"]))
    assert item["person"]["id"] == str(world["p_an"]) and item["score"] is not None
    assert item["evidence"]
    # v0.1.45 (F-60): tin bình thường → không gắn cờ 'Đáng ngờ'.
    assert item["suspicious"] is False and item["suspicious_reason"] is None

    detail = await owner_api.get(f"/people/reviews/{world['review_id']}")
    assert detail.status_code == 200
    d = detail.json()
    assert d["history"] and d["history"][0]["score"] == item["score"]
    assert d["disputes"] == []

    logs = (await owner_api.get("/audit")).json()["items"]
    assert any(entry["action"] == "people_review.viewed" and entry["target_id"] == str(world["review_id"])
              for entry in logs)
    # Xem danh sách KHÔNG ghi log mỗi lần (quá dày) — chỉ xem một đánh giá cụ thể mới ghi.
    assert not any(entry["action"] == "people_review.viewed" and entry["target_id"] is None for entry in logs)


async def test_board_filter_maps_to_person_type(world, owner_api: Api) -> None:  # type: ignore[no-untyped-def]
    await _pin(owner_api)
    employees = (await owner_api.get("/people/reviews?board=employee")).json()
    assert all(i["person"]["type"] == "staff" for i in employees["items"])
    customers = (await owner_api.get("/people/reviews?board=customer")).json()
    assert str(world["review_id"]) not in {i["id"] for i in customers["items"]}


async def test_auditor_sees_log_not_content_and_own_view_is_logged(world, client, db) -> None:  # type: ignore[no-untyped-def]
    auditor = await login_as(client, db, "auditor")
    r = await auditor.get("/people/reviews")
    assert r.status_code == 200, r.text                       # 200, KHÔNG 403
    body = r.json()
    item = next(i for i in body["items"] if i["id"] == str(world["review_id"]))
    assert "score" not in item and "signal" not in item and "evidence" not in item
    assert item["has_content"] is True
    assert isinstance(item["viewed_by"], list)

    detail = await auditor.get(f"/people/reviews/{world['review_id']}")
    assert detail.status_code == 200
    dbody = detail.json()
    assert "score" not in dbody and dbody["has_content"] is True and "dispute_count" in dbody

    rows = (await db.execute(text("""SELECT count(*) FROM ops.action_log
                                     WHERE action = 'people_review.audit_viewed' AND target_id = :i"""),
                             {"i": str(world["review_id"])})).scalar_one()
    assert rows >= 1                    # mỗi lần Auditor xem cũng tự ghi vào chính nhật ký đó

    # Auditor không cần PIN (không thấy nội dung).
    assert (await auditor.send("PATCH", f"/people/reviews/{world['review_id']}",
                               {"score": 10, "reason": "x", "evidence": [{"type": "meaning_unit", "id": "x"}]})
           ).status_code == 403


async def test_explain_review_needs_pin_and_logs_each_view(world, owner_api: Api, db) -> None:  # type: ignore[no-untyped-def]
    r = await owner_api.get(f"/explain/review/{world['review_id']}")
    assert r.status_code == 423 and r.json()["code"] == "PIN_REQUIRED"
    await _pin(owner_api)
    r2 = await owner_api.get(f"/explain/review/{world['review_id']}")
    assert r2.status_code == 200, r2.text
    body = r2.json()
    assert body["kind"] == "review" and body["id"] == str(world["review_id"])
    assert body["units"]              # có chứng cứ (đơn vị ý nghĩa của khách An đã trả lời trong kỳ)

    n = (await db.execute(text("""SELECT count(*) FROM ops.action_log
                                  WHERE action = 'people_review.explained' AND target_id = :i"""),
                          {"i": str(world["review_id"])})).scalar_one()
    assert n >= 1


async def test_manager_is_blocked(world, client, db) -> None:  # type: ignore[no-untyped-def]
    manager = await login_as(client, db, "manager")
    assert (await manager.get("/people/reviews")).status_code == 403
    assert (await manager.get(f"/people/reviews/{world['review_id']}")).status_code == 403
    assert (await manager.get(f"/explain/review/{world['review_id']}")).status_code == 403


async def test_edit_score_keeps_history_not_overwrite(world, owner_api: Api, db) -> None:  # type: ignore[no-untyped-def]
    await _pin(owner_api)
    old = (await owner_api.get(f"/people/reviews/{world['review_id']}")).json()
    r = await owner_api.send("PATCH", f"/people/reviews/{world['review_id']}",
                             {"score": 95.5, "reason": "Đã xác minh lại, khách phản hồi rất hài lòng",
                              "evidence": [{"type": "meaning_unit", "id": str(uuid.uuid4())}]})
    assert r.status_code == 200, r.text
    new = r.json()
    assert new["id"] != str(world["review_id"]) and new["score"] == 95.5
    assert new["overridden"] is True and new["override_reason"].startswith("Đã xác minh")
    assert new["supersedes_id"] == str(world["review_id"])

    # Dòng cũ vẫn nguyên vẹn (không bị UPDATE/xoá).
    old_row = (await db.execute(text("SELECT score FROM biz.people_reviews WHERE id = :i"),
                                {"i": world["review_id"]})).one()
    assert float(old_row.score) == old["score"]

    # Danh sách chỉ trả bản hiện hành (mới nhất).
    listing = (await owner_api.get("/people/reviews?person_id=" + str(world["p_an"]))).json()
    ids = {i["id"] for i in listing["items"]}
    assert new["id"] in ids and str(world["review_id"]) not in ids

    # Lịch sử đầy đủ ở chi tiết.
    detail = (await owner_api.get(f"/people/reviews/{new['id']}")).json()
    scores = [h["score"] for h in detail["history"]]
    assert new["score"] in scores and old["score"] in scores

    # Job chạy lại không đụng tới bản đã sửa tay (chỉ UPDATE dòng hệ thống `overridden_by IS NULL`).
    await recompute_people_reviews_org(db, world["org"], today=BASE_DATE + timedelta(days=1))
    await db.commit()
    still = (await db.execute(text("SELECT score, overridden_by FROM biz.people_reviews WHERE id = :i"),
                              {"i": new["id"]})).one()
    assert float(still.score) == 95.5 and still.overridden_by is not None


async def test_edit_score_requires_evidence(world, owner_api: Api) -> None:  # type: ignore[no-untyped-def]
    await _pin(owner_api)
    r = await owner_api.send("PATCH", f"/people/reviews/{world['review_id']}",
                             {"score": 50, "reason": "x", "evidence": []})
    assert r.status_code == 422


# ═══ Phản biện (spec I) ════════════════════════════════════════════════════════

async def test_dispute_create_and_resolve_does_not_touch_score(world, owner_api: Api, db) -> None:  # type: ignore[no-untyped-def]
    await _pin(owner_api)
    r = await owner_api.send("POST", f"/people/reviews/{world['review_id']}/disputes",
                             {"body": "An cho rằng điểm chậm quá do khách hỏi ngoài giờ, không có mặt để trả lời"})
    assert r.status_code == 201, r.text
    dispute = r.json()
    assert dispute["status"] == "open" and dispute["review_id"] == str(world["review_id"])

    before_score = (await db.execute(text("SELECT score FROM biz.people_reviews WHERE id = :i"),
                                     {"i": world["review_id"]})).scalar_one()

    r2 = await owner_api.send("PATCH", f"/people/reviews/disputes/{dispute['id']}",
                              {"status": "resolved", "resolution": "Đồng ý, sẽ xem xét lại điểm ở lần chấm sau"})
    assert r2.status_code == 200, r2.text
    resolved = r2.json()
    assert resolved["status"] == "resolved" and resolved["resolved_by"]["id"]

    # Không hành động kỷ luật tự động: giải quyết phản biện KHÔNG tự đổi điểm.
    after_score = (await db.execute(text("SELECT score FROM biz.people_reviews WHERE id = :i"),
                                    {"i": world["review_id"]})).scalar_one()
    assert before_score == after_score

    again = await owner_api.send("PATCH", f"/people/reviews/disputes/{dispute['id']}",
                                 {"status": "rejected", "resolution": "x"})
    assert again.status_code == 409 and again.json()["code"] == "DISPUTE_DECIDED"

    detail = (await owner_api.get(f"/people/reviews/{world['review_id']}")).json()
    assert len(detail["disputes"]) == 1 and detail["disputes"][0]["status"] == "resolved"


# ═══ Chất lượng chăm sóc ═══════════════════════════════════════════════════════

async def test_response_times_grid_buckets_correctly(world, owner_api: Api) -> None:  # type: ignore[no-untyped-def]
    r = await owner_api.get("/care/response-times")
    assert r.status_code == 200, r.text
    body = r.json()
    row = next(i for i in body["items"] if i["staff"]["id"] == str(world["p_an"]))
    assert (row["fast"], row["normal"], row["slow"]) == (1, 1, 1)
    assert row["total_answered"] == 3
    assert body["totals"]["fast"] >= 1
    assert body["unattended"] >= 1       # tin của Chi chưa ai trả lời — không gắn được vào nhân viên nào


async def test_repeated_issues_broken_promise_and_abandoned_customer(world, owner_api: Api) -> None:  # type: ignore[no-untyped-def]
    r = await owner_api.get("/care/repeated-issues")
    assert r.status_code == 200, r.text
    body = r.json()
    broken = [i for i in body["items"] if i["kind"] == "broken_promise"]
    assert any(i["subject"]["id"] == str(world["p_an"]) and i["count"] == 1 for i in broken)

    abandoned = [i for i in body["items"] if i["kind"] == "abandoned_customer"]
    assert any(i["subject"]["id"] == str(world["p_chi"]) for i in abandoned)
    assert not any(i["subject"]["id"] == str(world["p_lan"]) for i in abandoned)   # Lan luôn được trả lời

    only_broken = (await owner_api.get("/care/repeated-issues?issue_type=broken_promise")).json()
    assert all(i["kind"] == "broken_promise" for i in only_broken["items"])
    assert only_broken["next_cursor"] is None


async def test_care_scenarios_won_deal_shows_response_stats(world, owner_api: Api) -> None:  # type: ignore[no-untyped-def]
    r = await owner_api.get("/care/scenarios?status=won")
    assert r.status_code == 200, r.text
    body = r.json()
    item = next(i for i in body["items"] if i["deal"]["id"] == str(world["deal_id"]))
    assert item["deal"]["status"] == "won" and item["person"]["id"] == str(world["p_lan"])
    assert item["response"] is not None and item["response"]["fast"] >= 1
    assert "Kịch bản thắng" in item["note"]
    assert body["summary"]["won"]["count"] >= 1


async def test_care_requires_permission(world, client, db) -> None:  # type: ignore[no-untyped-def]
    manager = await login_as(client, db, "manager")
    assert (await manager.get("/care/response-times")).status_code == 403


# ═══ Trình thiết lập bước 8–9 ══════════════════════════════════════════════════

async def test_step8_and_step9_require_prior_steps_then_create_agent(owner_api: Api, db) -> None:  # type: ignore[no-untyped-def]
    # v0.1.68 (F026): bước 8 KHÔNG còn đòi bước 4 'done' ("Để sau" bước 4 vẫn tạo được agent) — chỉ đòi bước 1–3.
    # Bước 9 vẫn đòi agent của bước 8 (kiểm bên dưới); tạo agent ở lượt đầu rồi xoá để bước 9 còn "chưa có agent".
    r = await owner_api.send("PUT", "/setup/steps/8", {"name": "Trợ lý Mai", "role_desc": "Chăm sóc khách hàng",
                                                        "try_message": "Chào bạn"})
    assert r.status_code == 200, r.text
    await db.execute(text("DELETE FROM agent.identities"))
    await db.commit()

    org = (await db.execute(text("SELECT id FROM core.organizations"))).scalar_one()
    done = {"steps": {"1": "done", "2": "done", "3": "done", "4": "done", "5": "done", "6": "done", "7": "done"}}
    await db.execute(text("UPDATE ops.setup_state SET completed = CAST(:c AS jsonb) WHERE org_id = :o"),
                     {"c": '{"steps": {"1": "done", "2": "done", "3": "done", "4": "done", "5": "done", '
                           '"6": "done", "7": "done"}}', "o": org})
    await db.commit()
    _ = done

    r2 = await owner_api.send("PUT", "/setup/steps/9", {"autonomy_level": 4, "ack_boundaries": True})
    assert r2.status_code == 409 and r2.json()["code"] == "STEP_INCOMPLETE"   # bước 8 chưa xong

    r3 = await owner_api.send("PUT", "/setup/steps/8", {"name": "Trợ lý Mai", "role_desc": "Chăm sóc khách hàng",
                                                         "try_message": "Chào bạn, giới thiệu bản thân nhé"})
    assert r3.status_code == 200, r3.text
    body3 = r3.json()
    assert body3["agent"]["name"] == "Trợ lý Mai"
    # Chưa cấu hình model nào ở bước 4 (test không dựng provider thật) → thử trò chuyện lỗi nhưng KHÔNG chặn bước.
    assert body3["agent"]["try_reply"] is None and isinstance(body3["agent"]["try_error"], str)
    agent_id = body3["agent"]["id"]
    row = (await db.execute(text("SELECT autonomy_level FROM agent.identities WHERE id = :i"),
                            {"i": agent_id})).scalar_one()
    assert row == 4   # mặc định policy.DEFAULT_AUTONOMY trước khi bước 9 đặt lại

    r4 = await owner_api.send("PUT", "/setup/steps/9", {"autonomy_level": 3, "ack_boundaries": False})
    assert r4.status_code == 422

    r5 = await owner_api.send("PUT", "/setup/steps/9", {"autonomy_level": 3, "ack_boundaries": True})
    assert r5.status_code == 200, r5.text
    body5 = r5.json()
    assert body5["agent"]["autonomy_level"] == 3 and len(body5["hard_boundaries"]) == 8
    row2 = (await db.execute(text("SELECT autonomy_level FROM agent.identities WHERE id = :i"),
                             {"i": agent_id})).scalar_one()
    assert row2 == 3

    state = (await owner_api.get("/setup/state")).json()
    done_map = {s["n"]: s["status"] for s in state["steps"]}
    assert done_map[8] == "done" and done_map[9] == "done"
