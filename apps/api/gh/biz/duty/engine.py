"""Agent trực kênh: hỏi model quyết định, qua policy, ghi vết (ARCHITECTURE §5 bước 2–4, docs/api/phase-3-duty.md).

Luật:
- Một đơn vị ý nghĩa → nhiều nhất **một** quyết định cho mỗi agent (khoá Redis khi đang xử lý + chỉ mục duy nhất
  `(agent_id, trigger_unit_id)`; bản nháp và quyết định nằm cùng transaction nên trùng thì bỏ cả hai).
- `send` / `draft` → `create_draft(...)`: ghi ra ngoài **luôn** chờ duyệt ở mọi mức (khoá cứng 3, quyết định Q2);
  mức 3 thành gợi ý, mức 0–2 im lặng. `note` → sổ tay. `suggest` → chỉ ghi quyết định (mức ≥ 3).
- Model trả JSON sai, trích mã ngữ cảnh không có, hoặc nhắc tới ID/mã đối tượng không có trong ngữ cảnh → loại,
  ghi `silent` kèm lý do (không tạo bản nháp, không ghi sổ tay).
- Model không chạy được → đơn vị không bị đánh dấu gì, lượt sau thử lại (`Deferred` giữ sự kiện trong consumer
  group; việc quét định kỳ vớt phần còn sót).
"""

import logging
import time
import uuid
from dataclasses import dataclass, field, replace
from typing import Any

import orjson
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from gh import realtime
from gh.biz.core import drafts
from gh.biz.duty.context import Candidate, Context, build, mentioned_ids
from gh.chassis import actionlog, policy
from gh.memory import notebook
from gh.providers.clients import Message
from gh.providers.router import ModelUnavailable

log = logging.getLogger("gh.biz.duty")

DECISIONS = ("silent", "note", "suggest", "draft", "send")
NOTE_SECTIONS = ("attention_now", "rolling_context", "preferences", "open_threads")   # "Giới hạn" do Sếp đặt
DEFAULT_LIMITS = {"decisions_per_min": 20, "drafts_per_hour": 30}
CLAIM_TTL_S = 300
MAX_FAILS = 5
MAX_TEXT = 2000
PURPOSE = "duty_decide"

DONE, SKIPPED, DEFERRED, FAILED = "done", "skipped", "deferred", "failed"
# outcome ghi trong agent.decisions
NONE, NOTED, SUGGESTED, HELD, BLOCKED, REJECTED = "none", "noted", "suggested", "held", "blocked", "rejected"


def agent_key(agent_id: uuid.UUID) -> str:
    return f"agent:{agent_id}"


# ─── lời nhắc ─────────────────────────────────────────────────────────────────

def _identity(agent: Any) -> str:
    forbidden = "\n".join(f"  - {f}" for f in (agent.forbidden or [])) or "  (không có)"
    return (f"Bạn là \"{agent.name}\" — {agent.role_desc}.\n"
            f"Xưng hô: {orjson.dumps(agent.addressing or {}).decode()}\n"
            f"Giọng: {agent.voice}\n"
            f"Được nói khi: {agent.speak_when}\n"
            f"Cấm:\n{forbidden}")


def identity_block(agent: Any) -> str:
    """Khối "bạn là ai" của agent (tên, vai, xưng hô, giọng, lúc được nói, điều cấm) — dùng chung với "Soạn lại" nháp
    (v0.1.67, F081) để cùng một giọng và cùng điều cấm."""
    return _identity(agent)


#: Lý do ghi vào `agent.decisions.rationale` khi quyết định bị chặn vì nhóm Lắng nghe im lặng (Nhật ký lắng nghe nhận ra
#: quyết định này bằng chính chuỗi này để nói đúng nguyên nhân).
SILENT_GROUP_REASON = "nhóm Lắng nghe im lặng: không bao giờ trả lời, kể cả khi được tag"


def replies_allowed(c: Candidate) -> bool:
    """QD 17/10/2026: nhóm «Lắng nghe im lặng» (`silent`) KHÔNG BAO GIỜ trả lời — kể cả khi được tag, kể cả khi agent
    được gán BOT cho nhóm/người. Mọi chế độ khác giữ như cũ (tagged_only: được tag thì trả lời).
    Mặc định #22: nhóm `silent` còn không vào ứng viên (`context.candidates`) nên hàm này là lớp phòng thủ — nhóm
    vừa đổi sang `silent` giữa lúc chọn ứng viên và lúc xử lý phản hồi (xem `apply`, đọc lại chế độ hiện tại)."""
    return not (c.group_id and c.listen_mode == "silent")


def allowed_decisions(level: int, *, replies: bool = True) -> list[str]:
    if level <= 2 or not replies:
        return ["silent", "note"]
    if level == 3:
        return ["silent", "note", "suggest"]
    return list(DECISIONS)


def messages(ctx: Context) -> list[Message]:
    c = ctx.candidate
    guard = "\n".join(f"  - {g}" for g in ctx.guardrails) or "  (không có)"
    allowed = allowed_decisions(ctx.level, replies=replies_allowed(c))
    system = f"""{_identity(ctx.agent)}

Bạn đang trực kênh cho doanh nghiệp. Với MỘT sự kiện mới, quyết định một trong:
- "silent": không làm gì (im lặng đúng lúc là năng lực — mặc định khi không chắc hoặc không phải việc của bạn).
- "note": ghi một dòng vào sổ tay nhận thức của người/nhóm (điều cần nhớ cho lần sau).
- "suggest": gợi ý hành động cho nhân viên (không gửi gì ra ngoài).
- "draft": soạn sẵn một tin trả lời để người duyệt.
- "send": muốn gửi tin ngay — hệ thống VẪN đưa vào Bàn làm việc chờ người duyệt.
Mức tự trị hiện tại: {ctx.level} ({policy.LEVELS[ctx.level]}). Được chọn: {", ".join(allowed)}.
Giới hạn cho agent (từ sổ tay, bắt buộc tuân thủ):
{guard}

Chỉ dựa vào ngữ cảnh C1…Cn được cho. Không bịa giá, số lượng, mốc thời gian, mã hay ID không có trong ngữ cảnh.
Tin soạn phải đúng xưng hô, giọng và điều cấm ở trên, viết bằng ngôn ngữ của người gửi.
Trả về DUY NHẤT một đối tượng JSON:
{{"decision": "silent|note|suggest|draft|send",
 "rationale": "1–2 câu: vì sao",
 "context_refs": ["C1", "…"],
 "note": {{"section": "attention_now|rolling_context|preferences|open_threads", "text": "…"}},
 "text": "nội dung gợi ý (suggest) hoặc tin soạn sẵn (draft/send)"}}
"context_refs" là các mã ngữ cảnh bạn đã dựa vào (bắt buộc ít nhất một khi không im lặng)."""
    where = (f"nhóm \"{ctx.group_name}\" (chế độ nghe: {c.listen_mode})" if c.group_id
             else f"tin 1-1 trên {c.channel_type}")
    head = [f"Sự kiện mới: C1 trong {where}."]
    if c.tagged and replies_allowed(c):
        head.append("Bạn được TAG trực tiếp trong tin này — người gửi đang chờ bạn phản hồi, hãy ưu tiên.")
    if c.from_us:
        head.append("Tin này do phía mình (nhân viên/Sếp) nói ra, không phải khách.")
    if not replies_allowed(c):
        head.append("Nhóm đang ở chế độ Lắng nghe im lặng: không bao giờ soạn tin trả lời, kể cả khi được tag"
                    + (" (tin này có tag bạn, vẫn KHÔNG trả lời — chỉ ghi chú nếu cần)." if c.tagged else "."))
    body = "\n".join(i.line() for i in ctx.items)
    user = "\n".join(head) + f"\n\nNgữ cảnh (mỗi dòng: mã rồi JSON):\n{body}"
    return [Message("system", system), Message("user", user)]


# ─── đọc phản hồi model ──────────────────────────────────────────────────────

@dataclass
class Parsed:
    decision: str                     # đề xuất của model (bị loại → "silent")
    rationale: str
    cited: list[str] = field(default_factory=list)
    text: str | None = None
    note_section: str = "rolling_context"
    error: str | None = None          # khác None → bị loại
    requested: str | None = None      # quyết định model nêu, kể cả khi bị loại (None = không đọc được)


def parse(raw: str, ctx: Context, prompt: str) -> Parsed:
    """Kiểm phản hồi model. Sai hình dạng / mã bịa → `Parsed(error=...)` (ghi `silent`)."""
    def reject(why: str, decision: str | None = None) -> Parsed:
        return Parsed("silent", "", error=why, requested=decision)

    try:
        data = orjson.loads(raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip())
    except orjson.JSONDecodeError:
        return reject("phản hồi không phải JSON")
    if not isinstance(data, dict):
        return reject("phản hồi không phải đối tượng JSON")
    decision = data.get("decision")
    if decision not in DECISIONS:
        return reject(f"quyết định không hợp lệ: {str(decision)[:40]}")
    refs = data.get("context_refs") or []
    if not isinstance(refs, list) or not all(isinstance(x, str) for x in refs):
        return reject("context_refs phải là danh sách mã", decision)
    known = ctx.by_handle()
    bad = [x for x in refs if x not in known]
    if bad:
        return reject(f"trích mã ngữ cảnh không có: {', '.join(bad[:5])}", decision)
    rationale = data.get("rationale")
    rationale = rationale.strip()[:1000] if isinstance(rationale, str) else ""
    body = data.get("text")
    body = body.strip() if isinstance(body, str) else None
    note: dict[str, Any] = data["note"] if isinstance(data.get("note"), dict) else {}
    raw_note = note.get("text")
    note_text = raw_note.strip() if isinstance(raw_note, str) else None
    section = str(note["section"]) if note.get("section") in NOTE_SECTIONS else "rolling_context"
    # ID / mã đối tượng nhắc trong câu chữ phải có trong ngữ cảnh đã đưa.
    invented = sorted(mentioned_ids(" ".join(filter(None, [rationale, body, note_text]))) - mentioned_ids(prompt))
    if invented:
        return reject(f"nhắc tới ID không có trong ngữ cảnh: {', '.join(invented[:5])}", decision)
    if decision != "silent" and not refs:
        return reject("không dẫn ngữ cảnh nào cho quyết định", decision)
    if decision == "note" and not note_text:
        return reject("quyết định note thiếu nội dung ghi", decision)
    if decision in ("suggest", "draft", "send") and not body:
        return reject(f"quyết định {decision} thiếu nội dung", decision)
    if body and len(body) > MAX_TEXT:
        return reject(f"nội dung dài quá {MAX_TEXT} ký tự", decision)
    return Parsed(decision, rationale, list(dict.fromkeys(refs)), body if decision != "note" else note_text,
                  section, requested=decision)


# ─── áp policy và ghi vết ─────────────────────────────────────────────────────

@dataclass
class Result:
    decision: str
    outcome: str
    reason: str | None = None
    draft: dict[str, Any] | None = None
    proposal: dict[str, Any] | None = None


async def _pending_draft(db: AsyncSession, agent_id: uuid.UUID, c: Candidate) -> str | None:
    p: dict[str, Any]
    if c.group_id:
        where, p = "group_id = :g", {"g": c.group_id}
    else:
        where, p = "group_id IS NULL AND subject_type = 'person' AND subject_id = :p", {"p": c.person_id}
    return (await db.execute(text(f"""SELECT code FROM biz.action_drafts
                                      WHERE agent_id = :a AND status = 'pending' AND {where}
                                      ORDER BY created_at DESC LIMIT 1"""),  # noqa: S608 — where là hằng
                             {"a": agent_id, **p})).scalar_one_or_none()


def limits_of(agent: Any) -> dict[str, int]:
    raw = agent.limits if isinstance(getattr(agent, "limits", None), dict) else {}
    return {k: int(raw.get(k, v)) for k, v in DEFAULT_LIMITS.items()}


async def _drafts_last_hour(db: AsyncSession, agent_id: uuid.UUID) -> int:
    return int((await db.execute(text("""SELECT count(*) FROM biz.action_drafts
                                         WHERE agent_id = :a AND created_at > now() - interval '1 hour'"""),
                                 {"a": agent_id})).scalar_one())


async def apply(db: AsyncSession, org_id: uuid.UUID, ctx: Context, parsed: Parsed) -> Result:
    """Chuyển đề xuất của model thành việc thật theo mức tự trị hiệu lực. Không commit."""
    c, agent, level = ctx.candidate, ctx.agent, ctx.level
    if parsed.error:
        return Result("silent", REJECTED, f"Loại phản hồi model: {parsed.error}")
    req = parsed.decision
    if req == "silent":
        return Result("silent", NONE)
    if req == "note":
        subject_id = c.person_id or c.group_id
        if subject_id is None:
            return Result("silent", NONE, "đơn vị không gắn người hay nhóm để ghi sổ tay")
        subject = ("person" if c.person_id else "group", subject_id)
        cited = ctx.by_handle()
        refs = [ctx.trigger.ref, *(cited[h].ref for h in parsed.cited if cited[h].ref != ctx.trigger.ref)]
        entry = await notebook.append(db, org_id, subject[0], subject[1], parsed.note_section, parsed.text or "",
                                      refs, author=agent_key(agent.id))
        return Result("note", NOTED, proposal={"note_entry_id": str(entry), "section": parsed.note_section,
                                                 "text": parsed.text})
    proposal = {"text": parsed.text}
    if c.group_id:
        # Chế độ nghe có thể đổi giữa lúc chọn ứng viên / hỏi model và lúc này (Sếp bấm «Lắng nghe im lặng») ⇒ đọc lại
        # giá trị HIỆN TẠI trước khi gợi ý / soạn nháp, không tin ảnh chụp trong `Candidate`.
        now_mode = (await db.execute(text("SELECT listen_mode FROM core.groups WHERE id = :g"),
                                     {"g": c.group_id})).scalar_one_or_none()
        if now_mode != c.listen_mode:
            c = replace(c, listen_mode=now_mode)
    if not replies_allowed(c):             # QD 17/10: nhóm Lắng nghe im lặng — không gợi ý, không soạn, không gửi
        return Result("silent", BLOCKED, SILENT_GROUP_REASON, proposal=proposal)
    gate = policy.evaluate("message.send", level)
    if gate.outcome == policy.BLOCKED:
        return Result("silent", BLOCKED, f"mức {level} ({policy.LEVELS[level]}) không gợi ý hay soạn tin",
                      proposal=proposal)
    if req == "suggest":
        return Result("suggest", SUGGESTED, proposal=proposal)

    # draft / send — các lý do hạ xuống gợi ý (không tạo bản nháp)
    why: str | None = None
    if c.from_us:
        why = "tin do phía mình nói ra, không soạn trả lời"
    elif not c.tagged and (code := await _pending_draft(db, agent.id, c)):
        why = f"đã có bản nháp {code} chờ duyệt cho cùng nơi nhận"
    elif await _drafts_last_hour(db, agent.id) >= limits_of(agent)["drafts_per_hour"]:
        why = f"vượt giới hạn {limits_of(agent)['drafts_per_hour']} bản nháp/giờ của agent"
    if why:
        return Result("suggest", SUGGESTED, why, proposal=proposal)

    identity_id = None
    if not c.group_id and c.person_id:       # tin 1-1: gửi đúng tài khoản đã nhắn (cùng đường với Hộp thư)
        acct = await drafts.person_reply_account(db, org_id, c.person_id, c.unit_id)
        identity_id = acct[1] if acct and acct[0] == c.channel_type else None
    target = drafts.reply_target(c.channel_type, group_id=c.group_id, person_id=c.person_id, identity_id=identity_id)
    subject_id = c.person_id or c.group_id
    about = (("person" if c.person_id else "group"), subject_id) if subject_id else None
    title = f"Trả lời {ctx.person_name or ctx.group_name or 'tin nhắn'}" + (
        f" · {ctx.group_name}" if ctx.person_name and ctx.group_name else "")
    made = await drafts.create_draft(db, org_id=org_id, kind="message", title=title[:200],
                                     body_text=parsed.text or "", target=target, agent_id=agent.id,
                                     subject=about, sources=ctx.sources(),
                                     autonomy_level=level)
    if made["outcome"] == policy.HELD:
        return Result(req, HELD, made["hold_reason"], draft=made, proposal=proposal)
    if made["outcome"] == policy.SUGGEST:
        return Result("suggest", SUGGESTED, made["hold_reason"], proposal=proposal)
    # message.send ghi ra ngoài nên không bao giờ `auto`; mọi kết quả khác coi như bị chặn.
    return Result("silent", BLOCKED, made["hold_reason"], proposal=proposal)


async def record(db: AsyncSession, org_id: uuid.UUID, ctx: Context, parsed: Parsed, res: Result) -> uuid.UUID | None:
    """Ghi agent.decisions + Action Log. Trùng (đã có quyết định cho cặp agent–đơn vị) → None, người gọi rollback."""
    c = ctx.candidate
    rationale = parsed.rationale if not parsed.error else ""
    if res.reason:
        rationale = f"{rationale} [{res.reason}]".strip() if rationale else res.reason
    known = ctx.by_handle()
    row = (await db.execute(text("""
        INSERT INTO agent.decisions (org_id, agent_id, trigger_ref, trigger_unit_id, decision, requested, outcome,
                                     autonomy_level, rationale, context_refs, cited_refs, draft_id, proposal)
        VALUES (:o, :a, CAST(:tr AS jsonb), :u, :d, :rq, :oc, :lvl, :why, CAST(:cr AS jsonb), CAST(:ci AS jsonb),
                :dr, CAST(:pr AS jsonb))
        ON CONFLICT (agent_id, trigger_unit_id) WHERE trigger_unit_id IS NOT NULL DO NOTHING
        RETURNING id"""),
        {"o": org_id, "a": c.agent_id, "tr": orjson.dumps(ctx.trigger.ref).decode(), "u": c.unit_id,
         "d": res.decision, "rq": parsed.requested,
         "oc": res.outcome, "lvl": ctx.level, "why": rationale or None, "cr": orjson.dumps(ctx.refs()).decode(),
         "ci": orjson.dumps([known[h].ref for h in parsed.cited]).decode(),
         "dr": res.draft["id"] if res.draft else None,
         "pr": orjson.dumps(res.proposal).decode() if res.proposal else None})).scalar_one_or_none()
    if row is None:
        return None
    result = {HELD: "held", BLOCKED: "blocked", REJECTED: "failed"}.get(res.outcome, "ok")
    await actionlog.record(db, org_id=org_id, actor_type="agent", actor_id=agent_key(c.agent_id),
                           action="agent.decided", target_type="meaning_unit", target_id=str(c.unit_id),
                           target_label=ctx.trigger.label[:200], autonomy_level=ctx.level, result=result,
                           detail={"decision": res.decision, "requested": parsed.requested, "outcome": res.outcome,
                                   "reason": res.reason, "decision_id": str(row),
                                   "draft_id": str(res.draft["id"]) if res.draft else None,
                                   "draft_code": res.draft["code"] if res.draft else None,
                                   "context_refs": len(ctx.items), "tagged": c.tagged})
    return row  # type: ignore[no-any-return]


# ─── một cặp agent–đơn vị ─────────────────────────────────────────────────────

async def _rate_ok(redis: Redis, agent_id: uuid.UUID, per_min: int) -> bool:
    key = f"gh:duty:rate:{agent_id}:{int(time.time() // 60)}"
    n = await redis.incr(key)
    await redis.expire(key, 120)
    return int(n) <= per_min


async def _load_agent(db: AsyncSession, agent_id: uuid.UUID) -> Any:
    return (await db.execute(text("""
        SELECT a.*, b.temperature, b.context_tokens FROM agent.identities a
        LEFT JOIN agent.bindings b ON b.org_id = a.org_id AND b.agent_key = 'agent:' || a.id::text
        WHERE a.id = :a"""), {"a": agent_id})).one_or_none()


async def _record_failure(sm: async_sessionmaker[AsyncSession], redis: Redis, org_id: uuid.UUID, c: Candidate,
                          exc: Exception) -> None:
    """Lỗi xử lý (không phải model chết) lặp lại MAX_FAILS lần → chốt `silent` kèm lý do để không thử mãi."""
    key = f"gh:duty:fail:{c.agent_id}:{c.unit_id}"
    n = int(await redis.incr(key))
    await redis.expire(key, 86400)
    if n < MAX_FAILS:
        return
    async with sm() as db:
        ref = {"type": "meaning_unit", "id": str(c.unit_id)}
        await db.execute(text("""
            INSERT INTO agent.decisions (org_id, agent_id, trigger_ref, trigger_unit_id, decision, outcome, rationale,
                                         context_refs)
            VALUES (:o, :a, CAST(:r AS jsonb), :u, 'silent', 'rejected', :why, '[]')
            ON CONFLICT (agent_id, trigger_unit_id) WHERE trigger_unit_id IS NOT NULL DO NOTHING"""),
            {"o": org_id, "a": c.agent_id, "r": orjson.dumps(ref).decode(), "u": c.unit_id,
             "why": f"Bỏ qua sau {n} lần lỗi xử lý: {str(exc)[:300]}"})
        await actionlog.record(db, org_id=org_id, actor_type="agent", actor_id=agent_key(c.agent_id),
                               action="agent.decided", target_type="meaning_unit", target_id=str(c.unit_id),
                               result="failed", detail={"decision": "silent", "outcome": REJECTED,
                                                        "error": str(exc)[:300]})
        await db.commit()
    await redis.delete(key)


async def handle(sm: async_sessionmaker[AsyncSession], redis: Redis, router: Any, org_id: uuid.UUID,
                 c: Candidate) -> str:
    """Xử lý một cặp agent–đơn vị. Ném `ModelUnavailable` khi không còn model nào (người gọi hoãn cả lô)."""
    claim = f"gh:duty:claim:{c.agent_id}:{c.unit_id}"
    if not await redis.set(claim, "1", nx=True, ex=CLAIM_TTL_S):
        return DEFERRED                     # worker khác đang xử lý đúng cặp này
    try:
        async with sm() as db:
            agent = await _load_agent(db, c.agent_id)
            if agent is None or not agent.is_enabled:
                return SKIPPED
            if (await db.execute(text("""SELECT 1 FROM agent.decisions WHERE agent_id = :a AND trigger_unit_id = :u"""),
                                 {"a": c.agent_id, "u": c.unit_id})).first():
                return SKIPPED
            # Tin tag agent đi đường nhanh: không bị giới hạn tần suất chặn lại.
            if not c.tagged and not await _rate_ok(redis, c.agent_id, limits_of(agent)["decisions_per_min"]):
                return DEFERRED
            level = await drafts.effective_level(db, org_id, agent_id=c.agent_id, person_id=c.person_id,
                                                 group_id=c.group_id)
            ctx = await build(db, org_id, agent, c, level, context_tokens=agent.context_tokens)
        msgs = messages(ctx)
        routed = await router.generate(org_id, agent_key=agent_key(c.agent_id), purpose=PURPOSE, messages=msgs,
                                       json_mode=True,
                                       temperature=float(agent.temperature) if agent.temperature is not None else 0.3)
        parsed = parse(routed.text, ctx, "\n".join(m.content for m in msgs))
        async with sm() as db:
            res = await apply(db, org_id, ctx, parsed)
            decision_id = await record(db, org_id, ctx, parsed, res)
            if decision_id is None:
                await db.rollback()          # đã có quyết định (trùng) → bỏ luôn bản nháp / mục sổ tay vừa tạo
                return SKIPPED
            await db.commit()
            draft_item = await drafts.list_item(db, res.draft["id"]) if res.draft else None
        if draft_item is not None:
            await realtime.publish(redis, "draft.new", draft_item, org_id=org_id)
        await realtime.publish(redis, "agent.decision", {
            "id": str(decision_id), "agent": {"id": str(agent.id), "name": agent.name}, "decision": res.decision,
            "trigger": ctx.trigger.ref, "draft": {"id": str(res.draft["id"]), "code": res.draft["code"]}
            if res.draft else None}, org_id=org_id)
        await redis.delete(f"gh:duty:fail:{c.agent_id}:{c.unit_id}")
        return DONE
    except ModelUnavailable:
        raise
    except Exception as exc:  # noqa: BLE001 — cách ly lỗi từng cặp: cặp khác vẫn chạy
        log.exception("Agent %s lỗi với đơn vị %s", c.agent_id, c.unit_id)
        await _record_failure(sm, redis, org_id, c, exc)
        return FAILED
    finally:
        await redis.delete(claim)


@dataclass
class Batch:
    done: int = 0
    skipped: int = 0
    deferred: int = 0
    failed: int = 0
    model_down: bool = False

    @property
    def pending(self) -> bool:
        return self.deferred > 0 or self.model_down


async def process(sm: async_sessionmaker[AsyncSession], redis: Redis, router: Any, org_id: uuid.UUID,
                  cands: list[Candidate], *, deadline: float | None = None) -> Batch:
    """Chạy lần lượt (tin tag trước — thứ tự đã sắp ở `candidates`). Model chết → dừng, phần còn lại để lượt sau."""
    b = Batch()
    for i, c in enumerate(cands):
        if deadline is not None and time.monotonic() > deadline:
            b.deferred += len(cands) - i
            break
        try:
            r = await handle(sm, redis, router, org_id, c)
        except ModelUnavailable as e:
            log.warning("Agent trực kênh hoãn %d đơn vị: %s", len(cands) - i, e)
            b.model_down = True
            b.deferred += len(cands) - i
            break
        setattr(b, r, getattr(b, r) + 1)
    return b
