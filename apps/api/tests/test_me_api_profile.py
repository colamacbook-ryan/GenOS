# ruff: noqa: E501
"""API Hồ sơ Sếp (`/me/profile*`): PIN, tách "Sếp nói"/"em đoán", xác nhận/từ chối, IDOR, Nhật ký."""

import uuid

from sqlalchemy import text

from gh.biz.mem import entities, profile
from tests.conftest import OWNER, Api
from tests.mem_helpers import mk_org
from tests.phase2 import org_id
from tests.test_owner_profile_support import link_owner_user, mk_raw, mk_world


async def _pin(api: Api) -> None:
    r = await api.send("POST", "/auth/pin/verify", {"pin": OWNER["pin"]})
    assert r.status_code == 200, r.text


async def test_me_api_profile_get_has_six_sections_and_separates_owner_and_assistant(owner_api, db) -> None:  # type: ignore[no-untyped-def]
    api: Api = owner_api
    w = await link_owner_user(db, await org_id(db))
    raw = await mk_raw(db, w.org, w.channel, identity=w.owner_identity, body="Quý này anh ưu tiên mở quán thứ 2.", group=w.group)
    prop = await profile.propose(db, w.org, section="goals", text_value="Quý này anh ưu tiên mở quán thứ 2.", raw_event_id=raw)
    assert prop is not None
    await db.commit()
    r = await api.get("/me/profile")
    assert r.status_code == 200, r.text
    b = r.json()
    assert [s["code"] for s in b["sections"]] == ["goals", "resources", "key_relations", "values_limits", "rhythm", "report_prefs"]
    assert b["breadcrumb"] == [{"tier": "me", "id": str(w.owner), "label": "Tôi"}] and b["proposals_n"] == 1
    item = b["sections"][0]["items"][0]
    assert (item["status"], item["source"], item["source_label"], item["editable"]) == ("proposed", "assistant", "em đoán", True)
    assert item["fact_ids"] == [item["fact_id"]] and len(item["evidence_ids"]) == 1 and item["evidence"][0]["quote_status"] == "verified"
    assert all(s["items"] == [] for s in b["sections"][1:])


async def test_me_api_profile_writes_need_pin_then_roundtrip_and_log(owner_api, db) -> None:  # type: ignore[no-untyped-def]
    api: Api = owner_api
    org = await org_id(db)
    await link_owner_user(db, org)
    body = {"section": "goals", "text": "mở quán thứ 2"}
    assert (await api.send("POST", "/me/profile/items", body)).status_code == 423                       # chưa PIN ⇒ không ghi gì
    assert (await db.execute(text("SELECT count(*) FROM mem.facts"))).scalar_one() == 0
    await _pin(api)
    r = await api.send("POST", "/me/profile/items", body)
    assert r.status_code == 201, r.text
    a = r.json()
    assert a["item"]["status"] == "owner_confirmed" and a["section"] == "goals" and a["version"] >= 1 and a["notify_secondary"] is False
    assert [x["tier"] for x in a["breadcrumb"]] == ["me", "evidence"] and len(a["evidence_ids"]) == 1
    fid = a["item"]["fact_id"]
    r = await api.send("PATCH", f"/me/profile/items/{fid}", {"text": "mở quán thứ 3"})
    assert r.status_code == 200, r.text
    e = r.json()
    assert e["item"]["text"] == "mở quán thứ 3" and e["replaced_fact_id"] == fid
    r = await api.send("DELETE", f"/me/profile/items/{e['item']['fact_id']}")
    assert r.status_code == 200 and r.json()["item"]["status"] == "retracted"
    assert [i for s in (await api.get("/me/profile")).json()["sections"] for i in s["items"]] == []
    acts = (await db.execute(text("SELECT action FROM ops.action_log WHERE action LIKE 'mem.profile_%' ORDER BY at"))).scalars().all()
    assert acts == ["mem.profile_add", "mem.profile_edit", "mem.profile_remove"]
    detail = (await db.execute(text("SELECT detail FROM ops.action_log WHERE action = 'mem.profile_add'"))).scalar_one()
    assert "quán" not in str(detail)                                                                      # Nhật ký không mang nội dung
    # quiet hours ⇒ cờ báo kênh phụ
    r = await api.send("POST", "/me/profile/items", {"section": "rhythm", "text": "giờ yên lặng 22h-6h"})
    assert r.status_code == 201 and r.json()["notify_secondary"] is True


async def test_me_api_profile_validation_messages_and_errors_are_friendly(owner_api, db) -> None:  # type: ignore[no-untyped-def]
    api: Api = owner_api
    await link_owner_user(db, await org_id(db))
    await _pin(api)
    assert (await api.send("POST", "/me/profile/items", {"section": "goals"})).status_code == 422
    assert (await api.send("POST", "/me/profile/items", {"section": "goals", "text": "a", "object_entity_id": str(uuid.uuid4())})).status_code == 422
    assert (await api.send("POST", "/me/profile/items", {"section": "khong_co", "text": "a"})).status_code == 422
    r = await api.send("POST", "/me/profile/items", {"section": "goals", "text": "x" * 500})
    assert r.status_code == 422 and r.json()["code"] == "TEXT_TOO_LONG" and r.json().get("detail")       # chuỗi thân thiện + "Chi tiết kỹ thuật"
    r = await api.send("POST", "/me/profile/items", {"section": "goals", "text": "   "})
    assert r.status_code == 422 and r.json()["code"] == "EMPTY_TEXT"
    r = await api.send("POST", "/me/profile/items", {"section": "key_relations", "object_entity_id": str(uuid.uuid4())})
    assert r.status_code == 404 and r.json()["code"] == "BAD_ENTITY"


async def test_me_api_profile_decide_confirm_without_pin_for_normal_and_pin_for_defensive(owner_api, db) -> None:  # type: ignore[no-untyped-def]
    api: Api = owner_api
    w = await link_owner_user(db, await org_id(db))

    async def prop(section: str, sentence: str) -> str:
        raw = await mk_raw(db, w.org, w.channel, identity=w.owner_identity, body=sentence, group=w.group)
        p = await profile.propose(db, w.org, section=section, text_value=sentence, raw_event_id=raw)
        assert p is not None
        await db.commit()
        return str(p.fact.id)

    normal = await prop("goals", "Quý này anh ưu tiên mở quán thứ 2.")
    risky = await prop("report_prefs", "Đừng báo chuyện lừa đảo vặt nhé.")
    r = await api.send("POST", f"/me/profile/proposals/{normal}/decide", {"decision": "confirm"})
    assert r.status_code == 200, r.text
    assert r.json()["item"]["status"] == "owner_confirmed" and r.json()["replaced_fact_id"] == normal
    assert (await api.send("POST", f"/me/profile/proposals/{risky}/decide", {"decision": "confirm"})).status_code == 423   # mục phòng thủ cần PIN
    assert (await db.execute(text("SELECT status FROM mem.facts WHERE id = :i"), {"i": risky})).scalar_one() == "proposed"
    r = await api.send("POST", f"/me/profile/proposals/{risky}/decide", {"decision": "reject"})
    assert r.status_code == 200 and r.json()["item"]["status"] == "retracted"                                               # từ chối không cần PIN
    assert (await api.send("POST", f"/me/profile/proposals/{risky}/decide", {"decision": "reject"})).status_code == 404       # đã retracted
    assert (await api.send("POST", f"/me/profile/proposals/{normal}/decide", {"decision": "confirm"})).status_code == 404     # id cũ đã superseded
    assert (await api.send("POST", f"/me/profile/proposals/{uuid.uuid4()}/decide", {"decision": "confirm"})).status_code == 404
    assert (await api.send("POST", f"/me/profile/proposals/{normal}/decide", {"decision": "xxx"})).status_code == 422
    # xác nhận mục phòng thủ sau khi nhập PIN
    risky2 = await prop("report_prefs", "Đừng báo, trừ khi lừa đảo, nhé.")
    await _pin(api)
    r = await api.send("POST", f"/me/profile/proposals/{risky2}/decide", {"decision": "confirm"})
    assert r.status_code == 200 and r.json()["item"]["status"] == "owner_confirmed"


async def test_me_api_profile_idor_other_org_and_non_profile_facts(owner_api, db) -> None:  # type: ignore[no-untyped-def]
    api: Api = owner_api
    mine = await org_id(db)
    await link_owner_user(db, mine)
    other = await mk_world(db, await mk_org(db, "Org khác"))
    theirs = await profile.add_item(db, other.org, user_id=other.user, section="goals", text_value="mục bí mật của org khác", action_log_id=uuid.uuid4())
    lan = (await entities.create(db, mine, "person", "Lan")).id
    from gh.biz.mem import facts
    from tests.mem_helpers import mkfact

    mine_other = await facts.assert_fact(db, mkfact(mine, lan, "HN", predicate="lives_in"))
    raw = await mk_raw(db, other.org, other.channel, identity=other.owner_identity, body="Anh ưu tiên mở quán thứ 2 nhé", group=other.group)
    prop = await profile.propose(db, other.org, section="goals", text_value="Anh ưu tiên mở quán thứ 2 nhé", raw_event_id=raw)
    assert prop is not None
    await db.commit()
    await _pin(api)
    for fid in (theirs.fact.id, mine_other.id):
        assert (await api.send("PATCH", f"/me/profile/items/{fid}", {"text": "đè"})).status_code == 404
        assert (await api.send("DELETE", f"/me/profile/items/{fid}")).status_code == 404
    assert (await api.send("POST", f"/me/profile/proposals/{prop.fact.id}/decide", {"decision": "confirm"})).status_code == 404
    assert (await api.send("POST", "/me/profile/items", {"section": "key_relations", "object_entity_id": str(other.owner)})).status_code == 404
    assert "bí mật" not in (await api.get("/me/profile")).text
    left = (await db.execute(text("SELECT status FROM mem.facts WHERE id = ANY(:i)"), {"i": [theirs.fact.id, mine_other.id, prop.fact.id]})).scalars().all()
    assert sorted(left) == ["asserted", "owner_confirmed", "proposed"]                                                         # không ai bị đụng


async def test_me_api_profile_requires_session(client, owner_api, db) -> None:  # type: ignore[no-untyped-def]
    anon = Api(client)
    anon.c.cookies.clear()
    assert (await anon.get("/me/profile")).status_code == 401
    assert (await anon.send("POST", "/me/profile/items", {"section": "goals", "text": "x"})).status_code == 401
    assert (await anon.get("/me/obligations")).status_code == 401
    assert (await anon.get(f"/entities/{uuid.uuid4()}")).status_code == 401
    assert (await anon.get(f"/evidence/raw/{uuid.uuid4()}")).status_code == 401
