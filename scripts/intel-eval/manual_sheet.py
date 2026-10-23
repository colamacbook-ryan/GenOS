#!/usr/bin/env python3
# ruff: noqa: E501
"""manual_sheet.py — phiếu rà tay các mục KHÔNG GHÉP được (spec §2.1: "rà tay mục không ghép"; KET-QUA-v4 rà 100% mục không ghép).

    python3 scripts/intel-eval/manual_sheet.py --data kb1 --key "$GH_EVAL_KEY" --run /tmp/run/kb1/r1 --out /tmp/run/kb1/r1/phieu-ra-tay.md

Sinh một phiếu Markdown: mỗi đơn vị không ghép với sự việc nào của đáp án kèm loại, mức, tóm tắt, trích dẫn và ±2 tin xung quanh (từ bộ dữ liệu) để
người chấm quyết định đúng/sai; kèm khuôn `manual.json` ({"turn:unit_ref": "ok" | "bad"}) để `gkb_score.py --manual` đọc. Mục chưa rà ⇒ cổng precision
báo `manual_pending`, KHÔNG tự coi là đúng. Đáp án không in ra phiếu (chỉ id sự việc đã ghép)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import Dataset, load_dataset, write_json  # noqa: E402
from gkb_score import Run, load_key, score_run  # noqa: E402


def build_sheet(ds: Dataset, run: Run, key: dict[str, Any], manual: dict[str, str] | None = None) -> tuple[str, dict[str, str]]:
    sc = score_run(run, key, manual)
    pos = {m.id: i for i, m in enumerate(ds.msgs)}
    by_key = {u["key"]: u for u in run.units}
    lines = [f"# Phiếu rà tay — {ds.name} / {run.label}", "",
             f"Đơn vị: {sc['units']} · không ghép: {len(sc['unmatched_units'])} · chưa rà: {len(sc['manual_pending'])}", "",
             "Ghi `ok` (mục hợp lý, tin có thật việc đó) hoặc `bad` (nhiễu/sai) vào `manual.json`.", ""]
    template: dict[str, str] = {}
    for k in sc["unmatched_units"]:
        u = by_key[k]
        template[k] = (manual or {}).get(k, "")
        lines += [f"## {k} — loại `{u['kind']}` · mức `{u['level']}` · cổng `{u['gate']}`", f"> {u['summary']}", ""]
        for q in u["quotes"]:
            lines.append(f"- trích dẫn ({q['field']}) `{q['message_id']}`: «{q['text']}»")
        shown: set[str] = set()
        for mid in sorted(u["ids"]):
            i = pos.get(mid)
            if i is None:
                lines.append(f"- ⚠ tin `{mid}` không có trong bộ dữ liệu (id lạ)")
                continue
            for j in range(max(0, i - 2), min(len(ds.msgs), i + 3)):
                m = ds.msgs[j]
                if m.id in shown:
                    continue
                shown.add(m.id)
                lines.append(f"    {'→' if m.id == mid else ' '} [{m.ts:%d/%m %H:%M}] {m.group} · {m.sender_name}: {m.text}")
        lines.append("")
    return "\n".join(lines) + "\n", template


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--key")
    ap.add_argument("--run", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--manual")
    a = ap.parse_args()
    ds = load_dataset(a.data)
    manual = json.loads(Path(a.manual).read_text()) if a.manual else None
    md, template = build_sheet(ds, Run(a.run, ds), load_key(a.key), manual)
    Path(a.out).write_text(md, encoding="utf-8")
    write_json(Path(a.out).with_name("manual.template.json"), template)
    print(f"phiếu: {a.out} ({len(template)} mục)")


if __name__ == "__main__":
    main()
