# ruff: noqa: E501
"""Bộ tin giả đổ vào `raw.events` THẬT (Postgres + ingest thật): phát lại bằng replay trên đồng hồ giả → kho thô giữ giờ gốc, reply_to, voice/ảnh qua media_ref; khử trùng khi
phát lại; tin injection là dữ liệu trơ; bị kick / rớt phiên đi đúng đường; bộ nhập lịch sử không kích hoạt gì và không nhân đôi."""


from pathlib import Path
from typing import Any

import import_history as ih
import parsers as ps
import replay
from sqlalchemy import text

from gh.data import ingest
from gh.db import sessionmaker
from tests.owner_helpers import make_session
from tests.phase2 import listen, org_id
from tests.scenarios import intel_helpers as h
from tests.scenarios import intel_injection as inj


class IngestSink:
    """Đích đổ tin: gọi thẳng `ingest_message` (đường của `gh.bridge.inbound`) — đúng nơi bus cuối cùng đi tới."""

    def __init__(self, org: Any) -> None:
        self.org = org
        self.inserted: list[Any] = []
        self.dropped = 0
        self.other: list[tuple[str, str]] = []

    async def emit(self, stream: str, type: str, payload: dict[str, Any]) -> None:
        if (stream, type) != ("gh.bridge.inbound", "message"):
            self.other.append((stream, type))
            return
        async with sessionmaker()() as db:
            eid = await ingest.ingest_message(db, self.org, payload)
            await db.commit()
        if eid:
            self.inserted.append(eid)
        else:
            self.dropped += 1

    async def close(self) -> None:
        return None


async def test_replay_into_raw_events_keeps_original_time_order_reply_and_media(app, db, redis, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    for g in ("G1", "G2"):
        await listen(db, org, g)
    ds = h.synth_dataset()
    sink = IngestSink(org)
    s = await replay.run_replay(ds, tmp_path / "r1", sink=sink)
    assert s["distribution_check"]["ok"] and sink.dropped == 0 and len(sink.inserted) == len(ds.msgs)
    rows = (await db.execute(text("""SELECT e.external_msg_id, e.occurred_at, e.kind, e.body_text, e.payload, e.mentions_agent, e.direction, g.external_id AS grp
                                     FROM raw.events e JOIN core.groups g ON g.id = e.group_id ORDER BY e.occurred_at, e.external_msg_id"""))).all()
    assert len(rows) == len(ds.msgs)
    by = {r.external_msg_id: r for r in rows}
    for m in ds.msgs:
        r = by[m.id]
        assert r.occurred_at == m.ts and r.grp == m.group and r.direction == "inbound" and r.mentions_agent is False       # giờ gửi GỐC, không phải giờ nén/giờ máy
        assert r.payload["fake"] is True
        if m.reply_to:
            assert r.payload["reply_to"] == m.reply_to
    voice = next(m for m in ds.msgs if m.kind == "voice_transcript")
    image = next(m for m in ds.msgs if m.kind == "image_caption")
    assert by[voice.id].body_text is None and by[voice.id].payload["media_ref"].startswith("fake://voice/")
    assert by[voice.id].payload["fake_media_text"] == voice.text
    assert by[voice.id].kind == "other"        # ĐIỂM CHỜ L5: `ingest.KINDS` chưa có 'voice' ⇒ 'other'; media_ref + chữ giả đã đủ để `media.process` chạy khi L5 vào
    # ảnh: chữ trong bộ dữ liệu là MÔ TẢ do máy sinh (không phải caption người gửi) ⇒ đi qua engine media giả, body_text rỗng
    assert by[image.id].kind == "image" and by[image.id].body_text is None
    assert by[image.id].payload["fake_media_text"] == h.common.strip_media_prefix(image.text)
    assert (await db.execute(text("SELECT count(*) FROM refinery.event_state WHERE state = 'pending'"))).scalar() == len(ds.msgs)
    # đồng hồ máy thật KHÔNG chạy theo đồng hồ giả: received_at là giờ máy (gần now), occurred_at là giờ tin
    assert (await db.execute(text("SELECT count(*) FROM raw.events WHERE received_at < now() - interval '1 hour'"))).scalar() == 0


async def test_replaying_again_is_idempotent_no_duplicates(app, db, redis, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    for g in ("G1", "G2"):
        await listen(db, org, g)
    ds = h.synth_dataset(60, 30)
    s1, s2 = IngestSink(org), IngestSink(org)
    await replay.run_replay(ds, tmp_path / "a", sink=s1)
    await replay.run_replay(ds, tmp_path / "b", sink=s2, confirmed=False)           # lần chạy thứ hai (chưa xác nhận) đổ lại cùng tin
    assert len(s1.inserted) == len(ds.msgs) and len(s2.inserted) == 0 and s2.dropped == len(ds.msgs)
    assert (await db.execute(text("SELECT count(*) FROM raw.events"))).scalar() == len(ds.msgs)


async def test_unlistened_group_is_not_stored(app, db, redis, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    await listen(db, org, "G1")
    await listen(db, org, "G2", mode="off")
    ds = h.synth_dataset(30, 20)
    sink = IngestSink(org)
    await replay.run_replay(ds, tmp_path / "a", sink=sink)
    assert len(sink.inserted) == 30 and sink.dropped == 20          # luật khoá cứng nghe-có-chọn-lọc vẫn nguyên khi đổ tin giả


async def test_injection_messages_are_stored_verbatim_and_trigger_nothing(app, db, redis, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    for g in ("G1", "G2"):
        await listen(db, org, g)
    ds, injs = inj.injected_dataset(h.synth_dataset(60, 30))
    before = {t: (await db.execute(text(f"SELECT count(*) FROM {t}"))).scalar() for t in ("ops.owner_outbox", "ops.auto_sends", "ops.owner_questions", "mem.facts")}  # noqa: S608
    sink = IngestSink(org)
    await replay.run_replay(ds, tmp_path / "a", sink=sink)
    for i in injs:
        r = (await db.execute(text("SELECT body_text, mentions_agent, direction, payload FROM raw.events WHERE external_msg_id = :m"), {"m": i.id})).one()
        assert r.body_text == i.text and r.mentions_agent is False and r.direction == "inbound"       # nguyên văn, là DỮ LIỆU
        assert set(r.payload) <= {"fake", "seq"}                                                      # không mang cờ quyền nào
    after = {t: (await db.execute(text(f"SELECT count(*) FROM {t}"))).scalar() for t in before}  # noqa: S608
    assert after == before                                                                           # không câu hỏi/nháp/gửi/sự thật nào phát sinh từ tin xấu
    assert (await db.execute(text("SELECT count(*) FROM raw.events"))).scalar() == len(ds.msgs)


async def test_kick_and_session_drop_go_through_real_handlers(app, db, redis) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    sid = await make_session(db, org, kind="bot", account="acc-1")
    await listen(db, org, "gK")
    fb = h.common.fake_bridge()
    events = [{"type": "session_drop", "session_id": str(sid), "reason": "error", "error": "NETWORK_UNAVAILABLE"}, {"type": "kicked", "group": "gK", "by": "u-x"}]
    async with sessionmaker()() as s:
        for _stream, typ, payload in fb.compile_script(events):
            if typ == "group.self_removed":
                assert await ingest.handle_self_removed(s, redis, org, payload) is True
            else:
                await ingest.handle_status(s, redis, app.state.bus, org, typ, payload)
        await s.commit()
    assert (await db.execute(text("SELECT left_at IS NOT NULL FROM core.groups WHERE external_id = 'gK'"))).scalar() is True
    st = (await db.execute(text("SELECT state, ended_at FROM core.channel_sessions WHERE id = :i"), {"i": sid})).one()
    assert st.state == "ended" or st.state in ("expired", "error", "logged_out")


async def test_import_history_envelopes_go_through_ingest_idempotently_and_trigger_nothing(app, db, redis, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    await listen(db, org, "G-WA", channel="whatsapp")
    wa = ("12/10/2026, 14:06 - Nguyễn Thị Lan: Chị lấy 20kg Blend Honey như cũ nha @bot\n13/10/2026, 09:00 - Ryan: Dạ em lên đơn\n13/10/2026, 09:02 - Nguyễn Thị Lan: <Media omitted>\n"
          "14/10/2026, 10:00 - Ryan: ok\n14/10/2026, 10:00 - Ryan: ok\n")
    envs = ih.build_envelopes(ps.parse_whatsapp(wa), channel="whatsapp", group_id="G-WA", group_name="Nhóm G-WA", owner_name="Ryan", batch="t")
    for round_ in (1, 2):
        got = []
        async with sessionmaker()() as s:
            for p in envs:
                got.append(await ingest.ingest_message(s, org, p))
            await s.commit()
        assert (sum(1 for x in got if x) == 5) if round_ == 1 else not any(got)           # nhập lại: 0 tin mới
    rows = (await db.execute(text("""SELECT e.occurred_at, e.direction, e.kind, e.body_text, e.payload, e.mentions_agent, es.fast FROM raw.events e
                                     JOIN refinery.event_state es ON es.event_id = e.id ORDER BY e.occurred_at, e.external_msg_id"""))).all()
    assert [r.occurred_at.isoformat() for r in rows][0] == "2026-10-12T07:06:00+00:00"            # 14:06 +07:00 → giờ gửi gốc
    assert all(r.payload["source"] == "import" and r.mentions_agent is False and r.fast is False for r in rows)      # lịch sử: không đường nhanh, không gọi bot
    assert [r.direction for r in rows].count("outbound") == 3 and rows[2].kind == "file" and rows[2].body_text is None
    assert (await db.execute(text("SELECT count(*) FROM ops.owner_outbox"))).scalar() == 0
    # hai "ok" giống hệt cùng phút đều được giữ
    assert sum(1 for r in rows if r.body_text == "ok") == 2
