# ruff: noqa: E501
"""v0.1.72 (G3 `ingest-owner`): lõi nhận sự kiện bot từ bridge — nhận diện Owner theo ID, ai thêm bot vào nhóm, tin 1-1 của
Owner (không vào Kho thô), mã ghép, Sếp tag ở nhóm, đổi phiên tài khoản bot, khoá Redis cho bridge."""

import uuid
from typing import Any

from sqlalchemy import text

from gh.chassis.bus import OWNER_INBOUND
from gh.data import ingest
from gh.data.ingest import handle_self_joined, handle_self_removed, sync_listen_sets
from gh.db import sessionmaker
from gh.owner_channel import inbound as oin
from gh.owner_channel.identity import OwnerIds, bare, is_owner, owner_ids, sync_owner_sets
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
from tests.phase2 import listen, msg, org_id

# ─── nhận diện Owner: theo ID, không theo tên ────────────────────────────────

def test_bare_normalizes_whatsapp_device_suffix_and_keeps_namespace() -> None:
    assert bare("84901234567:12@s.whatsapp.net") == "84901234567@s.whatsapp.net"
    assert bare(" 123@LID ") == "123@lid"
    assert bare("100200300") == "100200300"
    assert bare(None) == "" and bare("") == ""
    assert bare("84901234567@s.whatsapp.net") != bare("84901234567@lid")      # số điện thoại ≠ LID cùng chữ số


def test_is_owner_matches_ids_only() -> None:
    oi = OwnerIds(channel="whatsapp", ids=frozenset({bare(OWNER_WA), bare(OWNER_WA_LID)}), enabled=True, paired=True)
    assert is_owner(oi, OWNER_WA) and is_owner(oi, "x", OWNER_WA_LID) and is_owner(oi, "84901234567:3@s.whatsapp.net")
    assert not is_owner(oi, "84901234567@lid")          # cùng chữ số nhưng khác không gian ID
    assert not is_owner(oi, "Sếp Ryan")                 # tên không phải ID
    assert not is_owner(OwnerIds(channel="zalo"), OWNER_ZALO)    # chưa ghép ⇒ không ai là Owner


async def test_owner_ids_requires_bot_active_session_and_enabled(app, db) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    assert (await owner_ids(db, org, "zalo")).paired is False
    sid = await make_session(db, org, "zalo", kind="owner_self")
    await pair_owner(db, org, sid, "zalo")
    oi = await owner_ids(db, org, "zalo")
    assert oi.paired and oi.ids == {OWNER_ZALO} and oi.enabled is False        # tài khoản cá nhân ⇒ tính năng tắt
    await db.execute(text("UPDATE core.channel_sessions SET account_kind = 'bot' WHERE id = :i"), {"i": sid})
    await db.commit()
    assert (await owner_ids(db, org, "zalo")).enabled is True
    await db.execute(text("UPDATE ops.owner_channels SET enabled = false"))
    await db.commit()
    assert (await owner_ids(db, org, "zalo")).enabled is False
    await db.execute(text("UPDATE ops.owner_channels SET enabled = true"))
    await db.execute(text("UPDATE core.channel_sessions SET state = 'error' WHERE id = :i"), {"i": sid})
    await db.commit()
    assert (await owner_ids(db, org, "zalo")).enabled is False                  # phiên không còn active


# ─── khoá Redis cho bridge ───────────────────────────────────────────────────

async def test_sync_owner_sets_writes_bridge_keys_and_clears_when_not_bot(app, db, redis) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    sid = await bot_world(db, org, "whatsapp", alt=OWNER_WA_LID)
    gid = await listen(db, org, "g-sup", channel="whatsapp")
    await enable_support(db, gid)
    async with sessionmaker()() as s:
        await sync_listen_sets(s, redis, org)
    assert {x.decode() for x in await redis.smembers("gh:bridge:owner:whatsapp")} == {bare(OWNER_WA), bare(OWNER_WA_LID)}
    assert OWNER_WA.encode() in await redis.smembers("gh:bridge:owner_thread:whatsapp")
    assert await redis.smembers("gh:bridge:support:whatsapp") == {b"g-sup"}
    assert await redis.get(f"gh:bridge:account_kind:whatsapp:{sid}") == b"bot"
    assert await redis.get("gh:bridge:new_group_mode:whatsapp") == b"owner_only"
    assert await redis.get("gh:bridge:new_group_mode:zalo") == b"silent"        # kênh khác không đổi
    await db.execute(text("UPDATE core.channel_sessions SET account_kind = 'owner_self' WHERE id = :i"), {"i": sid})
    await db.commit()
    async with sessionmaker()() as s:
        await sync_owner_sets(s, redis, org)
    assert await redis.smembers("gh:bridge:owner:whatsapp") == set()
    assert await redis.smembers("gh:bridge:support:whatsapp") == set()
    assert await redis.get(f"gh:bridge:account_kind:whatsapp:{sid}") == b"owner_self"


async def test_pairing_key_lives_only_while_code_is_valid(app, db, redis) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    sid = await make_session(db, org, "zalo")
    await db.execute(text("""INSERT INTO ops.owner_channels (org_id, channel_type, session_id, pair_code_hash, pair_expires_at)
                             VALUES (:o, 'zalo', :s, '\\x01', now() + interval '10 minutes')"""), {"o": org, "s": sid})
    await db.commit()
    async with sessionmaker()() as s:
        await sync_owner_sets(s, redis, org)
    assert await redis.get("gh:bridge:pairing:zalo") == b"1" and 0 < await redis.ttl("gh:bridge:pairing:zalo") <= 600
    await db.execute(text("UPDATE ops.owner_channels SET pair_expires_at = now() - interval '1 minute'"))
    await db.commit()
    async with sessionmaker()() as s:
        await sync_owner_sets(s, redis, org)
    assert await redis.get("gh:bridge:pairing:zalo") is None


# ─── ai thêm bot vào nhóm ────────────────────────────────────────────────────

def _joined(**kw: Any) -> dict[str, Any]:
    return {"channel": "zalo", "group_id": "gJ", "name": "Nhóm J", "added_by": OWNER_ZALO, "added_by_alt": None,
            "method": "add", **kw}


async def _join(org: uuid.UUID, redis: Any, **kw: Any) -> Any:
    async with sessionmaker()() as s:
        out = await handle_self_joined(s, redis, org, _joined(**kw))
        await s.commit()
        return out


async def _grp(db: Any, gid: str = "gJ") -> Any:
    await db.rollback()
    return (await db.execute(text("""SELECT listen_mode, added_by_kind, added_by_external_id, added_at, left_at
                                     FROM core.groups WHERE external_id = :x"""), {"x": gid})).one()


async def test_bot_added_by_owner_listens_silently_by_default(app, db, redis) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    await bot_world(db, org)
    row, created = await _join(org, redis)
    g = await _grp(db)
    assert created and g.listen_mode == "silent" and g.added_by_kind == "owner" and g.added_by_external_id == OWNER_ZALO
    assert g.added_at is not None
    assert (await db.execute(text("SELECT count(*) FROM ops.owner_questions"))).scalar() == 0     # Sếp tự thêm: khỏi hỏi


async def test_bot_added_by_other_is_pending_asks_owner_and_logs(app, db, redis) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    await bot_world(db, org)
    await _join(org, redis, added_by="55555")
    g = await _grp(db)
    assert g.listen_mode == "pending" and g.added_by_kind == "other"
    q = (await db.execute(text("SELECT kind, short_code FROM ops.owner_questions"))).one()
    assert q.kind == "group_added" and len(q.short_code) == 4
    ob = (await db.execute(text("SELECT kind, severity, text FROM ops.owner_outbox"))).one()
    assert ob.kind == "ask" and ob.severity == "important" and f"/nghe {q.short_code}" in ob.text and "Nhóm J" in ob.text
    assert (await db.execute(text("SELECT count(*) FROM ops.listen_events WHERE kind = 'group_added_by_other'"))).scalar() == 1
    assert (await db.execute(text("SELECT count(*) FROM raw.events"))).scalar() == 0


async def test_bot_added_unknown_or_by_link_is_treated_as_other(app, db, redis) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    await bot_world(db, org)
    await _join(org, redis, added_by=None, method="unknown", group_id="gU")
    await _join(org, redis, added_by=OWNER_ZALO, method="link", group_id="gL")       # vào bằng link: không tin người thêm
    for gid in ("gU", "gL"):
        g = await _grp(db, gid)
        assert g.listen_mode == "pending" and g.added_by_kind == "unknown"


async def test_whatsapp_owner_matches_by_jid_and_by_lid(app, db, redis) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    await bot_world(db, org, "whatsapp", alt=OWNER_WA_LID)
    await _join(org, redis, channel="whatsapp", group_id="gW1", added_by=OWNER_WA_LID, added_by_alt=OWNER_WA)
    await _join(org, redis, channel="whatsapp", group_id="gW2", added_by="1111@lid", added_by_alt=OWNER_WA)
    await _join(org, redis, channel="whatsapp", group_id="gW3", added_by="1111@lid", added_by_alt="2222@s.whatsapp.net")
    assert [(await _grp(db, g)).added_by_kind for g in ("gW1", "gW2", "gW3")] == ["owner", "owner", "other"]


async def test_name_equal_to_owner_is_not_owner(app, db, redis) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    await bot_world(db, org)
    await _join(org, redis, added_by="Sếp Ryan", added_by_alt="Sếp Ryan")
    assert (await _grp(db)).added_by_kind == "other"


async def test_personal_account_keeps_old_behaviour_and_records_who(app, db, redis) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    await make_session(db, org, "zalo", kind=None)                           # bản cài cũ: account_kind NULL
    await _join(org, redis, added_by="55555")
    g = await _grp(db)
    assert g.listen_mode == "silent" and g.added_by_kind in ("other", "unknown")   # như v0.1.70: nghe im lặng
    assert (await db.execute(text("SELECT count(*) FROM ops.owner_questions"))).scalar() == 0
    assert (await db.execute(text("SELECT count(*) FROM ops.owner_outbox"))).scalar() == 0


async def test_late_event_only_upgrades_unknown_to_owner(app, db, redis) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    await bot_world(db, org)
    async with sessionmaker()() as s:                                       # nhóm lộ ra trước qua danh bạ: không rõ ai thêm
        ch = await ingest.channel_row(s, org, "zalo")
        await ingest.upsert_group_ex(s, org, ch, "gJ", "Nhóm J")
        await s.commit()
    assert (await _grp(db)).listen_mode == "pending" and (await _grp(db)).added_by_kind == "unknown"
    await _join(org, redis, added_by="55555")                              # người khác báo muộn: không hạ/đổi
    assert (await _grp(db)).added_by_kind == "unknown"
    await _join(org, redis)                                                # Sếp: nâng lên owner, pending → silent
    g = await _grp(db)
    assert g.added_by_kind == "owner" and g.listen_mode == "silent"
    await _join(org, redis, added_by="55555")                              # không bao giờ hạ owner
    assert (await _grp(db)).added_by_kind == "owner"


async def test_new_group_flag_blocks_first_message_for_bot_accounts(app, db, redis) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    await bot_world(db, org)
    ing = ingest.Ingest(FakeBus(), redis, org, sessionmaker())

    class Ev:
        type = "group.discovered"
        stream = "gh.bridge.directory"
        message_id = None
        payload = {"channel": "zalo", "group_id": "gX", "name": "Lạ",
                   "first_message": {**msg("tin đầu", group="gX"), "external_group_id": "gX"}}

    await ing.on_directory(Ev())
    assert (await _grp(db, "gX")).listen_mode == "pending"
    assert (await db.execute(text("SELECT count(*) FROM raw.events"))).scalar() == 0     # không lưu nội dung nhóm lạ


async def test_more_than_three_questions_an_hour_collapse_into_one_message(app, db, redis) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    await bot_world(db, org)
    for i in range(6):
        await _join(org, redis, added_by="55555", group_id=f"gS{i}", name=f"S{i}")
    rows = (await db.execute(text("SELECT dedupe_key FROM ops.owner_outbox ORDER BY created_at"))).scalars().all()
    assert len([r for r in rows if r.startswith("group_added:batch:")]) == 1 and len(rows) == 4
    assert (await db.execute(text("SELECT count(*) FROM ops.owner_questions"))).scalar() == 6


async def test_self_removed_marks_left_disables_support_and_alerts(app, db, redis) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    await bot_world(db, org)
    gid = await listen(db, org, "gR")
    await enable_support(db, gid)
    async with sessionmaker()() as s:
        assert await handle_self_removed(s, redis, org, {"channel": "zalo", "group_id": "gR", "by": "55555"}) is True
        assert await handle_self_removed(s, redis, org, {"channel": "zalo", "group_id": "nope"}) is False
        await s.commit()
    assert (await _grp(db, "gR")).left_at is not None
    sup = (await db.execute(text("SELECT disabled_reason FROM ops.group_support"))).scalar_one()
    assert sup == "bot_removed"
    kinds = (await db.execute(text("SELECT kind FROM ops.listen_events"))).scalars().all()
    assert "bot_removed" in kinds and "support_off" in kinds
    assert (await db.execute(text("SELECT severity FROM ops.owner_outbox WHERE dedupe_key LIKE 'bot_removed:%'"))).scalar_one() == "important"


# ─── tin trong nhóm: Sếp gọi / hỏi hỗ trợ ───────────────────────────────────

async def _pending(org: uuid.UUID, *payloads: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    out: list[tuple[str, dict[str, Any]]] = []
    async with sessionmaker()() as s:
        for p in payloads:
            await ingest.ingest_message(s, org, p)
        await s.commit()
        out = oin.take_pending(s)
    return out


async def test_owner_tag_in_listening_group_emits_call_only_for_owner_id(app, db, redis) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    await bot_world(db, org)
    await listen(db, org, "g1", mode="silent")
    ev = await _pending(org, msg("@bot tóm tắt giúp", sender=OWNER_ZALO, name="Sếp Ryan", mentions=True))
    assert [t for t, _ in ev] == ["call"] and ev[0][1]["group_id"] and ev[0][1]["channel"] == "zalo"
    assert (await db.execute(text("SELECT attrs->>'is_owner' FROM core.persons p JOIN core.person_identities i "
                                  "ON i.person_id = p.id WHERE i.external_id = :x"), {"x": OWNER_ZALO})).scalar() == "true"
    # tên giống Sếp nhưng ID khác ⇒ không phải Owner
    assert await _pending(org, msg("@bot làm đi", sender="999", name="Sếp Ryan", mentions=True)) == []
    # Sếp nhắn không tag ⇒ không gọi
    assert await _pending(org, msg("chào cả nhà", sender=OWNER_ZALO, name="Sếp Ryan")) == []


async def test_owner_call_ignored_when_group_not_listening_or_account_not_bot(app, db, redis) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    sid = await bot_world(db, org)
    await listen(db, org, "g1", mode="paused")
    assert await _pending(org, msg("@bot", sender=OWNER_ZALO, mentions=True)) == []        # nhóm không nghe: tin bị bỏ
    await listen(db, org, "g2", mode="silent")
    await db.execute(text("UPDATE core.channel_sessions SET account_kind = 'owner_self' WHERE id = :i"), {"i": sid})
    await db.commit()
    assert await _pending(org, msg("@bot", group="g2", sender=OWNER_ZALO, mentions=True)) == []


async def test_support_ask_only_when_group_enabled_and_member_tags_or_replies(app, db, redis) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    await bot_world(db, org)
    gid = await listen(db, org, "g1", mode="silent")
    assert await _pending(org, msg("@bot còn hàng không?", sender="777", mentions=True)) == []       # chưa bật hỗ trợ
    await enable_support(db, gid)
    ev = await _pending(org, msg("@bot còn hàng không?", sender="777", mentions=True))
    assert [t for t, _ in ev] == ["support_ask"] and ev[0][1]["sender_person_id"]
    reply = {**msg("còn không ạ", sender="778"), "replies_to_self": True}
    assert [t for t, _ in await _pending(org, reply)] == ["support_ask"]
    raw = (await db.execute(text("SELECT payload FROM raw.events WHERE body_text = 'còn không ạ'"))).scalar_one()
    assert raw.get("replies_to_self") is True
    assert await _pending(org, msg("nói chuyện riêng thôi", sender="777")) == []                      # không tag/không reply
    assert [t for t, _ in await _pending(org, msg("@bot hỏi", sender=OWNER_ZALO, mentions=True))] == ["call"]   # Sếp ≠ hỗ trợ
    await db.execute(text("UPDATE ops.group_support SET disabled_at = now(), disabled_reason = 'owner'"))
    await db.commit()
    assert await _pending(org, msg("@bot hỏi nữa", sender="777", mentions=True)) == []
    await db.execute(text("""UPDATE ops.group_support SET disabled_at = NULL, disabled_reason = NULL,
                             until_at = now() - interval '1 minute'"""))
    await db.commit()
    assert await _pending(org, msg("@bot hỏi nữa", sender="777", mentions=True)) == []              # hết hạn


async def test_outbound_and_personal_account_never_trigger(app, db, redis) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    await make_session(db, org, "zalo", kind=None)
    await listen(db, org, "g1", mode="silent")
    assert await _pending(org, msg("@bot", sender=OWNER_ZALO, mentions=True)) == []


# ─── tin 1-1 của Owner và mã ghép ───────────────────────────────────────────

def _dm(**kw: Any) -> dict[str, Any]:
    return {"channel": "zalo", "session_id": "", "external_msg_id": uuid.uuid4().hex, "sender_external_id": OWNER_ZALO,
            "sender_alt_id": None, "occurred_at": ago(seconds=5), "kind": "text", "body_text": "/hom_nay", "forwarded": False,
            **kw}


async def _dm_run(org: uuid.UUID, redis: Any, sid: uuid.UUID, **kw: Any) -> list[tuple[str, dict[str, Any]]]:
    async with sessionmaker()() as s:
        await oin.handle_owner_dm(s, redis, org, _dm(session_id=str(sid), **kw))
        await s.commit()
        return oin.take_pending(s)


async def test_owner_dm_is_forwarded_not_stored_and_rechecked(app, db, redis) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    sid = await bot_world(db, org)
    ev = await _dm_run(org, redis, sid, external_msg_id="d1")
    assert [t for t, _ in ev] == ["dm"] and ev[0][1]["text"] == "/hom_nay" and ev[0][1]["session_id"] == str(sid)
    assert (await db.execute(text("SELECT count(*) FROM raw.events"))).scalar() == 0
    assert (await db.execute(text("SELECT count(*) FROM core.person_identities"))).scalar() == 0   # không tạo hồ sơ
    assert await _dm_run(org, redis, sid, external_msg_id="d1") == []                               # trùng tin ⇒ bỏ
    assert await _dm_run(org, redis, sid, sender_external_id="999") == []                           # không phải Owner
    assert await _dm_run(org, redis, sid, forwarded=True) == []                                     # tin chuyển tiếp
    assert await _dm_run(org, redis, sid, occurred_at=ago(minutes=11)) == []                        # quá cũ
    assert await _dm_run(org, redis, sid, kind="image") == [] and await _dm_run(org, redis, sid, body_text="  ") == []
    assert await _dm_run(org, redis, uuid.uuid4()) == []                                            # sai phiên
    await db.execute(text("UPDATE ops.owner_channels SET enabled = false"))
    await db.commit()
    assert await _dm_run(org, redis, sid) == []                                                     # kênh quản trị đã tắt


async def test_owner_pair_forwarded_only_with_live_code(app, db, redis) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    sid = await make_session(db, org, "zalo")
    p = {"channel": "zalo", "session_id": str(sid), "sender_external_id": OWNER_ZALO, "sender_alt_id": None,
         "sender_name": "Sếp", "occurred_at": ago(seconds=3), "code": "123456", "external_msg_id": "p1"}

    async def run(payload: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
        async with sessionmaker()() as s:
            await oin.handle_owner_pair(s, org, payload)
            await s.commit()
            return oin.take_pending(s)

    assert await run(p) == []                                                                       # chưa có mã chờ
    await db.execute(text("""INSERT INTO ops.owner_channels (org_id, channel_type, session_id, pair_code_hash, pair_expires_at)
                             VALUES (:o, 'zalo', :s, '\\x01', now() + interval '5 minutes')"""), {"o": org, "s": sid})
    await db.commit()
    ev = await run(p)
    assert [t for t, _ in ev] == ["pair"] and ev[0][1]["code"] == "123456" and ev[0][1]["sender_external_id"] == OWNER_ZALO
    assert ev[0][1]["external_msg_id"] == "p1"                      # bridge gửi kèm ⇒ lời đáp «Đã ghép» có tin kích hoạt
    assert await run({**p, "code": "12345"}) == [] and await run({**p, "code": "12a456"}) == []
    assert await run({**p, "forwarded": True}) == []
    await db.execute(text("UPDATE ops.owner_channels SET pair_expires_at = now() - interval '1 second'"))
    await db.commit()
    assert await run(p) == []


async def test_consumer_publishes_owner_stream_and_drops_source_record(app, db, redis) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    sid = await bot_world(db, org)
    bus = FakeBus()
    ing = ingest.Ingest(bus, redis, org, sessionmaker())

    class Ev:
        type = "owner.dm"
        stream = "gh.bridge.inbound"
        message_id = "1-0"
        payload = _dm(session_id=str(sid))

    await ing.on_inbound(Ev())
    assert [(t, p["text"]) for t, p in bus.on(OWNER_INBOUND)] == [("dm", "/hom_nay")]


async def test_owner_ping_on_not_listening_group_asks_owner(app, db, redis) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    sid = await bot_world(db, org)
    await listen(db, org, "gP", mode="off")
    ping = {"channel": "zalo", "session_id": str(sid), "group_id": "gP", "name": "Nhóm P", "at": ago(seconds=1)}
    async with sessionmaker()() as s:
        assert await oin.handle_owner_ping(s, org, ping) is True
        assert await oin.handle_owner_ping(s, org, {**ping, "group_id": "zzz"}) is False
        await s.commit()
    ob = (await db.execute(text("SELECT text FROM ops.owner_outbox WHERE dedupe_key LIKE 'owner_ping:%'"))).scalar_one()
    q = (await db.execute(text("SELECT short_code FROM ops.owner_questions WHERE kind = 'listen_confirm'"))).scalar_one()
    assert f"/nghe {q}" in ob
    ev = (await db.execute(text("SELECT kind, detail FROM ops.listen_events WHERE kind = 'owner_called'"))).one()
    assert ev.detail.get("not_listening") is True
    await listen(db, org, "gQ", mode="silent")                     # nhóm đang nghe: không cần hỏi
    async with sessionmaker()() as s:
        assert await oin.handle_owner_ping(s, org, {**ping, "group_id": "gQ"}) is False


# ─── đổi phiên tài khoản bot ────────────────────────────────────────────────

async def test_new_session_same_account_keeps_pairing_other_account_cancels(app, db, redis) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    old = await bot_world(db, org)
    gid = await listen(db, org, "g1")
    await enable_support(db, gid)
    new_same = await make_session(db, org, "zalo", kind=None, account="bot-acc-1")
    async with sessionmaker()() as s:
        assert await oin.on_session_active(s, org, "zalo", new_same, "bot-acc-1") == "carried"
        await s.commit()
    row = (await db.execute(text("SELECT session_id, enabled FROM ops.owner_channels"))).one()
    assert row.session_id == new_same and row.enabled is True
    assert (await db.execute(text("SELECT account_kind FROM core.channel_sessions WHERE id = :i"), {"i": new_same})).scalar() == "bot"
    assert (await db.execute(text("SELECT count(*) FROM ops.group_support WHERE disabled_at IS NULL"))).scalar() == 1
    new_other = await make_session(db, org, "zalo", kind=None, account="khac")
    async with sessionmaker()() as s:
        assert await oin.on_session_active(s, org, "zalo", new_other, "khac") == "cancelled"
        assert await oin.on_session_active(s, org, "zalo", new_other, "khac") is None       # đã huỷ: không còn gì để làm
        await s.commit()
    row = (await db.execute(text("SELECT session_id, enabled, owner_external_id FROM ops.owner_channels"))).one()
    assert row.session_id is None and row.enabled is False and row.owner_external_id is None
    assert (await db.execute(text("SELECT disabled_reason FROM ops.group_support"))).scalar() == "session_changed"
    assert "owner_unpaired" in (await db.execute(text("SELECT kind FROM ops.listen_events"))).scalars().all()
    assert old != new_same
