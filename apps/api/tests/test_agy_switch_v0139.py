"""v0.1.39 (F-76) — "Đổi tài khoản Google" phải đổi THẬT: sau khi đổi, lượt gọi thử chạy bằng đúng tài khoản vừa chọn.

CLI giả nhiều tài khoản (tests/fixtures/fake_agy_multi.py, fixture `clis` của test_cli_models_v0131): `agy -p` trả
`whoami:<email>` theo tệp phiên hiện tại; `/providers/{id}/test` trả thêm `account` = email của tệp phiên vừa dùng.
Kịch bản Boss: đăng nhập an rồi binh → gọi thử (binh) → đổi sang an (gọi thử = an) → đổi lại binh (gọi thử = binh).
"""

import base64
from typing import Any

import orjson
from sqlalchemy import text

from gh import crypto
from gh.db import admin_sessionmaker
from gh.providers import cli as climod
from tests.conftest import Api, verify_pin
from tests.test_cli_models_v0131 import _login, _provider, clis  # noqa: F401 — fixture dùng lại

AGY = "antigravity_cli"


def _agy_token(name: str) -> bytes:
    claims = base64.urlsafe_b64encode(orjson.dumps({"email": f"{name}@example.vn"})).decode().rstrip("=")
    return orjson.dumps({"access_token": f"tok-{name}", "id_token": f"h.{claims}.s", "expiry": 4102444800})


async def _run(api: Api, key: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
    r = await api.send("POST", f"/boss-checks/{key}/run", body or {})
    assert r.status_code == 200, r.text
    return r.json()  # type: ignore[no-any-return]


async def _profile_id(api: Api, email: str) -> str:
    profs = (await api.get(f"/cli/profiles?kind={AGY}")).json()
    return next(p["id"] for p in profs if p["email"] == email)


async def _db_text(sql: str) -> str:
    async with admin_sessionmaker()() as db:
        return orjson.dumps((await db.execute(text(sql))).all(), default=str).decode()


async def test_switch_back_and_forth_calls_with_selected_account(owner_api: Api, clis: Any) -> None:  # noqa: F811
    api = owner_api
    out = await _run(api, "agy_call")
    assert out["status"] == "fail" and out["error_code"] == "AGY_NOT_LOGGED_IN"
    await _login(api, AGY, "4/an")
    await _login(api, AGY, "4/binh")
    ov = (await api.get("/boss-checks")).json()
    login = ov["results"]["agy_login"]
    assert login["status"] == "pass" and login["runs"] == 2
    assert login["detail"]["code_shape"] == {"length": 6, "classes": ["digit", "lower", "symbol"], "symbols": "/",
                                             "has_space": False}
    assert login["detail"]["credentials_file"] is True and login["detail"]["account_masked"] == "b***@example.vn"

    out = await _run(api, "agy_call")
    assert out["status"] == "pass" and out["account"] == "binh@example.vn", out
    assert out["detail"]["account_masked"] == "b***@example.vn" and out["detail"]["models_count"] > 0
    assert out["detail"]["probe_model"] and out["detail"]["models_source"] == "cli"
    # /providers/{id}/test cũng trả account + error_code (hợp đồng cho web).
    p = await _provider(api, AGY)
    t = (await api.send("POST", f"/providers/{p['id']}/test")).json()
    assert t["ok"] is True and t["account"] == "binh@example.vn" and t["error_code"] is None
    assert (await _provider(api, AGY))["last_test"]["account"] == "binh@example.vn"

    await verify_pin(api)
    an, binh = await _profile_id(api, "an@example.vn"), await _profile_id(api, "binh@example.vn")
    out = await _run(api, "agy_switch", {"profile_id": an})
    assert out["status"] == "pass" and out["account"] == "an@example.vn", out
    assert out["detail"] == {"expected_masked": "a***@example.vn", "account_masked": "a***@example.vn",
                             "account_match": True, "latency_ms": out["detail"]["latency_ms"],
                             "target_profile": an, "from_profile": binh}
    assert (await _run(api, "agy_call"))["account"] == "an@example.vn"
    assert (await _provider(api, AGY))["account_label"] == "an@example.vn"

    out = await _run(api, "agy_switch", {"profile_id": binh})
    assert out["status"] == "pass" and out["account"] == "binh@example.vn", out
    out = await _run(api, "agy_call")
    assert out["status"] == "pass" and out["account"] == "binh@example.vn"
    assert (await _provider(api, AGY))["account_label"] == "binh@example.vn"

    ov = (await api.get("/boss-checks")).json()
    assert next(r for r in ov["rows"] if r["key"] == "agy")["done"] is True and "switch_passes" not in ov
    # Hồ sơ nào giữ đúng phiên của mình (không bị ghi đè chéo khi đổi qua lại).
    async with admin_sessionmaker()() as db:
        rows = (await db.execute(text("SELECT email, token_enc, is_active FROM agent.cli_profiles"))).all()
    for r in rows:
        assert climod.file_email(crypto.decrypt(bytes(r.token_enc), climod.CLI_AAD)) == r.email
    assert {r.email: r.is_active for r in rows} == {"an@example.vn": False, "binh@example.vn": True}
    # CSDL chỉ có email đã che.
    blob = await _db_text("SELECT message, detail::text FROM ops.boss_checks")
    assert "an@example.vn" not in blob.replace("a***@example.vn", "") and "binh@example.vn" not in blob
    assert "b***@example.vn" in blob


async def test_switch_mismatch_when_session_file_is_other_account(owner_api: Api, clis: Any,  # noqa: F811
                                                                  monkeypatch: Any) -> None:
    api = owner_api
    await _login(api, AGY, "4/an")
    await _login(api, AGY, "4/binh")
    await verify_pin(api)
    an = await _profile_id(api, "an@example.vn")
    real_write = climod.write_session

    def wrong_write(kind: str, raw: bytes) -> None:
        real_write(kind, _agy_token("khac"))   # tệp phiên sau khi đổi vẫn là tài khoản khác

    monkeypatch.setattr(climod, "write_session", wrong_write)
    out = await _run(api, "agy_switch", {"profile_id": an})
    assert out["status"] == "fail" and out["error_code"] == "AGY_ACCOUNT_MISMATCH", out
    assert out["account"] == "khac@example.vn"
    assert out["detail"]["account_match"] is False and out["detail"]["expected_masked"] == "a***@example.vn"
    assert out["message"].startswith("Đã đổi sang a***@example.vn nhưng lượt gọi thử vẫn chạy bằng tài khoản khác")
    # Gọi thử (lưu lại phiên vào hồ sơ đang dùng) KHÔNG được ghi phiên "khác" đè lên hồ sơ an.
    async with admin_sessionmaker()() as db:
        tok = (await db.execute(text("SELECT token_enc FROM agent.cli_profiles WHERE email = 'an@example.vn'"))
               ).scalar_one()
    assert climod.file_email(crypto.decrypt(bytes(tok), climod.CLI_AAD)) == "an@example.vn"
    ov = (await api.get("/boss-checks")).json()
    assert next(r for r in ov["rows"] if r["key"] == "agy")["done"] is False
    assert "khac@example.vn" not in await _db_text("SELECT message, detail::text FROM ops.boss_checks")


async def test_switch_while_login_in_progress_is_recorded(owner_api: Api, clis: Any, app: Any) -> None:  # noqa: F811
    api = owner_api
    await _login(api, AGY, "4/an")
    await verify_pin(api)
    an = await _profile_id(api, "an@example.vn")
    app.state.cli_logins.busy = lambda org, kind=None: True
    try:
        out = await _run(api, "agy_switch", {"profile_id": an})
    finally:
        del app.state.cli_logins.busy
    assert out["status"] == "fail" and out["error_code"] == "CLI_LOGIN_IN_PROGRESS" and out["transient"] is True
    # Lỗi tạm: không ghi bản kiểm nào (không che kết quả đổi tài khoản đã lưu).
    assert (await api.get("/boss-checks")).json()["results"]["agy_switch"] is None


async def test_switch_without_id_token_uses_userinfo_not_mismatch(owner_api: Api, clis: Any,  # noqa: F811
                                                                  monkeypatch: Any) -> None:
    """Tệp phiên agy có thể không có id_token (email chỉ có qua userinfo) → lượt đổi thành công KHÔNG được báo
    AGY_ACCOUNT_MISMATCH; không đọc được email nào thì 'pass' với account_match = None (không so được ≠ lệch)."""
    api = owner_api
    await _login(api, AGY, "4/an")
    await _login(api, AGY, "4/binh")
    await verify_pin(api)
    an, binh = await _profile_id(api, "an@example.vn"), await _profile_id(api, "binh@example.vn")
    monkeypatch.setattr(climod, "session_email", lambda kind, raw: None)     # như tệp phiên không có id_token
    asked: list[bytes] = []

    async def userinfo(raw: bytes, transport: Any = None) -> dict[str, Any]:
        asked.append(raw)
        return {"email": "an@example.vn", "expires_at": None}

    monkeypatch.setattr(climod, "token_identity", userinfo)
    out = await _run(api, "agy_switch", {"profile_id": an})
    assert out["status"] == "pass" and out["error_code"] is None and out["account"] == "an@example.vn", out
    assert out["detail"]["account_match"] is True and asked

    async def unknown(raw: bytes, transport: Any = None) -> dict[str, Any]:
        return {"email": None, "expires_at": None}

    monkeypatch.setattr(climod, "token_identity", unknown)
    out = await _run(api, "agy_switch", {"profile_id": binh})
    assert out["status"] == "pass" and out["error_code"] is None, out
    assert out["detail"]["account_match"] is None and out["detail"]["account_masked"] is None
    ov = (await api.get("/boss-checks")).json()
    # v0.1.68 (F040): bộ đếm không còn — web không đọc, mỗi lần overview thừa truy vấn
    assert "switch_passes" not in ov
    assert ov["results"]["agy_switch"]["runs"] == 2


async def test_switch_to_active_account_keeps_refreshed_session(owner_api: Api, clis: Any) -> None:  # noqa: F811
    """Lỗi tìm thấy khi đổi qua lại (F-76): `activate` đọc phiên của hồ sơ đích TRƯỚC khi lưu tệp đang dùng → đổi sang
    chính tài khoản đang dùng ghi bản cũ trong CSDL đè lên tệp CLI vừa làm mới token."""
    api = owner_api
    await _login(api, AGY, "4/an")
    await _login(api, AGY, "4/binh")
    await verify_pin(api)
    path = climod.token_path(AGY)
    refreshed = orjson.loads(path.read_bytes()) | {"access_token": "tok-binh-lam-moi"}
    path.write_bytes(orjson.dumps(refreshed))           # CLI tự làm mới token sau lượt gọi
    out = await _run(api, "agy_switch", {"profile_id": await _profile_id(api, "binh@example.vn")})
    assert out["status"] == "pass" and out["account"] == "binh@example.vn", out
    assert orjson.loads(path.read_bytes())["access_token"] == "tok-binh-lam-moi"
    # Đổi sang an rồi về binh: bản đã làm mới vẫn theo hồ sơ binh.
    await _run(api, "agy_switch", {"profile_id": await _profile_id(api, "an@example.vn")})
    assert orjson.loads(path.read_bytes())["access_token"] == "tok-an"
    await _run(api, "agy_switch", {"profile_id": await _profile_id(api, "binh@example.vn")})
    assert orjson.loads(path.read_bytes())["access_token"] == "tok-binh-lam-moi"


async def test_noop_switch_to_active_account_records_from_profile(owner_api: Api, clis: Any) -> None:  # noqa: F811
    """F-76: binh đang hoạt động, "đổi" sang binh (không đổi gì): bản ghi vẫn nói đúng hồ sơ trước đó
    (`from_profile`)."""
    api = owner_api
    await _login(api, AGY, "4/an")
    await _login(api, AGY, "4/binh")
    await verify_pin(api)
    an, binh = await _profile_id(api, "an@example.vn"), await _profile_id(api, "binh@example.vn")
    out = await _run(api, "agy_switch", {"profile_id": binh})
    assert out["status"] == "pass" and out["detail"]["from_profile"] == binh, out
    assert "switch_passes" not in (await api.get("/boss-checks")).json()
    out = await _run(api, "agy_switch", {"profile_id": an})
    assert out["status"] == "pass" and out["detail"]["from_profile"] == binh, out
    ov = (await api.get("/boss-checks")).json()
    # Dòng agy chỉ cần agy_call đạt (v0.1.55); không còn bộ đếm đổi qua lại.
    assert "switch_passes" not in ov and next(r for r in ov["rows"] if r["key"] == "agy")["done"] is False
    await _run(api, "agy_switch", {"profile_id": binh})
    assert (await api.get("/boss-checks")).json()["results"]["agy_switch"]["runs"] == 3


def test_scrub_codes_long_code_is_linear_and_masks_all() -> None:
    import time

    code = "".join(chr(0x41 + (i * 7) % 26) + str(i % 10) for i in range(250))   # 500 ký tự (giới hạn CodeIn)
    msg = ("x" * 50 + code[123:400] + "\n" + code[:90]) * 3
    t = time.perf_counter()
    out = climod.scrub_codes(msg, [code, code[::-1], code[1:]])
    assert time.perf_counter() - t < 0.5
    for i in range(len(code) - 7):
        assert code[i:i + 8] not in out
    assert out.startswith("x" * 50)


def test_scrub_codes_masks_cut_and_wrapped_pieces() -> None:
    code = "4/0AVGzR1A-abcdefghijklmnopqrstuvwxyz0123456789"
    # Đuôi bộ đệm cắt ngang mã + TUI ngắt dòng giữa mã: không mảnh ≥ 8 ký tự nào được lọt ra.
    msg = "CLI đã thoát: " + code[17:] + " | dòng: " + code[:20] + "\n" + code[20:]
    out = climod.scrub_codes(msg, [code])
    for i in range(len(code) - 7):
        assert code[i:i + 8] not in out, code[i:i + 8]
    assert out.startswith("CLI đã thoát: ") and "dòng:" in out
    assert climod.scrub_codes("không có mã", [code, ""]) == "không có mã"


async def test_login_done_even_if_boss_check_raises(owner_api: Api, clis: Any, app: Any,  # noqa: F811
                                                    monkeypatch: Any) -> None:
    """Việc phụ sau khi đã commit hồ sơ (ghi kết quả kiểm) ném lỗi → lượt đăng nhập vẫn 'done', tệp phiên mới giữ
    nguyên (không bị trả tệp phiên cũ về đè)."""
    api = owner_api
    await _login(api, AGY, "4/an")

    async def boom(*a: Any, **k: Any) -> None:
        raise RuntimeError("hỏng khi ghi kết quả kiểm")

    monkeypatch.setattr(type(app.state.cli_logins), "_boss_check_record", boom)
    await _login(api, AGY, "4/binh")
    assert climod.file_email(climod.token_path(AGY).read_bytes()) == "binh@example.vn"


async def test_cancel_after_profile_commit_keeps_new_session_file(owner_api: Api, clis: Any, app: Any,  # noqa: F811
                                                                  monkeypatch: Any) -> None:
    """Huỷ/tắt (CancelledError) SAU khi hồ sơ mới đã commit nhưng trước khi báo "done": khối finally không được trả tệp
    phiên CŨ (an) về trong khi CSDL đã ghi binh là hồ sơ hoạt động."""
    import asyncio

    from tests.test_cli_models_v0131 import _wait

    api = owner_api
    await _login(api, AGY, "4/an")

    async def cancelled(*a: Any, **k: Any) -> None:
        raise asyncio.CancelledError

    monkeypatch.setattr(type(app.state.cli_logins), "_boss_check_record", cancelled)
    await verify_pin(api)  # v0.1.45 (F-20): thêm tài khoản CLI cần PIN
    r = await api.send("POST", f"/cli/login?kind={AGY}")
    login_id = r.json()["login_id"]
    assert (await _wait(api, login_id, ("waiting_code", "failed", "done")))["status"] == "waiting_code"
    await api.send("POST", f"/cli/login/{login_id}/code", {"code": "4/binh"})
    await _wait(api, login_id, ("done", "failed"))
    task = app.state.cli_logins.sessions[login_id].task
    await asyncio.wait([task], timeout=10)             # khối finally (trả/bỏ tệp gửi tạm) đã chạy xong
    assert climod.file_email(climod.token_path(AGY).read_bytes()) == "binh@example.vn"
    profs = {p["email"]: p["active"] for p in (await api.get(f"/cli/profiles?kind={AGY}")).json()}
    assert profs == {"an@example.vn": False, "binh@example.vn": True}
