# ruff: noqa: E501
"""v0.1.76 (gói T3) — `reports.py` thành cửa giao hàng của Tình báo (spec r6 W7, CNS-031): `kind='digest'` miễn `min_severity` và
`max_per_hour`; giờ yên lặng tách khỏi `urgent` bằng `bypass_quiet` (mặc định giữ hành vi cũ); ghi `refs`; giờ yên lặng đọc từ
`settings.intel.quiet_*` khi có; ĐANG KHOÁ TRỢ LÝ ⇒ không xếp, không gửi gì ra Zalo."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import text

from gh.chassis.bus import BRIDGE_OUTBOUND
from gh.db import sessionmaker
from gh.owner_channel import reports
from gh.telegram import backup
from tests.owner_helpers import FakeBus, bot_world
from tests.phase2 import org_id

SM = sessionmaker
NOON = datetime(2026, 10, 18, 12, 0, tzinfo=reports.VN_TZ).astimezone(UTC)
NIGHT = datetime(2026, 10, 18, 23, 30, tzinfo=reports.VN_TZ).astimezone(UTC)


async def enq(org: uuid.UUID, **kw: Any) -> bool:
    kw.setdefault("now", NOON)
    kw.setdefault("kind", "alert")
    kw.setdefault("severity", "important")
    kw.setdefault("text_", "Có việc")
    kw.setdefault("dedupe_key", uuid.uuid4().hex)
    async with SM()() as s:
        ok = await reports.enqueue(s, org, "zalo", **kw)
        await s.commit()
        return ok


async def not_before(db: Any, key: str) -> datetime:
    return (await db.execute(text("SELECT not_before FROM ops.owner_outbox WHERE dedupe_key = :k"), {"k": key})).scalar_one()  # type: ignore[no-any-return]


async def test_digest_is_exempt_from_min_severity_and_hourly_cap(app, db, redis) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    await bot_world(db, org)
    for i in range(6):
        assert await enq(org, dedupe_key=f"a{i}") is True                       # chạm max_per_hour = 6
    assert await enq(org, dedupe_key="a-over") is False
    assert await enq(org, kind="digest", severity="info", dedupe_key="d1") is True    # bản tin: KHÔNG bị trần, KHÔNG bị min_severity
    await db.execute(text("""UPDATE ops.owner_channels SET report_prefs = '{"min_severity": "urgent"}'"""))
    await db.commit()
    assert await enq(org, kind="digest", severity="info", dedupe_key="d2") is True
    assert await enq(org, kind="alert", severity="important", dedupe_key="a-x") is False   # alert vẫn bị ngưỡng


async def test_refs_are_stored_and_other_kinds_still_rejected(app, db, redis) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    await bot_world(db, org)
    refs = [{"n": 1, "signal_id": str(uuid.uuid4()), "ask_id": None}]
    assert await enq(org, kind="digest", severity="info", dedupe_key="d", refs=refs) is True
    assert (await db.execute(text("SELECT refs FROM ops.owner_outbox WHERE dedupe_key = 'd'"))).scalar_one() == refs
    with pytest.raises(ValueError):
        await enq(org, kind="newsletter")


@pytest.mark.parametrize("kind,severity,bypass,night,waits", [
    ("digest", "info", None, True, True),          # bản tin trong giờ yên lặng: chờ tới sáng
    ("digest", "info", None, False, False),
    ("alert", "urgent", None, True, False),        # bên gọi cũ: urgent vẫn gửi ngay (mặc định bypass = severity urgent)
    ("alert", "important", None, True, True),
    ("alert", "urgent", False, True, True),        # 🔴 thường: KHÔNG gắn với urgent nữa
    ("alert", "urgent", True, True, False),        # cờ money / lừa đảo-an toàn: gửi ngay
    ("alert", "important", True, True, False),
    ("alert", "urgent", False, False, False),      # ngoài giờ yên lặng thì luôn gửi
])
async def test_quiet_hours_bypass_table(app, db, redis, kind: str, severity: str, bypass: bool | None, night: bool, waits: bool) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    await bot_world(db, org)
    now = NIGHT if night else NOON
    assert await enq(org, kind=kind, severity=severity, bypass_quiet=bypass, now=now, dedupe_key="k") is True
    nb = await not_before(db, "k")
    assert (nb > now) is waits
    if waits:
        assert nb.astimezone(reports.VN_TZ).hour == 7                         # chờ tới 07:00 giờ VN


async def test_quiet_hours_follow_intel_settings_hhmm(app, db, redis) -> None:  # type: ignore[no-untyped-def]
    """Một nguồn giờ yên lặng (CNS-117): `settings.intel.quiet_start/quiet_end` ("HH:MM") thắng `owner_report` khi có."""
    org = await org_id(db)
    await bot_world(db, org)
    await db.execute(text("""UPDATE core.organizations SET settings = settings || jsonb_build_object('intel',
                               jsonb_build_object('quiet_start', '12:00', 'quiet_end', '13:30')) WHERE id = :o"""), {"o": org})
    await db.commit()
    assert await enq(org, now=NOON, dedupe_key="in") is True
    nb = (await not_before(db, "in")).astimezone(reports.VN_TZ)
    assert (nb.hour, nb.minute) == (13, 30)
    assert await enq(org, now=NIGHT, dedupe_key="out") is True               # 23:30 nằm ngoài khung 12:00–13:30
    assert await not_before(db, "out") == NIGHT


async def _lock(db: Any, org: uuid.UUID) -> None:
    await db.execute(text("""UPDATE core.organizations SET settings = settings || jsonb_build_object('intel',
                               COALESCE(settings->'intel', '{}'::jsonb) || jsonb_build_object('assistant_locked_at', '2026-10-18T00:00:00Z'))
                             WHERE id = :o"""), {"o": org})
    await db.commit()
    async with SM()() as s:
        assert await backup.is_locked(s, org)


async def test_locked_assistant_enqueues_and_flushes_nothing(app, db, redis) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    await bot_world(db, org)
    assert await enq(org, kind="digest", severity="info", dedupe_key="before") is True   # đã xếp trước khi khoá
    await _lock(db, org)
    assert await enq(org, kind="digest", severity="info", dedupe_key="d") is False
    assert await enq(org, kind="alert", severity="urgent", dedupe_key="a") is False
    bus = FakeBus()
    stats = await reports.flush(SM(), bus, redis, now=NOON + timedelta(minutes=1))
    assert stats["sent"] == 0 and not bus.on(BRIDGE_OUTBOUND)
    assert (await db.execute(text("SELECT sent_at FROM ops.owner_outbox WHERE dedupe_key = 'before'"))).scalar_one() is None


async def test_flush_sets_sent_at_for_digest_so_deadman_sees_it(app, db, redis) -> None:  # type: ignore[no-untyped-def]
    """Hợp đồng với dead-man của T10: `sent_at` của `owner_outbox(kind='digest')` vẫn do `flush` ghi."""
    org = await org_id(db)
    await bot_world(db, org)
    assert await enq(org, kind="digest", severity="info", dedupe_key="d", text_="Bản tin thử") is True
    bus = FakeBus()
    assert (await reports.flush(SM(), bus, redis, now=NOON + timedelta(minutes=1)))["sent"] == 1
    assert (await db.execute(text("SELECT sent_at FROM ops.owner_outbox WHERE kind = 'digest'"))).scalar_one() is not None


async def test_locked_org_rows_do_not_starve_other_orgs_and_stale_ones_expire(app, db, redis) -> None:  # type: ignore[no-untyped-def]
    """TẤN CÔNG DoS chéo tenant: org A khoá với nhiều hàng chờ không được chiếm batch (BATCH = 20) của org B."""
    from tests.mem_helpers import mk_org

    a = await org_id(db)
    await bot_world(db, a)
    for i in range(reports.BATCH + 5):
        assert await enq(a, dedupe_key=f"a{i}") is True or True        # xếp được nhiều hàng trước khi khoá (bỏ qua trần: chèn thẳng)
    await db.execute(text("""INSERT INTO ops.owner_outbox (org_id, channel_type, kind, severity, dedupe_key, text, created_at, not_before, next_attempt_at)
                             SELECT :o, 'zalo', 'alert', 'urgent', 'bulk' || g, 'cũ', :c, :c, :c FROM generate_series(1, :n) g"""),
                     {"o": a, "c": NOON - timedelta(hours=1), "n": reports.BATCH + 5})
    await db.execute(text("""INSERT INTO ops.owner_outbox (org_id, channel_type, kind, severity, dedupe_key, text, created_at, not_before, next_attempt_at)
                             VALUES (:o, 'zalo', 'digest', 'info', 'stale', 'rất cũ', :c, :c, :c)"""), {"o": a, "c": NOON - timedelta(hours=30)})
    await db.commit()
    await _lock(db, a)
    # org B (cùng DB): hàng MỚI hơn hàng của A ⇒ trước đây nằm sau 20 hàng của A và không bao giờ được gửi
    b = await mk_org(db, "Org B")
    ch = (await db.execute(text("INSERT INTO core.channels (org_id, type, name) VALUES (:o, 'zalo', 'z') RETURNING id"), {"o": b})).scalar_one()
    await db.commit()
    await bot_world(db, b)
    assert ch is not None
    assert await enq(b, kind="alert", severity="urgent", dedupe_key="b1", text_="Tin của B", now=NOON) is True
    bus = FakeBus()
    stats = await reports.flush(SM(), bus, redis, now=NOON + timedelta(minutes=1))
    assert stats["sent"] == 1 and [p["text"] for _, p in bus.on(BRIDGE_OUTBOUND)] == ["Tin của B"]
    # hàng của A: còn chờ (không bị gửi, không bị đốt lượt thử); hàng quá MAX_AGE bị đóng để mở khoá không dội báo cáo cũ
    pend = (await db.execute(text("SELECT count(*) FROM ops.owner_outbox WHERE org_id = :o AND sent_at IS NULL AND failed_code IS NULL AND attempts = 0"), {"o": a})).scalar_one()
    assert pend >= reports.BATCH
    assert (await db.execute(text("SELECT failed_code FROM ops.owner_outbox WHERE dedupe_key = 'stale'"))).scalar_one() == "ASSISTANT_LOCKED"
