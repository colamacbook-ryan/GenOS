"""v0.1.55 (G5) — Mặt tiền Owner: `GET /api/v1/owner/today|relations|tasks` (CHỈ ĐỌC, chỉ Owner).

- Owner 200 đúng hình dạng; operator/manager/auditor/agent_staff ⇒ 403 cả 3 đường; chưa đăng nhập ⇒ 401.
- `list` sai ⇒ 422; `limit` bị chặn ≤ 50 (không lỗi); tổ chức rỗng ⇒ mảng rỗng, không 500.
- `filter_value` lấy từ `triage.value_summary` (monkeypatch); gợi ý lấy từ `gh.defaults.registry.suggestions` + nguồn
  nền.
- Không có route ghi nào trong gh/owner/routes.py; GET không ghi Action Log; không lộ bí mật.

v0.1.59 (G1): thêm `nav` vào /today, 8 route đọc (xem tests/test_owner_v0159.py).

Chạy cả quyền superuser và `GH_TEST_APP_ROLE=1` (gh_app): `pytest -q tests/test_owner_v0155.py`.
"""

import re
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import orjson
import pytest
from sqlalchemy import text

from gh.owner import routes as owner_routes
from gh.owner import service as svc
from gh.refinery import triage
from tests.conftest import Api
from tests.test_rbac_api import login_as

ROUTES_FILE = Path(owner_routes.__file__)


@pytest.fixture
async def owner_app(app: Any) -> Any:
    """Router Owner phải được `gh/app.py` gắn sẵn (prefix /api/v1)."""
    assert any(getattr(r, "original_router", None) is owner_routes.router
               or str(getattr(r, "path", "")).startswith("/api/v1/owner/") for r in app.routes)
    return app


@pytest.fixture
async def owner(owner_app: Any, owner_api: Api) -> Api:
    return owner_api


async def _org(db: Any) -> uuid.UUID:
    return (await db.execute(text("SELECT id FROM core.organizations"))).scalar_one()  # type: ignore[no-any-return]


async def _owner_id(db: Any) -> uuid.UUID:
    return (await db.execute(text("""SELECT u.id FROM core.users u JOIN core.user_roles ur ON ur.user_id = u.id
                                     JOIN core.roles r ON r.id = ur.role_id WHERE r.code = 'owner'"""
                                  ))).scalar_one()  # type: ignore[no-any-return]


async def _person(db: Any, org: uuid.UUID, code: str, name: str, *, ptype: str = "customer",
                  org_name: str | None = None, heat: float | None = None, trend: str | None = None) -> uuid.UUID:
    pid = (await db.execute(text("""INSERT INTO core.persons (org_id, code, display_name, person_type,
                                                    organization_name)
                                    VALUES (:o, :c, :n, :t, :g) RETURNING id"""),
                            {"o": org, "c": code, "n": name, "t": ptype, "g": org_name})).scalar_one()
    if heat is not None:
        await db.execute(text("""INSERT INTO clean.current_scores (subject_type, subject_id, dimension, value, trend,
                                                                   snapshot_id, updated_at)
                                 VALUES ('person', :i, 'heat', :v, :t, :s, now())"""),
                         {"i": pid, "v": heat, "t": trend, "s": uuid.uuid4()})
    return pid  # type: ignore[no-any-return]


async def _edge(db: Any, org: uuid.UUID, kind: str, a: uuid.UUID, b: uuid.UUID, b_type: str, *, last_days: int,
                weight: float = 3.0, topic: str | None = None) -> None:
    await db.execute(text("""
        INSERT INTO clean.relationships (org_id, from_type, from_id, to_type, to_id, kind, window_days, weight,
                                         interactions, last_at, state, topic)
        VALUES (:o, 'person', :a, :bt, :b, :k, 90, :w, 4, now() - make_interval(days => :d), 'active', :tp)"""),
                     {"o": org, "a": a, "b": b, "bt": b_type, "k": kind, "w": weight, "d": last_days, "tp": topic})


async def _signal(db: Any, org: uuid.UUID, side: str, item: str, person: uuid.UUID | None) -> uuid.UUID:
    return (await db.execute(text("""INSERT INTO biz.market_signals (org_id, side, person_id, item)
                                     VALUES (:o, :s, :p, :i) RETURNING id"""),
                             {"o": org, "s": side, "p": person, "i": item})).scalar_one()  # type: ignore[no-any-return]


async def seed_world(db: Any) -> dict[str, Any]:
    """Một tổ chức đủ dữ liệu cho cả 3 màn: khách nóng/nguội, cầu nối, cặp Cung ↔ Cầu, nháp, việc, lời hứa, cơ hội."""
    org = await _org(db)
    uid = await _owner_id(db)
    a = await _person(db, org, "PER-1", "Chị Lan", org_name="Gỗ Việt", heat=92, trend="up")
    b = await _person(db, org, "PER-2", "Anh Bình", ptype="supplier", org_name="Xưởng Bình", heat=85)
    c = await _person(db, org, "PER-3", "Em Cúc", heat=40)
    d = await _person(db, org, "PER-4", "Ông Dũng", heat=88)
    # đã gộp / đã xoá: không được hiện
    gone = await _person(db, org, "PER-5", "Người đã xoá", heat=99)
    await db.execute(text("UPDATE core.persons SET deleted_at = now() WHERE id = :i"), {"i": gone})
    await _edge(db, org, "interacts", a, b, "person", last_days=60, weight=9, topic="MDF E1")
    await _edge(db, org, "interacts", a, c, "person", last_days=3, weight=20)        # còn nóng ⇒ không phải nguội
    await _edge(db, org, "interacts", c, d, "person", last_days=45, weight=2)
    group_a, group_b = uuid.uuid4(), uuid.uuid4()
    await _edge(db, org, "bridges", d, group_a, "group", last_days=1, weight=5)
    await _edge(db, org, "bridges", d, group_b, "group", last_days=1, weight=5)
    await _edge(db, org, "bridges", a, group_a, "group", last_days=1, weight=2)
    dem, sup = await _signal(db, org, "demand", "MDF E1 17mm", a), await _signal(db, org, "supply", "MDF E1 17mm", b)
    done_dem = await _signal(db, org, "demand", "Ván ép", c)
    await db.execute(text("""INSERT INTO biz.matches (org_id, demand_id, supply_id, score, reasons, status)
                             VALUES (:o, :d, :s, 87.5, '[]', 'suggested'), (:o, :x, :s, 95, '[]', 'rejected')"""),
                     {"o": org, "d": dem, "s": sup, "x": done_dem})
    await db.execute(text("""INSERT INTO biz.action_drafts (org_id, code, kind, title, body, autonomy_level, status)
                             VALUES (:o, 'ACT-1', 'message', 'Trả lời chị Lan về giá', '{}', 2, 'pending'),
                                    (:o, 'ACT-2', 'quotation', NULL, '{}', 2, 'pending'),
                                    (:o, 'ACT-3', 'message', 'Đã gửi rồi', '{}', 2, 'sent')"""), {"o": org})
    await db.execute(text("""INSERT INTO biz.tasks (org_id, code, title, status, due_at)
                             VALUES (:o, 'TSK-1', 'Gọi lại chị Lan', 'todo', now() - interval '2 days'),
                                    (:o, 'TSK-2', 'Gửi báo giá', 'doing', now() + interval '2 days'),
                                    (:o, 'TSK-3', 'Việc đã xong', 'done', now() - interval '9 days'),
                                    (:o, 'TSK-4', 'Việc chưa có hạn', 'todo', NULL)"""), {"o": org})
    await db.execute(text("""INSERT INTO biz.promises (org_id, promiser_person_id, text, due_at, kept_at)
                             VALUES (:o, :p, 'Giao mẫu', now() - interval '3 days', NULL),
                                    (:o, :p, 'Báo giá', now() - interval '3 days', now() - interval '4 days'),
                                    (:o, :p, 'Hẹn gặp', now() + interval '3 days', NULL)"""), {"o": org, "p": a})
    await db.execute(text("""INSERT INTO biz.opportunities (org_id, code, need, stage, value_vnd, confidence,
                                                 first_signal_at)
                             VALUES (:o, 'OPP-1', 'MDF', 'negotiating', 1000000, 'high', now()),
                                    (:o, 'OPP-2', 'Ván ép', 'matched', 2500000, 'medium', now()),
                                    (:o, 'OPP-3', 'Đã thắng', 'won', 9000000, 'high', now()),
                                    (:o, 'OPP-4', 'Đã mất', 'lost', 7000000, 'high', now())"""), {"o": org})
    await db.execute(text("""INSERT INTO biz.alerts (org_id, code, alert_type, priority, title, status)
                             VALUES (:o, 'ALR-1', 'customer_cooling', 'P1', 'Khách đang nguội', 'open'),
                                    (:o, 'ALR-2', 'slow_response', 'P2', 'Đã xử lý', 'resolved')"""), {"o": org})
    await db.commit()
    return {"org": org, "uid": uid, "a": a, "b": b, "c": c, "d": d}


async def seed_conversation(db: Any, org: uuid.UUID, uid: uuid.UUID) -> dict[str, uuid.UUID]:
    """Hội thoại có một đề xuất Gen chưa xác nhận (+ một đã huỷ) và một Bản tin Gen."""
    chat = (await db.execute(text("""INSERT INTO agent.gen_conversations (org_id, user_id, title)
                                     VALUES (:o, :u, 'Soạn nháp cho chị Lan') RETURNING id"""),
                             {"o": org, "u": uid})).scalar_one()
    brief = (await db.execute(text("""INSERT INTO agent.gen_conversations (org_id, user_id, title)
                                      VALUES (:o, :u, 'Bản tin Gen · sáng 10/10') RETURNING id"""),
                              {"o": org, "u": uid})).scalar_one()
    proposal = {"kind": "proposal", "proposal": {"id": str(uuid.uuid4()), "type": "kho_create", "status": "pending",
                                                  "requires_pin": True, "summary": "BÍ-MẬT-KHÔNG-ĐƯỢC-LỘ"}}
    cancelled = {"kind": "proposal", "proposal": {"id": str(uuid.uuid4()), "type": "assign", "status": "cancelled"}}
    msgs = [(chat, {"steps": [{"kind": "say", "text": "Em soạn xong rồi."}, proposal, cancelled]}),
            (brief, {"kind": "briefing", "steps": [{"kind": "tool", "name": "briefing.sources"},
                                                    {"kind": "say", "text": "Sáng nay có 3 nháp chờ Sếp duyệt."},
                                                    {"kind": "say", "text": "Việc đến hạn (2)"}]})]
    for cid, content in msgs:
        await db.execute(text("""INSERT INTO agent.gen_messages (org_id, conversation_id, turn_id, role, content)
                                 VALUES (:o, :c, :t, 'assistant', CAST(:x AS jsonb))"""),
                         {"o": org, "c": cid, "t": uuid.uuid4(), "x": orjson.dumps(content).decode()})
    await db.commit()
    return {"chat": chat, "brief": brief}


# ─── quyền ───────────────────────────────────────────────────────────────────────────────────────────────────────

PATHS = ["/owner/today", "/owner/relations?list=hot", "/owner/relations?list=matches", "/owner/tasks"]


@pytest.mark.parametrize("role", ["manager", "operator", "agent_staff", "auditor"])
async def test_non_owner_roles_get_403_on_every_route(owner: Api, client: Any, db: Any, role: str) -> None:
    api = await login_as(client, db, role)
    for path in PATHS:
        r = await api.get(path)
        assert r.status_code == 403, (role, path, r.text)
        assert r.json()["code"] == "FORBIDDEN"


async def test_anonymous_gets_401(owner: Api, client: Any) -> None:
    anon = client.__class__(transport=client._transport, base_url="http://test")      # không cookie phiên
    for path in PATHS:
        assert (await anon.get(f"/api/v1{path}")).status_code == 401
    await anon.aclose()


# ─── hình dạng + dữ liệu ─────────────────────────────────────────────────────────────────────────────────────────

REVIEW_KEYS = {"kind", "title", "to", "at"}
ROW_KEYS = {"id", "name", "subtitle", "metric_text", "to"}


async def test_empty_org_returns_empty_arrays_not_500(owner: Api) -> None:
    today = (await owner.get("/owner/today"))
    assert today.status_code == 200, today.text
    d = today.json()
    # v0.1.59 (G1): thêm `nav` (số đếm trên thanh điều hướng Mặt tiền); các khoá cũ giữ nguyên.
    assert set(d) == {"needs_review", "kpis", "briefing_latest", "filter_value", "suggestions", "progress", "nav"}
    assert d["nav"] == {"urgent_open": 0, "decide": 0}
    assert d["needs_review"] == [] and d["briefing_latest"] is None
    # tổ chức trống chưa có khoá API cho việc nền ⇒ gợi ý thật duy nhất của registry (G1) là thiếu khoá nền
    assert [s["key"] for s in d["suggestions"]] == ["background_key_missing"]
    assert d["kpis"] == {"hot": 0, "cooling": 0, "open_opps": 0, "open_value_vnd": 0, "overdue_promises": 0}
    assert d["filter_value"] == {"filtered": 0, "spam_blocked": 0, "calls_saved": 0, "jev_on": False}
    # tiến độ lấy từ "Việc Sếp cần làm" (không ghi cứng): tổng = số dòng bắt buộc của boss_checks
    from gh.boss_checks import service as boss
    assert d["progress"] == {"required_done": 0, "required_total": boss.REQUIRED_TOTAL}
    for lst in ("hot", "cooling", "bridges", "matches"):
        r = await owner.get(f"/owner/relations?list={lst}")
        assert r.status_code == 200, r.text
        assert r.json() == {"list": lst, "items": []}
    t = (await owner.get("/owner/tasks")).json()
    assert [g["key"] for g in t["groups"]] == ["inbox", "desk", "tasks"]
    assert all(g["count"] == 0 and g["items"] == [] for g in t["groups"])
    assert [g["to"] for g in t["groups"]] == ["/inbox", "/workbench", "/tasks"]


async def test_today_shape_and_numbers(owner: Api, db: Any) -> None:
    w = await seed_world(db)
    conv = await seed_conversation(db, w["org"], w["uid"])
    r = await owner.get("/owner/today")
    assert r.status_code == 200, r.text
    d = r.json()
    # 4 số (+ giá trị): 3 khách nóng (≥ 80, không tính người đã xoá), 2 quan hệ nguội (> 30 ngày), 2 cơ hội mở,
    # 1 lời hứa quá hạn
    assert d["kpis"] == {"hot": 3, "cooling": 2, "open_opps": 2, "open_value_vnd": 3_500_000, "overdue_promises": 1}
    kinds = [x["kind"] for x in d["needs_review"]]
    assert kinds == ["proposal", "draft", "draft", "overdue_task"]
    assert all(set(x) == REVIEW_KEYS for x in d["needs_review"])
    prop = d["needs_review"][0]
    assert prop["to"] == f"/owner/gen?gen={conv['chat']}" and "cần mã PIN" in prop["title"]
    assert "BÍ-MẬT" not in orjson.dumps(d).decode()           # chỉ nhãn tĩnh, không chép tóm tắt/nội dung đề xuất
    drafts = [x for x in d["needs_review"] if x["kind"] == "draft"]
    assert {x["title"] for x in drafts} == {"Trả lời chị Lan về giá", "Báo giá chờ duyệt"}
    assert all(x["to"].startswith("/workbench?id=") for x in drafts)
    assert d["needs_review"][-1] == {"kind": "overdue_task", "title": "Gọi lại chị Lan", "to": "/tasks?overdue=true",
                                     "at": d["needs_review"][-1]["at"]}
    b = d["briefing_latest"]
    assert set(b) == {"title", "at", "summary_text", "to"}
    assert b["title"] == "Bản tin Gen · sáng 10/10" and b["summary_text"] == "Sáng nay có 3 nháp chờ Sếp duyệt."
    assert b["to"] == f"/owner/gen?gen={conv['brief']}"


async def test_needs_review_capped_at_ten(owner: Api, db: Any) -> None:
    org = await _org(db)
    await db.execute(text("""INSERT INTO biz.action_drafts (org_id, code, kind, body, autonomy_level, status)
                             SELECT :o, 'ACT-' || g, 'message', '{}', 2, 'pending' FROM generate_series(1, 30) g"""),
                     {"o": org})
    await db.execute(text("""INSERT INTO biz.tasks (org_id, code, title, status, due_at)
                             SELECT :o, 'TSK-' || g, 'Việc ' || g, 'todo', now() - interval '1 day'
                             FROM generate_series(1, 30) g"""), {"o": org})
    await db.commit()
    items = (await owner.get("/owner/today")).json()["needs_review"]
    assert len(items) == 10 and {x["kind"] for x in items} == {"draft", "overdue_task"}


async def test_proposals_only_from_the_owners_own_recent_pending(owner: Api, db: Any) -> None:
    w = await seed_world(db)
    conv = await seed_conversation(db, w["org"], w["uid"])
    # quá 24 giờ ⇒ đề xuất đã hết hạn, không còn trong danh sách
    await db.execute(text("UPDATE agent.gen_messages SET created_at = now() - interval '30 hours' "
                          "WHERE conversation_id = :c"), {"c": conv["chat"]})
    await db.commit()
    assert "proposal" not in [x["kind"] for x in (await owner.get("/owner/today")).json()["needs_review"]]


async def test_relations_four_lists(owner: Api, db: Any) -> None:
    w = await seed_world(db)
    hot = (await owner.get("/owner/relations?list=hot")).json()
    assert hot["list"] == "hot" and [r["name"] for r in hot["items"]] == ["Chị Lan", "Ông Dũng", "Anh Bình"]
    # v0.1.59: dòng cũ giữ nguyên 5 khoá, thêm trường tuỳ chọn (person_id, heat, …)
    assert all(ROW_KEYS <= set(r) <= ROW_KEYS | {"person_id", "heat", "churn_risk", "last_contact_days",
                                                 "open_value_vnd"} for r in hot["items"])
    assert hot["items"][0]["person_id"] == str(w["a"]) and hot["items"][0]["heat"] == 92
    assert hot["items"][0]["metric_text"] == "Độ nóng 92 · đang tăng"
    assert hot["items"][0]["subtitle"] == "Khách hàng · Gỗ Việt"
    assert hot["items"][0]["to"] == f"/profile?id={w['a']}"
    cooling = (await owner.get("/owner/relations?list=cooling")).json()["items"]
    assert [r["name"] for r in cooling] == ["Chị Lan và Anh Bình", "Em Cúc và Ông Dũng"]
    assert cooling[0]["metric_text"] == "60 ngày chưa liên lạc" and cooling[0]["subtitle"] == "Hay trao đổi về MDF E1"
    assert cooling[0]["to"] == f"/profile?id={w['a']}"
    bridges = (await owner.get("/owner/relations?list=bridges")).json()["items"]
    assert [r["name"] for r in bridges] == ["Ông Dũng", "Chị Lan"]
    assert bridges[0]["metric_text"] == "Nối 5 cặp nhóm" and "2 nhóm" in bridges[0]["subtitle"]
    matches = (await owner.get("/owner/relations?list=matches")).json()["items"]
    assert len(matches) == 1                                   # cặp đã bị từ chối không hiện
    assert matches[0]["name"] == "MDF E1 17mm ↔ MDF E1 17mm" and matches[0]["metric_text"] == "Khớp 88%"
    assert matches[0]["subtitle"] == "Chị Lan cần · Anh Bình có" and matches[0]["to"] == f"/profile?id={w['a']}"


async def test_relations_limit_is_clamped_and_list_validated(owner: Api, db: Any) -> None:
    org = await _org(db)
    await db.execute(text("""INSERT INTO core.persons (org_id, code, display_name) SELECT :o, 'PX-' || g, 'Khách ' || g
                             FROM generate_series(1, 70) g"""), {"o": org})
    await db.execute(text("""INSERT INTO clean.current_scores (subject_type, subject_id, dimension, value, snapshot_id,
                                                               updated_at)
                             SELECT 'person', id, 'heat', 90, gen_random_uuid(), now() FROM core.persons
                             WHERE org_id = :o"""), {"o": org})
    await db.commit()
    assert len((await owner.get("/owner/relations?list=hot&limit=500")).json()["items"]) == 50
    assert len((await owner.get("/owner/relations?list=hot&limit=3")).json()["items"]) == 3
    assert len((await owner.get("/owner/relations?list=hot&limit=0")).json()["items"]) == 1
    assert len((await owner.get("/owner/relations?list=hot&limit=-7")).json()["items"]) == 1
    assert len((await owner.get("/owner/relations?list=hot")).json()["items"]) == svc.DEFAULT_LIMIT
    assert (await owner.get("/owner/relations?list=bogus")).status_code == 422
    assert (await owner.get("/owner/relations?list=hot&limit=abc")).status_code == 422
    assert svc.clamp_limit(None) == 20 and svc.clamp_limit(10**9) == 50


async def test_tasks_three_groups(owner: Api, db: Any) -> None:
    await seed_world(db)
    t = (await owner.get("/owner/tasks")).json()
    inbox, desk, tasks = t["groups"]
    assert (inbox["key"], inbox["title"], inbox["to"]) == ("inbox", "Hộp thư đã lọc", "/inbox")
    assert inbox["count"] == 1 and inbox["items"][0]["title"] == "Khách đang nguội"      # chỉ cảnh báo đang mở
    assert (desk["key"], desk["title"], desk["count"]) == ("desk", "Bàn làm việc", 2)
    assert {i["title"] for i in desk["items"]} == {"Trả lời chị Lan về giá", "Báo giá chờ duyệt"}
    assert all(set(i) == {"title", "at", "to"} for i in desk["items"])
    assert (tasks["key"], tasks["title"], tasks["count"]) == ("tasks", "Việc & Nhắc hẹn", 3)
    # hạn gần nhất trước, việc không hạn xuống cuối
    assert [i["title"] for i in tasks["items"]] == ["Gọi lại chị Lan", "Gửi báo giá", "Việc chưa có hạn"]


async def test_inbox_group_counts_only_kept_units(owner: Api, db: Any) -> None:
    org = await _org(db)
    now = datetime.now(UTC)
    run = uuid.uuid4()
    ids = {}
    for key, quality, spam in (("keep", 80, False), ("low", 10, False), ("spam", 90, True)):
        mu = (await db.execute(text("""
            INSERT INTO clean.meaning_units (org_id, observed_at, event_type, conclusion, confidence, run_id)
            VALUES (:o, :t, 'AskedPrice', :c, 0.9, :r) RETURNING id"""),
                                {"o": org, "t": now - timedelta(hours=1), "c": f"Tin {key}", "r": run})).scalar_one()
        ids[key] = mu
        await db.execute(text("""INSERT INTO refinery.item_marks (org_id, item_type, item_id, observed_at, text_hash,
                                                                  simhash, text_len, is_spam, quality, source,
                                                                  heuristic_quality, heuristic_spam)
                                 VALUES (:o, 'unit', :i, :t, decode('00', 'hex'), 0, 5, :s, :q, 'heuristic', :q,
                                         :s)"""),
                         {"o": org, "i": mu, "t": now - timedelta(hours=1), "s": spam, "q": quality})
    await db.commit()
    inbox = (await owner.get("/owner/tasks")).json()["groups"][0]
    assert inbox["count"] == 1 and inbox["items"][0]["title"] == "Hỏi giá / nhu cầu mua: Tin keep"
    # tổ chức tắt lọc đầu ⇒ lấy hết mọi tin trong 7 ngày
    await db.execute(text("""UPDATE core.organizations SET settings = jsonb_set(settings, '{triage}',
                             '{"enabled": false, "min_score": 30, "use_jev": false}')"""))
    await db.commit()
    assert (await owner.get("/owner/tasks")).json()["groups"][0]["count"] == 3


# ─── cầu mềm tới G1 / G4 ─────────────────────────────────────────────────────────────────────────────────────────

async def test_filter_value_comes_from_value_summary(owner: Api, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[Any] = []

    async def fake(db: Any, org_id: Any, days: int = 7) -> dict[str, Any]:
        seen.append(org_id)
        return {"filtered": 120, "spam_blocked": 31, "calls_saved": 77, "jev_on": True, "ghi_chú": "bỏ qua"}

    monkeypatch.setattr(triage, "value_summary", fake, raising=False)
    fv = (await owner.get("/owner/today")).json()["filter_value"]
    assert fv == {"filtered": 120, "spam_blocked": 31, "calls_saved": 77, "jev_on": True}
    assert len(seen) == 1


async def test_filter_value_failure_does_not_break_today(owner: Api, monkeypatch: pytest.MonkeyPatch) -> None:
    async def boom(db: Any, org_id: Any, days: int = 7) -> dict[str, Any]:
        await db.execute(text("SELECT * FROM bang_khong_ton_tai"))
        return {}

    monkeypatch.setattr(triage, "value_summary", boom, raising=False)
    r = await owner.get("/owner/today")
    assert r.status_code == 200 and r.json()["filter_value"]["filtered"] == 0
    assert r.json()["kpis"]["hot"] == 0                       # giao dịch còn dùng được sau lỗi của cầu mềm


def _fake_registry(monkeypatch: pytest.MonkeyPatch, rows: Any) -> None:
    async def suggestions(db: Any, org_id: Any) -> Any:
        return rows

    monkeypatch.setattr(svc.defaults_registry, "suggestions", suggestions)


async def test_suggestions_from_registry_and_background_state(owner: Api, db: Any,
                                                              monkeypatch: pytest.MonkeyPatch) -> None:
    std = {"key": "apply_standard", "title": "Áp chuẩn", "body": "Dùng cấu hình chuẩn.",
           "to": "/system?tab=brain#chuan"}
    _fake_registry(monkeypatch, [std, {"key": "x", "title": 5}, "rác"])      # dòng sai kiểu bị bỏ
    assert (await owner.get("/owner/today")).json()["suggestions"] == [std]
    # sự cố "việc nền chưa có nguồn" đang mở ⇒ gộp thêm gợi ý thiếu khoá nền (tới Bộ não AI)
    org = await _org(db)
    await db.execute(text("""INSERT INTO ops.health_alerts (org_id, key, kind, severity, title)
                             VALUES (:o, 'ai.background_no_source', 'ai.background_no_source', 'warn', 'x')"""),
                     {"o": org})
    await db.commit()
    got = (await owner.get("/owner/today")).json()["suggestions"]
    assert [s["key"] for s in got] == ["apply_standard", "background_key_missing"]
    assert got[1]["to"] == "/system?tab=brain" and "model" not in got[1]["title"].lower()
    # registry đã tự nêu khoá nền ⇒ không thêm trùng
    own = {"key": "background_key_missing", "title": "T", "body": "B", "to": "/system?tab=brain"}
    _fake_registry(monkeypatch, [own])
    assert [s["key"] for s in (await owner.get("/owner/today")).json()["suggestions"]] == ["background_key_missing"]


async def test_suggestions_registry_failure_is_ignored(owner: Api, monkeypatch: pytest.MonkeyPatch) -> None:
    async def boom(db: Any, org_id: Any) -> Any:
        raise RuntimeError("hỏng")

    monkeypatch.setattr(svc.defaults_registry, "suggestions", boom)
    r = await owner.get("/owner/today")
    assert r.status_code == 200 and r.json()["suggestions"] == []


# ─── chỉ đọc ─────────────────────────────────────────────────────────────────────────────────────────────────────

def test_routes_file_has_no_write_methods() -> None:
    src = ROUTES_FILE.read_text(encoding="utf-8")
    assert not re.search(r"@router\.(post|put|patch|delete)\b", src)
    assert not re.search(r"\bmethods\s*=", src)
    assert all(r.methods == {"GET"} for r in owner_routes.router.routes)
    assert {r.path for r in owner_routes.router.routes} == {
        "/owner/today", "/owner/relations", "/owner/tasks", "/owner/brief", "/owner/feed",
        "/owner/feed/{item_id}/context", "/owner/insights", "/owner/proposals"}


async def test_get_is_readonly_no_action_log_and_no_model_calls(owner: Api, db: Any) -> None:
    await seed_world(db)
    before = (await db.execute(text("SELECT count(*) FROM ops.action_log"))).scalar_one()
    calls_before = (await db.execute(text("SELECT count(*) FROM agent.model_calls"))).scalar_one()
    for path in PATHS:
        assert (await owner.get(path)).status_code == 200
    await db.rollback()
    assert (await db.execute(text("SELECT count(*) FROM ops.action_log"))).scalar_one() == before
    assert (await db.execute(text("SELECT count(*) FROM agent.model_calls"))).scalar_one() == calls_before


async def test_write_methods_are_not_allowed(owner: Api) -> None:
    for method in ("POST", "PUT", "PATCH", "DELETE"):
        for path in ("/owner/today", "/owner/relations", "/owner/tasks"):
            assert (await owner.send(method, path, {})).status_code == 405


async def test_response_has_no_secrets_or_raw_objects(owner: Api, db: Any) -> None:
    w = await seed_world(db)
    await seed_conversation(db, w["org"], w["uid"])
    body = orjson.dumps([(await owner.get(p)).json() for p in PATHS]).decode().lower()
    for bad in ("token", "password", "api_key", "secret", "[object"):
        assert bad not in body


def test_text_helpers() -> None:
    assert svc._person_subtitle("customer", "Gỗ Việt") == "Khách hàng · Gỗ Việt"
    assert svc._person_subtitle("unknown", None) == "Chưa phân loại"
    assert svc._days_text(0) == "Chưa tới 1 ngày" and svc._days_text(45) == "45 ngày chưa liên lạc"
    assert svc.profile_link("abc") == "/profile?id=abc"
