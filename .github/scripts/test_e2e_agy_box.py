"""Test e2e_agy_box.sh (v0.1.63 H3) — bước kiểm hộp agy-box của E2E cài mới và nâng cấp.

Chạy đúng script bằng bash với `docker`/`genh` giả trên PATH (không cần Docker thật). Mỗi test dựng một bản cài giả rồi
cố ý làm hỏng MỘT điều kiện và đòi script đỏ đúng lý do; test đường xanh đòi script xanh."""

import base64
import os
import re
import stat
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / ".github" / "scripts" / "e2e_agy_box.sh"

COMPOSE_WITH_BOX = "services:\n  api:\n    image: x\n  agy-box:\n    image: x\n"
COMPOSE_NO_BOX = "services:\n  api:\n    image: x\n"
STATUS_OK = "DỊCH VỤ    TRẠNG THÁI HEALTH\nagy-box    running    healthy\napi        running    healthy\n"
KEY = base64.b64encode(bytes(range(32))).decode()


def _exe(path: Path, body: str) -> None:
    path.write_text("#!/usr/bin/env bash\nset -euo pipefail\n" + textwrap.dedent(body), encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


class Box(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.home = self.root / "gh"
        (self.home / "deploy").mkdir(parents=True)
        (self.home / "secrets").mkdir()
        (self.home / "bin").mkdir()
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.compose = self.home / "deploy" / "compose.yaml"
        self.compose.write_text(COMPOSE_WITH_BOX, encoding="utf-8")
        self.key = self.home / "secrets" / "gh_agy_key"
        self.key.write_text(KEY, encoding="utf-8")
        self.key.chmod(0o644)
        self.out = self.root / "fake"
        self.out.mkdir()
        (self.out / "status.txt").write_text(STATUS_OK, encoding="utf-8")
        (self.out / "exec-cid-agy-box.out").write_text("OK\n", encoding="utf-8")
        (self.out / "exec-cid-api.out").write_text("OK\n", encoding="utf-8")
        _exe(
            self.bin / "docker",
            f"""
            case "$1" in
              ps)
                svc=""
                for a in "$@"; do case "$a" in label=com.docker.compose.service=*) svc="${{a#label=com.docker.compose.service=}}";; esac; done
                [ -f "{self.out}/noservice-$svc" ] || echo "cid-$svc" ;;
              exec) cat "{self.out}/exec-$2.out" ;;
              logs) echo "log giả của $2" ;;
              *) echo "docker giả: lệnh không mong đợi: $*" >&2; exit 2 ;;
            esac
        """,
        )
        _exe(self.home / "bin" / "genh", f'[ "$1" = status ] || exit 2\ncat "{self.out}/status.txt"\n')

    def run_script(self, **env: str) -> subprocess.CompletedProcess[str]:
        full = {
            "PATH": f"{self.bin}{os.pathsep}{os.environ.get('PATH', '')}",
            "HOME": str(self.root),
            "GEN_HARNESS_HOME": str(self.home),
            "GH_PORT": "8443",
            "MODE": "release",
            "TAG": "v0.1.63",
            "AGY_BOX_WAIT_TRIES": "2",
            "AGY_BOX_WAIT_SLEEP": "0",
            **env,
        }
        return subprocess.run(["bash", "--noprofile", "--norc", str(SCRIPT)], env=full, capture_output=True, text=True, timeout=60, check=False)

    def assertRed(self, r: subprocess.CompletedProcess[str], needle: str) -> None:
        self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn(needle, r.stdout + r.stderr)

    # ── đường xanh ──
    def test_green(self) -> None:
        r = self.run_script()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("OK: genh status", r.stdout)
        self.assertIn("OK: api gọi được agy-box", r.stdout)
        self.assertNotIn(KEY, r.stdout + r.stderr, "không được in giá trị khoá")

    def test_green_with_genh_compose_file_in_pr_mode(self) -> None:
        alt = self.root / "checkout" / "deploy"
        alt.mkdir(parents=True)
        (alt / "compose.yaml").write_text(COMPOSE_WITH_BOX, encoding="utf-8")
        (self.root / "checkout" / "secrets").mkdir()
        k = self.root / "checkout" / "secrets" / "gh_agy_key"
        k.write_text(KEY, encoding="utf-8")
        k.chmod(0o644)
        self.compose.write_text(COMPOSE_NO_BOX, encoding="utf-8")  # compose cài đặt không dùng khi có GENH_COMPOSE_FILE
        r = self.run_script(MODE="pr", TAG="v0.1.62", GENH_COMPOSE_FILE=str(alt / "compose.yaml"))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    # ── bản có/không có box ──
    def test_old_release_without_box_is_skipped(self) -> None:
        self.compose.write_text(COMPOSE_NO_BOX, encoding="utf-8")
        r = self.run_script(TAG="v0.1.62")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("bỏ qua", r.stdout + r.stderr)

    def test_new_release_without_box_is_red(self) -> None:
        self.compose.write_text(COMPOSE_NO_BOX, encoding="utf-8")
        self.assertRed(self.run_script(TAG="v0.1.63"), "không có dịch vụ agy-box")
        self.assertRed(self.run_script(TAG="v0.1.70"), "không có dịch vụ agy-box")
        self.assertRed(self.run_script(TAG="v0.1.100"), "không có dịch vụ agy-box")  # sort -V, không sort chữ

    def test_pr_mode_requires_box_even_if_tag_is_old(self) -> None:
        self.compose.write_text(COMPOSE_NO_BOX, encoding="utf-8")
        self.assertRed(self.run_script(MODE="pr", TAG="v0.1.62"), "không có dịch vụ agy-box")

    # ── khoá ──
    def test_missing_key(self) -> None:
        self.key.unlink()
        self.assertRed(self.run_script(), "thiếu")

    def test_key_not_32_bytes(self) -> None:
        self.key.write_text(base64.b64encode(b"ngan").decode(), encoding="utf-8")
        self.assertRed(self.run_script(), "32 byte base64")

    def test_key_with_trailing_newline(self) -> None:
        self.key.write_text(KEY + "\n", encoding="utf-8")
        self.assertRed(self.run_script(), "32 byte base64")

    def test_key_wrong_mode(self) -> None:
        self.key.chmod(0o600)
        self.assertRed(self.run_script(), "0644")

    # ── trạng thái ──
    def test_box_never_healthy(self) -> None:
        (self.out / "status.txt").write_text("agy-box    running    starting\n", encoding="utf-8")
        self.assertRed(self.run_script(), "không running+healthy")

    def test_box_missing_from_status(self) -> None:
        (self.out / "status.txt").write_text("api        running    healthy\n", encoding="utf-8")
        self.assertRed(self.run_script(), "không running+healthy")

    def test_box_container_not_found(self) -> None:
        (self.out / "noservice-agy-box").write_text("", encoding="utf-8")
        self.assertRed(self.run_script(), "không thấy container agy-box")

    # ── cô lập bên trong box ──
    def test_box_probe_reports_violation(self) -> None:
        (self.out / "exec-cid-agy-box.out").write_text("FAIL /run/secrets có ['gh_agy_key', 'gh_master_key'] (chỉ được gh_agy_key)\n", encoding="utf-8")
        self.assertRed(self.run_script(), "gh_master_key")

    def test_box_probe_without_ok_is_red(self) -> None:
        (self.out / "exec-cid-agy-box.out").write_text("", encoding="utf-8")
        self.assertRed(self.run_script(), "không trả OK")

    # ── api ↔ box ──
    def test_api_cannot_reach_box(self) -> None:
        (self.out / "exec-cid-api.out").write_text("FAIL api không gọi được agy-box:8090/healthz: URLError\n", encoding="utf-8")
        self.assertRed(self.run_script(), "api ↔ agy-box")

    def test_api_container_not_found(self) -> None:
        (self.out / "noservice-api").write_text("", encoding="utf-8")
        self.assertRed(self.run_script(), "không thấy container api")


class EmbeddedProbes(unittest.TestCase):
    """Hai khối Python nhúng trong script (chạy trong container) phải biên dịch được và có đủ kiểm tra then chốt."""

    @staticmethod
    def _probe(name: str) -> str:
        m = re.search(name + r" <<'PY' \|\| true\n(.*?)\nPY\n", SCRIPT.read_text(encoding="utf-8"), re.S)
        assert m, name
        return m.group(1)

    def test_probes_compile(self) -> None:
        for name in ("BOX_PROBE", "API_PROBE"):
            compile(self._probe(name), name, "exec")

    def test_box_probe_covers_the_isolation_contract(self) -> None:
        code = self._probe("BOX_PROBE")
        for must in ("/run/secrets", "gh_master_key", "DATABASE", "REDIS", "/proc/1/environ", "claude", "objects",
                     "docker.sock", "CapEff", "getuid", "browser-redis", "agy"):
            self.assertIn(must, code)

    def test_api_probe_requires_401_for_unsigned(self) -> None:
        code = self._probe("API_PROBE")
        self.assertIn("/healthz", code)
        self.assertIn("e.code == 401", code)


if __name__ == "__main__":
    unittest.main()
