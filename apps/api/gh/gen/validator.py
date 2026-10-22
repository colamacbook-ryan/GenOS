"""Kiểm server-side mọi hành động UI trước khi gửi xuống web (docs/design/gen-v1.md §3.6).

1. Envelope đã khớp schema (gh.gen.envelope, `extra="forbid"`).
2. `screen` có trong registry VÀ người hỏi được xem màn đó (RBAC).
3. `target` có trong registry VÀ thuộc đúng màn đang bàn; id động (`<base>:<row>`) chỉ hợp lệ khi `<row>` vừa xuất hiện
   trong kết quả tool của CHÍNH lượt này (chống model bịa id).
4. Tham số URL chỉ gồm khoá cho phép; `id` cũng phải là id vừa thấy.
Không đạt → bỏ action (caller ghi Action Log `result="blocked"` và báo model lý do ở vòng kế).
"""

from dataclasses import dataclass

from gh.auth import rbac
from gh.gen import envelope, registry

PARAM_KEYS = {"tab", "id", "q", "filter", "status"}
ID_PARAMS = {"id"}


@dataclass
class Verdict:
    ok: bool
    reason: str | None = None
    # Màn sau khi thực thi action (để kiểm action kế tiếp trong cùng lượt).
    screen: str | None = None


class Validator:
    def __init__(self, permissions: dict[str, str], seen_ids: set[str], current_screen: str | None):
        self.permissions = permissions
        self.seen_ids = seen_ids
        self.screen = current_screen

    def _screen(self, screen: str) -> str | None:
        if not registry.screen_exists(screen):
            return f"màn '{screen}' không tồn tại"
        if not registry.can_see(self.permissions, screen):
            return f"người hỏi không được xem màn '{screen}'"
        return None

    def _target(self, target: str, screen: str | None) -> str | None:
        t = registry.resolve_target(target)
        if t is None:
            return f"mục tiêu '{target}' không có trong registry"
        if screen is None or t.screen != screen:
            return f"mục tiêu '{target}' thuộc màn '{t.screen}', không phải màn đang mở '{screen}'"
        err = self._screen(t.screen)
        if err:
            return err
        if t.permission and self.permissions.get(t.permission, rbac.NONE) == rbac.NONE:
            return f"người hỏi không có quyền '{t.permission}' để chỉ vào '{target}'"
        _, row = registry.split_target(target)
        if row is not None and row not in self.seen_ids:
            return f"id dòng '{row}' không có trong kết quả tool của lượt này"
        return None

    def check(self, action: envelope.Navigate | envelope.Highlight | envelope.Tour, *, commit: bool = True) -> Verdict:
        """`commit=False` (thẻ đề xuất): kiểm nhưng không đổi màn hiện tại của lượt."""
        if isinstance(action, envelope.Navigate):
            err = self._screen(action.screen)
            if err is None and action.params:
                bad = set(action.params) - PARAM_KEYS
                if bad:
                    err = f"tham số không cho phép: {', '.join(sorted(bad))}"
                for k in ID_PARAMS & set(action.params):
                    if action.params[k] not in self.seen_ids:
                        err = f"id '{action.params[k]}' không có trong kết quả tool của lượt này"
            if err:
                return Verdict(False, err)
            if commit:
                self.screen = action.screen
            return Verdict(True, screen=action.screen)
        if isinstance(action, envelope.Highlight):
            err = self._target(action.target, self.screen)
            return Verdict(err is None, err, self.screen)
        screen = self.screen
        for i, step in enumerate(action.steps, start=1):
            if step.screen is not None:
                err = self._screen(step.screen)
                if err:
                    return Verdict(False, f"bước {i}: {err}")
                screen = step.screen
            err = self._target(step.target, screen)
            if err:
                return Verdict(False, f"bước {i}: {err}")
        if commit:
            self.screen = screen
        return Verdict(True, screen=screen)
