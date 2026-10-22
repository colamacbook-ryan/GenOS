#!/usr/bin/env python3
"""Test cho check_terms.py (lưới L5) — chạy: python3 -m unittest discover -s .github/scripts -p 'test_*.py'.

Gồm: (1) bộ trích chuỗi hiển thị cho .ts/.tsx/.py/.json/.sql, (2) từng mẫu cấm của terms.json bắt được câu sai và
tha câu đúng, (3) ratchet (vượt baseline ⇒ hỏng; baseline dư ⇒ hỏng; `--lower` chỉ hạ), (4) repo thật phải sạch.
"""

from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import check_terms as ct  # noqa: E402


def texts(rel: str, src: str) -> list[str]:
    return [h.text for h in ct.extract_strings(rel, src)]


class ExtractTest(unittest.TestCase):
    def test_typescript_strings_templates_and_comments(self) -> None:
        src = (
            "// khóa trong chú thích không tính\n"
            "/* Kho tri thức trong chú thích khối\n   cũng không tính */\n"
            "const a = 'Mở khoá ở đây';\n"
            'const b = "Hai \\"nháy\\" kép";\n'
            "const c = `Xin chào ${name} — nhập PIN ${n > 1 ? `lần ${n}` : 'một lần'}`;\n"
            "const url = 'http://x.test/a'; // đuôi chú thích\n"
        )
        got = texts("apps/web/src/a.ts", src)
        self.assertIn("Mở khoá ở đây", got)
        self.assertIn('Hai "nháy" kép', got)
        self.assertTrue(any("nhập PIN" in t for t in got), got)
        self.assertIn("lần ", got)
        self.assertIn("một lần", got)
        self.assertIn("http://x.test/a", got)
        self.assertFalse(any("chú thích" in t for t in got), got)

    def test_typescript_line_numbers(self) -> None:
        src = "const a = 1;\n\nconst b = 'dòng ba';\nconst c = `nhiều\ndòng`;\n"
        hits = {h.text: h.line_no for h in ct.extract_typescript(src)}
        self.assertEqual(hits["dòng ba"], 3)
        self.assertEqual(hits["nhiều\ndòng"], 4)

    def test_typescript_jsx_text(self) -> None:
        src = (
            "export const X = () => (\n"
            "  <div className=\"gh-x\">\n"
            "    <h1>Kho tri thức của Sếp</h1>\n"
            "    <p>\n"
            "      Hai lần nhập PIN chưa khớp\n"
            "      và dòng nữa.\n"
            "    </p>\n"
            "    {count > 0 && <b>{count}</b>}\n"
            "  </div>\n"
            ");\n"
        )
        got = texts("apps/web/src/X.tsx", src)
        self.assertIn("Kho tri thức của Sếp", got)
        self.assertIn("Hai lần nhập PIN chưa khớp", got)
        self.assertNotIn("gh-x", " ".join(g for g in got if g != "gh-x"))

    def test_python_skips_docstrings_and_comments(self) -> None:
        src = (
            '"""Docstring module nhắc khóa."""\n'
            "# chú thích nhắc Kho tri thức\n"
            "class A:\n"
            '    """Docstring lớp: nhập PIN."""\n'
            "    def f(self) -> str:\n"
            '        """Docstring hàm: Nghe im lặng."""\n'
            '        return "Cần 1 khóa API"\n'
        )
        got = texts("apps/api/gh/a.py", src)
        self.assertEqual(got, ["Cần 1 khóa API"])

    def test_json_values_not_keys(self) -> None:
        src = '{\n  "khóa": "giá trị có khóa",\n  "list": ["một", "hai \\"x\\""]\n}\n'
        got = texts("apps/api/gh/x.json", src)
        self.assertEqual(got, ["giá trị có khóa", "một", 'hai "x"'])

    def test_sql_strings_without_comments(self) -> None:
        src = (
            "-- Kho tri thức trong chú thích\n"
            "/* nhập PIN\n   chú thích khối */\n"
            "UPDATE t SET note = 'Kho tri thức (chỉ đọc)'\n"
            " WHERE x = 'it''s';\n"
        )
        hits = list(ct.extract_sql(src))
        self.assertEqual([h.text for h in hits], ["Kho tri thức (chỉ đọc)", "it's"])
        self.assertEqual(hits[0].line_no, 4)


class RulesTest(unittest.TestCase):
    """Mỗi mẫu cấm của terms.json: câu sai bị bắt, câu chuẩn được tha."""

    BAD_GOOD: dict[str, tuple[list[str], list[str]]] = {
        "khoa-dau-hoi": (["Cần 1 khóa API", "Khóa OpenRouter"], ["Cần 1 khoá API", "khóa học", "mở khoá"]),
        "kho-tri-thuc": (["Gen đọc và ghi Kho tri thức"], ["Gen đọc và ghi Kho dữ liệu", "Kho của Sếp"]),
        "kho-ten-rieng": (["Gen ghi " + "Kho R" + "yan"], ["Gen ghi Kho dữ liệu"]),
        "nhap-pin": (["Hai lần nhập PIN chưa khớp", "Nhập PIN"], ["Hai lần nhập mã PIN chưa khớp", "Mã PIN hết hạn"]),
        "agent-model-ten-man": (["vào Agent & Model để chọn"], ["vào API & Model để chọn"]),
        "vai-tieng-anh": (["đổi vai trò thành Manager", "Auditor không có quyền ghi"], ["thay Account Manager", "Quản lý"]),
        "nghe-im-lang": (["chế độ nghe im lặng", "Nghe im lặng"], ["chế độ Lắng nghe im lặng"]),
        "value-error": (["Value error, mật khẩu ngắn"], ["Mật khẩu quá ngắn"]),
        "trang-thai-tho": (["Trạng thái: failed", "approved"], ["Trạng thái: thất bại", "Đã duyệt"]),
        "tu-ky-thuat": (["Tìm chat_id", "trình cài (TUI)", "Prefill từ mẫu", "kho_update — Sửa"], ["Tìm mã cuộc trò chuyện"]),
    }

    def setUp(self) -> None:
        self.terms = ct.load_terms()
        self.rules = {r.id: r for r in ct.compile_rules(self.terms)}

    def test_every_rule_is_covered_by_a_sample(self) -> None:
        self.assertEqual(set(self.rules), set(self.BAD_GOOD), "mẫu cấm mới phải có câu sai/đúng trong test")

    def test_bad_caught_and_good_spared(self) -> None:
        for rid, (bad, good) in self.BAD_GOOD.items():
            rx = self.rules[rid].regex
            for text in bad:
                self.assertTrue(rx.search(ct._norm(text)), f"{rid} phải bắt: {text}")
            for text in good:
                self.assertFalse(rx.search(ct._norm(text)), f"{rid} phải tha: {text}")

    def test_canonical_terms_are_never_forbidden(self) -> None:
        for term in self.terms["canonical"]:
            for rule in self.rules.values():
                self.assertFalse(rule.regex.search(ct._norm(term)), f"{rule.id} cấm từ chuẩn {term}")

    def test_compile_rejects_rule_forbidding_canonical(self) -> None:
        bad = {"canonical": ["Kho dữ liệu"], "forbidden": [{"id": "x", "pattern": "Kho dữ"}]}
        with self.assertRaises(ValueError):
            ct.compile_rules(bad)

    def test_duplicate_ids_rejected(self) -> None:
        bad = {"forbidden": [{"id": "x", "pattern": "a"}, {"id": "x", "pattern": "b"}]}
        with self.assertRaises(ValueError):
            ct.compile_rules(bad)

    def test_baseline_only_names_existing_rules_and_files(self) -> None:
        for rid, files in self.terms["baseline"].items():
            self.assertIn(rid, self.rules)
            for rel, n in files.items():
                self.assertTrue((ct.REPO / rel).is_file(), f"baseline nhắc tệp không tồn tại: {rel}")
                self.assertGreater(n, 0)


class RatchetTest(unittest.TestCase):
    RULES: list[dict[str, Any]] = [
        {"id": "kho-tri-thuc", "pattern": "Kho tri thức", "use": "Kho dữ liệu"},
        {"id": "vai", "pattern": "\\b(?:Manager|Auditor)\\b", "multiword_only": True},
        {"id": "ten-rieng", "pattern": "Tên Cấm", "allow_files": ["db/sql/"]},
    ]

    def run_main(
        self, files: dict[str, str], baseline: dict[str, dict[str, int]] | None = None, *flags: str
    ) -> tuple[int, str, dict[str, Any]]:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            for rel, body in files.items():
                p = base / "repo" / rel
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(body, encoding="utf-8")
            tpath = base / "terms.json"
            tpath.write_text(
                json.dumps({"canonical": ["Kho dữ liệu"], "forbidden": self.RULES, "baseline": baseline or {}}), encoding="utf-8"
            )
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = ct.main(["check_terms.py", str(base / "repo"), *flags], tpath)
            return code, out.getvalue(), json.loads(tpath.read_text(encoding="utf-8"))

    def test_clean_passes(self) -> None:
        code, out, _ = self.run_main({"apps/web/src/a.ts": "const a = 'Kho dữ liệu';\n"})
        self.assertEqual(code, 0, out)
        self.assertIn("Sạch", out)

    def test_new_violation_fails_with_file_line_and_hint(self) -> None:
        code, out, _ = self.run_main({"apps/web/src/a.ts": "const x = 1;\nconst a = 'Gen đọc Kho tri thức';\n"})
        self.assertEqual(code, 1)
        self.assertIn("apps/web/src/a.ts:2", out)
        self.assertIn("::error file=apps/web/src/a.ts,line=2::", out)
        self.assertIn("Kho dữ liệu", out)

    def test_baseline_allows_existing_but_not_more(self) -> None:
        files = {"apps/web/src/a.ts": "const a = 'Kho tri thức'; const b = 'Kho tri thức';\n"}
        ok, _, _ = self.run_main(files, {"kho-tri-thuc": {"apps/web/src/a.ts": 2}})
        self.assertEqual(ok, 0)
        worse, out, _ = self.run_main(files, {"kho-tri-thuc": {"apps/web/src/a.ts": 1}})
        self.assertEqual(worse, 1)
        self.assertIn("vượt baseline", out)

    def test_new_file_has_zero_allowance(self) -> None:
        files = {"apps/web/src/a.ts": "const a = 'Kho tri thức';\n", "apps/web/src/b.ts": "const b = 'Kho tri thức';\n"}
        code, out, _ = self.run_main(files, {"kho-tri-thuc": {"apps/web/src/a.ts": 1}})
        self.assertEqual(code, 1)
        self.assertIn("apps/web/src/b.ts", out)

    def test_stale_baseline_fails(self) -> None:
        code, out, _ = self.run_main(
            {"apps/web/src/a.ts": "const a = 'Kho dữ liệu';\n"}, {"kho-tri-thuc": {"apps/web/src/a.ts": 1}}
        )
        self.assertEqual(code, 1)
        self.assertIn("hạ baseline", out)

    def test_baseline_of_unknown_rule_fails(self) -> None:
        code, out, _ = self.run_main({"apps/web/src/a.ts": "const a = 1;\n"}, {"mau-da-xoa": {"apps/web/src/a.ts": 1}})
        self.assertEqual(code, 1)
        self.assertIn("không còn trong terms.json", out)

    def test_lower_only_lowers_and_drops_zero(self) -> None:
        files = {"apps/web/src/a.ts": "const a = 'Kho tri thức';\n", "apps/web/src/b.ts": "const b = 'Kho dữ liệu';\n"}
        base = {"kho-tri-thuc": {"apps/web/src/a.ts": 3, "apps/web/src/b.ts": 2}}
        code, _, saved = self.run_main(files, base, "--lower")
        self.assertEqual(code, 0)
        self.assertEqual(saved["baseline"], {"kho-tri-thuc": {"apps/web/src/a.ts": 1}})

    def test_lower_refuses_when_new_violation(self) -> None:
        files = {"apps/web/src/a.ts": "const a = 'Kho tri thức'; const b = 'Kho tri thức';\n"}
        base = {"kho-tri-thuc": {"apps/web/src/a.ts": 1}}
        code, _, saved = self.run_main(files, base, "--lower")
        self.assertEqual(code, 1)
        self.assertEqual(saved["baseline"], base, "--lower không bao giờ nâng baseline")

    def test_multiword_only_spares_bare_role_keys(self) -> None:
        src = "const r = 'Manager'; const s = \"Auditor\"; const t = 'đổi vai trò thành Manager';\n"
        code, out, _ = self.run_main({"apps/web/src/a.ts": src})
        self.assertEqual(code, 1)
        self.assertIn("1 chỗ", out, "chỉ câu nhiều từ bị tính")

    def test_allow_files_and_allow_mark(self) -> None:
        files = {
            "db/sql/0001.sql": "UPDATE t SET c = replace(c, 'Tên Cấm', 'Kho dữ liệu');\n",
            "apps/web/src/a.ts": "const a = 'Kho tri thức'; // terms-ok: nhắc để migration dọn\n",
        }
        code, out, _ = self.run_main(files)
        self.assertEqual(code, 0, out)

    def test_scope_skips_tests_mocks_docs_and_comments(self) -> None:
        files = {
            "apps/web/test/unit/a.test.tsx": "const a = 'Kho tri thức';\n",
            "apps/web/e2e/a.spec.ts": "const a = 'Kho tri thức';\n",
            "apps/api/tests/test_a.py": "A = 'Kho tri thức'\n",
            "docs/x.md": "Kho tri thức\n",
            "apps/web/src/a.ts": "// Kho tri thức chỉ trong chú thích\nexport const a = 1;\n",
        }
        code, out, _ = self.run_main(files)
        self.assertEqual(code, 0, out)

    def test_missing_root_and_bad_config(self) -> None:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(ct.main(["check_terms.py", "/khong/ton/tai/dau-ca"]), 2)
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "terms.json"
            bad.write_text(json.dumps({"forbidden": [{"id": "x", "pattern": "("}]}), encoding="utf-8")
            with contextlib.redirect_stdout(out):
                self.assertEqual(ct.main(["check_terms.py", tmp], bad), 2)


class RealRepoTest(unittest.TestCase):
    def test_repo_has_no_new_forbidden_terms(self) -> None:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = ct.main(["check_terms.py"])
        self.assertEqual(code, 0, out.getvalue())


if __name__ == "__main__":
    unittest.main()
