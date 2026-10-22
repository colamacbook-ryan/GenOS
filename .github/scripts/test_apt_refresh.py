"""Tầng apt trong Dockerfile phải làm mới được mỗi tuần (sửa sau review v0.1.48).

Ảnh nền ghim digest ⇒ khoá cache tầng `apt-get update && apt-get install` không bao giờ đổi; release.yml dùng cache gha
mode=max nên sẽ dùng lại gói apt cũ mãi (lỡ bản vá bảo mật Debian/PGDG). Mỗi Dockerfile có apt-get phải khai báo
`ARG APT_REFRESH` TRƯỚC lệnh apt-get đầu tiên, và release.yml phải truyền APT_REFRESH theo tuần cho mọi lần build.
"""

import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


class AptRefresh(unittest.TestCase):
    def test_dockerfiles_with_apt_declare_refresh_arg_first(self):
        files = sorted((REPO / "deploy" / "images").glob("*.Dockerfile")) + [REPO / "apps" / "web" / "Dockerfile"]
        with_apt = []
        bad = []
        for p in files:
            text = p.read_text(encoding="utf-8")
            apt = re.search(r"\bapt-get\s+update\b", text)
            if not apt:
                continue
            with_apt.append(p)
            arg = re.search(r"^ARG APT_REFRESH\b", text, re.MULTILINE)
            if not arg or arg.start() > apt.start():
                bad.append(str(p.relative_to(REPO)))
        self.assertTrue(with_apt, "không thấy Dockerfile nào dùng apt-get — test cần cập nhật theo")
        self.assertEqual(bad, [], "thêm `ARG APT_REFRESH=` ngay trước RUN apt-get đầu tiên")

    def test_release_passes_weekly_refresh_to_every_build(self):
        wf = (REPO / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
        self.assertIn("APT_REFRESH=$(date -u +%G-W%V)", wf)
        builds = wf.count("uses: docker/build-push-action@")
        self.assertGreaterEqual(builds, 1)
        self.assertEqual(wf.count("APT_REFRESH=${{ env.APT_REFRESH }}"), builds)


if __name__ == "__main__":
    unittest.main()
