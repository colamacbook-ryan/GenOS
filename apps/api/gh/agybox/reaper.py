"""Dọn hộp sau MỖI lượt agy (review lần 2 v0.1.63, P2-1) — chỉ stdlib, chỉ chạy trong container `agy-box`.

Vì sao: `AgyClient` giết nhóm tiến trình của agy (`killpg`), nhưng một lệnh agy bị lừa chạy kiểu
`setsid nohup sh -c '…' &` tự lập session riêng ⇒ thoát nhóm, được tini (PID 1) nhận nuôi và sống qua các lượt, cùng
uid `gh` với agy của lượt sau: canh /tmp/gh-agy-home-* để cài settings.json/mcp, đọc /proc/<agy>/fd/0 (prompt của khách
khác), ghi /proc/<agy>/fd/1 (chèn chữ vào trả lời). Tệp để lại trong /tmp, /dev/shm cũng sống qua lượt.

Cách dọn (`reap_strays` rồi `wipe_dir`), khi KHÔNG còn lượt nào chạy (server giữ khoá hộp):
1. `kill(-1, SIGSTOP)` — ngữ nghĩa Linux (đã kiểm thật trong container hộp, uid gh 10001): tín hiệu tới MỌI tiến trình
   người gọi có quyền gửi trong PID namespace, TRỪ chính tiến trình gọi (cả nhóm luồng) và PID 1; kernel duyệt danh sách
   tiến trình dưới khoá nên tiến trình đang `fork` không lọt (tín hiệu "nhiều tiến trình" cũng tới tiến trình con mới).
   ⇒ mọi tiến trình sót lại bị ĐÓNG BĂNG một lần, không kịp fork/ghi tệp/thoát khỏi vòng dọn.
2. Duyệt /proc: tiến trình có chuỗi cha đi tới tiến trình "ppid 0" khác PID 1 là tiến trình do `docker exec` đưa vào
   (healthcheck, `genh doctor`, kịch bản e2e — cha của nó ở ngoài namespace) ⇒ SIGCONT trả lại. Mọi tiến trình khác
   (con cháu của tini/server: tiến trình agy để lại) ⇒ SIGKILL. Tiến trình trong hộp không thể tự đổi cha thành ppid 0.
   Gọi `kill(-1, SIGKILL)` trần thì giết luôn healthcheck đang chờ /healthz (đã thấy khi kiểm: tiến trình `docker exec`
   cùng uid gh bị giết).
3. `wipe_dir("/tmp")`, `wipe_dir("/dev/shm")`: xoá mọi mục bên trong, không theo liên kết tượng trưng.

Không bao giờ chạy ngoài hộp: server chỉ bật khi `GH_AGYBOX_REAP=1` VÀ là tiến trình chính của container
(pid 1 hoặc con trực tiếp của init) — chạy nhầm trên máy dev thì `kill(-1, …)` đóng băng mọi tiến trình người dùng."""

import contextlib
import logging
import os
import shutil
import signal
from collections.abc import Callable
from pathlib import Path

log = logging.getLogger("agybox")

Kill = Callable[[int, int], None]
#: Thư mục ghi được trong hộp (root chỉ đọc): tmpfs /tmp (HOME tạm của lượt) và /dev/shm của Docker.
WIPE_DIRS = ("/tmp", "/dev/shm")  # noqa: S108 — chính là thư mục cần dọn


def proc_table(proc: str = "/proc") -> dict[int, tuple[int, str]]:
    """pid → (ppid, trạng thái) từ /proc/<pid>/stat (đọc được cả với tiến trình non-dumpable)."""
    out: dict[int, tuple[int, str]] = {}
    try:
        names = os.listdir(proc)
    except OSError:
        return out
    for name in names:
        if not name.isdigit():
            continue
        try:
            with open(os.path.join(proc, name, "stat"), "rb") as fh:
                raw = fh.read().decode(errors="replace")
            fields = raw[raw.rindex(")") + 2:].split()
            out[int(name)] = (int(fields[1]), fields[0])
        except (OSError, ValueError, IndexError):
            continue
    return out


def _injected(pid: int, table: dict[int, tuple[int, str]]) -> bool:
    """Tiến trình thuộc cây do `docker exec` đưa vào (tổ tiên có ppid 0 mà không phải PID 1)?"""
    seen: set[int] = set()
    while pid not in seen:
        seen.add(pid)
        if pid == 1 or pid not in table:
            return False
        ppid = table[pid][0]
        if ppid == 0:
            return True
        pid = ppid
    return False


def reap_strays(*, self_pid: int | None = None, kill: Kill = os.kill, proc: str = "/proc") -> int:
    """Đóng băng mọi tiến trình khác (kill(-1, SIGSTOP)), giết tiến trình agy để lại, trả lại tiến trình `docker exec`.
    Trả số tiến trình đã giết."""
    me = os.getpid() if self_pid is None else self_pid
    try:
        kill(-1, signal.SIGSTOP)
    except ProcessLookupError:
        return 0                                   # không còn tiến trình nào khác ngoài server và PID 1
    except PermissionError:
        pass
    killed = 0
    table = proc_table(proc)
    for pid, (_ppid, state) in sorted(table.items()):
        if pid in (1, me) or state == "Z":
            continue
        sig = signal.SIGCONT if _injected(pid, table) else signal.SIGKILL
        try:
            kill(pid, sig)
        except (ProcessLookupError, PermissionError):
            continue
        killed += sig == signal.SIGKILL
    return killed


def wipe_dir(path: str | Path) -> int:
    """Xoá mọi mục TRONG `path` (giữ chính thư mục). Liên kết tượng trưng bị xoá như tệp, không bao giờ đi theo;
    thư mục con xoá bằng `shutil.rmtree` (bản dùng fd, chống đổi thư mục thành liên kết giữa chừng). Trả số mục lỗi."""
    errors = 0
    try:
        entries = list(os.scandir(path))
    except OSError:
        return 0
    for entry in entries:
        try:
            if entry.is_dir(follow_symlinks=False):
                shutil.rmtree(entry.path)
            else:
                os.unlink(entry.path)
        except FileNotFoundError:
            continue
        except OSError:
            errors += 1
    return errors


def clean_box(*, kill: Kill = os.kill, dirs: tuple[str, ...] = WIPE_DIRS, proc: str = "/proc") -> None:
    """Một lần dọn đầy đủ sau lượt: giết tiến trình sót lại rồi xoá /tmp, /dev/shm. Không ném lỗi (chỉ ghi nhật ký)."""
    try:
        killed = reap_strays(kill=kill, proc=proc)
    except Exception:  # noqa: BLE001 — dọn không được thì ghi nhật ký, không làm hỏng trả lời của lượt
        log.exception("agy-box: không dọn được tiến trình sót lại")
        killed = -1
    errors = 0
    for d in dirs:
        with contextlib.suppress(Exception):
            errors += wipe_dir(d)
    if killed or errors:
        log.warning("agy-box: dọn sau lượt — giết %d tiến trình sót lại, %d mục không xoá được", killed, errors)


def reap_allowed(enabled: bool, *, pid: int | None = None, ppid: int | None = None) -> bool:
    """Chỉ dọn khi được bật (GH_AGYBOX_REAP=1) VÀ là tiến trình chính của container (pid 1, hoặc con của init)."""
    pid = os.getpid() if pid is None else pid
    ppid = os.getppid() if ppid is None else ppid
    return enabled and (pid == 1 or ppid == 1)
