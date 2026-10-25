# ruff: noqa: E501
"""MỘT cổng đọc tín hiệu/ask cho MỌI đường (bài học L-90: đường anh em phải cùng cổng) — `/signals`, `/asks`, hàng chung `/inbox`
(đọc + `/act`), `/explain/alert`, mặt tiền Owner.

- `privacy='console_pin'` (đánh giá người/nhân sự): chỉ Owner có phiên PIN còn hiệu lực. Vai khác ⇒ như không tồn tại (404); Owner
  chưa mở PIN ⇒ 423 khi đọc/ghi đích danh, danh sách ẩn và báo số ẩn.
- Nhóm «Chỉ Sếp»/«Sếp + quản lý» (`core.groups.view_scope`): tín hiệu có `group_id`, `subject_type='group'`, hoặc bằng chứng
  (meaning_unit / raw / raw_event / mọi phần tử mang `raw_event_id`) thuộc nhóm vai đó không được xem ⇒ ẩn (404).
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gh.auth import rbac, service
from gh.biz.core.scope import group_view_sql
from gh.errors import not_found, pin_required

_UUID_RE = "^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
#: ask nói về NGƯỜI (đề xuất Hồ sơ Sếp, câu hỏi gộp danh tính) — cùng cổng PIN với console_pin.
PEOPLE_ASK_SOURCES = ("profile", "identity")


def can_see_private(user: service.CurrentUser) -> bool:
    return user.role_code == rbac.OWNER and user.pin_active()


def privacy_sql(user: service.CurrentUser, col: str) -> str:
    """Điều kiện SQL "dòng có `privacy` ở cột `col` được người này thấy"."""
    return "TRUE" if can_see_private(user) else f"COALESCE({col}, 'chat_ok') = 'chat_ok'"


def evidence_hidden_sql(role_code: str, evidence_col: str) -> str:
    """Bằng chứng (jsonb mảng) có phần tử thuộc nhóm vai `role_code` không được xem. Hiểu mọi loại: `meaning_unit`, `raw` (cũ),
    `raw_event` (đường nhanh T1), và mọi phần tử mang `raw_event_id` (fact / meaning_unit của T1)."""
    if group_view_sql(role_code, "x") == "TRUE":
        return "FALSE"
    uid = f"CASE WHEN ev->>'id' ~ '{_UUID_RE}' THEN (ev->>'id')::uuid END"
    rid = f"CASE WHEN ev->>'raw_event_id' ~ '{_UUID_RE}' THEN (ev->>'raw_event_id')::uuid END"
    return (f"EXISTS (SELECT 1 FROM jsonb_array_elements(CASE WHEN jsonb_typeof({evidence_col}) = 'array' THEN {evidence_col} "
            f"ELSE '[]'::jsonb END) ev WHERE "
            f"(ev->>'type' = 'meaning_unit' AND EXISTS (SELECT 1 FROM clean.meaning_units hu WHERE hu.id = {uid}"
            f" AND hu.group_id IS NOT NULL AND NOT ({group_view_sql(role_code, 'hu.group_id')})))"
            f" OR (ev->>'type' IN ('raw', 'raw_event') AND EXISTS (SELECT 1 FROM raw.events hr WHERE hr.id = {uid}"
            f" AND hr.group_id IS NOT NULL AND NOT ({group_view_sql(role_code, 'hr.group_id')})))"
            f" OR (ev ? 'raw_event_id' AND EXISTS (SELECT 1 FROM raw.events hr2 WHERE hr2.id = {rid}"
            f" AND hr2.group_id IS NOT NULL AND NOT ({group_view_sql(role_code, 'hr2.group_id')}))))")


def alert_hidden_group_sql(role_code: str, alias: str) -> str:
    """Dòng `biz.alerts` bí danh `alias` thuộc nhóm vai này không được xem (cột group_id, subject nhóm, hoặc bằng chứng)."""
    if group_view_sql(role_code, "x") == "TRUE":
        return "FALSE"
    return (f"(({alias}.group_id IS NOT NULL AND NOT ({group_view_sql(role_code, f'{alias}.group_id')}))"
            f" OR ({alias}.subject_type = 'group' AND {alias}.subject_id IS NOT NULL AND NOT ({group_view_sql(role_code, f'{alias}.subject_id')}))"
            f" OR {evidence_hidden_sql(role_code, f'{alias}.evidence')})")


def alert_visible_sql(user: service.CurrentUser, alias: str) -> str:
    return f"({privacy_sql(user, f'{alias}.privacy')} AND NOT {alert_hidden_group_sql(user.role_code, alias)})"


async def require_alert(db: AsyncSession, user: service.CurrentUser, alert_id: uuid.UUID) -> Any | None:
    """Cổng đọc/ghi ĐÍCH DANH một dòng `biz.alerts`. Không phải alert của tổ chức này ⇒ trả None (bên gọi tự xử lý).
    console_pin: vai khác ⇒ 404, Owner chưa PIN ⇒ 423. Nhóm ẩn ⇒ 404."""
    r = (await db.execute(text(f"""SELECT a.id, a.privacy, {alert_hidden_group_sql(user.role_code, 'a')} AS hidden
                                   FROM biz.alerts a WHERE a.id = :i AND a.org_id = :o"""),  # noqa: S608
                          {"i": alert_id, "o": user.org_id})).one_or_none()
    if r is None:
        return None
    if r.privacy == "console_pin" and not can_see_private(user):
        if user.role_code == rbac.OWNER:
            raise pin_required()
        raise not_found("Tín hiệu")
    if r.hidden:
        raise not_found("Tín hiệu")
    return r


def ask_visible_sql(user: service.CurrentUser, ask_alias: str, alert_alias: str) -> str:
    """Ask thấy được: không gắn tín hiệu console_pin / nhóm ẩn, và (ask nói về người) chỉ khi có PIN."""
    if can_see_private(user):
        return f"(({alert_alias}.id IS NULL) OR NOT {alert_hidden_group_sql(user.role_code, alert_alias)})"
    people = ", ".join(f"'{s}'" for s in PEOPLE_ASK_SOURCES)
    return (f"({ask_alias}.source NOT IN ({people}) AND ({alert_alias}.id IS NULL OR "
            f"(COALESCE({alert_alias}.privacy, 'chat_ok') = 'chat_ok' AND NOT {alert_hidden_group_sql(user.role_code, alert_alias)})))")
