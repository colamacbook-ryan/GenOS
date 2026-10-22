"""Giai đoạn 3 — các màn kinh doanh, mỗi cụm một gói con (core, queue, relations, graph, market, people, duty).

Mỗi gói con có `routes.router` (gắn vào /api/v1) và `jobs.HOOKS` / `jobs.JOBS` (worker tự đăng ký), nên thêm cụm
không phải sửa app.py hay worker.py.
"""

import importlib

from fastapi import APIRouter

from gh.biz.hooks import CronJob, Hook

CLUSTERS = ("core", "queue", "relations", "graph", "market", "people", "duty")


def routers() -> list[APIRouter]:
    return [importlib.import_module(f"gh.biz.{c}.routes").router for c in CLUSTERS]


def hooks() -> list[Hook]:
    out: list[Hook] = []
    for c in CLUSTERS:
        out.extend(importlib.import_module(f"gh.biz.{c}.jobs").HOOKS)
    return out


def jobs() -> list[CronJob]:
    out: list[CronJob] = []
    for c in CLUSTERS:
        out.extend(importlib.import_module(f"gh.biz.{c}.jobs").JOBS)
    return out
