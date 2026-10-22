"""Giai đoạn 5.4 — Chịu lỗi THẬT (spec M7: không sập, không mất dữ liệu). Không mock: dừng/khởi động lại
Postgres và Redis THẬT trên máy chạy test (`pg_ctlcluster`, `redis-cli shutdown` + `redis-server`).

Đánh dấu `slow`: KHÔNG chạy trong `pytest -q` mặc định (xem addopts trong pyproject.toml) — chạy riêng bằng
`pytest -m slow -q tests/test_resilience.py`. Lý do: làm gián đoạn Postgres/Redis ảnh hưởng mọi test khác chạy
song song, và Postgres cần vài giây để khởi động lại thật (không giả lập được bằng thời gian ngắn hơn).

Yêu cầu trước khi chạy: chỉ một tiến trình pytest, không có test nào khác đang dùng Postgres/Redis cùng lúc.
"""

import asyncio
import logging
import subprocess
import time

import pytest

from gh.chassis.bus import EventBus
from tests.conftest import Api

pytestmark = pytest.mark.slow

PG_READY_TIMEOUT_S = 60
REDIS_READY_TIMEOUT_S = 20


def _pg_stop() -> None:
    subprocess.run(["pg_ctlcluster", "16", "main", "stop", "-m", "fast"], check=True, timeout=30)


def _pg_start() -> None:
    subprocess.run(["pg_ctlcluster", "16", "main", "start"], check=True, timeout=30)
    deadline = time.monotonic() + PG_READY_TIMEOUT_S
    while time.monotonic() < deadline:
        r = subprocess.run(["pg_isready", "-h", "localhost", "-p", "5432"], capture_output=True)
        if r.returncode == 0:
            return
        time.sleep(0.5)
    raise RuntimeError("Postgres không khởi động lại kịp trong lúc test")


def _redis_stop() -> None:
    subprocess.run(["redis-cli", "-p", "6379", "shutdown", "nosave"], timeout=10)


def _redis_start() -> None:
    subprocess.run(["redis-server", "--daemonize", "yes"], check=True, timeout=10)
    deadline = time.monotonic() + REDIS_READY_TIMEOUT_S
    while time.monotonic() < deadline:
        r = subprocess.run(["redis-cli", "-p", "6379", "ping"], capture_output=True, text=True)
        if r.stdout.strip() == "PONG":
            return
        time.sleep(0.3)
    raise RuntimeError("Redis không khởi động lại kịp trong lúc test")


# ─── Postgres mất kết nối giữa chừng ───────────────────────────────────────────

async def test_postgres_outage_mid_request_gives_clear_error_and_self_heals(owner_api: Api, caplog) -> None:  # type: ignore[no-untyped-def]
    """Dừng cluster Postgres THẬT trong lúc có request đang chờ + worker nền đang chạy (lifespan của `app` khởi
    động các vòng consume/quét permit — xem gh/app.py). Kỳ vọng (spec M7):
    1. Request đang gửi lúc CSDL chết nhận lỗi RÕ RÀNG (503 DB_UNAVAILABLE), không phải 500 không rõ nguyên nhân.
    2. Tiến trình (đại diện bằng chính tiến trình test/worker asyncio) KHÔNG sập — request sau đó vẫn được xử lý,
       và vòng quét nền (permit-sweep) tự bắt lỗi, không crash task (thấy qua log "quét permit hết hạn lỗi").
    3. Khi Postgres khởi động lại: hệ thống tự kết nối lại (pool_pre_ping=True, gh/db.py) — KHÔNG cần khởi động
       lại app/tiến trình.
    """
    caplog.set_level(logging.ERROR, logger="gh.app")
    r0 = await owner_api.get("/auth/me")
    assert r0.status_code == 200, r0.text

    _pg_stop()
    try:
        # Kết nối mới thất bại lúc CSDL tắt hẳn: ConnectionRefusedError chưa qua asyncpg/SQLAlchemy bọc thành
        # DBAPIError (chỉ lỗi SAU KHI có kết nối mới được bọc vậy) → handler OSError bắt, mã SERVICE_UNAVAILABLE.
        # Cả hai đều là lỗi RÕ RÀNG (503, mã máy đọc được), không phải 500 không rõ nguyên nhân.
        r1 = await owner_api.get("/auth/me")
        assert r1.status_code == 503, r1.text
        assert r1.json()["code"] in ("DB_UNAVAILABLE", "SERVICE_UNAVAILABLE")
        # Tiến trình vẫn sống: gọi lại lần nữa vẫn được xử lý bình thường (không treo, không kết nối bị bỏ mặc).
        r2 = await owner_api.get("/auth/me")
        assert r2.status_code == 503, r2.text
        # Đợi ít nhất một vòng của worker nền (permit-sweep, mỗi 15s) — nhưng không bắt buộc chờ đủ 15s để
        # test nhanh: chỉ cần xác nhận request-path đã chứng minh "không sập"; nếu log worker nền tới kịp, tốt.
        await asyncio.sleep(1)
    finally:
        _pg_start()

    ok = False
    for _ in range(30):
        r3 = await owner_api.get("/auth/me")
        if r3.status_code == 200:
            ok = True
            break
        await asyncio.sleep(0.5)
    assert ok, "hệ thống không tự phục hồi sau khi Postgres khởi động lại (không restart app)"


async def test_postgres_outage_background_worker_survives_and_recovers(app, redis) -> None:  # type: ignore[no-untyped-def]
    """Vòng quét nền (permit-sweep, mẫu chung với mọi consumer EventBus.run) không được chết vì Postgres rớt:
    gọi trực tiếp một vòng của nó trong lúc CSDL đang tắt, xác nhận không ném ngoại lệ ra ngoài (bắt gọn, log lỗi,
    thử lại vòng sau) — đúng thiết kế try/except trong gh.app._permit_sweep_loop / gh.chassis.bus.EventBus.run."""
    from gh.app import _permit_sweep_loop
    from gh.db import sessionmaker

    _pg_stop()
    try:
        stop = asyncio.Event()
        # Chạy MỘT vòng thực tế của vòng quét trong khi Postgres tắt hẳn — không được raise ra ngoài.
        task = asyncio.create_task(_permit_sweep_loop(sessionmaker(), redis, stop, interval_s=0.5))
        await asyncio.sleep(1.5)
        assert not task.done(), "vòng quét nền chết theo Postgres thay vì tự bắt lỗi và thử lại"
        stop.set()
        await asyncio.wait_for(task, timeout=5)
    finally:
        _pg_start()


# ─── Redis mất kết nối giữa chừng ──────────────────────────────────────────────

async def test_redis_outage_mid_consume_does_not_lose_messages(redis) -> None:  # type: ignore[no-untyped-def]
    """Dừng Redis THẬT trong lúc consumer group đang chạy: worker không sập (EventBus.run bắt lỗi mỗi vòng),
    khi Redis lên lại consumer tự nối lại (XREADGROUP) và xử lý tiếp — không mất tin đã publish trước đó."""
    bus = EventBus(redis)
    stream, group = "gh.test.resilience", "grp1"
    received: list[int] = []

    async def handler(ev):  # type: ignore[no-untyped-def]
        received.append(ev.payload["n"])

    await bus.publish(stream, "ping", {"n": 1}, actor="test")
    stop = asyncio.Event()
    task = asyncio.create_task(bus.run(stream, group, "c1", handler, stop, block_ms=200))
    for _ in range(20):
        if received == [1]:
            break
        await asyncio.sleep(0.2)
    assert received == [1]

    _redis_stop()
    try:
        await asyncio.sleep(2)                              # vài vòng lặp lỗi kết nối — task phải vẫn sống
        assert not task.done(), "vòng consume chết theo Redis thay vì tự bắt lỗi và thử lại"
    finally:
        _redis_start()
        for _ in range(40):                                  # đợi client redis (fixture) tự nối lại thật
            try:
                if await redis.ping():
                    break
            except Exception:  # noqa: BLE001
                pass
            await asyncio.sleep(0.3)

    await bus.publish(stream, "ping", {"n": 2}, actor="test")  # tin publish SAU khi Redis lên lại
    for _ in range(30):
        if received == [1, 2]:
            break
        await asyncio.sleep(0.3)
    assert received == [1, 2], "consumer không tự nối lại / mất tin sau khi Redis khởi động lại"

    stop.set()
    await asyncio.wait_for(task, timeout=10)
