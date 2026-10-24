# ruff: noqa: E501
"""Sửa sau lần chạy G-KB thật đầu tiên (kb1-r1): (1) trích tin ngữ cảnh + cách ly ghi từng đơn vị + fail-high cho đơn vị an toàn +
tin của lượt hỏng được thử lại; (3) truy hồi mang lịch sử của chủ thể (kèm tin bằng chứng để trích)."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import text

from gh.biz.mem import profile_read
from gh.db import sessionmaker
from gh.refinery import extract, runner
from gh.refinery.brain_contract import PersonMention
from gh.refinery.runner import Refinery
from gh.untrusted import UNTRUSTED_OPEN
from tests.phase2 import FakeRouter, listen, msg, org_id, put, states
from tests.refinery_group_helpers import Analyzer, enable_groups, out_with, unit_for


async def setup(db):  # type: ignore[no-untyped-def]
    org = await org_id(db)
    gid = await listen(db, org, "g1")
    await enable_groups(db, org)
    return org, gid


def refinery(sm, redis, analyzer: Analyzer) -> Refinery:  # type: ignore[no-untyped-def]
    return Refinery(sm, redis, FakeRouter(), None, analyzer=analyzer)


async def clear_backoff(db) -> None:  # type: ignore[no-untyped-def]
    await db.execute(text("UPDATE refinery.event_state SET detail = detail - 'next_try_at'"))
    await db.commit()


async def test_context_message_can_be_cited_with_its_received_at(app, db, redis) -> None:  # type: ignore[no-untyped-def]
    """Lỗi kb1 lượt 21: trích tin ngữ cảnh ⇒ `raw_received_at` NULL ⇒ cả lượt mất. Nay ghi đúng received_at của tin ngữ cảnh."""
    sm = sessionmaker()
    org, gid = await setup(db)
    [old] = await put(sm, org, msg("Thứ 7 mình muốn đặt bàn 12 người ở quán buổi chiều", sender="u1"))
    r = refinery(sm, redis, Analyzer())
    await r.run(org, "manual")                                            # lượt 1: không đơn vị ⇒ tin cũ đã xử lý (ngữ cảnh)
    await put(sm, org, msg("Chốt giúp em bàn hôm đó nhé anh", sender="u1"))

    def build(w):  # type: ignore[no-untyped-def]
        assert [c.raw_event_id for c in w.context_events] == [old]
        return out_with(w, unit_for(w, "đặt bàn 12 người", kind="nghia_vu_loi_hua", unit_ref="u1", summary="Đặt bàn 12 người"),
                        unit_for(w, "Chốt giúp em bàn", kind="cau_hoi_cho_xu_ly", unit_ref="u2", summary="Nhắc chốt bàn"))

    an = Analyzer(build)
    r.groups.analyze = an
    await r.run(org, "manual")
    rcv = (await db.execute(text("SELECT received_at FROM raw.events WHERE id = :e"), {"e": old})).scalar_one()
    ev = (await db.execute(text("SELECT raw_received_at FROM clean.evidence WHERE raw_event_id = :e"), {"e": old})).scalar_one()
    assert ev == rcv
    assert (await db.execute(text("SELECT count(*) FROM clean.meaning_units"))).scalar_one() == 2
    a = (await db.execute(text("SELECT status, errors FROM refinery.analyses ORDER BY started_at DESC LIMIT 1"))).one()
    assert a.status == "ok" and not any("write" in str(e.get("code")) for e in a.errors)
    assert await states(db, org) == {"discarded": 1, "clean": 1}          # tin ngữ cảnh giữ trạng thái của lượt trước


async def test_one_failing_unit_does_not_drop_the_others(app, db, redis, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    sm = sessionmaker()
    org, _ = await setup(db)
    await put(sm, org, msg("Em hứa giao 50 hộp thứ 7 nhé chị", sender="u1"), msg("Anh ơi máy pha cà phê hỏng rồi", sender="u2", name="Anh Tư"))
    real = runner.GroupTurns._notebook

    async def flaky(self, db_, org_id_, uid, u, first_ev):  # type: ignore[no-untyped-def]
        if u.unit_ref == "u2":
            raise RuntimeError("ghi sổ tay hỏng")
        return await real(self, db_, org_id_, uid, u, first_ev)

    monkeypatch.setattr(runner.GroupTurns, "_notebook", flaky)
    an = Analyzer(lambda w: out_with(w, unit_for(w, "giao 50 hộp", kind="nghia_vu_loi_hua", unit_ref="u1"),
                                     unit_for(w, "máy pha cà phê hỏng", kind="xung_dot_khieu_nai", unit_ref="u2")))
    await refinery(sm, redis, an).run(org, "manual")
    assert (await db.execute(text("SELECT kind_hint FROM clean.meaning_units"))).scalars().all() == ["nghia_vu_loi_hua"]
    assert (await db.execute(text("SELECT count(*) FROM clean.evidence"))).scalar_one() == 1          # bằng chứng của u2 rollback theo savepoint
    a = (await db.execute(text("SELECT status, errors FROM refinery.analyses"))).one()
    assert a.status == "ok"
    err = [e for e in a.errors if e.get("code") == "unit_write_failed"]
    assert err == [{"code": "unit_write_failed", "unit_ref": "u2", "kind": "xung_dot_khieu_nai", "gate": None, "error": "RuntimeError"}]
    assert "máy pha" not in str(a.errors)                                 # mã lỗi, không nội dung tin
    assert await states(db, org) == {"clean": 1, "discarded": 1}


async def test_failing_safety_unit_is_fail_high_and_retried(app, db, redis, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Đơn vị lừa đảo/an toàn ghi lỗi: dấu vết đọc được (mã lỗi + cảnh báo P1), tin KHÔNG bị bỏ mà giữ chờ; lượt sau ghi được."""
    sm = sessionmaker()
    org, _ = await setup(db)
    await put(sm, org, msg("Voucher 500k miễn phí, gửi mã OTP để nhận ngay", sender="u9", name="Shop Ưu Đãi"))
    real = runner.GroupTurns._notebook

    async def flaky(self, *a, **k):  # type: ignore[no-untyped-def]
        raise RuntimeError("ghi sổ tay hỏng")

    monkeypatch.setattr(runner.GroupTurns, "_notebook", flaky)
    an = Analyzer(lambda w: out_with(w, unit_for(w, "gửi mã OTP để nhận ngay", kind="rui_ro_lua_dao", unit_ref="u1", summary="Voucher giả đòi OTP")))
    r = refinery(sm, redis, an)
    await r.run(org, "manual")
    assert (await db.execute(text("SELECT count(*) FROM clean.meaning_units"))).scalar_one() == 0
    a = (await db.execute(text("SELECT status, errors FROM refinery.analyses"))).one()
    assert any(e.get("code") == "unit_write_failed_safety" and e.get("kind") == "rui_ro_lua_dao" for e in a.errors)
    s = (await db.execute(text("SELECT state, detail FROM refinery.event_state"))).one()
    assert s.state == "pending" and s.detail["last_error"] == "unit_write_failed_safety" and s.detail.get("next_try_at")
    al = (await db.execute(text("SELECT priority, alert_type, summary FROM biz.alerts WHERE alert_type = 'intel_write_failed_safety'"))).one()
    assert al.priority == "P1" and "OTP" not in al.summary
    # hết lỗi ⇒ lượt kế ghi được
    monkeypatch.setattr(runner.GroupTurns, "_notebook", real)
    await clear_backoff(db)
    await r.run(org, "manual")
    assert (await db.execute(text("SELECT kind_hint FROM clean.meaning_units"))).scalars().all() == ["rui_ro_lua_dao"]
    assert await states(db, org) == {"clean": 1}


async def test_events_of_a_failed_turn_are_retried_not_stuck(app, db, redis, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    sm = sessionmaker()
    org, _ = await setup(db)
    await put(sm, org, msg("Em hứa giao 50 hộp thứ 7 nhé chị", sender="u1"))

    async def boom(*a, **k):  # type: ignore[no-untyped-def]
        raise RuntimeError("hỏng giữa chừng")

    real = Refinery._set_state
    monkeypatch.setattr(Refinery, "_set_state", boom)
    an = Analyzer(lambda w: out_with(w, unit_for(w, "giao 50 hộp", kind="nghia_vu_loi_hua")))
    r = refinery(sm, redis, an)
    await r.run(org, "manual")
    s = (await db.execute(text("SELECT state, attempts, detail FROM refinery.event_state"))).one()
    assert s.state == "pending" and s.attempts == 1 and s.detail["next_try_at"]
    # lùi nhịp chưa hết ⇒ lượt kế không nhận tin; hết lùi nhịp ⇒ nhận lại và ghi được
    monkeypatch.setattr(Refinery, "_set_state", real)
    n = len(an.windows)
    await r.run(org, "manual")
    assert len(an.windows) == n
    await clear_backoff(db)
    await r.run(org, "manual")
    assert await states(db, org) == {"clean": 1}


async def test_history_block_carries_subject_units_with_citable_evidence(app, db, redis) -> None:  # type: ignore[no-untyped-def]
    """Lỗi kb1 (3): lượt sau không thấy việc trước (lời hứa giữ giá 320k ⇄ hoá đơn 345k). Truy hồi mang đơn vị trước của người trong
    cửa sổ — kể cả ngoài 24 giờ — và tin bằng chứng của nó vào tin ngữ cảnh (để trích), trong khối không tin cậy."""
    sm = sessionmaker()
    org, gid = await setup(db)
    old = msg("Em giữ giá 320k/thùng cho chị tới hết tháng nhé", sender="u1", name="Anh Tư")
    old["occurred_at"] = (datetime.now(UTC) - timedelta(days=5)).isoformat()
    [old_id] = await put(sm, org, old)
    r = refinery(sm, redis, Analyzer(lambda w: out_with(w, unit_for(w, "giữ giá 320k/thùng", kind="nghia_vu_loi_hua", summary="Tư hứa giữ giá 320k/thùng tới hết tháng"))))
    await r.run(org, "manual")
    await put(sm, org, msg("Hoá đơn tuần này 345k/thùng nha chị", sender="u1", name="Anh Tư"))
    an = Analyzer()
    r.groups.analyze = an
    await r.run(org, "manual")
    pack, w = an.packs[-1], an.windows[-1]
    assert [i.text.split(" ", 1)[1] for i in pack.history] == ["Tư hứa giữ giá 320k/thùng tới hết tháng"]
    assert pack.history[0].evidence_raw_ids == (old_id,)
    assert old_id in {c.raw_event_id for c in w.context_events}           # ngoài 24 giờ nhưng là bằng chứng của lịch sử ⇒ trích được
    prompt = extract.build_user_prompt(w, pack, await profile_read.snapshot(db, org), pack.policy)
    block = prompt[prompt.index("Lịch sử gần đây"):].split("\n\n")[0]
    assert block.count(UNTRUSTED_OPEN) == 1 and block.index(UNTRUSTED_OPEN) < block.index("320k")      # chữ người ngoài trong khối không tin cậy
    assert f"(tin: m_{old_id})" in block


# ─── lỗi 4: bộ lọc không làm rơi tin cần cho nghiệp vụ ───────────────────────────────────────────────────────────────

async def test_short_ack_stays_in_window_as_evidence_but_never_triggers_a_turn(app, db, redis) -> None:  # type: ignore[no-untyped-def]
    sm = sessionmaker()
    org, _ = await setup(db)
    order = msg("Thảo ơi tuần này a lấy 25kg Blend Honey nha", sender="u1", name="Anh Dũng")
    ack = msg("Dạ e ghi nhận ạ", sender="u2", name="Thảo")
    now = datetime.now(UTC)
    order["occurred_at"], ack["occurred_at"] = (now - timedelta(minutes=3)).isoformat(), (now - timedelta(minutes=2)).isoformat()
    an = Analyzer()
    r = refinery(sm, redis, an)
    await put(sm, org, ack)                                               # lời xác nhận một mình: không lượt model
    await r.run(org, "manual")
    assert an.windows == [] and await states(db, org) == {"discarded": 1}
    await put(sm, org, order)
    await r.run(org, "manual")
    w = an.windows[-1]
    assert [e.body for e in w.new_events] == ["Thảo ơi tuần này a lấy 25kg Blend Honey nha", "Dạ e ghi nhận ạ"]       # đúng thứ tự thời gian
    assert await states(db, org) == {"discarded": 2}                      # lời xác nhận vẫn không tính là tin của lượt (không tốn trần)


async def test_recurring_business_message_is_not_a_template_but_broadcast_is(app, db, redis) -> None:  # type: ignore[no-untyped-def]
    """Đơn định kỳ của một người (kể cả lặp 3 lần/7 ngày, kể cả nhập lịch sử cùng lúc ⇒ received_at dồn) không bị bỏ; tin rao lặp vẫn bỏ."""
    sm = sessionmaker()
    org, _ = await setup(db)
    now = datetime.now(UTC)
    weekly, daily, ads = [], [], []
    for k in range(3):
        m = msg("Thứ 3 này 9h giữ giúp phòng họp nhỏ HC cho 6 người nha Mộc Rang", sender="u1", name="Chị Vy")
        m["occurred_at"] = (now - timedelta(days=21 - 7 * k, minutes=5)).isoformat()                   # hằng tuần: 3 tin trải 14 ngày
        weekly.append(m)
        d = msg("Anh Dũng tuần này NHS tăng thêm 10 lít/ngày giúp em nha", sender="u2", name="Chị Hà")
        d["occurred_at"] = (now - timedelta(days=3 - k)).isoformat()                                   # 3 lần trong 7 ngày, nghiệp vụ
        daily.append(d)
        a = msg("Thanh lý máy xay Fiorenzato còn mới 85%, ai cần ib em nhé cả nhà", sender="u3", name="Shop Máy")
        a["occurred_at"] = (now - timedelta(days=3 - k, hours=1)).isoformat()
        ads.append(a)
    await put(sm, org, *weekly, *daily, *ads)                             # cùng lúc vào kho (như nhập lịch sử/replay)
    await refinery(sm, redis, Analyzer()).groups.ensure_scanned(org)
    rows = (await db.execute(text("""SELECT e.body_text, s.detail ->> 'reason' AS why FROM refinery.event_state s
                                     JOIN raw.events e ON e.id = s.event_id ORDER BY e.occurred_at"""))).all()
    why = {}
    for x in rows:
        why.setdefault(x.body_text.split()[0], []).append(x.why)
    assert why["Thứ"] == [None, None, None]
    assert why["Anh"] == [None, None, None]
    assert why["Thanh"] == [None, None, "template"]               # rác rao vặt lặp lần 3 trong 7 ngày (theo giờ gửi)


def test_is_broadcast_and_no_model_reason() -> None:
    from gh.refinery import prefilter

    assert prefilter.is_broadcast("Tuyển barista part-time ca tối Hải Châu, ib em nha")
    assert prefilter.is_broadcast("Kính gửi quý khách, bảng giá thép cuộn tháng 10 đã cập nhật")
    assert not prefilter.is_broadcast("Đợt ly + ống hút tháng này giao thứ 5 nha chị")
    body = "Đợt ly + ống hút tháng này giao thứ 5 nha chị, nhớ giúp em"
    assert prefilter.no_model_reason("text", body, template_n=5) is None
    assert prefilter.no_model_reason("text", "Tuyển barista part-time ca tối Hải Châu, ib em nha nhé mọi người", template_n=3) == "template"
    assert prefilter.no_model_reason("text", "Dạ e ghi nhận ạ") == "ack"


# ─── review: fail-high không được thua đơn vị thường cùng tin / đơn vị an toàn chỉ trích tin ngữ cảnh ─────────────────

def _fail_unit(monkeypatch, ref: str) -> None:  # type: ignore[no-untyped-def]
    real = runner.GroupTurns._notebook

    async def flaky(self, db_, org_id_, uid, u, first_ev):  # type: ignore[no-untyped-def]
        if u.unit_ref == ref:
            raise RuntimeError("ghi sổ tay hỏng")
        return await real(self, db_, org_id_, uid, u, first_ev)

    monkeypatch.setattr(runner.GroupTurns, "_notebook", flaky)


async def test_safety_unit_failing_beside_a_normal_unit_on_the_same_message_keeps_it_pending(app, db, redis, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    sm = sessionmaker()
    org, _ = await setup(db)
    await put(sm, org, msg("Chị đặt 5kg hạt nhé, à shop gửi link nhận voucher bảo nhập OTP", sender="u1"))
    _fail_unit(monkeypatch, "u2")
    an = Analyzer(lambda w: out_with(w, unit_for(w, "Chị đặt 5kg hạt", kind="nghia_vu_loi_hua", unit_ref="u1"),
                                     unit_for(w, "bảo nhập OTP", kind="rui_ro_lua_dao", unit_ref="u2")))
    await refinery(sm, redis, an).run(org, "manual")
    assert (await db.execute(text("SELECT kind_hint FROM clean.meaning_units"))).scalars().all() == ["nghia_vu_loi_hua"]
    s = (await db.execute(text("SELECT state, detail FROM refinery.event_state"))).one()
    assert s.state == "pending" and s.detail["last_error"] == "unit_write_failed_safety"       # không thành clean ⇒ đơn vị 🔴 được thử lại


async def test_safety_unit_citing_only_context_keeps_the_turns_new_messages_pending(app, db, redis, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    sm = sessionmaker()
    org, _ = await setup(db)
    [old] = await put(sm, org, msg("Shop gửi link voucher bảo nhập OTP để nhận quà", sender="u9", name="Shop"))
    r = refinery(sm, redis, Analyzer())
    await r.run(org, "manual")
    [new] = await put(sm, org, msg("Mọi người đừng bấm nha", sender="u1"))
    _fail_unit(monkeypatch, "u1")
    r.groups.analyze = Analyzer(lambda w: out_with(w, unit_for(w, "bảo nhập OTP", kind="rui_ro_lua_dao", unit_ref="u1")))
    await r.run(org, "manual")
    rows = dict((await db.execute(text("SELECT event_id, state FROM refinery.event_state"))).all())
    assert rows[new] == "pending" and rows[old] == "discarded"


async def test_notebook_and_mention_attribute_a_cited_context_message_to_its_own_sender(app, db, redis) -> None:  # type: ignore[no-untyped-def]
    sm = sessionmaker()
    org, _ = await setup(db)
    [old] = await put(sm, org, msg("Tuần sau em nghỉ phép 3 ngày nha anh", sender="u9", name="Chị Hằng"))
    r = refinery(sm, redis, Analyzer())
    await r.run(org, "manual")
    await put(sm, org, msg("Ok để anh sắp ca", sender="u1", name="Anh Tư"))
    r.groups.analyze = Analyzer(lambda w: out_with(w, unit_for(w, "nghỉ phép 3 ngày", kind="viec_han", summary="Hằng nghỉ phép 3 ngày",
                                                                people=[PersonMention(surface="Chị Hằng", person_ref=None, role="nguoi_gui")])))
    await r.run(org, "manual")
    hang = (await db.execute(text("""SELECT p.code, p.id FROM core.persons p JOIN core.person_identities i ON i.person_id = p.id
                                     WHERE i.external_id LIKE 'u9%'"""))).one()
    u = (await db.execute(text("SELECT person_id FROM clean.meaning_units"))).scalar_one()
    assert u == hang.id
    entries = (await db.execute(text("SELECT body FROM memory.entries"))).scalars().all()
    assert entries and all(b.startswith(f"theo lời {hang.code}: ") for b in entries)
    assert (await db.execute(text("SELECT raw_event_id FROM mem.mentions"))).scalars().all() == [old]
