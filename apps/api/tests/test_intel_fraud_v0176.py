# ruff: noqa: E501
"""v0.1.76 (gói T7) — W5 Rủi ro & lừa đảo trên DB thật: ba ca phải 🔴 (mạo danh NCC đòi đổi STK, người lạ nhờ ứng tiền, đơn lớn không cọc
giục gửi ngay), ca OTP/thẻ đang diễn ra (thân tin + OCR), một vụ một 🔴 (gộp theo `case_key`), không bao giờ hạ 🔴 khi chưa có xác nhận
an toàn ((d2) không đồng ý / lỗi), STK đã xác minh = an toàn, mâu thuẫn `payment_account` qua `mem.disputes` (không chiếu đôi), STK lần đầu
chỉ ghi lịch sử, mồi phishing tối đa 🟠."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import orjson
import pytest
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
    assert (await db.execute(text("SELECT count(*) FROM biz.payment_identifiers WHERE org_id = :o AND digest = :d"),
                             {"o": w.org, "d": facts.payment_digest(ACCT_NEW)})).scalar_one() == 0      # tin đã kích luật: không vào lịch sử
    uid = (await db.execute(text("INSERT INTO core.users (org_id, email, display_name, password_hash) VALUES (:o, 'sep@x.vn', 'Sếp', 'x') RETURNING id"),
                            {"o": w.org})).scalar_one()
    try:
        await fraud.verify_signal_payee(db, w.org, out[0].id, user_id=uid, pin_verified=False, now=w.now)
        raise AssertionError("phải cần Console + PIN")
    except fraud.FraudError as e:
        assert "PIN" in str(e)
    closed = await fraud.verify_signal_payee(db, w.org, out[0].id, user_id=uid, pin_verified=True, now=w.now)
    pid = (await db.execute(text("SELECT id FROM biz.payment_identifiers WHERE org_id = :o AND digest = :d AND verified_at IS NOT NULL"),
                            {"o": w.org, "d": facts.payment_digest(ACCT_NEW)})).scalar_one()
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
    pi = (await db.execute(text("SELECT person_id FROM biz.payment_identifiers WHERE org_id = :o AND digest = :d"), {"o": w.org, "d": new_d})).scalars().all()
    assert ncc not in pi                                                          # không bao giờ gắn với người bị mạo danh


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


# ─── review T7 (M2, M5, M6, m1, m2) ───────────────────────────────────────────────────────────────────────────────────────

async def test_fraud_split_messages_grouped_is_red(db: AsyncSession) -> None:
    """M2: "đổi tài khoản rồi" → "0123456789 MB" → "anh chuyển khoản giúp em" (3 tin, cùng người, ≤ 10 phút) ⇒ 🔴."""
    w, _ncc, ncc_iid = await _ncc_world(db)
    await w.raw("Anh ơi bên em đổi tài khoản nhận tiền rồi nhé", sender=ncc_iid, group="kh", at=w.now - timedelta(minutes=3))
    await w.raw(f"{ACCT_NEW} MB", sender=ncc_iid, group="kh", at=w.now - timedelta(minutes=2))
    await w.raw("Anh chuyển khoản tiền hàng giúp em hôm nay nhé", sender=ncc_iid, group="kh", at=w.now - timedelta(minutes=1))
    out = await fraud.run(db, w.org, w.now)
    assert out and any((s.level, s.gate) == ("red", levels.G1) for s in out)
    assert all(s.level == "red" for s in out) and len({s.id for s in out}) == 1


async def test_fraud_flood_401_then_scam_caught_first_run(db: AsyncSession) -> None:
    """M5: 401 tin thường rồi 1 tin lừa đảo ⇒ lượt ĐẦU bắt được (mới nhất trước + watermark); lượt sau không quét lại tin cũ."""
    w = await world(db, owner_name="Minh")
    await w.group("kh", "Khách", 150)
    _p, ok = await w.person("Khách quen", group="kh", history_days_ago=40)
    _s, siid = await w.person("Tuấn Kiệt", group="kh", joined_days_ago=1)
    for i in range(401):
        await w.raw(f"hàng về chưa anh {i}", sender=ok, group="kh", at=w.now - timedelta(minutes=5))
    await w.raw("Anh Minh ơi ứng giúp em 5 triệu, chiều em trả liền ạ", sender=siid, group="kh")
    store = fraud.SeenStore()
    out = await fraud.run_and_commit(db, w.org, w.now, seen=store)
    assert len(out) == 1 and out[0].level == "red"
    calls: list[str] = []
    real = fraud.prepare

    async def spy(*a: object, **k: object) -> object:
        calls.append(a[2].body)  # type: ignore[attr-defined]
        return await real(*a, **k)  # type: ignore[arg-type]

    import pytest as _pt

    mp = _pt.MonkeyPatch()
    mp.setattr(fraud, "prepare", spy)
    try:
        await fraud.run_and_commit(db, w.org, w.now + timedelta(minutes=1), seen=store)
        await fraud.run_and_commit(db, w.org, w.now + timedelta(minutes=2), seen=store)
    finally:
        mp.undo()
    # chỉ những tin CHƯA quét ở lượt đầu (402 tin trong cửa sổ + tin lịch sử của helper − 400 đã quét), mỗi tin một lần.
    # 401 tin cùng received_at (một transaction) ⇒ thứ tự hoà do s.id (uuid) quyết, nên 2 tin "hàng về" còn lại là bất kỳ —
    # chỉ khẳng định bất biến: đúng 3 tin, không tin nào quét hai lần, có tin lịch sử.
    assert len(calls) == 3 and len(set(calls)) == 3 and "chào cả nhà" in calls, calls
    assert sum(c.startswith("hàng về chưa anh ") for c in calls) == 2, calls


async def test_fraud_run_budget_leaves_rest_for_next_run(db: AsyncSession) -> None:
    """M5: hết trần thời gian ⇒ dừng, tin chưa quét KHÔNG bị đánh dấu, lượt sau bắt được."""
    w = await world(db)
    await w.group("gd", "Nhóm nhỏ", 20)
    _p, iid = await w.person("Người mới", group="gd", joined_days_ago=1)
    await w.raw("Cho em vay 3tr, mai em gửi lại", sender=iid, group="gd")
    store = fraud.SeenStore()
    assert await fraud.run(db, w.org, w.now, seen=store, budget_s=-1) == []
    out = await fraud.run(db, w.org, w.now, seen=store)
    assert len(out) == 1 and out[0].level == "red"


async def test_fraud_mutation_history_excludes_any_flagged_digest(db: AsyncSession) -> None:
    """M6 (đột biến 1): STK THỨ HAI của tin bị gắn cờ (không phải payee) nằm trong `fraud_digests` ⇒ dòng lịch sử cũ của nó KHÔNG "đã biết"."""
    from gh.biz.intel.detectors.fraud import Who, _history

    w = await world(db)
    await w.group("kh", "Khách", 120)
    p, iid = await w.person("Đối tác Bình", ptype="partner", group="kh", history_days_ago=60)
    await w.raw(f"Bên em đổi STK sang {ACCT_NEW} MB, hoặc {ACCT_OLD} VCB cũng được, anh chuyển vào số này", sender=iid, group="kh")
    out = await fraud.run(db, w.org, w.now)
    assert len(out) == 1
    a = (await _alerts(db, w.org))[0]
    assert a.facts["payee_digest"] == facts.payment_digest(ACCT_NEW) and facts.payment_digest(ACCT_OLD) in a.facts["fraud_digests"]
    await db.execute(text("""INSERT INTO biz.payment_identifiers (org_id, person_id, digest, last3, source, created_at)
                             VALUES (:o, :p, :d, '321', 'chat', :ca)"""), {"o": w.org, "p": p, "d": facts.payment_digest(ACCT_OLD), "ca": w.now - timedelta(days=30)})
    ent = (await db.execute(text("SELECT id FROM mem.entities WHERE person_id = :p"), {"p": p})).scalar_one()
    known, _v, _a = await _history(db, w.org, Who(ref="x", person_id=p, entity_id=ent), w.now + timedelta(days=1))
    assert facts.payment_digest(ACCT_OLD) not in known


async def test_fraud_mutation_fraud_digests_accumulate_across_case(db: AsyncSession) -> None:
    """M6 (đột biến 2): hai tin cùng vụ (cách > 10 phút, không gom) mang hai STK ⇒ `fraud_digests` giữ CẢ HAI (upsert ghi đè `facts`)."""
    from gh.biz.intel.detectors.fraud import Who, _history

    w = await world(db)
    await w.group("gd", "Nhóm nhỏ", 20)
    _s, iid = await w.person("Người lạ", group="gd", joined_days_ago=1)
    await w.raw(f"Cho em mượn 2 triệu, chuyển vào STK {ACCT_OLD} VCB giúp em", sender=iid, group="gd", at=w.now - timedelta(minutes=30))
    first = await fraud.run(db, w.org, w.now - timedelta(minutes=29))
    await w.raw(f"Chuyển vào STK {ACCT_NEW} MB nhé chị, em cần gấp", sender=iid, group="gd")
    second = await fraud.run(db, w.org, w.now)
    assert len(first) == 1 and len(second) == 1 and first[0].id == second[0].id
    fx = (await _alerts(db, w.org))[0].facts
    assert {facts.payment_digest(ACCT_OLD), facts.payment_digest(ACCT_NEW)} <= set(fx["fraud_digests"])
    legit, _ = await w.person("Công ty thật", ptype="customer", group="gd", history_days_ago=90)
    await db.execute(text("""INSERT INTO biz.payment_identifiers (org_id, person_id, digest, last3, source, created_at)
                             VALUES (:o, :p, :d, '321', 'chat', :ca) ON CONFLICT (org_id, digest) DO UPDATE SET person_id = EXCLUDED.person_id,
                             created_at = EXCLUDED.created_at"""), {"o": w.org, "p": legit, "d": facts.payment_digest(ACCT_OLD), "ca": w.now - timedelta(days=30)})
    ent = (await db.execute(text("SELECT id FROM mem.entities WHERE person_id = :p"), {"p": legit})).scalar_one()
    known, _v, _a = await _history(db, w.org, Who(ref="x", person_id=legit, entity_id=ent), w.now + timedelta(days=1))
    assert facts.payment_digest(ACCT_OLD) not in known


async def test_fraud_mutation_no_payment_account_for_stranger(db: AsyncSession) -> None:
    """M6 (đột biến 3): người lạ gửi STK (không đòi tiền) ⇒ KHÔNG ghi `payment_account` (không có fact nào cho thực thể người lạ)."""
    w = await world(db)
    await w.group("kh", "Khách", 120)
    s, iid = await w.person("Người lạ", group="kh", joined_days_ago=1)
    await w.raw(f"STK em {ACCT_NEW} MB nha", sender=iid, group="kh")
    assert await fraud.run(db, w.org, w.now) == []
    ent = (await db.execute(text("SELECT id FROM mem.entities WHERE person_id = :p"), {"p": s})).scalar_one()
    assert (await db.execute(text("SELECT count(*) FROM mem.facts WHERE subject_id = :e AND predicate = 'payment_account'"),
                             {"e": ent})).scalar_one() == 0


async def test_fraud_verify_payee_does_not_close_credentials_case(db: AsyncSession) -> None:
    """m1: xác minh STK chỉ đóng vụ đổi STK / mâu thuẫn STK, KHÔNG đóng vụ lộ OTP/thẻ dù cùng STK."""
    w, _ncc, ncc_iid = await _ncc_world(db)
    _st, staff = await w.person("Nhi kế toán", ptype="staff", group="kh", history_days_ago=30)
    await w.raw(f"Em lỡ nhập mã OTP rồi, nó bảo chuyển vào STK {ACCT_NEW} MB", sender=staff, group="kh", at=w.now - timedelta(minutes=30))
    cred = await fraud.run(db, w.org, w.now - timedelta(minutes=29))
    await w.raw(f"Bên em đổi STK sang {ACCT_NEW} MB, anh chuyển vào số này", sender=ncc_iid, group="kh")
    stk = await fraud.run(db, w.org, w.now)
    assert len(cred) == 1 and len(stk) == 1 and cred[0].id != stk[0].id
    uid = (await db.execute(text("INSERT INTO core.users (org_id, email, display_name, password_hash) VALUES (:o, 'v@x.vn', 'S', 'x') RETURNING id"),
                            {"o": w.org})).scalar_one()
    closed = await fraud.verify_signal_payee(db, w.org, stk[0].id, user_id=uid, pin_verified=True, now=w.now)
    assert [c.id for c in closed] == [stk[0].id]
    st = (await db.execute(text("SELECT status, level FROM biz.alerts WHERE id = :i"), {"i": cred[0].id})).one()
    assert st.status == "open" and st.level == "red"


async def test_fraud_hijacked_account_later_account_not_recorded(db: AsyncSession) -> None:
    """m2: tài khoản NCC (đã biết) vừa dính tín hiệu W5 ⇒ STK trong tin SAU (không kèm đòi tiền) không vào fact/lịch sử của NCC."""
    w, ncc, ncc_iid = await _ncc_world(db)
    await w.raw(f"Bên em đổi STK sang {ACCT_NEW} MB, anh chuyển vào số này", sender=ncc_iid, group="kh", at=w.now - timedelta(minutes=30))
    assert len(await fraud.run(db, w.org, w.now - timedelta(minutes=29))) == 1
    await w.raw("STK em 5566778899 TCB nha", sender=ncc_iid, group="kh")
    await fraud.run(db, w.org, w.now)
    d = facts.payment_digest("5566778899")
    ent = (await db.execute(text("SELECT id FROM mem.entities WHERE person_id = :p"), {"p": ncc})).scalar_one()
    assert (await db.execute(text("""SELECT count(*) FROM mem.facts WHERE subject_id = :e AND predicate = 'payment_account'
                                     AND object_value ->> 'digest' = :d"""), {"e": ent, "d": d})).scalar_one() == 0
    assert (await db.execute(text("SELECT count(*) FROM biz.payment_identifiers WHERE org_id = :o AND digest = :d"),
                             {"o": w.org, "d": d})).scalar_one() == 0


async def test_fraud_watermark_only_after_commit_rollback_rescans(db: AsyncSession) -> None:
    """Review rv3 (MAJOR): dấu "đã quét" chỉ có SAU commit. Lượt bị rollback (commit lỗi / wait_for huỷ / worker chết) ⇒ lượt sau vẫn bắt."""
    w = await world(db, owner_name="Minh")
    await w.group("kh", "Khách sỉ", 150)
    _p, iid = await w.person("Tuấn Kiệt", group="kh", joined_days_ago=2)
    await w.raw("Anh Minh ơi ứng giúp em 5 triệu, chiều em trả liền ạ", sender=iid, group="kh")
    await db.commit()
    seen = fraud.SeenStore()
    assert len(await fraud.run(db, w.org, w.now, seen=seen)) == 1
    await db.rollback()
    out2 = await fraud.run(db, w.org, w.now + timedelta(minutes=1), seen=seen)
    await db.commit()
    assert len(out2) == 1 and out2[0].level == "red"
    # run_and_commit: commit lỗi ⇒ không đánh dấu
    _q, iid2 = await w.person("Người lạ 2", group="kh", joined_days_ago=1)
    await w.raw("Anh Minh ơi cho em mượn 3 triệu, chuyển giúp em gấp", sender=iid2, group="kh")
    await db.commit()
    seen2 = fraud.SeenStore()
    real_commit = db.commit

    async def boom() -> None:
        raise ConnectionError("mất kết nối lúc commit")

    db.commit = boom  # type: ignore[method-assign]
    try:
        await fraud.run_and_commit(db, w.org, w.now + timedelta(minutes=2), seen=seen2)
        raise AssertionError("commit phải lỗi")
    except ConnectionError:
        pass
    finally:
        db.commit = real_commit  # type: ignore[method-assign]
    await db.rollback()
    again = await fraud.run_and_commit(db, w.org, w.now + timedelta(minutes=3), seen=seen2)
    assert len(again) == 1 and again[0].level == "red"


async def test_fraud_poison_message_dropped_after_max_fails(db: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> None:
    """Tin làm hỏng bộ xử lý: thử tối đa `MAX_FAILS` lượt rồi bỏ (không quét lại vô hạn); tin khác vẫn chạy."""
    w = await world(db)
    await w.group("g", "Nhóm nhỏ", 20)
    _p, iid = await w.person("Người lạ", group="g", joined_days_ago=1)
    bad = await w.raw("TIN_HONG cho em mượn 2 triệu", sender=iid, group="g", at=w.now - timedelta(minutes=3))
    real = fraud.prepare
    tries: list[int] = []

    async def flaky(db_: AsyncSession, org: object, row: object, **kw: object) -> object:
        if row.event_id == bad[0]:  # type: ignore[attr-defined]
            tries.append(1)
            raise ValueError("parser hỏng")
        return await real(db_, org, row, **kw)  # type: ignore[arg-type]

    monkeypatch.setattr(fraud, "prepare", flaky)
    seen = fraud.SeenStore()
    for k in range(fraud.MAX_FAILS + 2):
        await fraud.run_and_commit(db, w.org, w.now + timedelta(minutes=k), seen=seen)
    assert len(tries) == fraud.MAX_FAILS


# ─── T12 review BLOCKER 1: câu thường không đẩy 🔴 luật cứng; ứng viên mềm chỉ ghi khi (d2) xác nhận ─────────────────────

FP_STAFF = ["anh đăng nhập app ngân hàng rồi, ngân hàng gửi mã OTP bảo nhập để xác nhận chuyển khoản cho nhà cung cấp",
            "em đăng nhập zalo trên máy tính rồi, nó gửi mã xác nhận về điện thoại bảo nhập",
            "anh đăng nhập vào hệ thống kế toán rồi, cần mật khẩu wifi không anh"]


async def test_fraud_everyday_login_messages_never_hard_red(db: AsyncSession) -> None:
    """Không bộ định tuyến ((d2) không chạy được): ứng viên mềm ghi mức thấp (fail-high nhưng không 🔴, không đẩy); có (d2) không đồng ý ⇒ không gì."""
    for body in FP_STAFF:
        for call in (None, fake_call("disagree")):
            w = await world(db)
            await w.group("nv", "Nhóm NV", 20)
            _p, iid = await w.person("Nhân viên", ptype="staff", group="nv", history_days_ago=60)
            await w.raw(body, sender=iid, group="nv")
            out = await fraud.run(db, w.org, w.now, call=call)
            assert all(s.level != "red" and s.delivery != "push_now" for s in out), body
            assert all(not (a.facts.get("fraud_rules") or []) or "credentials_entered" not in a.facts["fraud_rules"] for a in await _alerts(db, w.org))
            if call is not None:
                assert out == [], body


def _confirm_call(flags: list[str]) -> Any:
    from gh.refinery.extract import ModelReply

    async def call(messages: list[Any]) -> ModelReply:
        return ModelReply(orjson.dumps({"gate": "lua_dao_dang_dien_ra", "flags_confirmed": flags, "harm": {"what": "mất tiền"}, "verdict": "agree",
                                        "reason": "thử"}).decode(), "fake-strong")
    return call


async def test_fraud_soft_credential_candidate_written_only_when_d2_confirms(db: AsyncSession) -> None:
    for call, want in ((fake_call("disagree"), None), (_confirm_call([]), "amber"), (_confirm_call(["credentials_entered"]), "red"), (None, "amber")):
        w = await world(db)
        await w.group("nv", "Nhóm NV", 20)
        _p, iid = await w.person("Nhân viên", ptype="staff", group="nv", history_days_ago=60)
        await w.raw(FP_STAFF[1], sender=iid, group="nv")
        out = await fraud.run(db, w.org, w.now, call=call)
        assert [s.level for s in out] == ([want] if want else [])
        if want == "red":
            assert out[0].gate == levels.G1


async def test_fraud_soft_candidate_not_lost_when_d2_budget_exhausted_or_errors(db: AsyncSession) -> None:
    """Security review: hết trần (d2) lượt này / (d2) lỗi ⇒ KHÔNG đánh dấu "đã quét" (lượt sau còn xử lý); lỗi đủ MAX_FAILS ⇒ ghi mức thấp."""
    w = await world(db)
    await w.group("nv", "Nhóm NV", 20)
    _p, iid = await w.person("Nhân viên", ptype="staff", group="nv", history_days_ago=60)
    await w.raw(FP_STAFF[1], sender=iid, group="nv")
    seen = fraud.SeenStore()
    keys: list[str] = []
    assert await fraud.run(db, w.org, w.now, call=_confirm_call(["credentials_entered"]), max_d2=0, seen=seen, processed=keys) == []
    first = set(keys)                                                    # chỉ tin chào hỏi cũ (không phải ứng viên) được đánh dấu
    out = await fraud.run(db, w.org, w.now, call=_confirm_call(["credentials_entered"]), seen=seen, processed=keys)
    assert [s.level for s in out] == ["red"] and len(set(keys) - first) == 1    # hết trần lượt trước: tin không mất, lượt sau xử lý
    w2 = await world(db)
    await w2.group("nv", "Nhóm NV", 20)
    _p2, iid2 = await w2.person("Nhân viên", ptype="staff", group="nv", history_days_ago=60)
    await w2.raw(FP_STAFF[1], sender=iid2, group="nv")
    seen2 = fraud.SeenStore()
    outs, marked = [], []
    for _ in range(fraud.MAX_FAILS):
        keys2: list[str] = []
        outs.append(await fraud.run(db, w2.org, w2.now, call=fake_call(error=True), seen=seen2, processed=keys2))
        marked.append(len(keys2))
    assert all(o == [] for o in outs[:-1]) and [s.level for s in outs[-1]] == ["amber"]
    assert marked[-1] == marked[0] + 1                                   # chỉ lượt cuối (đủ MAX_FAILS) mới đánh dấu tin


async def test_fraud_soft_candidate_written_low_when_org_safety_budget_exhausted(db: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> None:
    """Security review vòng cuối: (d2) bị chặn vì lý do KHÁC trần của chính lượt (tổ chức hết trần lượt an toàn trong ngày) ⇒ ghi mức thấp
    ngay (≤ 🟠, không cờ), không bỏ mãi."""
    async def denied(*a: Any, **k: Any) -> bool:
        return False

    monkeypatch.setattr(fraud, "_d2_allowed", denied)
    w = await world(db)
    await w.group("nv", "Nhóm NV", 20)
    _p, iid = await w.person("Nhân viên", ptype="staff", group="nv", history_days_ago=60)
    await w.raw(FP_STAFF[1], sender=iid, group="nv")
    out = await fraud.run(db, w.org, w.now, call=_confirm_call(["credentials_entered"]))
    assert [s.level for s in out] == ["amber"]
