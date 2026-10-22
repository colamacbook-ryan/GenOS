"""Hợp nhất danh tính (dò → gộp 🔒 → tách → đảo ngược đúng thứ tự) và sổ tay tự nén (mục ghim, luật cấm giữ nguyên)."""

from sqlalchemy import text

from gh.data.ingest import handle_directory
from gh.identity import service as identity
from gh.memory import notebook
from tests.conftest import OWNER, Api
from tests.phase2 import org_id


async def two_accounts(db, redis) -> object:  # type: ignore[no-untyped-def]
    """Cùng một người trên Zalo và WhatsApp (trùng SĐT), cộng một người khác."""
    org = await org_id(db)
    zalo = [{"external_id": "z-lan", "name": "Nguyễn Thị Lan", "phone": "0912 345 678"},
            {"external_id": "z-tung", "name": "Trần Văn Tùng", "phone": "0987000111"}]
    await handle_directory(db, redis, org, {"channel": "zalo", "groups": [{"external_id": "gz", "name": "Sỉ",
                                                                            "members": zalo}]})
    await handle_directory(db, redis, org, {"channel": "whatsapp", "groups": [{"external_id": "gw", "name": "Đối tác",
        "members": [{"external_id": "84912345678@s.whatsapp.net", "name": "Lan Nguyen", "phone": "+84912345678"}]}]})
    await db.commit()
    return org


async def test_detect_merge_split_revert(owner_api, db, redis) -> None:  # type: ignore[no-untyped-def]
    api: Api = owner_api
    org = await two_accounts(db, redis)
    assert await identity.detect(db, org) >= 1
    assert await identity.detect(db, org) == 0                     # không sinh trùng cặp
    await db.commit()
    cands = (await api.get("/identity/candidates")).json()
    lan = next(c for c in cands if "số điện thoại" in c["basis"])
    assert lan["confidence"] >= 0.9
    assert (await api.get("/identity/stats")).json()["pending_pairs"] >= 1

    # Gộp là thao tác nhạy cảm: thiếu PIN → 423.
    r = await api.send("POST", f"/identity/candidates/{lan['id']}/merge")
    assert r.status_code == 423
    await api.send("POST", "/auth/pin/verify", {"pin": OWNER["pin"]})
    r = await api.send("POST", f"/identity/candidates/{lan['id']}/merge")
    assert r.status_code == 200, r.text
    merge_log = r.json()["log_id"]
    live = (await db.execute(text("SELECT count(*) FROM core.persons WHERE merged_into_id IS NULL"))).scalar()
    assert live == 2
    keep = (await db.execute(text("SELECT person_id FROM core.person_identities WHERE external_id = 'z-lan'"))).scalar()
    ids = (await db.execute(text("SELECT id FROM core.person_identities WHERE person_id = :p ORDER BY external_id"),
                            {"p": keep})).scalars().all()
    assert len(ids) == 2
    r = await api.send("POST", f"/identity/candidates/{lan['id']}/merge")
    assert r.status_code == 409                                     # đã xử lý

    # Tách một tài khoản ra hồ sơ mới.
    wa = (await db.execute(text("SELECT id FROM core.person_identities WHERE external_id LIKE '%whatsapp%'"))).scalar()
    r = await api.send("POST", "/identity/split", {"person_id": str(keep), "identity_ids": [str(wa)]})
    assert r.status_code == 200, r.text
    split_log = r.json()["log_id"]
    # Đảo ngược phải theo thứ tự: thao tác mới (tách) trước, rồi mới tới gộp.
    r = await api.send("POST", f"/identity/history/{merge_log}/revert")
    assert r.status_code == 409 and r.json()["code"] == "REVERT_ORDER"
    assert (await api.send("POST", f"/identity/history/{split_log}/revert")).status_code == 200
    assert (await api.send("POST", f"/identity/history/{merge_log}/revert")).status_code == 200
    owners = (await db.execute(text("""SELECT count(DISTINCT person_id) FROM core.person_identities
                                       WHERE external_id IN ('z-lan', '84912345678@s.whatsapp.net')"""))).scalar()
    assert owners == 2                                              # trở lại hai hồ sơ riêng
    status = (await db.execute(text("SELECT status FROM core.identity_merge_candidates WHERE id = :i"),
                               {"i": lan["id"]})).scalar()
    assert status == "pending"
    hist = (await api.get("/identity/history")).json()
    assert len(hist) == 2 and all(h["reverted"] for h in hist)
    actions = (await db.execute(text("SELECT action FROM ops.action_log WHERE action LIKE 'identity.%'"))
               ).scalars().all()
    assert {"identity.merged", "identity.split", "identity.reverted"} <= set(actions)


async def test_notebook_compacts_at_90_percent_keeping_pinned_and_guardrails(app, db) -> None:  # type: ignore[no-untyped-def]
    org = await org_id(db)
    pid = (await db.execute(text("""INSERT INTO core.persons (org_id, code, display_name)
                                    VALUES (:o, 'PER-9', 'Chị Mai') RETURNING id"""), {"o": org})).scalar()
    nb = await notebook.ensure(db, org, "person", pid)
    await db.execute(text("UPDATE memory.notebooks SET token_budget = 300 WHERE id = :i"), {"i": nb.id})
    pinned = await notebook.append(db, org, "person", pid, "preferences", "Thích gọi điện buổi sáng", [], "user:x",
                                   pinned=True)
    guard = await notebook.append(db, org, "person", pid, "guardrails", "Không nhắc chuyện nợ cũ", [], "user:x")
    for i in range(30):
        await notebook.append(db, org, "person", pid, "rolling_context",
                              f"Lượt {i}: hỏi giá thép cuộn, cần 3 container giao trước cuối tháng", [], "agent:core")
    row = (await db.execute(text("SELECT token_used, token_budget, compaction_no FROM memory.notebooks WHERE id = :i"),
                            {"i": nb.id})).one()
    assert row.compaction_no >= 1 and row.token_used < 0.9 * row.token_budget
    alive = set((await db.execute(text("""SELECT id FROM memory.entries WHERE notebook_id = :n
                                           AND archived_at IS NULL"""), {"n": nb.id})).scalars().all())
    assert {pinned, guard} <= alive
    archived = (await db.execute(text("""SELECT count(*) FROM memory.entries WHERE notebook_id = :n
                                         AND archived_at IS NOT NULL"""), {"n": nb.id})).scalar()
    assert archived > 0                                             # lưu trữ, không xoá cứng
    n = (await db.execute(text("SELECT count(*) FROM memory.compactions WHERE notebook_id = :n"),
                          {"n": nb.id})).scalar()
    assert n == row.compaction_no
