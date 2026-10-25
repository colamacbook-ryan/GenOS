# ruff: noqa: E501
"""v0.1.76 (gói T1) — rào tĩnh: chỉ `gh.biz.intel.signals` ghi `biz.alerts` (INSERT, và UPDATE cột level/lane/status) (r4 §4.3); chỉ
`levels.py` quyết mức (không module nào khác gọi tên `levels.compute` để ghi); cụm `intel` được đăng ký; job đồng hồ có mặt;
`strong_confirm` stub fail-closed."""

from __future__ import annotations

import re
from pathlib import Path

GH = Path(__file__).resolve().parents[1] / "gh"
WRITER = GH / "biz" / "intel" / "signals.py"
_T = r"(?:ONLY\s+)?\"?biz\"?\s*\.\s*\"?alerts\"?"                     # biz.alerts / "biz"."alerts" / ONLY biz.alerts
UPDATE_RX = re.compile(r"UPDATE\s+" + _T + r"(?:\s+(?:AS\s+)?\w+)??\s+SET\b(?P<set>.*?)(?:\bWHERE\b|$)", re.S | re.I)
INSERT_RX = re.compile(r"INSERT\s+INTO\s+" + _T + r"(?![\w\"])", re.I)
DELETE_RX = re.compile(r"DELETE\s+FROM\s+" + _T + r"(?![\w\"])", re.I)
DELETE_ALLOWED = {"seed_demo.py"}                                   # dọn dữ liệu demo (không phải đường ghi tín hiệu)


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
        if DELETE_RX.search(src) and str(p.relative_to(GH)) not in DELETE_ALLOWED:
            bad.append(f"{p.relative_to(GH)}: DELETE FROM biz.alerts")
        for m in UPDATE_RX.finditer(src):
            if re.search(r"\b(level|lane|status)\s*=", m.group("set"), re.I):
                bad.append(f"{p.relative_to(GH)}: UPDATE biz.alerts SET level/lane/status")
    assert not bad, bad


def _hits(src: str) -> bool:
    if INSERT_RX.search(src) or DELETE_RX.search(src):
        return True
    return any(re.search(r"\b(level|lane|status)\s*=", m.group("set"), re.I) for m in UPDATE_RX.finditer(src))


def test_intel_single_writer_fence_catches_alias_quoted_and_delete() -> None:
    """Rào không lách được bằng alias, tên có ngoặc kép, ONLY, hay DELETE (review M5); vẫn cho phép cột không phải mức/ngăn/trạng thái."""
    caught = ["UPDATE biz.alerts SET status = 'resolved' WHERE id = 1",
              "UPDATE biz.alerts AS a SET status = 'x' WHERE a.id = 1",
              "UPDATE biz.alerts a SET level = 'red' WHERE a.id = 1",
              'UPDATE "biz"."alerts" SET lane = \'fyi\'',
              "update ONLY biz.alerts set status='x'",
              'INSERT INTO "biz"."alerts" (id) VALUES (1)',
              "DELETE FROM biz.alerts WHERE id = 1",
              'delete from "biz".alerts where id = 1']
    for q in caught:
        assert _hits(q), q
    for q in ("UPDATE biz.alerts SET digest_at = now() WHERE id = 1", "UPDATE biz.alerts_archive SET status = 'x'",
              "SELECT status FROM biz.alerts", "INSERT INTO biz.alerts_x (id) VALUES (1)"):
        assert not _hits(q), q


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
