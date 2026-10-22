"""Giao diện chung của một adapter nền tảng (đọc + ghi có permit)."""

from typing import Any, Literal

PageState = Literal["ok", "need_login", "checkpoint", "captcha"]

# Trạng thái trang → mã lỗi báo api (api dừng tài khoản + chuông Owner với checkpoint/captcha).
STATE_ERROR = {"need_login": "LOGGED_OUT", "checkpoint": "CHECKPOINT", "captcha": "CAPTCHA"}


class TargetNotFound(Exception):
    """Không thấy ô trả lời / ô soạn trên trang."""


class Adapter:
    key: str = ""
    home: str = ""

    async def page_state(self, page: Any, context: Any) -> PageState:
        raise NotImplementedError

    async def handle(self, page: Any, context: Any) -> str | None:
        return None

    async def read(self, page: Any, what: str, max_items: int) -> list[dict[str, Any]]:
        raise NotImplementedError

    # Loại ghi adapter hỗ trợ (rỗng = chỉ đọc). Chỉ chạy khi có permit hợp lệ (ghb.permit); selector là quy tắc cố định.
    write_kinds: tuple[str, ...] = ()

    async def open_target(self, page: Any, action: str, target_url: str) -> None:
        raise NotImplementedError

    async def compose(self, page: Any, action: str, text: str, target_url: str = "") -> None:
        """Điền nội dung bằng MỘT lần chèn; ném TargetNotFound nếu không thấy ô trả lời / ô soạn / đích đúng
        `target_url` (đã ký trong permit)."""
        raise NotImplementedError

    async def submit(self, page: Any, action: str) -> None:
        raise NotImplementedError

    async def confirm_sent(self, page: Any, action: str, text: str, timeout_ms: int) -> bool:
        raise NotImplementedError
