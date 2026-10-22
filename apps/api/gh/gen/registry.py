"""Registry màn + mục tiêu làm sáng của Gen (docs/design/gen-v1.md §3.5).

Nguồn sự thật là `packages/contracts/src/genTargets.ts`; `registry.json` là bản xuất (test web
`gen-targets.test.ts` bảo đảm hai bên khớp) để image API không phụ thuộc mã TypeScript.
"""

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from gh.auth import rbac

REGISTRY_PATH = Path(__file__).with_name("registry.json")

# Trang ngoài danh mục màn (rbac.SCREEN_PERMISSION): quyền cần có để Gen được mở/chỉ vào.
EXTRA_SCREEN_PERMISSION: dict[str, tuple[str, ...] | None] = {
    "guide": ("system.manage",),  # Hướng dẫn thiết lập = việc thiết lập của Owner
    "account": None,  # ai đăng nhập cũng có "Tài khoản của tôi"
    "help": None,  # Trợ giúp / Giới thiệu (v0.1.22) — mọi vai trò
    "social": ("system.manage",),  # v0.1.39 (F-32): Tài khoản mạng xã hội — chỉ Owner
    "boss_checks": ("system.manage",),  # v0.1.39 (F-74): Việc Sếp cần làm (/guide/viec-sep) — chỉ Owner
}


@dataclass(frozen=True)
class Target:
    id: str
    screen: str
    label: str
    description: str
    dynamic: str | None
    params: dict[str, str] | None
    sensitive: bool = False
    safe_message: str = ""
    permission: str | None = None


@dataclass(frozen=True)
class Registry:
    screens: dict[str, dict[str, str]]
    targets: dict[str, Target]
    guide: list[dict[str, Any]]


@lru_cache(maxsize=1)
def load() -> Registry:
    raw = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    targets = {t["id"]: Target(id=t["id"], screen=t["screen"], label=t["label"], description=t["description"],
                               dynamic=t.get("dynamic"), params=t.get("params"),
                               sensitive=bool(t.get("sensitive")), safe_message=t.get("safe_message") or "",
                               permission=t.get("permission"))
               for t in raw["targets"]}
    return Registry(screens=raw["screens"], targets=targets, guide=raw["guide"])


def screen_exists(screen: str) -> bool:
    return screen in load().screens


def can_see(permissions: dict[str, str], screen: str) -> bool:
    if screen in EXTRA_SCREEN_PERMISSION:
        need = EXTRA_SCREEN_PERMISSION[screen]
        return need is None or any(permissions.get(p, rbac.NONE) != rbac.NONE for p in need)
    return rbac.can_see_screen(permissions, screen)


def split_target(target_id: str) -> tuple[str, str | None]:
    base, sep, row = target_id.partition(":")
    return base, (row if sep else None)


def resolve_target(target_id: str) -> Target | None:
    base, row = split_target(target_id)
    t = load().targets.get(base)
    if t is None or (t.dynamic == "row") != (row is not None) or (row is not None and not row):
        return None
    return t


def targets_for(screen: str) -> list[Target]:
    return [t for t in load().targets.values() if t.screen == screen]


def visible_screens(permissions: dict[str, str]) -> list[dict[str, str]]:
    return [{"key": k, "title": v["title"], "path": v["path"]} for k, v in load().screens.items()
            if can_see(permissions, k)]
