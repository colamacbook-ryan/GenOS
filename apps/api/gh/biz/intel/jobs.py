# ruff: noqa: E501
"""Việc nền cụm `intel`: TỰ GOM `HOOKS`/`JOBS` của mọi module con trong `detectors/` và `pipes/` (gói T3–T10 chỉ thêm tệp), cộng việc của T1:

- `intel_reeval` (mỗi phút): `signals.reevaluate_due` — tính lại THEO ĐỒNG HỒ các tín hiệu tới mốc `next_eval_at` (hạn tới gần ⇒ leo cổng,
  quá cửa sổ ⇒ 🟠 đứng đầu bản tin), không cần tin mới; `raw_tail.tail` (đường nhanh luật cứng); hết hạn ask.
- `intel_project_disputes` (mỗi 10 phút): chiếu `mem.disputes` sang tín hiệu.
Đồng hồ: `gh.refinery.clock.now()` (đồng hồ giả chỉ ở `GH_ENV=eval`, để G-KB replay chạy đúng thời gian).
"""

from __future__ import annotations

import importlib
import logging
import pkgutil
from typing import Any

from sqlalchemy import text

from gh.biz.hooks import CronJob, Hook

log = logging.getLogger("gh.intel.jobs")


def _submodules(pkg_name: str) -> list[Any]:
    pkg = importlib.import_module(pkg_name)
    return [importlib.import_module(f"{pkg_name}.{m.name}") for m in sorted(pkgutil.iter_modules(pkg.__path__), key=lambda m: m.name)]


SUBMODULES = _submodules("gh.biz.intel.detectors") + _submodules("gh.biz.intel.pipes")


async def intel_reeval(ctx: dict[str, Any]) -> dict[str, int]:
    from gh.biz.intel import asks, raw_tail, signals
    from gh.db import sessionmaker
    from gh.refinery import clock

    now = clock.now()
    sm = sessionmaker()
    out = {"changed": 0, "raw_tail": 0, "asks_expired": 0, "errors": 0}
    async with sm() as db:
        out["changed"] = len(await signals.reevaluate_due(db, now))
        out["asks_expired"] = await asks.expire(db)
        await db.commit()
        orgs = (await db.execute(text("SELECT id FROM core.organizations"))).scalars().all()
    for org in orgs:
        try:
            async with sm() as db:
                out["raw_tail"] += len(await raw_tail.tail(db, org, now))
                await db.commit()
        except Exception:                                       # noqa: BLE001 — một tổ chức lỗi không dừng tổ chức khác
            log.exception("intel_reeval: raw_tail lỗi ở một tổ chức")
            out["errors"] += 1
    return out


async def intel_project_disputes(ctx: dict[str, Any]) -> dict[str, int]:
    from gh.biz.intel import signals
    from gh.db import sessionmaker
    from gh.refinery import clock

    sm = sessionmaker()
    n = 0
    async with sm() as db:
        orgs = (await db.execute(text("SELECT id FROM core.organizations"))).scalars().all()
    for org in orgs:
        try:
            async with sm() as db:
                n += len(await signals.project_disputes(db, org, clock.now()))
                await db.commit()
        except Exception:                                       # noqa: BLE001
            log.exception("intel_project_disputes lỗi ở một tổ chức")
    return {"projected": n}


HOOKS: list[Hook] = [h for m in SUBMODULES for h in getattr(m, "HOOKS", [])]
JOBS: list[CronJob] = [(intel_reeval, {"minute": set(range(60))}),
                       (intel_project_disputes, {"minute": set(range(0, 60, 10))}),
                       *[j for m in SUBMODULES for j in getattr(m, "JOBS", [])]]
