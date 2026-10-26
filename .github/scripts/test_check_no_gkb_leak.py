#!/usr/bin/env python3
"""Test cho check_no_gkb_leak.py — chạy: python3 -m unittest discover -s .github/scripts -p 'test_*.py'."""

from __future__ import annotations

import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import check_no_gkb_leak as check  # noqa: E402


class NoGkbLeakTest(unittest.TestCase):
    def run_check(self, fixture: str | None, extra: dict[str, str] | None = None) -> tuple[int, str]:
        out = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            files = dict(extra or {})
            if fixture is not None:
                files[str(check.FIXTURE)] = fixture
            for rel, body in files.items():
                p = root / rel
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(body, encoding="utf-8")
            with contextlib.redirect_stdout(out):
                rc = check.main(["x", str(root)])
        return rc, out.getvalue()

    def test_clean_fixture_passes(self) -> None:
        self.assertEqual(self.run_check('{"desc": "Nhân viên nhập OTP"}')[0], 0)

    def test_iso_time_not_flagged(self) -> None:
        self.assertEqual(self.run_check('{"now": "2026-10-23T08:00:00+07:00"}')[0], 0)

    def test_each_pattern_fails(self) -> None:
        for bad in ("kb3", "ca S25", "ca T14", "ALR-12", "m0123", "xem context.md"):
            with self.subTest(bad=bad):
                rc, out = self.run_check('{"desc": "%s"}' % bad)
                self.assertEqual(rc, 1, out)

    def test_bare_T_code_still_fails_next_to_iso_time(self) -> None:
        self.assertEqual(self.run_check('{"desc": "T14", "now": "2026-10-23T08:00:00"}')[0], 1)

    def test_missing_fixture_fails(self) -> None:
        self.assertEqual(self.run_check(None)[0], 1)

    def test_forbidden_files_fail(self) -> None:
        for name in ("int19-edge-cases.jsonl", "sub/chats.jsonl"):
            with self.subTest(name=name):
                self.assertEqual(self.run_check("{}", {name: ""})[0], 1)

    def test_real_repo_is_clean(self) -> None:
        self.assertEqual(check.check(check.REPO), [])


if __name__ == "__main__":
    unittest.main()
