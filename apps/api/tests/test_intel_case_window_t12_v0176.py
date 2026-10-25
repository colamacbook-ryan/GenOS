# ruff: noqa: E501
"""v0.1.76 (T12, gỡ Nợ #83) — khoá vụ KHÔNG gắn cửa sổ cố định (ngày VN / bucket epoch). Trước đây hai tin của CÙNG một kẻ lừa vắt qua
nửa đêm (khoá `raw|actor:<người>:<ngày>`) hoặc qua biên bucket 24 giờ (07:00 VN) ra HAI vụ ⇒ có thể đẩy 🔴 bypass hai lần. Nay: khoá chỉ
gồm dữ kiện (STK / người bị mạo danh / domain / người gửi / khách …); cửa sổ TRƯỢT theo loại (`levels.case_window`) kiểm trong
`signals.upsert` bằng mốc tin mới nhất của vụ đang mở; quá cửa sổ ⇒ vụ mới với hậu tố tuần tự `|#n`."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gh.biz.intel import levels as L
from gh.biz.intel import signals
from gh.biz.intel.detectors import fraud
from tests import fraud_helpers
from tests.fraud_helpers import world

A = datetime(2026, 10, 23, 23, 50, tzinfo=L.VN)
B = datetime(2026, 10, 24, 0, 10, tzinfo=L.VN)


def test_case_keys_have_no_fixed_time_window() -> None:
    """Mọi khoá vụ dựa trên dữ kiện, không đổi khi qua nửa đêm / biên bucket 07:00 VN."""
    for kw, kind in (({"payee_digest": "h", "group_id": "g"}, L.FR), ({"impersonated_entity_id": "e"}, L.FR), ({"domain": "x.top"}, L.FR),
                     ({"entity_id": "me"}, L.SK), ({"entity_id": "p"}, "quan_he_nong_nguoi"), ({"simhash": "ab"}, "thi_truong_thong_tin"),
                     ({"customer_entity_id": "c"}, "xung_dot_khieu_nai")):
        assert L.case_key(kind, at=A, **kw) == L.case_key(kind, at=B, **kw) == L.case_key(kind, at=A + timedelta(days=30), **kw), kind
    seven = datetime(2026, 10, 23, 6, 50, tzinfo=L.VN)
    assert L.case_key(L.SK, at=seven, entity_id="me") == L.case_key(L.SK, at=seven + timedelta(minutes=20), entity_id="me")


def test_case_window_by_kind() -> None:
    H = timedelta(hours=1)
    assert L.case_window(L.case_key(L.FR, at=A, impersonated_entity_id="e")) == 24 * H
    assert L.case_window(L.case_key(L.FR, at=A, domain="x.top")) == 24 * H
    assert L.case_window(L.case_key(L.FR, at=A, payee_digest="h", group_id="g")) == 7 * 24 * H
    assert L.case_window(L.case_key(L.FR, at=A, raw_event_id="actor:p1")) == 24 * H
    assert L.case_window(L.case_key(L.SK, at=A, entity_id="me")) == 24 * H
    assert L.case_window(L.case_key("xung_dot_khieu_nai", at=A, customer_entity_id="c")) == 72 * H
    assert L.case_window(L.case_key("xung_dot_khieu_nai", at=A, customer_entity_id="c", reply_chain_id="r")) is None
    assert L.case_window(L.case_key("quan_he_nong_nguoi", at=A, entity_id="p")) == 14 * 24 * H
    assert L.case_window(L.case_key("thi_truong_thong_tin", at=A, simhash="s")) == 7 * 24 * H
    assert L.case_window(L.case_key(L.FR, at=A, raw_event_id="r1")) is None                       # một tin gốc = một sự việc
    assert L.case_window(L.case_key(L.FR, at=A, impersonated_entity_id="e") + "|#2") == 24 * H    # hậu tố tuần tự không đổi cửa sổ


def _li(at: datetime) -> L.SignalIn:
    return L.SignalIn(kind=L.FR, msg_at=at, event_at=at, hard_rule_hit=["credentials_entered"], flags=L.Flags(credentials_entered=True),
                      source_class="rule", confidence_exist=1.0, confidence_content=1.0, harm_what="mất tiền",
                      victims=[L.PersonRel(ref="u_v", team=True, thread_member=True)], thread=L.Thread(type="group", size=20, agent_in_thread=True),
                      impact_base=3)


async def _upsert(db: AsyncSession, org, at: datetime, now: datetime) -> signals.Signal:  # type: ignore[no-untyped-def]
    raw = (await db.execute(text("""INSERT INTO raw.events (org_id, occurred_at, channel_id, external_msg_id, kind, body_text, payload, content_hash)
                                    VALUES (:o, :t, (SELECT id FROM core.channels WHERE org_id = :o LIMIT 1), gen_random_uuid()::text, 'text', 'x', '{}', '\\x01')
                                    RETURNING id"""), {"o": org, "t": at})).scalar_one()
    ev = signals.EvidenceIn(type="raw_event", id=raw, source_field="body", span_start=0, span_end=1)
    ck = L.case_key(L.FR, at=at, impersonated_entity_id="ent-ke-mao-danh")
    return await signals.upsert(db, org, signals.UpsertIn(kind=L.FR, title="t", detector="test", level_input=_li(at), evidence=[ev], case_key=ck), now)


async def test_same_impersonator_across_midnight_is_one_case_one_red(db: AsyncSession) -> None:
    w = await world(db)
    s1 = await _upsert(db, w.org, A, A + timedelta(minutes=1))
    s2 = await _upsert(db, w.org, B, B + timedelta(minutes=1))
    assert s1.id == s2.id and (s1.delivery, s2.delivery) == ("push_now", "merge")


async def test_same_impersonator_25h_later_is_a_new_case(db: AsyncSession) -> None:
    w = await world(db)
    s1 = await _upsert(db, w.org, A, A + timedelta(minutes=1))
    later = A + timedelta(hours=25)
    s2 = await _upsert(db, w.org, later, later + timedelta(minutes=1))
    assert s1.id != s2.id and s2.case_key == s1.case_key + "|#2" and s2.delivery == "push_now"
    s3 = await _upsert(db, w.org, later + timedelta(hours=2), later + timedelta(hours=2, minutes=1))     # trong cửa sổ của vụ #2
    assert s3.id == s2.id and s3.delivery == "merge"


def _last_vn(h: int, m: int, s: int = 0) -> datetime:
    real = datetime.now(L.VN).replace(microsecond=0)
    t = real.replace(hour=h, minute=m, second=s)
    return t if t <= real else t - timedelta(days=1)


@pytest.mark.parametrize("pin", [(23, 59, 30), (0, 15, 0), (0, 0, 20)])
async def test_fraud_actor_case_survives_midnight_at_any_clock(db: AsyncSession, monkeypatch: pytest.MonkeyPatch, pin: tuple[int, int, int]) -> None:
    """Cùng người lạ, hai tin cách 29′ (không gom) — đồng hồ ghim quanh nửa đêm VN (CI chạy ~00:00 VN): MỘT vụ, MỘT 🔴."""
    now = _last_vn(*pin)
    monkeypatch.setattr(fraud_helpers, "now_vn", lambda: now)
    w = await world(db)
    await w.group("gd", "Nhóm nhỏ", 20)
    _s, iid = await w.person("Người lạ", group="gd", joined_days_ago=1)
    await w.raw("Cho em mượn 2 triệu, chuyển vào STK 0987654321 VCB giúp em", sender=iid, group="gd", at=w.now - timedelta(minutes=30))
    first = await fraud.run(db, w.org, w.now - timedelta(minutes=29))
    await w.raw("Chuyển vào STK 0123456789 MB nhé chị, em cần gấp", sender=iid, group="gd")
    second = await fraud.run(db, w.org, w.now)
    assert len(first) == 1 and len(second) == 1 and first[0].id == second[0].id
    n = (await db.execute(text("SELECT count(*) FROM biz.alerts WHERE org_id = :o"), {"o": w.org})).scalar_one()
    assert n == 1
