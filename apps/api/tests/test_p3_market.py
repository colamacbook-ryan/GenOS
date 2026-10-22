"""Cơ hội & Thị trường (PLAN §3.8–3.10 + §3.14 "Deal & Vụ việc"): kéo thả cơ hội ghi lịch sử + tổng pipeline,
chấm điểm ghép Cung↔Cầu có lý do, "Giới thiệu hai bên" tạo bản nháp chờ duyệt, Kho hội thoại trả về người qua
facet, deal/case đổi trạng thái + người xử lý, phạm vi theo vai trò."""

import uuid

import orjson
import pytest
from sqlalchemy import text

from gh.biz.hooks import HookCtx
from gh.biz.market import service as msvc
from gh.biz.market.jobs import market_signal_capture, recompute_matches_org
from gh.db import sessionmaker
from tests.conftest import Api
from tests.phase2 import listen, msg, org_id, put
from tests.test_rbac_api import login_as


async def _unit(db, org, *, group_id=None, person_id, event_type: str, side: str, conclusion: str,  # type: ignore[no-untyped-def]
                entities: dict, confidence: float = 0.9) -> uuid.UUID:
    return (await db.execute(text("""
        INSERT INTO clean.meaning_units (org_id, observed_at, group_id, person_id, event_type, side, conclusion,
                                         entities, confidence, run_id)
        VALUES (:o, now(), :g, :p, :et, :side, :c, CAST(:e AS jsonb), :conf, core.uuid_v7()) RETURNING id"""),
        {"o": org, "g": group_id, "p": person_id, "et": event_type, "side": side, "c": conclusion,
         "e": orjson.dumps(entities).decode(), "conf": confidence})).scalar_one()


async def _person_of(db, raw_id) -> uuid.UUID:  # type: ignore[no-untyped-def]
    return (await db.execute(text("""SELECT pi.person_id FROM raw.events e
                                     JOIN core.person_identities pi ON pi.id = e.sender_identity_id
                                     WHERE e.id = :r"""), {"r": raw_id})).scalar_one()


async def assign_scope(db, email: str, subject_type: str, subject_id) -> None:  # type: ignore[no-untyped-def]
    await db.execute(text("""INSERT INTO core.assignments (org_id, user_id, subject_type, subject_id)
                             SELECT org_id, id, :t, :s FROM core.users WHERE email = :e"""),
                     {"t": subject_type, "s": subject_id, "e": email})
    await db.commit()


async def capture(db, redis, org, *unit_ids) -> None:  # type: ignore[no-untyped-def]
    ctx = HookCtx(org_id=org, unit_ids=list(unit_ids), run_id=None, sm=sessionmaker(), redis=redis, bus=None,
                 router=None)
    await market_signal_capture(ctx)


@pytest.fixture
async def world(app, db, redis, owner_api):  # type: ignore[no-untyped-def]
    """Tổ chức đã khởi tạo, nhóm g1: chị Lan (cầu — thép cuộn, 3 container, ngân sách 1,2 tỷ, Hà Nội) và anh
    Bình (cung — thép cuộn, 4 container, giá 1,1 tỷ, Hà Nội) — đủ khớp để chấm điểm ghép; hook sau sàng lọc đã
    chạy nên đã có `biz.market_signals` + một cơ hội `raw_signal` cho chị Lan."""
    org = await org_id(db)
    gid = await listen(db, org, "g1")
    sm = sessionmaker()
    [raw_lan] = await put(sm, org, msg("Cần mua thép cuộn 3 container", group="g1", sender="lan", name="Chị Lan"))
    [raw_binh] = await put(sm, org, msg("Bên em còn tồn thép cuộn", group="g1", sender="binh", name="Anh Bình"))
    await db.commit()
    p_lan, p_binh = await _person_of(db, raw_lan), await _person_of(db, raw_binh)
    u_demand = await _unit(db, org, group_id=gid, person_id=p_lan, event_type="AskedPrice", side="demand",
                           conclusion="Cần mua thép cuộn 3 container", confidence=0.9,
                           entities={"product": "Thép cuộn", "qty": 3, "unit": "container",
                                     "budget_vnd": 1_200_000_000, "place": "Hà Nội"})
    u_supply = await _unit(db, org, group_id=gid, person_id=p_binh, event_type="OfferedSupply", side="supply",
                           conclusion="Còn tồn thép cuộn 4 container", confidence=0.85,
                           entities={"product": "Thép cuộn", "qty": 4, "unit": "container",
                                     "budget_vnd": 1_100_000_000, "place": "Hà Nội"})
    await db.commit()
    await capture(db, redis, org, u_demand, u_supply)
    await db.commit()
    return {"org": org, "group": gid, "p_lan": p_lan, "p_binh": p_binh, "u_demand": u_demand, "u_supply": u_supply}


# ═══ Bảng cơ hội: kéo thả + tổng pipeline ═════════════════════════════════════

async def test_demand_signal_opens_raw_signal_opportunity(world, owner_api: Api) -> None:  # type: ignore[no-untyped-def]
    body = (await owner_api.get("/opportunities")).json()
    assert body["total"] == 1
    opp = body["items"][0]
    assert opp["stage"] == "raw_signal" and opp["person"]["id"] == str(world["p_lan"])
    assert opp["value_vnd"] == 1_200_000_000 and opp["confidence"] == "high"
    assert opp["first_contact_at"] is None


async def test_capture_is_idempotent_on_retry(world, db, redis) -> None:  # type: ignore[no-untyped-def]
    n_signals = (await db.execute(text("SELECT count(*) FROM biz.market_signals WHERE org_id = :o"),
                                  {"o": world["org"]})).scalar_one()
    n_opps = (await db.execute(text("SELECT count(*) FROM biz.opportunities WHERE org_id = :o"),
                               {"o": world["org"]})).scalar_one()
    await capture(db, redis, world["org"], world["u_demand"], world["u_supply"])   # chạy lại (retry)
    await db.commit()
    assert (await db.execute(text("SELECT count(*) FROM biz.market_signals WHERE org_id = :o"),
                             {"o": world["org"]})).scalar_one() == n_signals
    assert (await db.execute(text("SELECT count(*) FROM biz.opportunities WHERE org_id = :o"),
                             {"o": world["org"]})).scalar_one() == n_opps


async def test_stage_drag_drop_writes_history(world, owner_api: Api, db) -> None:  # type: ignore[no-untyped-def]
    opp_id = (await owner_api.get("/opportunities")).json()["items"][0]["id"]
    r = await owner_api.send("PATCH", f"/opportunities/{opp_id}/stage", {"to_stage": "validated"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["stage"] == "validated" and body["first_contact_at"] is not None    # rời raw_signal → tiếp cận

    hist = (await db.execute(text("""SELECT from_stage, to_stage, actor FROM biz.opportunity_stage_history
                                     WHERE opportunity_id = :i ORDER BY at"""), {"i": opp_id})).all()
    assert [(h.from_stage, h.to_stage) for h in hist] == [(None, "raw_signal"), ("raw_signal", "validated")]
    assert hist[-1].actor.startswith("user:")

    r2 = await owner_api.send("PATCH", f"/opportunities/{opp_id}/stage", {"to_stage": "bogus"})
    assert r2.status_code == 422

    detail = (await owner_api.get(f"/opportunities/{opp_id}")).json()
    assert [h["to_stage"] for h in detail["stage_history"]] == ["validated", "raw_signal"]


async def test_pipeline_totals_exclude_closed_stages(world, owner_api: Api, db) -> None:  # type: ignore[no-untyped-def]
    opp_id = (await owner_api.get("/opportunities")).json()["items"][0]["id"]
    code = (await db.execute(text("SELECT core.next_code('OPP')"))).scalar_one()
    closed = (await db.execute(text("""INSERT INTO biz.opportunities (org_id, code, person_id, need, stage,
                                       value_vnd, confidence, first_signal_at)
        VALUES (:o, :c, :p, 'Đã ngủ đông', 'dormant', 500000000, 'low', now()) RETURNING id"""),
        {"o": world["org"], "c": code, "p": world["p_binh"]})).scalar_one()
    await db.commit()

    before = (await owner_api.get("/opportunities/pipeline")).json()
    assert before["open_pipeline_value_vnd"] == 1_200_000_000        # cơ hội dormant KHÔNG tính vào pipeline mở
    by_stage = {s["stage"]: s for s in before["stages"]}
    assert by_stage["dormant"]["value_vnd"] == 500_000_000 and by_stage["raw_signal"]["value_vnd"] == 1_200_000_000

    r = await owner_api.send("PATCH", f"/opportunities/{opp_id}/stage", {"to_stage": "won"})
    assert r.status_code == 200 and r.json()["closed_at"] is not None
    after = (await owner_api.get("/opportunities/pipeline")).json()
    assert after["open_pipeline_value_vnd"] == 0                     # won cũng không tính
    assert {s["stage"]: s["value_vnd"] for s in after["stages"]}["won"] == 1_200_000_000
    _ = closed


# ═══ Cung ↔ Cầu: chấm điểm ghép có lý do ══════════════════════════════════════

def test_score_match_full_breakdown() -> None:
    demand = {"item": "Thép cuộn", "category": None, "quantity": 3.0, "unit": "container",
              "value_vnd": 1_200_000_000.0, "location": "Hà Nội"}
    supply = {"item": "Thép cuộn cao cấp", "category": None, "quantity": 4.0, "unit": "container",
              "value_vnd": 1_100_000_000.0, "location": "Hà Nội"}
    score, reasons = msvc.score_match(demand, supply)
    # 50 (mặt hàng) + 20*3/4=15 (số lượng) + 20 (trong ngân sách) + 10 (khu vực) = 95
    assert score == 95.0
    assert any("Cùng mặt hàng" in r and "+50" in r for r in reasons)
    assert any("Số lượng khớp 75%" in r for r in reasons)
    assert any("Trong ngân sách" in r for r in reasons)
    assert any("Cùng khu vực" in r for r in reasons)


def test_score_match_over_budget_scales_down() -> None:
    demand = {"item": "Gỗ MDF", "category": None, "quantity": None, "value_vnd": 1_000_000_000.0, "location": None}
    supply = {"item": "Gỗ MDF E1", "category": None, "quantity": None, "value_vnd": 1_500_000_000.0,
              "location": None}
    score, reasons = msvc.score_match(demand, supply)
    # 50 (mặt hàng) + 20*(1-0.5)=10 (vượt ngân sách 50%) = 60
    assert score == 60.0
    assert any("Vượt ngân sách 50%" in r for r in reasons)


def test_score_match_no_item_or_category_overlap_is_zero() -> None:
    score, reasons = msvc.score_match({"item": "Thép cuộn", "category": "kim loại"},
                                      {"item": "Bàn ghế gỗ", "category": "nội thất"})
    assert score == 0.0
    assert "không cùng mặt hàng" in reasons[0].lower() or "không đủ căn cứ" in reasons[0].lower()


def test_score_match_category_fallback() -> None:
    score, reasons = msvc.score_match({"item": "thép cuộn", "category": "kim loại"},
                                      {"item": "tôn lạnh", "category": "kim loại"})
    assert score == 30.0
    assert any("Cùng ngành hàng" in r for r in reasons)


async def test_recompute_creates_match_with_verifiable_score(world, db) -> None:  # type: ignore[no-untyped-def]
    n = await recompute_matches_org(db, world["org"])
    await db.commit()
    assert n >= 1
    row = (await db.execute(text("SELECT score, reasons, status FROM biz.matches WHERE org_id = :o"),
                            {"o": world["org"]})).one()
    assert row.status == "suggested" and row.score == 95.0
    assert any("Cùng mặt hàng" in r for r in row.reasons)


async def test_introduce_creates_pending_draft_and_transforms_status(world, owner_api: Api, db) -> None:  # type: ignore[no-untyped-def]
    await recompute_matches_org(db, world["org"])
    await db.commit()
    listing = (await owner_api.get("/matches")).json()
    assert listing["total"] == 1
    match_id = listing["items"][0]["id"]
    assert listing["items"][0]["status"] == "suggested"

    r = await owner_api.send("POST", f"/matches/{match_id}/introduce", {})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True and body["draft"]["id"] is not None
    assert body["draft"]["status"] == "pending"       # người tự tạo → luôn dừng ở Bàn làm việc (luật cứng Q2)

    draft_row = (await db.execute(text("""SELECT status, kind, subject_type, subject_id FROM biz.action_drafts
                                          WHERE id = :i"""), {"i": body["draft"]["id"]})).one()
    assert draft_row.status == "pending" and draft_row.kind == "message"
    assert draft_row.subject_type == "person" and str(draft_row.subject_id) == str(world["p_lan"])

    after = (await owner_api.get("/matches?status=introduced")).json()
    assert after["total"] == 1 and after["items"][0]["id"] == match_id

    opp = (await db.execute(text("SELECT stage FROM biz.opportunities WHERE id = :i"),
                            {"i": body["opportunity_id"]})).scalar_one()
    assert opp == "matched"

    again = await owner_api.send("POST", f"/matches/{match_id}/introduce", {})
    assert again.status_code == 409 and again.json()["code"] == "MATCH_DECIDED"


async def test_reject_match(world, owner_api: Api, db) -> None:  # type: ignore[no-untyped-def]
    await recompute_matches_org(db, world["org"])
    await db.commit()
    match_id = (await owner_api.get("/matches")).json()["items"][0]["id"]
    r = await owner_api.send("POST", f"/matches/{match_id}/reject", {})
    assert r.status_code == 200
    row = (await db.execute(text("SELECT status FROM biz.matches WHERE id = :i"), {"i": match_id})).scalar_one()
    assert row == "rejected"
    again = await owner_api.send("POST", f"/matches/{match_id}/reject", {})
    assert again.status_code == 409


# ═══ Kho hội thoại: tìm ra NGƯỜI qua facet ═════════════════════════════════════

async def test_search_groups_by_person_with_facets(world, owner_api: Api, db) -> None:  # type: ignore[no-untyped-def]
    # Một đơn vị chỉ gắn nhóm (không người) — không được dựng thành một dòng "người".
    await db.execute(text("""INSERT INTO clean.meaning_units (org_id, observed_at, group_id, event_type,
                             conclusion, entities, confidence, run_id)
        VALUES (:o, now(), :g, 'AskedPrice', 'Ai đó hỏi giá thép trong nhóm', '{}', 0.5, core.uuid_v7())"""),
        {"o": world["org"], "g": world["group"]})
    await db.commit()

    body = (await owner_api.get("/search?q=thép")).json()
    persons = {i["person"]["id"]: i for i in body["items"]}
    assert str(world["p_lan"]) in persons and str(world["p_binh"]) in persons
    assert persons[str(world["p_lan"])]["last_event_type"] == "AskedPrice"
    assert all("person" in i for i in body["items"])                     # kết quả luôn là người

    events = {f["value"]: f["count"] for f in body["facets"]["event_type"]}
    assert events.get("AskedPrice", 0) >= 1 and events.get("OfferedSupply", 0) >= 1
    channels = {f["value"] for f in body["facets"]["channel"]}
    assert "zalo" in channels

    only_demand = (await owner_api.get("/search?event_type=OfferedSupply")).json()
    assert {i["person"]["id"] for i in only_demand["items"]} == {str(world["p_binh"])}

    no_hit = (await owner_api.get("/search?q=khong-lien-quan-xyz")).json()
    assert no_hit["total"] == 0


async def test_search_bulk_task_and_tag(world, owner_api: Api, db) -> None:  # type: ignore[no-untyped-def]
    r = await owner_api.send("POST", "/search/bulk",
                             {"person_ids": [str(world["p_lan"])], "action": "task", "text": "Gọi lại chị Lan",
                              "priority": "P1"})
    assert r.status_code == 200 and r.json()["count"] == 1
    task = (await db.execute(text("""SELECT title, priority, subject_id FROM biz.tasks
                                     WHERE org_id = :o AND source = 'draft'"""), {"o": world["org"]})).one()
    assert task.title == "Gọi lại chị Lan" and task.priority == "P1" and str(task.subject_id) == str(world["p_lan"])

    r2 = await owner_api.send("POST", "/search/bulk",
                              {"person_ids": [str(world["p_lan"])], "action": "tag", "text": "Khách VIP tiềm năng"})
    assert r2.status_code == 200
    note = (await db.execute(text("""SELECT body FROM memory.entries e JOIN memory.notebooks n ON n.id = e.notebook_id
                                     WHERE n.subject_type = 'person' AND n.subject_id = :p"""),
                             {"p": world["p_lan"]})).scalar_one()
    assert note == "Khách VIP tiềm năng"


# ═══ Deal & Vụ việc ═══════════════════════════════════════════════════════════

async def test_deal_status_change_syncs_opportunity(world, owner_api: Api, db) -> None:  # type: ignore[no-untyped-def]
    opp_id = (await owner_api.get("/opportunities")).json()["items"][0]["id"]
    r = await owner_api.send("POST", "/deals", {"person_id": str(world["p_lan"]), "amount_vnd": 1_150_000_000,
                                                "opportunity_id": opp_id})
    assert r.status_code == 201, r.text
    deal = r.json()
    assert deal["status"] == "open" and deal["won_at"] is None

    r2 = await owner_api.send("PATCH", f"/deals/{deal['id']}", {"status": "won"})
    assert r2.status_code == 200
    body = r2.json()
    assert body["status"] == "won" and body["won_at"] is not None

    opp = (await db.execute(text("SELECT stage, closed_at FROM biz.opportunities WHERE id = :i"),
                            {"i": opp_id})).one()
    assert opp.stage == "won" and opp.closed_at is not None            # đồng bộ một chiều deal → cơ hội


async def test_case_status_and_assignee(world, owner_api: Api, db) -> None:  # type: ignore[no-untyped-def]
    owner_id = (await db.execute(text("SELECT id FROM core.users WHERE email = 'owner@example.vn'"))).scalar_one()
    r = await owner_api.send("POST", "/cases", {"title": "Khách phàn nàn giao trễ", "priority": "P1",
                                                "subject": {"type": "person", "id": str(world["p_lan"])},
                                                "assignee_user_id": str(owner_id)})
    assert r.status_code == 201, r.text
    case = r.json()
    assert case["kind"] == "complaint" and case["status"] == "open"
    assert case["assignee"]["id"] == str(owner_id)
    assert case["subject"]["id"] == str(world["p_lan"])

    r2 = await owner_api.send("PATCH", f"/cases/{case['id']}", {"status": "resolved"})
    assert r2.status_code == 200
    body = r2.json()
    assert body["status"] == "resolved" and body["resolved_at"] is not None

    listing = (await owner_api.get("/cases?status=resolved")).json()
    assert case["id"] in {i["id"] for i in listing["items"]}


# ═══ Phạm vi theo vai trò ═════════════════════════════════════════════════════

async def test_scope_by_role(world, owner_api: Api, client, db) -> None:  # type: ignore[no-untyped-def]
    staff = await login_as(client, db, "agent_staff")
    assert (await staff.get("/opportunities")).json()["total"] == 0
    assert (await staff.get("/supply")).json()["total"] == 0
    assert (await staff.get("/search?q=thép")).json()["total"] == 0

    await assign_scope(db, "agent_staff@example.vn", "person", world["p_lan"])
    scoped_opps = (await staff.get("/opportunities")).json()
    assert scoped_opps["total"] == 1 and scoped_opps["items"][0]["person"]["id"] == str(world["p_lan"])

    scoped_supply = (await staff.get("/supply")).json()
    assert {i["person"]["id"] for i in scoped_supply["items"]} == {str(world["p_lan"])}   # chỉ tín hiệu của Lan

    scoped_search = (await staff.get("/search?q=thép")).json()
    assert {i["person"]["id"] for i in scoped_search["items"]} == {str(world["p_lan"])}

    auditor = await login_as(client, db, "auditor")
    assert (await auditor.get("/opportunities")).status_code == 200
    assert (await auditor.send("POST", "/opportunities", {"person_id": str(world["p_lan"]), "need": "x"}
                               )).status_code == 403
