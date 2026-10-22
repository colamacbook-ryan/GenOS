#!/usr/bin/env python3
"""Kiểm ảnh api/browser có SẴN .pyc cho venv (v0.1.48, sửa sau review F-36) — thiếu thì mỗi tiến trình dịch lại cả cây thư viện.

uv (khác pip) KHÔNG tự sinh .pyc trừ khi đặt UV_COMPILE_BYTECODE=1. Trong ảnh, /opt/venv thuộc root, tiến trình chạy bằng
user thường và PYTHONDONTWRITEBYTECODE=1, nên thiếu .pyc thì api, worker, migrate và mọi `python -m gh.…` do genh gọi đều
dịch lại toàn bộ thư viện mỗi lần khởi động (~+1,3 giây CPU, chậm hơn trên máy arm64 yếu).

Đầu vào: log `python -v -c "import <module chính>"` (stderr) chạy TRONG ảnh, đúng user của ảnh. Với mỗi module, Python in
  # code object from '<…>.pyc'   ← nạp từ .pyc có sẵn
  # code object from <…>.py      ← phải dịch lại từ mã nguồn lúc chạy
Lỗi khi: có module dưới --prefix dịch lại từ mã nguồn, hoặc không module nào dưới --prefix nạp từ .pyc (import hỏng / sai
--prefix — không được coi là "sạch").

Dùng:  python3 .github/scripts/check_bytecode.py --title gh-api --prefix /opt/venv/ <log>
Thoát 0 nếu đạt, 1 nếu không đạt, 2 nếu sai tham số / không đọc được log.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# repr() của đường dẫn .pyc: '…' (hoặc "…" khi đường dẫn có dấu nháy đơn).
CACHED = re.compile(r"""^# code object from (['"])(?P<path>.+)\1\s*$""")
FROM_SOURCE = re.compile(r"^# code object from (?P<path>[^'\"].*\.py)\s*$")


def classify(log: str, prefix: str) -> tuple[list[str], list[str]]:
    """Trả (module dưới prefix nạp từ .pyc, module dưới prefix phải dịch lại từ .py)."""
    cached: list[str] = []
    source: list[str] = []
    for line in log.splitlines():
        m = CACHED.match(line)
        if m:
            if m.group("path").startswith(prefix):
                cached.append(m.group("path"))
            continue
        m = FROM_SOURCE.match(line)
        if m and m.group("path").startswith(prefix):
            source.append(m.group("path"))
    return cached, source


def check(log: str, prefix: str, title: str) -> tuple[bool, str]:
    cached, source = classify(log, prefix)
    if source:
        sample = "\n".join(f"  - {p}" for p in source[:10])
        more = f"\n  … và {len(source) - 10} module khác" if len(source) > 10 else ""
        return False, (
            f"::error::{title}: {len(source)} module dưới {prefix} phải dịch lại từ mã nguồn mỗi lần khởi động (thiếu .pyc) — "
            "đặt UV_COMPILE_BYTECODE=1 (hoặc `uv sync --compile-bytecode`) ở bước cài venv trong Dockerfile.\n"
            f"{sample}{more}"
        )
    if not cached:
        return False, (
            f"::error::{title}: không module nào dưới {prefix} được nạp từ .pyc — lệnh import trong ảnh hỏng hoặc sai "
            "--prefix (xem log `python -v` ở bước này)."
        )
    return True, f"OK: {title}: {len(cached)} module dưới {prefix} nạp từ .pyc có sẵn, 0 module phải dịch lại."


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--title", required=True, help="tên ảnh để in trong thông báo")
    p.add_argument("--prefix", default="/opt/venv/", help="chỉ xét module dưới thư mục này (mặc định /opt/venv/)")
    p.add_argument("log", type=Path, help="tệp chứa stderr của `python -v -c 'import …'`")
    args = p.parse_args(argv)
    try:
        log = args.log.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        print(f"check_bytecode: không đọc được {args.log}: {e}", file=sys.stderr)
        return 2
    ok, msg = check(log, args.prefix, args.title)
    print(msg, file=sys.stdout if ok else sys.stderr)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
