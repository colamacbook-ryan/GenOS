"""v0.1.50 (F-87, QD-18) — Gen đề xuất ghi một Phiên vào Kho dữ liệu mỗi khi máy chủ lên bản mới (cron
`gen_kho_release`).

Mỗi (tổ chức, phiên bản) ĐÚNG MỘT lần (bảng agent.hub_release_proposals); chỉ khi Gen-hub đã cấp quyền ghi Kho; job chỉ
ĐỀ XUẤT (0 lời gọi ghi) — ghi khi Owner bấm Xác nhận + PIN; Owner thứ hai xác nhận cùng bản → 409 và thẻ của Owner khác
được đóng khi bản đã ghi / đã huỷ / ghi chưa chắc (502 HUB_WRITE_UNCERTAIN không trả bản về 'pending'). Migration 0032
chạy lại an toàn."""

import asyncio
import uuid
from types import SimpleNamespace
from typing import Any

import orjson
import psycopg
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

import gh
from gh.crypto import hash_secret
from gh.db import admin_sessionmaker, sessionmaker
from gh.errors import ApiError
from gh.gen import kho_release, proposals
from gh.gen import routes as gen_routes
from gh.hub_link import kho_write
from gh.worker import JOB_LABELS, WorkerSettings
from tests.conftest import PG, Api
from tests.phase2 import org_id
from tests.test_gen_proposals import _log
from tests.test_hub_kho_write_v0150 import PREFIX, FakeHub, _linked, _pin, fake_hub  # noqa: F401
from tests.test_rbac_api import PASSWORD
from tests.test_rls import _as_low_priv

REPO = "Genesis-ryan-84-0567536339/Gen-Harness"
TITLE = "Gen đề xuất ghi Kho · Phiên {v}"


async def _run(redis: Any, now: Any = None) -> dict[str, Any]:
    return await kho_release.run(sessionmaker(), redis, now)


async def _rows() -> list[Any]:
    async with admin_sessionmaker()() as db:
        return (await db.execute(text("""SELECT org_id, version, status, proposal_ids, kho_ma, created_at, decided_at,
                                                decided_by, uncertain_by FROM agent.hub_release_proposals
                                         ORDER BY version"""))).all()


async def _count(sql: str) -> int:
    async with admin_sessionmaker()() as db:
        return int((await db.execute(text(sql))).scalar_one())


async def _release_proposal(api: Api, version: str = "v0.1.50") -> dict[str, Any]:
    """Thẻ đề xuất của hội thoại 'Gen đề xuất ghi Kho · Phiên {version}' của chính người đang đăng nhập."""
    convs = [c for c in (await api.get("/gen/conversations")).json() if c["title"] == TITLE.format(v=version)]
    assert len(convs) == 1, convs
    msgs = (await api.get(f"/gen/conversations/{convs[0]['id']}/messages")).json()
    assert len(msgs) == 1 and msgs[0]["role"] == "assistant"
    steps = msgs[0]["content"]["steps"]
    assert [s["kind"] for s in steps] == ["say", "proposal"]
    p: dict[str, Any] = steps[1]["proposal"]
    return p


async def _clear_breaker(redis: Any) -> None:
    async for k in redis.scan_iter(match="gh:hub:brk:*"):
        await redis.delete(k)


async def _b_pin(b: Api) -> None:
    assert (await b.send("POST", "/auth/pin/verify", {"pin": "112233"})).status_code == 200


async def _add_owner(db: Any, email: str = "owner2@example.vn") -> Api:
    org = await org_id(db)
    uid = (await db.execute(text("""INSERT INTO core.users (org_id, email, display_name, password_hash, pin_hash)
                                    VALUES (:o, :e, 'Owner hai', :p, :pin) RETURNING id"""),
                            {"o": org, "e": email, "p": hash_secret(PASSWORD), "pin": hash_secret("112233")})
                           ).scalar_one()
    await db.execute(text("""INSERT INTO core.user_roles (user_id, role_id)
                             SELECT :u, id FROM core.roles WHERE code = 'owner'"""), {"u": uid})
    await db.commit()
    return uid  # type: ignore[no-any-return]


async def _login(client: Any, email: str) -> Api:
    api = Api(client.__class__(transport=client._transport, base_url="http://test"))
    r = await api.send("POST", "/auth/login", {"email": email, "password": PASSWORD})
    assert r.status_code == 200, r.text
    return api


# ─── 1. Mỗi (tổ chức, phiên bản) đúng một lần ────────────────────────────────────────────────────────────────────

async def test_job_proposes_once_per_version(owner_api: Api, fake_hub: FakeHub, db: Any, redis: Any,  # noqa: F811
                                             monkeypatch: pytest.MonkeyPatch) -> None:
    await _linked(owner_api)
    monkeypatch.setattr(gh, "__version__", "v0.1.50")
    fake_hub.calls.clear()
    first = await _run(redis)
    org = str(await org_id(db))
    assert first["version"] == "v0.1.50" and first[org] == "proposed"
    second = await _run(redis)
    assert second[org] == "already_proposed"
    rows = await _rows()
    assert len(rows) == 1 and rows[0].version == "v0.1.50" and rows[0].status == "pending"
    assert len(rows[0].proposal_ids) == 1 and rows[0].kho_ma is None and rows[0].decided_at is None
    # Đúng 1 hội thoại + 1 đề xuất + 1 chuông cho Owner; 0 lời gọi ghi (job không gọi Gen-hub).
    p = await _release_proposal(owner_api)
    assert fake_hub.calls == []
    assert await _count("SELECT count(*) FROM agent.gen_conversations WHERE title LIKE 'Gen đề xuất ghi Kho%'") == 1
    bells = (await owner_api.get("/notifications")).json()["items"]
    bell = [b for b in bells if b["kind"] == "gen.kho_proposal"]
    assert len(bell) == 1 and bell[0]["title"] == TITLE.format(v="v0.1.50")
    convs = (await owner_api.get("/gen/conversations")).json()
    cid = next(c["id"] for c in convs if c["title"] == TITLE.format(v="v0.1.50"))
    assert bell[0]["link"] == f"/overview?gen={cid}"
    # Thẻ đề xuất do HỆ THỐNG dựng.
    assert p["id"] == str(rows[0].proposal_ids[0]) and p["type"] == "kho_create" and p["status"] == "pending"
    assert set(p) == {"id", "type", "fields", "summary", "labels", "target", "requires_pin", "status"}
    today = kho_write.vn_today().isoformat()
    assert p["fields"] == {"bang": "Phiên", "record": {
        "Chủ đề": "Gen-Harness lên bản v0.1.50", "Ngày": today,
        "Đã chốt": ("Máy chủ Gen-Harness đã nâng lên v0.1.50. Ghi chú phát hành: "
                    f"https://github.com/{REPO}/releases/tag/v0.1.50")}}
    assert p["requires_pin"] is True and p["target"] == "hub.kho_write:Phiên"
    # `release` = phiên bản: thẻ nói rõ mỗi bản ghi MỘT lần cho cả tổ chức (Huỷ = huỷ cho mọi Owner).
    assert p["labels"] == {"bang": "Phiên", "target": "Tạo mới ở bảng Phiên", "write_scope": "ok", "release": "v0.1.50"}
    assert p["summary"].endswith("Chỉ ghi khi Sếp bấm Xác nhận và nhập mã PIN (qua Gen-hub).")
    # Redis: sống 7 ngày, mang khoá meta release_version (không thuộc fields).
    stored = await proposals.load(redis, p["id"])
    assert stored is not None and stored["release_version"] == "v0.1.50" and "release_version" not in stored["fields"]
    assert 6 * 86400 < await redis.ttl(proposals.key(p["id"])) <= 7 * 86400
    # Action Log hệ thống.
    log = await _log("gen.kho_release_proposed")
    assert len(log) == 1 and log[0].actor_type == "system" and log[0].detail["version"] == "v0.1.50"
    assert log[0].detail["proposal_ids"] == [p["id"]] and log[0].detail["owners"] == 1
    # Phiên bản mới ⇒ thêm đúng một lần nữa.
    monkeypatch.setattr(gh, "__version__", "v0.1.51")
    assert (await _run(redis))[org] == "proposed"
    assert [r.version for r in await _rows()] == ["v0.1.50", "v0.1.51"]
    p51 = await _release_proposal(owner_api, "v0.1.51")
    assert p51["fields"]["record"]["Chủ đề"] == "Gen-Harness lên bản v0.1.51"
    assert fake_hub.writes() == []


async def test_dev_and_prerelease_versions_are_skipped(owner_api: Api, fake_hub: FakeHub, redis: Any,  # noqa: F811
                                                       monkeypatch: pytest.MonkeyPatch) -> None:
    await _linked(owner_api)
    for raw in ("0.0.0-dev", "dev", "0.1.50-rc1", "", "v0.1"):
        monkeypatch.setattr(gh, "__version__", raw)
        out = await _run(redis)
        assert out["skipped"] == "dev_version" and out["version"] is None, raw
    assert await _rows() == [] and fake_hub.writes() == []
    # Không có 'v' ở đầu vẫn chuẩn hoá thành vX.Y.Z.
    monkeypatch.setattr(gh, "__version__", "0.1.50")
    await _run(redis)
    assert [r.version for r in await _rows()] == ["v0.1.50"]
    assert kho_release.version_of("1.2.3") == "v1.2.3" and kho_release.version_of("v10.20.30") == "v10.20.30"


async def test_not_eligible_inserts_nothing_then_retries(owner_api: Api, fake_hub: FakeHub, db: Any,  # noqa: F811
                                                         redis: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gh, "__version__", "v0.1.50")
    org = str(await org_id(db))
    assert (await _run(redis))[org] == "hub_off"                       # chưa nối Gen-hub
    fake_hub.drop = {"kho_create"}
    await _linked(owner_api)                                           # nối, có kho_update nhưng chưa có kho_create
    assert (await _run(redis))[org] == "no_write_scope" and await _rows() == []
    # Sếp chỉ tick kho_create rồi bấm Kiểm tra: thẻ ghi Phiên chỉ dùng kho_create ⇒ đủ (thiếu kho_update không chặn),
    # khớp dòng "Tạo bản ghi Phiên/Việc: Có" ở Kết nối và nhãn write_scope của thẻ (theo đúng tool).
    fake_hub.drop = {"kho_update"}
    ws = (await owner_api.send("POST", "/hub/link/test", {})).json()["write_scopes"]
    assert ws == {"kho": False, "kho_create": True, "kho_update": False}
    assert (await _run(redis))[org] == "proposed" and len(await _rows()) == 1
    # Gen tắt → không đề xuất cho bản kế.
    await db.execute(text("""UPDATE core.organizations SET settings = jsonb_set(COALESCE(settings, '{}'::jsonb),
        '{gen}', COALESCE(settings->'gen', '{}'::jsonb) || '{"enabled": false}'::jsonb)"""))
    await db.commit()
    monkeypatch.setattr(gh, "__version__", "v0.1.51")
    assert (await _run(redis))[org] == "gen_off" and len(await _rows()) == 1
    assert fake_hub.writes() == []


async def test_concurrent_runs_propose_once(owner_api: Api, fake_hub: FakeHub, redis: Any,  # noqa: F811
                                            monkeypatch: pytest.MonkeyPatch) -> None:
    await _linked(owner_api)
    monkeypatch.setattr(gh, "__version__", "v0.1.50")
    outs = await asyncio.gather(_run(redis), _run(redis))
    kinds_ = sorted(next(v for k, v in o.items() if k not in ("version", "expired", "reset")) for o in outs)
    assert kinds_ == ["already_proposed", "proposed"]
    assert len(await _rows()) == 1
    assert await _count("SELECT count(*) FROM agent.gen_conversations WHERE title LIKE 'Gen đề xuất ghi Kho%'") == 1
    assert await _count("SELECT count(*) FROM core.notifications WHERE kind = 'gen.kho_proposal'") == 1


# ─── 2. Xác nhận + PIN → đúng MỘT lời gọi; Owner thứ hai → 409 ───────────────────────────────────────────────────

async def test_confirm_writes_phien_and_marks_written(owner_api: Api, fake_hub: FakeHub, db: Any,  # noqa: F811
                                                      redis: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    await _linked(owner_api)
    monkeypatch.setattr(gh, "__version__", "v0.1.50")
    await _run(redis)
    p = await _release_proposal(owner_api)
    fake_hub.calls.clear()
    await _pin(owner_api)
    r = await owner_api.send("POST", f"/gen/proposals/{p['id']}/confirm", {})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "confirmed" and r.json()["result"]["code"] == "PHIEN-12"
    assert fake_hub.calls == [PREFIX + "kho_create"]
    assert fake_hub.args[-1] == {"bang": "Phiên", "fields": p["fields"]["record"]}
    row = (await _rows())[0]
    assert row.status == "written" and row.kho_ma == "PHIEN-12" and row.decided_at is not None
    me = (await owner_api.get("/auth/me")).json()
    assert str(row.decided_by) == me["id"]
    conf = await _log("gen.proposal_confirmed")
    assert len(conf) == 1 and conf[0].detail["release_version"] == "v0.1.50"
    assert len(await _log("hub.kho_written")) == 1
    # Job chạy lại không dựng lại bản đã ghi; xác nhận lại → 409.
    assert (await _run(redis))[str(await org_id(db))] == "already_proposed"
    again = await owner_api.send("POST", f"/gen/proposals/{p['id']}/confirm", {})
    assert again.status_code == 409 and again.json()["code"] == "GEN_PROPOSAL_DECIDED"
    assert fake_hub.writes() == [PREFIX + "kho_create"]


async def test_second_owner_cannot_write_same_version(owner_api: Api, fake_hub: FakeHub, db: Any,  # noqa: F811
                                                      client: Any, redis: Any,
                                                      monkeypatch: pytest.MonkeyPatch) -> None:
    await _linked(owner_api)
    await _add_owner(db)
    monkeypatch.setattr(gh, "__version__", "v0.1.50")
    await _run(redis)
    assert await _count("SELECT count(*) FROM agent.gen_conversations WHERE title LIKE 'Gen đề xuất ghi Kho%'") == 2
    assert await _count("SELECT count(*) FROM core.notifications WHERE kind = 'gen.kho_proposal'") == 2
    row = (await _rows())[0]
    assert len(row.proposal_ids) == 2 and (await _log("gen.kho_release_proposed"))[0].detail["owners"] == 2
    b = await _login(client, "owner2@example.vn")
    try:
        pa, pb = await _release_proposal(owner_api), await _release_proposal(b)
        assert pa["id"] != pb["id"] and {pa["id"], pb["id"]} == {str(x) for x in row.proposal_ids}
        fake_hub.calls.clear()
        await _pin(owner_api)
        assert (await owner_api.send("POST", f"/gen/proposals/{pa['id']}/confirm", {})).status_code == 200
        await _b_pin(b)
        # Thẻ của Owner hai được ĐÓNG ngay khi Owner một ghi xong (Redis + tin đã lưu + chuông đã đọc): mở chuông / tải
        # lại hội thoại thấy "Owner khác đã ghi", không còn nút Xác nhận chết.
        stored = await proposals.load(redis, pb["id"])
        assert stored["status"] == "cancelled"
        assert stored["labels"]["closed"] == "Owner khác đã ghi bản này vào Kho (PHIEN-12)"
        reloaded = await _release_proposal(b)
        assert reloaded["status"] == "cancelled" and reloaded["labels"]["closed"] == stored["labels"]["closed"]
        bell = [x for x in (await b.get("/notifications")).json()["items"] if x["kind"] == "gen.kho_proposal"]
        assert len(bell) == 1 and bell[0]["read"] is True
        mine = [x for x in (await owner_api.get("/notifications")).json()["items"] if x["kind"] == "gen.kho_proposal"]
        assert len(mine) == 1
        r = await b.send("POST", f"/gen/proposals/{pb['id']}/confirm", {})
        assert r.status_code == 409 and r.json()["code"] == "GEN_PROPOSAL_DECIDED"
        assert fake_hub.writes() == [PREFIX + "kho_create"]                              # vẫn đúng một lời gọi
        # Bấm Huỷ trên thẻ cũ (màn chưa tải lại) ⇒ 200 với trạng thái đã đóng — không 409, thẻ trên màn cập nhật theo.
        r = await b.send("POST", f"/gen/proposals/{pb['id']}/cancel", {})
        assert r.status_code == 200 and r.json()["status"] == "cancelled"
        # Đua: thẻ B vẫn 'pending' lúc bấm ⇒ _release_claim chặn 409 TRƯỚC Gen-hub và đóng thẻ đó.
        await redis.set(proposals.key(pb["id"]), orjson.dumps({**stored, "status": "pending"}))
        r = await b.send("POST", f"/gen/proposals/{pb['id']}/confirm", {})
        assert r.status_code == 409 and r.json()["code"] == "GEN_PROPOSAL_DECIDED"
        assert r.json()["title"] == gen_routes.RELEASE_DECIDED_MSG
        assert (await proposals.load(redis, pb["id"]))["status"] == "cancelled"
        assert fake_hub.writes() == [PREFIX + "kho_create"]
    finally:
        await b.c.aclose()
    assert (await _rows())[0].status == "written"


async def test_uncertain_write_stays_with_same_owner_and_can_retry(owner_api: Api, fake_hub: FakeHub,  # noqa: F811
                                                                    redis: Any,
                                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    await _linked(owner_api)
    monkeypatch.setattr(gh, "__version__", "v0.1.50")
    await _run(redis)
    p = await _release_proposal(owner_api)
    await _pin(owner_api)
    fake_hub.mode = "write_timeout"
    r = await owner_api.send("POST", f"/gen/proposals/{p['id']}/confirm", {})
    assert r.status_code == 502 and r.json()["code"] == "HUB_WRITE_UNCERTAIN"
    row = (await _rows())[0]
    # CÓ THỂ đã ghi ⇒ KHÔNG trả về 'pending' (Owner khác sẽ ghi trùng): 'uncertain', gắn đúng Owner vừa ghi.
    me = (await owner_api.get("/auth/me")).json()
    assert row.status == "uncertain" and str(row.uncertain_by) == me["id"]
    assert row.kho_ma is None and row.decided_at is None
    assert (await proposals.load(redis, p["id"]))["status"] == "pending"     # thẻ của chính Owner này vẫn bấm lại được
    fake_hub.mode = "ok"
    await _clear_breaker(redis)
    r = await owner_api.send("POST", f"/gen/proposals/{p['id']}/confirm", {})
    assert r.status_code == 200, r.text
    assert (await _rows())[0].status == "written" and (await _rows())[0].kho_ma == "PHIEN-12"
    assert len(fake_hub.writes()) == 2                                       # 1 lần timeout + 1 lần ghi được


async def test_uncertain_write_blocks_second_owner(owner_api: Api, fake_hub: FakeHub, db: Any,  # noqa: F811
                                                   client: Any, redis: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """Owner một nhận 502 HUB_WRITE_UNCERTAIN ⇒ Owner hai KHÔNG ghi được bản đó (409 GEN_PROPOSAL_DECIDED, 0 lời gọi):
    mỗi (tổ chức, bản) ghi Kho đúng MỘT lần. Chỉ Owner một bấm lại / huỷ sau khi mở Kho kiểm."""
    await _linked(owner_api)
    await _add_owner(db)
    monkeypatch.setattr(gh, "__version__", "v0.1.50")
    await _run(redis)
    b = await _login(client, "owner2@example.vn")
    try:
        pa, pb = await _release_proposal(owner_api), await _release_proposal(b)
        await _pin(owner_api)
        fake_hub.calls.clear()
        fake_hub.mode = "write_timeout"
        r = await owner_api.send("POST", f"/gen/proposals/{pa['id']}/confirm", {})
        assert r.status_code == 502 and r.json()["code"] == "HUB_WRITE_UNCERTAIN"
        assert (await _rows())[0].status == "uncertain"
        # Thẻ của Owner hai đóng ngay, nói rõ vì sao.
        stored = await proposals.load(redis, pb["id"])
        assert stored["status"] == "cancelled" and stored["labels"]["closed"] == gen_routes.RELEASE_CLOSED_UNCERTAIN
        fake_hub.mode = "ok"
        await _clear_breaker(redis)
        await _b_pin(b)
        r = await b.send("POST", f"/gen/proposals/{pb['id']}/confirm", {})
        assert r.status_code == 409 and r.json()["code"] == "GEN_PROPOSAL_DECIDED"
        # Đua: thẻ B còn 'pending' lúc bấm ⇒ vẫn 409 GEN_PROPOSAL_DECIDED từ dòng bảng (không phải BUSY), thẻ đóng.
        await redis.set(proposals.key(pb["id"]), orjson.dumps({**stored, "status": "pending"}))
        r = await b.send("POST", f"/gen/proposals/{pb['id']}/confirm", {})
        assert r.status_code == 409 and r.json()["code"] == "GEN_PROPOSAL_DECIDED"
        assert (await proposals.load(redis, pb["id"]))["status"] == "cancelled"
        assert fake_hub.writes() == [PREFIX + "kho_create"]                  # chỉ lần chưa chắc của Owner một
        # Owner hai bấm Huỷ không gỡ được lần ghi chưa chắc của Owner một.
        assert (await b.send("POST", f"/gen/proposals/{pb['id']}/cancel", {})).status_code == 200
        assert (await _rows())[0].status == "uncertain"
        # Owner một bấm lại, Kho từ chối CHẮC CHẮN ⇒ vẫn 'uncertain' (lần trước có thể đã ghi), không về 'pending'.
        fake_hub.mode = "write_error"
        r = await owner_api.send("POST", f"/gen/proposals/{pa['id']}/confirm", {})
        assert r.status_code == 409 and r.json()["code"] == "HUB_WRITE_REJECTED"
        assert (await _rows())[0].status == "uncertain"
        # Owner một mở Kho thấy đã có ⇒ Huỷ ⇒ bản 'cancelled' cho cả tổ chức.
        r = await owner_api.send("POST", f"/gen/proposals/{pa['id']}/cancel", {})
        assert r.status_code == 200 and r.json()["status"] == "cancelled"
        row = (await _rows())[0]
        me = (await owner_api.get("/auth/me")).json()
        assert row.status == "cancelled" and str(row.decided_by) == me["id"]
    finally:
        await b.c.aclose()
    assert len(fake_hub.writes()) == 2


async def test_definite_failure_returns_to_pending_for_everyone(owner_api: Api, fake_hub: FakeHub,  # noqa: F811
                                                                db: Any, client: Any, redis: Any,
                                                                monkeypatch: pytest.MonkeyPatch) -> None:
    """Lỗi CHẮC CHẮN chưa ghi (Kho báo isError) ⇒ về 'pending', thẻ Owner khác KHÔNG bị đóng: Owner hai ghi được."""
    await _linked(owner_api)
    await _add_owner(db)
    monkeypatch.setattr(gh, "__version__", "v0.1.50")
    await _run(redis)
    b = await _login(client, "owner2@example.vn")
    try:
        pa, pb = await _release_proposal(owner_api), await _release_proposal(b)
        await _pin(owner_api)
        fake_hub.mode = "write_error"
        r = await owner_api.send("POST", f"/gen/proposals/{pa['id']}/confirm", {})
        assert r.status_code == 409 and r.json()["code"] == "HUB_WRITE_REJECTED"
        row = (await _rows())[0]
        assert row.status == "pending" and row.uncertain_by is None
        assert (await proposals.load(redis, pb["id"]))["status"] == "pending"
        fake_hub.mode = "ok"
        await _b_pin(b)
        r = await b.send("POST", f"/gen/proposals/{pb['id']}/confirm", {})
        assert r.status_code == 200, r.text
        assert (await _rows())[0].status == "written"
        closed = await proposals.load(redis, pa["id"])
        assert closed["status"] == "cancelled" and closed["labels"]["closed"].startswith("Owner khác đã ghi")
    finally:
        await b.c.aclose()


class _RaceDb:
    """Giả đúng khe đua trong `_release_claim`: câu UPDATE trượt (dòng đang 'writing' — Owner khác đang ghi), rồi lần
    ghi đó kết thúc TRƯỚC câu SELECT nên SELECT thấy trạng thái mới."""

    def __init__(self, status: str, uncertain_by: Any = None) -> None:
        self.after = SimpleNamespace(status=status, uncertain_by=uncertain_by)
        self.n = 0

    async def execute(self, *_a: Any, **_k: Any) -> Any:
        self.n += 1
        row = None if self.n == 1 else self.after
        return SimpleNamespace(first=lambda: row)


@pytest.mark.parametrize(("status", "mine", "msg"), [
    ("pending", False, gen_routes.RELEASE_RETRY_MSG),   # lần ghi của Owner khác vừa lỗi CHẮC CHẮN ⇒ về 'pending'
    ("uncertain", True, gen_routes.RELEASE_RETRY_MSG),  # chính Owner này vừa ghi chưa chắc ⇒ chỉ Owner này bấm lại
    ("writing", False, gen_routes.RELEASE_BUSY_MSG),
])
async def test_release_claim_race_keeps_card_open(redis: Any, status: str, mine: bool, msg: str) -> None:
    """UPDATE trượt vì Owner khác đang ghi, rồi dòng về 'pending' (hoặc 'uncertain' của chính Owner này) trước câu
    SELECT ⇒ bản VẪN ghi được: 409 GEN_PROPOSAL_BUSY (bấm lại), thẻ KHÔNG bị đóng với lời "Owner khác đã huỷ"."""
    user = SimpleNamespace(org_id=uuid.uuid4(), id=uuid.uuid4())
    pid = str(uuid.uuid4())
    card = {"id": pid, "org_id": str(user.org_id), "status": "pending", "labels": {"release": "v0.1.50"}}
    await redis.set(proposals.key(pid), orjson.dumps(card), ex=60)
    db = _RaceDb(status, user.id if mine else uuid.uuid4())
    with pytest.raises(ApiError) as e:
        await gen_routes._release_claim(db, user, "v0.1.50", card, redis)  # type: ignore[arg-type]
    assert e.value.code == "GEN_PROPOSAL_BUSY" and e.value.title == msg
    stored = await proposals.load(redis, pid)
    assert stored is not None and stored["status"] == "pending" and "closed" not in stored["labels"]


async def test_release_say_uses_owner_addressing(owner_api: Api, fake_hub: FakeHub, db: Any,  # noqa: F811
                                                 redis: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    await _linked(owner_api)
    await db.execute(text("""UPDATE core.users SET addressing = addressing || '{"bot_calls_me": "Anh Cả"}'::jsonb"""))
    await db.commit()
    monkeypatch.setattr(gh, "__version__", "v0.1.50")
    await _run(redis)
    convs = [c for c in (await owner_api.get("/gen/conversations")).json() if c["title"] == TITLE.format(v="v0.1.50")]
    msgs = (await owner_api.get(f"/gen/conversations/{convs[0]['id']}/messages")).json()
    say = msgs[0]["content"]["steps"][0]["text"]
    assert "Anh Cả xem lại" in say and "Sếp xem lại" not in say


async def test_cancel_cancels_version_for_everyone(owner_api: Api, fake_hub: FakeHub, db: Any,  # noqa: F811
                                                   client: Any, redis: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    await _linked(owner_api)
    await _add_owner(db)
    monkeypatch.setattr(gh, "__version__", "v0.1.50")
    await _run(redis)
    b = await _login(client, "owner2@example.vn")
    try:
        pa, pb = await _release_proposal(owner_api), await _release_proposal(b)
        fake_hub.calls.clear()
        r = await owner_api.send("POST", f"/gen/proposals/{pa['id']}/cancel", {})
        assert r.status_code == 200 and r.json()["status"] == "cancelled"
        row = (await _rows())[0]
        assert row.status == "cancelled" and row.decided_at is not None
        stored = await proposals.load(redis, pb["id"])
        assert stored["status"] == "cancelled" and stored["labels"]["closed"] == "Owner khác đã huỷ ghi bản này"
        await b.send("POST", "/auth/pin/verify", {"pin": "112233"})
        r = await b.send("POST", f"/gen/proposals/{pb['id']}/confirm", {})
        assert r.status_code == 409 and r.json()["code"] == "GEN_PROPOSAL_DECIDED"
        assert fake_hub.writes() == []
    finally:
        await b.c.aclose()
    # Đã huỷ thì job không dựng lại bản này.
    assert (await _run(redis))[str(await org_id(db))] == "already_proposed"


# ─── 3. Dọn: hết hạn / kẹt 'writing' ──────────────────────────────────────────────────────────────────────────────

async def test_expired_and_stuck_rows_are_cleaned(owner_api: Api, fake_hub: FakeHub, db: Any,  # noqa: F811
                                                  redis: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    await _linked(owner_api)
    monkeypatch.setattr(gh, "__version__", "v0.1.50")
    await _run(redis)
    p = await _release_proposal(owner_api)
    old = "UPDATE agent.hub_release_proposals SET created_at = now() - interval '8 days'"
    async with admin_sessionmaker()() as adm:
        await adm.execute(text(old))
        await adm.commit()
    # Đề xuất Redis còn sống ⇒ vẫn 'pending'.
    out = await _run(redis)
    assert out["expired"] == 0 and (await _rows())[0].status == "pending"
    # Đề xuất Redis hết hạn (7 ngày) mà vẫn 'pending' ⇒ 'expired'.
    await redis.delete(proposals.key(p["id"]))
    out = await _run(redis)
    assert out["expired"] == 1 and (await _rows())[0].status == "expired"
    assert (await _rows())[0].decided_at is not None
    # 'writing' mà không còn khoá đang-ghi (tiến trình ghi chết giữa chừng) ⇒ trả về 'pending'.
    async with admin_sessionmaker()() as adm:
        await adm.execute(text("UPDATE agent.hub_release_proposals SET status = 'writing'"))
        await adm.commit()
    await redis.set(proposals.key(p["id"]), orjson.dumps({"id": p["id"], "status": "pending"}))
    await redis.set(proposals.claim_key(p["id"]), "1", ex=60)                         # đang ghi thật ⇒ giữ nguyên
    assert (await _run(redis))["reset"] == 0 and (await _rows())[0].status == "writing"
    await redis.delete(proposals.claim_key(p["id"]))
    assert (await _run(redis))["reset"] == 1 and (await _rows())[0].status == "pending"
    # 'writing' kẹt mà trước đó đã có một lần ghi chưa chắc ⇒ về 'uncertain' (về 'pending' thì Owner khác ghi trùng).
    async with admin_sessionmaker()() as adm:
        await adm.execute(text("UPDATE agent.hub_release_proposals SET status = 'writing', "
                               "uncertain_by = gen_random_uuid()"))
        await adm.commit()
    assert (await _run(redis))["reset"] == 1 and (await _rows())[0].status == "uncertain"
    # 'uncertain' còn đề xuất sống ⇒ giữ nguyên (chỉ Owner đã ghi gỡ được); hết đề xuất ⇒ 'expired'.
    out = await _run(redis)
    assert out["expired"] == 0 and out["reset"] == 0 and (await _rows())[0].status == "uncertain"
    await redis.delete(proposals.key(p["id"]))
    assert (await _run(redis))["expired"] == 1 and (await _rows())[0].status == "expired"


# ─── 4. Cron + migration ──────────────────────────────────────────────────────────────────────────────────────────

def test_cron_registered() -> None:
    cj = next(c for c in WorkerSettings.cron_jobs if c.name == "cron:gen_kho_release")
    assert cj.minute == {7, 37} and cj.hour is None
    assert JOB_LABELS["gen_kho_release"] == "Đề xuất ghi Phiên vào Kho"
    assert any(getattr(f, "__name__", "") == "gen_kho_release" for f in WorkerSettings.functions)


async def test_migration_0032_is_rerunnable_and_constrained(owner_api: Api, fresh_db: str, db: Any) -> None:
    from migrations import sqlfile  # type: ignore[import-not-found]

    sql = (sqlfile.sql_dir() / "0032_v0150_gen_memory_kho_write.sql").read_text(encoding="utf-8")
    with psycopg.connect(f"{PG}/{fresh_db}", autocommit=True) as c:        # đã migrate một lần; chạy thêm hai lần nữa
        c.execute(sql)  # type: ignore[arg-type]
        c.execute(sql)  # type: ignore[arg-type]
        # Bảng của bản dựng trước (chưa có trạng thái 'uncertain' / cột uncertain_by): chạy lại phải nâng được.
        c.execute("ALTER TABLE agent.hub_release_proposals DROP COLUMN uncertain_by")
        c.execute("ALTER TABLE agent.hub_release_proposals DROP CONSTRAINT hub_release_proposals_status_check")
        c.execute("ALTER TABLE agent.hub_release_proposals ADD CONSTRAINT hub_release_proposals_status_check "
                  "CHECK (status IN ('pending', 'writing', 'written', 'cancelled', 'expired'))")
        c.execute(sql)  # type: ignore[arg-type]
    org = await org_id(db)
    ok_cases = [
        "INSERT INTO agent.hub_release_proposals (org_id, version, status) VALUES (:o, 'v0.1.50', 'pending')",
        "INSERT INTO agent.hub_release_proposals (org_id, version, status, uncertain_by) "
        "VALUES (:o, 'v0.1.52', 'uncertain', gen_random_uuid())",
        "INSERT INTO agent.gen_memory_notes (org_id, text, source) VALUES (:o, 'a', 'gen')",
    ]
    for sql_ in ok_cases:
        await db.execute(text(sql_), {"o": org})
    await db.commit()
    bad_cases = [
        "INSERT INTO agent.hub_release_proposals (org_id, version, status) VALUES (:o, 'v1.2', 'pending')",
        "INSERT INTO agent.hub_release_proposals (org_id, version, status) VALUES (:o, 'v0.1.51', 'done')",
        "INSERT INTO agent.hub_release_proposals (org_id, version, status) VALUES (:o, 'v0.1.50', 'pending')",  # PK
        "INSERT INTO agent.gen_memory_notes (org_id, text, source) VALUES (:o, '', 'gen')",
        f"INSERT INTO agent.gen_memory_notes (org_id, text, source) VALUES (:o, '{'x' * 281}', 'gen')",
        "INSERT INTO agent.gen_memory_notes (org_id, text, source) VALUES (:o, 'b', 'khach')",
        f"INSERT INTO agent.gen_memory_notes (org_id, text, reason, source) VALUES (:o, 'c', '{'y' * 201}', 'gen')",
    ]
    for sql_ in bad_cases:
        with pytest.raises(DBAPIError):
            await db.execute(text(sql_), {"o": org})
        await db.rollback()
    # proposal_id UNIQUE: một đề xuất chỉ lưu một ghi chú.
    pid = uuid.uuid4()
    await db.execute(text("INSERT INTO agent.gen_memory_notes (org_id, text, source, proposal_id) "
                          "VALUES (:o, 'd', 'gen', :p)"), {"o": org, "p": pid})
    with pytest.raises(DBAPIError):
        await db.execute(text("INSERT INTO agent.gen_memory_notes (org_id, text, source, proposal_id) "
                              "VALUES (:o, 'e', 'gen', :p)"), {"o": org, "p": pid})
    await db.rollback()


@pytest.mark.parametrize(("table", "insert"), [
    ("agent.gen_memory_notes", "INSERT INTO agent.gen_memory_notes (org_id, text, source) VALUES (:o, 'n', 'owner')"),
    ("agent.hub_release_proposals",
     "INSERT INTO agent.hub_release_proposals (org_id, version, status) VALUES (:o, 'v0.1.50', 'pending')"),
])
async def test_new_tables_rls_isolate_orgs(app: Any, db: Any, table: str, insert: str) -> None:
    org_a = await org_id(db)
    org_b = (await db.execute(text(
        "INSERT INTO core.organizations (name) VALUES ('Tổ chức B (RLS 0032)') RETURNING id"))).scalar_one()
    org_c = (await db.execute(text(
        "INSERT INTO core.organizations (name) VALUES ('Tổ chức C (RLS 0032)') RETURNING id"))).scalar_one()
    for org in (org_a, org_b):
        await db.execute(text(insert), {"o": org})
    await _as_low_priv(db, table)
    await db.execute(text("SELECT set_config('app.org_id', :o, true)"), {"o": str(org_a)})
    assert (await db.execute(text(f"SELECT org_id FROM {table}"))).scalars().all() == [org_a]  # noqa: S608
    with pytest.raises(Exception, match="row-level security|row_level_security"):
        await db.execute(text(insert), {"o": org_c})
