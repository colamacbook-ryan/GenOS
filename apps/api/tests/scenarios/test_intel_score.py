# ruff: noqa: E501
"""Bộ chấm G-KB (L6) trên đáp án GIẢ tí hon tự tạo + điểm cắm "nhà tiên tri": chạy replay thật (đồng hồ giả, chế độ nén) rồi chấm. Chứng minh: cổng đỗ khi
đầu ra đúng; trượt đúng dòng khi bẻ hỏng (ca OTP hạ 🟠, 🔴 thừa, bẫy, trích dẫn bịa, lỗi hợp đồng, nhảy mức giữa các lần, phân phối); đáp án không vào repo."""

import json
from pathlib import Path
from typing import Any

import common
import gkb_score as gs
import pytest
import replay

from tests.scenarios import intel_helpers as h


@pytest.fixture
def world(tmp_path: Path) -> dict[str, Any]:
    ds = h.synth_dataset()
    key = h.tiny_key(ds)
    kp = tmp_path / "key.json"
    kp.write_text(json.dumps(key, ensure_ascii=False))
    return {"ds": ds, "key": key, "key_path": kp, "tmp": tmp_path}


async def do_runs(w: dict[str, Any], analyzers: list[Any], *, confirmed: list[bool] | None = None) -> list[str]:
    outs = []
    for i, a in enumerate(analyzers, 1):
        d = w["tmp"] / f"r{i}"
        await replay.run_replay(w["ds"], d, analyzer=a, confirmed=True if confirmed is None else confirmed[i - 1], label=f"r{i}")
        outs.append(str(d))
    return outs


def full_score(w: dict[str, Any], runs: list[str], **kw: Any) -> dict[str, Any]:
    return gs.score_all(w["ds"], w["key"], runs, **kw)


def oracle_n(w: dict[str, Any], n: int = 3, **kw: Any) -> list[h.Oracle]:
    return [h.Oracle(w["key"], **kw) for _ in range(n)]


async def test_perfect_oracle_passes_every_gate_row(world) -> None:  # type: ignore[no-untyped-def]
    runs = await do_runs(world, oracle_n(world))
    u = world["tmp"] / "u1"

    def no_opportunity(e: dict[str, Any], u_: dict[str, Any]) -> dict[str, Any]:       # đúng spec: nhóm chưa xác nhận không có `co_hoi_ket_noi` ngoài cổng
        if e["kind"] == "co_hoi_ket_noi":
            u_["kind"] = None
        return u_

    await replay.run_replay(world["ds"], u, analyzer=h.Oracle(world["key"], tweak=no_opportunity), confirmed=False, label="u1")
    res = full_score(world, runs, unconfirmed_dir=str(u), manual={})
    g = res["gate"]
    assert g["pass"], json.dumps({k: v for k, v in g["rows"].items() if not v["pass"]}, ensure_ascii=False, indent=1, default=str)
    s0 = res["scores"][0]
    assert s0["recall_event"]["p"] == 1 and s0["recall_event_tight"]["p"] == 1 and s0["recall_red"]["p"] == 1
    assert s0["red_strict_gates"]["n"] == 2 and s0["red_other_gates"]["n"] == 1 and s0["level_match"]["p"] == 1
    assert s0["facts_recall"]["p"] == 1 and s0["red_excess"] == 0 and s0["hard_trap_hits"] == []
    assert res["stability"]["flip_rate"] == 0 and res["stability"]["jaccard_events"] == 1


async def test_hard_trap_hit_flagged(world) -> None:  # type: ignore[no-untyped-def]
    # nhà tiên tri phát thêm đơn vị trên tin bẫy cứng (cũng là tin của sự thật) với kind ≠ null ⇒ chạm bẫy
    def extra(inp: Any, n: int) -> list[dict[str, Any]]:
        hit = [m for m in inp.new if m["id"] == world["key"]["traps"]["hard"][0]["message_ids"][0]]
        return [{"unit_ref": f"x{n}", "event_ref": "t", "kind": "viec_han", "quotes": [{"message_id": hit[0]["message_id"], "field": "body", "text": hit[0]["text"][:80]}],
                 "summary": "bẫy", "level_hint": "amber"}] if hit else []

    runs = await do_runs(world, oracle_n(world, extra=extra))
    res = full_score(world, runs)
    assert all(s["hard_trap_hits"] for s in res["scores"]) and not res["gate"]["rows"]["traps_zero"]["pass"]


async def test_privacy_trap_flagged(world) -> None:  # type: ignore[no-untyped-def]
    pid = world["key"]["traps"]["privacy"][0]["message_ids"][0]

    def extra(inp: Any, n: int) -> list[dict[str, Any]]:
        hit = [m for m in inp.new if m["id"] == pid]
        return [{"unit_ref": f"p{n}", "event_ref": "p", "kind": "quan_he_nong_nguoi", "quotes": [{"message_id": hit[0]["message_id"], "field": "body", "text": hit[0]["text"][:80]}],
                 "summary": "nhận xét tính cách chị Hai", "level_hint": "amber"}] if hit else []

    res = full_score(world, await do_runs(world, oracle_n(world, extra=extra)))
    assert res["scores"][0]["privacy_hits"] and not res["gate"]["rows"]["traps_zero"]["pass"]


async def test_otp_case_downgraded_to_amber_fails_the_hard_row(world) -> None:  # type: ignore[no-untyped-def]
    def tweak(e: dict[str, Any], u: dict[str, Any]) -> dict[str, Any]:
        if e["id"] == "E1":
            u["level_hint"] = "amber"         # ca đã nhập OTP bị hạ 🟠 = trượt cổng (spec §2.1)
        return u

    res = full_score(world, await do_runs(world, oracle_n(world, tweak=tweak)))
    rows = res["gate"]["rows"]
    assert not rows["red_strict_100_no_otp_downgrade"]["pass"] and not rows["recall_red_100"]["pass"]
    assert res["scores"][0]["otp_downgraded"] == ["E1"] and not res["gate"]["pass"]


async def test_red_excess_over_one_per_run_fails(world) -> None:  # type: ignore[no-untyped-def]
    unkeyed = [m for m in world["ds"].msgs if m.group == "G1" and m.text.startswith("tin thường")]

    def extra(inp: Any, n: int) -> list[dict[str, Any]]:
        outs = []
        for m in inp.new:
            if m["id"] in {unkeyed[3].id, unkeyed[10].id}:
                outs.append({"unit_ref": f"z{n + len(outs)}", "event_ref": "z", "kind": "viec_han",
                             "quotes": [{"message_id": m["message_id"], "field": "body", "text": m["text"][:80]}], "summary": "nhiễu", "level_hint": "red"})
        return outs

    res = full_score(world, await do_runs(world, oracle_n(world, extra=extra)))
    assert res["scores"][0]["red_excess"] == 2 and not res["gate"]["rows"]["red_excess_le_1_per_run"]["pass"]
    # đúng 1 🔴 thừa thì vẫn đỗ dòng này
    res1 = full_score(world, await do_runs(world, oracle_n(world, extra=lambda inp, n: [x for x in extra(inp, n) if unkeyed[3].id in x["quotes"][0]["message_id"]][:1])))
    assert res1["scores"][0]["red_excess"] == 1 and res1["gate"]["rows"]["red_excess_le_1_per_run"]["pass"]


async def test_near_red_case_made_red_is_flagged(world) -> None:  # type: ignore[no-untyped-def]
    def tweak(e: dict[str, Any], u: dict[str, Any]) -> dict[str, Any]:
        if e["id"] == "E6":
            u["level_hint"] = "red"
        return u

    res = full_score(world, await do_runs(world, oracle_n(world, tweak=tweak)))
    assert res["scores"][0]["near_red_false_red"] == ["E6"] and not res["gate"]["rows"]["near_red_not_red"]["pass"]


async def test_fabricated_quote_breaks_evidence_100(world) -> None:  # type: ignore[no-untyped-def]
    def tweak(e: dict[str, Any], u: dict[str, Any]) -> dict[str, Any]:
        if e["id"] == "E2":
            u["quotes"][0]["text"] = "câu này không hề có trong tin gốc"
        return u

    res = full_score(world, await do_runs(world, oracle_n(world, tweak=tweak)))
    assert res["scores"][0]["evidence"]["k"] < res["scores"][0]["evidence"]["n"] and not res["gate"]["rows"]["evidence_100"]["pass"]


async def test_contract_errors_counted_and_retries_reported(world) -> None:  # type: ignore[no-untyped-def]
    o = h.Oracle(world["key"], invalid_turns={1})
    runs = await do_runs(world, [o, h.Oracle(world["key"]), h.Oracle(world["key"])])
    res = full_score(world, runs)
    assert res["scores"][0]["contract_errors"] == 1 and res["scores"][1]["contract_errors"] == 0
    assert not res["gate"]["rows"]["contract_errors_0_after_retry"]["pass"]

    class Retrying(h.Oracle):
        async def analyze(self, inp: Any) -> Any:
            r = await super().analyze(inp)
            r.attempts = 2
            return r

    res2 = full_score(world, await do_runs(world, [Retrying(world["key"])] * 3))
    assert res2["scores"][0]["contract_retries"] == res2["scores"][0]["turns"] and res2["gate"]["rows"]["contract_errors_0_after_retry"]["pass"]


async def test_level_flip_between_runs_breaks_stability(world) -> None:  # type: ignore[no-untyped-def]
    def flip(e: dict[str, Any], u: dict[str, Any]) -> dict[str, Any]:
        if e["id"] == "E4":
            u["level_hint"] = "grey"
        return u

    res = full_score(world, await do_runs(world, [h.Oracle(world["key"]), h.Oracle(world["key"]), h.Oracle(world["key"], tweak=flip)]))
    assert res["stability"]["flipped_events"] == ["E4"] and res["stability"]["flip_rate"] == pytest.approx(1 / 6)
    assert not res["gate"]["rows"]["stability"]["pass"] and res["stability"]["jaccard_events"] == 1


async def test_missed_events_lower_recall_and_gate(world) -> None:  # type: ignore[no-untyped-def]
    def drop(e: dict[str, Any], u: dict[str, Any]) -> Any:
        if e["id"] in ("E1", "E3"):
            return None
        return u

    # tweak trả None ⇒ giữ u cũ; để bỏ hẳn sự việc: lọc bằng subclass
    class Dropper(h.Oracle):
        async def analyze(self, inp: Any) -> Any:
            r = await super().analyze(inp)
            r.output["units"] = [u for u in r.output["units"] if u["event_ref"] not in ("E1", "E3")]
            r.output["facts_proposed"] = [f for f in r.output["facts_proposed"] if f["unit_ref"] in {u["unit_ref"] for u in r.output["units"]}]
            return r

    res = full_score(world, await do_runs(world, [Dropper(world["key"]) for _ in range(3)]))
    s = res["scores"][0]
    assert sorted(s["missed_events"]) == ["E1", "E3"] and s["recall_event"]["p"] == pytest.approx(4 / 6)
    assert not res["gate"]["rows"]["recall_event"]["pass"] and not res["gate"]["rows"]["recall_red_100"]["pass"]


async def test_kind_mismatch_counts_for_event_recall_but_not_tight(world) -> None:  # type: ignore[no-untyped-def]
    def tweak(e: dict[str, Any], u: dict[str, Any]) -> dict[str, Any]:
        if e["id"] == "E4":
            u["kind"] = "cau_hoi_cho_xu_ly"
        return u

    s = full_score(world, await do_runs(world, oracle_n(world, 1, tweak=tweak)))["scores"][0]
    assert s["recall_event"]["p"] == 1 and s["recall_event_tight"]["p"] == pytest.approx(5 / 6) and s["event_status"]["E4"] == "kind_mismatch"


async def test_unconfirmed_run_zero_opportunity_outside_gate_and_people_eval(world) -> None:  # type: ignore[no-untyped-def]
    runs = await do_runs(world, oracle_n(world))
    u = world["tmp"] / "u1"
    await replay.run_replay(world["ds"], u, analyzer=h.Oracle(world["key"]), confirmed=False, label="u1")
    ok = full_score(world, runs, unconfirmed_dir=str(u))["gate"]["rows"]["unconfirmed_zero_opportunity_and_people_eval"]
    # E6 là co_hoi_ket_noi với gate=None ⇒ chính là "cơ hội ngoài cổng co_hoi_lon_sap_troi" ở lần chưa xác nhận
    assert not ok["pass"] and ok["detail"]["opp_outside_gate"]
    u2 = world["tmp"] / "u2"

    def clean(e: dict[str, Any], u_: dict[str, Any]) -> Any:
        if e["kind"] == "co_hoi_ket_noi":
            u_["kind"] = None
        return u_

    await replay.run_replay(world["ds"], u2, analyzer=h.Oracle(world["key"], tweak=clean), confirmed=False, label="u2")
    assert full_score(world, runs, unconfirmed_dir=str(u2))["gate"]["rows"]["unconfirmed_zero_opportunity_and_people_eval"]["pass"]

    def judge(e: dict[str, Any], u_: dict[str, Any]) -> dict[str, Any]:
        u_["summary"] = "đánh giá nhân viên: lười, thái độ kém"
        return u_

    u3 = world["tmp"] / "u3"
    await replay.run_replay(world["ds"], u3, analyzer=h.Oracle(world["key"], tweak=judge), confirmed=False, label="u3")
    assert full_score(world, runs, unconfirmed_dir=str(u3))["unconfirmed"]["people_eval_suspect"]


async def test_unconfirmed_run_does_not_seed_group_labels(world) -> None:  # type: ignore[no-untyped-def]
    seen: dict[bool, Any] = {}

    class Spy(h.Oracle):
        async def analyze(self, inp: Any) -> Any:
            seen[inp.contexts_confirmed] = inp.context_label
            return await super().analyze(inp)

    await replay.run_replay(world["ds"], world["tmp"] / "a", analyzer=Spy(world["key"]), confirmed=True)
    await replay.run_replay(world["ds"], world["tmp"] / "b", analyzer=Spy(world["key"]), confirmed=False)
    assert seen[True] in ("kinh_doanh", "gia_dinh") and seen[False] is None


async def test_precision_manual_review_blocks_until_all_unmatched_reviewed(world) -> None:  # type: ignore[no-untyped-def]
    unkeyed = next(m for m in world["ds"].msgs if m.group == "G1" and m.text.startswith("tin thường") and m.reply_to is None)

    def extra(inp: Any, n: int) -> list[dict[str, Any]]:
        return [{"unit_ref": f"q{n}", "event_ref": "q", "kind": "viec_han", "quotes": [{"message_id": m["message_id"], "field": "body", "text": m["text"][:80]}],
                 "summary": "mục lạ", "level_hint": "amber"} for m in inp.new if m["id"] == unkeyed.id]

    runs = await do_runs(world, oracle_n(world, extra=extra))
    r0 = gs.score_run(gs.Run(runs[0], world["ds"]), world["key"])
    assert len(r0["unmatched_units"]) == 1 and r0["manual_pending"] == r0["unmatched_units"]
    g = full_score(world, runs)["gate"]
    assert g["manual_pending_total"] == 3 and g["note"]
    k = r0["unmatched_units"][0]
    good = full_score(world, runs, manual={k: "ok"})["gate"]
    assert good["manual_pending_total"] == 0 and good["rows"]["precision_ge_95"]["pass"]
    bad = gs.score_run(gs.Run(runs[0], world["ds"]), world["key"], {k: "bad"})
    assert bad["manual_bad"] == [k] and bad["precision_manual"]["p"] < 1


async def test_edge_cases_per_gate_with_wilson(world) -> None:  # type: ignore[no-untyped-def]
    s = full_score(world, await do_runs(world, oracle_n(world, 1)))["scores"][0]
    assert s["red_edge_per_gate"]["loi_hua_khach_sap_vo"]["n"] == 1 and s["red_edge_per_gate"]["loi_hua_khach_sap_vo"]["lo95"] is not None
    w = common.wilson(10, 10)
    assert w["lo95"] == pytest.approx(0.722, abs=0.002) and w["p"] == 1.0
    assert common.wilson(0, 0)["p"] is None


async def test_extraction_only_gate_skips_level_rows(world) -> None:  # type: ignore[no-untyped-def]
    def tweak(e: dict[str, Any], u: dict[str, Any]) -> dict[str, Any]:
        u["level_hint"] = "grey"
        return u

    runs = await do_runs(world, oracle_n(world, tweak=tweak))
    full = full_score(world, runs)["gate"]
    ext = full_score(world, runs, extraction_only=True)["gate"]
    assert not full["pass"] and ext["pass"] and "recall_red_100" not in ext["rows"] and "recall_event" in ext["rows"]


async def test_regression_against_baseline_tolerance(world) -> None:  # type: ignore[no-untyped-def]
    good = full_score(world, await do_runs(world, oracle_n(world)))["gate"]

    class Dropper(h.Oracle):
        async def analyze(self, inp: Any) -> Any:
            r = await super().analyze(inp)
            r.output["units"] = [u for u in r.output["units"] if u["event_ref"] != "E4"]
            r.output["facts_proposed"] = [f for f in r.output["facts_proposed"] if f["unit_ref"] in {u["unit_ref"] for u in r.output["units"]}]
            return r

    worse = full_score(world, await do_runs(world, [Dropper(world["key"]) for _ in range(3)]))["gate"]
    assert gs.regression(good, good)["ok"] and not gs.regression(good, worse)["ok"]
    assert gs.regression(good, worse)["metrics"]["recall_event"]["ok"] is False


def test_key_must_come_from_param_or_env_and_stay_out_of_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GH_EVAL_KEY", raising=False)
    with pytest.raises(SystemExit, match="thiếu đáp án"):
        gs.load_key(None)
    with pytest.raises(SystemExit, match="không thấy"):
        gs.load_key(tmp_path / "none.json")
    k = tmp_path / "k.json"
    k.write_text(json.dumps({"format": "intel-key/1", "events": []}))
    monkeypatch.setenv("GH_EVAL_KEY", str(k))
    assert gs.load_key(None)["format"] == "intel-key/1"
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"format": "khac"}))
    with pytest.raises(SystemExit, match="khuôn đáp án lạ"):
        gs.load_key(bad)
    inside = h.REPO / "scripts" / "intel-eval" / "zz_key_test.json"
    inside.write_text(json.dumps({"format": "intel-key/1", "events": []}))
    try:
        with pytest.raises(SystemExit, match="TRONG repo"):
            gs.load_key(inside)
    finally:
        inside.unlink()


def test_no_answer_key_file_inside_repo_scripts() -> None:
    """Rào chống tái phạm: không tệp đáp án nào (intel-key/1, evalkey*) trong thư mục script của L6."""
    for sub in ("scripts/intel-eval", "scripts/import-export", "apps/api/tests/scenarios"):
        for p in (h.REPO / sub).rglob("*"):
            if p.is_file() and p.suffix in (".json", ".jsonl"):
                assert "intel-key/1" not in p.read_text(encoding="utf-8", errors="ignore"), p
            assert "evalkey" not in p.name
