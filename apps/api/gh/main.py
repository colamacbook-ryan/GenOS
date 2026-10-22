"""Điểm vào: `gh-api` (uvicorn)."""

import uvicorn

from gh.hardening import set_not_dumpable

set_not_dumpable()   # gói G: sớm nhất có thể — agy cùng uid không đọc được /proc/1/environ của tiến trình api

from gh.app import configure_logging, create_app  # noqa: E402

configure_logging()
app = create_app()


def run() -> None:
    # Review v0.1.63 (P3): KHÔNG để uvicorn tin X-Forwarded-For của mọi kết nối (trước: forwarded_allow_ips="*") —
    # `request.client` giữ IP kết nối thật; chỉ `gh.auth.deps.client_ip` đọc header khi kết nối đến từ proxy tin cậy.
    uvicorn.run("gh.main:app", host="0.0.0.0", port=8000, proxy_headers=False, log_config=None)  # noqa: S104
