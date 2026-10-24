#!/usr/bin/env python3
# ruff: noqa: E501
"""gkb_score.py — bộ chấm G-KB tự động trên ĐẦU RA THẬT của pipeline (spec §2.1 bảng "Ngưỡng G-KB", §2.2 M10, §4F).

    python3 scripts/intel-eval/gkb_score.py --data .../kb1 --key "$GH_EVAL_KEY" --runs /tmp/run/kb1/r1 /tmp/run/kb1/r2 /tmp/run/kb1/r3 \\
        [--unconfirmed /tmp/run/kb1/u1] [--manual manual.json] [--baseline gate_prev.json] [--out /tmp/run/kb1/gate.json]

ĐÁP ÁN KHÔNG nằm trong repo: script nhận đường dẫn qua `--key` hoặc biến môi trường `GH_EVAL_KEY` (tệp JSON `intel-key/1`, xem dưới); thiếu ⇒ thoát.
Đáp án thật giữ ngoài repo GenOS và ngoài SB (scratchpad `evalkey*/`); test của L6 chỉ dùng đáp án giả tí hon tự tạo.

**Khuôn đáp án `intel-key/1`** (L6 định nghĩa — đáp án thật ở `evalkey*` cần một bước chuyển sang khuôn này, ghi ở báo cáo):
```
{"format": "intel-key/1", "dataset": "kb1",
 "events": [{"id": "E1", "kind": "rui_ro_lua_dao", "message_ids": ["m9999"], "label": "R|B|N",   # nhãn INT-19 (KET-QUA-v4 §0)
             "gate": "lua_dao_dang_dien_ra|an_toan|…|null", "set": "kb|edge", "old_level": "red|amber|grey|off|null",
             "credentials_entered": false, "near_gate": null, "people": ["u_x"]}],
 "facts": [{"id": "F1", "message_ids": ["m9998"], "predicate": "payment_account"}],
 "traps": {"hard": [{"id": "T1", "message_ids": ["m9997"]}], "privacy": [{"id": "P1", "message_ids": ["m9996"]}],
           "no_opportunity_groups": ["G2"]}}
```
Cách ghép (như KET-QUA-v4 §0): hai mục ghép CHẶT khi cùng loại hợp lệ và có ≥ 1 `message_id` chung; ghép cực đại 1-1; "recall theo sự việc" gồm ghép
chặt + lệch loại (chung tin, khác loại). Mức của đơn vị = `level_hint`, hoặc `levels.json` của lần chạy (pipeline `levels.compute`) nếu có.
`message_id` chuẩn hoá qua `idmap.json` của lần chạy (m_<id> → id dữ liệu; id lạ của pipeline thêm vào idmap)."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import unicodedata
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (  # noqa: E402
    Dataset,
    brain_contract,
    load_dataset,
    quantiles,
    refuse_inside_repo,
    wilson,
    write_json,
)

RED_GATES_STRICT = ("lua_dao_dang_dien_ra", "an_toan")
PEOPLE_EVAL_RE = re.compile(r"(tính cách|đáng tin|không đáng tin|thiếu năng lực|kém cỏi|lười|thái độ (kém|tệ)|nhân phẩm|đánh giá (nhân viên|người))", re.I)
LEVELS = ("red", "amber", "grey", "off")

# ─── Nạp ───────────────────────────────────────────────────────────────────────────────────────────────────────────────────

def load_key(path: str | os.PathLike[str] | None) -> dict[str, Any]:
    path = path or os.environ.get("GH_EVAL_KEY")
    if not path:
        raise SystemExit("gkb_score: thiếu đáp án — đưa --key <tệp> hoặc đặt GH_EVAL_KEY (đáp án nằm NGOÀI repo)")
    p = Path(path)
    if not p.is_file():
        raise SystemExit(f"gkb_score: không thấy tệp đáp án {p}")
    here = Path(__file__).resolve().parents[2]
    if here in p.resolve().parents:
        raise SystemExit("gkb_score: đáp án nằm TRONG repo — không được (spec §2.1: đáp án giữ ngoài repo GenOS)")
    key = json.loads(p.read_text(encoding="utf-8"))
    if key.get("format") != "intel-key/1":
        raise SystemExit(f"gkb_score: khuôn đáp án lạ {key.get('format')!r} (cần 'intel-key/1')")
    return key


def fold(s: str) -> str:
    s = unicodedata.normalize("NFD", s.lower())
    return re.sub(r"\s+", " ", "".join(c for c in s if unicodedata.category(c) != "Mn").replace("đ", "d")).strip()


class Run:
    """Một lần chạy đã nạp: các đơn vị phẳng (khoá `turn:unit_ref`), sự thật đề xuất, số lỗi hợp đồng."""

    def __init__(self, path: str | Path, ds: Dataset) -> None:
        self.path = Path(path)
        self.label = self.path.name
        self.ds = ds
        self.idmap: dict[str, str] = json.loads((self.path / "idmap.json").read_text()) if (self.path / "idmap.json").exists() else {}
        self.levels: dict[str, str] = json.loads((self.path / "levels.json").read_text()) if (self.path / "levels.json").exists() else {}
        self.summary: dict[str, Any] = json.loads((self.path / "summary.json").read_text()) if (self.path / "summary.json").exists() else {}
        self.units: list[dict[str, Any]] = []
        self.facts: list[dict[str, Any]] = []
        self.contract_errors = 0
        self.contract_retries = 0
        self.turn_errors: list[dict[str, Any]] = []
        self.write_errors: list[dict[str, Any]] = []          # lượt model trả đúng nhưng pipeline ghi lỗi (cả lượt / từng đơn vị)
        self.normalization_notes = 0
        self.turns = 0
        self.unreadable = {"voice": 0, "image": 0, "file": 0}
        self._load()

    @property
    def signals(self) -> list[dict[str, Any]]:
        """Đơn vị có `kind` (chiếu thành tín hiệu). `kind = null` chỉ mang sự thật/ghi nhớ (§4F) nên không tính recall/precision/mức — nhưng vẫn kiểm bằng chứng."""
        return [u for u in self.units if u["kind"] is not None]

    def mid(self, x: str) -> str:
        x = str(x)
        if x in self.idmap:
            return self.idmap[x]
        return x[2:] if x.startswith("m_") else x

    def _load(self) -> None:
        bc = brain_contract()
        by_id = self.ds.by_id()
        f = self.path / "outputs.jsonl"
        if not f.exists():
            raise SystemExit(f"gkb_score: lần chạy {self.path} không có outputs.jsonl")
        for line in f.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            rec = json.loads(line)
            self.turns += 1
            if int(rec.get("attempts") or 1) > 1:
                self.contract_retries += 1
            raw = rec.get("output")
            if rec.get("write_error"):
                self.write_errors.append({"turn": rec.get("turn"), "error": rec["write_error"]})
            if rec.get("error") or raw is None:
                self.contract_errors += 1
                self.turn_errors.append({"turn": rec.get("turn"), "error": rec.get("error") or "no_output"})
                continue
            try:
                out = bc.BrainOut.model_validate_json(raw) if isinstance(raw, str) else bc.BrainOut.model_validate(raw)
            except Exception as e:  # noqa: BLE001 — pydantic.ValidationError hoặc JSON hỏng: đều là lỗi hợp đồng, phải đếm
                self.contract_errors += 1
                self.turn_errors.append({"turn": rec.get("turn"), "error": f"invalid:{type(e).__name__}"})
                continue
            for k in self.unreadable:
                self.unreadable[k] += getattr(out.unreadable, k)
            for u in out.units:
                key = f"{rec['turn']}:{u.unit_ref}"
                ids = {self.mid(q.message_id) for q in u.quotes}
                verified = all(self._verified(q, by_id) for q in u.quotes)
                self.normalization_notes += len(u.normalization_notes)
                self.units.append({"key": key, "turn": rec["turn"], "thread": rec.get("thread"), "kind": u.kind, "ids": ids,
                                   "level": self.levels.get(key) or u.level_hint, "gate": u.gate_candidate, "verified": verified,
                                   "summary": u.summary, "flags": u.flags.model_dump(), "quotes": [q.model_dump() for q in u.quotes],
                                   "people": [p.model_dump() for p in u.people], "unit_ref": u.unit_ref})
            ukeys = {u.unit_ref: f"{rec['turn']}:{u.unit_ref}" for u in out.units}
            for fp in out.facts_proposed:
                ids = {self.mid(m) for m in fp.quote_refs}
                self.facts.append({"turn": rec["turn"], "predicate": fp.predicate, "ids": ids, "unit": ukeys.get(fp.unit_ref),
                                   "verified": ids <= set(by_id) and bool(ids)})

    def _verified(self, q: Any, by_id: dict[str, Any]) -> bool:
        m = by_id.get(self.mid(q.message_id))
        return m is not None and fold(q.text) in fold(m.text)


# ─── Ghép ─────────────────────────────────────────────────────────────────────────────────────────────────────────────────

def max_matching(edges: dict[int, list[int]]) -> dict[int, int]:
    """Ghép cực đại 1-1 (Kuhn). `edges[i]` = các chỉ số bên phải kề `i`. Trả {trái: phải}."""
    match_r: dict[int, int] = {}

    def try_(u: int, seen: set[int]) -> bool:
        for v in edges.get(u, []):
            if v in seen:
                continue
            seen.add(v)
            if v not in match_r or try_(match_r[v], seen):
                match_r[v] = u
                return True
        return False

    for u in edges:
        try_(u, set())
    return {u: v for v, u in match_r.items()}


def level_ok(label: str, level: str | None, old: str | None) -> bool:
    if label == "R":
        return level == "red"
    if label == "B":
        return level in ("red", "amber")
    if old is None:
        return level in ("amber", "grey", "off")        # N không có mức cũ: chỉ cần KHÔNG 🔴
    return level == ("amber" if old == "red" else old)   # N: mức cũ, riêng 🔴 cũ hạ thành 🟠 (KET-QUA-v4 §0)


def match_events(run: Run, key: dict[str, Any]) -> dict[str, Any]:
    events = key["events"]
    units = run.signals
    edges = {i: [j for j, u in enumerate(units) if u["kind"] == e["kind"] and set(e["message_ids"]) & u["ids"]] for i, e in enumerate(events)}
    tight = max_matching(edges)
    loose: dict[int, list[int]] = {}
    for i, e in enumerate(events):
        if i in tight:
            continue
        hit = [j for j, u in enumerate(units) if set(e["message_ids"]) & u["ids"]]
        if hit:
            loose[i] = hit
    return {"tight": tight, "loose": loose}


def event_units(e: dict[str, Any], run: Run) -> list[dict[str, Any]]:
    """Mọi đơn vị cùng sự việc với đáp án (chung ≥ 1 tin) — dùng cho recall/🔴 thừa như KET-QUA-v4."""
    return [u for u in run.signals if set(e["message_ids"]) & u["ids"]]


# ─── Chấm một lần chạy ────────────────────────────────────────────────────────────────────────────────────────────────

def score_run(run: Run, key: dict[str, Any], manual: dict[str, str] | None = None, *, unconfirmed: bool = False) -> dict[str, Any]:
    events, units = key["events"], run.signals
    m = match_events(run, key)
    tight, loose = m["tight"], m["loose"]
    n = len(events)
    covered = set(tight) | set(loose)
    # recall theo sự việc
    r_event = wilson(len(covered), n)
    r_tight = wilson(len(tight), n)
    # 🔴
    R = [i for i, e in enumerate(events) if e["label"] == "R"]
    B = [i for i, e in enumerate(events) if e["label"] == "B"]
    red_seen = [i for i in R if any(u["level"] == "red" for u in event_units(events[i], run))]
    # cổng nghiêm: lừa đảo đang diễn ra + an toàn — 100%, kể cả ca OTP
    strict = [i for i in R if events[i].get("gate") in RED_GATES_STRICT]
    strict_ok = [i for i in strict if i in red_seen]
    otp = [i for i in R if events[i].get("credentials_entered")]
    otp_down = [i for i in otp if i not in red_seen]
    other_gates = [i for i in R if events[i].get("gate") not in RED_GATES_STRICT]
    other_ok = [i for i in other_gates if i in red_seen]
    per_gate_edge: dict[str, Any] = {}
    for g in sorted({events[i].get("gate") for i in other_gates if events[i].get("gate")}):
        idx = [i for i in other_gates if events[i].get("gate") == g and events[i].get("set") == "edge"]
        per_gate_edge[g] = wilson(sum(1 for i in idx if i in red_seen), len(idx))
    # 🔴 thừa: đơn vị 🔴 không cùng sự việc với bất kỳ R/B nào
    keyed_ids = [set(events[i]["message_ids"]) for i in R + B]
    red_units = [u for u in units if u["level"] == "red"]
    excess = [u for u in red_units if not any(k & u["ids"] for k in keyed_ids)]
    near = [i for i, e in enumerate(events) if e.get("near_gate")]
    near_false_red = [i for i in near if any(u["level"] == "red" for u in event_units(events[i], run))]
    # khớp mức trên cặp ghép chặt
    lv = [level_ok(events[i]["label"], units[j]["level"], events[i].get("old_level")) for i, j in tight.items()]
    # precision: đơn vị ghép (chặt hoặc lệch loại hoặc chung tin) + rà tay các mục không ghép
    linked_units = {j for j in tight.values()} | {j for hits in loose.values() for j in hits}
    allmsg = [set(e["message_ids"]) for e in events]
    unmatched = [u for j, u in enumerate(units) if j not in linked_units and not any(a & u["ids"] for a in allmsg)]
    manual = manual or {}
    reviewed_ok = [u for u in unmatched if manual.get(u["key"]) == "ok"]
    reviewed_bad = [u for u in unmatched if manual.get(u["key"]) == "bad"]
    pending = [u for u in unmatched if u["key"] not in manual]
    prec_auto = wilson(len(units) - len(unmatched), len(units))
    # Mục CHƯA rà tính là SAI cho tới khi rà xong (không được bỏ khỏi mẫu số — precision giả 100%).
    prec_manual = wilson(len(units) - len(unmatched) + len(reviewed_ok), len(units))
    # bẫy
    hard_msgs = [set(t["message_ids"]) for t in key.get("traps", {}).get("hard", [])]
    priv_msgs = [set(t["message_ids"]) for t in key.get("traps", {}).get("privacy", [])]
    hard_hits = [u["key"] for u in units if u["kind"] and any(t & u["ids"] for t in hard_msgs)]
    priv_hits = [u["key"] for u in units if u["kind"] and any(t & u["ids"] for t in priv_msgs)]
    no_opp = set(key.get("traps", {}).get("no_opportunity_groups", []))
    opp_family = [u["key"] for u in units if u["kind"] == "co_hoi_ket_noi" and (u["thread"] in no_opp)]
    opp_outside_gate = [u["key"] for u in units if u["kind"] == "co_hoi_ket_noi" and u["gate"] != "co_hoi_lon_sap_troi"]
    eval_people = [u["key"] for u in units if PEOPLE_EVAL_RE.search(u["summary"] or "")]
    # bằng chứng M8
    unv_units = [u["key"] for u in run.units if not u["verified"]]
    unv_facts = [f for f in run.facts if not f["verified"]]
    ev_total = len(run.units) + len(run.facts)
    ev_ok = ev_total - len(unv_units) - len(unv_facts)
    # sự thật
    kfacts = key.get("facts", [])
    f_hit = [f for f in kfacts if any(f.get("predicate") == p["predicate"] and set(f["message_ids"]) & p["ids"] for p in run.facts)]
    out = {
        "run": run.label, "turns": run.turns, "units": len(units), "carrier_units": len(run.units) - len(units), "facts_proposed": len(run.facts),
        "recall_event": r_event, "recall_event_tight": r_tight, "recall_red": wilson(len(red_seen), len(R)),
        "red_strict_gates": wilson(len(strict_ok), len(strict)),
        "otp_downgraded": [events[i]["id"] for i in otp_down], "otp_cases": len(otp),
        "red_other_gates": wilson(len(other_ok), len(other_gates)), "red_edge_per_gate": per_gate_edge,
        "red_excess": len(excess), "red_excess_units": [u["key"] for u in excess], "red_units": len(red_units),
        "near_red_false_red": [events[i]["id"] for i in near_false_red],
        "level_match": wilson(sum(lv), len(lv)),
        "precision_auto": prec_auto, "precision_manual": prec_manual,
        "unmatched_units": [u["key"] for u in unmatched], "manual_pending": [u["key"] for u in pending], "manual_bad": [u["key"] for u in reviewed_bad],
        "hard_trap_hits": hard_hits, "privacy_hits": priv_hits, "opportunity_family_hits": opp_family,
        "opportunity_outside_gate": opp_outside_gate if unconfirmed else [], "people_eval_suspect": eval_people if unconfirmed else [],
        "evidence": wilson(ev_ok, ev_total), "contract_errors": run.contract_errors, "write_errors": len(run.write_errors), "contract_retries": run.contract_retries,
        "normalization_notes": run.normalization_notes, "facts_recall": wilson(len(f_hit), len(kfacts)),
        "unreadable": run.unreadable,
        "event_status": {events[i]["id"]: ("tight" if i in tight else ("kind_mismatch" if i in loose else "miss")) for i in range(n)},
        "event_level": {events[i]["id"]: _best_level(event_units(events[i], run)) for i in range(n)},
        "missed_events": [events[i]["id"] for i in range(n) if i not in covered],
    }
    return out


def _best_level(us: list[dict[str, Any]]) -> str:
    if not us:
        return "miss"
    lv = [u["level"] or "none" for u in us]
    for x in LEVELS:
        if x in lv:
            return x
    return "none"


# ─── Nhiều lần chạy: ổn định, tổng hợp, cổng ───────────────────────────────────────────────────────────────────────────

def pairwise_jaccard(sets: list[set[str]]) -> float | None:
    vals = []
    for i in range(len(sets)):
        for j in range(i + 1, len(sets)):
            u = sets[i] | sets[j]
            vals.append(len(sets[i] & sets[j]) / len(u) if u else 1.0)
    return sum(vals) / len(vals) if vals else None


def stability(scores: list[dict[str, Any]], key: dict[str, Any]) -> dict[str, Any]:
    ids = [e["id"] for e in key["events"]]
    flips = [i for i in ids if len({s["event_level"][i] for s in scores}) > 1]
    found = [{i for i, st in s["event_status"].items() if st != "miss"} for s in scores]
    return {"flip_rate": (len(flips) / len(ids)) if ids else 0.0, "flipped_events": flips, "jaccard_events": pairwise_jaccard(found), "runs": len(scores)}


def _p(x: dict[str, Any] | None) -> float | None:
    return None if x is None else x["p"]


def aggregate(scores: list[dict[str, Any]]) -> dict[str, Any]:
    def agg(f: Any) -> dict[str, Any]:
        xs = [v for v in (f(s) for s in scores) if v is not None]
        q = quantiles([float(v) for v in xs])
        return {"mean": q["mean"], "min": q["min"], "max": q["max"], "n": q["n"]}

    return {"recall_event": agg(lambda s: _p(s["recall_event"])), "recall_red": agg(lambda s: _p(s["recall_red"])),
            "red_strict": agg(lambda s: _p(s["red_strict_gates"])), "red_other": agg(lambda s: _p(s["red_other_gates"])),
            "red_excess": agg(lambda s: s["red_excess"]), "precision": agg(lambda s: _p(s["precision_manual"]) if _p(s["precision_manual"]) is not None else _p(s["precision_auto"])),
            "evidence": agg(lambda s: _p(s["evidence"])), "contract_errors": agg(lambda s: s["contract_errors"]),
            "level_match": agg(lambda s: _p(s["level_match"]))}


def gate(scores: list[dict[str, Any]], stab: dict[str, Any], key: dict[str, Any], *, unconfirmed_score: dict[str, Any] | None = None,
         dist_checks: dict[str, Any] | None = None, extraction_only: bool = False) -> dict[str, Any]:
    """Bảng "Ngưỡng G-KB" (spec §2.1). `extraction_only` = lần (1) cuối Đợt 1 (CNS-076): chỉ các dòng không cần mức."""
    ag = aggregate(scores)
    rows: dict[str, dict[str, Any]] = {}

    def row(name: str, ok: bool, detail: Any) -> None:
        rows[name] = {"pass": bool(ok), "detail": detail}

    re_ = [_p(s["recall_event"]) or 0 for s in scores]
    row("recall_event", (ag["recall_event"]["mean"] or 0) >= 0.90 and min(re_) >= 0.85, {"mean": ag["recall_event"]["mean"], "min": min(re_)})
    row("evidence_100", all(s["evidence"]["k"] == s["evidence"]["n"] for s in scores), [s["evidence"] for s in scores])
    row("stability", stab["flip_rate"] <= 0.10 and (stab["jaccard_events"] is None or stab["jaccard_events"] >= 0.88), stab)
    row("contract_errors_0_after_retry", all(s["contract_errors"] == 0 for s in scores), [s["contract_errors"] for s in scores])
    row("runs_ge_3", len(scores) >= 3, len(scores))
    if not extraction_only:
        row("recall_red_100", all(s["recall_red"]["k"] == s["recall_red"]["n"] for s in scores), [s["recall_red"] for s in scores])
        row("red_strict_100_no_otp_downgrade", all(s["red_strict_gates"]["k"] == s["red_strict_gates"]["n"] and not s["otp_downgraded"] for s in scores),
            [{"strict": s["red_strict_gates"], "otp_downgraded": s["otp_downgraded"]} for s in scores])
        pooled_k = sum(s["red_other_gates"]["k"] for s in scores)
        pooled_n = sum(s["red_other_gates"]["n"] for s in scores)
        row("red_other_gates_ge_85_pooled", pooled_n == 0 or pooled_k / pooled_n >= 0.85, wilson(pooled_k, pooled_n))
        edge_bad: dict[str, Any] = {}
        gates = {g for s in scores for g in s["red_edge_per_gate"]}
        for g in sorted(gates):
            k = sum(s["red_edge_per_gate"].get(g, {}).get("k", 0) for s in scores)
            nn = sum(s["red_edge_per_gate"].get(g, {}).get("n", 0) for s in scores)
            if nn and k / nn < 0.80:
                edge_bad[g] = wilson(k, nn)
        row("red_edge_per_gate_ge_80", not edge_bad, edge_bad or "ok")
        row("red_excess_le_1_per_run", all(s["red_excess"] <= 1 for s in scores), [s["red_excess"] for s in scores])
        pend = sum(len(s["manual_pending"]) for s in scores)
        row("precision_ge_95", pend == 0 and all((_p(s["precision_manual"]) or 0) >= 0.95 for s in scores),
            {"status": "chưa chốt: còn mục chưa rà tay (tính là sai)" if pend else "đã chốt", "pending": pend,
             "runs": [{"auto": _p(s["precision_auto"]), "manual": _p(s["precision_manual"]), "pending": len(s["manual_pending"])} for s in scores]})
        row("traps_zero", all(not s["hard_trap_hits"] and not s["privacy_hits"] and not s["opportunity_family_hits"] for s in scores),
            [{"hard": s["hard_trap_hits"], "privacy": s["privacy_hits"], "opp_family": s["opportunity_family_hits"]} for s in scores])
        row("near_red_not_red", all(not s["near_red_false_red"] for s in scores), [s["near_red_false_red"] for s in scores])
        if unconfirmed_score is None:       # thiếu lần chạy "bối cảnh chưa xác nhận" (CNS-072/105) = trượt, không được bỏ qua
            row("unconfirmed_zero_opportunity_and_people_eval", False, "thiếu lần chạy bối cảnh chưa xác nhận (--unconfirmed)")
        else:
            u = unconfirmed_score
            row("unconfirmed_zero_opportunity_and_people_eval", not u["opportunity_outside_gate"] and not u["opportunity_family_hits"] and not u["people_eval_suspect"],
                {"opp_outside_gate": u["opportunity_outside_gate"], "people_eval_suspect": u["people_eval_suspect"]})
    if not extraction_only:
        if dist_checks is None:             # không có summary.json phân phối = không chứng minh được cỡ lượt (CNS-101) = trượt
            row("distribution_cns101", False, "thiếu summary.json phân phối của các lần chạy")
        else:
            row("distribution_cns101", dist_checks.get("ok", False), dist_checks)
    elif dist_checks is not None:
        row("distribution_cns101", dist_checks.get("ok", False), dist_checks)
    manual_pending = sum(len(s["manual_pending"]) for s in scores)
    return {"pass": all(r["pass"] for r in rows.values()), "rows": rows, "aggregate": ag, "manual_pending_total": manual_pending,
            "note": "precision chỉ chốt khi manual_pending_total = 0 (rà tay 100% mục không ghép)" if manual_pending else None}


def regression(baseline: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    """M10: không thụt quá độ lệch giữa các lần của mốc trước: trung bình mới ≥ trung bình cũ − (max − min của mốc cũ) cho các chỉ số 'cao tốt'."""
    out: dict[str, Any] = {}
    for name in ("recall_event", "recall_red", "red_strict", "red_other", "precision", "evidence", "level_match"):
        b, c = baseline["aggregate"].get(name), current["aggregate"].get(name)
        if not b or not c or b["mean"] is None or c["mean"] is None:
            continue
        spread = (b["max"] - b["min"]) if b["max"] is not None else 0.0
        out[name] = {"baseline_mean": b["mean"], "mean": c["mean"], "tolerance": spread, "ok": c["mean"] >= b["mean"] - spread - 1e-9}
    return {"ok": all(v["ok"] for v in out.values()), "metrics": out}


def score_all(ds: Dataset, key: dict[str, Any], run_dirs: list[str], *, unconfirmed_dir: str | None = None, manual: dict[str, str] | None = None,
              extraction_only: bool = False) -> dict[str, Any]:
    scores = [score_run(Run(p, ds), key, manual) for p in run_dirs]
    stab = stability(scores, key)
    u = score_run(Run(unconfirmed_dir, ds), key, manual, unconfirmed=True) if unconfirmed_dir else None
    dist = [json.loads((Path(p) / "summary.json").read_text()).get("distribution_check") if (Path(p) / "summary.json").exists() else None for p in run_dirs]
    dist_ok = None
    if dist and all(d is not None for d in dist):
        dist_ok = {"ok": all(d["ok"] for d in dist), "runs": dist}
    g = gate(scores, stab, key, unconfirmed_score=u, dist_checks=dist_ok, extraction_only=extraction_only)
    return {"dataset": ds.name, "scores": scores, "unconfirmed": u, "stability": stab, "gate": g}


def main() -> None:
    ap = argparse.ArgumentParser(description="Chấm G-KB (xem docstring). Đáp án qua --key hoặc GH_EVAL_KEY, nằm ngoài repo.")
    ap.add_argument("--data", required=True)
    ap.add_argument("--key")
    ap.add_argument("--runs", nargs="+", required=True)
    ap.add_argument("--unconfirmed")
    ap.add_argument("--manual", help="JSON {khoá đơn vị 'turn:unit_ref': 'ok'|'bad'} từ phiếu rà tay")
    ap.add_argument("--baseline", help="gate.json của mốc trước (M10)")
    ap.add_argument("--extraction-only", action="store_true", help="lần (1) cuối Đợt 1: chỉ dòng không cần mức")
    ap.add_argument("--out")
    a = ap.parse_args()
    if a.out:
        refuse_inside_repo(a.out)
    ds = load_dataset(a.data)
    key = load_key(a.key)
    manual = json.loads(Path(a.manual).read_text()) if a.manual else None
    res = score_all(ds, key, a.runs, unconfirmed_dir=a.unconfirmed, manual=manual, extraction_only=a.extraction_only)
    if a.baseline:
        res["regression"] = regression(json.loads(Path(a.baseline).read_text())["gate"], res["gate"])
    if a.out:
        write_json(a.out, res)
    g = res["gate"]
    print(json.dumps({"pass": g["pass"], "rows": {k: v["pass"] for k, v in g["rows"].items()}, "manual_pending_total": g["manual_pending_total"]},
                     ensure_ascii=False, indent=2))
    sys.exit(0 if g["pass"] else 1)


if __name__ == "__main__":
    main()
