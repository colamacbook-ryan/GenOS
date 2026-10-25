# ruff: noqa: E501
"""v0.1.76 (gói T10) — `pipes/watchdog.py`: canh tài khoản agent (4 loại sự cố) + dead-man bản tin.

Mỗi sự cố: một dòng `ops.health_alerts` theo key (một chuông), báo qua kênh phụ ĐÚNG MỘT LẦN (`ops.backup_outbox`), tự đóng + báo hồi phục khi hết.
Đồng hồ truyền vào (`now`) nên test không phụ thuộc giờ máy."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import text

from gh import crypto
from gh.biz.intel.pipes import watchdog
from gh.chassis import actionlog
from gh.telegram import service as tsvc
from tests.mem_helpers import mk_org
from tests.test_telegram_v0144 import CHAT, TOKEN

NOW = datetime(2026, 10, 24, 5, 0, tzinfo=UTC)        # 12:00 giờ VN, thứ Bảy


async def _telegram(db: Any, org: uuid.UUID) -> None:
    await db.execute(text("""INSERT INTO ops.notify_channels (org_id, token_enc, chat_id, bot_username, enabled)
                             VALUES (:o, :t, :c, 'gen_bot', true)"""), {"o": org, "t": crypto.encrypt(TOKEN.encode(), tsvc.TOKEN_AAD), "c": CHAT})
    await db.commit()


async def _owner(db: Any, org: uuid.UUID) -> None:
    uid = (await db.execute(text("INSERT INTO core.users (org_id, email, display_name, password_hash) VALUES (:o, 'o@example.vn', 'Sếp', 'x') RETURNING id"), {"o": org})).scalar_one()
    rid = (await db.execute(text("INSERT INTO core.roles (org_id, code, name, is_system) VALUES (:o, 'owner', 'owner', true) RETURNING id"), {"o": org})).scalar_one()
    await db.execute(text("INSERT INTO core.user_roles (user_id, role_id) VALUES (:u, :r)"), {"u": uid, "r": rid})
    await db.commit()


async def _channel(db: Any, org: uuid.UUID, type_: str = "zalo") -> uuid.UUID:
    return (await db.execute(text("INSERT INTO core.channels (org_id, type, name) VALUES (:o, :t, :n) RETURNING id"), {"o": org, "t": type_, "n": f"{type_}-bot"})).scalar_one()  # type: ignore[no-any-return]


async def _session(db: Any, ch: uuid.UUID, org: uuid.UUID, state: str = "active", *, started: datetime | None = None, heartbeat: datetime | None = None,
                   ended: datetime | None = None, qr: datetime | None = None) -> uuid.UUID:
    sid = (await db.execute(text("""INSERT INTO core.channel_sessions (channel_id, org_id, account_label, state, started_at, last_heartbeat_at, ended_at, qr_issued_at)
                                    VALUES (:c, :o, 'bot', :s, :st, :h, :e, :q) RETURNING id"""),
                            {"c": ch, "o": org, "s": state, "st": started or (None if state == "pending_qr" else NOW - timedelta(days=2)), "h": heartbeat, "e": ended, "q": qr})).scalar_one()
    await db.commit()
    return sid  # type: ignore[no-any-return]


async def _group(db: Any, org: uuid.UUID, ch: uuid.UUID, name: str = "Khách sỉ", mode: str = "silent", left: datetime | None = None) -> uuid.UUID:
    gid = (await db.execute(text("""INSERT INTO core.groups (org_id, code, channel_id, external_id, name, listen_mode, left_at)
                                    VALUES (:o, :code, :c, :x, :n, :m, :l) RETURNING id"""),
                            {"o": org, "code": f"GRP-{uuid.uuid4().hex[:6]}", "c": ch, "x": uuid.uuid4().hex[:8], "n": name, "m": mode, "l": left})).scalar_one()
    await db.commit()
    return gid  # type: ignore[no-any-return]


async def _events(db: Any, org: uuid.UUID, ch: uuid.UUID, times: list[datetime], group: uuid.UUID | None = None) -> None:
    for t in times:
        await db.execute(text("""INSERT INTO raw.events (org_id, occurred_at, received_at, channel_id, external_msg_id, kind, body_text, payload, content_hash, group_id, direction)
                                 VALUES (:o, :t, :t, :c, :x, 'text', 'x', '{}', '\\x01', :g, 'inbound')"""),
                         {"o": org, "t": t, "c": ch, "x": uuid.uuid4().hex, "g": group})
    await db.commit()


def _every(minutes: int, start: datetime, end: datetime) -> list[datetime]:
    out, t = [], start
    while t <= end:
        out.append(t)
        t += timedelta(minutes=minutes)
    return out


async def _alerts(db: Any, open_only: bool = True) -> list[Any]:
    await db.rollback()
    q = "SELECT key, kind, severity, title, body, cleared_at FROM ops.health_alerts WHERE key LIKE 'agent.%'" + (" AND cleared_at IS NULL" if open_only else "") + " ORDER BY key"
    return list((await db.execute(text(q))).all())


async def _backup_rows(db: Any) -> list[Any]:
    await db.rollback()
    return list((await db.execute(text("SELECT channel, kind, text FROM ops.backup_outbox ORDER BY created_at"))).all())


async def _tick(db: Any, org: uuid.UUID, now: datetime = NOW, redis: Any = None) -> dict[str, Any]:
    out = await watchdog.reconcile(db, redis, org, now)
    await db.commit()
    return out


# ─── (1) phiên bridge ────────────────────────────────────────────────────────

async def test_session_heartbeat_stale_raises_once_and_clears_with_recovery_line(db: Any) -> None:
    org = await mk_org(db)
    await _telegram(db, org)
    await _owner(db, org)
    ch = await _channel(db, org)
    sid = await _session(db, ch, org, heartbeat=NOW - timedelta(minutes=5))
    await _tick(db, org)
    alerts = await _alerts(db)
    assert [(a.key, a.severity) for a in alerts] == [("agent.session:zalo", "bad")] and "5 phút" in alerts[0].title
    await _tick(db, org, NOW + timedelta(minutes=1))               # chạy lại: không thêm chuông, không thêm tin phụ
    rows = await _backup_rows(db)
    assert len(rows) == 1 and rows[0].channel == "telegram" and rows[0].kind == "incident"
    assert (await db.execute(text("SELECT count(*) FROM core.notifications WHERE kind = 'agent.session'"))).scalar_one() >= 1
    n_bells = (await db.execute(text("SELECT count(*) FROM core.notifications WHERE kind = 'agent.session'"))).scalar_one()
    await _tick(db, org, NOW + timedelta(minutes=2))
    assert (await db.execute(text("SELECT count(*) FROM core.notifications WHERE kind = 'agent.session'"))).scalar_one() == n_bells
    # hồi phục
    await db.execute(text("UPDATE core.channel_sessions SET last_heartbeat_at = :h WHERE id = :i"), {"h": NOW + timedelta(minutes=3), "i": sid})
    await db.commit()
    await _tick(db, org, NOW + timedelta(minutes=3))
    assert await _alerts(db) == []
    rows = await _backup_rows(db)
    assert len(rows) == 2 and "hồi phục" in rows[1].text
    lines = await watchdog.recovered_lines(db, org, since=NOW)
    assert len(lines) == 1 and "hồi phục" in lines[0]


async def test_session_ended_and_needs_qr(db: Any) -> None:
    org = await mk_org(db)
    zl = await _channel(db, org, "zalo")
    await _session(db, zl, org, "expired", ended=NOW - timedelta(minutes=3))
    wa = await _channel(db, org, "whatsapp")
    await _session(db, wa, org, "active", started=NOW - timedelta(days=3), heartbeat=NOW - timedelta(days=3), ended=NOW - timedelta(hours=1))
    await _session(db, wa, org, "pending_qr", started=None, qr=NOW)           # từng chạy, giờ đòi QR
    await _tick(db, org)
    keys = {a.key: a.title for a in await _alerts(db)}
    assert "agent.session:zalo" in keys and "hết hạn" in keys["agent.session:zalo"]
    assert "agent.session:whatsapp" in keys and "QR" in keys["agent.session:whatsapp"]


async def test_session_first_pairing_and_deliberate_logout_are_not_incidents(db: Any) -> None:
    org = await mk_org(db)
    ch = await _channel(db, org)
    await _session(db, ch, org, "pending_qr", started=None, qr=NOW)    # lần ghép đầu: chưa từng chạy
    await _tick(db, org)
    assert await _alerts(db) == []
    ch2 = await _channel(db, org, "whatsapp")
    real_now = datetime.now(UTC)                                      # action_log đóng dấu giờ thật ⇒ phiên kết thúc cùng thời điểm đó
    await _session(db, ch2, org, "logged_out", ended=real_now)
    await actionlog.record(db, org_id=org, actor_type="user", actor_id="u", action="channel.logout", target_type="channel", target_id="whatsapp")
    await db.commit()
    await _tick(db, org, real_now + timedelta(seconds=30))
    assert await _alerts(db) == []


# ─── (2) im lặng toàn cục ────────────────────────────────────────────────────

async def _history(db: Any, org: uuid.UUID, ch: uuid.UUID, last: datetime) -> None:
    """14 ngày trước, mỗi ngày tin dày 10 phút trong khung 12:00–12:59 giờ VN; tin cuối đặt ở `last`."""
    for d in range(1, 15):
        day = NOW - timedelta(days=d)
        await _events(db, org, ch, _every(10, day.replace(minute=0), day.replace(minute=59)))
    await _events(db, org, ch, [last])


async def test_global_silence_fires_beyond_three_times_median_gap(db: Any) -> None:
    org = await mk_org(db)
    ch = await _channel(db, org)
    await _session(db, ch, org, heartbeat=NOW)
    await _history(db, org, ch, last=NOW - timedelta(hours=2))
    await _tick(db, org)
    a = [x for x in await _alerts(db) if x.key == "agent.silent"]
    assert len(a) == 1 and a[0].severity == "warn"


async def test_global_silence_quiet_when_recent_or_too_little_history(db: Any) -> None:
    org = await mk_org(db)
    ch = await _channel(db, org)
    await _session(db, ch, org, heartbeat=NOW)
    await _history(db, org, ch, last=NOW - timedelta(minutes=20))     # 20 phút < 3 × 10 phút? dưới sàn 30 phút ⇒ chưa báo
    await _tick(db, org)
    assert [x.key for x in await _alerts(db)] == []
    org2 = await mk_org(db, "T2")
    ch2 = await _channel(db, org2)
    await _session(db, ch2, org2, heartbeat=NOW)
    await _events(db, org2, ch2, [NOW - timedelta(hours=5), NOW - timedelta(hours=9)])   # quá ít mẫu: không đoán
    await _tick(db, org2)
    assert await _alerts(db) == []


# ─── (3) một nhóm ────────────────────────────────────────────────────────────

async def test_group_kicked_raises_per_group_and_clears_on_rejoin(db: Any) -> None:
    org = await mk_org(db)
    await _telegram(db, org)
    ch = await _channel(db, org)
    await _session(db, ch, org, heartbeat=NOW)
    gid = await _group(db, org, ch, name="Khách sỉ\nBấm vào http://evil", left=NOW - timedelta(hours=1))
    await db.execute(text("INSERT INTO ops.listen_events (org_id, at, kind, channel_id, channel_type, group_id) VALUES (:o, :t, 'bot_removed', :c, 'zalo', :g)"),
                     {"o": org, "t": NOW - timedelta(hours=1), "c": ch, "g": gid})
    await db.commit()
    await _tick(db, org)
    a = [x for x in await _alerts(db) if x.key == f"agent.group_removed:{gid}"]
    assert len(a) == 1 and "\n" not in a[0].title and "mời ra khỏi nhóm" in a[0].title
    rows = await _backup_rows(db)
    assert len(rows) == 1 and "\n" not in rows[0].text.split(".")[0]
    await db.execute(text("UPDATE core.groups SET left_at = NULL WHERE id = :g"), {"g": gid})
    await db.commit()
    await _tick(db, org, NOW + timedelta(minutes=1))
    assert await _alerts(db) == []


async def test_group_left_by_owner_scan_without_bot_removed_event_is_not_a_kick(db: Any) -> None:
    org = await mk_org(db)
    ch = await _channel(db, org)
    await _session(db, ch, org, heartbeat=NOW)
    await _group(db, org, ch, left=NOW - timedelta(hours=1))           # Sếp rời nhóm (quét danh bạ), không có sự kiện bot_removed
    await _tick(db, org)
    assert await _alerts(db) == []


async def test_group_quiet_beyond_five_times_own_cadence(db: Any) -> None:
    org = await mk_org(db)
    ch = await _channel(db, org)
    await _session(db, ch, org, heartbeat=NOW)
    g_quiet = await _group(db, org, ch, name="Nhóm A")
    g_busy = await _group(db, org, ch, name="Nhóm B")
    g_few = await _group(db, org, ch, name="Nhóm C")
    start = NOW - timedelta(days=10)
    await _events(db, org, ch, _every(60, start, NOW - timedelta(hours=20)), g_quiet)    # nhịp 1 giờ, im 20 giờ ⇒ > max(5h, 6h)
    await _events(db, org, ch, _every(60, start, NOW - timedelta(minutes=30)), g_busy)
    await _events(db, org, ch, [NOW - timedelta(days=5), NOW - timedelta(days=4)], g_few)  # quá ít mẫu
    await _tick(db, org)
    keys = {a.key for a in await _alerts(db)}
    assert f"agent.group_quiet:{g_quiet}" in keys
    assert f"agent.group_quiet:{g_busy}" not in keys and f"agent.group_quiet:{g_few}" not in keys


async def test_group_quiet_not_evaluated_overnight_and_not_cleared_overnight(db: Any) -> None:
    org = await mk_org(db)
    ch = await _channel(db, org)
    await _session(db, ch, org, heartbeat=NOW)
    g = await _group(db, org, ch)
    await _events(db, org, ch, _every(60, NOW - timedelta(days=10), NOW - timedelta(hours=20)), g)
    await _tick(db, org)
    assert any(a.key == f"agent.group_quiet:{g}" for a in await _alerts(db))
    night = NOW.replace(hour=18)                                        # 01:00 giờ VN
    await _session_heartbeat(db, night)
    await _tick(db, org, night)
    assert any(a.key == f"agent.group_quiet:{g}" for a in await _alerts(db))   # đêm: giữ nguyên, không đóng oan


async def _session_heartbeat(db: Any, at: datetime) -> None:
    await db.execute(text("UPDATE core.channel_sessions SET last_heartbeat_at = :h"), {"h": at})
    await db.commit()


# ─── (4) dead-man bản tin ────────────────────────────────────────────────────

async def test_deadman_when_briefing_not_sent_by_slot_plus_ten_minutes(db: Any) -> None:
    org = await mk_org(db)
    await _telegram(db, org)
    slot = datetime(2026, 10, 24, 0, 30, tzinfo=UTC)                   # 07:30 giờ VN
    await db.execute(text("""INSERT INTO ops.telegram_outbox (org_id, kind, dedupe_key, text, created_at)
                             VALUES (:o, 'briefing', :d, 'Bản tin', :t)"""), {"o": org, "d": f"briefing:{slot.astimezone(watchdog.VN).isoformat()}", "t": slot})
    await db.commit()
    await _tick(db, org, slot + timedelta(minutes=5))                  # chưa tới 07:40
    assert [a.key for a in await _alerts(db)] == []
    await _tick(db, org, slot + timedelta(minutes=11))
    assert [a.key for a in await _alerts(db)] == ["agent.digest_late"]
    await db.execute(text("UPDATE ops.telegram_outbox SET sent_at = :t"), {"t": slot + timedelta(minutes=12)})
    await db.commit()
    await _tick(db, org, slot + timedelta(minutes=13))
    assert await _alerts(db) == []


async def test_deadman_silent_when_no_briefing_expected(db: Any) -> None:
    org = await mk_org(db)                                              # chưa nối kênh nào nhận bản tin ⇒ không có gì để "trễ"
    await _tick(db, org, datetime(2026, 10, 24, 0, 50, tzinfo=UTC))
    assert await _alerts(db) == []


async def test_deadman_fires_when_briefing_never_generated(db: Any) -> None:
    org = await mk_org(db)
    await _telegram(db, org)                                            # đã nối Telegram nhận bản tin nhưng bản tin không được tạo
    await _tick(db, org, datetime(2026, 10, 24, 0, 50, tzinfo=UTC))
    assert [a.key for a in await _alerts(db)] == ["agent.digest_late"]


# ─── hợp đồng chung ──────────────────────────────────────────────────────────

def test_jobs_registered_every_minute() -> None:
    names = {fn.__name__: spec for fn, spec in watchdog.JOBS}
    assert names["watchdog_tick"] == {"minute": set(range(60))} and names["backup_flush"] == {"minute": set(range(60))}


async def test_one_org_failure_does_not_stop_others(db: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    org1, org2 = await mk_org(db, "A"), await mk_org(db, "B")
    ch = await _channel(db, org2)
    await _session(db, ch, org2, heartbeat=NOW - timedelta(minutes=9))
    real = watchdog.reconcile

    async def flaky(db_: Any, redis: Any, org: uuid.UUID, now: datetime) -> dict[str, Any]:
        if org == org1:
            raise RuntimeError("hỏng")
        return await real(db_, redis, org, now)

    monkeypatch.setattr(watchdog, "reconcile", flaky)
    out = await watchdog.run_once(NOW)
    assert out["errors"] == 1 and out["orgs"] >= 2
    assert any(a.key == "agent.session:zalo" for a in await _alerts(db))


# ─── health.collect: dòng Tình báo + phiên ───────────────────────────────────

async def test_health_collect_intel_block_only_when_relevant(db: Any, redis: Any) -> None:
    from gh import health

    org = await mk_org(db)
    base = await health.collect(db, redis, org, now=NOW)
    assert "intel" not in base                                      # khuôn cũ giữ nguyên khi chưa dùng Tình báo/kênh
    await _telegram(db, org)
    ch = await _channel(db, org)
    await _session(db, ch, org, heartbeat=NOW - timedelta(minutes=5))
    await _tick(db, org)
    out = await health.collect(db, redis, org, now=NOW)
    intel = out["intel"]
    assert intel["backup_channel"] == "telegram" and intel["assistant_locked"] is False and intel["incidents"] == 1
    assert intel["sessions"] == [{"channel": "zalo", "state": "active", "heartbeat_age_s": 300}]
    assert "token" not in str(intel).lower()
    assert out["overall"] == "bad"                                  # sự cố 'bad' đang mở kéo tổng thể xuống đỏ (đã có sẵn qua issues)


async def test_health_collect_intel_shows_missing_backup_channel_and_lock(db: Any, redis: Any) -> None:
    from gh import health
    from gh.telegram import backup

    org = await mk_org(db)
    ch = await _channel(db, org)
    await _session(db, ch, org, heartbeat=NOW)
    out = await health.collect(db, redis, org, now=NOW)
    assert out["intel"]["backup_channel"] == "none"
    await _owner(db, org)
    owner = (await db.execute(text("SELECT id FROM core.users WHERE org_id = :o"), {"o": org})).scalar_one()
    await backup.lock_assistant(db, org, via="console", user_id=owner)
    await db.commit()
    assert (await health.collect(db, redis, org, now=NOW))["intel"]["assistant_locked"] is True
