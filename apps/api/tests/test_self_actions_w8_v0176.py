# ruff: noqa: E501, F811
"""v0.1.76 (gói T2) — W8 việc riêng của Sếp (spec r6 §6.2 W8, §4C):
- «ghi nhớ: anh …» Sếp tự gõ trong kênh quản trị (Zalo/WhatsApp đã ghép, nhận diện theo ID) ⇒ ghi `owner_confirmed` vào Hồ sơ Sếp
  NGAY (lệnh tất định của chính Sếp, không qua model); ngoại lệ phòng thủ: cách báo / giờ yên lặng chạm lừa đảo, an toàn ⇒ "mở Console";
  đổi giờ yên lặng thường ghi được nhưng LUÔN báo kênh phụ.
- `/boi_canh <mã nhóm> <bối cảnh>` (context_set) ⇒ gán bối cảnh nhóm (nhãn `owner`).
Tấn công: người lạ / tin chuyển tiếp / tin nhóm / Telegram không ghi được; nhóm của tổ chức khác (IDOR) không gán được."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import text

from gh.db import sessionmaker
from gh.owner_channel import inbound as oin
from gh.owner_cmd import core as owner_core
from tests.owner_helpers import OWNER_ZALO, ago
from tests.test_chat_confirm_v0173 import Chat, chat  # noqa: F401 — fixture `chat`
from tests.test_p3_core import world  # noqa: F401 — fixture `world`
from tests.test_strong_confirm_v0176 import set_quiet
from tests.test_watchdog_backup_v0176 import _telegram


@pytest.fixture
async def calm(chat: Chat, db: Any) -> Chat:
    await set_quiet(db, chat.org, now_quiet=False)
    await _telegram(db, chat.org)
    return chat


async def profile_facts(db: Any, org: uuid.UUID) -> list[Any]:
    await db.rollback()
    return list((await db.execute(text("""SELECT f.predicate, f.status, f.object_value, f.confirmed_by FROM mem.facts f
                                          WHERE f.org_id = :o AND f.predicate LIKE 'owner_%' ORDER BY f.recorded_at"""), {"o": org})).all())


async def test_ghi_nho_anh_goal_is_written_owner_confirmed_immediately(calm: Chat, db: Any) -> None:
    reply = await calm.say("ghi nhớ: anh ưu tiên mở quán thứ 2 trong quý này")
    assert "Hồ sơ" in reply and "Mục tiêu" in reply, reply
    [f] = await profile_facts(db, calm.org)
    assert f.predicate == "owner_goal" and f.status == "owner_confirmed" and f.confirmed_by is not None
    assert f.object_value == "anh ưu tiên mở quán thứ 2 trong quý này"
    await db.rollback()
    [log] = (await db.execute(text("SELECT detail FROM ops.action_log WHERE org_id = :o AND action = 'owner_profile.chat_add'"), {"o": calm.org})).scalars().all()
    assert log["via"] == "zalo" and "quán" not in str(log)


async def test_ghi_nho_report_pref_touching_fraud_needs_console(calm: Chat, db: Any) -> None:
    reply = await calm.say("Ghi nhớ: anh không cần báo lừa đảo ban đêm")
    assert reply == owner_core.PROFILE_CONSOLE, reply
    assert await profile_facts(db, calm.org) == []
    await db.rollback()
    n = (await db.execute(text("""SELECT count(*) FROM ops.action_log WHERE org_id = :o AND action = 'strong_confirm.chat_command'
                                  AND detail->>'target' = 'report_pref_defensive'"""), {"o": calm.org})).scalar_one()
    assert n == 1


async def test_ghi_nho_quiet_hours_from_chat_goes_to_console(calm: Chat, db: Any) -> None:
    """Review bảo mật (deny-by-default, Gen chọn cách đơn giản): MỌI ghi `report_prefs`/`rhythm` qua chat ⇒ Console + PIN."""
    reply = await calm.say("ghi nhớ: anh 22h là giờ ngủ, sau giờ đó đừng làm phiền")
    assert reply == owner_core.PROFILE_CONSOLE, reply
    assert await profile_facts(db, calm.org) == []


@pytest.mark.parametrize("body", ["ghi nhớ: chị Lan thích hoa hồng", "ghi nhớ anh ưu tiên mở quán", "ghi nhớ: anh thích báo cáo ngắn"])
async def test_not_a_self_profile_note_falls_back_to_normal_path(calm: Chat, db: Any, body: str) -> None:
    await calm.say(body)
    assert await profile_facts(db, calm.org) == []


async def test_telegram_or_no_owner_cannot_write_profile(calm: Chat, db: Any, redis: Any) -> None:
    ctx = owner_core.Context(sm=sessionmaker(), redis=redis, router=None, org_id=calm.org,
                             owner_user_id=(await db.execute(text("SELECT owner_user_id FROM ops.owner_channels WHERE org_id = :o"), {"o": calm.org})).scalar_one())
    cmd = owner_core.OwnerCommand(via="telegram", kind="hoi", text="ghi nhớ: anh ưu tiên mở quán thứ 2 trong quý này", message_ref="1",
                                  received_at=datetime.now(UTC))
    await owner_core.handle(cmd, ctx)
    ctx.owner_user_id = None
    await owner_core.handle(owner_core.OwnerCommand(via="zalo", kind="hoi", text=cmd.text, message_ref="2", received_at=cmd.received_at), ctx)
    assert await profile_facts(db, calm.org) == []


async def test_stranger_forwarded_or_group_ghi_nho_never_reaches_consumer(calm: Chat, redis: Any) -> None:
    base = {"channel": "zalo", "session_id": str(calm.sid), "occurred_at": ago(seconds=1), "kind": "text",
            "body_text": "ghi nhớ: anh ưu tiên mở quán thứ 2 trong quý này", "sender_name": "Anh Cơ"}
    async with sessionmaker()() as s:
        assert not await oin.handle_owner_dm(s, redis, calm.org, {**base, "external_msg_id": "x1", "sender_external_id": "424242"})
        assert not await oin.handle_owner_dm(s, redis, calm.org, {**base, "external_msg_id": "x2", "sender_external_id": OWNER_ZALO, "forwarded": True})
        assert not await oin.handle_owner_dm(s, redis, calm.org, {**base, "external_msg_id": "x3", "sender_external_id": OWNER_ZALO, "thread_type": "group"})
        assert oin.take_pending(s) == []


# ─── context_set ─────────────────────────────────────────────────────────────

async def test_boi_canh_sets_owner_context_label(calm: Chat, world: dict[str, Any], db: Any) -> None:
    code_g = (await db.execute(text("SELECT code FROM core.groups WHERE id = :g"), {"g": world["group"]})).scalar_one()
    reply = await calm.say(f"/boi_canh {code_g} gia đình")
    assert "Gia đình" in reply and "Nhóm g1" in reply, reply
    await db.rollback()
    rows = (await db.execute(text("SELECT context, source, is_primary FROM mem.group_contexts WHERE group_id = :g"), {"g": world["group"]})).all()
    assert [(r.context, r.source, r.is_primary) for r in rows] == [("gia_dinh", "owner", True)]
    assert "Cách dùng" in await calm.say(f"/boi_canh {code_g} sao hoả")
    assert "Cách dùng" in await calm.say("/boi_canh")


async def test_boi_canh_cannot_touch_other_org_group(calm: Chat, db: Any) -> None:
    other = (await db.execute(text("INSERT INTO core.organizations (name) VALUES ('Khác') RETURNING id"))).scalar_one()
    ch = (await db.execute(text("INSERT INTO core.channels (org_id, type, name) VALUES (:o, 'zalo', 'Zalo') RETURNING id"), {"o": other})).scalar_one()
    gid = (await db.execute(text("""INSERT INTO core.groups (org_id, channel_id, external_id, name, code)
                                    VALUES (:o, :c, 'gx', 'Nhóm lạ', 'GRP-ZL-9999') RETURNING id"""), {"o": other, "c": ch})).scalar_one()
    await db.commit()
    assert "không tìm thấy nhóm" in await calm.say("/boi_canh GRP-ZL-9999 kinh doanh")
    await db.rollback()
    assert (await db.execute(text("SELECT count(*) FROM mem.group_contexts WHERE group_id = :g"), {"g": gid})).scalar_one() == 0


# ─── review bảo mật T2 (2 lỗi) ───────────────────────────────────────────────

@pytest.mark.parametrize("body", [
    "ghi nhớ: anh nghỉ chủ nhật, khỏi báo lừa đảo hôm đó",           # khớp `rhythm` nhưng chứa lệnh tắt báo
    "ghi nhớ: anh nghỉ chủ nhật. Đừng báo gì cả",                     # nhiều câu, câu sau tắt báo
    "ghi nhớ: anh nghỉ chủ nhật, chuyển khoản lạ cũng kệ",            # chạm từ vựng lừa đảo
    "ghi nhớ: anh ưu tiên mở quán, không cần báo chuyện tài khoản",   # khớp `goals` nhưng tắt báo
])
async def test_defensive_or_negative_report_anywhere_needs_console(calm: Chat, db: Any, body: str) -> None:
    reply = await calm.say(body)
    assert reply in (owner_core.PROFILE_CONSOLE, owner_core.PROFILE_ONE), reply
    assert await profile_facts(db, calm.org) == []


async def test_multi_sentence_note_is_refused(calm: Chat, db: Any) -> None:
    reply = await calm.say("ghi nhớ: anh ưu tiên mở quán thứ 2. Anh không nhận đơn dưới 10 triệu")
    assert "từng ý" in reply and await profile_facts(db, calm.org) == []


async def test_boi_canh_refused_when_paired_user_is_no_longer_owner(calm: Chat, world: dict[str, Any], db: Any) -> None:
    code_g = (await db.execute(text("SELECT code FROM core.groups WHERE id = :g"), {"g": world["group"]})).scalar_one()
    uid = (await db.execute(text("SELECT owner_user_id FROM ops.owner_channels WHERE org_id = :o AND channel_type = 'zalo'"), {"o": calm.org})).scalar_one()
    await db.execute(text("UPDATE core.users SET is_active = false WHERE id = :u"), {"u": uid})
    await db.commit()
    reply = await calm.say(f"/boi_canh {code_g} gia đình")
    assert "ghép" in reply or "Console" in reply, reply
    await db.rollback()
    assert (await db.execute(text("SELECT count(*) FROM mem.group_contexts WHERE group_id = :g AND source = 'owner'"), {"g": world["group"]})).scalar_one() == 0


# ─── review bảo mật T2 (minor 3, 4) ──────────────────────────────────────────

@pytest.mark.parametrize("body", [
    "ghi nhớ: anh nghỉ chủ nhật, l\u200bừa đảo cũng kệ",        # ZWSP chen giữa chữ (ký tự Cf)
    "ghi nhớ: anh nghỉ chủ nhật, l\u2060ừa đảo cũng kệ",        # WORD JOINER
    "ghi nhớ: anh nghỉ chủ nhật, \uff4c\u1eeba đảo cũng kệ",    # chữ full-width (NFKC)
    "ghi nhớ: anh muốn gian lận chuyển tiền sau 9h thì sáng mai hẵng báo",
    "ghi nhớ: anh 22h trở đi chỉ báo việc gấp",                 # report_prefs + "báo" + khung giờ ⇒ Console
])
async def test_defensive_filters_resist_invisible_chars_and_time_windows(calm: Chat, db: Any, body: str) -> None:
    reply = await calm.say(body)
    assert reply == owner_core.PROFILE_CONSOLE, reply
    assert await profile_facts(db, calm.org) == []


@pytest.mark.parametrize("body", ["ghi nhớ: anh Tuấn ưu tiên mở quán thứ 2 trong quý này",
                                  "ghi nhớ: chị Lan không nhận đơn dưới 10 triệu",
                                  "ghi nhớ: em Hùng nghỉ chủ nhật"])
async def test_note_about_a_named_person_is_not_written_to_owner_profile(calm: Chat, db: Any, body: str) -> None:
    await calm.say(body)
    assert await profile_facts(db, calm.org) == []



# ─── review bảo mật T2 (đợt 2): chữ lấp vô hình / homoglyph / dấu chen giữa; giờ viết kiểu khác ──────────

@pytest.mark.parametrize("body", [
    "ghi nhớ: anh ưu tiên mở quán, l\u3164ừa đảo cũng kệ",       # HANGUL FILLER
    "ghi nhớ: anh ưu tiên mở quán, l\u115fừa đảo cũng kệ",       # HANGUL CHOSEONG FILLER
    "ghi nhớ: anh ưu tiên mở quán, l\u2800ừa đảo cũng kệ",       # BRAILLE BLANK
    "ghi nhớ: anh ưu tiên mở quán, l\uffa0ừa đảo cũng kệ",       # HALFWIDTH HANGUL FILLER
    "ghi nhớ: anh ưu tiên mở quán, lừ\u0430 đảo cũng kệ",        # 'а' Cyrillic
    "ghi nhớ: anh ưu tiên mở quán, chuyển kh\u03bfản lạ cũng kệ",  # 'ο' Greek
    "ghi nhớ: anh ưu tiên mở quán, l.ừa đảo cũng kệ",
    "ghi nhớ: anh ưu tiên mở quán, lừa-đảo cũng kệ",
    "ghi nhớ: anh ưu tiên mở quán, l ừ a đ ả o cũng kệ",
])
async def test_obfuscated_defensive_words_fail_closed_to_console(calm: Chat, db: Any, body: str) -> None:
    reply = await calm.say(body)
    assert reply == owner_core.PROFILE_CONSOLE, reply
    assert await profile_facts(db, calm.org) == []


@pytest.mark.parametrize("body", [
    "ghi nhớ: anh nghỉ chủ nhật",                                  # rhythm qua chat ⇒ Console (deny-by-default)
    "ghi nhớ: anh 8h30 mới cần báo",
    "ghi nhớ: anh tám giờ tối trở đi khỏi báo",
    "ghi nhớ: anh ban đêm chỉ báo việc gấp",
    "ghi nhớ: anh 8:30 pm trở đi chỉ báo việc gấp",
])
async def test_report_prefs_and_rhythm_from_chat_always_console(calm: Chat, db: Any, body: str) -> None:
    reply = await calm.say(body)
    assert reply == owner_core.PROFILE_CONSOLE, reply
    assert await profile_facts(db, calm.org) == []
