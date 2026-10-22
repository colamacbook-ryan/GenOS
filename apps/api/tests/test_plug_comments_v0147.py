"""F-92 — chốt chặn: không còn chú thích 'CHỖ CẮM v<số>' trong mã nguồn được git theo dõi (apps, packages, deploy)."""

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
PLUG = re.compile(r"CHỖ CẮM v\d")
SKIP = ("node_modules/", "docs/audit/")
SELF = Path(__file__).resolve()


def test_no_versioned_plug_comments() -> None:
    out = subprocess.run(["git", "ls-files", "-z", "--", "apps", "packages", "deploy"], cwd=ROOT, check=True,
                         capture_output=True).stdout.decode()
    hits = []
    for rel in filter(None, out.split("\0")):
        p = ROOT / rel
        if any(s in rel for s in SKIP) or p.resolve() == SELF or not p.is_file():
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        hits += [f"{rel}:{i}" for i, line in enumerate(text.splitlines(), 1) if PLUG.search(line)]
    assert not hits, f"còn chú thích 'CHỖ CẮM v<số>' (không ghi số phiên bản vào chú thích chỗ cắm): {hits}"
