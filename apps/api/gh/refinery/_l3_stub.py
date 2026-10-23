# ruff: noqa: E501
"""STUB của gói L3 (`extract.analyze_group`, `verify.check`) — dùng trong lúc L3 chưa merge (spec §11 L2, "stub sinh từ brain_contract").

ĐIỂM CẮM L3 — DUY NHẤT: `gh/refinery/runner.py` có đúng MỘT dòng import `from gh.refinery import _l3_stub as l3` (đánh dấu `# L3-PLUG`).
Sau khi L3 merge, thay NỘI DUNG file này bằng bản re-export (giữ tên file để runner không đổi) HOẶC đổi dòng import đó:

    from gh.refinery.extract import (EXTRACTOR_VERSION, Analysis, ContractError, GroupEvent, GroupWindow, ModelReply,
                                     analyze_group_detailed)
    from gh.refinery.verify import MediaText, Verified, VerifiedFact, VerifiedQuote, VerifiedUnit, check
    IS_STUB = False

Hình dạng các kiểu dưới đây chép đúng bản L3 đang làm (`extract.GroupEvent/GroupWindow/Analysis`, `verify.MediaText/Verified*`) để chỉ
cần đổi nguồn import. Stub KHÔNG gọi model (0 token, 0 ₫): `analyze_group_detailed` trả `BrainOut` rỗng; `check` kiểm trích dẫn bằng
phép "chuỗi con của đúng tin" (đủ cho test L2: sai ⇒ `absent`). Runner tự bật đường theo luồng khi `IS_STUB` là False (hoặc khi tổ chức
đặt `settings.intel.group_pipeline = 'on'`); test tiêm `analyzer`/`verifier` giả qua `Refinery(..., l3=…)`.
"""

import re
import unicodedata
import uuid
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from gh.biz.mem import contexts
from gh.biz.mem.profile_read import ProfileSnapshot
from gh.biz.mem.retrieve import ContextPack
from gh.providers.clients import Message
from gh.refinery.brain_contract import BrainOut, FactProposed, MemoryUpdate, Unit

IS_STUB = True
EXTRACTOR_VERSION = "stub-0"
DEFAULT_TZ = "Asia/Ho_Chi_Minh"


@dataclass
class GroupEvent:
    message_id: str
    sent_at: datetime
    body: str | None = None
    sender_person_id: uuid.UUID | None = None
    sender_name: str | None = None
    kind: str = "text"
    reply_to: str | None = None
    forwarded: bool = False
    raw_event_id: uuid.UUID | None = None
    received_at: datetime | None = None
    context: bool = False

    @property
    def person_ref(self) -> str | None:
        return f"u_{self.sender_person_id}" if self.sender_person_id is not None else None


@dataclass(frozen=True)
class MediaText:
    message_id: str
    kind: str
    text: str
    status: str = "ok"


@dataclass
class GroupWindow:
    thread_ref: str
    new_events: list[GroupEvent]
    context_events: list[GroupEvent] = field(default_factory=list)
    media_texts: Mapping[str, Sequence[MediaText]] = field(default_factory=dict)
    unreadable: Mapping[str, int] = field(default_factory=lambda: {"voice": 0, "image": 0, "file": 0})
    now: datetime | None = None
    tz: str = DEFAULT_TZ
    group_name: str | None = None
    predicates: Sequence[tuple[str, str]] = ()

    @property
    def all_events(self) -> list[GroupEvent]:
        return [*self.context_events, *self.new_events]


@dataclass
class ModelReply:
    text: str
    model: str = ""
    tokens_in: int | None = None
    tokens_out: int | None = None


ModelCall = Callable[[list[Message]], Awaitable[ModelReply]]


class ContractError(Exception):
    def __init__(self, errors: list[str], *, model: str = "", tokens_in: int | None = None, tokens_out: int | None = None):
        super().__init__("contract_error: " + "; ".join(errors[:5]))
        self.errors, self.model, self.tokens_in, self.tokens_out = errors, model, tokens_in, tokens_out


@dataclass
class Analysis:
    out: BrainOut
    model: str
    tokens_in: int | None
    tokens_out: int | None
    attempts: int
    prompt_chars: int


async def analyze_group_detailed(window: GroupWindow, pack: ContextPack, profile: ProfileSnapshot, policy: contexts.Policy, *,
                                 call: ModelCall | None = None, secrets: Sequence[str] = ()) -> Analysis:
    """Stub: không gọi model, trả đầu ra hợp lệ KHÔNG có đơn vị nào."""
    return Analysis(BrainOut(thread_ref=window.thread_ref), "stub", None, None, 0, 0)


# ─── verify (rút gọn: chỉ kiểm trích dẫn) ───────────────────────────────────────────────────────────────────────────────

@dataclass
class VerifiedQuote:
    message_id: str
    field: str
    text: str
    status: str                              # verified | absent
    span_start: int | None = None
    span_end: int | None = None
    sent_at: datetime | None = None
    raw_event_id: uuid.UUID | None = None
    sender_person_id: uuid.UUID | None = None
    reason: str | None = None
    via_scrub: bool = False

    @property
    def span(self) -> dict[str, Any] | None:
        if self.status != "verified" or self.span_start is None or self.span_end is None:
            return None
        return {"source_field": self.field, "span_start": self.span_start, "span_end": self.span_end}


@dataclass
class VerifiedUnit:
    unit: Unit
    quotes: list[VerifiedQuote]
    status: str
    msg_at: datetime | None = None
    event_at: datetime | None = None
    notes: list[str] = field(default_factory=list)
    projects_signal: bool = True

    @property
    def verified_quotes(self) -> list[VerifiedQuote]:
        return [q for q in self.quotes if q.status == "verified"]

    @property
    def absent(self) -> bool:
        return self.status == "absent"


@dataclass
class VerifiedFact:
    fact: FactProposed
    unit_ref: str
    evidence: list[VerifiedQuote]
    speaker_person_id: uuid.UUID | None = None


@dataclass
class Verified:
    out: BrainOut
    units: list[VerifiedUnit] = field(default_factory=list)
    facts: list[VerifiedFact] = field(default_factory=list)
    memory_updates: list[MemoryUpdate] = field(default_factory=list)
    counters: dict[str, int] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    @property
    def usable_units(self) -> list[VerifiedUnit]:
        return [u for u in self.units if u.status == "ok"]

    @property
    def signal_units(self) -> list[VerifiedUnit]:
        return [u for u in self.usable_units if u.projects_signal]

    def bump(self, key: str, n: int = 1) -> None:
        self.counters[key] = self.counters.get(key, 0) + n


def _norm(s: str | None) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", s or "")).strip()


def check(out: BrainOut, events: Sequence[Any], media_texts: Mapping[str, Sequence[Any]] | Iterable[Any] | None = None, *,
          now: datetime | None = None, tz: str = DEFAULT_TZ, thread_ref: str | None = None,
          profile_fact_ids: Any = None, known_person_refs: Any = None, known_predicates: Any = None) -> Verified:
    """Stub: mỗi `quotes[].text` phải là chuỗi con (sau chuẩn hoá hẹp) của ĐÚNG tin `message_id`; `field='body'` kiểm với thân tin,
    `transcript|ocr|caption` kiểm với `media_texts[message_id]`. Không khớp ⇒ `absent` ⇒ đơn vị `absent`."""
    res = Verified(out=out)
    if thread_ref is not None and out.thread_ref != thread_ref:
        res.errors.append("thread_ref_mismatch")
        return res
    by_id = {str(getattr(e, "message_id", None)): e for e in events}
    media: dict[str, list[Any]] = {}
    if isinstance(media_texts, Mapping):
        for k, v in media_texts.items():
            media.setdefault(str(k), []).extend(v)
    elif media_texts is not None:
        for mt in media_texts:
            media.setdefault(str(getattr(mt, "message_id", "")), []).append(mt)
    by_unit: dict[str, VerifiedUnit] = {}
    for u in out.units:
        vqs: list[VerifiedQuote] = []
        for q in u.quotes:
            ev = by_id.get(q.message_id)
            vq = VerifiedQuote(q.message_id, q.field, q.text, "absent", reason="message_unknown" if ev is None else "not_substring")
            if ev is not None:
                if q.field == "body":
                    src = ev.body or ""
                else:
                    kind = {"transcript": "transcript", "ocr": "ocr", "caption": "description"}[q.field]
                    src = "\n".join(m.text for m in media.get(q.message_id, []) if m.kind == kind and m.status == "ok")
                i = src.find(q.text)
                if i < 0 and _norm(q.text) and _norm(q.text) in _norm(src):
                    i = 0                                                      # khớp qua chuẩn hoá khoảng trắng: span thô
                if i >= 0:
                    vq.status, vq.reason, vq.span_start, vq.span_end = "verified", None, i, i + len(q.text)
                    vq.sent_at, vq.raw_event_id, vq.sender_person_id = ev.sent_at, ev.raw_event_id, ev.sender_person_id
            vqs.append(vq)
        ok = all(v.status == "verified" for v in vqs)
        sent = [v.sent_at for v in vqs if v.sent_at is not None]
        vu = VerifiedUnit(u, vqs, "ok" if ok else "absent", msg_at=max(sent) if (ok and sent) else None,
                          projects_signal=u.kind is not None, notes=list(u.normalization_notes))
        vu.event_at = vu.msg_at
        if not ok:
            res.bump("absent_units")
        res.units.append(vu)
        by_unit[u.unit_ref] = vu
    for f in out.facts_proposed:
        vun = by_unit.get(f.unit_ref)
        if vun is None or vun.status != "ok":
            res.bump("fact_dropped_no_unit")
            continue
        ev_q = [q for q in vun.verified_quotes if q.message_id in f.quote_refs]
        if not ev_q:
            res.bump("fact_dropped_no_quote")
            continue
        res.facts.append(VerifiedFact(f, f.unit_ref, ev_q, ev_q[0].sender_person_id))
    res.memory_updates = list(out.memory_updates)
    _ = (now, tz, profile_fact_ids, known_person_refs, known_predicates)
    return res
