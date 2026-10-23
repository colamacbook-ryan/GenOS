#!/usr/bin/env python3
# ruff: noqa: E501, ASYNC230, ASYNC240     # script chạy một lần, ghi tệp cục bộ: chặn I/O ngắn trong coroutine là chấp nhận được
"""replay.py — phát lại bộ dữ liệu tổng hợp qua pipeline trên ĐỒNG HỒ GIẢ (spec §2.1 G-KB, CNS-101, gói L6).

    GH_ENV=eval python3 scripts/intel-eval/replay.py --data research/eval-tinh-bao/v2/kb1 --out /tmp/run/kb1 \\
        --mode compressed --repeat 3 --unconfirmed-extra [--sink bus|jsonl|none] [--analyzer pkg.mod:attr] [--seed-url URL]

Việc replay làm:
1. nạp `chats.jsonl` + `context.md`; lập lượt theo luật §4D.1/§4D.5 trên đồng hồ giả (`clockplan.py`): chế độ **compressed** (mặc định, cổng;
   giữ thứ tự + `reply_to`, nén khoảng lặng, mỗi lượt cỡ sản phẩm 10–60 tin, có ca dư 60 tin) hoặc **realtime** (`sent_at` gốc, chạy MỘT lần
   làm tham chiếu); báo phân phối tin/lượt;
2. đổ tin vào `raw.events` qua bus (`--sink bus`: `gh.bridge.inbound`, đúng phong bì của `fake_bridge.py`) hoặc ghi `envelopes.jsonl` (`jsonl`);
   mỗi tin có `occurred_at` = giờ gửi GỐC; voice/ảnh đi qua `media_ref` + engine media giả trả đúng chữ có sẵn (`FakeMediaEngine`);
3. seed Hồ sơ Sếp + nhãn bối cảnh nhóm từ `context.md` qua API CÔNG KHAI (`Seeder`; bỏ qua với lần chạy "bối cảnh chưa xác nhận", CNS-072);
4. tới mỗi lượt gọi **điểm cắm phân tích** (`--analyzer`): chỗ L2 (scheduler) + L3 (lượt phân tích) sẽ cắm vào. Chưa có ⇒ `StubAnalyzer`
   (đầu ra hợp lệ `brain-out/1` không có đơn vị — chỉ để chạy được khung). Điểm cắm trả đầu ra + usage token để chấm (`gkb_score.py`)
   và đo S7 (`tokens.py`).

Đồng hồ giả: `GH_ENV=eval` là điều kiện BẮT BUỘC cho mọi thứ chạm bus/API (không bao giờ chạy nhầm vào stack thật). Lượng thời gian giả chỉ
sống trong `clockplan.FakeClock`/`plan.json`; `received_at` của `raw.events` do `ingest_message` đặt bằng giờ máy (không thuộc L6) — pipeline
của L2 phải đọc giờ qua `gh/refinery/clock.py` (L2 sở hữu) để chạy theo `turn.start_s` này. Điểm cắm ghi ở `ANALYZER_CONTRACT` bên dưới."""

from __future__ import annotations

import argparse
import asyncio
import importlib
import json
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

sys.path.insert(0, str(Path(__file__).resolve().parent))

from clockplan import (  # noqa: E402
    FakeClock,
    PlanConfig,
    check_distribution,
    distribution,
    lively_threads,
    plan_batch40,
    plan_turns,
    realtime_times,
    tune_max_gap,
)
from common import (  # noqa: E402
    Dataset,
    Msg,
    brain_contract,
    fake_bridge,
    load_dataset,
    parse_context,
    refuse_inside_repo,
    to_script_event,
    write_json,
)

ANALYZER_CONTRACT = """Điểm cắm phân tích (chờ L2/L3): một đối tượng có `async analyze(inp: TurnInput) -> AnalysisResult` (hoặc hàm async cùng chữ ký).
`inp` mang: thread_ref ('g:<nhóm>'), tin mới (message_id = 'm_<id dữ liệu>'), cửa sổ gần, Hồ sơ Sếp đã seed, chính sách bối cảnh, giờ giả.
Trả `output` (dict hoặc chuỗi JSON `brain-out/1`), `usage` ({input, output, cache_read, cache_write, model, calls}) và `attempts` (số lần gọi
model kể cả lần tự sửa schema). Khi L2/L3 merge: viết adapter gọi `refinery.runner`/`extract` với đồng hồ `turn.start_s`, rồi `--analyzer adapter:Analyzer`."""

TOKEN_FIELDS = ("input", "output", "cache_read", "cache_write")


def require_eval_env() -> None:
    if os.environ.get("GH_ENV") != "eval":
        raise SystemExit("replay: từ chối chạm bus/API khi GH_ENV != 'eval' (đặt GH_ENV=eval cho stack đo; không bao giờ chạy trên stack thật)")


# ─── Engine media giả (spec §4E, "voice/ảnh qua media.process với engine giả trả đúng chữ có sẵn") ─────────────────────────────

class FakeMediaEngine:
    """Engine STT/OCR giả: `media_ref` → đúng chữ có sẵn của tin trong bộ dữ liệu. `fail_refs` mô phỏng tin không đọc được (đếm độ phủ M12)."""

    def __init__(self, table: dict[str, str] | None = None, fail_refs: set[str] | None = None) -> None:
        self.table = dict(table or {})
        self.fail_refs = set(fail_refs or ())
        self.processed = 0
        self.failed = 0

    def register(self, media_ref: str, text: str) -> None:
        self.table[media_ref] = text

    def process(self, media_ref: str) -> dict[str, Any]:
        """Tương đương một lượt `media.process`: trả {status, text} — `status` ∈ ok | failed (khớp `clean.media_texts.status`)."""
        if media_ref in self.fail_refs or media_ref not in self.table:
            self.failed += 1
            return {"status": "failed", "text": None, "engine": "fake"}
        self.processed += 1
        return {"status": "ok", "text": self.table[media_ref], "engine": "fake"}

    def coverage(self) -> dict[str, Any]:
        n = self.processed + self.failed
        return {"processed": self.processed, "failed": self.failed, "read_rate": (self.processed / n) if n else None}

    def dump(self, path: str | Path) -> None:
        write_json(path, {"table": self.table, "fail_refs": sorted(self.fail_refs)})

    @classmethod
    def load(cls, path: str | Path) -> FakeMediaEngine:
        d = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(d.get("table"), set(d.get("fail_refs", [])))


# ─── Điểm cắm phân tích ───────────────────────────────────────────────────────────────────────────────────────────────

@dataclass
class TurnInput:
    n: int
    thread_ref: str
    now_iso: str
    new: list[dict[str, Any]]
    recent: list[dict[str, Any]]
    contexts_confirmed: bool
    context_label: str | None
    profile: dict[str, list[str]] = field(default_factory=dict)
    media: dict[str, Any] = field(default_factory=dict)
    turn: dict[str, Any] = field(default_factory=dict)


@dataclass
class AnalysisResult:
    output: Any = None
    usage: dict[str, Any] = field(default_factory=dict)
    attempts: int = 1
    error: str | None = None
    latency_s: float = 0.0


class Analyzer(Protocol):
    async def analyze(self, inp: TurnInput) -> AnalysisResult: ...


class StubAnalyzer:
    """Chờ L2/L3: đầu ra hợp lệ tối thiểu (không đơn vị nào), usage 0. Ghi rõ trong `plan.json.analyzer = 'stub'`."""

    name = "stub"

    async def analyze(self, inp: TurnInput) -> AnalysisResult:
        bc = brain_contract()
        first = inp.new[0] if inp.new else {"message_id": "m_none", "text": "(không có tin)"}
        out = bc.stub(inp.thread_ref, message_id=first["message_id"], quote=(first.get("text") or "(trống)")[:100] or "(trống)")
        return AnalysisResult(output=out.model_dump(mode="json"), usage={"input": 0, "output": 0, "cache_read": 0, "cache_write": 0,
                                                                           "model": "stub", "calls": 0})


def load_analyzer(spec: str | None) -> Any:
    if not spec:
        return StubAnalyzer()
    mod, _, attr = spec.partition(":")
    obj = getattr(importlib.import_module(mod), attr or "Analyzer")
    obj = obj() if isinstance(obj, type) else obj
    if callable(obj) and not hasattr(obj, "analyze"):
        fn = obj

        class _Fn:
            name = spec

            async def analyze(self, inp: TurnInput) -> AnalysisResult:
                return await fn(inp)

        return _Fn()
    return obj


# ─── Đích đổ tin ─────────────────────────────────────────────────────────────────────────────────────────────────────────

class Sink(Protocol):
    async def emit(self, stream: str, type: str, payload: dict[str, Any]) -> None: ...
    async def close(self) -> None: ...


class NullSink:
    async def emit(self, stream: str, type: str, payload: dict[str, Any]) -> None:
        return None

    async def close(self) -> None:
        return None


class JsonlSink:
    def __init__(self, path: str | Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.f = open(path, "w", encoding="utf-8")  # noqa: SIM115
        self.n = 0

    async def emit(self, stream: str, type: str, payload: dict[str, Any]) -> None:
        self.f.write(json.dumps({"stream": stream, "type": type, "payload": payload}, ensure_ascii=False) + "\n")
        self.n += 1

    async def close(self) -> None:
        self.f.close()


class BusSink:
    """Đổ lên bus thật (Redis Stream) — cần `GH_ENV=eval`, `GH_REDIS_URL`, `ORG`. Dùng `gh.chassis.bus.EventBus` (cùng đường với bridge)."""

    def __init__(self, redis_url: str, org: str) -> None:
        require_eval_env()
        d = str(Path(os.environ.get("GH_API_DIR", Path(__file__).resolve().parents[2] / "apps" / "api")))
        if d not in sys.path:
            sys.path.insert(0, d)
        from redis.asyncio import Redis  # noqa: PLC0415

        from gh.chassis.bus import EventBus  # noqa: PLC0415

        self.r = Redis.from_url(redis_url)
        self.bus = EventBus(self.r, 10000)
        self.org = org
        self.checked = False

    async def check_target(self) -> None:
        """Kiểm PHÍA ĐÍCH (không tin biến môi trường của người chạy): Redis đích phải có khoá `gh:env` = `eval`, do bước dựng stack đo đặt
        (`redis-cli SET gh:env eval`). Thiếu ⇒ từ chối đổ — chống đổ tin giả vào stack thật chỉ vì lỡ đặt GH_ENV=eval ở máy chạy."""
        v = await self.r.get("gh:env")
        if (v.decode() if isinstance(v, bytes) else v) != "eval":
            raise SystemExit("replay: Redis đích KHÔNG có khoá gh:env = 'eval' — từ chối đổ tin (đây có thể là stack thật)")
        self.checked = True

    async def emit(self, stream: str, type: str, payload: dict[str, Any]) -> None:
        if not self.checked:
            await self.check_target()
        await self.bus.publish(stream, type, payload, actor="bridge:eval", org_id=self.org)

    async def close(self) -> None:
        await self.r.aclose()


# ─── Seed Hồ sơ + bối cảnh qua API công khai ──────────────────────────────────────────────────────────────────────────────

class Seeder:
    """Seed bằng API CÔNG KHAI (spec §7.2b), không chạm DB: nhãn bối cảnh `PUT /groups/{id}/contexts`; Hồ sơ Sếp `POST /me/profile/items`
    (Owner + PIN; vì thế client phải đã đăng nhập, có `X-CSRF-Token` và PIN còn hiệu lực). `client` = httpx.AsyncClient trỏ tới `/api/v1`.
    ĐIỂM CHỜ: `POST /me/profile/items` thuộc gói Đợt 2 (T-pack); thân JSON {section, predicate, text} là GIẢ ĐỊNH của L6 theo bảng §7.2b,
    sửa ở `PROFILE_ITEM_BODY` khi hợp đồng thật chốt."""

    PROFILE_ITEM_BODY = staticmethod(lambda predicate, text: {"predicate": predicate, "text": text})

    def __init__(self, client: Any, channel: str = "zalo") -> None:
        self.c = client
        self.channel = channel
        self.log: list[dict[str, Any]] = []

    async def login(self, email: str, password: str) -> None:
        """Đăng nhập thật (`POST /auth/login`), lấy cookie phiên + CSRF (`gh_csrf` → header `X-CSRF-Token`) như e2e-live. Mật khẩu không vào log.
        ĐIỂM CHỜ: ghi Hồ sơ cần PIN còn hiệu lực (423 nếu chưa) — cách nhập PIN cho phiên đo do T-pack/stack đo quyết định."""
        r = await self.c.post("/auth/login", json={"email": email, "password": password})
        if r.status_code >= 400:
            raise SystemExit(f"seed: đăng nhập thất bại (HTTP {r.status_code})")
        csrf = self.c.cookies.get("gh_csrf")
        if not csrf:
            raise SystemExit("seed: đăng nhập xong nhưng không có cookie gh_csrf")
        self.c.headers["X-CSRF-Token"] = csrf

    async def group_ids_by_name(self) -> dict[str, str]:
        r = await self.c.get(f"/channels/{self.channel}/groups")
        r.raise_for_status()
        return {g["name"]: g["id"] for g in r.json()}

    async def put_contexts(self, group_names: dict[str, str], labels: dict[str, str]) -> int:
        """`labels` = {Gk: mã bối cảnh}; `group_names` = {Gk: tên nhóm}. Trả số nhóm đã gán nhãn (nhãn đánh dấu chính)."""
        ids = await self.group_ids_by_name()
        n = 0
        for gk, code in labels.items():
            gid = ids.get(group_names.get(gk, ""))
            if gid is None:
                self.log.append({"group": gk, "skipped": "không thấy nhóm trong kênh (chưa có tin nào vào?)"})
                continue
            r = await self.c.put(f"/groups/{gid}/contexts", json={"labels": [{"context": code, "is_primary": True}]})
            r.raise_for_status()
            self.log.append({"group": gk, "context": code, "status": r.status_code})
            n += 1
        return n

    async def post_profile(self, profile: dict[str, list[str]]) -> int:
        n = 0
        for predicate, lines in profile.items():
            for line in lines:
                r = await self.c.post("/me/profile/items", json=self.PROFILE_ITEM_BODY(predicate, line))
                r.raise_for_status()
                self.log.append({"profile": predicate, "status": r.status_code})
                n += 1
        return n


# ─── Chạy một lần ────────────────────────────────────────────────────────────────────────────────────────────────────────

def thread_ref(m: Msg) -> str:
    return f"g:{m.group}"


def msg_json(m: Msg) -> dict[str, Any]:
    return {"message_id": f"m_{m.id}", "id": m.id, "sender_id": m.sender_id, "sender_name": m.sender_name, "ts": m.ts.isoformat(),
            "text": m.text, "kind": m.kind, "reply_to": (f"m_{m.reply_to}" if m.reply_to else None)}


def plan_for(ds: Dataset, mode: str, cfg: PlanConfig, *, target_median: float = 10.0, plan_kind: str = "thread") -> dict[str, Any]:
    live = lively_threads(ds.msgs)
    if plan_kind == "batch40":      # khuôn cũ cho red team B: lô 40 tin trộn nhóm, thời gian thật
        turns = plan_batch40(ds.msgs)
        dist = distribution(turns, set(), cfg)
        return {"mode": "batch40", "max_gap_s": None, "times": realtime_times(ds.msgs), "turns": turns, "dist": dist,
                "check": {"ok": True, "checks": {}, "note": "khuôn cũ, không kiểm phân phối"}, "burst_idx": None}
    if plan_kind != "thread":
        raise ValueError(f"plan_kind không hợp lệ: {plan_kind!r} (thread | batch40)")
    if mode == "realtime":
        times = realtime_times(ds.msgs)
        stats: dict[str, int] = {}
        turns = plan_turns(ds.msgs, times, cfg, None, stats)
        dist = distribution(turns, live, cfg, stats.get("skipped_flushed", 0))
        return {"mode": mode, "max_gap_s": None, "times": times, "turns": turns, "dist": dist, "check": check_distribution(dist), "burst_idx": None}
    if mode == "compressed":
        r = tune_max_gap(ds.msgs, cfg, target_median=target_median)
        return {"mode": mode, **r}
    raise ValueError(f"mode không hợp lệ: {mode!r} (compressed | realtime)")


async def run_replay(ds: Dataset, out_dir: str | Path, *, mode: str = "compressed", analyzer: Any = None, sink: Any = None,
                     seeder: Seeder | None = None, contexts: dict[str, str] | None = None, confirmed: bool = True,
                     cfg: PlanConfig | None = None, engine: FakeMediaEngine | None = None, label: str = "r1",
                     plan_kind: str = "thread") -> dict[str, Any]:
    """Một lần chạy. `confirmed=False` = lần "bối cảnh chưa xác nhận" (không seed nhãn nhóm; Hồ sơ vẫn seed — chỉ nhãn bị bỏ, CNS-072)."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    cfg = cfg or PlanConfig()
    analyzer = analyzer or StubAnalyzer()
    sink = sink or NullSink()
    engine = engine or FakeMediaEngine()
    ctx = parse_context(ds.context_md)
    labels = dict(contexts if contexts is not None else ctx["groups"])
    plan = plan_for(ds, mode, cfg, plan_kind=plan_kind)
    msgs, times, turns = ds.msgs, plan["times"], plan["turns"]
    clock = FakeClock(msgs[0].ts)
    # id nhận diện → id dữ liệu: bộ chấm dùng khi pipeline gắn id khác (m_<uuid>, id kho thô…); replay ghi những alias nó biết.
    idmap: dict[str, str] = {}
    for m in msgs:
        idmap[m.id] = m.id
        idmap[f"m_{m.id}"] = m.id
    if seeder is not None and os.environ.get("GH_EVAL_EMAIL") and os.environ.get("GH_EVAL_PASSWORD"):
        await seeder.login(os.environ["GH_EVAL_EMAIL"], os.environ["GH_EVAL_PASSWORD"])
    seeded = {"contexts": 0, "profile": 0}
    emitted = 0
    cursor = 0
    outputs_f = open(out / "outputs.jsonl", "w", encoding="utf-8")  # noqa: SIM115
    turns_f = open(out / "turns.jsonl", "w", encoding="utf-8")  # noqa: SIM115
    tokens_f = open(out / "tokens.jsonl", "w", encoding="utf-8")  # noqa: SIM115
    seeded_done = False
    bridge = fake_bridge()

    async def emit_upto(t: float) -> None:
        nonlocal cursor, emitted
        while cursor < len(msgs) and times[cursor] <= t + 1e-9:
            m = msgs[cursor]
            ev = to_script_event(m)
            for stream, typ, payload in bridge.compile_script_event(ev, cursor):
                if ev["type"] in ("voice", "image") and payload.get("payload", {}).get("media_ref"):
                    engine.register(payload["payload"]["media_ref"], payload["payload"].get("fake_media_text") or "")
                    idmap[payload["payload"]["media_ref"]] = m.id
                await sink.emit(stream, typ, payload)
                emitted += 1
            cursor += 1

    try:
        for turn in turns:
            clock.advance_to(turn.start)
            await emit_upto(turn.start)
            if seeder is not None and not seeded_done:
                # nhóm chỉ có trong API sau khi tin đầu của nó vào kho ⇒ seed sau khi đã đổ tin tới lượt đầu; nhãn được gán TRƯỚC khi ai phân tích
                if confirmed and labels:
                    seeded["contexts"] = await seeder.put_contexts(ds.groups, labels)
                seeded["profile"] = await seeder.post_profile(ctx["profile"])
                seeded_done = True
            first = turn.new[0]
            media = {m.id: engine.process(f"fake://{'voice' if m.kind == 'voice_transcript' else 'image'}/{m.id}")
                     for m in turn.new if m.kind in ("voice_transcript", "image_caption")}
            inp = TurnInput(n=turn.n, thread_ref=("g:mixed" if turn.thread == "mixed" else thread_ref(first)), now_iso=clock.now().isoformat(), new=[msg_json(m) for m in turn.new],
                            recent=[msg_json(m) for m in turn.recent], contexts_confirmed=bool(confirmed and labels.get(turn.thread)),
                            context_label=(labels.get(turn.thread) if confirmed else None), profile=ctx["profile"], media=media,
                            turn=turn.to_json())
            t0 = time.monotonic()
            try:
                res = await analyzer.analyze(inp)
            except Exception as e:  # noqa: BLE001 — điểm cắm lỗi phải hiện trong đầu ra, không làm chết cả lần chạy
                res = AnalysisResult(error=f"analyzer_exception:{type(e).__name__}:{str(e)[:200]}")
            res.latency_s = res.latency_s or (time.monotonic() - t0)
            outputs_f.write(json.dumps({"turn": turn.n, "thread_ref": inp.thread_ref, "thread": turn.thread, "output": res.output, "error": res.error,
                                        "attempts": res.attempts, "new_ids": [m.id for m in turn.new]}, ensure_ascii=False, default=str) + "\n")
            turns_f.write(json.dumps(turn.to_json(), ensure_ascii=False) + "\n")
            tokens_f.write(json.dumps({"turn": turn.n, "thread": turn.thread, "new_n": len(turn.new), "recent_n": len(turn.recent),
                                       "chars": turn.chars, "reason": turn.reason, "usage": res.usage, "latency_s": res.latency_s,
                                       "attempts": res.attempts, "mode": mode}) + "\n")
        await emit_upto(float("inf"))      # tin đuôi chưa kịp lượt (đã vào kho; lượt kế của scheduler thật sẽ nhặt)
    finally:
        for f in (outputs_f, turns_f, tokens_f):
            f.close()
        await sink.close()
    write_json(out / "idmap.json", idmap)
    engine.dump(out / "media_table.json")
    summary = {"label": label, "dataset": ds.name, "mode": plan["mode"], "confirmed": confirmed, "analyzer": getattr(analyzer, "name", type(analyzer).__name__),
               "max_gap_s": plan["max_gap_s"], "burst_idx": plan["burst_idx"], "turns_n": len(turns), "messages": len(msgs), "emitted": emitted,
               "distribution": plan["dist"], "distribution_check": plan["check"], "media_coverage": engine.coverage(), "seeded": seeded,
               "config": cfg.__dict__}
    write_json(out / "plan.json", {**{k: v for k, v in summary.items()}, "analyzer_contract": ANALYZER_CONTRACT.splitlines()[0]})
    write_json(out / "summary.json", summary)
    return summary


async def run_suite(ds: Dataset, out_root: str | Path, *, repeat: int = 3, unconfirmed_extra: bool = True, realtime_ref: bool = False,
                    make_analyzer: Any = None, make_sink: Any = None, make_seeder: Any = None, contexts: dict[str, str] | None = None,
                    cfg: PlanConfig | None = None) -> list[dict[str, Any]]:
    """≥ 3 lần compressed + 1 lần chưa xác nhận (+ 1 lần realtime tham chiếu nếu `realtime_ref`). Mỗi lần một thư mục `r1…`, `u1`, `ref`."""
    runs: list[tuple[str, str, bool]] = [(f"r{i}", "compressed", True) for i in range(1, repeat + 1)]
    if unconfirmed_extra:
        runs.append(("u1", "compressed", False))
    if realtime_ref:
        runs.append(("ref", "realtime", True))
    out: list[dict[str, Any]] = []
    for label, mode, confirmed in runs:
        out.append(await run_replay(ds, Path(out_root) / label, mode=mode, analyzer=make_analyzer() if make_analyzer else None,
                                    sink=make_sink(label) if make_sink else None, seeder=make_seeder() if make_seeder else None,
                                    contexts=contexts, confirmed=confirmed, cfg=cfg, label=label))
    write_json(Path(out_root) / "suite.json", {"dataset": ds.name, "runs": [{k: r[k] for k in ("label", "mode", "confirmed", "turns_n")} for r in out]})
    return out


def _cli() -> None:
    ap = argparse.ArgumentParser(description="Phát lại bộ dữ liệu G-KB trên đồng hồ giả (xem docstring)")
    ap.add_argument("--data", required=True, help="thư mục kbN (chats.jsonl + context.md)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--mode", choices=("compressed", "realtime"), default="compressed")
    ap.add_argument("--repeat", type=int, default=1, help=">1 hoặc --suite: nhiều lần chạy r1…rN")
    ap.add_argument("--suite", action="store_true", help="chạy bộ cổng: --repeat lần nén + 1 lần chưa xác nhận (+ --realtime-ref)")
    ap.add_argument("--unconfirmed-extra", action="store_true")
    ap.add_argument("--realtime-ref", action="store_true")
    ap.add_argument("--plan", choices=("thread", "batch40"), default="thread", help="batch40 = khuôn lô 40 tin trộn nhóm cũ (so sánh red team B)")
    ap.add_argument("--sink", choices=("none", "jsonl", "bus"), default="none")
    ap.add_argument("--analyzer", help="module:attr — điểm cắm L2/L3 (mặc định StubAnalyzer)")
    ap.add_argument("--contexts", help="JSON {Gk: mã bối cảnh} khi context.md không có bảng nhóm (kb1–kb3)")
    ap.add_argument("--seed-url", help="gốc API (…/api/v1) để seed Hồ sơ + nhãn bối cảnh; cần GH_EVAL_EMAIL/GH_EVAL_PASSWORD")
    ap.add_argument("--redis-url", default=os.environ.get("GH_REDIS_URL", ""))
    ap.add_argument("--org", default=os.environ.get("ORG", ""))
    a = ap.parse_args()
    refuse_inside_repo(a.out)
    ds = load_dataset(a.data)
    contexts = json.loads(Path(a.contexts).read_text()) if a.contexts else None
    if a.sink == "bus" or a.seed_url:
        require_eval_env()

    def make_sink(label: str) -> Any:
        if a.sink == "bus":
            return BusSink(a.redis_url, a.org)
        if a.sink == "jsonl":
            return JsonlSink(Path(a.out) / label / "envelopes.jsonl")
        return NullSink()

    def make_seeder() -> Seeder | None:
        if not a.seed_url:
            return None
        import httpx  # noqa: PLC0415

        if not os.environ.get("GH_EVAL_EMAIL") or not os.environ.get("GH_EVAL_PASSWORD"):
            raise SystemExit("--seed-url cần GH_EVAL_EMAIL và GH_EVAL_PASSWORD")
        c = httpx.AsyncClient(base_url=a.seed_url.rstrip("/"), timeout=30)
        return Seeder(c)

    async def go() -> None:
        if a.suite or a.repeat > 1:
            await run_suite(ds, a.out, repeat=a.repeat, unconfirmed_extra=a.unconfirmed_extra or a.suite, realtime_ref=a.realtime_ref,
                            make_analyzer=lambda: load_analyzer(a.analyzer), make_sink=make_sink, make_seeder=make_seeder, contexts=contexts)
        else:
            s = await run_replay(ds, a.out, mode=a.mode, analyzer=load_analyzer(a.analyzer), sink=make_sink("r1"),
                                 seeder=make_seeder(), contexts=contexts, plan_kind=a.plan)
            print(json.dumps({k: s[k] for k in ("mode", "turns_n", "max_gap_s", "distribution_check")}, ensure_ascii=False, indent=2))

    asyncio.run(go())


if __name__ == "__main__":
    _cli()
