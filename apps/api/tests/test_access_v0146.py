"""v0.1.46 (F-21): GET /system/access, chuông 'Cổng đang mở cho cả mạng' (network.open_lan) và boss check
`remote_access`.

`run/network-status.json` do genh ghi (không tin cậy hoàn toàn): chỉ nhận giá trị trong tập cho phép; tệp thiếu/symlink
⇒ không mở không đóng chuông."""

import json
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text

from gh import health
from gh.config import get_settings
from gh.db import sessionmaker
from gh.system_api.access import is_local_url
from tests.conftest import Api
from tests.phase2 import org_id
from tests.test_rbac_api import login_as

KIND = "network.open_lan"
TITLE = "Cổng đang mở cho cả mạng"


@pytest.fixture
def link(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    d = tmp_path / "run"
    (d / "request").mkdir(parents=True)
    monkeypatch.setenv("GH_HOST_LINK_DIR", str(d))
    get_settings.cache_clear()
    yield d
    get_settings.cache_clear()


def write_net(link: Path, **kw: Any) -> None:
    data: dict[str, Any] = {"schema": 1, "mode": "lan_legacy", "bind_addr": "0.0.0.0", "site_address": "",
                            "public_url": "https://localhost:8443", "port": 8443,
                            "checked_at": "2026-10-02T00:00:00Z"}
    data.update(kw)
    (link / "network-status.json").write_text(json.dumps(data))


async def evaluate(redis: Any, org: Any) -> None:
    now = datetime.now(UTC)
    async with sessionmaker()() as s:
        await health.evaluate(s, redis, org, now=now, started_at=now)
        await s.commit()


async def alerts(db: Any) -> list[Any]:
    await db.commit()
    return list((await db.execute(text("""SELECT severity, title, link, fingerprint, cleared_at
                                          FROM ops.health_alerts WHERE key = :k"""), {"k": KIND})).all())


async def bells(db: Any) -> list[Any]:
    await db.commit()
    return list((await db.execute(text("SELECT title, body, link, user_id FROM core.notifications WHERE kind = :k"),
                                  {"k": KIND})).all())


@pytest.mark.parametrize("url", ["localhost", "https://localhost:8443", "127.0.0.5", "http://127.0.0.5:80", "::1",
                                 "https://[::1]:8443", "0.0.0.0", "foo.localhost", "https://foo.localhost", "", "  ",
                                 "http://[bad"])
def test_is_local_url_true(url: str) -> None:
    assert is_local_url(url) is True


@pytest.mark.parametrize("url", ["gen.tail1234.ts.net", "https://gen.tail1234.ts.net", "192.168.1.20",
                                 "https://192.168.1.20:8443", "https://localhost.example.com"])
def test_is_local_url_false(url: str) -> None:
    assert is_local_url(url) is False


async def test_access_default_is_local(owner_api: Api, link: Path) -> None:
    r = await owner_api.get("/system/access")
    assert r.status_code == 200
    body = r.json()
    assert body["public_url"] == "https://localhost:8443" and body["login_url"] == "https://localhost:8443/login"
    assert body["public_url_local"] is True and body["can_manage"] is True
    assert body["mode"] == "unknown" and body["bind_addr"] is None and body["site_address"] is None
    assert body["checked_at"] is None


async def test_access_remote_url_and_status_file(owner_api: Api, link: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GH_PUBLIC_URL", "https://gen.tail1234.ts.net/")
    get_settings.cache_clear()
    write_net(link, mode="tailscale", bind_addr="127.0.0.1", site_address="gen.tail1234.ts.net")
    body = (await owner_api.get("/system/access")).json()
    assert body["public_url"] == "https://gen.tail1234.ts.net"
    assert body["login_url"] == "https://gen.tail1234.ts.net/login" and body["public_url_local"] is False
    assert body["mode"] == "tailscale" and body["bind_addr"] == "127.0.0.1"
    assert body["site_address"] == "gen.tail1234.ts.net" and body["checked_at"] == "2026-10-02T00:00:00Z"
    assert all(v is None or isinstance(v, (str, bool)) for v in body.values())


async def test_access_filters_bad_fields(owner_api: Api, link: Path) -> None:
    write_net(link, mode="<script>", bind_addr="1.2.3.4", site_address="a.b/<x>;rm", checked_at="không phải ngày",
              extra={"x": 1})
    body = (await owner_api.get("/system/access")).json()
    assert body["mode"] == "unknown" and body["bind_addr"] is None and body["site_address"] is None
    assert body["checked_at"] is None and "extra" not in body


async def test_access_auditor_cannot_manage(owner_api: Api, client: Any, db: Any, link: Path) -> None:
    aud = await login_as(client, db, "auditor")
    r = await aud.get("/system/access")
    assert r.status_code == 200 and r.json()["can_manage"] is False


async def test_open_lan_rings_once_and_closes(owner_api: Api, app, db, redis, link: Path) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    write_net(link)
    for _ in range(3):
        await evaluate(redis, org)
    [a] = await alerts(db)
    assert a.severity == "warn" and a.title == TITLE and a.cleared_at is None
    assert a.link == "/system?tab=storage&focus=access" and a.fingerprint == "lan_legacy"
    rows = await bells(db)
    assert len(rows) == 1 and rows[0].title == TITLE and rows[0].link == a.link
    [issue] = [i for i in await health.active_issues(db, org) if i["kind"] == KIND]
    assert issue["action"] == "Chọn cách truy cập"
    [non_owner] = [i for i in await health.active_issues(db, org, is_owner=False) if i["kind"] == KIND]
    assert non_owner["action"] == "Nhờ Owner xử lý"
    # Owner: thẻ đích chỉ có lệnh chạy trên máy chủ ⇒ không hứa "Bấm để chọn"; người khác: câu nhờ Owner.
    assert "Bấm để chọn" not in issue["body"] and "chạy trên máy chủ" in issue["body"]
    assert non_owner["body"] == "Cổng Console đang mở cho cả mạng — nhờ Owner chọn cách truy cập từ xa."
    # Owner tự chọn LAN (genh remote --lan) ⇒ đóng, không chuông mới.
    write_net(link, mode="lan", bind_addr="0.0.0.0")
    await evaluate(redis, org)
    [a] = await alerts(db)
    assert a.cleared_at is not None
    assert len(await bells(db)) == 1
    # Mở lại (cài cũ) rồi sang local ⇒ đóng.
    write_net(link)
    await evaluate(redis, org)
    assert (await alerts(db))[0].cleared_at is None
    write_net(link, mode="local", bind_addr="127.0.0.1")
    await evaluate(redis, org)
    assert (await alerts(db))[0].cleared_at is not None


async def test_missing_or_symlink_file_keeps_state(owner_api: Api, app, db, redis, link: Path, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    await evaluate(redis, org)  # tệp thiếu ⇒ không mở
    assert await alerts(db) == []
    write_net(link)
    await evaluate(redis, org)
    assert (await alerts(db))[0].cleared_at is None
    # Tệp thiếu ⇒ giữ nguyên (không đóng).
    (link / "network-status.json").unlink()
    await evaluate(redis, org)
    assert (await alerts(db))[0].cleared_at is None
    # Symlink tới tệp 'local' ⇒ bị bỏ qua, vẫn không đóng.
    real = tmp_path / "real.json"
    real.write_text(json.dumps({"mode": "local", "bind_addr": "127.0.0.1"}))
    (link / "network-status.json").symlink_to(real)
    await evaluate(redis, org)
    assert (await alerts(db))[0].cleared_at is None
    # Chế độ lạ ⇒ giữ nguyên.
    (link / "network-status.json").unlink()
    write_net(link, mode="whatever", bind_addr="127.0.0.1")
    await evaluate(redis, org)
    assert (await alerts(db))[0].cleared_at is None
    assert len(await bells(db)) == 1


async def test_symlink_never_opens(owner_api: Api, app, db, redis, link: Path, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    real = tmp_path / "real.json"
    real.write_text(json.dumps({"mode": "lan_legacy", "bind_addr": "0.0.0.0"}))
    (link / "network-status.json").symlink_to(real)
    await evaluate(redis, org)
    assert await alerts(db) == []


# ─── boss check remote_access ───────────────────────────────────────────────────────────────────────────────

async def run_remote(api: Api, origin: str | None) -> dict[str, Any]:
    headers = dict(api._headers())
    if origin:
        headers["Origin"] = origin
    r = await api.c.post("/api/v1/boss-checks/remote_access/run", headers=headers)
    assert r.status_code == 200, r.text
    out: dict[str, Any] = r.json()
    return out


async def test_boss_remote_opened_on_server(owner_api: Api, link: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GH_PUBLIC_URL", "https://gen.tail1234.ts.net")
    get_settings.cache_clear()
    out = await run_remote(owner_api, "https://localhost:8443")
    assert out["status"] == "fail" and out["error_code"] == "REMOTE_OPENED_ON_SERVER"
    assert "Đang mở trên chính máy chủ" in out["message"]


async def test_boss_remote_not_configured(owner_api: Api, link: Path) -> None:
    out = await run_remote(owner_api, "https://gen.tail1234.ts.net")
    assert out["status"] == "fail" and out["error_code"] == "REMOTE_NOT_CONFIGURED"
    assert "genh remote tailscale" in out["message"] and "genh remote --lan" in out["message"]


async def test_boss_remote_pass_uses_origin_not_host(owner_api: Api, link: Path,
                                                      monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GH_PUBLIC_URL", "https://gen.tail1234.ts.net")
    get_settings.cache_clear()
    write_net(link, mode="tailscale", bind_addr="127.0.0.1")
    # Host của ASGI test là 'test' (không phải local) nhưng Origin local ⇒ vẫn fail: quyết theo Origin.
    out = await run_remote(owner_api, "https://127.0.0.1:8443")
    assert out["error_code"] == "REMOTE_OPENED_ON_SERVER"
    out = await run_remote(owner_api, "https://gen.tail1234.ts.net")
    assert out["status"] == "pass"
    assert out["detail"]["opened_from"] == "gen.tail1234.ts.net" and out["detail"]["access_mode"] == "tailscale"
    ov = (await owner_api.get("/boss-checks")).json()
    # v0.1.55 (G2): Truy cập từ xa là tuỳ chọn — dòng bắt buộc duy nhất là "nguồn AI" (chưa có ⇒ 0/1).
    assert ov["required_total"] == 1 and ov["required_done"] == 0
    remote = next(r for r in ov["rows"] if r["key"] == "remote")
    assert remote["row"] == 7 and remote["title"] == "Truy cập từ xa" and remote["done"] is True
    assert remote["optional"] is True
