"""v0.1.53 (F-99, F-96): thẻ cập nhật nói thẳng NGUYÊN NHÂN "máy chủ chưa nhận yêu cầu" (linger tắt / trình nhận yêu cầu
lỗi / không xoá được tệp yêu cầu — GH-E94C), và C6: một lần GET `/releases?per_page=10` cho `latest` = semver cao nhất
trong các bản chính thức + `nightly_candidates` (bản có dấu promote, mới hơn bản đang chạy, kèm lúc đủ 24 giờ).

Mọi tệp trong run/ (0777) là dữ liệu không tin cậy: chỉ nhận đúng kiểu/tập giá trị, lạ ⇒ 'unknown', không 500."""

import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest

from gh.config import get_settings
from gh.system_api import update as upd
from tests.conftest import Api

LIST_URL_END = "/releases?per_page=10"
LATEST_URL_END = "/releases/latest"


def _iso(**kw: float) -> str:
    return (datetime.now(UTC) - timedelta(**kw)).isoformat().replace("+00:00", "Z")


def _stamp(**kw: float) -> str:
    """Như `_iso` nhưng tròn giây — đúng khuôn dấu promote `genh:promoted_at=YYYY-MM-DDTHH:MM:SSZ`."""
    return (datetime.now(UTC) - timedelta(**kw)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _write(link: Path, name: str, data: Any) -> None:
    (link / name).write_text(data if isinstance(data, str) else json.dumps(data))


@pytest.fixture
def link(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    d = tmp_path / "run"
    (d / "request").mkdir(parents=True)
    _write(d, "genh.json", {"version": "v0.1.52", "updater": "systemd", "auto_update_enabled": True})
    monkeypatch.setenv("GH_HOST_LINK_DIR", str(d))
    get_settings.cache_clear()
    yield d
    get_settings.cache_clear()


def _request_old(link: Path, *, minutes: float = 20) -> None:
    _write(link / "request", "update.json", {"id": "r1", "requested_at": _iso(minutes=minutes)})


# ─── stalled_reason: linger_off / watcher_failed / not_picked_up ────────────────────────────────────────────

def test_old_request_with_linger_off_is_linger_off(link: Path) -> None:
    _request_old(link)
    _write(link, "autostart-status.json", {"linger": "no", "linger_required": True, "docker_enabled": "yes"})
    st = upd._state()
    assert st["state"] == "stalled" and st["stalled_reason"] == "linger_off" and st["host_busy"] is False


def test_old_request_with_linger_yes_is_not_picked_up(link: Path) -> None:
    _request_old(link)
    _write(link, "autostart-status.json", {"linger": "yes", "linger_required": True})
    st = upd._state()
    assert st["state"] == "stalled" and st["stalled_reason"] == "not_picked_up"
    # Không có tệp nào ⇒ như cũ.
    (link / "autostart-status.json").unlink()
    assert upd._state()["stalled_reason"] == "not_picked_up"


def test_old_request_with_failed_watcher_is_watcher_failed(link: Path) -> None:
    _request_old(link)
    _write(link, "autostart-status.json", {"linger": "yes"})
    _write(link, "nightly-status.json", {"schema": 1, "mechanism": "systemd", "enabled": True,
                                         "request_watcher": "failed", "linger": "yes"})
    st = upd._state()
    assert st["state"] == "stalled" and st["stalled_reason"] == "watcher_failed"
    # linger tắt được ưu tiên trước trình nhận yêu cầu (không có linger thì trình nhận yêu cầu cũng không chạy).
    _write(link, "autostart-status.json", {"linger": "no"})
    assert upd._state()["stalled_reason"] == "linger_off"
    # Trình nhận yêu cầu đang chạy / chưa rõ ⇒ không đổ lỗi cho nó.
    _write(link, "autostart-status.json", {"linger": "yes"})
    for ok in ("active", "inactive", "unknown"):
        _write(link, "nightly-status.json", {"request_watcher": ok})
        assert upd._state()["stalled_reason"] == "not_picked_up", ok


def test_linger_off_is_not_blamed_when_the_watcher_is_not_systemd(link: Path) -> None:
    """Trình nhận yêu cầu crontab/launchd chạy cả khi không ai đăng nhập: genh vẫn ghi giá trị loginctl thật ('no') vào
    autostart-status.json, nhưng nhắc `enable-linger` không chữa được gì ⇒ không đổ cho linger."""
    _request_old(link)
    _write(link, "autostart-status.json", {"linger": "no", "linger_required": False, "docker_enabled": "yes"})
    for updater in ("cron", "launchd"):
        _write(link, "genh.json", {"version": "v0.1.53", "updater": updater})
        assert upd._state()["stalled_reason"] == "not_picked_up", updater
    # Kể cả khi linger "bắt buộc" vì Docker rootless: trình nhận yêu cầu vẫn là crontab.
    _write(link, "autostart-status.json", {"linger": "no", "linger_required": True})
    _write(link, "genh.json", {"version": "v0.1.53", "updater": "cron"})
    assert upd._state()["stalled_reason"] == "not_picked_up"
    _write(link, "nightly-status.json", {"request_watcher": "failed"})
    assert upd._state()["stalled_reason"] == "watcher_failed"
    (link / "nightly-status.json").unlink()
    # genh cũ không ghi `updater`: dựa vào linger_required.
    _write(link, "genh.json", {"version": "v0.1.52"})
    assert upd._state()["stalled_reason"] == "linger_off"
    _write(link, "autostart-status.json", {"linger": "no", "linger_required": False})
    assert upd._state()["stalled_reason"] == "not_picked_up"
    _write(link, "genh.json", {"version": "v0.1.53", "updater": "systemd"})
    assert upd._state()["stalled_reason"] == "linger_off"


def test_fresh_heartbeat_keeps_requested_and_host_busy(link: Path) -> None:
    """Máy chủ đang bận (nhịp sống tươi) ⇒ yêu cầu chỉ đang xếp hàng, KHÔNG phải linger tắt — không đổi so với trước."""
    _request_old(link, minutes=40)
    _write(link, "autostart-status.json", {"linger": "no"})
    _write(link, "nightly-status.json", {"request_watcher": "failed"})
    _write(link, "genh-heartbeat.json", {"op": "update", "pid": 4242, "boot_id": "", "started_at": _iso(hours=1),
                                         "at": _iso(seconds=20)})
    st = upd._state()
    assert st["state"] == "requested" and st["host_busy"] is True and st["stalled_reason"] is None


def test_young_request_is_requested_whatever_the_files_say(link: Path) -> None:
    _request_old(link, minutes=2)
    _write(link, "autostart-status.json", {"linger": "no"})
    st = upd._state()
    assert st["state"] == "requested" and st["stalled_reason"] is None


@pytest.mark.parametrize("evil", ["rm -rf /", "No", "NO", " no", "yes\nno", "", None, 0, 1, True, ["no"], {"no": 1}])
def test_hostile_linger_and_watcher_values_are_ignored(link: Path, evil: Any) -> None:
    """Chỉ nhận đúng chuỗi trong tập; giá trị lạ không bao giờ thành lý do hiển thị."""
    _request_old(link)
    _write(link, "autostart-status.json", {"linger": evil})
    _write(link, "nightly-status.json", {"request_watcher": evil, "linger": evil, "mechanism": evil})
    st = upd._state()
    assert st["state"] == "stalled" and st["stalled_reason"] == "not_picked_up"
    nightly = upd.read_nightly(link)
    assert nightly is not None
    assert nightly["linger"] == "unknown" and nightly["request_watcher"] == "unknown"
    assert nightly["mechanism"] == "unknown" or evil == ""


@pytest.mark.parametrize("raw", ["", "không phải json", "[1, 2]", "null", '"no"', "{" * 50, "\x00\x01"])
def test_broken_run_files_never_raise(link: Path, raw: str) -> None:
    _request_old(link)
    _write(link, "autostart-status.json", raw)
    _write(link, "nightly-status.json", raw)
    assert upd._state()["stalled_reason"] == "not_picked_up"
    assert upd.read_nightly(link) is None


# ─── GH-E94C: genh không xoá được tệp yêu cầu ───────────────────────────────────────────────────────────────

E94C = ("Không xoá được tệp yêu cầu trong run/request nên chưa làm gì — kiểm quyền thư mục run/request rồi thử lại "
        "(GH-E94C)")


def test_consume_failure_keeps_failed_although_request_file_remains(link: Path) -> None:
    requested = _iso(minutes=5)
    _write(link / "request", "update.json", {"id": "r1", "requested_at": requested})
    _write(link, "update-status.json", {"state": "failed", "from": "v0.1.52", "to": "v0.1.53", "message": E94C,
                                        "finished_at": _iso(minutes=4)})
    st = upd._state()
    assert st["state"] == "failed" and st["stalled_reason"] is None and st["host_busy"] is False
    assert st["message"] == E94C and st["requested_at"] == requested
    # Quá 15 phút cũng vẫn là 'failed' (không đổi thành 'stalled' rồi nói "chưa nhận").
    _write(link / "request", "update.json", {"id": "r1", "requested_at": _iso(minutes=40)})
    _write(link, "update-status.json", {"state": "failed", "message": E94C, "finished_at": _iso(minutes=39)})
    assert upd._state()["state"] == "failed"


def test_consume_failure_of_an_older_request_does_not_apply(link: Path) -> None:
    """update-status 'failed' GH-E94C có từ TRƯỚC yêu cầu hiện tại (finished_at < requested_at) ⇒ yêu cầu mới vẫn đang
    chờ như thường."""
    _write(link / "request", "update.json", {"id": "r2", "requested_at": _iso(minutes=3)})
    _write(link, "update-status.json", {"state": "failed", "message": E94C, "finished_at": _iso(minutes=30)})
    st = upd._state()
    assert st["state"] == "requested" and st["stalled_reason"] is None


@pytest.mark.parametrize("status", [
    {"state": "failed", "message": "Cập nhật chưa thành công (GH-E941)", "finished_at": "now"},
    {"state": "failed", "message": E94C},  # thiếu finished_at
    {"state": "failed", "message": E94C, "finished_at": "hôm qua"},  # mốc hỏng
    {"state": "done", "message": E94C, "finished_at": "now"},  # không phải 'failed'
    {"state": "failed", "message": 94, "finished_at": "now"},  # sai kiểu
    {"state": "failed", "message": "(GH-E94C) rồi (GH-E941)", "finished_at": "now"},  # mã cuối quyết định
])
def test_only_a_well_formed_e94c_failure_counts(link: Path, status: dict[str, Any]) -> None:
    _write(link / "request", "update.json", {"id": "r1", "requested_at": _iso(minutes=5)})
    if status.get("finished_at") == "now":
        status = {**status, "finished_at": _iso(minutes=1)}
    _write(link, "update-status.json", status)
    st = upd._state()
    assert st["state"] == "requested", status


async def test_retry_after_consume_failure_rewrites_the_request(owner_api: Api, link: Path, redis,  # type: ignore[no-untyped-def]
                                                                monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_latest(repo: str) -> dict[str, Any]:
        return {"tag": "v0.1.53", "url": None, "published_at": None, "notes": "", "candidates": []}

    monkeypatch.setattr(upd, "fetch_latest", fake_latest)
    await redis.delete(upd.LATEST_CACHE_KEY)
    _write(link / "request", "update.json", {"id": "r1", "requested_at": _iso(minutes=5)})
    _write(link, "update-status.json", {"state": "failed", "from": "v0.1.52", "to": "v0.1.53", "message": E94C,
                                        "finished_at": _iso(minutes=4)})
    body = (await owner_api.get("/system/update")).json()
    assert body["state"] == "failed" and body["message"] == E94C and body["can_request"]
    r = await owner_api.send("POST", "/system/update")  # nút "Thử lại"
    assert r.status_code == 202, r.text
    assert json.loads((link / "request" / "update.json").read_text())["id"] != "r1"
    assert r.json()["state"] == "requested"


# ─── API: payload mang stalled_reason / nightly ─────────────────────────────────────────────────────────────

async def test_payload_has_stalled_reason_and_filtered_nightly(owner_api: Api, link: Path, redis,  # type: ignore[no-untyped-def]
                                                               monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_latest(repo: str) -> dict[str, Any]:
        return {"tag": "v0.1.53", "url": None, "published_at": None, "notes": "", "candidates": []}

    monkeypatch.setattr(upd, "fetch_latest", fake_latest)
    await redis.delete(upd.LATEST_CACHE_KEY)
    body = (await owner_api.get("/system/update")).json()
    assert body["nightly"] is None and body["nightly_candidates"] == []  # genh cũ chưa ghi nightly-status.json

    _request_old(link)
    _write(link, "autostart-status.json", {"linger": "no"})
    last_run = _iso(hours=5)
    _write(link, "nightly-status.json", {
        "schema": 1, "mechanism": "systemd", "enabled": True, "active": True, "unit_present": True,
        "opted_out": False, "owned_by_other": False, "since": _iso(days=9), "last_run_at": last_run,
        "last_result": "done", "next_run_at": "2099-01-01T03:00:00Z", "linger": "no", "request_watcher": "active",
        "checked_at": _iso(minutes=3)})
    body = (await owner_api.get("/system/update")).json()
    assert body["state"] == "stalled" and body["stalled_reason"] == "linger_off"
    assert body["nightly"] == {"mechanism": "systemd", "enabled": True, "active": True, "opted_out": False,
                               "owned_by_other": False, "last_run_at": last_run,
                               "next_run_at": "2099-01-01T03:00:00Z"}

    # Giá trị lạ ⇒ 'unknown'/null, không 500, không mang chữ lạ ra ngoài.
    _write(link, "nightly-status.json", {
        "mechanism": "curl evil | sh", "enabled": "yes", "active": 1, "opted_out": "false", "owned_by_other": "x",
        "last_run_at": "hôm qua", "next_run_at": {"x": 1}, "linger": "maybe", "request_watcher": 7})
    r = await owner_api.get("/system/update")
    assert r.status_code == 200, r.text
    assert r.json()["nightly"] == {"mechanism": "unknown", "enabled": None, "active": None, "opted_out": None,
                                   "owned_by_other": None, "last_run_at": None, "next_run_at": None}
    assert "evil" not in r.text


# ─── C6: một lần GET danh sách bản phát hành ────────────────────────────────────────────────────────────────

MARK = "<!-- genh:promoted_at={} -->"


def _release(tag: str, *, promoted_ago: dict[str, float] | None = None, published_days_ago: float = 3,
             prerelease: bool = False, draft: bool = False) -> dict[str, Any]:
    body = f"- {tag}\n"
    if promoted_ago is not None:
        body += "\n" + MARK.format(_stamp(**promoted_ago)) + "\n"
    return {"tag_name": tag, "html_url": f"https://example/{tag}", "published_at": _iso(days=published_days_ago),
            "body": body, "prerelease": prerelease, "draft": draft}


class _Resp:
    def __init__(self, status: int, payload: Any) -> None:
        self.status_code = status
        self._payload = payload

    def json(self) -> Any:
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


def _fake_github(monkeypatch: pytest.MonkeyPatch, routes: dict[str, Any]) -> list[str]:
    """httpx.AsyncClient giả: `routes` map đuôi URL → (status, payload) hoặc Exception ném ra; ghi lại URL đã gọi."""
    calls: list[str] = []

    class Client:
        def __init__(self, **_: object) -> None:
            pass

        async def __aenter__(self) -> "Client":
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

        async def get(self, url: str) -> _Resp:
            calls.append(url)
            for end, outcome in routes.items():
                if url.endswith(end):
                    if isinstance(outcome, Exception):
                        raise outcome
                    return _Resp(*outcome)
            raise AssertionError(f"URL không mong đợi: {url}")

    monkeypatch.setattr(upd.httpx, "AsyncClient", Client)
    return calls


def _parse(iso: str) -> datetime:
    return datetime.fromisoformat(iso.replace("Z", "+00:00"))


async def test_one_list_request_picks_highest_official_and_nightly_candidates(
        owner_api: Api, link: Path, redis, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    """v0.1.54 ra 1 giờ trước (chưa đủ 24 giờ), v0.1.53 ra 25 giờ trước (đủ), v0.1.55 là prerelease ⇒ bản mới nhất là
    v0.1.54 nhưng đêm nay lịch đêm cài v0.1.53; v0.1.54 đủ hạn sau 23 giờ nữa."""
    _write(link, "genh.json", {"version": "v0.1.52", "updater": "systemd", "auto_update_enabled": True})
    releases = [
        _release("v0.1.54", promoted_ago={"hours": 1}),
        _release("v0.1.53", promoted_ago={"hours": 25}),
        _release("v0.1.55", promoted_ago={"hours": 30}, prerelease=True),
        _release("v0.1.56", promoted_ago={"hours": 30}, draft=True),
        _release("v0.1.52", promoted_ago={"hours": 90}),  # bản đang chạy ⇒ không phải ứng viên
        _release("v0.1.51"),  # chính thức nhưng không có dấu promote ⇒ không phải ứng viên (và cũ hơn current)
        {"tag_name": "nightly-build", "body": "", "published_at": _iso(days=1)},  # tag lạ ⇒ bỏ
        "không phải object",
    ]
    calls = _fake_github(monkeypatch, {LIST_URL_END: (200, releases)})
    await redis.delete(upd.LATEST_CACHE_KEY)
    r = await owner_api.get("/system/update")
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(calls) == 1 and calls[0].endswith(LIST_URL_END)  # MỘT lần gọi, không gọi /releases/latest
    assert body["latest"] == "v0.1.54" and body["update_available"] is True
    assert body["release_url"] == "https://example/v0.1.54"
    assert "genh:promoted_at" not in (body["release_notes"] or "")
    cands = body["nightly_candidates"]
    assert [c["tag"] for c in cands] == ["v0.1.53", "v0.1.54"]
    now = datetime.now(UTC)
    assert _parse(cands[0]["eligible_at"]) < now  # v0.1.53 đã đủ 24 giờ
    assert timedelta(hours=22, minutes=50) < _parse(cands[1]["eligible_at"]) - now < timedelta(hours=23, minutes=10)
    assert all(c["eligible_at"].endswith("Z") for c in cands)
    assert not any(c["tag"] in ("v0.1.55", "v0.1.56", "v0.1.52", "v0.1.51") for c in cands)
    assert body["published_at"] == _iso_of(releases[0])  # mốc chính thức của bản latest = lúc promote
    assert (await redis.ttl(upd.LATEST_CACHE_KEY)) > 0 and upd.LATEST_CACHE_KEY.endswith(":v2")


def _iso_of(release: dict[str, Any]) -> str:
    return str(upd.official_since(release["published_at"], release["body"]))


async def test_candidates_follow_the_running_version_without_refetch(
        owner_api: Api, link: Path, redis, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    """Bộ đệm giữ cả danh sách; sau khi máy lên v0.1.53 thì ứng viên chỉ còn bản mới hơn — không cần hỏi GitHub lại."""
    releases = [_release("v0.1.54", promoted_ago={"hours": 1}), _release("v0.1.53", promoted_ago={"hours": 25})]
    calls = _fake_github(monkeypatch, {LIST_URL_END: (200, releases)})
    await redis.delete(upd.LATEST_CACHE_KEY)
    assert [c["tag"] for c in (await owner_api.get("/system/update")).json()["nightly_candidates"]] == [
        "v0.1.53", "v0.1.54"]
    _write(link, "genh.json", {"version": "v0.1.53", "updater": "systemd"})
    body = (await owner_api.get("/system/update")).json()
    assert [c["tag"] for c in body["nightly_candidates"]] == ["v0.1.54"] and len(calls) == 1
    _write(link, "genh.json", {"version": "v0.1.54", "updater": "systemd"})
    body = (await owner_api.get("/system/update")).json()
    assert body["nightly_candidates"] == [] and body["update_available"] is False


async def test_candidates_are_capped_and_sorted_by_semver(monkeypatch: pytest.MonkeyPatch) -> None:
    """Thứ tự trả về của GitHub không quyết định: sắp tăng dần theo semver (v0.1.9 < v0.1.10), tối đa 10 bản."""
    tags = [f"v0.1.{n}" for n in (9, 10, 12, 11, 8, 20, 13, 14, 15, 16, 17, 18)]
    _fake_github(monkeypatch, {LIST_URL_END: (200, [_release(t, promoted_ago={"hours": 30}) for t in tags])})
    got = await upd.fetch_latest("o/r")
    assert got is not None and got["tag"] == "v0.1.20"
    cand_tags = [c["tag"] for c in got["candidates"]]
    assert cand_tags == [f"v0.1.{n}" for n in (9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 20)][-10:]
    assert len(cand_tags) == 10


@pytest.mark.parametrize("outcome", [
    (500, {"message": "boom"}),            # GitHub lỗi
    (403, {"message": "rate limit"}),      # hết hạn mức
    (200, {"không": "phải danh sách"}),    # sai hình dạng
    (200, []),                             # chưa có bản nào
    (200, [_release("v0.1.99", prerelease=True), _release("v0.1.98", draft=True)]),  # không có bản chính thức
    (200, ValueError("không phải JSON")),  # thân hỏng
    httpx.ConnectError("mạng đứt"),
])
async def test_list_failure_falls_back_to_releases_latest(owner_api: Api, link: Path, redis,  # type: ignore[no-untyped-def]
                                                          monkeypatch: pytest.MonkeyPatch, outcome: Any) -> None:
    fallback = _release("v0.1.53", promoted_ago={"hours": 26})
    calls = _fake_github(monkeypatch, {LIST_URL_END: outcome, LATEST_URL_END: (200, fallback)})
    await redis.delete(upd.LATEST_CACHE_KEY)
    r = await owner_api.get("/system/update")
    assert r.status_code == 200, r.text
    body = r.json()
    assert [u.rsplit("/", 1)[-1] for u in calls] == ["releases?per_page=10", "latest"]
    assert body["latest"] == "v0.1.53" and body["update_available"] is True
    assert body["release_url"] == "https://example/v0.1.53"
    assert "genh:promoted_at" not in (body["release_notes"] or "")
    assert [c["tag"] for c in body["nightly_candidates"]] == ["v0.1.53"]


async def test_both_requests_failing_gives_none_and_keeps_old_cache(
        owner_api: Api, link: Path, redis, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    _fake_github(monkeypatch, {LIST_URL_END: (502, None), LATEST_URL_END: (502, None)})
    assert await upd.fetch_latest("o/r") is None
    await redis.delete(upd.LATEST_CACHE_KEY)
    body = (await owner_api.get("/system/update")).json()
    assert body["latest"] is None and body["update_available"] is False and body["nightly_candidates"] == []
    _fake_github(monkeypatch, {LIST_URL_END: httpx.ReadTimeout("chậm"), LATEST_URL_END: httpx.ReadTimeout("chậm")})
    assert await upd.fetch_latest("o/r") is None


async def test_old_cache_shape_is_not_reused(owner_api: Api, link: Path, redis,  # type: ignore[no-untyped-def]
                                             monkeypatch: pytest.MonkeyPatch) -> None:
    """Khoá đệm đổi sang ':v2' — bản đệm hình dạng cũ (không có `candidates`) còn nằm ở khoá cũ không bị đọc nhầm."""
    await redis.set("gh:update:latest", json.dumps({"tag": "v0.1.99", "url": None, "notes": "cũ"}), ex=600)
    await redis.delete(upd.LATEST_CACHE_KEY)
    _fake_github(monkeypatch, {LIST_URL_END: (200, [_release("v0.1.53", promoted_ago={"hours": 26})])})
    try:
        body = (await owner_api.get("/system/update")).json()
        assert body["latest"] == "v0.1.53"
    finally:
        await redis.delete("gh:update:latest")


def test_official_since_and_eligible_at_use_the_later_marker() -> None:
    """eligible_at = mốc chính thức (dấu promote muộn nhất, hoặc published_at nếu muộn hơn) + 24 giờ."""
    rel = {"tag_name": "v0.1.53", "published_at": "2026-10-01T00:00:00Z", "html_url": "u",
           "body": "x\n<!-- genh:promoted_at=2026-10-05T10:00:00Z -->\n<!-- genh:promoted_at=2026-10-04T10:00:00Z -->"}
    got = upd._summarize([rel])
    assert got is not None
    assert got["candidates"] == [{"tag": "v0.1.53", "eligible_at": "2026-10-06T10:00:00Z"}]
    assert got["published_at"] == "2026-10-05T10:00:00Z"
