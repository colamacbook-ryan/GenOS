"""Bản sao ngoài máy (v0.1.40 — F-12): ổ USB/NAS giữ một gói `.ghbundle` mã hoá mỗi tuần, ngoài ổ đĩa của máy chủ.

Việc chép ra ổ ngoài là của genh trên máy chủ (container api không thấy ổ USB/NAS): giống "Cập nhật ngay" (update.py)
và "Khôi phục" (backups.py), Console chỉ để lại YÊU CẦU `request/offsite.json` {id, action: set|run|disable, path?,
requested_at, by} trong hộp thư chung `<gốc cài đặt>/run`; watcher chạy `genh offsite …` rồi ghi kết quả vào
`offsite-status.json`. Hợp đồng tệp: docs/api/system-offsite.md.

run/ là 0777 — API coi MỌI giá trị đọc từ đó là KHÔNG tin cậy: chỉ nhận state/error_code/schedule trong tập cho phép
(còn lại 'unknown'), chuỗi `dest` cắt ≤ 200 ký tự và bỏ ký tự điều khiển, thông điệp hiển thị do API tự ghép theo
error_code từ bảng tiếng Việt cố định — KHÔNG lấy chữ từ tệp.

Khoá khôi phục (`Settings.offsite_key_file`, Docker secret) chỉ rời máy chủ khi Owner + PIN: "Bộ khôi phục" (xem khoá)
và "Tải gói mang đi" (gói .ghbundle mã hoá bằng chính khoá đó, tạo bằng `python -m gh.bundle export`; mật khẩu chỉ
qua biến môi trường tiến trình con, không qua argv). Không bao giờ log/ghi khoá vào nhật ký thao tác.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import logging
import os
import re
import sys
import tempfile
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.types import Receive, Scope, Send

from gh import health
from gh.auth import rbac, service
from gh.auth.deps import require, require_owner, require_pin
from gh.chassis import actionlog
from gh.config import get_settings, offsite_key
from gh.db import DB
from gh.errors import ApiError, conflict, field_errors
from gh.hostlink_io import write_request
from gh.system_api import update as upd

log = logging.getLogger(__name__)
router = APIRouter(tags=["system"])
READ = require("system.read")
MANAGE = require("system.manage", rbac.ALL)

STATUS_FILE = "offsite-status.json"
REQUEST_FILE = "offsite.json"
ACTIONS = ("set", "run", "disable")
STATES = ("ok", "failed", "not_mounted", "not_configured", "running", "skipped_busy")
SCHEDULES = ("systemd", "cron", "launchd", "schtasks")
DEST_MAX = 200
PATH_MAX = 400
#: 'running' mà lần thử bắt đầu quá lâu ⇒ genh đã chết giữa chừng (máy tắt…) — không chặn yêu cầu mới.
RUNNING_STALE_SECONDS = 2 * 3600
_KEY_ID = re.compile(r"^[0-9a-f]{8}$")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_WIN_ABS = re.compile(r"^[A-Za-z]:[\\/]")

#: Lệnh Owner tự chạy trên máy chủ khi chưa có watcher nhận yêu cầu (Windows / máy cài trước v0.1.40) — theo đúng
#: việc đang yêu cầu. `set` chỉ có lệnh khi biết đường dẫn thật (không bao giờ đưa lệnh chứa chỗ giữ chỗ).
MANUAL_COMMANDS = {"run": "genh offsite run", "disable": "genh offsite disable", "set": 'genh offsite set "{path}"'}
#: Ký tự làm lệnh trong dấu nháy kép đổi nghĩa (bash/PowerShell: thay biến, chạy lệnh con) ⇒ không ghép lệnh.
_UNSAFE_IN_QUOTES = re.compile(r'["$`]')

#: Thông điệp theo mã lỗi genh (GH-EBxx, GH-E94C) — bảng cố định, KHÔNG lấy chữ từ tệp run/.
ERROR_MESSAGES = {
    "GH-EB00": "Chưa chọn nơi lưu bản sao ngoài máy",
    "GH-EB01": "Chưa thấy ổ USB/NAS — cắm lại ổ rồi bấm 'Sao lưu ra ổ ngoài ngay'",
    "GH-EB02": "Không xuất được gói dữ liệu — thử lại sau ít phút",
    "GH-EB03": "Bản sao vừa tạo không đọc lại được — chưa có bản sao ngoài máy",
    "GH-EB04": "Không ghi được vào nơi lưu — kiểm tra ổ còn chỗ trống và cho phép ghi",
    "GH-EB05": "Máy chủ đang cập nhật/khôi phục — lần sao lưu ra ổ ngoài sẽ thử lại sau",
    "GH-EB06": "Dịch vụ Gen-Harness chưa chạy — bật lại rồi thử lại",
    "GH-EB07": "Nơi lưu không hợp lệ — chọn lại thư mục trên ổ USB/NAS",
    # v0.1.53 (F-97): genh không xoá được tệp yêu cầu trong run/request nên KHÔNG làm yêu cầu (tránh chạy lặp) — thử
    # lại/cắm ổ không chữa được; phải sửa quyền thư mục.
    "GH-E94C": ("Máy chủ không xoá được tệp yêu cầu — chưa làm gì. Nhờ người quản trị kiểm quyền thư mục run/request "
                "trong thư mục cài đặt rồi thử lại"),
}
FAILED_GENERIC = "Lần sao lưu ra ổ ngoài gần nhất chưa thành công"
STATE_MESSAGES = {
    "ok": "Bản sao ngoài máy gần nhất đã kiểm đọc lại được",
    "running": "Đang sao lưu ra ổ ngoài…",
    "not_configured": "Chưa chọn nơi lưu bản sao ngoài máy",
    "skipped_busy": "Lần trước bỏ qua vì máy chủ đang cập nhật/khôi phục — sẽ thử lại lần sau",
    "unknown": "Không đọc được trạng thái bản sao ngoài máy",
}

RECOVERY_STEPS = [
    "Cài Gen-Harness trên máy mới theo hướng dẫn cài đặt (chưa cần tạo dữ liệu gì).",
    "Cắm ổ USB (hoặc mở thư mục NAS) chứa bản sao ngoài máy, chọn tệp .ghbundle mới nhất.",
    "Chạy trên máy mới: genh import --yes <tệp .ghbundle>",
    "Khi được hỏi mật khẩu gói, nhập Khoá khôi phục này (gõ đủ cả dấu '-').",
    "Đăng nhập Console bằng tài khoản Owner cũ và kiểm tra dữ liệu.",
]
RECOVERY_WARNING = "Cất Bộ khôi phục TÁCH khỏi ổ USB: ai có cả hai sẽ đọc được toàn bộ dữ liệu"

PORTABLE_LOCK_KEY = "gh:offsite:portable:lock"
PORTABLE_LOCK_TTL = 2 * 3600
PORTABLE_TIMEOUT_SECONDS = 30 * 60
PORTABLE_DIR_NAME = "gh-portable"
PORTABLE_FAILED_TITLE = "Không tạo được gói mang đi"
#: Thư mục apps/api (chứa gói `gh`) — cwd của `python -m gh.bundle`.
API_DIR = str(Path(__file__).resolve().parents[2])


# ─── đọc hộp thư (không tin cậy) ─────────────────────────────────────────────────────────────────────────────

def _pick(value: Any, allowed: tuple[str, ...]) -> str:
    return value if isinstance(value, str) and value in allowed else "unknown"


def _iso(value: Any) -> str | None:
    t = health._parse_ts(value)
    return t.astimezone(UTC).isoformat().replace("+00:00", "Z") if t is not None else None


def _error_code(value: Any) -> str | None:
    """'' / thiếu ⇒ None; mã trong bảng ⇒ giữ; còn lại ⇒ 'unknown'."""
    if value in (None, ""):
        return None
    return value if isinstance(value, str) and value in ERROR_MESSAGES else "unknown"


def message_for(state: str, error_code: str | None) -> str:
    """Thông điệp hiển thị — chỉ ghép từ bảng cố định."""
    if state in ("failed", "not_mounted"):
        code = error_code or ("GH-EB01" if state == "not_mounted" else None)
        return ERROR_MESSAGES.get(code or "", FAILED_GENERIC)
    return STATE_MESSAGES.get(state, STATE_MESSAGES["unknown"])


def read_status(d: Path, *, now: datetime | None = None) -> dict[str, Any]:
    """Trạng thái bản sao ngoài máy đã lọc từ `run/offsite-status.json` (thiếu tệp ⇒ chưa cấu hình)."""
    now = now or datetime.now(UTC)
    raw = upd._read_json(d / STATUS_FILE)
    if raw is None:
        state, configured, err = "not_configured", False, None
        raw = {}
    else:
        state = _pick(raw.get("state"), STATES)
        configured = raw.get("configured") is True
        err = _error_code(raw.get("error_code"))
    dest_raw = raw.get("dest")
    dest = _CONTROL.sub("", dest_raw)[:DEST_MAX] if isinstance(dest_raw, str) else ""
    last_success = health._parse_ts(raw.get("last_success_at"))
    age_days = int((now - last_success).total_seconds() // 86400) if last_success is not None else None
    stale = last_success is None or now - last_success > _stale_after()
    size = raw.get("last_size_bytes")
    key_id = raw.get("key_id")
    return {
        "configured": configured,
        "dest": dest or None,
        "state": state,
        "error_code": err,
        "message": message_for(state, err),
        "last_attempt_at": _iso(raw.get("last_attempt_at")),
        "last_success_at": _iso(raw.get("last_success_at")),
        "age_days": max(age_days, 0) if age_days is not None else None,
        "stale": stale,
        "last_size_bytes": size if isinstance(size, int) and not isinstance(size, bool) and size >= 0 else None,
        "verified": raw.get("verified") is True,
        "key_id": key_id if isinstance(key_id, str) and _KEY_ID.match(key_id) else None,
        "schedule": raw.get("schedule") if raw.get("schedule") in SCHEDULES else None,
    }


def _stale_after() -> timedelta:
    return health.OFFSITE_STALE_AFTER


def _request_raw(d: Path) -> dict[str, Any] | None:
    return upd._read_json(d / "request" / REQUEST_FILE)


def _request_state(d: Path, req: dict[str, Any] | None = None) -> dict[str, Any]:
    req = _request_raw(d) if req is None else req
    if req is None:
        return {"state": "idle", "action": None, "requested_at": None}
    age = upd._age_seconds(req.get("requested_at"))
    stalled = age is not None and age > upd.STALE_REQUEST_SECONDS and not upd._host_busy(d)
    action = req.get("action")
    return {"state": "stalled" if stalled else "requested",
            "action": action if action in ACTIONS else None,
            "requested_at": _iso(req.get("requested_at"))}


def _can_request(d: Path) -> bool:
    info = upd._read_json(d / "genh.json") or {}
    requests = info.get("requests")
    return (bool(info.get("updater")) and isinstance(requests, list) and "offsite" in requests
            and os.access(d / "request", os.W_OK))


def manual_command(action: str | None, path: str | None = None) -> str | None:
    """Lệnh chạy tay trên máy chủ cho đúng việc đang yêu cầu (run/disable/set). `set` cần đường dẫn đầy đủ dùng được
    trong dấu nháy kép (không `"`, `$`, `` ` ``, ký tự điều khiển, ≤ PATH_MAX byte) — không có ⇒ None (web chỉ hiện câu
    hướng dẫn, KHÔNG đưa lệnh chứa chỗ giữ chỗ để Owner chép nhầm)."""
    if action not in MANUAL_COMMANDS:
        return None
    if action != "set":
        return MANUAL_COMMANDS[action]
    if (not isinstance(path, str) or not path.strip() or _CONTROL.search(path) or _UNSAFE_IN_QUOTES.search(path)
            or len(path.encode("utf-8")) > PATH_MAX or not _is_absolute(path)):
        return None
    return MANUAL_COMMANDS["set"].format(path=path)


def state(*, now: datetime | None = None) -> dict[str, Any]:
    """Khuôn GET /system/offsite. `manual_command` chỉ có khi đang có yêu cầu chờ máy chủ nhận — theo action/path của
    chính yêu cầu đó (tệp run/ không tin cậy ⇒ path qua cùng bộ lọc như khi ghép lệnh)."""
    d = upd._dir()  # không có hộp thư (dev/test) ⇒ đọc tệp đều None ⇒ chưa cấu hình, không nhận lệnh
    raw = _request_raw(d)
    req = _request_state(d, raw)
    cmd = manual_command(req["action"], raw.get("path")) if isinstance(raw, dict) else None
    return {**read_status(d, now=now), "request": req,
            "can_request": d.is_dir() and _can_request(d),
            "manual_command": cmd,
            "key_present": offsite_key() is not None}


def _running_fresh(st: dict[str, Any], now: datetime) -> bool:
    if st["state"] != "running":
        return False
    started = health._parse_ts(st.get("last_attempt_at"))
    return started is None or (now - started).total_seconds() <= RUNNING_STALE_SECONDS


# ─── API ─────────────────────────────────────────────────────────────────────────────────────────────────────

@router.get("/system/offsite")
async def get_offsite(user: service.CurrentUser = Depends(READ)) -> dict[str, Any]:
    """Trạng thái bản sao ngoài máy (nơi lưu, lần gần nhất, lịch), yêu cầu đang chờ và máy chủ có nhận lệnh không."""
    return state()


class DestinationIn(BaseModel):
    path: str = Field(default="", max_length=4000)


def _check_path(path: str) -> None:
    if not path.strip():
        raise field_errors({"path": "Nhập đường dẫn thư mục trên ổ USB/NAS"})
    if _CONTROL.search(path):
        raise field_errors({"path": "Đường dẫn không được có xuống dòng hoặc ký tự điều khiển"})
    # genh giới hạn theo BYTE (UTF-8) — đường dẫn tiếng Việt có dấu tốn 2–3 byte mỗi ký tự.
    if len(path.encode("utf-8")) > PATH_MAX:
        raise field_errors({"path": f"Đường dẫn quá dài (tối đa {PATH_MAX} byte — chữ có dấu tính 2–3 byte)"})
    if not _is_absolute(path):
        raise field_errors({"path": "Cần đường dẫn đầy đủ, vd /media/usb/gen-harness, D:\\GenHarness hoặc "
                                    "\\\\nas\\sao-luu"})


def _is_absolute(path: str) -> bool:
    return path.startswith("/") or path.startswith("\\\\") or bool(_WIN_ABS.match(path))


def _guard(d: Path, *, action: str, path: str | None = None) -> None:
    """Các 409 dùng chung cho mọi yêu cầu: máy chủ chưa nhận lệnh, đang có yêu cầu, đang cập nhật/khôi phục."""
    from gh.system_api import backups

    if not d.is_dir() or not _can_request(d):
        raise ApiError(409, "OFFSITE_UNAVAILABLE",
                       "Máy chủ chưa nhận lệnh từ Console — chạy lệnh sau một lần trên máy chủ",
                       manual_command=manual_command(action, path))
    now = datetime.now(UTC)
    if _request_state(d)["state"] == "requested" or _running_fresh(read_status(d, now=now), now):
        raise conflict("OFFSITE_IN_PROGRESS", "Đang sao lưu ra ổ ngoài — chờ xong rồi thử lại")
    if upd._state()["state"] in ("requested", "running"):
        raise conflict("UPDATE_IN_PROGRESS", "Đang cập nhật phiên bản — chờ xong rồi thử lại")
    if backups._restore_state()["state"] in ("requested", "running"):
        raise conflict("RESTORE_IN_PROGRESS", "Đang khôi phục dữ liệu — chờ xong rồi thử lại")


def _write_request(d: Path, req: dict[str, Any]) -> None:
    write_request(d / "request", REQUEST_FILE, req)


async def _request(db: AsyncSession, user: service.CurrentUser, action: str, log_action: str,
                   path: str | None = None) -> dict[str, Any]:
    d = upd._dir()
    _guard(d, action=action, path=path)
    req: dict[str, Any] = {"id": str(uuid.uuid4()), "action": action}
    if path is not None:
        req["path"] = path
    req.update({"requested_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"), "by": user.actor_id})
    _write_request(d, req)
    detail: dict[str, Any] = {"request_id": req["id"]}
    if path is not None:
        detail["path"] = path
    await actionlog.record(db, org_id=user.org_id, actor_type="user", actor_id=user.actor_id, action=log_action,
                           target_type="system", target_id="offsite", detail=detail, ip=user.ip)
    await db.commit()
    return state()


@router.put("/system/offsite/destination", status_code=202)
async def put_destination(body: DestinationIn, db: AsyncSession = DB,
                          user: service.CurrentUser = Depends(require_owner),
                          _pin: Any = Depends(require_pin("offsite.destination"))) -> dict[str, Any]:
    """"Chọn nơi lưu bản sao ngoài máy" (chỉ Owner, cần PIN) — genh kiểm đích (khác ổ máy chủ, ghi được) rồi lưu."""
    _check_path(body.path)
    return await _request(db, user, "set", "offsite.destination_requested", path=body.path)


@router.post("/system/offsite/run", status_code=202)
async def run_now(db: AsyncSession = DB, user: service.CurrentUser = Depends(MANAGE)) -> dict[str, Any]:
    """"Sao lưu ra ổ ngoài ngay" — genh xuất gói, chép ra đích, kiểm đọc lại, giữ 4 bản gần nhất."""
    return await _request(db, user, "run", "offsite.run_requested")


@router.post("/system/offsite/disable", status_code=202)
async def disable(db: AsyncSession = DB, user: service.CurrentUser = Depends(require_owner),
                  _pin: Any = Depends(require_pin("offsite.destination"))) -> dict[str, Any]:
    """Tắt bản sao ngoài máy (chỉ Owner, cần PIN). Các bản đã chép ra ổ ngoài giữ nguyên."""
    return await _request(db, user, "disable", "offsite.disable_requested")


def _key_or_409() -> str:
    key = offsite_key()
    if key is None:
        raise conflict("OFFSITE_KEY_MISSING",
                       "Chưa có Khoá khôi phục trên máy chủ — chạy `genh update` một lần trên máy chủ để tạo khoá")
    return key


def key_id(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()[:8]


def _created_hint() -> str | None:
    """Ngày tạo khoá (mtime tệp secret, YYYY-MM-DD) — chỉ để Owner đối chiếu Bộ khôi phục đã cất có còn đúng."""
    try:
        mtime = Path(get_settings().offsite_key_file).stat().st_mtime
    except OSError:
        return None
    return datetime.fromtimestamp(mtime, UTC).date().isoformat()


@router.get("/system/offsite/recovery-kit")
async def recovery_kit(response: Response, db: AsyncSession = DB,
                       user: service.CurrentUser = Depends(require_owner),
                       _pin: Any = Depends(require_pin("offsite.recovery_kit"))) -> dict[str, Any]:
    """"Bộ khôi phục" (chỉ Owner, cần PIN): Khoá khôi phục + các bước dựng lại trên máy mới. Không lưu đệm."""
    response.headers["Cache-Control"] = "no-store"
    key = _key_or_409()
    kid = key_id(key)
    await actionlog.record(db, org_id=user.org_id, actor_type="user", actor_id=user.actor_id,
                           action="offsite.recovery_kit_viewed", target_type="system", target_id="offsite",
                           detail={"key_id": kid}, ip=user.ip)
    await db.commit()
    return {"key": key, "key_id": kid, "created_hint": _created_hint(), "steps": list(RECOVERY_STEPS),
            "warning": RECOVERY_WARNING}


# ─── Tải gói mang đi ────────────────────────────────────────────────────────────────────────────────────────

async def _exec(*argv: str, env: dict[str, str], cwd: str) -> Any:
    """Tiến trình con (tách riêng để test thay bằng tiến trình giả)."""
    return await asyncio.create_subprocess_exec(*argv, env=env, cwd=cwd, stdout=asyncio.subprocess.DEVNULL,
                                                stderr=asyncio.subprocess.PIPE)


def _portable_dir() -> Path:
    d = Path(tempfile.gettempdir()) / PORTABLE_DIR_NAME
    d.mkdir(mode=0o700, exist_ok=True)
    # Dọn tệp sót từ lần trước (trình duyệt ngắt giữa chừng ⇒ tác vụ nền không chạy): quá hạn khoá là chắc đã bỏ.
    cutoff = time.time() - PORTABLE_LOCK_TTL
    for p in d.glob("*.ghbundle"):
        with contextlib.suppress(OSError):
            if p.stat().st_mtime < cutoff:
                p.unlink()
    return d


async def _export_portable(out: Path, key: str) -> None:
    """`python -m gh.bundle export --out <out>`; mật khẩu gói = Khoá khôi phục, CHỈ qua biến môi trường."""
    env = {**os.environ, "GH_BUNDLE_PASSWORD": key}
    proc = await _exec(sys.executable, "-m", "gh.bundle", "export", "--out", str(out), env=env, cwd=API_DIR)
    try:
        _out, err = await asyncio.wait_for(proc.communicate(), timeout=PORTABLE_TIMEOUT_SECONDS)
    except TimeoutError:
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
        with contextlib.suppress(Exception):
            await proc.wait()
        raise _portable_failed("quá 30 phút chưa xong") from None
    if proc.returncode != 0:
        tail = (err or b"").decode(errors="replace")[-2000:].replace(key, "***")
        log.error("Xuất gói mang đi thất bại (mã %s): %s", proc.returncode, tail)
        raise _portable_failed(f"lệnh xuất gói thoát với mã {proc.returncode}")


def _portable_failed(reason: str) -> ApiError:
    return ApiError(500, "PORTABLE_FAILED", PORTABLE_FAILED_TITLE,
                    f"Chi tiết kỹ thuật: {reason}. Thử lại sau ít phút; nếu vẫn lỗi, xem nhật ký máy chủ.")


class _CleanupFileResponse(FileResponse):
    """FileResponse dọn tệp tạm + nhả khoá trong `finally` — BackgroundTask của Starlette KHÔNG chạy khi trình duyệt
    ngắt giữa lượt tải (send ném lỗi) ⇒ khoá Redis 2 giờ và tệp trong /tmp/gh-portable còn lại, Owner bị 409
    PORTABLE_IN_PROGRESS suốt 2 giờ."""

    def __init__(self, *args: Any, cleanup: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._cleanup = cleanup

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            await asyncio.shield(self._cleanup())


async def _org_tz(db: AsyncSession, org_id: uuid.UUID) -> Any:
    tz_name = (await db.execute(text("SELECT timezone FROM core.organizations WHERE id = :o"),
                                {"o": org_id})).scalar_one_or_none()
    try:
        return ZoneInfo(tz_name or "UTC")
    except (ValueError, KeyError):
        return UTC


@router.get("/system/offsite/portable")
async def portable(request: Request, db: AsyncSession = DB, user: service.CurrentUser = Depends(require_owner),
                   _pin: Any = Depends(require_pin("offsite.portable"))) -> Response:
    """"Tải gói mang đi" (chỉ Owner, cần PIN): toàn bộ dữ liệu thành MỘT tệp .ghbundle mã hoá bằng Khoá khôi phục —
    mở được trên máy mới bằng `genh import` + Bộ khôi phục. GET để trình duyệt tải thẳng xuống đĩa (không qua RAM)."""
    key = _key_or_409()
    redis = request.app.state.redis
    token = uuid.uuid4().hex
    if not await redis.set(PORTABLE_LOCK_KEY, token, nx=True, ex=PORTABLE_LOCK_TTL):
        raise conflict("PORTABLE_IN_PROGRESS", "Đang tạo một gói mang đi khác — chờ xong rồi thử lại")

    out: Path | None = None

    async def cleanup() -> None:
        if out is not None:
            with contextlib.suppress(OSError):
                out.unlink()
        with contextlib.suppress(Exception):
            current = await redis.get(PORTABLE_LOCK_KEY)
            if current is not None and (current.decode() if isinstance(current, bytes) else current) == token:
                await redis.delete(PORTABLE_LOCK_KEY)

    try:
        fd, name = tempfile.mkstemp(prefix="gen-harness-", suffix=".ghbundle", dir=_portable_dir())
        os.close(fd)
        out = Path(name)
        await _export_portable(out, key)
        size = out.stat().st_size
        if size == 0:
            raise _portable_failed("gói rỗng")
        await actionlog.record(db, org_id=user.org_id, actor_type="user", actor_id=user.actor_id,
                               action="offsite.portable_downloaded", target_type="system", target_id="offsite",
                               detail={"size_bytes": size, "key_id": key_id(key)}, ip=user.ip)
        await db.commit()
        tz = await _org_tz(db, user.org_id)
    except BaseException:
        await cleanup()
        raise
    filename = f"gen-harness-mang-di-{datetime.now(tz):%Y%m%d-%H%M}.ghbundle"
    return _CleanupFileResponse(out, media_type="application/octet-stream", filename=filename,
                                headers={"Cache-Control": "no-store"}, cleanup=cleanup)
