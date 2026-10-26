# ruff: noqa: E501
"""v0.1.77 (gói P3b) — detector còn lại + nối trường r7 vào `SignalIn` (spec r7.2 §4.2.1 (c′) cột Nguồn/Code kiểm).

Kiểm việc ĐIỀN trường vào đầu vào (`level_input`), KHÔNG kiểm mức ra (luật r7 ở `levels.py` là việc của gói P1):
`promises` (`deadline_granularity`, `nudged_by_customer`, `fixed_occasion`, P-T2, NV hẹn giờ ⇒ lời hứa), `questions` (chờ trong `work_hours`),
`catalog_match` (`need_fields_n`, `explicit_urgent`), `units` (`public_eff` cho đường bảng, tên khoá `g3_fields`)."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from types import SimpleNamespace
from typing import Any

import orjson
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gh.biz.intel import levels, signals
from gh.biz.intel.detectors import catalog_match, promises, questions
from gh.biz.intel.detectors import units as U
from gh.biz.mem import facts
from tests.mem_helpers import mk_org, mkfact
from tests.test_intel_catalog_v0176 import _alerts as _cat_alerts
from tests.test_intel_catalog_v0176 import _demand, _setup
from tests.test_intel_units_t12_v0176 import _customer_of, _person, _rows, _staff_of, _unit, _world
from tests.test_owner_profile_support import mk_raw

VN = levels.VN
NOW = datetime(2026, 10, 23, 14, 0, tzinfo=VN)          # T6 14:00 giờ VN


# ─── promises: hàm thuần ─────────────────────────────────────────────────────

def test_p3b_granularity_prefers_stored_then_recomputes_only_when_due_matches() -> None:
    anchor = datetime(2026, 10, 23, 10, 0, tzinfo=VN)
    due_day = datetime(2026, 10, 24, 23, 59, 59, tzinfo=VN)
    assert promises.deadline_granularity({"deadline_granularity": "hour"}, "mai giao", anchor, due_day) == "hour"      # giá trị đã lưu thắng
    assert promises.deadline_granularity({"deadline_granularity": "bogus"}, "mai em giao hàng", anchor, due_day) == "day"   # giá trị lạ ⇒ tính lại
    assert promises.deadline_granularity({}, "mai em giao hàng cho chị", anchor, due_day) == "day"
    assert promises.deadline_granularity({}, "chiều mai giao", anchor, due_day) is None            # hạn tính lại (18:00) khác hạn thật ⇒ không đoán
    assert promises.deadline_granularity({}, "mai em giao", anchor, None) is None                  # không hạn ⇒ không độ mịn
    assert promises.deadline_granularity({}, "em giao hàng", anchor, due_day) is None


def test_p3b_level_input_carries_granularity_and_promise_fields() -> None:
    due = NOW + timedelta(days=1)
    c = promises.PromiseCtx(kind="nghia_vu_loi_hua", text="mai giao hàng", valid_from=NOW, due_at=due, to_customer=True, granularity="day",
                            nudged_by_customer=True, fixed_occasion=True)
    li = promises.level_input(c, NOW)
    assert li.deadline_granularity == "day" and li.promise.nudged_by_customer is True and li.promise.fixed_occasion is True
    assert li.gate_candidate == levels.G4
    c2 = promises.PromiseCtx(kind="nghia_vu_loi_hua", text="x", valid_from=NOW, due_at=due, to_customer=True, granularity="day", condition_met="no")
    li2 = promises.level_input(c2, NOW)                                  # điều kiện chưa đạt ⇒ không có hạn ⇒ không độ mịn
    assert li2.deadline_at is None and li2.deadline_granularity is None
    plain = promises.level_input(promises.PromiseCtx(kind="viec_han", text="x", valid_from=NOW), NOW)
    assert plain.promise.nudged_by_customer is False and plain.promise.fixed_occasion is False and plain.deadline_granularity is None


def test_p3b_fixed_occasion_needs_model_flag_and_keyword_in_verified_quote() -> None:
    assert promises.fixed_occasion_of({"fixed_occasion": True}, ["thứ bảy khai trương, em giao đủ bàn ghế"]) is True
    assert promises.fixed_occasion_of({"fixed_occasion": True}, ["đám cưới con em tuần sau"]) is True
    assert promises.fixed_occasion_of({"fixed_occasion": True}, ["cuối tuần em giao"]) is False       # "cuối" ≠ "cưới"
    assert promises.fixed_occasion_of({"fixed_occasion": False}, ["khai trương thứ bảy"]) is False    # từ khoá một mình không đủ (model khai)
    assert promises.fixed_occasion_of({}, ["khai trương thứ bảy"]) is False


def test_p3b_promise_source_class_ptt2() -> None:
    a, b = SimpleNamespace(entity_id=uuid.uuid4()), SimpleNamespace(entity_id=uuid.uuid4())
    assert promises.promise_source_class(a, a) == "insider"          # tự nói lời hứa của mình
    assert promises.promise_source_class(a, b) == "member"           # người khác kể lại
    assert promises.promise_source_class(a, None) == "member" and promises.promise_source_class(None, a) == "member"


# ─── promises: đơn vị (DB) ───────────────────────────────────────────────────

async def _promise_world(db: AsyncSession) -> Any:
    w = await _world(db)
    w.spid, w.siid, w.sent = await _person(db, w, "NV Hà")
    w.cpid, w.ciid, w.cent = await _person(db, w, "Chị Tư")
    await _staff_of(db, w, w.sent)
    await _customer_of(db, w, w.cent)
    return w


async def _add_evidence(db: AsyncSession, uid: uuid.UUID, at: datetime, raw: uuid.UUID, quote: str = "trích 2") -> None:
    rec = (await db.execute(text("SELECT received_at FROM raw.events WHERE id = :i"), {"i": raw})).scalar_one()
    await db.execute(text("""INSERT INTO clean.evidence (meaning_unit_id, meaning_observed_at, raw_event_id, raw_received_at, quote, quote_status,
                                                          source_field, span_start, span_end)
                             VALUES (:u, :t, :r, :rr, :q, 'verified', 'body', 0, 5)"""), {"u": uid, "t": at, "r": raw, "rr": rec, "q": quote})
    await db.commit()


async def _promise_unit(db: AsyncSession, w: Any, *, v: dict[str, Any] | None = None, flags: dict[str, Any] | None = None,
                        body: str = "dạ 5h chiều nay em giao đủ 200 bao cho chị", nudge: str | None = None,
                        due: datetime | None = None, conclusion: str = "NV hứa giao 200 bao cho khách") -> tuple[uuid.UUID, uuid.UUID]:
    """Lời hứa của NV với khách (tin NV lúc −2h). `nudge` ≠ None ⇒ khách nhắn thêm lúc −1h (tin ĐẾN) làm bằng chứng thứ hai của đơn vị.
    Trả (unit_id, raw id tin nhắc hoặc raw lời hứa)."""
    raw = await mk_raw(db, w.org, w.channel, identity=w.siid, body=body, group=w.group, at=NOW - timedelta(hours=2))
    uid = await _unit(db, w, raw, "nghia_vu_loi_hua", person=w.spid, people=[("nguoi_hua", w.sent), ("nguoi_duoc_hua", w.cent)], flags=flags,
                      v={"deadline_at": (due or NOW + timedelta(hours=3)).isoformat(), **(v or {})}, gate=levels.G4, at=NOW - timedelta(hours=2),
                      conclusion=conclusion)
    nraw = raw
    if nudge is not None:
        nraw = await mk_raw(db, w.org, w.channel, identity=w.ciid, body=nudge, group=w.group, at=NOW - timedelta(hours=1))
        await _add_evidence(db, uid, NOW - timedelta(hours=2), nraw, nudge)
    return uid, nraw


async def _li_of_unit(db: AsyncSession, w: Any) -> levels.SignalIn:
    out = await promises.project_units(db, w.org, NOW)
    await db.commit()
    assert len(out) == 1
    return levels.SignalIn.model_validate((await _rows(db, w.org, "nghia_vu_loi_hua"))[0].level_input)


async def test_p3b_unit_v2_nudge_by_promisee_verified_sets_flag(db: AsyncSession) -> None:
    w = await _promise_world(db)
    nraw = await mk_raw(db, w.org, w.channel, identity=w.ciid, body="giờ này hàng chưa tới em ơi", group=w.group, at=NOW - timedelta(hours=1))
    await _promise_unit(db, w, flags={"nudged": True}, v={"contract_version": "brain-out/2", "nudge_message_id": f"m_{nraw}", "nudge_by_role": "customer"})
    li = await _li_of_unit(db, w)
    assert li.promise.nudged_by_customer is True and li.promise.to_customer is True and li.flags.nudged is True


async def test_p3b_unit_v2_nudge_rejected_or_other_person_or_missing_id_is_false(db: AsyncSession) -> None:
    for case in ("rejected", "other_person", "no_id"):
        w = await _promise_world(db)
        other_pid, other_iid, _ = await _person(db, w, "Người khác")
        sender_iid = other_iid if case == "other_person" else w.ciid
        nraw = await mk_raw(db, w.org, w.channel, identity=sender_iid, body="giờ này hàng chưa tới", group=w.group, at=NOW - timedelta(hours=1))
        v: dict[str, Any] = {"contract_version": "brain-out/2", "nudge_message_id": None if case == "no_id" else f"m_{nraw}", "nudge_by_role": "customer",
                             "nudge_rejected": case == "rejected"}
        await _promise_unit(db, w, flags={"nudged": True}, v=v)
        li = await _li_of_unit(db, w)
        assert li.promise.nudged_by_customer is False, case             # /2 không có đường tương thích: thiếu id / bị từ chối / người khác nhắc ⇒ false
        assert li.flags.nudged is True                                  # `flags.nudged` thô giữ nguyên (cờ cuối, không phải trường này)


async def test_p3b_unit_v1_compat_needs_flag_and_verified_quote_of_promisee_after_promise(db: AsyncSession) -> None:
    # (1) có cờ nudged + trích dẫn verified của khách gửi sau lời hứa ⇒ true
    w = await _promise_world(db)
    await _promise_unit(db, w, flags={"nudged": True}, nudge="giờ này hàng chưa tới em ơi", v={"contract_version": "brain-out/1"})
    assert (await _li_of_unit(db, w)).promise.nudged_by_customer is True
    # (2) thiếu contract_version (đơn vị cũ) cũng là đường /1
    w = await _promise_world(db)
    await _promise_unit(db, w, flags={"nudged": True}, nudge="giờ này hàng chưa tới em ơi")
    assert (await _li_of_unit(db, w)).promise.nudged_by_customer is True
    # (3) cờ nudged nhưng KHÔNG có trích dẫn của khách ⇒ false
    w = await _promise_world(db)
    await _promise_unit(db, w, flags={"nudged": True}, v={"contract_version": "brain-out/1"})
    assert (await _li_of_unit(db, w)).promise.nudged_by_customer is False
    # (4) có trích dẫn của khách nhưng không có cờ nudged ⇒ false
    w = await _promise_world(db)
    await _promise_unit(db, w, nudge="giờ này hàng chưa tới em ơi", v={"contract_version": "brain-out/1"})
    assert (await _li_of_unit(db, w)).promise.nudged_by_customer is False


async def test_p3b_unit_v2_flag_nudged_without_message_id_does_not_use_compat_path(db: AsyncSession) -> None:
    w = await _promise_world(db)
    await _promise_unit(db, w, flags={"nudged": True}, nudge="giờ này hàng chưa tới em ơi", v={"contract_version": "brain-out/2"})
    assert (await _li_of_unit(db, w)).promise.nudged_by_customer is False


async def test_p3b_unit_fixed_occasion_and_stored_granularity(db: AsyncSession) -> None:
    w = await _promise_world(db)
    due = datetime(2026, 10, 24, 23, 59, 59, tzinfo=VN)
    await _promise_unit(db, w, body="thứ bảy khai trương, mai em giao đủ 40 bộ bàn ghế", due=due,
                        v={"contract_version": "brain-out/2", "fixed_occasion": True, "deadline_granularity": "day"})
    li = await _li_of_unit(db, w)
    assert li.promise.fixed_occasion is False        # quote mặc định của helper là "trích" (không từ khoá dịp) ⇒ false dù model khai
    assert li.deadline_granularity == "day" and li.deadline_at == due


async def test_p3b_unit_fixed_occasion_true_when_verified_quote_has_keyword(db: AsyncSession) -> None:
    w = await _promise_world(db)
    uid, _ = await _promise_unit(db, w, v={"contract_version": "brain-out/2", "fixed_occasion": True})
    await db.execute(text("UPDATE clean.evidence SET quote = 'thứ bảy khai trương, em giao đủ bàn ghế' WHERE meaning_unit_id = :u"), {"u": uid})
    await db.commit()
    li = await _li_of_unit(db, w)
    assert li.promise.fixed_occasion is True


async def test_p3b_unit_granularity_derived_from_text_when_not_stored(db: AsyncSession) -> None:
    w = await _promise_world(db)
    # P3a chưa lưu `deadline_granularity` ⇒ tính lại từ chữ lời hứa bằng `vi_time.parse` (chỉ nhận khi hạn trùng).
    anchor = NOW - timedelta(hours=2)
    due = datetime(2026, 10, 24, 23, 59, 59, tzinfo=VN)
    await _promise_unit(db, w, due=due, conclusion="mai em giao hàng cho chị", v={"msg_at": anchor.isoformat()})
    li = await _li_of_unit(db, w)
    assert li.deadline_granularity == "day"


async def test_p3b_unit_ptt2_source_class_insider_vs_member(db: AsyncSession) -> None:
    w = await _promise_world(db)
    await _promise_unit(db, w)
    assert (await _li_of_unit(db, w)).source_class == "insider"                       # NV tự hứa
    w2 = await _promise_world(db)
    third_pid, third_iid, _ = await _person(db, w2, "Chị kế toán")
    raw = await mk_raw(db, w2.org, w2.channel, identity=third_iid, body="NV Hà bảo 5h chiều nay giao đủ 200 bao", group=w2.group, at=NOW - timedelta(hours=2))
    await _unit(db, w2, raw, "nghia_vu_loi_hua", person=third_pid, people=[("nguoi_hua", w2.sent), ("nguoi_duoc_hua", w2.cent)],
                v={"deadline_at": (NOW + timedelta(hours=3)).isoformat()}, gate=levels.G4, at=NOW - timedelta(hours=2))
    await promises.project_units(db, w2.org, NOW)
    await db.commit()
    assert levels.SignalIn.model_validate((await _rows(db, w2.org, "nghia_vu_loi_hua"))[0].level_input).source_class == "member"   # người khác kể lại


# ─── promises: đường sự thật (DB) ────────────────────────────────────────────

async def test_p3b_fact_path_ptt2_nudge_and_granularity(db: AsyncSession) -> None:
    w = await _promise_world(db)
    due = datetime(2026, 10, 24, 23, 59, 59, tzinfo=VN)
    raw = await mk_raw(db, w.org, w.channel, identity=w.siid, body="mai em giao đủ 200 bao cho chị", group=w.group, at=NOW - timedelta(hours=2))
    nraw = await mk_raw(db, w.org, w.channel, identity=w.ciid, body="giờ này hàng chưa tới em ơi", group=w.group, at=NOW - timedelta(hours=1))
    uid = await _unit(db, w, raw, "nghia_vu_loi_hua", person=w.spid, people=[("nguoi_hua", w.sent), ("nguoi_duoc_hua", w.cent)], flags={"nudged": True},
                      v={"contract_version": "brain-out/2", "nudge_message_id": f"m_{nraw}", "nudge_by_role": "customer", "deadline_granularity": "day"},
                      at=NOW - timedelta(hours=2))
    # lời hứa mới có sự thật `promised_to`, nguồn là tin của NV; fact chưa có `source_class` insider (runner cũ ghi "member")
    await facts.assert_fact(db, mkfact(w.org, w.sent, {"text": "mai em giao đủ 200 bao cho chị", "to_entity_id": str(w.cent), "due_at": due.isoformat()},
                                       NOW - timedelta(hours=2), predicate="promised_to", raw=raw, cls="member"))
    fid = (await db.execute(text("SELECT id FROM mem.facts WHERE org_id = :o AND predicate = 'promised_to'"), {"o": w.org})).scalar_one()
    await db.execute(text("UPDATE mem.fact_evidence SET meaning_unit_id = :u WHERE fact_id = :f"), {"u": uid, "f": fid})
    await db.commit()
    out = await promises.project(db, w.org, NOW)
    await db.commit()
    assert len(out) == 1
    li = levels.SignalIn.model_validate((await _rows(db, w.org, "nghia_vu_loi_hua"))[0].level_input)
    assert li.promise.nudged_by_customer is True and li.deadline_granularity == "day"
    assert li.source_class == "insider"                                                # P-T2: tin gốc do chính NV (người hứa) gửi


async def test_p3b_fact_path_member_when_somebody_else_recounts(db: AsyncSession) -> None:
    w = await _promise_world(db)
    third_pid, third_iid, _ = await _person(db, w, "Chị kế toán")
    raw = await mk_raw(db, w.org, w.channel, identity=third_iid, body="NV Hà hứa mai giao đủ 200 bao", group=w.group, at=NOW - timedelta(hours=2))
    await facts.assert_fact(db, mkfact(w.org, w.sent, {"text": "giao đủ 200 bao", "to_entity_id": str(w.cent), "due_at": (NOW + timedelta(hours=3)).isoformat()},
                                       NOW - timedelta(hours=2), predicate="promised_to", raw=raw, cls="member"))
    await db.commit()
    await promises.project(db, w.org, NOW)
    await db.commit()
    assert levels.SignalIn.model_validate((await _rows(db, w.org, "nghia_vu_loi_hua"))[0].level_input).source_class == "member"


# ─── promises: NV hẹn giờ ⇒ lời hứa với khách ────────────────────────────────

async def _staff_commit_world(db: AsyncSession, *, trusted: bool = True) -> tuple[Any, uuid.UUID]:
    w = await _world(db)
    w.cpid, w.ciid, w.cent = await _person(db, w, "Anh Bình", ptype="customer")
    w.spid, w.siid, w.sent = await _person(db, w, "NV Hoa")
    if trusted:
        await db.execute(text("""UPDATE core.persons SET person_type = 'staff', attrs = attrs || jsonb_build_object('person_type_locked', true, 'staff_marked_at', CAST(:t AS text))
                                 WHERE id = :p"""), {"t": (NOW - timedelta(days=2)).isoformat(), "p": w.spid})
        await db.commit()
    r1 = await mk_raw(db, w.org, w.channel, identity=w.ciid, body="đơn của anh trễ rồi, huỷ đơn luôn", group=w.group, at=NOW - timedelta(minutes=50))
    r2 = await mk_raw(db, w.org, w.channel, identity=w.siid, body="dạ qua 15h em xử lý xong cho anh nhé", group=w.group, at=NOW - timedelta(minutes=30))
    uid = await _unit(db, w, r1, "xung_dot_khieu_nai", person=w.cpid, flags={"said_will_leave": True}, people=[("nguoi_gui", None)],
                      v={"complaint_stage": "ultimatum", "status_hint": "dang_xu_ly", "status_by_person_id": str(w.spid),
                         "status_at": (NOW - timedelta(minutes=30)).isoformat(), "deadline_at": datetime(2026, 10, 23, 15, 0, tzinfo=VN).isoformat(),
                         "deadline_granularity": "minute"}, at=NOW - timedelta(minutes=50), conclusion="Khách doạ huỷ đơn; NV hẹn qua 15h xử lý")
    await _add_evidence(db, uid, NOW - timedelta(minutes=50), r2, "qua 15h em xử lý xong")
    return w, uid


async def test_p3b_staff_deadline_commitment_becomes_promise_to_customer(db: AsyncSession) -> None:
    w, uid = await _staff_commit_world(db)
    out = await promises.project_staff_commitments(db, w.org, NOW)
    await db.commit()
    assert len(out) == 1
    r = (await _rows(db, w.org, "nghia_vu_loi_hua"))[0]
    li = levels.SignalIn.model_validate(r.level_input)
    assert li.promise.to_customer is True and li.gate_candidate == levels.G4 and li.source_class == "insider"
    assert li.deadline_at == datetime(2026, 10, 23, 15, 0, tzinfo=VN) and li.deadline_granularity == "minute"
    assert r.facts["staff_unit_id"] == str(uid) and "unit_id" not in r.facts                  # KHÔNG `unit_id` (không chặn đường khiếu nại của đơn vị)
    p = (await db.execute(text("SELECT to_customer, promiser_person_id, to_person_id, signal_id FROM biz.promises WHERE org_id = :o AND meaning_unit_id = :u"),
                          {"o": w.org, "u": uid})).one()
    assert p.to_customer and p.promiser_person_id == w.spid and p.to_person_id == w.cpid and p.signal_id == out[0].id
    assert await promises.project_staff_commitments(db, w.org, NOW) == []                      # chạy lại an toàn
    assert len(await _rows(db, w.org, "nghia_vu_loi_hua")) == 1


async def test_p3b_staff_commitment_skipped_when_untrusted_or_has_other_promise_path(db: AsyncSession) -> None:
    w, _ = await _staff_commit_world(db, trusted=False)                                        # NV chưa được Sếp xác nhận ⇒ không tin
    assert await promises.project_staff_commitments(db, w.org, NOW) == []
    w2, uid2 = await _staff_commit_world(db)
    # đã có đường lời hứa cho chính tin của NV (đơn vị nghia_vu_loi_hua cùng tin gốc) ⇒ không đẻ thẻ thứ hai
    raw2 = (await db.execute(text("""SELECT raw_event_id FROM clean.evidence WHERE meaning_unit_id = :u AND quote = 'qua 15h em xử lý xong'"""), {"u": uid2})).scalar_one()
    u3 = await _unit(db, w2, raw2, "nghia_vu_loi_hua", person=w2.spid, people=[("nguoi_hua", w2.sent), ("nguoi_duoc_hua", w2.cent)],
                     v={"deadline_at": datetime(2026, 10, 23, 15, 0, tzinfo=VN).isoformat()}, at=NOW - timedelta(minutes=30))
    assert u3 is not None
    assert await promises.project_staff_commitments(db, w2.org, NOW) == []
    # đã đánh dấu đã xử lý (vĩnh viễn) ⇒ không lấy lại
    n = (await db.execute(text("SELECT count(*) FROM ops.action_log WHERE org_id = :o AND action = :a"), {"o": w2.org, "a": promises.STAFF_COMMIT_ACTION})).scalar_one()
    assert n == 1


async def test_p3b_staff_commitment_skipped_when_customer_spoke_after_staff(db: AsyncSession) -> None:
    w, _ = await _staff_commit_world(db)
    await mk_raw(db, w.org, w.channel, identity=w.ciid, body="vẫn chưa thấy gì hết", group=w.group, at=NOW - timedelta(minutes=10))
    assert await promises.project_staff_commitments(db, w.org, NOW) == []


async def test_p3b_staff_commitment_does_not_block_complaint_path(db: AsyncSession) -> None:
    """Thẻ lời hứa mang `staff_unit_id`, nên `units.pending_sql` vẫn lấy đơn vị khiếu nại để chiếu như cũ."""
    w, uid = await _staff_commit_world(db)
    await promises.project_staff_commitments(db, w.org, NOW)
    await db.commit()
    rows = (await db.execute(text(U.pending_sql("intel.units.done")), {"o": w.org, "k": list(U.UNIT_KINDS), "since": NOW - timedelta(days=14), "n": 10})).all()
    assert uid in {r.id for r in rows}


# ─── questions: chờ tính trong work_hours ────────────────────────────────────

def test_p3b_waiting_hours_counts_only_work_hours() -> None:
    wh = levels.WorkHours()                                              # 08:00–17:30
    fri_17 = datetime(2026, 10, 23, 17, 0, tzinfo=VN)                    # thứ sáu
    mon_0830 = datetime(2026, 10, 26, 8, 30, tzinfo=VN)                  # thứ hai
    # khung giờ làm áp mọi ngày lịch (spec không có lịch nghỉ cuối tuần): 0,5 + 9,5 (T7) + 9,5 (CN) + 0,5
    assert questions.waiting_hours(fri_17, mon_0830, wh) == pytest.approx(0.5 + 9.5 + 9.5 + 0.5)
    assert questions.waiting_hours(datetime(2026, 10, 23, 18, 0, tzinfo=VN), datetime(2026, 10, 24, 7, 0, tzinfo=VN), wh) == 0.0   # cả đêm ngoài giờ làm
    assert questions.waiting_hours(datetime(2026, 10, 23, 9, 0, tzinfo=VN), datetime(2026, 10, 23, 12, 30, tzinfo=VN), wh) == pytest.approx(3.5)
    assert questions.waiting_hours(mon_0830, fri_17, wh) == 0.0                                  # đồng hồ lệch ⇒ không âm
    assert questions.waiting_hours(fri_17, mon_0830, levels.WorkHours(start="09:00", end="09:00")) == 0.0
    assert questions.waiting_hours(datetime(2026, 10, 23, 9, 0, tzinfo=VN), datetime(2026, 10, 23, 12, 30, tzinfo=VN),
                                   levels.WorkHours(start="10:00", end="11:00")) == pytest.approx(1.0)       # giờ làm tuỳ chỉnh


def test_p3b_in_work_hours_and_wake_at_follow_work_window() -> None:
    wh = levels.WorkHours()
    assert questions.in_work_hours(datetime(2026, 10, 23, 8, 0, tzinfo=VN), wh) is True
    assert questions.in_work_hours(datetime(2026, 10, 23, 17, 29, tzinfo=VN), wh) is True
    assert questions.in_work_hours(datetime(2026, 10, 23, 17, 30, tzinfo=VN), wh) is False       # [start, end)
    assert questions.in_work_hours(datetime(2026, 10, 23, 7, 59, tzinfo=VN), wh) is False
    # hỏi 16:30 ⇒ đủ 2 giờ chờ lúc 17:30 chưa (còn 1 giờ) ⇒ 9:00 sáng hôm sau mới đủ (1 giờ + 1 giờ từ 08:00)
    assert questions.wake_at(datetime(2026, 10, 23, 16, 30, tzinfo=VN), wh) == datetime(2026, 10, 24, 9, 0, tzinfo=VN)
    assert questions.wake_at(datetime(2026, 10, 23, 9, 0, tzinfo=VN), wh) == datetime(2026, 10, 23, 11, 0, tzinfo=VN)
    assert questions.wake_at(datetime(2026, 10, 23, 20, 0, tzinfo=VN), wh) == datetime(2026, 10, 24, 10, 0, tzinfo=VN)   # hỏi ngoài giờ ⇒ tính từ 08:00 sáng sau


def test_p3b_build_input_uses_work_hours() -> None:
    wh = levels.WorkHours()
    q = questions.QuestionIn(asked_at=datetime(2026, 10, 23, 17, 0, tzinfo=VN), raw_event_id=uuid.uuid4(), asker="internal", blocks_work=True)
    li = questions.build_input(q, datetime(2026, 10, 24, 8, 30, tzinfo=VN), work=wh)
    assert li.owner_unanswered_h == pytest.approx(1.0) and li.in_work_hours is True               # 0,5 + 0,5 giờ, KHÔNG phải 15,5 giờ tường
    li2 = questions.build_input(q, datetime(2026, 10, 24, 6, 0, tzinfo=VN), work=wh)
    assert li2.owner_unanswered_h == pytest.approx(0.5) and li2.in_work_hours is False
    assert levels.SignalIn.model_validate(li.model_dump(mode="json")) == li


async def test_p3b_questions_read_org_work_hours_settings(db: AsyncSession) -> None:
    org = await mk_org(db)
    assert await questions.work_hours_of(db, org) == levels.WorkHours(start="08:00", end="17:30")        # mặc định (không settings)
    await db.execute(text("UPDATE core.organizations SET settings = settings || CAST(:s AS jsonb) WHERE id = :o"),
                     {"s": orjson.dumps({"intel": {"work_start": "07:30", "work_end": "16:00"}}).decode(), "o": org})
    assert await questions.work_hours_of(db, org) == levels.WorkHours(start="07:30", end="16:00")
    await db.execute(text("UPDATE core.organizations SET settings = settings || CAST(:s AS jsonb) WHERE id = :o"),
                     {"s": orjson.dumps({"intel": {"work_start": "25:99", "work_end": "17:30"}}).decode(), "o": org})
    assert (await questions.work_hours_of(db, org)).start == "08:00"                                      # sai định dạng ⇒ mặc định


async def test_p3b_questions_emit_and_refresh_use_work_hours(db: AsyncSession) -> None:
    w = await _world(db)
    cpid, ciid, _ = await _person(db, w, "Anh Bình", ptype="customer")
    asked = datetime(2026, 10, 23, 17, 0, tzinfo=VN)
    raw = await mk_raw(db, w.org, w.channel, identity=ciid, body="anh cần báo giá gấp, hôm nay có không em", group=w.group, at=asked)
    q = questions.QuestionIn(asked_at=asked, raw_event_id=raw, asker="customer", asker_person_id=cpid, blocks_work=True, thread_key=f"g:{w.group}", group_id=w.group)
    sig = await questions.emit(db, w.org, q, datetime(2026, 10, 24, 8, 30, tzinfo=VN))
    await db.commit()
    li = levels.SignalIn.model_validate((await _rows(db, w.org, "cau_hoi_cho_xu_ly"))[0].level_input)
    assert li.owner_unanswered_h == pytest.approx(1.0) and li.in_work_hours is True
    nxt = (await db.execute(text("SELECT next_eval_at FROM biz.alerts WHERE id = :i"), {"i": sig.id})).scalar_one()
    assert nxt is not None and nxt <= datetime(2026, 10, 24, 9, 30, tzinfo=VN)                           # đồng hồ thức dậy khi đủ 2 giờ trong giờ làm
    # 09:35 sáng hôm sau: đủ > 2 giờ chờ trong giờ làm ⇒ làm mới ghi owner_unanswered_h > 2
    changed = await questions.refresh_waiting(db, w.org, datetime(2026, 10, 24, 9, 35, tzinfo=VN))
    await db.commit()
    li2 = levels.SignalIn.model_validate((await _rows(db, w.org, "cau_hoi_cho_xu_ly"))[0].level_input)
    assert li2.owner_unanswered_h > 2 and li2.in_work_hours is True
    assert isinstance(changed, list)
    # 03:00 đêm: chờ trong giờ làm không đổi, nhưng đã ra ngoài giờ làm ⇒ cờ in_work_hours cập nhật
    await questions.refresh_waiting(db, w.org, datetime(2026, 10, 25, 3, 0, tzinfo=VN))
    await db.commit()
    li3 = levels.SignalIn.model_validate((await _rows(db, w.org, "cau_hoi_cho_xu_ly"))[0].level_input)
    assert li3.in_work_hours is False


async def test_p3b_questions_refresh_does_not_rewrite_when_work_wait_not_enough(db: AsyncSession) -> None:
    w = await _world(db)
    cpid, ciid, _ = await _person(db, w, "Anh Bình", ptype="customer")
    asked = datetime(2026, 10, 23, 17, 0, tzinfo=VN)
    raw = await mk_raw(db, w.org, w.channel, identity=ciid, body="anh cần báo giá", group=w.group, at=asked)
    q = questions.QuestionIn(asked_at=asked, raw_event_id=raw, asker="customer", asker_person_id=cpid, blocks_work=True, thread_key=f"g:{w.group}", group_id=w.group)
    await questions.emit(db, w.org, q, datetime(2026, 10, 23, 18, 0, tzinfo=VN))
    await db.commit()
    before = (await _rows(db, w.org, "cau_hoi_cho_xu_ly"))[0].level_input
    # 03:00 sáng hôm sau: tường > 2 giờ nhưng chờ trong giờ làm chỉ ~0,5 giờ, cờ in_work_hours đã khớp (false) ⇒ không ghi lại
    await questions.refresh_waiting(db, w.org, datetime(2026, 10, 24, 3, 0, tzinfo=VN))
    await db.commit()
    assert (await _rows(db, w.org, "cau_hoi_cho_xu_ly"))[0].level_input["owner_unanswered_h"] == before["owner_unanswered_h"] < 2
    _ = signals


# ─── catalog_match: need_fields_n, explicit_urgent ───────────────────────────

def test_p3b_need_fields_of_none_when_no_source() -> None:
    assert catalog_match.need_fields_of({"v": {"need_fields_n": 3}}) == 3
    assert catalog_match.need_fields_of({"v": {"need_fields_n": 0}}) == 0           # 0 là giá trị hợp lệ (đã đếm), khác None
    assert catalog_match.need_fields_of({"v": {}}) is None                          # brain-out/1 / model bỏ ô `need`
    assert catalog_match.need_fields_of({"product": "thép"}) is None                # cầu không qua entities.v
    assert catalog_match.need_fields_of(None) is None and catalog_match.need_fields_of("x") is None
    for bad in (5, -1, True, "3", 2.0):
        assert catalog_match.need_fields_of({"v": {"need_fields_n": bad}}) is None


def test_p3b_urgent_only_from_verified_quotes() -> None:
    def e(q: str, status: str) -> Any:
        return SimpleNamespace(quote=q, quote_status=status)

    assert catalog_match.urgent_in_verified([e("Cần gấp 50 tấn thép", "verified")]) is True
    assert catalog_match.urgent_in_verified([e("Cần gấp 50 tấn thép", "unverifiable")]) is False
    assert catalog_match.urgent_in_verified([e("cần 50 tấn thép, tuần sau", "verified")]) is False
    assert catalog_match.urgent_in_verified([e("đã có bên khác xem rồi", "verified")]) is False       # "đã có bên khác xem" một mình không phải
    assert catalog_match.urgent_in_verified([e("ai nhanh thì được", "unverifiable"), e("cần ngay hôm nay", "verified")]) is True   # trích dẫn kiểm được vẫn tính
    assert catalog_match.urgent_in_verified([e("", "verified")]) is False and catalog_match.urgent_in_verified([]) is False


async def test_p3b_catalog_demand_fills_need_fields_n_and_explicit_urgent(db: AsyncSession) -> None:
    w = await _setup(db)
    uid, _ = await _demand(db, w)
    sid = (await db.execute(text("SELECT id FROM biz.market_signals WHERE meaning_unit_id = :u"), {"u": uid})).scalar_one()
    a = (await _cat_alerts(db, w.org))[0]
    li = levels.SignalIn.model_validate(a.level_input)
    assert li.opportunity.need_fields_n is None and li.flags.explicit_urgent is True              # không nguồn ⇒ None; "Cần gấp" trong trích dẫn verified
    await db.execute(text("""UPDATE clean.meaning_units SET entities = entities || CAST(:v AS jsonb) WHERE id = :u"""),
                     {"v": orjson.dumps({"v": {"need_fields_n": 2, "contract_version": "brain-out/2"}}).decode(), "u": uid})
    await db.commit()
    from gh.refinery import clock

    await catalog_match.process_demand(db, w.org, sid, clock.now())
    await db.commit()
    li2 = levels.SignalIn.model_validate((await _cat_alerts(db, w.org))[0].level_input)
    assert li2.opportunity.need_fields_n == 2


# ─── units: public_eff cho đường bảng; tên khoá g3_fields ────────────────────

def _u(kind: str, *, public: bool, v: dict[str, Any]) -> Any:
    return SimpleNamespace(kind_hint=kind, observed_at=NOW, entities={"v": v}, flags={"public": public, "said_will_leave": True}, harm={}, confidence=0.8,
                           gate_candidate=None, conclusion="x", person_id=None, group_id=None, id=uuid.uuid4())


def _people() -> Any:
    return U.People(None, 0, None)


def test_p3b_public_flag_uses_public_eff_only_for_g3_kinds_on_v2() -> None:
    for kind in ("xung_dot_khieu_nai", "quan_he_nong_nguoi"):
        v2 = {"contract_version": "brain-out/2", "public_eff": False}
        assert U.public_flag(kind, v2, {"public": True}) is False                      # /2: public thô nhưng không có venue ⇒ public_eff false
        assert U.public_flag(kind, {**v2, "public_eff": True}, {"public": True}) is True
        assert U.public_flag(kind, {**v2, "public_eff": True}, {}) is True              # public_eff là nguồn (không phụ thuộc cờ thô)
        assert U.public_flag(kind, {"contract_version": "brain-out/1"}, {"public": True}) is True      # /1 như r6.6.1
        assert U.public_flag(kind, {}, {"public": True}) is True                        # đơn vị cũ (không contract_version)
        assert U.public_flag(kind, {}, {}) is False
    # Bước 0 / loại khác: luôn đọc cờ thô (B2)
    for kind in ("rui_ro_lua_dao", "suc_khoe_an_toan", "nghia_vu_loi_hua"):
        assert U.public_flag(kind, {"contract_version": "brain-out/2", "public_eff": False}, {"public": True}) is True


def test_p3b_signal_input_flags_public_follows_public_eff_for_v2_complaint() -> None:
    sets = SimpleNamespace(is_owner=lambda e: False, customers=set(), staff=set(), owners=set(), key_relations=set())
    v = {"contract_version": "brain-out/2", "public_eff": False, "people": []}
    u = _u("xung_dot_khieu_nai", public=True, v=v)
    li = U.signal_input("xung_dot_khieu_nai", u, _people(), sets)  # type: ignore[arg-type]
    assert li.flags.public is False and li.flags.said_will_leave is True               # chỉ `public` đổi; cờ khác giữ
    u2 = _u("xung_dot_khieu_nai", public=True, v={**v, "public_eff": True})
    assert U.signal_input("xung_dot_khieu_nai", u2, _people(), sets).flags.public is True  # type: ignore[arg-type]
    u3 = _u("xung_dot_khieu_nai", public=True, v={"people": []})                        # /1
    assert U.signal_input("xung_dot_khieu_nai", u3, _people(), sets).flags.public is True  # type: ignore[arg-type]
    fr = _u("rui_ro_lua_dao", public=True, v={**v, "public_eff": False})
    assert U.signal_input("rui_ro_lua_dao", fr, _people(), sets).flags.public is True    # type: ignore[arg-type]  # lừa đảo: public thô, không đổi


async def test_p3b_conflict_route_public_follows_public_eff(db: AsyncSession) -> None:
    """Khách chê trong nhóm: /2 + `public_eff=false` (có cờ public thô nhưng không venue) ⇒ thẻ khiếu nại không `public`; `public_eff=true` ⇒ `public`."""
    results = {}
    for eff in (False, True):
        w = await _world(db, size=20)
        cpid, ciid, cent = await _person(db, w, "Anh Bình", ptype="customer")
        await _customer_of(db, w, cent)
        raw = await mk_raw(db, w.org, w.channel, identity=ciid, body="dịch vụ tệ quá, ai cũng nên biết", group=w.group, at=NOW - timedelta(minutes=30))
        await _unit(db, w, raw, "xung_dot_khieu_nai", person=cpid, flags={"public": True, "said_will_leave": True}, people=[("nguoi_gui", None)],
                    v={"contract_version": "brain-out/2", "public_eff": eff, "complaint_stage": "voice", "public_venue": "group" if eff else None},
                    at=NOW - timedelta(minutes=30))
        out = await U.project(db, w.org, NOW)
        await db.commit()
        assert out
        results[eff] = levels.SignalIn.model_validate((await _rows(db, w.org, "xung_dot_khieu_nai"))[0].level_input).flags.public
    assert results == {False: False, True: True}


def test_p3b_g3_fields_keys_match_runner_verified_fields() -> None:
    """P3a ghi `entities.v` bằng `runner.verified_fields`; `units.g3_fields` phải đọc ĐÚNG các khoá đó (không lệch tên)."""
    from gh.refinery.runner import verified_fields

    vu = SimpleNamespace(msg_at=NOW, event_at=NOW, event_time_unparsed=False, deadline_at=None, deadline_unparsed=False, ongoing=False, money_vnd=None,
                         status_by_person_id=None, status_at=None, txn_state=None, txn_at=None, delivery_form="direct", need_fields_n=None,
                         status_by_role=None, public_eff=True, stage_by_role="customer", asks_by_role="customer", nudge_by_role=None,
                         nudge_rejected=False, contract_version="brain-out/2")
    unit = SimpleNamespace(status_hint="dang_mo", condition=None, complaint_stage="ultimatum", stage_message_id="m_1", asks_for_owner=True,
                           public_venue="social", flags=SimpleNamespace(public=True), need=None, fixed_occasion=False, nudge_message_id=None, owner_action=None)
    v = verified_fields(vu, unit, [])
    g3 = U.g3_fields(v)
    assert g3 == {"complaint_stage": "ultimatum", "asks_for_owner": True, "public_venue": "social"}
    assert {"complaint_stage", "asks_for_owner", "public_venue", "public_eff", "contract_version", "need_fields_n", "fixed_occasion",
            "nudge_message_id", "nudge_rejected", "status_at", "status_by_person_id", "status_hint", "deadline_at"} <= set(v)
