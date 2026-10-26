# ruff: noqa: E501
"""Cửa giao hàng của Tình báo tới Zalo (v0.1.76, gói T3 — spec r6 W7, §4.2.1 (e), CNS-117/121). CHỈ THỰC THI `Level.delivery` đã lưu
(trần, giờ yên lặng, bóng, `bypass_all` đã quyết ở `levels.compute`; ở đây không tính lại).

- `deliver_digest`: bản tin 07:30 / 17:30 (khung của `gen.briefing`) qua `reports.enqueue(kind='digest')` ⇒ `flush` ⇒ `send_auto(owner_dm)`.
  Dựng bằng `digest.build(audience='chat')` ⇒ CHỈ tín hiệu `privacy='chat_ok'`; tín hiệu `console_pin` chỉ là dòng "có N mục cần xem trên
  Console" (không tiêu đề, không trích). ≤ 10 dòng, ≤ 1.500 ký tự; mọi mục có trích đã che «…»; STK/SĐT hiển thị dạng "0123…789".
  Cuối bản tin: "Đã hồi phục" (watchdog T10), dòng độ phủ; "[chạy thử]" khi `intel.mode=shadow`. Một khung giờ chỉ một bản tin (cổng
  `ops.job_watermarks`). Đang khoá trợ lý ⇒ không xếp gì, không tiêu thụ cổng.
- "Tắt bản tin" (`set_digest(off=True)`): bản tin dừng, nhưng mục `digest_top`/`digest_morning` (🔴 chưa đẩy, lừa đảo/an toàn…) gom thành
  tin "Điểm gấp trong ngày" (CNS-121) — không có mục gấp thì im.
- `push_alerts`: tín hiệu `push_now` (🔴 đã được chấm là đẩy ngay, kể cả lừa đảo/an toàn bypass giờ yên lặng) ⇒ `kind='alert'` 3 dòng.
Nơi ghi `sent_at` giữ nguyên ở `owner_outbox` (`reports.flush`) nên dead-man của T10 vẫn đọc đúng.
"""

from __future__ import annotations

import logging
import re
import secrets
import unicodedata
import uuid
from collections.abc import Iterable
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gh.biz.hooks import CronJob
from gh.biz.intel import digest as digest_mod
from gh.biz.intel import levels, mutes
from gh.biz.intel.pipes import coverage, watchdog
from gh.owner_channel import reports
from gh.owner_channel.identity import CHANNELS, owner_ids
from gh.telegram import backup

log = logging.getLogger("gh.intel.delivery")

JOB = "intel.digest_zalo"
MAX_LINES, MAX_CHARS, MAX_ITEMS, QUOTE_MAX = 10, reports.TEXT_MAX, 6, 80
HINT = "Trả lời: «1 ok» · «2 sai» · «3 không cần báo» · «em sót: …»"
QUIET_DAY = "Hôm nay yên, không có gì cần Sếp ra tay."
URGENT_HEAD = "Điểm gấp trong ngày"
MARK = {"red": "🔴", "amber": "🟠", "grey": "⚪"}
# Dãy ≥ 8 chữ số, giữa các chữ số có thể có ≤ 3 ký tự BẤT KỲ không phải chữ/số/xuống dòng (kể cả `_`): `0912–345–678`, `(0912) 345 678`.
_LONGNUM = re.compile(r"(?<!\d)(\d(?:(?:[^\w\n]|_){0,3}\d){7,24})(?!\d)")
_INVISIBLE = re.compile("[\u00ad\u180e\u200b-\u200f\u202a-\u202e\u2060-\u2064\u2066-\u2069\ufeff]")
_FRAME = re.compile(r"(——— nội dung ———\n.*?\n——— hết ———)", re.S)
CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
_WS = re.compile(r"\s+")
_MONEY_SHAPE = re.compile(r"[1-9]\d{0,2}(?:[., ]\d{3})+")           # nhóm hàng nghìn: 25.000.000
_MONEY_AFTER = re.compile(r"\s{0,2}(?:đ|₫|vnd|vnđ|k|tr|triệu|tỷ|ty|nghìn|nghin|ngàn|ngan)(?![\w])", re.I)
_MONEY_BEFORE = re.compile(r"(?:vnd|vnđ|₫|số tiền|giá)\s{0,2}[:=]?\s{0,2}$", re.I)
_ACCOUNT_KW = re.compile(r"(?:stk|tk\b|tài khoản|tai khoan|số tk|so tk|thẻ|the\b|card|ngân hàng|ngan hang)", re.I)
MONEY_MAX_DIGITS = 12


_D_YMD = re.compile(r"(\d{4})-(\d{1,2})-(\d{1,2})")
_D_DMY = re.compile(r"(\d{1,2})[./-](\d{1,2})[./-](\d{4})")
_T_HMS = re.compile(r"(\d{1,2}):(\d{2})(?::(\d{2}))?")


def _is_date(x: str) -> bool:
    try:
        if m := _D_YMD.fullmatch(x):
            date(int(m[1]), int(m[2]), int(m[3]))
            return True
        if m := _D_DMY.fullmatch(x):
            date(int(m[3]), int(m[2]), int(m[1]))
            return True
    except ValueError:
        return False
    return False


def _is_time(x: str) -> bool:
    m = _T_HMS.fullmatch(x)
    return m is not None and int(m[1]) < 24 and int(m[2]) < 60 and int(m[3] or 0) < 60


def _is_datetime(raw: str) -> bool:
    """TOÀN BỘ đoạn là một ngày thật, hoặc ngày + giờ thật («2026-10-23 17:35»). Một phần giống ngày không đủ để miễn che."""
    x = raw.strip()
    if _is_date(x):
        return True
    m = re.fullmatch(r"(.+?)[ T,]{1,2}(\d{1,2}:\d{2}(?::\d{2})?)", x)
    return m is not None and _is_date(m[1]) and _is_time(m[2])


def display_mask(s: str) -> str:
    """Số dài (≥ 8 chữ số: STK, SĐT, thẻ) hiển thị dạng "0123…789" — phòng thủ khi tài khoản Zalo của Sếp bị chiếm (§8.1).
    MẶC ĐỊNH CHE. Miễn che CHỈ khi đủ cả hai: (1) hình dạng số tiền (nhóm hàng nghìn «25.000.000», ≤ 12 chữ số, không bắt đầu bằng 0 / 84 /
    +84) và (2) ngữ cảnh tiền sát bên (đ/VND/k/tr/triệu/tỷ sau số, hoặc «giá / số tiền / VND» trước số) mà trong 30 ký tự phía trước KHÔNG có
    từ khoá tài khoản (STK, tài khoản, thẻ, card…). Hoặc là ngày tháng."""
    s = _INVISIBLE.sub("", unicodedata.normalize("NFKC", s))      # chống lách: chữ số toàn chiều, NBSP/khoảng trắng lạ, ký tự vô hình
    def sub(m: re.Match[str]) -> str:
        raw = m.group(1)
        d = re.sub(r"\D", "", raw)
        if len(d) < 8 or _is_datetime(raw):
            return raw
        before = s[max(0, m.start(1) - 30):m.start(1)]
        phone_like = d.startswith("0") or d.startswith("84") or before.endswith("+")
        money = (_MONEY_SHAPE.fullmatch(raw) is not None and len(d) <= MONEY_MAX_DIGITS and not phone_like
                 and (_MONEY_AFTER.match(s, m.end(1)) is not None or _MONEY_BEFORE.search(before) is not None)
                 and _ACCOUNT_KW.search(before) is None)
        return raw if money else f"{d[:4]}…{d[-3:]}"

    return _LONGNUM.sub(sub, s)


def _defang_frame(s: str) -> str:
    return s.replace("——— nội dung ———", "—— nội dung ——").replace("——— hết ———", "—— hết ——")


def mask_reply(s: str, trusted_frames: Iterable[str] = ()) -> str:
    """Che hiển thị câu trả lời gửi Sếp. CHỈ các khung nguyên văn do CODE dựng từ đề xuất đã lưu (`trusted_frames`, khớp CHÍNH XÁC từng ký tự)
    được giữ nguyên — Sếp phải thấy đủ từng ký tự tin sẽ gửi. Mọi đoạn khác (kể cả chữ tự do của model hay người ngoài chèn, kể cả khi in giả dấu
    khung «——— nội dung ———») đều bị che; dấu khung trong đoạn không tin cậy bị đổi dạng để không giả được khung."""
    frames = sorted({f for f in trusted_frames if f}, key=len, reverse=True)
    if not frames:
        return display_mask(_defang_frame(s))
    parts = re.split("(" + "|".join(re.escape(f) for f in frames) + ")", s)
    return "".join(p if i % 2 else display_mask(_defang_frame(p)) for i, p in enumerate(parts))


def new_code() -> str:
    """Mã bản tin ngắn (4 ký tự, không I/O/0/1) — «sai 2 #K7F2» gắn đúng bản tin được tham chiếu."""
    return "".join(secrets.choice(CODE_ALPHABET) for _ in range(4))


def _one_line(s: str, limit: int) -> str:
    s = _WS.sub(" ", s).strip()
    return s if len(s) <= limit else s[: limit - 1] + "…"


# ─── cài đặt ─────────────────────────────────────────────────────────────────

async def _intel(db: AsyncSession, org_id: uuid.UUID) -> dict[str, Any]:
    v = (await db.execute(text("SELECT settings -> 'intel' FROM core.organizations WHERE id = :o"), {"o": org_id})).scalar_one_or_none()
    return v if isinstance(v, dict) else {}


async def set_digest(db: AsyncSession, org_id: uuid.UUID, *, off: bool) -> None:
    """Bật/tắt bản tin Zalo (`settings.intel.digest_off`). Bên gọi lo xác nhận (kênh phụ) và commit."""
    await db.execute(text("""UPDATE core.organizations SET settings = settings || jsonb_build_object('intel',
                               COALESCE(settings->'intel', '{}'::jsonb) || jsonb_build_object('digest_off', CAST(:v AS boolean)))
                             WHERE id = :o"""), {"o": org_id, "v": off})


async def set_quiet_hours(db: AsyncSession, org_id: uuid.UUID, start: str, end: str) -> None:
    """Giờ yên lặng MỘT nguồn: `settings.intel.quiet_start/quiet_end` ("HH:MM", giờ VN). Bên gọi lo xác nhận + commit."""
    await db.execute(text("""UPDATE core.organizations SET settings = settings || jsonb_build_object('intel',
                               COALESCE(settings->'intel', '{}'::jsonb) || jsonb_build_object('quiet_start', CAST(:a AS text), 'quiet_end', CAST(:b AS text)))
                             WHERE id = :o"""), {"o": org_id, "a": start, "b": end})


# ─── dựng chữ ────────────────────────────────────────────────────────────────

def _item_line(it: digest_mod.DigestItem) -> str:
    head = f"{it.n}. {MARK.get(it.level, '⚪')} {_one_line(it.title, 90)}"
    if it.line_facts:
        head += " " + _one_line(" · ".join(it.line_facts), 60)
    if it.quote:
        head += f" «{_one_line(it.quote, QUOTE_MAX)}»"
    return head


def render(d: digest_mod.Digest, *, label: str, shadow: bool, hidden_console: int, coverage_line: str, recovered: list[str],
           urgent_only: bool = False, code: str = "") -> tuple[str, list[digest_mod.DigestItem]]:
    """Bản chữ Zalo + các mục thật sự nằm trong tin (refs chỉ gồm các mục này, đúng số thứ tự)."""
    head = (URGENT_HEAD if urgent_only else "Bản tin") + f" {label}" + (" [chạy thử]" if shadow else "") + (f" #{code}" if code else "")
    tail = [_one_line(r, 120) for r in recovered[:2]]
    if hidden_console:
        tail.append(f"Có {hidden_console} mục cần xem trên Console.")
    if d.system_question:
        if d.system_question.get("hidden"):
            tail.append("Có câu hỏi cần xem trên Console.")      # ask về NGƯỜI (profile/identity): chỉ đếm, không lộ chữ
        else:
            tail.append("❓ " + _one_line(str(d.system_question.get("summary") or ""), 120))
    tail.append(_one_line(coverage_line, 200))
    if d.items:
        tail.append(HINT)
    budget = max(0, MAX_LINES - 1 - len(tail))
    k = len(d.items) if len(d.items) <= min(MAX_ITEMS, budget) else max(0, min(MAX_ITEMS, budget - 1))

    def build(k: int) -> str:
        lines = [head]
        if not d.items:
            if not urgent_only:
                lines.append(QUIET_DAY)
        else:
            lines += [_item_line(it) for it in d.items[:k]]
            if k < len(d.items):
                lines.append(f"…và {len(d.items) - k} mục nữa (xem trên Console).")
        return display_mask("\n".join(lines + tail))

    out = build(k)
    while len(out) > MAX_CHARS and k > 0:
        k -= 1
        out = build(k)
    return out[:MAX_CHARS], d.items[:k]


async def _hidden_console(db: AsyncSession, org_id: uuid.UUID) -> int:
    return int((await db.execute(text("""
        SELECT count(*) FROM biz.alerts WHERE org_id = :o AND privacy = 'console_pin' AND status IN ('open', 'acknowledged')
          AND delivery = ANY(:d) AND kind NOT IN ('system', 'legacy') AND digest_at IS NULL"""),
        {"o": org_id, "d": [*digest_mod.SHOWN, "push_now"]})).scalar_one())


async def _hidden_defensive(db: AsyncSession, org_id: uuid.UUID) -> bool:
    """Có mục phòng thủ (lừa đảo/an toàn) `console_pin` chỉ được ĐẾM trong bản tin ⇒ vẫn kéo `bypass_quiet`."""
    return bool((await db.execute(text("""
        SELECT EXISTS (SELECT 1 FROM biz.alerts WHERE org_id = :o AND privacy = 'console_pin' AND status IN ('open', 'acknowledged')
          AND delivery = ANY(:d) AND kind NOT IN ('system', 'legacy') AND digest_at IS NULL
          AND (kind = ANY(:k) OR gate = ANY(:g) OR candidate = ANY(:g)))"""),
        {"o": org_id, "d": [*digest_mod.SHOWN, "push_now"], "k": list(mutes.DEFENSIVE_KINDS), "g": list(levels.BYPASS_GATES)})).scalar_one())


async def _since(db: AsyncSession, org_id: uuid.UUID, now: datetime) -> datetime:
    last = (await db.execute(text("SELECT max(created_at) FROM ops.owner_outbox WHERE org_id = :o AND kind = 'digest'"), {"o": org_id})).scalar_one()
    return last if last is not None and last < now else now - timedelta(hours=24)


async def _channel(db: AsyncSession, org_id: uuid.UUID) -> str | None:
    for ch in CHANNELS:
        if (await owner_ids(db, org_id, ch)).enabled:
            return ch
    return None


# ─── bản tin ─────────────────────────────────────────────────────────────────

async def deliver_digest(db: AsyncSession, org_id: uuid.UUID, now: datetime) -> str:
    """Một lượt cho một tổ chức. Trả: sent | nothing | locked | no_channel | no_slot | already_sent. Bên gọi commit."""
    from gh.gen import briefing

    slot = briefing.due_slot(now)
    if slot is None:
        return "no_slot"
    ch = await _channel(db, org_id)
    if ch is None:
        return "no_channel"
    if await backup.is_locked(db, org_id):
        return "locked"
    cfg = await _intel(db, org_id)
    last = (await db.execute(text("SELECT last_at FROM ops.job_watermarks WHERE org_id = :o AND job = :j"), {"o": org_id, "j": JOB})).scalar_one_or_none()
    if last is not None and last >= slot.at:
        return "already_sent"
    off = bool(cfg.get("digest_off"))
    d = await digest_mod.build(db, org_id, now, audience="chat", only_new=True)
    if d.system_question is not None:                               # kênh chat: chỉ câu hỏi `question` hiện chữ (ask về người cần PIN)
        src = (await db.execute(text("SELECT source FROM ops.asks WHERE id = :i AND org_id = :o"),
                                {"i": uuid.UUID(d.system_question["ask_id"]), "o": org_id})).scalar_one_or_none()
        if src != "question":
            d.system_question = {"hidden": True}
    if off:
        d.items[:] = [it for it in d.items if it.delivery in digest_mod.TOP]
        for i, it in enumerate(d.items, 1):
            it.n = i
        d.system_question = None
        if not d.items:
            return "nothing"
    cov = await coverage.line(db, org_id, now)
    rec = [] if off else await watchdog.recovered_lines(db, org_id, since=await _since(db, org_id, now))
    hidden = 0 if off else await _hidden_console(db, org_id)
    code = new_code()
    body, shown = render(d, label=slot.label, shadow=cfg.get("mode") == "shadow", hidden_console=hidden, coverage_line=cov,
                         recovered=rec, urgent_only=off, code=code)
    shown_d = digest_mod.Digest(d.org_id, d.at, shown, d.counts, d.system_question)
    defensive = any((it.gate or it.candidate) in levels.BYPASS_GATES or it.kind in mutes.DEFENSIVE_KINDS for it in shown) or (
        not off and hidden > 0 and await _hidden_defensive(db, org_id))
    ok = await reports.enqueue(db, org_id, ch, kind="digest", severity="important", text_=body, dedupe_key=f"digest:{slot.at.isoformat()}",
                               refs=[{**r, "code": code} for r in shown_d.refs()], now=now, bypass_quiet=defensive or None)
    if not ok:
        return "locked" if await backup.is_locked(db, org_id) else "no_channel"
    await db.execute(text("""INSERT INTO ops.job_watermarks (org_id, job, last_at, updated_at) VALUES (:o, :j, :s, now())
                             ON CONFLICT (org_id, job) DO UPDATE SET last_at = EXCLUDED.last_at, updated_at = now()"""),
                     {"o": org_id, "j": JOB, "s": slot.at})
    await digest_mod.mark_sent(db, org_id, shown_d, now)
    return "sent"


# ─── 🔴 tức thì ──────────────────────────────────────────────────────────────

async def push_alerts(db: AsyncSession, org_id: uuid.UUID, now: datetime) -> int:
    """Xếp tin 🔴 cho tín hiệu `push_now` chưa gửi (mỗi lần đẩy/leo thang một khoá riêng). `chat_ok` ⇒ tin 3 dòng; `console_pin` ⇒ tin 🔴 KHÔNG nội dung (mở Console); khoá trợ lý ⇒ 0. Trả số tin đã xếp."""
    ch = await _channel(db, org_id)
    if ch is None or await backup.is_locked(db, org_id):
        return 0
    # Chỉ lấy dòng CHƯA có tin (khoá `signal:{id}:{pushed_at}`), mới nhất trước: không bị ≥ 10 tín hiệu cũ chiếm chỗ (bỏ đói).
    rows = (await db.execute(text("""
        SELECT a.id, a.title, a.summary, a.suggested_action, a.evidence, a.pushed_at, a.privacy, a.reasons FROM biz.alerts a
        WHERE a.org_id = :o AND a.delivery = 'push_now' AND a.pushed_at IS NOT NULL
          AND a.status IN ('open', 'acknowledged') AND a.kind NOT IN ('system', 'legacy')
          AND NOT EXISTS (SELECT 1 FROM ops.owner_outbox x WHERE x.org_id = a.org_id AND x.channel_type = :c
                            AND x.dedupe_key = 'signal:' || a.id::text || ':' || floor(extract(epoch FROM a.pushed_at))::bigint::text)
        ORDER BY a.pushed_at DESC LIMIT 10"""), {"o": org_id, "c": ch})).all()
    n = 0
    for r in rows:
        code = new_code()
        key = f"signal:{r.id}:{int(r.pushed_at.timestamp())}"
        if r.privacy != "chat_ok":              # 🔴 riêng tư (console_pin): tin 🔴 KHÔNG nội dung, không refs
            body = f"🔴 Có 1 vụ khẩn cần xem trên Console (mã PIN). #{code}"
            refs = [{"n": 1, "signal_id": None, "ask_id": None, "ask_code": None, "cluster_ids": None, "code": code, "console_only": True}]
        else:
            why = _one_line(r.summary or "Vụ này cần Sếp xem ngay.", 160)
            quote = digest_mod._quote(r.evidence)
            remind = "Nhắc lại — hôm nay là ngày hẹn chuyển tiền: " if "txn_day_reminder" in (r.reasons or []) else ""
            lines = [f"🔴 {remind}{_one_line(r.title, 110)}" + (f" «{_one_line(quote, QUOTE_MAX)}»" if quote else "") + f" #{code}", f"Vì sao: {why}",
                     f"Đề xuất: {_one_line(r.suggested_action or 'Sếp xem chi tiết trên Console.', 160)}", "Trả lời: «1 ok» · «1 sai» · «1 không cần báo»"]
            body = display_mask("\n".join(lines))
            refs = [{"n": 1, "signal_id": str(r.id), "ask_id": None, "ask_code": None, "cluster_ids": None, "code": code}]
        if await reports.enqueue(db, org_id, ch, kind="alert", severity="urgent", text_=body, dedupe_key=key, refs=refs, now=now, bypass_quiet=True):
            n += 1
    return n


# ─── việc nền ────────────────────────────────────────────────────────────────

async def intel_delivery(ctx: dict[str, Any]) -> dict[str, int]:
    """Mỗi phút: 🔴 tức thì + bản tin tới khung. `flush` (worker) gửi đi. Một tổ chức lỗi không dừng tổ chức khác."""
    from gh.db import sessionmaker
    from gh.refinery import clock

    now = clock.now()
    sm = sessionmaker()
    out = {"alerts": 0, "digests": 0, "errors": 0}
    async with sm() as db:
        orgs = (await db.execute(text("SELECT id FROM core.organizations"))).scalars().all()
    for org in orgs:
        try:
            async with sm() as db:
                out["alerts"] += await push_alerts(db, org, now)
                if await deliver_digest(db, org, now) == "sent":
                    out["digests"] += 1
                await db.commit()
        except Exception:                                       # noqa: BLE001
            log.exception("intel_delivery lỗi ở một tổ chức")
            out["errors"] += 1
    return out


JOBS: list[CronJob] = [(intel_delivery, {"minute": set(range(60))})]
__all__ = ["JOBS", "deliver_digest", "display_mask", "push_alerts", "render", "set_digest"]
