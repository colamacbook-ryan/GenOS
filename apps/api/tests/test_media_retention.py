# ruff: noqa: E501
"""L5 — dọn dữ liệu quá hạn: `clean.media_texts.attachment_id` là FK không ON DELETE nên phải xoá chữ media TRƯỚC tệp
(cùng giao dịch); chữ media cũng bị dọn theo hạn lưu của tin gốc (`raw_received_at`)."""

import time
from pathlib import Path

import pytest
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from gh import retention
from gh.biz.media import jobs
from gh.biz.media.providers import fake_voice_bytes
from gh.chassis.objects import LocalObjectStore
from gh.db import admin_sessionmaker, sessionmaker
from tests.phase2 import listen, org_id, put
from tests.test_media_pipeline import REF, FakeFetcher, deps, media_msg, pend


async def _orphan_attachment(db, redis: Redis, tmp_path: Path, n: str) -> None:  # type: ignore[no-untyped-def]
    d = deps(redis, tmp_path, FakeFetcher({REF.format(n): fake_voice_bytes("xin chào " + n)}))
    [ev] = [e for e in await pend(await org_id(db)) if e.media_ref == REF.format(n)]
    assert await jobs.process_event(d, ev) == "ok"


async def _admin(*sqls: str) -> None:
    """Chạy bằng kết nối quản trị (migration role): vô hiệu trigger/UPDATE mô phỏng partman cần quyền chủ bảng, gh_app không có."""
    async with admin_sessionmaker()() as s:
        for q in sqls:
            await s.execute(text(q))
        await s.commit()


async def _age(db) -> None:  # type: ignore[no-untyped-def]
    """Mô phỏng partman đã DROP phân vùng cũ: sự kiện biến mất, attachment/media_texts còn lại cũ 400 ngày."""
    await _admin("ALTER TABLE raw.attachments DISABLE TRIGGER USER",
                 "UPDATE raw.attachments SET event_id = gen_random_uuid(), event_received_at = now() - interval '400 days'",
                 "ALTER TABLE raw.attachments ENABLE TRIGGER USER",
                 "UPDATE clean.media_texts SET raw_received_at = now() - interval '400 days'")


async def test_fk_chan_xoa_tep_neu_khong_xoa_chu_truoc(app, db, redis: Redis, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    """Tái hiện lỗi cũ: DELETE raw.attachments thẳng (như bản trước khi sửa) vi phạm FK từ clean.media_texts."""
    org = await org_id(db)
    await listen(db, org, "g1")
    await put(sessionmaker(), org, media_msg("voice", "1"))
    await _orphan_attachment(db, redis, tmp_path, "1")
    await _age(db)
    with pytest.raises(IntegrityError):
        await db.execute(text("DELETE FROM raw.attachments"))
    await db.rollback()


async def test_don_tep_mo_coi_xoa_ca_chu_media_va_object(app, db, redis: Redis, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    await listen(db, org, "g1")
    await put(sessionmaker(), org, media_msg("voice", "1"), media_msg("voice", "2"))
    await _orphan_attachment(db, redis, tmp_path, "1")
    await _orphan_attachment(db, redis, tmp_path, "2")
    keys = [r[0] for r in (await db.execute(text("SELECT storage_key FROM raw.attachments"))).all()]
    assert len(keys) == 2 and all((tmp_path / k).exists() for k in keys)
    await _age(db)
    async with sessionmaker()() as s:
        n = await retention.purge_orphan_attachments(s, 30, time.monotonic() + 30, store=LocalObjectStore(str(tmp_path)))
    assert n == 2
    assert (await db.execute(text("SELECT count(*) FROM raw.attachments"))).scalar() == 0
    assert (await db.execute(text("SELECT count(*) FROM clean.media_texts"))).scalar() == 0
    assert not any((tmp_path / k).exists() for k in keys)


async def test_hang_giu_cho_khong_object_va_tin_con_song_khong_bi_xoa(app, db, redis: Redis, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    await listen(db, org, "g1")
    await put(sessionmaker(), org, media_msg("voice", "1", media={"kind": "voice", "mime": "audio/ogg", "size": 6 * 1024 * 1024, "seconds": 1}),
              media_msg("voice", "2"))
    d = deps(redis, tmp_path, FakeFetcher({REF.format("2"): fake_voice_bytes("còn sống")}))
    for ev in await pend(org):
        await jobs.process_event(d, ev)
    # tin 1: chỉ có hàng giữ chỗ none:… (too_large); tin 2: còn sự kiện thật ⇒ không phải mồ côi
    await _admin("ALTER TABLE raw.attachments DISABLE TRIGGER USER",
                 "UPDATE raw.attachments SET event_id = gen_random_uuid(), event_received_at = now() - interval '400 days' WHERE storage_key LIKE 'none:%'",
                 "ALTER TABLE raw.attachments ENABLE TRIGGER USER")
    async with sessionmaker()() as s:
        n = await retention.purge_orphan_attachments(s, 30, time.monotonic() + 30, store=LocalObjectStore(str(tmp_path)))
    assert n == 1
    left = (await db.execute(text("SELECT storage_key FROM raw.attachments"))).scalars().all()
    assert len(left) == 1 and not left[0].startswith("none:")
    assert (await db.execute(text("SELECT count(*) FROM clean.media_texts WHERE status = 'ok'"))).scalar() == 1


async def test_don_chu_media_theo_han_luu(app, db, redis: Redis, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    await listen(db, org, "g1")
    await put(sessionmaker(), org, media_msg("voice", "1"), media_msg("voice", "2"))
    await _orphan_attachment(db, redis, tmp_path, "1")
    await _orphan_attachment(db, redis, tmp_path, "2")
    await _admin("UPDATE clean.media_texts SET raw_received_at = now() - interval '400 days' "
                 "WHERE id = (SELECT id FROM clean.media_texts ORDER BY id LIMIT 1)")
    async with sessionmaker()() as s:
        assert await retention.purge_media_texts(s, None, time.monotonic() + 30) == 0      # chưa có hạn ⇒ không xoá
        assert await retention.purge_media_texts(s, 30, time.monotonic() + 30) == 1
    assert (await db.execute(text("SELECT count(*) FROM clean.media_texts"))).scalar() == 1
    async with sessionmaker()() as s:
        assert await retention.purge_attachments_and_media_texts(s, 30, time.monotonic() + 30) == 0   # tin còn sống: tệp giữ nguyên
