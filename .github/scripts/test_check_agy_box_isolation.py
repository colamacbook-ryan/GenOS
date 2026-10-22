"""Test check_agy_box_isolation.py (v0.1.63 H3): bản phát hành phải mang agy-box đúng cấu hình cô lập.

Mỗi vi phạm cách ly là một test riêng: sửa đúng MỘT chỗ của compose mẫu (khớp khối agy-box của H1) rồi đòi
script báo lỗi nhắc đúng chỗ đó. Không cần Docker."""

import copy
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml  # type: ignore[import-untyped]  # PyYAML không kèm stub kiểu

HERE = Path(__file__).resolve().parent
SCRIPT = HERE / "check_agy_box_isolation.py"
sys.path.insert(0, str(HERE))
import check_agy_box_isolation as chk  # noqa: E402

API_IMG = "ghcr.io/o/gen-harness-api@sha256:" + "a" * 64

GOOD = {
    "services": {
        "db": {"image": "ghcr.io/o/gen-harness-db@sha256:" + "d" * 64, "secrets": ["gh_master_key"]},
        "redis": {"image": "redis:7-alpine"},
        "api": {
            "image": API_IMG,
            "secrets": ["gh_master_key", "gh_bridge_key", "gh_browser_key", "gh_offsite_key", "gh_agy_key"],
            "networks": ["default", "browser-bus", "agy-bus"],
            "volumes": ["agy_state:/var/lib/gh/agy", "claude_state:/var/lib/gh/claude"],
        },
        "worker": {
            "image": API_IMG,
            "secrets": ["gh_master_key", "gh_bridge_key", "gh_browser_key", "gh_agy_key"],
            "networks": ["default", "browser-bus", "agy-bus"],
        },
        "agy-box": {
            "image": API_IMG,
            "command": ["python", "-m", "gh.agybox.server"],
            "environment": {
                "GH_AGYBOX_KEY_FILE": "/run/secrets/gh_agy_key",
                "GH_CLI_BINARY": "agy",
                "AGY_CLI_DISABLE_AUTO_UPDATE": "1",
                "GH_AGYBOX_MAX_JOBS": "1",
                "HTTPS_PROXY": "http://agy-egress:3128",
                "HTTP_PROXY": "http://agy-egress:3128",
                "NO_PROXY": "127.0.0.1,localhost",
                "GH_AGYBOX_REAP": "1",
            },
            "secrets": ["gh_agy_key"],
            "networks": ["agy-bus"],
            "read_only": True,
            "tmpfs": ["/tmp:size=256m"],
            "cap_drop": ["ALL"],
            "security_opt": ["no-new-privileges:true"],
            "init": True,
        },
        "agy-egress": {
            "image": API_IMG,
            "command": ["python", "-m", "gh.agybox.egress"],
            "environment": {"GH_AGY_EGRESS_ALLOW": "", "GH_AGY_EGRESS_PORTS": "443"},
            "networks": ["agy-bus", "agy-out"],
            "read_only": True,
            "cap_drop": ["ALL"],
            "security_opt": ["no-new-privileges:true"],
        },
    },
    "networks": {"default": {}, "browser-bus": {"internal": True}, "agy-bus": {"internal": True}, "agy-out": {}},
    "volumes": {"agy_state": None, "claude_state": None},
    "secrets": {k: {"file": f"../secrets/{k}"} for k in
                ["gh_master_key", "gh_bridge_key", "gh_browser_key", "gh_offsite_key", "gh_agy_key"]},
}


def box(d: dict) -> dict:
    return d["services"]["agy-box"]


def mutated(fn) -> dict:
    d = copy.deepcopy(GOOD)
    fn(d)
    return d


class IsolationRules(unittest.TestCase):
    def assertFlags(self, d: dict, needle: str, release: bool = True) -> None:
        errs = chk.check(d, release=release)
        self.assertTrue(any(needle in e for e in errs), f"muốn lỗi chứa {needle!r}, được {errs}")

    def test_good_compose_passes_both_modes(self) -> None:
        self.assertEqual(chk.check(GOOD, release=True), [])
        d = copy.deepcopy(GOOD)
        for s in ("api", "worker", "agy-box", "agy-egress"):
            d["services"][s].pop("image")
            d["services"][s]["build"] = {"context": "..", "dockerfile": "deploy/images/api.Dockerfile"}
        self.assertEqual(chk.check(d, release=False), [])

    def test_missing_service(self) -> None:
        d = copy.deepcopy(GOOD)
        del d["services"]["agy-box"]
        self.assertFlags(d, "agy-box")

    def test_extra_secret(self) -> None:
        self.assertFlags(mutated(lambda d: box(d).update(secrets=["gh_agy_key", "gh_master_key"])), "gh_master_key")

    def test_no_secret_at_all(self) -> None:
        self.assertFlags(mutated(lambda d: box(d).update(secrets=[])), "gh_agy_key")

    def test_secret_long_syntax_extra(self) -> None:
        self.assertFlags(mutated(lambda d: box(d).update(secrets=["gh_agy_key", {"source": "gh_bridge_key"}])), "gh_bridge_key")

    def test_default_network_explicit(self) -> None:
        self.assertFlags(mutated(lambda d: box(d).update(networks=["agy-bus", "default"])), "default")

    def test_default_network_implicit(self) -> None:
        self.assertFlags(mutated(lambda d: box(d).pop("networks")), "default")

    def test_network_mode_host(self) -> None:
        self.assertFlags(mutated(lambda d: box(d).update(network_mode="host")), "network_mode")

    def test_agy_bus_not_internal(self) -> None:
        self.assertFlags(mutated(lambda d: d["networks"]["agy-bus"].update(internal=False)), "internal")

    def test_agy_bus_shared_with_other_service(self) -> None:
        self.assertFlags(mutated(lambda d: d["services"]["db"].update(networks=["default", "agy-bus"])), "agy-bus")

    def test_agy_out_shared_with_other_service(self) -> None:
        self.assertFlags(mutated(lambda d: d["services"]["api"]["networks"].append("agy-out")), "agy-out")

    def test_box_on_agy_out_is_refused(self) -> None:
        """Review v0.1.63 (P2-2): hộp ở agy-out (bridge thường) tới được gateway + LAN — chỉ proxy được ở đó."""
        self.assertFlags(mutated(lambda d: box(d).update(networks=["agy-bus", "agy-out"])), "agy-out")

    def test_box_without_proxy_env(self) -> None:
        self.assertFlags(mutated(lambda d: box(d)["environment"].pop("HTTPS_PROXY")), "HTTPS_PROXY")
        self.assertFlags(mutated(lambda d: box(d)["environment"].update(HTTPS_PROXY="http://evil:3128")),
                         "HTTPS_PROXY")

    def test_box_without_reap(self) -> None:
        """Review lần 2 v0.1.63 (P2-1): thiếu GH_AGYBOX_REAP=1 ⇒ tiến trình setsid của agy sống qua các lượt."""
        self.assertFlags(mutated(lambda d: box(d)["environment"].pop("GH_AGYBOX_REAP")), "GH_AGYBOX_REAP")
        self.assertFlags(mutated(lambda d: box(d)["environment"].update(GH_AGYBOX_REAP="0")), "GH_AGYBOX_REAP")

    def test_egress_missing(self) -> None:
        self.assertFlags(mutated(lambda d: d["services"].pop("agy-egress")), "agy-egress")

    def test_egress_rules(self) -> None:
        eg = lambda d: d["services"]["agy-egress"]  # noqa: E731
        self.assertFlags(mutated(lambda d: eg(d).update(secrets=["gh_agy_key"])), "agy-egress")
        self.assertFlags(mutated(lambda d: eg(d).update(volumes=["agy_state:/x"])), "volume")
        self.assertFlags(mutated(lambda d: eg(d).update(networks=["default", "agy-bus", "agy-out"])), "agy-egress")
        self.assertFlags(mutated(lambda d: eg(d).pop("cap_drop")), "cap_drop")
        self.assertFlags(mutated(lambda d: eg(d).pop("read_only")), "read_only")
        self.assertFlags(mutated(lambda d: eg(d).update(privileged=True)), "privileged")
        self.assertFlags(mutated(lambda d: eg(d)["environment"].update(GH_DATABASE_URL="x")), "GH_DATABASE_URL")
        self.assertFlags(mutated(lambda d: eg(d).update(image="ghcr.io/o/x@sha256:" + "c" * 64)), "agy-egress")
        self.assertFlags(mutated(lambda d: eg(d).update(command=["sh"])), "gh.agybox.egress")

    def test_cap_drop_missing(self) -> None:
        self.assertFlags(mutated(lambda d: box(d).pop("cap_drop")), "cap_drop")

    def test_cap_add(self) -> None:
        self.assertFlags(mutated(lambda d: box(d).update(cap_add=["NET_ADMIN"])), "cap_add")

    def test_privileged(self) -> None:
        self.assertFlags(mutated(lambda d: box(d).update(privileged=True)), "privileged")

    def test_no_new_privileges_missing(self) -> None:
        self.assertFlags(mutated(lambda d: box(d).update(security_opt=[])), "no-new-privileges")

    def test_unconfined_security_opt(self) -> None:
        self.assertFlags(mutated(lambda d: box(d)["security_opt"].append("seccomp=unconfined")), "unconfined")

    def test_not_read_only(self) -> None:
        self.assertFlags(mutated(lambda d: box(d).update(read_only=False)), "read_only")

    def test_root_user(self) -> None:
        self.assertFlags(mutated(lambda d: box(d).update(user="root")), "user")

    def test_env_database_url(self) -> None:
        self.assertFlags(mutated(lambda d: box(d)["environment"].update(GH_DATABASE_URL="postgresql://x")), "GH_DATABASE_URL")

    def test_env_list_form(self) -> None:
        self.assertFlags(mutated(lambda d: box(d).update(environment=["GH_REDIS_URL=redis://redis:6379"])), "GH_REDIS_URL")

    def test_env_file(self) -> None:
        self.assertFlags(mutated(lambda d: box(d).update(env_file=[".env"])), "env_file")

    def test_extra_volume_named(self) -> None:
        self.assertFlags(mutated(lambda d: box(d).update(volumes=["claude_state:/var/lib/gh/claude"])), "claude_state")

    def test_bind_mount_short(self) -> None:
        self.assertFlags(mutated(lambda d: box(d).update(volumes=["/var/run/docker.sock:/var/run/docker.sock"])),
                         "docker.sock")

    def test_bind_mount_long(self) -> None:
        self.assertFlags(mutated(lambda d: box(d).update(volumes=[{"type": "bind", "source": "../run",
                                                                    "target": "/x"}])), "bind")

    def test_agy_state_mounted_is_refused(self) -> None:
        """Review v0.1.63 (P1-1): hộp mount agy_state (kể cả nocopy / chỉ đọc) ⇒ vi phạm — token đến theo yêu cầu."""
        for v in ("agy_state:/var/lib/gh/agy", "agy_state:/var/lib/gh/agy:ro",
                  {"type": "volume", "source": "agy_state", "target": "/var/lib/gh/agy", "volume": {"nocopy": True}}):
            self.assertFlags(mutated(lambda d, v=v: box(d).update(volumes=[v])), "agy_state")

    def test_gh_cli_home_env_is_refused(self) -> None:
        self.assertFlags(mutated(lambda d: box(d)["environment"].update(GH_CLI_HOME="/var/lib/gh/agy/x")),
                         "GH_CLI_HOME")

    def test_volumes_from_extends_sysctls_refused(self) -> None:
        """Review v0.1.63 (P3): volumes_from (lấy volume của api), extends (kế thừa cấu hình dịch vụ khác), sysctls,
        cgroup_parent — ở hộp lẫn proxy lối ra."""
        for k, v in (("volumes_from", ["api"]), ("extends", {"service": "api"}), ("sysctls", {"net.ipv4.ip_forward": 1}),
                     ("cgroup_parent", "x")):
            self.assertFlags(mutated(lambda d, k=k, v=v: box(d).update({k: v})), k)
            self.assertFlags(mutated(lambda d, k=k, v=v: d["services"]["agy-egress"].update({k: v})), k)

    def test_privileged_pid_ipc_host_also_on_egress(self) -> None:
        for k, v in (("privileged", True), ("pid", "host"), ("ipc", "host"), ("network_mode", "host")):
            self.assertFlags(mutated(lambda d, k=k, v=v: box(d).update({k: v})), k)
            self.assertFlags(mutated(lambda d, k=k, v=v: d["services"]["agy-egress"].update({k: v})), k)

    def test_published_ports(self) -> None:
        self.assertFlags(mutated(lambda d: box(d).update(ports=["8090:8090"])), "ports")

    def test_devices_and_pid_host(self) -> None:
        self.assertFlags(mutated(lambda d: box(d).update(devices=["/dev/fuse"])), "devices")
        self.assertFlags(mutated(lambda d: box(d).update(pid="host")), "pid")

    def test_agy_key_leaks_to_other_service(self) -> None:
        self.assertFlags(mutated(lambda d: d["services"]["db"].update(secrets=["gh_master_key", "gh_agy_key"])), "gh_agy_key")

    def test_release_must_pin_digest(self) -> None:
        self.assertFlags(mutated(lambda d: box(d).update(image="ghcr.io/o/gen-harness-api:latest")), "digest")

    def test_release_must_not_build(self) -> None:
        self.assertFlags(mutated(lambda d: box(d).update(build={"context": ".."})), "build")

    def test_release_box_image_equals_api_image(self) -> None:
        self.assertFlags(mutated(lambda d: box(d).update(image="ghcr.io/o/gen-harness-api@sha256:" + "b" * 64)), "api")

    def test_source_mode_requires_api_dockerfile(self) -> None:
        d = copy.deepcopy(GOOD)
        for s in ("api", "worker"):
            d["services"][s].pop("image")
            d["services"][s]["build"] = {"context": "..", "dockerfile": "deploy/images/api.Dockerfile"}
        box(d).pop("image")
        box(d)["build"] = {"context": "..", "dockerfile": "deploy/images/browser.Dockerfile"}
        self.assertFlags(d, "api.Dockerfile", release=False)

    def test_one_line_build_string_form(self) -> None:
        # compose cho phép build: ./dir (chuỗi) — không phải api.Dockerfile ⇒ lỗi, không được vỡ script.
        d = copy.deepcopy(GOOD)
        box(d).pop("image")
        box(d)["build"] = "./x"
        self.assertFlags(d, "api.Dockerfile", release=False)


class Cli(unittest.TestCase):
    def run_cli(self, d: dict, *flags: str) -> subprocess.CompletedProcess[str]:
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "compose.release.yaml"
            p.write_text(yaml.safe_dump(d, allow_unicode=True), encoding="utf-8")
            return subprocess.run([sys.executable, str(SCRIPT), str(p), *flags], capture_output=True, text=True, check=False)

    def test_exit_0_and_ok_message(self) -> None:
        r = self.run_cli(GOOD, "--release")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("OK", r.stdout)

    def test_exit_1_with_github_error_annotation(self) -> None:
        d = mutated(lambda d: box(d).update(secrets=["gh_agy_key", "gh_master_key"]))
        r = self.run_cli(d, "--release")
        self.assertEqual(r.returncode, 1)
        self.assertIn("::error::", r.stdout)
        self.assertIn("gh_master_key", r.stdout)

    def test_exit_2_when_file_missing(self) -> None:
        r = subprocess.run([sys.executable, str(SCRIPT), "/không/có/compose.yaml"], capture_output=True, text=True, check=False)
        self.assertEqual(r.returncode, 2)


if __name__ == "__main__":
    unittest.main()
