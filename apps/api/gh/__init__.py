"""Gen-Harness (Genesis Harness OS) — API, worker và chassis plugin.

v0.1.36 (F-46): `__version__` không còn viết cứng — đọc theo thứ tự:
1. biến môi trường `GH_VERSION` (ảnh Docker: `ARG VERSION` → `ENV GH_VERSION`, release.yml truyền build-arg);
2. tệp `VERSION` ở một thư mục cha của gói (chạy từ repo khi phát triển/CI);
3. không có cả hai ⇒ `"dev"`.
"""

import os
from pathlib import Path


def _read_version() -> str:
    env = os.environ.get("GH_VERSION", "").strip()
    if env:
        return env
    for parent in Path(__file__).resolve().parents:
        f = parent / "VERSION"
        try:
            if f.is_file():
                v = f.read_text(encoding="utf-8").strip()
                if v:
                    return v
        except OSError:
            continue
    return "dev"


__version__ = _read_version()
