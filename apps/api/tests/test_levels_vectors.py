# ruff: noqa: E501
"""Rào hồi quy v0.1.77: bộ vector hợp đồng `levels.compute` r7.2 (348) chạy trên code hiện hành. Thuần, không DB.

Chính sách bối cảnh dựng từ seed 0043 (cùng `intel_helpers`). Hai tập khoá bằng băm SHA-256 trên JSON chuẩn hoá (sort_keys, tách theo id):
  1. 125 vector cổng 1/2 gốc (`gate_under_test` ∈ {lua_dao_dang_dien_ra, an_toan}, không có `locked_since`) — `LOCKED_SHA256`;
  2. 22 vector khoá mới có `locked_since` (r7 / r7.1 / r7.2: LV-R7-49…62 và 89…96, Bước 0, kỳ vọng = v0.1.76) — `LOCKED2_SHA256`.
Sửa nhãn hoặc input của vector đã khoá ⇒ `test_fail_high_vectors_locked` đỏ. Chỉ được THÊM vector khoá mới: đặt `locked_since`, KHÔNG đụng
hai băm cũ, và thêm một băm thứ ba cho tập mới (đừng đổi `LOCKED2_SHA256`: băm đó chính là cái khoá 22 vector này).
124 vector còn lại của G0 chỉ được gán thêm trường r7; vector đổi nhãn theo luật r7 ghi `changed_in: "r7"`. Kỳ vọng cuối do oracle độc lập tính.
`expected` hiểu: `level/gate/candidate/delivery`, `would_push`, `capped`, `reasons_include`, tuỳ chọn `next_change` (ISO) kiểm `next_change_at`.
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
LOCKED2_COUNT = 22
LOCKED2_SHA256 = "a2d1e3e49b277d51a0912a979124f41ad95c7928c59e71d3e66f3b3de6535177"


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
    assert len(VECTORS) == 348
    assert len({v["id"] for v in VECTORS}) == 348


@pytest.mark.parametrize("vec", VECTORS, ids=[v["id"] for v in VECTORS])
def test_levels_vector(vec: dict[str, Any]) -> None:
    i, e = vec["input"], vec["expected"]
    p = i["profile"]
    sig, st, now = _signal(i["signal"], p), _state(i["state"], p), datetime.fromisoformat(i["now"])
    out = L.compute(sig, _profile(p), _policy(i["ctx"]), st, now)
    got = (out.level, out.gate, out.candidate, out.delivery)
    assert got == (e["level"], e["gate"], e["candidate"], e["delivery"]), vec["desc"]
    assert out.would_push == e.get("would_push", False)
    assert out.capped == e.get("capped", False)
    missing = [r for r in e.get("reasons_include", []) if r not in out.reasons]
    assert not missing, f"thiếu reasons {missing}; có {out.reasons}"
    if "next_change" in e:
        nxt = L.next_change_at(sig, st, now)
        assert nxt is not None and nxt == datetime.fromisoformat(e["next_change"])


def test_fail_high_vectors_locked() -> None:
    locked = sorted((v for v in VECTORS if v["gate_under_test"] in LOCKED_GATES and "locked_since" not in v),
                    key=lambda v: v["id"])
    assert len(locked) == LOCKED_COUNT
    blob = json.dumps(locked, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    assert hashlib.sha256(blob.encode("utf-8")).hexdigest() == LOCKED_SHA256


def test_locked_new_vectors_hash() -> None:
    locked = sorted((v for v in VECTORS if "locked_since" in v), key=lambda v: v["id"])
    assert len(locked) == LOCKED2_COUNT
    assert {v["locked_since"] for v in locked} == {"r7", "r7.1", "r7.2"}
    blob = json.dumps(locked, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    assert hashlib.sha256(blob.encode("utf-8")).hexdigest() == LOCKED2_SHA256
