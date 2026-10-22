# ruff: noqa: E501
"""v0.1.73 (lỗi C): `send.result` của bridge KHÔNG mang `session_id` (đúng giao thức: nonce, draft_id, auto_send_id?, channel,
ok, external_msg_id?, error?). Trước đây `handle_status` tra phiên theo `session_id` rồi thoát sớm ⇒ `ops.auto_sends` kẹt
«issued» (sent_at / external_msg_id rỗng) dù bridge đã gửi xong. Nay `send.result` được xử lý TRƯỚC bước tra phiên."""

from typing import Any

from sqlalchemy import text

from gh.data import ingest
from gh.db import sessionmaker
from gh.owner_channel import autosend
from gh.owner_channel.basis import Basis, Trigger
from tests.owner_helpers import FakeBus, bot_world
from tests.phase2 import org_id
from tests.test_autosend_v0172 import CLEAN, ledger, outbox, send


class Ev:
    stream = "gh.bridge.status"
    message_id = None

    def __init__(self, type_: str, payload: dict[str, Any]) -> None:
        self.type = type_
        self.payload = payload


async def one_issued(db: Any, redis: Any) -> tuple[Any, Any]:
    org = await org_id(db)
    await bot_world(db, org)
    r = await send(org, FakeBus(), redis, basis=Basis.OWNER_DM, trigger=Trigger("report", await outbox(db, org)), text=CLEAN)
    [row] = await ledger(db)
    assert row.status == "issued" and row.sent_at is None and row.external_msg_id is None
    return org, r


async def test_send_result_without_session_id_marks_auto_send_sent(app, db, redis) -> None:  # type: ignore[no-untyped-def]
    org, r = await one_issued(db, redis)
    ing = ingest.Ingest(FakeBus(), redis, org, sessionmaker())
    # đúng hình dạng bridge phát (apps/bridge/src/sessions.js #send): không có session_id
    await ing.on_status(Ev("send.result", {"nonce": "n1", "draft_id": None, "channel": "zalo", "ok": True,
                                           "auto_send_id": str(r.auto_send_id), "external_msg_id": "zm-77"}))
    [row] = await ledger(db)
    assert (row.status, row.external_msg_id, row.error) == ("sent", "zm-77", None)
    assert row.sent_at is not None
    log = (await db.execute(text("SELECT result FROM ops.action_log WHERE action = 'autosend.sent'"))).scalar_one()
    assert log == "ok"


async def test_send_result_without_session_id_marks_failed_with_error(app, db, redis) -> None:  # type: ignore[no-untyped-def]
    org, r = await one_issued(db, redis)
    ing = ingest.Ingest(FakeBus(), redis, org, sessionmaker())
    await ing.on_status(Ev("send.result", {"nonce": "n2", "draft_id": None, "channel": "zalo", "ok": False,
                                           "auto_send_id": str(r.auto_send_id), "error": "SEND_FAILED"}))
    [row] = await ledger(db)
    assert (row.status, row.error, row.sent_at, row.external_msg_id) == ("failed", "SEND_FAILED", None, None)


async def test_send_result_is_applied_once_and_other_org_cannot_touch(app, db, redis) -> None:  # type: ignore[no-untyped-def]
    import uuid

    org, r = await one_issued(db, redis)
    ing = ingest.Ingest(FakeBus(), redis, org, sessionmaker())
    p = {"nonce": "n3", "draft_id": None, "channel": "zalo", "ok": True, "auto_send_id": str(r.auto_send_id), "external_msg_id": "a"}
    await ing.on_status(Ev("send.result", p))
    await ing.on_status(Ev("send.result", {**p, "ok": False, "error": "X", "external_msg_id": "b"}))   # lần hai bị bỏ
    other = ingest.Ingest(FakeBus(), redis, uuid.uuid4(), sessionmaker())
    await other.on_status(Ev("send.result", {**p, "ok": False, "error": "Y"}))                       # tổ chức khác: không đụng
    [row] = await ledger(db)
    assert (row.status, row.external_msg_id) == ("sent", "a")
    assert autosend is not None


async def test_draft_send_result_without_session_id_is_still_handled(app, db, redis) -> None:  # type: ignore[no-untyped-def]
    """Cùng lỗi gốc cho bản nháp: `send.result` không có session_id vẫn phải tới `drafts.on_send_result`."""
    org = await org_id(db)
    seen: list[dict[str, Any]] = []
    from gh.biz.core import drafts

    orig = drafts.on_send_result

    async def spy(db_: Any, org_: Any, p: Any, redis_: Any = None) -> Any:
        seen.append(p)
        return await orig(db_, org_, p, redis_)

    drafts.on_send_result = spy  # type: ignore[assignment]
    try:
        ing = ingest.Ingest(FakeBus(), redis, org, sessionmaker())
        await ing.on_status(Ev("send.result", {"nonce": "n4", "draft_id": "00000000-0000-0000-0000-000000000001",
                                               "channel": "zalo", "ok": True, "external_msg_id": "z"}))
    finally:
        drafts.on_send_result = orig  # type: ignore[assignment]
    assert len(seen) == 1
