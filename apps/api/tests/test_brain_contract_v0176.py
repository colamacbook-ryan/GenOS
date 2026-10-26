# ruff: noqa: E501
"""Hợp đồng đầu ra bộ não `brain-out/1` (spec §4F): khuôn đúng như spec, kiểm schema chặt, JSON Schema xuất được, đồng bộ với
danh mục loại tín hiệu (DB) và 6 cổng của chính sách bối cảnh."""

import copy
import hashlib
import json

import pytest
from pydantic import ValidationError
from sqlalchemy import text

from gh.biz.mem import contexts
from gh.refinery import brain_contract as bc

#: Vân tay JSON Schema. ĐỔI HỢP ĐỒNG ⇒ test này đỏ ⇒ phải tăng `CONTRACT_VERSION` ("brain-out/3"), cập nhật vân tay
#: và chạy lại G-KB (spec §4F). Không sửa vân tay mà không tăng phiên bản.
SCHEMA_SHA256 = "cd7f18ff2ced3fb41ed692726320813be96e476bba4df34663c4422e4d8e48b4"

FULL: dict = {
    "contract_version": "brain-out/1",
    "thread_ref": "g:6f1c7e2a-0000-4000-8000-000000000001",
    "units": [{
        "unit_ref": "u1", "event_ref": "e1", "kind": "nghia_vu_loi_hua",
        "quotes": [{"message_id": "m_123", "field": "body", "text": "thứ 6 em giao 200 bao"}],
        "people": [{"surface": "chị Tư Đồng Nai", "person_ref": "u_9", "role": "nguoi_hua"}],
        "summary": "Chị Tư hứa giao 200 bao thứ 6 · chạm hợp đồng của Sếp · tiếng Việt",
        "flags": {"public": False, "nudged": False, "repeat_n": 2, "explicit_urgent": False, "needs_owner": True,
                  "blocks_work": False, "is_template": False, "conditional": False, "said_will_leave": False,
                  "money_request": False, "credentials_entered": False, "account_takeover": False, "emergency": False, "ongoing": True},
        "ongoing_message_ids": ["m_123"], "event_time": {"text": "hôm qua", "anchor_message_id": "m_123"},
        "status_message_id": "m_123",
        "harm": {"what": "trễ hàng 1 tuần", "amount_text": "50 triệu", "horizon_text": "trong tuần", "irreversible": False},
        "delivery_form_hint": "direct", "txn": {"state_hint": "requested", "message_id": "m_123"},
        "normalization_notes": [],
        "deadline": {"text": "thứ 6", "anchor_message_id": "m_123", "lunar": False, "ambiguous": False},
        "amount_text": "200 bao",
        "condition": {"text": None, "met": "unknown", "met_message_id": None},
        "status_hint": "dang_mo",
        "evidence": {"sources_n": 1, "source_rating": "B2 — nhân viên nhóm", "conflicts": [{"fact_id": None, "text": "chưa rõ"}],
                     "modality": "text"},
        "owner_relevance": [{"fact_id": "f_77", "how": "goal", "note": "mở quán thứ 2"}],
        "owner_basis": "co_ho_so", "gate_candidate": "loi_hua_khach_sap_vo", "level_hint": "amber",
        "competing_hypothesis": "có thể đã giao ngoài nhóm",
        "action": {"who": "owner", "what": "nhắc chị Tư", "by_when_text": "trước thứ 6", "decision_kind": "outbound", "draft": "Chị Tư ơi…"},
    }],
    "facts_proposed": [{"unit_ref": "u1", "subject": {"surface": "chị Tư Đồng Nai", "person_ref": None}, "predicate": "promised_to",
                        "object": {"surface": None, "person_ref": None, "value": {"text": "200 bao"}},
                        "valid_from_text": "thứ 6", "quote_refs": ["m_123"], "source_hint": "insider"}],
    "memory_updates": [{"type": "alias", "text": "chị Tư = Tư Đồng Nai", "message_ids": ["m_123"]}],
    "unreadable": {"voice": 1, "image": 0, "file": 0},
    "owner_question": "Chị Tư Đồng Nai là khách quen của anh đúng không?",
}


def _bad(mut) -> None:  # type: ignore[no-untyped-def]
    d = copy.deepcopy(FULL)
    mut(d)
    with pytest.raises(ValidationError):
        bc.BrainOut.model_validate(d)


def test_brain_contract_full_example_roundtrips_exactly() -> None:
    out = bc.BrainOut.model_validate(FULL)
    assert out.contract_version == "brain-out/1" and bc.CONTRACT_VERSION == "brain-out/2"     # /1 vẫn parse được bằng schema /2
    back = json.loads(out.model_dump_json())
    v2_defaults = {"complaint_stage": None, "stage_message_id": None, "asks_for_owner": False, "asks_message_id": None, "public_venue": None,
                   "need": None, "fixed_occasion": False, "occasion_message_id": None, "nudge_message_id": None, "owner_action": None}
    assert back == {**FULL, "units": [{**FULL["units"][0], **v2_defaults}]}      # khuôn §4F + §4F.2: chỉ thêm khoá mặc định, không bớt
    assert bc.validate_json(json.dumps(FULL)).units[0].kind == "nghia_vu_loi_hua"


def test_brain_contract_unit_keys_and_flags_exactly_as_spec() -> None:
    assert set(bc.Unit.model_fields) == {"unit_ref", "event_ref", "kind", "quotes", "people", "summary", "flags", "harm", "deadline",
                                         "amount_text", "condition", "status_hint", "evidence", "owner_relevance", "owner_basis",
                                         "gate_candidate", "level_hint", "competing_hypothesis", "action", "status_message_id",
                                         "ongoing_message_ids", "event_time", "delivery_form_hint", "txn",
                                         "normalization_notes",
                                         # brain-out/2 (§4F.2)
                                         "complaint_stage", "stage_message_id", "asks_for_owner", "asks_message_id", "public_venue", "need",
                                         "fixed_occasion", "occasion_message_id", "nudge_message_id", "owner_action"}
    assert list(bc.Flags.model_fields) == ["public", "nudged", "repeat_n", "explicit_urgent", "needs_owner", "blocks_work",
                                           "is_template", "conditional", "said_will_leave", "money_request", "credentials_entered",
                                           "account_takeover", "emergency", "ongoing"]
    assert set(bc.BrainOut.model_fields) == {"contract_version", "thread_ref", "units", "facts_proposed", "memory_updates",
                                             "unreadable", "owner_question"}
    # model KHÔNG trả mức cuối / độ chắc / ngày tính sẵn / tiền tính sẵn (phân vai §4F)
    for banned in ("severity", "severity_basis", "level", "confidence", "due_at", "value_vnd", "value_basis", "computed"):
        assert banned not in bc.Unit.model_fields and banned not in str(bc.Deadline.model_fields)


def test_brain_contract_rejects_schema_violations() -> None:
    _bad(lambda d: d.update(extra_field=1))                                           # khoá thừa ở gốc
    _bad(lambda d: d["units"][0].update(severity="red"))                              # model không được trả mức cuối
    _bad(lambda d: d["units"][0].update(kind="people"))                               # kind r4 không còn
    _bad(lambda d: d["units"][0].update(kind="khac"))
    _bad(lambda d: d["units"][0]["quotes"][0].update(text="x" * 121))                 # trích dẫn ≤ 120 ký tự
    _bad(lambda d: d["units"][0]["quotes"][0].update(text=""))
    _bad(lambda d: d["units"][0]["quotes"][0].update(field="subject"))
    _bad(lambda d: d["units"][0]["quotes"][0].update(message_id="123"))               # message_id phải dạng m_…
    _bad(lambda d: d["units"][0].update(quotes=[]))                                   # đơn vị phải có trích dẫn
    _bad(lambda d: d["units"][0].pop("summary"))
    _bad(lambda d: d["units"][0]["flags"].update(unknown_flag=True))
    _bad(lambda d: d["units"][0]["flags"].update(repeat_n=-1))
    _bad(lambda d: d["units"][0]["action"].update(decision_kind="sep_quyet_manh"))    # v2 cũ ⇒ phải là console
    _bad(lambda d: d["units"][0]["action"].update(who="khach"))
    _bad(lambda d: d["units"][0].update(gate_candidate="co_hoi_nong"))
    _bad(lambda d: d["units"][0].update(level_hint="orange"))
    _bad(lambda d: d["units"][0]["event_time"].update(anchor_message_id="123"))
    _bad(lambda d: d["units"][0]["event_time"].update(extra="x"))
    _bad(lambda d: d["units"][0].update(delivery_form_hint="forward"))
    _bad(lambda d: d["units"][0].update(txn={"state_hint": "done", "message_id": None}))
    _bad(lambda d: d["units"][0]["harm"].update(irreversible="maybe"))
    _bad(lambda d: d["units"][0].update(status_hint="xong"))
    _bad(lambda d: d["units"][0]["evidence"].update(modality="video"))
    _bad(lambda d: d["units"][0]["owner_relevance"][0].update(fact_id="77"))          # phải dạng f_…
    _bad(lambda d: d["units"][0]["owner_relevance"][0].update(how="mood"))
    _bad(lambda d: d["units"][0]["people"][0].update(person_ref="9"))
    _bad(lambda d: d["units"][0]["people"][0].update(role="boss"))
    _bad(lambda d: d.update(thread_ref="group:1"))                                    # thread_key: g:<id> | dm:<…>:<…>
    _bad(lambda d: d.update(thread_ref="dm:onlyone"))
    _bad(lambda d: d.update(contract_version="brain-out/3"))
    _bad(lambda d: d["facts_proposed"][0].update(quote_refs=[]))
    _bad(lambda d: d["facts_proposed"][0].update(source_hint="owner"))
    _bad(lambda d: d["memory_updates"][0].update(type="forget"))
    _bad(lambda d: d["unreadable"].update(voice=-1))
    _bad(lambda d: d.update(owner_question="x" * 401))
    with pytest.raises(ValidationError):
        bc.validate_json("không phải json")
    with pytest.raises(ValidationError):
        bc.validate_json('{"thread_ref": "g:1", "units": "x"}')


def test_brain_contract_accepts_nullable_and_minimal_forms() -> None:
    for thread in ("g:abc", "dm:p1:acc1"):
        out = bc.BrainOut.model_validate({"thread_ref": thread})
        assert out.units == [] and out.owner_question is None and out.unreadable.voice == 0
    d = copy.deepcopy(FULL)
    d["units"][0].update(kind=None, gate_candidate=None, level_hint=None, harm=None, action=None)   # kind null ⇒ chỉ ghi sự thật
    out = bc.BrainOut.model_validate(d)
    assert out.units[0].kind is None
    d["units"][0]["action"] = {"who": "u_55", "what": "x", "decision_kind": "self"}
    assert bc.BrainOut.model_validate(d).units[0].action.who == "u_55"          # type: ignore[union-attr]
    for dk in bc.DECISION_KINDS:
        d["units"][0]["action"]["decision_kind"] = dk
        bc.BrainOut.model_validate(d)


def test_brain_contract_enums_match_spec_lists() -> None:
    assert bc.KINDS == ("nghia_vu_loi_hua", "viec_han", "cau_hoi_cho_xu_ly", "su_kien_dip", "suc_khoe_an_toan", "xung_dot_khieu_nai",
                        "quan_he_nong_nguoi", "co_hoi_ket_noi", "rui_ro_lua_dao", "thi_truong_thong_tin")
    assert bc.GATES == contexts.GATES                                           # 6 cổng INT-19: một danh sách ở hai nơi phải khớp
    assert bc.DECISION_KINDS == ("self", "internal", "outbound", "console")
    # tập giá trị trong schema Pydantic = tập hằng (không lệch)
    ann = bc.Unit.model_json_schema()["properties"]
    kinds = {x for opt in ann["kind"]["anyOf"] for x in opt.get("enum", [])}
    assert kinds == set(bc.KINDS)
    gates = {x for opt in ann["gate_candidate"]["anyOf"] for x in opt.get("enum", [])}
    assert gates == set(bc.GATES)
    assert set(bc.Action.model_json_schema()["properties"]["decision_kind"]["enum"]) == set(bc.DECISION_KINDS)
    assert set(bc.Quote.model_json_schema()["properties"]["field"]["enum"]) == set(bc.QUOTE_FIELDS)
    assert set(bc.Evidence.model_json_schema()["properties"]["modality"]["enum"]) == set(bc.MODALITIES)


async def test_brain_contract_kinds_match_signal_kinds_table(fresh_db, db) -> None:  # type: ignore[no-untyped-def]
    rows = (await db.execute(text("SELECT code FROM biz.signal_kinds WHERE on_intel"))).scalars().all()
    assert set(rows) == set(bc.KINDS)


async def test_brain_contract_gate_candidate_matches_meaning_units_check(fresh_db, db) -> None:  # type: ignore[no-untyped-def]
    """Cột clean.meaning_units.gate_candidate / flags / level_hint nhận đúng giá trị của hợp đồng."""
    org = (await db.execute(text("INSERT INTO core.organizations (name) VALUES ('o') RETURNING id"))).scalar_one()
    for g in bc.GATES:
        await db.execute(text("""INSERT INTO clean.meaning_units (org_id, observed_at, event_type, conclusion, confidence, run_id,
                                    gate_candidate, level_hint, kind_hint, flags)
                                 VALUES (:o, now(), 'x', 'c', 0.9, core.uuid_v7(), :g, 'amber', 'viec_han', CAST(:f AS jsonb))"""),
                         {"o": org, "g": g, "f": json.dumps(FULL["units"][0]["flags"])})
    with pytest.raises(Exception, match="gate_candidate_chk"):
        async with db.begin_nested():
            await db.execute(text("""INSERT INTO clean.meaning_units (org_id, observed_at, event_type, conclusion, confidence, run_id,
                                        gate_candidate) VALUES (:o, now(), 'x', 'c', 0.9, core.uuid_v7(), 'khong_co')"""), {"o": org})
    with pytest.raises(Exception, match="foreign key|kind_hint"):
        async with db.begin_nested():
            await db.execute(text("""INSERT INTO clean.meaning_units (org_id, observed_at, event_type, conclusion, confidence, run_id,
                                        kind_hint) VALUES (:o, now(), 'x', 'c', 0.9, core.uuid_v7(), 'people')"""), {"o": org})


def test_brain_contract_json_schema_is_stable_and_complete() -> None:
    s = bc.json_schema()
    assert s["$id"] == "urn:genos:brain-out/2" and s["title"] == "BrainOut"
    assert s["properties"]["contract_version"]["enum"] == ["brain-out/1", "brain-out/2"]
    assert s["additionalProperties"] is False and "thread_ref" in s["required"]
    assert {"Unit", "Quote", "Flags", "FactProposed", "Action"} <= set(s["$defs"])
    assert s["$defs"]["Quote"]["properties"]["text"]["maxLength"] == 120
    canon = json.dumps(s, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    assert hashlib.sha256(canon.encode()).hexdigest() == SCHEMA_SHA256, (
        "Hợp đồng brain-out đã đổi: tăng CONTRACT_VERSION + chạy lại G-KB rồi mới cập nhật vân tay (spec §4F)")
    json.dumps(s)                                                                # xuất được ra JSON


def test_brain_contract_stub_is_valid_for_l2() -> None:
    out = bc.stub("dm:p1:a1")
    assert bc.BrainOut.model_validate_json(out.model_dump_json()) == out
    assert out.units[0].kind is None and out.thread_ref == "dm:p1:a1"
    with pytest.raises(ValidationError):
        bc.stub("không hợp lệ")


def test_brain_contract_r63_delta_defaults_and_rules() -> None:
    d = copy.deepcopy(FULL)
    u = d["units"][0]
    u["flags"]["ongoing"] = False
    u["ongoing_message_ids"] = []
    u["event_time"] = None
    u["status_message_id"] = None
    out = bc.BrainOut.model_validate(d).units[0]
    assert out.flags.ongoing is False and out.ongoing_message_ids == [] and out.event_time is None
    bare = bc.stub("g:x").units[0]                                   # mọi trường mới có mặc định
    assert (bare.flags.ongoing, bare.ongoing_message_ids, bare.event_time, bare.status_message_id) == (False, [], None, None)
    # ongoing_message_ids có id nhưng cờ tắt: hợp lệ (code đặt ongoing theo bằng chứng)
    u["ongoing_message_ids"] = ["m_123"]
    assert bc.BrainOut.model_validate(d).units[0].ongoing_message_ids == ["m_123"]
    for hint in ("red", "amber", "grey", "off", None):
        u["level_hint"] = hint
        bc.BrainOut.model_validate(d)


def test_brain_contract_event_time_text_must_be_substring_of_anchor() -> None:
    unit = bc.BrainOut.model_validate(FULL).units[0]
    assert bc.check_event_time_text(unit, {"m_123": "hôm qua em đã gửi rồi"})
    assert not bc.check_event_time_text(unit, {"m_123": "tuần trước em gửi"})
    assert not bc.check_event_time_text(unit, {})                    # không có tin anchor
    none = unit.model_copy(update={"event_time": None})
    assert bc.check_event_time_text(none, {})
    no_text = unit.model_copy(update={"event_time": bc.EventTime(text=None, anchor_message_id=None)})
    assert bc.check_event_time_text(no_text, {})
    assert not bc.check_event_time_text(unit.model_copy(update={"event_time": bc.EventTime(text="hôm qua", anchor_message_id=None)}), {})


def test_brain_contract_r64_delta_defaults_roles_and_txn() -> None:
    d = copy.deepcopy(FULL)
    u = d["units"][0]
    u["people"][0]["role"] = "nguoi_nhan_tien"
    u["txn"] = {"state_hint": None, "message_id": None}
    u["delivery_form_hint"] = "warning_story"
    u["harm"]["irreversible"] = True
    out = bc.BrainOut.model_validate(d).units[0]
    assert out.people[0].role == "nguoi_nhan_tien" and out.delivery_form_hint == "warning_story" and out.harm.irreversible  # type: ignore[union-attr]
    for st in ("requested", "transferred", None):
        u["txn"] = {"state_hint": st, "message_id": "m_123" if st else None}
        bc.BrainOut.model_validate(d)
    u["txn"] = None
    bare = bc.stub("g:x").units[0]
    assert bare.delivery_form_hint == "direct" and bare.txn is None and bare.harm is None
    assert bc.Harm(what="x").irreversible is False
    assert "nguoi_nhan_tien" in bc.ROLES


def test_brain_contract_normalizes_message_refs_instead_of_failing_the_turn() -> None:
    """Review L1 🟠6: id ngoài `quotes` bị lọc/đặt None/ép false và GHI vào normalization_notes; không raise."""
    d = copy.deepcopy(FULL)
    u = d["units"][0]
    u["ongoing_message_ids"] = ["m_999", "m_123"]
    u["status_message_id"] = "m_888"
    u["txn"] = {"state_hint": "transferred", "message_id": "m_777"}
    out = bc.BrainOut.model_validate(d).units[0]
    assert out.ongoing_message_ids == ["m_123"] and out.flags.ongoing is True            # còn id hợp lệ ⇒ giữ ongoing
    assert out.status_message_id is None and out.txn is not None and out.txn.message_id is None
    assert out.txn.state_hint == "transferred"                                            # chỉ id bị bỏ, gợi ý giữ nguyên
    assert out.normalization_notes == ["ongoing_message_id_dropped:m_999", "status_message_id_dropped:m_888",
                                       "txn_message_id_dropped:m_777"]
    u["ongoing_message_ids"] = ["m_999"]
    out2 = bc.BrainOut.model_validate(d).units[0]
    assert out2.ongoing_message_ids == [] and out2.flags.ongoing is False                # rỗng ⇒ ép false
    assert "ongoing_forced_false" in out2.normalization_notes
    u["ongoing_message_ids"] = []                                                         # ongoing=true mà không có id
    assert bc.BrainOut.model_validate(d).units[0].normalization_notes[-1] == "ongoing_forced_false"
    # đầu ra sạch ⇒ không có ghi chú; sai schema cơ bản vẫn raise
    assert bc.BrainOut.model_validate(FULL).units[0].normalization_notes == []
    _bad(lambda x: x["units"][0].update(txn={"state_hint": "done", "message_id": None}))
    _bad(lambda x: x["units"][0].update(normalization_notes="x"))
