"""Tiến trình nền (arq): chạy plugin tiêu thụ sự kiện + việc định kỳ.

- Giai đoạn 1: kiểm chuỗi Action Log hằng đêm, bảo trì phân vùng pg_partman.
- Giai đoạn 2: bộ kích hoạt sàng lọc (chu kỳ / ngưỡng / đường nhanh / chạy ngay), dò trùng định danh
  mỗi 10 phút, nén sổ tay hằng ngày.
- Giai đoạn 3: hook sau sàng lọc và việc định kỳ của từng cụm màn (`gh.biz.*.jobs`), tự đăng ký.
- v0.1.36 (F-45): lịch cron hiểu theo GIỜ VN (`WORKER_TZ`, không phụ thuộc múi giờ máy/ảnh Docker); job nặng
  theo ngày dời về 04:20–05:10 — ngoài giờ làm việc 08:00–18:00 và ngoài cửa sổ cập nhật genh 02:30–03:30.
- v0.1.36 (F-6): mọi cron được bọc `_tracked` — sau mỗi lần chạy ghi `gh:cron:last:<tên hàm>` (+ tên vào tập
  `gh:cron:names`) = JSON
  {"at": ISO UTC "Z", "ok": bool, "ms": int} (TTL 7 ngày) và `gh:worker:heartbeat` = ISO UTC (TTL 1 ngày; cũng
  ghi lúc startup) — API đọc để dựng GET /system/health ("Bộ xử lý nền" im lặng / cron hỏng).
- v0.1.40 (F-2): mọi việc dọn dữ liệu quá hạn đi qua `gh.retention` — một cron `retention_sweep` 05:00 giờ VN thay
  cho purge_gen_conversations (05:00) và purge_notifications (05:10) cũ (hai hàm vẫn ở `functions` cho job enqueue cũ).
- v0.1.40 (F-16): `job_timeout` tường minh (JOB_TIMEOUT = 300 giây như mặc định arq). `_tracked` nhận ra lần chạy
  bị cắt vì quá giờ (payload thêm "timeout": bool, bộ đếm `gh:cron:timeouts:<tên>`); quá giờ 2 lần liền ⇒ sự cố
  `job.timeout:<tên>` (ops.health_alerts + chuông cho Owner), tự đóng khi lần chạy sau thành công.
- v0.1.41 (F-8b): `gen_briefing` — Bản tin Gen 07:30 / 17:30 giờ VN (`gh.gen.briefing`); cron chạy thêm 08:30, 09:30,
  18:30, 19:30 chỉ để bù khi worker lỡ giờ (idempotent theo khung giờ — không gửi lần hai; quá 3 giờ thì bỏ).
  Việc nền (sàng lọc, trực việc, bản tin) mặc định chỉ dùng khoá API (F-86, gh.providers.router).
- v0.1.44 (F-8c): `telegram_flush` mỗi phút gửi hộp thư đi Telegram (bản tin + nhắc việc của Owner).
- v0.1.49 (F-83): `hub_breaker_watch` mỗi 5 phút — Gen-hub không trả lời hơn 15 phút (ngắt mạch đã mở) ⇒ sự cố
  `hub.breaker` + chuông Owner một lần (`gh.hub_link.service.breaker_watch`); tự đóng khi gọi lại được.
- v0.1.50 (F-87): `gen_kho_release` phút 7 và 37 mỗi giờ — Gen đề xuất ghi Phiên vào Kho khi máy chủ lên bản mới (mỗi
  tổ chức, mỗi bản đúng MỘT lần; chỉ khi Gen-hub đã cấp quyền ghi Kho — `gh.gen.kho_release`). Không ghi gì lên Kho.
- v0.1.54 (g1-api): `gen_coach` 09:05 / 11:05 / 14:05 giờ VN — chuông "Hôm nay Sếp còn n việc cần làm" cho từng Owner
  (tối đa một chuông mỗi Owner mỗi ngày, ngoài giờ yên lặng; `gh.gen.coach.cron`). Không gọi model, không đẩy Telegram.
"""

import asyncio
import functools
import logging
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

import orjson
from arq import cron
from arq.connections import RedisSettings
from arq.cron import CronJob as ArqCronJob
from redis.asyncio import Redis
from sqlalchemy import text

from gh import __version__, biz, health, jobcodec, retention
from gh.app import build_plugin_manager, configure_logging
from gh.backup import FUNCTIONS as BACKUP_FUNCTIONS
from gh.backup import JOBS as BACKUP_JOBS
from gh.biz.hooks import start_hooks
from gh.bootstrap import bootstrap
from gh.chassis import actionlog
from gh.chassis.bus import EventBus
from gh.config import get_settings
from gh.db import admin_sessionmaker, dispose_engine, sessionmaker
from gh.gen import briefing, kho_release
from gh.gen.coach import cron as coach_cron
from gh.hardening import set_not_dumpable
from gh.hub_link import service as hub_link
from gh.identity import service as identity
from gh.memory import notebook
from gh.owner_channel import reports as owner_reports
from gh.providers import cli as climod
from gh.providers import clients
from gh.providers.router import ModelRouter
from gh.refinery.runner import Refinery
from gh.refinery.scheduler import Scheduler
from gh.social import service as social
from gh.social import session_watch
from gh.telegram import service as telegram
from gh.telegram.poller import JOBS as TELEGRAM_JOBS

log = logging.getLogger("gh.worker")

# v0.1.36 (F-45): cron của arq hiểu theo múi giờ này (WorkerSettings.timezone). Ảnh python:slim có thể thiếu
# /usr/share/zoneinfo — gói `tzdata` (pyproject.toml) bảo đảm ZoneInfo nạp được.
WORKER_TZ = ZoneInfo("Asia/Ho_Chi_Minh")

# v0.1.36 (F-6) — hợp đồng Redis với API (GET /system/health đọc).
CRON_LAST_KEY = "gh:cron:last:{}"
CRON_LAST_TTL = 7 * 86400
#: Tập tên hàm cron đã ghi dấu — API đọc tập này + MGET thay vì SCAN `gh:cron:last:*` mỗi lần.
CRON_NAMES_KEY = "gh:cron:names"
HEARTBEAT_KEY = "gh:worker:heartbeat"
HEARTBEAT_TTL = 86400
# v0.1.40 (F-16): bộ đếm lần quá giờ LIÊN TIẾP của từng cron; đạt TIMEOUT_ALERT_AFTER ⇒ sự cố + chuông.
CRON_TIMEOUTS_KEY = "gh:cron:timeouts:{}"
CRON_TIMEOUTS_TTL = 7 * 86400
TIMEOUT_ALERT_AFTER = 2
#: Thời gian tối đa mặc định của một job (giây) — như mặc định arq, đặt tường minh để `_tracked` biết.
JOB_TIMEOUT = 300
#: Nhãn tiếng Việt của việc nền (tiêu đề chuông quá giờ); thiếu nhãn ⇒ tên hàm.
JOB_LABELS = {
    "detect_identities": "Dò trùng danh tính",
    "graph_recompute": "Dựng bản đồ quan hệ",
    "retention_sweep": "Dọn dữ liệu quá hạn",
    "partition_maintenance": "Bảo trì phân vùng",
    "verify_action_log": "Kiểm chuỗi nhật ký hành động",
    "compact_notebooks": "Nén sổ tay",
    "expire_sessions": "Dọn phiên đăng nhập",
    "hub_token_expiry_scan": "Nhắc hạn token Gen-hub",
    "social_schedule": "Lịch đọc mạng xã hội",
    "social_session_check": "Kiểm phiên mạng xã hội",
    "people_review_recompute": "Tính lại đánh giá nhân sự",
    "scheduled_backup_scan": "Sao lưu theo lịch",
    "gen_briefing": "Bản tin Gen",
    "telegram_flush": "Gửi tin Telegram",
    "owner_flush": "Báo cáo cho Sếp qua kênh quản trị",
    "telegram_inbound": "Nhận tin Telegram của Sếp",
    "hub_breaker_watch": "Theo dõi Gen-hub",
    "gen_kho_release": "Đề xuất ghi Phiên vào Kho",
    "gen_coach": "Gen hướng dẫn",
}
#: v0.1.44 (F-8c): lượt gửi hộp thư đi Telegram ngắn (≤ 20 tin, ngân sách 60 giây — gh.telegram.service).
TELEGRAM_FLUSH_TIMEOUT = 90


def _utc_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def startup_message() -> str:
    """Dòng log khởi động — có phiên bản và múi giờ để đối chiếu nhanh khi đọc log/`genh doctor`."""
    return f"Worker sẵn sàng (phiên bản {__version__}, múi giờ {WORKER_TZ.key})"


async def _beat(redis: Any) -> None:
    """Ghi nhịp tim của Bộ xử lý nền; lỗi Redis chỉ log, không làm hỏng job."""
    if redis is None:
        return
    try:
        await redis.set(HEARTBEAT_KEY, _utc_iso(), ex=HEARTBEAT_TTL)
    except Exception as exc:  # noqa: BLE001 — dấu sức khoẻ không được làm hỏng job
        log.warning("Không ghi được nhịp tim worker: %s", exc)


async def _job_timeout_alert(ctx: dict[str, Any], name: str, *, open_: bool) -> None:
    """Mở (quá giờ 2 lần liền) hoặc đóng sự cố `job.timeout:<tên>` cho MỌI tổ chức. Bên gọi bắt lỗi."""
    label = JOB_LABELS.get(name, name)
    key = f"job.timeout:{name}"
    async with sessionmaker()() as db:
        for org in (await db.execute(text("SELECT id FROM core.organizations"))).scalars().all():
            if open_:
                await health.raise_once(
                    db, org, key=key, kind="job.timeout", severity="warn",
                    title=f"Việc nền '{label}' chạy quá giờ {TIMEOUT_ALERT_AFTER} lần liền",
                    body="Hệ thống vẫn chạy; việc này sẽ thử lại ở lượt sau. Nếu còn lặp lại, mở Sức khoẻ hệ thống.",
                    link=health.HEALTH_LINK, fingerprint=str(TIMEOUT_ALERT_AFTER), redis=ctx.get("redis"))
            else:
                await health.clear(db, org, key, redis=ctx.get("redis"))
        await db.commit()


async def _note_timeout(ctx: dict[str, Any], name: str, *, timed_out: bool, ok: bool) -> None:
    """Đếm lần quá giờ liên tiếp (Redis) ⇒ mở/đóng sự cố. Mọi lỗi Redis/DB chỉ log.warning."""
    redis = ctx.get("redis")
    if redis is None:
        return
    key = CRON_TIMEOUTS_KEY.format(name)
    try:
        if timed_out:
            count = int(await redis.incr(key))
            await redis.expire(key, CRON_TIMEOUTS_TTL)
            if count >= TIMEOUT_ALERT_AFTER:
                await _job_timeout_alert(ctx, name, open_=True)
            return
        raw = await redis.get(key)
        if raw is None:
            return
        prev = int(raw)
        if ok or prev < TIMEOUT_ALERT_AFTER:
            # lần chạy lỗi (không quá giờ) khi sự cố đang mở: giữ bộ đếm để lần thành công sau đóng sự cố
            await redis.delete(key)
        if ok and prev >= TIMEOUT_ALERT_AFTER:
            await _job_timeout_alert(ctx, name, open_=False)
    except Exception as exc:  # noqa: BLE001 — dấu sức khoẻ không được làm hỏng job
        log.warning("Không ghi được bộ đếm quá giờ của %s: %s", name, exc)


def _tracked(fn: Callable[[dict[str, Any]], Awaitable[Any]], timeout_s: float = JOB_TIMEOUT
             ) -> Callable[[dict[str, Any]], Awaitable[Any]]:
    """v0.1.36 (F-6): bọc hàm cron — chạy `fn`, rồi (kể cả khi lỗi/bị huỷ) ghi dấu lần chạy cuối vào Redis.

    Ngoại lệ của `fn` (kể cả CancelledError) luôn được ném lại nguyên vẹn; lỗi ghi Redis chỉ log.warning.
    `functools.wraps` giữ `__qualname__` ⇒ tên CronJob của arq vẫn là `cron:<tên hàm>`.

    v0.1.40 (F-16): CancelledError/TimeoutError khi đã chạy ≥ `timeout_s` − 1 giây ⇒ coi là quá giờ (arq huỷ job
    bằng asyncio.wait_for); bị huỷ sớm hơn (vd tắt worker) KHÔNG tính.
    """

    @functools.wraps(fn)
    async def wrapper(ctx: dict[str, Any]) -> Any:
        started = time.monotonic()
        ok = False
        timed_out = False
        try:
            result = await fn(ctx)
            ok = True
            return result
        except (asyncio.CancelledError, TimeoutError):
            timed_out = time.monotonic() - started >= timeout_s - 1
            raise
        finally:
            redis = ctx.get("redis")
            if redis is not None:
                payload = {"at": _utc_iso(), "ok": ok, "ms": int((time.monotonic() - started) * 1000),
                           "timeout": timed_out}
                try:
                    await redis.set(CRON_LAST_KEY.format(fn.__name__), orjson.dumps(payload), ex=CRON_LAST_TTL)
                    await redis.sadd(CRON_NAMES_KEY, fn.__name__)
                except Exception as exc:  # noqa: BLE001 — dấu sức khoẻ không được làm hỏng job
                    log.warning("Không ghi được dấu cron %s: %s", fn.__name__, exc)
                await _note_timeout(ctx, fn.__name__, timed_out=timed_out, ok=ok)
                await _beat(redis)

    return wrapper


def _cron(fn: Callable[[dict[str, Any]], Awaitable[Any]], **kw: Any) -> ArqCronJob:
    """`arq.cron` cho hàm đã bọc `_tracked` — truyền NGUYÊN mọi khoá (vd `timeout` của sao lưu); timeout hiệu lực
    (kw['timeout'] hoặc JOB_TIMEOUT) đi vào `_tracked` để nhận ra lần chạy quá giờ."""
    timeout: Any = kw.get("timeout") or JOB_TIMEOUT
    timeout_s = float(timeout.total_seconds()) if hasattr(timeout, "total_seconds") else float(timeout)
    return cron(_tracked(fn, timeout_s=timeout_s), **kw)  # type: ignore[arg-type]


async def startup(ctx: dict[str, Any]) -> None:
    # Gói G: việc ĐẦU TIÊN của tiến trình worker (trước DB, Redis, job) — agy cùng uid không đọc được
    # /proc/<pid>/environ. Đặt ở đây (không ở mức nhập mô-đun) để test nhập gh.worker không đổi trạng thái pytest.
    set_not_dumpable()
    configure_logging()
    # Review lần 2 v0.1.63 (P3-5): HOME tạm agy (có token) còn sót khi tiến trình worker trước bị SIGKILL.
    clients.sweep_stale_turn_dirs()
    async with sessionmaker()() as db:
        await bootstrap(db, mint_setup_code=False)   # P2-2: chỉ api sinh / giao mã thiết lập, worker không sinh lại
        await db.commit()
    s = get_settings()
    ctx["redis_bus"] = Redis.from_url(s.redis_url)
    ctx["plugins"] = await build_plugin_manager(EventBus(ctx["redis_bus"], s.stream_maxlen), ctx["redis_bus"])
    ctx["plugins"].start_control_listener()
    sm = sessionmaker()
    bus = EventBus(ctx["redis_bus"], s.stream_maxlen)
    try:
        await climod.restore_active(sm, owns_logins=False)
    except Exception as exc:  # noqa: BLE001 — thiếu phiên CLI không chặn worker
        log.warning("Không khôi phục được phiên CLI: %s", exc)
    router = ModelRouter(sm, ctx["redis_bus"])
    ctx["model_router"] = router
    ctx["scheduler"] = Scheduler(sm, ctx["redis_bus"], Refinery(sm, ctx["redis_bus"], router, bus))
    await ctx["scheduler"].start()
    ctx["hooks_stop"] = asyncio.Event()
    ctx["hooks"] = start_hooks(biz.hooks(), sm=sm, redis=ctx["redis_bus"], bus=bus, router=router,
                               stop=ctx["hooks_stop"])
    await _beat(ctx.get("redis"))
    log.info(startup_message())


async def shutdown(ctx: dict[str, Any]) -> None:
    ctx["hooks_stop"].set()
    for t in ctx["hooks"]:
        t.cancel()
    await asyncio.gather(*ctx["hooks"], return_exceptions=True)
    await ctx["scheduler"].stop()
    await ctx["plugins"].shutdown()
    await social.close_bus(ctx["redis_bus"])
    await ctx["redis_bus"].aclose()
    await dispose_engine()


async def verify_action_log(ctx: dict[str, Any]) -> dict[str, Any]:
    """Đứt chuỗi → cảnh báo P1 vào hàng đợi của Owner (không tự sửa gì)."""
    out: dict[str, Any] = {}
    async with sessionmaker()() as db:
        orgs = (await db.execute(text("SELECT id FROM core.organizations"))).scalars().all()
        for org in orgs:
            report = await actionlog.verify_chain(db, org)
            out[str(org)] = {"ok": report.ok, "checked": report.checked, "broken_at": report.broken_at}
            await actionlog.record(db, org_id=org, actor_type="system", actor_id="system:worker",
                                   action="audit.chain_verified", result="ok" if report.ok else "failed",
                                   detail={"checked": report.checked, "broken_at": report.broken_at})
            # v0.1.69 (F104): chuỗi đã đứt thì đêm nào cũng đứt ở cùng một dòng — chỉ báo khi chưa có cảnh báo MỞ
            # (open/acknowledged) cho đúng dòng đứt đó; Owner xử lý xong (resolved/dismissed) mà vẫn đứt thì báo lại.
            if not report.ok and not (await db.execute(text("""
                    SELECT EXISTS (SELECT 1 FROM biz.alerts
                    WHERE org_id = :o AND alert_type = 'data_conflict' AND subject_type = 'action_log'
                      AND status IN ('open', 'acknowledged') AND evidence -> 0 ->> 'id' = CAST(:b AS text))"""),
                                                {"o": org, "b": report.broken_at})).scalar_one():
                code = (await db.execute(text("SELECT core.next_code('ALR')"))).scalar_one()
                await db.execute(text("""
                    INSERT INTO biz.alerts (org_id, code, alert_type, priority, recipient_user_id, subject_type,
                                            title, summary, evidence)
                    SELECT :o, :c, 'data_conflict', 'P1', u.id, 'action_log',
                           'Nhật ký hành động bị sửa ngoài hệ thống',
                           'Chuỗi băm đứt tại dòng ' || :b || '. Cần kiểm tra quyền truy cập CSDL.',
                           jsonb_build_array(jsonb_build_object('type', 'action_log', 'id', :b))
                    FROM core.users u JOIN core.user_roles ur ON ur.user_id = u.id
                    JOIN core.roles r ON r.id = ur.role_id AND r.code = 'owner'
                    WHERE u.org_id = :o LIMIT 1"""), {"o": org, "c": code, "b": report.broken_at})
        await db.commit()
    return out


async def partition_maintenance(ctx: dict[str, Any]) -> None:
    """`partman.run_maintenance()` tạo bảng phân vùng mới hằng tháng — là DDL, role `gh_app` (GH_DATABASE_URL,
    không superuser) không có quyền tạo bảng nên job này luôn chạy qua `GH_ADMIN_DATABASE_URL`."""
    async with admin_sessionmaker()() as db:
        # v0.1.40 (F-2): bảo trì chỉ tạo phân vùng mới, KHÔNG xoá tháng quá hạn — retention đưa về NULL trong cùng giao
        # dịch; chỉ `retention_sweep` 05:00 xoá (đúng giờ câu xác nhận nói với Owner, đếm đủ số tháng đã xoá).
        await retention.clear_partman_retention(db)
        await db.execute(text("SELECT partman.run_maintenance()"))
        await db.commit()
    # v0.1.40 (F-16): LEAKPROOF của similarity_op mất sau `genh import` (pg_restore không giữ) — đặt lại ở đây.
    try:
        async with admin_sessionmaker()() as db:
            await retention.ensure_leakproof(db)
            await db.commit()
    except Exception:  # noqa: BLE001 — chỉ ảnh hưởng tốc độ dò trùng tên
        log.warning("Không đặt lại được LEAKPROOF cho similarity_op", exc_info=True)


async def detect_identities(ctx: dict[str, Any]) -> dict[str, int]:
    """Dò cặp định danh có thể trùng (SĐT, tên gần giống, chung nhóm) → hàng chờ duyệt, không tự gộp."""
    out: dict[str, int] = {}
    async with sessionmaker()() as db:
        for org in (await db.execute(text("SELECT id FROM core.organizations"))).scalars().all():
            out[str(org)] = await identity.detect(db, org)
        await db.commit()
    return out


async def expire_sessions(ctx: dict[str, Any]) -> int:
    """Dọn `core.sessions` (PLAN §5.6 lỗi 🟡): xoá vĩnh viễn phiên hết hạn/thu hồi quá
    `GH_SESSION_PURGE_AFTER_DAYS` ngày (mặc định 30) — chạy hằng giờ."""
    async with sessionmaker()() as db:
        n = await retention.purge_sessions(db)
        await db.commit()
    return n


async def compact_notebooks(ctx: dict[str, Any]) -> int:
    """Nén hằng ngày các sổ tay có mục mới từ lần nén trước (mục ghim và luật cấm không bao giờ bị nén)."""
    n = 0
    async with sessionmaker()() as db:
        for org in (await db.execute(text("SELECT id FROM core.organizations"))).scalars().all():
            for nb in await notebook.due_for_daily(db, org):
                if await notebook.compact(db, nb, reason="daily"):
                    n += 1
        await db.commit()
    return n


async def purge_gen_conversations(ctx: dict[str, Any]) -> int:
    """Gen v1 (§9.4): xoá hội thoại Gen quá hạn lưu (mặc định 90 ngày, `settings->'gen'->'retention_days'`).
    v0.1.40: không còn cron riêng (nằm trong `retention_sweep`); giữ cho job enqueue cũ."""
    async with sessionmaker()() as db:
        return await retention.purge_gen(db)


async def purge_notifications(ctx: dict[str, Any]) -> int:
    """v0.1.27: hạn lưu chuông thông báo — đã đọc > 30 ngày, mọi thông báo > 90 ngày (`gh.notifications.purge_old`).
    v0.1.40: không còn cron riêng (nằm trong `retention_sweep`); giữ cho job enqueue cũ."""
    async with sessionmaker()() as db:
        return await retention.purge_notifications(db)


async def retention_sweep(ctx: dict[str, Any]) -> dict[str, Any]:
    """v0.1.40 (F-2): dọn dữ liệu quá hạn hằng ngày — xem `gh.retention.retention_sweep`."""
    return await retention.retention_sweep(ctx)


async def hub_token_expiry_scan(ctx: dict[str, Any]) -> int:
    """v0.1.26 (Đợt D1): token Gen-hub còn ≤ 14 ngày → chuông cho Owner (một lần mỗi token)."""
    async with sessionmaker()() as db:
        n = await hub_link.expiry_scan(db, ctx.get("redis_bus"))
        await db.commit()
    return n


async def hub_breaker_watch(ctx: dict[str, Any]) -> int:
    """v0.1.49 (F-83): Gen-hub không trả lời hơn 15 phút → sự cố `hub.breaker` + chuông Owner (một lần); hết sự cố
    thì tự đóng. Chỉ đọc Redis + ghi `ops.health_alerts`, không gọi mạng."""
    return await hub_link.breaker_watch(sessionmaker(), ctx["redis_bus"])


async def social_schedule(ctx: dict[str, Any]) -> int:
    """v0.1.29: lịch đọc mạng xã hội (TẮT mặc định; Owner bật từng tài khoản, vd 08:00/17:00) — mỗi phút."""
    async with sessionmaker()() as db:
        n = await social.schedule_tick(db, ctx["redis_bus"])
        await db.commit()
    return n


async def social_session_check(ctx: dict[str, Any]) -> int:
    """F-83: mỗi ngày 09:10 giờ VN kiểm phiên đăng nhập của từng tài khoản mạng xã hội (việc health, chỉ đọc)."""
    async with sessionmaker()() as db:
        n = await session_watch.daily_check(db, ctx["redis_bus"])
        await db.commit()
    return n


async def gen_briefing(ctx: dict[str, Any]) -> dict[str, Any]:
    """v0.1.41 (F-8b): Bản tin Gen 07:30 / 17:30 giờ VN cho Owner — xem `gh.gen.briefing.run_briefing`."""
    return await briefing.run_briefing(sessionmaker(), ctx["redis_bus"], ctx["model_router"])


async def gen_kho_release(ctx: dict[str, Any]) -> dict[str, Any]:
    """v0.1.50 (F-87): bản mới lên ⇒ Gen đề xuất ghi một Phiên vào Kho cho từng Owner (chỉ khi Gen-hub đã cấp quyền ghi
    Kho; mỗi (tổ chức, bản) một lần) — xem `gh.gen.kho_release.run`. Job chỉ ĐỀ XUẤT, không ghi Kho."""
    return await kho_release.run(sessionmaker(), ctx["redis_bus"])


async def gen_coach(ctx: dict[str, Any]) -> dict[str, Any]:
    """v0.1.54 (g1-api): chuông Gen hướng dẫn 09:05 / 11:05 / 14:05 giờ VN cho từng Owner — xem
    `gh.gen.coach.cron.run_coach`. Chỉ đọc tín hiệu + ghi chuông; không gọi model, không đẩy Telegram."""
    return await coach_cron.run_coach(sessionmaker(), ctx["redis_bus"])


async def telegram_flush(ctx: dict[str, Any]) -> dict[str, int]:
    """v0.1.44 (F-8c): gửi bản tin/nhắc việc đang chờ trong ops.telegram_outbox — mỗi phút (Telegram Bot API, một
    chiều, không qua bridge/Zalo). Lỗi cấu hình ⇒ sự cố telegram.failed (chuông Owner một lần)."""
    return await telegram.flush_outbox(sessionmaker(), redis=ctx.get("redis_bus"))


async def owner_flush(ctx: dict[str, Any]) -> dict[str, int]:
    """v0.1.72: gửi hàng đợi báo cáo chủ động của kênh quản trị (chỉ sự kiện đặc biệt) qua cửa `send_auto(owner_dm)`,
    mỗi phút."""
    s = get_settings()
    return await owner_reports.flush(sessionmaker(), EventBus(ctx["redis_bus"], s.stream_maxlen), ctx["redis_bus"])


_BIZ_JOBS = [*biz.jobs(), *BACKUP_JOBS, *TELEGRAM_JOBS]  # PLAN §5.6 — cùng mẫu CronJob; v0.1.64: + telegram_inbound


class WorkerSettings:
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
    # JSON thay pickle — Redis bị ghi bậy cũng không thành chạy mã trong worker (gh/jobcodec.py).
    job_serializer = staticmethod(jobcodec.dumps)
    job_deserializer = staticmethod(jobcodec.loads)
    on_startup = startup
    on_shutdown = shutdown
    functions = [verify_action_log, partition_maintenance, detect_identities, compact_notebooks, expire_sessions,
                 purge_gen_conversations, purge_notifications, retention_sweep, hub_token_expiry_scan,
                 hub_breaker_watch, social_schedule, social_session_check, gen_briefing, gen_kho_release,
                 gen_coach, telegram_flush, owner_flush, *(fn for fn, _ in _BIZ_JOBS), *BACKUP_FUNCTIONS]
    health_check_interval = 30
    job_timeout = JOB_TIMEOUT  # v0.1.40 (F-16): tường minh — `_cron` dùng cùng giá trị để nhận ra lần quá giờ
    # v0.1.36 (F-45): mọi giờ dưới đây là GIỜ VN (Asia/Ho_Chi_Minh). Job nặng theo ngày tránh 08:00–18:00 và cửa
    # sổ cập nhật genh 03:00 ±30' (02:30–03:30).
    timezone = WORKER_TZ
    cron_jobs = [
        _cron(verify_action_log, hour={4}, minute={30}),         # 04:30 giờ VN hằng ngày — kiểm chuỗi Action Log
        _cron(partition_maintenance, hour={4, 23}, minute={20}),  # 04:20 và 23:20 giờ VN — bảo trì pg_partman
        _cron(detect_identities, minute=set(range(0, 60, 10))),  # mỗi 10 phút
        _cron(compact_notebooks, hour={4}, minute={50}),         # 04:50 giờ VN hằng ngày — nén sổ tay
        _cron(expire_sessions, minute={20}),                     # mỗi giờ — dọn core.sessions (0014_v011_db)
        _cron(retention_sweep, hour={5}, minute={0}, timeout=1800),  # 05:00 giờ VN — dọn dữ liệu quá hạn (F-2)
        _cron(hub_token_expiry_scan, hour={8}, minute={50}),     # 08:50 giờ VN — nhắc token Gen-hub (nhẹ)
        _cron(hub_breaker_watch, minute=set(range(0, 60, 5))),   # mỗi 5 phút — Gen-hub im > 15 phút (F-83)
        _cron(social_schedule, minute=set(range(60))),           # mỗi phút — lịch đọc mạng xã hội (tắt mặc định)
        _cron(social_session_check, hour={9}, minute={10}),      # 09:10 giờ VN — kiểm phiên mạng xã hội (F-83)
        # 07:30 / 17:30 giờ VN — Bản tin Gen (F-8b); các lượt sau trong 3 giờ chỉ bù khi lỡ giờ (idempotent)
        _cron(gen_briefing, hour={7, 8, 9, 17, 18, 19}, minute={30}),
        # phút 7 và 37 mỗi giờ — Gen đề xuất ghi Phiên vào Kho khi máy chủ lên bản mới (F-87; mỗi bản một lần)
        _cron(gen_kho_release, minute={7, 37}),
        # 09:05 / 11:05 / 14:05 giờ VN — chuông Gen hướng dẫn (v0.1.54); chủ ý trong giờ làm việc để Sếp thấy chuông
        _cron(gen_coach, hour={9, 11, 14}, minute={5}),
        # mỗi phút — gửi hộp thư đi Telegram (bản tin + nhắc việc của Owner, F-8c); lượt ngắn
        _cron(telegram_flush, minute=set(range(60)), timeout=TELEGRAM_FLUSH_TIMEOUT),
        _cron(owner_flush, minute=set(range(60)), timeout=TELEGRAM_FLUSH_TIMEOUT),
        *(_cron(fn, **kw) for fn, kw in _BIZ_JOBS),              # biz + sao lưu: giữ NGUYÊN kw (kể cả timeout)
    ]


def run() -> None:
    from arq import run_worker

    run_worker(WorkerSettings)  # type: ignore[arg-type]
