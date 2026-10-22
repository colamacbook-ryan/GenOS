"""Ingest: bridge → Kho thô (ARCHITECTURE §4.2) và đồng bộ trạng thái kênh, danh bạ nhóm.

Mỗi tin vào: một transaction gồm ánh xạ nhóm/người gửi (tạo mới nếu chưa có), khử trùng theo
(kênh, mã tin), `INSERT raw.events` + `refinery.event_state(pending)`, `NOTIFY raw_ingested`. Sau commit mới
đẩy dòng mới lên WebSocket. Tin của nhóm chưa bật (hoặc tin 1-1 khi Owner chưa cho nghe) bị bỏ — khoá cứng
`listen_authorized_only`, kiểm lần hai sau bridge.
"""

import hashlib
import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import orjson
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gh import crypto, realtime
from gh.biz.relations import service as rsvc
from gh.chassis import actionlog
from gh.chassis.bus import BRIDGE_CONTROL, EventBus, uuid7
from gh.data import listen_events as lev
from gh.data.common import (
    CHANNEL_NAME,
    CHANNEL_PREFIX,
    DEFAULT_NEW_GROUP_MODE,
    LISTENING_MODES,
    NEW_GROUP_MODE,
    NEW_SILENT_KEY,
    bump_channel_counter,
    direct_mode,
    fetch_raw,
    initial_group_mode,
    iso,
    new_group_mode,
    org_settings,
    raw_item,
    set_channel_setting,
)
from gh.owner_channel import inbound as owner_inbound
from gh.owner_channel import questions as owner_questions
from gh.owner_channel import reports as owner_reports
from gh.owner_channel.identity import OwnerIds, is_bot_account, is_owner, owner_ids, sync_owner_sets

log = logging.getLogger("gh.ingest")

#: v0.1.62 (F063): TTL ảnh QR trong Redis lấy theo `expires_at`
# của sự kiện (Zalo sống 100 giây, WhatsApp 60/20 giây) cộng
#: một đoạn đệm để Console còn thấy mã tới đúng lúc nó hết hạn; không đọc được `expires_at` thì dùng giá trị dự phòng.
QR_TTL_FALLBACK_S = 110
QR_TTL_MARGIN_S = 10
#: v0.1.36 (F-6a): lý do phiên rớt → câu cho chuông "Kênh … đã ngắt kết nối" (không đưa lỗi thô/credential).
CHANNEL_DOWN_REASONS = {"expired": "phiên đã hết hạn", "error": "gặp lỗi", "logged_out": "đã bị đăng xuất"}
#: v0.1.62 (F046): nguyên nhân THẬT theo mã lỗi bridge — mất mạng không được gọi là "phiên hết hạn".
CHANNEL_DOWN_BY_ERROR = {
    "NETWORK_UNAVAILABLE": "mất kết nối mạng (Gen đang tự nối lại bằng phiên đã lưu)",
    "RECONNECT_FAILED": "mất kết nối mạng",
    "CONNECTION_REPLACED": "tài khoản vừa được mở ở một nơi khác nên phiên này bị ngắt",
    "LOGGED_OUT_REMOTELY": "thiết bị đã bị gỡ khỏi tài khoản trên điện thoại",
    "CREDENTIAL_REJECTED": "nền tảng không còn nhận phiên đã lưu (đã đăng xuất hoặc hết hạn)",
}
#: Mã lỗi mà phiên còn credential và nối lại được bằng nút "Nối lại bằng phiên đã lưu" (không cần quét QR).
RESUMABLE_ERRORS = ("NETWORK_UNAVAILABLE", "RECONNECT_FAILED", "CONNECTION_REPLACED", "LOGIN_FAILED")


def qr_ttl_s(expires_at: Any, now: datetime | None = None) -> int:
    """Số giây giữ ảnh QR trong Redis: tới `expires_at` + đệm (tối thiểu 5 giây); sai định dạng → giá trị dự phòng."""
    if not isinstance(expires_at, str) or not expires_at:
        return QR_TTL_FALLBACK_S
    try:
        end = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
    except ValueError:
        return QR_TTL_FALLBACK_S
    if end.tzinfo is None:
        end = end.replace(tzinfo=UTC)
    left = (end - (now or datetime.now(UTC))).total_seconds()
    return max(5, min(600, int(left) + QR_TTL_MARGIN_S))


def channel_down_text(reason: str, error: str | None) -> tuple[str, str]:
    """(nguyên nhân, việc Sếp cần làm) cho chuông "Kênh … đã ngắt kết nối" theo lý do + mã lỗi bridge."""
    cause = CHANNEL_DOWN_BY_ERROR.get(error or "") or CHANNEL_DOWN_REASONS.get(reason, "gặp lỗi")
    if error == "NETWORK_UNAVAILABLE":
        return cause, "không cần quét lại QR; có mạng là tự nhận tin trở lại."
    if reason == "error" and error in RESUMABLE_ERRORS:
        return cause, 'bấm "Nối lại bằng phiên đã lưu" ở Kết nối (không cần quét QR).'
    if reason == "error":
        return cause, 'bấm "Nối lại bằng phiên đã lưu" ở Kết nối; còn lỗi thì quét lại QR.'
    if error in CHANNEL_DOWN_BY_ERROR:
        return cause, "đăng nhập lại (quét mã QR) để tiếp tục nhận tin."
    return cause, "đăng nhập lại để tiếp tục nhận tin."  # mã lỗi lạ: giữ câu chung, không đưa mã thô lên chuông
KINDS = ("text", "image", "file", "sticker", "reaction", "system", "other")


def _ts(value: Any) -> datetime:
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value / 1000 if value > 1e11 else value, UTC)
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            pass
    return datetime.now(UTC)


async def channel_row(db: AsyncSession, org_id: uuid.UUID, type: str) -> Any:
    return (await db.execute(text("SELECT id, type, name FROM core.channels WHERE org_id = :o AND type = :t"),
                             {"o": org_id, "t": type})).one_or_none()


async def upsert_group(db: AsyncSession, org_id: uuid.UUID, channel: Any, external_id: str, name: str | None,
                       member_count: int | None = None, *, quiet: bool = False) -> Any:
    return (await upsert_group_ex(db, org_id, channel, external_id, name, member_count, quiet=quiet))[0]


async def upsert_group_ex(db: AsyncSession, org_id: uuid.UUID, channel: Any, external_id: str, name: str | None,
                          member_count: int | None = None, *, quiet: bool = False,
                          added_by: tuple[str, str | None] | None = None) -> tuple[Any, bool]:
    """Như `upsert_group` nhưng trả thêm cờ "vừa tạo". v0.1.68 (Nhật ký lắng nghe): nhóm MỚI ghi sự kiện `group_new`
    (kèm chế độ nghe nó tự vào), nhóm đổi tên ghi `group_renamed`; `quiet` tắt sự kiện (lần quét danh bạ đầu tiên)."""
    created = False
    row = (await db.execute(text("""SELECT id, code, name, listen_mode, left_at FROM core.groups
                                    WHERE channel_id = :c AND external_id = :x"""),
                            {"c": channel.id, "x": external_id})).one_or_none()
    if row is None:
        # QD 17/10/2026: nhóm mới MẶC ĐỊNH vào "Lắng nghe im lặng" (`silent`: lưu hết tin, không bao giờ trả lời, kể
        # cả khi được tag). Theo thiết lập của kênh (`new_group_mode`, F1d): silent (mặc định, kể cả khi chưa có khoá)
        # · off ⇒ vào thẳng chế độ đó · ask ⇒ `pending` (chưa lưu tin cho tới khi Sếp chọn trong hộp «Nhóm mới»).
        # Danh sách thành viên luôn được thu (Q5=2).
        mode = initial_group_mode(await org_settings(db, org_id), channel.type)
        # v0.1.72 (tài khoản riêng của bot): chỉ nhóm do chính Sếp thêm bot mới được nghe ngay; người khác / không rõ ai
        # thêm ⇒ `pending` (không lưu nội dung) và hỏi Sếp qua kênh quản trị. Tài khoản cá nhân của Sếp: hành vi cũ.
        bot_acct = await is_bot_account(db, org_id, channel.type)
        by_kind, by_id = added_by if added_by is not None else (("unknown", None) if bot_acct else (None, None))
        if bot_acct and by_kind != "owner":
            mode = NEW_GROUP_MODE
        code = (await db.execute(text("SELECT core.next_code(:p)"),
                                 {"p": f"GRP-{CHANNEL_PREFIX.get(channel.type, 'XX')}"})).scalar_one()
        row = (await db.execute(text("""
            INSERT INTO core.groups (org_id, code, channel_id, external_id, name, listen_mode, member_count,
                                     added_by_external_id, added_by_kind, added_at)
            VALUES (:o, :code, :c, :x, :n, :mode, :m, :by_id, :by_kind, CASE WHEN CAST(:by_kind AS text) IS NULL
                                                                           THEN NULL ELSE now() END)
            ON CONFLICT (channel_id, external_id) DO UPDATE SET name = core.groups.name
            RETURNING id, code, name, listen_mode, left_at, (xmax = 0) AS inserted"""),
            {"o": org_id, "code": code, "c": channel.id, "x": external_id, "n": name or external_id,
             "m": member_count, "mode": mode, "by_id": by_id, "by_kind": by_kind})).one()
        created = bool(row.inserted)      # ON CONFLICT (hai luồng tạo cùng lúc) ⇒ không phải nhóm vừa tạo
        if created and not quiet:
            await lev.record(db, org_id, "group_new", channel_id=channel.id, channel_type=channel.type,
                             group_id=row.id, detail={"listen_mode": row.listen_mode, "name": row.name})
        if row.inserted and row.listen_mode == "silent":      # thẻ kênh báo «N nhóm mới đã vào Lắng nghe im lặng»
            await bump_channel_counter(db, org_id, NEW_SILENT_KEY, channel.type)
        if created and bot_acct and by_kind != "owner" and not quiet:
            await _ask_owner_about_group(db, org_id, channel, row, by_kind or "unknown")
    elif (name and name != row.name) or member_count is not None:
        await db.execute(text("""UPDATE core.groups SET name = COALESCE(:n, name),
                                 member_count = COALESCE(:m, member_count) WHERE id = :i"""),
                         {"n": name, "m": member_count, "i": row.id})
        # Tên cũ còn là mã thô (nhóm vừa tạo chưa có tên) thì cập nhật tên không phải "đổi tên".
        if name and name != row.name and row.name != external_id and not quiet:
            await lev.record(db, org_id, "group_renamed", channel_id=channel.id, channel_type=channel.type,
                             group_id=row.id, detail={"old": row.name[:120], "name": name[:120]})
    return row, created


async def _ask_owner_about_group(db: AsyncSession, org_id: uuid.UUID, channel: Any, group: Any, by_kind: str) -> None:
    """Nhóm mới do NGƯỜI KHÁC / không rõ ai thêm bot (tài khoản bot): ghi Nhật ký + hỏi Sếp qua kênh quản trị.
    Sự kiện đặc
    biệt (Sếp 18/10): tối đa 3 câu lẻ mỗi giờ, từ câu thứ 4 gộp thành MỘT tin «N nhóm mới…» (chống spam nhóm)."""
    await lev.record(db, org_id, "group_added_by_other", channel_id=channel.id, channel_type=channel.type,
                     group_id=group.id, detail={"added_by_kind": by_kind, "name": group.name})
    q = await owner_questions.create(db, org_id, kind="group_added", subject_id=group.id,
                                     payload={"added_by_kind": by_kind})
    n = int((await db.execute(text("""
        SELECT count(*) FROM ops.owner_questions
        WHERE org_id = :o AND kind = 'group_added' AND created_at > now() - interval '1 hour'"""),
                              {"o": org_id})).scalar_one())
    who = "Có người khác" if by_kind == "other" else "Chưa rõ ai"
    if n <= 3:
        await owner_reports.enqueue(
            db, org_id, channel.type, kind="ask", severity="important", dedupe_key=f"group_added:{group.id}",
            text_=(f"{who} thêm em vào nhóm «{str(group.name)[:80]}». Em chưa nghe nhóm này. "
                   f"Gõ /nghe {q.short_code} (Lắng nghe im lặng), /bo {q.short_code} để không nghe."))
    else:
        await owner_reports.enqueue(
            db, org_id, channel.type, kind="ask", severity="important",
            dedupe_key=f"group_added:batch:{datetime.now(UTC):%Y%m%d%H}",
            text_=f"Có {n} nhóm mới do người khác / chưa rõ ai thêm em trong 1 giờ qua, em chưa nghe nhóm nào. "
                  "Gõ /nhom để xem danh sách và trả lời từng nhóm.")


async def handle_self_joined(db: AsyncSession, redis: Redis, org_id: uuid.UUID, p: dict[str, Any]) -> Any:
    """`group.self_joined{channel, group_id, name, added_by, added_by_alt, method}`: ai thêm bot vào nhóm
    (thiết kế §3.5).
    Tài khoản bot: do Sếp (khớp ID đã ghép) ⇒ vào chế độ nghe mặc định; người khác / không rõ ⇒ `pending` + hỏi Sếp.
    Tài khoản cá nhân của Sếp: như v0.1.70 (chỉ ghi `added_by_*`). Sự kiện tới muộn chỉ NÂNG `unknown → owner`.
    Trả (nhóm, mới?) hoặc None."""
    channel = await channel_row(db, org_id, str(p.get("channel", "")))
    gid = str(p.get("group_id") or "")
    if channel is None or not gid:
        return None
    oi: OwnerIds = await owner_ids(db, org_id, channel.type)
    by = str(p.get("added_by") or "")
    if not by or p.get("method") == "link":
        kind: str = "unknown"
    else:
        kind = "owner" if is_owner(oi, by, p.get("added_by_alt")) else "other"
    cur = (await db.execute(text("""SELECT id, listen_mode, left_at, added_by_kind FROM core.groups
                                    WHERE channel_id = :c AND external_id = :x"""),
                            {"c": channel.id, "x": gid})).one_or_none()
    if cur is None:
        row, created = await upsert_group_ex(db, org_id, channel, gid, p.get("name"), None, added_by=(kind, by or None))
        return row, created
    bot_acct = await is_bot_account(db, org_id, channel.type)
    rejoin = cur.left_at is not None
    upgrade = cur.added_by_kind == "unknown" and kind == "owner"
    if cur.added_by_kind is None or rejoin or upgrade:
        await db.execute(text("""UPDATE core.groups SET added_by_external_id = :b, added_by_kind = :k, added_at = now(),
                                 left_at = NULL WHERE id = :i"""), {"b": by or None, "k": kind, "i": cur.id})
        if bot_acct and kind != "owner" and rejoin:
            await db.execute(text("UPDATE core.groups SET listen_mode = 'pending' WHERE id = :i"), {"i": cur.id})
            grp = (await db.execute(text("SELECT id, name FROM core.groups WHERE id = :i"), {"i": cur.id})).one()
            await _ask_owner_about_group(db, org_id, channel, grp, kind)
        elif bot_acct and upgrade and cur.listen_mode == "pending":
            mode = initial_group_mode(await org_settings(db, org_id), channel.type)
            await db.execute(text("UPDATE core.groups SET listen_mode = :m WHERE id = :i"), {"m": mode, "i": cur.id})
        if rejoin:
            await lev.record(db, org_id, "group_rejoined", channel_id=channel.id, channel_type=channel.type,
                             group_id=cur.id)
    row = (await db.execute(text("SELECT id, code, name, listen_mode, left_at FROM core.groups WHERE id = :i"),
                            {"i": cur.id})).one()
    return row, False


async def handle_self_removed(db: AsyncSession, redis: Redis, org_id: uuid.UUID, p: dict[str, Any]) -> bool:
    """`group.self_removed{channel, group_id, by}`: bot bị mời ra / bị chặn khỏi nhóm ⇒ đặt đã rời, tắt hỗ trợ của nhóm
    (`bot_removed`), ghi Nhật ký, báo Sếp (quan trọng) nếu kênh quản trị đang bật."""
    channel = await channel_row(db, org_id, str(p.get("channel", "")))
    gid = str(p.get("group_id") or "")
    if channel is None or not gid:
        return False
    g = (await db.execute(text("""UPDATE core.groups SET left_at = COALESCE(left_at, now())
                                  WHERE channel_id = :c AND external_id = :x RETURNING id, name"""),
                          {"c": channel.id, "x": gid})).one_or_none()
    if g is None:
        return False
    await owner_inbound.disable_support(db, org_id, channel.type, reason="bot_removed", group_id=g.id)
    await lev.record(db, org_id, "bot_removed", channel_id=channel.id, channel_type=channel.type, group_id=g.id)
    await owner_reports.enqueue(db, org_id, channel.type, kind="alert", severity="important",
                                dedupe_key=f"bot_removed:{g.id}:{datetime.now(UTC):%Y%m%d%H}",
                                text_=f"Em vừa bị mời ra khỏi nhóm «{str(g.name)[:80]}». Em không còn nghe nhóm này.")
    return True


def _looks_raw(display_name: str, external_id: str, phone_e164: str | None) -> bool:
    """Tên hiển thị còn là mã tài khoản thô (rỗng, mã kênh, SĐT, dãy số, địa chỉ kiểu `...@s.whatsapp.net`)."""
    n = (display_name or "").strip()
    return (not n or n == external_id or n == phone_e164 or n.lstrip("+").replace(" ", "").isdigit()
            or ("@" in n and " " not in n))


async def upsert_identity(db: AsyncSession, org_id: uuid.UUID, channel: Any, external_id: str, name: str | None,
                          phone: str | None = None) -> Any:
    name = (name or "").strip()[:200] or None
    row = (await db.execute(text("""SELECT pi.id, pi.person_id, pi.phone_e164, p.display_name,
                                           p.attrs->>'name_source' AS name_source
                                    FROM core.person_identities pi JOIN core.persons p ON p.id = pi.person_id
                                    WHERE pi.channel_id = :c AND pi.external_id = :x"""),
                            {"c": channel.id, "x": external_id})).one_or_none()
    if row is not None:
        if name or phone:
            e164 = _e164(phone)
            await db.execute(text("""UPDATE core.person_identities SET handle = COALESCE(:h, handle),
                                     phone_e164 = COALESCE(:p, phone_e164) WHERE id = :i"""),
                             {"h": name, "p": e164, "i": row.id})
            if e164 and e164 != row.phone_e164:
                # v0.1.40 (F-16): dò trùng chỉ xét định danh mới/hồ sơ vừa sửa (mốc `ops.job_watermarks`) — SĐT mới
                # của định danh cũ phải làm hồ sơ "mới" để cặp trùng SĐT được dò (trigger touch đặt updated_at).
                await db.execute(text("UPDATE core.persons SET updated_at = now() WHERE id = :p"),
                                 {"p": row.person_id})
        if (name and name != external_id and row.name_source != "owner"
                and _looks_raw(row.display_name, external_id, row.phone_e164)):
            await _upgrade_name(db, row.person_id, name)
        return row
    code = (await db.execute(text("SELECT core.next_code('PER')"))).scalar_one()
    use_name = name if name and name != external_id else None
    person_id = (await db.execute(text("""
        INSERT INTO core.persons (org_id, code, display_name, relation_to_owner, attrs)
        VALUES (:o, :code, :n, 'stranger', CAST(:a AS jsonb)) RETURNING id"""),
        {"o": org_id, "code": code, "n": (use_name or external_id)[:200],
         "a": orjson.dumps({"auto": True}).decode()}
    )).scalar_one()
    return (await db.execute(text("""
        INSERT INTO core.person_identities (person_id, channel_id, external_id, handle, phone_e164)
        VALUES (:p, :c, :x, :h, :ph)
        ON CONFLICT (channel_id, external_id)
          DO UPDATE SET handle = COALESCE(EXCLUDED.handle, core.person_identities.handle)
        RETURNING id, person_id"""),
        {"p": person_id, "c": channel.id, "x": external_id, "h": name, "ph": _e164(phone)})).one()


async def _upgrade_name(db: AsyncSession, person_id: uuid.UUID, name: str) -> None:
    """v0.1.61 (F047): tên hiển thị còn là mã thô → nâng lên tên thật vừa gặp (tên kênh/pushName). KHÔNG đè tên Sếp đã
    sửa (`attrs.name_source = 'owner'`) hay tên khác mã thô (chỉ nâng khi tên hiện tại rỗng hoặc là mã/SĐT/dãy số/địa
    chỉ kiểu `...@...` — điều kiện kiểm lại trong SQL, an toàn khi hai tin cùng lúc). Chạm `updated_at` ⇒ lượt dò
    trùng danh tính xét lại hồ sơ vì tên đổi.

    LƯU Ý cho người viết route đổi tên người sau này: hiện KHÔNG đường nào ghi `name_source = 'owner'` (chưa có
    route đổi tên người), nên chốt "không đè tên Sếp" chỉ dựa vào điều kiện "tên trông như mã thô". Route đổi tên PHẢI
    đặt `attrs.name_source = 'owner'` cùng lúc đổi `display_name` — `test_ten_hien_thi_v016x` có lưới dò, thêm UPDATE
    mới ghi `display_name` ở file khác sẽ đỏ để nhắc việc này."""
    await db.execute(text("""
        UPDATE core.persons p SET display_name = :n, updated_at = now(),
               attrs = p.attrs || '{"name_source": "channel"}'::jsonb
        WHERE p.id = :p AND COALESCE(p.attrs->>'name_source', '') <> 'owner'
          AND (btrim(p.display_name) = '' OR p.display_name ~ '^[+0-9 ]+$'
               OR (p.display_name LIKE '%@%' AND p.display_name !~ ' ')
               OR EXISTS (SELECT 1 FROM core.person_identities i WHERE i.person_id = p.id
                          AND (i.external_id = p.display_name OR i.phone_e164 = p.display_name)))"""),
                     {"n": name, "p": person_id})


def _e164(phone: str | None) -> str | None:
    if not phone:
        return None
    digits = "".join(ch for ch in phone if ch.isdigit())
    if not digits:
        return None
    if phone.strip().startswith("+"):
        return "+" + digits
    if digits.startswith("84"):
        return "+" + digits
    if digits.startswith("0"):
        return "+84" + digits[1:]
    return "+" + digits


async def _member(db: AsyncSession, group_id: uuid.UUID, person_id: uuid.UUID, role: str | None = None) -> None:
    """Ghi thành viên nhóm. `role` ('admin'/'member') chỉ có khi nền tảng cho biết; None ⇒ giữ vai trò cũ.
    Người từng rời mà xuất hiện lại ⇒ xoá `left_at`."""
    await db.execute(text("""INSERT INTO core.group_members (group_id, person_id, role, joined_at)
                             VALUES (:g, :p, :r, now())
                             ON CONFLICT (group_id, person_id) DO UPDATE
                               SET left_at = NULL, role = COALESCE(EXCLUDED.role, core.group_members.role)
                             WHERE core.group_members.left_at IS NOT NULL
                                OR (EXCLUDED.role IS NOT NULL
                                    AND core.group_members.role IS DISTINCT FROM EXCLUDED.role)"""),
                     {"g": group_id, "p": person_id, "r": role})


async def ingest_message(db: AsyncSession, org_id: uuid.UUID, p: dict[str, Any]) -> uuid.UUID | None:
    """Ghi một tin vào Kho thô. Trả id bản ghi mới, hoặc None nếu bỏ (trùng, nhóm chưa bật, kênh lạ)."""
    channel = await channel_row(db, org_id, str(p.get("channel", "")))
    msg_id = str(p.get("external_msg_id") or "")
    sender = str(p.get("sender_external_id") or "")
    if channel is None or not msg_id or not sender:
        return None
    group = None
    dm = "off"
    if p.get("external_group_id"):
        group = await upsert_group(db, org_id, channel, str(p["external_group_id"]), p.get("group_name"))
        if group.listen_mode not in LISTENING_MODES:
            return None
        if group.left_at is not None:
            # Nhận được tin của nhóm ⇒ chắc chắn còn trong nhóm: xoá cờ «đã rời» (do quét danh bạ đủ nhưng sót nhóm).
            await db.execute(text("UPDATE core.groups SET left_at = NULL WHERE id = :g AND left_at IS NOT NULL"),
                             {"g": group.id})
            await lev.record(db, org_id, "group_rejoined", channel_id=channel.id, channel_type=channel.type,
                             group_id=group.id)
    else:
        # v0.1.65: tin 1-1 mặc định LƯU IM LẶNG (silent); chỉ "off" mới bỏ.
        dm = direct_mode(await org_settings(db, org_id), channel.type)
        if dm == "off":
            return None
    # Người nhắn riêng LẦN ĐẦU (chưa có định danh nào trên kênh) ⇒ sự kiện `direct_new` (ghi sau khi tin vào kho).
    new_direct = (group is None and p.get("direction") != "outbound" and (await db.execute(
        text("SELECT 1 FROM core.person_identities WHERE channel_id = :c AND external_id = :x"),
        {"c": channel.id, "x": sender})).first() is None)
    identity = await upsert_identity(db, org_id, channel, sender, p.get("sender_name"), p.get("sender_phone"))
    if p.get("direction") == "outbound":
        # v0.1.71 (P3-12): người gửi TIN ĐI là chính tài khoản kênh của Sếp / bot — không phải «Người lạ có tín hiệu».
        # Chỉ đổi khi vẫn là giá trị mặc định do hệ thống tự tạo (Sếp đã tự chọn «Liên quan Sếp» thì giữ nguyên).
        await db.execute(text("""UPDATE core.persons SET relation_to_owner = 'staff', updated_at = now()
                                 WHERE id = :p AND relation_to_owner = 'stranger' AND attrs->>'auto' = 'true'"""),
                         {"p": identity.person_id})
    if group is not None:
        await _member(db, group.id, identity.person_id)

    event_id, received_at = uuid7(), datetime.now(UTC)
    fresh = (await db.execute(text("""
        INSERT INTO raw.event_keys (channel_id, external_msg_id, event_id, received_at) VALUES (:c, :m, :e, :r)
        ON CONFLICT DO NOTHING RETURNING event_id"""),
        {"c": channel.id, "m": msg_id, "e": event_id, "r": received_at})).scalar_one_or_none()
    if fresh is None:
        return None  # bridge gửi lại / tin trùng
    body = p.get("body_text")
    kind = p.get("kind") if p.get("kind") in KINDS else "other"
    payload: dict[str, Any] = p["payload"] if isinstance(p.get("payload"), dict) else {"value": p.get("payload")}
    replies = bool(p.get("replies_to_self"))
    if replies:
        payload = {**payload, "replies_to_self": True}    # v0.1.72: trả lời (quote) tin của chính bot
    content_hash = hashlib.sha256(orjson.dumps([channel.type, msg_id, sender, body or "", kind])).digest()
    direction = "outbound" if p.get("direction") == "outbound" else "inbound"
    mentions = bool(p.get("mentions_self"))
    await db.execute(text("""
        INSERT INTO raw.events (id, org_id, received_at, occurred_at, channel_id, group_id, sender_identity_id,
                                external_msg_id, kind, body_text, payload, content_hash, direction, mentions_agent,
                                listen_mode)
        VALUES (:id, :o, :r, :occ, :c, :g, :s, :m, :k, :b, CAST(:pl AS jsonb), :h, :d, :men, :lm)"""),
        {"id": event_id, "o": org_id, "r": received_at, "occ": _ts(p.get("occurred_at")), "c": channel.id,
         "g": group.id if group else None, "s": identity.id, "m": msg_id, "k": kind, "b": body,
         "pl": orjson.dumps(payload, default=str).decode(), "h": content_hash, "d": direction, "men": mentions,
         # v0.1.68: chế độ nghe LÚC tin vào kho (nhóm: chế độ của nhóm; tin 1-1: silent | reply) cho Nhật ký lắng nghe.
         "lm": group.listen_mode if group is not None else dm})
    if group is not None and direction == "inbound":
        await _owner_triggers(db, org_id, channel, group, identity, event_id, sender, p, mentions, replies)
    if new_direct:
        await lev.record(db, org_id, "direct_new", channel_id=channel.id, channel_type=channel.type,
                         person_id=identity.person_id, detail={"raw_event_id": str(event_id)})
    # Đường nhanh: tin tag agent và tin 1-1 (ARCHITECTURE §4.3, quyết định Q3). Tin của chính mình không cần.
    #   v0.1.65: tin 1-1 im lặng (silent) không cần nhanh — chỉ "reply" mới đi đường nhanh cho agent trực.
    #   Người được gán BOT (B8) thì tin 1-1 của họ cũng đi nhanh: BOT phụ trách đích danh dù kênh để im lặng.
    direct_fast = group is None and (dm == "reply" or await rsvc.person_has_bot(db, identity.person_id))
    #   QD 17/10: nhóm "Lắng nghe im lặng" KHÔNG BAO GIỜ trả lời, kể cả khi được tag ⇒ tin tag ở đó không cần đi nhanh.
    tag_fast = mentions and not (group is not None and group.listen_mode == "silent")
    fast = direction == "inbound" and (tag_fast or direct_fast)
    await db.execute(text("""
        INSERT INTO refinery.event_state (event_id, event_received_at, org_id, state, fast)
        VALUES (:e, :r, :o, 'pending', :f)"""), {"e": event_id, "r": received_at, "o": org_id, "f": fast})
    await db.execute(text("SELECT pg_notify('raw_ingested', :p)"),
                     {"p": f"{org_id}|{'fast' if fast else 'normal'}|{event_id}"})
    return event_id


async def _owner_triggers(db: AsyncSession, org_id: uuid.UUID, channel: Any, group: Any, identity: Any,
                          event_id: uuid.UUID, sender: str, p: dict[str, Any], mentions: bool, replies: bool) -> None:
    """v0.1.72: tin trong nhóm có phải lệnh gọi của Sếp / câu hỏi cho chế độ hỗ trợ không (chỉ tài khoản bot đã ghép).
    Chỉ PHÁT SỰ KIỆN cho consumer (sau commit) — không gửi gì ở đây. Nhận diện Owner theo ID, không theo tên."""
    oi = await owner_ids(db, org_id, channel.type)
    if not oi.enabled:
        return
    if is_owner(oi, sender, p.get("sender_alt_id")):
        await db.execute(text("""UPDATE core.persons SET attrs = attrs || '{"is_owner": true}'::jsonb
                                 WHERE id = :p AND COALESCE(attrs->>'is_owner', '') <> 'true'"""),
                         {"p": identity.person_id})
        if mentions and group.listen_mode in LISTENING_MODES:
            owner_inbound.stash(db, "call", {"channel": channel.type, "raw_event_id": str(event_id),
                                            "group_id": str(group.id)})
        return
    if (mentions or replies) and (await db.execute(text("""
            SELECT 1 FROM ops.group_support WHERE group_id = :g AND disabled_at IS NULL
              AND (until_at IS NULL OR until_at > now())"""), {"g": group.id})).first() is not None:
        owner_inbound.stash(db, "support_ask", {"channel": channel.type, "raw_event_id": str(event_id),
                                               "group_id": str(group.id), "sender_person_id": str(identity.person_id)})


# ─── Tập nhóm được nghe (bridge kiểm trước khi đẩy tin) ─────────────────────

async def sync_listen_sets(db: AsyncSession, redis: Redis, org_id: uuid.UUID) -> None:
    rows = (await db.execute(text("""
        SELECT c.type, g.external_id, g.listen_mode FROM core.channels c
        LEFT JOIN core.groups g ON g.channel_id = c.id WHERE c.org_id = :o"""), {"o": org_id})).all()
    sets: dict[str, list[str]] = {}
    for r in rows:
        sets.setdefault(r.type, [])
        if r.external_id and r.listen_mode in LISTENING_MODES:
            sets[r.type].append(r.external_id)
    settings = await org_settings(db, org_id)
    pipe = redis.pipeline(transaction=True)
    for ch, ids in sets.items():
        key = f"gh:bridge:listen:{ch}"
        pipe.delete(key)
        if ids:
            pipe.sadd(key, *ids)
        pipe.set(f"gh:bridge:listen_direct:{ch}", "0" if direct_mode(settings, ch) == "off" else "1")
        # Cờ «nhóm mới» của kênh: bridge chỉ gắn nội dung tin đầu (`first_message`) vào `group.discovered` khi =
        # silent — ask/off thì lõi không lưu nên nội dung không được phép nằm lại trong stream (review v0.1.65 P2-1).
        # v0.1.72: tài khoản bot ⇒ `owner_only` (không phải `silent`) ⇒ bridge không gắn `first_message` — nhóm
        # lạ chưa rõ ai
        # thêm thì không lưu nội dung.
        pipe.set(f"gh:bridge:new_group_mode:{ch}",
                 "owner_only" if await is_bot_account(db, org_id, ch) else new_group_mode(settings, ch))
    await pipe.execute()
    await sync_owner_sets(db, redis, org_id)


# ─── Trạng thái phiên kênh ─────────────────────────────────────────────────

async def _session(db: AsyncSession, session_id: Any) -> Any:
    try:
        sid = uuid.UUID(str(session_id))
    except ValueError:
        return None
    return (await db.execute(text("""
        SELECT s.id, s.channel_id, s.state, s.account_label, s.started_at, s.ended_at, c.type, c.org_id
        FROM core.channel_sessions s JOIN core.channels c ON c.id = s.channel_id WHERE s.id = :i"""),
        {"i": sid})).one_or_none()


async def _status_ws(redis: Redis, org_id: Any, type: str, state: str, account_label: str | None,
                     session_id: Any, scanned: bool = False, error: str | None = None) -> None:
    await realtime.publish(redis, "channel.status", {"type": type, "state": state, "account_label": account_label,
                                                     "session_id": str(session_id), "scanned": scanned,
                                                     "error": error}, org_id=org_id)


async def publish_resume(bus: EventBus, org_id: uuid.UUID, type: str, session_id: Any, credential_enc: Any,
                         actor: str = "system:ingest") -> None:
    """Ra lệnh bridge đăng nhập lại một phiên bằng credential đã lưu (giải mã ở lõi, mã hoá lại theo khoá vận chuyển)
    — dùng chung cho bridge.hello và nút "Nối lại bằng phiên đã lưu"."""
    wire = crypto.transport_encrypt(crypto.decrypt(bytes(credential_enc), b"channel_session"), f"{type}:{session_id}")
    await bus.publish(BRIDGE_CONTROL, "session.login", {"channel": type, "session_id": str(session_id),
                                                        "credential": wire}, actor=actor, org_id=org_id)


async def close_sessions(bus: EventBus, org_id: uuid.UUID, type: str, session_ids: list[Any],
                         actor: str = "system:ingest") -> None:
    """v0.1.62 (F067): bảo bridge ĐÓNG kết nối trong RAM của các phiên cũ (không đăng xuất nền tảng — phiên mới có thể
    cùng tài khoản) để không còn kết nối ma chạy ngầm cạnh phiên mới."""
    for sid in session_ids:
        await bus.publish(BRIDGE_CONTROL, "session.close", {"channel": type, "session_id": str(sid)}, actor=actor,
                          org_id=org_id)


async def _reset_new_group_mode_if_account_changed(db: AsyncSession, redis: Redis, org_id: uuid.UUID, s: Any,
                                                   account_id: str | None) -> None:
    """Quét QR một tài khoản KHÁC tài khoản trước đó của kênh ⇒ «Nhóm mới» về lại MẶC ĐỊNH (`silent`: Lắng nghe im lặng,
    QD 17/10): lựa chọn Hỏi Sếp / Không nghe là của tài khoản cũ, không mang sang tài khoản mới (review v0.1.65 P3). Lần
    nối đầu tiên (chưa có tài khoản nào trước) và nối lại CÙNG tài khoản thì giữ nguyên."""
    if not account_id:
        return
    prev = (await db.execute(text("""
        SELECT external_account FROM core.channel_sessions
        WHERE channel_id = :c AND id <> :i AND external_account IS NOT NULL
        ORDER BY COALESCE(started_at, qr_issued_at) DESC NULLS LAST LIMIT 1"""),
        {"c": s.channel_id, "i": s.id})).scalar_one_or_none()
    if prev is None or prev == account_id:
        return
    before = new_group_mode(await org_settings(db, org_id), s.type)
    await set_channel_setting(db, org_id, "new_group_mode", s.type, DEFAULT_NEW_GROUP_MODE)
    await set_channel_setting(db, org_id, "new_group_asked", s.type, False)
    await set_channel_setting(db, org_id, NEW_SILENT_KEY, s.type, 0)
    if before != DEFAULT_NEW_GROUP_MODE:
        await actionlog.record(db, org_id=org_id, actor_type="system", actor_id=f"bridge:{s.type}",
                               action="channel.new_group_mode_reset", target_type="channel", target_id=s.type,
                               detail={"from": before, "reason": "account_changed"})
        await sync_listen_sets(db, redis, org_id)   # cờ gh:bridge:new_group_mode:<kênh> của bridge theo mặc định mới
        # Báo Sếp (chuông của Owner), không chỉ Nhật ký: nhóm mới của tài khoản này sẽ được LƯU tin chứ không còn
        # «Hỏi Sếp» / «Không nghe» như Sếp đã chọn cho tài khoản cũ.
        from gh import notifications

        name = CHANNEL_NAME.get(s.type, s.type)
        was = {"ask": "Hỏi Sếp", "off": "Không nghe"}.get(before, before)
        await notifications.notify(
            db, org_id, await notifications.owner_ids(db, org_id), kind="channel.new_group_mode_reset",
            title=f"Kênh {name}: nhóm mới về Lắng nghe im lặng",
            body=(f"Sếp vừa quét tài khoản {name} khác nên cách xử lý nhóm mới (trước: «{was}») về mặc định «Lắng nghe "
                  "im lặng»: nhóm mới sẽ được lưu tin, không bao giờ trả lời. Muốn đổi, vào Kết nối › thẻ kênh."),
            link="/connections", redis=redis)


async def _on_send_result(db: AsyncSession, redis: Redis, org_id: uuid.UUID, p: dict[str, Any]) -> None:
    if p.get("auto_send_id"):
        # v0.1.72: kết quả của tin TỰ GỬI (cửa `send_auto`) — cập nhật sổ `ops.auto_sends`, không phải bản nháp.
        from gh.owner_channel import autosend

        await autosend.on_send_result(db, org_id, p)
        return
    await actionlog.record(db, org_id=org_id, actor_type="system", actor_id=f"bridge:{p.get('channel') or 'bridge'}",
                           action="message.sent" if p.get("ok") else "message.send_failed",
                           target_type="draft", target_id=str(p.get("draft_id") or ""),
                           result="ok" if p.get("ok") else "failed",
                           detail={"error": p.get("error"), "external_msg_id": p.get("external_msg_id")})
    from gh.biz.core import drafts  # bản nháp của Bàn làm việc: sent / failed

    await drafts.on_send_result(db, org_id, p, redis)


async def handle_status(db: AsyncSession, redis: Redis, bus: EventBus, org_id: uuid.UUID, type: str,
                        p: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """Xử lý một sự kiện gh.bridge.status. Trả các sự kiện WebSocket cần phát sau commit."""
    out: list[tuple[str, dict[str, Any]]] = []
    if type == "bridge.hello":
        await sync_listen_sets(db, redis, org_id)
        # v0.1.62 (F046): nối lại cả phiên 'error' còn credential (mất mạng
        # / bị mở nơi khác), không chỉ 'active' — miễn là
        # chưa có phiên nào MỚI hơn của kênh (không "sống lại" phiên cũ sau khi Sếp đã quét QR mới hoặc đăng xuất).
        # Gói K (P1-1): phiên bị ngắt vì tài khoản mở ở nơi khác (CONNECTION_REPLACED) KHÔNG tự nối lại khi bridge
        # khởi động — đăng nhập lại sẽ đá Zalo Web/WhatsApp Web của Sếp; chỉ nối khi Sếp bấm "Nối lại".
        rows = (await db.execute(text("""
            SELECT s.id, s.credential_enc, c.type
            FROM core.channel_sessions s JOIN core.channels c ON c.id = s.channel_id
            WHERE c.org_id = :o AND s.credential_enc IS NOT NULL AND (
                (s.state = 'active' AND s.ended_at IS NULL)
                OR (s.state = 'error' AND COALESCE(s.meta->>'error', '') <> 'CONNECTION_REPLACED'
                    AND NOT EXISTS (
                    SELECT 1 FROM core.channel_sessions n WHERE n.channel_id = s.channel_id AND n.id <> s.id
                      AND COALESCE(n.started_at, n.qr_issued_at) > COALESCE(s.started_at, s.qr_issued_at))))"""),
            {"o": org_id})).all()
        for r in rows:
            await publish_resume(bus, org_id, r.type, r.id, r.credential_enc)
        return out
    if type == "send.result":
        # v0.1.73: kết quả gửi KHÔNG mang `session_id` (giao thức bridge) ⇒ xử lý
        # TRƯỚC bước tra phiên; trước đây thoát sớm ở đó nên `ops.auto_sends` kẹt «issued» và bản nháp kẹt «approved».
        await _on_send_result(db, redis, org_id, p)
        return out
    s = await _session(db, p.get("session_id"))
    if s is None or s.org_id != org_id:
        return out
    if type == "session.qr":
        await redis.set(f"gh:channel:qr:{s.id}", orjson.dumps({"image": p.get("image"),
                                                                "expires_at": p.get("expires_at"),
                                                                "scanned": False}),
                        ex=qr_ttl_s(p.get("expires_at")))
        await db.execute(text("""UPDATE core.channel_sessions SET state = 'pending_qr', qr_issued_at = now()
                                 WHERE id = :i AND ended_at IS NULL"""), {"i": s.id})
        out.append(("channel.qr", {"type": s.type, "session_id": str(s.id), "image": p.get("image"),
                                   "expires_at": p.get("expires_at")}))
    elif type == "session.scanned":
        raw = await redis.get(f"gh:channel:qr:{s.id}")
        if raw:
            qr = orjson.loads(raw)
            qr["scanned"] = True
            await redis.set(f"gh:channel:qr:{s.id}", orjson.dumps(qr), ex=qr_ttl_s(qr.get("expires_at")))
        out.append(("channel.status", {"type": s.type, "state": "pending_qr", "account_label": s.account_label,
                                       "session_id": str(s.id), "scanned": True, "error": None}))
    elif type == "session.active":
        if s.state == "logged_out":
            # Review lần 2 v0.1.62: Sếp đã đăng xuất phiên này ở Console — sự kiện `session.active` đến muộn của bridge
            # không được làm phiên sống lại (cũng không được đóng phiên đang chạy khác của kênh).
            return out
        account = p.get("account") or {}
        cred = _reencrypt(p.get("credential"), s)
        await _reset_new_group_mode_if_account_changed(db, redis, org_id, s, str(account.get("id") or "") or None)
        # Một kênh chỉ một phiên hoạt động: phiên cũ kết thúc
        # — và (v0.1.62, F067) bridge được bảo đóng kết nối của chúng.
        old = (await db.execute(text("""UPDATE core.channel_sessions SET state = 'logged_out', ended_at = now()
                                        WHERE channel_id = :c AND id <> :i AND ended_at IS NULL RETURNING id"""),
                                {"c": s.channel_id, "i": s.id})).scalars().all()
        await close_sessions(bus, org_id, s.type, list(old))
        label = s.account_label or account.get("name") or s.type
        # v0.1.62 (F068): nối lại CÙNG phiên (bridge khởi động lại, mất mạng) giữ `started_at` gốc — "Phiên mở" không bị
        # đặt lại; phiên vừa nối lại thì `ended_at` về NULL (phiên 'error' được nối lại bằng phiên đã lưu).
        await db.execute(text("""
            UPDATE core.channel_sessions SET state = 'active', started_at = COALESCE(started_at, now()),
                   ended_at = NULL,
                   credential_enc = COALESCE(:c, credential_enc),
                   external_account = :x, account_label = :l, last_heartbeat_at = now(),
                   meta = meta || CAST(:m AS jsonb) WHERE id = :i AND state <> 'logged_out'"""),
            {"c": cred, "x": str(account.get("id") or "") or None, "l": label, "i": s.id,
             "m": orjson.dumps({"account_name": account.get("name")}).decode()})
        await redis.delete(f"gh:channel:qr:{s.id}")
        # v0.1.72: phiên mới thay phiên đã ghép kênh quản trị ⇒ cùng tài khoản bot thì giữ ghép, khác tài khoản
        # thì huỷ ghép.
        await owner_inbound.on_session_active(db, org_id, s.type, s.id, str(account.get("id") or "") or None)
        await sync_owner_sets(db, redis, org_id)
        from gh import health  # v0.1.36 (F-6a): kênh đã nối lại ⇒ đóng sự cố "kênh rớt"

        await health.clear(db, org_id, f"channel.down:{s.type}", redis=redis)
        await actionlog.record(db, org_id=org_id, actor_type="system", actor_id=f"bridge:{s.type}",
                               action="channel.session_active", target_type="channel_session",
                               target_id=str(s.id), target_label=label)
        await lev.record(db, org_id, "channel_connected", channel_id=s.channel_id, channel_type=s.type,
                         detail={"account_label": label, "resumed": s.state in ("active", "error")})
        out.append(("channel.status", {"type": s.type, "state": "active", "account_label": label,
                                       "session_id": str(s.id), "scanned": True, "error": None}))
    elif type == "session.reconnecting":
        # v0.1.62 (F046): bridge mất mạng/máy chủ nền tảng, ĐANG tự
        # thử nối lại (credential còn nguyên) — không phải hết hạn.
        if s.state in ("active", "error"):
            error = str(p.get("error") or "NETWORK_UNAVAILABLE")[:64]
            was_live = s.state == "active" and s.ended_at is None
            await db.execute(text("""
                UPDATE core.channel_sessions SET state = 'error', ended_at = NULL,
                       meta = meta || CAST(:m AS jsonb) WHERE id = :i"""),
                {"i": s.id, "m": orjson.dumps({"error": error}).decode()})
            if was_live:
                from gh import health

                name = CHANNEL_NAME.get(s.type, s.type)
                cause, todo = channel_down_text("error", error)
                await health.raise_once(
                    db, org_id, key=f"channel.down:{s.type}", kind="channel.down", severity="bad",
                    title=f"Kênh {name} đã ngắt kết nối", body=f"{s.account_label or name}: {cause} — {todo}",
                    link="/connections", fingerprint=f"error:{error}", redis=redis)
                await actionlog.record(db, org_id=org_id, actor_type="system", actor_id=f"bridge:{s.type}",
                                       action="channel.session_reconnecting", target_type="channel_session",
                                       target_id=str(s.id), target_label=s.account_label, result="failed",
                                       detail={"error": error})
                await lev.record(db, org_id, "channel_error", channel_id=s.channel_id, channel_type=s.type,
                                 detail={"error": error})
            out.append(("channel.status", {"type": s.type, "state": "error", "account_label": s.account_label,
                                           "session_id": str(s.id), "scanned": False, "error": error}))
    elif type == "session.credential":
        cred = _reencrypt(p.get("credential"), s)
        if cred is not None:
            await db.execute(text("UPDATE core.channel_sessions SET credential_enc = :c WHERE id = :i"),
                             {"c": cred, "i": s.id})
    elif type == "session.ended":
        reason = p.get("reason") if p.get("reason") in ("expired", "logged_out", "error") else "error"
        # v0.1.36 (F-6a): chỉ phiên ĐANG CHẠY rớt mới đáng chuông. Phiên chờ QR hết hạn (Sếp chưa quét) và phiên
        # Console đã tự đăng xuất (routes.channel_logout đặt ended_at trước khi bridge báo lại) thì không.
        was_live = s.state == "active" and s.ended_at is None
        await db.execute(text("""
            UPDATE core.channel_sessions SET state = :st, ended_at = COALESCE(ended_at, now()),
                   credential_enc = CASE WHEN :st IN ('logged_out', 'expired') THEN NULL ELSE credential_enc END,
                   meta = meta || CAST(:m AS jsonb) WHERE id = :i"""),
            {"st": reason, "i": s.id, "m": orjson.dumps({"error": p.get("error")}).decode()})
        await redis.delete(f"gh:channel:qr:{s.id}")
        if was_live and reason in CHANNEL_DOWN_REASONS:
            from gh import health

            name = CHANNEL_NAME.get(s.type, s.type)
            cause, todo = channel_down_text(reason, p.get("error"))
            await health.raise_once(
                db, org_id, key=f"channel.down:{s.type}", kind="channel.down", severity="bad",
                title=f"Kênh {name} đã ngắt kết nối", body=f"{s.account_label or name}: {cause} — {todo}",
                link="/connections", fingerprint=f"{reason}:{p.get('error')}", redis=redis)
        await actionlog.record(db, org_id=org_id, actor_type="system", actor_id=f"bridge:{s.type}",
                               action="channel.session_ended", target_type="channel_session", target_id=str(s.id),
                               target_label=s.account_label, result="ok" if reason == "logged_out" else "failed",
                               detail={"reason": reason, "error": p.get("error")})
        # Chỉ phiên từng nối được (Console đã đăng xuất / QR hết hạn chưa quét thì không ghi).
        if s.state in ("active", "error"):
            await lev.record(db, org_id, "channel_ended", channel_id=s.channel_id, channel_type=s.type,
                             detail={"reason": reason, "error": p.get("error")})
        out.append(("channel.status", {"type": s.type, "state": reason, "account_label": s.account_label,
                                       "session_id": str(s.id), "scanned": False, "error": p.get("error")}))
    elif type == "heartbeat":
        await db.execute(text("""UPDATE core.channel_sessions SET last_heartbeat_at = now(),
                                 meta = meta || CAST(:m AS jsonb) WHERE id = :i AND ended_at IS NULL"""),
                         {"i": s.id, "m": orjson.dumps({"latency_ms": p.get("latency_ms"),
                                                        "queued": p.get("queued", 0)}).decode()})
        now = datetime.now(UTC)
        key = f"gh:uptime:{s.type}:{now:%Y%m%d}"
        await redis.setbit(key, now.hour * 60 + now.minute, 1)
        await redis.expire(key, 3 * 86400)
    return out


def _reencrypt(transport: Any, s: Any) -> bytes | None:
    if not transport:
        return None
    try:
        plain = crypto.transport_decrypt(str(transport), f"{s.type}:{s.id}")
    except Exception:  # noqa: BLE001 — khoá bridge sai / dữ liệu hỏng: không lưu
        log.error("Không giải mã được phiên kênh %s từ bridge", s.id)
        return None
    return crypto.encrypt(plain, b"channel_session")


# ─── Danh bạ nhóm ───────────────────────────────────────────────────────────

async def handle_directory(db: AsyncSession, redis: Redis, org_id: uuid.UUID, p: dict[str, Any]) -> int:
    """Danh bạ đầy đủ từ bridge. Thu thành viên của MỌI nhóm kể cả "Không nghe" (Sếp chốt Q5=2).
    v0.1.65 (F058): khi bridge báo danh sách ĐỦ (`members_complete` / `groups_complete`) thì người vắng đặt `left_at`
    và nhóm vắng đặt cờ đã rời; xuất hiện lại thì xoá. Vai trò admin (`members[].role`) ghi nếu nền tảng cho biết."""
    channel = await channel_row(db, org_id, str(p.get("channel", "")))
    if channel is None:
        return 0
    n = 0
    seen: list[str] = []
    # Lần quét ĐẦU của kênh (chưa có nhóm nào) là nhập danh bạ hàng loạt: không ghi từng nhóm mới/thành viên vào
    # Nhật ký lắng nghe — chỉ một dòng tóm tắt `groups_synced`.
    first_sync = (await db.execute(text("SELECT 1 FROM core.groups WHERE channel_id = :c LIMIT 1"),
                                   {"c": channel.id})).first() is None
    # Lần quét KHÔNG phải đầu tiên cũng có thể thấy hàng trăm nhóm mới cùng lúc (đăng nhập tài khoản khác, bridge
    # vừa báo một nhóm lạ trước lần quét đầu): chỉ ghi tối đa MAX_GROUP_NEW_EVENTS dòng «nhóm mới» (và phát
    # WebSocket tương ứng), phần còn lại gom vào MỘT dòng tóm tắt `groups_synced` — như left/rejoined (≤ 50).
    new_events = suppressed = 0
    for g in p.get("groups") or []:
        if not g.get("external_id"):
            continue
        quiet = first_sync or new_events >= lev.MAX_GROUP_NEW_EVENTS
        row, created = await upsert_group_ex(db, org_id, channel, str(g["external_id"]), g.get("name"),
                                             g.get("member_count"), quiet=quiet)
        if created and not first_sync:
            if quiet:
                suppressed += 1
            else:
                new_events += 1
        n += 1
        seen.append(str(g["external_id"]))
        members = (g.get("members") or [])[:5000]
        before: set[uuid.UUID] = set()
        if not created and not first_sync:
            before = set((await db.execute(text("""SELECT person_id FROM core.group_members
                                                   WHERE group_id = :g AND left_at IS NULL"""),
                                           {"g": row.id})).scalars().all())
        person_ids: list[uuid.UUID] = []
        for m in members:
            if not m.get("external_id"):
                continue
            ident = await upsert_identity(db, org_id, channel, str(m["external_id"]), m.get("name"), m.get("phone"))
            role = m.get("role") if m.get("role") in ("admin", "member") else None
            await _member(db, row.id, ident.person_id, role)
            person_ids.append(ident.person_id)
        gone: list[uuid.UUID] = []
        if g.get("members_complete") is True and person_ids and len(g.get("members") or []) <= 5000:
            gone = list((await db.execute(text("""UPDATE core.group_members SET left_at = now()
                                     WHERE group_id = :g AND left_at IS NULL AND person_id <> ALL(:keep)
                                     RETURNING person_id"""), {"g": row.id, "keep": person_ids})).scalars().all())
        # Thành viên vào/rời của nhóm ĐANG nghe (nhóm "Không nghe" thu thành viên nhưng không báo từng người).
        joined = [x for x in dict.fromkeys(person_ids) if x not in before] if before else []
        if (joined or gone) and row.listen_mode in LISTENING_MODES:
            await lev.record(db, org_id, "members_changed", channel_id=channel.id, channel_type=channel.type,
                             group_id=row.id, detail={"joined": await _named(db, joined),
                                                      "left": await _named(db, gone)})
    if seen:
        back = (await db.execute(text("UPDATE core.groups SET left_at = NULL WHERE channel_id = :c AND "
                                      "external_id = ANY(:s) AND left_at IS NOT NULL RETURNING id"),
                                 {"c": channel.id, "s": seen})).scalars().all()
        for gid in back[:50]:
            await lev.record(db, org_id, "group_rejoined", channel_id=channel.id, channel_type=channel.type,
                             group_id=gid)
        if p.get("groups_complete") is True:
            left = (await db.execute(text("""UPDATE core.groups SET left_at = now()
                                     WHERE channel_id = :c AND left_at IS NULL AND external_id <> ALL(:s)
                                     RETURNING id"""), {"c": channel.id, "s": seen})).scalars().all()
            for gid in left[:50]:
                await lev.record(db, org_id, "group_left", channel_id=channel.id, channel_type=channel.type,
                                 group_id=gid)
    if first_sync and n:
        await lev.record(db, org_id, "groups_synced", channel_id=channel.id, channel_type=channel.type,
                         detail={"groups": n})
    elif suppressed:
        await lev.record(db, org_id, "groups_synced", channel_id=channel.id, channel_type=channel.type,
                         detail={"groups": suppressed, "extra": True})
    await sync_listen_sets(db, redis, org_id)
    return n


async def _named(db: AsyncSession, person_ids: list[uuid.UUID]) -> dict[str, Any]:
    """{count, names[≤5]} của một nhóm người — tên để viết câu sự kiện (không lưu nội dung tin)."""
    names: list[str] = []
    if person_ids:
        names = [r for r in (await db.execute(text("SELECT display_name FROM core.persons WHERE id = ANY(:i) "
                                                   "ORDER BY display_name LIMIT :n"),
                                              {"i": person_ids[:lev.MAX_NAMES * 4], "n": lev.MAX_NAMES})).scalars()]
    return {"count": len(person_ids), "names": names}


async def handle_group_discovered(db: AsyncSession, redis: Redis, org_id: uuid.UUID, p: dict[str, Any]) -> Any:
    """`group.discovered{group_id, name, member_count}`: bridge vừa thấy tin từ một nhóm chưa có trong danh bạ.
    Tạo nhóm ở «Lắng nghe im lặng» (`silent`, mặc định theo QD 17/10) — hoặc `pending` (Console hỏi Sếp) / `off` nếu Sếp
    đã đặt `new_group_mode` của kênh là `ask` / `off` (F1d); nhóm đã có thì không đổi chế độ.
    Trả `(nhóm, mới?)` hoặc None nếu kênh/nhóm không hợp lệ."""
    channel = await channel_row(db, org_id, str(p.get("channel", "")))
    gid = str(p.get("group_id") or "")
    if channel is None or not gid:
        return None
    existed = (await db.execute(text("SELECT 1 FROM core.groups WHERE channel_id = :c AND external_id = :x"),
                                {"c": channel.id, "x": gid})).first() is not None
    count = p.get("member_count")
    row = await upsert_group(db, org_id, channel, gid, p.get("name"), count if isinstance(count, int) else None)
    if (await db.execute(text("UPDATE core.groups SET left_at = NULL WHERE id = :i AND left_at IS NOT NULL "
                              "RETURNING id"), {"i": row.id})).first() is not None:
        await lev.record(db, org_id, "group_rejoined", channel_id=channel.id, channel_type=channel.type,
                         group_id=row.id)
    await sync_listen_sets(db, redis, org_id)
    return row, not existed


async def ingest_discovered_message(db: AsyncSession, org_id: uuid.UUID, p: dict[str, Any]) -> uuid.UUID | None:
    """Tin ĐẦU TIÊN của nhóm lạ (`group.discovered.first_message`, QD 17/10 «thu hết tình báo»): bridge không đẩy tin
    đó lên luồng inbound vì nhóm chưa có trong tập nghe, nên đi kèm sự kiện này. Gọi SAU `handle_group_discovered`.
    Dùng chính `ingest_message` ⇒ chỉ lưu khi nhóm đang ở chế độ nghe (`silent` mặc định); `pending` / `off` thì bỏ,
    không lưu nội dung, không tạo người gửi; `raw.event_keys` chặn lưu trùng (bus giao lại, tin đã vào đường thường).
    Tin không thuộc đúng nhóm được báo (hoặc không phải tin nhóm) bị bỏ."""
    first = p.get("first_message")
    gid = str(p.get("group_id") or "")
    if not isinstance(first, dict) or not gid or str(first.get("external_group_id") or "") != gid:
        return None
    return await ingest_message(db, org_id, {**first, "channel": p.get("channel")})


# ─── Consumer ──────────────────────────────────────────────────────────────

class Ingest:
    """Ba consumer chạy trong api: inbound → Kho thô, status → phiên kênh, directory → nhóm."""

    GROUP = "ingest"

    def __init__(self, bus: EventBus, redis: Redis, org_id: uuid.UUID, sessionmaker: Any):
        self.bus, self.redis, self.org_id, self.sm = bus, redis, org_id, sessionmaker

    async def on_inbound(self, event: Any) -> None:
        if event.type in ("owner.dm", "owner.pair"):
            return await self._on_owner_inbound(event)
        if event.type != "message":
            return
        async with self.sm() as db:
            event_id = await ingest_message(db, self.org_id, event.payload)
            await db.commit()
            pending = lev.take_pending(db)
            owner_pending = owner_inbound.take_pending(db)
            row = await fetch_raw(db, event_id) if event_id is not None else None
        await lev.publish_pending(self.sm, self.redis, self.org_id, pending)
        await owner_inbound.publish_pending(self.bus, self.org_id, owner_pending)
        if row is not None:
            await realtime.publish(self.redis, "raw.new", raw_item(row), org_id=self.org_id)

    async def _on_owner_inbound(self, event: Any) -> None:
        """`owner.dm` / `owner.pair` từ bridge: kiểm lại ở lõi rồi chuyển sang `gh.owner.inbound`. Tin 1-1 của Sếp và mã
        ghép KHÔNG vào Kho thô, và bản ghi gốc (mang chữ/mã) bị XDEL ngay sau khi xử lý."""
        async with self.sm() as db:
            if event.type == "owner.dm":
                await owner_inbound.handle_owner_dm(db, self.redis, self.org_id, event.payload)
            else:
                await owner_inbound.handle_owner_pair(db, self.org_id, event.payload)
            await db.commit()
            owner_pending = owner_inbound.take_pending(db)
        await owner_inbound.publish_pending(self.bus, self.org_id, owner_pending)
        if getattr(event, "message_id", None):
            await self.redis.xdel(event.stream, event.message_id)

    async def on_status(self, event: Any) -> None:
        async with self.sm() as db:
            out = await handle_status(db, self.redis, self.bus, self.org_id, event.type, event.payload)
            await db.commit()
            pending = lev.take_pending(db)
        await lev.publish_pending(self.sm, self.redis, self.org_id, pending)
        for type, data in out:
            await realtime.publish(self.redis, type, data, org_id=self.org_id)
        if any(t == "channel.status" for t, _ in out):
            from gh.shell.routes import publish_header

            async with self.sm() as db:
                await publish_header(db, self.redis, self.org_id)

    async def _on_owner_directory(self, event: Any) -> None:
        """Bot được thêm / bị mời ra khỏi nhóm, Sếp tag bot ở nhóm không nghe (thiết kế §3.1, §3.5)."""
        found = None
        async with self.sm() as db:
            if event.type == "group.self_joined":
                found = await handle_self_joined(db, self.redis, self.org_id, event.payload)
                await sync_listen_sets(db, self.redis, self.org_id)
            elif event.type == "group.self_removed":
                if await handle_self_removed(db, self.redis, self.org_id, event.payload):
                    await sync_listen_sets(db, self.redis, self.org_id)
            else:
                await owner_inbound.handle_owner_ping(db, self.org_id, event.payload)
            await db.commit()
            pending = lev.take_pending(db)
        await lev.publish_pending(self.sm, self.redis, self.org_id, pending)
        if found is not None and found[1]:      # nhóm MỚI do sự kiện thêm bot ⇒ báo Console như `group.discovered`
            row = found[0]
            await realtime.publish(self.redis, "group.discovered",
                                   {"id": str(row.id), "code": row.code, "name": row.name,
                                    "channel": event.payload.get("channel"), "listen_mode": row.listen_mode},
                                   org_id=self.org_id)

    async def on_directory(self, event: Any) -> None:
        if event.type == "group.discovered":
            async with self.sm() as db:
                found = await handle_group_discovered(db, self.redis, self.org_id, event.payload)
                first_id = None
                if found is not None:
                    # Savepoint: lưu tin đầu lỗi thì chỉ mất tin đầu — nhóm vừa tạo, tập nghe và bộ đếm vẫn commit
                    # (không có savepoint, cả giao dịch hỏng ⇒ bus giao lại 5 lần rồi DLQ, nhóm không bao giờ có).
                    try:
                        async with db.begin_nested():
                            first_id = await ingest_discovered_message(db, self.org_id, event.payload)
                    except Exception as exc:  # noqa: BLE001 — cách ly: không ghi nội dung tin vào log
                        log.warning("không lưu được tin đầu của nhóm lạ (%s)", type(exc).__name__)
                await db.commit()
                pending = lev.take_pending(db)
                first_row = await fetch_raw(db, first_id) if first_id is not None else None
            await lev.publish_pending(self.sm, self.redis, self.org_id, pending)
            # Đã commit (tin lưu được thì đã vào Kho thô; ask/off thì bỏ): bản ghi mang NỘI DUNG tin không được ở lại
            # trong stream (MAXLEN 100000, AOF bật). Lỗi trước dòng này ⇒ bus giao lại; vào DLQ thì bus tự bỏ nội dung.
            if getattr(event, "message_id", None):
                await self.redis.xdel(event.stream, event.message_id)
            if first_row is not None:
                await realtime.publish(self.redis, "raw.new", raw_item(first_row), org_id=self.org_id)
            if found is not None and found[1]:   # nhóm MỚI ⇒ báo Console (nhóm `pending` mở hộp "Nhóm mới — chọn …")
                row = found[0]
                await realtime.publish(self.redis, "group.discovered",
                                       {"id": str(row.id), "code": row.code, "name": row.name,
                                        "channel": event.payload.get("channel"), "listen_mode": row.listen_mode},
                                       org_id=self.org_id)
            return
        if event.type in ("group.self_joined", "group.self_removed", "group.owner_ping"):
            return await self._on_owner_directory(event)
        if event.type != "groups":
            return
        async with self.sm() as db:
            await handle_directory(db, self.redis, self.org_id, event.payload)
            await db.commit()
            pending = lev.take_pending(db)
        await lev.publish_pending(self.sm, self.redis, self.org_id, pending)


def uptime_minutes_key(type: str, day: datetime) -> str:
    return f"gh:uptime:{type}:{day:%Y%m%d}"


async def uptime_pct(redis: Redis, type: str, since: datetime | None) -> float | None:
    """Tỉ lệ phút có heartbeat trong 24 giờ qua (tính từ lúc phiên bắt đầu nếu mới hơn)."""
    now = datetime.now(UTC)
    start = max(now - timedelta(hours=24), since) if since else now - timedelta(hours=24)
    total = int((now - start).total_seconds() // 60)
    if total <= 0:
        return None
    maps: dict[str, bytes] = {}
    hits, t = 0, start
    while t < now:
        key = uptime_minutes_key(type, t)
        if key not in maps:
            maps[key] = await redis.get(key) or b""
        bit, bm = t.hour * 60 + t.minute, maps[key]
        if bit // 8 < len(bm) and bm[bit // 8] & (0x80 >> (bit % 8)):
            hits += 1
        t += timedelta(minutes=1)
    return round(min(100.0, hits * 100 / total), 1)


__all__ = ["Ingest", "handle_directory", "handle_group_discovered", "handle_self_joined", "handle_self_removed",
           "handle_status", "ingest_discovered_message", "ingest_message", "iso", "sync_listen_sets", "uptime_pct"]
