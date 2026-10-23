# ruff: noqa: E501
"""Engine STT / OCR / mô tả ảnh (spec §4E). Giao diện `SttEngine` / `VisionEngine`; ba cài đặt:

- `RouterSttEngine` / `RouterVisionEngine` — MẶC ĐỊNH, qua bộ định tuyến (`gh.providers.router.ModelRouter`) tức gói thuê bao
  Sếp đã nối, 0 ₫ thêm (Q14). **Điểm cắm còn chờ**: `Message` của bộ định tuyến hiện chỉ có văn bản (`content: str`), chưa có
  đường âm thanh/ảnh. Engine gọi `router.transcribe_audio(...)` / `router.read_image(...)` / `router.describe_image(...)` NẾU
  chúng tồn tại; chưa có ⇒ `EngineError('engine_unavailable')` — việc ghi trạng thái "chưa có công cụ đọc", tự thử lại, KHÔNG
  làm hỏng pipeline. Thêm các hàm đó vào router (gói khác) là đủ để bật, không phải sửa gói này.
- `LocalWhisperEngine` — tuỳ chọn `faster-whisper` chạy tại máy (service `stt-local` profile tắt mặc định trong compose,
  API kiểu OpenAI `/v1/audio/transcriptions`). Địa chỉ lấy từ biến môi trường của máy (`GH_STT_LOCAL_URL`), KHÔNG từ cài đặt
  người dùng ⇒ không thể bị trỏ ra địa chỉ tuỳ ý.
- `FakeSttEngine` / `FakeVisionEngine` — engine giả cho test và cho hạ tầng đo S5 (chỉ khi `GH_ENV` ∈ test/eval/development).
"""

from __future__ import annotations

import asyncio
import math
import os
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from gh.config import get_settings

PROVIDERS = ("router", "local", "fake")
DEFAULT_PROVIDER = "router"
FAKE_ENVS = ("test", "eval", "development")
LOCAL_URL_ENV = "GH_STT_LOCAL_URL"
LOCAL_MODEL_ENV = "GH_STT_LOCAL_MODEL"
DEFAULT_LOCAL_URL = "http://stt-local:8000"
DEFAULT_LOCAL_MODEL = "Systran/faster-whisper-small"


class EngineError(Exception):
    """Lỗi engine với MÃ (`engine_unavailable` | `engine_error` | `engine_timeout`); thông điệp không mang nội dung file."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class EngineText:
    text: str
    lang: str | None
    confidence: float | None
    engine: str
    engine_version: str


class SttEngine(Protocol):
    name: str
    version: str
    is_fake: bool

    async def transcribe(self, data: bytes, mime: str, *, lang_hint: str | None = None) -> EngineText: ...


class VisionEngine(Protocol):
    name: str
    version: str
    is_fake: bool

    async def ocr(self, data: bytes, mime: str) -> EngineText: ...
    async def describe(self, data: bytes, mime: str) -> EngineText: ...


# ─── bộ định tuyến (mặc định, gói thuê bao) ─────────────────────────────────────────────────────────────────────────

class _RouterBase:
    name = "router"
    version = "router/1"
    is_fake = False

    def __init__(self, router: Any, org_id: Any):
        self.router, self.org_id = router, org_id

    async def _call(self, method: str, *args: Any, **kw: Any) -> Any:
        fn = getattr(self.router, method, None)
        if fn is None:
            raise EngineError("engine_unavailable")
        try:
            return await fn(self.org_id, *args, **kw)
        except EngineError:
            raise
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 — nguồn AI hết chuỗi/lỗi tạm: việc nằm chờ, thử lại
            code = "engine_unavailable" if type(e).__name__ in ("ModelUnavailable",) else "engine_error"
            raise EngineError(code) from None

    @staticmethod
    def _wrap(res: Any, kind: str) -> EngineText:
        text = str(getattr(res, "text", res) or "")
        raw = getattr(res, "raw", None) or {}
        model = str(raw.get("model") or "router") if isinstance(raw, dict) else "router"
        return EngineText(text=text, lang=None, confidence=None, engine=f"router:{kind}", engine_version=model)


class RouterSttEngine(_RouterBase):
    async def transcribe(self, data: bytes, mime: str, *, lang_hint: str | None = None) -> EngineText:
        return self._wrap(await self._call("transcribe_audio", data, mime, lang_hint=lang_hint), "stt")


class RouterVisionEngine(_RouterBase):
    async def ocr(self, data: bytes, mime: str) -> EngineText:
        return self._wrap(await self._call("read_image", data, mime), "ocr")

    async def describe(self, data: bytes, mime: str) -> EngineText:
        return self._wrap(await self._call("describe_image", data, mime), "vision")


# ─── faster-whisper tại máy (tuỳ chọn) ──────────────────────────────────────────────────────────────────────────────

class LocalWhisperEngine:
    name = "faster-whisper"
    is_fake = False

    def __init__(self, base_url: str | None = None, model: str | None = None, timeout_s: float = 300.0,
                 client: httpx.AsyncClient | None = None):
        self.base_url = (base_url or os.environ.get(LOCAL_URL_ENV) or DEFAULT_LOCAL_URL).rstrip("/")
        self.model = model or os.environ.get(LOCAL_MODEL_ENV) or DEFAULT_LOCAL_MODEL
        self.version = self.model
        self.timeout_s = timeout_s
        self._client = client

    async def transcribe(self, data: bytes, mime: str, *, lang_hint: str | None = None) -> EngineText:
        form = {"model": self.model, "response_format": "verbose_json"}
        if lang_hint:
            form["language"] = lang_hint
        try:
            if self._client is not None:
                r = await self._client.post(f"{self.base_url}/v1/audio/transcriptions", data=form,
                                            files={"file": ("voice", data, mime)}, timeout=self.timeout_s)
            else:
                async with httpx.AsyncClient() as c:
                    r = await c.post(f"{self.base_url}/v1/audio/transcriptions", data=form,
                                     files={"file": ("voice", data, mime)}, timeout=self.timeout_s)
        except httpx.TimeoutException:
            raise EngineError("engine_timeout") from None
        except httpx.HTTPError:
            raise EngineError("engine_unavailable") from None      # container chưa bật / chưa tải model
        if r.status_code >= 500 or r.status_code == 404:
            raise EngineError("engine_unavailable")
        if r.status_code != 200:
            raise EngineError("engine_error")
        try:
            j = r.json()
        except ValueError:
            raise EngineError("engine_error") from None
        segs = [s for s in (j.get("segments") or []) if isinstance(s, dict) and isinstance(s.get("avg_logprob"), (int, float))]
        conf = round(min(1.0, max(0.0, math.exp(sum(s["avg_logprob"] for s in segs) / len(segs)))), 3) if segs else None
        return EngineText(text=str(j.get("text") or ""), lang=j.get("language") or lang_hint, confidence=conf,
                          engine=self.name, engine_version=self.model)


# ─── engine giả (test + hạ tầng đo S5) ──────────────────────────────────────────────────────────────────────────────

FAKE_VOICE_PREFIX = b"OggS\x00FAKE:"
FAKE_IMAGE_PREFIX = b"\x89PNG\r\n\x1a\nFAKE:"


def fake_voice_bytes(transcript: str) -> bytes:
    """Byte qua được kiểm MIME (`OggS`) mà engine giả đọc ngược ra `transcript` — để test không cần file âm thanh thật."""
    return FAKE_VOICE_PREFIX + transcript.encode()


def fake_image_bytes(ocr_text: str, description: str = "") -> bytes:
    return FAKE_IMAGE_PREFIX + ocr_text.encode() + b"\x00" + description.encode()


class FakeSttEngine:
    """`transcribe` đọc ngược `fake_voice_bytes`; `mutate(text)` giả lập lỗi nghe (đo S5); `fail` ném EngineError; `latency_s`
    ngủ thật để đo thời gian."""

    name = "fake-stt"
    version = "fake/1"
    is_fake = True

    def __init__(self, *, mutate: Callable[[str], str] | None = None, fail: str | None = None, latency_s: float = 0.0,
                 confidence: float | None = 0.9):
        self.mutate, self.fail, self.latency_s, self.confidence = mutate, fail, latency_s, confidence
        self.calls = 0

    async def transcribe(self, data: bytes, mime: str, *, lang_hint: str | None = None) -> EngineText:
        self.calls += 1
        if self.latency_s:
            await asyncio.sleep(self.latency_s)
        if self.fail:
            raise EngineError(self.fail)
        text = data[len(FAKE_VOICE_PREFIX):].decode(errors="replace") if data.startswith(FAKE_VOICE_PREFIX) else ""
        return EngineText(text=self.mutate(text) if self.mutate else text, lang=lang_hint or "vi",
                          confidence=self.confidence, engine=self.name, engine_version=self.version)


class FakeVisionEngine:
    name = "fake-vision"
    version = "fake/1"
    is_fake = True

    def __init__(self, *, fail: str | None = None, fail_describe: str | None = None):
        self.fail, self.fail_describe = fail, fail_describe
        self.calls = 0

    def _parts(self, data: bytes) -> tuple[str, str]:
        body = data[len(FAKE_IMAGE_PREFIX):] if data.startswith(FAKE_IMAGE_PREFIX) else b""
        a, _, b = body.partition(b"\x00")
        return a.decode(errors="replace"), b.decode(errors="replace")

    async def ocr(self, data: bytes, mime: str) -> EngineText:
        self.calls += 1
        if self.fail:
            raise EngineError(self.fail)
        return EngineText(self._parts(data)[0], "vi", 0.9, self.name, self.version)

    async def describe(self, data: bytes, mime: str) -> EngineText:
        self.calls += 1
        if self.fail or self.fail_describe:
            raise EngineError(self.fail_describe or self.fail or "engine_error")
        return EngineText(self._parts(data)[1], "vi", 0.8, self.name, self.version)


# ─── chọn engine ────────────────────────────────────────────────────────────────────────────────────────────────────

def provider_name(org_settings: Mapping[str, Any] | None) -> str:
    """`core.organizations.settings->'media'->>'stt_provider'` (bật tại máy trong Cài đặt); lạ ⇒ mặc định `router`.
    `fake` chỉ hợp lệ ngoài production."""
    media = (org_settings or {}).get("media")
    name = str(media.get("stt_provider")) if isinstance(media, dict) and media.get("stt_provider") else DEFAULT_PROVIDER
    if name not in PROVIDERS or (name == "fake" and get_settings().env not in FAKE_ENVS):
        return DEFAULT_PROVIDER
    return name


def stt_engine(provider: str, *, router: Any = None, org_id: Any = None) -> SttEngine:
    if provider == "local":
        return LocalWhisperEngine()
    if provider == "fake":
        return FakeSttEngine()
    return RouterSttEngine(router, org_id)


def vision_engine(*, router: Any = None, org_id: Any = None, provider: str = DEFAULT_PROVIDER) -> VisionEngine:
    if provider == "fake":
        return FakeVisionEngine()
    return RouterVisionEngine(router, org_id)


def now_s() -> float:
    return time.monotonic()
