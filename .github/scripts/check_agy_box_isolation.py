#!/usr/bin/env python3
"""Kiểm cấu hình CÔ LẬP của dịch vụ `agy-box` trong một compose.yaml (v0.1.63, H3).

`agy-box` là hộp riêng chạy Antigravity CLI cho agent ngoại giao: nội dung người ngoài có lừa được agy đọc tệp hay chạy
lệnh cũng không được chạm khoá master, CSDL hay Redis chính. Điều đó chỉ đúng khi compose mang theo đúng cấu hình, nên bản
phát hành (release.yml, job pin-compose) kiểm lại trên deploy/compose.release.yaml TRƯỚC khi chở vào genh.

Dùng: python3 .github/scripts/check_agy_box_isolation.py <compose.yaml> [--release]
  --release: compose đã ghim (không `build:`, `image:` có @sha256 và TRÙNG ảnh của api).
  Không có --release: compose nguồn (`build:` từ deploy/images/api.Dockerfile).
Exit 0 = đạt; 1 = vi phạm (mỗi lỗi một dòng `::error::`); 2 = không đọc được tệp.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]  # PyYAML không kèm stub kiểu

BOX = "agy-box"
EGRESS = "agy-egress"
EGRESS_URL = "http://agy-egress:3128"
KEY = "gh_agy_key"
ALLOWED_ENV = {
    "GH_AGYBOX_KEY_FILE",
    "GH_CLI_BINARY",
    "AGY_CLI_DISABLE_AUTO_UPDATE",
    "GH_AGYBOX_MAX_JOBS",
    "HTTPS_PROXY",
    "HTTP_PROXY",
    "NO_PROXY",
    "GH_AGYBOX_REAP",
}
# Review v0.1.63 (P2-2): hộp CHỈ ở agy-bus (internal); ra Internet duy nhất qua proxy agy-egress (chặn IP nội bộ).
ALLOWED_NETWORKS = {"agy-bus"}
EGRESS_NETWORKS = {"agy-bus", "agy-out"}
EGRESS_ENV = {"GH_AGY_EGRESS_ALLOW", "GH_AGY_EGRESS_PORTS", "GH_AGY_EGRESS_PORT"}
# Dịch vụ được ở agy-bus (gọi vào hộp / hộp gọi proxy) và được giữ khoá HMAC.
BUS_MEMBERS = {"api", "worker", BOX, EGRESS}
KEY_HOLDERS = {"api", "worker", BOX}
# Khoá/trường tuyệt đối không được có ở hộp.
# Review v0.1.63 (P3): thêm volumes_from (mượn volume của api/worker), extends (kế thừa cấu hình dịch vụ khác), sysctls,
# cgroup_parent. Áp cho cả hộp lẫn proxy lối ra.
FORBIDDEN_KEYS = ("privileged", "cap_add", "devices", "pid", "ipc", "userns_mode", "network_mode", "ports", "env_file",
                  "volumes_from", "extends", "sysctls", "cgroup_parent")
BIND_PREFIXES = ("/", ".", "~", "$")


def _secret_names(svc: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for s in svc.get("secrets") or []:
        out.append(str(s.get("source", "")) if isinstance(s, dict) else str(s))
    return out


def _networks(svc: dict[str, Any]) -> set[str]:
    n = svc.get("networks")
    if not n:
        return {"default"}  # không khai ⇒ compose đặt vào mạng default
    return {str(k) for k in (n.keys() if isinstance(n, dict) else n)}


def _env_keys(svc: dict[str, Any]) -> set[str]:
    env = svc.get("environment") or {}
    if isinstance(env, dict):
        return {str(k) for k in env}
    return {str(e).split("=", 1)[0] for e in env}


def _volumes(svc: dict[str, Any]) -> list[tuple[str, str]]:
    """(loại, nguồn): loại là 'volume' (có tên), 'bind', 'anon' hoặc 'tmpfs'."""
    out: list[tuple[str, str]] = []
    for v in svc.get("volumes") or []:
        if isinstance(v, dict):
            typ = str(v.get("type", "volume"))
            out.append((typ, str(v.get("source", ""))))
            continue
        parts = str(v).split(":")
        if len(parts) == 1:
            out.append(("anon", parts[0]))
        elif parts[0].startswith(BIND_PREFIXES):
            out.append(("bind", parts[0]))
        else:
            out.append(("volume", parts[0]))
    return out


def _is_digest_ref(image: str) -> bool:
    return re.search(r"@sha256:[0-9a-f]{64}$", image) is not None


def check(compose: dict[str, Any], release: bool) -> list[str]:
    errs: list[str] = []
    services: dict[str, Any] = compose.get("services") or {}
    box = services.get(BOX)
    if not isinstance(box, dict):
        return [f"compose không có dịch vụ {BOX}"]

    # Bí mật: duy nhất gh_agy_key.
    secrets = _secret_names(box)
    if KEY not in secrets:
        errs.append(f"{BOX} thiếu secret {KEY} (không kiểm được chữ ký yêu cầu)")
    for s in secrets:
        if s != KEY:
            errs.append(f"{BOX} mount secret {s} — chỉ được {KEY}")

    # Mạng: chỉ agy-bus (internal); không default, không agy-out (ra ngoài chỉ qua proxy).
    nets = _networks(box)
    if "default" in nets:
        errs.append(f"{BOX} ở mạng default (khai tường minh hoặc quên khai networks) — sẽ thấy db/redis/bridge")
    for n in sorted(nets - ALLOWED_NETWORKS - {"default"}):
        errs.append(f"{BOX} ở mạng {n} — chỉ được {sorted(ALLOWED_NETWORKS)}")
    for n in sorted(ALLOWED_NETWORKS - nets):
        errs.append(f"{BOX} không ở mạng {n}")
    top_nets = compose.get("networks") or {}
    bus = top_nets.get("agy-bus")
    if not (isinstance(bus, dict) and bus.get("internal") is True):
        errs.append("mạng agy-bus phải khai internal: true")
    if (top_nets.get("agy-out") or {}).get("internal") is True:
        errs.append("mạng agy-out không được internal (proxy agy-egress cần ra Internet gọi Google)")
    for name, svc in services.items():
        if not isinstance(svc, dict):
            continue
        if "agy-bus" in _networks(svc) and name not in BUS_MEMBERS:
            errs.append(f"dịch vụ {name} ở mạng agy-bus — chỉ {sorted(BUS_MEMBERS)} được ở đó")
        if "agy-out" in _networks(svc) and name != EGRESS:
            errs.append(f"dịch vụ {name} ở mạng agy-out — chỉ {EGRESS} được ra Internet qua mạng này")
        if name not in KEY_HOLDERS and KEY in _secret_names(svc):
            errs.append(f"dịch vụ {name} mount {KEY} — chỉ {sorted(KEY_HOLDERS)} được giữ khoá này")

    # Đặc quyền.
    drops = [str(c).upper() for c in (box.get("cap_drop") or [])]
    if "ALL" not in drops:
        errs.append(f"{BOX} thiếu cap_drop: [ALL]")
    for k in FORBIDDEN_KEYS:
        if box.get(k):
            errs.append(f"{BOX} có {k} — không được dùng ở hộp cô lập")
    sec_opt = [str(o) for o in (box.get("security_opt") or [])]
    if not any(o in ("no-new-privileges:true", "no-new-privileges=true") for o in sec_opt):
        errs.append(f"{BOX} thiếu security_opt no-new-privileges:true")
    for o in sec_opt:
        if "unconfined" in o:
            errs.append(f"{BOX} có security_opt {o} (unconfined) — không được tắt hàng rào")
    if box.get("read_only") is not True:
        errs.append(f"{BOX} thiếu read_only: true")
    user = box.get("user")
    if user is not None and re.match(r"^(root|0)(:|$)", str(user)):
        errs.append(f"{BOX} chạy bằng user {user} (root) — phải dùng user gh của ảnh")

    # Môi trường: chỉ danh sách cho phép (không DB, Redis, khoá master/bridge/browser/offsite).
    for k in sorted(_env_keys(box) - ALLOWED_ENV):
        errs.append(f"{BOX} có biến môi trường {k} — ngoài danh sách cho phép {sorted(ALLOWED_ENV)}")
    env = box.get("environment") or {}
    env_map = env if isinstance(env, dict) else dict(str(e).split("=", 1) for e in env if "=" in str(e))
    if str(env_map.get("HTTPS_PROXY", "")) != EGRESS_URL:
        errs.append(f"{BOX} thiếu HTTPS_PROXY={EGRESS_URL} (agy phải ra ngoài qua proxy lối ra)")
    # Review lần 2 v0.1.63 (P2-1): hộp phải dọn tiến trình agy để lại (kể cả đã setsid) + /tmp, /dev/shm sau mỗi lượt.
    if str(env_map.get("GH_AGYBOX_REAP", "")) != "1":
        errs.append(f"{BOX} thiếu GH_AGYBOX_REAP=\"1\" (dọn tiến trình/tệp agy để lại sau mỗi lượt)")

    errs += _check_egress(services, release)

    # Volume (review v0.1.63, P1-1): KHÔNG volume nào ngoài tmpfs — kể cả agy_state. Token agy đến theo từng yêu cầu đã
    # ký; hộp mount volume ghi được thì agy bị lừa trong hộp ghi được cấu hình/phiên mà api/worker đọc lại.
    for typ, src in _volumes(box):
        if typ == "tmpfs":
            continue
        if typ == "bind":
            errs.append(f"{BOX} mount kiểu bind {src} (vd docker.sock, run/) — không được")
        elif typ == "anon":
            errs.append(f"{BOX} có volume ẩn danh {src}")
        else:
            errs.append(f"{BOX} mount volume {src} — hộp không được mount volume nào (token đến theo yêu cầu đã ký)")

    # Ảnh: bản phát hành dùng đúng ảnh api đã ghim digest; bản nguồn build từ api.Dockerfile.
    image = str(box.get("image") or "")
    build = box.get("build")
    if release:
        if build:
            errs.append(f"{BOX} còn build: trong compose phát hành — chưa được ghim image")
        if not _is_digest_ref(image):
            errs.append(f"{BOX} image {image!r} chưa ghim digest @sha256")
        api_image = str((services.get("api") or {}).get("image") or "")
        if image != api_image:
            errs.append(f"{BOX} image khác ảnh api ({image!r} ≠ {api_image!r}) — hộp phải dùng đúng ảnh api đã ghim")
    else:
        dockerfile = build.get("dockerfile") if isinstance(build, dict) else None
        if not (isinstance(dockerfile, str) and dockerfile.endswith("deploy/images/api.Dockerfile")):
            errs.append(f"{BOX} phải build từ deploy/images/api.Dockerfile (dùng chung ảnh api), đang: {build!r}")
    return errs


def _check_egress(services: dict[str, Any], release: bool) -> list[str]:
    """Proxy lối ra của hộp (review v0.1.63, P2-2): cùng ảnh api, không khoá, không volume, đúng hai mạng, đã khoá đặc
    quyền."""
    eg = services.get(EGRESS)
    if not isinstance(eg, dict):
        return [f"compose không có dịch vụ {EGRESS} (proxy lối ra của hộp)"]
    errs: list[str] = []
    if _secret_names(eg):
        errs.append(f"{EGRESS} mount secret {_secret_names(eg)} — proxy không được giữ khoá nào")
    if eg.get("volumes") or eg.get("volumes_from"):
        errs.append(f"{EGRESS} mount volume — proxy không được mount gì")
    nets = _networks(eg)
    if nets != EGRESS_NETWORKS:
        errs.append(f"{EGRESS} ở mạng {sorted(nets)} — phải đúng {sorted(EGRESS_NETWORKS)}")
    if "ALL" not in [str(c).upper() for c in (eg.get("cap_drop") or [])]:
        errs.append(f"{EGRESS} thiếu cap_drop: [ALL]")
    if not any(str(o) in ("no-new-privileges:true", "no-new-privileges=true") for o in (eg.get("security_opt") or [])):
        errs.append(f"{EGRESS} thiếu security_opt no-new-privileges:true")
    if eg.get("read_only") is not True:
        errs.append(f"{EGRESS} thiếu read_only: true")
    for k in FORBIDDEN_KEYS:
        if eg.get(k):
            errs.append(f"{EGRESS} có {k} — không được dùng ở proxy lối ra")
    for k in sorted(_env_keys(eg) - EGRESS_ENV):
        errs.append(f"{EGRESS} có biến môi trường {k} — ngoài danh sách cho phép {sorted(EGRESS_ENV)}")
    if list(eg.get("command") or []) != ["python", "-m", "gh.agybox.egress"]:
        errs.append(f"{EGRESS} phải chạy python -m gh.agybox.egress")
    image = str(eg.get("image") or "")
    if release:
        api_image = str((services.get("api") or {}).get("image") or "")
        if eg.get("build") or not _is_digest_ref(image) or image != api_image:
            errs.append(f"{EGRESS} image {image!r} phải là đúng ảnh api đã ghim digest ({api_image!r})")
    else:
        build = eg.get("build")
        dockerfile = build.get("dockerfile") if isinstance(build, dict) else None
        if not (isinstance(dockerfile, str) and dockerfile.endswith("deploy/images/api.Dockerfile")):
            errs.append(f"{EGRESS} phải build từ deploy/images/api.Dockerfile (dùng chung ảnh api), đang: {build!r}")
    return errs


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("compose")
    p.add_argument("--release", action="store_true")
    args = p.parse_args(argv)
    path = Path(args.compose)
    if not path.is_file():
        print(f"check_agy_box_isolation: không thấy {path}", file=sys.stderr)
        return 2
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (yaml.YAMLError, UnicodeDecodeError) as e:
        print(f"check_agy_box_isolation: {path} không phải YAML hợp lệ: {e}", file=sys.stderr)
        return 2
    errs = check(data, release=args.release)
    if errs:
        for e in errs:
            print(f"::error::{e}")
        print(f"{len(errs)} vi phạm cô lập {BOX} trong {path}")
        return 1
    print(f"OK: {BOX} đúng cấu hình cô lập ({'bản phát hành' if args.release else 'bản nguồn'}) trong {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
