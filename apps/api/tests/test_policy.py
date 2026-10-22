"""Bảng chân lý policy: cờ rủi ro × mức tự trị 0–6 (ARCHITECTURE §7)."""

import uuid

import pytest

from gh.chassis import policy
from gh.chassis.policy import AUTO, BLOCKED, HELD, SUGGEST

THRESHOLD = 50_000_000


def expected(key: str, level: int, amount: int, whitelist: frozenset[str]) -> str:
    t = policy.action_type(key)
    if level <= 2:
        return BLOCKED
    if level == 3:
        return SUGGEST
    if t.writes_external or t.personnel_related or (t.has_amount and amount > THRESHOLD):
        return HELD
    if level == 4:
        return HELD
    if level == 5:
        return AUTO if t.low_risk else HELD
    return AUTO if (t.low_risk or key in whitelist) else HELD


KEYS = ["message.send", "quotation.send", "contract.send", "crm.write", "erp.draft_order", "mcp.write",
        "people.review_update", "people.alert", "task.create", "reminder.create", "note.write", "label.apply",
        "owner.assign", "report.create"]


@pytest.mark.parametrize("key", KEYS)
@pytest.mark.parametrize("level", range(7))
@pytest.mark.parametrize("amount", [0, THRESHOLD, THRESHOLD + 1])
@pytest.mark.parametrize("whitelist", [frozenset(), frozenset({"owner.assign", "message.send", "people.alert"})])
def test_truth_table(key: str, level: int, amount: int, whitelist: frozenset[str]) -> None:
    d = policy.evaluate(key, level, amount_vnd=amount, approval_threshold_vnd=THRESHOLD, whitelist=whitelist)
    assert d.outcome == expected(key, level, amount, whitelist)


@pytest.mark.parametrize("key", ["message.send", "quotation.send", "mcp.write", "people.review_update", "people.alert"])
def test_hard_gates_hold_even_when_whitelisted_at_level_6(key: str) -> None:
    d = policy.evaluate(key, 6, whitelist=frozenset({key}))
    assert d.outcome == HELD
    assert d.hold_reason


def test_amount_over_threshold_is_held_with_reason() -> None:
    d = policy.evaluate("quotation.send", 6, amount_vnd=120_000_000)
    assert d.outcome == HELD
    assert "vượt ngưỡng 50.000.000 ₫" in (d.hold_reason or "")


def test_effective_autonomy_is_minimum() -> None:
    assert policy.effective_autonomy(6, 5, None, 4) == 4
    assert policy.effective_autonomy() == policy.DEFAULT_AUTONOMY
    with pytest.raises(ValueError):
        policy.effective_autonomy(7)


def test_unknown_action_is_rejected() -> None:
    with pytest.raises(KeyError):
        policy.evaluate("bank.transfer", 6)


def test_action_type_flags_cannot_be_redefined() -> None:
    with pytest.raises(ValueError):
        policy.register_action_type(policy.ActionType("message.send", "Gửi tin", writes_external=False, low_risk=True))


def test_permit_binds_draft_body_target_and_expiry() -> None:
    did = uuid.uuid4()
    token, digest, exp = policy.issue_permit(did, b"noi dung", "zalo:GRP-1", now=1000.0)
    assert policy.check_permit(token, did, b"noi dung", "zalo:GRP-1", now=1001.0)
    assert not policy.check_permit(token, did, b"noi dung da sua", "zalo:GRP-1", now=1001.0)
    assert not policy.check_permit(token, did, b"noi dung", "zalo:GRP-2", now=1001.0)
    assert not policy.check_permit(token, uuid.uuid4(), b"noi dung", "zalo:GRP-1", now=1001.0)
    assert not policy.check_permit(token, did, b"noi dung", "zalo:GRP-1", now=exp + 1)
    assert not policy.check_permit("rac", did, b"noi dung", "zalo:GRP-1", now=1001.0)
    assert len(digest) == 32
