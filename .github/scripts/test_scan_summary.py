import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent / "scan_summary.py"


def run(kind, content, title="Mẫu"):
    d = tempfile.mkdtemp()
    f = Path(d) / "x.json"
    if content is not None:
        f.write_text(content)
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--kind", kind, "--title", title, str(f)], capture_output=True, text=True
    )


class ScanSummary(unittest.TestCase):
    def test_pip_vuln(self):
        data = {"dependencies": [{"name": "foo", "version": "1.0", "vulns": [
            {"id": "PYSEC-1", "fix_versions": ["1.1"], "aliases": []}]}, {"name": "ok", "version": "2", "vulns": []}]}
        r = run("pip-audit", json.dumps(data))
        self.assertEqual(r.returncode, 0)
        self.assertIn("PYSEC-1", r.stdout)
        self.assertIn("1.1", r.stdout)
        self.assertIn("::warning::", r.stderr)
        # PyPI không ghi mức ⇒ không gắn nhãn "mức cao" (npm/govulncheck mới có mức thật).
        self.assertIn("1 lỗ cần xem", r.stdout)
        self.assertNotIn("mức cao", r.stdout)

    def test_pip_clean(self):
        r = run("pip-audit", json.dumps({"dependencies": [{"name": "a", "version": "1", "vulns": []}]}))
        self.assertEqual(r.returncode, 0)
        self.assertIn("✅", r.stdout)
        self.assertNotIn("::warning::", r.stderr)

    def test_npm(self):
        data = {"metadata": {"vulnerabilities": {"critical": 0, "high": 1, "moderate": 2, "low": 0, "info": 0}},
                "vulnerabilities": {
                    "lodash": {"severity": "high", "fixAvailable": True, "via": []},
                    "a": {"severity": "moderate", "fixAvailable": False, "via": []},
                    "b": {"severity": "moderate", "fixAvailable": False, "via": []}}}
        r = run("npm-audit", json.dumps(data))
        self.assertEqual(r.returncode, 0)
        self.assertIn("high 1", r.stdout)
        self.assertIn("moderate 2", r.stdout)
        self.assertIn("lodash", r.stdout)
        self.assertNotIn("| a |", r.stdout)
        self.assertEqual(r.stderr.count("::warning::"), 1)

    def test_govulncheck_stream(self):
        objs = [
            {"config": {"scanner_name": "govulncheck"}},
            {"finding": {"osv": "GO-2024-0001", "fixed_version": "v1.2.3",
                         "trace": [{"module": "m/x", "version": "v1.0.0", "package": "m/x", "function": "Do"}]}},
            {"finding": {"osv": "GO-2024-0002", "trace": [{"module": "m/y", "version": "v1.0.0"}]}},
        ]
        r = run("govulncheck", "\n".join(json.dumps(o, indent=1) for o in objs))
        self.assertEqual(r.returncode, 0)
        self.assertIn("cao (mã thật sự gọi tới)", r.stdout)
        self.assertIn("thấp", r.stdout)
        self.assertEqual(r.stderr.count("::warning::"), 1)

    def test_missing_and_broken(self):
        for content in (None, "", "{hỏng"):
            r = run("pip-audit", content)
            self.assertEqual(r.returncode, 0)
            self.assertIn("Không chạy được quét", r.stdout)
            self.assertIn("::warning::", r.stderr)
            # Step summary và ::warning:: dùng cùng một câu.
            msg = "Không chạy được quét Mẫu (thiếu kết quả) — xem log bước quét"
            self.assertIn(msg, r.stdout)
            self.assertIn(f"::warning::{msg}", r.stderr)

    def test_govulncheck_incomplete_is_not_clean(self):
        # Luồng thật khi không tải được vuln.go.dev: config + SBOM + "Fetching…" rồi dừng (exit 1).
        objs = [
            {"config": {"scanner_name": "govulncheck"}},
            {"SBOM": {"go_version": "go1.26", "modules": [{"path": "m/x"}]}},
            {"progress": {"message": "Fetching vulnerabilities from the database..."}},
        ]
        r = run("govulncheck", "\n".join(json.dumps(o) for o in objs))
        self.assertEqual(r.returncode, 0)
        self.assertIn("Không chạy được quét", r.stdout)
        self.assertNotIn("✅", r.stdout)
        self.assertIn("::warning::", r.stderr)

    def test_govulncheck_complete_clean(self):
        objs = [
            {"config": {"scanner_name": "govulncheck"}},
            {"progress": {"message": "Fetching vulnerabilities from the database..."}},
            {"progress": {"message": "Checking the code against the vulnerabilities..."}},
        ]
        r = run("govulncheck", "\n".join(json.dumps(o) for o in objs))
        self.assertEqual(r.returncode, 0)
        self.assertIn("✅", r.stdout)
        self.assertNotIn("::warning::", r.stderr)

    def test_tool_error_json_is_not_clean(self):
        # npm audit mất mạng vẫn in JSON {"error": …}; pip-audit lỗi không có "dependencies".
        for kind, data in (("npm-audit", {"error": {"code": "ENOTFOUND", "summary": "x"}}),
                           ("pip-audit", {"fixes": []})):
            r = run(kind, json.dumps(data))
            self.assertEqual(r.returncode, 0)
            self.assertIn("Không chạy được quét", r.stdout)
            self.assertNotIn("✅", r.stdout)

    def test_bad_kind(self):
        self.assertEqual(run("khac", "{}").returncode, 2)


if __name__ == "__main__":
    unittest.main()
