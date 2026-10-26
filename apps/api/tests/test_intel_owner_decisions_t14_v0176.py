# ruff: noqa: E501
"""v0.1.76 (gói T14) — bốn quyết định của Sếp (Ryan, 2026-10-25) sau G-KB Đợt 2:
D1 giữ 🔴 lừa đảo tới hết ngày hẹn chuyển tiền + nhắc lại một lần sáng ngày đó; D2 mạo danh khách/đối tác đặt hàng ghi nợ / đổi địa chỉ giao
⇒ cổng 1; D3 "Thiết lập Tình báo" (API trạng thái sẵn sàng — test ở `test_intel_setup_t14_v0176.py`); D5 cơ hội lớn có hạn gần ở nhóm chưa xác
nhận tối thiểu 🟠. Kèm quyết định Gen: nhóm không rõ cỡ không phải "≤ 50 khách lõi" của cổng an toàn. Câu thử tự soạn, đồng hồ đóng băng."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from types import SimpleNamespace
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gh.biz.intel import digest, levels, signals
from gh.biz.intel.detectors import fraud
from gh.biz.intel.detectors import units as U
from gh.biz.intel.pipes import delivery
from gh.db import sessionmaker
from tests import intel_helpers as H
from tests import test_intel_units_t12_v0176 as T12
from tests.fraud_helpers import world
from tests.mem_helpers import mk_org
from tests.owner_helpers import bot_world
from tests.test_intel_core_signals_v0176 import _ev, _install, _raw
from tests.test_owner_profile_support import mk_raw

FR = levels.FR
VN = levels.VN
T0 = datetime(2026, 10, 17, 9, 0, tzinfo=VN)              # tin "đổi STK, thứ 4 anh chuyển nhé" lúc 09:00 thứ 7 17/10
DUE = datetime(2026, 10, 21, 10, 0, tzinfo=VN)            # hẹn lên lệnh 10:00 thứ 4 21/10


def _fr(*, due: datetime | None = DUE, txn_at: datetime | None = None, now: datetime, **kw: Any) -> levels.SignalIn:
    """Vụ lừa đảo đang chờ giao dịch (cổng 1 nhánh yêu cầu tiền), tin gửi lúc T0."""
    return H.sig(FR, now=now, ago_h=(now - T0).total_seconds() / 3600, flags={"money_request": True}, txn_state="pending",
                 deadline_at=due, txn_at=txn_at, **kw)


# ─── D1: giữ 🔴 tới hết ngày hẹn chuyển ───────────────────────────────────────

def test_d1_pending_with_future_due_stays_red_past_48h() -> None:
    now = T0 + timedelta(hours=60)                         # 19/10 21:00 — quá 48 giờ, giao dịch còn chưa tới
    lv = H.compute(_fr(now=now), now=now)
    assert (lv.level, lv.gate, lv.delivery) == ("red", levels.G1, "push_now")
    no_due = H.compute(_fr(due=None, now=now), now=now)    # không có hạn ⇒ cửa sổ 48 giờ như cũ
    assert (no_due.level, no_due.candidate, no_due.delivery) == ("amber", levels.G1, "digest_top")
    assert "window_expired" in no_due.reasons and "txn_pending" in no_due.reasons


def test_d1_hold_ends_at_end_of_vn_due_day() -> None:
    late = datetime(2026, 10, 21, 23, 59, tzinfo=VN)
    assert H.compute(_fr(now=late), now=late).level == "red"
    after = datetime(2026, 10, 22, 0, 1, tzinfo=VN)
    lv = H.compute(_fr(now=after), now=after)
    assert (lv.level, lv.candidate) == ("amber", levels.G1) and "window_expired" in lv.reasons
    assert "txn_pending" not in lv.reasons                 # ngày hẹn đã qua ⇒ "đã quá thời điểm báo ngay" là đúng


def test_d1_txn_at_equal_to_message_time_is_not_a_due_date_but_future_txn_at_is() -> None:
    now = T0 + timedelta(hours=60)
    same = _fr(due=None, txn_at=T0, now=now)               # model điền txn_at = giờ tin ⇒ không phải ngày hẹn
    assert levels.txn_due_at(same) is None and H.compute(same, now=now).level == "amber"
    fut = _fr(due=None, txn_at=DUE, now=now)
    assert levels.txn_due_at(fut) == DUE and H.compute(fut, now=now).level == "red"


def test_d1_hold_capped_by_floor_horizon_and_needs_pending() -> None:
    far = T0 + timedelta(days=30)
    s = _fr(due=far, now=T0 + timedelta(days=15))
    assert levels.txn_hold_until(s) == T0 + timedelta(hours=levels.H_SAN)
    assert H.compute(s, now=T0 + timedelta(days=15)).level != "red"
    done = _fr(now=T0 + timedelta(hours=60)).model_copy(update={"txn_state": "none"})
    assert levels.txn_due_at(done) is None


def test_d1_reminder_once_on_morning_of_due_day() -> None:
    cp = levels.CasePrior(red_pushed=True, gate=levels.G1, branch_rank=1, victim_refs=["v1"])
    before = datetime(2026, 10, 21, 7, 29, tzinfo=VN)
    at = datetime(2026, 10, 21, 7, 31, tzinfo=VN)
    eve = datetime(2026, 10, 20, 20, 0, tzinfo=VN)
    for now, want in ((eve, "merge"), (before, "merge"), (at, "push_now")):
        lv = H.compute(_fr(now=now, case_prior=cp), now=now)
        assert (lv.level, lv.delivery) == ("red", want), now
    lv = H.compute(_fr(now=at, case_prior=cp), now=at)
    assert "txn_day_reminder" in lv.reasons and "escalation" not in lv.reasons
    done = cp.model_copy(update={"txn_reminded": True})
    assert H.compute(_fr(now=at, case_prior=done), now=at).delivery == "merge"          # một vụ một lần nhắc
    assert levels.txn_remind_at(_fr(now=at)) == at.replace(minute=30)


def test_d1_next_change_includes_reminder_and_hold_end() -> None:
    now = datetime(2026, 10, 20, 12, 0, tzinfo=VN)
    s = _fr(now=now)
    assert levels.next_change_at(s, now) <= datetime(2026, 10, 21, 7, 30, tzinfo=VN)
    after_rem = datetime(2026, 10, 21, 12, 0, tzinfo=VN)
    assert levels.next_change_at(s, after_rem) <= datetime(2026, 10, 22, 0, 0, tzinfo=VN)


def test_d1_digest_line_never_says_window_passed_while_txn_pending() -> None:
    r = SimpleNamespace(reasons=["window_expired", "txn_pending"], would_push_at=None)
    line = digest._facts_line(r)
    assert not any("đã quá thời điểm" in x for x in line) and any("chưa thấy xảy ra" in x for x in line)
    assert digest._facts_line(SimpleNamespace(reasons=["window_expired"], would_push_at=None)) == ["đã quá thời điểm báo ngay"]


def _upd(li: levels.SignalIn, raw: uuid.UUID) -> signals.UpsertIn:
    return signals.UpsertIn(kind=FR, title="Có yêu cầu đổi số tài khoản nhận tiền", detector="test", level_input=li, evidence=[_ev(raw, "đổi số")],
                            case_key=levels.case_key(FR, at=li.msg_at, payee_digest="d" * 16), thread_key="g:t14")


async def _row(db: AsyncSession, sid: uuid.UUID) -> Any:
    return (await db.execute(text("SELECT level, delivery, pushed_at, reasons, case_state, next_eval_at FROM biz.alerts WHERE id = :i"),
                             {"i": sid})).one()


async def test_d1_relane_holds_red_then_reminds_once_and_push_text_says_reminder(db: AsyncSession) -> None:
    org = await mk_org(db)
    await _install(db, org)
    raw = await _raw(db, org, "đổi số")
    t1 = T0 + timedelta(minutes=2)
    s = await signals.upsert(db, org, _upd(_fr(now=t1), raw), t1)
    assert (s.level, s.delivery) == ("red", "push_now")
    first_push = s.pushed_at
    r = await _row(db, s.id)
    assert not r.case_state.get("txn_reminded")
    # 19/10 10:00 (49 giờ): còn chờ giao dịch ⇒ giữ 🔴 (một vụ một 🔴 ⇒ merge), KHÔNG tụt window_expired
    t2 = T0 + timedelta(hours=49)
    s2 = await signals.relane(db, org, s.id, t2)
    assert (s2.level, s2.delivery) == ("red", "merge") and "window_expired" not in s2.reasons
    # sáng 21/10 07:31 ⇒ nhắc lại một lần
    t3 = datetime(2026, 10, 21, 7, 31, tzinfo=VN)
    s3 = await signals.relane(db, org, s.id, t3)
    assert (s3.level, s3.delivery) == ("red", "push_now") and "txn_day_reminder" in s3.reasons
    assert s3.pushed_at == t3 and s3.pushed_at != first_push
    assert (await _row(db, s.id)).case_state["txn_reminded"] is True
    await db.commit()
    await bot_world(db, org)
    async with sessionmaker()() as sx:
        assert await delivery.push_alerts(sx, org, t3) == 1
        await sx.commit()
    body = (await db.execute(text("SELECT text FROM ops.owner_outbox WHERE kind = 'alert' ORDER BY created_at DESC LIMIT 1"))).scalar_one()
    assert "Nhắc lại — hôm nay là ngày hẹn chuyển tiền" in body
    # 08:00 cùng ngày: không nhắc thêm
    s4 = await signals.relane(db, org, s.id, t3 + timedelta(minutes=29))
    assert s4.delivery == "merge" and s4.pushed_at == t3
    # sang 22/10: hết ngày hẹn ⇒ 🟠 đầu bản tin
    s5 = await signals.relane(db, org, s.id, datetime(2026, 10, 22, 0, 5, tzinfo=VN))
    assert (s5.level, s5.delivery) == ("amber", "digest_top") and "window_expired" in s5.reasons


async def test_d1_first_push_on_due_day_counts_as_reminder(db: AsyncSession) -> None:
    org = await mk_org(db)
    await _install(db, org)
    raw = await _raw(db, org, "đổi số")
    t = datetime(2026, 10, 21, 6, 0, tzinfo=VN)            # tin tới sáng sớm ngày hẹn
    li = _fr(now=t).model_copy(update={"msg_at": t - timedelta(minutes=1), "event_at": t - timedelta(minutes=1)})
    s = await signals.upsert(db, org, _upd(li, raw), t)
    assert s.delivery == "push_now"
    assert (await _row(db, s.id)).case_state["txn_reminded"] is True
    s2 = await signals.relane(db, org, s.id, datetime(2026, 10, 21, 7, 35, tzinfo=VN))
    assert s2.delivery == "merge"


# ─── D2: mạo danh đặt hàng / đổi địa chỉ giao ─────────────────────────────────

GOODS_POS = ("Giao 10 cái tivi Sony về địa chỉ mới giúp em, ghi công nợ như mọi lần nhé", "em đổi địa chỉ giao hàng sang kho Bình Dương",
             "Anh ơi em đổi địa chỉ giao hàng sang 12 Lê Lợi nhé", "đơn hàng hôm qua đổi người nhận là chị Hoa nhé", "lấy 2 thùng, ghi nợ nhé",
             "Em lấy 50 thùng, ghi nợ cuối tháng trả, giao về kho mới ở Bình Dương nhé")
#: Câu âm tính của review T14 (D2-a): người gửi mới trùng tên người đã biết mà nói chuyện thường ⇒ KHÔNG được ra mao_danh_dat_hang.
GOODS_NEG = ("Việc này chị giao cho người khác làm nhé", "Em có địa chỉ mới rồi, cuối tuần giao lưu nhé", "Mai đổi chỗ gửi xe sang hầm B nhé cả nhà",
             "Công ty đổi nơi nhận hồ sơ sang tầng 3", "Đổi người nhận giải thưởng tháng này là anh Tuấn nhé",
             "Bên em không cọc đâu, đặt hàng thì thanh toán khi nhận nhé", "Em mới chuyển nhà, đổi địa chỉ nhà rồi nha mọi người",
             "Cho em đổi địa chỉ email nhận hoá đơn với ạ", "Em chào anh chị, em là nhân viên mới, phụ trách giao hàng khu Q7",
             "Lan đây, hôm nay em nghỉ ốm, bàn giao ca cho bạn Minh", "Chị ơi địa chỉ khác của chị là gì để em gửi thiệp cưới")


def _imp_ctx() -> fraud.Ctx:
    snd = fraud.Who(ref="u_s", person_id=uuid.uuid4(), entity_id=uuid.uuid4(), name="Lan")
    imp = fraud.Who(ref="u_i", person_id=uuid.uuid4(), entity_id=uuid.uuid4(), name="Lan", customer=True, team=True, business_contact=True,
                    present_in_thread=True)
    own = fraud.Who(ref="u_o", person_id=uuid.uuid4(), entity_id=uuid.uuid4(), is_owner=True, business_contact=True, present_in_thread=True)
    return fraud.Ctx(sender=snd, stranger=True, impersonated=imp, owner=own)


def test_d2a_impersonator_small_talk_is_not_goods_rule_nor_red() -> None:
    """Review D2-a: 11 câu thường của người mới trùng tên khách/nhân viên đã biết ⇒ không luật mạo danh đặt hàng, không 🔴 (nhóm nhỏ lẫn lớn)."""
    ctx = _imp_ctx()
    for t in GOODS_NEG:
        d = fraud.decide(fraud.scan(t), ctx)
        assert d is None or "impersonated_goods" not in d.rules, t
        if d is not None:
            for size in (8, 120):
                li = fraud.signal_input(d, msg_at=H.NOW, thread=levels.Thread(type="group", size=size, owner_in_thread=True))
                assert H.compute(li).level != "red", (t, size)
    for t in GOODS_POS:
        d = fraud.decide(fraud.scan(t), ctx)
        assert d is not None and "impersonated_goods" in d.rules, t


async def test_d2a_trusted_staff_sender_is_not_impersonation(db: AsyncSession) -> None:
    """Người gửi đã được Sếp đánh dấu nhân viên (khoá loại, đánh dấu ≥ 7 ngày) trùng tên khách ⇒ không coi là mạo danh."""
    w = await world(db)
    await w.group("kh", "Đại lý điện máy", 120)
    await w.person("Tuấn Điện Máy Q7", ptype="customer", group="kh", history_days_ago=60)
    staff, iid = await w.person("Tuấn Điện Máy Q7", ptype="staff", group="kh", joined_days_ago=1)
    await db.execute(text("""UPDATE core.persons SET attrs = attrs || jsonb_build_object('person_type_locked', true, 'staff_marked_at', CAST(:t AS text))
                             WHERE id = :p"""), {"p": staff, "t": (w.now - timedelta(days=30)).isoformat()})
    await w.raw("Anh ơi đổi địa chỉ giao hàng 10 cái tivi sang kho mới giúp em nhé", sender=iid, group="kh")
    assert await fraud.run(db, w.org, w.now) == []


async def test_d2a_staff_marked_recently_not_trusted_by_hard_rules(db: AsyncSession) -> None:
    """Nhân viên mới được đánh dấu < 7 ngày: luật cứng lừa đảo không tin vai nhân viên (vẫn là người lạ nhờ ứng tiền ⇒ 🔴)."""
    w = await world(db)
    await w.group("gd", "Nhân viên cửa hàng", 25)
    p, iid = await w.person("Minh Mới", ptype="staff", group="gd", joined_days_ago=1)
    await db.execute(text("""UPDATE core.persons SET attrs = attrs || jsonb_build_object('person_type_locked', true, 'staff_marked_at', CAST(:t AS text))
                             WHERE id = :p"""), {"p": p, "t": (w.now - timedelta(days=1)).isoformat()})
    await w.raw("Mọi người ơi ứng giúp em 3 triệu, chiều em trả liền", sender=iid, group="gd")
    out = await fraud.run(db, w.org, w.now)
    assert len(out) == 1 and out[0].level == "red"


async def test_d2c_units_scans_only_sender_quotes(db: AsyncSession) -> None:
    """Review D2-c: trích dẫn "đổi địa chỉ giao hàng" của NGƯỜI KHÁC trong cùng đơn vị không làm kẻ trùng tên thành mạo danh đặt hàng."""
    w = await T12._world(db, size=20)
    _real, _riid, real_ent = await T12._person(db, w, "Tuấn Điện Máy", ptype="customer")
    scam, siid, _ = await T12._person(db, w, "Tuấn Điện Máy")
    _other, oiid, _ = await T12._person(db, w, "Cường giao nhận")
    now = T12.NOW
    raw = await mk_raw(db, w.org, w.channel, identity=siid, body="chào cả nhà, em mới vào nhóm", group=w.group, at=now - timedelta(minutes=20))
    raw2 = await mk_raw(db, w.org, w.channel, identity=oiid, body="anh ơi khách đổi địa chỉ giao hàng 10 cái tivi sang kho mới",
                        group=w.group, at=now - timedelta(minutes=19))
    uid = await T12._unit(db, w, raw, FR, person=scam, people=[("nguoi_bi_mao_danh", real_ent)], harm="có thể mạo danh")
    t = now - timedelta(minutes=20)
    rec = (await db.execute(text("SELECT received_at FROM raw.events WHERE id = :i"), {"i": raw2})).scalar_one()
    await db.execute(text("""INSERT INTO clean.evidence (meaning_unit_id, meaning_observed_at, raw_event_id, raw_received_at, quote, quote_status,
                                                          source_field, span_start, span_end)
                             VALUES (:u, :t, :r, :rr, 'khách đổi địa chỉ giao hàng 10 cái tivi sang kho mới', 'verified', 'body', 0, 20)"""),
                     {"u": uid, "t": t, "r": raw2, "rr": rec})
    await db.commit()
    out = await U.project(db, w.org, now)
    await db.commit()
    assert out and all(s.level != "red" for s in out)
    assert (await T12._rows(db, w.org))[0].level_input.get("goods_risk") is False


def _imp(**kw: Any) -> levels.SignalIn:
    base: dict[str, Any] = {"victims": [], "impersonated": [H.person("u_tuan", customer=True, present_in_thread=True, thread_member=True)],
                            "goods_risk": True, "flags": {}, "confidence_exist": 0.8}
    base.update(kw)
    return H.sig(FR, **base)


def test_d2_impersonated_known_customer_goods_order_is_red_gate1() -> None:
    lv = H.compute(_imp())
    assert (lv.level, lv.gate, lv.delivery) == ("red", levels.G1, "push_now") and lv.branch_rank == levels.BRANCH_RANK["goods"]
    assert H.compute(_imp(goods_risk=False)).level == "amber"                 # mạo danh không đặt hàng/đổi địa chỉ ⇒ như cũ
    assert H.compute(_imp(impersonated=[])).level != "red"                    # không mạo danh ⇒ không nhánh
    owner = [H.person("u_owner", is_owner=True, present_in_thread=True)]
    assert H.compute(_imp(impersonated=owner)).level != "red"                 # mạo danh Sếp đổi địa chỉ: giữ đường cũ (T12)
    assert H.compute(_imp(), now=H.NOW + timedelta(hours=49)).level == "amber"   # cửa sổ 48 giờ như nhánh yêu cầu
    assert H.compute(_imp(d2={"verdict": "disagree"})).level != "red"         # (d2) không đồng ý ⇒ không 🔴


def test_d2_scan_goods_redirect_vocabulary() -> None:
    for t in GOODS_POS:
        assert fraud.goods_redirect(t) and fraud.scan(t).goods_imp, t
    for t in ("địa chỉ mới của em là 12 Lê Lợi", "mai em ghé lấy hàng", "hôm nay giao hàng hơi trễ nha", *GOODS_NEG):
        assert not fraud.goods_redirect(t), t


async def test_d2_fraud_impersonator_redirects_delivery_without_rush_is_red(db: AsyncSession) -> None:
    w = await world(db)
    await w.group("kh", "Đại lý điện máy", 120)
    await w.person("Tuấn Điện Máy Q7", ptype="customer", group="kh", history_days_ago=60)
    _scam, iid = await w.person("Tuấn Điện Máy Q7", group="kh", joined_days_ago=1)
    await w.raw("Anh ơi giao 10 cái tivi Sony 55 inch về địa chỉ mới giúp em, ghi công nợ như mọi lần, cần trước thứ 4", sender=iid, group="kh")
    out = await fraud.run(db, w.org, w.now)
    assert len(out) == 1 and (out[0].level, out[0].gate, out[0].delivery) == ("red", levels.G1, "push_now")
    a = (await db.execute(text("SELECT facts, level_input, title FROM biz.alerts WHERE org_id = :o"), {"o": w.org})).one()
    assert "impersonated_goods" in a.facts["fraud_rules"] and a.level_input["goods_risk"] is True
    assert fraud.H_IMP_GOODS in a.level_input["hard_rule_hit"] and any(v["is_owner"] for v in a.level_input["victims"])
    assert "đổi địa chỉ giao" in a.title


async def test_d2_real_customer_changes_address_from_own_old_id_is_not_red(db: AsyncSession) -> None:
    w = await world(db)
    await w.group("kh", "Đại lý điện máy", 120)
    _p, iid = await w.person("Tuấn Điện Máy Q7", ptype="customer", group="kh", history_days_ago=60)
    await w.raw("Anh ơi giao 10 cái tivi về địa chỉ mới giúp em, ghi công nợ như mọi lần nhé", sender=iid, group="kh")
    assert await fraud.run(db, w.org, w.now) == []


async def test_d2_stranger_order_without_rush_still_needs_four_parts(db: AsyncSession) -> None:
    w = await world(db)
    _p, iid = await w.person("Anh Long Sài Gòn")
    await w.raw("Em lấy 200 thùng nước suối, ghi công nợ nha", sender=iid, group=None)       # người lạ, không giục gấp ⇒ không luật goods
    assert await fraud.run(db, w.org, w.now) == []


async def test_d2_units_impersonated_customer_delivery_change_is_red(db: AsyncSession) -> None:
    w = await T12._world(db, size=20)
    _real, _riid, real_ent = await T12._person(db, w, "Tuấn Điện Máy", ptype="customer")
    scam, siid, _ = await T12._person(db, w, "Tuấn Điện Máy")
    now = T12.NOW
    raw = await mk_raw(db, w.org, w.channel, identity=siid, body="anh ơi đổi địa chỉ giao 10 cái tivi sang kho mới bên Q7 giúp em nhé",
                       group=w.group, at=now - timedelta(minutes=20))
    await T12._unit(db, w, raw, FR, person=scam, people=[("nguoi_bi_mao_danh", real_ent)], harm="giao hàng cho người mạo danh")
    out = await U.project(db, w.org, now)
    await db.commit()
    assert [(s.level, s.gate, s.delivery) for s in out] == [("red", levels.G1, "push_now")]
    li = (await T12._rows(db, w.org))[0].level_input
    assert li["goods_risk"] is True and any(v["is_owner"] for v in li["victims"])


async def test_d2_units_real_customer_own_id_address_change_not_red(db: AsyncSession) -> None:
    w = await T12._world(db, size=20)
    real, riid, _ = await T12._person(db, w, "Tuấn Điện Máy", ptype="customer")
    now = T12.NOW
    raw = await mk_raw(db, w.org, w.channel, identity=riid, body="anh ơi đổi địa chỉ giao sang kho mới bên Q7 giúp em nhé",
                       group=w.group, at=now - timedelta(minutes=20))
    await T12._unit(db, w, raw, FR, person=real, people=[], harm="giao nhầm chỗ")
    out = await U.project(db, w.org, now)
    await db.commit()
    assert out and all(s.level != "red" for s in out)
    assert (await T12._rows(db, w.org))[0].level_input.get("goods_risk") is False


def test_d2_merge_keeps_goods_risk() -> None:
    a, b = _imp(), _imp(goods_risk=False)
    assert signals.merge_level_input(a, b).goods_risk and signals.merge_level_input(b, a).goods_risk


# ─── D5: cơ hội lớn có hạn gần ở nhóm chưa xác nhận ⇒ tối thiểu 🟠 ─────────────

def _opp(**kw: Any) -> levels.SignalIn:
    base: dict[str, Any] = {"victims": [], "money_vnd": 216_000_000, "money_basis": "quoted", "due_in_h": 30}
    base.update(kw)
    return H.sig("co_hoi_ket_noi", **base)


def test_d5_big_opportunity_near_deadline_unconfirmed_group_is_amber_not_off() -> None:
    lv = H.compute(_opp(), ctx=None)
    assert (lv.level, lv.delivery, lv.candidate) == ("amber", "digest", levels.G5) and "big_opportunity_no_catalog" in lv.reasons
    urgent = H.compute(_opp(due_in_h=None, flags={"explicit_urgent": True}), ctx=None)
    assert urgent.level == "amber"


def test_d5_floor_needs_big_quoted_money_and_near_deadline() -> None:
    assert H.compute(_opp(due_in_h=None), ctx=None).level == "off"                          # không hạn, không gấp
    assert H.compute(_opp(due_in_h=72), ctx=None).level == "off"                            # hạn xa
    assert H.compute(_opp(due_in_h=-2), ctx=None).level == "off"                            # đã trôi
    assert H.compute(_opp(money_basis="estimated"), ctx=None).level == "off"                # tiền ước tính một mình không đủ
    assert H.compute(_opp(money_vnd=20_000_000), ctx=None).level == "off"                   # chưa tới ngưỡng tiền lớn
    assert H.compute(_opp(opportunity={"touches_value_limit": True}), ctx=None).level == "off"
    assert H.compute(_opp(confidence_content=0.5), ctx=None).level != "amber"               # chưa chắc ⇒ không nâng
    assert H.compute(_opp(), ctx="gia_dinh").level == "off"                                 # Sếp đã xác nhận bối cảnh tắt loại này


def test_d5_red_gate5_still_needs_catalog() -> None:
    assert H.compute(_opp(), ctx=None).level != "red"
    assert H.compute(_opp(), ctx="kinh_doanh").level != "red"
    lv = H.compute(_opp(opportunity={"catalog_score": 90}), ctx="kinh_doanh")
    assert (lv.level, lv.gate) == ("red", levels.G5)


# ─── Gen quyết D-T3 (2026-10-26, fail-high): nhóm không rõ cỡ ⇒ cổng an toàn/lừa đảo coi như vòng gần + khách lõi ──

def test_gen_dt3_unknown_size_group_customer_emergency_is_red() -> None:
    def sk(**th: Any) -> levels.SignalIn:
        return H.sig(levels.SK, flags={"emergency": True}, victims=[H.person("c1", customer=True)],
                     thread={"type": "group", "owner_in_thread": True, "agent_in_thread": True, **th})

    assert levels.owner_side(sk(size=40), H.policy("kinh_doanh")).safety
    unk = levels.owner_side(sk(size=0, size_known=False), H.policy("kinh_doanh"))
    assert unk.safety
    assert (H.compute(sk(size=0, size_known=False)).level, H.compute(sk(size=0, size_known=False)).gate) == ("red", levels.G2)
    assert H.compute(sk(size=0, size_known=False), ctx=None).level == "red"     # nhóm chưa xác nhận, không rõ cỡ: vẫn fail-high
    assert H.compute(sk(size=40)).level == "red"
    assert H.compute(sk(size=120)).level == "amber"                               # cỡ ĐÃ BIẾT > 50 không gần: giữ luật cũ


async def test_pt1_generic_safety_victim_marked_customer_is_red(db: AsyncSession) -> None:
    """P-T1 (G-KB sau T14, kb3 S19): detector generic dựng nạn nhân theo CHUNG hàm vai người — khách Sếp đã đánh dấu (person_type=customer)
    báo bình nóng lạnh rò điện trong nhóm kinh doanh 40 người ⇒ 🔴 cổng an toàn."""
    from gh.biz.intel.detectors import generic

    w = await T12._world(db, size=40, business=True)
    pid, iid, ent = await T12._person(db, w, "Chị Hiền khách", ptype="customer")
    now = T12.NOW
    raw = await mk_raw(db, w.org, w.channel, identity=iid, body="bình nóng lạnh nhà chị mở vòi thấy tê tê tay, chắc rò điện em ơi",
                       group=w.group, at=now - timedelta(minutes=20))
    await T12._unit(db, w, raw, levels.SK, person=pid, flags={"emergency": True}, people=[("nguoi_bi_anh_huong", ent)], gate=levels.G2,
                    harm="nguy cơ điện giật")
    out = await generic.project_units(db, w.org, now)
    await db.commit()
    assert [(s.level, s.gate) for s in out] == [("red", levels.G2)]
