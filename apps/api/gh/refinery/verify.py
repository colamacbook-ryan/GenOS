# ruff: noqa: E501
"""Tầng (c) — CODE kiểm đầu ra bộ não (spec §4D.2 (c), §4F "Luật kiểm", §4F.1 #1–#12). Không gọi model, không I/O.

`check(out, events, media_texts, *, now, tz, ...) -> Verified`:
- mỗi `quotes[].text` phải là chuỗi con của ĐÚNG tin `message_id`, đúng trường (`body`/`caption` ⇒ thân tin; `transcript`/`ocr` ⇒
  `clean.media_texts` loại tương ứng) sau chuẩn hoá HẸP (NFC, gộp khoảng trắng, bỏ khoảng trắng hai đầu; phân biệt hoa/thường và dấu).
  Ghi `quote_status` (`verified`|`absent`) + span (tính trên chuỗi đã chuẩn hoá). Đơn vị có ≥ 1 trích dẫn `absent` ⇒ đơn vị `absent`:
  bị loại khỏi tín hiệu và sự thật (QUYẾT ĐỊNH L3: nghiêm — một trích dẫn bịa đủ để loại cả đơn vị; G-KB đếm `absent` để chỉnh).
- tính MỐC bằng code: `msg_at`, `event_at` (`vi_time.parse_past(...).latest`), `deadline_at` (`vi_time.parse`), `ongoing`, `money_vnd`,
  `status`/`txn` (người gửi + giờ gửi tin chứng minh), `delivery_form`, `explicit_urgent` (kiểm bằng `URGENT_LEXICON`).
- lọc: `owner_relevance.fact_id` ngoài khối Hồ sơ đã gửi ⇒ bỏ mục (giữ đơn vị); `person_ref` lạ ⇒ chỉ dùng `surface`;
  `predicate` lạ ⇒ bỏ sự thật, đếm `unknown_predicate`; sự thật của đơn vị `absent` ⇒ bỏ.
Đầu vào "events" là đối tượng/dict có: `message_id`, `sent_at`, `body` (hoặc `text`), `sender_person_id`, `forwarded`, `raw_event_id`
(xem `extract.GroupEvent`); `media_texts` là {message_id: [MediaText]} hoặc danh sách phẳng có `message_id`, `kind`, `text`.
"""

from __future__ import annotations

import re
import unicodedata
import uuid
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from gh.biz.mem import vi_time
from gh.chassis import masking as _masking
from gh.refinery.brain_contract import BrainOut, FactProposed, MemoryUpdate, Unit

DEFAULT_TZ = vi_time.DEFAULT_TZ
ONGOING_WINDOW = timedelta(hours=48)
TXN_JUST_DONE = timedelta(hours=24)
#: `quotes[].field` → loại `clean.media_texts` (body/caption nằm ở thân tin).
FIELD_TO_MEDIA_KIND = {"transcript": "transcript", "ocr": "ocr"}


# ─── đầu vào ───────────────────────────────────────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class MediaText:
    message_id: str
    kind: str                    # transcript | ocr | description
    text: str
    status: str = "ok"


def _g(o: Any, name: str, default: Any = None) -> Any:
    if isinstance(o, Mapping):
        return o.get(name, default)
    return getattr(o, name, default)


def norm(s: str | None) -> str:
    """Chuẩn hoá HẸP để so trích dẫn: NFC, gộp mọi khoảng trắng thành một dấu cách, bỏ khoảng trắng hai đầu."""
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", s or "")).strip()


def _event_body(ev: Any) -> str:
    return str(_g(ev, "body") if _g(ev, "body") is not None else (_g(ev, "text") or ""))


def _index_media(media_texts: Mapping[str, Sequence[Any]] | Iterable[Any] | None) -> dict[str, list[Any]]:
    out: dict[str, list[Any]] = {}
    if media_texts is None:
        return out
    if isinstance(media_texts, Mapping):
        for mid, items in media_texts.items():
            out.setdefault(str(mid), []).extend(items)
        return out
    for it in media_texts:
        out.setdefault(str(_g(it, "message_id")), []).append(it)
    return out


# ─── kết quả ────────────────────────────────────────────────────────────────────────────────────────────────────────────
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
    reason: str | None = None                # message_unknown | field_missing | not_substring
    via_scrub: bool = False                  # khớp qua bản đã che thông tin đăng nhập (trích dẫn chứa nhãn)

    @property
    def span(self) -> dict[str, Any] | None:
        if self.status != "verified" or self.span_start is None or self.span_end is None:
            return None
        return {"source_field": self.field, "span_start": self.span_start, "span_end": self.span_end}


@dataclass
class VerifiedUnit:
    unit: Unit                               # đã lọc (person_ref lạ, owner_relevance ngoài Hồ sơ, cờ ongoing/urgent đã kiểm)
    quotes: list[VerifiedQuote]
    status: str                              # ok | absent
    msg_at: datetime | None = None           # sent_at MỚI NHẤT trong trích dẫn verified (CNS-137)
    event_at: datetime | None = None
    event_time_unparsed: bool = False
    event_granularity: str | None = None
    deadline_at: datetime | None = None
    deadline_lunar: bool = False
    deadline_ambiguous: bool = False
    deadline_unparsed: bool = False
    ongoing: bool = False
    explicit_urgent: bool = False
    money_vnd: int | None = None
    money_basis: str | None = None           # amount_text | harm.amount_text
    status_by_person_id: uuid.UUID | None = None
    status_at: datetime | None = None
    txn_state: str | None = None             # pending | just_done | done_cannot_stop
    txn_at: datetime | None = None
    delivery_form: str = "direct"            # direct | warning_story | forwarded
    owner_relevance_kept: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    projects_signal: bool = True             # kind != null ⇒ chiếu tín hiệu; null ⇒ chỉ ghi sự thật/ghi nhớ

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
    evidence: list[VerifiedQuote]            # mỗi quote_ref còn hợp lệ ↔ trích dẫn verified của đơn vị
    speaker_person_id: uuid.UUID | None = None


@dataclass
class Verified:
    out: BrainOut
    units: list[VerifiedUnit] = field(default_factory=list)      # theo thứ tự đầu ra; gồm cả đơn vị absent (status="absent")
    facts: list[VerifiedFact] = field(default_factory=list)
    memory_updates: list[MemoryUpdate] = field(default_factory=list)
    counters: dict[str, int] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)              # mã lỗi, KHÔNG nội dung

    @property
    def usable_units(self) -> list[VerifiedUnit]:
        return [u for u in self.units if u.status == "ok"]

    @property
    def signal_units(self) -> list[VerifiedUnit]:
        return [u for u in self.usable_units if u.projects_signal]

    def bump(self, key: str, n: int = 1) -> None:
        self.counters[key] = self.counters.get(key, 0) + n


# ─── số tiền (code parse, §4D.2 (c)) ──────────────────────────────────────────────────────────────────────────────────────
_UNITS_VND = {"ty": 1_000_000_000, "ti": 1_000_000_000, "trieu": 1_000_000, "tr": 1_000_000, "cu": 1_000_000, "nghin": 1_000,
              "ngan": 1_000, "k": 1_000}
_MONEY_RE = re.compile(
    r"(?<![\w.,])(?P<num>\d{1,3}(?:[.,]\d{3})+|\d+(?:[.,]\d+)?)\s*(?P<unit>ty|ti|trieu|tr|cu|nghin|ngan|k)(?![a-z])(?:\s*(?P<half>ruoi)|(?P<tail0>\d{1,3})|\s+(?P<tail1>\d{3}))?(?!\d)")
_PLAIN_RE = re.compile(r"(?<![\w.,])(?P<num>\d{1,3}(?:[.,]\d{3})+|\d{4,})\s*(?:dong|vnd|vnđ|d|đ)\b")


def _fold(s: str) -> str:
    from gh.textnorm import strip_accents
    return strip_accents(norm(s).lower())


def _to_float(raw: str) -> float:
    if re.fullmatch(r"\d{1,3}(?:[.,]\d{3})+", raw):
        return float(re.sub(r"[.,]", "", raw))
    return float(raw.replace(",", "."))


def parse_money_vnd(text: str | None) -> int | None:
    """"50 triệu" ⇒ 50_000_000; "1,2 tỷ" ⇒ 1_200_000_000; "3tr5" ⇒ 3_500_000; "800k" ⇒ 800_000; "25.000.000đ" ⇒ 25_000_000.
    Không có số tiền đọc được ⇒ None (không đoán)."""
    if not text:
        return None
    f = _fold(text)
    m = _MONEY_RE.search(f)
    if m:
        unit = _UNITS_VND[m.group("unit")]
        val = _to_float(m.group("num")) * unit
        if m.group("half"):
            val += unit / 2
        elif m.group("tail0") or m.group("tail1"):
            tail = m.group("tail0") or m.group("tail1")
            val += int(tail) / (10 ** len(tail)) * unit
        return int(round(val))
    m = _PLAIN_RE.search(f)
    if m:
        return int(_to_float(m.group("num")))
    return None


# ─── NGOẠI LỆ Q9 duy nhất: thông tin ĐĂNG NHẬP không tới model (Gen duyệt, review bảo mật 23/10) ─────────────────────────────
# Q9 = gửi model chat ĐẦY ĐỦ (tên, SĐT, số tiền, STK nguyên văn vì code cần STK để so lịch sử). Riêng mã OTP, mật khẩu, số thẻ đầy đủ
# (13–19 số, qua Luhn) và API key/token bị thay bằng NHÃN giữ được nghĩa ("[OTP 6 số]", "[mật khẩu]", "[thẻ …1234]", "[token]") để model
# vẫn biết "đã gửi OTP" mà không thấy giá trị. THỨ TỰ BẮT BUỘC: luật (a) (`credentials_entered`, `hard_rule_hit`) chạy trên bản GỐC của tin
# TRƯỚC khi che; che chỉ xảy ra lúc dựng prompt (không sửa tin gốc). Trích dẫn của model có thể chứa nhãn ⇒ `_quote_status` thử thêm
# bản đã che của tin (cùng hàm này) và ánh xạ span về chữ gốc.
_CARD_RE = re.compile(r"(?<![\d-])\d(?:[ -]?\d){12,18}(?!\d)")
_STK_CTX_RE = re.compile(r"(?:stk|so tk|tai khoan|chuyen khoan|\bck\b|ngan hang|so tai khoan)\s*[:\-]?\s*$")


def _luhn_ok(digits: str) -> bool:
    total, alt = 0, False
    for ch in reversed(digits):
        d = int(ch)
        if alt:
            d = d * 2 - 9 if d * 2 > 9 else d * 2
        total += d
        alt = not alt
    return total % 10 == 0


def _credential_spans(text_: str) -> list[tuple[int, int, str]]:
    m_ = _masking
    spans: list[tuple[int, int, str]] = []

    def add(m: re.Match[str], grp: int, label: str) -> None:
        if m.start(grp) >= 0 and m.end(grp) > m.start(grp):
            spans.append((m.start(grp), m.end(grp), label))

    def otp_label(code: str) -> str:
        n = len(re.sub(r"\D", "", code))
        return f"[OTP {n} số]"

    for rx in (m_._RE_OTP_AFTER, m_._RE_OTP_GAP, m_._RE_OTP_BEFORE, m_._RE_OTP_BEFORE_CODE):
        for m in rx.finditer(text_):
            add(m, 2, otp_label(m.group(2)))
    for m in m_._RE_OTP_BARE.finditer(text_):
        tail = re.search(r"\d{3}[ -]\d{3}$|\d+$", m.group(0))
        if tail:
            a, b = m.start() + tail.start(), m.start() + tail.end()
            spans.append((a, b, otp_label(m.group(0)[tail.start():tail.end()])))
    for rx, need_mark in ((m_._RE_PW_EXPLICIT, False), (m_._RE_PW_IS, True)):
        for m in rx.finditer(text_):
            val = m.group(2)
            core = val.rstrip(".,;!?)")
            if len(core) >= 4 and not (need_mark and all(c.isalpha() for c in core)):
                spans.append((m.start(2), m.start(2) + len(core), "[mật khẩu]"))
    for rx in (m_._RE_URL_PARAM, m_._RE_URL_PATH):
        for m in rx.finditer(text_):
            add(m, 2, "[token]")
    for m in m_._RE_SECRET.finditer(text_):
        spans.append((m.start(), m.end(), "[token]"))
    for m in _CARD_RE.finditer(text_):
        digits = re.sub(r"\D", "", m.group(0))
        if 13 <= len(digits) <= 19 and _luhn_ok(digits):
            if _STK_CTX_RE.search(_fold(text_[max(0, m.start() - 24):m.start()])):
                continue                      # "STK 1234 …": số tài khoản ngân hàng, Q9 giữ nguyên để so lịch sử
            spans.append((m.start(), m.end(), f"[thẻ …{digits[-4:]}]"))
    spans.sort(key=lambda t: (t[0], -(t[1] - t[0])))
    out: list[tuple[int, int, str]] = []
    for a, b, lab in spans:
        if out and a < out[-1][1]:
            continue
        out.append((a, b, lab))
    return out


def scrub_credentials_map(text_: str) -> tuple[str, list[tuple[int, int, int, int]]]:
    """(chữ đã che, [(đầu_mới, cuối_mới, đầu_cũ, cuối_cũ)]) — để ánh xạ span về chữ gốc."""
    pieces: list[str] = []
    segs: list[tuple[int, int, int, int]] = []
    pos_old, new_len = 0, 0
    for a, b, lab in _credential_spans(text_):
        pieces.append(text_[pos_old:a])
        new_len += a - pos_old
        segs.append((new_len, new_len + len(lab), a, b))
        pieces.append(lab)
        new_len += len(lab)
        pos_old = b
    pieces.append(text_[pos_old:])
    return "".join(pieces), segs


def scrub_credentials(text_: str | None) -> str | None:
    """Thay OTP / mật khẩu / số thẻ / token bằng nhãn giữ nghĩa; mọi thứ khác (tên, SĐT, tiền, STK) giữ NGUYÊN (Q9)."""
    return None if text_ is None else scrub_credentials_map(text_)[0]


def _map_back(segs: list[tuple[int, int, int, int]], new_pos: int, *, end: bool) -> int:
    delta = 0
    for ns, ne, os_, oe in segs:
        if end:
            if ns < new_pos <= ne:
                return oe
            if new_pos <= ns:
                break
        else:
            if ns <= new_pos < ne:
                return os_
            if new_pos < ns:
                break
        delta = oe - ne
    return new_pos + delta


# ─── kiểm trích dẫn ─────────────────────────────────────────────────────────────────────────────────────────────────────────
def _quote_status(q: Any, ev_by_id: Mapping[str, Any], media: Mapping[str, list[Any]]) -> VerifiedQuote:
    mid, fld, text = q.message_id, q.field, norm(q.text)
    ev = ev_by_id.get(mid)
    base = VerifiedQuote(mid, fld, q.text, "absent")
    if ev is None:
        base.reason = "message_unknown"
        return base
    base.sent_at = _g(ev, "sent_at")
    base.raw_event_id = _g(ev, "raw_event_id")
    base.sender_person_id = _g(ev, "sender_person_id")
    if fld in ("body", "caption"):
        haystacks = [_event_body(ev)]
    else:
        kind = FIELD_TO_MEDIA_KIND.get(fld, fld)
        haystacks = [str(_g(m, "text") or "") for m in media.get(mid, []) if _g(m, "kind") == kind and _g(m, "status", "ok") == "ok"]
        if fld == "caption":
            haystacks += [str(_g(m, "text") or "") for m in media.get(mid, []) if _g(m, "kind") == "description"]
    if not haystacks or not any(h.strip() for h in haystacks):
        base.reason = "field_missing"
        return base
    if not text:
        base.reason = "not_substring"
        return base
    for h in haystacks:
        hn = norm(h)
        i = hn.find(text)
        if i >= 0:
            base.status, base.span_start, base.span_end = "verified", i, i + len(text)
            return base
    for h in haystacks:                     # trích dẫn có thể chứa nhãn che đăng nhập ("[OTP 6 số]"): so với bản đã che, span ánh xạ về chữ gốc
        hn = norm(h)
        sn, segs = scrub_credentials_map(hn)
        if segs and (i := sn.find(text)) >= 0:
            base.status, base.span_start, base.span_end = "verified", _map_back(segs, i, end=False), _map_back(segs, i + len(text), end=True)
            base.via_scrub = True
            return base
    base.reason = "not_substring"
    return base


def _message_text(ev: Any, media: Mapping[str, list[Any]]) -> str:
    parts = [_event_body(ev)] + [str(_g(m, "text") or "") for m in media.get(str(_g(ev, "message_id")), [])]
    return "\n".join(p for p in parts if p)


def _tzinfo(tz: str | Any) -> Any:
    from zoneinfo import ZoneInfo
    return ZoneInfo(tz) if isinstance(tz, str) else tz


def _as_ref(x: str) -> str:
    return x if x.startswith("f_") else f"f_{x}"


# ─── hàm chính ─────────────────────────────────────────────────────────────────────────────────────────────────────────────
def check(out: BrainOut, events: Sequence[Any], media_texts: Mapping[str, Sequence[Any]] | Iterable[Any] | None = None, *,
          now: datetime | None = None, tz: str = DEFAULT_TZ, thread_ref: str | None = None,
          profile_fact_ids: Collection[Any] | None = None, known_person_refs: Collection[str] | None = None,
          known_predicates: Collection[str] | None = None) -> Verified:
    """Kiểm một `BrainOut` với tin của lượt (gồm cả cửa sổ gần). `profile_fact_ids` = id sự thật có trong khối Hồ sơ đã gửi cho model
    (`ProfileSnapshot.fact_ids()` hoặc chuỗi `f_…`); `None` ⇒ không có Hồ sơ nào được gửi ⇒ mọi `owner_relevance` bị bỏ. `known_person_refs`
    / `known_predicates` `None` ⇒ không lọc. `now` mặc định giờ máy (L2 truyền `clock.now()` để đồng hồ giả chạy được)."""
    now = (now or datetime.now(UTC))
    zone = _tzinfo(tz)
    res = Verified(out=out)
    if thread_ref is not None and out.thread_ref != thread_ref:
        res.errors.append("thread_ref_mismatch")
        res.bump("thread_ref_mismatch")
        return res
    ev_by_id = {str(_g(e, "message_id")): e for e in events}
    media = _index_media(media_texts)
    prof_ids = {_as_ref(str(i)) for i in (profile_fact_ids or [])}
    seen_refs: set[str] = set()
    unit_by_ref: dict[str, VerifiedUnit] = {}
    kind_by_event: set[tuple[str, str]] = set()
    for u in out.units:
        notes: list[str] = list(u.normalization_notes)
        if u.unit_ref in seen_refs:
            res.errors.append("duplicate_unit_ref")
            res.bump("duplicate_unit_ref")
            continue
        seen_refs.add(u.unit_ref)
        vq = [_quote_status(q, ev_by_id, media) for q in u.quotes]
        vu = VerifiedUnit(unit=u, quotes=vq, status="ok", notes=notes, projects_signal=u.kind is not None)
        if any(q.status == "absent" for q in vq):
            vu.status = "absent"
            res.bump("absent_units")
            res.bump("absent_quotes", sum(1 for q in vq if q.status == "absent"))
            for q in vq:
                if q.status == "absent" and q.reason:
                    res.bump(f"quote_{q.reason}")
            res.units.append(vu)
            continue
        if u.kind is not None:
            key = (u.event_ref, u.kind)
            if key in kind_by_event:
                notes.append("duplicate_kind_for_event")
                res.bump("duplicate_kind_for_event")
            kind_by_event.add(key)
        _verify_unit(res, vu, ev_by_id, media, now, zone, tz, prof_ids, known_person_refs)
        unit_by_ref[u.unit_ref] = vu
        res.units.append(vu)
    _verify_facts(res, out.facts_proposed, unit_by_ref, ev_by_id, known_predicates)
    for mu in out.memory_updates:
        ids = [i for i in mu.message_ids if i in ev_by_id]
        if len(ids) != len(mu.message_ids):
            res.bump("memory_update_message_dropped", len(mu.message_ids) - len(ids))
        res.memory_updates.append(mu.model_copy(update={"message_ids": ids}))
    return res


def _verify_unit(res: Verified, vu: VerifiedUnit, ev_by_id: Mapping[str, Any], media: Mapping[str, list[Any]], now: datetime,
                 zone: Any, tz: str, prof_ids: set[str], known_person_refs: Collection[str] | None) -> None:
    u = vu.unit
    vq = vu.quotes
    q_by_msg = {q.message_id: q for q in vq}
    sent = [q.sent_at for q in vq if q.sent_at is not None]
    vu.msg_at = max(sent) if sent else None
    notes = vu.notes

    # người: person_ref lạ ⇒ chỉ dùng surface
    if known_person_refs is not None:
        known = set(known_person_refs)
        people = []
        for p in u.people:
            if p.person_ref is not None and p.person_ref not in known:
                notes.append("person_ref_unknown_dropped")
                res.bump("person_ref_unknown")
                p = p.model_copy(update={"person_ref": None})
            people.append(p)
        u = u.model_copy(update={"people": people})

    # ongoing (#1–#2): ids ⊆ quotes (contract đã lọc), verified, sent_at ≥ now − 48 giờ
    kept_ongoing = []
    for mid in u.ongoing_message_ids:
        q = q_by_msg.get(mid)
        if q is None or q.status != "verified" or q.sent_at is None:
            notes.append(f"ongoing_id_dropped:{mid}")
        elif q.sent_at < now - ONGOING_WINDOW:
            notes.append(f"ongoing_id_expired:{mid}")
        else:
            kept_ongoing.append(mid)
    vu.ongoing = bool(u.flags.ongoing and kept_ongoing)
    if u.flags.ongoing and not vu.ongoing:
        notes.append("ongoing_forced_false")
        res.bump("ongoing_forced_false")
    u = u.model_copy(update={"ongoing_message_ids": kept_ongoing, "flags": u.flags.model_copy(update={"ongoing": vu.ongoing})})

    # explicit_urgent (#8): cờ chỉ giữ khi trong tin trích dẫn có từ vựng "gấp"
    vu.explicit_urgent = u.flags.explicit_urgent
    if u.flags.explicit_urgent:
        blob = "\n".join(_message_text(ev_by_id[q.message_id], media) for q in vq if q.message_id in ev_by_id)
        if not vi_time.has_urgent(blob):
            vu.explicit_urgent = False
            notes.append("explicit_urgent_unverified")
            res.bump("explicit_urgent_unverified")
            u = u.model_copy(update={"flags": u.flags.model_copy(update={"explicit_urgent": False})})

    # event_at (#3)
    vu.event_at = vu.msg_at
    et = u.event_time
    if et is not None and et.text:
        anchor_ev = ev_by_id.get(et.anchor_message_id or "")
        anchor_at = _g(anchor_ev, "sent_at") if anchor_ev is not None else None
        ok_text = anchor_ev is not None and norm(et.text) in norm(_message_text(anchor_ev, media))
        inst = vi_time.parse_past(et.text, anchor_at, tz) if (ok_text and anchor_at is not None) else None
        if inst is None:
            vu.event_time_unparsed = True
            notes.append("event_time_unparsed" if ok_text else "event_time_text_mismatch")
            res.bump("event_time_unparsed")
        else:
            vu.event_at = min(inst.latest, vu.msg_at) if vu.msg_at is not None else inst.latest
            vu.event_granularity = inst.granularity

    # deadline_at
    d = u.deadline
    if d.text:
        anchor_ev = ev_by_id.get(d.anchor_message_id or "")
        anchor_at = _g(anchor_ev, "sent_at") if anchor_ev is not None else None
        if anchor_at is None:
            anchor_at = vu.msg_at
            notes.append("deadline_anchor_fallback")
        due = vi_time.parse(d.text, anchor_at, tz) if anchor_at is not None else vi_time.Due(at=None)
        if due.at is None:
            vu.deadline_unparsed = True
            notes.append("deadline_unparsed")
            res.bump("deadline_unparsed")
        else:
            vu.deadline_at, vu.deadline_lunar, vu.deadline_ambiguous = due.at, due.lunar, due.ambiguous
            if due.recurrence is not None:
                notes.append("deadline_recurring")

    # tiền (code parse); số phải có trong tin được trích dẫn
    blob_fold = _fold("\n".join(_message_text(ev_by_id[q.message_id], media) for q in vq if q.message_id in ev_by_id))
    for basis, txt in (("amount_text", u.amount_text), ("harm.amount_text", u.harm.amount_text if u.harm else None)):
        if not txt:
            continue
        val = parse_money_vnd(txt)
        if val is None:
            continue
        if _fold(txt) not in blob_fold:
            notes.append("amount_text_not_in_messages")
            res.bump("amount_text_not_in_messages")
            continue
        vu.money_vnd, vu.money_basis = val, basis
        break

    # status (#4)
    if u.status_message_id:
        sq = q_by_msg.get(u.status_message_id)
        if sq is not None and sq.status == "verified":
            vu.status_by_person_id, vu.status_at = sq.sender_person_id, sq.sent_at

    # txn (#10)
    if u.txn is not None and u.txn.state_hint and u.txn.message_id:
        tq = q_by_msg.get(u.txn.message_id)
        if tq is not None and tq.status == "verified":
            vu.txn_at = tq.sent_at
            if u.txn.state_hint == "requested":
                vu.txn_state = "pending"
            else:
                ref = min([t for t in (vu.event_at, vu.txn_at) if t is not None], default=None)
                vu.txn_state = "just_done" if ref is None or max(timedelta(0), now - ref) < TXN_JUST_DONE else "done_cannot_stop"
        else:
            notes.append("txn_message_unverified")

    # delivery_form (#9): cờ chuyển tiếp của nền tảng thắng
    fwd = any(bool(_g(ev_by_id[q.message_id], "forwarded")) for q in vq if q.message_id in ev_by_id)
    vu.delivery_form = "forwarded" if fwd else u.delivery_form_hint

    # owner_relevance: chỉ id có trong khối Hồ sơ đã gửi
    rel = []
    for r in u.owner_relevance:
        if _as_ref(r.fact_id) in prof_ids:
            rel.append(r)
        else:
            res.bump("owner_relevance_dropped")
            notes.append("owner_relevance_dropped")
    vu.owner_relevance_kept = [r.fact_id for r in rel]
    u = u.model_copy(update={"owner_relevance": rel, "owner_basis": "co_ho_so" if rel else "thieu_ho_so"})
    vu.unit = u


def _verify_facts(res: Verified, facts: Sequence[FactProposed], unit_by_ref: Mapping[str, VerifiedUnit], ev_by_id: Mapping[str, Any],
                  known_predicates: Collection[str] | None) -> None:
    known = set(known_predicates) if known_predicates is not None else None
    for f in facts:
        vu = unit_by_ref.get(f.unit_ref)
        if vu is None or vu.status != "ok":
            res.bump("fact_dropped_unit_absent")
            continue
        if known is not None and f.predicate not in known:
            res.bump("unknown_predicate")
            continue
        by_msg = {q.message_id: q for q in vu.verified_quotes}
        ev = [by_msg[m] for m in dict.fromkeys(f.quote_refs) if m in by_msg]
        if not ev:
            res.bump("fact_dropped_no_verified_quote")
            continue
        res.facts.append(VerifiedFact(f, f.unit_ref, ev, ev[0].sender_person_id))


__all__ = ["MediaText", "Verified", "VerifiedUnit", "VerifiedQuote", "VerifiedFact", "check", "norm", "parse_money_vnd",
           "ONGOING_WINDOW"]
