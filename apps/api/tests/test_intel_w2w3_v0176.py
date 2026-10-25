# ruff: noqa: E501
"""v0.1.76 (gói T5) — W2 câu hỏi chờ + xung đột/khiếu nại (cổng khach_che_cong_khai_doa_bo) và W3 quan hệ nóng/nguội, trên PostgreSQL thật.
Ba điểm yếu G-KB: (a) câu hỏi chờ quá 2 giờ leo thang THEO ĐỒNG HỒ; (b) khách im lặng theo nhịp đặt hàng sinh tín hiệu qua đường quét định kỳ
của heat; (c) câu đùa/càu nhàu mà vẫn đặt hàng không thành 🔴."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Any

import orjson
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gh.biz.intel import jobs as intel_jobs
from gh.biz.intel import levels, signals
from gh.biz.intel.detectors import conflicts, questions
from gh.biz.intel.detectors import heat as heat_det
from gh.biz.mem import entities, facts
from tests.mem_helpers import T, mk_org, mkfact
from tests.test_owner_profile_support import mk_group, mk_person, mk_raw, mk_user, mk_world

NOW = datetime(2026, 10, 23, 14, 0, tzinfo=levels.VN)


async def _install(db: AsyncSession, org: uuid.UUID) -> None:
    s = {"installed_at": "2026-09-01T00:00:00+07:00", "m1_passed_gates": list(levels.REGULAR_GATES)}
    await db.execute(text("UPDATE core.organizations SET settings = settings || jsonb_build_object('intel', CAST(:s AS jsonb)) WHERE id = :o"),
                     {"s": orjson.dumps(s).decode(), "o": org})
    await db.commit()


async def _customer(db: AsyncSession, name: str = "Chị Lan") -> tuple[Any, uuid.UUID, uuid.UUID, uuid.UUID]:
    w = await mk_world(db, await mk_org(db))
    await _install(db, w.org)
    pid, iid = await mk_person(db, w.org, w.channel, name)
    await db.execute(text("UPDATE core.persons SET person_type = 'customer' WHERE id = :p"), {"p": pid})
    await db.commit()
    return w, pid, iid, (await entities.ensure_person(db, w.org, pid)).id


def _complaint(raw: uuid.UUID, pid: uuid.UUID, *, ago_h: float = 1.0, **kw: Any) -> conflicts.ComplaintIn:
    data: dict[str, Any] = {"sent_at": NOW - timedelta(hours=ago_h), "raw_event_id": raw, "customer_person_id": pid, "quote": "dịch vụ tệ quá",
                            "span_end": 14, "public": True, "thread": levels.Thread(type="group", size=20, owner_in_thread=True, agent_in_thread=True),
                            "thread_key": f"g:{uuid.uuid4()}"}
    data.update(kw)
    return conflicts.ComplaintIn(**data)


async def _row(db: AsyncSession, sid: uuid.UUID) -> Any:
    return (await db.execute(text("SELECT level, gate, candidate, delivery, reasons, next_eval_at, status FROM biz.alerts WHERE id = :i"), {"i": sid})).one()


# ─── (a) câu hỏi chờ: leo thang theo đồng hồ ─────────────────────────────────

async def _question(db: AsyncSession, w: Any, **kw: Any) -> signals.Signal:
    raw = await mk_raw(db, w.org, w.channel, identity=None, body="Anh ơi hết xi măng rồi, lấy ở đâu?", at=NOW - timedelta(minutes=30))
    q = questions.QuestionIn(asked_at=NOW - timedelta(minutes=30), raw_event_id=raw, span_end=20, asker="internal", blocks_work=True,
                             thread=levels.Thread(type="group", size=12, owner_in_thread=True, agent_in_thread=True), thread_key=f"g:{uuid.uuid4()}", **kw)
    sig = await questions.emit(db, w.org, q, NOW)
    await db.commit()
    return sig


async def test_question_blocking_escalates_by_clock_after_2h_without_new_message(db) -> None:  # type: ignore[no-untyped-def]
    w = await mk_world(db, await mk_org(db))
    await _install(db, w.org)
    sig = await _question(db, w)
    r = await _row(db, sig.id)
    assert r.level == "grey" and r.delivery == "digest_low"                       # mới hỏi 30 phút: chưa leo
    assert r.next_eval_at is not None and r.next_eval_at <= NOW + timedelta(hours=1.5)    # đồng hồ đã hẹn mốc 2 giờ
    assert await questions.refresh_waiting(db, w.org, NOW + timedelta(hours=1)) == []     # chưa tới mốc
    changed = await questions.refresh_waiting(db, w.org, NOW + timedelta(hours=1, minutes=45))   # 2h15 sau câu hỏi, 15:45 VN
    await db.commit()
    assert [c.id for c in changed] == [sig.id]
    r = await _row(db, sig.id)
    assert r.level == "amber" and r.gate is None                                  # chỉ 🔴 qua cổng su_co_cong_viec_gap
    assert await questions.refresh_waiting(db, w.org, NOW + timedelta(hours=3)) == []     # chạy lại an toàn


async def test_question_clock_job_is_registered_and_kin_question_is_covered(db) -> None:  # type: ignore[no-untyped-def]
    assert "intel_questions_clock" in {f.__name__ for f, _ in intel_jobs.JOBS} and "intel_heat_scan" in {f.__name__ for f, _ in intel_jobs.JOBS}
    w = await mk_world(db, await mk_org(db))
    await _install(db, w.org)
    mother, mother_i = await mk_person(db, w.org, w.channel, "Mẹ")
    raw = await mk_raw(db, w.org, w.channel, identity=mother_i, body="Con ơi tối nay về ăn cơm không?", at=NOW - timedelta(hours=1))
    q = questions.QuestionIn(asked_at=NOW - timedelta(hours=1), raw_event_id=raw, span_end=10, asker="kin", asker_person_id=mother, needs_owner=True,
                             thread=levels.Thread(type="dm_owner", owner_in_thread=True))
    sig = await questions.emit(db, w.org, q, NOW)
    r = (await db.execute(text("SELECT kind, level, summary, facts FROM biz.alerts WHERE id = :i"), {"i": sig.id})).one()
    assert r.kind == "cau_hoi_cho_xu_ly" and r.level == "amber" and "có thể đã xử lý ngoài nhóm" in r.summary
    assert r.facts["asker_kind"] == "kin"


async def test_question_answered_closes_signal(db) -> None:  # type: ignore[no-untyped-def]
    w = await mk_world(db, await mk_org(db))
    await _install(db, w.org)
    sig = await _question(db, w)
    await questions.refresh_waiting(db, w.org, NOW + timedelta(hours=2))
    after = await questions.mark_answered(db, w.org, sig.id, NOW + timedelta(hours=2, minutes=5), by="staff")
    r = await _row(db, after.id)
    assert r.status == "resolved" and r.level == "grey" and r.delivery == "none"


# ─── W2: cổng khach_che_cong_khai_doa_bo ─────────────────────────────────────

async def test_public_complaint_by_customer_is_red_then_amber_top_after_24h(db) -> None:  # type: ignore[no-untyped-def]
    w, pid, iid, _ = await _customer(db)
    raw = await mk_raw(db, w.org, w.channel, identity=iid, body="dịch vụ tệ quá", at=NOW - timedelta(hours=1))
    [sig] = await conflicts.emit(db, w.org, _complaint(raw, pid), NOW)
    r = await _row(db, sig.id)
    assert (r.level, r.gate, r.delivery) == ("red", "khach_che_cong_khai_doa_bo", "push_now")
    changed = await signals.reevaluate_due(db, NOW + timedelta(hours=24), org_id=w.org)      # mốc vụ + 24 giờ: đồng hồ, không tin mới
    assert [c.id for c in changed] == [sig.id]
    r = await _row(db, sig.id)
    assert (r.level, r.candidate, r.delivery) == ("amber", "khach_che_cong_khai_doa_bo", "digest_top") and "window_expired" in r.reasons


async def test_non_customer_complaint_is_not_routed_and_leave_threat_adds_relation_signal(db) -> None:  # type: ignore[no-untyped-def]
    w = await mk_world(db, await mk_org(db))
    await _install(db, w.org)
    stranger, iid = await mk_person(db, w.org, w.channel, "Người lạ")
    raw = await mk_raw(db, w.org, w.channel, identity=iid, body="dịch vụ tệ quá", at=NOW - timedelta(hours=1))
    assert await conflicts.emit(db, w.org, _complaint(raw, stranger), NOW) == []
    w2, pid, iid2, _ = await _customer(db, "Anh Bình")
    raw2 = await mk_raw(db, w2.org, w2.channel, identity=iid2, body="thôi khỏi, lần sau lấy chỗ khác", at=NOW - timedelta(hours=1))
    sigs = await conflicts.emit(db, w2.org, _complaint(raw2, pid, public=False, said_will_leave=True, quote="lần sau lấy chỗ khác", span_end=20), NOW)
    assert len(sigs) == 2
    rows = (await db.execute(text("SELECT kind, level, gate, case_key FROM biz.alerts WHERE org_id = :o ORDER BY kind"), {"o": w2.org})).all()
    assert [r.kind for r in rows] == ["quan_he_nong_nguoi", "xung_dot_khieu_nai"] and rows[0].case_key == rows[1].case_key
    assert rows[1].level == "red" and rows[1].gate == "khach_che_cong_khai_doa_bo"


async def test_closure_by_owner_staff_customer(db) -> None:  # type: ignore[no-untyped-def]
    for by, delivery in (("owner", "none"), ("customer", "none"), ("staff", "digest_low")):
        w, pid, iid, _ = await _customer(db)
        raw = await mk_raw(db, w.org, w.channel, identity=iid, body="dịch vụ tệ quá", at=NOW - timedelta(hours=1))
        [sig] = await conflicts.emit(db, w.org, _complaint(raw, pid), NOW)
        key = (await db.execute(text("SELECT case_key FROM biz.alerts WHERE id = :i"), {"i": sig.id})).scalar_one()
        [closed] = await conflicts.close_case(db, w.org, key, by=by, now=NOW + timedelta(minutes=10))  # type: ignore[arg-type]
        r = await _row(db, closed.id)
        assert r.level == "grey" and r.delivery == delivery, by


async def test_repeat_n_counts_cases_within_30_days(db) -> None:  # type: ignore[no-untyped-def]
    w, pid, iid, _ = await _customer(db)
    for i in range(3):
        raw = await mk_raw(db, w.org, w.channel, identity=iid, body=f"tệ lần {i}", at=NOW - timedelta(days=10 * (3 - i)))
        await conflicts.emit(db, w.org, _complaint(raw, pid, ago_h=24 * 10 * (3 - i), public=False, reply_chain_id=f"c{i}", quote="tệ lần"), NOW)
    last = (await db.execute(text("SELECT level_input -> 'flags' ->> 'repeat_n' AS n FROM biz.alerts WHERE org_id = :o ORDER BY created_at DESC LIMIT 1"),
                             {"o": w.org})).scalar_one()
    assert int(last) == 3


# ─── (c) câu đùa / càu nhàu mà vẫn đặt hàng ───────────────────────────────────

async def test_joke_or_grumble_with_reorder_is_never_red(db) -> None:  # type: ignore[no-untyped-def]
    # đối chứng: lời chê thật ở nơi công khai ⇒ 🔴
    w, pid, iid, _ = await _customer(db)
    raw = await mk_raw(db, w.org, w.channel, identity=iid, body="giao trễ, tệ quá", at=NOW - timedelta(hours=1))
    [real] = await conflicts.emit(db, w.org, _complaint(raw, pid, quote="giao trễ, tệ quá"), NOW)
    assert (await _row(db, real.id)).level == "red"
    # câu đùa "xách dao qua", public + doạ bỏ theo model, khách vẫn đặt hàng ngay sau đó (biz.deals) ⇒ không 🔴, đóng ⚪
    w, pid, iid, _ = await _customer(db)
    raw = await mk_raw(db, w.org, w.channel, identity=iid, body="giao trễ nữa là em xách dao qua đó nha 😂", at=NOW - timedelta(hours=1))
    await db.execute(text("INSERT INTO biz.deals (org_id, code, person_id, amount_vnd, status, won_at) VALUES (:o, 'D1', :p, 1000000, 'won', :t)"),
                     {"o": w.org, "p": pid, "t": NOW - timedelta(minutes=20)})
    sigs = await conflicts.emit(db, w.org, _complaint(raw, pid, quote="em xách dao qua đó nha", span_end=22, said_will_leave=True), NOW)
    assert sigs
    for s in sigs:
        r = await _row(db, s.id)
        assert r.level != "red" and r.level == "grey" and r.delivery == "none", r.reasons
    # càu nhàu (model nêu tone=grumble), đặt hàng lại (hint) ⇒ không 🔴
    w, pid, iid, _ = await _customer(db)
    raw = await mk_raw(db, w.org, w.channel, identity=iid, body="sao giá lên hoài vậy trời", at=NOW - timedelta(hours=1))
    [g] = await conflicts.emit(db, w.org, _complaint(raw, pid, tone="grumble", ordered_after=True), NOW)
    assert (await _row(db, g.id)).level != "red"
    # đùa nhưng CHƯA có đơn sau đó ⇒ vẫn không 🔴 (hạ khỏi cổng), tối đa 🟠
    w, pid, iid, _ = await _customer(db)
    raw = await mk_raw(db, w.org, w.channel, identity=iid, body="xách dao qua haha", at=NOW - timedelta(hours=1))
    [j] = await conflicts.emit(db, w.org, _complaint(raw, pid, quote="xách dao qua haha", span_end=10), NOW)
    assert (await _row(db, j.id)).level in ("grey", "amber")


async def test_runner_fast_path_routes_customer_complaint_through_gate_and_falls_back(db) -> None:  # type: ignore[no-untyped-def]
    w, pid, iid, _ = await _customer(db)
    gid = await mk_group(db, w.org, w.channel)
    await db.execute(text("UPDATE core.groups SET member_count = 30 WHERE id = :g"), {"g": gid})
    raw = await mk_raw(db, w.org, w.channel, identity=iid, body="Thôi khỏi, lần sau lấy chỗ khác nhé", group=gid, at=NOW - timedelta(hours=1))
    out = await conflicts.from_rule(db, w.org, event_ids=[raw], person_id=pid, group_id=gid, now=NOW)
    assert out and (await _row(db, out[0].id)).gate == "khach_che_cong_khai_doa_bo"
    assert await conflicts.from_rule(db, w.org, event_ids=[raw], person_id=None, group_id=gid, now=NOW) is None
    stranger, _ = await mk_person(db, w.org, w.channel, "Người lạ")
    assert await conflicts.from_rule(db, w.org, event_ids=[raw], person_id=stranger, group_id=gid, now=NOW) is None


# ─── W3 / (b): quan hệ nóng-nguội, nhịp đặt hàng ──────────────────────────────

def test_order_silence_is_relative_to_customers_own_rhythm() -> None:
    ago = lambda *ds: [NOW - timedelta(days=d) for d in ds]  # noqa: E731
    assert heat_det.order_silence(ago(39, 33, 27, 21, 15), NOW) == (15, 6.0)        # đặt đều 6 ngày, 15 ngày chưa đặt
    assert heat_det.order_silence(ago(25, 19, 13, 7, 1), NOW) is None               # mới đặt hôm qua
    assert heat_det.order_silence(ago(80, 50, 20), NOW) is None                     # nhịp 30 ngày: 20 ngày im còn bình thường
    assert heat_det.order_silence(ago(50, 44), NOW) is None                         # ít hơn 3 đơn: chưa có nhịp
    assert heat_det.order_silence(ago(400, 394, 388), NOW) is None                  # im quá 120 ngày: đã mất, không còn "nguội dần"


async def _orders(db: AsyncSession, org: uuid.UUID, pid: uuid.UUID, days_ago: list[int]) -> None:
    for d in days_ago:
        await db.execute(text("INSERT INTO biz.deals (org_id, code, person_id, amount_vnd, status, won_at) VALUES (:o, :c, :p, 500000, 'won', :t)"),
                         {"o": org, "c": f"D{uuid.uuid4().hex[:6]}", "p": pid, "t": NOW - timedelta(days=d)})
    await db.commit()


async def test_heat_scan_customer_silent_15_days_vs_rhythm_creates_signal_idempotent_and_resolves(db) -> None:  # type: ignore[no-untyped-def]
    w, pid, iid, _ = await _customer(db, "Chị Hoa")
    gid = await mk_group(db, w.org, w.channel, "Nhóm khách")
    await db.execute(text("INSERT INTO mem.group_contexts (org_id, group_id, context, source, is_primary) VALUES (:o, :g, 'kinh_doanh', 'owner', true)"),
                     {"o": w.org, "g": gid})
    await mk_raw(db, w.org, w.channel, identity=iid, body="Chị lấy thêm 10 thùng nhé", group=gid, at=NOW - timedelta(days=15))
    await _orders(db, w.org, pid, [39, 33, 27, 21, 15])                             # nhịp 6 ngày, 15 ngày chưa đặt
    # khách khác đặt đều và mới đặt hôm qua ⇒ không có tín hiệu
    pid2, iid2 = await mk_person(db, w.org, w.channel, "Anh Đều")
    await db.execute(text("UPDATE core.persons SET person_type = 'customer' WHERE id = :p"), {"p": pid2})
    await mk_raw(db, w.org, w.channel, identity=iid2, body="ok anh", at=NOW - timedelta(days=1))
    await db.commit()
    for i, d in enumerate([25, 19, 13, 7, 1]):
        await db.execute(text("INSERT INTO biz.deals (org_id, code, person_id, amount_vnd, status, won_at) VALUES (:o, :c, :p, 1, 'won', :t)"),
                         {"o": w.org, "c": f"E{i}", "p": pid2, "t": NOW - timedelta(days=d)})
    await db.commit()
    r1 = await heat_det.scan(db, w.org, NOW)
    r2 = await heat_det.scan(db, w.org, NOW)
    await db.commit()
    assert r1["signals"] == 1 and r2["signals"] == 1
    rows = (await db.execute(text("SELECT kind, subject_id, status, level, facts FROM biz.alerts WHERE org_id = :o"), {"o": w.org})).all()
    assert len(rows) == 1 and rows[0].kind == "quan_he_nong_nguoi" and rows[0].subject_id == pid and rows[0].level in ("grey", "amber")
    assert rows[0].facts["order_silent_days"] == 15 and rows[0].facts["median_gap_days"] == 6.0
    await _orders(db, w.org, pid, [0])                                              # khách đặt lại ⇒ hồi phục ⇒ đóng
    r3 = await heat_det.scan(db, w.org, NOW)
    await db.commit()
    assert r3["resolved"] == 1
    assert (await db.execute(text("SELECT status FROM biz.alerts WHERE org_id = :o"), {"o": w.org})).scalar_one() == "resolved"


async def test_heat_scan_key_relation_cooling_from_heat_series(db) -> None:  # type: ignore[no-untyped-def]
    w = await mk_world(db, await mk_org(db))
    await _install(db, w.org)
    user = await mk_user(db, w.org)
    pid, iid = await mk_person(db, w.org, w.channel, "Bạn thân")
    ent = (await entities.ensure_person(db, w.org, pid)).id
    await facts.assert_fact(db, mkfact(w.org, w.owner, None, T(1), predicate="owner_key_relation", object_entity=ent, cls="owner", confirmed_by=user))
    await mk_raw(db, w.org, w.channel, identity=iid, body="lâu quá không gặp", at=NOW - timedelta(days=20))
    for i, h in enumerate([40, 42, 38, 41, 45, 40, 10, 8]):
        await db.execute(text("""INSERT INTO mem.relation_heat_weekly (org_id, entity_a, entity_b, week, msgs_ab, msgs_ba, heat, state)
                                 VALUES (:o, :a, :b, :w, 3, 3, :h, 'warm')"""),
                         {"o": w.org, "a": w.owner, "b": ent, "w": (NOW - timedelta(weeks=8 - i)).date() - timedelta(days=(NOW - timedelta(weeks=8 - i)).weekday()), "h": h})
    await db.commit()
    r = await heat_det.scan(db, w.org, NOW)
    await db.commit()
    assert r["signals"] == 1
    row = (await db.execute(text("SELECT kind, level, facts FROM biz.alerts WHERE org_id = :o"), {"o": w.org})).one()
    assert row.kind == "quan_he_nong_nguoi" and row.facts["heat_cooling"] is True and row.facts["key_relation"] is True
    assert row.level != "red"


# ─── tấn công: tin của người khác không được gán cho khách ───────────────────

async def test_attack_third_party_leave_threat_is_not_attributed_to_customer(db) -> None:  # type: ignore[no-untyped-def]
    w, pid, iid, _ = await _customer(db, "Chị Lan")
    other, other_i = await mk_person(db, w.org, w.channel, "Nhân viên / người lạ")
    gid = await mk_group(db, w.org, w.channel)
    await db.execute(text("UPDATE core.groups SET member_count = 30 WHERE id = :g"), {"g": gid})
    body = "Thôi khỏi, lần sau lấy chỗ khác nhé"
    third = await mk_raw(db, w.org, w.channel, identity=other_i, body=body, group=gid, at=NOW - timedelta(hours=1))
    outbound = await mk_raw(db, w.org, w.channel, identity=None, body=body, group=gid, direction="outbound", at=NOW - timedelta(hours=1))
    # đường nhanh: id tin chỉ có tin của người thứ ba / tin đi ⇒ None, không tín hiệu
    assert await conflicts.from_rule(db, w.org, event_ids=[third], person_id=pid, group_id=gid, now=NOW) is None
    assert await conflicts.from_rule(db, w.org, event_ids=[outbound, third], person_id=pid, group_id=gid, now=NOW) is None
    # có cả tin của khách (không doạ bỏ) lẫn tin người khác (doạ bỏ) ⇒ chọn đúng tin của khách, marker không lấy từ tin người khác
    own = await mk_raw(db, w.org, w.channel, identity=iid, body="giao hơi trễ", group=gid, at=NOW - timedelta(hours=1, minutes=-5))
    out = await conflicts.from_rule(db, w.org, event_ids=[third, own], person_id=pid, group_id=gid, now=NOW)
    assert out and len(out) == 1 and out[0].kind == "xung_dot_khieu_nai"           # không có bản quan_he (không doạ bỏ) — marker của người khác bị bỏ
    li = (await db.execute(text("SELECT level_input -> 'flags' ->> 'said_will_leave' AS s FROM biz.alerts WHERE id = :i"), {"i": out[0].id})).scalar_one()
    assert li == "false"
    # gọi thẳng emit với tin của người khác ⇒ từ chối
    assert await conflicts.emit(db, w.org, _complaint(third, pid, said_will_leave=True), NOW) == []
    assert (await db.execute(text("SELECT count(*) FROM biz.alerts WHERE org_id = :o AND kind = 'quan_he_nong_nguoi'"), {"o": w.org})).scalar_one() == 0


async def test_attack_question_of_other_person_is_not_labelled_kin_or_customer(db) -> None:  # type: ignore[no-untyped-def]
    w = await mk_world(db, await mk_org(db))
    await _install(db, w.org)
    mother, _mi = await mk_person(db, w.org, w.channel, "Mẹ")
    _stranger, si = await mk_person(db, w.org, w.channel, "Người lạ")
    raw = await mk_raw(db, w.org, w.channel, identity=si, body="Cho hỏi giá?", at=NOW - timedelta(hours=1))
    q = questions.QuestionIn(asked_at=NOW - timedelta(hours=1), raw_event_id=raw, span_end=5, asker="kin", asker_person_id=mother, needs_owner=True)
    sig = await questions.emit(db, w.org, q, NOW)
    r = (await db.execute(text("SELECT facts, subject_id FROM biz.alerts WHERE id = :i"), {"i": sig.id})).one()
    assert r.facts["asker_kind"] == "other" and r.subject_id is None
