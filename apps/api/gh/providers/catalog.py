"""Danh sách model + mức suy nghĩ (effort) cho nguồn CLI.

v0.1.32 — Boss 01/10: "high" KHÔNG phải một phần tên model mà là MỨC SUY NGHĨ. Model và effort tách riêng:
`agy -p … --model <model gốc> --effort <low|medium|high>`; `claude -p --model=<bí danh> --effort=<mức>`.

## Nguồn dữ liệu model (thứ tự tin cậy — Boss 01/10)
1. Chính CLI đã đăng nhập báo lúc chạy: `agy models` (đọc trung thực: biến thể "gemini-3.8-flash-high" hay
   "Gemini 3.8 Flash (High)" → model gốc `gemini-3.8-flash` + effort `high`), lỗi `Invalid model %q (available: %s)`
   của CLI (danh sách CLI tự nêu), `--help` của từng CLI.
2. Tài liệu chính thức có ghi URL + ngày kiểm (bên dưới, cạnh từng mục dự phòng).
3. Không có (1), (2): danh sách tối thiểu, mỗi mục mang `source` và `verified=False` → Console ghi "chưa xác minh".

Bằng chứng đã đo (2026-10-01, tệp chạy agy 1.2.9 thật, chưa đăng nhập):
- `agy --help`: "--model  Model for the current CLI session", "--effort  Reasoning effort for the current CLI session
  (low|medium|high)". `agy models` chưa đăng nhập: "Error: Please sign in to view available models", thoát 1;
  `agy models` không có cờ nào khác (`--json` → "flags provided but not defined").
- Changelog trong tệp chạy: "Added an `--effort` flag to select a model's reasoning-effort variant", "/model picker to
  group models by their base model and choose reasoning effort"; hàm `backend.SlugParts`, `EffortsForBase`,
  `ModelForBaseEffort`; lỗi "invalid model selection (--model %q --effort %q)", "Invalid model %q (available: %s)",
  "invalid --effort %q (valid: %s)". Biến thể có trong tệp chạy: gemini-3.8-flash-{low,medium,high},
  gemini-3.1-pro-{low,high}.
- `claude --help` 2.1.285: "--effort <level>  Effort level for the current session (low, medium, high, xhigh, max)";
  "--model <model> … an alias for the latest model (e.g. 'fable', 'opus', or 'sonnet') or a model's full name".
- https://code.claude.com/docs/en/model-config (đọc 2026-10-01): bí danh thêm `haiku`; bảng effort chỉ liệt kê
  Fable/Opus/Sonnet (low…max) — Haiku không có trong bảng → không gửi `--effort` cho haiku.
Bản v0.1.31 có "claude-sonnet-4-6"/"claude-opus-4-6" (qua Antigravity): tệp chạy agy 1.2.9 có chuỗi này nhưng chưa xác
minh khi đăng nhập (không phải nguồn (1)/(2)) → bỏ khỏi danh sách dự phòng cho đến khi xác minh.
"""

import re
from typing import Any

_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\r")
# Mã model: họ đã biết + phần đuôi chữ/số/chấm/gạch (gemini-3.8-flash-high, claude-sonnet-4-6, gpt-oss-120b…).
_SLUG = re.compile(r"(?<![\w./-])((?:gemini|claude|gpt-oss|gpt|o\d|deepseek|llama|qwen|mistral)[a-z0-9]*"
                   r"(?:[-.][a-z0-9]+)+)(?![\w/-])", re.I)
# Tên hiển thị khi dòng không có mã: "Gemini 3.8 Flash (High)", "Claude Sonnet 4.6 (Thinking)".
_DISPLAY = re.compile(r"\b(Gemini|Claude|GPT-OSS)\s+([A-Za-z0-9. ]+?)\s*(?:\(([A-Za-z ]+)\))?\s*$")
_SKIP = re.compile(r"available models|fetching|error|sign in|please|usage:|^\s*$", re.I)

EFFORT_ORDER = ("low", "medium", "high", "xhigh", "max")
EFFORT_LABEL = {"low": "Thấp", "medium": "Vừa", "high": "Cao", "xhigh": "Rất cao", "max": "Tối đa"}
AGY_EFFORTS = ("low", "medium", "high")                     # `agy --help` 1.2.9
CLAUDE_EFFORTS = ("low", "medium", "high", "xhigh", "max")  # `claude --help` 2.1.285
# "gemini-3.5-flash-extra-low" (có trong tệp chạy agy 1.2.9) KHÔNG phải "gemini-3.5-flash-extra" + "low" → giữ nguyên.
_VARIANT = re.compile(r"^(?P<base>.+?)(?<!-extra)-(?P<effort>low|medium|high)$", re.I)
_EFFORT_WORD = re.compile(r"(?<![\w-])(low|medium|high)(?![\w-])", re.I)

GROUP_ORDER = ("Gemini", "Claude (qua Antigravity)", "Claude", "GPT-OSS", "Khác")
TIER_HINT = {"fast": "nhanh, rẻ", "balanced": "cân bằng", "strong": "mạnh, chậm hơn, tốn hạn mức hơn"}

VERIFIED_ON = "2026-10-01"
AGY_SRC = (f"agy 1.2.9 — biến thể trong tệp chạy + `agy --help` (--effort low|medium|high), kiểm {VERIFIED_ON}; "
           "CHƯA xác minh bằng `agy models` khi đã đăng nhập")
CLAUDE_SRC = (f"`claude --help` 2.1.285 + https://code.claude.com/docs/en/model-config (đọc {VERIFIED_ON})")

# (3) Antigravity: tối thiểu, chưa xác minh — chỉ hiện khi `agy models` không đọc được (Console ghi "chưa xác minh").
# v0.1.55 (G1): mỗi model agy mang `tier` cho hồ sơ tiêu chuẩn (`tier_of`): flash = "balanced", pro = "strong".
# v0.1.66 (F025): MỘT bảng tầng — gợi ý của ô chọn model (`describe` → `tier_of`), `tier()` và hồ sơ chuẩn nói cùng
# một tầng cho cùng một model (test_model_chon_hop_dong.py kiểm).
AGY_FALLBACK: tuple[dict[str, Any], ...] = (
    {"id": "gemini-3.8-flash", "tier": "balanced", "efforts": ("low", "medium", "high"), "source": AGY_SRC,
     "verified": False},
    {"id": "gemini-3.1-pro", "tier": "strong", "efforts": ("low", "high"), "source": AGY_SRC, "verified": False},
)
# (1)+(2) Claude Code: bí danh của `claude --model` (tự trỏ tới bản mới nhất gói Claude cho dùng).
CLAUDE_CODE_MODELS: tuple[dict[str, Any], ...] = (
    {"id": "haiku", "label": "Haiku (bản mới nhất)", "tier": "fast", "efforts": (), "source": CLAUDE_SRC,
     "verified": True},
    {"id": "sonnet", "label": "Sonnet (bản mới nhất)", "tier": "balanced", "efforts": CLAUDE_EFFORTS,
     "source": CLAUDE_SRC, "verified": True},
    {"id": "opus", "label": "Opus (bản mới nhất)", "tier": "strong", "efforts": CLAUDE_EFFORTS, "source": CLAUDE_SRC,
     "verified": True},
    {"id": "fable", "label": "Fable (bản mới nhất)", "tier": "strong", "efforts": CLAUDE_EFFORTS,
     "source": CLAUDE_SRC, "verified": True},
)


def strip_ansi(s: str) -> str:
    return _ANSI.sub("", s)


def valid_efforts(kind: str) -> tuple[str, ...]:
    return AGY_EFFORTS if kind == "antigravity_cli" else CLAUDE_EFFORTS if kind == "claude_code_cli" else ()


def known_efforts(kind: str, model_id: str) -> tuple[str, ...] | None:
    """Mức suy nghĩ của model theo nguồn ĐÃ xác minh (CLI --help + tài liệu chính thức); None = không biết."""
    if kind == "claude_code_cli":
        for m in CLAUDE_CODE_MODELS:
            if m["id"] == model_id:
                return tuple(m["efforts"])
    return None


def split_variant(kind: str, model_id: str) -> tuple[str, str | None]:
    """"gemini-3.8-flash-high" (biến thể agy) → ("gemini-3.8-flash", "high"). Chỉ áp dụng cho Antigravity."""
    if kind != "antigravity_cli":
        return model_id, None
    m = _VARIANT.match(model_id.strip())
    return (m.group("base"), m.group("effort").lower()) if m else (model_id.strip(), None)


def sort_efforts(efforts: Any) -> list[str]:
    s = {str(e).lower() for e in efforts or ()}
    return [e for e in EFFORT_ORDER if e in s]


def _display_to_slug(family: str, rest: str, level: str | None) -> str:
    parts = [family.lower(), *rest.lower().split()]
    if level:
        parts += level.lower().split()
    return "-".join(p for p in parts if p)


def _base_label(label: str | None) -> str | None:
    if not label:
        return None
    return re.sub(r"\s*\((low|medium|high)\)\s*$", "", label, flags=re.I).strip() or None


def parse_agy_models(text: str) -> list[dict[str, Any]]:
    """Đọc đầu ra `agy models` → [{id (model gốc), label, efforts, current, current_effort}] theo thứ tự xuất hiện.

    Mỗi biến thể ("gemini-3.8-flash-high" / "Gemini 3.8 Flash (High)") gộp vào model gốc của nó; chữ low/medium/high
    đứng riêng trên cùng dòng (vd "gemini-3.8-flash  low, medium, high") cũng là mức suy nghĩ của model đó."""
    out: dict[str, dict[str, Any]] = {}
    for raw in strip_ansi(text).splitlines():
        line = raw.strip().lstrip("*•->› ").strip()
        if not line or (_SKIP.search(line) and not _SLUG.search(line)):
            continue
        current = bool(re.search(r"\(current\)|\(default\)|\[current\]|\bcurrent\b|^\s*[*>›]", strip_ansi(raw),
                                 re.I))
        line = re.sub(r"\s*[(\[](current|default)[)\]]\s*", " ", line, flags=re.I).strip()
        m = _SLUG.search(line)
        if m:
            slug = m.group(1).lower()
            rest = line[:m.start()] + " " + line[m.end():]
            label = re.sub(r"\s{2,}", " ", rest).strip(" -—:|[],")
        else:
            d = _DISPLAY.search(line)
            if not d:
                continue
            slug = _display_to_slug(d.group(1), d.group(2), d.group(3))
            label, rest = d.group(0).strip(), ""
        base, effort = split_variant("antigravity_cli", slug)
        efforts = {effort} if effort else set()
        # Chữ mức suy nghĩ đứng riêng ở phần còn lại của dòng ("low, medium, high", "(High)" trong tên hiển thị).
        efforts |= {w.lower() for w in _EFFORT_WORD.findall(rest)}
        clean = _base_label(re.sub(r"(?<![\w-])(low|medium|high)(?:\s*[,|/]\s*(low|medium|high))+", "", label,
                                   flags=re.I).strip(" -—:|[],")) if label else None
        entry = out.setdefault(base, {"id": base, "label": None, "efforts": set(), "current": False,
                                      "current_effort": None})
        entry["label"] = entry["label"] or clean
        entry["efforts"] |= efforts
        if current:
            entry["current"] = True
            entry["current_effort"] = entry["current_effort"] or effort
    return [{**e, "efforts": sort_efforts(e["efforts"])} for e in out.values()]


def parse_available(msg: str) -> list[str]:
    """Danh sách model CLI tự nêu trong lỗi `Invalid model %q (available: %s)` (agy 1.2.9)."""
    m = re.search(r"available:\s*([^)\n]+)", strip_ansi(msg), re.I)
    if not m:
        return []
    return [s for s in (x.strip(" '\"[]") for x in re.split(r"[,\s]+", m.group(1))) if s][:50]


def family(kind: str, model_id: str) -> str:
    m = model_id.lower()
    if m.startswith("gemini"):
        return "Gemini"
    if m.startswith("claude") or m.split("[")[0] in {"haiku", "sonnet", "opus", "fable", "opusplan"}:
        return "Claude (qua Antigravity)" if kind == "antigravity_cli" else "Claude"
    if m.startswith("gpt"):
        return "GPT-OSS"
    return "Khác"


#: Tầng model của hồ sơ tiêu chuẩn (v0.1.55, G1): fast < balanced < strong. Chat gọi tầng "strong" là "deep" (Kỹ hơn).
TIERS = ("fast", "balanced", "strong")
_TIER_FAST_WORDS = frozenset({"lite", "mini", "haiku", "nano", "20b"})
_TIER_STRONG_WORDS = frozenset({"pro", "opus", "fable", "ultra", "120b"})
_TIER_BALANCED_WORDS = frozenset({"flash", "sonnet"})
_TEXT_KINDS_NO_TIER = frozenset({"embedding", "system_one"})


def _tier_words(model_name: str) -> set[str]:
    # Tách theo gạch/chấm/gạch chéo/hai chấm: "google/gemini-2.5-flash-lite" → {google, gemini, 2, 5, flash, lite};
    # "gemini" KHÔNG chứa từ "mini" (so theo từ, không chuỗi con).
    return {w for w in re.split(r"[-_./:\[\]\s]+", model_name.lower()) if w}


def tier(model_id: str) -> str:
    """Tầng của một model theo TÊN (không theo mức suy nghĩ) — cùng bảng với `tier_of`; cho bên chỉ có mã model."""
    return infer_tier(model_id)


def infer_tier(model_name: str) -> str:
    """Suy tầng theo TÊN model (nguồn khoá API): flash-lite/mini/haiku/nano ⇒ fast; pro/opus ⇒ strong; flash/sonnet ⇒
    balanced; không đoán được ⇒ balanced. Thứ tự kiểm: fast trước (flash-lite có cả "flash" lẫn "lite")."""
    words = _tier_words(model_name)
    if words & _TIER_FAST_WORDS:
        return "fast"
    if words & _TIER_STRONG_WORDS:
        return "strong"
    if words & _TIER_BALANCED_WORDS:
        return "balanced"
    return "balanced"


def tier_of(kind: str, model_name: str) -> str | None:
    """Tầng của một model theo NGUỒN (v0.1.55, G1) — dữ liệu cho hồ sơ tiêu chuẩn, không ghi cứng tên model trong hồ sơ.

    - Claude Code CLI: theo nhãn sẵn của danh mục (haiku=fast, sonnet=balanced, opus/fable=strong); tên đầy đủ ngoài
      danh mục ("claude-sonnet-4-6") suy theo tên.
    - Antigravity CLI: theo nhãn mới của danh mục agy (flash=balanced, pro=strong); model khác suy theo tên, riêng
      "flash" của agy luôn là balanced.
    - Khoá API (gemini/deepseek/openai_compat): suy theo tên (`infer_tier`).
    - None: nguồn không sinh văn bản (embedding, system_one) hoặc model embedding."""
    if kind in _TEXT_KINDS_NO_TIER or re.search(r"embed", model_name, re.I):
        return None
    if kind == "claude_code_cli":
        base = model_name.split("[")[0].strip().lower()
        for m in CLAUDE_CODE_MODELS:
            if m["id"] == base:
                return str(m["tier"])
        return infer_tier(model_name)
    if kind == "antigravity_cli":
        base, _eff = split_variant(kind, model_name)
        for m in AGY_FALLBACK:
            if m["id"] == base.lower():
                return str(m["tier"])
        return infer_tier(base)
    return infer_tier(model_name)


def _pretty(model_id: str) -> str:
    """Nhãn đọc được từ mã model. Các mảnh số một chữ số đứng liền nhau là SỐ PHIÊN BẢN: "4-6" → "4.6"
    ("claude-sonnet-4-6-thinking" → "Claude Sonnet 4.6 Thinking"); mã đã có dấu chấm ("3.8") giữ nguyên."""
    words: list[str] = []
    for w in (x for x in re.split(r"[-_]", model_id) if x):
        if words and re.fullmatch(r"\d", w) and re.fullmatch(r"\d(?:\.\d)*", words[-1]):
            words[-1] += "." + w
        else:
            words.append(w.upper() if w in {"gpt", "oss"} else w.capitalize())
    return " ".join(words)


def describe(kind: str, model_id: str, *, label: str | None = None, source: str = "cli",
             tier_hint: str | None = None, efforts: Any = None, verified: bool = True,
             source_ref: str | None = None, current_effort: str | None = None) -> dict[str, Any]:
    t = tier_hint or tier_of(kind, model_id) or tier(model_id)
    effs = sort_efforts(efforts)
    return {"id": model_id, "label": label or _pretty(model_id), "group": family(kind, model_id), "tier": t,
            "hint": TIER_HINT[t], "source": source, "efforts": effs,
            "default_effort": current_effort if current_effort in effs else None,
            "verified": verified, "source_ref": source_ref}


def fallback(kind: str) -> list[dict[str, Any]]:
    if kind == "claude_code_cli":
        return [describe(kind, m["id"], label=m["label"], source="catalog", tier_hint=m["tier"], efforts=m["efforts"],
                         verified=m["verified"], source_ref=m["source"]) for m in CLAUDE_CODE_MODELS]
    if kind == "antigravity_cli":
        return [describe(kind, m["id"], source="catalog", efforts=m["efforts"], verified=m["verified"],
                         source_ref=m["source"]) for m in AGY_FALLBACK]
    return []


def grouped(models: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """[{label, models}] theo GROUP_ORDER; trong nhóm giữ thứ tự gốc."""
    groups: dict[str, list[dict[str, Any]]] = {}
    for m in models:
        groups.setdefault(m["group"], []).append(m)
    order = {g: i for i, g in enumerate(GROUP_ORDER)}
    return [{"label": g, "models": ms} for g, ms in sorted(groups.items(), key=lambda kv: order.get(kv[0], 99))]


def build(kind: str, discovered: list[str] | list[dict[str, Any]] | None,
          saved: list[tuple[str, str | None]] | None = None) -> dict[str, Any]:
    """Danh sách cuối cho Console: model CLI liệt kê được (nguồn "cli"), không có thì danh mục dự phòng ("catalog").

    Không bao giờ thu gọn còn đúng model đã lưu (Boss 01/10): CLI chỉ liệt kê ≤ 1 model gốc → thêm mục dự phòng
    (chưa xác minh) chưa có; model đã lưu mà CLI không liệt kê vẫn hiện (nguồn "saved")."""
    items: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add(d: dict[str, Any]) -> None:
        if d["id"] not in seen and not re.search(r"embed", d["id"], re.I):
            seen.add(d["id"])
            items.append(d)

    for d in discovered or []:
        if isinstance(d, dict):
            add(describe(kind, d["id"], label=d.get("label"), efforts=d.get("efforts"),
                         current_effort=d.get("current_effort"), source_ref=f"`agy models` ({kind})"
                         if kind == "antigravity_cli" else None))
        else:
            base, eff = split_variant(kind, d)
            if base in seen and eff:
                prev = next(x for x in items if x["id"] == base)
                prev["efforts"] = sort_efforts({*prev["efforts"], eff})
                continue
            add(describe(kind, base, efforts=[eff] if eff else None))
    source = "cli" if items else "catalog"
    if kind == "claude_code_cli" or len(items) <= 1:
        for f in fallback(kind):
            add(f)
    for name, eff in saved or []:
        base, var_eff = split_variant(kind, name)
        if base not in seen:
            add(describe(kind, base, source="saved", efforts=[e for e in (eff or var_eff,) if e], verified=False,
                         source_ref="model đã lưu"))
    return {"models_source": source, "model_groups": grouped(items), "models": [m["id"] for m in items]}
