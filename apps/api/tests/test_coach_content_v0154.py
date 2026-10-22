"""Nội dung Gen hướng dẫn (v0.1.54, g3-noi-dung): 10 bài N01–N10 (`content/lessons.json`) + 6 mẹo (`content/tips.json`).

Pytest thuần — không CSDL, không Redis, không model. Đọc THẲNG tệp thật (không nội dung giả) và đối chiếu với:
- `gh.gen.coach.signals` (từ vựng tín hiệu `STATE_SIGNALS`, chủ đề `TOPICS`) — gói api-coach;
- `gh.gen.coach.lessons` (bộ nạp, 9 bài G05..G14 sinh từ registry, lộ trình 19 bài) — gói api-coach;
- `gh.gen.registry` + `registry.json` (mục tiêu làm sáng `resolve_target`) — gói web-coach.

KHÔNG có test nào ở đây theo `VERSION`; sửa chữ trong bài thì chỉ cần giữ đúng luật (3–5 câu, ≤ 600 ký tự…).
Bảng `LESSON_DESIGN` / `TIP_DESIGN` khoá đúng thiết kế (đích, điều kiện mở khoá, tín hiệu xong, thứ tự):
đổi thiết kế thì sửa bảng này có chủ ý, không phải lỡ tay.
"""

import json
import re
import unicodedata
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from gh.gen import registry
from gh.gen.coach import lessons, signals

CONTENT_DIR = Path(lessons.__file__).resolve().parent / "content"
LESSON_BODY_MAX = 600
TIP_BODY_MAX = 300
TITLE_MAX = 120
LABEL_MAX = 40

#: Thiết kế chốt của 10 bài: order (xen giữa các bài guide n × 10 = 50..110, 130, 140), đích "Làm thử", mở khoá, xong.
LESSON_DESIGN: dict[str, dict[str, Any]] = {
    "N01": {"order": 10, "target": "help.ask_gen"},
    "N02": {"order": 20, "target": "account.pin", "done": "pin.set"},
    "N03": {"order": 55, "target": "system.channels.telegram", "unlock": ["boss.telegram.done"],
            "done": "telegram.briefing_on"},
    "N04": {"order": 65, "target": "workbench.drafts"},
    "N05": {"order": 75, "target": "system.brain.memory", "unlock": ["model.bound"]},
    "N06": {"order": 145, "target": "boss_checks.row.kho_write", "unlock": ["boss.hub.done"],
            "done": "boss.kho_write.done"},
    "N07": {"order": 85, "target": "overview.needs_boss"},
    "N08": {"order": 95, "target": "help.genh"},
    "N09": {"order": 135, "target": "mcp.hub_link.token", "unlock": ["boss.hub.done"]},
    "N10": {"order": 115, "target": "system.offsite.choose", "unlock": ["backup.scheduled"], "done": "offsite.chosen"},
}
TIP_DESIGN: dict[str, dict[str, Any]] = {
    "telegram_briefing": {"topic": "telegram", "when": ["boss.telegram.done", "!telegram.briefing_on"],
                          "target": "system.channels.telegram"},
    "hub_kho_write": {"topic": "hub", "when": ["boss.hub.done", "hub.kho_write_missing"],
                      "target": "boss_checks.row.kho_write"},
    "memory_empty": {"topic": "memory", "when": ["model.bound", "memory.empty"], "target": "system.brain.memory"},
    "ai_budget": {"topic": "ai_cost", "when": ["api_key.present", "!ai_budget.set"], "target": "system.ai_cost"},
    "facebook_reply": {"topic": "facebook", "when": ["boss.facebook.done", "!boss.facebook_reply.done"],
                       "target": "boss_checks.row.facebook_reply"},
    "offsite_unset": {"topic": "offsite", "when": ["backup.scheduled", "!offsite.chosen"],
                      "target": "system.offsite.choose"},
}
#: Thứ tự lộ trình 19 bài (N xen G theo `order`) — k = vị trí 1..19.
CURRICULUM_IDS = ["N01", "N02", "G05", "N03", "G06", "N04", "G07", "N05", "G08", "N07", "G09", "N08", "G10", "G11",
                  "N10", "G13", "N09", "G14", "N06"]
GUIDE_NS = (5, 6, 7, 8, 9, 10, 11, 13, 14)

VI_LETTERS = set("ăâđêôơưàáảãạằắẳẵặầấẩẫậèéẻẽẹềếểễệìíỉĩịòóỏõọồốổỗộờớởỡợùúủũụừứửữựỳýỷỹỵ")
#: Thuật ngữ kỹ thuật không được lọt vào bài/mẹo. Tên Sếp thấy trên màn (genh, token, PIN, khoá API…) thì được.
JARGON = re.compile(r"\b(endpoint|json|rls|migration|cron|webhook|docker|systemd|payload|backend|frontend|sql)\b",
                    re.IGNORECASE)
WRONG_ADDRESS = re.compile(r"\b(tôi|bạn|mình|boss)\b", re.IGNORECASE)
#: Nhãn có sẵn trên màn (chép nguyên văn) được phép chứa chữ 'tôi'.
SCREEN_LABELS = ("Tài khoản của tôi",)


@pytest.fixture(autouse=True)
def _real_content() -> Iterator[None]:
    """Bộ nạp có lru_cache: xoá trước/sau để test đọc đúng tệp thật, không dính nội dung giả của test khác."""
    assert Path(lessons.CONTENT_DIR).resolve() == CONTENT_DIR, "CONTENT_DIR không trỏ vào content/ thật"
    lessons.load_lessons.cache_clear()
    lessons.load_tips.cache_clear()
    yield
    lessons.load_lessons.cache_clear()
    lessons.load_tips.cache_clear()


def _raw(name: str) -> list[Any]:
    data = json.loads((CONTENT_DIR / name).read_text(encoding="utf-8"))
    assert isinstance(data, list), f"{name}: gốc phải là một danh sách"
    return data


def raw_lessons() -> list[dict[str, Any]]:
    return _raw("lessons.json")


def raw_tips() -> list[dict[str, Any]]:
    return _raw("tips.json")


def sentences(text: str) -> int:
    """Số câu = số cụm dấu . ! ? đứng cuối câu (liền sau là khoảng trắng hoặc hết chuỗi); bỏ dấu nháy trước khi đếm."""
    return len(re.findall(r"[.!?]+(?=\s|$)", re.sub(r"[\"“”'‘’]", "", text)))


def names(expr: list[str]) -> list[str]:
    return [x.strip().lstrip("!").strip() for x in expr]


def all_items() -> list[tuple[str, dict[str, Any]]]:
    return [(x["id"], x) for x in raw_lessons()] + [(x["key"], x) for x in raw_tips()]


def check_try(where: str, t: Any) -> None:
    assert isinstance(t, dict), f"{where}: try phải là một đối tượng"
    assert set(t) == {"label", "target"}, f"{where}: try chỉ gồm label + target, đang có {sorted(t)}"
    for f in ("label", "target"):
        assert isinstance(t[f], str) and t[f].strip(), f"{where}: try.{f} phải là chuỗi không rỗng"
    assert len(t["label"]) <= LABEL_MAX, f"{where}: try.label dài quá {LABEL_MAX} ký tự"


# ═══ lessons.json ════════════════════════════════════════════════════════════════════════════════════════════════

def test_lessons_dung_10_bai_N01_den_N10() -> None:
    rows = raw_lessons()
    assert len(rows) == 10
    assert [x["id"] for x in rows] == [f"N{i:02d}" for i in range(1, 11)]
    assert set(LESSON_DESIGN) == {x["id"] for x in rows}


def test_lessons_schema_tung_truong_va_khong_khoa_thua() -> None:
    required, optional = {"id", "order", "title", "body"}, {"try", "unlock", "done_signal"}
    for i, x in enumerate(raw_lessons()):
        where = f"lessons.json[{i}]"
        assert isinstance(x, dict), f"{where}: phải là đối tượng"
        keys = set(x)
        assert required <= keys, f"{where}: thiếu {sorted(required - keys)}"
        assert keys <= required | optional, f"{where}: khoá thừa {sorted(keys - required - optional)}"
        assert isinstance(x["id"], str) and re.fullmatch(r"N(0[1-9]|10)", x["id"]), f"{where}: id sai"
        assert type(x["order"]) is int and 1 <= x["order"] <= 10000, f"{where}: order phải là số nguyên 1..10000"
        assert isinstance(x["title"], str) and x["title"].strip() and len(x["title"]) <= TITLE_MAX, f"{where}: title"
        assert isinstance(x["body"], str) and x["body"].strip(), f"{where}: body phải là chuỗi không rỗng"
        if "try" in x:
            check_try(where, x["try"])
        if "unlock" in x:
            assert isinstance(x["unlock"], list) and x["unlock"], f"{where}: unlock phải là danh sách không rỗng"
            assert all(isinstance(e, str) and e.strip() for e in x["unlock"]), f"{where}: unlock chỉ gồm chuỗi"
        if "done_signal" in x:
            assert isinstance(x["done_signal"], str) and x["done_signal"].strip(), f"{where}: done_signal"
            assert not x["done_signal"].startswith("!"), f"{where}: done_signal không được phủ định"


def test_lessons_id_va_order_duy_nhat_khong_trung_bai_guide() -> None:
    rows = raw_lessons()
    assert len({x["id"] for x in rows}) == 10
    assert len({x["order"] for x in rows}) == 10, "order của 10 bài phải khác nhau"
    guide_orders = {g["order"] for g in lessons.guide_lessons()}
    assert guide_orders == {n * 10 for n in GUIDE_NS}
    assert not guide_orders & {x["order"] for x in rows}, "order của bài N trùng order của bài guide"
    assert len({x["title"] for x in rows}) == 10, "tiêu đề các bài phải khác nhau"


def test_lessons_dung_thiet_ke_order_dich_mo_khoa_va_tin_hieu_xong() -> None:
    for x in raw_lessons():
        d = LESSON_DESIGN[x["id"]]
        assert x["order"] == d["order"], f"{x['id']}: order"
        assert x["try"]["target"] == d["target"], f"{x['id']}: đích Làm thử"
        assert x["try"]["label"] == "Làm thử", f"{x['id']}: nhãn nút của bài học là 'Làm thử'"
        assert x.get("unlock", []) == d.get("unlock", []), f"{x['id']}: unlock"
        assert x.get("done_signal") == d.get("done"), f"{x['id']}: done_signal"


@pytest.mark.parametrize("lesson_id", sorted(LESSON_DESIGN))
def test_lessons_body_3_den_5_cau_toi_da_600_ky_tu(lesson_id: str) -> None:
    lesson = next(x for x in raw_lessons() if x["id"] == lesson_id)
    body = lesson["body"]
    assert len(body) <= LESSON_BODY_MAX, f"{lesson['id']}: {len(body)} ký tự, tối đa {LESSON_BODY_MAX}"
    n = sentences(body)
    assert 3 <= n <= 5, f"{lesson['id']}: {n} câu, phải 3–5"
    # Mỗi dấu kết câu đứng đúng chỗ kết câu: không có dấu chấm/hỏi lạc giữa câu (số phiên bản, địa chỉ…).
    assert len(re.findall(r"[.!?]", body)) == n, f"{lesson['id']}: có dấu . ! ? không phải kết câu"
    assert body.rstrip()[-1] in ".!?”\"", f"{lesson['id']}: bài phải kết thúc bằng dấu câu"


def test_tips_body_1_den_3_cau_toi_da_300_ky_tu() -> None:
    for t in raw_tips():
        body = t["body"]
        assert len(body) <= TIP_BODY_MAX, f"{t['key']}: {len(body)} ký tự, tối đa {TIP_BODY_MAX}"
        n = sentences(body)
        assert 1 <= n <= 3, f"{t['key']}: {n} câu, phải 1–3"
        assert len(re.findall(r"[.!?]", body)) == n, f"{t['key']}: có dấu . ! ? không phải kết câu"


# ═══ tips.json ═══════════════════════════════════════════════════════════════════════════════════════════════════

def test_tips_dung_6_goi_y_theo_thiet_ke() -> None:
    rows = raw_tips()
    assert len(rows) == 6
    assert {t["key"] for t in rows} == set(TIP_DESIGN)
    assert len({t["key"] for t in rows}) == 6, "key của mẹo phải khác nhau"
    assert len({t["title"] for t in rows}) == 6, "tiêu đề các mẹo phải khác nhau"


def test_tips_schema_tung_truong_va_khong_khoa_thua() -> None:
    required, optional = {"key", "topic", "when", "title", "body"}, {"try"}
    for i, t in enumerate(raw_tips()):
        where = f"tips.json[{i}]"
        assert isinstance(t, dict), f"{where}: phải là đối tượng"
        keys = set(t)
        assert required <= keys, f"{where}: thiếu {sorted(required - keys)}"
        assert keys <= required | optional, f"{where}: khoá thừa {sorted(keys - required - optional)}"
        assert isinstance(t["key"], str) and re.fullmatch(r"[a-z0-9][a-z0-9_.-]{1,63}", t["key"]), f"{where}: key"
        assert isinstance(t["topic"], str), f"{where}: topic phải là chuỗi"
        assert isinstance(t["when"], list) and t["when"], f"{where}: when phải là danh sách không rỗng"
        assert all(isinstance(e, str) and e.strip() for e in t["when"]), f"{where}: when chỉ gồm chuỗi"
        assert isinstance(t["title"], str) and t["title"].strip() and len(t["title"]) <= TITLE_MAX, f"{where}: title"
        assert isinstance(t["body"], str) and t["body"].strip(), f"{where}: body phải là chuỗi không rỗng"
        check_try(where, t["try"])        # cả 6 mẹo đều có nút "Thử ngay"


def test_tips_dung_thiet_ke_chu_de_dieu_kien_dich() -> None:
    for t in raw_tips():
        d = TIP_DESIGN[t["key"]]
        assert t["topic"] == d["topic"], f"{t['key']}: topic"
        assert t["when"] == d["when"], f"{t['key']}: when"
        assert t["try"] == {"label": "Thử ngay", "target": d["target"]}, f"{t['key']}: try"


def test_tips_topic_thuoc_tu_vung_chu_de() -> None:
    for t in raw_tips():
        assert t["topic"] in signals.TOPICS, f"{t['key']}: topic '{t['topic']}' không thuộc {sorted(signals.TOPICS)}"


# ═══ tín hiệu, đích làm sáng, chữ ════════════════════════════════════════════════════════════════════════════════

def test_unlock_done_signal_when_deu_la_tin_hieu_trang_thai() -> None:
    for item_id, x in all_items():
        for field in ("unlock", "when"):
            for name in names(x.get(field, [])):
                assert name in signals.STATE_SIGNALS, f"{item_id}: {field} dùng tín hiệu lạ '{name}'"
        if "done_signal" in x:
            assert x["done_signal"] in signals.STATE_SIGNALS, f"{item_id}: done_signal lạ '{x['done_signal']}'"


def test_dieu_kien_khong_tu_mau_thuan_va_tinh_duoc() -> None:
    every_true = dict.fromkeys(signals.STATE_SIGNALS, True)
    for item_id, x in all_items():
        for field in ("unlock", "when"):
            expr = x.get(field, [])
            pos = {n for e, n in zip(expr, names(expr), strict=True) if not e.strip().startswith("!")}
            neg = {n for e, n in zip(expr, names(expr), strict=True) if e.strip().startswith("!")}
            assert not pos & neg, f"{item_id}: {field} vừa đòi vừa cấm cùng tín hiệu {sorted(pos & neg)}"
            assert len(expr) == len(set(expr)), f"{item_id}: {field} có biểu thức lặp"
            # Có ít nhất một trạng thái làm biểu thức đúng: đặt đúng theo từng tên rồi tính bằng bộ tính của hệ thống.
            state = dict(every_true)
            state.update({n: False for n in neg})
            assert lessons.eval_cond(expr, state), f"{item_id}: {field} không thể đúng"


def test_moi_try_target_resolve_duoc_trong_registry() -> None:
    reg = registry.load()
    for item_id, x in all_items():
        target = x["try"]["target"]
        t = registry.resolve_target(target)
        assert t is not None, f"{item_id}: đích '{target}' không có trong registry.json (gói web-coach)"
        assert registry.screen_exists(t.screen), f"{item_id}: màn '{t.screen}' của đích '{target}' không có"
        assert t.screen in reg.screens


def test_khong_chua_dau_bao_ve_khung_nhap_cua_model() -> None:
    for item_id, x in all_items():
        for f in ("title", "body"):
            assert "<<<" not in x[f] and ">>>" not in x[f], f"{item_id}: {f} chứa <<< hoặc >>>"
        label = x["try"]["label"]
        assert "<<<" not in label and ">>>" not in label, f"{item_id}: try.label chứa <<< hoặc >>>"


def test_giong_van_tieng_viet_co_dau_em_goi_sep_khong_thuat_ngu() -> None:
    for item_id, x in all_items():
        text = f"{x['title']} {x['body']}"
        assert VI_LETTERS & set(unicodedata.normalize("NFC", text).lower()), f"{item_id}: phải là tiếng Việt có dấu"
        assert "Sếp" in x["body"], f"{item_id}: body phải gọi người đọc là Sếp"
        assert re.search(r"\bem\b", x["body"], re.IGNORECASE), f"{item_id}: body phải xưng 'em' (Gen)"
        plain = text
        for label in SCREEN_LABELS:
            plain = plain.replace(label, "")
        wrong, jargon = WRONG_ADDRESS.search(plain), JARGON.search(plain)
        assert wrong is None, f"{item_id}: dùng xưng hô lạ '{wrong and wrong.group(0)}'"
        assert jargon is None, f"{item_id}: có thuật ngữ kỹ thuật '{jargon and jargon.group(0)}'"
        assert "…" not in text and "..." not in text, f"{item_id}: không dùng dấu ba chấm"


# ═══ bộ nạp + lộ trình ═══════════════════════════════════════════════════════════════════════════════════════════

def test_load_lessons_va_load_tips_doc_duoc_tep_that() -> None:
    ls, ts = lessons.load_lessons(), lessons.load_tips()
    assert [x["id"] for x in ls] == [x["id"] for x in raw_lessons()]
    assert [x["key"] for x in ts] == [x["key"] for x in raw_tips()]      # giữ nguyên thứ tự trong tệp
    assert len(ls) == 10 and len(ts) == 6
    for loaded, raw in zip(ls, raw_lessons(), strict=True):
        assert loaded["body"] == raw["body"] and loaded["order"] == raw["order"]
        assert loaded.get("try") == raw["try"]


def test_guide_lessons_sinh_du_9_bai_G05_den_G14() -> None:
    g = lessons.guide_lessons()
    assert [x["id"] for x in g] == ["G05", "G06", "G07", "G08", "G09", "G10", "G11", "G13", "G14"]
    for x in g:
        n = int(x["id"][1:])
        assert x["order"] == n * 10
        assert x["try"]["target"] == f"guide.item.do:{n}"
        assert registry.resolve_target(x["try"]["target"]) is not None
        assert x["done_signal"] == f"followup.{n}.done" and x["done_signal"] in signals.STATE_SIGNALS
        assert x["title"].strip() and x["body"].strip()


def test_curriculum_dung_19_bai_order_tang_dan_k_tu_1_den_19() -> None:
    curr = lessons.curriculum()
    assert len(curr) == 19
    assert [x["k"] for x in curr] == list(range(1, 20))
    orders = [x["order"] for x in curr]
    assert orders == sorted(orders) and len(set(orders)) == 19, "order phải tăng NGẶT dọc lộ trình"
    assert [x["id"] for x in curr] == CURRICULUM_IDS
    assert len({x["id"] for x in curr}) == 19
    for x in curr:
        if x.get("try"):
            assert registry.resolve_target(x["try"]["target"]) is not None, x["id"]
