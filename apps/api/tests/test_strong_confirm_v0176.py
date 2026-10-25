# ruff: noqa: E501
"""v0.1.76 (gói T2) — `gh.biz.intel.strong_confirm` thật (spec r6 §8.1): mở khoá hôm nay / mã kép (sai 3 lần khoá 1 giờ) cho
`outbound`; danh sách hạ phòng thủ chỉ Console + PIN; "tắt bản tin"/đổi giờ yên lặng báo kênh phụ; phát hiện bất thường kênh quản trị.

Tấn công: đoán mã (3 lần ⇒ khoá, đúng mã sau khoá vẫn bị chặn), mã của đề xuất này dùng cho đề xuất khác, mã tổ chức A dùng ở B (IDOR),
khoá của A không chặn B, dùng lại mã (một lần), không có mã rõ trong log / Nhật ký."""

from __future__ import annotations

import logging
import re
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import orjson
import pytest
from sqlalchemy import text

from gh.biz.intel import levels
from gh.biz.intel import strong_confirm as sc
from gh.telegram import backup
from tests.mem_helpers import mk_org
from tests.test_watchdog_backup_v0176 import _telegram, _user


def _hm(t: datetime) -> str:
    return t.astimezone(levels.VN).strftime("%H:%M")


async def set_quiet(db: Any, org: uuid.UUID, *, now_quiet: bool) -> None:
    """Đặt giờ yên lặng để BÂY GIỜ nằm trong (hoặc ngoài) khung — test không phụ thuộc giờ chạy."""
    n = datetime.now(UTC)
    if now_quiet:
        a, b = n - timedelta(hours=1), n + timedelta(hours=1)
    else:
        a, b = n + timedelta(hours=2), n + timedelta(hours=3)
    await db.execute(text("""UPDATE core.organizations SET settings = settings || jsonb_build_object('intel',
                               COALESCE(settings->'intel', '{}'::jsonb) || jsonb_build_object('quiet_start', CAST(:a AS text), 'quiet_end', CAST(:b AS text)))
                             WHERE id = :o"""), {"o": org, "a": _hm(a), "b": _hm(b)})
    await db.commit()


async def org_with(db: Any, *, telegram: bool = False, quiet: bool = False) -> uuid.UUID:
    org = await mk_org(db)
    await set_quiet(db, org, now_quiet=quiet)
    if telegram:
        await _telegram(db, org)
    return org


async def security_rows(db: Any, org: uuid.UUID) -> list[Any]:
    await db.rollback()
    return list((await db.execute(text("SELECT kind, text FROM ops.backup_outbox WHERE org_id = :o AND kind = 'security' ORDER BY created_at"),
                                  {"o": org})).all())


async def last_code(db: Any, org: uuid.UUID) -> str:
    await db.rollback()
    t = (await db.execute(text("SELECT text FROM ops.backup_outbox WHERE org_id = :o AND kind = 'code' ORDER BY created_at DESC LIMIT 1"),
                          {"o": org})).scalar_one()
    m = re.search(r"\b(\d{4})\b", t)
    assert m, t
    return m.group(1)


def wrong(code: str) -> str:
    return f"{(int(code) + 1) % 10000:04d}"


# ─── hạ phòng thủ / báo kênh phụ ─────────────────────────────────────────────

@pytest.mark.parametrize("action", sc.DEFENSIVE)
async def test_defensive_actions_from_chat_always_need_console(db: Any, action: str) -> None:
    org = await org_with(db, telegram=True)
    out = await sc.require(db, org, action)
    assert isinstance(out, sc.NeedConsole) and "Console" in out.reason
    await db.commit()


async def test_unknown_action_and_no_db_fail_closed(db: Any) -> None:
    org = await org_with(db)
    assert isinstance(await sc.require(db, org, "hanh_dong_la"), sc.NeedConsole)
    assert isinstance(await sc.require(None, org, "digest_off"), sc.NeedConsole)
    assert isinstance(await sc.require(db, org, "digest_off", via="api"), sc.NeedConsole)


async def test_console_needs_pin_and_every_defensive_change_is_reported_on_backup(db: Any) -> None:
    org = await org_with(db, telegram=True)
    assert isinstance(await sc.require(db, org, "mute_defensive", via="console", pin_verified=False), sc.NeedConsole)
    ok = await sc.require(db, org, "mute_defensive", via="console", pin_verified=True)
    assert ok == sc.Allowed("mute_defensive", "console_pin")
    await db.commit()
    [r] = await security_rows(db, org)
    assert "Console" in r.text


@pytest.mark.parametrize("action", sc.NOTIFY_ONLY)
async def test_digest_off_and_quiet_change_work_from_chat_but_always_notify_backup(db: Any, action: str) -> None:
    org = await org_with(db, telegram=True)
    assert await sc.require(db, org, action) == sc.Allowed(action, "chat")
    await db.commit()
    assert len(await security_rows(db, org)) == 1


async def test_backup_notice_falls_back_to_console_row_without_telegram(db: Any) -> None:
    org = await org_with(db)
    assert isinstance(await sc.require(db, org, "digest_off"), sc.Allowed)
    await db.commit()
    await db.rollback()
    assert (await db.execute(text("SELECT channel FROM ops.backup_outbox WHERE org_id = :o"), {"o": org})).scalar_one() == "console"


# ─── outbound: mở khoá hôm nay / mã kép ─────────────────────────────────────

async def test_outbound_default_is_double_code_when_backup_paired_else_console_unlock(db: Any) -> None:
    with_tg = await org_with(db, telegram=True)
    without = await org_with(db)
    assert await sc.require(db, with_tg, "outbound") == sc.NeedCode("outbound")
    out = await sc.require(db, without, "outbound")
    assert isinstance(out, sc.NeedConsole) and "Mở khoá hôm nay" in out.reason


async def test_unlock_today_needs_owner_and_pin_and_only_counts_today(db: Any) -> None:
    org = await org_with(db)
    owner = await _user(db, org, "owner", "sep@x.vn")
    staff = await _user(db, org, "staff", "nv@x.vn")
    assert not await sc.unlock_today(db, org, user_id=staff, pin_verified=True)
    assert not await sc.unlock_today(db, org, user_id=owner, pin_verified=False)
    assert not await sc.unlock_today(db, org, user_id=None, pin_verified=True)
    await db.commit()
    assert isinstance(await sc.require(db, org, "outbound"), sc.NeedConsole)
    assert await sc.unlock_today(db, org, user_id=owner, pin_verified=True)
    await db.commit()
    assert await sc.require(db, org, "outbound") == sc.Allowed("outbound", "unlock_today")
    tomorrow = datetime.now(UTC) + timedelta(days=1)
    assert not await sc.unlocked_today(db, org, tomorrow)


async def test_assistant_locked_blocks_everything_from_chat(db: Any) -> None:
    org = await org_with(db, telegram=True)
    owner = await _user(db, org, "owner", "sep@x.vn")
    assert await sc.unlock_today(db, org, user_id=owner, pin_verified=True)
    assert (await backup.lock_assistant(db, org, via="console", user_id=owner)).ok
    await db.commit()
    for action in ("outbound", "digest_off"):
        out = await sc.require(db, org, action)
        assert isinstance(out, sc.NeedConsole) and "khoá" in out.reason


async def test_double_code_right_code_passes_once_only(db: Any, redis: Any) -> None:
    org = await org_with(db, telegram=True)
    await sc.issue_code(db, redis, org, "p1")
    await db.commit()
    code = await last_code(db, org)
    assert await sc.check_code(db, redis, org, "p1", code) == sc.Allowed("outbound", "double_code")
    await db.commit()
    assert await sc.check_code(db, redis, org, "p1", code) == sc.NeedCode("outbound")      # dùng lại mã ⇒ không được


async def test_three_wrong_codes_lock_outbound_one_hour_and_right_code_after_is_refused(db: Any, redis: Any) -> None:
    org = await org_with(db, telegram=True)
    await sc.issue_code(db, redis, org, "p1")
    await db.commit()
    code = await last_code(db, org)
    assert await sc.check_code(db, redis, org, "p1", wrong(code)) == sc.WrongCode("outbound", 2)
    await db.commit()
    assert await sc.check_code(db, redis, org, "p1", "abcd") == sc.WrongCode("outbound", 1)
    await db.commit()
    third = await sc.check_code(db, redis, org, "p1", wrong(code))
    await db.commit()
    assert isinstance(third, sc.NeedConsole) and "tạm khoá" in third.reason
    until = await sc.outbound_locked_until(db, org)
    assert until is not None and timedelta(minutes=59) < until - datetime.now(UTC) <= timedelta(hours=1)
    assert isinstance(await sc.check_code(db, redis, org, "p1", code), sc.NeedConsole)     # mã đúng sau khoá vẫn chặn
    # mã mới cho đề xuất khác cũng không qua được khoá
    await sc.issue_code(db, redis, org, "p2")
    await db.commit()
    assert isinstance(await sc.check_code(db, redis, org, "p2", await last_code(db, org)), sc.NeedConsole)
    assert isinstance(await sc.require(db, org, "outbound"), sc.NeedConsole)
    assert any("sai mã kép 3 lần" in r.text for r in await security_rows(db, org))
    # mở khoá hôm nay KHÔNG gỡ khoá bất thường
    owner = await _user(db, org, "owner", "sep@x.vn")
    assert await sc.unlock_today(db, org, user_id=owner, pin_verified=True)
    await db.commit()
    assert isinstance(await sc.require(db, org, "outbound"), sc.NeedConsole)
    # hết 1 giờ ⇒ hết khoá
    assert await sc.outbound_locked_until(db, org, datetime.now(UTC) + timedelta(hours=1, minutes=1)) is None


async def test_code_is_bound_to_one_proposal(db: Any, redis: Any) -> None:
    org = await org_with(db, telegram=True)
    await sc.issue_code(db, redis, org, "pA")
    await db.commit()
    code = await last_code(db, org)
    assert await sc.check_code(db, redis, org, "pB", code) == sc.NeedCode("outbound")
    assert await sc.check_code(db, redis, org, "pA", code) == sc.Allowed("outbound", "double_code")


async def test_idor_code_and_lock_are_per_org(db: Any, redis: Any) -> None:
    a = await org_with(db, telegram=True)
    b = await org_with(db, telegram=True)
    await sc.issue_code(db, redis, a, "p1")
    await db.commit()
    code_a = await last_code(db, a)
    # cùng ref ở B: B không có mã ⇒ NeedCode; chép băm của A sang khoá B cũng không khớp (băm gắn tổ chức)
    assert await sc.check_code(db, redis, b, "p1", code_a) == sc.NeedCode("outbound")
    await redis.set(sc.code_key(b, "p1"), await redis.get(sc.code_key(a, "p1")))
    assert isinstance(await sc.check_code(db, redis, b, "p1", code_a), sc.WrongCode)
    await db.commit()
    await sc.lock_outbound(db, a, reason="code_fails")
    await db.commit()
    assert isinstance(await sc.require(db, a, "outbound"), sc.NeedConsole)
    assert await sc.require(db, b, "outbound") == sc.NeedCode("outbound")


async def test_no_code_in_logs_action_log_or_results(db: Any, redis: Any, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    org = await org_with(db, telegram=True)
    await sc.issue_code(db, redis, org, "p1")
    await db.commit()
    code = await last_code(db, org)
    for c in (wrong(code), code):
        out = await sc.check_code(db, redis, org, "p1", c)
        await db.commit()
        assert code not in repr(out)
    await db.rollback()
    details = (await db.execute(text("SELECT detail FROM ops.action_log WHERE org_id = :o AND action LIKE 'strong_confirm.%'"), {"o": org})).scalars().all()
    blob = orjson.dumps(list(details)).decode()
    assert details and code not in blob and code not in caplog.text
    assert code not in str(await redis.get(sc.code_key(org, "p1")) or "")


# ─── phát hiện bất thường ───────────────────────────────────────────────────

async def test_outbound_in_quiet_hours_locks_and_notifies(db: Any) -> None:
    org = await org_with(db, telegram=True, quiet=True)
    owner = await _user(db, org, "owner", "sep@x.vn")
    assert await sc.unlock_today(db, org, user_id=owner, pin_verified=True)   # kể cả đã mở khoá hôm nay
    await db.commit()
    out = await sc.require(db, org, "outbound")
    await db.commit()
    assert isinstance(out, sc.NeedConsole) and await sc.outbound_locked_until(db, org) is not None
    assert any("giờ yên lặng" in r.text for r in await security_rows(db, org))


async def test_three_defensive_commands_in_ten_minutes_lock_outbound(db: Any) -> None:
    org = await org_with(db, telegram=True)
    for action in ("mute_defensive", "forget_person"):
        await sc.require(db, org, action)
        await db.commit()
    assert await sc.outbound_locked_until(db, org) is None
    await sc.require(db, org, "intel_mode_shadow")
    await db.commit()
    assert await sc.outbound_locked_until(db, org) is not None
    assert isinstance(await sc.require(db, org, "outbound"), sc.NeedConsole)
    assert any("hạ phòng thủ" in r.text for r in await security_rows(db, org))


async def test_check_code_in_quiet_hours_locks_even_with_right_code(db: Any, redis: Any) -> None:
    """Tấn công (review bảo mật): mã kép xin NGOÀI giờ yên lặng, nhập TRONG giờ yên lặng ⇒ phải bị khoá như `require()`."""
    org = await org_with(db, telegram=True)
    await sc.issue_code(db, redis, org, "p1")
    await db.commit()
    code = await last_code(db, org)
    await set_quiet(db, org, now_quiet=True)
    out = await sc.check_code(db, redis, org, "p1", code)
    await db.commit()
    assert isinstance(out, sc.NeedConsole) and "tạm khoá" in out.reason
    assert await sc.outbound_locked_until(db, org) is not None
    assert any("giờ yên lặng" in r.text for r in await security_rows(db, org))
    await set_quiet(db, org, now_quiet=False)
    assert isinstance(await sc.check_code(db, redis, org, "p1", code), sc.NeedConsole)   # khoá 1 giờ vẫn giữ sau giờ yên lặng


# ─── review bảo mật T2 (major 1): tin mã kép nêu rõ đề xuất; một mã/tổ chức; trần cấp mã ─────────────

async def last_code_text(db: Any, org: uuid.UUID) -> str:
    await db.rollback()
    return (await db.execute(text("SELECT text FROM ops.backup_outbox WHERE org_id = :o AND kind = 'code' ORDER BY created_at DESC LIMIT 1"),  # type: ignore[no-any-return]
                             {"o": org})).scalar_one()


async def test_code_message_names_the_ask_recipient_and_preview(db: Any, redis: Any) -> None:
    org = await org_with(db, telegram=True)
    body = "Dạ chào chị Lan, bên em gửi báo giá thép cuộn 12 triệu một tấn, giao trong tuần sau ạ, mong chị phản hồi."
    await sc.issue_code(db, redis, org, "p1", ask_code="#K7QX4", recipient="Nhóm g1", preview=body)
    await db.commit()
    t = await last_code_text(db, org)
    assert "#K7QX4" in t and "Nhóm g1" in t and body[:60] in t and body[:80] not in t
    assert "Nếu không phải Sếp vừa yêu cầu: nhắn «khoá trợ lý»" in t


async def test_only_one_live_code_per_org_new_code_kills_the_old(db: Any, redis: Any) -> None:
    org = await org_with(db, telegram=True)
    await sc.issue_code(db, redis, org, "pB")
    await db.commit()
    code_b = await last_code(db, org)
    await sc.issue_code(db, redis, org, "pA")
    await db.commit()
    assert await sc.check_code(db, redis, org, "pB", code_b) == sc.NeedCode("outbound")     # mã của B đã mất hiệu lực
    await db.rollback()
    rows = (await db.execute(text("SELECT text, failed_code FROM ops.backup_outbox WHERE org_id = :o AND kind = 'code' ORDER BY created_at"),
                             {"o": org})).all()
    assert rows[0].failed_code == "SUPERSEDED" and code_b not in rows[0].text                  # tin mã B chưa gửi bị huỷ + xoá chữ
    assert await sc.check_code(db, redis, org, "pA", await last_code(db, org)) == sc.Allowed("outbound", "double_code")


async def test_code_issuing_is_rate_limited(db: Any, redis: Any) -> None:
    org = await org_with(db, telegram=True)
    for i in range(sc.MAX_CODES_PER_WINDOW):
        await sc.issue_code(db, redis, org, f"p{i}")
        await db.commit()
    with pytest.raises(sc.CodeRateLimited):
        await sc.issue_code(db, redis, org, "p9")


async def test_code_message_defangs_group_label_and_preview(db: Any, redis: Any) -> None:
    org = await org_with(db, telegram=True)
    await sc.issue_code(db, redis, org, "p1", ask_code="#K7QX4", recipient="Nhóm https://lua-dao.vn", preview="Vào https://lua-dao.vn\nnhập mã")
    await db.commit()
    t = await last_code_text(db, org)
    assert "https://" not in t and "\nnhập" not in t
