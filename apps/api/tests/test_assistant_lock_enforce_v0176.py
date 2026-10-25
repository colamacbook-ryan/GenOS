# ruff: noqa: E501
"""v0.1.76 (gói T2, việc nối từ review T10) — cờ "khoá trợ lý" (`settings.intel.assistant_locked_at`) được THỰC THI ở các đường của T2:
ghép lại kênh quản trị (bắt đầu ghép + nhận mã), lệnh bật hỗ trợ nhóm qua chat (/ho_tro … bat, /xac_nhan), tin 1-1 vào kênh quản trị.
Tấn công: đang khoá thì ghép lại kênh hay gõ lệnh bật hỗ trợ đều KHÔNG mở lại được; tắt hỗ trợ (hướng an toàn) vẫn làm được."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import text

from gh.db import sessionmaker
from gh.owner_channel import group_cmds, pairing
from gh.owner_channel import inbound as oin
from gh.telegram import backup
from tests.owner_helpers import OWNER_ZALO, ago, bot_world, enable_support, make_session
from tests.phase2 import listen, org_id
from tests.test_owner_channel_v0172 import _start_in_tx, cmd, pair_payload
from tests.test_watchdog_backup_v0176 import _user

SM = sessionmaker


async def lock(db: Any, org: uuid.UUID) -> None:
    owner = await _user(db, org, "owner", f"sep-{uuid.uuid4().hex[:6]}@x.vn")
    assert (await backup.lock_assistant(db, org, via="console", user_id=owner)).ok
    await db.commit()


async def test_locked_blocks_start_pairing_and_code_pairing(app: Any, db: Any, redis: Any) -> None:
    org = await org_id(db)
    sid = await make_session(db, org, "zalo")
    code = await _start_in_tx(redis, org, "zalo", commit=True)        # mã xin TRƯỚC khi khoá
    await lock(db, org)
    with pytest.raises(Exception, match="ASSISTANT_LOCKED|khoá"):
        await _start_in_tx(redis, org, "zalo")
    async with SM()() as s:
        assert await pairing.try_pair(s, redis, org, pair_payload(sid, code)) == (False, "assistant_locked")
        assert not await oin.handle_owner_pair(s, org, pair_payload(sid, code))
    oc = (await db.execute(text("SELECT enabled, owner_external_id FROM ops.owner_channels WHERE org_id = :o"), {"o": org})).one()
    assert not oc.enabled and oc.owner_external_id is None


async def test_locked_blocks_support_on_and_confirm_but_allows_off(app: Any, db: Any, redis: Any) -> None:
    org = await org_id(db)
    await bot_world(db, org)
    gid = await listen(db, org, "g1")
    await db.execute(text("UPDATE core.groups SET added_by_kind = 'owner'"))
    await db.commit()
    code_g = (await db.execute(text("SELECT code FROM core.groups WHERE id = :g"), {"g": gid})).scalar_one()
    out = await cmd(redis, org, "ho_tro", f"{code_g} bat")
    confirm = out.split("/xac_nhan ")[1].split()[0]                  # xin bật TRƯỚC khi khoá
    await lock(db, org)
    assert "khoá" in await cmd(redis, org, "ho_tro", f"{code_g} bat")
    assert "khoá" in await cmd(redis, org, "xac_nhan", confirm)
    assert (await db.execute(text("SELECT count(*) FROM ops.group_support WHERE disabled_at IS NULL"))).scalar() == 0
    await enable_support(db, gid)                                      # (giả: một lượt còn sót) — tắt vẫn được
    assert "Đã tắt" in await cmd(redis, org, "ho_tro", f"{code_g} tat")


async def test_locked_owner_dm_is_ignored_even_if_channel_re_enabled(app: Any, db: Any, redis: Any) -> None:
    org = await org_id(db)
    sid = await bot_world(db, org)
    await lock(db, org)
    await db.execute(text("UPDATE ops.owner_channels SET enabled = true WHERE org_id = :o"), {"o": org})   # bật lại bằng đường khác
    await db.commit()
    p = {"channel": "zalo", "session_id": str(sid), "occurred_at": ago(seconds=1), "kind": "text", "body_text": "OK",
         "forwarded": False, "external_msg_id": uuid.uuid4().hex, "sender_external_id": OWNER_ZALO, "sender_alt_id": None}
    async with SM()() as s:
        assert not await oin.handle_owner_dm(s, redis, org, p)
        assert oin.take_pending(s) == []


async def test_unlocked_paths_still_work(app: Any, db: Any, redis: Any) -> None:
    org = await org_id(db)
    sid = await make_session(db, org, "zalo")
    code = await _start_in_tx(redis, org, "zalo", commit=True)
    async with SM()() as s:
        assert await pairing.try_pair(s, redis, org, pair_payload(sid, code)) == (True, None)
    assert group_cmds.KINDS


# ─── cổng "Owner còn hợp lệ" cho MỌI lệnh nhóm (Gen quyết, lỗ hổng có từ trước) ────────────────

@pytest.mark.parametrize("kind,args", [("nhom", ""), ("nghe", "ABCD"), ("bo", "ABCD"), ("ho_tro", ""), ("ho_tro", "1 bat"),
                                       ("ho_tro", "1 tat"), ("xac_nhan", "ABCD"), ("boi_canh", "1 gia đình")])
async def test_every_group_command_needs_a_still_valid_owner(app: Any, db: Any, redis: Any, kind: str, args: str) -> None:
    org = await org_id(db)
    await bot_world(db, org)
    uid = (await db.execute(text("SELECT owner_user_id FROM ops.owner_channels WHERE org_id = :o"), {"o": org})).scalar_one()
    assert uid is not None                                              # fixture ghép Owner THẬT
    await db.execute(text("UPDATE core.users SET is_active = false WHERE id = :u"), {"u": uid})
    await db.commit()
    assert "ghép lại" in await cmd(redis, org, kind, args)
    assert kind in group_cmds.OWNER_CHECKED
