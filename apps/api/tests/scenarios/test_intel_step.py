# ruff: noqa: E501
"""Bộ chạy G-KB TỪNG BƯỚC (`scripts/intel-eval/step.py`): vòng `init → next → submit → … → DONE → export` chạy PIPELINE THẬT (replay + ingest +
GroupTurns + extract + verify) với một "agent giả" thay chỗ model: câu trả lời cố định hợp lệ; một lượt cố ý trả chuỗi hỏng để chứng minh đường
thử lại MỘT lần kèm lỗi (lượt `_c2`). Tiến trình nền dùng DB `gh_step_*` + Redis DB riêng (mặc định 9, đổi bằng GH_STEP_TEST_REDIS)."""

import json
import os
import re
import signal
import subprocess
import sys
from pathlib import Path
from typing import Any

import psycopg

from tests.scenarios import intel_helpers as h

STEP = h.REPO / "scripts" / "intel-eval" / "step.py"
PG = os.environ.get("GH_TEST_PG", "postgresql://postgres:postgres@localhost:5432")
REDIS = os.environ.get("GH_STEP_TEST_REDIS", "redis://localhost:6379/9")
PHONE = "0901234567"


def step(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    r = subprocess.run([sys.executable, str(STEP), *args], capture_output=True, text=True, timeout=300, check=False)
    if check and r.returncode:
        raise AssertionError(f"step {' '.join(args)} lỗi {r.returncode}:\n{r.stdout[-1500:]}\n{r.stderr[-3000:]}")
    return r


def cleanup(run: Path, cfg: dict[str, Any]) -> None:
    """Dừng tiến trình nền do CHÍNH test này khởi (nếu còn chờ agent) và xoá DB lần chạy."""
    try:
        pid = json.loads((run / "state.json").read_text()).get("pid")
        if pid and Path(f"/proc/{pid}/cmdline").exists() and b"_serve" in Path(f"/proc/{pid}/cmdline").read_bytes():
            os.kill(pid, signal.SIGTERM)
    except (OSError, ValueError):
        pass
    if cfg.get("db", "").startswith("gh_step_"):
        with psycopg.connect(f"{PG}/postgres", autocommit=True) as c:
            c.execute(f'DROP DATABASE IF EXISTS "{cfg["db"]}" WITH (FORCE)')


def fake_agent(prompt: dict[str, Any], *, broken: bool) -> str:
    """Đóng vai model: đọc CHỈ prompt.json. Có tin chứa số điện thoại ⇒ 1 đơn vị (kind null) + 1 sự thật `contact_phone`; không có ⇒ không đơn vị."""
    if broken:
        return "không phải JSON {"
    user = prompt["messages"][0]["content"]
    tref = re.search(r"thread_ref = (\S+)", user).group(1)  # type: ignore[union-attr]
    new = user.split("tin mới của luồng", 1)[1]
    out: dict[str, Any] = {"contract_version": "brain-out/1", "thread_ref": tref, "units": [], "facts_proposed": []}
    for line in new.splitlines():
        if line.startswith("{") and PHONE in line:
            m = json.loads(line)
            out["units"].append({"unit_ref": "u1", "event_ref": "e1", "kind": None, "summary": "Chị Lan cho số điện thoại",
                                 "quotes": [{"message_id": m["id"], "field": "body", "text": f"số điện thoại chị Lan là {PHONE}"}]})
            out["facts_proposed"].append({"unit_ref": "u1", "subject": {"surface": m["from"], "person_ref": m["from_ref"]}, "predicate": "contact_phone",
                                          "object": {"value": PHONE}, "quote_refs": [m["id"]]})
            break
    return json.dumps(out, ensure_ascii=False)


def test_step_init_next_submit_export_runs_real_pipeline_and_writes_facts(tmp_path: Path) -> None:
    ds = h.synth_dataset(40, 20)
    msgs = list(ds.msgs)
    i = next(k for k, m in enumerate(msgs) if m.group == "G1" and m.kind == "text" and "tin thường" in m.text and k > 5)
    msgs[i] = h.common.Msg(**{**msgs[i].__dict__, "text": f"Anh ơi số điện thoại chị Lan là {PHONE} nhé"})
    ds.msgs = msgs
    data = h.write_ds(ds, tmp_path / "kbx")
    run = tmp_path / "run"
    cfg: dict[str, Any] = {}
    try:
        out = json.loads(step("init", "--dataset", str(data), "--out", str(run), "--pg", PG, "--redis-url", REDIS, "--force").stdout)
        cfg = json.loads((run / "config.json").read_text())
        assert out["status"] == "waiting" and out["turns_planned"] >= 2 and out["pending"] == "turn_001"
        broken_first = True
        done = 0
        for _ in range(60):
            nxt = step("next", "--out", str(run)).stdout.strip()
            if nxt == "DONE":
                break
            prompt_path = Path(nxt)
            prompt = json.loads(prompt_path.read_text())
            assert prompt["format"] == "gkb-step/1" and prompt["system"].startswith("<!-- extractor_version") and prompt["messages"][-1]["role"] == "user"
            user = prompt["messages"][0]["content"]
            now = h.datetime.fromisoformat(re.search(r"Bây giờ[^:]*: (\S+)", user).group(1))  # type: ignore[union-attr]
            ats = [h.datetime.fromisoformat(json.loads(x)["at"]) for x in user.split("tin mới của luồng", 1)[1].splitlines()
                   if x.startswith('{"id"')]
            assert ats and now >= max(ats) and now - max(ats) < h.timedelta(days=1)       # "bây giờ" = giờ của lượt theo dữ liệu (lỗi kb1 #2)
            if prompt["kind"] == "extract" and broken_first:           # lượt đầu: trả chuỗi hỏng → pipeline thật phải gọi lại MỘT lần kèm lỗi
                (prompt_path.parent / "answer.json").write_text(fake_agent(prompt, broken=True), encoding="utf-8")
                step("submit", str(prompt_path.parent / "answer.json"))
                broken_first = False
                nxt2 = step("next", "--out", str(run)).stdout.strip()
                retry = json.loads(Path(nxt2).read_text())
                assert retry["kind"] == "retry" and Path(nxt2).parent.name.endswith("_c2") and len(retry["messages"]) == 3
                assert "không phải JSON hợp lệ" in retry["messages"][-1]["content"]
                prompt_path, prompt = Path(nxt2), retry
            (prompt_path.parent / "answer.json").write_text(fake_agent(prompt, broken=False), encoding="utf-8")
            r = step("submit", str(prompt_path.parent / "answer.json"))
            done += 1
            assert r.stdout.strip().splitlines()[-1].startswith(("tiếp:", "DONE"))
        else:
            raise AssertionError("không tới DONE sau 60 vòng")
        assert step("next", "--out", str(run)).stdout.strip() == "DONE" and done >= 2
        # đầu ra theo khuôn replay/gkb_score: outputs.jsonl có lượt thử lại (attempts = 2), lượt có đơn vị mang sự thật
        recs = [json.loads(x) for x in (run / "outputs.jsonl").read_text().splitlines()]
        assert recs[0]["attempts"] == 2 and recs[0]["error"] is None
        assert sum(len(r["output"]["units"]) for r in recs if r["output"]) == 1
        sig = tmp_path / "signals.json"
        step("export", "--out", str(sig), "--run", str(run))
        s = json.loads(sig.read_text())
        assert s["format"] == "gkb-step-signals/2" and s["contract_errors"] == 0 and len(s["units"]) == 1 and len(s["facts"]) == 1
        # Đợt 2: job đồng hồ production chạy theo giờ dữ liệu, export có biz.alerts + bản tin sáng (owner) sau `now` cuối
        assert s["clock"]["ticks"] > 0 and s["clock"]["job_runs"]["intel_reeval"] > 0 and s["clock"]["job_runs"]["intel_generic"] > 0
        assert s["clock"]["job_errors"] == {} and "market_signal_capture" in s["clock"]["hooks"]
        assert isinstance(s["alerts"], list) and isinstance(s["pushed"], list) and s["alerts_at"].endswith("07:30:00+07:00")
        assert s["digest"]["audience"] == "owner" and s["digest"]["at"] == s["alerts_at"] and s["alerts_end_at"] < s["alerts_at"]
        assert s["facts"][0]["predicate"] == "contact_phone" and s["units"][0]["verified"] is True
        # facts THẬT được ghi bởi pipeline (verify.check → facts.assert_fact), không chỉ nằm trong đầu ra của model
        with psycopg.connect(f"{PG}/{cfg['db']}") as c:
            n = c.execute("SELECT count(*) FROM mem.facts WHERE predicate = 'contact_phone'").fetchone()[0]  # type: ignore[index]
            ok = c.execute("SELECT count(*) FROM refinery.analyses WHERE status = 'ok'").fetchone()[0]  # type: ignore[index]
            raw = c.execute("SELECT count(*) FROM raw.events").fetchone()[0]  # type: ignore[index]
            stuck = c.execute("SELECT count(*) FROM refinery.event_state WHERE state IN ('pending', 'processing')").fetchone()[0]  # type: ignore[index]
        assert n == 1 and ok == done and raw == len(ds.msgs)
        assert stuck == 0                                                  # tin chờ sau lượt kế hoạch cuối được xả (drain), không kẹt
        assert s["write_errors"] == [] and all(r["write_error"] is None for r in recs)
        assert s["db"]["facts_by_predicate"]["contact_phone"] == 1
        st = json.loads(step("stats", "--out", str(run)).stdout)
        assert st["by_kind"]["retry"] == 1 and st["calls"] == done + 1
        # từ chối ghi vào trong repo
        assert step("export", "--out", str(h.REPO / "signals-x.json"), "--run", str(run), check=False).returncode != 0
    finally:
        cleanup(run, cfg)


def test_step_submit_rejects_wrong_turn_and_empty_answer(tmp_path: Path) -> None:
    ds = h.synth_dataset(30, 15)
    data = h.write_ds(ds, tmp_path / "kbx")
    run = tmp_path / "run"
    cfg: dict[str, Any] = {}
    try:
        step("init", "--dataset", str(data), "--out", str(run), "--pg", PG, "--redis-url", REDIS, "--force")
        cfg = json.loads((run / "config.json").read_text())
        pending = Path(step("next", "--out", str(run)).stdout.strip()).parent
        assert step("submit", str(pending / "answer.json"), check=False).returncode != 0           # chưa có answer.json
        wrong = run / "turn_099"
        wrong.mkdir()
        (wrong / "answer.json").write_text("{}")
        r = step("submit", str(wrong / "answer.json"), check=False)
        assert r.returncode != 0 and "lượt đang chờ" in (r.stdout + r.stderr)
    finally:
        cleanup(run, cfg)


def test_est_tokens_is_chars_over_three() -> None:
    import step as stepmod

    assert stepmod.est_tokens(30_000) == 10_000 and stepmod.est_tokens(0) == 0


def test_write_error_reports_failed_writes() -> None:
    """Lỗi kb1 (1d): lượt model trả đúng nhưng ghi lỗi (cả lượt / từng đơn vị) ⇒ chuỗi mã lỗi; ghi trọn ⇒ None."""
    import step as stepmod

    assert stepmod.write_error({"write_errors": []}) is None
    assert stepmod.write_error({"write_errors": ["write_failed"]}) == "write_failed×1"
    assert stepmod.write_error({"write_errors": ["unit_write_failed", "unit_write_failed_safety", "unit_write_failed"]}) == \
        "unit_write_failed×2,unit_write_failed_safety×1"


def test_fire_times_follow_worker_cron_in_vn_time() -> None:
    """Lịch job đồng hồ = lịch arq của worker (giờ VN): mỗi phút, 5 phút lệch 3, giờ cố định; đầu khoảng mở, cuối khoảng đóng."""
    from zoneinfo import ZoneInfo

    import step as stepmod

    vn = ZoneInfo("Asia/Ho_Chi_Minh")
    t0 = h.datetime(2026, 10, 1, 8, 0, 30, tzinfo=vn)
    assert [x.minute for x in stepmod.fire_times({"minute": set(range(60))}, t0, t0 + h.timedelta(minutes=3), vn)] == [1, 2, 3]
    assert [x.minute for x in stepmod.fire_times({"minute": set(range(3, 60, 5))}, t0, t0 + h.timedelta(minutes=20), vn)] == [3, 8, 13, 18]
    utc = h.datetime(2026, 9, 30, 12, 0, tzinfo=h.timezone.utc)                      # = 19:00 giờ VN
    heat = stepmod.fire_times({"hour": {1, 7, 19}, "minute": {55}}, utc, utc + h.timedelta(days=2), vn)
    assert [(x.day, x.hour) for x in heat] == [(30, 19), (1, 1), (1, 7), (1, 19), (2, 1), (2, 7)]
    assert all(x.utcoffset() == h.timedelta(hours=7) for x in heat)


def test_next_morning_digest_is_first_0730_after_end() -> None:
    from zoneinfo import ZoneInfo

    import step as stepmod

    vn = ZoneInfo("Asia/Ho_Chi_Minh")
    d = h.datetime(2026, 10, 1, 6, 0, tzinfo=vn)
    assert stepmod.next_morning_digest(d, vn) == d.replace(hour=7, minute=30)
    assert stepmod.next_morning_digest(d.replace(hour=7, minute=30), vn) == d.replace(day=2, hour=7, minute=30)
    assert stepmod.next_morning_digest(h.datetime(2026, 10, 1, 2, 0, tzinfo=h.timezone.utc), vn) == d.replace(day=2, hour=7, minute=30)   # 09:00 VN


def test_clock_jobs_still_registered_in_production() -> None:
    """Harness lấy lịch từ registry worker (`gh.biz.jobs()`/`hooks()`): job/hook Đợt 2 đổi tên hay bị gỡ ⇒ đỏ ở đây, không âm thầm bỏ đo."""
    import step as stepmod

    from gh import biz

    names = {fn.__name__ for fn, _ in biz.jobs()}
    assert set(stepmod.CLOCK_JOBS) <= names
    assert set(stepmod.POST_TURN_HOOKS) <= {x.name for x in biz.hooks()}


def test_every_intel_job_is_run_or_explicitly_skipped() -> None:
    """Job `intel_*` mới của production (vd `intel_units`) mà harness không chạy ⇒ G-KB đo thiếu im lặng (bài học T12). Phải vào
    CLOCK_JOBS hoặc SKIPPED_INTEL_JOBS kèm lý do."""
    import step as stepmod

    from gh import biz

    intel = {fn.__name__ for fn, _ in biz.jobs() if fn.__name__.startswith("intel_")}
    assert intel - set(stepmod.CLOCK_JOBS) - set(stepmod.SKIPPED_INTEL_JOBS) == set()


def test_export_dot2_maps_raw_ids_and_splits_push_log(tmp_path: Path) -> None:
    import step as stepmod

    (tmp_path / "state.json").write_text(json.dumps({"status": "done", "clock": {"ticks": 3}}))
    a = {"id": "s1", "code": "ALR-0001", "kind": "rui_ro_lua_dao", "raw_event_ids": ["u1", "u9"]}
    (tmp_path / "alerts_final.json").write_text(json.dumps({"at": "2026-10-02T07:30:00+07:00", "alerts": [a], "first_seen": {"s1": "2026-10-01T09:00:00+07:00"}}))
    log = [{"event": "new", "id": "s1"}, {"event": "push", "id": "s1", "pushed_at": "x"}, {"event": "shadow_push", "id": "s1"}]
    (tmp_path / "alerts_log.jsonl").write_text("\n".join(json.dumps(x) for x in log) + "\n")
    d = stepmod.export_dot2(tmp_path, {"m_u1": "t0007"})
    assert d["alerts"][0]["raw_ids"] == ["t0007", "u9"] and "raw_event_ids" not in d["alerts"][0]
    assert d["alerts"][0]["first_seen_at"] == "2026-10-01T09:00:00+07:00" and d["clock"] == {"ticks": 3}
    assert [e["event"] for e in d["pushed"]] == ["push"] and len(d["shadow_pushed"]) == 1 and len(d["alert_events"]) == 1
    assert d["alerts_end"] == [] and d["digest"] is None                    # lần chạy thiếu tệp ⇒ rỗng, không lỗi
