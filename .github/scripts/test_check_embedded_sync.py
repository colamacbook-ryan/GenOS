import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
SCRIPT = HERE / "check_embedded_sync.py"
EMB = "apps/genh/internal/compose/"
FILES = [
    "deploy/compose.yaml",
    "deploy/proxy/Caddyfile",
    "deploy/browser/chromium-seccomp.json",
    EMB + "embedded_compose.yaml",
    EMB + "embedded_Caddyfile",
    EMB + "embedded_chromium-seccomp.json",
]


def run(root):
    r = subprocess.run([sys.executable, str(SCRIPT), "--root", str(root)], capture_output=True, text=True)
    return r.returncode, r.stdout + r.stderr


class CheckEmbeddedSync(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        for f in FILES:
            (self.tmp / f).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(REPO / f, self.tmp / f)

    def test_repo_ok(self):
        code, out = run(REPO)
        self.assertEqual(code, 0, out)
        self.assertIn("OK: 3 tệp nhúng khớp deploy/", out)

    def test_temp_copy_ok(self):
        self.assertEqual(run(self.tmp)[0], 0)

    def test_compose_extra_line(self):
        p = self.tmp / (EMB + "embedded_compose.yaml")
        p.write_text(p.read_text() + "# them\n")
        code, out = run(self.tmp)
        self.assertEqual(code, 1)
        self.assertIn("embedded_compose.yaml", out)
        self.assertIn("lệch", out)

    def test_seccomp_one_byte(self):
        p = self.tmp / (EMB + "embedded_chromium-seccomp.json")
        p.write_bytes(p.read_bytes() + b" ")
        code, out = run(self.tmp)
        self.assertEqual(code, 1)
        self.assertIn("seccomp", out)

    def test_caddyfile_changed(self):
        p = self.tmp / (EMB + "embedded_Caddyfile")
        p.write_text(p.read_text() + "# x\n")
        self.assertEqual(run(self.tmp)[0], 1)

    def test_missing_embedded(self):
        (self.tmp / (EMB + "embedded_Caddyfile")).unlink()
        code, out = run(self.tmp)
        self.assertEqual(code, 1)
        self.assertIn("embedded_Caddyfile", out)

    def test_only_deploy_changed(self):
        p = self.tmp / "deploy/compose.yaml"
        p.write_text(p.read_text() + "# moi\n")
        code, out = run(self.tmp)
        self.assertEqual(code, 1)
        self.assertIn("cp deploy/compose.yaml", out)


if __name__ == "__main__":
    unittest.main()
