# ruff: noqa: E501
"""Việc nền `mem_heat_weekly` (L4): dựng lại độ nóng quan hệ 12 tuần gần nhất, mỗi đêm 01:40 giờ VN (trước job hợp nhất 02:10)."""

from __future__ import annotations

from typing import Any

from sqlalchemy import text

from gh.biz.hooks import CronJob, Hook
from gh.biz.mem import heat

HOOKS: list[Hook] = []


async def mem_heat_weekly(ctx: dict[str, Any]) -> dict[str, dict[str, int]]:
    from gh.db import sessionmaker

    sm = sessionmaker()
    out: dict[str, dict[str, int]] = {}
    async with sm() as db:
        orgs = (await db.execute(text("SELECT id FROM core.organizations"))).scalars().all()
    for org in orgs:
        async with sm() as db:
            out[str(org)] = await heat.rebuild(db, org)
            await db.commit()
    return out


JOBS: list[CronJob] = [(mem_heat_weekly, {"hour": {1}, "minute": {40}})]
