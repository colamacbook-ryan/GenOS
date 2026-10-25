# ruff: noqa: E501
"""v0.1.76 (gói T3, Q9/CNS-081) — `gen/briefing.py` gửi model chữ chat NGUYÊN VĂN (STK/SĐT/tiền giữ nguyên); chỉ bí mật HỆ THỐNG
(`secrets`) và thông tin đăng nhập (OTP, mật khẩu, thẻ) bị thay."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any

from gh.gen import briefing


class Router:
    def __init__(self) -> None:
        self.messages: list[Any] = []

    async def generate(self, org: Any, **kw: Any) -> Any:
        self.messages = kw["messages"]
        return SimpleNamespace(text="Tóm tắt")


SECTIONS = [{"key": "hot_customers", "title": "Khách nóng", "count": 1,
             "lines": ["Lan (SĐT 0901234567) chuyển vào STK 0123456789012 số tiền 25.000.000đ; OTP của Lan là 482913; token tokentoken-SECRET-1"]}]


async def test_summary_prompt_keeps_phone_account_and_money_but_not_credentials() -> None:
    r = Router()
    out = await briefing._summarize(r, uuid.uuid4(), SECTIONS, secrets=("tokentoken-SECRET-1",))  # type: ignore[arg-type]
    assert out == "Tóm tắt"
    prompt = "\n".join(m.content for m in r.messages)
    assert "0901234567" in prompt and "0123456789012" in prompt and "25.000.000đ" in prompt        # Q9: nguyên văn
    assert "482913" not in prompt                                                                    # ngoại lệ Q9: thông tin đăng nhập
    assert "tokentoken-SECRET-1" not in prompt                                                       # bí mật hệ thống


async def test_summary_without_secrets_argument_still_works_for_old_callers() -> None:
    r = Router()
    assert await briefing._summarize(r, uuid.uuid4(), SECTIONS) == "Tóm tắt"  # type: ignore[arg-type]
