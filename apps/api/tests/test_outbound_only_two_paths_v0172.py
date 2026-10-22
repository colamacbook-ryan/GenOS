# ruff: noqa: E501
"""v0.1.72: KHÔNG có đường gửi thứ tư. Quét AST toàn bộ `gh/**`:
  I1  chỉ HAI module được phát lên luồng gửi của bridge / ký permit gửi: `biz/core/drafts.py` (bản nháp + PIN) và
      `owner_channel/autosend.py` (cửa `send_auto`, ba cơ sở).
  I2  `send_auto` chỉ được import ở các module kênh quản trị liệt kê dưới đây.
  I7  module của kênh quản trị (trừ `autosend.py`) và `owner_cmd/*` không import đường ghi/duyệt/PIN/Kho/bridge.
Thêm module mới chạm vào các tên này ⇒ test đỏ; muốn thêm đường thứ tư phải được Sếp duyệt và sửa test này."""

import ast
from pathlib import Path

GH = Path(__file__).resolve().parents[1] / "gh"
SENDER_MODULES = {"biz/core/drafts.py", "owner_channel/autosend.py"}
#: Nơi ĐỊNH NGHĨA các tên (không phải nơi dùng để gửi).
DEFINERS = {"chassis/bus.py", "crypto.py"}
SEND_AUTO_IMPORTERS = {"owner_channel/dm.py", "owner_channel/reports.py", "owner_channel/group_reply.py",
                       "owner_channel/pairing.py", "owner_channel/autosend.py"}
FORBIDDEN_NAMES = {"BRIDGE_OUTBOUND", "bridge_permit"}
FORBIDDEN_STRINGS = {"gh.bridge.outbound", "message.send"}


def _py_files() -> list[Path]:
    return sorted(p for p in GH.rglob("*.py") if "__pycache__" not in p.parts)


def _rel(p: Path) -> str:
    return p.relative_to(GH).as_posix()


def _names(tree: ast.AST) -> set[str]:
    out: set[str] = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Name):
            out.add(n.id)
        elif isinstance(n, ast.Attribute):
            out.add(n.attr)
        elif isinstance(n, ast.alias):
            out.add(n.name.split(".")[-1])
            if n.asname:
                out.add(n.asname)
    return out


def _strings(tree: ast.AST) -> set[str]:
    return {n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)}


def _tree(p: Path) -> ast.AST:
    return ast.parse(p.read_text(encoding="utf-8"), filename=str(p))


def test_i1_only_two_modules_can_publish_outbound_or_sign_send_permit() -> None:
    offenders: dict[str, set[str]] = {}
    for p in _py_files():
        rel = _rel(p)
        if rel in SENDER_MODULES or rel in DEFINERS:
            continue
        tree = _tree(p)
        hit = (_names(tree) & FORBIDDEN_NAMES) | {s for s in _strings(tree) if s in {"gh.bridge.outbound"}}
        if hit:
            offenders[rel] = hit
    assert offenders == {}, f"Đường gửi thứ tư? {offenders}"


def test_i1_message_send_event_type_is_only_published_by_the_two_modules() -> None:
    """Loại sự kiện `message.send` lên bus: chỉ hai module gửi. (`policy.py`/`drafts.KIND_ACTION` dùng nó làm TÊN hành động
    chính sách, không phát lên bus — kiểm riêng bằng việc không gọi `.publish(` kèm hằng này.)"""
    for p in _py_files():
        rel = _rel(p)
        if rel in SENDER_MODULES:
            continue
        for n in ast.walk(_tree(p)):
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "publish":
                consts = {a.value for a in n.args if isinstance(a, ast.Constant)}
                assert "message.send" not in consts, f"{rel} phát message.send"


def test_i1_hmac_sign_used_only_by_policy_drafts_and_crypto() -> None:
    users = set()
    for p in _py_files():
        if "hmac_sign" in _names(_tree(p)):
            users.add(_rel(p))
    assert users <= {"crypto.py", "chassis/policy.py", "biz/core/drafts.py"}, users
    assert "owner_channel/autosend.py" not in users          # autosend ký qua drafts.bridge_permit, không tự ký


def test_i1_both_sender_modules_really_exist_and_use_the_stream() -> None:
    for rel in SENDER_MODULES:
        assert FORBIDDEN_NAMES & _names(_tree(GH / rel)), rel


def test_i2_send_auto_only_imported_by_listed_owner_channel_modules() -> None:
    offenders = []
    for p in _py_files():
        rel = _rel(p)
        if "send_auto" in _names(_tree(p)) and rel not in SEND_AUTO_IMPORTERS:
            offenders.append(rel)
    assert offenders == [], offenders


FORBIDDEN_IMPORT_MODULES = ("gh.biz.core.drafts", "gh.hub_link.permit", "gh.hub_link.kho_write", "gh.mcp_api",
                           "gh.chassis.mcp_client", "gh.auth.pin")
FORBIDDEN_IMPORT_NAMES = {"require_pin", "decide", "permit", "mcp_write", "BRIDGE_OUTBOUND", "bridge_permit"}


#: Ngoại lệ DUY NHẤT: `draft_hand.py` TẠO bản nháp chờ Xác nhận + PIN (đường cũ) — chỉ được lấy ba tên này từ drafts.
DRAFT_HAND_NAMES = {"create_draft", "effective_level", "Target"}


def _import_violations(files: list[Path]) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for p in files:
        bad: set[str] = set()
        for n in ast.walk(_tree(p)):
            if isinstance(n, ast.ImportFrom) and n.module:
                if p.name == "draft_hand.py" and n.module == "gh.biz.core.drafts":
                    bad |= {a.name for a in n.names if a.name not in DRAFT_HAND_NAMES}
                    continue
                if n.module.startswith(FORBIDDEN_IMPORT_MODULES):
                    bad.add(n.module)
                # routes.py của Console đòi PIN cho thao tác đổi trạng thái — `require_pin` là phụ thuộc, không phải đường gửi.
                bad |= {a.name for a in n.names if a.name in FORBIDDEN_IMPORT_NAMES
                        and not (p.name == "routes.py" and a.name == "require_pin")}
            elif isinstance(n, ast.Import):
                bad |= {a.name for a in n.names if a.name.startswith(FORBIDDEN_IMPORT_MODULES)}
        if bad:
            out[p.name if GH not in p.parents else _rel(p)] = bad
    return out


def test_i7_owner_channel_and_owner_cmd_modules_import_no_write_executor() -> None:
    files = [p for p in _py_files() if _rel(p).startswith(("owner_channel/", "owner_cmd/")) and _rel(p) != "owner_channel/autosend.py"]
    assert files, "không có module để kiểm"
    assert _import_violations(files) == {}


def test_scanner_would_catch_a_fourth_path(tmp_path: Path) -> None:
    """Tự kiểm bộ quét: một module giả gọi `BRIDGE_OUTBOUND` / `send_auto` bị bắt."""
    bad = tmp_path / "bad.py"
    bad.write_text("from gh.chassis.bus import BRIDGE_OUTBOUND\nasync def f(bus):\n    await bus.publish(BRIDGE_OUTBOUND, 'message.send', {})\n")
    tree = _tree(bad)
    assert FORBIDDEN_NAMES & _names(tree)
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "publish"]
    assert calls and "message.send" in {a.value for a in calls[0].args if isinstance(a, ast.Constant)}
    bad2 = tmp_path / "bad2.py"
    bad2.write_text("from gh.owner_channel.autosend import send_auto\n")
    assert "send_auto" in _names(_tree(bad2))
    bad3 = tmp_path / "bad3.py"
    bad3.write_text("from gh.biz.core.drafts import decide\n")
    assert _import_violations([bad3]) != {}
