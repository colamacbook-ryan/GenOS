import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
SCRIPT = HERE / "check_bytecode.py"
sys.path.insert(0, str(HERE))

import check_bytecode as cb

CACHED_LOG = """\
# /usr/local/lib/python3.11/encodings/__pycache__/__init__.cpython-311.pyc matches /usr/local/lib/python3.11/encodings/__init__.py
# code object from '/usr/local/lib/python3.11/encodings/__pycache__/__init__.cpython-311.pyc'
# /opt/venv/lib/python3.11/site-packages/gh/__pycache__/__init__.cpython-311.pyc matches /opt/venv/lib/python3.11/site-packages/gh/__init__.py
# code object from '/opt/venv/lib/python3.11/site-packages/gh/__pycache__/__init__.cpython-311.pyc'
# code object from '/opt/venv/lib/python3.11/site-packages/fastapi/__pycache__/__init__.cpython-311.pyc'
import 'fastapi' # <_frozen_importlib_external.SourceFileLoader object at 0x7f>
"""

# Log thật khi venv cài bằng uv KHÔNG có UV_COMPILE_BYTECODE: module của venv dịch lại từ .py, stdlib vẫn có .pyc.
SOURCE_LOG = """\
# code object from '/usr/local/lib/python3.11/encodings/__pycache__/__init__.cpython-311.pyc'
# code object from /opt/venv/lib/python3.11/site-packages/gh/__init__.py
# code object from /opt/venv/lib/python3.11/site-packages/gh/main.py
# code object from '/opt/venv/lib/python3.11/site-packages/fastapi/__pycache__/__init__.cpython-311.pyc'
"""


def run(log: str, *extra: str) -> subprocess.CompletedProcess[str]:
    d = tempfile.mkdtemp()
    f = Path(d) / "pyv.txt"
    f.write_text(log, encoding="utf-8")
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--title", "gh-api", *extra, str(f)], capture_output=True, text=True, check=False
    )


class CheckBytecode(unittest.TestCase):
    def test_all_cached_ok(self):
        r = run(CACHED_LOG)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("2 module", r.stdout)

    def test_module_compiled_from_source_fails(self):
        r = run(SOURCE_LOG)
        self.assertEqual(r.returncode, 1)
        self.assertIn("::error::", r.stderr)
        self.assertIn("2 module", r.stderr)
        self.assertIn("gh/main.py", r.stderr)
        self.assertIn("UV_COMPILE_BYTECODE=1", r.stderr)

    def test_stdlib_outside_prefix_ignored(self):
        cached, source = cb.classify(
            "# code object from /usr/local/lib/python3.11/site.py\n"
            "# code object from '/opt/venv/lib/python3.11/site-packages/a/__pycache__/__init__.cpython-311.pyc'\n",
            "/opt/venv/",
        )
        self.assertEqual(source, [])
        self.assertEqual(len(cached), 1)

    def test_nothing_imported_is_not_clean(self):
        # Import hỏng / sai --prefix ⇒ không có dòng nào dưới prefix — KHÔNG được báo đạt.
        for log, extra in ((CACHED_LOG, ("--prefix", "/khong/co/")), ("Traceback (most recent call last):\n", ())):
            r = run(log, *extra)
            self.assertEqual(r.returncode, 1)
            self.assertIn("không module nào", r.stderr)

    def test_missing_log_exit_2(self):
        r = subprocess.run(
            [sys.executable, str(SCRIPT), "--title", "x", "/khong/co/tep.txt"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(r.returncode, 2)


class DockerfilesCompileBytecode(unittest.TestCase):
    """Mọi Dockerfile cài venv bằng `uv sync` phải bật biên dịch .pyc (uv mặc định KHÔNG sinh .pyc như pip)."""

    def test_uv_sync_dockerfiles_compile_bytecode(self):
        files = sorted((REPO / "deploy" / "images").glob("*.Dockerfile")) + [REPO / "apps" / "web" / "Dockerfile"]
        users = [p for p in files if re.search(r"\buv sync\b", p.read_text(encoding="utf-8"))]
        self.assertTrue(users, "không thấy Dockerfile nào dùng `uv sync` — test này cần cập nhật theo")
        bad = []
        for p in users:
            text = p.read_text(encoding="utf-8")
            if not re.search(r"\bUV_COMPILE_BYTECODE=1\b", text) and "--compile-bytecode" not in text:
                bad.append(str(p.relative_to(REPO)))
        self.assertEqual(bad, [], "thêm UV_COMPILE_BYTECODE=1 vào ENV trước bước `uv sync`")

    def test_ci_images_job_runs_check(self):
        ci = (REPO / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        for title in ("gh-api", "gh-browser"):
            self.assertRegex(ci, rf"check_bytecode\.py --title {title}\b")


if __name__ == "__main__":
    unittest.main()
