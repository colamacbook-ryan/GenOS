"""GET /system/access — thẻ "Truy cập từ xa" và hộp mời nhân viên (v0.1.46, F-21).

Console chỉ ĐỌC `run/network-status.json` (genh ghi, theo hợp đồng chung) qua `update._read_json` (kiểm chủ/symlink).
Tệp nằm trong run/ (không tin cậy hoàn toàn): mỗi trường chỉ nhận giá trị trong tập cho phép, còn lại → 'unknown'/None.
Mọi trường trả về là chuỗi/bool/null.
"""

import ipaddress
import re
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

from fastapi import APIRouter, Depends

from gh.auth import rbac, service
from gh.auth.deps import require
from gh.config import get_settings

router = APIRouter(tags=["system"])

MODES = ("local", "lan", "lan_legacy", "tailscale", "cloudflare")
BIND_ADDRS = ("127.0.0.1", "0.0.0.0")
NETWORK_FILE = "network-status.json"
_LABEL = r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
_SITE_RE = re.compile(rf"^{_LABEL}(?:\.{_LABEL})*$")


def is_local_url(url: str | None) -> bool:
    """True khi địa chỉ chỉ mở được trên chính máy chủ: localhost, *.localhost, 127.0.0.0/8, ::1, 0.0.0.0; rỗng/hỏng
    cũng coi là local (an toàn: cảnh báo thay vì gửi địa chỉ sai)."""
    raw = (url or "").strip()
    if not raw:
        return True
    try:
        host = urlparse(raw if "//" in raw else f"//{raw}").hostname
    except ValueError:
        return True
    if not host:
        return True
    host = host.lower().rstrip(".")
    if host == "localhost" or host.endswith(".localhost"):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return ip.is_loopback or ip.is_unspecified


def network_status() -> dict[str, Any] | None:
    """Nội dung đã lọc của run/network-status.json, hoặc None khi tệp thiếu/không an toàn/hỏng.
    `mode` ngoài tập cho phép → 'unknown'; `bind_addr`/`site_address` không hợp lệ → None."""
    from gh.system_api import update

    d = update._dir()
    raw = update._read_json(d / NETWORK_FILE) if d.is_dir() else None
    if raw is None:
        return None
    mode = raw.get("mode")
    bind = raw.get("bind_addr")
    site = raw.get("site_address")
    checked = raw.get("checked_at")
    checked_iso: str | None = None
    if isinstance(checked, str):
        try:
            checked_iso = (datetime.fromisoformat(checked.replace("Z", "+00:00")).astimezone(UTC)
                           .isoformat().replace("+00:00", "Z"))
        except ValueError:
            checked_iso = None
    return {"mode": mode if isinstance(mode, str) and mode in MODES else "unknown",
            "bind_addr": bind if isinstance(bind, str) and bind in BIND_ADDRS else None,
            "site_address": site if isinstance(site, str) and len(site) <= 253 and _SITE_RE.match(site) else None,
            "checked_at": checked_iso}


@router.get("/system/access")
async def get_access(user: service.CurrentUser = Depends(require("system.read"))) -> dict[str, Any]:
    """Địa chỉ đăng nhập (từ GH_PUBLIC_URL) + chế độ truy cập genh báo. Đổi chế độ bằng `genh remote` trên máy chủ."""
    public = get_settings().public_url.strip().rstrip("/")
    st = network_status() or {"mode": "unknown", "bind_addr": None, "site_address": None, "checked_at": None}
    return {"public_url": public, "login_url": f"{public}/login", "public_url_local": is_local_url(public),
            **st, "can_manage": user.role_code == rbac.OWNER}
