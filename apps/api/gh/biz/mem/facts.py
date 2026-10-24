# ruff: noqa: E501
"""Sổ sự thật HAI THỜI GIAN (L3, spec §4A.1): `valid_from/valid_to` (thời điểm sự việc, neo vào giờ GỬI tin) + `recorded_at`
(lúc ghi). Không bao giờ sửa nội dung: mâu thuẫn thì ĐÓNG hiệu lực (`valid_to`) hoặc gắn `disputed`.

`assert_fact(db, FactIn) -> Fact` là MỘT nơi duy nhất ghi `mem.facts` (test AST `test_mem_core_single_writer`). Thuật toán theo
thứ tự cố định của r6.1 (CNS-093/094/095/096/102/107):

 0. (`assert_facts`) sắp theo (chủ thể, vị từ); mỗi sự thật một SAVEPOINT; lỗi ⇒ ROLLBACK TO SAVEPOINT + `refinery.fact_retry`.
 1. chuẩn hoá chủ thể/đối tượng về thực thể đích;  2. khoá tư vấn theo (org, chủ thể, vị từ);
 3. idempotent theo (chủ thể, vị từ, object_key, source_raw_event_id) — chỉ thêm bằng chứng còn thiếu;
 4. đa trị: ghi thẳng (cùng giá trị đang hiệu lực ⇒ chỉ thêm bằng chứng);
 5. đơn trị: tìm `prev` / `next` theo thời gian sự việc (kể cả dòng đã đóng);
 6. quyết định rồi mới ghi: a) cùng giá trị ⇒ thêm bằng chứng · b) tự nói/Sếp nói, vị từ auto_supersede ⇒ UPDATE prev TRƯỚC
    rồi INSERT mới · c) auto_supersede=false (payment_account) ⇒ disputed bất kể ai nói · d) hai nguồn khác nhau cùng
    khoảng ⇒ cả hai disputed · e) outsider/forwarded luôn `asserted` + attributed_to, chỉ làm sự thật khác `disputed`
    · f) Sếp sửa nội dung (`correct`) ⇒ bản cũ `superseded`;
 7. INSERT `fact_evidence` (trigger tăng `evidence_n`);  8. `SET CONSTRAINTS … IMMEDIATE` rồi `DEFERRED` trong savepoint;
 9. `exclusion_violation` là BUG (`fact_write_conflict`), không đổi thành disputed.
"""

from __future__ import annotations

import hashlib
import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import orjson
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from gh import crypto
from gh.biz.mem import disputes, entities, predicates
from gh.textnorm import strip_accents

SOURCE_CLASSES = ("owner", "insider", "member", "outsider", "forwarded", "rule")
EXTERNAL_CLASSES = ("outsider", "forwarded")
#: Độ tin nguồn mặc định theo lớp nguồn (code tính, không do model tự khai — CNS-071/104).
RELIABILITY: dict[str, float] = {"owner": 0.95, "insider": 0.80, "member": 0.60, "outsider": 0.35,
                                 "forwarded": 0.30, "rule": 0.90}
EXTERNAL_CONTENT_CAP = 0.5
LIVE = ("asserted", "owner_confirmed")
MAX_RETRY = 3

# Mã lỗi (đi vào refinery.analyses.errors — CHỈ mã + id, không nội dung).
ERR_UNKNOWN_PREDICATE = "unknown_predicate"
ERR_INVALID = "invalid_fact"
ERR_EVIDENCE = "evidence_missing"
ERR_CONFLICT = "fact_write_conflict"
ERR_DEADLOCK = "deadlock"
ERR_DB = "db_error"


class FactWriteError(Exception):
    def __init__(self, code: str, detail: str = "", *, retryable: bool = False) -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code, self.detail, self.retryable = code, detail, retryable


@dataclass
class EvidenceIn:
    kind: str                                   # raw_event | meaning_unit | owner_edit
    raw_event_id: uuid.UUID | None = None
    raw_received_at: datetime | None = None
    meaning_unit_id: uuid.UUID | None = None
    action_log_id: uuid.UUID | None = None
    quote_status: str | None = None             # None ⇒ code tự suy (xem _evidence_status)
    span: dict[str, Any] | None = None          # {source_field, span_start, span_end} — do code cắt


@dataclass
class FactIn:
    org_id: uuid.UUID
    subject_id: uuid.UUID
    predicate: str
    valid_from: datetime
    object_entity_id: uuid.UUID | None = None
    object_value: Any = None
    source_class: str = "insider"
    evidence: list[EvidenceIn] = field(default_factory=list)
    speaker_entity_id: uuid.UUID | None = None  # ai nói câu này (chủ thể "tự nói về mình" ⇒ được thay thế giá trị)
    attributed_to: uuid.UUID | None = None      # "theo lời X"
    source_raw_event_id: uuid.UUID | None = None
    source_received_at: datetime | None = None  # giờ NHẬN của tin nguồn (phân xử khi hoà thời điểm)
    source_reliability: float | None = None
    agreement: float | None = None
    confidence_exist: float = 0.5
    confidence_content: float = 0.5
    context_code: str | None = None
    extractor_version: str | None = None
    created_by: str = "extractor"               # extractor | consolidator | owner | rule
    confirmed_by: uuid.UUID | None = None       # user id của Sếp ⇒ owner_confirmed (chỉ với source_class='owner')
    proposed: bool = False                      # trợ lý ĐỀ XUẤT (Hồ sơ Sếp) ⇒ status 'proposed'
    valid_to: datetime | None = None            # chỉ dùng cho vị từ đa trị

    def to_json(self) -> dict[str, Any]:
        return orjson.loads(orjson.dumps(asdict(self), default=str))

    @classmethod
    def from_json(cls, d: dict[str, Any]) -> FactIn:
        def u(v: Any) -> uuid.UUID | None:
            return None if v is None else uuid.UUID(str(v))

        def t(v: Any) -> datetime | None:
            return None if v is None else datetime.fromisoformat(str(v))

        ev = [EvidenceIn(e["kind"], u(e.get("raw_event_id")), t(e.get("raw_received_at")), u(e.get("meaning_unit_id")),
                         u(e.get("action_log_id")), e.get("quote_status"), e.get("span")) for e in d.get("evidence", [])]
        return cls(org_id=uuid.UUID(d["org_id"]), subject_id=uuid.UUID(d["subject_id"]), predicate=d["predicate"],
                   valid_from=t(d["valid_from"]),  # type: ignore[arg-type]
                   object_entity_id=u(d.get("object_entity_id")), object_value=d.get("object_value"),
                   source_class=d.get("source_class", "insider"), evidence=ev, speaker_entity_id=u(d.get("speaker_entity_id")),
                   attributed_to=u(d.get("attributed_to")), source_raw_event_id=u(d.get("source_raw_event_id")),
                   source_received_at=t(d.get("source_received_at")), source_reliability=d.get("source_reliability"),
                   agreement=d.get("agreement"), confidence_exist=d.get("confidence_exist", 0.5),
                   confidence_content=d.get("confidence_content", 0.5), context_code=d.get("context_code"),
                   extractor_version=d.get("extractor_version"), created_by=d.get("created_by", "extractor"),
                   confirmed_by=u(d.get("confirmed_by")), proposed=bool(d.get("proposed", False)),
                   valid_to=t(d.get("valid_to")))


@dataclass(frozen=True)
class Fact:
    id: uuid.UUID
    org_id: uuid.UUID
    subject_id: uuid.UUID
    predicate: str
    object_entity_id: uuid.UUID | None
    object_value: Any
    object_key: str
    valid_from: datetime
    valid_to: datetime | None
    recorded_at: datetime
    status: str
    is_single: bool
    source_class: str
    source_reliability: float
    confidence_exist: float
    confidence_content: float
    attributed_to: uuid.UUID | None
    context_code: str | None
    sensitivity: str
    source_raw_event_id: uuid.UUID | None
    supersedes: uuid.UUID | None
    superseded_by: uuid.UUID | None
    dispute_id: uuid.UUID | None
    evidence_n: int
    extractor_version: str | None
    created_by: str
    confirmed_by: uuid.UUID | None
    outcome: str = "read"       # inserted | duplicate | evidence_added | superseded_prev | inserted_between | disputed | …

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d.pop("outcome", None)
        return orjson.loads(orjson.dumps(d, default=str))


_COLS = """id, org_id, subject_id, predicate, object_entity_id, object_value, object_key, valid_from, valid_to,
           recorded_at, status, is_single, source_class, source_reliability, confidence_exist, confidence_content,
           attributed_to, context_code, sensitivity, source_raw_event_id, supersedes, superseded_by, dispute_id,
           evidence_n, extractor_version, created_by, confirmed_by"""


def _fact(r: Any, outcome: str = "read") -> Fact:
    return Fact(r.id, r.org_id, r.subject_id, r.predicate, r.object_entity_id, r.object_value, r.object_key,
                r.valid_from, r.valid_to, r.recorded_at, r.status, r.is_single, r.source_class,
                float(r.source_reliability), float(r.confidence_exist), float(r.confidence_content), r.attributed_to,
                r.context_code, r.sensitivity, r.source_raw_event_id, r.supersedes, r.superseded_by, r.dispute_id,
                r.evidence_n, r.extractor_version, r.created_by, r.confirmed_by, outcome)


# ─── khoá so sánh ───────────────────────────────────────────────────────────

def _fold(s: str) -> str:
    return strip_accents(" ".join(s.split())).lower()


def _norm(v: Any) -> Any:
    if isinstance(v, str):
        return _fold(v)
    if isinstance(v, dict):
        return {str(k): _norm(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_norm(x) for x in v]
    return v


OBLIGATION_KEYS = frozenset({"text", "due_at", "at", "to_entity_id"})
OBLIGATION_PREDICATES = frozenset({"promised_to", "owes", "deadline", "scheduled_event"})


def value_key(value: Any, predicate: str | None = None) -> str:
    """Khoá so sánh của một giá trị: băm của giá trị đã chuẩn hoá (bỏ dấu, chữ thường, gộp khoảng trắng; khoá JSON sắp
    xếp). Cùng ý khác cách gõ hoa/dấu ⇒ cùng khoá. Với vị từ lời hứa/hạn (`OBLIGATION_PREDICATES`) và giá trị dạng
    `{"text", "due_at", "at", "to_entity_id"}`, khoá gồm `text` + `to_entity_id` (không gồm hạn): cùng nội dung + cùng người nhận khác hạn
    là CÙNG một dòng (hạn đổi được cập nhật tường minh trong `_assert`), hứa với hai người nhận khác nhau là hai dòng. Không có người nhận ⇒
    khoá = khoá của chuỗi `text` (tương thích dòng cũ ghi bằng chuỗi)."""
    if predicate in OBLIGATION_PREDICATES and isinstance(value, dict) and isinstance(value.get("text"), str) and set(value) <= OBLIGATION_KEYS:
        to = value.get("to_entity_id")
        value = value["text"] if not to else {"text": value["text"], "to_entity_id": str(to)}
    raw = orjson.dumps(_norm(value), option=orjson.OPT_SORT_KEYS | orjson.OPT_NON_STR_KEYS)
    return "v:" + hashlib.sha256(raw).hexdigest()[:40]


def entity_key(entity_id: uuid.UUID) -> str:
    return "e:" + str(entity_id)


def _aware(t: datetime) -> datetime:
    return t if t.tzinfo is not None else t.replace(tzinfo=UTC)


def _clamp(x: float | Decimal | None, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, float(x if x is not None else 0)))


# ─── ghi ────────────────────────────────────────────────────────────────────

async def _evidence_status(db: AsyncSession, e: EvidenceIn) -> str:
    """quote_status của một bằng chứng. Tường minh ⇒ dùng; owner_edit ⇒ verified (Sếp tự sửa); raw_event có span do code cắt
    ⇒ verified theo cấu tạo (đường nhanh, CNS-070); meaning_unit ⇒ verified khi MỌI dòng clean.evidence của đơn vị đã kiểm."""
    if e.quote_status is not None:
        return e.quote_status
    if e.kind == "owner_edit":
        return "verified"
    if e.kind == "raw_event":
        sp = e.span or {}
        if e.raw_event_id is not None and sp.get("source_field") and isinstance(sp.get("span_start"), int) \
                and isinstance(sp.get("span_end"), int) and sp["span_end"] > sp["span_start"] >= 0:
            return "verified"
        return "unverifiable"
    if e.kind == "meaning_unit" and e.meaning_unit_id is not None:
        ok = (await db.execute(text("""SELECT count(*) > 0 AND bool_and(quote_status = 'verified')
                                       FROM clean.evidence WHERE meaning_unit_id = :u"""),
                               {"u": e.meaning_unit_id})).scalar_one()
        return "verified" if ok else "unverifiable"
    return "unverifiable"


async def _add_evidence(db: AsyncSession, org_id: uuid.UUID, fact_id: uuid.UUID, evidence: list[EvidenceIn]) -> int:
    n = 0
    for e in evidence:
        if e.kind not in ("raw_event", "meaning_unit", "owner_edit"):
            raise FactWriteError(ERR_INVALID, f"loại bằng chứng lạ: {e.kind}")
        if e.kind == "raw_event" and e.raw_event_id is None:
            raise FactWriteError(ERR_INVALID, "bằng chứng raw_event thiếu raw_event_id")
        if e.kind == "meaning_unit" and e.meaning_unit_id is None:
            raise FactWriteError(ERR_INVALID, "bằng chứng meaning_unit thiếu meaning_unit_id")
        status = await _evidence_status(db, e)
        n += (await db.execute(text("""
            INSERT INTO mem.fact_evidence (org_id, fact_id, kind, raw_event_id, raw_received_at, meaning_unit_id,
                                           action_log_id, quote_status, span)
            VALUES (:o, :f, :k, :r, :rt, :m, :a, :q, CAST(:s AS jsonb))
            ON CONFLICT (fact_id, kind, coalesce(raw_event_id, meaning_unit_id, action_log_id)) DO NOTHING"""),
            {"o": org_id, "f": fact_id, "k": e.kind, "r": e.raw_event_id, "rt": e.raw_received_at,
             "m": e.meaning_unit_id, "a": e.action_log_id, "q": status,
             "s": None if e.span is None else orjson.dumps(e.span).decode()})).rowcount  # type: ignore[attr-defined]
    return n


async def _insert_row(db: AsyncSession, fin: FactIn, pred: predicates.Predicate, *, subject: uuid.UUID,
                      object_entity: uuid.UUID | None, key: str, status: str, valid_from: datetime,
                      valid_to: datetime | None, attributed: uuid.UUID | None, dispute_id: uuid.UUID | None = None,
                      supersedes: uuid.UUID | None = None, new_id: uuid.UUID | None = None) -> uuid.UUID:
    """INSERT một dòng mem.facts — DUY NHẤT một câu INSERT INTO mem.facts trong toàn mã (test AST)."""
    ext = fin.source_class in EXTERNAL_CLASSES
    content = min(_clamp(fin.confidence_content), EXTERNAL_CONTENT_CAP) if ext else _clamp(fin.confidence_content)
    reliability = _clamp(fin.source_reliability if fin.source_reliability is not None else RELIABILITY[fin.source_class])
    r = (await db.execute(text("""
        INSERT INTO mem.facts (id, org_id, subject_id, predicate, object_entity_id, object_value, object_key,
            valid_from, valid_to, status, is_single, source_class, source_reliability, agreement, confidence_exist,
            confidence_content, attributed_to, context_code, sensitivity, source_raw_event_id, supersedes, dispute_id,
            extractor_version, created_by, confirmed_by, confirmed_at)
        VALUES (coalesce(CAST(:id AS uuid), core.uuid_v7()), :o, :s, :p, :oe, CAST(:ov AS jsonb), :k, :vf, :vt, :st, :sg,
            :sc, :rel, :ag, :ce, :cc, :at, :cx, :sens, :sr, :sup, :dp, :ev, :cb, :cby,
            CASE WHEN CAST(:cby AS uuid) IS NOT NULL THEN now() END)
        RETURNING id"""),
        {"id": new_id, "o": fin.org_id, "s": subject, "p": fin.predicate, "oe": object_entity,
         "ov": None if object_entity is not None else orjson.dumps(fin.object_value).decode(), "k": key,
         "vf": valid_from, "vt": valid_to, "st": status, "sg": pred.is_single, "sc": fin.source_class,
         "rel": round(reliability, 2), "ag": None if fin.agreement is None else round(_clamp(fin.agreement), 2),
         "ce": round(_clamp(fin.confidence_exist), 3), "cc": round(content, 3), "at": attributed,
         "cx": fin.context_code, "sens": pred.sensitivity, "sr": fin.source_raw_event_id, "sup": supersedes,
         "dp": dispute_id, "ev": fin.extractor_version, "cb": fin.created_by,
         "cby": fin.confirmed_by if status == "owner_confirmed" else None})).scalar_one()
    return r  # type: ignore[no-any-return]


async def _load(db: AsyncSession, fact_id: uuid.UUID, outcome: str = "read") -> Fact:
    r = (await db.execute(text(f"SELECT {_COLS} FROM mem.facts WHERE id = :i"), {"i": fact_id})).one()  # noqa: S608
    return _fact(r, outcome)


def _validate(fin: FactIn, pred: predicates.Predicate, subject: entities.Entity) -> None:
    if fin.source_class not in SOURCE_CLASSES:
        raise FactWriteError(ERR_INVALID, f"source_class lạ: {fin.source_class}")
    if fin.created_by not in ("extractor", "consolidator", "owner", "rule"):
        raise FactWriteError(ERR_INVALID, f"created_by lạ: {fin.created_by}")
    if subject.entity_type not in pred.subject_types:
        raise FactWriteError(ERR_INVALID, f"{fin.predicate}: chủ thể loại {subject.entity_type} không hợp lệ")
    if pred.object_kind == "entity" and fin.object_entity_id is None:
        raise FactWriteError(ERR_INVALID, f"{fin.predicate}: cần object_entity_id")
    if pred.object_kind == "value" and (fin.object_value is None or fin.object_entity_id is not None):
        raise FactWriteError(ERR_INVALID, f"{fin.predicate}: cần object_value (không dùng object_entity_id)")
    if fin.predicate == "payment_account":
        _validate_payment_value(fin.object_value)
    if fin.proposed and fin.source_class in EXTERNAL_CLASSES:
        raise FactWriteError(ERR_INVALID, "nguồn ngoài không thể ở trạng thái proposed")
    if fin.confirmed_by is not None and fin.source_class != "owner":
        raise FactWriteError(ERR_INVALID, "chỉ nguồn owner mới owner_confirmed")
    if not fin.evidence:
        raise FactWriteError(ERR_EVIDENCE, "không có bằng chứng nào")


PAYMENT_KEYS = frozenset({"digest", "last3", "bank"})
_LONG_DIGITS = re.compile(r"\d{7,}")
_DIGEST_RE = re.compile(r"[0-9a-f]{64}")
BANK_MAX_LEN = 32


def payment_digest(account_number: str) -> str:
    """Băm CÓ KHOÁ (HMAC-SHA256 bằng khoá con của khoá master — `gh.crypto.keyed_digest`) của số tài khoản đã chuẩn hoá
    (chỉ giữ chữ số), 64 hex thường. Số tài khoản là giá trị ngắn/đoán được: sha256 trần dò ngược được, nên bắt buộc dùng khoá."""
    digits = re.sub(r"[\s.\-]", "", account_number or "")
    if not digits.isdigit() or len(digits) < 6:
        raise ValueError("số tài khoản phải có ít nhất 6 chữ số")
    return crypto.keyed_digest(b"payment-account", digits.encode()).hex()


def _validate_payment_value(v: Any) -> None:
    """`payment_account` CHỈ lưu băm có khoá (64 hex, `payment_digest`) + 3 số cuối (+ mã ngân hàng ngắn): số tài khoản nguyên
    văn không bao giờ vào sổ sự thật. Chỉ `bank` (chữ tự do) bị quét chuỗi số dài; `digest` đúng định dạng cứng nên không quét."""
    if not isinstance(v, dict) or not set(v) <= PAYMENT_KEYS:
        raise FactWriteError(ERR_INVALID, "payment_account: chỉ nhận {digest, last3, bank}")
    if not isinstance(v.get("digest"), str) or not _DIGEST_RE.fullmatch(v["digest"]):
        raise FactWriteError(ERR_INVALID, "payment_account: digest phải là 64 ký tự hex thường (facts.payment_digest)")
    if "last3" in v and not (isinstance(v["last3"], str) and re.fullmatch(r"\d{1,3}", v["last3"])):
        raise FactWriteError(ERR_INVALID, "payment_account: last3 phải là tối đa 3 chữ số")
    bank = v.get("bank")
    if bank is not None and (not isinstance(bank, str) or len(bank) > BANK_MAX_LEN or _LONG_DIGITS.search(bank)):
        raise FactWriteError(ERR_INVALID, "payment_account: bank phải là mã ngân hàng ngắn, không chứa số tài khoản")


def _map_db_error(e: DBAPIError) -> FactWriteError:
    code = getattr(e.orig, "sqlstate", None) or getattr(getattr(e.orig, "__cause__", None), "sqlstate", None)
    msg = str(e.orig)
    if code == "23P01":
        return FactWriteError(ERR_CONFLICT, "exclusion_violation (bug: lẽ ra đã đóng bản cũ trước)", retryable=True)
    if code == "40P01":
        return FactWriteError(ERR_DEADLOCK, "deadlock_detected", retryable=True)
    cname = getattr(getattr(e.orig, "__cause__", None), "constraint_name", None)
    if code == "23514" and (cname == "mem_facts_need_evidence" or "thiếu bằng chứng" in msg):
        return FactWriteError(ERR_EVIDENCE, "thiếu bằng chứng đã kiểm")
    return FactWriteError(ERR_DB, f"sqlstate={code} {msg[:200]}", retryable=code in ("40001", "55P03"))


async def assert_fact(db: AsyncSession, fin: FactIn) -> Fact:
    """Ghi MỘT sự thật theo thuật toán §4A.1. Chạy trong SAVEPOINT riêng: lỗi ⇒ ROLLBACK TO SAVEPOINT rồi ném
    `FactWriteError(code)`; các sự thật đã ghi trước đó của cùng giao dịch không bị ảnh hưởng."""
    try:
        pred = await predicates.get(db, fin.predicate)
    except predicates.UnknownPredicate:
        raise FactWriteError(ERR_UNKNOWN_PREDICATE, fin.predicate) from None
    try:
        async with db.begin_nested():
            res = await _assert(db, fin, pred)
            # CNS-093: kiểm bằng chứng HOÃN ngay trong savepoint để lỗi thiếu bằng chứng bắt được ở đây.
            await db.execute(text("SET CONSTRAINTS mem.mem_facts_need_evidence IMMEDIATE"))
            await db.execute(text("SET CONSTRAINTS mem.mem_facts_need_evidence DEFERRED"))
        return await _load(db, res.id, res.outcome)
    except DBAPIError as e:
        raise _map_db_error(e) from e


@dataclass
class _Neighbor:
    id: uuid.UUID
    subject_id: uuid.UUID
    valid_from: datetime
    valid_to: datetime | None
    object_key: str
    status: str
    source_class: str
    source_raw_event_id: uuid.UUID | None
    recorded_at: datetime
    received_at: datetime | None


def _deadline_changed(predicate: str, old: Any, new: Any) -> bool:
    """Giá trị lời hứa/hạn mới mang `due_at` (hoặc, với `scheduled_event`, `at`) KHÁC và không rỗng so với bản đang hiệu lực. Giá trị mới rỗng
    không bao giờ xoá hạn cũ."""
    if predicate not in OBLIGATION_PREDICATES or not isinstance(old, dict) or not isinstance(new, dict):
        return False
    fields = ("at",) if predicate == "scheduled_event" else ("due_at",)
    return any(new.get(k) is not None and new.get(k) != old.get(k) for k in fields)


def _covers(n: _Neighbor, t: datetime) -> bool:
    return n.valid_from <= t and (n.valid_to is None or n.valid_to > t)


async def _assert(db: AsyncSession, fin: FactIn, pred: predicates.Predicate) -> Fact:
    org = fin.org_id
    t = _aware(fin.valid_from)
    # 1) chuẩn hoá về thực thể đích
    try:
        subject_id = await entities.canonical(db, fin.subject_id)
        object_entity = None if fin.object_entity_id is None else await entities.canonical(db, fin.object_entity_id)
        speaker = None if fin.speaker_entity_id is None else await entities.canonical(db, fin.speaker_entity_id)
        attributed = None if fin.attributed_to is None else await entities.canonical(db, fin.attributed_to)
        # Khoá dòng thực thể (FOR SHARE, theo thứ tự id) TRƯỚC mọi khoá/ghi khác: quên người (FOR UPDATE) chờ lượt ghi đang
        # chạy, lượt ghi đến sau thấy `forgotten_at` và bị từ chối — không để lại sự thật mồ côi sau khi quên.
        await entities.lock_rows(db, [x for x in (subject_id, object_entity, speaker, attributed) if x is not None], "share")
        subject = await entities.require(db, org, subject_id, allow_forgotten=False)
        for ref in (object_entity, speaker, attributed):             # mọi tham chiếu cùng tổ chức, chưa bị quên
            if ref is not None:
                await entities.require(db, org, ref, allow_forgotten=False)
    except entities.EntityError as e:
        raise FactWriteError(ERR_INVALID, str(e)) from e
    _validate(fin, pred, subject)
    key = entity_key(object_entity) if object_entity is not None else value_key(fin.object_value, fin.predicate)
    if attributed is None and fin.source_class in EXTERNAL_CLASSES:
        attributed = speaker
    by = "owner" if fin.created_by == "owner" or fin.source_class == "owner" else \
        ("consolidator" if fin.created_by == "consolidator" else "extractor")
    await disputes.set_actor(db, by, "assert_fact")
    # 2) khoá tư vấn — mọi lượt lấy khoá cùng thứ tự (assert_facts đã sắp), ép ::text (CNS-102)
    await db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(CAST(:o AS text) || CAST(:s AS text) || :p, 0))"),
                     {"o": str(org), "s": str(subject_id), "p": fin.predicate})
    members = await entities.members(db, subject_id)

    # 3) idempotent theo tin nguồn + thay thế khi replay bằng extractor_version mới
    new_id: uuid.UUID | None = None
    if fin.source_raw_event_id is not None:
        # Sếp đã sửa ĐÚNG sự thật này (cùng giá trị gốc) của tin này ⇒ replay sinh lại nó KHÔNG được đảo ngược; sự thật anh em
        # cùng tin (vd 'muối' khi Sếp sửa 'gạo') không bị ảnh hưởng.
        fixed = (await db.execute(text(f"""
            SELECT {', '.join('n.' + c.strip() for c in _COLS.split(','))} FROM mem.facts o JOIN mem.facts n ON n.id = o.superseded_by
            WHERE o.org_id = :o AND o.subject_id = ANY(:m) AND o.predicate = :p AND o.source_raw_event_id = :r
              AND o.object_key = :k AND o.status = 'superseded' AND n.created_by = 'owner' AND n.status IN ('owner_confirmed', 'asserted', 'disputed')
            ORDER BY n.recorded_at DESC LIMIT 1"""),  # noqa: S608
            {"o": org, "m": members, "p": fin.predicate, "r": fin.source_raw_event_id, "k": key})).one_or_none()
        if fixed is not None:
            return _fact(fixed, "duplicate")
        dup = (await db.execute(text(f"""
            SELECT {_COLS} FROM mem.facts WHERE org_id = :o AND subject_id = ANY(:m) AND predicate = :p AND object_key = :k
              AND source_raw_event_id = :r AND status <> 'retracted' ORDER BY recorded_at LIMIT 1"""),  # noqa: S608
            {"o": org, "m": members, "p": fin.predicate, "k": key, "r": fin.source_raw_event_id})).one_or_none()
        if dup is not None and not (dup.status == "superseded" and fin.extractor_version
                                    and dup.extractor_version != fin.extractor_version):
            await _add_evidence(db, org, dup.id, fin.evidence)
            return _fact(dup, "duplicate")
        if dup is not None:                # bản cũ đã bị thay ở lần replay trước, nay bản mới sinh lại đúng nó
            await db.execute(text("UPDATE mem.facts SET status = 'retracted' WHERE id = :i"), {"i": dup.id})
        if fin.extractor_version:
            new_id = (await db.execute(text("SELECT core.uuid_v7()"))).scalar_one()
            olds = (await db.execute(text("""
                UPDATE mem.facts SET status = 'superseded', closed_recorded_at = now()
                WHERE org_id = :o AND subject_id = ANY(:m) AND predicate = :p AND source_raw_event_id = :r
                  AND object_key <> :k AND extractor_version IS DISTINCT FROM :v
                  AND status IN ('proposed', 'asserted', 'owner_confirmed', 'disputed') RETURNING id"""),
                {"o": org, "m": members, "p": fin.predicate, "r": fin.source_raw_event_id, "k": key,
                 "v": fin.extractor_version})).scalars().all()
        else:
            olds = []
    else:
        olds = []

    def finish_old(inserted: uuid.UUID) -> Any:
        return db.execute(text("UPDATE mem.facts SET superseded_by = :n WHERE id = ANY(:o)"), {"n": inserted, "o": list(olds)})

    ext = fin.source_class in EXTERNAL_CLASSES
    owner_confirmed = fin.confirmed_by is not None and fin.source_class == "owner" and not fin.proposed
    base_status = "proposed" if fin.proposed else ("owner_confirmed" if owner_confirmed else "asserted")

    async def insert(status: str, vf: datetime, vt: datetime | None, dispute_id: uuid.UUID | None = None,
                     supersedes: uuid.UUID | None = None) -> uuid.UUID:
        fid = await _insert_row(db, fin, pred, subject=subject_id, object_entity=object_entity, key=key, status=status,
                                valid_from=vf, valid_to=vt, attributed=attributed, dispute_id=dispute_id, new_id=new_id,
                                supersedes=supersedes)
        await _add_evidence(db, org, fid, fin.evidence)
        if olds:
            await finish_old(fid)
        return fid

    # 4) đa trị
    if not pred.is_single:
        same = (await db.execute(text(f"""
            SELECT {_COLS} FROM mem.facts WHERE org_id = :o AND subject_id = ANY(:m) AND predicate = :p AND object_key = :k
              AND status IN ('proposed', 'asserted', 'owner_confirmed', 'disputed')
              AND (valid_to IS NULL OR valid_to >= :t) AND valid_from <= :t ORDER BY valid_from DESC LIMIT 1"""),  # noqa: S608
            {"o": org, "m": members, "p": fin.predicate, "k": key, "t": t})).one_or_none()
        changed = (same is not None and not olds and base_status == "asserted" and t > same.valid_from
                   and _deadline_changed(fin.predicate, same.object_value, fin.object_value))
        if changed and same is not None:
            # Cổng tin cậy (như đường đơn trị): chỉ bản mới "tự đủ tư cách" — nguồn owner, hoặc chính người hứa/Sếp nói, không phải
            # người ngoài/tin chuyển tiếp, độ tin nguồn ≥ bản cũ — mới được ĐỔI hạn. Thiếu một điều kiện ⇒ mở TRANH CHẤP (Sếp thấy có người báo hạn
            # khác), bản cũ giữ nguyên.
            owner_id = (await db.execute(text("SELECT id FROM mem.entities WHERE org_id = :o AND is_owner"), {"o": org})).scalar_one_or_none()
            trusted = (not ext and fin.source_class != "forwarded"
                       and (fin.source_class == "owner" or (speaker is not None and (speaker == subject_id or speaker == owner_id)))
                       and RELIABILITY.get(fin.source_class, 0.0) >= RELIABILITY.get(same.source_class, 0.0))
            if not trusted and same.status in LIVE:
                fid = await insert("disputed", t, None if fin.valid_to is None else _aware(fin.valid_to))
                await disputes.open(db, org, subject_id, fin.predicate, [same.id, fid], signal_kind=pred.dispute_signal_kind, note="han_khac")
                return await _load(db, fid, "disputed")
            if trusted and same.status == "asserted":
                # hạn/giờ của CÙNG lời hứa (cùng nội dung + cùng người nhận) đổi theo tin MỚI HƠN ⇒ cập nhật TƯỜNG MINH, bảo toàn hai thời
                # gian: bản cũ `superseded` (+ `superseded_by`), bản mới trỏ `supersedes`. Sự thật Sếp đã xác nhận không đi đường này.
                await db.execute(text("UPDATE mem.facts SET status = 'superseded', closed_recorded_at = now() WHERE id = :i"), {"i": same.id})
                fid = await insert(base_status, t, None if fin.valid_to is None else _aware(fin.valid_to), supersedes=same.id)
                await db.execute(text("UPDATE mem.facts SET superseded_by = :n WHERE id = :i"), {"n": fid, "i": same.id})
                return await _load(db, fid, "deadline_updated")
        if same is not None and not olds and not (base_status == "owner_confirmed" and same.status != "owner_confirmed"):
            await _add_evidence(db, org, same.id, fin.evidence)
            return _fact(same, "evidence_added")
        fid = await insert(base_status, t, None if fin.valid_to is None else _aware(fin.valid_to))
        return await _load(db, fid, "inserted")

    # trợ lý đề xuất (proposed): không tham gia logic thay thế
    if base_status == "proposed":
        fid = await insert("proposed", t, None)
        return await _load(db, fid, "inserted")

    # 5) đơn trị — láng giềng theo thời gian sự việc (KỂ CẢ dòng đã đóng)
    rows = (await db.execute(text("""
        SELECT f.id, f.subject_id, f.valid_from, f.valid_to, f.object_key, f.status, f.source_class, f.source_raw_event_id,
               f.recorded_at, e.received_at
        FROM mem.facts f LEFT JOIN LATERAL (SELECT received_at FROM raw.events WHERE id = f.source_raw_event_id LIMIT 1) e ON true
        WHERE f.org_id = :o AND f.subject_id = ANY(:m) AND f.predicate = :p AND f.status = ANY(:st)
        ORDER BY f.valid_from, f.recorded_at"""),
        {"o": org, "m": members, "p": fin.predicate, "st": [*LIVE, "disputed"]})).all()
    live = [_Neighbor(r.id, r.subject_id, r.valid_from, r.valid_to, r.object_key, r.status, r.source_class,
                      r.source_raw_event_id, r.recorded_at, r.received_at) for r in rows if r.status in LIVE]
    disp = [_Neighbor(r.id, r.subject_id, r.valid_from, r.valid_to, r.object_key, r.status, r.source_class,
                      r.source_raw_event_id, r.recorded_at, r.received_at) for r in rows if r.status == "disputed"]
    nonzero = [n for n in live if n.valid_to is None or n.valid_to > n.valid_from]
    prev = max((n for n in nonzero if n.valid_from <= t), key=lambda n: (n.valid_from, n.recorded_at), default=None)
    nxt = min((n for n in nonzero if n.valid_from > t), key=lambda n: (n.valid_from, n.recorded_at), default=None)
    next_from = None if nxt is None else nxt.valid_from

    # tranh chấp đang mở: cùng giá trị với một bên ⇒ chỉ thêm bằng chứng cho bên đó
    for dn in disp:
        if dn.object_key == key and dn.valid_from <= t and (dn.valid_to is None or dn.valid_to > t):
            await _add_evidence(db, org, dn.id, fin.evidence)
            return await _load(db, dn.id, "evidence_added")
    open_disp_overlap = [dn for dn in disp if dn.valid_to is None or dn.valid_to > t]

    covers = prev is not None and _covers(prev, t)
    # 6a) cùng giá trị với prev ⇒ chỉ thêm bằng chứng
    if prev is not None and prev.object_key == key and (covers or prev.valid_from == t) and not olds:
        await _add_evidence(db, org, prev.id, fin.evidence)
        return await _load(db, prev.id, "evidence_added")

    # hoà thời điểm với prev: tin NHẬN trước xếp trước (phân xử theo thứ tự nhận của source_raw_event_id)
    n_before_prev = False
    if prev is not None and prev.valid_from == t and prev.object_key != key:
        prev_recv = prev.received_at or prev.recorded_at
        mine = _aware(fin.source_received_at) if fin.source_received_at is not None else None
        n_before_prev = mine is not None and mine < _aware(prev_recv)

    async def to_dispute(conflicts: list[_Neighbor], note: str | None) -> Fact:
        # bên đang hiệu lực xung đột ⇒ disputed (rời EXCLUDE) TRƯỚC khi chèn bản mới; cùng dispute_id
        ids: list[uuid.UUID] = []
        for conflict in conflicts:
            await db.execute(text("""UPDATE mem.facts SET status = 'disputed', closed_recorded_at = now()
                                     WHERE id = :i AND status IN ('asserted', 'owner_confirmed')"""), {"i": conflict.id})
            ids.append(conflict.id)
        ids.extend(dn.id for dn in open_disp_overlap)
        did, _created = await disputes.open(db, org, subject_id, fin.predicate, ids, signal_kind=pred.dispute_signal_kind,
                                            note=note)
        fid = await insert("disputed", t, next_from, did)
        await disputes.open(db, org, subject_id, fin.predicate, [fid], signal_kind=pred.dispute_signal_kind, note=note)
        return await _load(db, fid, "disputed")

    # 6c) auto_supersede = false (payment_account): MỌI đổi giá trị ⇒ disputed, kể cả chính chủ tài khoản (CNS-096)
    if not pred.auto_supersede:
        # Bất kể thời gian: prev (kể cả đã đóng) hoặc next mang giá trị KHÁC ⇒ tranh chấp. Chèn LÙI (tin cũ đến sau) cũng vậy —
        # một đổi STK không bao giờ lặng lẽ thành hai dòng asserted (review L1 🔴1).
        conflicts = [n for n in (prev, nxt) if n is not None and n.object_key != key]
        if conflicts:
            seen = (await db.execute(text("""
                SELECT EXISTS (SELECT 1 FROM mem.facts WHERE org_id = :o AND subject_id = ANY(:m) AND predicate = :p
                                  AND object_key = :k AND status <> 'retracted')"""),
                {"o": org, "m": members, "p": fin.predicate, "k": key})).scalar_one()
            return await to_dispute(conflicts, "stk_quay_lai_cu" if seen else "stk_moi")
        if open_disp_overlap:
            return await to_dispute([], "stk_moi")
        if prev is not None and prev.valid_from == t and n_before_prev:
            fid = await insert("asserted", t, t)
            return await _load(db, fid, "inserted_between")
        fid = await insert(base_status, t, next_from)
        return await _load(db, fid, "inserted" if nxt is None else "inserted_between")

    async def resolve_open(winner: uuid.UUID) -> None:
        """Sếp nói lại ⇒ đóng các tranh chấp đang mở của cặp này với bản của Sếp là bên thắng (CNS-107 i)."""
        if fin.source_class != "owner":
            return
        for did in {d for d in [(await db.execute(text("SELECT dispute_id FROM mem.facts WHERE id = :i"), {"i": dn.id})
                                  ).scalar_one_or_none() for dn in open_disp_overlap] if d is not None}:
            try:
                await disputes.resolve(db, org, did, winner, by="owner", user_id=fin.confirmed_by)
            except disputes.DisputeError:
                pass

    eligible_self = fin.source_class == "owner" or (speaker is not None and speaker == subject_id)
    if covers and prev is not None and prev.object_key != key and not n_before_prev:
        if ext or (prev.status == "owner_confirmed" and fin.source_class != "owner") or not eligible_self:
            return await to_dispute([prev], "hai_nguon_khac_nhau")            # 6d / 6e
        # 6b) UPDATE prev TRƯỚC (đóng theo thời gian sự việc, GIỮ trạng thái), rồi INSERT bản mới
        await db.execute(text("""UPDATE mem.facts SET valid_to = :t, closed_recorded_at = now()
                                 WHERE id = :i AND (valid_to IS NULL OR valid_to > :t)"""), {"t": t, "i": prev.id})
        fid = await insert(base_status, t, next_from)
        await resolve_open(fid)
        return await _load(db, fid, "superseded_prev")
    if not covers and open_disp_overlap and not (fin.source_class == "owner" and eligible_self):
        return await to_dispute([], "hai_nguon_khac_nhau")
    if n_before_prev:                       # tin cũ hơn, cùng thời điểm: xếp TRƯỚC prev, không đè
        fid = await insert("asserted", t, t)
        return await _load(db, fid, "inserted_between")
    fid = await insert(base_status, t, next_from)
    await resolve_open(fid)
    return await _load(db, fid, "inserted" if nxt is None else "inserted_between")


# ─── lời hứa đã giữ ⇒ đóng hiệu lực ────────────────────────────────────────

#: Vị từ "nghĩa vụ" mà `promise_kept` được đóng (kỹ năng v2.1 §9.3: "đóng lời hứa trong sổ").
KEPT_PREDICATES = ("promised_to", "owes")


async def close_kept(db: AsyncSession, org_id: uuid.UUID, raw_event_ids: list[uuid.UUID], *, at: datetime) -> list[uuid.UUID]:
    """`memory_updates.promise_kept` (model: [tin hứa, tin chứng minh đã giữ]): ĐÓNG hiệu lực theo thời gian sự việc các sự thật
    `promised_to`/`owes` đang mở mà tin nguồn hoặc bằng chứng nằm trong `raw_event_ids` — `valid_to = at` (giờ gửi tin đã giữ),
    `closed_recorded_at = now()`, trạng thái GIỮ `asserted` (quy ước CNS-095), không xoá. Không nối theo chữ (không có mã tin hứa ⇒ không
    đóng gì). `owner_confirmed` chỉ Sếp đóng (§4A.1 6f); `disputed` đi đường tranh chấp. Cùng khoá tư vấn với `assert_fact`. Trả id đã đóng."""
    if not raw_event_ids:
        return []
    at = _aware(at)
    rows = (await db.execute(text("""
        SELECT f.id, f.subject_id, f.predicate FROM mem.facts f
        WHERE f.org_id = :o AND f.predicate = ANY(:p) AND f.status = 'asserted' AND f.valid_from < :t AND (f.valid_to IS NULL OR f.valid_to > :t)
          AND (f.source_raw_event_id = ANY(:r) OR EXISTS (SELECT 1 FROM mem.fact_evidence x WHERE x.fact_id = f.id AND x.raw_event_id = ANY(:r)))
        ORDER BY f.subject_id, f.predicate, f.id"""), {"o": org_id, "p": list(KEPT_PREDICATES), "t": at, "r": raw_event_ids})).all()
    if not rows:
        return []
    await disputes.set_actor(db, "extractor", "promise_kept")
    for sid, pred in dict.fromkeys((str(r.subject_id), r.predicate) for r in rows):       # cùng thứ tự khoá với assert_facts (CNS-102)
        await db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(CAST(:o AS text) || CAST(:s AS text) || :p, 0))"),
                         {"o": str(org_id), "s": sid, "p": pred})
    res = await db.execute(text("""UPDATE mem.facts SET valid_to = :t, closed_recorded_at = now()
                                   WHERE id = ANY(:i) AND status = 'asserted' AND (valid_to IS NULL OR valid_to > :t) RETURNING id"""),
                           {"t": at, "i": [r.id for r in rows]})
    return [r[0] for r in res.all()]


# ─── lượt nhiều sự thật + thử lại ──────────────────────────────────────────

@dataclass
class BatchResult:
    written: list[Fact] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)       # {code, predicate, subject_id} — KHÔNG nội dung
    fact_write_conflict: int = 0


async def assert_facts(db: AsyncSession, facts: list[FactIn], *, analysis_id: uuid.UUID | None = None) -> BatchResult:
    """Ghi cả lượt: sắp theo (chủ thể đích, vị từ) để mọi lượt lấy khoá cùng thứ tự (chống deadlock, CNS-102); mỗi sự thật
    một savepoint; lỗi ⇒ ghi `refinery.fact_retry` + `refinery.analyses.errors` (mã + id), lượt VẪN tiến."""
    keyed: list[tuple[str, str, int, FactIn]] = []
    for i, f in enumerate(facts):
        try:
            sid = str(await entities.canonical(db, f.subject_id))
        except entities.EntityError:
            sid = str(f.subject_id)
        keyed.append((sid, f.predicate, i, f))
    keyed.sort(key=lambda x: (x[0], x[1], x[2]))
    out = BatchResult()
    for _sid, _p, _i, f in keyed:
        try:
            out.written.append(await assert_fact(db, f))
        except FactWriteError as e:
            err = {"code": e.code, "predicate": f.predicate, "subject_id": str(f.subject_id)}
            out.errors.append(err)
            if e.code == ERR_CONFLICT:
                out.fact_write_conflict += 1
            if e.code != ERR_UNKNOWN_PREDICATE and e.code != ERR_INVALID:
                await db.execute(text("""
                    INSERT INTO refinery.fact_retry (org_id, analysis_id, fact_in, error_code, attempts, next_at)
                    VALUES (:o, :a, CAST(:f AS jsonb), :c, 0, now() + interval '1 minute')"""),
                    {"o": f.org_id, "a": analysis_id, "f": orjson.dumps(f.to_json()).decode(), "c": e.code})
    if analysis_id is not None and out.errors:
        await db.execute(text("UPDATE refinery.analyses SET errors = errors || CAST(:e AS jsonb) WHERE id = :i"),
                         {"e": orjson.dumps(out.errors).decode(), "i": analysis_id})
    return out


async def retry_failed(db: AsyncSession, *, limit: int = 50) -> dict[str, int]:
    """Thử lại tối đa 3 lần các sự thật lỗi tạm (deadlock, exclusion_violation, thiếu bằng chứng do lỗi tạm)."""
    rows = (await db.execute(text("""
        SELECT id, fact_in, attempts FROM refinery.fact_retry WHERE attempts < :m AND next_at <= now()
        ORDER BY next_at LIMIT :n FOR UPDATE SKIP LOCKED"""), {"m": MAX_RETRY, "n": limit})).all()
    ok = failed = 0
    for r in rows:
        try:
            await assert_fact(db, FactIn.from_json(r.fact_in))
            await db.execute(text("DELETE FROM refinery.fact_retry WHERE id = :i"), {"i": r.id})
            ok += 1
        except FactWriteError as e:
            failed += 1
            await db.execute(text("""UPDATE refinery.fact_retry SET attempts = attempts + 1, error_code = :c,
                                     next_at = now() + make_interval(mins => 5 * (attempts + 1)) WHERE id = :i"""),
                             {"c": e.code, "i": r.id})
    return {"ok": ok, "failed": failed}


async def retire_stale_replay(db: AsyncSession, org_id: uuid.UUID, raw_event_ids: list[uuid.UUID], *,
                              extractor_version: str, keep_fact_ids: list[uuid.UUID]) -> int:
    """Sau replay bằng `extractor_version` mới: dòng của bản cũ cho cùng tin mà bản mới KHÔNG sinh lại ⇒ `retracted`
    (§4A.1 bước 3). `keep_fact_ids` = id các sự thật bản mới đã ghi/chạm tới (kể cả `duplicate`)."""
    await disputes.set_actor(db, "extractor", "replay_retired")
    r = await db.execute(text("""
        UPDATE mem.facts SET status = 'retracted', closed_recorded_at = now(),
               valid_to = coalesce(valid_to, greatest(now(), valid_from))
        WHERE org_id = :o AND source_raw_event_id = ANY(:r) AND extractor_version IS DISTINCT FROM :v
          AND NOT (id = ANY(:k)) AND status IN ('proposed', 'asserted', 'disputed')"""),
        {"o": org_id, "r": raw_event_ids, "v": extractor_version, "k": keep_fact_ids})
    return int(r.rowcount)  # type: ignore[attr-defined]


# ─── đối soát sau gộp người (CNS-065) ──────────────────────────────────────

@dataclass
class Reconciled:
    created: list[uuid.UUID] = field(default_factory=list)            # tranh chấp MỚI mở
    disputes: list[uuid.UUID] = field(default_factory=list)           # mọi tranh chấp bị chạm (mới hoặc gia nhập)
    facts: list[tuple[uuid.UUID, str]] = field(default_factory=list)  # (sự thật, trạng thái CŨ trước khi thành disputed)

    def to_json(self) -> dict[str, Any]:
        return {"disputes": [str(d) for d in self.disputes], "created": [str(d) for d in self.created],
                "facts": [[str(i), st] for i, st in self.facts]}


async def reconcile(db: AsyncSession, org_id: uuid.UUID, entity_id: uuid.UUID) -> Reconciled:
    """Sự thật ĐƠN TRỊ trái nhau giữa các thực thể đã gộp vào cùng một đích (mỗi bên giữ `subject_id` gốc nên EXCLUDE không
    thấy) ⇒ mọi bên `disputed`, chung một tranh chấp. Ghi lại trạng thái cũ để tách lại hoàn tác (`reverse_reconcile`)."""
    out = Reconciled()
    target = await entities.canonical(db, entity_id)
    members = await entities.members(db, target)
    if len(members) < 2:
        return out
    pairs = (await db.execute(text("""
        SELECT DISTINCT a.predicate, a.id AS a_id, b.id AS b_id
        FROM mem.facts a JOIN mem.facts b
          ON a.org_id = b.org_id AND a.predicate = b.predicate AND a.id < b.id AND a.subject_id <> b.subject_id
         AND a.is_single AND b.is_single AND a.object_key <> b.object_key
         AND tstzrange(a.valid_from, coalesce(a.valid_to, 'infinity'::timestamptz))
          && tstzrange(b.valid_from, coalesce(b.valid_to, 'infinity'::timestamptz))
        WHERE a.org_id = :o AND a.subject_id = ANY(:m) AND b.subject_id = ANY(:m)
          AND a.status IN ('asserted', 'owner_confirmed') AND b.status IN ('asserted', 'owner_confirmed')
        ORDER BY a.predicate"""), {"o": org_id, "m": members})).all()
    by_pred: dict[str, set[uuid.UUID]] = {}
    for p in pairs:
        by_pred.setdefault(p.predicate, set()).update((p.a_id, p.b_id))
    await disputes.set_actor(db, "consolidator", "reconcile_after_merge")
    for pcode, ids in by_pred.items():
        pred = await predicates.get(db, pcode)
        await db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(CAST(:o AS text) || CAST(:s AS text) || :p, 0))"),
                         {"o": str(org_id), "s": str(target), "p": pcode})
        before = (await db.execute(text("""SELECT id, status FROM mem.facts WHERE id = ANY(:i) AND status IN ('asserted', 'owner_confirmed')"""),
                                   {"i": sorted(ids)})).all()
        await db.execute(text("""UPDATE mem.facts SET status = 'disputed', closed_recorded_at = now()
                                 WHERE id = ANY(:i) AND status IN ('asserted', 'owner_confirmed')"""), {"i": [r.id for r in before]})
        out.facts.extend((r.id, r.status) for r in before)
        did, created = await disputes.open(db, org_id, target, pcode, sorted(ids), signal_kind=pred.dispute_signal_kind,
                                           note="sau_gop_nguoi")
        out.disputes.append(did)
        if created:
            out.created.append(did)
    return out


async def reconcile_entity(db: AsyncSession, org_id: uuid.UUID, entity_id: uuid.UUID) -> int:
    """Như `reconcile` nhưng chỉ trả số tranh chấp MỚI mở (job đối soát của L4 dùng)."""
    return len((await reconcile(db, org_id, entity_id)).created)


async def reverse_reconcile(db: AsyncSession, org_id: uuid.UUID, recon: dict[str, Any]) -> int:
    """Hoàn tác đối soát lúc gộp khi TÁCH người: sự thật còn `disputed` và còn thuộc tranh chấp đã ghi ⇒ trả về trạng thái cũ,
    bỏ khỏi tranh chấp; tranh chấp còn dưới 2 bên ⇒ đóng với `resolved_by='unmerge'`. Việc xảy ra SAU gộp (Sếp đã chọn bên
    thắng, sự thật bị thay…) không bị lật lại: chỉ đụng sự thật vẫn `disputed`. Trả số sự thật đã trả lại."""
    restored = 0
    dispute_ids = [uuid.UUID(d) for d in recon.get("disputes", [])]
    await disputes.set_actor(db, "consolidator", "unmerge")
    for fid_s, old_status in recon.get("facts", []):
        fid = uuid.UUID(fid_s)
        try:
            async with db.begin_nested():
                r = await db.execute(text("""
                    UPDATE mem.facts SET status = :s, dispute_id = NULL, closed_recorded_at = now()
                    WHERE id = ANY(CAST(:f AS uuid[])) AND org_id = :o AND status = 'disputed' AND dispute_id = ANY(:d)"""),
                                     {"s": old_status, "f": [fid], "o": org_id, "d": dispute_ids})
                restored += int(r.rowcount)  # type: ignore[attr-defined]
        except DBAPIError:                           # trả lại sẽ chồng khoảng với sự thật mới hơn ⇒ để nguyên disputed
            continue
    # Sự thật gia nhập tranh chấp SAU khi gộp (vd số thứ ba): không kẹt ở `disputed` trong tranh chấp sắp đóng — tách khỏi tranh
    # chấp cũ rồi đưa về `asserted`; nếu chồng khoảng với sự thật đang hiệu lực của CHÍNH chủ thể đó (23P01) thì mở/gia nhập tranh
    # chấp đúng chủ thể.
    leftovers = (await db.execute(text("""SELECT id FROM mem.facts WHERE org_id = :o AND status = 'disputed' AND dispute_id = ANY(:d)
                                          ORDER BY valid_from, recorded_at"""), {"o": org_id, "d": dispute_ids})).scalars().all()
    for fid in leftovers:
        await _rehome(db, org_id, fid)
        restored += 1
    for did in dispute_ids:
        await db.execute(text("""
            UPDATE mem.disputes SET fact_ids = (SELECT coalesce(array_agg(f.id), '{}') FROM mem.facts f
                                                WHERE f.id = ANY(mem.disputes.fact_ids) AND f.dispute_id = mem.disputes.id)
            WHERE id = :d AND org_id = :o AND resolved_at IS NULL"""), {"d": did, "o": org_id})
        await db.execute(text("""
            UPDATE mem.disputes SET resolved_at = now(), resolved_by = 'unmerge', note = 'unmerge'
            WHERE id = :d AND org_id = :o AND resolved_at IS NULL AND cardinality(fact_ids) < 2"""), {"d": did, "o": org_id})
    return restored


async def _rehome(db: AsyncSession, org_id: uuid.UUID, fact_id: uuid.UUID) -> None:
    """Đưa một sự thật `disputed` (của tranh chấp sắp đóng) về luồng chuẩn: `asserted`/`owner_confirmed` nếu không chồng khoảng;
    chồng khoảng với sự thật đang hiệu lực cùng chủ thể ⇒ bên kia + nó vào tranh chấp của chủ thể đó. Không để kẹt."""
    row = (await db.execute(text("""SELECT id, subject_id, predicate, confirmed_by, source_class FROM mem.facts
                                    WHERE id = :i AND org_id = :o AND status = 'disputed'"""), {"i": fact_id, "o": org_id})).one_or_none()
    if row is None:
        return
    new_status = "owner_confirmed" if row.confirmed_by is not None and row.source_class not in EXTERNAL_CLASSES else "asserted"
    try:
        async with db.begin_nested():
            await db.execute(text("UPDATE mem.facts SET status = :s, dispute_id = NULL, closed_recorded_at = now() WHERE id = :i"),
                             {"s": new_status, "i": fact_id})
        return
    except DBAPIError as e:
        if getattr(e.orig, "sqlstate", None) != "23P01" and getattr(getattr(e.orig, "__cause__", None), "sqlstate", None) != "23P01":
            raise
    members = await entities.members(db, row.subject_id)
    subject = await entities.canonical(db, row.subject_id)
    conflicts = (await db.execute(text("""
        SELECT o.id FROM mem.facts f JOIN mem.facts o ON o.org_id = f.org_id AND o.predicate = f.predicate AND o.id <> f.id
          AND o.subject_id = ANY(:m) AND o.status IN ('asserted', 'owner_confirmed')
          AND tstzrange(o.valid_from, coalesce(o.valid_to, 'infinity'::timestamptz))
           && tstzrange(f.valid_from, coalesce(f.valid_to, 'infinity'::timestamptz))
        WHERE f.id = :i"""), {"i": fact_id, "m": members})).scalars().all()
    pred = await predicates.get(db, row.predicate)
    await db.execute(text("""UPDATE mem.facts SET status = 'disputed', dispute_id = NULL, closed_recorded_at = now()
                             WHERE id = ANY(:c) AND status IN ('asserted', 'owner_confirmed')"""), {"c": list(conflicts)})
    await disputes.open(db, org_id, subject, row.predicate, [fact_id, *conflicts], signal_kind=pred.dispute_signal_kind,
                        note="sau_tach_nguoi")


# ─── đọc & sửa ──────────────────────────────────────────────────────────────

async def at(db: AsyncSession, org_id: uuid.UUID, subject_id: uuid.UUID, predicate: str | None = None,
             ts: datetime | None = None, *, known_at: datetime | None = None, include_disputed: bool = True) -> list[Fact]:
    """Truy vấn HAI THỜI GIAN: sự thật đúng tại thời điểm `ts` (mặc định bây giờ). `known_at` = chỉ những gì hệ thống
    ĐÃ BIẾT vào lúc đó ("hôm 10/10 em tin gì"): dựng lại status/valid_to theo `mem.fact_status_events`."""
    ts = _aware(ts) if ts is not None else datetime.now(UTC)
    members = await entities.members(db, subject_id)
    statuses = [*LIVE, *(["disputed"] if include_disputed else [])]
    if known_at is None:
        rows = (await db.execute(text(f"""
            SELECT {_COLS} FROM mem.facts
            WHERE org_id = :o AND subject_id = ANY(:m) AND (CAST(:p AS text) IS NULL OR predicate = :p)
              AND status = ANY(:st) AND valid_from <= :t AND (valid_to IS NULL OR valid_to > :t)
            ORDER BY predicate, valid_from, recorded_at"""),  # noqa: S608
            {"o": org_id, "m": members, "p": predicate, "st": statuses, "t": ts})).all()
        return [_fact(r) for r in rows]
    known = _aware(known_at)
    rows = (await db.execute(text("""
        SELECT f.id, ev.to_status AS k_status, ev.valid_to_after AS k_valid_to
        FROM mem.facts f
        JOIN LATERAL (SELECT to_status, valid_to_after FROM mem.fact_status_events e
                      WHERE e.fact_id = f.id AND e.at <= :k ORDER BY e.at DESC, e.id DESC LIMIT 1) ev ON true
        WHERE f.org_id = :o AND f.subject_id = ANY(:m) AND (CAST(:p AS text) IS NULL OR f.predicate = :p)
          AND ev.to_status = ANY(:st) AND f.valid_from <= :t AND (ev.valid_to_after IS NULL OR ev.valid_to_after > :t)
        ORDER BY f.predicate, f.valid_from, f.recorded_at"""),
        {"o": org_id, "m": members, "p": predicate, "st": statuses, "t": ts, "k": known})).all()
    out: list[Fact] = []
    for r in rows:
        f = await _load(db, r.id)
        out.append(Fact(**{**asdict(f), "status": r.k_status, "valid_to": r.k_valid_to}))
    return out


async def get(db: AsyncSession, org_id: uuid.UUID, fact_id: uuid.UUID) -> Fact | None:
    r = (await db.execute(text(f"SELECT {_COLS} FROM mem.facts WHERE id = :i AND org_id = :o"),  # noqa: S608
                          {"i": fact_id, "o": org_id})).one_or_none()
    return None if r is None else _fact(r)


async def correct(db: AsyncSession, org_id: uuid.UUID, fact_id: uuid.UUID, *, actor_user_id: uuid.UUID,
                  new_value: Any = None, new_object_id: uuid.UUID | None = None, retract: bool = False,
                  action_log_id: uuid.UUID | None = None) -> Fact:
    """Sếp sửa nội dung sai / xoá (§4A.1 6f, §7.2b). `retract` ⇒ `retracted` (đóng hiệu lực, KHÔNG xoá dòng). Sửa ⇒ bản cũ
    `superseded` (`superseded_by`), bản mới `owner_confirmed` cùng khoảng hiệu lực, bằng chứng `owner_edit`. Tranh chấp
    chứa bản cũ được đóng với bản mới là bên thắng."""
    probe = (await db.execute(text("SELECT subject_id, predicate FROM mem.facts WHERE id = :i AND org_id = :o"),
                              {"i": fact_id, "o": org_id})).one_or_none()
    if probe is None:
        raise FactWriteError(ERR_INVALID, "không có sự thật này")
    pred = await predicates.get(db, probe.predicate)
    obj: uuid.UUID | None = None
    if not retract:                                   # kiểm giá trị mới TRƯỚC khi đụng bất kỳ dòng nào (review L1 🔴2)
        if (new_value is None) == (new_object_id is None):
            raise FactWriteError(ERR_INVALID, "cần đúng một trong new_value / new_object_id")
        if pred.object_kind == "entity" and new_object_id is None or pred.object_kind == "value" and new_value is None:
            raise FactWriteError(ERR_INVALID, f"{probe.predicate}: loại đối tượng không khớp")
        if probe.predicate == "payment_account":
            _validate_payment_value(new_value)
    try:
        subject_c = await entities.canonical(db, probe.subject_id)
        if new_object_id is not None:
            obj = await entities.canonical(db, new_object_id)
        # cùng thứ tự khoá với assert_fact: dòng thực thể (share) → khoá tư vấn (chủ thể đích, vị từ) → dòng sự thật
        await entities.lock_rows(db, [x for x in (subject_c, obj) if x is not None], "share")
        if obj is not None:
            await entities.require(db, org_id, obj, allow_forgotten=False)
    except entities.EntityError as e:
        raise FactWriteError(ERR_INVALID, str(e)) from e
    await db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(CAST(:o AS text) || CAST(:s AS text) || :p, 0))"),
                     {"o": str(org_id), "s": str(subject_c), "p": probe.predicate})
    old = (await db.execute(text(f"SELECT {_COLS} FROM mem.facts WHERE id = :i AND org_id = :o FOR UPDATE"),  # noqa: S608
                            {"i": fact_id, "o": org_id})).one_or_none()
    if old is None:
        raise FactWriteError(ERR_INVALID, "không có sự thật này")
    if old.status not in ("proposed", "asserted", "owner_confirmed", "disputed"):
        raise FactWriteError(ERR_INVALID, f"sự thật đang ở trạng thái {old.status}, không sửa được")
    await disputes.set_actor(db, "owner", "owner_correct")
    if retract:
        await db.execute(text("""UPDATE mem.facts SET status = 'retracted', closed_recorded_at = now(),
                                   valid_to = coalesce(valid_to, greatest(now(), valid_from)) WHERE id = :i"""),
                         {"i": fact_id})
        return await _load(db, fact_id, "retracted")
    key = entity_key(obj) if obj is not None else value_key(new_value, probe.predicate)
    fin = FactIn(org_id=org_id, subject_id=old.subject_id, predicate=old.predicate, valid_from=old.valid_from,
                 object_entity_id=obj, object_value=new_value if obj is None else None, source_class="owner",
                 confidence_exist=1.0, confidence_content=1.0, context_code=old.context_code, created_by="owner",
                 confirmed_by=actor_user_id,
                 evidence=[EvidenceIn("owner_edit", action_log_id=action_log_id or uuid.uuid4())])
    try:
        async with db.begin_nested():
            # bản cũ rời EXCLUDE TRƯỚC khi chèn bản mới cùng khoảng
            await db.execute(text("UPDATE mem.facts SET status = 'superseded', closed_recorded_at = now() WHERE id = :i"),
                             {"i": fact_id})
            new_id = await _insert_row(db, fin, pred, subject=old.subject_id, object_entity=obj, key=key,
                                       status="owner_confirmed", valid_from=old.valid_from, valid_to=old.valid_to,
                                       attributed=None, supersedes=fact_id)
            await _add_evidence(db, org_id, new_id, fin.evidence)
            await db.execute(text("UPDATE mem.facts SET superseded_by = :n WHERE id = :i"), {"n": new_id, "i": fact_id})
            if old.dispute_id is not None:
                await disputes.resolve(db, org_id, old.dispute_id, new_id, by="owner", user_id=actor_user_id)
            await db.execute(text("SET CONSTRAINTS mem.mem_facts_need_evidence IMMEDIATE"))
            await db.execute(text("SET CONSTRAINTS mem.mem_facts_need_evidence DEFERRED"))
    except DBAPIError as e:
        raise _map_db_error(e) from e
    return await _load(db, new_id, "corrected")
