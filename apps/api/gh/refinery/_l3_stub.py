# ruff: noqa: E501
"""ĐIỂM CẮM L3 — duy nhất (spec §11 L2). L3 đã merge (PR #4): file này giờ chỉ RE-EXPORT bản thật từ `extract`/`verify`.

`gh/refinery/runner.py` có đúng một dòng import `from gh.refinery import _l3_stub as l3  # L3-PLUG`; giữ tên file để runner không đổi.
`IS_STUB = False` ⇒ đường theo luồng bật mặc định (xem `Refinery.group_mode`). Chữ ký thật khớp bản stub cũ: `analyze_group_detailed(window,
pack, profile, policy, *, call, secrets)`, `check(out, events, media_texts, *, now, tz, thread_ref, profile_fact_ids, known_person_refs,
known_predicates, secrets)`.
"""

from gh.refinery.extract import (
    DEFAULT_TZ,
    EXTRACTOR_VERSION,
    Analysis,
    ContractError,
    GroupEvent,
    GroupWindow,
    ModelCall,
    ModelReply,
    analyze_group_detailed,
)
from gh.refinery.verify import MediaText, Verified, VerifiedFact, VerifiedQuote, VerifiedUnit, check

IS_STUB = False

__all__ = ["DEFAULT_TZ", "EXTRACTOR_VERSION", "IS_STUB", "Analysis", "ContractError", "GroupEvent", "GroupWindow", "MediaText",
           "ModelCall", "ModelReply", "Verified", "VerifiedFact", "VerifiedQuote", "VerifiedUnit", "analyze_group_detailed", "check"]
