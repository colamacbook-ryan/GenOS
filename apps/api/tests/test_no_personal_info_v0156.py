"""v0.1.56 — không tiết lộ địa chỉ Gen-hub / tên riêng của chủ Gen-hub cho Owner khác.

(a) Tên Kho chung: `KHO_LABEL` một chỗ (gh.hub_link), mọi chuỗi chạy thật (ghi chú máy chủ, nhãn nguồn, nhãn thẻ đề
    xuất, nhãn quyền PIN, mô tả tool, prompt Gen, bài học / mẹo, registry) dùng đúng chữ đó — không còn tên riêng;
(b) câu lỗi địa chỉ Gen-hub chỉ mô tả dạng `https://<máy-chủ>/mcp`, không nêu máy chủ thật;
(c) quét mã nguồn gh/: không còn các mẫu cấm (song song với `.github/scripts/check_no_personal_info.py`);
(d) migration 0035 cập nhật ghi chú máy chủ Gen-hub cũ đã lưu trong DB Owner, chạy lại an toàn, không đụng ghi chú khác;
(e) nối Gen-hub mới lưu ghi chú chung vào DB.
"""

import importlib.util
import json
import os
import re
from pathlib import Path
from typing import Any

import psycopg
import pytest
from sqlalchemy import text

from gh.auth import service as auth_service
from gh.gen import kho_release, proposals
from gh.gen.tools import TOOLS
from gh.hub_link import KHO_LABEL
from gh.hub_link import service as hub
from tests.conftest import PG, Api
from tests.test_hub_link import FakeHub, _configure, _db_text
from tests.test_hub_link import fake_hub as fake_hub  # noqa: F401  (fixture)

ROOT = Path(__file__).resolve().parents[3]
GH_DIR = ROOT / "apps" / "api" / "gh"
SQL_0035 = ROOT / "db" / "sql" / "0035_v0156_hub_note_generic.sql"
# v0.1.57: KHÔNG ghi tên riêng/tên miền riêng thật vào test. Ghi chú cũ (mang tên riêng) lấy thẳng từ câu khớp của
# migration 0035 — nguồn khớp duy nhất, đã mang chú thích `allow-personal-info`; tên cũ cắt ra từ chính ghi chú đó.
_OLD_NOTE_MATCH = re.search(r"AND note = '([^']+)'; -- allow-personal-info", SQL_0035.read_text(encoding="utf-8"))
assert _OLD_NOTE_MATCH is not None, "migration 0035 không còn câu khớp ghi chú cũ"
OLD_NOTE = _OLD_NOTE_MATCH.group(1)
_OLD_NAME_MATCH = re.search(r"Gen đọc (.+?), lịch", OLD_NOTE)
assert _OLD_NAME_MATCH is not None
OLD_NAME = _OLD_NAME_MATCH.group(1)
# Ghi chú ĐÍCH của migration 0035 (còn "(chỉ đọc)"); v0.1.69 (F124, migration 0041) đổi sang `CURRENT_NOTE` bên dưới.
NEW_NOTE = "Liên kết Gen-hub — Gen đọc Kho dữ liệu, lịch, mail, việc, Drive (chỉ đọc). Quản lý ở thẻ Gen-hub."
CURRENT_NOTE = ("Liên kết Gen-hub — Gen đọc Kho dữ liệu, lịch, mail, việc, Drive "
                "(đọc; ghi Kho chỉ khi Sếp Xác nhận + mã PIN). Quản lý ở thẻ Gen-hub.")

_CHECK_PATH = ROOT / ".github" / "scripts" / "check_no_personal_info.py"
_spec = importlib.util.spec_from_file_location("check_no_personal_info", _CHECK_PATH)
assert _spec is not None and _spec.loader is not None
check = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check)


def _private_patterns() -> list[re.Pattern[str]]:
    """Mẫu riêng: tên cũ (lấy từ migration) + mẫu trong `GH_PERSONAL_PATTERNS` nếu môi trường có (CI nạp từ secret)."""
    pats, invalid = check.load_private_patterns(os.environ)
    assert invalid == []
    return [re.compile(re.escape(OLD_NAME), re.IGNORECASE), *pats]


def test_kho_label_is_single_generic_name() -> None:
    assert KHO_LABEL == "Kho dữ liệu"
    assert hub.KHO_LABEL is KHO_LABEL
    assert hub.SERVER_NOTE == CURRENT_NOTE
    assert hub.source_of("kho_get") == "Kho dữ liệu qua Gen-hub"
    assert hub.source_of("gmail_search") == "Gmail qua Gen-hub"          # nguồn khác không đổi


def test_runtime_strings_use_generic_name() -> None:
    kho_tools = [t for t in TOOLS.values() if t.name.startswith("hub.kho_")]
    assert len(kho_tools) == 3
    chunks = [hub.SERVER_NOTE, auth_service.PIN_OPERATIONS["hub.write"], kho_release.SAY, kho_release.BELL_BODY,
              *proposals.TYPE_LABELS.values(), *proposals.OWNER_ONLY_MSG.values(), *(t.description for t in kho_tools)]
    for c in chunks:
        assert OLD_NAME not in c, c
    assert proposals.TYPE_LABELS["kho_create"] == "Ghi vào Kho dữ liệu (tạo mới)"
    assert proposals.TYPE_LABELS["kho_update"] == "Ghi vào Kho dữ liệu (cập nhật)"
    assert auth_service.PIN_OPERATIONS["hub.write"] == "Ghi Kho dữ liệu qua Gen-hub (Phiên, Việc)"
    assert "Kho dữ liệu" in kho_release.SAY and "Kho dữ liệu" in kho_release.BELL_BODY
    assert all("Kho dữ liệu" in t.description for t in kho_tools if t.name in ("hub.kho_summary", "hub.kho_get"))


def test_static_content_uses_generic_name() -> None:
    gen_dir = GH_DIR / "gen"
    for rel in ("coach/content/lessons.json", "coach/content/tips.json", "registry.json"):
        raw = (gen_dir / rel).read_text(encoding="utf-8")
        json.loads(raw)                                                    # vẫn là JSON hợp lệ
        assert OLD_NAME not in raw, rel
    lessons = json.loads((gen_dir / "coach/content/lessons.json").read_text(encoding="utf-8"))
    assert any(KHO_LABEL in (x.get("title", "") + x.get("body", "")) for x in lessons)


def test_endpoint_error_describes_shape_without_real_host() -> None:
    assert "https://<máy-chủ>/mcp" in hub.ENDPOINT_INVALID_MSG
    assert hub.ENDPOINT_INVALID_MSG.startswith("Địa chỉ Gen-hub không hợp lệ")
    assert check.MCP_URL.search(hub.ENDPOINT_INVALID_MSG) is None          # chỉ chỗ giữ chỗ <…>, không có máy chủ thật
    assert not any(p.search(hub.ENDPOINT_INVALID_MSG) for p in _private_patterns())


def test_api_source_has_no_personal_pattern() -> None:
    """Mã API giao đi sạch: quy tắc chung (email không phải example, /mcp thật) + mẫu riêng của môi trường + tên cũ."""
    bad = [f"{h.rel}:{h.line_no} ({h.rule})" for h in check.violations(ROOT, _private_patterns())
           if h.rel.startswith("apps/api/gh/")]
    assert bad == []


def test_general_rules_catch_real_email_and_mcp_host_without_private_patterns(tmp_path: Path) -> None:
    f = tmp_path / "apps" / "api" / "gh" / "x.py"
    f.parent.mkdir(parents=True)
    f.write_text('A = "ai.do@nha-that.vn"\nB = "https://hub.nha-that.vn/mcp"\nC = "vd@example.test"\n'
                 'D = "https://<máy-chủ>/mcp"\n', encoding="utf-8")
    hits = [(h.line_no, h.rule) for h in check.violations(tmp_path, [])]
    assert hits == [(1, "email"), (2, "mcp_url")]


async def test_new_link_stores_generic_note(owner_api: Api, fake_hub: FakeHub) -> None:  # noqa: F811
    await _configure(owner_api)
    assert OLD_NAME not in await _db_text("SELECT name, note FROM agent.mcp_servers")
    assert CURRENT_NOTE in await _db_text("SELECT note FROM agent.mcp_servers WHERE name = 'Gen-hub'")


async def test_migration_0035_updates_old_note_only_and_is_rerunnable(owner_api: Api, db: Any, fresh_db: str) -> None:
    org = (await db.execute(text("SELECT id FROM core.organizations LIMIT 1"))).scalar_one()
    await db.rollback()
    ins = ("INSERT INTO agent.mcp_servers (org_id, name, transport, endpoint, note) "
           "VALUES (%s, %s, 'streamable_http', 'https://mcp.example.test/mcp', %s)")
    with psycopg.connect(f"{PG}/{fresh_db}", autocommit=True) as c:
        c.execute(ins, (org, "Gen-hub", OLD_NOTE))                          # Owner cài bản cũ: ghi chú mang tên riêng
        c.execute(ins, (org, "Gen-hub", "Ghi chú Owner tự sửa " + OLD_NAME))  # Owner đã tự sửa tay ⇒ KHÔNG đụng
        c.execute(ins, (org, "Máy chủ khác", OLD_NOTE))                     # tên máy chủ khác ⇒ KHÔNG đụng
        c.execute(ins, (org, "Gen-hub", NEW_NOTE))                          # đã là bản mới ⇒ giữ nguyên
        sql = SQL_0035.read_text(encoding="utf-8")

        def snap() -> list[Any]:
            return c.execute("SELECT name, note FROM agent.mcp_servers ORDER BY name, note").fetchall()

        before = snap()
        c.execute(sql)  # type: ignore[call-overload]
        first = snap()
        c.execute(sql)  # type: ignore[call-overload]
        assert snap() == first                                              # chạy lại lần hai không đổi gì
        assert first != before
        assert sorted(first) == sorted([
            ("Gen-hub", NEW_NOTE), ("Gen-hub", NEW_NOTE), ("Gen-hub", "Ghi chú Owner tự sửa " + OLD_NAME),
            ("Máy chủ khác", OLD_NOTE)])
        assert c.execute("SELECT count(*) FROM agent.mcp_servers").fetchone() == (4,)


def test_migration_0035_follows_0034_and_has_single_head() -> None:
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    api_dir = ROOT / "apps" / "api"
    cfg = Config(str(api_dir / "alembic.ini"))
    cfg.set_main_option("script_location", str(api_dir / "migrations"))
    script = ScriptDirectory.from_config(cfg)
    rev = script.get_revision("0035")
    assert rev is not None and rev.down_revision == "0034"
    assert len(script.get_heads()) == 1   # v0.1.61: đầu cây đã lên 0036; chỉ cần còn đúng một head


@pytest.mark.parametrize("name", ["lessons.json", "tips.json"])
def test_coach_content_keeps_json_shape(name: str) -> None:
    data = json.loads((GH_DIR / "gen" / "coach" / "content" / name).read_text(encoding="utf-8"))
    assert isinstance(data, list) and data
