# ruff: noqa: E501
"""Điểm cắm sang L2: `prefilter.scan_text(text, source_field) -> [Candidate]` (spec §4.2/§4E, CNS-089a).

L2 sở hữu hàm thật (`gh/refinery/prefilter.py`); L5 chỉ GỌI nó trên chữ OCR/transcript. Khi L2 chưa merge, `scan_text` ở đây
là STUB trả danh sách rỗng (đúng chữ ký) — `is_stub()` cho biết, và báo cáo gói ghi rõ. Ngay khi `gh.refinery.prefilter` có
`scan_text`, điểm cắm tự dùng hàm thật (resolve MỖI LẦN gọi), không phải sửa gì ở L5.
"""

from __future__ import annotations

import importlib
from collections.abc import Callable
from typing import Any

#: Tên trường nguồn của chữ lấy từ media (khớp `{source_field, span_start, span_end}` của bằng chứng `raw_event`, §4.2).
SOURCE_FIELD = "media_texts.text"


def _real() -> Callable[[str, str], Any] | None:
    try:
        fn = getattr(importlib.import_module("gh.refinery.prefilter"), "scan_text", None)
    except ImportError:
        return None
    return fn if callable(fn) else None


def is_stub() -> bool:
    return _real() is None


def scan_text(text: str, source_field: str = SOURCE_FIELD) -> list[Any]:
    fn = _real()
    if fn is None or not text:
        return []
    return list(fn(text, source_field))
