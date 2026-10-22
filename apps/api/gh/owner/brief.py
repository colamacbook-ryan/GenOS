"""Mặt tiền Owner v2 (v0.1.59, G1) — `GET /owner/brief?id=`: Bản tin cho trang Tình báo.

CHỈ ĐỌC, KHÔNG gọi model, không đọc Gen-hub:

- `latest` = Bản tin Gen ĐÃ LƯU của chính Owner này (hội thoại có tin `kind='briefing'` — cùng kho
  `service.briefing_latest`
  đọc); `id` (mã hội thoại bản tin) chọn một bản trong `history`. Mục Gen-hub (lịch, mail, việc Google) chỉ có ở đây.
- `live` = `gh.gen.briefing.collect(...)` chạy lúc đọc (CHỈ khi không có `id`; xem một bản cũ ⇒ `live: null`): CHỈ đọc
  DB (việc đến hạn, khách nóng, nháp chờ duyệt, sự cố, Facebook, Kho). Không `_hub_sections`, không `_summarize` ⇒
  chưa tới 07:30 vẫn có số.
- Mỗi mục gắn `group` (decide · alert · key) và `to` đổi về đường Mặt tiền (/tasks, /workbench → /owner/viec …).
"""

import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gh.biz.core.scope import not_found
from gh.data.common import iso
from gh.gen import briefing
from gh.owner import service as svc

log = logging.getLogger("gh.owner.brief")

HISTORY_MAX = 14
ALERTS_MAX = 5
SUMMARY_MAX = 1000
LINES_MAX = 5
#: Sự cố không có đường dẫn riêng ⇒ về trang sức khoẻ hệ thống của Console.
HEALTH_LINK = "/system?tab=storage&focus=health"

#: Nhóm hiển thị của từng mục bản tin: Việc phải quyết · Cảnh báo · Điểm chính. Mục lạ ⇒ "key".
GROUP_OF = {
    "drafts_pending": "decide", "tasks_due": "decide", "mail_reply": "decide", "gtasks_open": "decide",
    "incidents": "alert",
    "hot_customers": "key", "calendar_today": "key", "facebook": "key", "kho": "key",
}
#: Đường cũ của Console → đường Mặt tiền (khớp theo phần đường dẫn, bỏ ?query và #neo).
FRONT_PATHS = {
    "/tasks": "/owner/viec", "/workbench": "/owner/viec",
    "/inbox": "/owner/thong-tin",
    "/people": "/owner/quan-he", "/profile": "/owner/quan-he", "/directory": "/owner/quan-he",
}
_STATE_OF = {"error": "error", "breaker": "error", "empty": "empty"}


def front_to(link: Any, default: str = "/owner") -> str:
    """Đổi `link` Console về đường Mặt tiền; đường lạ giữ nguyên nếu bắt đầu bằng '/' (không phải '//'); còn lại về
    `default` (không bao giờ trả link ngoài / chuỗi lạ)."""
    s = str(link or "").strip()
    if not s.startswith("/") or s.startswith("//") or "\\" in s or any(ch in s for ch in "\r\n\t"):
        return default
    path = s.split("?", 1)[0].split("#", 1)[0].rstrip("/") or "/"
    for old, new in FRONT_PATHS.items():
        if path == old or path.startswith(old + "/"):
            return new
    return s


def _section(s: Any) -> dict[str, Any] | None:
    if not isinstance(s, dict) or not s.get("key"):
        return None
    key = str(s["key"])
    count = s.get("count")
    lines = [str(x) for x in (s.get("lines") or []) if isinstance(x, str)][:LINES_MAX]
    n = int(count) if isinstance(count, int | float) and not isinstance(count, bool) else 0
    out: dict[str, Any] = {"key": key, "title": str(s.get("title") or key)[:80], "count": n, "lines": lines,
                           "to": front_to(s.get("link") or s.get("to")), "group": GROUP_OF.get(key, "key")}
    state = _STATE_OF.get(str(s.get("state") or ""))
    if state is None and n == 0 and not lines:
        state = "empty"
    if state:
        out["state"] = state
    if s.get("more"):
        out["more"] = True      # Gen-hub chạm trần ⇒ "10+"
    return out


def _sections(raw: Any) -> list[dict[str, Any]]:
    return [x for x in (_section(s) for s in (raw if isinstance(raw, list) else [])) if x is not None]


def _slot_part(content: dict[str, Any], created_at: datetime) -> str:
    first = str(content.get("slot_label") or "").split(" ", 1)[0]
    if first in ("sáng", "chiều"):
        return first
    return "sáng" if created_at.astimezone(briefing.VN_TZ).hour < 12 else "chiều"


def _summary(content: dict[str, Any]) -> str | None:
    """Tóm tắt do model viết = lời `say` đầu tiên của bản tin. `summary_source == 'none'` (chưa có nguồn AI / tóm tắt
    lỗi) ⇒ lời `say` đầu chỉ là một dòng mục / lời nhắc, không phải tóm tắt ⇒ None."""
    if content.get("summary_source") == "none":
        return None
    for st in content.get("steps") or []:
        if isinstance(st, dict) and st.get("kind") == "say" and isinstance(st.get("text"), str) and st["text"].strip():
            return str(st["text"]).strip()[:SUMMARY_MAX]
    return None


def next_at(now: datetime) -> datetime:
    """Mốc gửi bản tin kế tiếp (07:30 / 17:30 giờ VN) sau `now`."""
    local = now.astimezone(briefing.VN_TZ)
    for back in (0, 1):
        d = local.date() + timedelta(days=back)
        for h, m, _ in briefing.SLOTS:
            at = datetime(d.year, d.month, d.day, h, m, tzinfo=briefing.VN_TZ)
            if at > local:
                return at
    raise AssertionError("luôn có mốc 07:30 ngày mai")     # pragma: no cover


async def _stored(db: AsyncSession, org_id: uuid.UUID, user_id: uuid.UUID, bid: str | None,
                  ) -> tuple[Any | None, list[Any]]:
    """(bản được chọn, tối đa HISTORY_MAX bản gần nhất). `bid` lạ / không phải của Owner này ⇒ 404."""
    base = {"o": org_id, "u": user_id}
    history = list((await db.execute(text(svc.BRIEF_ROWS.format(only="")), {**base, "n": HISTORY_MAX})).all())
    if bid is None:
        return (history[0] if history else None), history
    try:
        cid = uuid.UUID(bid)
    except ValueError:
        raise not_found("Bản tin") from None
    pick = next((r for r in history if r.id == cid), None)
    if pick is None:         # bản cũ hơn 14 bản gần nhất vẫn đọc được nếu đúng của Owner này
        pick = (await db.execute(text(svc.BRIEF_ROWS.format(only="AND c.id = :cid")),
                                 {**base, "cid": cid, "n": 1})).one_or_none()
    if pick is None:
        raise not_found("Bản tin")
    return pick, history


def _latest(r: Any) -> dict[str, Any]:
    content = r.content if isinstance(r.content, dict) else {}
    return {"id": str(r.id), "slot": _slot_part(content, r.created_at), "at": iso(r.created_at),
            "title": svc._text(r.title, 80) or "Bản tin Gen", "summary": _summary(content),
            "needs_api_key": bool(content.get("needs_api_key")), "sections": _sections(content.get("sections"))}


async def _alerts(db: AsyncSession, org_id: uuid.UUID) -> list[dict[str, Any]]:
    rows = (await db.execute(text("""
        SELECT key, title, severity, raised_at, link FROM ops.health_alerts
        WHERE org_id = :o AND cleared_at IS NULL ORDER BY (severity = 'bad') DESC, raised_at DESC LIMIT :n"""),
                             {"o": org_id, "n": ALERTS_MAX})).all()
    return [{"key": str(r.key), "title": svc._text(r.title, 160), "severity": "bad" if r.severity == "bad" else "warn",
             "raised_at": iso(r.raised_at), "to": front_to(r.link, HEALTH_LINK)} for r in rows]


async def ai_ready(db: AsyncSession, org_id: uuid.UUID) -> bool:
    """Có ít nhất một nguồn AI đạt — mục n=4 của `follow_up_status` (= `readiness.ai_ready`; cùng nguồn dữ liệu thẻ
    `NoModelNotice` và `model.bound` của Gen hướng dẫn dùng). Không đọc được ⇒ coi là chưa."""
    from gh.setup.routes import follow_up_status

    try:
        async with db.begin_nested():
            row = (await db.execute(text("SELECT org_id, completed FROM ops.setup_state WHERE org_id = :o"),
                                    {"o": org_id})).one_or_none()
            if row is None:
                return False
            return any(bool(it.get("done")) for it in await follow_up_status(db, row) if it.get("n") == 4)
    except Exception as exc:  # noqa: BLE001 — chỉ là cờ hiển thị
        log.warning("ai_ready lỗi: %s", type(exc).__name__)
        return False


async def brief(db: AsyncSession, org_id: uuid.UUID, user_id: uuid.UUID, bid: str | None = None,
                *, now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    pick, history = await _stored(db, org_id, user_id, bid)
    # Xem một bản CŨ (`?id=`): không chạy `collect` (6+ truy vấn) — số liệu lúc này đã có ở lượt tải bản mới nhất;
    # `live` = null (web vốn chỉ lấy `live` từ lượt tải không `id`).
    live = None
    if bid is None:
        collected = await briefing.collect(db, org_id, briefing.slot_for(now), now)   # chỉ đọc DB, savepoint từng mục
        live = {"at": iso(now), "sections": _sections(collected)}
    return {
        "latest": _latest(pick) if pick is not None else None,
        "live": live,
        "alerts": await _alerts(db, org_id),
        "history": [{"id": str(r.id), "slot": _slot_part(r.content if isinstance(r.content, dict) else {},
        r.created_at),
                     "at": iso(r.created_at), "title": svc._text(r.title, 80) or "Bản tin Gen"} for r in history],
        "next_at": iso(next_at(now)),
        "ai_ready": await ai_ready(db, org_id),
        "channels_connected": await svc.channels_connected(db, org_id),
    }
