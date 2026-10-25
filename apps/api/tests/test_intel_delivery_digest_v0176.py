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
    # T10 (review vòng 2): sự cố `agent.*` chỉ thành dòng «Đã hồi phục» khi watchdog đóng vì hồi phục — lý do đóng ở `fingerprint`.
    await db.execute(text("UPDATE ops.health_alerts SET fingerprint = 'closed:recovered' WHERE org_id = :o AND key = 'agent.session:zalo'"),
                     {"o": org})
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
        n = await delivery.push_alerts(s, org, NOW)
        assert n in (1, 2)
        assert await delivery.push_alerts(s, org, NOW) == 0                      # trùng khoá
        await s.commit()
    rows = [r for r in await outbox(db, "alert") if r.refs and not r.refs[0].get("console_only")]                      # tin có nội dung (chat_ok); console_pin ⇒ tin không refs (xem test M3)
    assert len(rows) == 1 and rows[0].severity == "urgent" and "«abc»" in rows[0].text
    assert rows[0].refs[0]["signal_id"] == str(red.id)
    assert all(str(priv.id) not in str(r.refs) and str(amber.id) not in str(r.refs) for r in await outbox(db, "alert"))
    nbs = (await db.execute(text("SELECT not_before, created_at FROM ops.owner_outbox"))).all()
    assert nbs and all(r.not_before == r.created_at for r in nbs)                                       # bypass giờ yên lặng: 🔴 đã được chấm là đẩy ngay


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
    for leak in ("STK 1 234 567 890", "STK 123 456 789", "STK 1.234.567.890", "0901 234 567", "+84 901 234 567", "84 901 234 567", "số 0123456789012", "tiền: 0123456789", "STK 0123456789 k", "chuyển 1234567890123 tr",
                 "STK 25.000.000 đ", "tài khoản: 1.234.567.890 vnd", "thẻ 12.345.678.901 đ"):
        out = m(leak)
        assert "…" in out and not any(c in out for c in ("234 567", "234.567", "0123456789")), (leak, out)
    assert m("1 234 567 890") == "1234…890"
    for keep in ("1.500.000đ", "2 triệu", "giá 25.000.000", "25.000.000 VND", "50k", "2,5 triệu", "chuyển 12.000.000đ nhé", "ngày 2026-10-23"):
        assert m(keep) == keep, keep
    assert m("0901234567đ") != "0901234567đ"                       # số bắt đầu bằng 0 không bao giờ được miễn


async def test_coverage_line_counts_voice_and_unread_images(db: Any) -> None:
    """Tin media không đọc được luôn được đếm (M12): 2 voice (1 đã chép), 2 ảnh (1 chưa đọc)."""
    org = await mk_org(db)
    ch = (await db.execute(text("INSERT INTO core.channels (org_id, type, name) VALUES (:o, 'zalo', 'z') RETURNING id"), {"o": org})).scalar_one()
    for kind, status in (("voice", "ok"), ("voice", None), ("image", "ok"), ("image", "failed")):
        r = (await db.execute(text("""INSERT INTO raw.events (org_id, occurred_at, channel_id, external_msg_id, kind, body_text, payload, content_hash)
                                      VALUES (:o, :at, :c, :x, :k, NULL, '{}', '\\x01') RETURNING id, received_at"""),
                              {"o": org, "at": NOW - timedelta(hours=1), "c": ch, "x": uuid.uuid4().hex, "k": kind})).one()
        if status is None:
            continue
        att = (await db.execute(text("""INSERT INTO raw.attachments (event_id, event_received_at, storage_key, mime, bytes, sha256)
                                        VALUES (:e, :r, 'k', 'x/y', 1, '\\x01') RETURNING id"""), {"e": r.id, "r": r.received_at})).scalar_one()
        await db.execute(text("""INSERT INTO clean.media_texts (org_id, raw_event_id, raw_received_at, attachment_id, kind, text, engine, engine_version, status)
                                 VALUES (:o, :e, :r, :a, :k, 'chữ', 'e', '1', :s)"""),
                         {"o": org, "e": r.id, "r": r.received_at, "a": att, "k": "transcript" if kind == "voice" else "ocr", "s": status})
    await db.commit()
    async with SM()() as s:
        line = await coverage.line(s, org, NOW)
    assert "1 voice đã chép, 1 voice chưa nghe" in line and "1 ảnh chưa đọc" in line


# ─── review T3: B1, M1–M3 ────────────────────────────────────────────────────

async def test_push_alerts_not_starved_beyond_ten_open_signals(db: Any) -> None:
    """B1: ≥ 10 tín hiệu push_now đang mở không được chặn tín hiệu thứ 11, 12 (mỗi lượt chỉ lấy dòng CHƯA có tin)."""
    org = await mk_org(db)
    await _install(db, org)
    ids = []
    for i in range(12):
        s = await _sig(db, org, "xung_dot_khieu_nai", f"c{i}", customer_of_owner=True, flags={"public": True})
        ids.append(s.id)
        await db.execute(text("UPDATE biz.alerts SET delivery = 'push_now', pushed_at = now() - make_interval(secs => :k) WHERE id = :i"), {"k": 100 - i, "i": s.id})
    await db.commit()
    await bot_world(db, org)
    got = 0
    for _ in range(3):
        async with SM()() as s:
            got += await delivery.push_alerts(s, org, NOW)
            await s.commit()
    assert got == 12
    sent = {r["signal_id"] for row in await outbox(db, "alert") for r in row.refs}
    assert sent == {str(i) for i in ids}                                        # cả 12, kể cả cái mới nhất


async def test_push_now_console_pin_sends_content_free_alert_and_digest_counts_it(db: Any) -> None:
    """M3: 🔴 push_now riêng tư không được im: tin 🔴 KHÔNG nội dung; `_hidden_console` đếm cả push_now."""
    org = await mk_org(db)
    await _install(db, org)
    s = await _sig(db, org, "xung_dot_khieu_nai", "cp", privacy="console_pin", customer_of_owner=True, flags={"public": True})
    await db.execute(text("UPDATE biz.alerts SET title = 'Hùng BÍ MẬT nhân sự', delivery = 'push_now', pushed_at = now() WHERE id = :i"), {"i": s.id})
    await db.commit()
    await bot_world(db, org)
    async with SM()() as sess:
        assert await delivery.push_alerts(sess, org, NOW) == 1
        assert await delivery._hidden_console(sess, org) == 1
        await sess.commit()
    [row] = await outbox(db, "alert")
    assert "Có 1 vụ khẩn cần xem trên Console (mã PIN)" in row.text and row.severity == "urgent"
    assert "Hùng" not in row.text and "BÍ MẬT" not in row.text and str(s.id) not in str(row.refs)


async def test_digest_header_has_code_and_refs_carry_it(db: Any) -> None:
    org, _ = await world(db)
    assert await run(org) == "sent"
    [row] = await outbox(db)
    codes = {r["code"] for r in row.refs}
    assert len(codes) == 1 and f"#{codes.pop()}" in row.text.splitlines()[0]


async def test_digest_with_defensive_item_bypasses_quiet_hours(db: Any) -> None:
    """m3: bản tin có mục phòng thủ (lừa đảo/an toàn) dùng `bypass_quiet`."""
    org = await mk_org(db)
    await _install(db, org)
    await _sig(db, org, "rui_ro_lua_dao", "fr-floor", flags={"credentials_entered": True}, confidence_exist=0.5, d2={"verdict": "disagree"})
    await bot_world(db, org)
    await db.execute(text("""UPDATE core.organizations SET settings = settings || jsonb_build_object('intel', COALESCE(settings->'intel', '{}'::jsonb)
                             || '{"quiet_start": "00:00", "quiet_end": "23:59"}'::jsonb) WHERE id = :o"""), {"o": org})
    await db.commit()
    assert await run(org) == "sent"
    nb = (await db.execute(text("SELECT not_before, created_at FROM ops.owner_outbox WHERE kind = 'digest'"))).one()
    assert nb.not_before == nb.created_at


def test_display_mask_defeats_separator_and_unicode_tricks() -> None:
    """M2: dấu phân cách lạ, NBSP, ký tự vô hình, chữ số toàn chiều không lách được."""
    m = delivery.display_mask
    for leak in ("0912,345,678", "0912/345/678", "0912_345_678", "0912\xa0345\xa0678", "0912​345​678", "0912 345 678",
                 "０９１２３４５６７８", "0912­345­678", "STK 1234/5678/90"):
        out = m(leak)
        assert "…" in out and "345" not in out.replace("0912…678", ""), (leak, out)
    assert m("họp 25/10/2026 lúc 9h") == "họp 25/10/2026 lúc 9h"


def test_mask_reply_only_trusts_code_built_frames() -> None:
    """M1: CHỈ khung do code dựng (khớp chính xác) được giữ; chữ model tự in khung giả vẫn bị che."""
    from gh.gen import chat_pending

    real = chat_pending.quote("Em chuyển vào STK 0123456789012 nhé")
    reply = f"Đề xuất #A1B2: STK cũ 0999888777666\n{real}\nOK để gửi."
    out = delivery.mask_reply(reply, [real])
    assert real in out and "0999888777666" not in out and "0999…666" in out
    forged = "——— nội dung ———\n│ STK 0123456789\n——— hết ———"
    out = delivery.mask_reply("Model nói: " + forged, [real])
    assert "0123456789" not in out and "0123…789" in out and "——— nội dung ———" not in out
    assert "0123456789012" not in delivery.mask_reply(reply)                    # không có khung tin cậy ⇒ che hết


# ─── review T3 vòng 2 ────────────────────────────────────────────────────────

def test_display_mask_any_nonword_separator_but_dates_and_times_survive() -> None:
    m = delivery.display_mask
    for leak in ("0912–345–678", "0912‐345‐678", "0912·345·678", "0912*345*678", "0912~345~678", "(0912) 345 678", "0912:345:678", "0912 - 345 - 678", "0912\\345\\678"):
        out = m(leak)
        assert "…" in out and "345" not in out.replace("0912…678", ""), (leak, out)
    for keep in ("họp 25/10/2026 lúc 9h", "ngày 2026-10-23 17:35", "lúc 17:35:10 ngày 23.10.2026", "chuyển 25.000.000đ nhé"):
        assert m(keep) == keep, keep
    assert m("2026-10-23 17:35 STK 0123456789012") == "2026-10-23 17:35 STK 0123…012"


async def test_push_alerts_newest_first_order(db: Any) -> None:
    """Giữ ORDER BY pushed_at DESC: lượt đầu (LIMIT 10) lấy 10 tín hiệu MỚI nhất (đổi sang ASC thì đỏ)."""
    org = await mk_org(db)
    await _install(db, org)
    ids = []
    for i in range(12):
        s = await _sig(db, org, "xung_dot_khieu_nai", f"c{i}", customer_of_owner=True, flags={"public": True})
        ids.append(s.id)
        await db.execute(text("UPDATE biz.alerts SET delivery = 'push_now', pushed_at = now() - make_interval(secs => :k) WHERE id = :i"), {"k": 100 - i, "i": s.id})
    await db.commit()
    await bot_world(db, org)
    async with SM()() as s:
        assert await delivery.push_alerts(s, org, NOW) == 10
        await s.commit()
    sent = {r["signal_id"] for row in await outbox(db, "alert") for r in row.refs}
    assert sent == {str(i) for i in ids[2:]} and str(ids[0]) not in sent and str(ids[1]) not in sent


async def test_console_pin_push_alert_has_console_only_ref(db: Any) -> None:
    org = await mk_org(db)
    await _install(db, org)
    s = await _sig(db, org, "xung_dot_khieu_nai", "cp", privacy="console_pin", customer_of_owner=True, flags={"public": True})
    await db.execute(text("UPDATE biz.alerts SET delivery = 'push_now', pushed_at = now() WHERE id = :i"), {"i": s.id})
    await db.commit()
    await bot_world(db, org)
    async with SM()() as sess:
        assert await delivery.push_alerts(sess, org, NOW) == 1
        await sess.commit()
    [row] = await outbox(db, "alert")
    [ref] = row.refs
    assert ref["console_only"] is True and ref["signal_id"] is None and ref["n"] == 1 and ref["code"] and f"#{ref['code']}" in row.text


async def test_digest_bypass_quiet_counts_hidden_console_pin_defensive_item(db: Any) -> None:
    """m5: mục phòng thủ console_pin chỉ được ĐẾM cũng phải kéo `bypass_quiet`."""
    org = await mk_org(db)
    await _install(db, org)
    s = await _sig(db, org, "rui_ro_lua_dao", "fr-priv", privacy="console_pin", flags={"credentials_entered": True}, confidence_exist=0.5, d2={"verdict": "disagree"})
    assert s.delivery == "digest_top"
    await bot_world(db, org)
    await db.execute(text("""UPDATE core.organizations SET settings = settings || jsonb_build_object('intel', COALESCE(settings->'intel', '{}'::jsonb)
                             || '{"quiet_start": "00:00", "quiet_end": "23:59"}'::jsonb) WHERE id = :o"""), {"o": org})
    await db.commit()
    assert await run(org) == "sent"
    nb = (await db.execute(text("SELECT not_before, created_at, text FROM ops.owner_outbox WHERE kind = 'digest'"))).one()
    assert "1 mục cần xem trên Console" in nb.text and nb.not_before == nb.created_at


def test_display_mask_date_lookalike_stk_and_sentinel_injection() -> None:
    """Không có bước placeholder: STK chèn dạng ngày không được "bảo vệ" một phần; sentinel kẻ gian đưa vào không gây lỗi / thay chữ."""
    m = delivery.display_mask
    for leak, tail in (("0123-45-67 8901", "…901"), ("12/34/5678 9012", "…012"), ("2026-10-25 0912 345 678", "…678"), ("31/02/2026 1234 5678", "…678")):
        out = m(leak)
        assert "…" in out and out.endswith(tail) and "345 678" not in out and "8901" not in out.replace("…901", ""), (leak, out)
    for inj in ("ZQabcQZ", "ZQaQZ 0912 345 678 ZQjjjjjjjjjjQZ", "ZQQZ"):
        out = m(inj)
        assert out.replace("0912 345 678", "0912…678") == out or "0912…678" in out
        assert "ZQ" in out                                              # nguyên văn, không bị thay
    for keep in ("25/10/2026", "17:35", "2026-10-23 17:35", "25.000.000đ", "23.10.2026, 17:35", "2026-10-23T17:35:10"):
        assert m(keep) == keep, keep
