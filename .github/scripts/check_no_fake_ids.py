#!/usr/bin/env python3
"""Kiểm tĩnh v0.1.35 (F-1): web không được dùng ID giả kiểu 'u-lan' / 'agent-ka' (danh sách người/trợ lý cứng).

Mọi ô chọn người / trợ lý phải lấy id THẬT (UUID) từ `GET /pickers/users` / `GET /pickers/agents`
(`apps/web/src/lib/pickers.ts`). Script quét *.ts / *.tsx dưới thư mục gốc (mặc định `apps/web/src`) và báo:
  - `id: 'u-…'` / `value: 'u-…'` (cả nháy kép và backtick),
  - `id: 'agent-…'` / `value: 'agent-…'` / `agent_id: 'agent-…'` (cả nháy kép và backtick).
Chuỗi `agent-` khác (className="agent-card", data-testid="agent-row", khoá i18n…) là hợp lệ. Dòng có chú thích
`allow-fake-id` được bỏ qua (chỉ dùng khi thật sự cần, vd. dữ liệu mẫu có chủ đích).

Cách chạy: `python3 .github/scripts/check_no_fake_ids.py [root]` — in `tệp:dòng` + dòng `::error::` cho GitHub
Actions, exit 1 nếu có vi phạm, 0 nếu sạch.
"""

from __future__ import annotations

import re
import sys
from collections.abc import Iterator
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DEFAULT_ROOT = REPO / "apps" / "web" / "src"
SUFFIXES = (".ts", ".tsx")

PATTERNS = (
    re.compile(r"""\b(id|value)\s*:\s*['"`]u-"""),
    re.compile(r"""\b(id|value|agent_id)\s*:\s*['"`]agent-"""),
)
ALLOW_MARK = "allow-fake-id"


def violations(root: Path) -> Iterator[tuple[Path, int, str]]:
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix not in SUFFIXES or "node_modules" in path.parts:
            continue
        for n, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), start=1):
            if ALLOW_MARK not in line and any(p.search(line) for p in PATTERNS):
                yield path, n, line.strip()


def main(argv: list[str]) -> int:
    root = Path(argv[1]) if len(argv) > 1 else DEFAULT_ROOT
    if not root.is_dir():
        print(f"::error::Không thấy thư mục {root}")
        return 2
    found = list(violations(root))
    for path, n, line in found:
        try:
            shown = path.resolve().relative_to(Path.cwd().resolve())
        except ValueError:
            shown = path
        print(f"{shown}:{n}: {line}")
        print(f"::error file={shown},line={n}::ID giả ('u-…' / 'agent-…') — lấy id thật từ /pickers/users hoặc "
              "/pickers/agents (apps/web/src/lib/pickers.ts)")
    if found:
        print(f"Có {len(found)} chỗ dùng ID giả.")
        return 1
    print(f"Sạch: không có ID giả trong {root}.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
