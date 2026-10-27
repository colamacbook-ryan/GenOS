# ruff: noqa: E501
"""Một bộ dựng bản tin `digest.build(org, now, audience)` (r4 §4.6, r6 §4.2.1 (e)) — CHỈ THỰC THI `Level.delivery` đã lưu, không tính lại mức
(mức đã đóng băng lúc `relane`). Mỗi kênh (Zalo T3, Console/Telegram `briefing.py`) chỉ là một bộ render của `Digest`.

Thứ tự (r7.1, §4.2.1 (e), CNS-176 — nhóm định nghĩa bằng TRƯỜNG đã lưu, không tính lại mức):
  1. lừa đảo/an toàn (`kind` ∈ {rui_ro_lua_dao, suc_khoe_an_toan} hoặc gate/candidate ∈ {G1, G2}) — KHÔNG BAO GIỜ chịu trần dòng;
  2. 🔴 bóng / bị trần / hạ precision (`would_push` · `capped` · `demoted`) kèm "(đáng lẽ báo ngay …)";
  3. P2 (`reasons ∋ prio_p2`): `window_expired` (Q15) rồi `milestone_due_soon` (Q-A) đứng ĐẦU và không tính vào trần; các dòng P2 còn lại xếp
     impact → urgency → độ chắc, hiện tối đa `P2_MAX` (5) dòng; phần dư xuống nhóm 🟠 thường, dòng đầu của phần dư kèm "(+k mục cần xem)";
  4. mục `digest_top`/`digest_morning` còn lại, rồi 🟠 `digest`; rồi ⚪ dòng `staff_owned` ("NV X đang lo: …"), rồi ⚪ `digest_low` gộp DÒNG CỤM
     theo loại (giữ từng `ref`). `push_now`, `merge`, `none`, `off` không lên bản tin. Hoà ⇒ mục chạm mục tiêu Sếp (`owner_fact_ids`) trước.
Mỗi dòng ≥ 🟠 (và dòng `staff_owned`) có "Việc Sếp cần làm" (Q18): `suggested_action` đã qua kiểm (không chào bán/chốt đơn, "báo giá" chỉ khi
trích dẫn có hỏi giá) hoặc 1 trong 10 mẫu theo `kind` + `reasons`.
Kênh chat (`audience='chat'`) bỏ `privacy='console_pin'`. Trích dẫn chỉ lấy `quote_display` (đã che) của bằng chứng `verified`.
Trần 1 câu hỏi hệ thống/bản tin cho ask `profile`/`identity`/`question`.
"""

from __future__ import annotations

import re
import unicodedata
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gh.biz.intel import levels
from gh.textnorm import strip_accents

TOP = ("digest_top", "digest_morning")
SHOWN = TOP + ("digest", "digest_low")
REASON_VI = {"shadow": "(đáng lẽ báo ngay lúc {wp})", "capped": "(đáng lẽ báo ngay)", "precision": "(đáng lẽ báo ngay)",
             "window_expired": "đã quá thời điểm báo ngay", "txn_pending": "giao dịch chưa thấy xảy ra — vẫn kịp chặn",
             "big_opportunity_no_catalog": "cơ hội lớn sắp hết hạn, chưa khớp hàng/dịch vụ của Sếp", "low_confidence": "chưa đủ để báo ngay: chưa chắc",
             "no_harm": "chưa đủ để báo ngay: chưa rõ thiệt hại", "remediated": "đã có người phía Sếp khắc phục",
             "closed_by_staff": "nhân viên báo đã xử lý", "safety_wide_circle": "trong nhóm có người gặp nạn",
             "fraud_wide_circle": "trong nhóm có người bị nhắm lừa", "agent_probe": "có người thử lừa tài khoản trợ lý",
             "impersonation_big_group": "có người xưng là anh trong nhóm", "impersonation_dm_agent": "có người xưng là anh nhắn trợ lý",
             "milestone_due_soon": "lời hứa với khách sắp tới hạn", "held_for_digest": "giữ đầu bản tin tới bản tin kế"}
P2_MAX = 5                                                  # trần dòng P2 (không tính window_expired, milestone_due_soon) — [Gen chọn] chống phình đầu bản tin
FORBIDDEN_ACTION = ("chào hàng", "mời mua", "mời khách mua", "upsell", "bán thêm", "gợi ý mua", "khuyến mãi để chốt", "chốt đơn", "chốt sale")
MAX_ACTION_CHARS = 140
COPY_RUN_WORDS = 6                                         # ≥ 6 từ liền trùng trích dẫn/bằng chứng ⇒ coi là chép lời khách ⇒ bỏ
_WS = re.compile(r"\s+")
_PRICE_ASK = re.compile(r"gia bao nhieu|bao gia|bao nhieu tien|gia sao|hoi gia")


def _fold(t: str) -> str:
    """Chữ thường, bỏ dấu, gộp khoảng trắng — để so từ cấm / trùng lời không bị lách bằng dấu hoặc hoa-thường."""
    return _WS.sub(" ", strip_accents(unicodedata.normalize("NFC", t))).casefold().strip()


_FORBIDDEN_FOLDED = tuple(_fold(w) for w in FORBIDDEN_ACTION) + ("moi khach mua", "chao ban", "chot don", "chot sale", "ban them", "goi y mua")


def _copies_quote(action_folded: str, quotes: list[str]) -> bool:
    words = action_folded.split()
    if len(words) < COPY_RUN_WORDS:
        return False
    for q in quotes:
        qf = " " + _fold(q) + " "
        for i in range(len(words) - COPY_RUN_WORDS + 1):
            if " " + " ".join(words[i:i + COPY_RUN_WORDS]) + " " in qf:
                return True
    return False


def checked_action(action: str | None, quote: str | list[str] | None) -> str | None:
    """"Việc Sếp cần làm" do model đề xuất (`suggested_action`, Q18/CNS-185) chỉ được dùng khi qua kiểm: không rỗng, ≤ `MAX_ACTION_CHARS`, KHÔNG có
    đoạn ≥ 6 từ trùng trích dẫn/bằng chứng (không chép lời khách), không chứa từ chào bán/chốt đơn (so cả trên bản bỏ dấu), và "báo giá" chỉ khi
    trích dẫn có mẫu hỏi giá (giá bao nhiêu · báo giá · bao nhiêu tiền · giá sao · hỏi giá). Không đạt ⇒ None (bên gọi dùng mẫu)."""
    a = _WS.sub(" ", action or "").strip()
    if not a or len(a) > MAX_ACTION_CHARS:
        return None
    quotes = [quote] if isinstance(quote, str) else [q for q in (quote or []) if q]
    f = _fold(a)
    if any(w in f for w in _FORBIDDEN_FOLDED) or _copies_quote(f, quotes):
        return None
    if "bao gia" in f and not any(_PRICE_ASK.search(_fold(q)) for q in quotes):
        return None
    return a


def template_action(kind: str, reasons: list[str], gate: str | None, stage: str | None, staff: str | None) -> str:
    """10 mẫu dự phòng theo `kind` + `reasons` (§4.2.1 "Việc Sếp cần làm"); không có mẫu nào chào bán."""
    if kind == levels.FR:
        return "Gọi xác minh trực tiếp, chưa chuyển tiền/giao hàng"
    if kind == levels.SK:
        return "Gọi hỏi tình hình, cần thì gọi 115"
    if "staff_owned" in reasons:
        return f"Không cần làm gì, {staff or 'NV'} đang lo"
    if "window_expired" in reasons:
        return "Hỏi NV vụ này đã xử lý chưa"
    if gate == levels.G3:
        return "Gọi khách trước khi khách huỷ hẳn" if stage == "exit_pending" else "Đọc lời khách, quyết cách xử lý"
    if gate == levels.G4:
        return "Xác nhận giờ giao với khách hoặc giao NV báo lại"
    if gate == levels.G5 or kind == "co_hoi_ket_noi":
        return "Xem nhu cầu, trả lời khách nếu muốn nhận"
    if gate == levels.G6:
        return "Quyết: làm ngay, dời hạn hay giao người"
    return "Xem tin gốc khi rảnh"


@dataclass
class DigestItem:
    n: int
    signal_id: str
    code: str
    kind: str
    level: str
    delivery: str
    gate: str | None
    candidate: str | None
    title: str
    line_facts: list[str]
    quote: str | None
    ask_id: str | None = None
    ask_code: str | None = None
    cluster_ids: list[str] = field(default_factory=list)
    state_at_build: dict[str, tuple[str, str]] = field(default_factory=dict)   # id -> (level, delivery) lúc dựng; `mark_sent` đối chiếu
    action: str | None = None                           # "Việc Sếp cần làm" (đã kiểm hoặc mẫu); None cho dòng cụm ⚪
    reasons: list[str] = field(default_factory=list)    # mã lý do đã lưu (giải thích, INT-10); renderer có thể dùng


@dataclass
class Digest:
    org_id: str
    at: datetime
    items: list[DigestItem]
    counts: dict[str, int]
    system_question: dict[str, Any] | None = None
    p2_more: int = 0                                    # số dòng P2 vượt trần `P2_MAX` (đã xuống nhóm 🟠 thường, "(+k mục cần xem)")

    def refs(self) -> list[dict[str, Any]]:
        """Định dạng `ops.owner_outbox.refs` (hợp đồng T1 → T3): "1 ok / sai 2" trỏ đúng mục."""
        return [{"n": it.n, "signal_id": it.signal_id, "ask_id": it.ask_id, "ask_code": it.ask_code,
                 "cluster_ids": it.cluster_ids or None} for it in self.items]


def _quotes(evidence: Any) -> list[str]:
    return [str(e["quote_display"]) for e in evidence or [] if isinstance(e, dict) and e.get("quote_status") == "verified" and e.get("quote_display")]


def _quote(evidence: Any) -> str | None:
    for e in evidence or []:
        if isinstance(e, dict) and e.get("quote_status") == "verified" and e.get("quote_display"):
            return str(e["quote_display"])
    return None


def _facts_line(r: Any) -> list[str]:
    out: list[str] = []
    codes = list(r.reasons or [])
    if "txn_pending" in codes:
        codes = [c for c in codes if c != "window_expired"]   # D1: giao dịch chưa tới ⇒ không ghi "đã quá thời điểm báo ngay"
    for code in codes:
        t = REASON_VI.get(code)
        if t:
            out.append(t.format(wp=r.would_push_at.astimezone(levels.VN).strftime("%H:%M") if r.would_push_at else "--:--"))
    return out


async def _staff_name(db: AsyncSession, org_id: uuid.UUID, ref: str | None) -> str:
    """"NV X": `staff_ref` dạng `u_<person_id>` ⇒ tên hiển thị trong cùng tổ chức; không tra được ⇒ "NV"."""
    if ref and ref.startswith("u_"):
        try:
            pid = uuid.UUID(ref[2:])
        except ValueError:
            return "NV"
        name = (await db.execute(text("SELECT display_name FROM core.persons WHERE id = :p AND org_id = :o"), {"p": pid, "o": org_id})).scalar_one_or_none()
        if name:
            return "NV " + _WS.sub(" ", str(name)).strip()[:40]
    return "NV"


def group_of(r: Any) -> int:
    """Nhóm thứ tự bản tin: 0 lừa đảo/an toàn · 1 🔴 bóng/trần/precision · 2 P2 · 3 `digest_top` còn lại · 4 🟠 `digest` · 5 `staff_owned` · 6 cụm."""
    reasons = list(r.reasons or [])
    top = r.delivery in TOP
    defensive = (r.gate or r.candidate) in levels.BYPASS_GATES or r.kind in (levels.FR, levels.SK)
    if top and defensive:
        return 0
    if top and ((r.level == "red" and r.would_push_at is not None) or r.demoted is not None or "capped" in reasons or "shadow" in reasons):
        return 1
    if top and {"prio_p2", "window_expired", "milestone_due_soon"} & set(reasons):
        return 2
    if top:
        return 3
    if r.delivery == "digest":
        return 4
    return 5 if "staff_owned" in reasons else 6


async def build(db: AsyncSession, org_id: uuid.UUID, now: datetime, *, audience: Literal["owner", "chat"] = "chat",
                only_new: bool = True) -> Digest:
    rows = (await db.execute(text("""
        SELECT a.id, a.code, a.kind, a.level, a.delivery, a.gate, a.candidate, a.title, a.evidence, a.reasons, a.impact, a.urgency,
               a.confidence_exist, a.confidence_content, a.owner_fact_ids, a.would_push_at, a.privacy, a.demoted, a.suggested_action,
               a.level_input ->> 'staff_ref' AS staff_ref, a.level_input ->> 'stage_eff' AS stage_eff,
               q.id AS ask_id, q.code AS ask_code
        FROM biz.alerts a LEFT JOIN ops.asks q ON q.id = a.ask_id AND q.state = 'open'
        WHERE a.org_id = :o AND a.status IN ('open', 'acknowledged') AND a.delivery = ANY(:d)
          AND a.kind NOT IN ('system', 'legacy') AND (NOT :new OR a.digest_at IS NULL)
          AND (:aud = 'owner' OR a.privacy = 'chat_ok')"""),
        {"o": org_id, "d": list(SHOWN), "new": only_new, "aud": audience})).all()

    def rank(r: Any) -> tuple[Any, ...]:
        g = group_of(r)
        reasons = r.reasons or []
        # nhóm P2: Q15 rồi Q-A đứng đầu, rồi impact → urgency → độ chắc
        head = 0 if "window_expired" in reasons else 1 if "milestone_due_soon" in reasons else 2
        lvl = {"red": 0, "amber": 1, "grey": 2}.get(r.level, 3)
        conf = float(max(r.confidence_exist or 0, r.confidence_content or 0))
        return (g, head if g == 2 else 0, lvl, -(r.impact or 0), -(r.urgency or 0), -conf, 0 if r.owner_fact_ids else 1, str(r.id))

    ordered = sorted(rows, key=rank)
    # trần P2: window_expired/milestone_due_soon không tính; dòng P2 thường vượt `P2_MAX` ⇒ xuống nhóm 🟠 thường (ngay sau nhóm 3), kèm "(+k mục cần xem)"
    p2_plain = [r for r in ordered if group_of(r) == 2 and not {"window_expired", "milestone_due_soon"} & set(r.reasons or [])]
    overflow = {str(r.id) for r in p2_plain[P2_MAX:]}
    if overflow:
        top_part = [r for r in ordered if group_of(r) <= 2 and str(r.id) not in overflow]
        rest = [r for r in ordered if group_of(r) == 3] + [r for r in ordered if str(r.id) in overflow]
        rest += [r for r in ordered if group_of(r) >= 4]
        ordered = top_part + rest
    items: list[DigestItem] = []
    clusters: dict[str, list[Any]] = {}
    first_overflow = True
    for r in ordered:
        grp = group_of(r)
        if grp == 6:
            clusters.setdefault(r.kind, []).append(r)
            continue
        facts_ = _facts_line(r)
        if str(r.id) in overflow and first_overflow:
            facts_ = [*facts_, f"(+{len(overflow)} mục cần xem)"]
            first_overflow = False
        quote = _quote(r.evidence)
        title = r.title
        rs = list(r.reasons or [])
        staff = await _staff_name(db, org_id, r.staff_ref) if "staff_owned" in rs else None      # một lần/dòng; dòng staff_owned ở MỌI nhóm có tên NV
        if staff:
            title = f"{staff} đang lo: {r.title}"
        own = None if r.kind in (levels.FR, levels.SK) else checked_action(r.suggested_action, _quotes(r.evidence))      # lừa đảo/an toàn: luôn mẫu cố định
        action = own or template_action(r.kind, rs, r.gate or r.candidate, r.stage_eff, staff)
        items.append(DigestItem(len(items) + 1, str(r.id), r.code, r.kind, r.level, r.delivery, r.gate, r.candidate, title,
                                facts_, quote, str(r.ask_id) if r.ask_id else None, r.ask_code,
                                state_at_build={str(r.id): (r.level, r.delivery)}, action=action, reasons=list(r.reasons or [])))
    for kind, rs in sorted(clusters.items()):
        first = rs[0]
        items.append(DigestItem(len(items) + 1, str(first.id), first.code, kind, "grey", "digest_low", None, None,
                                f"{len(rs)} mục {kind.replace('_', ' ')}", [], None, cluster_ids=[str(x.id) for x in rs],
                                state_at_build={str(x.id): (x.level, x.delivery) for x in rs}))
    q = (await db.execute(text("""SELECT id, code, summary FROM ops.asks WHERE org_id = :o AND state = 'open'
                                  AND source IN ('profile', 'identity', 'question') AND (expires_at IS NULL OR expires_at > :now)
                                  ORDER BY created_at LIMIT 1"""), {"o": org_id, "now": now})).one_or_none()
    counts = {lv: sum(1 for r in rows if r.level == lv) for lv in ("red", "amber", "grey")}
    counts["top"] = sum(1 for r in rows if r.delivery in TOP)
    return Digest(str(org_id), now, items, counts,
                  {"ask_id": str(q.id), "code": q.code, "summary": q.summary} if q else None, p2_more=len(overflow))


async def mark_sent(db: AsyncSession, org_id: uuid.UUID, digest: Digest, now: datetime) -> int:
    """Sau khi kênh gửi xong: đánh dấu `digest_at` (mục không lặp lại ở bản tin sau). CHỈ đánh dấu mục còn đúng mức/phân phối như lúc dựng —
    mục đã đổi giữa lúc dựng và lúc gửi xong (vd leo lên nhóm đầu bản tin) chưa được Sếp thấy đúng dạng mới ⇒ để bản tin kế."""
    state = {i: st for it in digest.items for i, st in it.state_at_build.items()}
    if not state:
        return 0
    ids = list(state)
    r = await db.execute(text("""
        UPDATE biz.alerts a SET digest_at = :now
        FROM unnest(CAST(:ids AS uuid[]), CAST(:lv AS text[]), CAST(:dl AS text[])) AS v(id, level, delivery)
        WHERE a.org_id = :o AND a.id = v.id AND a.level = v.level AND a.delivery = v.delivery AND a.digest_at IS NULL"""),
        {"now": now, "o": org_id, "ids": [uuid.UUID(i) for i in ids], "lv": [state[i][0] for i in ids], "dl": [state[i][1] for i in ids]})
    return int(r.rowcount)  # type: ignore[attr-defined]
