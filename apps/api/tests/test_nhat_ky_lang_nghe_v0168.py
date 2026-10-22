"""v0.1.68 — Nhật ký lắng nghe: dòng thời gian gộp tin + sự kiện kênh, "hệ thống đã làm gì", phân trang con trỏ,
lọc, quyền (403, 1-1 chỉ Owner/người phụ trách, che số, tìm chữ không dò được số bị che), sự kiện WebSocket."""

import uuid
from typing import Any

import orjson
from sqlalchemy import text

from gh.data import listen_events
from gh.data.ingest import handle_directory, handle_group_discovered, handle_status
from gh.db import sessionmaker
from gh.realtime import Hub
from tests.conftest import Api
from tests.phase2 import listen, msg, org_id, put
from tests.test_phase2_api import FakeWs, user
from tests.test_rbac_api import login_as

PHONE = "0912345678"


async def _channel(db: Any, org: uuid.UUID, type_: str = "zalo") -> Any:
    return (await db.execute(text("SELECT id FROM core.channels WHERE org_id = :o AND type = :t"),
                             {"o": org, "t": type_})).scalar_one()


async def _agent_decision(db: Any, org: uuid.UUID, raw_id: uuid.UUID, decision: str = "draft", *, draft: bool = True,
                          outcome: str = "held", rationale: str | None = None) -> dict[str, Any]:
    """Một agent đã quyết định cho tin `raw_id` (đơn vị ý nghĩa + chứng cứ + bản nháp thật nếu cần)."""
    unit = (await db.execute(text("""
        INSERT INTO clean.meaning_units (org_id, observed_at, event_type, conclusion, entities, confidence, run_id)
        VALUES (:o, now(), 'AskedPrice', 'Hỏi giá', '{}'::jsonb, 0.9, core.uuid_v7()) RETURNING id"""),
                            {"o": org})).scalar_one()
    await db.execute(text("""
        INSERT INTO clean.evidence (meaning_unit_id, meaning_observed_at, raw_event_id, raw_received_at, quote)
        SELECT mu.id, mu.observed_at, e.id, e.received_at, e.body_text FROM clean.meaning_units mu, raw.events e
        WHERE mu.id = :u AND e.id = :r"""), {"u": unit, "r": raw_id})
    agent = (await db.execute(text("""
        INSERT INTO agent.identities (org_id, name, role_desc, addressing, voice, speak_when, autonomy_level)
        VALUES (:o, 'Trợ lý Mai', 'CSKH', '{}'::jsonb, 'lễ phép', 'khi được tag', 4) RETURNING id"""),
                              {"o": org})).scalar_one()
    draft_id = None
    if draft:
        draft_id = (await db.execute(text("""
            INSERT INTO biz.action_drafts (org_id, code, kind, agent_id, body, autonomy_level, status)
            VALUES (:o, 'DR-0001', 'message', :a, '{"text": "Dạ em gửi báo giá"}'::jsonb, 4, 'pending')
            RETURNING id"""), {"o": org, "a": agent})).scalar_one()
    did = (await db.execute(text("""
        INSERT INTO agent.decisions (org_id, agent_id, trigger_ref, trigger_unit_id, decision, outcome, draft_id,
                                     rationale)
        VALUES (:o, :a, CAST(:tr AS jsonb), :u, :d, :oc, :dr, :why) RETURNING id"""),
                            {"o": org, "a": agent, "u": unit, "d": decision, "oc": outcome, "dr": draft_id,
                             "why": rationale,
                             "tr": orjson.dumps({"type": "meaning_unit", "id": str(unit)}).decode()})).scalar_one()
    await db.commit()
    return {"unit": unit, "agent": agent, "draft": draft_id, "decision": did}


async def _frames(sub: Any) -> list[dict[str, Any]]:
    """Mọi khung WebSocket đã phát lên kênh `gh.ws` (get_message trả None cả khi chỉ là xác nhận subscribe nên phải
    thăm dò nhiều lần)."""
    out: list[dict[str, Any]] = []
    for _ in range(6):
        while (m := await sub.get_message(ignore_subscribe_messages=True, timeout=0.2)) is not None:
            out.append(orjson.loads(m["data"]))
    return out


async def _ask(db, org) -> None:  # type: ignore[no-untyped-def]
    """Sếp đặt «Nhóm mới: Hỏi Sếp» (mặc định từ QĐ 17/10 là Lắng nghe im lặng) ⇒ nhóm mới vào `pending`."""
    await db.execute(text("""UPDATE core.organizations SET settings = settings
                             || '{"new_group_mode": {"zalo": "ask"}}'::jsonb WHERE id = :o"""), {"o": org})
    await db.commit()


async def _log(api: Api, qs: str = "") -> dict[str, Any]:
    r = await api.get("/listen-log" + qs)
    assert r.status_code == 200, r.text
    return r.json()  # type: ignore[no-any-return]


# ─── dòng thời gian & "hệ thống đã làm gì" ────────────────────────────────────────────────────────────────────────

async def test_timeline_merges_messages_and_events_newest_first(owner_api: Api, db) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    await listen(db, org, "g1", mode="tagged_only")
    ids = await put(sessionmaker(), org, msg("Báo giá 3 container giúp chị", name="Chị Lan"),
                    msg("Chào anh", group=None, sender="u9", name="Khách lạ"))
    assert all(ids)
    out = await _log(owner_api)
    items = out["items"]
    ats = [i["at"] for i in items]
    assert ats == sorted(ats, reverse=True)
    kinds = [i["kind"] for i in items]
    assert kinds.count("message") == 2 and kinds.count("event") == 2      # 2 tin; sự kiện nhóm mới + người nhắn riêng
    dm = next(i for i in items if i["kind"] == "message" and i["direct"])
    assert dm["group"] is None and dm["sender"]["name"] == "Khách lạ"
    assert dm["listen_mode"] == "silent" and dm["listen_mode_known"] is True
    assert dm["listen_mode_label"] == "Tin riêng — lưu im lặng"
    grp = next(i for i in items if i["kind"] == "message" and i["text"] == "Báo giá 3 container giúp chị")
    assert grp["group"]["name"] == "Nhóm g1" and grp["channel"] == {"type": "zalo", "name": "Zalo"}
    assert grp["listen_mode"] == "tagged_only" and grp["listen_mode_known"] is True
    assert grp["listen_mode_label"] == "Nghe hết, chỉ trả lời khi được tag"
    assert grp["screening"]["state"] == "pending" and grp["decisions"] == [] and out["decisions_visible"] is True
    ev = {i["event_type"]: i for i in items if i["kind"] == "event"}
    assert "group_new" in ev and "direct_new" in ev
    assert ev["group_new"]["text"].startswith("Tài khoản Zalo của Sếp vừa được thêm vào nhóm «Nhóm g1»")
    # QĐ 17/10: nhóm mới mặc định vào Lắng nghe im lặng (không còn «Chờ Sếp chọn» trừ khi Sếp đặt Hỏi Sếp)
    assert ev["group_new"]["text"].endswith("Nhóm tự vào chế độ: Lắng nghe im lặng.")
    assert ev["group_new"]["listen_mode"] == "silent" and ev["group_new"]["listen_mode_label"] == "Lắng nghe im lặng"
    assert ev["direct_new"]["direct"] is True and ev["direct_new"]["person"]["name"] == "Khách lạ"
    assert ev["direct_new"]["icon"] and ev["group_new"]["icon"] != ev["direct_new"]["icon"]


async def test_message_shows_screening_and_agent_decision_with_draft_link(owner_api: Api, db) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    await listen(db, org, "g1", mode="proactive")
    [raw] = await put(sessionmaker(), org, msg("Báo giá container nhé", mentions=True))
    await db.execute(text("""UPDATE refinery.event_state SET state = 'clean', label = 'AskedPrice', confidence = 0.87
                             WHERE event_id = :e"""), {"e": raw})
    d = await _agent_decision(db, org, raw)
    item = next(i for i in (await _log(owner_api))["items"] if i["kind"] == "message" and i["id"] == str(raw))
    assert item["tagged"] is True
    assert item["screening"] == {"state": "clean", "label": "AskedPrice", "confidence": 0.87,
                                 "note": "Đã vào kho sạch"}
    [dec] = item["decisions"]
    assert dec["agent"]["name"] == "Trợ lý Mai" and dec["decision"] == "draft" and dec["outcome"] == "held"
    assert dec["label"] == "Nháp trả lời: chờ duyệt"
    assert dec["draft"] == {"id": str(d["draft"]), "code": "DR-0001", "status": "pending"}
    assert item["note"] is None
    # lọc "chỉ tin có hành động" (agent đã làm gì đó không phải im lặng)
    acted = await _log(owner_api, "?acted=true")
    assert [i["id"] for i in acted["items"]] == [str(raw)]
    # quyết định im lặng thì không tính là hành động
    await db.execute(text("UPDATE agent.decisions SET decision = 'silent', outcome = 'none', draft_id = NULL"))
    await db.commit()
    assert (await _log(owner_api, "?acted=true"))["items"] == []


async def test_blocked_silent_decision_names_the_real_reason(owner_api: Api, db) -> None:  # type: ignore[no-untyped-def]
    """Review Opus P2-2: nhóm Lắng nghe im lặng chặn trả lời ⇒ nhãn nói đúng «nhóm Lắng nghe im lặng», không đổ cho
    «mức tự trị không cho phép»; quyết định bị chặn vì mức tự trị vẫn giữ nhãn cũ."""
    from gh.biz.duty.engine import SILENT_GROUP_REASON
    org = await org_id(db)
    await listen(db, org, "g1", mode="silent")
    [raw] = await put(sessionmaker(), org, msg("Báo giá container nhé", mentions=True))
    await _agent_decision(db, org, raw, "silent", draft=False, outcome="blocked", rationale=SILENT_GROUP_REASON)
    item = next(i for i in (await _log(owner_api))["items"] if i["kind"] == "message" and i["id"] == str(raw))
    [dec] = item["decisions"]
    assert dec["label"] == "Im lặng (nhóm Lắng nghe im lặng — không bao giờ trả lời, kể cả khi được tag)"
    await db.execute(text("UPDATE agent.decisions SET rationale = 'mức 1 (chỉ đọc) không gợi ý hay soạn tin'"))
    await db.commit()
    item = next(i for i in (await _log(owner_api))["items"] if i["id"] == str(raw))
    assert item["decisions"][0]["label"] == "Im lặng (mức tự trị không cho phép)"


async def test_why_nothing_happened_notes(owner_api: Api, db) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    await listen(db, org, "g1", mode="silent")
    [a, b, c] = await put(sessionmaker(), org, msg("Báo giá bao nhiêu vậy"), msg("Alo anh", group=None, sender="u7"),
                          msg("Rác rưởi"))
    await db.execute(text("UPDATE refinery.event_state SET state = 'clean' WHERE event_id = ANY(:i)"), {"i": [a, b]})
    await db.execute(text("""UPDATE refinery.event_state SET state = 'discarded',
                             detail = '{"discarded_by": "prefilter", "reason": "exact_dup"}'::jsonb
                             WHERE event_id = :e"""), {"e": c})
    await db.commit()
    by_id = {i["id"]: i for i in (await _log(owner_api, "?kind=message"))["items"]}
    assert by_id[str(a)]["note"] == "Nhóm Lắng nghe im lặng — chỉ lưu và sàng lọc, không đưa cho agent trực"
    assert by_id[str(b)]["note"] == "Tin riêng ở chế độ lưu im lặng — không đưa cho agent trực"
    assert by_id[str(c)]["screening"]["note"] == "Đã loại khi sàng lọc: Trùng hẳn một tin đã có"
    assert by_id[str(c)]["note"] == "Đã loại ở sàng lọc nên không đưa cho agent"


async def test_group_not_listening_leaves_no_message_row_only_events(owner_api: Api, db) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    await _ask(db, org)                                          # «Hỏi Sếp»: nhóm mới vào `pending`
    [dropped] = await put(sessionmaker(), org, msg("Tin nhóm chưa chọn", group="gNew"))
    assert dropped is None                                       # nhóm `pending` không lưu tin
    items = (await _log(owner_api))["items"]
    assert [i["kind"] for i in items] == ["event"] and items[0]["event_type"] == "group_new"
    assert items[0]["listen_mode"] == "pending" and "Chờ Sếp chọn" in items[0]["text"]


# ─── phân trang con trỏ & lọc ─────────────────────────────────────────────────────────────────────────────────────

async def test_cursor_pagination_has_no_gaps_or_duplicates(owner_api: Api, db) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    await listen(db, org, "g1", mode="silent")
    await put(sessionmaker(), org, *[msg(f"Tin số {i}") for i in range(7)])
    await put(sessionmaker(), org,
              *[msg(f"Riêng {i}", group=None, sender=f"d{i}", name=f"Khách {i}") for i in range(2)])
    full = (await _log(owner_api, "?limit=100"))["items"]
    assert full and (await _log(owner_api, "?limit=100"))["next_cursor"] is None
    seen: list[str] = []
    cursor = None
    pages = 0
    while True:
        out = await _log(owner_api, "?limit=4" + (f"&cursor={cursor}" if cursor else ""))
        assert len(out["items"]) <= 4
        seen += [i["id"] for i in out["items"]]
        pages += 1
        cursor = out["next_cursor"]
        if cursor is None:
            break
        assert pages < 20
    assert seen == [i["id"] for i in full] and len(set(seen)) == len(seen) and pages == -(-len(full) // 4)
    bad = await owner_api.get("/listen-log?cursor=xyz")
    assert bad.status_code == 422


async def test_filters_channel_group_kind_tagged_and_search(owner_api: Api, db) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    g1 = await listen(db, org, "g1", mode="proactive")
    await listen(db, org, "g2", mode="silent")
    wa = await listen(db, org, "wa1", mode="silent", channel="whatsapp")
    await put(sessionmaker(), org, msg("Giá thép cuộn hôm nay", group="g1", mentions=True),
              msg("Hàng về kho chưa", group="g2"), msg("Zdravo bạn", group="wa1", channel="whatsapp"))
    msgs = (await _log(owner_api, "?kind=message"))["items"]
    assert all(i["kind"] == "message" for i in msgs)
    only_events = (await _log(owner_api, "?kind=event"))["items"]
    assert only_events and all(i["kind"] == "event" for i in only_events)
    zalo = (await _log(owner_api, "?channel=zalo&kind=message"))["items"]
    assert {i["channel"]["type"] for i in zalo} == {"zalo"}
    only_wa = (await _log(owner_api, "?channel=whatsapp&kind=message"))["items"]
    assert {i["text"] for i in only_wa} == {"Zdravo bạn"}
    by_group = (await _log(owner_api, f"?group_id={g1}&kind=message"))["items"]
    assert {i["group"]["id"] for i in by_group} == {str(g1)}
    assert (await _log(owner_api, f"?group_id={wa}&channel=zalo"))["items"] == []
    tagged = (await _log(owner_api, "?tagged=true"))["items"]
    assert [i["text"] for i in tagged] == ["Giá thép cuộn hôm nay"]          # tag/acted bỏ hết sự kiện
    found = (await _log(owner_api, "?q=hàng về"))["items"]
    assert [i["text"] for i in found] == ["Hàng về kho chưa"]
    like = (await _log(owner_api, "?q=100%25"))["items"]                   # `%` được thoát, không thành ký tự đại diện
    assert like == []
    by_name = (await _log(owner_api, "?q=Nhóm g2"))["items"]
    assert any(i["kind"] == "message" for i in by_name) and any(i["kind"] == "event" for i in by_name)
    assert (await owner_api.get("/listen-log?group_id=khong-phai-uuid")).status_code == 422
    assert (await owner_api.get("/listen-log?limit=0")).status_code == 422


async def test_stats_counts_24h_by_channel_and_groups_listening(owner_api: Api, db) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    await listen(db, org, "g1", mode="silent")
    await listen(db, org, "wa1", mode="silent", channel="whatsapp")
    await listen(db, org, "gx", mode="off")
    await put(sessionmaker(), org, msg("Một"), msg("Hai"), msg("Ba", group="wa1", channel="whatsapp"),
              msg("Riêng", group=None, sender="d1"))
    s = (await owner_api.get("/listen-log/stats")).json()
    by = {c["type"]: c for c in s["by_channel"]}
    assert by["zalo"]["messages_24h"] == 3 and by["whatsapp"]["messages_24h"] == 1
    assert by["zalo"]["groups_listening"] == 1 and by["whatsapp"]["groups_listening"] == 1
    assert s["groups_listening"] == 2 and s["messages_24h"] == 4 and s["events_24h"] >= 4
    # nhóm "Không nghe" không có trong ô lọc
    assert {g["name"] for g in s["groups"]} == {"Nhóm g1", "Nhóm wa1"}


# ─── sự kiện kênh ─────────────────────────────────────────────────────────────────────────────────────────────────

async def test_directory_events_first_sync_is_summary_then_changes_are_events(owner_api: Api, db, redis) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)

    def grp(x: str, name: str, members: list[str], complete: bool = True) -> dict[str, Any]:
        return {"external_id": x, "name": name, "member_count": len(members), "members_complete": complete,
                "members": [{"external_id": m, "name": f"Người {m}"} for m in members]}

    await handle_directory(db, redis, org, {"channel": "zalo", "groups_complete": True,
                                            "groups": [grp("gA", "Nhóm A", ["m1", "m2", "m3"]),
                                                       grp("gB", "Nhóm B", ["m1"])]})
    await db.commit()
    kinds = [i["event_type"] for i in (await _log(owner_api))["items"]]
    assert kinds == ["groups_synced"]                    # lần đầu: một dòng tóm tắt, không từng nhóm/thành viên
    await db.execute(text("UPDATE core.groups SET listen_mode = 'silent' WHERE external_id = 'gA'"))
    await db.commit()
    await _ask(db, org)                                  # «Hỏi Sếp»: nhóm mới (gC) vào `pending`
    # quét lần hai: gA đổi tên + thêm m4, bớt m3; gB vắng ⇒ đã rời; gC mới
    await handle_directory(db, redis, org, {"channel": "zalo", "groups_complete": True,
                                            "groups": [grp("gA", "Nhóm A mới", ["m1", "m2", "m4"]),
                                                       grp("gC", "Nhóm C", ["m1"])]})
    await db.commit()
    ev = [i for i in (await _log(owner_api, "?kind=event"))["items"]]
    by_type = {i["event_type"]: i for i in ev}
    assert set(by_type) == {"groups_synced", "group_renamed", "members_changed", "group_left", "group_new"}
    assert by_type["group_renamed"]["text"] == "Nhóm «Nhóm A» đổi tên thành «Nhóm A mới»."
    assert by_type["members_changed"]["text"] == ("Nhóm «Nhóm A mới»: 1 thành viên mới (Người m4), "
                                                  "1 thành viên rời (Người m3).")
    assert by_type["group_left"]["text"].startswith("Tài khoản Zalo của Sếp không còn trong nhóm «Nhóm B»")
    assert by_type["group_new"]["group"]["name"] == "Nhóm C" and by_type["group_new"]["listen_mode"] == "pending"
    # nhóm không nghe (gC pending) không báo từng thành viên; gB cũng không
    assert [i["group"]["name"] for i in ev if i["event_type"] == "members_changed"] == ["Nhóm A mới"]
    # trở lại nhóm: quét thấy gB ⇒ group_rejoined
    await handle_directory(db, redis, org, {"channel": "zalo", "groups_complete": True,
                                            "groups": [grp("gA", "Nhóm A mới", ["m1", "m2", "m4"]),
                                                       grp("gB", "Nhóm B", ["m1"]), grp("gC", "Nhóm C", ["m1"])]})
    await db.commit()
    assert "group_rejoined" in {i["event_type"] for i in (await _log(owner_api, "?kind=event"))["items"]}


async def test_many_new_groups_after_first_sync_are_capped_with_a_summary_line(owner_api: Api, db, redis) -> None:  # type: ignore[no-untyped-def]
    """Review Opus P3: quét danh bạ KHÔNG phải lần đầu thấy hàng trăm nhóm mới (đổi tài khoản) ⇒ chỉ ghi ≤ 50 dòng
    «nhóm mới» (kèm WebSocket), phần dư gom một dòng tóm tắt; nhóm vẫn được tạo đủ."""
    org = await org_id(db)
    await handle_directory(db, redis, org, {"channel": "zalo", "groups_complete": False,
                                            "groups": [{"external_id": "g0", "name": "Nhóm đầu"}]})
    await db.commit()
    total = listen_events.MAX_GROUP_NEW_EVENTS + 20
    await handle_directory(db, redis, org, {"channel": "zalo", "groups_complete": False,
                                            "groups": [{"external_id": f"gn{i}", "name": f"Nhóm mới {i}"}
                                                       for i in range(total)]})
    n_groups = (await db.execute(text("SELECT count(*) FROM core.groups WHERE external_id LIKE 'gn%'"))).scalar_one()
    assert n_groups == total
    counts = {r.kind: r.n for r in (await db.execute(text(
        "SELECT kind, count(*) AS n FROM ops.listen_events GROUP BY kind"))).all()}
    assert counts.get("group_new", 0) == listen_events.MAX_GROUP_NEW_EVENTS
    assert counts.get("groups_synced", 0) == 2        # lần quét đầu + dòng tóm tắt phần dư
    assert len(listen_events.take_pending(db)) == listen_events.MAX_GROUP_NEW_EVENTS + 2   # + 2 dòng tóm tắt
    await db.commit()
    texts = [i["text"] for i in (await _log(owner_api, "?kind=event"))["items"] if i["event_type"] == "groups_synced"]
    assert any("còn 20 nhóm mới nữa" in t for t in texts)


async def test_new_group_follows_new_group_mode_setting(owner_api: Api, db, redis) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    await db.execute(text("""UPDATE core.organizations
                             SET settings = settings || '{"new_group_mode": {"zalo": "silent"}}'::jsonb
                             WHERE id = :o"""),
                     {"o": org})
    await db.commit()
    await handle_group_discovered(db, redis, org, {"channel": "zalo", "group_id": "gZ", "name": "Nhóm Z"})
    await db.commit()
    [ev] = (await _log(owner_api, "?kind=event"))["items"]
    assert ev["event_type"] == "group_new" and ev["listen_mode"] == "silent"
    assert ev["text"].endswith("Nhóm tự vào chế độ: Lắng nghe im lặng.")
    # tin nhóm vừa vào chế độ im lặng được lưu và ghi đúng chế độ lúc đó
    [raw] = await put(sessionmaker(), org, msg("Xin chào", group="gZ"))
    row = (await db.execute(text("SELECT listen_mode FROM raw.events WHERE id = :i"), {"i": raw})).scalar_one()
    assert row == "silent"


async def test_old_messages_without_recorded_mode_fall_back_to_current_mode(owner_api: Api, db) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    await listen(db, org, "g1", mode="proactive")
    [seed] = await put(sessionmaker(), org, msg("Tin mẫu"))
    # Tin vào kho TRƯỚC v0.1.68 (không có `listen_mode`): bảng thô chỉ-INSERT nên dựng bằng một INSERT thẳng.
    raw = uuid.uuid4()
    await db.execute(text("""
        INSERT INTO raw.events (id, org_id, received_at, occurred_at, channel_id, group_id, sender_identity_id,
                                external_msg_id, kind, body_text, payload, content_hash, direction, mentions_agent)
        SELECT :n, org_id, now(), now(), channel_id, group_id, sender_identity_id, 'old-1', 'text', 'Tin cũ',
               '{}'::jsonb, decode('00', 'hex'), 'inbound', false FROM raw.events WHERE id = :s"""),
                     {"n": raw, "s": seed})
    await db.execute(text("""INSERT INTO refinery.event_state (event_id, event_received_at, org_id, state)
                             SELECT id, received_at, org_id, 'pending' FROM raw.events WHERE id = :n"""), {"n": raw})
    await db.commit()
    item = next(i for i in (await _log(owner_api, "?kind=message"))["items"] if i["id"] == str(raw))
    assert item["listen_mode"] == "proactive" and item["listen_mode_known"] is False


async def test_channel_session_events(owner_api: Api, db, redis, app) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    ch = await _channel(db, org)
    sid = (await db.execute(text("""INSERT INTO core.channel_sessions (channel_id, account_label, state, started_at)
                                    VALUES (:c, 'Zalo Sếp', 'pending_qr', NULL) RETURNING id"""),
                            {"c": ch})).scalar_one()
    await db.commit()
    bus = app.state.bus
    for t, p in (("session.active", {"session_id": str(sid), "account": {"id": "acc1", "name": "Sếp"}}),
                 ("session.reconnecting", {"session_id": str(sid), "error": "NETWORK_UNAVAILABLE"}),
                 ("session.active", {"session_id": str(sid), "account": {"id": "acc1", "name": "Sếp"}}),
                 ("session.ended", {"session_id": str(sid), "reason": "logged_out"})):
        await handle_status(db, redis, bus, org, t, p)
        await db.commit()
    texts = [i["text"] for i in (await _log(owner_api, "?kind=event"))["items"]]
    assert texts == ["Kênh Zalo đã đăng xuất.", "Kênh Zalo đã nối lại (Zalo Sếp).",
                     "Kênh Zalo mất kết nối, đang tự nối lại.", "Kênh Zalo đã đăng nhập (Zalo Sếp)."]
    # phiên chưa từng nối (QR hết hạn) kết thúc thì KHÔNG thành sự kiện "ngắt"
    sid2 = (await db.execute(text("""INSERT INTO core.channel_sessions (channel_id, account_label, state)
                                     VALUES (:c, 'Zalo mới', 'pending_qr') RETURNING id"""), {"c": ch})).scalar_one()
    await db.commit()
    await handle_status(db, redis, bus, org, "session.ended", {"session_id": str(sid2), "reason": "expired"})
    await db.commit()
    assert len((await _log(owner_api, "?kind=event"))["items"]) == 4


async def test_console_logout_records_event_and_publishes(owner_api: Api, db, redis) -> None:  # type: ignore[no-untyped-def]
    from tests.conftest import verify_pin

    org = await org_id(db)
    ch = await _channel(db, org)
    await db.execute(text("""INSERT INTO core.channel_sessions (channel_id, account_label, state, started_at)
                             VALUES (:c, 'Zalo Sếp', 'active', now())"""), {"c": ch})
    await db.commit()
    await verify_pin(owner_api)
    sub = redis.pubsub()
    await sub.subscribe("gh.ws")
    r = await owner_api.send("POST", "/channels/zalo/logout")
    assert r.status_code == 204, r.text
    [ev] = (await _log(owner_api, "?kind=event"))["items"]
    assert ev["event_type"] == "channel_ended" and ev["text"] == "Kênh Zalo đã đăng xuất."
    frames = await _frames(sub)
    # Pub/sub của Redis dùng chung cho mọi DB số trên cùng máy chủ: khi hai lượt pytest chạy song song, khung của
    # lượt kia lọt vào đây — nên chỉ đòi "có ít nhất một", và MỌI khung listen.event đều chỉ là tín hiệu.
    live = [f for f in frames if f["type"] == "listen.event"]
    assert any(f["data"]["event_type"] == "channel_ended" for f in live)
    assert all("text" not in f["data"] and "group" not in f["data"] for f in live)   # chỉ tín hiệu
    await sub.aclose()


# ─── quyền & che ──────────────────────────────────────────────────────────────────────────────────────────────────

async def test_role_without_data_read_gets_403(owner_api: Api, client, db) -> None:  # type: ignore[no-untyped-def]
    manager = await login_as(client, db, "manager")
    for url in ("/listen-log", "/listen-log/stats"):
        r = await manager.get(url)
        assert r.status_code == 403, (url, r.text)
        assert "data.read" in r.text
    nav = (await manager.get("/navigation")).json()
    assert "listenlog" not in str(nav)


async def test_auditor_sees_group_rows_masked_but_not_direct_unless_assigned(owner_api: Api, client, db) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    await listen(db, org, "g1", mode="silent")
    ids = await put(sessionmaker(), org, msg(f"Chuyển khoản vào số {PHONE}12 giúp chị", name=f"Chị {PHONE}12"),
                    msg("Tin riêng bí mật", group=None, sender="d1", name="Khách riêng"))
    aud = await login_as(client, db, "auditor")
    items = (await _log(aud))["items"]
    grp = next(i for i in items if i["kind"] == "message" and not i["direct"]
               and "chuyển khoản" in (i["text"] or "").lower())
    assert PHONE not in grp["text"] and "•" in grp["text"] and grp["text"].endswith("12 giúp chị")
    assert PHONE not in str(grp["sender"]) and "•" in grp["sender"]["name"]
    assert not any(i["kind"] == "message" and i["direct"] for i in items)           # 1-1: không thấy
    assert not any(i["kind"] == "event" and i["event_type"] == "direct_new" for i in items)
    assert any(i["kind"] == "event" and i["event_type"] == "group_new" for i in items)
    # tìm chữ không dò được số bị che; tìm chữ thường vẫn ra
    assert (await _log(aud, f"?q={PHONE}"))["items"] == []
    assert (await _log(aud, "?q=0912345678"))["items"] == []
    assert len((await _log(aud, "?q=chuyển khoản"))["items"]) == 1
    assert len((await _log(owner_api, f"?q={PHONE}"))["items"]) == 1                # Owner thì tìm được
    # stats của kiểm soát không đếm tin 1-1
    st = (await aud.get("/listen-log/stats")).json()
    assert st["messages_24h"] == 1                                               # chỉ tin chuyển khoản (không đếm 1-1)
    # được giao đúng người ⇒ thấy tin 1-1 và sự kiện "người mới nhắn riêng"
    dm_person = (await db.execute(text("""SELECT pi.person_id FROM raw.events e
                                          JOIN core.person_identities pi ON pi.id = e.sender_identity_id
                                          WHERE e.id = :i"""), {"i": ids[1]})).scalar_one()
    aud_id = (await db.execute(text("SELECT id FROM core.users WHERE email = 'auditor@example.vn'"))).scalar_one()
    await db.execute(text("UPDATE core.persons SET owner_user_id = :u WHERE id = :p"), {"u": aud_id, "p": dm_person})
    await db.commit()
    items = (await _log(aud))["items"]
    assert any(i["kind"] == "message" and i["direct"] and i["text"] == "Tin riêng bí mật" for i in items)
    assert any(i["kind"] == "event" and i["event_type"] == "direct_new" for i in items)


async def test_search_cannot_probe_masked_numbers_written_in_unicode_digits(owner_api: Api, client, db) -> None:  # type: ignore[no-untyped-def]
    """Review Opus P3: `mask_text` (Python `\\d`) che cả chữ số toàn chiều rộng / Ả Rập–Ấn Độ; bộ lọc tìm chữ ở
    PostgreSQL cũng phải bỏ chúng, không thì vai dưới Owner dò được số đã che bằng chính chữ số đó."""
    org = await org_id(db)
    await listen(db, org, "g1", mode="silent")
    full = "０９１２３４５６７８"                      # toàn chiều rộng
    arab = "٠٩١٢٣٤٥٦٧٨"                              # Ả Rập–Ấn Độ
    await put(sessionmaker(), org, msg(f"Số tài khoản {full} nhé"), msg(f"Số điện thoại {arab} nhé"))
    aud = await login_as(client, db, "auditor")
    shown = " ".join(i["text"] or "" for i in (await _log(aud))["items"] if i["kind"] == "message")
    assert full not in shown and arab not in shown and "•" in shown
    assert (await _log(aud, f"?q={full}"))["items"] == []
    assert (await _log(aud, f"?q={arab}"))["items"] == []
    assert len((await _log(aud, "?q=Số tài khoản"))["items"]) == 1               # chữ thường vẫn tìm được
    assert len((await _log(owner_api, f"?q={full}"))["items"]) == 1              # Owner thì tìm được


async def test_group_scope_limits_rows_for_scoped_role(owner_api: Api, client, db) -> None:  # type: ignore[no-untyped-def]
    """Vai có `data.read` phạm vi `assigned` chỉ thấy nhóm được giao (cả tin lẫn sự kiện); sự kiện cả kênh bị ẩn."""
    org = await org_id(db)
    g1 = await listen(db, org, "g1", mode="silent")
    await listen(db, org, "g2", mode="silent")
    ch = await _channel(db, org)
    sid = (await db.execute(text("""INSERT INTO core.channel_sessions (channel_id, account_label, state)
                                    VALUES (:c, 'Zalo Sếp', 'pending_qr') RETURNING id"""), {"c": ch})).scalar_one()
    await db.commit()
    await put(sessionmaker(), org, msg("Nhóm một", group="g1"), msg("Nhóm hai", group="g2"))
    aud = await login_as(client, db, "auditor")
    aud_id = (await db.execute(text("SELECT id FROM core.users WHERE email = 'auditor@example.vn'"))).scalar_one()
    await db.execute(text("""UPDATE core.role_permissions SET scope = 'assigned' WHERE permission_code = 'data.read'
                             AND role_id = (SELECT id FROM core.roles WHERE code = 'auditor')"""))
    await db.execute(text("""INSERT INTO core.assignments (org_id, user_id, subject_type, subject_id)
                             VALUES (:o, :u, 'group', :g)"""), {"o": org, "u": aud_id, "g": g1})
    await db.commit()
    items = (await _log(aud))["items"]
    assert {i["text"] for i in items if i["kind"] == "message"} == {"Nhóm một"}
    assert all(i["group"] is None or i["group"]["id"] == str(g1) for i in items)
    events = [i for i in items if i["kind"] == "event"]
    assert events and {i["group"]["id"] for i in events} == {str(g1)}
    assert sid                                     # (sự kiện cả kênh được kiểm riêng ở bài kiểm quyền ALL bên dưới)


async def test_channel_events_hidden_from_scoped_role(owner_api: Api, client, db, redis, app) -> None:  # type: ignore[no-untyped-def]
    from gh.data.ingest import handle_status

    org = await org_id(db)
    ch = await _channel(db, org)
    sid = (await db.execute(text("""INSERT INTO core.channel_sessions (channel_id, account_label, state, started_at)
                                    VALUES (:c, 'Zalo Sếp', 'pending_qr', NULL) RETURNING id"""),
                            {"c": ch})).scalar_one()
    await db.commit()
    await handle_status(db, redis, app.state.bus, org, "session.active",
                        {"session_id": str(sid), "account": {"id": "a", "name": "Sếp"}})
    await db.commit()
    aud = await login_as(client, db, "auditor")
    assert [i["event_type"] for i in (await _log(aud, "?kind=event"))["items"]] == ["channel_connected"]
    await db.execute(text("""UPDATE core.role_permissions SET scope = 'assigned' WHERE permission_code = 'data.read'
                             AND role_id = (SELECT id FROM core.roles WHERE code = 'auditor')"""))
    await db.commit()
    assert (await _log(aud, "?kind=event"))["items"] == []


# ─── WebSocket ────────────────────────────────────────────────────────────────────────────────────────────────────

async def test_ws_listen_event_permission_and_owner_only_flag(app, redis) -> None:  # type: ignore[no-untyped-def]
    hub = Hub(redis)
    owner, auditor, manager = FakeWs(), FakeWs(), FakeWs()
    hub.clients = {owner: user("owner", "o1", {"data.read": "all"}),  # type: ignore[dict-item]
                   auditor: user("auditor", "o1", {"data.read": "all"}),
                   manager: user("manager", "o1", {"data.read": "none"})}
    base = {"id": "e1", "at": "t", "event_type": "group_new", "channel_type": "zalo", "group_id": None}
    await hub.dispatch({"type": "listen.event", "data": {**base, "owner_only": False}, "at": "t", "org_id": "o1"})
    await hub.dispatch({"type": "listen.event", "data": {**base, "event_type": "direct_new", "owner_only": True},
                        "at": "t", "org_id": "o1"})
    assert len(owner.sent) == 2 and len(auditor.sent) == 1 and manager.sent == []
    assert "direct_new" not in auditor.sent[0]


async def test_ingest_publishes_listen_event_for_new_group(owner_api: Api, db, redis, app) -> None:  # type: ignore[no-untyped-def]
    from gh.data.ingest import Ingest

    org = await org_id(db)
    sub = redis.pubsub()
    await sub.subscribe("gh.ws")
    ing = Ingest(app.state.bus, redis, org, sessionmaker())

    class Ev:
        type = "group.discovered"
        payload = {"channel": "zalo", "group_id": "gW", "name": "Nhóm W"}

    await ing.on_directory(Ev())
    types = [f["type"] for f in await _frames(sub)]
    assert "listen.event" in types and "group.discovered" in types
    await sub.aclose()


def test_listen_event_texts_cover_every_kind() -> None:
    for kind in listen_events.KINDS:
        t = listen_events.event_text(kind, {"listen_mode": "off", "old": "A", "groups": 3, "reason": "expired",
                                            "joined": {"count": 2, "names": ["X", "Y"]}}, channel_type="whatsapp",
                                     group_name="G", person_name="P", owner=True)
        assert t and t.endswith(".") and "None" not in t
    # vai dưới Owner: số dài trong tên bị che
    t = listen_events.event_text("direct_new", {}, channel_type="zalo", group_name=None,
                                 person_name="0912345678", owner=False)
    assert "0912345678" not in t and "•" in t


def test_server_event_icons_exist_in_web_icon_registry_and_screen_is_registered() -> None:
    """Máy chủ gửi `icon` cho từng loại sự kiện: web chỉ vẽ icon có trong bảng đăng ký (iconRegistry.ts)."""
    from pathlib import Path

    from gh.auth import rbac
    from gh.shell import navigation

    root = Path(__file__).resolve().parents[3]
    reg = (root / "packages" / "ui" / "src" / "iconRegistry.ts").read_text(encoding="utf-8")
    for kind, (icon, _tone) in listen_events.KINDS.items():
        name = icon.replace("ph ph-", "")
        assert f"'{name}':" in reg or f"\n  {name}:" in reg, f"{kind}: icon {name} chưa đăng ký ở iconRegistry.ts"
    assert "listenlog" in navigation.all_screen_keys() and rbac.SCREEN_PERMISSION["listenlog"] == ("data.read",)
