"""v0.1.36 (F-46): phiên bản lấy từ build-arg/tệp VERSION (không viết cứng) + GET /system/about + log khởi động.

- `gh.__version__`: env `GH_VERSION` (ảnh Docker) > tệp VERSION của repo > "dev".
- `/system/about`: `image_version` (ảnh api), `genh_version` (genh.json, null khi phát triển), `version` =
  genh_version ?? image_version (khoá cũ cho web cũ).
- Log khởi động API/worker có phiên bản.
"""

import importlib
import json
import logging
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

import gh
from tests.conftest import Api

REPO_ROOT = Path(__file__).resolve().parents[3]
VERSION = (REPO_ROOT / "VERSION").read_text(encoding="utf-8").strip()


@pytest.fixture
def info_logs(caplog: pytest.LogCaptureFixture) -> Iterator[pytest.LogCaptureFixture]:
    caplog.set_level(logging.INFO, logger="gh.app")
    caplog.set_level(logging.INFO, logger="gh.worker")
    yield caplog


def test_version_reads_repo_file() -> None:
    assert VERSION  # tệp VERSION của repo không rỗng
    assert gh.__version__ == VERSION


def test_version_env_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GH_VERSION", "  v9.9.9-test\n")
    try:
        importlib.reload(gh)
        assert gh.__version__ == "v9.9.9-test"
        monkeypatch.setenv("GH_VERSION", "   ")  # rỗng ⇒ bỏ qua, quay về tệp VERSION
        importlib.reload(gh)
        assert gh.__version__ == VERSION
    finally:
        monkeypatch.delenv("GH_VERSION", raising=False)
        importlib.reload(gh)
    assert gh.__version__ == VERSION


async def test_about_versions(owner_api: Api, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from gh.config import get_settings

    monkeypatch.setattr(get_settings(), "host_link_dir", str(tmp_path))
    r = await owner_api.get("/system/about")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["image_version"] == VERSION
    assert body["genh_version"] is None
    assert body["version"] == VERSION
    assert body["org_name"] == "Genesis Việt"

    (tmp_path / "genh.json").write_text(json.dumps({"version": "v0.1.30"}), encoding="utf-8")
    body = (await owner_api.get("/system/about")).json()
    assert body["genh_version"] == "v0.1.30"
    assert body["version"] == "v0.1.30"
    assert body["image_version"] == VERSION


async def test_about_dev_build_version_is_null(owner_api: Api, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from gh.config import get_settings

    monkeypatch.setattr(get_settings(), "host_link_dir", str(tmp_path))
    monkeypatch.setattr(gh, "__version__", "dev")
    body = (await owner_api.get("/system/about")).json()
    assert body["version"] is None and body["image_version"] == "dev" and body["genh_version"] is None


async def test_api_startup_log_has_version(info_logs: pytest.LogCaptureFixture, app: Any) -> None:
    recs = [r for r in info_logs.get_records("setup") if r.name == "gh.app"]
    assert any(VERSION in r.getMessage() for r in recs), [r.getMessage() for r in recs]


def test_worker_startup_message() -> None:
    from gh import worker

    msg = worker.startup_message()
    assert gh.__version__ in msg and "Asia/Ho_Chi_Minh" in msg


async def test_worker_startup_logs_version_and_heartbeat(
        info_logs: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    """Gọi `worker.startup` với các phụ thuộc nặng được thay bằng bản giả."""
    from gh import worker

    class _Db:
        async def __aenter__(self) -> "_Db":
            return self

        async def __aexit__(self, *a: Any) -> None:
            return None

        async def commit(self) -> None:
            return None

    class _Plugins:
        def start_control_listener(self) -> None:
            return None

    class _Scheduler:
        def __init__(self, *a: Any) -> None:
            pass

        async def start(self) -> None:
            return None

    class _Redis:
        def __init__(self) -> None:
            self.data: dict[str, Any] = {}

        async def set(self, k: str, v: Any, ex: int | None = None) -> None:
            self.data[k] = (v, ex)

    async def _noop(*a: Any, **kw: Any) -> None:
        return None

    async def _plugins(*a: Any, **kw: Any) -> _Plugins:
        return _Plugins()

    monkeypatch.setattr(worker, "configure_logging", lambda: None)
    monkeypatch.setattr(worker, "sessionmaker", lambda: _Db)
    monkeypatch.setattr(worker, "bootstrap", _noop)
    monkeypatch.setattr(worker.Redis, "from_url", staticmethod(lambda url: object()))
    monkeypatch.setattr(worker, "build_plugin_manager", _plugins)
    monkeypatch.setattr(worker, "EventBus", lambda *a: object())
    monkeypatch.setattr(worker.climod, "restore_active", _noop)
    monkeypatch.setattr(worker, "ModelRouter", lambda *a: object())
    monkeypatch.setattr(worker, "Refinery", lambda *a: object())
    monkeypatch.setattr(worker, "Scheduler", _Scheduler)
    monkeypatch.setattr(worker, "start_hooks", lambda *a, **kw: [])
    redis = _Redis()
    await worker.startup({"redis": redis})
    msgs = [r.getMessage() for r in info_logs.records if r.name == "gh.worker"]
    assert any(gh.__version__ in m and "Asia/Ho_Chi_Minh" in m for m in msgs), msgs
    value, ex = redis.data["gh:worker:heartbeat"]
    assert value.endswith("Z") and ex == 86400
