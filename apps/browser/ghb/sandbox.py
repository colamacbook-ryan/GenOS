"""Sandbox của Chromium (F-85): chế độ, cờ khởi chạy và phép dò THẬT qua /proc.

Playwright tự thêm `--no-sandbox` khi `chromium_sandbox=False` (mặc định) — nên phải bật `chromium_sandbox=True`
thì Chromium mới dùng vùng cách ly của chính nó (user namespace + seccomp-bpf), không cần root hay `chrome-sandbox`
setuid (trái `no-new-privileges`). Không tin cờ mà DÒ: tiến trình trình duyệt không có `--no-sandbox`, và có tiến
trình render đang chạy seccomp (`Seccomp: 2`) trong user namespace khác. Kết quả đi theo nhịp tim cho api.

`python -m ghb.sandbox --probe` mở Chromium, in JSON kết quả (dùng cho CI/docker smoke, sau này cho `genh doctor`).
"""

import asyncio
import contextlib
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import orjson

ENV = "GH_BROWSER_SANDBOX"
MODES = ("auto", "on", "off")
PROC = Path("/proc")

REASON_UNCHECKABLE = "không kiểm được"
REASON_BLOCKED = "Máy chủ không cho Chromium tạo vùng cách ly (user namespace) — xem trang cảnh báo trong Console"
REASON_OFF = "Tắt bằng GH_BROWSER_SANDBOX=off"


def mode() -> str:
    """auto (mặc định) | on | off — giá trị lạ coi như auto."""
    v = os.environ.get(ENV, "auto").strip().lower()
    return v if v in MODES else "auto"


def launch_kwargs(mode_try: bool) -> dict[str, Any]:
    """Đối số cho `chromium.launch`: True = có sandbox; False = Playwright tự thêm --no-sandbox."""
    return {"chromium_sandbox": bool(mode_try)}


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _result(enabled: bool, md: str, reason: str | None) -> dict[str, Any]:
    return {"enabled": enabled, "mode": md, "reason": None if enabled else (reason or REASON_UNCHECKABLE),
            "checked_at": _now()}


def _read(path: Path) -> bytes | None:
    try:
        return path.read_bytes()
    except OSError:
        return None


def _ppid(pid: int) -> int | None:
    raw = _read(PROC / str(pid) / "stat")
    if raw is None:
        return None
    try:   # "pid (comm) S ppid ..." — comm có thể chứa dấu cách/ngoặc: cắt sau ')' cuối cùng
        return int(raw.decode("utf-8", "replace").rsplit(")", 1)[1].split()[1])
    except (IndexError, ValueError):
        return None


def _descendants(root: int) -> set[int]:
    kids: dict[int, list[int]] = {}
    for p in PROC.iterdir():
        if p.name.isdigit():
            pp = _ppid(int(p.name))
            if pp is not None:
                kids.setdefault(pp, []).append(int(p.name))
    out, todo = {root}, [root]
    while todo:
        for c in kids.get(todo.pop(), []):
            if c not in out:
                out.add(c)
                todo.append(c)
    return out


def _cmdline(pid: int) -> list[str] | None:
    raw = _read(PROC / str(pid) / "cmdline")
    if raw is None:
        return None
    # Tiến trình con của Chromium ghi đè tiêu đề: cmdline thành MỘT chuỗi cách nhau bằng dấu cách — tách cả hai kiểu.
    return raw.decode("utf-8", "replace").replace("\0", " ").split()


def _seccomp_mode(pid: int) -> str | None:
    raw = _read(PROC / str(pid) / "status")
    if raw is None:
        return None
    for line in raw.decode("utf-8", "replace").splitlines():
        if line.startswith("Seccomp:"):
            return line.split(":", 1)[1].strip()
    return None


def _userns(pid: int) -> str | None:
    try:
        return os.readlink(PROC / str(pid) / "ns" / "user")
    except OSError:
        return None


def inspect(pids: set[int] | None = None) -> dict[str, Any]:
    """Dò /proc: trả {readable, main: [{pid, no_sandbox}], renderers: [{pid, seccomp, userns_differs}]}.
    `pids` giới hạn trong cây tiến trình của trình duyệt đang kiểm; None → mọi tiến trình thấy được."""
    mine = _userns(os.getpid())
    if mine is None:
        return {"readable": False, "main": [], "renderers": []}
    main: list[dict[str, Any]] = []
    renderers: list[dict[str, Any]] = []
    try:
        entries = [int(p.name) for p in PROC.iterdir() if p.name.isdigit()]
    except OSError:
        return {"readable": False, "main": [], "renderers": []}
    for pid in entries:
        if pids is not None and pid not in pids:
            continue
        cmd = _cmdline(pid)
        if not cmd or "chrome" not in " ".join(cmd).lower():
            continue
        types = [a for a in cmd if a.startswith("--type=")]
        if not types:
            main.append({"pid": pid, "no_sandbox": "--no-sandbox" in cmd})
        elif types[0] == "--type=renderer":
            ns = _userns(pid)
            renderers.append({"pid": pid, "seccomp": _seccomp_mode(pid),
                              "userns_differs": ns is not None and mine is not None and ns != mine})
    return {"readable": True, "main": main, "renderers": renderers}


async def _browser_pids(browser: Any) -> set[int] | None:
    """Cây tiến trình của ĐÚNG trình duyệt này (qua CDP SystemInfo) — tránh lẫn Chromium khác trên máy; lỗi → None."""
    try:
        cdp = await browser.new_browser_cdp_session()
        try:
            info = await cdp.send("SystemInfo.getProcessInfo")
        finally:
            with contextlib.suppress(Exception):
                await cdp.detach()
        for p in info.get("processInfo", []):
            if p.get("type") == "browser" and isinstance(p.get("id"), int):
                return _descendants(int(p["id"]))
    except Exception:  # noqa: BLE001
        return None
    return None


async def probe(browser: Any) -> dict[str, Any]:
    """Dò sandbox THẬT của `browser`: mở 1 ngữ cảnh about:blank (không qua guard, không ra mạng) rồi đọc /proc."""
    md = mode()
    try:
        ctx = await browser.new_context()
    except Exception:  # noqa: BLE001
        return _result(False, md, REASON_UNCHECKABLE)
    try:
        page = await ctx.new_page()
        await page.goto("about:blank")
        # Tiến trình render sinh chậm sau điều hướng: thử lại ngắn trước khi kết luận.
        res: dict[str, Any] = {"readable": False, "main": [], "renderers": []}
        for _ in range(10):
            res = await asyncio.to_thread(inspect, await _browser_pids(browser))
            if not res["readable"] or any(r["seccomp"] == "2" and r["userns_differs"] for r in res["renderers"]):
                break
            await asyncio.sleep(0.2)
    except Exception:  # noqa: BLE001
        return _result(False, md, REASON_UNCHECKABLE)
    finally:
        with contextlib.suppress(Exception):
            await ctx.close()
    return _result_from(res, md)


def _result_from(res: dict[str, Any], md: str) -> dict[str, Any]:
    if not res["readable"]:
        return _result(False, md, REASON_UNCHECKABLE)
    if not res["main"]:
        return _result(False, md, REASON_UNCHECKABLE)
    if any(m["no_sandbox"] for m in res["main"]):
        return _result(False, md, "Chromium đang chạy với --no-sandbox")
    if not any(r["seccomp"] == "2" and r["userns_differs"] for r in res["renderers"]):
        return _result(False, md, "Tiến trình render không nằm trong vùng cách ly (seccomp + user namespace riêng)")
    return _result(True, md, None)


async def _cli() -> int:
    from playwright.async_api import async_playwright

    headless = os.environ.get("GH_BROWSER_HEADLESS", "1").strip().lower() in ("1", "true", "yes", "on")
    md = mode()
    async with async_playwright() as pw:
        try:
            browser = await pw.chromium.launch(headless=headless, **launch_kwargs(md != "off"))
        except Exception as exc:  # noqa: BLE001
            if md == "on":
                print(orjson.dumps({"enabled": False, "mode": md, "reason": str(exc).splitlines()[0][:200],
                                    "checked_at": _now()}).decode())
                return 1
            browser = await pw.chromium.launch(headless=headless, **launch_kwargs(False))
        try:
            out = await probe(browser)
        finally:
            await browser.close()
    print(orjson.dumps(out).decode())
    return 0


if __name__ == "__main__":
    if "--probe" not in sys.argv[1:]:
        print("dùng: python -m ghb.sandbox --probe", file=sys.stderr)
        sys.exit(2)
    sys.exit(asyncio.run(_cli()))
