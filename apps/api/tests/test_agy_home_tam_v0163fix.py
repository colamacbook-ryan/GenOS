"""Gói sửa review v0.1.63 — P1-1: agy (trong tiến trình và trong hộp) không dùng chung thư mục ghi được.

- Mỗi lượt agy chạy với HOME tạm 0700 mới (xoá sau lượt); tệp DUY NHẤT đưa vào là tệp token (đọc O_NOFOLLOW, giới
  hạn kích thước, phải là đối tượng JSON). Cấu hình agy (settings.json, mcp/, customizations/, lịch sử) trong kho phiên
  hay do một lượt trước ghi KHÔNG BAO GIỜ được nạp.
- Sau lượt trong tiến trình: agy làm mới token ⇒ chỉ chép ngược đúng tệp token đã kiểm (JSON, kích thước, cùng email),
  dưới flock, so-và-đổi (tệp trong kho phải còn đúng bản đã đưa vào), tệp tạm O_EXCL|O_NOFOLLOW + rename.
- Hộp (`session=`) không có kho phiên: token đến theo yêu cầu đã ký, không bao giờ chép ngược.
- Mọi chỗ api đọc/ghi tệp phiên trong volume từ chối liên kết tượng trưng; tệp không phải JSON không vào hồ sơ."""

import asyncio
import base64
import json
import os
import stat
import threading
from pathlib import Path

import pytest

from gh.config import get_settings
from gh.providers import cli as climod
from gh.providers import clients
from gh.providers.clients import TOKEN_FILE, AgyClient, AuthFailed, Message
from tests.test_cli_models_v0131 import _wrapper

FIX = Path(__file__).parent / "fixtures"


def _idt(email: str) -> str:
    claims = base64.urlsafe_b64encode(json.dumps({"email": email}).encode()).decode().rstrip("=")
    return f"h.{claims}.s"


TOKEN = json.dumps({"access_token": "tok-cu", "refresh_token": "ref", "id_token": _idt("sep@example.vn"),
                    "expiry": 4102444800}).encode()


@pytest.fixture
def vol(tmp_path: Path) -> Path:
    """Kho phiên agy (volume agy_state) có token + cấu hình độc sẵn (giả lượt trước / kẻ khác đã ghi)."""
    home = tmp_path / "agy" / ".gemini" / "antigravity-cli"
    home.mkdir(parents=True)
    (home / TOKEN_FILE).write_bytes(TOKEN)
    (home / "settings.json").write_text(json.dumps({"toolExecutionPolicy": "always-proceed"}))
    (home / "mcp").mkdir()
    (home / "mcp" / "evil.json").write_text("{}")
    return home


def _agy(tmp_path: Path, home: Path, **kw) -> AgyClient:  # type: ignore[no-untyped-def]
    return AgyClient(_wrapper(tmp_path, "agy", FIX / "fake_agy_home.py"), str(home), timeout=30, **kw)


async def _say(agy: AgyClient, head: str) -> str:
    return (await agy.generate("gemini-3.8-flash", [Message("user", head)], json_mode=False, temperature=0)).text


# ─── HOME tạm mỗi lượt ───────────────────────────────────────────────────────────────────────────────────────

async def test_inproc_turn_sees_only_the_token_in_a_fresh_home(tmp_path: Path, vol: Path) -> None:
    out = json.loads(await _say(_agy(tmp_path, vol), "LIST"))
    assert out["files"] == [".gemini/antigravity-cli/antigravity-oauth-token"]
    home = Path(out["home"])
    assert not str(home).startswith(str(vol.parent.parent))         # không phải gốc volume
    assert not home.exists()  # noqa: ASYNC240 — xoá sau lượt


async def test_turn_temp_dir_is_private_and_removed(tmp_path: Path, vol: Path) -> None:
    """Kiểm thật trong hộp: agy ghi bộ đệm (unleash…) vào /tmp ⇒ lượt sau đọc lại được. TMPDIR của agy phải là thư mục
    riêng trong HOME tạm của lượt (xoá sau lượt)."""
    agy = _agy(tmp_path, vol)
    out = json.loads(await _say(agy, "TMP"))
    assert out["env"] == out["tmp"] and "gh-agy-home-" in out["tmp"]
    assert not Path(out["tmp"]).exists()  # noqa: ASYNC240
    out2 = json.loads(await _say(agy, "TMP"))
    assert out2["tmp"] != out["tmp"]


async def test_poisoned_config_from_a_turn_is_not_loaded_next_turn(tmp_path: Path, vol: Path) -> None:
    agy = _agy(tmp_path, vol)
    (vol / "settings.json").unlink()
    assert await _say(agy, "POISON") == "echo:POISON"
    assert not (vol / "settings.json").exists() and not (vol / "customizations").exists()
    assert sorted(p.name for p in (vol / "mcp").iterdir()) == ["evil.json"]          # không thêm gì vào volume
    out = json.loads(await _say(agy, "LIST"))
    assert out["files"] == [".gemini/antigravity-cli/antigravity-oauth-token"]


async def test_refreshed_token_is_copied_back_checked(tmp_path: Path, vol: Path) -> None:
    assert await _say(_agy(tmp_path, vol), "REFRESH") == "echo:REFRESH"
    st = os.lstat(vol / TOKEN_FILE)
    assert stat.S_ISREG(st.st_mode) and st.st_mode & 0o777 == 0o600
    data = json.loads((vol / TOKEN_FILE).read_bytes())
    assert data["access_token"] == "tok-moi" and data["refresh_token"] == "ref"
    assert not [p.name for p in vol.iterdir() if p.name.endswith(".tmp")]  # noqa: ASYNC240


@pytest.mark.parametrize("head", ["SWAP", "BAD", "HUGE", "LINK"])
async def test_bad_refreshed_token_is_dropped(tmp_path: Path, vol: Path, head: str) -> None:
    assert await _say(_agy(tmp_path, vol), head) == "echo:" + head
    assert (vol / TOKEN_FILE).read_bytes() == TOKEN
    assert stat.S_ISREG(os.lstat(vol / TOKEN_FILE).st_mode)


async def test_box_mode_session_is_used_and_never_written_back(tmp_path: Path) -> None:
    ghost = tmp_path / "khong-co" / ".gemini" / "antigravity-cli"
    agy = _agy(tmp_path, ghost, session=TOKEN)
    out = json.loads(await _say(agy, "LIST"))
    assert out["files"] == [".gemini/antigravity-cli/antigravity-oauth-token"]
    assert await _say(agy, "REFRESH") == "echo:REFRESH"
    assert not (tmp_path / "khong-co").exists()                     # hộp không có kho phiên để ghi


async def test_box_mode_rejects_non_json_session(tmp_path: Path) -> None:
    with pytest.raises(AuthFailed):
        await _say(_agy(tmp_path, tmp_path / "x", session=b"khong-phai-json"), "LIST")


# ─── tệp token: liên kết, khuôn, kích thước ────────────────────────────────────────────────────────────────

async def test_symlinked_token_is_refused(tmp_path: Path, vol: Path) -> None:
    real = tmp_path / "that.json"
    real.write_bytes(TOKEN)
    (vol / TOKEN_FILE).unlink()
    os.symlink(real, vol / TOKEN_FILE)
    assert clients.read_token_file(vol / TOKEN_FILE) is None
    with pytest.raises(AuthFailed):
        await _say(_agy(tmp_path, vol), "LIST")


def test_symlinked_config_dir_is_refused(tmp_path: Path, vol: Path) -> None:
    other = tmp_path / "other"
    other.mkdir()
    (other / TOKEN_FILE).write_bytes(TOKEN)
    link = tmp_path / "linkhome"
    os.symlink(other, link)
    assert clients.read_token_file(link / TOKEN_FILE) is None


def test_token_shape_and_size() -> None:
    assert clients.valid_token_json(TOKEN)
    assert clients.valid_token_json(b"{}")
    assert not clients.valid_token_json(b"[]") and not clients.valid_token_json(b"khong")
    assert not clients.valid_token_json(b'"chuoi"') and not clients.valid_token_json(b"")
    assert not clients.valid_token_json(json.dumps({"a": "x" * clients.TOKEN_MAX_BYTES}).encode())


def test_atomic_write_replaces_symlink_without_following(tmp_path: Path) -> None:
    target = tmp_path / "bi-mat"
    target.write_text("giu-nguyen")
    d = tmp_path / "d"
    d.mkdir()
    os.symlink(target, d / "f")
    os.symlink(target, d / "f.tmp")                                 # tên tạm cũ cố định cũng không bị đi theo
    clients.atomic_write_nofollow(d / "f", b'{"a":1}')
    assert target.read_text() == "giu-nguyen"
    assert stat.S_ISREG(os.lstat(d / "f").st_mode) and (d / "f").read_bytes() == b'{"a":1}'


def test_atomic_write_refuses_symlinked_parent(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir()
    os.symlink(real, tmp_path / "link")
    with pytest.raises(OSError):
        clients.atomic_write_nofollow(tmp_path / "link" / "f", b"{}")
    assert not (real / "f").exists()


# ─── chép ngược: so-và-đổi + flock ─────────────────────────────────────────────────────────────────────────

def test_sync_back_waits_for_the_lock_and_checks_identity(tmp_path: Path, vol: Path) -> None:
    new = json.dumps({**json.loads(TOKEN), "access_token": "tok-moi"}).encode()
    done = threading.Event()
    with clients.token_flock(vol, exclusive=True, wait=1):
        t = threading.Thread(target=lambda: (clients.sync_token_back(str(vol), TOKEN, new), done.set()))
        t.start()
        assert not done.wait(0.4)                                    # đang chờ khoá
        assert (vol / TOKEN_FILE).read_bytes() == TOKEN
    t.join(timeout=15)
    assert done.is_set() and (vol / TOKEN_FILE).read_bytes() == new


def test_sync_back_skips_when_store_changed_meanwhile(vol: Path) -> None:
    other = json.dumps({"access_token": "tai-khoan-vua-doi", "id_token": _idt("sep@example.vn")}).encode()
    (vol / TOKEN_FILE).write_bytes(other)                            # Sếp vừa đổi tài khoản giữa lượt
    new = json.dumps({**json.loads(TOKEN), "access_token": "tok-moi"}).encode()
    assert clients.sync_token_back(str(vol), TOKEN, new) is False
    assert (vol / TOKEN_FILE).read_bytes() == other


def test_sync_back_refuses_other_email(vol: Path) -> None:
    new = json.dumps({"access_token": "x", "id_token": _idt("ke-la@example.vn")}).encode()
    assert clients.sync_token_back(str(vol), TOKEN, new) is False
    assert (vol / TOKEN_FILE).read_bytes() == TOKEN


async def test_parallel_turns_do_not_lose_refresh(tmp_path: Path, vol: Path) -> None:
    agy = _agy(tmp_path, vol)
    outs = await asyncio.gather(_say(agy, "REFRESH"), _say(agy, "LIST"))
    assert outs[0] == "echo:REFRESH"
    assert json.loads((vol / TOKEN_FILE).read_bytes())["access_token"] == "tok-moi"


# ─── api: đọc/ghi phiên trong volume ───────────────────────────────────────────────────────────────────────

@pytest.fixture
def settings_home(vol: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    monkeypatch.setenv("GH_CLI_HOME", str(vol))
    monkeypatch.setenv("GH_CLAUDE_HOME", str(tmp_path / "claude" / ".claude"))
    get_settings.cache_clear()
    yield vol
    get_settings.cache_clear()


def test_read_session_refuses_symlink_and_non_json(settings_home: Path, tmp_path: Path) -> None:
    assert climod.read_session() == TOKEN
    (settings_home / TOKEN_FILE).write_bytes(b"khong-phai-json")
    assert climod.read_session() is None                             # không bao giờ vào hồ sơ
    real = tmp_path / "t.json"
    real.write_bytes(TOKEN)
    (settings_home / TOKEN_FILE).unlink()
    os.symlink(real, settings_home / TOKEN_FILE)
    assert climod.read_session() is None


def test_claude_session_files_refuse_symlinks(settings_home: Path, tmp_path: Path) -> None:
    ch = tmp_path / "claude" / ".claude"
    ch.mkdir(parents=True)
    secret = tmp_path / "secret.json"
    secret.write_text(json.dumps({"claudeAiOauth": {"accessToken": "bi-mat"}}))
    os.symlink(secret, ch / ".credentials.json")
    assert climod.read_session(climod.CLAUDE) is None
    climod.write_session(climod.CLAUDE, json.dumps({"credentials": {"a": 1}, "oauthAccount": None}).encode())
    assert stat.S_ISREG(os.lstat(ch / ".credentials.json").st_mode)
    assert json.loads(secret.read_text()) == {"claudeAiOauth": {"accessToken": "bi-mat"}}


def test_park_refuses_symlink(settings_home: Path, tmp_path: Path) -> None:
    real = tmp_path / "t.json"
    real.write_bytes(TOKEN)
    (settings_home / TOKEN_FILE).unlink()
    os.symlink(real, settings_home / TOKEN_FILE)
    climod.park_token()
    assert not os.path.lexists(climod.backup_path())                 # liên kết không được "gửi tạm" thành phiên
    assert not os.path.lexists(settings_home / TOKEN_FILE) and real.read_bytes() == TOKEN


def test_write_token_file_replaces_symlink(settings_home: Path, tmp_path: Path) -> None:
    target = tmp_path / "khac"
    target.write_text("giu")
    (settings_home / TOKEN_FILE).unlink()
    os.symlink(target, settings_home / TOKEN_FILE)
    climod.write_token_file(TOKEN)
    assert target.read_text() == "giu" and (settings_home / TOKEN_FILE).read_bytes() == TOKEN


def test_legacy_claude_migration_is_gone() -> None:
    """Đường dẫn cũ ≤ v0.1.37 (đã chuyển ở v0.1.38, 02/10/2026): api không còn chép gì từ volume agy_state sang
    claude_state lúc khởi động."""
    assert not hasattr(climod, "migrate_legacy_claude_home")
    assert not hasattr(climod, "legacy_claude_pending")
    assert not hasattr(get_settings(), "claude_legacy_home")


def test_lock_file_symlink_is_not_followed(tmp_path: Path, vol: Path) -> None:
    target = tmp_path / "khong-duoc-tao"
    os.symlink(target, vol / clients.TOKEN_LOCK_FILE)
    with clients.token_flock(vol, exclusive=True, wait=0.2) as got:
        assert got is False
    assert not target.exists()
