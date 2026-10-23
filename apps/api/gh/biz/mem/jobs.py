"""Việc nền của cụm `mem`: TỰ GOM `HOOKS`/`JOBS` từ mọi module `gh/biz/mem/job_*.py` (L3: `job_evidence_verify`, L4:
`job_consolidate`, `job_heat`…) — gói đó chỉ thêm một tệp, không sửa `jobs.py`/`gh.biz.__init__`/worker."""

from __future__ import annotations

import importlib
import pkgutil
from collections.abc import Iterable
from types import ModuleType
from typing import Any

from gh.biz.hooks import CronJob, Hook


def job_modules(package: ModuleType | None = None) -> list[ModuleType]:
    """Các module con tên `job_*` của gói (mặc định `gh.biz.mem`), theo thứ tự tên — xác định."""
    pkg = package or importlib.import_module("gh.biz.mem")
    names = sorted(m.name for m in pkgutil.iter_modules(pkg.__path__) if m.name.startswith("job_"))
    return [importlib.import_module(f"{pkg.__name__}.{n}") for n in names]


def collect(modules: Iterable[ModuleType], attr: str) -> list[Any]:
    out: list[Any] = []
    for m in modules:
        out.extend(getattr(m, attr, []))
    return out


_MODS = job_modules()
HOOKS: list[Hook] = collect(_MODS, "HOOKS")
JOBS: list[CronJob] = collect(_MODS, "JOBS")
