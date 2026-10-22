"""Telegram 2 chiều — vòng nhận tin (getUpdates long-poll) chạy trong worker (v0.1.64, T1).

Không webhook (máy sau NAT/tunnel, không mở cổng). Cron `telegram_inbound` mỗi phút (đăng ký ở `JOBS`, được
`gh/worker.py` nối vào danh sách việc nền): với mỗi tổ chức đang BẬT "Nhận tin từ Sếp" (hoặc có mã ghép sống) lấy khoá
Redis riêng, chạy các lượt getUpdates ~20 giây tới hết ngân sách ~55 giây — nên gần như liên tục mà vẫn chỉ một
tiến trình nhận. `update_id` đã xử lý lưu ở `ops.notify_channels.inbound_offset` NGAY (trước khi chạy lệnh) — tối
đa một lần mỗi tin, sập giữa chừng không chạy lại lệnh cũ.

Mỗi update: (1) có mã ghép sống và là `/start <6 số>` ⇒ `inbound.try_pair`; (2) còn lại ⇒ `inbound.evaluate`; không
đạt ⇒ bỏ im lặng + Action Log `telegram.rejected`; đạt ⇒ giới hạn 20 lệnh/phút ⇒ `commands.handle` ⇒ gửi trả lời
cho chat đã ghép.
"""

import asyncio
import json
import logging
import time
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text

from gh import crypto
from gh.telegram import client as tg
from gh.telegram import commands, inbound
from gh.telegram import service as tsvc

log = logging.getLogger("gh.telegram.poller")

LOCK_KEY = "gh:tg:poll:{}"
LOCK_TTL_S = 240
STATUS_KEY = "gh:tg:inbound:status:{}"
STATUS_TTL_S = 900
LONG_POLL_S = 20
BUDGET_S = 55.0
JOB_TIMEOUT_S = 180
PAIRED_TEXT = (
    "Đã ghép tài khoản này với Gen-Harness. Từ giờ Sếp nhắn em ở đây được: /hom_nay · /viec · /suc_khoe · "
    "/nhap <ý> · hoặc hỏi tự do. Em chỉ đọc và đề xuất — việc ghi/gửi Sếp Xác nhận (kèm mã PIN) trong Console."
)
RATE_TEXT = "Sếp nhắn hơi nhanh — em đợi một chút rồi trả lời tiếp (tối đa 20 lệnh mỗi phút)."


async def read_status(redis: Any, org_id: uuid.UUID) -> dict[str, Any] | None:
    """Trạng thái vòng nhận tin lần chạy gần nhất ({"at", "error_code"}); None nếu chưa có / Redis lỗi."""
    try:
        raw = await redis.get(STATUS_KEY.format(org_id))
        v = json.loads(raw) if raw else None
        return v if isinstance(v, dict) else None
    except Exception:  # noqa: BLE001
        return None


async def _status(redis: Any, org_id: uuid.UUID, error_code: str | None) -> None:
    try:
        await redis.set(
            STATUS_KEY.format(org_id),
            json.dumps({"at": datetime.now(UTC).isoformat(), "error_code": error_code}),
            ex=STATUS_TTL_S,
        )
    except Exception:  # noqa: BLE001
        pass


async def _reply(
    client: tg.TelegramClient, token: str, chat_id: str, body: str, buttons: list[commands.Button]
) -> None:
    """Gửi trả lời. Nút link bị Telegram từ chối (vd địa chỉ Console là http/localhost ⇒ 400) ⇒ gửi lại không nút,
    kèm các link ở cuối tin. Lỗi khác chỉ log (mã, không URL)."""
    try:
        await client.send_message(token, chat_id, body, buttons or None)
        return
    except tg.TelegramError as e:
        if not (buttons and e.code == tg.BAD_REQUEST):
            log.warning("Telegram: gửi trả lời lỗi (%s)", e.code)
            return
    try:
        await client.send_message(token, chat_id, body + "\n" + "\n".join(f"{label}: {url}" for label, url in buttons))
    except tg.TelegramError as e:
        log.warning("Telegram: gửi trả lời lỗi (%s)", e.code)


def _now() -> datetime:
    """Đồng hồ của vòng nhận tin — một chỗ để test tiêm thời gian (mô phỏng ranh giới phút)."""
    return datetime.now(UTC)


async def _process(
    sm: Any,
    redis: Any,
    router: Any,
    client: tg.TelegramClient,
    org_id: uuid.UUID,
    token: str,
    update: dict[str, Any],
    now: datetime,
) -> str:
    """Xử lý MỘT update. Trả nhãn kết quả: 'paired' | 'rejected:<lý do>' | 'handled:<kind>'."""
    uid = inbound.update_id_of(update)
    async with sm() as db:
        await db.execute(text("SELECT set_config('app.org_id', :o, true)"), {"o": str(org_id)})
        row = await inbound.load(db, org_id)
        if row is None:
            return "rejected:unconfigured"
        cfg = inbound.config_of(row)
        code = inbound.pair_candidate(update)
        if code is not None and inbound.pair_active(row, now) and uid > cfg.last_update_id:
            ok, reason = await inbound.try_pair(db, redis, row, update, code, now=now)
            await inbound.advance_offset(db, org_id, uid)
            if not ok:
                await inbound.record_reject(db, redis, org_id, reason or inbound.R_PAIR_WRONG, update)
            await db.commit()
            if ok:  # sai mã ⇒ im lặng (người lạ không được biết bot này là Gen-Harness)
                await _reply(client, token, str(update["message"]["chat"]["id"]), PAIRED_TEXT, [])
            return "paired" if ok else f"rejected:{reason}"
        verdict = inbound.evaluate(update, cfg, now=now)
        if uid > cfg.last_update_id:
            await inbound.advance_offset(db, org_id, uid)  # TRƯỚC khi chạy lệnh: tối đa một lần mỗi tin
        if isinstance(verdict, str):
            await inbound.record_reject(db, redis, org_id, verdict, update, owner_tg_user_id=cfg.owner_tg_user_id)
            await db.commit()
            return f"rejected:{verdict}"
        if not row.inbound_enabled:
            await inbound.record_reject(db, redis, org_id, inbound.R_DISABLED, update)
            await db.commit()
            return f"rejected:{inbound.R_DISABLED}"
        await db.commit()
    if not await inbound.rate_ok(redis, org_id, now):
        async with sm() as db:
            await inbound.record_reject(db, redis, org_id, inbound.R_RATE, update)
            await db.commit()
        # Chỉ báo MỘT lần ở tin đầu tiên vượt hạn mức (tin sau im lặng — tránh Telegram tự giới hạn bot).
        # SET NX EX 60: một lời báo mỗi 60 giây (không theo phút đồng hồ — tránh báo hai lần quanh ranh giới phút).
        if await redis.set(f"gh:tg:ratemsg:{org_id}", 1, nx=True, ex=60):
            await _reply(client, token, verdict.chat_id, RATE_TEXT, [])
        return f"rejected:{inbound.R_RATE}"
    ctx = commands.Context(sm=sm, redis=redis, router=router, org_id=org_id, owner_user_id=cfg.owner_user_id)
    body, buttons = await commands.handle(verdict, ctx)
    await _reply(client, token, verdict.chat_id, body, buttons)
    return f"handled:{verdict.kind}"


async def poll_org(
    sm: Any, redis: Any, router: Any, org_id: uuid.UUID, *, transport: Any = None, timeout_s: int = 0
) -> dict[str, Any]:
    """MỘT lượt getUpdates cho một tổ chức rồi xử lý tuần tự các update nhận được. Không ném."""
    out: dict[str, Any] = {"updates": 0, "results": [], "error_code": None}
    client = tg.client_for(transport)
    async with sm() as db:
        await db.execute(text("SELECT set_config('app.org_id', :o, true)"), {"o": str(org_id)})
        row = await inbound.load(db, org_id)
        await db.rollback()
    if row is None or not row.enabled or not (row.inbound_enabled or inbound.pair_active(row)):
        return out
    try:
        token = crypto.decrypt(bytes(row.token_enc), tsvc.TOKEN_AAD).decode()
    except Exception:  # noqa: BLE001 — token không giải được (đổi khoá): để cấu hình báo lỗi
        out["error_code"] = tg.TOKEN_REJECTED
        await _status(redis, org_id, tg.TOKEN_REJECTED)
        return out
    try:
        updates = await client.poll_updates(token, int(row.inbound_offset or 0) + 1, timeout_s=timeout_s)
    except tg.TelegramError as e:
        out["error_code"] = e.code
        await _status(redis, org_id, e.code)
        log.warning("Telegram: getUpdates lỗi (%s)", e.code)
        return out
    await _status(redis, org_id, None)
    out["updates"] = len(updates)
    for upd in sorted(updates, key=inbound.update_id_of):
        try:
            out["results"].append(await _process(sm, redis, router, client, org_id, token, upd, _now()))
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — một tin hỏng không được chặn các tin sau
            log.exception("Telegram: xử lý update lỗi")
            out["results"].append("error")
    return out


async def run_inbound(
    sm: Any,
    redis: Any,
    router: Any,
    *,
    transport: Any = None,
    budget_s: float = BUDGET_S,
    long_poll_s: int = LONG_POLL_S,
) -> dict[str, Any]:
    """Một lượt cron: mọi tổ chức đang nhận tin; mỗi tổ chức một khoá Redis (hai worker không tranh offset)."""
    async with sm() as db:
        orgs = (
            (
                await db.execute(
                    text("""
            SELECT org_id FROM ops.notify_channels
            WHERE enabled AND (inbound_enabled OR (pair_code_hash IS NOT NULL AND pair_expires_at > now()))""")
                )
            )
            .scalars()
            .all()
        )
        await db.rollback()
    stats: dict[str, Any] = {"orgs": len(orgs), "updates": 0}
    deadline = time.monotonic() + budget_s
    for org_id in orgs:
        key = LOCK_KEY.format(org_id)
        if not await redis.set(key, "1", nx=True, ex=LOCK_TTL_S):
            continue  # lượt trước còn chạy
        try:
            while time.monotonic() < deadline:
                started = time.monotonic()
                wait = max(1, min(long_poll_s, int(deadline - time.monotonic()))) if long_poll_s else 0
                r = await poll_org(sm, redis, router, org_id, transport=transport, timeout_s=wait)
                stats["updates"] += r["updates"]
                if r["error_code"] is not None:
                    break
                if not r["updates"] and time.monotonic() - started < 1.0:
                    await asyncio.sleep(1.0)  # phòng khi Telegram trả ngay (không long-poll)
        finally:
            await redis.delete(key)
    return stats


async def telegram_inbound(ctx: dict[str, Any]) -> dict[str, Any]:
    """v0.1.64 (T1): nhận tin Telegram của Sếp (long-poll) — xem module. Worker gọi mỗi phút."""
    from gh.db import sessionmaker

    return await run_inbound(sessionmaker(), ctx["redis_bus"], ctx.get("model_router"))


#: (hàm, tham số cron) như `gh.backup.JOBS` — `gh/worker.py` nối vào `functions` + `cron_jobs`.
JOBS: list[tuple[Any, dict[str, Any]]] = [(telegram_inbound, {"minute": set(range(60)), "timeout": JOB_TIMEOUT_S})]

__all__ = ["JOBS", "poll_org", "run_inbound", "telegram_inbound", "read_status"]
