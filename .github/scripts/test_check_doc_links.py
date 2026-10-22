#!/usr/bin/env python3
"""Test cho check_doc_links.py + các bất biến tài liệu của v0.1.50 (F-90, F-69, F-47).

Chạy: python3 -m unittest discover -s .github/scripts -p 'test_*.py' (CI chạy đúng lệnh này).
"""

from __future__ import annotations

import contextlib
import io
import re
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import check_doc_links as check  # noqa: E402

REPO = Path(__file__).resolve().parents[2]


def write(base: Path, files: dict[str, str]) -> None:
    for rel, body in files.items():
        p = base / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")


def run_main(base: Path) -> tuple[int, str]:
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = check.main(["check_doc_links.py", str(base)])
    return code, out.getvalue()


class DocLinksTempDirTest(unittest.TestCase):
    def test_live_links_pass(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            write(base, {
                "README.md": "[a](docs/a.md) [b](docs/a.md#tiêu-đề-một) [thư mục](docs/design/) [ngoài](https://example.com/x.md)\n"
                             "[mail](mailto:a@b.c) [gốc](/abs/path.md) [cùng tệp](#mục-hai)\n\n## Mục hai\n",
                "docs/a.md": "# Tiêu đề một\n\n[về README](../README.md)\n",
                "docs/design/g.md": "# G\n",
                "CHANGELOG.md": "# Nhật ký\n[chi tiết](docs/releases/v0.1.1.md \"tiêu đề\")\n",
                "docs/releases/v0.1.1.md": "# v0.1.1\n[lên](../../CHANGELOG.md)\n",
            })
            code, out = run_main(base)
            self.assertEqual(code, 0, out)
            self.assertIn("OK", out)

    def test_dead_file_link_fails_with_location(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            write(base, {"README.md": "dòng 1\n[sống](docs/a.md) và [chết](docs/missing.md)\n", "docs/a.md": "# A\n"})
            code, out = run_main(base)
            self.assertEqual(code, 1)
            self.assertIn("README.md:2", out)
            self.assertIn("docs/missing.md", out)
            self.assertIn("::error", out)
            self.assertNotIn("docs/a.md)", out)

    def test_dead_anchor_fails_but_live_anchor_passes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            write(base, {
                "README.md": "[ok](docs/a.md#phần-hai) [hỏng](docs/a.md#khong-co) [cùng tệp hỏng](#khong-co-nua)\n",
                "docs/a.md": "# Một\n\n## Phần hai\n\n## Phần hai\n",
            })
            dead = check.dead_links(base)
            self.assertEqual(sorted(d.target for d in dead), ["#khong-co-nua", "docs/a.md#khong-co"])

    def test_duplicate_headings_get_numbered_anchors(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            write(base, {"README.md": "[hai](docs/a.md#phần-hai-1)\n", "docs/a.md": "## Phần hai\n\n## Phần hai\n"})
            self.assertEqual(check.dead_links(base), [])

    def test_links_inside_code_are_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            body = ("Cú pháp `[x](khong/co.md)` chỉ là ví dụ.\n\n```\n[y](cung/khong/co.md)\n```\n\n"
                    "~~~\n[z](nua.md)\n~~~\n")
            write(base, {"README.md": body})
            code, out = run_main(base)
            self.assertEqual(code, 0, out)

    def test_link_with_query_percent_and_angle_brackets(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            write(base, {
                "README.md": "[a](<docs/có dấu cách.md>) [b](docs/c%C3%B3%20d%E1%BA%A5u%20c%C3%A1ch.md?x=1) "
                             "[c](docs/missing%20x.md)\n",
                "docs/có dấu cách.md": "# x\n",
            })
            dead = check.dead_links(base)
            self.assertEqual([d.target for d in dead], ["docs/missing%20x.md"])

    def test_link_escaping_repo_root_is_dead(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "repo"
            write(base, {"README.md": "[ra ngoài](../ngoai.md)\n"})
            (Path(tmp) / "ngoai.md").write_text("# n\n", encoding="utf-8")
            dead = check.dead_links(base)
            self.assertEqual(len(dead), 1)
            self.assertIn("ngoài repo", dead[0].reason)

    def test_only_listed_documents_are_scanned(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            write(base, {
                "docs/audit/x.md": "[chết](khong.md)\n",
                "docs/reports/phase-1.md": "[chết](khong.md)\n",
                "docs/reports/HANDOFF-v0.1.1.md": "[chết](khong.md)\n",
            })
            dead = check.dead_links(base)
            self.assertEqual([d.path.name for d in dead], ["HANDOFF-v0.1.1.md"])

    def test_missing_root_exits_2(self) -> None:
        code, out = run_main(Path("/khong/ton/tai/gh-doc-links"))
        self.assertEqual(code, 2)
        self.assertIn("::error", out)


class RepoDocsInvariantTest(unittest.TestCase):
    """Chạy trên repo thật."""

    def test_real_repo_has_no_dead_links(self) -> None:
        dead = check.dead_links(REPO)
        self.assertEqual([f"{d.path.relative_to(REPO)}:{d.line} → {d.target} ({d.reason})" for d in dead], [])

    def test_real_repo_main_exits_0(self) -> None:
        code, out = run_main(REPO)
        self.assertEqual(code, 0, out)

    def test_handoff_stays_within_200_lines(self) -> None:
        n = len((REPO / "docs/reports/HANDOFF-v0.1.1.md").read_text(encoding="utf-8").splitlines())
        self.assertLessEqual(n, 200, f"HANDOFF {n} dòng — chuyển phần lịch sử sang docs/releases/ và CHANGELOG.md")

    def test_handoff_keeps_headings_the_code_points_to(self) -> None:
        text = (REPO / "docs/reports/HANDOFF-v0.1.1.md").read_text(encoding="utf-8")
        self.assertTrue(text.startswith("# "))
        self.assertIn("## Hợp đồng chung", text)
        self.assertIn("GH_ADMIN_DATABASE_URL", text)
        self.assertIn("GH_APP_DB_PASSWORD", text)

    def test_claude_md_within_60_lines(self) -> None:
        n = len((REPO / "CLAUDE.md").read_text(encoding="utf-8").splitlines())
        self.assertLessEqual(n, 60, f"CLAUDE.md {n} dòng")

    def test_changelog_has_every_version_heading_v0_1_28_to_v0_1_74(self) -> None:
        text = (REPO / "CHANGELOG.md").read_text(encoding="utf-8")
        found = [int(m.group(1)) for m in re.finditer(r"^## v0\.1\.(\d+)\b", text, flags=re.MULTILINE)]
        self.assertEqual(found, list(range(74, 27, -1)),
                         "tiêu đề '## v0.1.N' phải đủ v0.1.74 … v0.1.28, mới nhất trên cùng")
        for n in found:
            self.assertIn(f"docs/releases/v0.1.{n}.md", text, f"CHANGELOG thiếu link chi tiết v0.1.{n}")

    def test_every_release_has_a_file(self) -> None:
        for n in range(1, 74):
            self.assertTrue((REPO / f"docs/releases/v0.1.{n}.md").is_file(), f"thiếu docs/releases/v0.1.{n}.md")

    def test_release_files_have_at_most_one_version_h2(self) -> None:
        # v0.1.30 gộp hai mục trùng tên thành MỘT tệp (hai tiểu mục), không còn hai tiêu đề '## v0.1.30'.
        text = (REPO / "docs/releases/v0.1.30.md").read_text(encoding="utf-8")
        self.assertLessEqual(len(re.findall(r"^## v0\.1\.30", text, flags=re.MULTILINE)), 1)
        self.assertEqual(len(re.findall(r"^## Tiểu mục \d", text, flags=re.MULTILINE)), 2)

    def test_v0_1_29_notes_the_slipped_promise(self) -> None:
        text = (REPO / "docs/releases/v0.1.29.md").read_text(encoding="utf-8")
        self.assertIn("> Ghi chú 2026-10: lời hẹn này đã trượt — xem ROADMAP mục Nợ", text)

    def test_roadmap_has_debt_and_hotfix_sections(self) -> None:
        text = (REPO / "docs/ROADMAP.md").read_text(encoding="utf-8")
        self.assertRegex(text, r"(?m)^## Nợ\s*$")
        self.assertRegex(text, r"(?m)^## Bản phản ứng \(hotfix\)\s*$")

    def test_design_docs_open_with_a_status_line_and_drop_retired_material(self) -> None:
        for name in ("gen-v1.md", "gen-hub-link.md", "gen-browser-agent.md"):
            lines = (REPO / "docs/design" / name).read_text(encoding="utf-8").splitlines()
            self.assertTrue(lines[0].startswith("# "), name)
            self.assertEqual(lines[1], "", name)
            self.assertTrue(lines[2].startswith("> Trạng thái (10/2026)"),
                            f"{name}: dòng đầu sau tiêu đề phải là dòng trạng thái")
            body = "\n".join(lines)
            self.assertNotIn("Jules", body, name)
            self.assertNotIn("không dùng Playwright", body, name)

    def test_idempotency_key_is_marked_unimplemented(self) -> None:
        for rel in ("docs/ARCHITECTURE.md", "docs/PLAN.md", "docs/api/phase-1.md"):
            for n, line in enumerate((REPO / rel).read_text(encoding="utf-8").splitlines(), start=1):
                if "Idempotency-Key" in line:
                    self.assertIn("chưa thi hành", line, f"{rel}:{n}")


if __name__ == "__main__":
    unittest.main()
