# ruff: noqa: E501
"""J2 — lọc trước khi trích xuất (v0.1.55, G4): bỏ qua tin KHÔNG CẦN gửi model để tiết kiệm lượt trích xuất.

Chạy trong `gh.refinery.runner` sau bước quy tắc nhiễu, trước vòng `extract.chunks`. Tin bị bỏ qua KHÔNG bị xoá: trạng
thái `discarded` kèm `detail.discarded_by = 'prefilter'` + lý do, Sếp xem lại ở "Tin đã bỏ qua"
(`GET /refinery/triage/skipped`). Hàm `decide` THUẦN (không DB, không mạng) — nối dữ liệu ở runner.

Luật BỎ QUA (chặt — "Boss không muốn rủi ro"; chỉ bỏ khi chắc):
1. **Trùng hẳn** (`exact_dup`): chuẩn hoá + sha256 (`text_hash`) trùng một tin đã thấy (trong lô hoặc trong dấu
   `item_marks`) CỦA CÙNG NGƯỜI GỬI Ở CÙNG NƠI (cùng nhóm / cùng hội thoại riêng) TRONG `EXACT_DUP_HOURS` GIỜ — tức
   người đó gửi lặp y hệt (bấm gửi hai lần, dán lại, tin rải lặp). Cửa sổ NGẮN (khác 14 ngày ẩn tin ở Hộp thư): khách
   nhắc lại y nguyên câu báo giá sau nhiều ngày vì chưa ai trả lời là TÍN HIỆU theo dõi, không phải bản trùng — vẫn
   được trích xuất. Hai người khác nhau, hoặc hai nhóm khác nhau, gửi cùng một câu là hai tín hiệu riêng (khách A và
   khách B cùng hỏi "Cần 3 container thép cuộn, giá bao nhiêu vậy em?") ⇒ KHÔNG bỏ — chặt hơn chỉ so băm, vì mất
   một khách hỏi giá đắt hơn một lượt trích xuất. Người gửi không rõ ⇒ không bao giờ là bản trùng.
   Tin NGẮN (< `triage.BROADCAST_MIN_LEN` ký tự sau chuẩn hoá, gồm tin chỉ có biểu tượng cảm xúc = rỗng) cũng không
   bao giờ là "trùng hẳn" (cùng khuôn `triage.find_duplicate`).
2. **Quy tắc chấm rác VÀ Jev cũng chấm rác** (`spam_rule_jev`).
3. **Quy tắc chấm rác VÀ chưa có Jev** (`spam_rule_nojev`) — `jev_labels is None`.

KHÔNG bao giờ bỏ:
- Tin TAG trực tiếp (`tagged`): là yêu cầu rõ ràng gửi cho Gen/agent — luôn đi tiếp.
- Jev MỘT MÌNH chấm rác (quy tắc không): chỉ `lower_priority` — tin vẫn được trích xuất, chỉ xếp cuối lô.
- Jev ĐÃ cấu hình nhưng lỗi / chậm / độ tin thấp với tin đó (nhãn `None`): coi như chỉ có quy tắc và THẬN TRỌNG — chỉ
  bỏ khi trùng hẳn; quy tắc chấm rác một mình không đủ (Jev hỏng không được làm mất tin). Tin như vậy cũng chỉ
  `lower_priority`.
Thứ tự ưu tiên lý do: `exact_dup` > `spam_rule_jev` > `spam_rule_nojev`.

v0.1.76 (L2, spec Bước 1 r6.6.1 §4D.2 tầng (a)): thêm LUẬT RẺ — `scan_text(text, source_field) -> [Candidate]` (0 token): từ
vựng INT-19, cờ `credentials_entered` / `account_takeover` / `money_request` / `emergency`, doạ bỏ/chê công khai, mốc
"hôm nay", "gấp/sập/hạn chót", STK/link/SĐT/số tiền. `Candidate.kind` theo bảng cờ (r6.4, CNS-131): `money_request` MỘT MÌNH
không gán loại. Phần "người" (`victims`, `impersonated`) cần ngữ cảnh người gửi nên do `enrich(cands, SenderCtx)` đặt (r6.6,
CNS-149). Hàm thuần, không DB, không mạng. L5 gọi `scan_text` trên chữ OCR/transcript.
"""

import re
import uuid
from collections.abc import Collection, Hashable, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any, Literal, TypeVar

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gh.refinery import triage
from gh.textnorm import strip_accents

Reason = Literal["exact_dup", "spam_rule_jev", "spam_rule_nojev"]
T = TypeVar("T")

#: Chữ cho người đọc (web "Tin đã bỏ qua") — khoá = `Reason`.
REASON_TEXT: dict[str, str] = {
    "exact_dup": "Trùng hẳn một tin đã có",
    "spam_rule_jev": "Rác — quy tắc và Jev cùng chấm rác",
    "spam_rule_nojev": "Rác — quy tắc chấm rác (chưa có Jev)",
}
JEV_SPAM_LABEL = "spam"
#: Cửa sổ "gửi lặp y hệt" tính bằng GIỜ (không phải `triage.WINDOW_DAYS` = 14 ngày): chỉ bắt lỗi bấm gửi hai lần / dán
#: lại. Lặp lại sau cửa sổ này được coi là nhắc việc và đi tiếp tới trích xuất.
EXACT_DUP_HOURS = 6.0


@dataclass(frozen=True)
class PrefilterItem:
    """Một tin đưa vào `decide`. `sender`/`place` (người gửi, nhóm hoặc hội thoại riêng) quyết định "trùng hẳn" là
    cùng người, cùng nơi; `sender=None` (không rõ) ⇒ không bao giờ tính trùng. `tagged` = tin tag trực tiếp. `at` = thời
    điểm tin xảy ra: bản lặp TRONG LÔ chỉ là "trùng hẳn" khi cách bản trước ≤ `EXACT_DUP_HOURS` (thiếu `at` ⇒ coi là
    trong cửa sổ)."""

    text: str
    sender: Hashable | None = None
    place: Hashable | None = None
    tagged: bool = False
    at: datetime | None = None


@dataclass(frozen=True)
class PrefilterResult:
    skip: bool = False
    reason: Reason | None = None
    lower_priority: bool = False


def reason_text(reason: str | None) -> str:
    return REASON_TEXT.get(reason or "", "Đã bỏ qua trước khi trích xuất")


def exact_key(item: PrefilterItem | str) -> tuple[bytes, Hashable, Hashable | None] | None:
    """Khoá "trùng hẳn" = (băm văn bản chuẩn hoá, người gửi, nơi gửi) — None nếu không đủ điều kiện (tin ngắn/rỗng,
    người gửi không rõ). Chuỗi trần = tin của một người gửi vô danh chung (dùng cho test/gọi nhanh)."""
    it = PrefilterItem(item, sender="") if isinstance(item, str) else item
    norm = triage.normalize(it.text)
    if len(norm) < triage.BROADCAST_MIN_LEN or it.sender is None:
        return None
    return triage.text_hash(norm), it.sender, it.place


def rule_spam(raw: str, *, jev_present: bool) -> bool:
    """Quy tắc tất định có chấm tin này là rác không? Khi KHÔNG có Jev đối chiếu, chỉ tin rác CHẮC (≥ 3 tín hiệu —
    `Heuristic.strong_spam`) mới tính, vì lúc đó quy tắc là cổng duy nhất; có Jev thì 2 tín hiệu (`spam`) đủ để
    Jev xác nhận."""
    h = triage.heuristic(raw, event_type=None, confidence=None, entities=None)
    return h.spam if jev_present else h.strong_spam


def decide(items: Sequence[PrefilterItem | str], *, rules_spam: Sequence[bool],
           jev_labels: Sequence[str | None] | None, dup_index: Collection[Hashable]) -> list[PrefilterResult]:
    """Quyết định từng tin (cùng thứ tự `items`).

    - `items`: tin thô (`PrefilterItem`, hoặc chuỗi trần = người gửi vô danh chung);
    - `rules_spam[i]`: quy tắc có chấm tin i là rác không (`rule_spam`);
    - `jev_labels`: None = CHƯA có Jev; ngược lại nhãn Jev của từng tin (`'spam'|'low'|'medium'|'high'`) hoặc None =
      Jev lỗi/chậm với tin đó;
    - `dup_index`: tập khoá `exact_key` của các tin đã thấy trước lô này. Lần xuất hiện ĐẦU TIÊN trong lô giữ lại,
      các lần sau trùng hẳn (cùng người, cùng nơi, trong `EXACT_DUP_HOURS` giờ) mới bị bỏ."""
    n = len(items)
    if len(rules_spam) != n or (jev_labels is not None and len(jev_labels) != n):
        raise ValueError("items, rules_spam, jev_labels phải cùng độ dài")
    seen: dict[Hashable, datetime | None] = {}      # khoá → thời điểm lần xuất hiện gần nhất trong lô
    out: list[PrefilterResult] = []
    for i, raw in enumerate(items):
        it = PrefilterItem(raw, sender="") if isinstance(raw, str) else raw
        key = exact_key(it)
        dup = key is not None and (key in dup_index or (key in seen and _within_window(seen[key], it.at)))
        if key is not None:
            seen[key] = it.at
        if it.tagged:                                # tag trực tiếp: luôn đi tiếp
            out.append(PrefilterResult())
            continue
        if dup:
            out.append(PrefilterResult(True, "exact_dup", False))
            continue
        rule = bool(rules_spam[i])
        if jev_labels is None:                       # chưa có Jev: quy tắc là cổng duy nhất
            out.append(PrefilterResult(True, "spam_rule_nojev", False) if rule else PrefilterResult())
            continue
        label = jev_labels[i]
        if label is None:                            # Jev lỗi với tin này → thận trọng: không bỏ, chỉ hạ ưu tiên
            out.append(PrefilterResult(False, None, rule))
        elif label == JEV_SPAM_LABEL:
            out.append(PrefilterResult(True, "spam_rule_jev", False) if rule else PrefilterResult(False, None, True))
        else:
            out.append(PrefilterResult())
    return out


def _within_window(a: datetime | None, b: datetime | None) -> bool:
    """Hai thời điểm cách nhau ≤ `EXACT_DUP_HOURS` giờ? Thiếu một bên ⇒ True (hành vi cũ: cùng lô là gửi lặp)."""
    if a is None or b is None:
        return True
    return abs((b - a).total_seconds()) <= EXACT_DUP_HOURS * 3600


def lower_last(items: Sequence[T], results: Sequence[PrefilterResult]) -> list[T]:
    """Giữ tin không bị bỏ; tin `lower_priority` xếp CUỐI lô (J2 "rẻ trước, đắt sau"), thứ tự còn lại giữ nguyên."""
    kept = [(it, r) for it, r in zip(items, results, strict=True) if not r.skip]
    return [it for it, r in kept if not r.lower_priority] + [it for it, r in kept if r.lower_priority]


async def dup_index(db: AsyncSession, org_id: uuid.UUID, hashes: Collection[bytes],
                    event_ids: Collection[uuid.UUID], *, window_hours: float | None = None) -> set[Hashable]:
    """Khoá "trùng hẳn" `(băm, người, nhóm)` đã có dấu `refinery.item_marks` trong `EXACT_DUP_HOURS` giờ gần đây (hoặc
    `window_hours`), của các đơn vị ý nghĩa có người gửi rõ. Loại các dấu của chính những tin đang xử lý (chạy lại cùng
    một tin không được tự coi mình là bản trùng)."""
    if not hashes:
        return set()
    rows = (await db.execute(text("""
        SELECT DISTINCT m.text_hash, mu.person_id, mu.group_id
        FROM refinery.item_marks m
        JOIN clean.meaning_units mu ON mu.id = m.item_id AND mu.observed_at = m.observed_at
        WHERE m.org_id = :o AND m.item_type = 'unit' AND m.text_hash = ANY(:hs) AND mu.person_id IS NOT NULL
          AND m.observed_at > now() - make_interval(secs => :w)
          AND NOT EXISTS (SELECT 1 FROM clean.evidence ev
                          WHERE ev.meaning_unit_id = m.item_id AND ev.raw_event_id = ANY(:eids))"""),
        {"o": org_id, "hs": list(hashes), "eids": list(event_ids),
         "w": (EXACT_DUP_HOURS if window_hours is None else window_hours) * 3600})).all()
    return {(bytes(r.text_hash), r.person_id, r.group_id) for r in rows}


# ═══ Tầng (a) — luật rẻ (v0.1.76, L2): `Candidate`, `scan_text`, `enrich` ═══════════════════════════════════════════════

SOURCE_FIELDS = ("body", "transcript", "ocr", "caption")
#: Cờ → loại tín hiệu (spec §4D.2, r6.4 CNS-131). Cờ không có trong bảng (kể cả `money_request` một mình) ⇒ `kind = None`.
KIND_BY_FLAG: dict[str, str] = {"credentials_entered": "rui_ro_lua_dao", "account_takeover": "rui_ro_lua_dao",
                                "emergency": "suc_khoe_an_toan"}
GATE_BY_FLAG: dict[str, str] = {"credentials_entered": "lua_dao_dang_dien_ra", "account_takeover": "lua_dao_dang_dien_ra",
                                "emergency": "an_toan", "said_will_leave": "khach_che_cong_khai_doa_bo",
                                "public": "khach_che_cong_khai_doa_bo"}
#: Cổng thuộc lớp "an toàn" (trần lượt an toàn riêng, không bao giờ bị cắt — §4D.5) và lớp "cổng" (4 cổng còn lại).
SAFETY_GATES = frozenset({"lua_dao_dang_dien_ra", "an_toan"})
STRANGER_DAYS = 7.0
TEMPLATE_MIN_N = 3
#: Cờ rủi ro: tin có bất kỳ cờ nào dưới đây KHÔNG BAO GIỜ bị bỏ vì "không cần model"/"mẫu lặp" (chỉ hạ ưu tiên).
RISK_FLAGS = frozenset({"scam_keyword", "link", "money_request", "account_number", "phone", "credentials_entered",
                        "account_takeover", "emergency"})
BIG_DEAL_VND = 100_000_000
MAX_SCAN_CHARS = 20_000


@dataclass(frozen=True)
class Candidate:
    """Một ứng viên do luật (a) đánh dấu — KHÔNG mang chữ của tin (chỉ `span` trong trường nguồn) để không rò OTP/STK vào DB.

    `kind`: loại tín hiệu theo bảng cờ (None ⇒ không gán loại). `gate_candidate`: cổng INT-19 mà ứng viên có thể mở (bật lượt
    chạy ngay). `hard_rule_hit`: mã luật cứng trên span (`credentials_entered`, `emergency`, `stk_changed_money_request`).
    `victims`/`impersonated`: mã người (`u_…`) do `enrich` đặt, KHÔNG do model."""

    flag: str
    source_field: str = "body"
    span: tuple[int, int] = (0, 0)
    kind: str | None = None
    gate_candidate: str | None = None
    hard_rule_hit: str | None = None
    first_person: bool = False
    victims: tuple[str, ...] = ()
    impersonated: tuple[str, ...] = ()
    details: dict[str, Any] = field(default_factory=dict, compare=False)

    @property
    def immediate(self) -> bool:
        """Ứng viên của 6 cổng INT-19 ⇒ chạy lượt NGAY, không chờ nhịp (§4D.1, §4D.5)."""
        return self.gate_candidate is not None

    @property
    def safety(self) -> bool:
        return self.gate_candidate in SAFETY_GATES

    def to_json(self) -> dict[str, Any]:
        """Dạng lưu ở `refinery.event_state.detail` — không có chữ của tin, không có số tài khoản thô."""
        det = {k: v for k, v in self.details.items() if k != "account_numbers"}
        return {"flag": self.flag, "field": self.source_field, "span": list(self.span), "kind": self.kind,
                "gate": self.gate_candidate, "hard": self.hard_rule_hit, "first_person": self.first_person,
                "victims": list(self.victims), "impersonated": list(self.impersonated), "details": det}

    @classmethod
    def from_json(cls, d: dict[str, Any]) -> "Candidate":
        sp = d.get("span") or [0, 0]
        return cls(flag=str(d["flag"]), source_field=str(d.get("field") or "body"), span=(int(sp[0]), int(sp[1])),
                   kind=d.get("kind"), gate_candidate=d.get("gate"), hard_rule_hit=d.get("hard"),
                   first_person=bool(d.get("first_person")), victims=tuple(d.get("victims") or ()),
                   impersonated=tuple(d.get("impersonated") or ()), details=dict(d.get("details") or {}))


@dataclass(frozen=True)
class SenderCtx:
    """Dữ kiện THÔ về người gửi mà `enrich` cần (spec r6.6 CNS-149: mạo danh chỉ khi ID mới / vừa đổi tên / chưa là thực thể
    đã biết). `joined_days` None = không biết ⇒ không tính "người lạ mới vào"."""

    sender_ref: str | None = None
    name_collision_with: str | None = None          # mã người KHÁC cùng tên hiển thị trong nhóm
    joined_days: float | None = None
    display_name_changed_recent: bool = False
    known_stk_digests: frozenset[str] = frozenset()  # STK đã biết của người này (digest có khoá)
    known_entity: bool = False                       # người này đã là thực thể/có lịch sử tin ≥ 7 ngày (người gọi tra `raw.events`)


# ─── chuẩn hoá hai bản: `fold` (bỏ dấu, chữ thường) và `lc` (chữ thường, GIỮ dấu — phân biệt cháy/chạy, gấp/gặp) ───────────

def _views(text: str) -> tuple[str, list[int], str]:
    """(fold, ánh xạ vị trí fold → vị trí gốc, lc). `lc` có cùng độ dài bản gốc (chữ thường từng ký tự)."""
    folded: list[str] = []
    idx: list[int] = []
    lc: list[str] = []
    for i, ch in enumerate(text):
        low = ch.lower()
        lc.append(low if len(low) == 1 else ch)
        for c in strip_accents(ch).lower():
            folded.append(c)
            idx.append(i)
    return "".join(folded), idx, "".join(lc)


_GAP = r"(?:[\s,]+[a-z0-9]+)"
_SEP = r"[\s,]+"
_MONEY_AMT = r"\d[\d.,]*\s*(?:tr|trieu|k|nghin|ngan|ty|d|dong|vnd)\b"


def _gap(lo: int, hi: int) -> str:
    return rf"{_GAP}{{{lo},{hi}}}?"


_SECRET = (r"(?:ma otp|otp|ma xac (?:thuc|minh|nhan)(?!\s+(?:don|hang|giao|dat|van))|mat khau|password|passwd|"
           r"pass(?!\s+(?:wifi|wi-fi|mang|phong|cua|do|lai|hang|tu|ghe|ban|xe|nha|dien thoai|bai|mon))|tai khoan va mat khau|so the|"
           r"ma the|the (?:atm|ngan hang|tin dung|visa|master|ghi no)|cvv|cvc|ma pin|pin|thong tin the|"
           r"thong tin (?:ngan hang|dang nhap)|ma bao mat|ma bi mat|mat ma|ma 6 so|ma gom 6 so)")
# [Gen chốt] `pass` trần là bí mật, trừ ngữ cảnh vô hại ngay sau nó (pass wifi/mạng/phòng/cửa, pass đồ/lại hàng). "tài khoản và mật khẩu"
# có "mật khẩu" nên đúng là thông tin đăng nhập; "thông tin tài khoản" chỉ khi có ngữ cảnh nhập/điền vào link (xem `cred_info_login`).
# STK để NHẬN tiền, `pass` trần (pass wifi) không phải bí mật đăng nhập: không đưa vào _SECRET
_LOGIN = r"(?:dang nhap|login|log in)"
_NUM_AMT = rf"(?:{_MONEY_AMT}|\d{{1,3}}(?:[.,]\d{{3}})+\b)"
_VERB = r"(?:nhap|dien|go|cung cap|dua|gui|doc|cho|chia se|khai bao|bam|bao)"
_PAST = r"(?:da|vua|lo|trot|moi|lam xong|nhap xong)"
_NEG = re.compile(r"\b(?:chua|khong|chang|(?<!tu )dung|ko|(?<!tu )dung co|chua bao gio|dam)\b")
_NEG_TAIL = re.compile(r"^[\s,]*(?:chua|hay chua|chua a|chua ha)\b")

# Mỗi mục: (mã luật, regex trên `fold`). Tất cả cùng cờ `credentials_entered`.
_CRED: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("cred_enter", re.compile(rf"\b{_PAST}\b{_gap(0, 5)}{_SEP}{_VERB}\b{_gap(0, 6)}{_SEP}{_SECRET}\b")),
    ("cred_enter_done", re.compile(rf"\b{_VERB}\b{_gap(0, 5)}{_SEP}{_SECRET}\b{_gap(0, 7)}{_SEP}(?:roi|xong|luon|r|mat roi)\b")),
    ("cred_info_login", re.compile(rf"\b{_PAST}\b{_gap(0, 5)}{_SEP}{_VERB}\b{_gap(0, 3)}{_SEP}thong tin (?:tai khoan|dang nhap)"
                                   rf"{_gap(0, 3)}{_SEP}(?:link|duong dan|trang|web|app)\b")),
    ("cred_enter_luon", re.compile(rf"\b(?:nhap|dien|go|gui|doc|cho|dua){_SEP}luon\b{_gap(0, 3)}{_SEP}{_SECRET}\b")),
    ("cred_enter_done2", re.compile(rf"\b{_VERB}{_SEP}xong{_SEP}(?:\w+{_SEP}){{0,3}}{_SECRET}\b")),
    ("cred_secret_sent", re.compile(rf"\b{_SECRET}\b{_gap(0, 4)}{_SEP}(?:da|vua|lo|trot){_SEP}{_VERB}\b")),
    ("cred_secret_gave", re.compile(rf"\b{_SECRET}\b{_gap(0, 4)}{_SEP}(?:em|toi|minh|anh|chi|con|chau|bac|co|chu){_SEP}"
                                     rf"(?:da |vua |lo |trot )?{_VERB}\b{_gap(0, 4)}{_SEP}(?:roi|xong|luon|r)\b")),
    ("login_link", re.compile(rf"\b(?:da|vua|lo|trot|moi)\b{_gap(0, 4)}{_SEP}{_LOGIN}\b{_gap(0, 6)}{_SEP}(?:link|duong dan)\b")),
    ("login_page", re.compile(rf"\b(?:da|vua|lo|trot|moi)\b{_gap(0, 4)}{_SEP}{_LOGIN}\b{_gap(0, 5)}{_SEP}"
                              rf"(?:trang|web|website|app|ung dung|giao dien){_gap(0, 4)}{_SEP}"
                              rf"(?:gia|la|do|nay|kia|ho gui|nguoi la|lua dao|phishing|nhan duoc)\b")),
    ("login_after_link", re.compile(rf"\b(?:link|duong dan|trang gia|web gia|trang la|web la)\b{_gap(0, 6)}{_SEP}{_LOGIN}\b"
                                    rf"{_gap(0, 4)}{_SEP}(?:roi|xong|luon|r)\b")),
    ("login_done_link", re.compile(rf"\b{_LOGIN}\b{_gap(0, 3)}{_SEP}(?:vao )?(?:link|duong dan|trang gia|web gia|trang la|web la)"
                                   rf"\b{_gap(0, 4)}{_SEP}(?:roi|xong|luon|r)\b")),
    ("charged_bi", re.compile(rf"\b(?:bi|da bi|vua bi|dang bi)\s+(?:tru|rut|lay)(?:\s+(?:het|sach|oan|mat))?\s+"
                              rf"(?:tien|{_NUM_AMT}|tai khoan|the\b|het\b|sach\b)")),
    ("charged_acc2", re.compile(rf"\b(?:tk|tai khoan)\b{_gap(0, 5)}{_SEP}(?:(?:vua|da|bi|tu dung|tu nhien|dang)\s+)*(?:mat|tru|rut)\s+{_NUM_AMT}")),
    ("charged_bank_report", re.compile(rf"\b(?:ngan hang|bank|app|momo)\s+(?:bao|thong bao)\s+(?:da\s+)?(?:tru|rut)\s+(?:tien\b|{_NUM_AMT})")),
    ("charged_bao_tru", re.compile(rf"\bbao tru\s+{_NUM_AMT}")),
    ("charged_acc", re.compile(rf"\btai khoan\b{_gap(0, 3)}{_SEP}(?:bi|vua bi|da bi)\s+(?:tru|rut|mat|lay)\b")),
    ("charged_sms", re.compile(rf"\b(?:tin nhan|thong bao|sms|bien dong so du)\b{_gap(0, 2)}{_SEP}(?:tru|rut)\s+(?:tien|\d)")),
    ("charged_lost", re.compile(rf"\b(?:bi lua|bi mat|bi lay)\b{_gap(0, 3)}{_SEP}(?:\d[\d.,]*\s*(?:tr|trieu|k|nghin|ngan|ty|d|dong|vnd)\b|"
                                rf"tien\b|het tien|sach tien)")),
    ("charged_vanish", re.compile(rf"\btien\b{_gap(0, 5)}{_SEP}(?:bay|bien mat|boc hoi|het sach|mat sach|mat het)\b")),
    ("charged_vanish2", re.compile(r"\btien\b(?:\s+\w+){0,3}\s+(?:bay mat|bay het|bay sach)\b")),
)

_TAKEOVER_ASSET = r"(?:tai khoan|zalo|facebook|fb|nick|messenger|telegram|whatsapp|momo|zalopay|email|gmail|sim|vi)"
_TAKEOVER: tuple[re.Pattern[str], ...] = (
    re.compile(rf"\b{_TAKEOVER_ASSET}\b{_gap(0, 4)}{_SEP}(?:bi|vua bi|da bi|dang bi)\s+(?:hack|hacker|chiem|danh cap|chiem doat|cuop|khoa)\b"),
    re.compile(rf"\b(?:bi|vua bi|da bi|dang bi)\s+(?:hack|hacker|chiem doat|chiem|danh cap)\s+{_TAKEOVER_ASSET}\b"),
    re.compile(rf"\bco nguoi\b{_gap(0, 2)}{_SEP}(?:dang nhap|vao|su dung|dung|chiem)\b{_gap(0, 2)}{_SEP}{_TAKEOVER_ASSET}\b"),
    re.compile(rf"\b(?:bi mat|mat)\s+{_TAKEOVER_ASSET}\b(?!\s+(?:cua nguoi|tien))"),
    re.compile(rf"\b{_TAKEOVER_ASSET}\b{_gap(0, 3)}{_SEP}(?:dang bi|bi)\s+(?:nguoi la|ke gian|ke xau|hacker)\b"),
)

# Cháy/chạy, gấp/gặp trùng nhau khi bỏ dấu ⇒ các cụm có dấu chạy trên `lc`; cụm không dấu chỉ nhận khi đủ rõ.
_EMERG_LC: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("fire", re.compile(r"(?<![a-zà-ỹ])(?:đang |bị |có |vừa |xảy ra |vụ |đám )?cháy(?![a-zà-ỹ])(?!\s+(?:hàng|túi|máy|ví|slot|vé|sale|kho hàng|ngân sách|pin|điểm|giá|đơn|cầu chì|bóng|nồi|cơm|chảo|bánh|đèn))")),
    ("fire2", re.compile(r"(?<![a-zà-ỹ])(?:hỏa hoạn|hoả hoạn|chập điện|cháy nổ|báo cháy|phòng cháy)(?![a-zà-ỹ])")),
    # gõ không dấu: chỉ nhận khi kèm danh từ rõ ("chay nha"); "chay xe" là CHẠY xe, không phải cháy xe
    ("fire_ascii", re.compile(r"(?<![a-zà-ỹ])(?:chay (?:nha|xuong|kho|tang|bep|rung)|dam chay|vu chay)(?![a-zà-ỹ])")),
)
_EMERG_FOLD: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("fire_hoa_hoan", re.compile(r"\b(?:hoa hoan|chap dien|bao chay)\b")),
    ("cap_cuu", re.compile(r"\b(?:cap cuu|xe cap cuu|(?:goi|bao|alo|call|quay|bam|tong dai)\s+(?:so\s+|ngay\s+)?115|115\s+(?:di|gap|nhanh|giup|den|toi|cap cuu))\b")),
    ("accident", re.compile(r"\b(?:tai nan|nhap vien|dot quy|ngung tho|ngung tim|ngo doc|dien giat|duoi nuoc|bat tinh|nguy kich|hon me|"
                            r"bi thuong nang|chay mau nhieu|dong dat|sap nha|ngat xiu|co giat|tu vong|qua doi|phau thuat gap)\b")),
)
_HARD_EMERG = frozenset({"fire", "fire2", "fire_ascii", "fire_hoa_hoan", "cap_cuu"})

_MONEY_VERB = (r"(?:chuyen khoan|chuyen tien|chuyen gap|chuyen ngay|chuyen giup|chuyen ho|ck|gui tien|nap tien|dong tien|"
               r"ung tien|tra tien|thanh toan|nop tien|nho chuyen|chuyen)")
_MONEY: tuple[re.Pattern[str], ...] = (
    re.compile(rf"\b(?:chuyen khoan|chuyen tien|chuyen gap|chuyen ngay|chuyen giup|chuyen ho|nho chuyen|gui tien|nap tien|dong tien|ung tien|ck)\b"
               rf"{_gap(0, 6)}{_SEP}(?:cho|vao|qua|toi|sang|giup|ho|stk|so tai khoan|tk|tai khoan|ngan hang|vcb|mb|acb|tcb|bidv|agribank|momo|zalopay|\d)"),
    re.compile(rf"\b(?:muon|vay|xin|ung|can|vay gap|xin gap|cho muon)\s+(?:\w+\s+){{0,2}}?(?:tien\b|{_MONEY_AMT})"),
    re.compile(rf"\b{_MONEY_VERB}\b{_gap(0, 4)}{_SEP}{_MONEY_AMT}"),
    re.compile(r"\b(?:stk|so tai khoan|so tk|tk)\b[\s:.-]*\d[\d\s.-]{7,18}\d"),
)

_LEAVE: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b(?:doa|de doa)\s+(?:bo|huy|ngung|dung)\b"),
    re.compile(r"\b(?:se|se phai|se cho|sap|chuan bi)\s+(?:bo|huy|ngung|doi|chuyen sang|khong (?:mua|hop tac|dung|ung ho|lay))\b"),
    re.compile(r"\b(?:khong (?:mua|hop tac|dung|ung ho|lay)\s+(?:nua|cua shop|cua cong ty)|bo luon|huy hop dong|huy don hang|"
               r"ngung hop tac|doi nha cung cap|doi ben khac|chuyen sang (?:ben|nha|cong ty) khac|chia tay (?:shop|cong ty)|thoi khong mua|"
               r"khong bao gio mua|khong hop tac nua)\b"),
)
_PUBLIC: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b(?:dang|post|viet|dua|lan|cho len|len)\s+(?:len\s+)?(?:fb|facebook|group|nhom|mang xa hoi|tiktok|bao|dai|bao chi|youtube|review|google map|hoi nhom)\b"),
    re.compile(r"\b(?:boc phot|to cao|to (?:shop|cong ty)|danh gia (?:1|mot) sao|review (?:xau|te|1 sao)|phot|lan truyen|cong khai)\b"),
)

_URGENT_SYSTEM = re.compile(
    r"\b(?:he thong (?:sap|loi|die|treo|down|cham)|(?:sap|loi) he thong|web (?:sap|die|loi)|app (?:sap|loi)|server (?:sap|die|down)|"
    r"may pos (?:hong|loi)|pos (?:hong|loi)|mat ket noi|khong (?:vao|truy cap|dang nhap|in|ban)\s+duoc|treo may|bi down|down roi|"
    r"sap het he thong|sap mang)\b")
_URGENT_WORDS = re.compile(r"\b(?:khan cap|khan|deadline|han chot|tre han|qua han|nop bai|sap het han|can gap|rat gap|gap lam|gap qua)\b")
_URGENT_LC = re.compile(r"(?<![a-zà-ỹ])(?:gấp|khẩn)(?![a-zà-ỹ])")
_TODAY = re.compile(r"\b(?:hom nay|trong hom nay|chieu nay|toi nay|sang nay|ngay bay gio|bay gio|truoc (?:trua|chieu|toi|het ngay)|"
                    r"het ngay hom nay|cuoi ngay|truoc \d{1,2}\s*(?:h|g|gio|:\d\d|h\d\d))\b")
_PROMISE = re.compile(r"\b(?:hua|cam ket|se giao|giao hang|giao cho|bao gia|gui hang|gui ngay|co ngay|giao truoc|chot don)\b")
_DEAL = re.compile(r"\b(?:chot|dat coc|dat hang|can mua|can nhap|lay luon|ky hop dong|mua luon|dat mua|can gap|nhap ve)\b")
_SCAM_WORDS = re.compile(r"\b(?:trung thuong|nhan qua mien phi|xac minh tai khoan|tai khoan (?:cua ban )?bi khoa|cong an goi|toa an goi|"
                         r"co quan dieu tra|viec nhe luong cao|tuyen ctv|nhiem vu online|dau tu lai suat cao|vay khong the chap|nang han muc|"
                         r"cap nhat thong tin ngan hang|click vao link|bam vao link)\b")
_LINK = re.compile(r"(?:https?://\S+|www\.\S+|\b[a-z0-9-]+\.(?:com|vn|net|org|xyz|top|click|info|me|io|cc|link)(?:/\S*)?\b)")
_PHONE = re.compile(r"(?<!\d)(?:\+?84|0)[\s.-]?(?:3|5|7|8|9)(?:[\s.-]?\d){8}(?!\d)")
_ACCOUNT = re.compile(r"(?:\b(?:stk|so tai khoan|so tk|tai khoan|tk)\b[\s:.-]*|(?<=\s)|^)(\d(?:[\s.-]?\d){7,15})(?!\d)")
_AMOUNT = re.compile(r"(?<![\d.,])(\d{1,3}(?:[.,]\d{3})+|\d+(?:[.,]\d+)?)\s*(ty|ti|tr|trieu|nghin|ngan|k|d|dong|vnd)\b")
# Ngôi thứ ba CHỈ khi từ chỉ người đứng làm CHỦ NGỮ ngay trước động từ ("mẹ em đã nhập OTP", "bạn tôi bị trừ tiền"). KHÔNG tính: cách
# gọi ("mẹ ơi", "khách ơi", "bạn ơi") và tân ngữ/định ngữ sau cho|vào|của ("link bạn gửi", "nhà mình hay dùng").
_THIRD = re.compile(r"\b(?:me|ba|bo|cha|ong|vo|chong|con trai|con gai|anh trai|chi gai|em trai|em gai|nguoi than|ban|khach|"
                    r"nhan vien|hang xom|co ay|chu ay|anh ay|chi ay|ho)\s+(?:cua\s+)?(?:em|toi|minh|anh|chi|nha|tao|tui)?\s*"
                    r"(?:da|vua|lo|trot|bi|moi|dang|nhap|dien|doc|dua|cung cap|dang nhap|login)\b")
_ACCT_BEFORE = re.compile(r"(?:\bvao|\bqua|\bsang|\bchuyen(?: khoan| tien)?|\bck|\bstk|\btk)\s*(?:so\s+|tai khoan\s+|cho\s+)?[:.-]?\s*$")
_ORDER_BEFORE = re.compile(r"(?:ma don|don hang|so don|ma van don|van don|mvd|ngay|don|ma)\s*(?:hang|so)?\s*[:#.-]?\s*$")
_CLAUSE_SPLIT = re.compile(r"[.!?\n;]")


_VND_MAX = 10**15   # trần hợp lý; chuỗi số cực dài (tin rác/tấn công) ⇒ None thay vì inf → OverflowError làm hỏng cả lượt


def _vnd(num: str, unit: str) -> int | None:
    n = num.replace(" ", "")
    if sum(c.isdigit() for c in n) > 18:
        return None
    try:
        if unit in ("ty", "ti"):
            v = int(float(n.replace(",", ".")) * 1_000_000_000)
        elif unit in ("tr", "trieu"):
            v = int(float(n.replace(",", ".")) * 1_000_000)
        else:
            digits = re.sub(r"[.,]", "", n) if re.fullmatch(r"\d{1,3}(?:[.,]\d{3})+", n) else n.replace(",", ".")
            mult = {"nghin": 1_000, "ngan": 1_000, "k": 1_000}.get(unit, 1)
            v = int(float(digits) * mult)
    except (ValueError, OverflowError):
        return None
    return v if 0 <= v <= _VND_MAX else None


def parse_amounts(text: str) -> list[int]:
    """Các số tiền (VND) có đơn vị trong chữ. Chỉ để đặt cờ ứng viên; số tiền chính thức do `verify.py` parse."""
    fold, _idx, _lc = _views(text[:MAX_SCAN_CHARS])
    out: list[int] = []
    for m in _AMOUNT.finditer(fold):
        v = _vnd(m.group(1), m.group(2))
        if v:
            out.append(v)
    return out


def _orig(idx: list[int], n: int, a: int, b: int) -> tuple[int, int]:
    return idx[a], idx[b - 1] + 1 if b > a and b - 1 < len(idx) else n


def _first_person(fold: str, a: int, b: int) -> bool:
    """Ngôi thứ nhất: mệnh đề chứa span KHÔNG có từ chỉ người khác (mẹ/bà nhà/bạn/khách…). Câu lược chủ ngữ ("lỡ nhập OTP
    rồi") tính là ngôi thứ nhất — người gửi tự kể."""
    lo = max((m.end() for m in _CLAUSE_SPLIT.finditer(fold, 0, a)), default=0)
    hi = next((m.start() for m in _CLAUSE_SPLIT.finditer(fold, b)), len(fold))
    clause = fold[lo:hi]
    return _THIRD.search(clause) is None


def _cred_hits(fold: str) -> list[tuple[str, int, int]]:
    out: list[tuple[str, int, int]] = []
    for code, rx in _CRED:
        for m in rx.finditer(fold):
            a, b = m.start(), m.end()
            if _NEG.search(fold[a:b]) or _NEG_TAIL.match(fold[b:b + 14]):
                continue
            out.append((code, a, b))
    return out


def _first(rx: re.Pattern[str], s: str) -> re.Match[str] | None:
    return rx.search(s)


def scan_text(text: str, source_field: str = "body") -> list[Candidate]:
    """Luật (a) trên MỘT đoạn chữ (tin, chữ OCR, transcript). Trả ứng viên theo thứ tự ưu tiên (an toàn/lừa đảo trước).
    Mỗi cờ tối đa một ứng viên (span đầu tiên). Rỗng ⇒ [] (đa số tin)."""
    if source_field not in SOURCE_FIELDS:
        raise ValueError(f"source_field không hợp lệ: {source_field!r}")
    if not text or not text.strip():
        return []
    text = text[:MAX_SCAN_CHARS]
    fold, idx, lc = _views(text)
    n = len(text)
    out: list[Candidate] = []

    def add(flag: str, a: int, b: int, *, hard: str | None = None, first_person: bool = False, gate: str | None = None,
            on_lc: bool = False, **details: Any) -> None:
        if any(c.flag == flag for c in out):
            return
        sa, sb = (a, b) if on_lc else _orig(idx, n, a, b)
        out.append(Candidate(flag, source_field, (sa, max(sb, sa)), KIND_BY_FLAG.get(flag),
                             gate if gate is not None else GATE_BY_FLAG.get(flag), hard, first_person, details=details))

    # 1. thông tin đăng nhập đã lộ / bị trừ tiền (luật cứng)
    hits = _cred_hits(fold)
    if hits:
        code, a, b = min(hits, key=lambda h: h[1])
        add("credentials_entered", a, b, hard="credentials_entered", first_person=_first_person(fold, a, b), rule=code)
    # 2. tài khoản bị chiếm
    for rx in _TAKEOVER:
        m = rx.search(fold)
        if m:
            add("account_takeover", m.start(), m.end(), first_person=_first_person(fold, m.start(), m.end()))
            break
    # 3. cấp cứu / cháy / tai nạn
    em: tuple[str, int, int, bool] | None = None
    for code, rx in _EMERG_LC:
        m = rx.search(lc)
        if m:
            em = (code, m.start(), m.end(), True)
            break
    if em is None:
        for code, rx in _EMERG_FOLD:
            m = rx.search(fold)
            if m:
                em = (code, m.start(), m.end(), False)
                break
    if em is not None:
        code, a, b, on_lc = em
        add("emergency", a, b, hard="emergency" if code in _HARD_EMERG else None, on_lc=on_lc, rule=code)
    # 4. yêu cầu chuyển tiền (một mình: không gán loại)
    for rx in _MONEY:
        m = rx.search(fold)
        if m:
            add("money_request", m.start(), m.end())
            break
    # 5. doạ bỏ / chê công khai
    for rx in _LEAVE:
        m = rx.search(fold)
        if m:
            add("said_will_leave", m.start(), m.end())
            break
    for rx in _PUBLIC:
        m = rx.search(fold)
        if m:
            add("public", m.start(), m.end())
            break
    # 6. gấp / sập / hạn chót / hôm nay
    today = _TODAY.search(fold)
    sysm = _URGENT_SYSTEM.search(fold)
    urg = _URGENT_WORDS.search(fold)
    urg_lc = _URGENT_LC.search(lc)
    if today is not None:
        add("deadline_today", today.start(), today.end())
    if sysm is not None:
        add("urgent", sysm.start(), sysm.end(), gate="su_co_cong_viec_gap", system=True)
    elif (urg is not None or urg_lc is not None) and today is not None:
        a, b, on_lc = (urg.start(), urg.end(), False) if urg is not None else (urg_lc.start(), urg_lc.end(), True)  # type: ignore[union-attr]
        add("urgent", a, b, gate="su_co_cong_viec_gap", on_lc=on_lc)
    elif urg is not None or urg_lc is not None:
        a, b, on_lc = (urg.start(), urg.end(), False) if urg is not None else (urg_lc.start(), urg_lc.end(), True)  # type: ignore[union-attr]
        add("urgent", a, b, on_lc=on_lc)
    # 7. lời hứa tới hạn hôm nay; cơ hội lớn
    pm = _PROMISE.search(fold)
    if pm is not None and today is not None:
        add("promise_due", pm.start(), pm.end(), gate="loi_hua_khach_sap_vo")
    amounts = [(m, _vnd(m.group(1), m.group(2))) for m in _AMOUNT.finditer(fold)]
    big = [(m, v) for m, v in amounts if v and v >= BIG_DEAL_VND]
    if big:
        m, v = big[0]
        dm = _DEAL.search(fold)
        add("amount", m.start(), m.end(), vnd=v)
        if dm is not None and any(c.flag == "urgent" for c in out) or (dm is not None and today is not None):
            add("big_deal", dm.start(), dm.end(), gate="co_hoi_lon_sap_troi", vnd=v)
    elif amounts:
        m0, v0 = amounts[0]
        add("amount", m0.start(), m0.end(), vnd=v0)
    # 8. dấu vết rẻ: từ khoá lừa đảo, link, SĐT, STK
    sm = _SCAM_WORDS.search(fold)
    if sm is not None:
        add("scam_keyword", sm.start(), sm.end())
    lm = _LINK.search(fold)
    if lm is not None:
        add("link", lm.start(), lm.end())
    pmatch = _PHONE.search(fold)
    if pmatch is not None:
        add("phone", pmatch.start(), pmatch.end())
    phone_spans = [m.span() for m in _PHONE.finditer(fold)]
    accts: list[str] = []
    first_acct: re.Match[str] | None = None
    for m in _ACCOUNT.finditer(fold):
        digits = re.sub(r"\D", "", m.group(1))
        has_label = bool(re.search(r"(?:stk|so tai khoan|so tk|tai khoan|tk)\b", fold[max(0, m.start() - 2):m.start(1)]))
        if not has_label:
            if any(s <= m.start(1) and m.end(1) <= e for s, e in phone_spans):
                continue                    # số trông như SĐT mà không có nhãn STK ⇒ SĐT (T7: SĐT dùng làm STK hiện bị bỏ — chỉ nhận khi có nhãn)
            before = fold[max(0, m.start(1) - 24):m.start(1)]
            if _ORDER_BEFORE.search(before) or not _ACCT_BEFORE.search(before):
                continue                    # STK không nhãn chỉ nhận khi sát vao/qua/chuyen/ck; loại mã đơn/ngày/mã vận đơn
        if len(digits) >= 8 and (has_label or any(c.flag == "money_request" for c in out)):
            accts.append(digits)
            first_acct = first_acct or m
    if first_acct is not None:
        add("account_number", first_acct.start(1), first_acct.end(1), account_numbers=tuple(dict.fromkeys(accts)))
    order = {"credentials_entered": 0, "account_takeover": 1, "emergency": 2, "money_request": 3}
    out.sort(key=lambda c: (order.get(c.flag, 9), c.span[0]))
    return out


def _impersonated(ctx: SenderCtx) -> tuple[str, ...]:
    """ID khác, tên trùng ⇒ `impersonated = [người bị trùng tên]` CHỈ khi ID gửi MỚI (< 7 ngày), vừa đổi tên hiển thị, hoặc chưa
    là thực thể đã biết (`joined_days` None = chưa biết gì về ID này). Thành viên lâu năm trùng tên không phải mạo danh (CNS-149)."""
    if ctx.name_collision_with is None:
        return ()
    fresh = ((ctx.joined_days is not None and ctx.joined_days < STRANGER_DAYS) or ctx.display_name_changed_recent
             or not ctx.known_entity)
    return (ctx.name_collision_with,) if fresh else ()


def enrich(cands: Sequence[Candidate], ctx: SenderCtx) -> list[Candidate]:
    """Gắn phần NGƯỜI và nâng `money_request` khi đi cùng dấu hiệu lừa đảo (§4D.2):
    - luật cứng ngôi thứ nhất (`credentials_entered`, tài khoản mình bị chiếm) ⇒ `victims = [người gửi]`;
    - `money_request` + (STK khác lịch sử | mạo danh | người lạ vào nhóm < 7 ngày) ⇒ `kind = rui_ro_lua_dao`, cổng
      `lua_dao_dang_dien_ra`; STK khác lịch sử thêm `hard_rule_hit = stk_changed_money_request`;
    - `money_request` một mình ⇒ KHÔNG gán loại (đơn hàng nào cũng có yêu cầu chuyển khoản)."""
    impers = _impersonated(ctx)
    stranger = ctx.joined_days is not None and ctx.joined_days < STRANGER_DAYS
    acct = next((c for c in cands if c.flag == "account_number"), None)
    digests: tuple[str, ...] = ()
    if acct is not None:
        digests = tuple(d for d in (_digest(n) for n in acct.details.get("account_numbers", ())) if d)
    changed = bool(ctx.known_stk_digests) and any(d not in ctx.known_stk_digests for d in digests)
    out: list[Candidate] = []
    for c in cands:
        det = {k: v for k, v in c.details.items() if k != "account_numbers"}
        if c.flag == "account_number":
            det["stk_digests"] = list(digests)
            out.append(replace(c, details=det))
            continue
        victims = (ctx.sender_ref,) if (c.first_person and ctx.sender_ref and c.flag in (
            "credentials_entered", "account_takeover")) else ()
        if c.flag == "money_request":
            if changed or impers or stranger:
                det["promoted_by"] = [x for x, on in (("stk_changed", changed), ("impersonation", bool(impers)),
                                                       ("stranger", stranger)) if on]
                out.append(replace(c, kind="rui_ro_lua_dao", gate_candidate="lua_dao_dang_dien_ra",
                                   hard_rule_hit="stk_changed_money_request" if changed else c.hard_rule_hit,
                                   impersonated=impers, details=det))
            else:
                out.append(replace(c, details=det))
            continue
        out.append(replace(c, victims=victims, impersonated=impers if c.kind == "rui_ro_lua_dao" else (),
                           details=det))
    return out


def _digest(number: str) -> str | None:
    """Băm có khoá của STK (cùng hàm với `mem.facts`); không có khoá master / số lạ ⇒ None."""
    try:
        from gh.biz.mem import facts

        return facts.payment_digest(number)
    except Exception:  # noqa: BLE001 — thiếu khoá master không được làm hỏng luật rẻ
        return None


def gate_class(cands: Sequence[Candidate]) -> str | None:
    """Lớp chạy ngay của một tin: 'safety' (lừa đảo/an toàn — trần riêng, không bị cắt), 'gate' (4 cổng còn lại) hoặc None."""
    if any(c.safety for c in cands):
        return "safety"
    if any(c.immediate for c in cands):
        return "gate"
    return None


# ─── tin KHÔNG cần model (§4D.1): sticker, reaction, "dạ/ok/cảm ơn" — vẫn đếm vào dòng độ phủ ─────────────────────────────
NO_MODEL_KINDS = ("sticker", "reaction", "system")
_ACK_WORDS = frozenset({"da", "dạ", "ok", "oke", "okie", "okay", "vang", "vâng", "uh", "uhm", "um", "u", "ừ", "ừm", "cam", "on",
                        "cảm", "ơn", "thanks", "thank", "you", "tks", "thx", "a", "ạ", "nhe", "nhé", "nha", "nhá", "roi", "rồi",
                        "duoc", "được", "da.", "hi", "hello", "chao", "chào", "oki", "okla", "ok.", "vâng.", "ừ.", "👍", "🙏",
                        "ạ.", "e", "em", "anh", "chi", "chị", "nhiều", "nhieu", "xin", "cám", "ghi", "nhận", "nhan", "biết",
                        "biet", "hiểu", "hieu", "rõ", "ro"})


def is_ack(text: str | None) -> bool:
    """Tin chỉ có lời xác nhận/cảm ơn ngắn ("dạ", "ok ạ", "cảm ơn anh"). Có số, link hay chữ lạ ⇒ không phải."""
    t = (text or "").strip().lower()
    if not t or len(t) > 24:
        return False
    words = [w for w in re.split(r"[\s,!.?~]+", t) if w]
    return bool(words) and all(w in _ACK_WORDS for w in words)


# Dấu hiệu tin rao/thông báo hàng loạt (trên chữ đã bỏ dấu, chữ thường): chỉ những tin này mới bị coi là "mẫu lặp".
# Chỉ cụm đặc trưng (không từ đơn dễ khớp nhầm: "tuyen" ⊂ "tuyến"/"trực tuyến", "co ai", "cac ban"); `\b` hai đầu ⇒ "sale" ≠ "Salem".
_BROADCAST = re.compile(r"\b(?:ib|inbox|ibx|lien he|tuyen dung|tuyen nhan vien|tuyen gap|can tuyen|thanh ly|quan nao can|ai can|"
                        r"khuyen mai|giam gia|sale|uu dai|quy khach|bang gia|thong bao|moi nguoi oi|ca nha oi|cac quan|lich rang|"
                        r"follow|dang ky|mien phi|qua tang|voucher)\b")


def is_broadcast(body: str | None) -> bool:
    """Tin có dáng rao vặt/thông báo hàng loạt (quảng cáo, tuyển dụng, thanh lý, bảng giá, lịch chung) hoặc có link/SĐT."""
    fold = strip_accents((body or "").lower())
    return bool(_BROADCAST.search(fold) or _LINK.search(fold) or _PHONE.search(fold))


def no_model_reason(kind: str | None, body: str | None, *, template_n: int = 0) -> str | None:
    """Vì sao tin KHÔNG vào phần "tin mới" của lượt model (None = vào). `template_n` = số lần cùng nội dung CÙNG người CÙNG luồng
    trong 7 ngày theo giờ gửi (≥ 3 ⇒ tin mẫu lặp, `is_template` — sửa điểm yếu 5 của v2). CHỈ áp cho tin rao/thông báo hàng loạt
    (`is_broadcast`): tin nghiệp vụ lặp của một người (đơn định kỳ "tuần này a lấy 25kg", giữ phòng họp thứ 3) KHÔNG phải mẫu —
    chính nhịp lặp là tín hiệu (lỗi kb1 #4)."""
    if kind in NO_MODEL_KINDS:
        return str(kind)
    if is_ack(body):
        return "ack"
    if template_n >= TEMPLATE_MIN_N and len(triage.normalize(body or "")) >= triage.BROADCAST_MIN_LEN and is_broadcast(body):
        return "template"
    return None
