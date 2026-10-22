#!/usr/bin/env python3
"""Kiểm BẢN BUILD TÁI LẬP của ảnh Python (v0.1.48, F-19).

Cách dùng:
  python3 .github/scripts/check_image_lock.py --lock apps/api/uv.lock --project gen-harness-api freeze1.txt [freeze2.txt ...]

Mỗi tệp freeze là danh sách `Tên==phiên_bản` của các gói Python trong ảnh (một lần build). Script so với uv.lock
(đọc bằng tomllib, tên chuẩn hoá PEP 503) và báo lỗi (exit 1, `::error::` tiếng Việt, liệt kê từng gói) khi:
  - các tệp freeze khác nhau (hai lần build ra hai tổ hợp gói khác nhau — build không tái lập);
  - gói trong freeze (trừ chính --project) không có trong uv.lock hoặc khác phiên bản;
  - dependency trực tiếp của --project trong lock (mục `dependencies` không có `marker`) vắng mặt trong freeze.
Thoát 0 và in `OK: <n> gói khớp uv.lock qua <k> lần build`; thoát 2 khi thiếu tệp/uv.lock hỏng/--project không có trong lock.
"""

from __future__ import annotations

import argparse
import re
import sys
import tomllib
from pathlib import Path
from typing import Any, NoReturn


def norm(name: str) -> str:
    """Chuẩn hoá tên gói theo PEP 503."""
    return re.sub(r"[-_.]+", "-", name.strip()).lower()


def die(msg: str) -> NoReturn:
    print(f"check_image_lock: {msg}", file=sys.stderr)
    sys.exit(2)


def read_lock(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        die(f"không thấy uv.lock: {path} — chạy từ gốc repo hoặc truyền đúng --lock.")
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as e:
        die(f"uv.lock hỏng ({path}): {e}")
    pkgs = data.get("package")
    if not isinstance(pkgs, list) or not pkgs:
        die(f"uv.lock không có mục [[package]] nào ({path}).")
    return [p for p in pkgs if isinstance(p, dict) and "name" in p]


def read_freeze(path: Path) -> dict[str, str]:
    if not path.is_file():
        die(f"không thấy tệp freeze: {path}")
    out: dict[str, str] = {}
    for n, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "==" not in line:
            die(f"{path}:{n}: dòng freeze không có dạng Tên==phiên_bản: {line!r}")
        name, _, ver = line.partition("==")
        out[norm(name)] = ver.strip()
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Kiểm gói trong ảnh khớp uv.lock và các lần build giống nhau.")
    ap.add_argument("--lock", type=Path, required=True, help="đường dẫn uv.lock")
    ap.add_argument("--project", required=True, help="tên dự án trong lock (vd gen-harness-api)")
    ap.add_argument("freezes", nargs="+", type=Path, help="tệp freeze (Tên==phiên_bản), mỗi lần build một tệp")
    args = ap.parse_args(argv)

    packages = read_lock(args.lock)
    project = norm(args.project)
    versions: dict[str, set[str]] = {}
    proj_pkg: dict[str, Any] | None = None
    for p in packages:
        n = norm(str(p["name"]))
        versions.setdefault(n, set()).add(str(p.get("version", "")))
        if n == project:
            proj_pkg = p
    if proj_pkg is None:
        die(f"--project {args.project} không có trong {args.lock} — kiểm tên dự án (trường name trong pyproject.toml).")
    direct = sorted(
        {
            norm(str(d["name"]))
            for d in proj_pkg.get("dependencies") or []
            if isinstance(d, dict) and "name" in d and "marker" not in d
        }
    )

    freezes = [read_freeze(f) for f in args.freezes]
    errs: list[str] = []

    first = freezes[0]
    for i, other in enumerate(freezes[1:], 2):
        if other != first:
            diff = sorted(k for k in set(first) | set(other) if first.get(k) != other.get(k))
            lines = ", ".join(f"{k} ({first.get(k, 'vắng')} ≠ {other.get(k, 'vắng')})" for k in diff)
            errs.append(
                f"lần build 1 và lần build {i} ra tổ hợp gói khác nhau — {lines}; ghim mọi gói bằng `uv sync --frozen` "
                "trong Dockerfile (không `pip install` không khoá phiên bản)"
            )

    for i, fr in enumerate(freezes, 1):
        for name, ver in sorted(fr.items()):
            if name == project:
                continue
            if name not in versions:
                errs.append(
                    f"lần build {i}: gói {name}=={ver} trong ảnh KHÔNG có trong {args.lock} — ảnh cài ngoài lock; "
                    "bỏ gói thừa khỏi Dockerfile hoặc thêm vào pyproject.toml rồi chạy `uv lock` và commit"
                )
            elif ver not in versions[name]:
                errs.append(
                    f"lần build {i}: gói {name} phiên bản {ver} trong ảnh khác uv.lock ({', '.join(sorted(versions[name]))}) — "
                    "ảnh phải cài đúng lock: dùng `uv sync --frozen` trong Dockerfile"
                )
        for name in direct:
            if name not in fr:
                errs.append(
                    f"lần build {i}: dependency trực tiếp {name} của {args.project} (theo {args.lock}) vắng mặt trong ảnh — "
                    "kiểm Dockerfile có `uv sync --frozen` đúng dự án, rồi build lại"
                )

    if errs:
        print(f"Tái lập ảnh: {len(errs)} lỗi:", file=sys.stderr)
        for e in errs:
            print(f"::error::{e}", file=sys.stderr)
        return 1
    n = len([k for k in first if k != project])
    print(f"OK: {n} gói khớp uv.lock qua {len(freezes)} lần build")
    return 0


if __name__ == "__main__":
    sys.exit(main())
