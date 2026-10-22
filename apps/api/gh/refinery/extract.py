"""Bước 2 của sàng lọc: trích xuất bằng LLM có kiểm chứng chứng cứ (ARCHITECTURE §4.3, R7).

Model chỉ thấy mã tham chiếu ngắn (E1, E2…) của lô hiện tại. Mọi kết luận phải trích ít nhất một mã; kết luận trích
mã không có trong lô (bịa) bị loại toàn bộ. Đoạn trích hiển thị lấy từ bản ghi thô, không lấy từ model.
"""

import math
import re
from dataclasses import dataclass, field
from typing import Any

import orjson

from gh.chassis.masking import mask_for_prompt
from gh.providers.clients import Message, parse_json_block
from gh.refinery.rules import RuleDef

EVENT_TYPE_RE = re.compile(r"^[A-Z][A-Za-z]{2,40}$")
MAX_CONCLUSION = 400
CHUNK_EVENTS = 40
CHUNK_CHARS = 24_000

SYSTEM = """Bạn là Core agent sàng lọc của Gen-Harness: đọc tin nhắn thô từ nhóm chat doanh nghiệp (tiếng Việt) và \
rút ra các "sự kiện có nghĩa" (spec G3) cho kho dữ liệu sạch. Chỉ kết luận điều có trong tin; không suy diễn đời tư; \
không đánh giá con người.

Trả về DUY NHẤT một JSON object:
{"units": [{"evidence": ["E1"], "event_type": "AskedPrice", "side": "demand|supply|null",
  "conclusion": "1–2 câu tiếng Việt, cụ thể (mặt hàng, số lượng, giá, hạn)", "entities": {"product": "…", "qty": 3,
  "unit": "container", "budget_vnd": 1200000000, "place": "…", "deadline": "…", "competitor": "…"},
  "confidence": 0.0-1.0, "rules": {"R-01": 0.0-1.0}, "signals": {"heat": 0-100, "potential": 0-100,
  "churn_risk": 0-100, "fit": 0-100}}],
 "noise": ["E2"]}

Luật:
- "evidence" chỉ được dùng mã E… có trong danh sách tin bên dưới; mỗi unit ít nhất một mã.
- Mỗi tin phải xuất hiện trong ít nhất một unit hoặc trong "noise" (chào hỏi, xác nhận ngắn, sticker, tán gẫu).
- "event_type" dạng PascalCase, ưu tiên: {event_types}.
- "rules": độ tin (0–1) rằng quy tắc được mô tả bên dưới áp dụng cho unit này; bỏ qua quy tắc không liên quan.
- "confidence" là độ tin của chính kết luận; không chắc thì để thấp.
"""


@dataclass
class EventIn:
    ref: str
    event_id: str
    group: str | None
    sender: str | None
    time: str
    kind: str
    text: str
    hints: list[str] = field(default_factory=list)


@dataclass
class Unit:
    evidence: list[str]
    event_type: str
    side: str | None
    conclusion: str
    entities: dict[str, Any]
    confidence: float
    rules: dict[str, float]
    signals: dict[str, float]


@dataclass
class Extraction:
    units: list[Unit]
    noise: set[str]
    rejected: int
    reject_reasons: list[str] = field(default_factory=list)


def chunks(events: list[EventIn]) -> list[list[EventIn]]:
    out: list[list[EventIn]] = []
    cur: list[EventIn] = []
    size = 0
    for e in events:
        n = len(e.text or "") + 120
        if cur and (len(cur) >= CHUNK_EVENTS or size + n > CHUNK_CHARS):
            out.append(cur)
            cur, size = [], 0
        cur.append(e)
        size += n
    if cur:
        out.append(cur)
    return out


def build_messages(events: list[EventIn], rules: list[RuleDef], event_types: list[str]) -> list[Message]:
    rule_lines = []
    for r in rules:
        if not r.enabled or r.discards:
            continue
        conds = "; ".join(c.get("hint") or c.get("label") or c.get("type", "") for c in r.conditions)
        rule_lines.append(f"- {r.code} {r.name} (ngưỡng {r.threshold:.2f}): {conds}"
                          + (f". Gợi ý: {r.prompt_hint}" if r.prompt_hint else ""))
    # v0.1.71 (P2-3): tin nhắn của người ngoài vào prompt là bản đã che — OTP, mật khẩu, token, SĐT (giữ 3 số cuối), số
    # tài khoản / thẻ, email; số tiền, ngày giờ, mã đối tượng giữ nguyên để model còn trích `value_vnd`.
    lines = [orjson.dumps({"ref": e.ref, "group": mask_for_prompt(e.group), "sender": mask_for_prompt(e.sender),
                           "time": e.time, "kind": e.kind, "text": mask_for_prompt((e.text or "")[:2000]),
                           **({"rule_hints": e.hints} if e.hints else {})}).decode()
             for e in events]
    user = ("Quy tắc của tổ chức:\n" + ("\n".join(rule_lines) or "(không có)") +
            "\n\nTin nhắn (mỗi dòng một JSON):\n" + "\n".join(lines))
    return [Message("system", SYSTEM.replace("{event_types}", ", ".join(event_types))), Message("user", user)]


def _num(v: Any, lo: float, hi: float) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if math.isnan(f):
        return None
    return max(lo, min(hi, f))


def validate(raw_text: str, refs: set[str], rule_codes: set[str]) -> Extraction:
    data = parse_json_block(raw_text)
    if not isinstance(data, dict):
        raise ValueError("Model phải trả một JSON object")
    units: list[Unit] = []
    rejected, reasons = 0, []
    for u in data.get("units") or []:
        if not isinstance(u, dict):
            rejected += 1
            continue
        ev = u.get("evidence")
        if not isinstance(ev, list) or not ev or not all(isinstance(x, str) for x in ev):
            rejected += 1
            reasons.append("thiếu chứng cứ")
            continue
        bad = [x for x in ev if x not in refs]
        if bad:
            rejected += 1
            reasons.append(f"trích mã không có trong lô: {', '.join(bad[:3])}")
            continue
        et = str(u.get("event_type") or "")
        concl = str(u.get("conclusion") or "").strip()
        conf = _num(u.get("confidence"), 0.0, 1.0)
        if not EVENT_TYPE_RE.match(et) or not concl or conf is None:
            rejected += 1
            reasons.append("thiếu loại sự kiện / kết luận / độ tin")
            continue
        side = u.get("side") if u.get("side") in ("demand", "supply") else None
        raw_ents: dict[Any, Any] = u["entities"] if isinstance(u.get("entities"), dict) else {}
        ents = {str(k)[:40]: v for k, v in list(raw_ents.items())[:20]
                if isinstance(v, (str, int, float, bool)) or v is None}
        rules_conf = {}
        for code, v in (u.get("rules") or {}).items() if isinstance(u.get("rules"), dict) else []:
            c = _num(v, 0.0, 1.0)
            if code in rule_codes and c is not None:
                rules_conf[code] = c
        signals = {}
        for k, v in (u.get("signals") or {}).items() if isinstance(u.get("signals"), dict) else []:
            n = _num(v, 0.0, 100.0)
            if k in ("heat", "potential", "churn_risk", "fit") and n is not None:
                signals[k] = n
        units.append(Unit(list(dict.fromkeys(ev)), et, side, concl[:MAX_CONCLUSION], ents, conf, rules_conf, signals))
    noise = {x for x in (data.get("noise") or []) if isinstance(x, str) and x in refs}
    return Extraction(units, noise, rejected, reasons)
