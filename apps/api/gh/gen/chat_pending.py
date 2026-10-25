# ruff: noqa: E501
"""v0.1.73 — Xác nhận trong chat: sổ các đề xuất Gen soạn từ KÊNH QUẢN TRỊ (Zalo / WhatsApp) đang chờ Sếp trả lời «OK».

Đề xuất vẫn nằm trong Redis như cũ (`proposals.save`, hạn `PROPOSAL_TTL_S`). Sổ này chỉ là chỉ mục theo (tổ chức, kênh) để biết
«đề xuất nào Sếp vừa được hỏi trong chat» — đề xuất soạn ở Console / Telegram KHÔNG vào sổ nên «OK» trong chat không bao giờ
xác nhận được chúng. Mục giữ thêm 48 giờ sau khi đề xuất hết hạn để báo đúng «đã hết hạn» thay vì «không có gì».
Module chỉ ghi Redis và dựng chữ — không có đường thực thi nào ở đây (thực thi: `gh.owner_channel.chat_confirm`).
"""

import logging
import time
from typing import Any

from redis.asyncio import Redis

from gh.gen import proposals
from gh.owner_cmd import text as otext

log = logging.getLogger("gh.gen.chat_pending")

#: Kênh quản trị hỗ trợ Xác nhận trong chat. Telegram giữ nguyên (Console + PIN) — ghi Nợ ở docs/ROADMAP.md.
CHAT_VIAS: tuple[str, ...] = ("zalo", "whatsapp")
KEEP_S = 48 * 3600
MAX_ENTRIES = 30
#: Tin gửi người ngoài dài hơn mức này không đọc trọn trong chat được ⇒ để Console (phải thấy NGUYÊN VĂN mới được OK).
OUTBOUND_TEXT_MAX = 1500
#: Giới hạn một tin trả lời Sếp (dm.REPLY_MAX dùng lại): nguyên văn tin gửi ngoài PHẢI nằm trọn trong giới hạn này.
REPLY_MAX = 3800
#: Chừa chỗ cho phần đuôi (lời Gen, nhắc nhiều đề xuất) khi xếp các câu hỏi vào một tin.
REPLY_TAIL_RESERVE = 900
#: «OK» không mã chỉ nhận khi đề xuất được hỏi trong chừng này giây VÀ là câu hỏi cuối cùng gửi Sếp.
FRESH_S = 600
#: «Nội dung nháp» nội bộ hiển thị tối đa chừng này ký tự.
DRAFT_SHOW_MAX = 600
VIA_LABEL = {"zalo": "Zalo", "whatsapp": "WhatsApp"}
SUFFIX_NEEDS_PIN = " Chỉ ghi khi Sếp bấm Xác nhận và nhập mã PIN (qua Gen-hub)."


def key(org_id: Any, via: str) -> str:
    return f"gh:owner:chatprop:{org_id}:{via}"


def code_of(p: Any) -> str:
    """Mã hiển thị của một đề xuất. v0.1.76 (T2): đề xuất đã có ask ⇒ MÃ ASK 5 ký tự (`#K7QX4`, một mã cho mọi nguồn);
    chưa có (hoặc truyền id) ⇒ mã hex cũ '#A3F9C2' (giữ đọc trong bản chuyển tiếp)."""
    if isinstance(p, dict):
        if p.get("ask_code"):
            return "#" + str(p["ask_code"])
        p = p.get("id")
    return "#" + str(p).replace("-", "")[:6].upper()


def legacy_code(pid: Any) -> str:
    return "#" + str(pid).replace("-", "")[:6].upper()


def matches(p: dict[str, Any], code: str) -> bool:
    """«OK #MÃ»: mã ask đúng TRỌN 5 ký tự, hoặc tiền tố mã hex cũ (4–6 ký tự)."""
    code = code.upper().lstrip("#")
    if p.get("ask_code") and code == str(p["ask_code"]):
        return True
    return legacy_code(p["id"])[1:].startswith(code) and 4 <= len(code) <= 6 and all(c in "0123456789ABCDEF" for c in code)


def decision_kind_of(p: dict[str, Any]) -> str:
    """Loại quyết định của ask (spec r4 §4.4/W8): `self` (việc riêng của Sếp: nhắc chính Sếp, ghi nhớ) · `internal` · `outbound` ·
    `console`. Chỉ để phân loại hàng Cần quyết — việc `self` vẫn chờ «OK» (luật "Gen chỉ ĐỀ XUẤT" trong CLAUDE.md)."""
    k = kind_of(p)
    if k != "internal":
        return k
    f = p.get("fields") or {}
    if p.get("type") == proposals.MEMORY_TYPE or (p.get("type") == "reminder"
                                                   and str(f.get("assignee_user_id") or p.get("user_id")) == str(p.get("user_id"))):
        return "self"
    return "internal"


async def ensure_ask(sm: Any, redis: Redis, org_id: Any, p: dict[str, Any], via: str) -> dict[str, Any]:
    """Mỗi đề xuất hỏi qua kênh chat / Telegram sinh MỘT ask `ops.asks` (nguồn `proposal`, mã ngẫu nhiên 5 ký tự). Ghi mã vào đề
    xuất (Redis) để mọi câu hỏi / trả lời dùng cùng một mã. Lỗi ghi ask ⇒ giữ mã hex cũ (không chặn lời đáp)."""
    if p.get("ask_code"):
        return p
    import uuid as _uuid

    from sqlalchemy import text

    from gh.biz.intel import asks

    try:
        async with sm() as db:
            await db.execute(text("SELECT set_config('app.org_id', :o, true)"), {"o": str(org_id)})
            a = await asks.create(db, _uuid.UUID(str(org_id)), source="proposal", ref_id=_uuid.UUID(str(p["id"])),
                                  decision_kind=decision_kind_of(p), summary=str(p.get("summary") or p.get("type"))[:500],
                                  created_via=via if via in ("zalo", "whatsapp", "telegram", "console") else "job")
            await db.commit()
    except Exception:  # noqa: BLE001 — hàng Cần quyết là phụ: lỗi thì vẫn hỏi bằng mã cũ
        log.warning("chat_pending: không tạo được ask cho đề xuất", exc_info=True)
        return p
    p["ask_code"] = a.code
    ttl = await redis.ttl(proposals.key(p["id"]))
    await proposals.save(redis, p, int(ttl) if ttl and int(ttl) > 0 else proposals.PROPOSAL_TTL_S)
    return p


def last_key(org_id: Any, via: str) -> str:
    return f"gh:owner:chatlast:{org_id}:{via}"


async def mark_last(redis: Redis, org_id: Any, via: str, pid: str | None) -> None:
    """Ghi «câu hỏi cuối cùng gửi Sếp» (một đề xuất + giờ hỏi); None ⇒ xoá (không còn câu hỏi đơn nào đang chờ «OK» trần)."""
    if pid is None:
        await redis.delete(last_key(org_id, via))
    else:
        await redis.set(last_key(org_id, via), f"{pid}|{time.time()}", ex=FRESH_S + 60)


async def last_ask(redis: Redis, org_id: Any, via: str) -> tuple[str, float] | None:
    raw = await redis.get(last_key(org_id, via))
    if not raw:
        return None
    try:
        pid, ts = (raw.decode() if isinstance(raw, bytes) else str(raw)).rsplit("|", 1)
        return pid, float(ts)
    except ValueError:
        return None


async def forget_since(redis: Redis, org_id: Any, via: str, since: float) -> None:
    """Gỡ mọi đề xuất vừa vào sổ chờ từ `since` (tin hỏi không tới được Sếp / bị cắt) và quên «câu hỏi cuối»."""
    await redis.zremrangebyscore(key(org_id, via), since, "+inf")
    await redis.delete(last_key(org_id, via))


async def register(redis: Redis, org_id: Any, via: str, pid: str) -> None:
    now = time.time()
    await mark_last(redis, org_id, via, pid)
    await redis.zadd(key(org_id, via), {str(pid): now})
    await redis.zremrangebyscore(key(org_id, via), "-inf", now - KEEP_S)
    await redis.zremrangebyrank(key(org_id, via), 0, -(MAX_ENTRIES + 1))
    await redis.expire(key(org_id, via), KEEP_S + 3600)


async def forget(redis: Redis, org_id: Any, via: str, pid: str) -> None:
    await redis.zrem(key(org_id, via), str(pid))


async def entries(redis: Redis, org_id: Any, via: str) -> list[str]:
    """Mã đề xuất đã hỏi trong chat, mới nhất trước."""
    got = await redis.zrevrange(key(org_id, via), 0, MAX_ENTRIES - 1)
    return [x.decode() if isinstance(x, bytes) else str(x) for x in got]


def kind_of(p: dict[str, Any]) -> str:
    """'internal' (OK là làm) | 'outbound' (gửi tin cho người ngoài: OK riêng + mã + nguyên văn) | 'console' (chỉ Console)."""
    t = p.get("type")
    if t in proposals.SOCIAL_TYPES:
        return "console"      # đăng lên mạng xã hội: giữ Xác nhận + PIN ở Console
    if t == "draft_message":
        subj = (p.get("fields") or {}).get("subject") or {}
        if subj.get("type") == "group":
            return "console" if len(str((p.get("fields") or {}).get("text") or "")) > OUTBOUND_TEXT_MAX else "outbound"
    return "internal"


def quote(text_: str) -> str:
    """Khung nguyên văn: mỗi dòng bắt đầu «│ » (dấu trích, không gửi đi) nên nguyên văn không thể giả một dòng «Đề xuất #…»
    hay đóng khung sớm."""
    body = "\n".join("│ " + ln for ln in str(text_).splitlines() or [""])
    return f"——— nội dung ———\n{body}\n——— hết ———"


def question_console(p: dict[str, Any], via: str | None = None) -> str:
    """Đề xuất gửi tin ra ngoài nhưng không đọc trọn trong một tin chat ⇒ không ghi sổ chờ, chuyển Console."""
    return (f"Đề xuất {code_of(p)}: tin gửi ra ngoài này quá dài để Sếp đọc trọn trong chat nên em không hỏi OK ở đây.\n"
            "Việc này em chưa làm được từ chat — Sếp mở Console để Xác nhận (và nhập mã PIN).")


def question(p: dict[str, Any], via: str | None = None, *, with_code: bool = False) -> str:
    """Câu hỏi gửi Sếp cho MỘT đề xuất (đã kiểm). Tóm tắt do hệ thống viết; tin gửi ra ngoài nêu kênh, người nhận (tên + vài ký
    tự cuối ID nhóm) + NGUYÊN VĂN trong khung (không defang: Sếp phải thấy đúng từng ký tự sẽ được gửi). `with_code`: nhắc lại
    đề xuất và đòi «OK #MÃ»."""
    code = code_of(p)
    kind = kind_of(p)
    if kind == "outbound":
        f = p["fields"]
        who = (p.get("labels") or {}).get("subject") or "nhóm đã chọn"
        tail = str((f.get("subject") or {}).get("id") or "").replace("-", "")[-4:]
        where = ", ".join(x for x in (VIA_LABEL.get(via or "", via or ""), f"ID …{tail}" if tail else "") if x)
        return (f"Đề xuất {code} — GỬI TIN cho nhóm «{otext.defang(otext.line(str(who)))}»" + (f" ({where})" if where else "")
                + ", nguyên văn (dấu │ đầu dòng chỉ để trích, không gửi đi):\n"
                f"{quote(f['text'])}\n"
                f"Trả lời «OK {code}» để em gửi đúng nội dung này, hoặc HUỶ {code}.")
    if kind == "console":
        return (f"Đề xuất {code}: {otext.defang(str(p['summary']).replace(SUFFIX_NEEDS_PIN, ''))}\n"
                "Việc này em chưa làm được từ chat — Sếp mở Console để Xác nhận (và nhập mã PIN).")
    body = f"Đề xuất {code}: {otext.defang(str(p['summary']).replace(SUFFIX_NEEDS_PIN, ''))}"
    if p.get("type") == "draft_message":      # nháp chỉ lưu ở Bàn làm việc, không gửi — vẫn cho Sếp thấy nội dung (có giới hạn)
        t = str(p["fields"]["text"])
        shown = t if len(t) <= DRAFT_SHOW_MAX else t[:DRAFT_SHOW_MAX] + "… (còn nữa, xem đủ ở Bàn làm việc)"
        body += f"\nNội dung nháp:\n{quote(shown)}"
    if with_code:
        return f"{body}\nTrả lời «OK {code}» để em làm, hoặc HUỶ {code}."
    return f"{body}\nTrả lời OK để em làm (mã {code}), hoặc HUỶ."
