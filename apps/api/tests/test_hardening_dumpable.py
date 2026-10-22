"""Gói G — `prctl(PR_SET_DUMPABLE, 0)` sớm ở tiến trình api và worker (agy cùng uid không đọc được /proc/<pid>/environ).

Mỗi phép thử chạy trong tiến trình con riêng: đặt dumpable=0 cho chính tiến trình pytest sẽ làm hỏng công cụ gỡ lỗi
và việc đọc /proc của các test khác.
"""

import ast
import os
import subprocess
import sys
from pathlib import Path

import pytest

API_DIR = Path(__file__).resolve().parents[1]
LINUX = sys.platform.startswith("linux")
ENV = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "GH_ENV": "test", "HOME": "/tmp",
       "GH_DATABASE_URL": "postgresql://x:y@127.0.0.1:1/z", "GH_ADMIN_DATABASE_URL": "postgresql://x:y@127.0.0.1:1/z"}


def _py(code: str, extra_env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, "-c", code], cwd=API_DIR, env={**ENV, **(extra_env or {})},
                          capture_output=True, text=True, timeout=120)


def test_set_not_dumpable_clears_the_flag() -> None:
    r = _py("from gh import hardening as h; a = h.is_dumpable(); ok = h.set_not_dumpable(); "
            "print(a, ok, h.is_dumpable())")
    assert r.returncode == 0, r.stderr
    if LINUX:
        assert r.stdout.split() == ["True", "True", "False"], r.stdout
    else:
        assert r.stdout.split() == ["None", "False", "None"], r.stdout     # nền khác: an toàn, không ném lỗi


def test_api_entrypoint_clears_dumpable_on_import() -> None:
    """Nhập `gh.main` (điểm vào tiến trình api) là đã đặt dumpable=0 — chưa cần chạy lifespan."""
    r = _py("import gh.main; from gh import hardening; print(hardening.is_dumpable())")
    assert r.returncode == 0, r.stderr[-800:]
    assert r.stdout.strip().splitlines()[-1] == ("False" if LINUX else "None"), r.stdout


def test_worker_startup_clears_dumpable_before_anything_else() -> None:
    """`startup` của worker (arq on_startup) gọi `set_not_dumpable()` ĐẦU TIÊN — trước logging, DB, Redis."""
    tree = ast.parse((API_DIR / "gh" / "worker.py").read_text(encoding="utf-8"))
    fn = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == "startup")
    body = [n for n in fn.body if not (isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant))]   # bỏ docstring
    first = body[0]
    assert isinstance(first, ast.Expr) and isinstance(first.value, ast.Call)
    assert isinstance(first.value.func, ast.Name) and first.value.func.id == "set_not_dumpable"
    import gh.worker as worker

    assert worker.WorkerSettings.on_startup is worker.startup        # arq thật gọi đúng hàm này khi worker lên


def test_other_process_of_same_uid_cannot_read_environ_of_a_non_dumpable_process() -> None:
    if not LINUX:
        return       # /proc/<pid>/environ chỉ có ở Linux — nền khác không có gì để kiểm
    secret = "GHFAKE-" + os.urandom(8).hex()
    code = ("import sys, time; from gh import hardening as h; h.set_not_dumpable(); "
            "print('ready', flush=True); sys.stdin.readline()")
    child = subprocess.Popen([sys.executable, "-c", code], cwd=API_DIR, env={**ENV, "GH_SECRET_PROBE": secret},
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    try:
        assert child.stdout is not None and child.stdout.readline().strip() == "ready"
        environ = Path(f"/proc/{child.pid}/environ")
        if os.geteuid() != 0:                       # root có CAP_SYS_PTRACE nên vẫn đọc được — không áp cho root
            with pytest.raises(PermissionError):
                environ.read_bytes()
    finally:
        assert child.stdin is not None
        child.stdin.write("\n")
        child.stdin.flush()
        child.wait(timeout=20)
