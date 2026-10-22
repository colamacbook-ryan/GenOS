# ruff: noqa: E501
"""v0.1.72 (I5): kịch bản ngẫu nhiên hạt giống cố định (~300 sự kiện) — người lạ, Sếp, tag giả bằng tên trùng, người khác thêm
bot, bật/tắt hỗ trợ, tin cũ, đổi loại tài khoản, câu bị bộ lọc chặn… chạy qua `send_auto` với bus giả. KHÔNG tin người gọi: lúc
MỖI tin ra bus, bộ kiểm độc lập đọc lại DB và đòi (a) có dòng sổ `ops.auto_sends` khớp băm thân tin + cơ sở + đích, (b) điều
kiện của cơ sở ĐÚNG theo dữ liệu thật, (c) permit không có `draft_id`. Cuối cùng: số tin ra == số lần `ok` == số dòng sổ."""

import random
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import text

from gh.chassis.bus import BRIDGE_OUTBOUND
from gh.db import sessionmaker
from gh.owner_channel import autosend
from gh.owner_channel.basis import Basis, Trigger
from gh.owner_channel.identity import is_owner, owner_ids
from tests.owner_helpers import OWNER_ZALO, FakeBus, bot_world, enable_support
from tests.phase2 import listen, msg, org_id, put
from tests.test_autosend_v0172 import CLEAN, decode_permit

TEXTS = [CLEAN, "Dạ cửa hàng mở từ 8 giờ ạ.", "Dạ giá 150k ạ", "Gọi 0901234567 nhé", "", "   ", "Hợp đồng ký ngay ạ",
         "x" * 1700, "Em chào anh/chị ạ.", "@Lan xem giúp", "https://evil.example.com/a", "lương của anh B"]


class CheckedBus(FakeBus):
    """Mỗi tin ra bus phải qua bộ kiểm độc lập (đọc lại DB)."""

    def __init__(self, org: uuid.UUID) -> None:
        super().__init__()
        self.org = org
        self.checked = 0

    async def publish(self, stream: str, type: str, payload: dict[str, Any], **kw: Any) -> str:
        if stream == BRIDGE_OUTBOUND:
            await self._validate(payload)
            self.checked += 1
        return await super().publish(stream, type, payload, **kw)

    async def _validate(self, p: dict[str, Any]) -> None:
        claims = decode_permit(p["permit"])
        assert "draft_id" not in claims and claims["basis"] in {"owner_dm", "owner_tag", "support"}, claims
        async with sessionmaker()() as s:
            row = (await s.execute(text("SELECT * FROM ops.auto_sends WHERE id = :i"),
                                   {"i": uuid.UUID(claims["auto_send_id"])})).one()
            assert row.org_id == self.org and row.basis == claims["basis"] and row.thread_id == p["thread_id"] == claims["thread_id"]
            import hashlib

            assert row.body_sha256 == hashlib.sha256(p["text"].encode()).hexdigest() == claims["body_sha256"]
            sess = (await s.execute(text("SELECT account_kind, state FROM core.channel_sessions WHERE id = :i"),
                                    {"i": row.session_id})).one()
            assert sess.account_kind == "bot" and sess.state == "active"
            oi = await owner_ids(s, self.org, "zalo")
            if row.basis == "owner_dm":
                assert oi.enabled and row.thread_type == "user" and row.thread_id == oi.thread_id == OWNER_ZALO
                return
            g = (await s.execute(text("SELECT id, external_id, listen_mode, left_at FROM core.groups WHERE id = :g"),
                                 {"g": row.group_id})).one()
            assert row.thread_type == "group" and g.external_id == row.thread_id and g.left_at is None
            assert g.listen_mode in ("tagged_only", "silent", "proactive")
            ev = (await s.execute(text("""SELECT e.mentions_agent, e.direction, e.group_id, i.external_id AS ext, e.payload
                                          FROM raw.events e JOIN core.person_identities i ON i.id = e.sender_identity_id
                                          WHERE e.id = CAST(:i AS uuid)"""), {"i": row.trigger_ref})).one()
            assert ev.direction == "inbound" and ev.group_id == g.id
            if row.basis == "owner_tag":
                assert oi.enabled and ev.mentions_agent and is_owner(oi, ev.ext)
            else:
                sup = (await s.execute(text("""SELECT 1 FROM ops.group_support WHERE group_id = :g AND disabled_at IS NULL
                                               AND (until_at IS NULL OR until_at > now())"""), {"g": g.id})).first()
                assert sup is not None and not is_owner(oi, ev.ext)
                assert ev.mentions_agent or (ev.payload or {}).get("replies_to_self") is True


async def test_random_scenario_only_three_cases_ever_reach_the_bridge(app, db, redis) -> None:  # type: ignore[no-untyped-def]
    rng = random.Random(20261018)
    org = await org_id(db)
    sid = await bot_world(db, org)
    g_sup = await listen(db, org, "g-sup", mode="silent")
    g_plain = await listen(db, org, "g-plain", mode="silent")
    g_off = await listen(db, org, "g-off", mode="tagged_only")
    await enable_support(db, g_sup)
    groups = {"g-sup": g_sup, "g-plain": g_plain, "g-off": g_off}
    bus = CheckedBus(org)
    sm = sessionmaker()
    ok_count = 0
    attempts = 0
    for _step in range(300):
        action = rng.choice(["member", "owner_tag", "fake_owner", "owner_dm", "stale", "wrong_group", "toggle", "kind", "wrong_basis"])
        text_ = rng.choice(TEXTS)
        now = None
        kw: dict[str, Any] = {}
        grp = rng.choice(list(groups))
        if action == "member":
            [eid] = await put(sm, org, msg("@bot hỏi chút", group=grp, sender=f"u{rng.randint(1, 4)}", mentions=rng.random() < 0.8))
            basis, trig, kw["group_id"] = Basis.SUPPORT, Trigger("raw_event", str(eid)), groups[grp]
        elif action == "owner_tag":
            [eid] = await put(sm, org, msg("@bot giúp", group=grp, sender=OWNER_ZALO, name="Sếp Ryan", mentions=rng.random() < 0.8))
            basis, trig, kw["group_id"] = rng.choice([Basis.OWNER_TAG, Basis.SUPPORT]), Trigger("raw_event", str(eid)), groups[grp]
        elif action == "fake_owner":
            [eid] = await put(sm, org, msg("@bot giúp", group=grp, sender=f"fake{rng.randint(1, 3)}", name="Sếp Ryan", mentions=True))
            basis, trig, kw["group_id"] = Basis.OWNER_TAG, Trigger("raw_event", str(eid)), groups[grp]
        elif action == "owner_dm":
            oid = (await db.execute(text("""INSERT INTO ops.owner_outbox (org_id, channel_type, kind, severity, dedupe_key, text)
                                            VALUES (:o, 'zalo', 'alert', 'important', :d, 'x') RETURNING id"""),
                                    {"o": org, "d": uuid.uuid4().hex})).scalar_one()
            await db.commit()
            basis = Basis.OWNER_DM
            trig = Trigger("report", rng.choice([str(oid), str(oid), str(uuid.uuid4()), "rác"]))
            if rng.random() < 0.2:
                kw["group_id"] = groups[grp]
        elif action == "stale":
            [eid] = await put(sm, org, msg("@bot cũ", group=grp, sender=rng.choice([OWNER_ZALO, "u9"]), mentions=True))
            basis, trig, kw["group_id"] = rng.choice([Basis.OWNER_TAG, Basis.SUPPORT]), Trigger("raw_event", str(eid)), groups[grp]
            now = datetime.now(UTC) + timedelta(hours=rng.randint(1, 5))
        elif action == "wrong_group":
            [eid] = await put(sm, org, msg("@bot ở nhóm khác", group="g-sup", sender="u1", mentions=True))
            basis, trig, kw["group_id"] = rng.choice([Basis.OWNER_TAG, Basis.SUPPORT]), Trigger("raw_event", str(eid)), groups["g-plain"]
        elif action == "toggle":
            await db.execute(text("UPDATE ops.group_support SET disabled_at = CASE WHEN disabled_at IS NULL THEN now() ELSE NULL END, disabled_reason = CASE WHEN disabled_at IS NULL THEN 'owner' ELSE NULL END"))
            await db.commit()
            continue
        elif action == "kind":
            await db.execute(text("UPDATE core.channel_sessions SET account_kind = CASE account_kind WHEN 'bot' THEN 'owner_self' ELSE 'bot' END WHERE id = :i"), {"i": sid})
            await db.commit()
            continue
        else:
            [eid] = await put(sm, org, msg("@bot", group=grp, sender="u1", mentions=True))
            basis, trig, kw["group_id"] = rng.choice(["telegram_dm", "owner", "", "support "]), Trigger("raw_event", str(eid)), groups[grp]  # type: ignore[assignment]
        attempts += 1
        async with sm() as s:
            r = await autosend.send_auto(s, bus, redis, org_id=org, channel="zalo", basis=basis, trigger=trig, text=text_,
                                         now=now, **kw)
        ok_count += bool(r.ok)
        if not r.ok:
            assert r.code
    out = bus.on(BRIDGE_OUTBOUND)
    rows = (await db.execute(text("SELECT count(*) FROM ops.auto_sends"))).scalar()
    await db.rollback()
    assert len(out) == ok_count == bus.checked == rows, (len(out), ok_count, rows)
    assert attempts > 150 and ok_count >= 5                      # kịch bản có đủ ca được gửi lẫn ca bị chặn
    assert all(t == "message.send" for t, _ in out)
