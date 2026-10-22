"""Plugin mẫu cho test (được nạp cả trong tiến trình lẫn trong sandbox)."""

import asyncio
import os

from gh.chassis.plugins import Plugin

SEEN: list[str] = []


class Recorder(Plugin):
    async def handle(self, event):  # type: ignore[no-untyped-def]
        SEEN.append(event.payload["n"])


class Exploder(Plugin):
    async def handle(self, event):  # type: ignore[no-untyped-def]
        raise RuntimeError("nổ")


class FlakyUntilTold(Plugin):
    """Lỗi cho tới khi settings['heal_file'] tồn tại (điều khiển được từ test)."""

    async def handle(self, event):  # type: ignore[no-untyped-def]
        if not os.path.exists(self.ctx.settings["heal_file"]):  # noqa: ASYNC240
            raise RuntimeError("chưa lành")
        SEEN.append(event.payload["n"])


class Sleeper(Plugin):
    async def handle(self, event):  # type: ignore[no-untyped-def]
        await asyncio.sleep(10)


class MemoryHog(Plugin):
    async def handle(self, event):  # type: ignore[no-untyped-def]
        self.blob = bytearray(1024 * 1024 * 1024)  # 1 GB > giới hạn RAM


class Publisher(Plugin):
    async def handle(self, event):  # type: ignore[no-untyped-def]
        await self.ctx.bus.publish("gh.test.allowed", "t.ok", {"n": event.payload["n"]}, actor="x")
        await self.ctx.bus.publish("gh.test.forbidden", "t.bad", {"n": event.payload["n"]}, actor="x")


class Printer(Plugin):
    async def handle(self, event):  # type: ignore[no-untyped-def]
        print("rác in ra stdout không được làm hỏng giao thức")
        self.ctx.log("INFO", f"đã xử lý {event.payload['n']}")
