"""Bàn làm việc: tạo bản nháp qua policy, duyệt → permit dùng một lần → bridge gửi → kết quả (ARCHITECTURE §7).

Luật cứng (khoá 3): mọi thứ ghi ra ngoài, vượt ngưỡng tiền, liên quan nhân sự dừng ở đây ở **mọi** mức tự trị.
Cờ lấy từ registry loại hành động của chassis (`gh.chassis.policy`), không từ model hay người gọi.

Mã khác tạo bản nháp bằng `create_draft(...)`. Duyệt / sửa rồi gửi / huỷ đi qua `decide(...)`.
"""

import base64
import hashlib
import hmac
import json
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import orjson
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gh import crypto, notifications, realtime
from gh.chassis import actionlog, policy
from gh.chassis.bus import BRIDGE_OUTBOUND, EventBus
from gh.data.common import iso, mask_text

KIND_LABELS = {"message": "Tin nhắn", "quotation": "Báo giá", "contract": "Hợp đồng", "reminder": "Nhắc việc",
               "report": "Báo cáo", "mcp_write": "Ghi hệ thống ngoài"}
# Loại bản nháp → khoá registry mặc định.
KIND_ACTION = {"message": "message.send", "quotation": "quotation.send", "contract": "contract.send",
               "reminder": "reminder.create", "report": "report.create", "mcp_write": "mcp.write"}
CHANNEL_LABELS = {"zalo": "Zalo", "whatsapp": "WhatsApp", "telegram": "Telegram", "linkedin": "LinkedIn"}
SENDABLE = {"message", "quotation", "contract"}
PENDING, APPROVED, EDITED, REJECTED, SENT, FAILED = "pending", "approved", "edited", "rejected", "sent", "failed"
DECIDED = (APPROVED, EDITED, REJECTED, SENT, FAILED)

# Gửi lỗi (v0.1.61, F048): Sếp phải được báo (chuông + sự kiện `draft.failed`) bằng câu tiếng Việt theo mã; mã gốc
# chỉ nằm ở "Chi tiết kỹ thuật". Mã lạ (bridge thêm mã mới) rơi về câu chung — không bao giờ để trống.
realtime.register_event("draft.failed", "action.approve")
SEND_ERROR_TEXT = {
    "NO_TARGET": "Bản nháp chưa có nơi nhận (nhóm hoặc người nhận) nên chưa gửi được.",
    "TARGET_NOT_FOUND": "Không tìm thấy nhóm hoặc tài khoản nhận trên nền tảng "
                        "(có thể đã rời nhóm hoặc đổi tài khoản).",
    "SESSION_NOT_ACTIVE": "Kênh chat đang ngắt kết nối nên tin chưa đi. Đăng nhập lại kênh rồi gửi lại.",
    "PERMIT_EXPIRED": "Chưa nhận được xác nhận đã gửi từ kênh chat (kênh có thể vừa rớt mạng), "
                      "tin có thể chưa tới người nhận. Kiểm tra trên ứng dụng chat rồi gửi lại.",
    "PERMIT_INVALID": "Lệnh gửi không còn hợp lệ nên kênh chat không gửi. Hãy gửi lại.",
    "PERMIT_MISMATCH": "Lệnh gửi không khớp nội dung hoặc nơi nhận nên kênh chat không gửi. Hãy gửi lại.",
    "PERMIT_REUSED": "Lệnh gửi này đã được dùng một lần, kênh chat không gửi lại lần nữa để tránh trùng tin.",
    "NONCE_CHECK_FAILED": "Hệ thống chưa kiểm tra được lệnh gửi nên kênh chat không gửi. Hãy gửi lại.",
    "SEND_FAILED": "Nền tảng chat từ chối hoặc không gửi được tin này.",
}
SEND_ERROR_FALLBACK = "Tin chưa gửi được vì lỗi từ kênh chat."
# F074 (Q8-1): duyệt "Ghi hệ thống ngoài" (mcp_write) chưa có bước thực thi ⇒ KHÔNG duyệt, KHÔNG đặt `sent`.
MCP_WRITE_UNSUPPORTED = ("Chưa hỗ trợ thực thi tool ghi — đã giữ yêu cầu ở Bàn làm việc, hệ thống ngoài chưa bị "
                         "gọi. Sếp có thể huỷ bản nháp này.")


def send_error_message(code: str | None) -> str:
    """Câu tiếng Việt cho mã lỗi gửi tin (`send_result.error`)."""
    return SEND_ERROR_TEXT.get(code or "", SEND_ERROR_FALLBACK)


class DraftError(Exception):
    def __init__(self, status: int, code: str, title: str):
        super().__init__(title)
        self.status, self.code, self.title = status, code, title


@dataclass(frozen=True)
class Target:
    """Nơi bản nháp sẽ được gửi tới. Đúng một trong group_id / person_id."""

    channel: str                      # zalo | whatsapp | …
    thread_type: str                  # group | user
    group_id: uuid.UUID | None = None
    person_id: uuid.UUID | None = None
    #: Tài khoản kênh cụ thể (core.person_identities.id) của tin 1-1. Có thì gửi đúng tài khoản đó — người có hai
    #: tài khoản cùng loại kênh (thường gặp sau khi gộp hồ sơ) không bị gửi nhầm sang tài khoản khác; tài khoản đi
    #: theo người khi gộp.
    identity_id: uuid.UUID | None = None

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {"channel": self.channel, "thread_type": self.thread_type,
                               "group_id": str(self.group_id) if self.group_id else None,
                               "person_id": str(self.person_id) if self.person_id else None}
        if self.identity_id:
            out["identity_id"] = str(self.identity_id)
        return out


def reply_target(channel: str, *, group_id: uuid.UUID | None = None, person_id: uuid.UUID | None = None,
                 identity_id: uuid.UUID | None = None) -> Target:
    """Đích trả lời một tin: có nhóm → gửi vào nhóm; không thì tin 1-1 gửi riêng cho người (F049), ưu tiên đúng
    tài khoản đã nhắn (`identity_id`). Dùng chung cho người gác kênh (`gh.biz.duty.engine`) và Hộp thư
    (`gh.biz.queue.routes.act_inbox_item`)."""
    if group_id is not None:
        return Target(channel, "group", group_id=group_id)
    return Target(channel, "user", person_id=person_id, identity_id=identity_id)


async def person_reply_account(db: AsyncSession, org_id: uuid.UUID, person_id: uuid.UUID,
                               unit_id: uuid.UUID | None = None) -> tuple[str, uuid.UUID] | None:
    """(loại kênh, id tài khoản) để nhắn riêng `person_id`: chính tài khoản đã nói ra tin gốc (đơn vị `unit_id`, qua
    chứng cứ) nếu còn thuộc người này; không thì tài khoản mới xuất hiện gần nhất (`first_seen_at` giảm dần).
    None = người này chưa có tài khoản kênh nào (không dựng được đích)."""
    r = (await db.execute(text("""
        SELECT c.type AS channel, pi.id AS identity_id
        FROM core.person_identities pi JOIN core.channels c ON c.id = pi.channel_id
        WHERE pi.person_id = :p AND c.org_id = :o
        ORDER BY (pi.id IN (SELECT e.sender_identity_id FROM clean.evidence ev
                            JOIN raw.events e ON e.id = ev.raw_event_id AND e.received_at = ev.raw_received_at
                            WHERE ev.meaning_unit_id = :u)) DESC,
                 (c.id IN (SELECT e.channel_id FROM clean.evidence ev
                           JOIN raw.events e ON e.id = ev.raw_event_id AND e.received_at = ev.raw_received_at
                           WHERE ev.meaning_unit_id = :u)) DESC, pi.first_seen_at DESC
        LIMIT 1"""), {"p": person_id, "o": org_id, "u": unit_id})).one_or_none()
    return (str(r.channel), r.identity_id) if r else None


# ─── mức tự trị hiệu lực ──────────────────────────────────────────────────────

async def approval_threshold(db: AsyncSession, org_id: uuid.UUID) -> int:
    v = (await db.execute(text("""SELECT params->>'approval_threshold_vnd' FROM ops.policy_boundaries
                                   WHERE org_id = :o AND code = 'approval_gate'"""),
                          {"o": org_id})).scalar_one_or_none()
    return int(v) if v else policy.DEFAULT_APPROVAL_THRESHOLD_VND


async def effective_level(db: AsyncSession, org_id: uuid.UUID, *, agent_id: uuid.UUID | None = None,
                          person_id: uuid.UUID | None = None, group_id: uuid.UUID | None = None) -> int:
    """Mức thấp nhất trong các lớp đã đặt: tổ chức → nhóm → người → agent (ARCHITECTURE §7.1).

    Mức riêng của một người / nhóm nằm ở `attrs.autonomy_level` (đặt từ Hồ sơ sống, Nhóm & Con người).
    """
    r = (await db.execute(text("""
        SELECT (SELECT (settings->>'autonomy_level')::int FROM core.organizations WHERE id = :o) AS org,
               (SELECT (attrs->>'autonomy_level')::int FROM core.groups WHERE id = :g) AS grp,
               (SELECT (attrs->>'autonomy_level')::int FROM core.persons WHERE id = :p) AS per,
               (SELECT autonomy_level FROM agent.identities WHERE id = :a) AS agt"""),
        {"o": org_id, "g": group_id, "p": person_id, "a": agent_id})).one()
    return policy.effective_autonomy(r.org, r.grp, r.per, r.agt)


# ─── tạo ──────────────────────────────────────────────────────────────────────

def _paragraphs(body: str) -> list[str]:
    return [p.strip() for p in body.replace("\r\n", "\n").split("\n\n") if p.strip()]


async def create_draft(db: AsyncSession, *, org_id: uuid.UUID, kind: str, title: str, body_text: str,
                       target: Target | None = None, action_key: str | None = None,
                       agent_id: uuid.UUID | None = None, created_by: uuid.UUID | None = None,
                       subject: tuple[str, uuid.UUID] | None = None, sources: list[dict[str, Any]] | None = None,
                       side_actions: list[dict[str, Any]] | None = None, amount_vnd: int | None = None,
                       autonomy_level: int | None = None, lang: str = "vi", parent_draft_id: uuid.UUID | None = None,
                       redis: Redis | None = None) -> dict[str, Any]:
    """Tạo một hành động đề xuất và cho qua policy.

    - `sources`: `[{"label", "ref": {"type", "id", "code"?}}]` — đúng dữ liệu đã dùng, không mục nào không truy được.
    - `side_actions`: `[{"key", "label", "on", "action_key", "params"}]` — công tắc "Tạo kèm theo".
    - `autonomy_level`: bỏ trống → tính mức hiệu lực theo tổ chức / nhóm / người / agent.

    Trả `{"id", "code", "status", "outcome", "hold_reason"}`. `outcome` là quyết định policy: `held` (vào Bàn
    làm việc, trạng thái `pending`), `auto` (việc nội bộ được tự làm: thực hiện ngay, trạng thái `sent`),
    `suggest` / `blocked` (mức 0–3: không tạo bản nháp, `id` = None).
    """
    if kind not in KIND_LABELS:
        raise ValueError(f"Loại bản nháp không hợp lệ: {kind}")
    key = action_key or KIND_ACTION[kind]
    t = policy.action_type(key)
    person_id = target.person_id if target else None
    group_id = target.group_id if target else None
    if subject and subject[0] == "person":
        person_id = person_id or subject[1]
    if subject and subject[0] == "group":
        group_id = group_id or subject[1]
    level = autonomy_level if autonomy_level is not None else await effective_level(
        db, org_id, agent_id=agent_id, person_id=person_id, group_id=group_id)
    threshold = await approval_threshold(db, org_id)
    if created_by is not None and level < 4:
        level = 4    # người tự soạn: luôn thành bản nháp chờ duyệt, không bị mức 0–3 của agent chặn
    decision = policy.evaluate(key, level, amount_vnd=amount_vnd or 0, approval_threshold_vnd=threshold)
    if decision.outcome in (policy.SUGGEST, policy.BLOCKED):
        return {"id": None, "code": None, "status": None, "outcome": decision.outcome,
                "hold_reason": "; ".join(decision.reasons)}
    over = bool(t.has_amount and (amount_vnd or 0) > threshold)
    flags = {"writes_external": t.writes_external, "personnel_related": t.personnel_related,
             "over_threshold": over, "amount_vnd": amount_vnd}
    channel_id = None
    if target is not None:
        channel_id = (await db.execute(text("SELECT id FROM core.channels WHERE org_id = :o AND type = :t"),
                                       {"o": org_id, "t": target.channel})).scalar_one_or_none()
    body: dict[str, Any] = {"text": body_text, "lang": lang, "target": target.to_json() if target else None,
            "amount_vnd": amount_vnd, "versions": []}
    code = (await db.execute(text("SELECT core.next_code('ACT')"))).scalar_one()
    status = PENDING if decision.outcome == policy.HELD else SENT
    row = (await db.execute(text("""
        INSERT INTO biz.action_drafts (org_id, code, kind, action_key, title, agent_id, created_by, subject_type,
                                       subject_id, channel_id, group_id, body, sources, side_actions, autonomy_level,
                                       hold_reason, status, flags, parent_draft_id,
                                       decided_at, sent_at)
        VALUES (:o, :c, :k, :ak, :ti, :a, :cb, :st, :si, :ch, :g, CAST(:b AS jsonb), CAST(:src AS jsonb),
                CAST(:sa AS jsonb), :lvl, :hr, :s, CAST(:f AS jsonb), :par,
                CASE WHEN :s = 'sent' THEN now() END, CASE WHEN :s = 'sent' THEN now() END)
        RETURNING id, code"""),
        {"o": org_id, "c": code, "k": kind, "ak": key, "ti": title, "a": agent_id, "cb": created_by,
         "st": subject[0] if subject else None, "si": subject[1] if subject else None, "ch": channel_id,
         "g": group_id, "b": orjson.dumps(body).decode(), "src": orjson.dumps(sources or []).decode(),
         "sa": orjson.dumps(side_actions or []).decode(), "lvl": level, "hr": decision.hold_reason,
         "s": status, "f": orjson.dumps(flags).decode(), "par": parent_draft_id})).one()
    actor_type, actor_id = ("agent", f"agent:{agent_id}") if agent_id else (
        ("user", f"user:{created_by}") if created_by else ("system", "system:drafts"))
    await actionlog.record(db, org_id=org_id, actor_type=actor_type, actor_id=actor_id,
                           action="draft.created" if status == PENDING else "action.auto_executed",
                           target_type="draft", target_id=str(row.id), target_label=f"{row.code} · {title}",
                           autonomy_level=level, result="held" if status == PENDING else "ok",
                           detail={"action_key": key, "reasons": decision.reasons, "flags": flags})
    if status == SENT:
        await _execute_internal(db, org_id, row.id, key, body_text, subject, created_by)
    if redis is not None:
        await realtime.publish(redis, "draft.new", await list_item(db, row.id), org_id=org_id)
    return {"id": row.id, "code": row.code, "status": status, "outcome": decision.outcome,
            "hold_reason": decision.hold_reason}


def _when(value: Any) -> datetime | None:
    if value is None or isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))


async def _execute_internal(db: AsyncSession, org_id: uuid.UUID, draft_id: uuid.UUID | None, action_key: str,
                            label: str, subject: tuple[str, uuid.UUID] | None, user_id: uuid.UUID | None,
                            params: dict[str, Any] | None = None) -> None:
    """Việc nội bộ (không ghi ra ngoài): tạo việc, đặt nhắc, ghi sổ tay, gắn nhãn."""
    params = params or {}
    if action_key in ("task.create", "reminder.create"):
        code = (await db.execute(text("SELECT core.next_code('TSK')"))).scalar_one()
        due = _when(params.get("due_at"))
        remind = _when(params.get("remind_at"))
        if action_key == "reminder.create" and not remind:
            remind = datetime.now(UTC) + timedelta(hours=float(params.get("after_hours", 48)))
        await db.execute(text("""
            INSERT INTO biz.tasks (org_id, code, title, priority, status, assignee_user_id, subject_type, subject_id,
                                   due_at, remind_at, source)
            VALUES (:o, :c, :t, :p, 'todo', :u, :st, :si, :d, :r, 'draft')"""),
            {"o": org_id, "c": code, "t": label, "p": params.get("priority", "P2"), "u": user_id,
             "st": subject[0] if subject else None, "si": subject[1] if subject else None, "d": due, "r": remind})
    elif action_key == "note.write" and subject and subject[0] in ("person", "group"):
        from gh.memory import notebook

        await notebook.append(db, org_id, subject[0], subject[1], params.get("section", "rolling_context"), label,
                              refs=[{"type": "draft", "id": str(draft_id)}] if draft_id else [],
                              author="user" if user_id else "agent")


# ─── đọc ──────────────────────────────────────────────────────────────────────

_SELECT = """
SELECT d.*, a.name AS agent_name, u.display_name AS creator_name, du.display_name AS decider_name,
       c.type AS channel_type,
       p.id AS p_id, p.code AS p_code, p.display_name AS p_name, p.person_type AS p_type,
       p.organization_name AS p_org,
       g.id AS g_id, g.code AS g_code, g.name AS g_name, gc.type AS g_channel,
       rs.id AS rs_id, rs.code AS rs_code
FROM biz.action_drafts d
LEFT JOIN agent.identities a ON a.id = d.agent_id
LEFT JOIN core.users u ON u.id = d.created_by
LEFT JOIN core.users du ON du.id = d.decided_by
LEFT JOIN core.channels c ON c.id = d.channel_id
LEFT JOIN core.persons p ON d.subject_type = 'person' AND p.id = d.subject_id
LEFT JOIN core.groups g ON g.id = COALESCE(CASE WHEN d.subject_type = 'group' THEN d.subject_id END, d.group_id)
LEFT JOIN core.channels gc ON gc.id = g.channel_id
LEFT JOIN LATERAL (SELECT rc.id, rc.code FROM biz.action_drafts rc
                   WHERE d.status = 'failed' AND rc.parent_draft_id = d.id AND rc.kind = d.kind
                     AND rc.kind IN ('message', 'quotation', 'contract') AND rc.status <> 'rejected'
                   ORDER BY rc.created_at DESC LIMIT 1) rs ON true
"""
# Bản "đã gửi lại": nháp gửi lỗi đã có bản MỚI cùng loại (nối bằng parent_draft_id, `resend_of`) chưa bị huỷ.
# Bản con khác loại (mcp_write/report) là hành động kèm theo của lần duyệt, KHÔNG phải gửi lại. Dùng cho thẻ + bộ đếm
# "Gửi lỗi".
RESENT_SQL = """EXISTS (SELECT 1 FROM biz.action_drafts rc WHERE rc.parent_draft_id = d.id AND rc.kind = d.kind
                         AND rc.kind IN ('message', 'quotation', 'contract') AND rc.status <> 'rejected')"""


def _subject(r: Any) -> dict[str, Any] | None:
    if r.p_id:
        return {"id": str(r.p_id), "code": r.p_code, "name": r.p_name, "type": r.p_type, "org_name": r.p_org}
    if r.g_id and r.subject_type == "group":
        return {"id": str(r.g_id), "code": r.g_code, "name": r.g_name, "channel": r.g_channel}
    return None


def item_payload(r: Any) -> dict[str, Any]:
    return {"id": str(r.id), "code": r.code, "kind": r.kind, "kind_label": KIND_LABELS.get(r.kind, r.kind),
            "title": r.title or KIND_LABELS.get(r.kind, r.kind),
            "agent": {"id": str(r.agent_id), "name": r.agent_name} if r.agent_id else None,
            "created_by": {"id": str(r.created_by), "name": r.creator_name} if r.created_by else None,
            "created_at": iso(r.created_at), "status": r.status, "hold_reason": r.hold_reason,
            "subject": _subject(r),
            "resent_by": {"id": str(r.rs_id), "code": r.rs_code} if r.rs_id else None}


async def list_item(db: AsyncSession, draft_id: uuid.UUID) -> dict[str, Any]:
    r = (await db.execute(text(_SELECT + " WHERE d.id = :i"), {"i": draft_id})).one()
    return item_payload(r)


async def load(db: AsyncSession, draft_id: uuid.UUID) -> Any:
    return (await db.execute(text(_SELECT + " WHERE d.id = :i"), {"i": draft_id})).one_or_none()


# ─── gửi ──────────────────────────────────────────────────────────────────────

def _b64url(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def bridge_permit(claims: dict[str, Any]) -> str:
    """Permit gửi tin theo docs/api/bridge-protocol.md: base64url(JSON claims) "." base64url(HMAC)."""
    head = _b64url(json.dumps(claims, separators=(",", ":"), ensure_ascii=False).encode())
    return f"{head}.{_b64url(crypto.hmac_sign(head.encode()))}"


async def identity_of_person(db: AsyncSession, org_id: uuid.UUID, identity_id: Any, person_id: Any) -> bool:
    """Tài khoản kênh `identity_id` có thuộc `person_id` (cùng tổ chức) không? Đi theo chuỗi `merged_into_id` tới hồ sơ
    còn sống: tài khoản đi theo người khi gộp, nên bản nháp soạn trước khi gộp (người bị gộp) vẫn khớp."""
    row = (await db.execute(text("""
        WITH RECURSIVE chain(id, merged_into_id, depth) AS (
            SELECT id, merged_into_id, 0 FROM core.persons WHERE id = CAST(:p AS uuid) AND org_id = :o
            UNION ALL
            SELECT p.id, p.merged_into_id, c.depth + 1 FROM core.persons p JOIN chain c ON p.id = c.merged_into_id
            WHERE c.depth < 10)
        SELECT 1 FROM core.person_identities pi JOIN core.channels c ON c.id = pi.channel_id
        WHERE pi.id = CAST(:i AS uuid) AND c.org_id = :o AND pi.person_id IN (SELECT id FROM chain)
        LIMIT 1"""), {"i": identity_id, "p": person_id, "o": org_id})).first()
    return row is not None


async def user_account(db: AsyncSession, org_id: uuid.UUID, target: dict[str, Any]) -> Any:
    """Tài khoản nhận của đích 1-1 (dòng có `external_id`, `handle`), hoặc None.

    Ưu tiên `identity_id` trong đích (đúng tài khoản đã nhắn; tài khoản đi theo người khi gộp hồ sơ). Không có thì theo
    `person_id`, đi theo chuỗi `merged_into_id` tới hồ sơ còn sống: bản nháp soạn TRƯỚC khi gộp vẫn gửi được sau khi gộp
    (hồ sơ bị gộp không còn giữ tài khoản nào); đảo ngược gộp thì tự về lại hồ sơ cũ."""
    ident, pid = target.get("identity_id"), target.get("person_id")
    if ident and (not pid or await identity_of_person(db, org_id, ident, pid)):
        # Đích có người thì tài khoản PHẢI thuộc người đó (hoặc hồ sơ sống sau gộp): không tin `identity_id` trần.
        r = (await db.execute(text("""
            SELECT pi.external_id, pi.handle FROM core.person_identities pi JOIN core.channels c ON c.id = pi.channel_id
            WHERE pi.id = CAST(:i AS uuid) AND c.type = :ch AND c.org_id = :o"""),
            {"i": target["identity_id"], "ch": target["channel"], "o": org_id})).one_or_none()
        if r is not None:
            return r
    if not target.get("person_id"):
        return None
    return (await db.execute(text("""
        WITH RECURSIVE chain(id, merged_into_id, depth) AS (
            SELECT id, merged_into_id, 0 FROM core.persons WHERE id = CAST(:p AS uuid)
            UNION ALL
            SELECT p.id, p.merged_into_id, c.depth + 1 FROM core.persons p JOIN chain c ON p.id = c.merged_into_id
            WHERE c.depth < 10)
        SELECT pi.external_id, pi.handle FROM chain JOIN core.person_identities pi ON pi.person_id = chain.id
        JOIN core.channels c ON c.id = pi.channel_id
        WHERE c.type = :ch AND c.org_id = :o ORDER BY chain.depth DESC, pi.first_seen_at LIMIT 1"""),
        {"p": target["person_id"], "ch": target["channel"], "o": org_id})).one_or_none()


async def _thread(db: AsyncSession, org_id: uuid.UUID, target: dict[str, Any]) -> tuple[str, str] | None:
    """(thread_id phía nền tảng, thread_type) của đích gửi."""
    if target.get("group_id"):
        ext = (await db.execute(text("SELECT external_id FROM core.groups WHERE id = :g"),
                                {"g": target["group_id"]})).scalar_one_or_none()
        return (ext, "group") if ext else None
    acc = await user_account(db, org_id, target)
    return (acc.external_id, "user") if acc is not None else None


async def _dispatch_send(db: AsyncSession, bus: EventBus | None, r: Any, body_text: str) -> dict[str, Any] | None:
    """Cấp permit dùng một lần và đẩy lệnh gửi sang bridge. Trả send_result nếu thất bại ngay."""
    target = (r.body or {}).get("target")
    if not target:
        return {"ok": False, "error": "NO_TARGET"}
    thread = await _thread(db, r.org_id, target)
    if thread is None:
        return {"ok": False, "error": "TARGET_NOT_FOUND"}
    session_id = (await db.execute(text("""
        SELECT s.id FROM core.channel_sessions s JOIN core.channels c ON c.id = s.channel_id
        WHERE c.org_id = :o AND c.type = :t AND s.state = 'active' AND s.ended_at IS NULL
        ORDER BY s.started_at DESC LIMIT 1"""), {"o": r.org_id, "t": target["channel"]})).scalar_one_or_none()
    if session_id is None:
        return {"ok": False, "error": "SESSION_NOT_ACTIVE"}
    exp = int(time.time()) + policy.PERMIT_TTL_S
    nonce = uuid.uuid4().hex
    claims = {"nonce": nonce, "draft_id": str(r.id), "channel": target["channel"], "thread_id": thread[0],
              "thread_type": thread[1], "body_sha256": hashlib.sha256(body_text.encode()).hexdigest(), "exp": exp}
    token = bridge_permit(claims)
    await db.execute(text("""UPDATE biz.action_drafts SET permit_hash = :h, permit_expires_at = to_timestamp(:e)
                             WHERE id = :i"""), {"h": hashlib.sha256(token.encode()).digest(), "e": exp, "i": r.id})
    if bus is not None:
        await bus.publish(BRIDGE_OUTBOUND, "message.send",
                          {"channel": target["channel"], "session_id": str(session_id), "thread_id": thread[0],
                           "thread_type": thread[1], "text": body_text, "permit": token},
                          actor="system:drafts", org_id=r.org_id, correlation_id=str(r.id))
    return None


async def report_send_failure(db: AsyncSession, redis: Redis | None, *, org_id: uuid.UUID, draft_id: uuid.UUID,
                              code: str | None, title: str, draft_code: str,
                              decided_by: uuid.UUID | None = None, created_by: uuid.UUID | None = None) -> None:
    """Gửi lỗi (F048): chuông cho người duyệt, người soạn và mọi Owner + sự kiện WS `draft.failed` có câu tiếng Việt;
    mã gốc (`error`) đi kèm để màn hiện "Chi tiết kỹ thuật". Không mang nội dung tin hay tên khách ra ngoài."""
    message = send_error_message(code)
    owners = await notifications.owner_ids(db, org_id)
    others = [u for u in dict.fromkeys((decided_by, created_by)) if u is not None and u not in owners]
    body = f"{message} Mở Bàn làm việc, thẻ Gửi lỗi để gửi lại."
    link = f"/workbench?status=failed&id={draft_id}"
    # Tiêu đề bản nháp có thể chứa số điện thoại/số tài khoản: Owner thấy nguyên văn, vai khác thấy bản đã che
    # (cùng quy tắc `mask_text` của GET /drafts, ranh giới "hide_sensitive_below_owner").
    await notifications.notify(db, org_id, owners, kind="draft.failed", title=f"Tin chưa gửi được: {title}"[:160],
                               body=body, link=link, redis=redis)
    if others:
        await notifications.notify(db, org_id, others, kind="draft.failed",
                                   title=f"Tin chưa gửi được: {mask_text(title, False)}"[:160],
                                   body=body, link=link, redis=redis)
    if redis is not None:
        await realtime.publish(redis, "draft.failed",
                               {"id": str(draft_id), "code": draft_code, "title": mask_text(title, False),
                                "error": str(code)[:80] if code else None,
                                "message": message}, org_id=org_id)


async def expire_stale_permits(db: AsyncSession, redis: Redis | None = None) -> list[uuid.UUID]:
    """Giai đoạn 5.4 (chịu lỗi, spec M7): bridge rớt giữa chừng khi đang gửi (mất kết nối trước khi gửi lại
    `send.result` cho `on_send_result`) từng khiến bản nháp kẹt ở `approved`/`edited` MÃI MÃI — không rõ đã gửi
    hay chưa, không thấy trong hàng đợi lỗi. Quét định kỳ (xem `gh.app._permit_sweep_loop`): permit dùng một
    lần đã hết hạn (`permit_expires_at < now()`) mà chưa được dùng (`permit_used_at IS NULL`) → đánh dấu
    `failed` rõ ràng với lý do `PERMIT_EXPIRED`, vào Action Log, đẩy WS để Owner thấy và có thể tạo bản nháp
    gửi lại. Không có bản ghi nào bị xoá hay mất — chỉ chuyển từ "không rõ" sang "lỗi, cần gửi lại"."""
    rows = (await db.execute(text("""
        UPDATE biz.action_drafts SET status = :failed, send_result = CAST(:r AS jsonb), updated_at = now()
        WHERE status IN (:approved, :edited) AND permit_used_at IS NULL
          AND permit_expires_at IS NOT NULL AND permit_expires_at < now()
        RETURNING id, org_id, code, title, autonomy_level, decided_by, created_by"""),
        {"failed": FAILED, "approved": APPROVED, "edited": EDITED,
         "r": orjson.dumps({"ok": False, "error": "PERMIT_EXPIRED", "message": send_error_message("PERMIT_EXPIRED"),
                            "at": datetime.now(UTC).isoformat()}).decode()})).all()
    for row in rows:
        await actionlog.record(db, org_id=row.org_id, actor_type="system", actor_id="system:drafts",
                               action="draft.failed", target_type="draft", target_id=str(row.id),
                               target_label=f"{row.code} · {row.title}", autonomy_level=row.autonomy_level,
                               result="failed", detail={"error": "PERMIT_EXPIRED"})
        if redis is not None:
            await realtime.publish(redis, "draft.updated",
                                   {"id": str(row.id), "status": FAILED,
                                    "send_result": {"ok": False, "error": "PERMIT_EXPIRED",
                                                    "message": send_error_message("PERMIT_EXPIRED")}},
                                   org_id=row.org_id)
        await report_send_failure(db, redis, org_id=row.org_id, draft_id=row.id, code="PERMIT_EXPIRED",
                                  title=row.title, draft_code=row.code, decided_by=row.decided_by,
                                  created_by=row.created_by)
    from gh.owner_channel import autosend  # v0.1.72: sổ tin tự gửi cũng có permit hết hạn

    await autosend.expire_stale(db)
    return [row.id for row in rows]


async def on_send_result(db: AsyncSession, org_id: uuid.UUID, payload: dict[str, Any],
                         redis: Redis | None = None) -> None:
    """`send.result` từ bridge → trạng thái `sent` / `failed` của bản nháp (dùng một lần: chỉ lần đầu có tác dụng)."""
    try:
        draft_id = uuid.UUID(str(payload.get("draft_id")))
    except ValueError:
        return
    ok = bool(payload.get("ok"))
    result: dict[str, Any] = {"ok": ok, "error": payload.get("error"),
                              "external_msg_id": payload.get("external_msg_id"), "at": datetime.now(UTC).isoformat()}
    if not ok:
        result["message"] = send_error_message(result["error"])    # câu cho Sếp; mã gốc ở `error` (Chi tiết kỹ thuật)
    row = (await db.execute(text("""
        UPDATE biz.action_drafts SET status = :s, send_result = CAST(:r AS jsonb), permit_used_at = now(),
               sent_at = CASE WHEN :ok THEN now() END, updated_at = now()
        WHERE id = :i AND org_id = :o AND status IN ('approved', 'edited') AND permit_used_at IS NULL
        RETURNING id, code, title, autonomy_level, decided_by, created_by"""),
        {"s": SENT if ok else FAILED, "r": orjson.dumps(result).decode(), "ok": ok, "i": draft_id,
         "o": org_id})).one_or_none()
    if row is None:
        return
    await actionlog.record(db, org_id=org_id, actor_type="system", actor_id="system:drafts",
                           action="draft.sent" if ok else "draft.failed", target_type="draft",
                           target_id=str(row.id), target_label=f"{row.code} · {row.title}",
                           autonomy_level=row.autonomy_level, result="ok" if ok else "failed", detail=result)
    if redis is not None:
        await realtime.publish(redis, "draft.updated", {"id": str(row.id), "status": SENT if ok else FAILED,
                                                        "send_result": result}, org_id=org_id)
    if not ok:
        await report_send_failure(db, redis, org_id=org_id, draft_id=row.id, code=result["error"], title=row.title,
                                  draft_code=row.code, decided_by=row.decided_by, created_by=row.created_by)


# ─── quyết định ───────────────────────────────────────────────────────────────

async def decide(db: AsyncSession, *, draft_id: uuid.UUID, org_id: uuid.UUID, user_id: uuid.UUID, verdict: str,
                 text_override: str | None = None, reason: str | None = None,
                 side_actions: dict[str, bool] | None = None, bus: EventBus | None = None,
                 redis: Redis | None = None) -> str:
    """Duyệt (`approve`), sửa rồi gửi (`edit`) hoặc huỷ (`reject`). Trả trạng thái mới.

    Khoá dòng bằng `FOR UPDATE` → hai người bấm cùng lúc chỉ một người quyết (người sau nhận 409 DRAFT_DECIDED).
    """
    r = (await db.execute(text("SELECT * FROM biz.action_drafts WHERE id = :i AND org_id = :o FOR UPDATE"),
                          {"i": draft_id, "o": org_id})).one_or_none()
    if r is None:
        raise DraftError(404, "NOT_FOUND", "Bản nháp không tồn tại")
    if r.status != PENDING:
        raise DraftError(409, "DRAFT_DECIDED", "Bản nháp này đã được quyết định")
    if verdict != "reject" and r.kind == "mcp_write":
        # Giữ nguyên `pending` (chưa ghi gì): chưa nối bước gọi tool ghi sau duyệt thì không được báo "đã thực hiện".
        raise DraftError(409, "MCP_WRITE_UNSUPPORTED", MCP_WRITE_UNSUPPORTED)
    body = dict(r.body or {})
    if verdict == "reject":
        status = REJECTED
    else:
        status = EDITED if verdict == "edit" else APPROVED
        if verdict == "edit":
            if not (text_override or "").strip():
                raise DraftError(422, "VALIDATION", "Nội dung gửi không được để trống")
            versions = list(body.get("versions") or [])
            versions.append({"at": datetime.now(UTC).isoformat(), "by": "agent" if r.agent_id else "user",
                             "text": body.get("text", "")})
            body = {**body, "text": text_override, "versions": versions}
    toggles = side_actions or {}
    actions = [{**a, "on": bool(toggles.get(a.get("key"), a.get("on")))} for a in (r.side_actions or [])]
    await db.execute(text("""
        UPDATE biz.action_drafts SET status = :s, decided_by = :u, decided_at = now(), decision_reason = :why,
               body = CAST(:b AS jsonb), side_actions = CAST(:sa AS jsonb), updated_at = now()
        WHERE id = :i"""), {"s": status, "u": user_id, "why": reason, "b": orjson.dumps(body).decode(),
                            "sa": orjson.dumps(actions).decode(), "i": draft_id})
    label = f"{r.code} · {r.title or KIND_LABELS.get(r.kind, r.kind)}"
    await actionlog.record(db, org_id=org_id, actor_type="user", actor_id=f"user:{user_id}",
                           action={"approve": "draft.approved", "edit": "draft.edited", "reject": "draft.rejected"}[
                               verdict], target_type="draft", target_id=str(draft_id), target_label=label,
                           autonomy_level=r.autonomy_level, result="ok", detail={"reason": reason})
    subject = (r.subject_type, r.subject_id) if r.subject_type and r.subject_id else None
    if status != REJECTED:
        for a in actions:
            if not a.get("on") or not a.get("action_key"):
                continue
            t = policy.action_type(a["action_key"])
            if t.writes_external or t.personnel_related:
                await create_draft(db, org_id=org_id, kind="mcp_write" if a["action_key"] == "mcp.write" else "report",
                                   title=a.get("label", ""), body_text=a.get("label", ""),
                                   action_key=a["action_key"], created_by=user_id, subject=subject,
                                   parent_draft_id=draft_id, redis=redis)
            else:
                await _execute_internal(db, org_id, draft_id, a["action_key"], a.get("label", ""), subject, user_id,
                                        a.get("params"))
        if r.kind in SENDABLE:
            fresh = (await db.execute(text("SELECT * FROM biz.action_drafts WHERE id = :i"), {"i": draft_id})).one()
            failed = await _dispatch_send(db, bus, fresh, body.get("text", ""))
            if failed is not None:
                await db.execute(text("""UPDATE biz.action_drafts SET status = 'failed',
                                         send_result = CAST(:r AS jsonb), updated_at = now() WHERE id = :i"""),
                                 {"r": orjson.dumps({**failed, "message": send_error_message(failed["error"]),
                                                     "at": datetime.now(UTC).isoformat()}).decode(),
                                  "i": draft_id})
                await actionlog.record(db, org_id=org_id, actor_type="system", actor_id="system:drafts",
                                       action="draft.failed", target_type="draft", target_id=str(draft_id),
                                       target_label=label, autonomy_level=r.autonomy_level, result="failed",
                                       detail=failed)
                status = FAILED
                await report_send_failure(db, redis, org_id=org_id, draft_id=draft_id, code=failed["error"],
                                          title=r.title or KIND_LABELS.get(r.kind, r.kind), draft_code=r.code,
                                          decided_by=user_id, created_by=r.created_by)
        else:
            await _execute_internal(db, org_id, draft_id, r.action_key or KIND_ACTION[r.kind],
                                    r.title or body.get("text", ""), subject, user_id)
            await db.execute(text("""UPDATE biz.action_drafts SET status = 'sent', sent_at = now(), updated_at = now()
                                     WHERE id = :i"""), {"i": draft_id})
            status = SENT
    if redis is not None:
        cur = (await db.execute(text("SELECT send_result FROM biz.action_drafts WHERE id = :i"),
                                {"i": draft_id})).scalar_one()
        await realtime.publish(redis, "draft.updated", {"id": str(draft_id), "status": status, "send_result": cur},
                               org_id=org_id)
    return status


def permit_matches(token: str, stored_hash: bytes | None) -> bool:
    return stored_hash is not None and hmac.compare_digest(hashlib.sha256(token.encode()).digest(), stored_hash)
