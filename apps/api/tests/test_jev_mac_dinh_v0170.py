"""v0.1.70 (E3, F127) — QD Q7 (Sếp chốt 16/10/2026): GIỮ Jev. Khoá trạng thái đã chốt bằng test (lưới L6):

- Lọc tin trước khi trích xuất (`triage.prefilter`), `use_jev` và ngưỡng lọc giữ NGUYÊN mặc định của v0.1.55. Muốn tắt
  mặc định: sửa DEFAULTS ở `refinery/triage.py` (không phải prefilter.py), cập nhật test này cùng PR;
- Jev KHÔNG tự bật: tổ chức mới chưa có nguồn `system_one` ⇒ `jev_on = false`; chỉ nút "Bật Jev" (cần mã PIN) tạo nguồn;
- Thẻ Jev luôn nằm trong "Nâng cao" của Bộ não AI (v0.1.43), không bắt buộc.
Không đổi mã chạy — chỉ ghi lại quyết định bằng khẳng định.
"""

import re
from pathlib import Path
from typing import Any

from sqlalchemy import text

from gh.refinery import triage
from tests.conftest import Api
from tests.phase2 import org_id

REPO = Path(__file__).resolve().parents[3]


def test_q7_triage_defaults_keep_jev_and_prefilter() -> None:
    assert triage.DEFAULTS == {"enabled": True, "min_score": 30, "use_jev": True, "prefilter": True}


async def test_q7_new_org_has_no_jev_source_until_boss_enables(owner_api: Api, db: Any) -> None:
    org = await org_id(db)
    got = await triage.value_summary(db, org)
    assert got["jev_on"] is False
    n = (await db.execute(text("SELECT count(*) FROM agent.providers WHERE org_id = :o AND kind = 'system_one'"),
                          {"o": org})).scalar_one()
    assert n == 0
    r = await owner_api.get("/jev/value-summary")
    assert r.status_code == 200 and r.json()["jev_on"] is False


def test_q7_jev_card_stays_inside_advanced_section() -> None:
    src = (REPO / "apps/web/src/screens/system/BrainTab.tsx").read_text(encoding="utf-8")
    m = re.search(r'<details className="brain-advanced" id="jev"[^>]*>(.*?)</details>', src, re.S)
    assert m is not None, 'thẻ Jev phải nằm trong <details id="jev"> "Nâng cao"'
    assert "Nâng cao" in m.group(1) and "jevCard" in m.group(1)
    # chỉ một chỗ vẽ thẻ, và chỗ đó nằm trong <details>
    assert src.count("{jevCard}") == 1 and src.count("<JevCard") == 1
