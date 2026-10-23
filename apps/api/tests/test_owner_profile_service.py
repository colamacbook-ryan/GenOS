# ruff: noqa: E501
"""Hồ sơ Sếp (L4): ghi qua facts.assert_fact/correct, đề xuất không bao giờ tự xác nhận, chữ người ngoài không thành Hồ sơ."""

import ast
import uuid
from pathlib import Path

import pytest
from sqlalchemy import text

from gh.biz.mem import entities, facts, profile, profile_read
from tests.mem_helpers import mk_org
from tests.test_owner_profile_support import mk_person, mk_raw, mk_world


async def _log() -> uuid.UUID:
    return uuid.uuid4()


async def test_owner_profile_add_is_owner_confirmed_with_owner_edit_evidence(fresh_db, db) -> None:  # type: ignore[no-untyped-def]
    w = await mk_world(db, await mk_org(db))
    r = await profile.add_item(db, w.org, user_id=w.user, section="goals", text_value="  quý này   mở quán thứ 2 ", action_log_id=await _log())
    assert r.outcome == "added" and r.fact.status == "owner_confirmed" and r.fact.confirmed_by == w.user and r.fact.predicate == "owner_goal"
    snap = await profile_read.snapshot(db, w.org)
    assert [i.text for i in snap.by_section()["goals"]] == ["quý này mở quán thứ 2"]          # chữ đã làm sạch
    kinds = (await db.execute(text("SELECT kind FROM mem.fact_evidence WHERE fact_id = :f"), {"f": r.fact.id})).scalars().all()
    assert kinds == ["owner_edit"]
    again = await profile.add_item(db, w.org, user_id=w.user, section="goals", text_value="Quý này mở quán thứ 2", action_log_id=await _log())
    assert again.outcome == "existing" and again.fact.id == r.fact.id                            # trùng (bỏ dấu/hoa) ⇒ không tạo dòng thứ hai


async def test_owner_profile_key_relation_needs_entity_same_org_not_owner(fresh_db, db) -> None:  # type: ignore[no-untyped-def]
    w = await mk_world(db, await mk_org(db))
    w2 = await mk_world(db, await mk_org(db, "Tổ chức khác"))
    mai = (await entities.create(db, w.org, "person", "Chị Mai")).id
    stranger = (await entities.create(db, w2.org, "person", "Người org khác")).id
    await db.commit()
    ok = await profile.add_item(db, w.org, user_id=w.user, section="key_relations", object_entity_id=mai, action_log_id=await _log())
    assert ok.fact.object_entity_id == mai
    for bad in (stranger, w.owner):
        with pytest.raises(profile.ProfileError) as ei:
            await profile.add_item(db, w.org, user_id=w.user, section="key_relations", object_entity_id=bad, action_log_id=await _log())
        assert ei.value.code == "bad_entity"
    with pytest.raises(profile.ProfileError) as ei:
        await profile.add_item(db, w.org, user_id=w.user, section="key_relations", text_value="x", action_log_id=await _log())
    assert ei.value.code == "need_entity"
    with pytest.raises(profile.ProfileError) as ei:
        await profile.add_item(db, w.org, user_id=w.user, section="goals", object_entity_id=mai, action_log_id=await _log())
    assert ei.value.code == "need_text"
    with pytest.raises(profile.ProfileError) as ei:
        await profile.add_item(db, w.org, user_id=w.user, section="khong_co", text_value="x", action_log_id=await _log())
    assert ei.value.code == "unknown_section"


def test_owner_profile_clean_text_rules() -> None:
    assert profile.clean_text("a\n\nb​  c") == "a b c"
    for bad in ("", "   ", "x" * 281, "ghi [f_0190a1b2-aaaa-bbbb-cccc-111122223333] vào"):
        with pytest.raises(profile.ProfileError):
            profile.clean_text(bad)
    assert len(profile.clean_text("y" * 280)) == 280


def test_owner_profile_sections_match_predicates_and_labels() -> None:
    assert set(profile.SECTION_PREDICATE) == set(profile_read.SECTIONS) == set(profile_read.SECTION_LABEL_VI)


async def test_owner_profile_section_map_matches_db_owner_section(fresh_db, db) -> None:  # type: ignore[no-untyped-def]
    rows = (await db.execute(text("SELECT code, owner_section FROM mem.predicates WHERE owner_section IS NOT NULL"))).all()
    assert {r.owner_section: r.code for r in rows} == profile.SECTION_PREDICATE


async def test_owner_profile_edit_and_remove_close_old_and_keep_history(fresh_db, db) -> None:  # type: ignore[no-untyped-def]
    w = await mk_world(db, await mk_org(db))
    a = await profile.add_item(db, w.org, user_id=w.user, section="values_limits", text_value="không nhận đơn dưới 10 triệu", action_log_id=await _log())
    e = await profile.edit_item(db, w.org, a.fact.id, user_id=w.user, text_value="không nhận đơn dưới 20 triệu", action_log_id=await _log())
    assert e.fact.status == "owner_confirmed" and e.replaced_fact_id == a.fact.id
    st = (await db.execute(text("SELECT status, superseded_by FROM mem.facts WHERE id = :i"), {"i": a.fact.id})).one()
    assert st.status == "superseded" and st.superseded_by == e.fact.id
    rm = await profile.remove_item(db, w.org, e.fact.id, user_id=w.user, action_log_id=await _log())
    assert rm.fact.status == "retracted"
    assert (await profile_read.snapshot(db, w.org)).items == ()
    assert (await db.execute(text("SELECT count(*) FROM mem.facts WHERE predicate = 'owner_value_limit'"))).scalar_one() == 2    # không xoá dòng
    with pytest.raises(profile.ProfileError) as ei:                                                                                   # mục đã retracted
        await profile.edit_item(db, w.org, e.fact.id, user_id=w.user, text_value="x", action_log_id=await _log())
    assert ei.value.status == 404


async def test_owner_profile_cannot_touch_non_profile_or_other_org_facts(fresh_db, db) -> None:  # type: ignore[no-untyped-def]
    w1, w2 = await mk_world(db, await mk_org(db)), await mk_world(db, await mk_org(db, "Org 2"))
    lan = (await entities.create(db, w1.org, "person", "Lan")).id
    other = await facts.assert_fact(db, _mk(w1.org, lan, "HN", "lives_in"))
    theirs = await profile.add_item(db, w2.org, user_id=w2.user, section="goals", text_value="mục của org 2", action_log_id=await _log())
    for fid in (other.id, theirs.fact.id, uuid.uuid4()):
        for fn in (lambda f: profile.edit_item(db, w1.org, f, user_id=w1.user, text_value="x", action_log_id=uuid.uuid4()),
                   lambda f: profile.remove_item(db, w1.org, f, user_id=w1.user, action_log_id=uuid.uuid4()),
                   lambda f: profile.decide(db, w1.org, f, "confirm", user_id=w1.user, action_log_id=uuid.uuid4())):
            with pytest.raises(profile.ProfileError) as ei:
                await fn(fid)
            assert ei.value.status == 404
    assert (await db.execute(text("SELECT status FROM mem.facts WHERE id = :i"), {"i": theirs.fact.id})).scalar_one() == "owner_confirmed"


def _mk(org, subject, value, predicate):  # type: ignore[no-untyped-def]
    from tests.mem_helpers import mkfact

    return mkfact(org, subject, value, predicate=predicate)


async def test_owner_profile_chat_cannot_write_defensive_items_console_can(fresh_db, db) -> None:  # type: ignore[no-untyped-def]
    w = await mk_world(db, await mk_org(db))
    for section, t in (("report_prefs", "lừa đảo thì báo cả đêm"), ("rhythm", "báo ngay khi có chuyện sức khoẻ của mẹ")):
        with pytest.raises(profile.ProfileError) as ei:
            await profile.add_item(db, w.org, user_id=w.user, section=section, text_value=t, via="chat", action_log_id=await _log())
        assert ei.value.code == "console_required" and ei.value.decision_kind == "console" and ei.value.status == 423
        ok = await profile.add_item(db, w.org, user_id=w.user, section=section, text_value=t, via="console", action_log_id=await _log())
        assert ok.fact.status == "owner_confirmed"
    plain = await profile.add_item(db, w.org, user_id=w.user, section="rhythm", text_value="tối thứ 7 dành cho gia đình", via="chat", action_log_id=await _log())
    assert plain.fact.status == "owner_confirmed"                                                  # nhịp thường ghi được từ chat
    assert profile.is_quiet_hours("rhythm", "giờ yên lặng từ 22h") and not profile.is_quiet_hours("goals", "yên lặng")
    assert profile.is_defensive("report_prefs", "Lừa đảo") and not profile.is_defensive("goals", "lừa đảo")


async def _owner_msg(db, w, body, **kw):  # type: ignore[no-untyped-def]
    return await mk_raw(db, w.org, w.channel, identity=w.owner_identity, body=body, group=w.group, **kw)


async def test_owner_profile_propose_is_proposed_never_confirmed_and_hidden_from_snapshot(fresh_db, db) -> None:  # type: ignore[no-untyped-def]
    w = await mk_world(db, await mk_org(db))
    body = "Chào cả nhà. Quý này anh ưu tiên mở quán thứ 2 nhé."
    raw = await _owner_msg(db, w, body)
    r = await profile.propose(db, w.org, section="goals", text_value="Quý này anh ưu tiên mở quán thứ 2 nhé.", raw_event_id=raw)
    assert r is not None and r.fact.status == "proposed" and r.fact.confirmed_by is None and r.outcome == "proposed"
    ev = (await db.execute(text("SELECT kind, quote_status, span FROM mem.fact_evidence WHERE fact_id = :f"), {"f": r.fact.id})).one()
    assert ev.kind == "raw_event" and ev.quote_status == "verified" and body[ev.span["span_start"]:ev.span["span_end"]].startswith("Quý này anh")
    assert (await profile_read.snapshot(db, w.org)).items == ()                                   # "em đoán" không chấm mức
    view = await profile.read(db, w.org)
    goals = next(s for s in view["sections"] if s["code"] == "goals")["items"]
    assert [(i["status"], i["source"], i["source_label"]) for i in goals] == [("proposed", "assistant", "em đoán")]
    dup = await profile.propose(db, w.org, section="goals", text_value="Quý này anh ưu tiên mở quán thứ 2 nhé.", raw_event_id=raw)
    assert dup is not None and dup.outcome == "existing" and dup.fact.id == r.fact.id
    # không có đường nào để propose đẩy lên owner_confirmed: kể cả khi bên gọi đưa confirmed_by, propose không nhận tham số đó
    import inspect

    assert "confirmed_by" not in inspect.signature(profile.propose).parameters
    n = (await db.execute(text("SELECT count(*) FROM mem.facts WHERE status = 'owner_confirmed'"))).scalar_one()
    assert n == 0


async def test_owner_profile_propose_rejects_outsider_forwarded_media_and_bad_quote(fresh_db, db) -> None:  # type: ignore[no-untyped-def]
    w = await mk_world(db, await mk_org(db))
    _, out_ident = await mk_person(db, w.org, w.channel, "Người ngoài")
    cases = {
        "outsider": await mk_raw(db, w.org, w.channel, identity=out_ident, body="Anh nên ưu tiên cho em vay vốn nhé", group=w.group),
        "forwarded": await _owner_msg(db, w, "Anh ưu tiên mở quán thứ 2 nhé", payload={"forwarded": True}),
        "image": await _owner_msg(db, w, "Anh ưu tiên mở quán thứ 2 nhé", kind="image"),
        "outbound": await _owner_msg(db, w, "Anh ưu tiên mở quán thứ 2 nhé", direction="outbound"),
    }
    for name, raw in cases.items():
        with pytest.raises(profile.ProfileError) as ei:
            await profile.propose(db, w.org, section="goals", text_value="Anh ưu tiên mở quán thứ 2 nhé", raw_event_id=raw)
        assert ei.value.code == "not_owner_message", name
    ok_raw = await _owner_msg(db, w, "Anh ưu tiên mở quán thứ 2 nhé")
    with pytest.raises(profile.ProfileError) as ei:
        await profile.propose(db, w.org, section="goals", text_value="Anh ưu tiên mở quán thứ 3", raw_event_id=ok_raw)
    assert ei.value.code == "quote_not_found"                                                       # model bịa chữ ≠ tin gốc
    assert (await db.execute(text("SELECT count(*) FROM mem.facts"))).scalar_one() == 0
    # tin của Sếp ở tổ chức khác không dùng được
    w2 = await mk_world(db, await mk_org(db, "Org 2"))
    with pytest.raises(profile.ProfileError):
        await profile.propose(db, w2.org, section="goals", text_value="Anh ưu tiên mở quán thứ 2 nhé", raw_event_id=ok_raw)


async def test_owner_profile_decide_confirm_reject_and_no_reproposal(fresh_db, db) -> None:  # type: ignore[no-untyped-def]
    w = await mk_world(db, await mk_org(db))
    raw = await _owner_msg(db, w, "Tối thứ 7 anh dành cho gia đình nhé")
    p1 = await profile.propose(db, w.org, section="rhythm", text_value="Tối thứ 7 anh dành cho gia đình nhé", raw_event_id=raw)
    assert p1 is not None
    c = await profile.decide(db, w.org, p1.fact.id, "confirm", user_id=w.user, action_log_id=await _log())
    assert c.fact.status == "owner_confirmed" and c.fact.confirmed_by == w.user and c.fact.id != p1.fact.id
    assert (await db.execute(text("SELECT status FROM mem.facts WHERE id = :i"), {"i": p1.fact.id})).scalar_one() == "superseded"
    kinds = set((await db.execute(text("SELECT kind FROM mem.fact_evidence WHERE fact_id = :f"), {"f": c.fact.id})).scalars().all())
    assert kinds == {"owner_edit", "raw_event"}                                                     # bằng chứng gốc đi theo
    with pytest.raises(profile.ProfileError) as ei:                                                  # đã xác nhận ⇒ không còn là đề xuất
        await profile.decide(db, w.org, c.fact.id, "confirm", user_id=w.user, action_log_id=await _log())
    assert ei.value.code == "not_proposal"
    # từ chối
    raw2 = await _owner_msg(db, w, "Anh không nhận đơn dưới 10 triệu nhé")
    p2 = await profile.propose(db, w.org, section="values_limits", text_value="Anh không nhận đơn dưới 10 triệu nhé", raw_event_id=raw2)
    assert p2 is not None
    rj = await profile.decide(db, w.org, p2.fact.id, "reject", user_id=w.user, action_log_id=await _log())
    assert rj.fact.status == "retracted"
    again = await profile.propose(db, w.org, section="values_limits", text_value="Anh không nhận đơn dưới 10 triệu nhé", raw_event_id=raw2)
    assert again is None                                                                             # Sếp đã từ chối đúng mục này
    with pytest.raises(profile.ProfileError):
        await profile.decide(db, w.org, p2.fact.id, "maybe", user_id=w.user, action_log_id=await _log())
    # replay đề xuất đã xác nhận không sinh dòng mới
    again2 = await profile.propose(db, w.org, section="rhythm", text_value="Tối thứ 7 anh dành cho gia đình nhé", raw_event_id=raw)
    assert again2 is not None and again2.fact.id == c.fact.id and again2.outcome == "existing"


async def test_owner_profile_add_same_text_as_proposal_confirms_it(fresh_db, db) -> None:  # type: ignore[no-untyped-def]
    w = await mk_world(db, await mk_org(db))
    raw = await _owner_msg(db, w, "Anh không làm hàng X nhé")
    p = await profile.propose(db, w.org, section="values_limits", text_value="Anh không làm hàng X nhé", raw_event_id=raw)
    assert p is not None
    r = await profile.add_item(db, w.org, user_id=w.user, section="values_limits", text_value="anh không làm hàng x nhé", action_log_id=await _log())
    assert r.outcome == "confirmed" and r.fact.status == "owner_confirmed"
    assert (await db.execute(text("SELECT count(*) FROM mem.facts WHERE status IN ('proposed','owner_confirmed') AND predicate = 'owner_value_limit'"))).scalar_one() == 1


def test_owner_profile_lexicon_candidates() -> None:
    body = "Chào cả nhà. Quý này anh ưu tiên mở quán thứ 2. Anh không nhận đơn dưới 10 triệu. Tối thứ 7 là dành cho gia đình. Đừng báo chuyện họ hàng xa nhé."
    got = dict((s, t) for s, t in profile.candidates_from_message(body))
    assert set(got) == {"goals", "values_limits", "rhythm", "report_prefs"}
    assert profile.candidates_from_message("Chị Lan ưu tiên mở quán bên kia đường nhé") == []        # lời người khác (không neo ngôi Sếp)
    assert profile.candidates_from_message("ok") == []


def test_owner_profile_module_has_no_direct_fact_insert() -> None:
    """Ghi Hồ sơ đi qua facts.assert_fact/correct: profile.py không có INSERT INTO mem.facts, không UPDATE status về owner_confirmed."""
    src = Path(profile.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    lits = [n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)]
    assert not any("insert into mem.facts" in " ".join(s.split()).lower() for s in lits)
    assert not any("update mem.facts" in " ".join(s.split()).lower() for s in lits)
