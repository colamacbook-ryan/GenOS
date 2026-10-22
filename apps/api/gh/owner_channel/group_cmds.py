"""Lệnh nhóm của kênh quản trị (CHỈ Zalo/WhatsApp; Telegram vẫn chỉ đọc — thiết kế §5, mặc định 3):

  /nhom                       liệt kê nhóm (tên, mã, chế độ nghe, hỗ trợ bật/tắt)
  /nghe <mã> · /bo <mã>       trả lời câu hỏi «nhóm do người khác thêm / Sếp gọi ở nhóm không nghe» (mã 4 ký tự)
  /ho_tro                     xem nhóm đang bật hỗ trợ và số tin đã trả lời hôm nay
  /ho_tro <mã nhóm> bat [giờ] xin bật chế độ hỗ trợ — bot hỏi lại «Gõ /xac_nhan <mã> trong 5 phút»
  /ho_tro <mã nhóm> tat       tắt ngay (hướng an toàn, không cần xác nhận)
  /xac_nhan <mã>              xác nhận bật hỗ trợ

KHÔNG có PIN qua chat. Mọi lệnh đổi trạng thái: Nhật ký hành động `owner_channel.<lệnh>`, chuông Console, Nhật
ký lắng nghe.
Lệnh chỉ ĐỔI CẤU HÌNH — không có đường gửi tin ra ngoài (test AST).
"""

import logging
import re
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gh import notifications
from gh.chassis import actionlog
from gh.data import listen_events as lev
from gh.data.common import LISTENING_MODES
from gh.data.ingest import sync_listen_sets
from gh.owner_channel import inbound as oin
from gh.owner_channel import questions
from gh.owner_channel.identity import OwnerIds

log = logging.getLogger("gh.owner_channel.group_cmds")

KINDS = ("nhom", "nghe", "bo", "ho_tro", "xac_nhan")
CONFIRM_TTL = timedelta(minutes=5)
MAX_LIST = 15
MAX_HOURS = 24 * 30
_BAT = ("bat", "bật", "on")
_TAT = ("tat", "tắt", "off")

USAGE_SUPPORT = ("Cách dùng: /ho_tro (xem) · /ho_tro <mã nhóm> bat [số giờ] · /ho_tro <mã nhóm> tat. "
                 "Mã nhóm xem ở /nhom.")
NO_Q = "Em không thấy câu hỏi mã đó (có thể đã trả lời hoặc hết hạn). Gõ /nhom để xem lại."


def _label(mode: str) -> str:
    return lev.LISTEN_MODE_LABEL.get(mode, mode)


async def _find_group(db: AsyncSession, org_id: uuid.UUID, channel: str, ref: str) -> Any:
    """Nhóm theo mã (`GRP-ZL-0003`, không phân biệt hoa thường) hoặc phần số cuối (`3`, `0003`) nếu không mơ hồ."""
    ref = ref.strip()
    rows = (await db.execute(text("""
        SELECT g.id, g.code, g.name, g.listen_mode, g.left_at, g.added_by_kind FROM core.groups g
        JOIN core.channels c ON c.id = g.channel_id WHERE g.org_id = :o AND c.type = :t AND g.left_at IS NULL
          AND (upper(g.code) = upper(:r) OR (:r ~ '^[0-9]{1,6}$' AND g.code ~ ('-0*' || :r2 || '$')))
        LIMIT 3"""), {"o": org_id, "t": channel, "r": ref, "r2": ref.lstrip("0") or "0"})).all()
    return rows[0] if len(rows) == 1 else None


async def _announce(db: AsyncSession, redis: Redis, org_id: uuid.UUID, channel: str, cmd: str, what: str,
                    group_id: uuid.UUID | None, detail: dict[str, Any]) -> None:
    """Nhật ký hành động + chuông Console cho mọi lệnh ĐỔI trạng thái."""
    await actionlog.record(db, org_id=org_id, actor_type="system", actor_id=f"system:owner_channel:{channel}",
                           action=f"owner_channel.{cmd}", target_type="group" if group_id else "owner_channel",
                           target_id=str(group_id) if group_id else channel, detail={"via": channel, **detail})
    await notifications.notify(db, org_id, await notifications.owner_ids(db, org_id), kind="owner_channel.command",
                               title="Sếp vừa đổi cài đặt qua kênh quản trị", body=what, link="/connections",
                               redis=redis)


async def _nhom(db: AsyncSession, org_id: uuid.UUID, channel: str) -> str:
    rows = (await db.execute(text("""
        SELECT g.code, g.name, g.listen_mode, g.added_by_kind,
               EXISTS (SELECT 1 FROM ops.group_support s WHERE s.group_id = g.id AND s.disabled_at IS NULL
                         AND (s.until_at IS NULL OR s.until_at > now())) AS support
        FROM core.groups g JOIN core.channels c ON c.id = g.channel_id
        WHERE g.org_id = :o AND c.type = :t AND g.left_at IS NULL ORDER BY g.listen_mode = 'pending' DESC, g.name
        LIMIT :n"""), {"o": org_id, "t": channel, "n": MAX_LIST + 1})).all()
    if not rows:
        return "Em chưa có nhóm nào."
    lines = ["Các nhóm của em:"]
    for r in rows[:MAX_LIST]:
        extra = " · hỗ trợ BẬT" if r.support else ""
        lines.append(f"{r.code} «{str(r.name)[:40]}» — {_label(r.listen_mode)}{extra}")
    if len(rows) > MAX_LIST:
        lines.append("… còn nhiều nhóm nữa, xem đủ trong Console › Quan hệ › Danh bạ nhóm.")
    return "\n".join(lines)


async def _answer_listen(db: AsyncSession, redis: Redis, org_id: uuid.UUID, channel: str, cmd: str, code: str) -> str:
    q = await questions.open_by_code(db, org_id, code)
    if q is None or q.kind not in ("group_added", "listen_confirm") or q.subject_id is None:
        return NO_Q
    g = (await db.execute(text("SELECT id, name, listen_mode FROM core.groups WHERE id = :i AND left_at IS NULL"),
                          {"i": q.subject_id})).one_or_none()
    if g is None:
        await questions.answer(db, q.id, cmd)
        return "Nhóm đó em không còn ở trong nữa."
    mode = "silent" if cmd == "nghe" else "off"
    await db.execute(text("UPDATE core.groups SET listen_mode = :m WHERE id = :i"), {"m": mode, "i": g.id})
    if mode == "off":
        await oin.disable_support(db, org_id, channel, reason="owner", group_id=g.id)
    await questions.answer(db, q.id, cmd)
    name = str(g.name)[:80]
    await _announce(db, redis, org_id, channel, cmd, f"Nhóm «{name}»: {_label(mode)}.", g.id, {"mode": mode})
    await sync_listen_sets(db, redis, org_id)
    return f"Đã xong: nhóm «{name}» → {_label(mode)}." + (
        " Em lưu tin, không bao giờ trả lời (trừ khi Sếp tag em)." if mode == "silent" else "")


async def _approved_for_support(db: AsyncSession, g: Any) -> bool:
    """Nhóm do chính Sếp thêm bot, hoặc Sếp đã duyệt nghe nhóm qua câu hỏi."""
    if g.added_by_kind == "owner":
        return True
    return (await db.execute(text("""SELECT 1 FROM ops.owner_questions WHERE subject_id = :g AND answer = 'nghe'
                                     AND kind IN ('group_added', 'listen_confirm') LIMIT 1"""),
                             {"g": g.id})).first() is not None


async def _ho_tro(db: AsyncSession, redis: Redis, org_id: uuid.UUID, channel: str, args: str) -> str:
    parts = args.split()
    if not parts:
        rows = (await db.execute(text("""
            SELECT g.code, g.name, (SELECT count(*) FROM ops.auto_sends a WHERE a.group_id = g.id AND a.basis =
            'support'
                                      AND a.created_at > date_trunc('day', now())) AS n
            FROM ops.group_support s JOIN core.groups g ON g.id = s.group_id JOIN core.channels c ON c.id = g.channel_id
            WHERE s.org_id = :o AND c.type = :t AND s.disabled_at IS NULL AND (s.until_at IS NULL OR s.until_at > now())
            ORDER BY g.name LIMIT :n"""), {"o": org_id, "t": channel, "n": MAX_LIST})).all()
        if not rows:
            return "Chưa nhóm nào bật chế độ hỗ trợ. " + USAGE_SUPPORT
        return "Nhóm đang bật hỗ trợ:\n" + "\n".join(f"{r.code} «{str(r.name)[:40]}» — hôm nay đã trả lời {r.n} tin"
                                                       for r in rows)
    if len(parts) < 2 or parts[1].lower() not in _BAT + _TAT:
        return USAGE_SUPPORT
    g = await _find_group(db, org_id, channel, parts[0])
    if g is None:
        return "Em không tìm thấy nhóm đó (hoặc mã trùng nhiều nhóm). Gõ /nhom để xem mã."
    if parts[1].lower() in _TAT:
        gone = await oin.disable_support(db, org_id, channel, reason="owner", group_id=g.id)
        if not gone:
            return f"Nhóm «{str(g.name)[:60]}» chưa bật hỗ trợ."
        await _announce(db, redis, org_id, channel, "ho_tro_tat", f"Tắt chế độ hỗ trợ nhóm «{str(g.name)[:60]}».",
                        g.id, {"group_id": str(g.id)})
        await sync_listen_sets(db, redis, org_id)
        return f"Đã tắt chế độ hỗ trợ nhóm «{str(g.name)[:60]}»."
    hours: int | None = None
    if len(parts) >= 3:
        if not re.fullmatch(r"\d{1,4}", parts[2]) or not 1 <= int(parts[2]) <= MAX_HOURS:
            return "Số giờ phải là số từ 1 trở lên, ví dụ: /ho_tro 3 bat 8"
        hours = int(parts[2])
    if g.listen_mode not in LISTENING_MODES:
        return (f"Nhóm «{str(g.name)[:60]}» đang {_label(g.listen_mode)}. "
                "Bật nghe nhóm trước (Console) rồi mới bật hỗ trợ.")
    if not await _approved_for_support(db, g):
        return "Nhóm này không do Sếp thêm em vào và Sếp chưa duyệt nghe. Gõ /nghe với mã câu hỏi của nhóm trước."
    q = await questions.create(db, org_id, kind="support_confirm", subject_id=g.id,
                               payload={"hours": hours}, ttl=CONFIRM_TTL)
    span = f" trong {hours} giờ" if hours else ""
    return (f"Sếp muốn bật chế độ hỗ trợ cho nhóm «{str(g.name)[:60]}»{span}? Em sẽ trả lời thành viên khi được "
            "tag/trả lời, tự xưng là trợ lý tự động, không nói giá/hợp đồng/nhân sự. "
            f"Gõ /xac_nhan {q.short_code} trong 5 phút để bật.")


async def _xac_nhan(db: AsyncSession, redis: Redis, org_id: uuid.UUID, channel: str, code: str) -> str:
    q = await questions.open_by_code(db, org_id, code, kind="support_confirm")
    if q is None or q.subject_id is None:
        return NO_Q
    g = (await db.execute(text("""SELECT g.id, g.name, g.listen_mode, g.added_by_kind, g.left_at, c.id AS channel_id
                                  FROM core.groups g JOIN core.channels c ON c.id = g.channel_id
                                  WHERE g.id = :i AND c.type = :t AND g.org_id = :o"""),
                          {"i": q.subject_id, "t": channel, "o": org_id})).one_or_none()
    if g is None or g.left_at is not None or g.listen_mode not in LISTENING_MODES:
        await questions.answer(db, q.id, "huy")
        return "Nhóm đó không còn đủ điều kiện bật hỗ trợ."
    if not await _approved_for_support(db, g):
        await questions.answer(db, q.id, "huy")
        return "Nhóm đó không còn đủ điều kiện bật hỗ trợ."
    hours = (q.payload or {}).get("hours")
    until = datetime.now(UTC) + timedelta(hours=int(hours)) if hours else None
    await oin.expire_stale_support(db, g.id)
    try:
        async with db.begin_nested():
            await db.execute(text("""INSERT INTO ops.group_support (org_id, group_id, enabled_via, until_at)
                                     VALUES (:o, :g, :v, :u)"""), {"o": org_id, "g": g.id, "v": channel, "u": until})
    except Exception:  # noqa: BLE001 — chỉ một lượt đang bật mỗi nhóm (unique index)
        await questions.answer(db, q.id, "xac_nhan")
        return f"Nhóm «{str(g.name)[:60]}» đã bật hỗ trợ từ trước."
    await questions.answer(db, q.id, "xac_nhan")
    await lev.record(db, org_id, "support_on", channel_id=g.channel_id, channel_type=channel, group_id=g.id,
                     detail={"via": channel, "hours": hours})
    await _announce(db, redis, org_id, channel, "ho_tro_bat",
                    f"Bật chế độ hỗ trợ nhóm «{str(g.name)[:60]}» qua {channel}.", g.id,
                    {"group_id": str(g.id), "hours": hours})
    await sync_listen_sets(db, redis, org_id)
    tail = f" trong {hours} giờ." if hours else ". Gõ /ho_tro <mã> tat để tắt."
    return f"Đã bật chế độ hỗ trợ nhóm «{str(g.name)[:60]}»" + tail


async def handle(db: AsyncSession, redis: Redis, org_id: uuid.UUID, oi: OwnerIds, kind: str, args: str) -> str:
    """Chạy một lệnh nhóm; trả chữ trả lời. Gọi khi `oi.enabled`; commit do bên gọi."""
    channel = oi.channel
    if kind == "nhom":
        return await _nhom(db, org_id, channel)
    if kind in ("nghe", "bo"):
        return await _answer_listen(db, redis, org_id, channel, kind, args.split()[0] if args.split() else "")
    if kind == "ho_tro":
        return await _ho_tro(db, redis, org_id, channel, args)
    if kind == "xac_nhan":
        return await _xac_nhan(db, redis, org_id, channel, args.split()[0] if args.split() else "")
    return "Em chưa có lệnh đó. Gõ /tro_giup để xem các lệnh em hiểu."
