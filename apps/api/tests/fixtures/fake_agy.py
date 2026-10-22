"""CLI giả mô phỏng Antigravity CLI 1.2.9 đo thật trên pty (test_cli_login.py):
hỏi terminal (DA) và CHỜ trả lời trước khi vẽ; link dài bị ngắt dòng, bản đầy đủ nằm trong hyperlink OSC 8;
đọc mã xác thực rồi ghi tệp phiên.

v0.1.38 (F-22): không có -p/--print/--prompt mà stdin không phải tty → print mode như agy 1.2.9 thật: đọc prompt từ
stdin, nhận `--model x` lẫn `--model=x`, `--effort=x`, bỏ qua `--disable-slash-commands`; ghi $HOME/agy-calls.log
(`cwd`, `via_stdin`, `argv`)."""

import base64
import json
import os
import select
import sys
import termios
import tty
from pathlib import Path

URL = "https://accounts.google.com/o/oauth2/auth?" + "&".join(f"p{i}=" + "x" * 40 for i in range(15)) + "&state=END"


def out(s: str) -> None:
    os.write(1, s.encode())


def read_until(pred, timeout: float) -> bytes:
    data = b""
    while not pred(data):
        r, _, _ = select.select([0], [], [], timeout)
        if not r:
            sys.exit(3)  # không ai trả lời: CLI thật treo; ở đây thoát để test thấy lỗi nhanh
        data += os.read(0, 1024)
    return data


def print_mode(argv: list[str]) -> None:
    opts = {"-p": None, "--print": None, "--prompt": None, "--model": "", "--effort": ""}
    i = 0
    while i < len(argv):
        name, eq, val = argv[i].partition("=")
        if name in opts or name in ("--output-format", "--print-timeout"):
            if not eq:
                i += 1
                val = argv[i] if i < len(argv) else ""
            opts[name] = val
        elif argv[i] != "--disable-slash-commands" and argv[i].startswith("-"):
            print(f"flag provided but not defined: {name}", file=sys.stderr)
            sys.exit(2)
        i += 1
    prompt = opts["-p"] or opts["--print"] or opts["--prompt"]
    via_stdin = prompt is None
    if via_stdin:
        if os.isatty(0):
            return
        prompt = sys.stdin.read()
    with (Path(os.environ["HOME"]) / "agy-calls.log").open("a") as fh:
        fh.write(json.dumps({"model": opts["--model"], "effort": opts["--effort"], "cwd": os.getcwd(),
                             "via_stdin": via_stdin, "prompt_len": len(prompt or ""), "argv": argv}) + "\n")
    token = Path(os.environ["HOME"]) / ".gemini" / "antigravity-cli" / "antigravity-oauth-token"
    if not token.exists():
        print(json.dumps({"status": "ERROR", "error": "authentication failed or timed out"}))
        sys.exit(1)
    print(json.dumps({"response": "ok", "usage": {"input_tokens": 1, "output_tokens": 1}}))
    sys.exit(0)


print_mode(sys.argv[1:])
tty.setraw(0)
out("\x1b[>c\x1b_Ga=q,f=32,s=1,v=1,i=31;AAAAAA==\x1b\\\x1b[c")
read_until(lambda d: b"c" in d and b"\x1b[?" in d, 5)
out(
    "Welcome to the Antigravity CLI. You are currently not signed in.\r\nSelect login method:\r\n"
    " > 1. Google OAuth\r\n2. Use a Google Cloud project\r\n"
)
read_until(lambda d: b"\r" in d, 10)
wrapped = "\r\n".join(URL[i : i + 200] for i in range(0, len(URL), 200))
out(
    f"Open the URL below in your browser:\r\n{wrapped}\r\n"
    f"\x1b]8;;{URL}\x1b\\Click here to authenticate\x1b]8;;\x1b\\\r\n"
    "After authenticating, copy the code displayed in the browser and paste it below:\r\n"
)
code = read_until(lambda d: b"\r" in d, 30).split(b"\r")[0].decode().strip()
claims = base64.urlsafe_b64encode(json.dumps({"email": "boss@example.vn"}).encode()).decode().rstrip("=")
home = Path(os.environ["HOME"]) / ".gemini" / "antigravity-cli"
home.mkdir(parents=True, exist_ok=True)
(home / "antigravity-oauth-token").write_text(
    json.dumps({"access_token": f"tok-{code}", "id_token": f"h.{claims}.s", "expiry": 4102444800})
)
out("Signed in.\r\n")
termios.tcflush(0, termios.TCIFLUSH)
select.select([0], [], [], 30)
