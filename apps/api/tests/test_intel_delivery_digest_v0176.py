# ruff: noqa: E501
"""v0.1.76 (gói T3) — bản tin Zalo + 🔴 tức thì (`pipes/delivery.py`, `pipes/coverage.py`): bản tin chỉ chứa tín hiệu `chat_ok`
(`console_pin` chỉ là "có N mục cần xem trên Console"), ≤ 10 dòng / 1.500 ký tự, mọi mục có trích dẫn đã che, dòng độ phủ,
"Đã hồi phục", [chạy thử] khi bật bóng, "tắt bản tin" ⇒ "điểm gấp trong ngày", ĐANG KHOÁ ⇒ không gửi gì, idempotent theo khung giờ."""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

from sqlalchemy import text

from gh import health
from gh.biz.intel.pipes import coverage, delivery
from gh.chassis.bus import BRIDGE_OUTBOUND
from gh.db import sessionmaker
from gh.owner_channel import reports
from tests.mem_helpers import mk_org
from tests.owner_helpers import FakeBus, bot_world
from tests.test_intel_delivery_reports_v0176 import _lock
from tests.test_intel_digest_rawtail_v0176 import NOW, _install, _raw, _sig

SM = sessionmaker
EVENING = NOW.replace(hour=17, minute=35)       # khung 17:30 (giờ VN)


async def world(db: Any, *, pin: bool = False) -> tuple[uuid.UUID, dict[str, Any]]:
    org = await mk_org(db)
    await _install(db, org)
    sig = {
        "red_top": await _sig(db, org, "xung_dot_khieu_nai", "c-q15", msg_at=NOW - timedelta(hours=30), customer_of_owner=True, flags={"public": True}),
        "amber": await _sig(db, org, "cau_hoi_cho_xu_ly", "q1", impact_base=2, deadline_at=NOW + timedelta(hours=10)),
    }
    if pin:
        sig["priv"] = await _sig(db, org, "cau_hoi_cho_xu_ly", "q-priv", privacy="console_pin", impact_base=2, deadline_at=NOW + timedelta(hours=10))
        await db.execute(text("UPDATE biz.alerts SET title = 'Đánh giá nhân sự Hùng BÍ MẬT' WHERE id = :i"), {"i": sig["priv"].id})
        await db.commit()
    await bot_world(db, org)
    return org, sig


async def run(org: uuid.UUID, now: Any = EVENING) -> str:
    async with SM()() as db:
        out = await delivery.deliver_digest(db, org, now)
        await db.commit()
    return out


async def outbox(db: Any, kind: str = "digest") -> list[Any]:
    return list((await db.execute(text("SELECT text, refs, severity, sent_at FROM ops.owner_outbox WHERE kind = :k ORDER BY created_at"), {"k": kind})).all())


async def test_digest_is_enqueued_with_quotes_refs_and_coverage_line(db: Any, redis: Any) -> None:
    org, sig = await world(db)
    assert await run(org) == "sent"
    [row] = await outbox(db)
    assert "«abc»" in row.text and "Độ phủ" in row.text and "đọc" in row.text
    assert [r["n"] for r in row.refs] == [1, 2] and row.refs[1]["signal_id"] == str(sig["amber"].id)
    assert len(row.text.splitlines()) <= 10 and len(row.text) <= 1500
    bus = FakeBus()
    assert (await reports.flush(SM(), bus, redis, now=EVENING + timedelta(minutes=1)))["sent"] == 1
    assert len(bus.on(BRIDGE_OUTBOUND)) == 1
    assert (await db.execute(text("SELECT count(*) FROM biz.alerts WHERE digest_at IS NOT NULL"))).scalar_one() == 2


async def test_digest_is_idempotent_per_slot(db: Any) -> None:
    org, _ = await world(db)
    assert await run(org) == "sent"
    assert await run(org, EVENING + timedelta(minutes=20)) == "already_sent"
    assert len(await outbox(db)) == 1


async def test_digest_never_leaks_console_pin_only_counts_them(db: Any) -> None:
    org, sig = await world(db, pin=True)
    assert await run(org) == "sent"
    [row] = await outbox(db)
    assert "BÍ MẬT" not in row.text and "Hùng" not in row.text
    assert "1 mục cần xem trên Console" in row.text
    assert str(sig["priv"].id) not in str(row.refs)


async def test_digest_masks_account_numbers_in_quotes(db: Any) -> None:
    org = await mk_org(db)
    await _install(db, org)
    s = await _sig(db, org, "cau_hoi_cho_xu_ly", "q1", impact_base=2, deadline_at=NOW + timedelta(hours=10))
    await db.execute(text("""UPDATE biz.alerts SET evidence = jsonb_build_array(jsonb_build_object('quote_status', 'verified',
                             'quote_display', 'chuyển vào 0123456789012 nhé')) WHERE id = :i"""), {"i": s.id})
    await db.commit()
    await bot_world(db, org)
    assert await run(org) == "sent"
    [row] = await outbox(db)
    assert "0123456789012" not in row.text and "0123…012" in row.text


async def test_digest_is_capped_at_ten_lines_and_1500_chars(db: Any) -> None:
    org = await mk_org(db)
    await _install(db, org)
    for i in range(14):
        s = await _sig(db, org, "cau_hoi_cho_xu_ly", f"q{i}", impact_base=2, deadline_at=NOW + timedelta(hours=10))
        await db.execute(text("UPDATE biz.alerts SET title = :t WHERE id = :i"), {"t": "Câu hỏi dài " + "x" * 200, "i": s.id})
    await db.commit()
    await bot_world(db, org)
    assert await run(org) == "sent"
    [row] = await outbox(db)
    assert len(row.text.splitlines()) <= 10 and len(row.text) <= 1500 and "mục nữa" in row.text


async def test_quiet_day_is_one_line_plus_coverage(db: Any) -> None:
    org = await mk_org(db)
    await _raw(db, org)
    await bot_world(db, org)
    assert await run(org) == "sent"
    [row] = await outbox(db)
    assert "yên" in row.text and "Độ phủ" in row.text


async def test_recovered_line_appears_in_next_digest(db: Any) -> None:
    org, _ = await world(db)
    await health.raise_once(db, org, key="agent.session:zalo", kind="agent.session", severity="bad", title="Phiên Zalo của trợ lý bị đăng xuất", body="x", link=None)
    await health.clear(db, org, "agent.session:zalo")
    await db.commit()
    assert await run(org) == "sent"
    [row] = await outbox(db)
    assert "Đã hồi phục: Phiên Zalo của trợ lý bị đăng xuất" in row.text


async def test_shadow_mode_tags_the_digest(db: Any) -> None:
    org, _ = await world(db)
    await db.execute(text("""UPDATE core.organizations SET settings = settings || jsonb_build_object('intel',
                               COALESCE(settings->'intel', '{}'::jsonb) || '{"mode": "shadow"}'::jsonb) WHERE id = :o"""), {"o": org})
    await db.commit()
    assert await run(org) == "sent"
    assert "[chạy thử]" in (await outbox(db))[0].text


async def test_digest_off_sends_only_urgent_points_and_nothing_when_none(db: Any) -> None:
    org, sig = await world(db)
    await delivery.set_digest(db, org, off=True)
    await db.commit()
    assert await run(org) == "sent"
    [row] = await outbox(db)
    assert "Điểm gấp trong ngày" in row.text
    assert str(sig["red_top"].id) in str(row.refs) and str(sig["amber"].id) not in str(row.refs)     # chỉ digest_top / digest_morning
    assert await run(org, EVENING + timedelta(days=1)) == "nothing"                                 # hết mục gấp ⇒ im
    assert len(await outbox(db)) == 1


async def test_locked_assistant_sends_nothing_and_keeps_items(db: Any) -> None:
    org, _ = await world(db)
    await _lock(db, org)
    assert await run(org) == "locked"
    assert await outbox(db) == []
    assert (await db.execute(text("SELECT count(*) FROM biz.alerts WHERE digest_at IS NOT NULL"))).scalar_one() == 0
    assert (await db.execute(text("SELECT count(*) FROM ops.job_watermarks WHERE job = :j"), {"j": delivery.JOB})).scalar_one() == 0


async def test_push_alerts_sends_push_now_chat_ok_only_and_dedupes(db: Any) -> None:
    org = await mk_org(db)
    await _install(db, org)
    red = await _sig(db, org, "xung_dot_khieu_nai", "c-red", customer_of_owner=True, flags={"public": True})
    priv = await _sig(db, org, "xung_dot_khieu_nai", "c-red-priv", privacy="console_pin", customer_of_owner=True, flags={"public": True})
    amber = await _sig(db, org, "cau_hoi_cho_xu_ly", "q1", impact_base=2, deadline_at=NOW + timedelta(hours=10))
    assert red.delivery == "push_now"
    await bot_world(db, org)
    async with SM()() as s:
        assert await delivery.push_alerts(s, org, NOW) == 1
        assert await delivery.push_alerts(s, org, NOW) == 0                      # trùng khoá
        await s.commit()
    rows = await outbox(db, "alert")
    assert len(rows) == 1 and rows[0].severity == "urgent" and "«abc»" in rows[0].text
    assert rows[0].refs[0]["signal_id"] == str(red.id)
    assert str(priv.id) not in str(rows[0].refs) and str(amber.id) not in str(rows[0].refs)
    nb = (await db.execute(text("SELECT not_before, created_at FROM ops.owner_outbox"))).one()
    assert nb.not_before == nb.created_at                                       # bypass giờ yên lặng: 🔴 đã được chấm là đẩy ngay


async def test_push_alerts_locked_sends_nothing(db: Any) -> None:
    org = await mk_org(db)
    await _install(db, org)
    await _sig(db, org, "xung_dot_khieu_nai", "c-red", customer_of_owner=True, flags={"public": True})
    await bot_world(db, org)
    await _lock(db, org)
    async with SM()() as s:
        assert await delivery.push_alerts(s, org, NOW) == 0
    assert await outbox(db, "alert") == []


async def test_coverage_line_counts_messages_media_and_lost_groups(db: Any) -> None:
    org = await mk_org(db)
    await _raw(db, org, at=NOW - timedelta(hours=2))
    await _raw(db, org, at=NOW - timedelta(hours=3))
    await health.raise_once(db, org, key="agent.group_removed:g1", kind="agent.group_removed", severity="bad", title="t", body="b", link=None)
    await db.commit()
    async with SM()() as s:
        line = await coverage.line(s, org, NOW)
    assert "đọc 2 tin" in line and "1 nhóm mất kết nối" in line


async def _ask(db: Any, org: uuid.UUID, source: str, summary: str) -> None:
    from gh.biz.intel import asks

    await asks.create(db, org, source=source, ref_id=uuid.uuid4(), decision_kind=asks.DECISION_KINDS[0], summary=summary)
    await db.commit()


async def test_people_ask_summary_never_leaks_to_zalo_digest(db: Any) -> None:
    """TẤN CÔNG: ask về NGƯỜI (profile/identity) chỉ xem được qua PIN ⇒ bản tin Zalo chỉ có dòng đếm; Console (audience owner) vẫn thấy đủ."""
    from gh.biz.intel import digest as digest_mod

    for source in ("identity", "profile"):
        org, _ = await world(db)
        await _ask(db, org, source, "Hùng và Hưng có phải một người? BÍ MẬT-NHÂN-SỰ")
        assert await run(org) == "sent"
        text_ = (await db.execute(text("SELECT text FROM ops.owner_outbox WHERE org_id = :o AND kind = 'digest'"), {"o": org})).scalar_one()
        assert "BÍ MẬT-NHÂN-SỰ" not in text_ and "Hùng" not in text_ and "câu hỏi cần xem trên Console" in text_
        async with SM()() as s:
            owner_view = await digest_mod.build(s, org, EVENING, audience="owner", only_new=False)
        assert owner_view.system_question is not None and "BÍ MẬT-NHÂN-SỰ" in owner_view.system_question["summary"]


async def test_question_ask_summary_is_shown_in_zalo_digest(db: Any) -> None:
    org, _ = await world(db)
    await _ask(db, org, "question", "Anh muốn giao hàng hôm nào?")
    assert await run(org) == "sent"
    assert "❓ Anh muốn giao hàng hôm nào?" in (await outbox(db))[0].text


def test_display_mask_defaults_to_masking_and_only_exempts_clear_money_context() -> None:
    """TẤN CÔNG: STK/SĐT viết tách nhóm không được lọt qua vì "trông giống tiền"."""
    m = delivery.display_mask
    for leak in ("STK 1 234 567 890", "STK 123 456 789", "STK 1.234.567.890", "0901 234 567", "+84 901 234 567", "84 901 234 567", "số 0123456789012"):
        out = m(leak)
        assert "…" in out and not any(c in out for c in ("234 567", "234.567", "0123456789")), (leak, out)
    assert m("1 234 567 890") == "1234…890"
    for keep in ("1.500.000đ", "2 triệu", "giá 25.000.000", "25.000.000 VND", "chuyển 12.000.000đ nhé", "ngày 2026-10-23"):
        assert m(keep) == keep, keep
    assert m("0901234567đ") != "0901234567đ"                       # số bắt đầu bằng 0 không bao giờ được miễn
