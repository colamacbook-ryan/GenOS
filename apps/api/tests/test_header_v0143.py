"""v0.1.43 (F-29): /header tách "chưa nối kênh" (channels_connected = 0) với "kênh mất phiên" (đã từng nối,
channels_live = 0) để trạng thái trống của Console chỉ đúng chỗ: quét lại QR thay vì nối kênh từ đầu."""

from sqlalchemy import text

from tests.conftest import Api
from tests.phase2 import org_id


async def test_header_channels_connected_counts_channels_that_ever_logged_in(app, db, owner_api: Api) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    h = (await owner_api.get("/header")).json()
    assert (h["channels_live"], h["channels_connected"]) == (0, 0)     # kênh có sẵn từ bootstrap không tính

    ch = (await db.execute(text("SELECT id FROM core.channels WHERE org_id = :o AND type = 'zalo'"),
                           {"o": org})).scalar()
    # Phiên chờ QR chưa bao giờ đăng nhập → vẫn là "chưa nối kênh".
    await db.execute(text("""INSERT INTO core.channel_sessions (channel_id, org_id, account_label, state)
                             VALUES (:c, :o, 'Zalo Sếp', 'pending_qr')"""), {"c": ch, "o": org})
    await db.commit()
    h = (await owner_api.get("/header")).json()
    assert (h["channels_live"], h["channels_connected"]) == (0, 0)

    await db.execute(text("UPDATE core.channel_sessions SET state = 'active', started_at = now()"))
    await db.commit()
    h = (await owner_api.get("/header")).json()
    assert (h["channels_live"], h["channels_connected"]) == (1, 1)

    # Phiên hết hạn: không còn kênh sống nhưng vẫn là kênh đã nối (cần quét lại QR).
    await db.execute(text("UPDATE core.channel_sessions SET state = 'expired', ended_at = now()"))
    await db.commit()
    h = (await owner_api.get("/header")).json()
    assert (h["channels_live"], h["channels_connected"]) == (0, 1)
