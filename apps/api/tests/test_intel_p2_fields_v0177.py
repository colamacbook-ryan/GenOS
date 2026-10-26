# ruff: noqa: E501
"""v0.1.77 (gói P2) — các trường (c′) do người/khách/khán giả: `prior_threat_no_exit_n`, trường G3 ở conflicts/units/heat, `customer_audience_n`
ở mọi đường chiếu có luồng nhóm, `staff_owned`/`staff_ref` end-to-end, khách chủ chốt (`subject_entity_id`). Trên PostgreSQL thật."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gh.biz.intel import levels
from gh.biz.intel.detectors import conflicts
from gh.biz.intel.detectors import heat as heat_det
from gh.biz.intel.detectors import units as U
from gh.biz.mem import facts
from tests.intel_helpers import compute, person, sig
from tests.mem_helpers import T, mkfact
from tests.test_intel_units_t12_v0176 import _person, _unit, _world
from tests.test_owner_profile_support import mk_raw, mk_user

NOW = datetime(2026, 10, 23, 14, 0, tzinfo=levels.VN)


async def _li(db: AsyncSession, org: uuid.UUID, kind: str = "xung_dot_khieu_nai") -> list[dict[str, Any]]:
    return [r.level_input for r in (await db.execute(text("SELECT level_input FROM biz.alerts WHERE org_id = :o AND kind = :k ORDER BY created_at"),
                                                     {"o": org, "k": kind})).all()]


async def _complain(db: AsyncSession, w: Any, pid: uuid.UUID, iid: uuid.UUID, at: datetime, *, chain: str, now: datetime | None = None, **kw: Any) -> list[Any]:
    raw = await mk_raw(db, w.org, w.channel, identity=iid, body="giao trễ quá, lần sau lấy chỗ khác", at=at, group=w.group)
    data: dict[str, Any] = {"sent_at": at, "raw_event_id": raw, "customer_person_id": pid, "quote": "lần sau lấy chỗ khác", "span_end": 20,
                            "said_will_leave": True, "reply_chain_id": chain, "thread": levels.Thread(type="group", size=20, owner_in_thread=True, agent_in_thread=True),
                            "thread_key": f"g:{w.group}", "group_id": w.group}
    data.update(kw)
    out = await conflicts.emit(db, w.org, conflicts.ComplaintIn(**data), now or at)
    await db.commit()
    assert out
    return out


async def _prior(db: AsyncSession, w: Any, pid: uuid.UUID, ck: str = "case3:khac|x") -> int:
    return await conflicts.prior_threat_no_exit_n(db, w.org, pid, ck, NOW)


# ─── prior_threat_no_exit_n (c′) ─────────────────────────────────────────────

async def test_prior_threat_counts_only_closed_other_cases_in_90_days(db: AsyncSession) -> None:
    w = await _world(db)
    pid, iid, _ = await _person(db, w, "Chú Bảy", ptype="customer")
    assert await _prior(db, w, pid) == 0                                                      # chưa có lịch sử
    await _complain(db, w, pid, iid, NOW - timedelta(days=20), chain="c1", closure="owner")   # doạ, đã đóng (Sếp) ⇒ đếm
    assert await _prior(db, w, pid) == 1
    await _complain(db, w, pid, iid, NOW - timedelta(days=10), chain="c2")                    # doạ, ĐANG MỞ ⇒ không đếm (cùng vụ/leo thang không phải tiền lệ)
    assert await _prior(db, w, pid) == 1
    await _complain(db, w, pid, iid, NOW - timedelta(days=8), chain="c3", closure="customer", complaint_stage="exit_pending")   # đã có exit_* ⇒ không đếm
    await _complain(db, w, pid, iid, NOW - timedelta(days=7), chain="c4", closure="staff", public=True, public_venue="social")   # công khai ⇒ không đếm
    await _complain(db, w, pid, iid, NOW - timedelta(days=6), chain="c5", closure="staff", said_will_leave=False, complaint_stage="voice")   # không phải lời doạ
    assert await _prior(db, w, pid) == 1
    await _complain(db, w, pid, iid, NOW - timedelta(days=100), chain="c6", closure="owner", now=NOW - timedelta(days=100))      # quá 90 ngày
    assert await _prior(db, w, pid) == 1
    await _complain(db, w, pid, iid, NOW - timedelta(days=30), chain="c7", closure="customer")
    assert await _prior(db, w, pid) == 2                                                      # đếm theo VỤ (case_key), không theo hàng
    other, oiid, _ = await _person(db, w, "Khách khác", ptype="customer")
    assert await _prior(db, w, other) == 0                                                    # lời doạ của người khác không tính


async def test_prior_threat_excludes_current_case_key_and_open_quiet_case_needs_later_order(db: AsyncSession) -> None:
    w = await _world(db)
    pid, iid, _ = await _person(db, w, "Chị Mai", ptype="customer")
    out = await _complain(db, w, pid, iid, NOW - timedelta(days=30), chain="q1")              # vụ mở, ≥ 14 ngày không exit_*
    ck = (await db.execute(text("SELECT case_key FROM biz.alerts WHERE id = :i"), {"i": out[0].id})).scalar_one()
    assert await _prior(db, w, pid) == 0                                                      # khách chưa đặt việc nào sau đó
    assert await conflicts.prior_threat_no_exit_n(db, w.org, pid, ck, NOW) == 0               # đúng case_key hiện tại: luôn loại
    await db.execute(text("INSERT INTO biz.deals (org_id, code, person_id, amount_vnd, status, won_at) VALUES (:o, 'DQ1', :p, 1, 'won', :t)"),
                     {"o": w.org, "p": pid, "t": NOW - timedelta(days=5)})
    await db.commit()
    assert await _prior(db, w, pid) == 1                                                      # doạ, không bỏ: còn đặt việc sau đó ⇒ coi như đã đóng
    assert await conflicts.prior_threat_no_exit_n(db, w.org, pid, ck, NOW) == 0
    out2 = await _complain(db, w, pid, iid, NOW - timedelta(hours=1), chain="q2", now=NOW)    # emit đưa số đếm vào level_input
    assert (await _li(db, w.org))[-1]["prior_threat_no_exit_n"] == 1 and out2


async def test_prior_threat_reads_stage_eff_when_writer_stores_it(db: AsyncSession) -> None:
    w = await _world(db)
    pid, iid, _ = await _person(db, w, "Anh Tư", ptype="customer")
    out = await _complain(db, w, pid, iid, NOW - timedelta(days=12), chain="s1", closure="owner")
    assert await _prior(db, w, pid) == 1
    # bên ghi (P4) lưu `stage_eff` TRƯỚC θ1 (spec (h)); ở đây giả lập giá trị `vent` ⇒ không phải lời doạ, rồi `ultimatum` ⇒ đếm lại
    await db.execute(text("UPDATE biz.alerts SET level_input = level_input || '{\"stage_eff\": \"vent\"}'::jsonb WHERE id = :i"), {"i": out[0].id})
    await db.execute(text("UPDATE biz.alerts SET level_input = level_input || '{\"stage_eff\": \"vent\"}'::jsonb WHERE case_key = (SELECT case_key FROM biz.alerts WHERE id = :i)"), {"i": out[0].id})
    assert await _prior(db, w, pid) == 0
    await db.execute(text("UPDATE biz.alerts SET level_input = level_input || '{\"stage_eff\": \"ultimatum\"}'::jsonb WHERE case_key = (SELECT case_key FROM biz.alerts WHERE id = :i)"), {"i": out[0].id})
    assert await _prior(db, w, pid) == 1


# ─── trường G3 ở conflicts ───────────────────────────────────────────────────

async def test_conflicts_fills_g3_fields_and_subject_is_key_customer_entity(db: AsyncSession) -> None:
    w = await _world(db)
    user = await mk_user(db, w.org)
    pid, iid, ent = await _person(db, w, "Khách chủ chốt", ptype="customer")
    await facts.assert_fact(db, mkfact(w.org, w.owner, None, T(1), predicate="owner_key_relation", object_entity=ent, cls="owner", confirmed_by=user))
    await db.commit()
    await _complain(db, w, pid, iid, NOW - timedelta(hours=1), chain="g1", public=True, public_venue="group", complaint_stage="ultimatum", asks_for_owner=True)
    li = (await _li(db, w.org))[0]
    assert (li["complaint_stage"], li["asks_for_owner"], li["public_venue"]) == ("ultimatum", True, "group")
    assert li["customer_of_owner"] is True and li["subject_entity_id"] == str(ent)           # khách chủ chốt = key_relations ∩ khách: đối tượng đúng
    from gh.biz.mem import profile_read
    assert ent in (await profile_read.snapshot(db, w.org)).key_relation_entity_ids()
    assert li["thread"]["customer_audience_n"] is None                                        # nhóm chưa có danh sách thành viên ⇒ không đếm được
    # public_venue chỉ mang theo khi `public` còn; mặc định = tương thích r6.6.1 (null)
    await _complain(db, w, pid, iid, NOW - timedelta(days=5), chain="g2", public=False, public_venue="social")
    li2 = (await _li(db, w.org))[1]
    assert li2["public_venue"] is None and li2["complaint_stage"] is None and li2["asks_for_owner"] is False and li2["prior_threat_no_exit_n"] == 0


async def test_conflicts_fills_customer_audience_from_group_members(db: AsyncSession) -> None:
    w = await _world(db)
    pid, iid, _ = await _person(db, w, "Người nói", ptype="customer")
    for name in ("K1", "K2", "K3"):
        k, _, _ = await _person(db, w, name, ptype="customer")
        await db.execute(text("INSERT INTO core.group_members (group_id, person_id) VALUES (:g, :p)"), {"g": w.group, "p": k})
    await db.execute(text("INSERT INTO core.group_members (group_id, person_id) VALUES (:g, :p)"), {"g": w.group, "p": pid})
    await db.execute(text("UPDATE core.groups SET member_count = 4 WHERE id = :g"), {"g": w.group})     # danh sách đủ ⇒ đếm được
    await db.commit()
    await _complain(db, w, pid, iid, NOW - timedelta(hours=1), chain="a1", public=True, public_venue="group")
    assert (await _li(db, w.org))[0]["thread"]["customer_audience_n"] == 3                  # thành viên KHÁC người nói là khách


async def test_conflicts_staff_owned_and_asks_for_owner_wins(db: AsyncSession) -> None:
    w = await _world(db)
    pid, iid, _ = await _person(db, w, "Khách A", ptype="customer")
    await _complain(db, w, pid, iid, NOW - timedelta(hours=1), chain="o1", staff_owned=True, staff_ref="u_nv")
    li = (await _li(db, w.org))[0]
    assert li["staff_owned"] is True and li["staff_ref"] == "u_nv"
    pid2, iid2, _ = await _person(db, w, "Khách B", ptype="customer")
    await _complain(db, w, pid2, iid2, NOW - timedelta(hours=1), chain="o2", staff_owned=True, staff_ref="u_nv", asks_for_owner=True)
    li = (await _li(db, w.org))[1]
    assert li["staff_owned"] is False and li["staff_ref"] is None and li["asks_for_owner"] is True   # asks_for_owner thắng


# ─── units: đọc entities.v bằng .get, kiểm người gửi NV ──────────────────────

async def test_units_route_reads_g3_fields_and_staff_owned_from_verified(db: AsyncSession) -> None:
    w = await _world(db)
    cpid, ciid, _ = await _person(db, w, "Anh Bình", ptype="customer")
    spid, siid, _ = await _person(db, w, "NV Hoa")
    await db.execute(text("""UPDATE core.persons SET person_type = 'staff', attrs = attrs || jsonb_build_object('person_type_locked', true, 'staff_marked_at', CAST(:t AS text))
                             WHERE id = :p"""), {"t": (NOW - timedelta(days=2)).isoformat(), "p": spid})
    await db.commit()
    r1 = await mk_raw(db, w.org, w.channel, identity=ciid, body="trễ nữa là em huỷ đơn", group=w.group, at=NOW - timedelta(minutes=50))
    r2 = await mk_raw(db, w.org, w.channel, identity=siid, body="em đang xử lý anh nhé", group=w.group, at=NOW - timedelta(minutes=30))
    uid = await _unit(db, w, r1, "xung_dot_khieu_nai", person=cpid, flags={"said_will_leave": True}, people=[("nguoi_gui", None)],
                      v={"complaint_stage": "exit_pending", "asks_for_owner": False, "public_venue": "bogus", "status_hint": "dang_xu_ly",
                         "status_by_person_id": str(spid), "status_at": (NOW - timedelta(minutes=30)).isoformat()}, at=NOW - timedelta(minutes=50))
    rec = (await db.execute(text("SELECT received_at FROM raw.events WHERE id = :i"), {"i": r2})).scalar_one()
    await db.execute(text("""INSERT INTO clean.evidence (meaning_unit_id, meaning_observed_at, raw_event_id, raw_received_at, quote, quote_status, source_field, span_start, span_end)
                             VALUES (:u, :t, :r, :rr, 'trích', 'verified', 'body', 0, 5)"""), {"u": uid, "t": NOW - timedelta(minutes=50), "r": r2, "rr": rec})
    await db.commit()
    await U.project(db, w.org, NOW)
    await db.commit()
    li = (await _li(db, w.org))[0]
    assert li["complaint_stage"] == "exit_pending" and li["public_venue"] is None             # venue sai ⇒ mặc định
    assert li["staff_owned"] is True and li["staff_ref"] == f"u_{spid}"


async def test_units_route_without_brain_out_2_fields_keeps_defaults(db: AsyncSession) -> None:
    w = await _world(db)
    cpid, ciid, _ = await _person(db, w, "Anh Bình", ptype="customer")
    raw = await mk_raw(db, w.org, w.channel, identity=ciid, body="giao trễ lần thứ ba rồi", group=w.group, at=NOW - timedelta(hours=1))
    await _unit(db, w, raw, "xung_dot_khieu_nai", person=cpid, flags={"public": True, "said_will_leave": True}, people=[("nguoi_gui", None)], at=NOW - timedelta(hours=1))
    await U.project(db, w.org, NOW)
    await db.commit()
    li = (await _li(db, w.org))[0]
    assert (li["complaint_stage"], li["asks_for_owner"], li["public_venue"], li["staff_owned"], li["staff_ref"]) == (None, False, None, False, None)
    assert li["flags"]["public"] is True                                                      # brain-out/1: public giữ như r6.6.1 (không venue ⇒ tương thích)


def test_units_g3_fields_pure_defaults_and_validation() -> None:
    assert U.g3_fields({}) == {"complaint_stage": None, "asks_for_owner": False, "public_venue": None}
    assert U.g3_fields({"complaint_stage": "voice", "asks_for_owner": True, "public_venue": "authority"}) == {
        "complaint_stage": "voice", "asks_for_owner": True, "public_venue": "authority"}
    assert U.g3_fields({"complaint_stage": "lạ", "asks_for_owner": "yes", "public_venue": 3})["asks_for_owner"] is False


# ─── heat ────────────────────────────────────────────────────────────────────

async def test_heat_fills_audience_for_group_and_none_for_dm(db: AsyncSession) -> None:
    w = await _world(db)
    pid, iid, ent = await _person(db, w, "Chị Hoa", ptype="customer")
    k, _, _ = await _person(db, w, "Khách cùng nhóm", ptype="customer")
    for p in (pid, k):
        await db.execute(text("INSERT INTO core.group_members (group_id, person_id) VALUES (:g, :p)"), {"g": w.group, "p": p})
    await db.execute(text("UPDATE core.groups SET member_count = 2 WHERE id = :g"), {"g": w.group})
    await mk_raw(db, w.org, w.channel, identity=iid, body="Chị lấy thêm 10 thùng nhé", group=w.group, at=NOW - timedelta(days=15))
    for i, d in enumerate([39, 33, 27, 21, 15]):
        await db.execute(text("INSERT INTO biz.deals (org_id, code, person_id, amount_vnd, status, won_at) VALUES (:o, :c, :p, 1, 'won', :t)"),
                         {"o": w.org, "c": f"H{i}", "p": pid, "t": NOW - timedelta(days=d)})
    await db.commit()
    await heat_det.scan(db, w.org, NOW)
    await db.commit()
    li = (await _li(db, w.org, "quan_he_nong_nguoi"))[0]
    assert li["thread"]["type"] == "group" and li["thread"]["customer_audience_n"] == 1 and li["customer_of_owner"] is True
    assert li["subject_entity_id"] == str(ent)


# ─── prior_threat_no_exit_n: các điểm review ─────────────────────────────────

async def test_prior_threat_owner_resolved_wrong_feedback_old_same_key_and_bad_msg_at(db: AsyncSession) -> None:
    w = await _world(db)
    pid, iid, _ = await _person(db, w, "Chú Sáu", ptype="customer")
    # Sếp bấm "Đã xử lý" (owner_resolved) = đã đóng, dù closure vẫn "none"
    out = await _complain(db, w, pid, iid, NOW - timedelta(days=20), chain="r1")
    assert await _prior(db, w, pid) == 0                                                      # vụ còn mở
    await db.execute(text("UPDATE biz.alerts SET level_input = level_input || '{\"owner_resolved\": true}'::jsonb WHERE id = :i"), {"i": out[0].id})
    assert await _prior(db, w, pid) == 1
    # Sếp chấm Sai ⇒ không phải tiền lệ
    await db.execute(text("INSERT INTO biz.signal_feedback (org_id, signal_id, verdict, verdict_by) VALUES (:o, :s, 'wrong', 'owner')"), {"o": w.org, "s": out[0].id})
    assert await _prior(db, w, pid) == 0
    # vụ cũ ĐÃ ĐÓNG cùng khoá với vụ hiện tại (chưa có vụ mở) vẫn được đếm
    out2 = await _complain(db, w, pid, iid, NOW - timedelta(days=9), chain="r2", closure="owner")
    await db.execute(text("UPDATE biz.alerts SET status = 'resolved' WHERE id = :i"), {"i": out2[0].id})
    ck = (await db.execute(text("SELECT case_key FROM biz.alerts WHERE id = :i"), {"i": out2[0].id})).scalar_one()
    assert await conflicts.prior_threat_no_exit_n(db, w.org, pid, ck, NOW) == 1                # khoá trùng nhưng vụ đã đóng ⇒ vẫn là tiền lệ
    await db.execute(text("UPDATE biz.alerts SET status = 'open' WHERE id = :i"), {"i": out2[0].id})
    assert await conflicts.prior_threat_no_exit_n(db, w.org, pid, ck, NOW) == 0                # vụ MỞ hiện tại luôn loại
    # msg_at sai định dạng không làm lỗi
    await db.execute(text("UPDATE biz.alerts SET level_input = jsonb_set(level_input, '{msg_at}', '\"không-phải-ngày\"') WHERE id = :i"), {"i": out2[0].id})
    assert await _prior(db, w, pid) == 1


async def test_prior_threat_only_rows_whose_subject_is_the_person(db: AsyncSession) -> None:
    w = await _world(db)
    pid, iid, _ = await _person(db, w, "Khách X", ptype="customer")
    await _complain(db, w, pid, iid, NOW - timedelta(days=20), chain="p1", closure="owner")
    await db.execute(text("UPDATE biz.alerts SET subject_id = :o WHERE org_id = :org"), {"o": uuid.uuid4(), "org": w.org})   # facts.person_ids vẫn chứa khách
    assert await _prior(db, w, pid) == 0                                                      # chỉ đếm dòng subject = người này


# ─── closure = staff (N1) ────────────────────────────────────────────────────

async def _staff_unit(db: AsyncSession, w: Any, status: str, *, asks: bool = False) -> None:
    cpid, ciid, _ = await _person(db, w, "Anh Bình", ptype="customer")
    spid, siid, _ = await _person(db, w, "NV Hoa")
    await db.execute(text("""UPDATE core.persons SET person_type = 'staff', attrs = attrs || jsonb_build_object('person_type_locked', true, 'staff_marked_at', CAST(:t AS text))
                             WHERE id = :p"""), {"t": (NOW - timedelta(days=2)).isoformat(), "p": spid})
    await db.commit()
    r1 = await mk_raw(db, w.org, w.channel, identity=ciid, body="trễ nữa là em huỷ đơn", group=w.group, at=NOW - timedelta(minutes=50))
    await mk_raw(db, w.org, w.channel, identity=siid, body="em đã đổi hàng cho anh rồi", group=w.group, at=NOW - timedelta(minutes=30))
    await _unit(db, w, r1, "xung_dot_khieu_nai", person=cpid, flags={"said_will_leave": True}, people=[("nguoi_gui", None)],
                v={"asks_for_owner": asks, "status_hint": status, "status_by_person_id": str(spid), "status_at": (NOW - timedelta(minutes=30)).isoformat()},
                at=NOW - timedelta(minutes=50))
    await U.project(db, w.org, NOW)
    await db.commit()


async def test_units_staff_da_xu_ly_closes_as_staff_dang_xu_ly_owns(db: AsyncSession) -> None:
    w = await _world(db)
    await _staff_unit(db, w, "da_xu_ly")
    li = (await _li(db, w.org))[0]
    assert li["closure"] == "staff" and li["staff_owned"] is False
    w2 = await _world(db)
    await _staff_unit(db, w2, "dang_xu_ly")
    li = (await _li(db, w2.org))[0]
    assert li["closure"] == "none" and li["staff_owned"] is True


async def test_units_asks_for_owner_beats_staff_closure_and_ownership(db: AsyncSession) -> None:
    for status in ("da_xu_ly", "dang_xu_ly"):
        w = await _world(db)
        await _staff_unit(db, w, status, asks=True)
        li = (await _li(db, w.org))[0]
        assert li["closure"] == "none" and li["staff_owned"] is False and li["asks_for_owner"] is True


# ─── B2: trường r7 không đổi Level của cổng 1/2 ──────────────────────────────

def test_b2_customer_audience_n_never_changes_gate1_or_gate2_level() -> None:
    for kind, fl in (("rui_ro_lua_dao", {"money_request": True}), ("suc_khoe_an_toan", {"emergency": True})):
        base = sig(kind, flags=fl, victims=[person("v1", customer=True)], hard_rule_hit=["luat_cung"])
        ref = compute(base)
        for n in (None, 0, 2, 50):
            s = base.model_copy(update={"thread": base.thread.model_copy(update={"customer_audience_n": n})})
            assert compute(s) == ref, (kind, n)
