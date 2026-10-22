"""v0.1.59 (G1) — Mặt tiền Owner v2: `GET /owner/brief|today(nav)|proposals|relations?list=care` (+ nền dùng chung cho
test_owner_feed_v0159 / test_insights_v0159).

- 7 GET mới / mở rộng (brief, today, feed, feed/{id}/context, relations?list=care, insights, proposals): vai khác
  Owner ⇒ 403.
- brief: `latest=null` khi chưa có bản tin; `live` không gọi model (router giả đếm 0 lượt, bảng model_calls không đổi);
  `ai_ready=false` khi chưa nguồn AI; mục có `group` và `to` đổi về đường Mặt tiền; `id` chọn bản trong `history`
  (≤ 14).
- today.nav.urgent_open == feed.counts.urgent_open; nav.decide = nháp chờ duyệt + đề xuất Gen còn hợp lệ.
- proposals: bỏ đề xuất đã hết TTL ở Redis dù tin nhắn vẫn ghi pending; bỏ đề xuất của người khác / đã huỷ.
- care: đúng luật + thứ tự, gồm người `unknown`, loại staff/candidate/learner, loại người đã xoá/gộp.
- Không có chuỗi 'analytics.' trong apps/api/gh/owner/*.py.

Chạy cả quyền superuser và `GH_TEST_APP_ROLE=1` (gh_app): `pytest -q tests/test_owner_v0159.py`.
"""

import hashlib
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import orjson
import pytest
from redis.exceptions import ConnectionError as RedisConnectionError
from sqlalchemy import text

from gh import crypto
from gh.gen import briefing
from gh.gen import proposals as gen_proposals
from gh.owner import brief as brief_mod
from gh.owner import routes as owner_routes
from gh.owner import service as svc
from gh.providers.router import KEY_AAD, ModelRouter
from tests.conftest import Api
from tests.test_owner_v0155 import _org, _owner_id, _person
from tests.test_rbac_api import login_as

OWNER_DIR = Path(owner_routes.__file__).parent

GET_PATHS = [
    "/owner/brief", "/owner/today", "/owner/feed", f"/owner/feed/{uuid.uuid4()}/context",
    "/owner/relations?list=care", "/owner/insights", "/owner/proposals",
]


@pytest.fixture
async def owner(owner_api: Api) -> Api:
    return owner_api


# ─── nền dùng chung (import bởi test_owner_feed_v0159 / test_insights_v0159) ───────────────────────────────────────

async def channel(db: Any, org: uuid.UUID, type_: str = "zalo", name: str | None = None) -> uuid.UUID:
    got = (await db.execute(text("SELECT id FROM core.channels WHERE org_id = :o AND type = :t AND name = :n"),
                            {"o": org, "t": type_, "n": name or type_.title()})).scalar_one_or_none()
    if got is not None:
        return got  # type: ignore[no-any-return]
    return (await db.execute(text("""INSERT INTO core.channels (org_id, type, name) VALUES (:o, :t, :n)
                                     RETURNING id"""), {"o": org, "t": type_, "n": name or type_.title()})
            ).scalar_one()  # type: ignore[no-any-return]


async def identity(db: Any, person: uuid.UUID, ch: uuid.UUID, ext: str | None = None) -> uuid.UUID:
    return (await db.execute(text("""INSERT INTO core.person_identities (person_id, channel_id, external_id)
                                     VALUES (:p, :c, :x) RETURNING id"""),
                             {"p": person, "c": ch, "x": ext or f"x-{uuid.uuid4().hex[:10]}"})).scalar_one()  # type: ignore


async def group(db: Any, org: uuid.UUID, ch: uuid.UUID, name: str = "Nhóm khách hàng") -> uuid.UUID:
    return (await db.execute(text("""INSERT INTO core.groups (org_id, code, channel_id, external_id, name)
                                     VALUES (:o, :c, :ch, :x, :n) RETURNING id"""),
                             {"o": org, "c": f"GRP-{uuid.uuid4().hex[:6]}", "ch": ch, "x": uuid.uuid4().hex,
                              "n": name})).scalar_one()  # type: ignore[no-any-return]


async def event(db: Any, org: uuid.UUID, ch: uuid.UUID, at: datetime, *, direction: str = "inbound",
                sender: uuid.UUID | None = None, grp: uuid.UUID | None = None, body: str | None = "Xin chào",
                kind: str = "text", received_at: datetime | None = None, payload: str = "{}") -> SimpleNamespace:
    """Một dòng `raw.events`. `received_at` mặc định = `at` (lịch sử); sender = id `core.person_identities`."""
    ext = f"m-{uuid.uuid4().hex}"
    r = (await db.execute(text("""
        INSERT INTO raw.events (org_id, received_at, occurred_at, channel_id, group_id, sender_identity_id,
                                external_msg_id, kind, body_text, payload, content_hash, direction)
        VALUES (:o, :r, :t, :c, :g, :s, :x, :k, :b, CAST(:pl AS jsonb), :h, :d) RETURNING id, received_at, seq"""),
                           {"o": org, "r": received_at or at, "t": at, "c": ch, "g": grp, "s": sender, "x": ext,
                            "k": kind, "b": body, "h": hashlib.sha256(ext.encode()).digest(), "d": direction,
                            "pl": payload})).one()
    return SimpleNamespace(id=r.id, received_at=r.received_at, seq=r.seq, at=at)


async def unit(db: Any, org: uuid.UUID, observed_at: datetime, event_type: str, conclusion: str = "Khách hỏi giá MDF",
               *, confidence: float = 0.8, person: uuid.UUID | None = None, grp: uuid.UUID | None = None,
               raw: tuple[SimpleNamespace, ...] = ()) -> uuid.UUID:
    """Một đơn vị ý nghĩa (+ chứng cứ trỏ về các bản ghi thô `raw`)."""
    uid = (await db.execute(text("""
        INSERT INTO clean.meaning_units (org_id, observed_at, group_id, person_id, event_type, conclusion, confidence,
                                         run_id)
        VALUES (:o, :t, :g, :p, :e, :c, :f, :r) RETURNING id"""),
                             {"o": org, "t": observed_at, "g": grp, "p": person, "e": event_type, "c": conclusion,
                              "f": confidence, "r": uuid.uuid4()})).scalar_one()
    for ev in raw:
        await db.execute(text("""INSERT INTO clean.evidence (meaning_unit_id, meaning_observed_at, raw_event_id,
                                                              raw_received_at)
                                 VALUES (:u, :t, :e, :r)"""),
                         {"u": uid, "t": observed_at, "e": ev.id, "r": ev.received_at})
    return uid  # type: ignore[no-any-return]


async def mark(db: Any, org: uuid.UUID, unit_id: uuid.UUID, observed_at: datetime, *, spam: bool = False,
               quality: int = 80, dup: uuid.UUID | None = None) -> None:
    await db.execute(text("""
        INSERT INTO refinery.item_marks (org_id, item_type, item_id, observed_at, text_hash, simhash, text_len,
                                         duplicate_of, duplicate_kind, is_spam, quality, source, heuristic_quality,
                                         heuristic_spam)
        VALUES (:o, 'unit', :i, :t, decode('00', 'hex'), 0, 5, :d,
                CASE WHEN CAST(:d AS uuid) IS NULL THEN NULL ELSE 'exact' END, :s, :q, 'heuristic', :q, :s)"""),
                     {"o": org, "i": unit_id, "t": observed_at, "d": dup, "s": spam, "q": quality})


async def alert(db: Any, org: uuid.UUID, title: str, *, priority: str = "P2", status: str = "open",
                created_at: datetime | None = None) -> uuid.UUID:
    return (await db.execute(text("""
        INSERT INTO biz.alerts (org_id, code, alert_type, priority, title, status, created_at)
        VALUES (:o, :c, 'customer_cooling', :p, :t, :s, COALESCE(:at, now())) RETURNING id"""),
                             {"o": org, "c": f"ALR-{uuid.uuid4().hex[:6]}", "p": priority, "t": title, "s": status,
                              "at": created_at})).scalar_one()  # type: ignore[no-any-return]


async def draft(db: Any, org: uuid.UUID, title: str | None = "Trả lời chị Lan", *, status: str = "pending",
                created_at: datetime | None = None, text_: str = "Dạ em gửi báo giá ạ") -> uuid.UUID:
    return (await db.execute(text("""
        INSERT INTO biz.action_drafts (org_id, code, kind, title, body, autonomy_level, status, created_at)
        VALUES (:o, :c, 'message', :t, CAST(:b AS jsonb), 2, :s, COALESCE(:at, now())) RETURNING id"""),
                             {"o": org, "c": f"ACT-{uuid.uuid4().hex[:6]}", "t": title, "s": status, "at": created_at,
                              "b": orjson.dumps({"text": text_}).decode()})).scalar_one()  # type: ignore[no-any-return]


async def other_user(db: Any, org: uuid.UUID) -> uuid.UUID:
    return (await db.execute(text("""INSERT INTO core.users (org_id, email, display_name, password_hash)
                                     VALUES (:o, 'khac@example.test', 'Người khác', 'x') RETURNING id"""),
                             {"o": org})).scalar_one()  # type: ignore[no-any-return]


def ago(**kw: float) -> datetime:
    return datetime.now(UTC) - timedelta(**kw)


async def conversation(db: Any, org: uuid.UUID, uid: uuid.UUID, content: dict[str, Any], *, title: str = "Gen",
                       created_at: datetime | None = None) -> uuid.UUID:
    """Một hội thoại Gen + MỘT tin trả lời có `content` (bản tin hoặc đề xuất)."""
    cid = (await db.execute(text("""INSERT INTO agent.gen_conversations (org_id, user_id, title, last_at)
                                    VALUES (:o, :u, :t, COALESCE(:at, now())) RETURNING id"""),
                            {"o": org, "u": uid, "t": title, "at": created_at})).scalar_one()
    await db.execute(text("""INSERT INTO agent.gen_messages (org_id, conversation_id, turn_id, role, content,
                                                             created_at)
                             VALUES (:o, :c, :t, 'assistant', CAST(:x AS jsonb), COALESCE(:at, now()))"""),
                     {"o": org, "c": cid, "t": uuid.uuid4(), "x": orjson.dumps(content).decode(), "at": created_at})
    return cid  # type: ignore[no-any-return]


async def proposal(db: Any, redis: Any, org: uuid.UUID, uid: uuid.UUID, *, ptype: str = "reminder",
                   in_redis: bool = True, status: str = "pending", owner: uuid.UUID | None = None,
                   requires_pin: bool = False, summary: str = "Nhắc gọi lại chị Lan lúc 9h",
                   created_at: datetime | None = None, ttl: int = gen_proposals.PROPOSAL_TTL_S) -> dict[str, Any]:
    """Một hội thoại có bước `proposal` (ghi `pending` trong tin nhắn) + (tuỳ chọn) bản trong Redis."""
    pid = str(uuid.uuid4())
    step = {"kind": "proposal",
            "proposal": {"id": pid, "type": ptype, "status": "pending", "requires_pin": requires_pin,
                         "summary": "KHÔNG ĐƯỢC LẤY TỪ TIN NHẮN"}}
    cid = await conversation(db, org, uid, {"steps": [{"kind": "say", "text": "Em đề xuất."}, step]},
                             created_at=created_at)
    p = {"id": pid, "type": ptype, "status": status, "requires_pin": requires_pin, "summary": summary,
         "user_id": str(owner or uid), "org_id": str(org), "conversation_id": str(cid), "fields": {}, "labels": {},
         "target": "tasks.new", "turn_id": str(uuid.uuid4())}
    if in_redis:
        await gen_proposals.save(redis, p, ttl=ttl)
    return {**p, "cid": cid}


# ─── quyền ───────────────────────────────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("role", ["manager", "operator", "agent_staff", "auditor"])
async def test_every_new_get_is_owner_only(owner: Api, client: Any, db: Any, role: str) -> None:
    api = await login_as(client, db, role)
    for path in GET_PATHS:
        r = await api.get(path)
        assert r.status_code == 403, (role, path, r.text)
        assert r.json()["code"] == "FORBIDDEN"


async def test_anonymous_gets_401_and_owner_gets_200(owner: Api, client: Any) -> None:
    for path in GET_PATHS:
        want = 404 if "/context" in path else 200            # id lạ ⇒ 404 thân thiện
        assert (await owner.get(path)).status_code == want, path
    anon = client.__class__(transport=client._transport, base_url="http://test")
    for path in GET_PATHS:
        assert (await anon.get(f"/api/v1{path}")).status_code == 401, path
    await anon.aclose()


# ─── brief ───────────────────────────────────────────────────────────────────────────────────────────────────────

async def test_brief_empty_org(owner: Api) -> None:
    d = (await owner.get("/owner/brief")).json()
    assert set(d) == {"latest", "live", "alerts", "history", "next_at", "ai_ready", "channels_connected"}
    assert d["latest"] is None and d["alerts"] == [] and d["history"] == []
    assert d["ai_ready"] is False and d["channels_connected"] == 0
    nxt = datetime.fromisoformat(d["next_at"].replace("Z", "+00:00"))
    assert d["next_at"].endswith("Z") and nxt > datetime.now(UTC)
    keys = [s["key"] for s in d["live"]["sections"]]
    assert keys == ["tasks_due", "hot_customers", "drafts_pending", "incidents"]     # Facebook/Kho chỉ khi có
    assert {s["key"]: s["group"] for s in d["live"]["sections"]} == {
        "tasks_due": "decide", "hot_customers": "key", "drafts_pending": "decide", "incidents": "alert"}
    assert {s["key"]: s["to"] for s in d["live"]["sections"]} == {
        "tasks_due": "/owner/viec", "hot_customers": "/owner/quan-he", "drafts_pending": "/owner/viec",
        "incidents": "/system?tab=storage&focus=health"}
    assert all(s["count"] == 0 and s["state"] == "empty" for s in d["live"]["sections"])


async def test_brief_live_never_calls_the_model(owner: Api, db: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    async def boom(*a: Any, **k: Any) -> Any:
        calls.append("model")
        raise AssertionError("brief.live không được gọi model")

    monkeypatch.setattr(ModelRouter, "generate", boom)
    monkeypatch.setattr(briefing, "_summarize", boom)
    monkeypatch.setattr(briefing, "_hub_sections", boom)
    org = await _org(db)
    await draft(db, org)
    await db.commit()
    before = (await db.execute(text("SELECT count(*) FROM agent.model_calls"))).scalar_one()
    r = await owner.get("/owner/brief")
    assert r.status_code == 200, r.text
    live = {s["key"]: s for s in r.json()["live"]["sections"]}
    assert live["drafts_pending"]["count"] == 1 and calls == []
    await db.rollback()
    assert (await db.execute(text("SELECT count(*) FROM agent.model_calls"))).scalar_one() == before


def _brief_content(**over: Any) -> dict[str, Any]:
    secs = [
        {"key": "tasks_due", "title": "Việc đến hạn", "count": 2, "lines": ["Gọi lại chị Lan — hạn 09:00"],
         "link": "/tasks"},
        {"key": "drafts_pending", "title": "Nháp chờ duyệt", "count": 3, "lines": ["ACT-1 · tin nhắn"],
         "link": "/workbench?id=1"},
        {"key": "incidents", "title": "Sự cố cần Sếp", "count": 1, "lines": ["Đầy đĩa"],
         "link": "/system?tab=storage&focus=health"},
        {"key": "hot_customers", "title": "Khách nóng", "count": 4, "lines": [f"Khách {i}" for i in range(9)],
         "link": "/directory"},
        {"key": "calendar_today", "title": "Lịch hôm nay", "count": 10, "lines": ["09:00 · Họp"], "more": True,
         "state": "ok", "external": True, "link": "/connections#genhub"},
        {"key": "mail_reply", "title": "Mail cần trả lời", "count": 0, "lines": ["Gen-hub tạm không trả lời"],
         "state": "breaker", "external": True, "link": "/connections#genhub"},
        {"key": "gtasks_open", "title": "Việc Google đang mở", "count": 0, "lines": [], "state": "empty",
         "external": True, "link": "/inbox?tab=alert"},
        {"key": "facebook", "title": "Facebook mới", "count": 5, "lines": ["Trang A: 5"], "link": "/social"},
        {"key": "kho", "title": "Kho có gì mới", "count": 0, "lines": ["Chưa đọc Kho lần nào"],
         "link": "//evil.example"},
        {"key": "la", "title": "Mục lạ", "count": 1, "lines": ["x"], "link": "https://evil.example/x"},
    ]
    base = {"kind": "briefing", "slot_label": "sáng 10/10", "summary_source": "model", "needs_api_key": False,
            "sections": secs, "steps": [{"kind": "tool", "name": "briefing.sources"},
                                        {"kind": "say", "text": "Sáng nay Sếp có 3 nháp chờ duyệt."}]}
    return {**base, **over}


async def test_brief_latest_sections_groups_and_front_links(owner: Api, db: Any) -> None:
    org, uid = await _org(db), await _owner_id(db)
    cid = await conversation(db, org, uid, _brief_content(), title="Bản tin Gen · sáng 10/10")
    await db.commit()
    d = (await owner.get("/owner/brief")).json()
    lt = d["latest"]
    assert lt["id"] == str(cid) and lt["slot"] == "sáng" and lt["title"] == "Bản tin Gen · sáng 10/10"
    assert lt["summary"] == "Sáng nay Sếp có 3 nháp chờ duyệt." and lt["needs_api_key"] is False
    secs = {s["key"]: s for s in lt["sections"]}
    assert {k: s["group"] for k, s in secs.items()} == {
        "tasks_due": "decide", "drafts_pending": "decide", "incidents": "alert", "hot_customers": "key",
        "calendar_today": "key", "mail_reply": "decide", "gtasks_open": "decide", "facebook": "key", "kho": "key",
        "la": "key"}
    assert secs["tasks_due"]["to"] == "/owner/viec" and secs["drafts_pending"]["to"] == "/owner/viec"
    assert secs["hot_customers"]["to"] == "/owner/quan-he" and len(secs["hot_customers"]["lines"]) == 5   # ≤ 5 dòng
    assert secs["gtasks_open"]["to"] == "/owner/thong-tin"            # /inbox → Thông tin
    assert secs["calendar_today"]["to"] == "/connections#genhub"      # đường lạ bắt đầu bằng '/' được giữ
    assert secs["facebook"]["to"] == "/social"
    # '//…' và 'https://…' không bao giờ lọt ra
    assert secs["kho"]["to"] == "/owner" and secs["la"]["to"] == "/owner"
    assert secs["mail_reply"]["state"] == "error" and secs["gtasks_open"]["state"] == "empty"
    assert secs["calendar_today"]["more"] is True and "state" not in secs["calendar_today"]
    assert d["history"] == [{"id": str(cid), "slot": "sáng", "at": lt["at"], "title": "Bản tin Gen · sáng 10/10"}]


async def test_brief_summary_null_without_model_summary(owner: Api, db: Any) -> None:
    org, uid = await _org(db), await _owner_id(db)
    await conversation(db, org, uid, _brief_content(
        summary_source="none", needs_api_key=True, slot_label="chiều 10/10",
        steps=[{"kind": "tool", "name": "briefing.sources"}, {"kind": "say", "text": "Việc đến hạn (2): …"}]))
    await db.commit()
    lt = (await owner.get("/owner/brief")).json()["latest"]
    assert lt["summary"] is None and lt["needs_api_key"] is True and lt["slot"] == "chiều"


async def test_brief_id_picks_a_history_entry_and_history_is_capped(owner: Api, db: Any) -> None:
    org, uid = await _org(db), await _owner_id(db)
    ids = []
    for i in range(16):
        ids.append(await conversation(db, org, uid, _brief_content(
            steps=[{"kind": "say", "text": f"Tóm tắt số {i}"}]), title=f"Bản tin {i}",
            created_at=ago(hours=100 - i)))
    other = await conversation(db, org, uid, {"steps": [{"kind": "say", "text": "Không phải bản tin"}]},
                               created_at=ago(minutes=1))
    await db.commit()
    d = (await owner.get("/owner/brief")).json()
    assert len(d["history"]) == 14 and d["history"][0]["id"] == str(ids[-1])         # mới nhất trước
    assert d["latest"]["id"] == str(ids[-1]) and d["latest"]["summary"] == "Tóm tắt số 15"
    older = (await owner.get(f"/owner/brief?id={ids[3]}")).json()
    assert older["latest"]["id"] == str(ids[3]) and older["latest"]["summary"] == "Tóm tắt số 3"
    for bad in (str(other), str(uuid.uuid4()), "không-phải-uuid"):
        r = await owner.get(f"/owner/brief?id={bad}")
        assert r.status_code == 404 and r.json()["code"] == "NOT_FOUND", bad


async def test_brief_is_found_even_behind_more_than_sixty_newer_conversations(owner: Api, db: Any) -> None:
    """F-60-R11 (sau /code-review): Owner chat nhiều (61 hội thoại mới hơn) vẫn thấy bản tin, ở Tình báo lẫn Hôm nay."""
    org, uid = await _org(db), await _owner_id(db)
    cid = await conversation(db, org, uid, _brief_content(), title="Bản tin Gen · sáng 10/10",
                             created_at=ago(days=3))
    for i in range(61):
        await conversation(db, org, uid, {"steps": [{"kind": "say", "text": f"Chat {i}"}]}, title=f"Chat {i}",
                           created_at=ago(minutes=60 - i))
    await db.commit()
    d = (await owner.get("/owner/brief")).json()
    assert d["latest"]["id"] == str(cid) and [h["id"] for h in d["history"]] == [str(cid)]
    assert (await owner.get(f"/owner/brief?id={cid}")).json()["latest"]["id"] == str(cid)
    assert (await owner.get("/owner/today")).json()["briefing_latest"]["to"] == f"/owner/gen?gen={cid}"


async def test_brief_with_id_skips_the_live_collection(owner: Api, db: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """F-60-R12: xem một bản CŨ (`?id=`) không chạy `briefing.collect` — `live` null; không `id` thì vẫn có `live`."""
    org, uid = await _org(db), await _owner_id(db)
    cid = await conversation(db, org, uid, _brief_content(), title="Bản tin cũ")
    await db.commit()
    real = briefing.collect
    calls: list[int] = []

    async def counting(*a: Any, **k: Any) -> Any:
        calls.append(1)
        return await real(*a, **k)

    monkeypatch.setattr(briefing, "collect", counting)
    d = (await owner.get(f"/owner/brief?id={cid}")).json()
    assert calls == [] and d["live"] is None and d["latest"]["id"] == str(cid)
    assert set(d) == {"latest", "live", "alerts", "history", "next_at", "ai_ready", "channels_connected"}
    live = (await owner.get("/owner/brief")).json()["live"]
    assert calls == [1] and live["sections"] and live["at"].endswith("Z")


async def test_brief_only_shows_own_conversations(owner: Api, client: Any, db: Any) -> None:
    org = await _org(db)
    other = await other_user(db, org)
    theirs = await conversation(db, org, other, _brief_content())
    await db.commit()
    d = (await owner.get("/owner/brief")).json()
    assert d["latest"] is None and d["history"] == []
    assert (await owner.get(f"/owner/brief?id={theirs}")).status_code == 404


async def test_brief_alerts_cap_order_and_safe_links(owner: Api, db: Any) -> None:
    org = await _org(db)
    rows = [("a1", "warn", 5, "/system?tab=brain"), ("a2", "bad", 4, None), ("a3", "warn", 3, "https://evil.example"),
            ("a4", "bad", 2, "/connections"), ("a5", "warn", 1, None), ("a6", "bad", 0, None)]
    for key, sev, hrs, link in rows:
        await db.execute(text("""INSERT INTO ops.health_alerts (org_id, key, kind, severity, title, link, raised_at)
                                 VALUES (:o, :k, 'x', :s, :t, :l, now() - make_interval(hours => :h))"""),
                         {"o": org, "k": key, "s": sev, "t": f"Sự cố {key}", "l": link, "h": hrs})
    await db.execute(text("""INSERT INTO ops.health_alerts (org_id, key, kind, severity, title, cleared_at)
                             VALUES (:o, 'old', 'x', 'bad', 'Đã xử lý', now())"""), {"o": org})
    await db.commit()
    al = (await owner.get("/owner/brief")).json()["alerts"]
    assert len(al) == 5 and [a["key"] for a in al] == ["a6", "a4", "a2", "a5", "a3"]      # bad trước, mới trước
    assert all(set(a) == {"key", "title", "severity", "raised_at", "to"} for a in al)
    by = {a["key"]: a for a in al}
    assert by["a3"]["to"] == "/system?tab=storage&focus=health" and by["a4"]["to"] == "/connections"
    assert by["a2"]["severity"] == "bad" and by["a5"]["severity"] == "warn"
    # mục "Sự cố" của live đếm cùng nguồn
    live = {s["key"]: s for s in (await owner.get("/owner/brief")).json()["live"]["sections"]}
    assert live["incidents"]["count"] == 6


async def test_brief_ai_ready_and_channels_connected(owner: Api, db: Any) -> None:
    org = await _org(db)
    assert (await owner.get("/owner/brief")).json()["ai_ready"] is False
    pid = (await db.execute(text("""INSERT INTO agent.providers (org_id, kind, name, auth_state)
                                    VALUES (:o, 'gemini', 'Gemini', 'ok') RETURNING id"""), {"o": org})).scalar_one()
    await db.execute(text("INSERT INTO agent.models (provider_id, model_name) VALUES (:p, 'gemini-2.5-flash')"),
                     {"p": pid})
    # v0.1.63 (B2): nguồn khoá API chỉ "dùng được" khi có khoá đang bật (không chỉ có dòng nhà cung cấp + model).
    await db.execute(text("""INSERT INTO agent.provider_keys (provider_id, label, secret_enc, last4, is_enabled)
                             VALUES (:p, 'K1', :s, '1234', true)"""),
                     {"p": pid, "s": crypto.encrypt(b"sk-test-owner-brief-1234", KEY_AAD)})
    zalo = await channel(db, org, "zalo")
    tele = await channel(db, org, "telegram")
    fb = await channel(db, org, "facebook")
    for ch in (tele, fb):                                  # Telegram / Facebook không phải kênh nhận tin
        await db.execute(text("INSERT INTO core.channel_sessions (channel_id, account_label, state) "
                              "VALUES (:c, 'tk', 'active')"), {"c": ch})
    await db.commit()
    d = (await owner.get("/owner/brief")).json()
    assert d["ai_ready"] is True and d["channels_connected"] == 0
    await db.execute(text("INSERT INTO core.channel_sessions (channel_id, account_label, state) "
                          "VALUES (:c, 'Zalo của Sếp', 'active'), (:c, 'cũ', 'expired')"), {"c": zalo})
    await db.commit()
    assert (await owner.get("/owner/brief")).json()["channels_connected"] == 1


def test_next_at_is_the_next_vn_slot() -> None:
    vn = briefing.VN_TZ
    for now, want in ((datetime(2026, 10, 11, 6, 0, tzinfo=vn), (11, 7, 30)), (datetime(2026, 10, 11, 7, 30, tzinfo=vn),
                                                                               (11, 17, 30)),
                      (datetime(2026, 10, 11, 8, 0, tzinfo=vn), (11, 17, 30)),
                      (datetime(2026, 10, 11, 18, 0, tzinfo=vn), (12, 7, 30))):
        got = brief_mod.next_at(now.astimezone(UTC)).astimezone(vn)
        assert (got.day, got.hour, got.minute) == want, (now, got)


def test_front_to_maps_console_paths_and_rejects_foreign_links() -> None:
    f = brief_mod.front_to
    assert f("/tasks") == f("/tasks?overdue=true") == f("/workbench?id=7") == "/owner/viec"
    assert f("/inbox") == f("/inbox?tab=alert") == "/owner/thong-tin"
    assert f("/people") == f("/profile?id=1") == f("/directory") == "/owner/quan-he"
    assert f("/tasksxyz") == "/tasksxyz" and f("/social") == "/social" and f("/system?tab=brain") == "/system?tab=brain"
    for bad in ("", None, "//evil.example", "https://evil.example", "javascript:alert(1)", "\\evil", "/a\nb"):
        assert f(bad, "/owner") == "/owner", bad


# ─── today.nav ───────────────────────────────────────────────────────────────────────────────────────────────────

async def test_today_nav_matches_feed_and_counts_valid_proposals(owner: Api, db: Any, redis: Any) -> None:
    org, uid = await _org(db), await _owner_id(db)
    kh = await _person(db, org, "PER-1", "Chị Lan")
    await unit(db, org, ago(hours=2), "Complained", "Khách than giao chậm", person=kh)
    await unit(db, org, ago(hours=3), "AskedPrice", "Khách hỏi giá", person=kh)
    await alert(db, org, "Khách sắp rời", priority="P1")
    await alert(db, org, "Phản hồi chậm", priority="P2")
    await draft(db, org, "Nháp mới")
    await draft(db, org, "Nháp 10 ngày tuổi", created_at=ago(days=10))
    await proposal(db, redis, org, uid)                                          # hợp lệ
    await proposal(db, redis, org, uid, in_redis=False)                           # hết TTL ở Redis
    await db.commit()
    nav = (await owner.get("/owner/today")).json()["nav"]
    feed_counts = (await owner.get("/owner/feed")).json()["counts"]
    assert nav == {"urgent_open": 2, "decide": 3}
    assert nav["urgent_open"] == feed_counts["urgent_open"]


async def test_today_and_proposals_survive_a_broken_redis(owner: Api, db: Any, redis: Any,
                                                          monkeypatch: pytest.MonkeyPatch,
                                                          caplog: pytest.LogCaptureFixture) -> None:
    """F-60-R2: Redis chậm / hỏng không được làm hỏng `/owner/today` (thanh điều hướng + Tình báo) hay `/proposals`."""
    org, uid = await _org(db), await _owner_id(db)
    await unit(db, org, ago(hours=2), "Complained", "Khách than giao chậm")
    await draft(db, org, "Nháp mới")
    await proposal(db, redis, org, uid)
    await db.commit()

    async def broken(*_a: Any, **_k: Any) -> Any:
        raise RedisConnectionError("mật khẩu=bí-mật-123 không nối được")

    monkeypatch.setattr(gen_proposals, "load_many", broken)
    with caplog.at_level("WARNING"):
        r = await owner.get("/owner/today")
        p = await owner.get("/owner/proposals")
    assert r.status_code == 200, r.text
    assert r.json()["nav"] == {"urgent_open": 1, "decide": 1, "proposals_unavailable": True}   # chỉ còn nháp
    assert p.status_code == 200 and p.json() == {"items": [], "state": "unavailable"}
    assert "ConnectionError" in caplog.text and "bí-mật-123" not in caplog.text                # chỉ ghi loại lỗi


# ─── proposals ───────────────────────────────────────────────────────────────────────────────────────────────────

async def test_proposals_only_valid_redis_backed_own_pending(owner: Api, db: Any, redis: Any) -> None:
    org, uid = await _org(db), await _owner_id(db)
    other = await other_user(db, org)
    good = await proposal(db, redis, org, uid, ptype="reminder", created_at=ago(hours=2))
    pin = await proposal(db, redis, org, uid, ptype="kho_create", requires_pin=True, summary="Ghi vào Kho",
                         created_at=ago(hours=1), ttl=3600)
    # tin vẫn ghi pending, Redis đã hết TTL
    await proposal(db, redis, org, uid, in_redis=False)
    await proposal(db, redis, org, uid, status="cancelled")                       # đã huỷ ở Redis
    await proposal(db, redis, org, uid, status="confirmed")                       # đã xác nhận
    await proposal(db, redis, org, uid, owner=other)                              # Redis ghi của người khác
    await proposal(db, redis, org, other)                                         # hội thoại của người khác
    await proposal(db, redis, org, uid, created_at=ago(hours=30))                 # tin cũ hơn 24 giờ
    await db.commit()
    r = await owner.get("/owner/proposals")
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    assert [i["id"] for i in items] == [pin["id"], good["id"]]                    # sắp hết hạn trước (TTL 1 giờ)
    assert all(set(i) == {"id", "type", "type_label", "summary", "needs_pin", "created_at", "expires_at",
                          "conversation_id"} for i in items)
    g, p = items[1], items[0]
    assert g["type"] == "reminder" and g["type_label"] == "Tạo nhắc việc" and g["needs_pin"] is False
    assert g["summary"] == "Nhắc gọi lại chị Lan lúc 9h"          # tóm tắt do HỆ THỐNG viết (Redis), không chép từ tin
    assert g["conversation_id"] == str(good["cid"])
    assert p["type"] == "kho_create" and p["needs_pin"] is True and "Kho" in p["type_label"]
    exp = datetime.fromisoformat(p["expires_at"].replace("Z", "+00:00"))
    assert timedelta(minutes=50) < exp - datetime.now(UTC) <= timedelta(hours=1)
    assert "KHÔNG ĐƯỢC LẤY" not in r.text


async def test_proposals_use_one_redis_round_trip_for_the_whole_list(owner: Api, db: Any, redis: Any,
                                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    """F-60-R13 (sau /code-review): trước đây 2 lượt Redis tuần tự (GET + TTL) cho MỖI đề xuất trên mỗi /owner/today."""
    from redis.asyncio.client import Pipeline, Redis

    org, uid = await _org(db), await _owner_id(db)
    for i in range(5):
        await proposal(db, redis, org, uid, created_at=ago(minutes=10 + i))
    await db.commit()
    prop_key = gen_proposals.key("")
    direct: list[Any] = []
    pipes: list[int] = []
    real_exec, real_pipe = Redis.execute_command, Pipeline.execute

    async def spy_exec(self: Any, *args: Any, **kw: Any) -> Any:
        if len(args) > 1 and str(args[1]).startswith(prop_key):
            direct.append(args[:2])
        return await real_exec(self, *args, **kw)

    async def spy_pipe(self: Any, *a: Any, **kw: Any) -> Any:
        n = sum(1 for cmd in self.command_stack if len(cmd[0]) > 1 and str(cmd[0][1]).startswith(prop_key))
        if n:
            pipes.append(n)
        return await real_pipe(self, *a, **kw)

    monkeypatch.setattr(Redis, "execute_command", spy_exec)
    monkeypatch.setattr(Pipeline, "execute", spy_pipe)
    items = (await owner.get("/owner/proposals")).json()["items"]
    assert len(items) == 5 and all(i["expires_at"] for i in items)
    assert direct == [] and pipes == [10]                       # một pipeline: 5 GET + 5 TTL, không lệnh lẻ
    pipes.clear()
    assert (await owner.get("/owner/today")).json()["nav"]["decide"] == 5 and pipes == [10]


async def test_proposals_empty(owner: Api) -> None:
    assert (await owner.get("/owner/proposals")).json() == {"items": []}


# ─── care ────────────────────────────────────────────────────────────────────────────────────────────────────────

async def _care_person(db: Any, org: uuid.UUID, code: str, name: str, ptype: str, *,
                       churn: float | None = None, heat: float | None = None, msgs: int = 0, quiet_days: int = 0,
                       deleted: bool = False, merged_into: uuid.UUID | None = None) -> uuid.UUID:
    pid = await _person(db, org, code, name, ptype=ptype, heat=heat)
    if churn is not None:
        await db.execute(text("""INSERT INTO clean.current_scores (subject_type, subject_id, dimension, value,
                                                                   snapshot_id, updated_at)
                                 VALUES ('person', :i, 'churn_risk', :v, :s, now())"""),
                         {"i": pid, "v": churn, "s": uuid.uuid4()})
    if deleted:
        await db.execute(text("UPDATE core.persons SET deleted_at = now() WHERE id = :i"), {"i": pid})
    if merged_into:
        await db.execute(text("UPDATE core.persons SET merged_into_id = :m WHERE id = :i"),
                         {"m": merged_into, "i": pid})
    if msgs:
        ch = await channel(db, org)
        sid = await identity(db, pid, ch)
        for k in range(msgs):
            await event(db, org, ch, ago(days=quiet_days + k, hours=1), sender=sid)
    return pid


async def test_care_rule_order_and_scope(owner: Api, db: Any) -> None:
    org = await _org(db)
    # churn, unknown
    a = await _care_person(db, org, "C-1", "Anh Unknown", "unknown", churn=75, msgs=1, quiet_days=3)
    # churn cao nhất
    b = await _care_person(db, org, "C-2", "Chị Sắp Rời", "customer", churn=90, msgs=2, quiet_days=40)
    # silent 30 ngày
    c = await _care_person(db, org, "C-3", "Ông Im Lặng", "partner", churn=20, msgs=4, quiet_days=30)
    # silent 15 ngày
    d = await _care_person(db, org, "C-4", "Bà Lâu Chưa Nhắn", "supplier", msgs=3, quiet_days=15)
    # không thuộc "cần chăm"
    await _care_person(db, org, "N-1", "Nhân viên rủi ro", "staff", churn=95, msgs=5, quiet_days=60)
    await _care_person(db, org, "N-2", "Ứng viên rủi ro", "candidate", churn=95)
    await _care_person(db, org, "N-3", "Học viên rủi ro", "learner", churn=95)
    await _care_person(db, org, "N-4", "Rủi ro 59", "customer", churn=59, msgs=2, quiet_days=1)
    await _care_person(db, org, "N-5", "Mới nhắn 2 tin", "customer", msgs=2, quiet_days=40)               # chỉ 2 tin
    await _care_person(db, org, "N-6", "Nhắn 5 ngày trước", "customer", msgs=5, quiet_days=5)             # chưa 14 ngày
    await _care_person(db, org, "N-7", "Đã xoá", "customer", churn=99, deleted=True)
    await _care_person(db, org, "N-8", "Đã gộp", "customer", churn=99, merged_into=a)
    await db.execute(text("""INSERT INTO biz.opportunities (org_id, code, person_id, need, stage, value_vnd, confidence,
                                                             first_signal_at)
                             VALUES (:o, 'OPP-1', :p, 'MDF', 'negotiating', 5000000, 'high', now()),
                                    (:o, 'OPP-2', :p, 'Đã thắng', 'won', 9000000, 'high', now())"""),
                     {"o": org, "p": b})
    await db.commit()
    r = await owner.get("/owner/relations?list=care")
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    # churn giảm dần (90, 75, 20, rồi người chưa có điểm), cùng nhau thì ngày im lặng giảm dần
    assert [i["name"] for i in items] == ["Chị Sắp Rời", "Anh Unknown", "Ông Im Lặng", "Bà Lâu Chưa Nhắn"]
    assert [i["reason"] for i in items] == ["churn", "churn", "silent", "silent"]
    assert [i["person_id"] for i in items] == [str(b), str(a), str(c), str(d)]
    assert all({"id", "name", "subtitle", "metric_text", "to", "person_id", "reason"} <= set(i) for i in items)
    assert items[0]["churn_risk"] == 90 and items[0]["open_value_vnd"] == 5_000_000        # chỉ cơ hội còn mở
    assert items[0]["last_contact_days"] == 40 and items[2]["last_contact_days"] == 30
    assert items[1]["to"] == f"/profile?id={a}" and items[1]["subtitle"] == "Chưa phân loại"      # unknown vẫn có mặt
    assert "ngày chưa liên lạc" in items[3]["metric_text"] and "sắp rời" in items[0]["metric_text"]
    assert (await owner.get("/owner/relations?list=care&limit=2")).json()["items"][1]["name"] == "Anh Unknown"
    assert (await owner.get("/owner/relations?list=care&limit=999")).status_code == 200


async def test_care_marks_people_who_already_have_an_open_reminder(owner: Api, db: Any) -> None:
    """F-60-R15 (sau /code-review): "Gen đề xuất" không gợi ý tạo nhắc trùng — dòng care báo `has_open_task` khi người
    đã có việc/nhắc hẹn ĐANG MỞ gắn vào hồ sơ (xong/huỷ thì không)."""
    org = await _org(db)
    open_ = await _care_person(db, org, "R-1", "Đã có nhắc", "customer", churn=80)
    done = await _care_person(db, org, "R-2", "Nhắc đã xong", "customer", churn=70)
    none = await _care_person(db, org, "R-3", "Chưa có nhắc", "customer", churn=60)
    for i, (pid, status) in enumerate(((open_, "todo"), (open_, "done"), (done, "done"), (done, "cancelled"))):
        await db.execute(text("""INSERT INTO biz.tasks (org_id, code, title, priority, status, subject_type, subject_id,
                                                         due_at, source)
                                 VALUES (:o, :c, 'Gọi hỏi thăm', 'P3', :s, 'person', :p, now() + interval '1 day',
                                         'manual')"""), {"o": org, "c": f"TSK-R{i}", "s": status, "p": pid})
    await db.commit()
    items = {i["person_id"]: i for i in (await owner.get("/owner/relations?list=care")).json()["items"]}
    assert items[str(open_)]["has_open_task"] is True
    assert "has_open_task" not in items[str(done)] and "has_open_task" not in items[str(none)]


async def test_care_bounds_the_silent_scan_to_a_window_and_keeps_churn_people(owner: Api, db: Any) -> None:
    """F-60-R3: luật "im lặng" chỉ xét người có tin trong CARE_WINDOW_DAYS; rủi ro rời bỏ cao thì không bị cắt."""
    org = await _org(db)
    await _care_person(db, org, "W-1", "Im lặng trong cửa sổ", "customer", msgs=3, quiet_days=100)
    await _care_person(db, org, "W-2", "Im lặng quá lâu", "customer", msgs=3, quiet_days=svc.CARE_WINDOW_DAYS + 40)
    await _care_person(db, org, "W-3", "Quá lâu nhưng sắp rời", "customer", churn=80, msgs=3,
                       quiet_days=svc.CARE_WINDOW_DAYS + 40)
    await _care_person(db, org, "W-4", "Chưa từng nhắn", "unknown")
    await db.commit()
    r = await owner.get("/owner/relations?list=care")
    assert r.status_code == 200 and "state" not in r.json()
    assert [i["name"] for i in r.json()["items"]] == ["Quá lâu nhưng sắp rời", "Im lặng trong cửa sổ"]


async def test_care_timeout_or_error_returns_state_error_not_500(owner: Api, db: Any,
                                                                 monkeypatch: pytest.MonkeyPatch) -> None:
    async def boom(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError("quá giờ 5s — nội-dung-nhạy-cảm")

    monkeypatch.setattr(svc, "_care", boom)
    r = await owner.get("/owner/relations?list=care")
    assert r.status_code == 200 and r.json() == {"list": "care", "items": [], "state": "error"}
    assert (await owner.get("/owner/relations?list=hot")).status_code == 200            # danh sách khác không ảnh hưởng


async def test_care_empty_org_and_other_lists_have_optional_fields(owner: Api, db: Any) -> None:
    assert (await owner.get("/owner/relations?list=care")).json() == {"list": "care", "items": []}
    from tests.test_owner_v0155 import seed_world
    w = await seed_world(db)
    matches = (await owner.get("/owner/relations?list=matches")).json()["items"]
    assert matches[0]["match_id"] == matches[0]["id"] and matches[0]["person_id"] == str(w["a"])
    cooling = (await owner.get("/owner/relations?list=cooling")).json()["items"]
    assert cooling[0]["person_id"] == str(w["a"]) and cooling[0]["last_contact_days"] == 60
    bridges = (await owner.get("/owner/relations?list=bridges")).json()["items"]
    assert bridges[0]["person_id"] == str(w["d"])
    hot = (await owner.get("/owner/relations?list=hot")).json()["items"]
    assert hot[0]["open_value_vnd"] == 0 or "open_value_vnd" in hot[0]


# ─── chỉ đọc / không lộ ──────────────────────────────────────────────────────────────────────────────────────────

def test_owner_package_never_reads_analytics_schema() -> None:
    for f in sorted(OWNER_DIR.glob("*.py")):
        assert "analytics." not in f.read_text(encoding="utf-8"), f.name


async def test_new_gets_are_read_only_and_reject_write_methods(owner: Api, db: Any) -> None:
    org = await _org(db)
    await draft(db, org)
    await db.commit()
    before = (await db.execute(text("SELECT count(*) FROM ops.action_log"))).scalar_one()
    for path in GET_PATHS:
        assert (await owner.get(path)).status_code in (200, 404), path
    await db.rollback()
    assert (await db.execute(text("SELECT count(*) FROM ops.action_log"))).scalar_one() == before
    for method in ("POST", "PUT", "PATCH", "DELETE"):
        for path in ("/owner/brief", "/owner/feed", "/owner/insights", "/owner/proposals"):
            assert (await owner.send(method, path, {})).status_code == 405, (method, path)
