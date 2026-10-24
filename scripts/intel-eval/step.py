#!/usr/bin/env python3
# ruff: noqa: E501, ASYNC230, ASYNC240, ASYNC110, I001     # script đo một mình: I/O tệp ngắn + poll tệp trong coroutine là chấp nhận được
"""step.py — bộ chạy G-KB TỪNG BƯỚC: một agent Claude đóng vai MODEL trong pipeline thật của Đợt 1 (máy không có Claude CLI/API key).

Dùng CHÍNH pipeline thật: replay (L6: đồng hồ giả, chế độ nén, Seeder, IngestSink) → `raw.events` thật → `GroupTurns.ensure_scanned/plan/turn`
(L2) → `extract.analyze_group_detailed` (L3: dựng prompt, parse hợp đồng, thử lại MỘT lần kèm lỗi) → `verify.check` → ghi `mem.facts`,
`clean.meaning_units`… Chỗ DUY NHẤT khác: `ModelRouter.generate` được thay bằng "model ngoài" — ghi prompt ra tệp, chờ agent ghi câu trả lời.

    python3 scripts/intel-eval/step.py init   --dataset .../kb1 --out /tmp/run/kb1 [--contexts ctx.json] [--auto-stub]
    python3 scripts/intel-eval/step.py next   --out /tmp/run/kb1            # in đường dẫn prompt.json đang chờ, hoặc DONE
    python3 scripts/intel-eval/step.py submit /tmp/run/kb1/turn_001/answer.json
    python3 scripts/intel-eval/step.py export --out /tmp/run/kb1/signals.json --run /tmp/run/kb1
    python3 scripts/intel-eval/step.py stats  --out /tmp/run/kb1            # số lượt, ký tự, token ước tính

Chạy bằng Python của venv API (`apps/api/.venv/bin/python`: cần sqlalchemy, fastapi, psycopg, redis). `init` dựng DB `gh_step_*` riêng (nhân
từ khuôn đã migrate) + một DB Redis riêng, rồi mở một TIẾN TRÌNH NỀN (`_serve`) chạy pipeline và dừng ở mỗi lần gọi model. `next`/`submit` chỉ
trao đổi tệp với tiến trình đó (`state.json`, `turn_NNN/prompt.json`, `answer.json` + `answer.submitted`). Thư mục chạy PHẢI nằm ngoài repo.
Đáp án G-KB không bao giờ đi qua đây (chấm bằng `gkb_score.py` sau khi chạy xong)."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import replay  # noqa: E402
from clockplan import PlanConfig  # noqa: E402
from common import Dataset, api_dir, load_dataset, parse_context, refuse_inside_repo, write_json  # noqa: E402

CHARS_PER_TOKEN = 3.0          # ƯỚC TÍNH thô cho tiếng Việt + JSON (không có tokenizer cục bộ); sai số ± 25%
PG_DEFAULT = "postgresql://postgres:postgres@localhost:5432"
REDIS_DEFAULT = "redis://localhost:6379/10"
TEMPLATE_LOCK_KEY = 0x6768_5445              # cùng khoá với tests/conftest.py: không migrate chồng lên pytest chạy song song
OWNER = {"display_name": "Anh Cơ", "email": "owner@example.vn", "password": "mat-khau-rat-dai-123", "pin": "246810", "pin_confirm": "246810"}
SETUP_TOKEN = "step-setup-token"
MAX_ROUNDS = 6                 # tối đa lượt pipeline cho MỘT lượt kế hoạch (an toàn/cổng nối nhau + một lượt thường)
IMMEDIATE = ("safety", "gate", "fast")
WAIT_S = float(os.environ.get("GH_STEP_WAIT_S", "900"))


def est_tokens(chars: int) -> int:
    return int(chars / CHARS_PER_TOKEN + 0.5)


# ─── tệp trạng thái ─────────────────────────────────────────────────────────────────────────────────────────────────────

def read_state(run: Path) -> dict[str, Any]:
    p = run / "state.json"
    if not p.exists():
        raise SystemExit(f"step: {run} chưa có state.json — chạy `init` trước")
    return json.loads(p.read_text(encoding="utf-8"))  # type: ignore[no-any-return]


def write_state(run: Path, **kw: Any) -> dict[str, Any]:
    p = run / "state.json"
    cur = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    if kw.pop("_daemon", False):
        kw["pid"] = os.getpid()
    cur.update(kw, updated=datetime.now().isoformat(timespec="seconds"))
    tmp = run / "state.json.tmp"
    tmp.write_text(json.dumps(cur, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, p)
    return cur


def alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    try:
        return Path(f"/proc/{pid}/stat").read_text().split()[2] != "Z"
    except OSError:
        return True


# ─── hạ tầng: DB khuôn + DB lần chạy + Redis ────────────────────────────────────────────────────────────────────────────

def _psycopg() -> Any:
    import psycopg  # noqa: PLC0415

    return psycopg


def async_url(pg: str, db: str) -> str:
    return f"{pg.replace('postgresql://', 'postgresql+asyncpg://')}/{db}"


def template_name() -> str:
    mig = sorted((api_dir() / "migrations" / "versions").glob("*.py"))
    h = hashlib.sha1(b"".join(f"{p.name}:{p.stat().st_size}".encode() for p in mig)).hexdigest()[:8]  # noqa: S324 — chỉ đặt tên
    return f"gh_step_tpl_{h}"


def ensure_template(pg: str) -> str:
    name = template_name()
    psycopg = _psycopg()
    with psycopg.connect(f"{pg}/postgres", autocommit=True) as c:
        c.execute("SELECT pg_advisory_lock(%s)", (TEMPLATE_LOCK_KEY,))
        if c.execute("SELECT 1 FROM pg_database WHERE datname = %s", (name,)).fetchone():
            return name
        c.execute(f'CREATE DATABASE "{name}"')
        env = {**os.environ, "GH_DATABASE_URL": async_url(pg, name), "GH_ADMIN_DATABASE_URL": "", "GH_ENV": "eval"}
        r = subprocess.run([sys.executable, "-m", "alembic", "upgrade", "heads"], cwd=api_dir(), env=env, capture_output=True, text=True)  # noqa: S603
        if r.returncode:
            c.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
            raise SystemExit(f"step: migrate khuôn lỗi:\n{r.stderr[-2000:]}")
    return name


def run_db_name(run: Path) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", run.name.lower()).strip("_")[:24] or "run"
    return f"gh_step_{slug}_{hashlib.sha1(str(run.resolve()).encode()).hexdigest()[:6]}"  # noqa: S324 — chỉ đặt tên


def create_run_db(pg: str, tpl: str, db: str, *, force: bool) -> None:
    psycopg = _psycopg()
    with psycopg.connect(f"{pg}/postgres", autocommit=True) as c:
        if c.execute("SELECT 1 FROM pg_database WHERE datname = %s", (db,)).fetchone():
            if not force:
                raise SystemExit(f"step: DB {db} đã tồn tại (lần chạy cũ ở cùng thư mục). Thêm --force để dựng lại.")
            assert db.startswith("gh_step_")
            c.execute(f'DROP DATABASE "{db}" WITH (FORCE)')
        c.execute(f'CREATE DATABASE "{db}" TEMPLATE "{tpl}"')


def prepare_redis(url: str, *, force: bool) -> None:
    from redis import Redis  # noqa: PLC0415

    r = Redis.from_url(url)
    owner = r.get("gh:env")
    if r.dbsize() and (owner or b"") != b"eval" and not force:
        raise SystemExit(f"step: Redis {url} đang có dữ liệu không phải của stack đo (thiếu gh:env=eval). Dùng --redis-url khác hoặc --force.")
    r.flushdb()
    r.set("gh:env", "eval")
    r.close()


# ─── lệnh init ──────────────────────────────────────────────────────────────────────────────────────────────────────────

def cmd_init(a: argparse.Namespace) -> None:
    run = Path(a.out).resolve()
    refuse_inside_repo(run)
    ds = load_dataset(a.dataset)
    if run.exists() and any(run.iterdir()):
        if not a.force:
            raise SystemExit(f"step: {run} không rỗng — thêm --force để chạy lại từ đầu")
        old = read_state(run) if (run / "state.json").exists() else {}
        if alive(old.get("pid")):
            raise SystemExit(f"step: tiến trình nền cũ (pid {old['pid']}) của {run} vẫn đang chạy — tự dừng nó rồi chạy lại (kill không do bộ này làm)")
        for p in run.iterdir():
            if p.is_dir():
                import shutil  # noqa: PLC0415

                shutil.rmtree(p)
            else:
                p.unlink()
    run.mkdir(parents=True, exist_ok=True)
    ctx = parse_context(ds.context_md)
    contexts = json.loads(Path(a.contexts).read_text()) if a.contexts else None
    pg, redis_url = a.pg, a.redis_url
    tpl = ensure_template(pg)
    db = run_db_name(run)
    create_run_db(pg, tpl, db, force=True if a.force else False)
    prepare_redis(redis_url, force=a.force)
    cfg = {"dataset": str(Path(a.dataset).resolve()), "contexts": contexts, "pg": pg, "db": db, "redis": redis_url, "auto": "stub" if a.auto_stub else None,
           "profile_items": sum(len(v) for v in ctx["profile"].values()), "context_groups": sorted(contexts or ctx["groups"])}
    write_json(run / "config.json", cfg)
    write_state(run, status="starting", seq=0, pending=None, turn=0, calls=0, error=None, dataset=ds.name, messages=len(ds.msgs))
    log = open(run / "daemon.log", "ab")  # noqa: SIM115
    p = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "_serve", "--out", str(run)], stdout=log, stderr=subprocess.STDOUT,  # noqa: S603
                         stdin=subprocess.DEVNULL, start_new_session=True, close_fds=True)
    write_state(run, pid=p.pid)
    st = wait_state(run, lambda s: s["status"] in ("waiting", "done", "error"))
    plan = st.get("plan", {})
    print(json.dumps({"run_dir": str(run), "db": db, "status": st["status"], "dataset": ds.name, "messages": len(ds.msgs),
                      "turns_planned": plan.get("turns_n"), "note": "số lượt kế hoạch đồng hồ; chưa tính lượt thử lại sau lỗi hợp đồng",
                      "profile_items": cfg["profile_items"], "context_groups": cfg["context_groups"], "pending": st.get("pending")}, ensure_ascii=False, indent=2))
    if st["status"] == "error":
        raise SystemExit(f"step: tiến trình nền lỗi: {st.get('error')} (xem {run / 'daemon.log'})")


def wait_state(run: Path, ok: Any, timeout: float = WAIT_S) -> dict[str, Any]:
    t0 = time.monotonic()
    while True:
        st = read_state(run)
        if ok(st):
            return st
        if st["status"] in ("starting", "running") and not alive(st.get("pid")):
            raise SystemExit(f"step: tiến trình nền (pid {st.get('pid')}) đã chết ở trạng thái {st['status']} — xem {run / 'daemon.log'}. Chạy lại init --force.")
        if time.monotonic() - t0 > timeout:
            raise SystemExit(f"step: hết {timeout:.0f}s chờ tiến trình nền (trạng thái {st['status']})")
        time.sleep(0.4)


def cmd_next(a: argparse.Namespace) -> None:
    run = Path(a.out).resolve()
    st = wait_state(run, lambda s: s["status"] in ("waiting", "done", "error"))
    if st["status"] == "done":
        print("DONE")
    elif st["status"] == "error":
        raise SystemExit(f"step: lỗi: {st.get('error')}")
    else:
        print(run / st["pending"] / "prompt.json")


def cmd_submit(a: argparse.Namespace) -> None:
    ans = Path(a.answer).resolve()
    run, d = ans.parent.parent, ans.parent
    st = read_state(run)
    if ans.name != "answer.json" or not (run / "state.json").exists():
        raise SystemExit("step: đường dẫn phải dạng <run_dir>/turn_NNN/answer.json")
    if st["status"] != "waiting" or st["pending"] != d.name:
        raise SystemExit(f"step: lượt đang chờ là {st.get('pending')!r} (trạng thái {st['status']}), không phải {d.name!r}. Chạy `next`.")
    if not ans.is_file() or not ans.read_text(encoding="utf-8").strip():
        raise SystemExit(f"step: {ans} chưa có hoặc rỗng — ghi câu trả lời (JSON brain-out/1) vào đó trước")
    seq = st["seq"]
    (d / "answer.submitted").write_text(datetime.now().isoformat(), encoding="utf-8")
    st = wait_state(run, lambda s: (s["seq"] > seq and s["status"] != "running") or s["status"] in ("done", "error"))
    turn = int(re.match(r"turn_(\d+)", d.name).group(1))  # type: ignore[union-attr]
    if st["status"] == "waiting" and st.get("turn") == turn:
        print(json.dumps({"turn": turn, "result": "gọi lại (câu trả lời trước sai hợp đồng hoặc JSON hỏng; pipeline thật thử lại MỘT lần kèm lỗi)"}, ensure_ascii=False))
    else:
        print(json.dumps(st.get("last") or {}, ensure_ascii=False))
    if st["status"] == "error":
        raise SystemExit(f"step: lỗi: {st.get('error')}")
    print("DONE" if st["status"] == "done" else f"tiếp: {run / st['pending'] / 'prompt.json'}")


# ─── tiến trình nền ─────────────────────────────────────────────────────────────────────────────────────────────────────

class Exchange:
    """Trao đổi với "model ngoài" qua tệp. Mỗi lần gọi model = một thư mục `turn_NNN[_cK]` có `prompt.json`; chờ `answer.submitted`."""

    def __init__(self, run: Path, auto: str | None) -> None:
        self.run, self.auto = run, auto
        self.turn, self.k, self.seq, self.calls = 0, 0, 0, 0
        self.log = open(run / "exchange.jsonl", "a", encoding="utf-8")  # noqa: SIM115
        self.thread_ref = ""
        self.fresh = False

    def begin_turn(self, thread_ref: str) -> None:
        """Báo một lượt pipeline mới sắp chạy; số lượt (`turn_NNN`) chỉ tăng khi lượt đó THỰC SỰ gọi model (lần gọi đầu tiên)."""
        self.thread_ref, self.fresh = thread_ref, True

    async def ask(self, purpose: str, messages: list[Any]) -> str:
        if self.fresh:
            self.turn, self.k, self.fresh = self.turn + 1, 0, False
        self.k += 1
        self.calls += 1
        tail = purpose.rsplit(".", 1)[-1]
        kind = tail if tail in ("d1", "d2") else ("extract" if self.k == 1 else "retry")
        name = f"turn_{self.turn:03d}" + ("" if self.k == 1 else f"_c{self.k}")
        d = self.run / name
        d.mkdir(parents=True, exist_ok=True)
        chars = sum(len(m.content) for m in messages)
        body = {"format": "gkb-step/1", "kind": kind, "turn": self.turn, "attempt": self.k, "chars": chars, "est_tokens": est_tokens(chars),
                "system": messages[0].content if messages and messages[0].role == "system" else "",
                "messages": [{"role": m.role, "content": m.content} for m in messages if not (m is messages[0] and m.role == "system")]}
        (d / "prompt.json").write_text(json.dumps(body, ensure_ascii=False, indent=1), encoding="utf-8")
        t0 = time.monotonic()
        if self.auto == "stub":
            text = json.dumps({"contract_version": "brain-out/1", "thread_ref": self.thread_ref, "units": [], "facts_proposed": []})
        else:
            self.seq += 1
            write_state(self.run, status="waiting", seq=self.seq, pending=name, kind=kind, turn=self.turn, calls=self.calls, _daemon=True)
            while not (d / "answer.submitted").exists():
                await asyncio.sleep(0.4)
            text = (d / "answer.json").read_text(encoding="utf-8")
            self.seq += 1
            write_state(self.run, status="running", seq=self.seq, pending=None, _daemon=True)
        self.log.write(json.dumps({"turn": self.turn, "call": self.k, "kind": kind, "dir": name, "prompt_chars": chars, "est_tokens_in": est_tokens(chars),
                                   "answer_chars": len(text), "est_tokens_out": est_tokens(len(text)), "wait_s": round(time.monotonic() - t0, 1)}) + "\n")
        self.log.flush()
        return text


def serve_env(cfg: dict[str, Any], run: Path) -> None:
    os.environ.update({
        "GH_ENV": "eval", "GH_DATABASE_URL": async_url(cfg["pg"], cfg["db"]), "GH_REDIS_URL": cfg["redis"], "GH_COOKIE_SECURE": "false",
        "GH_SETUP_TOKEN": SETUP_TOKEN, "GH_CLAUDE_HOME": str(run / "home" / ".claude"), "GH_CLAUDE_BINARY": "gh-step-no-claude",
        "GH_CLI_HOME": str(run / "home" / "agy"), "GH_OBJECTS_DIR": str(run / "objects"), "GH_EVAL_EMAIL": OWNER["email"], "GH_EVAL_PASSWORD": OWNER["password"]})
    os.environ.setdefault("GH_MASTER_KEY", "")
    os.environ.pop("GH_ADMIN_DATABASE_URL", None)
    d = str(api_dir())
    if d not in sys.path:
        sys.path.insert(0, d)


class PipelineAnalyzer:
    """Điểm cắm `replay` (`analyze(TurnInput) -> AnalysisResult`): mỗi lượt kế hoạch = ghi media giả, quét luật (a), lập kế hoạch cho ĐÚNG luồng đó
    rồi chạy `GroupTurns.turn` (phần thân của `GroupTurns.run`) — mọi thứ còn lại là code thật."""

    name = "gkb-step"

    def __init__(self, refinery: Any, org: Any, ex: Exchange, captured: list[Any]) -> None:
        self.r, self.org, self.ex, self.captured = refinery, org, ex, captured
        self.records: list[dict[str, Any]] = []
        self.tk: dict[str, str] = {}

    async def thread_key(self, gk: str) -> str | None:
        if gk not in self.tk:
            from sqlalchemy import text  # noqa: PLC0415

            async with self.r.sm() as db:
                gid = (await db.execute(text("SELECT id FROM core.groups WHERE external_id = :x"), {"x": gk})).scalar_one_or_none()
            if gid is None:
                return None
            self.tk[gk] = f"g:{gid}"
        return self.tk[gk]

    async def write_media(self, inp: replay.TurnInput) -> None:
        """Thay bước tải/STT/OCR (L5, cần bridge + engine thật) bằng engine giả của replay: ghi `clean.media_texts` đúng chữ có sẵn, qua helper của L5."""
        from sqlalchemy import text  # noqa: PLC0415

        from gh.biz.media import jobs  # noqa: PLC0415

        for m in inp.new:
            res = inp.media.get(m["id"])
            if res is None:
                continue
            async with self.r.sm() as db:
                row = (await db.execute(text("SELECT id, received_at, kind FROM raw.events WHERE org_id = :o AND external_msg_id = :m"),
                                        {"o": self.org, "m": m["id"]})).one_or_none()
                if row is None:
                    continue
                await db.execute(text("SELECT set_config('app.org_id', :o, true)"), {"o": str(self.org)})
                ev = jobs.PendingEvent(row.id, row.received_at, self.org, row.kind, f"fake://{row.kind}/{m['id']}", {"kind": row.kind})
                mime = "audio/ogg" if row.kind == "voice" else "image/jpeg"
                att = await jobs._insert_attachment(db, ev, key=f"fake:{m['id']}", mime=mime, size=len(res.get("text") or ""), sha=hashlib.sha256(m["id"].encode()).digest())
                if res["status"] == "ok":
                    await jobs._write_text(db, ev, att, kind=jobs.PRIMARY_KIND[row.kind] if row.kind == "voice" else "description", status="ok",
                                           text_=res["text"], engine="fake", version="fake/1")
                else:
                    await jobs._write_text(db, ev, att, kind=jobs.PRIMARY_KIND[row.kind], status="failed", error="fake_unreadable", engine="fake", version="fake/1")
                await db.commit()

    async def analyze(self, inp: replay.TurnInput) -> replay.AnalysisResult:
        """Một lượt KẾ HOẠCH của replay = các lượt pipeline thật cần để xả tin của luồng đó: lượt an toàn/cổng/nhanh chạy NGAY (nhận tin ứng viên +
        ngữ cảnh), rồi MỘT lượt thường nhận phần tồn đọng — đúng thứ tự `GroupTurns.run` (phần "ứng viên đến trong lúc chạy"). Mỗi lượt pipeline có
        bản ghi riêng (`self.records`) để chấm; phần còn quá 60 tin / lượt lỗi hợp đồng (đang lùi nhịp) chờ lượt kế hoạch sau của luồng."""
        from gh.refinery import clock
        from gh.refinery.runner import RunStats

        clock.freeze(datetime.fromisoformat(inp.now_iso))
        tk = await self.thread_key(inp.thread_ref.removeprefix("g:"))
        empty = {"contract_version": "brain-out/1", "thread_ref": inp.thread_ref, "units": [], "facts_proposed": []}
        usage_sum = {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0, "model": "agent-step", "calls": 0}
        if tk is None:
            return replay.AnalysisResult(output=empty, usage=usage_sum, attempts=0)
        await self.write_media(inp)
        g = self.r.groups
        last_out: Any = None
        last_err: str | None = None
        for _round in range(MAX_ROUNDS):
            while await g.ensure_scanned(self.org) >= 500:
                pass
            plan = await g.plan(self.org, only=[tk])
            progressed, immediate = False, False
            for ts, reason in plan:
                self.captured.clear()
                self.ex.begin_turn(inp.thread_ref)
                calls0 = self.ex.calls
                run_id = await self.r.start_run(self.org, "step")
                await g.turn(self.org, RunStats(run_id, "step"), ts, reason)
                if not self.captured:
                    continue            # lượt không gọi model (không còn tin tới hạn / đang lùi nhịp)
                progressed = True
                immediate = immediate or reason in IMMEDIATE
                kind, v, w = self.captured[-1]
                usage = {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0, "model": "agent-step", "calls": self.ex.calls - calls0}
                out: Any = None
                err: str | None = None
                if kind == "ok":
                    out = v.out.model_dump(mode="json") | {"thread_ref": inp.thread_ref}
                    attempts = v.attempts
                else:
                    err = f"{type(v).__name__}:{';'.join(getattr(v, 'errors', [str(v)[:200]])[:5])}"
                    attempts = 2
                usage |= {"input": (v.tokens_in or 0) if kind == "ok" else (getattr(v, "tokens_in", 0) or 0),
                          "output": (v.tokens_out or 0) if kind == "ok" else (getattr(v, "tokens_out", 0) or 0)}
                info = await self.outcome(run_id)
                rec = {"turn": self.ex.turn, "plan_turn": inp.n, "thread_ref": inp.thread_ref, "thread": inp.turn.get("thread"), "reason": reason, "output": out, "error": err,
                       "attempts": attempts, "usage": usage, "new_n": len(w.new_events), "recent_n": len(w.context_events), "new_msg_refs": [e.message_id for e in w.new_events]}
                self.records.append(rec)
                for k in ("input", "output", "calls"):
                    usage_sum[k] += usage[k]
                last_out, last_err = (out if out is not None else last_out), err
                write_state(self.ex.run, last={"turn": self.ex.turn, "plan_turn": inp.n, "thread": inp.thread_ref, "reason": reason, "attempts": attempts,
                                               "calls": usage["calls"], "units": len(out["units"]) if out else None,
                                               "facts_proposed": len(out["facts_proposed"]) if out else None, "error": err, **info})
                if reason not in IMMEDIATE:
                    break               # lượt thường xong: phần còn lại chờ nhịp kế (lượt kế hoạch sau của luồng)
            if not progressed or not immediate:
                break
        return replay.AnalysisResult(output=last_out if last_out is not None else empty, usage=usage_sum, attempts=1, error=last_err)

    async def outcome(self, run_id: Any) -> dict[str, Any]:
        from sqlalchemy import text  # noqa: PLC0415

        async with self.r.sm() as db:
            an = (await db.execute(text("SELECT status, errors FROM refinery.analyses WHERE run_id = :r ORDER BY started_at"), {"r": run_id})).all()
            units = (await db.execute(text("SELECT count(*) FROM clean.meaning_units WHERE run_id = :r"), {"r": run_id})).scalar_one()
            states = (await db.execute(text("SELECT state, count(*) FROM refinery.event_state WHERE run_id = :r GROUP BY 1"), {"r": run_id})).all()
        return {"analysis_status": [r.status for r in an], "written_units": int(units), "event_states": {r.state: int(r[1]) for r in states}}


class IngestSink:
    """Đổ tin vào `raw.events` bằng `ingest_message` thật (đúng nơi bus `gh.bridge.inbound` cuối cùng đi tới)."""

    def __init__(self, org: Any) -> None:
        self.org, self.inserted, self.dropped = org, 0, 0

    async def emit(self, stream: str, type: str, payload: dict[str, Any]) -> None:
        from gh.data import ingest  # noqa: PLC0415
        from gh.db import sessionmaker  # noqa: PLC0415

        if (stream, type) != ("gh.bridge.inbound", "message"):
            return
        async with sessionmaker()() as db:
            eid = await ingest.ingest_message(db, self.org, payload)
            await db.commit()
        if eid:
            self.inserted += 1
        else:
            self.dropped += 1

    async def close(self) -> None:
        return None


class StepSeeder(replay.Seeder):
    """`Seeder` của L6 + (1) mở phiên PIN sau khi đăng nhập (ghi Hồ sơ cần PIN); (2) thân `POST /me/profile/items` đúng hợp đồng L4 ({section, text})."""

    @staticmethod
    def PROFILE_ITEM_BODY(predicate: str, text: str) -> dict[str, Any]:  # noqa: N802 — ghi đè thuộc tính lớp của Seeder
        from gh.biz.mem.profile import PREDICATE_SECTION  # noqa: PLC0415

        return {"section": PREDICATE_SECTION[predicate], "text": text}

    async def login(self, email: str, password: str) -> None:
        await super().login(email, password)
        r = await self.c.post("/auth/pin/verify", json={"pin": OWNER["pin"]}, headers={"X-CSRF-Token": self.c.headers["X-CSRF-Token"]})
        if r.status_code >= 400:
            raise SystemExit(f"seed: mở phiên PIN thất bại (HTTP {r.status_code})")


async def do_setup(c: Any) -> None:
    def hdr() -> dict[str, str]:
        t = c.cookies.get("gh_csrf")
        return {"X-CSRF-Token": t} if t else {}

    for n, body in ((1, {"token": SETUP_TOKEN, "language": "vi", "mode": "empty"}), (2, {"token": SETUP_TOKEN, **OWNER}),
                    (3, {"org_name": "Mộc Rang (đo G-KB)", "timezone": "Asia/Ho_Chi_Minh", "currency": "VND", "self_name": "Anh", "bot_calls_me": "Sếp"})):
        r = await c.put(f"/setup/steps/{n}", json=body, headers=hdr())
        if r.status_code != 200:
            raise SystemExit(f"setup bước {n} lỗi HTTP {r.status_code}: {r.text[:300]}")


async def serve(run: Path) -> None:
    cfg = json.loads((run / "config.json").read_text(encoding="utf-8"))
    serve_env(cfg, run)
    import httpx  # noqa: PLC0415
    from sqlalchemy import text  # noqa: PLC0415

    from gh.app import create_app  # noqa: PLC0415
    from gh.data.ingest import channel_row, upsert_group  # noqa: PLC0415
    from gh.db import sessionmaker  # noqa: PLC0415
    from gh.providers.router import Routed  # noqa: PLC0415
    from gh.refinery import _l3_stub as l3  # noqa: PLC0415
    from gh.refinery import clock, runner  # noqa: PLC0415

    ds: Dataset = load_dataset(cfg["dataset"])
    plan = replay.plan_for(ds, "compressed", PlanConfig())
    write_state(run, status="running", plan={"turns_n": len(plan["turns"]), "max_gap_s": plan["max_gap_s"], "mode": plan["mode"]}, _daemon=True)
    ex = Exchange(run, cfg["auto"])

    class ExchangeRouter:
        """Thay `ModelRouter` (chỉ phần pipeline gọi): `generate` = hỏi agent; `embed` = không có embedding."""

        async def generate(self, org_id: Any, *, agent_key: str, purpose: str, messages: list[Any], json_mode: bool = True, temperature: float = 0.2, **kw: Any) -> Any:
            text_ = await ex.ask(purpose, messages)
            chars = sum(len(m.content) for m in messages)
            return Routed(text_, "agent", "agent-step", est_tokens(chars), est_tokens(len(text_)))

        async def embed(self, org_id: Any, texts: list[str]) -> None:
            return None

    captured: list[Any] = []

    async def capture(window: Any, pack: Any, profile: Any, policy: Any, *, call: Any = None, secrets: Any = ()) -> Any:
        try:
            a = await l3.analyze_group_detailed(window, pack, profile, policy, call=call, secrets=secrets)
        except Exception as e:
            captured.append(("err", e, window))
            raise
        captured.append(("ok", a, window))
        return a

    runner.AGY_BACKGROUND_TIMEOUT_S = 1e9          # `turn()` đặt wait_for theo hằng này: chờ agent không được coi là hết giờ model
    app = create_app()
    try:
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://step/api/v1", timeout=120) as client:
                await do_setup(client)
                org = app.state.org_id
                sm = sessionmaker()
                # nhóm có sẵn TRƯỚC khi seed nhãn bối cảnh (replay chỉ gán nhãn cho nhóm đã có tin tới lượt đầu)
                async with sm() as db:
                    ch = await channel_row(db, org, "zalo")
                    for gk, gname in ds.groups.items():
                        await upsert_group(db, org, ch, gk, gname)
                    await db.commit()
                refinery = runner.Refinery(sm, app.state.redis, ExchangeRouter(), app.state.bus, time_budget_s=1e9, part_worst_s=0.0, analyzer=capture)  # type: ignore[arg-type]
                an = PipelineAnalyzer(refinery, org, ex, captured)
                sink = IngestSink(org)
                summary = await replay.run_replay(ds, run, mode="compressed", analyzer=an, sink=sink, seeder=StepSeeder(client), contexts=cfg["contexts"],
                                                  engine=replay.FakeMediaEngine(), label="step")
                # idmap: m_<uuid kho thô> → id dữ liệu (bộ chấm chuẩn hoá id trích dẫn qua đây)
                idmap = json.loads((run / "idmap.json").read_text(encoding="utf-8"))
                async with sm() as db:
                    for r in (await db.execute(text("SELECT id, external_msg_id FROM raw.events WHERE org_id = :o"), {"o": org})).all():
                        idmap[f"m_{r.id}"] = r.external_msg_id
                write_json(run / "idmap.json", idmap)
                write_records(run, an.records, idmap, summary)
                clock.reset()
                write_state(run, status="done", pending=None, ingested=sink.inserted, dropped=sink.dropped, plan_turns=summary["turns_n"], pipeline_turns=len(an.records),
                            seeded=summary["seeded"], _daemon=True)
    except BaseException as e:
        write_state(run, status="error", error=f"{type(e).__name__}: {str(e)[:400]}", _daemon=True)
        raise


def write_records(run: Path, records: list[dict[str, Any]], idmap: dict[str, str], summary: dict[str, Any]) -> None:
    """Ghi lại `outputs.jsonl`/`tokens.jsonl` THEO TỪNG LƯỢT PIPELINE (replay chỉ ghi mỗi lượt kế hoạch một bản ghi, nhưng một lượt kế hoạch có thể là
    nhiều lượt pipeline: an toàn/cổng + thường). `turn` = số lượt pipeline (trùng `turn_NNN`), `plan_turn` = lượt kế hoạch của replay."""
    ex = [json.loads(x) for x in (run / "exchange.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()] if (run / "exchange.jsonl").exists() else []
    chars: dict[int, int] = {}
    wait: dict[int, float] = {}
    for e in ex:
        chars[e["turn"]] = chars.get(e["turn"], 0) + e["prompt_chars"]
        wait[e["turn"]] = wait.get(e["turn"], 0.0) + e["wait_s"]
    with open(run / "outputs.jsonl", "w", encoding="utf-8") as fo, open(run / "tokens.jsonl", "w", encoding="utf-8") as ft:
        for r in records:
            fo.write(json.dumps({"turn": r["turn"], "plan_turn": r["plan_turn"], "thread_ref": r["thread_ref"], "thread": r["thread"], "output": r["output"], "error": r["error"],
                                 "attempts": r["attempts"], "reason": r["reason"], "new_ids": [idmap.get(x, x) for x in r["new_msg_refs"]]}, ensure_ascii=False) + "\n")
            ft.write(json.dumps({"turn": r["turn"], "plan_turn": r["plan_turn"], "thread": r["thread"], "new_n": r["new_n"], "recent_n": r["recent_n"], "chars": chars.get(r["turn"], 0),
                                 "reason": r["reason"], "usage": r["usage"], "latency_s": wait.get(r["turn"], 0.0), "attempts": r["attempts"], "mode": summary["mode"]}) + "\n")
    write_json(run / "summary.json", {**summary, "pipeline_turns": len(records), "plan_turns": summary["turns_n"]})


# ─── export + stats ─────────────────────────────────────────────────────────────────────────────────────────────────────

def db_counts(cfg: dict[str, Any]) -> dict[str, Any]:
    psycopg = _psycopg()
    q = {"analyses": "SELECT status, count(*) FROM refinery.analyses GROUP BY 1", "meaning_units": "SELECT count(*) FROM clean.meaning_units",
         "evidence": "SELECT count(*) FROM clean.evidence", "facts_by_status": "SELECT status, count(*) FROM mem.facts GROUP BY 1 ORDER BY 1",
         "facts_by_predicate": "SELECT predicate, count(*) FROM mem.facts GROUP BY 1 ORDER BY 2 DESC",
         "event_state": "SELECT state, count(*) FROM refinery.event_state GROUP BY 1 ORDER BY 1", "media_texts": "SELECT status, count(*) FROM clean.media_texts GROUP BY 1"}
    out: dict[str, Any] = {}
    with psycopg.connect(f"{cfg['pg']}/{cfg['db']}") as c:
        for k, sql in q.items():
            rows = c.execute(sql).fetchall()
            out[k] = int(rows[0][0]) if k in ("meaning_units", "evidence") else {str(r[0]): int(r[1]) for r in rows}
    return out


def cmd_export(a: argparse.Namespace) -> None:
    import gkb_score  # noqa: PLC0415

    run = Path(a.run or Path(a.out).parent).resolve()
    out = Path(a.out).resolve()
    refuse_inside_repo(out)
    st = read_state(run)
    if st["status"] != "done":
        print(f"step: cảnh báo — lần chạy chưa DONE (trạng thái {st['status']}); tệp outputs.jsonl có thể thiếu lượt", file=sys.stderr)
    cfg = json.loads((run / "config.json").read_text(encoding="utf-8"))
    r = gkb_score.Run(run, load_dataset(cfg["dataset"]))

    def js(u: dict[str, Any]) -> dict[str, Any]:
        return {k: (sorted(v) if isinstance(v, set) else v) for k, v in u.items()}

    write_json(out, {"format": "gkb-step-signals/1", "dataset": st.get("dataset"), "turns": r.turns, "contract_errors": r.contract_errors,
                     "contract_retries": r.contract_retries, "turn_errors": r.turn_errors, "unreadable": r.unreadable,
                     "units": [js(u) for u in r.units], "facts": [js(f) for f in r.facts], "db": db_counts(cfg),
                     "note": "định dạng nội bộ của gkb_score.Run; chấm đầy đủ: gkb_score.py --runs <run_dir> --key <đáp án ngoài repo>"})
    print(f"đã ghi {out}: {len(r.units)} đơn vị ({len(r.signals)} có kind), {len(r.facts)} sự thật đề xuất, {r.contract_errors} lỗi hợp đồng / {r.turns} lượt")


def cmd_stats(a: argparse.Namespace) -> None:
    run = Path(a.out).resolve()
    rows = [json.loads(x) for x in (run / "exchange.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()] if (run / "exchange.jsonl").exists() else []
    if not rows:
        print("step: chưa có lượt gọi model nào")
        return
    from common import quantiles  # noqa: PLC0415

    tin = [float(r["est_tokens_in"]) for r in rows]
    tout = [float(r["est_tokens_out"]) for r in rows]
    st = read_state(run)
    print(json.dumps({"status": st["status"], "turns_planned": (st.get("plan") or {}).get("turns_n"), "calls": len(rows), "by_kind": {k: sum(1 for r in rows if r["kind"] == k) for k in sorted({r["kind"] for r in rows})},
                      "est_tokens_in": {"sum": sum(tin), **quantiles(tin)}, "est_tokens_out": {"sum": sum(tout), "mean": sum(tout) / len(tout)},
                      "chars_per_token_assumed": CHARS_PER_TOKEN}, ensure_ascii=False, indent=2))


def _cli() -> None:
    ap = argparse.ArgumentParser(description="Bộ chạy G-KB từng bước (agent đóng vai model) — xem docstring")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("init")
    p.add_argument("--dataset", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--contexts", help="JSON {Gk: mã bối cảnh}; bỏ trống ⇒ chỉ nhãn trong context.md (nếu có), không có ⇒ bối cảnh chưa xác nhận")
    p.add_argument("--auto-stub", action="store_true", help="tự trả brain-out rỗng hợp lệ (đếm lượt/kích thước prompt, kiểm hạ tầng); KHÔNG dùng để chấm")
    p.add_argument("--pg", default=os.environ.get("GH_STEP_PG", PG_DEFAULT))
    p.add_argument("--redis-url", default=os.environ.get("GH_STEP_REDIS", REDIS_DEFAULT))
    p.add_argument("--force", action="store_true")
    p = sub.add_parser("next")
    p.add_argument("--out", required=True)
    p = sub.add_parser("submit")
    p.add_argument("answer")
    p = sub.add_parser("export")
    p.add_argument("--out", required=True)
    p.add_argument("--run")
    p = sub.add_parser("stats")
    p.add_argument("--out", required=True)
    p = sub.add_parser("_serve")
    p.add_argument("--out", required=True)
    a = ap.parse_args()
    if a.cmd == "_serve":
        asyncio.run(serve(Path(a.out).resolve()))
    else:
        {"init": cmd_init, "next": cmd_next, "submit": cmd_submit, "export": cmd_export, "stats": cmd_stats}[a.cmd](a)


if __name__ == "__main__":
    _cli()
