"""Claude Code CLI giả (v0.1.31), mô phỏng hành vi đo thật trên claude 2.1.285/2.1.286:

- `claude auth login --claudeai` (trong pty): in "Opening browser to sign in…", link trong hyperlink OSC 8 kết thúc
  bằng BEL, rồi "Paste code here if prompted > "; đọc mã `c/<tên>` → ghi `$CLAUDE_CONFIG_DIR/.credentials.json`
  ({claudeAiOauth: accessToken, refreshToken, expiresAt (ms), subscriptionType}) và `oauthAccount` vào `.claude.json`.
- `claude auth status --json`: {loggedIn, authMethod: "claude.ai", email, subscriptionType}; thoát 1 khi chưa đăng nhập.
- `claude -p … --model X --output-format json --tools ""` (prompt qua stdin): JSON một dòng {type: result, is_error,
  result, usage, api_error_status}. Model ngoài bí danh → is_error + 404 "There's an issue with the selected model".
  Thiếu `--tools ""` (công cụ chưa tắt), `--safe-mode`/`--strict-mcp-config`, hoặc lời nhắn hệ thống nằm trên dòng
  lệnh (phải qua `--system-prompt-file`, tệp 0600) → lỗi, để test bắt được nếu client quên.
- v0.1.39: `FAKE_CLAUDE_CONFIRM=1` — sau khi nhận mã in "Login successful. Press Enter to continue…" và CHỈ ghi
  `.credentials.json` sau khi nhận Enter (bước cuối của CLI thật). `FAKE_CLAUDE_FAIL=1` — in lại mã trong câu lỗi rồi
  thoát 1 (CLI thoát sớm).
"""

import json
import os
import select
import sys
import termios
import time
import tty
from pathlib import Path

CONF = Path(os.environ["CLAUDE_CONFIG_DIR"])
CRED = CONF / ".credentials.json"
STATE = CONF / ".claude.json"
URL = "https://claude.com/cai/oauth/authorize?code=true&client_id=fake&response_type=code&state=CLAUDE"
ALIASES = {"haiku", "sonnet", "opus", "fable"}


def email_of() -> str | None:
    try:
        if not json.loads(CRED.read_text()).get("claudeAiOauth", {}).get("accessToken"):
            return None
        return str(json.loads(STATE.read_text())["oauthAccount"]["emailAddress"])
    except (OSError, ValueError, KeyError):
        return None


args = sys.argv[1:]
if args[:1] == ["--version"]:
    print("2.1.285 (Claude Code)")
    sys.exit(0)
if args[:3] == ["auth", "status", "--json"]:
    who = email_of()
    print(json.dumps({"loggedIn": who is not None, "authMethod": "claude.ai" if who else "none", "email": who,
                      "subscriptionType": "max" if who else None}, indent=2))
    sys.exit(0 if who else 1)

if args[:1] == ["-p"]:
    prompt = sys.stdin.read()
    model = next((a.split("=", 1)[1] for a in args if a.startswith("--model=")), None) or \
        (args[args.index("--model") + 1] if "--model" in args else "sonnet")
    # v0.1.32: `--effort` như claude 2.1.285 thật (đo 2026-10-01): mức lạ → cảnh báo trên stderr rồi bỏ qua.
    effort = next((a.split("=", 1)[1] for a in args if a.startswith("--effort=")), None) or \
        (args[args.index("--effort") + 1] if "--effort" in args else None)
    if effort and effort not in ("low", "medium", "high", "xhigh", "max"):
        print(f"Warning: Unknown --effort value '{effort}' — ignoring it and using the default effort. Valid values: "
              "low, medium, high, xhigh, max.", file=sys.stderr)
        effort = None
    tools_off = "--tools" in args and args[args.index("--tools") + 1:] == [""]
    # Review v0.1.31: hook/CLAUDE.md của thư mục cấu hình chỉ tắt bằng --safe-mode; lời nhắn hệ thống qua tệp.
    hardened = "--safe-mode" in args and "--strict-mcp-config" in args and "--system-prompt" not in args
    if "--system-prompt-file" in args:
        sp = Path(args[args.index("--system-prompt-file") + 1])
        hardened = hardened and sp.read_text() != "" and (sp.stat().st_mode & 0o077) == 0

    def result(text: str, *, error: bool = False, status: int | None = None) -> None:
        print(json.dumps({"type": "result", "subtype": "success", "is_error": error, "result": text,
                          "api_error_status": status, "usage": {"input_tokens": len(prompt), "output_tokens": 2}}))
        sys.exit(1 if error else 0)

    who = email_of()
    if who is None:
        result("Not logged in · Please run /login", error=True, status=401)
    if not tools_off:
        result("tools were not disabled", error=True)
    if not hardened:
        result("settings/hooks not disabled or system prompt on argv", error=True)
    if model not in ALIASES:
        result(f"There's an issue with the selected model ({model}). It may not exist or you may not have access to "
               "it. Run --model to pick a different model.", error=True, status=404)
    result(f"whoami:{who}|{model}" + (f"|{effort}" if effort else ""))

if args[:2] == ["auth", "login"]:
    def out(s: str) -> None:
        os.write(1, s.encode())

    tty.setraw(0)
    out(f"Opening browser to sign in…\r\nIf the browser didn't open, visit: \x1b]8;;{URL}\x07\x1b[94m{URL}\x1b[39m"
        "\x1b]8;;\x07\r\nPaste code here if prompted > ")
    data = b""
    while b"\r" not in data:
        r, _, _ = select.select([0], [], [], 30)
        if not r:
            sys.exit(3)
        data += os.read(0, 1024)
    code = data.split(b"\r")[0].decode().strip()
    if os.environ.get("FAKE_CLAUDE_FAIL") == "1":
        # v0.1.39: CLI từ chối mã và THOÁT (in lại mã như CLI thật hay làm) — app phải che mã khỏi log/thông báo.
        out(f"\r\nOAuth error: Invalid code {code}\r\n")
        sys.exit(1)
    if os.environ.get("FAKE_CLAUDE_CONFIRM") == "1":
        # v0.1.39 (F-77): bước cuối như claude thật — báo thành công rồi CHỜ Enter; chỉ ghi tệp phiên sau Enter.
        out("\r\nLogin successful. Press Enter to continue…\r\n")
        rest = data.split(b"\r", 1)[1]
        while b"\r" not in rest:
            r, _, _ = select.select([0], [], [], 30)
            if not r:
                sys.exit(4)
            rest += os.read(0, 1024)
    name = code.split("/", 1)[-1] or "boss"
    CONF.mkdir(parents=True, exist_ok=True)
    state = json.loads(STATE.read_text()) if STATE.exists() else {}
    state["oauthAccount"] = {"emailAddress": f"{name}@example.vn", "organizationName": "Cá nhân"}
    STATE.write_text(json.dumps(state))
    CRED.write_text(json.dumps({"claudeAiOauth": {
        "accessToken": f"sk-ant-oat-{name}", "refreshToken": f"sk-ant-ort-{name}",
        "expiresAt": int((time.time() - 60) * 1000),   # token ngắn hạn ĐÃ quá hạn nhưng còn refresh → vẫn dùng được
        "scopes": ["user:inference"], "subscriptionType": "max"}}))
    out("\r\nLogin successful.\r\n")
    termios.tcflush(0, termios.TCIFLUSH)
    sys.exit(0)

print("fake claude: unsupported args", args, file=sys.stderr)
sys.exit(2)
