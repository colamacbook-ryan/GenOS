"""Sao lưu & khôi phục trên giao diện (v0.1.20) — Điều khiển hệ thống › Dữ liệu & lưu trữ.

- Danh sách: đọc đúng danh mục `gh.backup` giữ trong ObjectStore (`backups/manifest.json`) — cùng nơi `genh backup`,
  lịch chạy của worker và bản tự sao lưu trước `genh update` ghi vào.
- "Sao lưu ngay": xếp hàng `gh.backup.backup_now` cho worker arq (pg_dump có thể lâu); tiến trình ở Redis
  `gh.backup.JOB_KEY`, Console hỏi lại (polling).
- Tải về: chỉ Owner + phiên PIN; trả đúng bytes ĐÃ MÃ HOÁ (như `genh backup --to`) — không giải mã ra ngoài.
- Khôi phục: api KHÔNG tự khôi phục CSDL mà chính nó đang dùng. Giống nút "Cập nhật ngay" (update.py), api chỉ để
  lại `request/restore.json` {key} trong hộp thư chung với genh trên máy chủ; watcher chạy `genh handle-requests` →
  `genh restore --if-requested`: sao lưu an toàn → dừng api/worker → khôi phục → migrate → khởi động lại, ghi tiến
  trình vào `restore-status.json`. Cần Owner + PIN + gõ đúng "KHÔI PHỤC".
- Lịch: sửa `core.organizations.settings->'backup'` (cùng chỗ bước 11 trình thiết lập ghi).
"""

import json
import os
import re
import uuid
from datetime import UTC, datetime
from typing import Any, Literal

import orjson
from arq.connections import ArqRedis
from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gh import backup, jobcodec
from gh.auth import rbac, service
from gh.auth.deps import require, require_owner, require_pin
from gh.chassis import actionlog
from gh.chassis.objects import ObjectNotFound, get_object_store
from gh.db import DB
from gh.errors import conflict, field_errors, not_found
from gh.hostlink_io import write_request
from gh.system_api import update as upd

router = APIRouter(tags=["system"])
MANAGE = require("system.manage", rbac.ALL)

CONFIRM_TEXT = "KHÔI PHỤC"
RESTORE_REQUEST = "restore.json"
RESTORE_STATUS = "restore-status.json"
JOB_STALE_SECONDS = 30 * 60
KEY_RE = re.compile(r"^backups/\d{8}T\d{6}Z-[0-9a-f]{8}\.pgcustom\.enc$")
TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def _entry_out(e: backup.BackupEntry) -> dict[str, Any]:
    return {"key": e.key, "taken_at": e.taken_at.astimezone(UTC).isoformat().replace("+00:00", "Z"),
            "size_bytes": e.size_bytes, "trigger": e.trigger, "encrypted": True, "key_id": e.key_id}


async def _job(redis: Any) -> dict[str, Any] | None:
    raw = await redis.get(backup.JOB_KEY)
    if not raw:
        return None
    job: dict[str, Any] = orjson.loads(raw)
    if job.get("state") in ("queued", "running"):
        age = upd._age_seconds(job.get("started_at") or job.get("requested_at"))
        if age is not None and age > JOB_STALE_SECONDS:
            job["state"] = "stalled"
    return job


def _restore_state() -> dict[str, Any]:
    d = upd._dir()
    info = upd._read_json(d / "genh.json") or {}
    status = upd._read_json(d / RESTORE_STATUS) or {}
    request = upd._read_json(d / "request" / RESTORE_REQUEST)
    state = status.get("state") or "idle"
    if request is not None:
        age = upd._age_seconds(request.get("requested_at"))
        state = "stalled" if age is not None and age > upd.STALE_REQUEST_SECONDS else "requested"
    requests = info.get("requests") or []
    return {
        "can_request": bool(info.get("updater")) and "restore" in requests and os.access(d / "request", os.W_OK),
        "state": state,
        "key": (request or {}).get("key") or status.get("key") or None,
        "safety_key": status.get("safety_key") or None,
        "message": status.get("message") or None,
        "started_at": status.get("started_at") or None,
        "finished_at": status.get("finished_at") or None,
        "requested_at": request.get("requested_at") if request else None,
    }


async def _schedule(db: AsyncSession, org_id: uuid.UUID) -> tuple[dict[str, Any] | None, str]:
    row = (await db.execute(text("SELECT settings -> 'backup' AS cfg, timezone FROM core.organizations WHERE id = :o"),
                            {"o": org_id})).one()
    cfg = row.cfg or None
    sched = ({"frequency": cfg.get("frequency", "daily"), "time_of_day": cfg.get("time_of_day", "02:00")}
             if cfg else None)
    return sched, row.timezone or "UTC"


async def _payload(request: Request, db: AsyncSession, user: service.CurrentUser) -> dict[str, Any]:
    entries = await backup.list_backups()
    schedule, tz = await _schedule(db, user.org_id)
    return {
        "items": [_entry_out(e) for e in entries],
        "schedule": schedule,
        "timezone": tz,
        "retention": {"daily": backup.DAILY_KEEP, "weekly": backup.WEEKLY_KEEP, "monthly": backup.MONTHLY_KEEP,
                      "recent_hours": backup.RECENT_KEEP_HOURS},
        "job": await _job(request.app.state.redis),
        "restore": _restore_state(),
    }


@router.get("/system/backups")
async def list_backups(request: Request, db: AsyncSession = DB,
                       user: service.CurrentUser = Depends(MANAGE)) -> dict[str, Any]:
    """Các bản sao lưu còn giữ (mới nhất trước), lịch chạy, việc "Sao lưu ngay" và tiến trình khôi phục."""
    return await _payload(request, db, user)


@router.post("/system/backups", status_code=202)
async def backup_now(request: Request, db: AsyncSession = DB,
                     user: service.CurrentUser = Depends(MANAGE)) -> dict[str, Any]:
    """Xếp hàng một lần sao lưu cho worker. 409 BACKUP_IN_PROGRESS nếu đang có lần khác chưa xong."""
    redis = request.app.state.redis
    job = await _job(redis)
    if (job and job.get("state") in ("queued", "running")) or await redis.exists(backup.LOCK_KEY):
        raise conflict("BACKUP_IN_PROGRESS", "Đang sao lưu — chờ xong rồi thử lại")
    job_id = str(uuid.uuid4())
    await redis.set(backup.JOB_KEY, orjson.dumps({"id": job_id, "state": "queued", "by": user.actor_id,
                                                  "requested_at": datetime.now(UTC).isoformat()}),
                    ex=7 * 24 * 3600)
    await ArqRedis(pool_or_conn=redis.connection_pool, job_serializer=jobcodec.dumps,
                   job_deserializer=jobcodec.loads).enqueue_job("backup_now", trigger="manual", _job_id=job_id)
    await actionlog.record(db, org_id=user.org_id, actor_type="user", actor_id=user.actor_id,
                           action="backup.requested", target_type="system", target_id="backup",
                           detail={"job_id": job_id}, ip=user.ip)
    await db.commit()
    return await _payload(request, db, user)


async def _known_entry(key: str) -> backup.BackupEntry:
    if not KEY_RE.match(key):
        raise not_found("Bản sao lưu")
    entry = next((e for e in await backup.list_backups() if e.key == key), None)
    if entry is None:
        raise not_found("Bản sao lưu")
    return entry


@router.get("/system/backups/download")
async def download_backup(key: str = Query(..., max_length=200), db: AsyncSession = DB,
                          user: service.CurrentUser = Depends(require_owner),
                          _pin: Any = Depends(require_pin("backup.download"))) -> Response:
    """Tải tệp sao lưu ĐÃ MÃ HOÁ (chỉ Owner, cần PIN) — lưu trữ ngoài máy chủ; chỉ khôi phục được bằng khoá của
    bản cài này."""
    entry = await _known_entry(key)
    try:
        data = await get_object_store().get(entry.key)
    except ObjectNotFound as e:
        raise not_found("Tệp sao lưu") from e
    await actionlog.record(db, org_id=user.org_id, actor_type="user", actor_id=user.actor_id,
                           action="backup.downloaded", target_type="backup", target_id=entry.key,
                           detail={"size_bytes": len(data)}, ip=user.ip)
    await db.commit()
    name = entry.key.rsplit("/", 1)[-1]
    return Response(content=data, media_type="application/octet-stream",
                    headers={"Content-Disposition": f'attachment; filename="gen-harness-{name}"',
                             "Cache-Control": "no-store"})


class RestoreIn(BaseModel):
    key: str = Field(min_length=1, max_length=200)
    confirm: str = Field(default="", max_length=40)


@router.post("/system/backups/restore", status_code=202)
async def request_restore(body: RestoreIn, request: Request, db: AsyncSession = DB,
                          user: service.CurrentUser = Depends(require_owner),
                          _pin: Any = Depends(require_pin("backup.restore"))) -> dict[str, Any]:
    """Để lại yêu cầu khôi phục cho genh trên máy chủ (chỉ Owner, cần PIN, gõ đúng "KHÔI PHỤC")."""
    if body.confirm.strip() != CONFIRM_TEXT:
        raise field_errors({"confirm": f'Gõ đúng "{CONFIRM_TEXT}" để xác nhận'})
    entry = await _known_entry(body.key)
    s = _restore_state()
    if not s["can_request"]:
        raise conflict("RESTORE_UNAVAILABLE",
                       "Máy chủ chưa bật nhận yêu cầu khôi phục từ Console — chạy `genh update` một lần trên máy chủ")
    if s["state"] in ("requested", "running"):
        raise conflict("RESTORE_IN_PROGRESS", "Đang khôi phục — chờ xong rồi thử lại")
    if upd._state()["state"] in ("requested", "running"):
        raise conflict("UPDATE_IN_PROGRESS", "Đang cập nhật phiên bản — chờ xong rồi thử lại")
    req = {"id": str(uuid.uuid4()), "key": entry.key, "requested_at": datetime.now(UTC).isoformat(),
           "by": user.actor_id}
    write_request(upd._dir() / "request", RESTORE_REQUEST, req)
    await actionlog.record(db, org_id=user.org_id, actor_type="user", actor_id=user.actor_id,
                           action="backup.restore_requested", target_type="backup", target_id=entry.key,
                           detail={"request_id": req["id"], "taken_at": entry.taken_at.isoformat()}, ip=user.ip)
    await db.commit()
    return await _payload(request, db, user)


class ScheduleIn(BaseModel):
    frequency: Literal["daily", "weekly", "monthly"]
    time_of_day: str = Field(max_length=5)


@router.put("/system/backups/schedule")
async def put_schedule(body: ScheduleIn, request: Request, db: AsyncSession = DB,
                       user: service.CurrentUser = Depends(MANAGE)) -> dict[str, Any]:
    """Đổi lịch sao lưu tự động — cùng chỗ bước 11 trình thiết lập ghi (`settings->'backup'`)."""
    if not TIME_RE.match(body.time_of_day):
        raise field_errors({"time_of_day": "Giờ chạy sao lưu dạng HH:MM (00:00–23:59)"})
    before, _tz = await _schedule(db, user.org_id)
    patch = {"frequency": body.frequency, "time_of_day": body.time_of_day}
    await db.execute(text("""
        UPDATE core.organizations SET settings = jsonb_set(settings, '{backup}',
          COALESCE(settings -> 'backup', '{"retention_count": 7, "destination": "local"}'::jsonb)
            || CAST(:p AS jsonb)) WHERE id = :o"""), {"p": json.dumps(patch), "o": user.org_id})
    await actionlog.record(db, org_id=user.org_id, actor_type="user", actor_id=user.actor_id,
                           action="backup.schedule_changed", target_type="system", target_id="backup",
                           detail={"from": before, "to": patch}, ip=user.ip)
    await db.commit()
    return await _payload(request, db, user)
