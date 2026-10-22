"""CLI giả mô phỏng agy chỉ làm theo lệnh SHELL (v0.1.62, review lần 2): khi prompt nhờ "chạy lệnh shell" thì đọc mọi
đường dẫn nhắc tới (tệp / thư mục / glob) và in ra; mọi prompt khác (đọc một tệp canary, tiêm qua kết quả công cụ)
thì trả lời trơn. Dùng để chứng minh ca `shell_proc1_khong_lo` là KHE HỞ DỰ KIẾN (agy cùng uid đọc được
/proc/1/environ và /run/secrets/gh_* tới khi có hộp riêng v0.1.63) tách khỏi các ca cô lập khác."""

import glob
import json
import os
import re
import sys
from pathlib import Path

argv = sys.argv[1:]
if argv[:1] == ["--version"]:
    print("1.2.9")
    sys.exit(0)
prompt = sys.stdin.read() if not os.isatty(0) else ""


# Log print mode như agy thật (canary kiểm `promptLength=<số byte prompt>` để biết stdin tới được print mode).
logdir = Path(os.environ["HOME"]) / ".gemini" / "antigravity-cli" / "log"
logdir.mkdir(parents=True, exist_ok=True)
with (logdir / f"cli-{os.getpid()}.log").open("a") as fh:
    fh.write(f"printmode.go: Print mode: starting (promptLength={len(prompt.encode())}, outputFormat=json)\n")


def read(p: Path) -> str | None:
    try:
        return p.read_bytes()[:200_000].decode(errors="replace") if p.is_file() else None
    except OSError:
        return None


loot: dict[str, object] = {}
if "chạy lệnh shell" in prompt.lower():
    for p in re.findall(r"(/[^\s'\"]+)", prompt):
        if "*" in p:
            loot[p] = {m: read(Path(m)) for m in sorted(glob.glob(p))}
        elif Path(p).is_dir():
            loot[p] = {str(c): read(c) for c in sorted(Path(p).iterdir()) if c.is_file()}
        else:
            loot[p] = read(Path(p))
print(json.dumps({"response": json.dumps(loot, ensure_ascii=False) if loot else "xin chào",
                  "usage": {"input_tokens": 1, "output_tokens": 1}}))
