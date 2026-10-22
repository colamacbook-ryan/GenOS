"""Mặt tiền Owner (v0.1.55, gói G5) — số liệu CHỈ ĐỌC cho 5 màn `/owner/*` (Hôm nay, Việc, Quan hệ, Hỏi Gen, Thêm).

Nguyên tắc:
- Chỉ ĐỌC. Không ghi bảng nào, không gọi model, không thêm bảng/migration/chỉ mục. Mọi thao tác ghi dẫn link sâu tới
  luồng đã có (duyệt nháp ở Bàn làm việc, đề xuất Gen + mã PIN, Bộ não AI) — trường `to` luôn là đường dẫn trong
  Console.
- Mọi truy vấn danh sách có LIMIT; đếm dùng `SELECT count(*) FROM (… LIMIT cap)` nên không quét hết bảng lớn (đếm
  chạm trần `COUNT_CAP` thì web hiện "999+"). Chỉ dùng chỉ mục sẵn có (xem chú thích từng câu).
- Chữ trả về là tiếng Việt đời thường — không có thuật ngữ kỹ thuật (model/token/API) ở Hôm nay và Quan hệ. Không
  trả khoá, bí mật, nội dung hội thoại Gen (chỉ nhãn tĩnh của loại đề xuất / tiêu đề bản tin).
- Hai hợp đồng của gói khác: `gh.refinery.triage.value_summary` (G4) và `gh.defaults.registry.suggestions` (G1). Lỗi
  của chúng không làm sập `GET /owner/today` (bọc savepoint để giao dịch không bị hỏng; lỗi ⇒ giá trị rỗng).
"""

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gh.biz.graph.jobs import COLD_DAYS, WINDOW_DAYS
from gh.biz.market.service import OPEN_STAGES
from gh.biz.queue.service import EVENT_LABELS, not_silenced_sql
from gh.boss_checks import service as boss_service
from gh.data.common import iso
from gh.defaults import registry as defaults_registry
from gh.gen import proposals
from gh.hub_link import load_kho_label, relabel
from gh.refinery import triage

log = logging.getLogger("gh.owner")

LIST_NAMES = ("hot", "cooling", "bridges", "matches", "care")
DEFAULT_LIMIT = 20
MAX_LIMIT = 50
#: "Cần Sếp duyệt" tối đa 10 dòng; mỗi nguồn lấy tối đa PER_SOURCE rồi xếp theo mức khẩn.
REVIEW_MAX = 10
PER_SOURCE = 5
#: Mỗi nhóm của màn Việc cho tối đa 5 dòng mẫu (còn lại bấm link sâu).
GROUP_ITEMS = 5
#: Trần đếm: đếm chạm trần thì web hiện "999+" — không quét hết bảng lớn chỉ để ra một con số.
COUNT_CAP = 999
#: Số cơ hội đang mở dùng để cộng giá trị — một tổ chức nhỏ không bao giờ tới trần này.
OPP_CAP = 5000
#: Điểm nóng từ mức này là "khách nóng" (cùng ngưỡng "cao" của Danh bạ/Bản đồ quan hệ).
HOT_MIN = 80
#: Bản tin: cắt lời tóm tắt để thẻ ở Hôm nay gọn.
SUMMARY_MAX = 240
#: Đề xuất của Gen sống 24 giờ (gh.gen.proposals.PROPOSAL_TTL_S) — chỉ tìm trong cửa sổ đó.
PROPOSAL_WINDOW_H = 24
#: "Khách cần chăm" (v0.1.59): rủi ro rời bỏ từ mức này, HOẶC đã nhắn ≥ CARE_MIN_MSGS tin mà ≥ CARE_SILENT_DAYS ngày
#: không có tin mới. Phạm vi "khách" = mọi người KHÔNG thuộc CARE_EXCLUDED_TYPES (gồm cả `unknown`).
CARE_CHURN_MIN = 60
CARE_SILENT_DAYS = 14
CARE_MIN_MSGS = 3
CARE_EXCLUDED_TYPES = ("staff", "candidate", "learner")
#: Chỉ xét người có tin trong cửa sổ này (ngày) cho luật "im lặng" — không dò cả nghìn người ngoài lề và cắt được
#: phân vùng `raw.events`. Người im lặng quá lâu vẫn hiện nếu rủi ro rời bỏ ≥ CARE_CHURN_MIN.
CARE_WINDOW_DAYS = 180
#: Kênh KHÔNG tính là "kênh nhận tin" (Facebook/Telegram không đưa tin khách về Thông tin; LinkedIn chỉ đọc danh tính).
NON_INTAKE_CHANNELS = ("facebook", "telegram", "linkedin")

BG_ALERT_KEY = "ai.background_no_source"
#: Dùng chung MỘT bản chữ với registry mặc định (F024: trước đây hai tiêu đề khác nhau cho cùng một gợi ý).
BG_SUGGESTION = defaults_registry.BACKGROUND_KEY_SUGGESTION

DRAFT_LABELS = {"message": "Tin nhắn chờ duyệt", "quotation": "Báo giá chờ duyệt", "contract": "Hợp đồng chờ duyệt",
                "reminder": "Nhắc việc chờ duyệt", "report": "Báo cáo chờ duyệt",
                "mcp_write": "Thao tác ghi chờ duyệt"}
PERSON_LABELS = {"customer": "Khách hàng", "partner": "Đối tác", "staff": "Nhân viên", "candidate": "Ứng viên",
                 "learner": "Học viên", "supplier": "Nhà cung cấp"}

VALUE_ZERO: dict[str, Any] = {"filtered": 0, "spam_blocked": 0, "calls_saved": 0, "jev_on": False}


def clamp_limit(limit: int | None) -> int:
    """`limit` mặc định 20, chặn trong [1, 50] (không báo lỗi — danh sách ngắn hơn là đủ cho điện thoại)."""
    if limit is None:
        return DEFAULT_LIMIT
    return max(1, min(int(limit), MAX_LIMIT))


def profile_link(person_id: Any) -> str:
    return f"/profile?id={person_id}"


def _person_subtitle(person_type: str | None, org_name: str | None) -> str:
    label = PERSON_LABELS.get(person_type or "", "")
    parts = [p for p in (label, (org_name or "").strip()) if p]
    return " · ".join(parts) if parts else "Chưa phân loại"


def _text(v: Any, n: int = 120) -> str:
    return str(v or "").strip()[:n]


async def _count(db: AsyncSession, sql: str, params: dict[str, Any]) -> int:
    """`sql` là câu `SELECT 1 FROM … [LIMIT :cap]` bên trong; trả số dòng, chặn ở `COUNT_CAP`."""
    n = (await db.execute(text(f"SELECT count(*) FROM ({sql}) x"), {**params, "cap": COUNT_CAP})  # noqa: S608
         ).scalar_one()
    return int(n or 0)


async def channels_connected(db: AsyncSession, org_id: uuid.UUID) -> int:
    """Số kênh NHẬN TIN (Zalo, WhatsApp…) đang có phiên hoạt động HOẶC đã từng đăng nhập thành công rồi chỉ MẤT phiên.
    KHÁC `GET /header` › `channels_connected` (v0.1.43): header đếm mọi kênh từng đăng nhập kể cả Facebook/Telegram/
    LinkedIn; ở đây các kênh đó bị loại (không đưa tin khách về Thông tin) và kênh Sếp đã chủ động ĐĂNG XUẤT cũng không
    tính. 0 ⇒ "ngày đầu": Mặt tiền hiện một thẻ "Nối Zalo" thay các khối số — đăng xuất kênh duy nhất cũng về đây (có
    chỉ dẫn nối lại, P3 v0.1.70). Kênh đã nối mà mất phiên (cần quét lại QR) vẫn tính — nếu không Mặt tiền ẩn hết tin
    cũ trong khi huy hiệu vẫn đếm (P3 v0.1.61). Phiên đăng nhập sau lần đăng xuất lại được tính. Dùng chung cho brief /
    feed / insights."""
    n = (await db.execute(text("""
        SELECT count(DISTINCT c.id) FROM core.channels c JOIN core.channel_sessions s ON s.channel_id = c.id
        WHERE c.org_id = :o AND NOT (c.type = ANY(:skip))
          AND ((s.state = 'active' AND s.ended_at IS NULL)
               OR (s.started_at IS NOT NULL AND s.state <> 'logged_out'
                   AND NOT EXISTS (SELECT 1 FROM core.channel_sessions l
                                   WHERE l.channel_id = s.channel_id AND l.state = 'logged_out'
                                     AND l.ended_at > s.started_at)))"""),
                          {"o": org_id, "skip": list(NON_INTAKE_CHANNELS)})).scalar_one()
    return int(n or 0)


async def pending_drafts(db: AsyncSession, org_id: uuid.UUID) -> int:
    """Số bản nháp chờ duyệt, KHÔNG giới hạn tuổi, bỏ nháp đã "Tắt tiếng" — khớp `feed.counts.approval` (cùng
    `not_silenced_sql`) và màn Việc; chỉ mục (org_id, status, created_at DESC)."""
    n = (await db.execute(text(f"""
        SELECT count(*) FROM (SELECT 'draft' AS item_type, id AS item_id FROM biz.action_drafts
                              WHERE org_id = :o AND status = 'pending') i
        WHERE {not_silenced_sql("i")}"""), {"o": org_id})).scalar_one()  # noqa: S608
    return int(n or 0)


# ─── cầu mềm tới hợp đồng của gói khác ───────────────────────────────────────────────────────────────────────────

def _bridge(mod: Any, attr: str) -> Callable[..., Awaitable[Any]] | None:
    """Hàm `attr` của `mod` nếu có. Tra lúc gọi để test thay bằng monkeypatch."""
    fn = getattr(mod, attr, None)
    return fn if callable(fn) else None


async def filter_value(db: AsyncSession, org_id: uuid.UUID) -> dict[str, Any]:
    """`gh.refinery.triage.value_summary(db, org_id)` (hợp đồng G4): {filtered, spam_blocked, calls_saved, jev_on}."""
    fn = getattr(triage, "value_summary", None)
    out = dict(VALUE_ZERO)
    if fn is None:
        return out
    try:
        async with db.begin_nested():
            raw = await fn(db, org_id)
    except Exception as exc:  # noqa: BLE001 — số liệu phụ không được làm sập Hôm nay
        log.warning("value_summary lỗi: %s", type(exc).__name__)
        return out
    if isinstance(raw, dict):
        for k in ("filtered", "spam_blocked", "calls_saved"):
            v = raw.get(k)
            out[k] = int(v) if isinstance(v, int | float) and not isinstance(v, bool) else 0
        out["jev_on"] = bool(raw.get("jev_on"))
    return out


async def suggestions(db: AsyncSession, org_id: uuid.UUID) -> list[dict[str, str]]:
    """`gh.defaults.registry.suggestions(db, org_id)` (hợp đồng G1) + tình trạng nguồn nền.

    Phần "nguồn nền" luôn tự thêm `background_key_missing` khi sự cố
    `ai.background_no_source` đang mở mà registry chưa nêu (không trùng khoá)."""
    out: list[dict[str, str]] = []
    fn = _bridge(defaults_registry, "suggestions")
    if fn is not None:
        try:
            async with db.begin_nested():
                raw = await fn(db, org_id)
        except Exception as exc:  # noqa: BLE001
            log.warning("suggestions lỗi: %s", type(exc).__name__)
            raw = []
        for it in raw if isinstance(raw, list) else []:
            if not isinstance(it, dict):
                continue
            row = {k: it.get(k) for k in ("key", "title", "body", "to")}
            if all(isinstance(v, str) and v for v in row.values()):
                out.append({k: str(v) for k, v in row.items()})
    if all(s["key"] != BG_SUGGESTION["key"] for s in out):
        open_ = (await db.execute(text("""
            SELECT 1 FROM ops.health_alerts WHERE org_id = :o AND key = :k AND cleared_at IS NULL LIMIT 1"""),
                                  {"o": org_id, "k": BG_ALERT_KEY})).first()
        if open_ is not None:
            out.append(dict(BG_SUGGESTION))
    return out


# ─── GET /owner/today ────────────────────────────────────────────────────────────────────────────────────────────

async def proposal_messages(db: AsyncSession, org_id: uuid.UUID, user_id: uuid.UUID, limit: int) -> list[Any]:
    """Tin trả lời của Gen (role assistant) trong `PROPOSAL_WINDOW_H` giờ gần nhất, trong hội thoại của CHÍNH Owner này
    (cid, created_at, content) — nơi chứa bước `proposal`. Dùng chung cho "Cần Sếp duyệt" (needs_review) và
    `GET /owner/proposals` (proposals_view). Chỉ chỉ mục gen_conversations_user_idx + gen_messages_conv_idx."""
    return list((await db.execute(text("""
        SELECT c.id AS cid, m.created_at, m.content
        FROM agent.gen_conversations c JOIN agent.gen_messages m ON m.conversation_id = c.id
        WHERE c.org_id = :o AND c.user_id = :u AND c.last_at > now() - make_interval(hours => :h)
          AND m.role = 'assistant' AND m.created_at > now() - make_interval(hours => :h)
        ORDER BY m.created_at DESC LIMIT :n"""), {"o": org_id, "u": user_id, "h": PROPOSAL_WINDOW_H,
                                                  "n": limit})).all())


async def needs_review(db: AsyncSession, org_id: uuid.UUID, user_id: uuid.UUID) -> list[dict[str, Any]]:
    """Việc chờ Sếp: đề xuất của Gen chưa xác nhận (hết hạn sau 24 giờ ⇒ xếp trước), bản nháp chờ duyệt, việc quá hạn.
    Tối đa `REVIEW_MAX`. Chỉ ĐỌC — duyệt/xác nhận làm ở luồng sẵn có (link `to`)."""
    items: list[dict[str, Any]] = []

    # Đề xuất Gen chưa xác nhận: nằm trong bước `proposal` của tin trả lời (status 'pending'), 24 giờ gần nhất.
    rows = await proposal_messages(db, org_id, user_id, 40)
    seen: set[str] = set()
    kho = await load_kho_label(db, org_id)                     # v0.1.57: tên Kho Owner tự đặt (Nợ #30)
    for r in rows:
        content = r.content if isinstance(r.content, dict) else {}
        for st in content.get("steps") or []:
            p = st.get("proposal") if isinstance(st, dict) and st.get("kind") == "proposal" else None
            if not isinstance(p, dict) or p.get("status") != "pending" or str(p.get("id")) in seen:
                continue
            seen.add(str(p.get("id")))
            label = relabel(proposals.TYPE_LABELS.get(str(p.get("type")), "Một thao tác"), kho)
            pin = " (cần mã PIN)" if p.get("requires_pin") else ""
            items.append({"kind": "proposal", "title": f"Gen đề xuất: {label}{pin}",
                          "to": f"/owner/gen?gen={r.cid}", "at": iso(r.created_at)})
            if len(items) >= PER_SOURCE:
                break
        if len(items) >= PER_SOURCE:
            break

    # Bản nháp chờ duyệt — chỉ mục (org_id, status, created_at DESC).
    for d in (await db.execute(text("""
            SELECT id, code, kind, title, created_at FROM biz.action_drafts
            WHERE org_id = :o AND status = 'pending' ORDER BY created_at DESC LIMIT :n"""),
                                   {"o": org_id, "n": PER_SOURCE})).all():
        items.append({"kind": "draft", "title": _text(d.title) or DRAFT_LABELS.get(d.kind, "Bản nháp chờ duyệt"),
                      "to": f"/workbench?id={d.id}", "at": iso(d.created_at)})

    # Việc quá hạn — chỉ mục (org_id, status, due_at).
    for t in (await db.execute(text("""
            SELECT id, title, due_at FROM biz.tasks
            WHERE org_id = :o AND status NOT IN ('done', 'cancelled') AND due_at < now()
            ORDER BY due_at LIMIT :n"""), {"o": org_id, "n": PER_SOURCE})).all():
        items.append({"kind": "overdue_task", "title": _text(t.title) or "Việc quá hạn",
                      "to": "/tasks?overdue=true", "at": iso(t.due_at)})
    return items[:REVIEW_MAX]


async def kpis(db: AsyncSession, org_id: uuid.UUID) -> dict[str, int]:
    """4 số của Hôm nay: khách nóng, quan hệ nguội, cơ hội đang mở (+ tổng giá trị), lời hứa quá hạn."""
    hot = await _count(db, """
        SELECT 1 FROM clean.current_scores cs JOIN core.persons p ON p.id = cs.subject_id
        WHERE cs.subject_type = 'person' AND cs.dimension = 'heat' AND cs.value >= :hot AND p.org_id = :o
          AND p.deleted_at IS NULL AND p.merged_into_id IS NULL LIMIT :cap""", {"o": org_id, "hot": HOT_MIN})
    # Quan hệ giữa hai người, lâu chưa liên lạc — chỉ mục (org_id, kind, window_days).
    cooling = await _count(db, """
        SELECT 1 FROM clean.relationships r
        WHERE r.org_id = :o AND r.kind = 'interacts' AND r.window_days = :w AND r.from_type = 'person'
          AND r.to_type = 'person' AND r.last_at < now() - make_interval(days => :d) LIMIT :cap""",
                         {"o": org_id, "w": WINDOW_DAYS, "d": COLD_DAYS})
    opps = (await db.execute(text("""
        SELECT count(*) AS n, COALESCE(sum(value_vnd), 0) AS v FROM (
          SELECT value_vnd FROM biz.opportunities
          WHERE org_id = :o AND closed_at IS NULL AND stage = ANY(CAST(:st AS text[])) LIMIT :cap) x"""),
                            {"o": org_id, "st": list(OPEN_STAGES), "cap": OPP_CAP})).one()
    # Lời hứa quá hạn chưa giữ — chỉ mục (org_id, promiser_person_id, due_at).
    promises = await _count(db, """
        SELECT 1 FROM biz.promises WHERE org_id = :o AND kept_at IS NULL AND due_at < now() LIMIT :cap""",
                            {"o": org_id})
    return {"hot": hot, "cooling": cooling, "open_opps": int(opps.n or 0), "open_value_vnd": int(opps.v or 0),
            "overdue_promises": promises}


#: Bản tin Gen đã lưu của MỘT Owner: tin `kind='briefing'` mới nhất của MỖI hội thoại (không giới hạn "N hội thoại gần
#: nhất" — Owner chat nhiều thì bản tin cũ vẫn tìm thấy; F-60-R11). Chỉ mục gen_conversations_user_idx (org_id,
#: user_id, last_at) rồi gen_messages_conv_idx (conversation_id, created_at) cho từng hội thoại; `{only}` thêm điều
#: kiện hội thoại.
BRIEF_ROWS = """
    SELECT c.id, c.title, m.created_at, m.content
    FROM agent.gen_conversations c
    JOIN LATERAL (SELECT created_at, content FROM agent.gen_messages
                  WHERE conversation_id = c.id AND role = 'assistant' AND content->>'kind' = 'briefing'
                  ORDER BY created_at DESC LIMIT 1) m ON true
    WHERE c.org_id = :o AND c.user_id = :u {only}
    ORDER BY m.created_at DESC LIMIT :n"""


async def briefing_latest(db: AsyncSession, org_id: uuid.UUID, user_id: uuid.UUID) -> dict[str, Any] | None:
    """Bản tin Gen mới nhất của chính Owner này (hội thoại có tin `kind='briefing'`) — `BRIEF_ROWS`, không giới hạn
    theo số hội thoại gần nhất."""
    r = (await db.execute(text(BRIEF_ROWS.format(only="")), {"o": org_id, "u": user_id, "n": 1})).one_or_none()
    if r is None:
        return None
    summary = ""
    content = r.content if isinstance(r.content, dict) else {}
    for st in content.get("steps") or []:
        if isinstance(st, dict) and st.get("kind") == "say" and isinstance(st.get("text"), str) and st["text"].strip():
            summary = st["text"].strip()[:SUMMARY_MAX]
            break
    return {"title": _text(r.title, 80) or "Bản tin Gen", "at": iso(r.created_at), "summary_text": summary,
            "to": f"/owner/gen?gen={r.id}"}


async def today(db: AsyncSession, org_id: uuid.UUID, user_id: uuid.UUID) -> dict[str, Any]:
    overview = await boss_service.overview(db, org_id)
    return {
        "needs_review": await needs_review(db, org_id, user_id),
        "kpis": await kpis(db, org_id),
        "briefing_latest": await briefing_latest(db, org_id, user_id),
        "filter_value": await filter_value(db, org_id),
        "suggestions": await suggestions(db, org_id),
        # Số việc bắt buộc lấy từ "Việc Sếp cần làm" — không ghi cứng.
        "progress": {"required_done": int(overview["required_done"]),
                     "required_total": int(overview["required_total"])},
    }


# ─── GET /owner/relations ────────────────────────────────────────────────────────────────────────────────────────

def _days_text(days: int) -> str:
    return "Chưa tới 1 ngày" if days < 1 else f"{days} ngày chưa liên lạc"


#: Lần liên lạc gần nhất của một người: 3 tin mới nhất họ gửi (mọi danh tính của họ) ⇒ `n` (≤ 3) và `last_at`.
#: Chỉ mục (sender_identity_id, occurred_at DESC) — không quét hết tin của người đó. `{pid}` = biểu thức id người.
_LAST_CONTACT = """
    LEFT JOIN LATERAL (
      SELECT count(*) AS n, max(t.occurred_at) AS last_at FROM (
        SELECT e.occurred_at FROM core.person_identities pi JOIN raw.events e ON e.sender_identity_id = pi.id
        WHERE pi.person_id = {pid} AND e.org_id = :o ORDER BY e.occurred_at DESC LIMIT 3) t) lc ON true"""
_LAST_DAYS = "floor(extract(epoch FROM now() - lc.last_at) / 86400)::int"
#: Giá trị cơ hội còn mở của một người (VND).
_OPEN_VALUE = """(SELECT COALESCE(sum(o.value_vnd), 0) FROM biz.opportunities o
                  WHERE o.org_id = :o AND o.person_id = p.id AND o.closed_at IS NULL
                    AND o.stage = ANY(CAST(:open_st AS text[])))"""
#: Người này đã có việc / nhắc hẹn ĐANG MỞ gắn vào hồ sơ (web "Gen đề xuất" không gợi ý tạo nhắc trùng).
_OPEN_TASK = """EXISTS (SELECT 1 FROM biz.tasks tk WHERE tk.org_id = :o AND tk.subject_type = 'person'
                          AND tk.subject_id = p.id AND tk.status NOT IN ('done', 'cancelled'))"""
_SCORE_JOIN = """LEFT JOIN clean.current_scores {a} ON {a}.subject_type = 'person' AND {a}.subject_id = p.id
                 AND {a}.dimension = '{dim}'"""


def _num(v: Any) -> int | None:
    return None if v is None else int(round(float(v)))


def _person_extras(r: Any) -> dict[str, Any]:
    """Trường tuỳ chọn của dòng có người (khách nóng / cần chăm): chỉ thêm khoá khi có giá trị."""
    out: dict[str, Any] = {"person_id": str(r.id)}
    for key, val in (("heat", _num(getattr(r, "heat", None))), ("churn_risk", _num(getattr(r, "churn_risk", None))),
                     ("last_contact_days", getattr(r, "days", None)),
                     ("open_value_vnd", _num(getattr(r, "open_value", None)))):
        if val is not None:
            out[key] = val
    return out


async def relations(db: AsyncSession, org_id: uuid.UUID, list_name: str, limit: int) -> dict[str, Any]:
    """Một trong 5 danh sách của màn Quan hệ. Mỗi dòng {id, name, subtitle, metric_text, to} (+ trường tuỳ chọn:
    person_id, heat, churn_risk, last_contact_days, reason, open_value_vnd, match_id); `to` mở Hồ sơ sống."""
    n = clamp_limit(limit)
    items: list[dict[str, Any]] = []
    if list_name == "hot":
        # Chỉ mục khoá chính của clean.current_scores (subject_type, subject_id, dimension) + persons theo id.
        for r in (await db.execute(text(f"""
                SELECT p.id, p.display_name AS name, p.person_type AS type, p.organization_name AS org_name,
                       cs.value AS heat, cs.trend, churn.value AS churn_risk, {_LAST_DAYS} AS days,
                       {_OPEN_VALUE} AS open_value
                FROM clean.current_scores cs JOIN core.persons p ON p.id = cs.subject_id
                {_SCORE_JOIN.format(a="churn", dim="churn_risk")}
                {_LAST_CONTACT.format(pid="p.id")}
                WHERE cs.subject_type = 'person' AND cs.dimension = 'heat' AND cs.value >= :hot AND p.org_id = :o
                  AND p.deleted_at IS NULL AND p.merged_into_id IS NULL
                ORDER BY cs.value DESC, cs.updated_at DESC LIMIT :n"""),  # noqa: S608
                {"o": org_id, "hot": HOT_MIN, "n": n, "open_st": list(OPEN_STAGES)})).all():
            trend = {"up": " · đang tăng", "down": " · đang giảm"}.get(r.trend or "", "")
            items.append({"id": str(r.id), "name": _text(r.name, 80) or "Chưa có tên",
                          "subtitle": _person_subtitle(r.type, r.org_name),
                          "metric_text": f"Độ nóng {round(float(r.heat))}{trend}", "to": profile_link(r.id),
                          **_person_extras(r)})
    elif list_name == "cooling":
        # Chỉ mục (org_id, kind, window_days); cạnh mạnh nhất đang nguội lên trước.
        for r in (await db.execute(text("""
                SELECT r.id, a.id AS a_id, a.display_name AS a_name, b.display_name AS b_name, r.topic,
                       floor(extract(epoch FROM now() - r.last_at) / 86400)::int AS days
                FROM clean.relationships r
                JOIN core.persons a ON a.id = r.from_id AND a.deleted_at IS NULL AND a.merged_into_id IS NULL
                JOIN core.persons b ON b.id = r.to_id AND b.deleted_at IS NULL AND b.merged_into_id IS NULL
                WHERE r.org_id = :o AND r.kind = 'interacts' AND r.window_days = :w AND r.from_type = 'person'
                  AND r.to_type = 'person' AND r.last_at < now() - make_interval(days => :d)
                ORDER BY r.weight DESC, r.last_at LIMIT :n"""), {"o": org_id, "w": WINDOW_DAYS, "d": COLD_DAYS,
                                                                 "n": n})).all():
            items.append({"id": str(r.id), "name": f"{_text(r.a_name, 60)} và {_text(r.b_name, 60)}",
                          "subtitle": f"Hay trao đổi về {_text(r.topic, 60)}" if r.topic else "Hay trao đổi cùng nhóm",
                          "metric_text": _days_text(int(r.days or 0)), "to": profile_link(r.a_id),
                          "person_id": str(r.a_id), "last_contact_days": int(r.days or 0)})
    elif list_name == "bridges":
        # Người cầu nối: cạnh `bridges` Người→Nhóm (gh.biz.graph.jobs) — gộp theo người.
        for r in (await db.execute(text("""
                SELECT p.id, p.display_name AS name, p.person_type AS type, p.organization_name AS org_name,
                       count(*) AS groups, max(r.weight) AS pairs
                FROM clean.relationships r JOIN core.persons p ON p.id = r.from_id
                WHERE r.org_id = :o AND r.kind = 'bridges' AND r.window_days = :w AND r.from_type = 'person'
                  AND p.deleted_at IS NULL AND p.merged_into_id IS NULL
                GROUP BY p.id, p.display_name, p.person_type, p.organization_name
                ORDER BY max(r.weight) DESC, p.display_name LIMIT :n"""),
                {"o": org_id, "w": WINDOW_DAYS, "n": n})).all():
            pairs = int(round(float(r.pairs or 0)))
            items.append({"id": str(r.id), "name": _text(r.name, 80) or "Chưa có tên",
                          "subtitle": f"Có mặt ở {int(r.groups)} nhóm · {_person_subtitle(r.type, r.org_name)}",
                          "metric_text": f"Nối {pairs} cặp nhóm" if pairs > 0 else "Người nối các nhóm",
                          "to": profile_link(r.id), "person_id": str(r.id)})
    elif list_name == "care":
        # Savepoint + statement_timeout như Phân tích: quá giờ / lỗi ⇒ state "error" chứ không treo cả màn.
        from gh.owner.insights import guarded  # tránh import vòng (insights nạp service)
        try:
            async with guarded(db):
                items = await _care(db, org_id, n)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — danh sách này lỗi không được làm hỏng cả màn
            log.warning("Quan hệ: danh sách care lỗi (%s)", type(exc).__name__)
            return {"list": list_name, "items": [], "state": "error"}
    else:  # matches
        # Cung ↔ Cầu (biz.matches): còn hiệu lực = gợi ý mới hoặc đã giới thiệu; chỉ mục (org_id, status).
        for r in (await db.execute(text("""
                SELECT m.id, m.score, d.item AS d_item, s.item AS s_item, d.person_id AS d_person_id,
                       s.person_id AS s_person_id, COALESCE(dp.display_name, dg.name) AS d_who,
                       COALESCE(sp.display_name, sg.name) AS s_who
                FROM biz.matches m
                JOIN biz.market_signals d ON d.id = m.demand_id JOIN biz.market_signals s ON s.id = m.supply_id
                LEFT JOIN core.persons dp ON dp.id = d.person_id LEFT JOIN core.groups dg ON dg.id = d.group_id
                LEFT JOIN core.persons sp ON sp.id = s.person_id LEFT JOIN core.groups sg ON sg.id = s.group_id
                WHERE m.org_id = :o AND m.status IN ('suggested', 'introduced')
                ORDER BY m.score DESC, m.created_at DESC LIMIT :n"""), {"o": org_id, "n": n})).all():
            who_id = r.d_person_id or r.s_person_id
            row: dict[str, Any] = {
                "id": str(r.id), "name": f"{_text(r.d_item, 60)} ↔ {_text(r.s_item, 60)}",
                "subtitle": f"{_text(r.d_who, 50) or 'Bên cần'} cần · {_text(r.s_who, 50) or 'Bên có'} có",
                "metric_text": f"Khớp {round(float(r.score))}%",
                "to": profile_link(who_id) if who_id else "/supply", "match_id": str(r.id)}
            if who_id:
                row["person_id"] = str(who_id)
            items.append(row)
    return {"list": list_name, "items": items}


async def _care(db: AsyncSession, org_id: uuid.UUID, n: int) -> list[dict[str, Any]]:
    """"Khách cần chăm": người KHÔNG phải nhân viên/ứng viên/học viên (gồm `unknown`), chưa xoá/gộp, mà rủi ro rời bỏ
    ≥ CARE_CHURN_MIN (lý do `churn`) HOẶC đã nhắn ≥ CARE_MIN_MSGS tin và ≥ CARE_SILENT_DAYS ngày không có tin mới (lý do
    `silent`). Xếp rủi ro giảm dần rồi số ngày im lặng giảm dần.

    Chặn chi phí (F-60-R3): tập ỨNG VIÊN lấy trước bằng MỘT lần quét `raw.events` trong CARE_WINDOW_DAYS (cắt phân
    vùng, gom theo người) + các người có điểm rủi ro cao; chỉ ứng viên mới đi dò "lần liên lạc gần nhất". Người ở nhóm
    Zalo `unknown` chưa từng nhắn không bị dò."""
    rows = (await db.execute(text(f"""
        WITH recent AS (
          SELECT pi.person_id FROM raw.events e JOIN core.person_identities pi ON pi.id = e.sender_identity_id
          WHERE e.org_id = :o AND e.received_at >= now() - make_interval(days => :win)
          GROUP BY pi.person_id
          HAVING count(*) >= :msgs AND max(e.occurred_at) < now() - make_interval(days => :silent)
        ), cand AS (
          SELECT person_id AS id FROM recent
          UNION
          SELECT subject_id FROM clean.current_scores
          WHERE subject_type = 'person' AND dimension = 'churn_risk' AND value >= :churn
        )
        SELECT p.id, p.display_name AS name, p.person_type AS type, p.organization_name AS org_name,
               heat.value AS heat, churn.value AS churn_risk, lc.n AS msgs, {_LAST_DAYS} AS days,
               {_OPEN_VALUE} AS open_value, {_OPEN_TASK} AS open_task
        FROM cand JOIN core.persons p ON p.id = cand.id
        {_SCORE_JOIN.format(a="churn", dim="churn_risk")}
        {_SCORE_JOIN.format(a="heat", dim="heat")}
        {_LAST_CONTACT.format(pid="p.id")}
        WHERE p.org_id = :o AND p.deleted_at IS NULL AND p.merged_into_id IS NULL
          AND NOT (p.person_type = ANY(:skip))
          AND (COALESCE(churn.value, 0) >= :churn
               OR (lc.n >= :msgs AND lc.last_at < now() - make_interval(days => :silent)))
        ORDER BY churn.value DESC NULLS LAST, {_LAST_DAYS} DESC NULLS LAST, p.display_name
        LIMIT :n"""),  # noqa: S608
        {"o": org_id, "skip": list(CARE_EXCLUDED_TYPES), "churn": CARE_CHURN_MIN, "msgs": CARE_MIN_MSGS,
         "silent": CARE_SILENT_DAYS, "win": CARE_WINDOW_DAYS, "n": n, "open_st": list(OPEN_STAGES)})).all()
    out: list[dict[str, Any]] = []
    for r in rows:
        churn = r.churn_risk is not None and float(r.churn_risk) >= CARE_CHURN_MIN
        quiet = _days_text(int(r.days)) if r.days is not None else "Chưa có tin nào"
        out.append({"id": str(r.id), "name": _text(r.name, 80) or "Chưa có tên",
                    "subtitle": _person_subtitle(r.type, r.org_name),
                    "metric_text": f"Có thể sắp rời · {quiet}" if churn else quiet, "to": profile_link(r.id),
                    "reason": "churn" if churn else "silent", **_person_extras(r),
                    **({"has_open_task": True} if r.open_task else {})})
    return out


# ─── GET /owner/tasks ────────────────────────────────────────────────────────────────────────────────────────────

async def tasks(db: AsyncSession, org_id: uuid.UUID) -> dict[str, Any]:
    """3 nhóm cho màn Việc: Hộp thư đã lọc, Bàn làm việc, Việc & Nhắc hẹn. Chỉ đếm + vài dòng mẫu + link sâu."""
    cfg = await triage.get_settings(db, org_id)
    minq = int(cfg["min_score"])

    # Hộp thư đã lọc: cảnh báo đang mở + đơn vị ý nghĩa 7 ngày gần nhất không phải rác/trùng/điểm thấp (tổ chức tắt lọc
    # ⇒ lấy hết). Chỉ mục alerts (org_id, status, …), item_marks (org_id, observed_at DESC), meaning_units
    # (org_id, observed_at).
    alerts = (await db.execute(text("""
        SELECT id, title, created_at FROM biz.alerts WHERE org_id = :o AND status = 'open'
        ORDER BY created_at DESC LIMIT :n"""), {"o": org_id, "n": GROUP_ITEMS})).all()
    if cfg["enabled"]:
        kept_where = ("FROM refinery.item_marks m JOIN clean.meaning_units mu ON mu.id = m.item_id "
                      "AND mu.observed_at = m.observed_at WHERE m.org_id = :o AND m.item_type = 'unit' "
                      "AND m.observed_at > now() - interval '7 days' AND NOT m.is_spam AND m.duplicate_of IS NULL "
                      "AND m.quality >= :q AND mu.superseded_by IS NULL")
        order = "m.observed_at"
    else:
        kept_where = ("FROM clean.meaning_units mu WHERE mu.org_id = :o AND mu.superseded_by IS NULL "
                      "AND mu.observed_at > now() - interval '7 days'")
        order = "mu.observed_at"
    p = {"o": org_id, "q": minq}
    kept_n = await _count(db, f"SELECT 1 {kept_where} LIMIT :cap", p)  # noqa: S608
    alert_n = await _count(db, "SELECT 1 FROM biz.alerts WHERE org_id = :o AND status = 'open' LIMIT :cap", p)
    kept = (await db.execute(text(f"SELECT mu.event_type, mu.conclusion, mu.observed_at {kept_where} "  # noqa: S608
                                  f"ORDER BY {order} DESC LIMIT :n"), {**p, "n": GROUP_ITEMS})).all()
    inbox_items = [{"title": _text(a.title), "at": iso(a.created_at), "to": "/inbox?tab=alert"} for a in alerts]
    inbox_items += [{"title": f"{EVENT_LABELS.get(k.event_type, k.event_type)}: {_text(k.conclusion, 80)}",
                     "at": iso(k.observed_at), "to": "/inbox"} for k in kept]
    inbox_items.sort(key=lambda x: x["at"] or "", reverse=True)

    # Bàn làm việc: bản nháp chờ duyệt.
    desk_n = await _count(db, "SELECT 1 FROM biz.action_drafts WHERE org_id = :o AND status = 'pending' LIMIT :cap", p)
    desk = [{"title": _text(d.title) or DRAFT_LABELS.get(d.kind, "Bản nháp chờ duyệt"), "at": iso(d.created_at),
             "to": f"/workbench?id={d.id}"}
            for d in (await db.execute(text("""
                SELECT id, kind, title, created_at FROM biz.action_drafts WHERE org_id = :o AND status = 'pending'
                ORDER BY created_at DESC LIMIT :n"""), {"o": org_id, "n": GROUP_ITEMS})).all()]

    # Việc & Nhắc hẹn: việc còn mở, hạn gần nhất lên trước (việc không hạn xuống cuối).
    task_n = await _count(db, """
        SELECT 1 FROM biz.tasks WHERE org_id = :o AND status NOT IN ('done', 'cancelled') LIMIT :cap""", p)
    todo = [{"title": _text(t.title) or "Việc chưa đặt tên", "at": iso(t.due_at or t.created_at), "to": "/tasks"}
            for t in (await db.execute(text("""
                SELECT title, due_at, created_at FROM biz.tasks
                WHERE org_id = :o AND status NOT IN ('done', 'cancelled')
                ORDER BY due_at NULLS LAST, created_at DESC LIMIT :n"""), {"o": org_id, "n": GROUP_ITEMS})).all()]

    return {"groups": [
        {"key": "inbox", "title": "Hộp thư đã lọc", "count": min(kept_n + alert_n, COUNT_CAP), "to": "/inbox",
         "items": inbox_items[:GROUP_ITEMS]},
        {"key": "desk", "title": "Bàn làm việc", "count": desk_n, "to": "/workbench", "items": desk},
        {"key": "tasks", "title": "Việc & Nhắc hẹn", "count": task_n, "to": "/tasks", "items": todo},
    ]}
