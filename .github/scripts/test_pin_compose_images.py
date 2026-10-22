"""Test deploy/scripts/pin-compose-images.sh (v0.1.63 H3): agy-box dùng chung api.Dockerfile ⇒ nhận đúng digest ảnh api.

Chạy script thật trên compose mẫu (bash + sed), không cần Docker."""

import subprocess
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "deploy" / "scripts" / "pin-compose-images.sh"

IMG = {k: f"ghcr.io/o/gen-harness-{k}@sha256:{c * 64}" for k, c in
       {"api": "a", "web": "b", "bridge": "c", "db": "d", "browser": "e"}.items()}

COMPOSE = """name: gen-harness
services:
  web:
    build: { context: .., dockerfile: apps/web/Dockerfile }
  migrate:
    build: { context: .., dockerfile: deploy/images/api.Dockerfile }
  api:
    build: { context: .., dockerfile: deploy/images/api.Dockerfile }
  worker:
    build: { context: .., dockerfile: deploy/images/api.Dockerfile }
  agy-box:
    build: { context: .., dockerfile: deploy/images/api.Dockerfile }
    command: ["python", "-m", "gh.agybox.server"]
  bridge:
    build: { context: .., dockerfile: deploy/images/bridge.Dockerfile }
  db:
    build: { context: .., dockerfile: deploy/images/db.Dockerfile }
  browser:
    build: { context: .., dockerfile: deploy/images/browser.Dockerfile }
  browser-egress:
    build: { context: .., dockerfile: deploy/images/browser.Dockerfile }
"""


def run_pin(compose: str) -> tuple[int, str, str]:
    with tempfile.TemporaryDirectory() as tmp:
        src, out = Path(tmp) / "compose.yaml", Path(tmp) / "release.yaml"
        src.write_text(compose, encoding="utf-8")
        args = ["bash", str(SCRIPT), str(src), str(out)]
        for k, v in IMG.items():
            args += [f"--{k}", v]
        r = subprocess.run(args, capture_output=True, text=True, check=False)
        return r.returncode, out.read_text(encoding="utf-8") if out.exists() else "", r.stderr


class PinComposeImages(unittest.TestCase):
    def test_agy_box_gets_api_digest_and_keeps_command(self) -> None:
        rc, out, err = run_pin(COMPOSE)
        self.assertEqual(rc, 0, err)
        block = out.split("  agy-box:\n")[1].split("\n  bridge:")[0]
        self.assertIn(f"image: {IMG['api']}", block)
        self.assertNotIn("build:", block)
        self.assertIn('command: ["python", "-m", "gh.agybox.server"]', block)
        self.assertNotIn("build: {", out)


if __name__ == "__main__":
    unittest.main()
