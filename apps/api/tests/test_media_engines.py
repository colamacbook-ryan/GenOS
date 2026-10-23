# ruff: noqa: E501
"""L5 — engine STT/OCR (giả, faster-whisper tại máy qua HTTP giả, bộ định tuyến chưa có đường âm thanh) + hạ tầng đo S5."""

import json
from typing import Any

import httpx
import pytest

from gh.biz.media import ocr, providers, transcribe
from gh.biz.media.meter import CAVEAT, Sample, fake_samples, intent_hit, load_manifest, normalise, run_benchmark, wer
from gh.biz.media.providers import (
    EngineError,
    FakeSttEngine,
    FakeVisionEngine,
    LocalWhisperEngine,
    RouterSttEngine,
    RouterVisionEngine,
    fake_image_bytes,
    fake_voice_bytes,
)


async def test_stt_gia_doc_nguoc_byte() -> None:
    r = await transcribe.transcribe(FakeSttEngine(), fake_voice_bytes("  xin\x00 chào\x07  "), "audio/ogg")
    assert r.text == "xin chào" and r.lang == "vi" and r.engine == "fake-stt"      # NUL/ký tự điều khiển bị bỏ


async def test_chu_qua_dai_bi_cat_va_engine_treo_thanh_timeout() -> None:
    r = await transcribe.transcribe(FakeSttEngine(), fake_voice_bytes("a" * 50_000), "audio/ogg")
    assert len(r.text) == transcribe.MAX_TEXT_CHARS
    with pytest.raises(EngineError) as e:
        await transcribe.transcribe(FakeSttEngine(latency_s=2), fake_voice_bytes("x"), "audio/ogg", timeout_s=0.05)
    assert e.value.code == "engine_timeout"


async def test_anh_ocr_mo_ta_song_song_loi_rieng() -> None:
    img = fake_image_bytes("Tổng 3.000.000đ", "Hoá đơn")
    full = await ocr.read_image(FakeVisionEngine(), img, "image/png")
    assert (full.ocr.text, full.description.text, full.errors) == ("Tổng 3.000.000đ", "Hoá đơn", {})  # type: ignore[union-attr]
    part = await ocr.read_image(FakeVisionEngine(fail_describe="engine_error"), img, "image/png")
    assert part.ocr is not None and part.description is None and part.errors == {"description": "engine_error"}
    with pytest.raises(EngineError) as e:
        await ocr.read_image(FakeVisionEngine(fail="engine_unavailable"), img, "image/png")
    assert e.value.code == "engine_unavailable"


async def test_router_chua_co_duong_am_thanh_la_engine_unavailable_khong_vo() -> None:
    class Router:      # giống ModelRouter hiện tại: chỉ có generate (văn bản)
        async def generate(self, *a: Any, **k: Any) -> Any: ...

    for call in (RouterSttEngine(Router(), "o").transcribe(b"OggS", "audio/ogg"), RouterVisionEngine(Router(), "o").ocr(b"x", "image/png")):
        with pytest.raises(EngineError) as e:
            await call
        assert e.value.code == "engine_unavailable"


async def test_router_co_ham_thi_dung_va_loi_nguon_ai_thanh_ma() -> None:
    class Routed:
        text = "chào anh"
        raw = {"model": "gemini-x"}

    class Good:
        async def transcribe_audio(self, org: Any, data: bytes, mime: str, *, lang_hint: str | None = None) -> Any:
            assert (data, mime, lang_hint) == (b"OggS", "audio/ogg", "vi")
            return Routed()

    r = await RouterSttEngine(Good(), "o").transcribe(b"OggS", "audio/ogg", lang_hint="vi")
    assert (r.text, r.engine, r.engine_version) == ("chào anh", "router:stt", "gemini-x")

    class ModelUnavailable(Exception):
        pass

    class Down:
        async def transcribe_audio(self, *a: Any, **k: Any) -> Any:
            raise ModelUnavailable("chi tiết nội bộ có token abc")

    with pytest.raises(EngineError) as e:
        await RouterSttEngine(Down(), "o").transcribe(b"x", "audio/ogg")
    assert e.value.code == "engine_unavailable" and "token" not in str(e.value)


def _client(handler: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_faster_whisper_tai_may_goi_api_kieu_openai() -> None:
    seen: dict[str, Any] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["url"], seen["body"] = str(req.url), req.content
        return httpx.Response(200, json={"text": "chuyển 5 triệu", "language": "vi",
                                         "segments": [{"avg_logprob": -0.1}, {"avg_logprob": -0.3}]})

    eng = LocalWhisperEngine(base_url="http://stt-local:8000/", client=_client(handler))
    r = await eng.transcribe(b"OggS", "audio/ogg", lang_hint="vi")
    assert seen["url"] == "http://stt-local:8000/v1/audio/transcriptions"
    assert b'name="language"' in seen["body"] and b"verbose_json" in seen["body"]
    assert (r.text, r.lang, r.engine) == ("chuyển 5 triệu", "vi", "faster-whisper") and 0.8 < (r.confidence or 0) < 0.85


@pytest.mark.parametrize(("status", "code"), [(503, "engine_unavailable"), (404, "engine_unavailable"), (400, "engine_error"),
                                              (200, "engine_error")])
async def test_faster_whisper_ma_loi(status: int, code: str) -> None:
    eng = LocalWhisperEngine(client=_client(lambda r: httpx.Response(status, content=b"not json")))
    with pytest.raises(EngineError) as e:
        await eng.transcribe(b"x", "audio/ogg")
    assert e.value.code == code


async def test_faster_whisper_container_chua_bat_la_unavailable() -> None:
    def boom(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("tên stt-local không phân giải được")

    with pytest.raises(EngineError) as e:
        await LocalWhisperEngine(client=_client(boom)).transcribe(b"x", "audio/ogg")
    assert e.value.code == "engine_unavailable"
    to = LocalWhisperEngine(client=_client(lambda r: (_ for _ in ()).throw(httpx.ReadTimeout("chậm"))))
    with pytest.raises(EngineError) as e2:
        await to.transcribe(b"x", "audio/ogg")
    assert e2.value.code == "engine_timeout"


def test_chon_engine_mac_dinh_la_bo_dinh_tuyen(monkeypatch: pytest.MonkeyPatch) -> None:
    assert providers.provider_name({}) == "router" and providers.provider_name(None) == "router"
    assert providers.provider_name({"media": {"stt_provider": "local"}}) == "local"
    assert providers.provider_name({"media": {"stt_provider": "rm -rf"}}) == "router"
    assert providers.provider_name({"media": {"stt_provider": "fake"}}) == "fake"         # GH_ENV=test cho phép
    from gh.config import get_settings

    monkeypatch.setenv("GH_ENV", "production")
    get_settings.cache_clear()
    try:
        assert providers.provider_name({"media": {"stt_provider": "fake"}}) == "router"  # production: engine giả bị từ chối
    finally:
        monkeypatch.undo()
        get_settings.cache_clear()
    assert isinstance(providers.stt_engine("router", router=object(), org_id=1), RouterSttEngine)
    assert isinstance(providers.stt_engine("local"), LocalWhisperEngine)


def test_dia_chi_local_chi_tu_bien_moi_truong(monkeypatch: pytest.MonkeyPatch) -> None:
    assert LocalWhisperEngine().base_url == "http://stt-local:8000"
    monkeypatch.setenv("GH_STT_LOCAL_URL", "http://10.0.0.9:9000/")
    assert LocalWhisperEngine().base_url == "http://10.0.0.9:9000"


# ─── S5: hạ tầng đo ───────────────────────────────────────────────────────────────────────────────────────────────────

def test_wer_va_chuan_hoa_tieng_viet() -> None:
    assert normalise("Chuyển 1.500.000đ, ngay!") == "chuyển 1500000đ ngay"
    assert wer("anh gửi chị 5 triệu", "anh gửi chị 5 triệu") == 0.0
    assert wer("anh gửi chị 5 triệu", "anh gửi chị 6 triệu") == pytest.approx(0.2)
    assert wer("anh gửi chị", "anh gui chi") == pytest.approx(2 / 3)           # sai dấu = sai (không bỏ dấu thanh)
    assert wer("", "") == 0.0 and wer("", "x") == 1.0 and wer("a", "") == 1.0
    assert wer("a", "x y z w") == 1.0                                          # trần 1.0


def test_dung_y_theo_facts_so_va_cum_tu() -> None:
    assert intent_hit(["5 triệu", "ngày mai"], "anh sẽ gửi 5 triệu vào ngày mai nhé")
    assert not intent_hit(["5 triệu", "ngày mai"], "anh sẽ gửi 6 triệu vào ngày mai")
    assert intent_hit(["1.500.000"], "chuyển 1,500,000 đồng")                  # so theo chữ số, bỏ dấu ngăn cách
    assert not intent_hit(["5 triệu"], "năm triệu")
    assert intent_hit([], "bất kỳ")


async def test_bench_voi_engine_gia_do_duoc_ti_le_va_thoi_gian() -> None:
    def drop_last_word(t: str) -> str:
        return t.rsplit(" ", 1)[0]

    rep = await run_benchmark(FakeSttEngine(), fake_samples(30))
    assert (rep.n, rep.failed, rep.wer_mean, rep.intent_rate) == (30, 0, 0.0, 1.0)
    assert rep.fake and rep.caveat == CAVEAT and "chưa đo trên voice thật" in rep.summary()
    assert rep.seconds_per_audio_minute is not None and rep.seconds_per_audio_minute < 1     # engine giả tức thì: 10 giây âm thanh / mẫu
    bad = await run_benchmark(FakeSttEngine(mutate=drop_last_word), fake_samples(10))       # mất "ngày mai" → mất "mai"
    assert bad.intent_rate == 0.0 and (bad.wer_mean or 0) > 0
    slow = await run_benchmark(FakeSttEngine(latency_s=0.05), fake_samples(2))
    assert (slow.seconds_per_audio_minute or 0) >= 0.05 * 2 / (20 / 60) - 0.01             # đo thời gian treo tường thật
    failing = await run_benchmark(FakeSttEngine(fail="engine_unavailable"), fake_samples(3))
    assert (failing.failed, failing.intent_rate, failing.wer_mean) == (3, 0.0, None)         # mẫu lỗi tính là không đúng ý


async def test_manifest_va_chong_thoat_thu_muc(tmp_path: Any) -> None:
    (tmp_path / "v1.ogg").write_bytes(fake_voice_bytes("alo"))
    (tmp_path / "m.json").write_text(json.dumps([{"id": "v1", "file": "v1.ogg", "reference": "alo", "facts": ["alo"], "seconds": 3}]))
    [s] = load_manifest(tmp_path / "m.json")
    assert isinstance(s, Sample) and s.seconds == 3 and s.data == fake_voice_bytes("alo")
    (tmp_path / "evil.json").write_text(json.dumps([{"id": "x", "file": "../../etc/passwd", "reference": "x"}]))
    with pytest.raises(ValueError):
        load_manifest(tmp_path / "evil.json")
