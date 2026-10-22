"""Đăng nhập Antigravity CLI qua Console, với CLI giả mô phỏng hành vi đo thật của agy 1.2.9.
(tests/fixtures/fake_agy.py)

Hai lỗi thật trên máy Owner (v0.1.11, "Quá 10 phút chưa hoàn tất đăng nhập"):
1. CLI hỏi terminal (Device Attributes) rồi chờ; không ai trả lời → không bao giờ in link.
2. Link ~700 ký tự bị ngắt dòng; chỉ bắt dòng đầu → link cụt. Bản đầy đủ nằm trong hyperlink OSC 8.
"""

import asyncio
import sys
from pathlib import Path

from gh.providers import cli as climod
from tests.conftest import verify_pin

FAKE = Path(__file__).parent / "fixtures" / "fake_agy.py"


def test_terminal_replies_answer_device_attribute_queries() -> None:
    assert climod.terminal_replies(b"\x1b[>c\x1b_Ga=q;AAAA\x1b\\\x1b[c") == b"\x1b[>1;10;0c\x1b[?62;22c"
    assert climod.terminal_replies(b"plain text") == b""


def test_login_url_prefers_full_osc8_link_over_wrapped_text() -> None:
    full = "https://accounts.google.com/o/oauth2/auth?a=1&b=2&state=END"
    raw = f"Open:\r\nhttps://accounts.google.com/o/oau\r\nth2/auth?a=1\x1b]8;;{full}\x1b\\Click\x1b]8;;\x1b\\"
    assert climod.login_url(raw, "https://accounts.google.com/o/oau") == full
    assert climod.login_url("", "see https://x.test/a.") == "https://x.test/a"


async def test_console_login_with_real_cli_behaviour(owner_api, app, tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("GH_CLI_HOME", str(tmp_path / ".gemini" / "antigravity-cli"))
    from gh.config import get_settings

    get_settings.cache_clear()
    logins = app.state.cli_logins
    logins.argv = [sys.executable, str(FAKE)]

    await verify_pin(owner_api)  # v0.1.45 (F-20): thêm tài khoản CLI cần PIN
    r = await owner_api.send("POST", "/cli/login")
    assert r.status_code == 202, r.text
    login_id = r.json()["login_id"]
    s = next(iter(logins.sessions.values()))
    for _ in range(100):
        if s.status != "starting":
            break
        await asyncio.sleep(0.1)
    assert s.status == "waiting_code", s.message
    assert s.url is not None and s.url.endswith("&state=END"), s.url

    r = await owner_api.send("POST", f"/cli/login/{login_id}/code", {"code": "4/abc"})
    assert r.status_code == 202, r.text
    for _ in range(100):
        if s.status in ("done", "failed"):
            break
        await asyncio.sleep(0.1)
    assert s.status == "done", s.message
    profiles = (await owner_api.get("/cli/profiles")).json()
    assert [p["email"] for p in profiles] == ["boss@example.vn"]
    get_settings.cache_clear()


async def test_without_terminal_replies_cli_never_shows_link(owner_api, app, tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Tái hiện lỗi cũ: không trả lời truy vấn terminal thì CLI không in link, đăng nhập thất bại."""
    monkeypatch.setenv("GH_CLI_HOME", str(tmp_path / ".gemini" / "antigravity-cli"))
    monkeypatch.setattr(climod, "terminal_replies", lambda chunk: b"")
    from gh.config import get_settings

    get_settings.cache_clear()
    logins = app.state.cli_logins
    logins.argv = [sys.executable, str(FAKE)]
    await verify_pin(owner_api)  # v0.1.45 (F-20): thêm tài khoản CLI cần PIN
    r = await owner_api.send("POST", "/cli/login")
    assert r.status_code == 202, r.text
    s = next(iter(logins.sessions.values()))
    for _ in range(150):
        if s.status == "failed":
            break
        await asyncio.sleep(0.1)
    assert s.status == "failed" and s.url is None
    get_settings.cache_clear()


# ─── v0.1.58 — nhãn "Sắp hết hạn" của agy: token truy cập sống 1 giờ, CLI tự gia hạn nếu có khoá làm mới ──────────

SECRET = "ya29.TOKEN-BI-MAT-KHONG-DUOC-LOG-0123456789"
REFRESH_SECRET = "1//REFRESH-BI-MAT-KHONG-DUOC-LOG-9876543210"


def _agy_token(extra: dict[str, object]) -> bytes:
    """Tệp phiên agy còn 56 phút (hạn epoch giây) + `extra`."""
    import time

    import orjson

    return orjson.dumps({"access_token": SECRET, "expiry": int(time.time()) + 56 * 60, **extra})


def test_agy_session_with_refresh_key_is_not_expiring(caplog) -> None:  # type: ignore[no-untyped-def]
    import logging

    caplog.set_level(logging.DEBUG)
    cases = [
        ({"refreshToken": REFRESH_SECRET}, True),                                  # khoá cấp đầu (camelCase)
        ({"refresh_token": REFRESH_SECRET}, True),                                 # khoá cấp đầu (snake_case)
        ({"token": {"refresh_token": REFRESH_SECRET}}, True),                      # lồng một cấp
        ({"token": {"refreshToken": REFRESH_SECRET}}, True),
        ({"token": {"deep": {"refresh_token": REFRESH_SECRET}}}, False),           # lồng hai cấp: không tính
        ({}, False),                                                               # không có khoá làm mới
        ({"note": "refresh_token"}, False),                                        # tên khoá chứ không phải giá trị
        ({"refresh_token": "", "refreshToken": None, "token": {"refresh_token": None}}, False),  # khoá rỗng / null
    ]
    states = []
    for extra, want in cases:
        meta = climod.session_meta(climod.AGY, _agy_token(extra))
        assert meta["refreshable"] is want, extra
        states.append(climod.profile_state(meta["expires_at"], refreshable=meta["refreshable"]))
    # còn 56 phút: "Sắp hết hạn" chỉ khi KHÔNG làm mới được
    assert states == ["ok", "ok", "ok", "ok", "expiring", "expiring", "expiring", "expiring"]
    assert SECRET not in caplog.text and REFRESH_SECRET not in caplog.text


async def test_agy_profile_label_follows_refresh_key_and_no_token_in_output(owner_api, caplog) -> None:  # type: ignore[no-untyped-def]
    """Qua API `/cli/profiles`: hồ sơ còn 56 phút — có khoá làm mới ⇒ 'ok', không có ⇒ 'expiring'; giá trị token không
    xuất hiện trong phản hồi lẫn log."""
    import logging
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import text

    from gh import crypto
    from gh.db import sessionmaker

    caplog.set_level(logging.DEBUG)
    async with sessionmaker()() as db:
        org = (await db.execute(text("SELECT id FROM core.organizations LIMIT 1"))).scalar_one()
        pid = await climod.cli_provider_id(db, org)
        left = datetime.now(UTC) + timedelta(minutes=56)
        for tag, extra in (("co-khoa", {"refreshToken": REFRESH_SECRET}), ("khong-khoa", {})):
            enc = crypto.encrypt(_agy_token(extra), climod.CLI_AAD)
            await db.execute(text("""INSERT INTO agent.cli_profiles (org_id, provider_id, email, token_enc,
                                                                      expires_at, is_active)
                                     VALUES (:o, :p, :e, :t, :x, false)"""),
                             {"o": org, "p": pid, "e": f"{tag}@example.vn", "t": enc, "x": left})
        await db.commit()
    r = await owner_api.get("/cli/profiles")
    assert r.status_code == 200, r.text
    by = {p["email"]: p for p in r.json()}
    assert by["co-khoa@example.vn"]["state"] == "ok" and by["co-khoa@example.vn"]["refreshable"] is True
    assert by["khong-khoa@example.vn"]["state"] == "expiring" and by["khong-khoa@example.vn"]["refreshable"] is False
    assert SECRET not in r.text and REFRESH_SECRET not in r.text
    assert SECRET not in caplog.text and REFRESH_SECRET not in caplog.text
