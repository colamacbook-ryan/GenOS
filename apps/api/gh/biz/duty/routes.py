"""API agent trực kênh (docs/api/phase-3-duty.md).

Danh sách quyết định (`GET /agents/decisions`) thuộc nền chung (`gh/biz/core/routes.py`). Cụm này chỉ khai báo
sự kiện WebSocket `agent.decision` do worker phát sau mỗi quyết định.
"""

from fastapi import APIRouter

from gh import realtime

router = APIRouter(tags=["duty"])

realtime.register_event("agent.decision", "system.read")
