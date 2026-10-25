# ruff: noqa: E501
"""v0.1.76 (gói T2, spec r4 §4.6 / CNS-035) — `/hom_nay` trên mọi kênh chat = bản tin KÉO từ cùng `digest.build(audience='chat')`
(số đếm khớp bản tin Zalo), đặt trước các mục riêng của `briefing`. Kéo không "tiêu" mục (không đánh dấu `digest_at`).
Kênh chat bỏ mục `console_pin`; chữ từ tin nhóm được defang."""

from __future__ import annotations

from typing import Any

from sqlalchemy import text

from gh.db import sessionmaker
from gh.owner_cmd import core as owner_core
from tests.mem_helpers import mk_org
from tests.test_intel_digest_rawtail_v0176 import NOW, _install, _sig


def ctx(org: Any, redis: Any) -> owner_core.Context:
    return owner_core.Context(sm=sessionmaker(), redis=redis, router=None, org_id=org, owner_user_id=None)


async def test_hom_nay_pulls_chat_digest_first_and_does_not_consume(db: Any, redis: Any) -> None:
    from datetime import timedelta

    org = await mk_org(db)
    await _install(db, org)
    amber = await _sig(db, org, "cau_hoi_cho_xu_ly", "q1", impact_base=2, deadline_at=NOW + timedelta(hours=10))
    priv = await _sig(db, org, "cau_hoi_cho_xu_ly", "q-priv", privacy="console_pin", impact_base=2, deadline_at=NOW + timedelta(hours=10))
    await db.commit()
    reply = await owner_core.hom_nay(ctx(org, redis))
    lines = reply.text.splitlines()
    assert lines[0].startswith("Hôm nay · ")
    assert any(ln.startswith("1. 🟠") and "cau_hoi_cho_xu_ly q1" in ln and "«abc»" in ln for ln in lines), reply.text
    assert "q-priv" not in reply.text and priv.id
    await db.rollback()
    assert (await db.execute(text("SELECT digest_at FROM biz.alerts WHERE id = :i"), {"i": amber.id})).scalar_one() is None


async def test_hom_nay_without_signals_has_no_intel_block(db: Any, redis: Any) -> None:
    org = await mk_org(db)
    reply = await owner_core.hom_nay(ctx(org, redis))
    assert "1. " not in reply.text and reply.text.startswith("Hôm nay · ")
