# ruff: noqa: E501
"""Lõi trí nhớ: quên người (xoá thật, qua Console + PIN) và API sự thật / nhãn bối cảnh."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import text

from gh.biz.mem import aliases, disputes, entities, facts, forget, mentions
from tests.conftest import OWNER, Api
from tests.mem_helpers import T, mk_entity, mkfact
from tests.phase2 import org_id


async def _pin(api: Api) -> None:
    r = await api.send("POST", "/auth/pin/verify", {"pin": OWNER["pin"]})
    assert r.status_code == 200, r.text


async def _person(db, org, name):  # type: ignore[no-untyped-def]
    pid = (await db.execute(text("""INSERT INTO core.persons (org_id, code, display_name) VALUES (:o, core.next_code('PER'), :n)
                                    RETURNING id"""), {"o": org, "n": name})).scalar_one()
    return pid


async def _scenario(db, org):  # type: ignore[no-untyped-def]
    """Lan (có thực thể con đã gộp) + Bình (người khác). Sự thật: về Lan, về Bình có đối tượng = Lan, tranh chấp, retry."""
    p_lan, p_binh, p_lan2 = await _person(db, org, "Chị Lan"), await _person(db, org, "Anh Bình"), await _person(db, org, "Lan Kho")
    lan, binh = await entities.ensure_person(db, org, p_lan), await entities.ensure_person(db, org, p_binh)
    lan2 = await entities.ensure_person(db, org, p_lan2)
    await entities.merge(db, org, lan2.id, lan.id)
    f1 = await facts.assert_fact(db, mkfact(org, lan.id, "0901", T(1), speaker=lan.id))
    f2 = await facts.assert_fact(db, mkfact(org, lan2.id, "HN", T(1), predicate="lives_in", speaker=lan.id))
    f3 = await facts.assert_fact(db, mkfact(org, binh.id, None, T(1), predicate="family_of", object_entity=lan.id, speaker=binh.id))
    keep = await facts.assert_fact(db, mkfact(org, binh.id, "HCM", T(1), predicate="lives_in", speaker=binh.id))
    # tranh chấp của Bình có chứa sự thật về Lan? — tranh chấp khác chỉ bị bỏ id đã xoá
    d, _ = await disputes.open(db, org, binh.id, "lives_in", [keep.id, f3.id])
    await aliases.add(db, org, lan.id, "chị Lan Kho", source="mention")
    await mentions.link(db, org, meaning_unit_id=uuid.uuid4(), meaning_observed_at=T(2), surface="chị Lan Kho")
    await db.execute(text("INSERT INTO mem.entity_summaries (entity_id, org_id, summary, fact_ids) VALUES (:e, :o, 's', :f)"),
                     {"e": lan.id, "o": org, "f": [f1.id]})
    await db.execute(text("INSERT INTO mem.entity_summaries (entity_id, org_id, summary, fact_ids) VALUES (:e, :o, 's2', :f)"),
                     {"e": binh.id, "o": org, "f": [f3.id]})                  # tóm tắt của Bình dựa trên sự thật về Lan
    await db.execute(text("""INSERT INTO mem.relation_heat_weekly (org_id, entity_a, entity_b, week) VALUES (:o, :a, :b, '2026-10-05')"""),
                     {"o": org, "a": binh.id, "b": lan.id})
    await db.execute(text("""INSERT INTO refinery.fact_retry (org_id, fact_in, error_code) VALUES (:o, CAST(:j AS jsonb), 'deadlock')"""),
                     {"o": org, "j": '{"subject_id": "' + str(lan.id) + '"}'})
    await db.commit()
    return p_lan, lan, lan2, binh, [f1, f2, f3], keep, d


async def test_mem_core_forget_erases_person_facts_evidence_events_and_keeps_others(app, db, redis, owner_api) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    p_lan, lan, lan2, binh, gone, keep, d = await _scenario(db, org)
    counts = await forget.erase_entity(db, org, p_lan)
    await db.commit()
    assert counts["facts"] == 3 and counts["evidence"] == 3 and counts["status_events"] >= 3
    assert counts["aliases"] >= 3 and counts["mentions"] == 1 and counts["summaries"] == 2     # cả bản tóm tắt dựa trên sự thật bị xoá
    assert counts["heat"] == 1 and counts["retry"] == 1 and counts["entities"] == 2
    left = (await db.execute(text("SELECT id FROM mem.facts WHERE org_id = :o"), {"o": org})).scalars().all()
    assert left == [keep.id]                                                                  # chỉ còn sự thật không dính Lan
    for t in ("mem.fact_evidence", "mem.fact_status_events"):
        ids = set((await db.execute(text(f"SELECT DISTINCT fact_id FROM {t}"))).scalars().all())  # noqa: S608
        assert ids == {keep.id}
    for e in (lan, lan2):
        got = await entities.get(db, e.id)
        assert got is not None and got.forgotten and got.display_name == forget.ERASED_NAME and got.attrs == {}
    assert (await db.execute(text("SELECT count(*) FROM mem.aliases WHERE entity_id = ANY(:e)"), {"e": [lan.id, lan2.id]})).scalar_one() == 0
    # tên cũ không còn tra được; thực thể đã quên không bị tìm thấy qua bí danh
    assert await aliases.resolve(db, org, "Chị Lan") == []
    dd = await disputes.get(db, org, d)
    assert dd is not None and list(dd.fact_ids) == [keep.id]                                  # id đã xoá bị bỏ khỏi tranh chấp khác
    # chủ thể đã quên không nhận sự thật mới
    import pytest

    with pytest.raises(facts.FactWriteError):
        await facts.assert_fact(db, mkfact(org, lan.id, "x", T(9), predicate="prefers"))
    # chạy lại ⇒ toàn 0, không lỗi
    again = await forget.erase_entity(db, org, p_lan)
    assert again["facts"] == 0 and again["evidence"] == 0 and again["aliases"] == 0
    # người chưa có thực thể ⇒ toàn 0
    assert sum((await forget.erase_entity(db, org, await _person(db, org, "Chưa có"))).values()) == 0
    # cờ xoá đã tắt: xoá lẻ vẫn bị chặn
    with pytest.raises(Exception, match="không cho xoá"):
        await db.execute(text("DELETE FROM mem.facts"))


async def test_mem_core_forget_via_data_request_needs_pin_and_logs_counts(owner_api, db) -> None:  # type: ignore[no-untyped-def]
    api: Api = owner_api
    org = await org_id(db)
    ch = (await db.execute(text("SELECT id FROM core.channels WHERE org_id = :o AND type = 'zalo'"), {"o": org})).scalar_one()
    pid = await _person(db, org, "Anh Nam")
    await db.execute(text("INSERT INTO core.person_identities (person_id, channel_id, external_id) VALUES (:p, :c, 'zx')"), {"p": pid, "c": ch})
    e = await entities.ensure_person(db, org, pid)
    await facts.assert_fact(db, mkfact(org, e.id, "0909", T(1), speaker=e.id))
    await db.commit()
    r = await api.send("POST", f"/persons/{pid}/data-requests", {"kind": "erase"})
    assert r.status_code == 423                                                                # chưa PIN ⇒ không xoá gì
    assert (await db.execute(text("SELECT count(*) FROM mem.facts"))).scalar_one() == 1
    await _pin(api)
    r = await api.send("POST", f"/persons/{pid}/data-requests", {"kind": "erase"})
    assert r.status_code == 201, r.text
    assert r.json()["result"] == {"erased": True}                                              # đúng hình dạng cũ
    assert (await db.execute(text("SELECT count(*) FROM mem.facts"))).scalar_one() == 0
    got = await entities.get(db, e.id)
    assert got is not None and got.forgotten
    detail = (await db.execute(text("SELECT detail FROM ops.action_log WHERE action = 'person_data.erase'"))).scalar_one()
    assert detail["mem_erased"]["facts"] == 1 and "0909" not in str(detail)                    # chỉ con số, không nội dung
    # export / restrict không đụng sổ sự thật
    pid2 = await _person(db, org, "Chị B")
    e2 = await entities.ensure_person(db, org, pid2)
    await facts.assert_fact(db, mkfact(org, e2.id, "0911", T(1), speaker=e2.id))
    await db.commit()
    for kind in ("export", "restrict"):
        assert (await api.send("POST", f"/persons/{pid2}/data-requests", {"kind": kind})).status_code == 201
    assert (await db.execute(text("SELECT count(*) FROM mem.facts"))).scalar_one() == 1


# ─── API ────────────────────────────────────────────────────────────────────

async def test_mem_core_routes_facts_query_correct_and_breadcrumb(owner_api, db) -> None:  # type: ignore[no-untyped-def]
    api: Api = owner_api
    org = await org_id(db)
    sep = (await db.execute(text("SELECT id FROM core.users WHERE org_id = :o"), {"o": org})).scalar_one()
    lan = await mk_entity(db, org, "Chị Lan")
    a = await facts.assert_fact(db, mkfact(org, lan, "khách", T(1), predicate="prefers"))
    hr = await facts.assert_fact(db, mkfact(org, lan, "bỏ việc cuối tháng", T(1), predicate="said_will_leave"))
    await db.commit()
    r = await api.get(f"/facts?subject_id={lan}&predicate=prefers")
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["breadcrumb"] == [{"tier": "object", "id": str(lan), "label": "Chị Lan"}]
    assert [f["id"] for f in b["facts"]] == [str(a.id)] and b["facts"][0]["fact_ids"] == [str(a.id)] and len(b["facts"][0]["evidence_ids"]) == 1
    # sự thật nhân sự (hr) cần PIN: danh sách chung ẩn + đếm; đích danh ⇒ 423
    r = await api.get(f"/facts?subject_id={lan}")
    assert [f["predicate"] for f in r.json()["facts"]] == ["prefers"] and r.json()["hidden_pin_n"] == 1
    assert (await api.get(f"/facts?subject_id={lan}&predicate=said_will_leave")).status_code == 423
    await _pin(api)
    r = await api.get(f"/facts?subject_id={lan}")
    assert {f["predicate"] for f in r.json()["facts"]} == {"prefers", "said_will_leave"} and r.json()["hidden_pin_n"] == 0
    # truy vấn hai thời gian: trước khi sự thật bắt đầu ⇒ rỗng
    r = await api.get(f"/facts?subject_id={lan}&predicate=prefers&at=2026-09-01T00:00:00Z")
    assert r.json()["facts"] == []
    assert (await api.get(f"/facts?subject_id={lan}&predicate=khong_co")).status_code == 422
    assert (await api.get(f"/facts?subject_id={uuid.uuid4()}")).status_code == 404
    # sửa
    r = await api.send("POST", f"/facts/{a.id}/correct", {"new_value": "em họ"})
    assert r.status_code == 200, r.text
    c = r.json()
    assert c["fact"]["status"] == "owner_confirmed" and c["fact"]["confirmed_by"] == str(sep) and c["replaced_fact_id"] == str(a.id)
    assert [x["tier"] for x in c["breadcrumb"]] == ["object", "evidence"]
    assert (await db.execute(text("SELECT status FROM mem.facts WHERE id = :i"), {"i": a.id})).scalar_one() == "superseded"
    assert (await db.execute(text("SELECT count(*) FROM ops.action_log WHERE action = 'mem.fact_correct'"))).scalar_one() == 1
    r = await api.send("POST", f"/facts/{c['fact']['id']}/correct", {"retract": True})
    assert r.status_code == 200 and r.json()["fact"]["status"] == "retracted"
    assert (await api.send("POST", f"/facts/{c['fact']['id']}/correct", {"new_value": "x"})).status_code == 422   # đã retracted
    assert (await api.send("POST", f"/facts/{uuid.uuid4()}/correct", {"retract": True})).status_code == 404
    assert (await api.send("POST", f"/facts/{hr.id}/correct", {})).status_code == 422                            # thiếu hành động
    assert (await api.send("POST", f"/facts/{hr.id}/correct", {"retract": True, "new_value": "x"})).status_code == 422


async def test_mem_core_routes_require_owner_and_session(client, owner_api, db) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    lan = await mk_entity(db, org, "L")
    anon = Api(client)
    anon.c.cookies.clear()
    r = await anon.get(f"/facts?subject_id={lan}")
    assert r.status_code == 401


async def test_mem_core_routes_group_contexts_roundtrip(owner_api, db) -> None:  # type: ignore[no-untyped-def]
    api: Api = owner_api
    org = await org_id(db)
    ch = (await db.execute(text("SELECT id FROM core.channels WHERE org_id = :o AND type = 'zalo'"), {"o": org})).scalar_one()
    gid = (await db.execute(text("""INSERT INTO core.groups (org_id, code, channel_id, external_id, name)
                                    VALUES (:o, 'GRP-T1', :c, 'gt1', 'Nhóm họ hàng') RETURNING id"""), {"o": org, "c": ch})).scalar_one()
    await db.commit()
    r = await api.get(f"/groups/{gid}/contexts")
    assert r.status_code == 200
    b = r.json()
    assert b["labels"] == [] and b["confirmed"] is False and b["effective_context"] == "khac"
    assert b["breadcrumb"] == [{"tier": "context", "id": str(gid), "label": "Nhóm họ hàng"}] and "CHƯA xác nhận" in b["policy_prompt"]
    r = await api.send("PUT", f"/groups/{gid}/contexts", {"labels": [{"context": "gia_dinh", "is_primary": True}, {"context": "ban_be"}]})
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["confirmed"] is True and b["effective_context"] == "gia_dinh"
    assert [(x["context"], x["is_primary"], x["confirmed"]) for x in b["labels"]] == [("gia_dinh", True, True), ("ban_be", False, True)]
    assert "KHÔNG chấm điểm người" in b["policy_prompt"]
    assert (await api.send("PUT", f"/groups/{gid}/contexts", {"labels": [{"context": "linh_tinh"}]})).status_code == 422
    assert (await api.send("PUT", f"/groups/{gid}/contexts", {"labels": []})).status_code == 422
    assert (await api.get(f"/groups/{uuid.uuid4()}/contexts")).status_code == 404
    assert (await db.execute(text("SELECT count(*) FROM ops.action_log WHERE action = 'mem.group_contexts_set'"))).scalar_one() == 1


def test_mem_core_utc_helper_sanity() -> None:
    assert T(3).tzinfo is UTC and T(3) < datetime(2026, 10, 4, tzinfo=UTC)


async def test_mem_core_routes_sensitive_facts_need_pin_for_read_and_correct(owner_api, db) -> None:  # type: ignore[no-untyped-def]
    api: Api = owner_api
    org = await org_id(db)
    lan = await mk_entity(db, org, "Chị Lan")
    stk = await facts.assert_fact(db, mkfact(org, lan, {"digest": "aa", "last3": "123"}, T(1), predicate="payment_account"))
    phone = await facts.assert_fact(db, mkfact(org, lan, "0901", T(1)))
    hr = await facts.assert_fact(db, mkfact(org, lan, "nghỉ", T(1), predicate="said_will_leave"))
    await db.commit()
    for f in (stk, phone, hr):                                   # chưa PIN: không sửa/xoá được, không ghi gì
        r = await api.send("POST", f"/facts/{f.id}/correct", {"retract": True})
        assert r.status_code == 423, r.text
    assert (await db.execute(text("SELECT count(*) FROM mem.facts WHERE status = 'retracted'"))).scalar_one() == 0
    assert (await db.execute(text("SELECT count(*) FROM ops.action_log WHERE action = 'mem.fact_correct'"))).scalar_one() == 0
    assert (await api.get(f"/facts?subject_id={lan}&predicate=payment_account")).status_code == 423
    assert (await api.get(f"/facts?subject_id={lan}")).json()["hidden_pin_n"] == 3
    await _pin(api)
    assert (await api.get(f"/facts?subject_id={lan}&predicate=payment_account")).status_code == 200
    r = await api.send("POST", f"/facts/{stk.id}/correct", {"retract": True})
    assert r.status_code == 200 and r.json()["fact"]["status"] == "retracted"
