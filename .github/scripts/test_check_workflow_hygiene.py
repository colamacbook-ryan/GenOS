#!/usr/bin/env python3
"""Test cho check_workflow_hygiene.py — chạy: python3 -m unittest discover -s .github/scripts -p 'test_*.py'.

Mỗi test chép 4 workflow THẬT của repo sang thư mục tạm, làm hỏng đúng một điểm vệ sinh (F-71/F-36) rồi khẳng định
script bắt được (và repo nguyên trạng thì sạch; `ubuntu-latest`, `releases/latest` không bị bắt nhầm).
"""

from __future__ import annotations

import contextlib
import io
import shutil
import sys
import tempfile
import unittest
from collections.abc import Callable
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import check_workflow_hygiene as hygiene  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
CI = ".github/workflows/ci.yml"
INSTALLER = ".github/workflows/installer-matrix.yml"
RELEASE = ".github/workflows/release.yml"
E2E = ".github/workflows/e2e-install.yml"
CHECKOUT = "actions/checkout@fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09 # v5.1.0"
PERMS_THEN_JOBS = "permissions:\n  contents: read\n\njobs:"


class WorkflowHygieneTest(unittest.TestCase):
    def run_check(self, mutate: Callable[[Path], None] | None = None) -> tuple[int, str]:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for rel in hygiene.WORKFLOWS:
                (root / rel).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy(ROOT / rel, root / rel)
            if mutate:
                mutate(root)
            err = io.StringIO()
            with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
                code = hygiene.main(["--root", str(root)])
            return code, err.getvalue()

    @staticmethod
    def replace(rel: str, old: str, new: str, count: int = -1) -> Callable[[Path], None]:
        def mutate(root: Path) -> None:
            p = root / rel
            s = p.read_text(encoding="utf-8")
            assert old in s, f"không thấy đoạn cần sửa trong {rel}: {old!r}"
            p.write_text(s.replace(old, new, count), encoding="utf-8")

        return mutate

    def test_repo_nguyen_trang_sach(self) -> None:
        code, err = self.run_check()
        self.assertEqual(code, 0, err)

    def test_uses_theo_tag_khong_ghim_sha(self) -> None:
        code, err = self.run_check(self.replace(CI, CHECKOUT, "actions/checkout@v5"))
        self.assertEqual(code, 1)
        self.assertIn("ghim SHA", err)

    def test_uses_sha_ngan_khong_hop_le(self) -> None:
        code, err = self.run_check(self.replace(CI, CHECKOUT, "actions/checkout@fbc6f39 # v5.1.0"))
        self.assertEqual(code, 1)
        self.assertIn("ghim SHA", err)

    def test_uses_ghim_sha_nhung_thieu_chu_thich_phien_ban(self) -> None:
        code, err = self.run_check(self.replace(RELEASE, CHECKOUT, CHECKOUT.split(" #")[0], 1))
        self.assertEqual(code, 1)
        self.assertIn("thiếu chú thích phiên bản", err)

    def test_uses_job_cuc_bo_khong_bi_bat(self) -> None:
        # `uses: ./.github/workflows/ci.yml` (reusable workflow cục bộ) không cần ghim.
        code, err = self.run_check()
        self.assertEqual(code, 0, err)
        self.assertIn("./.github/workflows/ci.yml", (ROOT / RELEASE).read_text(encoding="utf-8"))

    def test_promote_gan_lai_anh_latest(self) -> None:
        anchor = '          echo "OK: $TAG là bản chính thức'
        new = '          docker buildx imagetools create -t "$img:latest" "$img:$TAG"\n' + anchor
        code, err = self.run_check(self.replace(E2E, anchor, new))
        self.assertEqual(code, 1)
        self.assertIn(":latest", err)

    def test_build_images_them_tag_latest(self) -> None:
        old = "            ${{ env.REGISTRY }}/${{ needs.meta.outputs.owner_lower }}/gen-harness-${{ matrix.service }}:sha-${{ github.sha }}\n"
        latest = old.replace(":sha-${{ github.sha }}", ":latest")
        code, err = self.run_check(self.replace(RELEASE, old, old + latest))
        self.assertEqual(code, 1)
        self.assertIn(":latest", err)

    def test_ci_thieu_permissions_cap_workflow(self) -> None:
        code, err = self.run_check(self.replace(CI, PERMS_THEN_JOBS, "jobs:"))
        self.assertEqual(code, 1)
        self.assertIn("thiếu `permissions` cấp workflow", err)
        self.assertIn(CI, err)

    def test_installer_matrix_thieu_permissions_cap_workflow(self) -> None:
        code, err = self.run_check(self.replace(INSTALLER, PERMS_THEN_JOBS, "jobs:"))
        self.assertEqual(code, 1)
        self.assertIn(INSTALLER, err)

    def test_permissions_write_all(self) -> None:
        code, err = self.run_check(self.replace(CI, PERMS_THEN_JOBS, "permissions: write-all\n\njobs:"))
        self.assertEqual(code, 1)
        self.assertIn("quyền write", err)

    def test_permissions_cap_workflow_co_contents_write(self) -> None:
        code, err = self.run_check(self.replace(INSTALLER, PERMS_THEN_JOBS, "permissions:\n  contents: write\n\njobs:"))
        self.assertEqual(code, 1)
        self.assertIn("quyền write", err)

    def test_services_redis_khong_digest(self) -> None:
        def mutate(root: Path) -> None:
            p = root / CI
            s = p.read_text(encoding="utf-8")
            import re

            s2, n = re.subn(r"image: redis:7-alpine@sha256:[0-9a-f]{64}", "image: redis:7-alpine", s)
            assert n >= 1
            p.write_text(s2, encoding="utf-8")

        code, err = self.run_check(mutate)
        self.assertEqual(code, 1)
        self.assertIn("chưa ghim digest", err)

    def test_container_image_khong_digest(self) -> None:
        old = "  version:\n    runs-on: ubuntu-latest\n"
        code, err = self.run_check(self.replace(CI, old, old + "    container: python:3.11-slim\n"))
        self.assertEqual(code, 1)
        self.assertIn("chưa ghim digest", err)

    def test_khong_bat_nham_ubuntu_latest_va_releases_latest(self) -> None:
        # ubuntu-latest đã có sẵn trong mọi workflow; thêm chuỗi releases/latest và `--latest` vào một lệnh run.
        anchor = '          echo "VERSION hợp lệ: $version"\n'
        extra = anchor + '          echo "https://api.github.com/repos/x/y/releases/latest và gh release edit --latest"\n'
        self.assertIn("ubuntu-latest", (ROOT / CI).read_text(encoding="utf-8"))
        code, err = self.run_check(self.replace(CI, anchor, extra))
        self.assertEqual(code, 0, err)

    def test_dong_chu_thich_trong_run_khong_bi_bat(self) -> None:
        anchor = '          echo "VERSION hợp lệ: $version"\n'
        extra = anchor + "          # ví dụ cũ: docker buildx imagetools create -t img:latest img:v1\n"
        code, err = self.run_check(self.replace(CI, anchor, extra))
        self.assertEqual(code, 0, err)

    def test_thieu_tep_thoat_2(self) -> None:
        def mutate(root: Path) -> None:
            (root / E2E).unlink()

        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as cm:
                self.run_check(mutate)
        self.assertEqual(cm.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
