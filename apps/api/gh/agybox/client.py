"""Client gọi `agy-box` từ api/worker (v0.1.63 H1) — cùng giao diện `AgyClient` để bộ định tuyến (H2) hoán đổi.

    AgyBoxClient(url, key_file, timeout)    generate · discover_models · list_models · diagnose · kind="antigravity_cli"
    AgyBoxClient.from_env(timeout)            theo GH_AGYBOX_URL + GH_AGYBOX_KEY_FILE; None nếu hộp chưa được cài
    await box_status() -> {"state": "ok"|"down"|"not_installed", "checked_at", "agy_version", "cli_logged_in"}

Lỗi (đều là ProviderError để router xử lý như một nguồn lỗi): 401 → BoxUnauthorized (khoá/đồng hồ lệch — KHÔNG phải
AuthFailed nên không đánh dấu phiên agy hết hạn); không nối được → BoxUnavailable; agy chưa đăng nhập → AuthFailed;
hết hạn mức / hộp bận → RateLimited; model sai → ModelRejected; yêu cầu sai / prompt quá dài → BadRequest;
quá giờ → ProviderError. Thân trả lời là văn bản KHÔNG tin cậy (như đầu ra agy trực tiếp) — chỉ chữ ký được kiểm."""

import os
import time
import uuid
from datetime import UTC, datetime
from typing import Any

import httpx
import orjson

from gh.agybox import protocol as P
from gh.providers.clients import (
    TOKEN_FILE,
    AuthFailed,
    BadRequest,
    Completion,
    Message,
    ModelRejected,
    ProviderError,
    RateLimited,
    cli_home_dir,
    is_regular_file,
    read_token_file,
    redact,
)

ENV_URL = "GH_AGYBOX_URL"
ENV_KEY_FILE = "GH_AGYBOX_KEY_FILE"
ENV_CLI_HOME = "GH_CLI_HOME"
DEFAULT_CLI_HOME = "~/.gemini/antigravity-cli"     # = gh.config.Settings.cli_home
STATUS_TTL_S = 10.0
STATUS_TIMEOUT_S = 2.0
CONNECT_TIMEOUT_S = 5.0


class BoxUnavailable(ProviderError):
    """Không nối được hộp (tắt, chưa cài, mạng agy-bus lỗi)."""


class BoxUnauthorized(ProviderError):
    """Hộp từ chối chữ ký: sai khoá `gh_agy_key` hoặc đồng hồ lệch quá 60 giây. Không phải lỗi đăng nhập agy."""


def _rid() -> str:
    return uuid.uuid4().hex


def raise_for_error(status: int, payload: Any) -> None:
    """Dựng lại ngoại lệ từ {"error": {"code", "message", ...}} của hộp. Thông điệp đã che bí mật, cắt 300 ký tự."""
    err = payload.get("error") if isinstance(payload, dict) else None
    if not isinstance(err, dict) or not isinstance(err.get("code"), str):
        raise BoxUnavailable(f"Hộp riêng Antigravity trả mã {status} không đúng khuôn")
    code = err["code"]
    msg = redact(str(err.get("message") or code), 300)
    retry = err.get("retry_after")
    retry = float(retry) if isinstance(retry, (int, float)) and not isinstance(retry, bool) else None
    if code == P.E_UNAUTHORIZED:
        raise BoxUnauthorized("Hộp riêng Antigravity từ chối chữ ký (sai khoá hoặc đồng hồ lệch quá 60 giây)")
    if code == P.E_BUSY:
        raise RateLimited(msg, retry if retry is not None else P.BUSY_RETRY_AFTER_S)
    if code == P.E_RATE_LIMITED:
        raise RateLimited(msg, retry if retry is not None else 60)
    if code == P.E_CLI_AUTH:
        raise AuthFailed(msg)
    if code == P.E_MODEL_REJECTED:
        avail = err.get("available")
        e = ModelRejected(msg, what="effort" if err.get("what") == "effort" else "model",
                          available=[str(a) for a in avail][:50] if isinstance(avail, list) else None,
                          raw=redact(str(err.get("raw") or msg), 2000))
        raise e
    if code in (P.E_BAD_REQUEST, P.E_TOO_LARGE):
        raise BadRequest(msg)
    raise ProviderError(msg)     # timeout, provider_error, internal, not_found…


class AgyBoxClient:
    kind = "antigravity_cli"
    MAX_PROMPT = P.MAX_PROMPT_BYTES

    def __init__(self, url: str, key_file: str, timeout: float = P.DEFAULT_TIMEOUT_S, effort: str | None = None, *,
                 cli_home: str | None = None, transport: httpx.AsyncBaseTransport | None = None,
                 clock: Any = time.time):
        self.url, self.key_file, self.timeout, self.effort = url.rstrip("/"), key_file, timeout, effort
        #: Kho phiên agy của api/worker (volume agy_state) — nguồn tệp token gửi kèm mỗi yêu cầu (protocol 2).
        self.cli_home = cli_home or os.environ.get(ENV_CLI_HOME) or DEFAULT_CLI_HOME
        self.transport, self.clock = transport, clock
        self.last_models_raw: str | None = None
        self._key: bytes | None = None

    @classmethod
    def from_env(cls, timeout: float = P.DEFAULT_TIMEOUT_S, effort: str | None = None,
                 cli_home: str | None = None) -> "AgyBoxClient | None":
        url, key_file = os.environ.get(ENV_URL, ""), os.environ.get(ENV_KEY_FILE, "")
        if not (url and key_file and os.path.isfile(key_file)):
            return None
        return cls(url, key_file, timeout, effort, cli_home=cli_home)

    def _session(self) -> str:
        """Tệp token agy của hồ sơ đang hoạt động, đã kiểm (tệp thường, không liên kết, ≤ 64 KiB, đối tượng JSON) — gửi
        kèm yêu cầu vì hộp không có kho phiên. Chưa đăng nhập ⇒ AuthFailed ngay, không gọi hộp."""
        path = cli_home_dir(self.cli_home) / TOKEN_FILE
        raw = read_token_file(path)
        if raw is None:
            if os.path.lexists(path):
                raise AuthFailed("Tệp phiên Antigravity không hợp lệ (không phải JSON hoặc là liên kết) — "
                                 "đăng nhập lại")
            if is_regular_file(path.with_name(TOKEN_FILE + ".before-login")):
                raise ProviderError("CLI đang đăng nhập thêm tài khoản Google")
            raise AuthFailed("CLI chưa đăng nhập")
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError as e:
            raise AuthFailed("Tệp phiên Antigravity không phải UTF-8 — đăng nhập lại") from e

    def _load_key(self) -> bytes:
        if self._key is None:
            try:
                self._key = P.load_key(self.key_file)
            except (OSError, ValueError) as e:
                raise BoxUnavailable("Hộp riêng Antigravity chưa được cài (thiếu khoá gh_agy_key)") from e
        return self._key

    async def _call(self, method: str, path: str, payload: dict[str, Any] | None,
                    timeout: float) -> dict[str, Any]:  # noqa: ASYNC109 — hạn HTTP của một lượt gọi, không phải asyncio.timeout
        key = self._load_key()
        body = orjson.dumps(payload) if payload is not None else b""
        headers = {"Content-Type": "application/json", **P.sign_request(key, method, path, body, self.clock())}
        req_sig = headers[P.SIG_HEADER]
        try:
            async with httpx.AsyncClient(transport=self.transport, timeout=httpx.Timeout(
                    timeout + P.HTTP_MARGIN_S, connect=CONNECT_TIMEOUT_S)) as c:
                r = await c.request(method, self.url + path, content=body, headers=headers)
        except httpx.TimeoutException as e:
            if isinstance(e, httpx.ConnectTimeout):
                raise BoxUnavailable("Hộp riêng Antigravity không phản hồi kết nối") from e
            raise ProviderError(f"Hộp riêng Antigravity quá {timeout:.0f}s không trả lời") from e
        except httpx.HTTPError as e:
            raise BoxUnavailable("Hộp riêng Antigravity chưa chạy") from e
        sig = r.headers.get(P.SIG_HEADER)
        signed_ok = sig is not None and P.signature_equal(P.response_signature(key, req_sig, r.status_code, r.content),
                                                          sig)
        if r.status_code == 200:
            if not signed_ok:
                raise ProviderError("Trả lời của hộp riêng Antigravity sai chữ ký — bỏ qua")
            try:
                out = orjson.loads(r.content)
            except orjson.JSONDecodeError as e:
                raise ProviderError("Trả lời của hộp riêng Antigravity không phải JSON") from e
            if not isinstance(out, dict):
                raise ProviderError("Trả lời của hộp riêng Antigravity không đúng khuôn")
            return out
        if sig is not None and not signed_ok:
            raise ProviderError("Trả lời lỗi của hộp riêng Antigravity sai chữ ký — bỏ qua")
        try:
            payload_err = orjson.loads(r.content)
        except orjson.JSONDecodeError:
            payload_err = None
        raise_for_error(r.status_code, payload_err)
        raise AssertionError("unreachable")  # pragma: no cover

    async def generate(self, model: str, messages: list[Message], *, json_mode: bool, temperature: float,
                       effort: str | None = None) -> Completion:
        # Ghép đúng như AgyClient.generate để cùng một prompt dù chạy trong tiến trình hay trong hộp.
        prompt = "\n\n".join(f"[{m.role}]\n{m.content}" if m.role != "user" else m.content for m in messages)
        if len(prompt.encode()) > self.MAX_PROMPT:
            raise BadRequest("Prompt quá dài cho CLI")
        out = await self._call("POST", P.PATH_GENERATE, {
            "request_id": _rid(), "model": model, "effort": effort or self.effort, "prompt": prompt,
            "json_mode": json_mode, "timeout_s": self.timeout, "session": self._session()}, self.timeout)
        text = out.get("text")
        if not isinstance(text, str):
            raise ProviderError("Trả lời của hộp riêng Antigravity thiếu văn bản")

        def num(v: Any) -> int | None:
            return v if isinstance(v, int) and not isinstance(v, bool) else None

        raw = out.get("raw")
        return Completion(text, num(out.get("tokens_in")), num(out.get("tokens_out")),
                          raw if isinstance(raw, dict) else {})

    async def discover_models(self) -> list[dict[str, Any]]:
        t = min(self.timeout, P.DEFAULT_MODELS_TIMEOUT_S)
        out = await self._call("POST", P.PATH_MODELS, {"request_id": _rid(), "timeout_s": t,
                                                       "session": self._session()}, t)
        models = out.get("models")
        if not isinstance(models, list) or not all(
                isinstance(m, dict) and isinstance(m.get("id"), str) for m in models):
            raise ProviderError("Danh sách model của hộp riêng Antigravity không đúng khuôn")
        raw = out.get("raw_text")
        self.last_models_raw = redact(raw, 3000) if isinstance(raw, str) else None
        return models

    async def list_models(self) -> list[str]:
        return [m["id"] for m in await self.discover_models()]

    async def diagnose(self, model: str | None, effort: str | None, prompt: str) -> list[dict[str, Any]]:
        """Chẩn đoán qua hộp: trạng thái hộp + `agy models` (không gọi thử lượt — tránh tốn hạn mức)."""
        started = time.monotonic()
        st = await box_status(self.url, self.key_file, transport=self.transport, clock=self.clock, use_cache=False)
        steps: list[dict[str, Any]] = [{
            "label": "Hộp riêng Antigravity", "command": "GET /healthz", "exit_code": 0 if st["state"] == "ok" else 1,
            "stdout": orjson.dumps(st).decode(), "stderr": "", "ms": int((time.monotonic() - started) * 1000),
            "note": None if st["state"] == "ok" else "hộp chưa chạy hoặc chưa cài"}]
        if st["state"] == "ok":
            started = time.monotonic()
            step: dict[str, Any] = {"label": "Danh sách model", "command": "POST /v1/models", "exit_code": 0,
                                    "stdout": "", "stderr": "", "ms": 0, "note": None}
            try:
                step["stdout"] = redact("\n".join(await self.list_models()))
            except Exception as e:  # noqa: BLE001 — chẩn đoán trả được mọi lỗi
                step.update(exit_code=1, note=redact(f"{type(e).__name__}: {e}")[:500])
            step["ms"] = int((time.monotonic() - started) * 1000)
            steps.append(step)
        return steps

    async def embed(self, model: str, texts: list[str]) -> list[list[float]]:
        raise BadRequest("CLI không hỗ trợ embedding")


# ─── trạng thái hộp (khối sức khoẻ `agy_box`, router/placement của H2) ──────────────────────────────────────────

_status_cache: dict[tuple[str, str], tuple[float, dict[str, Any]]] = {}


def reset_status_cache() -> None:
    _status_cache.clear()


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


async def box_status(url: str | None = None, key_file: str | None = None, *,
                     transport: httpx.AsyncBaseTransport | None = None, clock: Any = time.time,
                     use_cache: bool = True) -> dict[str, Any]:
    """`not_installed` = chưa cấu hình URL/khoá (bản cài cũ chưa `genh update`, dev không có hộp); `down` = đã cấu hình
    mà không trả lời `/healthz`; `ok` = trả lời. Kết quả cache `STATUS_TTL_S` giây (router gọi mỗi lượt không dội
    hộp)."""
    url = os.environ.get(ENV_URL, "") if url is None else url
    key_file = os.environ.get(ENV_KEY_FILE, "") if key_file is None else key_file
    now = clock()
    base: dict[str, Any] = {"state": "not_installed", "checked_at": _iso(now), "agy_version": None,
                            "cli_logged_in": None}
    if not url or not key_file or not os.path.isfile(key_file):  # noqa: ASYNC240 — stat một tệp nhỏ, không đáng executor
        return base
    ck = (url, key_file)
    hit = _status_cache.get(ck)
    if use_cache and hit is not None and 0 <= now - hit[0] < STATUS_TTL_S:
        return dict(hit[1])
    res = {**base, "state": "down"}
    try:
        async with httpx.AsyncClient(transport=transport, timeout=httpx.Timeout(STATUS_TIMEOUT_S)) as c:
            r = await c.get(url.rstrip("/") + P.PATH_HEALTHZ)
        j = r.json() if r.status_code == 200 else None
        if isinstance(j, dict) and j.get("ok") is True and j.get("service") == P.SERVICE:
            v = j.get("agy_version")
            res.update(state="ok", agy_version=v if isinstance(v, str) else None,
                       cli_logged_in=j.get("cli_logged_in") if isinstance(j.get("cli_logged_in"), bool) else None)
    except (httpx.HTTPError, ValueError):
        pass
    _status_cache[ck] = (now, res)
    return dict(res)
