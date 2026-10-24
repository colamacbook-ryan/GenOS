# ruff: noqa: E501
"""Đồng hồ giả + bộ lập lượt phân tích theo luồng (spec §4D.1 / §4D.5, CNS-101) — HÀM THUẦN, không DB, không đồng hồ thật.

Mô phỏng đúng luật scheduler trên thời gian giả:
* theo luồng (`thread_key` = nhóm; tin 1-1 là luồng `dm:`); mỗi lượt chỉ MỘT luồng;
* kích hoạt: tin chờ cũ nhất ≥ `cadence_s` (20 phút) HOẶC ≥ `count_trigger` (40) tin mới; HOẶC luật (a) `fast_rule` đánh dấu ứng viên
  (chạy ngay — điểm cắm, mặc định không có luật nên mặc định không có lượt nhanh);
* một lượt nhận tối đa `max_msgs` (60) tin / `max_chars` (12.000 ký tự); dư thì lượt kế CÙNG nhóm nối tiếp ("carry"), KHÔNG cắt giữa
  một chuỗi trả lời (`reply_to` trỏ tin trong lượt, hoặc cùng người gửi trong 2 phút theo giờ gốc) — trừ khi không còn chỗ cắt (đếm `hard_cut`);
* tin không cần model (sticker, reaction, "dạ/ok/cảm ơn") không vào phần tin mới nhưng được đếm (`skipped`) cho dòng độ phủ;
* hàng chờ xoay vòng theo luồng; `workers` lượt song song, mỗi lượt kéo dài `turn_s` giây giả (M9: lượt ≤ 60 s);
* giãn nhịp (§4D.5 bậc 2): luồng chạm 80% trần luồng/ngày hoặc tổ chức chạm 80% trần ⇒ nhịp 60 phút (`stretched`); vượt trần chỉ ĐÁNH DẤU
  (`over_cap`), KHÔNG chặn — replay phải thấy đủ tin để chấm và để S7 đo số lượt thật cần. (Lệch spec có chủ ý, ghi ở báo cáo L6.)
Ngày tính trần = ngày GỐC (giờ VN) của tin cuối lượt — trong chế độ nén đồng hồ giả chạy nhanh nên không thể dùng ngày giả.

Hai chế độ thời gian: `realtime` (thời gian giả = `ts` gốc, chạy một lần làm tham chiếu) và `compressed` (CNS-101): giữ thứ tự toàn cục và
chuỗi `reply_to`, chỉ NÉN khoảng lặng — mỗi khoảng cách giữa hai tin liên tiếp bị cắt còn ≤ `max_gap_s`; `tune_max_gap` chọn mức nén NHẸ NHẤT
cho trung vị lượt của nhóm sôi nổi ≥ 10 tin; `burst` ép ít nhất một ca dư 60 tin mỗi kịch bản."""

from __future__ import annotations

import heapq
import re
from collections import defaultdict, deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from common import VN_TZ, Msg, quantiles

NO_MODEL_RE = re.compile(r"^\W*(da|dạ|vang|vâng|ok|oke|okie|okay|uh|ừ|u|cam on|cảm ơn|cám ơn|thanks|thank you|tks|👍|🙏|❤️)\W*$", re.I)
CHAIN_GAP_S = 120


class FakeClock:
    """Đồng hồ giả: giây kể từ gốc; `now()` trả datetime có múi giờ (neo ở `origin`). Chỉ tiến, không bao giờ đọc giờ máy thật."""

    def __init__(self, origin: datetime) -> None:
        self.origin = origin
        self.t = 0.0

    def advance_to(self, t: float) -> None:
        if t < self.t - 1e-9:
            raise ValueError(f"đồng hồ giả không lùi: {t} < {self.t}")
        self.t = max(self.t, t)

    def now(self) -> datetime:
        return self.origin + timedelta(seconds=self.t)

    def at(self, t: float) -> datetime:
        return self.origin + timedelta(seconds=t)


@dataclass(frozen=True)
class PlanConfig:
    cadence_s: float = 1200.0
    count_trigger: int = 40
    max_msgs: int = 60
    max_chars: int = 12_000
    turn_s: float = 60.0
    workers: int = 1
    stretch_cadence_s: float = 3600.0
    daily_call_cap: int = 400
    thread_cap_share: float = 0.10
    stretch_at: float = 0.80
    recent_msgs: int = 30
    recent_hours: float = 24.0


@dataclass
class Turn:
    n: int
    thread: str
    start: float                      # giây giả
    end: float
    reason: str                       # cadence | count | fast | carry
    new: list[Msg]
    recent: list[Msg]                 # cửa sổ gần (đã xử lý, nhãn "ngữ cảnh")
    skipped: int = 0                  # tin không cần model trong khoảng này
    carry_after: int = 0              # số tin còn chờ sau lượt này vì trần 60/12.000
    hard_cut: bool = False
    stretched: bool = False
    over_cap: bool = False
    orig_day: str = ""
    chars: int = 0

    def to_json(self) -> dict[str, Any]:
        return {"n": self.n, "thread": self.thread, "start_s": self.start, "end_s": self.end, "reason": self.reason,
                "new_n": len(self.new), "new_ids": [m.id for m in self.new], "recent_ids": [m.id for m in self.recent],
                "skipped": self.skipped, "carry_after": self.carry_after, "hard_cut": self.hard_cut, "stretched": self.stretched,
                "over_cap": self.over_cap, "orig_day": self.orig_day, "chars": self.chars}


def needs_model(m: Msg) -> bool:
    if m.kind in ("sticker", "reaction"):
        return False
    return not NO_MODEL_RE.match(m.text.strip()) if m.text.strip() else False


def continues(prev: Msg, nxt: Msg, taken_ids: set[str]) -> bool:
    """`nxt` nối tiếp chuỗi của lượt (không được cắt giữa): trả lời một tin đã trong lượt, hoặc cùng người gửi ≤ 2 phút (giờ gốc)."""
    if nxt.reply_to and nxt.reply_to in taken_ids:
        return True
    return nxt.sender_id == prev.sender_id and (nxt.ts - prev.ts).total_seconds() <= CHAIN_GAP_S


def cut_turn(pending: list[Msg], cfg: PlanConfig) -> tuple[int, bool]:
    """Số tin lấy vào lượt (từ đầu `pending`) và cờ cắt cứng. Trần 60 tin / 12.000 ký tự; lùi về chỗ cắt không phá chuỗi."""
    chars = 0
    k = 0
    for m in pending:
        if k >= cfg.max_msgs or (k > 0 and chars + len(m.text) > cfg.max_chars):
            break
        chars += len(m.text)
        k += 1
    if k >= len(pending):
        return k, False
    j = k
    while j > 0 and continues(pending[j - 1], pending[j], {m.id for m in pending[:j]}):
        j -= 1
    if j == 0:
        return k, True          # cả lượt là một chuỗi dài hơn trần ⇒ buộc cắt cứng
    return j, False


def compress_gaps(msgs: list[Msg], max_gap_s: float, burst_idx: tuple[int, int] | None = None, burst_gap_s: float = 0.05) -> list[float]:
    """Thời gian giả (giây) cho từng tin theo thứ tự đã sắp: mỗi khoảng cách liên tiếp bị cắt còn ≤ `max_gap_s` (thứ tự giữ nguyên);
    trong cửa sổ `burst_idx` (chỉ số bắt đầu, kết thúc) khoảng cách còn ≤ `burst_gap_s` để dựng ca dư tin."""
    t = [0.0]
    for i in range(1, len(msgs)):
        gap = max(0.0, (msgs[i].ts - msgs[i - 1].ts).total_seconds())
        cap = max_gap_s
        if burst_idx and burst_idx[0] < i <= burst_idx[1]:
            cap = min(cap, burst_gap_s)
        t.append(t[-1] + min(gap, cap))
    return t


def data_time(msgs: list[Msg], times: list[float], t: float) -> datetime:
    """Giờ của DỮ LIỆU ứng với thời điểm giả `t` (giây): giờ gốc của tin cuối cùng đã đến (`times[i] ≤ t`) + phần giây giả đã trôi
    sau tin đó, chặn trước giờ gốc của tin kế. Chế độ realtime ⇒ đúng bằng đồng hồ giả; chế độ nén ⇒ giờ thật của lượt (đồng hồ giả
    nén chỉ dùng để lập lượt — đưa nó cho pipeline làm "bây giờ" thì mốc "quá hạn/dưới 24 giờ/hôm nay" và cửa sổ truy hồi sai)."""
    import bisect

    i = bisect.bisect_right(times, t + 1e-9) - 1
    if i < 0:
        return msgs[0].ts - timedelta(seconds=times[0] - t)
    at = msgs[i].ts + timedelta(seconds=max(0.0, t - times[i]))
    if i + 1 < len(msgs) and at > msgs[i + 1].ts:
        at = msgs[i + 1].ts
    return at


def realtime_times(msgs: list[Msg]) -> list[float]:
    t0 = msgs[0].ts
    return [(m.ts - t0).total_seconds() for m in msgs]


def plan_batch40(msgs: list[Msg], chunk_events: int = 40, chunk_chars: int = 24_000) -> list[Turn]:
    """KHUÔN CŨ để so (red team B, spec §10): `extract.CHUNK_EVENTS = 40` / `CHUNK_CHARS = 24_000`, một lô TRỘN nhiều nhóm theo thời gian, không
    có tin cũ làm ngữ cảnh. Luồng = "mixed". Thời điểm lô = thời điểm tin cuối của lô (thời gian thật)."""
    turns: list[Turn] = []
    cur: list[Msg] = []
    chars = 0
    t0 = msgs[0].ts if msgs else None

    def flush() -> None:
        nonlocal cur, chars
        if cur:
            at = (cur[-1].ts - t0).total_seconds() if t0 else 0.0
            turns.append(Turn(n=len(turns) + 1, thread="mixed", start=at, end=at, reason="batch40", new=cur, recent=[], chars=chars,
                              orig_day=_orig_day(cur[-1])))
        cur, chars = [], 0

    for m in msgs:
        if cur and (len(cur) >= chunk_events or chars + len(m.text) > chunk_chars):
            flush()
        cur.append(m)
        chars += len(m.text)
    flush()
    return turns


def lively_threads(msgs: list[Msg], floor: int = 60) -> set[str]:
    """Nhóm sôi nổi (CNS-101): số tin ≥ max(floor, trung vị số tin các nhóm) — định nghĩa của L6, ghi trong báo cáo."""
    cnt: dict[str, int] = defaultdict(int)
    for m in msgs:
        cnt[m.thread_key] += 1
    if not cnt:
        return set()
    med = quantiles([float(v) for v in cnt.values()])["median"] or 0
    return {g for g, v in cnt.items() if v >= max(floor, med)}


def densest_window(msgs: list[Msg], thread: str, size: int) -> tuple[int, int] | None:
    """Chỉ số [đầu, cuối] (trong danh sách toàn cục) của đoạn ngắn nhất chứa `size` tin liên tiếp của `thread` (đoạn dày nhất)."""
    idx = [i for i, m in enumerate(msgs) if m.thread_key == thread]
    if len(idx) < size:
        return None
    best = min(range(len(idx) - size + 1), key=lambda a: idx[a + size - 1] - idx[a])
    return idx[best], idx[best + size - 1]


def plan_turns(msgs: list[Msg], times: list[float], cfg: PlanConfig | None = None,
               fast_rule: Callable[[Msg], bool] | None = None, stats: dict[str, int] | None = None) -> list[Turn]:
    """Mô phỏng scheduler. `msgs` đã sắp theo thời gian; `times[i]` = giây giả khi tin i tới. `stats` (nếu có) nhận
    `skipped_flushed` = số tin không cần model bị xả mà không có lượt nào (vẫn tính vào độ phủ)."""
    cfg = cfg or PlanConfig()
    fast_rule = fast_rule or (lambda m: False)
    assert len(msgs) == len(times)
    seq = 0
    heap: list[tuple[float, int, int, Any]] = []        # (t, prio, seq, payload); prio 0 = tin tới, 1 = đồng hồ nhịp, 2 = worker rảnh

    def push(t: float, prio: int, payload: Any) -> None:
        nonlocal seq
        seq += 1
        heapq.heappush(heap, (t, prio, seq, payload))

    for i, t in enumerate(times):
        push(t, 0, i)
    pending: dict[str, list[tuple[float, Msg]]] = defaultdict(list)      # chưa phân tích (kể cả tin không cần model)
    seen: dict[str, list[Msg]] = defaultdict(list)                        # đã phân tích hoặc đã qua (cho cửa sổ gần)
    queue: deque[tuple[str, str]] = deque()
    queued: set[str] = set()
    free_at = [0.0] * cfg.workers
    day_calls: dict[str, int] = defaultdict(int)
    thread_day_calls: dict[tuple[str, str], int] = defaultdict(int)
    stretched_threads: set[tuple[str, str]] = set()
    turns: list[Turn] = []
    flushed = 0
    thread_cap = max(1.0, cfg.daily_call_cap * cfg.thread_cap_share)

    def trigger(thread: str, reason: str) -> None:
        if thread not in queued:
            queued.add(thread)
            queue.append((thread, reason))

    def cadence_for(thread: str, now: float) -> float:
        day = _orig_day(pending[thread][0][1]) if pending[thread] else ""
        if (thread, day) in stretched_threads or day_calls.get(day, 0) >= cfg.stretch_at * cfg.daily_call_cap:
            return cfg.stretch_cadence_s
        return cfg.cadence_s

    def dispatch(now: float) -> None:
        nonlocal flushed
        while queue:
            w = min(range(cfg.workers), key=lambda k: free_at[k])
            if free_at[w] > now + 1e-9:
                push(free_at[w], 2, None)
                return
            thread, reason = queue.popleft()
            queued.discard(thread)
            items = pending[thread]
            model_msgs = [m for _, m in items if needs_model(m)]
            if not model_msgs:           # chỉ còn tin không cần model: xả, không gọi model (vẫn đếm độ phủ)
                seen[thread].extend(m for _, m in items)
                pending[thread] = []
                flushed += len(items)
                continue
            k, hard = cut_turn(model_msgs, cfg)
            take = model_msgs[:k]
            take_ids = {m.id for m in take}
            # phần tin không cần model nằm trước tin cuối lượt thì đi cùng lượt này (đã đếm), phần sau giữ chờ
            last_pos = max(i for i, (_, m) in enumerate(items) if m.id in take_ids)
            consumed = items[: last_pos + 1]
            rest = items[last_pos + 1:]
            skipped_now = sum(1 for _, m in consumed if m.id not in take_ids)
            lo = take[0].ts - timedelta(hours=cfg.recent_hours)
            recent = [m for m in seen[thread] if m.ts >= lo][-cfg.recent_msgs:]
            day = _orig_day(take[-1])
            stretched = (thread, day) in stretched_threads or day_calls.get(day, 0) >= cfg.stretch_at * cfg.daily_call_cap
            day_calls[day] += 1
            thread_day_calls[(thread, day)] += 1
            if thread_day_calls[(thread, day)] >= cfg.stretch_at * thread_cap:
                stretched_threads.add((thread, day))
            over = day_calls[day] > cfg.daily_call_cap or thread_day_calls[(thread, day)] > thread_cap
            carry = sum(1 for _, m in rest if needs_model(m))
            t = Turn(n=len(turns) + 1, thread=thread, start=now, end=now + cfg.turn_s, reason=reason, new=take, recent=recent,
                     skipped=skipped_now, carry_after=carry, hard_cut=hard, stretched=stretched, over_cap=over, orig_day=day,
                     chars=sum(len(m.text) for m in take))
            turns.append(t)
            seen[thread].extend(m for _, m in consumed)
            pending[thread] = rest
            free_at[w] = now + cfg.turn_s
            if rest:
                if k < len(model_msgs):                  # dư vì trần 60/12.000 ⇒ lượt kế cùng nhóm, nối tiếp
                    trigger(thread, "carry")
                else:                                    # chỉ còn tin tới sau lượt ⇒ nhịp mới tính từ tin cũ nhất còn chờ
                    push(max(now, rest[0][0] + cadence_for(thread, now)), 1, (thread, rest[0][0]))
        return

    while heap:
        now, prio, _, payload = heapq.heappop(heap)
        if prio == 0:
            m = msgs[payload]
            th = m.thread_key
            first = not pending[th]
            pending[th].append((now, m))
            if first:
                push(now + cadence_for(th, now), 1, (th, now))
            if fast_rule(m):
                trigger(th, "fast")
            elif sum(1 for _, x in pending[th] if needs_model(x)) >= cfg.count_trigger:
                trigger(th, "count")
        elif prio == 1:
            th, oldest = payload
            if pending[th] and abs(pending[th][0][0] - oldest) < 1e-9:
                trigger(th, "cadence")
        dispatch(now)
    if stats is not None:
        stats["skipped_flushed"] = flushed
    return turns


def _orig_day(m: Msg) -> str:
    return m.ts.astimezone(VN_TZ).strftime("%Y-%m-%d")


# ─── Báo cáo phân phối tin/lượt (CNS-101) ────────────────────────────────────────────────────────────────────────────────

def distribution(turns: list[Turn], lively: set[str], cfg: PlanConfig | None = None, skipped_flushed: int = 0) -> dict[str, Any]:
    cfg = cfg or PlanConfig()
    per: dict[str, list[float]] = defaultdict(list)
    for t in turns:
        per[t.thread].append(float(len(t.new)))
    lively_sizes = [float(len(t.new)) for t in turns if t.thread in lively]
    return {
        "turns_n": len(turns),
        "overall": quantiles([float(len(t.new)) for t in turns]),
        "lively_threads": sorted(lively),
        "lively": quantiles(lively_sizes),
        "per_thread": {g: quantiles(v) for g, v in sorted(per.items())},
        "reasons": _count(t.reason for t in turns),
        "overflow_turns": sum(1 for t in turns if t.carry_after > 0),       # lượt phải cắt vì trần 60/12.000 (có ca dư)
        "hard_cuts": sum(1 for t in turns if t.hard_cut),
        "max_new": max((len(t.new) for t in turns), default=0),
        "max_chars": max((t.chars for t in turns), default=0),
        "over_cap_turns": sum(1 for t in turns if t.over_cap),
        "stretched_turns": sum(1 for t in turns if t.stretched),
        "skipped_total": sum(t.skipped for t in turns) + skipped_flushed,
        "chars_over_cap_turns": sum(1 for t in turns if t.chars > cfg.max_chars and len(t.new) > 1),
        "caps": {"max_msgs": cfg.max_msgs, "max_chars": cfg.max_chars},
    }


def check_distribution(dist: dict[str, Any], *, median_min: float = 10.0) -> dict[str, Any]:
    """Cổng "Phân phối tin/lượt" (bảng ngưỡng G-KB): mỗi nhóm sôi nổi trung vị ≥ 10 tin/lượt; ≥ 1 ca dư 60 tin; không lượt nào vượt trần."""
    caps = dist["caps"]
    bad_threads = {g: q["median"] for g, q in dist["per_thread"].items()
                   if g in dist["lively_threads"] and (q["median"] is None or q["median"] < median_min)}
    checks = {
        "lively_median_ge_10": not bad_threads and bool(dist["lively_threads"]),
        "has_overflow_case": dist["overflow_turns"] >= 1,
        "within_caps": dist["max_new"] <= caps["max_msgs"] and dist["chars_over_cap_turns"] == 0,
    }
    return {"ok": all(checks.values()), "checks": checks, "lively_below_median": bad_threads}


def tune_max_gap(msgs: list[Msg], cfg: PlanConfig | None = None, *, target_median: float = 10.0, burst: bool = True,
                 candidates: tuple[float, ...] = (3600, 1800, 900, 600, 300, 180, 120, 60, 30, 15, 5, 2),
                 fast_rule: Callable[[Msg], bool] | None = None) -> dict[str, Any]:
    """Chọn mức nén NHẸ NHẤT (khoảng lặng tối đa lớn nhất) mà mọi nhóm sôi nổi có trung vị ≥ `target_median` tin/lượt và có ca dư tin.
    Trả {max_gap_s, times, turns, dist, check, burst_idx}; không mức nào đạt ⇒ lấy mức nén mạnh nhất, `check.ok = False`."""
    cfg = cfg or PlanConfig()
    live = lively_threads(msgs)
    burst_idx = None
    if burst and live:
        top = max(live, key=lambda g: sum(1 for m in msgs if m.thread_key == g))
        # Dư tin cần: lượt đầu chạy ở tin thứ `count_trigger`, lượt hai (sau `turn_s`) nhận ≤ 60 ⇒ dư khi cụm ≥ count_trigger + max_msgs + 1.
        n_top = sum(1 for m in msgs if m.thread_key == top)
        burst_idx = densest_window(msgs, top, min(n_top, cfg.count_trigger + cfg.max_msgs + 30))   # +30: dự phòng tin không cần model
    best: dict[str, Any] | None = None
    for gap in candidates:
        times = compress_gaps(msgs, gap, burst_idx)
        stats: dict[str, int] = {}
        turns = plan_turns(msgs, times, cfg, fast_rule, stats)
        dist = distribution(turns, live, cfg, stats.get("skipped_flushed", 0))
        chk = check_distribution(dist, median_min=target_median)
        best = {"max_gap_s": gap, "times": times, "turns": turns, "dist": dist, "check": chk, "burst_idx": burst_idx}
        if chk["ok"]:
            return best
    assert best is not None
    return best


def _count(xs: Any) -> dict[str, int]:
    out: dict[str, int] = defaultdict(int)
    for x in xs:
        out[x] += 1
    return dict(out)
