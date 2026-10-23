# ruff: noqa: E501
"""Việc nền `mem_consolidate` (L4, spec §4A.3): job đêm hợp nhất, 02:10 giờ VN (cửa sổ 02:00–05:00). Ngoài cửa sổ (chạy bù muộn) thì bỏ qua,
trừ khi `ctx['force']`. Mỗi tổ chức một giao dịch riêng: tổ chức lỗi không kéo các tổ chức khác."""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import text

from gh.biz.hooks import CronJob, Hook
from gh.biz.mem import consolidate

HOOKS: list[Hook] = []
log = logging.getLogger("gh.mem.consolidate")


async def mem_consolidate(ctx: dict[str, Any]) -> dict[str, Any]:
    from gh.db import sessionmaker

    if not ctx.get("force") and not consolidate.in_window():
        return {"skipped": "ngoài cửa sổ 02:00–05:00 giờ VN"}
    sm = sessionmaker()
    out: dict[str, Any] = {}
    async with sm() as db:
        orgs = (await db.execute(text("SELECT id FROM core.organizations"))).scalars().all()
    for org in orgs:
        try:
            async with sm() as db:
                out[str(org)] = await consolidate.run_org(db, org)
                await db.commit()
        except Exception:                                       # noqa: BLE001 — một tổ chức lỗi không dừng cả đêm
            log.exception("mem_consolidate lỗi ở một tổ chức")
            out[str(org)] = {"error": 1}
    return out


JOBS: list[CronJob] = [(mem_consolidate, {"hour": {2}, "minute": {10}})]
