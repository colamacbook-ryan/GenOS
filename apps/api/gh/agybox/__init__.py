"""Hộp riêng Antigravity (`agy-box`, v0.1.63 H1): container chạy `agy` không có khoá master, DB hay Redis chính.

- `protocol`: hợp đồng HTTP + ký HMAC giữa api/worker và hộp (docs/api/agybox-protocol.md).
- `server`: dịch vụ trong container `agy-box` (`python -m gh.agybox.server`) — CHỈ import stdlib, starlette, uvicorn và
  `gh.providers.clients` (không `gh.config`, không DB, không Redis).
- `client`: `AgyBoxClient` (cùng giao diện `AgyClient`) và `box_status()` cho api/worker.
"""
