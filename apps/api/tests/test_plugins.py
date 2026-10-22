"""Plugin manager: thứ tự nạp, phụ thuộc, khoá plugin nền, cách ly lỗi, sandbox."""

import asyncio
from pathlib import Path
from typing import Any

import pytest

from gh.chassis.bus import Event
from gh.chassis.plugins import (
    Manifest,
    PluginError,
    PluginIsolated,
    PluginManager,
    dependency_order,
    read_manifests,
    version_satisfies,
)
from tests import plugin_fixtures as fx

ROOT = Path(__file__).resolve().parents[3] / "plugins"


def mf(package: str, **kw: Any) -> Manifest:
    base: dict[str, Any] = {"package": package, "name": package, "version": "1.0.0", "layer": "extension"}
    return Manifest.model_validate(base | kw)


def ev(n: int) -> Event:
    return Event(stream="s", message_id="0-1", event_id="e", type="t", org_id=None, correlation_id="c",
                 actor="test", occurred_at="", schema_version=1, payload={"n": n})


def test_versions() -> None:
    assert version_satisfies("2.2.0", "^2.2.0") and version_satisfies("2.9.1", "^2.2.0")
    assert not version_satisfies("3.0.0", "^2.2.0") and not version_satisfies("2.1.9", "^2.2.0")
    assert version_satisfies("1.0.0", ">=0.9.0") and version_satisfies("1.0.0", "*")


def test_chassis_manifests_load_in_dependency_order() -> None:
    order = [m.package for m in dependency_order(read_manifests(ROOT))]
    assert order[0] == "@gen/chassis-kernel"
    assert order.index("@gen/chassis-bus") < order.index("@gen/chassis-store") < order.index("@gen/chassis-auth")
    manifests = read_manifests(ROOT)
    assert all(not m.removable and not m.can_disable for m in manifests if m.package.startswith("@gen/chassis-"))
    # Kênh tắt được (Owner quyết), nhưng không gỡ được vì là plugin lõi.
    assert all(not m.removable for m in manifests)
    assert order.index("@gen/intel-core") < order.index("@gen/channel-zalo")


def test_dependency_errors() -> None:
    with pytest.raises(PluginError, match="cần @x/missing"):
        dependency_order([mf("@x/a", dependencies={"@x/missing": "*"})])
    with pytest.raises(PluginError) as e:
        dependency_order([mf("@x/a", dependencies={"@x/b": "^2.0.0"}), mf("@x/b")])
    assert e.value.code == "DEPENDENCY_VERSION"
    with pytest.raises(PluginError) as e:
        dependency_order([mf("@x/a", dependencies={"@x/b": "*"}), mf("@x/b", dependencies={"@x/a": "*"})])
    assert e.value.code == "DEPENDENCY_CYCLE"


async def test_core_plugin_cannot_be_removed_or_disabled() -> None:
    pm = PluginManager()
    for m in read_manifests(ROOT):
        pm.register(m, origin="core")
    await pm.load_all()
    with pytest.raises(PluginError) as e:
        await pm.uninstall("@gen/chassis-store")
    assert e.value.code == "PLUGIN_LOCKED"
    with pytest.raises(PluginError) as e:
        await pm.disable("@gen/chassis-bus")
    assert e.value.code == "PLUGIN_LOCKED"
    # origin=core luôn ép removable=False dù manifest ghi khác.
    lp = pm.register(mf("@gen/fake-core", removable=True), origin="core")
    assert lp.manifest.removable is False


async def test_enable_disable_respects_dependencies() -> None:
    changes: list[tuple[str, dict[str, Any]]] = []

    async def persist(p: str, c: dict[str, Any]) -> None:
        changes.append((p, c))

    pm = PluginManager(persist=persist)
    pm.register(mf("@x/base"), origin="marketplace")
    pm.register(mf("@x/child", dependencies={"@x/base": "*"}), origin="marketplace")
    await pm.load_all()
    with pytest.raises(PluginError) as e:
        await pm.disable("@x/base")
    assert e.value.code == "DEPENDED_ON"
    await pm.disable("@x/child")
    await pm.disable("@x/base")
    with pytest.raises(PluginError) as e:
        await pm.enable("@x/child")
    assert e.value.code == "DEPENDENCY_DISABLED"
    await pm.enable("@x/base")
    await pm.enable("@x/child")
    assert changes == [("@x/child", {"is_enabled": False}), ("@x/base", {"is_enabled": False}),
                       ("@x/base", {"is_enabled": True}), ("@x/child", {"is_enabled": True})]
    await pm.uninstall("@x/child")
    assert "@x/child" not in pm.plugins


async def test_failing_plugin_is_isolated_and_others_keep_working() -> None:
    fx.SEEN.clear()
    pm = PluginManager()
    pm.register(mf("@x/bad", entry="tests.plugin_fixtures:Exploder",
                   breaker={"failure_threshold": 3, "window_s": 60, "cooldown_s": 60}), origin="marketplace")
    pm.register(mf("@x/good", entry="tests.plugin_fixtures:Recorder"), origin="marketplace")
    await pm.load_all()
    for i in range(3):
        with pytest.raises(RuntimeError):
            await pm.dispatch("@x/bad", ev(i))
    assert pm.get("@x/bad").health == "isolated"
    with pytest.raises(PluginIsolated):
        await pm.dispatch("@x/bad", ev(9))
    assert await pm.dispatch("@x/good", ev(1))
    assert fx.SEEN == [1]
    assert pm.get("@x/good").health == "healthy"
    report = {r["package"]: r for r in pm.reports()}
    assert report["@x/bad"]["breaker"]["state"] == "open"
    assert report["@x/bad"]["breaker"]["total_errors"] == 3


async def test_plugin_load_error_does_not_crash_manager() -> None:
    pm = PluginManager()
    pm.register(mf("@x/broken", entry="tests.khong_ton_tai:Nope"), origin="local_file")
    pm.register(mf("@x/ok", entry="tests.plugin_fixtures:Recorder"), origin="local_file")
    await pm.load_all()
    assert pm.get("@x/broken").enabled is False
    assert pm.get("@x/ok").enabled is True


# ─── Sandbox (tiến trình con) ───────────────────────────────────────────────

def sandboxed(package: str, entry: str, **kw: Any) -> Manifest:
    sb = {"mode": "subprocess", "memory_mb": 256, "timeout_s": 3} | kw.pop("sandbox", {})
    return mf(package, entry=entry, sandbox=sb, **kw)


async def test_sandbox_runs_plugin_in_child_process() -> None:
    pm = PluginManager()
    pm.register(sandboxed("@x/printer", "tests.plugin_fixtures:Printer"), origin="marketplace")
    await pm.load_all()
    try:
        assert await pm.dispatch("@x/printer", ev(7))
        assert ("INFO", "đã xử lý 7") in pm.get("@x/printer").logs
        proc = pm.get("@x/printer").instance.proc
        assert proc is not None and proc.pid != __import__("os").getpid()
    finally:
        await pm.shutdown()


async def test_sandbox_timeout_kills_and_respawns() -> None:
    pm = PluginManager()
    pm.register(sandboxed("@x/sleeper", "tests.plugin_fixtures:Sleeper", sandbox={"timeout_s": 1}),
                origin="marketplace")
    await pm.load_all()
    try:
        with pytest.raises(Exception, match="quá thời gian"):
            await pm.dispatch("@x/sleeper", ev(1))
        assert pm.get("@x/sleeper").instance.proc is None      # đã giết
        assert pm.get("@x/sleeper").breaker.total_errors == 1
    finally:
        await pm.shutdown()


async def test_sandbox_memory_limit_contains_hog() -> None:
    pm = PluginManager()
    pm.register(sandboxed("@x/hog", "tests.plugin_fixtures:MemoryHog"), origin="marketplace")
    pm.register(mf("@x/good", entry="tests.plugin_fixtures:Recorder"), origin="marketplace")
    await pm.load_all()
    try:
        with pytest.raises(Exception, match="MemoryError"):
            await pm.dispatch("@x/hog", ev(1))
        fx.SEEN.clear()
        assert await pm.dispatch("@x/good", ev(2))
        assert fx.SEEN == [2]
    finally:
        await pm.shutdown()


async def test_sandbox_can_only_publish_declared_streams(redis: Any) -> None:
    from gh.chassis.bus import EventBus

    bus = EventBus(redis)
    pm = PluginManager(bus)
    pm.register(sandboxed("@x/pub", "tests.plugin_fixtures:Publisher", events={"publish": ["gh.test.allowed"]}),
                origin="marketplace")
    await pm.load_all()
    try:
        assert await pm.dispatch("@x/pub", ev(5))
        await asyncio.sleep(0.2)
        assert await redis.xlen("gh.test.allowed") == 1
        assert await redis.exists("gh.test.forbidden") == 0
        assert any("bị chặn publish" in m for _, m in pm.get("@x/pub").logs)
    finally:
        await pm.shutdown()
