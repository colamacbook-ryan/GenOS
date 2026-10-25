# ruff: noqa: E501
"""v0.1.76 (gói T7) — test bảo mật W5: số tài khoản / OTP / số thẻ không bao giờ vào log, thân lỗi, `biz.alerts` (title, summary, facts,
evidence, level_input), `ops.asks.summary`, Nhật ký (`ops.action_log`), `mem.facts`, `biz.payment_identifiers`; mọi đọc/ghi theo `org_id`
(IDOR: tin tổ chức B không bao giờ thành tín hiệu của tổ chức A; lịch sử STK tổ chức A không làm "lịch sử" cho tổ chức B)."""

from __future__ import annotations

import logging
from datetime import timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gh import suspicious
from gh.biz.intel.detectors import fraud
from gh.biz.mem import facts
from tests.fraud_helpers import ACCT_NEW, ACCT_OLD, OTP, fake_call, world

CARD = "4111 1111 1111 1111"


async def _dump_tables(db: AsyncSession) -> str:
    parts: list[str] = []
    for q in ("SELECT title, summary, suggested_action, facts::text, evidence::text, level_input::text, reasons::text FROM biz.alerts",
              "SELECT coalesce(summary, '') FROM ops.asks",
              "SELECT action || coalesce(detail::text, '') || coalesce(target_id, '') FROM ops.action_log",
              "SELECT object_value::text FROM mem.facts", "SELECT digest || coalesce(last3, '') || coalesce(bank, '') FROM biz.payment_identifiers",
              "SELECT coalesce(projected_signal_id::text, '') FROM mem.disputes"):
        for r in (await db.execute(text(q))).all():
            parts.extend(str(x) for x in r)
    return "\n".join(parts)


def _no_secret(blob: str) -> None:
    for secret in (ACCT_NEW, ACCT_OLD, OTP, CARD, CARD.replace(" ", ""), "4111111111111111"):
        assert secret not in blob, f"lộ bí mật: …{secret[-3:]}"


async def test_fraud_security_no_account_otp_in_db_or_logs(db: AsyncSession, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    w = await world(db)
    await w.group("kh", "Khách", 20)
    _o, old_iid = await w.person("Hùng NCC", ptype="supplier", group="kh", history_days_ago=40)
    await w.raw(f"STK bên em {ACCT_OLD} VCB", sender=old_iid, group="kh", at=w.now - timedelta(days=9))
    await fraud.run(db, w.org, w.now - timedelta(days=9) + timedelta(minutes=1))
    _p, iid = await w.person("Người lạ", group="kh", joined_days_ago=1)
    await w.raw(f"Anh ơi chuyển gấp 5tr vào STK {ACCT_NEW} MB, mã OTP {OTP} em gửi rồi đó", sender=iid, group="kh")
    await w.raw(f"Em lỡ nhập số thẻ {CARD} với OTP {OTP} rồi", sender=old_iid, group="kh")
    await w.raw(f"Bên em đổi tài khoản mới {ACCT_NEW} MB, anh chuyển vào số này", sender=old_iid, group="kh")
    out = await fraud.run(db, w.org, w.now, call=fake_call("agree"))
    assert out and all(s.level in ("red", "amber") for s in out)
    _no_secret(await _dump_tables(db))
    _no_secret("\n".join(r.getMessage() for r in caplog.records))


async def test_fraud_security_errors_and_masking_never_carry_numbers(db: AsyncSession) -> None:
    w = await world(db)
    uid = (await db.execute(text("INSERT INTO core.users (org_id, email, display_name, password_hash) VALUES (:o, 'a@x.vn', 'A', 'x') RETURNING id"),
                            {"o": w.org})).scalar_one()
    import uuid as _u

    with pytest.raises(fraud.FraudError) as e:
        await fraud.verify_identifier(db, w.org, _u.uuid4(), user_id=uid, pin_verified=True, now=w.now)
    _no_secret(str(e.value))
    with pytest.raises(fraud.FraudError) as e2:
        fraud.scan(f"STK {ACCT_NEW}", "x")                                       # trường lạ ⇒ lỗi không mang chữ tin
    _no_secret(str(e2.value))
    masked = suspicious.mask_secrets(f"STK {ACCT_NEW} MB, OTP {OTP}, thẻ {CARD} cvv 123, token=abcDEF12345")
    _no_secret(masked)
    assert "789" in masked and "abcDEF12345" not in masked and "cvv 123" not in masked
    # quét thuần không giữ chữ tin, không giữ số
    sc = fraud.scan(f"Bên em đổi tài khoản mới {ACCT_NEW} MB, anh chuyển vào số này")
    _no_secret(repr(sc))
    assert sc.accounts and sc.accounts[0].digest == facts.payment_digest(ACCT_NEW)


async def test_fraud_security_idor_org_isolation(db: AsyncSession) -> None:
    a = await world(db)
    b = await world(db)
    await a.group("g", "Nhóm A", 20)
    await b.group("g", "Nhóm B", 20)
    _pa, ia = await a.person("Hùng NCC", ptype="supplier", group="g", history_days_ago=40)
    await db.execute(text("INSERT INTO biz.payment_identifiers (org_id, person_id, digest, last3, source) VALUES (:o, :p, :d, '321', 'chat')"),
                     {"o": a.org, "p": _pa, "d": facts.payment_digest(ACCT_OLD)})
    _pb, ib = await b.person("Người lạ B", group="g", joined_days_ago=1)
    await b.raw("Cho em mượn 3 triệu, chuyển giúp em gấp", sender=ib, group="g")
    assert await fraud.run(db, a.org, a.now) == []                               # tin của B không thành tín hiệu của A
    out_b = await fraud.run(db, b.org, b.now)
    assert len(out_b) == 1
    n_a = (await db.execute(text("SELECT count(*) FROM biz.alerts WHERE org_id = :o"), {"o": a.org})).scalar_one()
    assert n_a == 0
    # STK tổ chức A đã biết không làm "lịch sử" cho người tổ chức B
    _pb2, ib2 = await b.person("Công ty X", ptype="customer", group="g", history_days_ago=30)
    await b.raw(f"STK công ty {ACCT_OLD} VCB", sender=ib2, group="g")
    await fraud.run(db, b.org, b.now)
    rows = (await db.execute(text("SELECT org_id FROM biz.payment_identifiers WHERE digest = :d"), {"d": facts.payment_digest(ACCT_OLD)})).scalars().all()
    assert sorted(map(str, rows)) == sorted([str(a.org), str(b.org)])
