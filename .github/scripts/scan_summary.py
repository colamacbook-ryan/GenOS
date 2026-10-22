#!/usr/bin/env python3
"""Tóm tắt quét bảo mật dạng báo cáo (F-13) — KHÔNG chặn CI.

Dùng: python3 .github/scripts/scan_summary.py --kind {pip-audit,npm-audit,govulncheck} --title <t> <json>
Markdown ra stdout (nối vào $GITHUB_STEP_SUMMARY), `::warning::` ra stderr. Luôn exit 0, trừ sai tham số (2).
"""

import argparse
import json
import sys
from pathlib import Path

KINDS = ("pip-audit", "npm-audit", "govulncheck")


def _warn(msg: str) -> None:
    print(f"::warning::{msg}", file=sys.stderr)


def _cell(s: object) -> str:
    return str(s).replace("|", "\\|").replace("\n", " ")


def parse_stream(text: str) -> list:
    dec, i, out = json.JSONDecoder(), 0, []
    n = len(text)
    while i < n:
        while i < n and text[i].isspace():
            i += 1
        if i >= n:
            break
        obj, i = dec.raw_decode(text, i)
        out.append(obj)
    return out


def render_pip(title: str, data: dict) -> str:
    rows = []
    for dep in data.get("dependencies", []) or []:
        for v in dep.get("vulns", []) or []:
            fix = ", ".join(v.get("fix_versions") or []) or "chưa có"
            rows.append((dep.get("name", "?"), dep.get("version", "?"), v.get("id", "?"), fix))
    out = [f"### Quét bảo mật: {title}", ""]
    if not rows:
        return "\n".join(out + ["✅ Không thấy lỗ đã biết", ""])
    out += [f"**⚠️ {len(rows)} lỗ cần xem** (PyPI không ghi mức)", ""]
    out += ["| gói | phiên bản | mã lỗ | bản sửa |", "|---|---|---|---|"]
    for n, v, i, f in rows:
        out.append(f"| {_cell(n)} | {_cell(v)} | {_cell(i)} | {_cell(f)} |")
        _warn(f"{title}: {n} {v} có lỗ {i} (bản sửa: {f})")
    return "\n".join(out + [""])


def render_npm(title: str, data: dict) -> str:
    counts = (data.get("metadata") or {}).get("vulnerabilities") or {}
    vulns = data.get("vulnerabilities") or {}
    levels = ["critical", "high", "moderate", "low", "info"]
    out = [f"### Quét bảo mật: {title}", ""]
    total = sum(int(counts.get(k, 0) or 0) for k in levels)
    if total == 0 and not vulns:
        return "\n".join(out + ["✅ Không thấy lỗ đã biết", ""])
    out.append("Đếm theo mức: " + ", ".join(f"{k} {int(counts.get(k, 0) or 0)}" for k in levels))
    out.append("")
    hi = [(n, v) for n, v in vulns.items() if v.get("severity") in ("high", "critical")]
    if hi:
        out += [f"**⚠️ Lỗ mức cao: {len(hi)} gói high/critical**", ""]
        out += ["| gói | mức | bản sửa |", "|---|---|---|"]
        for n, v in hi:
            fa = v.get("fixAvailable")
            if isinstance(fa, dict):
                fix = f"{fa.get('name', n)}@{fa.get('version', '?')}"
            else:
                fix = "có" if fa else "chưa có"
            out.append(f"| {_cell(n)} | {_cell(v.get('severity'))} | {_cell(fix)} |")
            _warn(f"{title}: {n} có lỗ mức {v.get('severity')} (bản sửa: {fix})")
        out.append("")
    else:
        out += ["Không có lỗ high/critical.", ""]
    return "\n".join(out)


# govulncheck in tiến trình này SAU khi tải xong CSDL lỗ (internal/vulncheck: checkingSrcVulnsMessage /
# checkingBinVulnsMessage). Luồng thiếu nó = quét dừng giữa chừng (vd không tải được vuln.go.dev) dù JSON vẫn hợp lệ.
GOVULN_CHECKED_PREFIX = "Checking the "


def render_govuln(title: str, objs: list) -> str | None:
    found: dict[str, dict] = {}
    checked = False
    for o in objs:
        prog = o.get("progress") if isinstance(o, dict) else None
        if isinstance(prog, dict) and str(prog.get("message", "")).startswith(GOVULN_CHECKED_PREFIX):
            checked = True
        f = o.get("finding") if isinstance(o, dict) else None
        if not f:
            continue
        rec = found.setdefault(f.get("osv", "?"), {"called": False, "mods": set(), "fixed": ""})
        trace = f.get("trace") or []
        if any(fr.get("function") for fr in trace):
            rec["called"] = True
        if trace and trace[0].get("module"):
            rec["mods"].add(f"{trace[0]['module']}@{trace[0].get('version', '?')}")
        rec["fixed"] = rec["fixed"] or f.get("fixed_version", "")
    if not found and not checked:
        return None  # quét chưa chạy xong — KHÔNG được báo "không thấy lỗ"
    out = [f"### Quét bảo mật: {title}", ""]
    if not found:
        return "\n".join(out + ["✅ Không thấy lỗ đã biết", ""])
    called = {k: v for k, v in found.items() if v["called"]}
    if called:
        out += [f"**⚠️ Lỗ mức cao: {len(called)} lỗ có mã thật sự gọi tới**", ""]
    out += ["| mã lỗ | mức | mô-đun | bản sửa |", "|---|---|---|---|"]
    for osv, r in sorted(found.items(), key=lambda kv: (not kv[1]["called"], kv[0])):
        lv = "cao (mã thật sự gọi tới)" if r["called"] else "thấp"
        mods = ", ".join(sorted(r["mods"])) or "?"
        out.append(f"| {_cell(osv)} | {lv} | {_cell(mods)} | {_cell(r['fixed'] or 'chưa có')} |")
        if r["called"]:
            _warn(f"{title}: {osv} — mã thật sự gọi tới ({mods})")
    return "\n".join(out + [""])


def _complete(kind: str, data: dict) -> bool:
    """JSON hợp lệ nhưng là báo lỗi của công cụ (mất mạng, sai tham số…) thì KHÔNG phải kết quả quét."""
    if kind == "pip-audit":
        return isinstance(data.get("dependencies"), list)
    return "error" not in data and isinstance(data.get("metadata"), dict)


def _miss_text(title: str) -> str:
    """Một câu chung cho step summary và ::warning:: (trước đây hai nơi ghi khác nhau)."""
    return f"Không chạy được quét {title} (thiếu kết quả) — xem log bước quét"


def build(kind: str, title: str, path: Path) -> str:
    miss = f"### Quét bảo mật: {title}\n\n⚠️ {_miss_text(title)}\n"
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        text = ""
    out: str | None = None
    if text.strip():
        try:
            if kind == "govulncheck":
                out = render_govuln(title, parse_stream(text))
            else:
                data = json.loads(text)
                if isinstance(data, dict) and _complete(kind, data):
                    out = render_pip(title, data) if kind == "pip-audit" else render_npm(title, data)
        except (ValueError, AttributeError, TypeError):
            out = None
    if out is None:
        _warn(_miss_text(title))
        return miss
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--kind", required=True, choices=KINDS)
    p.add_argument("--title", required=True)
    p.add_argument("file")
    args = p.parse_args(argv)  # sai tham số => argparse thoát với mã 2
    print(build(args.kind, args.title, Path(args.file)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
