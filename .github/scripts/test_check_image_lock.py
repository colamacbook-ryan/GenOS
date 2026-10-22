#!/usr/bin/env python3
"""Test cho check_image_lock.py — chạy: python3 -m unittest discover -s .github/scripts -p 'test_*.py'."""

from __future__ import annotations

import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import check_image_lock as chk  # noqa: E402

LOCK = """\
version = 1
requires-python = ">=3.11"

[[package]]
name = "gen-harness-api"
version = "0.1.48"
source = { editable = "." }
dependencies = [
    { name = "fastapi" },
    { name = "pyyaml" },
    { name = "typing-extensions" },
    { name = "pywin32", marker = "sys_platform == 'win32'" },
]

[package.optional-dependencies]
dev = [{ name = "pytest" }]

[[package]]
name = "fastapi"
version = "0.115.0"
source = { registry = "https://pypi.org/simple" }

[[package]]
name = "PyYAML"
version = "6.0.2"
source = { registry = "https://pypi.org/simple" }

[[package]]
name = "typing_extensions"
version = "4.12.2"
source = { registry = "https://pypi.org/simple" }

[[package]]
name = "pytest"
version = "8.3.0"
source = { registry = "https://pypi.org/simple" }
"""

GOOD = "fastapi==0.115.0\nPyYAML==6.0.2\ntyping_extensions==4.12.2\ngen-harness-api==0.1.48\n"


class ImageLockTest(unittest.TestCase):
    def run_check(self, *freezes: str, lock: str = LOCK, missing: bool = False) -> tuple[int, str, str]:
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "uv.lock").write_text(lock, encoding="utf-8")
            paths = []
            for i, body in enumerate(freezes, 1):
                p = d / f"f{i}.txt"
                p.write_text(body, encoding="utf-8")
                paths.append(str(p))
            if missing:
                paths.append(str(d / "khong-co.txt"))
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                try:
                    code = chk.main(["--lock", str(d / "uv.lock"), "--project", "gen-harness-api", *paths])
                except SystemExit as e:
                    code = int(e.code or 0)
            return code, out.getvalue(), err.getvalue()

    def test_khop_hai_lan_build(self) -> None:
        code, out, err = self.run_check(GOOD, GOOD)
        self.assertEqual(code, 0, err)
        self.assertIn("OK: 3 gói khớp uv.lock qua 2 lần build", out)

    def test_hai_freeze_khac_nhau(self) -> None:
        code, _, err = self.run_check(GOOD, GOOD.replace("fastapi==0.115.0", "fastapi==0.115.0\nrequests==2.0"))
        self.assertEqual(code, 1)
        self.assertIn("::error::", err)
        self.assertIn("tổ hợp gói khác nhau", err)
        self.assertIn("requests", err)

    def test_goi_khac_phien_ban(self) -> None:
        bad = GOOD.replace("fastapi==0.115.0", "fastapi==0.116.0")
        code, _, err = self.run_check(bad, bad)
        self.assertEqual(code, 1)
        self.assertIn("fastapi phiên bản 0.116.0", err)

    def test_goi_la_khong_co_trong_lock(self) -> None:
        bad = GOOD + "evil-pkg==1.0\n"
        code, _, err = self.run_check(bad, bad)
        self.assertEqual(code, 1)
        self.assertIn("evil-pkg==1.0", err)
        self.assertIn("KHÔNG có trong", err)

    def test_thieu_dependency_truc_tiep(self) -> None:
        bad = GOOD.replace("fastapi==0.115.0\n", "")
        code, _, err = self.run_check(bad, bad)
        self.assertEqual(code, 1)
        self.assertIn("dependency trực tiếp fastapi", err)

    def test_dependency_co_marker_khong_bat_buoc(self) -> None:
        # pywin32 chỉ cho Windows (có marker) — vắng trong ảnh Linux là đúng.
        code, _, err = self.run_check(GOOD, GOOD)
        self.assertEqual(code, 0, err)
        self.assertNotIn("pywin32", err)

    def test_ten_chuan_hoa(self) -> None:
        # PyYAML vs pyyaml, typing_extensions vs typing-extensions đều khớp.
        alt = "FastAPI==0.115.0\npyyaml==6.0.2\ntyping-extensions==4.12.2\nGen_Harness_API==0.1.48\n"
        code, _, err = self.run_check(alt, GOOD)
        self.assertEqual(code, 0, err)

    def test_chinh_du_an_trong_freeze_duoc_bo_qua(self) -> None:
        no_proj = "fastapi==0.115.0\nPyYAML==6.0.2\ntyping_extensions==4.12.2\n"
        code, _, err = self.run_check(no_proj, no_proj)
        self.assertEqual(code, 0, err)

    def test_thieu_tep_freeze(self) -> None:
        code, _, err = self.run_check(GOOD, missing=True)
        self.assertEqual(code, 2)
        self.assertIn("không thấy tệp freeze", err)

    def test_thieu_uv_lock(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "f.txt"
            f.write_text(GOOD, encoding="utf-8")
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as cm:
                chk.main(["--lock", str(Path(tmp) / "khong-co.lock"), "--project", "x", str(f)])
            self.assertEqual(cm.exception.code, 2)

    def test_uv_lock_hong(self) -> None:
        code, _, err = self.run_check(GOOD, lock="đây không phải [toml")
        self.assertEqual(code, 2)
        self.assertIn("uv.lock hỏng", err)


if __name__ == "__main__":
    unittest.main()
