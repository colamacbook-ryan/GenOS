"""v0.1.35 (F-20): tạo / sửa nhà cung cấp AI, chuỗi ưu tiên, thêm khoá API cần phiên PIN `ai.route_change`.

Mục tiêu: phiên Owner bị lấy (vd qua XSS) không thể âm thầm chuyển lưu lượng LLM ra máy chủ lạ. PIN kiểm SAU quyền:
vai trò thiếu `system.manage` vẫn nhận 403 (không lộ là route có PIN). Ngoài phạm vi (không 423): GET, /test (kiểm
đường đi, không mở đường mới).

Dọn sót v0.1.70: THÊM / SỬA / TẮT-BẬT / CHỌN MẶC ĐỊNH model của một nguồn (`POST /providers/{id}/models`) và XOÁ model
(`DELETE .../models/{mid}`) cũng đổi model nào Gen gọi ⇒ cùng cổng PIN (hai dòng `add_model`, `delete_model`).

Review v0.1.68 (P3): XOÁ nhà cung cấp / xoá khoá API cũng đổi chuỗi nguồn AI (gỡ nguồn của Sếp, ép Gen rơi sang nguồn
còn lại) ⇒ nay cũng cần PIN `ai.route_change` (trước đây ghi "chỉ thu hẹp" — sai: gỡ nguồn tốt để nguồn lạ đứng đầu là
đổi đường đi). Hai dòng `delete`, `del_key` ở bảng dưới.
"""

import secrets
import uuid

import pytest
from sqlalchemy import text

from gh import crypto
from gh.auth.service import PIN_OPERATIONS
from gh.providers.router import KEY_AAD
from tests.conftest import Api, verify_pin
from tests.phase2 import org_id
from tests.test_rbac_api import login_as


def _fake_key() -> str:
    # Khoá test sinh ngẫu nhiên — không bao giờ là khoá thật.
    return "sk-test-" + secrets.token_hex(12)


async def _seed_provider(db) -> uuid.UUID:  # type: ignore[no-untyped-def]
    return (await _seed_provider_key(db))[0]


async def _seed_provider_key(db) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    pid = (await db.execute(text("""INSERT INTO agent.providers (org_id, kind, name, endpoint, failover_rank)
                                    VALUES (:o, 'openai_compat', 'Nguồn có sẵn', 'https://co-san.test/v1', 1)
                                    RETURNING id"""), {"o": org})).scalar_one()
    k = _fake_key()
    kid = (await db.execute(text("""INSERT INTO agent.provider_keys
                                    (provider_id, label, secret_enc, last4, rotation_order)
                                    VALUES (:p, 'API-KEY-01', :s, :f, 0) RETURNING id"""),
                            {"p": pid, "s": crypto.encrypt(k.encode(), KEY_AAD), "f": k[-4:]})).scalar_one()
    mid = (await db.execute(text("""INSERT INTO agent.models (provider_id, model_name) VALUES (:p, 'm-co-san')
                                    RETURNING id"""), {"p": pid})).scalar_one()
    await db.commit()
    return pid, kid, mid


def _routes(pid: uuid.UUID, kid: uuid.UUID, mid: uuid.UUID) -> list[tuple[str, str, dict | None]]:  # type: ignore[type-arg]
    return [
        ("POST", "/providers", {"kind": "openai_compat", "name": "Máy chủ lạ", "endpoint": "https://la.test/v1",
                                "keys": [_fake_key()], "models": ["m-la"]}),
        ("PATCH", "/providers/chain", {"provider_ids": [str(pid)]}),
        ("PATCH", f"/providers/{pid}", {"enabled": False}),
        ("PATCH", f"/providers/{pid}", {"failover_rank": 2}),
        ("POST", f"/providers/{pid}/keys", {"secret": _fake_key()}),
        ("POST", f"/providers/{pid}/models", {"model_name": "m-moi", "make_default": True}),
        ("DELETE", f"/providers/{pid}/models/{mid}", None),
        ("DELETE", f"/providers/{pid}/keys/{kid}", None),
        ("DELETE", f"/providers/{pid}", None),
    ]


ROUTE_IDS = ["create", "chain", "toggle", "rank", "add_key", "add_model", "delete_model", "del_key", "delete"]
OK_STATUS = {"create": 201, "chain": 200, "toggle": 200, "rank": 200, "add_key": 201, "add_model": 201,
             "delete_model": 204, "del_key": 204, "delete": 204}


def test_route_change_declared() -> None:
    assert "ai.route_change" in PIN_OPERATIONS
    assert PIN_OPERATIONS["ai.route_change"] == "Thêm / sửa / xoá nhà cung cấp AI, model, khoá API, chuỗi ưu tiên"


@pytest.mark.parametrize("idx", range(len(ROUTE_IDS)), ids=ROUTE_IDS)
async def test_owner_without_pin_gets_423_then_ok(owner_api, db, idx: int) -> None:  # type: ignore[no-untyped-def]
    api: Api = owner_api
    pid, kid, mid = await _seed_provider_key(db)
    method, path, body = _routes(pid, kid, mid)[idx]
    r = await api.send(method, path, body)
    assert r.status_code == 423, r.text
    j = r.json()
    assert j["code"] == "PIN_REQUIRED"
    assert j.get("detail") is None or isinstance(j["detail"], str)
    # Chưa có gì thay đổi khi bị chặn.
    provs = (await api.get("/providers")).json()
    assert [p["name"] for p in provs] == ["Nguồn có sẵn"]
    assert provs[0]["enabled"] is True and len(provs[0]["keys"]) == 1 and provs[0]["failover_rank"] == 1

    await verify_pin(api)
    r = await api.send(method, path, body)
    assert r.status_code == OK_STATUS[ROUTE_IDS[idx]], r.text


@pytest.mark.parametrize("role", ["manager", "operator"])
@pytest.mark.parametrize("idx", range(len(ROUTE_IDS)), ids=ROUTE_IDS)
async def test_role_without_permission_gets_403_not_423(owner_api, client, db, role: str,  # type: ignore[no-untyped-def]
                                                        idx: int) -> None:
    pid, kid, mid = await _seed_provider_key(db)
    staff = await login_as(client, db, role)
    method, path, body = _routes(pid, kid, mid)[idx]
    r = await staff.send(method, path, body)
    assert r.status_code == 403, r.text
    assert r.json()["code"] == "FORBIDDEN"
    # Kể cả khi đã nhập PIN của chính mình, vẫn 403.
    await verify_pin(staff, "112233")
    r = await staff.send(method, path, body)
    assert r.status_code == 403, r.text


async def test_out_of_scope_routes_not_pin_gated(owner_api, db) -> None:  # type: ignore[no-untyped-def]
    api: Api = owner_api
    pid = await _seed_provider(db)
    assert (await api.get("/providers")).status_code == 200
    r = await api.send("POST", f"/providers/{pid}/test")
    assert r.status_code != 423, r.text
    # Xoá nhà cung cấp / khoá API KHÔNG còn ở ngoài phạm vi PIN từ v0.1.68 (xem docstring đầu tệp).


async def test_action_log_still_recorded_with_pin(owner_api, db) -> None:  # type: ignore[no-untyped-def]
    api: Api = owner_api
    await verify_pin(api)
    r = await api.send("POST", "/providers", {"kind": "deepseek", "name": "DeepSeek chính",
                                              "keys": [_fake_key()], "models": ["deepseek-chat"]})
    assert r.status_code == 201, r.text
    pid = r.json()["id"]
    rows = (await db.execute(text("""SELECT action, target_label FROM ops.action_log
                                     WHERE action = 'provider.created' AND target_id = :t"""),
                             {"t": pid})).all()
    assert [(x.action, x.target_label) for x in rows] == [("provider.created", "DeepSeek chính")]
