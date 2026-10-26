#!/usr/bin/env python3
"""Rào rò rỉ đáp án G-KB (v0.1.77, CNS-158): bộ vector `levels` trong repo không được nhắc mã kịch bản chấm ngoài repo.

Fail (exit 1) khi:
  - fixture `apps/api/tests/fixtures/levels_vectors.json` có: kb[1-4], \\bS\\d{2}\\b, \\bT\\d{2}\\b, ALR-\\d+, m0\\d{3}, context.md;
  - repo có file tên `int19-edge-cases*` hoặc `chats.jsonl`.
Mẫu giờ ISO (`2026-10-23T08:00`) được gỡ trước khi quét để `\\bT\\d{2}\\b` không báo nhầm "T08"; mọi chuỗi khác đều bị quét.

Cách chạy: `python3 .github/scripts/check_no_gkb_leak.py [root]`.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
FIXTURE = Path("apps/api/tests/fixtures/levels_vectors.json")
PATTERNS = {
    "kb[1-4]": re.compile(r"kb[1-4]"),
    r"\bS\d{2}\b": re.compile(r"\bS\d{2}\b"),
    r"\bT\d{2}\b": re.compile(r"\bT\d{2}\b"),
    r"ALR-\d+": re.compile(r"ALR-\d+"),
    r"m0\d{3}": re.compile(r"m0\d{3}"),
    "context.md": re.compile(r"context\.md"),
}
ISO_TIME = re.compile(r"(?<=\d)T\d{2}(?=:\d{2})")  # "…-23T08:00…" → chỉ gỡ chữ T+giờ của mẫu thời gian
FORBIDDEN_FILES = ("int19-edge-cases*", "chats.jsonl")
SKIP_DIRS = {".git", "node_modules", ".venv", "__pycache__"}


def scan_text(text: str) -> list[str]:
    text = ISO_TIME.sub("", text)
    return [name for name, rx in PATTERNS.items() if rx.search(text)]


def forbidden_files(root: Path) -> list[Path]:
    found: list[Path] = []
    for pat in FORBIDDEN_FILES:
        found += [p for p in root.rglob(pat) if not SKIP_DIRS & set(p.relative_to(root).parts)]
    return sorted(found)


def check(root: Path) -> list[str]:
    errors: list[str] = []
    fx = root / FIXTURE
    if not fx.is_file():
        errors.append(f"thiếu fixture {FIXTURE}")
    else:
        for n, line in enumerate(fx.read_text(encoding="utf-8").splitlines(), 1):
            errors += [f"{FIXTURE}:{n}: có mẫu cấm {name}" for name in scan_text(line)]
    errors += [f"{p.relative_to(root)}: file đáp án G-KB không được nằm trong repo" for p in forbidden_files(root)]
    return errors


def main(argv: list[str]) -> int:
    root = Path(argv[1]) if len(argv) > 1 else REPO
    errors = check(root)
    for e in errors:
        print(f"::error::{e}")
    print("check_no_gkb_leak: " + ("ĐỎ" if errors else "sạch"))
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
