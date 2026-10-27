#!/usr/bin/env python3
# ruff: noqa: E501, ASYNC230, ASYNC240, ASYNC110, I001     # script đo một mình: I/O tệp ngắn + poll tệp trong coroutine là chấp nhận được
"""step.py — bộ chạy G-KB TỪNG BƯỚC: một agent Claude đóng vai MODEL trong pipeline thật của Đợt 1 (máy không có Claude CLI/API key).

Dùng CHÍNH pipeline thật: replay (L6: đồng hồ giả, chế độ nén, Seeder, IngestSink) → `raw.events` thật → `GroupTurns.ensure_scanned/plan/turn`
(L2) → `extract.analyze_group_detailed` (L3: dựng prompt, parse hợp đồng, thử lại MỘT lần kèm lỗi) → `verify.check` → ghi `mem.facts`,
`clean.meaning_units`… Chỗ DUY NHẤT khác: `ModelRouter.generate` được thay bằng "model ngoài" — ghi prompt ra tệp, chờ agent ghi câu trả lời.
Đợt 2 (`ClockJobs`): job cron production của Tình báo (`intel_reeval` + `raw_tail`, lừa đảo, generic, lời hứa, câu hỏi, heat, catalog…, lịch lấy từ
`gh.biz.jobs()`) chạy theo GIỜ DỮ LIỆU (`gh.refinery.clock` giả) giữa các tin/lượt và tới bản tin sáng sau `now` cuối; hook `market_signal_capture`
sau mỗi lượt; `export` (gkb-step-signals/2) thêm `biz.alerts`, lịch sử 🔴 đã đẩy, bản tin (`digest.build`, owner).

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
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import replay  # noqa: E402
from clockplan import PlanConfig  # noqa: E402
from common import Dataset, api_dir, load_dataset, parse_context, parse_member_counts, refuse_inside_repo, write_json  # noqa: E402

CHARS_PER_TOKEN = 3.0          # ƯỚC TÍNH thô cho tiếng Việt + JSON (không có tokenizer cục bộ); sai số ± 25%
PG_DEFAULT = "postgresql://postgres:postgres@localhost:5432"
REDIS_DEFAULT = "redis://localhost:6379/10"
TEMPLATE_LOCK_KEY = 0x6768_5445              # cùng khoá với tests/conftest.py: không migrate chồng lên pytest chạy song song
OWNER = {"display_name": "Anh Cơ", "email": "owner@example.vn", "password": "mat-khau-rat-dai-123", "pin": "246810", "pin_confirm": "246810"}
SETUP_TOKEN = "step-setup-token"
MAX_ROUNDS = 6                 # tối đa lượt pipeline cho MỘT lượt kế hoạch (an toàn/cổng nối nhau + một lượt thường)
IMMEDIATE = ("safety", "gate", "fast")
DRAIN_ROUNDS = 8               # vòng thử lại tin còn chờ sau lượt kế hoạch cuối (mỗi vòng tiến đồng hồ qua RETRY_BACKOFF)
WRITE_ERROR_CODES = ("write_failed", "unit_write_failed", "unit_write_failed_safety", "facts_write_failed")
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
           "intel_settings": json.loads(a.intel_settings) if a.intel_settings else None,
           "max_lag_min": a.max_lag_min, "identity": not a.no_identity,
           "profile_items": sum(len(v) for v in ctx["profile"].values()), "context_groups": sorted(contexts or ctx["groups"])}
    write_json(run / "config.json", cfg)
    write_state(run, status="starting", seq=0, pending=None, turn=0, calls=0, error=None, dataset=ds.name, messages=len(ds.msgs))
    log = open(run / "daemon.log", "ab")  # noqa: SIM115
    proc = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "_serve", "--out", str(run)], stdout=log, stderr=subprocess.STDOUT,  # noqa: S603
                            stdin=subprocess.DEVNULL, start_new_session=True, close_fds=True)
    write_state(run, pid=proc.pid)
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
    if st["status"] == "waiting" and st.get("turn") == turn and st.get("kind") != "retry":
        print(json.dumps({"turn": turn, "result": f"cùng lượt, lần gọi model kế ({st.get('kind')})"}, ensure_ascii=False))
    elif st["status"] == "waiting" and st.get("turn") == turn:
        print(json.dumps({"turn": turn, "result": "gọi lại (câu trả lời trước sai hợp đồng hoặc JSON hỏng; pipeline thật thử lại MỘT lần kèm lỗi)"}, ensure_ascii=False))
    else:
        last = st.get("last") or {}
        print(json.dumps(last, ensure_ascii=False))
        if last.get("write_error") or "failed" in (last.get("analysis_status") or []):
            print(f"step: LƯỢT LỖI GHI — {last.get('write_error') or 'failed'} (model trả đúng hợp đồng nhưng pipeline không ghi trọn; xem daemon.log)", file=sys.stderr)
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

    async def ask(self, purpose: str, messages: list[Any], hint: str | None = None) -> str:
        if self.fresh:
            self.turn, self.k, self.fresh = self.turn + 1, 0, False
        self.k += 1
        self.calls += 1
        tail = purpose.rsplit(".", 1)[-1]
        kind = hint or (tail if tail in ("d1", "d2") else ("extract" if self.k == 1 else "retry"))
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
        self.clock: ClockJobs | None = None        # Đợt 2: job đồng hồ + hook sau lượt (None ⇒ chỉ pipeline trích xuất như Đợt 1)

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
                    mkind = jobs.PRIMARY_KIND[row.kind] if row.kind == "voice" else "description"
                    await jobs._write_text(db, ev, att, kind=mkind, status="ok", text_=res["text"], engine="fake", version="fake/1")
                    # H3: nối đúng `media.jobs._process` — quét luật (a) trên chữ media (`prefilter_port.scan_text`, mặc định của `Deps.scan`);
                    # trúng ⇒ `_escalate_fast` (tin chạy đường nhanh). Trước đây harness bỏ hai bước này ⇒ tin thoại/ảnh nguy hiểm không có đường nhanh.
                    if res["text"] and jobs.prefilter_port.scan_text(res["text"], jobs.prefilter_port.source_field_for(mkind)):
                        await jobs._escalate_fast(db, ev)
                else:
                    await jobs._write_text(db, ev, att, kind=jobs.PRIMARY_KIND[row.kind], status="failed", error="fake_unreadable", engine="fake", version="fake/1")
                await db.commit()

    async def analyze(self, inp: replay.TurnInput) -> replay.AnalysisResult:
        """Một lượt KẾ HOẠCH của replay = các lượt pipeline thật cần để xả tin của luồng đó: lượt an toàn/cổng/nhanh chạy NGAY (nhận tin ứng viên +
        ngữ cảnh), rồi MỘT lượt thường nhận phần tồn đọng — đúng thứ tự `GroupTurns.run` (phần "ứng viên đến trong lúc chạy"). Mỗi lượt pipeline có
        bản ghi riêng (`self.records`) để chấm; phần còn quá 60 tin / lượt lỗi hợp đồng (đang lùi nhịp) chờ lượt kế hoạch sau của luồng."""
        from gh.refinery import clock

        now = datetime.fromisoformat(inp.now_iso)
        clock.freeze(now)                                      # giờ của lượt theo DỮ LIỆU (replay: clockplan.data_time), không phải đồng hồ nén
        if self.clock is not None:
            await self.clock.advance(now)                      # job đồng hồ tới hạn trước lượt (tin đến sau mốc cuối đã được job thấy ở IngestSink)
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
                rec = await self._pipeline_turn(ts, reason, inp.thread_ref, inp.n, inp.turn.get("thread"))
                if rec is None:
                    continue            # lượt không gọi model (không còn tin tới hạn / đang lùi nhịp)
                progressed = True
                immediate = immediate or reason in IMMEDIATE
                for k in ("input", "output", "calls"):
                    usage_sum[k] += rec["usage"][k]
                last_out, last_err = (rec["output"] if rec["output"] is not None else last_out), rec["error"]
                if reason not in IMMEDIATE:
                    break               # lượt thường xong: phần còn lại chờ nhịp kế (lượt kế hoạch sau của luồng)
            if not progressed or not immediate:
                break
        return replay.AnalysisResult(output=last_out if last_out is not None else empty, usage=usage_sum, attempts=1, error=last_err)

    async def _pipeline_turn(self, ts: Any, reason: str, thread_ref: str, plan_turn: int | None, thread: str | None) -> dict[str, Any] | None:
        """MỘT lượt pipeline thật (`GroupTurns.turn`) + bản ghi để chấm; None nếu lượt không gọi model."""
        from gh.refinery import clock
        from gh.refinery.runner import RunStats

        g = self.r.groups
        self.captured.clear()
        self.ex.begin_turn(thread_ref)
        calls0 = self.ex.calls
        run_id = await self.r.start_run(self.org, "step")
        st = RunStats(run_id, "step")
        await g.turn(self.org, st, ts, reason)
        if self.clock is not None:
            await self.clock.after_turn(st.unit_ids, run_id, clock.now())
        if not self.captured:
            return None
        kind, v, w = self.captured[-1]
        usage = {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0, "model": "agent-step", "calls": self.ex.calls - calls0}
        out: Any = None
        err: str | None = None
        if kind == "ok":
            out = v.out.model_dump(mode="json") | {"thread_ref": thread_ref}
            attempts = v.attempts
        else:
            err = f"{type(v).__name__}:{';'.join(getattr(v, 'errors', [str(v)[:200]])[:5])}"
            attempts = 2
        usage |= {"input": (v.tokens_in or 0) if kind == "ok" else (getattr(v, "tokens_in", 0) or 0),
                  "output": (v.tokens_out or 0) if kind == "ok" else (getattr(v, "tokens_out", 0) or 0)}
        info = await self.outcome(run_id)
        werr = write_error(info)
        rec = {"turn": self.ex.turn, "plan_turn": plan_turn, "thread_ref": thread_ref, "thread": thread, "reason": reason, "output": out, "error": err,
               "write_error": werr, "attempts": attempts, "usage": usage, "new_n": len(w.new_events), "recent_n": len(w.context_events),
               "new_msg_refs": [e.message_id for e in w.new_events]}
        self.records.append(rec)
        write_state(self.ex.run, last={"turn": self.ex.turn, "plan_turn": plan_turn, "thread": thread_ref, "reason": reason, "attempts": attempts,
                                       "calls": usage["calls"], "units": len(out["units"]) if out else None,
                                       "facts_proposed": len(out["facts_proposed"]) if out else None, "error": err, "write_error": werr, **info})
        return rec

    async def drain(self, max_rounds: int = DRAIN_ROUNDS) -> int:
        """Sau lượt kế hoạch cuối: tin còn `pending` (lượt ghi lỗi/lùi nhịp, tin đuôi) được thử lại như scheduler thật — tiến đồng hồ qua
        `RETRY_BACKOFF` rồi chạy mọi luồng đến hạn, tới khi hết tin chờ nhận được hoặc không còn tiến triển. Bản ghi có `plan_turn = None`,
        `reason = "drain:<lý do>"`. Trả số lượt pipeline đã chạy."""
        from gh.refinery import clock
        from gh.refinery.runner import RETRY_BACKOFF

        g = self.r.groups
        back = {v: k for k, v in self.tk.items()}
        n = 0
        for _ in range(max_rounds):
            clock.advance(RETRY_BACKOFF + timedelta(minutes=1))
            if self.clock is not None:
                await self.clock.advance(clock.now())
            while await g.ensure_scanned(self.org) >= 500:
                pass
            progressed = False
            for ts, reason in await g.plan(self.org, manual=True):
                gk = back.get(ts.thread_key)
                if await self._pipeline_turn(ts, f"drain:{reason}", f"g:{gk}" if gk else ts.thread_key, None, gk) is not None:
                    progressed, n = True, n + 1
            if not progressed:
                break
        return n

    async def outcome(self, run_id: Any) -> dict[str, Any]:
        from sqlalchemy import text  # noqa: PLC0415

        async with self.r.sm() as db:
            an = (await db.execute(text("SELECT status, errors FROM refinery.analyses WHERE run_id = :r ORDER BY started_at"), {"r": run_id})).all()
            units = (await db.execute(text("SELECT count(*) FROM clean.meaning_units WHERE run_id = :r"), {"r": run_id})).scalar_one()
            states = (await db.execute(text("SELECT state, count(*) FROM refinery.event_state WHERE run_id = :r GROUP BY 1"), {"r": run_id})).all()
        codes = [e.get("code") for r in an for e in (r.errors or []) if isinstance(e, dict) and e.get("code") in WRITE_ERROR_CODES]
        return {"analysis_status": [r.status for r in an], "written_units": int(units), "event_states": {r.state: int(r[1]) for r in states},
                "write_errors": codes}


def write_error(info: dict[str, Any]) -> str | None:
    """Lượt ghi lỗi (cả lượt `write_failed` hoặc từng đơn vị/sự thật) ⇒ chuỗi mã lỗi; None = ghi trọn vẹn. Model trả đúng hợp đồng
    nhưng sản phẩm không ghi được vẫn là lượt LỖI — không được hiện `error: null`."""
    codes = info.get("write_errors") or []
    if not codes:
        return None
    return ",".join(f"{c}×{codes.count(c)}" for c in dict.fromkeys(codes))


# ─── Đợt 2: job đồng hồ + hook sau lượt, chạy theo GIỜ DỮ LIỆU ─────────────────────────────────────────────────────────

#: Job định kỳ của production (`gh.biz.jobs()`, đúng lịch cron đã đăng ký) đổi `biz.alerts` hoặc nuôi tín hiệu. Lịch lấy từ registry, không chép tay.
CLOCK_JOBS = ("intel_reeval", "intel_project_disputes", "intel_fraud", "intel_generic", "intel_promises", "intel_questions_clock",
              "intel_questions_scan", "intel_units", "intel_outcomes", "intel_heat_scan", "matches_recompute", "mem_heat_weekly")
#: Job `intel_*` của production CỐ Ý không chạy (lý do bên dưới). Mọi job `intel_*` khác PHẢI nằm trong CLOCK_JOBS — test canh
#: (`test_every_intel_job_is_run_or_explicitly_skipped`) để job mới (vd `intel_units` của T12) không bị bỏ sót im lặng.
SKIPPED_INTEL_JOBS = ("intel_delivery", "intel_weekly", "intel_m1_nightly")
#: Không chạy (lý do): intel_delivery/shadow_pack/intel_weekly — chỉ xếp tin ra kênh/đo lường, không đổi mức; intel_m1_nightly (v0.1.77, P4) — ghi
#: m1_passed_gates theo Đúng/Sai thật của Sếp, harness không có ⇒ không chạy (đo G-KB luôn ở chạy thử, không tự bật đẩy thật); (🔴 đã đẩy đọc ở `pushed_at`);
#: watchdog_tick/backup_flush — giám sát bridge thật (harness không có nhịp tim ⇒ báo động giả); duty — agent trực kênh (gọi model, ngoài Tình báo);
#: mem_consolidate — cửa sổ giờ + `now()` của SQL theo giờ thật. Hook sau sàng lọc: chỉ `market_signal_capture` (cầu → `catalog_match`).
POST_TURN_HOOKS = ("market_signal_capture",)
TICK_MAX_GAP = timedelta(minutes=int(os.environ.get("GH_STEP_TICK_MAX_MIN", "60")))   # im lặng dài: vẫn có mốc ít nhất mỗi chừng này
MAX_TICKS_PER_ADVANCE = 3000
ALERT_COLS = ("code", "kind", "level", "gate", "candidate", "delivery", "lane", "status", "case_key", "pushed_at", "would_push_at")
CronSpec = dict[str, Any]


def _floor_min(t: datetime) -> datetime:
    return t.replace(second=0, microsecond=0)


def _cron_ok(spec: CronSpec, t: datetime) -> bool:
    """`t` (giờ VN, đúng phút) khớp lịch arq `spec` (hour/minute/day/month/weekday; khoá khác — timeout… — bỏ qua)."""
    vals = {"month": t.month, "day": t.day, "weekday": t.weekday(), "hour": t.hour, "minute": t.minute}
    for k, v in vals.items():
        want = spec.get(k)
        if want is not None and v not in ({want} if isinstance(want, int) else want):
            return False
    return True


def fire_times(spec: CronSpec, after: datetime, upto: datetime, tz: Any) -> list[datetime]:
    """Các phút cron của `spec` trong (after, upto] — theo giờ `tz` của worker (VN), trả giờ có múi."""
    out: list[datetime] = []
    h = _floor_min(after.astimezone(tz)).replace(minute=0)
    end = upto.astimezone(tz)
    hours, minutes = spec.get("hour"), spec.get("minute")
    mins = sorted({minutes} if isinstance(minutes, int) else (minutes if minutes is not None else range(60)))
    while h <= end:
        if hours is None or h.hour in ({hours} if isinstance(hours, int) else hours):
            for m in mins:
                x = h.replace(minute=m)
                if after < x <= end and _cron_ok(spec, x):
                    out.append(x)
        h += timedelta(hours=1)
    return out


class ClockJobs:
    """Thay arq cron + bus hook của worker production trong harness: đồng hồ là `gh.refinery.clock` (giả, GH_ENV=eval) đặt theo GIỜ DỮ LIỆU.

    `advance(to)` chạy mọi job (CLOCK_JOBS) có phút cron rơi vào (con trỏ, to] — mỗi "mốc" (tick) là một phút cron: job dày (mỗi phút/5 phút)
    gộp về mốc đầu + mốc cuối của khoảng, job theo giờ cố định (heat 01:55/07:55/19:55…) chạy ĐỦ từng lần; thêm mốc tại `next_eval_at` của tín
    hiệu mở (hạn tới ⇒ leo cổng/quá cửa sổ) và ít nhất mỗi `TICK_MAX_GAP` khi im lặng dài. Mỗi mốc: `ensure_scanned` (bước quét luật (a) của
    scheduler, chạy mỗi 5 s ở production) rồi các job tới hạn theo thứ tự registry. Ghi `alerts_log.jsonl` (đổi mức/ngăn/trạng thái, 🔴 đã đẩy)."""

    def __init__(self, run: Path, refinery: Any, org: Any, ex: Exchange, ctx: dict[str, Any], start: datetime) -> None:
        from gh import biz  # noqa: PLC0415
        from gh.worker import WORKER_TZ  # noqa: PLC0415

        self.run, self.r, self.org, self.ex, self.ctx, self.tz = run, refinery, org, ex, ctx, WORKER_TZ
        reg = {fn.__name__: (fn, kw) for fn, kw in biz.jobs()}
        missing = [n for n in CLOCK_JOBS if n not in reg]
        if missing:
            raise SystemExit(f"step: registry job production không còn {missing} — cập nhật CLOCK_JOBS")
        override = {"intel_fraud": _intel_fraud_untimed, "mem_heat_weekly": _mem_heat_at_clock}
        self.jobs: list[tuple[str, Any, CronSpec]] = [(n, override.get(n, reg[n][0]), reg[n][1]) for n in reg if n in CLOCK_JOBS]
        self.hooks = [h for h in biz.hooks() if h.name in POST_TURN_HOOKS]
        self.cursor = start
        self.ticks = 0
        self.deadline_ticks = 0
        self.runs: dict[str, int] = {n: 0 for n, _, _ in self.jobs}
        self.results: dict[str, dict[str, int]] = {n: {} for n, _, _ in self.jobs}
        self.errors: dict[str, int] = {}
        self.secs: dict[str, float] = {"ensure_scanned": 0.0, "observe": 0.0}
        self.hook_runs = 0
        self.seen: dict[str, dict[str, Any]] = {}
        self.first_seen: dict[str, str] = {}
        self.log = open(run / "alerts_log.jsonl", "a", encoding="utf-8")  # noqa: SIM115

    async def _next_eval(self) -> datetime | None:
        from sqlalchemy import text  # noqa: PLC0415

        async with self.r.sm() as db:
            return (await db.execute(text("SELECT min(next_eval_at) FROM biz.alerts WHERE org_id = :o AND status IN ('open', 'acknowledged') "
                                          "AND next_eval_at > :c"), {"o": self.org, "c": self.cursor})).scalar_one_or_none()

    async def advance(self, to: datetime) -> None:
        from gh.refinery import clock  # noqa: PLC0415

        static: set[datetime] = set()
        for _name, _fn, spec in self.jobs:
            fs = fire_times(spec, self.cursor, to, self.tz)
            if fs:
                static.update(fs if spec.get("hour") is not None else (fs[0], fs[-1]))
        g = _floor_min(self.cursor + TICK_MAX_GAP)
        while g < to:
            static.add(g)
            g += TICK_MAX_GAP
        queue = sorted(static)
        n = 0
        while n < MAX_TICKS_PER_ADVANCE:
            queue = [q for q in queue if q > self.cursor]
            d = await self._next_eval()
            dl = None
            if d is not None:
                dl = _floor_min(d) + (timedelta(minutes=1) if d != _floor_min(d) else timedelta(0))
                if not self.cursor < dl <= to or (queue and queue[0] <= dl):
                    dl = None
            if dl is None and not queue:
                break
            t = dl if dl is not None else queue[0]
            self.deadline_ticks += dl is not None
            await self.tick(t)
            n += 1
        else:
            print(f"step: cảnh báo — quá {MAX_TICKS_PER_ADVANCE} mốc đồng hồ trong một khoảng tới {to.isoformat()}; bỏ phần còn lại", flush=True)
        clock.freeze(to)
        if to > self.cursor:
            self.cursor = to

    async def tick(self, t: datetime) -> None:
        import traceback  # noqa: PLC0415

        from gh.refinery import clock  # noqa: PLC0415

        clock.freeze(t)
        t0 = time.monotonic()
        while await self.r.groups.ensure_scanned(self.org) >= 500:
            pass
        self.secs["ensure_scanned"] += time.monotonic() - t0
        for name, fn, spec in self.jobs:
            if not fire_times(spec, self.cursor, t, self.tz):
                continue
            self.ex.begin_turn(f"job:{name}")
            calls0 = self.ex.calls
            t0 = time.monotonic()
            try:
                res = await fn(self.ctx)
            except Exception as e:  # noqa: BLE001 — như worker: job lỗi được ghi + đếm, lượt đồng hồ chạy tiếp (traceback ra daemon.log)
                self.errors[name] = self.errors.get(name, 0) + 1
                print(f"step: job {name} lỗi lúc {t.isoformat()}: {type(e).__name__}\n{traceback.format_exc()[-2000:]}", flush=True)
                continue
            finally:
                self.secs[name] = self.secs.get(name, 0.0) + time.monotonic() - t0
            self.runs[name] += 1
            for k, v in (res or {}).items() if isinstance(res, dict) else ():
                if isinstance(v, int):
                    self.results[name][k] = self.results[name].get(k, 0) + v
            if self.ex.calls > calls0:
                write_state(self.run, last={"turn": self.ex.turn, "job": name, "calls": self.ex.calls - calls0, "at": t.astimezone(self.tz).isoformat()})
        self.cursor = t
        self.ticks += 1
        t0 = time.monotonic()
        await self.observe(t, "tick")
        self.secs["observe"] += time.monotonic() - t0
        if self.ticks % 50 == 0:
            write_state(self.run, clock={**self.stats(), "at": t.astimezone(self.tz).isoformat()})

    async def after_turn(self, unit_ids: list[str], run_id: Any, at: datetime) -> None:
        """Hook sau sàng lọc (`gh.clean.ready` ở production, mỗi lượt có đơn vị mới) — gọi đúng `Hook.fn` đã đăng ký."""
        from gh.biz.hooks import HookCtx  # noqa: PLC0415

        if unit_ids:
            import uuid  # noqa: PLC0415

            hc = HookCtx(org_id=self.org, unit_ids=[uuid.UUID(u) for u in unit_ids], run_id=run_id, sm=self.r.sm, redis=self.r.redis,
                         bus=self.r.bus, router=self.ctx["model_router"])
            for h in self.hooks:
                await h.fn(hc)
                self.hook_runs += 1
        await self.observe(at, "turn")

    async def observe(self, at: datetime, why: str) -> None:
        """So trạng thái `biz.alerts` với lần trước: tín hiệu mới / đổi mức-ngăn-trạng thái / 🔴 đẩy (pushed_at mới) / 🔴 bóng (would_push_at mới)."""
        ats = at.astimezone(self.tz).isoformat()            # mọi mốc trong nhật ký theo giờ VN
        for a in await alerts_snapshot(self.r.sm, self.org):
            old = self.seen.get(a["id"])
            if old is None:
                self.first_seen[a["id"]] = ats
            ch = {k: [old.get(k) if old else None, a[k]] for k in ALERT_COLS if old is None or old.get(k) != a[k]}
            if not ch:
                continue
            self.seen[a["id"]] = a
            ev = {"at": ats, "by": why, "id": a["id"], "code": a["code"], "kind": a["kind"], "event": "new" if old is None else "change", "changes": ch}
            self.log.write(json.dumps(ev, ensure_ascii=False) + "\n")
            for col, typ in (("pushed_at", "push"), ("would_push_at", "shadow_push")):
                if a[col] and (old is None or old.get(col) != a[col]):
                    self.log.write(json.dumps({"at": ats, "by": why, "id": a["id"], "code": a["code"], "kind": a["kind"], "event": typ,
                                               "level": a["level"], "gate": a["gate"], "case_key": a["case_key"], col: a[col]}, ensure_ascii=False) + "\n")
        self.log.flush()

    def stats(self) -> dict[str, Any]:
        return {"ticks": self.ticks, "deadline_ticks": self.deadline_ticks, "tick_max_gap_min": TICK_MAX_GAP.total_seconds() / 60,
                "job_runs": self.runs, "job_results": self.results, "job_errors": self.errors, "hook_runs": self.hook_runs,
                "seconds": {k: round(v, 1) for k, v in self.secs.items()},
                "jobs": [n for n, _, _ in self.jobs], "hooks": [h.name for h in self.hooks]}


async def _intel_fraud_untimed(ctx: dict[str, Any]) -> dict[str, int]:
    """= `fraud.intel_fraud` (cùng `run_and_commit`, cùng caller (d2) `refinery.safety` tier strong, cùng `RedisSeen`) — chỉ bỏ hai trần thời gian
    THẬT (wait_for 240 s / lượt, 30 s quét): agent đóng vai model trả lời (d2) chậm hơn model thật, trần đó sẽ huỷ lượt và quét lại vô hạn."""
    from sqlalchemy import text  # noqa: PLC0415

    from gh.biz.intel.detectors import fraud  # noqa: PLC0415
    from gh.db import sessionmaker  # noqa: PLC0415
    from gh.refinery import budget, clock  # noqa: PLC0415
    from gh.refinery.extract import router_caller  # noqa: PLC0415

    now, sm = clock.now(), sessionmaker()
    seen = fraud.RedisSeen(ctx["redis_bus"])
    res = {"signals": 0}
    async with sm() as db:
        orgs = (await db.execute(text("SELECT id FROM core.organizations"))).scalars().all()
    for org in orgs:
        call = router_caller(ctx["model_router"], org, purpose=budget.PURPOSE[budget.KIND_SAFETY], tier="strong")
        async with sm() as db:
            res["signals"] += len(await fraud.run_and_commit(db, org, now, call=call, seen=seen, budget_s=1e9))
    return res


async def _mem_heat_at_clock(ctx: dict[str, Any]) -> dict[str, int]:
    """= `job_heat.mem_heat_weekly`, nhưng truyền `now=clock.now()` cho `heat.rebuild` (job production để `now` mặc định = giờ thật)."""
    from sqlalchemy import text  # noqa: PLC0415

    from gh.biz.mem import heat  # noqa: PLC0415
    from gh.db import sessionmaker  # noqa: PLC0415
    from gh.refinery import clock  # noqa: PLC0415

    sm = sessionmaker()
    tot: dict[str, int] = {}
    async with sm() as db:
        orgs = (await db.execute(text("SELECT id FROM core.organizations"))).scalars().all()
    for org in orgs:
        async with sm() as db:
            for k, v in (await heat.rebuild(db, org, now=clock.now())).items():
                tot[k] = tot.get(k, 0) + v
            await db.commit()
    return tot


async def alerts_snapshot(sm: Any, org: Any) -> list[dict[str, Any]]:
    """Trạng thái `biz.alerts` (mọi loại, cả system) — id tin gốc để thô (uuid), `export` đổi sang mã tin dữ liệu qua idmap."""
    from sqlalchemy import text  # noqa: PLC0415

    async with sm() as db:
        rows = (await db.execute(text("""
            SELECT a.id, a.code, a.alert_type, a.kind, a.detector, a.level, a.gate, a.candidate, a.delivery, a.lane, a.status, a.case_key,
                   a.privacy, a.reasons, a.title, a.pushed_at, a.would_push_at, a.capped_at, a.due_at, a.next_eval_at, a.raw_event_ids,
                   a.evidence, a.thread_key, g.external_id AS group_ext
            FROM biz.alerts a LEFT JOIN core.groups g ON g.id = a.group_id WHERE a.org_id = :o ORDER BY a.created_at, a.code"""), {"o": org})).all()

    def iso(v: Any) -> str | None:
        return v.isoformat() if v is not None else None

    out = []
    for r in rows:
        ev = r.evidence if isinstance(r.evidence, list) else []
        out.append({"id": str(r.id), "code": r.code, "alert_type": r.alert_type, "kind": r.kind, "detector": r.detector, "level": r.level, "gate": r.gate,
                    "candidate": r.candidate, "delivery": r.delivery, "lane": r.lane, "status": r.status, "case_key": r.case_key, "privacy": r.privacy,
                    "reasons": list(r.reasons or []), "title": r.title, "pushed_at": iso(r.pushed_at), "would_push_at": iso(r.would_push_at),
                    "capped_at": iso(r.capped_at), "due_at": iso(r.due_at), "next_eval_at": iso(r.next_eval_at), "thread_key": r.thread_key,
                    "group": r.group_ext, "raw_event_ids": [str(x) for x in (r.raw_event_ids or [])],
                    "evidence_types": sorted({str(e.get("type")) for e in ev if isinstance(e, dict)})})
    return out


async def build_digest(sm: Any, org: Any, at: datetime) -> dict[str, Any]:
    """`digest.build(audience='owner', only_new=False)` lúc `at` (harness không chạy `intel_delivery` nên không có dấu `digest_at` của bản tin trước)."""
    from dataclasses import asdict  # noqa: PLC0415

    from gh.biz.intel import digest  # noqa: PLC0415

    async with sm() as db:
        d = await digest.build(db, org, at, audience="owner", only_new=False)
    return {"at": at.isoformat(), "audience": "owner", "only_new": False, "counts": d.counts, "system_question": d.system_question,
            "items": [{k: v for k, v in asdict(it).items() if k != "state_at_build"} for it in d.items]}


def next_morning_digest(after: datetime, tz: Any) -> datetime:
    """Mốc bản tin sáng (khung 07:30 của `gen.briefing.SLOTS`) đầu tiên SAU `after`."""
    from gh.gen.briefing import SLOTS  # noqa: PLC0415

    h, m, _ = SLOTS[0]
    loc = after.astimezone(tz)
    at = loc.replace(hour=h, minute=m, second=0, microsecond=0)
    return at if at > loc else at + timedelta(days=1)


class IngestSink:
    """Đổ tin vào `raw.events` bằng `ingest_message` thật (đúng nơi bus `gh.bridge.inbound` cuối cùng đi tới). Có `clock` (ClockJobs) ⇒ trước mỗi
    tin, chạy job đồng hồ tới giờ gửi GỐC của tin (job ở mốc T chỉ thấy tin đã đến trước T, như production)."""

    def __init__(self, org: Any) -> None:
        self.org, self.inserted, self.dropped = org, 0, 0
        self.clock: ClockJobs | None = None
        self.identity: IdentitySeed | None = None      # H2: đánh dấu nhân viên/khách ngay khi người đó có định danh (trước mọi lượt phân tích)

    async def emit(self, stream: str, type: str, payload: dict[str, Any]) -> None:
        from gh.data import ingest  # noqa: PLC0415
        from gh.db import sessionmaker  # noqa: PLC0415

        if (stream, type) != ("gh.bridge.inbound", "message"):
            return
        if self.clock is not None and payload.get("occurred_at"):
            at = datetime.fromisoformat(str(payload["occurred_at"]).replace("Z", "+00:00"))
            if at.tzinfo is not None and at > self.clock.cursor:
                await self.clock.advance(at)
        async with sessionmaker()() as db:
            eid = await ingest.ingest_message(db, self.org, payload)
            await db.commit()
        if eid:
            self.inserted += 1
            if self.identity is not None:
                await self.identity.on_sender(str(payload.get("sender_external_id") or ""))
        else:
            self.dropped += 1

    async def close(self) -> None:
        return None


class IdentitySeed:
    """H2 — danh tính + Hồ sơ Sếp "khai lúc cài", CHỈ từ `context.md` của bộ dữ liệu (không đáp án, không tin chat). Đi đường production:
    * Kênh quản trị: phiên Zalo của tài khoản `bot` (`core.channel_sessions.account_kind = 'bot'`, như bridge ghi khi đăng nhập) rồi
      `owner_channel.pairing.start_pairing` + `try_pair` (chính hàm ghép mã) với ID tài khoản Sếp trong context.md (`u_…`, thêm `fb_…` làm ID phụ).
      Tin đầu của Sếp trong nhóm ⇒ `ingest._owner_triggers` đặt `core.persons.attrs.is_owner` (đúng đường production, nhận diện theo ID).
    * Nhân viên / khách: `PATCH /profile/{person_id}` {person_type} — hành động "Đánh dấu là nhân viên/khách" của Sếp (F045, khoá `person_type_locked`),
      là nguồn của `generic.hard_owner_side` / `hard_customer` (không ghi fact `staff_of`/`customer_of`: không có đường Sếp-khai nào ghi fact trực tiếp).
    * `member_count` nhóm: chỉ nhóm mà context.md nêu rõ số thành viên (`upsert_group(..., member_count)`); nhóm không nêu để NULL (như cài thật chưa biết).
    Kết quả ghi `identity.json` (cái gì lấy từ đâu)."""

    def __init__(self, ctx: dict[str, Any], client: Any, org: Any, sm: Any, redis: Any) -> None:
        self.owner_ids: list[str] = ctx.get("owner_ids") or []
        self.want: dict[str, str] = {p["id"]: "staff" for p in ctx.get("staff") or []} | {p["id"]: "customer" for p in ctx.get("customers") or []}
        self.c, self.org, self.sm, self.redis = client, org, sm, redis
        self.done: dict[str, str] = {}
        self.failed: dict[str, int] = {}
        self.paired = False
        self.pair_error: str | None = None

    async def pair_owner(self) -> None:
        from datetime import UTC  # noqa: PLC0415

        from sqlalchemy import text  # noqa: PLC0415

        from gh.owner_channel import pairing  # noqa: PLC0415

        if not self.owner_ids:
            self.pair_error = "context.md không nêu ID tài khoản của Sếp"
            return
        ext = self.owner_ids[0]
        alt = next((x for x in self.owner_ids[1:]), None)
        async with self.sm() as db:
            uid = (await db.execute(text("""SELECT u.id FROM core.users u JOIN core.user_roles ur ON ur.user_id = u.id JOIN core.roles r ON r.id = ur.role_id
                                            WHERE u.org_id = :o AND r.code = 'owner' AND u.is_active AND u.deleted_at IS NULL ORDER BY u.created_at LIMIT 1"""),
                                    {"o": self.org})).scalar_one()
            sid = (await db.execute(text("""
                INSERT INTO core.channel_sessions (channel_id, org_id, account_label, state, started_at, external_account, account_kind)
                SELECT id, org_id, 'Bot (đo G-KB)', 'active', now(), 'bot-eval', 'bot' FROM core.channels WHERE org_id = :o AND type = 'zalo'
                RETURNING id"""), {"o": self.org})).scalar_one()
            await db.commit()
        async with self.sm() as db:
            code, _exp = await pairing.start_pairing(db, self.redis, self.org, "zalo", uid)
            await db.commit()
        async with self.sm() as db:
            ok, why = await pairing.try_pair(db, self.redis, self.org, {
                "channel": "zalo", "code": code, "sender_external_id": ext, "sender_alt_id": alt, "session_id": str(sid),
                "occurred_at": datetime.now(UTC).isoformat(), "sender_name": "Sếp"})
        self.paired, self.pair_error = ok, why

    async def on_sender(self, ext: str) -> None:
        kind = self.want.get(ext)
        if kind is None or ext in self.done:
            return
        from sqlalchemy import text  # noqa: PLC0415

        async with self.sm() as db:
            pid = (await db.execute(text("""SELECT pi.person_id FROM core.person_identities pi JOIN core.channels c ON c.id = pi.channel_id
                                            WHERE c.org_id = :o AND pi.external_id = :x"""), {"o": self.org, "x": ext})).scalar_one_or_none()
        if pid is None:
            return
        r = await self.c.patch(f"/profile/{pid}", json={"person_type": kind}, headers={"X-CSRF-Token": self.c.headers.get("X-CSRF-Token", "")})
        if r.status_code >= 400:
            self.failed[ext] = r.status_code
            raise SystemExit(f"seed: đánh dấu {ext} là {kind} lỗi HTTP {r.status_code}: {r.text[:200]}")
        self.done[ext] = kind

    def report(self, member_counts: dict[str, int]) -> dict[str, Any]:
        return {"source": "context.md của bộ dữ liệu (tương đương Sếp khai lúc cài); không dùng đáp án",
                "owner_pairing": {"ids": self.owner_ids, "paired": self.paired, "error": self.pair_error,
                                  "via": "core.channel_sessions(account_kind=bot) + owner_channel.pairing.start_pairing/try_pair; attrs.is_owner đặt bởi ingest._owner_triggers"},
                "person_type": {"via": "PATCH /profile/{id} person_type (Sếp đánh dấu)", "staff_in_context": sum(1 for v in self.want.values() if v == "staff"),
                                "customers_in_context": sum(1 for v in self.want.values() if v == "customer"),
                                "marked": dict(self.done), "never_seen_in_chat": sorted(set(self.want) - set(self.done))},
                "member_count": {"via": "context.md (chỉ nhóm nêu rõ số thành viên)", "groups": member_counts}}


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
    pcfg = PlanConfig(max_lag_s=(cfg.get("max_lag_min") or 0) * 60 or None)
    plan = replay.plan_for(ds, "compressed", pcfg)
    write_state(run, status="running", plan={"turns_n": len(plan["turns"]), "max_gap_s": plan["max_gap_s"], "mode": plan["mode"], "lag": plan["lag"]}, _daemon=True)
    ex = Exchange(run, cfg["auto"])

    class ExchangeRouter:
        """Thay `ModelRouter` (chỉ phần pipeline gọi): `generate` = hỏi agent; `embed` = không có embedding."""

        async def generate(self, org_id: Any, *, agent_key: str, purpose: str, messages: list[Any], json_mode: bool = True, temperature: float = 0.2, **kw: Any) -> Any:
            text_ = await ex.ask(purpose, messages, "d2" if kw.get("tier") == "strong" else None)   # (d2) của job lừa đảo: tier strong
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
    from gh.refinery import crosscheck  # noqa: PLC0415

    crosscheck.D1_TIMEOUT_S = 1e9                   # (d1)/(d2) cũng chờ agent, không coi là hết giờ (model thật: 90 s / 60 s ⇒ fail-high)
    crosscheck.verify_strong.__kwdefaults__["time_limit_s"] = 1e9  # type: ignore[index]
    app = create_app()
    try:
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://step/api/v1", timeout=120) as client:
                await do_setup(client)
                org = app.state.org_id
                sm = sessionmaker()
                if cfg.get("intel_settings"):
                    async with sm() as db:
                        await db.execute(text("""UPDATE core.organizations SET settings = jsonb_set(COALESCE(settings, '{}'::jsonb), '{intel}',
                                                 COALESCE(settings -> 'intel', '{}'::jsonb) || CAST(:j AS jsonb)) WHERE id = :o"""),
                                         {"j": json.dumps(cfg["intel_settings"]), "o": org})
                        await db.commit()
                # nhóm có sẵn TRƯỚC khi seed nhãn bối cảnh (replay chỉ gán nhãn cho nhóm đã có tin tới lượt đầu)
                async with sm() as db:
                    ch = await channel_row(db, org, "zalo")
                    counts = parse_member_counts(ds.context_md, ds.groups) if cfg.get("identity", True) else {}
                    for gk, gname in ds.groups.items():
                        await upsert_group(db, org, ch, gk, gname, counts.get(gk))
                    await db.commit()
                router = ExchangeRouter()
                refinery = runner.Refinery(sm, app.state.redis, router, app.state.bus, time_budget_s=1e9, part_worst_s=0.0, analyzer=capture)  # type: ignore[arg-type]
                an = PipelineAnalyzer(refinery, org, ex, captured)
                sink = IngestSink(org)
                cj = ClockJobs(run, refinery, org, ex, {"redis": app.state.redis, "redis_bus": app.state.redis, "model_router": router}, start=ds.msgs[0].ts)
                an.clock = sink.clock = cj
                ident: IdentitySeed | None = None
                if cfg.get("identity", True):
                    ident = IdentitySeed(parse_context(ds.context_md), client, org, sm, app.state.redis)
                    await ident.pair_owner()      # trước mọi tin: tin đầu của Sếp trong nhóm đã thành `attrs.is_owner`
                    sink.identity = ident
                seeder = StepSeeder(client)
                summary = await replay.run_replay(ds, run, mode="compressed", analyzer=an, sink=sink, seeder=seeder, contexts=cfg["contexts"],
                                                  engine=replay.FakeMediaEngine(), label="step", cfg=pcfg)
                if ident is not None:
                    write_json(run / "identity.json", ident.report(counts))
                    summary["identity"] = {"paired": ident.paired, "pair_error": ident.pair_error, "marked": len(ident.done)}
                summary["drain_turns"] = await an.drain()
                # Đợt 2: lượt đồng hồ cuối ở `now` cuối của replay, rồi chạy tiếp tới bản tin sáng kế (07:30) và dựng bản tin như Sếp sẽ thấy
                end = clock.now()
                await cj.advance(end)
                write_json(run / "alerts_end.json", {"at": end.astimezone(cj.tz).isoformat(), "alerts": await alerts_snapshot(sm, org)})
                dig_at = next_morning_digest(end, cj.tz)
                await cj.advance(dig_at)
                write_json(run / "digest.json", await build_digest(sm, org, dig_at))
                write_json(run / "alerts_final.json", {"at": dig_at.isoformat(), "alerts": await alerts_snapshot(sm, org), "first_seen": cj.first_seen})
                summary["clock"] = cj.stats()
                # idmap: m_<uuid kho thô> → id dữ liệu (bộ chấm chuẩn hoá id trích dẫn qua đây)
                idmap = json.loads((run / "idmap.json").read_text(encoding="utf-8"))
                async with sm() as db:
                    for r in (await db.execute(text("SELECT id, external_msg_id FROM raw.events WHERE org_id = :o"), {"o": org})).all():
                        idmap[f"m_{r.id}"] = r.external_msg_id
                write_json(run / "idmap.json", idmap)
                write_records(run, an.records, idmap, summary)
                clock.reset()
                write_state(run, status="done", pending=None, ingested=sink.inserted, dropped=sink.dropped, plan_turns=summary["turns_n"], pipeline_turns=len(an.records),
                            seeded=summary["seeded"], clock=summary["clock"], _daemon=True)
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
                                 "write_error": r.get("write_error"), "attempts": r["attempts"], "reason": r["reason"], "new_ids": [idmap.get(x, x) for x in r["new_msg_refs"]]}, ensure_ascii=False) + "\n")
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


def alert_lag(alerts: list[dict[str, Any]], msg_ts: dict[str, datetime]) -> dict[str, Any]:
    """H1 — độ trễ tín hiệu: giờ dữ liệu alert xuất hiện lần đầu (`first_seen_at`) − giờ gốc tin MỚI NHẤT mà alert dẫn (`raw_ids` → giờ tin), phút.
    Alert không có tin dẫn/giờ ⇒ bỏ qua (đếm `skipped`). Đường luật (fraud/raw_tail) lag ≈ 0; trần cửa sổ 24 h/48 h của cổng so với số này."""
    from common import quantiles  # noqa: PLC0415

    lags: list[float] = []
    skipped = 0
    for a in alerts:
        ts = [msg_ts[x] for x in a.get("raw_ids") or [] if x in msg_ts]
        seen = a.get("first_seen_at")
        if not ts or not seen:
            skipped += 1
            continue
        lags.append(max(0.0, (datetime.fromisoformat(seen) - max(ts)).total_seconds()) / 60.0)
    q = quantiles(lags)
    sl = sorted(lags)
    p90 = sl[min(len(sl) - 1, int(0.9 * (len(sl) - 1) + 0.5))] if sl else None
    return {"n": len(lags), "skipped": skipped, "median_min": q["median"], "p90_min": p90, "max_min": q["max"],
            "over_24h": sum(1 for x in lags if x > 1440), "over_48h": sum(1 for x in lags if x > 2880)}


def export_dot2(run: Path, idmap: dict[str, str], msg_ts: dict[str, datetime] | None = None) -> dict[str, Any]:
    """Phần Đợt 2 của signals.json, từ tệp tiến trình nền ghi (`alerts_final.json`, `alerts_end.json`, `alerts_log.jsonl`, `digest.json`). Mã tin gốc
    (`raw_event_ids` uuid kho thô) đổi về mã tin dữ liệu qua idmap (`m_<uuid>`). Lần chạy cũ (chưa có Đợt 2) ⇒ danh sách rỗng."""
    def load(name: str) -> Any:
        p = run / name
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None

    final, end = load("alerts_final.json") or {}, load("alerts_end.json") or {}
    first = final.get("first_seen") or {}

    def m(a: dict[str, Any]) -> dict[str, Any]:
        return {**{k: v for k, v in a.items() if k != "raw_event_ids"}, "raw_ids": [idmap.get(f"m_{x}", x) for x in a["raw_event_ids"]],
                "first_seen_at": first.get(a["id"])}

    log = [json.loads(x) for x in (run / "alerts_log.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()] if (run / "alerts_log.jsonl").exists() else []
    st = read_state(run)
    alerts = [m(a) for a in final.get("alerts") or []]
    clock = st.get("clock")
    if clock is not None and msg_ts is not None:
        clock = {**clock, "lag": {"alerts": alert_lag(alerts, msg_ts), "plan": (st.get("plan") or {}).get("lag")}}
    return {"alerts_at": final.get("at"), "alerts": alerts,
            "alerts_end_at": end.get("at"), "alerts_end": [m(a) for a in end.get("alerts") or []],
            "pushed": [e for e in log if e["event"] == "push"], "shadow_pushed": [e for e in log if e["event"] == "shadow_push"],
            "alert_events": [e for e in log if e["event"] in ("new", "change")], "digest": load("digest.json"), "clock": clock}


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

    ds = load_dataset(cfg["dataset"])
    d2 = export_dot2(run, r.idmap, {m.id: m.ts for m in ds.msgs})
    write_json(out, {"format": "gkb-step-signals/2", "dataset": st.get("dataset"), "turns": r.turns, "contract_errors": r.contract_errors,
                     "contract_retries": r.contract_retries, "turn_errors": r.turn_errors, "write_errors": r.write_errors, "unreadable": r.unreadable,
                     "units": [js(u) for u in r.units], "facts": [js(f) for f in r.facts], "db": db_counts(cfg), **d2, "intel_settings": cfg.get("intel_settings"),
                     "note": "units/facts/turns…: định dạng nội bộ của gkb_score.Run (như /1); /2 thêm alerts (cuối, lúc bản tin sáng), alerts_end (cuối replay), "
                             "pushed/shadow_pushed (🔴 đã đẩy / 🔴 bóng, giờ dữ liệu), alert_events, digest, clock. Chấm: gkb_score.py --runs <run_dir> --key <đáp án ngoài repo>"})
    print(f"đã ghi {out}: {len(r.units)} đơn vị ({len(r.signals)} có kind), {len(r.facts)} sự thật đề xuất, {r.contract_errors} lỗi hợp đồng, {len(r.write_errors)} lượt lỗi ghi / {r.turns} lượt; "
          f"{len(d2['alerts'])} biz.alerts, {len(d2['pushed'])} lần 🔴 đẩy, {len(d2['shadow_pushed'])} 🔴 bóng, bản tin {len((d2['digest'] or {}).get('items') or [])} mục")
    db = db_counts(cfg)
    stuck = db["event_state"].get("pending", 0) + db["event_state"].get("processing", 0)
    if r.write_errors or stuck:
        print(f"step: CẢNH BÁO — {len(r.write_errors)} lượt lỗi ghi, {stuck} tin còn chờ/đang xử lý sau lần chạy", file=sys.stderr)


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
    p.add_argument("--intel-settings", help='JSON gộp vào settings.intel của tổ chức sau setup (vd \'{"ramp_gates": "push"}\'); mặc định = cài mới')
    p.add_argument("--max-lag-min", type=float, default=30.0,
                   help="H1: trần độ trễ phân tích mỗi luồng theo GIỜ DỮ LIỆU (phút; 0 = tắt, khuôn cũ: lô trải nhiều ngày). Mặc định 30")
    p.add_argument("--no-identity", action="store_true", help="H2: KHÔNG ghép kênh Sếp / đánh dấu nhân viên-khách / member_count từ context.md (nhánh đối chứng)")
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
        import logging  # noqa: PLC0415

        logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(name)s %(levelname)s %(message)s")   # lỗi job production (tự bắt + log) hiện ở daemon.log
        asyncio.run(serve(Path(a.out).resolve()))
    else:
        {"init": cmd_init, "next": cmd_next, "submit": cmd_submit, "export": cmd_export, "stats": cmd_stats}[a.cmd](a)


if __name__ == "__main__":
    _cli()
