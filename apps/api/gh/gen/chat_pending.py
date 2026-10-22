# ruff: noqa: E501
"""v0.1.73 — Xác nhận trong chat: sổ các đề xuất Gen soạn từ KÊNH QUẢN TRỊ (Zalo / WhatsApp) đang chờ Sếp trả lời «OK».

Đề xuất vẫn nằm trong Redis như cũ (`proposals.save`, hạn `PROPOSAL_TTL_S`). Sổ này chỉ là chỉ mục theo (tổ chức, kênh) để biết
«đề xuất nào Sếp vừa được hỏi trong chat» — đề xuất soạn ở Console / Telegram KHÔNG vào sổ nên «OK» trong chat không bao giờ
xác nhận được chúng. Mục giữ thêm 48 giờ sau khi đề xuất hết hạn để báo đúng «đã hết hạn» thay vì «không có gì».
Module chỉ ghi Redis và dựng chữ — không có đường thực thi nào ở đây (thực thi: `gh.owner_channel.chat_confirm`).
"""

import time
from typing import Any

from redis.asyncio import Redis

from gh.gen import proposals
from gh.owner_cmd import text as otext

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


def code_of(pid: str) -> str:
    """'#A3F9C2' — cùng mã ngắn Console hiện (`owner_cmd.core.short_id`)."""
    return "#" + str(pid).replace("-", "")[:6].upper()


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
    return (f"Đề xuất {code_of(p['id'])}: tin gửi ra ngoài này quá dài để Sếp đọc trọn trong chat nên em không hỏi OK ở đây.\n"
            "Việc này em chưa làm được từ chat — Sếp mở Console để Xác nhận (và nhập mã PIN).")


def question(p: dict[str, Any], via: str | None = None, *, with_code: bool = False) -> str:
    """Câu hỏi gửi Sếp cho MỘT đề xuất (đã kiểm). Tóm tắt do hệ thống viết; tin gửi ra ngoài nêu kênh, người nhận (tên + vài ký
    tự cuối ID nhóm) + NGUYÊN VĂN trong khung (không defang: Sếp phải thấy đúng từng ký tự sẽ được gửi). `with_code`: nhắc lại
    đề xuất và đòi «OK #MÃ»."""
    code = code_of(p["id"])
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
