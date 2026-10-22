"""Bộ máy quy tắc tất định (bước 1 của sàng lọc, ARCHITECTURE §4.3). Hàm thuần — không I/O.

Một quy tắc "khớp" khi tỉ lệ điều kiện thoả ≥ ngưỡng của quy tắc (thiết kế: "Ngưỡng 70%"). Điều kiện `llm` không
tự thoả ở bước này; quy tắc có điều kiện LLM được chuyển cho model kèm gợi ý, model trả độ tin theo mã quy tắc.
"""

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

from gh.textnorm import collapse_lower, strip_accents

ENTITY_TYPES = ("qty", "price", "budget", "phone", "product", "date")
CONDITION_TYPES = ("keyword_any", "keyword_all", "regex", "has_entity", "min_words", "max_words", "is_question",
                   "kind_in", "repeat_unanswered", "llm")
SET_FIELDS = ("intent", "side", "label", "person_type")
ADD_FIELDS = ("heat", "potential", "churn_risk", "fit")
PRIORITIES = ("P1", "P2", "P3")
KINDS = ("intent", "risk", "competition", "hr", "hygiene", "custom")

_UNITS = (r"cont(?:ainer)?s?|công|tấn|kg|tạ|yến|m2|m3|m²|m³|mét|m|cm|mm|cái|chiếc|bộ|thùng|pallet|tấm|cuộn|lô|xe|"
          r"chuyến|hộp|bao|lít|kiện|đơn|sp|sản phẩm|người|phòng|suất")
_RE_QTY = re.compile(rf"\b\d+(?:[.,]\d+)?\s*(?:{_UNITS})\b", re.I)
_RE_MONEY = re.compile(r"\b\d+(?:[.,]\d+)?\s*(?:tỷ|tỏi|triệu|tr|củ|k|nghìn|ngàn|đ|đồng|₫|vnd|vnđ|usd|\$)(?!\w)", re.I)
_RE_MONEY_WORD = re.compile(r"\b(?:ngân sách|báo giá|giá|budget)\b[^.?!\n]{0,20}\d", re.I)
_RE_PHONE = re.compile(r"(?:\+?84|\b0)(?:[\s.-]?\d){8,10}\b")
_RE_DATE = re.compile(r"\b(?:\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?|tháng\s*\d{1,2}|thứ\s*[2-7]|chủ nhật|ngày mai|"
                      r"tuần (?:sau|tới)|hôm nay|cuối tuần|đầu tháng|cuối tháng)\b", re.I)
_RE_PRODUCT = re.compile(r"\b(?=[\w-]*\d)(?=[\w-]*[^\W\d_])[\w-]{2,}\b|\b\d+\s*x\s*\d+\b|"
                         r"\b(?:mặt hàng|sản phẩm|mã hàng|quy cách|model|loại)\s+\w+", re.I)
_RE_QUESTION = re.compile(r"\?|\b(?:bao nhiêu|ở đâu|khi nào|thế nào|như nào|ra sao|được không|có không|bên nào|"
                          r"ai có|ai cần|ai bán|mấy|chưa ạ|không ạ|ko ạ|hông|hả|nhỉ)\b|"
                          r"\b(?:không|ko|chưa)\s*(?:ạ|anh|chị|em|bạn|nhé|vậy)?\s*[?.!]*\s*$", re.I)
_RE_REPEAT = re.compile(r"\b(?:hỏi|nhắc|nhắn|gọi)\s*(?:lại\s*)?(?:\d+|hai|ba|bốn|năm|mấy|nhiều)\s*lần\b|"
                        r"\blần thứ\s*(?:\d+|hai|ba|bốn)\b|"
                        r"\bkhông (?:ai|thấy ai) (?:trả lời|rep|phản hồi|trả lời giúp)\b", re.I)
_WORD = re.compile(r"\w+", re.U)

_NUM_WORDS = {"hai": 2, "ba": 3, "bốn": 4, "năm": 5, "mấy": 2, "nhiều": 3}


def _plain_re(r: re.Pattern[str]) -> re.Pattern[str]:
    return re.compile(strip_accents(r.pattern), r.flags)


def normalize(s: str) -> str:
    return collapse_lower(s)


def has_accents(s: str) -> bool:
    # So với dạng NFKC (không phải chính chuỗi): strip_accents (gh.textnorm, NFKD) còn quy ký tự tương thích ('…',
    # 'm²', chữ toàn chiều rộng) về dạng thường — các ký tự đó không phải dấu tiếng Việt, không được tính là "có dấu".
    return strip_accents(s) != unicodedata.normalize("NFKC", s)


def entities(text: str) -> dict[str, list[str]]:
    t = text or ""
    money = [m.group(0) for m in _RE_MONEY.finditer(t)] + [m.group(0) for m in _RE_MONEY_WORD.finditer(t)]
    return {
        "qty": [m.group(0) for m in _RE_QTY.finditer(t)],
        "price": money,
        "budget": money,
        "phone": [m.group(0) for m in _RE_PHONE.finditer(t)],
        "date": [m.group(0) for m in _RE_DATE.finditer(t)],
        "product": [m.group(0) for m in _RE_PRODUCT.finditer(t)],
    }


@dataclass
class EventCtx:
    text: str
    kind: str = "text"
    unanswered: int = 0         # số tin liên tiếp của người gửi chưa ai trả lời (trong cùng luồng)

    def __post_init__(self) -> None:
        self.norm = normalize(self.text)
        self.plain = strip_accents(self.norm)
        self.accented = has_accents(self.norm)
        self.words = _WORD.findall(self.norm)
        self.ents = entities(self.text)


_RE_QUESTION_PLAIN = _plain_re(_RE_QUESTION)


def _kw_hit(ctx: EventCtx, kw: str) -> bool:
    k = normalize(kw)
    if not k:
        return False
    pat = rf"(?<!\w){re.escape(k)}(?!\w)"
    if re.search(pat, ctx.norm):
        return True
    # Tin gõ không dấu ("gia bao nhieu") so với từ khoá bỏ dấu; tin có dấu thì giữ dấu để "kho" ≠ "khó".
    return not ctx.accented and bool(re.search(rf"(?<!\w){re.escape(strip_accents(k))}(?!\w)", ctx.plain))


def _repeat_count(ctx: EventCtx) -> int:
    m = _RE_REPEAT.search(ctx.norm)
    if not m:
        return 0
    num = re.search(r"\d+|hai|ba|bốn|năm|mấy|nhiều", m.group(0))
    if num is None:
        return 2
    v = num.group(0)
    return int(v) if v.isdigit() else _NUM_WORDS.get(v, 2)


def eval_condition(cond: dict[str, Any], ctx: EventCtx) -> bool | None:
    t = cond.get("type")
    if t == "keyword_any":
        return any(_kw_hit(ctx, k) for k in cond.get("values", []))
    if t == "keyword_all":
        vals = cond.get("values", [])
        return bool(vals) and all(_kw_hit(ctx, k) for k in vals)
    if t == "regex":
        try:
            return re.search(cond.get("pattern", ""), ctx.text or "", re.I) is not None
        except re.error:
            return False
    if t == "has_entity":
        return bool(ctx.ents.get(cond.get("entity", ""), []))
    if t == "min_words":
        return len(ctx.words) >= int(cond.get("n", 0))
    if t == "max_words":
        ok = len(ctx.words) <= int(cond.get("n", 0))
        if cond.get("no_entity"):
            ok = ok and not any(v for k, v in ctx.ents.items() if k != "budget")
        return ok
    if t == "is_question":
        return bool(_RE_QUESTION.search(ctx.norm) or (not ctx.accented and _RE_QUESTION_PLAIN.search(ctx.plain)))
    if t == "kind_in":
        vals = cond.get("values", [])
        if ctx.kind == "image" and "image" in vals:
            return not ctx.words        # "ảnh không chú thích"
        return ctx.kind in vals
    if t == "repeat_unanswered":
        return max(ctx.unanswered, _repeat_count(ctx)) >= int(cond.get("n", 2))
    if t == "llm":
        return None
    return False


@dataclass
class RuleDef:
    id: str
    code: str
    name: str
    kind: str
    version: int
    threshold: float
    conditions: list[dict[str, Any]]
    outputs: list[dict[str, Any]]
    prompt_hint: str | None = None
    enabled: bool = True

    @property
    def discards(self) -> bool:
        return any(o.get("discard") for o in self.outputs)

    @property
    def needs_llm(self) -> bool:
        return any(c.get("type") == "llm" for c in self.conditions)


@dataclass
class RuleHit:
    rule: RuleDef
    score: float
    matched: list[str] = field(default_factory=list)


def score_rule(rule: RuleDef, ctx: EventCtx) -> RuleHit:
    if not rule.conditions:
        return RuleHit(rule, 0.0)
    matched = []
    for c in rule.conditions:
        if eval_condition(c, ctx):
            matched.append(c.get("label") or c.get("type", ""))
    return RuleHit(rule, len(matched) / len(rule.conditions), matched)


@dataclass
class Outcome:
    hits: list[RuleHit]                 # quy tắc khớp tất định (điểm ≥ ngưỡng)
    near: list[RuleHit]                 # có ít nhất một điều kiện khớp nhưng chưa đủ ngưỡng — gợi ý cho LLM
    discarded_by: str | None

    @property
    def codes(self) -> list[str]:
        return [h.rule.code for h in self.hits]


def evaluate(rules: list[RuleDef], ctx: EventCtx) -> Outcome:
    hits, near = [], []
    for r in rules:
        if not r.enabled:
            continue
        h = score_rule(r, ctx)
        if h.score >= r.threshold - 1e-9 and h.score > 0:
            hits.append(h)
        elif h.score > 0 or r.needs_llm:
            near.append(h)
    discard = [h for h in hits if h.rule.discards]
    keep = [h for h in hits if not h.rule.discards]
    # Nhiễu chỉ bị loại khi không có quy tắc nghiệp vụ nào khớp và không cần model xét thêm.
    discarded_by = discard[0].rule.code if discard and not keep and not any(
        n.rule.needs_llm and not n.rule.discards and n.score > 0 for n in near) else None
    return Outcome(hits, near, discarded_by)


def apply_outputs(hits: list[RuleHit]) -> dict[str, Any]:
    """Gộp kết quả của các quy tắc khớp: set (lần đầu thắng), add (cộng dồn, trần 100), alert (ưu tiên cao nhất)."""
    sets: dict[str, Any] = {}
    adds: dict[str, float] = {}
    alert: str | None = None
    for h in sorted(hits, key=lambda h: -h.score):
        for o in h.rule.outputs:
            if "set" in o:
                sets.setdefault(o["set"], o.get("value"))
            elif "add" in o:
                adds[o["add"]] = min(100.0, adds.get(o["add"], 0.0) + float(o.get("value", 0)))
            elif "alert" in o:
                p = o["alert"] if o["alert"] in PRIORITIES else "P2"
                alert = p if alert is None or p < alert else alert
    return {"sets": sets, "adds": adds, "alert": alert}


# ─── Kiểm tra dữ liệu quy tắc (API) ─────────────────────────────────────────

def validate(conditions: list[dict[str, Any]], outputs: list[dict[str, Any]]) -> dict[str, str]:
    errors: dict[str, str] = {}
    if not conditions:
        errors["conditions"] = "Cần ít nhất một điều kiện"
    for i, c in enumerate(conditions):
        t = c.get("type")
        key = f"conditions.{i}"
        if t not in CONDITION_TYPES:
            errors[key] = f"Loại điều kiện không hợp lệ: {t}"
        elif t in ("keyword_any", "keyword_all", "kind_in"):
            vals = c.get("values")
            if not isinstance(vals, list) or not all(isinstance(v, str) and v.strip() for v in vals):
                errors[key] = "values phải là danh sách chuỗi"
        elif t == "regex":
            pat = c.get("pattern")
            if not isinstance(pat, str) or not pat or len(pat) > 500:
                errors[key] = "pattern bắt buộc, tối đa 500 ký tự"
            else:
                try:
                    re.compile(pat)
                except re.error as e:
                    errors[key] = f"Biểu thức chính quy lỗi: {e}"
        elif t == "has_entity" and c.get("entity") not in ENTITY_TYPES:
            errors[key] = f"entity phải thuộc {', '.join(ENTITY_TYPES)}"
        elif t in ("min_words", "max_words", "repeat_unanswered"):
            n = c.get("n")
            if not isinstance(n, int) or not 0 <= n <= 1000:
                errors[key] = "n phải là số nguyên 0–1000"
        elif t == "llm" and not str(c.get("hint") or c.get("label") or "").strip():
            errors[key] = "Điều kiện LLM cần mô tả (hint)"
    if not outputs:
        errors["outputs"] = "Cần ít nhất một kết quả"
    for i, o in enumerate(outputs):
        key = f"outputs.{i}"
        if "set" in o:
            if o["set"] not in SET_FIELDS or not isinstance(o.get("value"), str) or not o["value"]:
                errors[key] = f"set phải thuộc {', '.join(SET_FIELDS)} và có value"
        elif "add" in o:
            if o["add"] not in ADD_FIELDS or not isinstance(o.get("value"), (int, float)) \
                    or not -100 <= o["value"] <= 100:
                errors[key] = f"add phải thuộc {', '.join(ADD_FIELDS)}, value −100…100"
        elif "discard" in o:
            if o["discard"] is not True:
                errors[key] = "discard phải là true"
        elif "alert" in o:
            if o["alert"] not in PRIORITIES:
                errors[key] = "alert phải là P1, P2 hoặc P3"
        else:
            errors[key] = "Kết quả phải là set, add, discard hoặc alert"
    return errors
