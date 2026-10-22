"""v0.1.65 (review P3) — nhận tin từ nhóm đã bị đánh dấu "rời" thì xoá cờ rời."""

from sqlalchemy import text

from gh.db import sessionmaker
from tests.phase2 import listen, msg, org_id, put


async def test_message_from_a_group_marked_left_clears_the_left_flag(owner_api, db) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    gid = await listen(db, org, "gL", mode="silent")
    await db.execute(text("UPDATE core.groups SET left_at = now() WHERE id = :g"), {"g": gid})
    await db.commit()
    [ev] = await put(sessionmaker(), org, msg("Còn ở đây mà", group="gL"))
    assert ev is not None
    await db.rollback()
    left = (await db.execute(text("SELECT left_at FROM core.groups WHERE id = :g"), {"g": gid})).scalar_one()
    assert left is None                                   # nhận được tin ⇒ chắc chắn còn trong nhóm
