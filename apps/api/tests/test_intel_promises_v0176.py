# ruff: noqa: E501
"""v0.1.76 (gói T4 / W1) — nghĩa vụ, việc & hạn, lời hứa có điều kiện; cổng `loi_hua_khach_sap_vo` + `su_co_cong_viec_gap`.

Bám ví dụ trong spec (§4.2, §4.2.1 A16/A17/A22, §4D.3-4), KHÔNG dùng đáp án eval. Chứng minh hai điểm yếu G-KB:
(a) HẠN TỚI GẦN phải leo thang mà không cần tin mới (gói thầu 216 triệu, hạn nộp 20/10: lần cuối chỉ thấy còn ~3 ngày ⇒ 🟠, tới ngày hạn
    `signals.reevaluate_due` kéo lên 🔴 qua `next_eval_at`);
(b) lời hứa ĐÃ GIỮ (fact đóng bởi `facts.close_kept`) phải đóng nghĩa vụ trong sổ + tín hiệu tương ứng.
Chạy trên PostgreSQL thật; phần thuần (không DB) dùng `promises.level_input` + `levels.compute`."""

from __future__ import annotations

import pathlib
import re
import uuid
from datetime import datetime, timedelta
from typing import Any

import orjson
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gh.biz.intel import levels, signals
from gh.biz.intel.detectors import promises
from gh.biz.mem import entities, facts
from tests import intel_helpers as H
from tests.mem_helpers import mk_org, mk_person_row, mkfact

VN = levels.VN
NOW = datetime(2026, 10, 23, 14, 0, tzinfo=VN)           # T6 23/10/2026 14:00 giờ VN


async def _install(db: AsyncSession, org: uuid.UUID) -> None:
    s = {"installed_at": "2026-09-01T00:00:00+07:00", "m1_passed_gates": list(levels.REGULAR_GATES)}
    await db.execute(text("UPDATE core.organizations SET settings = settings || jsonb_build_object('intel', CAST(:s AS jsonb)) WHERE id = :o"),
                     {"s": orjson.dumps(s).decode(), "o": org})


class World:
    def __init__(self) -> None:
        self.org: uuid.UUID
        self.owner: entities.Entity
        self.staff: uuid.UUID
        self.staff_person: uuid.UUID
        self.cust: uuid.UUID
        self.cust_person: uuid.UUID
        self.stranger: uuid.UUID


async def _world(db: AsyncSession) -> World:
    w = World()
    w.org = await mk_org(db)
    await _install(db, w.org)
    w.owner = await entities.owner(db, w.org)
    w.staff_person = await mk_person_row(db, w.org, "Nhân viên Lan")
    w.staff = (await entities.ensure_person(db, w.org, w.staff_person)).id
    w.cust_person = await mk_person_row(db, w.org, "Khách Tư")
    w.cust = (await entities.ensure_person(db, w.org, w.cust_person)).id
    w.stranger = (await entities.ensure_person(db, w.org, await mk_person_row(db, w.org, "Người lạ"))).id
    await facts.assert_fact(db, mkfact(w.org, w.staff, predicate="staff_of", object_entity=w.owner.id, at=NOW - timedelta(days=30)))
    await facts.assert_fact(db, mkfact(w.org, w.cust, predicate="customer_of", object_entity=w.owner.id, at=NOW - timedelta(days=30)))
    await db.commit()
    return w


async def _fact(db: AsyncSession, w: World, predicate: str, subject: uuid.UUID, body: str, *, at: datetime, due: datetime | None,
                to: uuid.UUID | None = None, cond: dict[str, Any] | None = None, cls: str = "insider") -> facts.Fact:
    val: dict[str, Any] = {"text": body, "due_at": due.isoformat() if due else None, "at": None,
                           "to_entity_id": str(to) if to else None}
    if cond is not None:
        val["condition"] = cond
    f = await facts.assert_fact(db, mkfact(w.org, subject, val, at, predicate=predicate, cls=cls))
    await db.commit()
    return f


async def _alert(db: AsyncSession, org: uuid.UUID, kind: str | None = None) -> Any:
    q = "SELECT id, kind, level, gate, candidate, delivery, status, reasons, next_eval_at, level_input FROM biz.alerts WHERE org_id = :o"
    if kind:
        q += " AND kind = :k"
    return (await db.execute(text(q + " ORDER BY created_at"), {"o": org, "k": kind})).all()


async def _promise_rows(db: AsyncSession, org: uuid.UUID) -> list[Any]:
    return list((await db.execute(text("""SELECT id, state, to_customer, due_at, kept_at, fact_id, signal_id, conditions, promiser_person_id,
                                          to_person_id FROM biz.promises WHERE org_id = :o ORDER BY created_at"""), {"o": org})).all())


# ─── (a) hạn tới gần leo thang không cần tin mới ───────────────────────────────

async def test_t4_deadline_escalates_by_clock_without_new_message(db: AsyncSession) -> None:
    """Gói thầu 216 triệu, hạn nộp 20/10 17:00: lần cuối được xem còn ~3 ngày ⇒ 🟠; không có tin mới mà tới ngày hạn ⇒ 🔴 (cổng 6)."""
    w = await _world(db)
    due = datetime(2026, 10, 20, 17, 0, tzinfo=VN)
    seen = datetime(2026, 10, 17, 14, 0, tzinfo=VN)
    await _fact(db, w, "deadline", w.staff, "nộp hồ sơ gói thầu 216 triệu", at=seen - timedelta(hours=1), due=due)
    out = await promises.project(db, w.org, seen)
    await db.commit()
    assert len(out) == 1
    [a] = await _alert(db, w.org)
    assert (a.kind, a.level, a.gate) == ("viec_han", "amber", None) and a.candidate == levels.G6
    assert a.next_eval_at is not None and a.next_eval_at > seen          # đồng hồ đã hẹn lần xem lại
    # còn 1 ngày: chưa phải "hôm nay" ⇒ vẫn 🟠, không đổi mức
    await signals.reevaluate_due(db, datetime(2026, 10, 19, 12, 0, tzinfo=VN))
    mid = (await _alert(db, w.org))[0]
    assert mid.level == "amber"
    # sang ngày hạn, KHÔNG có tin mới, KHÔNG gọi project: chỉ đồng hồ của T1
    changed = await signals.reevaluate_due(db, datetime(2026, 10, 20, 0, 0, 30, tzinfo=VN))
    await db.commit()
    [a] = await _alert(db, w.org)
    assert a.level == "red" and a.gate == levels.G6, (a.level, a.gate, a.reasons)
    assert any(c.level == "red" for c in changed)
    # quá giờ hạn trong hôm nay mà chưa nộp vẫn 🔴 (A22)
    await signals.reevaluate_due(db, datetime(2026, 10, 20, 18, 0, tzinfo=VN))
    assert (await _alert(db, w.org))[0].level == "red"


async def test_t4_deadline_today_needs_late_signs_then_clock_tick_sets_them(db: AsyncSession) -> None:
    """Việc có hạn (không phải nộp hồ sơ): cổng 6 cần dấu hiệu trễ. Hạn trôi qua theo đồng hồ ⇒ lần quét kế tiếp đặt `late_signs` ⇒ 🔴."""
    w = await _world(db)
    due = datetime(2026, 10, 23, 15, 0, tzinfo=VN)
    await _fact(db, w, "deadline", w.owner.id, "gửi báo cáo tồn kho cho kế toán", at=NOW - timedelta(days=1), due=due)
    await promises.project(db, w.org, NOW)
    await db.commit()
    assert (await _alert(db, w.org))[0].level == "amber"           # còn 1 giờ, chưa có dấu hiệu trễ
    later = datetime(2026, 10, 23, 16, 0, tzinfo=VN)
    await promises.project(db, w.org, later)
    await db.commit()
    a = (await _alert(db, w.org))[0]
    assert (a.level, a.gate) == ("red", levels.G6)
    assert len(await _alert(db, w.org)) == 1                        # vẫn MỘT tín hiệu


# ─── cổng 4: lời hứa với khách sắp vỡ (INTENT Q16, A16, A17) ──────────────────

async def test_t4_promise_to_customer_due_in_3h_is_red_gate4(db: AsyncSession) -> None:
    w = await _world(db)
    due = NOW + timedelta(hours=3)
    await _fact(db, w, "promised_to", w.staff, "giao 50 tấm MDF cho chị Tư chiều nay", at=NOW - timedelta(hours=20), due=due, to=w.cust)
    out = await promises.project(db, w.org, NOW)
    await db.commit()
    assert out[0].level == "red" and out[0].gate == levels.G4
    [p] = await _promise_rows(db, w.org)
    assert p.state == "open" and p.to_customer is True and p.fact_id is not None and p.signal_id == out[0].id
    assert p.promiser_person_id == w.staff_person and p.to_person_id == w.cust_person


async def test_t4_promise_to_customer_5h_left_without_obstacle_is_not_red_but_obstacle_makes_it_red(db: AsyncSession) -> None:
    w = await _world(db)
    await _fact(db, w, "promised_to", w.staff, "giao hàng cho chị Tư", at=NOW - timedelta(hours=20), due=NOW + timedelta(hours=5), to=w.cust)
    out = await promises.project(db, w.org, NOW)
    assert out[0].level == "amber" and out[0].gate is None and out[0].candidate == levels.G4
    s = _pure(due_in_h=5, obstacle=True)
    assert H.compute(s).gate == levels.G4                          # "máy hỏng / kẹt xe": có tin trở ngại ⇒ 🔴 dù còn 5 giờ


async def test_t4_promise_overdue_today_is_red_a17_but_overdue_yesterday_is_not_a_gate(db: AsyncSession) -> None:
    assert H.compute(_pure(due_in_h=-1)).gate == levels.G4         # A17: đã trễ trong hôm nay, chưa có tin hoàn thành
    lv = H.compute(_pure(due_in_h=-20))                            # hạn từ hôm qua = đã vỡ, không cổng
    assert lv.gate is None and lv.level != "red"
    assert H.compute(_pure(due_in_h=4.0)).gate == levels.G4        # đúng 4,0 giờ vẫn tính
    assert H.compute(_pure(due_in_h=4.2)).gate is None


async def test_t4_promise_completed_is_closed_a16() -> None:
    lv = H.compute(_pure(due_in_h=1, completed=True))
    assert lv.level == "grey" and lv.delivery == "none" and "closed" in lv.reasons


def _pure(*, due_in_h: float, obstacle: bool = False, completed: bool = False, cond: str | None = None, to_customer: bool = True) -> levels.SignalIn:
    ctx = promises.PromiseCtx(kind="nghia_vu_loi_hua", text="giao hàng cho chị Tư", valid_from=NOW - timedelta(hours=20),
                              due_at=NOW + timedelta(hours=due_in_h), to_customer=to_customer, obstacle=obstacle, completed=completed,
                              condition_met=cond, source_class="insider")
    return promises.level_input(ctx, NOW)


async def test_t4_promise_to_non_customer_is_at_most_amber() -> None:
    lv = H.compute(_pure(due_in_h=1, to_customer=False))
    assert lv.level != "red" or lv.gate == levels.G6
    assert lv.gate != levels.G4


# ─── lời hứa có điều kiện (§4D.3-4) ───────────────────────────────────────────

async def test_t4_conditional_promise_never_gate_and_never_broken_until_condition_met(db: AsyncSession) -> None:
    w = await _world(db)
    await _fact(db, w, "promised_to", w.staff, "giao hàng nếu chị Tư chuyển cọc", at=NOW - timedelta(hours=20),
                due=NOW + timedelta(hours=1), to=w.cust, cond={"text": "chuyển cọc", "met": "no"})
    out = await promises.project(db, w.org, NOW)
    await db.commit()
    assert out[0].gate is None and out[0].level != "red"
    [p] = await _promise_rows(db, w.org)
    assert p.state == "conditional" and p.due_at.year == 2026       # hạn danh nghĩa (không dùng mốc 9999: vỡ phân trang /tasks/promises)
    assert p.conditions["met"] == "no"
    # nghiệm lại bằng đồng hồ: qua hạn danh nghĩa vẫn không cổng, không broken
    await promises.project(db, w.org, NOW + timedelta(hours=2))
    await db.commit()
    assert (await _promise_rows(db, w.org))[0].state == "conditional"
    assert all(a.gate is None for a in await _alert(db, w.org))
    # điều kiện đạt ⇒ hạn tính lại, cổng 4 mở
    assert H.compute(_pure(due_in_h=3, cond="yes")).gate == levels.G4
    assert H.compute(_pure(due_in_h=3, cond="no")).gate is None
    assert H.compute(_pure(due_in_h=-1, cond="no")).gate is None


# ─── (b) lời hứa đã giữ ⇒ đóng nghĩa vụ + tín hiệu ────────────────────────────

async def _kept_flow(db: AsyncSession, w: World, f: facts.Fact, raw_src: uuid.UUID, at: datetime) -> facts.KeptResult:
    cited = [facts.KeptMsg(raw_src, at, w.staff_person, new=True, thread_key="g:t4"),
             facts.KeptMsg(uuid.uuid4(), at + timedelta(hours=1), w.cust_person, new=True, thread_key="g:t4", reply_to_raw=raw_src)]
    res = await facts.close_kept(db, w.org, cited)
    await db.commit()
    return res


async def test_t4_kept_promise_closes_obligation_and_signal(db: AsyncSession) -> None:
    w = await _world(db)
    t0 = NOW - timedelta(hours=20)
    f = await _fact(db, w, "promised_to", w.staff, "giao 50 tấm MDF cho chị Tư", at=t0, due=NOW + timedelta(hours=3), to=w.cust)
    raw_src = (await db.execute(text("SELECT source_raw_event_id FROM mem.facts WHERE id = :i"), {"i": f.id})).scalar_one()
    await promises.project(db, w.org, NOW)
    await db.commit()
    assert (await _alert(db, w.org))[0].level == "red"
    res = await _kept_flow(db, w, f, raw_src, t0 + timedelta(hours=1))
    assert res.closed == [f.id]                                    # cổng tin cậy của L1 đã đóng fact
    n = await promises.sync_kept(db, w.org, NOW)
    await db.commit()
    assert n >= 1
    [p] = await _promise_rows(db, w.org)
    assert p.state == "kept" and p.kept_at is not None
    [a] = await _alert(db, w.org)
    assert a.status == "resolved" and a.level == "grey" and a.delivery == "none" and "closed" in a.reasons
    # chạy lại an toàn; đồng hồ không kéo tín hiệu đã đóng sống lại
    assert await promises.sync_kept(db, w.org, NOW) == 0
    await promises.project(db, w.org, NOW + timedelta(hours=1))
    await signals.reevaluate_due(db, NOW + timedelta(hours=2))
    await db.commit()
    assert len(await _alert(db, w.org)) == 1 and (await _alert(db, w.org))[0].status == "resolved"
    assert len(await _promise_rows(db, w.org)) == 1


async def test_t4_promise_already_kept_before_first_scan_is_only_recorded_in_ledger(db: AsyncSession) -> None:
    """"Lời hứa đã giữ chỉ cập nhật sổ, không thành tín hiệu" (§4D.3-5)."""
    w = await _world(db)
    t0 = NOW - timedelta(hours=20)
    f = await _fact(db, w, "promised_to", w.staff, "gửi báo giá", at=t0, due=NOW + timedelta(hours=3), to=w.cust)
    raw_src = (await db.execute(text("SELECT source_raw_event_id FROM mem.facts WHERE id = :i"), {"i": f.id})).scalar_one()
    await _kept_flow(db, w, f, raw_src, t0 + timedelta(hours=1))
    await promises.project(db, w.org, NOW)
    await promises.sync_kept(db, w.org, NOW)
    await db.commit()
    assert await _alert(db, w.org) == []
    assert [p.state for p in await _promise_rows(db, w.org)] == ["kept"]


# ─── chiều lời hứa, chạy lại ──────────────────────────────────────────────────

async def test_t4_other_to_owner_promise_is_tracked_but_not_a_signal(db: AsyncSession) -> None:
    """Chiều người khác → Sếp: chỉ theo dõi (CNS-087b), không tính."""
    w = await _world(db)
    await _fact(db, w, "promised_to", w.cust, "mai em chuyển tiền", at=NOW - timedelta(hours=5), due=NOW + timedelta(hours=1), to=w.owner.id)
    assert await promises.project(db, w.org, NOW) == []
    assert await _alert(db, w.org) == []
    [p] = await _promise_rows(db, w.org)
    assert p.state == "open" and p.signal_id is None and p.to_customer is False


async def test_t4_between_strangers_is_ignored_and_project_is_idempotent(db: AsyncSession) -> None:
    w = await _world(db)
    await _fact(db, w, "promised_to", w.stranger, "việc riêng của họ", at=NOW - timedelta(hours=5), due=NOW + timedelta(hours=1), to=w.cust)
    await _fact(db, w, "promised_to", w.owner.id, "hứa với chị Tư", at=NOW - timedelta(hours=5), due=NOW + timedelta(hours=2), to=w.cust)
    for _ in range(3):
        await promises.project(db, w.org, NOW)
        await db.commit()
    assert await _promise_rows(db, w.org) == []        # thực thể Sếp của test chưa nối hồ sơ người ⇒ không có dòng sổ; người lạ ⇒ bỏ
    assert len(await _alert(db, w.org)) == 1


async def test_t4_org_isolation(db: AsyncSession) -> None:
    w = await _world(db)
    other = await _world(db)
    await _fact(db, w, "promised_to", w.staff, "giao hàng", at=NOW - timedelta(hours=5), due=NOW + timedelta(hours=1), to=w.cust)
    await promises.project(db, other.org, NOW)
    assert await _alert(db, other.org) == [] and await _promise_rows(db, other.org) == []


# ─── hợp đồng: bên ghi duy nhất, cờ ──────────────────────────────────────────

def test_t4_promises_detector_is_the_only_writer_of_biz_promises() -> None:
    root = pathlib.Path(__file__).resolve().parents[1] / "gh"
    writers = sorted(str(p.relative_to(root)) for p in root.rglob("*.py")
                     if re.search(r"INSERT\s+INTO\s+biz\.promises", p.read_text(), re.I))
    assert writers == ["biz/intel/detectors/promises.py"], writers


def test_t4_promises_tracked_flag_is_on() -> None:
    from gh.biz.queue import service

    assert service.PROMISES_TRACKED is True


def test_t4_detector_registers_job_and_never_sets_levels() -> None:
    from gh.biz.intel import jobs

    assert any(fn.__name__ == "intel_promises" for fn, _ in jobs.JOBS)
    src = pathlib.Path(promises.__file__).read_text()
    assert not re.search(r"UPDATE\s+biz\.alerts", src, re.I), "detector không được tự ghi biz.alerts (chỉ signals.py)"


# ─── review T4 (vòng 2) ───────────────────────────────────────────────────────

async def test_t4_two_promises_in_same_message_stay_two_signals(db: AsyncSession) -> None:
    w = await _world(db)
    raw = uuid.uuid4()
    for body, h in (("giao 20 bao xi măng", 3), ("gửi hóa đơn VAT", 30)):
        val = {"text": body, "due_at": (NOW + timedelta(hours=h)).isoformat(), "at": None, "to_entity_id": str(w.cust)}
        await facts.assert_fact(db, mkfact(w.org, w.staff, val, NOW - timedelta(hours=20), predicate="promised_to", raw=raw))
    await db.commit()
    for i in range(3):
        await promises.project(db, w.org, NOW + timedelta(minutes=5 * i))
        await db.commit()
    al = await _alert(db, w.org)
    assert len(al) == 2
    assert sorted(a.level for a in al) == ["amber", "red"]
    assert {r.signal_id for r in await _promise_rows(db, w.org)} == {a.id for a in al}


async def test_t4_old_facts_do_not_starve_new_promise(db: AsyncSession) -> None:
    w = await _world(db)
    for i in range(8):
        await _fact(db, w, "promised_to", w.staff, f"việc cũ {i}", at=NOW - timedelta(days=40 + i), due=NOW - timedelta(days=30), to=w.cust)
    await _fact(db, w, "promised_to", w.staff, "giao hàng chiều nay", at=NOW - timedelta(hours=2), due=NOW + timedelta(hours=3), to=w.cust)
    out = await promises.project(db, w.org, NOW, limit=5)
    assert out[0].gate == levels.G4                                  # lời hứa gần hạn nhất xử lý ĐẦU TIÊN dù có fact cũ (lần đầu gặp vẫn được xử lý)


async def test_t4_group_deadline_is_not_owner_work(db: AsyncSession) -> None:
    w = await _world(db)
    grp = await entities.create(db, w.org, "group", "Lớp 5A phụ huynh")
    await _fact(db, w, "deadline", grp.id, "hạn nộp học phí lớp 5A", at=NOW - timedelta(hours=3), due=NOW + timedelta(hours=2))
    out = await promises.project(db, w.org, NOW)
    assert all(s.level != "red" for s in out)


def test_t4_work_type_needs_real_submission_words() -> None:
    assert promises.work_type("đăng ký tên miền") == "deadline_today"
    assert promises.work_type("nộp tiền điện") == "deadline_today"
    assert promises.work_type("nộp hồ sơ dự thầu") == "submission_today"


async def test_t4_owner_without_person_kept_closes_signal_and_deadline_close_and_ui_kept(db: AsyncSession) -> None:
    w = await _world(db)
    f = await _fact(db, w, "promised_to", w.owner.id, "giao hàng chị Tư", at=NOW - timedelta(hours=20), due=NOW + timedelta(hours=3), to=w.cust)
    d = await _fact(db, w, "deadline", w.staff, "nộp hồ sơ thầu", at=NOW - timedelta(hours=20), due=NOW + timedelta(hours=5))
    # Sếp bấm "đã giữ" trên UI: chỉ đặt kept_at của sổ
    p_from = (await db.execute(text("SELECT person_id FROM mem.entities WHERE id = :i"), {"i": w.staff})).scalar_one()
    f2 = await _fact(db, w, "promised_to", w.staff, "gửi báo giá", at=NOW - timedelta(hours=20), due=NOW + timedelta(hours=3), to=w.cust)
    await promises.project(db, w.org, NOW)
    await db.commit()
    assert {a.level for a in await _alert(db, w.org)} == {"red"}
    await db.execute(text("UPDATE mem.facts SET valid_to = :t WHERE id = ANY(:i)"), {"t": NOW - timedelta(hours=1), "i": [f.id, d.id]})
    await db.execute(text("UPDATE biz.promises SET kept_at = :t WHERE fact_id = :f"), {"t": NOW, "f": f2.id})
    await db.commit()
    assert p_from is not None
    await promises.sync_kept(db, w.org, NOW + timedelta(minutes=5))
    await db.commit()
    assert [a.status for a in await _alert(db, w.org)] == ["resolved"] * 3
    assert await promises.sync_kept(db, w.org, NOW + timedelta(minutes=6)) == 0


async def _unit_flag(db: AsyncSession, w: World, group: uuid.UUID, person: uuid.UUID | None, at: datetime) -> None:
    await db.execute(text("""INSERT INTO clean.meaning_units (org_id, observed_at, group_id, person_id, event_type, conclusion, entities, confidence,
                             run_id, kind_hint, flags) VALUES (:o, :t, :g, :p, 'GroupUnit', 'kẹt xe', '{}', 0.8, :r, 'nghia_vu_loi_hua',
                             '{"blocks_work": true}')"""), {"o": w.org, "t": at, "g": group, "p": person, "r": uuid.uuid4()})
    await db.commit()


async def test_t4_obstacle_only_from_the_promiser(db: AsyncSession) -> None:
    w = await _world(db)
    ch = (await db.execute(text("INSERT INTO core.channels (org_id, type, name) VALUES (:o, 'zalo', 'z') RETURNING id"), {"o": w.org})).scalar_one()
    grp = (await db.execute(text("""INSERT INTO core.groups (org_id, code, channel_id, external_id, name, member_count)
                                    VALUES (:o, 'GRP-T4', :c, 'x', 'nhóm', 20) RETURNING id"""), {"o": w.org, "c": ch})).scalar_one()
    raw = (await db.execute(text("""INSERT INTO raw.events (org_id, occurred_at, group_id, channel_id, external_msg_id, kind, body_text, payload, content_hash)
                                    VALUES (:o, now(), :g, :c, 'm1', 'text', 'x', '{}', '\\x01') RETURNING id"""), {"o": w.org, "g": grp, "c": ch})).scalar_one()
    await facts.assert_fact(db, mkfact(w.org, w.staff, {"text": "giao hàng", "due_at": (NOW + timedelta(hours=5)).isoformat(), "at": None,
                                                         "to_entity_id": str(w.cust)}, NOW - timedelta(hours=20), predicate="promised_to", raw=raw))
    other = await mk_person_row(db, w.org, "Người khác")
    await _unit_flag(db, w, grp, other, NOW - timedelta(hours=1))
    out = await promises.project(db, w.org, NOW)
    assert out[0].level == "amber"                                  # trở ngại của người khác trong nhóm không tính
    await _unit_flag(db, w, grp, w.staff_person, NOW - timedelta(minutes=30))
    out = await promises.project(db, w.org, NOW + timedelta(minutes=1))
    assert out and out[0].gate == levels.G4
