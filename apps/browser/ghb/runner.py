"""Chạy MỘT việc trình duyệt (đăng nhập có cửa sổ từ xa / kiểm phiên / đọc) và gửi kết quả đã ký về api.

- Mỗi việc một ngữ cảnh trình duyệt MỚI (phiên nạp từ payload đã mã hoá khi truyền), xong là đóng — không giữ hồ sơ
  trên đĩa.
- Mọi yêu cầu mạng đi qua `guard`: chỉ https tới tên miền nền tảng; còn lại bị chặn (proxy ra ngoài chặn lần nữa).
- Trước MỖI bước kiểm công tắc Dừng tất cả + lệnh huỷ; huỷ → đóng ngữ cảnh ngay (thao tác đang chờ bị ngắt).
- Không plugin "stealth", không đổi user agent / vân tay, không giải CAPTCHA: gặp checkpoint/CAPTCHA → báo lỗi để api
  DỪNG tài khoản và báo Owner.
"""

import asyncio
import contextlib
import hashlib
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

import orjson
from redis.asyncio import Redis

from ghb import permit, protocol
from ghb.adapters import ADAPTERS, Adapter
from ghb.adapters.base import STATE_ERROR, TargetNotFound
from ghb.config import Config
from ghb.errors import JobError
from ghb.guard import url_allowed

log = logging.getLogger("ghb.runner")

FRAME_MIN_INTERVAL = 0.12        # ≤ ~8 khung/giây
STATE_POLL_S = 1.5
SPECIAL_KEYS = {"Space": " "}


__all__ = ["Halted", "JobError", "Runner"]

CONFIRM_TIMEOUT_MS = 10_000
SHOT_SOFT_MAX = int(1.9 * 1024 * 1024)
SHOT_HARD_MAX = 2 * 1024 * 1024
TRACE_MAX = 30


class Halted(Exception):
    pass


BrowserProvider = Callable[[], Awaitable[Any]]
ContextHook = Callable[[Any], Awaitable[None]]


class Runner:
    def __init__(self, cfg: Config, redis: Redis, browser: BrowserProvider, *, context_hook: ContextHook | None = None):
        self.cfg, self.redis, self._browser = cfg, redis, browser
        # Test cài trang mẫu vào ngữ cảnh (context.route) TRƯỚC guard — guard vẫn chạy trước và vẫn chặn.
        self.context_hook = context_hook

    # ─── gửi kết quả ──────────────────────────────────────────────────────────
    async def publish(self, job: dict[str, Any], typ: str, data: dict[str, Any] | None = None,
                      state: Any = None) -> None:
        msg: dict[str, Any] = {"v": protocol.VERSION, "job_id": job["id"], "account_id": job["account_id"],
                               "org_id": job["org_id"], "type": typ, "data": data or {}, "ts": int(time.time())}
        if state is not None:
            msg["state"] = protocol.seal(self.cfg.key, orjson.dumps(state),
                                         protocol.account_aad(job["org_id"], job["account_id"]))
        signed = protocol.sign(self.cfg.key, protocol.P_RESULT, msg)
        await self.redis.xadd(protocol.RESULTS_STREAM, {"m": orjson.dumps(signed)}, maxlen=1000, approximate=True)

    async def _frame(self, ticket: str, msg: dict[str, Any]) -> None:
        signed = protocol.sign(self.cfg.key, protocol.P_FRAME, {**msg, "ts": int(time.time())})
        await self.redis.publish(protocol.FRAMES_PREFIX + ticket, orjson.dumps(signed))

    # ─── kiểm dừng / nghỉ ───────────────────────────────────────────────────────
    async def check(self, cancel: asyncio.Event, job_id: str | None = None) -> None:
        """Dừng nếu: lệnh huỷ đã tới, Dừng tất cả đang bật, hoặc api đã huỷ RIÊNG việc này (khoá CANCELLED_PREFIX —
        lệnh pub/sub `cancel` gửi lúc việc còn trong hàng đợi không tới được `running`)."""
        if cancel.is_set() or await self.redis.exists(protocol.HALT_KEY):
            raise Halted()
        if job_id and await self.redis.exists(protocol.CANCELLED_PREFIX + job_id):
            cancel.set()
            raise Halted()

    async def pause(self, cancel: asyncio.Event, job_id: str | None = None) -> None:
        d = self.cfg.delay
        if d > 0:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(cancel.wait(), timeout=d)
        await self.check(cancel, job_id)

    # ─── ngữ cảnh ─────────────────────────────────────────────────────────────
    async def new_context(self, job: dict[str, Any], state: dict[str, Any] | None) -> tuple[Any, list[str]]:
        browser = await self._browser()
        kw: dict[str, Any] = {"viewport": self.cfg.viewport, "locale": self.cfg.locale,
                              "timezone_id": self.cfg.timezone, "service_workers": "block", "accept_downloads": False}
        if state is not None:
            kw["storage_state"] = state
        ctx = await browser.new_context(**kw)
        ctx.set_default_timeout(self.cfg.nav_timeout_ms)
        domains = tuple(job.get("domains") or ())
        blocked: list[str] = []

        async def guard(route: Any) -> None:
            url = route.request.url
            if url_allowed(url, domains):
                await route.fallback()
            else:
                blocked.append(url.split("?")[0][:120])
                await route.abort("blockedbyclient")

        if self.context_hook is not None:
            await self.context_hook(ctx)
        await ctx.route("**/*", guard)          # đăng ký SAU CÙNG → chạy TRƯỚC mọi handler khác
        return ctx, blocked

    # ─── chạy việc ─────────────────────────────────────────────────────────────
    async def run(self, job: dict[str, Any], cancel: asyncio.Event) -> None:
        adapter = ADAPTERS.get(job.get("platform", ""))
        ctx_box: list[Any] = []
        committed = asyncio.Event()      # việc ghi đã bấm gửi: huỷ đến sau KHÔNG đóng trình duyệt giữa chừng

        async def closer() -> None:
            await cancel.wait()
            if committed.is_set():
                return
            for c in ctx_box:
                with contextlib.suppress(Exception):
                    await c.close()

        watcher = asyncio.create_task(closer())
        try:
            if adapter is None:
                raise JobError("ERROR", "nền tảng chưa có adapter")
            await self.check(cancel, str(job.get("id") or ""))
            kind = job.get("kind")
            if kind == "login":
                await self._login(job, adapter, cancel, ctx_box)
            elif kind in ("read", "health"):
                await self._read(job, adapter, cancel, ctx_box, health=kind == "health")
            elif kind == "write":
                await self._write(job, adapter, cancel, ctx_box, committed)
            else:
                raise JobError("ERROR", f"loại việc lạ {kind}")
        except Halted:
            await self.publish(job, "halted")
        except JobError as e:
            await self.publish(job, "login.failed" if job.get("kind") == "login" else "failed", {"code": e.code})
        except Exception as e:  # noqa: BLE001 — lỗi trình duyệt không làm chết worker
            if cancel.is_set():
                await self.publish(job, "halted")
            else:
                # Chỉ dòng đầu (bỏ "Call log" của Playwright — có thể chứa URL kèm tham số phiên).
                log.warning("việc %s lỗi: %s %s", job.get("id"), type(e).__name__, str(e).split("\n", 1)[0][:200])
                code = "SELECTOR" if type(e).__name__ == "TimeoutError" else "ERROR"
                await self.publish(job, "login.failed" if job.get("kind") == "login" else "failed", {"code": code})
        finally:
            watcher.cancel()
            with contextlib.suppress(BaseException):
                await watcher
            for c in ctx_box:
                with contextlib.suppress(Exception):
                    await c.close()

    async def _state_code(self, adapter: Adapter, page: Any, ctx: Any, blocked: list[str],
                          domains: tuple[str, ...]) -> None:
        if not url_allowed(page.url, domains):
            raise JobError("BLOCKED_URL")
        st = await adapter.page_state(page, ctx)
        if st != "ok":
            raise JobError(STATE_ERROR[st])

    async def _read(self, job: dict[str, Any], adapter: Adapter, cancel: asyncio.Event, box: list[Any], *,
                    health: bool) -> None:
        started = time.monotonic()
        p = job.get("payload") or {}
        state = orjson.loads(protocol.unseal(self.cfg.key, p["state"],
                                             protocol.account_aad(job["org_id"], job["account_id"])))
        limits = p.get("limits") or {}
        max_pages = min(int(limits.get("max_pages", 40)), 40)
        max_items = min(int(limits.get("max_items", 30)), 30)
        domains = tuple(job.get("domains") or ())
        ctx, blocked = await self.new_context(job, state)
        box.append(ctx)
        page = await ctx.new_page()
        await page.goto(adapter.home, wait_until="domcontentloaded")
        pages = 1
        await self._state_code(adapter, page, ctx, blocked, domains)
        items: list[dict[str, Any]] = []
        if not health:
            for what in p.get("what") or ["notifications", "inbox"]:
                if pages >= max_pages:
                    break
                await self.pause(cancel)
                got = await adapter.read(page, what, max_items)
                pages += 1
                await self._state_code(adapter, page, ctx, blocked, domains)
                items.extend(got)
        await self.check(cancel)
        new_state = await ctx.storage_state()
        await self.publish(job, "done", {"items": items[: max_items * 2], "pages": pages, "page_state": "ok",
                                         "cost": {"ms": int((time.monotonic() - started) * 1000), "pages": pages,
                                                  "blocked": len(blocked)}}, state=new_state)

    async def _screenshot(self, page: Any) -> bytes | None:
        try:
            shot: bytes = await page.screenshot(type="jpeg", quality=70, full_page=False)
            if len(shot) > SHOT_SOFT_MAX:
                shot = await page.screenshot(type="jpeg", quality=40, full_page=False)
            return shot if len(shot) <= SHOT_HARD_MAX else None
        except Exception:  # noqa: BLE001 — chụp lỗi: api ghi PROOF_MISSING
            return None

    async def _write(self, job: dict[str, Any], adapter: Adapter, cancel: asyncio.Event, box: list[Any],
                     committed: asyncio.Event) -> None:
        # (a) permit TRƯỚC khi mở bất kỳ ngữ cảnh trình duyệt nào
        await permit.check(self.cfg, self.redis, job)
        p = job.get("payload") or {}
        action, target_url, text = str(p["action"]), str(p["target_url"]), str(p["text"])
        domains = tuple(job.get("domains") or ())
        if not url_allowed(target_url, domains):
            raise JobError("BLOCKED_URL")
        started = time.monotonic()
        trace: list[dict[str, Any]] = []

        def step(name: str, ok: bool = True) -> None:
            if len(trace) < TRACE_MAX:
                trace.append({"step": name, "ms": int((time.monotonic() - started) * 1000), "ok": ok})

        state = orjson.loads(protocol.unseal(self.cfg.key, p["state"],
                                             protocol.account_aad(job["org_id"], job["account_id"])))
        ctx, blocked = await self.new_context(job, state)
        box.append(ctx)
        page = await ctx.new_page()
        try:
            await adapter.open_target(page, action, target_url)
        except TargetNotFound as e:
            step("open", False)
            raise JobError("TARGET_NOT_FOUND") from e
        except Exception:
            # Trang checkpoint / CAPTCHA / đăng nhập không có ô trả lời: báo đúng nguyên nhân thay vì "SELECTOR".
            step("open", False)
            if not cancel.is_set():
                try:
                    await self._state_code(adapter, page, ctx, blocked, domains)
                except JobError:
                    raise
                except Exception:  # noqa: BLE001, S110 — không xác định được trạng thái: giữ lỗi gốc
                    pass
            raise
        step("open")
        pages = 1
        try:
            await self._state_code(adapter, page, ctx, blocked, domains)
        except JobError:
            step("page_state", False)
            raise                                   # checkpoint / CAPTCHA / đăng xuất → dừng, KHÔNG gửi
        step("page_state")
        job_id = str(job["id"])
        await self.pause(cancel, job_id)
        try:
            await adapter.compose(page, action, text, target_url)
        except TargetNotFound as e:
            step("compose", False)
            raise JobError("TARGET_NOT_FOUND") from e
        step("compose")
        await self.pause(cancel, job_id)
        await self.check(cancel, job_id)            # KIỂM DỪNG LẦN CUỐI ngay trước khi gửi (kể cả việc đã bị api huỷ)
        committed.set()
        step("halt_check")
        # ─── từ đây coi như ĐÃ GỬI: không bỏ dở, luôn chụp ảnh và báo 'done' ───
        # Bất kỳ lỗi nào sau điểm này (bấm Enter lỗi giữa chừng, trang đóng…) KHÔNG được báo 'failed'/'halted': phím
        # có thể đã gửi đi — báo 'failed' khiến Owner bấm gửi lại và tin bị gửi HAI lần. Báo 'done' với confirmed=False
        # + send_error để api nói rõ "chưa chắc đã gửi — mở Facebook kiểm tra trước khi gửi lại".
        confirmed = False
        send_error = False
        submitted = False
        try:
            await adapter.submit(page, action)
            submitted = True
            step("submit")
            confirmed = await adapter.confirm_sent(page, action, text, CONFIRM_TIMEOUT_MS)
            step("confirm", confirmed)
        except Exception as e:  # noqa: BLE001 — xem chú thích trên
            send_error = True
            step("confirm" if submitted else "submit", False)
            log.warning("việc %s lỗi sau khi bấm gửi: %s", job.get("id"), type(e).__name__)
        shot = await self._screenshot(page)
        step("screenshot", shot is not None)
        new_state = None
        with contextlib.suppress(Exception):
            new_state = await ctx.storage_state()
        proof = None
        proof_sha = None
        if shot is not None:
            aad = f"{job['org_id']}:{job['account_id']}:proof:{job['id']}"
            proof = protocol.seal(self.cfg.key, shot, aad)
            proof_sha = hashlib.sha256(shot).hexdigest()
        data: dict[str, Any] = {
            "action": action, "sent": True, "confirmed": confirmed, "proof": proof, "proof_sha256": proof_sha,
            "trace": trace, "cost": {"ms": int((time.monotonic() - started) * 1000), "pages": pages,
                                     "blocked": len(blocked)}}
        if send_error:
            data["send_error"] = True
        await self.publish(job, "done", data, state=new_state)

    async def _login(self, job: dict[str, Any], adapter: Adapter, cancel: asyncio.Event, box: list[Any]) -> None:
        p = job.get("payload") or {}
        ticket = str(p.get("ticket") or "")
        if not ticket:
            raise JobError("ERROR", "thiếu vé đăng nhập")
        timeout = min(int(p.get("timeout_s", 600)), 900)
        domains = tuple(job.get("domains") or ())
        login_url = str(p.get("login_url") or adapter.home)
        if not url_allowed(login_url, domains):
            raise JobError("BLOCKED_URL")
        ctx, _blocked = await self.new_context(job, None)
        box.append(ctx)
        page = await ctx.new_page()
        cdp = await ctx.new_cdp_session(page)
        last = [0.0]
        tasks: set[asyncio.Task[None]] = set()

        async def on_frame(params: dict[str, Any]) -> None:
            with contextlib.suppress(Exception):
                await cdp.send("Page.screencastFrameAck", {"sessionId": params["sessionId"]})
            now = time.monotonic()
            if now - last[0] < FRAME_MIN_INTERVAL:
                return
            last[0] = now
            meta = params.get("metadata") or {}
            await self._frame(ticket, {"t": "frame", "data": params["data"], "w": meta.get("deviceWidth"),
                                       "h": meta.get("deviceHeight")})

        def frame_cb(params: dict[str, Any]) -> None:
            t = asyncio.ensure_future(on_frame(params))
            tasks.add(t)
            t.add_done_callback(tasks.discard)

        cdp.on("Page.screencastFrame", frame_cb)
        pubsub = self.redis.pubsub()
        await pubsub.subscribe(protocol.INPUT_PREFIX + ticket)
        try:
            await page.goto(login_url, wait_until="domcontentloaded")
            vp = self.cfg.viewport
            await cdp.send("Page.startScreencast", {"format": "jpeg", "quality": 60, "maxWidth": vp["width"],
                                                    "maxHeight": vp["height"], "everyNthFrame": 1})
            await self._frame(ticket, {"t": "status", "state": "waiting",
                                       "message": "Sếp tự đăng nhập trong khung này (mật khẩu, mã 2FA)."})
            deadline = time.monotonic() + timeout
            next_check = 0.0
            while time.monotonic() < deadline:
                await self.check(cancel)
                msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=0.3)
                if msg is not None:
                    ev = self._input(msg.get("data"), ticket)
                    if ev is not None:
                        if ev["type"] == "cancel":
                            await self._frame(ticket, {"t": "status", "state": "cancelled", "message": "Đã huỷ."})
                            raise JobError("CANCELLED")
                        await self._dispatch(page, ev)
                        if ev["type"] == "done":
                            next_check = 0.0
                if time.monotonic() >= next_check:
                    next_check = time.monotonic() + STATE_POLL_S
                    if url_allowed(page.url, domains) and await adapter.page_state(page, ctx) == "ok":
                        state = await ctx.storage_state()
                        handle = await adapter.handle(page, ctx)
                        await self._frame(ticket, {"t": "status", "state": "logged_in",
                                                   "message": "Đã đăng nhập — đang lưu phiên (mã hoá)."})
                        await self.publish(job, "login.done", {"handle": handle}, state=state)
                        return
            await self._frame(ticket, {"t": "status", "state": "timeout", "message": "Hết 10 phút."})
            raise JobError("LOGIN_TIMEOUT")
        finally:
            with contextlib.suppress(Exception):
                await cdp.send("Page.stopScreencast")
            for t in list(tasks):
                t.cancel()
            with contextlib.suppress(Exception):
                await pubsub.unsubscribe()
                await pubsub.aclose()

    def _input(self, raw: Any, ticket: str) -> dict[str, Any] | None:
        try:
            ev = protocol.verify(self.cfg.key, protocol.P_INPUT, orjson.loads(raw))
        except (orjson.JSONDecodeError, TypeError):
            return None
        if ev is None or ev.get("ticket") != ticket or abs(time.time() - int(ev.get("ts") or 0)) > 120:
            return None
        return ev

    @staticmethod
    async def _dispatch(page: Any, ev: dict[str, Any]) -> None:
        t = ev.get("type")
        if t == "mouse":
            x, y, button = float(ev["x"]), float(ev["y"]), ev.get("button", "left")
            if ev["action"] == "click":
                await page.mouse.click(x, y, button=button)
            elif ev["action"] == "move":
                await page.mouse.move(x, y)
            elif ev["action"] == "down":
                await page.mouse.move(x, y)
                await page.mouse.down(button=button)
            elif ev["action"] == "up":
                await page.mouse.move(x, y)
                await page.mouse.up(button=button)
        elif t == "wheel":
            await page.mouse.move(float(ev["x"]), float(ev["y"]))
            await page.mouse.wheel(float(ev["dx"]), float(ev["dy"]))
        elif t == "key":
            key = SPECIAL_KEYS.get(ev["key"], ev["key"])
            if ev["action"] == "press":
                await page.keyboard.press(key)
            elif ev["action"] == "down":
                await page.keyboard.down(key)
            else:
                await page.keyboard.up(key)
        elif t == "text":
            await page.keyboard.insert_text(str(ev["text"]))
        elif t == "nav":
            if ev["action"] == "back":
                await page.go_back()
            else:
                await page.reload()
