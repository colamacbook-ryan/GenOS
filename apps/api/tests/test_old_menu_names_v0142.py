"""v0.1.42 (F-7, review): chữ Sếp đọc được từ API và genh không còn trỏ tới menu đã đổi tên.

Quét chuỗi (không quét comment/docstring) trong `apps/api/gh/**/*.py`, `apps/api/gh/**/*.json` và
`apps/genh/**/*.go` (trừ test). Menu cũ → mới: "Điều khiển hệ thống" → Cài đặt / Kết nối / Đội ngũ,
"Dữ liệu & lưu trữ" → Cài đặt › Sao lưu & cập nhật, "Tổng quan điều hành" → Hôm nay, "Nhóm & Con người" → Khách & Nhóm.
"""

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
OLD = ("Điều khiển hệ thống", "Dữ liệu & lưu trữ", "Tổng quan điều hành", "Nhóm & Con người",
       # Menu "Tổng quan" đổi tên thành "Hôm nay" (review v0.1.42): đường dẫn chữ trỏ tới menu cũ.
       "Tổng quan ›", "trang Tổng quan", "Đầu Tổng quan")
GO_STRING = re.compile(r'"(?:[^"\\\n]|\\.)*"|`[^`]*`')


def _docstring_ids(tree: ast.AST) -> set[int]:
    ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                ids.add(id(first.value))
    return ids


def python_strings(path: Path) -> list[str]:
    tree = ast.parse(path.read_text("utf-8"))
    skip = _docstring_ids(tree)
    return [n.value for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in skip]


def go_strings(path: Path) -> list[str]:
    out: list[str] = []
    for line in path.read_text("utf-8").splitlines():
        if line.lstrip().startswith("//"):
            continue
        out += GO_STRING.findall(line)
    return out


def offenders() -> list[str]:
    hits: list[str] = []
    for p in sorted((ROOT / "apps" / "api" / "gh").rglob("*.py")):
        hits += [f"{p.relative_to(ROOT)}: {s[:80]!r}" for s in python_strings(p) if any(o in s for o in OLD)]
    for p in sorted((ROOT / "apps" / "api" / "gh").rglob("*.json")):
        text = p.read_text("utf-8")
        hits += [f"{p.relative_to(ROOT)}: {o}" for o in OLD if o in text]
    for p in sorted((ROOT / "apps" / "genh").rglob("*.go")):
        if p.name.endswith("_test.go"):
            continue
        hits += [f"{p.relative_to(ROOT)}: {s[:80]}" for s in go_strings(p) if any(o in s for o in OLD)]
    return hits


def test_no_old_menu_names_in_user_facing_strings() -> None:
    assert offenders() == []


def test_scanner_catches_strings_but_not_comments(tmp_path: Path) -> None:
    py = tmp_path / "m.py"
    py.write_text('"""Điều khiển hệ thống."""\n# Dữ liệu & lưu trữ\nX = f"mở ở Dữ liệu & lưu trữ {1}"\n', "utf-8")
    assert [s for s in python_strings(py) if "Dữ liệu" in s] == ["mở ở Dữ liệu & lưu trữ "]
    assert not [s for s in python_strings(py) if "Điều khiển" in s]
    go = tmp_path / "m.go"
    go.write_text('// Điều khiển hệ thống\nNext: "Chọn ở Dữ liệu & lưu trữ",\n', "utf-8")
    assert go_strings(go) == ['"Chọn ở Dữ liệu & lưu trữ"']
