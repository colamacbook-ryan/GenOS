#!/usr/bin/env python3
"""Kiểm sự thật trong tài liệu công khai (lưới chống tài liệu lệch mã).

Vì sao: v0.1.55 đổi "Việc Sếp cần làm" còn 1 dòng bắt buộc nhưng README và gen-coach.md vẫn ghi "6 dòng bắt buộc" suốt
nhiều bản — người đọc tưởng phải nối Gen-hub, Facebook, Telegram mới "xong". Bộ kiểm này đối chiếu những con số tài liệu
nhắc với NGUỒN SỰ THẬT trong mã, để lệch là CI đỏ chứ không đợi người đọc phát hiện.

Các bất biến:
  1. Số dòng "Việc Sếp cần làm" và số dòng bắt buộc trong README.md và docs/design/gen-coach.md phải bằng
     `ROWS` / `REQUIRED_TOTAL` của apps/api/gh/boss_checks/service.py (đọc bằng `ast`, không import mã ứng dụng);
     mỗi tài liệu phải CÓ câu nêu số (xoá câu đi cũng là lệch); danh sách đánh số trong README không được gắn
     "(bắt buộc)" nhiều hơn số dòng bắt buộc thật.
  2. docs/handoff/06-owner-onboarding.md (thiết kế gốc của trình thiết lập 12 bước) phải ghi rõ ở đầu tệp là đã được
     thay bằng bản sau (cụm "đã thay bởi").
  3. docs/runbook.md không được chỉ đường cũ (Tổng quan có thẻ Sức khoẻ, nút Cập nhật ở Trợ giúp).
  4. docs/reports/HANDOFF-v0.1.1.md không quá 200 dòng (CLAUDE.md: chỉ giữ hiện trạng + việc dở).
  5. Tiêu đề "sau vX.Y.Z" trong docs/ROADMAP.md phải bằng VERSION (lên bản mà quên cập nhật lộ trình ⇒ đỏ).
  6. (v0.1.68, F119) HANDOFF chỉ có MỘT khối "Sếp cần làm" / "Boss phải làm" (một danh sách, đối chiếu "Việc Sếp cần làm"
     trong app — không chồng khối của từng bản), và không còn dòng "Phát hành vX" của bản ĐÃ QUA (X < VERSION; bản
     đang chờ phát hành = VERSION thì hợp lệ).
  7. (v0.1.68, F120) docs/ARCHITECTURE.md không nhắc MinIO (deploy/compose.yaml không có MinIO — tệp ở volume `gh_objects`).
  8. (v0.1.68, F120) Product Spec: phiên bản ở tiêu đề = phiên bản ở chân trang; mục O không còn là "những gì cần chốt tiếp"
     (năm câu đó đã được chốt, mục O ghi trạng thái hiện hành).

Chạy:  python3 .github/scripts/check_doc_facts.py [thư mục gốc repo]
Thoát 0 nếu đúng, 1 nếu có lỗi (in từng lỗi, kèm dòng ::error:: cho GitHub Actions), 2 nếu thiếu tệp / không đọc được nguồn.
"""

from __future__ import annotations

import ast
import re
import sys
from collections.abc import Sequence
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

BOSS_SERVICE = "apps/api/gh/boss_checks/service.py"
README = "README.md"
GEN_COACH = "docs/design/gen-coach.md"
ONBOARDING = "docs/handoff/06-owner-onboarding.md"
RUNBOOK = "docs/runbook.md"
HANDOFF = "docs/reports/HANDOFF-v0.1.1.md"
ROADMAP = "docs/ROADMAP.md"
VERSION_FILE = "VERSION"
ARCHITECTURE = "docs/ARCHITECTURE.md"
SPEC = "docs/Gen-Harness-Product-Spec-LOCKED.md"

HANDOFF_MAX_LINES = 200

#: Câu nêu TỔNG số dòng (nhóm 1 = số).
TOTAL_PATTERNS = (
    re.compile(r"Hiện có (\d+) dòng"),
    re.compile(r"(\d+) dòng \"Việc Sếp cần làm\""),
)
#: Câu nêu số dòng BẮT BUỘC (nhóm 1 = số).
REQUIRED_PATTERNS = (re.compile(r"(\d+) dòng bắt buộc"),)
REQUIRED_MARK = "(bắt buộc)"

#: Đường đi cũ không còn đúng (sau v0.1.42 chuyển màn, v0.1.55/v0.1.60 Mặt tiền) — runbook không được nhắc lại.
RUNBOOK_STALE = (
    ("Console → **Tổng quan**: dải", "màn 'Tổng quan' nay tên 'Hôm nay'; thẻ Sức khoẻ nằm ở Cài đặt › Sao lưu & cập nhật"),
    ("(Trợ giúp, hoặc", "Trợ giúp chỉ còn liên kết tới Cài đặt › Sao lưu & cập nhật, không còn nút Cập nhật ngay"),
)


class DocFactsError(Exception):
    """Không đọc được nguồn sự thật (thiếu tệp, mã đổi hình dạng) — thoát 2, không phải lỗi tài liệu."""


def _read(root: Path, rel: str) -> str:
    try:
        return (root / rel).read_text(encoding="utf-8")
    except OSError as exc:
        raise DocFactsError(f"không đọc được {rel}: {exc.strerror or exc}") from exc


def boss_rows(root: Path) -> tuple[int, int]:
    """(tổng số dòng, số dòng bắt buộc) đọc từ `ROWS` trong boss_checks/service.py bằng ast."""
    src = _read(root, BOSS_SERVICE)
    try:
        tree = ast.parse(src)
    except SyntaxError as exc:
        raise DocFactsError(f"{BOSS_SERVICE} không phân tích được: {exc.msg}") from exc
    for node in tree.body:
        target: ast.expr | None = None
        value: ast.expr | None = None
        if isinstance(node, ast.AnnAssign):
            target, value = node.target, node.value
        elif isinstance(node, ast.Assign) and len(node.targets) == 1:
            target, value = node.targets[0], node.value
        if not (isinstance(target, ast.Name) and target.id == "ROWS" and isinstance(value, (ast.Tuple, ast.List))):
            continue
        total = 0
        required = 0
        for item in value.elts:
            if not isinstance(item, ast.Dict):
                raise DocFactsError(f"ROWS trong {BOSS_SERVICE} có phần tử không phải dict")
            optional: bool | None = None
            for k, v in zip(item.keys, item.values, strict=True):
                if isinstance(k, ast.Constant) and k.value == "optional" and isinstance(v, ast.Constant):
                    optional = bool(v.value)
            if optional is None:
                raise DocFactsError(f"một dòng của ROWS trong {BOSS_SERVICE} không có 'optional' là hằng số")
            total += 1
            if not optional:
                required += 1
        if total == 0:
            raise DocFactsError(f"ROWS trong {BOSS_SERVICE} rỗng")
        return total, required
    raise DocFactsError(f"không thấy ROWS trong {BOSS_SERVICE}")


def _section(text: str, heading: str) -> str:
    """Nội dung từ dòng `heading` tới tiêu đề ### kế tiếp (rỗng nếu không có)."""
    m = re.search(rf"^{re.escape(heading)}\s*$(.*?)(?=^#{{1,3}} |\Z)", text, flags=re.MULTILINE | re.DOTALL)
    return m.group(1) if m else ""


def _numbers(text: str, patterns: Sequence[re.Pattern[str]]) -> list[int]:
    return [int(m.group(1)) for p in patterns for m in p.finditer(text)]


def check_boss_counts(root: Path) -> list[str]:
    total, required = boss_rows(root)
    errors: list[str] = []
    for rel in (README, GEN_COACH):
        text = _read(root, rel)
        totals = _numbers(text, TOTAL_PATTERNS)
        requireds = _numbers(text, REQUIRED_PATTERNS)
        if not totals:
            errors.append(f"{rel}: thiếu câu nêu tổng số dòng 'Việc Sếp cần làm' (mã có {total} dòng)")
        if not requireds:
            errors.append(f"{rel}: thiếu câu nêu số dòng bắt buộc (mã có {required} dòng bắt buộc)")
        for n in totals:
            if n != total:
                errors.append(f"{rel}: ghi {n} dòng 'Việc Sếp cần làm' nhưng {BOSS_SERVICE} có {total} dòng")
        for n in requireds:
            if n != required:
                errors.append(f"{rel}: ghi {n} dòng bắt buộc nhưng {BOSS_SERVICE} có REQUIRED_TOTAL = {required}")
    marked = _section(_read(root, README), "### Việc Sếp cần làm").count(REQUIRED_MARK)
    if marked > required:
        errors.append(f"{README}: mục 'Việc Sếp cần làm' gắn {REQUIRED_MARK} {marked} lần nhưng chỉ có {required} dòng bắt buộc")
    return errors


def check_onboarding_notice(root: Path) -> list[str]:
    head = "\n".join(_read(root, ONBOARDING).splitlines()[:12])
    if "đã thay bởi" not in head.lower():
        return [f"{ONBOARDING}: đầu tệp phải ghi rõ bản thiết kế gốc này 'đã thay bởi' bản nào (hiện trạng ở mã)"]
    return []


def check_runbook_paths(root: Path) -> list[str]:
    text = _read(root, RUNBOOK)
    return [f"{RUNBOOK}: còn chỉ đường cũ '{bad}' — {why}" for bad, why in RUNBOOK_STALE if bad in text]


def check_handoff_length(root: Path) -> list[str]:
    n = len(_read(root, HANDOFF).splitlines())
    if n > HANDOFF_MAX_LINES:
        return [f"{HANDOFF}: {n} dòng, quá {HANDOFF_MAX_LINES} dòng (chỉ giữ hiện trạng + việc dở; lịch sử để ở CHANGELOG và docs/releases/)"]
    return []


def check_roadmap_version(root: Path) -> list[str]:
    version = _read(root, VERSION_FILE).strip()
    headings = [ln for ln in _read(root, ROADMAP).splitlines() if ln.startswith("#") and re.search(r"\bsau v\d", ln)]
    if not headings:
        return [f"{ROADMAP}: không có tiêu đề nào dạng 'sau vX.Y.Z' để đối chiếu với VERSION ({version})"]
    errors = []
    for ln in headings:
        for v in re.findall(r"\bsau (v\d+\.\d+\.\d+(?:-[0-9A-Za-z.]*[0-9A-Za-z])?)", ln):
            if v != version:
                errors.append(f"{ROADMAP}: tiêu đề '{ln.strip()}' ghi 'sau {v}' nhưng VERSION là {version}")
    return errors


BOSS_BLOCK_RE = re.compile(r"^#{2,4}\s+(?:Boss phải làm|Sếp cần làm)", re.MULTILINE)
RELEASE_LINE_RE = re.compile(r"Phát hành (v\d+\.\d+\.\d+)")


def _semver(v: str) -> tuple[int, int, int]:
    m = re.match(r"v?(\d+)\.(\d+)\.(\d+)", v)
    return (int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else (0, 0, 0)


def check_handoff_lists(root: Path) -> list[str]:
    text = _read(root, HANDOFF)
    errors: list[str] = []
    blocks = len(BOSS_BLOCK_RE.findall(text))
    if blocks > 1:
        errors.append(f"{HANDOFF}: có {blocks} khối 'Boss phải làm' / 'Sếp cần làm' — gộp thành MỘT danh sách "
                      "'Sếp cần làm bây giờ' (đối chiếu Việc Sếp cần làm trong app; lịch sử từng bản ở docs/releases/)")
    version = _read(root, VERSION_FILE).strip()
    for v in dict.fromkeys(RELEASE_LINE_RE.findall(text)):
        if _semver(v) < _semver(version):
            errors.append(f"{HANDOFF}: còn dòng 'Phát hành {v}' nhưng VERSION là {version} — bản đó đã qua, xoá dòng")
    return errors


def check_architecture_no_minio(root: Path) -> list[str]:
    if re.search(r"minio", _read(root, ARCHITECTURE), flags=re.IGNORECASE):
        return [f"{ARCHITECTURE}: còn nhắc MinIO nhưng deploy/compose.yaml không có MinIO (tệp ở volume gh_objects)"]
    return []


def check_spec_consistency(root: Path) -> list[str]:
    text = _read(root, SPEC)
    errors: list[str] = []
    head = re.search(r"LOCKED v(\d+\.\d+)", text)
    foot = re.search(r"Product Spec v(\d+\.\d+)", text[-600:])
    if head and foot and head.group(1) != foot.group(1):
        errors.append(f"{SPEC}: tiêu đề ghi LOCKED v{head.group(1)} nhưng chân trang ghi Product Spec v{foot.group(1)}")
    if re.search(r"^# O\.\s*NHỮNG GÌ CẦN CHỐT TIẾP", text, flags=re.MULTILINE):
        errors.append(f"{SPEC}: mục O vẫn là 'NHỮNG GÌ CẦN CHỐT TIẾP' — năm câu đã chốt, ghi trạng thái hiện hành")
    return errors


def run_checks(root: Path) -> list[str]:
    errors: list[str] = []
    for check in (check_boss_counts, check_onboarding_notice, check_runbook_paths, check_handoff_length,
                  check_roadmap_version, check_handoff_lists, check_architecture_no_minio, check_spec_consistency):
        errors.extend(check(root))
    return errors


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    root = Path(args[0]).resolve() if args else REPO
    try:
        errors = run_checks(root)
    except DocFactsError as exc:
        print(f"LỖI: {exc}", file=sys.stderr)
        return 2
    for e in errors:
        print(f"::error::{e}")
    if errors:
        print(f"Tài liệu lệch mã: {len(errors)} chỗ.")
        return 1
    print("Tài liệu khớp mã.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
