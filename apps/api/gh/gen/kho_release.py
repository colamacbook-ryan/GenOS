"""Gen đề xuất ghi Phiên vào Kho dữ liệu mỗi khi máy chủ lên bản mới — v0.1.50 (F-87, QD-18).

Cron `gen_kho_release` (phút 7 và 37, gh/worker.py). Với MỖI tổ chức đủ điều kiện (Gen bật cho Owner, liên kết Gen-hub
đang bật, Gen-hub đã cấp quyền TẠO bản ghi Kho — `hub.write_scopes(...)['kho_create']`, đúng tool thẻ dùng, như
`kho_labels` và `write_kho`; thiếu riêng kho_update không chặn — ≥ 1 Owner hoạt động):

1. `INSERT INTO agent.hub_release_proposals (org_id, version, 'pending') ON CONFLICT DO NOTHING RETURNING` — chỉ khi
   chèn được mới dựng đề xuất ⇒ mỗi (tổ chức, phiên bản) ĐÚNG MỘT lần, kể cả khi hai worker chạy song song (PK).
   Chưa đủ điều kiện thì KHÔNG chèn: lần chạy sau (Sếp vừa tick quyền ghi rồi bấm Kiểm tra) sẽ thử lại.
2. Cho từng Owner: một hội thoại "Gen đề xuất ghi Kho · Phiên vX.Y.Z" có tin của Gen (lời ngắn + thẻ đề xuất
   `kho_create` bảng Phiên do `proposals.build_release` dựng — tóm tắt / nhãn do HỆ THỐNG viết, kèm khoá meta
   `release_version`), đề xuất lưu Redis 7 ngày, một chuông `gen.kho_proposal` (link `/overview?gen={cid}`).
3. Mọi thứ trên cùng MỘT transaction với dòng bảng + Action Log hệ thống `gen.kho_release_proposed` — lỗi giữa chừng thì
   không để lại dòng nào, lần chạy sau thử lại.

Job KHÔNG gọi Gen-hub và KHÔNG ghi Kho: ghi chỉ khi Owner bấm Xác nhận + nhập mã PIN (gh.gen.routes.confirm_proposal).
Dọn: đề xuất `pending` / `uncertain` mà đề xuất Redis đã hết hạn (7 ngày) → `expired`; `writing` mà không còn khoá
đang-ghi trong Redis (tiến trình ghi chết giữa chừng) → trả về `pending`, hoặc `uncertain` nếu trước đó đã có một lần
ghi chưa chắc (`uncertain_by` — chỉ Owner đó bấm lại / huỷ, Owner khác không ghi trùng được). `uncertain` còn đề xuất
sống thì giữ nguyên: chỉ Owner đã ghi chưa chắc mới gỡ được (sau khi mở Kho kiểm).
"""

import asyncio
import logging
import re
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import text

import gh
from gh import notifications
from gh.chassis import actionlog
from gh.config import get_settings
from gh.gen import proposals, store
from gh.hub_link import KHO_LABEL, load_kho_label, relabel
from gh.hub_link import service as hub

log = logging.getLogger("gh.gen.kho_release")

PROPOSAL_TTL_S = 7 * 24 * 3600
KIND = "gen.kho_proposal"
ACTION = "gen.kho_release_proposed"
TITLE = "Gen đề xuất ghi Kho · Phiên {version}"
SAY = ("Máy chủ Gen-Harness vừa lên {version}. Em đề xuất ghi một Phiên vào " + KHO_LABEL +
       " để lưu mốc này — {addr} xem lại, sửa nếu cần rồi bấm Xác nhận và nhập mã PIN thì em mới ghi (qua Gen-hub).")
BELL_BODY = "Gen-Harness đã lên {version}. Xem thẻ đề xuất, Xác nhận và nhập mã PIN để ghi Phiên vào " + KHO_LABEL + "."
#: Đề xuất `pending` còn trẻ hơn mốc này không bị coi là "hết hạn" dù thiếu khoá Redis (tránh đua với lúc vừa dựng).
STALE_AFTER = timedelta(hours=1)
_VERSION = re.compile(r"v?(\d+\.\d+\.\d+)")


def version_of(raw: str | None = None) -> str | None:
    """`gh.__version__` (hoặc `raw`) chuẩn hoá 'vX.Y.Z'. Bản dev / pre-release ('dev', '0.1.50-rc1') ⇒ None."""
    m = _VERSION.fullmatch((gh.__version__ if raw is None else raw).strip())
    return f"v{m.group(1)}" if m else None


async def _set_org(db: Any, org: uuid.UUID) -> None:
    await db.execute(text("SELECT set_config('app.org_id', :o, true)"), {"o": str(org)})


async def _expire_stale(sm: Any, redis: Any, now: datetime) -> dict[str, int]:
    """`pending` / `uncertain` mà mọi đề xuất Redis đã hết hạn → `expired`; `writing` mà không còn khoá đang-ghi →
    `pending` (hoặc `uncertain` khi đã có lần ghi chưa chắc trước đó)."""
    counts = {"expired": 0, "reset": 0}
    async with sm() as db:
        rows = (await db.execute(text("""SELECT org_id, version, status, proposal_ids FROM agent.hub_release_proposals
                                         WHERE status IN ('pending', 'writing', 'uncertain') AND created_at < :lim"""),
                                 {"lim": now - STALE_AFTER})).all()
        for r in rows:
            pids = [str(x) for x in (r.proposal_ids or [])]
            alive = sum([await redis.exists(proposals.key(x)) for x in pids])
            if r.status in ("pending", "uncertain") and not alive:
                await db.execute(text("""UPDATE agent.hub_release_proposals SET status = 'expired', decided_at = now()
                                         WHERE org_id = :o AND version = :v AND status = :s"""),
                                 {"o": r.org_id, "v": r.version, "s": r.status})
                counts["expired"] += 1
            elif r.status == "writing":
                busy = sum([await redis.exists(proposals.claim_key(x)) for x in pids])
                if not busy:
                    await db.execute(text("""UPDATE agent.hub_release_proposals
                                             SET status = CASE WHEN uncertain_by IS NULL THEN 'pending'
                                                               ELSE 'uncertain' END
                                             WHERE org_id = :o AND version = :v AND status = 'writing'"""),
                                     {"o": r.org_id, "v": r.version})
                    counts["reset"] += 1
        await db.commit()
    return counts


async def _addr_of(db: Any, uid: uuid.UUID) -> str:
    """Cách Gen gọi Owner này (`core.users.addressing.bot_calls_me`, như engine/GenPanel); trống ⇒ 'Sếp'."""
    raw = (await db.execute(text("SELECT addressing->>'bot_calls_me' FROM core.users WHERE id = :u"),
                            {"u": uid})).scalar_one_or_none()
    return " ".join(str(raw or "").split())[:60] or "Sếp"


async def _one_org(sm: Any, redis: Any, org: uuid.UUID, version: str, now: datetime) -> str:
    async with sm() as db:
        await _set_org(db, org)
        cfg = await store.get_settings(db, org)
        if not cfg.get("enabled") or "owner" not in (cfg.get("roles") or []):
            return "gen_off"
        link = await hub.load(db, org)
        if link is None or link.server_id is None or not link.enabled:
            return "hub_off"
        if not (await hub.write_scopes(db, org))["kho_create"]:  # thẻ ghi Phiên chỉ dùng kho_create
            return "no_write_scope"
        owners = await notifications.owner_ids(db, org)
        if not owners:
            return "no_owner"
        inserted = (await db.execute(text("""INSERT INTO agent.hub_release_proposals (org_id, version, status)
                                             VALUES (:o, :v, 'pending') ON CONFLICT DO NOTHING RETURNING version"""),
                                     {"o": org, "v": version})).first()
        if inserted is None:
            await db.rollback()
            return "already_proposed"
        tz = await proposals.org_tz(db, org)
        repo = get_settings().release_repo
        kho = await load_kho_label(db, org)                   # v0.1.57: tên Kho Owner tự đặt (Nợ #30)
        pids: list[str] = []
        for uid in owners:
            cid = (await db.execute(text("""INSERT INTO agent.gen_conversations (org_id, user_id, title)
                                            VALUES (:o, :u, :t) RETURNING id"""),
                                    {"o": org, "u": uid, "t": TITLE.format(version=version)})).scalar_one()
            turn_id = uuid.uuid4()
            prop = proposals.build_release(org, uid, version, turn_id=turn_id, conversation_id=cid, tz=tz, repo=repo,
                                           now=now, kho=kho)
            await proposals.save(redis, prop, ttl=PROPOSAL_TTL_S)
            say = relabel(SAY.format(version=version, addr=await _addr_of(db, uid)), kho)
            steps = [{"kind": "say", "text": say}, {"kind": "proposal", "proposal": proposals.public(prop)}]
            await store.add_message(db, org, cid, "assistant", {"steps": steps}, turn_id=turn_id)
            bell = relabel(BELL_BODY.format(version=version), kho)
            await notifications.notify(db, org, [uid], kind=KIND, title=TITLE.format(version=version),
                                       body=bell, link=f"/overview?gen={cid}", redis=redis)
            pids.append(prop["id"])
        await db.execute(text("""UPDATE agent.hub_release_proposals SET proposal_ids = CAST(:p AS uuid[])
                                 WHERE org_id = :o AND version = :v"""),
                         {"p": [uuid.UUID(x) for x in pids], "o": org, "v": version})
        await actionlog.record(db, org_id=org, actor_type="system", actor_id="system:worker", action=ACTION,
                               target_type="hub_release", target_id=version, target_label=f"Phiên {version}",
                               detail={"version": version, "owners": len(owners), "proposal_ids": pids})
        await db.commit()
    return "proposed"


async def run(sm: Any, redis: Any, now: datetime | None = None) -> dict[str, Any]:
    """Một lượt cron. Trả {version, expired, reset, <org_id>: kết quả}. Bản dev ⇒ chỉ dọn, không dựng đề xuất."""
    now = now or datetime.now(UTC)
    version = version_of()
    out: dict[str, Any] = {"version": version}
    try:
        out.update(await _expire_stale(sm, redis, now))
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 — dọn lỗi không được chặn việc dựng đề xuất
        log.exception("Dọn đề xuất ghi Kho lỗi")
    if version is None:
        out["skipped"] = "dev_version"
        return out
    async with sm() as db:
        orgs = (await db.execute(text("SELECT id FROM core.organizations ORDER BY created_at"))).scalars().all()
    for org in orgs:
        try:
            out[str(org)] = await _one_org(sm, redis, org, version, now)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — một tổ chức lỗi không chặn tổ chức khác
            log.exception("Đề xuất ghi Phiên vào Kho lỗi (%s)", org)
            out[str(org)] = "error"
    return out
