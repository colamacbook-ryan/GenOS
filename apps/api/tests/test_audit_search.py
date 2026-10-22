"""Nhật ký hành động (F099, F105): ô tìm chạy ở máy chủ trên TOÀN BỘ nhật ký, phân trang bằng cursor, ẩn dòng "ồn"
(http.*, kiểm chuỗi băm bình thường) theo yêu cầu, dòng http.* chung có nhãn đối tượng đọc được."""

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

from sqlalchemy import text

from gh.chassis import actionlog
from gh.db import admin_sessionmaker
from gh.middleware import ActionLogGuard
from tests.conftest import Api
from tests.test_rbac_api import login_as


async def _org(db) -> uuid.UUID:  # type: ignore[no-untyped-def]
    return (await db.execute(text("SELECT id FROM core.organizations LIMIT 1"))).scalar_one()


async def _add(db, org: uuid.UUID, action: str, *, actor_type: str = "system", actor_id: str = "system:worker",
               target_label: str | None = None, result: str = "ok") -> None:  # type: ignore[no-untyped-def]
    await actionlog.record(db, org_id=org, actor_type=actor_type, actor_id=actor_id, action=action,
                           target_label=target_label, result=result)


async def _items(api: Api, qs: str) -> list[dict[str, Any]]:
    r = await api.get(f"/audit?{qs}")
    assert r.status_code == 200, r.text
    return r.json()["items"]


async def test_search_reaches_rows_beyond_first_page(owner_api, db) -> None:  # type: ignore[no-untyped-def]
    """F099: dòng cũ (ngoài 100 dòng mới nhất) vẫn tìm được vì ô tìm chạy trên máy chủ."""
    api: Api = owner_api
    org = await _org(db)
    await _add(db, org, "provider.model_set", target_label="Mô hình cổ xưa")
    for i in range(130):
        await _add(db, org, "test.filler", target_label=f"đệm {i}")
    await db.commit()
    first = await _items(api, "limit=100")
    assert not any(i["target_label"] == "Mô hình cổ xưa" for i in first)
    found = await _items(api, "q=cổ xưa")
    assert [i["target_label"] for i in found] == ["Mô hình cổ xưa"]


async def test_search_matches_actor_action_and_target_and_escapes_wildcards(owner_api, db) -> None:  # type: ignore[no-untyped-def]
    api: Api = owner_api
    org = await _org(db)
    await _add(db, org, "rule.updated", target_label="Quy tắc 100% khách")
    await _add(db, org, "rule.created", target_label="Quy tắc khác")
    await db.commit()
    # tên người (display_name của Owner ghi ở bước thiết lập)
    assert any(i["actor_label"] == "Anh Cơ" for i in await _items(api, "q=Anh Cơ"))
    # mã hành động (không phân hoa/thường)
    assert {i["action"] for i in await _items(api, "q=RULE.UPD")} == {"rule.updated"}
    # ký tự % người dùng gõ là chữ thường, không phải ký tự đại diện
    assert [i["target_label"] for i in await _items(api, "q=100%25")] == ["Quy tắc 100% khách"]
    only_percent = await _items(api, "q=%25")
    assert only_percent and all("%" in (i["target_label"] or "") for i in only_percent)


async def test_search_accepts_codes_for_vietnamese_labels(owner_api, db) -> None:  # type: ignore[no-untyped-def]
    """Máy chủ chỉ lưu mã: web gõ "bộ nhận tin" → gửi kèm mã actor, "đăng nhập" → mã hành động."""
    api: Api = owner_api
    org = await _org(db)
    await _add(db, org, "message.sent", actor_id="bridge:zalo")
    await _add(db, org, "auth.login", actor_type="user", actor_id="user:" + str(uuid.uuid4()))
    await _add(db, org, "auth.logout", actor_type="user", actor_id="user:" + str(uuid.uuid4()))
    await db.commit()
    r = await _items(api, "q=Bộ nhận tin&q_actors=bridge:zalo")
    assert [i["actor_id"] for i in r] == ["bridge:zalo"]
    r = await _items(api, "q=đăng nhập&q_actions=auth.login,auth.login_failed")
    assert {i["action"] for i in r} == {"auth.login"}


async def test_hide_noise_hides_http_and_ok_chain_checks_only(owner_api, db) -> None:  # type: ignore[no-untyped-def]
    api: Api = owner_api
    org = await _org(db)
    await _add(db, org, "http.post", actor_type="user", actor_id="user:x")
    await _add(db, org, "audit.chain_verified")
    await _add(db, org, "audit.chain_verified", result="failed")
    await _add(db, org, "provider.model_set", actor_type="user", actor_id="user:x")
    await db.commit()
    shown = await _items(api, "hide_noise=true&limit=200")
    pairs = {(i["action"], i["result"]) for i in shown}
    assert ("http.post", "ok") not in pairs and ("audit.chain_verified", "ok") not in pairs
    assert ("audit.chain_verified", "failed") in pairs      # chuỗi đứt luôn hiện
    assert ("provider.model_set", "ok") in pairs
    everything = {i["action"] for i in await _items(api, "limit=200")}
    assert {"http.post", "audit.chain_verified"} <= everything     # mặc định API vẫn trả đủ


async def test_cursor_pagination_walks_whole_log_with_filters(owner_api, db) -> None:  # type: ignore[no-untyped-def]
    api: Api = owner_api
    org = await _org(db)
    now = datetime.now(UTC)
    for i in range(7):
        await _add(db, org, "test.page", target_label=f"dòng {i}")
    await db.commit()
    seen: list[str] = []
    cursor = None
    for _ in range(10):
        qs = "q=dòng&limit=3" + (f"&cursor={cursor}" if cursor else "")
        r = (await api.get(f"/audit?{qs}")).json()
        seen += [i["target_label"] for i in r["items"]]
        cursor = r["next_cursor"]
        if not cursor:
            break
    assert sorted(seen) == [f"dòng {i}" for i in range(7)]
    # lọc ngày: since trong tương lai ⇒ rỗng; until trong tương lai ⇒ đủ
    later = (now + timedelta(days=1)).isoformat().replace("+00:00", "Z")
    assert await _items(api, f"q=dòng&since={later}") == []
    assert len(await _items(api, f"q=dòng&until={later}")) == 7


async def test_team_scope_search_still_limited_to_team(owner_api, client, db) -> None:  # type: ignore[no-untyped-def]
    """Manager (audit.read = team) tìm vẫn chỉ thấy dòng của team mình — ô tìm không mở rộng phạm vi."""
    org = await _org(db)
    await _add(db, org, "rule.updated", actor_type="user", actor_id="user:" + str(uuid.uuid4()),
               target_label="Bí mật team khác")
    await db.commit()
    manager = await login_as(client, db, "manager")
    r = await manager.get("/audit?q=Bí mật")
    assert r.status_code == 200 and r.json()["items"] == []


def test_every_logged_action_has_a_vietnamese_label() -> None:
    """Lưới chống tái phát F105: mã hành động nào mã máy chủ ghi vào Nhật ký cũng phải có câu tiếng Việt trong
    `auditLabels.ts` — thêm hành động mới mà quên nhãn thì test này đỏ (Sếp không phải đọc mã máy)."""
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    labels = (root.parent / "web/src/screens/system/auditLabels.ts").read_text(encoding="utf-8")
    known = set(re.findall(r"^\s+'([a-z_.0-9]+)':", labels, re.M))
    assert len(known) > 150, "không đọc được bảng ACTION_LABEL"
    lit = re.compile(r'"([a-z][a-z_]*(?:\.[a-z_]+)+)"')
    found: dict[str, str] = {}
    for p in (root / "gh").rglob("*.py"):
        text_ = p.read_text(encoding="utf-8")
        for m in re.finditer(r"(?:\baction\s*=(?!=)|\b_log\(|^(?:JOB|ACTION)\s*=)([^\n]*)", text_, re.M):
            for code in lit.findall(m.group(1)):
                found.setdefault(code, p.name)
        for m in re.finditer(r'\{"approve":[^\n]*|action\s*=[^\n]*\n\s*(?:else|if)[^\n]*', text_):
            for code in lit.findall(m.group(0)):
                found.setdefault(code, p.name)
    assert len(found) > 150, "quét không ra mã hành động — sửa bộ quét"
    missing = {c: f for c, f in found.items() if c not in known}
    assert not missing, f"Hành động chưa có nhãn tiếng Việt trong auditLabels.ts: {missing}"


async def test_generic_http_row_has_readable_target_label(owner_api, db) -> None:  # type: ignore[no-untyped-def]
    """F105: dòng chung http.* do ActionLogGuard ghi có nhãn đối tượng "POST /đường-dẫn" (không còn trống)."""
    org = await _org(db)

    async def ok_app(scope, receive, send):  # type: ignore[no-untyped-def]
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    async def receive():  # type: ignore[no-untyped-def]
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(_m):  # type: ignore[no-untyped-def]
        return None

    user = SimpleNamespace(org_id=org, actor_id="user:" + str(uuid.uuid4()), ip=None)
    scope = {"type": "http", "method": "POST", "path": "/api/v1/vi-du/ghi", "state": {"user": user}}
    await ActionLogGuard(ok_app)(scope, receive, send)
    async with admin_sessionmaker()() as adm:
        row = (await adm.execute(text("SELECT target_type, target_id, target_label FROM ops.action_log "
                                      "WHERE action = 'http.post'"))).one()
    assert row.target_type == "route" and row.target_id == "/api/v1/vi-du/ghi"
    assert row.target_label == "POST /api/v1/vi-du/ghi"
