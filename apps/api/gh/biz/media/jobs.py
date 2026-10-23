"""Khung rỗng (L5 sở hữu): chưa có hook/việc nền."""

from gh.biz.hooks import CronJob, Hook

HOOKS: list[Hook] = []
JOBS: list[CronJob] = []
