"""Đọc/ghi AN TOÀN hộp thư `run/` dùng chung với genh trên máy chủ (bind mount tại `Settings.host_link_dir`).

v0.1.45: `run/` là 2770 nhóm 10001 (chỉ genh và api ghi được), nhưng api và genh vẫn không tin nhau hoàn toàn:

- GHI (`write_request`): tệp tạm TÊN NGẪU NHIÊN tạo bằng `tempfile.mkstemp` (O_EXCL — không đi theo symlink ai đó cài
  sẵn ở tên tạm đoán được như `<tệp>.tmp`), `fchmod 0644` (genh — chủ thư mục, uid khác — đọc qua bit "khác"),
  ghi + fsync rồi `os.replace` vào tên đích. `os.replace` thay chính ĐƯỜNG DẪN đích (kể cả khi đích là symlink) chứ
  không ghi theo symlink; vẫn xoá symlink đích trước cho rõ ràng.
- ĐỌC (`read_state`): mở thư mục bằng O_DIRECTORY|O_NOFOLLOW rồi mở tệp tương đối với fd đó (dir_fd) bằng
  O_NOFOLLOW|O_NONBLOCK (không treo ở FIFO), fstat: tệp thường, đúng 1 liên kết, ≤ max_bytes, chủ sở hữu là chủ `run/`
  (= người chạy genh, nhìn từ trong container — đúng cả Docker rootless). Sai bất kỳ điều nào ⇒ None + cảnh báo log
  (API trả "không rõ", không 500). Tệp YÊU CẦU do chính api ghi (`run/request/*.json`) đọc với `allow_self=True`.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import stat
import tempfile
from pathlib import Path
from typing import Any

log = logging.getLogger("gh.hostlink")

MAX_STATE_BYTES = 64 * 1024


def run_root() -> Path:
    """Gốc hộp thư `run/` (Settings.host_link_dir)."""
    from gh.config import get_settings

    return Path(get_settings().host_link_dir)


def write_request(dirpath: Path, name: str, payload: dict[str, Any], *, mode: int = 0o644) -> None:
    """Ghi nguyên tử `dirpath/name` = JSON(payload): mkstemp (O_EXCL) → fchmod → ghi + fsync → os.replace.

    Lỗi ⇒ xoá tệp tạm rồi ném lại (không rò `*.tmp`)."""
    if "/" in name or name in ("", ".", ".."):
        raise ValueError("tên tệp hộp thư không hợp lệ")
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    fd, tmp = tempfile.mkstemp(dir=dirpath, prefix=f".{name}.", suffix=".tmp")
    try:
        try:
            os.fchmod(fd, mode)
            view = memoryview(data)
            while view:
                n = os.write(fd, view)
                view = view[n:]
            os.fsync(fd)
        finally:
            os.close(fd)
        target = Path(dirpath) / name
        # os.replace thay đường dẫn (không theo symlink) — xoá symlink đích trước cho chắc, bỏ qua nếu đã biến mất.
        if target.is_symlink():
            with contextlib.suppress(FileNotFoundError):
                target.unlink()
        os.replace(tmp, target)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def _owner_uid(root: Path | None) -> int | None:
    try:
        return os.stat(root if root is not None else run_root()).st_uid
    except OSError:
        return None


def read_state(path: Path, max_bytes: int = MAX_STATE_BYTES, *, allow_self: bool = False,
               root: Path | None = None) -> dict[str, Any] | None:
    """Đọc một tệp JSON trong hộp thư AN TOÀN. None khi thiếu, không an toàn (symlink, FIFO, thiết bị, nhiều hard
    link, quá lớn, sai chủ) hoặc JSON hỏng/không phải object.

    Chủ hợp lệ: chủ thư mục gốc `run/` (genh) — `root` mặc định Settings.host_link_dir; uid 0 (genh chạy bằng
    `sudo` ghi lại trạng thái — chỉ root mới tạo được tệp do root sở hữu nên không nới mô hình tin cậy);
    `allow_self` thêm uid của chính tiến trình api (tệp yêu cầu api tự ghi)."""
    path = Path(path)
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    try:
        dfd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | nofollow)
    except FileNotFoundError:
        return None
    except OSError:
        log.warning("Hộp thư run/: thư mục %s không an toàn để đọc", path.parent.name)
        return None
    try:
        try:
            fd = os.open(path.name, os.O_RDONLY | nofollow | getattr(os, "O_NONBLOCK", 0), dir_fd=dfd)
        except FileNotFoundError:
            return None
        except OSError:
            log.warning("Hộp thư run/: bỏ qua %s (liên kết mềm hoặc không mở được)", path.name)
            return None
    finally:
        os.close(dfd)
    with os.fdopen(fd, "rb") as f:
        st = os.fstat(f.fileno())
        reason = None
        if not stat.S_ISREG(st.st_mode):
            reason = "không phải tệp thường"
        elif st.st_nlink != 1:
            reason = "nhiều hard link"
        elif st.st_size > max_bytes:
            reason = "quá lớn"
        else:
            owners = {_owner_uid(root), 0}
            if allow_self:
                owners.add(os.geteuid())
            if st.st_uid not in owners:
                reason = "sai chủ sở hữu"
        if reason is not None:
            log.warning("Hộp thư run/: bỏ qua %s (%s)", path.name, reason)
            return None
        raw = f.read(max_bytes + 1)
    if len(raw) > max_bytes:
        log.warning("Hộp thư run/: bỏ qua %s (quá lớn)", path.name)
        return None
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
    return data if isinstance(data, dict) else None
