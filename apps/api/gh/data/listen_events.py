"""Sự kiện trên kênh cho "Nhật ký lắng nghe" (v0.1.68): ghi nhẹ vào `ops.listen_events`, phát WebSocket `listen.event`.

Nơi ghi (ingest.py, system_api/routes.py) chỉ gọi `record(...)` TRONG transaction đang chạy; dòng chờ phát được cất ở
`db.info["listen_pub"]`, sau commit người gọi `await publish_pending(redis, org_id, pending)` (cùng khuôn "ghi rồi mới
phát" của `raw.new`). `detail` chỉ chứa tên/số đếm cần để viết lại câu — KHÔNG chứa nội dung tin nhắn.

Câu tiếng Việt do máy chủ viết (`event_text`) để API và WebSocket cùng một lời, che số nhạy cảm theo vai người xem.
"""

import logging
import uuid
from typing import Any

import orjson
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gh import realtime
from gh.data.common import CHANNEL_NAME, iso, mask_text

log = logging.getLogger("gh.listen_events")

#: Nhãn chế độ nghe (một nguồn cho API; web nhận sẵn `listen_mode_label`). Khớp systemModel.ts / relationsModel.ts.
LISTEN_MODE_LABEL = {
    "pending": "Chờ Sếp chọn", "off": "Không nghe", "tagged_only": "Nghe hết, chỉ trả lời khi được tag",
    "silent": "Lắng nghe im lặng", "proactive": "Chủ động bắt tín hiệu", "paused": "Tạm dừng",
}
#: Chế độ tin riêng 1-1 (lưu ở raw.events.listen_mode cho tin 1-1).
DIRECT_MODE_LABEL = {"silent": "Tin riêng — lưu im lặng", "reply": "Tin riêng — trợ lý được trả lời",
                     "off": "Tin riêng — không lưu"}

#: Loại sự kiện → (icon Phosphor, tông). Web dùng đúng bảng này qua trường `icon`/`tone`.
KINDS: dict[str, tuple[str, str]] = {
    "group_new": ("ph ph-users-three", "accent"),
    "group_left": ("ph ph-sign-out", "warn"),
    "group_rejoined": ("ph ph-sign-in", "ok"),
    "group_renamed": ("ph ph-pencil-simple", "neutral"),
    "members_changed": ("ph ph-user-plus", "neutral"),
    "groups_synced": ("ph ph-address-book", "neutral"),
    "channel_connected": ("ph ph-plugs-connected", "ok"),
    "channel_error": ("ph ph-wifi-slash", "warn"),
    "channel_ended": ("ph ph-plugs", "bad"),
    "direct_new": ("ph ph-user-circle-plus", "accent"),
    # v0.1.72 (bot tài khoản riêng): ai thêm bot, bot bị mời ra, Sếp gọi bot, kênh quản trị, chế độ hỗ trợ.
    "group_added_by_other": ("ph ph-user-plus", "warn"),
    "bot_removed": ("ph ph-sign-out", "warn"),
    "owner_called": ("ph ph-bell-ringing", "accent"),
    "owner_paired": ("ph ph-key", "ok"),
    "owner_unpaired": ("ph ph-link", "warn"),
    "support_on": ("ph ph-lifebuoy", "ok"),
    "support_off": ("ph ph-lifebuoy", "neutral"),
    "support_escalated": ("ph ph-handshake", "accent"),
}
#: Sự kiện của CẢ KÊNH (không thuộc nhóm nào, không phải tin 1-1) — chỉ phạm vi ALL mới thấy.
CHANNEL_KINDS = ("channel_connected", "channel_error", "channel_ended", "groups_synced", "owner_paired",
                 "owner_unpaired")
DIRECT_KINDS = ("direct_new",)
GROUP_KINDS = tuple(k for k in KINDS if k not in CHANNEL_KINDS and k not in DIRECT_KINDS)
#: Tên người/nhóm kèm theo trong câu: tối đa bao nhiêu tên.
MAX_NAMES = 5
#: Một lượt quét danh bạ ghi tối đa bấy nhiêu dòng «nhóm mới» (và WebSocket); phần dư gom thành một dòng tóm tắt.
MAX_GROUP_NEW_EVENTS = 50

_ENDED_TEXT = {"logged_out": "đã đăng xuất", "expired": "đã hết hạn phiên", "error": "bị ngắt do lỗi"}


def _names(items: Any) -> list[str]:
    return [str(x)[:80] for x in (items or [])][:MAX_NAMES] if isinstance(items, list) else []


def _chan(channel_type: str | None) -> str:
    return CHANNEL_NAME.get(channel_type or "", channel_type or "kênh")


def event_text(kind: str, detail: dict[str, Any], *, channel_type: str | None, group_name: str | None,
               person_name: str | None, owner: bool) -> str:
    """Câu tiếng Việt cho một sự kiện. `owner` False ⇒ che số dài trong tên (khoá cứng 8)."""
    g = f"«{group_name}»" if group_name else "một nhóm"
    ch = _chan(channel_type)
    if kind == "group_new":
        mode = LISTEN_MODE_LABEL.get(str(detail.get("listen_mode")), "Chờ Sếp chọn")
        out = f"Tài khoản {ch} của Sếp vừa được thêm vào nhóm {g}. Nhóm tự vào chế độ: {mode}."
    elif kind == "group_left":
        out = f"Tài khoản {ch} của Sếp không còn trong nhóm {g} (đã rời hoặc bị mời ra)."
    elif kind == "group_rejoined":
        out = f"Tài khoản {ch} của Sếp trở lại nhóm {g}."
    elif kind == "group_renamed":
        old = detail.get("old")
        out = (f"Nhóm «{old}» đổi tên thành {g}." if old else f"Nhóm đổi tên thành {g}.")
    elif kind == "members_changed":
        parts: list[str] = []
        for key, what in (("joined", "thành viên mới"), ("left", "thành viên rời")):
            blk = detail.get(key) or {}
            if blk.get("count"):
                nm = _names(blk.get("names"))
                more = "…" if blk["count"] > len(nm) else ""
                parts.append(f"{blk['count']} {what}" + (f" ({', '.join(nm)}{more})" if nm else ""))
        out = f"Nhóm {g}: " + (", ".join(parts) or "thành viên thay đổi") + "."
    elif kind == "groups_synced":
        if detail.get("extra"):
            out = (f"Quét danh bạ {ch}: còn {int(detail.get('groups') or 0)} nhóm mới nữa "
                   "(không ghi từng nhóm một).")
        else:
            out = f"Lần đầu quét danh bạ {ch}: thấy {int(detail.get('groups') or 0)} nhóm."
    elif kind == "channel_connected":
        label = detail.get("account_label")
        head = f"Kênh {ch} đã nối lại" if detail.get("resumed") else f"Kênh {ch} đã đăng nhập"
        out = head + (f" ({label})." if label else ".")
    elif kind == "channel_error":
        out = f"Kênh {ch} mất kết nối, đang tự nối lại."
    elif kind == "channel_ended":
        out = f"Kênh {ch} {_ENDED_TEXT.get(str(detail.get('reason')), 'đã ngắt')}."
    elif kind == "direct_new":
        who = person_name or "một người lạ"
        out = f"Người mới nhắn riêng lần đầu trên {ch}: {who}."
    elif kind == "group_added_by_other":
        who = "người khác" if detail.get("added_by_kind") == "other" else "chưa rõ ai"
        out = f"Bot {ch} được thêm vào nhóm {g} bởi {who}: nhóm ở chế độ Chờ Sếp chọn (chưa lưu tin)."
    elif kind == "bot_removed":
        out = f"Bot {ch} không còn trong nhóm {g} (bị mời ra); chế độ hỗ trợ của nhóm đã tắt."
    elif kind == "owner_called":
        out = f"Sếp gọi bot ở nhóm {g}" + (" nhưng nhóm đang không nghe." if detail.get("not_listening")
                                           else " (bot trả lời tại chỗ).")
    elif kind == "owner_paired":
        out = f"Đã ghép kênh quản trị {ch} với Sếp."
    elif kind == "owner_unpaired":
        out = f"Kênh quản trị {ch} đã huỷ ghép" + (" (đổi tài khoản bot)." if detail.get("reason") == "session_changed"
                                                  else ".")
    elif kind == "support_on":
        out = f"Đã bật chế độ hỗ trợ cho nhóm {g}."
    elif kind == "support_off":
        reason = {"bot_removed": "bot bị mời ra", "owner": "Sếp tắt", "expired": "hết hạn",
                  "abuse_guard": "bị tấn công lặp",
                  "session_changed": "đổi tài khoản bot", "account_kind_changed": "đổi loại tài khoản",
                  "group_left": "bot rời nhóm"}.get(str(detail.get("reason")), "đã tắt")
        out = f"Đã tắt chế độ hỗ trợ của nhóm {g} ({reason})."
    elif kind == "support_escalated":
        out = f"Bot chuyển một câu hỏi khó ở nhóm {g} cho Sếp."
    else:
        out = "Có thay đổi trên kênh."
    return mask_text(out, owner) or out


def event_item(r: Any, *, owner: bool) -> dict[str, Any]:
    """Một dòng `ops.listen_events` (kèm tên kênh/nhóm/người đã nối) → phần tử của Nhật ký lắng nghe."""
    detail = r.detail if isinstance(r.detail, dict) else {}
    icon, tone = KINDS.get(r.kind, ("ph ph-info", "neutral"))
    # nhóm đã đổi tên: `group_renamed` dùng tên mới lưu trong detail để câu đúng lúc đó
    gname = detail.get("name") or r.group_name
    direct = r.kind in DIRECT_KINDS
    return {
        "kind": "event", "id": str(r.id), "at": iso(r.at), "event_type": r.kind, "icon": icon, "tone": tone,
        "text": event_text(r.kind, detail, channel_type=r.channel_type, group_name=gname,
                           person_name=r.person_name if direct else None, owner=owner),
        "channel": {"type": r.channel_type, "name": _chan(r.channel_type)} if r.channel_type else None,
        "group": {"id": str(r.group_id), "code": r.group_code, "name": mask_text(gname, owner)} if r.group_id else None,
        "person": {"id": str(r.person_id), "code": r.person_code, "name": mask_text(r.person_name, owner)}
        if direct and r.person_id else None,
        "direct": direct,
        "listen_mode": detail.get("listen_mode") if r.kind == "group_new" else None,
        "listen_mode_label": LISTEN_MODE_LABEL.get(str(detail.get("listen_mode"))) if r.kind == "group_new" else None,
        "raw_event_id": detail.get("raw_event_id"),
    }


EVENT_SELECT = """
SELECT v.id, v.at, v.kind, v.channel_type, v.group_id, v.person_id, v.detail,
       g.code AS group_code, g.name AS group_name, p.code AS person_code, p.display_name AS person_name
FROM ops.listen_events v
LEFT JOIN core.groups g ON g.id = v.group_id
LEFT JOIN core.persons p ON p.id = v.person_id
"""


async def record(db: AsyncSession, org_id: uuid.UUID, kind: str, *, channel_id: uuid.UUID | None = None,
                 channel_type: str | None = None, group_id: uuid.UUID | None = None,
                 person_id: uuid.UUID | None = None, detail: dict[str, Any] | None = None) -> uuid.UUID | None:
    """Ghi một sự kiện (KHÔNG commit) và cất để phát sau commit. Lỗi ghi sự kiện không được làm hỏng việc chính: dùng
    savepoint, nuốt lỗi và trả None."""
    if kind not in KINDS:
        raise ValueError(f"Loại sự kiện lạ: {kind}")
    try:
        async with db.begin_nested():
            eid = (await db.execute(text("""
                INSERT INTO ops.listen_events (org_id, kind, channel_id, channel_type, group_id, person_id, detail)
                VALUES (:o, :k, :c, :t, :g, :p, CAST(:d AS jsonb)) RETURNING id"""),
                {"o": org_id, "k": kind, "c": channel_id, "t": channel_type, "g": group_id, "p": person_id,
                 "d": orjson.dumps(detail or {}).decode()})).scalar_one()
    except Exception:  # noqa: BLE001 — ghi sự kiện là phụ, không chặn ingest/đăng nhập kênh
        log.warning("Không ghi được sự kiện %s", kind, exc_info=True)
        return None
    db.info.setdefault("listen_pub", []).append(eid)
    return eid  # type: ignore[no-any-return]


def take_pending(db: AsyncSession) -> list[uuid.UUID]:
    """Lấy (và xoá) danh sách id sự kiện đã ghi trong phiên `db`, chờ phát sau commit."""
    return list(db.info.pop("listen_pub", []) or [])


async def publish_pending(sm: Any, redis: Redis, org_id: uuid.UUID, ids: list[uuid.UUID]) -> None:
    """Phát `listen.event` cho các sự kiện vừa commit. Gói tin chỉ là TÍN HIỆU (id, loại, giờ, kênh, id nhóm) — không
    mang câu chữ, tên nhóm hay tên người: màn tải lại qua API nên phạm vi quyền và việc che số luôn do máy chủ quyết
    (WebSocket không có phạm vi nhóm). Sự kiện 1-1 mang `owner_only` ⇒ hub chỉ gửi cho Owner (xem gh.realtime)."""
    if not ids:
        return
    try:
        async with sm() as db:
            rows = (await db.execute(text(EVENT_SELECT + " WHERE v.id = ANY(:ids) ORDER BY v.at"),
                                     {"ids": ids})).all()
        for r in rows:
            await realtime.publish(redis, "listen.event", {
                "id": str(r.id), "at": iso(r.at), "event_type": r.kind, "channel_type": r.channel_type,
                "group_id": str(r.group_id) if r.group_id else None, "owner_only": r.kind in DIRECT_KINDS},
                org_id=org_id)
    except Exception:  # noqa: BLE001 — phát WebSocket là phụ
        log.warning("Không phát được listen.event", exc_info=True)
