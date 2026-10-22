"""/notifications — chuông thông báo ở header (v0.1.23, Đợt B6; bảng core.notifications — migration 0017).

Mỗi thông báo thuộc đúng MỘT người nhận; API chỉ đọc/đánh dấu thông báo của chính người đang đăng nhập (kể cả
Owner cũng không đọc thông báo của người khác). Ghi thông báo qua `notify()` trong cùng transaction với việc gây
ra nó; sự kiện WebSocket `notification.new` chỉ gửi tới người nhận (`to_user`) để chuông cập nhật ngay.
"""

import asyncio
import logging
import uuid
from collections.abc import Iterable
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession

from gh import realtime
from gh.auth import service
from gh.auth.deps import current_user
from gh.chassis import actionlog
from gh.db import DB

log = logging.getLogger("gh.notifications")
router = APIRouter(prefix="/notifications", tags=["notifications"])

EVENT = "notification.new"
#: v0.1.69: sự cố đóng ⇒ chuông của nó đổi sang đã đọc / "Đã xử lý" — mang đúng một `NotificationItem` đã cập nhật.
EVENT_RESOLVED = "notification.resolved"
# Người nhận lọc bằng `to_user` ở Hub.dispatch; không cần quyền riêng.
realtime.register_event(EVENT, None)
realtime.register_event(EVENT_RESOLVED, None)

MAX_TITLE, MAX_BODY, MAX_LINK = 160, 1000, 300
_PENDING = "gh_pending_notifications"
_tasks: set[asyncio.Task[None]] = set()


async def _publish_all(redis: Any, items: list[tuple[uuid.UUID, uuid.UUID, str, dict[str, Any]]]) -> None:
    for org_id, uid, ev, item in items:
        try:
            await realtime.publish(redis, ev, item, org_id=org_id, to_user=uid)
        except Exception:  # noqa: BLE001 — chuông vẫn thấy khi tải lại
            log.warning("Không đẩy được %s", ev, exc_info=True)


def _after_commit(session: Any) -> None:
    # SQLAlchemy 2 bắn `after_commit` cả khi RELEASE một savepoint (`begin_nested`) — lúc đó transaction ngoài chưa
    # commit, chưa được đẩy (v0.1.27: nhắc việc mỗi dòng một savepoint).
    if session.in_nested_transaction():
        return
    items = session.info.pop(_PENDING, None) or []
    if not items:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    by_redis: dict[int, tuple[Any, list[tuple[uuid.UUID, uuid.UUID, str, dict[str, Any]]]]] = {}
    for r, o, u, ev, it in items:
        by_redis.setdefault(id(r), (r, []))[1].append((o, u, ev, it))
    for r, batch in by_redis.values():
        task = loop.create_task(_publish_all(r, batch))
        _tasks.add(task)
        task.add_done_callback(_tasks.discard)


def _after_rollback(session: Any) -> None:
    if session.in_nested_transaction():  # hoàn tác savepoint: bên gọi tự bỏ phần của mình (`pending_reset`)
        return
    session.info.pop(_PENDING, None)


def _queue_publish(db: AsyncSession, redis: Any, org_id: uuid.UUID, uid: uuid.UUID, item: dict[str, Any],
                   ev: str = EVENT) -> None:
    """Chỉ đẩy WS SAU KHI transaction commit (rollback → bỏ), để client không nhận thông báo chưa/không tồn tại."""
    sync = db.sync_session
    if not event.contains(sync, "after_commit", _after_commit):
        event.listen(sync, "after_commit", _after_commit)
        event.listen(sync, "after_rollback", _after_rollback)
    pending: list[tuple[Any, uuid.UUID, uuid.UUID, str, dict[str, Any]]] = sync.info.setdefault(_PENDING, [])
    pending.append((redis, org_id, uid, ev, item))


def pending_mark(db: AsyncSession) -> list[Any]:
    """Ảnh chụp các sự kiện WS đang chờ commit — dùng với `pending_reset` khi hoàn tác một savepoint có gọi
    `notify()` (hoàn tác savepoint không xoá hàng chờ — xem `_after_rollback`)."""
    return list(db.sync_session.info.get(_PENDING) or [])


def pending_reset(db: AsyncSession, mark: list[Any]) -> None:
    info = db.sync_session.info
    if mark:
        info[_PENDING] = list(mark)
    else:
        info.pop(_PENDING, None)


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat().replace("+00:00", "Z") if dt else None


def _out(r: Any) -> dict[str, Any]:
    # v0.1.69 (F098): `resolved` = sự cố sinh ra chuông này đã đóng ("Đã xử lý") — chuông vẫn nằm trong danh sách để Sếp
    # biết đã có chuyện, nhưng không còn đỏ/chưa đọc.
    resolved_at = getattr(r, "resolved_at", None)
    return {"id": str(r.id), "kind": r.kind, "title": r.title, "body": r.body, "link": r.link,
            "created_at": _iso(r.created_at), "read": r.read_at is not None,
            "resolved": resolved_at is not None, "resolved_at": _iso(resolved_at)}


async def owner_ids(db: AsyncSession, org_id: uuid.UUID) -> list[uuid.UUID]:
    """Các Owner còn hoạt động của tổ chức — người nhận mặc định của thông báo hệ thống (sao lưu …)."""
    rows = (await db.execute(text("""
        SELECT u.id FROM core.users u JOIN core.user_roles ur ON ur.user_id = u.id
        JOIN core.roles r ON r.id = ur.role_id
        WHERE u.org_id = :o AND u.is_active AND u.deleted_at IS NULL AND r.code = 'owner'"""),
        {"o": org_id})).scalars().all()
    return list(rows)


async def notify(db: AsyncSession, org_id: uuid.UUID, user_ids: Iterable[uuid.UUID], *, kind: str, title: str,
                 body: str = "", link: str | None = None, redis: Any = None,
                 incident_key: str | None = None) -> list[dict[str, Any]]:
    """Ghi một thông báo cho từng người nhận; có `redis` thì đẩy `notification.new` SAU KHI `db` commit (rollback
    → không đẩy; lỗi đẩy không làm hỏng việc chính — chuông vẫn thấy khi tải lại). `incident_key` (sự cố sức khoẻ sinh
    ra chuông, `health.raise_once`) để `resolve_incident` tắt đúng chuông của sự cố khi nó đóng."""
    out: list[dict[str, Any]] = []
    for uid in dict.fromkeys(user_ids):
        row = (await db.execute(text("""
            INSERT INTO core.notifications (org_id, user_id, kind, title, body, link, created_at, incident_key)
            VALUES (:o, :u, :k, :t, :b, :l, clock_timestamp(), :ik)
            RETURNING id, kind, title, body, link, created_at, read_at, resolved_at"""),
            {"o": org_id, "u": uid, "k": kind, "t": title[:MAX_TITLE], "b": body[:MAX_BODY],
             "l": (link or None) and link[:MAX_LINK], "ik": incident_key})).one()
        item = _out(row)
        out.append(item)
        if redis is not None:
            _queue_publish(db, redis, org_id, uid, item)
    return out


async def resolve_incident(db: AsyncSession, org_id: uuid.UUID, *, kind: str, title: str | None = None,
                           label: bool = True, key: str | None = None, redis: Any = None) -> int:
    """v0.1.69 (F098): sự cố sức khoẻ đóng ⇒ chuông của chính sự cố đó tắt — `read_at` (hết đỏ, hết đếm "chưa đọc") và,
    khi `label`, `resolved_at` ("Đã xử lý" ở chuông). `key` ⇒ chuông của đúng khoá sự cố đó (`incident_key`); chuông cũ
    chưa có khoá (tạo trước v0.1.69) rơi về cách cũ: `title` khác None ⇒ chỉ chuông có đúng tiêu đề đó (nhiều khoá cùng
    `kind`, vd. `job.timeout:<tên hàm>`: đóng một khoá không được tắt chuông của khoá còn mở), None ⇒ mọi chuông của
    `kind`.
    Không đụng chuông đã gắn nhãn; chạy lại an toàn. Bên gọi commit. Có `redis` thì đẩy `notification.resolved` (item đã
    cập nhật) tới chủ chuông SAU KHI commit để chuông đang mở đổi ngay. Trả số chuông đã đổi."""
    res = await db.execute(text("""
        UPDATE core.notifications
           SET read_at = COALESCE(read_at, now()),
               resolved_at = CASE WHEN :lab THEN now() ELSE resolved_at END
         WHERE org_id = :o AND kind = :k AND resolved_at IS NULL AND (:lab OR read_at IS NULL)
           AND (incident_key = :key
                OR (incident_key IS NULL OR CAST(:key AS text) IS NULL) AND (CAST(:t AS text) IS NULL OR title = :t))
        RETURNING id, user_id, kind, title, body, link, created_at, read_at, resolved_at"""),
        {"o": org_id, "k": kind, "t": title, "lab": label, "key": key})
    rows = res.all()
    if redis is not None:
        for r in rows:
            _queue_publish(db, redis, org_id, r.user_id, _out(r), EVENT_RESOLVED)
    return len(rows)


# Hạn lưu (v0.1.27): đã đọc quá 30 ngày → xoá; mọi thông báo (kể cả chưa đọc) quá 90 ngày → xoá. Job worker
# `purge_notifications` chạy hằng ngày, xoá theo lô để không giữ khoá lâu trên bảng.
READ_RETENTION_DAYS = 30
MAX_RETENTION_DAYS = 90
PURGE_BATCH = 5000


async def purge_old(db: AsyncSession, *, read_days: int = READ_RETENTION_DAYS,
                    max_days: int = MAX_RETENTION_DAYS, batch: int = PURGE_BATCH, commit_each: bool = False) -> int:
    """Xoá thông báo đã đọc tạo trước `read_days` ngày và mọi thông báo tạo trước `max_days` ngày. Bên gọi commit;
    `commit_each=True` (job worker) commit sau MỖI lô — khoá dòng không bị giữ tới hết cả lượt xoá."""
    total = 0
    while True:
        n = (await db.execute(text("""
            DELETE FROM core.notifications WHERE id IN (
              SELECT id FROM core.notifications
              WHERE (read_at IS NOT NULL AND created_at < now() - make_interval(days => :r))
                 OR created_at < now() - make_interval(days => :m)
              LIMIT :n)"""), {"r": read_days, "m": max_days, "n": batch})).rowcount  # type: ignore[attr-defined]
        total += int(n or 0)
        if commit_each:
            await db.commit()
        if not n or n < batch:
            return total


async def _unread(db: AsyncSession, user: service.CurrentUser) -> int:
    n: int = (await db.execute(text("""SELECT count(*) FROM core.notifications
                                       WHERE org_id = :o AND user_id = :u AND read_at IS NULL"""),
                               {"o": user.org_id, "u": user.id})).scalar_one()
    return n


async def _total(db: AsyncSession, user: service.CurrentUser) -> int:
    n: int = (await db.execute(text("SELECT count(*) FROM core.notifications WHERE org_id = :o AND user_id = :u"),
                               {"o": user.org_id, "u": user.id})).scalar_one()
    return n


@router.get("")
async def list_notifications(limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0),
                             user: service.CurrentUser = Depends(current_user),
                             db: AsyncSession = DB) -> dict[str, Any]:
    """`unread` đếm TOÀN BỘ chưa đọc, `items` chỉ `limit` mục mới nhất — `total` cho chuông biết còn bao nhiêu để hiện
    "Xem thêm" (F098: từng ghi 35 chưa đọc mà chỉ thấy 20). `offset` lật trang khi có quá 100 thông báo."""
    rows = (await db.execute(text("""
        SELECT id, kind, title, body, link, created_at, read_at, resolved_at FROM core.notifications
        WHERE org_id = :o AND user_id = :u ORDER BY created_at DESC, id DESC LIMIT :l OFFSET :f"""),
        {"o": user.org_id, "u": user.id, "l": limit, "f": offset})).all()
    return {"items": [_out(r) for r in rows], "unread": await _unread(db, user), "total": await _total(db, user)}


class ReadIn(BaseModel):
    """`ids` rỗng/không gửi = đánh dấu TẤT CẢ đã đọc."""
    ids: list[uuid.UUID] | None = Field(default=None, max_length=200)


@router.post("/read")
async def mark_read(body: ReadIn, user: service.CurrentUser = Depends(current_user),
                    db: AsyncSession = DB) -> dict[str, Any]:
    params: dict[str, Any] = {"o": user.org_id, "u": user.id}
    where = "org_id = :o AND user_id = :u AND read_at IS NULL"
    if body.ids:
        where += " AND id = ANY(:ids)"
        params["ids"] = list(body.ids)
    await db.execute(text(f"UPDATE core.notifications SET read_at = now() WHERE {where}"), params)  # noqa: S608
    actionlog.exempt()  # thao tác riêng tư, không phải hành động nghiệp vụ — không ghi Nhật ký
    return {"unread": await _unread(db, user)}
