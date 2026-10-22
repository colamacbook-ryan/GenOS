"""Nhật ký lắng nghe (v0.1.68): MỘT dòng thời gian gộp tin nghe được (Kho thô) + sự kiện trên mọi kênh, kèm "hệ thống đã
làm gì" (sàng lọc, quyết định của agent trực, bản nháp ở Bàn làm việc). Chỉ ĐỌC — không ghi, không lách quyền:

- Quyền vào màn = `data.read` (như Kho thô). Tin nhóm theo phạm vi `data.read` của vai (`Scope.group_id_sql`).
- Tin 1-1 (và sự kiện "người mới nhắn riêng") chỉ Owner hoặc người phụ trách đúng người đó
  (`direct_visible_sql`, v0.1.65).
- Nội dung/tên: vai dưới Owner bị che số dài (`mask_text`, khoá cứng 8); tìm chữ phía máy chủ cũng chạy trên bản đã
  bỏ số dài nên không dò được số bị che.
- Quyết định agent chỉ hiện cho vai có `system.read`/`action.approve` (như `/agents/decisions`); link bản nháp chỉ khi
  bản nháp nằm trong phạm vi của người xem (`_readable_draft_ids`).
- Nhóm "Không nghe"/"Tạm dừng"/"Chờ Sếp chọn" KHÔNG lưu tin ⇒ không có dòng tin nào của chúng (chỉ có sự kiện nhóm).
"""

import re
import unicodedata
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gh.auth import rbac, service
from gh.auth.deps import require
from gh.biz.core.routes import _readable_draft_ids
from gh.biz.core.scope import Scope, direct_visible_sql, scope_for
from gh.biz.duty.engine import SILENT_GROUP_REASON
from gh.data import listen_events as lev
from gh.data.common import CHANNEL_NAME, LISTENING_MODES, iso, mask_text, ref, since_cutoff
from gh.db import DB
from gh.errors import field_errors
from gh.refinery.prefilter import reason_text

router = APIRouter(tags=["data"])
READ = require("data.read")

#: Số dài (≥ 8 chữ số) mà `mask_text` che — dạng PostgreSQL. Dùng để BỎ khỏi chuỗi tìm kiếm của vai dưới Owner; mẫu này
#: khớp rộng hơn một chút so với mẫu Python (có thể bỏ thừa, không bao giờ bỏ thiếu) nên không lộ số bị che.
def _nd_class() -> str:
    """Mọi chữ số Unicode (loại Nd) dạng lớp ký tự regex — đúng tập mà `\\d` của Python (`mask_text`) coi là chữ số,
    kể cả số Ả Rập–Ấn Độ, số toàn chiều rộng…; `[0-9]` / `[[:digit:]]` của PostgreSQL chỉ khớp ASCII nên người xem dò
    được số đã che bằng chữ số Unicode. Dựng từ `unicodedata` lúc nạp mô-đun để hai bên luôn khớp nhau."""
    out: list[str] = []
    start: int | None = None
    for c in range(0x110000 + 1):
        nd = c < 0x110000 and unicodedata.category(chr(c)) == "Nd"
        if nd and start is None:
            start = c
        elif not nd and start is not None:
            out.append(chr(start) if start == c - 1 else f"{chr(start)}-{chr(c - 1)}")
            start = None
    return "".join(out)


_ND = _nd_class()
_LONGNUM_PG = rf"(?<![{_ND}])[{_ND}][{_ND} .-]{{7,22}}[{_ND}](?![{_ND}])"
_CURSOR_RE = re.compile(r"^(\d{1,20})_([0-9a-fA-F-]{36})$")
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
MAX_TEXT = 400

STATE_NOTE = {
    "pending": "Chờ lượt sàng lọc tới",
    "processing": "Đang được sàng lọc",
    "clean": "Đã vào kho sạch",
    "lowconf": "Độ tin cậy thấp — chưa vào kho sạch",
    "discarded": "Đã loại khi sàng lọc",
    "error": "Lỗi khi sàng lọc — sẽ không tự thử lại",
}
DECISION_LABEL = {
    "silent": "Im lặng", "note": "Ghi chú vào sổ tay", "suggest": "Gợi ý hành động", "draft": "Soạn nháp trả lời",
    "send": "Soạn nháp để gửi",
}
DRAFT_STATUS_LABEL = {
    "pending": "chờ duyệt", "approved": "đã duyệt", "edited": "đã sửa, chờ gửi", "rejected": "bị từ chối",
    "sent": "đã gửi", "failed": "gửi lỗi",
}


def _cursor_of(ts: datetime, id_: uuid.UUID) -> str:
    return f"{(ts - _EPOCH) // timedelta(microseconds=1)}_{id_}"


def _parse_cursor(cursor: str | None) -> tuple[datetime, uuid.UUID] | None:
    if not cursor:
        return None
    m = _CURSOR_RE.match(cursor)
    if not m:
        raise field_errors({"cursor": "Con trỏ không hợp lệ"})
    try:
        return _EPOCH + timedelta(microseconds=int(m.group(1))), uuid.UUID(m.group(2))
    except (ValueError, OverflowError) as e:
        raise field_errors({"cursor": "Con trỏ không hợp lệ"}) from e


def _like(q: str) -> str:
    return "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


class Filters:
    def __init__(self, *, channel: str | None, group_id: uuid.UUID | None, since: str | None, tagged: bool,
                 acted: bool, q: str | None, kind: str):
        self.channel, self.group_id, self.tagged, self.acted, self.kind = channel, group_id, tagged, acted, kind
        self.q = (q or "").strip()[:100] or None
        self.cut = since_cutoff(since)


def _vis(expr: str, owner: bool) -> str:
    """Biểu thức SQL của `expr` như người xem thấy: Owner nguyên văn; vai dưới Owner đã bỏ số dài."""
    return expr if owner else f"regexp_replace(COALESCE({expr}, ''), :lnre, '', 'g')"


def _msg_where(user: service.CurrentUser, sc: Scope, f: Filters,
               cursor: tuple[datetime, uuid.UUID] | None) -> tuple[str, dict[str, Any]]:
    owner = user.role_code == rbac.OWNER
    where = ["e.org_id = :o"]
    dm_sql, params = direct_visible_sql(
        user, "e.group_id",
        "(SELECT pi2.person_id FROM core.person_identities pi2 WHERE pi2.id = e.sender_identity_id)")
    where.append(dm_sql)
    params["o"] = user.org_id
    if not sc.is_all:
        gw, gp = sc.group_id_sql("e.group_id")
        where.append(f"(e.group_id IS NULL OR {gw})")
        params.update(gp)
    if f.channel:
        where.append("c.type = :ch")
        params["ch"] = f.channel
    if f.group_id:
        where.append("e.group_id = :g")
        params["g"] = f.group_id
    if f.cut:
        where.append("e.received_at >= :cut")
        params["cut"] = f.cut
    if f.tagged:
        where.append("e.mentions_agent")
    if f.acted:
        where.append("""EXISTS (SELECT 1 FROM clean.evidence ax
                         JOIN agent.decisions ad ON ad.trigger_unit_id = ax.meaning_unit_id
                         WHERE ax.raw_event_id = e.id AND ad.decision <> 'silent')""")
    if f.q:
        params["q"], params["lnre"] = _like(f.q), _LONGNUM_PG
        where.append(f"({_vis('e.body_text', owner)} ILIKE :q ESCAPE '\\' OR {_vis('p.display_name', owner)} ILIKE :q "
                     f"ESCAPE '\\' OR g.name ILIKE :q ESCAPE '\\')")
    if cursor:
        where.append("(e.received_at, e.id) < (:cts, :cid)")
        params["cts"], params["cid"] = cursor
    return " WHERE " + " AND ".join(where), params


def _event_where(user: service.CurrentUser, sc: Scope, f: Filters,
                 cursor: tuple[datetime, uuid.UUID] | None) -> tuple[str, dict[str, Any]]:
    owner = user.role_code == rbac.OWNER
    where = ["v.org_id = :o"]
    params: dict[str, Any] = {"o": user.org_id}
    # Sự kiện của cả kênh: chỉ phạm vi ALL; sự kiện nhóm: theo phạm vi nhóm; "người mới nhắn riêng": quy tắc 1-1.
    parts = []
    gw, gp = sc.group_id_sql("v.group_id")      # phạm vi gán + «Quyền xem» của nhóm (v0.1.71); `all` + Owner ⇒ TRUE
    if sc.is_all:
        parts.append("v.kind = ANY(:ch_kinds)")
        params["ch_kinds"] = list(lev.CHANNEL_KINDS)
    parts.append(f"(v.kind = ANY(:grp_kinds) AND {gw})")
    params.update(gp)
    params["grp_kinds"] = list(lev.GROUP_KINDS)
    dm_sql, dm_params = direct_visible_sql(user, "CAST(NULL AS uuid)", "v.person_id")
    parts.append(f"(v.kind = 'direct_new' AND {dm_sql})")
    params.update(dm_params)
    where.append("(" + " OR ".join(parts) + ")")
    if f.channel:
        where.append("v.channel_type = :ch")
        params["ch"] = f.channel
    if f.group_id:
        where.append("v.group_id = :g")
        params["g"] = f.group_id
    if f.cut:
        where.append("v.at >= :cut")
        params["cut"] = f.cut
    if f.q:
        params["q"], params["lnre"] = _like(f.q), _LONGNUM_PG
        where.append(f"(g.name ILIKE :q ESCAPE '\\' OR {_vis('p.display_name', owner)} ILIKE :q ESCAPE '\\' "
                     f"OR {_vis('CAST(v.detail AS text)', owner)} ILIKE :q ESCAPE '\\')")
    if cursor:
        where.append("(v.at, v.id) < (:cts, :cid)")
        params["cts"], params["cid"] = cursor
    return " WHERE " + " AND ".join(where), params


_MSG_SELECT = """
SELECT e.id, e.seq, e.received_at, e.kind, e.direction, e.body_text, e.mentions_agent, e.listen_mode,
       c.type AS channel_type, c.name AS channel_name,
       g.id AS group_id, g.code AS group_code, g.name AS group_name, g.listen_mode AS group_mode_now,
       p.id AS person_id, p.code AS person_code, p.display_name AS person_name,
       s.state, s.label, s.confidence, s.detail AS state_detail
FROM raw.events e
JOIN core.channels c ON c.id = e.channel_id
LEFT JOIN core.groups g ON g.id = e.group_id
LEFT JOIN core.person_identities pi ON pi.id = e.sender_identity_id
LEFT JOIN LATERAL (
  WITH RECURSIVE up(id, merged_into_id, depth) AS (
    SELECT id, merged_into_id, 0 FROM core.persons WHERE id = pi.person_id
    UNION ALL SELECT q.id, q.merged_into_id, up.depth + 1 FROM core.persons q JOIN up ON q.id = up.merged_into_id
    WHERE up.depth < 10)
  SELECT id FROM up WHERE merged_into_id IS NULL LIMIT 1) live ON true
LEFT JOIN core.persons p ON p.id = live.id
LEFT JOIN refinery.event_state s ON s.event_id = e.id AND s.event_received_at = e.received_at
"""


def _screening_note(state: str, detail: Any, label: str | None) -> str:
    base = STATE_NOTE.get(state, state)
    d = detail if isinstance(detail, dict) else {}
    if state == "discarded":
        by = d.get("discarded_by")
        if by == "prefilter":
            return f"{base}: {reason_text(str(d.get('reason') or ''))}"
        if by == "model":
            return f"{base}: model xếp là nhiễu"
        if by:
            return f"{base}: quy tắc {by} loại"
        return base
    if state == "lowconf" and d.get("reason") == "model_skipped":
        return f"{base} (model bỏ qua)"
    return base


def _decision_label(decision: str, outcome: str | None, draft_status: str | None, rationale: str | None = None) -> str:
    if decision == "silent":
        if outcome == "blocked" and SILENT_GROUP_REASON in (rationale or ""):
            return "Im lặng (nhóm Lắng nghe im lặng — không bao giờ trả lời, kể cả khi được tag)"
        return {"rejected": "Im lặng (phản hồi của model bị loại)", "blocked": "Im lặng (mức tự trị không cho phép)"
                }.get(outcome or "", "Im lặng")
    if decision in ("draft", "send"):
        if draft_status:
            return f"Nháp trả lời: {DRAFT_STATUS_LABEL.get(draft_status, draft_status)}"
        return "Hạ xuống gợi ý (chưa tạo nháp)" if outcome == "suggested" else DECISION_LABEL[decision]
    return DECISION_LABEL.get(decision, decision)


def _quiet_note(r: Any, decisions: list[dict[str, Any]], can_see_decisions: bool) -> str | None:
    """Vì sao hệ thống KHÔNG làm gì thêm cho tin này (chỉ nói điều thật biết được từ dữ liệu đã lưu)."""
    if decisions or not can_see_decisions:
        return None
    if r.direction == "outbound":
        return "Tin do phía mình gửi — không cần agent trả lời"
    if r.kind in ("reaction", "sticker", "system"):
        return "Biểu tượng cảm xúc, nhãn dán hoặc tin hệ thống — không cần trả lời"
    if r.state in ("pending", "processing", None):
        return None
    if r.state == "discarded":
        return "Đã loại ở sàng lọc nên không đưa cho agent"
    if r.state != "clean":
        return None
    if r.group_id is None and r.listen_mode == "silent":
        return "Tin riêng ở chế độ lưu im lặng — không đưa cho agent trực"
    if r.group_id is not None and (r.listen_mode or r.group_mode_now) == "silent":
        # QD 17/10: nhóm Lắng nghe im lặng chỉ lưu + sàng lọc; không vào ứng viên agent trực (không có quyết định nào).
        return "Nhóm Lắng nghe im lặng — chỉ lưu và sàng lọc, không đưa cho agent trực"
    return "Chưa có agent nào quyết định cho tin này"


async def _decisions_for(db: AsyncSession, user: service.CurrentUser,
                         ids: list[uuid.UUID]) -> dict[uuid.UUID, list[dict[str, Any]]]:
    if not ids:
        return {}
    rows = (await db.execute(text("""
        SELECT x.raw_event_id AS eid, d.id, d.at, d.agent_id, a.name AS agent_name, d.decision, d.outcome,
               d.draft_id, d.rationale, ad.code AS draft_code, ad.status AS draft_status
        FROM clean.evidence x
        JOIN agent.decisions d ON d.trigger_unit_id = x.meaning_unit_id AND d.org_id = :o
        JOIN agent.identities a ON a.id = d.agent_id
        LEFT JOIN biz.action_drafts ad ON ad.id = d.draft_id
        WHERE x.raw_event_id = ANY(:ids)
        ORDER BY d.at"""), {"o": user.org_id, "ids": ids})).all()
    readable = await _readable_draft_ids(db, user, [r.draft_id for r in rows if r.draft_id])
    out: dict[uuid.UUID, list[dict[str, Any]]] = {}
    for r in rows:
        draft = None
        if r.draft_id and r.draft_id in readable:
            draft = {"id": str(r.draft_id), "code": r.draft_code, "status": r.draft_status}
        out.setdefault(r.eid, []).append({
            "id": str(r.id), "at": iso(r.at), "agent": {"id": str(r.agent_id), "name": r.agent_name},
            "decision": r.decision, "outcome": r.outcome,
            "label": _decision_label(r.decision, r.outcome, r.draft_status if r.draft_id else None, r.rationale),
            "draft": draft})
    return out


def _message_item(r: Any, owner: bool, decisions: list[dict[str, Any]], can_see: bool) -> dict[str, Any]:
    direct = r.group_id is None
    if r.listen_mode:
        mode, known = r.listen_mode, True
        label = lev.DIRECT_MODE_LABEL.get(mode) if direct else lev.LISTEN_MODE_LABEL.get(mode)
    elif direct:
        mode, known, label = None, False, None
    else:
        mode, known = r.group_mode_now, False
        label = lev.LISTEN_MODE_LABEL.get(r.group_mode_now or "")
    body = mask_text(r.body_text, owner)
    return {
        "kind": "message", "id": str(r.id), "at": iso(r.received_at), "code": f"RAW-{r.seq:06d}",
        "channel": {"type": r.channel_type, "name": CHANNEL_NAME.get(r.channel_type, r.channel_name)},
        "group": ref(r.group_id, r.group_code, r.group_name), "direct": direct,
        "sender": ({"id": str(r.person_id), "code": r.person_code, "name": mask_text(r.person_name, owner)}
                   if r.person_id else None),
        "direction": r.direction, "msg_kind": r.kind,
        "text": (body[:MAX_TEXT] + "…") if body and len(body) > MAX_TEXT else body,
        "tagged": bool(r.mentions_agent),
        "listen_mode": mode, "listen_mode_label": label, "listen_mode_known": known,
        "screening": {"state": r.state or "pending", "label": r.label,
                      "confidence": float(r.confidence) if r.confidence is not None else None,
                      "note": _screening_note(r.state or "pending", r.state_detail, r.label)},
        "decisions": decisions, "note": _quiet_note(r, decisions, can_see),
    }


def _can_see_decisions(user: service.CurrentUser) -> bool:
    return (user.permissions.get("system.read", rbac.NONE) != rbac.NONE
            or user.permissions.get("action.approve", rbac.NONE) != rbac.NONE)


def _filters(channel: str | None, group_id: str | None, since: str | None, tagged: bool, acted: bool, q: str | None,
             kind: str) -> Filters:
    gid = None
    if group_id:
        try:
            gid = uuid.UUID(group_id)
        except ValueError as e:
            raise field_errors({"group_id": "Mã không hợp lệ"}) from e
    return Filters(channel=channel, group_id=gid, since=since, tagged=tagged, acted=acted, q=q, kind=kind)


@router.get("/listen-log")
async def listen_log(cursor: str | None = None, limit: int = Query(50, ge=1, le=100),
                     channel: Literal["zalo", "whatsapp", "telegram", "linkedin"] | None = None,
                     group_id: str | None = None, since: str | None = "7d", tagged: bool = False, acted: bool = False,
                     q: str | None = Query(None, max_length=200),
                     kind: Literal["all", "message", "event"] = "all",
                     user: service.CurrentUser = Depends(READ), db: AsyncSession = DB) -> dict[str, Any]:
    f = _filters(channel, group_id, since, tagged, acted, q, kind)
    cur = _parse_cursor(cursor)
    sc = await scope_for(db, user, "data.read")
    owner = user.role_code == rbac.OWNER
    can_see = _can_see_decisions(user)
    merged: list[tuple[datetime, uuid.UUID, str, Any]] = []   # (thời điểm, id, loại, dòng)
    if kind in ("all", "message"):
        where, params = _msg_where(user, sc, f, cur)
        for r in (await db.execute(text(_MSG_SELECT + where + " ORDER BY e.received_at DESC, e.id DESC LIMIT :lim"),
                                   {**params, "lim": limit + 1})).all():
            merged.append((r.received_at, r.id, "m", r))
    if kind in ("all", "event") and not tagged and not acted:
        where, params = _event_where(user, sc, f, cur)
        for r in (await db.execute(text(lev.EVENT_SELECT + where + " ORDER BY v.at DESC, v.id DESC LIMIT :lim"),
                                   {**params, "lim": limit + 1})).all():
            merged.append((r.at, r.id, "e", r))
    merged.sort(key=lambda t: (t[0], t[1].int), reverse=True)
    more = len(merged) > limit
    page = merged[:limit]
    dec = (await _decisions_for(db, user, [t[1] for t in page if t[2] == "m"])) if can_see else {}
    items = [_message_item(t[3], owner, dec.get(t[1], []), can_see) if t[2] == "m"
             else lev.event_item(t[3], owner=owner) for t in page]
    return {"items": items, "next_cursor": _cursor_of(page[-1][0], page[-1][1]) if more and page else None,
            "decisions_visible": can_see}


@router.get("/listen-log/stats")
async def listen_log_stats(user: service.CurrentUser = Depends(READ), db: AsyncSession = DB) -> dict[str, Any]:
    """Số liệu đầu màn: số tin + sự kiện 24 giờ theo kênh (cùng phạm vi/quy tắc 1-1 với danh sách), số nhóm đang nghe,
    và danh sách nhóm cho ô lọc."""
    sc = await scope_for(db, user, "data.read")
    f = _filters(None, None, "24h", False, False, None, "all")
    mw, mp = _msg_where(user, sc, f, None)
    ew, ep = _event_where(user, sc, f, None)
    msg = {r.ctype: r.n for r in (await db.execute(text(
        "SELECT c.type AS ctype, count(*) AS n FROM raw.events e JOIN core.channels c ON c.id = e.channel_id "
        "LEFT JOIN core.groups g ON g.id = e.group_id "
        "LEFT JOIN core.person_identities pi ON pi.id = e.sender_identity_id "
        "LEFT JOIN core.persons p ON p.id = pi.person_id" + mw + " GROUP BY c.type"), mp)).all()}
    evt = {r.ctype: r.n for r in (await db.execute(text(
        "SELECT COALESCE(v.channel_type, 'other') AS ctype, count(*) AS n FROM ops.listen_events v "
        "LEFT JOIN core.groups g ON g.id = v.group_id LEFT JOIN core.persons p ON p.id = v.person_id" + ew
        + " GROUP BY 1"), ep)).all()}
    gw, gp = sc.group_sql("g")
    groups = (await db.execute(text(f"""
        SELECT g.id, g.code, g.name, g.listen_mode, c.type AS channel_type FROM core.groups g
        JOIN core.channels c ON c.id = g.channel_id
        WHERE g.org_id = :o AND g.left_at IS NULL AND g.listen_mode = ANY(:modes) AND {gw}
        ORDER BY g.name LIMIT 300"""), {"o": user.org_id, "modes": list(LISTENING_MODES), **gp})).all()  # noqa: S608
    listening = (await db.execute(text(f"""
        SELECT c.type AS ctype, count(*) AS n FROM core.groups g JOIN core.channels c ON c.id = g.channel_id
        WHERE g.org_id = :o AND g.left_at IS NULL AND g.listen_mode = ANY(:modes) AND {gw} GROUP BY c.type"""),  # noqa: S608
        {"o": user.org_id, "modes": list(LISTENING_MODES), **gp})).all()
    chans = (await db.execute(text("SELECT type FROM core.channels WHERE org_id = :o ORDER BY type"),
                              {"o": user.org_id})).scalars().all()
    by_channel = [{"type": t, "name": CHANNEL_NAME.get(t, t), "messages_24h": int(msg.get(t, 0)),
                   "events_24h": int(evt.get(t, 0)),
                   "groups_listening": int(next((r.n for r in listening if r.ctype == t), 0))} for t in chans]
    return {"by_channel": by_channel, "messages_24h": sum(int(v) for v in msg.values()),
            "events_24h": sum(int(v) for v in evt.values()), "groups_listening": sum(int(r.n) for r in listening),
            "groups": [{"id": str(g.id), "code": g.code, "name": g.name, "channel": g.channel_type,
                        "listen_mode": g.listen_mode} for g in groups]}
