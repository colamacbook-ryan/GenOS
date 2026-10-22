#!/usr/bin/env python3
"""Test cho check_no_fake_ids.py — chạy: python3 -m unittest discover -s .github/scripts -p 'test_*.py'."""

from __future__ import annotations

import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import check_no_fake_ids as check  # noqa: E402


class NoFakeIdsTest(unittest.TestCase):
    def run_check(self, files: dict[str, str] | None = None, root: Path | None = None) -> tuple[int, str]:
        out = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp:
            base = root or Path(tmp)
            for rel, body in (files or {}).items():
                p = base / rel
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(body, encoding="utf-8")
            with contextlib.redirect_stdout(out):
                code = check.main(["check_no_fake_ids.py", str(base)])
        return code, out.getvalue()

    def test_fake_user_id_fails(self) -> None:
        code, out = self.run_check({"screens/Inbox.tsx": "const T = [\n  { id: 'u-lan', name: 'Chị Lan' },\n];\n"})
        self.assertEqual(code, 1)
        self.assertIn("Inbox.tsx:2", out)
        self.assertIn("::error", out)

    def test_fake_owner_option_and_agent_fail(self) -> None:
        code, out = self.run_check({
            "graph/model.ts": 'export const O = [{ value: "u-ha", label: "Hà" }];\n',
            "rel/Dir.tsx": "const A = [{ id: `agent-ka`, name: 'KA' }];\n",
        })
        self.assertEqual(code, 1)
        self.assertIn("model.ts:1", out)
        self.assertIn("Dir.tsx:1", out)

    def test_clean_sample_passes(self) -> None:
        code, out = self.run_check({
            "lib/pickers.ts": "export const qk = { users: ['pickers', 'users'], agents: ['pickers', 'agents'] };\n",
            "screens/X.tsx": "const user = { id: u.id, value: 'user-1', label: 'agentParams' };\n",
            "README.md": "{ id: 'u-lan' } — không phải .ts nên bỏ qua\n",
        })
        self.assertEqual(code, 0, out)

    def test_agent_prefix_only_in_id_keys(self) -> None:
        """'agent-' chỉ vi phạm khi là giá trị id/value/agent_id; className/testid/khoá i18n hợp lệ."""
        code, out = self.run_check({
            "ok/Card.tsx": ('<div className="agent-card" data-testid="agent-row" />\n'
                            "const k = t('agent-params.title');\n"
                            "const css = `agent-${size}`;\n"),
            "ok/Mock.ts": "const A = { id: 'agent-demo' }; // allow-fake-id\n",
        })
        self.assertEqual(code, 0, out)
        code, out = self.run_check({"bad/A.ts": 'const x = { agent_id: "agent-ka" };\nconst y = { value : `agent-z` };\n'})
        self.assertEqual(code, 1)
        self.assertIn("A.ts:1", out)
        self.assertIn("A.ts:2", out)

    def test_real_web_src_is_clean(self) -> None:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = check.main(["check_no_fake_ids.py"])
        self.assertEqual(code, 0, out.getvalue())


if __name__ == "__main__":
    unittest.main()
