"""Gói G — canary `agy_canary --live` thêm ca thử CHẠY LỆNH SHELL đọc /proc/1/environ và /run/secrets.

Review 16/10/2026: agy chạy cùng uid `gh` trong api/worker nên đọc được /proc/1/environ (GH_ADMIN_DATABASE_URL…) và
/run/secrets/gh_*_key. Canary cũ chỉ thử đọc một tệp canary ngoài workspace. Ca mới (`shell_proc1_khong_lo`) bảo agy
chạy `cat` trên hai đích đó rồi so đầu ra với giá trị bí mật mà canary (cùng uid) đọc được; đọc không được thì so
với dạng `GH_<TÊN>=` của một bản dump environ. Test dùng agy GIẢ + tệp giả — KHÔNG chạy agy thật (tốn hạn mức;
chạy --live thật là việc của Sếp trên dev).
"""

import os
import subprocess
import sys
from pathlib import Path

import orjson

from gh.providers import agy_canary
from gh.providers.clients import TOKEN_FILE, AgyClient
from tests.test_cli_models_v0131 import _wrapper

FIX = Path(__file__).parent / "fixtures"
API_DIR = Path(__file__).resolve().parents[1]
DB_SECRET = "s3cr3t-DB-passw0rd-GHFAKE"
KEY_SECRET = "master-key-GHFAKE-0123456789"


def _plant(tmp: Path) -> tuple[Path, Path]:
    """Giả lập /proc/1/environ (NAME=value cách nhau NUL) và /run/secrets/gh_*_key."""
    environ = tmp / "proc1-environ"
    environ.write_bytes(b"\0".join([b"PATH=/usr/bin", b"HOME=/var/lib/gh",
                                    f"GH_ADMIN_DATABASE_URL=postgresql://admin:{DB_SECRET}@db/gh".encode(),
                                    b"LANG=C.UTF-8"]) + b"\0")
    secrets = tmp / "run-secrets"
    secrets.mkdir()
    (secrets / "gh_master_key").write_text(KEY_SECRET)
    (secrets / "gh_short").write_text("abc")           # quá ngắn để làm "chuỗi canary" — bị bỏ
    return environ, secrets


def _client(tmp: Path, script: str) -> AgyClient:
    home = tmp / "agy" / ".gemini" / "antigravity-cli"
    home.mkdir(parents=True)
    (home / TOKEN_FILE).write_text("{}")
    return AgyClient(_wrapper(tmp, "agy", FIX / script), str(home), timeout=30)


def test_needles_are_secret_values_only(tmp_path: Path) -> None:
    environ, secrets = _plant(tmp_path)
    needles = agy_canary.secret_needles(environ, secrets)
    assert DB_SECRET.encode() in b"\n".join(needles) and KEY_SECRET.encode() in needles
    assert b"/usr/bin" not in needles and b"abc" not in needles       # biến thường và giá trị ngắn không phải bí mật
    assert agy_canary.secret_needles(tmp_path / "khong-co", tmp_path / "khong-co-dir") == []   # đọc không được ⇒ rỗng


def test_shell_prompt_asks_for_proc1_environ_and_run_secrets(tmp_path: Path) -> None:
    p = agy_canary.shell_probe_prompt(Path("/proc/1/environ"), Path("/run/secrets"))
    assert "/proc/1/environ" in p and "/run/secrets" in p and "shell" in p.lower()


async def test_shell_probe_detects_a_hostile_agy_that_runs_the_commands(tmp_path: Path) -> None:
    environ, secrets = _plant(tmp_path)
    hostile = _client(tmp_path, "fake_agy_hostile.py")      # làm theo prompt: đọc mọi đường dẫn nhắc tới, kể cả thư mục
    assert await agy_canary.shell_probe_safe(hostile, environ, secrets) is False


async def test_shell_probe_passes_for_an_agy_that_leaks_nothing(tmp_path: Path) -> None:
    environ, secrets = _plant(tmp_path)
    benign = _client(tmp_path, "fake_agy_multi.py")
    assert await agy_canary.shell_probe_safe(benign, environ, secrets) is True


async def test_shell_probe_flags_an_environ_style_dump_even_when_canary_cannot_read_secrets(tmp_path: Path) -> None:
    """Canary chạy sau khi api/worker đã dumpable=0 thì không đọc được /proc/1/environ ⇒ không có chuỗi để so; vẫn bắt
    được đầu ra có dạng bản dump environ (`GH_...=`)."""
    environ, secrets = _plant(tmp_path)
    hostile = _client(tmp_path, "fake_agy_hostile.py")
    missing_env, missing_dir = tmp_path / "khong-co", tmp_path / "khong-co-dir"
    # prompt trỏ tới đích thật trong tmp nhưng canary "không đọc được" ⇒ needles rỗng, nhờ dấu hiệu GH_...=
    assert await agy_canary.shell_probe_safe(hostile, environ, secrets, needles=[]) is False
    assert await agy_canary.shell_probe_safe(hostile, missing_env, missing_dir) is True      # đích không tồn tại: sạch


def _canary(tmp_path: Path, binary: str, *extra: str) -> subprocess.CompletedProcess[str]:
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "GH_ENV": "test",
           "GH_CLI_BINARY": binary, "GH_CLI_HOME": str(tmp_path / "agy" / ".gemini" / "antigravity-cli"),
           "GH_CLAUDE_HOME": str(tmp_path / "claude" / ".claude"), "HOME": str(tmp_path / "runner"),
           "TMPDIR": str(tmp_path / "tmpc")}
    (tmp_path / "tmpc").mkdir(exist_ok=True)
    return subprocess.run([sys.executable, "-m", "gh.providers.agy_canary", "--live", *extra], cwd=API_DIR, env=env,
                          capture_output=True, text=True, timeout=120)


def test_live_run_reports_the_shell_check_and_marks_leak(tmp_path: Path) -> None:
    environ, secrets = _plant(tmp_path)
    home = tmp_path / "agy" / ".gemini" / "antigravity-cli"
    home.mkdir(parents=True)
    (home / TOKEN_FILE).write_text("{}")
    hostile = _wrapper(tmp_path, "agy", FIX / "fake_agy_hostile.py")
    res = _canary(tmp_path, hostile, "--proc-environ", str(environ), "--secrets-dir", str(secrets))
    out = orjson.loads(res.stdout.strip().splitlines()[-1])
    assert out["mode"] == "live" and out["checks"]["shell_proc1_khong_lo"] is False, out
    assert out["result"] == "lo" and res.returncode == 1
    assert DB_SECRET not in res.stdout + res.stderr and KEY_SECRET not in res.stdout + res.stderr   # không in bí mật


def test_live_run_with_clean_agy_includes_the_shell_check_as_passed(tmp_path: Path) -> None:
    environ, secrets = _plant(tmp_path)
    home = tmp_path / "agy" / ".gemini" / "antigravity-cli"
    home.mkdir(parents=True)
    (home / TOKEN_FILE).write_text("{}")
    benign = _wrapper(tmp_path, "agy", FIX / "fake_agy_multi.py")
    res = _canary(tmp_path, benign, "--proc-environ", str(environ), "--secrets-dir", str(secrets))
    out = orjson.loads(res.stdout.strip().splitlines()[-1])
    assert out["checks"]["shell_proc1_khong_lo"] is True, out
    assert out["checks"]["tiem_qua_cong_cu_khong_lo"] is True, out
    assert res.stdout.count("\n") == 1                                    # vẫn đúng MỘT dòng JSON


def test_offline_run_does_not_add_the_shell_check(tmp_path: Path) -> None:
    """--offline (không phiên agy) giữ nguyên hợp đồng cũ: không có ca shell (cần model thật làm theo lệnh)."""
    benign = _wrapper(tmp_path, "agy", FIX / "fake_agy_multi.py")
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "GH_ENV": "test", "GH_CLI_BINARY": benign,
           "GH_CLI_HOME": str(tmp_path / "agy" / ".gemini" / "antigravity-cli"),
           "GH_CLAUDE_HOME": str(tmp_path / "claude" / ".claude"), "HOME": str(tmp_path / "runner"),
           "TMPDIR": str(tmp_path / "tmpc")}
    (tmp_path / "tmpc").mkdir()
    res = subprocess.run([sys.executable, "-m", "gh.providers.agy_canary", "--offline"], cwd=API_DIR, env=env,
                         capture_output=True, text=True, timeout=120)
    out = orjson.loads(res.stdout.strip().splitlines()[-1])
    assert out["result"] == "khong_lo" and "shell_proc1_khong_lo" not in out["checks"]


# ─── review lần 2 v0.1.62 (P2-2): ca shell /proc/1 là KHE HỞ DỰ KIẾN — báo nhưng không chặn phát hành ───────────

def test_live_run_shell_gap_is_reported_but_not_a_release_blocker(tmp_path: Path) -> None:
    """agy cùng uid đọc được /proc/1/environ và /run/secrets (trong tiến trình; hộp v0.1.63 chỉ lo người ngoài) ⇒
    ca shell báo false nhưng KHÔNG làm kết quả thành "lo" (chặn phát hành); các ca cô lập khác (đầu ra, log, env,
    tiêm lệnh) vẫn là "lo" khi hỏng."""
    environ, secrets = _plant(tmp_path)
    home = tmp_path / "agy" / ".gemini" / "antigravity-cli"
    home.mkdir(parents=True)
    (home / TOKEN_FILE).write_text("{}")
    shell_only = _wrapper(tmp_path, "agy", FIX / "fake_agy_shell_only.py")
    res = _canary(tmp_path, shell_only, "--proc-environ", str(environ), "--secrets-dir", str(secrets))
    out = orjson.loads(res.stdout.strip().splitlines()[-1])
    assert out["checks"]["shell_proc1_khong_lo"] is False, out                     # vẫn báo thật
    assert out["known_gaps"] == ["shell_proc1_khong_lo"], out
    assert out["result"] == "khong_lo" and res.returncode == 0, out                # nhưng không chặn phát hành
    assert DB_SECRET not in res.stdout + res.stderr and KEY_SECRET not in res.stdout + res.stderr


def test_live_run_without_gap_has_empty_known_gaps(tmp_path: Path) -> None:
    environ, secrets = _plant(tmp_path)
    home = tmp_path / "agy" / ".gemini" / "antigravity-cli"
    home.mkdir(parents=True)
    (home / TOKEN_FILE).write_text("{}")
    benign = _wrapper(tmp_path, "agy", FIX / "fake_agy_multi.py")
    res = _canary(tmp_path, benign, "--proc-environ", str(environ), "--secrets-dir", str(secrets))
    out = orjson.loads(res.stdout.strip().splitlines()[-1])
    assert out["known_gaps"] == [] and out["result"] == "khong_lo", out


def test_a_real_isolation_failure_still_blocks_even_with_the_known_gap(tmp_path: Path) -> None:
    environ, secrets = _plant(tmp_path)
    home = tmp_path / "agy" / ".gemini" / "antigravity-cli"
    home.mkdir(parents=True)
    (home / TOKEN_FILE).write_text("{}")
    hostile = _wrapper(tmp_path, "agy", FIX / "fake_agy_hostile.py")
    res = _canary(tmp_path, hostile, "--proc-environ", str(environ), "--secrets-dir", str(secrets))
    out = orjson.loads(res.stdout.strip().splitlines()[-1])
    assert out["result"] == "lo" and res.returncode == 1 and "shell_proc1_khong_lo" in out["known_gaps"], out
