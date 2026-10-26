# ruff: noqa: E501
"""v0.1.77 (gói P3a) — `brain-out/2` (spec r7.2 §4F.2): parse /1 bằng schema /2, chuẩn hoá, `verify` trường mới, prompt v2.2, `_history_evidence`."""

import copy
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import orjson
import pytest
from pydantic import ValidationError

from gh.refinery import brain_contract as bc
from gh.refinery import extract, runner, verify

TZ = ZoneInfo("Asia/Ho_Chi_Minh")
NOW = datetime(2026, 10, 26, 10, 30, tzinfo=TZ)
THREAD = "g:6f1c7e2a-0000-4000-8000-000000000001"
CUS, STAFF, OWN = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()


def ev(mid: str, body: str, role: str | None, sender: uuid.UUID, mins: int = 0) -> dict:
    d = {"message_id": mid, "sent_at": NOW - timedelta(minutes=mins), "body": body, "sender_person_id": sender, "forwarded": False,
         "raw_event_id": uuid.uuid4()}
    if role:
        d["sender_role"] = role
    return d


EVENTS = [
    ev("m_1", "Mai không lắp xong thì anh huỷ, bên Hoà Phát báo rẻ hơn rồi", "customer", CUS, 60),
    ev("m_2", "Cho chị nói chuyện với anh chủ nha", "customer", CUS, 50),
    ev("m_3", "Dạ em đang lo vụ này cho anh ạ", "staff", STAFF, 40),
    ev("m_4", "Cần gấp 50 tấn thép phi 16 giao tại Bình Tân tuần sau", "customer", CUS, 30),
    ev("m_5", "Khai trương chi nhánh mới thứ 7 này, hẹn giao đủ 20 bộ bàn ghế nhé", "customer", CUS, 20),
    ev("m_6", "Anh ơi 2h rồi sao chưa thấy ai", "customer", CUS, 10),
    ev("m_7", "Em nhắc lại cho anh Sếp nhé", "staff", STAFF, 5),
    ev("m_8", "Cần 50 tấn thép", None, CUS, 4),          # không có vai
]


def q(mid: str, text: str) -> dict:
    return {"message_id": mid, "field": "body", "text": text}


def unit(quotes: list[dict] | None = None, **kw) -> dict:  # type: ignore[no-untyped-def]
    d = {"unit_ref": "u1", "event_ref": "e1", "kind": "xung_dot_khieu_nai", "summary": "s",
         "quotes": quotes or [q("m_1", "thì anh huỷ, bên Hoà Phát báo rẻ hơn rồi")]}
    d.update(kw)
    return d


def out(*units: dict) -> bc.BrainOut:
    return bc.BrainOut.model_validate({"contract_version": "brain-out/2", "thread_ref": THREAD, "units": list(units)})


def chk(o: bc.BrainOut):  # type: ignore[no-untyped-def]
    return verify.check(o, EVENTS, None, now=NOW)


def u0(v):  # type: ignore[no-untyped-def]
    return v.units[0]


# ─── hợp đồng ──────────────────────────────────────────────────────────────────────────────────────────────────────────────
def test_v1_output_parses_with_v2_schema_and_gets_defaults() -> None:
    raw = {"contract_version": "brain-out/1", "thread_ref": THREAD, "units": [unit(status_hint="da_xu_ly", status_message_id="m_1")]}
    o = bc.validate_json(orjson.dumps(raw))
    assert o.contract_version == "brain-out/1"
    u = o.units[0]
    assert (u.complaint_stage, u.stage_message_id, u.asks_for_owner, u.asks_message_id, u.public_venue, u.need, u.fixed_occasion,
            u.occasion_message_id, u.nudge_message_id, u.owner_action) == (None, None, False, None, None, None, False, None, None, None)
    assert bc.BrainOut.model_validate({"thread_ref": THREAD}).contract_version == "brain-out/2"      # mặc định = /2


def test_v2_enums_and_extra_forbid() -> None:
    for stage in bc.COMPLAINT_STAGES:
        assert out(unit(complaint_stage=stage, stage_message_id="m_1")).units[0].complaint_stage == stage
    assert out(unit(status_hint="dang_xu_ly", status_message_id="m_1")).units[0].status_hint == "dang_xu_ly"
    for bad in ({"complaint_stage": "angry"}, {"public_venue": "tivi"}, {"zzz": 1}, {"need": {"what": {"text": "x", "message_id": "m_1"}, "why": None}},
                {"need": {"what": {"text": "x", "message_id": "1"}}}):
        with pytest.raises(ValidationError):
            out(unit(**bad))
    with pytest.raises(ValidationError):
        bc.BrainOut.model_validate({"contract_version": "brain-out/3", "thread_ref": THREAD})


def test_normalize_public_without_venue_becomes_false() -> None:
    u = out(unit(flags={"public": True})).units[0]
    assert u.flags.public is False and "public_without_venue" in u.normalization_notes
    u2 = out(unit(flags={"public": True}, public_venue="social")).units[0]
    assert u2.flags.public is True and u2.public_venue == "social" and "public_without_venue" not in u2.normalization_notes


def test_normalize_message_ids_and_missing_ids() -> None:
    u = out(unit(complaint_stage="ultimatum")).units[0]                                 # thiếu stage_message_id
    assert u.complaint_stage is None and "complaint_stage_no_message" in u.normalization_notes
    u = out(unit(complaint_stage="ultimatum", stage_message_id="m_999")).units[0]       # id ngoài quotes
    assert u.complaint_stage is None and u.stage_message_id is None
    assert any(n.startswith("stage_message_id_dropped:m_999") for n in u.normalization_notes)
    u = out(unit(asks_for_owner=True)).units[0]
    assert u.asks_for_owner is False and "asks_for_owner_no_message" in u.normalization_notes
    u = out(unit(fixed_occasion=True, occasion_message_id="m_999", nudge_message_id="m_999")).units[0]
    assert u.fixed_occasion is False and u.occasion_message_id is None and u.nudge_message_id is None
    u = out(unit(need={"what": {"text": "thép", "message_id": "m_1"}, "qty": {"text": "50 tấn", "message_id": "m_999"}})).units[0]
    assert u.need is not None and u.need.what is not None and u.need.qty is None and "need_qty_message_id_dropped:m_999" in u.normalization_notes
    long = " ".join(["gọi"] * 15)
    u = out(unit(owner_action=long)).units[0]
    assert u.owner_action is None and "owner_action_too_long" in u.normalization_notes
    assert out(unit(owner_action=" ".join(["gọi"] * 14))).units[0].owner_action is not None
    # ghi chú do model gửi bị bỏ (chỉ code ghi)
    assert out(unit(normalization_notes=["tự bịa"])).units[0].normalization_notes == []


# ─── verify ───────────────────────────────────────────────────────────────────────────────────────────────────────────────
def test_verify_stage_asks_nudge_need_customer_sender() -> None:
    v = chk(out(unit(complaint_stage="ultimatum", stage_message_id="m_1")))
    assert u0(v).unit.complaint_stage == "ultimatum" and u0(v).unit.stage_message_id == "m_1"
    # tin của NV ⇒ trường của khách về mặc định
    qs = [q("m_1", "thì anh huỷ, bên Hoà Phát báo rẻ hơn rồi"), q("m_7", "Em nhắc lại cho anh Sếp nhé")]
    v = chk(out(unit(qs, complaint_stage="ultimatum", stage_message_id="m_7", asks_for_owner=True, asks_message_id="m_7", nudge_message_id="m_7")))
    got = u0(v).unit
    assert (got.complaint_stage, got.stage_message_id, got.asks_for_owner, got.asks_message_id, got.nudge_message_id) == (None, None, False, None, None)
    assert {"stage_message_sender_not_customer", "asks_message_sender_not_customer", "nudge_message_sender_not_customer"} <= set(u0(v).notes)
    assert v.counters["asks_message_sender_not_customer"] == 1
    # đúng khách
    qs = [q("m_2", "Cho chị nói chuyện với anh chủ nha"), q("m_6", "Anh ơi 2h rồi sao chưa thấy ai")]
    got = u0(chk(out(unit(qs, asks_for_owner=True, asks_message_id="m_2", nudge_message_id="m_6")))).unit
    assert got.asks_for_owner is True and got.nudge_message_id == "m_6"


def test_verify_unverified_quote_unit_absent_and_role_unknown_kept_with_note() -> None:
    v = chk(out(unit([q("m_1", "câu bịa không có trong tin")], complaint_stage="voice", stage_message_id="m_1")))
    assert u0(v).status == "absent"
    qs = [q("m_8", "Cần 50 tấn thép")]
    v = chk(out(unit(qs, complaint_stage="voice", stage_message_id="m_8")))
    assert u0(v).unit.complaint_stage == "voice" and "stage_message_role_unchecked" in u0(v).notes


def test_verify_status_dang_xu_ly() -> None:
    qs = [q("m_1", "thì anh huỷ, bên Hoà Phát báo rẻ hơn rồi"), q("m_3", "Dạ em đang lo vụ này cho anh ạ")]
    v = chk(out(unit(qs, status_hint="dang_xu_ly", status_message_id="m_3")))
    assert u0(v).unit.status_hint == "dang_xu_ly" and u0(v).status_by_role == "staff" and u0(v).status_by_person_id == STAFF
    # thiếu mã tin ⇒ chuẩn hoá bỏ id nhưng hint còn ⇒ verify đưa về dang_mo
    v = chk(out(unit(status_hint="dang_xu_ly")))
    assert u0(v).unit.status_hint == "dang_mo" and "dang_xu_ly_unverified" in u0(v).notes


def test_verify_need_counts_only_verified_cells() -> None:
    qs = [q("m_4", "Cần gấp 50 tấn thép phi 16 giao tại Bình Tân tuần sau")]
    need = {"what": {"text": "thép phi 16", "message_id": "m_4"}, "qty": {"text": "50 tấn", "message_id": "m_4"},
            "when": {"text": "tuần sau", "message_id": "m_4"}, "who": {"text": "anh Hùng", "message_id": "m_4"}}     # "anh Hùng" không có trong tin
    v = chk(out(unit(qs, kind="co_hoi_ket_noi", need=need)))
    assert u0(v).need_fields_n == 3 and u0(v).unit.need.who is None and u0(v).unit.need.what is not None      # type: ignore[union-attr]
    assert "need_who_unverified" in u0(v).notes and v.counters["need_cell_unverified"] == 1
    assert chk(out(unit(qs, kind="co_hoi_ket_noi", need={}))).units[0].need_fields_n == 0
    assert u0(chk(out(unit(qs, kind="co_hoi_ket_noi")))).need_fields_n is None                                   # không khai need
    # ô dẫn tới tin ngoài quotes bị chuẩn hoá bỏ trước verify
    n = u0(chk(out(unit(qs, kind="co_hoi_ket_noi", need={"what": {"text": "thép", "message_id": "m_1"}})))).need_fields_n
    assert n == 0


def test_verify_fixed_occasion_keyword() -> None:
    qs = [q("m_5", "Khai trương chi nhánh mới thứ 7 này")]
    v = chk(out(unit(qs, kind="nghia_vu_loi_hua", fixed_occasion=True, occasion_message_id="m_5")))
    assert u0(v).unit.fixed_occasion is True
    qs = [q("m_6", "Anh ơi 2h rồi sao chưa thấy ai")]
    v = chk(out(unit(qs, kind="nghia_vu_loi_hua", fixed_occasion=True, occasion_message_id="m_6")))
    assert u0(v).unit.fixed_occasion is False and u0(v).unit.occasion_message_id is None and "fixed_occasion_unverified" in u0(v).notes


@pytest.mark.parametrize(("action", "kept"), [
    ("Gọi anh Tâm trước 17h: anh doạ huỷ nếu mai chưa lắp xong", True),
    ("Giao NV Hoa báo giá anh Khoa", False),                                          # khách chưa hỏi giá
    ("Mời mua thêm combo bàn ghế", False),
    ("Chào hàng gói bảo hành mở rộng", False),
    ("Chốt đơn ngay hôm nay", False),
    ("Gọi anh Tâm: bên Hoà Phát báo rẻ hơn rồi", False),                              # chép lời khách
])
def test_verify_owner_action(action: str, kept: bool) -> None:
    got = u0(chk(out(unit(owner_action=action)))).unit.owner_action
    assert (got == action) is kept and (got is None) is (not kept)


def test_verify_owner_action_quote_price_allowed_when_asked() -> None:
    qs = [q("m_1", "thì anh huỷ, bên Hoà Phát báo rẻ hơn rồi")]
    ev2 = [*EVENTS, ev("m_9", "Báo giá giùm anh 20 máy lạnh 1HP", "customer", CUS, 3)]
    o = out(unit([*qs, q("m_9", "Báo giá giùm anh 20 máy lạnh 1HP")], owner_action="Giao NV Hoa báo giá anh Khoa"))
    assert verify.check(o, ev2, None, now=NOW).units[0].unit.owner_action == "Giao NV Hoa báo giá anh Khoa"


def test_verify_still_masks_cards_and_uuid() -> None:
    """Giữ nguyên hành vi che thẻ/UUID."""
    t = "thẻ 4111 1111 1111 1111 nha"
    assert verify.mask_credentials(t) == "thẻ [thẻ …1111] nha"
    u = "id 01a17668-4746-7979-8123-456789012345 ok"
    assert verify.mask_credentials(u) == u


# ─── prompt v2.2 ──────────────────────────────────────────────────────────────────────────────────────────────────────────
def test_prompt_v22_teaches_new_fields_and_is_default() -> None:
    sk = extract.skill_prompt()
    assert extract.PROMPT_PATH.name == "intel_v2_2.md" and f"extractor_version: {extract.EXTRACTOR_VERSION} ·" in sk and "brain-out/2" in sk
    for name in ("complaint_stage", "stage_message_id", "asks_for_owner", "asks_message_id", "public_venue", "need", "fixed_occasion",
                 "occasion_message_id", "nudge_message_id", "owner_action", "dang_xu_ly", "role"):
        assert name in sk, name
    for stage in bc.COMPLAINT_STAGES:
        assert stage in sk
    low = sk.lower()
    for banned in ("lead", "ngân sách", "thẩm quyền mua"):
        assert banned not in low, banned
    assert "không** coi \"doạ bỏ\" một mình là mức cao nhất" in sk


def test_event_line_renders_role_next_to_from() -> None:
    e = extract.GroupEvent(message_id="m_1", sent_at=NOW, body="hi", sender_name="Anh Tâm", sender_role="customer")
    d = orjson.loads(extract.event_line(e, {}, "Asia/Ho_Chi_Minh", ()))
    assert d["from"] == "Anh Tâm" and d["role"] == "khach"
    assert "role" not in orjson.loads(extract.event_line(extract.GroupEvent(message_id="m_2", sent_at=NOW, body="x"), {}, "Asia/Ho_Chi_Minh", ()))


def test_schema_example_in_prompt_parses_default_fields() -> None:
    d = copy.deepcopy({"thread_ref": THREAD, "units": [unit(complaint_stage="exit_pending", stage_message_id="m_1", public_venue="authority",
                                                          flags={"public": True})]})
    u = bc.BrainOut.model_validate(d).units[0]
    assert u.flags.public is True and u.complaint_stage == "exit_pending"


# ─── runner._history_evidence (P5 → P3a) ──────────────────────────────────────────────────────────────────────────────────
def _item(kind: str, evidence: list[tuple[uuid.UUID, datetime]]) -> SimpleNamespace:
    return SimpleNamespace(meta={"kind_hint": kind, "evidence": evidence})


def test_history_evidence_prefers_promise_units_before_cap() -> None:
    t = datetime(2026, 10, 1, tzinfo=UTC)
    ids = [uuid.uuid4() for _ in range(14)]
    promise_old = _item("nghia_vu_loi_hua", [(ids[0], t), (ids[1], t)])          # cũ nhất (đứng đầu danh sách, vì đọc theo thời gian)
    deadline = _item("viec_han", [(ids[2], t)])
    others = [_item("xung_dot_khieu_nai", [(ids[i], t)]) for i in range(3, 14)]    # 11 tin khác, mới hơn
    history = [promise_old, deadline, *others]
    want = runner.pick_history_evidence(history, set())
    assert len(want) == runner.HISTORY_EVIDENCE_MAX == 10
    assert {ids[0], ids[1], ids[2]} <= set(want)                                   # lời hứa/việc hạn luôn lọt, dù cũ
    # các tin còn lại: mới nhất trước
    assert set(want) - {ids[0], ids[1], ids[2]} == set(ids[7:14])
    # `have` loại tin đã có; kind khác không đổi hành vi cũ khi không có lời hứa
    want2 = runner.pick_history_evidence(others, {ids[13]})
    assert ids[13] not in want2 and len(want2) == 10 and ids[3] in want2
