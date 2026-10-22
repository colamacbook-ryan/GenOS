"""/audit — Nhật ký hành động (đọc) và kiểm chuỗi băm.

`query_log`/`export_rows`/`team_actor_ids` cũng được `gh.system_api.routes` dùng lại cho tab "Nhật ký" của Điều
khiển hệ thống (`/system/audit-log`, `/system/audit-log/export`) — cùng một đường đọc, không viết lại SQL."""

import base64
import json
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gh.auth import rbac, service
from gh.auth.deps import require
from gh.chassis import actionlog
from gh.db import DB
from gh.errors import ApiError

router = APIRouter(prefix="/audit", tags=["audit"])


def _cursor_encode(at: datetime, id_: Any) -> str:
    return base64.urlsafe_b64encode(json.dumps([at.isoformat(), str(id_)]).encode()).decode()


def _cursor_decode(c: str) -> tuple[datetime, str]:
    try:
        at, id_ = json.loads(base64.urlsafe_b64decode(c.encode()))
        return datetime.fromisoformat(at), id_
    except (ValueError, TypeError) as e:
        raise ApiError(400, "BAD_CURSOR", "Con trỏ phân trang không hợp lệ") from e


async def team_actor_ids(db: AsyncSession, user: service.CurrentUser) -> list[str]:
    rows = (await db.execute(text("""SELECT 'user:' || user_id::text FROM core.user_roles
                                     WHERE team_id IS NOT DISTINCT FROM :t AND :t IS NOT NULL"""),
                             {"t": user.team_id})).scalars().all()
    return [*rows, user.actor_id]


# Dòng "ồn" mà Nhật ký ẩn mặc định (F105): dòng chung `http.*` do ActionLogGuard ghi khi route chưa có dòng riêng, và
# lượt kiểm chuỗi băm ban đêm KHI chuỗi nguyên vẹn. Kiểm chuỗi bị đứt (result != ok) luôn hiện.
NOISE_SQL = "NOT (a.action LIKE 'http.%' OR (a.action = 'audit.chain_verified' AND a.result = 'ok'))"
_ESC = "!"


def _like_pattern(s: str) -> str:
    """Chuỗi người dùng gõ → mẫu ILIKE "chứa" (thoát %, _ và ký tự thoát; ESCAPE '!')."""
    return "%" + s.replace(_ESC, _ESC * 2).replace("%", _ESC + "%").replace("_", _ESC + "_") + "%"


def split_codes(raw: str | None) -> list[str]:
    """"a,b,c" → ["a","b","c"] (bỏ trống/trùng, tối đa 60 mã) — mã hành động/tác nhân web gửi kèm ô tìm."""
    out: list[str] = []
    for part in (raw or "").split(","):
        part = part.strip()
        if part and part not in out:
            out.append(part)
    return out[:60]


async def _log_filters(db: AsyncSession, user: service.CurrentUser, *, actor_type: str | None, action: str | None,
                       target_type: str | None, target_id: str | None, since: datetime | None,
                       until: datetime | None, q: str | None = None, q_actions: str | None = None,
                       q_actors: str | None = None, hide_noise: bool = False) -> tuple[list[str], dict[str, Any]]:
    where = ["a.org_id = :o"]
    params: dict[str, Any] = {"o": user.org_id}
    if user.permissions.get("audit.read") == rbac.TEAM:
        where.append("a.actor_id = ANY(:actors)")
        params["actors"] = await team_actor_ids(db, user)
    if actor_type:
        where.append("a.actor_type = :at")
        params["at"] = actor_type
    if action:
        where.append("a.action LIKE :ac")
        params["ac"] = action.replace("%", "").replace("_", r"\_") + "%"
    if target_type:
        where.append("a.target_type = :tt")
        params["tt"] = target_type
    if target_id:
        where.append("a.target_id = :ti")
        params["ti"] = target_id
    if since:
        where.append("a.at >= :since")
        params["since"] = since
    if until:
        where.append("a.at < :until")
        params["until"] = until
    if hide_noise:
        where.append(NOISE_SQL)
    needle = (q or "").strip()
    if needle:
        # Ô tìm chạy ở máy chủ trên TOÀN BỘ nhật ký (không chỉ trang đã tải): khớp tên người/agent, mã hành động,
        # tên đối tượng; web gửi kèm mã hành động (`q_actions`) và mã tác nhân hệ thống (`q_actors`) mà nhãn
        # TIẾNG VIỆT khớp chữ Sếp gõ (vd "đăng nhập" → auth.login), vì máy chủ chỉ lưu mã.
        params["qq"] = _like_pattern(needle)
        params["qa"] = split_codes(q_actions)
        params["qac"] = split_codes(q_actors)
        where.append(f"""(COALESCE(u.display_name, ag.name, a.actor_id) ILIKE :qq ESCAPE '{_ESC}'
                         OR a.action ILIKE :qq ESCAPE '{_ESC}' OR a.target_label ILIKE :qq ESCAPE '{_ESC}'
                         OR a.action = ANY(:qa) OR a.actor_id = ANY(:qac))""")
    return where, params


_ROW_SELECT = """
    SELECT a.id, a.at, a.actor_type, a.actor_id, a.action, a.target_type, a.target_id, a.target_label,
           a.autonomy_level, a.result, a.detail,
           COALESCE(u.display_name, ag.name, a.actor_id) AS actor_label
    FROM ops.action_log a
    LEFT JOIN core.users u ON a.actor_type = 'user' AND u.id::text = substr(a.actor_id, 6)
    LEFT JOIN agent.identities ag ON a.actor_type = 'agent' AND ag.id::text = substr(a.actor_id, 7)
"""


def _row_out(r: Any) -> dict[str, Any]:
    return {"id": str(r.id), "at": r.at.isoformat(), "actor_type": r.actor_type, "actor_id": r.actor_id,
            "actor_label": r.actor_label, "action": r.action, "target_type": r.target_type,
            "target_id": r.target_id, "target_label": r.target_label, "autonomy_level": r.autonomy_level,
            "result": r.result, "detail": r.detail}


async def query_log(db: AsyncSession, user: service.CurrentUser, *, cursor: str | None, limit: int,
                    actor_type: str | None = None, action: str | None = None, target_type: str | None = None,
                    target_id: str | None = None, since: datetime | None = None,
                    until: datetime | None = None, q: str | None = None, q_actions: str | None = None,
                    q_actors: str | None = None, hide_noise: bool = False) -> dict[str, Any]:
    """Tìm Action Log theo actor/hành động/đối tượng/thời gian, phân trang cursor — dùng chung cho `/audit` và
    `/system/audit-log` (thiết kế: bảng `[auditLog]`)."""
    where, params = await _log_filters(db, user, actor_type=actor_type, action=action, target_type=target_type,
                                       target_id=target_id, since=since, until=until, q=q, q_actions=q_actions,
                                       q_actors=q_actors, hide_noise=hide_noise)
    params["lim"] = limit + 1
    if cursor:
        c_at, c_id = _cursor_decode(cursor)
        where.append("(a.at, a.id) < (:c_at, CAST(:c_id AS uuid))")
        params |= {"c_at": c_at, "c_id": c_id}
    rows = (await db.execute(text(f"""{_ROW_SELECT}
        WHERE {' AND '.join(where)}
        ORDER BY a.at DESC, a.id DESC LIMIT :lim"""), params)).all()  # noqa: S608 — where chỉ gồm chuỗi cố định
    more = len(rows) > limit
    rows = rows[:limit]
    items = [_row_out(r) for r in rows]
    return {"items": items, "next_cursor": _cursor_encode(rows[-1].at, rows[-1].id) if more and rows else None}


async def export_rows(db: AsyncSession, user: service.CurrentUser, *, actor_type: str | None = None,
                      action: str | None = None, target_type: str | None = None, target_id: str | None = None,
                      since: datetime | None = None, until: datetime | None = None,
                      q: str | None = None, q_actions: str | None = None, q_actors: str | None = None,
                      hide_noise: bool = False, limit: int = 100_000) -> list[dict[str, Any]]:
    """Toàn bộ dòng khớp bộ lọc (không phân trang) để xuất CSV — `/system/audit-log/export`."""
    where, params = await _log_filters(db, user, actor_type=actor_type, action=action, target_type=target_type,
                                       target_id=target_id, since=since, until=until, q=q, q_actions=q_actions,
                                       q_actors=q_actors, hide_noise=hide_noise)
    params["lim"] = limit
    rows = (await db.execute(text(f"""{_ROW_SELECT}
        WHERE {' AND '.join(where)}
        ORDER BY a.at DESC, a.id DESC LIMIT :lim"""), params)).all()  # noqa: S608 — where chỉ gồm chuỗi cố định
    return [_row_out(r) for r in rows]


@router.get("")
async def list_audit(cursor: str | None = None, limit: int = Query(50, ge=1, le=200),
                     actor_type: str | None = None, action: str | None = None,
                     target_type: str | None = None, target_id: str | None = None,
                     since: datetime | None = None, until: datetime | None = None,
                     q: str | None = Query(None, max_length=100), q_actions: str | None = Query(None, max_length=4000),
                     q_actors: str | None = Query(None, max_length=2000), hide_noise: bool = False,
                     user: service.CurrentUser = Depends(require("audit.read")),
                     db: AsyncSession = DB) -> dict[str, Any]:
    """`q` = ô tìm (người/agent, hành động, đối tượng) chạy trên toàn bộ nhật ký; `q_actions`/`q_actors` = mã (cách
    nhau dấu phẩy) mà nhãn tiếng Việt khớp ô tìm; `hide_noise` ẩn dòng chung `http.*` và lượt kiểm chuỗi băm ổn."""
    return await query_log(db, user, cursor=cursor, limit=limit, actor_type=actor_type, action=action,
                           target_type=target_type, target_id=target_id, since=since, until=until, q=q,
                           q_actions=q_actions, q_actors=q_actors, hide_noise=hide_noise)


@router.get("/verify")
async def verify(user: service.CurrentUser = Depends(require("audit.read", rbac.ALL)),
                 db: AsyncSession = DB) -> dict[str, Any]:
    report = await actionlog.verify_chain(db, user.org_id)
    return {"ok": report.ok, "checked": report.checked,
            "broken_at": str(report.broken_at) if report.broken_at else None}
