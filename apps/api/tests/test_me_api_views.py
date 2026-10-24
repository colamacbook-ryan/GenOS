# ruff: noqa: E501
"""API xem (L4): /me/obligations · /me/relations · /me/time · /entities/{id}[/timeline] · /evidence/raw/{id}. IDOR, PIN, breadcrumb, fact_ids."""

import uuid
from datetime import UTC, datetime, timedelta

from gh.biz.mem import consolidate, entities, facts, forget, heat, profile
from tests.conftest import OWNER, Api
from tests.mem_helpers import T, mk_entity, mk_org, mkfact
from tests.phase2 import org_id
from tests.test_owner_profile_support import link_owner_user, mk_person, mk_raw, mk_world


async def _pin(api: Api) -> None:
    assert (await api.send("POST", "/auth/pin/verify", {"pin": OWNER["pin"]})).status_code == 200


def _iso(delta: timedelta) -> str:
    return (datetime.now(UTC) + delta).isoformat()


async def test_me_api_obligations_mine_and_owed_sorted_by_due(owner_api, db) -> None:  # type: ignore[no-untyped-def]
    api: Api = owner_api
    w = await link_owner_user(db, await org_id(db))
    lan = await mk_entity(db, w.org, "Chị Lan")
    f_late = await facts.assert_fact(db, mkfact(w.org, w.owner, {"text": "gửi hàng mẫu", "due_at": _iso(timedelta(days=5))}, T(1), predicate="promised_to", cls="owner"))
    f_soon = await facts.assert_fact(db, mkfact(w.org, w.owner, {"text": "báo giá", "due_at": _iso(timedelta(hours=10))}, T(1), predicate="owes", cls="owner"))
    f_none = await facts.assert_fact(db, mkfact(w.org, w.owner, "gọi lại", T(1), predicate="promised_to", cls="owner"))
    f_over = await facts.assert_fact(db, mkfact(w.org, w.owner, {"text": "trả tiền", "due_at": _iso(-timedelta(days=1))}, T(1), predicate="owes", cls="owner"))
    f_owed = await facts.assert_fact(db, mkfact(w.org, lan, {"text": "chuyển tiền cọc", "to_entity_id": str(w.owner)}, T(1), predicate="owes", cls="insider"))
    await facts.assert_fact(db, mkfact(w.org, lan, {"text": "nợ người khác", "to_entity_id": str(uuid.uuid4())}, T(1), predicate="owes"))      # không phải nợ Sếp
    await db.commit()
    r = await api.get("/me/obligations")
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["breadcrumb"][0]["tier"] == "me"
    assert [x["fact_id"] for x in b["mine"]] == [str(f_over.id), str(f_soon.id), str(f_late.id), str(f_none.id)]    # theo hạn, không hạn cuối
    assert [(x["overdue"], x["due_soon"]) for x in b["mine"]] == [(True, False), (False, True), (False, False), (False, False)]
    assert all(x["fact_ids"] == [x["fact_id"]] and len(x["evidence_ids"]) == 1 for x in b["mine"])
    assert [x["fact_id"] for x in b["owed_to_me"]] == [str(f_owed.id)] and b["owed_to_me"][0]["subject"]["label"] == "Chị Lan"


async def test_me_api_obligations_do_not_leak_other_org(owner_api, db) -> None:  # type: ignore[no-untyped-def]
    api: Api = owner_api
    mine = await org_id(db)
    await link_owner_user(db, mine)
    other = await mk_world(db, await mk_org(db, "Org khác"))
    await facts.assert_fact(db, mkfact(other.org, other.owner, "bí mật org khác", T(1), predicate="promised_to", cls="owner"))
    await db.commit()
    r = await api.get("/me/obligations")
    assert r.status_code == 200 and "bí mật" not in r.text and r.json()["mine"] == []


async def test_me_api_relations_key_relations_with_heat_and_active_fill(owner_api, db) -> None:  # type: ignore[no-untyped-def]
    api: Api = owner_api
    w = await link_owner_user(db, await org_id(db))
    mai_p, mai_i = await mk_person(db, w.org, w.channel, "Chị Mai", "mai1")
    _, tu_i = await mk_person(db, w.org, w.channel, "Anh Tuấn", "tu1")
    mai = (await entities.ensure_person(db, w.org, mai_p)).id
    await profile.add_item(db, w.org, user_id=w.user, section="key_relations", object_entity_id=mai, action_log_id=uuid.uuid4())
    now = datetime.now(UTC)
    for i in range(4):
        await mk_raw(db, w.org, w.channel, identity=mai_i, body="a", at=now - timedelta(hours=10 + i))
        await mk_raw(db, w.org, w.channel, identity=tu_i, body="b", at=now - timedelta(hours=20 + i))
    await heat.rebuild(db, w.org, weeks=12)
    await db.commit()
    r = await api.get("/me/relations?top=5")
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["breadcrumb"][0]["tier"] == "me" and b["weeks"] == 12
    kinds = [(x["label"], x["kind"]) for x in b["relations"]]
    assert kinds[0] == ("Chị Mai", "key_relation") and ("Anh Tuấn", "active") in kinds
    mai_item = b["relations"][0]
    assert mai_item["status"] == "owner_confirmed" and len(mai_item["heat"]) == 12 and len(mai_item["fact_ids"]) == 1 and len(mai_item["evidence_ids"]) == 1
    assert any(x["msgs_ba"] > 0 for x in mai_item["heat"]) and mai_item["state"] in ("hot", "warm", "cool", "cold")
    assert len((await api.get("/me/relations?top=1")).json()["relations"]) == 1
    assert (await api.get("/me/relations?top=0")).status_code == 422


async def test_me_api_time_lists_upcoming_events_and_deadlines(owner_api, db) -> None:  # type: ignore[no-untyped-def]
    api: Api = owner_api
    w = await link_owner_user(db, await org_id(db))
    lan = await mk_entity(db, w.org, "Chị Lan")
    a = await facts.assert_fact(db, mkfact(w.org, lan, {"text": "đám giỗ", "at": _iso(timedelta(days=3))}, T(1), predicate="scheduled_event"))
    b = await facts.assert_fact(db, mkfact(w.org, w.owner, {"text": "nộp thuế", "due_at": _iso(timedelta(days=1))}, T(1), predicate="deadline", cls="owner"))
    await facts.assert_fact(db, mkfact(w.org, lan, {"text": "xa", "at": _iso(timedelta(days=40))}, T(1), predicate="scheduled_event"))
    await facts.assert_fact(db, mkfact(w.org, lan, {"text": "đã qua", "at": _iso(-timedelta(days=2))}, T(1), predicate="scheduled_event"))
    await facts.assert_fact(db, mkfact(w.org, lan, "không có giờ", T(1), predicate="scheduled_event"))
    await db.commit()
    r = await api.get("/me/time?days=14")
    assert r.status_code == 200, r.text
    assert [x["fact_id"] for x in r.json()["items"]] == [str(b.id), str(a.id)] and r.json()["days"] == 14
    assert len((await api.get("/me/time?days=60")).json()["items"]) == 3
    assert (await api.get("/me/time?days=0")).status_code == 422


async def test_me_api_entity_facts_history_summary_heat_pin_and_merge_redirect(owner_api, db) -> None:  # type: ignore[no-untyped-def]
    api: Api = owner_api
    w = await link_owner_user(db, await org_id(db))
    lan_p, lan_i = await mk_person(db, w.org, w.channel, "Chị Lan", "lan1")
    lan = (await entities.ensure_person(db, w.org, lan_p)).id
    dup = await mk_entity(db, w.org, "Lan Kho")
    v1 = await facts.assert_fact(db, mkfact(w.org, lan, "HN", T(1), predicate="lives_in", speaker=lan))
    v2 = await facts.assert_fact(db, mkfact(w.org, lan, "HCM", T(5), predicate="lives_in", speaker=lan))       # đóng v1 theo thời gian sự việc
    phone = await facts.assert_fact(db, mkfact(w.org, lan, "0901234567", T(1), predicate="contact_phone", speaker=lan))
    await facts.assert_fact(db, mkfact(w.org, lan, {"text": "gửi mẫu"}, T(2), predicate="promised_to"))
    await facts.assert_fact(db, mkfact(w.org, dup, "giá sỉ", T(2), predicate="prefers"))
    await entities.merge(db, w.org, dup, lan)
    await mk_raw(db, w.org, w.channel, identity=lan_i, body="a", at=datetime.now(UTC) - timedelta(hours=3))
    await heat.rebuild(db, w.org, weeks=4)
    await consolidate.rebuild_summaries(db, w.org)
    await db.commit()
    r = await api.get(f"/entities/{lan}")
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["breadcrumb"] == [{"tier": "object", "id": str(lan), "label": "Chị Lan"}] and b["entity"]["type"] == "person"
    live = {x["fact_id"]: x for x in b["facts"]}
    assert str(v2.id) in live and str(v1.id) not in live and str(phone.id) not in live and b["hidden_pin_n"] == 1     # SĐT ẩn khi chưa PIN
    assert {x["predicate"] for x in b["facts"]} == {"lives_in", "promised_to", "prefers"}                           # sự thật của thực thể đã gộp có mặt
    assert str(v1.id) in {x["fact_id"] for x in b["history"]}
    assert [x["predicate"] for x in b["open_items"]] == ["promised_to"]
    assert b["summary"] and len(b["summary"]["lines"]) == len(b["summary"]["fact_ids"]) and all(x["fact_ids"] for x in b["summary"]["lines"])
    assert "0901" not in r.text and b["heat"]["series"] and len(b["heat"]["series"]) == 12
    assert all(len(x["fact_ids"]) == 1 and x["evidence_ids"] for x in b["facts"])
    await _pin(api)
    b2 = (await api.get(f"/entities/{lan}")).json()
    assert str(phone.id) in {x["fact_id"] for x in b2["facts"]} and b2["hidden_pin_n"] == 0
    red = await api.get(f"/entities/{dup}")                                                                          # id cũ đã gộp ⇒ trả thực thể đích
    assert red.status_code == 200 and red.json()["entity"]["id"] == str(lan)
    # quên người ⇒ 404; id lạ ⇒ 404
    await forget.erase_entity(db, w.org, lan_p)
    await db.commit()
    assert (await api.get(f"/entities/{lan}")).status_code == 404
    assert (await api.get(f"/entities/{uuid.uuid4()}")).status_code == 404


async def test_me_api_entity_idor_other_org_is_404_and_never_leaks(owner_api, db) -> None:  # type: ignore[no-untyped-def]
    api: Api = owner_api
    mine = await org_id(db)
    await link_owner_user(db, mine)
    other = await mk_world(db, await mk_org(db, "Org khác"))
    sec = await mk_entity(db, other.org, "Khách bí mật")
    await facts.assert_fact(db, mkfact(other.org, sec, "địa chỉ bí mật", T(1), predicate="lives_in", speaker=sec))
    raw = await mk_raw(db, other.org, other.channel, identity=other.owner_identity, body="tin bí mật", group=other.group)
    await db.commit()
    for url in (f"/entities/{sec}", f"/entities/{sec}/timeline", f"/evidence/raw/{raw}", f"/facts?subject_id={sec}"):
        r = await api.get(url)
        assert r.status_code == 404 and "bí mật" not in r.text, url


async def test_me_api_timeline_lanes_range_and_validation(owner_api, db) -> None:  # type: ignore[no-untyped-def]
    api: Api = owner_api
    w = await link_owner_user(db, await org_id(db))
    lan = await mk_entity(db, w.org, "Chị Lan")
    p = await facts.assert_fact(db, mkfact(w.org, lan, {"text": "gửi mẫu"}, T(2), predicate="promised_to"))
    ev = await facts.assert_fact(db, mkfact(w.org, lan, {"text": "khai trương"}, T(10), predicate="scheduled_event"))
    cf = await facts.assert_fact(db, mkfact(w.org, lan, {"text": "chậm hàng"}, T(5), predicate="complained_about"))
    owes = await facts.assert_fact(db, mkfact(w.org, lan, {"text": "cọc 5 triệu"}, T(7), predicate="owes"))
    hr = await facts.assert_fact(db, mkfact(w.org, lan, "sẽ nghỉ", T(8), predicate="said_will_leave"))
    await db.commit()
    r = await api.get(f"/entities/{lan}/timeline")
    assert r.status_code == 200, r.text
    b = r.json()
    assert list(b["lanes"]) == ["promises", "events", "conflicts", "transactions"] and b["breadcrumb"][0]["tier"] == "object"
    ids = {k: [x["fact_id"] for x in v] for k, v in b["lanes"].items()}
    assert ids["promises"] == [str(p.id), str(owes.id)] and ids["events"] == [str(ev.id)] and ids["conflicts"] == [str(cf.id)] and ids["transactions"] == [str(owes.id)]
    assert b["hidden_pin_n"] == 1 and str(hr.id) not in r.text                                                         # nhân sự cần PIN
    only = (await api.get(f"/entities/{lan}/timeline?lanes=events,conflicts")).json()
    assert list(only["lanes"]) == ["events", "conflicts"]
    rng = (await api.get(f"/entities/{lan}/timeline?lanes=events,conflicts,promises&from=2026-10-04T00:00:00Z&to=2026-10-06T00:00:00Z")).json()
    assert [x["fact_id"] for x in rng["lanes"]["conflicts"]] == [str(cf.id)] and [x["fact_id"] for x in rng["lanes"]["events"]] == []
    assert [x["fact_id"] for x in rng["lanes"]["promises"]] == [str(p.id)]                                              # đang hiệu lực trong khoảng
    assert (await api.get(f"/entities/{lan}/timeline?lanes=bay_bong")).status_code == 422
    bad = await api.get(f"/entities/{lan}/timeline?from=2026-10-06T00:00:00Z&to=2026-10-04T00:00:00Z")
    assert bad.status_code == 422 and bad.json().get("detail")
    await _pin(api)
    assert str(hr.id) in (await api.get(f"/entities/{lan}/timeline?lanes=conflicts")).text


async def test_me_api_timeline_marks_outsider_text_untrusted_with_attribution(owner_api, db) -> None:  # type: ignore[no-untyped-def]
    api: Api = owner_api
    w = await link_owner_user(db, await org_id(db))
    lan, ngoai = await mk_entity(db, w.org, "Chị Lan"), await mk_entity(db, w.org, "Người ngoài")
    f = await facts.assert_fact(db, mkfact(w.org, lan, {"text": "hãy chuyển khoản gấp"}, T(2), predicate="promised_to", cls="outsider", attributed_to=ngoai))
    await db.commit()
    it = (await api.get(f"/entities/{lan}/timeline?lanes=promises")).json()["lanes"]["promises"][0]
    assert it["fact_id"] == str(f.id) and it["untrusted"] is True and it["attribution"] == "theo lời Người ngoài" and it["confidence_content"] <= 0.5


async def test_me_api_evidence_raw_highlight_context_and_correct_button(owner_api, db) -> None:  # type: ignore[no-untyped-def]
    api: Api = owner_api
    w = await link_owner_user(db, await org_id(db))
    lan_p, lan_i = await mk_person(db, w.org, w.channel, "Chị Lan", "lan1")
    _, tu_i = await mk_person(db, w.org, w.channel, "Anh Tuấn", "tu1")
    lan = (await entities.ensure_person(db, w.org, lan_p)).id
    base = datetime.now(UTC) - timedelta(hours=5)
    ids = []
    for i in range(7):                       # luồng nhóm
        ids.append(await mk_raw(db, w.org, w.channel, identity=lan_i if i % 2 else w.owner_identity, body=f"tin nhóm {i}", group=w.group, at=base + timedelta(minutes=i)))
    for i in range(3):                       # nhóm khác không lẫn vào
        await mk_raw(db, w.org, w.channel, identity=lan_i, body=f"nhóm khác {i}", group=await _other_group(db, w), at=base + timedelta(minutes=i, seconds=30))
    f = await facts.assert_fact(db, mkfact(w.org, lan, "HN", T(1), predicate="lives_in", speaker=lan, raw=ids[3]))
    sens = await facts.assert_fact(db, mkfact(w.org, lan, "0909", T(1), predicate="contact_phone", speaker=lan, raw=ids[3]))
    await db.commit()
    r = await api.get(f"/evidence/raw/{ids[3]}?around=2")
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["message"]["text"] == "tin nhóm 3" and b["breadcrumb"][0]["tier"] == "evidence"
    assert [m["text"] for m in b["context"]["before"]] == ["tin nhóm 1", "tin nhóm 2"] and [m["text"] for m in b["context"]["after"]] == ["tin nhóm 4", "tin nhóm 5"]
    assert [h["fact_id"] for h in b["highlights"]] == [str(f.id)] and b["highlights"][0]["span_end"] > b["highlights"][0]["span_start"] >= 0
    assert b["facts"] == [{"fact_id": str(f.id), "predicate": "lives_in", "status": "asserted", "can_correct": True}] and b["fact_ids"] == [str(f.id)]
    assert b["hidden_pin_n"] == 1 and str(sens.id) not in r.text                                                       # sự thật nhạy cảm cần PIN
    assert len(b["evidence_ids"]) == 1 and all("nhóm khác" not in m["text"] for m in b["context"]["before"] + b["context"]["after"])
    assert len((await api.get(f"/evidence/raw/{ids[0]}?around=0")).json()["context"]["before"]) == 0
    assert (await api.get(f"/evidence/raw/{ids[3]}?around=99")).status_code == 422
    assert (await api.get(f"/evidence/raw/{uuid.uuid4()}")).status_code == 404
    await _pin(api)
    assert {x["predicate"] for x in (await api.get(f"/evidence/raw/{ids[3]}")).json()["facts"]} == {"lives_in", "contact_phone"}
    # nút "Sai / Sửa": gọi đúng route correct của L1 với fact_id trả về
    r = await api.send("POST", f"/facts/{f.id}/correct", {"new_value": "Đà Nẵng"})
    assert r.status_code == 200 and r.json()["fact"]["status"] == "owner_confirmed"
    _ = tu_i


async def _other_group(db, w):  # type: ignore[no-untyped-def]
    from tests.test_owner_profile_support import mk_group

    return await mk_group(db, w.org, w.channel, "Nhóm khác")


async def test_me_api_evidence_dm_context_does_not_mix_other_customers(owner_api, db) -> None:  # type: ignore[no-untyped-def]
    api: Api = owner_api
    w = await link_owner_user(db, await org_id(db))
    _, a_i = await mk_person(db, w.org, w.channel, "Khách A", "ka")
    _, b_i = await mk_person(db, w.org, w.channel, "Khách B", "kb")
    t = datetime.now(UTC) - timedelta(hours=2)
    a1 = await mk_raw(db, w.org, w.channel, identity=a_i, body="A hỏi giá", at=t)
    await mk_raw(db, w.org, w.channel, identity=b_i, body="B hỏi hàng", at=t + timedelta(minutes=1))
    await mk_raw(db, w.org, w.channel, identity=None, body="Sếp trả lời B", at=t + timedelta(minutes=2), direction="outbound", payload={"threadId": "kb"})
    await mk_raw(db, w.org, w.channel, identity=None, body="Sếp trả lời A", at=t + timedelta(minutes=3), direction="outbound", payload={"threadId": "ka"})
    await mk_raw(db, w.org, w.channel, identity=a_i, body="A cảm ơn", at=t + timedelta(minutes=4))
    await db.commit()
    b = (await api.get(f"/evidence/raw/{a1}?around=5")).json()
    assert [m["text"] for m in b["context"]["after"]] == ["Sếp trả lời A", "A cảm ơn"] and b["context"]["before"] == []
