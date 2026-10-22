"""v0.1.39 (F-32) — Gen biết trang Tài khoản mạng xã hội (/social, chỉ Owner) và thẻ Facebook ở Hệ thống › Kênh."""

from gh.auth import rbac
from gh.gen import envelope, registry
from gh.gen.validator import Validator


def _owner() -> dict[str, str]:
    return dict(rbac.DEFAULT_MATRIX[rbac.OWNER])


def _operator() -> dict[str, str]:
    return dict(rbac.DEFAULT_MATRIX[rbac.OPERATOR])


def test_social_screen_in_registry() -> None:
    assert registry.screen_exists("social")
    assert registry.load().screens["social"] == {"path": "/social", "title": "Tài khoản mạng xã hội"}
    assert registry.load().screens["guide"]["title"] == "Hướng dẫn thiết lập"


def test_visible_screens_social_owner_only() -> None:
    owner = {s["key"]: s for s in registry.visible_screens(_owner())}
    assert owner["social"]["path"] == "/social"
    assert "social" not in {s["key"] for s in registry.visible_screens(_operator())}


def test_validator_navigate_social() -> None:
    nav = envelope.Navigate(type="navigate", screen="social")
    assert Validator(_owner(), set(), "overview").check(nav).ok
    assert not Validator(_operator(), set(), "overview").check(nav).ok


def test_boss_checks_screen_owner_only() -> None:
    """F-74: Gen mở được trang "Việc Sếp cần làm" cho Owner; Vận hành thì không (trang chỉ Owner)."""
    assert registry.load().screens["boss_checks"] == {"path": "/guide/viec-sep", "title": "Việc Sếp cần làm"}
    assert "boss_checks" in {s["key"] for s in registry.visible_screens(_owner())}
    assert "boss_checks" not in {s["key"] for s in registry.visible_screens(_operator())}
    nav = envelope.Navigate(type="navigate", screen="boss_checks")
    assert Validator(_owner(), set(), "overview").check(nav).ok
    assert not Validator(_operator(), set(), "overview").check(nav).ok


def test_facebook_card_target() -> None:
    t = registry.resolve_target("system.channels.facebook")
    assert t is not None
    # v0.1.42 (F-7): thẻ Facebook ở Kết nối (/connections) — id giữ nguyên, không còn tham số tab.
    assert t.screen == "connections"
    assert t.params is None
    assert t.permission == "roles.manage"      # thẻ chỉ hiện cho Owner → target chỉ Owner (như các target Owner khác)
    v = Validator(_owner(), set(), "overview")
    assert v.check(envelope.Navigate(type="navigate", screen="connections")).ok
    assert v.check(envelope.Highlight(type="highlight", target="system.channels.facebook", message="Thẻ Facebook")).ok
    # Vai trò tuỳ biến có system.manage nhưng không phải Owner: không chỉ tới thẻ không hiện trên màn hình của họ.
    admin = _operator() | {"system.read": rbac.ALL, "system.manage": rbac.ALL}
    hl = envelope.Highlight(type="highlight", target="system.channels.facebook", message="Thẻ Facebook")
    assert not Validator(admin, set(), "overview").check(hl).ok
