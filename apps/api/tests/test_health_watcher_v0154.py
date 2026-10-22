"""v0.1.54: người gác yêu cầu (`gen-harness-update-request.path`) tự chữa — khối `watcher` trong
`run/nightly-status.json` (genh ghi) ⇒ khối `nightly.watcher` của /system/health khi KHÔNG ok.

Tệp trong run/ (0777) là dữ liệu không tin cậy: state/reason ngoài tập bị lọc; câu gợi ý do API tự ghép từ chuỗi cố định
— không bao giờ chứa chữ lấy từ tệp. Thiếu khối (genh cũ) = ok ⇒ không có khoá `watcher`."""

import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from gh import health
from gh.config import get_settings
from gh.system_api import update


@pytest.fixture
def link(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    d = tmp_path / "run"
    (d / "request").mkdir(parents=True)
    (d / "genh.json").write_text(json.dumps({"version": "v0.1.54", "updater": "systemd"}))
    monkeypatch.setenv("GH_HOST_LINK_DIR", str(d))
    get_settings.cache_clear()
    yield d
    get_settings.cache_clear()


def write_nightly(link: Path, **kw: Any) -> None:
    ago = (datetime.now(UTC) - timedelta(hours=2)).isoformat().replace("+00:00", "Z")
    data: dict[str, Any] = {
        "schema": 1, "mechanism": "systemd", "enabled": True, "active": True, "unit_present": True, "opted_out": False,
        "since": ago, "last_run_at": ago, "last_result": "done", "next_run_at": "2099-01-01T03:00:00Z",
        "linger": "yes", "request_watcher": "failed", "checked_at": ago}
    data.update(kw)
    (link / "nightly-status.json").write_text(json.dumps(data))


INOTIFY_HINT = ("Hết hạn mức inotify: chạy `sudo sysctl -w fs.inotify.max_user_instances=1024` "
                "rồi `genh auto-update enable`.")


def test_fallback_inotify_adds_fixed_hint(link: Path) -> None:
    write_nightly(link, watcher={"state": "fallback", "reason": "inotify", "hint": "chữ lạ từ tệp, phải bị bỏ"})
    blk = health._nightly_status()
    assert blk["state"] == "ok"  # lịch đêm vẫn khoẻ — người gác chỉ là cảnh báo thêm
    assert blk["watcher"] == {"state": "fallback", "reason": "inotify", "hint": INOTIFY_HINT}
    assert "chữ lạ" not in json.dumps(blk, ensure_ascii=False)


def test_missing_ok_and_garbage_watcher_mean_no_key(link: Path) -> None:
    write_nightly(link)  # genh cũ: không có khối watcher
    assert "watcher" not in health._nightly_status()
    write_nightly(link, watcher={"state": "ok", "reason": "", "hint": ""})
    assert "watcher" not in health._nightly_status()
    for bad in ("pwn", 3, None, ["fallback"], {"state": "pwn", "reason": "inotify"}):
        write_nightly(link, watcher=bad)
        assert "watcher" not in health._nightly_status(), bad
    # state hợp lệ nhưng reason lạ ⇒ 'other' (vẫn có gợi ý cố định).
    write_nightly(link, watcher={"state": "failed", "reason": "rm -rf /"})
    w = health._nightly_status()["watcher"]
    assert w["state"] == "failed" and w["reason"] == "other" and w["hint"] == update.WATCHER_HINTS["other"]
    assert "rm -rf" not in json.dumps(w)
