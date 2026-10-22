"""Nội dung mẹo "Sếp biết chưa?" và bài học "Lộ trình học cùng Gen" (v0.1.54, g1-api).

- `content/lessons.json` = [{id: 'N01'..'N10', order: int, title, body (≤ 600 ký tự), try?: {label, target},
  unlock?: [biểu thức], done_signal?: tên tín hiệu}] — 10 bài nội dung (do gói tài liệu viết).
- `content/tips.json` = [{key, topic, when: [biểu thức], title, body (≤ 300 ký tự), try?: {label, target}}].
- 9 bài G05..G11, G13, G14 KHÔNG nằm trong tệp: dựng lúc chạy từ `gh.gen.registry` (guide) — title, why, console.label;
  order = n × 10; nút "Làm thử" trỏ `guide.item.do:<n>`; xong khi tín hiệu `followup.<n>.done`.
- `curriculum()` = N + G sắp theo order, k = vị trí 1-based (đủ nội dung thì đúng 19 bài).

Biểu thức điều kiện = danh sách tên tín hiệu (`signals.STATE_SIGNALS`), AND, tiền tố '!' = phủ định (`eval_cond`).
Tệp sai schema ⇒ `ValueError` có câu rõ ràng (bài/mẹo nào, trường nào) — không bao giờ lặng lẽ bỏ qua.
"""

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from gh.gen.coach.signals import FOLLOWUP_NS, STATE_SIGNALS, TOPICS, eval_cond

__all__ = ["CONTENT_DIR", "LESSON_BODY_MAX", "TIP_BODY_MAX", "curriculum", "eval_cond", "guide_lessons",
           "load_lessons", "load_tips"]

CONTENT_DIR = Path(__file__).with_name("content")
LESSON_BODY_MAX = 600
TIP_BODY_MAX = 300
TITLE_MAX = 120
LABEL_MAX = 40
_LESSON_ID = re.compile(r"^N(0[1-9]|10)$")
_TIP_KEY = re.compile(r"^[a-z0-9][a-z0-9_.-]{1,63}$")
GUIDE_TRY_LABEL = "Làm thử"


def _err(where: str, why: str) -> ValueError:
    return ValueError(f"Nội dung Gen hướng dẫn sai: {where} — {why}")


def _read(name: str) -> list[Any]:
    path = CONTENT_DIR / name
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as e:
        raise _err(name, "không thấy tệp") from e
    except (OSError, ValueError) as e:
        raise _err(name, f"không đọc được JSON ({type(e).__name__})") from e
    if not isinstance(raw, list):
        raise _err(name, "gốc phải là một danh sách")
    return raw


def _check_keys(where: str, obj: Any, required: set[str], optional: set[str]) -> dict[str, Any]:
    if not isinstance(obj, dict):
        raise _err(where, "phải là một đối tượng")
    keys = {k for k in obj if not str(k).startswith("_")}     # khoá bắt đầu bằng '_' = ghi chú, bỏ qua
    if missing := required - keys:
        raise _err(where, f"thiếu trường {', '.join(sorted(missing))}")
    if extra := keys - required - optional:
        raise _err(where, f"trường lạ {', '.join(sorted(extra))}")
    return {k: v for k, v in obj.items() if k in keys}


def _text(where: str, field: str, v: Any, max_len: int) -> str:
    if not isinstance(v, str) or not v.strip():
        raise _err(where, f"{field} phải là chuỗi không rỗng")
    if len(v) > max_len:
        raise _err(where, f"{field} dài {len(v)} ký tự, tối đa {max_len}")
    return v


def _try(where: str, v: Any) -> dict[str, str]:
    t = _check_keys(f"{where}.try", v, {"label", "target"}, set())
    return {"label": _text(where, "try.label", t["label"], LABEL_MAX),
            "target": _text(where, "try.target", t["target"], 120)}


def _expr(where: str, field: str, v: Any) -> list[str]:
    if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
        raise _err(where, f"{field} phải là danh sách tên tín hiệu")
    for x in v:
        name = x.strip().lstrip("!").strip()
        if name not in STATE_SIGNALS:
            raise _err(where, f"{field} dùng tín hiệu lạ '{x}'")
    return [x.strip() for x in v]


def _validate_lessons(raw: list[Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for i, item in enumerate(raw):
        where = f"lessons.json[{i}]"
        o = _check_keys(where, item, {"id", "order", "title", "body"}, {"try", "unlock", "done_signal"})
        lid = o["id"]
        if not isinstance(lid, str) or not _LESSON_ID.match(lid):
            raise _err(where, f"id '{lid}' phải là N01..N10")
        if lid in seen:
            raise _err(where, f"id {lid} bị lặp")
        seen.add(lid)
        where = f"lessons.json {lid}"
        order = o["order"]
        if isinstance(order, bool) or not isinstance(order, int) or not 1 <= order <= 10000:
            raise _err(where, "order phải là số nguyên 1..10000")
        # Giữ đúng hình dạng của tệp: trường tuỳ chọn chỉ có mặt khi tệp có (người dùng `.get()` cho an toàn).
        lesson: dict[str, Any] = {"id": lid, "order": order, "title": _text(where, "title", o["title"], TITLE_MAX),
                                  "body": _text(where, "body", o["body"], LESSON_BODY_MAX), "kind": "N"}
        if "try" in o and o["try"] is not None:
            lesson["try"] = _try(where, o["try"])
        if "unlock" in o:
            lesson["unlock"] = _expr(where, "unlock", o["unlock"])
        if o.get("done_signal") is not None:
            ds = o["done_signal"]
            if not isinstance(ds, str) or ds not in STATE_SIGNALS:
                raise _err(where, f"done_signal '{ds}' không phải tín hiệu trạng thái")
            lesson["done_signal"] = ds
        out.append(lesson)
    return out


def _validate_tips(raw: list[Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for i, item in enumerate(raw):
        where = f"tips.json[{i}]"
        o = _check_keys(where, item, {"key", "topic", "when", "title", "body"}, {"try"})
        key = o["key"]
        if not isinstance(key, str) or not _TIP_KEY.match(key):
            raise _err(where, f"key '{key}' không hợp lệ (chữ thường, số, '_', '.', '-')")
        if key in seen:
            raise _err(where, f"key {key} bị lặp")
        seen.add(key)
        where = f"tips.json {key}"
        if o["topic"] not in TOPICS:
            raise _err(where, f"topic '{o['topic']}' không thuộc {', '.join(sorted(TOPICS))}")
        tip: dict[str, Any] = {"key": key, "topic": o["topic"], "when": _expr(where, "when", o["when"]),
                               "title": _text(where, "title", o["title"], TITLE_MAX),
                               "body": _text(where, "body", o["body"], TIP_BODY_MAX)}
        if "try" in o and o["try"] is not None:
            tip["try"] = _try(where, o["try"])
        out.append(tip)
    return out


@lru_cache(maxsize=1)
def load_lessons() -> tuple[dict[str, Any], ...]:
    """10 bài nội dung N01..N10 từ `content/lessons.json` (đã kiểm schema). Test đổi `CONTENT_DIR` thì gọi
    `load_lessons.cache_clear()`."""
    return tuple(_validate_lessons(_read("lessons.json")))


@lru_cache(maxsize=1)
def load_tips() -> tuple[dict[str, Any], ...]:
    """Mẹo từ `content/tips.json` (đã kiểm schema), giữ NGUYÊN thứ tự trong tệp."""
    return tuple(_validate_tips(_read("tips.json")))


def guide_lessons() -> list[dict[str, Any]]:
    """9 bài G05..G11, G13, G14 dựng từ registry.guide: body = lý do ("vì sao cần") + nơi làm; order = n × 10."""
    from gh.gen import registry

    out: list[dict[str, Any]] = []
    for g in registry.load().guide:
        n = g.get("n")
        if n not in FOLLOWUP_NS:
            continue
        label = (g.get("console") or {}).get("label")
        why = str(g.get("why") or "").strip()
        body = f"{why} Làm ở: {label}." if label else why
        out.append({"id": f"G{n:02d}", "order": n * 10, "title": str(g.get("title") or f"Việc thiết lập {n}"),
                    "body": body, "kind": "G", "try": {"label": GUIDE_TRY_LABEL, "target": f"guide.item.do:{n}"},
                    "unlock": [], "done_signal": f"followup.{n}.done"})
    return sorted(out, key=lambda x: x["order"])


def curriculum() -> list[dict[str, Any]]:
    """Lộ trình = N + G sắp theo order (cùng order thì theo id); `k` = vị trí 1-based. Đủ nội dung thì đúng 19 bài."""
    merged = sorted([*load_lessons(), *guide_lessons()], key=lambda x: (x["order"], x["id"]))
    return [{**x, "k": k} for k, x in enumerate(merged, start=1)]
