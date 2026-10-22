#!/usr/bin/env python3
"""Test cho check_doc_facts.py: tài liệu công khai phải khớp mã (F118, F041, F112).

Chạy: python3 -m unittest discover -s .github/scripts -p 'test_*.py' (CI chạy đúng lệnh này).
Mỗi bất biến có một ca "phá" trên thư mục tạm — gỡ kiểm đi thì ca đó đỏ — và một ca kiểm trên repo thật.
"""

from __future__ import annotations

import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import check_doc_facts as check

REPO = Path(__file__).resolve().parents[2]

SERVICE = '''\
from typing import Any

ROWS: tuple[dict[str, Any], ...] = (
    {"row": 0, "key": "ai", "title": "Nguồn AI", "optional": False, "checks": ["ai_source"]},
    {"row": 1, "key": "hub", "title": "Gen-hub", "optional": True, "checks": ["hub"]},
    {"row": 2, "key": "facebook", "title": "Facebook", "optional": True, "checks": ["facebook"]},
)
REQUIRED_TOTAL = sum(1 for r in ROWS if not r["optional"])
'''

README_OK = """# Gen-Harness

### Việc Sếp cần làm

Hiện có 3 dòng, chỉ 1 dòng bắt buộc:

0. **Nguồn AI** (bắt buộc) — gọi thử.
1. **Gen-hub** (tuỳ chọn). 2. **Facebook** (tuỳ chọn).

### Dùng Gen
"""

COACH_OK = """# Gen hướng dẫn

3 dòng "Việc Sếp cần làm" (1 dòng bắt buộc), nhiều việc khác.
"""

ONBOARDING_OK = "# 06 · Trình thiết lập Owner\n\n> Đã thay bởi v0.1.55 và v0.1.60 — đây là thiết kế gốc.\n"
RUNBOOK_OK = "# Sổ tay\n\n2. Cài đặt › Sao lưu & cập nhật: thẻ Sức khoẻ hệ thống.\n"
ROADMAP_OK = "# Lộ trình (cập nhật 16/10/2026, sau v0.1.61)\n\n## Tiếp theo (sau v0.1.61)\n"

ARCH_OK = "# Kiến trúc\n\n| Tệp | Volume Docker `gh_objects` | Tài liệu, tệp đính kèm. |\n"
SPEC_OK = ("# GEN-HARNESS\n## Product Specification LOCKED v2.2 — Genesis Harness OS\n\n**Phiên bản:** 2.2 LOCKED\n\n"
           "# O. QUYẾT ĐỊNH SẢN PHẨM — TRẠNG THÁI HIỆN HÀNH\n\n1. Owner.\n\n---\n\n"
           "*Gen-Harness Product Spec v2.2 LOCKED — tài liệu định hướng sản phẩm.*\n")

FILES = {
    check.BOSS_SERVICE: SERVICE,
    check.README: README_OK,
    check.GEN_COACH: COACH_OK,
    check.ONBOARDING: ONBOARDING_OK,
    check.RUNBOOK: RUNBOOK_OK,
    check.HANDOFF: "# Hiện trạng\n" + "một dòng\n" * 50,
    check.ROADMAP: ROADMAP_OK,
    check.VERSION_FILE: "v0.1.61\n",
    check.ARCHITECTURE: ARCH_OK,
    check.SPEC: SPEC_OK,
}


def make_repo(base: Path, **overrides: str) -> Path:
    for rel, body in {**FILES, **overrides}.items():
        p = base / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
    return base


def run_main(base: Path) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = check.main([str(base)])
    return code, out.getvalue(), err.getvalue()


class FixtureTest(unittest.TestCase):
    def errors(self, overrides: dict[str, str] | None = None) -> list[str]:
        with tempfile.TemporaryDirectory() as tmp:
            return check.run_checks(make_repo(Path(tmp), **(overrides or {})))

    def test_consistent_docs_pass(self) -> None:
        self.assertEqual(self.errors(), [])
        with tempfile.TemporaryDirectory() as tmp:
            code, out, _ = run_main(make_repo(Path(tmp)))
        self.assertEqual(code, 0)
        self.assertIn("khớp mã", out)

    # --- F118: số dòng bắt buộc / tổng số dòng ---------------------------------------------------------------------
    def test_stale_required_count_in_readme_is_caught(self) -> None:
        errs = self.errors({check.README: README_OK.replace("chỉ 1 dòng bắt buộc", "6 dòng bắt buộc")})
        self.assertTrue(any("README.md" in e and "6 dòng bắt buộc" in e for e in errs), errs)

    def test_stale_total_count_in_readme_is_caught(self) -> None:
        errs = self.errors({check.README: README_OK.replace("Hiện có 3 dòng", "Hiện có 9 dòng")})
        self.assertTrue(any("README.md" in e and "ghi 9 dòng" in e for e in errs), errs)

    def test_stale_counts_in_gen_coach_are_caught(self) -> None:
        bad = COACH_OK.replace("3 dòng", "9 dòng").replace("1 dòng bắt buộc", "6 dòng bắt buộc")
        errs = self.errors({check.GEN_COACH: bad})
        self.assertEqual(len([e for e in errs if "gen-coach.md" in e]), 2, errs)

    def test_missing_count_sentence_is_caught(self) -> None:
        errs = self.errors({check.README: "# Gen-Harness\n\n### Việc Sếp cần làm\n\nKhông nêu số.\n"})
        self.assertTrue(any("thiếu câu nêu tổng số" in e for e in errs), errs)
        self.assertTrue(any("thiếu câu nêu số dòng bắt buộc" in e for e in errs), errs)

    def test_too_many_required_marks_in_readme_list_are_caught(self) -> None:
        bad = README_OK.replace("(tuỳ chọn). 2.", "(bắt buộc). 2.")
        errs = self.errors({check.README: bad})
        self.assertTrue(any("gắn (bắt buộc) 2 lần" in e for e in errs), errs)

    def test_counts_follow_the_code(self) -> None:
        """Mã đổi (đổi optional) thì tài liệu cũ phải đỏ — nguồn sự thật là ROWS, không phải hằng trong test."""
        more = SERVICE.replace('"optional": True, "checks": ["facebook"]', '"optional": False, "checks": ["facebook"]')
        errs = self.errors({check.BOSS_SERVICE: more})
        self.assertTrue(any("ghi 1 dòng bắt buộc" in e and "REQUIRED_TOTAL = 2" in e for e in errs), errs)
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(check.boss_rows(make_repo(Path(tmp), **{check.BOSS_SERVICE: more})), (3, 2))

    def test_unreadable_source_is_exit_2_not_silent_pass(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = make_repo(Path(tmp), **{check.BOSS_SERVICE: "x = 1\n"})
            code, _, err = run_main(base)
            self.assertEqual(code, 2)
            self.assertIn("ROWS", err)
            (base / check.BOSS_SERVICE).write_text(SERVICE, encoding="utf-8")
            (base / check.README).unlink()
            self.assertEqual(run_main(base)[0], 2)

    # --- F041: bản thiết kế gốc phải ghi đã bị thay ---------------------------------------------------------------
    def test_onboarding_without_replaced_notice_is_caught(self) -> None:
        errs = self.errors({check.ONBOARDING: "# 06 · Trình thiết lập Owner\n\n12 bước, bước 4–9 bắt buộc.\n"})
        self.assertTrue(any("đã thay bởi" in e for e in errs), errs)

    # --- F112: runbook không chỉ đường cũ -------------------------------------------------------------------------
    def test_runbook_old_paths_are_caught(self) -> None:
        old_a = "2. Console → **Tổng quan**: dải **Cần Sếp xử lý** và thẻ **Sức khoẻ hệ thống**.\n"
        old_b = "- Console → **Cập nhật ngay** (Trợ giúp, hoặc Cài đặt › Sao lưu & cập nhật).\n"
        self.assertEqual(len(self.errors({check.RUNBOOK: RUNBOOK_OK + old_a})), 1)
        self.assertEqual(len(self.errors({check.RUNBOOK: RUNBOOK_OK + old_b})), 1)
        self.assertEqual(len(self.errors({check.RUNBOOK: RUNBOOK_OK + old_a + old_b})), 2)

    # --- HANDOFF ≤ 200 dòng ---------------------------------------------------------------------------------------
    def test_handoff_line_limit(self) -> None:
        self.assertEqual(self.errors({check.HANDOFF: "x\n" * 200}), [])
        errs = self.errors({check.HANDOFF: "x\n" * 201})
        self.assertTrue(any("201 dòng" in e for e in errs), errs)

    # --- ROADMAP "sau vX" == VERSION -------------------------------------------------------------------------------
    def test_roadmap_title_must_match_version(self) -> None:
        errs = self.errors({check.VERSION_FILE: "v0.1.62\n"})
        self.assertEqual(len(errs), 2, errs)  # cả tiêu đề đầu lẫn "Tiếp theo (sau v0.1.61)"
        self.assertTrue(all("v0.1.61" in e and "v0.1.62" in e for e in errs), errs)

    def test_roadmap_without_any_after_heading_is_caught(self) -> None:
        errs = self.errors({check.ROADMAP: "# Lộ trình\n\n## Tiếp theo\n"})
        self.assertTrue(any("không có tiêu đề nào dạng 'sau vX.Y.Z'" in e for e in errs), errs)

    def test_roadmap_prerelease_version_is_matched_whole(self) -> None:
        ok = self.errors({check.VERSION_FILE: "v0.1.62-rc.1\n", check.ROADMAP: "# Lộ trình (sau v0.1.62-rc.1)\n"})
        self.assertEqual(ok, [])


    # --- F119: HANDOFF chỉ MỘT danh sách "Sếp cần làm", không còn dòng "Phát hành vX" của bản đã qua -------------------
    def test_handoff_single_boss_list(self) -> None:
        one = "# H\n\n### Sếp cần làm bây giờ (MỘT danh sách)\n\n1. Việc.\n"
        self.assertEqual(self.errors({check.HANDOFF: one}), [])
        two = one + "\n### Boss phải làm — v0.1.60 (không cần làm gì)\n\n1. Không.\n"
        errs = self.errors({check.HANDOFF: two})
        self.assertTrue(any("MỘT danh sách" in e and "2 khối" in e for e in errs), errs)
        many = "# H\n" + "".join(f"### Boss phải làm — v0.1.{n}\n" for n in (50, 53, 54))
        self.assertTrue(any("3 khối" in e for e in self.errors({check.HANDOFF: many})))

    def test_handoff_stale_release_line_is_caught(self) -> None:
        stale = "# H\n\n- **Phát hành v0.1.57**: push → PR → báo Boss.\n"
        errs = self.errors({check.HANDOFF: stale})        # VERSION là v0.1.61 ⇒ v0.1.57 đã qua
        self.assertTrue(any("Phát hành v0.1.57" in e and "v0.1.61" in e for e in errs), errs)
        # Bản ĐANG chờ phát hành (đúng bằng VERSION) hoặc mới hơn thì hợp lệ.
        self.assertEqual(self.errors({check.HANDOFF: "# H\n\n- **Phát hành v0.1.61**: chờ.\n- Phát hành v0.1.62 sau.\n"}), [])

    # --- F120: ARCHITECTURE không còn MinIO; Product Spec đồng nhất phiên bản, không còn câu hỏi đã chốt ---------------
    def test_architecture_must_not_mention_minio(self) -> None:
        errs = self.errors({check.ARCHITECTURE: ARCH_OK + "| objects | MinIO |\n"})
        self.assertTrue(any("MinIO" in e for e in errs), errs)
        self.assertTrue(any("minio" in e.lower() for e in self.errors({check.ARCHITECTURE: ARCH_OK + "minio-1\n"})))

    def test_spec_footer_version_must_match_header(self) -> None:
        errs = self.errors({check.SPEC: SPEC_OK.replace("Product Spec v2.2 LOCKED", "Product Spec v2.0")})
        self.assertTrue(any("v2.0" in e and "v2.2" in e for e in errs), errs)
        errs = self.errors({check.SPEC: SPEC_OK.replace("# O. QUYẾT ĐỊNH SẢN PHẨM — TRẠNG THÁI HIỆN HÀNH", "# O. NHỮNG GÌ CẦN CHỐT TIẾP Ở MỨC SẢN PHẨM")})
        self.assertTrue(any("CẦN CHỐT TIẾP" in e for e in errs), errs)


class LiveRepoTest(unittest.TestCase):
    """Tài liệu thật của repo phải khớp mã thật (đây là phép kiểm CI chạy)."""

    def test_repo_docs_match_code(self) -> None:
        self.assertEqual(check.run_checks(REPO), [])

    def test_boss_rows_of_real_service(self) -> None:
        total, required = check.boss_rows(REPO)
        self.assertGreaterEqual(total, 1)
        self.assertGreaterEqual(required, 1)
        self.assertLessEqual(required, total)

    def test_readme_names_the_real_numbers(self) -> None:
        total, required = check.boss_rows(REPO)
        text = (REPO / check.README).read_text(encoding="utf-8")
        self.assertIn(f"Hiện có {total} dòng", text)
        self.assertEqual(check._numbers(text, check.REQUIRED_PATTERNS), [required])


if __name__ == "__main__":
    unittest.main()
