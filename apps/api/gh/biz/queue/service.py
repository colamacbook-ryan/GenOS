"""Nền dùng chung của cụm Hàng đợi & Hành động: hình dạng, phạm vi, phân loại tab, im lặng có chủ đích.

`biz.inbox_items` (view, `db/sql/0005_p3_queue.sql`) hợp nhất đơn vị ý nghĩa mới + cảnh báo đang mở + bản nháp
chờ duyệt. Việc (`biz.tasks`) có màn riêng nên không nằm trong view này; khối "Đến hạn" của Tổng quan được ghép
thêm bằng một truy vấn riêng (`routes.py: _queue_widget`).
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gh.auth.service import CurrentUser
from gh.biz.core.scope import Scope, direct_visible_sql, group_view_sql
from gh.data.common import iso


def when(value: Any) -> datetime | None:
    """asyncpg đòi kiểu Python gốc cho tham số so sánh/ép `timestamptz` — không nhận chuỗi ISO trực tiếp
    (`CAST(:x AS timestamptz)` với `:x` là `str` làm asyncpg suy ra kiểu tham số là `timestamptz` và từ chối
    chuỗi ở bước bind, khác hẳn cách driver `psycopg` vẫn chấp nhận)."""
    if value is None or isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))

# Nhãn tiếng Việt cho event_type (spec G3) — tiêu đề thẻ ở Hộp thư / Tổng quan / Mặt tiền. v0.1.69 (F062): ĐỦ 15 loại và
# MỘT bảng duy nhất — bằng `core.lookup` (kind = 'event_type', db/sql/0003_phase2.sql) và bằng `packages/contracts/src/
# eventTypes.ts` của web (tests/test_event_labels_v016x.py giữ ba nơi khớp nhau, cùng thứ tự `sort_order`).
EVENT_LABELS = {
    "AskedPrice": "Hỏi giá / nhu cầu mua", "OfferedSupply": "Chào nguồn cung",
    "RequestedPartnership": "Đề nghị hợp tác", "Complained": "Phàn nàn", "PromisedDelivery": "Hứa giao hàng",
    "ScheduledMeeting": "Hẹn gặp", "SentQuotation": "Gửi báo giá", "SentDocument": "Gửi tài liệu",
    "MentionsCompetitor": "Nhắc đối thủ", "ComparedVendor": "So sánh nhà cung cấp", "RequestedSample": "Xin mẫu",
    "AskedStatus": "Hỏi tiến độ", "JobSignal": "Tín hiệu tìm việc", "DealWon": "Chốt đơn", "WentSilent": "Im lặng",
}
ALERT_TYPE_LABELS = {
    "customer_cooling": "Khách đang lạnh / sắp mất", "slow_response": "Phản hồi chậm bất thường",
    "repeated_complaint": "Than phiền lặp lại", "unclaimed_opportunity": "Cơ hội nóng chưa ai nhận",
    "competitor": "Đối thủ xuất hiện", "forgotten_deadline": "Deadline bị bỏ quên",
    "data_conflict": "Dữ liệu mâu thuẫn", "model_quota_low": "Hạn mức model sắp hết",
    "model_chain_exhausted": "Hết chuỗi model",
    # v0.1.62: không còn sinh cảnh báo này (luật "agy chỉ cho Gen" đã gỡ); giữ nhãn để các
    # dòng cũ (≤ v0.1.58) vẫn hiện đúng tên.
    "model_chain_agy_only": "Chưa có nguồn AI phù hợp",
}
#: `biz.promises` (lời hứa trong hội thoại) CHƯA có bộ nào ghi vào — không việc nền nào trích lời hứa ra khỏi tin nhắn
#: (v0.1.71, giả lập toàn luồng P3-4; Nợ #62). Chưa theo dõi thì các câu nhận xét của Chăm sóc khách không được
#: nói «không có lời hứa bị vỡ» như thể đã theo dõi (cảnh báo `forgotten_deadline` tự trống: không có dòng nào để quét).
#: Bật lại cùng cờ `PROMISES_TRACKED` của web (`apps/web/src/lib/features.ts`).
PROMISES_TRACKED = False

# Đơn vị ý nghĩa nào rơi vào tab nào của Hộp thư (mục "Chỗ tự quyết" trong docs/api/phase-3-queue.md).
OPPORTUNITY_EVENTS = frozenset({"AskedPrice", "OfferedSupply", "RequestedPartnership"})
REPLY_EVENTS = frozenset({"Complained", "ScheduledMeeting", "SentQuotation", "PromisedDelivery"})
TABS = ("all", "opportunity", "alert", "approval", "reply", "candidate")
ITEM_TYPES = ("unit", "alert", "draft")


_UUID_RE = "^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"


def alert_in_hidden_group_sql(role_code: str, alias: str) -> str:
    """Cảnh báo có chứng cứ (đơn vị ý nghĩa / tin thô) thuộc một nhóm mà `role_code` không được xem. Cảnh báo gắn vào
    NGƯỜI (đối thủ xuất hiện, phản hồi chậm…) vẫn mang kết luận / tên khách trích từ tin trong nhóm «Chỉ Sếp»."""
    uid = f"CASE WHEN ev->>'id' ~ '{_UUID_RE}' THEN (ev->>'id')::uuid END"
    return (
        "EXISTS (SELECT 1 FROM biz.alerts hal CROSS JOIN LATERAL jsonb_array_elements("
        "CASE WHEN jsonb_typeof(hal.evidence) = 'array' THEN hal.evidence ELSE '[]'::jsonb END) ev"
        f" WHERE hal.id = {alias}.item_id AND ("
        f"(ev->>'type' = 'meaning_unit' AND EXISTS (SELECT 1 FROM clean.meaning_units hu WHERE hu.id = {uid}"
        f" AND hu.group_id IS NOT NULL AND NOT ({group_view_sql(role_code, 'hu.group_id')})))"
        f" OR (ev->>'type' = 'raw' AND EXISTS (SELECT 1 FROM raw.events hr WHERE hr.id = {uid}"
        f" AND hr.group_id IS NOT NULL AND NOT ({group_view_sql(role_code, 'hr.group_id')})))))")


def item_scope_sql(sc: Scope, alias: str, user: CurrentUser) -> tuple[str, dict[str, Any]]:
    """Một dòng `biz.inbox_items` thấy được khi đối tượng nó gắn vào trong phạm vi, hoặc chính item được giao
    thẳng cho mình / team mình (`core.assignments(subject_type='queue')`, dùng khi giao mà không cần sở hữu
    người/nhóm — ví dụ Operator được giao xử lý một cảnh báo cụ thể).

    Thêm (review v0.1.65 P2-2): mục `unit` sinh từ tin 1-1 (không nhóm) chỉ Owner hoặc người được giao đúng người đó
    xem — kể cả phạm vi `all` của Vận hành/Kiểm soát (xem `gh.biz.core.scope.direct_visible_sql`)."""
    dm, dp = direct_visible_sql(user, f"{alias}.group_id", f"{alias}.person_id")
    dm_cond = "TRUE" if dm == "TRUE" else f"({alias}.item_type <> 'unit' OR {dm})"
    # «Quyền xem» của nhóm (v0.1.71): MỌI loại mục gắn với một nhóm (tin, cảnh báo, bản nháp) — nhóm «Chỉ Sếp» ẩn với
    # vai khác, kể cả khi mục được giao thẳng cho họ.
    gv = group_view_sql(user.role_code, f"{alias}.group_id")
    sv = group_view_sql(user.role_code, f"{alias}.subject_id")
    if gv != "TRUE":
        dm_cond = (f"({dm_cond} AND ({alias}.group_id IS NULL OR {gv})"
                   f" AND ({alias}.subject_type IS DISTINCT FROM 'group' OR {sv})"
                   f" AND ({alias}.item_type <> 'alert' OR NOT {alert_in_hidden_group_sql(user.role_code, alias)}))")
    if sc.is_all:
        return dm_cond, dp
    sw, sp = sc.subject_sql(f"{alias}.subject_type", f"{alias}.subject_id")
    q = (f"EXISTS (SELECT 1 FROM core.assignments qa WHERE qa.subject_type = 'queue' "
         f"AND qa.subject_id = {alias}.item_id AND qa.active_to IS NULL AND qa.user_id = ANY(:scope_users))")
    return f"(({sw} OR {q}) AND {dm_cond})", {**sp, **dp}


def not_silenced_sql(alias: str = "i", *, item_type: str | None = None, id_col: str = "item_id") -> str:
    """Điều kiện "chưa bị Tắt tiếng". Mặc định đọc `{alias}.item_type`/`{alias}.item_id` (view `biz.inbox_items`);
    bảng gốc thì đặt `item_type` (hằng chữ của mình, ví dụ 'draft') và `id_col` (ví dụ 'id')."""
    kind = f"'{item_type}'" if item_type else f"{alias}.item_type"
    return (f"NOT EXISTS (SELECT 1 FROM biz.queue_silences qs WHERE qs.item_type = {kind} "
            f"AND qs.item_id = {alias}.{id_col} AND (qs.until IS NULL OR qs.until > now()))")


def tab_of(item_type: str, event_type: str | None, person_type: str | None) -> str:
    if item_type == "alert":
        return "alert"
    if item_type == "draft":
        return "approval"
    if person_type == "candidate":
        return "candidate"
    if event_type in OPPORTUNITY_EVENTS:
        return "opportunity"
    if event_type in REPLY_EVENTS:
        return "reply"
    return "other"


def person_ref(r: Any, prefix: str = "p") -> dict[str, Any] | None:
    pid = getattr(r, f"{prefix}_id", None)
    if pid is None:
        return None
    return {"id": str(pid), "code": getattr(r, f"{prefix}_code", None), "name": getattr(r, f"{prefix}_name", None),
            "type": getattr(r, f"{prefix}_type", None), "org_name": getattr(r, f"{prefix}_org", None)}


def group_ref(r: Any, prefix: str = "g") -> dict[str, Any] | None:
    gid = getattr(r, f"{prefix}_id", None)
    if gid is None:
        return None
    return {"id": str(gid), "code": getattr(r, f"{prefix}_code", None), "name": getattr(r, f"{prefix}_name", None),
            "channel": getattr(r, f"{prefix}_channel", None)}


def subject_ref(r: Any) -> dict[str, Any] | None:
    return person_ref(r, "p") or group_ref(r, "g")


CONFIDENCE_BAND_HIGH, CONFIDENCE_BAND_MED = 0.8, 0.6


def confidence_band(v: float | None) -> str | None:
    if v is None:
        return None
    if v >= CONFIDENCE_BAND_HIGH:
        return "cao"
    if v >= CONFIDENCE_BAND_MED:
        return "trung bình"
    return "thấp"


async def silence(db: AsyncSession, org_id: uuid.UUID, item_type: str, item_id: uuid.UUID, *, user_id: uuid.UUID,
                  reason: str | None, until: str | None) -> None:
    await db.execute(text("""
        INSERT INTO biz.queue_silences (org_id, item_type, item_id, reason, until, silenced_by)
        VALUES (:o, :t, :i, :r, :u, :by)
        ON CONFLICT (item_type, item_id) DO UPDATE
          SET reason = EXCLUDED.reason, until = EXCLUDED.until, silenced_by = EXCLUDED.silenced_by,
              silenced_at = now()"""),
        {"o": org_id, "t": item_type, "i": item_id, "r": reason, "u": when(until), "by": user_id})


async def assign(db: AsyncSession, org_id: uuid.UUID, item_type: str, item_id: uuid.UUID, *, to_user: uuid.UUID,
                 by_user: uuid.UUID) -> None:
    await db.execute(text("""UPDATE core.assignments SET active_to = now()
                             WHERE subject_type = 'queue' AND subject_id = :i AND active_to IS NULL"""),
                     {"i": item_id})
    await db.execute(text("""INSERT INTO core.assignments (org_id, user_id, subject_type, subject_id, created_by)
                             VALUES (:o, :u, 'queue', :i, :by)"""),
                     {"o": org_id, "u": to_user, "i": item_id, "by": by_user})
    _ = item_type  # giữ tham số cho chữ ký đối xứng với silence(); id đã đủ duy nhất toàn hệ thống


def iso_or_none(v: Any) -> str | None:
    return iso(v) if hasattr(v, "astimezone") else v
