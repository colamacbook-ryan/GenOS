# ruff: noqa: E501
"""Đường phân tích THEO LUỒNG (spec Bước 1 r6.6.1 §4D.1): một lượt = một luồng, cửa sổ gần, giới hạn 60 tin / 12.000 ký tự không cắt giữa
chuỗi, gộp xả tin, ghi một giao dịch ngắn (đơn vị + bằng chứng đã kiểm + sự thật qua `assert_fact` + sổ tay có nguồn), lỗi giữ tin chờ."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

from gh.chassis.bus import CLEAN_READY
from gh.db import sessionmaker
from gh.refinery import _l3_stub as l3
from gh.refinery import runner
from gh.refinery.brain_contract import PersonMention
from gh.refinery.runner import GEv, Refinery, effective_count, merge_bursts, take_window
from tests.phase2 import FakeRouter, listen, msg, org_id, put, states
from tests.refinery_group_helpers import (
    Analyzer,
    Bus,
    LoggingRouter,
    enable_groups,
    find_msg,
    mk_model,
    out_with,
    pending_n,
    phone_fact,
    unit_for,
)

T0 = datetime(2026, 10, 20, 9, 0, tzinfo=UTC)


def ev(i: int, body: str = "x", *, who: str = "A", at: datetime | None = None, reply_ext: str | None = None, ext: str | None = None) -> GEv:
    return GEv(uuid.uuid4(), T0, at or T0 + timedelta(seconds=i * 300), i, "text", body, "inbound", None, None, None,
               uuid.uuid5(uuid.NAMESPACE_DNS, who), who, person_code=None, ext_id=ext or f"x{i}", reply_ext=reply_ext)


async def setup(db, sm, *, groups: tuple[str, ...] = ("g1",)):  # type: ignore[no-untyped-def]
    org = await org_id(db)
    gids = [await listen(db, org, g) for g in groups]
    await enable_groups(db, org)
    return org, gids


def refinery(sm, redis, analyzer: Analyzer, router=None, bus=None) -> Refinery:  # type: ignore[no-untyped-def]
    return Refinery(sm, redis, router or FakeRouter(), bus, analyzer=analyzer)


async def person_of(db, ext: str) -> uuid.UUID:  # type: ignore[no-untyped-def]
    return (await db.execute(text("""SELECT person_id FROM core.person_identities WHERE external_id LIKE :e || '%' LIMIT 1"""),
                             {"e": ext})).scalar_one()  # type: ignore[no-any-return]


# ─── hàm thuần: cửa sổ, chuỗi, gộp xả tin ────────────────────────────────────────────────────────────────────────────

def test_take_window_caps_60_messages_and_12000_chars() -> None:
    evs = [ev(i, who=f"p{i}") for i in range(70)]            # 70 người khác nhau, mỗi tin cách 5 phút ⇒ không có chuỗi
    assert len(take_window(evs)) == 60
    big = [ev(i, "y" * 5000, who=f"p{i}") for i in range(5)]
    assert [len(e.body) for e in take_window(big)] == [5000, 5000]      # 12.000 ký tự: tin thứ ba vượt ⇒ dừng
    assert len(take_window([ev(0, "z" * 20_000)])) == 1                 # luôn lấy ít nhất một tin


def test_take_window_never_cuts_inside_a_reply_chain_same_sender_within_2_minutes() -> None:
    evs = [ev(i, who=f"p{i}") for i in range(58)]
    # tin 58, 59, 60 cùng một người, cách nhau 30 giây ⇒ một chuỗi; giới hạn 60 rơi giữa chuỗi ⇒ lùi tới ranh giới chuỗi
    base = evs[-1].occurred_at + timedelta(minutes=5)
    chain = [ev(58 + k, who="chain", at=base + timedelta(seconds=30 * k)) for k in range(4)]
    taken = take_window(evs + chain)
    assert [e.sender_key for e in taken].count(chain[0].sender_key) in (0, 4)       # không bao giờ cắt đôi
    assert len(taken) == 58 and taken[-1] is evs[-1]


def test_take_window_reply_chain_by_reply_to() -> None:
    evs = [ev(i, who=f"p{i}", ext=f"e{i}") for i in range(59)]
    a = ev(59, who="q1", ext="ea", at=evs[-1].occurred_at + timedelta(minutes=10))
    b = ev(60, who="q2", ext="eb", at=a.occurred_at + timedelta(minutes=10), reply_ext="ea")     # b trả lời a
    taken = take_window([*evs, a, b])
    assert a not in taken and b not in taken and len(taken) == 59


def test_burst_merge_and_effective_count() -> None:
    flood = [ev(i, f"spam {i}", who="F", at=T0 + timedelta(seconds=10 * i)) for i in range(35)]      # 35 tin / 6 phút
    others = [ev(100 + i, f"hi {i}", who=f"o{i}", at=T0 + timedelta(minutes=1, seconds=i)) for i in range(3)]
    evs = sorted(flood + others, key=lambda e: e.occurred_at)
    assert effective_count([(e.sender_key, e.occurred_at) for e in evs]) == 3 + 1          # 3 tin thường + MỘT khối
    blocks = merge_bursts(evs)
    assert len(blocks) == 4
    block = next(b for b in blocks if b.members)
    assert len(block.all_events) == 35 and block.body.count("\n") == 34 and block.event_id == flood[0].event_id
    slow = [ev(i, f"s {i}", who="S", at=T0 + timedelta(minutes=2 * i)) for i in range(35)]          # 35 tin / 70 phút: không phải xả tin
    assert effective_count([(e.sender_key, e.occurred_at) for e in slow]) == 35


def test_thread_key_helpers() -> None:
    g = uuid.uuid4()
    assert runner.thread_group(f"g:{g}") == g and runner.thread_group("dm:x:y") is None and runner.thread_group("g:zzz") is None
    p, c = uuid.uuid4(), uuid.uuid4()
    assert runner.thread_dm(f"dm:{p}:{c}") == (p, c) and runner.thread_dm("g:1") == (None, None)


# ─── bật/tắt đường theo luồng ───────────────────────────────────────────────────────────────────────────────────────

async def test_group_mode_defaults_off_while_l3_is_stub_and_setting_wins(app, db, redis) -> None:  # type: ignore[no-untyped-def]
    sm = sessionmaker()
    org = await org_id(db)
    r = Refinery(sm, redis, FakeRouter())
    assert l3.IS_STUB and await r.group_mode(org) is False             # stub không trích gì ⇒ không bật mặc định (giữ đường cũ)
    await enable_groups(db, org)
    assert await r.group_mode(org) is True
    await enable_groups(db, org, group_pipeline="off")
    assert await r.group_mode(org) is False


# ─── một lượt = một luồng ───────────────────────────────────────────────────────────────────────────────────────────

async def test_one_analysis_per_thread_and_windows_never_mix_threads(app, db, redis) -> None:  # type: ignore[no-untyped-def]
    sm = sessionmaker()
    org, (g1, g2) = await setup(db, sm, groups=("g1", "g2"))
    await put(sm, org, msg("Hàng về kho chưa anh Bảy?", group="g1"), msg("Chiều nay em báo giá nhé", group="g1", sender="u2", name="Anh Tư"),
              msg("Cần 3 container thép cuộn", group="g2", sender="u3", name="Chị Năm"))
    an = Analyzer()
    st = await refinery(sm, redis, an).run(org, "manual")
    assert sorted(an.threads) == sorted([f"g:{g1}", f"g:{g2}"])
    for w in an.windows:
        gname = w.thread_ref
        assert len(w.new_events) == (2 if gname == f"g:{g1}" else 1)
        assert not w.context_events
    assert (st.total, st.status) == (3, "done")
    rows = (await db.execute(text("SELECT thread_key, status, stage, new_n, contract_version FROM refinery.analyses ORDER BY thread_key"))).all()
    assert {(r.thread_key, r.status, r.stage, r.contract_version) for r in rows} == {
        (f"g:{g1}", "ok", "extract", "brain-out/1"), (f"g:{g2}", "ok", "extract", "brain-out/1")}
    # không có đơn vị nào ⇒ mọi tin "không có sự việc"
    assert await states(db, org) == {"discarded": 3}


async def test_unit_evidence_mention_and_attributed_notebook(app, db, redis) -> None:  # type: ignore[no-untyped-def]
    sm = sessionmaker()
    org, (g1,) = await setup(db, sm)
    await put(sm, org, msg("Em hứa chiều nay giao 3 container cho anh Bảy nhé", sender="u1", name="Chị Lan"), msg("ok", sender="u2", name="Anh Tư"))
    lan = await person_of(db, "u1")

    def build(w):  # type: ignore[no-untyped-def]
        return out_with(w, unit_for(w, "chiều nay giao 3 container", kind="nghia_vu_loi_hua", summary="Lan hứa giao 3 container cho anh Bảy",
                                    people=[PersonMention(surface="Chị Lan", person_ref=f"u_{lan}", role="nguoi_hua"),
                                            PersonMention(surface="anh Bảy", person_ref="u_" + str(uuid.uuid4()), role="nguoi_duoc_hua")]))

    an = Analyzer(build)
    st = await refinery(sm, redis, an).run(org, "manual")
    u = (await db.execute(text("""SELECT id, event_type, conclusion, kind_hint, analysis_id, extractor_version, person_id, group_id, confidence,
                                         flags, gate_candidate FROM clean.meaning_units"""))).one()
    assert (u.event_type, u.kind_hint, u.person_id, u.group_id, u.extractor_version) == ("GroupUnit", "nghia_vu_loi_hua", lan, g1, l3.EXTRACTOR_VERSION)
    assert u.conclusion.startswith("Lan hứa") and float(u.confidence) == 0.8 and u.flags["public"] is False
    ev_rows = (await db.execute(text("SELECT quote, quote_status, source_field, span_start, span_end, extractor_version FROM clean.evidence "
                                     "WHERE meaning_unit_id = :u"), {"u": u.id})).all()
    assert len(ev_rows) == 1 and ev_rows[0].quote_status == "verified" and ev_rows[0].source_field == "body"
    body = "Em hứa chiều nay giao 3 container cho anh Bảy nhé"
    assert body[ev_rows[0].span_start:ev_rows[0].span_end] == ev_rows[0].quote == "chiều nay giao 3 container"
    # mention: người có mặt ⇒ linked; mã người lạ của model ⇒ KHÔNG nối bằng mã đó
    ms = (await db.execute(text("SELECT surface, status, entity_id FROM mem.mentions ORDER BY surface"))).all()
    assert {m.surface: m.status for m in ms} == {"Chị Lan": "linked", "anh Bảy": "unresolved"}
    # trạng thái tin: tin có đơn vị ⇒ clean; tin "ok" ⇒ đã bị loại ở bước quét (không cần model)
    assert await states(db, org) == {"clean": 1, "discarded": 1}
    assert st.clean == 1 and str(u.id) in st.unit_ids
    # sổ tay: chỉ câu có nguồn "theo lời <người>" kèm tham chiếu meaning_unit
    entries = (await db.execute(text("SELECT body, refs FROM memory.entries"))).all()
    code = (await db.execute(text("SELECT code FROM core.persons WHERE id = :p"), {"p": lan})).scalar_one()
    assert entries and all(e.body.startswith(f"theo lời {code}: ") for e in entries)          # mã người do hệ thống cấp, không phải tên hiển thị
    assert not any("Chị Lan" in e.body for e in entries)
    assert all(any(r["type"] == "meaning_unit" and r["id"] == str(u.id) for r in e.refs) for e in entries)


async def test_fact_written_through_assert_fact_with_verified_evidence_in_one_transaction(app, db, redis) -> None:  # type: ignore[no-untyped-def]
    sm = sessionmaker()
    org, _ = await setup(db, sm)
    await put(sm, org, msg("Số em là 0901234567 nha anh", sender="u1", name="Chị Lan"))
    lan = await person_of(db, "u1")
    an = Analyzer(lambda w: out_with(w, unit_for(w, "Số em là 0901234567", kind=None, summary="Lan cho số"),
                                     facts=[phone_fact(w, "Số em là", lan)]))
    st = await refinery(sm, redis, an).run(org, "manual")
    f = (await db.execute(text("""SELECT f.predicate, f.status, f.source_class, f.object_value, f.extractor_version, f.valid_from, f.subject_id,
                                         (SELECT count(*) FROM mem.fact_evidence e WHERE e.fact_id = f.id AND e.quote_status = 'verified') AS ev
                                  FROM mem.facts f"""))).one()
    assert (f.predicate, f.status, f.source_class, f.ev) == ("contact_phone", "asserted", "insider", 1)
    assert f.object_value == "0901234567" and f.extractor_version == l3.EXTRACTOR_VERSION
    assert st.facts_written == 1
    a = (await db.execute(text("SELECT errors, status FROM refinery.analyses"))).one()
    assert a.errors == [] and a.status == "ok"


async def test_fact_with_unknown_subject_or_unquoted_unit_is_dropped(app, db, redis) -> None:  # type: ignore[no-untyped-def]
    sm = sessionmaker()
    org, _ = await setup(db, sm)
    await put(sm, org, msg("Số em là 0901234567 nha anh", sender="u1", name="Chị Lan"))
    stranger = uuid.uuid4()                                                      # mã người model bịa
    an = Analyzer(lambda w: out_with(w, unit_for(w, "Số em là", summary="x"), facts=[phone_fact(w, "Số em là", stranger)]))
    await refinery(sm, redis, an).run(org, "manual")
    assert (await db.execute(text("SELECT count(*) FROM mem.facts"))).scalar_one() == 0


async def test_absent_quote_drops_unit_and_event_is_not_cited(app, db, redis) -> None:  # type: ignore[no-untyped-def]
    sm = sessionmaker()
    org, _ = await setup(db, sm)
    await put(sm, org, msg("Hẹn mai giao hàng nhé anh", sender="u1"))

    def build(w):  # type: ignore[no-untyped-def]
        e = find_msg(w, "Hẹn mai")
        u = unit_for(w, "Hẹn mai", summary="Hẹn giao", quote="giao hàng NGAY HÔM NAY")           # trích dẫn bịa ⇒ absent
        return out_with(w, u.model_copy(update={"quotes": [u.quotes[0].model_copy(update={"message_id": e.message_id})]}))

    await refinery(sm, redis, Analyzer(build)).run(org, "manual")
    assert (await db.execute(text("SELECT count(*) FROM clean.meaning_units"))).scalar_one() == 0
    a = (await db.execute(text("SELECT errors FROM refinery.analyses"))).scalar_one()
    assert any(e.get("code") == "unit_absent" for e in a)                       # CHỈ mã lỗi + unit_ref, không có nội dung tin
    assert "giao hàng" not in str(a)
    assert await states(db, org) == {"discarded": 1}


async def test_overflow_continues_next_turn_in_same_thread(app, db, redis) -> None:  # type: ignore[no-untyped-def]
    sm = sessionmaker()
    org, (g1,) = await setup(db, sm)
    base = datetime(2026, 10, 20, 8, 0, tzinfo=UTC)
    payloads = []
    for i in range(65):                                                       # 65 người khác nhau (không có chuỗi) trong một nhóm
        p = msg(f"Tin số {i} về hàng nhé", sender=f"s{i}", name=f"Người {i}")
        p["occurred_at"] = (base + timedelta(minutes=5 * i)).isoformat()
        payloads.append(p)
    await put(sm, org, *payloads)
    an = Analyzer()
    r = refinery(sm, redis, an)
    await r.run(org, "manual")
    assert [len(w.new_events) for w in an.windows][0] == 60 and await pending_n(db, org) == 5
    await r.run(org, "manual")
    assert [len(w.new_events) for w in an.windows] == [60, 5]
    # lượt kế mang 30 tin gần nhất của lượt trước làm ngữ cảnh (nhãn "ngữ cảnh"), không lẫn vào tin mới
    w2 = an.windows[1]
    assert len(w2.context_events) == 30 and all(c.context for c in w2.context_events) and not any(e.context for e in w2.new_events)
    assert max(c.sent_at for c in w2.context_events) < min(e.sent_at for e in w2.new_events)
    assert await pending_n(db, org) == 0


async def test_context_window_is_30_messages_and_24_hours(app, db, redis) -> None:  # type: ignore[no-untyped-def]
    sm = sessionmaker()
    org, _ = await setup(db, sm)
    now = datetime.now(UTC)
    old = msg("Tin cũ hơn 24 giờ", sender="u1")
    old["occurred_at"] = (now - timedelta(hours=30)).isoformat()
    recent = []
    for i in range(35):
        p = msg(f"Tin gần số {i}", sender="u1")
        p["occurred_at"] = (now - timedelta(hours=3) + timedelta(minutes=i)).isoformat()
        recent.append(p)
    await put(sm, org, old, *recent)
    an = Analyzer()
    r = refinery(sm, redis, an)
    await r.run(org, "manual")                                                 # xử lý hết lô đầu
    await put(sm, org, msg("Tin mới nhất", sender="u1"))
    await r.run(org, "manual")
    ctx = an.bodies(-1, context=True)
    assert len(ctx) == 30 and "Tin cũ hơn 24 giờ" not in ctx and ctx[-1] == "Tin gần số 34"
    assert an.bodies(-1) == ["Tin mới nhất"]


async def test_trivial_messages_never_reach_model(app, db, redis) -> None:  # type: ignore[no-untyped-def]
    sm = sessionmaker()
    org, _ = await setup(db, sm)
    stk = msg("", sender="u1")
    stk["kind"] = "sticker"
    await put(sm, org, msg("dạ", sender="u1"), msg("ok ạ", sender="u2"), stk, msg("cảm ơn anh", sender="u3"))
    an = Analyzer()
    st = await refinery(sm, redis, an).run(org, "manual")
    assert an.windows == [] and st.total == 0
    assert await states(db, org) == {"discarded": 4}
    d = (await db.execute(text("SELECT DISTINCT detail ->> 'discarded_by' AS by FROM refinery.event_state"))).scalar_one()
    assert d == "no_model"                                                    # vẫn đếm được ở dòng độ phủ


async def test_repeated_template_message_is_not_a_new_event(app, db, redis) -> None:  # type: ignore[no-untyped-def]
    sm = sessionmaker()
    org, _ = await setup(db, sm)
    tpl = "Kính gửi quý khách, bảng giá thép cuộn tháng 10 đã cập nhật, vui lòng xem file đính kèm nhé"
    for i in range(3):
        await put(sm, org, msg(tpl, sender=f"u{i}", name=f"Người {i}"))
    an = Analyzer()
    r = refinery(sm, redis, an)
    await r.groups.ensure_scanned(org)
    rows = (await db.execute(text("SELECT detail ->> 'discarded_by' AS by, state FROM refinery.event_state ORDER BY event_received_at"))).all()
    assert [(x.state, x.by) for x in rows] == [("pending", None), ("pending", None), ("discarded", "no_model")]    # lần thứ 3 trong 7 ngày
    await r.run(org, "manual")
    assert len(an.windows[0].new_events) == 2


# ─── lỗi: giữ tin chờ ───────────────────────────────────────────────────────────────────────────────────────────────

async def test_model_unavailable_keeps_events_pending_and_backs_off(app, db, redis) -> None:  # type: ignore[no-untyped-def]
    sm = sessionmaker()
    org, _ = await setup(db, sm)
    await put(sm, org, msg("Cần báo giá thép gấp", sender="u1"), msg("Kho còn 20 tấn", sender="u2"))
    model = await mk_model(db, org)
    router = LoggingRouter(sm, model, down=True)
    from gh.providers.router import ModelUnavailable

    an = Analyzer(raises=ModelUnavailable(["x"], no_chain=False))
    st = await refinery(sm, redis, an, router).run(org, "manual")
    assert st.status == "failed" and "giữ chờ" in (st.error or "")
    assert await states(db, org) == {"pending": 2}
    d = (await db.execute(text("SELECT detail ->> 'next_try_at' AS nt, attempts FROM refinery.event_state LIMIT 1"))).one()
    assert d.nt is not None and d.attempts == 0                                    # hoàn lượt thử: model chết không phải lỗi của tin
    # lùi nhịp: lượt thủ công NGAY SAU đó không nhận lại tin (chưa tới next_try_at)
    an2 = Analyzer()
    await refinery(sm, redis, an2).run(org, "manual")
    assert an2.windows == [] and await states(db, org) == {"pending": 2}
    a = (await db.execute(text("SELECT status, errors FROM refinery.analyses"))).one()
    assert a.status == "failed" and a.errors[0]["code"] == "model_unavailable"


async def test_contract_error_counts_attempts_then_errors(app, db, redis) -> None:  # type: ignore[no-untyped-def]
    sm = sessionmaker()
    org, _ = await setup(db, sm)
    await put(sm, org, msg("Cần báo giá thép gấp", sender="u1"))
    bad = Analyzer(raises=l3.ContractError(["units.0.kind: sai enum"], model="fake"))
    r = refinery(sm, redis, bad)
    for _ in range(3):
        await r.run(org, "manual")
        await db.execute(text("UPDATE refinery.event_state SET detail = detail - 'next_try_at'"))      # bỏ lùi nhịp để thử lại ngay
        await db.commit()
    assert await states(db, org) == {"error": 1}
    rows = (await db.execute(text("SELECT status, errors FROM refinery.analyses WHERE status = 'contract_error'"))).all()
    assert len(rows) == 3 and rows[0].errors[0]["code"] == "contract_error"
    assert "sai enum" in rows[0].errors[1]["code"]                                # mã lỗi schema, không có nội dung tin
    assert "báo giá" not in str(rows[0].errors)


async def test_write_failure_rolls_back_whole_turn(app, db, redis, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    sm = sessionmaker()
    org, _ = await setup(db, sm)
    await put(sm, org, msg("Em hứa giao hàng chiều nay nhé", sender="u1"))
    lan = await person_of(db, "u1")

    async def boom(*a, **k):  # type: ignore[no-untyped-def]
        raise RuntimeError("hỏng giữa chừng")

    monkeypatch.setattr(runner.facts_mod, "assert_facts", boom)
    an = Analyzer(lambda w: out_with(w, unit_for(w, "giao hàng chiều nay", kind="nghia_vu_loi_hua", summary="hứa"),
                                     facts=[phone_fact(w, "giao hàng", lan)]))
    await refinery(sm, redis, an).run(org, "manual")
    # giao dịch ghi bị rollback: không có đơn vị/sự thật/phân tích 'ok'; tin về pending để lượt sau thử lại
    assert (await db.execute(text("SELECT count(*) FROM clean.meaning_units"))).scalar_one() == 0
    assert (await db.execute(text("SELECT count(*) FROM memory.entries"))).scalar_one() == 0
    assert await states(db, org) == {"pending": 1}
    assert (await db.execute(text("SELECT status FROM refinery.analyses"))).scalar_one() == "failed"


async def test_unreadable_media_is_counted_not_guessed(app, db, redis) -> None:  # type: ignore[no-untyped-def]
    sm = sessionmaker()
    org, _ = await setup(db, sm)
    [eid] = await put(sm, org, msg("Em gửi ghi âm anh nghe nhé nhé nhé", sender="u1"))
    rcv = (await db.execute(text("SELECT received_at FROM raw.events WHERE id = :e"), {"e": eid})).scalar_one()
    await db.execute(text("""INSERT INTO raw.attachments (event_id, event_received_at, storage_key, mime, bytes, sha256)
                             VALUES (:e, :r, 'k', 'audio/ogg', 10, '\\x00')"""), {"e": eid, "r": rcv})
    await db.commit()
    an = Analyzer()
    await refinery(sm, redis, an).run(org, "manual")
    assert dict(an.windows[0].unreadable) == {"voice": 1, "image": 0, "file": 0}
    # chữ từ voice (đã có transcript ok) ⇒ vào media_texts, không còn tính "không đọc được"
    await put(sm, org, msg("Tin thứ hai để chạy lượt nữa nhé", sender="u1"))


# ─── sổ chi phí: gắn thẻ luồng, CLEAN_READY ─────────────────────────────────────────────────────────────────────────

async def test_model_calls_tagged_with_thread_and_analysis_and_clean_ready_published(app, db, redis) -> None:  # type: ignore[no-untyped-def]
    sm = sessionmaker()
    org, (g1,) = await setup(db, sm)
    await put(sm, org, msg("Em hứa giao hàng cho anh vào thứ Sáu nhé", sender="u1"))
    model = await mk_model(db, org)
    bus = Bus()
    an = Analyzer(lambda w: out_with(w, unit_for(w, "giao hàng cho anh", kind="nghia_vu_loi_hua", summary="hứa giao")))
    r = refinery(sm, redis, an, LoggingRouter(sm, model), bus)
    st = await r.run(org, "manual")
    row = (await db.execute(text("SELECT thread_key, analysis_id, purpose, status FROM agent.model_calls"))).one()
    aid = (await db.execute(text("SELECT id FROM refinery.analyses"))).scalar_one()
    assert (row.thread_key, row.analysis_id, row.purpose, row.status) == (f"g:{g1}", aid, "refinery.group", "ok")
    ready = [b for b in bus.sent if b[0] == CLEAN_READY]
    assert len(ready) == 1 and ready[0][2]["ids"] == st.unit_ids and ready[0][3] == org
    wm = (await db.execute(text("SELECT thread_key, last_analysis_at, pending_n FROM refinery.group_watermarks"))).one()
    assert wm.thread_key == f"g:{g1}" and wm.last_analysis_at is not None and wm.pending_n == 0
    v = (await db.execute(text("SELECT calls, cost_vnd FROM refinery.group_budget"))).one()                   # view L1: chi phí theo luồng
    assert v.calls == 1 and float(v.cost_vnd) == 0.0                                                         # CLI thuê bao = 0 ₫


async def test_lock_prevents_two_group_runs_of_one_org(app, db, redis) -> None:  # type: ignore[no-untyped-def]
    sm = sessionmaker()
    org, _ = await setup(db, sm)
    await put(sm, org, msg("Cần báo giá thép nhé anh", sender="u1"))
    assert await redis.set(f"gh:refinery:groups:{org}", "1", nx=True, ex=60)
    an = Analyzer()
    st = await refinery(sm, redis, an).run(org, "manual")
    assert an.windows == [] and st.total == 0 and await pending_n(db, org) == 1
    await redis.delete(f"gh:refinery:groups:{org}")
    await refinery(sm, redis, an).run(org, "manual")
    assert len(an.windows) == 1


async def test_dm_threads_are_separate_per_person(app, db, redis) -> None:  # type: ignore[no-untyped-def]
    sm = sessionmaker()
    org, _ = await setup(db, sm)
    await put(sm, org, msg("Em muốn đặt thép cuộn", group=None, sender="a1", name="Khách A"),
              msg("Giá thép bao nhiêu vậy", group=None, sender="b1", name="Khách B"),
              msg("Giao ngay được không", group=None, sender="a1", name="Khách A"))
    an = Analyzer()
    await refinery(sm, redis, an).run(org, "manual")
    assert len(an.windows) == 2 and all(w.thread_ref.startswith("dm:") for w in an.windows)
    sizes = sorted(len(w.new_events) for w in an.windows)
    assert sizes == [1, 2]
    a_pid = await person_of(db, "a1")
    wa = next(w for w in an.windows if f"dm:{a_pid}:" in w.thread_ref)
    assert [e.body for e in wa.new_events] == ["Em muốn đặt thép cuộn", "Giao ngay được không"]


@pytest.mark.parametrize("raw", ["", None])
def test_gev_message_id_is_stable(raw) -> None:  # type: ignore[no-untyped-def]
    e = ev(1)
    assert e.message_id == f"m_{e.event_id}" and e.all_events == [e]


async def test_contract_error_backs_off_and_does_not_retry_within_the_same_run(app, db, redis) -> None:  # type: ignore[no-untyped-def]
    sm = sessionmaker()
    org, _ = await setup(db, sm)
    await put(sm, org, msg("Em lỡ nhập mã OTP vào link đó rồi", sender="u1"))          # ứng viên an toàn ⇒ lượt chạy ngay, vòng 2 cũng quét lớp này
    bad = Analyzer(raises=l3.ContractError(["json_invalid"], model="fake"))
    await refinery(sm, redis, bad).run(org, "schedule")
    assert len(bad.windows) == 1                                                   # lùi nhịp: không dội lại model trong cùng lượt chạy
    d = (await db.execute(text("SELECT state, attempts, detail ->> 'next_try_at' AS nt FROM refinery.event_state"))).one()
    assert d.state == "pending" and d.attempts == 1 and d.nt is not None


async def test_member_present_at_group_discovery_is_not_a_stranger_but_late_joiner_is(app, db, redis) -> None:  # type: ignore[no-untyped-def]
    sm = sessionmaker()
    org, (gid,) = await setup(db, sm)
    ask = "Chuyển khoản gấp 15 triệu vào stk 0123456789 giúp em"
    await put(sm, org, msg(ask, sender="old", name="Thành viên có sẵn"), msg(ask, sender="new", name="Người mới vào"))
    old, new = await person_of(db, "old"), await person_of(db, "new")
    now = datetime.now(UTC)
    await db.execute(text("UPDATE core.groups SET created_at = :c WHERE id = :g"), {"c": now - timedelta(days=60), "g": gid})
    await db.execute(text("""INSERT INTO core.group_members (group_id, person_id, joined_at) VALUES (:g, :o, :jo), (:g, :n, :jn)
                             ON CONFLICT (group_id, person_id) DO UPDATE SET joined_at = EXCLUDED.joined_at"""),
                     {"g": gid, "o": old, "jo": now - timedelta(days=59, hours=23), "n": new, "jn": now - timedelta(days=2)})
    await db.commit()
    await refinery(sm, redis, Analyzer()).groups.ensure_scanned(org)
    rows = (await db.execute(text("""SELECT e.sender_identity_id, s.detail -> 'cands' AS c FROM refinery.event_state s
                                     JOIN raw.events e ON e.id = s.event_id AND e.received_at = s.event_received_at"""))).all()
    by_kind = {}
    for r in rows:
        by_kind[r.sender_identity_id] = next(c for c in r.c if c["flag"] == "money_request")["kind"]
    assert sorted(by_kind.values(), key=str) == [None, "rui_ro_lua_dao"]           # chỉ người vào nhóm 2 ngày trước bị nâng thành nghi lừa đảo


async def test_failed_turn_still_tags_its_model_calls_to_the_thread(app, db, redis) -> None:  # type: ignore[no-untyped-def]
    sm = sessionmaker()
    org, (g1,) = await setup(db, sm)
    await put(sm, org, msg("Cần báo giá thép nhé anh", sender="u1"))
    model = await mk_model(db, org)
    bad = Analyzer(raises=l3.ContractError(["json_invalid"], model="fake"))                 # gọi model (ghi sổ) rồi mới sai hợp đồng
    await refinery(sm, redis, bad, LoggingRouter(sm, model)).run(org, "manual")
    row = (await db.execute(text("SELECT thread_key, analysis_id FROM agent.model_calls"))).one()
    aid = (await db.execute(text("SELECT id FROM refinery.analyses WHERE status = 'contract_error'"))).scalar_one()
    assert (row.thread_key, row.analysis_id) == (f"g:{g1}", aid)                              # lượt lỗi vẫn tính vào trần của luồng
