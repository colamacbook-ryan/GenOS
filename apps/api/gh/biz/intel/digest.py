# ruff: noqa: E501
"""Một bộ dựng bản tin `digest.build(org, now, audience)` (r4 §4.6, r6 §4.2.1 (e)) — CHỈ THỰC THI `Level.delivery` đã lưu, không tính lại mức
(mức đã đóng băng lúc `relane`). Mỗi kênh (Zalo T3, Console/Telegram `briefing.py`) chỉ là một bộ render của `Digest`.

Thứ tự: mục `digest_top` / `digest_morning` trước (🔴 bóng, 🔴 bị trần/precision, sàn lừa đảo-an toàn, quá cửa sổ Q15) — trong nhóm
này lừa đảo/an toàn đứng đầu; rồi 🟠 `digest`; rồi ⚪ `digest_low` gộp DÒNG CỤM theo loại (giữ từng `ref`). `push_now`, `merge`, `none`,
`off` không lên bản tin. Xếp: impact → urgency → độ chắc; hoà ⇒ mục chạm mục tiêu Sếp (`owner_fact_ids`) trước.
Kênh chat (`audience='chat'`) bỏ `privacy='console_pin'`. Trích dẫn chỉ lấy `quote_display` (đã che) của bằng chứng `verified`.
Trần 1 câu hỏi hệ thống/bản tin cho ask `profile`/`identity`/`question`.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gh.biz.intel import levels

TOP = ("digest_top", "digest_morning")
SHOWN = TOP + ("digest", "digest_low")
REASON_VI = {"shadow": "(đáng lẽ báo ngay lúc {wp})", "capped": "(đáng lẽ báo ngay)", "precision": "(đáng lẽ báo ngay)",
             "window_expired": "đã quá thời điểm báo ngay", "txn_pending": "giao dịch chưa thấy xảy ra — vẫn kịp chặn",
             "big_opportunity_no_catalog": "cơ hội lớn sắp hết hạn, chưa khớp hàng/dịch vụ của Sếp", "low_confidence": "chưa đủ để báo ngay: chưa chắc",
             "no_harm": "chưa đủ để báo ngay: chưa rõ thiệt hại", "remediated": "đã có người phía Sếp khắc phục",
             "closed_by_staff": "nhân viên báo đã xử lý", "safety_wide_circle": "trong nhóm có người gặp nạn",
             "fraud_wide_circle": "trong nhóm có người bị nhắm lừa", "agent_probe": "có người thử lừa tài khoản trợ lý",
             "impersonation_big_group": "có người xưng là anh trong nhóm", "impersonation_dm_agent": "có người xưng là anh nhắn trợ lý"}


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


@dataclass
class Digest:
    org_id: str
    at: datetime
    items: list[DigestItem]
    counts: dict[str, int]
    system_question: dict[str, Any] | None = None

    def refs(self) -> list[dict[str, Any]]:
        """Định dạng `ops.owner_outbox.refs` (hợp đồng T1 → T3): "1 ok / sai 2" trỏ đúng mục."""
        return [{"n": it.n, "signal_id": it.signal_id, "ask_id": it.ask_id, "ask_code": it.ask_code,
                 "cluster_ids": it.cluster_ids or None} for it in self.items]


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


async def build(db: AsyncSession, org_id: uuid.UUID, now: datetime, *, audience: Literal["owner", "chat"] = "chat",
                only_new: bool = True) -> Digest:
    rows = (await db.execute(text("""
        SELECT a.id, a.code, a.kind, a.level, a.delivery, a.gate, a.candidate, a.title, a.evidence, a.reasons, a.impact, a.urgency,
               a.confidence_exist, a.confidence_content, a.owner_fact_ids, a.would_push_at, a.privacy, q.id AS ask_id, q.code AS ask_code
        FROM biz.alerts a LEFT JOIN ops.asks q ON q.id = a.ask_id AND q.state = 'open'
        WHERE a.org_id = :o AND a.status IN ('open', 'acknowledged') AND a.delivery = ANY(:d)
          AND a.kind NOT IN ('system', 'legacy') AND (NOT :new OR a.digest_at IS NULL)
          AND (:aud = 'owner' OR a.privacy = 'chat_ok')"""),
        {"o": org_id, "d": list(SHOWN), "new": only_new, "aud": audience})).all()

    def rank(r: Any) -> tuple[Any, ...]:
        top = r.delivery in TOP
        defensive = (r.gate or r.candidate) in levels.BYPASS_GATES or r.kind in ("rui_ro_lua_dao", "suc_khoe_an_toan")
        lvl = {"red": 0, "amber": 1, "grey": 2}.get(r.level, 3)
        conf = float(max(r.confidence_exist or 0, r.confidence_content or 0))
        return (0 if top else 1, 0 if (top and defensive) else 1, lvl, -(r.impact or 0), -(r.urgency or 0), -conf,
                0 if r.owner_fact_ids else 1, str(r.id))

    items: list[DigestItem] = []
    clusters: dict[str, list[Any]] = {}
    for r in sorted(rows, key=rank):
        if r.delivery == "digest_low":
            clusters.setdefault(r.kind, []).append(r)
            continue
        items.append(DigestItem(len(items) + 1, str(r.id), r.code, r.kind, r.level, r.delivery, r.gate, r.candidate, r.title,
                                _facts_line(r), _quote(r.evidence), str(r.ask_id) if r.ask_id else None, r.ask_code,
                                state_at_build={str(r.id): (r.level, r.delivery)}))
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
                  {"ask_id": str(q.id), "code": q.code, "summary": q.summary} if q else None)


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
