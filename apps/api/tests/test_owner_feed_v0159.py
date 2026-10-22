"""v0.1.59 (G1) — Mặt tiền Owner v2: `GET /owner/feed` và `GET /owner/feed/{id}/context`.

- Mức quan trọng do feed.py tự tính: Complained (Gen chỉ chắc 0.6) đứng trước AskedPrice (0.7), AskedPrice đứng
  trước câu
  chào (0.95) — KHÔNG dùng `priority` của view.
- Bỏ rác / trùng / điểm thấp theo refinery.item_marks (khi bật lọc đầu); bỏ tin tắt tiếng và tin kênh Facebook.
- Cửa sổ 7 ngày CHỈ cho unit: nháp chờ duyệt 10 ngày tuổi vẫn có ở view=approval và counts.approval; unit 8 ngày
  tuổi không.
- Bộ lọc, đếm, phân trang offset; `filtered_7d` lấy từ triage.value_summary.
- context: ≤ 10 tin cùng kênh/nhóm, đúng thứ tự, đúng một is_center, cắt 280 ký tự; item không có gốc → [].

Chạy cả quyền superuser và `GH_TEST_APP_ROLE=1` (gh_app): `pytest -q tests/test_owner_feed_v0159.py`.
"""

import uuid
from datetime import timedelta
from typing import Any

import orjson
import pytest
from sqlalchemy import text

from gh.owner import feed
from gh.refinery import triage
from tests.conftest import Api
from tests.test_owner_v0155 import _org, _person
from tests.test_owner_v0159 import (
    ago,
    alert,
    channel,
    draft,
    event,
    group,
    identity,
    mark,
    unit,
)


@pytest.fixture
async def owner(owner_api: Api) -> Api:
    return owner_api


async def _feed(owner: Api, **q: Any) -> dict[str, Any]:
    qs = "&".join(f"{k}={v}" for k, v in q.items())
    r = await owner.get("/owner/feed" + (f"?{qs}" if qs else ""))
    assert r.status_code == 200, r.text
    return r.json()  # type: ignore[no-any-return]


# ─── mức quan trọng + thứ tự ──────────────────────────────────────────────────────────────────────────────────────

async def test_importance_order_complained_before_asked_price_before_greeting(owner: Api, db: Any) -> None:
    org = await _org(db)
    lan = await _person(db, org, "PER-1", "Chị Lan")
    # tin cũ nhất lại là Complained (Gen chỉ chắc 0.6); câu chào mới nhất và Gen chắc 0.95 ⇒ vẫn xuống cuối
    await unit(db, org, ago(hours=5), "Complained", "Khách than giao hàng chậm", confidence=0.6, person=lan)
    await unit(db, org, ago(hours=3), "AskedPrice", "Khách hỏi giá MDF E1", confidence=0.7, person=lan)
    await unit(db, org, ago(hours=1), "Greeting", "Chào buổi sáng", confidence=0.95, person=lan)
    await alert(db, org, "Khách sắp rời", priority="P1", created_at=ago(hours=9))
    await alert(db, org, "Phản hồi chậm", priority="P2", created_at=ago(hours=8))
    await draft(db, org, "Trả lời chị Lan", created_at=ago(hours=7))
    await db.commit()
    d = await _feed(owner, view="all")
    got = [(i["item_type"], i["event_type"] or i["title"], i["importance"]) for i in d["items"]]
    assert got == [
        ("unit", "Complained", "Khẩn"),                      # hạng trước, rồi mới hơn đứng trước
        ("alert", "Khách sắp rời", "Khẩn"),
        ("unit", "AskedPrice", "Quan trọng"),
        ("draft", "Trả lời chị Lan", "Quan trọng"),
        ("alert", "Phản hồi chậm", "Quan trọng"),            # cảnh báo mở khác P1
        ("unit", "Greeting", "Thường"),
    ]
    units = [i["event_type"] for i in d["items"] if i["item_type"] == "unit"]
    assert units == ["Complained", "AskedPrice", "Greeting"]
    # view mặc định = important = Khẩn + Quan trọng (không có Thường)
    imp = await _feed(owner)
    assert {i["importance"] for i in imp["items"]} == {"Khẩn", "Quan trọng"} and len(imp["items"]) == 5
    assert imp["counts"] == {"important": 5, "all": 6, "opportunity": 1, "alert": 2, "approval": 1, "urgent_open": 2}


async def test_item_shape_titles_and_clipping(owner: Api, db: Any) -> None:
    org = await _org(db)
    ch = await channel(db, org)
    lan = await _person(db, org, "PER-1", "Chị Lan")
    g = await group(db, org, ch, "Nhóm Gỗ Việt")
    await unit(db, org, ago(hours=2), "AskedPrice", "Giá " + "rất dài " * 80, person=lan, grp=g)
    await unit(db, org, ago(hours=3), "Complained", "Giao chậm", person=lan)
    await unit(db, org, ago(hours=4), "KhongBiet", "Loại lạ", person=lan)
    await db.execute(text("""INSERT INTO biz.alerts (org_id, code, alert_type, priority, title, summary,
                                                     suggested_action, status)
                             VALUES (:o, 'ALR-9', 'customer_cooling', 'P1', 'Khách đang nguội', 'Đã 20 ngày',
                                     'Gọi hỏi thăm', 'open')"""), {"o": org})
    await db.commit()
    items = {i["event_type"] or i["item_type"]: i for i in (await _feed(owner, view="all"))["items"]}
    ask = items["AskedPrice"]
    assert set(ask) == {"id", "item_type", "title", "summary", "importance", "event_type", "created_at", "person",
                        "group", "suggested_action"}
    assert ask["title"] == "Hỏi giá / nhu cầu mua" and len(ask["summary"]) <= 240 and ask["summary"].endswith("…")
    assert ask["person"]["name"] == "Chị Lan" and ask["group"] == {"id": str(g), "name": "Nhóm Gỗ Việt",
                                                                     "channel": "zalo"}
    assert ask["suggested_action"] is None
    assert items["Complained"]["title"] == "Phàn nàn" and items["Complained"]["group"] is None
    assert items["KhongBiet"]["title"] == "Tin khác" and items["KhongBiet"]["importance"] == "Thường"
    al = items["alert"]
    assert al["title"] == "Khách đang nguội" and al["event_type"] is None and al["importance"] == "Khẩn"
    assert al["suggested_action"] == "Gọi hỏi thăm" and al["summary"] == "Đã 20 ngày" and al["person"] is None


async def test_hot_sender_makes_a_greeting_important(owner: Api, db: Any) -> None:
    org = await _org(db)
    hot = await _person(db, org, "PER-1", "Khách nóng", heat=85)
    warm = await _person(db, org, "PER-2", "Khách ấm", heat=70)
    await unit(db, org, ago(hours=1), "Greeting", "Chào từ khách nóng", person=hot)
    await unit(db, org, ago(hours=2), "Greeting", "Chào từ khách ấm", person=warm)
    await db.commit()
    by = {i["summary"]: i["importance"] for i in (await _feed(owner, view="all"))["items"]}
    assert by == {"Chào từ khách nóng": "Quan trọng", "Chào từ khách ấm": "Thường"}


# ─── cửa sổ thời gian ─────────────────────────────────────────────────────────────────────────────────────────────

async def test_window_only_for_units_pending_drafts_and_open_alerts_are_unbounded(owner: Api, db: Any) -> None:
    org = await _org(db)
    await unit(db, org, ago(days=6, hours=20), "AskedPrice", "Tin 6 ngày")
    await unit(db, org, ago(days=8), "AskedPrice", "Tin 8 ngày")
    await draft(db, org, "Nháp 10 ngày", created_at=ago(days=10))
    await draft(db, org, "Nháp đã gửi", status="sent", created_at=ago(days=1))
    await alert(db, org, "Cảnh báo 30 ngày", created_at=ago(days=30))
    await alert(db, org, "Cảnh báo đã xử lý", status="resolved")
    await db.commit()
    appr = await _feed(owner, view="approval")
    assert [i["title"] for i in appr["items"]] == ["Nháp 10 ngày"] and appr["counts"]["approval"] == 1
    assert appr["items"][0]["importance"] == "Quan trọng" and appr["items"][0]["summary"] == "Dạ em gửi báo giá ạ"
    assert [i["title"] for i in (await _feed(owner, view="alert"))["items"]] == ["Cảnh báo 30 ngày"]
    summaries = [i["summary"] for i in (await _feed(owner, view="all"))["items"] if i["item_type"] == "unit"]
    assert summaries == ["Tin 6 ngày"]                       # tin 8 ngày tuổi không có


# ─── rác / trùng / điểm thấp / tắt tiếng / Facebook ───────────────────────────────────────────────────────────────

async def test_spam_duplicate_and_low_score_are_hidden_unless_triage_is_off(owner: Api, db: Any) -> None:
    org = await _org(db)
    t = ago(hours=2)
    keep = await unit(db, org, t, "AskedPrice", "Tin tốt")
    await mark(db, org, keep, t, quality=80)
    spam = await unit(db, org, t, "AskedPrice", "Tin rác")
    await mark(db, org, spam, t, spam=True, quality=90)
    dup = await unit(db, org, t, "AskedPrice", "Tin trùng")
    await mark(db, org, dup, t, dup=keep, quality=90)
    low = await unit(db, org, t, "AskedPrice", "Điểm thấp")
    await mark(db, org, low, t, quality=10)
    await unit(db, org, t, "AskedPrice", "Chưa được lọc")           # chưa có dấu lọc ⇒ vẫn hiện
    await db.commit()
    d = await _feed(owner, view="all")
    assert sorted(i["summary"] for i in d["items"]) == ["Chưa được lọc", "Tin tốt"] and d["counts"]["all"] == 2
    await db.execute(text("""UPDATE core.organizations SET settings = jsonb_set(settings, '{triage}',
                             '{"enabled": false, "min_score": 30, "use_jev": false}')"""))
    await db.commit()
    assert (await _feed(owner, view="all"))["counts"]["all"] == 5


async def test_silenced_items_and_facebook_group_are_excluded(owner: Api, db: Any) -> None:
    org = await _org(db)
    uid = (await db.execute(text("SELECT id FROM core.users LIMIT 1"))).scalar_one()
    fb = await channel(db, org, "facebook", "Facebook")
    gfb = await group(db, org, fb, "Trang Facebook")
    await unit(db, org, ago(hours=1), "AskedPrice", "Bình luận Facebook", grp=gfb)
    quiet = await unit(db, org, ago(hours=2), "AskedPrice", "Tin đã tắt tiếng")
    await unit(db, org, ago(hours=3), "AskedPrice", "Tin còn hiện")
    await db.execute(text("""INSERT INTO biz.queue_silences (org_id, item_type, item_id, silenced_by)
                             VALUES (:o, 'unit', :i, :u)"""), {"o": org, "i": quiet, "u": uid})
    await db.commit()
    assert [i["summary"] for i in (await _feed(owner, view="all"))["items"]] == ["Tin còn hiện"]


async def test_silenced_draft_is_not_counted_by_nav_decide_nor_approval(owner: Api, db: Any) -> None:
    """nit: nav.decide (Việc) và counts.approval (Thông tin) cùng loại nháp đã "Tắt tiếng"."""
    org = await _org(db)
    uid = (await db.execute(text("SELECT id FROM core.users LIMIT 1"))).scalar_one()
    await draft(db, org, "Nháp còn hiện")
    quiet = await draft(db, org, "Nháp đã tắt tiếng")
    await db.execute(text("""INSERT INTO biz.queue_silences (org_id, item_type, item_id, silenced_by)
                             VALUES (:o, 'draft', :i, :u)"""), {"o": org, "i": quiet, "u": uid})
    await db.commit()
    assert (await owner.get("/owner/today")).json()["nav"]["decide"] == 1
    assert (await _feed(owner))["counts"]["approval"] == 1


async def test_drafts_list_can_hide_silenced_so_the_front_count_matches_nav(owner: Api, db: Any) -> None:
    """F-60-R17 (sau /code-review): "Việc phải quyết"/"Cần Sếp quyết" lấy `GET /drafts?hide_silenced=true` — cùng
    tập với nav.decide và counts.approval (mặc định, như Bàn làm việc của Console, vẫn liệt kê cả nháp tắt tiếng)."""
    org = await _org(db)
    uid = (await db.execute(text("SELECT id FROM core.users LIMIT 1"))).scalar_one()
    shown = await draft(db, org, "Nháp còn hiện")
    quiet = await draft(db, org, "Nháp đã tắt tiếng")
    expired = await draft(db, org, "Nháp tắt tiếng đã hết hạn")
    await db.execute(text("""INSERT INTO biz.queue_silences (org_id, item_type, item_id, silenced_by, until)
                             VALUES (:o, 'draft', :q, :u, NULL), (:o, 'draft', :e, :u, now() - interval '1 hour')"""),
                     {"o": org, "q": quiet, "e": expired, "u": uid})
    await db.commit()
    plain = (await owner.get("/drafts?status=pending")).json()
    assert plain["total"] == 3
    hidden = (await owner.get("/drafts?status=pending&hide_silenced=true")).json()
    assert hidden["total"] == 2 and {i["id"] for i in hidden["items"]} == {str(shown), str(expired)}
    nav = (await owner.get("/owner/today")).json()["nav"]["decide"]
    assert nav == hidden["total"] == (await _feed(owner))["counts"]["approval"]


async def _reply_draft(db: Any, org: uuid.UUID, unit_id: uuid.UUID, status: str) -> uuid.UUID:
    """Nháp trả lời tạo từ tin (như `POST /inbox/{id}/act`: sources[].ref = meaning_unit) ở trạng thái `status`."""
    did = await draft(db, org, "Trả lời Khiếu nại", status=status)
    await db.execute(text("UPDATE biz.action_drafts SET sources = CAST(:s AS jsonb) WHERE id = :i"),
                     {"i": did, "s": orjson.dumps([{"label": "Khiếu nại", "ref": {"type": "meaning_unit",
                                                                                    "id": str(unit_id)}}]).decode()})
    return did


async def test_urgent_open_drops_a_complaint_once_its_reply_draft_is_approved_or_sent(owner: Api, db: Any) -> None:
    """F-60-R10 (sau /code-review): trả lời + duyệt nháp làm số Khẩn giảm; nav.urgent_open == counts.urgent_open."""
    org = await _org(db)
    lan = await _person(db, org, "PER-1", "Chị Lan")
    keep = await unit(db, org, ago(hours=5), "Complained", "Khách than giao chậm", person=lan)
    done = await unit(db, org, ago(hours=4), "Complained", "Khách than hàng lỗi", person=lan)
    sent = await unit(db, org, ago(hours=3), "Complained", "Khách than sai màu", person=lan)
    waiting = await unit(db, org, ago(hours=2), "Complained", "Khách than đóng gói", person=lan)
    rejected = await unit(db, org, ago(hours=1), "Complained", "Khách than giá", person=lan)
    await _reply_draft(db, org, done, "approved")
    await _reply_draft(db, org, sent, "sent")
    await _reply_draft(db, org, waiting, "pending")           # còn chờ duyệt ⇒ CHƯA xử lý
    await _reply_draft(db, org, rejected, "rejected")         # bị từ chối ⇒ CHƯA xử lý
    await db.commit()
    d = await _feed(owner, view="all")
    by_id = {i["id"]: i["importance"] for i in d["items"] if i["item_type"] == "unit"}
    assert by_id[str(keep)] == by_id[str(waiting)] == by_id[str(rejected)] == "Khẩn"
    assert by_id[str(done)] == by_id[str(sent)] == "Thường"
    assert d["counts"]["urgent_open"] == 3
    assert (await owner.get("/owner/today")).json()["nav"]["urgent_open"] == 3
    # nháp "đã gửi" tạo từ tin khác loại/không ghi nguồn không làm tin nào mất Khẩn
    other = await draft(db, org, "Nháp lạ", status="sent")
    await db.execute(text("UPDATE biz.action_drafts SET sources = CAST('\"x\"' AS jsonb) WHERE id = :i"), {"i": other})
    await db.commit()
    assert (await _feed(owner))["counts"]["urgent_open"] == 3


async def test_huge_offset_is_clamped_not_a_500(owner: Api) -> None:
    r = await owner.get("/owner/feed?offset=99999999999999999999")
    assert r.status_code == 200 and r.json()["items"] == [] and r.json()["next_offset"] is None


# ─── bộ lọc, đếm, phân trang ──────────────────────────────────────────────────────────────────────────────────────

async def test_views_counts_and_offset_paging(owner: Api, db: Any) -> None:
    org = await _org(db)
    for k, et in enumerate(["AskedPrice", "OfferedSupply", "RequestedSample", "SentQuotation", "RequestedPartnership",
                            "DealWon", "AskedStatus", "PromisedDelivery", "MentionsCompetitor", "Greeting"]):
        await unit(db, org, ago(hours=k + 1), et, f"Tin {et}")
    await alert(db, org, "Cảnh báo")
    await draft(db, org)
    await db.commit()
    opp = await _feed(owner, view="opportunity")
    # v0.1.71 (P3-8): cùng bảng với tab «Cơ hội» của Hộp thư (`qsvc.OPPORTUNITY_EVENTS`)
    assert {i["event_type"] for i in opp["items"]} == {"AskedPrice", "OfferedSupply", "RequestedPartnership"}
    assert opp["counts"] == {"important": 11, "all": 12, "opportunity": 3, "alert": 1, "approval": 1, "urgent_open": 0}
    assert len((await _feed(owner, view="important"))["items"]) == 11
    assert [i["item_type"] for i in (await _feed(owner, view="alert"))["items"]] == ["alert"]
    # phân trang offset
    seen: list[str] = []
    off: int | None = 0
    pages = 0
    while off is not None:
        page = await _feed(owner, view="all", offset=off, limit=5)
        seen += [i["id"] for i in page["items"]]
        off, pages = page["next_offset"], pages + 1
    assert pages == 3 and len(seen) == 12 and len(set(seen)) == 12
    assert (await _feed(owner, view="all", offset=10, limit=5))["next_offset"] is None
    assert len((await _feed(owner, view="all", limit=500))["items"]) == 12          # chặn 50 (chỉ có 12)
    assert len((await _feed(owner, view="all", limit=0))["items"]) == 1             # chặn ≥ 1
    assert len((await _feed(owner, view="all", limit=-4))["items"]) == 1
    assert (await _feed(owner, view="all", offset=-5, limit=2))["next_offset"] == 2
    assert feed.clamp_limit(None) == 30 and feed.clamp_limit(10**6) == 50
    assert (await owner.get("/owner/feed?view=bogus")).status_code == 422


async def test_important_event_types_constant_is_the_single_source() -> None:
    assert feed.IMPORTANT_EVENT_TYPES == frozenset({
        "AskedPrice", "AskedStatus", "OfferedSupply", "SentQuotation", "RequestedSample", "RequestedPartnership",
        "PromisedDelivery", "MentionsCompetitor", "DealWon"})
    assert feed.OPPORTUNITY_EVENT_TYPES <= feed.IMPORTANT_EVENT_TYPES | {"OfferedSupply"}
    assert "Complained" not in feed.IMPORTANT_EVENT_TYPES            # Complained là Khẩn, xét riêng


async def test_filtered_7d_and_channels_connected(owner: Api, db: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake(db_: Any, org_id: Any, days: int = 7) -> dict[str, Any]:
        return {"filtered": 124, "spam_blocked": 31, "calls_saved": 9, "jev_on": True}

    monkeypatch.setattr(triage, "value_summary", fake, raising=False)
    d = await _feed(owner)
    assert d["filtered_7d"] == {"filtered": 124, "spam_blocked": 31} and d["channels_connected"] == 0
    assert d["items"] == [] and d["next_offset"] is None
    assert d["counts"] == {"important": 0, "all": 0, "opportunity": 0, "alert": 0, "approval": 0, "urgent_open": 0}
    org = await _org(db)
    ch = await channel(db, org)
    await db.execute(text("INSERT INTO core.channel_sessions (channel_id, account_label, state) "
                          "VALUES (:c, 'Zalo của Sếp', 'active')"), {"c": ch})
    await db.commit()
    assert (await _feed(owner))["channels_connected"] == 1


# ─── context ─────────────────────────────────────────────────────────────────────────────────────────────────────

async def _thread(db: Any, org: uuid.UUID, n: int = 15) -> dict[str, Any]:
    """Nhóm Zalo có `n` tin xen kẽ đến/đi (mỗi phút một tin); tin thứ 8 (chỉ số 7) có đơn vị ý nghĩa."""
    ch = await channel(db, org)
    g = await group(db, org, ch, "Nhóm Gỗ Việt")
    other_g = await group(db, org, ch, "Nhóm khác")
    lan = await _person(db, org, "PER-1", "Chị Lan")
    nam = await _person(db, org, "PER-2", "Anh Nam")
    sid_lan, sid_nam = await identity(db, lan, ch), await identity(db, nam, ch)
    base = ago(hours=3)
    evs = []
    for k in range(n):
        out = k % 5 == 4
        evs.append(await event(db, org, ch, base + timedelta(minutes=k),
                               direction="outbound" if out else "inbound",
                               sender=None if out else (sid_lan if k % 2 == 0 else sid_nam), grp=g,
                               body=f"Tin số {k}" + (" dài" * 120 if k == 6 else "")))
        # tin của NHÓM KHÁC xen giữa — không được lẫn vào
        await event(db, org, ch, base + timedelta(minutes=k, seconds=30), grp=other_g,
                    body=f"Nhóm khác {k}")
    return {"org": org, "ch": ch, "g": g, "lan": lan, "evs": evs, "sid_lan": sid_lan, "base": base}


async def test_context_ten_messages_in_order_one_center_same_group(owner: Api, db: Any) -> None:
    org = await _org(db)
    t = await _thread(db, org)
    evs = t["evs"]
    uid = await unit(db, org, evs[7].at, "AskedPrice", "Khách hỏi giá", person=t["lan"], grp=t["g"], raw=(evs[7],))
    await db.commit()
    r = await owner.get(f"/owner/feed/{uid}/context")
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["center_event_id"] == str(evs[7].id)
    msgs = d["messages"]
    assert len(msgs) == 10 and sum(m["is_center"] for m in msgs) == 1
    assert [m["id"] for m in msgs] == [str(e.id) for e in evs[2:12]]                      # 5 trước + tâm + 4 sau
    assert msgs[5]["is_center"] and msgs[5]["text"] == "Tin số 7"
    assert [m["at"] for m in msgs] == sorted(m["at"] for m in msgs)
    assert not any("Nhóm khác" in m["text"] for m in msgs)                                 # cùng nhóm
    assert all(set(m) == {"id", "at", "direction", "sender", "text", "is_center"} for m in msgs)
    assert [m["direction"] for m in msgs] == ["inbound", "inbound", "outbound", "inbound", "inbound", "inbound",
                                              "inbound", "outbound", "inbound", "inbound"]   # tin đi: số 4 và số 9
    assert msgs[2]["sender"] == "Bên mình" and msgs[0]["sender"] == "Chị Lan" and msgs[5]["sender"] == "Anh Nam"
    assert all(len(m["text"]) <= 280 for m in msgs)


async def test_context_text_is_clipped_to_280(owner: Api, db: Any) -> None:
    org = await _org(db)
    t = await _thread(db, org)
    evs = t["evs"]
    uid = await unit(db, org, evs[7].at, "AskedPrice", raw=(evs[7],), person=t["lan"], grp=t["g"])
    await db.commit()
    msgs = (await owner.get(f"/owner/feed/{uid}/context")).json()["messages"]
    six = next(m for m in msgs if m["id"] == str(evs[6].id))
    assert len(six["text"]) == 280 and six["text"].endswith("…") and six["text"].startswith("Tin số 6 dài")


async def test_context_edges_earliest_evidence_and_missing_sources(owner: Api, db: Any) -> None:
    org = await _org(db)
    t = await _thread(db, org)
    evs = t["evs"]
    first = await unit(db, org, evs[0].at, "AskedPrice", raw=(evs[0],), person=t["lan"], grp=t["g"])
    last = await unit(db, org, evs[14].at, "AskedPrice", raw=(evs[14],), person=t["lan"], grp=t["g"])
    two = await unit(db, org, evs[9].at, "AskedPrice", raw=(evs[11], evs[9]), person=t["lan"], grp=t["g"])
    nogoc = await unit(db, org, ago(hours=1), "AskedPrice")
    al = await alert(db, org, "Cảnh báo có chứng cứ")
    dr = await draft(db, org)
    await db.commit()
    a = (await owner.get(f"/owner/feed/{first}/context")).json()
    assert [m["is_center"] for m in a["messages"]] == [True, False, False, False, False]      # không có tin trước
    b = (await owner.get(f"/owner/feed/{last}/context")).json()
    assert len(b["messages"]) == 6 and b["messages"][-1]["is_center"]                         # không có tin sau
    c = (await owner.get(f"/owner/feed/{two}/context")).json()
    assert c["center_event_id"] == str(evs[9].id)                                              # tin SỚM NHẤT làm tâm
    for item in (nogoc, al, dr):
        r = await owner.get(f"/owner/feed/{item}/context")
        assert r.status_code == 200 and r.json() == {"center_event_id": None, "messages": []}, item
    for bad in (str(uuid.uuid4()), "không-phải-uuid"):
        r = await owner.get(f"/owner/feed/{bad}/context")
        assert r.status_code == 404 and r.json()["code"] == "NOT_FOUND", bad


async def test_context_one_to_one_excludes_other_customers_inbound(owner: Api, db: Any) -> None:
    org = await _org(db)
    ch = await channel(db, org)
    lan = await _person(db, org, "PER-1", "Chị Lan")
    nam = await _person(db, org, "PER-2", "Anh Nam")
    s_lan, s_nam = await identity(db, lan, ch), await identity(db, nam, ch)
    base = ago(hours=2)
    e1 = await event(db, org, ch, base, sender=s_lan, body="Lan: chào em")
    await event(db, org, ch, base + timedelta(minutes=1), sender=s_nam, body="Nam: nhắn riêng việc khác")
    await event(db, org, ch, base + timedelta(minutes=2), sender=s_lan, body="Lan: báo giá MDF?")
    await event(db, org, ch, base + timedelta(minutes=3), direction="outbound", body="Em gửi báo giá ạ")
    uid = await unit(db, org, base, "AskedPrice", raw=(e1,), person=lan)
    await db.commit()
    texts = [m["text"] for m in (await owner.get(f"/owner/feed/{uid}/context")).json()["messages"]]
    assert texts == ["Lan: chào em", "Lan: báo giá MDF?", "Em gửi báo giá ạ"]


async def test_context_one_to_one_outbound_center_keeps_only_that_customers_thread(owner: Api, db: Any) -> None:
    """F-60-R1: tin chính là tin ĐI (SentQuotation) — vẫn không lẫn tin của khách khác trên cùng kênh."""
    org = await _org(db)
    ch = await channel(db, org)
    lan = await _person(db, org, "PER-1", "Chị Lan")
    nam = await _person(db, org, "PER-2", "Anh Nam")
    s_lan, s_nam = await identity(db, lan, ch, "zl-lan"), await identity(db, nam, ch, "zl-nam")
    base = ago(hours=2)
    await event(db, org, ch, base, sender=s_lan, body="Lan: cho xin báo giá")
    await event(db, org, ch, base + timedelta(minutes=1), sender=s_nam, body="Nam: hỏi việc khác")
    out_lan = await event(db, org, ch, base + timedelta(minutes=2), direction="outbound", body="Gửi Lan báo giá",
                          payload='{"threadId": "zl-lan"}')
    await event(db, org, ch, base + timedelta(minutes=3), direction="outbound", body="Gửi Nam hàng mẫu",
                payload='{"threadId": "zl-nam"}')
    await event(db, org, ch, base + timedelta(minutes=4), sender=s_nam, body="Nam: cảm ơn")
    await event(db, org, ch, base + timedelta(minutes=5), direction="outbound", body="Trả lời Nam (không rõ)")
    await event(db, org, ch, base + timedelta(minutes=6), sender=s_lan, body="Lan: ok em")
    await event(db, org, ch, base + timedelta(minutes=7), direction="outbound", body="Dạ chị Lan (không rõ)")
    uid = await unit(db, org, base, "SentQuotation", raw=(out_lan,), person=lan)
    await db.commit()
    msgs = (await owner.get(f"/owner/feed/{uid}/context")).json()["messages"]
    assert [m["text"] for m in msgs] == [
        "Lan: cho xin báo giá", "Gửi Lan báo giá", "Lan: ok em", "Dạ chị Lan (không rõ)"]
    assert [m["is_center"] for m in msgs] == [False, True, False, False]


async def test_context_does_not_log_message_bodies(owner: Api, db: Any, caplog: pytest.LogCaptureFixture) -> None:
    org = await _org(db)
    t = await _thread(db, org)
    evs = t["evs"]
    uid = await unit(db, org, evs[7].at, "AskedPrice", raw=(evs[7],), person=t["lan"], grp=t["g"])
    await db.commit()
    with caplog.at_level("DEBUG"):
        assert (await owner.get(f"/owner/feed/{uid}/context")).status_code == 200
    assert "Tin số 7" not in caplog.text
