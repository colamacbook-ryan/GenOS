"""v0.1.69 (C4; F098 F103 F104): chuông có vòng đời — đóng sự cố thì chuông tắt, không trùng, thiếu sao lưu có chuông.

- F098: `health.clear()` đánh dấu chuông của chính sự cố đó là "Đã xử lý" (đã đọc + nhãn);
  `GET /notifications` trả `total` để chuông hiện "Xem thêm" khi nhiều hơn số mục đang liệt kê.
- F104: bản sao ngoài máy chưa cắm ổ chỉ một sự cố (offsite.stale); cập nhật lỗi lặp lại cùng mã lỗi không chuông mới;
  `verify_action_log` không chèn thêm cảnh báo khi cảnh báo cho cùng dòng đứt còn mở.
- F103: "chưa cấu hình sao lưu" (quá 24 giờ kể từ khi tạo tổ chức) là sự cố backup.stale có chuông, tự đóng khi có lịch.
"""

import json
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text

from gh import health, notifications
from gh.config import get_settings
from gh.db import sessionmaker
from tests.conftest import Api
from tests.phase2 import org_id


@pytest.fixture
def link(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    d = tmp_path / "run"
    (d / "request").mkdir(parents=True)
    (d / "genh.json").write_text(json.dumps({"version": "v0.1.68", "updater": "systemd",
                                             "requests": ["update", "restore", "offsite"]}))
    monkeypatch.setenv("GH_HOST_LINK_DIR", str(d))
    get_settings.cache_clear()
    yield d
    get_settings.cache_clear()


def _iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat().replace("+00:00", "Z")


async def _evaluate(redis: Any, org: Any, now: datetime | None = None) -> None:
    now = now or datetime.now(UTC)
    async with sessionmaker()() as s:
        await health.evaluate(s, redis, org, now=now, started_at=now)
        await s.commit()


async def _bells(db: Any, kind: str) -> list[Any]:
    await db.commit()
    return list((await db.execute(text("""SELECT title, body, read_at FROM core.notifications WHERE kind = :k
                                          ORDER BY created_at"""), {"k": kind})).all())


async def _alerts(db: Any, kind: str) -> list[Any]:
    await db.commit()
    return list((await db.execute(text("""SELECT key, severity, title, body, fingerprint, cleared_at
                                          FROM ops.health_alerts WHERE kind = :k ORDER BY key"""), {"k": kind})).all())


async def _raise(db: Any, org: Any, key: str, kind: str, title: str, **kw: Any) -> None:
    await health.raise_once(db, org, key=key, kind=kind, severity="warn", title=title, body="Chi tiết", link=None, **kw)
    await db.commit()


async def _clear(db: Any, org: Any, key: str, **kw: Any) -> None:
    await health.clear(db, org, key, **kw)
    await db.commit()


# ─── F098: sự cố đóng thì chuông tắt ──────────────────────────────────────────────────────────────────────────

async def test_clear_marks_bell_resolved_and_read(owner_api: Api, db) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    await _raise(db, org, "worker.silent", "worker.silent", "Bộ xử lý nền đã ngừng")
    before = (await owner_api.get("/notifications")).json()
    assert before["unread"] == 1 and before["items"][0]["read"] is False and before["items"][0]["resolved"] is False

    await _clear(db, org, "worker.silent")
    after = (await owner_api.get("/notifications")).json()
    assert after["unread"] == 0
    [item] = after["items"]
    assert item["read"] is True and item["resolved"] is True
    assert item["title"] == "Bộ xử lý nền đã ngừng"  # nhãn nằm ở cờ `resolved`, không đè lên tiêu đề
    assert item["resolved_at"]


async def test_clear_is_idempotent_and_does_not_touch_other_kinds(owner_api: Api, db) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    await _raise(db, org, "disk.low", "disk.low", "Ổ đĩa sắp hết chỗ")
    await _raise(db, org, "worker.silent", "worker.silent", "Bộ xử lý nền đã ngừng")
    await _clear(db, org, "disk.low")
    await _clear(db, org, "disk.low")  # lần hai: không còn dòng mở ⇒ không làm gì
    page = (await owner_api.get("/notifications")).json()
    by_kind = {i["kind"]: i for i in page["items"]}
    assert by_kind["disk.low"]["resolved"] is True
    assert by_kind["worker.silent"]["resolved"] is False and by_kind["worker.silent"]["read"] is False
    assert page["unread"] == 1


async def test_clear_one_of_many_keys_of_same_kind_only_resolves_its_own_bell(owner_api: Api, db) -> None:  # type: ignore[no-untyped-def]
    """job.timeout:<tên hàm> — nhiều khoá cùng `kind`: đóng một khoá không được tắt chuông của khoá còn mở."""
    org = await org_id(db)
    await _raise(db, org, "job.timeout:a", "job.timeout", "Việc nền a chạy quá giờ")
    await _raise(db, org, "job.timeout:b", "job.timeout", "Việc nền b chạy quá giờ")
    await _clear(db, org, "job.timeout:a")
    page = (await owner_api.get("/notifications")).json()
    by_title = {i["title"]: i for i in page["items"]}
    assert by_title["Việc nền a chạy quá giờ"]["resolved"] is True
    b = by_title["Việc nền b chạy quá giờ"]
    assert b["resolved"] is False and b["read"] is False
    await _clear(db, org, "job.timeout:b")
    page = (await owner_api.get("/notifications")).json()
    assert all(i["resolved"] for i in page["items"]) and page["unread"] == 0


async def test_clear_same_title_other_key_does_not_resolve_the_other_bell(owner_api: Api, db) -> None:  # type: ignore[no-untyped-def]
    """Review P3: hai khoá cùng `kind` TRÙNG tiêu đề (hai nguồn cùng tên) ⇒ chọn chuông theo tiêu đề tắt nhầm chuông
    kia. Chuông lưu khoá sự cố (`incident_key`) nên đóng khoá nào tắt đúng chuông của khoá đó."""
    org = await org_id(db)
    title = "Model hết đăng nhập: Gemini"
    await _raise(db, org, "model.auth_expired:a", "model.auth_expired", title)
    await _raise(db, org, "model.auth_expired:b", "model.auth_expired", title)
    q = """SELECT incident_key, resolved_at IS NOT NULL AS resolved, read_at IS NOT NULL AS read
           FROM core.notifications WHERE kind = 'model.auth_expired' ORDER BY created_at"""
    keys = [r.incident_key for r in (await db.execute(text(q))).all()]
    assert keys == ["model.auth_expired:a", "model.auth_expired:b"]
    await _clear(db, org, "model.auth_expired:a")
    rows = (await db.execute(text(q))).all()
    assert [(r.incident_key, r.resolved, r.read) for r in rows] == [
        ("model.auth_expired:a", True, True), ("model.auth_expired:b", False, False)]


async def test_clear_still_resolves_legacy_bell_without_incident_key(owner_api: Api, db) -> None:  # type: ignore[no-untyped-def]
    """Chuông tạo trước v0.1.69 chưa có khoá (NULL) ⇒ vẫn tắt theo kind (/ tiêu đề khi còn khoá khác cùng kind)."""
    org = await org_id(db)
    uid = (await notifications.owner_ids(db, org))[0]
    await notifications.notify(db, org, [uid], kind="worker.silent", title="Bộ xử lý nền đã ngừng")  # không khoá
    await db.execute(text("""INSERT INTO ops.health_alerts (org_id, key, kind, severity, fingerprint, title, body)
                             VALUES (:o, 'worker.silent', 'worker.silent', 'bad', '', 'Bộ xử lý nền đã ngừng', 'x')"""),
                     {"o": org})
    await db.commit()
    await _clear(db, org, "worker.silent")
    [item] = (await owner_api.get("/notifications")).json()["items"]
    assert item["resolved"] is True and item["read"] is True


class _FakeRedis:
    def __init__(self) -> None:
        self.published: list[dict[str, Any]] = []

    async def publish(self, channel: str, payload: bytes) -> None:
        import orjson

        self.published.append(orjson.loads(payload))


async def test_clear_pushes_notification_resolved_after_commit(owner_api: Api, db) -> None:  # type: ignore[no-untyped-def]
    """Review P3: đóng sự cố phải đẩy WebSocket `notification.resolved` (chỉ tới chủ chuông, sau commit) để chuông
    đang mở đổi ngay sang "Đã xử lý" — không đợi 120 giây tải lại."""
    import asyncio

    org = await org_id(db)
    redis = _FakeRedis()
    await health.raise_once(db, org, key="disk.low", kind="disk.low", severity="warn", title="Ổ đĩa sắp hết chỗ",
                            body="x", link=None, redis=redis)
    await db.commit()
    await asyncio.sleep(0.05)
    assert [m["type"] for m in redis.published] == ["notification.new"]
    await health.clear(db, org, "disk.low", redis=redis)
    await asyncio.sleep(0.05)
    assert len(redis.published) == 1  # chưa commit ⇒ chưa đẩy
    await db.commit()
    await asyncio.sleep(0.05)
    assert [m["type"] for m in redis.published] == ["notification.new", "notification.resolved"]
    msg = redis.published[1]
    assert msg["data"]["kind"] == "disk.low" and msg["data"]["resolved"] is True and msg["data"]["read"] is True
    assert msg["to_user"] == redis.published[0]["to_user"]


async def test_clear_without_label_pushes_read_but_not_resolved(owner_api: Api, db) -> None:  # type: ignore[no-untyped-def]
    import asyncio

    org = await org_id(db)
    redis = _FakeRedis()
    await health.raise_once(db, org, key="disk.low", kind="disk.low", severity="warn", title="Ổ đĩa sắp hết chỗ",
                            body="x", link=None, redis=redis)
    await health.clear(db, org, "disk.low", label=False, redis=redis)
    await db.commit()
    await asyncio.sleep(0.05)
    data = [m["data"] for m in redis.published if m["type"] == "notification.resolved"]
    assert len(data) == 1 and data[0]["read"] is True and data[0]["resolved"] is False


async def test_clear_resolves_every_old_bell_of_the_incident(owner_api: Api, db) -> None:  # type: ignore[no-untyped-def]
    """Cùng sự cố mở lại nhiều lần (fingerprint đổi) để lại nhiều chuông — hết sự cố thì TẤT CẢ đều "Đã xử lý"."""
    org = await org_id(db)
    await _raise(db, org, "backup.stale", "backup.stale", "Đã hơn 36 giờ chưa có bản sao lưu mới", fingerprint="1")
    await _raise(db, org, "backup.stale", "backup.stale", "Đã hơn 36 giờ chưa có bản sao lưu mới", fingerprint="2")
    assert len(await _bells(db, "backup.stale")) == 2
    await _clear(db, org, "backup.stale")
    page = (await owner_api.get("/notifications")).json()
    assert page["unread"] == 0 and [i["resolved"] for i in page["items"]] == [True, True]


async def test_clear_with_label_false_only_reads(owner_api: Api, db) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    await _raise(db, org, "offsite.failed", "offsite.failed", "Sao lưu ra ổ ngoài chưa thành công")
    await _clear(db, org, "offsite.failed", label=False)
    [item] = (await owner_api.get("/notifications")).json()["items"]
    assert item["read"] is True and item["resolved"] is False


async def test_new_incident_after_clear_rings_a_fresh_unread_bell(owner_api: Api, db) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    await _raise(db, org, "disk.low", "disk.low", "Ổ đĩa sắp hết chỗ")
    await _clear(db, org, "disk.low")
    await _raise(db, org, "disk.low", "disk.low", "Ổ đĩa sắp hết chỗ")
    page = (await owner_api.get("/notifications")).json()
    assert page["unread"] == 1
    assert sorted((i["resolved"], i["read"]) for i in page["items"]) == [(False, False), (True, True)]


async def test_list_reports_total_beyond_limit(owner_api: Api, db) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    uid = (await notifications.owner_ids(db, org))[0]
    for i in range(25):
        await notifications.notify(db, org, [uid], kind="backup.done", title=f"Sao lưu xong {i}")
    await db.commit()
    page = (await owner_api.get("/notifications", params={"limit": 20})).json()
    assert len(page["items"]) == 20 and page["total"] == 25 and page["unread"] == 25
    full = (await owner_api.get("/notifications", params={"limit": 100})).json()
    assert len(full["items"]) == 25 and full["total"] == 25


async def test_list_offset_pages_past_the_100_limit(owner_api: Api, db) -> None:  # type: ignore[no-untyped-def]
    """Review P3: `limit` tối đa 100 ⇒ chuông có quá 100 thông báo không xem hết được; `offset` để lật trang."""
    org = await org_id(db)
    uid = (await notifications.owner_ids(db, org))[0]
    for i in range(105):
        await notifications.notify(db, org, [uid], kind="backup.done", title=f"Sao lưu xong {i}")
    await db.commit()
    p1 = (await owner_api.get("/notifications", params={"limit": 100})).json()
    p2 = (await owner_api.get("/notifications", params={"limit": 100, "offset": 100})).json()
    assert len(p1["items"]) == 100 and len(p2["items"]) == 5 and p2["total"] == 105
    ids = [i["id"] for i in p1["items"] + p2["items"]]
    assert len(set(ids)) == 105  # không trùng, không sót
    assert p2["items"][-1]["title"] == "Sao lưu xong 0"  # mới nhất trước, cũ nhất ở cuối trang cuối
    assert (await owner_api.get("/notifications", params={"offset": -1})).status_code == 422


# ─── F104: không trùng lặp ────────────────────────────────────────────────────────────────────────────────────

def _offsite_status(d: Path, **kw: Any) -> None:
    base = {"schema": 1, "configured": True, "dest": "/media/usb", "state": "ok", "error_code": "",
            "last_attempt_at": "", "last_success_at": "", "last_file": "", "last_size_bytes": 0, "verified": True,
            "kept": 1, "schedule": "systemd", "key_id": "0badc0de"}
    (d / "offsite-status.json").write_text(json.dumps({**base, **kw}))


async def test_unplugged_usb_is_one_incident_not_two(owner_api: Api, link: Path, db, redis) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    now = datetime.now(UTC)
    _offsite_status(link, state="not_mounted", error_code="GH-EB01", last_attempt_at=_iso(now - timedelta(hours=1)),
                    last_success_at=_iso(now - timedelta(days=9)))
    await _evaluate(redis, org)
    await _evaluate(redis, org)
    [stale] = await _alerts(db, "offsite.stale")
    assert stale.cleared_at is None
    assert await _alerts(db, "offsite.failed") == []
    assert len(await _bells(db, "offsite.stale")) == 1 and await _bells(db, "offsite.failed") == []
    active = [i["kind"] for i in await health.active_issues(db, org) if i["kind"].startswith("offsite")]
    assert active == ["offsite.stale"]


async def test_failed_alert_open_is_folded_into_stale_without_label(owner_api: Api, link: Path, db, redis) -> None:  # type: ignore[no-untyped-def]
    """Đã có offsite.failed (ổ chưa quá cũ), vài ngày sau thành offsite.stale cùng nguyên nhân ⇒ failed đóng gộp,
    chuông failed được đọc nhưng KHÔNG gắn "Đã xử lý" (sự cố vẫn còn, chỉ gộp vào dòng stale)."""
    org = await org_id(db)
    now = datetime.now(UTC)
    _offsite_status(link, state="not_mounted", error_code="GH-EB01", last_attempt_at=_iso(now - timedelta(hours=1)),
                    last_success_at=_iso(now - timedelta(days=2)))
    await _evaluate(redis, org)
    [failed] = await _alerts(db, "offsite.failed")
    assert failed.cleared_at is None
    _offsite_status(link, state="not_mounted", error_code="GH-EB01", last_attempt_at=_iso(now + timedelta(days=7)),
                    last_success_at=_iso(now - timedelta(days=2)))
    await _evaluate(redis, org, now + timedelta(days=7, minutes=1))
    [failed] = await _alerts(db, "offsite.failed")
    [stale] = await _alerts(db, "offsite.stale")
    assert failed.cleared_at is not None and stale.cleared_at is None
    [bell] = await _bells(db, "offsite.failed")
    assert bell.read_at is not None
    page = (await owner_api.get("/notifications")).json()
    assert [i["resolved"] for i in page["items"] if i["kind"] == "offsite.failed"] == [False]


async def test_other_offsite_failure_stays_a_separate_incident(owner_api: Api, link: Path, db, redis) -> None:  # type: ignore[no-untyped-def]
    """Khác nguyên nhân (ổ cắm rồi nhưng không ghi được — GH-EB04) ⇒ cách chữa khác ⇒ vẫn là sự cố riêng."""
    org = await org_id(db)
    now = datetime.now(UTC)
    _offsite_status(link, state="failed", error_code="GH-EB04", last_attempt_at=_iso(now),
                    last_success_at=_iso(now - timedelta(days=9)))
    await _evaluate(redis, org)
    assert len(await _alerts(db, "offsite.stale")) == 1 and len(await _alerts(db, "offsite.failed")) == 1


async def _update_status(link: Path, **kw: Any) -> None:
    (link / "update-status.json").write_text(json.dumps({"state": "failed", "from": "v0.1.67", "to": "v0.1.68", **kw}))


async def test_update_failed_same_error_code_every_night_rings_once(owner_api: Api, link: Path, db, redis) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    now = datetime.now(UTC)
    for hours in (5, 3, 1):  # ba đêm thử lại, cùng lỗi thiếu chỗ ổ đĩa
        await _update_status(link, finished_at=_iso(now - timedelta(hours=hours)),
                             message="Hết chỗ trống để cập nhật (GH-E948)")
        await _evaluate(redis, org)
    bells = await _bells(db, "update.failed")
    assert len(bells) == 1 and bells[0].title == "Cập nhật lên v0.1.68 chưa thành công"
    # lỗi KHÁC (mã khác) ⇒ chuông mới
    await _update_status(link, finished_at=_iso(now), message="Không tải được bản mới (GH-E901)")
    await _evaluate(redis, org)
    assert len(await _bells(db, "update.failed")) == 2
    [alert] = await _alerts(db, "update.failed")
    assert "GH-E901" in alert.fingerprint and _iso(now) not in alert.fingerprint


async def test_update_failed_worse_outcome_rings_again(owner_api: Api, link: Path, db, redis) -> None:  # type: ignore[no-untyped-def]
    """Cùng mã lỗi nhưng kết cục nặng hơn (quay về bản cũ cũng lỗi) ⇒ vẫn đáng một chuông mới."""
    org = await org_id(db)
    now = datetime.now(UTC)
    await _update_status(link, finished_at=_iso(now - timedelta(hours=3)), message="lỗi (GH-E948)")
    (link / "update-blocked.json").write_text(json.dumps({"version": "v0.1.68"}))
    await _evaluate(redis, org)
    await _update_status(link, finished_at=_iso(now - timedelta(hours=1)), message="lỗi (GH-E948)")
    (link / "update-blocked.json").write_text(json.dumps({"version": "v0.1.68", "rollback_failed": True}))
    await _evaluate(redis, org)
    assert len(await _bells(db, "update.failed")) == 2


async def test_update_failed_nightly_slot_beyond_24h_keeps_one_incident(owner_api: Api, link: Path, db, redis) -> None:  # type: ignore[no-untyped-def]
    """Review P2-1: lịch đêm 03:00 + RandomizedDelaySec 1800 ⇒ lần thử kế cách lần trước tới 24 giờ 30 phút. Hạn 24 giờ
    đóng sự cố giữa hai lần thử rồi mở lại (chuông lặp mỗi đêm) ⇒ hạn 26 giờ: cách 25 giờ vẫn là MỘT sự cố mở."""
    org = await org_id(db)
    now = datetime.now(UTC)
    await _update_status(link, finished_at=_iso(now - timedelta(hours=25)), message="Hết chỗ trống (GH-E948)")
    await _evaluate(redis, org)
    [alert] = await _alerts(db, "update.failed")
    assert alert.cleared_at is None
    # lần thử đêm sau (cùng lỗi, finished_at mới) ⇒ vẫn một chuông, một sự cố
    await _update_status(link, finished_at=_iso(now), message="Hết chỗ trống để cập nhật (GH-E948)")
    await _evaluate(redis, org)
    assert len(await _bells(db, "update.failed")) == 1
    [alert] = await _alerts(db, "update.failed")
    assert alert.cleared_at is None


async def test_update_failed_expiry_reads_but_does_not_label_resolved(owner_api: Api, link: Path, db, redis) -> None:  # type: ignore[no-untyped-def]
    """Review P2-1: lỗi tự hết hạn (không ai sửa) ⇒ chuông được đọc nhưng KHÔNG gắn "Đã xử lý" (sai sự thật);
    lần cập nhật sau thành công (state=done) mới là "Đã xử lý"."""
    org = await org_id(db)
    now = datetime.now(UTC)
    await _update_status(link, finished_at=_iso(now - timedelta(hours=1)), message="Hết chỗ trống (GH-E948)")
    await _evaluate(redis, org)
    await _evaluate(redis, org, now + timedelta(hours=28))  # quá hạn 26 giờ
    [bell] = await _bells(db, "update.failed")
    assert bell.read_at is not None
    page = (await owner_api.get("/notifications")).json()
    assert [i["resolved"] for i in page["items"] if i["kind"] == "update.failed"] == [False]


async def test_update_done_after_failure_labels_resolved(owner_api: Api, link: Path, db, redis) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    await _update_status(link, finished_at=_iso(datetime.now(UTC) - timedelta(hours=1)), message="lỗi (GH-E948)")
    await _evaluate(redis, org)
    (link / "update-status.json").write_text(json.dumps({"state": "done", "to": "v0.1.68"}))
    await _evaluate(redis, org)
    page = (await owner_api.get("/notifications")).json()
    assert [i["resolved"] for i in page["items"] if i["kind"] == "update.failed"] == [True]


async def _break_chain(db: Any) -> None:
    from gh.db import admin_sessionmaker

    async with admin_sessionmaker()() as adm:
        await adm.execute(text("ALTER TABLE ops.action_log DISABLE TRIGGER USER"))
        await adm.execute(text("UPDATE ops.action_log SET action = 'sua.ngam' WHERE action = 'setup.owner_created'"))
        await adm.execute(text("ALTER TABLE ops.action_log ENABLE TRIGGER USER"))
        await adm.commit()


async def test_verify_action_log_raises_one_alert_per_broken_row(owner_api: Api, db) -> None:  # type: ignore[no-untyped-def]
    from gh.worker import partition_maintenance, verify_action_log

    await partition_maintenance({})
    await _break_chain(db)
    first = await verify_action_log({})
    assert not any(v["ok"] for v in first.values())
    await verify_action_log({})
    await verify_action_log({})  # ba đêm liền, chuỗi vẫn đứt ở cùng một dòng
    await db.commit()
    rows = (await db.execute(text("SELECT code, status FROM biz.alerts WHERE alert_type = 'data_conflict'"))).all()
    assert len(rows) == 1 and rows[0].status == "open"

    # Owner đã xử lý cảnh báo (resolved) mà chuỗi vẫn đứt ⇒ báo lại một lần (không còn cảnh báo MỞ nào cho dòng đó)
    await db.execute(text("UPDATE biz.alerts SET status = 'resolved', resolved_at = now()"))
    await db.commit()
    await verify_action_log({})
    await verify_action_log({})
    rows = (await db.execute(text("SELECT status FROM biz.alerts WHERE alert_type = 'data_conflict' "
                                  "ORDER BY created_at"))).all()
    assert [r.status for r in rows] == ["resolved", "open"]


async def test_acknowledged_alert_counts_as_open(owner_api: Api, db) -> None:  # type: ignore[no-untyped-def]
    from gh.worker import partition_maintenance, verify_action_log

    await partition_maintenance({})
    await _break_chain(db)
    await verify_action_log({})
    await db.execute(text("UPDATE biz.alerts SET status = 'acknowledged'"))
    await db.commit()
    await verify_action_log({})
    n = (await db.execute(text("SELECT count(*) FROM biz.alerts WHERE alert_type = 'data_conflict'"))).scalar_one()
    assert n == 1


# ─── F103: chưa cấu hình sao lưu là sự cố ─────────────────────────────────────────────────────────────────────

async def _unconfigure_backup(db: Any, org: Any, *, age_hours: float) -> None:
    await db.execute(text("""UPDATE core.organizations SET settings = settings - 'backup',
                             created_at = now() - make_interval(hours => :h) WHERE id = :o"""),
                     {"h": age_hours, "o": org})
    await db.commit()


async def test_unconfigured_backup_within_grace_is_quiet(owner_api: Api, db, redis) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    await _unconfigure_backup(db, org, age_hours=2)
    await _evaluate(redis, org)
    assert await _alerts(db, "backup.stale") == [] and await _bells(db, "backup.stale") == []


async def test_unconfigured_backup_after_grace_rings_once_and_clears_when_scheduled(owner_api: Api, db, redis) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    await _unconfigure_backup(db, org, age_hours=30)
    await _evaluate(redis, org)
    await _evaluate(redis, org)
    [alert] = await _alerts(db, "backup.stale")
    assert alert.fingerprint == "not_configured" and alert.cleared_at is None
    assert alert.title == "Chưa đặt lịch sao lưu tự động"
    assert "Mở mục Sao lưu" in alert.body or "Sao lưu" in alert.body
    assert len(await _bells(db, "backup.stale")) == 1
    issues = [i for i in await health.active_issues(db, org) if i["kind"] == "backup.stale"]
    assert issues and issues[0]["action"] == "Mở mục Sao lưu"

    await db.execute(text("""UPDATE core.organizations SET settings = settings ||
                             '{"backup": {"frequency": "daily", "time_of_day": "02:00"}}'::jsonb WHERE id = :o"""),
                     {"o": org})
    await db.commit()
    await _evaluate(redis, org)
    [alert] = await _alerts(db, "backup.stale")
    assert alert.cleared_at is not None
    page = (await owner_api.get("/notifications")).json()
    [bell] = [i for i in page["items"] if i["kind"] == "backup.stale"]
    assert bell["resolved"] is True and page["unread"] == 0


async def test_system_health_still_reports_unconfigured_not_stale(owner_api: Api, db, redis) -> None:  # type: ignore[no-untyped-def]
    """Khối `backup` của /system/health giữ nghĩa cũ (configured=False, stale=False); chuông do vòng theo dõi lo."""
    org = await org_id(db)
    await _unconfigure_backup(db, org, age_hours=30)
    body = (await owner_api.get("/system/health")).json()
    assert body["backup"]["configured"] is False and body["backup"]["stale"] is False
    assert uuid.UUID(str(org))
