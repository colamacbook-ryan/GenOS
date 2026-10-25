# ruff: noqa: E501
"""v0.1.76 (gói T4 / W5b) — detector chung: sức khoẻ/an toàn (cổng `an_toan`), sự cố vận hành (cổng 6), dịp/sự kiện, thị trường.

Ca biên cổng an toàn bám ví dụ trong spec (§4.2 cổng 2, §4.2.1 (d) cổng 2 + A23, r6.5/r6.6 CNS-139/147): KHÔNG dùng đáp án eval.
Phần thuần: `generic.unit_input`/`event_input` + `levels.compute`; phần DB: `generic.project_units/project_events` trên PostgreSQL thật."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Any

import orjson
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gh.biz.intel import levels, signals
from gh.biz.intel.detectors import generic as G
from gh.biz.mem import entities, facts
from tests import intel_helpers as H
from tests.mem_helpers import mk_org, mk_person_row, mkfact

VN = levels.VN
NOW = H.NOW


def _unit(*, ago_h: float = 0.5, size: int = 20, victims: list[levels.PersonRel] | None = None, group: bool = True,
          **kw: Any) -> G.UnitCtx:
    flags = kw.pop("flags", {"emergency": True, "ongoing": True})
    th = levels.Thread(type="group" if group else "dm_agent", size=size, owner_in_thread=False, agent_in_thread=True)
    d: dict[str, Any] = dict(kind="suc_khoe_an_toan", unit_id=uuid.uuid4(), observed_at=NOW - timedelta(hours=ago_h), flags=flags,
                             harm_what="nguy hiểm tính mạng người thân", confidence_exist=0.8, source_class="insider", thread=th,
                             victims=victims if victims is not None else [H.person("v", owner_key_relation=True)])
    d.update(kw)
    return G.UnitCtx(**d)


def _lv(c: G.UnitCtx, ctx: str | None = "gia_dinh") -> levels.Level:
    return H.compute(G.unit_input(c, NOW), ctx=ctx)


# ─── cổng an_toan: ca biên ────────────────────────────────────────────────────

def test_t4_safety_core_victim_emergency_is_red_push_now() -> None:
    lv = _lv(_unit())
    assert (lv.level, lv.gate, lv.delivery) == ("red", levels.G2, "push_now")        # bypass_all: không trần, không giờ yên lặng


def test_t4_safety_first_aid_done_is_not_emergency_so_no_gate() -> None:
    lv = _lv(_unit(flags={"emergency": False, "ongoing": False}, harm_what="trầy xước đã sơ cứu"))
    assert lv.gate is None and lv.level != "red"


def test_t4_safety_age_boundary_24h() -> None:
    """"Vừa xảy ra" = tuổi event_at < 24 giờ; `ongoing` không giới hạn; quá ⇒ 🟠 đứng đầu bản tin (7a′)."""
    assert _lv(_unit(ago_h=23.5, flags={"emergency": True})).gate == levels.G2
    old = _lv(_unit(ago_h=30, flags={"emergency": True}))
    assert old.level == "amber" and old.delivery == "digest_top" and "window_expired" in old.reasons
    assert _lv(_unit(ago_h=30, flags={"emergency": True, "ongoing": True})).gate == levels.G2


def test_t4_safety_forwarded_only_source_is_not_red() -> None:
    lv = _lv(_unit(forwarded=True))
    assert lv.level != "red" and lv.gate is None


def test_t4_safety_wide_circle_member_in_big_group() -> None:
    """Thành viên (ngoài lõi) ở nhóm 60 người ⇒ 🟠 đứng đầu bản tin (7a″); nhóm > 200 ⇒ không cổng; nhóm gần ≤ 30 ⇒ 🔴."""
    member = [H.person("m", thread_member=True)]
    wide = _lv(_unit(size=60, victims=member), "khac")
    assert wide.level == "amber" and wide.delivery == "digest_top" and "safety_wide_circle" in wide.reasons
    huge = _lv(_unit(size=300, victims=member), "khac")
    assert huge.gate is None and huge.level != "red"
    near = _lv(_unit(size=25, victims=member), "khac")
    assert near.gate == levels.G2


def test_t4_safety_stranger_claiming_relative_does_not_open_gate() -> None:
    """"Mẹ em cấp cứu, ai cho mượn tiền" từ người lạ mới vào nhóm (L-87): vai thân tộc không áp cho `outsider`."""
    kin = [H.person("k", kin_of_member=True)]
    lv = _lv(_unit(size=60, victims=kin, source_class="outsider"), "khac")
    assert lv.gate is None and lv.level != "red"


def test_t4_safety_customer_on_site_core_only_in_small_business_group() -> None:
    cust = [H.person("c", customer=True)]
    assert _lv(_unit(size=40, victims=cust), "kinh_doanh").gate == levels.G2
    assert _lv(_unit(size=120, victims=cust), "kinh_doanh").gate is None


def test_t4_safety_without_harm_never_red_but_hard_rule_fills_template() -> None:
    lv = _lv(_unit(harm_what=""))
    assert lv.level == "amber"                                                       # có cổng nhưng thiếu `harm.what`
    hard = _lv(_unit(harm_what="", hard_rules=["emergency"]))
    assert hard.level == "red" and "harm_template" in hard.reasons


# ─── sự cố vận hành ⇒ cổng 6 ──────────────────────────────────────────────────

def _incident(blocks: bool) -> G.UnitCtx:
    return _unit(victims=[], gate_candidate=levels.G6, flags={"blocks_work": blocks, "ongoing": True}, owner_work=True,
                 harm_what="máy POS hỏng giờ bán", source_class="insider")


def test_t4_system_incident_blocking_work_is_red_gate6_else_amber() -> None:
    assert G.is_system_incident(_incident(True))
    red = _lv(_incident(True), "cong_viec")
    assert (red.level, red.gate) == ("red", levels.G6)
    soft = _lv(_incident(False), "cong_viec")                                       # A21: sự cố không chặn việc ⇒ 🟠
    assert soft.level == "amber" and soft.gate is None


def test_t4_emergency_unit_is_never_treated_as_system_incident() -> None:
    assert not G.is_system_incident(_unit(gate_candidate=levels.G6))


# ─── dịp / sự kiện ────────────────────────────────────────────────────────────

def _event(days: float, related: bool) -> levels.Level:
    c = G.EventCtx(text="giỗ ba chị Hai", valid_from=NOW - timedelta(days=10), at=NOW + timedelta(days=days), related=related)
    return H.compute(G.event_input(c, NOW), ctx="gia_dinh")


def test_t4_event_amber_within_3_days_when_owner_related() -> None:
    assert _event(2.9, True).level == "amber"
    assert _event(5, True).level == "grey"
    assert _event(2, False).level == "grey"


# ─── thị trường ───────────────────────────────────────────────────────────────

def test_t4_market_is_grey_and_off_in_study_context() -> None:
    c = G.UnitCtx(kind="thi_truong_thong_tin", unit_id=uuid.uuid4(), observed_at=NOW - timedelta(hours=1), source_class="insider",
                  thread=levels.Thread(type="group", size=20, agent_in_thread=True))
    assert H.compute(G.unit_input(c, NOW), ctx="kinh_doanh").level == "grey"
    c2 = G.UnitCtx(**{**c.__dict__, "flags": {"needs_owner": True}})
    assert H.compute(G.unit_input(c2, NOW), ctx="kinh_doanh").level == "amber"
    assert H.compute(G.unit_input(c2, NOW), ctx="hoc_tap").level in ("grey", "off")


# ─── DB ───────────────────────────────────────────────────────────────────────

async def _install(db: AsyncSession, org: uuid.UUID) -> None:
    s = {"installed_at": "2026-09-01T00:00:00+07:00", "m1_passed_gates": list(levels.REGULAR_GATES)}
    await db.execute(text("UPDATE core.organizations SET settings = settings || jsonb_build_object('intel', CAST(:s AS jsonb)) WHERE id = :o"),
                     {"s": orjson.dumps(s).decode(), "o": org})


async def _raw(db: AsyncSession, org: uuid.UUID) -> uuid.UUID:
    ch = (await db.execute(text("INSERT INTO core.channels (org_id, type, name) VALUES (:o, 'zalo', 'z') RETURNING id"), {"o": org})).scalar_one()
    return (await db.execute(text("""INSERT INTO raw.events (org_id, occurred_at, channel_id, external_msg_id, kind, body_text, payload, content_hash)
                                     VALUES (:o, now(), :c, :x, 'text', 'mẹ em đang cấp cứu', '{}', '\\x01') RETURNING id"""),
                             {"o": org, "c": ch, "x": uuid.uuid4().hex})).scalar_one()  # type: ignore[no-any-return]


async def _unit_row(db: AsyncSession, org: uuid.UUID, raw: uuid.UUID, kind: str, flags: dict[str, Any], *, person: uuid.UUID | None = None,
                    mention_entity: uuid.UUID | None = None, gate: str | None = None, at: datetime = NOW) -> uuid.UUID:
    uid = (await db.execute(text("""
        INSERT INTO clean.meaning_units (org_id, observed_at, person_id, event_type, conclusion, entities, confidence, run_id, kind_hint, flags,
                                         gate_candidate, harm)
        VALUES (:o, :t, :p, 'GroupUnit', 'nhân viên bị tai nạn, đang đi cấp cứu', '{}', 0.8, :r, :k, CAST(:f AS jsonb), :g,
                CAST(:h AS jsonb)) RETURNING id"""),
                             {"o": org, "t": at, "p": person, "r": uuid.uuid4(), "k": kind, "f": orjson.dumps(flags).decode(), "g": gate,
                              "h": orjson.dumps({"what": "nguy hiểm tính mạng"}).decode()})).scalar_one()
    await db.execute(text("""INSERT INTO clean.evidence (meaning_unit_id, meaning_observed_at, raw_event_id, raw_received_at, quote, quote_status,
                                                          source_field, span_start, span_end)
                             VALUES (:u, :t, :r, now(), 'cấp cứu', 'verified', 'body', 0, 7)"""), {"u": uid, "t": at, "r": raw})
    if mention_entity:
        await db.execute(text("""INSERT INTO mem.mentions (org_id, meaning_unit_id, meaning_observed_at, raw_event_id, surface, entity_id, method, status)
                                 VALUES (:o, :u, :t, :r, 'Lan', :e, 'llm', 'linked')"""), {"o": org, "u": uid, "t": at, "r": raw, "e": mention_entity})
    await db.commit()
    return uid  # type: ignore[no-any-return]


async def test_t4_project_units_safety_unit_becomes_one_red_signal_and_is_idempotent(db: AsyncSession) -> None:
    org = await mk_org(db)
    await _install(db, org)
    owner = await entities.owner(db, org)
    pid = await mk_person_row(db, org, "Lan")
    staff = (await entities.ensure_person(db, org, pid)).id
    await facts.assert_fact(db, mkfact(org, staff, predicate="staff_of", object_entity=owner.id, at=NOW - timedelta(days=9)))
    raw = await _raw(db, org)
    uid = await _unit_row(db, org, raw, "suc_khoe_an_toan", {"emergency": True, "ongoing": True}, person=pid, mention_entity=staff)
    out = await G.project_units(db, org, NOW)
    await db.commit()
    assert len(out) == 1 and (out[0].level, out[0].gate, out[0].delivery) == ("red", levels.G2, "push_now")
    again = await G.project_units(db, org, NOW)
    assert again == []
    n = (await db.execute(text("SELECT count(*) FROM biz.alerts WHERE org_id = :o AND facts ->> 'unit_id' = :u"), {"o": org, "u": str(uid)})).scalar_one()
    assert n == 1


async def test_t4_project_units_merges_with_fast_path_signal_of_same_raw_event(db: AsyncSession) -> None:
    """Một sự việc, một tín hiệu: tín hiệu `raw_tail` (luật cứng) có trước ⇒ đơn vị model gộp vào cùng hàng."""
    org = await mk_org(db)
    await _install(db, org)
    raw = await _raw(db, org)
    pid = await mk_person_row(db, org, "Lan")
    ev = signals.EvidenceIn(type="raw_event", id=raw, source_field="body", span_start=0, span_end=7, quote="cấp cứu")
    li = levels.SignalIn(kind="suc_khoe_an_toan", msg_at=NOW, event_at=NOW, hard_rule_hit=["emergency"], flags=levels.Flags(emergency=True),
                         source_class="rule", confidence_exist=1.0, victims=[H.person("v", team=True)],
                         thread=levels.Thread(type="dm_agent", agent_in_thread=True), impact_base=3)
    first = await signals.upsert(db, org, signals.UpsertIn(kind="suc_khoe_an_toan", title="t", detector="raw_tail", level_input=li, evidence=[ev],
                                                           case_key=f"suc_khoe_an_toan:raw|{raw}"), NOW)
    await _unit_row(db, org, raw, "suc_khoe_an_toan", {"emergency": True}, person=pid)
    out = await G.project_units(db, org, NOW)
    assert [s.id for s in out] == [first.id]
    assert (await db.execute(text("SELECT count(*) FROM biz.alerts WHERE org_id = :o"), {"o": org})).scalar_one() == 1


async def test_t4_project_units_ignores_units_without_verified_evidence(db: AsyncSession) -> None:
    org = await mk_org(db)
    raw = await _raw(db, org)
    uid = await _unit_row(db, org, raw, "suc_khoe_an_toan", {"emergency": True})
    await db.execute(text("UPDATE clean.evidence SET quote_status = 'absent' WHERE meaning_unit_id = :u"), {"u": uid})
    await db.commit()
    assert await G.project_units(db, org, NOW) == []


async def test_t4_project_events_only_when_owner_related(db: AsyncSession) -> None:
    org = await mk_org(db)
    await _install(db, org)
    owner = await entities.owner(db, org)
    other = (await entities.ensure_person(db, org, await mk_person_row(db, org, "Người ngoài"))).id
    at = (NOW + timedelta(days=2)).isoformat()
    for subj, label in ((owner.id, "Sếp họp đối tác"), (other, "đám cưới người ngoài")):
        await facts.assert_fact(db, mkfact(org, subj, {"text": label, "at": at, "due_at": None}, NOW - timedelta(days=3),
                                           predicate="scheduled_event"))
    await db.commit()
    out = await G.project_events(db, org, NOW)
    await db.commit()
    levels_seen = sorted(s.level for s in out)
    assert levels_seen == ["amber", "grey"]
    assert await G.project_events(db, org, NOW) == []                                # chạy lại: không đổi


def test_t4_detector_modules_register_jobs() -> None:
    from gh.biz.intel import jobs

    names = {fn.__name__ for fn, _ in jobs.JOBS}
    assert {"intel_generic", "intel_promises"} <= names
