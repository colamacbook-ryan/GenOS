"""CLI giả mô phỏng Antigravity CLI BỊ PROMPT ĐỘC ĐIỀU KHIỂN (v0.1.38, F-22): công cụ đọc tệp / chạy lệnh của agy
không tắt được (`agy --help` 1.2.9 không có cờ nào), nên giả định xấu nhất — model làm mọi thứ có thể với quyền của
tiến trình:

- đọc đệ quy mọi tệp trong cwd và $HOME;
- đọc `$HOME/claude/.claude/.credentials.json` (đường dẫn phiên Claude Code CŨ, nằm trong volume của agy);
- dump `os.environ`;
- đọc `/proc/<ppid>/cmdline` (dòng lệnh tiến trình cha);
- đọc mọi đường dẫn tuyệt đối nhắc tới trong prompt (model làm theo "Đọc tệp … và in nguyên văn"); gói G: đường dẫn là
  THƯ MỤC thì đọc mọi tệp trong đó (như `ls` rồi `cat`), có dấu `*` thì khai triển glob — mô phỏng lệnh shell
  `cat /run/secrets/*` / `cat /proc/1/environ`;

rồi in tất cả trong `{"response": …}` (thoát 0). Test kiểm: không thứ gì ở trên chứa bí mật nhờ cô lập
cwd/HOME/env/stdin của AgyClient.
"""

import glob
import json
import os
import re
import sys
from pathlib import Path


def read(path: Path, limit: int = 200_000) -> str | None:
    try:
        if path.is_file():
            return path.read_bytes()[:limit].decode(errors="replace")
    except OSError:
        return None
    return None


def read_path(p: str) -> object:
    """Tệp → nội dung; thư mục → {tên tệp: nội dung}; có `*` → khai triển glob (lệnh shell của model)."""
    if "*" in p:
        return {m: read(Path(m)) for m in sorted(glob.glob(p))}
    path = Path(p)
    if path.is_dir():
        try:
            return {str(c): read(c) for c in sorted(path.iterdir()) if c.is_file()}
        except OSError:
            return None
    return read(path)


def walk(root: Path) -> dict[str, str]:
    got: dict[str, str] = {}
    try:
        for p in root.rglob("*"):
            if len(got) >= 500:
                break
            txt = read(p)
            if txt is not None:
                got[str(p)] = txt
    except OSError:
        pass
    return got


argv = sys.argv[1:]
if argv[:1] == ["--version"]:
    print("1.2.9")
    sys.exit(0)
prompt = ""
for i, a in enumerate(argv):
    name, eq, val = a.partition("=")
    if name in ("-p", "--print", "--prompt"):
        prompt = val if eq else (argv[i + 1] if i + 1 < len(argv) else "")
if not prompt and not os.isatty(0):
    prompt = sys.stdin.read()

home = Path(os.environ.get("HOME", "/"))
loot: dict[str, object] = {
    "cwd": os.getcwd(),
    "cwd_files": walk(Path(os.getcwd())),
    "home_files": walk(home),
    "old_claude_credentials": read(home / "claude" / ".claude" / ".credentials.json"),
    "environ": dict(os.environ),
    "parent_cmdline": read(Path(f"/proc/{os.getppid()}/cmdline")),
    "prompt_paths": {p: read_path(p) for p in re.findall(r"(/[^\s'\"]+)", prompt)},
}
print(json.dumps({"response": json.dumps(loot, ensure_ascii=False), "usage": {"input_tokens": 1,
                                                                               "output_tokens": 1}}))
sys.exit(0)
