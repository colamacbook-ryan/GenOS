# ruff: noqa: F811 — fixture `w` nhập từ test_p3_duty (tham số test trùng tên là cách pytest dùng fixture)
"""v0.1.65 (review P2-4) — `external_id` JID WhatsApp (chứa số điện thoại) trong hồ sơ bị che với vai dưới Owner."""

from typing import Any

from sqlalchemy import text

from tests.conftest import Api
from tests.phase2 import msg, put
from tests.test_p3_duty import w  # noqa: F401
from tests.test_rbac_api import login_as


async def _person(db: Any, external_id: str) -> Any:
    return (await db.execute(text("SELECT person_id FROM core.person_identities WHERE external_id = :x"),
                             {"x": external_id})).scalar_one()


async def test_profile_masks_whatsapp_jid_external_id_below_owner(w, db, redis, client) -> None:  # type: ignore[no-untyped-def]
    owner: Api = w["api"]
    jid = "84909123456@s.whatsapp.net"
    await put(w["sm"], w["org"], msg("chào", group=None, sender=jid, name="Khách Z", channel="whatsapp"))
    pz = await _person(db, jid)
    op = await login_as(client, db, "operator")

    def ext(r: Any) -> list[str]:
        assert r.status_code == 200, r.text
        return [i["external_id"] for i in r.json()["identities"]]

    assert ext(await owner.get(f"/profile/{pz}")) == [jid]                      # Owner thấy nguyên
    masked = ext(await op.get(f"/profile/{pz}"))
    assert masked and "84909123456" not in masked[0] and masked[0].endswith("456@s.whatsapp.net"), masked
    # Zalo (uid dài) cũng che như mọi số dài; id ngắn giữ nguyên
    assert "84909123456" not in str(masked)
