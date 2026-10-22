"""Việc nền của cụm nền chung (bản nháp, góc nhìn, chứng cứ): hook sau sàng lọc và việc định kỳ cho worker.

- `HOOKS`: hàm `async (ctx: HookCtx) -> None` chạy mỗi khi sàng lọc ghi xong đơn vị ý nghĩa (`gh.clean.ready`).
- `JOBS`: `(hàm arq async (ctx) -> Any, dict tham số arq.cron)` — worker tự đăng ký.
"""

from gh.biz.hooks import CronJob, Hook

HOOKS: list[Hook] = []
JOBS: list[CronJob] = []
