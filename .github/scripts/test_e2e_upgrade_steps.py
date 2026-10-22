"""Các bước của job e2e-upgrade (e2e-install.yml) phải thật sự kiểm được điều chúng hứa (sửa sau review v0.1.48).

- "Giả lập ảnh tồn đọng": trước v0.1.48 không bản phát hành nào ghim digest caddy/redis (bản cũ kéo theo tag) nên
  phép kiểm "mỗi repo tối đa 2 digest" luôn đạt dù genh có dọn hay không. Từ TAG v0.1.48 bước này phải kéo sẵn 1 digest
  caddy + 1 digest redis CŨ (không tag, khác digest đang ghim) và ghi vào prev2-refs.txt để bước "Chỉ còn ảnh…" đòi genh
  xoá.
- "Chỉ còn ảnh của bản hiện tại và bản liền trước": còn digest cũ ⇒ đỏ; đã dọn ⇒ xanh.
- "Ghi lại cổng/chế độ truy cập TRƯỚC khi nâng cấp" (gốc lỗi E2E release v0.1.47): phải chạy trước `genh update` và ghi
  PREV_BIND_ADDR/PREV_ACCESS_MODE từ .env của bản cũ (rỗng khi bản cũ < v0.1.46 chưa có GH_BIND_ADDR).

Chạy đúng khối `run:` trong workflow bằng bash với `docker`/`gh` giả trên PATH — không cần Docker thật.
"""

import os
import re
import stat
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

import yaml  # type: ignore[import-untyped]  # PyYAML không kèm stub kiểu

REPO = Path(__file__).resolve().parents[2]
WF = REPO / ".github" / "workflows" / "e2e-install.yml"

GH_REF_1 = "ghcr.io/o/gen-harness-api@sha256:" + "1" * 64
GH_REF_2 = "ghcr.io/o/gen-harness-web@sha256:" + "2" * 64


def _steps() -> list[dict]:
    data = yaml.safe_load(WF.read_text(encoding="utf-8"))
    steps = data["jobs"]["e2e-upgrade"]["steps"]
    assert isinstance(steps, list)
    return steps


def _step_index(prefix: str) -> int:
    for i, st in enumerate(_steps()):
        if str(st.get("name", "")).startswith(prefix):
            return i
    raise AssertionError(f"không thấy bước bắt đầu bằng {prefix!r} trong job e2e-upgrade")


def _run_of(prefix: str) -> str:
    run = _steps()[_step_index(prefix)].get("run")
    assert isinstance(run, str), f"bước {prefix!r} không có khối run"
    assert "${{" not in run, "khối run có biểu thức ${{ }} — test cần thay giá trị trước khi chạy"
    return run


def _write_exe(path: Path, body: str) -> None:
    path.write_text("#!/usr/bin/env bash\nset -euo pipefail\n" + textwrap.dedent(body), encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


class _Sandbox:
    def __init__(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.runner_temp = self.root / "rt"
        self.runner_temp.mkdir()

    def close(self) -> None:
        self._tmp.cleanup()

    def run(self, script: str, **env: str) -> subprocess.CompletedProcess[str]:
        full = {
            "PATH": f"{self.bin}{os.pathsep}{os.environ.get('PATH', '')}",
            "HOME": str(self.root),
            "RUNNER_TEMP": str(self.runner_temp),
            "R": "o/r",
            **env,
        }
        return subprocess.run(
            ["bash", "--noprofile", "--norc", "-e", "-c", script],
            env=full,
            cwd=self.root,
            capture_output=True,
            text=True,
            timeout=60,
        )


def _pinned_digests(repo: str) -> set[str]:
    text = (REPO / "deploy" / "compose.yaml").read_text(encoding="utf-8")
    return set(re.findall(rf"image:\s*{repo}:[^@\s]+@(sha256:[0-9a-f]{{64}})", text))


class Prev2Images(unittest.TestCase):
    def setUp(self) -> None:
        self.sb = _Sandbox()
        self.pull_log = self.sb.root / "pulls.txt"
        _write_exe(
            self.sb.bin / "docker",
            f"""
            if [ "$1" = pull ]; then shift; [ "$1" = -q ] && shift; echo "$1" >>"{self.pull_log}"; exit 0; fi
            echo "docker giả: lệnh không mong đợi: $*" >&2; exit 2
        """,
        )
        _write_exe(
            self.sb.bin / "gh",
            f"""
            if [ "$1 $2" = "release list" ]; then printf 'v0.1.46\\nv0.1.45\\nv0.1.44\\n'; exit 0; fi
            if [ "$1 $2" = "release download" ]; then
              dir=""; while [ $# -gt 0 ]; do [ "$1" = --dir ] && dir="$2"; shift; done
              mkdir -p "$dir"
              out="$dir/compose.release.yaml"
              printf 'services:\\n  api:\\n    image: "{GH_REF_1}"\\n  web:\\n    image: {GH_REF_2}\\n' >"$out"
              exit 0
            fi
            echo "gh giả: lệnh không mong đợi: $*" >&2; exit 2
        """,
        )
        self.script = _run_of("Giả lập ảnh tồn đọng")

    def tearDown(self) -> None:
        self.sb.close()

    def _refs(self) -> list[str]:
        return (self.sb.runner_temp / "prev2-refs.txt").read_text(encoding="utf-8").split()

    def test_v0148_pulls_old_untagged_caddy_redis_digests(self) -> None:
        r = self.sb.run(self.script, TAG="v0.1.48", PREV_TAG="v0.1.46")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        refs = self._refs()
        ext = [x for x in refs if not x.startswith("ghcr.io/")]
        caddy = [x for x in ext if x.startswith("caddy@sha256:")]
        redis = [x for x in ext if x.startswith("redis@sha256:")]
        self.assertEqual(len(caddy), 1, refs)
        self.assertEqual(len(redis), 1, refs)
        self.assertEqual(len(ext), 2, "chỉ kéo đúng 1 caddy + 1 redis cũ dạng `repo@sha256:` (không tag)")
        # Digest cũ phải KHÁC digest đang ghim — trùng thì genh giữ nó (đúng) và bước kiểm không chứng minh được gì.
        self.assertNotIn(caddy[0].split("@", 1)[1], _pinned_digests("caddy"))
        self.assertNotIn(redis[0].split("@", 1)[1], _pinned_digests("redis"))
        self.assertTrue(_pinned_digests("caddy"), "deploy/compose.yaml phải ghim digest caddy")
        self.assertTrue(_pinned_digests("redis"), "deploy/compose.yaml phải ghim digest redis")
        # Ảnh gen-harness-* của PREV2 vẫn được kéo + ghi như trước.
        self.assertIn(GH_REF_1, refs)
        self.assertIn(GH_REF_2, refs)
        pulled = self.pull_log.read_text(encoding="utf-8").split()
        self.assertEqual(sorted(pulled), sorted(refs), "mọi ref ghi vào prev2-refs.txt phải đã được kéo sẵn")

    def test_old_digests_pulled_even_without_prev2_release(self) -> None:
        # PREV_TAG là bản chính thức cũ nhất ⇒ không có PREV2, nhưng digest caddy/redis cũ vẫn phải được kiểm.
        r = self.sb.run(self.script, TAG="v0.1.48", PREV_TAG="v0.1.44")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        refs = self._refs()
        self.assertEqual(sorted(x.split("@")[0] for x in refs), ["caddy", "redis"], refs)

    def test_older_tag_does_not_expect_external_prune(self) -> None:
        # genh < v0.1.48 chưa dọn ảnh ngoài — chạy tay workflow cho tag cũ không được đỏ oan.
        r = self.sb.run(self.script, TAG="v0.1.47", PREV_TAG="v0.1.46")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        refs = self._refs()
        self.assertTrue(all(x.startswith("ghcr.io/") for x in refs), refs)
        self.assertEqual(sorted(refs), sorted([GH_REF_1, GH_REF_2]))


class OnlyCurrentAndPreviousImages(unittest.TestCase):
    def setUp(self) -> None:
        self.sb = _Sandbox()
        home = self.sb.root / "gh"
        (home / "deploy").mkdir(parents=True)
        cur = "ghcr.io/o/gen-harness-api@sha256:" + "a" * 64
        prev = "ghcr.io/o/gen-harness-api@sha256:" + "b" * 64
        pinned_caddy = f"caddy:2-alpine@sha256:{'c' * 64}"
        (home / "deploy" / "compose.yaml").write_text(
            f"services:\n  api:\n    image: {cur}\n  proxy:\n    image: {pinned_caddy}\n", encoding="utf-8"
        )
        (home / "deploy" / "compose.yaml.bak").write_text(
            f"services:\n  api:\n    image: {prev}\n  proxy:\n    image: caddy:2-alpine\n", encoding="utf-8"
        )
        self.home = home
        self.old_caddy = "caddy@sha256:" + "d" * 64
        (self.sb.runner_temp / "prev2-refs.txt").write_text(f"{self.old_caddy}\n{GH_REF_1}\n", encoding="utf-8")
        self.images = self.sb.root / "images.txt"
        _write_exe(
            self.sb.bin / "docker",
            f"""
            [ "$1" = images ] || {{ echo "docker giả: $*" >&2; exit 2; }}
            fmt=""; while [ $# -gt 0 ]; do [ "$1" = --format ] && fmt="$2"; shift; done
            case "$fmt" in
              '{{{{.Repository}}}}@{{{{.Digest}}}}') awk '{{print $1 "@" $2}}' "{self.images}" ;;
              '{{{{.Repository}}}} {{{{.Digest}}}}') cat "{self.images}" ;;
              *) cat "{self.images}" ;;
            esac
        """,
        )
        self.script = _run_of("Chỉ còn ảnh của bản hiện tại và bản liền trước")
        self.base_rows = [
            f"ghcr.io/o/gen-harness-api sha256:{'a' * 64}",
            f"ghcr.io/o/gen-harness-api sha256:{'b' * 64}",
            f"caddy sha256:{'c' * 64}",
            f"caddy sha256:{'e' * 64}",  # caddy:2-alpine của bản cũ (kéo theo tag) — được phép còn
        ]

    def tearDown(self) -> None:
        self.sb.close()

    def _run(self, rows: list[str]) -> subprocess.CompletedProcess[str]:
        self.images.write_text("\n".join(rows) + "\n", encoding="utf-8")
        return self.sb.run(self.script, GEN_HARNESS_HOME=str(self.home))

    def test_red_when_old_caddy_digest_left(self) -> None:
        r = self._run(self.base_rows + [f"caddy {self.old_caddy.split('@')[1]}"])
        self.assertNotEqual(r.returncode, 0, r.stdout)
        self.assertIn(self.old_caddy, r.stderr)

    def test_green_when_pruned(self) -> None:
        r = self._run(self.base_rows)
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)


class AccessStateBeforeUpgrade(unittest.TestCase):
    def setUp(self) -> None:
        self.sb = _Sandbox()
        self.home = self.sb.root / "gh"
        (self.home / "deploy").mkdir(parents=True)
        self.genv = self.sb.root / "github_env"
        self.genv.write_text("", encoding="utf-8")
        self.script = _run_of("Ghi lại cổng/chế độ truy cập TRƯỚC khi nâng cấp")

    def tearDown(self) -> None:
        self.sb.close()

    def test_runs_before_genh_update(self) -> None:
        self.assertLess(
            _step_index("Ghi lại cổng/chế độ truy cập TRƯỚC khi nâng cấp"),
            _step_index("genh update --yes --no-self-update"),
        )

    def _record(self, env_text: str | None) -> dict[str, str]:
        if env_text is not None:
            (self.home / "deploy" / ".env").write_text(env_text, encoding="utf-8")
        r = self.sb.run(self.script, GEN_HARNESS_HOME=str(self.home), GITHUB_ENV=str(self.genv), PREV_TAG="v0.1.46")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        out: dict[str, str] = {}
        for line in self.genv.read_text(encoding="utf-8").splitlines():
            k, _, v = line.partition("=")
            out[k] = v
        return out

    def test_v0146_install_keeps_local_bind(self) -> None:
        got = self._record("GH_PORT=8444\nGH_BIND_ADDR=127.0.0.1\nGH_ACCESS_MODE=local\n")
        self.assertEqual(got, {"PREV_BIND_ADDR": "127.0.0.1", "PREV_ACCESS_MODE": "local"})

    def test_pre_v0146_install_has_no_bind_addr(self) -> None:
        got = self._record("GH_PORT=8444\n")
        self.assertEqual(got, {"PREV_BIND_ADDR": "", "PREV_ACCESS_MODE": ""})

    def test_missing_env_file(self) -> None:
        got = self._record(None)
        self.assertEqual(got, {"PREV_BIND_ADDR": "", "PREV_ACCESS_MODE": ""})


class ClaudeSessionAfterUpgrade(unittest.TestCase):
    """Gốc lỗi E2E release v0.1.65: api không còn tự chuyển phiên Claude từ /var/lib/gh/agy/claude (v0.1.63, review P1-1),
    nhưng job nâng cấp vẫn đặt tệp giả ở đường dẫn cũ rồi đòi nó sang claude_state ⇒ đỏ ở mọi bản."""

    def test_no_legacy_probe_step_or_migration_assertion(self) -> None:
        names = [str(st.get("name", "")) for st in _steps()]
        self.assertFalse([n for n in names if "tệp giả" in n], "không được cài tệp giả ở đường dẫn phiên Claude cũ")
        for st in _steps():
            run = str(st.get("run", ""))
            self.assertNotIn("grep -q probe", run)
            self.assertNotIn("/var/lib/gh/agy/claude/.claude/.claude.json", run)
            self.assertNotIn("claude_probe", str(st))

    def test_after_upgrade_old_path_absent_and_claude_state_present(self) -> None:
        run = _run_of("Phiên Claude ở claude_state riêng")
        self.assertIn("test ! -e /var/lib/gh/agy/claude", run)
        self.assertIn("test -d /var/lib/gh/claude/.claude", run)
        self.assertIn("agy_canary --offline", run)
        self.assertNotIn("if", _steps()[_step_index("Phiên Claude ở claude_state riêng")], "bước phải chạy vô điều kiện")


if __name__ == "__main__":
    unittest.main()
