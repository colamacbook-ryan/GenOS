"""v0.1.69 (D5; F062): MỘT bảng nhãn loại sự kiện (15 loại, tiếng Việt) cho mọi màn — nguồn `core.lookup`.

Trước đây mỗi nơi tự giữ một bảng: máy chủ 9/15 loại ("Than phiền"), Mặt tiền thêm nhãn riêng, web 3 bảng (8 loại), Kho
thô / Kho sạch / Hồ sơ / Bằng chứng in thẳng mã tiếng Anh (`JobSignal`, `AskedStatus`…). Giờ: bảng ở máy chủ
(`gh.biz.queue.service.EVENT_LABELS`) PHẢI bằng `core.lookup` (test dưới đây), Mặt tiền dùng đúng bảng đó, bảng của web
(`packages/contracts/src/eventTypes.ts`) PHẢI bằng bảng máy chủ — lệch một chữ là CI đỏ.
"""

import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from sqlalchemy import text

from gh.biz.queue import routes as queue_routes
from gh.biz.queue import service as qsvc
from gh.owner import feed
from tests.conftest import Api

ROOT = Path(__file__).resolve().parents[3]
EVENT_TYPES_TS = ROOT / "packages" / "contracts" / "src" / "eventTypes.ts"


async def _lookup(db: Any) -> dict[str, str]:
    rows = (await db.execute(text("SELECT code, label_vi FROM core.lookup WHERE kind = 'event_type'"))).all()
    return {r.code: r.label_vi for r in rows}


async def test_server_table_covers_all_15_types_and_equals_core_lookup(owner_api: Api, db: Any) -> None:
    lookup = await _lookup(db)
    assert len(lookup) == 15
    assert qsvc.EVENT_LABELS == lookup
    for code, label in qsvc.EVENT_LABELS.items():
        assert label and label != code and not re.fullmatch(r"[A-Za-z]+", label), code


async def test_one_word_for_complaint_and_no_second_table(owner_api: Api, db: Any) -> None:
    """"Phàn nàn" (core.lookup) — không còn "Than phiền" cho cùng một loại; Mặt tiền không giữ bảng riêng."""
    assert qsvc.EVENT_LABELS["Complained"] == "Phàn nàn"
    assert "Than phiền" not in qsvc.EVENT_LABELS.values()
    assert feed.EVENT_TITLES == qsvc.EVENT_LABELS
    # loại lạ ⇒ "Tin khác", không lộ mã tiếng Anh
    assert feed.EVENT_TITLES.get("Greeting", feed.OTHER_TITLE) == feed.OTHER_TITLE


def _row(event_type: str) -> Any:
    return SimpleNamespace(item_id="00000000-0000-0000-0000-000000000001", code="UNIT-1", item_type="unit", tab="other",
                           title=event_type, summary="s", priority="P2", created_at=None, score=None,
                           draft_agent_id=None, draft_agent_name=None, alert_type=None, suggested_action=None,
                           subject_type=None, subject_id=None, group_id=None, group_name=None, group_code=None,
                           person_id=None, person_name=None, person_code=None, t_spam=False, t_spam_reason=None,
                           t_quality=100, t_reason=None, t_source=None)


def test_inbox_card_title_is_vietnamese_for_every_type() -> None:
    """Tiêu đề thẻ Hộp thư cho cả 15 loại là nhãn tiếng Việt từ bảng chung (trước: 6 loại hiện nguyên `JobSignal`…)."""
    for code, label in qsvc.EVENT_LABELS.items():
        got = queue_routes._item_payload(_row(code), owner=True)["title"]
        assert got == label, code
        assert not re.fullmatch(r"[A-Za-z]+", got)


def test_web_table_equals_server_table() -> None:
    src = EVENT_TYPES_TS.read_text(encoding="utf-8")
    pairs = dict(re.findall(r"\{ code: '([A-Za-z]+)', label: '([^']+)' \}", src))
    assert pairs == qsvc.EVENT_LABELS
    order = re.findall(r"\{ code: '([A-Za-z]+)', label: '", src)
    assert order == list(qsvc.EVENT_LABELS)          # cùng thứ tự với core.lookup.sort_order (bộ lọc "Ý định")
