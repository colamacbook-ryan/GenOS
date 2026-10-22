# ruff: noqa: E501
"""v0.1.72 (G4): cửa duy nhất `send_auto` — ma trận basis × điều kiện sai ⇒ từ chối và KHÔNG có tin nào ra bridge."""

import base64
import hashlib
import json
import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import text

from gh import crypto
from gh.chassis.bus import BRIDGE_OUTBOUND
from gh.db import sessionmaker
from gh.owner_channel import autosend
from gh.owner_channel.basis import Basis, Trigger
from tests.owner_helpers import OWNER_ZALO, FakeBus, bot_world, enable_support, make_session, pair_owner
from tests.phase2 import listen, msg, org_id, put

CLEAN = "Dạ em đã ghi nhận, bên em sẽ phản hồi sớm ạ."


def decode_permit(token: str) -> dict[str, Any]:
    head, sig = token.split(".")
    assert base64.urlsafe_b64decode(sig + "=" * (-len(sig) % 4)) == crypto.hmac_sign(head.encode())
    return json.loads(base64.urlsafe_b64decode(head + "=" * (-len(head) % 4)))  # type: ignore[no-any-return]


async def send(org: uuid.UUID, bus: FakeBus, redis: Any, **kw: Any) -> autosend.AutoSendResult:
    kw.setdefault("channel", "zalo")
    async with sessionmaker()() as s:
        return await autosend.send_auto(s, bus, redis, org_id=org, **kw)


async def outbox(db: Any, org: uuid.UUID, channel: str = "zalo", sev: str = "important") -> str:
    oid = (await db.execute(text("""INSERT INTO ops.owner_outbox (org_id, channel_type, kind, severity, dedupe_key, text)
                                    VALUES (:o, :c, 'alert', :s, :d, 'x') RETURNING id"""),
                            {"o": org, "c": channel, "s": sev, "d": uuid.uuid4().hex})).scalar_one()
    await db.commit()
    return str(oid)


async def event(org: uuid.UUID, group: str, sender: str, body: str = "xin chào", mentions: bool = True,
                name: str = "Chị Lan") -> str:
    [eid] = await put(sessionmaker(), org, msg(body, group=group, sender=sender, name=name, mentions=mentions))
    assert eid is not None
    return str(eid)


async def ledger(db: Any) -> list[Any]:
    await db.rollback()
    return list((await db.execute(text("SELECT * FROM ops.auto_sends ORDER BY created_at"))).all())


# ─── owner_dm ────────────────────────────────────────────────────────────────

async def test_owner_dm_report_ok_writes_ledger_actionlog_and_signed_permit(app, db, redis) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    sid = await bot_world(db, org)
    bus = FakeBus()
    oid = await outbox(db, org)
    r = await send(org, bus, redis, basis=Basis.OWNER_DM, trigger=Trigger("report", oid), text=CLEAN)
    assert r.ok and r.auto_send_id is not None
    [(typ, p)] = bus.on(BRIDGE_OUTBOUND)
    assert typ == "message.send" and p["text"] == CLEAN and p["thread_id"] == OWNER_ZALO and p["thread_type"] == "user"
    assert p["session_id"] == str(sid) and p["auto_send_id"] == str(r.auto_send_id)
    claims = decode_permit(p["permit"])
    assert claims["basis"] == "owner_dm" and claims["thread_id"] == OWNER_ZALO and claims["thread_type"] == "user"
    assert claims["body_sha256"] == hashlib.sha256(CLEAN.encode()).hexdigest() and "draft_id" not in claims
    assert claims["auto_send_id"] == str(r.auto_send_id) and claims["exp"] > time.time()
    [row] = await ledger(db)
    assert (row.basis, row.status, row.thread_id, row.trigger_kind, row.body_len) == ("owner_dm", "issued", OWNER_ZALO, "report", len(CLEAN))
    assert row.body_sha256 == claims["body_sha256"] and CLEAN not in json.dumps(row.guard)        # sổ không lưu thân tin
    al = (await db.execute(text("SELECT action, detail FROM ops.action_log WHERE action = 'autosend.issued'"))).one()
    assert al.detail["basis"] == "owner_dm" and CLEAN not in json.dumps(al.detail)


async def test_owner_dm_reply_needs_fresh_command_marker(app, db, redis) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    await bot_world(db, org)
    bus = FakeBus()
    ref = "zalo:m1"
    assert (await send(org, bus, redis, basis=Basis.OWNER_DM, trigger=Trigger("owner_message", ref), text=CLEAN)).code == "TRIGGER_NOT_FOUND"
    await redis.set("gh:owner:dm:zalo:m1", str(int(time.time())))
    assert (await send(org, bus, redis, basis=Basis.OWNER_DM, trigger=Trigger("owner_message", ref), text=CLEAN,
                       now=datetime.now(UTC) + timedelta(minutes=11))).code == "TRIGGER_TOO_OLD"
    assert not bus.events
    r = await send(org, bus, redis, basis=Basis.OWNER_DM, trigger=Trigger("owner_message", ref), text=CLEAN)
    assert r.ok and len(bus.on(BRIDGE_OUTBOUND)) == 1
    assert (await send(org, bus, redis, basis=Basis.OWNER_DM, trigger=Trigger("owner_message", ref), text=CLEAN)).code == "DUPLICATE_TRIGGER"
    assert len(bus.on(BRIDGE_OUTBOUND)) == 1                                                       # một tin kích hoạt ⇒ một trả lời


@pytest.mark.parametrize("case", ["not_bot", "unpaired", "disabled", "session_ended", "channel_telegram", "group_given",
                                  "too_long", "empty", "report_missing", "report_sent", "wa_session_for_zalo"])
async def test_owner_dm_refusals_send_nothing(app, db, redis, case: str) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    sid = await bot_world(db, org)
    bus = FakeBus()
    oid = await outbox(db, org)
    kw: dict[str, Any] = {"trigger": Trigger("report", oid), "text": CLEAN, "channel": "zalo"}
    if case == "not_bot":
        await db.execute(text("UPDATE core.channel_sessions SET account_kind = 'owner_self'"))
    elif case == "unpaired":
        await db.execute(text("DELETE FROM ops.owner_channels"))
    elif case == "disabled":
        await db.execute(text("UPDATE ops.owner_channels SET enabled = false"))
    elif case == "session_ended":
        await db.execute(text("UPDATE core.channel_sessions SET state = 'error'"))
    elif case == "channel_telegram":
        kw["channel"] = "telegram"
    elif case == "group_given":
        kw["group_id"] = uuid.uuid4()
    elif case == "too_long":
        kw["text"] = "x" * 4000
    elif case == "empty":
        kw["text"] = "  "
    elif case == "report_missing":
        kw["trigger"] = Trigger("report", str(uuid.uuid4()))
    elif case == "report_sent":
        await db.execute(text("UPDATE ops.owner_outbox SET sent_at = now()"))
    elif case == "wa_session_for_zalo":
        await make_session(db, org, "whatsapp")
        kw["channel"] = "whatsapp"
    await db.commit()
    r = await send(org, bus, redis, basis=Basis.OWNER_DM, **kw)
    assert not r.ok and r.code and not bus.events and not await ledger(db), (case, r)
    assert sid


# ─── owner_tag ───────────────────────────────────────────────────────────────

async def tag_world(db: Any, org: uuid.UUID, mode: str = "silent") -> tuple[uuid.UUID, str]:
    await bot_world(db, org)
    gid = await listen(db, org, "g1", mode=mode)
    return gid, await event(org, "g1", OWNER_ZALO, "@bot tóm tắt giúp", name="Sếp Ryan")


async def test_owner_tag_ok_even_in_silent_group(app, db, redis) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    gid, eid = await tag_world(db, org, "silent")
    bus = FakeBus()
    r = await send(org, bus, redis, basis=Basis.OWNER_TAG, trigger=Trigger("raw_event", eid), text=CLEAN, group_id=gid)
    assert r.ok
    [(_, p)] = bus.on(BRIDGE_OUTBOUND)
    claims = decode_permit(p["permit"])
    assert (claims["basis"], claims["thread_id"], claims["thread_type"]) == ("owner_tag", "g1", "group")
    [row] = await ledger(db)
    assert row.basis == "owner_tag" and row.group_id == gid and row.trigger_ref == eid


@pytest.mark.parametrize("case", ["name_only_owner", "no_mention", "group_mismatch", "off_group", "left_group", "stale",
                                  "not_bot", "price_blocked", "phone_blocked", "owner_not_paired", "bad_trigger", "no_group_id"])
async def test_owner_tag_refusals_send_nothing(app, db, redis, case: str) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    gid, eid = await tag_world(db, org)
    bus = FakeBus()
    kw: dict[str, Any] = {"trigger": Trigger("raw_event", eid), "text": CLEAN, "group_id": gid}
    if case == "name_only_owner":
        kw["trigger"] = Trigger("raw_event", await event(org, "g1", "999", "@bot", name="Sếp Ryan"))
    elif case == "no_mention":
        kw["trigger"] = Trigger("raw_event", await event(org, "g1", OWNER_ZALO, "chào", mentions=False))
    elif case == "group_mismatch":
        kw["group_id"] = await listen(db, org, "g2")
    elif case == "off_group":
        await db.execute(text("UPDATE core.groups SET listen_mode = 'off'"))
    elif case == "left_group":
        await db.execute(text("UPDATE core.groups SET left_at = now()"))
    elif case == "stale":
        kw["now"] = datetime.now(UTC) + timedelta(minutes=11)
    elif case == "not_bot":
        await db.execute(text("UPDATE core.channel_sessions SET account_kind = 'owner_self'"))
    elif case == "price_blocked":
        kw["text"] = "Dạ giá 150k ạ"
    elif case == "phone_blocked":
        kw["text"] = "Gọi 0901234567 ạ"
    elif case == "owner_not_paired":
        await db.execute(text("UPDATE ops.owner_channels SET enabled = false"))
    elif case == "bad_trigger":
        kw["trigger"] = Trigger("raw_event", "không-phải-uuid")
    elif case == "no_group_id":
        kw["group_id"] = None
    await db.commit()
    r = await send(org, bus, redis, basis=Basis.OWNER_TAG, **kw)
    assert not r.ok and r.code and not bus.events, (case, r)
    assert not await ledger(db)


async def test_owner_tag_guard_block_is_logged_without_text(app, db, redis) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    gid, eid = await tag_world(db, org)
    r = await send(org, FakeBus(), redis, basis=Basis.OWNER_TAG, trigger=Trigger("raw_event", eid), text="Báo giá 150k nhé", group_id=gid)
    assert r.code == "GUARD_BLOCKED" and "G-MONEY" in r.rules
    al = (await db.execute(text("SELECT detail, result FROM ops.action_log WHERE action = 'autosend.refused'"))).one()
    assert al.result == "blocked" and "G-MONEY" in al.detail["rules"] and "150" not in json.dumps(al.detail)


# ─── support ─────────────────────────────────────────────────────────────────

async def sup_world(db: Any, org: uuid.UUID) -> uuid.UUID:
    await bot_world(db, org)
    gid = await listen(db, org, "g1", mode="silent")
    await enable_support(db, gid)
    return gid


async def test_support_ok_for_member_question(app, db, redis) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    gid = await sup_world(db, org)
    eid = await event(org, "g1", "777", "@bot cửa hàng mở mấy giờ?")
    bus = FakeBus()
    r = await send(org, bus, redis, basis=Basis.SUPPORT, trigger=Trigger("raw_event", eid), text=CLEAN, group_id=gid)
    assert r.ok
    claims = decode_permit(bus.on(BRIDGE_OUTBOUND)[0][1]["permit"])
    assert (claims["basis"], claims["thread_id"], claims["thread_type"]) == ("support", "g1", "group")


@pytest.mark.parametrize("case", ["not_enabled", "disabled", "expired", "sender_is_owner", "stale", "other_group", "not_bot",
                                  "guard_money", "guard_other_person", "too_long", "left_group", "group_not_listening"])
async def test_support_refusals_send_nothing(app, db, redis, case: str) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    gid = await sup_world(db, org)
    eid = await event(org, "g1", "777", "@bot hỏi")
    other = await event(org, "g1", "778", "@bot hỏi nữa")                    # người 778 sẽ là «người ngoài nhóm khác»
    bus = FakeBus()
    kw: dict[str, Any] = {"trigger": Trigger("raw_event", eid), "text": CLEAN, "group_id": gid}
    if case == "not_enabled":
        await db.execute(text("DELETE FROM ops.group_support"))
    elif case == "disabled":
        await db.execute(text("UPDATE ops.group_support SET disabled_at = now(), disabled_reason = 'owner'"))
    elif case == "expired":
        await db.execute(text("UPDATE ops.group_support SET until_at = now() - interval '1 minute'"))
    elif case == "sender_is_owner":
        kw["trigger"] = Trigger("raw_event", await event(org, "g1", OWNER_ZALO, "@bot hỏi"))
    elif case == "stale":
        kw["now"] = datetime.now(UTC) + timedelta(seconds=301)
    elif case == "other_group":
        g2 = await listen(db, org, "g2")
        await enable_support(db, g2)
        kw["group_id"] = g2                                                    # trigger thuộc g1, đích g2
    elif case == "not_bot":
        await db.execute(text("UPDATE core.channel_sessions SET account_kind = 'owner_self'"))
    elif case == "guard_money":
        kw["text"] = "Dạ giá là 3 triệu ạ"
    elif case == "guard_other_person":
        await db.execute(text("""INSERT INTO core.persons (org_id, code, display_name, relation_to_owner)
                                 VALUES (:o, 'PER-9999', 'Trần Quốc Bảo', 'stranger')"""), {"o": org})
        kw["text"] = "Dạ anh Trần Quốc Bảo đã hỏi trước ạ"
    elif case == "too_long":
        kw["text"] = "dạ " * 300
    elif case == "left_group":
        await db.execute(text("UPDATE core.groups SET left_at = now()"))
    elif case == "group_not_listening":
        await db.execute(text("UPDATE core.groups SET listen_mode = 'paused'"))
    await db.commit()
    r = await send(org, bus, redis, basis=Basis.SUPPORT, **kw)
    assert not r.ok and r.code and not bus.events and not await ledger(db), (case, r)
    assert other


async def test_support_limits_member_group_gap_and_channel_day(app, db, redis) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    gid = await sup_world(db, org)
    await db.execute(text("""UPDATE core.organizations SET settings = settings || '{"support_limits": {"per_group_hour": 4,
        "per_member_hour": 2, "min_gap_s": 20, "per_channel_day": 5, "max_len": 600, "max_age_s": 300}}'::jsonb"""))
    await db.commit()
    bus = FakeBus()
    base = datetime.now(UTC)
    eids = [await event(org, "g1", "777", f"@bot câu {i}") for i in range(3)]
    t = 0
    r1 = await send(org, bus, redis, basis=Basis.SUPPORT, trigger=Trigger("raw_event", eids[0]), text=CLEAN, group_id=gid, now=base)
    assert r1.ok
    r2 = await send(org, bus, redis, basis=Basis.SUPPORT, trigger=Trigger("raw_event", eids[1]), text=CLEAN, group_id=gid, now=base + timedelta(seconds=5))
    assert r2.code == "RATE_GAP"                                                               # cách nhau ≥ 20 giây
    r3 = await send(org, bus, redis, basis=Basis.SUPPORT, trigger=Trigger("raw_event", eids[1]), text=CLEAN, group_id=gid, now=base + timedelta(seconds=30))
    assert r3.ok
    r4 = await send(org, bus, redis, basis=Basis.SUPPORT, trigger=Trigger("raw_event", eids[2]), text=CLEAN, group_id=gid, now=base + timedelta(seconds=60))
    assert r4.code == "RATE_MEMBER"                                                            # 2 tin / giờ / người
    other = await event(org, "g1", "888", "@bot hỏi")
    other2 = await event(org, "g1", "889", "@bot hỏi")
    other3 = await event(org, "g1", "890", "@bot hỏi")
    ok1 = await send(org, bus, redis, basis=Basis.SUPPORT, trigger=Trigger("raw_event", other), text=CLEAN, group_id=gid, now=base + timedelta(seconds=90))
    ok2 = await send(org, bus, redis, basis=Basis.SUPPORT, trigger=Trigger("raw_event", other2), text=CLEAN, group_id=gid, now=base + timedelta(seconds=120))
    assert ok1.ok and ok2.ok
    r5 = await send(org, bus, redis, basis=Basis.SUPPORT, trigger=Trigger("raw_event", other3), text=CLEAN, group_id=gid, now=base + timedelta(seconds=150))
    assert r5.code == "RATE_GROUP"                                                             # 4 tin / giờ / nhóm
    assert len(bus.on(BRIDGE_OUTBOUND)) == 4 and t == 0
    assert len(await ledger(db)) == 4


async def test_support_channel_day_limit(app, db, redis) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    gid = await sup_world(db, org)
    await db.execute(text("""UPDATE core.organizations SET settings = settings || '{"support_limits": {"per_group_hour": 100,
        "per_member_hour": 100, "min_gap_s": 0, "per_channel_day": 2, "max_len": 600, "max_age_s": 300}}'::jsonb"""))
    await db.commit()
    bus = FakeBus()
    res = []
    for i in range(3):
        eid = await event(org, "g1", f"u{i}", "@bot hỏi")
        res.append(await send(org, bus, redis, basis=Basis.SUPPORT, trigger=Trigger("raw_event", eid), text=CLEAN, group_id=gid))
    assert [r.ok for r in res] == [True, True, False] and res[2].code == "RATE_CHANNEL_DAY"


# ─── hạ tầng ─────────────────────────────────────────────────────────────────

async def test_on_send_result_updates_ledger_once(app, db, redis) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    await bot_world(db, org)
    r = await send(org, FakeBus(), redis, basis=Basis.OWNER_DM, trigger=Trigger("report", await outbox(db, org)), text=CLEAN)
    async with sessionmaker()() as s:
        await autosend.on_send_result(s, org, {"auto_send_id": str(r.auto_send_id), "ok": True, "external_msg_id": "zm1"})
        await autosend.on_send_result(s, org, {"auto_send_id": str(r.auto_send_id), "ok": False, "error": "X"})   # lần hai bị bỏ
        await autosend.on_send_result(s, org, {"auto_send_id": "không-phải-uuid", "ok": True})
        await s.commit()
    [row] = await ledger(db)
    assert (row.status, row.external_msg_id, row.error) == ("sent", "zm1", None) and row.sent_at is not None


async def test_failed_send_result_is_recorded(app, db, redis) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    await bot_world(db, org)
    r = await send(org, FakeBus(), redis, basis=Basis.OWNER_DM, trigger=Trigger("report", await outbox(db, org)), text=CLEAN)
    async with sessionmaker()() as s:
        await autosend.on_send_result(s, org, {"auto_send_id": str(r.auto_send_id), "ok": False, "error": "PERMIT_MISMATCH"})
        await s.commit()
    [row] = await ledger(db)
    assert (row.status, row.error) == ("failed", "PERMIT_MISMATCH")


async def test_expire_stale_marks_unfinished_rows_expired(app, db, redis) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    await bot_world(db, org)
    await send(org, FakeBus(), redis, basis=Basis.OWNER_DM, trigger=Trigger("report", await outbox(db, org)), text=CLEAN)
    async with sessionmaker()() as s:
        assert await autosend.expire_stale(s, now=datetime.now(UTC)) == 0
        assert await autosend.expire_stale(s, now=datetime.now(UTC) + timedelta(minutes=10)) == 1
        await s.commit()
    assert (await ledger(db))[0].status == "expired"


async def test_publish_failure_marks_ledger_failed(app, db, redis) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    await bot_world(db, org)

    class Boom(FakeBus):
        async def publish(self, *a: Any, **k: Any) -> str:
            raise RuntimeError("redis down")

    r = await send(org, Boom(), redis, basis=Basis.OWNER_DM, trigger=Trigger("report", await outbox(db, org)), text=CLEAN)
    assert not r.ok and r.code == "PUBLISH_FAILED"
    assert (await ledger(db))[0].status == "failed"


async def test_second_org_cannot_borrow_first_org_state(app, db, redis) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    await bot_world(db, org)
    oid = await outbox(db, org)
    r = await send(uuid.uuid4(), FakeBus(), redis, basis=Basis.OWNER_DM, trigger=Trigger("report", oid), text=CLEAN)
    assert not r.ok
    assert pair_owner and make_session
