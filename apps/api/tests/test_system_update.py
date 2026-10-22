"""Nút "Cập nhật ngay" trong Console: api chỉ để lại yêu cầu trong hộp thư chung với genh trên máy chủ
(apps/genh/internal/hostlink) và đọc lại tiến trình — không tự nâng cấp chính nó."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from gh.config import get_settings
from gh.system_api import update as upd
from tests.conftest import Api
from tests.test_rbac_api import login_as

# Bản thật — fixture `link` thay fetch_latest bằng bản giả; test cần đi qua bản thật thì đặt lại cái này.
_REAL_FETCH_LATEST = upd.fetch_latest


def test_is_newer() -> None:
    assert upd.is_newer("v0.1.17", "v0.1.16")
    assert upd.is_newer("v0.2.0", "v0.1.99")
    assert not upd.is_newer("v0.1.16", "v0.1.16")
    assert not upd.is_newer("v0.1.15", "v0.1.16")
    assert not upd.is_newer("v0.1.17", "dev") and not upd.is_newer(None, "v0.1.16")


def _ago(**kw: float) -> str:
    return (datetime.now(UTC) - timedelta(**kw)).isoformat().replace("+00:00", "Z")


def _heartbeat(link: Path, *, pid: int | None = None, minutes_ago: float = 0.5, boot_id: str = "") -> None:
    hb: dict[str, object] = {"op": "update", "boot_id": boot_id, "started_at": _ago(hours=2),
                             "at": _ago(minutes=minutes_ago)}
    if pid is not None:
        hb["pid"] = pid
    (link / "genh-heartbeat.json").write_text(json.dumps(hb))


@pytest.fixture
def link(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, redis) -> Path:  # type: ignore[no-untyped-def]
    d = tmp_path / "run"
    (d / "request").mkdir(parents=True)
    (d / "genh.json").write_text(json.dumps({"version": "v0.1.16", "updater": "systemd"}))
    monkeypatch.setenv("GH_HOST_LINK_DIR", str(d))
    get_settings.cache_clear()

    async def fake_latest(repo: str) -> dict[str, str]:
        return {"tag": "v0.1.17", "url": "https://example/v0.1.17", "published_at": "2026-09-28T00:00:00Z",
                "notes": "Nút cập nhật"}

    monkeypatch.setattr(upd, "fetch_latest", fake_latest)
    yield d
    get_settings.cache_clear()


async def test_update_request_lifecycle(owner_api: Api, link: Path, redis) -> None:  # type: ignore[no-untyped-def]
    await redis.delete(upd.LATEST_CACHE_KEY)
    api = owner_api
    r = (await api.get("/system/update")).json()
    assert r["current"] == "v0.1.16" and r["latest"] == "v0.1.17" and r["update_available"]
    assert r["can_request"] and r["state"] == "idle" and r["release_notes"] == "Nút cập nhật"

    r = await api.send("POST", "/system/update")
    assert r.status_code == 202, r.text
    assert r.json()["state"] == "requested"
    req = json.loads((link / "request" / "update.json").read_text())
    assert req["requested_at"] and req["id"]
    # Bấm hai lần không tạo hai lần cập nhật.
    r = await api.send("POST", "/system/update")
    assert r.status_code == 409 and r.json()["code"] == "UPDATE_IN_PROGRESS"

    # genh trên máy chủ nhận yêu cầu (xoá tệp) và báo tiến trình.
    (link / "request" / "update.json").unlink()
    (link / "update-status.json").write_text(json.dumps({"state": "running", "from": "v0.1.16",
                                                         "started_at": "2026-09-28T09:00:00Z"}))
    # v0.1.37: 'running' quá 60 phút chỉ còn 'running' khi genh còn nhịp sống (tươi) — ghi nhịp như genh thật.
    _heartbeat(link)
    assert (await api.get("/system/update")).json()["state"] == "running"
    assert (await api.send("POST", "/system/update")).status_code == 409
    (link / "update-status.json").write_text(json.dumps({"state": "done", "from": "v0.1.16", "to": "v0.1.17"}))
    (link / "genh.json").write_text(json.dumps({"version": "v0.1.17", "updater": "systemd"}))
    r = (await api.get("/system/update")).json()
    assert r["state"] == "done" and r["current"] == "v0.1.17" and not r["update_available"]


async def test_stale_request_can_be_retried_and_no_watcher_refuses(owner_api: Api, link: Path, redis) -> None:  # type: ignore[no-untyped-def]
    old = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    (link / "request" / "update.json").write_text(json.dumps({"requested_at": old}))
    assert (await owner_api.get("/system/update")).json()["state"] == "stalled"
    assert (await owner_api.send("POST", "/system/update")).status_code == 202

    (link / "request" / "update.json").unlink()
    (link / "genh.json").write_text(json.dumps({"version": "v0.1.16", "updater": ""}))
    r = await owner_api.send("POST", "/system/update")
    assert r.status_code == 409 and r.json()["code"] == "UPDATER_UNAVAILABLE"
    assert not (link / "request" / "update.json").exists()


async def test_without_host_link_nothing_is_offered(owner_api: Api, tmp_path: Path,
                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GH_HOST_LINK_DIR", str(tmp_path / "missing"))
    get_settings.cache_clear()
    try:
        r = (await owner_api.get("/system/update")).json()
        assert not r["linked"] and not r["can_request"] and not r["update_available"] and r["latest"] is None
        assert (await owner_api.send("POST", "/system/update")).status_code == 409
    finally:
        get_settings.cache_clear()


async def test_only_system_managers_can_update(owner_api: Api, client, db, link: Path) -> None:  # type: ignore[no-untyped-def]
    auditor = await login_as(client, db, "auditor")
    assert (await auditor.get("/system/update")).status_code == 403
    assert (await auditor.send("POST", "/system/update")).status_code == 403
    assert not (link / "request" / "update.json").exists()


# v0.1.33: mốc "thành bản chính thức" cho Console — dấu promote trong ghi chú Release (job promote của
# e2e-install.yml, cùng định dạng selfupdate.PromotedMarker của genh) muộn hơn published_at (lúc tạo bản thử).
MARK = "<!-- genh:promoted_at=2026-09-30T08:00:00Z -->"


def test_official_since_prefers_later_promotion_marker() -> None:
    # Bản thử tạo 25/09, promote 30/09 ⇒ lịch đêm đếm 24 giờ từ 30/09.
    assert upd.official_since("2026-09-25T00:00:00Z", f"## Điểm mới\n- a\n\n{MARK}\n") == "2026-09-30T08:00:00Z"
    # Không có dấu (bản trước cổng phát hành) ⇒ published_at.
    assert upd.official_since("2026-09-25T00:00:00Z", "## Điểm mới") == "2026-09-25T00:00:00Z"
    # Dấu lỡ sớm hơn published_at không làm mốc sớm đi; nhiều dấu lấy muộn nhất.
    assert upd.official_since("2026-10-01T00:00:00Z", MARK) == "2026-10-01T00:00:00Z"
    two = MARK + "\n<!-- genh:promoted_at=2026-09-30T09:30:00Z -->"
    assert upd.official_since(None, two) == "2026-09-30T09:30:00Z"
    # Dấu sai định dạng bị bỏ qua; không đọc được gì ⇒ None.
    assert upd.official_since(None, "<!-- genh:promoted_at=hom-qua -->") is None
    assert upd.official_since("rác", None) is None


def test_strip_markers_hides_promotion_comment() -> None:
    assert upd.strip_markers(f"## Điểm mới\n- a\n\n{MARK}\n") == "## Điểm mới\n- a"


class _GitHubResp:
    status_code = 200

    @staticmethod
    def json() -> dict[str, object]:
        return {"tag_name": "v0.1.17", "html_url": "https://example/v0.1.17",
                "published_at": "2026-09-25T00:00:00Z", "body": f"- Nút cập nhật\n\n{MARK}\n"}


class _GitHubListResp(_GitHubResp):
    @staticmethod
    def json() -> list[dict[str, object]]:  # type: ignore[override]
        return [_GitHubResp.json()]


class _GitHubClient:
    """httpx.AsyncClient giả — trả release có dấu promote, không gọi mạng."""

    def __init__(self, **_: object) -> None:
        pass

    async def __aenter__(self) -> "_GitHubClient":
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    async def get(self, url: str) -> _GitHubResp:
        # v0.1.53 (F-96): Console hỏi danh sách `/releases?per_page=10` trước, rơi về `/releases/latest` khi lỗi.
        assert url.endswith(("/releases?per_page=10", "/releases/latest"))
        if url.endswith("/releases?per_page=10"):
            return _GitHubListResp()
        return _GitHubResp()


async def test_payload_exposes_official_time_and_hides_marker(owner_api: Api, link: Path, redis,  # type: ignore[no-untyped-def]
                                                              monkeypatch: pytest.MonkeyPatch) -> None:
    await redis.delete(upd.LATEST_CACHE_KEY)
    monkeypatch.setattr(upd, "fetch_latest", _REAL_FETCH_LATEST)
    monkeypatch.setattr(upd.httpx, "AsyncClient", _GitHubClient)
    r = (await owner_api.get("/system/update")).json()
    assert r["latest"] == "v0.1.17" and r["update_available"]
    # Console đếm 24 giờ của lịch đêm từ lúc promote, không phải lúc tạo bản thử.
    assert r["published_at"] == "2026-09-30T08:00:00Z"
    assert r["release_notes"] == "- Nút cập nhật"


async def test_auto_update_enabled_from_genh_json(owner_api: Api, link: Path, redis) -> None:  # type: ignore[no-untyped-def]
    """v0.1.33: `auto_update_enabled` đọc từ genh.json — genh cũ chưa ghi / giá trị lạ ⇒ null."""
    await redis.delete(upd.LATEST_CACHE_KEY)
    assert (await owner_api.get("/system/update")).json()["auto_update_enabled"] is None
    for raw, want in ((True, True), (False, False), ("true", None), (1, None)):
        (link / "genh.json").write_text(json.dumps({"version": "v0.1.16", "updater": "systemd",
                                                    "auto_update_enabled": raw}))
        assert (await owner_api.get("/system/update")).json()["auto_update_enabled"] is want, raw


async def test_blocked_version_from_update_blocked_json(owner_api: Api, link: Path, redis) -> None:  # type: ignore[no-untyped-def]
    """v0.1.34: `blocked_version` đọc từ run/update-blocked.json (genh ghi khi bản mới lỗi + đã quay về bản cũ) —
    không có tệp / tệp hỏng / giá trị lạ ⇒ null."""
    await redis.delete(upd.LATEST_CACHE_KEY)
    assert (await owner_api.get("/system/update")).json()["blocked_version"] is None
    (link / "update-blocked.json").write_text(json.dumps({"version": "v0.1.17", "blocked_at": "2026-09-30T03:00:00Z",
                                                          "code": "GH-E944", "rollback_failed": True}))
    body = (await owner_api.get("/system/update")).json()
    assert body["blocked_version"] == "v0.1.17"
    assert body["blocked_rollback_failed"] is True
    (link / "update-blocked.json").write_text(json.dumps({"version": "v0.1.17", "blocked_at": "2026-09-30T03:00:00Z"}))
    assert (await owner_api.get("/system/update")).json()["blocked_rollback_failed"] is False
    for raw in ("{hỏng", json.dumps({"version": 17}), json.dumps({"version": ""}), json.dumps(["v0.1.17"])):
        (link / "update-blocked.json").write_text(raw)
        body = (await owner_api.get("/system/update")).json()
        assert body["blocked_version"] is None, raw
        assert body["blocked_rollback_failed"] is None, raw


# ─── v0.1.37 (F-34): 'running' mà tiến trình genh đã chết ⇒ 'stalled' (process_gone) ─────────────────────────────

def _running(link: Path, *, minutes_ago: float, pid: int | None = 4242, boot_id: str | None = None) -> None:
    st: dict[str, object] = {"state": "running", "from": "v0.1.16", "to": "v0.1.17",
                             "started_at": _ago(minutes=minutes_ago)}
    if pid is not None:
        st["pid"] = pid
    if boot_id is not None:
        st["boot_id"] = boot_id
    (link / "update-status.json").write_text(json.dumps(st))


async def _get(api: Api) -> dict[str, object]:
    r = await api.get("/system/update")
    assert r.status_code == 200, r.text
    return r.json()  # type: ignore[no-any-return]


async def test_running_without_heartbeat_after_an_hour_is_stalled(owner_api: Api, link: Path, redis) -> None:  # type: ignore[no-untyped-def]
    await redis.delete(upd.LATEST_CACHE_KEY)
    # (a) chạy 61 phút, pid 4242, không có nhịp sống ⇒ dừng giữa chừng, cho bấm Thử lại
    _running(link, minutes_ago=61)
    body = await _get(owner_api)
    assert body["state"] == "stalled" and body["stalled_reason"] == "process_gone"
    r = await owner_api.send("POST", "/system/update")
    assert r.status_code == 202, r.text
    assert r.json()["state"] == "requested" and r.json()["stalled_reason"] is None


async def test_running_heartbeat_rules(owner_api: Api, link: Path, redis) -> None:  # type: ignore[no-untyped-def]
    await redis.delete(upd.LATEST_CACHE_KEY)
    # (b) 61 phút + nhịp tươi cùng pid ⇒ vẫn 'running'
    _running(link, minutes_ago=61)
    _heartbeat(link, pid=4242)
    body = await _get(owner_api)
    assert body["state"] == "running" and body["stalled_reason"] is None
    assert (await owner_api.send("POST", "/system/update")).status_code == 409
    # (c) nhịp cũ 10 phút ⇒ chết
    _heartbeat(link, pid=4242, minutes_ago=10)
    assert (await _get(owner_api))["stalled_reason"] == "process_gone"
    # (f) nhịp tươi nhưng pid khác ⇒ của tiến trình khác, coi như chết sau 60 phút
    _heartbeat(link, pid=9999)
    assert (await _get(owner_api))["state"] == "stalled"
    # (d) mới chạy 10 phút, không nhịp ⇒ còn 'running'
    (link / "genh-heartbeat.json").unlink()
    _running(link, minutes_ago=10)
    assert (await _get(owner_api))["state"] == "running"
    # genh cũ (không pid, không nhịp) quá 60 phút ⇒ stalled (chấp nhận); có nhịp tươi (không pid) ⇒ running
    _running(link, minutes_ago=61, pid=None)
    assert (await _get(owner_api))["state"] == "stalled"
    _heartbeat(link, pid=1)
    assert (await _get(owner_api))["state"] == "running"


async def test_running_after_reboot_is_stalled_at_once(owner_api: Api, link: Path, redis,  # type: ignore[no-untyped-def]
                                                       monkeypatch: pytest.MonkeyPatch) -> None:
    await redis.delete(upd.LATEST_CACHE_KEY)
    # (e) nhịp sống cũ + boot_id lúc bắt đầu khác boot_id hiện tại ⇒ máy đã khởi động lại, dừng ngay dù mới 6 phút
    monkeypatch.setattr(upd, "_boot_id", lambda: "boot-b")
    _running(link, minutes_ago=6, boot_id="boot-a")
    _heartbeat(link, pid=4242, minutes_ago=6, boot_id="boot-a")
    body = await _get(owner_api)
    assert body["state"] == "stalled" and body["stalled_reason"] == "process_gone"
    # không có nhịp sống cũng vậy
    (link / "genh-heartbeat.json").unlink()
    assert (await _get(owner_api))["stalled_reason"] == "process_gone"
    # cùng boot_id ⇒ còn chạy; không đọc được boot_id ⇒ chỉ xét nhịp sống/60 phút
    _running(link, minutes_ago=6, boot_id="boot-b")
    assert (await _get(owner_api))["state"] == "running"
    monkeypatch.setattr(upd, "_boot_id", lambda: None)
    _running(link, minutes_ago=6, boot_id="boot-a")
    assert (await _get(owner_api))["state"] == "running"
    # nhịp sống (do genh ghi) thuộc lần khởi động khác với lần chạy cập nhật ⇒ genh mới sau khởi động lại, pid khác
    _heartbeat(link, pid=7777, boot_id="boot-c")
    assert (await _get(owner_api))["stalled_reason"] == "process_gone"
    # started_at hỏng ⇒ chỉ xét boot_id
    (link / "genh-heartbeat.json").unlink()
    (link / "update-status.json").write_text(json.dumps({"state": "running", "started_at": "hôm qua", "pid": 1}))
    assert (await _get(owner_api))["state"] == "running"


async def test_docker_desktop_vm_boot_id_differs_but_heartbeat_alive(  # type: ignore[no-untyped-def]
        owner_api: Api, link: Path, redis, monkeypatch: pytest.MonkeyPatch) -> None:
    """Docker Desktop for Linux: container chạy trong VM — boot_id container khác CẢ boot_id genh ghi. Nhịp sống tươi
    đúng pid ⇒ vẫn 'running' (không báo "bị dừng giữa chừng"); yêu cầu xếp hàng sau lịch đêm ⇒ host_busy."""
    await redis.delete(upd.LATEST_CACHE_KEY)
    monkeypatch.setattr(upd, "_boot_id", lambda: "vm-boot")
    _running(link, minutes_ago=3, boot_id="host-boot")
    _heartbeat(link, pid=4242, boot_id="host-boot")
    body = await _get(owner_api)
    assert body["state"] == "running" and body["stalled_reason"] is None
    (link / "update-status.json").unlink()
    (link / "request" / "update.json").write_text(json.dumps({"requested_at": _ago(minutes=25)}))
    body = await _get(owner_api)
    assert body["state"] == "requested" and body["host_busy"] is True and body["stalled_reason"] is None


async def test_stale_request_reason_not_picked_up(owner_api: Api, link: Path, redis) -> None:  # type: ignore[no-untyped-def]
    await redis.delete(upd.LATEST_CACHE_KEY)
    # (g) yêu cầu nằm quá 15 phút ⇒ watcher không chạy
    (link / "request" / "update.json").write_text(json.dumps({"requested_at": _ago(minutes=16)}))
    body = await _get(owner_api)
    assert body["state"] == "stalled" and body["stalled_reason"] == "not_picked_up"
    (link / "request" / "update.json").write_text(json.dumps({"requested_at": _ago(minutes=1)}))
    body = await _get(owner_api)
    assert body["state"] == "requested" and body["stalled_reason"] is None


async def test_stale_request_while_other_genh_runs_is_queued(owner_api: Api, link: Path, redis) -> None:  # type: ignore[no-untyped-def]
    await redis.delete(upd.LATEST_CACHE_KEY)
    # Yêu cầu nằm quá 15 phút NHƯNG một lần genh khác (lịch đêm) đang giữ khoá và còn nhịp sống ⇒ đang xếp hàng
    # (genh --if-requested chờ khoá), không phải "Máy chủ chưa nhận yêu cầu".
    (link / "request" / "update.json").write_text(json.dumps({"requested_at": _ago(minutes=25)}))
    _heartbeat(link, pid=4242, minutes_ago=0.5)
    body = await _get(owner_api)
    assert body["state"] == "requested" and body["stalled_reason"] is None and body["host_busy"] is True
    # Nhịp sống cũ (genh kia đã chết) ⇒ lại là not_picked_up.
    _heartbeat(link, pid=4242, minutes_ago=10)
    body = await _get(owner_api)
    assert body["state"] == "stalled" and body["stalled_reason"] == "not_picked_up" and body["host_busy"] is False


async def test_garbage_heartbeat_and_status_never_500(owner_api: Api, link: Path, redis) -> None:  # type: ignore[no-untyped-def]
    await redis.delete(upd.LATEST_CACHE_KEY)
    # (h) nhịp sống là JSON rác / không phải dict / kiểu sai ⇒ không 500, coi như không có nhịp
    _running(link, minutes_ago=61)
    for raw in ("{hỏng", "[1, 2]", "42", json.dumps({"pid": "4242", "at": 123}),
                json.dumps({"pid": True, "at": _ago(minutes=1)}),
                json.dumps({"pid": 4242, "at": "2026-10-01T00:00:00"})):
        (link / "genh-heartbeat.json").write_text(raw)
        body = await _get(owner_api)
        assert body["state"] == "stalled" and body["stalled_reason"] == "process_gone", raw
    # update-status.json kiểu lạ (pid chuỗi, boot_id số, started_at không múi giờ) ⇒ không 500
    (link / "update-status.json").write_text(json.dumps({"state": "running", "pid": "x", "boot_id": 7,
                                                         "started_at": "2026-09-28T09:00:00"}))
    assert (await _get(owner_api))["state"] == "running"
    (link / "request" / "update.json").write_text(json.dumps({"requested_at": "2026-09-28T09:00:00"}))
    assert (await _get(owner_api))["state"] == "requested"
