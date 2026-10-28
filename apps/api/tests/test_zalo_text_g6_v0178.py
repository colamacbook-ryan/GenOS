# ruff: noqa: E501
"""v0.1.78 (gói G6 "Chữ trên Zalo") — tiêu đề không bị cắt mất dấu đóng, "Vì sao" chỉ từ dữ liệu thật, tên người vào tiêu đề."""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

from sqlalchemy import text

from gh.biz.intel import digest as digest_mod
from gh.biz.intel.pipes import delivery
from gh.db import sessionmaker
from gh.owner_cmd import text as otext
from tests.mem_helpers import mk_org
from tests.owner_helpers import bot_world
from tests.test_intel_delivery_digest_v0176 import EVENING, outbox, run
from tests.test_intel_digest_rawtail_v0176 import NOW, _install, _sig

SM = sessionmaker
LONG = "Người mới vào nhóm “Chợ thép sỉ miền Nam” gửi số tài khoản và yêu cầu thanh toán (STK ...456)"


def _item(title: str) -> digest_mod.DigestItem:
    return digest_mod.DigestItem(1, "s", "C1", "cau_hoi_cho_xu_ly", "amber", "digest", None, None, title, [], None, action="Gọi xác minh")


def test_render_keeps_closing_paren_of_long_title_g6() -> None:
    """Gốc lỗi: `_one_line(title, 90)` cắt tiêu đề 93 ký tự ⇒ «(STK ...…» (mất «456)»)."""
    d = digest_mod.Digest("o", NOW, [_item(LONG)], {}, None)
    body, _ = delivery.render(d, label="07:30", shadow=False, hidden_console=0, coverage_line="Độ phủ", recovered=[])
    assert "(STK ...456)" in body
    assert "......" not in body and "...…" not in body


def test_cut_title_never_leaves_open_bracket_g6() -> None:
    assert otext.cut_title(LONG, 120) == LONG
    cut = otext.cut_title(LONG, 80)
    assert cut.endswith("…") and "(" not in cut and len(cut) <= 80
    assert otext.cut_title("Có người xưng là anh «chuyển khoản gấp ngay bây giờ nhé" * 2, 40).count("«") == 0
    assert otext.cut_title("ngắn", 40) == "ngắn"


async def test_digest_title_with_stk_not_truncated_g6(db: Any) -> None:
    org = await mk_org(db)
    await _install(db, org)
    s = await _sig(db, org, "cau_hoi_cho_xu_ly", "q-long", impact_base=2, deadline_at=NOW + timedelta(hours=10))
    await db.execute(text("UPDATE biz.alerts SET title = :t WHERE id = :i"), {"t": LONG, "i": s.id})
    await db.commit()
    await bot_world(db, org)
    assert await run(org) == "sent"
    [row] = await outbox(db)
    first = row.text.splitlines()[1]
    assert "(STK ...456)" in first and "......" not in first


async def _red_signal(db: Any, org: uuid.UUID, **upd: Any) -> Any:
    s = await _sig(db, org, "xung_dot_khieu_nai", "c-red", customer_of_owner=True, impact_base=2, flags={"public": True})
    if upd:
        sets = ", ".join(f"{k} = :{k}" for k in upd)
        await db.execute(text(f"UPDATE biz.alerts SET {sets} WHERE id = :i"), {**upd, "i": s.id})  # noqa: S608
        await db.commit()
    await bot_world(db, org)
    return s


async def _alert_text(org: uuid.UUID, db: Any) -> str:
    async with SM()() as sess:
        assert await delivery.push_alerts(sess, org, NOW) == 1
        await sess.commit()
    [row] = await outbox(db, "alert")
    return str(row.text)


async def test_alert_why_uses_summary_then_reasons_never_generic_g6(db: Any) -> None:
    org = await mk_org(db)
    await _install(db, org)
    await _red_signal(db, org, summary="Khách doạ bỏ vì giao trễ hai lần")
    body = await _alert_text(org, db)
    assert "Vì sao: Khách doạ bỏ vì giao trễ hai lần" in body and "Vụ này cần Sếp xem ngay" not in body


async def test_alert_why_falls_back_to_reasons_g6(db: Any) -> None:
    org = await mk_org(db)
    await _install(db, org)
    s = await _red_signal(db, org, summary=None)
    reasons = (await db.execute(text("SELECT reasons FROM biz.alerts WHERE id = :i"), {"i": s.id})).scalar_one()
    expect = [digest_mod.REASON_VI[c] for c in reasons if c in digest_mod.REASON_VI]
    body = await _alert_text(org, db)
    assert "Vụ này cần Sếp xem ngay" not in body
    why = [ln for ln in body.splitlines() if ln.startswith("Vì sao:")]
    assert bool(why) == bool(expect)                       # có lý do thật ⇒ có dòng; không có ⇒ bỏ hẳn dòng
    if why:
        assert expect[0].split("{")[0].strip()[:10] in why[0]


async def test_alert_without_summary_or_reasons_drops_why_line_g6(db: Any) -> None:
    org = await mk_org(db)
    await _install(db, org)
    await _red_signal(db, org, summary=None, reasons=[])
    body = await _alert_text(org, db)
    assert "Vì sao" not in body and "Vụ này cần Sếp" not in body and "Đề xuất:" in body


def test_why_line_helper_g6() -> None:
    assert delivery.why_line("  ", []) is None
    assert delivery.why_line(None, ["a", "b"]) == "a; b"
    assert delivery.why_line("Tóm tắt thật", ["a"]) == "Tóm tắt thật"


def test_with_name_g6() -> None:
    assert delivery.with_name("Khách có thể bỏ đi", "Anh Nam") == "Anh Nam: Khách có thể bỏ đi"
    assert delivery.with_name("Anh Nam doạ bỏ", "anh nam") == "Anh Nam doạ bỏ"
    assert delivery.with_name("x", None) == "x"


async def test_person_name_goes_into_alert_and_digest_title_and_stk_still_masked_g6(db: Any) -> None:
    org = await mk_org(db)
    await _install(db, org)
    pid = (await db.execute(text("INSERT INTO core.persons (org_id, code, display_name) VALUES (:o, 'P-G6', 'Anh Nam') RETURNING id"), {"o": org})).scalar_one()
    await _red_signal(db, org, subject_type="person", subject_id=pid, summary="Chuyển vào 0123456789012 gấp")
    body = await _alert_text(org, db)
    assert body.startswith("🔴 Anh Nam: ") and "0123456789012" not in body and "0123…012" in body
    am = await _sig(db, org, "cau_hoi_cho_xu_ly", "q-name", impact_base=2, deadline_at=NOW + timedelta(hours=10))
    await db.execute(text("UPDATE biz.alerts SET subject_type = 'person', subject_id = :p WHERE id = :i"), {"p": pid, "i": am.id})
    await db.commit()
    assert await run(org) == "sent"
    [dig] = await outbox(db)
    assert "Anh Nam: cau_hoi_cho_xu_ly q-name" in dig.text
    _ = EVENING
