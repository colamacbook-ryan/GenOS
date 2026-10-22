"""Hộp riêng Antigravity GIẢ cho test (v0.1.63 H2) — dùng chung bởi test_agy_placement_v0163 / test_agy_phan_lop /
test_agy_mo_thang_hop_dong. Không phải test (tên không bắt đầu bằng `test_`).

Fixture `box`: hộp giả gắn vào `app.state.model_router` (mặc định `ok`), điều khiển được trạng thái
(`ok` | `down` | `not_installed`), ghi lượt gọi, và ủy quyền cho agy giả mà bài test đã cài (đầu ra thật).
"""

from collections.abc import Callable
from typing import Any

import pytest

from gh.agybox import client as box_client
from gh.providers.clients import AgyClient, Completion, Message


class FakeBoxClient:
    """Thay `AgyBoxClient`: ghi lượt gọi; có `delegate` (AgyClient trỏ CLI giả) thì trả đầu ra thật."""

    kind = "antigravity_cli"

    def __init__(self, ctl: "BoxCtl", delegate: AgyClient | None = None):
        self.ctl, self.delegate, self.timeout = ctl, delegate, 300.0

    async def generate(self, model: str, messages: list[Message], *, json_mode: bool, temperature: float,
                       effort: str | None = None) -> Completion:
        self.ctl.calls.append({"model": model, "effort": effort, "prompt": messages[-1].content})
        if self.ctl.fail is not None:
            raise self.ctl.fail
        if self.delegate is not None:
            return await self.delegate.generate(model, messages, json_mode=json_mode, temperature=temperature,
                                                **({"effort": effort} if effort else {}))
        return Completion("trả lời từ hộp", 1, 1, {})


class BoxCtl:
    """Điều khiển hộp giả: `state`, `calls` (lượt tới hộp), `fail` (ngoại lệ ném ở mỗi lượt), `delegate_factory`."""

    def __init__(self) -> None:
        self.state = "ok"
        self.calls: list[dict[str, Any]] = []
        self.fail: BaseException | None = None
        self.delegate_factory: Callable[[], AgyClient] | None = None

    def factory(self) -> FakeBoxClient | None:
        if self.state == "not_installed":
            return None            # như AgyBoxClient.from_env() khi chưa cấu hình
        return FakeBoxClient(self, self.delegate_factory() if self.delegate_factory else None)

    async def status(self, *a: Any, **kw: Any) -> dict[str, Any]:
        return {"state": self.state, "checked_at": "2026-10-17T00:00:00Z", "agy_version": None, "cli_logged_in": None}


@pytest.fixture
def boxctl(monkeypatch):  # type: ignore[no-untyped-def]
    """Chỉ điều khiển TRẠNG THÁI hộp (`box_status`) — cho test tự dựng `ModelRouter` với `box_factory=ctl.factory`."""
    ctl = BoxCtl()
    monkeypatch.setattr(box_client, "box_status", ctl.status)
    return ctl


@pytest.fixture
def box(app, monkeypatch):  # type: ignore[no-untyped-def]
    """Hộp giả gắn vào `app.state.model_router` (mặc định `ok`). Ủy quyền cho agy mà bài test đã cài vào
    `router.cli_factory` (fixture `clis` / `needs_effort`… — đọc lúc gọi, nên thứ tự fixture không quan trọng)."""
    ctl = BoxCtl()
    router = app.state.model_router
    ctl.delegate_factory = lambda: router.cli_factory()
    monkeypatch.setattr(box_client, "box_status", ctl.status)
    old = router.box_factory
    router.box_factory = ctl.factory
    yield ctl
    router.box_factory = old
