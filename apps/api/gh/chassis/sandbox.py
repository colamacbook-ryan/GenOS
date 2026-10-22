"""Chạy plugin cài thêm trong tiến trình con cách ly (sandbox.mode = subprocess).

Giao thức: JSON một dòng mỗi thông điệp qua stdin/stdout.
  cha → con: {"id": n, "op": "on_load|on_enable|on_disable|on_unload|handle", "event": {...}}
  con → cha: {"id": n, "ok": true} | {"id": n, "ok": false, "error": "..."}
             {"log": [level, message]} | {"publish": {"stream", "type", "payload"}}
Giới hạn: RAM (RLIMIT_AS), timeout mỗi lời gọi (quá hạn → giết và khởi động lại tiến trình con),
chỉ được publish vào các stream khai báo trong manifest.events.publish.
"""

import asyncio
import dataclasses
import importlib
import json
import os
import sys
from typing import Any

from gh.chassis.plugins import Manifest, PluginContext


class SandboxError(Exception):
    pass


def _limit_resources(memory_mb: int) -> None:
    import resource

    limit = memory_mb * 1024 * 1024
    resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
    os.setsid()


class SubprocessPlugin:
    def __init__(self, manifest: Manifest, ctx: PluginContext):
        self.manifest, self.ctx = manifest, ctx
        self.proc: asyncio.subprocess.Process | None = None
        self._seq = 0
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._reader: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()

    async def _spawn(self) -> None:
        mem = self.manifest.sandbox.memory_mb
        self.proc = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "gh.chassis.sandbox", self.manifest.entry or "", self.manifest.package,
            json.dumps(self.ctx.settings),
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            preexec_fn=lambda: _limit_resources(mem), env={**os.environ, "PYTHONUNBUFFERED": "1"},
            limit=4 * 1024 * 1024)
        self._reader = asyncio.get_running_loop().create_task(self._read_loop())

    async def _read_loop(self) -> None:
        assert self.proc and self.proc.stdout
        while True:
            line = await self.proc.stdout.readline()
            if not line:
                break
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if "log" in msg:
                self.ctx.log(*msg["log"])
            elif "publish" in msg:
                await self._publish(msg["publish"])
            elif "id" in msg and msg["id"] in self._pending:
                self._pending.pop(msg["id"]).set_result(msg)
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(SandboxError("tiến trình plugin đã dừng"))
        self._pending.clear()

    async def _publish(self, req: dict[str, Any]) -> None:
        stream = req.get("stream")
        if stream not in self.manifest.events.publish:
            self.ctx.log("WARN", f"bị chặn publish vào {stream} (không khai báo trong manifest)")
            return
        if self.ctx.bus:
            await self.ctx.bus.publish(stream, req.get("type", "plugin.event"), req.get("payload", {}),
                                       actor=f"plugin:{self.manifest.package}")

    async def _kill(self) -> None:
        if self.proc and self.proc.returncode is None:
            self.proc.kill()
            await self.proc.wait()
        if self._reader:
            self._reader.cancel()
        self.proc = None

    async def _call(self, op: str, event: dict[str, Any] | None = None) -> None:
        async with self._lock:
            if self.proc is None or self.proc.returncode is not None:
                await self._spawn()
            assert self.proc and self.proc.stdin
            self._seq += 1
            fut: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
            self._pending[self._seq] = fut
            self.proc.stdin.write((json.dumps({"id": self._seq, "op": op, "event": event}) + "\n").encode())
            await self.proc.stdin.drain()
            try:
                resp = await asyncio.wait_for(fut, self.manifest.sandbox.timeout_s)
            except TimeoutError:
                self._pending.pop(self._seq, None)
                await self._kill()
                raise SandboxError(f"quá thời gian {self.manifest.sandbox.timeout_s}s, đã khởi động lại") from None
            except asyncio.CancelledError:
                # Bị huỷ từ ngoài: tiến trình con có thể đang kẹt → giết để lượt sau khởi động sạch.
                self._pending.pop(self._seq, None)
                await asyncio.shield(self._kill())
                raise
            if not resp.get("ok"):
                raise SandboxError(resp.get("error", "lỗi không rõ"))

    async def on_load(self) -> None:
        await self._call("on_load")

    async def on_enable(self) -> None:
        await self._call("on_enable")

    async def on_disable(self) -> None:
        await self._call("on_disable")

    async def on_unload(self) -> None:
        await self._call("on_unload")
        await self._kill()

    async def handle(self, event: Any) -> None:
        await self._call("handle", dataclasses.asdict(event))

    async def close(self) -> None:
        await self._kill()


# ─── Phía tiến trình con ─────────────────────────────────────────────────────

class _BusProxy:
    async def publish(self, stream: str, type: str, payload: dict[str, Any], **_: Any) -> str:
        _emit({"publish": {"stream": stream, "type": type, "payload": payload}})
        return ""


def _emit(msg: dict[str, Any]) -> None:
    out = sys.__stdout__
    assert out is not None
    out.write(json.dumps(msg, default=str) + "\n")
    out.flush()


async def _child_main(entry: str, package: str, settings: dict[str, Any]) -> None:
    from gh.chassis.bus import Event

    sys.stdout = sys.stderr  # print() của plugin không được làm hỏng kênh giao thức
    module_name, _, cls_name = entry.partition(":")
    cls = getattr(importlib.import_module(module_name), cls_name)
    ctx = PluginContext(package=package, bus=_BusProxy(), settings=settings,  # type: ignore[arg-type]
                        log=lambda level, msg: _emit({"log": [level, msg]}))
    plugin = cls(ctx)
    reader = asyncio.StreamReader()
    await asyncio.get_running_loop().connect_read_pipe(lambda: asyncio.StreamReaderProtocol(reader), sys.stdin)
    while line := await reader.readline():
        req = json.loads(line)
        try:
            if req["op"] == "handle":
                await plugin.handle(Event(**req["event"]))
            else:
                await getattr(plugin, req["op"])()
            _emit({"id": req["id"], "ok": True})
        except Exception as exc:  # noqa: BLE001
            _emit({"id": req["id"], "ok": False, "error": f"{type(exc).__name__}: {exc}"})


if __name__ == "__main__":
    asyncio.run(_child_main(sys.argv[1], sys.argv[2], json.loads(sys.argv[3])))
