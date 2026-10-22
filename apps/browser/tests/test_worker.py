"""Vòng worker (không cần trình duyệt): chữ ký, hạn, nonce một lần, khoá 1 việc/tài khoản, Dừng tất cả, nhịp tim;
giao thức khớp api; danh sách tên miền; proxy ra ngoài."""

import asyncio
import hashlib
import time
from pathlib import Path
from typing import Any

import orjson
import pytest
from redis.asyncio import Redis

from ghb import protocol
from ghb.adapters.facebook import parse_conversation, parse_notification
from ghb.config import decode_key
from ghb.egress import Egress
from ghb.guard import host_allowed, ip_forbidden, url_allowed
from ghb.worker import Worker
from tests.conftest import ACC, KEY, cfg, job, results


class FakeRunner:
    """Runner giả: ghi lại việc được chạy, chờ tới khi bị huỷ nếu `block`."""

    def __init__(self, redis: Redis, block: bool = False):
        self.redis, self.block, self.ran = redis, block, []
        from ghb.runner import Runner

        self._real = Runner(cfg(), redis, self._no_browser)

    @staticmethod
    async def _no_browser() -> Any:
        raise AssertionError("không mở trình duyệt trong test này")

    async def publish(self, j: dict[str, Any], typ: str, data: dict[str, Any] | None = None, state: Any = None) -> None:
        await self._real.publish(j, typ, data, state)

    async def run(self, j: dict[str, Any], cancel: asyncio.Event) -> None:
        self.ran.append(j["id"])
        if self.block:
            await cancel.wait()
            await self.publish(j, "halted")
        else:
            await self.publish(j, "done", {"items": []})


def signed(j: dict[str, Any], key: bytes = KEY) -> bytes:
    return orjson.dumps(protocol.sign(key, protocol.P_JOB, j))


# ─── giao thức ────────────────────────────────────────────────────────────────

def test_protocol_vectors_match_api() -> None:
    key = bytes(range(32))
    obj = {"a": 1, "b": "xin chào", "c": [1, 2, {"z": None}]}
    assert protocol.signature(key, "job", obj) == "4oQ_2B4CsAD9D_xEaU8z5sKWvWGfdTaVGxk-kk1UJlk"
    url = "https://www.facebook.com/permalink.php?story_fbid=1&comment_id=2"
    shared_notes = {"v": 1, "nonce": "00112233445566778899aabbccddeeff",
                    "job_id": "0190a000-0000-7000-8000-0000000000j1",
                    "org_id": "0190a000-0000-7000-8000-000000000001",
                    "account_id": "0190a000-0000-7000-8000-0000000000aa", "action": "reply_comment",
                    "target_url_sha256": hashlib.sha256(url.encode("utf-8")).hexdigest(),
                    "body_sha256": hashlib.sha256("Cảm ơn bạn!".encode()).hexdigest(), "iat": 1700000000,
                    "exp": 1700000300, "confirmed_by": "0190a000-0000-7000-8000-0000000000u1"}
    assert protocol.P_PERMIT == "permit" and protocol.PERMIT_NONCE_PREFIX == "gh:browser:permit:"
    assert protocol.signature(key, protocol.P_PERMIT, shared_notes) == "8yjIbFDVot8HRqGMFe3JMbTbPG9E5w_ajEISGa2sLTA"
    api = Path(__file__).resolve().parents[2] / "api" / "gh" / "social" / "protocol.py"
    if api.exists():   # trong repo: hai bản sao phải giống hệt (trừ docstring đầu tệp)
        mine = (Path(__file__).resolve().parents[1] / "ghb" / "protocol.py").read_text(encoding="utf-8")
        assert mine.split('"""', 2)[2] == api.read_text(encoding="utf-8").split('"""', 2)[2]
    assert decode_key(KEY.hex()) == KEY
    with pytest.raises(ValueError):
        decode_key("ngắn")


def test_guard_rules() -> None:
    d = ("facebook.com", "fbcdn.net")
    assert url_allowed("https://www.facebook.com/notifications", d)
    assert url_allowed("https://scontent.xx.fbcdn.net/a.jpg", d)
    assert url_allowed("about:blank", d) and url_allowed("data:image/png;base64,AA", d)
    for bad in ("http://www.facebook.com/", "https://facebook.com.evil.net/", "https://evil.net/?facebook.com",
                "https://www.facebook.com:8443/", "file:///etc/passwd", "https://10.0.0.5/", "ftp://facebook.com"):
        assert not url_allowed(bad, d), bad
    assert host_allowed("m.facebook.com", d) and not host_allowed("notfacebook.com", d)
    for ip in ("127.0.0.1", "10.1.2.3", "172.20.0.2", "192.168.1.1", "169.254.169.254", "::1", "fe80::1",
               "::ffff:10.0.0.1", "0.0.0.0", "224.0.0.1", "không-phải-ip"):
        assert ip_forbidden(ip), ip
    assert not ip_forbidden("157.240.1.35") and not ip_forbidden("2a03:2880:f10c:83:face:b00c:0:25de")


def test_parsers() -> None:
    n = parse_notification({"text": "Chị Lan đã thích ảnh của bạn.\n3 giờ", "href": "https://www.facebook.com/x",
                            "unread": 1})
    assert n == {"kind": "notification", "who": None, "text": "Chị Lan đã thích ảnh của bạn.", "time": "3 giờ",
                 "unread": True, "link": "https://www.facebook.com/x"}
    c = parse_conversation({"text": "Shop Mai\nĐang hoạt động\nAlo anh\n5 phút"})
    assert c is not None and (c["who"], c["text"], c["time"]) == ("Shop Mai", "Alo anh", "5 phút")
    assert parse_notification({"text": "  "}) is None


# ─── vòng worker ────────────────────────────────────────────────────────────────

async def test_accept_rejects_forged_expired_and_replayed_jobs(redis: Redis) -> None:
    w = Worker(cfg(), redis, FakeRunner(redis))  # type: ignore[arg-type]
    good = job("read", {"state": "x"})
    assert await w.accept(signed(good, key=b"k" * 32)) is None                 # sai khoá
    assert await w.accept(orjson.dumps({**protocol.sign(KEY, protocol.P_JOB, good), "kind": "login"})) is None
    assert await w.accept(signed({**good, "exp": int(time.time()) - 1})) is None   # hết hạn
    assert await w.accept(signed(good)) is not None
    assert await w.accept(signed(good)) is None                                # nonce dùng lại
    assert await w.accept(b"{not json") is None


async def test_one_job_per_account_and_halt(redis: Redis) -> None:
    fr = FakeRunner(redis, block=True)
    w = Worker(cfg(), redis, fr)  # type: ignore[arg-type]
    j1, j2 = job("read", {}), job("read", {})
    t1 = asyncio.create_task(w.execute(j1))
    await asyncio.sleep(0.2)
    assert await redis.get(protocol.LOCK_PREFIX + ACC) == j1["id"].encode()
    await w.execute(j2)                                                         # cùng tài khoản → BUSY
    # Lệnh dừng giả mạo bị bỏ; lệnh đúng chữ ký huỷ mọi việc đang chạy.
    assert w.on_control(orjson.dumps({"type": "halt", "sig": "sai"})) == "bad_sig"
    assert w.on_control(orjson.dumps(protocol.sign(KEY, protocol.P_CONTROL, {"type": "halt", "ts": 1}))) == "halt"
    await asyncio.wait_for(t1, timeout=5)
    assert await redis.get(protocol.LOCK_PREFIX + ACC) is None                  # khoá được nhả
    await redis.set(protocol.HALT_KEY, b"{}")
    await w.execute(job("read", {}))                                            # đang Dừng tất cả → không chạy
    kinds = [(r["job_id"], r["type"], r["data"].get("code")) for r in await results(redis)]
    assert (j2["id"], "failed", "BUSY") in kinds
    assert (j1["id"], "halted", None) in kinds
    assert [k for k in kinds if k[1] == "halted"].__len__() == 2
    assert fr.ran == [j1["id"]]
    await w.heartbeat_once()
    hb = orjson.loads(await redis.get(protocol.HEARTBEAT_KEY))
    assert hb["version"] and "running" in hb


async def test_queued_job_cancelled_by_api_never_runs(redis: Redis) -> None:
    """Việc GỬI còn nằm trong hàng đợi khi Owner rút đồng ý: pub/sub `cancel` bị bỏ qua (chưa có trong `running`)
    nhưng khoá CANCELLED_PREFIX api đặt vẫn chặn — worker nhận việc sau đó KHÔNG chạy (không gửi gì)."""
    fr = FakeRunner(redis)
    w = Worker(cfg(), redis, fr)  # type: ignore[arg-type]
    j = job("write", {})
    assert w.on_control(orjson.dumps(protocol.sign(KEY, protocol.P_CONTROL,
                                                   {"type": "cancel", "job_id": j["id"], "ts": 1}))) == "ignored"
    await redis.set(protocol.CANCELLED_PREFIX + j["id"], "1", ex=60)
    await w.execute(j)
    assert fr.ran == []
    kinds = [(r["job_id"], r["type"]) for r in await results(redis)]
    assert kinds == [(j["id"], "halted")]
    assert await redis.get(protocol.LOCK_PREFIX + ACC) is None


async def test_idle_closes_browser_only_when_no_job_running(redis: Redis) -> None:
    closed: list[float] = []

    async def close() -> None:
        closed.append(time.monotonic())

    fr = FakeRunner(redis, block=True)
    w = Worker(cfg(), redis, fr, idle_close=close, idle_close_s=0.0)  # type: ignore[arg-type]
    t = asyncio.create_task(w.execute(job("read", {})))
    await asyncio.sleep(0.2)
    assert await w.maybe_close_idle() is False and not closed                  # đang có việc → giữ trình duyệt
    w.on_control(orjson.dumps(protocol.sign(KEY, protocol.P_CONTROL, {"type": "halt", "ts": 1})))
    await asyncio.wait_for(t, timeout=5)
    assert await w.maybe_close_idle() is True and len(closed) == 1            # rảnh → đóng Chromium
    w2 = Worker(cfg(), redis, fr, idle_close=close)  # type: ignore[arg-type]
    assert await w2.maybe_close_idle() is False                                # chưa rảnh đủ lâu


async def test_run_loop_consumes_signed_jobs(redis: Redis) -> None:
    fr = FakeRunner(redis)
    w = Worker(cfg(), redis, fr)  # type: ignore[arg-type]
    stop = asyncio.Event()
    task = asyncio.create_task(w.run(stop))
    j = job("read", {})
    await redis.xadd(protocol.JOBS_STREAM, {"m": signed(j)})
    await redis.xadd(protocol.JOBS_STREAM, {"m": signed(job("read", {}), key=b"z" * 32)})   # giả mạo
    for _ in range(50):
        if len(await results(redis)) >= 2:
            break
        await asyncio.sleep(0.1)
    stop.set()
    await asyncio.wait_for(task, timeout=10)
    assert [r["type"] for r in await results(redis)] == ["started", "done"]
    assert fr.ran == [j["id"]]


# ─── proxy ra ngoài ─────────────────────────────────────────────────────────────

async def _connect(port: int, target: str) -> tuple[bytes, asyncio.StreamReader, asyncio.StreamWriter]:
    r, w = await asyncio.open_connection("127.0.0.1", port)
    w.write(f"CONNECT {target} HTTP/1.1\r\nHost: {target}\r\n\r\n".encode())
    await w.drain()
    head = await asyncio.wait_for(r.readuntil(b"\r\n\r\n"), timeout=5)
    return head, r, w


async def test_egress_allow_list_and_private_ip_block() -> None:
    async def echo(r: asyncio.StreamReader, w: asyncio.StreamWriter) -> None:
        w.write(await r.read(100))
        await w.drain()
        w.close()

    upstream = await asyncio.start_server(echo, "127.0.0.1", 0)
    up_port = upstream.sockets[0].getsockname()[1]

    async def to_local(host: str, port: int) -> list[str]:
        return ["127.0.0.1"]

    strict = Egress(("facebook.com",), resolver=to_local)                       # như chạy thật: cấm IP nội bộ
    lab = Egress(("facebook.com",), resolver=to_local, allow_private=True, upstream_port=up_port)
    s1 = await asyncio.start_server(strict.handle, "127.0.0.1", 0)
    s2 = await asyncio.start_server(lab.handle, "127.0.0.1", 0)
    p1, p2 = s1.sockets[0].getsockname()[1], s2.sockets[0].getsockname()[1]
    try:
        head, _r, w = await _connect(p1, "evil.example.com:443")
        assert b" 403 " in head
        w.close()
        head, _r, w = await _connect(p1, "www.facebook.com:80")
        assert b" 403 " in head
        w.close()
        head, _r, w = await _connect(p1, "www.facebook.com:443")               # phân giải ra 127.0.0.1 → chặn
        assert b" 403 " in head
        w.close()
        r, w = await asyncio.open_connection("127.0.0.1", p1)
        w.write(b"GET http://www.facebook.com/ HTTP/1.1\r\nHost: www.facebook.com\r\n\r\n")
        await w.drain()
        assert b" 405 " in await asyncio.wait_for(r.readuntil(b"\r\n\r\n"), timeout=5)
        w.close()
        head, r, w = await _connect(p2, "www.facebook.com:443")                # đường cho phép → đường ống thông
        assert b" 200 " in head
        w.write(b"xin-chao")
        await w.drain()
        assert await asyncio.wait_for(r.read(100), timeout=5) == b"xin-chao"
        w.close()
    finally:
        for s in (s1, s2, upstream):
            s.close()
