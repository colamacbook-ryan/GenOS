"""Phân lớp dùng Antigravity CLI (`agy`) — MỘT điểm quyết định duy nhất (`decide`), quyết định của Sếp 16/10/2026.

v0.1.63 (gói H2) — kiến trúc hai chỗ chạy (luật chỗ chạy nằm ở `gh.providers.placement`, thuần; ở đây gộp với trạng
thái hộp để ra MỘT kết luận cho mỗi lượt):
- LỚP CORE (chỉ Sếp và hệ thống tham gia): `core.gen` của Sếp (Owner) và agent nội bộ (`agent:<id>` KHÔNG gắn kênh /
  nhóm / người) ⇒ agy chạy TRONG TIẾN TRÌNH (`inproc`).
- LỚP NGOẠI GIAO / người ngoài: `core.reply` (soạn lại, dịch nháp tin gửi khách), agent ngoại giao, sàng lọc tin người
  lạ `core.refinery`, Bản tin `core.briefing` (prompt có tên hiển thị khách, tiêu đề việc — review v0.1.63 P2-3), Gen
  của nhân viên (không phải Owner) và lượt thử agent ⇒ agy chạy QUA HỘP RIÊNG (`box`,
  `gh.agybox`): container không có khoá, DB, Redis — nội dung bên ngoài có lừa agy đọc tệp cũng không chạm bí mật.
- Hộp chưa cài / tắt ⇒ lượt thuộc vai `box` KHÔNG được dùng agy (`allowed=False`, KHÔNG rơi về agy trong tiến trình):
  bộ định tuyến gỡ Antigravity khỏi chuỗi và chuyển nguồn AI kế; `reason` là câu tiếng Việt trung thực hiển thị cho
  người dùng.

Tiêu chí "agent ngoại giao" (rõ ràng, kiểm được bằng SQL — `diplomatic_agent_ids`): agent là ngoại giao khi nó được GẮN
để nhận nội dung từ bên ngoài, tức là có ít nhất một trong ba:
  1. phạm vi nghe kênh / nhóm chat (`agent.channel_scopes`);
  2. được gán làm BOT của một nhóm (`core.groups.assigned_agent_id`);
  3. được gán làm BOT của một người (`core.persons.attrs->>'agent_id'`).
Đây đúng là các đường mà agent trực việc (`gh.biz.duty`) và bản nháp trả lời khách nhận tin khách, nên xét theo việc
agent CÓ THỂ đọc nội dung ngoài (không theo mẫu / mức tự trị). Agent chưa gắn kênh / nhóm / người nào chỉ nhận lời của
Sếp ⇒ nội bộ. Quyết định tính LÚC GỌI (không lưu): gắn kênh cho agent sau này thì dòng gán agy cũ của agent đó tự đổi
chỗ chạy.

Soạn lại / dịch nháp (purpose `draft_translate` / `draft_regenerate`) luôn QUA HỘP theo MỤC ĐÍCH, bất kể khoá của
nháp (`agent:<id>` có thể hết "ngoại giao" sau khi nháp được tạo — review lần 2 v0.1.62, nay chạy qua hộp: H2).
Thử quy tắc (purpose `rule_test`, `/rules/test`) KHÔNG dùng agy ở bất kỳ chỗ nào: văn bản tuỳ ý của cả vai chỉ đọc —
dùng khoá API (F2).

Hàm `decide` THUẦN (không DB, không mạng; trạng thái hộp do bên gọi truyền); các nơi dùng: `set_binding` (agents_api),
`ModelRouter.generate`, hồ sơ tiêu chuẩn (`gh.defaults.profiles.resolve` / `missing_reason`), ô chọn model của khung
chat Gen (`choice_options`) và danh sách gán model của web (`agy_blocked_reason`). Bên gọi có DB dùng `verdict_for` /
`verdicts_for` (tự tra agent ngoại giao + đọc `box_status()` đã cache 10 giây).
"""

import logging
import uuid
from dataclasses import dataclass

from sqlalchemy import Uuid, bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

from gh.agybox import client as box_client
from gh.providers import placement
from gh.providers.placement import BOX, INPROC

log = logging.getLogger("gh.providers")

KIND_AGY = "antigravity_cli"
AGENT_PREFIX = placement.AGENT_PREFIX

#: Trạng thái hộp (khớp `gh.agybox.client.box_status()["state"]`).
BOX_OK = "ok"
BOX_DOWN = "down"
BOX_NOT_INSTALLED = "not_installed"

CODE_BOX_DOWN = "box_down"
CODE_BOX_NOT_INSTALLED = "box_not_installed"
CODE_UNTRUSTED_TEXT = "untrusted_text"

#: Câu hiển thị cho người dùng (tiếng Việt có dấu, trung thực) — nguồn duy nhất.
REASON_BOX_DOWN = ("Hộp riêng Antigravity chưa chạy — việc có người ngoài (trả lời khách, sàng lọc tin, Gen của "
                   "nhân viên) tạm dùng nguồn AI kế, không dùng Antigravity trong tiến trình")
REASON_BOX_NOT_INSTALLED = ("Hộp riêng Antigravity chưa được cài — việc có người ngoài (trả lời khách, sàng lọc "
                            "tin, Gen của nhân viên) dùng nguồn AI kế; cập nhật Gen-Harness để có hộp")
REASON_UNTRUSTED_TEXT = "Antigravity không dùng để thử quy tắc trên văn bản tuỳ ý — việc này dùng khoá API"

#: Thử quy tắc (`/rules/test`): văn bản do người dùng (kể cả vai chỉ đọc như Auditor) gõ tuỳ ý ⇒ KHÔNG dùng agy, kể cả
#: qua hộp (F2, review lần 2 v0.1.62). Còn Soạn lại / Dịch nháp: qua hộp (`placement.BOX_PURPOSES`).
UNTRUSTED_TEXT_PURPOSES = frozenset({"rule_test"})


@dataclass(frozen=True)
class AgyVerdict:
    """Kết quả quyết định cho MỘT lượt gọi.

    - `where`: chỗ chạy agy theo `placement` ("inproc" | "box");
    - `allowed`: dùng được agy ở `where` ngay lúc này (vai `box` mà hộp không `ok` ⇒ False);
    - `code` / `reason`: khi không dùng được — `box_down` | `box_not_installed` và câu hiển thị."""

    allowed: bool = True
    code: str | None = None
    reason: str | None = None
    where: str = INPROC


ALLOWED = AgyVerdict()                                  # agy trong tiến trình
ALLOWED_BOX = AgyVerdict(where=BOX)                     # agy qua hộp, hộp đang chạy
DENIED_BOX_DOWN = AgyVerdict(False, CODE_BOX_DOWN, REASON_BOX_DOWN, BOX)
DENIED_BOX_NOT_INSTALLED = AgyVerdict(False, CODE_BOX_NOT_INSTALLED, REASON_BOX_NOT_INSTALLED, BOX)


def decide(agent_key: str, *, owner: bool = True, diplomatic: bool = False, box: str = BOX_NOT_INSTALLED,
           purpose: str | None = None) -> AgyVerdict:
    """Khoá agent này chạy agy Ở ĐÂU và có dùng được không? (thuần — trạng thái hộp do bên gọi truyền)

    - `owner`: người hỏi là Owner (chỉ ảnh hưởng `core.gen`; mặc định True cho mọi lượt không có người hỏi cụ thể);
    - `diplomatic`: với `agent:<id>` — agent đó là ngoại giao (`diplomatic_agent_ids`); khoá khác bỏ qua tham số này;
    - `box`: trạng thái hộp (`ok` | `down` | `not_installed`); mặc định `not_installed` (đóng an toàn — bên gọi thật
      luôn truyền trạng thái thật qua `verdict_for`); chỉ vai chạy qua hộp mới xét;
    - `purpose`: mục đích lượt gọi (thử agent, Dịch / Soạn lại nháp tin khách luôn qua hộp: `placement.BOX_PURPOSES`;
      thử quy tắc `rule_test` KHÔNG dùng agy: `UNTRUSTED_TEXT_PURPOSES`).
    Vai trong tiến trình luôn `allowed`. Vai qua hộp: `allowed` đúng khi hộp `ok`; không thì lý do trung thực và KHÔNG
    bao giờ chuyển về chạy trong tiến trình. Khoá lạ (core.embedding…) tính là vai qua hộp (đóng an toàn) — chúng không
    dùng agy nên không ai thấy lý do này."""
    where = placement.placement(agent_key, diplomatic, owner=owner, purpose=purpose)
    if purpose in UNTRUSTED_TEXT_PURPOSES:               # không dùng agy ở đâu cả; `where` giữ theo luật vai
        return AgyVerdict(False, CODE_UNTRUSTED_TEXT, REASON_UNTRUSTED_TEXT, where)
    if where == INPROC:
        return ALLOWED
    if box == BOX_OK:
        return ALLOWED_BOX
    return DENIED_BOX_DOWN if box == BOX_DOWN else DENIED_BOX_NOT_INSTALLED


async def current_box_state() -> str:
    """Trạng thái hộp lúc này (cache 10 giây trong `box_status`; chưa cấu hình ⇒ `not_installed` không chạm mạng).
    Lỗi bất ngờ ⇒ coi là `down` (đóng an toàn, không bao giờ làm hỏng định tuyến)."""
    try:
        st = (await box_client.box_status()).get("state")
    except Exception:  # noqa: BLE001 — trạng thái hộp không được làm hỏng một lượt gọi model
        log.warning("Không đọc được trạng thái hộp riêng Antigravity", exc_info=True)
        return BOX_DOWN
    return st if st in (BOX_OK, BOX_DOWN, BOX_NOT_INSTALLED) else BOX_DOWN


def agent_id_of(agent_key: str) -> uuid.UUID | None:
    if not agent_key.startswith(AGENT_PREFIX):
        return None
    try:
        return uuid.UUID(agent_key[len(AGENT_PREFIX):])
    except ValueError:
        return None


_DIPLOMATIC_SQL = """
    SELECT a.id FROM agent.identities a
    WHERE a.org_id = :o AND (:a IS NULL OR a.id = :a) AND (
          EXISTS (SELECT 1 FROM agent.channel_scopes s WHERE s.agent_id = a.id)
       OR EXISTS (SELECT 1 FROM core.groups g WHERE g.org_id = a.org_id AND g.assigned_agent_id = a.id)
       OR EXISTS (SELECT 1 FROM core.persons p WHERE p.org_id = a.org_id AND p.attrs->>'agent_id' = a.id::text))
"""


async def diplomatic_agent_ids(db: AsyncSession, org_id: uuid.UUID,
                               only: uuid.UUID | None = None) -> set[uuid.UUID]:
    """Agent ngoại giao của tổ chức (tiêu chí ở docstring module): có phạm vi nghe kênh / nhóm, hoặc được gán BOT cho
    một nhóm / một người. MỘT truy vấn cho cả tổ chức (danh sách gán model dùng chung); `only` thu về một agent
    (mỗi lượt `ModelRouter.generate` chỉ hỏi đúng agent đang gọi)."""
    rows = (await db.execute(text(_DIPLOMATIC_SQL).bindparams(bindparam("a", type_=Uuid())),
                             {"o": org_id, "a": only})).all()
    return {r.id for r in rows}


async def verdict_for(db: AsyncSession, org_id: uuid.UUID, agent_key: str, *, owner: bool = True,
                      purpose: str | None = None) -> AgyVerdict:
    """`decide` cho MỘT khoá, tự tra DB khi là `agent:<id>` và đọc trạng thái hộp khi vai chạy qua hộp (vai trong tiến
    trình không tốn truy vấn hay lượt kiểm hộp)."""
    if purpose in UNTRUSTED_TEXT_PURPOSES:
        return decide(agent_key, owner=owner, purpose=purpose)
    aid = agent_id_of(agent_key)
    diplomatic = (aid is not None and purpose not in placement.BOX_PURPOSES
                  and aid in await diplomatic_agent_ids(db, org_id, only=aid))
    if placement.placement(agent_key, diplomatic, owner=owner, purpose=purpose) == INPROC:
        return ALLOWED
    return decide(agent_key, owner=owner, diplomatic=diplomatic, box=await current_box_state(), purpose=purpose)


async def verdicts_for(db: AsyncSession, org_id: uuid.UUID, agent_keys: list[str], *,
                       owner: bool = True) -> dict[str, AgyVerdict]:
    """`decide` cho nhiều khoá bằng đúng một truy vấn ngoại giao và một lần đọc trạng thái hộp."""
    dip = await diplomatic_agent_ids(db, org_id) if any(k.startswith(AGENT_PREFIX) for k in agent_keys) else set()
    need_box = any(placement.placement(k, agent_id_of(k) in dip, owner=owner) == BOX for k in agent_keys)
    box = await current_box_state() if need_box else BOX_NOT_INSTALLED
    return {k: decide(k, owner=owner, diplomatic=(agent_id_of(k) in dip), box=box) for k in agent_keys}
