# ruff: noqa: E501
"""L5 — API trạng thái media: quyền Owner, trạng thái từng tin, độ phủ."""

from sqlalchemy import text

from gh.biz.media import jobs
from gh.db import sessionmaker
from tests.phase2 import listen, org_id, put
from tests.test_media_pipeline import media_msg


async def test_media_routes_trang_thai_va_do_phu(owner_api, db) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    await listen(db, org, "g1")
    [vid, iid] = await put(sessionmaker(), org, media_msg("voice", "1"), media_msg("image", "2"))
    r = await owner_api.get(f"/media/events/{vid}")
    assert r.status_code == 200, r.text
    j = r.json()
    assert (j["state"], j["label"], j["kind"], j["will_retry"]) == ("pending", "Đang chờ đọc", "voice", True)
    assert (await owner_api.get(f"/media/events/{iid}")).json()["kind"] == "image"
    async with sessionmaker()() as s:
        ev = (await jobs.pending_events(s, org))[0]
        await jobs._record_failure(jobs.Deps(sm=sessionmaker(), redis=None, bus=None), ev, "too_large")  # type: ignore[arg-type]
    j2 = (await owner_api.get(f"/media/events/{vid}")).json()
    assert j2["state"] == "too_large" and "5 MB" in j2["reason"] and j2["will_retry"] is False
    cov = (await owner_api.get("/media/coverage?hours=24")).json()
    assert cov["lines"] == ["1 voice chưa chép · 1 ảnh chưa đọc ở Nhóm g1"], cov
    assert (await owner_api.get("/media/coverage?hours=0")).status_code == 422


async def test_media_routes_404_khi_khong_phai_media(owner_api, db) -> None:  # type: ignore[no-untyped-def]
    import uuid

    org = await org_id(db)
    await listen(db, org, "g1")
    from tests.phase2 import msg

    [tid] = await put(sessionmaker(), org, msg("tin chữ"))
    assert (await owner_api.get(f"/media/events/{tid}")).status_code == 404
    assert (await owner_api.get(f"/media/events/{uuid.uuid4()}")).status_code == 404
    assert (await db.execute(text("SELECT count(*) FROM clean.media_texts"))).scalar() == 0


async def test_media_routes_can_dang_nhap(owner_api) -> None:  # type: ignore[no-untyped-def]
    import uuid

    owner_api.c.cookies.clear()
    assert (await owner_api.get(f"/media/events/{uuid.uuid4()}")).status_code == 401
    assert (await owner_api.get("/media/coverage")).status_code == 401
