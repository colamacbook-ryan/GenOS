# ruff: noqa: E501
"""Voice → chữ. Giao diện `transcribe(engine, …)`: engine nào cũng được (bộ định tuyến, faster-whisper, giả)."""

from __future__ import annotations

import asyncio
import re
import unicodedata

from gh.biz.media.providers import EngineError, EngineText, SttEngine

MAX_TEXT_CHARS = 20_000
ENGINE_TIMEOUT_S = 240.0
_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def clean_text(text: str | None) -> str:
    """NFC, bỏ ký tự điều khiển (kể cả NUL — Postgres không nhận), cắt trần. Chữ này là DỮ LIỆU KHÔNG TIN CẬY: nơi dùng phải
    bọc `wrap_untrusted`; ở đây chỉ làm sạch, không diễn giải."""
    t = _CTRL.sub("", unicodedata.normalize("NFC", text or "")).strip()
    return t[:MAX_TEXT_CHARS]


async def transcribe(engine: SttEngine, data: bytes, mime: str, *, lang_hint: str | None = "vi",
                     timeout_s: float = ENGINE_TIMEOUT_S) -> EngineText:
    try:
        res = await asyncio.wait_for(engine.transcribe(data, mime, lang_hint=lang_hint), timeout=timeout_s)
    except (TimeoutError, asyncio.TimeoutError):  # noqa: UP041
        raise EngineError("engine_timeout") from None
    return EngineText(text=clean_text(res.text), lang=res.lang, confidence=res.confidence, engine=res.engine,
                      engine_version=res.engine_version)
