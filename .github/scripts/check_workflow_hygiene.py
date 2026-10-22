#!/usr/bin/env python3
"""Kiểm VỆ SINH workflow (v0.1.48, F-71 + F-36): chuỗi cung ứng của CI/phát hành.

Đọc 4 workflow (ci.yml, installer-matrix.yml, release.yml, e2e-install.yml) và báo lỗi (tiếng Việt, kèm cách sửa)
khi có một trong bốn điều sau:

  1. `uses:` (bước hoặc job) không phải `./…` mà không ghim SHA đầy đủ 40 ký tự (`owner/repo@<sha>`), hoặc dòng
     `uses:` thiếu chú thích phiên bản `# vX.Y.Z` (người đọc/Renovate cần biết SHA ứng với bản nào — YAML bỏ comment
     nên dò dòng văn bản thô).
  2. Workflow thiếu `permissions` cấp workflow, hoặc cấp workflow có quyền write/write-all (job nào cần hơn thì nâng
     cục bộ trong job).
  3. Tham chiếu ảnh `…:latest` trong `with.tags` hoặc trong nội dung `run:` (không tính dòng chú thích bắt đầu bằng `#`).
     Không bắt nhầm `ubuntu-latest`, `releases/latest`, `--latest`.
  4. `services.*.image` hoặc `container.image` của job không ghim `@sha256:` (ảnh dịch vụ CI phải bất biến).

Chạy:  python3 .github/scripts/check_workflow_hygiene.py [--root <thư mục repo>]
Cần python3 + PyYAML. Thoát 0 nếu sạch, 1 nếu có lỗi (in từng lỗi dạng ::error::), 2 nếu thiếu tệp/PyYAML.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Any

try:
    import yaml  # type: ignore[import-untyped]  # PyYAML không kèm stub kiểu
except ImportError:  # pragma: no cover - chỉ khi máy thiếu PyYAML
    print(
        "check_workflow_hygiene: thiếu PyYAML — cài bằng `python3 -m pip install pyyaml` rồi chạy lại.",
        file=sys.stderr,
    )
    sys.exit(2)

WORKFLOWS = (
    ".github/workflows/ci.yml",
    ".github/workflows/installer-matrix.yml",
    ".github/workflows/release.yml",
    ".github/workflows/e2e-install.yml",
)
USES_OK = re.compile(r"^[\w.-]+/[\w./-]+@[0-9a-f]{40}$")
USES_LINE = re.compile(r"^\s*(?:-\s+)?uses:\s*(?P<ref>[^\s#]+)(?P<rest>.*)$")
VERSION_COMMENT = re.compile(r"#\s*v\d+\.\d+\.\d+")
LATEST_REF = re.compile(r"\S+:latest\b")


def load(root: Path, rel: str) -> tuple[dict[Any, Any], str]:
    p = root / rel
    if not p.is_file():
        print(
            f"check_workflow_hygiene: không thấy {rel} (root={root}) — chạy từ gốc repo hoặc truyền --root.",
            file=sys.stderr,
        )
        sys.exit(2)
    text = p.read_text(encoding="utf-8")
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as e:
        print(f"check_workflow_hygiene: {rel} không phải YAML hợp lệ: {e}", file=sys.stderr)
        sys.exit(2)
    if not isinstance(data, dict):
        print(f"check_workflow_hygiene: {rel} không phải một workflow YAML hợp lệ.", file=sys.stderr)
        sys.exit(2)
    return data, text


def check_uses(rel: str, wf: dict[Any, Any], text: str) -> list[str]:
    errs: list[str] = []
    refs: list[str] = []
    for job in (wf.get("jobs") or {}).values():
        if not isinstance(job, dict):
            continue
        if isinstance(job.get("uses"), str):
            refs.append(job["uses"])
        for st in job.get("steps") or []:
            if isinstance(st, dict) and isinstance(st.get("uses"), str):
                refs.append(st["uses"])
    for ref in sorted(set(refs)):
        if ref.startswith("./") or USES_OK.match(ref):
            continue
        errs.append(
            f"{rel}: `uses: {ref}` chưa ghim SHA — ghim theo SHA commit 40 ký tự dạng `owner/repo@<sha> # vX.Y.Z` "
            "(tag có thể bị dời; lấy SHA bằng `git ls-remote --tags https://github.com/<owner>/<repo>.git`, "
            "tag có chú thích thì lấy dòng ^{})."
        )
    for n, line in enumerate(text.splitlines(), 1):
        m = USES_LINE.match(line)
        if not m or m.group("ref").startswith("./"):
            continue
        if not VERSION_COMMENT.search(m.group("rest")):
            errs.append(
                f"{rel}:{n}: `uses: {m.group('ref')}` thiếu chú thích phiên bản `# vX.Y.Z` cuối dòng — "
                "thêm đúng số phiên bản ứng với SHA đã ghim (Renovate dựa vào chú thích này để nâng)."
            )
    return errs


def check_permissions(rel: str, wf: dict[Any, Any]) -> list[str]:
    perms = wf.get("permissions")
    if perms is None:
        return [
            f"{rel}: thiếu `permissions` cấp workflow — mặc định GITHUB_TOKEN có quyền ghi rộng; thêm "
            "`permissions:\\n  contents: read` ngay sau `concurrency`/`on`, job nào cần hơn thì nâng cục bộ trong job."
        ]
    bad = False
    if isinstance(perms, str):
        bad = perms.strip() == "write-all"
    elif isinstance(perms, dict):
        bad = any(str(v).strip() == "write" for v in perms.values())
    if bad:
        return [
            f"{rel}: `permissions` cấp workflow có quyền write ({perms!r}) — đặt `contents: read` ở cấp workflow, "
            "chỉ nâng quyền write cục bộ trong job thật sự cần."
        ]
    return []


def run_text_without_comments(run: str) -> str:
    return "\n".join(ln for ln in run.splitlines() if not ln.lstrip().startswith("#"))


def check_latest(rel: str, wf: dict[Any, Any]) -> list[str]:
    errs: list[str] = []
    for name, job in (wf.get("jobs") or {}).items():
        if not isinstance(job, dict):
            continue
        for st in job.get("steps") or []:
            if not isinstance(st, dict):
                continue
            strings = [("with.tags", str((st.get("with") or {}).get("tags") or ""))]
            strings.append(("run", run_text_without_comments(str(st.get("run") or ""))))
            for where, val in strings:
                for m in LATEST_REF.finditer(val):
                    errs.append(
                        f"{rel}: job `{name}` có tham chiếu ảnh `{m.group(0)}` trong {where} — không dùng tag :latest "
                        "(trôi nổi, không tái lập); dùng `:<version>`/`:sha-<commit>` hoặc digest ghim trong compose.release.yaml."
                    )
    return errs


def check_images(rel: str, wf: dict[Any, Any]) -> list[str]:
    errs: list[str] = []
    for name, job in (wf.get("jobs") or {}).items():
        if not isinstance(job, dict):
            continue
        images: list[tuple[str, str]] = []
        services = job.get("services")
        if isinstance(services, dict):
            for sname, svc in services.items():
                if isinstance(svc, dict) and isinstance(svc.get("image"), str):
                    images.append((f"services.{sname}.image", svc["image"]))
        container = job.get("container")
        if isinstance(container, str):
            images.append(("container", container))
        elif isinstance(container, dict) and isinstance(container.get("image"), str):
            images.append(("container.image", container["image"]))
        for where, img in images:
            if "@sha256:" not in img:
                errs.append(
                    f"{rel}: job `{name}` {where} = `{img}` chưa ghim digest — thêm `@sha256:<digest>` "
                    "(`docker buildx imagetools inspect <ảnh>` cho digest; Renovate nâng cùng PR)."
                )
    return errs


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Kiểm vệ sinh workflow: ghim SHA, permissions, không :latest, ảnh ghim digest.")
    ap.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parents[2],
        help="thư mục gốc repo (mặc định: suy từ vị trí script)",
    )
    args = ap.parse_args(argv)
    root: Path = args.root

    errs: list[str] = []
    for rel in WORKFLOWS:
        wf, text = load(root, rel)
        errs += check_uses(rel, wf, text)
        errs += check_permissions(rel, wf)
        errs += check_latest(rel, wf)
        errs += check_images(rel, wf)
    if errs:
        print(f"Vệ sinh workflow: {len(errs)} lỗi:", file=sys.stderr)
        for e in errs:
            print(f"::error::{e}", file=sys.stderr)
        return 1
    print(f"Vệ sinh workflow OK: {len(WORKFLOWS)} workflow (action ghim SHA, permissions tối thiểu, không :latest, ảnh dịch vụ ghim digest).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
