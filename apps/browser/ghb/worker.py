"""Vòng chính của browser-worker: `python -m ghb.worker`.

Chỉ nói với Redis: đọc việc đã ký (`gh:browser:jobs`, nhóm `browser`), kiểm chữ ký + hạn + nonce
một lần, khoá 1 việc / tài khoản (`gh:browser:lock:<id>`), tối đa `GH_BROWSER_MAX_JOBS` việc song
song, nhịp tim `gh:browser:heartbeat`, nghe lệnh điều khiển đã ký (`halt` đóng mọi trình duyệt ngay,
`cancel` một việc). Không CSDL, không khoá master, không gọi model.
"""

import asyncio
import contextlib
import logging
import signal
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

import orjson
from redis.asyncio import Redis

from ghb import __version__, protocol, sandbox
from ghb.adapters import ADAPTERS
from ghb.config import Config, load
from ghb.runner import Runner

log = logging.getLogger("ghb.worker")
JOBS_GROUP = "browser"
LOCK_EXTRA_S = 120
# Rảnh quá lâu thì đóng hẳn Chromium (mở lại khi có việc) — tính năng không dùng thì không giữ trình duyệt trong RAM.
IDLE_CLOSE_S = 300


class Worker:
    def __init__(self, cfg: Config, redis: Redis, runner: Runner, *,
                 idle_close: Callable[[], Awaitable[None]] | None = None, idle_close_s: float = IDLE_CLOSE_S,
                 sandbox_info: Callable[[], dict[str, Any] | None] | None = None):
        self.cfg, self.redis, self.runner = cfg, redis, runner
        self.sandbox_info = sandbox_info
        self.running: dict[str, tuple[asyncio.Task[None], asyncio.Event]] = {}
        self.sem = asyncio.Semaphore(cfg.max_jobs)
        self.idle_close, self.idle_close_s = idle_close, idle_close_s
        self.last_active = time.monotonic()

    async def maybe_close_idle(self) -> bool:
        """Không có việc nào chạy và đã rảnh ≥ idle_close_s → đóng Chromium (BrowserHolder tự mở lại khi có việc)."""
        if self.idle_close is None or self.running or time.monotonic() - self.last_active < self.idle_close_s:
            return False
        await self.idle_close()
        return True

    async def heartbeat_once(self) -> None:
        await self.redis.set(protocol.HEARTBEAT_KEY, orjson.dumps({
            "version": __version__, "at": datetime.now(UTC).isoformat(), "running": len(self.running),
            "sandbox": self.sandbox_info() if self.sandbox_info is not None else None}), ex=45)

    async def _heartbeat(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            with contextlib.suppress(Exception):
                await self.heartbeat_once()
            with contextlib.suppress(Exception):
                await self.maybe_close_idle()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=15)

    def on_control(self, raw: Any) -> str:
        try:
            msg = protocol.verify(self.cfg.key, protocol.P_CONTROL, orjson.loads(raw))
        except (orjson.JSONDecodeError, TypeError):
            return "bad_json"
        if msg is None:
            return "bad_sig"
        if msg.get("type") == "halt":
            for _task, cancel in self.running.values():
                cancel.set()
            return "halt"
        if msg.get("type") == "cancel" and str(msg.get("job_id")) in self.running:
            self.running[str(msg["job_id"])][1].set()
            return "cancel"
        return "ignored"

    async def _control(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                pubsub = self.redis.pubsub()
                await pubsub.subscribe(protocol.CONTROL_CHANNEL)
                while not stop.is_set():
                    item = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                    if item is not None:
                        self.on_control(item["data"])
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — mất Redis thì thử lại
                log.warning("kênh điều khiển: %s", exc)
                await asyncio.sleep(1)

    async def accept(self, raw: Any) -> dict[str, Any] | None:
        """Kiểm một việc vừa nhận. Trả việc hợp lệ, hoặc None (sai chữ ký, hết hạn, nonce đã dùng — bỏ im lặng)."""
        try:
            env = protocol.verify(self.cfg.key, protocol.P_JOB, orjson.loads(raw))
        except (orjson.JSONDecodeError, TypeError):
            return None
        if env is None:
            log.warning("bỏ việc sai chữ ký")
            return None
        if int(env.get("exp") or 0) < time.time():
            log.info("bỏ việc hết hạn %s", env.get("id"))
            return None
        if not await self.redis.set(protocol.NONCE_PREFIX + str(env.get("nonce")), "1", nx=True, ex=3600):
            log.warning("bỏ việc dùng lại nonce %s", env.get("id"))
            return None
        return env

    async def execute(self, env: dict[str, Any]) -> None:
        job_id = str(env["id"])
        if env.get("platform") not in ADAPTERS:
            await self.runner.publish(env, "failed", {"code": "ERROR"})
            return
        if await self.redis.exists(protocol.HALT_KEY):
            await self.runner.publish(env, "halted")
            return
        if await self.redis.exists(protocol.CANCELLED_PREFIX + job_id):
            # api đã huỷ việc này khi nó còn trong hàng đợi (tạm dừng/gỡ tài khoản, rút đồng ý gửi) → không chạy.
            await self.runner.publish(env, "halted")
            return
        ttl = int((env.get("payload") or {}).get("timeout_s", 300)) + LOCK_EXTRA_S
        lock = protocol.LOCK_PREFIX + str(env["account_id"])
        if not await self.redis.set(lock, job_id, nx=True, ex=ttl):
            await self.runner.publish(env, "failed", {"code": "BUSY"})
            return
        cancel = asyncio.Event()
        try:
            await self.runner.publish(env, "started")
            task = asyncio.current_task()
            assert task is not None
            self.running[job_id] = (task, cancel)
            await self.runner.run(env, cancel)
        finally:
            self.running.pop(job_id, None)
            self.last_active = time.monotonic()
            with contextlib.suppress(Exception):
                if await self.redis.get(lock) == job_id.encode():
                    await self.redis.delete(lock)

    async def _one(self, env: dict[str, Any]) -> None:
        try:
            await self.execute(env)
        finally:
            self.sem.release()

    async def run(self, stop: asyncio.Event) -> None:
        with contextlib.suppress(Exception):
            await self.redis.xgroup_create(protocol.JOBS_STREAM, JOBS_GROUP, id="0", mkstream=True)
        side = [asyncio.create_task(self._heartbeat(stop)), asyncio.create_task(self._control(stop))]
        tasks: set[asyncio.Task[None]] = set()
        log.info("browser-worker %s sẵn sàng (tối đa %d việc)", __version__, self.cfg.max_jobs)
        try:
            while not stop.is_set():
                await self.sem.acquire()
                try:
                    resp = await self.redis.xreadgroup(JOBS_GROUP, self.cfg.consumer, {protocol.JOBS_STREAM: ">"},
                                                       count=1, block=2000)
                except Exception as exc:  # noqa: BLE001
                    self.sem.release()
                    log.warning("đọc hàng đợi: %s", exc)
                    await asyncio.sleep(1)
                    continue
                resp_list: list[Any] = list(resp or [])
                entries: list[Any] = [e for _s, es in resp_list for e in es]
                if not entries:
                    self.sem.release()
                    continue
                entry_id: Any = entries[0][0]
                fields: dict[Any, Any] = entries[0][1]
                # Nhận là ack ngay (tối đa một lần): worker chết giữa chừng → api tự đóng việc treo sau 15 phút.
                await self.redis.xack(protocol.JOBS_STREAM, JOBS_GROUP, entry_id)
                env = await self.accept(fields.get(b"m") or fields.get("m"))
                if env is None:
                    self.sem.release()
                    continue
                t = asyncio.create_task(self._one(env))
                tasks.add(t)
                t.add_done_callback(tasks.discard)
        finally:
            for _task, cancel in self.running.values():
                cancel.set()
            await asyncio.gather(*tasks, return_exceptions=True)
            for s in side:
                s.cancel()
            await asyncio.gather(*side, return_exceptions=True)


class BrowserHolder:
    """Một Chromium dùng chung (mỗi việc một ngữ cảnh riêng); tự mở lại nếu trình duyệt chết.

    Sandbox (F-85): GH_BROWSER_SANDBOX=on → bắt buộc có sandbox (lỗi thì ném); auto → thử có sandbox, lỗi thì lùi
    về không sandbox và BÁO THẬT; off → không sandbox. Sau mỗi lần khởi chạy dò lại (`sandbox.probe`) và giữ kết quả
    (kể cả khi Chromium bị đóng vì rảnh) — nhịp tim đưa nó cho api."""

    def __init__(self, cfg: Config, *, launcher: Callable[..., Awaitable[Any]] | None = None):
        self.cfg = cfg
        self._pw: Any = None
        self._browser: Any = None
        self._lock = asyncio.Lock()
        self._launcher = launcher
        self.sandbox: dict[str, Any] | None = None

    def sandbox_info(self) -> dict[str, Any] | None:
        return None if self.sandbox is None else dict(self.sandbox)

    async def close_browser(self) -> None:
        """Đóng Chromium khi rảnh (giữ Playwright driver nhỏ); lần `get()` sau tự mở lại."""
        async with self._lock:
            if self._browser is not None:
                with contextlib.suppress(Exception):
                    await self._browser.close()
                self._browser = None
                log.info("đóng Chromium (rảnh)")

    async def _launch(self, **kw: Any) -> Any:
        if self._launcher is not None:
            return await self._launcher(**kw)
        from playwright.async_api import async_playwright

        if self._pw is None:
            self._pw = await async_playwright().start()
        return await self._pw.chromium.launch(**kw)

    async def get(self) -> Any:
        async with self._lock:
            if self._browser is not None and self._browser.is_connected():
                return self._browser
            # WebRTC chỉ đi qua proxy (không mở UDP thẳng ra mạng nội bộ `browser` / lộ IP) — mọi lưu lượng qua egress.
            launch: dict[str, Any] = {"headless": self.cfg.headless,
                                      "args": ["--disable-dev-shm-usage",
                                               "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
                                               "--webrtc-ip-handling-policy=disable_non_proxied_udp"]}
            if self.cfg.proxy:
                launch["proxy"] = {"server": self.cfg.proxy}
            md = sandbox.mode()
            fallback: str | None = None
            if md == "off":
                browser = await self._launch(**launch, **sandbox.launch_kwargs(False))
                fallback = sandbox.REASON_OFF
            elif md == "on":
                browser = await self._launch(**launch, **sandbox.launch_kwargs(True))
            else:
                try:
                    browser = await self._launch(**launch, **sandbox.launch_kwargs(True))
                except Exception as exc:  # noqa: BLE001 — thường "No usable sandbox" / user namespace bị chặn
                    log.warning("Chromium không bật được sandbox (%s) — chạy KHÔNG sandbox, báo qua nhịp tim",
                                (str(exc).strip().splitlines() or [type(exc).__name__])[0][:160])
                    browser = await self._launch(**launch, **sandbox.launch_kwargs(False))
                    fallback = sandbox.REASON_BLOCKED
            self._browser = browser
            res = await sandbox.probe(browser)
            if fallback is not None:
                res = {**res, "enabled": False, "reason": fallback}
            self.sandbox = res
            log.info("sandbox Chromium: %s (%s)", "BẬT" if res["enabled"] else "TẮT", res["mode"])
            return browser

    async def warm_up(self) -> None:
        """Lúc worker khởi động: mở Chromium một lần để dò sandbox rồi đóng — trạng thái có ngay, không chờ việc đầu."""
        try:
            await self.get()
        except Exception as exc:  # noqa: BLE001
            md = sandbox.mode()
            log.warning("khởi động dò sandbox lỗi: %s", (str(exc).strip().splitlines() or [""])[0][:160])
            self.sandbox = {"enabled": False, "mode": md,
                            "reason": sandbox.REASON_BLOCKED if md != "off" else sandbox.REASON_OFF,
                            "checked_at": datetime.now(UTC).isoformat()}
        finally:
            await self.close_browser()

    async def close(self) -> None:
        with contextlib.suppress(Exception):
            if self._browser is not None:
                await self._browser.close()
        with contextlib.suppress(Exception):
            if self._pw is not None:
                await self._pw.stop()


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg = load()
    redis = Redis.from_url(cfg.redis_url)
    holder = BrowserHolder(cfg)
    worker = Worker(cfg, redis, Runner(cfg, redis, holder.get), idle_close=holder.close_browser,
                    sandbox_info=holder.sandbox_info)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop.set)
    try:
        await holder.warm_up()
        await worker.run(stop)
    finally:
        await holder.close()
        await redis.aclose()


if __name__ == "__main__":
    asyncio.run(main())
