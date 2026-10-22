"""Cập nhật phiên bản ngay trong Console — nút "Cập nhật ngay" thay cho gõ `genh update` trên máy chủ.

Container api không tự nâng cấp được chính nó (dừng/khởi động lại cả hệ thống là việc của genh trên máy chủ), nên
Console chỉ để lại YÊU CẦU trong hộp thư chung `<gốc cài đặt>/run` (bind mount tại `Settings.host_link_dir`, xem
apps/genh/internal/hostlink): một watcher trên máy chủ (systemd path unit / crontab mỗi phút / launchd) thấy tệp
`request/update.json` thì chạy `genh update` — backup → tải bản mới → migrate → khởi động lại, tự rollback nếu lỗi —
và ghi tiến trình vào `update-status.json` cho Console đọc lại. `genh.json` cho biết phiên bản đang chạy và máy chủ
đã có watcher chưa (chưa có ⇒ Console hiện lệnh để Owner tự chạy một lần, lần đó cài luôn watcher).
"""

import json
import logging
import os
import re
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from gh.auth import rbac, service
from gh.auth.deps import require
from gh.chassis import actionlog
from gh.config import get_settings
from gh.db import DB
from gh.errors import conflict
from gh.hostlink_io import read_state, write_request

log = logging.getLogger(__name__)
router = APIRouter(tags=["system"])
MANAGE = require("system.manage", rbac.ALL)

# v0.1.53 (F-96): hình dạng bộ đệm đổi (thêm danh sách bản đủ điều kiện cho lịch đêm) ⇒ khoá mới, bản đệm cũ bỏ.
LATEST_CACHE_KEY = "gh:update:latest:v2"
# v0.1.30: 1 giờ → 10 phút (Boss thấy "mất nút update" gần 1 giờ sau khi v0.1.29 đã phát hành).
LATEST_CACHE_SECONDS = 600
# `POST /system/update/check` hỏi GitHub ngay (bỏ qua bộ đệm) — tối đa 1 lần / 30 giây cho cả tổ chức.
CHECK_LOCK_KEY = "gh:update:check-lock"
CHECK_MIN_INTERVAL_SECONDS = 30
# Yêu cầu nằm quá lâu mà trạng thái không đổi ⇒ watcher không chạy (máy chủ tắt watcher, linger…) — cho bấm lại.
STALE_REQUEST_SECONDS = 15 * 60
# v0.1.37 (F-34): 'running' mà tiến trình genh đã chết (máy tắt/khởi động lại, bị kill) ⇒ 'stalled' để Console cho
# bấm Thử lại. Container api không thấy PID máy chủ — "còn sống" suy từ nhịp sống `run/genh-heartbeat.json` (genh
# ghi mỗi 30 giây khi giữ khoá loại trừ); boot_id chỉ phụ (container chỉ trùng nhân máy chủ khi Docker chạy thẳng trên
# Linux — Docker Desktop chạy trong VM).
RUNNING_STALL_SECONDS = 60 * 60
HEARTBEAT_STALE_SECONDS = 5 * 60
BOOT_ID_PATH = Path("/proc/sys/kernel/random/boot_id")
_SEMVER = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")
# v0.1.53 (F-96): lịch đêm chọn bản CAO NHẤT đã đủ thời gian chín 24 giờ (genh selfupdate.NightlyMinAge) trong vài bản
# gần nhất, không chỉ bản mới nhất — nên Console hỏi một danh sách thay vì /releases/latest.
NIGHTLY_MIN_AGE = timedelta(hours=24)
RELEASES_PER_PAGE = 10
# v0.1.33: dấu job `promote` (e2e-install.yml) ghi vào ghi chú Release lúc nâng bản thử thành bản chính thức — cùng
# định dạng với apps/genh/internal/selfupdate PromotedMarker. Thời gian chín 24 giờ của lịch đêm tính từ dấu này
# (published_at là lúc tạo bản thử, promote không đổi nó).
_PROMOTED = re.compile(r"<!--\s*genh:promoted_at=(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z)\s*-->")


def _dir() -> Path:
    return Path(get_settings().host_link_dir)


def _read_json(path: Path) -> dict[str, Any] | None:
    """Đọc AN TOÀN một tệp JSON trong hộp thư (gh.hostlink_io.read_state: không theo symlink, không treo ở FIFO,
    1 liên kết, ≤ 64 KiB, chủ = chủ run/). Tệp trong run/request/ do chính api ghi nên chấp nhận thêm uid của api.
    Tệp thiếu/bẫy/hỏng ⇒ None (API trả "không rõ", không 500)."""
    return read_state(path, allow_self=path.parent.name == "request")


def _parse(v: str | None) -> tuple[int, int, int] | None:
    m = _SEMVER.match((v or "").strip())
    return (int(m[1]), int(m[2]), int(m[3])) if m else None


def is_newer(latest: str | None, current: str | None) -> bool:
    a, b = _parse(latest), _parse(current)
    return a is not None and b is not None and a > b


def _ts(iso: str | None) -> datetime | None:
    try:
        t = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else None


def official_since(published_at: str | None, notes: str | None) -> str | None:
    """Lúc bản này thành bản chính thức: dấu promote MUỘN NHẤT trong ghi chú, hoặc published_at — lấy cái muộn hơn,
    như selfupdate.officialSince của genh (cùng mốc lịch đêm dùng để đếm 24 giờ). Không đọc được ⇒ None."""
    times = [t for t in (_ts(published_at), *(_ts(m) for m in _PROMOTED.findall(notes or ""))) if t is not None]
    return max(times).astimezone(UTC).isoformat().replace("+00:00", "Z") if times else None


def strip_markers(notes: str) -> str:
    """Bỏ dấu promote (chú thích HTML, GitHub không hiện) khỏi ghi chú trước khi đưa lên Console."""
    return _PROMOTED.sub("", notes).strip()


def _iso_z(t: datetime) -> str:
    return t.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _summarize(releases: list[Any]) -> dict[str, Any] | None:
    """Tóm tắt danh sách release GitHub (dữ liệu bên ngoài — chỉ nhận đúng kiểu, bỏ phần tử lạ): `latest` = semver CAO
    NHẤT trong các bản không draft/không prerelease; `candidates` = các bản chính thức CÓ dấu promote, sắp tăng dần theo
    semver, mỗi bản kèm `eligible_at` = lúc thành bản chính thức + 24 giờ (thời gian chín của lịch đêm). Việc lọc "mới
    hơn bản đang chạy" làm ở `_payload` (bản đang chạy đổi sau mỗi lần cập nhật, bộ đệm thì không)."""
    official: list[tuple[tuple[int, int, int], str, dict[str, Any], str]] = []
    for rel in releases:
        if not isinstance(rel, dict) or rel.get("draft") is True or rel.get("prerelease") is True:
            continue
        tag = rel.get("tag_name")
        ver = _parse(tag) if isinstance(tag, str) else None
        if ver is None or not isinstance(tag, str):
            continue
        notes = rel.get("body")
        official.append((ver, tag, rel, notes if isinstance(notes, str) else ""))
    if not official:
        return None
    official.sort(key=lambda o: o[0])
    candidates: list[dict[str, str]] = []
    for _, tag, rel, notes in official:
        if not _PROMOTED.search(notes):
            continue
        since = _ts(official_since(rel.get("published_at"), notes))
        if since is not None:
            candidates.append({"tag": tag, "eligible_at": _iso_z(since + NIGHTLY_MIN_AGE)})
    _, tag, rel, notes = official[-1]
    url = rel.get("html_url")
    return {"tag": tag, "url": url if isinstance(url, str) else None,
            "published_at": official_since(rel.get("published_at"), notes),
            "notes": strip_markers(notes)[:4000], "candidates": candidates[-RELEASES_PER_PAGE:]}


async def fetch_latest(repo: str) -> dict[str, Any] | None:
    """Bản phát hành mới nhất trên GitHub (tag, link, ghi chú) + danh sách bản đủ điều kiện cho lịch đêm — lỗi mạng ⇒
    None, không làm hỏng màn hình.

    v0.1.53 (F-96): MỘT lần GET `/releases?per_page=10` (thay `/releases/latest`): `latest` là semver cao nhất trong các
    bản chính thức, không phụ thuộc bản nào tạo sau cùng. Lỗi/danh sách không dùng được ⇒ rơi về `/releases/latest` như
    trước (không mất nút cập nhật), khi đó `candidates` chỉ có bản đó (nếu có dấu promote).

    `published_at` trả về là lúc bản này thành BẢN CHÍNH THỨC (official_since) — Console dùng để báo lịch đêm tự cài
    từ khi nào."""
    base = f"https://api.github.com/repos/{repo}"
    try:
        async with httpx.AsyncClient(timeout=6.0, headers={"Accept": "application/vnd.github+json"}) as c:
            summary = None
            try:
                r = await c.get(f"{base}/releases?per_page={RELEASES_PER_PAGE}")
                body = r.json() if r.status_code == 200 else None
                summary = _summarize(body) if isinstance(body, list) else None
            except (httpx.HTTPError, ValueError):
                log.info("Không đọc được danh sách bản phát hành — rơi về bản mới nhất của GitHub")
            if summary is None:
                r = await c.get(f"{base}/releases/latest")
                body = r.json() if r.status_code == 200 else None
                if not isinstance(body, dict):
                    return None
                notes = body.get("body")
                raw = notes if isinstance(notes, str) else ""
                summary = _summarize([body]) or {
                    # Tag không theo semver (hiếm): giữ cách cũ — Console vẫn hiện, is_newer tự bỏ qua.
                    "tag": body.get("tag_name"), "url": body.get("html_url"),
                    "published_at": official_since(body.get("published_at"), raw),
                    "notes": strip_markers(raw)[:4000], "candidates": []}
        return summary
    except (httpx.HTTPError, ValueError):
        return None


async def _latest(request: Request, *, force: bool = False) -> dict[str, Any] | None:
    """Bản mới nhất (đệm Redis `LATEST_CACHE_SECONDS`). `force` bỏ qua bộ đệm; hỏi GitHub lỗi thì giữ bản đệm cũ."""
    repo = get_settings().release_repo
    if not repo:
        return None
    redis = request.app.state.redis
    cached_raw = await redis.get(LATEST_CACHE_KEY)
    cached = json.loads(cached_raw) if cached_raw else None
    if cached and not force:
        return cached  # type: ignore[no-any-return]
    latest = await fetch_latest(repo)
    if latest and latest.get("tag"):
        latest = {**latest, "checked_at": datetime.now(UTC).isoformat()}
        await redis.set(LATEST_CACHE_KEY, json.dumps(latest), ex=LATEST_CACHE_SECONDS)
        return latest
    return cached or latest


def _age_seconds(iso: str | None) -> float | None:
    # Hỏng/thiếu múi giờ ⇒ None: trừ datetime không múi giờ sẽ ném TypeError — dữ liệu run/ không tin cậy, không 500.
    t = _ts(iso)
    return (datetime.now(UTC) - t).total_seconds() if t is not None else None


def _boot_id() -> str | None:
    """boot_id của nhân đang chạy — trong container trùng với máy chủ Linux. Lỗi/không phải Linux ⇒ None."""
    try:
        v = BOOT_ID_PATH.read_text(encoding="ascii").strip()
    except (OSError, ValueError):
        return None
    return v or None


def _int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _str(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _heartbeat(d: Path) -> tuple[bool, int | None, str | None]:
    """(nhịp tươi ≤ `HEARTBEAT_STALE_SECONDS`, pid, boot_id) từ `run/genh-heartbeat.json` — sai kiểu ⇒ None."""
    hb = _read_json(d / "genh-heartbeat.json") or {}
    at = _str(hb.get("at"))
    age = _age_seconds(at) if at else None
    return age is not None and age <= HEARTBEAT_STALE_SECONDS, _int(hb.get("pid")), _str(hb.get("boot_id"))


def _process_gone(status: dict[str, Any], d: Path) -> bool:
    """'running' nhưng tiến trình genh không còn. Nhịp sống là nguồn CHÍNH: tươi và đúng pid ⇒ còn sống — KHÔNG xét
    boot_id của container (Docker Desktop for Linux chạy container trong VM: boot_id container ≠ máy chủ dù genh vẫn
    chạy). Ngoài ra:
      - boot_id lúc bắt đầu (update-status.json) ≠ boot_id của nhịp sống (cả hai do genh ghi) ⇒ máy đã khởi động lại;
      - nhịp cũ/thiếu và boot_id lúc bắt đầu ≠ boot_id container ⇒ máy đã khởi động lại (dừng ngay, không chờ 60 phút);
      - còn lại: chạy quá `RUNNING_STALL_SECONDS` mà không có nhịp sống đúng pid ⇒ đã chết.
    Mọi giá trị đọc từ run/ (0777) là dữ liệu không tin cậy — chỉ nhận đúng kiểu, không đưa vào thông điệp."""
    pid = _int(status.get("pid"))
    fresh, hb_pid, hb_boot = _heartbeat(d)
    if fresh and (pid is None or hb_pid == pid):
        return False
    status_boot = _str(status.get("boot_id"))
    if status_boot and hb_boot and status_boot != hb_boot:
        return True
    here = _boot_id()
    if not fresh and status_boot and here and status_boot != here:
        return True
    started = _str(status.get("started_at"))
    age = _age_seconds(started) if started else None
    return age is not None and age > RUNNING_STALL_SECONDS


def _host_busy(d: Path) -> bool:
    """Một tiến trình genh đang giữ khoá loại trừ (update/restore/import — vd lịch đêm) và còn sống: nhịp sống
    `run/genh-heartbeat.json` tươi (≤ `HEARTBEAT_STALE_SECONDS`) là đủ — không so boot_id container (Docker Desktop
    chạy container trong VM). Yêu cầu "Cập nhật ngay" nằm lâu trong lúc này là đang XẾP HÀNG sau lần đó (genh
    --if-requested chờ khoá), không phải watcher không chạy."""
    return _heartbeat(d)[0]


#: v0.1.53 (F-99): giá trị cho phép của các trường chuỗi trong `run/autostart-status.json` / `run/nightly-status.json`
#: (genh ghi, thư mục 0777 ⇒ dữ liệu không tin cậy): ngoài tập này ⇒ 'unknown'. Không bao giờ đưa chữ lấy từ tệp vào
#: thông điệp; lệnh sửa do web/API ghép từ chuỗi cố định.
LINGER_VALUES = ("yes", "no", "unknown", "not_applicable")
NIGHTLY_MECHANISMS = ("systemd", "cron", "launchd", "schtasks", "")
NIGHTLY_RESULTS = ("done", "failed", "deferred", "up_to_date", "blocked", "")
WATCHER_VALUES = ("active", "failed", "inactive", "unknown")


#: v0.1.54: người gác yêu cầu (`gen-harness-update-request.path`) tự chữa — `watcher` trong nightly-status.json.
#: 'fallback' = .path lỗi (vd hết hạn mức inotify) nhưng timer dự phòng quét mỗi phút đang nhận yêu cầu thay;
#: 'failed' = .path lỗi và chưa bật được dự phòng. Thiếu khối (genh cũ) = 'ok'. Lý do là mã; câu gợi ý do API tự
#: ghép từ chuỗi cố định bên dưới — KHÔNG lấy chữ nào từ tệp (thư mục run/ không tin cậy).
WATCHER_STATES = ("ok", "fallback", "failed")
WATCHER_REASONS = ("inotify", "resources", "other")
INOTIFY_FIX = "sudo sysctl -w fs.inotify.max_user_instances=1024"
WATCHER_HINTS = {
    "inotify": f"Hết hạn mức inotify: chạy `{INOTIFY_FIX}` rồi `genh auto-update enable`.",
    "resources": ("Lỗi tài nguyên hệ thống (thường là hạn mức inotify): "
                  f"thử `{INOTIFY_FIX}` rồi `genh auto-update enable`."),
    "other": ("Xem `systemctl --user status gen-harness-update-request.path`, "
              "sửa lỗi rồi chạy `genh auto-update enable`."),
}


def _watcher(value: Any) -> dict[str, Any]:
    """Khối `watcher` đã LỌC: state ngoài tập (hoặc không phải object) ⇒ 'ok'; reason ngoài tập ⇒ None."""
    if not isinstance(value, dict):
        return {"state": "ok", "reason": None}
    state, reason = value.get("state"), value.get("reason")
    if not isinstance(state, str) or state not in WATCHER_STATES:
        return {"state": "ok", "reason": None}
    if state == "ok":
        return {"state": "ok", "reason": None}
    return {"state": state, "reason": reason if isinstance(reason, str) and reason in WATCHER_REASONS else "other"}


def _pick(value: Any, allowed: tuple[str, ...]) -> str:
    return value if isinstance(value, str) and value in allowed else "unknown"


def _bool(value: Any) -> bool | None:
    return value if isinstance(value, bool) else None


def _iso_or_none(value: Any) -> str | None:
    t = _ts(value) if isinstance(value, str) else None
    return _iso_z(t) if t is not None else None


def read_nightly(d: Path) -> dict[str, Any] | None:
    """`run/nightly-status.json` (genh ghi: publishHostInfo, lần chạy lịch đêm, trực canh 12 phút) đã LỌC: chỉ nhận
    đúng kiểu/tập giá trị — lạ ⇒ 'unknown'/None. Tệp thiếu/hỏng/không phải object ⇒ None (genh cũ chưa ghi)."""
    raw = _read_json(d / "nightly-status.json")
    if raw is None:
        return None
    mechanism = raw.get("mechanism")
    result = raw.get("last_result")
    return {
        "mechanism": mechanism if isinstance(mechanism, str) and mechanism in NIGHTLY_MECHANISMS else "unknown",
        "enabled": _bool(raw.get("enabled")), "active": _bool(raw.get("active")),
        "unit_present": _bool(raw.get("unit_present")), "opted_out": _bool(raw.get("opted_out")),
        # v0.1.53: lịch dùng chung của máy thuộc bản cài KHÁC còn sống (genh cũ không ghi ⇒ None).
        "owned_by_other": _bool(raw.get("owned_by_other")),
        "since": _iso_or_none(raw.get("since")), "last_run_at": _iso_or_none(raw.get("last_run_at")),
        "next_run_at": _iso_or_none(raw.get("next_run_at")),
        "last_result": result if isinstance(result, str) and result in NIGHTLY_RESULTS else "unknown",
        "linger": _pick(raw.get("linger"), LINGER_VALUES),
        "request_watcher": _pick(raw.get("request_watcher"), WATCHER_VALUES),
        "watcher": _watcher(raw.get("watcher")),
        # genh từ v0.1.54 luôn ghi khối `watcher`; thiếu khối = genh cũ (chưa có timer dự phòng) — xem `watcher_failed`.
        "watcher_reported": isinstance(raw.get("watcher"), dict),
        "checked_at": _iso_or_none(raw.get("checked_at")),
    }


def watcher_failed(nightly: dict[str, Any] | None) -> bool:
    """Trình nhận yêu cầu LỖI HẲN (không ai nhận yêu cầu bấm nút)? `request_watcher` chỉ là kết quả `systemctl is-active
    .path` — 'failed' cũng là đúng lúc genh bật timer dự phòng (`watcher.state = fallback`, dự phòng vẫn nhận yêu
    cầu mỗi phút) nên KHÔNG đủ để kết luận. genh từ v0.1.54 ghi khối `watcher` ⇒ chỉ `state = failed` mới là lỗi
    hẳn. Genh cũ (không có khối, chưa có dự phòng) ⇒ dựa vào `request_watcher = failed`."""
    if nightly is None:
        return False
    if nightly["watcher_reported"]:
        return nightly["watcher"]["state"] == "failed"
    return nightly["request_watcher"] == "failed"


def _stalled_cause(d: Path, updater: Any) -> str:
    """Vì sao yêu cầu nằm quá `STALE_REQUEST_SECONDS` mà không ai nhận (không tính lúc máy chủ đang bận):
    - 'linger_off': linger tắt VÀ trình nhận yêu cầu là systemd --user (genh.json `updater` = 'systemd'; genh cũ không
      ghi `updater` thì dựa vào `linger_required` của autostart-status.json) ⇒ chỉ chạy khi có người đăng nhập.
      Trình nhận yêu cầu crontab/launchd chạy cả khi không ai đăng nhập — genh vẫn ghi giá trị loginctl thật vào
      autostart-status.json, nên linger 'no' ở đó KHÔNG phải nguyên nhân (nhắc `enable-linger` chỉ che nguyên nhân
      thật);
    - 'watcher_failed': genh báo trình nhận yêu cầu (gen-harness-update-request.path/.service) lỗi hẳn — KHÔNG tính
      khi timer dự phòng đang nhận thay (`watcher.state = fallback`, xem `watcher_failed`);
    - 'not_picked_up': còn lại (không rõ nguyên nhân). Đọc trực tiếp tệp run/, không import gh.health (vòng import)."""
    auto = _read_json(d / "autostart-status.json") or {}
    if isinstance(updater, str) and updater:
        systemd_watcher = updater == "systemd"
    else:  # genh cũ không ghi `updater`
        systemd_watcher = auto.get("linger_required") is True
    if systemd_watcher and _pick(auto.get("linger"), LINGER_VALUES) == "no":
        return "linger_off"
    if watcher_failed(read_nightly(d)):
        return "watcher_failed"
    return "not_picked_up"


#: Mã genh (GH-E9xx) cuối thông điệp hộp thư — cùng cách đọc với web (updateModel.ts `updateErrorCode`).
_GH_CODE = re.compile(r"GH-E[0-9A-F]{3}")
#: Dấu "quay về chưa trọn / cần xử lý tay" trong thông điệp genh — cùng mẫu với web (updateModel.ts `failedCopy`).
_MANUAL = re.compile(r"CŨNG THẤT BẠI|chưa trọn|can thiệp tay|xử lý tay", re.IGNORECASE)
#: Cụm cố định genh ghi khi máy tắt SAU lúc đã đổi CSDL (ops.updateShutdownForwardMarker): giữ bản mới, cần chạy tiếp.
SHUTDOWN_FORWARD_MARKER = "CSDL đã sang bản mới, cần chạy tiếp"


def _interrupted(state: str, message: Any, rollback_failed: bool) -> str | None:
    """v0.1.37: lần cập nhật 'failed' do genh nhận TÍN HIỆU DỪNG (GH-E94B — máy tắt/khởi động lại/bị dừng tay) mà KHÔNG
    để máy dở dang ⇒ không phải bản mới hỏng. 'rolled_back' (chưa đụng gì / đã tự quay về), 'resume' (máy tắt sau khi
    đã đổi CSDL — giữ bản mới, chạy lại để đi tiếp); quay về chưa trọn hoặc không phải GH-E94B ⇒ None."""
    if state != "failed" or not isinstance(message, str) or rollback_failed:
        return None
    codes = _GH_CODE.findall(message)
    if not codes or codes[-1] != "GH-E94B" or _MANUAL.search(message):
        return None
    return "resume" if SHUTDOWN_FORWARD_MARKER in message else "rolled_back"


#: v0.1.53 (F-97): genh KHÔNG làm yêu cầu khi không xoá được tệp `request/update.json` (xoá hỏng ⇒ làm tiếp sẽ khiến
#: .path kích lặp). Nó ghi update-status 'failed' + mã GH-E94C rồi thoát; tệp yêu cầu vẫn nằm lại.
CONSUME_FAILED_CODE = "GH-E94C"


def _consume_failed(status: dict[str, Any], request: dict[str, Any]) -> bool:
    """update-status.json là 'failed' GH-E94C của CHÍNH yêu cầu còn nằm lại (finished_at ≥ requested_at) ⇒ Console phải
    hiện thông điệp genh (không phải 'đã gửi yêu cầu' mãi rồi 'chưa nhận'). Thiếu/hỏng mốc thời gian ⇒ không khớp."""
    message = status.get("message")
    if status.get("state") != "failed" or not isinstance(message, str):
        return False
    codes = _GH_CODE.findall(message)
    finished, requested = _ts(status.get("finished_at")), _ts(request.get("requested_at"))
    return bool(codes) and codes[-1] == CONSUME_FAILED_CODE and finished is not None and requested is not None \
        and finished >= requested


def running_version() -> str | None:
    """Phiên bản genh đã cài (genh.json) — None khi chạy bản phát triển không có hộp thư chung."""
    v = (_read_json(_dir() / "genh.json") or {}).get("version")
    return str(v) if v else None


def _state() -> dict[str, Any]:
    d = _dir()
    info = _read_json(d / "genh.json") or {}
    status = _read_json(d / "update-status.json") or {}
    request = _read_json(d / "request" / "update.json")
    state = status.get("state") or "idle"
    stalled_reason: str | None = None
    if state == "running" and _process_gone(status, d):
        state, stalled_reason = "stalled", "process_gone"
    host_busy = False
    if request is not None:
        age = _age_seconds(request.get("requested_at"))
        host_busy = _host_busy(d)
        if _consume_failed(status, request):
            # genh đã thử nhận yêu cầu này nhưng không xoá được tệp (GH-E94C): giữ 'failed' để Console hiện thông điệp
            # của genh (kiểm quyền thư mục run/request) thay vì chờ 15 phút rồi nói "chưa nhận" không rõ nguyên nhân.
            state, stalled_reason = "failed", None
        elif age is not None and age > STALE_REQUEST_SECONDS and not host_busy:
            state, stalled_reason = "stalled", _stalled_cause(d, info.get("updater"))
        else:
            state, stalled_reason = "requested", None
    updater = info.get("updater") or None
    # v0.1.33: genh ghi trạng thái lịch tự cập nhật đêm (cài/update/`genh auto-update enable|disable`); genh cũ chưa
    # ghi hoặc giá trị lạ ⇒ None — Console không hứa "Tự cài đêm …".
    auto = info.get("auto_update_enabled")
    # v0.1.34: bản genh đã lỗi + quay về bản cũ — lịch đêm không tự cài lại; Console không hứa "Tự cài đêm …".
    blocked_file = _read_json(d / "update-blocked.json") or {}
    blocked = blocked_file.get("version")
    blocked_ok = isinstance(blocked, str) and bool(blocked)
    # Trường có cấu trúc (genh ghi): tự quay về bản cũ CŨNG thất bại — Console dùng thay vì dò chữ trong thông điệp.
    rollback_failed = blocked_file.get("rollback_failed")
    rb_failed = rollback_failed is True and blocked_ok and blocked == status.get("to")
    return {
        "current": info.get("version") or None,
        "updater": updater,
        "auto_update_enabled": auto if isinstance(auto, bool) else None,
        "blocked_version": blocked if blocked_ok else None,
        "blocked_rollback_failed": rollback_failed is True if blocked_ok else None,
        "linked": d.is_dir(),
        "can_request": bool(updater) and os.access(d / "request", os.W_OK),
        "state": state,
        "stalled_reason": stalled_reason,
        # v0.1.37: yêu cầu đang chờ một lần cập nhật/khôi phục khác (vd lịch đêm) chạy xong — không phải "chưa nhận".
        "host_busy": host_busy,
        "message": status.get("message") or None,
        # v0.1.37: 'failed' vì tín hiệu dừng (GH-E94B) mà không dở dang — Console/chuông báo "bị dừng giữa chừng"
        # (vàng), không phải "chưa thành công" (đỏ).
        "interrupted": _interrupted(str(state), status.get("message"), rb_failed),
        "from": status.get("from") or None,
        "to": status.get("to") or None,
        "started_at": status.get("started_at") or None,
        "finished_at": status.get("finished_at") or None,
        "requested_at": request.get("requested_at") if request else None,
    }


#: v0.1.55 (G2): vì sao KHÔNG hiện nút "Cập nhật ngay" (null ⇔ hiện được nút). Thứ tự ưu tiên từ trên xuống; mã cố
#: định — web ghép câu lý do + việc cần làm (updateModel.ts `blockReasonCopy`), KHÔNG lấy chữ từ run/.
BLOCK_REASONS = ("not_owner", "in_progress", "genh_unlinked", "watcher_stalled", "up_to_date")


def request_block_reason(user: service.CurrentUser, s: dict[str, Any], nightly: dict[str, Any] | None, *,
                         update_available: bool) -> str | None:
    """`request_block_reason` của `GET /system/update`:
    1. `not_owner` — người gọi không phải Owner (chỉ Owner bấm cập nhật được);
    2. `in_progress` — đang `requested`/`running`;
    3. `genh_unlinked` — không có trình nhận yêu cầu hoặc hộp thư `request/` không ghi được (đúng điều kiện
       `can_request = false`);
    4. `watcher_stalled` — trình nhận yêu cầu LỖI HẲN: `stalled_reason = watcher_failed`, hoặc genh báo `watcher.state =
       failed` (genh cũ không có khối `watcher`: `request_watcher = failed`). `fallback` — timer dự phòng vẫn nhận yêu
       cầu — KHÔNG chặn nút dù `request_watcher` lúc đó là 'failed' (`.path` chính là cái đã lỗi);
    5. `up_to_date` — không có bản mới hơn.
    None ⇒ nút "Cập nhật ngay" hiện được."""
    if user.role_code != rbac.OWNER:
        return "not_owner"
    if s["state"] in ("requested", "running"):
        return "in_progress"
    if not s["can_request"]:
        return "genh_unlinked"
    if s.get("stalled_reason") == "watcher_failed" or watcher_failed(nightly):
        return "watcher_stalled"
    if not update_available:
        return "up_to_date"
    return None


async def _payload(request: Request, user: service.CurrentUser, *, force: bool = False) -> dict[str, Any]:
    s = _state()
    latest = await _latest(request, force=force) if s["linked"] else None
    tag = latest.get("tag") if latest else None
    nightly = read_nightly(_dir()) if s["linked"] else None
    available = is_newer(tag, s["current"])
    return {**s, "latest": tag, "release_url": latest.get("url") if latest else None,
            "release_notes": latest.get("notes") if latest else None,
            "published_at": latest.get("published_at") if latest else None,
            "checked_at": latest.get("checked_at") if latest else None,
            "update_available": available,
            # v0.1.55 (G2): vì sao không hiện nút "Cập nhật ngay" (null ⇔ hiện được) — Console nói rõ lý do.
            "request_block_reason": request_block_reason(user, s, nightly, update_available=available),
            # v0.1.53 (F-96): bản chính thức mới hơn bản đang chạy mà lịch đêm có thể chọn, kèm lúc đủ 24 giờ.
            "nightly_candidates": _candidates(latest, s["current"]),
            # v0.1.53 (F-99): lịch tự cập nhật đêm (genh ghi run/nightly-status.json, đã lọc); chưa có tệp ⇒ null.
            "nightly": None if nightly is None else {k: nightly[k] for k in (
                "mechanism", "enabled", "active", "opted_out", "owned_by_other", "last_run_at", "next_run_at")}}


def _candidates(latest: dict[str, Any] | None, current: str | None) -> list[dict[str, str]]:
    """`nightly_candidates` của payload: các bản trong bộ đệm mới hơn bản đang chạy (tăng dần theo semver), ≤ 10."""
    raw = (latest or {}).get("candidates")
    if not isinstance(raw, list):
        return []
    out = [{"tag": c["tag"], "eligible_at": c["eligible_at"]} for c in raw
           if isinstance(c, dict) and isinstance(c.get("tag"), str) and isinstance(c.get("eligible_at"), str)
           and is_newer(c["tag"], current)]
    return out[-RELEASES_PER_PAGE:]


@router.get("/system/update")
async def get_update(request: Request, user: service.CurrentUser = Depends(MANAGE)) -> dict[str, Any]:
    """Phiên bản đang chạy, bản mới nhất, và tiến trình cập nhật (idle/requested/running/done/failed/stalled)."""
    return await _payload(request, user)


@router.post("/system/update/check")
async def check_update(request: Request, db: AsyncSession = DB,
                       user: service.CurrentUser = Depends(MANAGE)) -> dict[str, Any]:
    """v0.1.30: nút "Kiểm tra bản mới" — hỏi GitHub ngay, bỏ qua bộ đệm. Giới hạn 1 lần / 30 giây (bấm dồn thì trả
    kết quả đang đệm, `throttled: true`) để không vượt hạn mức API GitHub không xác thực.
    v0.1.61 (F105): ghi Action Log riêng `system.update_checked` (ai bấm, bản đang chạy, bản mới nhất) — trước đó chỉ có
    dòng chung `http.post` của middleware, cột đối tượng trống."""
    fresh = bool(await request.app.state.redis.set(CHECK_LOCK_KEY, "1", nx=True, ex=CHECK_MIN_INTERVAL_SECONDS))
    payload = await _payload(request, user, force=fresh)
    await actionlog.record(db, org_id=user.org_id, actor_type="user", actor_id=user.actor_id,
                           action="system.update_checked", target_type="system", target_id="update",
                           target_label=payload["current"], ip=user.ip,
                           detail={"throttled": not fresh, "latest": payload["latest"]})
    await db.commit()
    return {**payload, "throttled": not fresh}


@router.post("/system/update", status_code=202)
async def request_update(request: Request, db: AsyncSession = DB,
                         user: service.CurrentUser = Depends(MANAGE)) -> dict[str, Any]:
    """Để lại yêu cầu cập nhật cho genh trên máy chủ. Hệ thống sẽ khởi động lại trong vài phút."""
    s = _state()
    if not s["can_request"]:
        raise conflict("UPDATER_UNAVAILABLE",
                       "Máy chủ chưa bật nhận yêu cầu cập nhật từ Console — chạy `genh update` một lần trên máy chủ")
    if s["state"] in ("requested", "running"):
        raise conflict("UPDATE_IN_PROGRESS", "Đang cập nhật — chờ xong rồi thử lại")
    d = _dir()
    restoring = (_read_json(d / "restore-status.json") or {}).get("state") == "running"
    if restoring or (d / "request" / "restore.json").exists():
        raise conflict("RESTORE_IN_PROGRESS", "Đang khôi phục dữ liệu — chờ xong rồi thử lại")
    req = {"id": str(uuid.uuid4()), "requested_at": datetime.now(UTC).isoformat(), "by": user.actor_id}
    write_request(_dir() / "request", "update.json", req)
    await actionlog.record(db, org_id=user.org_id, actor_type="user", actor_id=user.actor_id,
                           action="system.update_requested", target_type="system", target_id="update",
                           target_label=s["current"], detail={"request_id": req["id"]}, ip=user.ip)
    await db.commit()
    return await _payload(request, user)
