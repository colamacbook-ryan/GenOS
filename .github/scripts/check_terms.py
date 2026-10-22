#!/usr/bin/env python3
"""Kiểm thuật ngữ chuẩn (lưới L5, v0.1.61): chữ hiển thị cho Sếp phải dùng đúng từ của CLAUDE.md.

Vì sao: thuật ngữ trong CLAUDE.md (Kho dữ liệu, mã PIN, khoá, API & Model…) chỉ là quy ước viết tay — không ai bắt khi
một màn mới viết "Kho tri thức", "nhập PIN", "khóa" hay in tên vai trò tiếng Anh. Bộ kiểm này đọc `terms.json` (cùng thư mục):

- `canonical`: danh sách từ chuẩn (tài liệu sống; test kiểm không mẫu cấm nào cấm một từ chuẩn).
- `forbidden`: mẫu cấm — mỗi mẫu có `id`, `pattern` (biểu thức chính quy, so khớp sau khi chuẩn hoá Unicode NFC),
  `use` (nên viết gì thay), `static` (quét mã) / `crawl` (Playwright route-crawl quét chữ hiển thị trên trang thật),
  `multiword_only` (chỉ tính chuỗi có khoảng trắng — để tên vai `'Manager'` dùng làm khoá dữ liệu không bị tính),
  `allow_files` (tệp được phép nhắc chính từ cấm, vd migration dọn dữ liệu).
- `baseline`: hiện trạng lúc dựng lưới — `{id mẫu: {tệp: số lần}}`. RATCHET: số vi phạm của mỗi (mẫu, tệp) KHÔNG ĐƯỢC TĂNG
  (tệp mới ⇒ 0) và cũng không được để baseline cao hơn thực tế (sửa xong thì phải hạ dòng baseline, gói nào sửa gói đó xoá
  dòng của mình — `--lower` ghi lại cho đúng, chỉ hạ, không bao giờ nâng).
- `crawl_baseline`: hiện trạng tương tự cho route-crawl (`apps/web/e2e/route-crawl-v016x.spec.ts`), khoá `"<cỡ>|<route>|<luật>"`.

Phạm vi quét CHỮ HIỂN THỊ (không quét chú thích, docstring, tên biến): chuỗi trong `apps/web/src`, `packages/contracts/src`,
`packages/ui/src` (.ts/.tsx — chuỗi nháy đơn/kép/template + chữ JSX), chuỗi trong `apps/api/gh` (.py — bỏ docstring; .json —
giá trị chuỗi, vd `registry.json`, `lessons.json`), chuỗi SQL trong `db/sql` (migration dữ liệu). KHÔNG quét test / mock / e2e / docs.
Dòng có chú thích `terms-ok` được bỏ qua (chỉ dùng khi thật sự cần nhắc chính từ cấm, kèm lý do).

Cách chạy: `python3 .github/scripts/check_terms.py [root]` — in `tệp:dòng` + dòng `::error file=…,line=…::` cho GitHub Actions;
exit 1 nếu có vi phạm, 0 nếu sạch, 2 nếu lỗi cấu hình / không thấy thư mục gốc. `--lower` ghi lại baseline đã hạ.
"""

from __future__ import annotations

import ast
import json
import re
import subprocess
import sys
import unicodedata
from collections import Counter
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any, NamedTuple

REPO = Path(__file__).resolve().parents[2]
TERMS_PATH = Path(__file__).resolve().with_name("terms.json")

SCOPE_DIRS = ("apps/web/src/", "packages/contracts/src/", "packages/ui/src/", "apps/api/gh/", "db/sql/")
SCAN_SUFFIXES = (".ts", ".tsx", ".py", ".json", ".sql")
SKIP_PARTS = frozenset({"node_modules", ".git", "__pycache__", "dist", "tests", "test", "e2e", "__mocks__"})
SKIP_SUFFIXES = (".test.ts", ".test.tsx", ".spec.ts", ".d.ts")
MAX_BYTES = 2_000_000
ALLOW_MARK = "terms-ok"


class StringHit(NamedTuple):
    line_no: int
    text: str


class Violation(NamedTuple):
    rule: str
    rel: str
    count: int
    allowed: int
    first_line: int
    first_text: str


# ── Trích chuỗi hiển thị ────────────────────────────────────────────────────────────────────────────────────────
def _norm(text: str) -> str:
    return unicodedata.normalize("NFC", text)


def extract_python(src: str) -> Iterator[StringHit]:
    """Mọi hằng chuỗi trong .py trừ docstring (module / lớp / hàm)."""
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return
    docs: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
                docs.add(id(first.value))
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docs:
            yield StringHit(node.lineno, node.value)


_JSX_TEXT = re.compile(r">([^<>{}\n]*?[^\W\d_][^<>{}\n]*)(?=<)")
_PURE_TEXT_LINE = re.compile(r"^[^\W\d_][^<>{}()=;`'\"\\]*$")


def extract_typescript(src: str) -> Iterator[StringHit]:
    """Chuỗi '…', "…", `…` (kể cả phần ${…}) và chữ JSX; bỏ chú thích. Bộ quét trạng thái nhỏ, đủ cho mã web của repo."""
    n = len(src)
    i = 0
    line = 1
    stack: list[str] = ["code"]  # "code" | "expr" (trong ${…} của template) | "tpl"
    depth: list[int] = [0]  # độ sâu {} cho mỗi "expr"
    buf: list[str] = []
    start_line = 1
    code_chars: list[str] = []  # mã đã bỏ chuỗi + chú thích, giữ xuống dòng — để tìm chữ JSX
    while i < n:
        ch = src[i]
        mode = stack[-1]
        if mode in ("code", "expr"):
            nxt = src[i + 1] if i + 1 < n else ""
            if ch == "/" and nxt == "/":
                while i < n and src[i] != "\n":
                    i += 1
                continue
            if ch == "/" and nxt == "*":
                end = src.find("*/", i + 2)
                end = n if end < 0 else end + 2
                line += src.count("\n", i, end)
                code_chars.append("\n" * src.count("\n", i, end))
                i = end
                continue
            if ch in "'\"":
                quote = ch
                j = i + 1
                parts: list[str] = []
                start_line = line
                while j < n and src[j] != quote and src[j] != "\n":
                    if src[j] == "\\" and j + 1 < n:
                        parts.append(src[j + 1])
                        j += 2
                        continue
                    parts.append(src[j])
                    j += 1
                yield StringHit(start_line, "".join(parts))
                code_chars.append(" ")
                i = j + 1
                continue
            if ch == "`":
                stack.append("tpl")
                buf = []
                start_line = line
                i += 1
                continue
            if mode == "expr":
                if ch == "{":
                    depth[-1] += 1
                elif ch == "}":
                    if depth[-1] == 0:
                        stack.pop()
                        depth.pop()
                        buf = []
                        start_line = line
                        i += 1
                        continue
                    depth[-1] -= 1
            if ch == "\n":
                line += 1
            code_chars.append(ch)
            i += 1
            continue
        # mode == "tpl"
        if ch == "\\" and i + 1 < n:
            buf.append(src[i + 1])
            if src[i + 1] == "\n":
                line += 1
            i += 2
            continue
        if ch == "`":
            stack.pop()
            yield StringHit(start_line, "".join(buf))
            code_chars.append(" ")
            i += 1
            continue
        if ch == "$" and i + 1 < n and src[i + 1] == "{":
            yield StringHit(start_line, "".join(buf))
            buf = []
            stack.append("expr")
            depth.append(0)
            i += 2
            continue
        if ch == "\n":
            line += 1
        buf.append(ch)
        i += 1
    # Chữ JSX: giữa `>` và `<` trên một dòng, hoặc cả dòng chỉ có chữ (đoạn văn xuống dòng trong JSX).
    for no, row in enumerate("".join(code_chars).split("\n"), start=1):
        stripped = row.strip()
        for m in _JSX_TEXT.finditer(row):
            yield StringHit(no, m.group(1).strip())
        if (
            " " in stripped
            and _PURE_TEXT_LINE.match(stripped)
            and not stripped.startswith(("import ", "export ", "return ", "case ", "type ", "interface "))
        ):
            yield StringHit(no, stripped)


_JSON_STRING = re.compile(r'"((?:[^"\\\n]|\\.)*)"(\s*:)?')


def extract_json(src: str) -> Iterator[StringHit]:
    """Giá trị chuỗi (không phải khoá) trong JSON, kèm số dòng."""
    for no, row in enumerate(src.split("\n"), start=1):
        for m in _JSON_STRING.finditer(row):
            if m.group(2):
                continue
            try:
                yield StringHit(no, json.loads('"' + m.group(1) + '"'))
            except ValueError:
                yield StringHit(no, m.group(1))


_SQL_STRING = re.compile(r"\$\$.*?\$\$|'(?:[^']|'')*'", re.DOTALL)


def extract_sql(src: str) -> Iterator[StringHit]:
    """Chuỗi '…' và $$…$$ trong SQL, bỏ chú thích `--` và `/* */` (thay bằng khoảng trắng để giữ số dòng)."""

    def blank(m: re.Match[str]) -> str:
        return re.sub(r"[^\n]", " ", m.group(0))

    clean = re.sub(r"/\*.*?\*/", blank, re.sub(r"--[^\n]*", blank, src), flags=re.DOTALL)
    for m in _SQL_STRING.finditer(clean):
        raw = m.group(0)
        yield StringHit(clean.count("\n", 0, m.start()) + 1, raw.strip("'$").replace("''", "'"))


def extract_strings(rel: str, src: str) -> Iterator[StringHit]:
    if rel.endswith(".py"):
        yield from extract_python(src)
    elif rel.endswith((".ts", ".tsx")):
        yield from extract_typescript(src)
    elif rel.endswith(".json"):
        yield from extract_json(src)
    elif rel.endswith(".sql"):
        yield from extract_sql(src)


# ── Cấu hình + quét ─────────────────────────────────────────────────────────────────────────────────────────────
class Rule(NamedTuple):
    id: str
    regex: re.Pattern[str]
    use: str
    static: bool
    crawl: bool
    multiword_only: bool
    allow_files: tuple[str, ...]


def load_terms(path: Path = TERMS_PATH) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("terms.json phải là một đối tượng")
    return data


def compile_rules(terms: dict[str, Any]) -> list[Rule]:
    rules: list[Rule] = []
    seen: set[str] = set()
    canonical = [_norm(c) for c in terms.get("canonical", [])]
    for raw in terms.get("forbidden", []):
        rid = raw["id"]
        if rid in seen:
            raise ValueError(f"mẫu cấm trùng id: {rid}")
        seen.add(rid)
        flags = re.IGNORECASE if raw.get("ignorecase") else 0
        regex = re.compile(_norm(raw["pattern"]), flags)
        for term in canonical:
            if regex.search(term):
                raise ValueError(f"mẫu cấm '{rid}' lại cấm từ chuẩn '{term}'")
        rules.append(
            Rule(
                id=rid,
                regex=regex,
                use=raw.get("use", ""),
                static=bool(raw.get("static", True)),
                crawl=bool(raw.get("crawl", False)),
                multiword_only=bool(raw.get("multiword_only", False)),
                allow_files=tuple(raw.get("allow_files", [])),
            )
        )
    return rules


def in_scope(rel: str) -> bool:
    parts = rel.split("/")
    if any(p in SKIP_PARTS for p in parts):
        return False
    if rel.endswith(SKIP_SUFFIXES) or not rel.endswith(SCAN_SUFFIXES):
        return False
    return rel.startswith(SCOPE_DIRS)


def list_files(root: Path) -> list[str]:
    """Tệp git theo dõi trong phạm vi; không có git (thư mục tạm của test) thì duyệt thư mục."""
    rels: list[str] | None = None
    if (root / ".git").exists():
        try:
            out = subprocess.run(
                ["git", "-C", str(root), "ls-files", "-z"], check=True, capture_output=True, timeout=60
            ).stdout.decode("utf-8")
            rels = [r for r in out.split("\0") if r]
        except (OSError, subprocess.SubprocessError):
            rels = None
    if rels is None:
        rels = [p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()]
    return sorted(r for r in rels if in_scope(r))


def scan(root: Path, rules: Sequence[Rule]) -> dict[tuple[str, str], tuple[int, int, str]]:
    """{(mẫu, tệp): (số lần khớp, dòng đầu, trích dòng đầu)} cho các mẫu `static`."""
    active = [r for r in rules if r.static]
    found: dict[tuple[str, str], tuple[int, int, str]] = {}
    for rel in list_files(root):
        path = root / rel
        try:
            if path.stat().st_size > MAX_BYTES:
                continue
            src = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        lines = src.split("\n")
        counts: Counter[str] = Counter()
        first: dict[str, tuple[int, str]] = {}
        for hit in extract_strings(rel, src):
            if 0 < hit.line_no <= len(lines) and ALLOW_MARK in lines[hit.line_no - 1]:
                continue
            text = _norm(hit.text)
            for rule in active:
                if rule.multiword_only and not re.search(r"\s", text.strip()):
                    continue
                if any(rel == a or rel.startswith(a) for a in rule.allow_files):
                    continue
                n = len(rule.regex.findall(text))
                if n:
                    counts[rule.id] += n
                    first.setdefault(rule.id, (hit.line_no, " ".join(text.split())[:120]))
        for rid, n in counts.items():
            found[(rid, rel)] = (n, *first[rid])
    return found


def compare(
    found: dict[tuple[str, str], tuple[int, int, str]], baseline: dict[str, dict[str, int]], rules: Sequence[Rule]
) -> tuple[list[Violation], list[Violation]]:
    """(vượt baseline, baseline dư) — hai danh sách đều khiến kiểm hỏng."""
    over: list[Violation] = []
    stale: list[Violation] = []
    known = {r.id for r in rules if r.static}
    for (rid, rel), (n, line_no, text) in sorted(found.items()):
        allowed = baseline.get(rid, {}).get(rel, 0)
        if n > allowed:
            over.append(Violation(rid, rel, n, allowed, line_no, text))
    for rid, files in sorted(baseline.items()):
        if rid not in known:
            for rel, allowed in sorted(files.items()):
                stale.append(Violation(rid, rel, 0, allowed, 0, "mẫu không còn trong terms.json"))
            continue
        for rel, allowed in sorted(files.items()):
            n = found.get((rid, rel), (0, 0, ""))[0]
            if n < allowed:
                stale.append(Violation(rid, rel, n, allowed, 0, ""))
    return over, stale


def lowered_baseline(
    found: dict[tuple[str, str], tuple[int, int, str]], baseline: dict[str, dict[str, int]]
) -> dict[str, dict[str, int]]:
    """Baseline sau khi hạ: mỗi dòng = min(cũ, thực tế); dòng về 0 bị xoá. Không bao giờ nâng."""
    out: dict[str, dict[str, int]] = {}
    for rid, files in sorted(baseline.items()):
        for rel, allowed in sorted(files.items()):
            n = min(allowed, found.get((rid, rel), (0, 0, ""))[0])
            if n > 0:
                out.setdefault(rid, {})[rel] = n
    return out


def main(argv: Sequence[str], terms_path: Path = TERMS_PATH) -> int:
    args = [a for a in argv[1:] if not a.startswith("--")]
    lower = "--lower" in argv[1:]
    root = Path(args[0]).resolve() if args else REPO
    if not root.is_dir():
        print(f"Không thấy thư mục gốc: {root}")
        return 2
    try:
        terms = load_terms(terms_path)
        rules = compile_rules(terms)
    except (OSError, ValueError, KeyError, re.error) as exc:
        print(f"terms.json không hợp lệ: {exc}")
        return 2
    baseline: dict[str, dict[str, int]] = terms.get("baseline", {})
    found = scan(root, rules)

    over, stale = compare(found, baseline, rules)
    if lower:
        if over:
            print("Không hạ được baseline khi còn vi phạm mới — sửa chữ trước.")
        else:
            terms["baseline"] = lowered_baseline(found, baseline)
            terms_path.write_text(json.dumps(terms, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            print(f"Đã hạ baseline: {sum(len(v) for v in terms['baseline'].values())} dòng còn lại.")
            return 0
    total = sum(v[0] for v in found.values())
    by_id = {r.id: r for r in rules}
    for v in over:
        use = by_id[v.rule].use
        print(f"{v.rel}:{v.first_line}: [{v.rule}] {v.count} chỗ (baseline cho phép {v.allowed}) — «{v.first_text}»"
              + (f" → {use}" if use else ""))
        print(f"::error file={v.rel},line={v.first_line}::Thuật ngữ [{v.rule}] {v.count} chỗ, baseline {v.allowed}. {use}")
    for v in stale:
        print(f"{v.rel}: [{v.rule}] baseline ghi {v.allowed} nhưng thực tế còn {v.count} — hạ baseline (chạy với --lower)." + (f" ({v.first_text})" if v.first_text else ""))
        print(f"::error file=.github/scripts/terms.json::Baseline [{v.rule}] {v.rel} dư: ghi {v.allowed}, thực tế {v.count}")
    if over or stale:
        print(f"Lỗi: {len(over)} vượt baseline, {len(stale)} baseline dư (tổng {total} chỗ còn nợ).")
        return 1
    print(f"Sạch: không có chữ cấm mới (còn {total} chỗ nợ trong baseline, chỉ được giảm).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
