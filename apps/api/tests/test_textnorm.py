"""F-38 (v0.1.43) — gh/textnorm.py là nơi duy nhất bỏ dấu tiếng Việt; rules và triage dùng chung (không cần DB)."""

import unicodedata

import pytest

from gh import textnorm
from gh.refinery import rules, triage

SAMPLES = [
    "Đơn hàng giá bao nhiêu?",
    unicodedata.normalize("NFD", "Đơn hàng giá bao nhiêu?"),
    "ｇｉá",
    "m²",
    "Ðơn",
    "  Chị   Hồng\tđặt 20 thùng…  ",
]


def test_rules_and_triage_share_one_strip_accents() -> None:
    assert rules.strip_accents is textnorm.strip_accents
    assert triage.strip_accents is textnorm.strip_accents


@pytest.mark.parametrize("s", SAMPLES)
def test_samples_strip_the_same_in_rules_and_triage(s: str) -> None:
    assert rules.strip_accents(s) == triage.strip_accents(s) == textnorm.strip_accents(s)
    assert rules.EventCtx(s).plain == textnorm.strip_accents(rules.normalize(s))
    assert rules.normalize(s) == textnorm.collapse_lower(s)


def test_strip_accents_values() -> None:
    sa = textnorm.strip_accents
    assert sa("Đơn hàng giá bao nhiêu?") == "Don hang gia bao nhieu?"
    assert sa(unicodedata.normalize("NFD", "Đơn hàng giá bao nhiêu?")) == "Don hang gia bao nhieu?"
    assert sa("ｇｉá") == "gia"
    assert sa("m²") == "m2"
    assert sa("Ðơn") == sa("Đơn") == "Don"
    assert textnorm.collapse_lower("  Chị \n HỒNG ") == "chị hồng"


def test_nfc_and_nfd_give_same_triage_normalize_and_hash() -> None:
    for s in ("Đơn hàng giá bao nhiêu?", "Chị Hồng đặt 20 thùng nước suối, giao thứ Năm nhé"):
        nfc, nfd = unicodedata.normalize("NFC", s), unicodedata.normalize("NFD", s)
        assert nfc != nfd
        assert triage.normalize(nfc) == triage.normalize(nfd)
        assert triage.text_hash(triage.normalize(nfc)) == triage.text_hash(triage.normalize(nfd))


def test_has_accents_ignores_compatibility_characters() -> None:
    # '…', 'm²', chữ toàn chiều rộng không phải dấu tiếng Việt — tin gõ không dấu vẫn được so khớp dạng không dấu.
    assert not rules.has_accents("gia bao nhieu…")
    assert not rules.has_accents("dien tich 30m²")
    assert not rules.has_accents("ｇｉａ")
    assert rules.has_accents("giá") and rules.has_accents("đơn") and rules.has_accents("Ðon")
    ctx = rules.EventCtx("gia bao nhieu…")
    assert ctx.accented is False and ctx.plain == "gia bao nhieu..."
