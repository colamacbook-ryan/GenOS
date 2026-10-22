"""Action Log bất biến có chuỗi băm (ARCHITECTURE §8.4).

Mọi hành động của người, agent, plugin và hệ thống đi qua `record()` — đây là đường ghi duy nhất.
Mỗi dòng: row_hash = sha256(prev_hash || nội dung chuẩn hoá). Bảng chỉ INSERT (trigger DB).
"""

import contextvars
import hashlib
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import orjson
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

ACTOR_TYPES = ("user", "agent", "system", "plugin")
RESULTS = ("ok", "held", "blocked", "failed")

# Middleware HTTP đặt một danh sách vào đây; record() ghi dấu để biết request ghi đã có dòng nhật ký chưa.
request_marker: contextvars.ContextVar[list[uuid.UUID] | None] = contextvars.ContextVar("gh_log_marker",
                                                                                         default=None)


def _canonical(row: dict[str, Any]) -> bytes:
    return orjson.dumps(row, option=orjson.OPT_SORT_KEYS | orjson.OPT_NON_STR_KEYS, default=str)


def compute_hash(prev_hash: bytes | None, row: dict[str, Any]) -> bytes:
    return hashlib.sha256((prev_hash or b"") + _canonical(row)).digest()


def _content(*, id: uuid.UUID, org_id: uuid.UUID, at: datetime, actor_type: str, actor_id: str, action: str,
             target_type: str | None, target_id: str | None, target_label: str | None,
             autonomy_level: int | None, result: str, detail: dict[str, Any], ip: str | None) -> dict[str, Any]:
    return {"id": str(id), "org_id": str(org_id), "at": at.isoformat(), "actor_type": actor_type,
            "actor_id": actor_id, "action": action, "target_type": target_type, "target_id": target_id,
            "target_label": target_label, "autonomy_level": autonomy_level, "result": result,
            "detail": detail, "ip": ip}


async def record(db: AsyncSession, *, org_id: uuid.UUID, actor_type: str, actor_id: str, action: str,
                 target_type: str | None = None, target_id: str | None = None, target_label: str | None = None,
                 autonomy_level: int | None = None, result: str = "ok", detail: dict[str, Any] | None = None,
                 ip: str | None = None) -> uuid.UUID:
    if actor_type not in ACTOR_TYPES:
        raise ValueError(f"actor_type không hợp lệ: {actor_type}")
    if result not in RESULTS:
        raise ValueError(f"result không hợp lệ: {result}")
    detail = detail or {}
    # Khoá theo tổ chức trong transaction → chuỗi băm tuần tự, không rẽ nhánh.
    await db.execute(text("SELECT pg_advisory_xact_lock(hashtext('action_log:' || :org))"), {"org": str(org_id)})
    head = (await db.execute(text("SELECT core.uuid_v7() AS id, clock_timestamp() AS at"))).one()
    prev = (await db.execute(
        text("SELECT row_hash FROM ops.action_log WHERE org_id = :org ORDER BY at DESC, id DESC LIMIT 1"),
        {"org": org_id})).scalar_one_or_none()
    content = _content(id=head.id, org_id=org_id, at=head.at, actor_type=actor_type, actor_id=actor_id,
                       action=action, target_type=target_type, target_id=target_id, target_label=target_label,
                       autonomy_level=autonomy_level, result=result, detail=detail, ip=ip)
    row_hash = compute_hash(prev, content)
    await db.execute(text("""
        INSERT INTO ops.action_log (id, org_id, at, actor_type, actor_id, action, target_type, target_id,
            target_label, autonomy_level, result, detail, ip, prev_hash, row_hash)
        VALUES (:id, :org_id, :at, :actor_type, :actor_id, :action, :target_type, :target_id, :target_label,
            :autonomy_level, :result, CAST(:detail AS jsonb), CAST(:ip AS inet), :prev_hash, :row_hash)"""),
        {**content, "id": head.id, "org_id": org_id, "at": head.at, "detail": orjson.dumps(detail).decode(),
         "prev_hash": prev, "row_hash": row_hash})
    marker = request_marker.get()
    if marker is not None:
        marker.append(head.id)
    return head.id


def exempt() -> None:
    """Request ghi KHÔNG phải một hành động nghiệp vụ (vd đánh dấu đã đọc thông báo của chính mình): báo
    `ActionLogGuard` không ghi dòng `http.*` chung, tránh làm loãng Nhật ký. Chỉ dùng cho thay đổi riêng tư,
    không ảnh hưởng dữ liệu người khác."""
    marker = request_marker.get()
    if marker is not None:
        marker.append(uuid.UUID(int=0))


@dataclass
class ChainReport:
    ok: bool
    checked: int
    broken_at: str | None = None


async def verify_chain(db: AsyncSession, org_id: uuid.UUID, batch: int = 5000) -> ChainReport:
    prev: bytes | None = None
    checked = 0
    last_at, last_id = None, None
    while True:
        params: dict[str, Any] = {"org": org_id, "lim": batch}
        cond = ""
        if last_at is not None:
            cond = "AND (at, id) > (:last_at, :last_id)"
            params.update(last_at=last_at, last_id=last_id)
        rows = (await db.execute(text(f"""
            SELECT id, org_id, at, actor_type, actor_id, action, target_type, target_id, target_label,
                   autonomy_level, result, detail, host(ip) AS ip, prev_hash, row_hash
            FROM ops.action_log WHERE org_id = :org {cond} ORDER BY at, id LIMIT :lim"""), params)).all()
        if not rows:
            return ChainReport(ok=True, checked=checked)
        for r in rows:
            content = _content(id=r.id, org_id=r.org_id, at=r.at, actor_type=r.actor_type, actor_id=r.actor_id,
                               action=r.action, target_type=r.target_type, target_id=r.target_id,
                               target_label=r.target_label, autonomy_level=r.autonomy_level, result=r.result,
                               detail=r.detail, ip=r.ip)
            if (r.prev_hash or None) != prev or compute_hash(prev, content) != r.row_hash:
                return ChainReport(ok=False, checked=checked, broken_at=str(r.id))
            prev = r.row_hash
            checked += 1
            last_at, last_id = r.at, r.id
