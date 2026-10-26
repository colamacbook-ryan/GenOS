# ruff: noqa: E501
"""v0.1.76 (gói T13) — lỗi sản phẩm G-KB Đợt 2 lộ ra: 🔴 trùng khi nâng khoá yếu→mạnh (P1), từ vựng luật (a) rò điện/tê tay (P2),
danh tính Sếp ở chế độ tài khoản bot (P3), câu hỏi "chưa trả lời" khi phía Sếp trả lời bằng tin inbound (P4), nạn nhân mặc định của lừa
đảo (P5), nhóm không rõ cỡ (P7), cờ công khai của khiếu nại theo bối cảnh nhóm (P8). Câu thử tự soạn, không lấy từ bộ dữ liệu chấm."""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gh.biz.intel import levels, signals
from gh.biz.intel.detectors import generic, promises, questions
from gh.biz.intel.detectors import units as U
from gh.refinery import prefilter as pf
from tests import intel_helpers as H
from tests import test_intel_units_t12_v0176 as T12
from tests.mem_helpers import mk_org
from tests.test_intel_core_signals_v0176 import NOW, _ev, _install, _li, _raw
from tests.test_owner_profile_support import mk_raw

FR = "rui_ro_lua_dao"


async def _rows(db: AsyncSession, org: uuid.UUID) -> list[Any]:
    return list((await db.execute(text("""SELECT id, level, delivery, pushed_at, case_key, status, case_state FROM biz.alerts
                                          WHERE org_id = :o ORDER BY created_at"""), {"o": org})).all())


def _upm(kind: str, li: levels.SignalIn, raws: list[uuid.UUID], case_key: str, detector: str = "test") -> signals.UpsertIn:
    return signals.UpsertIn(kind=kind, title="Tín hiệu thử", detector=detector, level_input=li, evidence=[_ev(r) for r in raws],
                            case_key=case_key, thread_key="g:t13")


# ─── P1: một vụ một 🔴 khi nâng khoá yếu → mạnh ───────────────────────────────

async def test_upsert_weak_to_strong_absorbs_pushed_weak_row(db: AsyncSession) -> None:
    """Dòng B (raw|r1, cũ hơn, chưa đẩy) + dòng A (raw|actor:x, ĐÃ ĐẨY 🔴, raw r2); tin mạnh imp|e mang cả r1, r2 ⇒ một vụ: giữ dòng đã
    đẩy, dòng kia gộp vào, không đẩy lần hai."""
    org = await mk_org(db)
    await _install(db, org)
    r1, r2 = await _raw(db, org, "b"), await _raw(db, org, "a")
    b = await signals.upsert(db, org, _upm(FR, _li(FR, ago_h=0.5), [r1], levels.case_key(FR, at=NOW, raw_event_id=str(r1)), "units"), NOW)
    assert b.pushed_at is None
    t1 = NOW + timedelta(minutes=5)
    a = await signals.upsert(db, org, _upm(FR, _li(FR, ago_h=0.05, now=t1, hard_rule_hit=["credentials_entered"]), [r2],
                                           levels.case_key(FR, at=t1, raw_event_id="actor:x"), "fraud"), t1)
    assert (a.level, a.delivery) == ("red", "push_now") and a.pushed_at is not None
    t2 = NOW + timedelta(minutes=40)
    imp = levels.case_key(FR, at=t2, impersonated_entity_id="e")
    s = await signals.upsert(db, org, _upm(FR, _li(FR, ago_h=0.1, now=t2, flags={"credentials_entered": True}), [r1, r2], imp, "units"), t2)
    rows = await _rows(db, org)
    opened = [r for r in rows if r.status in ("open", "acknowledged")]
    assert [r.id for r in opened] == [s.id] == [a.id]       # dòng đã đẩy được giữ, mang khoá mạnh
    assert opened[0].case_key == imp
    assert next(r for r in rows if r.id == b.id).status == "dismissed"
    assert s.delivery != "push_now"
    assert sum(1 for r in rows if r.pushed_at is not None) == 1
    assert opened[0].pushed_at == a.pushed_at


async def test_fraud_actor_key_pushed_then_units_impersonation_same_raw_one_red(db: AsyncSession) -> None:
    """Kịch bản Đợt 2: luật fraud (raw|actor:) đẩy trước; đơn vị cũ (raw|tin gốc) của cùng người; đơn vị mới imp| chứa tin đã đẩy và
    một tin của đơn vị cũ ⇒ một vụ, một 🔴 — kể cả khi đơn vị cũ tạo TRƯỚC dòng fraud."""
    org = await mk_org(db)
    await _install(db, org)
    old1, old2, m_push, m_new = [await _raw(db, org, x) for x in ("o1", "o2", "p", "n")]
    old = await signals.upsert(db, org, _upm(FR, _li(FR, ago_h=30), [old1, old2], levels.case_key(FR, at=NOW, raw_event_id=str(old1)), "units"), NOW)
    t1 = NOW + timedelta(hours=1)
    fr = await signals.upsert(db, org, _upm(FR, _li(FR, ago_h=0.02, now=t1, hard_rule_hit=["credentials_entered"]), [m_push],
                                            levels.case_key(FR, at=t1, raw_event_id="actor:p9"), "fraud"), t1)
    assert fr.delivery == "push_now" and fr.id != old.id
    t2 = t1 + timedelta(hours=3)
    s = await signals.upsert(db, org, _upm(FR, _li(FR, ago_h=0.5, now=t2, flags={"credentials_entered": True}), [old2, m_push, m_new],
                                           levels.case_key(FR, at=t2, impersonated_entity_id="that"), "units"), t2)
    assert s.delivery != "push_now"
    rows = await _rows(db, org)
    assert len([r for r in rows if r.status in ("open", "acknowledged")]) == 1
    assert sum(1 for r in rows if r.pushed_at is not None) == 1


async def test_relane_merges_case_state_of_open_same_kind_rows_sharing_raw(db: AsyncSession) -> None:
    """Phòng thủ: hai dòng khoá MẠNH khác nhau cùng loại chung tin gốc (vd imp| và stk|) — dòng kia đã đẩy 🔴 ⇒ dòng này không đẩy lần 2."""
    org = await mk_org(db)
    await _install(db, org)
    r1, r2 = await _raw(db, org, "x"), await _raw(db, org, "y")
    imp = levels.case_key(FR, at=NOW, impersonated_entity_id="e")
    b = await signals.upsert(db, org, _upm(FR, _li(FR, ago_h=0.5), [r2], imp), NOW)
    assert b.pushed_at is None
    stk = levels.case_key(FR, at=NOW, payee_digest="D1", group_id="G1")
    a = await signals.upsert(db, org, _upm(FR, _li(FR, ago_h=0.1, flags={"credentials_entered": True}), [r1], stk), NOW)
    assert a.delivery == "push_now"
    t2 = NOW + timedelta(minutes=20)
    s = await signals.upsert(db, org, _upm(FR, _li(FR, ago_h=0.1, now=t2, flags={"credentials_entered": True}), [r1, r2], imp), t2)
    assert s.id == b.id
    assert s.delivery != "push_now" and s.pushed_at is None


# ─── P2: luật (a) — rò điện / tê tay / rò gas (dạng thoại) ─────────────────────

@pytest.mark.parametrize("text", [
    "chị ơi cái bình nóng lạnh nhà em đụng vô thấy tê tê tay, chắc rò điện rồi",   # transcript kiểu thoại
    "Dây điện ngoài ban công bị rò, ướt hết",
    "Ổ cắm trong bếp chạm vào tê tay luôn",
    "máy giặt bị rò rỉ điện anh qua coi giùm",
    "Thằng nhỏ bị giật điện lúc cắm sạc",
    "Nhà có mùi gas nồng lắm",
    "bình gas xì gas từ tối qua",
])
def test_prefilter_electric_gas_leak_is_emergency_candidate(text: str) -> None:
    f = {c.flag: c for c in pf.scan_text(text, "transcript")}
    assert "emergency" in f and f["emergency"].kind == "suc_khoe_an_toan" and f["emergency"].gate_candidate == "an_toan"
    assert f["emergency"].hard_rule_hit is None              # ứng viên mềm: model xác nhận, không luật cứng


@pytest.mark.parametrize("text", [
    "Trời lạnh quá tê tay không gõ nổi",
    "Ngồi gõ phím cả ngày tê tay",
    "Em bị giật mình khi nghe tin",
    "Hôm qua em bị giật điện thoại ngoài chợ",
    "Nói rõ điện thoại nào thì em báo giá",
    "Máy giặt em mua tuần trước chạy êm lắm",
])
def test_prefilter_electric_gas_no_false_hit(text: str) -> None:
    assert "emergency" not in {c.flag for c in pf.scan_text(text)}


# ─── dựng dữ liệu cho P3/P4/P5/P8 (cùng khuôn T12) ────────────────────────────

async def _paired_owner(db: AsyncSession, w: Any, name: str = "Sếp (tài khoản riêng)") -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    """Hồ sơ người của Sếp ở chế độ tài khoản bot: ingest nhận theo ID tài khoản đã ghép ⇒ đặt `attrs.is_owner` (đúng câu UPDATE của
    `data.ingest._owner_triggers`). Khác hồ sơ người của tài khoản Console (`w.owner`)."""
    pid, iid, ent = await T12._person(db, w, name)
    await db.execute(text("""UPDATE core.persons SET attrs = attrs || '{"is_owner": true}'::jsonb WHERE id = :p"""), {"p": pid})
    await db.commit()
    return pid, iid, ent


# ─── P3: danh tính Sếp ở chế độ tài khoản bot ─────────────────────────────────

async def test_paired_owner_promise_in_group_is_owner_side(db: AsyncSession) -> None:
    """Sếp ghép kênh (ID tài khoản X) — mọi tin của X là inbound; lời hứa của X với khách ⇒ phía Sếp ⇒ có tín hiệu (không `not_owner_side`)."""
    w = await T12._world(db)
    opid, oiid, oent = await _paired_owner(db, w)
    _, _, cent = await T12._person(db, w, "Chị Năm", ptype="customer")
    raw = await mk_raw(db, w.org, w.channel, identity=oiid, body="ok chị, 4 giờ chiều nay anh cho xe chở 30 thùng sơn qua", group=w.group,
                       at=NOW - timedelta(hours=1))
    await T12._unit(db, w, raw, "nghia_vu_loi_hua", person=opid, people=[("nguoi_hua", oent), ("nguoi_duoc_hua", cent)],
                    v={"deadline_at": (NOW + timedelta(hours=2)).isoformat()}, at=NOW - timedelta(hours=1))
    out = await promises.project_units(db, w.org, NOW)
    await db.commit()
    assert len(out) == 1 and out[0].gate == levels.G4
    why = (await db.execute(text("""SELECT detail ->> 'why' FROM ops.action_log WHERE org_id = :o AND action = 'intel.promises.unit_done'"""),
                            {"o": w.org})).scalars().all()
    assert "not_owner_side" not in why


async def test_org_sets_include_paired_owner_person(db: AsyncSession) -> None:
    w = await T12._world(db)
    _, _, oent = await _paired_owner(db, w)
    sets = await generic.org_sets(db, w.org)
    assert sets.is_owner(oent) and sets.is_owner(w.owner) and sets.owner == w.owner
    _, _, other = await T12._person(db, w, "Người thường")
    assert not sets.is_owner(other)


# ─── P4: "chưa trả lời" khi phía Sếp trả lời bằng tin inbound ─────────────────

async def test_question_answered_by_owner_side_inbound_is_not_pending(db: AsyncSession) -> None:
    """Chế độ tài khoản bot: nhân viên (staff_of) hoặc Sếp (đã ghép mã) trả lời trong nhóm bằng tin ĐẾN ⇒ câu hỏi đã có người trả lời.
    Người ngoài (khách khác) trả lời thì vẫn là câu hỏi chờ."""
    from datetime import UTC, datetime
    now = datetime.now(UTC)
    for who, pending in (("staff", 0), ("owner", 0), ("other", 1)):
        w = await T12._world(db)
        pid, iid, _ = await T12._person(db, w, "Khách hỏi", ptype="customer")
        await T12._question_unit(db, w, pid, iid, now)
        if who == "staff":
            _, riid, rent = await T12._person(db, w, "NV Lan")
            await T12._staff_of(db, w, rent)
        elif who == "owner":
            _, riid, _ = await _paired_owner(db, w)
        else:
            _, riid, _ = await T12._person(db, w, "Khách khác", ptype="customer")
        await mk_raw(db, w.org, w.channel, identity=riid, body="dạ bao này 95 nghìn chị nhé", group=w.group, at=now - timedelta(hours=2, minutes=50))
        assert len(await questions.scan_units(db, w.org, now)) == pending, who
        await db.commit()


# ─── P5: nạn nhân mặc định của lừa đảo ───────────────────────────────────────

async def _unit_row(db: AsyncSession, uid: uuid.UUID) -> Any:
    return (await db.execute(text("SELECT * FROM clean.meaning_units WHERE id = :u"), {"u": uid})).one()


async def test_units_people_fraud_impersonator_sender_is_not_default_victim(db: AsyncSession) -> None:
    w = await T12._world(db)
    fpid, fiid, _ = await T12._person(db, w, "Kế toán Hoà (tài khoản mới)")
    _, _, real = await T12._person(db, w, "Kế toán Hoà")
    raw = await mk_raw(db, w.org, w.channel, identity=fiid, body="bên em đổi tài khoản nhận tiền rồi nha", group=w.group, at=NOW - timedelta(minutes=20))
    uid = await T12._unit(db, w, raw, FR, person=fpid, people=[("nguoi_bi_mao_danh", real)])
    sets = await generic.org_sets(db, w.org)
    p = await U.people(db, w.org, await _unit_row(db, uid), sets, [raw])
    assert p.victims == [] and p.victim_who == []
    assert [x.entity_id for x in p.impersonated_who] == [real]
    # người gửi tự kể bị lừa (không vai mạo danh/nhận tiền) ⇒ vẫn là nạn nhân mặc định
    vpid, viid, vent = await T12._person(db, w, "Chú Sáu")
    raw2 = await mk_raw(db, w.org, w.channel, identity=viid, body="chú lỡ làm theo cái tin nhắn kia rồi", group=w.group, at=NOW - timedelta(minutes=10))
    uid2 = await T12._unit(db, w, raw2, FR, person=vpid, people=[])
    p2 = await U.people(db, w.org, await _unit_row(db, uid2), sets, [raw2])
    assert [x.entity_id for x in p2.victim_who] == [vent]


# ─── P7: nhóm không rõ cỡ không phải "nhóm gần" ───────────────────────────────

def test_levels_unknown_group_size_is_not_near_unless_near_context_confirmed() -> None:
    def fr(**th: Any) -> levels.SignalIn:
        return H.sig(FR, victims=[], delivery_form="direct", flags={"credentials_entered": True},
                     thread={"type": "group", "owner_in_thread": True, "agent_in_thread": True, **th})

    assert levels.owner_side(fr(size=0), H.policy("kinh_doanh")).fraud           # cỡ 0 đã biết (giữ hành vi cũ)
    # Gen quyết D-T3 (T14, 26/10): lừa đảo/an toàn fail-high — nhóm không rõ cỡ coi như nhóm gần (thay P7 của T13)
    assert levels.owner_side(fr(size=0, size_known=False), H.policy("kinh_doanh")).fraud
    assert levels.owner_side(fr(size=0, size_known=False), H.policy("gia_dinh")).fraud
    assert H.compute(fr(size=0, size_known=False)).level == "red"
    assert H.compute(fr(size=0)).level == "red"


async def test_raw_group_null_member_count_is_unknown_size(db: AsyncSession) -> None:
    w = await T12._world(db)
    raw = await mk_raw(db, w.org, w.channel, identity=w.owner_identity, body="x", group=w.group)
    assert await generic.raw_group(db, raw) == (w.group, 20)
    await db.execute(text("UPDATE core.groups SET member_count = NULL WHERE id = :g"), {"g": w.group})
    gid, size = await generic.raw_group(db, raw)
    assert gid == w.group and size is None
    th = generic.thread_of(gid, size)
    assert th.size_known is False and th.size == 0


# ─── P8: cờ công khai của khiếu nại theo bối cảnh nhóm ───────────────────────

async def test_complaint_public_flag_decided_by_group_context(db: AsyncSession) -> None:
    """Model gắn `public` cho lời chê của khách; code giữ cờ chỉ khi nhóm còn người KHÁC ngoài khách và phía Sếp."""
    for others, want in ((False, False), (True, True)):
        w = await T12._world(db)
        cpid, ciid, cent = await T12._person(db, w, "Chị Ba", ptype="customer")
        _, _, sent = await T12._person(db, w, "NV Tùng")
        await T12._staff_of(db, w, sent)
        members = [cpid, w.owner_person, (await db.execute(text("SELECT person_id FROM mem.entities WHERE id = :e"), {"e": sent})).scalar_one()]
        if others:
            members.append((await T12._person(db, w, "Khách khác", ptype="customer"))[0])
        for m in members:
            await db.execute(text("INSERT INTO core.group_members (group_id, person_id) VALUES (:g, :p)"), {"g": w.group, "p": m})
        await db.commit()
        raw = await mk_raw(db, w.org, w.channel, identity=ciid, body="giao trễ hoài, chán thiệt", group=w.group, at=NOW - timedelta(minutes=30))
        await T12._unit(db, w, raw, "xung_dot_khieu_nai", person=cpid, flags={"public": True}, people=[("nguoi_bi_anh_huong", cent)],
                        at=NOW - timedelta(minutes=30))
        await U.project(db, w.org, NOW)
        await db.commit()
        rows = await T12._rows(db, w.org, "xung_dot_khieu_nai")
        assert len(rows) == 1, others
        assert rows[0].level_input["flags"]["public"] is want, others
