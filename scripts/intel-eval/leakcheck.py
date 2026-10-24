#!/usr/bin/env python3
# ruff: noqa: E501
"""leakcheck.py — kiểm rò rỉ (spec §2.1 dòng "Rò rỉ"): chặn nếu ví dụ trong KỸ NĂNG trùng DỮ LIỆU KIỂM theo n-gram ≥ 8 từ.

    python3 scripts/intel-eval/leakcheck.py --skill gh/refinery/prompts/intel_v2_1.md [--skill …] --data kb1 --data kb2 [--extra-text key.json] [-n 8]

Chuẩn hoá: bỏ dấu, chữ thường, chỉ giữ chữ-số, tách từ. Dữ liệu kiểm = `text` của mọi tin trong `chats.jsonl` (+ mọi chuỗi trong `--extra-text`, ví dụ
đáp án nằm ngoài repo). Thoát mã 1 khi có trùng (in từng n-gram trùng + chỗ xuất hiện). Dùng được làm bước CI của M10 (chạy mỗi lần đổi kỹ năng)."""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import load_dataset  # noqa: E402

WORD = re.compile(r"[0-9a-z]+")


def words(text: str) -> list[str]:
    t = unicodedata.normalize("NFD", text.lower())
    t = "".join(c for c in t if unicodedata.category(c) != "Mn").replace("đ", "d")
    return WORD.findall(t)


def ngrams(ws: list[str], n: int) -> set[tuple[str, ...]]:
    return {tuple(ws[i:i + n]) for i in range(len(ws) - n + 1)}


def _strings(obj: Any) -> list[str]:
    if isinstance(obj, str):
        return [obj]
    if isinstance(obj, dict):
        return [s for v in obj.values() for s in _strings(v)]
    if isinstance(obj, list):
        return [s for v in obj for s in _strings(v)]
    return []


def data_index(texts: dict[str, str], n: int) -> dict[tuple[str, ...], str]:
    idx: dict[tuple[str, ...], str] = {}
    for src, t in texts.items():
        for g in ngrams(words(t), n):
            idx.setdefault(g, src)
    return idx


def check(skill_texts: dict[str, str], data_texts: dict[str, str], n: int = 8) -> list[dict[str, Any]]:
    """Trả danh sách trùng: {skill, ngram, source}. Rỗng = sạch."""
    idx = data_index(data_texts, n)
    hits: list[dict[str, Any]] = []
    for name, t in skill_texts.items():
        seen: set[tuple[str, ...]] = set()
        for g in sorted(ngrams(words(t), n)):
            if g in idx and g not in seen:
                seen.add(g)
                hits.append({"skill": name, "ngram": " ".join(g), "source": idx[g]})
    return hits


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--skill", action="append", required=True)
    ap.add_argument("--data", action="append", default=[])
    ap.add_argument("--extra-text", action="append", default=[], help="JSON/văn bản ngoài repo cũng coi là dữ liệu kiểm")
    ap.add_argument("-n", type=int, default=8)
    a = ap.parse_args()
    data: dict[str, str] = {}
    for d in a.data:
        ds = load_dataset(d)
        for m in ds.msgs:
            data[f"{ds.name}:{m.id}"] = m.text
    for p in a.extra_text:
        raw = Path(p).read_text(encoding="utf-8")
        try:
            for i, s in enumerate(_strings(json.loads(raw))):
                data[f"{Path(p).name}#{i}"] = s
        except json.JSONDecodeError:
            data[Path(p).name] = raw
    skills = {p: Path(p).read_text(encoding="utf-8") for p in a.skill}
    hits = check(skills, data, a.n)
    for h in hits:
        print(f"RÒ RỈ  {h['skill']}  ⟵  {h['source']}:  «{h['ngram']}»", file=sys.stderr)
    print(json.dumps({"n": a.n, "skills": len(skills), "data_texts": len(data), "leaks": len(hits)}, ensure_ascii=False))
    sys.exit(1 if hits else 0)


if __name__ == "__main__":
    main()
