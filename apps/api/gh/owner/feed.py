"""Mặt tiền Owner v2 (v0.1.59, G1) — dòng tin "Thông tin": `GET /owner/feed` và `GET /owner/feed/{id}/context`.

CHỈ ĐỌC, không gọi model, không ghi gì.

Nguồn: `biz.inbox_items` (view, db/sql/0005_p3_queue.sql) — `item_type` unit | alert | draft. Mức quan trọng
(`importance`) do chính tệp này tính, KHÔNG dùng cột `priority` của view (P2 ở đó = Gen chắc chắn ≥ 0.8, không
phải "việc quan trọng"):

- Khẩn: unit `Complained` CHƯA được xử lý, hoặc cảnh báo đang mở mức P1. Unit "đã được xử lý" = đã có bản nháp trả lời
  (tạo từ chính tin này: `sources[].ref = {type: meaning_unit, id}`) ở trạng thái đã duyệt / đã sửa-duyệt / đã gửi —
  rơi về mức theo các luật dưới (thường là Thường). Nháp còn chờ duyệt thì tin vẫn Khẩn (nháp nằm ở "Chờ duyệt").
- Quan trọng: unit có `event_type` thuộc `IMPORTANT_EVENT_TYPES`, hoặc người gửi có độ nóng ≥ `HOT_MIN`, hoặc nháp
  chờ duyệt, hoặc cảnh báo đang mở khác P1.
- Thường: còn lại (câu chào, tin xã giao… dù Gen chắc chắn). Loại lạ cũng rơi vào đây — không mất tin, vẫn có ở
  "Tất cả".

Cửa sổ 7 ngày CHỈ áp cho unit; cảnh báo đang mở và nháp chờ duyệt lấy hết (số "Chờ duyệt" khớp màn Việc). Ẩn rác /
trùng / điểm thấp theo `refinery.item_marks` (cùng điều kiện lọc đầu mà Hộp thư dùng để ẩn — chỉ khi tổ chức bật lọc
đầu), ẩn mục đã "tắt tiếng", bỏ tin của kênh Facebook (Facebook chỉ ở mục riêng của Tình báo).

`urgent_open` (số tin Khẩn đang mở) lấy từ CÙNG câu truy vấn nên `today.nav.urgent_open == feed.counts.urgent_open`.
"""

import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gh.biz.core.scope import not_found
from gh.biz.queue import service as qsvc
from gh.data.common import RAW_SELECT, iso, outbound_to_sql, raw_item
from gh.owner import service as svc
from gh.refinery import triage

VIEWS = ("important", "all", "opportunity", "alert", "approval")
DEFAULT_LIMIT = 30
MAX_LIMIT = 50
#: Cửa sổ thời gian của unit (ngày). Cảnh báo mở / nháp chờ duyệt không bị giới hạn tuổi.
UNIT_WINDOW_DAYS = 7
#: Chặn `offset` (phân trang không bao giờ cần xa hơn; tránh số vượt bigint → 500).
MAX_OFFSET = 100_000
SUMMARY_MAX = 240
#: Hội thoại quanh tin: 5 tin trước + tin chính + 4 tin sau (≤ 10), mỗi tin cắt 280 ký tự.
CONTEXT_BEFORE = 5
CONTEXT_AFTER = 4
TEXT_MAX = 280

#: Loại việc làm một unit thành "Quan trọng" (một chỗ duy nhất). Loại lạ ⇒ "Thường".
IMPORTANT_EVENT_TYPES = frozenset({
    "AskedPrice", "AskedStatus", "OfferedSupply", "SentQuotation", "RequestedSample", "RequestedPartnership",
    "PromisedDelivery", "MentionsCompetitor", "DealWon"})
#: Nhóm "Cơ hội" của bộ lọc. v0.1.71 (P3-8): MỘT bảng với tab «Cơ hội» của Hộp thư (`gh.biz.queue.service`) — trước đây
#: Mặt tiền tính cả xin mẫu / báo giá / chốt đơn nên cùng một chữ «Cơ hội» ra hai con số khác nhau.
OPPORTUNITY_EVENT_TYPES = frozenset(qsvc.OPPORTUNITY_EVENTS)
URGENT_EVENT_TYPE = "Complained"
#: Trạng thái nháp trả lời coi như tin đã được xử lý (nháp chờ duyệt / bị từ chối / lỗi thì chưa).
HANDLED_DRAFT_STATUSES = ("approved", "edited", "sent")

IMPORTANCE_LABELS = ("Khẩn", "Quan trọng", "Thường")
#: Nhãn việc (tiêu đề dòng tin) theo event_type — tiếng Việt đời thường, không lộ tên kỹ thuật.
# v0.1.69 (F062): KHÔNG giữ nhãn riêng — cùng bảng với Hộp thư (một loại sự kiện, một cách gọi trên mọi màn).
EVENT_TITLES: dict[str, str] = dict(qsvc.EVENT_LABELS)
OTHER_TITLE = "Tin khác"

_KIND_TEXT = {"image": "(Hình ảnh)", "file": "(Tệp đính kèm)", "sticker": "(Nhãn dán)", "reaction": "(Biểu cảm)"}

#: Mức xếp hạng 0 Khẩn · 1 Quan trọng · 2 Thường — `:imp` = IMPORTANT_EVENT_TYPES, `:hot` = svc.HOT_MIN.
_RANK_SQL = """CASE
      WHEN i.item_type = 'unit' AND i.title = :urgent AND hu.uid IS NULL THEN 0
      WHEN i.item_type = 'alert' AND i.priority = 'P1' THEN 0
      WHEN i.item_type = 'unit' AND i.title = ANY(CAST(:imp AS text[])) THEN 1
      WHEN i.item_type = 'unit' AND COALESCE(hs.value, 0) >= :hot THEN 1
      WHEN i.item_type IN ('draft', 'alert') THEN 1
      ELSE 2 END"""

_VIEW_WHERE = {
    "important": "rnk <= 1",
    "all": "TRUE",
    "opportunity": "item_type = 'unit' AND raw_title = ANY(CAST(:opp AS text[]))",
    "alert": "item_type = 'alert'",
    "approval": "item_type = 'draft'",
}


def clamp_limit(limit: int | None) -> int:
    return DEFAULT_LIMIT if limit is None else max(1, min(int(limit), MAX_LIMIT))


def _clip(v: Any, n: int) -> str:
    s = " ".join(str(v or "").split())
    return s if len(s) <= n else s[:n - 1] + "…"


async def _items_cte(db: AsyncSession, org_id: uuid.UUID) -> tuple[str, dict[str, Any]]:
    """Câu `WITH items AS (…)` dùng chung cho danh sách, số đếm và `urgent_open` (một nguồn ⇒ số luôn khớp)."""
    cfg = await triage.get_settings(db, org_id)
    junk = ""
    params: dict[str, Any] = {"o": org_id, "urgent": URGENT_EVENT_TYPE, "imp": sorted(IMPORTANT_EVENT_TYPES),
                              "hot": svc.HOT_MIN, "opp": sorted(OPPORTUNITY_EVENT_TYPES), "days": UNIT_WINDOW_DAYS,
                              "handled": list(HANDLED_DRAFT_STATUSES)}
    if cfg["enabled"]:
        # Cùng điều kiện "ẩn rác & trùng" của Hộp thư (gh.biz.queue.routes.JUNK_SQL).
        junk = "AND NOT COALESCE(m.is_spam OR m.duplicate_of IS NOT NULL OR m.quality < :minq, false)"
        params["minq"] = int(cfg["min_score"])
    sql = f"""
WITH handled AS (
  -- tin đã có nháp trả lời được duyệt/gửi (nháp tạo từ POST /inbox/{{id}}/act ghi nguồn meaning_unit); chỉ mục
  -- (org_id, status, created_at DESC); nháp luôn sinh SAU tin nên cùng cửa sổ UNIT_WINDOW_DAYS.
  SELECT DISTINCT src->'ref'->>'id' AS uid
  FROM biz.action_drafts d
  CROSS JOIN LATERAL jsonb_array_elements(
    CASE WHEN jsonb_typeof(d.sources) = 'array' THEN d.sources ELSE '[]'::jsonb END) src
  WHERE d.org_id = :o AND d.status = ANY(CAST(:handled AS text[]))
    AND d.created_at > now() - make_interval(days => :days) AND src->'ref'->>'type' = 'meaning_unit'
), items AS (
  SELECT i.item_type, i.item_id AS id, i.title AS raw_title, i.summary, i.created_at, i.person_id, i.group_id,
         p.display_name AS p_name, g.name AS g_name, gc.type AS g_channel,
         d.title AS d_title, d.kind AS d_kind, a.suggested_action,
         {_RANK_SQL} AS rnk
  FROM biz.inbox_items i
  LEFT JOIN core.persons p ON p.id = i.person_id
  LEFT JOIN core.groups g ON g.id = i.group_id
  LEFT JOIN core.channels gc ON gc.id = g.channel_id
  LEFT JOIN clean.current_scores hs ON i.item_type = 'unit' AND hs.subject_type = 'person'
                                   AND hs.subject_id = i.person_id AND hs.dimension = 'heat'
  LEFT JOIN handled hu ON i.item_type = 'unit' AND hu.uid = CAST(i.item_id AS text)
  LEFT JOIN biz.action_drafts d ON i.item_type = 'draft' AND d.id = i.item_id
  LEFT JOIN biz.alerts a ON i.item_type = 'alert' AND a.id = i.item_id
  LEFT JOIN refinery.item_marks m ON m.item_type = i.item_type AND m.item_id = i.item_id
  WHERE i.org_id = :o
    AND (i.item_type <> 'unit' OR i.created_at > now() - make_interval(days => :days))
    AND {qsvc.not_silenced_sql("i")}
    AND COALESCE(gc.type, '') <> 'facebook'
    {junk}
)"""
    return sql, params


async def counts(db: AsyncSession, org_id: uuid.UUID) -> dict[str, int]:
    cte, params = await _items_cte(db, org_id)
    r = (await db.execute(text(cte + """
        SELECT count(*) FILTER (WHERE rnk <= 1) AS important, count(*) AS total,
               count(*) FILTER (WHERE item_type = 'unit' AND raw_title = ANY(CAST(:opp AS text[]))) AS opportunity,
               count(*) FILTER (WHERE item_type = 'alert') AS alert,
               count(*) FILTER (WHERE item_type = 'draft') AS approval,
               count(*) FILTER (WHERE rnk = 0) AS urgent_open
        FROM items"""), params)).one()
    return {"important": int(r.important), "all": int(r.total), "opportunity": int(r.opportunity),
            "alert": int(r.alert), "approval": int(r.approval), "urgent_open": int(r.urgent_open)}


async def urgent_open(db: AsyncSession, org_id: uuid.UUID) -> int:
    """Số tin Khẩn đang mở (chưa xử lý) — cùng CTE với `counts` ⇒ `today.nav.urgent_open == feed.counts.urgent_open`."""
    cte, params = await _items_cte(db, org_id)
    n = (await db.execute(text(cte + " SELECT count(*) FROM items WHERE rnk = 0"), params)).scalar_one()
    return int(n or 0)


def _title(r: Any) -> str:
    if r.item_type == "unit":
        return EVENT_TITLES.get(r.raw_title, OTHER_TITLE)
    if r.item_type == "draft":
        return _clip(r.d_title, 120) or svc.DRAFT_LABELS.get(r.d_kind, "Bản nháp chờ duyệt")
    return _clip(r.raw_title, 120) or "Cảnh báo"


def _feed_item(r: Any) -> dict[str, Any]:
    return {
        "id": str(r.id), "item_type": r.item_type, "title": _title(r), "summary": _clip(r.summary, SUMMARY_MAX),
        "importance": IMPORTANCE_LABELS[int(r.rnk)],
        "event_type": r.raw_title if r.item_type == "unit" else None,
        "created_at": iso(r.created_at),
        "person": {"id": str(r.person_id), "name": _clip(r.p_name, 80) or "Chưa có tên"} if r.person_id else None,
        "group": ({"id": str(r.group_id), "name": _clip(r.g_name, 80) or "Nhóm", "channel": r.g_channel}
                  if r.group_id else None),
        "suggested_action": (_clip(r.suggested_action, SUMMARY_MAX) or None) if r.item_type == "alert" else None,
    }


async def feed(db: AsyncSession, org_id: uuid.UUID, view: str, offset: int, limit: int) -> dict[str, Any]:
    n = clamp_limit(limit)
    off = max(0, min(int(offset), MAX_OFFSET))      # quá cỡ bigint sẽ làm Postgres lỗi ⇒ chặn trước
    cte, params = await _items_cte(db, org_id)
    rows = (await db.execute(text(cte + f"""
        SELECT * FROM items WHERE {_VIEW_WHERE[view]}
        ORDER BY rnk, created_at DESC, id LIMIT :n OFFSET :off"""),  # noqa: S608
                             {**params, "n": n + 1, "off": off})).all()
    fv = await svc.filter_value(db, org_id)
    return {
        "items": [_feed_item(r) for r in rows[:n]],
        "next_offset": off + n if len(rows) > n else None,
        "counts": await counts(db, org_id),
        "filtered_7d": {"filtered": fv["filtered"], "spam_blocked": fv["spam_blocked"]},
        "channels_connected": await svc.channels_connected(db, org_id),
    }


# ─── GET /owner/feed/{id}/context ────────────────────────────────────────────────────────────────────────────────

def _message(r: Any, center_id: uuid.UUID) -> dict[str, Any]:
    it = raw_item(r)
    outbound = it["direction"] == "outbound"
    who = it["person"] or {}
    body = _clip(it["text"], TEXT_MAX) or _KIND_TEXT.get(str(it["kind"]), "")
    return {"id": it["id"], "at": it["occurred_at"], "direction": "outbound" if outbound else "inbound",
            "sender": _clip(who.get("name"), 80) or ("Bên mình" if outbound else "Khách"),
            "text": body, "is_center": r.id == center_id}


#: Tin 1-1 chỉ thuộc cuộc trò chuyện với ĐÚNG người của đơn vị ý nghĩa (v0.1.60, F-60-R1), bất kể tin chính đến hay đi:
#: tin ĐẾN = của người đó; tin ĐI = có người nhận trong payload (Zalo `threadId`, WhatsApp `key.remoteJid`, hoặc
#: `recipient`) trùng định danh của họ; tin đi không ghi người nhận ⇒ chỉ lấy khi tin ĐẾN 1-1 gần nhất trước nó
#: (trong 7 ngày, cùng kênh) là của người đó. Khách khác trên cùng kênh không bao giờ lẫn vào.


# `outbound_to_sql` đã chuyển sang `gh.data.common` (v0.1.67, F050) để cảnh báo / chăm sóc khách / sàng lọc dùng chung;
# `feed.outbound_to_sql` vẫn gọi được (Phân tích `insights` dùng tên này).


_ONE_TO_ONE_SQL = f"""AND (
  (e.direction = 'inbound' AND e.sender_identity_id = ANY(CAST(:ids AS uuid[])))
  OR {outbound_to_sql("e", "CAST(:ids AS uuid[])", "CAST(:ext AS text[])")}
)"""


async def _person_keys(db: AsyncSession, org_id: uuid.UUID, unit_id: uuid.UUID, observed_at: Any,
                       c: Any) -> dict[str, Any]:
    """Định danh (id + mã ngoài đã bỏ đuôi `@…`) của người sở hữu đơn vị ý nghĩa, kể cả hồ sơ đã gộp vào họ. Đơn vị
    không gắn người mà tin chính là tin ĐẾN ⇒ dùng chính người gửi tin chính."""
    rows = (await db.execute(text("""
        SELECT pi.id, split_part(pi.external_id, '@', 1) AS ext
        FROM clean.meaning_units mu
        JOIN core.person_identities pi ON pi.channel_id = :ch
         AND (pi.person_id = mu.person_id
              OR pi.person_id IN (SELECT q.id FROM core.persons q WHERE q.merged_into_id = mu.person_id))
        WHERE mu.id = :i AND mu.observed_at = :obs AND mu.org_id = :o"""),
                             {"i": unit_id, "obs": observed_at, "o": org_id, "ch": c.channel_id})).all()
    ids = {r.id for r in rows}
    ext = {r.ext for r in rows if r.ext}
    if not ids and c.direction == "inbound" and c.sender_identity_id is not None:
        ids.add(c.sender_identity_id)
        ext = {x for x in (await db.execute(text(
            "SELECT split_part(external_id, '@', 1) FROM core.person_identities WHERE id = :i"),
            {"i": c.sender_identity_id})).scalars().all() if x}
    return {"ids": sorted(ids, key=str), "ext": sorted(ext)}


async def context(db: AsyncSession, org_id: uuid.UUID, item_id: str) -> dict[str, Any]:
    """Hội thoại quanh một tin (CHỈ unit có bản ghi thô gốc): 5 tin trước + tin chính + 4 tin sau, cùng kênh và cùng
    nhóm (`group_id IS NOT DISTINCT FROM`), theo thời điểm xảy ra. Tin chính = bản ghi thô SỚM NHẤT trong chứng cứ
    (`clean.evidence`). Tin 1-1 (không nhóm): chỉ lấy tin của cuộc trò chuyện với người của đơn vị ý nghĩa, dù tin
    chính đến hay đi (xem `_ONE_TO_ONE_SQL`; cuộc 1-1 khác trên cùng kênh không lẫn vào).
    Không phải unit / không có gốc ⇒ `messages` rỗng. Id lạ ⇒ 404. Không ghi thân tin vào log."""
    empty: dict[str, Any] = {"center_event_id": None, "messages": []}
    try:
        iid = uuid.UUID(str(item_id))
    except ValueError:
        raise not_found("Tin này") from None
    item = (await db.execute(text("""
        SELECT item_type, created_at FROM biz.inbox_items WHERE org_id = :o AND item_id = :i LIMIT 1"""),
                             {"o": org_id, "i": iid})).one_or_none()
    if item is None:
        raise not_found("Tin này")
    if item.item_type != "unit":
        return empty
    c = (await db.execute(text("""
        SELECT e.id, e.received_at, e.occurred_at, e.seq, e.channel_id, e.group_id, e.sender_identity_id, e.direction
        FROM clean.evidence ev JOIN raw.events e ON e.id = ev.raw_event_id AND e.received_at = ev.raw_received_at
        WHERE ev.meaning_unit_id = :i AND ev.meaning_observed_at = :obs AND e.org_id = :o
        ORDER BY e.occurred_at, e.seq LIMIT 1"""), {"i": iid, "obs": item.created_at, "o": org_id})).one_or_none()
    if c is None:
        return empty
    center = (await db.execute(text(RAW_SELECT + " WHERE e.id = :i AND e.received_at = :r"),
                               {"i": c.id, "r": c.received_at})).one_or_none()
    if center is None:
        return empty
    thread = "WHERE e.org_id = :o AND e.channel_id = :ch AND e.group_id IS NOT DISTINCT FROM :g "
    p: dict[str, Any] = {"o": org_id, "ch": c.channel_id, "g": c.group_id, "t": c.occurred_at, "seq": c.seq}
    if c.group_id is None:
        thread += _ONE_TO_ONE_SQL
        p.update(await _person_keys(db, org_id, iid, item.created_at, c))
    before = (await db.execute(text(RAW_SELECT + thread + """
        AND (e.occurred_at, e.seq) < (:t, :seq) ORDER BY e.occurred_at DESC, e.seq DESC LIMIT :n"""),
                               {**p, "n": CONTEXT_BEFORE})).all()
    after = (await db.execute(text(RAW_SELECT + thread + """
        AND (e.occurred_at, e.seq) > (:t, :seq) ORDER BY e.occurred_at, e.seq LIMIT :n"""),
                              {**p, "n": CONTEXT_AFTER})).all()
    ordered = [*reversed(before), center, *after]
    return {"center_event_id": str(c.id), "messages": [_message(r, c.id) for r in ordered]}
