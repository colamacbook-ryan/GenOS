"""Client gọi model: Gemini API, API kiểu OpenAI (DeepSeek, tuỳ chọn), Antigravity CLI (`agy`, bản chính hãng).

Mọi client trả `Completion` hoặc ném một trong các lỗi phân loại dưới đây để bộ định tuyến quyết định
đổi khoá (429), bỏ qua nhà cung cấp (hết hạn mức, xác thực) hay tính lỗi cho ngắt mạch (tạm thời).
"""

import asyncio
import base64
import contextlib
import fcntl
import logging
import os
import re
import shutil
import signal
import stat
import tempfile
import time
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
import orjson

from gh.chassis.mcp_client import McpBlockedNetwork, McpError, pinned_client, pinned_request

EMBED_DIM = 768
DEFAULT_ENDPOINT = {
    "gemini": "https://generativelanguage.googleapis.com/v1beta",
    "deepseek": "https://api.deepseek.com/v1",
    "openai_compat": "",
}


class ProviderError(Exception):
    """Lỗi tạm thời (mạng, 5xx, hết giờ) — tính vào ngắt mạch."""


class RateLimited(ProviderError):  # noqa: N818
    def __init__(self, msg: str, retry_after: float | None = None):
        super().__init__(msg)
        self.retry_after = retry_after


class AuthFailed(ProviderError):  # noqa: N818
    """Khoá / phiên sai hoặc hết hạn."""


class QuotaExhausted(ProviderError):  # noqa: N818
    """Nhà cung cấp báo hết hạn mức (không phải giới hạn tốc độ)."""


class BadRequest(ProviderError):
    """Yêu cầu không hợp lệ — đổi khoá không giúp được."""


class ModelRejected(BadRequest):
    """CLI không nhận model / mức suy nghĩ này (v0.1.31: gọi thử trước khi lưu model).

    v0.1.32: chỉ dùng cho lỗi "không biết model" THẬT (mẫu chính xác bên dưới); `what` = "model" | "effort";
    `available` = danh sách CLI tự nêu trong lỗi (agy: "Invalid model %q (available: %s)"); `raw` = lỗi gốc."""

    def __init__(self, msg: str, *, what: str = "model", available: list[str] | None = None, raw: str = ""):
        super().__init__(msg)
        self.what, self.available, self.raw = what, available or [], raw or msg
        # v0.1.58: cặp (model, mức) của lượt gọi thử CUỐI bị từ chối — bộ định tuyến gắn vào để câu báo lỗi nêu đúng
        # thứ đã gửi (trước đây dựng câu bằng model "" vì chưa có lượt nào thành công).
        self.model: str | None = None
        self.effort: str | None = None


# Mẫu lỗi "không nhận model" — CHÍNH XÁC (v0.1.32). Nguồn: chuỗi trong agy 1.2.9 ("invalid model selection (--model %q
# --effort %q)", "Invalid model %q (available: %s)", "unknown model %q", "unknown model name %s", "invalid --effort %q
# (valid: %s)", "--effort is not supported for the current model", "--effort is not supported for model %q") và Claude
# Code ("There's an issue with the selected model", API 404 "not_found_error"). Trước đây mọi câu có "model" + "invalid"
# đều bị coi là từ chối model → báo nhầm "CLI không nhận model" cho lỗi khác.
# KHÔNG khớp (review v0.1.32): "unknown model tier: %q" / "unknown model key %s" (nội bộ agy), "The model is not
# available right now … (503)" (lỗi tạm thời — để bộ định tuyến thử lại / chuyển nguồn).
_MODEL_REJECT = re.compile(
    r"invalid model selection|invalid model\s+[\"'“]|unknown model(?: name)?\s+[\"'“]|unknown model name\b"
    r"|model not found|model_not_found|no such model|issue with the selected model|not_found_error|unsupported model"
    r"|not a valid model|model .{0,60} (?:does not exist|is not supported)", re.I)
_EFFORT_REJECT = re.compile(
    r"invalid (?:--)?effort|effort isn't adjustable|unsupported effort|--effort is not supported", re.I)


def model_rejected(msg: str) -> bool:
    return bool(_MODEL_REJECT.search(msg) or _EFFORT_REJECT.search(msg))


def rejection(msg: str) -> ModelRejected | None:
    """ModelRejected nếu `msg` đúng là lỗi CLI không nhận model/effort, kèm danh sách CLI tự nêu (nếu có)."""
    if not model_rejected(msg):
        return None
    from gh.providers.catalog import parse_available

    # "invalid model selection (--model … --effort …): invalid --effort …" = model đúng, MỨC sai.
    bad_model = re.search(r"invalid model\s+[\"'“]|unknown model(?: name)?\s+[\"'“]|unknown model name\b"
                          r"|model not found|issue with the selected model", msg, re.I)
    what = "effort" if _EFFORT_REJECT.search(msg) and not bad_model else "model"
    return ModelRejected(msg[:300], what=what, available=parse_available(msg), raw=msg[-2000:])


# ─── Chẩn đoán CLI (v0.1.32): đầu ra thô đã che bí mật để Boss gửi khi còn lỗi ────────────────────────────

_REDACT: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(https?://[^\s?\"']+)\?[^\s\"']*"), r"\1?…"),                          # link OAuth (code, state…)
    (re.compile(r'("(?:access_token|refresh_token|id_token|token|apiKey|api_key|accessToken|refreshToken|'
                r'secret|password)"\s*:\s*)"[^"]*"', re.I), r'\1"***"'),
    (re.compile(r"\bBearer\s+\S+", re.I), "Bearer ***"),
    (re.compile(r"\b(?:ya29\.|1//|sk-ant-|sk-|AIza)[\w.\-]{8,}"), "***"),
    (re.compile(r"\beyJ[\w-]{6,}\.[\w-]{6,}\.[\w-]*"), "***"),                                   # JWT
    (re.compile(r"\b[A-Za-z0-9_\-+/=]{48,}"), "***"),                                             # chuỗi dài kiểu token
    (re.compile(r"\b([A-Za-z0-9._%+-])[A-Za-z0-9._%+-]*@([A-Za-z0-9.-]+\.[A-Za-z]{2,})\b"), r"\1***@\2"),
)


def redact(text: str, limit: int = 6000) -> str:
    """Che token, link OAuth, email (giữ chữ đầu + tên miền) trong đầu ra CLI trước khi hiện / cho chép."""
    for pat, sub in _REDACT:
        text = pat.sub(sub, text)
    return text if len(text) <= limit else text[:limit] + f"\n… (cắt bớt {len(text) - limit} ký tự)"


async def diag_step(label: str, argv_shown: list[str], run: Any, limit_s: float) -> dict[str, Any]:
    """Chạy một bước chẩn đoán: {label, command, exit_code, stdout, stderr, ms, note}. Không bao giờ ném."""
    started = time.monotonic()
    step: dict[str, Any] = {"label": label, "command": redact(" ".join(argv_shown)), "exit_code": None, "stdout": "",
                            "stderr": "", "ms": 0, "note": None}
    try:
        async with asyncio.timeout(limit_s):
            code, out, err = await run()
        step.update(exit_code=code, stdout=redact(out.decode(errors="replace")),
                    stderr=redact(err.decode(errors="replace")))
    except TimeoutError:
        step["note"] = f"quá {limit_s:.0f}s không xong"
    except Exception as e:  # noqa: BLE001 — chẩn đoán phải trả được mọi lỗi
        step["note"] = redact(f"{type(e).__name__}: {e}")[:500]
    step["ms"] = int((time.monotonic() - started) * 1000)
    return step


def _skipped(label: str, argv: list[str], note: str) -> dict[str, Any]:
    return {"label": label, "command": redact(" ".join(argv)), "exit_code": None, "stdout": "", "stderr": "",
            "ms": 0, "note": note}


@dataclass
class Message:
    role: str      # system | user | assistant
    content: str


@dataclass
class Completion:
    text: str
    tokens_in: int | None = None
    tokens_out: int | None = None
    raw: dict[str, Any] = field(default_factory=dict)


def _retry_after(resp: httpx.Response) -> float | None:
    v = resp.headers.get("retry-after")
    try:
        return float(v) if v else None
    except ValueError:
        return None


def _raise_for(resp: httpx.Response) -> None:
    if resp.status_code < 400:
        return
    # v0.1.45: thân lỗi có thể phản chiếu header (khoá) / dữ liệu — che rồi cắt 200 ký tự trước khi vào lỗi/log.
    # Phân loại 429 trên thân ĐÃ CHE đủ 4000 ký tự (Gemini để dấu "…PerDay…" sâu trong JSON), chỉ cắt cho thông điệp.
    body_full = redact(resp.text[:4000])
    body = body_full[:200]
    if resp.status_code == 429:
        low = body_full.lower()
        if "quota" in low and "day" in low:
            raise QuotaExhausted(f"429 hết hạn mức: {body}")
        raise RateLimited(f"429: {body}", _retry_after(resp))
    if resp.status_code in (401, 403):
        raise AuthFailed(f"{resp.status_code}: {body}")
    if resp.status_code == 402:
        raise QuotaExhausted(f"402: {body}")
    if resp.status_code in (400, 404, 422):
        raise BadRequest(f"{resp.status_code}: {body}")
    raise ProviderError(f"{resp.status_code}: {body}")


PROVIDER_FORBIDDEN_MSG = "Địa chỉ nhà cung cấp trỏ vào vùng mạng bị cấm"


class HttpClient:
    """v0.1.45 (F-49): mọi lời gọi ghim DNS (`gh.chassis.mcp_client.pinned_request`) — phân giải MỘT lần, cấm
    link-local/siêu dữ liệu, 0.0.0.0, multicast và tên dịch vụ compose của Gen-Harness, rồi kết nối thẳng IP đã kiểm
    (Host + SNI theo tên gốc). Nhà cung cấp luôn được ra mạng công cộng; KHÔNG ép https lúc gọi (ép ở bước ghi cấu
    hình). Không đọc proxy môi trường (proxy tự phân giải lại tên máy — mất tác dụng ghim), như Gen-hub v0.1.27."""

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None, timeout: float = 60.0):
        self._transport = transport
        self._timeout = timeout

    async def _request(self, method: str, url: str, headers: dict[str, str], **kw: Any) -> httpx.Response:
        try:
            async with pinned_client(self._transport, self._timeout) as c:
                return await pinned_request(c, method, url, headers=headers, **kw)
        except McpBlockedNetwork as e:
            if "không hợp lệ" in str(e):   # sai dạng (thiếu tên máy, cổng lạ) — không phải "vùng mạng bị cấm"
                raise BadRequest("Địa chỉ nhà cung cấp không hợp lệ (cần dạng https://<máy chủ>/v1)") from e
            raise BadRequest(f"{PROVIDER_FORBIDDEN_MSG}: {e}") from e
        except McpError as e:   # không phân giải được tên máy — tạm thời (DNS chập chờn)
            raise ProviderError(str(e)) from e
        except httpx.HTTPError as e:
            raise ProviderError(f"mạng: {redact(str(e), 300)}") from e

    async def _post(self, url: str, json: dict[str, Any], headers: dict[str, str]) -> dict[str, Any]:
        resp = await self._request("POST", url, headers, json=json)
        _raise_for(resp)
        return resp.json()  # type: ignore[no-any-return]

    async def _get(self, url: str, headers: dict[str, str]) -> dict[str, Any]:
        resp = await self._request("GET", url, headers)
        _raise_for(resp)
        return resp.json()  # type: ignore[no-any-return]


class GeminiClient(HttpClient):
    kind = "gemini"

    def __init__(self, endpoint: str | None, key: str, **kw: Any):
        super().__init__(**kw)
        self.base = (endpoint or DEFAULT_ENDPOINT["gemini"]).rstrip("/")
        self.headers = {"x-goog-api-key": key, "content-type": "application/json"}

    async def generate(self, model: str, messages: list[Message], *, json_mode: bool, temperature: float) -> Completion:
        system = "\n\n".join(m.content for m in messages if m.role == "system")
        contents = [{"role": "model" if m.role == "assistant" else "user", "parts": [{"text": m.content}]}
                    for m in messages if m.role != "system"]
        body: dict[str, Any] = {"contents": contents, "generationConfig": {"temperature": temperature}}
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}
        if json_mode:
            body["generationConfig"]["responseMimeType"] = "application/json"
        data = await self._post(f"{self.base}/models/{model}:generateContent", body, self.headers)
        cands = data.get("candidates") or []
        if not cands:
            raise ProviderError(f"không có kết quả: {str(data.get('promptFeedback'))[:200]}")
        text = "".join(p.get("text", "") for p in (cands[0].get("content") or {}).get("parts", []))
        usage = data.get("usageMetadata") or {}
        return Completion(text, usage.get("promptTokenCount"), usage.get("candidatesTokenCount"), data)

    async def embed(self, model: str, texts: list[str]) -> list[list[float]]:
        reqs = [{"model": f"models/{model}", "content": {"parts": [{"text": t}]}, "outputDimensionality": EMBED_DIM}
                for t in texts]
        data = await self._post(f"{self.base}/models/{model}:batchEmbedContents", {"requests": reqs}, self.headers)
        return [e["values"] for e in data.get("embeddings", [])]

    async def list_models(self) -> list[str]:
        data = await self._get(f"{self.base}/models", self.headers)
        return [m["name"].removeprefix("models/") for m in data.get("models", [])]


class OpenAICompatClient(HttpClient):
    kind = "openai_compat"

    def __init__(self, endpoint: str | None, key: str, **kw: Any):
        super().__init__(**kw)
        if not endpoint:
            raise BadRequest("Thiếu endpoint")
        self.base = endpoint.rstrip("/").removesuffix("/chat/completions")
        self.headers = {"authorization": f"Bearer {key}", "content-type": "application/json"}

    async def generate(self, model: str, messages: list[Message], *, json_mode: bool, temperature: float) -> Completion:
        body: dict[str, Any] = {"model": model, "temperature": temperature,
                                "messages": [{"role": m.role, "content": m.content} for m in messages]}
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        try:
            data = await self._post(f"{self.base}/chat/completions", body, self.headers)
        except BadRequest:
            if not json_mode:
                raise
            body.pop("response_format")   # model không hỗ trợ JSON mode → vẫn yêu cầu JSON trong prompt
            data = await self._post(f"{self.base}/chat/completions", body, self.headers)
        choice = (data.get("choices") or [{}])[0]
        usage = data.get("usage") or {}
        return Completion((choice.get("message") or {}).get("content") or "", usage.get("prompt_tokens"),
                          usage.get("completion_tokens"), data)

    async def embed(self, model: str, texts: list[str]) -> list[list[float]]:
        data = await self._post(f"{self.base}/embeddings", {"model": model, "input": texts, "dimensions": EMBED_DIM},
                                self.headers)
        return [d["embedding"] for d in sorted(data.get("data", []), key=lambda d: d.get("index", 0))]

    async def list_models(self) -> list[str]:
        data = await self._get(f"{self.base}/models", self.headers)
        return [m["id"] for m in data.get("data", [])]


# ─── Antigravity CLI ─────────────────────────────────────────────────────────

TOKEN_FILE = "antigravity-oauth-token"

log = logging.getLogger("gh.providers")

# ─── Khoá (flock) quanh làm mới / ghi tệp token OAuth của agy dùng chung (gói G, P3) ─────────────────
#: Tệp khoá nằm cạnh tệp token (cùng volume api/worker). Khoá tư vấn `flock` theo mô tả tệp mở ⇒ chặn được giữa các tiến
#: trình (api ↔ worker) và giữa các lượt gọi trong cùng tiến trình.
TOKEN_LOCK_FILE = ".gh-token.lock"
#: Token truy cập sắp hết hạn trong khoảng này ⇒ lượt agy kế tiếp CÓ THỂ làm mới token (refresh token xoay vòng, dùng
#: một lần): hai tiến trình agy cùng làm mới sẽ làm hỏng phiên ⇒ các lượt như vậy chạy TUẦN TỰ (khoá độc quyền).
TOKEN_REFRESH_WINDOW_S = 600.0
#: Chờ khoá tối đa (giây) — hết hạn thì chạy tiếp KHÔNG khoá (ghi cảnh báo): chậm hay treo không thành chết dịch vụ.
TOKEN_LOCK_WAIT_S = 60.0
TOKEN_WRITER_LOCK_WAIT_S = 10.0


def _lock_path(cli_home: str | Path) -> Path:
    return cli_home_dir(str(cli_home)) / TOKEN_LOCK_FILE


def _try_flock(path: Path, *, exclusive: bool, wait: float) -> int | None:
    """Mở tệp khoá và `flock` (không chặn, thử lại mỗi 50 ms tới `wait` giây). Trả fd đang giữ khoá, hoặc None nếu hết
    hạn / không mở được tệp khoá (thư mục chưa có, chỉ đọc…)."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # Review v0.1.63 (P1-1): O_NOFOLLOW — tệp khoá là liên kết tượng trưng thì không mở (không tạo/khoá tệp lạ).
        fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    except OSError:
        return None
    deadline = time.monotonic() + max(0.0, wait)
    mode = (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH) | fcntl.LOCK_NB
    while True:
        try:
            fcntl.flock(fd, mode)
            return fd
        except BlockingIOError:
            if time.monotonic() >= deadline:
                os.close(fd)
                return None
            time.sleep(0.05)
        except OSError:
            os.close(fd)
            return None


def _release(fd: int | None) -> None:
    if fd is not None:
        with contextlib.suppress(OSError):
            fcntl.flock(fd, fcntl.LOCK_UN)
        with contextlib.suppress(OSError):
            os.close(fd)


@contextlib.contextmanager
def token_flock(cli_home: str | Path, *, exclusive: bool = True,
                wait: float = TOKEN_WRITER_LOCK_WAIT_S) -> Iterator[bool]:
    """Khoá tư vấn (flock) cho tệp token OAuth của agy — dùng cho mã GHI/đổi tệp (park/unpark/ghi phiên: độc quyền,
    ngắn). Yield True nếu giữ được khoá; hết hạn chờ (`wait`) ⇒ yield False và đi tiếp không khoá (ưu tiên dịch vụ
    không treo hơn khoá tuyệt đối)."""
    fd = _try_flock(_lock_path(cli_home), exclusive=exclusive, wait=wait)
    if fd is None:
        log.warning("Không lấy được khoá tệp phiên Antigravity trong %.0fs — đi tiếp không khoá", wait)
    try:
        yield fd is not None
    finally:
        _release(fd)


@contextlib.asynccontextmanager
async def token_flock_async(cli_home: str | Path, *, exclusive: bool = True,
                            wait: float = TOKEN_LOCK_WAIT_S) -> AsyncIterator[bool]:
    """Như `token_flock` nhưng chờ khoá trong luồng phụ (không chặn vòng lặp sự kiện) — cho lượt gọi agy chạy lâu."""
    fd = await asyncio.to_thread(_try_flock, _lock_path(cli_home), exclusive=exclusive, wait=wait)
    if fd is None:
        log.warning("Không lấy được khoá tệp phiên Antigravity trong %.0fs — chạy tiếp không khoá", wait)
    try:
        yield fd is not None
    finally:
        await asyncio.to_thread(_release, fd)


def token_expiry(raw: bytes) -> float | None:
    """Hạn của token truy cập (epoch giây) trong tệp phiên agy: `expiry` / `expires_at` (số hoặc ISO) rồi `exp` của
    id_token. Không đọc được ⇒ None. Không đọc, không log giá trị token."""
    try:
        data = orjson.loads(raw)
    except orjson.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    # Định dạng THẬT của agy 1.2.9 (đã kiểm trên phiên đăng nhập thật): {"token": {access_token, token_type,
    # refresh_token, expiry}, "auth_method", "id_token"} — `expiry` nằm trong `token`, không ở cấp đầu.
    inner = data.get("token")
    exp: Any = data.get("expiry") or data.get("expires_at")
    if exp is None and isinstance(inner, dict):
        exp = inner.get("expiry") or inner.get("expires_at")
    if exp is None:
        tok = data.get("id_token")
        if isinstance(tok, str) and tok.count(".") >= 2:
            part = tok.split(".")[1]
            try:
                claims = orjson.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
            except (ValueError, orjson.JSONDecodeError):
                claims = {}
            exp = claims.get("exp") if isinstance(claims, dict) else None
    if isinstance(exp, bool):
        return None
    if isinstance(exp, int | float):
        return float(exp)
    if isinstance(exp, str):
        with contextlib.suppress(ValueError):
            return datetime.fromisoformat(exp.replace("Z", "+00:00")).timestamp()
    return None


_REFRESH_KEYS = ("refresh_token", "refreshToken")


def _has_refresh_token(raw: bytes) -> bool:
    try:
        data = orjson.loads(raw)
    except orjson.JSONDecodeError:
        return False
    if not isinstance(data, dict):
        return False
    inner = data.get("token")  # định dạng thật của agy: refresh_token nằm trong `token`
    return any(isinstance(src.get(k), str) and src.get(k) for src in (data, inner if isinstance(inner, dict) else {})
               for k in _REFRESH_KEYS)


def token_needs_refresh(raw: bytes, *, now: float | None = None, window: float = TOKEN_REFRESH_WINDOW_S) -> bool:
    """Token truy cập đã hoặc sắp hết hạn (trong `window` giây) ⇒ lượt agy kế tiếp có thể làm mới token.

    Review lần 2 v0.1.62: tệp có refresh token mà KHÔNG đọc được hạn (thiếu / sai định dạng `expiry`, `expires_at`,
    `exp`) ⇒ coi như CÓ THỂ làm mới (khoá độc quyền, chậm hơn nhưng không làm hỏng phiên); tệp không phải JSON hoặc
    không có refresh token thì không có gì để làm mới ⇒ không khoá."""
    exp = token_expiry(raw)
    if exp is None:
        return _has_refresh_token(raw)
    return exp - (time.time() if now is None else now) <= window


# ─── Tệp phiên trong volume: đọc/ghi không đi theo liên kết tượng trưng (review v0.1.63, P1-1) ───────────────────
#: Tệp token OAuth của agy thật chỉ vài KiB; lớn hơn ⇒ không phải tệp phiên (từ chối, không đọc hết vào bộ nhớ).
TOKEN_MAX_BYTES = 64 * 1024
#: Tệp phiên Claude (`.credentials.json`, `.claude.json`) — `.claude.json` chứa thêm lịch sử dự án nên rộng hơn.
SESSION_FILE_MAX_BYTES = 4 * 1024 * 1024


def _open_dir_nofollow(path: Path) -> int:
    """fd thư mục `path` — từ chối nếu chính `path` là liên kết tượng trưng (ELOOP/ENOTDIR ⇒ OSError)."""
    return os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)


def read_regular_nofollow(path: Path, limit: int) -> bytes | None:
    """Nội dung tệp THƯỜNG `path` (≤ `limit` byte). Không có, là liên kết tượng trưng (kể cả thư mục chứa), không phải
    tệp thường hoặc quá lớn ⇒ None. Không bao giờ đi theo liên kết — một tiến trình cùng uid (agy có công cụ ghi tệp)
    không thể biến tệp phiên thành đường dẫn tới /run/secrets."""
    try:
        dfd = _open_dir_nofollow(path.parent)
    except OSError:
        return None
    try:
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=dfd)
    except OSError:
        return None
    finally:
        os.close(dfd)
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_size > limit:
            return None
        chunks: list[bytes] = []
        total = 0
        while chunk := os.read(fd, 65536):
            total += len(chunk)
            if total > limit:
                return None
            chunks.append(chunk)
        return b"".join(chunks)
    except OSError:
        return None
    finally:
        os.close(fd)


def is_regular_file(path: Path) -> bool:
    """`path` có và là tệp thường (lstat — liên kết tượng trưng KHÔNG tính)."""
    try:
        return stat.S_ISREG(os.lstat(path).st_mode)
    except OSError:
        return False


def atomic_write_nofollow(path: Path, raw: bytes) -> None:
    """Ghi `raw` vào `path` (0600) an toàn: thư mục chứa mở O_NOFOLLOW; tệp tạm tên ngẫu nhiên tạo O_CREAT|O_EXCL|
    O_NOFOLLOW trong đúng thư mục đó; fsync rồi rename (rename THAY liên kết tượng trưng ở đích, không đi theo nó).
    Lỗi ⇒ OSError (tệp tạm đã dọn)."""
    dfd = _open_dir_nofollow(path.parent)
    try:
        tmp = f".{path.name}.{os.urandom(6).hex()}.tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=dfd)
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(raw)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, path.name, src_dir_fd=dfd, dst_dir_fd=dfd)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(tmp, dir_fd=dfd)
            raise
    finally:
        os.close(dfd)


def valid_token_json(raw: bytes | None) -> bool:
    """Khuôn TỐI THIỂU của tệp token agy: ≤ TOKEN_MAX_BYTES và là một đối tượng JSON. Định dạng trường bên trong chưa
    xác minh trên tệp thật (ROADMAP Nợ #46) nên không đòi tên khoá cụ thể — chỉ chặn tệp rác / nhị phân / quá lớn."""
    if not raw or len(raw) > TOKEN_MAX_BYTES:
        return False
    try:
        return isinstance(orjson.loads(raw), dict)
    except orjson.JSONDecodeError:
        return False


def read_token_file(path: Path) -> bytes | None:
    """Tệp token agy đã kiểm (tệp thường, không liên kết, ≤ 64 KiB, đối tượng JSON) — None nếu không đạt."""
    raw = read_regular_nofollow(path, TOKEN_MAX_BYTES)
    if raw is not None and not valid_token_json(raw):
        log.warning("Tệp phiên Antigravity không đúng khuôn (không phải đối tượng JSON) — bỏ qua")
        return None
    return raw


def token_email(raw: bytes) -> str | None:
    """Email trong id_token của tệp token (không gọi mạng, không kiểm chữ ký — chỉ để so khớp định danh)."""
    try:
        data = orjson.loads(raw)
    except orjson.JSONDecodeError:
        return None
    tok = data.get("id_token") if isinstance(data, dict) else None
    if not isinstance(tok, str) or tok.count(".") < 2:
        return None
    part = tok.split(".")[1]
    try:
        claims = orjson.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
    except (ValueError, orjson.JSONDecodeError):
        return None
    email = claims.get("email") if isinstance(claims, dict) else None
    return email.lower() if isinstance(email, str) else None


def sync_token_back(cli_home: str, injected: bytes, new: bytes, *, locked: bool = False,
                    wait: float = TOKEN_WRITER_LOCK_WAIT_S) -> bool:
    """Chép tệp token agy đã làm mới trong HOME tạm (`new`) về kho phiên — CHỈ khi: khác bản đã đưa vào, đúng khuôn
    (`valid_token_json`), cùng email với bản đã đưa vào (nếu cả hai có id_token), và tệp trong kho VẪN là đúng bản
    đã đưa vào (so-và-đổi: Sếp đổi tài khoản / lượt khác đã ghi giữa chừng thì không đè). Giữ flock độc quyền
    (`locked=True` khi bên gọi đang giữ sẵn); không lấy được khoá trong `wait` giây ⇒ KHÔNG ghi (review lần 2 v0.1.63,
    P3-1 — trước đây đi tiếp ghi không khoá, có thể đè lên người ghi đang giữ khoá; lượt agy sau tự làm mới lại).
    Có thể chờ khoá (time.sleep) ⇒ từ mã async gọi qua luồng phụ. Trả True nếu đã ghi. Không bao giờ chép tệp nào
    khác từ HOME tạm."""
    if new == injected:
        return False
    if not valid_token_json(new):
        log.warning("agy để lại tệp token không đúng khuôn trong HOME tạm — không chép về kho phiên")
        return False
    old_email, new_email = token_email(injected), token_email(new)
    if old_email and new_email and old_email != new_email:
        log.warning("agy để lại tệp token của tài khoản khác trong HOME tạm — không chép về kho phiên")
        return False
    path = cli_home_dir(cli_home) / TOKEN_FILE
    guard = contextlib.nullcontext(True) if locked else token_flock(cli_home, exclusive=True, wait=wait)
    with guard as held:
        if not held:
            log.warning("Không lấy được khoá tệp phiên Antigravity — bỏ chép token làm mới về kho phiên")
            return False
        if read_regular_nofollow(path, TOKEN_MAX_BYTES) != injected:
            log.info("Tệp phiên Antigravity đã đổi trong lúc chạy lượt — không chép token làm mới đè lên")
            return False
        try:
            atomic_write_nofollow(path, new)
        except OSError as e:
            log.warning("Không chép được token Antigravity đã làm mới về kho phiên (%s)", type(e).__name__)
            return False
    return True


#: Thư mục tạm của một lượt agy: `gh-agy-home-<pid>-…` (HOME tạm, chứa token) và `gh-agy-<pid>-…` (cwd). Review lần 2
#: v0.1.63 (P3-5): mang pid tiến trình chủ để `sweep_stale_turn_dirs` biết thư mục nào của tiến trình đã chết.
TURN_DIR_RE = re.compile(r"^gh-agy-(?:home-)?(\d+)-")


def make_turn_home(token: bytes | None) -> Path:
    """HOME tạm cho MỘT lượt agy: thư mục mới 0700 (mkdtemp) có `.gemini/antigravity-cli/` 0700 và — nếu có — đúng
    một tệp token 0600. Không chép settings.json, mcp/, customizations/ hay lịch sử: cấu hình agy là mặc định của bản
    cài."""
    home = Path(tempfile.mkdtemp(prefix=f"gh-agy-home-{os.getpid()}-"))
    try:
        cfg = home / ".gemini" / "antigravity-cli"
        cfg.mkdir(mode=0o700, parents=True)
        os.chmod(home / ".gemini", 0o700)
        (home / "tmp").mkdir(mode=0o700)       # TMPDIR của lượt (cli_env) — agy ghi bộ đệm unleash… vào thư mục tạm
        if token is not None:
            fd = os.open(cfg / TOKEN_FILE, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
            with os.fdopen(fd, "wb") as fh:
                fh.write(token)
    except BaseException:
        shutil.rmtree(home, ignore_errors=True)
        raise
    return home


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True                                    # tiến trình của uid khác — còn sống
    return True


def sweep_stale_turn_dirs(root: str | Path | None = None) -> int:
    """Review lần 2 v0.1.63 (P3-5): /tmp của api/worker không phải tmpfs — tiến trình bị SIGKILL (dừng quá hạn, hết bộ
    nhớ) để lại HOME tạm có TOKEN và cwd của lượt; container khởi động lại vẫn giữ. Gọi MỘT lần lúc khởi động (trước
    lượt agy nào của tiến trình này): xoá thư mục lượt (`TURN_DIR_RE`) của tiến trình đã chết — hoặc mang chính pid này
    (container khởi động lại cấp lại số pid cũ). Liên kết tượng trưng bị xoá, không đi theo; tệp thường và tên không có
    pid (dạng cũ) được giữ. Trả số mục đã xoá."""
    base = Path(tempfile.gettempdir() if root is None else root)
    me, removed = os.getpid(), 0
    try:
        entries = list(os.scandir(base))
    except OSError:
        return 0
    for entry in entries:
        m = TURN_DIR_RE.match(entry.name)
        if m is None:
            continue
        pid = int(m.group(1))
        if pid != me and _pid_alive(pid):
            continue
        try:
            if entry.is_symlink():
                os.unlink(entry.path)
            elif entry.is_dir(follow_symlinks=False):
                shutil.rmtree(entry.path)
            else:
                continue
        except OSError:
            continue
        removed += 1
    if removed:
        log.info("Đã dọn %d thư mục tạm agy còn sót từ tiến trình cũ", removed)
    return removed


def turn_token_path(home: Path) -> Path:
    return home / ".gemini" / "antigravity-cli" / TOKEN_FILE


def cli_home_dir(cli_home: str) -> Path:
    """Thư mục cấu hình CLI (…/.gemini/antigravity-cli). HOME của tiến trình CLI = cha của `.gemini`."""
    return Path(os.path.expanduser(cli_home))


#: Biến proxy lối ra được chuyển cho agy (hộp đặt HTTPS_PROXY tới `agy-egress` — review v0.1.63, P2-2).
PROXY_ENV = ("HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY", "https_proxy", "http_proxy", "no_proxy")


def cli_env(cli_home: str, home: str | Path | None = None) -> dict[str, str]:
    """Môi trường SẠCH cho agy. `home` = HOME tạm của lượt (`make_turn_home`); None ⇒ HOME suy từ `cli_home` (đăng nhập
    trong api, canary). Biến proxy (PROXY_ENV) của tiến trình được chuyển tiếp nếu có."""
    if home is None:
        d = cli_home_dir(cli_home)
        home = d.parent.parent if d.parent.name == ".gemini" else d
        turn_tmp = None
    else:
        # Kiểm thật trong hộp (gói sửa review v0.1.63): agy ghi bộ đệm (unleash-repo-schema-…json) vào /tmp — chung
        # giữa các lượt ⇒ một lượt bị lừa để lại tệp cho lượt sau đọc. TMPDIR = thư mục riêng trong HOME tạm của lượt.
        turn_tmp = Path(home) / "tmp"
    env = {"PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"), "HOME": str(home),
           "NO_COLOR": "1", "TERM": "dumb", "AGY_CLI_DISABLE_AUTO_UPDATE": "1",
           "GEMINI_FORCE_FILE_STORAGE": "true", "LANG": "C.UTF-8"}
    if turn_tmp is not None and turn_tmp.is_dir():
        env["TMPDIR"] = str(turn_tmp)
    env.update({k: os.environ[k] for k in PROXY_ENV if os.environ.get(k)})
    return env


# v0.1.38 (F-22): tên model cho `--model=<tên>` — chỉ chữ, số và . _ : - (không khoảng trắng, tối đa 80 ký tự); ký tự
# đầu phải là chữ/số để tên kiểu cờ ("--dangerously-skip-permissions") cũng bị chặn.
AGY_MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,79}$")


AGY_TEXT_ONLY = ("[hệ thống CLI] Chỉ trả lời bằng CHỮ trong một lượt. KHÔNG dùng công cụ, KHÔNG đọc/ghi tệp, "
                 "KHÔNG chạy lệnh, KHÔNG duyệt web — mọi dữ liệu cần thiết đã có trong lời nhắc dưới đây.")
AGY_TEXT_ONLY_RETRY = ("[hệ thống CLI] Lượt trước em không trả lời gì. Hãy trả lời NGAY bằng chữ, "
                       "không dùng công cụ nào.")


class AgyClient:
    """Gọi Antigravity CLI (`agy`, chế độ in/headless chính thức) với tệp phiên của hồ sơ đang hoạt động.

    v0.1.38 (F-22) — cờ đã đo trên `agy --help` 1.2.9 (HOME tạm, chưa đăng nhập): KHÔNG có cờ tắt công cụ (đọc tệp,
    chạy lệnh, mở URL); có `--sandbox`, `--mode accept-edits|plan`, `--dangerously-skip-permissions` (CẤM dùng),
    `--disable-slash-commands` ("Disable slash command and skill expansion in print mode"), `--input-format
    stream-json`, `--print-timeout`, `--log-file`, `--agent`, `--add-dir`. Prompt qua stdin ĐƯỢC hỗ trợ: stdin là ống
    và không có `-p` → print mode (log agy: `Print mode: starting (promptLength=N, …)`) → prompt không lộ trên
    /proc/*/cmdline, không bị hiểu thành cờ, không vướng MAX_ARG_STRLEN. `-p` không gắn giá trị nuốt cờ kế tiếp làm
    prompt → slash command cố định viết `-p=/model`.

    Cô lập mỗi lượt: cwd = thư mục mới rỗng 0700 (`gh-agy-*`, xoá sau lượt — kể cả hết giờ/huỷ), env sạch
    (`cli_env`: không biến GH_*, không khoá), `--model=<tên>` qua AGY_MODEL_RE.
    Review v0.1.63 (P1-1): HOME cũng là thư mục TẠM 0700 mới mỗi lượt (`make_turn_home`, xoá sau lượt) chỉ chứa
    đúng tệp token — settings.json / mcp / customizations / lịch sử trong kho phiên (hay do một lượt trước ghi) không
    bao giờ được nạp. Token lấy từ kho phiên (`cli_home`, đọc O_NOFOLLOW, ≤ 64 KiB, đối tượng JSON) hoặc từ `session=`
    (hộp: token đến theo yêu cầu đã ký, hộp không có kho phiên). Sau lượt trong tiến trình, token agy làm mới được chép
    ngược qua `sync_token_back` (đã kiểm, flock, so-và-đổi); ở chế độ `session=` không bao giờ chép ngược.
    Changelog agy nói đọc tệp TRONG
    workspace được tự cho phép, ngoài workspace phải xin phép (bị từ chối trong print mode) — CHƯA kiểm được khi đã
    đăng nhập → không dựa vào đó.

    Chính sách (Sếp chốt 16/10/2026, gói G v0.1.62 + H2 v0.1.63): agy mở cho các vai nhưng MỖI VAI MỘT CHỖ CHẠY
    (`gh.providers.placement`, quyết định ở `gh.providers.agy_policy.decide`). Lớp CORE — Gen
    của Sếp, agent
    nội bộ không gắn kênh / nhóm / người — chạy `AgyClient` này TRONG TIẾN TRÌNH. Lớp ngoại giao / người ngoài —
    `core.reply`, agent ngoại giao, sàng lọc tin người lạ `core.refinery`, Bản tin Gen (review v0.1.63 P2-3), Gen của
    nhân viên, lượt thử agent — chạy qua
    HỘP RIÊNG (`gh.agybox.client.AgyBoxClient`; chính hộp dùng lại `AgyClient` này bên trong container không khoá);
    hộp chưa cài / tắt thì các vai đó dùng nguồn AI kế, không bao giờ rơi về `AgyClient` trong tiến trình.
    RỦI RO CÒN LẠI của lớp core trong tiến trình: agy có công cụ đọc tệp / chạy lệnh và chạy
    cùng uid, cùng container với
    api/worker (nơi có khoá hệ thống); lớp core chỉ nhận lời của Sếp và dữ liệu hệ thống,
    nhưng các lớp cô lập ở đây (cwd
    rỗng 0700, env sạch, prompt qua stdin, regex model, không `--dangerously-skip-permissions`) vẫn chỉ là phòng thủ
    thêm, KHÔNG phải rào chắn."""

    kind = "antigravity_cli"
    MAX_PROMPT = 120_000   # giữ giới hạn cũ (prompt qua stdin từ v0.1.38, không còn vướng MAX_ARG_STRLEN)

    def __init__(self, binary: str, cli_home: str, effort: str | None = None, timeout: float = 300.0, *,
                 session: bytes | None = None):
        # `effort` = mức mặc định khi model không có mức riêng; None = để CLI tự chọn (không gửi --effort).
        # `session` = nội dung tệp token cho chế độ hộp (không đọc/ghi kho phiên `cli_home`).
        self.binary, self.cli_home, self.effort, self.timeout = binary, cli_home, effort, timeout
        self.session = session
        self.last_models_raw: str | None = None
        #: Móc cho canary (`gh.providers.agy_canary`): gọi với HOME tạm của lượt ngay TRƯỚC khi xoá (đọc log agy).
        self.on_home: Any = None

    def _load_token(self) -> bytes | None:
        """Tệp token của lượt: `session` (hộp) hoặc tệp trong kho phiên — đã kiểm khuôn; None = chưa đăng nhập."""
        if self.session is not None:
            return self.session if valid_token_json(self.session) else None
        return read_token_file(cli_home_dir(self.cli_home) / TOKEN_FILE)

    @staticmethod
    def _workdir() -> str:
        """Thư mục làm việc riêng mỗi lượt: mkdtemp (0700) dưới thư mục tạm của hệ thống — không nằm trong HOME của
        agy hay GH_CLAUDE_HOME, rỗng → công cụ đọc tệp "trong workspace" của agy không thấy gì."""
        d = tempfile.mkdtemp(prefix=f"gh-agy-{os.getpid()}-")
        if os.stat(d).st_mode & 0o777 != 0o700:
            os.chmod(d, 0o700)
        return d


    async def _run(self, *args: str, stdin: bytes | None = None) -> tuple[int, bytes, bytes]:
        """Gói G (P3): token sắp hết hạn ⇒ lượt này có thể để agy LÀM MỚI token (refresh token xoay vòng, dùng một lần)
        — nhiều tiến trình agy (api + worker, nhiều lượt song song) dùng chung một tệp token mà cùng làm mới sẽ làm hỏng
        phiên. Những lượt như vậy giữ khoá flock ĐỘC QUYỀN trong suốt lượt (tuần tự); lượt token còn mới không khoá nên
        vẫn chạy song song. Chờ khoá tối đa min(timeout, 60s), hết hạn thì chạy tiếp (không treo).
        Chế độ hộp (`session=`) không có kho phiên chung ⇒ không khoá, không chép ngược."""
        token = self._load_token()
        if self.session is None and token is not None and token_needs_refresh(token):
            async with token_flock_async(self.cli_home, exclusive=True,
                                         wait=min(self.timeout, TOKEN_LOCK_WAIT_S)) as held:
                token = self._load_token()       # đọc lại SAU khi có khoá: lượt trước có thể vừa chép token mới về
                return await self._run_locked(*args, stdin=stdin, token=token, locked=held)
        return await self._run_locked(*args, stdin=stdin, token=token, locked=False)

    async def _run_locked(self, *args: str, stdin: bytes | None = None, token: bytes | None = None,
                          locked: bool = False) -> tuple[int, bytes, bytes]:
        work = self._workdir()
        try:
            home = make_turn_home(token)
        except BaseException:
            shutil.rmtree(work, ignore_errors=True)   # review lần 2 (P3-5): trước đây cwd rò khi tạo HOME tạm lỗi
            raise
        try:
            try:
                proc = await asyncio.create_subprocess_exec(
                    self.binary, *args,
                    stdin=asyncio.subprocess.PIPE if stdin is not None else asyncio.subprocess.DEVNULL,
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                    env=cli_env(self.cli_home, home=home), cwd=work, start_new_session=True)
            except FileNotFoundError as e:
                raise AuthFailed("Chưa cài Antigravity CLI trong worker") from e
            try:
                out, err = await asyncio.wait_for(proc.communicate(stdin), self.timeout)
            except (TimeoutError, asyncio.CancelledError):
                self._kill_group(proc.pid)
                await proc.wait()
                raise
            # Tiến trình con agy để lại (công cụ chạy lệnh chạy nền) không được sống quá lượt gọi.
            # Rủi ro chấp nhận (review): lúc này agy chính đã được bộ theo dõi tiến trình con của asyncio thu hồi
            # (không chặn được thứ tự). Còn tiến trình con ⇒ nhóm còn ⇒ Linux không cấp lại số pgid này cho tiến
            # trình khác. Không còn ⇒ killpg chỉ trúng nhóm lạ nếu số PID quay vòng hết pid_max VÀ tiến trình mới đó
            # tự lập nhóm đúng trong vài micro giây giữa hai dòng — thực tế không xảy ra; đổi lại không để sót tiến
            # trình nền của agy.
            self._kill_group(proc.pid)
            return proc.returncode or 0, out, err
        finally:
            try:
                # Kể cả hết giờ / huỷ: token agy đã làm mới (refresh token có thể xoay vòng) không được mất. Đọc một tệp
                # ≤ 64 KiB; đang giữ khoá ⇒ ghi ngay; chưa giữ ⇒ chờ khoá tối đa 2 giây TRONG LUỒNG PHỤ (review lần 2,
                # P3-1: `_try_flock` ngủ `time.sleep` — trước đây chặn vòng lặp sự kiện), không có khoá thì bỏ ghi.
                if self.session is None and token is not None:
                    new = read_regular_nofollow(turn_token_path(home), TOKEN_MAX_BYTES)
                    if new is not None and new != token:
                        if locked:
                            sync_token_back(self.cli_home, token, new, locked=True)
                        else:
                            await asyncio.to_thread(sync_token_back, self.cli_home, token, new, wait=2.0)
                    elif new is None and os.path.lexists(turn_token_path(home)):  # noqa: ASYNC240 — một lstat
                        log.warning("agy để lại tệp token không đọc được trong HOME tạm — không chép về kho phiên")
                if self.on_home is not None:
                    with contextlib.suppress(Exception):
                        self.on_home(home)
            finally:
                # Huỷ giữa lúc chờ luồng phụ vẫn xoá HOME tạm (luồng phụ chỉ dùng `new` đã đọc vào bộ nhớ).
                shutil.rmtree(work, ignore_errors=True)
                shutil.rmtree(home, ignore_errors=True)

    @staticmethod
    def _kill_group(pgid: int) -> None:
        """Review F-22: agy chạy trong session/nhóm tiến trình riêng (`start_new_session`) ⇒ giết cả nhóm — gồm tiến
        trình con agy sinh ra (công cụ chạy lệnh), không chỉ tiến trình agy chính."""
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(pgid, signal.SIGKILL)

    def model_args(self, model: str, effort: str | None = None) -> list[str]:
        """`--model=<model gốc> [--effort=<mức>]` (v0.1.32; dạng `=` từ v0.1.38). Tên biến thể cũ
        ("gemini-3.8-flash-high") được tách — không bao giờ gửi biến thể làm `--model` (agy 1.2.9 từ chối: "invalid
        model selection"). Tên gốc sai AGY_MODEL_RE → BadRequest, tiến trình KHÔNG được khởi chạy (F-22)."""
        from gh.providers.catalog import AGY_EFFORTS, split_variant

        base, var_effort = split_variant(self.kind, model)
        if not AGY_MODEL_RE.fullmatch(base or ""):
            raise BadRequest("Tên model không hợp lệ cho Antigravity CLI")
        eff = effort or var_effort or self.effort
        return [f"--model={base}", *([f"--effort={eff}"] if eff in AGY_EFFORTS else [])]

    async def generate(self, model: str, messages: list[Message], *, json_mode: bool, temperature: float,
                       effort: str | None = None) -> Completion:
        prompt = "\n\n".join(f"[{m.role}]\n{m.content}" if m.role != "user" else m.content for m in messages)
        if len(prompt.encode()) > self.MAX_PROMPT:
            raise BadRequest("Prompt quá dài cho CLI")
        if self._load_token() is None:
            if self.session is not None:
                raise AuthFailed("Phiên Antigravity gửi sang không đúng khuôn")
            token = cli_home_dir(self.cli_home) / TOKEN_FILE
            if os.path.lexists(token):  # noqa: ASYNC240 — một lstat
                raise AuthFailed("Tệp phiên Antigravity không hợp lệ (không phải JSON hoặc là liên kết) — "
                                 "đăng nhập lại")
            if is_regular_file(token.with_name(TOKEN_FILE + ".before-login")):
                # Console đang thêm tài khoản Google (tệp phiên gửi tạm, gh.providers.cli): tạm thời, không phải
                # "hết hạn" — chuyển nhà cung cấp kế tiếp mà không đánh dấu CLI hết hạn.
                raise ProviderError("CLI đang đăng nhập thêm tài khoản Google")
            raise AuthFailed("CLI chưa đăng nhập")
        margs = self.model_args(model, effort)    # BadRequest trước khi khởi chạy tiến trình
        started = time.monotonic()
        for attempt in range(2):
            try:
                # F-22: prompt CHỈ qua stdin (stdin là ống, không -p → print mode của agy 1.2.9).
                code, out, err = await self._run("--output-format", "json", "--disable-slash-commands", *margs,
                                                 stdin=prompt.encode())
            except TimeoutError as e:
                raise ProviderError(f"CLI quá {self.timeout:.0f}s") from e
            text = out.decode(errors="replace").strip()
            try:
                env = orjson.loads(text)
            except orjson.JSONDecodeError:
                env = None
            # v0.1.74 (bản thật 19/10): agy là CLI tác tử — đôi khi tự gọi công cụ, bị chặn (`denied_actions`) rồi
            # kết thúc với `response` RỖNG dù thoát 0 (~1/4 lượt có dữ liệu dài) ⇒ hỏi lại MỘT lần, dặn chỉ trả lời bằng
            # chữ; vẫn rỗng thì router xử như lỗi nguồn (thử nguồn kế).
            if (attempt == 0 and code == 0 and isinstance(env, dict) and not env.get("error")
                    and not str(env.get("response") or "").strip()):
                prompt = AGY_TEXT_ONLY_RETRY + "\n" + AGY_TEXT_ONLY + "\n\n" + prompt
                continue
            break
        if code != 0 or not isinstance(env, dict) or env.get("error"):
            stderr = err.decode(errors="replace")
            msg = (str(env.get("error")) if isinstance(env, dict) and env.get("error") else "") or \
                stderr[-300:] or text[-300:]
            low = msg.lower()
            # agy 1.2.9 (-p): lỗi model/agent → thoát 3 + dòng "AGY_ERROR: {...}" trên stderr; xét cả hai.
            rej = rejection(f"{msg}\n{stderr[-2000:]}")
            if rej is not None:
                raise rej
            if "auth" in low or "login" in low or "credential" in low:
                raise AuthFailed(msg)
            if "quota" in low or "resource_exhausted" in low or "429" in low:
                raise RateLimited(msg, 60)
            raise ProviderError(f"CLI thoát {code}: {msg}")
        usage = env.get("usage") or {}
        return Completion(str(env.get("response") or ""), usage.get("input_tokens") or usage.get("prompt_tokens"),
                          usage.get("output_tokens") or usage.get("completion_tokens"),
                          {"duration_seconds": env.get("duration_seconds"),
                           "ms": int((time.monotonic() - started) * 1000)})

    async def list_models(self) -> list[str]:
        return [m["id"] for m in await self.discover_models()]

    async def discover_models(self) -> list[dict[str, Any]]:
        """`agy models` (cần đã đăng nhập). Chưa đăng nhập / phiên hết hạn → AuthFailed, không coi là "OK"."""
        from gh.providers.catalog import parse_agy_models, strip_ansi

        try:
            code, out, err = await self._run("models")
        except TimeoutError as e:
            raise ProviderError(f"CLI quá {self.timeout:.0f}s") from e
        text = out.decode(errors="replace") + "\n" + err.decode(errors="replace")
        self.last_models_raw = redact(strip_ansi(text).strip(), 3000)   # cho "Chi tiết kỹ thuật" (v0.1.32)
        low = strip_ansi(text).lower()
        if "sign in" in low or "login" in low or "authenticat" in low or "credential" in low:
            raise AuthFailed(strip_ansi(text).strip()[-300:])
        if code != 0:
            raise ProviderError(f"CLI thoát {code}: {strip_ansi(text).strip()[-300:]}")
        return parse_agy_models(out.decode(errors="replace"))

    async def diagnose(self, model: str | None, effort: str | None, prompt: str) -> list[dict[str, Any]]:
        """Phiên bản, `agy models`, `agy -p=/model`, `agy -p=/effort`, một lượt gọi rất ngắn đúng cờ đang dùng (prompt
        qua stdin như `generate`) — đầu ra thô đã che (v0.1.32).

        `-p=/model` / `-p=/effort` (chuỗi cố định; dạng `=` để `-p` không nuốt cờ kế tiếp): changelog trong tệp chạy
        agy 1.2.9 (1.1.11) — ở chế độ in, `/model`, `/effort`…
        "emit one tab-separated record per line … without starting an agent turn, spending quota" → lấy được danh sách
        model / mức suy nghĩ THẬT của tài khoản đã đăng nhập mà không tốn lượt."""
        name = Path(self.binary).name
        steps = [await diag_step("Phiên bản", [name, "--version"], lambda: self._run("--version"), 20),
                 await diag_step("Danh sách model", [name, "models"], lambda: self._run("models"), 60)]
        try:
            margs, base_args = ((self.model_args(model, effort), self.model_args(model)) if model else ([], []))
            skip = None
        except BadRequest as e:      # tên model đã lưu sai AGY_MODEL_RE (bản cài cũ) — không chạy với tên đó
            margs, base_args, skip = [], [], f"bỏ qua — {e}"
        argv = ["--output-format", "json", "--disable-slash-commands", *margs]
        model_argv = ["-p=/model"]
        effort_argv = ["-p=/effort", *base_args]
        shown = [name, *argv, "(stdin:", prompt + ")"]
        if self._load_token() is None:
            # CLI chưa đăng nhập mà chạy print mode sẽ in link đăng nhập rồi chờ 60 giây — không chạy.
            skip = "bỏ qua — CLI chưa đăng nhập"
        if skip:
            for label, av in (("Model của tài khoản (/model)", model_argv),
                              ("Mức suy nghĩ (/effort)", effort_argv)):
                steps.append(_skipped(label, [name, *av], skip))
            steps.append(_skipped("Gọi thử 1 lượt", shown, skip))
            return steps
        steps.append(await diag_step("Model của tài khoản (/model)", [name, *model_argv],
                                     lambda: self._run(*model_argv), 60))
        steps.append(await diag_step("Mức suy nghĩ (/effort)", [name, *effort_argv],
                                     lambda: self._run(*effort_argv), 60))
        steps.append(await diag_step("Gọi thử 1 lượt", shown, lambda: self._run(*argv, stdin=prompt.encode()), 90))
        return steps

    async def embed(self, model: str, texts: list[str]) -> list[list[float]]:
        raise BadRequest("CLI không hỗ trợ embedding")


# ─── Claude Code CLI (v0.1.31) ───────────────────────────────────────────────

CLAUDE_CRED_FILE = ".credentials.json"     # trong CLAUDE_CONFIG_DIR (chuỗi trong tệp chạy claude 2.1.285)
CLAUDE_STATE_FILE = ".claude.json"         # thông tin tài khoản (oauthAccount.emailAddress …), cùng thư mục


def claude_env(claude_home: str) -> dict[str, str]:
    """Môi trường SẠCH cho `claude`: chỉ phiên đăng nhập gói Claude trong CLAUDE_CONFIG_DIR — không truyền
    ANTHROPIC_API_KEY hay biến nào khác của api (token không bao giờ nằm trên dòng lệnh / log)."""
    home = cli_home_dir(claude_home)
    return {"PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"), "HOME": str(home.parent),
            "CLAUDE_CONFIG_DIR": str(home), "NO_COLOR": "1", "TERM": "dumb", "LANG": "C.UTF-8",
            "DISABLE_AUTOUPDATER": "1", "DISABLE_TELEMETRY": "1", "DISABLE_ERROR_REPORTING": "1",
            "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1"}


class ClaudeCodeClient:
    """Gọi `claude -p` (Claude Code, chế độ không tương tác) bằng phiên đăng nhập gói Claude của Owner.

    Cờ đã kiểm trên `claude --help` 2.1.286: `-p/--print`, `--model <alias|tên>`, `--output-format json`, `--tools ""`
    (tắt MỌI công cụ — model chỉ trả lời chữ, không chạy lệnh trong container), `--system-prompt`,
    `--no-session-persistence`, `--strict-mcp-config`, `--disable-slash-commands`. Prompt đưa qua stdin (không giới
    hạn độ dài đối số dòng lệnh). JSON trả về: `result`, `is_error`, `usage.input_tokens/output_tokens`.

    Review v0.1.31 (đo thật trên 2.1.285): `--tools ""` KHÔNG chặn hook trong settings.json hay CLAUDE.md của
    CLAUDE_CONFIG_DIR/thư mục cha — thêm `--safe-mode` (tắt hook, CLAUDE.md, skill, plugin, MCP; đăng nhập vẫn chạy).
    Lời nhắn hệ thống đưa qua `--system-prompt-file` (tệp tạm 0600): không lộ trên /proc/*/cmdline và không vỡ khi dài
    quá 128 KiB (MAX_ARG_STRLEN). `--model=<tên>` để tên model không bao giờ bị hiểu thành một cờ."""

    kind = "claude_code_cli"
    MAX_PROMPT = 400_000

    def __init__(self, binary: str, claude_home: str, timeout: float = 300.0):
        self.binary, self.claude_home, self.timeout = binary, claude_home, timeout

    def _workdir(self) -> Path:
        d = cli_home_dir(self.claude_home).parent / "work"
        d.mkdir(parents=True, exist_ok=True)
        return d

    async def _run(self, *args: str, stdin: bytes | None = None) -> tuple[int, bytes, bytes]:
        try:
            proc = await asyncio.create_subprocess_exec(
                self.binary, *args, stdin=asyncio.subprocess.PIPE if stdin is not None else asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=claude_env(self.claude_home),
                cwd=str(self._workdir()))
        except FileNotFoundError as e:
            raise AuthFailed("Chưa cài Claude Code CLI trong máy chủ") from e
        try:
            out, err = await asyncio.wait_for(proc.communicate(stdin), self.timeout)
        except (TimeoutError, asyncio.CancelledError):
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
            await proc.wait()
            raise
        return proc.returncode or 0, out, err

    async def auth_status(self) -> dict[str, Any]:
        """`claude auth status --json` → {loggedIn, authMethod, email, subscriptionType…}; thoát 1 khi chưa đăng
        nhập."""
        try:
            code, out, _err = await self._run("auth", "status", "--json")
        except TimeoutError as e:
            raise ProviderError(f"CLI quá {self.timeout:.0f}s") from e
        try:
            data = orjson.loads(out)
        except orjson.JSONDecodeError:
            data = {}
        return data if isinstance(data, dict) else {"loggedIn": code == 0}

    @staticmethod
    def model_args(model: str, effort: str | None = None) -> list[str]:
        """`--model=<tên>` + `--effort=<mức>` (v0.1.32, `claude --help` 2.1.285: low, medium, high, xhigh, max)."""
        from gh.providers.catalog import CLAUDE_EFFORTS

        return [f"--model={model}", *([f"--effort={effort}"] if effort in CLAUDE_EFFORTS else [])]

    async def generate(self, model: str, messages: list[Message], *, json_mode: bool, temperature: float,
                       effort: str | None = None) -> Completion:
        if not (cli_home_dir(self.claude_home) / CLAUDE_CRED_FILE).exists():
            if (cli_home_dir(self.claude_home) / (CLAUDE_CRED_FILE + ".before-login")).exists():
                raise ProviderError("Claude Code CLI đang đăng nhập thêm tài khoản")
            raise AuthFailed("Claude Code CLI chưa đăng nhập")
        system = "\n\n".join(m.content for m in messages if m.role == "system")
        prompt = "\n\n".join(f"[{m.role}]\n{m.content}" if m.role != "user" else m.content
                               for m in messages if m.role != "system")
        if len(prompt.encode()) > self.MAX_PROMPT:
            raise BadRequest("Prompt quá dài cho CLI")
        if len(system.encode()) > self.MAX_PROMPT:
            raise BadRequest("Lời nhắn hệ thống quá dài cho CLI")
        args = ["-p", *self.model_args(model, effort), "--output-format", "json", "--no-session-persistence",
                "--safe-mode", "--strict-mcp-config", "--disable-slash-commands"]
        sys_file: str | None = None
        if system:
            fd, sys_file = tempfile.mkstemp(prefix="gh-claude-sys-", suffix=".txt")   # 0600, xoá ngay sau lượt gọi
            with os.fdopen(fd, "wb") as fh:
                fh.write(system.encode())
            args += ["--system-prompt-file", sys_file]
        args += ["--tools", ""]   # cuối cùng: `--tools <tools...>` nhận nhiều giá trị
        started = time.monotonic()
        try:
            code, out, err = await self._run(*args, stdin=prompt.encode())
        except TimeoutError as e:
            raise ProviderError(f"CLI quá {self.timeout:.0f}s") from e
        finally:
            if sys_file:
                with contextlib.suppress(OSError):
                    os.unlink(sys_file)
        text = out.decode(errors="replace").strip()
        try:
            env = orjson.loads(text.splitlines()[-1] if text else "")
        except (orjson.JSONDecodeError, IndexError):
            env = None
        if code != 0 or not isinstance(env, dict) or env.get("is_error"):
            msg = (str(env.get("result") or env.get("error") or env.get("subtype") or "")
                   if isinstance(env, dict) else "") or err.decode(errors="replace")[-300:] or text[-300:]
            low = msg.lower()
            status = env.get("api_error_status") if isinstance(env, dict) else None
            rej = rejection(msg)
            if rej is not None or status == 404:
                raise rej or ModelRejected(msg[:300], raw=msg)
            if status in (401, 403) or any(k in low for k in ("log in", "login", "authenticat", "oauth",
                                                                 "credential", "unauthorized")):
                raise AuthFailed(msg[:300])
            if status == 429 or any(k in low for k in ("rate limit", "usage limit", "limit reached", "429")):
                raise RateLimited(msg[:300], 300)
            raise ProviderError(f"CLI thoát {code}: {msg[:300]}")
        usage = env.get("usage") or {}
        return Completion(str(env.get("result") or ""), usage.get("input_tokens"), usage.get("output_tokens"),
                          {"duration_ms": env.get("duration_ms"), "ms": int((time.monotonic() - started) * 1000)})

    async def diagnose(self, model: str | None, effort: str | None, prompt: str) -> list[dict[str, Any]]:
        """Phiên bản, trạng thái đăng nhập (Claude Code không có lệnh liệt kê model), một lượt gọi rất ngắn."""
        name = Path(self.binary).name
        steps = [await diag_step("Phiên bản", [name, "--version"], lambda: self._run("--version"), 20),
                 await diag_step("Đăng nhập (Claude Code không có lệnh liệt kê model)",
                                 [name, "auth", "status", "--json"], lambda: self._run("auth", "status", "--json"), 30)]
        argv = ["-p", *self.model_args(model or "haiku", effort), "--output-format", "json",
                "--no-session-persistence", "--safe-mode", "--strict-mcp-config", "--disable-slash-commands",
                "--tools", ""]
        if not (cli_home_dir(self.claude_home) / CLAUDE_CRED_FILE).exists():
            steps.append(_skipped("Gọi thử 1 lượt", [name, *argv], "bỏ qua — CLI chưa đăng nhập"))
        else:
            steps.append(await diag_step("Gọi thử 1 lượt", [name, *argv, "(stdin:", prompt + ")"],
                                         lambda: self._run(*argv, stdin=prompt.encode()), 90))
        return steps

    async def discover_models(self) -> list[dict[str, Any]]:
        """Claude Code không có lệnh liệt kê model → chỉ kiểm phiên đăng nhập; danh sách là bí danh
        (gh.providers.catalog)."""
        st = await self.auth_status()
        if not st.get("loggedIn"):
            raise AuthFailed("Claude Code CLI chưa đăng nhập")
        return []

    async def list_models(self) -> list[str]:
        from gh.providers.catalog import CLAUDE_CODE_MODELS

        await self.discover_models()
        return [m["id"] for m in CLAUDE_CODE_MODELS]

    async def embed(self, model: str, texts: list[str]) -> list[list[float]]:
        raise BadRequest("CLI không hỗ trợ embedding")


def parse_json_block(text: str) -> Any:
    """Lấy JSON từ câu trả lời của model (chấp nhận khối ```json … ``` hoặc chữ thừa hai đầu)."""
    s = text.strip()
    if s.startswith("```"):
        s = s.split("\n", 1)[1] if "\n" in s else s
        s = s.rsplit("```", 1)[0]
    try:
        return orjson.loads(s)
    except orjson.JSONDecodeError:
        pass
    for open_, close in (("{", "}"), ("[", "]")):
        i, j = s.find(open_), s.rfind(close)
        if i != -1 and j > i:
            try:
                return orjson.loads(s[i:j + 1])
            except orjson.JSONDecodeError:
                continue
    raise ValueError("Model không trả JSON hợp lệ")
