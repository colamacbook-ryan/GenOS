"""v0.1.40 (F-12): chuông offsite.stale / offsite.failed trong dải "Cần Sếp xử lý" + khối 'offsite' của /system/health.

- Chỉ khi có hộp thư với genh (run/); không có ⇒ khuôn /system/health cũ (test_health_v0136 kiểm set(body)).
- Chuông khử trùng lặp: chạy lại vòng theo dõi không sinh chuông thứ hai; đổi mức (warn → bad) mới có chuông mới.
"""

import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text

from gh import health
from gh.config import get_settings
from gh.db import sessionmaker
from tests.conftest import Api
from tests.phase2 import org_id


@pytest.fixture
def link(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    d = tmp_path / "run"
    (d / "request").mkdir(parents=True)
    (d / "genh.json").write_text(json.dumps({"version": "v0.1.40", "updater": "systemd",
                                             "requests": ["update", "restore", "offsite"]}))
    monkeypatch.setenv("GH_HOST_LINK_DIR", str(d))
    get_settings.cache_clear()
    yield d
    get_settings.cache_clear()


def _iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _status(d: Path, **kw: Any) -> None:
    base = {"schema": 1, "configured": True, "dest": "/media/usb", "state": "ok", "error_code": "",
            "last_attempt_at": "", "last_success_at": "", "last_file": "", "last_size_bytes": 0, "verified": True,
            "kept": 1, "schedule": "systemd", "key_id": "0badc0de"}
    (d / "offsite-status.json").write_text(json.dumps({**base, **kw}))


async def _evaluate(redis: Any, org: Any, now: datetime | None = None) -> None:
    now = now or datetime.now(UTC)
    async with sessionmaker()() as s:
        await health.evaluate(s, redis, org, now=now, started_at=now)
        await s.commit()


async def _alerts(db: Any, kind: str) -> list[Any]:
    await db.commit()
    return list((await db.execute(text("""SELECT key, severity, title, body, link, cleared_at FROM ops.health_alerts
                                          WHERE kind = :k"""), {"k": kind})).all())


async def _bells(db: Any, kind: str) -> list[Any]:
    await db.commit()
    return list((await db.execute(text("SELECT title, body, link FROM core.notifications WHERE kind = :k"),
                                  {"k": kind})).all())


async def _age_org(db: Any, org: Any, days: int) -> None:
    await db.execute(text("UPDATE core.organizations SET created_at = now() - make_interval(days => :d) WHERE id = :o"),
                     {"d": days, "o": org})
    await db.commit()


async def test_stale_8_days_rings_once_then_bad_after_30_then_clears(owner_api: Api, link: Path, db, redis) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    now = datetime.now(UTC)
    _status(link, last_attempt_at=_iso(now - timedelta(days=8)), last_success_at=_iso(now - timedelta(days=8)))
    await _evaluate(redis, org)
    [row] = await _alerts(db, "offsite.stale")
    assert row.key == "offsite.stale" and row.severity == "warn" and row.cleared_at is None
    assert row.title == "Bản sao ngoài máy đã cũ 8 ngày"
    assert row.link == health.OFFSITE_LINK == "/system?tab=storage&focus=offsite"
    assert len(await _bells(db, "offsite.stale")) == 1
    # chạy lại (kể cả qua ngày) ⇒ không chuông thứ hai
    await _evaluate(redis, org)
    await _evaluate(redis, org, now + timedelta(days=1))
    assert len(await _bells(db, "offsite.stale")) == 1
    [row] = await _alerts(db, "offsite.stale")
    assert row.title == "Bản sao ngoài máy đã cũ 9 ngày"

    # 31 ngày ⇒ 'bad' (mức đổi ⇒ một chuông mới)
    _status(link, last_attempt_at=_iso(now - timedelta(days=31)), last_success_at=_iso(now - timedelta(days=31)))
    await _evaluate(redis, org)
    [row] = await _alerts(db, "offsite.stale")
    assert row.severity == "bad"
    assert len(await _bells(db, "offsite.stale")) == 2
    await _evaluate(redis, org)
    assert len(await _bells(db, "offsite.stale")) == 2

    # bản mới ⇒ đóng sự cố
    _status(link, last_attempt_at=_iso(now), last_success_at=_iso(now))
    await _evaluate(redis, org)
    [row] = await _alerts(db, "offsite.stale")
    assert row.cleared_at is not None
    assert [i for i in await health.active_issues(db, org) if i["kind"].startswith("offsite")] == []


async def test_not_mounted_rings_failed_with_friendly_body(owner_api: Api, link: Path, db, redis) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    now = datetime.now(UTC)
    _status(link, state="not_mounted", error_code="GH-EB01", last_attempt_at=_iso(now - timedelta(hours=1)),
            last_success_at=_iso(now - timedelta(days=2)))
    await _evaluate(redis, org)
    [row] = await _alerts(db, "offsite.failed")
    assert row.severity == "warn" and row.cleared_at is None
    assert row.body.startswith("Chưa thấy ổ USB/NAS")
    await _evaluate(redis, org)
    assert len(await _bells(db, "offsite.failed")) == 1
    # chưa cũ ⇒ không offsite.stale
    assert await _alerts(db, "offsite.stale") == []

    # lần thử khác lỗi khác (GH-EB03) ⇒ fingerprint mới ⇒ chuông mới, thân theo mã
    _status(link, state="failed", error_code="GH-EB03", last_attempt_at=_iso(now),
            last_success_at=_iso(now - timedelta(days=2)))
    await _evaluate(redis, org)
    [row] = await _alerts(db, "offsite.failed")
    assert row.body.startswith("Bản sao vừa tạo không đọc lại được — chưa có bản sao ngoài máy")
    assert len(await _bells(db, "offsite.failed")) == 2

    # thành công sau đó ⇒ đóng
    _status(link, state="ok", last_attempt_at=_iso(now + timedelta(minutes=5)),
            last_success_at=_iso(now + timedelta(minutes=5)))
    await _evaluate(redis, org, now + timedelta(minutes=6))
    [row] = await _alerts(db, "offsite.failed")
    assert row.cleared_at is not None


async def test_unknown_error_code_uses_fixed_body(owner_api: Api, link: Path, db, redis) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    now = datetime.now(UTC)
    _status(link, state="failed", error_code="Chữ lạ <b>", last_attempt_at=_iso(now),
            last_success_at=_iso(now - timedelta(days=1)))
    await _evaluate(redis, org)
    [row] = await _alerts(db, "offsite.failed")
    assert row.body == health.OFFSITE_FAILED_GENERIC
    assert "Chữ lạ" not in row.title + row.body


async def test_not_configured_new_org_no_alert_old_org_warns(owner_api: Api, link: Path, db, redis) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    await _evaluate(redis, org)  # không có offsite-status.json, tổ chức vừa tạo
    assert await _alerts(db, "offsite.stale") == []
    assert await _bells(db, "offsite.stale") == []
    body = (await owner_api.get("/system/health")).json()
    assert body["offsite"]["stale"] is False and body["offsite"]["configured"] is False

    await _age_org(db, org, 8)
    await _evaluate(redis, org)
    [row] = await _alerts(db, "offsite.stale")
    assert row.title == "Chưa có bản sao ngoài máy" and row.severity == "warn"
    assert row.body == ("Hỏng ổ đĩa là mất hết dữ liệu. Cắm ổ USB hoặc chọn thư mục NAS rồi bấm "
                        "'Chọn nơi lưu bản sao ngoài máy'")
    await _evaluate(redis, org)
    assert len(await _bells(db, "offsite.stale")) == 1


async def test_system_health_offsite_block_and_actions(owner_api: Api, link: Path, db, redis) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    now = datetime.now(UTC)
    _status(link, state="not_mounted", error_code="GH-EB01", last_attempt_at=_iso(now),
            last_success_at=_iso(now - timedelta(days=10)))
    await _evaluate(redis, org)
    body = (await owner_api.get("/system/health")).json()
    assert body["offsite"] == {"state": "not_mounted", "configured": True,
                               "last_success_at": _iso(now - timedelta(days=10)), "age_days": 10, "stale": True,
                               "error_code": "GH-EB01", "schedule": "systemd"}
    assert body["overall"] in ("warn", "bad")
    actions = {i["kind"]: i["action"] for i in body["issues"]}
    assert actions["offsite.stale"] == health.ACTIONS["offsite.stale"] == "Chọn nơi lưu / sao lưu ngay"
    # v0.1.69 (F104): ổ chưa cắm + bản sao đã cũ là MỘT nguyên nhân ⇒ chỉ một sự cố (offsite.stale).
    assert "offsite.failed" not in actions
    assert health.ACTIONS["offsite.failed"] == "Xem bản sao ngoài máy"
    assert health.ACTIONS["job.timeout"] == "Xem sức khoẻ"


async def test_manager_sees_neutral_offsite_action(client, owner_api: Api, link: Path, db, redis) -> None:  # type: ignore[no-untyped-def]
    """Dải "Cần Sếp xử lý" của Manager không hứa nút "Chọn nơi lưu" (chỉ Owner có)."""
    from tests.test_rbac_api import login_as

    org = await org_id(db)
    now = datetime.now(UTC)
    _status(link, last_attempt_at=_iso(now - timedelta(days=10)), last_success_at=_iso(now - timedelta(days=10)))
    await _evaluate(redis, org)
    await db.execute(text("""INSERT INTO core.role_permissions (role_id, permission_code, scope)
                             SELECT id, p, 'all' FROM core.roles, unnest(ARRAY['system.read', 'system.manage']) p
                             WHERE code = 'manager'
                             ON CONFLICT (role_id, permission_code) DO UPDATE SET scope = 'all'"""))
    await db.commit()
    mgr = await login_as(client, db, "manager")
    actions = {i["kind"]: i["action"] for i in (await mgr.get("/system/health")).json()["issues"]}
    assert actions["offsite.stale"] == "Xem bản sao ngoài máy"
    owner_actions = {i["kind"]: i["action"] for i in (await owner_api.get("/system/health")).json()["issues"]}
    assert owner_actions["offsite.stale"] == "Chọn nơi lưu / sao lưu ngay"


async def test_stale_offsite_only_warns_overall(owner_api: Api, link: Path, db, redis) -> None:  # type: ignore[no-untyped-def]
    """Không có sự cố nào khác: bản sao ngoài máy cũ chỉ kéo 'overall' xuống 'warn', không 'bad'."""
    now = datetime.now(UTC)
    _status(link, last_attempt_at=_iso(now - timedelta(days=40)), last_success_at=_iso(now - timedelta(days=40)))
    await redis.set(health.HEARTBEAT_KEY, _iso(now))
    body = (await owner_api.get("/system/health")).json()
    assert body["offsite"]["stale"] is True
    assert body["overall"] == "warn"


async def test_no_host_link_keeps_old_shape(owner_api: Api, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                            db, redis) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("GH_HOST_LINK_DIR", str(tmp_path / "khong-co"))
    get_settings.cache_clear()
    org = await org_id(db)
    await _age_org(db, org, 40)
    await _evaluate(redis, org)
    body = (await owner_api.get("/system/health")).json()
    get_settings.cache_clear()
    assert "offsite" not in body
    assert await _alerts(db, "offsite.stale") == []


async def test_weekly_run_in_progress_is_not_stale(owner_api: Api, link: Path, db, redis) -> None:  # type: ignore[no-untyped-def]
    """Lịch tuần: lần thành công trước cách 7 ngày + 40 phút, lượt tuần này đang chạy (timer trễ ngẫu nhiên tới 30 phút,
    xuất+kiểm mất vài phút) ⇒ chưa được coi là cũ — không chuông giả mỗi tuần."""
    org = await org_id(db)
    await _age_org(db, org, 40)
    now = datetime.now(UTC)
    last = now - timedelta(days=7, minutes=40)
    _status(link, state="running", last_attempt_at=_iso(now - timedelta(minutes=5)), last_success_at=_iso(last))
    await _evaluate(redis, org, now)
    assert await _alerts(db, "offsite.stale") == []
    assert await _bells(db, "offsite.stale") == []
    body = (await owner_api.get("/system/health")).json()
    assert body["offsite"]["stale"] is False
    assert (await owner_api.get("/system/offsite")).json()["stale"] is False
    # quá ân hạn (7 ngày 12 giờ) mới là cũ
    await _evaluate(redis, org, last + health.OFFSITE_STALE_AFTER + timedelta(minutes=1))
    [row] = await _alerts(db, "offsite.stale")
    assert row.cleared_at is None


async def test_manager_body_does_not_promise_owner_buttons(client, owner_api: Api, link: Path, db, redis) -> None:  # type: ignore[no-untyped-def]
    """Thân sự cố của Manager không bảo bấm "Chọn nơi lưu…" (nút chỉ Owner có) — nhờ Owner; Owner giữ thân gốc."""
    from tests.test_rbac_api import login_as

    org = await org_id(db)
    await _age_org(db, org, 8)
    now = datetime.now(UTC)
    _status(link, configured=False, dest="", state="failed", error_code="GH-EB07", last_attempt_at=_iso(now))
    await _evaluate(redis, org, now)
    await db.execute(text("""INSERT INTO core.role_permissions (role_id, permission_code, scope)
                             SELECT id, p, 'all' FROM core.roles, unnest(ARRAY['system.read', 'system.manage']) p
                             WHERE code = 'manager'
                             ON CONFLICT (role_id, permission_code) DO UPDATE SET scope = 'all'"""))
    await db.commit()
    mgr = await login_as(client, db, "manager")
    m_bodies = {i["kind"]: i["body"] for i in (await mgr.get("/system/health")).json()["issues"]}
    o_bodies = {i["kind"]: i["body"] for i in (await owner_api.get("/system/health")).json()["issues"]}
    assert m_bodies["offsite.stale"] == health.NON_OWNER_BODIES[("offsite.stale", "not_configured")]
    assert m_bodies["offsite.failed"] == health.NON_OWNER_BODIES[("offsite.failed", "GH-EB07")]
    for b in (m_bodies["offsite.stale"], m_bodies["offsite.failed"]):
        assert "bấm 'Chọn nơi lưu" not in b and "nhờ Owner" in b.replace("Nhờ Owner", "nhờ Owner")
    assert "'Chọn nơi lưu bản sao ngoài máy'" in o_bodies["offsite.stale"]
    assert o_bodies["offsite.failed"] == health.OFFSITE_FAILED_BODY["GH-EB07"]


async def test_failed_request_undeletable_body_says_fix_permissions(owner_api: Api, link: Path, db, redis) -> None:  # type: ignore[no-untyped-def]
    """v0.1.53 (F-97): offsite.failed vì không xoá được tệp yêu cầu (GH-E94C) ⇒ thân nói kiểm quyền run/request (cùng
    câu với GET /system/offsite), không phải câu chung "bấm để xem chi tiết và thử lại"."""
    org = await org_id(db)
    _status(link, state="failed", error_code="GH-E94C", last_attempt_at=_iso(datetime.now(UTC)))
    await _evaluate(redis, org)
    [row] = await _alerts(db, "offsite.failed")
    assert row.body == health.OFFSITE_FAILED_BODY["GH-E94C"]
    assert "run/request" in row.body and row.body != health.OFFSITE_FAILED_GENERIC
    from gh.system_api import offsite as offsite_api

    assert row.body == offsite_api.ERROR_MESSAGES["GH-E94C"]


def test_viewer_body_keeps_run_button_text_for_manager() -> None:
    """Manager CÓ nút "Sao lưu ra ổ ngoài ngay" ⇒ thân offsite cũ/EB01 giữ nguyên."""
    body = "Cắm ổ USB/NAS rồi bấm 'Sao lưu ra ổ ngoài ngay' để có bản sao mới ngoài máy chủ"
    assert health._viewer_body("offsite.stale", "warn", body, False) == body
    assert health._viewer_body("offsite.failed", "2026-01-01T00:00:00Z|GH-EB01", "x", False) == "x"
    assert health._viewer_body("offsite.failed", "2026-01-01T00:00:00Z|GH-EB00", "x", True) == "x"
    assert health._viewer_body("offsite.failed", "t|GH-EB00", "x", False).startswith("Chưa chọn nơi lưu — nhờ Owner")
