"""Lọc đầu Hộp thư (ROADMAP Đợt C1 — "Sàng lọc dùng Jev làm lớp lọc đầu"): trùng, rác, điểm chất lượng 0–100.

Chạy SAU khi đơn vị ý nghĩa đã vào kho sạch — không bao giờ chặn đường nhập:
- hook `triage` tiêu thụ `gh.clean.ready` (worker arq, consumer group riêng) → đánh dấu đúng các mục vừa sinh;
- job định kỳ `triage_sweep` (mỗi 5 phút) vét mục còn sót (worker khởi động lại, hook lỗi, bật lại sau khi tắt).
Cả hai đều idempotent: một mục chỉ có một dòng `refinery.item_marks` (PK item_type+item_id, ON CONFLICT DO NOTHING),
khoá tư vấn theo tổ chức để hai lượt song song không dò trùng chồng lên nhau.

Mỗi mục:
1. **Trùng** — văn bản gốc (tin thô làm chứng cứ, không có thì kết luận) chuẩn hoá (thường, bỏ dấu, gộp khoảng trắng):
   sha256 bằng nhau → `exact`; độ giống Jaccard trên tập 3-gram ký tự ≥ `NEAR_JACCARD` → `near` (simhash 64 bit chỉ
   dùng lọc thô `PREFILTER_BITS` — trên tin ngắn simhash dao động quá mạnh để tự quyết). Tin ngắn
   (< `BROADCAST_MIN_LEN`) chỉ tính trùng khi CÙNG người/nhóm — "giá bao nhiêu?" của hai khách khác nhau không phải
   bản trùng; tin dài giống nhau ở nhiều nhóm (tin rải) thì trùng dù khác người gửi. Mục gốc = mục xuất hiện trước.
2. **Rác + điểm** — quy tắc tất định luôn chạy (đủ dùng khi chưa cấu hình model). Có Jev (`Decider.classify`, trần
   1,5 s) và `use_jev` bật → Jev chọn một nhãn trong 4 mức; điểm = 60% Jev + 40% quy tắc. Jev lỗi/chậm/độ tin thấp
   → giữ kết quả quy tắc (không gọi model lớn cho từng mục — chi phí). Cả hai kết quả được lưu để đo độ khớp.

v0.1.55 (G4, QD-12): văn bản gửi Jev LUÔN là bản đã che (`gh.chassis.masking.mask_for_model` — SĐT, số tài khoản,
email, khoá) — `_ask_jev` che trước khi cắt, và `JevClient.choose` che thêm một lớp (lớp thứ hai không có cờ tắt).
`value_summary` = số đo giá trị lọc cho thẻ Jev / màn Hôm nay của Sếp (chỉ số lần, KHÔNG quy ra tiền).
"""

import asyncio
import hashlib
import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

import orjson
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from gh.biz.hooks import CronJob, Hook, HookCtx
from gh.chassis.masking import mask_for_model
from gh.gen import decider as decmod
from gh.textnorm import strip_accents  # F-38: bỏ dấu dùng chung (NFKD — giữ text_hash đã lưu)

log = logging.getLogger("gh.refinery.triage")

VERSION = 1
ITEM_TYPE = "unit"
# `prefilter` (v0.1.55, J2): lọc trước khi trích xuất — bỏ qua tin trùng hẳn / rác chắc chắn, xem gh.refinery.prefilter.
DEFAULTS: dict[str, Any] = {"enabled": True, "min_score": 30, "use_jev": True, "prefilter": True}
MIN_SCORE_RANGE = (0, 100)
CHUNK = 50
MAX_ROUNDS = 20                 # ≤ 1000 mục / lượt job; phần còn lại lượt sau
WINDOW_DAYS = 14                # cửa sổ dò trùng
SWEEP_DAYS = 14                 # quét vét chỉ xét mục gần đây: không lọc lại cả lịch sử (chi phí Jev + quét bảng lớn)
# Trần số mục so trùng mỗi phần (v0.1.27 — xem "Trần 3000" dưới `_candidates`): 3000 mục MỚI NHẤT trong cửa sổ
# [mục sớm nhất của phần − 14 ngày, mục muộn nhất của phần]. Trùng y hệt (sha256) KHÔNG bị trần này giới hạn.
CANDIDATES_LIMIT = 3000
EXACT_LIMIT = 500               # số mục trùng y hệt tối đa nạp thêm qua chỉ mục băm (mỗi phần 50 mục)
PREFILTER_BITS = 28
NEAR_JACCARD = 0.75
NEAR_MIN_LEN = 24
NORM_MAX = 600               # simhash không tin được trên chuỗi quá ngắn
BROADCAST_MIN_LEN = 40
JEV_TEXT_MAX = 800
JEV_MASK_SRC = 4000             # che phần đầu này của tin RỒI mới cắt `JEV_TEXT_MAX` (không để nửa số ĐT lọt qua)
JEV_PARALLEL = 4
JEV_MAX_FAILS = 3               # lỗi liên tiếp → thôi gọi Jev cho phần còn lại của lượt

JEV_OPTIONS: dict[str, str] = {
    "spam": "rác / quảng cáo / không liên quan kinh doanh",
    "low": "ít giá trị",
    "medium": "giá trị trung bình",
    "high": "giá trị cao — cần xử lý sớm",
}
JEV_SCORE = {"spam": 5, "low": 25, "medium": 55, "high": 85}
JEV_LABEL = {"spam": "rác", "low": "ít giá trị", "medium": "trung bình", "high": "giá trị cao"}

OPPORTUNITY_EVENTS = frozenset({"AskedPrice", "OfferedSupply", "RequestedPartnership"})
ACTION_EVENTS = frozenset({"Complained", "ScheduledMeeting", "PromisedDelivery", "SentQuotation"})

_URL = re.compile(r"(https?://|www\.)\S+|\b\S+\.(com|vn|net|xyz|top|info|io)(/\S*)?\b", re.I)
_PHONE = re.compile(r"(?<!\d)(\+?84|0)\d{8,10}(?!\d)")
_REPEAT = re.compile(r"(.)\1{5,}")
_NON_WORD = re.compile(r"[^0-9a-z]+")
# Từ khoá quảng cáo / lừa đảo phổ biến (đã bỏ dấu, chữ thường).
SPAM_WORDS = (
    "khuyen mai", "giam gia soc", "trung thuong", "nhan qua", "mien phi", "click", "dang ky ngay", "vay tien",
    "vay nhanh", "giai ngan", "casino", "ca cuoc", "nha cai", "lo de", "xo so", "kiem tien", "tuyen ctv",
    "viec nhe luong cao", "inbox ngay", "ib ngay", "chuyen khoan truoc", "free ship", "sale off", "hot hot",
    "follow", "like share", "ket ban zalo", "link nhom", "tang follow",
)


# ─── chuẩn hoá + băm ───────────────────────────────────────────────────────────

def normalize(s: str) -> str:
    """Chữ thường, bỏ dấu, bỏ đường link, gộp mọi ký tự không phải chữ/số thành một khoảng trắng."""
    s = _URL.sub(" ", strip_accents(s or "").lower())
    return _NON_WORD.sub(" ", s).strip()


def text_hash(norm: str) -> bytes:
    return hashlib.sha256(norm.encode()).digest()


def _to_signed(v: int) -> int:
    return v - (1 << 64) if v >= (1 << 63) else v


def simhash64(norm: str) -> int:
    """Simhash 64 bit trên 3-gram ký tự (bigint có dấu cho Postgres)."""
    s = norm.replace(" ", "_")
    grams = [s[i:i + 3] for i in range(max(1, len(s) - 2))] if s else [""]
    acc = [0] * 64
    for g in grams:
        h = int.from_bytes(hashlib.blake2b(g.encode(), digest_size=8).digest(), "big")
        for b in range(64):
            acc[b] += 1 if (h >> b) & 1 else -1
    v = sum(1 << b for b in range(64) if acc[b] > 0)
    return _to_signed(v)


def hamming(a: int, b: int) -> int:
    return ((a ^ b) & ((1 << 64) - 1)).bit_count()


def trigrams(norm: str) -> frozenset[str]:
    s = norm.replace(" ", "_")
    return frozenset(s[i:i + 3] for i in range(max(1, len(s) - 2)))


def jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    return len(a & b) / len(a | b) if a or b else 1.0


# ─── quy tắc tất định ──────────────────────────────────────────────────────────

@dataclass
class Heuristic:
    spam: bool
    strong_spam: bool
    spam_reason: str | None
    quality: int
    reason: str


def spam_signals(raw: str) -> list[str]:
    reasons: list[str] = []
    plain = strip_accents(raw or "").lower()
    if not re.search(r"[0-9a-z]", plain):
        return ["không có nội dung chữ"]
    urls = len(_URL.findall(raw or ""))
    if urls >= 2:
        reasons += ["nhiều đường link", "nhiều đường link"]  # nặng gấp đôi
    elif urls == 1:
        reasons.append("có đường link")
    words = [w for w in SPAM_WORDS if w in plain]
    if words:
        reasons.append(f"từ ngữ quảng cáo ({', '.join(words[:3])})")
        if len(words) >= 2:
            reasons.append("nhiều từ ngữ quảng cáo")
    if len(_PHONE.findall(re.sub(r"[ .\-]", "", raw or ""))) >= 2:
        reasons.append("nhiều số điện thoại")
    letters = [c for c in (raw or "") if c.isalpha()]
    if len(letters) >= 15 and sum(1 for c in letters if c.isupper()) / len(letters) > 0.6:
        reasons.append("viết hoa toàn bộ")
    if _REPEAT.search(raw or ""):
        reasons.append("ký tự lặp dài")
    return reasons


def heuristic(raw: str, *, event_type: str | None, confidence: float | None,
              entities: dict[str, Any] | None) -> Heuristic:
    signals = spam_signals(raw)
    empty = signals == ["không có nội dung chữ"]
    points = 3 if empty else len(signals)
    spam = points >= 2
    strong = points >= 3
    parts: list[str] = []
    q = 40.0
    conf = float(confidence or 0)
    q += round(max(0.0, min(1.0, conf)) * 30)
    if conf:
        parts.append(f"độ tin {conf:.2f}")
    filled = [k for k, v in (entities or {}).items() if v not in (None, "", [], {})]
    if filled:
        q += min(15, 5 * len(filled))
        parts.append(f"có {', '.join(filled[:3])}")
    if event_type in OPPORTUNITY_EVENTS:
        q += 15
        parts.append("cơ hội bán hàng")
    elif event_type in ACTION_EVENTS:
        q += 8
        parts.append("cần phản hồi")
    n = len(normalize(raw))
    if n < 8:
        q -= 20
        parts.append("quá ngắn")
    if spam:
        q = min(q, 10)
    quality = int(max(0, min(100, round(q))))
    spam_reason = "; ".join(dict.fromkeys(signals)) if spam else None
    reason = ("Rác: " + (spam_reason or "")) if spam else (" · ".join(parts) or "ít tín hiệu")
    return Heuristic(spam, strong, spam_reason, quality, reason[:200])


# ─── dò trùng ──────────────────────────────────────────────────────────────────

@dataclass
class Candidate:
    item_id: uuid.UUID
    observed_at: datetime
    subject_id: uuid.UUID | None
    text_hash: bytes
    simhash: int
    text_len: int
    duplicate_of: uuid.UUID | None
    norm: str = ""
    _grams: frozenset[str] | None = None

    @property
    def root(self) -> uuid.UUID:
        return self.duplicate_of or self.item_id

    @property
    def grams(self) -> frozenset[str]:
        if self._grams is None:
            self._grams = trigrams(self.norm)
        return self._grams


def find_duplicate(cands: list[Candidate], *, item_id: uuid.UUID, observed_at: datetime,
                   subject_id: uuid.UUID | None, h: bytes, sim: int, text_len: int,
                   norm: str = "") -> tuple[uuid.UUID | None, str | None]:
    """Mục gốc sớm nhất trùng với mục này (exact trước, near sau) — chỉ xét mục xuất hiện TRƯỚC nó."""
    if text_len == 0:
        return None, None
    grams: frozenset[str] | None = None
    matches: list[tuple[int, datetime, uuid.UUID, str]] = []
    for c in cands:
        if c.item_id == item_id or (c.observed_at, str(c.item_id)) >= (observed_at, str(item_id)):
            continue
        short = min(text_len, c.text_len) < BROADCAST_MIN_LEN
        if short and (subject_id is None or c.subject_id != subject_id):
            continue
        if c.text_hash == h:
            matches.append((0, c.observed_at, c.root, "exact"))
        elif (norm and c.norm and min(text_len, c.text_len) >= NEAR_MIN_LEN
              and min(text_len, c.text_len) / max(text_len, c.text_len) >= NEAR_JACCARD
              and hamming(c.simhash, sim) <= PREFILTER_BITS):
            if grams is None:
                grams = trigrams(norm)
            if jaccard(grams, c.grams) >= NEAR_JACCARD:
                matches.append((1, c.observed_at, c.root, "near"))
    if not matches:
        return None, None
    best = min(matches, key=lambda t: (t[0], t[1]))
    return best[2], best[3]


# ─── cấu hình ──────────────────────────────────────────────────────────────────

async def get_settings(db: AsyncSession, org_id: uuid.UUID) -> dict[str, Any]:
    raw = (await db.execute(text("SELECT settings->'triage' FROM core.organizations WHERE id = :o"),
                            {"o": org_id})).scalar_one_or_none()
    cfg = dict(DEFAULTS)
    if isinstance(raw, dict):
        cfg.update({k: v for k, v in raw.items() if k in DEFAULTS})
    return cfg


async def save_settings(db: AsyncSession, org_id: uuid.UUID, cfg: dict[str, Any]) -> None:
    await db.execute(text("""UPDATE core.organizations
                             SET settings = jsonb_set(COALESCE(settings, '{}'::jsonb), '{triage}', CAST(:c AS jsonb),
                                                      true)
                             WHERE id = :o"""),
                     {"c": orjson.dumps({k: cfg[k] for k in DEFAULTS}).decode(), "o": org_id})


# ─── đánh dấu ──────────────────────────────────────────────────────────────────

@dataclass
class _Item:
    id: uuid.UUID
    observed_at: datetime
    subject_id: uuid.UUID | None
    event_type: str
    raw: str
    confidence: float | None
    entities: dict[str, Any]
    norm: str = ""
    heur: Heuristic | None = None
    jev: decmod.Decision | None = None
    extra: dict[str, Any] = field(default_factory=dict)


_UNITS_SQL = """
SELECT mu.id, mu.observed_at, mu.event_type, mu.conclusion, mu.confidence, mu.entities,
       COALESCE(mu.person_id, mu.group_id) AS subject_id,
       (SELECT string_agg(COALESCE(NULLIF(r.body_text, ''), ev.quote, ''), E'\\n' ORDER BY ev.raw_received_at)
          FROM clean.evidence ev
          LEFT JOIN raw.events r ON r.id = ev.raw_event_id AND r.received_at = ev.raw_received_at
         WHERE ev.meaning_unit_id = mu.id) AS raw_text
FROM clean.meaning_units mu
WHERE mu.org_id = :o AND mu.superseded_by IS NULL {ids}
  AND NOT EXISTS (SELECT 1 FROM refinery.item_marks m
                  WHERE m.item_type = 'unit' AND m.item_id = mu.id AND m.version >= :v)
ORDER BY mu.observed_at, mu.id
LIMIT :n
"""


async def _candidates(db: AsyncSession, org_id: uuid.UUID, since: datetime, until: datetime,
                      hashes: list[bytes] | None = None) -> list[Candidate]:
    """Mục đã đánh dấu có thể là bản gốc của các mục trong phần đang xét.

    Trần 3000 (rà soát v0.1.27):
    - Chỉ lấy mục trong [since, until] — `until` = mục MUỘN NHẤT của phần: mục xuất hiện sau đó không bao giờ là bản
      gốc (`find_duplicate` chỉ xét mục TRƯỚC), nên không tốn chỗ trong trần khi quét vét mục cũ đến muộn.
    - Truy vấn đi chỉ mục `item_marks_observed_idx (org_id, observed_at DESC)` (0019): quét ngược theo thời gian và
      dừng ở 3000 dòng — chi phí cố định dù bảng lớn. So trùng gần là vòng Python 50 × 3000 (đã lọc thô bằng độ dài
      + simhash trước Jaccard) ≈ vài chục ms mỗi phần.
    - Vượt trần (tổ chức > 3000 mục trong ~14 ngày): trùng GẦN với mục cũ hơn 3000 mục gần nhất có thể bị bỏ sót
      (chấp nhận — tin rải thường lặp lại trong vài giờ). Trùng Y HỆT thì không: nạp thêm theo `text_hash` qua chỉ mục
      `item_marks_hash_idx (org_id, text_hash)` (tối đa `EXACT_LIMIT`), bất kể trần.
    """
    rows = list((await db.execute(text("""
        SELECT item_id, observed_at, subject_id, text_hash, simhash, text_len, duplicate_of, norm_text
        FROM refinery.item_marks WHERE org_id = :o AND item_type = 'unit' AND observed_at >= :s AND observed_at <= :u
        ORDER BY observed_at DESC LIMIT :n"""),
        {"o": org_id, "s": since, "u": until, "n": CANDIDATES_LIMIT})).all())
    if len(rows) >= CANDIDATES_LIMIT and hashes:
        log.info("Lọc đầu: chạm trần %d mục so trùng (tổ chức %s) — bổ sung trùng y hệt theo băm", CANDIDATES_LIMIT,
                 org_id)
        seen = {r.item_id for r in rows}
        extra = (await db.execute(text("""
            SELECT item_id, observed_at, subject_id, text_hash, simhash, text_len, duplicate_of, norm_text
            FROM refinery.item_marks
            WHERE org_id = :o AND item_type = 'unit' AND text_hash = ANY(:hs) AND observed_at >= :s
              AND observed_at <= :u
            ORDER BY observed_at LIMIT :n"""),
            {"o": org_id, "hs": list(dict.fromkeys(hashes)), "s": since, "u": until, "n": EXACT_LIMIT})).all()
        rows += [r for r in extra if r.item_id not in seen]
    return [Candidate(r.item_id, r.observed_at, r.subject_id, bytes(r.text_hash), int(r.simhash), int(r.text_len),
                      r.duplicate_of, r.norm_text or "") for r in rows]


def jev_context(event_type: str) -> str:
    return ("Đánh giá tin nhắn khách/đối tác gửi doanh nghiệp: rác hay giá trị kinh doanh "
            f"(loại sự kiện: {event_type}).")


async def ask_jev_batch(dec: decmod.Decider, jobs: list[tuple[str, str]]) -> list[decmod.Decision | None]:
    """Hỏi Jev nhãn rác/giá trị cho từng (văn bản, loại sự kiện) — song song `JEV_PARALLEL`, trần 1,5 s mỗi câu (do
    `JevDecider`), `JEV_MAX_FAILS` lỗi liên tiếp ngắt cả lượt (phần còn lại không hỏi nữa). Kết quả cùng thứ tự `jobs`;
    `None` = Jev không quyết định được (lỗi/chậm/độ tin < 0,5/bị ngắt) → bên gọi dùng quy tắc.

    Chỉ gửi văn bản ĐÃ CHE (v0.1.55, QD-12): che phần đầu `JEV_MASK_SRC` ký tự rồi mới cắt `JEV_TEXT_MAX`."""
    sem = asyncio.Semaphore(JEV_PARALLEL)
    fails = 0
    out: list[decmod.Decision | None] = [None] * len(jobs)

    async def one(i: int, raw: str, event_type: str) -> None:
        nonlocal fails
        if fails >= JEV_MAX_FAILS:
            return
        async with sem:
            if fails >= JEV_MAX_FAILS:
                return
            safe = str(mask_for_model(raw[:JEV_MASK_SRC]))[:JEV_TEXT_MAX]
            d = await dec.classify(safe, JEV_OPTIONS, jev_context(event_type))
        if d is None:
            fails += 1
        else:
            fails = 0
            out[i] = d

    await asyncio.gather(*(one(i, raw, et) for i, (raw, et) in enumerate(jobs)))
    return out


async def _ask_jev(dec: decmod.Decider, items: list[_Item]) -> None:
    for it, d in zip(items, await ask_jev_batch(dec, [(it.raw, it.event_type) for it in items]), strict=True):
        if d is not None:
            it.jev = d


def _final(it: _Item) -> dict[str, Any]:
    h = it.heur
    assert h is not None
    if it.jev is not None and it.jev.value in JEV_SCORE:
        key = it.jev.value
        spam = key == "spam" or h.strong_spam
        quality = round(0.6 * JEV_SCORE[key] + 0.4 * h.quality)
        if spam:
            quality = min(quality, 10)
        conf = f" {it.jev.confidence:.2f}" if it.jev.confidence is not None else ""
        spam_reason = (f"Jev: rác{conf}" if key == "spam" else None) or h.spam_reason
        reason = f"Jev: {JEV_LABEL[key]}{conf} · {h.reason}"
        return {"is_spam": spam, "spam_reason": spam_reason if spam else None, "quality": int(quality),
                "reason": reason[:240], "source": "jev", "latency_ms": it.jev.latency_ms}
    return {"is_spam": h.spam, "spam_reason": h.spam_reason, "quality": h.quality, "reason": h.reason,
            "source": "heuristic", "latency_ms": None}


async def mark_units(db: AsyncSession, org_id: uuid.UUID, unit_ids: list[uuid.UUID] | None = None, *,
                     decider: decmod.Decider | None = None, limit: int = CHUNK) -> int:
    """Đánh dấu tối đa `limit` đơn vị ý nghĩa chưa có dấu (idempotent). Bên gọi commit."""
    await db.execute(text("SELECT pg_advisory_xact_lock(hashtext('triage:' || :o))"), {"o": str(org_id)})
    # Hook: đúng các mục vừa sinh. Quét vét: chỉ cửa sổ gần đây (dùng chỉ mục (org_id, observed_at) của 0003).
    ids_sql = ("AND mu.id = ANY(:ids)" if unit_ids is not None
               else "AND mu.observed_at >= now() - make_interval(days => :w)")
    params: dict[str, Any] = {"o": org_id, "v": VERSION, "n": limit, "w": SWEEP_DAYS}
    if unit_ids is not None:
        if not unit_ids:
            return 0
        params["ids"] = unit_ids
    rows = (await db.execute(text(_UNITS_SQL.format(ids=ids_sql)), params)).all()
    if not rows:
        return 0
    items = [_Item(r.id, r.observed_at, r.subject_id, r.event_type, (r.raw_text or "").strip() or r.conclusion,
                   float(r.confidence) if r.confidence is not None else None,
                   r.entities if isinstance(r.entities, dict) else {}) for r in rows]
    for it in items:
        it.norm = normalize(it.raw)
        it.heur = heuristic(it.raw, event_type=it.event_type, confidence=it.confidence, entities=it.entities)
    if decider is not None and decider.name != "llm":
        await _ask_jev(decider, items)
    hashes = [text_hash(it.norm) for it in items]
    cands = await _candidates(db, org_id, min(i.observed_at for i in items) - timedelta(days=WINDOW_DAYS),
                              max(i.observed_at for i in items), hashes)
    n = 0
    for it, h in zip(items, hashes, strict=True):
        sim, norm = simhash64(it.norm), it.norm[:NORM_MAX]
        dup, kind = find_duplicate(cands, item_id=it.id, observed_at=it.observed_at, subject_id=it.subject_id,
                                   h=h, sim=sim, text_len=len(it.norm), norm=norm)
        f = _final(it)
        assert it.heur is not None
        res = await db.execute(text("""
            INSERT INTO refinery.item_marks (org_id, item_type, item_id, observed_at, subject_id, text_hash, simhash,
                                             text_len, norm_text, duplicate_of, duplicate_kind, is_spam, spam_reason,
                                             quality, reason, source, heuristic_quality, heuristic_spam, latency_ms,
                                             version)
            VALUES (:o, 'unit', :i, :obs, :subj, :h, :sim, :len, :norm, :dup, :kind, :spam, :sr, :q, :r, :src, :hq, :hs,
                    :ms, :v)
            ON CONFLICT (item_type, item_id) DO UPDATE SET
              duplicate_of = EXCLUDED.duplicate_of, duplicate_kind = EXCLUDED.duplicate_kind,
              is_spam = EXCLUDED.is_spam, spam_reason = EXCLUDED.spam_reason, quality = EXCLUDED.quality,
              reason = EXCLUDED.reason, source = EXCLUDED.source, heuristic_quality = EXCLUDED.heuristic_quality,
              heuristic_spam = EXCLUDED.heuristic_spam, latency_ms = EXCLUDED.latency_ms, version = EXCLUDED.version,
              text_hash = EXCLUDED.text_hash, simhash = EXCLUDED.simhash, text_len = EXCLUDED.text_len,
              norm_text = EXCLUDED.norm_text,
              marked_at = now()
            WHERE refinery.item_marks.version < EXCLUDED.version"""),
            {"o": org_id, "i": it.id, "obs": it.observed_at, "subj": it.subject_id, "h": h, "sim": sim,
             "len": len(it.norm), "norm": norm, "dup": dup, "kind": kind, "spam": f["is_spam"], "sr": f["spam_reason"],
             "q": f["quality"], "r": f["reason"], "src": f["source"], "hq": it.heur.quality,
             "hs": it.heur.spam, "ms": f["latency_ms"], "v": VERSION})
        n += res.rowcount or 0  # type: ignore[attr-defined]
        cands.append(Candidate(it.id, it.observed_at, it.subject_id, h, sim, len(it.norm), dup, norm))
    return n


async def run_org(sm: async_sessionmaker[AsyncSession], org_id: uuid.UUID,
                  unit_ids: list[uuid.UUID] | None = None) -> int:
    """Một lượt cho một tổ chức: tôn trọng cờ bật/tắt, nạp Decider một lần, commit theo từng phần."""
    async with sm() as db:
        cfg = await get_settings(db, org_id)
        if not cfg["enabled"]:
            return 0
        dec: decmod.Decider = await decmod.load_decider(db, org_id) if cfg["use_jev"] else decmod.LlmDecider()
    total = 0
    for _ in range(MAX_ROUNDS):
        async with sm() as db:
            n = await mark_units(db, org_id, unit_ids, decider=dec, limit=CHUNK)
            await db.commit()
        total += n
        if n < CHUNK:
            break
    return total


async def summary(db: AsyncSession, org_id: uuid.UUID, days: int = 7, *,
                  scope_sql: tuple[str, dict[str, Any]] | None = None, scope: str = "all") -> dict[str, Any]:
    """Số liệu lọc đầu `days` ngày. `scope_sql` = biểu thức phạm vi `queue.read` trên dòng `biz.inbox_items` bí danh
    `i` (`gh.biz.queue.service.item_scope_sql`) — None/`TRUE` = cả tổ chức. Phạm vi hẹp (Nhân viên: `assigned`,
    Quản lý: `team`) chỉ đếm mục mình thấy được trong Hộp thư (v0.1.27) — không lộ số liệu toàn tổ chức."""
    cfg = await get_settings(db, org_id)
    where, sparams = scope_sql or ("TRUE", {})
    scoped = where != "TRUE"
    m_scope = (f" AND EXISTS (SELECT 1 FROM biz.inbox_items i WHERE i.org_id = :o AND i.item_type = 'unit'"
               f" AND i.item_id = m.item_id AND {where})") if scoped else ""
    mu_scope = (f" AND EXISTS (SELECT 1 FROM biz.inbox_items i WHERE i.org_id = :o AND i.item_type = 'unit'"
                f" AND i.item_id = mu.id AND {where})") if scoped else ""
    r = (await db.execute(text("""
        SELECT count(*) AS total,
               count(*) FILTER (WHERE duplicate_of IS NOT NULL) AS duplicates,
               count(*) FILTER (WHERE duplicate_kind = 'exact') AS exact,
               count(*) FILTER (WHERE duplicate_kind = 'near') AS near,
               count(*) FILTER (WHERE is_spam) AS spam,
               count(*) FILTER (WHERE NOT is_spam AND duplicate_of IS NULL AND quality < :m) AS low_score,
               count(*) FILTER (WHERE NOT is_spam AND duplicate_of IS NULL AND quality >= :m) AS kept,
               round(avg(quality)) AS avg_quality,
               count(*) FILTER (WHERE source = 'jev') AS jev,
               round(avg(latency_ms) FILTER (WHERE source = 'jev')) AS jev_ms,
               count(*) FILTER (WHERE source = 'jev' AND is_spam = heuristic_spam) AS jev_agree
        FROM refinery.item_marks m
        WHERE m.org_id = :o AND m.observed_at > now() - make_interval(days => :d)""" + m_scope),
        {"o": org_id, "d": days, "m": int(cfg["min_score"]), **sparams})).one()
    pending = (await db.execute(text("""
        SELECT count(*) FROM clean.meaning_units mu
        WHERE mu.org_id = :o AND mu.superseded_by IS NULL AND mu.observed_at > now() - make_interval(days => :d)
          AND NOT EXISTS (SELECT 1 FROM refinery.item_marks m WHERE m.item_type = 'unit' AND m.item_id = mu.id)"""
        + mu_scope), {"o": org_id, "d": min(days, SWEEP_DAYS), **sparams})).scalar_one()
    # (cửa sổ `pending` ≤ SWEEP_DAYS: mục ngoài cửa sổ quét vét không bao giờ được lọc → không tính "chờ lọc")
    jev = int(r.jev or 0)
    return {
        "days": days, "scope": scope if scoped else "all",
        "enabled": bool(cfg["enabled"]), "min_score": int(cfg["min_score"]),
        "use_jev": bool(cfg["use_jev"]), "prefilter": bool(cfg["prefilter"]),
        "total": int(r.total or 0), "kept": int(r.kept or 0), "duplicates": int(r.duplicates or 0),
        "exact_duplicates": int(r.exact or 0), "near_duplicates": int(r.near or 0), "spam": int(r.spam or 0),
        "low_score": int(r.low_score or 0), "pending": int(pending or 0),
        "avg_quality": int(r.avg_quality) if r.avg_quality is not None else None,
        "jev": {"count": jev, "heuristic_count": int(r.total or 0) - jev,
                "avg_latency_ms": int(r.jev_ms) if r.jev_ms is not None else None,
                "spam_agreement": round(int(r.jev_agree or 0) / jev, 3) if jev else None},
    }


async def value_summary(db: AsyncSession, org_id: uuid.UUID, days: int = 7) -> dict[str, Any]:
    """Số đo "Jev giúp được gì" `days` ngày qua — CHỈ ĐẾM SỐ LẦN, không quy ra tiền (ai_cost cấm bịa giá).

    - `filtered`: tin rác/trùng đã được lọc ẩn (`refinery.item_marks`: `is_spam` hoặc có `duplicate_of`) + tin J2 đã bỏ
      qua trước khi trích xuất (`refinery.event_state`: discarded, `detail.discarded_by = 'prefilter'`);
    - `spam_blocked`: phần rác của `filtered` (cả hai nguồn);
    - `calls_saved`: số lần trích xuất không phải gửi model (mỗi tin J2 bỏ qua là một lần) + số lượt hỏi Gen được trả
      câu mẫu mà không gọi model (Action Log `gen.decide` với `value = 'out_of_scope'`, do G3 ghi);
    - `jev_on`: có nguồn Jev (`system_one`) đang bật kèm khoá."""
    days = max(1, int(days))
    marks = (await db.execute(text("""
        SELECT count(*) FILTER (WHERE is_spam OR duplicate_of IS NOT NULL) AS hidden,
               count(*) FILTER (WHERE is_spam) AS spam
        FROM refinery.item_marks
        WHERE org_id = :o AND item_type = 'unit' AND observed_at > now() - make_interval(days => :d)"""),
        {"o": org_id, "d": days})).one()
    skipped = (await db.execute(text("""
        SELECT count(*) AS n, count(*) FILTER (WHERE detail->>'reason' LIKE 'spam_rule%') AS spam
        FROM refinery.event_state
        WHERE org_id = :o AND state = 'discarded' AND detail->>'discarded_by' = 'prefilter'
          AND updated_at > now() - make_interval(days => :d)"""), {"o": org_id, "d": days})).one()
    out_of_scope = (await db.execute(text("""
        SELECT count(*) FROM ops.action_log
        WHERE org_id = :o AND action = 'gen.decide' AND detail->>'value' = 'out_of_scope'
          AND at > now() - make_interval(days => :d)"""), {"o": org_id, "d": days})).scalar_one()
    jev_on = (await db.execute(text("""
        SELECT EXISTS (SELECT 1 FROM agent.providers p
                       JOIN agent.provider_keys k ON k.provider_id = p.id AND k.is_enabled
                       WHERE p.org_id = :o AND p.kind = 'system_one' AND p.is_enabled)"""),
                              {"o": org_id})).scalar_one()
    return {"filtered": int(marks.hidden or 0) + int(skipped.n or 0),
            "spam_blocked": int(marks.spam or 0) + int(skipped.spam or 0),
            "calls_saved": int(skipped.n or 0) + int(out_of_scope or 0),
            "jev_on": bool(jev_on)}


# ─── worker: hook sau sàng lọc + quét định kỳ ─────────────────────────────────

async def triage_hook(ctx: HookCtx) -> None:
    if ctx.unit_ids:
        await run_org(ctx.sm, ctx.org_id, ctx.unit_ids)


async def triage_sweep(ctx: dict[str, Any]) -> dict[str, int]:
    from gh.db import sessionmaker

    sm = sessionmaker()
    async with sm() as db:
        orgs = (await db.execute(text("SELECT id FROM core.organizations"))).scalars().all()
    out: dict[str, int] = {}
    for org in orgs:
        try:
            out[str(org)] = await run_org(sm, org)
        except Exception:  # noqa: BLE001 — một tổ chức lỗi không chặn tổ chức khác
            log.exception("Lọc đầu lỗi cho tổ chức %s", org)
    return out


HOOKS: list[Hook] = [Hook("triage", triage_hook, timeout_s=300.0)]
JOBS: list[CronJob] = [(triage_sweep, {"minute": set(range(2, 60, 5))})]
