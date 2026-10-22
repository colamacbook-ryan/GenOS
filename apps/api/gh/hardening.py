"""Vá rẻ chống đọc bí mật qua /proc (gói G, review 16/10/2026).

Antigravity CLI (`agy`) chạy cùng uid `gh` với api/worker (xem `gh.providers.clients.AgyClient`) nên một agy bị prompt
độc điều khiển đọc được `/proc/1/environ` (biến môi trường của tiến trình api/worker, vd GH_ADMIN_DATABASE_URL) và
`/proc/<pid>/mem`. `prctl(PR_SET_DUMPABLE, 0)` đặt tiến trình "không dump được": nhân Linux đổi chủ các tệp
`/proc/<pid>/*` của nó thành root và từ chối ptrace/đọc `environ`, `mem`, `maps` với tiến trình cùng uid không có
CAP_SYS_PTRACE. Cờ được con cháu `fork` thừa hưởng nhưng `execve` (vd chạy `agy`) đặt lại — agy vẫn chạy bình
thường, chỉ tiến trình api/worker là không bị đọc.

KHÔNG thay cho cô lập thật: từ v0.1.63 việc có người ngoài chạy agy trong hộp riêng (`agy-box`), nhưng vai core
(Gen của Sếp, agent nội bộ) vẫn chạy agy trong tiến trình api/worker (Nợ #45) nên phần dưới vẫn còn với các
vai đó; đây chỉ thu hẹp mặt phơi bày. Phần CÒN HỞ (nói thật):
- `/run/secrets/gh_*` đọc được bằng uid `gh` (quyền tệp theo uid) — agy cùng uid đọc được;
- tiến trình con do api/worker sinh KHÔNG thừa hưởng cờ: `execve` đặt lại dumpable, nên HEALTHCHECK của api (15 giây)
  và của worker (`arq --check`, 30 giây) chạy bằng uid `gh` mang đủ biến môi trường (vd GH_ADMIN_DATABASE_URL) và
  `environ` của chúng đọc được trong khoảnh khắc đó;
- `pg_dump`/`pg_restore` nay chỉ nhận môi trường tối thiểu (`gh.backup.pg_child_env`: PATH, PGPASSWORD, LC_ALL/LANG) —
  nhưng PGPASSWORD (mật khẩu quản trị CSDL) vẫn nằm trong `environ` của chúng suốt lúc chạy.
Canary `agy_canary --live` ca `shell_proc1_khong_lo` vì vậy dự kiến báo "lo" tới hộp riêng (báo trong `known_gaps`,
không chặn phát hành). Chỉ có hiệu lực trên Linux; nền khác hoặc lỗi gọi ⇒ trả False, không ném lỗi.
"""

import ctypes
import logging
import sys

log = logging.getLogger("gh.hardening")

PR_SET_DUMPABLE = 4
PR_GET_DUMPABLE = 3


def set_not_dumpable() -> bool:
    """`prctl(PR_SET_DUMPABLE, 0)`. True khi đã đặt được (Linux); False ở nền khác / khi lệnh gọi lỗi."""
    if not sys.platform.startswith("linux"):
        return False
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        return bool(libc.prctl(PR_SET_DUMPABLE, 0, 0, 0, 0) == 0)
    except (OSError, AttributeError):   # không có libc/prctl (musl lạ, bản dựng tĩnh…) — vá phụ, không chặn khởi động
        log.warning("Không đặt được PR_SET_DUMPABLE=0 (bỏ qua)")
        return False


def is_dumpable() -> bool | None:
    """Trạng thái hiện tại (cho test / chẩn đoán); None khi không đọc được (nền không phải Linux)."""
    if not sys.platform.startswith("linux"):
        return None
    try:
        v = ctypes.CDLL(None, use_errno=True).prctl(PR_GET_DUMPABLE, 0, 0, 0, 0)
    except (OSError, AttributeError):
        return None
    return None if v < 0 else v == 1
