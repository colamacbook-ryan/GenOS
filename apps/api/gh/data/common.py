"""Tiện ích dùng chung cho tầng dữ liệu: mã công khai, dòng Kho thô, thời gian."""

import json
import re
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

CHANNEL_PREFIX = {"zalo": "ZL", "whatsapp": "WA", "telegram": "TG", "linkedin": "LI"}
CHANNEL_NAME = {"zalo": "Zalo", "whatsapp": "WhatsApp", "telegram": "Telegram", "linkedin": "LinkedIn"}
# v0.1.65 (F1): MỘT nguồn cho chế độ nghe của nhóm — API, bridge và web (`ListenMode`, contracts/phase2.ts) theo đây.
#   · ĐANG nghe (lưu tin vào Kho thô): tagged_only ("Nghe hết, chỉ trả lời khi được tag"), silent, proactive.
#   · KHÔNG nghe: off ("Không nghe"), paused (Sếp tạm dừng), pending (nhóm mới phát hiện, CHỜ Sếp chọn — không lưu tin,
#     vẫn thu danh sách thành viên).
LISTENING_MODES = ("tagged_only", "silent", "proactive")
NOT_LISTENING_MODES = ("off", "paused", "pending")
ALL_LISTEN_MODES = ("pending", "off", "tagged_only", "silent", "proactive", "paused")
#: Chế độ Sếp được CHỌN (không ai tự đặt lại về "pending").
SETTABLE_LISTEN_MODES = ("off", "tagged_only", "silent", "proactive", "paused")
NEW_GROUP_MODE = "pending"

# Nhóm MỚI phát hiện sau này (theo kênh, `core.organizations.settings.new_group_mode.<kênh>`; Sếp chốt 17/10/2026, thay
# quyết định «hỏi khi kết nối kênh»: «khi được add vào một group thì MẶC ĐỊNH lắng nghe trong im lặng, KHÔNG trả lời cho
# dù được tag»):
#   silent = vào "Lắng nghe im lặng" ngay (lưu hết tin, không bao giờ trả lời kể cả khi được tag) — MẶC ĐỊNH, kể cả bản
#   cài cũ chưa có khoá · ask = nhóm vào `pending` + hộp hỏi Sếp · off = vào "Không nghe" (không lưu tin). Cả ba vẫn thu
#   thành viên. Chỉ áp cho nhóm tạo SAU khi đặt — không bao giờ đổi nhóm đã có (nhóm cũ đang `off` vẫn `off`).
NEW_GROUP_MODES = ("ask", "silent", "off")
DEFAULT_NEW_GROUP_MODE = "silent"
#: Bộ đếm theo kênh `settings.new_groups_silent.<kênh>`: số nhóm MỚI vừa tự vào «Lắng nghe im lặng» mà Sếp chưa bấm
#: «Đã biết» (thẻ kênh báo «N nhóm mới đã vào Lắng nghe im lặng»).
NEW_SILENT_KEY = "new_groups_silent"


def new_group_mode(settings: dict[str, Any] | None, channel_type: str) -> str:
    """Thiết lập nhóm mới của một kênh; thiếu/sai kiểu/giá trị lạ ⇒ `silent` (mặc định)."""
    block = (settings or {}).get("new_group_mode")
    v = block.get(channel_type) if isinstance(block, dict) else None
    return str(v) if v in NEW_GROUP_MODES else DEFAULT_NEW_GROUP_MODE


def initial_group_mode(settings: dict[str, Any] | None, channel_type: str) -> str:
    """Chế độ nghe của một nhóm VỪA được tạo: ask ⇒ pending, còn lại ⇒ đúng giá trị đang đặt (silent | off)."""
    m = new_group_mode(settings, channel_type)
    return NEW_GROUP_MODE if m == "ask" else m


# Tin riêng 1-1 (theo kênh, lưu ở `core.organizations.settings.direct_mode.<kênh>`): off = không lưu; silent = lưu nhưng
# không tự trả lời và không đưa cho agent trực (MẶC ĐỊNH); reply = lưu + agent trực được phép trả lời (= `listen_direct`
# bật của bản ≤ 0.1.64).
DIRECT_MODES = ("off", "silent", "reply")
DEFAULT_DIRECT_MODE = "silent"


def direct_mode(settings: dict[str, Any] | None, channel_type: str) -> str:
    """Chế độ tin 1-1 của một kênh. Khoá mới `direct_mode` thắng; chưa có thì đọc khoá cũ `listen_direct`
    (true ⇒ reply); còn lại ⇒ mặc định silent."""
    s = settings or {}
    v = (s.get("direct_mode") or {}).get(channel_type)
    if v in DIRECT_MODES:
        return str(v)
    return "reply" if (s.get("listen_direct") or {}).get(channel_type) is True else DEFAULT_DIRECT_MODE


#: Cùng quy tắc `direct_mode` nhưng cho SQL (bí danh `o` = core.organizations, `c` = core.channels).
DIRECT_MODE_SQL = ("COALESCE(NULLIF(o.settings #>> ARRAY['direct_mode', c.type], ''), "
                   "CASE WHEN o.settings #>> ARRAY['listen_direct', c.type] = 'true' THEN 'reply' "
                   f"ELSE '{DEFAULT_DIRECT_MODE}' END)")
SINCE = {"24h": timedelta(hours=24), "7d": timedelta(days=7), "30d": timedelta(days=30)}
RAW_CODE_RE = re.compile(r"^RAW-(\d+)$")


def iso(dt: datetime | None) -> str | None:
    return dt.astimezone(UTC).isoformat().replace("+00:00", "Z") if dt else None


def parse_cursor(cursor: str | None) -> datetime | None:
    """Con trỏ phân trang kiểu `created_at < :c`: asyncpg suy ra kiểu tham số từ `CAST(:c AS timestamptz)` và từ
    chối bind một `str` ở đó (khác `psycopg`, vẫn chấp nhận) — luôn ép sang `datetime` trước khi bind, để SQL chỉ
    cần so sánh `:c` trực tiếp, không cần `CAST`."""
    return datetime.fromisoformat(cursor.replace("Z", "+00:00")) if cursor else None


def since_cutoff(since: str | None) -> datetime | None:
    if not since or since == "all":
        return None
    delta = SINCE.get(since)
    if delta is None:
        from gh.errors import field_errors

        raise field_errors({"since": "Chỉ nhận 24h, 7d, 30d, all"})
    return datetime.now(UTC) - delta


def raw_code(seq: int) -> str:
    return f"RAW-{seq:06d}"


def ref(id: Any, code: str | None, name: str | None) -> dict[str, Any] | None:
    if id is None:
        return None
    return {"id": str(id), "code": code, "name": name}


# Một dòng Kho thô kèm nhóm, người, trạng thái sàng lọc. Dùng chung cho danh sách, chi tiết, WebSocket, chứng cứ.
RAW_SELECT = """
SELECT e.id, e.seq, e.received_at, e.occurred_at, e.direction, e.kind, e.body_text, e.payload,
       c.type AS channel_type, c.name AS channel_name,
       g.id AS group_id, g.code AS group_code, g.name AS group_name,
       p.id AS person_id, p.code AS person_code, p.display_name AS person_name,
       s.state, s.label, s.confidence
FROM raw.events e
JOIN core.channels c ON c.id = e.channel_id
LEFT JOIN core.groups g ON g.id = e.group_id
LEFT JOIN core.person_identities pi ON pi.id = e.sender_identity_id
LEFT JOIN LATERAL (
  -- người sống sau hợp nhất: đi theo merged_into_id tới hồ sơ gốc
  WITH RECURSIVE up(id, merged_into_id, depth) AS (
    SELECT id, merged_into_id, 0 FROM core.persons WHERE id = pi.person_id
    UNION ALL SELECT q.id, q.merged_into_id, up.depth + 1 FROM core.persons q JOIN up ON q.id = up.merged_into_id
    WHERE up.depth < 10)
  SELECT id FROM up WHERE merged_into_id IS NULL LIMIT 1) live ON true
LEFT JOIN core.persons p ON p.id = live.id
LEFT JOIN refinery.event_state s ON s.event_id = e.id AND s.event_received_at = e.received_at
"""


def raw_item(r: Any, *, with_payload: bool = False) -> dict[str, Any]:
    out = {
        "id": str(r.id), "code": raw_code(r.seq), "received_at": iso(r.received_at),
        "occurred_at": iso(r.occurred_at),
        "channel": {"type": r.channel_type, "name": CHANNEL_NAME.get(r.channel_type, r.channel_name)},
        "group": ref(r.group_id, r.group_code, r.group_name),
        "person": ref(r.person_id, r.person_code, r.person_name),
        "direction": r.direction, "kind": r.kind, "text": r.body_text,
        "label": r.label, "confidence": float(r.confidence) if r.confidence is not None else None,
        "state": r.state or "pending",
    }
    if with_payload:
        out["payload"] = r.payload
    return out


async def fetch_raw(db: AsyncSession, event_id: uuid.UUID) -> Any:
    return (await db.execute(text(RAW_SELECT + " WHERE e.id = :i"), {"i": event_id})).one_or_none()


async def org_settings(db: AsyncSession, org_id: uuid.UUID) -> dict[str, Any]:
    return (await db.execute(text("SELECT settings FROM core.organizations WHERE id = :o"),
                             {"o": org_id})).scalar_one() or {}


async def set_channel_setting(db: AsyncSession, org_id: uuid.UUID, key: str, channel_type: str, value: Any) -> None:
    """Ghi `settings.<key>.<kênh>` = value (JSON), tạo khối `<key>` nếu chưa là object. Chỉ gọi với `key` là hằng."""
    await db.execute(text("""
        UPDATE core.organizations SET settings = jsonb_set(
          CASE WHEN jsonb_typeof(settings -> CAST(:k AS text)) = 'object' THEN settings
               ELSE jsonb_set(settings, ARRAY[CAST(:k AS text)], '{}'::jsonb) END,
          ARRAY[CAST(:k AS text), CAST(:t AS text)], CAST(:v AS jsonb)) WHERE id = :o"""),
        {"k": key, "t": channel_type, "v": json.dumps(value), "o": org_id})


async def drop_channel_counter(db: AsyncSession, org_id: uuid.UUID, key: str, channel_type: str) -> None:
    """Giảm `settings.<key>.<kênh>` đi 1, không xuống dưới 0 (thiếu = 0 ⇒ không đổi). Chỉ gọi với `key` là hằng."""
    await db.execute(text("""
        UPDATE core.organizations SET settings = jsonb_set(settings, ARRAY[CAST(:k AS text), CAST(:t AS text)],
          to_jsonb(GREATEST((settings #>> ARRAY[CAST(:k AS text), CAST(:t AS text)])::int - 1, 0)))
        WHERE id = :o AND jsonb_typeof(settings #> ARRAY[CAST(:k AS text), CAST(:t AS text)]) = 'number'"""),
        {"k": key, "t": channel_type, "o": org_id})


async def bump_channel_counter(db: AsyncSession, org_id: uuid.UUID, key: str, channel_type: str) -> None:
    """Tăng nguyên tử `settings.<key>.<kênh>` (số nguyên, thiếu = 0) thêm 1. Chỉ gọi với `key` là hằng."""
    await db.execute(text("""
        UPDATE core.organizations SET settings = jsonb_set(
          CASE WHEN jsonb_typeof(settings -> CAST(:k AS text)) = 'object' THEN settings
               ELSE jsonb_set(settings, ARRAY[CAST(:k AS text)], '{}'::jsonb) END,
          ARRAY[CAST(:k AS text), CAST(:t AS text)],
          to_jsonb(CASE WHEN jsonb_typeof(settings #> ARRAY[CAST(:k AS text), CAST(:t AS text)]) = 'number'
                        THEN (settings #>> ARRAY[CAST(:k AS text), CAST(:t AS text)])::int ELSE 0 END + 1))
        WHERE id = :o"""), {"k": key, "t": channel_type, "o": org_id})


async def live_person(db: AsyncSession, person_id: uuid.UUID) -> uuid.UUID:
    """Hồ sơ đang sống của một người (đi theo merged_into_id)."""
    seen = 0
    while seen < 10:
        nxt = (await db.execute(text("SELECT merged_into_id FROM core.persons WHERE id = :i"),
                                {"i": person_id})).scalar_one_or_none()
        if nxt is None:
            return person_id
        person_id, seen = nxt, seen + 1
    return person_id


# Khoá cứng 8: ẩn dữ liệu nhạy cảm khỏi vai trò dưới Owner (số tài khoản / thẻ / giấy tờ, số điện thoại đầy đủ).
_RE_LONGNUM = re.compile(r"(?<!\d)(\d[\d .-]{7,22}\d)(?!\d)")


#: Dấu vết của `mask_text` trong một chuỗi (≥ 5 dấu • liền nhau, phần còn lại là 3 số cuối).
_RE_MASKED = re.compile(r"•{4,}")


def has_masked_number(text_: str | None) -> bool:
    """Chuỗi có chỗ số nhạy cảm đã bị `mask_text` che — vai dưới Owner KHÔNG được gửi đi nguyên chuỗi đó."""
    return bool(text_ and _RE_MASKED.search(text_))


def mask_text(text_: str | None, is_owner: bool) -> str | None:
    if text_ is None or is_owner:
        return text_
    # v0.1.71 (P3-10): vai dưới Owner cũng không đọc được mã OTP / mật khẩu / token trong URL cạnh từ khoá (hình dạng
    # chuỗi không lộ, chỉ ngữ cảnh mới lộ) — cùng bộ luật với chữ gửi cho model. Import muộn: `chassis.masking` nhập
    # lại module này.
    from gh.chassis.masking import mask_context_secrets

    text_ = mask_context_secrets(text_)

    def sub(m: re.Match[str]) -> str:
        digits = re.sub(r"\D", "", m.group(1))
        return m.group(1) if len(digits) < 8 else "•" * (len(digits) - 3) + digits[-3:]

    return _RE_LONGNUM.sub(sub, text_)


# ─── ghép "tin đi" với đúng người nhận (tin 1-1) — MỘT chỗ cho Thông tin, Phân tích, cảnh báo, chăm sóc khách ─────

#: Tin vào KHÔNG cần ai trả lời (v0.1.67, F050): biểu tượng cảm xúc, nhãn dán, tin hệ thống.
NO_REPLY_KINDS = ("reaction", "sticker", "system")


def outbound_to_sql(e: str, ids: str, ext: str) -> str:
    """Điều kiện SQL "tin ĐI `e` (1-1) là gửi cho người có định danh `ids` (uuid[]) / mã ngoài `ext` (text[])":
    người nhận ghi trong payload (Zalo `threadId`; WhatsApp `key.remoteJid` hoặc `key.remoteJidAlt` — cùng một người
    có thể hiện bằng số điện thoại hay LID; hoặc `recipient`) trùng `ext`, hoặc (không ghi người nhận) tin ĐẾN 1-1
    gần nhất trước nó trên cùng kênh là của `ids`. Tin 1-1 KHÔNG có người nhận trong cột riêng (người gửi tin đi là
    tài khoản của Sếp) nên KHÔNG được ghép theo "cùng kênh, không nhóm" — trả lời khách A không được che khách B
    (v0.1.67, F050). `e`: bí danh của `raw.events`; `ids`/`ext`: biểu thức SQL (mảng)."""
    def bare(path: str) -> str:
        return f"NULLIF(split_part({path}, '@', 1), '')"

    keys = [bare(f"{e}.payload->>'threadId'"), bare(f"{e}.payload->'key'->>'remoteJid'"),
            bare(f"{e}.payload->'key'->>'remoteJidAlt'"), bare(f"{e}.payload->>'recipient'")]
    return f"""({e}.direction = 'outbound' AND (
    ARRAY[{", ".join(keys)}]::text[] && {ext}
    OR (COALESCE({", ".join(keys)}) IS NULL AND (
      SELECT i2.sender_identity_id FROM raw.events i2
      WHERE i2.org_id = {e}.org_id AND i2.channel_id = {e}.channel_id AND i2.group_id IS NULL
        AND i2.direction = 'inbound' AND i2.occurred_at <= {e}.occurred_at
        AND i2.occurred_at > {e}.occurred_at - interval '7 days'
        AND i2.received_at > {e}.received_at - interval '37 days' AND i2.received_at <= {e}.received_at
      ORDER BY i2.occurred_at DESC, i2.seq DESC LIMIT 1) = ANY({ids}))))"""
