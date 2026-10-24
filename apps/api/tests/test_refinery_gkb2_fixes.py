# ruff: noqa: E501
"""Sửa sau lần chạy G-KB kb1 lần 2 (kb1-r2, pipeline thật + agent đóng vai model):

(i) lượt nhận một phần tồn đọng của luồng (trần 60 tin, lượt an toàn nhận ứng viên trước, nhập lịch sử…) không biết còn tin đã đến
    mà chưa đưa vào ⇒ model kết luận "khách nguội/không đặt hàng". Lượt phải BIẾT phần tồn đọng (đếm + khoảng giờ gửi, không nội dung),
    và lời xác nhận ngắn (ack) lấy làm bằng chứng không được vượt qua tin tồn đọng đầu tiên sau cửa sổ.
(ii) `memory_updates.promise_kept` bị bỏ qua ⇒ sự thật `promised_to` mãi "đang hiệu lực". Lời hứa đã giữ ĐÓNG hiệu lực
    (`valid_to` = giờ gửi tin chứng minh đã giữ), không xoá, không đổi trạng thái (spec §4A.1 CNS-095, kỹ năng v2.1 §9.3)."""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import text

from gh.biz.mem import contexts, profile_read
from gh.biz.mem.profile_read import ProfileSnapshot
from gh.biz.mem.retrieve import ContextPack
from gh.db import sessionmaker
from gh.refinery import extract
from gh.refinery.brain_contract import BrainOut, FactObject, FactProposed, FactSubject, MemoryUpdate, Quote
from gh.refinery.runner import Refinery
from tests.phase2 import FakeRouter, listen, msg, org_id, put
from tests.refinery_group_helpers import Analyzer, enable_groups, find_msg, out_with, unit_for

THREAD = "g:6f1c7e2a-0000-4000-8000-000000000001"
NOW = datetime(2026, 10, 8, 10, 10, tzinfo=UTC)


async def setup(db):  # type: ignore[no-untyped-def]
    org = await org_id(db)
    gid = await listen(db, org, "g1")
    await enable_groups(db, org)
    return org, gid


def refinery(sm, redis, analyzer: Analyzer) -> Refinery:  # type: ignore[no-untyped-def]
    return Refinery(sm, redis, FakeRouter(), None, analyzer=analyzer)


async def person_of(db, ext: str) -> uuid.UUID:  # type: ignore[no-untyped-def]
    return (await db.execute(text("SELECT person_id FROM core.person_identities WHERE external_id LIKE :e || '%' LIMIT 1"),
                             {"e": ext})).scalar_one()  # type: ignore[no-any-return]


# ─── (i) phần tồn đọng của luồng ─────────────────────────────────────────────────────────────────────────────────────

def _prompt(backlog: "extract.Backlog | None") -> str:
    ev = [extract.GroupEvent("m_1", NOW - timedelta(days=12), "Chị lấy 20kg Blend Honey nha", None, "Chị An", raw_event_id=uuid.uuid4())]
    w = extract.GroupWindow(THREAD, ev, now=NOW, backlog=backlog)
    pol = contexts.unconfirmed(THREAD)
    return extract.build_user_prompt(w, ContextPack(THREAD, pol, 0), ProfileSnapshot(uuid.uuid4(), 0, None, ()), pol)


def test_prompt_states_unseen_backlog_with_count_and_time_range_only() -> None:
    p = _prompt(extract.Backlog(44, NOW - timedelta(days=10), NOW - timedelta(hours=1)))
    tail = p[p.index("Bây giờ"):]
    assert "44 tin" in tail and "2026-09-28T17:10+0700" in tail and "2026-10-08T16:10+0700" in tail
    assert "im lặng" in tail                                         # cấm kết luận vắng mặt/im lặng khi chưa thấy các tin đó
    assert _prompt(None).count("chưa đưa vào lượt này") == 0 and "chưa đưa vào lượt này" in tail
    assert _prompt(extract.Backlog(0, NOW, NOW)).count("chưa đưa vào lượt này") == 0
    done = _prompt(extract.Backlog(0, done_after=3, done_after_at=NOW - timedelta(days=2)))
    assert "chưa đưa vào lượt này" not in done and "3 tin gửi từ 2026-10-06T17:10+0700 trở đi đã được phân tích ở lượt trước" in done


async def test_carry_turn_knows_its_backlog_and_acks_stop_at_the_first_unseen_message(app, db, redis) -> None:  # type: ignore[no-untyped-def]
    """kb1-r2 lượt 3: 60 tin tới 26/09 + 2 sticker/"ok" của 05–07/10 (vượt qua 44 tin tồn của chị An 28/09–08/10), "Bây giờ" 08/10
    ⇒ model chấm "chị An nguội". Lượt nối tiếp phải mang phần tồn đọng; ack chỉ lấy tới trước tin tồn đọng đầu tiên."""
    sm = sessionmaker()
    org, _ = await setup(db)
    base = datetime.now(UTC) - timedelta(days=20)
    payloads = []
    for i in range(65):                                                    # 65 người khác nhau (không chuỗi) ⇒ lượt đầu 60, dư 5
        p = msg(f"Tin số {i} về đơn hàng nhé", sender=f"s{i}", name=f"Người {i}")
        p["occurred_at"] = (base + timedelta(hours=6 * i)).isoformat()
        payloads.append(p)
    early_ack = msg("Dạ e ghi nhận ạ", sender="thao", name="Thảo")          # nằm GIỮA cửa sổ lượt đầu ⇒ vẫn là bằng chứng của lượt đầu
    early_ack["occurred_at"] = (base + timedelta(hours=6 * 30 + 1)).isoformat()
    late_ack = msg("ok em", sender="ly", name="Ly")                          # nằm SAU tin tồn đọng đầu tiên (tin 60) ⇒ không thuộc lượt đầu
    late_ack["occurred_at"] = (base + timedelta(hours=6 * 62 + 1)).isoformat()
    await put(sm, org, *payloads, early_ack, late_ack)
    an = Analyzer()
    r = refinery(sm, redis, an)
    await r.run(org, "manual")
    w1 = an.windows[0]
    bodies1 = [e.body for e in w1.new_events]
    assert sum(1 for b in bodies1 if b and b.startswith("Tin số")) == 60
    assert "Dạ e ghi nhận ạ" in bodies1 and "ok em" not in bodies1
    assert w1.backlog is not None and w1.backlog.n == 5
    assert w1.backlog.first_at == base + timedelta(hours=6 * 60) and w1.backlog.last_at == base + timedelta(hours=6 * 64)
    prompt = extract.build_user_prompt(w1, an.packs[0], await profile_read.snapshot(db, org), an.packs[0].policy)
    assert "5 tin" in prompt[prompt.index("Bây giờ"):]
    assert "Tin số 61" not in prompt                                         # phần tồn đọng chỉ có đếm + giờ, không nội dung
    await r.run(org, "manual")
    w2 = an.windows[1]
    assert [e.body for e in w2.new_events] == ["Tin số 60 về đơn hàng nhé", "Tin số 61 về đơn hàng nhé", "Tin số 62 về đơn hàng nhé",
                                               "ok em", "Tin số 63 về đơn hàng nhé", "Tin số 64 về đơn hàng nhé"]
    assert w2.backlog is None                                                # hết tồn đọng ⇒ không có dòng cảnh báo


async def test_safety_turn_knows_the_older_backlog_it_skipped(app, db, redis) -> None:  # type: ignore[no-untyped-def]
    """Lượt an toàn nhận ứng viên trước tin tồn cũ hơn (đúng spec) ⇒ phải biết còn tin cũ hơn chưa thấy; lượt thường sau đó (tin cũ hơn)
    phải biết các tin MỚI hơn đã phân tích ở lượt an toàn, và không nhận lời xác nhận ngắn nằm sau chúng (kb1-r2 lượt 19→20)."""
    sm = sessionmaker()
    org, _ = await setup(db)
    base = datetime.now(UTC) - timedelta(hours=5)
    for i in range(8):
        p = msg(f"Tin thường số {i} về hàng nhé", sender=f"n{i}", name=f"Người {i}")
        p["occurred_at"] = (base + timedelta(minutes=i)).isoformat()
        await put(sm, org, p)
    otp = msg("Em lỡ nhập mã OTP vào link đó rồi", sender="victim", name="Nạn nhân")
    otp["occurred_at"] = (base + timedelta(minutes=200)).isoformat()
    ack = msg("ok em", sender="ly", name="Ly")
    ack["occurred_at"] = (base + timedelta(minutes=201)).isoformat()
    await put(sm, org, otp, ack)
    an = Analyzer()
    r = refinery(sm, redis, an)
    await r.run(org, "schedule")
    w = an.windows[0]
    taken = {e.body for e in w.new_events}
    assert "Em lỡ nhập mã OTP vào link đó rồi" in taken
    normal_in_safety = sum(1 for b in taken if b and b.startswith("Tin thường"))
    assert w.backlog is not None and w.backlog.n == 8 - normal_in_safety and w.backlog.done_after == 0
    assert w.backlog.first_at == base
    await r.run(org, "manual")
    w2 = an.windows[1]
    assert all((e.body or "").startswith("Tin thường") for e in w2.new_events) and "ok em" not in [e.body for e in w2.new_events]
    assert w2.backlog is not None and w2.backlog.n == 0 and w2.backlog.done_after == normal_in_safety + 1
    assert w2.backlog.done_after_at == base + timedelta(minutes=8 - normal_in_safety)
    prompt = extract.build_user_prompt(w2, an.packs[1], await profile_read.snapshot(db, org), an.packs[1].policy)
    assert "đã được phân tích ở lượt trước" in prompt and "OTP" not in prompt[prompt.index("Bây giờ"):]


# ─── (ii) lời hứa đã giữ đóng hiệu lực sự thật ───────────────────────────────────────────────────────────────────────

def _promise_turn(khoa: uuid.UUID, an_p: uuid.UUID):  # type: ignore[no-untyped-def]
    def build(w):  # type: ignore[no-untyped-def]
        u = unit_for(w, "tuần sau e gửi chị 2 túi mẫu decaf", kind="nghia_vu_loi_hua", summary="Khoa hứa gửi chị An 2 túi decaf")
        promise = find_msg(w, "tuần sau e gửi").message_id
        ask = find_msg(w, "có decaf không").message_id
        u = u.model_copy(update={"quotes": [*u.quotes, Quote(message_id=ask, field="body", text="bên mình có decaf không")]})
        return out_with(w, u, facts=[
            FactProposed(unit_ref="u1", subject=FactSubject(surface="Khoa", person_ref=f"u_{khoa}"), predicate="promised_to",
                         object=FactObject(value="gửi 2 túi mẫu decaf", person_ref=f"u_{an_p}"), quote_refs=[promise]),
            FactProposed(unit_ref="u1", subject=FactSubject(surface="chị An", person_ref=f"u_{an_p}"), predicate="needs",
                         object=FactObject(value="cà phê decaf"), quote_refs=[ask])])
    return build


async def _seed_promise(db, sm, redis, org):  # type: ignore[no-untyped-def]
    t0 = datetime.now(UTC) - timedelta(hours=10)
    ask = msg("Khoa ơi bên mình có decaf không, khách bầu hỏi hoài", sender="an", name="An Hội An Cafe")
    pr = msg("Dạ tuần sau e gửi chị 2 túi mẫu decaf thử nha", sender="khoa", name="Khoa Rang Xay MR")
    ask["occurred_at"], pr["occurred_at"] = t0.isoformat(), (t0 + timedelta(minutes=18)).isoformat()
    await put(sm, org, ask, pr)
    an_p, khoa = await person_of(db, "an"), await person_of(db, "khoa")
    r = refinery(sm, redis, Analyzer(_promise_turn(khoa, an_p)))
    await r.run(org, "manual")
    return r, t0


async def _facts(db):  # type: ignore[no-untyped-def]
    return {x.predicate: x for x in (await db.execute(text(
        "SELECT id, predicate, status, valid_from, valid_to, closed_recorded_at FROM mem.facts ORDER BY predicate"))).all()}


async def test_promise_kept_closes_the_promise_fact_by_valid_to_without_deleting(app, db, redis) -> None:  # type: ignore[no-untyped-def]
    sm = sessionmaker()
    org, _ = await setup(db)
    r, t0 = await _seed_promise(db, sm, redis, org)
    before = await _facts(db)
    assert before["promised_to"].valid_to is None and before["promised_to"].status == "asserted"
    kept = msg("Nhận 2 túi decaf rồi nha Khoa, khách khen thơm 👍", sender="an", name="An Hội An Cafe")
    kept_at = t0 + timedelta(hours=6)
    kept["occurred_at"] = kept_at.isoformat()
    await put(sm, org, kept)

    def build(w):  # type: ignore[no-untyped-def]
        ids = [find_msg(w, "tuần sau e gửi").message_id, find_msg(w, "Nhận 2 túi decaf").message_id]       # tin hứa (ngữ cảnh) + tin giữ
        return BrainOut(thread_ref=w.thread_ref, memory_updates=[MemoryUpdate(type="promise_kept", text="Khoa đã gửi decaf, chị An đã nhận", message_ids=ids)])
    r.groups.analyze = Analyzer(build)
    await r.run(org, "manual")
    after = await _facts(db)
    p = after["promised_to"]
    assert p.id == before["promised_to"].id and p.status == "asserted"            # không xoá, không đổi trạng thái (CNS-095)
    assert p.valid_to == kept_at and p.closed_recorded_at is not None
    assert after["needs"].valid_to is None                                         # spec không có đường đóng `needs` khi được đáp ứng
    ev = (await db.execute(text("SELECT by, reason, valid_to_after FROM mem.fact_status_events WHERE fact_id = :f AND valid_to_after IS NOT NULL"),
                           {"f": p.id})).all()
    assert [(e.by, e.reason) for e in ev] == [("extractor", "promise_kept")]
    # lượt sau: khối "Sự thật đã biết" không còn lời hứa đã giữ
    await put(sm, org, msg("Tuần này chị lấy 20kg Blend Honey nha", sender="an", name="An Hội An Cafe"))
    an3 = Analyzer()
    r.groups.analyze = an3
    await r.run(org, "manual")
    texts = [i.text for i in an3.packs[-1].facts]
    assert not any("promised_to" in t for t in texts) and any("needs" in t for t in texts)


async def test_promise_kept_without_the_promise_message_closes_nothing(app, db, redis) -> None:  # type: ignore[no-untyped-def]
    """Không có mã tin hứa ⇒ không đoán lời hứa nào (không nối theo chữ); đếm `promise_kept_unmatched` để G-KB thấy."""
    sm = sessionmaker()
    org, _ = await setup(db)
    r, t0 = await _seed_promise(db, sm, redis, org)
    kept = msg("Nhận 2 túi decaf rồi nha Khoa", sender="an", name="An Hội An Cafe")
    kept["occurred_at"] = (t0 + timedelta(hours=6)).isoformat()
    await put(sm, org, kept)

    def build(w):  # type: ignore[no-untyped-def]
        return BrainOut(thread_ref=w.thread_ref, memory_updates=[MemoryUpdate(type="promise_kept", text="đã nhận decaf",
                                                                              message_ids=[find_msg(w, "Nhận 2 túi decaf").message_id])])
    r.groups.analyze = Analyzer(build)
    await r.run(org, "manual")
    assert (await _facts(db))["promised_to"].valid_to is None
    errs = (await db.execute(text("SELECT errors FROM refinery.analyses ORDER BY started_at DESC LIMIT 1"))).scalar_one()
    assert {"code": "promise_kept_unmatched", "n": 1} in errs


# ─── (ii) cổng tin cậy: model đọc chữ người ngoài ⇒ promise_kept chỉ là đề xuất ──────────────────────────────────────────

async def _seed_debt(db, sm, redis, org):  # type: ignore[no-untyped-def]
    t0 = datetime.now(UTC) - timedelta(hours=10)
    debt = msg("Chị An ơi em nợ chị 5 triệu tiền hàng, cuối tuần em trả nha", sender="tuan", name="Tuấn Quán Cóc")
    debt["occurred_at"] = t0.isoformat()
    other = msg("Chị An cho em hỏi giá Culi tuần này", sender="an", name="An Hội An Cafe")
    other["occurred_at"] = (t0 + timedelta(minutes=1)).isoformat()
    await put(sm, org, debt, other)
    an_p, tuan = await person_of(db, "an"), await person_of(db, "tuan")

    def build(w):  # type: ignore[no-untyped-def]
        u = unit_for(w, "em nợ chị 5 triệu tiền hàng", kind="nghia_vu_loi_hua", summary="Tuấn nợ chị An 5 triệu")
        return out_with(w, u, facts=[FactProposed(unit_ref="u1", subject=FactSubject(surface="Tuấn", person_ref=f"u_{tuan}"), predicate="owes",
                                                  object=FactObject(value="5 triệu tiền hàng", person_ref=f"u_{an_p}"),
                                                  quote_refs=[find_msg(w, "em nợ chị 5 triệu").message_id])])
    r = refinery(sm, redis, Analyzer(build))
    await r.run(org, "manual")
    return r, t0


async def _claim_kept(sm, r, org, body: str, sender: str, name: str, at: datetime) -> None:  # type: ignore[no-untyped-def]
    p = msg(body, sender=sender, name=name)
    p["occurred_at"] = at.isoformat()
    await put(sm, org, p)

    def build(w):  # type: ignore[no-untyped-def]
        ids = [find_msg(w, "em nợ chị 5 triệu").message_id, find_msg(w, body).message_id]
        return BrainOut(thread_ref=w.thread_ref, memory_updates=[MemoryUpdate(type="promise_kept", text="đã trả", message_ids=ids)])
    r.groups.analyze = Analyzer(build)
    await r.run(org, "manual")


async def _last_errors(db):  # type: ignore[no-untyped-def]
    return (await db.execute(text("SELECT errors FROM refinery.analyses ORDER BY started_at DESC LIMIT 1"))).scalar_one()


async def test_promise_kept_attack_third_party_or_debtor_saying_paid_never_closes_a_debt(app, db, redis) -> None:  # type: ignore[no-untyped-def]
    """Người thứ ba (không phải chủ nợ) nhắn "Tuấn trả rồi đó", model báo promise_kept ⇒ khoản nợ VẪN hiệu lực; con nợ tự nhận "em trả
    rồi" cũng không đóng được; chỉ chủ nợ (hoặc Sếp) xác nhận mới đóng."""
    sm = sessionmaker()
    org, _ = await setup(db)
    r, t0 = await _seed_debt(db, sm, redis, org)
    await _claim_kept(sm, r, org, "Tuấn trả chị An rồi đó mọi người", "lan", "Lan Lạ Mặt", t0 + timedelta(hours=1))
    assert (await _facts(db))["owes"].valid_to is None
    assert {"code": "promise_kept_untrusted", "n": 1} in await _last_errors(db)
    await _claim_kept(sm, r, org, "Em chuyển trả chị 5 triệu rồi nha", "tuan", "Tuấn Quán Cóc", t0 + timedelta(hours=2))
    assert (await _facts(db))["owes"].valid_to is None                            # con nợ tự nói "đã trả" ⇒ không đóng khoản tiền
    paid_at = t0 + timedelta(hours=3)
    await _claim_kept(sm, r, org, "Chị nhận đủ 5 triệu rồi nha Tuấn", "an", "An Hội An Cafe", paid_at)
    f = (await _facts(db))["owes"]
    assert f.valid_to == paid_at and f.status == "asserted"


async def test_promise_kept_needs_a_new_message_of_this_turn_not_a_context_message(app, db, redis) -> None:  # type: ignore[no-untyped-def]
    """Tin "đã nhận" chỉ nằm ở tin ngữ cảnh (đã xử lý lượt trước) ⇒ không đóng; đóng đúng sự thật có tin nguồn được trích, không đóng
    sự thật khác chỉ vì tin đó là bằng chứng phụ."""
    sm = sessionmaker()
    org, _ = await setup(db)
    r, t0 = await _seed_promise(db, sm, redis, org)
    kept = msg("Nhận 2 túi decaf rồi nha Khoa", sender="an", name="An Hội An Cafe")
    kept["occurred_at"] = (t0 + timedelta(hours=1)).isoformat()
    await put(sm, org, kept)
    r.groups.analyze = Analyzer()                                                  # lượt này model không báo gì
    await r.run(org, "manual")
    await put(sm, org, msg("Tuần sau chị lấy thêm 10kg nha", sender="an", name="An Hội An Cafe"))

    def build(w):  # type: ignore[no-untyped-def]
        ids = [find_msg(w, "tuần sau e gửi").message_id, find_msg(w, "Nhận 2 túi decaf").message_id]      # cả hai là tin ngữ cảnh
        assert all(find_msg(w, n).context for n in ("tuần sau e gửi", "Nhận 2 túi decaf"))
        return BrainOut(thread_ref=w.thread_ref, memory_updates=[MemoryUpdate(type="promise_kept", text="đã nhận", message_ids=ids)])
    r.groups.analyze = Analyzer(build)
    await r.run(org, "manual")
    assert (await _facts(db))["promised_to"].valid_to is None
    # trích tin hỏi decaf của chị An (nguồn của `needs`, bằng chứng phụ của lời hứa) không đóng lời hứa và không đóng `needs`
    kept2 = msg("Decaf ngon lắm Khoa", sender="an", name="An Hội An Cafe")
    kept2["occurred_at"] = (t0 + timedelta(hours=2)).isoformat()
    await put(sm, org, kept2)

    def build2(w):  # type: ignore[no-untyped-def]
        ids = [find_msg(w, "có decaf không").message_id, find_msg(w, "Decaf ngon lắm").message_id]
        return BrainOut(thread_ref=w.thread_ref, memory_updates=[MemoryUpdate(type="promise_kept", text="đã nhận", message_ids=ids)])
    r.groups.analyze = Analyzer(build2)
    await r.run(org, "manual")
    fs = await _facts(db)
    assert fs["promised_to"].valid_to is None and fs["needs"].valid_to is None


async def _kept_turn(sm, r, org, t0, *extra: dict, cite: tuple[str, ...]) -> None:  # type: ignore[type-arg]
    await put(sm, org, *extra)

    def build(w):  # type: ignore[no-untyped-def]
        ids = [find_msg(w, "tuần sau e gửi").message_id, *(find_msg(w, n).message_id for n in cite)]
        return BrainOut(thread_ref=w.thread_ref, memory_updates=[MemoryUpdate(type="promise_kept", text="đã nhận", message_ids=ids)])
    r.groups.analyze = Analyzer(build)
    await r.run(org, "manual")


async def test_promise_kept_attack_forwarded_confirmation_from_an_allowed_sender_does_not_close(app, db, redis) -> None:  # type: ignore[no-untyped-def]
    """Chính bên được hứa CHUYỂN TIẾP tin/ảnh "đã nhận" của người khác ⇒ tin chuyển tiếp không phải bằng chứng đã giữ."""
    sm = sessionmaker()
    org, _ = await setup(db)
    r, t0 = await _seed_promise(db, sm, redis, org)
    fw = msg("Nhận 2 túi decaf rồi nha Khoa", sender="an", name="An Hội An Cafe")
    fw["occurred_at"], fw["forwarded"] = (t0 + timedelta(hours=1)).isoformat(), True
    await _kept_turn(sm, r, org, t0, fw, cite=("Nhận 2 túi decaf",))
    assert (await _facts(db))["promised_to"].valid_to is None
    assert {"code": "promise_kept_untrusted", "n": 1} in await _last_errors(db)


async def test_promise_kept_attack_mixed_with_a_third_party_message_does_not_close(app, db, redis) -> None:  # type: ignore[no-untyped-def]
    """Model trích tin xác nhận hợp lệ của bên được hứa KÈM tin người thứ ba ⇒ không đóng (không chọn lọc hộ model)."""
    sm = sessionmaker()
    org, _ = await setup(db)
    r, t0 = await _seed_promise(db, sm, redis, org)
    ok = msg("Nhận 2 túi decaf rồi nha Khoa", sender="an", name="An Hội An Cafe")
    bad = msg("Khoa gửi rồi mà, tui thấy", sender="lan", name="Lan Lạ Mặt")
    ok["occurred_at"], bad["occurred_at"] = (t0 + timedelta(hours=1)).isoformat(), (t0 + timedelta(hours=1, minutes=5)).isoformat()
    await _kept_turn(sm, r, org, t0, ok, bad, cite=("Nhận 2 túi decaf", "Khoa gửi rồi mà"))
    assert (await _facts(db))["promised_to"].valid_to is None
    assert {"code": "promise_kept_untrusted", "n": 1} in await _last_errors(db)


async def test_close_kept_requires_the_confirmation_in_the_promise_thread_or_a_reply_to_it(app, db, redis) -> None:  # type: ignore[no-untyped-def]
    """Phòng thủ tầng sâu: tin xác nhận ở luồng khác (dù đúng người, tin mới) không đóng; trả lời đúng tin hứa thì đóng."""
    from gh.biz.mem import facts as facts_mod

    sm = sessionmaker()
    org, _ = await setup(db)
    _r, t0 = await _seed_promise(db, sm, redis, org)
    f = (await _facts(db))["promised_to"]
    src, an_p = (await db.execute(text("SELECT source_raw_event_id FROM mem.facts WHERE id = :i"), {"i": f.id})).scalar_one(), await person_of(db, "an")
    tk = (await db.execute(text("SELECT detail ->> 'thread_key' FROM refinery.event_state WHERE event_id = :e"), {"e": src})).scalar_one()
    promise = facts_mod.KeptMsg(src, t0 + timedelta(minutes=18), None, new=False, thread_key=tk)
    other = uuid.uuid4()
    res = await facts_mod.close_kept(db, org, [promise, facts_mod.KeptMsg(other, t0 + timedelta(hours=1), an_p, new=True, thread_key="g:" + str(uuid.uuid4()))])
    assert res.closed == [] and res.untrusted == 1
    res = await facts_mod.close_kept(db, org, [promise, facts_mod.KeptMsg(other, t0 + timedelta(hours=1), an_p, new=True, thread_key="dm:x", reply_to_raw=src)])
    assert res.closed == [f.id]
    await db.rollback()
