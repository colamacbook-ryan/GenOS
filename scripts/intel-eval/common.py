# ruff: noqa: E501
"""Dùng chung cho bộ đo G-KB (gói L6): nạp bộ dữ liệu tổng hợp `kbN/` (chats.jsonl + context.md), dựng phong bì bridge giả,
đọc `brain_contract` của L1, tính Wilson. KHÔNG có đáp án ở đây — đáp án luôn do người chạy chỉ đường dẫn (`--key` /
`GH_EVAL_KEY`) và nằm NGOÀI repo (spec §2.1).

Định dạng dữ liệu (đọc từ `research/eval-tinh-bao/v2/kb*/`): mỗi dòng `chats.jsonl` = {id, group, group_name, ts (ISO, +07:00),
sender_id, sender_name, text, reply_to, kind?}; `kind` ∈ text (mặc định) | voice_transcript | image_caption | forward | link | system."""

from __future__ import annotations

import importlib.util
import json
import math
import os
import re
import sys
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
VN_TZ = timezone(timedelta(hours=7))


def api_dir() -> Path:
    return Path(os.environ.get("GH_API_DIR", REPO / "apps" / "api"))


def brain_contract() -> Any:
    """Nạp `gh.refinery.brain_contract` (L1) — hợp đồng duy nhất mà bộ chấm đọc (spec §4F)."""
    d = str(api_dir())
    if d not in sys.path:
        sys.path.insert(0, d)
    from gh.refinery import brain_contract as bc  # noqa: PLC0415

    return bc


def fake_bridge() -> Any:
    """Nạp `apps/web/e2e-live/fake_bridge.py` theo đường dẫn — một khuôn phong bì duy nhất cho bridge giả và `replay.py`."""
    path = REPO / "apps" / "web" / "e2e-live" / "fake_bridge.py"
    spec = importlib.util.spec_from_file_location("gh_fake_bridge", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("gh_fake_bridge", mod)
    spec.loader.exec_module(mod)
    return mod


# ─── Dữ liệu ─────────────────────────────────────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Msg:
    id: str
    group: str
    group_name: str
    ts: datetime
    sender_id: str
    sender_name: str
    text: str
    reply_to: str | None = None
    kind: str = "text"

    @property
    def thread_key(self) -> str:
        return self.group


@dataclass
class Dataset:
    name: str
    msgs: list[Msg]
    context_md: str = ""
    groups: dict[str, str] = field(default_factory=dict)

    def by_id(self) -> dict[str, Msg]:
        return {m.id: m for m in self.msgs}


def parse_ts(s: str) -> datetime:
    d = datetime.fromisoformat(s.replace("Z", "+00:00"))
    return d if d.tzinfo else d.replace(tzinfo=VN_TZ)


def load_chats(path: str | Path) -> list[Msg]:
    out: list[Msg] = []
    with open(path, encoding="utf-8") as f:
        for n, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
                out.append(Msg(id=str(r["id"]), group=str(r["group"]), group_name=str(r.get("group_name") or r["group"]),
                               ts=parse_ts(r["ts"]), sender_id=str(r["sender_id"]), sender_name=str(r.get("sender_name") or r["sender_id"]),
                               text=str(r.get("text") or ""), reply_to=(str(r["reply_to"]) if r.get("reply_to") else None),
                               kind=str(r.get("kind") or "text")))
            except (KeyError, ValueError, TypeError) as e:
                raise ValueError(f"{path}:{n}: dòng chats.jsonl sai khuôn ({e})") from e
    out.sort(key=lambda m: (m.ts, m.id))        # ổn định: cùng phút giữ thứ tự id
    return out


def load_dataset(directory: str | Path) -> Dataset:
    d = Path(directory)
    msgs = load_chats(d / "chats.jsonl")
    ctx = (d / "context.md").read_text(encoding="utf-8") if (d / "context.md").exists() else ""
    return Dataset(name=d.name, msgs=msgs, context_md=ctx, groups={m.group: m.group_name for m in msgs})


# ─── Bối cảnh + Hồ sơ Sếp từ context.md ───────────────────────────────────────────────────────────────────────────────────

CONTEXT_CODE_BY_LABEL = {"gia đình": "gia_dinh", "bạn bè": "ban_be", "công việc": "cong_viec", "học tập": "hoc_tap",
                         "nhân viên": "nhan_vien", "giao thương": "giao_thuong", "kinh doanh": "kinh_doanh", "khác": "khac"}
PROFILE_PREDICATE_BY_HEADING = (("mục tiêu", "owner_goal"), ("nguồn lực", "owner_resource"), ("vai trò", "owner_key_relation"),
                                ("giá trị", "owner_value_limit"), ("lịch", "owner_rhythm"), ("cách báo", "owner_report_pref"))


def _fold(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s.lower()) if unicodedata.category(c) != "Mn").replace("đ", "d")


def parse_context(md: str) -> dict[str, Any]:
    """Rút từ `context.md`: `groups` = {Gk: nhãn bối cảnh} (chỉ khi có bảng nhóm có cột bối cảnh, như kb4), `profile` = {vị từ: [dòng]}
    (mục "Hồ sơ Sếp", chia theo dòng in đậm), `today` (nếu có "Hôm nay" = …). Không có thì trả rỗng — bộ kb1–kb3 không có bảng
    nhãn nhóm: người chạy đưa `--contexts contexts.json` (Gk → mã bối cảnh)."""
    groups: dict[str, str] = {}
    for m in re.finditer(r"^\|\s*(G\d+)\s*\|[^|]*\|\s*([^|]+?)\s*\|", md, re.M):
        code = CONTEXT_CODE_BY_LABEL.get(m.group(2).strip().lower())
        if code:
            groups[m.group(1)] = code
    profile: dict[str, list[str]] = {}
    in_profile = False
    pred: str | None = None
    for line in md.splitlines():
        if line.startswith("## "):
            in_profile = "hồ sơ" in line.lower() and "sếp" in line.lower()
            pred = None
            continue
        if not in_profile:
            continue
        mh = re.match(r"^\*\*(.+?)\*\*\s*$", line.strip())
        if mh:
            h = _fold(mh.group(1))
            pred = next((p for k, p in PROFILE_PREDICATE_BY_HEADING if _fold(k) in h or (k == "cách báo" and "bao" in h and "thich" in h)), None)
            continue
        mi = re.match(r"^\s*(?:\d+\.|[-*])\s+(.*\S)\s*$", line)
        if mi and pred:
            profile.setdefault(pred, []).append(re.sub(r"\*\*", "", mi.group(1)))
    today = re.search(r"Hôm nay\"?\s*=\s*([^.\n]+?)(?:\.|\n|$)", md)
    return {"groups": groups, "profile": profile, "today": today.group(1).strip() if today else None}


# ─── Phong bì bridge giả ────────────────────────────────────────────────────────────────────────────────────────────────

KIND_TO_EVENT = {"voice_transcript": "voice", "image_caption": "image"}


def strip_media_prefix(text: str) -> str:
    return re.sub(r"^\[(?:ảnh|anh|image|voice|thoại)\]\s*", "", text, flags=re.I)


def to_script_event(m: Msg, *, ts: datetime | None = None, channel: str | None = None) -> dict[str, Any]:
    """Tin của bộ dữ liệu → sự kiện kịch bản của `fake_bridge` (voice/ảnh đi qua `media_ref` + chữ cho engine media giả).
    Mọi nhóm đi kênh `zalo` (kể cả nhóm Facebook của kb1/kb4: kho thô chỉ cần một kênh có sẵn) trừ khi `channel` ép khác."""
    typ = KIND_TO_EVENT.get(m.kind, "group_msg")
    ev: dict[str, Any] = {"type": typ, "id": m.id, "group": m.group, "group_name": m.group_name, "sender": m.sender_id,
                          "name": m.sender_name, "ts": (ts or m.ts).isoformat(), "channel": channel or "zalo"}
    if m.reply_to:
        ev["reply_to"] = m.reply_to
    if typ == "voice":
        ev["transcript"] = m.text
        ev["duration_s"] = max(3, min(120, len(m.text) // 12))
    elif typ == "image":
        ev["caption"] = None
        ev["media_text"] = strip_media_prefix(m.text)
    else:
        ev["text"] = m.text
        if m.kind in ("forward", "link", "system"):
            ev["dataset_kind"] = m.kind
        if m.kind == "forward":
            ev["forwarded"] = None      # §4E: Zalo không biết cờ chuyển tiếp ⇒ null (= không biết), không phải false
    return ev


# ─── Thống kê ───────────────────────────────────────────────────────────────────────────────────────────────────────────────

def wilson(k: int, n: int, z: float = 1.96) -> dict[str, float | int | None]:
    """Khoảng Wilson 95% (spec §2.1/§2.2). n = 0 ⇒ p/lo/hi = None."""
    if n <= 0:
        return {"k": k, "n": n, "p": None, "lo95": None, "hi95": None}
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return {"k": k, "n": n, "p": p, "lo95": max(0.0, c - h), "hi95": min(1.0, c + h)}


def quantiles(xs: list[float]) -> dict[str, float | None]:
    if not xs:
        return {"n": 0, "min": None, "p25": None, "median": None, "p75": None, "max": None, "mean": None}
    s = sorted(xs)

    def q(f: float) -> float:
        i = (len(s) - 1) * f
        lo, hi = math.floor(i), math.ceil(i)
        return s[lo] + (s[hi] - s[lo]) * (i - lo)

    return {"n": len(s), "min": s[0], "p25": q(0.25), "median": q(0.5), "p75": q(0.75), "max": s[-1], "mean": sum(s) / len(s)}


def write_json(path: str | Path, obj: Any) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(obj, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


def refuse_inside_repo(path: str | os.PathLike[str]) -> None:
    """Kết quả chấm (gate*.json, phiếu rà tay…) có thể chứa dấu vết đáp án ⇒ KHÔNG được ghi vào trong repo (spec §2.1)."""
    p = Path(path).resolve()
    if REPO == p or REPO in p.parents:
        raise SystemExit(f"từ chối ghi {p}: nằm TRONG repo (kết quả chấm/đáp án phải ở ngoài repo)")
