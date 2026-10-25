# ruff: noqa: E501
"""v0.1.76 T4 — review vòng 2: lời hứa không hạn / quá hạn lần đầu gặp vẫn ra tín hiệu; ngày sai không làm chết cả tổ chức; thứ tự quét."""

from __future__ import annotations

import uuid
from datetime import timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gh.biz.intel import levels
from gh.biz.intel.detectors import generic as G
from gh.biz.intel.detectors import promises
from gh.biz.mem import entities, facts
from tests.mem_helpers import mkfact
from tests.test_intel_promises_v0176 import NOW, _alert, _fact, _promise_rows, _world


async def test_rv2_staff_promise_to_customer_without_due_is_amber_signal_and_rerunnable(db: AsyncSession) -> None:
    w = await _world(db)
    await _fact(db, w, "promised_to", w.staff, "em sẽ gửi hợp đồng cho anh", at=NOW - timedelta(hours=2), due=None, to=w.cust)
    out = await promises.project(db, w.org, NOW)
    await db.commit()
    assert len(out) == 1 and out[0].level == "amber" and out[0].gate is None
    for _ in range(2):                                              # chạy lại: không trùng, không lỗi
        await promises.project(db, w.org, NOW + timedelta(minutes=5))
        await db.commit()
    assert len(await _alert(db, w.org)) == 1
    assert await _promise_rows(db, w.org) == []                      # không hạn ⇒ không có dòng sổ (cột due_at NOT NULL)


async def test_rv2_first_seen_overdue_4_days_still_projected_but_not_when_already_known(db: AsyncSession) -> None:
    w = await _world(db)
    await _fact(db, w, "promised_to", w.staff, "em gửi báo giá", at=NOW - timedelta(days=6), due=NOW - timedelta(days=4), to=w.cust)
    out = await promises.project(db, w.org, NOW)
    await db.commit()
    assert len(out) == 1 and out[0].gate is None and out[0].level != "red"      # đã vỡ từ ngày trước: không cổng
    assert [r.state for r in await _promise_rows(db, w.org)] == ["open"]           # sổ ghi nhận để "hứa rồi quên" thấy được
    assert await promises.project(db, w.org, NOW + timedelta(minutes=5)) == []      # đã có sổ + tín hiệu ⇒ không quét lại


async def test_rv2_invalid_calendar_date_only_skips_that_fact(db: AsyncSession) -> None:
    w = await _world(db)
    await _fact(db, w, "promised_to", w.staff, "giao hàng", at=NOW - timedelta(hours=3), due=NOW + timedelta(hours=2), to=w.cust)
    bad = {"text": "việc hỏng ngày", "due_at": "2026-02-30T10:00:00+07:00", "to_entity_id": str(w.cust)}
    await facts.assert_fact(db, mkfact(w.org, w.staff, bad, NOW - timedelta(hours=2), predicate="promised_to"))
    await db.commit()
    out = await promises.project(db, w.org, NOW)
    await db.commit()
    assert any(s.gate == levels.G4 for s in out)                      # lời hứa tốt vẫn ra 🔴; ngày sai = không hạn, không làm chết lượt quét


async def test_rv2_order_prefers_nearest_due(db: AsyncSession) -> None:
    w = await _world(db)
    await _fact(db, w, "promised_to", w.staff, "việc xa", at=NOW - timedelta(hours=5), due=NOW + timedelta(days=20), to=w.cust)
    await _fact(db, w, "promised_to", w.staff, "việc gần", at=NOW - timedelta(hours=9), due=NOW + timedelta(hours=2), to=w.cust)
    out = await promises.project(db, w.org, NOW, limit=1)
    assert [s.gate for s in out] == [levels.G4]


async def test_rv2_failhigh_group_without_member_list(db: AsyncSession) -> None:
    """Nhóm chưa có danh sách thành viên ⇒ người được nêu tên coi như thành viên (an toàn fail-high); có danh sách mà không có người đó ⇒ không."""
    from tests.mem_helpers import mk_org, mk_person_row
    from tests.test_intel_generic_v0176 import _install, _unit_row

    org = await mk_org(db)
    await _install(db, org)
    ch = (await db.execute(text("INSERT INTO core.channels (org_id, type, name) VALUES (:o, 'zalo', 'z') RETURNING id"), {"o": org})).scalar_one()
    raw = None
    for code, with_list in (("GRP-A", False), ("GRP-B", True)):
        grp = (await db.execute(text("""INSERT INTO core.groups (org_id, code, channel_id, external_id, name, member_count)
                                        VALUES (:o, :c, :ch, :c, 'nhóm', 20) RETURNING id"""), {"o": org, "c": code, "ch": ch})).scalar_one()
        raw = (await db.execute(text("""INSERT INTO raw.events (org_id, occurred_at, group_id, channel_id, external_msg_id, kind, body_text, payload, content_hash)
                                        VALUES (:o, now(), :g, :c, :x, 'text', 'x', '{}', '\\x01') RETURNING id"""),
                                {"o": org, "g": grp, "c": ch, "x": uuid.uuid4().hex})).scalar_one()
        sender = await mk_person_row(db, org, f"Gửi {code}")
        victim = (await entities.ensure_person(db, org, await mk_person_row(db, org, f"Nạn nhân {code}"))).id
        if with_list:
            await entities.ensure_person(db, org, sender)
            await db.execute(text("INSERT INTO core.group_members (group_id, person_id) VALUES (:g, :p)"), {"g": grp, "p": sender})
        await _unit_row(db, org, raw, "suc_khoe_an_toan", {"emergency": True, "ongoing": True}, person=sender, mention_entity=victim)
    out = await G.project_units(db, org, NOW)
    assert sum(1 for s in out if s.gate == levels.G2) == 1               # GRP-A (không danh sách) qua cổng; GRP-B (có danh sách, nạn nhân ngoài) thì không


async def test_rv2_flood_of_processed_no_due_promises_cannot_push_out_real_deadline(db: AsyncSession) -> None:
    """Tấn công: nhiều lời hứa không hạn (đã xử lý) + 1 lời hứa có hạn 3 giờ, limit nhỏ ⇒ lời hứa có hạn ra 🔴 ngay lượt đầu."""
    w = await _world(db)
    for i in range(30):
        await _fact(db, w, "promised_to", w.staff, f"em sẽ gửi tài liệu {i}", at=NOW - timedelta(hours=40 - i), due=None, to=w.cust)
    first = []
    for i in range(3):                                                # nhóm không hạn có trần riêng (limit/5) ⇒ cần vài lượt để xử lý hết
        first += await promises.project(db, w.org, NOW - timedelta(minutes=10 - i), limit=50)
    assert len(first) == 30
    await _fact(db, w, "promised_to", w.staff, "giao hàng chiều nay", at=NOW - timedelta(hours=2), due=NOW + timedelta(hours=3), to=w.cust)
    out = await promises.project(db, w.org, NOW, limit=3)
    assert [s.gate for s in out] == [levels.G4]                       # 30 fact không hạn đã có tín hiệu ⇒ không quét lại
    # chưa xử lý: trần riêng cho nhóm không hạn, hạn thật xếp trước
    w2 = await _world(db)
    for i in range(30):
        await _fact(db, w2, "promised_to", w2.staff, f"việc {i}", at=NOW - timedelta(hours=40 - i), due=None, to=w2.cust)
    await _fact(db, w2, "promised_to", w2.staff, "giao hàng chiều nay", at=NOW - timedelta(hours=2), due=NOW + timedelta(hours=3), to=w2.cust)
    out2 = await promises.project(db, w2.org, NOW, limit=5)
    assert out2[0].gate == levels.G4 and len(out2) == 5
    assert sum(1 for s in out2 if s.gate is None) == 4 and sum(1 for s in out2 if s.level != "red") == 4
