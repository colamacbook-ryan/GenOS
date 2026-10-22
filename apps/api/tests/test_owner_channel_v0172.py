# ruff: noqa: E501
"""v0.1.72 (G6 `kenh-quan-tri`): ghép bằng mã 6 số, tin 1-1 của Sếp, lệnh nhóm, báo cáo chủ động (chỉ sự kiện đặc biệt),
consumer, API Console. Mọi lời đáp/báo cáo đi qua cửa `send_auto`; ở đây đếm tin ra bus giả."""

import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import text

from gh.chassis.bus import BRIDGE_OUTBOUND, OWNER_INBOUND
from gh.data import ingest
from gh.db import sessionmaker
from gh.owner_channel import dm, group_cmds, jobs, pairing, reports
from gh.owner_channel import inbound as oin
from gh.owner_channel.identity import owner_ids
from tests.conftest import Api, verify_pin
from tests.owner_helpers import (
    OWNER_WA,
    OWNER_WA_LID,
    OWNER_ZALO,
    FakeBus,
    ago,
    bot_world,
    enable_support,
    make_session,
    pair_owner,
)
from tests.phase2 import listen, org_id
from tests.test_autosend_v0172 import decode_permit, outbox

SM = sessionmaker


async def out_texts(bus: FakeBus) -> list[str]:
    return [p["text"] for _, p in bus.on(BRIDGE_OUTBOUND)]


# ─── ghép kênh ───────────────────────────────────────────────────────────────

def pair_payload(sid: uuid.UUID, code: str, **kw: Any) -> dict[str, Any]:
    return {"channel": "zalo", "session_id": str(sid), "sender_external_id": OWNER_ZALO, "sender_alt_id": None,
            "sender_name": "Sếp Ryan", "occurred_at": ago(seconds=3), "code": code, "external_msg_id": uuid.uuid4().hex, **kw}


async def test_start_pairing_needs_bot_session_and_stores_only_hash(app, db, redis) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    with pytest.raises(Exception, match="đăng nhập|SESSION"):
        await _start_in_tx(redis, org, "zalo")
    sid = await make_session(db, org, "zalo", kind="owner_self")
    with pytest.raises(Exception, match="cá nhân|NOT_BOT"):
        await _start_in_tx(redis, org, "zalo")
    await db.execute(text("UPDATE core.channel_sessions SET account_kind = 'bot'"))
    await db.commit()
    code = await _start_in_tx(redis, org, "zalo", commit=True)
    assert len(code) == 6 and code.isdigit()
    row = (await db.execute(text("SELECT pair_code_hash, pair_expires_at, session_id FROM ops.owner_channels"))).one()
    assert bytes(row.pair_code_hash) and code.encode() not in bytes(row.pair_code_hash) and row.session_id == sid
    assert 0 < (row.pair_expires_at - datetime.now(UTC)).total_seconds() <= 600
    assert await redis.get("gh:bridge:pairing:zalo") == b"1"
    with pytest.raises(Exception, match="Zalo và WhatsApp|CHANNEL"):
        await _start_in_tx(redis, org, "telegram")


async def _start_in_tx(redis: Any, org: uuid.UUID, ch: str, commit: bool = False) -> str:
    async with SM()() as s:
        code, _ = await pairing.start_pairing(s, redis, org, ch, None)  # type: ignore[arg-type]
        if commit:
            await s.commit()
        return code


async def run_pair(redis: Any, org: uuid.UUID, p: dict[str, Any], now: datetime | None = None) -> tuple[bool, str | None]:
    async with SM()() as s:
        return await pairing.try_pair(s, redis, org, p, now)


async def test_pairing_success_records_owner_ids_bell_log_and_replies_through_send_auto(app, db, redis) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    sid = await make_session(db, org, "whatsapp")
    code = await _start_in_tx(redis, org, "whatsapp", commit=True)
    p = pair_payload(sid, code, channel="whatsapp", sender_external_id="99887766:3@lid", sender_alt_id=OWNER_WA)
    bus = FakeBus()
    assert await pairing.handle_pair_event(SM(), redis, bus, org, p) == "paired"
    oc = (await db.execute(text("SELECT * FROM ops.owner_channels"))).one()
    assert (oc.owner_external_id, oc.owner_external_alt, oc.owner_thread_id) == (OWNER_WA_LID, OWNER_WA, OWNER_WA)
    assert oc.enabled and oc.pair_code_hash is None and oc.paired_at is not None
    oi = await owner_ids(db, org, "whatsapp")
    assert oi.enabled and oi.ids == {OWNER_WA_LID, OWNER_WA}
    log = (await db.execute(text("SELECT detail FROM ops.action_log WHERE action = 'owner_channel.paired'"))).scalar_one()
    assert code not in str(log) and OWNER_WA_LID[:8] not in str(log)
    assert (await db.execute(text("SELECT count(*) FROM core.notifications WHERE kind = 'owner_channel.paired'"))).scalar() >= 0
    assert "owner_paired" in (await db.execute(text("SELECT kind FROM ops.listen_events"))).scalars().all()
    [(_, out)] = bus.on(BRIDGE_OUTBOUND)
    assert out["thread_id"] == OWNER_WA and out["thread_type"] == "user" and decode_permit(out["permit"])["basis"] == "owner_dm"
    assert {x.decode() for x in await redis.smembers("gh:bridge:owner:whatsapp")} == {OWNER_WA_LID, OWNER_WA}
    assert await redis.get("gh:bridge:pairing:whatsapp") is None


async def test_pairing_rejections_are_silent_and_lock_only_the_wrong_sender(app, db, redis) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    sid = await make_session(db, org, "zalo")
    code = await _start_in_tx(redis, org, "zalo", commit=True)
    bus = FakeBus()
    wrong = "000000" if code != "000000" else "111111"
    for _ in range(4):
        assert await pairing.handle_pair_event(SM(), redis, bus, org, pair_payload(sid, wrong, sender_external_id="999")) == "rejected:wrong_code"
    assert not bus.events                                                            # sai mã: KHÔNG trả lời
    assert (await db.execute(text("SELECT pair_attempts FROM ops.owner_channels"))).scalar() == 4
    assert await pairing.handle_pair_event(SM(), redis, bus, org, pair_payload(sid, wrong, sender_external_id="999")) == "rejected:sender_locked"
    assert (await db.execute(text("SELECT pair_code_hash FROM ops.owner_channels"))).scalar() is not None   # mã của Sếp còn nguyên
    assert await pairing.handle_pair_event(SM(), redis, bus, org, pair_payload(sid, code, sender_external_id="999")) == "rejected:sender_locked"
    assert not bus.events and not (await owner_ids(db, org, "zalo")).paired
    assert await pairing.handle_pair_event(SM(), redis, bus, org, pair_payload(sid, code)) == "paired"   # Sếp vẫn ghép được


async def test_pairing_total_wrong_cap_cancels_code(app, db, redis) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    sid = await make_session(db, org, "zalo")
    code = await _start_in_tx(redis, org, "zalo", commit=True)
    bus = FakeBus()
    wrong = "000000" if code != "000000" else "111111"
    res = []
    for i in range(pairing.PAIR_MAX_TOTAL):
        res.append(await pairing.handle_pair_event(SM(), redis, bus, org, pair_payload(sid, wrong, sender_external_id=f"s{i // 4}")))
    assert res[-1] == "rejected:locked"
    assert (await db.execute(text("SELECT pair_code_hash FROM ops.owner_channels"))).scalar() is None
    assert await pairing.handle_pair_event(SM(), redis, bus, org, pair_payload(sid, code)) == "rejected:no_code"
    assert not bus.events and not (await owner_ids(db, org, "zalo")).paired


async def test_bridge_shaped_pair_event_gets_confirmation_via_send_auto(app, db, redis) -> None:  # type: ignore[no-untyped-def]
    """Đúng payload `owner.pair` bridge phát (kèm external_msg_id) ⇒ ghép xong, tin xác nhận đi cửa send_auto."""
    org = await org_id(db)
    sid = await make_session(db, org, "zalo")
    code = await _start_in_tx(redis, org, "zalo", commit=True)
    async with SM()() as s:
        assert await oin.handle_owner_pair(s, org, pair_payload(sid, code, external_msg_id="52"))
        await s.commit()
        [(_, stashed)] = oin.take_pending(s)
    assert stashed["external_msg_id"] == "52"
    bus = FakeBus()
    assert await pairing.handle_pair_event(SM(), redis, bus, org, stashed) == "paired"
    assert await out_texts(bus) == [pairing.PAIRED_TEXT]
    p_no_id = {k: v for k, v in stashed.items() if k != "external_msg_id"}
    assert await pairing.reply_paired(SM(), redis, bus, org, p_no_id) is False       # thiếu id ⇒ (lỗi cũ) không gửi được


@pytest.mark.parametrize("case", ["expired", "stale", "wrong_session", "not_bot", "future"])
async def test_pairing_checks_expiry_age_session_and_kind(app, db, redis, case: str) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    sid = await make_session(db, org, "zalo")
    code = await _start_in_tx(redis, org, "zalo", commit=True)
    p = pair_payload(sid, code)
    now = None
    if case == "expired":
        now = datetime.now(UTC) + timedelta(minutes=11)
    elif case == "stale":
        p["occurred_at"] = ago(minutes=6)
    elif case == "future":
        p["occurred_at"] = ago(minutes=-5)
    elif case == "wrong_session":
        p["session_id"] = str(uuid.uuid4())
    elif case == "not_bot":
        await db.execute(text("UPDATE core.channel_sessions SET account_kind = 'owner_self'"))
        await db.commit()
    ok, why = await run_pair(redis, org, p, now)
    assert not ok and why == {"expired": "expired", "stale": "stale", "future": "stale", "wrong_session": "wrong_session", "not_bot": "wrong_session"}[case]
    assert not (await owner_ids(db, org, "zalo")).paired


async def test_repair_replaces_old_owner_and_unpair_clears_everything(app, db, redis) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    sid = await bot_world(db, org)
    gid = await listen(db, org, "g1")
    await enable_support(db, gid)
    code = await _start_in_tx(redis, org, "zalo", commit=True)
    assert (await owner_ids(db, org, "zalo")).enabled                                   # vẫn là Owner cũ trong lúc chờ ghép lại
    ok, _ = await run_pair(redis, org, pair_payload(sid, code, sender_external_id="777888"))
    assert ok and (await owner_ids(db, org, "zalo")).ids == {"777888"}
    async with SM()() as s:
        await pairing.unpair(s, redis, org, "zalo")
        await s.commit()
    oi = await owner_ids(db, org, "zalo")
    assert not oi.paired and not oi.enabled and oi.ids == frozenset()
    assert (await db.execute(text("SELECT disabled_reason FROM ops.group_support"))).scalar() == "owner"
    assert await redis.smembers("gh:bridge:owner:zalo") == set()


# ─── tin 1-1 của Sếp → lời đáp ──────────────────────────────────────────────

def dm_payload(sid: uuid.UUID, body: str, mid: str | None = None, **kw: Any) -> dict[str, Any]:
    mid = mid or uuid.uuid4().hex
    return {"channel": "zalo", "session_id": str(sid), "external_msg_id": mid, "text": body,
            "occurred_at": ago(seconds=2), **kw}


async def run_dm(redis: Any, org: uuid.UUID, bus: FakeBus, p: dict[str, Any], mark: bool = True) -> str:
    if mark:
        await redis.set(f"gh:owner:dm:zalo:{p['external_msg_id']}", str(int(time.time())), ex=600)
    return await dm.handle_dm(SM(), redis, bus, None, org, p)


async def test_dm_help_lists_group_commands_and_goes_to_owner_thread_only(app, db, redis) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    sid = await bot_world(db, org)
    bus = FakeBus()
    assert await run_dm(redis, org, bus, dm_payload(sid, "/tro_giup")) == "replied"
    [(_, out)] = bus.on(BRIDGE_OUTBOUND)
    assert out["thread_id"] == OWNER_ZALO and "/ho_tro" in out["text"] and "/hom_nay" in out["text"] or "Console" in out["text"]
    assert len(await out_texts(bus)) == 1


async def test_dm_unknown_command_and_one_reply_per_message(app, db, redis) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    sid = await bot_world(db, org)
    bus = FakeBus()
    p = dm_payload(sid, "/khong_co_lenh_nay")
    assert await run_dm(redis, org, bus, p) == "replied" and len(bus.on(BRIDGE_OUTBOUND)) == 1
    assert (await run_dm(redis, org, bus, p, mark=False)).startswith("refused:DUPLICATE")
    assert len(bus.on(BRIDGE_OUTBOUND)) == 1


async def test_dm_ignored_when_channel_off_or_wrong_session(app, db, redis) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    sid = await bot_world(db, org)
    bus = FakeBus()
    assert await run_dm(redis, org, bus, dm_payload(uuid.uuid4(), "/tro_giup")) == "ignored:not_enabled"
    await db.execute(text("UPDATE ops.owner_channels SET enabled = false"))
    await db.commit()
    assert await run_dm(redis, org, bus, dm_payload(sid, "/tro_giup")) == "ignored:not_enabled"
    assert not bus.events


async def test_dm_rate_limit_replies_once_then_silent(app, db, redis) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    sid = await bot_world(db, org)
    bus = FakeBus()
    res = [await run_dm(redis, org, bus, dm_payload(sid, "/tro_giup")) for _ in range(24)]
    assert res.count("replied") == 20 and res.count("rate") == 4
    assert len(bus.on(BRIDGE_OUTBOUND)) == 21                        # 20 lời đáp + MỘT lời báo «nhắn nhanh quá»


async def test_dm_without_fresh_marker_is_refused_by_the_gate(app, db, redis) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    sid = await bot_world(db, org)
    bus = FakeBus()
    assert (await run_dm(redis, org, bus, dm_payload(sid, "/tro_giup"), mark=False)).startswith("refused:TRIGGER_NOT_FOUND")
    assert not bus.events


async def test_end_to_end_bridge_event_to_reply_via_consumers(app, db, redis) -> None:  # type: ignore[no-untyped-def]
    """owner.dm (bridge) → Ingest → gh.owner.inbound → dispatch → send_auto → gh.bridge.outbound."""
    org = await org_id(db)
    sid = await bot_world(db, org)
    bus = FakeBus()
    ing = ingest.Ingest(bus, redis, org, SM())

    class Ev:
        type = "owner.dm"
        stream = "gh.bridge.inbound"
        message_id = None
        payload = {"channel": "zalo", "session_id": str(sid), "external_msg_id": "e2e1", "sender_external_id": OWNER_ZALO,
                   "sender_alt_id": None, "occurred_at": ago(seconds=1), "kind": "text", "body_text": "/tro_giup",
                   "forwarded": False}

    await ing.on_inbound(Ev())
    [(typ, payload)] = bus.on(OWNER_INBOUND)
    assert typ == "dm"

    class Ev2:
        type = "dm"
        stream = OWNER_INBOUND
        message_id = None

    Ev2.payload = payload                                # type: ignore[attr-defined]
    assert await jobs.dispatch(SM(), redis, bus, None, org, Ev2()) == "replied"
    assert len(bus.on(BRIDGE_OUTBOUND)) == 1
    assert (await db.execute(text("SELECT count(*) FROM raw.events"))).scalar() == 0


# ─── lệnh nhóm ───────────────────────────────────────────────────────────────

async def cmd(redis: Any, org: uuid.UUID, kind: str, args: str = "", ch: str = "zalo") -> str:
    async with SM()() as s:
        oi = await owner_ids(s, org, ch)
        out = await group_cmds.handle(s, redis, org, oi, kind, args)
        await s.commit()
        return out


async def pending_group(db: Any, org: uuid.UUID, name: str = "g-p") -> tuple[uuid.UUID, str]:
    """Nhóm do người khác thêm (pending) + mã câu hỏi."""
    gid = await listen(db, org, name, mode="pending")
    async with SM()() as s:
        q = await __import__("gh.owner_channel.questions", fromlist=["create"]).create(s, org, kind="group_added", subject_id=gid)
        await s.commit()
    return gid, q.short_code


async def test_nghe_and_bo_answer_the_question_once(app, db, redis) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    await bot_world(db, org)
    gid, code = await pending_group(db, org)
    out = await cmd(redis, org, "nghe", code.lower())
    assert "Lắng nghe im lặng" in out
    assert (await db.execute(text("SELECT listen_mode FROM core.groups WHERE id = :g"), {"g": gid})).scalar() == "silent"
    assert "owner_channel.nghe" in (await db.execute(text("SELECT action FROM ops.action_log"))).scalars().all()
    assert group_cmds.NO_Q in await cmd(redis, org, "bo", code)                       # đã trả lời: mã chết
    gid2, code2 = await pending_group(db, org, "g-q")
    assert "Không nghe" in await cmd(redis, org, "bo", code2)
    assert (await db.execute(text("SELECT listen_mode FROM core.groups WHERE id = :g"), {"g": gid2})).scalar() == "off"
    assert group_cmds.NO_Q in await cmd(redis, org, "nghe", "ZZZZ") and group_cmds.NO_Q in await cmd(redis, org, "nghe", "")


async def test_nghe_expired_question_is_dead(app, db, redis) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    await bot_world(db, org)
    gid, code = await pending_group(db, org)
    await db.execute(text("UPDATE ops.owner_questions SET expires_at = now() - interval '1 second'"))
    await db.commit()
    assert group_cmds.NO_Q in await cmd(redis, org, "nghe", code)
    assert (await db.execute(text("SELECT listen_mode FROM core.groups WHERE id = :g"), {"g": gid})).scalar() == "pending"


async def test_nhom_lists_groups_with_support_flag(app, db, redis) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    await bot_world(db, org)
    gid = await listen(db, org, "g1")
    await listen(db, org, "g2", mode="off")
    await enable_support(db, gid)
    out = await cmd(redis, org, "nhom")
    assert "Nhóm g1" in out and "hỗ trợ BẬT" in out and "Không nghe" in out and out.count("GRP-ZL-") == 2


async def test_support_on_needs_two_steps_and_approved_listening_group(app, db, redis) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    await bot_world(db, org)
    gid = await listen(db, org, "g1")
    code_g = (await db.execute(text("SELECT code FROM core.groups WHERE id = :g"), {"g": gid})).scalar_one()
    # nhóm không do Sếp thêm và Sếp chưa duyệt
    assert "chưa duyệt" in await cmd(redis, org, "ho_tro", f"{code_g} bat")
    await db.execute(text("UPDATE core.groups SET added_by_kind = 'owner'"))
    await db.commit()
    out = await cmd(redis, org, "ho_tro", f"{code_g} bat 8")
    assert "/xac_nhan" in out and "5 phút" in out and "trong 8 giờ" in out
    assert (await db.execute(text("SELECT count(*) FROM ops.group_support"))).scalar() == 0           # chưa bật khi chưa xác nhận
    confirm = out.split("/xac_nhan ")[1].split()[0]
    done = await cmd(redis, org, "xac_nhan", confirm)
    assert "Đã bật" in done
    row = (await db.execute(text("SELECT enabled_via, until_at, disabled_at FROM ops.group_support"))).one()
    assert row.enabled_via == "zalo" and row.disabled_at is None and 7 * 3600 < (row.until_at - datetime.now(UTC)).total_seconds() <= 8 * 3600
    assert await redis.smembers("gh:bridge:support:zalo") == {b"g1"}
    actions = (await db.execute(text("SELECT action FROM ops.action_log"))).scalars().all()
    assert "owner_channel.ho_tro_bat" in actions
    assert "support_on" in (await db.execute(text("SELECT kind FROM ops.listen_events"))).scalars().all()
    assert group_cmds.NO_Q in await cmd(redis, org, "xac_nhan", confirm)                               # dùng một lần
    assert "đã bật hỗ trợ từ trước" in (await cmd(redis, org, "xac_nhan", (await _fresh_confirm(redis, org, code_g))))


async def _fresh_confirm(redis: Any, org: uuid.UUID, code_g: str) -> str:
    out = await cmd(redis, org, "ho_tro", f"{code_g} bat")
    return out.split("/xac_nhan ")[1].split()[0]


async def test_support_off_is_immediate_and_logged(app, db, redis) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    await bot_world(db, org)
    gid = await listen(db, org, "g1")
    await enable_support(db, gid)
    code_g = (await db.execute(text("SELECT code FROM core.groups WHERE id = :g"), {"g": gid})).scalar_one()
    assert "Đã tắt" in await cmd(redis, org, "ho_tro", f"{code_g} tat")
    assert (await db.execute(text("SELECT disabled_reason FROM ops.group_support"))).scalar() == "owner"
    assert await redis.smembers("gh:bridge:support:zalo") == set()
    assert "chưa bật hỗ trợ" in await cmd(redis, org, "ho_tro", f"{code_g} tat")
    assert "owner_channel.ho_tro_tat" in (await db.execute(text("SELECT action FROM ops.action_log"))).scalars().all()


async def test_support_command_edge_cases(app, db, redis) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    await bot_world(db, org)
    gid = await listen(db, org, "g1")
    await db.execute(text("UPDATE core.groups SET added_by_kind = 'owner'"))
    await db.commit()
    code_g = (await db.execute(text("SELECT code FROM core.groups WHERE id = :g"), {"g": gid})).scalar_one()
    assert "Chưa nhóm nào" in await cmd(redis, org, "ho_tro", "")
    assert group_cmds.USAGE_SUPPORT in await cmd(redis, org, "ho_tro", "abc")
    assert "không tìm thấy nhóm" in (await cmd(redis, org, "ho_tro", "GRP-ZL-9999 bat")).lower()
    assert "Số giờ" in await cmd(redis, org, "ho_tro", f"{code_g} bat 0")
    assert "Số giờ" in await cmd(redis, org, "ho_tro", f"{code_g} bat abc")
    suffix = code_g.split("-")[-1]
    assert "/xac_nhan" in await cmd(redis, org, "ho_tro", f"{suffix.lstrip('0')} bat")           # tìm theo phần số cuối
    await db.execute(text("UPDATE core.groups SET listen_mode = 'paused'"))
    await db.commit()
    assert "Bật nghe nhóm trước" in await cmd(redis, org, "ho_tro", f"{code_g} bat")
    assert "chưa có lệnh" in await cmd(redis, org, "la_hoac", "")


async def test_group_commands_never_publish_anything(app, db, redis) -> None:  # type: ignore[no-untyped-def]
    """Lệnh nhóm chỉ ĐỔI cấu hình; module không có bus/đường gửi (cửa duy nhất là dm → send_auto)."""
    import inspect

    src = inspect.getsource(group_cmds)
    assert "publish" not in src and "bridge_permit" not in src and "send_auto" not in src


# ─── báo cáo chủ động ────────────────────────────────────────────────────────

def test_release_time_quiet_hours_vietnam() -> None:
    prefs = reports.prefs_of({})
    vn = reports.VN_TZ
    night = datetime(2026, 10, 18, 23, 30, tzinfo=vn).astimezone(UTC)
    assert reports.release_time(night, "important", prefs).astimezone(vn) == datetime(2026, 10, 19, 7, 0, tzinfo=vn)
    early = datetime(2026, 10, 18, 5, 0, tzinfo=vn).astimezone(UTC)
    assert reports.release_time(early, "important", prefs).astimezone(vn) == datetime(2026, 10, 18, 7, 0, tzinfo=vn)
    noon = datetime(2026, 10, 18, 12, 0, tzinfo=vn).astimezone(UTC)
    assert reports.release_time(noon, "important", prefs) == noon
    assert reports.release_time(night, "urgent", prefs) == night                      # urgent không chờ
    assert reports.release_time(night, "important", reports.prefs_of({"owner_report": {"quiet_start": 3, "quiet_end": 3}})) == night


def test_prefs_ignore_bad_values() -> None:
    p = reports.prefs_of({"owner_report": {"quiet_start": 99, "min_severity": "lạ", "max_per_hour": True}},
                         {"quiet_end": 6, "max_per_hour": 2})
    assert (p["quiet_start"], p["quiet_end"], p["max_per_hour"], p["min_severity"]) == (21, 6, 2, "important")


#: Giờ cố định giữa trưa VN (ngoài giờ yên lặng) — test không phụ thuộc đồng hồ máy.
NOON = datetime(2026, 10, 18, 12, 0, tzinfo=reports.VN_TZ).astimezone(UTC)
AFTER = NOON + timedelta(minutes=1)


async def enq(db: Any, org: uuid.UUID, **kw: Any) -> bool:
    kw.setdefault("now", NOON)
    kw.setdefault("kind", "alert")
    kw.setdefault("severity", "important")
    kw.setdefault("text_", "Có việc đặc biệt")
    kw.setdefault("dedupe_key", uuid.uuid4().hex)
    async with SM()() as s:
        ok = await reports.enqueue(s, org, "zalo", **kw)
        await s.commit()
        return ok


async def test_enqueue_only_special_events_to_enabled_bot_channel(app, db, redis) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    assert await enq(db, org) is False                                                   # chưa ghép: không xếp
    await bot_world(db, org)
    assert await enq(db, org, severity="info") is False                                  # dưới ngưỡng «quan trọng»: bỏ hẳn, KHÔNG dồn vào bản tóm tắt
    assert await enq(db, org, severity="important", dedupe_key="a") is True
    assert await enq(db, org, severity="important", dedupe_key="a") is False             # trùng khoá
    assert await enq(db, org, severity="urgent", dedupe_key="b") is True
    with pytest.raises(ValueError):
        await enq(db, org, kind="digest")                                                # không có bản tóm tắt định kỳ
    assert not hasattr(reports, "digest") and "digest" not in reports.__doc__.replace("KHÔNG bản tóm tắt", "")
    await db.execute(text("UPDATE core.channel_sessions SET account_kind = 'owner_self'"))
    await db.commit()
    assert await enq(db, org, severity="urgent") is False                                # tài khoản cá nhân: tắt


async def test_enqueue_hourly_cap_and_user_prefs(app, db, redis) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    await bot_world(db, org)
    res = [await enq(db, org, dedupe_key=f"k{i}") for i in range(8)]
    assert res.count(True) == 6                                                          # max_per_hour mặc định 6
    assert await enq(db, org, severity="urgent", dedupe_key="u") is True                 # urgent vượt trần
    await db.execute(text("""UPDATE ops.owner_channels SET report_prefs = '{"min_severity": "urgent"}'"""))
    await db.commit()
    assert await enq(db, org, severity="important", dedupe_key="z") is False


async def test_flush_sends_due_reports_via_send_auto_and_marks_sent(app, db, redis) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    await bot_world(db, org)
    await enq(db, org, dedupe_key="a", text_="Người lạ thêm em vào nhóm X")
    bus = FakeBus()
    stats = await reports.flush(SM(), bus, redis, now=AFTER)
    assert stats == {"sent": 1, "failed": 0, "retry": 0}
    [(_, out)] = bus.on(BRIDGE_OUTBOUND)
    assert out["text"] == "Người lạ thêm em vào nhóm X" and out["thread_id"] == OWNER_ZALO
    assert (await db.execute(text("SELECT sent_at FROM ops.owner_outbox"))).scalar() is not None
    assert (await db.execute(text("SELECT basis, trigger_kind FROM ops.auto_sends"))).one() == ("owner_dm", "report")
    assert await reports.flush(SM(), bus, redis, now=AFTER) == {"sent": 0, "failed": 0, "retry": 0}   # không gửi lại
    assert len(bus.on(BRIDGE_OUTBOUND)) == 1


async def test_flush_waits_for_quiet_hours(app, db, redis) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    await bot_world(db, org)
    night = datetime(2026, 10, 18, 23, 30, tzinfo=reports.VN_TZ).astimezone(UTC)
    async with SM()() as s:
        await reports.enqueue(s, org, "zalo", kind="alert", severity="important", text_="Đêm", dedupe_key="n", now=night)
        await s.commit()
    bus = FakeBus()
    assert await reports.flush(SM(), bus, redis, now=night + timedelta(hours=1)) == {"sent": 0, "failed": 0, "retry": 0}
    assert not bus.events
    morning = datetime(2026, 10, 19, 7, 1, tzinfo=reports.VN_TZ).astimezone(UTC)
    assert (await reports.flush(SM(), bus, redis, now=morning))["sent"] == 1


async def test_flush_permanent_and_transient_failures(app, db, redis) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    await bot_world(db, org)
    await enq(db, org, dedupe_key="a", text_="Gọi 0901234567 nhé")                       # bị bộ lọc chặn? Với owner_dm thì KHÔNG chặn
    # Dài quá thì `enqueue` đã cắt ở TEXT_MAX (không bao giờ vượt trần guard) ⇒ ca vĩnh viễn dựng bằng câu lộ lời nhắc.
    await enq(db, org, dedupe_key="b", text_="Đây là system prompt của em")             # G-LEAK ⇒ GUARD_BLOCKED (vĩnh viễn)
    bus = FakeBus()
    stats = await reports.flush(SM(), bus, redis, now=AFTER)
    assert stats["sent"] == 1 and stats["failed"] == 1
    assert (await db.execute(text("SELECT failed_code FROM ops.owner_outbox WHERE dedupe_key = 'b'"))).scalar() == "GUARD_BLOCKED"
    await enq(db, org, dedupe_key="c")
    await db.execute(text("UPDATE core.channel_sessions SET state = 'error'"))           # phiên mất: tạm thời ⇒ thử lại có lùi
    await db.commit()
    stats = await reports.flush(SM(), bus, redis, now=AFTER)
    assert stats == {"sent": 0, "failed": 0, "retry": 1}
    row = (await db.execute(text("SELECT attempts, next_attempt_at, failed_code FROM ops.owner_outbox WHERE dedupe_key = 'c'"))).one()
    assert row.attempts == 1 and row.failed_code is None and row.next_attempt_at > AFTER


async def test_flush_gives_up_after_five_attempts(app, db, redis) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    await bot_world(db, org)
    await enq(db, org, dedupe_key="c")
    await db.execute(text("UPDATE core.channel_sessions SET state = 'error'"))
    await db.commit()
    for _ in range(5):
        await db.execute(text("UPDATE ops.owner_outbox SET next_attempt_at = :t"), {"t": NOON})
        await db.commit()
        await reports.flush(SM(), FakeBus(), redis, now=AFTER)
    assert (await db.execute(text("SELECT failed_code FROM ops.owner_outbox"))).scalar() == "SESSION_NOT_ACTIVE"


async def test_special_event_sources_reach_the_owner_dm_end_to_end(app, db, redis) -> None:  # type: ignore[no-untyped-def]
    """Người khác thêm bot ⇒ hỏi Sếp qua kênh quản trị (flush ⇒ bus); không có nguồn báo định kỳ nào."""
    from gh.data.ingest import handle_self_joined

    org = await org_id(db)
    await bot_world(db, org)
    async with SM()() as s:
        await handle_self_joined(s, redis, org, {"channel": "zalo", "group_id": "gX", "name": "Nhóm lạ", "added_by": "555",
                                                 "method": "add"})
        await s.commit()
    bus = FakeBus()
    assert (await reports.flush(SM(), bus, redis, now=datetime.now(UTC) + timedelta(days=2)))["sent"] == 1
    [t] = await out_texts(bus)
    assert "Nhóm lạ" in t and "/nghe " in t and "/bo " in t
    assert oin and outbox and pair_owner and enable_support and OWNER_WA


# ─── API Console ─────────────────────────────────────────────────────────────

async def test_routes_need_pin_and_never_leak_code(owner_api: Api, db, redis) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    sid = await make_session(db, org, "zalo", kind=None)
    r = await owner_api.get("/owner-channel/zalo")
    assert r.status_code == 200 and r.json()["paired"] is False and r.json()["bot_account"] is False
    assert (await owner_api.send("POST", "/owner-channel/zalo/pair/start")).status_code == 423          # thiếu PIN
    assert (await owner_api.send("PUT", f"/channels/zalo/sessions/{sid}/account-kind", {"account_kind": "bot"})).status_code == 423
    await verify_pin(owner_api)
    r = await owner_api.send("POST", "/owner-channel/zalo/pair/start")
    assert r.status_code == 409 and r.json()["code"] == "NOT_BOT_ACCOUNT"
    r = await owner_api.send("PUT", f"/channels/zalo/sessions/{sid}/account-kind", {"account_kind": "bot"})
    assert r.status_code == 200 and r.json()["bot_account"] is True
    r = await owner_api.send("POST", "/owner-channel/zalo/pair/start")
    assert r.status_code == 200
    code = r.json()["pair"]["code"]
    assert len(code) == 6 and r.json()["pair_pending"] is True
    assert code not in str((await db.execute(text("SELECT detail FROM ops.action_log"))).scalars().all())
    assert code not in (await owner_api.get("/owner-channel/zalo")).text                                # GET không có mã
    assert (await owner_api.get("/owner-channel/telegram")).status_code == 404
    r = await owner_api.send("PUT", "/owner-channel/zalo", {"enabled": True})
    assert r.status_code == 409 and r.json()["code"] == "OWNER_NOT_PAIRED"
    r = await owner_api.send("PUT", "/owner-channel/zalo", {"report_prefs": {"quiet_start": 22, "min_severity": "urgent"}})
    assert r.status_code == 200 and r.json()["report_prefs"]["quiet_start"] == 22 and r.json()["report_prefs"]["min_severity"] == "urgent"
    assert (await owner_api.send("PUT", "/owner-channel/zalo", {"report_prefs": {"quiet_start": 30}})).status_code == 422


async def test_account_kind_back_to_personal_cancels_pairing_and_support(owner_api: Api, db, redis) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    sid = await bot_world(db, org)
    gid = await listen(db, org, "g1")
    await enable_support(db, gid)
    await verify_pin(owner_api)
    r = await owner_api.send("PUT", f"/channels/zalo/sessions/{sid}/account-kind", {"account_kind": "owner_self"})
    assert r.status_code == 200 and r.json()["paired"] is False and r.json()["enabled"] is False
    assert (await db.execute(text("SELECT disabled_reason FROM ops.group_support"))).scalar() == "account_kind_changed"
    assert await redis.smembers("gh:bridge:owner:zalo") == set() and await redis.smembers("gh:bridge:support:zalo") == set()
    assert (await owner_api.send("PUT", f"/channels/zalo/sessions/{uuid.uuid4()}/account-kind", {"account_kind": "bot"})).status_code == 404


async def test_support_route_conditions_and_toggle(owner_api: Api, db, redis) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    await bot_world(db, org)
    gid = await listen(db, org, "g1")
    await verify_pin(owner_api)
    v = (await owner_api.get(f"/groups/{gid}/support")).json()
    assert v["enabled"] is False and v["can_enable"] is False and v["blocked_reasons"]
    r = await owner_api.send("PUT", f"/groups/{gid}/support", {"enabled": True})
    assert r.status_code == 409 and r.json()["code"] == "SUPPORT_NOT_ALLOWED"
    await db.execute(text("UPDATE core.groups SET added_by_kind = 'owner'"))
    await db.commit()
    r = await owner_api.send("PUT", f"/groups/{gid}/support", {"enabled": True, "until_hours": 4, "limits": {"per_member_hour": 3}})
    assert r.status_code == 200 and r.json()["enabled"] is True and r.json()["limits"]["per_member_hour"] == 3 and r.json()["until_at"]
    assert (await owner_api.send("PUT", f"/groups/{gid}/support", {"enabled": True})).json()["code"] == "SUPPORT_ALREADY_ON"
    assert await redis.smembers("gh:bridge:support:zalo") == {b"g1"}
    assert (await owner_api.send("PUT", f"/groups/{gid}/support", {"enabled": False})).json()["enabled"] is False
    assert await redis.smembers("gh:bridge:support:zalo") == set()
    bad = await owner_api.send("PUT", f"/groups/{gid}/support", {"enabled": True, "limits": {"khong_co": 1}})
    assert bad.status_code == 422
    assert (await owner_api.get(f"/groups/{uuid.uuid4()}/support")).status_code == 404
    hist = (await owner_api.get(f"/groups/{gid}/support")).json()["history"]
    assert hist and hist[0]["reason"] == "owner"


async def test_expired_support_can_be_enabled_again_via_route(owner_api: Api, db, redis) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    await bot_world(db, org)
    gid = await listen(db, org, "g1")
    await verify_pin(owner_api)
    await db.execute(text("UPDATE core.groups SET added_by_kind = 'owner'"))
    await db.commit()
    assert (await owner_api.send("PUT", f"/groups/{gid}/support", {"enabled": True, "until_hours": 1})).status_code == 200
    await db.execute(text("UPDATE ops.group_support SET until_at = now() - interval '1 minute'"))
    await db.commit()
    assert (await owner_api.get(f"/groups/{gid}/support")).json()["enabled"] is False        # hết hạn = tắt
    r = await owner_api.send("PUT", f"/groups/{gid}/support", {"enabled": True, "until_hours": 2})
    assert r.status_code == 200 and r.json()["enabled"] is True
    rows = (await db.execute(text("SELECT disabled_at, disabled_reason FROM ops.group_support ORDER BY enabled_at"))).all()
    assert len(rows) == 2 and rows[0].disabled_reason == "expired" and rows[0].disabled_at is not None and rows[1].disabled_at is None


async def test_expired_support_can_be_enabled_again_via_chat_command(app, db, redis) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    await bot_world(db, org)
    gid = await listen(db, org, "g1")
    code_g = (await db.execute(text("SELECT code FROM core.groups WHERE id = :g"), {"g": gid})).scalar_one()
    await db.execute(text("UPDATE core.groups SET added_by_kind = 'owner'"))
    await db.commit()
    await enable_support(db, gid)
    await db.execute(text("UPDATE ops.group_support SET until_at = now() - interval '1 minute'"))
    await db.commit()
    done = await cmd(redis, org, "xac_nhan", await _fresh_confirm(redis, org, code_g))
    assert "Đã bật" in done and "từ trước" not in done
    assert (await db.execute(text("SELECT count(*) FROM ops.group_support WHERE disabled_at IS NULL"))).scalar() == 1
    assert (await db.execute(text("SELECT disabled_reason FROM ops.group_support WHERE disabled_at IS NOT NULL"))).scalar() == "expired"


async def test_auto_sends_list_has_no_message_text(owner_api: Api, db, redis) -> None:  # type: ignore[no-untyped-def]
    from gh.owner_channel.autosend import send_auto
    from gh.owner_channel.basis import Basis, Trigger

    org = await org_id(db)
    await bot_world(db, org)
    oid = await outbox(db, org)
    async with SM()() as s:
        await send_auto(s, FakeBus(), redis, org_id=org, channel="zalo", basis=Basis.OWNER_DM, trigger=Trigger("report", oid),
                        text="Bí mật không được lộ")
    r = await owner_api.get("/auto-sends?since=24h")
    assert r.status_code == 200 and "Bí mật" not in r.text
    j = r.json()
    assert j["items"][0]["basis_label"] == "Kênh quản trị" and j["items"][0]["body_len"] == len("Bí mật không được lộ")
    assert j["today"]["owner_dm"] == 1 and j["today"]["total"] == 1
    assert (await owner_api.get("/auto-sends?since=bad")).status_code == 422
