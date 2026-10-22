"""Plugin manager: manifest, thứ tự nạp, phụ thuộc, bật/tắt nóng, cách ly lỗi (ARCHITECTURE §6).

- Plugin nền (origin=core) nạp trước theo load_order, không gỡ được; chỉ tắt được nếu manifest cho phép.
- Plugin cài thêm chạy trong tiến trình con (sandbox.mode=subprocess) — xem gh.chassis.sandbox.
- Mỗi lần gọi handler đi qua circuit breaker + timeout; lỗi một plugin không lan sang plugin khác.
- Bật/tắt/gỡ lan tới mọi tiến trình (api, worker) qua stream gh.plugin.control.
"""

import asyncio
import importlib
import json
import logging
import os
import re
import socket
from collections.abc import Awaitable, Callable, Coroutine
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from gh.chassis import bus as busmod
from gh.chassis.breaker import CLOSED, OPEN, BreakerConfig, CircuitBreaker

log = logging.getLogger("gh.plugins")

LAYERS = ("chassis", "channel", "intelligence", "provider", "action", "ui", "extension")
ORIGINS = ("core", "marketplace", "local_file")
_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+([-+].*)?$")


class SandboxSpec(BaseModel):
    mode: Literal["inprocess", "subprocess"] = "inprocess"
    memory_mb: int = 512
    timeout_s: float = 30.0
    net: Literal["none", "internal", "public"] = "internal"


class EventsSpec(BaseModel):
    subscribe: list[str] = Field(default_factory=list)
    publish: list[str] = Field(default_factory=list)


class BreakerSpec(BaseModel):
    failure_threshold: int = 5
    window_s: float = 60.0
    cooldown_s: float = 60.0


class Manifest(BaseModel):
    package: str
    name: str
    version: str
    layer: Literal["chassis", "channel", "intelligence", "provider", "action", "ui", "extension"]
    entry: str | None = None                    # "module.path:ClassName"; None = plugin chỉ khai báo
    description: str = ""
    permissions: list[str] = Field(default_factory=list)
    events: EventsSpec = Field(default_factory=EventsSpec)
    settings_schema: dict[str, Any] = Field(default_factory=dict)
    sandbox: SandboxSpec = Field(default_factory=SandboxSpec)
    dependencies: dict[str, str] = Field(default_factory=dict)   # package → version range (">=2.0.0")
    load_order: int = 100
    removable: bool = True
    can_disable: bool = True
    breaker: BreakerSpec = Field(default_factory=BreakerSpec)

    @field_validator("package")
    @classmethod
    def _pkg(cls, v: str) -> str:
        if not re.match(r"^@[a-z0-9-]+/[a-z0-9-]+$", v):
            raise ValueError("package phải dạng @scope/ten-plugin")
        return v

    @field_validator("version")
    @classmethod
    def _ver(cls, v: str) -> str:
        if not _VERSION_RE.match(v):
            raise ValueError("version phải dạng semver x.y.z")
        return v


def _vtuple(v: str) -> tuple[int, int, int]:
    a, b, c = re.split(r"[-+]", v)[0].split(".")
    return int(a), int(b), int(c)


def version_satisfies(version: str, spec: str) -> bool:
    """Hỗ trợ '*', 'x.y.z', '>=x.y.z', '^x.y.z' (cùng major)."""
    spec = spec.strip()
    if spec in ("", "*"):
        return True
    if spec.startswith(">="):
        return _vtuple(version) >= _vtuple(spec[2:])
    if spec.startswith("^"):
        want = _vtuple(spec[1:])
        have = _vtuple(version)
        return have[0] == want[0] and have >= want
    return _vtuple(version) == _vtuple(spec)


class PluginError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass
class PluginContext:
    package: str
    bus: busmod.EventBus | None
    settings: dict[str, Any]
    log: Callable[[str, str], None]


class Plugin:
    """Lớp cơ sở cho plugin chạy trong tiến trình. Plugin cài thêm cũng hiện thực đúng giao diện này."""

    def __init__(self, ctx: PluginContext):
        self.ctx = ctx

    async def on_load(self) -> None: ...
    async def on_enable(self) -> None: ...
    async def on_disable(self) -> None: ...
    async def on_unload(self) -> None: ...

    async def handle(self, event: busmod.Event) -> None:
        """Xử lý sự kiện từ các stream đã khai báo trong events.subscribe."""


@dataclass
class LoadedPlugin:
    manifest: Manifest
    origin: str
    enabled: bool
    instance: Any = None                      # Plugin hoặc SubprocessPlugin
    breaker: CircuitBreaker = field(default_factory=CircuitBreaker)
    tasks: list[asyncio.Task[None]] = field(default_factory=list)
    stop: asyncio.Event = field(default_factory=asyncio.Event)
    logs: list[tuple[str, str]] = field(default_factory=list)
    settings: dict[str, Any] = field(default_factory=dict)

    @property
    def health(self) -> str:
        if not self.enabled:
            return "disabled"
        if self.breaker.state == OPEN:
            return "isolated"
        if self.breaker.state != CLOSED or self.breaker.recent_failures:
            return "degraded"
        return "healthy"

    def report(self) -> dict[str, Any]:
        m = self.manifest
        return {"package": m.package, "name": m.name, "version": m.version, "layer": m.layer,
                "origin": self.origin, "enabled": self.enabled, "removable": self.origin != "core" and m.removable,
                "can_disable": m.can_disable, "load_order": m.load_order, "health": self.health,
                "breaker": {"state": self.breaker.state, "total_errors": self.breaker.total_errors,
                            "last_error": self.breaker.last_error},
                "sandbox": m.sandbox.model_dump(), "permissions": m.permissions,
                "recent_logs": [{"level": lv, "message": msg} for lv, msg in self.logs[-10:]]}


StateStore = Callable[[str, dict[str, Any]], Awaitable[None]]
BreakerSink = Callable[[str, str, str | None], Coroutine[Any, Any, None]]
LogSink = Callable[[str, str, str], Coroutine[Any, Any, None]]


def read_manifests(root: Path) -> list[Manifest]:
    out = []
    for p in sorted(root.glob("*/manifest.json")):
        out.append(Manifest.model_validate(json.loads(p.read_text(encoding="utf-8"))))
    return out


def dependency_order(manifests: list[Manifest]) -> list[Manifest]:
    """Sắp theo load_order nhưng luôn đặt phụ thuộc trước; báo lỗi nếu thiếu, sai phiên bản hoặc vòng."""
    by_pkg = {m.package: m for m in manifests}
    for m in manifests:
        for dep, spec in m.dependencies.items():
            if dep not in by_pkg:
                raise PluginError("DEPENDENCY_MISSING", f"{m.package} cần {dep}")
            if not version_satisfies(by_pkg[dep].version, spec):
                raise PluginError("DEPENDENCY_VERSION", f"{m.package} cần {dep} {spec}, có {by_pkg[dep].version}")
    ordered: list[Manifest] = []
    state: dict[str, int] = {}

    def visit(m: Manifest, stack: tuple[str, ...]) -> None:
        if state.get(m.package) == 2:
            return
        if state.get(m.package) == 1:
            raise PluginError("DEPENDENCY_CYCLE", " → ".join((*stack, m.package)))
        state[m.package] = 1
        for dep in sorted(m.dependencies, key=lambda d: (by_pkg[d].load_order, d)):
            visit(by_pkg[dep], (*stack, m.package))
        state[m.package] = 2
        ordered.append(m)

    for m in sorted(manifests, key=lambda x: (x.load_order, x.package)):
        visit(m, ())
    return ordered


def _outer_timeout(m: Manifest) -> float:
    # Sandbox tự canh thời gian (và khởi động lại tiến trình con); lớp ngoài chỉ là lưới an toàn.
    return m.sandbox.timeout_s + (2.0 if m.sandbox.mode == "subprocess" else 0.0)


class PluginManager:
    def __init__(self, bus: busmod.EventBus | None = None, *, persist: StateStore | None = None,
                 breaker_sink: BreakerSink | None = None, log_sink: LogSink | None = None,
                 instance_id: str | None = None):
        self.bus = bus
        self.persist = persist
        self.breaker_sink = breaker_sink
        self.log_sink = log_sink
        self.instance_id = instance_id or f"{socket.gethostname()}-{os.getpid()}"
        self.plugins: dict[str, LoadedPlugin] = {}
        self._control_task: asyncio.Task[None] | None = None
        self._control_stop = asyncio.Event()

    # ─── Đăng ký & nạp ───────────────────────────────────────────────────────
    def register(self, manifest: Manifest, *, origin: str, enabled: bool = True,
                 settings: dict[str, Any] | None = None) -> LoadedPlugin:
        if origin not in ORIGINS:
            raise PluginError("ORIGIN_INVALID", origin)
        if origin == "core" and manifest.removable:
            manifest = manifest.model_copy(update={"removable": False})
        lp = LoadedPlugin(manifest=manifest, origin=origin, enabled=enabled, settings=settings or {},
                          breaker=CircuitBreaker(BreakerConfig(**manifest.breaker.model_dump())))
        lp.breaker.on_transition = self._make_transition_cb(manifest.package)
        self.plugins[manifest.package] = lp
        return lp

    def _make_transition_cb(self, package: str) -> Callable[[str, str, str | None], None]:
        def cb(old: str, new: str, reason: str | None) -> None:
            self._log(package, "BREAK" if new == OPEN else "INFO", f"breaker {old} → {new}: {reason or ''}")
            if self.breaker_sink:
                asyncio.get_running_loop().create_task(self.breaker_sink(package, new, reason))
            if self.bus:
                asyncio.get_running_loop().create_task(self.bus.publish(
                    busmod.PLUGIN_HEALTH, "plugin.breaker", {"package": package, "state": new, "reason": reason},
                    actor=f"plugin:{package}"))
        return cb

    def _log(self, package: str, level: str, message: str) -> None:
        lp = self.plugins.get(package)
        if lp is not None:
            lp.logs.append((level, message))
            del lp.logs[:-200]
        log.info("[%s] %s %s", package, level, message)
        if self.log_sink:
            asyncio.get_running_loop().create_task(self.log_sink(package, level, message))

    def _instantiate(self, lp: LoadedPlugin) -> Any:
        m = lp.manifest
        if not m.entry:
            return None
        def plugin_log(level: str, msg: str) -> None:
            self._log(m.package, level, msg)

        ctx = PluginContext(package=m.package, bus=self.bus, settings=lp.settings, log=plugin_log)
        if m.sandbox.mode == "subprocess":
            from gh.chassis.sandbox import SubprocessPlugin
            return SubprocessPlugin(m, ctx)
        module_name, _, cls_name = m.entry.partition(":")
        cls = getattr(importlib.import_module(module_name), cls_name)
        return cls(ctx)

    async def load_all(self) -> None:
        order = dependency_order([lp.manifest for lp in self.plugins.values()])
        for m in order:
            lp = self.plugins[m.package]
            try:
                lp.instance = self._instantiate(lp)
                if lp.instance is not None:
                    await asyncio.wait_for(lp.instance.on_load(), m.sandbox.timeout_s)
                if lp.enabled:
                    lp.enabled = False
                    await self.enable(m.package, persist=False)
            except Exception as exc:  # noqa: BLE001 — plugin lỗi khi nạp không kéo sập hệ thống
                lp.breaker.record_failure(f"nạp lỗi: {exc}")
                lp.enabled = False
                self._log(m.package, "ERROR", f"nạp lỗi: {exc}")

    # ─── Bật / tắt / gỡ ─────────────────────────────────────────────────────
    def get(self, package: str) -> LoadedPlugin:
        lp = self.plugins.get(package)
        if lp is None:
            raise PluginError("NOT_FOUND", f"Không có plugin {package}")
        return lp

    async def enable(self, package: str, *, persist: bool = True) -> LoadedPlugin:
        lp = self.get(package)
        if lp.enabled:
            return lp
        for dep in lp.manifest.dependencies:
            if not self.get(dep).enabled:
                raise PluginError("DEPENDENCY_DISABLED", f"Cần bật {dep} trước")
        if lp.instance is not None:
            await asyncio.wait_for(lp.instance.on_enable(), lp.manifest.sandbox.timeout_s)
        lp.enabled = True
        lp.stop = asyncio.Event()
        self._start_consumers(lp)
        self._log(package, "INFO", "đã bật")
        if persist and self.persist:
            await self.persist(package, {"is_enabled": True})
        return lp

    async def disable(self, package: str, *, persist: bool = True) -> LoadedPlugin:
        lp = self.get(package)
        if not lp.manifest.can_disable:
            raise PluginError("PLUGIN_LOCKED", f"{lp.manifest.name} là plugin nền, không tắt được")
        for other in self.plugins.values():
            if other.enabled and package in other.manifest.dependencies:
                raise PluginError("DEPENDED_ON", f"{other.manifest.name} đang cần plugin này")
        if not lp.enabled:
            return lp
        await self._stop_consumers(lp)
        lp.enabled = False
        if lp.instance is not None:
            try:
                await asyncio.wait_for(lp.instance.on_disable(), lp.manifest.sandbox.timeout_s)
            except Exception as exc:  # noqa: BLE001
                self._log(package, "WARN", f"on_disable lỗi: {exc}")
        self._log(package, "INFO", "đã tắt")
        if persist and self.persist:
            await self.persist(package, {"is_enabled": False})
        return lp

    async def uninstall(self, package: str, *, persist: bool = True) -> None:
        lp = self.get(package)
        if lp.origin == "core" or not lp.manifest.removable:
            raise PluginError("PLUGIN_LOCKED", f"{lp.manifest.name} là plugin nền, không gỡ được")
        if lp.enabled:
            await self.disable(package, persist=False)
        if lp.instance is not None:
            try:
                await lp.instance.on_unload()
            except Exception as exc:  # noqa: BLE001
                self._log(package, "WARN", f"on_unload lỗi: {exc}")
        del self.plugins[package]
        if persist and self.persist:
            await self.persist(package, {"_deleted": True})

    def reset_breaker(self, package: str) -> None:
        self.get(package).breaker.reset()

    # ─── Gọi handler có cách ly ─────────────────────────────────────────────
    async def dispatch(self, package: str, event: busmod.Event) -> bool:
        """Gọi handler qua breaker + timeout. Trả False nếu bị chặn hoặc lỗi (lỗi không ném ra ngoài)."""
        lp = self.get(package)
        if not lp.enabled or lp.instance is None:
            return False
        if not lp.breaker.allow():
            raise PluginIsolated(package)
        try:
            await asyncio.wait_for(lp.instance.handle(event), _outer_timeout(lp.manifest))
        except Exception as exc:  # noqa: BLE001 — cách ly lỗi plugin
            lp.breaker.record_failure(f"{type(exc).__name__}: {exc}")
            self._log(package, "ERROR", f"handle lỗi: {exc}")
            raise
        lp.breaker.record_success()
        return True

    def _start_consumers(self, lp: LoadedPlugin) -> None:
        if self.bus is None or lp.instance is None:
            return
        for stream in lp.manifest.events.subscribe:
            lp.tasks.append(asyncio.get_running_loop().create_task(self._consume(lp, stream)))

    async def _consume(self, lp: LoadedPlugin, stream: str) -> None:
        """Vòng đọc stream của một plugin. Khi mạch mở thì ngừng đọc: sự kiện nằm lại trong stream, không mất."""
        assert self.bus is not None
        pkg = lp.manifest.package

        async def handler(ev: busmod.Event) -> None:
            await self.dispatch(pkg, ev)

        recovering = True   # vòng đầu: xử lý tin còn treo của chính mình (sau khởi động lại)
        while not lp.stop.is_set():
            if lp.breaker.state == OPEN and not lp.breaker.cooldown_elapsed():
                recovering = True
                await asyncio.sleep(0.5)
                continue
            state_before = lp.breaker.state
            try:
                await self.bus.process_once(stream, pkg, self.instance_id, handler, own_pending=recovering,
                                            count=10 if state_before == CLOSED else 1, block_ms=1000)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                self._log(pkg, "WARN", f"vòng đọc {stream} lỗi: {exc}")
                await asyncio.sleep(1)
            # Vừa qua lượt thử nửa mở → vòng sau đọc tiếp phần tin treo còn lại.
            recovering = state_before != CLOSED or lp.breaker.state != CLOSED

    async def _stop_consumers(self, lp: LoadedPlugin) -> None:
        lp.stop.set()
        for t in lp.tasks:
            t.cancel()
        for t in lp.tasks:
            try:
                await t
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        lp.tasks.clear()

    # ─── Lan lệnh điều khiển giữa các tiến trình ────────────────────────────
    async def broadcast(self, op: str, package: str, actor: str) -> None:
        if self.bus:
            await self.bus.publish(busmod.PLUGIN_CONTROL, f"plugin.{op}", {"package": package, "op": op,
                                   "origin_instance": self.instance_id}, actor=actor)

    async def _on_control(self, ev: busmod.Event) -> None:
        if ev.payload.get("origin_instance") == self.instance_id:
            return
        op, package = ev.payload["op"], ev.payload["package"]
        if package not in self.plugins:
            return
        if op == "enable":
            await self.enable(package, persist=False)
        elif op == "disable":
            await self.disable(package, persist=False)
        elif op == "reset_breaker":
            self.reset_breaker(package)
        elif op == "uninstall":
            await self.uninstall(package, persist=False)

    def start_control_listener(self) -> None:
        if self.bus is None:
            return
        self._control_task = asyncio.get_running_loop().create_task(self.bus.run(
            busmod.PLUGIN_CONTROL, f"pm-{self.instance_id}", self.instance_id, self._on_control,
            self._control_stop, block_ms=1000))

    async def shutdown(self) -> None:
        self._control_stop.set()
        if self._control_task:
            self._control_task.cancel()
        for lp in list(self.plugins.values()):
            await self._stop_consumers(lp)
            if isinstance(lp.instance, object) and hasattr(lp.instance, "close"):
                await lp.instance.close()

    def reports(self) -> list[dict[str, Any]]:
        return [lp.report() for lp in sorted(self.plugins.values(),
                                             key=lambda x: (x.origin != "core", x.manifest.load_order))]


class PluginIsolated(busmod.Deferred):
    """Plugin đang bị ngắt mạch; sự kiện để lại trong stream để xử lý khi mạch đóng."""

    def __init__(self, package: str):
        super().__init__(f"{package} đang bị cách ly (circuit open)")
        self.package = package
