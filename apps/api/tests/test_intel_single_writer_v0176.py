# ruff: noqa: E501
"""v0.1.76 (gói T1) — rào tĩnh: chỉ `gh.biz.intel.signals` ghi `biz.alerts` (INSERT, và UPDATE cột level/lane/status) (r4 §4.3); chỉ
`levels.py` quyết mức (không module nào khác gọi tên `levels.compute` để ghi); cụm `intel` được đăng ký; job đồng hồ có mặt;
`strong_confirm` stub fail-closed."""

from __future__ import annotations

import re
from pathlib import Path

GH = Path(__file__).resolve().parents[1] / "gh"
WRITER = GH / "biz" / "intel" / "signals.py"
UPDATE_RX = re.compile(r"UPDATE\s+biz\.alerts\s+(?:\w+\s+)?SET\b(?P<set>.*?)(?:\bWHERE\b|$)", re.S | re.I)
INSERT_RX = re.compile(r"INSERT\s+INTO\s+biz\.alerts\b", re.I)


def _py_files() -> list[Path]:
    return [p for p in GH.rglob("*.py") if "__pycache__" not in p.parts]


def test_intel_single_writer_only_signals_inserts_or_changes_level_lane_status() -> None:
    bad: list[str] = []
    for p in _py_files():
        if p == WRITER:
            continue
        src = p.read_text(encoding="utf-8")
        if INSERT_RX.search(src):
            bad.append(f"{p.relative_to(GH)}: INSERT INTO biz.alerts")
        for m in UPDATE_RX.finditer(src):
            if re.search(r"\b(level|lane|status)\s*=", m.group("set"), re.I):
                bad.append(f"{p.relative_to(GH)}: UPDATE biz.alerts SET level/lane/status")
    assert not bad, bad


def test_intel_single_writer_compute_called_only_in_intel() -> None:
    callers = [p.relative_to(GH) for p in _py_files() if "levels.compute(" in p.read_text(encoding="utf-8")]
    assert all(str(c).startswith("biz/intel/") for c in callers), callers


def test_intel_cluster_registered_and_clock_job_present() -> None:
    from gh import biz
    from gh.biz.intel import jobs

    assert "intel" in biz.CLUSTERS
    names = [fn.__name__ for fn, _ in jobs.JOBS]
    assert "intel_reeval" in names and "intel_project_disputes" in names
    assert any(r.path == "/signals" for r in biz.routers()[biz.CLUSTERS.index("intel")].routes)  # type: ignore[attr-defined]


async def test_intel_strong_confirm_stub_fails_closed() -> None:
    import uuid

    from gh.biz.intel import strong_confirm

    for action in (*strong_confirm.ACTIONS, "hanh_dong_la"):
        out = await strong_confirm.require(None, uuid.uuid4(), action)
        assert isinstance(out, strong_confirm.NeedConsole)
