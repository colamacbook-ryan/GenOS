"""Cung ↔ Cầu không ghép hàng không liên quan (F061).

Trước đây thiếu `entities.product` thì lấy cả câu kết luận làm tên hàng, rồi so từ thô (kể cả "cần", "giao", "tại")
nên "Khách cần mua xi măng PCB40 giao tại Hà Nội" × "Nhà máy chào bán thép cuộn cần giao tại Đà Nẵng" ra "Cùng mặt
hàng +50" ≥ ngưỡng 40 và được lưu thành gợi ý. Nay: thiếu tên hàng thì không tạo tín hiệu; so khớp bỏ stopword và
đòi trùng danh từ hàng hoá; gợi ý cũ không còn đủ điểm thì được gỡ khi chấm lại."""

import pytest
from sqlalchemy import text

from gh.biz.market import service as msvc
from gh.biz.market.jobs import MIN_MATCH_SCORE, recompute_matches_org
from gh.db import sessionmaker
from tests.phase2 import listen, msg, org_id, put
from tests.test_p3_market import _person_of, _unit, capture

DEMAND_SENTENCE = "Khách cần mua xi măng PCB40 giao tại Hà Nội"
SUPPLY_SENTENCE = "Nhà máy chào bán thép cuộn cần giao tại Đà Nẵng"

# ═══ Hàm thuần ════════════════════════════════════════════════════════════════

def test_signal_from_entities_none_without_product_or_category() -> None:
    assert msvc.signal_from_entities({}, DEMAND_SENTENCE) is None
    assert msvc.signal_from_entities(None, DEMAND_SENTENCE) is None
    assert msvc.signal_from_entities({"product": "   ", "qty": 3, "place": "Hà Nội"}, DEMAND_SENTENCE) is None

def test_signal_from_entities_uses_product_never_conclusion() -> None:
    sig = msvc.signal_from_entities({"product": " Xi măng PCB40 ", "qty": "50", "place": "Hà Nội"}, DEMAND_SENTENCE)
    assert sig is not None
    assert sig["item"] == "Xi măng PCB40" and sig["quantity"] == 50.0 and sig["category"] is None
    cat_only = msvc.signal_from_entities({"category": "vật liệu xây dựng"}, DEMAND_SENTENCE)
    assert cat_only is not None and cat_only["item"] == "vật liệu xây dựng"
    assert cat_only["category"] == "vật liệu xây dựng"

def test_sentences_with_shared_filler_words_do_not_match() -> None:
    """Đường cũ: item = cả câu → trùng "cần", "giao", "tại" ⇒ +50. Dù lỡ có cả câu làm item cũng không được ghép."""
    score, reasons = msvc.score_match({"item": DEMAND_SENTENCE}, {"item": SUPPLY_SENTENCE})
    assert score == 0.0 and score < MIN_MATCH_SCORE
    assert "Cùng mặt hàng" not in " ".join(reasons)

def test_different_goods_same_filler_do_not_match() -> None:
    score, _ = msvc.score_match({"item": "xi măng PCB40", "quantity": 100.0, "location": "Hà Nội"},
                                {"item": "thép cuộn", "quantity": 100.0, "location": "Hà Nội"})
    assert score == 0.0

def test_packaging_and_generic_head_noun_alone_do_not_match() -> None:
    # "container" là quy cách đóng gói, không phải mặt hàng; "thép" chung nhưng cuộn ≠ tấm.
    assert msvc.score_match({"item": "container"}, {"item": "container lạnh"})[0] == 0.0
    assert msvc.score_match({"item": "thép cuộn"}, {"item": "thép tấm"})[0] == 0.0

def test_stopwords_and_numbers_are_dropped() -> None:
    assert msvc._norm_words("Cần mua 3 container thép cuộn giao tại Hà Nội") == {"thép", "cuộn", "hà", "nội"}
    assert msvc._norm_words(None) == set() and msvc._norm_words("cần mua giao tại") == set()

@pytest.mark.parametrize(("d", "s"), [
    ("Thép cuộn", "Thép cuộn cao cấp"),            # bao trọn tên
    ("xi măng PCB40", "xi măng PCB30 bao 50kg"),   # chung ≥ 2 từ mang tên hàng
    ("Gỗ MDF", "Gỗ MDF E1"),
    ("thép", "thép tấm"),
])
def test_same_goods_still_match(d: str, s: str) -> None:
    score, reasons = msvc.score_match({"item": d}, {"item": s})
    assert score == 50.0 and "Cùng mặt hàng" in reasons[0]

# ═══ Với CSDL ═════════════════════════════════════════════════════════════════

async def _people(db, org):  # type: ignore[no-untyped-def]
    gid = await listen(db, org, "g1")
    sm = sessionmaker()
    [raw_a] = await put(sm, org, msg("tin của khách", group="g1", sender="lan", name="Chị Lan"))
    [raw_b] = await put(sm, org, msg("tin của nhà máy", group="g1", sender="binh", name="Anh Bình"))
    await db.commit()
    return gid, await _person_of(db, raw_a), await _person_of(db, raw_b)

async def _count(db, sql: str, **p) -> int:  # type: ignore[no-untyped-def]
    return (await db.execute(text(sql), p)).scalar_one()  # type: ignore[no-any-return]

async def test_capture_without_product_makes_no_signal_but_keeps_demand_opportunity(app, db, redis) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    gid, p_lan, p_binh = await _people(db, org)
    u_d = await _unit(db, org, group_id=gid, person_id=p_lan, event_type="AskedPrice", side="demand",
                      conclusion=DEMAND_SENTENCE, entities={"place": "Hà Nội", "budget_vnd": 800_000_000})
    u_s = await _unit(db, org, group_id=gid, person_id=p_binh, event_type="OfferedSupply", side="supply",
                      conclusion=SUPPLY_SENTENCE, entities={"place": "Đà Nẵng"})
    await db.commit()
    await capture(db, redis, org, u_d, u_s)
    await db.commit()
    await capture(db, redis, org, u_d, u_s)    # chạy lại (retry) vẫn an toàn
    await db.commit()

    assert await _count(db, "SELECT count(*) FROM biz.market_signals WHERE org_id = :o", o=org) == 0
    assert await _count(db, "SELECT count(*) FROM biz.matches WHERE org_id = :o", o=org) == 0
    # nhu cầu của khách vẫn thành một cơ hội (chỉ phía cung không có cơ hội), không gắn tín hiệu vì không có
    opp = (await db.execute(text("SELECT need, value_vnd, attrs FROM biz.opportunities WHERE org_id = :o"),
                            {"o": org})).all()
    assert len(opp) == 1 and opp[0].need == DEMAND_SENTENCE and opp[0].value_vnd == 800_000_000
    assert "demand_signal_id" not in opp[0].attrs

async def test_recompute_does_not_match_unrelated_goods_and_drops_stale_suggestion(app, db, redis) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    gid, p_lan, p_binh = await _people(db, org)
    u_d = await _unit(db, org, group_id=gid, person_id=p_lan, event_type="AskedPrice", side="demand",
                      conclusion=DEMAND_SENTENCE, entities={"product": "xi măng PCB40", "place": "Hà Nội"})
    u_s = await _unit(db, org, group_id=gid, person_id=p_binh, event_type="OfferedSupply", side="supply",
                      conclusion=SUPPLY_SENTENCE, entities={"product": "thép cuộn", "place": "Đà Nẵng"})
    await db.commit()
    await capture(db, redis, org, u_d, u_s)
    await db.commit()
    assert await recompute_matches_org(db, org) == 0
    await db.commit()
    assert await _count(db, "SELECT count(*) FROM biz.matches WHERE org_id = :o", o=org) == 0

    # Gợi ý cũ do cách so từ thô sinh ra (status suggested) bị gỡ khi chấm lại; cặp Sếp đã giới thiệu thì giữ.
    d_id = (await db.execute(text("SELECT id FROM biz.market_signals WHERE side = 'demand' AND org_id = :o"),
                             {"o": org})).scalar_one()
    s_ok = (await db.execute(text("SELECT id FROM biz.market_signals WHERE side = 'supply' AND org_id = :o"),
                             {"o": org})).scalar_one()
    s_kept = (await db.execute(text("""INSERT INTO biz.market_signals (org_id, side, item, status)
                                       VALUES (:o, 'supply', 'cát vàng', 'open') RETURNING id"""),
                               {"o": org})).scalar_one()
    for sid, status in ((s_ok, "suggested"), (s_kept, "introduced")):
        await db.execute(text("""INSERT INTO biz.matches (org_id, demand_id, supply_id, score, reasons, status)
                                 VALUES (:o, :d, :s, 50, CAST('["cũ"]' AS jsonb), :st)"""),
                         {"o": org, "d": d_id, "s": sid, "st": status})
    await db.commit()
    await recompute_matches_org(db, org)
    await db.commit()
    rows = {r.supply_id: r.status for r in (await db.execute(
        text("SELECT supply_id, status FROM biz.matches WHERE org_id = :o"), {"o": org})).all()}
    assert rows == {s_kept: "introduced"}
