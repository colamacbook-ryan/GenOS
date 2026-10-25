# ruff: noqa: E501
"""v0.1.76 (gói T7) — W5 Rủi ro & lừa đảo trên DB thật: ba ca phải 🔴 (mạo danh NCC đòi đổi STK, người lạ nhờ ứng tiền, đơn lớn không cọc
giục gửi ngay), ca OTP/thẻ đang diễn ra (thân tin + OCR), một vụ một 🔴 (gộp theo `case_key`), không bao giờ hạ 🔴 khi chưa có xác nhận
an toàn ((d2) không đồng ý / lỗi), STK đã xác minh = an toàn, mâu thuẫn `payment_account` qua `mem.disputes` (không chiếu đôi), STK lần đầu
chỉ ghi lịch sử, mồi phishing tối đa 🟠."""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gh.biz.intel import levels, signals
from gh.biz.intel.detectors import fraud
from gh.biz.mem import facts
from tests.fraud_helpers import ACCT_NEW, ACCT_OLD, fake_call, world


async def _alerts(db: AsyncSession, org: object) -> list[object]:
    return list((await db.execute(text("""SELECT id, level, gate, candidate, delivery, reasons, pushed_at, facts, level_input
                                         FROM biz.alerts WHERE org_id = :o AND kind = 'rui_ro_lua_dao' ORDER BY created_at"""), {"o": org})).all())


async def _ncc_world(db: AsyncSession) -> tuple[object, object, object]:
    w = await world(db)
    await w.group("kh", "Đại lý thép miền Nam", 120)
    ncc, ncc_iid = await w.person("Hùng Thép Hoà Phát", ptype="supplier", group="kh", history_days_ago=40)
    await db.execute(text("""INSERT INTO biz.payment_identifiers (org_id, person_id, digest, last3, bank, source, created_at)
                             VALUES (:o, :p, :d, '321', 'VCB', 'chat', :ca)"""),
                     {"o": w.org, "p": ncc, "d": facts.payment_digest(ACCT_OLD), "ca": w.now - timedelta(days=40)})
    return w, ncc, ncc_iid


async def test_fraud_impersonated_supplier_changes_account_is_red(db: AsyncSession) -> None:
    w, _ncc, _ = await _ncc_world(db)
    _scam, scam_iid = await w.person("Hùng Thép Hoà Phát", group="kh", joined_days_ago=1)
    await w.raw(f"Anh ơi bên em đổi số tài khoản nhận tiền sang {ACCT_NEW} MB nhé, anh chuyển vào số này giúp em", sender=scam_iid, group="kh")
    out = await fraud.run(db, w.org, w.now)
    assert len(out) == 1
    s = out[0]
    assert (s.level, s.gate, s.delivery) == ("red", levels.G1, "push_now")
    li = levels.SignalIn.model_validate((await _alerts(db, w.org))[0].level_input)
    assert fraud.H_IMP in li.hard_rule_hit and li.flags.account_takeover and li.flags.money_request
    assert li.impersonated and any(v.is_owner for v in li.victims)


async def test_fraud_stranger_asks_owner_to_advance_money_is_red(db: AsyncSession) -> None:
    w = await world(db, owner_name="Minh")
    await w.group("kh", "Khách sỉ", 150)
    _p, iid = await w.person("Tuấn Kiệt", group="kh", joined_days_ago=2)
    await w.raw("Anh Minh ơi ứng giúp em 5 triệu, chiều em trả liền ạ", sender=iid, group="kh")
    out = await fraud.run(db, w.org, w.now)
    assert len(out) == 1 and (out[0].level, out[0].gate, out[0].delivery) == ("red", levels.G1, "push_now")


async def test_fraud_stranger_money_request_small_group_red_and_one_case(db: AsyncSession) -> None:
    w = await world(db)
    await w.group("gd", "Hội phụ huynh lớp 3A", 25)
    _p, iid = await w.person("Lan Anh", group="gd", joined_days_ago=1)
    await w.raw("Mọi người ơi cho em mượn 2 triệu, chuyển giúp em gấp với", sender=iid, group="gd", at=w.now - timedelta(minutes=5))
    first = await fraud.run(db, w.org, w.now)
    assert len(first) == 1 and first[0].level == "red" and first[0].delivery == "push_now"
    pushed = first[0].pushed_at
    await w.raw("Ai ứng giúp em 3 triệu với, em cần gấp lắm", sender=iid, group="gd")
    second = await fraud.run(db, w.org, w.now + timedelta(minutes=1))
    assert len(second) == 1 and second[0].id == first[0].id
    assert second[0].level == "red" and second[0].delivery == "merge" and second[0].pushed_at == pushed
    assert len(await _alerts(db, w.org)) == 1
    assert await fraud.run(db, w.org, w.now + timedelta(minutes=2)) == []          # chạy lại: không trùng


async def test_fraud_big_order_no_deposit_rush_is_red(db: AsyncSession) -> None:
    w = await world(db)
    _p, iid = await w.person("Anh Long Sài Gòn")
    await w.raw("Em lấy 200 thùng nước suối, không cọc nha, gửi ngay trong hôm nay giúp em", sender=iid, group=None)
    out = await fraud.run(db, w.org, w.now)
    assert len(out) == 1 and (out[0].level, out[0].gate, out[0].delivery) == ("red", levels.G1, "push_now")
    a = (await _alerts(db, w.org))[0]
    assert "goods_no_deposit_rush" in a.facts["fraud_rules"]


async def test_fraud_known_customer_big_order_is_not_fraud(db: AsyncSession) -> None:
    w = await world(db)
    await w.group("kh", "Khách quen", 40)
    _p, iid = await w.person("Chị Hạnh", ptype="customer", group="kh", history_days_ago=60)
    await w.raw("Chị lấy 200 thùng, cọc sau nha em, gửi ngay trong hôm nay giúp chị", sender=iid, group="kh")
    assert await fraud.run(db, w.org, w.now) == []


async def test_fraud_card_otp_after_form_body_and_ocr_are_red(db: AsyncSession) -> None:
    w = await world(db)
    await w.group("vip", "Khách VIP", 80)
    _p, iid = await w.person("Thư Ngân Hàng", ptype="customer", group="vip", history_days_ago=30)
    await w.raw("Mình điền rồi nè, nó hỏi thêm số thẻ với mã OTP để xác minh, hơi lạ ta", sender=iid, group="vip")
    _s, iid2 = await w.person("Nhi quản lý", ptype="staff", group="vip", history_days_ago=30)
    raw = await w.raw("[ảnh]", sender=iid2, group="vip", kind="image")
    await w.ocr(raw, "Em lỡ nhập mã OTP rồi, tài khoản bị trừ 15.000.000đ")
    out = await fraud.run(db, w.org, w.now)
    assert len(out) == 2 and all((s.level, s.gate) == ("red", levels.G1) for s in out)
    ev = (await db.execute(text("SELECT evidence FROM biz.alerts WHERE org_id = :o"), {"o": w.org})).scalars().all()
    assert any(e["source_field"] == "ocr" for evs in ev for e in evs)


async def test_fraud_red_never_demoted_by_d2_disagree_or_error(db: AsyncSession) -> None:
    for call in (fake_call("disagree"), fake_call(error=True)):
        w = await world(db)
        await w.group("gd", "Nhóm nhỏ", 20)
        _p, iid = await w.person("Người mới", group="gd", joined_days_ago=1)
        await w.raw("Chị ơi cho em vay 5 triệu, chuyển giúp em ngay", sender=iid, group="gd")
        out = await fraud.run(db, w.org, w.now, call=call)
        assert len(out) == 1 and out[0].level == "red" and out[0].gate == levels.G1
        assert call.calls, "(d2) phải được gọi"
        a = (await _alerts(db, w.org))[0]
        assert a.level == "red"
        assert ("d2_disagree" in a.reasons) == (not a.facts["d2"]["error"])


async def test_fraud_verified_account_is_safe_and_verify_resolves_case(db: AsyncSession) -> None:
    w, ncc, ncc_iid = await _ncc_world(db)
    await w.raw(f"Bên em đổi số tài khoản sang {ACCT_NEW} MB, anh chuyển vào số này nhé", sender=ncc_iid, group="kh")
    out = await fraud.run(db, w.org, w.now)
    assert len(out) == 1 and out[0].level == "red"
    pid = (await db.execute(text("SELECT id FROM biz.payment_identifiers WHERE org_id = :o AND digest = :d"),
                            {"o": w.org, "d": facts.payment_digest(ACCT_NEW)})).scalar_one()
    uid = (await db.execute(text("INSERT INTO core.users (org_id, email, display_name, password_hash) VALUES (:o, 'sep@x.vn', 'Sếp', 'x') RETURNING id"),
                            {"o": w.org})).scalar_one()
    try:
        await fraud.verify_identifier(db, w.org, pid, user_id=uid, pin_verified=False, now=w.now)
        raise AssertionError("phải cần Console + PIN")
    except fraud.FraudError as e:
        assert "PIN" in str(e)
    closed = await fraud.verify_identifier(db, w.org, pid, user_id=uid, pin_verified=True, now=w.now)
    assert [c.id for c in closed] == [out[0].id] and closed[0].level == "grey" and closed[0].status == "resolved"
    await db.execute(text("UPDATE biz.payment_identifiers SET person_id = :p WHERE id = :i"), {"p": ncc, "i": pid})
    await w.raw(f"Anh chuyển vào số {ACCT_NEW} MB giúp em nha", sender=ncc_iid, group="kh")
    assert await fraud.run(db, w.org, w.now + timedelta(minutes=1)) == []        # STK Sếp đã xác minh ⇒ không báo


async def test_fraud_verify_identifier_idor_other_org(db: AsyncSession) -> None:
    w, _ncc, _ = await _ncc_world(db)
    other = await world(db)
    pid = (await db.execute(text("SELECT id FROM biz.payment_identifiers WHERE org_id = :o LIMIT 1"), {"o": w.org})).scalar_one()
    uid = (await db.execute(text("INSERT INTO core.users (org_id, email, display_name, password_hash) VALUES (:o, 'b@x.vn', 'B', 'x') RETURNING id"),
                            {"o": other.org})).scalar_one()
    try:
        await fraud.verify_identifier(db, other.org, pid, user_id=uid, pin_verified=True, now=w.now)
        raise AssertionError("IDOR")
    except fraud.FraudError as e:
        assert "không tìm thấy" in str(e)
    assert (await db.execute(text("SELECT verified_at FROM biz.payment_identifiers WHERE id = :i"), {"i": pid})).scalar_one() is None


async def test_fraud_payment_account_dispute_projected_once(db: AsyncSession) -> None:
    w, ncc, ncc_iid = await _ncc_world(db)
    raw0 = await w.raw(f"STK bên em {ACCT_OLD} VCB nhé anh", sender=ncc_iid, group="kh", at=w.now - timedelta(days=10))
    del raw0
    first = await fraud.run(db, w.org, w.now - timedelta(days=10) + timedelta(minutes=2))
    assert first == []                                                            # STK đã biết, không đổi ⇒ chỉ lịch sử
    await w.raw(f"Từ nay anh chuyển vào tài khoản mới {ACCT_NEW} MB giúp em", sender=ncc_iid, group="kh")
    out = await fraud.run(db, w.org, w.now)
    assert len(out) == 1 and out[0].level == "red"
    d = (await db.execute(text("SELECT id, projected_signal_id FROM mem.disputes WHERE org_id = :o AND predicate = 'payment_account'"),
                          {"o": w.org})).one()
    assert d.projected_signal_id == out[0].id
    assert await signals.project_disputes(db, w.org, w.now) == []                 # không chiếu đôi
    assert len(await _alerts(db, w.org)) == 1


async def test_fraud_first_seen_account_only_history(db: AsyncSession) -> None:
    w = await world(db)
    await w.group("kh", "Khách", 30)
    p, iid = await w.person("Công ty Tâm An", ptype="customer", group="kh", history_days_ago=20)
    await w.raw(f"STK công ty: {ACCT_NEW} Vietcombank ạ", sender=iid, group="kh")
    assert await fraud.run(db, w.org, w.now) == []
    r = (await db.execute(text("SELECT person_id, digest, last3 FROM biz.payment_identifiers WHERE org_id = :o"), {"o": w.org})).one()
    assert r.person_id == p and r.last3 == "789" and len(r.digest) == 64


async def test_fraud_phishing_bait_at_most_amber(db: AsyncSession) -> None:
    w = await world(db)
    await w.group("gd", "Gia đình nhỏ", 6)
    _p, iid = await w.person("Lan", group="gd", history_days_ago=100)
    await w.raw("MỪNG 20/10 – NUTRIMILK TẶNG 500.000đ! Nhận quà tại nutrimilk-qua2010.com", sender=iid, group="gd")
    out = await fraud.run(db, w.org, w.now)
    assert len(out) == 1 and out[0].level in ("amber", "grey") and out[0].gate is None


async def test_fraud_poisoning_impersonator_repeats_account_still_caught(db: AsyncSession) -> None:
    """Tấn công "đầu độc lịch sử": kẻ mạo danh gửi STK của nó (lần 1 không đòi tiền) rồi lần 2 đòi chuyển vào đúng số đó ⇒ vẫn bị bắt;
    STK của kẻ mạo danh KHÔNG BAO GIỜ thành `payment_account` của thực thể bị mạo danh, không thành "đã biết"."""
    w, ncc, _ = await _ncc_world(db)
    _scam, scam_iid = await w.person("Hùng Thép Hoà Phát", group="kh", joined_days_ago=1)
    await w.raw(f"STK bên em {ACCT_NEW} MB nha anh", sender=scam_iid, group="kh", at=w.now - timedelta(minutes=20))
    await fraud.run(db, w.org, w.now - timedelta(minutes=19))
    await w.raw(f"Anh chuyển tiền hàng vào STK {ACCT_NEW} MB giúp em nhé", sender=scam_iid, group="kh", at=w.now - timedelta(minutes=10))
    out = await fraud.run(db, w.org, w.now - timedelta(minutes=9))
    assert len(out) == 1 and (out[0].level, out[0].gate) == ("red", levels.G1)
    await w.raw(f"Anh chuyển vào {ACCT_NEW} MB luôn giúp em", sender=scam_iid, group="kh")
    again = await fraud.run(db, w.org, w.now)
    assert len(again) == 1 and again[0].id == out[0].id and again[0].level == "red"           # lần nữa: vẫn bắt, cùng vụ
    new_d = facts.payment_digest(ACCT_NEW)
    ncc_ent = (await db.execute(text("SELECT id FROM mem.entities WHERE person_id = :p"), {"p": ncc})).scalar_one()
    poisoned = (await db.execute(text("""SELECT count(*) FROM mem.facts WHERE subject_id = :e AND predicate = 'payment_account'
                                         AND object_value ->> 'digest' = :d"""), {"e": ncc_ent, "d": new_d})).scalar_one()
    assert poisoned == 0
    from gh.biz.intel.detectors.fraud import Who, _history

    known, _v, _a = await _history(db, w.org, Who(ref="x", person_id=ncc, entity_id=ncc_ent), w.now + timedelta(days=30))
    assert new_d not in known
    pi = (await db.execute(text("SELECT person_id FROM biz.payment_identifiers WHERE org_id = :o AND digest = :d"), {"o": w.org, "d": new_d})).scalar_one()
    assert pi != ncc                                                              # gắn người gửi thật


async def test_fraud_poisoning_fresh_account_not_known_and_suspicious_never_asserted(db: AsyncSession) -> None:
    """STK mới thấy (< 7 ngày) chưa xác minh không phải "đã biết"; tin đã kích luật lừa đảo không để STK thành `asserted`."""
    w = await world(db)
    await w.group("kh", "Khách", 120)
    p, iid = await w.person("Đối tác Bình", ptype="partner", group="kh", history_days_ago=60)
    await w.raw(f"Anh chuyển vào tài khoản mới {ACCT_NEW} MB giúp em, số cũ bị khoá rồi", sender=iid, group="kh", at=w.now - timedelta(hours=2))
    out = await fraud.run(db, w.org, w.now - timedelta(hours=2) + timedelta(minutes=1))
    assert len(out) == 1 and out[0].level == "red"
    ent = (await db.execute(text("SELECT id FROM mem.entities WHERE person_id = :p"), {"p": p})).scalar_one()
    assert (await db.execute(text("SELECT count(*) FROM mem.facts WHERE subject_id = :e AND predicate = 'payment_account' AND status = 'asserted'"),
                             {"e": ent})).scalar_one() == 0
    await w.raw(f"Bên em đổi STK sang {ACCT_OLD} VCB, anh chuyển vào số này", sender=iid, group="kh")
    out2 = await fraud.run(db, w.org, w.now)
    assert len(out2) == 1 and out2[0].level == "red"                              # STK lần trước không "đầu độc" thành lịch sử
