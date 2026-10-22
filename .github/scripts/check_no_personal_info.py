#!/usr/bin/env python3
"""Kiểm tĩnh (v0.1.56, viết lại v0.1.57): mã GIAO cho Owner khác không được mang thông tin riêng của chủ Gen-hub.

Repo công khai và mọi bản phát hành (web bundle, ảnh API, genh, install, compose, db) đi tới Owner khác. Bộ kiểm này
KHÔNG được tự ghi danh tính của Sếp vào repo (bản v0.1.56 từng làm vậy: liệt kê tên miền / email / tên riêng làm mẫu cấm
ngay trong mã). Nên chia hai lớp:

1. QUY TẮC CHUNG (nằm trong repo, không lộ danh tính, luôn chạy):
   a. mọi địa chỉ email trong phạm vi quét phải thuộc miền `example.test` / `example.com` (kể cả miền con) hoặc là địa chỉ
      `noreply` (`noreply@…`, `no-reply@…`, `…@users.noreply.github.com`);
   b. địa chỉ Gen-hub dạng `https://<máy-chủ>/mcp` (ô nhập, ví dụ, câu lỗi) phải là chỗ giữ chỗ `<…>` hoặc tên miền
      `example.*` — một tên miền thật đứng trước `/mcp` là vi phạm.
2. MẪU RIÊNG (nằm NGOÀI repo): biến môi trường `GH_PERSONAL_PATTERNS`, mỗi dòng một biểu thức chính quy (không phân biệt
   hoa thường; dòng trống và dòng bắt đầu bằng `#` bị bỏ qua) — tên miền riêng, tên gọi riêng, email riêng của Sếp. CI nạp
   từ secret `GH_PERSONAL_PATTERNS`. Trống ⇒ bỏ lớp này và in cảnh báo "chưa cấu hình mẫu riêng". KHÔNG BAO GIỜ in giá trị
   mẫu hay dòng khớp mẫu riêng ra log: chỉ in `tệp:dòng` + số thứ tự mẫu (bắt đầu từ 1) + tổng số mẫu.

Phạm vi quét (tệp văn bản đã theo dõi bởi git; không có git thì duyệt thư mục): `apps/web/src`, `apps/api/gh`,
`packages/contracts/src`, `apps/genh` (trừ `*_test.go`), `install.sh`, `install.ps1`, `deploy`, `db/sql`.
KHÔNG quét test / mock / e2e / docs (đó là việc của người viết, không giao cho Owner).

Cho phép: chuỗi tổ chức GitHub phát hành (cần để tải bản phát hành; xem `ALLOWED_STRINGS`) và dòng có chú thích
`allow-personal-info` (chỉ dùng khi thật sự cần, vd câu SQL khớp ghi chú cũ đã lưu trong DB).

Cách chạy: `python3 .github/scripts/check_no_personal_info.py [root]` — in `tệp:dòng` + dòng `::error file=…,line=…::`
cho GitHub Actions, exit 1 nếu có vi phạm, 0 nếu sạch, 2 nếu không thấy thư mục gốc.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import NamedTuple

REPO = Path(__file__).resolve().parents[2]
#: Thư mục (tiền tố) và tệp đơn nằm trong phạm vi quét, tính từ gốc repo.
SCOPE_DIRS = ("apps/web/src/", "apps/api/gh/", "packages/contracts/src/", "apps/genh/", "deploy/", "db/sql/")
SCOPE_FILES = ("install.sh", "install.ps1")
SKIP_PARTS = frozenset({"node_modules", ".git", "__pycache__", "dist"})
MAX_BYTES = 2_000_000

ENV_PATTERNS = "GH_PERSONAL_PATTERNS"
ALLOW_MARK = "allow-personal-info"
#: Chuỗi cho phép duy nhất: tổ chức GitHub chứa bản phát hành (install / genh / config tải bản về).
ALLOWED_STRINGS = ("Genesis-ryan-84-0567536339",)

# Quy tắc chung 1a — địa chỉ email. Local-part không chứa khoảng trắng; miền phải có ít nhất một dấu chấm và TLD toàn chữ
# (nên `pkg@1.2.3`, `x@sha256:…`, `@scope/pkg` không bị bắt).
EMAIL = re.compile(r"(?<![\w.+-])([A-Za-z0-9][A-Za-z0-9._%+-]*)@((?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,})(?![\w-])")
#: Miền cho phép: example.test / example.com (và miền con); thêm example.org / example.net cho tiện ví dụ.
EXAMPLE_DOMAIN = re.compile(r"(?:^|\.)example\.(?:test|com|org|net)$", re.IGNORECASE)
NOREPLY_LOCAL = re.compile(r"^no-?reply(?:[+.-].*)?$", re.IGNORECASE)
NOREPLY_DOMAIN = re.compile(r"(?:^|\.)noreply\.github\.com$", re.IGNORECASE)
# Quy tắc chung 1b — địa chỉ Gen-hub: tên miền có chấm ngay trước `/mcp`. `https://<máy-chủ>/mcp` không khớp (có `<`).
MCP_URL = re.compile(r"https?://((?:[a-z0-9-]+\.)+[a-z]{2,})(?::\d+)?/mcp\b", re.IGNORECASE)


class Hit(NamedTuple):
    rel: str
    line_no: int
    rule: str        # "email" | "mcp_url" | "private"
    detail: str      # email: miền; mcp_url: máy chủ; private: số thứ tự mẫu (1-based)
    line: str | None  # nội dung dòng; None với mẫu riêng (không được in ra log)


def in_scope(rel: str) -> bool:
    if rel.endswith("_test.go"):
        return False
    if any(part in SKIP_PARTS for part in rel.split("/")):
        return False
    return rel in SCOPE_FILES or rel.startswith(SCOPE_DIRS)


def _git_files(root: Path) -> list[str] | None:
    """Danh sách tệp git theo dõi dưới `root`; None nếu `root` không phải kho git (ví dụ thư mục tạm của test)."""
    if not (root / ".git").exists():
        return None
    try:
        out = subprocess.run(["git", "ls-files", "-z"], cwd=root, capture_output=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        return None
    return sorted(p for p in out.decode("utf-8", errors="replace").split("\0") if p)


def candidate_files(root: Path) -> list[str]:
    files = _git_files(root)
    if files is None:
        files = sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file())
    return [f for f in files if in_scope(f)]


def scrub(line: str) -> str:
    for allowed in ALLOWED_STRINGS:
        line = line.replace(allowed, "")
    return line


def load_private_patterns(env: Mapping[str, str]) -> tuple[list[re.Pattern[str]], list[int]]:
    """(các mẫu riêng đã biên dịch, số thứ tự 1-based của dòng KHÔNG phải regex hợp lệ). Không bao giờ in giá trị mẫu."""
    patterns: list[re.Pattern[str]] = []
    invalid: list[int] = []
    idx = 0
    for raw in env.get(ENV_PATTERNS, "").splitlines():
        raw = raw.strip()
        if not raw or raw.startswith("#"):
            continue
        idx += 1
        try:
            patterns.append(re.compile(raw, re.IGNORECASE))
        except re.error:
            invalid.append(idx)
            patterns.append(re.compile(r"(?!x)x"))     # giữ nguyên thứ tự số; mẫu hỏng không khớp gì
    return patterns, invalid


def general_hits(shown: str) -> Iterator[tuple[str, str]]:
    """(quy tắc, chi tiết) cho từng vi phạm quy tắc chung trên một dòng đã `scrub`."""
    for m in EMAIL.finditer(shown):
        local, domain = m.group(1), m.group(2)
        if EXAMPLE_DOMAIN.search(domain) or NOREPLY_DOMAIN.search(domain) or NOREPLY_LOCAL.match(local):
            continue
        yield "email", domain
    for m in MCP_URL.finditer(shown):
        if not EXAMPLE_DOMAIN.search(m.group(1)):
            yield "mcp_url", m.group(1)


def violations(root: Path, private: list[re.Pattern[str]] | None = None) -> Iterator[Hit]:
    """Từng dòng vi phạm. Mẫu riêng được xét TRƯỚC và, nếu khớp, không trả nội dung dòng (không lộ ra log)."""
    private = private or []
    for rel in candidate_files(root):
        path = root / rel
        try:
            if not path.is_file() or path.stat().st_size > MAX_BYTES:
                continue
            text = path.read_bytes().decode("utf-8")      # tệp nhị phân / không phải UTF-8 thì bỏ qua
        except (OSError, UnicodeDecodeError):
            continue
        for n, line in enumerate(text.splitlines(), start=1):
            if ALLOW_MARK in line:
                continue
            shown = scrub(line)
            hit = next((i for i, pat in enumerate(private, start=1) if pat.search(shown)), None)
            if hit is not None:
                yield Hit(rel, n, "private", str(hit), None)
                continue
            for rule, detail in general_hits(shown):
                yield Hit(rel, n, rule, detail, line.strip())
                break


def main(argv: list[str], env: Mapping[str, str] | None = None) -> int:
    env = os.environ if env is None else env
    root = Path(argv[1]) if len(argv) > 1 else REPO
    if not root.is_dir():
        print(f"::error::Không thấy thư mục {root}")
        return 2
    private, invalid = load_private_patterns(env)
    if invalid:
        print(f"::error::{ENV_PATTERNS}: dòng mẫu số {', '.join(map(str, invalid))} không phải biểu thức chính quy hợp lệ "
              "(không in giá trị) — sửa secret rồi chạy lại.")
        return 2
    if private:
        print(f"Mẫu riêng: {len(private)} mẫu (đọc từ {ENV_PATTERNS}; không in giá trị).")
    else:
        print(f"::warning::chưa cấu hình mẫu riêng ({ENV_PATTERNS} trống) — chỉ chạy quy tắc chung (email / địa chỉ /mcp).")
    found = list(violations(root, private))
    for h in found:
        if h.rule == "private":
            print(f"{h.rel}:{h.line_no}: khớp mẫu riêng số {h.detail}/{len(private)} (không in dòng)")
            print(f"::error file={h.rel},line={h.line_no}::Thông tin riêng của Sếp (khớp mẫu riêng số {h.detail} trong "
                  f"{ENV_PATTERNS}) trong mã giao cho Owner khác — dùng chỗ giữ chỗ chung và tên Kho chung \"Kho dữ liệu\"")
            continue
        print(f"{h.rel}:{h.line_no}: {(h.line or '')[:160]}")
        if h.rule == "email":
            msg = (f"Địa chỉ email thật (miền {h.detail}) trong mã giao cho Owner khác — chỉ dùng example.test / "
                   "example.com hoặc địa chỉ noreply")
        else:
            msg = (f"Địa chỉ Gen-hub thật ({h.detail}) trước /mcp trong mã giao cho Owner khác — dùng chỗ giữ chỗ "
                   "https://<địa-chỉ-gen-hub-của-bạn>/mcp hoặc example.test")
        print(f"::error file={h.rel},line={h.line_no}::{msg}")
    if found:
        print(f"Có {len(found)} chỗ lộ thông tin riêng.")
        return 1
    print(f"Sạch: không lộ thông tin riêng trong mã giao cho Owner khác ({root}).")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
