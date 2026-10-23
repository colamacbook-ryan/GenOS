# ruff: noqa: E501
"""Trần chi phí + nhịp của lượt phân tích theo luồng (v0.1.76, gói L2, spec Bước 1 r6.6.1 §4D.5, CNS-074/099/100).

MỘT nguồn chi phí: `gh/ai_cost.py` (sổ `agent.model_calls` × giá Owner nhập; CLI thuê bao = 0 ₫). Module này KHÔNG có bảng giá hay
sổ riêng; nó chỉ ĐẾM từ sổ đó (lượt và VND, theo ngày VN, theo luồng) và quyết định cho một lượt sắp chạy. Phần `decide` là hàm
thuần (không DB) để test được mọi bậc.

Nhịp: luồng có tin mới VÀ (tin chờ cũ nhất ≥ `intel.cadence_min` = 20 phút HOẶC ≥ 40 tin mới); giãn lên 60 phút khi luồng hoặc tổ chức
chạm 80% trần; ứng viên của 6 cổng INT-19 chạy NGAY (đường nhanh ≤ 5 phút).

Ba lớp lượt (mỗi lớp một `purpose` trong sổ để đếm riêng, tất cả bắt đầu bằng `refinery.` nên vẫn là "việc nền" của router):
- `refinery.group`  — lượt thường (nhịp / ngưỡng / thủ công): tính vào trần chính `intel_daily_call_cap` (400/ngày);
- `refinery.gate`   — chạy ngay của 4 cổng còn lại: tính vào trần chính VÀ vào `intel_gate_call_cap` (40); hết trần cổng ⇒ xếp như lượt thường;
- `refinery.safety` — lừa đảo/an toàn: trần RIÊNG `intel_safety_call_cap` (60, ngoài 400). Vượt trần ⇒ báo Ryan, KHÔNG cắt ("không giới hạn
  có giám sát").

Bậc trần (theo tỉ lệ dùng): ≥ 80% (luồng hoặc tổ chức) ⇒ GIÃN nhịp; tổ chức 100% ⇒ chỉ luật (a) cho lượt thường (tin nằm chờ tới 00:00
giờ VN, trần đặt lại — không có job "lô đêm" riêng); `intel_daily_budget_vnd = 0` mà sổ đã có chi phí > 0 (model tính tiền theo token lọt
vào) ⇒ CHẾ ĐỘ MIỄN PHÍ: không lớp nào gọi model nữa (Q14), lừa đảo/an toàn đi bằng luật cứng.
"""

import math
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gh import ai_cost
from gh.providers.cli import CLI_KINDS
from gh.refinery import clock

KIND_NORMAL, KIND_GATE, KIND_SAFETY = "normal", "gate", "safety"
PURPOSE = {KIND_NORMAL: "refinery.group", KIND_GATE: "refinery.gate", KIND_SAFETY: "refinery.safety"}
KIND_BY_PURPOSE = {v: k for k, v in PURPOSE.items()}

DEFAULT_CADENCE_MIN = 20
STRETCHED_CADENCE_MIN = 60
COUNT_THRESHOLD = 40
STRETCH_AT = 0.8
THREAD_SHARE = 0.10
THREAD_FLOOR_VND = 15_000

# Chế độ (`Decision.mode`)
MODE_NORMAL, MODE_STRETCHED, MODE_RULES_ONLY, MODE_FREE = "normal", "stretched", "rules_only", "free_rules_only"


@dataclass(frozen=True)
class Caps:
    call_cap: int
    safety_call_cap: int
    gate_call_cap: int
    budget_vnd: int
    safety_budget_vnd: int
    gate_budget_vnd: int

    @classmethod
    def from_settings(cls, v: dict[str, int]) -> "Caps":
        return cls(v["intel_daily_call_cap"], v["intel_safety_call_cap"], v["intel_gate_call_cap"], v["intel_daily_budget_vnd"],
                   v["intel_safety_budget_vnd"], v["intel_gate_budget_vnd"])

    @property
    def thread_call_cap(self) -> int:
        """Trần luồng theo lượt = 10% `intel_daily_call_cap` (spec: "hoặc 10% intel_daily_call_cap"), tối thiểu 1 khi có trần."""
        return 0 if self.call_cap <= 0 else max(1, math.ceil(self.call_cap * THREAD_SHARE))

    @property
    def thread_vnd_cap(self) -> int | None:
        """Trần luồng theo VND = max(15.000 ₫; 10% trần tổ chức) — chỉ khi có trần VND (> 0)."""
        return None if self.budget_vnd <= 0 else max(THREAD_FLOOR_VND, math.ceil(self.budget_vnd * THREAD_SHARE))


@dataclass
class Usage:
    """Đếm trong ngày VN từ `agent.model_calls` (status ok)."""

    calls: dict[str, int] = field(default_factory=dict)             # kind → số lượt
    vnd: dict[str, float] = field(default_factory=dict)             # kind → VND
    thread_calls: dict[str, int] = field(default_factory=dict)      # thread_key → số lượt (mọi lớp)
    thread_vnd: dict[str, float] = field(default_factory=dict)

    @property
    def org_calls(self) -> int:
        """Lượt tính vào trần CHÍNH: thường + cổng (lượt an toàn nằm ngoài)."""
        return self.calls.get(KIND_NORMAL, 0) + self.calls.get(KIND_GATE, 0)

    @property
    def org_vnd(self) -> float:
        return self.vnd.get(KIND_NORMAL, 0.0) + self.vnd.get(KIND_GATE, 0.0)

    @property
    def total_vnd(self) -> float:
        return sum(self.vnd.values())


@dataclass(frozen=True)
class Decision:
    allow: bool                                   # có gọi model cho lượt này không
    mode: str                                     # normal | stretched | rules_only | free_rules_only
    kind: str                                     # lớp THỰC DÙNG (cổng hết trần ⇒ hạ về normal)
    stretch_thread: bool = False                  # luồng này phải giãn nhịp
    stretch_org: bool = False                     # cả tổ chức giãn nhịp
    capped: bool = False                          # trần chính 100%
    over_safety_cap: bool = False                 # lượt an toàn vượt trần — vẫn chạy, báo Ryan
    reasons: tuple[str, ...] = ()

    @property
    def stretched(self) -> bool:
        return self.stretch_thread or self.stretch_org


def thread_ratio(thread_key: str, caps: Caps, usage: Usage) -> float:
    if caps.call_cap <= 0:
        return 1.0
    r = usage.thread_calls.get(thread_key, 0) / caps.thread_call_cap
    if (vc := caps.thread_vnd_cap) is not None:
        r = max(r, usage.thread_vnd.get(thread_key, 0.0) / vc)
    return r


def org_ratio(caps: Caps, usage: Usage) -> float:
    if caps.call_cap <= 0:
        return 1.0
    r = usage.org_calls / caps.call_cap
    if caps.budget_vnd > 0:
        r = max(r, usage.org_vnd / caps.budget_vnd)
    return r


def free_mode(caps: Caps, usage: Usage) -> bool:
    """`intel_daily_budget_vnd = 0` (chế độ gói thuê bao) mà sổ ngày đã ghi chi phí > 0 ⇒ có model TÍNH TIỀN theo token đã lọt vào
    chuỗi: không gọi tiếp (Q14, "không chi tiền theo token"). Có trần VND > 0 ⇒ không dùng chế độ này."""
    return caps.budget_vnd <= 0 and usage.total_vnd > 0.0


def cadence_minutes(base: int, stretched: bool) -> int:
    return max(base, STRETCHED_CADENCE_MIN) if stretched else base


def decide(kind: str, thread_key: str, caps: Caps, usage: Usage) -> Decision:
    """Quyết định cho MỘT lượt sắp chạy của `thread_key` thuộc lớp `kind` (hàm thuần)."""
    if kind not in PURPOSE:
        raise ValueError(f"lớp lượt không hợp lệ: {kind!r}")
    o, t = org_ratio(caps, usage), thread_ratio(thread_key, caps, usage)
    stretch_org, stretch_thread, capped = o >= STRETCH_AT, t >= STRETCH_AT, o >= 1.0
    common: dict[str, Any] = {"stretch_thread": stretch_thread, "stretch_org": stretch_org, "capped": capped}
    if free_mode(caps, usage):
        return Decision(False, MODE_FREE, kind, reasons=("free_mode",), **common)
    if kind == KIND_SAFETY:
        over = caps.safety_call_cap <= 0 or usage.calls.get(KIND_SAFETY, 0) >= caps.safety_call_cap
        if caps.safety_budget_vnd > 0 and usage.vnd.get(KIND_SAFETY, 0.0) >= caps.safety_budget_vnd:
            over = True
        # KHÔNG bao giờ cắt lừa đảo/an toàn vì trần: vượt ⇒ báo Ryan (bên gọi), vẫn chạy.
        return Decision(True, MODE_STRETCHED if (stretch_thread or stretch_org) else MODE_NORMAL, kind,
                        over_safety_cap=over, reasons=("safety_cap_exceeded",) if over else (), **common)
    if kind == KIND_GATE:
        gate_used = usage.calls.get(KIND_GATE, 0)
        gate_ok = gate_used < caps.gate_call_cap and (caps.gate_budget_vnd <= 0
                                                       or usage.vnd.get(KIND_GATE, 0.0) < caps.gate_budget_vnd)
        if caps.gate_call_cap > 0 and gate_ok:
            # Đường chạy ngay của 4 cổng KHÔNG bị giãn nhịp và vẫn chạy khi tổ chức chạm trần chính (tới trần cổng).
            return Decision(True, MODE_STRETCHED if (stretch_thread or stretch_org) else MODE_NORMAL, kind, **common)
        d = decide(KIND_NORMAL, thread_key, caps, usage)
        return Decision(d.allow, d.mode, KIND_NORMAL, d.stretch_thread, d.stretch_org, d.capped, False,
                        ("gate_cap_reached", *d.reasons))
    if capped or t >= 1.0:
        return Decision(False, MODE_RULES_ONLY, kind, reasons=("org_cap" if capped else "thread_cap",), **common)
    return Decision(True, MODE_STRETCHED if (stretch_thread or stretch_org) else MODE_NORMAL, kind, **common)


# ─── đọc sổ ─────────────────────────────────────────────────────────────────────────────────────────────────────────────

def day_start(now: datetime | None = None) -> tuple[datetime, datetime]:
    """[00:00, 24:00) của ngày VN chứa `now` (mặc định `clock.now()`)."""
    return ai_cost.day_bounds(ai_cost.today_vn(now or clock.now()))


async def load_caps(db: AsyncSession, org_id: uuid.UUID) -> Caps:
    return Caps.from_settings(await ai_cost.get_intel(db, org_id))


async def load_usage(db: AsyncSession, org_id: uuid.UUID, now: datetime | None = None) -> Usage:
    """Số lượt/VND hôm nay (giờ VN) của các lượt phân tích theo luồng: dòng sổ có `purpose` `refinery.group|gate|safety`."""
    start, end = day_start(now)
    rows = (await db.execute(text(f"""
        SELECT c.purpose, c.thread_key, count(*) AS calls, COALESCE(sum({ai_cost._COST_EXPR}), 0) AS cost
        {ai_cost._CALLS_FROM}
        WHERE c.org_id = :o AND c.status = 'ok' AND c.at >= :s AND c.at < :e AND c.purpose = ANY(:ps)
        GROUP BY c.purpose, c.thread_key"""),  # noqa: S608 — biểu thức chi phí là hằng của ai_cost
        {"o": org_id, "s": start, "e": end, "ps": list(KIND_BY_PURPOSE), "cli": list(CLI_KINDS)})).all()
    u = Usage()
    for r in rows:
        kind = KIND_BY_PURPOSE[r.purpose]
        u.calls[kind] = u.calls.get(kind, 0) + int(r.calls)
        u.vnd[kind] = u.vnd.get(kind, 0.0) + float(Decimal(str(r.cost)))
        if r.thread_key:
            u.thread_calls[r.thread_key] = u.thread_calls.get(r.thread_key, 0) + int(r.calls)
            u.thread_vnd[r.thread_key] = u.thread_vnd.get(r.thread_key, 0.0) + float(Decimal(str(r.cost)))
    return u


# ─── trạng thái giãn nhịp / trần theo luồng (`refinery.group_watermarks`) ─────────────────────────────────────────────

async def sync_thread_state(db: AsyncSession, org_id: uuid.UUID, thread_key: str, d: Decision, *,
                            group_id: uuid.UUID | None = None, now: datetime | None = None) -> None:
    """Ghi `priority='stretched'` + `stretched_at` khi bắt đầu giãn nhịp; `capped_at` khi chạm trần; khi hết thì cộng số phút đã
    chịu vào `stretched_minutes` / `capped_minutes` (M13) và xoá mốc. Gọi lại nhiều lần an toàn (chỉ đổi khi trạng thái đổi)."""
    now = now or clock.now()
    await db.execute(text("""INSERT INTO refinery.group_watermarks (org_id, thread_key, group_id) VALUES (:o, :t, :g)
                             ON CONFLICT (org_id, thread_key) DO NOTHING"""), {"o": org_id, "t": thread_key, "g": group_id})
    row = (await db.execute(text("""SELECT priority, stretched_at, capped_at FROM refinery.group_watermarks
                                    WHERE org_id = :o AND thread_key = :t FOR UPDATE"""),
                            {"o": org_id, "t": thread_key})).one()
    want_stretch = d.stretched and d.mode != MODE_FREE
    want_cap = d.capped or d.mode in (MODE_RULES_ONLY, MODE_FREE)
    sets: list[str] = []
    p: dict[str, Any] = {"o": org_id, "t": thread_key}
    if want_stretch and row.stretched_at is None:
        sets += ["priority = 'stretched'", "stretched_at = :now"]
        p["now"] = now
    elif not want_stretch and row.stretched_at is not None:
        sets += ["priority = 'normal'", "stretched_minutes = stretched_minutes + :sm", "stretched_at = NULL"]
        p["sm"] = _minutes(now - row.stretched_at)
    if want_cap and row.capped_at is None:
        sets.append("capped_at = :now")
        p["now"] = now
    elif not want_cap and row.capped_at is not None:
        sets += ["capped_minutes = capped_minutes + :cm", "capped_at = NULL"]
        p["cm"] = _minutes(now - row.capped_at)
    if sets:
        await db.execute(text(f"UPDATE refinery.group_watermarks SET {', '.join(sets)} WHERE org_id = :o AND thread_key = :t"), p)  # noqa: S608


def _minutes(delta: timedelta) -> int:
    return max(0, int(delta.total_seconds() // 60))


async def coverage_notes(db: AsyncSession, org_id: uuid.UUID, now: datetime | None = None) -> list[str]:
    """Dòng độ phủ (§4D.5): "nhóm X giãn nhịp từ 14:10"; chế độ miễn phí/trần ghi rõ. Giờ theo giờ VN."""
    now = now or clock.now()
    caps, usage = await load_caps(db, org_id), await load_usage(db, org_id, now)
    notes: list[str] = []
    if free_mode(caps, usage):
        notes.append("đang chạy chế độ miễn phí: chỉ quét luật")
    elif org_ratio(caps, usage) >= 1.0:
        notes.append("đã hết lượt phân tích hôm nay: chỉ quét luật (lừa đảo/an toàn vẫn chạy)")
    rows = (await db.execute(text("""
        SELECT w.thread_key, w.stretched_at, w.capped_at, g.name
        FROM refinery.group_watermarks w LEFT JOIN core.groups g ON g.id = w.group_id
        WHERE w.org_id = :o AND (w.stretched_at IS NOT NULL OR w.capped_at IS NOT NULL) ORDER BY w.stretched_at, w.thread_key"""),
        {"o": org_id})).all()
    for r in rows:
        who = f"nhóm {r.name}" if r.name else f"luồng {r.thread_key}"
        if r.capped_at is not None:
            notes.append(f"{who} hết lượt từ {r.capped_at.astimezone(ai_cost.VN_TZ).strftime('%H:%M')}")
        elif r.stretched_at is not None:
            notes.append(f"{who} giãn nhịp từ {r.stretched_at.astimezone(ai_cost.VN_TZ).strftime('%H:%M')}")
    return notes


# ─── nhịp: luồng nào đến hạn chạy (hàm thuần) ───────────────────────────────────────────────────────────────────────────

@dataclass
class ThreadState:
    """Trạng thái hàng chờ của MỘT luồng. `n` đã gộp xả tin (một người > 30 tin / 10 phút = một khối) và bỏ tin không cần model."""

    thread_key: str
    n: int
    oldest: datetime                                # tin chờ cũ nhất (giờ NHẬN)
    group_id: uuid.UUID | None = None
    fast: bool = False                              # có tin đi đường nhanh (tag agent / 1-1 reply)
    gate_class: str | None = None                   # 'safety' | 'gate' | None — ứng viên của 6 cổng INT-19 (luật (a))
    next_try_at: datetime | None = None             # lùi lại sau khi model lỗi (không dội lại mỗi 5 giây)
    last_analysis_at: datetime | None = None


def due_reason(ts: ThreadState, now: datetime, *, cadence_min: int = DEFAULT_CADENCE_MIN, stretched: bool = False,
               count_threshold: int = COUNT_THRESHOLD) -> str | None:
    """Lý do luồng đến hạn: 'safety' / 'gate' (chạy NGAY, không chờ nhịp, không bị giãn), 'fast', 'count' (≥ 40 tin mới) hoặc
    'cadence' (tin chờ cũ nhất ≥ `cadence_min`; 60 phút khi giãn nhịp); None = chưa tới hạn. Không có tin mới (n = 0) ⇒ None."""
    if ts.n <= 0:
        return None
    if ts.next_try_at is not None and ts.next_try_at > now:
        return None
    if ts.gate_class in (KIND_SAFETY, KIND_GATE):
        return ts.gate_class
    if ts.fast:
        return "fast"
    if ts.n >= count_threshold:
        return "count"
    if (now - ts.oldest).total_seconds() >= cadence_minutes(cadence_min, stretched) * 60:
        return "cadence"
    return None


_RANK = {KIND_SAFETY: 0, KIND_GATE: 1, "fast": 2, "count": 3, "cadence": 4}


def order_threads(items: list[tuple[ThreadState, str]]) -> list[tuple[ThreadState, str]]:
    """Xoay vòng theo luồng (round-robin): an toàn → cổng → nhanh → ngưỡng → nhịp; trong cùng hạng, luồng LÂU CHƯA được phân tích đi
    trước (chưa bao giờ = trước hết), rồi tin chờ cũ hơn. Luồng ồn không chen ngang luồng khác vì mỗi luồng chỉ một lượt mỗi vòng."""
    epoch = datetime.min.replace(tzinfo=clock.now().tzinfo)
    return sorted(items, key=lambda it: (_RANK.get(it[1], 9), it[0].last_analysis_at or epoch, it[0].oldest, it[0].thread_key))


def kind_for(reason: str) -> str:
    return {KIND_SAFETY: KIND_SAFETY, KIND_GATE: KIND_GATE}.get(reason, KIND_NORMAL)
