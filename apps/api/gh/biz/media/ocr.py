# ruff: noqa: E501
"""Ảnh → chữ OCR + mô tả ngắn (model đa phương thức qua bộ định tuyến; Q9 cho phép gửi đầy đủ, không che)."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from gh.biz.media.providers import EngineError, EngineText, VisionEngine
from gh.biz.media.transcribe import ENGINE_TIMEOUT_S, clean_text


@dataclass
class ImageReading:
    ocr: EngineText | None = None
    description: EngineText | None = None
    errors: dict[str, str] = field(default_factory=dict)   # {"ocr": mã, "description": mã}


def _clean(t: EngineText) -> EngineText:
    return EngineText(text=clean_text(t.text), lang=t.lang, confidence=t.confidence, engine=t.engine,
                      engine_version=t.engine_version)


async def _guard(coro, timeout_s: float) -> EngineText:  # type: ignore[no-untyped-def]
    try:
        return _clean(await asyncio.wait_for(coro, timeout=timeout_s))
    except (TimeoutError, asyncio.TimeoutError):  # noqa: UP041
        raise EngineError("engine_timeout") from None


async def read_image(engine: VisionEngine, data: bytes, mime: str, *, timeout_s: float = ENGINE_TIMEOUT_S) -> ImageReading:
    """OCR và mô tả chạy song song, lỗi RIÊNG từng phần (mô tả hỏng không làm mất chữ OCR). Cả hai hỏng ⇒ ném lỗi đầu."""
    out = ImageReading()
    results = await asyncio.gather(_guard(engine.ocr(data, mime), timeout_s), _guard(engine.describe(data, mime), timeout_s),
                                   return_exceptions=True)
    for name, r in zip(("ocr", "description"), results, strict=True):
        if isinstance(r, EngineError):
            out.errors[name] = r.code
        elif isinstance(r, BaseException):
            if isinstance(r, asyncio.CancelledError):
                raise r
            out.errors[name] = "engine_error"
        elif name == "ocr":
            out.ocr = r
        else:
            out.description = r
    if out.ocr is None and out.description is None:
        raise EngineError(out.errors.get("ocr") or "engine_error")
    return out
