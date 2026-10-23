#!/usr/bin/env python3
# ruff: noqa: E501
"""compare_b.py — red team B "so pipeline" (spec §10): lô 40 tin trộn nhóm HIỆN CÓ vs lượt theo luồng, trên kb1–kb4, recall sự việc MỨC TRÍCH XUẤT.

    # 1) cùng điểm cắm phân tích, hai kế hoạch:
    replay.py --data kb1 --out /tmp/b/kb1/batch --plan batch40 --analyzer adapter:A
    replay.py --data kb1 --out /tmp/b/kb1/thread --suite --repeat 3 --analyzer adapter:A
    # 2) so:
    compare_b.py --data kb1 --key "$GH_EVAL_KEY" --batch /tmp/b/kb1/batch --thread /tmp/b/kb1/r1 /tmp/b/kb1/r2 /tmp/b/kb1/r3

Mức trích xuất = recall theo sự việc (ghép chặt + lệch loại), recall sự thật, bằng chứng; KHÔNG chấm mức 🔴 (mức là việc của `levels.py`).
Kết luận `thread_not_worse` = trung bình lượt-theo-luồng ≥ lô 40 − 0,02 (độ lệch tối thiểu 2 điểm %). Đáp án qua --key/GH_EVAL_KEY (ngoài repo)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import Dataset, load_dataset, write_json  # noqa: E402
from gkb_score import Run, load_key, score_run  # noqa: E402

TOLERANCE = 0.02


def _extract_metrics(s: dict[str, Any]) -> dict[str, Any]:
    return {"recall_event": s["recall_event"]["p"], "recall_event_tight": s["recall_event_tight"]["p"], "facts_recall": s["facts_recall"]["p"],
            "evidence": s["evidence"]["p"], "contract_errors": s["contract_errors"], "units": s["units"]}


def compare(ds: Dataset, key: dict[str, Any], batch_dirs: list[str], thread_dirs: list[str]) -> dict[str, Any]:
    b = [_extract_metrics(score_run(Run(p, ds), key)) for p in batch_dirs]
    t = [_extract_metrics(score_run(Run(p, ds), key)) for p in thread_dirs]

    def mean(xs: list[dict[str, Any]], k: str) -> float | None:
        v = [x[k] for x in xs if x[k] is not None]
        return sum(v) / len(v) if v else None

    rows = {k: {"batch40": mean(b, k), "thread": mean(t, k)} for k in ("recall_event", "recall_event_tight", "facts_recall", "evidence", "units")}
    rb, rt = rows["recall_event"]["batch40"], rows["recall_event"]["thread"]
    return {"dataset": ds.name, "rows": rows, "batch_runs": b, "thread_runs": t,
            "thread_not_worse": (rt is not None and rb is not None and rt >= rb - TOLERANCE),
            "delta_recall_event": (None if rt is None or rb is None else rt - rb), "tolerance": TOLERANCE}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--key")
    ap.add_argument("--batch", nargs="+", required=True)
    ap.add_argument("--thread", nargs="+", required=True)
    ap.add_argument("--out")
    a = ap.parse_args()
    res = compare(load_dataset(a.data), load_key(a.key), a.batch, a.thread)
    if a.out:
        write_json(a.out, res)
    print(json.dumps({k: res[k] for k in ("rows", "delta_recall_event", "thread_not_worse")}, ensure_ascii=False, indent=2))
    sys.exit(0 if res["thread_not_worse"] else 1)


if __name__ == "__main__":
    main()
