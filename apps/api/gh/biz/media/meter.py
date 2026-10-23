# ruff: noqa: E501
"""Hạ tầng ĐO cho spike S5 (§10): chọn STT mặc định theo (1) tỉ lệ đúng ý và (2) thời gian xử lý cho mỗi phút voice, trên ~30
voice TỰ THU theo kịch bản (không dùng chat thật, Q12).

CHƯA ĐO TRÊN VOICE THẬT — chờ voice tự thu theo kịch bản. Mọi con số sinh ra khi chạy với engine giả (`FakeSttEngine`) chỉ
chứng minh hạ tầng đo chạy đúng, KHÔNG phải kết luận chọn mặc định; báo cáo tự ghi dòng cảnh báo đó (`BenchReport.caveat`).

Định nghĩa:
- WER: tỉ lệ lỗi theo âm tiết (tiếng Việt tách theo khoảng trắng), sau chuẩn hoá NFC + hạ chữ + bỏ dấu câu. Không bỏ dấu
  thanh (sai dấu là sai nghĩa).
- Đúng ý (intent hit): mọi `facts` của kịch bản (số tiền, tên, mốc giờ, hành động — người soạn kịch bản liệt kê) xuất hiện
  trong bản chép sau chuẩn hoá; số được so theo chữ số (bỏ dấu chấm/phẩy ngăn cách). Tỉ lệ đúng ý = số mẫu đúng ý / tổng mẫu.
- Thời gian mỗi phút voice = giây xử lý (đồng hồ treo tường) / số phút âm thanh.

Dùng: `python -m gh.biz.media.meter --manifest samples.json --engine local|router|fake [--out report.json]`.
Manifest: [{"id": "v01", "file": "v01.ogg", "reference": "...", "facts": ["5 triệu", "ngày mai"], "seconds": 12.5}].
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
import unicodedata
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from gh.biz.media.providers import EngineError, SttEngine, fake_voice_bytes
from gh.biz.media.transcribe import transcribe

CAVEAT = "chưa đo trên voice thật, chờ voice tự thu theo kịch bản"
_PUNCT = re.compile(r"[^\w\s]", re.UNICODE)
_NUM_SEP = re.compile(r"(?<=\d)[.,](?=\d{3}(?!\d))")


def normalise(text: str) -> str:
    t = unicodedata.normalize("NFC", text).lower()
    t = _NUM_SEP.sub("", t)
    return " ".join(_PUNCT.sub(" ", t).split())


def wer(reference: str, hypothesis: str) -> float:
    """Khoảng cách sửa (thay/chèn/xoá) trên số âm tiết của tham chiếu; tham chiếu rỗng ⇒ 0 nếu hyp rỗng, ngược lại 1."""
    r, h = normalise(reference).split(), normalise(hypothesis).split()
    if not r:
        return 0.0 if not h else 1.0
    prev = list(range(len(h) + 1))
    for i, rw in enumerate(r, 1):
        cur = [i]
        for j, hw in enumerate(h, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (rw != hw)))
        prev = cur
    return min(1.0, prev[-1] / len(r))


def intent_hit(facts: list[str], hypothesis: str) -> bool:
    hyp = f" {normalise(hypothesis)} "
    return all(f" {normalise(f)} " in hyp for f in facts if normalise(f))


@dataclass
class Sample:
    id: str
    reference: str
    facts: list[str] = field(default_factory=list)
    seconds: float = 0.0               # độ dài âm thanh
    data: bytes | None = None           # byte âm thanh (hoặc `file` trong manifest)
    mime: str = "audio/ogg"


@dataclass
class SampleResult:
    id: str
    ok: bool
    error: str | None
    wer: float | None
    intent_ok: bool | None
    process_s: float
    audio_s: float


@dataclass
class BenchReport:
    engine: str
    engine_version: str
    fake: bool
    n: int
    failed: int
    wer_mean: float | None
    intent_rate: float | None          # tỉ lệ đúng ý trên TỔNG mẫu (mẫu lỗi tính là không đúng ý)
    seconds_per_audio_minute: float | None
    caveat: str | None
    samples: list[SampleResult]

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    def summary(self) -> str:
        rate = "n/a" if self.intent_rate is None else f"{self.intent_rate:.0%}"
        w = "n/a" if self.wer_mean is None else f"{self.wer_mean:.1%}"
        spm = "n/a" if self.seconds_per_audio_minute is None else f"{self.seconds_per_audio_minute:.2f}"
        out = f"{self.engine} ({self.engine_version}): {self.n} mẫu, đúng ý {rate}, WER {w}, {spm} giây xử lý / phút voice, lỗi {self.failed}"
        return out + (f"\n[{self.caveat}]" if self.caveat else "")


async def run_benchmark(engine: SttEngine, samples: list[Sample], *, lang_hint: str = "vi") -> BenchReport:
    results: list[SampleResult] = []
    for s in samples:
        t0 = time.perf_counter()
        try:
            got = await transcribe(engine, s.data or b"", s.mime, lang_hint=lang_hint)
            dt = time.perf_counter() - t0
            results.append(SampleResult(s.id, True, None, round(wer(s.reference, got.text), 4),
                                        intent_hit(s.facts, got.text), dt, s.seconds))
        except EngineError as e:
            results.append(SampleResult(s.id, False, e.code, None, False, time.perf_counter() - t0, s.seconds))
    ok = [r for r in results if r.ok]
    audio_min = sum(r.audio_s for r in results if r.audio_s) / 60
    proc = sum(r.process_s for r in results if r.audio_s)
    return BenchReport(
        engine=engine.name, engine_version=engine.version, fake=engine.is_fake, n=len(results),
        failed=len(results) - len(ok),
        wer_mean=round(sum(r.wer or 0 for r in ok) / len(ok), 4) if ok else None,
        intent_rate=round(sum(1 for r in results if r.intent_ok) / len(results), 4) if results else None,
        seconds_per_audio_minute=round(proc / audio_min, 3) if audio_min else None,
        caveat=CAVEAT if engine.is_fake else None, samples=results)


def load_manifest(path: Path) -> list[Sample]:
    items = json.loads(path.read_text(encoding="utf-8"))
    out = []
    for it in items:
        f = (path.parent / it["file"]).resolve()
        if path.parent.resolve() not in f.parents:
            raise ValueError("tệp mẫu phải nằm trong thư mục của manifest")
        out.append(Sample(id=str(it["id"]), reference=str(it["reference"]), facts=[str(x) for x in it.get("facts", [])],
                          seconds=float(it.get("seconds") or 0), data=f.read_bytes(), mime=it.get("mime", "audio/ogg")))
    return out


def fake_samples(n: int = 30) -> list[Sample]:
    """Bộ mẫu GIẢ để chạy thử hạ tầng (byte do `fake_voice_bytes` sinh) — KHÔNG phải voice thật."""
    return [Sample(id=f"fake{i:02d}", reference=f"anh gửi chị {i + 1} triệu vào ngày mai", facts=[f"{i + 1} triệu", "ngày mai"],
                   seconds=10.0, data=fake_voice_bytes(f"anh gửi chị {i + 1} triệu vào ngày mai")) for i in range(n)]


async def _main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--manifest", type=Path)
    ap.add_argument("--engine", choices=("local", "router", "fake"), default="local")
    ap.add_argument("--out", type=Path)
    a = ap.parse_args(argv)
    from gh.biz.media.providers import FakeSttEngine, LocalWhisperEngine

    if a.engine == "router":
        print("Engine router cần ModelRouter đang chạy (chạy trong worker); dùng local hoặc fake ở dòng lệnh.", file=sys.stderr)
        return 2
    engine: SttEngine = LocalWhisperEngine() if a.engine == "local" else FakeSttEngine()
    samples = load_manifest(a.manifest) if a.manifest else fake_samples()
    report = await run_benchmark(engine, samples)
    print(report.summary())
    if a.out:
        a.out.write_text(json.dumps(report.as_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_main(sys.argv[1:])))
