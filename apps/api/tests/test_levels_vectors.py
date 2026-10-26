"""Rào hồi quy v0.1.77 (G0): bộ vector hợp đồng `levels.compute` (249) chạy trên code hiện hành. Thuần, không DB.

Chính sách bối cảnh dựng từ seed 0043 (cùng `intel_helpers`). Vector khoá cổng 1/2 (`gate_under_test` ∈
{lua_dao_dang_dien_ra, an_toan}) bị khoá bằng băm: sửa nhãn hoặc input ⇒ `test_fail_high_vectors_locked` đỏ.
Chỉ được THÊM vector khoá mới (đặt `locked_since`, băm tính trên tập id cũ) — không sửa vector đã khoá.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from gh.biz.intel import levels as L
from gh.biz.mem.profile_read import ProfileItem, ProfileSnapshot
from tests.intel_helpers import ORG, policy

FIXTURE = Path(__file__).parent / "fixtures" / "levels_vectors.json"
VECTORS: list[dict[str, Any]] = json.loads(FIXTURE.read_text(encoding="utf-8"))["vectors"]
LOCKED_GATES = {"lua_dao_dang_dien_ra", "an_toan"}  # "cổng 1/2" (an toàn / lừa đảo)
LOCKED_COUNT = 125
LOCKED_SHA256 = "59e8f829ee62510e2c9a67882c14aa25adedbc0ff69dc6713e1dfc4906eb01af"


def _u(s: str) -> uuid.UUID:
    return uuid.uuid5(uuid.NAMESPACE_URL, s)


def _policy(ctx: dict[str, Any]) -> Any:
    if ctx["context_code"] == "unconfirmed" or ctx.get("context_confirmed") is False:
        return policy(None)
    return policy(ctx["context_code"])


def _profile(p: dict[str, Any]) -> ProfileSnapshot:
    items = tuple(ProfileItem(_u("f" + e), "key_relations", "owner_key_relation", e, _u(e), datetime(2026, 1, 1), 1)
                  for e in p["owner_key_relation_entity_ids"])
    return ProfileSnapshot(ORG, 1, _u("owner"), items)


def _signal(s: dict[str, Any], p: dict[str, Any]) -> L.SignalIn:
    s = {k: v for k, v in s.items() if k in L.SignalIn.model_fields}
    s["subject_entity_id"] = _u(p["target_entity_id"]) if p.get("target_entity_id") else None
    s["goal_priority1_match_valid"] = p["goal_priority1_match_valid"]
    if s.get("harm_what") is None:
        s["harm_what"] = ""
    return L.SignalIn.model_validate(s)


def _state(st: dict[str, Any], p: dict[str, Any]) -> L.DeliveryState:
    st = dict(st)
    st["money_small_vnd"] = p["money_small_vnd"]
    st["money_big_vnd"] = p["money_big_vnd"]
    return L.DeliveryState.model_validate(st)


def test_vector_count() -> None:
    assert len(VECTORS) == 249
    assert len({v["id"] for v in VECTORS}) == 249


@pytest.mark.parametrize("vec", VECTORS, ids=[v["id"] for v in VECTORS])
def test_levels_vector(vec: dict[str, Any]) -> None:
    i, e = vec["input"], vec["expected"]
    p = i["profile"]
    out = L.compute(_signal(i["signal"], p), _profile(p), _policy(i["ctx"]), _state(i["state"], p),
                    datetime.fromisoformat(i["now"]))
    got = (out.level, out.gate, out.candidate, out.delivery)
    assert got == (e["level"], e["gate"], e["candidate"], e["delivery"]), vec["desc"]
    assert out.would_push == e.get("would_push", False)
    assert out.capped == e.get("capped", False)
    missing = [r for r in e.get("reasons_include", []) if r not in out.reasons]
    assert not missing, f"thiếu reasons {missing}; có {out.reasons}"


def test_fail_high_vectors_locked() -> None:
    locked = sorted((v for v in VECTORS if v["gate_under_test"] in LOCKED_GATES and "locked_since" not in v),
                    key=lambda v: v["id"])
    assert len(locked) == LOCKED_COUNT
    blob = json.dumps(locked, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    assert hashlib.sha256(blob.encode("utf-8")).hexdigest() == LOCKED_SHA256
