#!/usr/bin/env python3
"""Test cho check_no_personal_info.py — chạy: python3 -m unittest discover -s .github/scripts -p 'test_*.py'.

Mọi chuỗi trong tệp này là GIẢ HOÀN TOÀN (`bi-mat-vd`, `ten-rieng-vd`, `vd@example.test`…): bộ kiểm chống lộ không được
tự ghi thông tin riêng thật vào repo công khai. Mẫu riêng được truyền qua `env` giả, không qua môi trường thật.
"""

from __future__ import annotations

import contextlib
import io
import sys
import tempfile
import unittest
from collections.abc import Mapping
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import check_no_personal_info as check  # noqa: E402

SECRET_HOST = "bi-mat-vd.vn"                    # tên miền "riêng" giả
SECRET_NAME = "Ten-Rieng-VD"                    # tên gọi "riêng" giả (chữ hoa/thường lẫn lộn)
PRIVATE_ENV = {check.ENV_PATTERNS: f"bi-mat-vd\\.vn\n# dòng chú thích bị bỏ qua\n\nten[-_ ]rieng[-_ ]vd\n"}
ORG = "Genesis-ryan-84-0567536339"              # tổ chức GitHub phát hành — chuỗi cho phép duy nhất
NO_ENV: Mapping[str, str] = {}


class NoPersonalInfoTest(unittest.TestCase):
    def run_check(self, files: dict[str, str | bytes], env: Mapping[str, str] = NO_ENV) -> tuple[int, str]:
        out = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            for rel, body in files.items():
                p = base / rel
                p.parent.mkdir(parents=True, exist_ok=True)
                if isinstance(body, bytes):
                    p.write_bytes(body)
                else:
                    p.write_text(body, encoding="utf-8")
            with contextlib.redirect_stdout(out):
                code = check.main(["check_no_personal_info.py", str(base)], env)
        return code, out.getvalue()

    # ── quy tắc chung (luôn chạy, không cần mẫu riêng) ──────────────────────────────────────────────────────────
    def test_clean_files_pass(self) -> None:
        code, out = self.run_check({
            "apps/web/src/screens/HubLinkCard.tsx": 'const P = "https://<địa-chỉ-gen-hub-của-bạn>/mcp";\n',
            "apps/api/gh/hub_link/service.py": 'KHO_LABEL = "Kho dữ liệu"\nX = "https://hub.example.test/mcp"\n',
            "packages/contracts/src/gen.ts": "export const KHO_LABEL = 'Kho dữ liệu';\n",
            "apps/api/gh/mail.py": ("A = 'vd@example.test'\nB = 'vd@mail.example.com'\nC = 'noreply@anthropic.test'\n"
                                    "D = '12345+bot@users.noreply.github.com'\nE = 'no-reply@x.vn'\n"),
            "apps/web/src/pkg.ts": "import x from 'lodash@4.17.21'; // @gen-harness/contracts, img@sha256:abcd\n",
            "install.sh": "REPO=example/repo\n",
            "db/sql/0001.sql": "-- sạch\n",
        })
        self.assertEqual(code, 0, out)
        self.assertIn("Sạch", out)

    def test_real_email_is_caught_without_any_private_pattern(self) -> None:
        for rel, body in {
            "apps/api/gh/a.py": 'OWNER = "nguoi.dung@cong-ty-that.vn"\n',
            "apps/web/src/b.tsx": '<input defaultValue="NGUOI.DUNG@Gmail.com" />\n',
            "deploy/c.yaml": "contact: x@ten-mien.io\n",
            "apps/api/gh/d.py": 'X = "vd@example.vn"\n',          # example.vn KHÔNG phải miền ví dụ được phép
        }.items():
            code, out = self.run_check({rel: body})
            self.assertEqual(code, 1, f"{rel}: {out}")
            self.assertIn(f"{rel}:1", out)
            self.assertIn(f"::error file={rel},line=1::Địa chỉ email thật", out)
            self.assertIn("chưa cấu hình mẫu riêng", out)           # cảnh báo lớp riêng chưa bật, nhưng quy tắc chung vẫn bắt

    def test_real_mcp_host_is_caught_without_any_private_pattern(self) -> None:
        for rel, body in {
            "apps/web/src/screens/HubLinkCard.tsx": '<input placeholder="https://hub.cong-ty-that.vn/mcp" />\n',
            "apps/api/gh/hub_link/service.py": 'MSG = "Nhập dạng http://may-chu.dich-vu.com:8443/mcp"\n',
            "install.ps1": "# https://A.B.io/MCP\n",
        }.items():
            code, out = self.run_check({rel: body})
            self.assertEqual(code, 1, f"{rel}: {out}")
            self.assertIn(f"{rel}:1", out)
            self.assertIn("Địa chỉ Gen-hub thật", out)

    def test_mcp_placeholders_and_example_hosts_are_allowed(self) -> None:
        code, out = self.run_check({"apps/web/src/x.tsx": (
            "const A = 'https://<máy-chủ>/mcp';\nconst B = 'https://<địa-chỉ-gen-hub-của-bạn>/mcp';\n"
            "const C = 'https://hub.example.test/mcp';\nconst D = 'http://example.com:8080/mcp';\n"
            "const E = 'http://localhost:3000/mcp';\nconst F = 'https://x.example.org/mcpx';\n")})
        self.assertEqual(code, 0, out)

    # ── mẫu riêng (đọc từ môi trường, không nằm trong repo) ─────────────────────────────────────────────────────
    def test_private_patterns_are_caught_case_insensitively_and_never_printed(self) -> None:
        samples = {
            "apps/api/gh/a.py": f'X = "https://{SECRET_HOST}/hub"\n',
            "apps/web/src/b.tsx": f"// {SECRET_NAME.upper()}\n",
            "packages/contracts/src/c.ts": "// vd ten rieng vd (design)\n",
            "deploy/d.yaml": f"owner: {SECRET_NAME.lower()}\n",
            "db/sql/e.sql": f"-- {SECRET_HOST.upper()}\n",
            "install.ps1": f"# {SECRET_HOST}\n",
        }
        for rel, body in samples.items():
            code, out = self.run_check({rel: body}, PRIVATE_ENV)
            self.assertEqual(code, 1, f"{rel}: {out}")
            self.assertIn(f"{rel}:1", out)
            self.assertIn(f"::error file={rel},line=1::", out)
            self.assertIn("/2 (không in dòng)", out)                 # 2 mẫu thật; dòng chú thích và dòng trống không tính
            self.assertNotIn("mẫu riêng: 0", out)
            for secret in (SECRET_HOST, SECRET_NAME, "rieng", "bi-mat"):
                self.assertNotIn(secret.lower(), out.lower(), f"{rel}: log lộ '{secret}'")
        code, out = self.run_check({"apps/api/gh/a.py": "x = 1\n"}, PRIVATE_ENV)
        self.assertEqual(code, 0, out)
        self.assertIn("Mẫu riêng: 2 mẫu", out)
        self.assertNotIn("chưa cấu hình mẫu riêng", out)

    def test_private_match_wins_over_general_rule_and_hides_the_line(self) -> None:
        # Dòng vừa chứa email lạ vừa khớp mẫu riêng ⇒ chỉ báo "mẫu riêng", không in nội dung (kể cả email).
        line = f'X = "dai.dien@{SECRET_HOST}"\n'
        code, out = self.run_check({"apps/api/gh/a.py": line}, PRIVATE_ENV)
        self.assertEqual(code, 1)
        self.assertIn("khớp mẫu riêng số 1/2", out)
        self.assertNotIn("dai.dien", out)
        self.assertNotIn("Địa chỉ email thật", out)

    def test_private_pattern_numbers_skip_comments_and_blank_lines(self) -> None:
        env = {check.ENV_PATTERNS: "# chú thích\n\nmau-mot-vd\n   \nmau-hai-vd\n"}
        code, out = self.run_check({"apps/api/gh/a.py": "# MAU-HAI-VD\n"}, env)
        self.assertEqual(code, 1)
        self.assertIn("khớp mẫu riêng số 2/2", out)

    def test_empty_private_patterns_only_warn(self) -> None:
        for env in (NO_ENV, {check.ENV_PATTERNS: ""}, {check.ENV_PATTERNS: "\n# chỉ chú thích\n  \n"}):
            code, out = self.run_check({"apps/api/gh/a.py": f"# {SECRET_HOST}\n"}, env)
            self.assertEqual(code, 0, out)                            # không mẫu riêng ⇒ chỉ quy tắc chung
            self.assertIn("::warning::chưa cấu hình mẫu riêng", out)

    def test_invalid_private_regex_returns_2_without_printing_the_pattern(self) -> None:
        env = {check.ENV_PATTERNS: "ok-vd\nmau-hong-vd(\n"}
        code, out = self.run_check({"apps/api/gh/a.py": "x = 1\n"}, env)
        self.assertEqual(code, 2)
        self.assertIn("dòng mẫu số 2", out)
        self.assertNotIn("mau-hong-vd", out)

    # ── phạm vi / ngoại lệ ──────────────────────────────────────────────────────────────────────────────────────
    def test_allow_comment_and_release_org_are_allowed(self) -> None:
        code, out = self.run_check({
            "db/sql/0035.sql": f"WHERE note = '...{SECRET_NAME}...'; -- allow-personal-info\n",
            "apps/api/gh/mail.py": 'X = "ai.do@nha-that.vn"  # allow-personal-info\n',
            "install.sh": f'REPO="{ORG}/Gen-Harness"\n',
            "apps/genh/main.go": f'const repo = "{ORG}/Gen-Harness"\n',
            "apps/api/gh/config.py": f'release_repo = "{ORG}/Gen-Harness"\n',
        }, PRIVATE_ENV)
        self.assertEqual(code, 0, out)

    def test_org_string_does_not_hide_a_real_leak_on_the_same_line(self) -> None:
        code, out = self.run_check({"install.sh": f'REPO="{ORG}/x" # {SECRET_HOST}\n'}, PRIVATE_ENV)
        self.assertEqual(code, 1)
        self.assertIn("install.sh:1", out)

    def test_out_of_scope_paths_and_binary_files_are_ignored(self) -> None:
        leak = f"u@{SECRET_HOST} https://{SECRET_HOST}/mcp {SECRET_NAME}\n"
        code, out = self.run_check({
            "apps/web/test/unit/hub.test.tsx": leak,      # test/mock: không giao cho Owner
            "apps/web/e2e/x.spec.ts": leak,
            "docs/releases/v0.1.26.md": leak,
            "apps/genh/internal/ops/x_test.go": leak,
            "apps/web/src/node_modules/pkg/index.js": leak,
            "apps/web/src/logo.png": b"\x89PNG\r\n\x1a\n\xff\xfe" + leak.encode() + b"\n",
        }, PRIVATE_ENV)
        self.assertEqual(code, 0, out)

    def test_go_sources_outside_tests_are_scanned(self) -> None:
        code, out = self.run_check({"apps/genh/internal/update/x.go": f'var h = "https://{SECRET_HOST}/mcp"\n'})
        self.assertEqual(code, 1)
        self.assertIn("apps/genh/internal/update/x.go:1", out)

    def test_missing_root_returns_2(self) -> None:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = check.main(["check_no_personal_info.py", "/khong/ton/tai/gen-harness"], NO_ENV)
        self.assertEqual(code, 2)

    def test_real_repo_is_clean(self) -> None:
        # Không truyền mẫu riêng: quy tắc chung trên mã thật của repo phải sạch (mẫu riêng chạy ở CI qua secret).
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = check.main(["check_no_personal_info.py"], NO_ENV)
        self.assertEqual(code, 0, out.getvalue())


if __name__ == "__main__":
    unittest.main()
