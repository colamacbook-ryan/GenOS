#!/usr/bin/env python3
"""Kiểm link chết trong tài liệu (v0.1.50, F-90): link markdown tương đối `](đường-dẫn)` và `](đường-dẫn#neo)`.

Quét: README.md, CHANGELOG.md, CLAUDE.md, docs/*.md (gồm docs/runbook.md), docs/design/*.md, docs/releases/*.md và
docs/reports/HANDOFF-v0.1.1.md. Mỗi link tương đối phải trỏ tới tệp/thư mục CÓ THẬT (tính từ thư mục chứa tài liệu);
nếu có `#neo` và đích là tệp .md thì tiêu đề tương ứng (kiểu GitHub) cũng phải có.
Link `#neo` thuần kiểm trong chính tệp đó.

Bỏ qua: link ngoài (http://, https://, mailto:, tel:), link bắt đầu bằng `/`, và mọi thứ nằm trong khối mã (```) hoặc
đoạn mã dòng (`...`) — ví dụ cú pháp trong tài liệu không bị coi là link.

Cách chạy: `python3 .github/scripts/check_doc_links.py [root]` — in `tệp:dòng: link chết → đích (lý do)` +
dòng `::error::` cho
GitHub Actions, exit 1 nếu có link chết, 0 nếu sạch, 2 nếu `root` không tồn tại.
"""

from __future__ import annotations

import re
import sys
import unicodedata
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote

REPO = Path(__file__).resolve().parents[2]

# Tệp được quét, tính từ gốc (glob).
SCAN_GLOBS = (
    "README.md",
    "CHANGELOG.md",
    "CLAUDE.md",
    "docs/*.md",
    "docs/design/*.md",
    "docs/releases/*.md",
    "docs/runbook.md",
    "docs/reports/HANDOFF-v0.1.1.md",
)

# `[chữ](đích)` hoặc `![alt](đích)`; đích có thể bọc <...> và kèm tiêu đề "..." / '...'.
LINK_RE = re.compile(r"""\]\(\s*(<[^>]*>|[^)\s]+)(?:\s+(?:"[^"]*"|'[^']*'))?\s*\)""")
FENCE_RE = re.compile(r"^\s{0,3}(`{3,}|~{3,})")
HEADING_RE = re.compile(r"^\s{0,3}(#{1,6})\s+(.+?)\s*#*\s*$")
INLINE_CODE_RE = re.compile(r"(`+)(.+?)\1")
EXTERNAL_PREFIXES = ("http://", "https://", "mailto:", "tel:", "ftp://", "data:")


@dataclass(frozen=True)
class Dead:
    path: Path  # tệp chứa link
    line: int
    target: str  # đích như viết trong tài liệu
    reason: str


def scan_files(root: Path) -> list[Path]:
    seen: dict[Path, None] = {}
    for pattern in SCAN_GLOBS:
        for p in sorted(root.glob(pattern)):
            if p.is_file():
                seen.setdefault(p, None)
    return list(seen)


def _lines_outside_code(text: str) -> Iterator[tuple[int, str]]:
    """(số dòng, dòng đã bỏ đoạn mã dòng) cho các dòng NẰM NGOÀI khối mã."""
    fence: str | None = None
    for n, raw in enumerate(text.splitlines(), start=1):
        m = FENCE_RE.match(raw)
        if m:
            mark = m.group(1)
            if fence is None:
                fence = mark[0]
            elif mark[0] == fence:
                fence = None
            continue
        if fence is not None:
            continue
        yield n, INLINE_CODE_RE.sub(lambda mm: " " * len(mm.group(0)), raw)


def _slug_text(heading: str) -> str:
    h = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", heading)  # link → chữ
    h = re.sub(r"<[^>]+>", "", h)  # thẻ HTML
    h = h.replace("`", "").replace("*", "")
    return h.strip()


def github_slug(heading: str) -> str:
    """Neo kiểu GitHub: chữ thường, bỏ ký tự không phải chữ/số/gạch ngang/gạch dưới/khoảng trắng; ` ` → `-`."""
    s = unicodedata.normalize("NFC", _slug_text(heading)).lower()
    s = re.sub(r"[^\w\- ]", "", s, flags=re.UNICODE)
    return s.replace(" ", "-")


def heading_anchors(text: str) -> set[str]:
    """Tập neo của mọi tiêu đề (tiêu đề trùng thêm hậu tố -1, -2… như GitHub)."""
    anchors: set[str] = set()
    counts: dict[str, int] = {}
    fence: str | None = None
    for raw in text.splitlines():
        m = FENCE_RE.match(raw)
        if m:
            mark = m.group(1)
            if fence is None:
                fence = mark[0]
            elif mark[0] == fence:
                fence = None
            continue
        if fence is not None:
            continue
        h = HEADING_RE.match(raw)
        if not h:
            continue
        base = github_slug(h.group(2))
        k = counts.get(base, 0)
        counts[base] = k + 1
        anchors.add(base if k == 0 else f"{base}-{k}")
    return anchors


def _split_target(raw: str) -> tuple[str, str]:
    """(đường dẫn, neo) của đích link; bỏ `<...>`, bỏ `?truy-vấn`, giải mã %20."""
    t = raw[1:-1] if raw.startswith("<") and raw.endswith(">") else raw
    path, _, frag = t.partition("#")
    path = path.split("?", 1)[0]
    return unquote(path), unicodedata.normalize("NFC", unquote(frag)).lower()


def dead_links(root: Path) -> list[Dead]:
    root = root.resolve()
    cache: dict[Path, set[str]] = {}

    def anchors_of(p: Path) -> set[str]:
        if p not in cache:
            cache[p] = heading_anchors(p.read_text(encoding="utf-8", errors="replace"))
        return cache[p]

    out: list[Dead] = []
    for doc in scan_files(root):
        text = doc.read_text(encoding="utf-8", errors="replace")
        for n, line in _lines_outside_code(text):
            for m in LINK_RE.finditer(line):
                raw = m.group(1)
                probe = raw[1:-1] if raw.startswith("<") and raw.endswith(">") else raw
                if probe.lower().startswith(EXTERNAL_PREFIXES) or probe.startswith("/"):
                    continue
                path_part, frag = _split_target(raw)
                if not path_part:  # `#neo` thuần: neo trong chính tệp này
                    if frag and frag not in anchors_of(doc):
                        out.append(Dead(doc, n, raw, f"không có tiêu đề tạo neo #{frag} trong chính tệp"))
                    continue
                target = (doc.parent / path_part).resolve()
                try:
                    target.relative_to(root)
                except ValueError:
                    out.append(Dead(doc, n, raw, "trỏ ra ngoài repo"))
                    continue
                if not target.exists():
                    out.append(Dead(doc, n, raw, "không có tệp/thư mục này"))
                    continue
                if frag and target.is_file() and target.suffix.lower() == ".md":
                    if frag not in anchors_of(target):
                        where = target.relative_to(root)
                        out.append(Dead(doc, n, raw, f"không có tiêu đề tạo neo #{frag} trong {where}"))
    return out


def main(argv: list[str]) -> int:
    root = Path(argv[1]) if len(argv) > 1 else REPO
    if not root.is_dir():
        print(f"::error::Không thấy thư mục {root}")
        return 2
    dead = dead_links(root)
    for d in dead:
        try:
            shown = d.path.resolve().relative_to(root.resolve())
        except ValueError:
            shown = d.path
        print(f"{shown}:{d.line}: link chết → {d.target} ({d.reason})")
        print(f"::error file={shown},line={d.line}::link chết → {d.target} ({d.reason})")
    if dead:
        print(f"{len(dead)} link chết trong {len(scan_files(root))} tài liệu đã quét.")
        return 1
    print(f"OK — {len(scan_files(root))} tài liệu, không có link chết.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
