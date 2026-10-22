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
"""

import uuid
from collections.abc import Collection, Hashable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, TypeVar

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gh.refinery import triage

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
