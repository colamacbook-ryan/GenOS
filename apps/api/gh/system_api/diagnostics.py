"""/system/diagnostics — "Gói chẩn đoán" từ Console (v0.1.44, F-4b). CHỈ Owner (+ `system.manage`).

Container api không chạy được `genh doctor` (cần docker/hệ thống máy chủ) ⇒ Console để lại YÊU CẦU trong hộp thư
chung `<gốc cài đặt>/run` (như cập nhật/khôi phục): genh handle-requests thấy `request/doctor.json` thì chạy
`genh doctor` (đã lọc bí mật), ghi tiến trình vào `doctor-status.json` và tệp zip vào `run/diagnostics/` (giữ 3 bản).

- `GET` — trạng thái (idle|pending|running|done|failed) + tệp mới nhất; `supported` khi genh.json có 'doctor'; `stale`
  khi đang chờ/chạy đã quá 15 phút (genh không nhận yêu cầu) — Console thôi chờ, cho tạo lại + hiện lệnh chạy tay.
- `POST` (PIN `diagnostics.download`) — ghi `request/doctor.json` {schema:1, request_id: 16 hex, requested_at}; genh
  chưa hỗ trợ ⇒ 409 DIAG_UNSUPPORTED; đang chờ/chạy < 15 phút ⇒ 409 DIAG_BUSY.
- `GET /download` (PIN) — chỉ phục vụ tên `genh-doctor-YYYYMMDDTHHMMSSZ.zip` (tên đọc từ doctor-status.json — dữ liệu
  run/ 0777 KHÔNG tin cậy), mở O_NOFOLLOW, phải là tệp thường ≤ 200 MB; sai ⇒ 404 DIAG_NOT_READY / 409 DIAG_FILE_UNSAFE.
"""

import os
import re
import secrets
import stat
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from gh.auth import rbac, service
from gh.auth.deps import require, require_owner, require_pin
from gh.chassis import actionlog
from gh.db import DB
from gh.errors import ApiError, conflict
from gh.hostlink_io import write_request
from gh.system_api import update as upd

router = APIRouter(prefix="/system/diagnostics", tags=["system"])
MANAGE = require("system.manage", rbac.ALL)

STATUS_FILE = "doctor-status.json"
REQUEST_FILE = "doctor.json"
DIAG_DIR = "diagnostics"
FILE_RE = re.compile(r"^genh-doctor-\d{8}T\d{6}Z\.zip$")
REQUEST_ID_RE = re.compile(r"^[0-9a-f]{16}$")
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
CODE_RE = re.compile(r"^[A-Z][A-Z0-9_-]{0,47}$")
MAX_BYTES = 200 * 1024 * 1024
BUSY_SECONDS = 15 * 60
CHUNK = 1 << 16
COMMAND = "genh doctor"
UNSUPPORTED_DETAIL = "Bản genh trên máy chủ chưa hỗ trợ — chạy genh doctor trên máy chủ"


def _iso_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _str(v: Any, limit: int = 300) -> str | None:
    return v[:limit] if isinstance(v, str) and v else None


def _ts(v: Any) -> str | None:
    t = upd._ts(v) if isinstance(v, str) else None
    return t.astimezone(UTC).isoformat().replace("+00:00", "Z") if t else None


def _supported() -> bool:
    d = upd._dir()
    info = upd._read_json(d / "genh.json") or {}
    reqs = info.get("requests")
    return isinstance(reqs, list) and "doctor" in reqs and os.access(d / "request", os.W_OK)


def _state() -> dict[str, Any]:
    """Khuôn GET/POST. Mọi giá trị từ run/ chỉ nhận đúng kiểu/dạng."""
    d = upd._dir()
    status = upd._read_json(d / STATUS_FILE) or {}
    req = upd._read_json(d / "request" / REQUEST_FILE)
    st_state = status.get("state") if status.get("state") in ("running", "done", "failed") else None
    st_rid = status.get("request_id") if isinstance(status.get("request_id"), str) else None
    req_rid = req.get("request_id") if req and isinstance(req.get("request_id"), str) else None
    if req is not None and (st_state is None or st_rid != req_rid):
        state = "pending"
    else:
        state = st_state or "idle"
    rid = req_rid if state == "pending" else st_rid
    raw_file = status.get("file")
    file_name = raw_file if isinstance(raw_file, str) and FILE_RE.fullmatch(raw_file) else None
    size = status.get("size_bytes")
    sha = status.get("sha256")
    code = status.get("error_code")
    done = state == "done"
    return {
        "supported": _supported(),
        "state": state,
        "request_id": rid if isinstance(rid, str) and REQUEST_ID_RE.fullmatch(rid) else None,
        "requested_at": _ts(req.get("requested_at")) if req else None,
        "started_at": _ts(status.get("started_at")) if state != "pending" else None,
        "finished_at": _ts(status.get("finished_at")) if state != "pending" else None,
        "file_name": file_name if done else None,
        "size_bytes": size if done and isinstance(size, int) and not isinstance(size, bool) and size >= 0 else None,
        "sha256": sha if done and isinstance(sha, str) and SHA_RE.fullmatch(sha) else None,
        "error_code": code if state == "failed" and isinstance(code, str) and CODE_RE.fullmatch(code) else None,
        "message": _str(status.get("message")) if state == "failed" else None,
        "command": COMMAND,
    }


def _busy(s: dict[str, Any]) -> bool:
    if s["state"] not in ("pending", "running"):
        return False
    at = s["requested_at"] if s["state"] == "pending" else (s["started_at"] or s["requested_at"])
    age = upd._age_seconds(at)
    return age is None or age < BUSY_SECONDS


def _stale(s: dict[str, Any]) -> bool:
    """Đang chờ/chạy nhưng quá BUSY_SECONDS ⇒ genh trên máy chủ không nhận/làm xong (watcher không chạy, máy thiếu
    linger, genh stop…). Console thôi chờ, cho tạo lại và hiện lệnh chạy tay."""
    return s["state"] in ("pending", "running") and not _busy(s)


def _view() -> dict[str, Any]:
    s = _state()
    s["stale"] = _stale(s)
    return s


@router.get("")
async def get_diagnostics(_m: service.CurrentUser = Depends(MANAGE),
                          user: service.CurrentUser = Depends(require_owner)) -> dict[str, Any]:
    return _view()


@router.post("", status_code=202)
async def request_diagnostics(_m: service.CurrentUser = Depends(MANAGE),
                              user: service.CurrentUser = Depends(require_owner),
                              _p: service.CurrentUser = Depends(require_pin("diagnostics.download")),
                              db: AsyncSession = DB) -> dict[str, Any]:
    s = _state()
    if not s["supported"]:
        raise conflict("DIAG_UNSUPPORTED", "Chưa tạo được gói chẩn đoán từ Console", UNSUPPORTED_DETAIL)
    if _busy(s):
        raise conflict("DIAG_BUSY", "Đang tạo gói chẩn đoán — chờ xong rồi tải")
    rid = secrets.token_hex(8)
    write_request(upd._dir() / "request", REQUEST_FILE,
                  {"schema": 1, "request_id": rid, "requested_at": _iso_now()})
    await actionlog.record(db, org_id=user.org_id, actor_type="user", actor_id=user.actor_id,
                           action="system.diagnostics.request", target_type="system", target_id="diagnostics",
                           detail={"request_id": rid}, ip=user.ip)
    await db.commit()
    return _view()


def _not_ready() -> ApiError:
    return ApiError(404, "DIAG_NOT_READY", "Chưa có gói chẩn đoán để tải — bấm Tạo gói chẩn đoán trước")


def _unsafe() -> ApiError:
    return conflict("DIAG_FILE_UNSAFE", "Tệp gói chẩn đoán trên máy chủ không an toàn để tải — chạy genh doctor lại "
                                        "trên máy chủ")


def _open_zip(name: str) -> tuple[int, int]:
    """(fd, kích thước). Thư mục và tệp đều không được là liên kết mềm; tệp thường ≤ MAX_BYTES.

    api và genh cùng ghi được run/ ⇒ mở THƯ MỤC bằng O_NOFOLLOW|O_DIRECTORY rồi mở tệp tương đối với fd đó (dir_fd):
    không còn khe giữa lúc kiểm và lúc mở để ai đó tráo run/diagnostics thành liên kết mềm trỏ ra chỗ khác."""
    d = upd._dir() / DIAG_DIR
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    if d.is_symlink():
        raise _unsafe()
    if not d.is_dir():
        raise _not_ready()
    try:
        dfd = os.open(d, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | nofollow)
    except FileNotFoundError:
        raise _not_ready() from None
    except OSError:
        raise _unsafe() from None
    try:
        fd = os.open(name, os.O_RDONLY | nofollow | getattr(os, "O_NONBLOCK", 0), dir_fd=dfd)
    except FileNotFoundError:
        raise _not_ready() from None
    except OSError:
        raise _unsafe() from None
    finally:
        os.close(dfd)
    st = os.fstat(fd)
    if not stat.S_ISREG(st.st_mode) or st.st_size > MAX_BYTES:
        os.close(fd)
        raise _unsafe()
    return fd, st.st_size


def _stream(fd: int) -> Iterator[bytes]:
    with os.fdopen(fd, "rb") as f:
        while chunk := f.read(CHUNK):
            yield chunk


@router.get("/download")
async def download_diagnostics(_m: service.CurrentUser = Depends(MANAGE),
                               user: service.CurrentUser = Depends(require_owner),
                               _p: service.CurrentUser = Depends(require_pin("diagnostics.download")),
                               db: AsyncSession = DB) -> StreamingResponse:
    status = upd._read_json(upd._dir() / STATUS_FILE) or {}
    name = status.get("file")
    if status.get("state") != "done" or not isinstance(name, str):
        raise _not_ready()
    if not FILE_RE.fullmatch(name):
        raise _unsafe()
    fd, size = _open_zip(name)
    try:
        await actionlog.record(db, org_id=user.org_id, actor_type="user", actor_id=user.actor_id,
                               action="system.diagnostics.download", target_type="system", target_id="diagnostics",
                               target_label=name, detail={"file": name, "size_bytes": size}, ip=user.ip)
        await db.commit()
    except BaseException:
        os.close(fd)
        raise
    return StreamingResponse(_stream(fd), media_type="application/zip",
                             headers={"Content-Disposition": f'attachment; filename="{name}"',
                                      "Content-Length": str(size), "Cache-Control": "no-store"})
