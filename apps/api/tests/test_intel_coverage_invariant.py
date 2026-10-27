# ruff: noqa: E501
"""Bất biến phủ v0.1.77 (bản 3 §5, dòng P6): không loại tín hiệu nào "rơi" khỏi đường chiếu hoặc khỏi bộ vector.

1. Mọi `kind_hint` hợp lệ (hợp đồng `brain-out`) có chủ chiếu thật (detector) hoặc dòng skip có lý do.
2. Mọi loại 🔴 `t` (cổng 3–6) có ≥ 1 vector P1 (🔴 đúng) và ≥ 1 vector "suýt P1" (cùng cổng, không 🔴) trong `levels_vectors.json`.
3. Khoá `entities.v` mà P2/P3b đọc đều do `runner.verified_fields` ghi (đổi tên một bên ⇒ đỏ).
4. Giờ làm chỉ có MỘT hàm đọc (`signals.work_hours_of`); `questions` dùng chung.
5. Mọi chỗ đọc `biz.alerts.level_input` thành `SignalIn` đều qua `signals.load_level_input` (khoá phụ `stage_eff`).
"""

from __future__ import annotations

import ast
import importlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import orjson
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gh.biz.intel import levels, signals
from gh.biz.intel.detectors import generic, promises, questions
from gh.biz.intel.detectors import units as U
from gh.refinery import brain_contract
from gh.refinery.runner import verified_fields
from tests.mem_helpers import mk_org

API = Path(__file__).resolve().parents[1]
VECTORS: list[dict[str, Any]] = json.loads((Path(__file__).parent / "fixtures" / "levels_vectors.json").read_text(encoding="utf-8"))["vectors"]

#: `kind_hint` không có đường chiếu thường — lý do bắt buộc. Hiện rỗng: cả 10 loại đều có chủ chiếu.
SKIP_KINDS: dict[str, str] = {}
#: Đường chiếu ngoài `OWNERS` cho `kind` do hàm khác chiếu: kind → (module chủ, tên hằng liệt kê kind module đó quét).
KIND_SCAN_CONST = {"generic.project_units": ("generic", "UNIT_KINDS"), "promises.project_units": ("promises", "PROMISE_KINDS"), "units": ("units", "UNIT_KINDS")}
RED_GATES = (levels.G3, levels.G4, levels.G5, levels.G6)


# ─── 1. mọi kind_hint có đường chiếu ─────────────────────────────────────────

@pytest.mark.parametrize("kind", brain_contract.KINDS)
def test_every_kind_hint_has_projector_or_reasoned_skip(kind: str) -> None:
    if kind in SKIP_KINDS:
        assert SKIP_KINDS[kind].strip(), f"{kind}: dòng skip phải có lý do"
        return
    owner = U.OWNERS.get(kind)
    assert owner, f"kind_hint {kind} không có chủ chiếu trong units.OWNERS và không có dòng skip"
    if owner in KIND_SCAN_CONST:
        mod, const = KIND_SCAN_CONST[owner]
        scanned = getattr(importlib.import_module(f"gh.biz.intel.detectors.{mod}"), const)
        assert kind in scanned, f"{owner} khai chiếu {kind} nhưng không quét loại đó ({mod}.{const})"
    else:
        mod, fn = owner.split(".")
        assert callable(getattr(importlib.import_module(f"gh.biz.intel.detectors.{mod}"), fn)), f"{kind}: {owner} không phải hàm có thật"


def test_skip_kinds_are_valid_and_reasoned() -> None:
    for kind, why in SKIP_KINDS.items():
        assert kind in brain_contract.KINDS and why.strip()
    assert set(brain_contract.KINDS) == set(levels.KINDS), "hợp đồng brain và levels lệch tập loại"
    assert set(promises.PROMISE_KINDS) | set(generic.UNIT_KINDS) | set(U.UNIT_KINDS) | {"cau_hoi_cho_xu_ly"} == set(brain_contract.KINDS)


# ─── 2. mọi loại 🔴 có vector P1 và vector "suýt P1" ─────────────────────────

@pytest.mark.parametrize("gate", RED_GATES)
def test_every_red_type_has_p1_vector_and_near_miss(gate: str) -> None:
    p1 = [v for v in VECTORS if v["id"].startswith("LV-R7") and v.get("gate_under_test") == gate]
    hits = [v["id"] for v in p1 if v["expected"]["level"] == "red" and v["expected"].get("gate") == gate]
    near = [v["id"] for v in p1 if v["expected"]["level"] != "red"]
    assert hits, f"loại 🔴 {gate} chưa có vector P1 (🔴 đúng cổng)"
    assert near, f"loại 🔴 {gate} chưa có vector suýt P1 (cùng cổng, không 🔴)"


# ─── 3. khoá entities.v: bên đọc ⊆ bên ghi ───────────────────────────────────

#: Khoá `v.get(...)` trong promises.py thuộc giá trị của SỰ THẬT (facts.value), không phải `entities.v` ⇒ không đối chiếu.
FACT_VALUE_KEYS = {"due_at", "condition", "text", "to_entity_id"}
ENTITIES_V_READERS = ("gh/biz/intel/detectors/units.py", "gh/biz/intel/detectors/promises.py", "gh/biz/intel/detectors/catalog_match.py")


def _written_keys() -> set[str]:
    vu = SimpleNamespace(msg_at=None, event_at=None, event_time_unparsed=False, deadline_at=None, deadline_unparsed=False, ongoing=False, money_vnd=None,
                         status_by_person_id=None, status_at=None, txn_state="none", txn_at=None, delivery_form=None)
    u = SimpleNamespace(flags=SimpleNamespace(public=False), condition=None)
    return set(verified_fields(vu, u, []))


def _read_keys(rel: str) -> set[str]:
    out: set[str] = set()
    for n in ast.walk(ast.parse((API / rel).read_text(encoding="utf-8"))):
        if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "get" and isinstance(n.func.value, ast.Name)
                and n.func.value.id == "v" and n.args and isinstance(n.args[0], ast.Constant) and isinstance(n.args[0].value, str)):
            out.add(n.args[0].value)
    return out


@pytest.mark.parametrize("rel", ENTITIES_V_READERS)
def test_entities_v_keys_read_are_written_by_runner(rel: str) -> None:
    read = _read_keys(rel) - (FACT_VALUE_KEYS if rel.endswith("promises.py") else set())
    missing = read - _written_keys()
    assert not missing, f"{rel} đọc khoá entities.v mà runner.verified_fields không ghi: {sorted(missing)}"


def test_entities_v_p2_contract_keys_present() -> None:
    """Khoá P2 ghi nhận trong bản nối: complaint_stage, asks_for_owner, public_venue, status_hint, status_by_person_id (+ public_eff cho đường bảng)."""
    assert {"complaint_stage", "asks_for_owner", "public_venue", "status_hint", "status_by_person_id", "public_eff", "contract_version"} <= _written_keys()


def test_entities_v_reader_extractor_finds_keys() -> None:
    """Rào cho chính bộ dò: nếu AST không bắt được khoá nào thì test đối chiếu ở trên vô nghĩa."""
    assert {"complaint_stage", "public_eff"} <= _read_keys("gh/biz/intel/detectors/units.py")


# ─── 4. giờ làm: một hàm ─────────────────────────────────────────────────────

def test_work_hours_single_reader() -> None:
    assert not hasattr(questions, "work_hours_of")
    src = (API / "gh/biz/intel/detectors/questions.py").read_text(encoding="utf-8")
    assert '"work_start"' not in src and '"work_end"' not in src


async def test_org_work_hours_matches_delivery_state(db: AsyncSession) -> None:
    from datetime import datetime

    org = await mk_org(db)
    now = datetime(2026, 10, 26, 10, 0, tzinfo=levels.VN)
    assert await signals.org_work_hours(db, org) == levels.WorkHours(start="08:00", end="17:30")
    await db.execute(text("UPDATE core.organizations SET settings = settings || CAST(:s AS jsonb) WHERE id = :o"),
                     {"s": orjson.dumps({"intel": {"work_start": "07:30", "work_end": "16:00"}}).decode(), "o": org})
    assert await signals.org_work_hours(db, org) == (await signals.delivery_state(db, org, now)).work_hours == levels.WorkHours(start="07:30", end="16:00")


# ─── 5. đọc level_input qua load_level_input ─────────────────────────────────

def test_level_input_readers_use_load_level_input() -> None:
    """`SignalIn.model_validate(<cột level_input>)` trực tiếp sẽ vỡ ở thẻ có khoá phụ `stage_eff` (extra=forbid) ⇒ cấm trong gh/ và tests/."""
    bad: list[str] = []
    for root in (API / "gh", API / "tests"):
        for p in root.rglob("*.py"):
            if ".venv" in p.parts or p.name == Path(__file__).name:
                continue
            for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
                if "SignalIn.model_validate(" in line and ".level_input" in line and "load_level_input" not in line:
                    bad.append(f"{p.relative_to(API)}:{i}")
    assert not bad, f"đọc level_input không qua signals.load_level_input: {bad}"
