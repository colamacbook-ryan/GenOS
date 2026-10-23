# ruff: noqa: E501
"""L5 — pipeline voice/ảnh: ingest ghi media_ref → job tải (bridge giả) → lưu objects + raw.attachments → engine giả →
clean.media_texts → luật (a); trạng thái đọc được; lỗi có lý do thân thiện; idempotent; không bỏ im lặng."""

import dataclasses
import hashlib
import uuid
from pathlib import Path
from typing import Any

from redis.asyncio import Redis
from sqlalchemy import text

from gh.biz.media import fetch as mf
from gh.biz.media import jobs, status
from gh.biz.media.providers import FakeSttEngine, FakeVisionEngine, fake_image_bytes, fake_voice_bytes
from gh.chassis.bus import EventBus
from gh.chassis.objects import LocalObjectStore
from gh.db import sessionmaker
from tests.phase2 import listen, msg, org_id, put

REF = "zalo:s1:{}"


def media_msg(kind: str, n: str, *, media: dict[str, Any] | None = None, **extra: Any) -> dict[str, Any]:
    p = msg("", msg_id=f"mm-{n}") | {"kind": kind, "body_text": None, "media_ref": REF.format(n),
                                      "media": media if media is not None else {"kind": kind, "mime": "", "size": 100, "seconds": 5}}
    return p | extra


class FakeFetcher:
    """Thay `fetch.fetch`: trả byte theo media_ref; `errors[ref]` ném MediaFetchError."""

    def __init__(self, blobs: dict[str, bytes] | None = None, errors: dict[str, str] | None = None):
        self.blobs, self.errors, self.calls, self.binds = blobs or {}, errors or {}, [], []

    async def __call__(self, bus: Any, org: uuid.UUID, *, kind: str, media_ref: str, chat: str, sender: str, timeout: float = 30.0) -> mf.Fetched:  # noqa: ASYNC109
        self.calls.append(media_ref)
        self.binds.append((chat, sender))
        if media_ref in self.errors:
            raise mf.MediaFetchError(self.errors[media_ref])
        data = self.blobs[media_ref]
        return mf.Fetched(data=data, mime=mf.verified_mime(kind, data), sha256=hashlib.sha256(data).hexdigest(), size=len(data))


def deps(redis: Redis, tmp_path: Path, fetcher: Any, **kw: Any) -> jobs.Deps:
    return jobs.Deps(sm=sessionmaker(), redis=redis, bus=EventBus(redis), store=LocalObjectStore(str(tmp_path)),
                     fetcher=fetcher, stt=kw.pop("stt", FakeSttEngine()), vision=kw.pop("vision", FakeVisionEngine()), **kw)


async def pend(org: uuid.UUID) -> list[jobs.PendingEvent]:
    async with sessionmaker()() as db:
        return await jobs.pending_events(db, org)


async def rows(db: Any, sql: str = "SELECT kind, status, error_code, text, engine FROM clean.media_texts ORDER BY kind") -> list[Any]:
    return list((await db.execute(text(sql))).all())


async def test_ingest_luu_media_ref_va_cac_co(app, db) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    await listen(db, org, "g1")
    sm = sessionmaker()
    ids = await put(sm, org,
                    media_msg("voice", "1", forwarded=None),
                    media_msg("image", "2", forwarded=True),
                    {**media_msg("voice", "3"), "media_ref": "https://evil.example/a.ogg"},            # URL KHÔNG được lưu làm ref
                    {**msg("", msg_id="mm-4"), "kind": "system", "revokes": "5001", "revokes_cli": "cli5001", "forwarded": None},
                    {**media_msg("voice", "5"), "forwarded": "yes", "revokes": "x" * 65})                  # rác bị bỏ
    assert all(i is not None for i in ids)
    pl = {r.external_msg_id: r for r in await rows(db, "SELECT external_msg_id, kind, payload FROM raw.events")}
    assert pl["mm-1"].kind == "voice" and pl["mm-1"].payload["media_ref"] == REF.format("1")
    assert pl["mm-1"].payload["media_bind"] == {"chat": "g1", "sender": "u1"}   # ràng buộc nhóm + người gửi cho bridge đối chiếu
    assert pl["mm-1"].payload["forwarded"] is None and pl["mm-1"].payload["media"]["seconds"] == 5
    assert pl["mm-2"].kind == "image" and pl["mm-2"].payload["forwarded"] is True
    assert "media_ref" not in pl["mm-3"].payload
    assert pl["mm-4"].kind == "system" and pl["mm-4"].payload["revokes"] == "5001" and pl["mm-4"].payload["revokes_cli"] == "cli5001"
    assert "forwarded" not in pl["mm-5"].payload and "revokes" not in pl["mm-5"].payload
    assert {p.media_ref for p in await pend(org)} == {REF.format("1"), REF.format("2"), REF.format("5")}   # chỉ tin có ref hợp lệ


async def test_voice_thanh_cong_luu_file_va_chu(app, db, redis: Redis, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    await listen(db, org, "g1")
    audio = fake_voice_bytes("anh chuyển khoản 5 triệu cho chị Lan")
    [eid] = await put(sessionmaker(), org, media_msg("voice", "1"))
    f = FakeFetcher({REF.format("1"): audio})
    d = deps(redis, tmp_path, f)
    [ev] = await pend(org)
    assert await jobs.process_event(d, ev) == "ok"
    assert f.binds == [("g1", "u1")]                              # media.fetch mang đúng ràng buộc nhóm + người gửi
    [t] = await rows(db)
    assert (t.kind, t.status, t.text, t.engine) == ("transcript", "ok", "anh chuyển khoản 5 triệu cho chị Lan", "fake-stt")
    att = (await db.execute(text("SELECT storage_key, mime, bytes, sha256 FROM raw.attachments"))).one()
    assert att.mime == "audio/ogg" and att.bytes == len(audio) and bytes(att.sha256) == hashlib.sha256(audio).digest()
    assert att.storage_key.endswith(".ogg") and ".." not in att.storage_key and (tmp_path / att.storage_key).read_bytes() == audio
    org_of_att = (await db.execute(text("SELECT org_id FROM raw.attachments"))).scalar_one()
    assert org_of_att == org                                     # trigger điền org_id (L1)
    assert await pend(org) == []                                 # xong ⇒ rời hàng đợi
    st = (await status.event_status(db, org, [eid]))[eid]
    assert (st.state, st.label, st.reason, st.will_retry) == ("ok", "Đã đọc", None, False)
    assert await jobs.process_event(d, ev) == "ok"               # chạy lại không nhân đôi hàng
    assert len(await rows(db)) == 1


async def test_dang_cho_truoc_khi_xu_ly_va_doc_duoc_trang_thai(app, db) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    await listen(db, org, "g1")
    [vid, iid] = await put(sessionmaker(), org, media_msg("voice", "1"), media_msg("image", "2"))
    [text_id] = await put(sessionmaker(), org, msg("tin chữ"))
    got = await status.event_status(db, org, [vid, iid, text_id])
    assert text_id not in got                                      # tin chữ không phải media
    assert got[vid].state == "pending" and got[vid].label == "Đang chờ đọc" and "voice" in (got[vid].reason or "")
    assert "ảnh" in (got[iid].reason or "")


async def test_anh_ocr_va_mo_ta_va_luat_a_tren_chu_ocr(app, db, redis: Redis, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    await listen(db, org, "g1")
    [eid] = await put(sessionmaker(), org, media_msg("image", "1"))
    img = fake_image_bytes("UNC 1.500.000đ STK 0123456789", "Ảnh chụp màn hình chuyển khoản")
    scanned: list[tuple[str, str]] = []

    def scan(t: str, field: str) -> list[Any]:
        scanned.append((t, field))
        return ["ứng viên"] if "STK" in t else []

    d = deps(redis, tmp_path, FakeFetcher({REF.format("1"): img}), scan=scan)
    [ev] = await pend(org)
    assert await jobs.process_event(d, ev) == "ok"
    got = {r.kind: r for r in await rows(db)}
    assert got["ocr"].text == "UNC 1.500.000đ STK 0123456789" and got["description"].text == "Ảnh chụp màn hình chuyển khoản"
    assert [s[1] for s in scanned] == ["ocr", "caption"]      # luật (a) chạy trên OCR và mô tả
    assert (await db.execute(text("SELECT fast FROM refinery.event_state WHERE event_id = :e"), {"e": eid})).scalar() is True


async def test_khong_co_ung_vien_thi_khong_nang_duong_nhanh(app, db, redis: Redis, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    await listen(db, org, "g1")
    [eid] = await put(sessionmaker(), org, media_msg("voice", "1"))
    d = deps(redis, tmp_path, FakeFetcher({REF.format("1"): fake_voice_bytes("chào buổi sáng")}), scan=lambda t, f: [])
    [ev] = await pend(org)
    await jobs.process_event(d, ev)
    assert (await db.execute(text("SELECT fast FROM refinery.event_state WHERE event_id = :e"), {"e": eid})).scalar() is False


async def test_qua_lon_khai_bao_chot_ngay_khong_cham_mang(app, db, redis: Redis, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    await listen(db, org, "g1")
    [a, b, c] = await put(sessionmaker(), org,
                          media_msg("voice", "1", media={"kind": "voice", "mime": "audio/ogg", "size": 6 * 1024 * 1024, "seconds": 5}),
                          media_msg("voice", "2", media={"kind": "voice", "mime": "audio/ogg", "size": 100, "seconds": 601}),
                          media_msg("image", "3", media={"kind": "image", "mime": "image/svg+xml", "size": 10, "seconds": 0}))
    f = FakeFetcher()
    d = deps(redis, tmp_path, f)
    for ev in await pend(org):
        await jobs.process_event(d, ev)
    assert f.calls == []                                            # không tải gì
    got = {r.kind + str(i): (r.status, r.error_code) for i, r in enumerate(await rows(db))}
    assert sorted(got.values()) == [("failed", "mime_not_allowed"), ("too_large", "too_large"), ("too_large", "too_large")]
    st = await status.event_status(db, org, [a, b, c])
    assert st[a].state == "too_large" and "5 MB" in (st[a].reason or "") and not st[a].will_retry
    assert st[c].state == "failed" and st[c].error_code == "mime_not_allowed" and not st[c].will_retry
    assert await pend(org) == []                                    # chung cuộc: không nhặt lại
    att = (await db.execute(text("SELECT storage_key, bytes FROM raw.attachments ORDER BY storage_key"))).all()
    assert all(a_.storage_key.startswith("none:") and a_.bytes == 0 for a_ in att)   # giữ chỗ, không có blob


async def test_loi_tai_co_dinh_vs_tam_thoi(app, db, redis: Redis, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    await listen(db, org, "g1")
    [gone, flaky] = await put(sessionmaker(), org, media_msg("voice", "1"), media_msg("voice", "2"))
    f = FakeFetcher(errors={REF.format("1"): "not_found", REF.format("2"): "timeout"})
    d = deps(redis, tmp_path, f)
    for ev in await pend(org):
        await jobs.process_event(d, ev)
    st = await status.event_status(db, org, [gone, flaky])
    assert (st[gone].state, st[gone].error_code, st[gone].will_retry) == ("failed", "not_found", True)   # lần 1: còn thử lại
    assert (st[flaky].state, st[flaky].error_code, st[flaky].will_retry) == ("failed", "timeout", True)
    assert len(await pend(org)) == 2                                         # cả hai còn trong hàng đợi
    for ev in await pend(org):
        await jobs.process_event(d, ev)                                      # lần 2: not_found chốt `gone`
    st = await status.event_status(db, org, [gone, flaky])
    assert (st[gone].error_code, st[gone].will_retry) == ("gone", False) and "không còn lấy được" in (st[gone].reason or "")
    assert st[flaky].will_retry is True
    assert [p.media_ref for p in await pend(org)] == [REF.format("2")]      # lỗi tạm còn trong hàng đợi, lỗi cố định thì không


async def test_thu_lai_toi_da_roi_chot_gave_up(app, db, redis: Redis, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    await listen(db, org, "g1")
    [eid] = await put(sessionmaker(), org, media_msg("voice", "1"))
    f = FakeFetcher(errors={REF.format("1"): "download_failed"})
    d = deps(redis, tmp_path, f, max_attempts=2)
    for _ in range(2):
        [ev] = await pend(org)
        assert await jobs.process_event(d, ev) == "failed"
    [ev] = await pend(org)
    assert await jobs.process_event(d, ev) == "failed"             # lần 3 > 2 ⇒ chốt
    assert len(f.calls) == 2                                         # không tải lần thứ ba
    assert await pend(org) == []
    st = (await status.event_status(db, org, [eid]))[eid]
    assert (st.error_code, st.will_retry) == ("gave_up", False) and "nhiều lần" in (st.reason or "")


async def test_engine_loi_giu_file_va_thu_lai_khong_tai_lai(app, db, redis: Redis, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    await listen(db, org, "g1")
    [eid] = await put(sessionmaker(), org, media_msg("voice", "1"))
    f = FakeFetcher({REF.format("1"): fake_voice_bytes("đã đọc được rồi")})
    [ev] = await pend(org)
    assert await jobs.process_event(deps(redis, tmp_path, f, stt=FakeSttEngine(fail="engine_unavailable"), unavailable_cooldown_s=0), ev) == "failed"
    st = (await status.event_status(db, org, [eid]))[eid]
    assert (st.state, st.error_code, st.will_retry) == ("failed", "engine_unavailable", True)
    assert len(f.calls) == 1
    [ev] = await pend(org)                                           # vẫn trong hàng đợi
    assert await jobs.process_event(deps(redis, tmp_path, f), ev) == "ok"
    assert len(f.calls) == 1                                         # đọc lại file đã lưu, không hỏi bridge nữa
    assert (await status.event_status(db, org, [eid]))[eid].state == "ok"
    assert (await db.execute(text("SELECT count(*) FROM raw.attachments"))).scalar() == 1


async def test_mo_ta_anh_loi_khong_mat_chu_ocr(app, db, redis: Redis, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    await listen(db, org, "g1")
    [eid] = await put(sessionmaker(), org, media_msg("image", "1"))
    d = deps(redis, tmp_path, FakeFetcher({REF.format("1"): fake_image_bytes("Hoá đơn 200k", "x")}),
             vision=FakeVisionEngine(fail_describe="engine_error"))
    [ev] = await pend(org)
    assert await jobs.process_event(d, ev) == "ok"
    got = {r.kind: (r.status, r.error_code) for r in await rows(db)}
    assert got == {"ocr": ("ok", None), "description": ("failed", "engine_error")}
    assert (await status.event_status(db, org, [eid]))[eid].state == "ok"


async def test_het_ngan_sach_ghi_skipped_va_thu_lai_sau(app, db, redis: Redis, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    await listen(db, org, "g1")
    [eid] = await put(sessionmaker(), org, media_msg("voice", "1"))
    f = FakeFetcher({REF.format("1"): fake_voice_bytes("sau này đọc")})
    allow = {"v": False}

    async def budget(db_: Any, org_: uuid.UUID) -> bool:
        return allow["v"]

    d = deps(redis, tmp_path, f, budget_ok=budget)
    [ev] = await pend(org)
    assert await jobs.process_event(d, ev) == "skipped_budget"
    assert f.calls == []
    st = (await status.event_status(db, org, [eid]))[eid]
    assert (st.state, st.will_retry, st.label) == ("skipped_budget", True, "Chờ ngân sách AI")
    allow["v"] = True
    [ev] = await pend(org)
    assert await jobs.process_event(d, ev) == "ok"
    assert (await status.event_status(db, org, [eid]))[eid].state == "ok"


async def test_khoa_chong_chay_trung(app, db, redis: Redis, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    await listen(db, org, "g1")
    await put(sessionmaker(), org, media_msg("voice", "1"))
    f = FakeFetcher({REF.format("1"): fake_voice_bytes("x")})
    d = deps(redis, tmp_path, f)
    [ev] = await pend(org)
    await redis.set(jobs.claim_key(ev.id), b"1")
    assert await jobs.process_event(d, ev) == "claimed" and f.calls == []


async def test_sweep_xu_ly_ca_lo_va_do_phu(app, db, redis: Redis, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    await listen(db, org, "g1")
    await put(sessionmaker(), org, media_msg("voice", "1"), media_msg("voice", "2"), media_msg("image", "3"), media_msg("image", "4"))
    f = FakeFetcher({REF.format("1"): fake_voice_bytes("một"), REF.format("2"): fake_voice_bytes("hai"),
                     REF.format("3"): fake_image_bytes("ba", "")}, errors={REF.format("4"): "timeout"})
    d = deps(redis, tmp_path, f)
    counts = await jobs.sweep_org(d, org)
    assert counts == {"ok": 3, "failed": 1}
    cov = await status.coverage(db, org)
    assert cov["lines"] == ["2 voice đã chép · 1 ảnh chưa đọc ở Nhóm g1"]
    g = cov["groups"][0]
    assert (g["voice_read"], g["voice_unread"], g["image_read"], g["image_unread"]) == (2, 0, 1, 1)


async def test_job_dang_ky_trong_cum_biz() -> None:
    from gh import biz

    assert jobs.media_sweep in [fn for fn, _ in biz.jobs()]


async def test_tin_thieu_rang_buoc_khong_tai_chot_bad_request(app, db, redis: Redis, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    await listen(db, org, "g1")
    [eid] = await put(sessionmaker(), org, media_msg("voice", "1"))
    f = FakeFetcher({REF.format("1"): fake_voice_bytes("x")})
    [ev] = await pend(org)
    old = dataclasses.replace(ev, bind={})                       # tin cũ trước khi có ràng buộc (raw.events chỉ-INSERT nên dựng tay)
    assert await jobs.process_event(deps(redis, tmp_path, f), old) == "failed"
    assert f.calls == []
    assert (await status.event_status(db, org, [eid]))[eid].error_code == "bad_request"


async def test_engine_chua_bat_khong_tinh_luot_thu_khong_gave_up_va_doc_lai_khi_co(app, db, redis: Redis, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    await listen(db, org, "g1")
    [eid] = await put(sessionmaker(), org, media_msg("voice", "1"))
    f = FakeFetcher({REF.format("1"): fake_voice_bytes("đọc được sau khi bật công cụ")})
    off = deps(redis, tmp_path, f, stt=FakeSttEngine(fail="engine_unavailable"), unavailable_cooldown_s=0, max_attempts=2)
    for _ in range(6):                                   # nhiều hơn max_attempts nhiều lần
        [ev] = await pend(org)
        assert await jobs.process_event(off, ev) == "failed"
    st = (await status.event_status(db, org, [eid]))[eid]
    assert (st.state, st.error_code, st.will_retry) == ("failed", "engine_unavailable", True)
    assert st.reason == "Chưa bật công cụ đọc voice/ảnh."
    assert len(f.calls) == 1                             # file đã lưu, không tải lại
    assert await redis.get(jobs.attempts_key(ev.id)) in (b"0", 0, None)      # không tính lượt thử
    [ev] = await pend(org)                               # vẫn trong hàng đợi, KHÔNG gave_up
    assert await jobs.process_event(deps(redis, tmp_path, f), ev) == "ok"      # công cụ được bật ⇒ đọc lại
    assert (await status.event_status(db, org, [eid]))[eid].state == "ok"


async def test_engine_chua_bat_thu_lai_cham_15_phut(app, db, redis: Redis, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    await listen(db, org, "g1")
    await put(sessionmaker(), org, media_msg("voice", "1"))
    f = FakeFetcher({REF.format("1"): fake_voice_bytes("x")})
    d = deps(redis, tmp_path, f, stt=FakeSttEngine(fail="engine_unavailable"))
    assert d.unavailable_cooldown_s == 900
    [ev] = await pend(org)
    assert await jobs.process_event(d, ev) == "failed"
    assert 800 < await redis.ttl(jobs.cooldown_key(ev.id)) <= 900
    stt = FakeSttEngine()
    d2 = deps(redis, tmp_path, f, stt=stt)
    assert await jobs.process_event(d2, ev) == "cooldown" and stt.calls == 0      # chưa tới giờ: không đụng engine
    await redis.delete(jobs.cooldown_key(ev.id))
    assert await jobs.process_event(d2, ev) == "ok"


async def test_will_retry_sai_khi_qua_cua_so_quet(app, db, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    await listen(db, org, "g1")
    [eid] = await put(sessionmaker(), org, media_msg("voice", "1"))
    assert (await status.event_status(db, org, [eid]))[eid].will_retry is True
    monkeypatch.setattr(mf, "RETRY_WINDOW_H", 0)           # tin coi như đã quá cửa sổ quét
    assert (await status.event_status(db, org, [eid]))[eid].will_retry is False


def test_moi_loai_chu_media_map_dung_truong_nguon_luat_a() -> None:
    # Hợp đồng L5 ↔ L2: mọi trường nguồn L5 truyền phải nằm trong `prefilter.SOURCE_FIELDS` (CI PR #5 đỏ vì 'media_texts.text').
    from gh.biz.media import prefilter_port
    from gh.refinery import prefilter
    for kind in ("transcript", "ocr", "description", "loai_la"):
        assert prefilter_port.source_field_for(kind) in prefilter.SOURCE_FIELDS
    assert prefilter_port.SOURCE_FIELD in prefilter.SOURCE_FIELDS
    assert prefilter.scan_text("em đã nhập mã OTP rồi", prefilter_port.source_field_for("transcript"))
