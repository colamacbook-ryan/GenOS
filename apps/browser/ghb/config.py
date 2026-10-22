"""Cấu hình browser-worker (biến môi trường tiền tố GH_). Worker KHÔNG có URL CSDL và KHÔNG có khoá master."""

import base64
import binascii
import os
from dataclasses import dataclass, field


def decode_key(raw: str) -> bytes:
    """32 byte dạng hex (64 ký tự — `genh` sinh) hoặc base64 (`make secrets`) — như `gh.crypto.decode_key`."""
    raw = raw.strip()
    if len(raw) == 64:
        try:
            return bytes.fromhex(raw)
        except ValueError:
            pass
    try:
        key = base64.b64decode(raw, validate=True)
    except (ValueError, binascii.Error) as e:
        raise ValueError("khoá browser phải là 32 byte dạng hex hoặc base64") from e
    if len(key) != 32:
        raise ValueError("khoá browser phải là 32 byte dạng hex hoặc base64")
    return key


def _env_bool(name: str, default: bool) -> bool:
    v = os.environ.get(name)
    return default if v is None else v.strip().lower() in ("1", "true", "yes", "on")


@dataclass
class Config:
    redis_url: str = "redis://localhost:6379/0"
    key: bytes = b""
    # Proxy ra ngoài (dịch vụ browser-egress): chỉ cho phép tên miền của nền tảng, chặn IP nội bộ.
    proxy: str | None = None
    headless: bool = True
    max_jobs: int = 2
    # Nghỉ CỐ ĐỊNH giữa các thao tác — để lịch sự với nền tảng (giới hạn tốc độ), KHÔNG phải để giả người hay né
    # chống bot; không ngẫu nhiên.
    delay: float = 3.0
    viewport: dict[str, int] = field(default_factory=lambda: {"width": 1280, "height": 800})
    locale: str = "vi-VN"
    timezone: str = "Asia/Ho_Chi_Minh"
    nav_timeout_ms: int = 30_000
    consumer: str = "browser-1"


def parse_delay(raw: str) -> float:
    """'3' → 3.0; dạng cũ 'lo,hi' → lấy giá trị LỚN hơn (lịch sự hơn); kẹp [1, 30]."""
    try:
        v = max(float(x) for x in raw.split(",") if x.strip())
    except ValueError:
        v = 3.0
    return min(30.0, max(1.0, v))


def load() -> Config:
    raw = os.environ.get("GH_BROWSER_KEY", "")
    path = os.environ.get("GH_BROWSER_KEY_FILE")
    if path:
        with open(path, encoding="utf-8") as f:
            raw = f.read().strip()
    if not raw:
        raise RuntimeError("Thiếu khoá browser (GH_BROWSER_KEY_FILE / GH_BROWSER_KEY)")
    return Config(redis_url=os.environ.get("GH_REDIS_URL", "redis://localhost:6379/0"), key=decode_key(raw),
                  proxy=os.environ.get("GH_BROWSER_PROXY") or None,
                  headless=_env_bool("GH_BROWSER_HEADLESS", True),
                  max_jobs=max(1, min(4, int(os.environ.get("GH_BROWSER_MAX_JOBS", "2")))),
                  delay=parse_delay(os.environ.get("GH_BROWSER_DELAY", "3")),
                  consumer=os.environ.get("HOSTNAME", "browser-1"))
