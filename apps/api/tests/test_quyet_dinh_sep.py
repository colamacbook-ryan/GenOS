# ruff: noqa: F811 — fixture `w` nhập từ test_p3_duty (tham số test trùng tên là cách pytest dùng fixture)
"""L6 (v0.1.69, gói D3) — quyết định của Sếp thành test: trôi khỏi ý Sếp mà không ai biết thì CI đỏ.

Mỗi quyết định (QD) đang hiệu lực có ít nhất một khẳng định; docstring/tên test ghi mã QD. Quyết định MỚI của Sếp ⇒ thêm
khẳng định trong cùng PR (kế hoạch sửa sau kiểm kê 16/10/2026, lưới L6).

- QD-20 (Sếp duyệt 10/10): Gen hướng dẫn yên lặng 21:00–07:00, ≤ 3 việc, chuông ≤ 1/ngày, cron 09:05/11:05/14:05 (F121).
- QD-18: ghi Kho dữ liệu (Phiên, Việc) chỉ qua Xác nhận + mã PIN; kanban, mail, lịch không ghi (F124).
- QD-22 (2): không còn chữ "Kho <tên riêng cũ>" trong chuỗi chạy thật, kể cả dữ liệu đã lưu qua migration (F122).
- QD-12: trình duyệt nền không "stealth", không né/giải CAPTCHA.
- v0.1.55 (Sếp "gọn"): chỉ 1 dòng "Việc Sếp cần làm" bắt buộc; mô tả mục tiêu Gen bám đúng điều đã bỏ (F033).
- 16/10 Q1: Antigravity mở cho lớp core/nội bộ (trong tiến trình); lớp ngoại giao / người ngoài (trả lời, soạn tin,
  sàng lọc, Gen nhân viên) chạy qua hộp riêng từ v0.1.63 — hộp tắt thì không dùng agy.
- 16/10 Telegram: Gen chỉ ĐỀ XUẤT — không nút Duyệt, không nhận mã PIN, không ghi ra ngoài từ Telegram.
- 16/10 Q5 (=2): thu thành viên MỌI nhóm, kể cả nhóm "Không nghe". Q6 (=1): chế độ "chỉ khi được tag" vẫn lưu tin,
  chỉ sửa chữ.
- 17/10 (thay quyết định «hỏi khi kết nối kênh»): nhóm MỚI (add vào) mặc định «Lắng nghe im lặng» — lưu hết tin, KHÔNG
  trả lời kể cả khi được tag; `tagged_only` vẫn trả lời khi được tag. Ca chi tiết còn ở test_qd_17_10_nghe_im_lang.py.
- 16/10 Q4 (=2): vai Quản lý thấy việc được giao cho mình (không hứa "team"); Q7 (=1): giữ Jev; Q8 (=1): ghi
  hệ thống ngoài
  chặn và nói rõ; Q2/Q3: xem test_*_quyet_dinh_* của gói liên quan.
"""

import ast
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import psycopg
import pytest
from sqlalchemy import text

from gh.biz.duty import engine as duty_engine
from gh.boss_checks import service as boss_service
from gh.data.common import DEFAULT_NEW_GROUP_MODE, NEW_GROUP_MODES, initial_group_mode, new_group_mode
from gh.db import sessionmaker
from gh.gen import proposals
from gh.gen.coach import engine
from gh.hub_link import service as hub
from gh.providers import agy_policy
from tests.conftest import PG, Api
from tests.phase2 import msg, org_id, put
from tests.test_no_personal_info_v0156 import OLD_NAME
from tests.test_p3_duty import BUY, agent_drafts, decide, decisions, hook, make_agent, refine, w  # noqa: F401

ROOT = Path(__file__).resolve().parents[3]
GH_DIR = ROOT / "apps" / "api" / "gh"
SQL_0041 = ROOT / "db" / "sql" / "0041_v016x_theo_qd.sql"
VN = ZoneInfo("Asia/Ho_Chi_Minh")


# ─── QD-20 · Gen hướng dẫn ────────────────────────────────────────────────────────────────────────────────────

def test_qd20_defaults_quiet_21_to_7_three_todos_one_lesson() -> None:
    """QD-20: yên lặng 21:00–07:00 (F121 — từng là 22), tối đa 3 việc, 1 bài/ngày."""
    p = engine.Prefs()
    assert (p.quiet_start, p.quiet_end) == (21, 7)
    assert engine.MAX_TODOS == 3
    assert p.lessons_per_day == 1 and p.bell is True


@pytest.mark.parametrize(("hour", "minute", "quiet"), [(20, 59, False), (21, 0, True), (23, 30, True), (3, 0, True),
                                                       (6, 59, True), (7, 0, False), (9, 5, False)])
def test_qd20_quiet_window_edges(hour: int, minute: int, quiet: bool) -> None:
    """QD-20: ranh giới giờ yên lặng đúng 21:00 (vào) và 07:00 (ra), theo múi giờ tổ chức."""
    now = datetime(2026, 10, 12, hour, minute, tzinfo=VN).astimezone(UTC)
    assert engine.in_quiet_hours(engine.Prefs(), now, VN) is quiet


def test_qd20_cron_times() -> None:
    """QD-20: chuông Gen hướng dẫn chạy 09:05, 11:05, 14:05 giờ VN (trong giờ làm việc)."""
    from gh.worker import WorkerSettings

    cj = next(c for c in WorkerSettings.cron_jobs if c.name == "cron:gen_coach")
    assert cj.hour == {9, 11, 14} and cj.minute == {5}


async def test_qd20_new_owner_prefs_default_21_and_bell_once_a_day(owner_api: Api, db: Any) -> None:
    """QD-20: máy mới trả giờ yên lặng 21→7; cột DB mặc định 21; chuông ≤ 1/ngày (cổng nguyên tử `claim_bell`)."""
    from gh.gen.coach import store

    prefs = (await owner_api.get("/gen/coach/prefs")).json()
    assert (prefs["quiet_start"], prefs["quiet_end"]) == (21, 7)
    default = (await db.execute(text("""SELECT column_default FROM information_schema.columns
                                        WHERE table_schema = 'agent' AND table_name = 'gen_coach_prefs'
                                          AND column_name = 'quiet_start'"""))).scalar_one()
    assert default == "21"
    org = await org_id(db)
    uid = (await db.execute(text("SELECT id FROM core.users WHERE org_id = :o LIMIT 1"), {"o": org})).scalar_one()
    await store.ensure_prefs(db, org, uid)
    today = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    assert await store.claim_bell(db, uid, today, ["todo:a"], datetime.now(UTC)) is True
    assert await store.claim_bell(db, uid, today, ["todo:a"], datetime.now(UTC)) is False


async def test_qd20_migration_0041_moves_default_rows_only_and_is_rerunnable(owner_api: Api, db: Any,
                                                                             fresh_db: str) -> None:
    """QD-20: migration đổi hàng còn mặc định cũ 22→21; giá trị Sếp đã chỉnh (lệch hoặc có trong Nhật ký) được giữ."""
    org = await org_id(db)
    users = [(await db.execute(text("""
        INSERT INTO core.users (org_id, email, display_name, password_hash) VALUES (:o, :e, :n, 'x') RETURNING id"""),
                               {"o": org, "e": f"u{i}@example.test", "n": f"Owner {i}"})).scalar_one()
             for i in range(4)]
    await db.commit()
    sql = SQL_0041.read_text(encoding="utf-8")
    with psycopg.connect(f"{PG}/{fresh_db}", autocommit=True) as c:
        ins = "INSERT INTO agent.gen_coach_prefs (user_id, org_id, quiet_start, quiet_end) VALUES (%s, %s, %s, %s)"
        c.execute("ALTER TABLE agent.gen_coach_prefs ALTER COLUMN quiet_start SET DEFAULT 22")   # máy v0.1.54–v0.1.68
        c.execute(ins, (users[0], org, 22, 7))      # còn mặc định cũ ⇒ đổi
        c.execute(ins, (users[1], org, 23, 7))      # Sếp chỉnh 23 ⇒ giữ
        c.execute(ins, (users[2], org, 22, 8))      # Sếp chỉnh giờ ra ⇒ giữ
        c.execute(ins, (users[3], org, 22, 7))      # Sếp từng chỉnh rồi quay lại đúng 22 (có trong Nhật ký) ⇒ giữ
        c.execute("""INSERT INTO ops.action_log (org_id, actor_type, actor_id, action, target_type, target_id, result,
                                                 detail, row_hash)
                     VALUES (%s, 'user', %s, 'gen.coach_prefs_changed', 'settings', 'gen_coach', 'ok',
                             '{"fields": ["quiet_start", "quiet_end"]}', '\\x00')""", (org, f"user:{users[3]}"))

        def snap() -> dict[str, int]:
            rows = c.execute("SELECT user_id, quiet_start FROM agent.gen_coach_prefs").fetchall()
            return {str(u): int(q) for u, q in rows}

        c.execute(sql)  # type: ignore[call-overload]
        first = snap()
        assert first == {str(users[0]): 21, str(users[1]): 23, str(users[2]): 22, str(users[3]): 22}
        c.execute(sql)  # type: ignore[call-overload]
        assert snap() == first
        default = c.execute("""SELECT column_default FROM information_schema.columns WHERE table_schema = 'agent'
                               AND table_name = 'gen_coach_prefs' AND column_name = 'quiet_start'""").fetchone()
        assert default == ("21",)


# ─── QD-18 · ghi Kho chỉ qua Xác nhận + PIN ───────────────────────────────────────────────────────────────────

def test_qd18_kho_write_needs_pin_and_owner_other_writes_denied() -> None:
    """QD-18: ghi Kho (Phiên, Việc) luôn cần mã PIN và chỉ Owner; tool ghi kanban/mail/lịch/Drive bị chặn."""
    assert proposals.requires_pin(f"{proposals.KHO_TARGET_PREFIX}Phiên") is True
    assert proposals.requires_pin(f"{proposals.KHO_TARGET_PREFIX}VIEC-1") is True
    assert set(proposals.KHO_TYPES) <= set(proposals.OWNER_ONLY_TYPES)
    from gh.auth import service as auth_service

    assert auth_service.PIN_OPERATIONS["hub.write"].startswith("Ghi Kho dữ liệu")
    for suffix in ("gmail_send", "gmail_create_draft", "calendar_create_event", "drive_create_file", "tasks_create"):
        assert suffix in hub.WRITE_SUFFIXES_DENY
        assert suffix not in hub.READ_SUFFIXES
    assert all(not re.search(r"create|update|send|write|share|edit", s) for s in hub.READ_SUFFIXES)


def test_qd18_hub_server_note_says_write_needs_confirm_and_pin() -> None:
    """QD-18 (F124): ghi chú máy chủ Gen-hub không còn "(chỉ đọc)" — nói rõ ghi Kho chỉ khi Sếp Xác nhận + mã PIN."""
    assert "chỉ đọc" not in hub.SERVER_NOTE
    assert "ghi Kho chỉ khi Sếp Xác nhận + mã PIN" in hub.SERVER_NOTE
    for rel in ("gen/registry.json",):
        raw = (GH_DIR / rel).read_text(encoding="utf-8")
        assert "(chỉ đọc, chỉ Sếp)" not in raw


async def test_qd18_migration_0041_updates_old_server_note_only_and_is_rerunnable(owner_api: Api, db: Any,
                                                                                  fresh_db: str) -> None:
    """QD-18 (F124): ghi chú máy chủ Gen-hub cũ "(chỉ đọc)" đã lưu → ghi chú mới (kể cả khi Owner đặt tên Kho khác);
    ghi chú Owner tự sửa, máy chủ khác, bản mới: giữ nguyên."""
    org = await org_id(db)
    await db.rollback()
    old = "Liên kết Gen-hub — Gen đọc {k}, lịch, mail, việc, Drive (chỉ đọc). Quản lý ở thẻ Gen-hub."
    ins = ("INSERT INTO agent.mcp_servers (org_id, name, transport, endpoint, note) "
           "VALUES (%s, %s, 'streamable_http', 'https://mcp.example.test/mcp', %s)")
    sql = SQL_0041.read_text(encoding="utf-8")
    with psycopg.connect(f"{PG}/{fresh_db}", autocommit=True) as c:
        c.execute(ins, (org, "Gen-hub", old.format(k="Kho dữ liệu")))
        c.execute(ins, (org, "Gen-hub", old.format(k="Kho nhà tôi")))               # Owner tự đặt tên Kho (v0.1.57)
        c.execute(ins, (org, "Gen-hub", "Ghi chú Owner tự sửa (chỉ đọc)"))          # tự sửa tay ⇒ KHÔNG đụng
        c.execute(ins, (org, "Máy chủ khác", old.format(k="Kho dữ liệu")))          # tên máy chủ khác ⇒ KHÔNG đụng

        def notes() -> list[tuple[str, str]]:
            return c.execute("SELECT name, note FROM agent.mcp_servers ORDER BY name, note").fetchall()

        c.execute(sql)  # type: ignore[call-overload]
        first = notes()
        c.execute(sql)  # type: ignore[call-overload]
        assert notes() == first
        new = "(đọc; ghi Kho chỉ khi Sếp Xác nhận + mã PIN). Quản lý ở thẻ Gen-hub."
        assert sorted(first) == sorted([
            ("Gen-hub", "Liên kết Gen-hub — Gen đọc Kho dữ liệu, lịch, mail, việc, Drive " + new),
            ("Gen-hub", "Liên kết Gen-hub — Gen đọc Kho nhà tôi, lịch, mail, việc, Drive " + new),
            ("Gen-hub", "Ghi chú Owner tự sửa (chỉ đọc)"),
            ("Máy chủ khác", old.format(k="Kho dữ liệu"))])


# ─── QD-22 · không còn tên riêng trong dữ liệu đã lưu ─────────────────────────────────────────────────────────

async def test_qd22_migration_0041_cleans_stored_old_kho_name_and_is_rerunnable(owner_api: Api, db: Any,
                                                                                fresh_db: str) -> None:
    """QD-22 (F122): hội thoại Gen + chuông đã lưu chữ "Kho <tên riêng cũ>" (v0.1.50–55) được thay bằng "Kho dữ liệu";
    dòng khác giữ nguyên; chạy lại không đổi gì."""
    org = await org_id(db)
    uid = (await db.execute(text("SELECT id FROM core.users WHERE org_id = :o LIMIT 1"), {"o": org})).scalar_one()
    await db.rollback()
    old = f"Kho {OLD_NAME.removeprefix('Kho ')}"
    sql = SQL_0041.read_text(encoding="utf-8")
    with psycopg.connect(f"{PG}/{fresh_db}", autocommit=True) as c:
        conv = c.execute("""INSERT INTO agent.gen_conversations (org_id, user_id, title) VALUES (%s, %s, 'T')
                            RETURNING id""", (org, uid)).fetchone()
        assert conv is not None
        contents = (f"Em đề xuất ghi một Phiên vào {old} để lưu mốc", f"em đọc {old.lower()} rồi", "Không liên quan")
        for content in contents:
            c.execute("INSERT INTO agent.gen_messages (conversation_id, org_id, role, content) "
                      "VALUES (%s, %s, 'assistant', %s::jsonb)", (conv[0], org, json.dumps({"text": content})))
        for title, body in ((f"Gen đề xuất ghi {old}", f"Ghi Phiên vào {old}."), ("Sao lưu xong", "Không liên quan")):
            c.execute("INSERT INTO core.notifications (org_id, user_id, kind, title, body) "
                      "VALUES (%s, %s, 'x', %s, %s)", (org, uid, title, body))

        def dump() -> list[Any]:
            m = c.execute("SELECT content FROM agent.gen_messages ORDER BY content").fetchall()
            n = c.execute("SELECT title, body FROM core.notifications ORDER BY title").fetchall()
            return [m, n]

        c.execute(sql)  # type: ignore[call-overload]
        first = dump()
        flat = " ".join(str(x) for part in first for row in part for x in row)
        assert OLD_NAME.lower() not in flat.lower()
        assert "Kho dữ liệu" in flat and "Không liên quan" in flat
        c.execute(sql)  # type: ignore[call-overload]
        assert dump() == first


async def test_qd22_migration_0041_only_rewrites_assistant_text_on_word_boundary(owner_api: Api, db: Any,
                                                                                 fresh_db: str) -> None:
    """Review P3: 0041 mục c chỉ sửa lời Gen (role='assistant') — lời Sếp gõ (role='user') nguyên văn; chỉ khớp cả từ
    ("Kho Ryanx", "xKho Ryan" giữ nguyên) nhưng vẫn khớp sau xuống dòng (JSON lưu "\\n" ngay trước chữ Kho)."""
    org = await org_id(db)
    uid = (await db.execute(text("SELECT id FROM core.users WHERE org_id = :o LIMIT 1"), {"o": org})).scalar_one()
    await db.rollback()
    old = f"Kho {OLD_NAME.removeprefix('Kho ')}"
    sql = SQL_0041.read_text(encoding="utf-8")
    rows = [("user", f"Sếp gõ: ghi vào {old} nhé"),
            ("assistant", f"Dòng một\n{old} là nơi lưu"),
            ("assistant", f"Không đụng {old}x hay x{old} hay {old}y"),
            ("assistant", f"Đã ghi vào {old.upper()}.")]
    with psycopg.connect(f"{PG}/{fresh_db}", autocommit=True) as c:
        conv = c.execute("""INSERT INTO agent.gen_conversations (org_id, user_id, title) VALUES (%s, %s, 'T')
                            RETURNING id""", (org, uid)).fetchone()
        assert conv is not None
        for role, content in rows:
            c.execute("INSERT INTO agent.gen_messages (conversation_id, org_id, role, content) "
                      "VALUES (%s, %s, %s, %s::jsonb)", (conv[0], org, role, json.dumps({"text": content})))
        for _ in range(2):  # chạy lại không đổi thêm
            c.execute(sql)  # type: ignore[call-overload]
        got = [(r[0], r[1]["text"]) for r in c.execute(
            "SELECT role, content FROM agent.gen_messages ORDER BY created_at, content::text").fetchall()]
    assert sorted(got) == sorted([
        ("user", rows[0][1]),
        ("assistant", "Dòng một\nKho dữ liệu là nơi lưu"),
        ("assistant", rows[2][1]),
        ("assistant", "Đã ghi vào Kho dữ liệu."),
    ])


def test_qd22_no_old_name_in_runtime_strings_or_registry() -> None:
    """QD-22: chuỗi chạy thật + registry sinh ra không chứa tên riêng cũ (bổ sung test_no_personal_info_v0156)."""
    for rel in ("gen/registry.json", "gen/coach/content/lessons.json", "gen/coach/content/tips.json"):
        assert OLD_NAME not in (GH_DIR / rel).read_text(encoding="utf-8"), rel
    assert OLD_NAME not in hub.SERVER_NOTE


# ─── v0.1.55 · "gọn" ──────────────────────────────────────────────────────────────────────────────────────────

def test_v0155_only_one_required_boss_row_and_readme_says_so() -> None:
    """v0.1.55: chỉ dòng 0 (nguồn AI) bắt buộc; README nói đúng điều đó."""
    assert boss_service.REQUIRED_TOTAL == 1
    required = [r["key"] for r in boss_service.ROWS if not r["optional"]]
    assert required == ["ai"]
    assert "chỉ 1 dòng bắt buộc" in (ROOT / "README.md").read_text(encoding="utf-8")


def test_v0155_agy_row_target_does_not_promise_switching_two_accounts() -> None:
    """v0.1.55 (F033): Sếp đã bỏ bài "đổi qua lại hai tài khoản" — mô tả mục tiêu Gen của dòng Google bám đúng."""
    reg = json.loads((GH_DIR / "gen" / "registry.json").read_text(encoding="utf-8"))
    agy = next(t for t in reg["targets"] if t["id"] == "boss_checks.row.agy")
    assert agy["description"] == "Dòng 3 của Việc Sếp cần làm: đăng nhập tài khoản Google rồi bấm Gọi thử"
    texts = [t["description"] for t in reg["targets"]] + [s for g in reg["guide"] for s in g["steps"]]
    assert not [t for t in texts if "đổi qua lại" in t and "hai tài khoản" in t]
    ts = (ROOT / "packages" / "contracts" / "src" / "genTargets.ts").read_text(encoding="utf-8")
    assert "đổi qua lại" not in ts


# ─── 16/10 · Q1 agy: core/nội bộ mở, ngoại giao qua hộp riêng ─────────────────────────────────────────────────

def test_q1_agy_core_in_process_outer_layer_through_the_box() -> None:
    """16/10 Q1 — chính sách CUỐI (v0.1.63, `placement`): core.gen của Sếp và agent nội bộ chạy agy TRONG TIẾN TRÌNH;
    core.reply, sàng lọc tin người lạ, Bản tin (prompt có tên khách — review v0.1.63 P2-3), agent ngoại giao, Gen của
    nhân viên, thử agent, Soạn lại / Dịch nháp chạy QUA HỘP RIÊNG (hộp không `ok` ⇒ KHÔNG dùng agy,
    không rơi về trong tiến trình); `rule_test` không dùng agy."""
    d = agy_policy.decide
    ok, down = agy_policy.BOX_OK, agy_policy.BOX_DOWN
    internal = "agent:11111111-1111-1111-1111-111111111111"
    for verdict in (d("core.gen", owner=True, box=down), d(internal, diplomatic=False, box=down)):
        assert verdict.allowed and verdict.where == agy_policy.INPROC
    outer: list[Any] = [
        dict(agent_key="core.reply"), dict(agent_key="core.refinery"), dict(agent_key="core.briefing"),
        dict(agent_key=internal, diplomatic=True),
        dict(agent_key="core.gen", owner=False), dict(agent_key="core.gen", purpose="setup_agent_try"),
        dict(agent_key="core.gen", purpose="draft_translate"), dict(agent_key="core.gen", purpose="draft_regenerate"),
    ]
    for kw in outer:
        assert d(box=ok, **kw).allowed and d(box=ok, **kw).where == agy_policy.BOX, kw
        gone = d(box=down, **kw)
        assert not gone.allowed and gone.code == agy_policy.CODE_BOX_DOWN and gone.where == agy_policy.BOX, kw
        assert d(box=agy_policy.BOX_NOT_INSTALLED, **kw).code == agy_policy.CODE_BOX_NOT_INSTALLED, kw
    rule_test = d("core.gen", purpose="rule_test", box=ok)
    assert not rule_test.allowed and rule_test.code == agy_policy.CODE_UNTRUSTED_TEXT
    assert "hộp riêng" in agy_policy.REASON_BOX_DOWN.lower()


# ─── 16/10 · Telegram chỉ đề xuất ─────────────────────────────────────────────────────────────────────────────

_TELEGRAM_FORBIDDEN = ("write_kho", "kho_write", "confirm_proposal", "approve_draft", "verify_pin", "require_pin",
                       "mcp_write", "send_draft")


def test_telegram_modules_cannot_execute_writes() -> None:
    """16/10: Telegram chỉ ĐỀ XUẤT — không module nào của gh/telegram import/gọi đường ghi Kho, duyệt nháp, kiểm mã PIN
    (PIN không đi qua bên thứ ba). Quét mọi tệp trong gh/telegram bằng cây cú pháp: tên import, tên hàm/thuộc tính và
    chuỗi đối số TRONG LỜI GỌI (docstring/chú thích nói «không có đường ghi» không bị tính). Ngoại lệ DUY NHẤT:
    `routes.py` (API cấu hình của Console, người dùng đã đăng nhập) được đòi mã PIN để đổi cấu hình Telegram."""
    bad: list[str] = []
    for path in sorted((GH_DIR / "telegram").rglob("*.py")):
        banned = tuple(w for w in _TELEGRAM_FORBIDDEN if not (path.name == "routes.py" and w == "require_pin"))
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or "", *(a.name for a in node.names)]
            elif isinstance(node, ast.Call):
                for sub in ast.walk(node):
                    if isinstance(sub, ast.Name):
                        names.append(sub.id)
                    elif isinstance(sub, ast.Attribute):
                        names.append(sub.attr)
                    elif isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                        names.append(sub.value)
            bad += [f"{path.relative_to(ROOT)}:{node.lineno}: {x}" for x in banned if any(x in n for n in names)]
    assert bad == []


# ─── 16/10 · Q5 / Q6 danh bạ & chế độ nghe ───────────────────────────────────────────────────────────────────

def test_q5_group_members_are_collected_for_every_group_including_off() -> None:
    """16/10 Q5 (=2): thu thành viên MỌI nhóm, kể cả "Không nghe" — nhập danh sách nhóm không xét `listen_mode`."""
    src = (GH_DIR / "data" / "ingest.py").read_text(encoding="utf-8")
    block = src[src.index("async def handle_directory"): src.index("async def handle_group_discovered")]
    # Chế độ nghe chỉ được xét ở MỘT chỗ: có ghi sự kiện «thành viên đổi» vào Nhật ký lắng nghe hay không (v0.1.68);
    # việc THU thành viên (`_member`) không phụ thuộc chế độ nghe.
    gated = [ln.strip() for ln in block.splitlines() if "listen_mode" in ln]
    assert all("joined or gone" in ln and "LISTENING_MODES" in ln for ln in gated), gated
    assert "for m in members:" in block and "_member(db, row.id, ident.person_id" in block


def test_q6_tagged_only_group_messages_are_still_stored() -> None:
    """16/10 Q6 (=1): "chỉ khi được tag" chỉ đổi cách TRẢ LỜI — tin của nhóm vẫn được lưu và phân tích (chỉ sửa chữ)."""
    from gh.data import common

    assert "tagged_only" in common.LISTENING_MODES and "silent" in common.LISTENING_MODES
    assert "off" not in common.LISTENING_MODES and "paused" not in common.LISTENING_MODES
    # `pending` (chưa lưu tin) chỉ còn khi Sếp đặt «Nhóm mới: Hỏi Sếp» — nhóm mới mặc định vào `silent` (QD 17/10).
    assert "pending" not in common.LISTENING_MODES
    # Chữ trên giao diện: chế độ này nói đúng «nghe hết» (vẫn lưu mọi tin), chỉ việc trả lời cần được tag.
    label = "Nghe hết, chỉ trả lời khi được tag"
    web = ROOT / "apps" / "web" / "src"
    for rel in ("screens/relations/relationsModel.ts", "screens/system/systemModel.ts", "setup/steps.ts"):
        assert label in (web / rel).read_text(encoding="utf-8"), rel


# ─── 16/10 · Q4 / Q7 ──────────────────────────────────────────────────────────────────────────────────────────

def test_q4_manager_promise_matches_reality_no_team_feature() -> None:
    """16/10 Q4 (=2): chưa có tính năng Nhóm ⇒ vai Quản lý KHÔNG hứa "thấy team mình" (xem test_team_scope_v016x)."""
    from gh.auth import rbac

    manager = next(rd for rd in rbac.ROLES if rd.code == rbac.MANAGER)
    assert manager.meta == "thấy việc được giao"
    users_model = (ROOT / "apps" / "web" / "src" / "screens" / "system" / "usersModel.ts").read_text(encoding="utf-8")
    assert "thấy việc được giao" in users_model and "thấy team mình" not in users_model


def test_q7_jev_stays_enabled_as_decided() -> None:
    """16/10 Q7 (=1): giữ Jev như hiện tại — lọc trước vẫn bật mặc định (mở băng QD-10/QD-13 được ghi nhận)."""
    from gh.refinery import triage

    assert triage.DEFAULTS["enabled"] is True


# ─── 17/10 · nhóm mới Lắng nghe im lặng, không bao giờ trả lời kể cả khi được tag ─────────────────────────────

def test_qd1710_new_group_default_is_silent_and_choices_remain() -> None:
    """17/10 (thay «hỏi khi kết nối kênh»): add vào một group ⇒ MẶC ĐỊNH lắng nghe im lặng, mọi bản cài kể cả cũ
    (không có khoá `new_group_mode`); Sếp vẫn đổi được sang «Hỏi Sếp» (`ask` ⇒ pending) hoặc «Không nghe» (`off`)."""
    assert DEFAULT_NEW_GROUP_MODE == "silent" and NEW_GROUP_MODES == ("ask", "silent", "off")
    assert new_group_mode(None, "zalo") == "silent" and new_group_mode({}, "zalo") == "silent"
    assert initial_group_mode({}, "zalo") == "silent"
    assert initial_group_mode({"new_group_mode": {"zalo": "ask"}}, "zalo") == "pending"
    assert initial_group_mode({"new_group_mode": {"zalo": "off"}}, "zalo") == "off"


async def test_qd1710_ingest_new_group_stores_silently_even_when_first_message_tags(app, db, redis) -> None:  # type: ignore[no-untyped-def]
    """17/10: nhóm lạ đầu tiên nhắn (kể cả tag) vào thẳng `silent`: lưu tin, không đi đường nhanh (không ai trả lời)."""
    org = await org_id(db)
    [eid] = await put(sessionmaker(), org, msg("Em hỏi giá lô này ạ", group="gQ", mentions=True))
    assert eid is not None
    mode = (await db.execute(text("SELECT listen_mode FROM core.groups WHERE external_id = 'gQ'"))).scalar_one()
    assert mode == "silent"
    assert (await db.execute(text("SELECT fast FROM refinery.event_state"))).scalar_one() is False


def test_qd1710_allowed_decisions_in_silent_group_are_silent_and_note_at_every_level() -> None:
    """17/10: nhóm silent ⇒ agent trực chỉ được `silent`/`note`, ở mọi mức tự trị; nhóm khác như cũ."""
    for level in range(0, 7):
        assert duty_engine.allowed_decisions(level, replies=False) == ["silent", "note"]
    assert duty_engine.allowed_decisions(4) == list(duty_engine.DECISIONS)
    assert duty_engine.allowed_decisions(3, replies=True) == ["silent", "note", "suggest"]


@pytest.mark.parametrize("tagged", [False, True])
@pytest.mark.parametrize("choice", ["suggest", "draft", "send"])
async def test_qd1710_silent_group_never_replies_even_when_tagged(w, db, redis, choice: str, tagged: bool) -> None:  # type: ignore[no-untyped-def]
    """17/10 (+ mặc định #22): nhóm silent (kể cả được tag) không vào ứng viên agent trực ⇒ không gọi model, không quyết
    định, không nháp, không tin ra ngoài (sàng lọc vẫn tạo đơn vị ý nghĩa)."""
    a = await make_agent(db, w, level=6)
    await db.execute(text("UPDATE core.groups SET listen_mode = 'silent' WHERE id = :g"), {"g": w["g1"]})
    await db.commit()
    [u] = await refine(w, redis, msg(BUY, mentions=tagged))
    router = decide(choice)
    await hook(w, redis, router, [u])
    assert router.calls == [] and await decisions(db, a) == []
    assert await agent_drafts(db) == [] and await redis.xlen("gh.bridge.outbound") == 0


async def test_qd1710_tagged_only_group_still_replies_when_tagged(w, db, redis) -> None:  # type: ignore[no-untyped-def]
    """17/10 (đối chiếu Q6): «Nghe hết, chỉ trả lời khi được tag» vẫn trả lời khi được tag — khác `silent`."""
    a = await make_agent(db, w, level=4)
    await db.execute(text("UPDATE core.groups SET listen_mode = 'tagged_only' WHERE id = :g"), {"g": w["g1"]})
    await db.commit()
    plain, tagged = await refine(w, redis, msg(BUY), msg(BUY, sender="u2", mentions=True))
    await hook(w, redis, decide("draft"), [plain, tagged])
    [d] = await decisions(db, a)
    assert d.trigger_unit_id == tagged and (d.decision, d.outcome) == ("draft", "held") and d.draft_id is not None
