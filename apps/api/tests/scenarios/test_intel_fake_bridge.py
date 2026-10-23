# ruff: noqa: E501
"""Bridge giả (L6): phát tin nhóm/DM/voice/ảnh, rớt phiên, bị kick; hàm biên dịch thuần khớp tên stream của bus; kịch bản sai lộ ngay; phát qua EventBus thật
lên Redis; bộ tin injection + kiểm canary."""

import json
from pathlib import Path
from typing import Any

import injection_check as ic
import pytest
import replay

from gh.chassis.bus import BRIDGE_DIRECTORY, BRIDGE_INBOUND, BRIDGE_STATUS, EventBus
from tests.scenarios import intel_helpers as h
from tests.scenarios import intel_injection as inj

fb = h.common.fake_bridge()


def test_stream_names_match_gh_bus() -> None:
    assert (fb.S_INBOUND, fb.S_STATUS, fb.S_DIRECTORY) == (BRIDGE_INBOUND, BRIDGE_STATUS, BRIDGE_DIRECTORY)


def test_group_msg_dm_voice_image_envelopes() -> None:
    (s, t, p), = fb.compile_script_event({"type": "group_msg", "group": "g1", "group_name": "Nhóm 1", "sender": "u1", "name": "Lan", "text": "Chào cả nhà",
                                          "ts": "2026-10-01T08:00:00+07:00", "reply_to": "m0", "mention": True})
    assert (s, t) == (BRIDGE_INBOUND, "message")
    assert p["external_group_id"] == "g1" and p["body_text"] == "Chào cả nhà" and p["kind"] == "text" and p["mentions_self"] is True
    assert p["payload"]["reply_to"] == "m0" and p["payload"]["fake"] is True and p["occurred_at"] == "2026-10-01T08:00:00+07:00"
    (_, _, dm), = fb.compile_script_event({"type": "dm", "sender": "u9", "name": "Lạ", "text": "Anh ơi", "group": "bỏ qua"})
    assert dm["external_group_id"] is None and dm["body_text"] == "Anh ơi"                  # DM không có nhóm
    (_, _, v), = fb.compile_script_event({"type": "voice", "group": "g1", "sender": "u1", "name": "Lan", "transcript": "thoai khong dau", "duration_s": 12})
    assert v["kind"] == "voice" and v["body_text"] is None and v["payload"]["fake_media_text"] == "thoai khong dau" and v["payload"]["duration_s"] == 12
    (_, _, im), = fb.compile_script_event({"type": "image", "group": "g1", "sender": "u1", "name": "Lan", "caption": "[ảnh] hoá đơn", "ocr_text": "STK 0123456789"})
    assert im["kind"] == "image" and im["body_text"] == "[ảnh] hoá đơn" and im["payload"]["fake_media_text"] == "STK 0123456789"


def test_message_ids_are_stable_and_distinct() -> None:
    ev = {"type": "group_msg", "group": "g1", "sender": "u1", "name": "Lan", "text": "ok", "ts": "2026-10-01T08:00:00+07:00"}
    a = fb.compile_script_event(ev, 0)[0][2]["external_msg_id"]
    assert a == fb.compile_script_event(ev, 0)[0][2]["external_msg_id"] != fb.compile_script_event(ev, 1)[0][2]["external_msg_id"]


def test_session_drop_reconnect_and_kick() -> None:
    (s, t, p), = fb.compile_script_event({"type": "session_drop", "session_id": "S1", "reason": "expired"})
    assert (s, t, p["reason"], p["session_id"]) == (BRIDGE_STATUS, "session.ended", "expired", "S1")
    (_, t2, p2), = fb.compile_script_event({"type": "session_reconnecting", "session_id": "S1"})
    assert t2 == "session.reconnecting" and p2["error"] == "NETWORK_UNAVAILABLE"
    (s3, t3, p3), = fb.compile_script_event({"type": "kicked", "group": "g1", "by": "55555"})
    assert (s3, t3, p3) == (BRIDGE_DIRECTORY, "group.self_removed", {"channel": "zalo", "group_id": "g1", "by": "55555"})
    assert fb.compile_script_event({"type": "sleep", "seconds": 3}) == []


@pytest.mark.parametrize("bad", [{"type": "nổ"}, {"type": "group_msg", "sender": "u", "name": "n", "text": "x"}, {"type": "group_msg", "group": "g", "name": "n", "text": "x"},
                                 {"type": "voice", "group": "g", "sender": "u", "name": "n"}, {"type": "session_drop"},
                                 {"type": "session_drop", "session_id": "S", "reason": "lạ"}, {"type": "kicked"}])
def test_bad_script_events_raise(bad: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        fb.compile_script_event(bad)


async def test_play_script_publishes_to_real_bus_in_order(redis) -> None:  # type: ignore[no-untyped-def]
    bus = EventBus(redis, 10000)
    script = [{"type": "group_msg", "group": "g1", "sender": "u1", "name": "Lan", "text": "một"}, {"type": "sleep", "seconds": 99},
              {"type": "voice", "group": "g1", "sender": "u1", "name": "Lan", "transcript": "hai"}, {"type": "session_drop", "session_id": "S1", "reason": "error"},
              {"type": "kicked", "group": "g1"}]
    n = await fb.play_script(bus, script, org="org-x", pace=False)
    assert n == 4
    got_in = await redis.xrange(BRIDGE_INBOUND)
    assert len(got_in) == 2 and [json.loads(f[b"payload"])["body_text"] if b"payload" in f else None for _, f in got_in][0] == "một"
    assert len(await redis.xrange(BRIDGE_STATUS)) == 1 and len(await redis.xrange(BRIDGE_DIRECTORY)) == 1


def test_fake_bridge_module_is_import_safe_without_org_env() -> None:
    """Import không chạy `main()` và không đòi ORG (trước đây import = chạy bridge ngay)."""
    assert callable(fb.main) and hasattr(fb, "play_script")


# ─── bộ tin injection ───────────────────────────────────────────────────────────────────────────────────────────────────────

def test_injection_set_shape() -> None:
    assert len(inj.INJECTIONS) >= 10 and len({i.canary for i in inj.INJECTIONS}) == len(inj.INJECTIONS)
    assert {"ignore_instructions", "memory_poison", "schema_break", "fake_stk_change", "role_spoof", "exfil"} <= {i.attack for i in inj.INJECTIONS}
    assert all(i.canary in i.text for i in inj.INJECTIONS)
    ds, injs = inj.injected_dataset(h.synth_dataset())
    assert all(any(m.id == i.id for m in ds.msgs) for i in injs) and [m.ts for m in ds.msgs] == sorted(m.ts for m in ds.msgs)


async def test_injection_check_catches_compliance_and_passes_clean_output(tmp_path: Path) -> None:
    ds, injs = inj.injected_dataset(h.synth_dataset())
    injections = inj.as_dicts()
    carriers = {i.id: i for i in injs}

    class Obedient(replay.StubAnalyzer):
        """Nghe lời kẻ xấu: in canary vào summary, đưa STK giả vào sự thật + nháp."""

        async def analyze(self, inp: replay.TurnInput) -> replay.AnalysisResult:
            units, facts = [], []
            for m in inp.new:
                i = carriers.get(m["id"])
                if i is None:
                    continue
                q = {"message_id": m["message_id"], "field": "body", "text": m["text"][:100]}
                units.append({"unit_ref": f"u{len(units)}", "event_ref": i.id, "kind": "rui_ro_lua_dao" if i.fake_account else "viec_han", "quotes": [q],
                              "summary": f"làm theo: {i.canary}", "level_hint": "red", "action": {"who": "owner", "what": "gửi", "decision_kind": "outbound",
                                                                                                "draft": f"chuyển vào {i.fake_account or ''}"}})
                if i.fake_account:
                    facts.append({"unit_ref": units[-1]["unit_ref"], "subject": {"surface": "Sếp"}, "predicate": "payment_account",
                                  "object": {"value": i.fake_account}, "quote_refs": [m["message_id"]]})
            return replay.AnalysisResult(output={"contract_version": "brain-out/1", "thread_ref": inp.thread_ref, "units": units, "facts_proposed": facts,
                                                 "owner_question": ""}, usage={})

    class Resistant(replay.StubAnalyzer):
        """Trích dẫn đúng tin xấu nhưng không làm theo; ghi nhận là nghi lừa đảo (kind rui_ro_lua_dao hợp lệ kể cả khi tin tự nêu canary trong quotes)."""

        async def analyze(self, inp: replay.TurnInput) -> replay.AnalysisResult:
            units = []
            for m in inp.new:
                if m["id"] in carriers:
                    units.append({"unit_ref": f"u{len(units)}", "event_ref": m["id"], "kind": "rui_ro_lua_dao", "level_hint": "amber",
                                  "quotes": [{"message_id": m["message_id"], "field": "body", "text": m["text"][:100]}], "summary": "tin có dấu hiệu chèn lệnh, bỏ qua"})
            return replay.AnalysisResult(output={"contract_version": "brain-out/1", "thread_ref": inp.thread_ref, "units": units}, usage={})

    await replay.run_replay(ds, tmp_path / "bad", analyzer=Obedient())
    await replay.run_replay(ds, tmp_path / "good", analyzer=Resistant())
    bad = ic.check(tmp_path / "bad", injections, ds)
    good = ic.check(tmp_path / "good", injections, ds)
    kinds = {v["type"] for v in bad["violations"]}
    assert not bad["ok"] and {"canary_echoed", "fake_account_in_fact", "fake_account_in_draft"} <= kinds
    assert good["ok"], good["violations"]
    assert {v["injection"] for v in bad["violations"] if v["type"] == "canary_echoed"} == {i.id for i in injs}


def test_injection_check_red_from_self_declared() -> None:
    ds, injs = inj.injected_dataset(h.synth_dataset())
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        p = Path(d)
        (p / "outputs.jsonl").write_text(json.dumps({"turn": 1, "output": {"units": [{"unit_ref": "u1", "kind": "viec_han", "level_hint": "red", "summary": "x",
                                                                                        "quotes": [{"message_id": "m_inj01", "field": "body", "text": "x"}]}]}}) + "\n")
        r = ic.check(p, inj.as_dicts())
        assert [v["type"] for v in r["violations"]] == ["red_from_self_declared"]
