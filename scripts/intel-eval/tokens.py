#!/usr/bin/env python3
# ruff: noqa: E501
"""tokens.py — S7 "token/lượt thật" (spec §10): đo token vào/ra/cache MỖI LƯỢT, tin/lượt, lượt/ngày theo nhịp trên các lần replay (cả hai chế độ),
rồi so với giả định của bảng §4D.5 (≈ 8,9k vào không cache + ~5k kỹ năng cache + ~1,2k ra) để hiệu chỉnh và ước chi phí một vòng G-KB.

    python3 scripts/intel-eval/tokens.py --runs /tmp/run/kb1/r1 /tmp/run/kb1/ref [--prices prices.json] [--out s7.json]

Giá: KHÔNG có giá mặc định (Owner nhập — §4D.5, CNS-099). `--prices` = {"input_vnd_per_m":…, "output_vnd_per_m":…, "cache_read_vnd_per_m":…, "cache_write_vnd_per_m":…};
không đưa thì chỉ báo token, `cost_vnd = null`. Gói thuê bao CLI = 0 ₫ (đưa giá 0 cho đúng chuyện đó). Đầu vào: `tokens.jsonl` + `turns.jsonl` của lần chạy."""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import quantiles, write_json  # noqa: E402

#: Giả định ƯỚC TÍNH của spec §4D.5 để đối chiếu (token/lượt).
SPEC_ASSUMPTION = {"input": 8_900, "cache_read": 5_000, "output": 1_200}
FIELDS = ("input", "output", "cache_read", "cache_write")


def _jl(p: Path) -> list[dict[str, Any]]:
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()] if p.exists() else []


def cost_of(usage: dict[str, float], prices: dict[str, float] | None) -> float | None:
    if prices is None:
        return None
    return sum(usage.get(f, 0) * prices.get(f"{f}_vnd_per_m", 0.0) / 1_000_000 for f in FIELDS)


def measure(run_dir: str | Path, prices: dict[str, float] | None = None) -> dict[str, Any]:
    d = Path(run_dir)
    toks, turns = _jl(d / "tokens.jsonl"), {t["n"]: t for t in _jl(d / "turns.jsonl")}
    summary = json.loads((d / "summary.json").read_text()) if (d / "summary.json").exists() else {}
    per_field = {f: [float(t["usage"].get(f, 0) or 0) for t in toks if t.get("usage")] for f in FIELDS}
    calls = sum(int((t.get("usage") or {}).get("calls", 1) or 0) for t in toks)
    by_day: dict[str, int] = defaultdict(int)
    for t in turns.values():
        by_day[t.get("orig_day", "")] += 1
    total = {f: sum(per_field[f]) for f in FIELDS}
    per_turn_cost = [cost_of({f: float((t.get("usage") or {}).get(f, 0) or 0) for f in FIELDS}, prices) for t in toks]
    models = sorted({(t.get("usage") or {}).get("model", "?") for t in toks})
    return {
        "run": d.name, "mode": summary.get("mode"), "turns": len(toks), "model_calls": calls, "models": models,
        "tokens_per_turn": {f: quantiles(per_field[f]) for f in FIELDS}, "tokens_total": total,
        "new_msgs_per_turn": quantiles([float(t["new_n"]) for t in toks]), "turns_per_orig_day": quantiles([float(v) for v in by_day.values()]),
        "turns_per_day_max": max(by_day.values(), default=0), "latency_s": quantiles([float(t.get("latency_s") or 0) for t in toks]),
        "attempts_gt1": sum(1 for t in toks if int(t.get("attempts") or 1) > 1),
        "cost_vnd_total": (None if prices is None else sum(c or 0 for c in per_turn_cost)),
        "cost_vnd_per_turn_mean": (None if prices is None or not toks else sum(c or 0 for c in per_turn_cost) / len(toks)),
        "vs_spec_assumption": {f: {"spec": SPEC_ASSUMPTION[f], "measured_mean": quantiles(per_field[f])["mean"]} for f in SPEC_ASSUMPTION},
        "real_usage_present": any(t.get("usage") and any((t["usage"].get(f) or 0) for f in FIELDS) for t in toks),
    }


def report(run_dirs: list[str], prices: dict[str, float] | None = None) -> dict[str, Any]:
    runs = [measure(r, prices) for r in run_dirs]
    modes: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in runs:
        modes[r["mode"] or "?"].append(r)
    return {"runs": runs, "by_mode": {m: {"runs": len(v), "turns": sum(x["turns"] for x in v), "tokens_total": {f: sum(x["tokens_total"][f] for x in v) for f in FIELDS},
                                          "cost_vnd_total": (None if prices is None else sum(x["cost_vnd_total"] or 0 for x in v))} for m, v in modes.items()},
            "note": "usage toàn 0 ⇒ điểm cắm phân tích là StubAnalyzer hoặc chưa báo token; S7 chỉ có nghĩa khi L2/L3 cắm vào" if not any(r["real_usage_present"] for r in runs) else None}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", nargs="+", required=True)
    ap.add_argument("--prices")
    ap.add_argument("--out")
    a = ap.parse_args()
    prices = json.loads(Path(a.prices).read_text()) if a.prices else None
    rep = report(a.runs, prices)
    if a.out:
        write_json(a.out, rep)
    json.dump(rep, sys.stdout, ensure_ascii=False, indent=2)
    print()


if __name__ == "__main__":
    main()
