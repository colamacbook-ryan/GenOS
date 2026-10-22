"""v0.1.63 H1 — cấu hình cô lập của `agy-box` trong deploy/compose.yaml (đọc YAML, không cần Docker).

Bản thật được kiểm thêm bằng hộp dựng thật (project riêng) — xem docs/api/agybox-protocol.md mục "Kiểm cô lập"."""

from pathlib import Path
from typing import Any

import pytest
import yaml

REPO = Path(__file__).resolve().parents[3]
COMPOSE = yaml.safe_load((REPO / "deploy" / "compose.yaml").read_text(encoding="utf-8"))
SERVICES: dict[str, Any] = COMPOSE["services"]
BOX: dict[str, Any] = SERVICES["agy-box"]
ENV_ALLOWED = {"GH_AGYBOX_KEY_FILE", "GH_CLI_BINARY", "AGY_CLI_DISABLE_AUTO_UPDATE", "GH_AGYBOX_MAX_JOBS",
               "HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY", "GH_AGYBOX_REAP"}


def _nets(svc: dict[str, Any]) -> set[str]:
    n = svc.get("networks", ["default"])
    return set(n if isinstance(n, list) else n.keys())


def test_box_has_only_the_agy_key_secret() -> None:
    assert BOX["secrets"] == ["gh_agy_key"]
    assert COMPOSE["secrets"]["gh_agy_key"] == {"file": "../secrets/gh_agy_key"}


def test_box_env_is_exactly_the_allowlist_no_db_redis_or_keys() -> None:
    env = BOX["environment"]
    assert set(env) == ENV_ALLOWED
    assert "env_file" not in BOX
    flat = " ".join(f"{k}={v}" for k, v in env.items()).lower()
    for bad in ("database", "redis", "master", "bridge", "browser", "offsite", "backup", "setup_token"):
        assert bad not in flat, bad


def test_box_network_is_internal_agy_bus_only() -> None:
    """Review v0.1.63 (P2-2): hộp chỉ ở agy-bus (internal); ra ngoài qua proxy agy-egress (chặn IP nội bộ)."""
    assert _nets(BOX) == {"agy-bus"}
    nets = COMPOSE["networks"]
    assert nets["agy-bus"].get("internal") is True
    assert not nets["agy-out"].get("internal")
    assert "ports" not in BOX and "expose" not in BOX


def test_box_hardening() -> None:
    assert BOX["read_only"] is True
    assert BOX["cap_drop"] == ["ALL"]
    assert "cap_add" not in BOX
    assert "no-new-privileges:true" in BOX["security_opt"]
    assert not any("seccomp=unconfined" in o or "apparmor=unconfined" in o for o in BOX["security_opt"])
    assert BOX["init"] is True
    assert BOX["mem_limit"] == "1g" and BOX["cpus"] == 1.5 and BOX["pids_limit"] == 256
    assert BOX["tmpfs"] == ["/tmp:size=256m"]
    assert BOX.get("privileged") is not True and "user" not in BOX        # user gh của ảnh (không root)
    assert BOX["command"] == ["python", "-m", "gh.agybox.server"]
    assert BOX["build"]["dockerfile"] == "deploy/images/api.Dockerfile"
    assert BOX["healthcheck"]["test"][0] == "CMD" and "8090/healthz" in BOX["healthcheck"]["test"][-1]


def test_box_mounts_no_volume_at_all() -> None:
    """Review v0.1.63 (P1-1): hộp không mount volume nào — kể cả agy_state (token đến theo từng yêu cầu đã ký); thứ duy
    nhất ghi được là tmpfs /tmp (HOME tạm mỗi lượt), mất khi container dừng."""
    assert "volumes" not in BOX and "volumes_from" not in BOX
    assert "GH_CLI_HOME" not in BOX["environment"]


def test_only_api_and_worker_reach_the_box() -> None:
    on_bus = {n for n, s in SERVICES.items() if "agy-bus" in _nets(s)}
    assert on_bus == {"api", "worker", "agy-box", "agy-egress"}
    assert {n for n, s in SERVICES.items() if "agy-out" in _nets(s)} == {"agy-egress"}
    holders = {n for n, s in SERVICES.items() if "gh_agy_key" in (s.get("secrets") or [])}
    assert holders == {"api", "worker", "agy-box"}
    for n in ("api", "worker"):
        assert SERVICES[n]["environment"]["GH_AGYBOX_URL"] == "http://agy-box:8090"
        assert SERVICES[n]["environment"]["GH_AGYBOX_KEY_FILE"] == "/run/secrets/gh_agy_key"
    for n in ("api", "worker"):
        assert "default" in _nets(SERVICES[n])


def test_box_not_on_default_or_browser_networks_and_peers_not_exposed() -> None:
    assert not _nets(BOX) & {"default", "browser", "browser-bus", "browser-out"}
    for n in ("db", "redis", "bridge", "browser-redis", "migrate"):
        assert not _nets(SERVICES[n]) & {"agy-bus", "agy-out"}, n


def test_agy_state_only_in_api_worker() -> None:
    users = {n for n, s in SERVICES.items() if "agy_state" in str(s.get("volumes", ""))}
    assert users == {"api", "worker"}


def test_make_secrets_generates_the_key() -> None:
    mk = (REPO / "Makefile").read_text(encoding="utf-8")
    assert "secrets/gh_agy_key ||" in mk and "secrets/gh_agy_key secrets/gh_offsite_key" in mk


@pytest.mark.parametrize("name", ["api", "worker"])
def test_api_worker_still_on_default_network_and_browser_bus(name: str) -> None:
    assert {"default", "browser-bus"} <= _nets(SERVICES[name])
