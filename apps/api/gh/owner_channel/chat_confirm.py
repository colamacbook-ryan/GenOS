# ruff: noqa: E501
"""v0.1.73 — Xác nhận TRONG CHAT (Sếp duyệt 19/10/2026): trong kênh quản trị 1-1 đã ghép (Zalo / WhatsApp), Sếp trả lời «OK»
cho một đề xuất Gen vừa hỏi = Xác nhận thay cho Console + mã PIN.

Chỉ đi qua `dm.build_reply`, tức là chỉ khi tin đã qua `inbound.handle_owner_dm` (kênh đã ghép + đang bật, đúng phiên bot, ID
người gửi KHỚP Owner kiểm lại từ DB, không phải tin chuyển tiếp, không phải tin nhóm) và `dm.handle_dm` (Owner còn hiệu lực).
Tin trong nhóm không bao giờ tới đây (bridge không phát `owner.dm` cho nhóm; `handle_owner_dm` còn chặn thêm lần nữa).

Thực thi ĐÚNG đường Xác nhận của Console, không có đường ghi riêng: tạo một phiên Console NGẮN HẠN cho Owner đã ghép (cơ sở
«chat_ok»: phiên sống ≤ 5 phút, cửa sổ PIN 2 phút, không bao giờ ra khỏi tiến trình, thu hồi ngay sau khi xong dù lỗi), rồi gọi
nội bộ (ASGI) `POST /gen/proposals/{id}/confirm` — endpoint tự kiểm quyền, loại đề xuất, hạn, xác nhận một lần — và với tin gửi
người ngoài gọi tiếp `POST /drafts/{id}/approve` (đường bản nháp CŨ; tin ra bridge do `biz/core/drafts.py` phát, module này
KHÔNG phát gì lên bus — test AST test_outbound_only_two_paths_v0172). Mỗi lần xác nhận / huỷ ghi Action Log
(`via` = zalo | whatsapp, `confirmation` = chat), không kèm nội dung tin (chỉ băm).

Phạm vi: việc nội bộ (nhắc việc, ghi nhớ, giao việc, ghi Kho Phiên / Việc theo QD-18) ⇒ «OK» là làm; nếu chỉ có MỘT đề xuất đang
chờ thì không cần mã, nhiều đề xuất thì đòi mã. Tin gửi người ngoài ⇒ luôn «OK #MÃ» riêng từng tin (không «ok tất cả»).
Đăng mạng xã hội và tin quá dài không đọc trọn trong chat ⇒ vẫn Console + PIN.
"""

import logging
import re
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from gh.auth import service
from gh.biz.intel import strong_confirm
from gh.chassis import actionlog
from gh.crypto import new_token, token_digest
from gh.gen import chat_pending, proposals
from gh.gen.engine import digest
from gh.owner_cmd import core as owner_core
from gh.telegram import backup
from gh.textnorm import strip_accents

log = logging.getLogger("gh.owner_channel.chat_confirm")

SESSION_TTL = timedelta(minutes=5)
PIN_WINDOW = timedelta(minutes=2)
CLAIM_TTL_S = 300

_OK = ("dong y", "lam di", "duoc roi", "duoc", "xac nhan", "okay", "okie", "oke", "ok")
_NO = ("huy bo", "huy", "thoi")
_UNDO = ("hoan tac",)            # v0.1.76: «hoàn tác #MÃ» (khác «huỷ #MÃ» = huỷ mục CHƯA làm)
UNDO_WINDOW_S = 600
_FILL = frozenset({"em", "nhe", "nha", "a", "ne", "luon", "di", "gen"})
_ALL = frozenset({"tat", "ca", "het", "all"})
_CODE = re.compile(r"^#?([0-9a-f]{4,6})$")
_PIN2 = re.compile(r"^[0-9]{4}$")
#: v0.1.76 (T2): mã ask 5 ký tự (bảng `A-HJ-NP-Z2-9`). Không có '#' thì phải có chữ số (để «ok nhanh» không thành mã).
_ASK = re.compile(r"^(#?)([a-hj-np-z2-9]{5})$")
_PUNCT = re.compile(r"[.!,;:?~…]+")

NONE_PENDING = "Em không có đề xuất nào đang chờ Sếp xác nhận ạ. Sếp nhắn /nhap <ý> để em soạn."
EXPIRED = "Đề xuất {code} đã hết hạn (quá 24 giờ) nên em không làm. Sếp nhắn /nhap <ý> để em soạn lại."
UNKNOWN_CODE = "Em không thấy đề xuất {code} đang chờ. Sếp kiểm lại mã giúp em."
DECIDED = "Đề xuất {code} đã được xử lý rồi (xác nhận hoặc huỷ trước đó), em không làm lại."
NEED_CODE_OUTBOUND = "Tin gửi cho người ngoài cần Sếp trả lời kèm mã. Em nhắc lại đề xuất:\n\n{question}"
NEED_CODE_STALE = ("Em hỏi đề xuất này đã lâu (hoặc sau đó còn tin khác) nên Sếp trả lời OK kèm mã cho chắc. "
                   "Em nhắc lại:\n\n{question}")
ALL_REFUSED = "Em không làm hàng loạt. Mỗi đề xuất Sếp trả lời OK kèm mã riêng (tin gửi người ngoài thì từng tin một)."
NOT_HERE = "Kênh này chưa xác nhận bằng chat được — Sếp mở Console để Xác nhận."
FAILED = "Em chưa làm được đề xuất {code}: {why}. Chưa có gì thay đổi, Sếp thử lại hoặc mở Console."
CANCELLED = "Đã huỷ đề xuất {code}. Em không làm gì cả."
UNDO_NEED_CODE = "Sếp nhắn «hoàn tác #MÃ» kèm mã việc vừa làm (trong 10 phút)."
UNDO_REFUSED = ("Em không hoàn tác được {code}: chỉ việc riêng của Sếp vừa làm trong 10 phút mới hoàn tác ở đây "
                "(việc khác Sếp sửa trong Console).")
UNDO_DONE = "Đã hoàn tác {code}: {what}."
# v0.1.76 (T2) — xác nhận mạnh cho tin gửi khách (spec r6 §8.1). Không bao giờ nhắc lại mã kép trong chat.
CODE_SENT = ("Tin gửi khách cần xác nhận mạnh: em vừa gửi mã 4 số qua kênh phụ (Telegram riêng của Sếp). "
             "Sếp nhắn «OK {code} <mã 4 số>» trong 10 phút để em gửi. Không đưa mã này cho ai.")
CODE_RESENT = "Mã cũ đã hết hạn hoặc không phải của đề xuất này. " + CODE_SENT
WRONG_CODE = "Mã xác nhận chưa đúng, em chưa gửi {code}. Sếp còn {left} lần thử; sai hết em sẽ tạm khoá gửi tin cho khách 1 giờ."
STRONG_CONSOLE = "Em chưa gửi {code}: {why}."


@dataclass(frozen=True)
class Decision:
    verb: str          # 'ok' | 'no' | 'all'
    code: str | None   # 4–6 ký tự hex HOA, không '#'
    pin2: str | None = None   # v0.1.76: mã kép 4 số (chỉ sau mã đề xuất, chỉ với «OK»)


def parse_decision(raw: str) -> Decision | None:
    """«ok» / «OK #A3F9C2» / «đồng ý» / «làm đi» (bỏ dấu, không phân biệt hoa thường) ⇒ ('ok', mã?); «huỷ» / «thôi» ⇒ ('no', mã?).
    Câu dài hơn / lẫn chữ khác ⇒ None (đi đường hỏi đáp bình thường). «ok tất cả» ⇒ ('all', None) để từ chối gộp."""
    t = " ".join(_PUNCT.sub(" ", strip_accents(str(raw)).lower()).split())
    if not t or len(t) > 40:
        return None
    for verb, words in (("ok", _OK), ("no", _NO), ("undo", _UNDO)):
        for w in words:
            if t != w and not t.startswith(w + " "):
                continue
            code: str | None = None
            pin2: str | None = None
            rest = t[len(w):].split()
            if verb == "ok" and set(rest) & _ALL:
                return Decision("all", None) if set(rest) <= _ALL | _FILL else None
            for tok in rest:
                if tok in _FILL:
                    continue
                got = _code_token(tok)
                if got and code is None:
                    code = got
                elif verb == "ok" and code is not None and pin2 is None and _PIN2.match(tok):
                    pin2 = tok
                elif tok not in _FILL:
                    return None
            return Decision(verb, code, pin2)
    return None


def _code_token(tok: str) -> str | None:
    m = _CODE.match(tok)
    if m:
        return m.group(1).upper()
    a = _ASK.match(tok)
    if a and (a.group(1) or any(c.isdigit() for c in a.group(2))):
        return a.group(2).upper()
    return None


@dataclass
class _Session:
    id: uuid.UUID
    cookies: dict[str, str]
    csrf: str


async def _set_org(db: AsyncSession, org_id: uuid.UUID) -> None:
    await db.execute(text("SELECT set_config('app.org_id', :o, true)"), {"o": str(org_id)})


async def _mint(sm: async_sessionmaker[AsyncSession], org_id: uuid.UUID, user_id: uuid.UUID, via: str, *,
                pin: bool, agent: str = "chat_ok") -> _Session:
    """Phiên Console ngắn hạn cho Owner đã ghép (cơ sở «chat_ok»). Chỉ sống trong tiến trình này."""
    token, csrf = new_token(), new_token(16)
    now = datetime.now(UTC)
    async with sm() as db:
        await _set_org(db, org_id)
        sid = (await db.execute(text("""
            INSERT INTO core.sessions (user_id, token_hash, csrf_hash, ip, user_agent, expires_at, last_seen_at,
                                       pin_verified_until)
            VALUES (:u, :th, :ch, NULL, :ua, :exp, now(), :pin) RETURNING id"""),
            {"u": user_id, "th": token_digest(token), "ch": token_digest(csrf), "ua": f"{agent}:{via}",
             "exp": now + SESSION_TTL, "pin": now + PIN_WINDOW if pin else None})).scalar_one()
        await db.commit()
    return _Session(sid, {service.SESSION_COOKIE: token, service.CSRF_COOKIE: csrf}, csrf)


async def _revoke(sm: async_sessionmaker[AsyncSession], org_id: uuid.UUID, sid: uuid.UUID) -> None:
    try:
        async with sm() as db:
            await _set_org(db, org_id)
            await service.revoke_session(db, sid)
            await db.execute(text("UPDATE core.sessions SET pin_verified_until = NULL WHERE id = :s"), {"s": sid})
            await db.commit()
    except Exception:  # noqa: BLE001 — phiên tự hết hạn sau 5 phút dù thu hồi lỗi
        log.warning("chat_ok: không thu hồi được phiên tạm", exc_info=True)


async def _call(app: Any, ses: _Session, method: str, path: str, body: dict[str, Any]) -> tuple[int, Any]:
    return await proposals.call_as_user(app, dict(ses.cookies), ses.csrf,
                                        proposals.Call(method, path, body, "chat_ok"))


async def _log(sm: async_sessionmaker[AsyncSession], org_id: uuid.UUID, user_id: uuid.UUID, via: str, action: str,
               p: dict[str, Any], result: str, **detail: Any) -> None:
    try:
        async with sm() as db:
            await _set_org(db, org_id)
            await actionlog.record(
                db, org_id=org_id, actor_type="user", actor_id=f"user:{user_id}", action=action, target_type="gen_proposal",
                target_id=p["id"], result=result,
                detail={"via": via, "confirmation": "chat", "proposal_id": p["id"], "type": p["type"],
                        "kind": chat_pending.kind_of(p), "turn_id": p.get("turn_id"),
                        "fields_digest": digest(p.get("fields")), **detail})
            await db.commit()
    except Exception:  # noqa: BLE001
        log.warning("chat_ok: không ghi được Action Log %s", action, exc_info=True)


def _why(status: int, body: Any) -> str:
    b = body if isinstance(body, dict) else {}
    if status == 403:
        return "tài khoản Sếp không đủ quyền cho việc này"
    if status == 404:
        return "đề xuất không còn (có thể đã hết hạn)"
    if status == 422:
        errs = b.get("errors")
        return "nội dung đề xuất chưa hợp lệ" + (f" ({'; '.join(str(v) for v in errs.values())[:120]})" if isinstance(errs, dict) else "")
    return str(b.get("title") or "lỗi khi thực hiện")[:140]


def _done(p: dict[str, Any], result: dict[str, Any]) -> str:
    code = chat_pending.code_of(p)
    t = result.get("type")
    if t == "task":
        c = f" ({result['code']})" if result.get("code") else ""
        return f"Đã làm xong {code}: việc{c} đã được ghi."
    if t == "memory_note":
        return f"Đã làm xong {code}: em đã ghi nhớ."
    if t == "kho_record":
        c = f" {result['code']}" if result.get("code") else ""
        return f"Đã làm xong {code}: đã ghi vào Kho{c}."
    if t == "inbox_item":
        return f"Đã làm xong {code}: đã giao việc."
    return f"Đã làm xong {code}."


async def _strong(sm: async_sessionmaker[AsyncSession], redis: Redis, org_id: uuid.UUID, p: dict[str, Any],
                  pin2: str | None) -> str | None:
    """Xác nhận mạnh cho tin gửi khách (spec r6 §8.1). None = qua; ngược lại là câu trả lời Sếp (chưa gửi gì).
    Mọi luật (mở khoá hôm nay, mã kép, sai 3 lần khoá 1 giờ theo tổ chức, giờ yên lặng) nằm ở `strong_confirm`."""
    code = chat_pending.code_of(p)
    async with sm() as db:
        await _set_org(db, org_id)
        r: Any = (await strong_confirm.check_code(db, redis, org_id, p["id"], pin2) if pin2
                  else await strong_confirm.require(db, org_id, strong_confirm.OUTBOUND))
        reply: str | None = None
        if isinstance(r, strong_confirm.NeedCode):
            try:
                await strong_confirm.issue_code(db, redis, org_id, p["id"])
                reply = (CODE_RESENT if pin2 else CODE_SENT).format(code=code)
            except backup.BackupUnavailable:
                reply = STRONG_CONSOLE.format(code=code, why=strong_confirm.UNLOCK_REASON)
        elif isinstance(r, strong_confirm.WrongCode):
            reply = WRONG_CODE.format(code=code, left=r.left)
        elif isinstance(r, strong_confirm.NeedConsole):
            reply = STRONG_CONSOLE.format(code=code, why=r.reason)
        await db.commit()                 # giữ dòng Nhật ký đếm lần sai / khoá kể cả khi bị chặn
    return reply


async def _execute(sm: async_sessionmaker[AsyncSession], redis: Redis, app: Any, org_id: uuid.UUID, via: str,
                   owner_id: uuid.UUID, p: dict[str, Any], ref: str, pin2: str | None = None) -> str:
    code = chat_pending.code_of(p)
    pid = p["id"]
    kind = chat_pending.kind_of(p)
    if kind == "console":
        return NOT_HERE
    if kind == "outbound":
        blocked = await _strong(sm, redis, org_id, p, pin2)
        if blocked is not None:
            return blocked
    if not await redis.set(f"gh:owner:chatok:{pid}", "1", nx=True, ex=CLAIM_TTL_S):
        return DECIDED.format(code=code)           # xác nhận MỘT lần: lượt trước đang/đã làm
    ses = await _mint(sm, org_id, owner_id, via, pin=True)
    if kind == "outbound":            # dấu một lần cho route duyệt nháp (phòng thủ chiều sâu: phiên chat_ok khác không duyệt được)
        await redis.set(strong_confirm.pass_key(ses.id), "1", ex=int(SESSION_TTL.total_seconds()))
    try:
        status, res = await _call(app, ses, "POST", f"/gen/proposals/{pid}/confirm", {"fields": {}})
        if status == 409:
            await chat_pending.forget(redis, org_id, via, pid)
            await _log(sm, org_id, owner_id, via, "gen.proposal_chat_confirmed", p, "blocked", status=status, ref=ref)
            return DECIDED.format(code=code)
        if status >= 400:
            await redis.delete(f"gh:owner:chatok:{pid}")        # lỗi chắc chắn chưa làm ⇒ Sếp được thử lại
            await chat_pending.mark_last(redis, org_id, via, pid)    # lời báo lỗi là tin cuối; thử lại bằng «OK» vẫn đúng đề xuất này
            await _log(sm, org_id, owner_id, via, "gen.proposal_chat_confirmed", p,
                       "blocked" if status in (403, 404, 423) else "failed", status=status, ref=ref,
                       code=(res or {}).get("code") if isinstance(res, dict) else None)
            return FAILED.format(code=code, why=_why(status, res))
        result = (res or {}).get("result") or {}
        await chat_pending.forget(redis, org_id, via, pid)
        sent = ""
        if kind == "outbound" and result.get("type") == "draft":
            sent = await _approve_draft(app, ses, result)
        await _log(sm, org_id, owner_id, via, "gen.proposal_chat_confirmed", p, "ok", status=status, ref=ref,
                   result_type=result.get("type"), outbound=sent or None)
        if kind == "outbound":
            who = (p.get("labels") or {}).get("subject") or "nhóm"
            if sent == "approved" or sent == "sent":
                return f"Đã gửi {code} cho nhóm «{who}» (đang chuyển đi)."
            if sent:
                return f"Em lưu tin {code} ở Bàn làm việc nhưng chưa gửi được ({sent}). Sếp xem trong Console."
            return f"Em lưu tin {code} làm bản nháp ở Bàn làm việc, chưa gửi đi (chưa có nơi nhận)."
        return _done(p, result)
    except Exception:  # noqa: BLE001 — lỗi lạ: không rõ đã làm hay chưa ⇒ giữ khoá một lần, báo Sếp kiểm
        log.exception("chat_ok: thực thi đề xuất lỗi")
        await _log(sm, org_id, owner_id, via, "gen.proposal_chat_confirmed", p, "failed", ref=ref, error="exception")
        return FAILED.format(code=code, why="lỗi bất ngờ, em chưa chắc đã làm hay chưa — Sếp xem lại trong Console")
    finally:
        await redis.delete(strong_confirm.pass_key(ses.id))
        await _revoke(sm, org_id, ses.id)


async def _approve_draft(app: Any, ses: _Session, result: dict[str, Any]) -> str:
    """Bản nháp vừa tạo từ đề xuất «gửi tin» đã được Sếp OK trong chat ⇒ duyệt qua đường bản nháp CŨ (thay bước PIN bằng
    phiên «chat_ok»). Trả trạng thái bản nháp ('approved' | 'sent') hoặc mã lỗi; chuỗi rỗng nếu nháp không có nơi gửi."""
    if not result.get("sendable") or not result.get("id"):
        return ""
    status, body = await _call(app, ses, "POST", f"/drafts/{result['id']}/approve", {})
    if status >= 400:
        return str((body or {}).get("code") or status) if isinstance(body, dict) else str(status)
    return str((body or {}).get("status") or "approved")


async def _cancel(sm: async_sessionmaker[AsyncSession], redis: Redis, app: Any, org_id: uuid.UUID, via: str,
                  owner_id: uuid.UUID, p: dict[str, Any], ref: str) -> str:
    code = chat_pending.code_of(p)
    ses = await _mint(sm, org_id, owner_id, via, pin=False)
    try:
        status, res = await _call(app, ses, "POST", f"/gen/proposals/{p['id']}/cancel", {})
    finally:
        await _revoke(sm, org_id, ses.id)
    if status >= 400 and status != 409:
        await _log(sm, org_id, owner_id, via, "gen.proposal_chat_cancelled", p, "failed", status=status, ref=ref)
        return FAILED.format(code=code, why=_why(status, res))
    await chat_pending.forget(redis, org_id, via, p["id"])
    await _log(sm, org_id, owner_id, via, "gen.proposal_chat_cancelled", p, "ok", status=status, ref=ref)
    return CANCELLED.format(code=code)


async def handle(sm: async_sessionmaker[AsyncSession], redis: Redis, app: Any, org_id: uuid.UUID, oi: Any,
                 d: Decision, ref: str = "") -> str:
    """Trả lời cho «OK» / «HUỶ» của Sếp. Không ném (lỗi ⇒ câu thân thiện)."""
    via = str(oi.channel)
    if via not in chat_pending.CHAT_VIAS or app is None:
        return NOT_HERE
    async with sm() as db:
        await _set_org(db, org_id)
        owner = await owner_core.owner_user(db, org_id, oi.owner_user_id)
        await db.rollback()
    if owner is None:
        return owner_core.no_owner_text(via)
    if d.verb == "all":
        return ALL_REFUSED
    if d.verb == "undo":
        return await _undo(sm, app, org_id, via, owner.id, d.code, ref)
    pend: list[dict[str, Any]] = []
    gone: list[str] = []
    ids = await chat_pending.entries(redis, org_id, via)
    for pid, (p, _ttl) in zip(ids, await proposals.load_many(redis, ids), strict=True):
        if p is None:
            gone.append(pid)
        elif p.get("status") == "pending" and p.get("user_id") == str(owner.id) and p.get("org_id") == str(org_id):
            pend.append(p)
        else:
            await chat_pending.forget(redis, org_id, via, pid)   # đã xác nhận / huỷ ở nơi khác
    if d.code:
        hit = [p for p in pend if chat_pending.matches(p, d.code)]
        if len(hit) > 1:
            return "Mã " + d.code + " khớp nhiều đề xuất, Sếp gõ đủ 6 ký tự giúp em."
        if not hit:
            late = [g for g in gone if chat_pending.legacy_code(g)[1:].startswith(d.code)] or await _gone_by_ask(sm, org_id, gone, d.code)
            if late:
                await chat_pending.forget(redis, org_id, via, late[0])
                return EXPIRED.format(code="#" + d.code)
            return UNKNOWN_CODE.format(code="#" + d.code)
        target = hit[0]
    else:
        if not pend:
            if gone:
                code = await _gone_code(sm, org_id, gone[0])
                for g in gone:
                    await chat_pending.forget(redis, org_id, via, g)
                return EXPIRED.format(code=code)
            return NONE_PENDING
        if len(pend) > 1:
            lines = [f"• {chat_pending.code_of(p)} — {_headline(p)}" for p in pend]
            return ("Em đang có nhiều đề xuất chờ, Sếp trả lời " + ("OK" if d.verb == "ok" else "HUỶ")
                    + " kèm mã nhé:\n" + "\n".join(lines))
        target = pend[0]
        if d.verb == "ok":
            if chat_pending.kind_of(target) == "outbound":
                return NEED_CODE_OUTBOUND.format(question=chat_pending.question(target, via, with_code=True))
            last = await chat_pending.last_ask(redis, org_id, via)
            if last is None or last[0] != target["id"] or time.time() - last[1] > chat_pending.FRESH_S:
                return NEED_CODE_STALE.format(question=chat_pending.question(target, via, with_code=True))
    if d.verb == "no":
        return await _cancel(sm, redis, app, org_id, via, owner.id, target, ref)
    return await _execute(sm, redis, app, org_id, via, owner.id, target, ref, d.pin2)


async def _undo(sm: async_sessionmaker[AsyncSession], app: Any, org_id: uuid.UUID, via: str, owner_id: uuid.UUID,
                code: str | None, ref: str) -> str:
    """«hoàn tác #MÃ» (spec r4 §4.4/W8): CHỈ ask `executed` ∧ `self` ∧ quyết ≤ 10 phút ∧ chưa hoàn tác, đúng tổ chức; id tài nguyên lấy
    từ `ask.result`. Đi ĐÚNG đường cũ bằng phiên `chat_ok` (không PIN): nhắc ⇒ `PATCH /tasks/{id}` cancelled; ghi nhớ ⇒
    `DELETE /gen/memory/{id}`. Đánh dấu `result.undone` để không hoàn tác hai lần."""
    if not code or len(code) != 5:
        return UNDO_NEED_CODE
    shown = "#" + code
    async with sm() as db:
        await _set_org(db, org_id)
        rows = (await db.execute(text("""
            SELECT id, result FROM ops.asks WHERE org_id = :o AND code = :c AND state = 'executed' AND decision_kind = 'self'
              AND source = 'proposal'
              AND decided_at >= now() - make_interval(secs => :w) AND COALESCE(result->>'undone', '') = ''
            ORDER BY decided_at DESC"""), {"o": org_id, "c": code, "w": UNDO_WINDOW_S})).all()
        await db.rollback()
    res = rows[0].result if len(rows) == 1 and isinstance(rows[0].result, dict) else {}
    rtype, rid = res.get("type"), res.get("id")
    if rtype not in ("task", "memory_note") or not rid:
        return UNDO_REFUSED.format(code=shown)
    call = (("PATCH", f"/tasks/{rid}", {"status": "cancelled"}) if rtype == "task" else ("DELETE", f"/gen/memory/{rid}", {}))
    ses = await _mint(sm, org_id, owner_id, via, pin=False)
    try:
        status, body = await _call(app, ses, *call)
    finally:
        await _revoke(sm, org_id, ses.id)
    if status >= 400:
        return FAILED.format(code=shown, why=_why(status, body))
    async with sm() as db:
        await _set_org(db, org_id)
        await db.execute(text("""UPDATE ops.asks SET result = COALESCE(result, '{}'::jsonb) || '{"undone": true}'::jsonb
                                 WHERE id = :i AND org_id = :o"""), {"i": rows[0].id, "o": org_id})
        await actionlog.record(db, org_id=org_id, actor_type="user", actor_id=f"user:{owner_id}", action="gen.proposal_chat_undone",
                               target_type=str(rtype), target_id=str(rid), detail={"via": via, "confirmation": "chat", "type": rtype,
                                                                                    "ask_id": str(rows[0].id), "ref": ref})
        await db.commit()
    return UNDO_DONE.format(code=shown, what="đã huỷ nhắc việc" if rtype == "task" else "đã xoá ghi nhớ")


async def _gone_code(sm: async_sessionmaker[AsyncSession], org_id: uuid.UUID, pid: str) -> str:
    """Mã hiển thị của đề xuất đã mất khỏi Redis: mã ask (nếu có) — cùng mã Sếp đã thấy; không có ⇒ mã hex cũ."""
    async with sm() as db:
        await _set_org(db, org_id)
        c = (await db.execute(text("SELECT code FROM ops.asks WHERE org_id = :o AND source = 'proposal' AND ref_id = CAST(:r AS uuid)"),
                              {"o": org_id, "r": str(pid)})).scalar_one_or_none()
        await db.rollback()
    return f"#{c}" if c else chat_pending.legacy_code(pid)


async def _gone_by_ask(sm: async_sessionmaker[AsyncSession], org_id: uuid.UUID, gone: list[str], code: str) -> list[str]:
    """Đề xuất đã hết hạn (Redis xoá) mà Sếp gõ MÃ ASK ⇒ tra ask (cùng tổ chức) để báo đúng «đã hết hạn»."""
    if not gone or len(code) != 5:
        return []
    async with sm() as db:
        await _set_org(db, org_id)
        rows = (await db.execute(text("""SELECT ref_id FROM ops.asks WHERE org_id = :o AND source = 'proposal' AND code = :c
                                         AND ref_id = ANY(CAST(:g AS uuid[]))"""),
                                 {"o": org_id, "c": code, "g": [str(g) for g in gone]})).scalars().all()
        await db.rollback()
    return [str(r) for r in rows]


def _headline(p: dict[str, Any]) -> str:
    s = str(p.get("summary") or p.get("type"))
    return s.replace(chat_pending.SUFFIX_NEEDS_PIN, "")[:90]
