#!/usr/bin/env python3
"""Kiểm bản nhúng genh khớp deploy/ (F-44). Chỉ đọc, không sửa gì trên đĩa.

Dùng: python3 .github/scripts/check_embedded_sync.py [--root <repo>]
Exit 0 = khớp cả 3 cặp; 1 = lệch hoặc thiếu tệp.
"""

import argparse
import difflib
import sys
from pathlib import Path

EMB = "apps/genh/internal/compose/"
PAIRS = [
    ("deploy/compose.yaml", EMB + "embedded_compose.yaml"),
    ("deploy/proxy/Caddyfile", EMB + "embedded_Caddyfile"),
    ("deploy/browser/chromium-seccomp.json", EMB + "embedded_chromium-seccomp.json"),
]


def _text(data: bytes) -> list[str]:
    return data.decode("utf-8", errors="replace").splitlines(keepends=True)


def check(root: Path) -> int:
    bad = 0
    for deploy, emb in PAIRS:
        d, e = root / deploy, root / emb
        msg = f"::error::{emb} lệch {deploy} — genh phát hành sẽ chở bản cũ; chép lại: cp {deploy} {emb}"
        if not d.is_file() or not e.is_file():
            thieu = deploy if not d.is_file() else emb
            print(msg)
            print(f"(thiếu tệp: {thieu})")
            bad += 1
            continue
        a, b = d.read_bytes(), e.read_bytes()
        if a == b:
            continue
        print(msg)
        diff = list(difflib.unified_diff(_text(b), _text(a), fromfile=emb, tofile=deploy))
        for line in diff[:20]:
            print(line.rstrip("\n"))
        if not diff:
            print("(khác biệt ở mức byte, ví dụ ký tự xuống dòng)")
        bad += 1
    if bad:
        return 1
    print("OK: 3 tệp nhúng khớp deploy/")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--root", default=str(Path(__file__).resolve().parents[2]))
    args = p.parse_args(argv)
    return check(Path(args.root))


if __name__ == "__main__":
    sys.exit(main())
