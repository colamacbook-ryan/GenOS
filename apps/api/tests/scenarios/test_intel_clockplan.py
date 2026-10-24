# ruff: noqa: E501
"""Đồng hồ giả + bộ lập lượt (L6, CNS-101): thứ tự và reply_to được giữ khi nén, lượt 10–60 tin, đúng trần 60 tin / 12.000 ký tự, có ca dư, không cắt giữa
chuỗi trả lời, kích hoạt đúng luật §4D.1, tin không cần model không vào lượt, giãn nhịp, khuôn lô 40 cũ."""

from datetime import timedelta

import clockplan as cp
import common
import pytest

from tests.scenarios import intel_helpers as h


def mk(i: int, group: str = "G1", *, secs: int = 0, sender: str = "u1", text: str | None = None, reply_to: str | None = None) -> common.Msg:
    t0 = h.datetime(2026, 10, 1, 8, 0, tzinfo=h.VN)
    return common.Msg(id=f"t{i:04d}", group=group, group_name=group, ts=t0 + timedelta(seconds=secs), sender_id=sender, sender_name=sender,
                      text=text if text is not None else f"nội dung số {i} có độ dài vừa phải để tính ký tự", reply_to=reply_to)


def test_fake_clock_only_moves_forward_and_never_reads_real_time() -> None:
    c = cp.FakeClock(h.datetime(2026, 10, 1, 8, 0, tzinfo=h.VN))
    c.advance_to(90)
    assert c.now() == h.datetime(2026, 10, 1, 8, 1, 30, tzinfo=h.VN)
    with pytest.raises(ValueError):
        c.advance_to(10)


def test_compress_gaps_keeps_order_and_clips_silence() -> None:
    msgs = [mk(1, secs=0), mk(2, secs=30), mk(3, secs=3 * 3600), mk(4, secs=3 * 3600 + 5)]
    t = cp.compress_gaps(msgs, 60)
    assert t == [0, 30, 90, 95]                 # khoảng lặng 3 giờ bị nén còn 60 s, thứ tự giữ nguyên
    assert t == sorted(t)
    assert cp.realtime_times(msgs) == [0, 30, 3 * 3600, 3 * 3600 + 5]


def test_turn_triggers_on_count_40_and_on_cadence_20_minutes() -> None:
    burst = [mk(i, secs=i) for i in range(1, 41)]                      # 40 tin trong 40 giây ⇒ chạy ngay khi đủ 40
    turns = cp.plan_turns(burst, cp.realtime_times(burst))
    assert [(t.reason, len(t.new)) for t in turns] == [("count", 40)]
    assert turns[0].start == pytest.approx(40, abs=1)
    slow = [mk(1, secs=0), mk(2, secs=60)]                                # 2 tin lẻ ⇒ chờ nhịp 20 phút kể từ tin cũ nhất
    t2 = cp.plan_turns(slow, cp.realtime_times(slow))
    assert [(t.reason, len(t.new)) for t in t2] == [("cadence", 2)]
    assert t2[0].start == pytest.approx(1200, abs=1)


def test_fast_rule_runs_immediately() -> None:
    msgs = [mk(1, secs=0), mk(2, secs=10, text="đã nhập OTP rồi")]
    turns = cp.plan_turns(msgs, cp.realtime_times(msgs), fast_rule=lambda m: "OTP" in m.text)
    assert turns[0].reason == "fast" and turns[0].start == pytest.approx(10, abs=1) and len(turns[0].new) == 2


def test_overflow_over_60_messages_carries_to_next_turn_same_thread_no_loss() -> None:
    msgs = [mk(i, secs=0, sender=f"u{i}") for i in range(1, 131)]         # 130 tin cùng lúc, người gửi khác nhau, không reply
    turns = cp.plan_turns(msgs, [0.0] * 130)
    sizes = [len(t.new) for t in turns]
    assert sizes == [40, 60, 30]            # lượt 1 chạy ở tin thứ 40; trong lúc nó chạy dồn 90 tin ⇒ lượt 2 đúng trần 60, dư 30 sang lượt 3
    assert [t.reason for t in turns] == ["count", "count", "carry"]
    assert turns[1].carry_after == 30 and turns[2].carry_after == 0 and {t.thread for t in turns} == {"G1"}
    assert turns[1].start >= turns[0].end             # một worker: lượt sau chờ lượt trước xong
    assert sorted(m.id for t in turns for m in t.new) == [m.id for m in msgs]      # không mất, không trùng tin
    assert [m.id for t in turns for m in t.new] == [m.id for m in msgs]            # thứ tự giữ nguyên


def test_char_cap_12000() -> None:
    big = [mk(i, secs=0, sender=f"u{i}", text="x" * 5000) for i in range(1, 6)]    # mỗi tin 5.000 ký tự
    turns = cp.plan_turns(big, [0.0] * 5, cp.PlanConfig(count_trigger=1))
    assert all(t.chars <= 12_000 or len(t.new) == 1 for t in turns)
    assert max(len(t.new) for t in turns) <= 2


def test_cut_never_splits_a_reply_chain_or_same_sender_burst() -> None:
    cfg = cp.PlanConfig(max_msgs=5)
    # 7 tin: tin 5→6 là trả lời trực tiếp ⇒ không được cắt ở 5; phải lùi về 4
    msgs = [mk(i, secs=i * 1000, sender=f"u{i}") for i in range(1, 8)]
    msgs[5] = mk(6, secs=6000, sender="u6", reply_to="t0005")
    k, hard = cp.cut_turn(msgs, cfg)
    assert (k, hard) == (4, False)
    # cùng người gửi trong 2 phút cũng là một chuỗi
    msgs2 = [mk(i, secs=i * 1000, sender=f"u{i}") for i in range(1, 8)]
    msgs2[5] = mk(6, secs=5000 + 60, sender="u5")        # u5 gửi lại sau 60 s
    assert cp.cut_turn(msgs2, cfg) == (4, False)
    # cả lượt là một chuỗi dài hơn trần ⇒ buộc cắt cứng, đếm
    chain = [mk(1, secs=0, sender="u1")] + [mk(i, secs=i, sender="u1") for i in range(2, 9)]
    assert cp.cut_turn(chain, cfg) == (5, True)


def test_no_model_messages_not_in_turn_but_counted() -> None:
    msgs = [mk(1, secs=0, text="dạ"), mk(2, secs=1, text="ok"), mk(3, secs=2, text="Hàng về chưa anh?"), mk(4, secs=3, text="cảm ơn")]
    stats: dict[str, int] = {}
    turns = cp.plan_turns(msgs, cp.realtime_times(msgs), stats=stats)
    assert [m.id for t in turns for m in t.new] == ["t0003"]
    d = cp.distribution(turns, set(), skipped_flushed=stats["skipped_flushed"])
    assert d["skipped_total"] == 3


def test_one_thread_per_turn_and_round_robin() -> None:
    msgs = [mk(i, "G1" if i % 2 else "G2", secs=i) for i in range(1, 121)]
    turns = cp.plan_turns(msgs, cp.realtime_times(msgs))
    for t in turns:
        assert {m.group for m in t.new} == {t.thread}


def test_recent_window_is_context_only() -> None:
    msgs = [mk(i, secs=i * 400) for i in range(1, 12)]
    turns = cp.plan_turns(msgs, cp.realtime_times(msgs))
    assert len(turns) >= 2
    seen_ids: set[str] = set()
    for t in turns:
        assert not ({m.id for m in t.recent} & {m.id for m in t.new})
        assert {m.id for m in t.recent} <= seen_ids         # cửa sổ gần chỉ gồm tin ĐÃ xử lý trước đó
        seen_ids |= {m.id for m in t.new}


def test_stretch_cadence_and_over_cap_flag_do_not_drop_messages() -> None:
    cfg = cp.PlanConfig(daily_call_cap=10, thread_cap_share=0.2, count_trigger=2, max_msgs=2)        # trần luồng = 2 lượt/ngày
    msgs = [mk(i, secs=i, sender=f"u{i}") for i in range(1, 21)]
    turns = cp.plan_turns(msgs, cp.realtime_times(msgs), cfg)
    assert any(t.stretched for t in turns) and any(t.over_cap for t in turns)
    assert sum(len(t.new) for t in turns) == 20


def test_tune_max_gap_hits_distribution_gate_on_synthetic_data() -> None:
    ds = h.synth_dataset()
    r = cp.tune_max_gap(ds.msgs)
    assert r["check"]["ok"], r["check"]
    d = r["dist"]
    assert d["lively"]["median"] >= 10 and d["overflow_turns"] >= 1 and d["max_new"] <= 60 and d["max_chars"] <= 12_000
    # mọi tin có mặt đúng một lần; thứ tự trong từng nhóm giữ nguyên; reply_to không bị đảo (tin trả lời sau tin gốc)
    flat = [m for t in r["turns"] for m in t.new if cp.needs_model(m)]
    assert len({m.id for m in flat}) == len(flat)
    for g in ("G1", "G2"):
        ids = [m.id for m in flat if m.group == g]
        assert ids == sorted(ids)
    order = {m.id: i for i, m in enumerate(flat)}
    for m in flat:
        if m.reply_to and m.reply_to in order:
            assert order[m.reply_to] < order[m.id]


def test_realtime_mode_is_reference_with_original_gaps() -> None:
    ds = h.synth_dataset()
    t = cp.realtime_times(ds.msgs)
    assert t[-1] == (ds.msgs[-1].ts - ds.msgs[0].ts).total_seconds()
    turns = cp.plan_turns(ds.msgs, t)
    assert sum(len(x.new) for x in turns) + sum(1 for m in ds.msgs if not cp.needs_model(m)) >= len(ds.msgs) - 0
    assert cp.distribution(turns, cp.lively_threads(ds.msgs))["overall"]["median"] < 10         # thời gian thật: lượt nhỏ ⇒ chính vì thế phải nén


def test_batch40_old_shape_mixes_groups() -> None:
    ds = h.synth_dataset()
    b = cp.plan_batch40(ds.msgs)
    assert all(len(t.new) <= 40 for t in b) and sum(len(t.new) for t in b) == len(ds.msgs)
    assert any(len({m.group for m in t.new}) > 1 for t in b) and all(t.thread == "mixed" and t.recent == [] for t in b)


def test_check_distribution_fails_when_lively_median_low() -> None:
    ds = h.synth_dataset()
    turns = cp.plan_turns(ds.msgs, cp.realtime_times(ds.msgs))
    d = cp.distribution(turns, cp.lively_threads(ds.msgs))
    chk = cp.check_distribution(d)
    assert not chk["ok"] and not chk["checks"]["lively_median_ge_10"] and "G1" in chk["lively_below_median"]


def test_data_time_maps_compressed_clock_back_to_data_time() -> None:
    """Lỗi kb1 (2): chế độ nén đưa đồng hồ NÉN cho pipeline làm "bây giờ" (kẹt ở ngày tin đầu). `data_time` = giờ gốc của tin cuối đã đến +
    phần giây giả trôi sau nó, không vượt tin kế; realtime ⇒ trùng đồng hồ giả."""
    msgs = [mk(1, secs=0), mk(2, secs=30), mk(3, secs=50 * 86400), mk(4, secs=50 * 86400 + 5)]
    t = cp.compress_gaps(msgs, 60)                                         # [0, 30, 90, 95]
    assert cp.data_time(msgs, t, 30) == msgs[1].ts
    assert cp.data_time(msgs, t, 45) == msgs[1].ts + timedelta(seconds=15)
    assert cp.data_time(msgs, t, 90) == msgs[2].ts                         # sau khoảng lặng 50 ngày: đúng ngày của tin
    assert cp.data_time(msgs, t, 200) == msgs[3].ts + timedelta(seconds=105)
    rt = cp.realtime_times(msgs)
    c = cp.FakeClock(msgs[0].ts)
    for x in (0, 31, 50 * 86400 + 1):
        c.advance_to(x)
        assert cp.data_time(msgs, rt, x) == c.now()
    seq = [cp.data_time(msgs, t, x) for x in range(0, 120, 3)]
    assert seq == sorted(seq)                                              # không bao giờ lùi
