"""v0.1.54: việc cần làm (`todo:*`) của Gen hướng dẫn KHÔNG nhận `understood`/`done`.

Hai hành động đó ghi trạng thái giải quyết vĩnh viễn cho việc không phải P0 mà không qua kiểm mức P2 + xác nhận như
`dismiss`, và `_dismissed_rows` không liệt kê nên Sếp không "Bật lại" được. Kiểm trực tiếp hàm route (không cần CSDL):
lỗi được ném TRƯỚC mọi truy cập CSDL."""

from typing import Any, cast

import pytest

from gh.errors import ApiError
from gh.gen.coach import routes
from gh.gen.coach import signals as sg


def _call(item_key: str, **body: Any) -> Any:
    # request/user/db không được dùng khi bị chặn ở bước kiểm hành động ⇒ truyền None (nếu bị dùng sẽ lỗi to).
    none: Any = cast(Any, None)
    return routes.item_action(item_key, routes.CoachItemAction(**body), none, none, none)


@pytest.mark.parametrize("action", ["understood", "done"])
async def test_todo_rejects_understood_and_done(action: str) -> None:
    key = sorted(sg.static_todo_keys())[0]
    with pytest.raises(ApiError) as e:
        await _call(f"todo:{key}", action=action)
    assert e.value.status == 422 and e.value.code == "COACH_ACTION_NOT_ALLOWED"


async def test_health_todo_also_rejects_understood() -> None:
    kind = sorted(sg.health_kinds())[0]
    with pytest.raises(ApiError) as e:
        await _call(f"todo:health.{kind}", action="understood")
    assert e.value.code == "COACH_ACTION_NOT_ALLOWED"
