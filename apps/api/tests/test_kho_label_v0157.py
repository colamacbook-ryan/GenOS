"""v0.1.57 (Nợ #30) — Owner tự đặt "Tên Kho" (khoá `kho_label` trong settings của tổ chức).

(a) mặc định: GET /hub/link trả `kho_label` = "Kho dữ liệu", chưa đổi;
(b) đặt tên: PATCH /hub/link {kho_label} (Owner + PIN) ⇒ GET trả tên mới, nhãn nguồn Kho / ghi chú máy chủ / mục "Về mặc
    định" / prompt Gen / tool / đề xuất / nhãn PIN / chuỗi coach đều dùng tên mới; "Kho dữ liệu thô" không bị đổi;
(c) nhân viên (vai trò không phải Owner) bị 403, không đổi được; tên > 40 ký tự bị 422 (câu thân thiện);
(d) Về mặc định xoá khoá, địa chỉ + token Gen-hub giữ nguyên.
"""

import uuid
from typing import Any

import httpx
from sqlalchemy import text

from gh.db import admin_sessionmaker
from gh.gen import engine, kho_release, proposals
from gh.gen.coach import routes as coach_routes
from gh.hub_link import KHO_LABEL, KHO_LABEL_MAX, clean_kho_label, kho_label, relabel
from gh.hub_link import service as hub
from tests.conftest import Api, verify_pin
from tests.test_gen import _user_of
from tests.test_hub_link import ENDPOINT, TOKEN, FakeHub, _configure, _db_text, _linked
from tests.test_hub_link import fake_hub as fake_hub  # noqa: F401  (fixture)
from tests.test_rbac_api import login_as

NAME = "Sổ tay Công ty"


def _inp() -> engine.TurnInput:
    return engine.TurnInput(turn_id=uuid.uuid4(), conversation_id=uuid.uuid4(), text="x", route="/overview",
                            screen_key="overview")


async def _settings_label() -> Any:
    async with admin_sessionmaker()() as db:
        return (await db.execute(text("SELECT settings->>'kho_label' FROM core.organizations LIMIT 1"))).scalar_one()


# ─── (a) mặc định ─────────────────────────────────────────────────────────────

async def test_default_label_is_generic_and_unchanged(owner_api: Api, fake_hub: FakeHub) -> None:  # noqa: F811
    body = (await owner_api.get("/hub/link")).json()
    assert body["kho_label"] == KHO_LABEL == "Kho dữ liệu"
    assert body["kho_label_custom"] is False and body["kho_label_default"] == KHO_LABEL
    assert body["kho_label_max"] == KHO_LABEL_MAX == 40
    assert await _settings_label() is None                                   # chưa đặt ⇒ không có khoá
    await _configure(owner_api)
    assert hub.server_note() in await _db_text("SELECT note FROM agent.mcp_servers WHERE name = 'Gen-hub'")
    d = {r["key"]: r for r in (await owner_api.get("/defaults")).json()["items"]}["kho_label"]
    assert d["customized"] is False and d["resettable"] is True and KHO_LABEL in d["current_text"]


def test_kho_label_function_and_relabel_are_pure_and_safe() -> None:
    assert kho_label(None) == kho_label({}) == kho_label({"kho_label": ""}) == KHO_LABEL
    assert kho_label({"kho_label": f"  {NAME}  "}) == NAME
    assert kho_label({"kho_label": "x" * (KHO_LABEL_MAX + 1)}) == KHO_LABEL     # giá trị hỏng ⇒ mặc định
    assert kho_label({"kho_label": 123}) == KHO_LABEL
    assert clean_kho_label("a\n\t b\x00  c") == "a b c"
    assert relabel(f"Ghi vào {KHO_LABEL} (tạo mới)", NAME) == f"Ghi vào {NAME} (tạo mới)"
    assert relabel("Kho dữ liệu thô và Kho dữ liệu", NAME) == f"Kho dữ liệu thô và {NAME}"  # tầng thô giữ nguyên
    assert relabel(f"Ghi {KHO_LABEL}", KHO_LABEL) == f"Ghi {KHO_LABEL}"


# ─── (b) đặt tên ──────────────────────────────────────────────────────────────

async def test_set_label_flows_into_runtime_strings(owner_api: Api, fake_hub: FakeHub, app: Any) -> None:  # noqa: F811
    await _linked(owner_api)
    assert (await owner_api.send("PATCH", "/hub/link", {"kho_label": f"  {NAME}  "})).status_code == 200
    got = (await owner_api.get("/hub/link")).json()
    assert got["kho_label"] == NAME and got["kho_label_custom"] is True
    assert await _settings_label() == NAME
    # Chỉ đổi tên Kho thì liên kết nguyên vẹn (vẫn bật, vẫn có token).
    assert got["configured"] is True and got["enabled"] is True and got["has_token"] is True
    # Ghi chú máy chủ do hệ thống tự đặt đổi theo tên mới.
    assert hub.server_note(NAME) in await _db_text("SELECT note FROM agent.mcp_servers WHERE name = 'Gen-hub'")
    # Nhãn nguồn của kết quả đọc Kho.
    r = (await owner_api.get("/hub/kho/summary")).json()
    assert r["source"] == f"{NAME} qua Gen-hub"
    assert hub.source_of("kho_get") == "Kho dữ liệu qua Gen-hub"             # mặc định không đổi
    assert hub.source_of("kho_get", NAME) == f"{NAME} qua Gen-hub"
    assert hub.source_of("gmail_search", NAME) == "Gmail qua Gen-hub"
    # Mục "Về mặc định": Đã đổi.
    d = {x["key"]: x for x in (await owner_api.get("/defaults")).json()["items"]}["kho_label"]
    assert d["customized"] is True and NAME in d["current_text"] and KHO_LABEL in d["default_text"]
    # Action Log có dòng đổi tên (không chứa token).
    log = await _db_text("SELECT action, detail FROM ops.action_log WHERE action = 'hub.kho_label_changed'")
    assert NAME in log and TOKEN not in log


async def test_set_label_before_first_link_does_not_touch_link_and_names_new_note(
        owner_api: Api, fake_hub: FakeHub) -> None:  # noqa: F811
    await verify_pin(owner_api)
    r = await owner_api.send("PATCH", "/hub/link", {"kho_label": NAME})       # chưa nối Gen-hub: vẫn lưu được tên
    assert r.status_code == 200, r.text
    assert r.json()["configured"] is False and r.json()["kho_label"] == NAME
    await _configure(owner_api)                                               # lần nối đầu lưu ghi chú theo tên mới
    assert hub.server_note(NAME) in await _db_text("SELECT note FROM agent.mcp_servers WHERE name = 'Gen-hub'")


async def test_runtime_prompts_tools_proposals_and_coach_use_label(owner_api: Api, app: Any, db: Any) -> None:
    await verify_pin(owner_api)
    assert (await owner_api.send("PATCH", "/hub/link", {"kho_label": NAME})).status_code == 200
    owner, _tok = await _user_of(owner_api)
    # prompt của Gen + mô tả tool + mục tiêu
    prompt = engine.system_prompt(owner, _inp(), [], kho=NAME)
    assert f"{NAME} (tool hub.kho_*)" in prompt and f'"{NAME} qua Gen-hub"' in prompt
    assert f"Tìm trong {NAME} theo từ khoá" in prompt and "Tìm trong Kho dữ liệu theo" not in prompt
    assert "Kho dữ liệu (tool hub.kho_*)" in engine.system_prompt(owner, _inp(), [])           # mặc định như cũ
    # đề xuất ghi Kho: tóm tắt + câu chặn
    fields = {"bang": "Phiên", "record": {"Chủ đề": "Họp"}, "ma": None}
    assert f"của {NAME}:" in proposals.summary("kho_create", fields, {"bang": "Phiên"}, proposals.ZoneInfo("UTC"), NAME)
    assert proposals.permission_error({"system.manage": "all"}, "kho_create", "x", "manager", NAME) \
        == f"chỉ Owner được ghi vào {NAME}"
    assert await proposals.kho_for(db, owner.org_id, "kho_create") == NAME
    rel = proposals.build_release(owner.org_id, owner.id, "v1.2.3", turn_id=uuid.uuid4(), conversation_id=uuid.uuid4(),
                                  tz=proposals.ZoneInfo("UTC"), repo="o/r", kho=NAME)
    assert rel["labels"]["kho"] == NAME and f"của {NAME}:" in rel["summary"]       # web đọc tên Kho từ nhãn thẻ
    rel0 = proposals.build_release(owner.org_id, owner.id, "v1.2.3", turn_id=uuid.uuid4(), conversation_id=uuid.uuid4(),
                                   tz=proposals.ZoneInfo("UTC"), repo="o/r")
    assert "kho" not in rel0["labels"] and f"của {KHO_LABEL}:" in rel0["summary"]   # mặc định: thẻ như cũ
    assert await proposals.kho_for(db, owner.org_id, "reminder") == KHO_LABEL
    # job đề xuất theo bản mới + nhãn quyền PIN
    assert NAME in relabel(kho_release.SAY.format(version="v1", addr="Sếp"), NAME)
    assert NAME in relabel(kho_release.BELL_BODY.format(version="v1"), NAME)
    # coach: bài học / mẹo dựng theo tên Kho
    tips, curr = coach_routes.load_content(NAME)
    text_all = " ".join(str(x.get("title", "")) + str(x.get("body", "")) for x in [*tips, *curr])
    assert NAME in text_all and f"Ghi vào {KHO_LABEL}" not in text_all
    assert (await owner_api.get("/gen/coach/curriculum")).json()["lessons"]
    cur = (await owner_api.get("/gen/coach/curriculum")).json()["lessons"]
    assert any(NAME in (x["title"] + x["body"]) for x in cur)
    # ranh giới (system_api): nhãn nhắc tên Kho
    labels = [b["label"] for b in (await owner_api.get("/boundaries")).json()]
    assert any(f"Ghi vào {NAME}" in x for x in labels)


# ─── (c) nhân viên 403 + kiểm hợp lệ ──────────────────────────────────────────

async def test_staff_cannot_set_label_and_too_long_is_rejected(owner_api: Api, fake_hub: FakeHub,  # noqa: F811
                                                                client: httpx.AsyncClient, db: Any) -> None:
    await _linked(owner_api)
    for role in ("manager", "auditor"):
        staff = await login_as(client, db, role)
        assert (await staff.send("PATCH", "/hub/link", {"kho_label": "Của tôi"})).status_code == 403
        if role == "auditor":                                                         # có system.read ⇒ đọc được tên
            assert (await staff.get("/hub/link")).json()["kho_label"] == KHO_LABEL
        assert (await staff.send("POST", "/defaults/kho_label/reset", {"confirm": True})).status_code == 403
    assert await _settings_label() is None
    await verify_pin(owner_api)
    r = await owner_api.send("PATCH", "/hub/link", {"kho_label": "x" * (KHO_LABEL_MAX + 1)})
    assert r.status_code == 422 and "40" in r.text and "kho_label" in r.text
    assert await _settings_label() is None
    ok = await owner_api.send("PATCH", "/hub/link", {"kho_label": "x" * KHO_LABEL_MAX})
    assert ok.status_code == 200 and ok.json()["kho_label"] == "x" * KHO_LABEL_MAX


async def test_set_label_requires_owner_pin(owner_api: Api, fake_hub: FakeHub) -> None:  # noqa: F811
    r = await owner_api.send("PATCH", "/hub/link", {"kho_label": NAME})
    assert r.status_code == 423                                              # chưa có phiên PIN
    assert await _settings_label() is None


# ─── (d) Về mặc định ──────────────────────────────────────────────────────────

async def test_reset_to_default_clears_label_and_keeps_hub_link(owner_api: Api, fake_hub: FakeHub) -> None:  # noqa: F811
    await _linked(owner_api)
    assert (await owner_api.send("PATCH", "/hub/link", {"kho_label": NAME})).status_code == 200
    r = await owner_api.send("POST", "/defaults/kho_label/reset", {"confirm": True})
    assert r.status_code == 200, r.text
    assert r.json()["customized"] is False
    assert await _settings_label() is None
    got = (await owner_api.get("/hub/link")).json()
    assert got["kho_label"] == KHO_LABEL and got["kho_label_custom"] is False
    assert got["configured"] is True and got["enabled"] is True and got["endpoint"] == ENDPOINT
    assert hub.server_note() in await _db_text("SELECT note FROM agent.mcp_servers WHERE name = 'Gen-hub'")
    # Đặt rỗng cũng về mặc định (ô nhập để trống rồi Lưu).
    assert (await owner_api.send("PATCH", "/hub/link", {"kho_label": NAME})).status_code == 200
    assert (await owner_api.send("PATCH", "/hub/link", {"kho_label": "   "})).status_code == 200
    assert await _settings_label() is None
