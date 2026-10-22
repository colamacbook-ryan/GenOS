"""Review lần 2 gói sửa v0.1.63 — P2-1: tiến trình nền thoát `killpg` (setsid) sống qua các lượt trong hộp.

Đơn vị (giả lập /proc + hàm kill, KHÔNG bao giờ gọi kill(-1) thật trên máy chạy test): `gh.agybox.reaper` đóng băng mọi
tiến trình, giết tiến trình agy để lại, trả lại tiến trình `docker exec`; xoá /tmp, /dev/shm không theo liên kết; server
dọn sau MỌI lượt (generate, models, `agy --version`) và không bao giờ dọn chen giữa một lượt. Kiểm thật trong container
hộp: docs/releases/v0.1.63.md (mục kiểm gói sửa review lần 2)."""

import asyncio
import json
import os
import signal
from pathlib import Path
from typing import Any

import httpx
import pytest

from gh.agybox import protocol, reaper, server
from tests.test_agybox_v0163 import KEY, URL, _body, box  # noqa: F401, F811 — fixture dùng chung

# ─── reaper: đóng băng → giết tiến trình sót lại / trả lại tiến trình docker exec ─────────────────────────────


def _fake_proc(root: Path, rows: list[tuple[int, str, str, int]]) -> str:
    for pid, comm, state, ppid in rows:
        d = root / str(pid)
        d.mkdir(parents=True)
        (d / "stat").write_text(f"{pid} ({comm}) {state} {ppid} {pid} {pid} 0 -1 4194560 0 0 0 0\n")
    (root / "self").mkdir()                              # mục không phải số: bỏ qua
    (root / "99").mkdir()                                # không có stat (tiến trình vừa thoát): bỏ qua
    return str(root)


def test_reap_strays_stops_everything_first_then_kills_strays_and_resumes_exec(tmp_path: Path) -> None:
    proc = _fake_proc(tmp_path, [
        (1, "docker-init", "S", 0),                      # tini — không đụng
        (7, "python", "R", 1),                           # chính server — không đụng
        (20, "sleep", "S", 1),                           # setsid nohup sleep 600 & — tini nhận nuôi
        (21, "sh", "S", 20),                             # cháu của tiến trình sót lại
        (22, "agy", "S", 7),                             # con trực tiếp của server còn sót
        (23, "evil) S 0 (x", "S", 1),                    # tên lừa bộ đọc stat (giả ppid 0)
        (30, "python", "S", 0),                          # healthcheck (docker exec) — trả lại
        (31, "sh", "S", 30),                             # con của tiến trình docker exec — trả lại
        (40, "sleep", "Z", 1),                           # zombie: tini thu hồi, không gửi gì
    ])
    calls: list[tuple[int, int]] = []

    def kill(pid: int, sig: int) -> None:
        calls.append((pid, sig))

    assert reaper.reap_strays(self_pid=7, kill=kill, proc=proc) == 4
    assert calls[0] == (-1, signal.SIGSTOP)              # đóng băng TRƯỚC mọi thao tác khác
    rest = dict(calls[1:])
    assert {p for p, s in rest.items() if s == signal.SIGKILL} == {20, 21, 22, 23}
    assert {p for p, s in rest.items() if s == signal.SIGCONT} == {30, 31}
    assert not {1, 7, 40, 99} & rest.keys()


def test_reap_strays_no_other_process(tmp_path: Path) -> None:
    calls: list[tuple[int, int]] = []

    def kill(pid: int, sig: int) -> None:
        calls.append((pid, sig))
        if pid == -1:
            raise ProcessLookupError

    assert reaper.reap_strays(self_pid=7, kill=kill, proc=_fake_proc(tmp_path, [(1, "init", "S", 0)])) == 0
    assert calls == [(-1, signal.SIGSTOP)]


def test_reap_strays_tolerates_vanished_and_foreign_processes(tmp_path: Path) -> None:
    proc = _fake_proc(tmp_path, [(1, "init", "S", 0), (7, "py", "R", 1), (20, "a", "S", 1), (21, "b", "S", 1)])

    def kill(pid: int, sig: int) -> None:
        if pid == 20:
            raise ProcessLookupError                     # thoát giữa chừng
        if pid == 21:
            raise PermissionError                        # uid khác

    assert reaper.reap_strays(self_pid=7, kill=kill, proc=proc) == 0


def test_wipe_dir_removes_everything_without_following_symlinks(tmp_path: Path) -> None:
    outside = tmp_path / "ngoai"
    outside.mkdir()
    (outside / "giu.txt").write_text("không được xoá")
    target = tmp_path / "tmp"
    (target / "gh-agy-home-x" / ".gemini").mkdir(parents=True)
    (target / "gh-agy-home-x" / ".gemini" / "settings.json").write_text("{}")
    (target / "gh-agy-home-x" / "lienket").symlink_to(outside, target_is_directory=True)
    (target / "tep").write_text("x")
    (target / ".an").write_text("x")
    (target / "lk-thu-muc").symlink_to(outside, target_is_directory=True)
    (target / "lk-tep").symlink_to(outside / "giu.txt")
    (target / "lk-gay").symlink_to(tmp_path / "khong-co")
    os.mkfifo(target / "ong")
    assert reaper.wipe_dir(target) == 0
    assert target.is_dir() and list(target.iterdir()) == []
    assert (outside / "giu.txt").read_text() == "không được xoá"
    assert reaper.wipe_dir(tmp_path / "khong-co") == 0  # thư mục không có (vd /dev/shm vắng): không lỗi


def test_clean_box_wipes_dirs_even_when_reaping_fails(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    a, b = tmp_path / "tmp", tmp_path / "shm"
    for d in (a, b):
        d.mkdir()
        (d / "f").write_text("x")

    def boom(pid: int, sig: int) -> None:
        raise RuntimeError("không ngờ")

    reaper.clean_box(kill=boom, dirs=(str(a), str(b)), proc=str(tmp_path / "proc"))
    assert list(a.iterdir()) == [] and list(b.iterdir()) == []
    assert "không dọn được tiến trình" in caplog.text


def test_reap_allowed_only_as_container_main_process() -> None:
    assert reaper.reap_allowed(True, pid=1, ppid=0)                 # server là PID 1 (không init)
    assert reaper.reap_allowed(True, pid=7, ppid=1)                 # con trực tiếp của tini (init: true)
    assert not reaper.reap_allowed(True, pid=4242, ppid=4000)       # chạy tay trên máy dev
    assert not reaper.reap_allowed(False, pid=7, ppid=1)            # không bật GH_AGYBOX_REAP


def test_config_reap_flag_and_allowed_env() -> None:
    assert server.Config(key_file="k").reap is False
    assert server.load_config({"GH_AGYBOX_KEY_FILE": "k"}).reap is False
    assert server.load_config({"GH_AGYBOX_KEY_FILE": "k", "GH_AGYBOX_REAP": "1"}).reap is True
    env = {"GH_AGYBOX_KEY_FILE": "k", "GH_AGYBOX_REAP": "1", "GH_MASTER_KEY_FILE": "m"}
    server.scrub_environ(env)
    assert sorted(env) == ["GH_AGYBOX_KEY_FILE", "GH_AGYBOX_REAP"]


def test_main_disables_reap_outside_container(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """main() trên máy dev (không phải tiến trình chính của container) ⇒ không bao giờ bật dọn kill(-1)."""
    keyfile = tmp_path / "k"
    keyfile.write_text("AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8=")
    monkeypatch.setenv("GH_AGYBOX_KEY_FILE", str(keyfile))
    monkeypatch.setenv("GH_AGYBOX_REAP", "1")
    seen: dict[str, Any] = {}
    monkeypatch.setattr(server, "create_app", lambda cfg: seen.setdefault("cfg", cfg))
    import uvicorn
    monkeypatch.setattr(uvicorn, "run", lambda *a, **k: None)
    monkeypatch.setattr(reaper, "reap_allowed", lambda enabled, **k: False)
    monkeypatch.setattr(server, "scrub_environ", lambda env: [])     # không xoá GH_* thật của tiến trình pytest
    server.main()
    assert seen["cfg"].reap is False


# ─── server: dọn sau MỌI lượt, không bao giờ chen giữa lượt ───────────────────────────────────────────────────

class Counter:
    def __init__(self, fail: bool = False):
        self.n, self.fail = 0, fail

    def __call__(self) -> None:
        self.n += 1
        if self.fail:
            raise RuntimeError("dọn lỗi")


def _app(b: Any, cleaner: Counter) -> httpx.ASGITransport:
    return httpx.ASGITransport(app=server.create_app(b.cfg, clock=b.clock, cleaner=cleaner))


async def _post(t: httpx.ASGITransport, b: Any, path: str, body: bytes) -> httpx.Response:
    h = protocol.sign_request(KEY, "POST", path, body, b.clock())
    async with httpx.AsyncClient(transport=t, base_url=URL, timeout=30) as c:
        return await c.post(path, content=body, headers=h)


async def test_cleaner_runs_after_generate_models_error_and_version(box) -> None:  # noqa: F811
    cl = Counter()
    t = _app(box, cl)
    assert (await _post(t, box, "/v1/generate", _body(request_id="g-1"))).status_code == 200
    assert cl.n == 1
    assert (await _post(t, box, "/v1/models", json.dumps({"request_id": "m-1", "session": json.loads(
        _body())["session"]}).encode())).status_code == 200
    assert cl.n == 2
    r = await _post(t, box, "/v1/generate", _body(request_id="g-2", prompt="BOOM"))   # agy lỗi: vẫn dọn
    assert r.status_code >= 400 and cl.n == 3
    async with httpx.AsyncClient(transport=t, base_url=URL) as c:
        assert (await c.get("/healthz")).json()["agy_version"] == "1.2.9"            # `agy --version` ⇒ dọn
        assert cl.n == 4
        await c.get("/healthz")                                                      # phiên bản trong bộ đệm
        assert cl.n == 4
    bad = await _post(t, box, "/v1/generate", _body(request_id="g-3", prompt=""))   # bị từ chối trước khi chạy agy
    assert bad.status_code == 400 and cl.n == 4


async def test_cleaner_error_does_not_break_the_answer(box) -> None:  # noqa: F811
    cl = Counter(fail=True)
    r = await _post(_app(box, cl), box, "/v1/generate", _body(request_id="g-1"))
    assert r.status_code == 200 and cl.n == 1


async def test_healthz_never_runs_agy_or_cleaner_during_a_turn(box) -> None:  # noqa: F811
    """/healthz giữa lượt: không chạy `agy --version` (chen lượt), không dọn (giết agy của lượt) — trả bản gần nhất."""
    cl = Counter()
    t = _app(box, cl)
    slow = asyncio.create_task(_post(t, box, "/v1/generate", _body(request_id="s-1", prompt="SLEEP", timeout_s=1.5)))
    await asyncio.sleep(0.5)
    async with httpx.AsyncClient(transport=t, base_url=URL) as c:
        h = await asyncio.wait_for(c.get("/healthz"), 1.0)
        assert h.status_code == 200 and h.json()["agy_version"] is None and cl.n == 0
        r = await slow
        assert r.json()["error"]["code"] == protocol.E_TIMEOUT and cl.n == 1          # hết giờ: vẫn dọn
        assert (await c.get("/healthz")).json()["agy_version"] == "1.2.9" and cl.n == 2


async def test_cleaner_runs_when_client_disconnects(box) -> None:  # noqa: F811
    cl = Counter()
    t = _app(box, cl)
    slow = asyncio.create_task(_post(t, box, "/v1/generate", _body(request_id="d-1", prompt="SLEEP")))
    await asyncio.sleep(0.5)
    slow.cancel()
    await asyncio.gather(slow, return_exceptions=True)
    for _ in range(50):
        if cl.n:
            break
        await asyncio.sleep(0.1)
    assert cl.n == 1


def test_default_cleaner_is_clean_box_only_when_reap(box, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    calls: list[str] = []
    monkeypatch.setattr(reaper, "clean_box", lambda: calls.append("clean"))
    box.cfg.reap = False
    server.create_app(box.cfg, clock=box.clock)
    box.cfg.reap = True
    app = server.create_app(box.cfg, clock=box.clock)

    async def go() -> None:
        r = await _post(httpx.ASGITransport(app=app), box, "/v1/generate", _body(request_id="c-1"))
        assert r.status_code == 200

    asyncio.run(go())
    assert calls == ["clean"]
